#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "clock_estimator.hpp"

namespace app::recording {

// Value representation of gui_control.proto::TaskFrameBoundary.  The host
// tap adapter owns protobuf decoding; this recorder boundary deliberately
// stores an independent immutable copy rather than a protobuf pointer.
struct ReferenceFrame {
  std::string source_id;
  std::uint64_t sequence_number = 0;
  std::uint64_t timestamp_ns = 0;
};

enum class TaskEventKind {
  kStart,
  kTransition,
  kAbort,
  kReset,
};

// Value representation of the authoritative fields in TaskTransitionEvent.
// proposal metadata is retained because it explains why an external commit
// happened, but never changes the effective reference-frame boundary.
struct TaskTransitionRecord {
  std::uint32_t protocol_version = 1;
  std::string definition_id;
  std::uint64_t definition_revision = 0;
  std::string definition_hash;
  std::string app_session_id;
  std::uint64_t run_sequence = 0;
  std::uint64_t event_sequence = 0;
  std::uint64_t transition_sequence = 0;
  TaskEventKind event_kind = TaskEventKind::kStart;
  std::uint32_t transition_id = 0;
  std::uint32_t previous_state_id = 0;
  std::uint32_t current_state_id = 0;
  std::string trigger_kind;
  std::string trigger_source;
  std::string request_id;
  std::uint64_t proposal_receipt_sequence = 0;
  std::uint64_t proposal_receipt_time_ns = 0;
  ReferenceFrame effective_frame;
  std::uint64_t host_receive_time_ns = 0;
};

enum class TimelineDiagnosticKind {
  kInvalidEvent,
  kSessionMismatch,
  kDefinitionMismatch,
  kRunSequenceRegression,
  kEventSequenceGap,
  kDuplicateOrReorderedEvent,
  kReferenceFrameRegression,
  kReferenceDiscontinuity,
};

struct TimelineDiagnostic {
  TimelineDiagnosticKind kind = TimelineDiagnosticKind::kInvalidEvent;
  std::uint64_t observed = 0;
  std::uint64_t expected = 0;
  std::string detail;
};

// A state applies on [start, end).  An absent end is still live.  `complete`
// records whether the interval contains an event-sequence or reference-stream
// discontinuity; consumers must never upgrade it to a confident label.
struct TaskInterval {
  std::string app_session_id;
  std::uint64_t run_sequence = 0;
  std::uint32_t state_id = 0;
  ReferenceFrame start;
  std::optional<ReferenceFrame> end;
  bool complete = true;
};

enum class SampleTaskLabelKind {
  kLabeled,
  kAmbiguousBoundary,
  kIncompleteTimeline,
  kUnboundedClock,
  kNoActiveState,
};

struct SampleTaskLabel {
  SampleTaskLabelKind kind = SampleTaskLabelKind::kNoActiveState;
  std::optional<std::uint32_t> state_id;
  std::optional<std::size_t> interval_index;
};

// Recorder-side authority timeline.  It does not infer events from snapshots
// or commands.  Callers persist records(), intervals(), and diagnostics() in
// their raw HDF5 session so an analysis can distinguish known state from loss.
class TaskTimeline {
 public:
  bool ingest(TaskTransitionRecord record);

  // Marks the current reference-domain interval incomplete without inventing
  // a replacement transition.  Use this for a discovered broadband source
  // loss/reset or a tap transport discontinuity.
  void mark_reference_discontinuity(std::string detail);

  // Classifies one already-mapped auxiliary sample. A bounded interval which
  // touches either half-open task boundary remains ambiguous.
  SampleTaskLabel label(const wireless::MappedTime& mapped_time) const;

  const std::vector<TaskTransitionRecord>& records() const { return records_; }
  const std::vector<TaskInterval>& intervals() const { return intervals_; }
  const std::vector<TimelineDiagnostic>& diagnostics() const { return diagnostics_; }
  bool complete() const { return complete_; }

 private:
  bool validate(const TaskTransitionRecord& record, std::string& detail) const;
  void append_diagnostic(TimelineDiagnostic diagnostic);
  void close_open_interval(const ReferenceFrame& end, bool complete);
  bool same_reference_source(const ReferenceFrame& frame) const;

  std::vector<TaskTransitionRecord> records_;
  std::vector<TaskInterval> intervals_;
  std::vector<TimelineDiagnostic> diagnostics_;
  std::optional<std::size_t> open_interval_index_;
  std::optional<std::string> app_session_id_;
  std::optional<std::string> definition_identity_;
  std::optional<std::uint64_t> last_run_sequence_;
  std::optional<std::uint64_t> last_event_sequence_;
  std::optional<ReferenceFrame> last_effective_frame_;
  bool complete_ = true;
};

}  // namespace app::recording
