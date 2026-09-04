#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include "gui_control.pb.h"
#include "task_timeline.hpp"

namespace app::recording {

// Host-side bridge from the authoritative wire streams to the SDK/protobuf-
// independent TaskTimeline value boundary.  The recorder consumes two taps:
//
//   task_transition  protocol::TaskTransitionEvent  the committed event history
//   <reference>      BroadbandFrame                 the first-governed frame
//
// The adapter owns wire decoding and host-receipt stamping; TaskTimeline owns
// interval/diagnostic semantics and never sees a protobuf type.  The reference
// frame is taken as a small value observation rather than the SDK
// synapse::BroadbandFrame so the adapter and its test build without the closed
// SDK headers; the live call site passes frame.sequence_number()/
// frame.timestamp_ns() exactly as ModeSwitchApp::process_task_frame already
// reads them.

// The two authoritative fields of a reference BroadbandFrame the recorder needs
// to detect reference-stream continuity independently of task commits.
struct ReferenceFrameObservation {
  std::uint64_t sequence_number = 0;
  std::uint64_t timestamp_ns = 0;
};

enum class AdapterRejection {
  kNone,
  kMalformedWireEvent,     // failed protocol::validate_task_transition_event
  kUnknownEventKind,       // enum value outside the closed value-layer set
  kUnknownTriggerKind,     // trigger enum outside the closed wire set
  kMissingEffectiveFrame,  // committed event without its governing frame
  kTimelineRejected,       // value boundary refused it (see diagnostics())
};

struct AdapterResult {
  bool accepted = false;
  AdapterRejection rejection = AdapterRejection::kNone;
};

// Monotonic host clock in nanoseconds.  Production wiring passes the same
// steady clock the App uses (synapse::get_steady_clock_now); tests inject a
// deterministic counter.
using HostClockFn = std::uint64_t (*)();

class TaskTimelineAdapter {
 public:
  // reference_source_id is the configured task reference source (cfg_.task_
  // reference_source_id on the device); it labels reference-stream diagnostics
  // that carry no wire source_id of their own.
  TaskTimelineAdapter(TaskTimeline& timeline, std::string reference_source_id,
                      HostClockFn host_clock);

  // Decodes one task_transition tap message into a value record, stamps the
  // host receive time, and ingests it.  Returns why it was refused, if it was.
  AdapterResult on_task_transition(
      const stateful_decode_and_sync::v1::TaskTransitionEvent& wire);

  // Observes one reference BroadbandFrame.  A sequence regression or gap in the
  // reference stream marks the active task interval incomplete without inventing
  // a transition; a gap is reported once per discontinuity.
  void on_reference_frame(const ReferenceFrameObservation& frame);

  std::uint64_t accepted_events() const { return accepted_events_; }
  std::uint64_t rejected_events() const { return rejected_events_; }
  std::uint64_t reference_frames() const { return reference_frames_; }
  std::uint64_t reference_discontinuities() const {
    return reference_discontinuities_;
  }

 private:
  static std::optional<TaskEventKind> value_event_kind(
      stateful_decode_and_sync::v1::TaskEventKind kind);
  static std::optional<std::string> value_trigger_kind(
      stateful_decode_and_sync::v1::TaskTriggerKind kind);

  TaskTimeline& timeline_;
  std::string reference_source_id_;
  HostClockFn host_clock_;
  bool have_last_reference_ = false;
  std::uint64_t last_reference_sequence_ = 0;
  std::uint64_t last_reference_timestamp_ns_ = 0;
  std::uint64_t accepted_events_ = 0;
  std::uint64_t rejected_events_ = 0;
  std::uint64_t reference_frames_ = 0;
  std::uint64_t reference_discontinuities_ = 0;
};

}  // namespace app::recording
