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

namespace scifi2_hub::protocol {

using stateful_decode_and_sync::v1::ActiveTarget;
using stateful_decode_and_sync::v1::CollectionStatus;
using stateful_decode_and_sync::v1::CommandKind;
using stateful_decode_and_sync::v1::CommandResult;
using stateful_decode_and_sync::v1::ControlCommand;
using stateful_decode_and_sync::v1::Error;
using stateful_decode_and_sync::v1::ErrorCode;
using stateful_decode_and_sync::v1::FitProgress;
using stateful_decode_and_sync::v1::ModelPhase;
using stateful_decode_and_sync::v1::ModelStatus;
using stateful_decode_and_sync::v1::PipelineState;
using stateful_decode_and_sync::v1::PipelineStatus;
using stateful_decode_and_sync::v1::ResultStatus;
using stateful_decode_and_sync::v1::StateSnapshot;
using stateful_decode_and_sync::v1::TaskEventKind;
using stateful_decode_and_sync::v1::TaskFrameBoundary;
using stateful_decode_and_sync::v1::TaskLifecycle;
using stateful_decode_and_sync::v1::TaskPreconditions;
using stateful_decode_and_sync::v1::TaskStatus;
using stateful_decode_and_sync::v1::TaskTransitionEvent;
using stateful_decode_and_sync::v1::TaskTriggerKind;

constexpr std::uint32_t kProtocolVersion = 1;
constexpr std::size_t kMaxSerializedPayloadBytes = 64 * 1024;
constexpr std::size_t kMaxRequestIdLength = 128;
constexpr std::uint32_t kMaxCollections = 8;
constexpr std::uint32_t kMaxLabels = 32;
constexpr std::uint32_t kMaxCapacity = 10000;
constexpr std::uint32_t kMaxFeatureDimensions = 8192;
constexpr std::uint32_t kMaxFitEpochs = 10000;
constexpr std::size_t kMaxTaskNameLength = 64;
constexpr std::size_t kMaxTaskSessionIdLength = 128;
constexpr std::uint32_t kMaxTaskId = 65535;

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
    case stateful_decode_and_sync::v1::COMMAND_GET_STATE:
    case stateful_decode_and_sync::v1::COMMAND_SUBSCRIBE_STATE:
    case stateful_decode_and_sync::v1::COMMAND_PREPARE_CAPTURE:
    case stateful_decode_and_sync::v1::COMMAND_SELECT_COLLECTION:
    case stateful_decode_and_sync::v1::COMMAND_SELECT_LABEL:
    case stateful_decode_and_sync::v1::COMMAND_SET_CAPTURE:
    case stateful_decode_and_sync::v1::COMMAND_FIT:
    case stateful_decode_and_sync::v1::COMMAND_FLUSH:
    case stateful_decode_and_sync::v1::COMMAND_START_TASK:
    case stateful_decode_and_sync::v1::COMMAND_PROPOSE_TASK_EVENT:
    case stateful_decode_and_sync::v1::COMMAND_PROPOSE_TASK_TRANSITION:
    case stateful_decode_and_sync::v1::COMMAND_ABORT_TASK:
    case stateful_decode_and_sync::v1::COMMAND_RESET_TASK:
    case stateful_decode_and_sync::v1::COMMAND_SET_EXO_MODE:
    case stateful_decode_and_sync::v1::COMMAND_SET_EXO_POSE:
      return true;
    default:
      return false;
  }
}

constexpr std::int32_t kExoJointValueMax = 100;
constexpr int kMaxExoPoseJoints = 6;

inline bool is_known_exo_mode(stateful_decode_and_sync::v1::ExoMode mode) {
  using namespace stateful_decode_and_sync::v1;
  return mode == EXO_MODE_OFF || mode == EXO_MODE_EXTERNAL || mode == EXO_MODE_DECODE;
}

inline bool is_known_exo_joint(stateful_decode_and_sync::v1::ExoJoint joint) {
  using namespace stateful_decode_and_sync::v1;
  return joint == EXO_JOINT_THUMB || joint == EXO_JOINT_INDEX || joint == EXO_JOINT_MIDDLE ||
         joint == EXO_JOINT_RING || joint == EXO_JOINT_PINKY || joint == EXO_JOINT_WRIST;
}

