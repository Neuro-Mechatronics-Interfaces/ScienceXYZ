#include "exo_control/controller.hpp"
#include <algorithm>
#include <atomic>
#include <iomanip>
#include <random>
#include <sstream>
#include <set>
#include <thread>

namespace exo_control {
namespace {
using Clock = std::chrono::steady_clock;
constexpr size_t max_message = 4 * 1024 * 1024;
template<class T> T parse(const std::string& bytes) {
  T value;
  if (bytes.size() > max_message || !value.ParseFromString(bytes) || value.protocol_version() != 1)
    throw std::runtime_error("malformed message or unsupported protocol version");
  return value;
}
std::string session_id() {
  static std::atomic<unsigned long long> counter{0};
  std::random_device random;
  std::ostringstream out;
  out << "exo/" << std::hex << random() << '-' << random() << '-'
      << Clock::now().time_since_epoch().count() << '-' << ++counter;
  return out.str();
}
}
CommandError::CommandError(wire::CommandResult value)
  : std::runtime_error("device rejected command: " + value.error().message()), result(std::move(value)) {}
Controller::Controller(std::unique_ptr<Transport> transport, Milliseconds timeout)
  : transport_(std::move(transport)), timeout_(timeout), session_(session_id()) {
  if (!transport_ || timeout.count() < 1 || timeout.count() > 60000)
    throw std::invalid_argument("transport required; timeout must be 1..60000 ms");
}
Controller::~Controller() { try { disconnect(); } catch (...) {} }
wire::ControlCommand Controller::command(wire::CommandKind kind) {
  wire::ControlCommand value;
  value.set_protocol_version(1);
  value.set_request_id(session_ + '/' + std::to_string(++sequence_));
  value.set_command(kind);
  return value;
}
void Controller::connect() {
  if (connected_) return;
  state_.reset();
  try {
    transport_->open(timeout_);
    connected_ = true;
    // PUB/SUB subscriptions propagate asynchronously. Only this read-only
    // handshake is retried, under one deadline; never retry mode/pose/raw.
    auto deadline = Clock::now() + timeout_;
    auto next_send = Clock::time_point::min();
    std::set<std::string> handshake_ids; // at most 601 IDs for the 60 s maximum
    bool acknowledged = false;
    while (Clock::now() < deadline) {
      if (Clock::now() >= next_send) {
        // Fresh read-only IDs cause the App to publish another snapshot rather
        // than returning only a cached result from an earlier lost publication.
        auto request = command(wire::COMMAND_GET_STATE);
        request.mutable_get_state();
        handshake_ids.insert(request.request_id());
        transport_->send(request.SerializeAsString());
        next_send = Clock::now() + Milliseconds(100);
      }
      read_state(Milliseconds(0));
      if (acknowledged && state_) break;
      auto bytes = transport_->receive(false, Milliseconds(10));
      if (!bytes) continue;
      auto result = parse<wire::CommandResult>(*bytes);
      if (handshake_ids.count(result.request_id()) == 0 || result.command() != wire::COMMAND_GET_STATE) continue;
      if (result.status() == wire::RESULT_FAILED) throw CommandError(result);
      if (result.status() == wire::RESULT_SUCCEEDED && result.error().code() == wire::ERROR_NONE) {
        acknowledged = true;
        if (state_) break;
      }
    }
    if (!acknowledged || !state_) throw TimeoutError("connect handshake timed out waiting for result/state");
    auto subscribe = command(wire::COMMAND_SUBSCRIBE_STATE);
    subscribe.mutable_subscribe_state()->set_enabled(true);
    execute(std::move(subscribe));
  } catch (...) {
    connected_ = false;
    transport_->close();
    throw;
  }
}
void Controller::disconnect() {
  std::exception_ptr failure;
  if (connected_ && needs_off_) {
    try { set_mode(wire::EXO_MODE_OFF); } catch (...) { failure = std::current_exception(); }
  }
  connected_ = false;
  transport_->close();
  state_.reset();
  // Preserve unresolved disarm across reconnect; a timeout is not an OFF ack.
  if (failure) std::rethrow_exception(failure);
}
wire::CommandResult Controller::execute(wire::ControlCommand request) {
  if (!connected_) throw std::runtime_error("controller disconnected");
  // Track possible engagement immediately before send, including send failures
  // with unknown delivery, but never for rejected pre-connect API calls.
  if ((request.command() == wire::COMMAND_SET_EXO_MODE && request.set_exo_mode().mode() != wire::EXO_MODE_OFF) ||
      request.command() == wire::COMMAND_SET_EXO_POSE || request.command() == wire::COMMAND_EXO_RAW)
    needs_off_ = true;
  transport_->send(request.SerializeAsString());
  auto deadline = Clock::now() + timeout_;
  while (Clock::now() < deadline) {
    read_state(Milliseconds(0));
    auto remaining = std::chrono::duration_cast<Milliseconds>(deadline - Clock::now());
    auto bytes = transport_->receive(false, std::max(Milliseconds(0), std::min(remaining, Milliseconds(10))));
    if (!bytes) continue;
    auto result = parse<wire::CommandResult>(*bytes);
    if (result.request_id() != request.request_id() || result.command() != request.command()) continue;
    if (result.status() == wire::RESULT_ACCEPTED) continue;
    if (result.status() == wire::RESULT_FAILED) throw CommandError(result);
    if (result.status() != wire::RESULT_SUCCEEDED || result.error().code() != wire::ERROR_NONE)
      throw std::runtime_error("invalid terminal command result");
    return result;
  }
  throw TimeoutError("command timed out; outcome unknown; do not automatically retry " + request.request_id());
}
wire::CommandResult Controller::set_mode(wire::ExoMode mode) {
  if (mode != wire::EXO_MODE_OFF && mode != wire::EXO_MODE_CONNECTED &&
      mode != wire::EXO_MODE_EXTERNAL && mode != wire::EXO_MODE_DECODE)
    throw std::invalid_argument("invalid exo mode");
  auto value = command(wire::COMMAND_SET_EXO_MODE);
  value.mutable_set_exo_mode()->set_mode(mode);
  auto result = execute(std::move(value));
  if (mode == wire::EXO_MODE_OFF) needs_off_ = false;
  return result;
}
wire::CommandResult Controller::set_pose(const std::map<wire::ExoJoint, int>& joints) {
  if (joints.empty()) throw std::invalid_argument("pose must name at least one joint");
  auto value = command(wire::COMMAND_SET_EXO_POSE);
  for (auto [joint, target] : joints) {
    if (joint < wire::EXO_JOINT_THUMB || joint > wire::EXO_JOINT_WRIST || target < -100 || target > 100)
      throw std::invalid_argument("joint must be 1..6; target must be -100..100");
    auto* entry = value.mutable_set_exo_pose()->add_joints();
    entry->set_joint(joint); entry->set_value(target);
  }
  return execute(std::move(value));
}
wire::CommandResult Controller::query(const std::string& query) {
  if (query != "version" && query != "check_limits" && query != "get_gesture_angles:all")
    throw std::invalid_argument("query not in read-only allowlist");
  auto value = command(wire::COMMAND_QUERY_EXO);
  value.mutable_query_exo()->set_query(query);
  return execute(std::move(value));
}
wire::CommandResult Controller::raw(const std::string& text) {
  if (text.empty() || text.size() > 200 || text.find_first_of("\r\n") != std::string::npos ||
      text.find('\0') != std::string::npos || text.find_first_not_of(" \t") == std::string::npos)
    throw std::invalid_argument("raw requires a nonblank single line, at most 200 UTF-8 bytes, no NUL");
  auto value = command(wire::COMMAND_EXO_RAW);
  value.mutable_exo_raw()->set_command(text);
  return execute(std::move(value));
}
wire::CommandResult Controller::get_state() {
  auto value = command(wire::COMMAND_GET_STATE); value.mutable_get_state();
  return execute(std::move(value));
}
void Controller::read_state(Milliseconds timeout) {
  auto bytes = transport_->receive(true, timeout);
  if (!bytes) return;
  auto value = parse<wire::StateSnapshot>(*bytes);
  if (state_ && value.task().app_session_id() == state_->task().app_session_id() &&
      value.state_version() < state_->state_version()) return;
  state_ = std::move(value);
  if (callback_) callback_(*state_);
}
void Controller::poll(Milliseconds timeout) {
  if (!connected_) throw std::runtime_error("controller disconnected");
  if (timeout.count() < 0 || timeout > timeout_) throw std::invalid_argument("invalid poll timeout");
  read_state(timeout);
}
void Controller::on_state(std::function<void(const wire::StateSnapshot&)> callback) { callback_ = std::move(callback); }
}
