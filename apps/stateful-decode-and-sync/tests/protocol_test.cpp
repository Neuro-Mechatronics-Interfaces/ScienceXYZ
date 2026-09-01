#include "control_protocol.hpp"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <string>

namespace {

using app::protocol::CommandResult;
using app::protocol::ControlCommand;
using app::protocol::StateSnapshot;
using app::protocol::ValidationCode;
using namespace stateful_decode_and_sync::v1;

void expect(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL " << message << '\n';
    std::exit(1);
  }
}

ControlCommand valid_prepare_command() {
  ControlCommand command;
  command.set_protocol_version(app::protocol::kProtocolVersion);
  command.set_request_id("test/1");
  command.set_command(COMMAND_PREPARE_CAPTURE);
  command.mutable_prepare_capture()->set_collection_id(0);
  command.mutable_prepare_capture()->set_label(2);
  command.mutable_prepare_capture()->set_enabled(true);
  return command;
}

StateSnapshot valid_state() {
  StateSnapshot state;
  state.set_protocol_version(app::protocol::kProtocolVersion);
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
  expect(static_cast<bool>(app::protocol::validate_command(command)), "valid command accepted");

  std::string encoded;
  expect(static_cast<bool>(app::protocol::serialize_command(command, encoded)),
         "valid command serialized");
  ControlCommand decoded;
  expect(static_cast<bool>(app::protocol::parse_command(encoded, decoded)),
         "serialized command parsed");
  expect(decoded.request_id() == command.request_id() &&
             decoded.prepare_capture().label() == command.prepare_capture().label(),
         "command fields survive round trip");

  const std::string truncated = encoded.substr(0, encoded.size() - 1);
  expect(!app::protocol::parse_command(truncated, decoded), "truncated command rejected");
  expect(decoded.request_id() == command.request_id(), "failed parse does not mutate output");
}

void test_command_rejects_version_range_and_shape_errors() {
  auto command = valid_prepare_command();
  command.set_protocol_version(2);
  auto result = app::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kUnsupportedVersion,
         "incompatible command version rejected");
  std::string encoded = "sentinel";
  expect(!static_cast<bool>(app::protocol::serialize_command(command, encoded)) && encoded.empty(),
         "invalid command is not serialized");

  command = valid_prepare_command();
  command.mutable_prepare_capture()->set_label(app::protocol::kMaxLabels);
  result = app::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kOutOfRange,
         "out of range label rejected");

  command = valid_prepare_command();
  command.set_command(COMMAND_SELECT_LABEL);
  result = app::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kIncompatiblePayload,
         "mismatched oneof payload rejected");

  command = valid_prepare_command();
  command.set_command(COMMAND_FLUSH);
  command.clear_prepare_capture();
  auto* flush = command.mutable_flush();
  flush->set_scope(Flush_Scope_SCOPE_ALL);
  flush->set_has_label(true);
  flush->set_label(0);
  result = app::protocol::validate_command(command);
  expect(!result && result.code == ValidationCode::kIncompatiblePayload,
         "target on all flush rejected");
}

void test_state_and_result_validation() {
  auto state = valid_state();
  expect(static_cast<bool>(app::protocol::validate_state(state)), "valid state accepted");

  std::string encoded;
  expect(static_cast<bool>(app::protocol::serialize_state(state, encoded)),
         "valid state serialized");
  StateSnapshot decoded;
  expect(static_cast<bool>(app::protocol::parse_state(encoded, decoded)),
         "serialized state parsed");
  expect(decoded.collections(0).labels(2).count() == 100, "state counts survive round trip");

  state.mutable_collections(0)->mutable_labels(0)->set_count(2001);
  auto validation = app::protocol::validate_state(state);
  expect(!validation && validation.code == ValidationCode::kOutOfRange,
         "count greater than capacity rejected");

  state = valid_state();
  state.mutable_model()->set_loss(std::nanf(""));
  validation = app::protocol::validate_state(state);
  expect(!validation && validation.code == ValidationCode::kInvalidArgument,
         "non-finite model metric rejected");

  CommandResult result;
  result.set_protocol_version(app::protocol::kProtocolVersion);
  result.set_request_id("test/2");
  result.set_command(COMMAND_FIT);
  result.set_status(RESULT_FAILED);
  result.mutable_error()->set_code(ERROR_EMPTY_COLLECTION);
  result.mutable_error()->set_message("no captured windows");
  expect(static_cast<bool>(app::protocol::validate_command_result(result)),
         "failed result with error accepted");

  result.clear_error();
  expect(!app::protocol::validate_command_result(result),
         "failed result without error rejected");

  result.set_status(RESULT_ACCEPTED);
  auto* progress = result.mutable_progress();
  progress->set_epoch(1);
  progress->set_total_epochs(4);
  progress->set_loss(0.7f);
  progress->set_accuracy(0.5f);
  expect(static_cast<bool>(app::protocol::validate_command_result(result)),
         "fit progress result accepted");

  result.set_command(COMMAND_SET_CAPTURE);
  expect(!app::protocol::validate_command_result(result),
         "progress on non-fit result rejected");
}

}  // namespace

int main() {
  test_command_round_trip_and_validation();
  test_command_rejects_version_range_and_shape_errors();
  test_state_and_result_validation();
  std::cout << "PASS protocol serialization and validation\n";
  return 0;
}
