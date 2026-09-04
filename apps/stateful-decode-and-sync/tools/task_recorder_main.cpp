// Host-side task-timeline recorder consumer.
//
// STATUS: reviewed, not yet built. This program links vendor/synapse-cpp (the
// Synapse Tap client) and the concrete libhdf5 RecordSink backend, neither of
// which is part of the SDK-free host-tests build. It is committed as the
// consumer half of T-32/H-53 so the design and wire handling are reviewable;
// its CMake target (tools/CMakeLists.txt) and the Hdf5RecordSink .cpp are
// enabled once the project selects an HDF5 dependency mechanism.
//
// Architecture (AGENTS.md, host fusion): the device App is the task_transition
// PRODUCER; this is a separate host CONSUMER. It reads two producer taps --
//
//   task_transition   stateful_decode_and_sync::v1::TaskTransitionEvent
//   broadband_out     synapse::BroadbandFrame   (the task reference stream)
//
// -- and drives TaskTimelineAdapter, which validates each event against the
// producer-side contract, stamps a host receive time, and marks reference-
// stream discontinuities. TaskRecorderHdf5Writer persists the authoritative
// records/intervals/diagnostics and the auxiliary clock-model history to HDF5.
//
// The recorder never overwrites a source timestamp, never resamples or
// interpolates, and records every message it could not persist as an explicit
// discarded count on the stop control event (AGENTS.md data integrity).

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "science/synapse/tap.h"

#include "api/datatype.pb.h"      // synapse::BroadbandFrame
#include "gui_control.pb.h"       // stateful_decode_and_sync::v1::TaskTransitionEvent

#include "clock_estimator.hpp"
#include "hdf5_record_sink.hpp"
#include "task_recorder_writer.hpp"
#include "task_timeline.hpp"
#include "task_timeline_adapter.hpp"

namespace {

std::atomic<bool> g_stop_requested{false};

void handle_signal(int /*signum*/) { g_stop_requested.store(true); }

// Host receive clock for the adapter. The consumer links synapse-cpp, not the
// App SDK, so it uses the host steady clock directly rather than the device's
// synapse::get_steady_clock_now. Both are monotonic nanosecond sources.
std::uint64_t host_steady_now_ns() {
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

struct Options {
  std::string device_uri;
  std::string task_tap = "task_transition";
  std::string reference_tap = "broadband_out";
  std::string reference_source_id = "rhd2132";
  std::string output_path;
  std::string metadata_json = "{}";
  std::string session_id = "task-recording";
  int read_timeout_ms = 100;
};

void print_usage(const char* argv0) {
  std::cerr
      << "Usage: " << argv0 << " --device <uri> --output <file.h5>\n"
      << "  --device <uri>              Synapse device URI (e.g. 192.168.100.157:647)\n"
      << "  --output <file.h5>          HDF5 output path\n"
      << "  --task-tap <name>           task_transition tap name (default: task_transition)\n"
      << "  --reference-tap <name>      reference BroadbandFrame tap (default: broadband_out)\n"
      << "  --reference-source-id <id>  reference source id label (default: rhd2132)\n"
      << "  --metadata-json <json>      opaque provenance stored verbatim (default: {})\n"
      << "  --session-id <id>           recording session id (default: task-recording)\n"
      << "  --read-timeout-ms <n>       per-poll tap read timeout (default: 100)\n";
}

bool parse_args(int argc, char** argv, Options& out) {
  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    auto next = [&](std::string& dst) -> bool {
      if (i + 1 >= argc) {
        std::cerr << "missing value for " << arg << "\n";
        return false;
      }
      dst = argv[++i];
      return true;
    };
    if (arg == "--device") {
      if (!next(out.device_uri)) return false;
    } else if (arg == "--output") {
      if (!next(out.output_path)) return false;
    } else if (arg == "--task-tap") {
      if (!next(out.task_tap)) return false;
    } else if (arg == "--reference-tap") {
      if (!next(out.reference_tap)) return false;
    } else if (arg == "--reference-source-id") {
      if (!next(out.reference_source_id)) return false;
    } else if (arg == "--metadata-json") {
      if (!next(out.metadata_json)) return false;
    } else if (arg == "--session-id") {
      if (!next(out.session_id)) return false;
    } else if (arg == "--read-timeout-ms") {
      std::string value;
      if (!next(value)) return false;
      out.read_timeout_ms = std::atoi(value.c_str());
    } else if (arg == "-h" || arg == "--help") {
      return false;
    } else {
      std::cerr << "unknown argument: " << arg << "\n";
      return false;
    }
  }
  if (out.device_uri.empty() || out.output_path.empty()) {
    std::cerr << "--device and --output are required\n";
    return false;
  }
  return true;
}

// Connect one producer tap by name. Returns false with a message on failure so
// the operator sees exactly which tap is unavailable rather than a silent stall.
bool connect_tap(synapse::Tap& tap, const std::string& name) {
  const auto status = tap.connect(name);
  if (!status.ok()) {
    std::cerr << "failed to connect tap '" << name << "': " << status.message()
              << "\n";
    return false;
  }
  return true;
}

}  // namespace

