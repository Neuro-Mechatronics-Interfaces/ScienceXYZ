#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <string>
#include <string_view>
#include <unordered_set>
#include <utility>

#include "gui_control.pb.h"

namespace app::protocol {

using broadband_mode_switch::v1::ActiveTarget;
using broadband_mode_switch::v1::CollectionStatus;
using broadband_mode_switch::v1::CommandKind;
using broadband_mode_switch::v1::CommandResult;
using broadband_mode_switch::v1::ControlCommand;
using broadband_mode_switch::v1::Error;
using broadband_mode_switch::v1::ErrorCode;
using broadband_mode_switch::v1::FitProgress;
using broadband_mode_switch::v1::ModelPhase;
using broadband_mode_switch::v1::ModelStatus;
using broadband_mode_switch::v1::PipelineState;
using broadband_mode_switch::v1::PipelineStatus;
using broadband_mode_switch::v1::ResultStatus;
using broadband_mode_switch::v1::StateSnapshot;

constexpr std::uint32_t kProtocolVersion = 1;
constexpr std::size_t kMaxSerializedPayloadBytes = 64 * 1024;
constexpr std::size_t kMaxRequestIdLength = 128;
constexpr std::uint32_t kMaxCollections = 8;
constexpr std::uint32_t kMaxLabels = 32;
constexpr std::uint32_t kMaxCapacity = 10000;
constexpr std::uint32_t kMaxFeatureDimensions = 8192;
constexpr std::uint32_t kMaxFitEpochs = 10000;

enum class ValidationCode {
  kNone,
  kMalformed,
  kUnsupportedVersion,
  kUnknownCommand,
  kInvalidArgument,
  kOutOfRange,
  kIncompatiblePayload,
};

struct ValidationResult {
  bool valid = false;
  ValidationCode code = ValidationCode::kNone;
  std::string field;
  std::string message;

  explicit operator bool() const { return valid; }
};

inline ValidationResult valid_result() { return {true, ValidationCode::kNone, {}, {}}; }

inline ValidationResult invalid_result(ValidationCode code, std::string field,
                                       std::string message) {
  return {false, code, std::move(field), std::move(message)};
}

inline bool is_known_command(CommandKind command) {
  switch (command) {
    case broadband_mode_switch::v1::COMMAND_GET_STATE:
    case broadband_mode_switch::v1::COMMAND_SUBSCRIBE_STATE:
    case broadband_mode_switch::v1::COMMAND_PREPARE_CAPTURE:
    case broadband_mode_switch::v1::COMMAND_SELECT_COLLECTION:
    case broadband_mode_switch::v1::COMMAND_SELECT_LABEL:
    case broadband_mode_switch::v1::COMMAND_SET_CAPTURE:
    case broadband_mode_switch::v1::COMMAND_FIT:
    case broadband_mode_switch::v1::COMMAND_FLUSH:
      return true;
    default:
      return false;
  }
}

inline bool is_known_error(ErrorCode code) {
  return static_cast<int>(code) >= static_cast<int>(broadband_mode_switch::v1::ERROR_NONE) &&
         static_cast<int>(code) <= static_cast<int>(broadband_mode_switch::v1::ERROR_INTERNAL);
}

inline bool is_known_result_status(ResultStatus status) {
  return status == broadband_mode_switch::v1::RESULT_ACCEPTED ||
         status == broadband_mode_switch::v1::RESULT_SUCCEEDED ||
         status == broadband_mode_switch::v1::RESULT_FAILED;
}

inline bool is_known_pipeline_state(PipelineState state) {
  return state == broadband_mode_switch::v1::PIPELINE_DISCONNECTED ||
         state == broadband_mode_switch::v1::PIPELINE_NOT_READY ||
         state == broadband_mode_switch::v1::PIPELINE_READY ||
         state == broadband_mode_switch::v1::PIPELINE_ERROR;
}

inline bool is_known_source_mode(broadband_mode_switch::v1::SourceMode mode) {
  return mode == broadband_mode_switch::v1::SOURCE_MODE_SAMPLING ||
         mode == broadband_mode_switch::v1::SOURCE_MODE_SYNTHETIC;
}

inline bool is_known_model_phase(ModelPhase phase) {
  return phase == broadband_mode_switch::v1::MODEL_IDLE ||
         phase == broadband_mode_switch::v1::MODEL_QUEUED ||
         phase == broadband_mode_switch::v1::MODEL_RUNNING ||
         phase == broadband_mode_switch::v1::MODEL_SUCCEEDED ||
         phase == broadband_mode_switch::v1::MODEL_FAILED ||
         phase == broadband_mode_switch::v1::MODEL_CANCELLED;
}

inline bool is_finite_nonnegative(float value) {
  return std::isfinite(value) && value >= 0.0f;
}

inline ValidationResult validate_envelope(std::uint32_t version, std::string_view request_id) {
  if (version != kProtocolVersion) {
    return invalid_result(ValidationCode::kUnsupportedVersion, "protocol_version",
                          "unsupported protocol version");
  }
  if (request_id.empty()) {
    return invalid_result(ValidationCode::kInvalidArgument, "request_id",
                          "request_id must not be empty");
  }
  if (request_id.size() > kMaxRequestIdLength) {
    return invalid_result(ValidationCode::kOutOfRange, "request_id",
                          "request_id exceeds the v1 length limit");
  }
  return valid_result();
}

inline ValidationResult validate_flush(const broadband_mode_switch::v1::Flush& flush) {
  switch (flush.scope()) {
    case broadband_mode_switch::v1::Flush_Scope_SCOPE_LABEL:
      if (flush.has_collection_id() && flush.collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.collection_id",
                              "collection_id is outside the v1 bound");
      }
      if (flush.has_label() && flush.label() >= kMaxLabels) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.label",
                              "label is outside the v1 bound");
      }
      return valid_result();
    case broadband_mode_switch::v1::Flush_Scope_SCOPE_COLLECTION:
      if (flush.has_label()) {
        return invalid_result(ValidationCode::kIncompatiblePayload, "flush.label",
                              "label is not valid for a collection flush");
      }
      if (flush.has_collection_id() && flush.collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.collection_id",
                              "collection_id is outside the v1 bound");
      }
      return valid_result();
    case broadband_mode_switch::v1::Flush_Scope_SCOPE_ALL:
      if (flush.has_collection_id() || flush.has_label()) {
        return invalid_result(ValidationCode::kIncompatiblePayload, "flush",
                              "all flush does not accept a target");
      }
      return valid_result();
    default:
      return invalid_result(ValidationCode::kInvalidArgument, "flush.scope",
                            "unknown or unspecified flush scope");
  }
}