inline ValidationResult validate_set_exo_pose(
    const stateful_decode_and_sync::v1::SetExoPose& pose) {
  if (pose.joints_size() == 0 || pose.joints_size() > kMaxExoPoseJoints) {
    return invalid_result(ValidationCode::kInvalidArgument, "set_exo_pose.joints",
                          "a pose must name between one and six joints");
  }
  std::unordered_set<int> seen;
  for (const auto& joint_value : pose.joints()) {
    if (!is_known_exo_joint(joint_value.joint())) {
      return invalid_result(ValidationCode::kInvalidArgument, "set_exo_pose.joints.joint",
                            "unknown or unspecified joint");
    }
    if (!seen.insert(static_cast<int>(joint_value.joint())).second) {
      return invalid_result(ValidationCode::kInvalidArgument, "set_exo_pose.joints",
                            "each joint may appear at most once");
    }
    if (joint_value.value() < -kExoJointValueMax || joint_value.value() > kExoJointValueMax) {
      return invalid_result(ValidationCode::kOutOfRange, "set_exo_pose.joints.value",
                            "joint value must be in [-100, 100]");
    }
  }
  return valid_result();
}

inline bool is_known_error(ErrorCode code) {
  return static_cast<int>(code) >= static_cast<int>(stateful_decode_and_sync::v1::ERROR_NONE) &&
         static_cast<int>(code) <= static_cast<int>(stateful_decode_and_sync::v1::ERROR_INTERNAL);
}

inline bool is_known_result_status(ResultStatus status) {
  return status == stateful_decode_and_sync::v1::RESULT_ACCEPTED ||
         status == stateful_decode_and_sync::v1::RESULT_SUCCEEDED ||
         status == stateful_decode_and_sync::v1::RESULT_FAILED;
}

inline bool is_known_pipeline_state(PipelineState state) {
  return state == stateful_decode_and_sync::v1::PIPELINE_DISCONNECTED ||
         state == stateful_decode_and_sync::v1::PIPELINE_NOT_READY ||
         state == stateful_decode_and_sync::v1::PIPELINE_READY ||
         state == stateful_decode_and_sync::v1::PIPELINE_ERROR;
}

inline bool is_known_source_mode(stateful_decode_and_sync::v1::SourceMode mode) {
  return mode == stateful_decode_and_sync::v1::SOURCE_MODE_SAMPLING ||
         mode == stateful_decode_and_sync::v1::SOURCE_MODE_SYNTHETIC;
}

inline bool is_known_model_phase(ModelPhase phase) {
  return phase == stateful_decode_and_sync::v1::MODEL_IDLE ||
         phase == stateful_decode_and_sync::v1::MODEL_QUEUED ||
         phase == stateful_decode_and_sync::v1::MODEL_RUNNING ||
         phase == stateful_decode_and_sync::v1::MODEL_SUCCEEDED ||
         phase == stateful_decode_and_sync::v1::MODEL_FAILED ||
         phase == stateful_decode_and_sync::v1::MODEL_CANCELLED;
}

inline bool is_known_task_lifecycle(TaskLifecycle lifecycle) {
  using namespace stateful_decode_and_sync::v1;
  return lifecycle == TASK_LIFECYCLE_IDLE || lifecycle == TASK_LIFECYCLE_RUNNING ||
         lifecycle == TASK_LIFECYCLE_COMPLETED || lifecycle == TASK_LIFECYCLE_ABORTED ||
         lifecycle == TASK_LIFECYCLE_FAULT;
}

inline bool is_known_task_event_kind(TaskEventKind kind) {
  using namespace stateful_decode_and_sync::v1;
  return kind == TASK_EVENT_START || kind == TASK_EVENT_TRANSITION ||
         kind == TASK_EVENT_ABORT || kind == TASK_EVENT_RESET;
}

