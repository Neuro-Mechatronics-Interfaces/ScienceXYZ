#include "task_recorder_writer.hpp"

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "clock_estimator.hpp"
#include "task_timeline.hpp"

namespace {

using app::recording::RecordingControlEvent;
using app::recording::RecordingControlKind;
using app::recording::RecordSink;
using app::recording::TaskEventKind;
using app::recording::TaskRecorderHdf5Writer;
using app::recording::TaskTimeline;
using app::recording::TaskTransitionRecord;
using app::recording::kTaskTimelineSchema;
using app::wireless::AffineClockEstimator;
using app::wireless::ClockModel;
using app::wireless::ClockSyncSample;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

// In-memory RecordSink for the SDK/HDF5-free host-tests build.  It captures the
// value structures the writer hands it so a test can assert on both call
// ordering and content without any serialization backend.
struct FakeRecordSink : RecordSink {
  struct Snapshot {
    std::vector<TaskTransitionRecord> records;
    std::vector<app::recording::TaskInterval> intervals;
    std::vector<app::recording::TimelineDiagnostic> diagnostics;
    std::vector<ClockModel> epochs;
    bool complete = true;
  };

  bool open(const std::string& schema_version,
            std::uint64_t created_host_time_ns,
            const std::string& metadata_json) override {
    ++open_calls;
    schema = schema_version;
    created = created_host_time_ns;
    metadata = metadata_json;
    return open_ok;
  }
  bool write_control_event(const RecordingControlEvent& event) override {
    control_events.push_back(event);
    return control_ok;
  }
  bool write_transition_records(
      const std::vector<TaskTransitionRecord>& records) override {
    pending.records = records;
    return records_ok;
  }
  bool write_intervals(
      const std::vector<app::recording::TaskInterval>& intervals) override {
    pending.intervals = intervals;
    return true;
  }
  bool write_diagnostics(
      const std::vector<app::recording::TimelineDiagnostic>& diagnostics)
      override {
    pending.diagnostics = diagnostics;
    return true;
  }
  bool write_clock_epochs(const std::vector<ClockModel>& epochs) override {
    pending.epochs = epochs;
    return true;
  }
  bool write_timeline_complete(bool complete) override {
    pending.complete = complete;
    snapshots.push_back(pending);  // a completed snapshot ends on this call
    return true;
  }
  bool close() override {
    ++close_calls;
    return true;
  }

