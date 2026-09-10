// Hardware-free tests for the exo link worker and command formatting.
//
// Drives ExoLinkWorker against an in-process fake SerialPort that answers like
// the OpenRB dual-CDC firmware: commands that are silent in firmware stay
// silent here, so the fake cannot make the worker look healthier than a real
// device. No serial hardware and no nml_hand_exo SDK are required.

#include "exo_link.hpp"
#include "serial_port.hpp"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

using scifi2_hub::exo::ExoLinkConfig;
using scifi2_hub::exo::ExoLinkWorker;
using scifi2_hub::exo::Joint;
using scifi2_hub::exo::JointPose;
using scifi2_hub::exo::SerialPort;

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

// Firmware-shaped fake. Thread-safe: the worker thread calls write()/read()
// while the test thread inspects sent().
class FakeSerialPort : public SerialPort {
 public:
  bool suppress_route = false;
  bool suppress_pose = false;
  explicit FakeSerialPort(std::string firmware) : firmware_(std::move(firmware)) {}

  bool open() override {
    open_ = true;
    return true;
  }
  void close() override { open_ = false; }
  bool is_open() const override { return open_; }

  bool write(const std::string& data) override {
    if (!open_) {
      error_ = "closed";
      return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    // The worker frames commands with a trailing "\r\n"; split into lines.
    buffer_ += data;
    std::size_t nl;
    while ((nl = buffer_.find('\n')) != std::string::npos) {
      std::string line = buffer_.substr(0, nl);
      buffer_.erase(0, nl + 1);
      while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
      if (line.empty()) continue;
      sent_.push_back(line);
      const std::string reply = reply_for(line);
      if (!reply.empty()) pending_ += reply;
    }
    return true;
  }

  std::string read(std::size_t max_bytes, int timeout_ms) override {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
      {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!pending_.empty()) {
          const std::size_t n = std::min(max_bytes, pending_.size());
          std::string out = pending_.substr(0, n);
          pending_.erase(0, n);
          return out;
        }
      }
      if (std::chrono::steady_clock::now() >= deadline) return {};
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
  }

  const std::string& name() const override { return name_; }
  const std::string& error() const override { return error_; }

  std::vector<std::string> sent() {
    std::lock_guard<std::mutex> lock(mutex_);
    return sent_;
  }

 private:
  std::string reply_for(const std::string& command) {
    const auto head = command.substr(0, command.find(':'));
    if (command == "set_reply_route:cmd") return suppress_route ? "" : "OK: reply_route cmd;";
    if (command == "check_limits") return "Limit check:\nMotor 1: OK;";
    if (head == "version") return "Exo Device Version: " + firmware_ + ";";
    if (head == "set_finger_angles") return suppress_pose ? "" : "OK: finger_angles " + command + ";";
    if (head == "set_total_current_lim") return "OK: total_current_lim;";
    if (head == "set_current_lim") return "OK: set_current_lim;";
    if (head == "home") return "OK: home;";
    // enable / disable are silent in firmware.
    return {};
  }

  std::string firmware_;
  std::string name_ = "FAKE";
  std::string error_;
  std::atomic<bool> open_{false};
  std::mutex mutex_;
  std::string buffer_;
  std::string pending_;
  std::vector<std::string> sent_;
};

bool sent_contains(std::vector<std::string> sent, const std::string& command) {
  for (const auto& line : sent) {
    if (line == command) return true;
  }
  return false;
}

std::ptrdiff_t index_of(const std::vector<std::string>& sent, const std::string& command) {
  for (std::size_t i = 0; i < sent.size(); ++i) {
    if (sent[i] == command) return static_cast<std::ptrdiff_t>(i);
  }
  return -1;
}

