#include "exo_link.hpp"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <sstream>
#include <thread>
#include <utility>

namespace scifi2_hub::exo {

namespace {

using Clock = std::chrono::steady_clock;

// Parse a firmware version string ("0.6.4", "Exo Device Version: 0.6.4;", ...)
// into a comparable {major, minor, patch}. Missing components are 0; an
// unparseable string yields {0,0,0}, which compares below every real version so
// a feature gate fails closed.
std::array<int, 3> parse_firmware(const std::string& text) {
  std::array<int, 3> out{0, 0, 0};
  std::size_t i = 0;
  while (i < text.size() && !std::isdigit(static_cast<unsigned char>(text[i]))) ++i;
  int component = 0;
  int have = 0;
  bool in_number = false;
  for (; i < text.size() && have < 3; ++i) {
    const char c = text[i];
    if (std::isdigit(static_cast<unsigned char>(c))) {
      if (component > 10000) return {0, 0, 0};
      component = component * 10 + (c - '0');
      in_number = true;
    } else if (c == '.') {
      out[static_cast<std::size_t>(have++)] = component;
      component = 0;
      in_number = false;
    } else {
      break;
    }
  }
  if (in_number && have < 3) out[static_cast<std::size_t>(have++)] = component;
  return out;
}

bool firmware_at_least(const std::array<int, 3>& have, const std::array<int, 3>& want) {
  return have >= want;
}

}  // namespace

bool format_set_finger_angles(const JointPose& pose, std::string& out) {
  bool any = false;
  std::array<std::string, kJointCount> fields;
  for (std::size_t j = 0; j < kJointCount; ++j) {
    if (!pose.has_value[j]) {
      fields[j].clear();
      continue;
    }
    const int value = pose.values[j];
    if (value < -kJointValueMax || value > kJointValueMax) return false;
    fields[j] = std::to_string(value);
    any = true;
  }
  if (!any) return false;
  // Drop trailing empty (held) fields: an omitted field holds like an empty one.
  std::size_t last = kJointCount;
  while (last > 0 && fields[last - 1].empty()) --last;
  std::string command = "set_finger_angles";
  for (std::size_t j = 0; j < last; ++j) {
    command += ':';
    command += fields[j];
  }
  out = std::move(command);
  return true;
}

ExoLinkWorker::ExoLinkWorker(ExoLinkConfig config, std::unique_ptr<SerialPort> port)
    : config_(std::move(config)), port_(std::move(port)) {}

ExoLinkWorker::~ExoLinkWorker() { stop(); }

void ExoLinkWorker::start() {
  std::lock_guard<std::mutex> lock(jobs_mutex_);
  if (thread_.joinable()) return;
  stopping_ = false;
  thread_ = std::thread([this] { run(); });
}

void ExoLinkWorker::stop() {
  {
    std::lock_guard<std::mutex> lock(jobs_mutex_);
    if (!thread_.joinable()) return;
  }
  // Best-effort disarm/close while the worker still runs.
  std::string ignored;
  submit([this](std::string& error) { return do_disconnect(error); }, &ignored, 5000);
  {
    std::lock_guard<std::mutex> lock(jobs_mutex_);
    stopping_ = true;
  }
  jobs_cv_.notify_all();
  if (thread_.joinable()) thread_.join();
}

bool ExoLinkWorker::connect(std::string* error_out, int timeout_ms) {
  start();
  return submit([this](std::string& error) { return do_connect(error); }, error_out, timeout_ms);
}

bool ExoLinkWorker::disconnect(std::string* error_out, int timeout_ms) {
  return submit([this](std::string& error) { return do_disconnect(error); }, error_out, timeout_ms);
}

bool ExoLinkWorker::arm(bool home, std::string* error_out, int timeout_ms) {
  return submit([this, home](std::string& error) { return do_arm(home, error); }, error_out,
                timeout_ms);
}

bool ExoLinkWorker::disarm(std::string* error_out, int timeout_ms) {
  return submit([this](std::string& error) { return do_disarm(error); }, error_out, timeout_ms);
}

bool ExoLinkWorker::home(std::string* error_out, int timeout_ms) {
  return submit([this](std::string& error) { return do_home(error); }, error_out, timeout_ms);
}

bool ExoLinkWorker::set_pose(const JointPose& pose, std::string* error_out, int timeout_ms) {
  return submit([this, pose](std::string& error) { return do_set_pose(pose, error); }, error_out,
                timeout_ms);
}

bool ExoLinkWorker::query(const std::string& command, std::string* error_out, int timeout_ms) {
  std::string prefix;
  if (command == "version") prefix = "Version:";
  else if (command == "get_gesture_angles:all") prefix = "GESTURE_ANGLES:";
  else if (command == "check_limits") prefix = "Limit check:";
  else { if (error_out) *error_out = "unsupported read-only query"; return false; }
  return submit([this, command, prefix](std::string& error) {
    if (!port_ || !port_->is_open()) { error = "connect the exo first"; return false; }
    if (snapshot().armed) { error = "read-only queries require connected (disarmed) mode"; return false; }
    return send_command(command, prefix, error);
  }, error_out, timeout_ms);
}

bool ExoLinkWorker::raw(const std::string& command, std::string* reply_out,
                        std::string* error_out, int timeout_ms) {
  // Trim and reject empty/oversized lines and embedded terminators; the firmware
  // parses one line at a time, so a raw multi-line string would desync framing.
  std::string trimmed = command;
  const auto begin = trimmed.find_first_not_of(" \t\r\n");
  const auto end = trimmed.find_last_not_of(" \t\r\n");
  trimmed = begin == std::string::npos ? std::string{} : trimmed.substr(begin, end - begin + 1);
  if (trimmed.empty()) { if (error_out) *error_out = "empty raw command"; return false; }
  if (trimmed.size() > 200) { if (error_out) *error_out = "raw command too long (max 200)"; return false; }
  if (trimmed.find_first_of("\r\n") != std::string::npos) {
    if (error_out) *error_out = "raw command must be a single line";
    return false;
  }
  return submit([this, trimmed, reply_out](std::string& error) {
    if (!port_ || !port_->is_open()) { error = "connect the exo first"; return false; }
    // Send verbatim with the standard terminator, then read whatever frame(s)
    // arrive within the reply budget. Many firmware replies begin "OK:"/"ERROR:"
    // and end with ';'; some commands are silent. read_until returns the first
    // frame containing ';'-delimited content, or times out (treated as an
    // accepted fire-and-forget: the caller sees an empty reply).
    read_buffer_.clear();
    const std::string framed = trimmed + config_.line_terminator;
    if (!port_->write(framed)) {
      error = "serial write failed: " + port_->error();
      port_->close();
      set_status([&error](ExoStatus& s) { s.link_open = false; s.last_error = error; });
      return false;
    }
    const auto reply = read_until_any(config_.reply_timeout_ms);
    const std::string text = reply.value_or("");
    set_status([&text](ExoStatus& s) { s.last_reply = text; });
    if (reply_out) *reply_out = text;
    // Surface a firmware ERROR as a failure so the terminal shows it as such,
    // but still return the text. A silent (empty) reply is not an error.
    std::string upper = text;
    std::transform(upper.begin(), upper.end(), upper.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    if (upper.find("ERROR:") != std::string::npos) {
      error = text;
      return false;
    }
    return true;
  }, error_out, timeout_ms);
}

ExoStatus ExoLinkWorker::snapshot() const {
  std::lock_guard<std::mutex> lock(status_mutex_);
  return status_;
}

void ExoLinkWorker::run() {
  for (;;) {
    std::shared_ptr<Job> job;
    {
      std::unique_lock<std::mutex> lock(jobs_mutex_);
      const auto idle_ms = config_.watchdog_ms > 0 ? std::min(config_.watchdog_ms, 25) : 100;
      jobs_cv_.wait_for(lock, std::chrono::milliseconds(idle_ms),
                        [this] { return stopping_ || !jobs_.empty(); });
      if (stopping_ && jobs_.empty()) return;
      if (!jobs_.empty()) {
        job = jobs_.front();
        jobs_.pop_front();
      }
    }
    if (job) {
      std::string error;
      {
        std::lock_guard<std::mutex> lock(jobs_mutex_);
        if (job->cancelled) continue;
      }
      service_idle();
      job->ok = job->fn(error);
      if (!job->ok) set_status([&error](ExoStatus& s) { s.last_error = error; });
      job->error = std::move(error);
      {
        std::lock_guard<std::mutex> lock(jobs_mutex_);
        job->done = true;
      }
      jobs_cv_.notify_all();
      continue;
    }
    service_idle();
  }
}

bool ExoLinkWorker::submit(std::function<bool(std::string&)> fn, std::string* error_out,
                           int timeout_ms) {
  auto job = std::make_shared<Job>();
  job->fn = std::move(fn);
  {
    std::lock_guard<std::mutex> lock(jobs_mutex_);
    if (stopping_ || !thread_.joinable()) {
      if (error_out) *error_out = "exo worker is not running";
      return false;
    }
    if (jobs_.size() >= 8) {
      if (error_out) *error_out = "exo command queue full";
      return false;
    }
    jobs_.push_back(job);
  }
  jobs_cv_.notify_all();
  std::unique_lock<std::mutex> lock(jobs_mutex_);
  const bool completed = jobs_cv_.wait_for(
      lock, std::chrono::milliseconds(timeout_ms), [&job] { return job->done; });
  if (!completed) {
    job->cancelled = true;
    if (error_out) *error_out = "exo command timed out; outcome unknown, do not retry motion";
    return false;
  }
  if (error_out) *error_out = job->error;
  return job->ok;
}

void ExoLinkWorker::service_idle() {
  if (!port_ || !port_->is_open()) return;
  const auto snap = snapshot();
  if (config_.watchdog_ms > 0 && snap.armed && last_command_time_ && !snap.watchdog_tripped) {
    const auto idle = Clock::now() - *last_command_time_;
    if (idle >= std::chrono::milliseconds(config_.watchdog_ms)) {
      std::string error;
      const bool ok = do_disarm(error);
      set_status([&](ExoStatus& s) {
        s.watchdog_tripped = true;
        s.last_error = ok ? "inactivity watchdog disarmed; explicit re-arm required" : "watchdog disarm failed: " + error;
      });
    }
  }
}

bool ExoLinkWorker::do_connect(std::string& error) {
  if (port_ && port_->is_open()) return true;
  if (!port_) {
    error = "no serial port";
    return false;
  }
  // Start every connect from the first CDC candidate. A previous failed connect
  // leaves the port on its last-tried candidate; without this reset a reconnect
  // would only ever probe that one interface (the "connected once, never
  // reconnects" failure).
  port_->reset_candidate();
  // Reply-route handshake, tried on each candidate CDC command interface in turn.
  //
  // route:both (not :cmd): the App claims one CDC and reads replies only there,
  // but which physical CDC is the firmware's command/reply channel is NOT
  // knowable from the descriptors -- the firmware's two PluggableUSB CDCs
  // (Serial / SerialTelem) are globals with no defined init order (its
  // DUAL_CDC_SWAP hazard). route:both mirrors replies to both firmware CDCs, so
  // whichever the App claimed carries them; it also matches the App serial
  // layer's single-node "defaults to reply_route:both" assumption and resets a
  // board another host left decoupled. Sending it every connect keeps the ACK as
  // a liveness/firmware-present check.
  //
  // Even with route:both, a claimed interface that is not one of the firmware's
  // two CDC endpoints returns nothing, so if the handshake gets no reply the
  // port advances to the next candidate control interface (0 then 2 on the
  // OpenRB) and we retry. Within one interface the (side-effect-free) handshake
  // is also retried, because a first reply can be lost to the DTR-assert race or
  // a stale startup banner; each attempt settles, drains buffered bytes, and
  // records what it saw in ExoStatus.last_error so a persistent failure is
  // diagnosable from state.exo without a device journal.
  bool handshaken = false;
  bool opened = port_->open();
  if (!opened) {
    error = port_->error().empty() ? "serial open failed" : port_->error();
    set_status([&error](ExoStatus& s) { s.link_open = false; s.last_error = error; });
    return false;
  }
  for (;;) {
    if (handshake_reply_route(error)) { handshaken = true; break; }
    set_status([&error](ExoStatus& s) { s.last_error = error; });
    // send_command / the last attempt closed the port; try the next CDC.
    if (!port_->select_next_candidate()) break;  // candidates exhausted
    if (!port_->open()) {
      error = "next CDC candidate open failed: " +
              (port_->error().empty() ? std::string("unknown") : port_->error());
      set_status([&error](ExoStatus& s) { s.link_open = false; s.last_error = error; });
      break;
    }
  }
  if (!handshaken) {
    port_->close();
    return false;
  }
  std::string firmware;
  bool firmware_ok = false;
  std::string fw_error;
  if (!query_firmware(firmware, firmware_ok, fw_error)) {
    error = fw_error; port_->close(); return false;
  }
  last_command_time_.reset();
  set_status([&](ExoStatus& s) {
    s.transport = port_->name();
    s.link_open = true;
    s.armed = false;
    s.firmware = firmware;
    s.firmware_ok = firmware_ok;
    s.watchdog_tripped = false;
    s.last_commanded = JointPose{};
    s.last_error.clear();
  });
  return true;
}

bool ExoLinkWorker::do_disconnect(std::string& error) {
  bool ok = true;
  if (snapshot().armed && (!port_ || !port_->is_open())) {
    error = "link lost while armed; physical torque state unknown"; ok = false;
  }
  if (port_ && port_->is_open()) {
    if (snapshot().armed) ok = do_disarm(error);
    port_->close();
  }
  read_buffer_.clear();
  last_command_time_.reset();
  set_status([](ExoStatus& s) {
    s.link_open = false;
    s.armed = false;
    s.watchdog_tripped = false;
    s.last_commanded = JointPose{};
  });
  return ok;
}

bool ExoLinkWorker::do_arm(bool home, std::string& error) {
  if (!port_ || !port_->is_open()) {
    error = "exo link is not open";
    return false;
  }
  if (!snapshot().firmware_ok) {
    error = "device firmware does not support set_finger_angles (needs >= 0.6.4)";
    return false;
  }
  // Current settings before torque: combined budget first (it constrains the
  // per-motor value), then per-motor, then enable, so nothing energizes at the
  // firmware's booted budget.
  if (config_.total_current_ma > 0) {
    if (!send_command("set_total_current_lim:" + std::to_string(config_.total_current_ma),
                      "current_lim", error))
      return false;
  }
  if (config_.per_motor_current_ma > 0) {
    if (!send_command("set_current_lim:all:" + std::to_string(config_.per_motor_current_ma),
                      "current_lim", error))
      return false;
  }
  if (!send_command("enable:all", "", error)) return false;  // silent in firmware.
  set_status([](ExoStatus& s) {
    s.armed = true;
    s.watchdog_tripped = false;
  });
  last_command_time_ = Clock::now();
  if (home && !do_home(error)) {
    std::string ignored;
    do_disarm(ignored);
    return false;
  }
  return true;
}

bool ExoLinkWorker::do_disarm(std::string& error) {
  if (snapshot().armed && (!port_ || !port_->is_open())) {
    error = "cannot disarm: link lost while armed; physical torque state unknown";
    return false;
  }
  if (port_ && port_->is_open()) {
    if (!send_command("disable:all", "", error)) {
      // A failed disarm write is worth surfacing, but still mark disarmed
      // intent; the link may be down and the caller needs to know.
      set_status([](ExoStatus& s) { s.armed = false; });
      return false;
    }
  }
  set_status([](ExoStatus& s) { s.armed = false; });
  return true;
}

bool ExoLinkWorker::do_home(std::string& error) {
  if (!port_ || !port_->is_open()) {
    error = "exo link is not open";
    return false;
  }
  if (!send_command("home:all", "home", error)) return false;
  last_command_time_ = Clock::now();
  JointPose neutral;
  for (std::size_t j = 0; j < kJointCount; ++j) neutral.set(static_cast<Joint>(j), 0);
  set_status([&neutral](ExoStatus& s) {
    s.last_commanded = neutral;
    s.watchdog_tripped = false;
  });
  return true;
}

bool ExoLinkWorker::do_set_pose(const JointPose& pose, std::string& error) {
  if (!port_ || !port_->is_open()) {
    error = "exo link is not open";
    return false;
  }
  if (!snapshot().armed) {
    error = "exo must be armed before commanding a pose";
    return false;
  }
  std::string command;
  if (!format_set_finger_angles(pose, command)) {
    error = "pose has no joint value in [-100, 100]";
    return false;
  }
  if (snapshot().watchdog_tripped) { error = "watchdog tripped; explicitly re-arm"; return false; }
  if (!send_command(command, "OK: finger_angles", error)) return false;
  const auto ack = snapshot().last_reply;
  if (ack.find("unknown=") != std::string::npos || ack.find("zero_travel=") != std::string::npos) {
    error = "firmware reported unsupported/un-calibrated joints; partial motion possible: " + ack;
    return false;
  }
  last_command_time_ = Clock::now();
  set_status([&pose](ExoStatus& s) {
    for (std::size_t j = 0; j < kJointCount; ++j) {
      if (pose.has_value[j]) {
        s.last_commanded.values[j] = pose.values[j];
        s.last_commanded.has_value[j] = true;
      }
    }
    s.watchdog_tripped = false;
  });
  return true;
}

bool ExoLinkWorker::send_command(const std::string& command, const std::string& expect_substr,
                                 std::string& error) {
  const std::string framed = command + config_.line_terminator;
  if (!port_->write(framed)) {
    error = "serial write failed: " + port_->error();
    set_status([&error](ExoStatus& s) {
      s.link_open = false;
      s.last_error = error;
    });
    port_->close();
    return false;
  }
  if (expect_substr.empty()) return true;  // silent-in-firmware command.
  const auto reply = read_until(expect_substr, config_.reply_timeout_ms);
  if (!reply) {
    error = "no '" + expect_substr + "' ack for " + command + "; outcome unknown; link closed";
    port_->close();
    set_status([&](ExoStatus& s) { s.link_open = false; s.last_error = error; });
    return false;
  }
  set_status([&](ExoStatus& s) { s.last_reply = *reply; });
  std::string upper = *reply;
  std::transform(upper.begin(), upper.end(), upper.begin(),
                 [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  if (upper.find("ERROR:") != std::string::npos) {
    error = "device rejected " + command + ": " + *reply;
    return false;
  }
  return true;
}

std::optional<std::string> ExoLinkWorker::read_until(const std::string& needle, int timeout_ms) {
  const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
  for (;;) {
    // Firmware terminates a reply frame with ';'. Split the accumulated buffer
    // on ';' and check each complete frame for the needle.
    std::size_t delim;
    while ((delim = read_buffer_.find(';')) != std::string::npos) {
      std::string frame = read_buffer_.substr(0, delim);
      read_buffer_.erase(0, delim + 1);
      if (frame.find(needle) != std::string::npos || frame.find("ERROR:") != std::string::npos) return frame;
    }
    const int remaining =
        static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
                             deadline - Clock::now())
                             .count());
    if (remaining <= 0) return std::nullopt;
    const std::string chunk = port_->read(256, std::min(remaining, 50));
    if (!port_->is_open() || !port_->error().empty()) return std::nullopt;
    if (read_buffer_.size() + chunk.size() > 16384) { port_->close(); read_buffer_.clear(); return std::nullopt; }
    if (!chunk.empty()) read_buffer_ += chunk;
  }
}

std::optional<std::string> ExoLinkWorker::read_until_any(int timeout_ms) {
  const auto deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
  for (;;) {
    const std::size_t delim = read_buffer_.find(';');
    if (delim != std::string::npos) {
      std::string frame = read_buffer_.substr(0, delim);
      read_buffer_.erase(0, delim + 1);
      return frame;  // first complete frame, whatever it contains
    }
    const int remaining =
        static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
                             deadline - Clock::now())
                             .count());
    if (remaining <= 0) return std::nullopt;
    const std::string chunk = port_->read(256, std::min(remaining, 50));
    if (!port_->is_open() || !port_->error().empty()) return std::nullopt;
    if (read_buffer_.size() + chunk.size() > 16384) { port_->close(); read_buffer_.clear(); return std::nullopt; }
    if (!chunk.empty()) read_buffer_ += chunk;
  }
}

