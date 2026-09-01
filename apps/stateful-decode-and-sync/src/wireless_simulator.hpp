#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "wireless_ingress.hpp"

namespace app::wireless {

struct SimulatedSourceConfig {
  std::string source_id;
  // Empty means the contract's conventional wireless/v1/<source_id> topic.
  std::string topic;
  RationalRate sample_rate{1000, 1};
  std::uint32_t channel_count = 2;
  sciencexyz::wireless::v1::SampleFormat sample_format =
      sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE;
  std::uint64_t source_tick_frequency_hz = 1'000'000;
  std::uint32_t samples_per_batch = 8;
  std::uint64_t batch_count = 16;
  std::uint64_t start_source_tick = 0;
  std::uint64_t start_reference_time_ns = 1'000'000'000;
  double clock_drift_ppm = 0.0;
  std::uint64_t gateway_jitter_ns = 0;
  std::uint32_t loss_every_n_batches = 0;
  bool reorder_adjacent_batches = false;
  std::optional<std::uint64_t> reset_at_batch;
  std::string gateway_id = "sim-gateway";
  std::string gateway_session_id = "sim-session";
  std::string gateway_clock_id = "sim-clock";
  sciencexyz::wireless::v1::SourceTimeDomain source_time_domain =
      sciencexyz::wireless::v1::SOURCE_TIME_DOMAIN_MONOTONIC_NS;
  std::vector<sciencexyz::wireless::v1::ChannelDescriptor> channels;
};

struct SimulatedAcceptedBatch {
  AcceptedBatch accepted;
  std::uint64_t host_receive_time_ns = 0;
};

struct MultiSourceSimulatorConfig {
  std::vector<SimulatedSourceConfig> sources;
  std::size_t max_output_batches = 4096;
};

using FourSourceSimulatorConfig = MultiSourceSimulatorConfig;

// Deterministic, hardware-free source publisher. Each source is configured
// once and emits the same WirelessBatch v1 envelope used by the gateway.
class MultiSourceSimulator {
 public:
  explicit MultiSourceSimulator(MultiSourceSimulatorConfig config);

  bool valid() const { return construction_error_.empty(); }
  const std::string& construction_error() const { return construction_error_; }
  std::vector<SimulatedAcceptedBatch> generate() const;

 private:
  static constexpr std::size_t kSourceCount = 4;

  MultiSourceSimulatorConfig config_;
  std::string construction_error_;
};

using FourSourceSimulator = MultiSourceSimulator;

}  // namespace app::wireless
