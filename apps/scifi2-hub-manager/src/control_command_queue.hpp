#pragma once

#include <cstddef>
#include <deque>
#include <mutex>
#include <string>
#include <unordered_set>
#include <utility>

#include "control_protocol.hpp"

namespace scifi2_hub::control {

// A small request is copied by a tap callback and consumed by the App main
// loop.  Legacy source-mode changes have no protocol envelope, while the
// other legacy taps are adapted to ControlCommand by SciFi2HubManagerApp.
struct ControlRequest {
  enum class Kind { kProtocolCommand, kLegacySourceMode };

  Kind kind = Kind::kProtocolCommand;
  protocol::ControlCommand command;
  int legacy_source_mode = 0;
  bool reserve_request_id = true;
  bool rejected_before_enqueue = false;
  protocol::ErrorCode rejection_code = stateful_decode_and_sync::v1::ERROR_NONE;
  std::string rejection_field;
  std::string rejection_message;

  static ControlRequest protocol_command(const protocol::ControlCommand& command) {
    ControlRequest request;
    request.kind = Kind::kProtocolCommand;
    request.command = command;
    return request;
  }

  static ControlRequest legacy_source_mode_request(int mode) {
    ControlRequest request;
    request.kind = Kind::kLegacySourceMode;
    request.legacy_source_mode = mode;
    request.reserve_request_id = false;
    return request;
  }

  static ControlRequest legacy_protocol_command(const protocol::ControlCommand& command) {
    ControlRequest request;
    request.kind = Kind::kProtocolCommand;
    request.command = command;
    // Legacy taps predate request-id de-duplication. They may be invoked
    // repeatedly with the same compatibility id and must retain that behavior.
    request.reserve_request_id = false;
    return request;
  }

  static ControlRequest rejected_protocol_command(const protocol::ControlCommand& command,
                                                  protocol::ErrorCode code,
                                                  std::string field,
                                                  std::string message) {
    ControlRequest request;
    request.kind = Kind::kProtocolCommand;
    request.command = command;
    request.reserve_request_id = false;
    request.rejected_before_enqueue = true;
    request.rejection_code = code;
    request.rejection_field = std::move(field);
    request.rejection_message = std::move(message);
    return request;
  }
};

// Bounded FIFO shared by tap callback threads and the App main loop.  The
// callback path performs only validation, a bounded copy, and a short lock.
// Request ids remain reserved after dequeue so a retried command cannot be
// applied twice during this App session.
class ControlCommandQueue {
 public:
  enum class EnqueueResult { kAccepted, kDuplicateRequestId, kFull };

  explicit ControlCommandQueue(std::size_t capacity = 64) : capacity_(capacity) {}

  EnqueueResult try_enqueue(ControlRequest request) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (queue_.size() >= capacity_) {
      return EnqueueResult::kFull;
    }

    if (request.kind == ControlRequest::Kind::kProtocolCommand && request.reserve_request_id) {
      const std::string& request_id = request.command.request_id();
      if (!request_ids_.insert(request_id).second) {
        return EnqueueResult::kDuplicateRequestId;
      }
    }

    queue_.push_back(std::move(request));
    return EnqueueResult::kAccepted;
  }

  bool try_dequeue(ControlRequest& request) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (queue_.empty()) {
      return false;
    }
    request = std::move(queue_.front());
    queue_.pop_front();
    return true;
  }

  std::size_t size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return queue_.size();
  }

 private:
  const std::size_t capacity_;
  mutable std::mutex mutex_;
  std::deque<ControlRequest> queue_;
  std::unordered_set<std::string> request_ids_;
};

}  // namespace scifi2_hub::control
