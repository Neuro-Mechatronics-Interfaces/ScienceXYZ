#include "task_timeline_adapter.hpp"

#include <utility>

#include "control_protocol.hpp"

namespace scifi2_hub::recording {
namespace {

using wire_v1 = stateful_decode_and_sync::v1::TaskEventKind;
using wire_trigger = stateful_decode_and_sync::v1::TaskTriggerKind;

}  // namespace

std::optional<TaskEventKind> TaskTimelineAdapter::value_event_kind(
    stateful_decode_and_sync::v1::TaskEventKind kind) {
  switch (kind) {
    case wire_v1::TASK_EVENT_START:
      return TaskEventKind::kStart;
    case wire_v1::TASK_EVENT_TRANSITION:
      return TaskEventKind::kTransition;
    case wire_v1::TASK_EVENT_ABORT:
      return TaskEventKind::kAbort;
    case wire_v1::TASK_EVENT_RESET:
      return TaskEventKind::kReset;
    default:
      return std::nullopt;
  }
}

// Canonical, stable trigger names persisted alongside the raw record so an
// analysis can distinguish why an external commit happened without decoding the
// wire enum.  These mirror the task-layer TriggerKind identities.
std::optional<std::string> TaskTimelineAdapter::value_trigger_kind(
    stateful_decode_and_sync::v1::TaskTriggerKind kind) {
  switch (kind) {
    case wire_trigger::TASK_TRIGGER_START_COMMAND:
      return std::string("start_command");
    case wire_trigger::TASK_TRIGGER_EXTERNAL_EVENT:
      return std::string("external_event");
    case wire_trigger::TASK_TRIGGER_SOURCE_TIMEOUT:
      return std::string("source_timeout");
    case wire_trigger::TASK_TRIGGER_DECODER_PREDICATE:
      return std::string("decoder_predicate");
    case wire_trigger::TASK_TRIGGER_ABORT_COMMAND:
      return std::string("abort_command");
    case wire_trigger::TASK_TRIGGER_RESET_COMMAND:
      return std::string("reset_command");
    default:
      return std::nullopt;
  }
}

TaskTimelineAdapter::TaskTimelineAdapter(TaskTimeline& timeline,
                                         std::string reference_source_id,
                                         HostClockFn host_clock)
    : timeline_(timeline),
      reference_source_id_(std::move(reference_source_id)),
      host_clock_(host_clock) {}

AdapterResult TaskTimelineAdapter::on_task_transition(
    const stateful_decode_and_sync::v1::TaskTransitionEvent& wire) {
  // Reuse the producer-side wire contract so the recorder refuses exactly what
  // the App refuses to publish, rather than inventing a second, looser rule.
  if (!protocol::validate_task_transition_event(wire)) {
    ++rejected_events_;
    return {false, AdapterRejection::kMalformedWireEvent};
  }
  if (!wire.has_effective_frame()) {
    ++rejected_events_;
    return {false, AdapterRejection::kMissingEffectiveFrame};
  }
  const auto event_kind = value_event_kind(wire.event_kind());
  if (!event_kind.has_value()) {
    ++rejected_events_;
    return {false, AdapterRejection::kUnknownEventKind};
  }
  const auto trigger_kind = value_trigger_kind(wire.trigger_kind());
  if (!trigger_kind.has_value()) {
    ++rejected_events_;
    return {false, AdapterRejection::kUnknownTriggerKind};
  }

  TaskTransitionRecord record;
  record.protocol_version = wire.protocol_version();
  record.definition_id = wire.definition_id();
  record.definition_revision = wire.definition_revision();
  record.definition_hash = wire.definition_hash();
  record.app_session_id = wire.app_session_id();
  record.run_sequence = wire.run_sequence();
  record.event_sequence = wire.event_sequence();
  record.transition_sequence = wire.transition_sequence();
  record.event_kind = *event_kind;
  record.transition_id = wire.transition_id();
  record.previous_state_id = wire.previous_state_id();
  record.current_state_id = wire.current_state_id();
  record.trigger_kind = std::move(*trigger_kind);
  record.trigger_source = wire.trigger_source();
  record.request_id = wire.request_id();
  record.proposal_receipt_sequence = wire.proposal_receipt_sequence();
  record.proposal_receipt_time_ns = wire.proposal_receipt_time_ns();
  record.effective_frame = {wire.effective_frame().source_id(),
                            wire.effective_frame().sequence_number(),
                            wire.effective_frame().timestamp_ns()};
  record.host_receive_time_ns = host_clock_();

  if (!timeline_.ingest(std::move(record))) {
    ++rejected_events_;
    return {false, AdapterRejection::kTimelineRejected};
  }
  ++accepted_events_;
  return {true, AdapterRejection::kNone};
}

void TaskTimelineAdapter::on_reference_frame(
    const ReferenceFrameObservation& frame) {
  ++reference_frames_;
  if (have_last_reference_) {
    const bool regressed =
        frame.sequence_number <= last_reference_sequence_ ||
        frame.timestamp_ns < last_reference_timestamp_ns_;
    const bool gapped = frame.sequence_number > last_reference_sequence_ + 1;
    if (regressed || gapped) {
      ++reference_discontinuities_;
      std::string detail = reference_source_id_;
      detail += regressed ? " reference frame regressed at sequence "
                          : " reference frame gap before sequence ";
      detail += std::to_string(frame.sequence_number);
      timeline_.mark_reference_discontinuity(std::move(detail));
    }
  }
  have_last_reference_ = true;
  last_reference_sequence_ = frame.sequence_number;
  last_reference_timestamp_ns_ = frame.timestamp_ns;
}

}  // namespace scifi2_hub::recording
