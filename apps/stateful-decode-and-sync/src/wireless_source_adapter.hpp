#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <vector>

#include "clock_estimator.hpp"
#include "wireless_ingress.hpp"

namespace app::wireless {

enum class AdapterDiagnosticKind {
  kInvalidBatch,
  kSessionReset,
  kBatchGap,
  kSampleGap,
  kDuplicateBatch,
  kReorderedBatch,
  kReorderedSamples,
  kQueueOverflow,
  kUnboundedClock,
  kUnknownSource,
};

struct AdapterDiagnostic {
  AdapterDiagnosticKind kind = AdapterDiagnosticKind::kInvalidBatch;
  std::size_t source_index = 0;
  std::string source_id;
  std::string boot_session_id;
  std::uint64_t observed = 0;
  std::uint64_t expected = 0;
  std::uint64_t count = 0;
  std::string detail;
};

struct AdapterConfig {
  std::string source_id;
  std::string topic;
  RationalRate expected_sample_rate;
  std::uint32_t expected_channel_count = 0;
  sciencexyz::wireless::v1::SampleFormat expected_sample_format =
      sciencexyz::wireless::v1::SAMPLE_FORMAT_UNSPECIFIED;
  std::size_t max_batch_samples = 4096;
  std::size_t queue_capacity_batches = 64;
  GapPolicy gap_policy = GapPolicy::kDiagnoseAndPreserve;
  ClockEstimatorConfig clock;
};

struct NormalizedSampleTime {
  std::uint32_t sample_index = 0;
  std::uint64_t source_sample_sequence = 0;
  std::uint64_t source_tick = 0;
  std::optional<TimeInterval> aligned_time;
};

// A value-owned normalized batch. The original AcceptedBatch is retained so
// downstream recording can write the exact protobuf, while the derived sample
// times and host stamp are explicit fields rather than replacements.
struct NormalizedWirelessBatch {
  AcceptedBatch accepted;
  std::uint64_t host_receive_time_ns = 0;
  std::vector<NormalizedSampleTime> sample_times;
  std::optional<ClockModel> clock_model;
  std::vector<AdapterDiagnostic> diagnostics;
};

class WirelessSourceAdapter {
 public:
  explicit WirelessSourceAdapter(AdapterConfig config);

  bool valid() const { return construction_error_.empty(); }
  const std::string& construction_error() const { return construction_error_; }

  // The host receipt timestamp is intentionally supplied separately from all
  // source and gateway timestamps in AcceptedBatch.
  bool submit(AcceptedBatch batch, std::uint64_t host_receive_time_ns);
  std::optional<NormalizedWirelessBatch> pop_next();

  ClockObservationResult observe_clock_sync(const ClockSyncSample& sample) {
    return clock_.observe(sample);
  }
  void begin_new_clock_epoch(std::uint64_t first_source_tick,
                             std::uint64_t reference_time_ns = 0) {
    clock_.begin_new_epoch(first_source_tick, reference_time_ns);
  }

  std::vector<AdapterDiagnostic> drain_diagnostics();
  std::size_t queued_batches() const { return queue_.size(); }
  const std::string& source_id() const { return config_.source_id; }
  const AdapterConfig& config() const { return config_; }
  const AffineClockEstimator& clock() const { return clock_; }

 private:
  static constexpr std::size_t kDiagnosticHistoryCapacity = 4096;
  static constexpr std::size_t kSequenceHistoryCapacity = 4096;

  bool validate_batch(const AcceptedBatch& accepted,
                      std::string& detail) const;
  void add_diagnostic(AdapterDiagnostic diagnostic,
                      std::vector<AdapterDiagnostic>* batch_diagnostics = nullptr);
  void add_gap_diagnostic(AdapterDiagnosticKind kind,
                          const sciencexyz::wireless::v1::WirelessBatch& batch,
                          std::uint64_t observed, std::uint64_t expected,
                          std::uint64_t count, std::string detail,
                          std::vector<AdapterDiagnostic>& batch_diagnostics);
  void remember_sequence(std::uint64_t batch_sequence);
  static std::uint64_t sample_tick(const sciencexyz::wireless::v1::WirelessBatch& batch,
                                   std::uint32_t sample_index);

  AdapterConfig config_;
  AffineClockEstimator clock_;
  std::string construction_error_;
  bool have_session_ = false;
  std::string boot_session_id_;
  bool have_last_batch_ = false;
  std::uint64_t last_batch_sequence_ = 0;
  std::uint64_t last_sample_end_ = 0;
  std::deque<std::uint64_t> sequence_history_;
  std::unordered_set<std::uint64_t> sequence_history_set_;
  std::deque<NormalizedWirelessBatch> queue_;
  std::deque<AdapterDiagnostic> diagnostics_;
};

class FourSourceAdapter {
 public:
  FourSourceAdapter(std::vector<AdapterConfig> configs,
                    std::size_t aggregate_queue_capacity = 256);

  bool valid() const { return construction_error_.empty(); }
  const std::string& construction_error() const { return construction_error_; }
  bool submit(AcceptedBatch batch, std::uint64_t host_receive_time_ns);
  std::optional<NormalizedWirelessBatch> pop_next();
  std::optional<ClockObservationResult> observe_clock_sync(
      const std::string& source_id, const ClockSyncSample& sample);
  std::vector<AdapterDiagnostic> drain_diagnostics();
  std::size_t queued_batches() const { return queued_batches_; }

 private:
  static constexpr std::size_t kMaxSources = 4;
  static constexpr std::size_t kDiagnosticHistoryCapacity = 4096;

  void add_diagnostic(AdapterDiagnostic diagnostic);

  std::vector<std::unique_ptr<WirelessSourceAdapter>> adapters_;
  std::size_t aggregate_queue_capacity_ = 0;
  std::size_t queued_batches_ = 0;
  std::size_t pop_cursor_ = 0;
  std::string construction_error_;
  std::deque<AdapterDiagnostic> diagnostics_;
};

}  // namespace app::wireless
