#include "control_protocol.hpp"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using scifi2_hub::protocol::CommandResult;
using scifi2_hub::protocol::ControlCommand;
using scifi2_hub::protocol::StateSnapshot;
using scifi2_hub::protocol::ValidationCode;
using namespace stateful_decode_and_sync::v1;

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

ControlCommand valid_prepare_command() {
  ControlCommand command;
  command.set_protocol_version(scifi2_hub::protocol::kProtocolVersion);
  command.set_request_id("test/1");
  command.set_command(COMMAND_PREPARE_CAPTURE);
  command.mutable_prepare_capture()->set_collection_id(0);
  command.mutable_prepare_capture()->set_label(2);
  command.mutable_prepare_capture()->set_enabled(true);
  return command;
}

StateSnapshot valid_state() {
  StateSnapshot state;
  state.set_protocol_version(scifi2_hub::protocol::kProtocolVersion);
  state.set_state_version(42);
  state.set_timestamp_ns(1234567890);
  state.mutable_pipeline()->set_state(PIPELINE_READY);
  state.mutable_pipeline()->set_source_mode(SOURCE_MODE_SAMPLING);
  state.mutable_active()->set_collection_id(0);
  state.mutable_active()->set_label(2);
  state.mutable_active()->set_capture_enabled(false);
  auto* collection = state.add_collections();
  collection->set_collection_id(0);
  collection->set_feature_dimension(256);
  collection->set_data_generation(17);
  for (std::uint32_t label = 0; label < 5; ++label) {
    auto* item = collection->add_labels();
    item->set_label(label);
    item->set_count(label == 2 ? 100 : 0);
    item->set_capacity(2000);
  }
  state.mutable_model()->set_phase(MODEL_SUCCEEDED);
  state.mutable_model()->set_ready(true);
  state.mutable_model()->set_has_source_collection_id(true);
  state.mutable_model()->set_source_collection_id(0);
  state.mutable_model()->set_source_generation(12);
  state.mutable_model()->set_stale(true);
  state.mutable_model()->set_epoch(100);
  state.mutable_model()->set_total_epochs(100);
  state.mutable_model()->set_loss(0.12f);
  state.mutable_model()->set_accuracy(0.98f);
  state.mutable_model()->set_duration_ms(4312);
  return state;
}

void test_command_round_trip_and_validation() {
  const ControlCommand command = valid_prepare_command();
  expect(static_cast<bool>(scifi2_hub::protocol::validate_command(command)), "valid command accepted");

  std::string encoded;
  expect(static_cast<bool>(scifi2_hub::protocol::serialize_command(command, encoded)),
         "valid command serialized");
  ControlCommand decoded;
  expect(static_cast<bool>(scifi2_hub::protocol::parse_command(encoded, decoded)),
         "serialized command parsed");
  expect(decoded.request_id() == command.request_id() &&
             decoded.prepare_capture().label() == command.prepare_capture().label(),
         "command fields survive round trip");

  const std::string truncated = encoded.substr(0, encoded.size() - 1);
  expect(!scifi2_hub::protocol::parse_command(truncated, decoded), "truncated command rejected");
  expect(decoded.request_id() == command.request_id(), "failed parse does not mutate output");
}

void test_command_rejects_version_range_and_shape_errors() {
  auto command = valid_prepare_command();
  command.set_protocol_version(2);
  auto result = scifi2_hub::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kUnsupportedVersion,
         "incompatible command version rejected");
  std::string encoded = "sentinel";
  expect(!static_cast<bool>(scifi2_hub::protocol::serialize_command(command, encoded)) && encoded.empty(),
         "invalid command is not serialized");

  command = valid_prepare_command();
  command.mutable_prepare_capture()->set_label(scifi2_hub::protocol::kMaxLabels);
  result = scifi2_hub::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kOutOfRange,
         "out of range label rejected");

  command = valid_prepare_command();
  command.set_command(COMMAND_SELECT_LABEL);
  result = scifi2_hub::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kIncompatiblePayload,
         "mismatched oneof payload rejected");

  command = valid_prepare_command();
  command.set_command(COMMAND_FLUSH);
  command.clear_prepare_capture();
  auto* flush = command.mutable_flush();
  flush->set_scope(Flush_Scope_SCOPE_ALL);
  flush->set_has_label(true);
  flush->set_label(0);
  result = scifi2_hub::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kIncompatiblePayload,
         "target on all flush rejected");
}

