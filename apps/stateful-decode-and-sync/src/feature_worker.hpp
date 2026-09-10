#pragma once

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

#include "mpf_features.hpp"

namespace app {

// Moves stride-triggered work off the acquisition loop. The ingestion thread
// owns channel selection and feature-rate window state while continuously
// draining value-owned sample batches. Each completed window is copied into a
// FIFO compute queue; the compute thread performs MPF and optional
// classification in order. The App thread decimates raw frames to the feature
// rate (see StreamingDecimator), forwards them, enqueues the decimated samples,
// and consumes completed results. Samples reaching this worker are therefore
// already at the feature rate; no decimation happens here. Route metadata is
// captured at the source-frame boundary so delayed feature results cannot be
// relabelled by a later control command.
class FeatureWorker {
 public:
  struct Route {
    bool capture_enabled = false;
    std::uint32_t collection_id = 0;
    std::uint32_t label = 0;
    bool classify = false;
  };

  struct Sample {
    std::vector<std::int32_t> values;
    std::uint64_t source_sequence_number = 0;
    std::uint64_t timestamp_ns = 0;
  };

  struct SampleBatch {
    std::vector<Sample> samples;
    Route route;
  };

  struct Result {
    std::vector<float> feature;
    std::vector<float> probabilities;
    std::uint64_t source_sequence_number = 0;
    std::uint64_t timestamp_ns = 0;
    Route route;
  };

  struct Config {
    std::vector<std::size_t> channel_map;
    std::size_t window_samples = 0;
    std::size_t stride_samples = 1;
    std::shared_ptr<const MpfFeaturizer> featurizer;
    // Called only by the compute thread, after MPF has completed. The app
    // supplies a mutex-protected snapshot/inference callback for the live
    // model; leaving it empty disables classification work.
    std::function<std::vector<float>(const std::vector<float>&)> classifier;
    std::size_t input_capacity = 128;
    std::size_t compute_capacity = 256;
    std::size_t result_capacity = 256;
  };

  struct Stats {
    std::uint64_t input_batches = 0;
    std::uint64_t input_samples = 0;
    std::uint64_t ingest_samples_processed = 0;
    // Equal to ingest_samples_processed now that decimation is upstream; kept
    // for diagnostic-log/stat compatibility.
    std::uint64_t feature_samples_emitted = 0;
    std::uint64_t ingest_service_ns_total = 0;
    std::uint64_t ingest_service_ns_max_batch = 0;
    std::uint64_t compute_jobs_enqueued = 0;
    std::uint64_t compute_jobs_dropped = 0;
    std::uint64_t windows_computed = 0;
    std::uint64_t results_enqueued = 0;
    std::uint64_t compute_errors = 0;
    std::uint64_t input_batches_dropped = 0;
    std::uint64_t input_samples_dropped = 0;
    std::uint64_t results_dropped = 0;
    std::uint64_t mpf_service_calls = 0;
    std::uint64_t mpf_service_ns_total = 0;
    std::uint64_t mpf_service_ns_max = 0;
    std::uint64_t inference_service_calls = 0;
    std::uint64_t inference_service_ns_total = 0;
    std::uint64_t inference_service_ns_max = 0;
    std::size_t pending_input_batches = 0;
    std::size_t pending_compute_jobs = 0;
    std::size_t pending_results = 0;
  };

  FeatureWorker() = default;
  FeatureWorker(const FeatureWorker&) = delete;
  FeatureWorker& operator=(const FeatureWorker&) = delete;

  ~FeatureWorker() { stop(); }

  bool start(Config config) {
    if (config.channel_map.empty() || config.window_samples == 0 ||
        config.stride_samples == 0 || !config.featurizer || config.input_capacity == 0 ||
        config.compute_capacity == 0 || config.result_capacity == 0) {
      return false;
    }

    stop();
    {
      std::lock_guard<std::mutex> lock(mutex_);
      config_ = std::move(config);
      input_.clear();
      results_.clear();
      stats_ = {};
      stopping_ = false;
      running_ = true;
      reset_window_state();
    }

    try {
      ingest_thread_ = std::thread(&FeatureWorker::run_ingest, this);
      compute_thread_ = std::thread(&FeatureWorker::run_compute, this);
    } catch (...) {
      stop();
      return false;
    }
    return true;
  }