void test_format_set_finger_angles() {
  JointPose pose;
  pose.set(Joint::kThumb, 70);
  pose.set(Joint::kIndex, -40);
  pose.set(Joint::kWrist, 0);
  std::string out;
  expect(scifi2_hub::exo::format_set_finger_angles(pose, out), "pose with values formats");
  // thumb=70, index=-40, middle/ring/pinky held (empty), wrist=0.
  expect(out == "set_finger_angles:70:-40::::0", "batch command matches wire form");

  JointPose empty;
  expect(!scifi2_hub::exo::format_set_finger_angles(empty, out), "empty pose is rejected");

  JointPose out_of_range;
  out_of_range.set(Joint::kIndex, 200);
  expect(!scifi2_hub::exo::format_set_finger_angles(out_of_range, out), "out-of-range value rejected");

  JointPose trailing;
  trailing.set(Joint::kThumb, 10);
  expect(scifi2_hub::exo::format_set_finger_angles(trailing, out), "single leading joint formats");
  expect(out == "set_finger_angles:10", "trailing held joints are dropped");
}

ExoLinkConfig test_config() {
  ExoLinkConfig config;
  config.watchdog_ms = 0;  // deterministic unless a test opts in.
  config.reply_timeout_ms = 500;
  return config;
}

void test_connect_reports_firmware() {
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  FakeSerialPort* raw = port.get();
  ExoLinkWorker worker(test_config(), std::move(port));
  std::string error;
  expect(worker.connect(&error), "connect succeeds");
  const auto status = worker.snapshot();
  expect(status.link_open, "link is open");
  expect(status.firmware_ok, "firmware 0.6.4 is accepted");
  expect(status.firmware.find("0.6.4") != std::string::npos, "firmware string parsed");
  expect(sent_contains(raw->sent(), "version"), "version was queried");
  worker.stop();
}

void test_old_firmware_blocks_arm() {
  auto port = std::make_unique<FakeSerialPort>("0.5.0");
  ExoLinkWorker worker(test_config(), std::move(port));
  expect(worker.connect(), "connect succeeds on old firmware");
  expect(!worker.snapshot().firmware_ok, "old firmware is not ok");
  std::string error;
  expect(!worker.arm(/*home=*/false, &error), "arm is refused on old firmware");
  expect(error.find("0.6.4") != std::string::npos, "arm error names the required version");
  worker.stop();
}

void test_arm_orders_current_before_enable() {
  auto config = test_config();
  config.total_current_ma = 800;
  config.per_motor_current_ma = 250;
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  FakeSerialPort* raw = port.get();
  ExoLinkWorker worker(config, std::move(port));
  expect(worker.connect(), "connect");
  expect(worker.arm(/*home=*/false), "arm succeeds");
  const auto sent = raw->sent();
  const auto budget = index_of(sent, "set_total_current_lim:800");
  const auto per_motor = index_of(sent, "set_current_lim:all:250");
  const auto enable = index_of(sent, "enable:all");
  expect(budget >= 0 && per_motor >= 0 && enable >= 0, "all arm commands sent");
  expect(budget < per_motor && per_motor < enable, "budget before per-motor before enable");
  expect(worker.snapshot().armed, "worker reports armed");
  worker.stop();
}

void test_set_pose_requires_arm() {
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  ExoLinkWorker worker(test_config(), std::move(port));
  expect(worker.connect(), "connect");
  JointPose pose;
  pose.set(Joint::kIndex, 50);
  std::string error;
  expect(!worker.set_pose(pose, &error), "pose refused before arm");
  expect(error.find("armed") != std::string::npos, "error explains arm requirement");
  worker.stop();
}

void test_set_pose_writes_batch() {
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  FakeSerialPort* raw = port.get();
  ExoLinkWorker worker(test_config(), std::move(port));
  expect(worker.connect(), "connect");
  expect(worker.arm(/*home=*/false), "arm");
  JointPose pose;
  pose.set(Joint::kThumb, 70);
  pose.set(Joint::kIndex, -40);
  expect(worker.set_pose(pose), "pose accepted when armed");
  expect(sent_contains(raw->sent(), "set_finger_angles:70:-40"), "batch command written");
  const auto status = worker.snapshot();
  expect(status.last_commanded.has_value[static_cast<std::size_t>(Joint::kThumb)], "thumb recorded");
  expect(status.last_commanded.values[static_cast<std::size_t>(Joint::kIndex)] == -40,
         "index value recorded");
  worker.stop();
}