inline bool is_known_task_trigger_kind(TaskTriggerKind kind) {
  using namespace stateful_decode_and_sync::v1;
  return kind == TASK_TRIGGER_START_COMMAND || kind == TASK_TRIGGER_EXTERNAL_EVENT ||
         kind == TASK_TRIGGER_SOURCE_TIMEOUT || kind == TASK_TRIGGER_DECODER_PREDICATE ||
         kind == TASK_TRIGGER_ABORT_COMMAND || kind == TASK_TRIGGER_RESET_COMMAND;
}

inline bool is_task_name(std::string_view value) {
  if (value.empty() || value.size() > kMaxTaskNameLength) return false;
  const auto alpha = [](char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); };
  const auto rest = [alpha](char c) {
    return alpha(c) || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-';
  };
  if (!alpha(value.front())) return false;
  for (char c : value) {
    if (!rest(c)) return false;
  }
  return true;
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

inline ValidationResult validate_flush(const stateful_decode_and_sync::v1::Flush& flush) {
  switch (flush.scope()) {
    case stateful_decode_and_sync::v1::Flush_Scope_SCOPE_LABEL:
      if (flush.has_collection_id() && flush.collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.collection_id",
                              "collection_id is outside the v1 bound");
      }
      if (flush.has_label() && flush.label() >= kMaxLabels) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.label",
                              "label is outside the v1 bound");
      }
      return valid_result();
    case stateful_decode_and_sync::v1::Flush_Scope_SCOPE_COLLECTION:
      if (flush.has_label()) {
        return invalid_result(ValidationCode::kIncompatiblePayload, "flush.label",
                              "label is not valid for a collection flush");
      }
      if (flush.has_collection_id() && flush.collection_id() >= kMaxCollections) {
        return invalid_result(ValidationCode::kOutOfRange, "flush.collection_id",
                              "collection_id is outside the v1 bound");
      }
      return valid_result();
    case stateful_decode_and_sync::v1::Flush_Scope_SCOPE_ALL:
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

inline ValidationResult validate_task_preconditions(const TaskPreconditions& preconditions) {
  if (preconditions.expected_app_session_id().empty() ||
      preconditions.expected_app_session_id().size() > kMaxTaskSessionIdLength) {
    return invalid_result(ValidationCode::kInvalidArgument, "task.preconditions.app_session_id",
                          "expected_app_session_id must be non-empty and bounded");
  }
  if (preconditions.has_expected_state_id() &&
      (preconditions.expected_state_id() == 0 ||
       preconditions.expected_state_id() > kMaxTaskId)) {
    return invalid_result(ValidationCode::kOutOfRange, "task.preconditions.state_id",
                          "expected_state_id must be in [1, 65535] when present");
  }
  return valid_result();
}

inline ValidationResult validate_command(const ControlCommand& command) {
  auto envelope = validate_envelope(command.protocol_version(), command.request_id());
  if (!envelope) return envelope;
  if (!is_known_command(command.command())) {
    return invalid_result(ValidationCode::kUnknownCommand, "command", "unknown command kind");
  }

  using namespace stateful_decode_and_sync::v1;
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
    case COMMAND_START_TASK:
      if (!command.has_start_task()) break;
      return validate_task_preconditions(command.start_task().preconditions());
    case COMMAND_PROPOSE_TASK_EVENT:
      if (!command.has_propose_task_event()) break;
      if (!is_task_name(command.propose_task_event().event_name())) {
        return invalid_result(ValidationCode::kInvalidArgument, "propose_task_event.event_name",
                              "event_name must match the bounded task-name syntax");
      }
      return validate_task_preconditions(command.propose_task_event().preconditions());
    case COMMAND_PROPOSE_TASK_TRANSITION:
      if (!command.has_propose_task_transition()) break;
      if (command.propose_task_transition().transition_id() == 0 ||
          command.propose_task_transition().transition_id() > kMaxTaskId) {
        return invalid_result(ValidationCode::kOutOfRange,
                              "propose_task_transition.transition_id",
                              "transition_id must be in [1, 65535]");
      }
      return validate_task_preconditions(command.propose_task_transition().preconditions());
    case COMMAND_ABORT_TASK:
      if (!command.has_abort_task()) break;
      return validate_task_preconditions(command.abort_task().preconditions());
    case COMMAND_RESET_TASK:
      if (!command.has_reset_task()) break;
      return validate_task_preconditions(command.reset_task().preconditions());
    case COMMAND_SET_EXO_MODE:
      if (!command.has_set_exo_mode()) break;
      if (!is_known_exo_mode(command.set_exo_mode().mode())) {
        return invalid_result(ValidationCode::kInvalidArgument, "set_exo_mode.mode",
                              "unknown or unspecified exo mode");
      }
      return valid_result();
    case COMMAND_SET_EXO_POSE:
      if (!command.has_set_exo_pose()) break;
      return validate_set_exo_pose(command.set_exo_pose());
    default:
      break;
  }
  return invalid_result(ValidationCode::kIncompatiblePayload, "payload",
                        "payload does not match command kind");
}

