#include "clock_estimator.hpp"
#include "wireless_simulator.hpp"
#include "wireless_source_adapter.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <vector>

namespace {

using app::wireless::AdapterConfig;
using app::wireless::AdapterDiagnosticKind;
using app::wireless::AffineClockEstimator;
using app::wireless::ClockObservationResult;
using app::wireless::ClockSyncSample;
using app::wireless::FourSourceAdapter;
using app::wireless::FourSourceSimulator;
using app::wireless::FourSourceSimulatorConfig;
using app::wireless::MultiSourceAdapter;
using app::wireless::MultiSourceSimulator;
using app::wireless::MultiSourceSimulatorConfig;
using app::wireless::NormalizedWirelessBatch;
using app::wireless::RationalRate;
using app::wireless::SimulatedSourceConfig;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

void test_affine_fit_uncertainty_and_reordering() {
  app::wireless::ClockEstimatorConfig config;
  config.nominal_source_tick_frequency_hz = 100'000'000.0;
  config.timestamp_jitter_ns = 10;
  config.sync_asymmetry_ns = 20;
  config.batching_ambiguity_ns = 30;
  config.rounding_ns = 1;
  config.drift_uncertainty_ppm = 2.0;
  AffineClockEstimator estimator(config);

  expect(estimator.observe(ClockSyncSample{100, 1'000'000, 40, 5, 0}) ==
             ClockObservationResult::kAccepted,
         "first sync sample is accepted");
  expect(estimator.observe(ClockSyncSample{1'100, 1'010'000, 60, 5, 1}) ==
             ClockObservationResult::kAccepted,
         "second sync sample locks the affine model");
  expect(estimator.observe(ClockSyncSample{600, 1'005'000, 50, 5, 0}) ==
             ClockObservationResult::kReordered,
         "older source sequence is ignored as a reordered sync sample");

  const auto& model = estimator.current_model();
  expect(model.locked && model.sync_sample_count == 2,
         "two ordered sync samples produce a locked model");
  expect(std::fabs(model.slope_ns_per_source_tick - 10.0) < 1e-12,
         "affine slope is recovered exactly");
  expect(model.rtt_min_ns == 40 && model.rtt_median_ns == 60 && model.rtt_max_ns == 60,
         "RTT statistics are persisted");
  const auto mapped =
      estimator.map_source_tick(std::uint64_t{600}, 1'020'000);
  expect(mapped.bounded && mapped.interval.has_value() &&
             mapped.estimated_reference_time_ns == 1'005'000,
         "mapped time exposes a bounded estimate and interval");
  expect(mapped.epsilon_ns >= 10 + 20 + 30 + 5 + 1,
         "epsilon includes explicit uncertainty components");
  expect(!estimator.map_source_tick(std::uint64_t{600}, 6'020'000'000ULL).bounded,
         "stale models become explicitly unbounded");
}

void test_wrap_reset_and_epoch_history() {
  app::wireless::ClockEstimatorConfig config;
  config.nominal_source_tick_frequency_hz = 1'000'000.0;
  config.source_tick_wrap_modulus = 1'000;
  AffineClockEstimator estimator(config);
  estimator.observe(ClockSyncSample{900, 1'000, 0, 0, 0});
  estimator.observe(ClockSyncSample{950, 1'050, 0, 0, 1});
  expect(estimator.observe(ClockSyncSample{10, 1'110, 0, 0, 2}) ==
             ClockObservationResult::kWrapped,
         "large backwards tick transition is recognized as wrap");
  expect(estimator.current_model().locked,
         "wrapped ticks remain fit-compatible in the same epoch");
  const auto old_epoch = estimator.current_model().epoch_id;
  estimator.begin_new_epoch(5, 2'000);
  expect(estimator.current_model().epoch_id > old_epoch && estimator.epochs().size() == 1,
         "explicit reset closes the old model and starts a new epoch");
  expect(estimator.observe(ClockSyncSample{5, 2'000, 0, 0, 0}) ==
             ClockObservationResult::kAccepted,
         "new epoch accepts its first source tick");
  expect(estimator.observe(ClockSyncSample{105, 2'100, 0, 0, 1}) ==
             ClockObservationResult::kAccepted,
         "new epoch refits independently");
}

FourSourceSimulatorConfig simulator_config() {
  FourSourceSimulatorConfig config;
  for (std::size_t i = 0; i < 4; ++i) {
    SimulatedSourceConfig source;
    source.source_id = "sim-" + std::to_string(i);
    source.sample_rate = RationalRate{static_cast<std::uint64_t>(1000 + i * 500), 1};
    source.channel_count = 2;
    source.samples_per_batch = 4;
    source.batch_count = 6;
    source.gateway_jitter_ns = 100 + i * 10;
    source.clock_drift_ppm = static_cast<double>(i) * 25.0;
    source.loss_every_n_batches = i == 0 ? 3 : 0;
    source.reorder_adjacent_batches = i == 1;
    source.reset_at_batch = i == 2 ? std::optional<std::uint64_t>(3) : std::nullopt;
    config.sources.push_back(std::move(source));
  }
  return config;
}

std::vector<AdapterConfig> adapter_configs() {
  std::vector<AdapterConfig> configs;
  for (std::size_t i = 0; i < 4; ++i) {
    AdapterConfig config;
    config.source_id = "sim-" + std::to_string(i);
    config.topic = "wireless/v1/" + config.source_id;
    config.expected_sample_rate = RationalRate{static_cast<std::uint64_t>(1000 + i * 500), 1};
    config.expected_channel_count = 2;
    config.expected_sample_format =
        sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE;
    config.max_batch_samples = 8;
    config.queue_capacity_batches = 32;
    config.clock.nominal_source_tick_frequency_hz = 1'000'000.0;
    configs.push_back(std::move(config));
  }
  return configs;
}

void test_deterministic_four_source_adapter() {
  FourSourceSimulator simulator(simulator_config());
  expect(simulator.valid(), "four-source simulator configuration is valid");
  const auto first = simulator.generate();
  const auto second = simulator.generate();
  expect(first.size() == second.size() && first.size() == 22,
         "loss and reset controls produce a bounded deterministic stream");
  for (std::size_t i = 0; i < first.size(); ++i) {
    std::string lhs;
    std::string rhs;
    expect(first[i].accepted.batch.SerializeToString(&lhs) &&
               second[i].accepted.batch.SerializeToString(&rhs) && lhs == rhs &&
               first[i].host_receive_time_ns == second[i].host_receive_time_ns,
           "simulator output is reproducible byte-for-byte");
  }

  FourSourceAdapter adapter(adapter_configs(), 64);
  expect(adapter.valid(), "four reusable adapter instances are valid");
  std::size_t sync_samples = 0;
  for (const auto& item : first) {
    if (item.accepted.batch.source_id() == "sim-0" && sync_samples < 2) {
      const auto result = adapter.observe_clock_sync(
          "sim-0", ClockSyncSample{item.accepted.batch.first_source_tick(),
                                   item.accepted.batch.source_acquisition_time_ns(),
                                   100, 20, item.accepted.batch.batch_sequence()});
      expect(result.has_value() && *result == ClockObservationResult::kAccepted,
             "adapter forwards deterministic sync observations");
      ++sync_samples;
    }
    expect(adapter.submit(item.accepted, item.host_receive_time_ns),
           "valid simulated AcceptedBatch enters the reusable adapter");
  }

  bool saw_gap = false;
  bool saw_reorder = false;
  bool saw_unbounded = false;
  std::size_t popped = 0;
  while (auto normalized = adapter.pop_next()) {
    ++popped;
    const auto& batch = normalized->accepted.batch;
    expect(normalized->host_receive_time_ns != 0 &&
               normalized->accepted.topic == "wireless/v1/" + batch.source_id() &&
               normalized->sample_times.size() == batch.sample_count(),
           "normalized output preserves host stamp, topic, payload shape, and sample count");
    expect(normalized->accepted.batch.gateway_receive_time_ns() != 0 &&
               normalized->accepted.batch.has_source_acquisition_time_ns(),
           "source and gateway timestamps remain in the accepted protobuf");
    if (batch.source_id() == "sim-0" && normalized->clock_model.has_value()) {
      expect(normalized->sample_times.front().aligned_time.has_value(),
             "locked source exposes per-sample affine intervals");
    }
  }
  expect(popped == first.size(), "adapter queue drains every accepted simulated batch");
  for (const auto& diagnostic : adapter.drain_diagnostics()) {
    saw_gap |= diagnostic.kind == AdapterDiagnosticKind::kBatchGap ||
               diagnostic.kind == AdapterDiagnosticKind::kSampleGap;
    saw_reorder |= diagnostic.kind == AdapterDiagnosticKind::kReorderedBatch ||
                   diagnostic.kind == AdapterDiagnosticKind::kReorderedSamples;
    saw_unbounded |= diagnostic.kind == AdapterDiagnosticKind::kUnboundedClock;
  }
  expect(saw_gap && saw_reorder && saw_unbounded,
         "loss, reordering, and missing clock lock remain explicit diagnostics");
}

MultiSourceSimulatorConfig two_source_simulator_config() {
  MultiSourceSimulatorConfig config;
  config.max_output_batches = 64;

  SimulatedSourceConfig emg;
  emg.source_id = "sim-emg-8ch";
  emg.topic = "wireless/v1/sim-emg-8ch";
  emg.sample_rate = RationalRate{2048, 1};
  emg.channel_count = 8;
  emg.sample_format = sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE;
  emg.source_tick_frequency_hz = 2'048'000;
  emg.samples_per_batch = 32;  // Simulator fixture; hardware batch size is pending.
  emg.batch_count = 4;
  emg.start_source_tick = 10'000;
  emg.start_reference_time_ns = 2'000'000'000;
  emg.gateway_id = "sim-gateway-emg";
  emg.gateway_session_id = "sim-session-emg";
  emg.gateway_clock_id = "sim-clock-emg";
  for (std::uint32_t i = 0; i < emg.channel_count; ++i) {
    sciencexyz::wireless::v1::ChannelDescriptor descriptor;
    descriptor.set_channel_id("emg-" + std::to_string(i));
    descriptor.set_label("EMG " + std::to_string(i));
    descriptor.set_unit("uV");
    emg.channels.push_back(std::move(descriptor));
  }
  config.sources.push_back(std::move(emg));

  SimulatedSourceConfig imu;
  imu.source_id = "sim-imu-10ch";
  imu.topic = "wireless/v1/sim-imu-10ch";
  imu.sample_rate = RationalRate{128, 1};
  imu.channel_count = 10;
  imu.sample_format = sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE;
  imu.source_tick_frequency_hz = 1'000'000;
  imu.samples_per_batch = 8;  // Simulator fixture; hardware batch size is pending.
  imu.batch_count = 4;
  imu.start_source_tick = 20'000;
  imu.start_reference_time_ns = 2'000'000'000;
  imu.clock_drift_ppm = 12.0;
  imu.gateway_jitter_ns = 150;
  imu.loss_every_n_batches = 3;
  imu.gateway_id = "sim-gateway-imu";
  imu.gateway_session_id = "sim-session-imu";
  imu.gateway_clock_id = "sim-clock-imu";
  // WXYZ is deliberately fixture-only; the hardware quaternion order is a
  // profile-gating fact and must be confirmed before deployment.
  const std::vector<std::pair<std::string, std::string>> imu_channels{
      {"accel_x", "m/s^2"}, {"accel_y", "m/s^2"}, {"accel_z", "m/s^2"},
      {"gyro_x", "rad/s"},   {"gyro_y", "rad/s"},   {"gyro_z", "rad/s"},
      {"quat_w", "unitless"}, {"quat_x", "unitless"},
      {"quat_y", "unitless"}, {"quat_z", "unitless"}};
  for (const auto& [channel_id, unit] : imu_channels) {
    sciencexyz::wireless::v1::ChannelDescriptor descriptor;
    descriptor.set_channel_id(channel_id);
    descriptor.set_label(channel_id);
    descriptor.set_unit(unit);
    imu.channels.push_back(std::move(descriptor));
  }
  config.sources.push_back(std::move(imu));
  return config;
}

std::vector<AdapterConfig> two_source_adapter_configs() {
  std::vector<AdapterConfig> configs;
  for (const auto& [source_id, rate, channels, format, tick_frequency] :
       std::vector<std::tuple<std::string, RationalRate, std::uint32_t,
                              sciencexyz::wireless::v1::SampleFormat, std::uint64_t>>{
           {"sim-emg-8ch", RationalRate{2048, 1}, 8,
            sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE, 2'048'000},
           {"sim-imu-10ch", RationalRate{128, 1}, 10,
            sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE, 1'000'000}}) {
    AdapterConfig adapter;
    adapter.source_id = source_id;
    adapter.topic = "wireless/v1/" + source_id;
    adapter.expected_sample_rate = rate;
    adapter.expected_channel_count = channels;
    adapter.expected_sample_format = format;
    adapter.max_batch_samples = 32;
    adapter.queue_capacity_batches = 16;
    adapter.clock.nominal_source_tick_frequency_hz =
        static_cast<double>(tick_frequency);
    configs.push_back(std::move(adapter));
  }
  return configs;
}

void test_two_source_emg_imu_adapter_and_recorder_boundary() {
  MultiSourceSimulator simulator(two_source_simulator_config());
  expect(simulator.valid(), "two-source EMG/IMU simulator configuration is valid");
  const auto generated = simulator.generate();
  expect(generated.size() == 7,
         "two-source simulator emits both native-rate streams and models loss");

  MultiSourceAdapter adapter(two_source_adapter_configs(), 16);
  expect(adapter.valid(), "bounded two-source adapter is valid");

  std::unordered_map<std::string, std::string> expected_payloads;
  for (const auto& item : generated) {
    const auto& batch = item.accepted.batch;
    const auto key = batch.source_id() + ":" + batch.boot_session_id() + ":" +
                     std::to_string(batch.batch_sequence());
    expected_payloads.emplace(key, batch.payload());
    expect(adapter.submit(item.accepted, item.host_receive_time_ns),
           "two-source simulator batches enter the aggregate adapter");
  }

  std::size_t emg_batches = 0;
  std::size_t imu_batches = 0;
  std::size_t recorder_records = 0;
  bool saw_sender_or_receiver_loss = false;
  while (auto normalized = adapter.pop_next()) {
    ++recorder_records;
    const auto& batch = normalized->accepted.batch;
    const auto key = batch.source_id() + ":" + batch.boot_session_id() + ":" +
                     std::to_string(batch.batch_sequence());
    expect(expected_payloads.contains(key) &&
               expected_payloads.at(key) == batch.payload(),
           "recorder boundary retains the exact source payload bytes");
    expect(normalized->host_receive_time_ns != 0,
           "recorder boundary receives an independent host receipt timestamp");
    expect(batch.source_acquisition_time_ns() != 0 &&
               batch.gateway_receive_time_ns() != 0 && batch.gateway_send_time_ns() != 0 &&
               !batch.gateway_id().empty() && !batch.gateway_session_id().empty() &&
               !batch.gateway_clock_id().empty(),
           "recorder boundary retains source, gateway, session, and clock metadata");
    expect(normalized->sample_times.size() == batch.sample_count(),
           "recorder boundary receives one source tick record per sample");
    expect(normalized->accepted.topic == "wireless/v1/" + batch.source_id(),
           "recorder boundary retains the exact logical-source topic");

    if (batch.source_id() == "sim-emg-8ch") {
      ++emg_batches;
      expect(batch.sample_rate_numerator_hz() == 2048 && batch.channel_count() == 8 &&
                 batch.sample_format() == sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE,
             "EMG fixture preserves native rate, channel count, and wire format");
      expect(batch.channels(0).channel_id() == "emg-0" &&
                 batch.channels(0).unit() == "uV",
             "EMG channel metadata reaches the recorder boundary");
    } else {
      ++imu_batches;
      expect(batch.sample_rate_numerator_hz() == 128 && batch.channel_count() == 10 &&
                 batch.sample_format() == sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE,
             "IMU fixture preserves native rate, channel count, and wire format");
      expect(batch.channels(0).channel_id() == "accel_x" &&
                 batch.channels(3).channel_id() == "gyro_x" &&
                 batch.channels(6).channel_id() == "quat_w" &&
                 batch.channels(9).channel_id() == "quat_z",
             "IMU accel/gyro/quaternion ordering reaches the recorder boundary");
    }
    for (const auto& diagnostic : normalized->diagnostics) {
      saw_sender_or_receiver_loss |=
          diagnostic.kind == AdapterDiagnosticKind::kBatchGap ||
          diagnostic.kind == AdapterDiagnosticKind::kSampleGap;
    }
  }
  expect(emg_batches == 4 && imu_batches == 3 && recorder_records == generated.size(),
         "recorder boundary drains all valid batches from both sources");
  for (const auto& diagnostic : adapter.drain_diagnostics()) {
    saw_sender_or_receiver_loss |=
        diagnostic.kind == AdapterDiagnosticKind::kBatchGap ||
        diagnostic.kind == AdapterDiagnosticKind::kSampleGap;
  }
  expect(saw_sender_or_receiver_loss,
         "source loss remains explicit after normalization and recorder draining");
}

void test_multi_source_bounds_and_legacy_alias() {
  MultiSourceAdapter empty({}, 1);
  expect(!empty.valid(), "empty aggregate adapter is rejected");
  MultiSourceAdapter too_many(std::vector<AdapterConfig>(5), 1);
  expect(!too_many.valid(), "aggregate adapter rejects more than four sources");
  FourSourceAdapter legacy(two_source_adapter_configs(), 16);
  expect(legacy.valid(), "legacy FourSourceAdapter name accepts two sources");
}

}  // namespace

int main() {
  try {
    test_affine_fit_uncertainty_and_reordering();
    test_wrap_reset_and_epoch_history();
    test_deterministic_four_source_adapter();
    test_two_source_emg_imu_adapter_and_recorder_boundary();
    test_multi_source_bounds_and_legacy_alias();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS affine clock estimator and four-source simulator/adapter\n";
  return 0;
}
