#include "control_command_queue.hpp"
#include "control_state.hpp"

#include <cstdlib>
#include <iostream>

namespace {

using app::control::ActiveTarget;
using app::control::ControlCommandQueue;
using app::control::ControlRequest;
using app::protocol::ControlCommand;

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

ControlCommand command(const char* request_id, std::uint32_t label) {
  ControlCommand result;
  result.set_protocol_version(app::protocol::kProtocolVersion);
  result.set_request_id(request_id);
  result.set_command(broadband_mode_switch::v1::COMMAND_SELECT_LABEL);
  result.mutable_select_label()->set_label(label);
  return result;
}

void test_queue_is_bounded_fifo_and_deduplicates() {
  ControlCommandQueue queue(2);
  expect(queue.try_enqueue(ControlRequest::protocol_command(command("one", 1))) ==
             ControlCommandQueue::EnqueueResult::kAccepted,
         "first command is queued");
  expect(queue.try_enqueue(ControlRequest::protocol_command(command("one", 2))) ==
             ControlCommandQueue::EnqueueResult::kDuplicateRequestId,
         "duplicate request id is rejected");
  expect(queue.try_enqueue(ControlRequest::protocol_command(command("two", 2))) ==
             ControlCommandQueue::EnqueueResult::kAccepted,
         "second command is queued");
  expect(queue.try_enqueue(ControlRequest::legacy_source_mode_request(1)) ==
             ControlCommandQueue::EnqueueResult::kFull,
         "queue rejects overflow without growing");

  ControlRequest first;
  ControlRequest second;
  expect(queue.try_dequeue(first) && queue.try_dequeue(second), "queued commands dequeue");
  expect(first.command.request_id() == "one" && second.command.request_id() == "two",
         "queue preserves FIFO order");
  expect(!queue.try_dequeue(first), "queue becomes empty");
}

void test_prepare_capture_is_one_atomic_transition() {
  ActiveTarget target{0, 1, false};
  const auto result = app::control::prepare_capture(target, 1, 3, true, 2, 4);
  expect(static_cast<bool>(result), "valid prepare_capture succeeds");
  expect(target.collection_id == 1 && target.label == 3 && target.capture_enabled,
         "prepare_capture replaces all target fields together");

  const ActiveTarget before = target;
  const auto invalid = app::control::prepare_capture(target, 2, 0, false, 2, 4);
  expect(!static_cast<bool>(invalid), "invalid prepare_capture is rejected");
  expect(target.collection_id == before.collection_id && target.label == before.label &&
             target.capture_enabled == before.capture_enabled,
         "invalid prepare_capture leaves every target field unchanged");
}

void test_selection_rejects_capture_and_invalid_targets_without_mutation() {
  ActiveTarget target{0, 1, true};
  const ActiveTarget before = target;
  const auto collection = app::control::select_collection(target, 1, 2, 4);
  expect(!static_cast<bool>(collection) &&
             collection.code == broadband_mode_switch::v1::ERROR_CAPTURE_ENABLED,
         "collection selection is rejected during capture");
  expect(target.collection_id == before.collection_id && target.label == before.label &&
             target.capture_enabled == before.capture_enabled,
         "rejected collection selection does not mutate target");

  target.capture_enabled = false;
  const auto label = app::control::select_label(target, 4, 4);
  expect(!static_cast<bool>(label), "out of range label selection is rejected");
  expect(target.collection_id == 0 && target.label == 1 && !target.capture_enabled,
         "invalid label selection leaves target unchanged");

  expect(static_cast<bool>(app::control::select_collection(target, 1, 2, 4)),
         "valid collection selection succeeds while disabled");
  expect(target.collection_id == 1 && target.label == 1 && !target.capture_enabled,
         "selection changes only the requested field");
}

}  // namespace

int main() {
  test_queue_is_bounded_fifo_and_deduplicates();
  test_prepare_capture_is_one_atomic_transition();
  test_selection_rejects_capture_and_invalid_targets_without_mutation();
  std::cout << "PASS control queue and atomic target transitions\n";
  return 0;
}
