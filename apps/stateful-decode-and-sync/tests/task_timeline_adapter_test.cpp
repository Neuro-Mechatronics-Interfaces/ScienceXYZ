#include "task_timeline_adapter.hpp"

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

#include "control_protocol.hpp"
#include "task_timeline.hpp"

namespace {

using app::recording::AdapterRejection;
using app::recording::SampleTaskLabelKind;
using app::recording::TaskTimeline;
using app::recording::TaskTimelineAdapter;
using app::wireless::MappedTime;
using app::wireless::TimeInterval;

using stateful_decode_and_sync::v1::TaskEventKind;  // wire enum
using stateful_decode_and_sync::v1::TaskTransitionEvent;
using stateful_decode_and_sync::v1::TaskTriggerKind;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

// The wire validator requires a 71-character definition hash and a valid task
// name; use fixtures that satisfy the producer-side contract exactly.
const std::string kHash(71, 'a');

// A deterministic monotonically increasing host clock for receipt stamping.
std::uint64_t g_host_now_ns = 0;
std::uint64_t fake_host_clock() {
  g_host_now_ns += 10;
  return g_host_now_ns;
}

TaskTransitionEvent wire_event(std::uint64_t event_sequence,
                               stateful_decode_and_sync::v1::TaskEventKind kind,
                               std::uint32_t current_state,
                               std::uint32_t previous_state,
                               std::uint64_t frame_sequence,
                               std::uint64_t frame_timestamp_ns) {
  TaskTransitionEvent wire;
  wire.set_protocol_version(app::protocol::kProtocolVersion);
  wire.set_definition_id("cyclic.task");
  wire.set_definition_revision(7);
  wire.set_definition_hash(kHash);
  wire.set_app_session_id("app-session-a");
  wire.set_run_sequence(3);
  wire.set_event_sequence(event_sequence);
  wire.set_transition_sequence(event_sequence);
  wire.set_event_kind(kind);
  wire.set_previous_state_id(previous_state);
  wire.set_current_state_id(current_state);
  wire.set_trigger_kind(TaskTriggerKind::TASK_TRIGGER_START_COMMAND);
  wire.set_trigger_source("host");
  auto* boundary = wire.mutable_effective_frame();
  boundary->set_source_id("rhd2132");
  boundary->set_sequence_number(frame_sequence);
  boundary->set_timestamp_ns(frame_timestamp_ns);
  return wire;
}

MappedTime mapped(std::int64_t lower, std::int64_t upper) {
  MappedTime value;
  value.bounded = true;
  value.interval = TimeInterval{(lower + upper) / 2, lower, upper,
                                static_cast<std::uint64_t>(upper - lower)};
  return value;
}

void test_wire_events_flow_into_value_intervals() {
  g_host_now_ns = 0;
  TaskTimeline timeline;
  TaskTimelineAdapter adapter(timeline, "rhd2132", fake_host_clock);

  expect(adapter.on_task_transition(
             wire_event(1, TaskEventKind::TASK_EVENT_START, 11, 0, 100, 1'000))
             .accepted,
         "valid start event accepted through the adapter");
  expect(adapter.on_task_transition(
             wire_event(2, TaskEventKind::TASK_EVENT_TRANSITION, 12, 11, 101,
                        2'000))
             .accepted,
         "valid transition accepted through the adapter");
  expect(adapter.on_task_transition(
             wire_event(3, TaskEventKind::TASK_EVENT_ABORT, 0, 12, 102, 3'000))
             .accepted,
         "valid abort accepted through the adapter");

  expect(adapter.accepted_events() == 3 && adapter.rejected_events() == 0,
         "adapter counts exactly the accepted events");
  expect(timeline.records().size() == 3 && timeline.intervals().size() == 2,
         "raw records and derived intervals reach the value boundary");
  expect(timeline.records()[0].trigger_kind == "start_command",
         "the wire trigger enum is rendered as a stable canonical string");
  expect(timeline.records()[0].host_receive_time_ns == 10 &&
             timeline.records()[1].host_receive_time_ns == 20,
         "each record carries the injected host receipt time");

  const auto label = timeline.label(mapped(1'100, 1'200));
  expect(label.kind == SampleTaskLabelKind::kLabeled && label.state_id == 11,
         "a bounded sample inside the first interval is labeled via the adapter");
}

void test_wire_contract_rejections_do_not_reach_the_timeline() {
  g_host_now_ns = 0;
  TaskTimeline timeline;
  TaskTimelineAdapter adapter(timeline, "rhd2132", fake_host_clock);

  auto short_hash = wire_event(1, TaskEventKind::TASK_EVENT_START, 11, 0, 100,
                               1'000);
  short_hash.set_definition_hash("too-short");
  auto result = adapter.on_task_transition(short_hash);
  expect(!result.accepted &&
             result.rejection == AdapterRejection::kMalformedWireEvent,
         "a wire-invalid event is refused before the value boundary");

  auto missing_frame = wire_event(1, TaskEventKind::TASK_EVENT_START, 11, 0, 100,
                                  1'000);
  missing_frame.clear_effective_frame();
  result = adapter.on_task_transition(missing_frame);
  expect(!result.accepted,
         "a committed event without its governing frame is refused");

  auto unknown_kind = wire_event(1, TaskEventKind::TASK_EVENT_START, 11, 0, 100,
                                 1'000);
  unknown_kind.set_event_kind(
      stateful_decode_and_sync::v1::TASK_EVENT_UNSPECIFIED);
  result = adapter.on_task_transition(unknown_kind);
  expect(!result.accepted &&
             result.rejection == AdapterRejection::kMalformedWireEvent,
         "an unspecified event kind fails the wire contract");

  expect(timeline.records().empty() && timeline.intervals().empty(),
         "no rejected wire event fabricates a value record");
  expect(adapter.accepted_events() == 0 && adapter.rejected_events() == 3,
         "the adapter accounts for every rejection");
}

void test_reference_frame_discontinuity_marks_active_interval_incomplete() {
  g_host_now_ns = 0;
  TaskTimeline timeline;
  TaskTimelineAdapter adapter(timeline, "rhd2132", fake_host_clock);

  expect(adapter.on_task_transition(
             wire_event(1, TaskEventKind::TASK_EVENT_START, 11, 0, 100, 1'000))
             .accepted,
         "start opens an interval");

  adapter.on_reference_frame({100, 1'000});
  adapter.on_reference_frame({101, 1'050});
  expect(timeline.intervals()[0].complete,
         "a contiguous reference stream keeps the interval complete");

  adapter.on_reference_frame({104, 1'200});  // gap: 102 and 103 missing
  expect(adapter.reference_discontinuities() == 1,
         "a reference-stream gap is reported once");
  expect(!timeline.intervals()[0].complete,
         "a reference gap marks the active interval incomplete without a fake "
         "transition");
  expect(timeline.label(mapped(1'100, 1'150)).kind ==
             SampleTaskLabelKind::kIncompleteTimeline,
         "samples in the affected interval are explicitly incomplete");
}

}  // namespace

int main() {
  try {
    test_wire_events_flow_into_value_intervals();
    test_wire_contract_rejections_do_not_reach_the_timeline();
    test_reference_frame_discontinuity_marks_active_interval_incomplete();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS task timeline adapter\n";
  return 0;
}