inline ValidationResult validate_command(const ControlCommand& command) {
  auto envelope = validate_envelope(command.protocol_version(), command.request_id());
  if (!envelope) return envelope;
  if (!is_known_command(command.command())) {
    return invalid_result(ValidationCode::kUnknownCommand, "command", "unknown command kind");
  }

  using namespace broadband_mode_switch::v1;
  switch (command.command()) {
    case COMMAND_GET_STATE:
      if (!command.has_get_state()) break;
      return valid_result();
    case COMMAND_SUBSCRIBE_STATE:
      if (!command.has_subscribe_state()) break;
      return valid_result();
    case COMMAND_PREPARE_CAPTURE:
      if (!command.has_prepare_capture()) break;
      if (command.prepare_capture().collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "prepare_capture.collection_id",
                              "collection_id is outside the v1 bound");
      }
      if (command.prepare_capture().label() >= kMaxLabels) {
        return invalid_result(ValidationCode::kOutOfRange, "prepare_capture.label",
                              "label is outside the v1 bound");
      }
      return valid_result();
    case COMMAND_SELECT_COLLECTION:
      if (!command.has_select_collection()) break;
      if (command.select_collection().collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "select_collection.collection_id",
                              "collection_id is outside the v1 bound");
      }
      return valid_result();
    case COMMAND_SELECT_LABEL:
      if (!command.has_select_label()) break;
      if (command.select_label().label() >= kMaxLabels) {
        return invalid_result(ValidationCode::kOutOfRange, "select_label.label",
                              "label is outside the v1 bound");
      }
      return valid_result();
    case COMMAND_SET_CAPTURE:
      if (!command.has_set_capture()) break;
      return valid_result();
    case COMMAND_FIT:
      if (!command.has_fit()) break;
      if (command.fit().epochs() > kMaxFitEpochs) {
        return invalid_result(ValidationCode::kOutOfRange, "fit.epochs",
                              "epochs exceeds the v1 bound");
      }
      return valid_result();
    case COMMAND_FLUSH:
      if (!command.has_flush()) break;
      return validate_flush(command.flush());
    default:
      break;
  }
  return invalid_result(ValidationCode::kIncompatiblePayload, "payload",
                        "payload does not match command kind");
}

