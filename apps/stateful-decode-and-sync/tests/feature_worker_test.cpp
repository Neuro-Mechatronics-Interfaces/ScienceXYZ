#include "feature_worker.hpp"

#include <chrono>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <thread>

namespace {

void expect(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}

void test_off_diagonal_band_limit_controls_dimension() {
  app::MpfFeaturizer::Config config;
  config.num_channels = 4;
  config.stft_size = 4;
  config.stft_hop = 2;
  config.num_bands = 3;
  config.num_off_diag_bands = 2;

  app::MpfFeaturizer featurizer(config);
  // C + 2 * ((C - 1) + (C - 2)) = 14 values per frequency band.
  expect(featurizer.feature_dim() == 42,
         "diagonal plus two upper off-diagonal bands determine dimension");

  std::vector<std::vector<float>> window(4, std::vector<float>(8, 1.0f));
  expect(featurizer.compute(window).size() == 42,
         "computed feature uses the configured reduced dimension");

  config.num_off_diag_bands = 0;
  app::MpfFeaturizer diagonal_only(config);
  expect(diagonal_only.feature_dim() == 12,
         "zero off-diagonal bands retains only the diagonal");
}

void test_batches_are_windowed_off_thread() {
  app::MpfFeaturizer::Config featurizer_config;
  featurizer_config.num_channels = 1;
  featurizer_config.stft_size = 4;
  featurizer_config.stft_hop = 2;
  featurizer_config.num_bands = 2;

  app::FeatureWorker worker;
  app::FeatureWorker::Config config;
  config.channel_map = {0};
  config.window_samples = 8;
  config.stride_samples = 2;
  config.featurizer = std::make_shared<app::MpfFeaturizer>(featurizer_config);
  config.classifier = [](const std::vector<float>& feature) {
    return std::vector<float>{feature.empty() ? 0.0f : feature.front(), 1.0f};
  };
  expect(worker.start(config), "feature worker starts");

  app::FeatureWorker::SampleBatch batch;
  batch.route.capture_enabled = true;
  batch.route.collection_id = 3;
  batch.route.label = 4;
  batch.route.classify = true;
  for (std::int32_t i = 0; i < 10; ++i) {
    batch.samples.push_back(
        {{i, i + 1}, static_cast<std::uint64_t>(2000 + i),
         static_cast<std::uint64_t>(1000 + i)});
  }
  expect(worker.try_enqueue(std::move(batch)), "feature batch is accepted");

  std::size_t result_count = 0;
  app::FeatureWorker::Result result;
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (std::chrono::steady_clock::now() < deadline && result_count < 2) {
    if (worker.poll(result)) {
      ++result_count;
      expect(result.route.capture_enabled && result.route.classify,
             "route metadata reaches the feature result");
      expect(result.feature.size() == 2, "feature dimension is preserved");
      expect(result.probabilities.size() == 2,
             "classification is completed by the compute worker");
      expect(result.source_sequence_number == 2007 + (result_count - 1) * 2,
             "source sequence reaches the feature result");
    } else {
      std::this_thread::yield();
    }
  }
  const auto stats = worker.stats();
  worker.stop();

  expect(result_count == 2, "one result is produced per completed stride");
  expect(stats.input_batches == 1 && stats.input_samples == 10,
         "worker accounts for the complete input batch");
  expect(stats.compute_jobs_enqueued == 2 && stats.compute_jobs_dropped == 0,
         "completed windows are queued for ordered computation");
  expect(stats.windows_computed == 2 && stats.results_enqueued == 2,
         "worker computes and queues both windows");
  expect(stats.input_samples_dropped == 0 && stats.results_dropped == 0,
         "test batch has no queue loss");
}

}  // namespace

int main() {
  try {
    test_off_diagonal_band_limit_controls_dimension();
    test_batches_are_windowed_off_thread();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "feature worker tests passed\n";
  return 0;
}