  bool try_enqueue(SampleBatch batch) {
    const auto sample_count = batch.samples.size();
    if (sample_count == 0) return true;

    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!running_ || stopping_ || input_.size() >= config_.input_capacity) {
        ++stats_.input_batches_dropped;
        stats_.input_samples_dropped += sample_count;
        return false;
      }
      ++stats_.input_batches;
      stats_.input_samples += sample_count;
      input_.push_back(std::move(batch));
    }
    input_condition_.notify_one();
    return true;
  }

  bool poll(Result& result) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (results_.empty()) return false;
    result = std::move(results_.front());
    results_.pop_front();
    return true;
  }

  Stats stats() const {
    std::lock_guard<std::mutex> lock(mutex_);
    Stats result = stats_;
    result.pending_input_batches = input_.size();
    result.pending_compute_jobs = compute_jobs_.size();
    result.pending_results = results_.size();
    return result;
  }

  void stop() {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!running_ && !ingest_thread_.joinable() && !compute_thread_.joinable()) {
        return;
      }
      stopping_ = true;
      // Do not make application shutdown wait behind queued feature work.
      // Count discarded input and window jobs explicitly; a job already being
      // computed is allowed to finish before the thread is joined.
      for (const auto& batch : input_) {
        ++stats_.input_batches_dropped;
        stats_.input_samples_dropped += batch.samples.size();
      }
      input_.clear();
      stats_.compute_jobs_dropped += compute_jobs_.size();
      compute_jobs_.clear();
    }
    input_condition_.notify_all();
    compute_condition_.notify_all();
    if (ingest_thread_.joinable()) ingest_thread_.join();
    if (compute_thread_.joinable()) compute_thread_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    running_ = false;
    results_.clear();
  }

 private:
  struct ComputeJob {
    std::vector<std::vector<float>> window;
    std::uint64_t source_sequence_number = 0;
    std::uint64_t timestamp_ns = 0;
    Route route;
  };

  void reset_window_state() {
    window_ring_.assign(config_.channel_map.size(),
                        std::vector<float>(config_.window_samples, 0.0f));
    ring_pos_ = 0;
    samples_seen_ = 0;
    samples_since_stride_ = 0;
    selected_scratch_.assign(config_.channel_map.size(), 0.0f);
  }

  void run_ingest() {
    for (;;) {
      SampleBatch batch;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        input_condition_.wait(lock, [this] { return stopping_ || !input_.empty(); });
        if (stopping_) return;
        batch = std::move(input_.front());
        input_.pop_front();
      }

      const auto ingest_start = std::chrono::steady_clock::now();
      std::uint64_t feature_samples = 0;
      for (auto& sample : batch.samples) {
        if (ingest(std::move(sample), batch.route)) ++feature_samples;
      }
      const auto ingest_ns = elapsed_ns(ingest_start);
      {
        std::lock_guard<std::mutex> lock(mutex_);
        stats_.ingest_samples_processed += batch.samples.size();
        stats_.feature_samples_emitted += feature_samples;
        stats_.ingest_service_ns_total += ingest_ns;
        stats_.ingest_service_ns_max_batch =
            std::max(stats_.ingest_service_ns_max_batch, ingest_ns);
      }
    }
  }

  void run_compute() {
    for (;;) {
      ComputeJob job;
      {
        std::unique_lock<std::mutex> lock(mutex_);
        compute_condition_.wait(lock,
                                [this] { return stopping_ || !compute_jobs_.empty(); });
        if (stopping_) return;
        job = std::move(compute_jobs_.front());
        compute_jobs_.pop_front();
      }

      try {
        const auto mpf_start = std::chrono::steady_clock::now();
        auto feature = config_.featurizer->compute(job.window);
        const auto mpf_ns = elapsed_ns(mpf_start);
        std::vector<float> probabilities;
        std::uint64_t inference_ns = 0;
        bool inference_called = false;
        if (job.route.classify && config_.classifier) {
          const auto inference_start = std::chrono::steady_clock::now();
          probabilities = config_.classifier(feature);
          inference_ns = elapsed_ns(inference_start);
          inference_called = true;
        }

        std::lock_guard<std::mutex> lock(mutex_);
        ++stats_.mpf_service_calls;
        stats_.mpf_service_ns_total += mpf_ns;
        stats_.mpf_service_ns_max = std::max(stats_.mpf_service_ns_max, mpf_ns);
        if (inference_called) {
          ++stats_.inference_service_calls;
          stats_.inference_service_ns_total += inference_ns;
          stats_.inference_service_ns_max =
              std::max(stats_.inference_service_ns_max, inference_ns);
        }
        ++stats_.windows_computed;
        if (results_.size() >= config_.result_capacity) {
          ++stats_.results_dropped;
          continue;
        }
        results_.push_back({std::move(feature), std::move(probabilities),
                            job.source_sequence_number, job.timestamp_ns, job.route});
        ++stats_.results_enqueued;
      } catch (...) {
        std::lock_guard<std::mutex> lock(mutex_);
        ++stats_.compute_errors;
      }
    }
  }

  static std::uint64_t elapsed_ns(
      const std::chrono::steady_clock::time_point& start) {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start)
            .count());
  }

  // Samples arrive already at the feature rate (decimation happens upstream in
  // the acquisition loop). Select the featurized channels and feed one
  // feature-rate sample into the window state.
  bool ingest(Sample sample, const Route& route) {
    for (std::size_t fc = 0; fc < config_.channel_map.size(); ++fc) {
      const auto upstream = config_.channel_map[fc];
      selected_scratch_[fc] = upstream < sample.values.size()
                                  ? static_cast<float>(sample.values[upstream])
                                  : 0.0f;
    }
    ingest_feature_sample(selected_scratch_, sample.source_sequence_number,
                          sample.timestamp_ns, route);
    return true;
  }

  void ingest_feature_sample(const std::vector<float>& values,
                             std::uint64_t source_sequence_number,
                             std::uint64_t timestamp_ns, const Route& route) {
    for (std::size_t fc = 0; fc < values.size(); ++fc) {
      window_ring_[fc][ring_pos_] = values[fc];
    }
    ring_pos_ = (ring_pos_ + 1) % config_.window_samples;
    ++samples_seen_;
    ++samples_since_stride_;
    if (samples_seen_ < config_.window_samples ||
        samples_since_stride_ < config_.stride_samples) {
      return;
    }
    samples_since_stride_ = 0;
    if (!route.capture_enabled && !route.classify) return;

    ComputeJob job;
    job.window.resize(config_.channel_map.size());
    for (std::size_t fc = 0; fc < config_.channel_map.size(); ++fc) {
      job.window[fc].resize(config_.window_samples);
      for (std::size_t n = 0; n < config_.window_samples; ++n) {
        const auto index = (ring_pos_ + n) % config_.window_samples;
        job.window[fc][n] = window_ring_[fc][index];
      }
    }
    job.source_sequence_number = source_sequence_number;
    job.timestamp_ns = timestamp_ns;
    job.route = route;

    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (stopping_ || compute_jobs_.size() >= config_.compute_capacity) {
        ++stats_.compute_jobs_dropped;
        return;
      }
      compute_jobs_.push_back(std::move(job));
      ++stats_.compute_jobs_enqueued;
    }
    compute_condition_.notify_one();
  }

  mutable std::mutex mutex_;
  std::condition_variable input_condition_;
  std::condition_variable compute_condition_;
  Config config_;
  std::deque<SampleBatch> input_;
  std::deque<ComputeJob> compute_jobs_;
  std::deque<Result> results_;
  std::vector<std::vector<float>> window_ring_;
  std::vector<float> selected_scratch_;
  std::size_t ring_pos_ = 0;
  std::size_t samples_seen_ = 0;
  std::size_t samples_since_stride_ = 0;
  Stats stats_;
  bool stopping_ = false;
  bool running_ = false;
  std::thread ingest_thread_;
  std::thread compute_thread_;
};

}  // namespace app
