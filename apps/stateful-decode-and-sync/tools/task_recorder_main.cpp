#include <atomic>
#include <limits>
#include <sstream>
#include <fstream>
#include <condition_variable>
#include <mutex>
#include <google/protobuf/struct.pb.h>
#include <google/protobuf/util/json_util.h>
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

#ifndef SCIENCEXYZ_REVISION
#define SCIENCEXYZ_REVISION "unknown"
#endif

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
  std::string metadata_json;
  std::string metadata_file;
  std::string session_id = "task-recording";
  int idle_timeout_ms = 5000;
};

void print_usage(const char* argv0) {
  std::cerr
      << "Usage: " << argv0 << " --device <uri> --output <file.h5>\n"
      << "  --device <uri>              Synapse device URI (e.g. 192.168.100.157:647)\n"
      << "  --output <file.h5>          HDF5 output path\n"
      << "  --task-tap <name>           task_transition tap name (default: task_transition)\n"
      << "  --reference-tap <name>      reference BroadbandFrame tap (default: broadband_out)\n"
      << "  --reference-source-id <id>  reference source id label (default: rhd2132)\n"
      << "  --metadata-file <path>      required operator device/config provenance JSON\n"
      << "  --metadata-json <json>      alternative inline provenance JSON\n"
      << "  --session-id <id>           recording session id (default: task-recording)\n"
      << "  --idle-timeout-ms <n>       abort after broadband silence (default: 5000)\n";
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
    } else if (arg == "--metadata-file") {
      if (!next(out.metadata_file)) return false;
    } else if (arg == "--session-id") {
      if (!next(out.session_id)) return false;
    } else if (arg == "--idle-timeout-ms") {
      std::string value;
      if (!next(value)) return false;
            try {
        std::size_t end = 0;
        out.idle_timeout_ms = std::stoi(value, &end);
        if (end != value.size() || out.idle_timeout_ms <= 0) return false;
      } catch (...) { return false; }
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
  if (!out.metadata_file.empty()) {
    if (!out.metadata_json.empty()) {
      std::cerr << "choose --metadata-file or --metadata-json\n";
      return false;
    }
    std::ifstream input(out.metadata_file, std::ios::binary);
    if (!input) { std::cerr << "cannot read metadata file\n"; return false; }
    std::ostringstream contents;
    contents << input.rdbuf();
    if (input.bad()) { std::cerr << "metadata read failed\n"; return false; }
    out.metadata_json = contents.str();
  }
  google::protobuf::Struct metadata;
  if (out.metadata_json.empty() ||
      !google::protobuf::util::JsonStringToMessage(out.metadata_json, &metadata).ok() ||
      metadata.fields().empty()) {
    std::cerr << "supply a nonempty provenance JSON object using --metadata-file or --metadata-json\n";
    return false;
  }
  if (out.session_id.empty() || out.reference_source_id.empty()) return false;
  return true;
}