inline ValidationResult validate_error(const Error& error) {
  if (!is_known_error(error.code()) || error.code() == broadband_mode_switch::v1::ERROR_NONE) {
    return invalid_result(ValidationCode::kInvalidArgument, "error.code",
                          "error code is missing or unknown");
  }
  if (error.message().empty()) {
    return invalid_result(ValidationCode::kInvalidArgument, "error.message",
                          "error message must not be empty");
  }
  return valid_result();
}

inline ValidationResult validate_progress(const FitProgress& progress) {
  if (progress.total_epochs() == 0 || progress.total_epochs() > kMaxFitEpochs) {
    return invalid_result(ValidationCode::kOutOfRange, "progress.total_epochs",
                          "total_epochs is outside the v1 bound");
  }
  if (progress.epoch() > progress.total_epochs()) {
    return invalid_result(ValidationCode::kOutOfRange, "progress.epoch",
                          "epoch exceeds total_epochs");
  }
  if (!is_finite_nonnegative(progress.loss()) || !std::isfinite(progress.accuracy()) ||
      progress.accuracy() < 0.0f || progress.accuracy() > 1.0f) {
    return invalid_result(ValidationCode::kInvalidArgument, "progress",
                          "progress metrics are not finite or are out of range");
  }
  return valid_result();
}

inline ValidationResult validate_command_result(const CommandResult& result) {
  auto envelope = validate_envelope(result.protocol_version(), result.request_id());
  if (!envelope) return envelope;
  if (!is_known_command(result.command())) {
    return invalid_result(ValidationCode::kUnknownCommand, "command", "unknown command kind");
  }
  if (!is_known_result_status(result.status())) {
    return invalid_result(ValidationCode::kInvalidArgument, "status",
                          "status is missing or unknown");
  }
  if (result.status() == broadband_mode_switch::v1::RESULT_FAILED) {
    if (!result.has_error()) {
      return invalid_result(ValidationCode::kInvalidArgument, "error",
                            "failed result must include an error");
    }
    auto error = validate_error(result.error());
    if (!error) return error;
  } else if (result.has_error()) {
    return invalid_result(ValidationCode::kIncompatiblePayload, "error",
                          "successful result must not include an error");
  }
  if (result.has_progress()) return validate_progress(result.progress());
  return valid_result();
}

inline ValidationResult validate_label_status(
    const broadband_mode_switch::v1::LabelStatus& label, std::unordered_set<std::uint32_t>& seen) {
  if (label.label() >= kMaxLabels) {
    return invalid_result(ValidationCode::kOutOfRange, "collections.labels.label",
                          "label is outside the v1 bound");
  }
  if (!seen.insert(label.label()).second) {
    return invalid_result(ValidationCode::kInvalidArgument, "collections.labels",
                          "label ids must be unique within a collection");
  }
  if (label.capacity() == 0 || label.capacity() > kMaxCapacity ||
      label.count() > label.capacity()) {
    return invalid_result(ValidationCode::kOutOfRange, "collections.labels.capacity",
                          "label count/capacity is invalid");
  }
  return valid_result();
}