inline ValidationResult validate_error(const Error& error) {
  if (!is_known_error(error.code()) || error.code() == stateful_decode_and_sync::v1::ERROR_NONE) {
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
  if (result.status() == stateful_decode_and_sync::v1::RESULT_FAILED) {
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
  if (result.has_progress()) {
    if (result.command() != stateful_decode_and_sync::v1::COMMAND_FIT) {
      return invalid_result(ValidationCode::kIncompatiblePayload, "progress",
                            "progress is only valid for fit results");
    }
    return validate_progress(result.progress());
  }
  return valid_result();
}

inline ValidationResult validate_label_status(
    const stateful_decode_and_sync::v1::LabelStatus& label, std::unordered_set<std::uint32_t>& seen) {
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
  if (state.has_last_error()) {
    auto error = validate_error(state.last_error());
    if (!error) return error;
  }
  if (state.has_task()) {
    const auto& task = state.task();
    if (!task.configured()) {
      return valid_result();
    }
    if (!is_task_name(task.definition_id()) || task.definition_revision() == 0 ||
        task.definition_hash().size() != 71 ||
        task.app_session_id().empty() || task.app_session_id().size() > kMaxTaskSessionIdLength ||
        !is_known_task_lifecycle(task.lifecycle()) ||
        task.staged_command_count() > 64) {
      return invalid_result(ValidationCode::kInvalidArgument, "task",
                            "configured task status is incomplete or invalid");
    }
    if (task.has_current_state_id() &&
        (task.current_state_id() == 0 || task.current_state_id() > kMaxTaskId)) {
      return invalid_result(ValidationCode::kOutOfRange, "task.current_state_id",
                            "current state id must be in [1, 65535] when present");
    }
    if (task.has_effective_frame() && task.last_effective_frame().source_id().empty()) {
      return invalid_result(ValidationCode::kInvalidArgument, "task.last_effective_frame",
                            "effective frame must name its reference source");
    }
  }
  return valid_result();
}

inline ValidationResult validate_task_transition_event(const TaskTransitionEvent& event) {
  if (event.protocol_version() != kProtocolVersion || !is_task_name(event.definition_id()) ||
      event.definition_revision() == 0 || event.definition_hash().size() != 71 ||
      event.app_session_id().empty() || event.app_session_id().size() > kMaxTaskSessionIdLength ||
      event.run_sequence() == 0 || event.event_sequence() == 0 ||
      event.transition_sequence() == 0 || !is_known_task_event_kind(event.event_kind()) ||
      !is_known_task_trigger_kind(event.trigger_kind()) || !event.has_effective_frame() ||
      event.effective_frame().source_id().empty()) {
    return invalid_result(ValidationCode::kInvalidArgument, "task_transition",
                          "task transition event is incomplete or invalid");
  }
  if (event.previous_state_id() > kMaxTaskId || event.current_state_id() > kMaxTaskId ||
      event.transition_id() > kMaxTaskId) {
    return invalid_result(ValidationCode::kOutOfRange, "task_transition.state_or_transition_id",
                          "task id is outside [0, 65535]");
  }
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

inline ValidationResult serialize_task_transition_event(const TaskTransitionEvent& event,
                                                        std::string& output) {
  return serialize_validated(event, output, validate_task_transition_event(event));
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

}  // namespace scifi2_hub::protocol
