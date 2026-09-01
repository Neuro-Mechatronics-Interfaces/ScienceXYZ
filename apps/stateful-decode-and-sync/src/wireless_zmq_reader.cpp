#include "wireless_zmq_reader.hpp"

#include <utility>

namespace app::wireless {

ZmqNonblockingReader::ZmqNonblockingReader(zmq::context_t& context,
                                           std::vector<std::string> endpoints,
                                           std::string topic)
    : socket_(context, zmq::socket_type::sub),
      endpoints_(std::move(endpoints)),
      topic_(std::move(topic)) {
  socket_.set(zmq::sockopt::linger, 0);
  socket_.set(zmq::sockopt::subscribe, topic_);
}

bool ZmqNonblockingReader::connect_next() {
  disconnect();
  if (endpoints_.empty()) return false;
  const auto endpoint = endpoints_[next_endpoint_ % endpoints_.size()];
  next_endpoint_ = (next_endpoint_ + 1) % endpoints_.size();
  try {
    socket_.connect(endpoint);
  } catch (const zmq::error_t&) {
    return false;
  }
  current_endpoint_ = endpoint;
  return true;
}

void ZmqNonblockingReader::disconnect() {
  if (!current_endpoint_.has_value()) return;
  try {
    socket_.disconnect(*current_endpoint_);
  } catch (const zmq::error_t&) {
    // The socket is still usable for the next explicit failover attempt.
  }
  current_endpoint_.reset();
}

bool ZmqNonblockingReader::try_receive(MultipartMessage& message) {
  message.frames.clear();
  if (!connected()) return false;

  zmq::message_t frame;
  auto received = socket_.recv(frame, zmq::recv_flags::dontwait);
  if (!received.has_value()) return false;

  bool more = socket_.get(zmq::sockopt::rcvmore);
  while (true) {
    if (message.frames.size() < kMaxRetainedFrames) {
      message.frames.emplace_back(static_cast<const char*>(frame.data()), frame.size());
    }
    if (!more) break;

    frame = zmq::message_t{};
    received = socket_.recv(frame, zmq::recv_flags::dontwait);
    if (!received.has_value()) break;
    more = socket_.get(zmq::sockopt::rcvmore);
  }
  return true;
}

}  // namespace app::wireless