// Connect one producer tap by name. Returns false with a message on failure so
// the operator sees exactly which tap is unavailable rather than a silent stall.
bool connect_tap(synapse::Tap& tap, const std::string& name) {
  // Upstream Tap::connect issues a query without a deadline. Bound this startup
  // phase externally; no recording file has been created yet. A stuck upstream
  // call cannot be safely cancelled/detached while it owns the Tap object.
  std::mutex mutex;
  std::condition_variable changed;
  bool finished = false;
  std::thread watchdog([&] {
    std::unique_lock lock(mutex);
    if (!changed.wait_for(lock, std::chrono::seconds(10), [&] { return finished; })) {
      std::cerr << "tap connection timed out: " << name << std::endl;
      std::_Exit(1);
    }
  });
  science::Status status;
  try { status = tap.connect(name); }
  catch (const std::exception& error) {
    status = science::Status(science::StatusCode::kInternal, error.what());
  }
  {
    std::lock_guard lock(mutex);
    finished = true;
  }
  changed.notify_one();
  watchdog.join();
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
  if (!sink.write_provenance("software_revision", SCIENCEXYZ_REVISION) ||
      !sink.write_provenance("device_uri", options.device_uri) ||
      !sink.write_provenance("reference_source_id", options.reference_source_id) ||
      !sink.write_provenance("reference_tap", options.reference_tap) ||
      !sink.write_provenance("task_tap", options.task_tap) ||
      !sink.write_provenance("clock_mapping", "unmeasured; source and host clocks are not interchangeable")) {
    std::cerr << "failed to persist provenance\n";
    if (!writer.close()) std::cerr << "failed to close recording\n";
    return 1;
  }
  if (!writer.start(options.session_id, host_steady_now_ns(),
                    /*request_id=*/"cli")) {
    std::cerr << "failed to start recording epoch\n";
    if (!writer.close()) std::cerr << "failed to close recording\n";
    return 1;
  }
  std::cerr << "recording raw broadband and task messages to " << options.output_path
            << " (Ctrl-C to stop)\n";

  std::uint64_t discarded_task_events = 0, discarded_reference_frames = 0;
  std::uint64_t task_parse_errors = 0, reference_parse_errors = 0;
  std::uint64_t transport_errors = 0, write_errors = 0;
  std::uint64_t missing_sequences = 0, sequence_regressions = 0;
  std::uint64_t raw_task_count = 0, raw_reference_count = 0;
  std::uint64_t last_sequence = 0;
  bool have_sequence = false, failed = false;
  auto last_reference = std::chrono::steady_clock::now();
  auto last_flush = last_reference;
  constexpr int kBatchSize = 256;
  std::vector<std::uint8_t> buffer;
  std::string failure_reason;

  // Alternate bounded nonblocking drains; an idle task stream cannot block raw data.
  while (!g_stop_requested.load() && !failed) {
    bool did_work = false;
    for (bool reference : {false, true}) {
      auto& tap = reference ? reference_tap : task_tap;
      std::vector<app::recording::RawTapMessage> batch;
      for (int n = 0; n < kBatchSize && !g_stop_requested.load(); ++n) {
        const auto status = tap.read(&buffer, 0);
        const auto receipt = host_steady_now_ns();
        if (!status.ok()) {
          if (status.code() != science::StatusCode::kDeadlineExceeded &&
              !g_stop_requested.load()) {
            ++transport_errors;
            failed = true;
            failure_reason = reference ? "reference_transport_error" : "task_transport_error";
            std::cerr << failure_reason << ": " << status.message() << "\n";
          }
          break;
        }
        did_work = true;
        batch.push_back({receipt, buffer});
        const bool parseable_size = buffer.size() <= static_cast<std::size_t>(std::numeric_limits<int>::max());
        if (reference) {
          last_reference = std::chrono::steady_clock::now();
          synapse::BroadbandFrame frame;
          if (!parseable_size || !frame.ParseFromArray(buffer.data(), static_cast<int>(buffer.size()))) {
            ++reference_parse_errors;
            timeline.mark_reference_discontinuity("unparseable broadband wire message");
            continue;
          }
          if (have_sequence) {
            if (frame.sequence_number() <= last_sequence) ++sequence_regressions;
            else missing_sequences += frame.sequence_number() - last_sequence - 1;
          }
          have_sequence = true;
          last_sequence = frame.sequence_number();
          adapter.on_reference_frame({frame.sequence_number(), frame.timestamp_ns()});
        } else {
          stateful_decode_and_sync::v1::TaskTransitionEvent event;
          if (!parseable_size || !event.ParseFromArray(buffer.data(), static_cast<int>(buffer.size()))) {
            ++task_parse_errors;
            timeline.mark_reference_discontinuity("unparseable task wire message");
            continue;
          }
          if (!adapter.on_task_transition(event).accepted)
            timeline.mark_reference_discontinuity("rejected task event; raw wire retained");
        }
      }
      if (!batch.empty()) {
        if (!sink.write_raw_messages(reference, batch)) {
          ++write_errors;
          (reference ? discarded_reference_frames : discarded_task_events) += batch.size();
          failed = true;
          failure_reason = "raw_write_failed";
        } else {
          (reference ? raw_reference_count : raw_task_count) += batch.size();
        }
      }
      if (failed) break;
    }
    const auto now = std::chrono::steady_clock::now();
    if (now - last_reference > std::chrono::milliseconds(options.idle_timeout_ms)) {
      failed = true;
      failure_reason = "reference_idle_timeout";
    }
    if (!failed && now - last_flush >= std::chrono::seconds(1)) {
      if (!writer.flush(timeline, clock)) {
        ++write_errors;
        failed = true;
        failure_reason = "timeline_write_failed";
      }
      last_flush = now;
    }
    if (!did_work && !failed) std::this_thread::sleep_for(std::chrono::milliseconds(1));
  }

  // PUB/SUB provides neither an end barrier nor trustworthy queue-drop counters.
  std::ostringstream status;
  status << "{\"state\":\"" << (failed ? "failed" : "stopped")
         << "\",\"reason\":\"" << failure_reason
         << "\",\"queue_loss_known\":false,\"head_complete\":false,\"tail_complete\":false"
         << ",\"raw_reference_messages\":" << raw_reference_count
         << ",\"raw_task_messages\":" << raw_task_count
         << ",\"reference_parse_errors\":" << reference_parse_errors
         << ",\"task_parse_errors\":" << task_parse_errors
         << ",\"rejected_task_events\":" << adapter.rejected_events()
         << ",\"missing_reference_sequences\":" << missing_sequences
         << ",\"reference_sequence_regressions\":" << sequence_regressions
         << ",\"reference_discontinuities\":" << adapter.reference_discontinuities()
         << ",\"transport_errors\":" << transport_errors
         << ",\"write_errors\":" << write_errors << "}";
  auto pending_status = status.str();
  const auto stopped_at = pending_status.find("\"stopped\"");
  if (stopped_at != std::string::npos) pending_status.replace(stopped_at, 9, "\"finalizing\"");
  if (!sink.write_recording_status(pending_status)) {
    failed = true;
    std::cerr << "failed to persist recording status; file remains incomplete\n";
  }
  if (!failed && !writer.stop(timeline, clock, options.session_id,
        host_steady_now_ns(), "cli", discarded_task_events,
        discarded_reference_frames, false)) {
    failed = true;
    std::cerr << "failed to persist stop\n";
  }
  if (!failed && !sink.write_recording_status(status.str())) {
    failed = true;
    std::cerr << "failed to persist final status\n";
  }
  if (failed) {
    if (writer.is_active() && !writer.abort(options.session_id, host_steady_now_ns(), "cli",
          discarded_task_events, discarded_reference_frames, false))
      std::cerr << "failed to persist abort\n";
  }
  if (!writer.close()) {
    failed = true;
    std::cerr << "failed to close recording\n";
  }
  std::cerr << status.str() << "\n";
  if (!failed) std::cerr << "recording stopped and flushed; unread tail/queue loss unknown\n";
  return failed ? 1 : 0;
}
