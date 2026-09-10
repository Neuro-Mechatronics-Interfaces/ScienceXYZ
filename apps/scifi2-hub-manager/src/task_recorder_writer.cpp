#include "task_recorder_writer.hpp"

#include <vector>

namespace scifi2_hub::recording {

TaskRecorderHdf5Writer::TaskRecorderHdf5Writer(RecordSink& sink)
    : sink_(sink) {}

bool TaskRecorderHdf5Writer::open(std::uint64_t created_host_time_ns,
                                  const std::string& metadata_json) {
  if (opened_ || closed_) {
    return false;
  }
  if (!sink_.open(kTaskTimelineSchema, created_host_time_ns, metadata_json)) {
    return false;
  }
  opened_ = true;
  return true;
}

bool TaskRecorderHdf5Writer::start(const std::string& recording_session_id,
                                   std::uint64_t host_time_ns,
                                   const std::string& request_id) {
  if (!opened_ || active_ || closed_) {
    return false;
  }
  if (recording_session_id.empty()) {
    return false;
  }
  RecordingControlEvent event;
  event.kind = RecordingControlKind::kStart;
  event.recording_session_id = recording_session_id;
  event.host_time_ns = host_time_ns;
  event.request_id = request_id;
  if (!sink_.write_control_event(event)) {
    return false;
  }
  active_ = true;
  active_session_id_ = recording_session_id;
  return true;
}

bool TaskRecorderHdf5Writer::flush_snapshot(const TaskTimeline& timeline,
                                            const wireless::AffineClockEstimator& clock) {
  // Overwrite the persisted timeline snapshot with the current authoritative
  // contents.  Each collection is emitted whole so a mid-recording flush and a
  // final stop flush leave identical, self-consistent state.
  if (!sink_.write_transition_records(timeline.records())) {
    return false;
  }
  if (!sink_.write_intervals(timeline.intervals())) {
    return false;
  }
  if (!sink_.write_diagnostics(timeline.diagnostics())) {
    return false;
  }
  // Persist the full clock history: the completed epochs plus the still-active
  // current model when it is locked. The estimator exposes the active epoch
  // only through current_model() (it is not in epochs() until a new epoch
  // begins), so persisting epochs() alone would silently drop the clock model
  // that was actually mapping auxiliary samples at recording time. Each model
  // carries a distinct model_id, so appending the current one never duplicates
  // a completed epoch.
  std::vector<wireless::ClockModel> clock_history = clock.epochs();
  if (clock.current_model().locked) {
    clock_history.push_back(clock.current_model());
  }
  if (!sink_.write_clock_epochs(clock_history)) {
    return false;
  }
  if (!sink_.write_timeline_complete(timeline.complete())) {
    return false;
  }
  return true;
}

bool TaskRecorderHdf5Writer::flush(const TaskTimeline& timeline,
                                   const wireless::AffineClockEstimator& clock) {
  if (!active_) {
    return false;
  }
  return flush_snapshot(timeline, clock);
}

bool TaskRecorderHdf5Writer::stop(const TaskTimeline& timeline,
                                  const wireless::AffineClockEstimator& clock,
                                  const std::string& recording_session_id,
                                  std::uint64_t host_time_ns,
                                  const std::string& request_id,
                                  std::uint64_t discarded_task_events,
                                  std::uint64_t discarded_reference_frames,
                                  bool discarded_counts_complete) {
  if (!active_) {
    return false;
  }
  if (recording_session_id != active_session_id_) {
    return false;
  }
  if (!flush_snapshot(timeline, clock)) {
    return false;
  }
  RecordingControlEvent event;
  event.kind = RecordingControlKind::kStop;
  event.recording_session_id = recording_session_id;
  event.host_time_ns = host_time_ns;
  event.request_id = request_id;
  event.discarded_task_events = discarded_task_events;
  event.discarded_reference_frames = discarded_reference_frames;
  event.discarded_counts_complete = discarded_counts_complete;
  if (!sink_.write_control_event(event)) {
    return false;
  }
  active_ = false;
  active_session_id_.clear();
  return true;
}

bool TaskRecorderHdf5Writer::abort(const std::string& recording_session_id,
                                   std::uint64_t host_time_ns,
                                   const std::string& request_id,
                                   std::uint64_t discarded_task_events,
                                   std::uint64_t discarded_reference_frames,
                                   bool discarded_counts_complete) {
  if (!active_) {
    return false;
  }
  if (recording_session_id != active_session_id_) {
    return false;
  }
  RecordingControlEvent event;
  event.kind = RecordingControlKind::kAbort;
  event.recording_session_id = recording_session_id;
  event.host_time_ns = host_time_ns;
  event.request_id = request_id;
  event.discarded_task_events = discarded_task_events;
  event.discarded_reference_frames = discarded_reference_frames;
  event.discarded_counts_complete = discarded_counts_complete;
  if (!sink_.write_control_event(event)) {
    return false;
  }
  active_ = false;
  active_session_id_.clear();
  return true;
}

bool TaskRecorderHdf5Writer::close() {
  if (!opened_ || closed_) {
    return false;
  }
  // An active epoch must be explicitly stopped or aborted first so its
  // discarded accounting is never silently lost.
  if (active_) {
    return false;
  }
  if (!sink_.close()) {
    return false;
  }
  closed_ = true;
  return true;
}

}  // namespace scifi2_hub::recording