std::string ExoLinkWorker::drain_rx(int timeout_ms) {
  std::string seen;
  const auto deadline = Clock::now() + std::chrono::milliseconds(std::max(0, timeout_ms));
  while (Clock::now() < deadline && port_ && port_->is_open()) {
    const int remaining =
        static_cast<int>(std::chrono::duration_cast<std::chrono::milliseconds>(
                             deadline - Clock::now())
                             .count());
    const std::string chunk = port_->read(256, std::min(std::max(1, remaining), 50));
    if (!port_->is_open() || !port_->error().empty()) break;
    if (chunk.empty()) continue;
    if (seen.size() < 512) seen += chunk;  // bounded diagnostic capture
  }
  return seen;
}

bool ExoLinkWorker::handshake_reply_route(std::string& error) {
  const int attempts = std::max(1, config_.connect_handshake_attempts);
  const std::string name = port_ ? port_->name() : "port";
  for (int attempt = 1; attempt <= attempts; ++attempt) {
    if (!port_ || !port_->is_open()) {
      if (!port_ || !port_->open()) {
        error = "reply_route on " + name + ": reopen failed";
        return false;
      }
    }
    // Settle after the DTR assert in open(), then clear any stale bytes so a
    // leftover partial frame cannot mask the ACK.
    if (config_.open_settle_ms > 0)
      std::this_thread::sleep_for(std::chrono::milliseconds(config_.open_settle_ms));
    const std::string drained = drain_rx(config_.open_settle_ms > 0 ? config_.open_settle_ms : 50);
    read_buffer_.clear();
    std::string attempt_error;
    if (send_command("set_reply_route:both", "OK: reply_route both", attempt_error))
      return true;
    // send_command closed the port on failure.
    error = "reply_route on " + name + " attempt " + std::to_string(attempt) + "/" +
            std::to_string(attempts) + ": " + attempt_error +
            (drained.empty() ? " (no bytes seen before write)"
                             : " (pre-write bytes: " + drained + ")");
  }
  return false;
}

bool ExoLinkWorker::query_firmware(std::string& firmware, bool& firmware_ok, std::string& error) {
  read_buffer_.clear();
  if (!send_command("version", "", error)) return false;
  const auto reply = read_until("ersion", config_.reply_timeout_ms);  // "Version"/"version"
  if (!reply || reply->find("ERROR:") != std::string::npos) {
    firmware.clear();
    firmware_ok = false;
    error = "no version reply";
    return false;
  }
  // Take the text after the last ':' if present, else the whole frame.
  const auto colon = reply->rfind(':');
  firmware = colon == std::string::npos ? *reply : reply->substr(colon + 1);
  // Trim surrounding whitespace.
  const auto begin = firmware.find_first_not_of(" \t\r\n");
  const auto end = firmware.find_last_not_of(" \t\r\n");
  firmware = begin == std::string::npos ? std::string{} : firmware.substr(begin, end - begin + 1);
  firmware_ok = firmware_at_least(parse_firmware(firmware), kMinFingerAnglesFirmware);
  return true;
}

void ExoLinkWorker::set_status(const std::function<void(ExoStatus&)>& mutate) {
  std::lock_guard<std::mutex> lock(status_mutex_);
  mutate(status_);
}

}  // namespace scifi2_hub::exo