void test_watchdog_disarms() {
  auto config = test_config();
  config.watchdog_ms = 50;
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  FakeSerialPort* raw = port.get();
  ExoLinkWorker worker(config, std::move(port));
  expect(worker.connect(), "connect");
  expect(worker.arm(/*home=*/false), "arm");
  JointPose pose;
  pose.set(Joint::kIndex, 80);
  expect(worker.set_pose(pose), "pose accepted");
  // Wait past the idle gap; the worker thread should ease to neutral.
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (std::chrono::steady_clock::now() < deadline && !worker.snapshot().watchdog_tripped) {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  expect(worker.snapshot().watchdog_tripped, "watchdog tripped");
  expect(sent_contains(raw->sent(), "disable:all"), "watchdog disables torque");
  expect(!worker.snapshot().armed, "watchdog requires rearm");
  expect(!worker.set_pose(pose), "late pose cannot reset watchdog");
  worker.stop();
}

void test_missing_reply_closes_without_replay() {
  ExoLinkConfig config;
  config.watchdog_ms = 1000;
  config.reply_timeout_ms = 20;
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  auto* raw = port.get();
  raw->suppress_pose = true;
  ExoLinkWorker worker(config, std::move(port));
  expect(worker.connect(), "handshake");
  expect(worker.query("check_limits"), "read query");
  expect(worker.snapshot().last_reply.find("Limit check:") != std::string::npos, "query reply retained");
  expect(!worker.query("enable:all"), "raw writes rejected");
  expect(worker.arm(false), "arm without home");
  expect(!worker.query("check_limits"), "queries cannot delay armed watchdog");
  JointPose pose; pose.set(Joint::kIndex, 10);
  expect(!worker.set_pose(pose), "missing pose ACK fails");
  expect(!worker.snapshot().link_open, "uncertain link closed");
  expect(!worker.disarm(), "closed link cannot confirm disarm");
  expect(!worker.disconnect(), "disconnect reports unknown torque");
  expect(!sent_contains(raw->sent(), "home:all"), "no implicit home");
}

void test_missing_route_ack_never_arms() {
  ExoLinkConfig config; config.reply_timeout_ms = 20;
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  auto* raw = port.get(); raw->suppress_route = true;
  ExoLinkWorker worker(config, std::move(port));
  expect(!worker.connect(), "route acknowledgement required");
  expect(!worker.snapshot().link_open, "failed handshake closed");
  expect(!sent_contains(raw->sent(), "enable:all"), "probe cannot enable");
}

void test_disconnect_disarms() {
  auto port = std::make_unique<FakeSerialPort>("0.6.4");
  FakeSerialPort* raw = port.get();
  ExoLinkWorker worker(test_config(), std::move(port));
  expect(worker.connect(), "connect");
  expect(worker.arm(/*home=*/false), "arm");
  expect(worker.disconnect(), "disconnect");
  expect(sent_contains(raw->sent(), "disable:all"), "disarm command sent on disconnect");
  expect(!worker.snapshot().link_open, "link reported closed");
  expect(!worker.snapshot().armed, "worker reports disarmed");
  worker.stop();
}

}  // namespace

int main() {
  test_format_set_finger_angles();
  test_connect_reports_firmware();
  test_old_firmware_blocks_arm();
  test_arm_orders_current_before_enable();
  test_set_pose_requires_arm();
  test_set_pose_writes_batch();
  test_watchdog_disarms();
  test_disconnect_disarms();
  test_missing_reply_closes_without_replay();
  test_missing_route_ack_never_arms();
  std::cout << "exo_link_test: all tests passed\n";
  return 0;
}