int main(int argc, char** argv) {
  Options options;
  if (!parse_args(argc, argv, options)) {
    print_usage(argv[0]);
    return 2;
  }

  std::signal(SIGINT, handle_signal);
  std::signal(SIGTERM, handle_signal);

  // Two independent producer-tap connections. A single Tap connects to one tap
  // at a time, so the two authoritative streams get their own client.
  synapse::Tap task_tap(options.device_uri);
  synapse::Tap reference_tap(options.device_uri);
  if (!connect_tap(task_tap, options.task_tap) ||
      !connect_tap(reference_tap, options.reference_tap)) {
    return 1;
  }

  app::recording::TaskTimeline timeline;
  app::wireless::AffineClockEstimator clock;  // auxiliary-source clock history
  app::recording::TaskTimelineAdapter adapter(timeline, options.reference_source_id,
                                              &host_steady_now_ns);

  app::recording::Hdf5RecordSink sink(options.output_path);
  app::recording::TaskRecorderHdf5Writer writer(sink);

  if (!writer.open(host_steady_now_ns(), options.metadata_json)) {
    std::cerr << "failed to open recording file: " << options.output_path << "\n";
    return 1;
  }
  if (!writer.start(options.session_id, host_steady_now_ns(),
                    /*request_id=*/"cli")) {
    std::cerr << "failed to start recording epoch\n";
    writer.close();
    return 1;
  }
  std::cerr << "recording task timeline to " << options.output_path
            << " (Ctrl-C to stop)\n";

  // Loss accounting: messages the consumer read but could not persist as a
  // value record are counted and reported on stop, never hidden. A decode
  // failure means a wire message did not parse; an adapter rejection means the
  // event failed the producer-side contract (both are explicit losses).
  std::uint64_t discarded_task_events = 0;
  std::uint64_t discarded_reference_frames = 0;

  std::vector<std::uint8_t> buffer;
  std::uint64_t flush_counter = 0;
  constexpr std::uint64_t kFlushEveryMessages = 256;
  // Bound each per-iteration drain so a sustained high-rate stream (the
  // reference broadband tap) cannot starve the other tap; the outer loop then
  // re-polls both and re-checks the stop flag.
  constexpr int kMaxDrainPerIteration = 1024;

  while (!g_stop_requested.load()) {
    bool did_work = false;

    // Drain the task_transition tap. Each message is one committed event.
    for (int drained = 0; drained < kMaxDrainPerIteration; ++drained) {
      const auto status = task_tap.read(&buffer, options.read_timeout_ms);
      if (!status.ok()) break;  // timeout or transient: fall through to reference
      did_work = true;
      stateful_decode_and_sync::v1::TaskTransitionEvent wire;
      if (!wire.ParseFromArray(buffer.data(),
                               static_cast<int>(buffer.size()))) {
        ++discarded_task_events;
        continue;
      }
      const auto result = adapter.on_task_transition(wire);
      if (!result.accepted) {
        ++discarded_task_events;
      }
      if (++flush_counter % kFlushEveryMessages == 0) {
        writer.flush(timeline, clock);
      }
    }

    // Drain the reference BroadbandFrame tap. Only the two continuity fields
    // are needed; the adapter marks reference-stream gaps/regressions.
    for (int drained = 0; drained < kMaxDrainPerIteration; ++drained) {
      const auto status = reference_tap.read(&buffer, options.read_timeout_ms);
      if (!status.ok()) break;
      did_work = true;
      synapse::BroadbandFrame frame;
      if (!frame.ParseFromArray(buffer.data(),
                                static_cast<int>(buffer.size()))) {
        ++discarded_reference_frames;
        continue;
      }
      adapter.on_reference_frame(
          {frame.sequence_number(), frame.timestamp_ns()});
    }

    if (!did_work) {
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  }

  std::cerr << "stopping: " << adapter.accepted_events() << " events, "
            << adapter.reference_frames() << " reference frames, "
            << adapter.reference_discontinuities() << " discontinuities\n";

  // The counts above are exact for messages we read; a queue we could not drain
  // at shutdown is unknown, so discarded_counts_complete=false keeps an analysis
  // from treating unread backlog as zero loss.
  const bool discarded_counts_complete = false;
  if (!writer.stop(timeline, clock, options.session_id, host_steady_now_ns(),
                   /*request_id=*/"cli", discarded_task_events,
                   discarded_reference_frames, discarded_counts_complete)) {
    std::cerr << "failed to stop recording epoch cleanly\n";
    writer.abort(options.session_id, host_steady_now_ns(), "cli",
                 discarded_task_events, discarded_reference_frames,
                 /*discarded_counts_complete=*/false);
  }
  if (!writer.close()) {
    std::cerr << "failed to close recording file cleanly\n";
    return 1;
  }
  return 0;
}
