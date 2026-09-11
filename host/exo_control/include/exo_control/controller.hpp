#pragma once
#include <chrono>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include "gui_control.pb.h"

namespace exo_control {
namespace wire = stateful_decode_and_sync::v1;
using Milliseconds = std::chrono::milliseconds;

// All transport calls and destruction occur on the owning controller's thread.
// Implementations must bound open/read/send/close and throw on transport errors.
class Transport {
public:
  virtual ~Transport() = default;
  virtual void open(Milliseconds timeout) = 0;
  virtual void send(const std::string& bytes) = 0;
  virtual std::optional<std::string> receive(bool state, Milliseconds timeout) = 0;
  virtual void close() noexcept = 0;
};
std::unique_ptr<Transport> make_synapse_transport(const std::string& host, int rpc_port = 647);

class CommandError : public std::runtime_error {
public:
  wire::CommandResult result;
  explicit CommandError(wire::CommandResult value);
};
class TimeoutError : public std::runtime_error {
public:
  using std::runtime_error::runtime_error;
};

// Single-owner synchronous API. Use one worker/executor, never the UI thread.
// No automatic retries of commands with potential side effects.
class Controller {
public:
  explicit Controller(std::unique_ptr<Transport>, Milliseconds timeout = Milliseconds(5000));
  ~Controller();
  Controller(const Controller&) = delete;
  Controller& operator=(const Controller&) = delete;
  void connect(); // transport + acknowledged protocol handshake; does not engage USB
  void disconnect(); // best-effort OFF, always close; throws if OFF unconfirmed
  bool connected() const noexcept { return connected_; }
  wire::CommandResult set_mode(wire::ExoMode);
  wire::CommandResult set_pose(const std::map<wire::ExoJoint, int>&);
  wire::CommandResult query(const std::string&);
  wire::CommandResult raw(const std::string&);
  wire::CommandResult get_state();
  // Call periodically on the owner thread while idle. Callback runs on this
  // thread, must be short, and must not reenter/destroy this controller.
  void poll(Milliseconds timeout = Milliseconds(0));
  void on_state(std::function<void(const wire::StateSnapshot&)> callback);
  const std::optional<wire::StateSnapshot>& state() const noexcept { return state_; }
private:
  wire::ControlCommand command(wire::CommandKind);
  wire::CommandResult execute(wire::ControlCommand);
  void read_state(Milliseconds);
  std::unique_ptr<Transport> transport_;
  Milliseconds timeout_;
  bool connected_ = false, needs_off_ = false;
  std::string session_;
  unsigned long long sequence_ = 0;
  std::optional<wire::StateSnapshot> state_;
  std::function<void(const wire::StateSnapshot&)> callback_;
};
}
