#include "feature_worker.hpp"

#include "feature_decimator.hpp"

#include <chrono>
#include <cmath>
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

void test_explicit_frequency_bands_control_dimension() {
  app::MpfFeaturizer::Config config;
  config.num_channels = 4;
  config.sample_rate_hz = 800.0;
  config.stft_size = 8;
  config.stft_hop = 4;
  config.num_bands = 99;
  config.frequency_bands_hz = {{0.0, 100.0}, {100.0, 300.0}};
  config.num_off_diag_bands = 2;

  app::MpfFeaturizer featurizer(config);
  expect(featurizer.feature_dim() == 28,
         "explicit Hz bands replace the legacy full-Nyquist band count");
  std::vector<std::vector<float>> window(4, std::vector<float>(8, 1.0f));
  expect(featurizer.compute(window).size() == 28,
         "explicit Hz bands produce one matrix feature block per configured band");
}

void test_decimation_plan_preserves_window_and_stride_clock() {
  expect(app::next_power_of_two(250) == 256 && app::next_power_of_two(256) == 256 &&
             app::next_power_of_two(500) == 512,
         "FFT sizing rounds up dynamically without expanding exact powers of two");
  const auto plan =
      app::make_feature_decimation_plan(20000.0, 1000.0, 1.25, 4000, 400);
  expect(plan.factor == 8, "decimation chooses the largest exact clock divisor");
  expect(plan.feature_sample_rate_hz == 2500.0,
         "decimation sets the feature sample rate from the source clock");
  expect(plan.stopband_edge_hz == 1250.0 && !plan.coefficients.empty(),
         "anti-alias stopband starts at the decimated Nyquist frequency");
  expect(plan.coefficients.size() % 2 == 1 &&
             plan.group_delay_source_samples == (plan.coefficients.size() - 1) / 2,
         "anti-alias FIR has an integer source-sample group delay");
  double gain = 0.0;
  for (const auto coefficient : plan.coefficients) gain += coefficient;
  expect(std::abs(gain - 1.0) < 1e-9, "anti-alias FIR has unity DC gain");

  const auto response = [&](double frequency_hz) {
    constexpr double kPi = 3.14159265358979323846;
    double real = 0.0;
    double imaginary = 0.0;
    for (std::size_t index = 0; index < plan.coefficients.size(); ++index) {
      const double phase = -2.0 * kPi * frequency_hz * static_cast<double>(index) /
                           plan.source_sample_rate_hz;
      real += plan.coefficients[index] * std::cos(phase);
      imaginary += plan.coefficients[index] * std::sin(phase);
    }
    return std::hypot(real, imaginary);
  };
  expect(response(plan.passband_edge_hz) > 0.9,
         "anti-alias FIR preserves the highest requested feature edge");
  expect(response(plan.stopband_edge_hz) < 0.1,
         "anti-alias FIR attenuates at the decimated Nyquist edge");

  app::MpfFeaturizer::Config requested_bands;
  requested_bands.num_channels = 1;
  requested_bands.sample_rate_hz = plan.feature_sample_rate_hz;
  requested_bands.stft_size = app::next_power_of_two(4000 / plan.factor);
  requested_bands.stft_hop = requested_bands.stft_size;
  requested_bands.frequency_bands_hz = {
      {0.0, 62.5},     {62.5, 125.0},  {125.0, 250.0},
      {250.0, 375.0},  {375.0, 687.5}, {687.5, 1000.0}};
  requested_bands.num_off_diag_bands = 0;
  app::MpfFeaturizer requested_featurizer(requested_bands);
  expect(requested_featurizer.feature_dim() == 6,
         "all six requested physical bands map onto the decimated FFT grid");
}

void test_power_of_two_fft_matches_impulse_spectrum() {
  app::MpfFeaturizer::Config config;
  config.num_channels = 1;
  config.stft_size = 8;
  config.stft_hop = 8;
  config.num_bands = 1;
  config.num_off_diag_bands = 0;
  config.eps = 1e-3f;

  app::MpfFeaturizer featurizer(config);
  std::vector<std::vector<float>> window(1, std::vector<float>(8, 0.0f));
  window[0][1] = 2.0f;
  const auto feature = featurizer.compute(window);

  constexpr double kPi = 3.14159265358979323846;
  const double hann = 0.5 * (1.0 - std::cos(2.0 * kPi / 8.0));
  const double impulse_power = std::pow(2.0 * hann, 2.0);
  const double expected = std::log(impulse_power + config.eps);
  expect(feature.size() == 1, "single-channel single-band feature has one value");
  expect(std::abs(static_cast<double>(feature.front()) - expected) < 1e-5,
         "radix-2 STFT matches the analytic impulse spectrum");
}

