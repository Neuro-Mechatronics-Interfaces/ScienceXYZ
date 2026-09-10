#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "task_recorder_writer.hpp"

namespace scifi2_hub::recording {

// Wire bytes are authoritative, including unknown protobuf fields and malformed
// messages. Receipt time is host steady-clock time, never a source timestamp.
struct RawTapMessage {
  std::uint64_t host_receive_time_ns = 0;
  std::vector<std::uint8_t> payload;
};

// Concrete RecordSink backed by the raw C libhdf5 API.
//
// Built by host/recording/CMakeLists.txt using libhdf5. The SDK-free host-tests
// build still exercises the writer with FakeRecordSink without HDF5.
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
// Raw /raw_broadband and /raw_task rows preserve wire bytes and host receipt.
// Successful writes flush HDF5 buffers, without promising power-loss recovery.
// Local stop/close does not establish PUB/SUB queue or tail completeness.
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
  bool write_raw_messages(bool reference_stream,
                          const std::vector<RawTapMessage>& messages);
  bool write_recording_status(const std::string& status_json);
  bool write_provenance(const std::string& key, const std::string& value);

 private:
  struct Impl;                   // hides the libhdf5 handles from consumers
  std::string path_;
  std::unique_ptr<Impl> impl_;
};

}  // namespace scifi2_hub::recording