inline ValidationResult validate_state(const StateSnapshot& state) {
  if (state.protocol_version() != kProtocolVersion) {
    return invalid_result(ValidationCode::kUnsupportedVersion, "protocol_version",
                          "unsupported protocol version");
  }
  if (!state.has_pipeline() || !is_known_pipeline_state(state.pipeline().state()) ||
      !is_known_source_mode(state.pipeline().source_mode())) {
    return invalid_result(ValidationCode::kInvalidArgument, "pipeline",
                          "pipeline state or source mode is missing or unknown");
  }
  if (!state.has_active() || !state.has_model()) {
    return invalid_result(ValidationCode::kInvalidArgument, "state",
                          "complete state requires active and model sections");
  }
  if (state.active().collection_id() >= kMaxCollections ||
      state.active().label() >= kMaxLabels) {
    return invalid_result(ValidationCode::kOutOfRange, "active",
                          "active target is outside the v1 bound");
  }
  if (state.collections_size() > static_cast<int>(kMaxCollections)) {
    return invalid_result(ValidationCode::kOutOfRange, "collections",
                          "too many collections");
  }
  std::unordered_set<std::uint32_t> collections;
  bool active_collection_found = false;
  bool active_label_found = false;
  for (const auto& collection : state.collections()) {
    if (collection.collection_id() >= kMaxCollections ||
        !collections.insert(collection.collection_id()).second) {
      return invalid_result(ValidationCode::kInvalidArgument, "collections.collection_id",
                            "collection ids must be unique and within the v1 bound");
    }
    if (collection.feature_dimension() == 0 ||
        collection.feature_dimension() > kMaxFeatureDimensions) {
      return invalid_result(ValidationCode::kOutOfRange, "collections.feature_dimension",
                            "feature dimension is outside the v1 bound");
    }
    std::unordered_set<std::uint32_t> labels;
    for (const auto& label : collection.labels()) {
      auto status = validate_label_status(label, labels);
      if (!status) return status;
      if (collection.collection_id() == state.active().collection_id()) {
        active_collection_found = true;
        if (label.label() == state.active().label()) active_label_found = true;
      }
    }
  }
  if (!state.collections().empty() && (!active_collection_found || !active_label_found)) {
    return invalid_result(ValidationCode::kInvalidArgument, "active",
                          "active collection and label must exist in state");
  }

  const auto& model = state.model();
  if (!is_known_model_phase(model.phase())) {
    return invalid_result(ValidationCode::kInvalidArgument, "model.phase",
                          "model phase is missing or unknown");
  }
  if (model.total_epochs() > kMaxFitEpochs || model.epoch() > model.total_epochs()) {
    return invalid_result(ValidationCode::kOutOfRange, "model.epoch",
                          "model epoch progress is invalid");
  }
  if (!is_finite_nonnegative(model.loss()) || !std::isfinite(model.accuracy()) ||
      model.accuracy() < 0.0f || model.accuracy() > 1.0f) {
    return invalid_result(ValidationCode::kInvalidArgument, "model.metrics",
                          "model metrics are not finite or are out of range");
  }
  if (model.has_source_collection_id() && model.source_collection_id() >= kMaxCollections) {
    return invalid_result(ValidationCode::kOutOfRange, "model.source_collection_id",
                          "source collection is outside the v1 bound");
  }
  if (state.has_last_error()) return validate_error(state.last_error());
  return valid_result();
}

template <typename Message>
inline ValidationResult serialize_validated(const Message& message, std::string& output,
                                            ValidationResult validation) {
  output.clear();
  if (!validation) return validation;
  if (!message.SerializeToString(&output) || output.size() > kMaxSerializedPayloadBytes) {
    output.clear();
    return invalid_result(ValidationCode::kMalformed, "payload",
                          "payload could not be serialized within the v1 limit");
  }
  return valid_result();
}

inline ValidationResult serialize_command(const ControlCommand& command, std::string& output) {
  return serialize_validated(command, output, validate_command(command));
}

inline ValidationResult serialize_command_result(const CommandResult& result, std::string& output) {
  return serialize_validated(result, output, validate_command_result(result));
}

inline ValidationResult serialize_state(const StateSnapshot& state, std::string& output) {
  return serialize_validated(state, output, validate_state(state));
}

template <typename Message, typename Validator>
inline ValidationResult parse_validated(std::string_view input, Message& output, Validator validator) {
  if (input.empty() || input.size() > kMaxSerializedPayloadBytes) {
    return invalid_result(ValidationCode::kMalformed, "payload",
                          "serialized payload is empty or exceeds the v1 limit");
  }
  Message parsed;
  if (!parsed.ParseFromArray(input.data(), static_cast<int>(input.size()))) {
    return invalid_result(ValidationCode::kMalformed, "payload",
                          "serialized protobuf payload is malformed");
  }
  auto validation = validator(parsed);
  if (!validation) return validation;
  output = std::move(parsed);
  return valid_result();
}

inline ValidationResult parse_command(std::string_view input, ControlCommand& output) {
  return parse_validated(input, output, validate_command);
}

inline ValidationResult parse_command_result(std::string_view input, CommandResult& output) {
  return parse_validated(input, output, validate_command_result);
}

inline ValidationResult parse_state(std::string_view input, StateSnapshot& output) {
  return parse_validated(input, output, validate_state);
}

}  // namespace app::protocol
