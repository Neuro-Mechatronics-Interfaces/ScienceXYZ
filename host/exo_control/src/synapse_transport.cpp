#include "exo_control/controller.hpp"
#include "api/synapse.grpc.pb.h"
#include <grpcpp/grpcpp.h>
#include <zmq.hpp>
#include <array>
#include <regex>

namespace exo_control {
namespace {
class SynapseTransport final : public Transport {
  std::string host_;
  int port_;
  std::unique_ptr<zmq::context_t> context_;
  std::array<std::unique_ptr<zmq::socket_t>, 3> sockets_;
public:
  SynapseTransport(std::string host, int port) : host_(std::move(host)), port_(port) {
    // Host and RPC port are separate to avoid ambiguous URI parsing. IPv6 is
    // accepted as a bare literal and bracketed when constructing endpoints.
    if (host_.empty() || host_.find_first_of("/[] \t\r\n") != std::string::npos ||
        host_.find('\0') != std::string::npos || port < 1 || port > 65535)
      throw std::invalid_argument("expected bare hostname/IP and RPC port 1..65535");
  }
  ~SynapseTransport() override { close(); }
  void open(Milliseconds timeout) override {
    close();
    try {
      auto host = host_.find(':') == std::string::npos ? host_ : '[' + host_ + ']';
      auto rpc = synapse::SynapseDevice::NewStub(grpc::CreateChannel(
          host + ':' + std::to_string(port_), grpc::InsecureChannelCredentials()));
      grpc::ClientContext request_context;
      request_context.set_deadline(std::chrono::system_clock::now() + timeout);
      synapse::QueryRequest request;
      request.set_query_type(synapse::QueryRequest::kListTaps);
      request.mutable_list_taps_query();
      synapse::QueryResponse response;
      auto status = rpc->Query(&request_context, request, &response);
      if (status.error_code() == grpc::StatusCode::DEADLINE_EXCEEDED)
        throw TimeoutError("ListTaps RPC deadline exceeded: " + status.error_message());
      if (!status.ok()) throw std::runtime_error("ListTaps RPC failed: " + status.error_message());
      if (response.status().code() != synapse::kOk || !response.has_list_taps_response())
        throw std::runtime_error("ListTaps rejected or missing response: " + response.status().message());
      context_ = std::make_unique<zmq::context_t>(1);
      const std::array<std::string, 3> names = {"control", "command_result", "state"};
      for (size_t i = 0; i < names.size(); ++i) {
        const synapse::TapConnection* selected = nullptr;
        for (const auto& tap : response.list_taps_response().taps()) {
          if (tap.name() != names[i]) continue;
          if (selected) throw std::runtime_error("duplicate Tap: " + names[i]);
          selected = &tap;
        }
        if (!selected) throw std::runtime_error("missing Tap: " + names[i]);
        const auto type = selected->tap_type();
        // Science's Python/C++ clients subscribe to legacy output Taps whose
        // direction is omitted (proto3 UNSPECIFIED). Control must be explicit.
        const bool compatible = i == 0 ? type == synapse::TAP_TYPE_CONSUMER
            : type == synapse::TAP_TYPE_PRODUCER || type == synapse::TAP_TYPE_UNSPECIFIED;
        if (!compatible) throw std::runtime_error("wrong Tap direction: " + names[i] +
            " (advertised " + std::to_string(static_cast<int>(type)) + ", expected " +
            (i == 0 ? "CONSUMER=2" : "PRODUCER=1 or legacy UNSPECIFIED=0") + ")");
        std::smatch match;
        const auto endpoint = selected->endpoint();
        if (!std::regex_match(endpoint, match, std::regex(R"(tcp://(?:\[[^\]]+\]|[^:]+):([0-9]{1,5}))")))
          throw std::runtime_error("Tap must advertise a TCP endpoint: " + names[i]);
        const auto tap_port = std::stoi(match[1].str());
        if (tap_port < 1 || tap_port > 65535) throw std::runtime_error("invalid Tap port");
        auto socket = std::make_unique<zmq::socket_t>(*context_, i == 0 ? zmq::socket_type::pub : zmq::socket_type::sub);
        socket->set(zmq::sockopt::linger, 0);
        if (host_.find(':') != std::string::npos) socket->set(zmq::sockopt::ipv6, 1);
        socket->set(zmq::sockopt::sndtimeo, static_cast<int>(timeout.count()));
        socket->set(zmq::sockopt::sndhwm, 64);
        socket->set(zmq::sockopt::rcvhwm, 256);
        socket->set(zmq::sockopt::maxmsgsize, static_cast<int64_t>(4 * 1024 * 1024));
        if (i != 0) socket->set(zmq::sockopt::subscribe, "");
        socket->connect("tcp://" + host + ':' + std::to_string(tap_port));
        sockets_[i] = std::move(socket);
      }
    } catch (...) { close(); throw; }
  }
  void send(const std::string& bytes) override {
    if (!sockets_[0]) throw std::runtime_error("transport closed");
    if (!sockets_[0]->send(zmq::buffer(bytes), zmq::send_flags::none))
      throw std::runtime_error("Tap send failed; outcome unknown");
  }
  std::optional<std::string> receive(bool state, Milliseconds timeout) override {
    auto& socket = sockets_[state ? 2 : 1];
    if (!socket) throw std::runtime_error("transport closed");
    socket->set(zmq::sockopt::rcvtimeo, static_cast<int>(timeout.count()));
    zmq::message_t message;
    if (!socket->recv(message)) return std::nullopt;
    if (socket->get(zmq::sockopt::rcvmore)) throw std::runtime_error("unexpected multipart Tap message");
    return std::string(static_cast<const char*>(message.data()), message.size());
  }
  void close() noexcept override {
    for (auto& socket : sockets_) socket.reset();
    context_.reset();
  }
};
}
std::unique_ptr<Transport> make_synapse_transport(const std::string& host, int rpc_port) {
  return std::make_unique<SynapseTransport>(host, rpc_port);
}
}
