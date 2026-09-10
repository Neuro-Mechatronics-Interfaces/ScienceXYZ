#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "clock_estimator.hpp"
#include "task_timeline.hpp"

namespace scifi2_hub::recording {

// Host task-timeline recording boundary.
//
// The device App is the task_transition PRODUCER; this writer belongs to the
// separate host CONSUMER (see tools/task_recorder_main.cpp).  It persists the
// authoritative, SDK/protobuf-free value types the TaskTimeline and the
// AffineClockEstimator already expose -- TaskTransitionRecord, TaskInterval,
// TimelineDiagnostic, and ClockModel epochs -- without changing their meaning.
//
// Serialization is deliberately kept behind a thin RecordSink seam.  The value
// logic (epoch control, loss-aware discarded-sample accounting, the mapping
// from timeline/clock structures onto the persisted layout) is exercised in the
// SDK- and HDF5-free host-tests build against an in-memory FakeRecordSink.  The
// concrete raw-C-libhdf5 backend is a separate compilation unit added once the
// project selects an HDF5 dependency mechanism; it implements only this
// interface and carries none of the value logic.
//
// The persisted layout mirrors the loss-aware conventions already used by the
// wireless HDF5 recorder: a schema-versioned file, an append-only recording
// epoch bracketed by acknowledged start/stop control events, and explicit
// discarded-sample counts that are recorded rather than inferred or hidden.

inline constexpr char kTaskTimelineSchema[] = "sciencexyz.host_task_timeline.v1";

// One acknowledged recording-control transition.  Start and stop each emit one;
// stop additionally carries the drained/discarded accounting.
enum class RecordingControlKind {
  kStart,
  kStop,
  kAbort,  // context torn down without an explicit, drained stop
};

struct RecordingControlEvent {
  RecordingControlKind kind = RecordingControlKind::kStart;
  std::string recording_session_id;
  std::uint64_t host_time_ns = 0;
  std::string request_id;
  // Stop/abort only.  discarded_events/frames are messages the consumer
  // dropped without persisting (queue not drained, tap torn down, etc.).
  // discarded_counts_complete is false when the consumer cannot prove the
  // count is exact, so an analysis never treats an unknown loss as zero.
  std::uint64_t discarded_task_events = 0;
  std::uint64_t discarded_reference_frames = 0;
  bool discarded_counts_complete = true;
};

// The persistence seam.  A backend receives fully-formed value structures and
// only serializes them; it makes no timeline or clock decisions of its own.
// Every method reports success/failure so a partial-write backend surfaces I/O
// loss instead of silently dropping records.
class RecordSink {
 public:
  virtual ~RecordSink() = default;

  // Establishes the file/schema header exactly once, before any epoch opens.
  // metadata_json is opaque provenance (software/firmware ids, config hash,
  // peripheral ids) captured verbatim by the caller.
  virtual bool open(const std::string& schema_version,
                    std::uint64_t created_host_time_ns,
                    const std::string& metadata_json) = 0;

  virtual bool write_control_event(const RecordingControlEvent& event) = 0;

  // The authoritative task history, persisted as raw records so an analysis can
  // distinguish known state from loss.
  virtual bool write_transition_records(
      const std::vector<TaskTransitionRecord>& records) = 0;
  virtual bool write_intervals(const std::vector<TaskInterval>& intervals) = 0;
  virtual bool write_diagnostics(
      const std::vector<TimelineDiagnostic>& diagnostics) = 0;

  // Clock-model history from the auxiliary-source AffineClockEstimator, retained
  // beside -- never over -- the source timestamps so mappings are reproducible.
  // The writer supplies the completed epochs plus the active (locked) current
  // model, so the model that was mapping samples at recording time is persisted;
  // each carries a distinct model_id.
  virtual bool write_clock_epochs(const std::vector<wireless::ClockModel>& epochs) = 0;

  // Marks the timeline's own completeness flag (an event-sequence or reference
  // discontinuity anywhere in the recording forces this false).
  virtual bool write_timeline_complete(bool complete) = 0;

  virtual bool close() = 0;
};

// Drives a RecordSink from the live TaskTimeline and AffineClockEstimator.
//
// Lifecycle: open() once, start() an epoch, then flush() as often as desired
// (each flush overwrites the persisted timeline snapshot with the current
// authoritative contents), stop() to bracket the epoch with drained/discarded
// counts, close() once.  The writer never resamples, interpolates, or drops
// records; discarded counts are supplied by the caller and recorded literally.
class TaskRecorderHdf5Writer {
 public:
  explicit TaskRecorderHdf5Writer(RecordSink& sink);

  bool open(std::uint64_t created_host_time_ns,
            const std::string& metadata_json);

  // Acknowledge and begin a recording epoch.  Rejects an empty session id or a
  // second concurrent start.
  bool start(const std::string& recording_session_id,
             std::uint64_t host_time_ns, const std::string& request_id);

  // Persist the current authoritative timeline + clock-epoch snapshot.  Safe to
  // call repeatedly; each call re-emits the full snapshot.
  bool flush(const TaskTimeline& timeline,
             const wireless::AffineClockEstimator& clock);

  // Persist a final snapshot and an acknowledged stop with the drained/
  // discarded accounting.  discarded_counts_complete=false when the count is a
  // lower bound rather than exact.
  bool stop(const TaskTimeline& timeline, const wireless::AffineClockEstimator& clock,
            const std::string& recording_session_id,
            std::uint64_t host_time_ns, const std::string& request_id,
            std::uint64_t discarded_task_events,
            std::uint64_t discarded_reference_frames,
            bool discarded_counts_complete);

  // Record an abort control event (torn down without a drained stop).  Leaves
  // discarded counts explicitly incomplete unless the caller proved them.
  bool abort(const std::string& recording_session_id,
             std::uint64_t host_time_ns, const std::string& request_id,
             std::uint64_t discarded_task_events,
             std::uint64_t discarded_reference_frames,
             bool discarded_counts_complete);

  bool close();

  bool is_open() const { return opened_; }
  bool is_active() const { return active_; }

 private:
  bool flush_snapshot(const TaskTimeline& timeline,
                      const wireless::AffineClockEstimator& clock);

  RecordSink& sink_;
  bool opened_ = false;
  bool active_ = false;
  bool closed_ = false;
  std::string active_session_id_;
};

}  // namespace scifi2_hub::recording
