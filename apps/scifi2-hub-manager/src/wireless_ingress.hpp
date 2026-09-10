#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <vector>

#include "wireless/v1/wireless_batch.pb.h"

namespace scifi2_hub::wireless {

// The transport boundary deliberately carries frames, not a partially parsed
// protobuf. This keeps the exact two-frame envelope testable without hardware
// and makes malformed input observable before any source state is changed.
struct MultipartMessage {
  std::vector<std::string> frames;
};

class NonblockingReader {
 public:
  virtual ~NonblockingReader() = default;

  // Return false only when no complete multipart message is currently ready.
  // A returned message may contain any frame count; the ingress validator
  // rejects everything other than the v1 two-frame envelope.
  virtual bool try_receive(MultipartMessage& message) = 0;
};

enum class GapPolicy {
  kDiagnoseAndPreserve,
  kRejectSourceOnGap,
};

struct RationalRate {
  std::uint64_t numerator_hz = 0;
  std::uint64_t denominator = 0;
};

struct SourceConfig {
  std::string source_id;
  std::string topic;
  std::string expected_gateway_id;
  RationalRate expected_sample_rate;
  std::uint32_t expected_channel_count = 0;
  sciencexyz::wireless::v1::SampleFormat expected_sample_format =
      sciencexyz::wireless::v1::SAMPLE_FORMAT_UNSPECIFIED;
  std::size_t max_batch_samples = 4096;
  std::size_t queue_capacity_batches = 1;
  bool allow_replay = true;
};

struct IngressConfig {
  std::vector<SourceConfig> sources;
  std::size_t queue_capacity_batches = 1;
  std::size_t max_batch_bytes = 1024;
  GapPolicy gap_policy = GapPolicy::kDiagnoseAndPreserve;
};

enum class DiagnosticKind {
  kMalformedEnvelope,
  kMalformedBatch,
  kSessionReset,
  kSenderGap,
  kBatchGap,
  kSampleGap,
  kDuplicateBatch,
  kReorderedBatch,
  kReorderedSamples,
  kQueueOverflow,
  kSourceRejected,
};

struct Diagnostic {
  DiagnosticKind kind = DiagnosticKind::kMalformedBatch;
  std::size_t source_index = 0;
  std::string source_id;
  std::string boot_session_id;
  std::uint64_t observed = 0;
  std::uint64_t expected = 0;
  std::uint64_t count = 0;
  std::string detail;
};

struct AcceptedBatch {
  std::size_t source_index = 0;
  std::string topic;
  sciencexyz::wireless::v1::WirelessBatch batch;
};

struct PollStats {
  std::size_t readers_polled = 0;
  std::size_t envelopes_received = 0;
  std::size_t batches_accepted = 0;
  std::size_t batches_rejected = 0;
  std::size_t batches_deduplicated = 0;
};

struct SourceStats {
  std::size_t envelopes_received = 0;
  std::size_t accepted_batches = 0;
  std::size_t rejected_batches = 0;
  std::size_t duplicate_batches = 0;
  std::size_t queue_overflow_batches = 0;
  std::uint64_t sender_dropped_batches = 0;
  std::uint64_t sender_dropped_samples = 0;
};

// SDK-independent, App-owned ingress mux. Each reader is called only by
// poll_once(); no reader is shared with another owner or worker thread.
class IngressMux {
 public:
  IngressMux(IngressConfig config, std::vector<std::unique_ptr<NonblockingReader>> readers);

  bool valid() const { return construction_error_.empty(); }
  const std::string& construction_error() const { return construction_error_; }

  // Poll each reader once, beginning at the rotating cursor. A reader must
  // never block; at most one message per reader is admitted per pass.
  PollStats poll_once();

  // Pop accepted batches in source round-robin order. The returned object owns
  // the protobuf and all received metadata, so the caller may process it later.
  std::optional<AcceptedBatch> pop_next();

  // Diagnostics are bounded by kDiagnosticHistoryCapacity. Counters in
  // source_stats() remain available after the diagnostic history is drained.
  std::vector<Diagnostic> drain_diagnostics();
  const std::vector<SourceStats>& source_stats() const { return source_stats_; }
  std::size_t queued_batches() const { return queued_batches_; }

 private:
  struct SourceState {
    bool have_session = false;
    std::string boot_session_id;
    bool have_last_batch = false;
    std::uint64_t last_batch_sequence = 0;
    std::uint64_t last_sample_end = 0;
    bool source_rejected = false;
    std::deque<std::uint64_t> sequence_history;
    std::unordered_set<std::uint64_t> sequence_history_set;
    std::deque<AcceptedBatch> queue;
  };

  static constexpr std::size_t kMaxSources = 4;
  static constexpr std::size_t kSequenceHistoryCapacity = 4096;
  static constexpr std::size_t kDiagnosticHistoryCapacity = 4096;

  std::optional<AcceptedBatch> validate_message(
      std::size_t source_index, const MultipartMessage& message,
      std::vector<Diagnostic>& diagnostics);
  bool validate_batch(const SourceConfig& source, const std::string& serialized,
                      sciencexyz::wireless::v1::WirelessBatch& batch,
                      std::string& detail) const;
  bool validate_config();
  void add_diagnostic(Diagnostic diagnostic);
  void add_source_diagnostic(std::size_t source_index, DiagnosticKind kind,
                             const sciencexyz::wireless::v1::WirelessBatch& batch,
                             std::uint64_t observed, std::uint64_t expected,
                             std::uint64_t count, std::string detail,
                             std::vector<Diagnostic>& batch_diagnostics);
  void remember_batch_sequence(SourceState& state, std::uint64_t sequence);

  IngressConfig config_;
  std::vector<std::unique_ptr<NonblockingReader>> readers_;
  std::vector<SourceState> source_states_;
  std::vector<SourceStats> source_stats_;
  std::deque<Diagnostic> diagnostics_;
  std::string construction_error_;
  std::size_t poll_cursor_ = 0;
  std::size_t pop_cursor_ = 0;
  std::size_t queued_batches_ = 0;
};

}  // namespace scifi2_hub::wireless