  int open_calls = 0;
  int close_calls = 0;
  bool open_ok = true;
  bool control_ok = true;
  bool records_ok = true;
  std::string schema;
  std::uint64_t created = 0;
  std::string metadata;
  std::vector<RecordingControlEvent> control_events;
  Snapshot pending;
  std::vector<Snapshot> snapshots;
};

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

ClockSyncSample sync_sample(std::uint64_t tick, std::uint64_t reference_ns) {
  ClockSyncSample sample;
  sample.source_tick = tick;
  sample.reference_time_ns = reference_ns;
  sample.round_trip_time_ns = 200;
  sample.uncertainty_ns = 100;
  return sample;
}

// A clock estimator whose active epoch is locked (>=2 well-separated samples,
// positive slope). epochs() stays empty -- the active model lives in
// current_model() -- so this also verifies the writer persists the active
// model rather than only the completed epochs().
AffineClockEstimator locked_active_clock() {
  AffineClockEstimator clock;
  clock.observe(sync_sample(1'000, 1'000'000));
  clock.observe(sync_sample(2'000, 2'000'000));
  return clock;
}

void test_open_start_flush_stop_close_ordering() {
  FakeRecordSink sink;
  TaskRecorderHdf5Writer writer(sink);

  expect(!writer.is_open() && !writer.is_active(), "writer starts idle");
  expect(writer.open(42, "{\"software\":\"x\"}"), "open succeeds once");
  expect(sink.open_calls == 1 && sink.schema == kTaskTimelineSchema &&
             sink.created == 42 && sink.metadata == "{\"software\":\"x\"}",
         "header carries schema, created time, and verbatim metadata");
  expect(!writer.open(43, "{}"), "a second open is refused");

  TaskTimeline timeline;
  timeline.ingest(event(1, TaskEventKind::kStart, 11, 100, 1'000));
  timeline.ingest(event(2, TaskEventKind::kTransition, 12, 101, 2'000));
  AffineClockEstimator clock = locked_active_clock();

  expect(!writer.flush(timeline, clock), "flush before start is refused");
  expect(writer.start("rec-1", 50, "req-1"), "start opens an epoch");
  expect(!writer.start("rec-2", 51, "req-2"), "second concurrent start refused");
  expect(sink.control_events.size() == 1 &&
             sink.control_events[0].kind == RecordingControlKind::kStart &&
             sink.control_events[0].recording_session_id == "rec-1",
         "start emits exactly one acknowledged start control event");

  expect(writer.flush(timeline, clock), "flush during an epoch succeeds");
  expect(sink.snapshots.size() == 1, "flush emits one complete snapshot");
  expect(sink.snapshots.back().records.size() == 2 &&
             sink.snapshots.back().intervals.size() == 2,
         "snapshot mirrors the authoritative records and intervals");
  // epochs() is empty (the epoch is still active), but the writer must persist
  // the active locked model so no clock model that mapped samples is dropped.
  expect(clock.epochs().empty() && clock.current_model().locked,
         "the estimator exposes the active model only through current_model()");
  expect(sink.snapshots.back().epochs.size() == 1 &&
             sink.snapshots.back().epochs.front().model_id ==
                 clock.current_model().model_id,
         "snapshot carries the active clock model, not only completed epochs");
  expect(sink.snapshots.back().complete == timeline.complete(),
         "snapshot records the timeline completeness flag");

  expect(!writer.close(), "close is refused while an epoch is active");
  expect(writer.stop(timeline, clock, "rec-1", 90, "req-stop", 0, 0, true),
         "stop with the matching session id succeeds");
  expect(sink.snapshots.size() == 2, "stop flushes a final snapshot");
  expect(sink.control_events.size() == 2 &&
             sink.control_events[1].kind == RecordingControlKind::kStop,
         "stop emits an acknowledged stop control event");
  expect(!writer.is_active(), "writer is inactive after stop");
  expect(writer.close() && sink.close_calls == 1, "close succeeds once idle");
  expect(!writer.close(), "a second close is refused");
}

void test_stop_requires_matching_session_and_records_loss() {
  FakeRecordSink sink;
  TaskRecorderHdf5Writer writer(sink);
  writer.open(1, "{}");
  writer.start("rec-A", 10, "req");

  TaskTimeline timeline;
  AffineClockEstimator clock;
  expect(!writer.stop(timeline, clock, "rec-B", 20, "req", 0, 0, true),
         "stop with a mismatched session id is refused");
  expect(writer.is_active(), "a refused stop leaves the epoch active");

  // A stop that could not drain the queue must persist the loss, not hide it.
  expect(writer.stop(timeline, clock, "rec-A", 20, "req", 7, 3, false),
         "stop with the correct session id succeeds");
  const auto& stop = sink.control_events.back();
  expect(stop.kind == RecordingControlKind::kStop &&
             stop.discarded_task_events == 7 &&
             stop.discarded_reference_frames == 3 &&
             stop.discarded_counts_complete == false,
         "discarded counts are recorded literally and marked incomplete");
}

void test_abort_brackets_epoch_without_a_drained_stop() {
  FakeRecordSink sink;
  TaskRecorderHdf5Writer writer(sink);
  writer.open(1, "{}");
  writer.start("rec-X", 10, "req");
  expect(writer.abort("rec-X", 15, "req-abort", 4, 0, false),
         "abort closes an active epoch");
  const auto& abort = sink.control_events.back();
  expect(abort.kind == RecordingControlKind::kAbort &&
             abort.discarded_task_events == 4 &&
             !abort.discarded_counts_complete,
         "abort carries incomplete discarded accounting");
  expect(!writer.is_active() && writer.close(),
         "an aborted epoch can be closed");
}

void test_start_rejects_empty_session_id() {
  FakeRecordSink sink;
  TaskRecorderHdf5Writer writer(sink);
  writer.open(1, "{}");
  expect(!writer.start("", 10, "req"), "empty session id is refused");
  expect(sink.control_events.empty() && !writer.is_active(),
         "a refused start writes no control event");
}

void test_start_requires_open_and_sink_failures_propagate() {
  {
    FakeRecordSink sink;
    TaskRecorderHdf5Writer writer(sink);
    expect(!writer.start("rec", 1, "req"), "start before open is refused");
  }
  {
    FakeRecordSink sink;
    sink.open_ok = false;
    TaskRecorderHdf5Writer writer(sink);
    expect(!writer.open(1, "{}"), "a failed sink open propagates");
    expect(!writer.is_open(), "writer is not open when the sink open fails");
  }
  {
    FakeRecordSink sink;
    sink.records_ok = false;
    TaskRecorderHdf5Writer writer(sink);
    writer.open(1, "{}");
    writer.start("rec", 1, "req");
    TaskTimeline timeline;
    AffineClockEstimator clock;
    expect(!writer.flush(timeline, clock),
           "a sink write failure surfaces as a flush failure");
  }
}

}  // namespace

int main() {
  try {
    test_open_start_flush_stop_close_ordering();
    test_stop_requires_matching_session_and_records_loss();
    test_abort_brackets_epoch_without_a_drained_stop();
    test_start_rejects_empty_session_id();
    test_start_requires_open_and_sink_failures_propagate();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS task recorder writer\n";
  return 0;
}