void test_short_decimated_window_is_zero_padded() {
  app::MpfFeaturizer::Config config;
  config.num_channels = 1;
  config.sample_rate_hz = 1250.0;
  config.stft_size = 8;
  config.stft_hop = 4;
  config.num_bands = 1;
  config.num_off_diag_bands = 0;
  config.eps = 1e-3f;

  app::MpfFeaturizer featurizer(config);
  std::vector<std::vector<float>> window(1, std::vector<float>(4, 0.0f));
  window[0][1] = 2.0f;
  const auto feature = featurizer.compute(window);
  const double expected = std::log(1.0 + config.eps);
  expect(feature.size() == 1 &&
             std::abs(static_cast<double>(feature.front()) - expected) < 1e-5,
         "a short decimated window is tapered at its own duration and zero-padded");
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
  expect(stats.mpf_service_calls == 2 && stats.mpf_service_ns_total > 0 &&
             stats.mpf_service_ns_max > 0,
         "MPF service time is measured separately");
  expect(stats.inference_service_calls == 2 && stats.inference_service_ns_total > 0 &&
             stats.inference_service_ns_max > 0,
         "inference service time is measured separately");
}

void test_streaming_decimator_centers_metadata_and_averages() {
  // A factor-2 identity-center FIR ({0,1,0}) emits every other input starting
  // once the filter has filled, tagged with the FIR-center source metadata.
  app::FeatureDecimationPlan plan;
  plan.factor = 2;
  plan.group_delay_source_samples = 1;
  plan.coefficients = {0.0, 1.0, 0.0};
  app::StreamingDecimator decimator(plan);

  std::vector<std::uint64_t> sequences;
  std::vector<std::int32_t> values;
  app::StreamingDecimator::Sample out;
  for (std::int32_t i = 0; i < 7; ++i) {
    const std::int32_t v = i;
    if (decimator.push(&v, 1, static_cast<std::uint64_t>(2000 + i),
                       static_cast<std::uint64_t>(1000 + i), out)) {
      sequences.push_back(out.source_sequence_number);
      values.push_back(out.values.front());
    }
  }
  // taps=3, factor=2: emits at raw index 2,4,6 -> center index 1,3,5.
  expect(sequences == std::vector<std::uint64_t>({2001, 2003, 2005}),
         "streaming decimator retains FIR-center source metadata");
  expect(values == std::vector<std::int32_t>({1, 3, 5}),
         "identity-center FIR passes the center sample through");

  // A true 3-tap averaging FIR reduces to the mean of the window; verify the
  // rounding path produces the averaged value at the emit phase.
  app::FeatureDecimationPlan avg_plan;
  avg_plan.factor = 2;
  avg_plan.group_delay_source_samples = 1;
  avg_plan.coefficients = {1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0};
  app::StreamingDecimator averager(avg_plan);
  std::vector<std::int32_t> averaged;
  const std::int32_t ramp[] = {0, 30, 60, 90, 120, 150, 180};
  for (int i = 0; i < 7; ++i) {
    if (averager.push(&ramp[i], 1, 0, 0, out)) averaged.push_back(out.values.front());
  }
  // Emit at raw index 2 -> mean(0,30,60)=30; index 4 -> mean(60,90,120)=90;
  // index 6 -> mean(120,150,180)=150.
  expect(averaged == std::vector<std::int32_t>({30, 90, 150}),
         "averaging FIR emits the rounded window mean at each emit phase");
}

void test_streaming_decimator_passthrough_when_factor_one() {
  app::FeatureDecimationPlan plan;  // factor defaults to 1
  app::StreamingDecimator decimator(plan);
  app::StreamingDecimator::Sample out;
  const std::int32_t values[] = {5, 6};
  expect(decimator.push(values, 2, 42, 99, out), "factor-1 decimator always emits");
  expect(out.values == std::vector<std::int32_t>({5, 6}) &&
             out.source_sequence_number == 42 && out.timestamp_ns == 99,
         "factor-1 decimator passes samples and metadata through unchanged");
}

}  // namespace

int main() {
  try {
    test_off_diagonal_band_limit_controls_dimension();
    test_explicit_frequency_bands_control_dimension();
    test_decimation_plan_preserves_window_and_stride_clock();
    test_power_of_two_fft_matches_impulse_spectrum();
    test_short_decimated_window_is_zero_padded();
    test_batches_are_windowed_off_thread();
    test_streaming_decimator_centers_metadata_and_averages();
    test_streaming_decimator_passthrough_when_factor_one();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "feature worker tests passed\n";
  return 0;
}
