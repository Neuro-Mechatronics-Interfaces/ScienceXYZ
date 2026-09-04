#include "task_timeline.hpp"

#include <limits>
#include <utility>

namespace app::recording {
namespace {

bool starts_state(const TaskTransitionRecord& record) {
  return record.event_kind == TaskEventKind::kStart ||
         record.event_kind == TaskEventKind::kTransition;
}

bool closes_state(const TaskTransitionRecord& record) {
  return record.event_kind == TaskEventKind::kAbort ||
         record.event_kind == TaskEventKind::kReset;
}

}  // namespace

bool TaskTimeline::validate(const TaskTransitionRecord& record,
                            std::string& detail) const {
  if (record.protocol_version != 1 || record.definition_id.empty() ||
      record.definition_hash.empty() || record.app_session_id.empty() ||
      record.event_sequence == 0 || record.effective_frame.source_id.empty() ||
      record.effective_frame.sequence_number == 0 ||
      record.effective_frame.timestamp_ns == 0 || record.host_receive_time_ns == 0) {
    detail = "transition record omits a required authoritative or host-receipt field";
    return false;
  }
  if (starts_state(record) && record.current_state_id == 0) {
    detail = "start/transition event must open a nonzero state";
    return false;
  }
  if (closes_state(record) && record.current_state_id != 0) {
    detail = "abort/reset event must close to NO_STATE";
    return false;
  }
  return true;
}

void TaskTimeline::append_diagnostic(TimelineDiagnostic diagnostic) {
  diagnostics_.push_back(std::move(diagnostic));
  complete_ = false;
}

bool TaskTimeline::same_reference_source(const ReferenceFrame& frame) const {
  return !last_effective_frame_.has_value() ||
         last_effective_frame_->source_id == frame.source_id;
}

void TaskTimeline::close_open_interval(const ReferenceFrame& end, bool complete) {
  if (!open_interval_index_.has_value()) return;
  auto& interval = intervals_[*open_interval_index_];
  interval.end = end;
  interval.complete = interval.complete && complete;
  open_interval_index_.reset();
}

bool TaskTimeline::ingest(TaskTransitionRecord record) {
  std::string detail;
  if (!validate(record, detail)) {
    append_diagnostic({TimelineDiagnosticKind::kInvalidEvent, record.event_sequence, 0,
                       std::move(detail)});
    return false;
  }

  if (app_session_id_.has_value() && *app_session_id_ != record.app_session_id) {
    append_diagnostic({TimelineDiagnosticKind::kSessionMismatch, record.event_sequence, 0,
                       "one TaskTimeline accepts exactly one app session"});
    return false;
  }
  const auto identity = record.definition_id + "@" +
                        std::to_string(record.definition_revision) + ":" +
                        record.definition_hash;
  if (definition_identity_.has_value() && *definition_identity_ != identity) {
    append_diagnostic({TimelineDiagnosticKind::kDefinitionMismatch, record.event_sequence, 0,
                       "definition identity changed within an app session"});
    return false;
  }
  if (last_run_sequence_.has_value() && record.run_sequence < *last_run_sequence_) {
    append_diagnostic({TimelineDiagnosticKind::kRunSequenceRegression, record.run_sequence,
                       *last_run_sequence_, "run sequence regressed"});
    return false;
  }
  if (last_event_sequence_.has_value()) {
    const auto expected = *last_event_sequence_ + 1;
    if (record.event_sequence < expected) {
      append_diagnostic({TimelineDiagnosticKind::kDuplicateOrReorderedEvent,
                         record.event_sequence, expected,
                         "event sequence is duplicate or reordered"});
      return false;
    }
    if (record.event_sequence > expected) {
      append_diagnostic({TimelineDiagnosticKind::kEventSequenceGap,
                         record.event_sequence, expected,
                         "missing transition events make the preceding interval incomplete"});
      close_open_interval(record.effective_frame, false);
    }
  }
  if (!same_reference_source(record.effective_frame) ||
      (last_effective_frame_.has_value() &&
       (record.effective_frame.sequence_number < last_effective_frame_->sequence_number ||
        record.effective_frame.timestamp_ns < last_effective_frame_->timestamp_ns))) {
    append_diagnostic({TimelineDiagnosticKind::kReferenceFrameRegression,
                       record.effective_frame.sequence_number,
                       last_effective_frame_.has_value()
                           ? last_effective_frame_->sequence_number
                           : 0,
                       "reference source changed or frame boundary regressed"});
    close_open_interval(record.effective_frame, false);
  }

  if (!app_session_id_.has_value()) app_session_id_ = record.app_session_id;
  if (!definition_identity_.has_value()) definition_identity_ = identity;
  if (starts_state(record)) {
    close_open_interval(record.effective_frame, true);
    intervals_.push_back({record.app_session_id, record.run_sequence,
                          record.current_state_id, record.effective_frame,
                          std::nullopt, true});
    open_interval_index_ = intervals_.size() - 1;
  } else if (closes_state(record)) {
    close_open_interval(record.effective_frame, true);
  }

  last_run_sequence_ = record.run_sequence;
  last_event_sequence_ = record.event_sequence;
  last_effective_frame_ = record.effective_frame;
  records_.push_back(std::move(record));
  return true;
}

void TaskTimeline::mark_reference_discontinuity(std::string detail) {
  append_diagnostic({TimelineDiagnosticKind::kReferenceDiscontinuity, 0, 0,
                     std::move(detail)});
  if (open_interval_index_.has_value()) intervals_[*open_interval_index_].complete = false;
}

SampleTaskLabel TaskTimeline::label(const wireless::MappedTime& mapped_time) const {
  if (!mapped_time.bounded || !mapped_time.interval.has_value()) {
    return {SampleTaskLabelKind::kUnboundedClock, std::nullopt, std::nullopt};
  }
  const auto& sample = *mapped_time.interval;
  for (std::size_t index = 0; index < intervals_.size(); ++index) {
    const auto& interval = intervals_[index];
    const auto start = static_cast<std::int64_t>(interval.start.timestamp_ns);
    // An absent end means the interval is still live; model it as +inf so the
    // comparisons below need no conditional dereference (which GCC's
    // -Wmaybe-uninitialized mis-analyzes for std::optional under -O).
    const auto end = interval.end.has_value()
                         ? static_cast<std::int64_t>(interval.end->timestamp_ns)
                         : std::numeric_limits<std::int64_t>::max();
    if (sample.upper_bound_ns < start || sample.lower_bound_ns >= end) {
      continue;
    }
    if (!interval.complete || !complete_) {
      return {SampleTaskLabelKind::kIncompleteTimeline, std::nullopt, index};
    }
    if (sample.lower_bound_ns < start || sample.upper_bound_ns >= end) {
      return {SampleTaskLabelKind::kAmbiguousBoundary, std::nullopt, index};
    }
    return {SampleTaskLabelKind::kLabeled, interval.state_id, index};
  }
  return {SampleTaskLabelKind::kNoActiveState, std::nullopt, std::nullopt};
}

}  // namespace app::recording
