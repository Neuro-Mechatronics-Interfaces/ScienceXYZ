#pragma once

#include <array>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

#include "serial_port.hpp"
#include "usb_cdc_port.hpp"

namespace scifi2_hub::exo {

// Fixed set_finger_angles joint order (firmware >= 0.6.4). Index into
// JointPose::values. Mirrors kFingerOrder in the firmware's utils.cpp and the
// SDK's SET_FINGER_ANGLES_ORDER; the host proto ExoJoint enum uses the same
// order (offset by the UNSPECIFIED=0 sentinel).
enum class Joint : std::size_t {
  kThumb = 0,
  kIndex = 1,
  kMiddle = 2,
  kRing = 3,
  kPinky = 4,
  kWrist = 5,
};
inline constexpr std::size_t kJointCount = 6;

// Firmware that introduced the set_finger_angles batch command. Below this the
// worker refuses to arm, because the command is silent on older firmware and a
// pose write would look accepted while nothing moved.
inline constexpr std::array<int, 3> kMinFingerAnglesFirmware = {0, 6, 4};

// Inclusive magnitude of a signed joint value: -100 extend, 0 rest, +100 flex.
inline constexpr int kJointValueMax = 100;

// One commanded pose. A joint with has_value=false is held (an empty field in
// the batch command), matching the firmware's per-joint hold semantics.
struct JointPose {
  std::array<int, kJointCount> values{};
  std::array<bool, kJointCount> has_value{};

  void set(Joint joint, int value) {
    values[static_cast<std::size_t>(joint)] = value;
    has_value[static_cast<std::size_t>(joint)] = true;
  }
};

// Build the firmware "set_finger_angles:<t>:<i>:<m>:<r>:<p>:<w>" command from a
// pose. Unset joints become empty fields; trailing empty fields are dropped.
// Returns false (leaving `out` untouched) if no joint carries a value or a
// value is outside [-100, 100]. SDK-independent so the on-device App carries no
// nml_hand_exo dependency.
bool format_set_finger_angles(const JointPose& pose, std::string& out);

struct ExoLinkConfig {
  std::string transport = "usb_cdc";
  UsbCdcConfig usb;
  // Device path/name, supplied from App config as a plain parameter so it is
  // trivially changeable when the OpenRB-150 board is plugged in (see the
  // device-identification procedure in the app README/config docs).
  std::string device_path = "/dev/ttyACM0";
  unsigned int baud = 1000000;
  // Combined current budget in mA applied before enabling torque (0 leaves the
  // firmware default). Per-motor nominal current likewise (0 leaves default).
  int total_current_ma = 800;
  int per_motor_current_ma = 250;
  // Idle milliseconds after arming/the last pose before the watchdog sends
  // disable:all and requires explicit rearming. 0 disables the watchdog.
  int watchdog_ms = 1000;
  // Reply wait budget for a command that expects an acknowledgement.
  int reply_timeout_ms = 1500;
  std::string line_terminator = "\r\n";
  // After opening the CDC (DTR asserted), wait this long before the first write.
  // An OpenRB/SAMD CDC gates TX on DTR and needs a brief moment before it will
  // answer; writing immediately loses the first reply. 0 disables the settle.
  int open_settle_ms = 300;
  // The reply-route handshake is idempotent and has no motor side effect, so a
  // lost first reply (DTR race, or a stale startup banner still draining) is
  // retried up to this many total attempts before the connect fails. Each retry
  // drains any buffered bytes first. 1 disables retrying.
  int connect_handshake_attempts = 3;
};

// Immutable-ish status the worker publishes to the App on every change. Copied
// under the worker's lock by snapshot().
struct ExoStatus {
  bool link_open = false;
  bool armed = false;
  bool firmware_ok = false;
  std::string firmware;
  std::string last_error;
  JointPose last_commanded;
  bool watchdog_tripped = false;
  std::string last_reply;
  std::string transport;
};

// Owns the exo serial link on one dedicated thread. Every device interaction
// runs on that thread, drained from a job queue, so the App's main loop and tap
// callbacks never touch the port. The same thread runs the inactivity watchdog
// between jobs. Mirrors the host-side ExoWorker (Python) contract: connect ->
// arm(+home) -> set_pose, with a watchdog disarm (best effort, not a hardware safety interlock).
//
// The worker takes an already-built SerialPort so a test can inject a fake with
// firmware-shaped replies and no hardware is required.
class ExoLinkWorker {
 public:
  ExoLinkWorker(ExoLinkConfig config, std::unique_ptr<SerialPort> port);
  ~ExoLinkWorker();

