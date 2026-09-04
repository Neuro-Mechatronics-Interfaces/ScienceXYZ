#include "task_state.hpp"

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

#include <google/protobuf/struct.pb.h>
#include <google/protobuf/util/json_util.h>

namespace {

using namespace app::task;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

TaskDefinition make_definition() {
  TaskDefinition definition;
  definition.definition_id = "canonical_test";
  definition.revision = 7;
  definition.initial_state_id = 1;
  definition.states = {{1, "waiting", false}, {2, "active", false},
                       {3, "complete", true}};
  definition.transitions = {
      {10, "begin", 1, 2, 100, ExternalEventTrigger{"go"}},
      {11, "retry", 2, 1, 50, SourceTimeoutTrigger{100}},
      {12, "decoded_success", 2, 3, 100,
       DecoderPredicateTrigger{1, 800'000, 2}},
  };
  definition.source_policy =
      {LossAction::kFault, 1000, SequenceGapAction::kContinue, 5000};
  const auto result = validate_and_hash(definition, 3);
  expect(static_cast<bool>(result), "fixture definition validates: " + result.message);
  return definition;
}

Preconditions preconditions(const TaskRuntime& runtime) {
  const auto snapshot = runtime.snapshot();
  return {snapshot.app_session_id, snapshot.run_sequence, snapshot.transition_sequence,
          snapshot.current_state_id};
}

FrameBoundary frame(std::uint64_t sequence, std::uint64_t timestamp,
                    std::uint64_t steady = 0) {
  return {"broadband:1", sequence, timestamp, steady == 0 ? timestamp : steady};
}

void start(TaskRuntime& runtime, std::uint64_t sequence = 10,
           std::uint64_t timestamp = 1000) {
  const auto staged = runtime.stage_start("start", preconditions(runtime), timestamp - 10);
  expect(staged.accepted, "start stages");
  const auto boundary = runtime.on_frame(frame(sequence, timestamp));
  expect(boundary.event.has_value(), "start commits at a frame");
  expect(boundary.event->event_kind == EventKind::kStart &&
             boundary.event->previous_state_id == kNoState &&
             boundary.event->current_state_id == 1,
         "start event identifies NO_STATE to initial state");
}

void enter_active(TaskRuntime& runtime, std::uint64_t sequence = 11,
                  std::uint64_t timestamp = 2000) {
  const auto staged =
      runtime.stage_external_event("go", "go", preconditions(runtime), timestamp - 10);
  expect(staged.accepted, "external event stages");
  const auto boundary = runtime.on_frame(frame(sequence, timestamp));
  expect(boundary.event.has_value() && boundary.event->transition_id == 10 &&
             boundary.event->current_state_id == 2,
         "external event commits configured transition");
}

void test_validation_hash_and_parser() {
  auto definition = make_definition();
  expect(definition.definition_hash ==
             "sha256:76c40630fefc9f12cbf7d622ddfa22299bd3d6be3b5b4571ce4d3f84ba8660ca",
         "definition receives the independently verified canonical SHA-256 identity");
  auto reordered = definition;
  std::reverse(reordered.states.begin(), reordered.states.end());
  std::reverse(reordered.transitions.begin(), reordered.transitions.end());
  const auto reordered_result = validate_and_hash(reordered, 3);
  expect(static_cast<bool>(reordered_result) &&
             reordered.definition_hash == definition.definition_hash,
         "array order does not change normalized hash");
  reordered.transitions[0].priority = 99;
  const auto changed_result = validate_and_hash(reordered, 3);
  expect(static_cast<bool>(changed_result) &&
             reordered.definition_hash != definition.definition_hash,
         "semantic change changes normalized hash");

  const std::string json = R"json({
    "schema_version": 1,
    "definition_id": "parsed_task",
    "revision": 1,
    "initial_state_id": 1,
    "states": [
      {"id": 1, "name": "loop", "terminal": false},
      {"id": 2, "name": "done", "terminal": true}
    ],
    "transitions": [
      {"id": 1, "name": "again", "from_state_id": 1, "to_state_id": 1,
       "priority": 2, "trigger": {"kind": "external_event", "event_name": "repeat"}},
      {"id": 2, "name": "finish", "from_state_id": 1, "to_state_id": 2,
       "priority": 1, "trigger": {"kind": "decoder_predicate", "label": 0,
       "probability_threshold_ppm": 900000, "dwell_results": 2}}
    ],
    "source_policy": {"loss_action": "fault", "loss_timeout_ms": 1000,
      "sequence_gap_action": "continue", "staged_command_timeout_ms": 5000}
  })json";
  google::protobuf::Struct object;
  const auto json_result = google::protobuf::util::JsonStringToMessage(json, &object);
  expect(json_result.ok(), "protobuf parses task JSON fixture");
  google::protobuf::Value value;
  *value.mutable_struct_value() = object;
  const auto parsed = parse_task_definition(value, 2);
  expect(static_cast<bool>(parsed) && parsed.definition.states.size() == 2 &&
             parsed.definition.transitions.size() == 2,
         "typed parser accepts valid cyclic task");

  (*value.mutable_struct_value()->mutable_fields())["unexpected"].set_bool_value(true);
  const auto unknown = parse_task_definition(value, 2);
  expect(!static_cast<bool>(unknown) && unknown.result.error == DefinitionError::kUnknownField,
         "parser rejects unknown fields");
}

void test_invalid_graphs_are_rejected() {
  auto duplicate_priority = make_definition();
  duplicate_priority.transitions[1].priority = 100;
  const auto priority_result = validate_and_hash(duplicate_priority, 3);
  expect(!static_cast<bool>(priority_result) &&
             priority_result.error == DefinitionError::kDuplicate,
         "duplicate outgoing priorities are rejected");

  auto unreachable = make_definition();
  unreachable.states.push_back({4, "orphan", true});
  const auto unreachable_result = validate_and_hash(unreachable, 3);
  expect(!static_cast<bool>(unreachable_result) &&
             unreachable_result.error == DefinitionError::kInvalidTopology,
         "unreachable state is rejected");

  auto terminal_outgoing = make_definition();
  terminal_outgoing.transitions.push_back(
      {13, "illegal", 3, 1, 1, ExternalEventTrigger{"bad"}});
  const auto terminal_result = validate_and_hash(terminal_outgoing, 3);
  expect(!static_cast<bool>(terminal_result) &&
             terminal_result.error == DefinitionError::kInvalidTopology,
         "terminal state with outgoing edge is rejected");

  auto bad_decoder = make_definition();
  std::get<DecoderPredicateTrigger>(bad_decoder.transitions[2].trigger).label = 3;
  const auto decoder_result = validate_and_hash(bad_decoder, 3);
  expect(!static_cast<bool>(decoder_result) &&
             decoder_result.error == DefinitionError::kInvalidTrigger,
         "decoder label outside configured classes is rejected");
}

void test_lifecycle_preconditions_duplicates_and_conflicts() {
  TaskRuntime runtime(make_definition(), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  const auto initial_preconditions = preconditions(runtime);
  expect(runtime.stage_start("first", initial_preconditions, 1).accepted,
         "first start accepted");
  const auto duplicate = runtime.stage_start("first", initial_preconditions, 2);
  expect(!duplicate.accepted && duplicate.error == RuntimeError::kDuplicate,
         "duplicate staged request id is rejected");
  expect(runtime.stage_start("second", initial_preconditions, 3).accepted,
         "distinct simultaneous start accepted for deterministic resolution");
  const auto start_boundary = runtime.on_frame(frame(1, 100));
  expect(start_boundary.event.has_value() && start_boundary.event->request_id == "first" &&
             start_boundary.failed_proposals.size() == 1 &&
             start_boundary.failed_proposals[0].request_id == "second",
         "earliest lifecycle proposal wins and loser terminates");

  const auto stale = runtime.stage_external_event("stale", "go", initial_preconditions, 110);
  expect(!stale.accepted && stale.error == RuntimeError::kStalePrecondition,
         "pre-boundary snapshot cannot propose after commit");

  const auto current = preconditions(runtime);
  expect(runtime.stage_external_event("go-one", "go", current, 120).accepted,
         "first external proposal accepted");
  expect(runtime.stage_external_event("go-two", "go", current, 121).accepted,
         "second external proposal accepted");
  const auto transition = runtime.on_frame(frame(2, 200));
  expect(transition.event.has_value() && transition.event->transition_id == 10 &&
             transition.event->request_id == "go-one" &&
             transition.failed_proposals.size() == 1 &&
             transition.failed_proposals[0].error == RuntimeError::kConflict,
         "same-transition proposals use receipt sequence and expose conflict");
}

void test_source_timer_boundary() {
  TaskRuntime runtime(make_definition(), "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
  start(runtime);
  enter_active(runtime);
  expect(!runtime.on_frame(frame(12, 2099)).event.has_value(),
         "timer does not commit before deadline");
  const auto deadline = runtime.on_frame(frame(13, 2100));
  expect(deadline.event.has_value() && deadline.event->transition_id == 11 &&
             deadline.event->trigger_kind == TriggerKind::kSourceTimeout &&
             deadline.event->effective_frame.sequence_number == 13,
         "timer commits on first frame at exact source deadline");
}

void test_direct_transition_proposal() {
  TaskRuntime runtime(make_definition(), "abababababababababababababababab");
  start(runtime);
  const auto illegal_timer =
      runtime.stage_transition("timer", 11, preconditions(runtime), 1010);
  expect(!illegal_timer.accepted &&
             illegal_timer.error == RuntimeError::kNoMatchingTransition,
         "client cannot directly propose a timer transition");
  const auto direct = runtime.stage_transition("direct", 10, preconditions(runtime), 1020);
  expect(direct.accepted, "client can propose an external configured transition id");
  const auto boundary = runtime.on_frame(frame(11, 1100));
  expect(boundary.event.has_value() && boundary.event->transition_id == 10 &&
             boundary.event->request_id == "direct",
         "direct proposal still commits only through the configured edge");
}

void test_each_frame_in_a_transport_batch_is_a_boundary() {
  TaskRuntime runtime(make_definition(), "91919191919191919191919191919191");
  start(runtime);
  expect(runtime.stage_external_event("go-in-batch", "go", preconditions(runtime), 150).accepted,
         "external event stages before a multi-frame receive");

  // This models the application ReadBatch loop: a transport batch is not one
  // task boundary. The first frame commits go; the next frame in the same
  // batch independently reaches the timer deadline from that committed state.
  const std::vector<FrameBoundary> receive_batch = {frame(11, 1100), frame(12, 1200)};
  std::vector<TransitionEvent> events;
  for (const auto& item : receive_batch) {
    const auto result = runtime.on_frame(item);
    if (result.event) events.push_back(*result.event);
  }
  expect(events.size() == 2 && events[0].transition_id == 10 &&
             events[0].effective_frame.sequence_number == 11 &&
             events[1].transition_id == 11 &&
             events[1].effective_frame.sequence_number == 12,
         "each valid frame in wire order gets its own unsuppressed boundary evaluation");
}

void test_decoder_dwell_and_priority() {
  TaskRuntime runtime(make_definition(), "cccccccccccccccccccccccccccccccc");
  start(runtime);
  enter_active(runtime);
  runtime.observe_decoder(1, 900'000, 2010);
  runtime.observe_decoder(1, 700'000, 2020);
  runtime.observe_decoder(1, 900'000, 2030);
  expect(!runtime.on_frame(frame(12, 2050)).event.has_value(),
         "non-qualifying decoder result resets dwell");
  runtime.observe_decoder(1, 700'000, 2060);
  expect(!runtime.on_frame(frame(13, 2099)).event.has_value(),
         "reset decoder dwell and timer both remain unready");
  runtime.observe_decoder(1, 900'000, 2099);
  runtime.observe_decoder(1, 900'000, 2099);
  const auto boundary = runtime.on_frame(frame(14, 2100));
  expect(boundary.event.has_value() && boundary.event->transition_id == 12 &&
             boundary.event->trigger_kind == TriggerKind::kDecoderPredicate &&
             boundary.event->current_state_id == 3,
         "ready decoder beats simultaneously due lower-priority timer");
  expect(runtime.snapshot().lifecycle == Lifecycle::kCompleted,
         "terminal transition atomically enters COMPLETED");
}

void test_cyclic_external_timer_and_decoder_simulation() {
  // One deterministic, hardware-free run exercises all three task trigger
  // authorities while preserving one exact reference boundary per commit.
  TaskRuntime runtime(make_definition(), "c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1");
  std::vector<TransitionEvent> events;
  std::uint64_t sequence = 1;
  std::uint64_t timestamp = 1'000;

  expect(runtime.stage_start("sim-start", preconditions(runtime), 1).accepted,
         "cyclic simulation start stages");
  auto result = runtime.on_frame(frame(sequence++, timestamp));
  expect(result.event.has_value(), "cyclic simulation start commits");
  events.push_back(*result.event);

  for (std::uint32_t cycle = 0; cycle < 3; ++cycle) {
    timestamp += 50;
    expect(runtime.stage_external_event("sim-go-" + std::to_string(cycle), "go",
                                        preconditions(runtime), timestamp - 1).accepted,
           "external event stages for each cyclic pass");
    result = runtime.on_frame(frame(sequence++, timestamp));
    expect(result.event.has_value() && result.event->transition_id == 10 &&
               result.event->effective_frame.sequence_number == sequence - 1,
           "external event commits on its first subsequent reference frame");
    events.push_back(*result.event);

    if (cycle < 2) {
      expect(!runtime.on_frame(frame(sequence++, timestamp + 99)).event.has_value(),
             "timer remains uncommitted before its source-time deadline");
      timestamp += 100;
      result = runtime.on_frame(frame(sequence++, timestamp));
      expect(result.event.has_value() && result.event->transition_id == 11 &&
                 result.event->trigger_kind == TriggerKind::kSourceTimeout,
             "timer returns cyclic task to waiting at the deadline frame");
      events.push_back(*result.event);
    }
  }

  runtime.observe_decoder(1, 800'000, timestamp + 1);
  runtime.observe_decoder(1, 800'000, timestamp + 2);
  timestamp += 1;
  result = runtime.on_frame(frame(sequence++, timestamp));
  expect(result.event.has_value() && result.event->transition_id == 12 &&
             result.event->trigger_kind == TriggerKind::kDecoderPredicate &&
             runtime.snapshot().lifecycle == Lifecycle::kCompleted,
         "decoder dwell closes the final active cyclic pass on a frame boundary");
  events.push_back(*result.event);

  expect(events.size() == 7, "cyclic simulation has one start plus six committed boundaries");
  for (std::size_t index = 0; index < events.size(); ++index) {
    expect(events[index].event_sequence == index + 1 &&
               (index == 0 || events[index - 1].effective_frame.sequence_number <
                                  events[index].effective_frame.sequence_number),
           "simulation preserves contiguous event sequences and increasing frame attribution");
  }
}

void test_abort_reset_and_new_run() {
  TaskRuntime runtime(make_definition(), "dddddddddddddddddddddddddddddddd");
  start(runtime);
  const auto before_abort = preconditions(runtime);
  expect(runtime.stage_external_event("go", "go", before_abort, 1100).accepted,
         "normal transition stages before abort");
  expect(runtime.stage_abort("abort", before_abort, 1101).accepted,
         "abort stages concurrently");
  const auto aborted = runtime.on_frame(frame(11, 1200));
  expect(aborted.event.has_value() && aborted.event->event_kind == EventKind::kAbort &&
             aborted.event->current_state_id == kNoState &&
             aborted.event->request_id == "abort" &&
             runtime.snapshot().lifecycle == Lifecycle::kAborted,
         "abort wins and closes active interval at its frame");

  expect(runtime.stage_reset("reset", preconditions(runtime), 1210).accepted,
         "reset stages from ABORTED with healthy source");
  const auto reset = runtime.on_frame(frame(12, 1300));
  expect(reset.event.has_value() && reset.event->event_kind == EventKind::kReset &&
             runtime.snapshot().lifecycle == Lifecycle::kIdle,
         "reset returns to IDLE at a real frame");

  expect(runtime.stage_start("restart", preconditions(runtime), 1310).accepted,
         "new run stages after reset");
  const auto restarted = runtime.on_frame(frame(13, 1400));
  expect(restarted.event.has_value() && restarted.event->run_sequence == 2 &&
             restarted.event->transition_sequence == 1,
         "new run increments run and restarts transition sequence");
}

void test_expiry_source_fault_and_session_rollover() {
  auto definition = make_definition();
  definition.source_policy.staged_command_timeout_ms = 1;
  expect(static_cast<bool>(validate_and_hash(definition, 3)), "short expiry validates");
  TaskRuntime runtime(definition, "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee");
  expect(runtime.stage_start("expire", preconditions(runtime), 100).accepted,
         "expiring command stages");
  const auto expired = runtime.expire_staged(1'000'100);
  expect(expired.size() == 1 && expired[0].error == RuntimeError::kExpired,
         "staged command expires by steady time without a frame");

  start(runtime, 1, 2'000'000);
  const auto source_fault = runtime.poll_source_loss(1'002'000'000);
  expect(source_fault.entered_fault && runtime.snapshot().lifecycle == Lifecycle::kFault &&
             !runtime.snapshot().source_healthy,
         "source-loss timeout faults without fabricating an event");
  const auto unhealthy_reset = runtime.stage_reset("too-soon", preconditions(runtime),
                                                   1'002'000'001);
  expect(!unhealthy_reset.accepted &&
             unhealthy_reset.error == RuntimeError::kInvalidLifecycle,
         "fault reset is rejected until source health returns");
  runtime.mark_source_healthy(true);
  expect(runtime.stage_reset("recover", preconditions(runtime), 1'002'000'002).accepted,
         "fault reset stages after source health returns");
  const auto recovered = runtime.on_frame(frame(2, 2'000'100, 1'002'000'003));
  expect(recovered.event.has_value() && recovered.event->event_kind == EventKind::kReset &&
             runtime.snapshot().lifecycle == Lifecycle::kIdle,
         "fault recovery uses the next real reference frame");

  TaskRuntime next_session(make_definition(), "ffffffffffffffffffffffffffffffff");
  start(next_session, 1, 100);
  const auto next = next_session.snapshot();
  expect(next.run_sequence == 1 && next.event_sequence == 1 &&
             next.app_session_id != runtime.snapshot().app_session_id,
         "new app session disambiguates restarted counters");

  TaskRuntime exhausted(make_definition(), "0123456789abcdef0123456789abcdef",
                        {std::numeric_limits<std::uint64_t>::max(), 0, 0});
  expect(exhausted.stage_start("overflow", preconditions(exhausted), 1).accepted,
         "overflow is detected at commit, not staging");
  const auto overflow = exhausted.on_frame(frame(1, 10));
  expect(overflow.entered_fault && overflow.fault_error == RuntimeError::kCounterOverflow,
         "run counter exhaustion faults without wrapping");
}

void test_non_monotonic_reference_faults() {
  TaskRuntime runtime(make_definition(), "11111111111111111111111111111111");
  start(runtime, 20, 1000);
  const auto fault = runtime.on_frame(frame(20, 1001));
  expect(fault.entered_fault && fault.fault_error == RuntimeError::kSourceFault &&
             runtime.snapshot().lifecycle == Lifecycle::kFault,
         "repeated reference sequence faults authority");
  expect(!fault.event.has_value(), "source fault does not fabricate transition event");
}

void test_sequence_gap_policy() {
  TaskRuntime continuing(make_definition(), "22222222222222222222222222222222");
  start(continuing, 1, 1000);
  const auto continued = continuing.on_frame(frame(3, 1001));
  expect(!continued.entered_fault && continuing.snapshot().lifecycle == Lifecycle::kRunning,
         "continue policy preserves authority across an increasing sequence gap");

  auto strict_definition = make_definition();
  strict_definition.source_policy.sequence_gap_action = SequenceGapAction::kFault;
  expect(static_cast<bool>(validate_and_hash(strict_definition, 3)),
         "strict sequence-gap policy validates");
  TaskRuntime strict(strict_definition, "33333333333333333333333333333333");
  start(strict, 1, 1000);
  const auto faulted = strict.on_frame(frame(3, 1001));
  expect(faulted.entered_fault && strict.snapshot().lifecycle == Lifecycle::kFault,
         "fault policy rejects an increasing sequence gap");
}

}  // namespace

int main() {
  try {
    test_validation_hash_and_parser();
    test_invalid_graphs_are_rejected();
    test_lifecycle_preconditions_duplicates_and_conflicts();
    test_source_timer_boundary();
    test_direct_transition_proposal();
    test_each_frame_in_a_transport_batch_is_a_boundary();
    test_decoder_dwell_and_priority();
    test_cyclic_external_timer_and_decoder_simulation();
    test_abort_reset_and_new_run();
    test_expiry_source_fault_and_session_rollover();
    test_non_monotonic_reference_faults();
    test_sequence_gap_policy();
  } catch (const std::exception& error) {
    std::cerr << "FAIL " << error.what() << '\n';
    return EXIT_FAILURE;
  }
  std::cout << "PASS authoritative task-state core\n";
  return EXIT_SUCCESS;
}
