#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "task_recorder_writer.hpp"

namespace app::recording {

// Concrete RecordSink backed by the raw C libhdf5 API.
//
// STATUS: interface declared, implementation pending.  The .cpp backend is a
// deliberate follow-up gated on the project selecting an HDF5 dependency
// mechanism (libhdf5 is currently absent from apps/stateful-decode-and-sync/
// vcpkg.json and from the WSL host-tests toolchain).  It is intentionally NOT
// part of the SDK-free host-tests build: that build exercises the writer's
// value logic through an in-memory FakeRecordSink, so no HDF5 dependency is
// pulled into unit testing.
//
// The backend carries none of the timeline/clock logic; it only serializes the
// fully-formed value structures TaskRecorderHdf5Writer hands it, mirroring the
// loss-aware layout the wireless HDF5 recorder already uses:
//
//   /                 attrs: schema_version, format, created_host_time_ns,
//                            metadata_json
//   /events           recording-control events (kind, session id, host time,
//                     request id, discarded counts, discarded_counts_complete)
//   /transitions      one row per TaskTransitionRecord (definition identity,
//                     app/run/event/transition sequences, event kind, state
//                     ids, canonical trigger, effective frame, host receive)
//   /intervals        half-open state intervals (state id, start/end frame,
//                     complete flag)
//   /diagnostics      timeline diagnostics (kind, observed, expected, detail)
//   /clock_epochs     ClockModel history (completed epochs + active model)
//   /                 attr: timeline_complete
//
// Every write is append/overwrite-and-flush so a crash mid-recording leaves a
// readable file whose control events bound whatever was persisted.
class Hdf5RecordSink : public RecordSink {
 public:
  explicit Hdf5RecordSink(std::string path);
  ~Hdf5RecordSink() override;

  Hdf5RecordSink(const Hdf5RecordSink&) = delete;
  Hdf5RecordSink& operator=(const Hdf5RecordSink&) = delete;

  bool open(const std::string& schema_version,
            std::uint64_t created_host_time_ns,
            const std::string& metadata_json) override;
  bool write_control_event(const RecordingControlEvent& event) override;
  bool write_transition_records(
      const std::vector<TaskTransitionRecord>& records) override;
  bool write_intervals(const std::vector<TaskInterval>& intervals) override;
  bool write_diagnostics(
      const std::vector<TimelineDiagnostic>& diagnostics) override;
  bool write_clock_epochs(
      const std::vector<wireless::ClockModel>& epochs) override;
  bool write_timeline_complete(bool complete) override;
  bool close() override;

 private:
  struct Impl;                   // hides the libhdf5 handles from consumers
  std::string path_;
  std::unique_ptr<Impl> impl_;
};

}  // namespace app::recording
