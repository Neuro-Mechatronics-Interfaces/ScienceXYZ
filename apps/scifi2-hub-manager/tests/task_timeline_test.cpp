#include "task_timeline.hpp"

#include <iostream>
#include <stdexcept>
#include <string>

namespace {

using scifi2_hub::recording::SampleTaskLabelKind;
using scifi2_hub::recording::TaskEventKind;
using scifi2_hub::recording::TaskTimeline;
using scifi2_hub::recording::TaskTransitionRecord;
using scifi2_hub::recording::TimelineDiagnosticKind;
using scifi2_hub::wireless::MappedTime;
using scifi2_hub::wireless::TimeInterval;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

TaskTransitionRecord event(std::uint64_t event_sequence, TaskEventKind kind,
                           std::uint32_t state, std::uint64_t frame_sequence,
                           std::uint64_t timestamp_ns) {
  TaskTransitionRecord value;
  value.definition_id = "cyclic-task";
  value.definition_revision = 7;
  value.definition_hash = "abc123";
  value.app_session_id = "app-session-a";
  value.run_sequence = 3;
  value.event_sequence = event_sequence;
  value.transition_sequence = event_sequence;
  value.event_kind = kind;
  value.current_state_id = state;
  value.effective_frame = {"rhd2132", frame_sequence, timestamp_ns};
  value.host_receive_time_ns = timestamp_ns + 50;
  return value;
}

MappedTime mapped(std::int64_t lower, std::int64_t upper) {
  MappedTime value;
  value.bounded = true;
  value.interval = TimeInterval{(lower + upper) / 2, lower, upper,
                                static_cast<std::uint64_t>(upper - lower)};
  return value;
}

void test_half_open_intervals_and_uncertainty() {
  TaskTimeline timeline;
  expect(timeline.ingest(event(1, TaskEventKind::kStart, 11, 100, 1'000)),
         "start opens a state interval");
  expect(timeline.ingest(event(2, TaskEventKind::kTransition, 12, 101, 2'000)),
         "transition closes old and opens new interval");
  expect(timeline.ingest(event(3, TaskEventKind::kAbort, 0, 102, 3'000)),
         "abort closes active interval");
  expect(timeline.records().size() == 3 && timeline.intervals().size() == 2,
         "all raw events and both state intervals are retained");
  expect(timeline.intervals()[0].start.timestamp_ns == 1'000 &&
             timeline.intervals()[0].end->timestamp_ns == 2'000 &&
             timeline.intervals()[1].start.timestamp_ns == 2'000 &&
             timeline.intervals()[1].end->timestamp_ns == 3'000,
         "transitions yield exact consecutive half-open boundaries");
  const auto in_first = timeline.label(mapped(1'100, 1'200));
  expect(in_first.kind == SampleTaskLabelKind::kLabeled && in_first.state_id == 11,
         "bounded sample inside an interval receives its task label");
  expect(timeline.label(mapped(1'950, 2'050)).kind ==
             SampleTaskLabelKind::kAmbiguousBoundary,
         "uncertainty crossing a boundary is never forced to either state");
  expect(timeline.label(mapped(2'000, 2'100)).kind == SampleTaskLabelKind::kLabeled &&
             timeline.label(mapped(2'000, 2'100)).state_id == 12,
         "the effective boundary belongs to the later half-open interval");
  expect(timeline.label(MappedTime{}).kind == SampleTaskLabelKind::kUnboundedClock,
         "unbounded clock mappings remain explicitly unlabeled");
}

void test_gap_and_reference_discontinuity_remain_observable() {
  TaskTimeline timeline;
  expect(timeline.ingest(event(10, TaskEventKind::kStart, 11, 100, 1'000)),
         "initial event accepted");
  expect(timeline.ingest(event(12, TaskEventKind::kTransition, 12, 102, 3'000)),
         "later event remains stored after a sequence gap");
  expect(!timeline.complete() && timeline.intervals().size() == 2 &&
             !timeline.intervals()[0].complete,
         "gap makes the affected preceding interval incomplete rather than inferred");
  expect(timeline.label(mapped(1'100, 1'200)).kind ==
             SampleTaskLabelKind::kIncompleteTimeline,
         "samples in an affected interval are explicitly incomplete");
  bool saw_gap = false;
  for (const auto& diagnostic : timeline.diagnostics()) {
    saw_gap |= diagnostic.kind == TimelineDiagnosticKind::kEventSequenceGap &&
               diagnostic.expected == 11 && diagnostic.observed == 12;
  }
  expect(saw_gap, "event-sequence gap carries exact expected/observed provenance");
  timeline.mark_reference_discontinuity("broadband tap sequence gap");
  expect(!timeline.intervals()[1].complete,
         "reference stream discontinuity marks the active interval incomplete");
}

void test_invalid_and_reordered_events_do_not_fabricate_history() {
  TaskTimeline timeline;
  expect(timeline.ingest(event(1, TaskEventKind::kStart, 11, 100, 1'000)),
         "valid event accepted");
  const auto duplicate = event(1, TaskEventKind::kTransition, 12, 101, 2'000);
  expect(!timeline.ingest(duplicate) && timeline.records().size() == 1,
         "duplicate event is not converted into a fabricated state interval");
  auto malformed = event(2, TaskEventKind::kAbort, 9, 102, 3'000);
  expect(!timeline.ingest(malformed) && timeline.records().size() == 1,
         "invalid abort cannot create a task-history record");
}

}  // namespace

int main() {
  try {
    test_half_open_intervals_and_uncertainty();
    test_gap_and_reference_discontinuity_remain_observable();
    test_invalid_and_reordered_events_do_not_fabricate_history();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS authoritative task timeline\n";
  return 0;
}