void test_state_and_result_validation() {
  auto state = valid_state();
  expect(static_cast<bool>(scifi2_hub::protocol::validate_state(state)), "valid state accepted");

  std::string encoded;
  expect(static_cast<bool>(scifi2_hub::protocol::serialize_state(state, encoded)),
         "valid state serialized");
  StateSnapshot decoded;
  expect(static_cast<bool>(scifi2_hub::protocol::parse_state(encoded, decoded)),
         "serialized state parsed");
  expect(decoded.collections(0).labels(2).count() == 100, "state counts survive round trip");

  state.mutable_collections(0)->mutable_labels(0)->set_count(2001);
  auto validation = scifi2_hub::protocol::validate_state(state);
  expect(!validation && validation.code == ValidationCode::kOutOfRange,
         "count greater than capacity rejected");

  state = valid_state();
  state.mutable_model()->set_loss(std::nanf(""));
  validation = scifi2_hub::protocol::validate_state(state);
  expect(!validation && validation.code == ValidationCode::kInvalidArgument,
         "non-finite model metric rejected");

  CommandResult result;
  result.set_protocol_version(scifi2_hub::protocol::kProtocolVersion);
  result.set_request_id("test/2");
  result.set_command(COMMAND_FIT);
  result.set_status(RESULT_FAILED);
  result.mutable_error()->set_code(ERROR_EMPTY_COLLECTION);
  result.mutable_error()->set_message("no captured windows");
  expect(static_cast<bool>(scifi2_hub::protocol::validate_command_result(result)),
         "failed result with error accepted");

  result.clear_error();
  expect(!scifi2_hub::protocol::validate_command_result(result),
         "failed result without error rejected");

  result.set_status(RESULT_ACCEPTED);
  auto* progress = result.mutable_progress();
  progress->set_epoch(1);
  progress->set_total_epochs(4);
  progress->set_loss(0.7f);
  progress->set_accuracy(0.5f);
  expect(static_cast<bool>(scifi2_hub::protocol::validate_command_result(result)),
         "fit progress result accepted");

  result.set_command(COMMAND_SET_CAPTURE);
  expect(!scifi2_hub::protocol::validate_command_result(result),
         "progress on non-fit result rejected");
}

void test_task_protocol_round_trip_and_validation() {
  ControlCommand command;
  command.set_protocol_version(scifi2_hub::protocol::kProtocolVersion);
  command.set_request_id("task/start/1");
  command.set_command(COMMAND_START_TASK);
  auto* preconditions = command.mutable_start_task()->mutable_preconditions();
  preconditions->set_expected_app_session_id("0123456789abcdef0123456789abcdef");
  preconditions->set_expected_run_sequence(0);
  preconditions->set_expected_transition_sequence(0);
  expect(static_cast<bool>(scifi2_hub::protocol::validate_command(command)),
         "valid task start command accepted");

  std::string encoded;
  expect(static_cast<bool>(scifi2_hub::protocol::serialize_command(command, encoded)),
         "task command serialized");
  ControlCommand decoded;
  expect(static_cast<bool>(scifi2_hub::protocol::parse_command(encoded, decoded)),
         "task command parsed");
  expect(decoded.has_start_task() &&
             decoded.start_task().preconditions().expected_app_session_id() ==
                 preconditions->expected_app_session_id(),
         "task preconditions survive round trip");

  command = decoded;
  command.mutable_start_task()->mutable_preconditions()->set_has_expected_state_id(true);
  command.mutable_start_task()->mutable_preconditions()->set_expected_state_id(0);
  expect(!scifi2_hub::protocol::validate_command(command), "zero expected task state rejected");

  stateful_decode_and_sync::v1::TaskTransitionEvent event;
  event.set_protocol_version(scifi2_hub::protocol::kProtocolVersion);
  event.set_definition_id("reach_task");
  event.set_definition_revision(1);
  event.set_definition_hash("sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef");
  event.set_app_session_id("0123456789abcdef0123456789abcdef");
  event.set_run_sequence(1);
  event.set_event_sequence(1);
  event.set_transition_sequence(1);
  event.set_event_kind(TASK_EVENT_START);
  event.set_trigger_kind(TASK_TRIGGER_START_COMMAND);
  event.mutable_effective_frame()->set_source_id("broadband.1");
  event.mutable_effective_frame()->set_sequence_number(42);
  event.mutable_effective_frame()->set_timestamp_ns(99);
  expect(static_cast<bool>(scifi2_hub::protocol::validate_task_transition_event(event)),
         "complete task transition event accepted");
}

}  // namespace

void test_raw_command_validation() {
  auto command = valid_prepare_command();
  command.set_command(COMMAND_EXO_RAW);
  expect(scifi2_hub::protocol::is_known_command(command.command()),
         "raw command supports correlated results and rejections");
  expect(!scifi2_hub::protocol::validate_command(command), "raw payload required");
  for (const auto& text : {std::string("version"), std::string("info;")}) {
    command.mutable_exo_raw()->set_command(text);
    std::string encoded;
    ControlCommand decoded;
    expect(static_cast<bool>(scifi2_hub::protocol::serialize_command(command, encoded)),
           "raw command serializes through protocol validator");
    expect(static_cast<bool>(scifi2_hub::protocol::parse_command(encoded, decoded)),
           "raw command passes incoming protocol validation");
    expect(decoded.exo_raw().command() == text, "raw text preserved");
  }
  for (const auto& text : {std::string(), std::string(" \t"), std::string("version\ninfo"),
                           std::string("version\rinfo"), std::string(201, 'x')}) {
    command.mutable_exo_raw()->set_command(text);
    expect(!scifi2_hub::protocol::validate_command(command), "malformed raw rejected");
  }
}

int main() {
  test_raw_command_validation();
  test_command_round_trip_and_validation();
  test_command_rejects_version_range_and_shape_errors();
  test_state_and_result_validation();
  test_task_protocol_round_trip_and_validation();
  std::cout << "PASS protocol serialization and validation\n";
  return 0;
}
