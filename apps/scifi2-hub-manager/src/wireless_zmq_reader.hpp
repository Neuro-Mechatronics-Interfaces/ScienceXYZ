#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <vector>

#include <zmq.hpp>

#include "wireless_ingress.hpp"

namespace scifi2_hub::wireless {

// One SUB socket owns one logical source. Endpoint failover is explicit:
// connect_next() disconnects the current endpoint before trying the next one.
// The reader never blocks in try_receive().
class ZmqNonblockingReader final : public NonblockingReader {
 public:
  ZmqNonblockingReader(zmq::context_t& context, std::vector<std::string> endpoints,
                       std::string topic);

  bool connect_next();
  void disconnect();
  bool connected() const { return current_endpoint_.has_value(); }
  const std::optional<std::string>& current_endpoint() const { return current_endpoint_; }

  bool try_receive(MultipartMessage& message) override;

 private:
  static constexpr std::size_t kMaxRetainedFrames = 3;

  zmq::socket_t socket_;
  std::vector<std::string> endpoints_;
  std::string topic_;
  std::size_t next_endpoint_ = 0;
  std::optional<std::string> current_endpoint_;
};

}  // namespace scifi2_hub::wireless