  ExoLinkWorker(const ExoLinkWorker&) = delete;
  ExoLinkWorker& operator=(const ExoLinkWorker&) = delete;

  // Start the worker thread. Idempotent. Does not open the port.
  void start();
  // Disarm, close the port, and stop the thread. Safe to call more than once.
  void stop();

  // Blocking commands (enqueue a job, wait for completion). Each returns true
  // on success; on failure `error_out` (when non-null) receives the reason and
  // the published status carries it too. `timeout_ms` bounds the wait.
  bool connect(std::string* error_out = nullptr, int timeout_ms = 10000);
  bool disconnect(std::string* error_out = nullptr, int timeout_ms = 5000);
  bool arm(bool home, std::string* error_out = nullptr, int timeout_ms = 20000);
  bool disarm(std::string* error_out = nullptr, int timeout_ms = 5000);
  bool home(std::string* error_out = nullptr, int timeout_ms = 10000);
  bool set_pose(const JointPose& pose, std::string* error_out = nullptr,
                int timeout_ms = 5000);
  bool query(const std::string& command, std::string* error_out = nullptr, int timeout_ms = 5000);
  // Raw firmware-command passthrough (bench serial terminal). Unlike query(),
  // NO allowlist: `command` is sent verbatim and can command motion. The App
  // only calls this when exo_raw_enabled is configured. Requires an open link;
  // the firmware reply (or error) is returned and published in last_reply. A
  // command with an empty firmware terminator is sent fire-and-forget.
  bool raw(const std::string& command, std::string* reply_out = nullptr,
           std::string* error_out = nullptr, int timeout_ms = 5000);

  ExoStatus snapshot() const;

 private:
  struct Job {
    std::function<bool(std::string&)> fn;
    bool done = false;
    bool ok = false;
    std::string error;
    bool cancelled = false;
  };

  void run();
  bool submit(std::function<bool(std::string&)> fn, std::string* error_out, int timeout_ms);
  void service_idle();

  // Device operations, worker-thread only. Each returns false and fills `error`
  // on failure.
  bool do_connect(std::string& error);
  bool do_disconnect(std::string& error);
  bool do_arm(bool home, std::string& error);
  bool do_disarm(std::string& error);
  bool do_home(std::string& error);
  bool do_set_pose(const JointPose& pose, std::string& error);

  // Send a framed command and (optionally) confirm the firmware's ack contains
  // `expect_substr`. `expect_substr` empty means fire-and-forget (silent in
  // firmware, e.g. enable/disable). Returns false on a link write failure or a
  // missing/negative ack when one was required.
  bool send_command(const std::string& command, const std::string& expect_substr,
                    std::string& error);
  // Read frames until one containing `needle` arrives or the timeout elapses.
  std::optional<std::string> read_until(const std::string& needle, int timeout_ms);
  // Read and return the first complete ';'-terminated frame (any content), or
  // nullopt on timeout. Used by raw() where the reply text is not known ahead.
  std::optional<std::string> read_until_any(int timeout_ms);
  // Drain and return whatever bytes are already buffered on the link within
  // `timeout_ms` (best effort). Used before a connect handshake to clear a stale
  // startup banner and to record what the board emitted for diagnostics.
  std::string drain_rx(int timeout_ms);
  // Run the reply_route:both handshake on the currently-open port: settle after
  // open, then up to connect_handshake_attempts of drain-then-send. Returns true
  // once the ACK is seen; on false, `error` describes the last attempt (with the
  // bytes seen) and the port has been closed. Reopens the same interface between
  // in-interface attempts; the caller advances to a different CDC candidate.
  bool handshake_reply_route(std::string& error);
  bool query_firmware(std::string& firmware, bool& firmware_ok, std::string& error);

  void set_status(const std::function<void(ExoStatus&)>& mutate);

  ExoLinkConfig config_;
  std::unique_ptr<SerialPort> port_;

  mutable std::mutex status_mutex_;
  ExoStatus status_;

  std::mutex jobs_mutex_;
  std::condition_variable jobs_cv_;
  std::deque<std::shared_ptr<Job>> jobs_;
  bool stopping_ = false;
  std::thread thread_;

  // Worker-thread-only inactivity tracking.
  std::optional<std::chrono::steady_clock::time_point> last_command_time_;
  std::string read_buffer_;  // partial-frame accumulator for read_until
};

}  // namespace scifi2_hub::exo
