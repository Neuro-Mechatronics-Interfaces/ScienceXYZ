#pragma once

#include <cstddef>
#include <cmath>
#include <deque>
#include <exception>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "mlp.hpp"

namespace app {

// Owns one asynchronous fit at a time. The request contains value-owned
// training data, so the worker never reads CollectionStore while fitting. The
// worker also owns its candidate Mlp; the caller receives it only in the
// terminal success event and can then swap it into live inference under its
// model lock.
class FitWorker {
 public:
  struct Request {
    Mlp::Config config;
    std::vector<std::vector<float>> features;
    std::vector<std::size_t> labels;
  };

  enum class EventKind { kProgress, kSucceeded, kFailed };

  struct Event {
    EventKind kind = EventKind::kFailed;
    Mlp::FitProgress progress;
    std::shared_ptr<Mlp> candidate;
    bool malformed = false;
    std::string message;
  };

  FitWorker() = default;
  FitWorker(const FitWorker&) = delete;
  FitWorker& operator=(const FitWorker&) = delete;

  ~FitWorker() { stop(); }

  // Start a request after the previous terminal event has been consumed.
  // Returns false if another request is queued or running.
  bool start(Request request) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (job_active_) return false;
    }

    // A completed std::thread remains joinable until it is reclaimed. The
    // main loop consumes the terminal event before starting a new request.
    if (thread_.joinable()) thread_.join();

    {
      std::lock_guard<std::mutex> lock(mutex_);
      events_.clear();
      job_active_ = true;
    }

    try {
      thread_ = std::thread(&FitWorker::run, this, std::move(request));
    } catch (const std::exception& e) {
      std::lock_guard<std::mutex> lock(mutex_);
      events_.push_back({EventKind::kFailed, {}, nullptr, false,
                         std::string("could not start fit worker: ") + e.what()});
    } catch (...) {
      std::lock_guard<std::mutex> lock(mutex_);
      events_.push_back({EventKind::kFailed, {}, nullptr, false,
                         "could not start fit worker"});
    }
    return true;
  }

  // Move the oldest worker event to the caller. Events are published in
  // training order. A terminal event releases the job slot; its thread is
  // joined by the next start() or by stop().
  bool poll(Event& event) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (events_.empty()) return false;
    event = std::move(events_.front());
    events_.pop_front();
    if (event.kind != EventKind::kProgress) job_active_ = false;
    return true;
  }

  bool busy() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return job_active_;
  }

  // There is deliberately no mid-epoch cancellation in the v1 MLP API.
  // Joining here still guarantees that no worker accesses this object after
  // destruction, and it bounds shutdown to the remaining fit work.
  void stop() {
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    events_.clear();
    job_active_ = false;
  }

 private:
  void push(Event event) {
    std::lock_guard<std::mutex> lock(mutex_);
    events_.push_back(std::move(event));
  }

  void run(Request request) {
    try {
      auto candidate = std::make_shared<Mlp>();
      candidate->init(request.config);

      float accuracy = 0.0f;
      const float loss = candidate->fit(
          request.features, request.labels, &accuracy,
          [this](const Mlp::FitProgress& progress) {
            push({EventKind::kProgress, progress, nullptr, false, {}});
          });

      if (loss < 0.0f || !std::isfinite(loss) || !std::isfinite(accuracy)) {
        push({EventKind::kFailed,
              {0, request.config.epochs, loss, accuracy},
              nullptr,
              true,
              "training data is malformed or produced non-finite metrics"});
        return;
      }

      Mlp::FitProgress final_progress;
      final_progress.epoch = request.config.epochs;
      final_progress.total_epochs = request.config.epochs;
      final_progress.loss = loss;
      final_progress.accuracy = accuracy;
      push({EventKind::kSucceeded, final_progress, std::move(candidate), false, {}});
    } catch (const std::exception& e) {
      push({EventKind::kFailed, {}, nullptr, false,
            std::string("fit worker failed: ") + e.what()});
    } catch (...) {
      push({EventKind::kFailed, {}, nullptr, false, "fit worker failed"});
    }
  }

  mutable std::mutex mutex_;
  std::deque<Event> events_;
  bool job_active_ = false;
  std::thread thread_;
};

}  // namespace app
