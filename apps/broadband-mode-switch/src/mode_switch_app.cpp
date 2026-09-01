#include "mode_switch_app.hpp"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <thread>
#include <utility>

namespace app {

// Upstream broadband source node id (see config JSON).
static constexpr uint32_t kBroadbandNodeId = 1;

control::TransitionResult storage_failure(const CollectionStore::OperationResult& status) {
  using broadband_mode_switch::v1::ERROR_INTERNAL;
  using broadband_mode_switch::v1::ERROR_OUT_OF_RANGE;
  using broadband_mode_switch::v1::ERROR_PIPELINE_NOT_READY;

  if (status.success) {
    return {true, broadband_mode_switch::v1::ERROR_NONE, {}, {}};
  }
  if (status.error == CollectionStore::ErrorCode::kNotConfigured) {
    return control::failure(ERROR_PIPELINE_NOT_READY, "pipeline", "pipeline is not ready");
  }
  if (status.error == CollectionStore::ErrorCode::kInvalidCollection ||
      status.error == CollectionStore::ErrorCode::kInvalidLabel) {
    return control::failure(ERROR_OUT_OF_RANGE, "target", status.message);
  }
  return control::failure(ERROR_INTERNAL, "storage", status.message);
}

broadband_mode_switch::v1::ErrorCode protocol_error_code(
    protocol::ValidationCode code) {
  using namespace broadband_mode_switch::v1;
  switch (code) {
    case protocol::ValidationCode::kMalformed:
      return ERROR_MALFORMED;
    case protocol::ValidationCode::kUnsupportedVersion:
      return ERROR_UNSUPPORTED_VERSION;
    case protocol::ValidationCode::kUnknownCommand:
      return ERROR_UNKNOWN_COMMAND;
    case protocol::ValidationCode::kOutOfRange:
      return ERROR_OUT_OF_RANGE;
    case protocol::ValidationCode::kInvalidArgument:
    case protocol::ValidationCode::kIncompatiblePayload:
      return ERROR_INVALID_ARGUMENT;
    default:
      return ERROR_INTERNAL;
  }
}

bool ModeSwitchApp::setup() {
  if (!get_app_config(
          [this](const synapse::ApplicationNodeConfig& c) { return validate_config(c); },
          application_config_)) {
    spdlog::error("Failed to get app config");
    return false;
  }
  if (!parse_config(application_config_)) {
    spdlog::error("Failed to parse app config");
    return false;
  }

  // Canonical typed control tap. Its callback only validates and queues a
  // bounded request; the main loop applies it at a feature-routing boundary.
  if (!create_consumer_tap<protocol::ControlCommand>(
          "control",
          [this](const protocol::ControlCommand& command) { on_control_command(command); })) {
    spdlog::error("Failed to create consumer tap control");
    return false;
  }

  // Legacy control taps remain compatibility shims during migration. Their
  // callbacks use the same queue and application path as canonical commands.
  if (!create_consumer_tap<google::protobuf::ListValue>(
          "set_source_mode",
          [this](const google::protobuf::ListValue& m) { on_set_source_mode(m); })) {
    spdlog::error("Failed to create consumer tap set_source_mode");
    return false;
  }
  if (!create_consumer_tap<google::protobuf::ListValue>(
          "set_capture", [this](const google::protobuf::ListValue& m) { on_set_capture(m); })) {
    spdlog::error("Failed to create consumer tap set_capture");
    return false;
  }
  if (!create_consumer_tap<google::protobuf::ListValue>(
          "fit_mlp", [this](const google::protobuf::ListValue& m) { on_fit_mlp(m); })) {
    spdlog::error("Failed to create consumer tap fit_mlp");
    return false;
  }

  // Upstream reader (real broadband frames from node 1).
  if (!setup_reader(kBroadbandNodeId)) {
    spdlog::error("Failed to set up reader for node {}", kBroadbandNodeId);
    return false;
  }

  // Producer taps.
  // NOTE (plan open-question #1): broadband_out is a BroadbandFrame tap, the
  // semantically correct type for a broadband stream. The example app only
  // demonstrates create_tap<synapse::Tensor>; if the SDK rejects a
  // BroadbandFrame tap at build time, fall back to a Tensor payload here.
  if (!create_tap<synapse::BroadbandFrame>("broadband_out")) {
    spdlog::error("Failed to create tap broadband_out");
    return false;
  }
  if (!create_tap<synapse::Tensor>("class_out")) {
    spdlog::error("Failed to create tap class_out");
    return false;
  }
  if (!create_tap<protocol::StateSnapshot>("state")) {
    spdlog::error("Failed to create tap state");
    return false;
  }
  if (!create_tap<protocol::CommandResult>("command_result")) {
    spdlog::error("Failed to create tap command_result");
    return false;
  }

  spdlog::info("broadband-mode-switch setup complete (mode=SAMPLING)");
  return true;
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
bool ModeSwitchApp::validate_config(const synapse::ApplicationNodeConfig& configuration) {
  // All parameters have safe defaults, so nothing is strictly required. We
  // still surface the config for debugging.
  spdlog::info("Validating config: {}", configuration.DebugString());
  return true;
}

bool ModeSwitchApp::parse_config(const synapse::ApplicationNodeConfig& configuration) {
  const auto& p = configuration.parameters();
  auto num = [&](const char* k, double dflt) -> double {
    return p.contains(k) ? p.at(k).number_value() : dflt;
  };
  try {
    cfg_.sample_rate_hz = static_cast<uint32_t>(num("sample_rate_hz", 20000));
    cfg_.num_classes = static_cast<std::size_t>(num("num_classes", 5));
    cfg_.window_ms = num("window_ms", 200.0);
    cfg_.stride_ms = num("stride_ms", 20.0);
    cfg_.stft_size = static_cast<std::size_t>(num("stft_size", 256));
    cfg_.stft_hop = static_cast<std::size_t>(num("stft_hop", 128));
    cfg_.num_bands = static_cast<std::size_t>(num("num_bands", 8));
    cfg_.ring_capacity = static_cast<std::size_t>(num("ring_capacity", 2000));
    cfg_.mlp_hidden = static_cast<std::size_t>(num("mlp_hidden", 64));
    cfg_.mlp_dropout = static_cast<float>(num("mlp_dropout", 0.2));
    cfg_.mlp_lr = static_cast<float>(num("mlp_lr", 0.01));
    cfg_.mlp_epochs = static_cast<std::size_t>(num("mlp_epochs", 100));
    cfg_.synthetic_seed = static_cast<uint32_t>(num("synthetic_seed", 0xACE1));

    cfg_.channel_subset.clear();
    if (p.contains("channel_subset")) {
      for (const auto& v : p.at("channel_subset").list_value().values()) {
        cfg_.channel_subset.push_back(static_cast<int>(v.number_value()));
      }
    }

    if (cfg_.num_classes == 0) {
      spdlog::error("num_classes must be >= 1");
      return false;
    }
    application_config_ = configuration;
    spdlog::info(
        "Config: fs={} Hz classes={} window={} ms stride={} ms stft={}({}) bands={} "
        "ring={} hidden={} dropout={} lr={} epochs={} seed={:#x} subset={}",
        cfg_.sample_rate_hz, cfg_.num_classes, cfg_.window_ms, cfg_.stride_ms, cfg_.stft_size,
        cfg_.stft_hop, cfg_.num_bands, cfg_.ring_capacity, cfg_.mlp_hidden, cfg_.mlp_dropout,
        cfg_.mlp_lr, cfg_.mlp_epochs, cfg_.synthetic_seed, cfg_.channel_subset.size());
    return true;
  } catch (const std::exception& e) {
    spdlog::error("Failed to parse configuration: {}", e.what());
    return false;
  }
}

// ---------------------------------------------------------------------------
// Tap callbacks (off-thread; validation and bounded enqueue only)
// ---------------------------------------------------------------------------
void ModeSwitchApp::on_control_command(const protocol::ControlCommand& command) {
  const auto validation = protocol::validate_command(command);
  if (!validation) {
    // A command with a usable request id and known command kind can still
    // receive a correlated rejection. It is queued as a notification only;
    // it never reaches command application.
    if (!command.request_id().empty() &&
        command.request_id().size() <= protocol::kMaxRequestIdLength &&
        protocol::is_known_command(command.command())) {
      const auto rejection = control::ControlRequest::rejected_protocol_command(
          command, protocol_error_code(validation.code), validation.field, validation.message);
      if (control_queue_.try_enqueue(rejection) ==
          control::ControlCommandQueue::EnqueueResult::kAccepted) {
        return;
      }
    }
    spdlog::warn("control: rejected request_id={} field={} error={}", command.request_id(),
                 validation.field, validation.message);
    return;
  }
  const auto result =
      control_queue_.try_enqueue(control::ControlRequest::protocol_command(command));
  if (result == control::ControlCommandQueue::EnqueueResult::kDuplicateRequestId) {
    const auto rejection = control::ControlRequest::rejected_protocol_command(
        command, broadband_mode_switch::v1::ERROR_DUPLICATE_REQUEST_ID, "request_id",
        "request_id was already accepted in this App session");
    if (control_queue_.try_enqueue(rejection) ==
        control::ControlCommandQueue::EnqueueResult::kAccepted) {
      return;
    }
    spdlog::warn("control: duplicate request_id={} rejected", command.request_id());
  } else if (result == control::ControlCommandQueue::EnqueueResult::kFull) {
    spdlog::warn("control: queue full; request_id={} rejected", command.request_id());
  }
}

bool ModeSwitchApp::enqueue_legacy_request(control::ControlRequest request,
                                            const char* tap_name) {
  const auto result = control_queue_.try_enqueue(std::move(request));
  if (result == control::ControlCommandQueue::EnqueueResult::kFull) {
    spdlog::warn("{}: control queue full; request rejected", tap_name);
    return false;
  }
  return true;
}

void ModeSwitchApp::on_set_source_mode(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  if (v.empty() || !v[0].has_number_value()) {
    spdlog::warn("set_source_mode: expected [int mode]");
    return;
  }
  const double mode_value = v[0].number_value();
  if (!std::isfinite(mode_value) || std::floor(mode_value) != mode_value ||
      mode_value < static_cast<double>(static_cast<int>(SourceMode::kSampling)) ||
      mode_value > static_cast<double>(static_cast<int>(SourceMode::kSynthetic))) {
    spdlog::warn("set_source_mode: invalid mode");
    return;
  }
  const int m = static_cast<int>(mode_value);
  enqueue_legacy_request(control::ControlRequest::legacy_source_mode_request(m), "set_source_mode");
}

void ModeSwitchApp::on_set_capture(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  if (v.size() < 2 || !v[0].has_number_value() || !v[1].has_number_value()) {
    spdlog::warn("set_capture: expected [int label, int enable]");
    return;
  }
  const double label_value = v[0].number_value();
  if (!std::isfinite(label_value) || label_value < 0.0 ||
      label_value > static_cast<double>(std::numeric_limits<int>::max()) ||
      std::floor(label_value) != label_value) {
    spdlog::warn("set_capture: label must be a non-negative integer");
    return;
  }
  const int label = static_cast<int>(label_value);
  if (!std::isfinite(v[1].number_value())) {
    spdlog::warn("set_capture: enable must be finite");
    return;
  }
  const bool enable = v[1].number_value() != 0.0;
  if (static_cast<std::size_t>(label) >= cfg_.num_classes) {
    spdlog::warn("set_capture: label {} out of range [0,{})", label, cfg_.num_classes);
    return;
  }
  protocol::ControlCommand command;
  command.set_protocol_version(protocol::kProtocolVersion);
  command.set_request_id("legacy/set_capture");
  command.set_command(broadband_mode_switch::v1::COMMAND_PREPARE_CAPTURE);
  auto* prepare = command.mutable_prepare_capture();
  prepare->set_collection_id(0);
  prepare->set_label(static_cast<std::uint32_t>(label));
  prepare->set_enabled(enable);
  enqueue_legacy_request(control::ControlRequest::legacy_protocol_command(command), "set_capture");
}

void ModeSwitchApp::on_fit_mlp(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  std::uint32_t epochs = 0;
  if (!v.empty()) {
    if (!v[0].has_number_value() || !std::isfinite(v[0].number_value()) ||
        v[0].number_value() < 0.0 ||
        v[0].number_value() > static_cast<double>(protocol::kMaxFitEpochs) ||
        std::floor(v[0].number_value()) != v[0].number_value()) {
      spdlog::warn("fit_mlp: expected an optional non-negative integer epoch count");
      return;
    }
    epochs = static_cast<std::uint32_t>(v[0].number_value());
  }
  protocol::ControlCommand command;
  command.set_protocol_version(protocol::kProtocolVersion);
  command.set_request_id("legacy/fit_mlp");
  command.set_command(broadband_mode_switch::v1::COMMAND_FIT);
  command.mutable_fit()->set_epochs(epochs);
  enqueue_legacy_request(control::ControlRequest::legacy_protocol_command(command), "fit_mlp");
}

void ModeSwitchApp::drain_control_commands() {
  control::ControlRequest request;
  while (control_queue_.try_dequeue(request)) {
    apply_control_request(request);
  }
}

void ModeSwitchApp::apply_control_request(const control::ControlRequest& request) {
  if (request.kind == control::ControlRequest::Kind::kLegacySourceMode) {
    const auto previous_mode = mode_;
    mode_ = static_cast<SourceMode>(request.legacy_source_mode);
    if (mode_ != previous_mode) {
      source_connected_ = false;
      have_last_source_frame_wall_time_ = false;
    }
    spdlog::info("set_source_mode -> {}", mode_ == SourceMode::kSampling ? "SAMPLING"
                                                                          : "SYNTHETIC");
    publish_state_snapshot();
    return;
  }
  if (request.rejected_before_enqueue) {
    const auto failure = control::failure(request.rejection_code, request.rejection_field,
                                          request.rejection_message);
    publish_command_outcome(request.command,
                            broadband_mode_switch::v1::RESULT_FAILED, failure);
    return;
  }
  apply_protocol_command(request.command);
}

void ModeSwitchApp::set_last_error(const control::TransitionResult& failure) {
  last_error_.set_code(failure.code);
  last_error_.set_message(failure.message.empty() ? "command failed" : failure.message);
  last_error_.set_field(failure.field);
  last_error_.set_retryable(failure.code == broadband_mode_switch::v1::ERROR_PIPELINE_NOT_READY ||
                            failure.code == broadband_mode_switch::v1::ERROR_BUSY ||
                            failure.code == broadband_mode_switch::v1::ERROR_TIMEOUT ||
                            failure.code == broadband_mode_switch::v1::ERROR_TRANSPORT_DISCONNECTED);
  has_last_error_ = true;
}

void ModeSwitchApp::publish_command_outcome(
    const protocol::ControlCommand& command, broadband_mode_switch::v1::ResultStatus status,
    const control::TransitionResult& outcome, const protocol::FitProgress* progress) {
  using namespace broadband_mode_switch::v1;
  if (!outcome.success) {
    set_last_error(outcome);
    spdlog::warn("control: request_id={} command={} rejected field={} error={}",
                 command.request_id(), static_cast<int>(command.command()), outcome.field,
                 outcome.message);
  }

  // Publish the replacement state first so the result's state_version always
  // identifies the snapshot that describes the command outcome.
  publish_state_snapshot();

  CommandResult result;
  result.set_protocol_version(protocol::kProtocolVersion);
  result.set_request_id(command.request_id());
  result.set_command(command.command());
  result.set_status(status);
  result.set_state_version(state_version_);
  if (!outcome.success) {
    auto* error = result.mutable_error();
    error->set_code(outcome.code);
    error->set_message(outcome.message.empty() ? "command failed" : outcome.message);
    error->set_field(outcome.field);
    error->set_retryable(outcome.code == ERROR_PIPELINE_NOT_READY ||
                         outcome.code == ERROR_BUSY || outcome.code == ERROR_TIMEOUT ||
                         outcome.code == ERROR_TRANSPORT_DISCONNECTED);
  }
  if (progress != nullptr) {
    *result.mutable_progress() = *progress;
  }
  if (!protocol::validate_command_result(result)) {
    spdlog::error("control: refusing to publish invalid result for request_id={}",
                  command.request_id());
    return;
  }
  if (!publish_tap("command_result", result)) {
    spdlog::warn("control: failed to publish result for request_id={}", command.request_id());
  }
}

void ModeSwitchApp::publish_state_snapshot() {
  using namespace broadband_mode_switch::v1;

  last_state_publication_ = std::chrono::steady_clock::now();
  have_last_state_publication_ = true;
  StateSnapshot snapshot;
  snapshot.set_protocol_version(protocol::kProtocolVersion);
  snapshot.set_state_version(++state_version_);
  snapshot.set_timestamp_ns(synapse::get_steady_clock_now().count());

  auto* pipeline = snapshot.mutable_pipeline();
  if (pipeline_error_) {
    pipeline->set_state(PIPELINE_ERROR);
  } else if (!pipeline_ready_) {
    pipeline->set_state(PIPELINE_NOT_READY);
  } else if (!source_connected_) {
    pipeline->set_state(PIPELINE_DISCONNECTED);
  } else {
    pipeline->set_state(PIPELINE_READY);
  }
  pipeline->set_source_mode(mode_ == SourceMode::kSampling ? SOURCE_MODE_SAMPLING
                                                            : SOURCE_MODE_SYNTHETIC);

  auto* active = snapshot.mutable_active();
  active->set_collection_id(active_target_.collection_id);
  active->set_label(active_target_.label);
  active->set_capture_enabled(active_target_.capture_enabled);

  {
    // CollectionStore is intentionally not internally synchronized. Hold the
    // short-lived model lock while copying all counts/generations into the
    // protobuf, then release it before crossing the SDK publication boundary.
    std::lock_guard<std::mutex> lock(model_mutex_);
    for (std::size_t collection_id = 0; collection_id < buffers_.collection_count();
         ++collection_id) {
      const auto collection = buffers_.collection_info(collection_id);
      if (!collection.success()) {
        continue;
      }
      auto* collection_status = snapshot.add_collections();
      collection_status->set_collection_id(static_cast<std::uint32_t>(collection.value.collection_id));
      collection_status->set_feature_dimension(
          static_cast<std::uint32_t>(collection.value.feature_dimension));
      collection_status->set_data_generation(collection.value.data_generation);
      for (std::size_t label = 0; label < collection.value.label_count; ++label) {
        const auto target = buffers_.target_info(collection_id, label);
        if (!target.success()) {
          continue;
        }
        auto* label_status = collection_status->add_labels();
        label_status->set_label(static_cast<std::uint32_t>(target.value.label));
        label_status->set_count(static_cast<std::uint32_t>(target.value.count));
        label_status->set_capacity(static_cast<std::uint32_t>(target.value.capacity));
      }
    }
  auto* model = snapshot.mutable_model();
  model->set_phase(model_phase_);
  model->set_ready(model_ready_);
  model->set_has_source_collection_id(model_has_source_collection_);
  if (model_has_source_collection_) {
    model->set_source_collection_id(model_source_collection_);
    model->set_source_generation(model_source_generation_);
    const auto generation = buffers_.generation(model_source_collection_);
    model->set_stale(!generation.success() || generation.value != model_source_generation_);
  }
  model->set_epoch(model_epoch_);
  model->set_total_epochs(model_total_epochs_);
  model->set_loss(model_loss_);
  model->set_accuracy(model_accuracy_);
  model->set_duration_ms(model_duration_ms_);
  }

  if (has_last_error_) {
    *snapshot.mutable_last_error() = last_error_;
  }

  if (!protocol::validate_state(snapshot)) {
    spdlog::error("state: refusing to publish invalid snapshot at version {}", state_version_);
    return;
  }
  if (!publish_tap("state", snapshot)) {
    spdlog::warn("state: failed to publish snapshot version {}", state_version_);
  }
}

void ModeSwitchApp::publish_periodic_state_if_due() {
  const auto now = std::chrono::steady_clock::now();
  if (pipeline_ready_ && mode_ == SourceMode::kSampling &&
      have_last_source_frame_wall_time_ &&
      now - last_source_frame_wall_time_ > source_disconnect_timeout_) {
    source_connected_ = false;
  }
  if (!have_last_state_publication_ || now - last_state_publication_ >= state_publication_period_) {
    publish_state_snapshot();
  }
}

void ModeSwitchApp::apply_protocol_command(const protocol::ControlCommand& command) {
  using namespace broadband_mode_switch::v1;

  auto configured_dimensions = [this]() {
    std::pair<std::size_t, std::size_t> dimensions;
    std::lock_guard<std::mutex> lock(model_mutex_);
    dimensions = {buffers_.collection_count(), buffers_.labels_per_collection()};
    return dimensions;
  };

  auto check_target = [this](std::uint32_t collection_id, std::uint32_t label) {
    std::lock_guard<std::mutex> lock(model_mutex_);
    return buffers_.check_target(collection_id, label);
  };

  auto finish = [this, &command](const control::TransitionResult& outcome,
                                 ResultStatus status = RESULT_SUCCEEDED) {
    publish_command_outcome(command, status, outcome);
  };

  switch (command.command()) {
    case COMMAND_GET_STATE:
      finish(control::success());
      return;
    case COMMAND_SUBSCRIBE_STATE:
      state_subscription_enabled_ = command.subscribe_state().enabled();
      finish(control::success());
      return;
    case COMMAND_PREPARE_CAPTURE: {
      const auto [collections, labels] = configured_dimensions();
      if (collections == 0 || labels == 0) {
        finish(control::failure(ERROR_PIPELINE_NOT_READY, "pipeline", "pipeline is not ready"));
        return;
      }
      auto candidate = active_target_;
      auto transition = control::prepare_capture(
          candidate, command.prepare_capture().collection_id(), command.prepare_capture().label(),
          command.prepare_capture().enabled(), collections, labels);
      if (!transition) {
        finish(transition);
        return;
      }
      const auto status = check_target(candidate.collection_id, candidate.label);
      if (!status.success) {
        finish(storage_failure(status));
        return;
      }
      // The assignment is the only active-target mutation and occurs after
      // every validation, so capture can never observe a partial target.
      active_target_ = candidate;
      spdlog::info("prepare_capture -> collection={} label={} enable={}",
                   active_target_.collection_id, active_target_.label,
                   active_target_.capture_enabled);
      finish(control::success());
      return;
    }
    case COMMAND_SELECT_COLLECTION: {
      const auto [collections, labels] = configured_dimensions();
      if (collections == 0 || labels == 0) {
        finish(control::failure(ERROR_PIPELINE_NOT_READY, "pipeline", "pipeline is not ready"));
        return;
      }
      auto candidate = active_target_;
      auto transition = control::select_collection(
          candidate, command.select_collection().collection_id(), collections, labels);
      if (!transition) {
        finish(transition);
        return;
      }
      const auto status = check_target(candidate.collection_id, candidate.label);
      if (!status.success) {
        finish(storage_failure(status));
        return;
      }
      active_target_ = candidate;
      finish(control::success());
      return;
    }
    case COMMAND_SELECT_LABEL: {
      const auto [collections, labels] = configured_dimensions();
      if (collections == 0 || labels == 0) {
        finish(control::failure(ERROR_PIPELINE_NOT_READY, "pipeline", "pipeline is not ready"));
        return;
      }
      auto candidate = active_target_;
      auto transition = control::select_label(candidate, command.select_label().label(), labels);
      if (!transition) {
        finish(transition);
        return;
      }
      const auto status = check_target(candidate.collection_id, candidate.label);
      if (!status.success) {
        finish(storage_failure(status));
        return;
      }
      active_target_ = candidate;
      finish(control::success());
      return;
    }
    case COMMAND_SET_CAPTURE: {
      const bool enabled = command.set_capture().enabled();
      if (enabled) {
        const auto status = check_target(active_target_.collection_id, active_target_.label);
        if (!status.success) {
          finish(storage_failure(status));
          return;
        }
      }
      active_target_.capture_enabled = enabled;
      finish(control::success());
      return;
    }
    case COMMAND_FIT: {
      if (!pipeline_ready_) {
        finish(control::failure(ERROR_PIPELINE_NOT_READY, "pipeline", "pipeline is not ready"));
        return;
      }
      if (fit_requested_ || fit_worker_.busy()) {
        finish(control::failure(ERROR_BUSY, "fit", "fit is already queued or running"));
        return;
      }
      const auto status = check_target(active_target_.collection_id, active_target_.label);
      if (!status.success) {
        finish(storage_failure(status));
        return;
      }

      // Copy the selected collection while holding the short-lived data/model
      // lock. The worker receives only this value-owned snapshot and never
      // reads buffers_ while acquisition and capture continue.
      FitWorker::Request request;
      control::TransitionResult snapshot_failure = control::success();
      {
        std::lock_guard<std::mutex> lock(model_mutex_);
        const auto collect_status =
            buffers_.collect(active_target_.collection_id, request.features, request.labels);
        if (!collect_status.success) {
          snapshot_failure = storage_failure(collect_status);
        } else if (request.features.empty()) {
          snapshot_failure = control::failure(ERROR_EMPTY_COLLECTION, "collection",
                                               "active collection has no captured windows");
        } else {
          request.config = mlp_.config();
          if (command.fit().epochs() != 0) {
            request.config.epochs = command.fit().epochs();
          }
          model_source_generation_ = collect_status.generation;
        }
      }
      if (!snapshot_failure.success) {
        finish(snapshot_failure);
        return;
      }

      fit_requested_ = true;
      pending_fit_ = std::move(request);
      fit_command_ = command;
      model_phase_ = MODEL_QUEUED;
      model_has_source_collection_ = true;
      model_source_collection_ = active_target_.collection_id;
      finish(control::success(), RESULT_ACCEPTED);
      return;
    }
    case COMMAND_FLUSH: {
      const auto& flush = command.flush();
      CollectionStore::OperationResult status;
      bool invalid_scope = false;
      {
        std::lock_guard<std::mutex> lock(model_mutex_);
        switch (flush.scope()) {
          case Flush_Scope_SCOPE_LABEL: {
            const auto collection = flush.has_collection_id()
                                        ? flush.collection_id()
                                        : active_target_.collection_id;
            const auto label = flush.has_label() ? flush.label() : active_target_.label;
            status = buffers_.check_target(collection, label);
            if (status.success) status = buffers_.clear(collection, label);
            break;
          }
          case Flush_Scope_SCOPE_COLLECTION: {
            const auto collection = flush.has_collection_id()
                                        ? flush.collection_id()
                                        : active_target_.collection_id;
            status = buffers_.clear_collection(collection);
            break;
          }
          case Flush_Scope_SCOPE_ALL:
            status = buffers_.clear_all();
            break;
          default:
            invalid_scope = true;
            break;
        }
      }
      if (invalid_scope) {
        finish(control::failure(ERROR_INVALID_ARGUMENT, "flush.scope",
                                "unknown flush scope"));
        return;
      }
      finish(status.success ? control::success() : storage_failure(status));
      return;
    }
    default:
      // The tap callback validates command shape and kind. Keep this guard so
      // a future command cannot silently mutate state before its application
      // semantics are added.
      finish(control::failure(ERROR_UNKNOWN_COMMAND, "command",
                              "command application is not implemented"));
      return;
  }
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------
void ModeSwitchApp::main() {
  std::vector<int32_t> synth_data;

  // Publish a complete baseline before the first source frame arrives. This
  // makes the device discoverable by late subscribers and explicitly reports
  // the not-ready pipeline state.
  publish_state_snapshot();

  while (node_running_) {
    publish_periodic_state_if_due();

    // Apply every queued request before reading/routing the next source
    // sample. No feature window can see an intermediate target transition.
    drain_fit_events();
    drain_control_commands();

    // Start a pending training request before touching data. Training itself
    // runs on FitWorker and does not block this acquisition loop.
    maybe_fit();

    const auto mode = mode_;

    if (mode == SourceMode::kSynthetic) {
      // Lazily create the generator once we know the channel count. Prefer the
      // upstream channel count if we've seen a frame; else fall back to the
      // config subset size or 32.
      if (!synthetic_) {
        std::size_t ch = upstream_channels_;
        if (ch == 0) {
          ch = !cfg_.channel_subset.empty() ? cfg_.channel_subset.size() : 32;
        }
        synthetic_ = std::make_unique<SyntheticSource>(
            ch, static_cast<uint16_t>(cfg_.synthetic_seed & 0xFFFF));
        synthetic_seq_ = 0;
        synthetic_start_ns_ = synapse::get_steady_clock_now().count();
        spdlog::info("Synthetic source started: {} channels", ch);
      }

      source_connected_ = true;
      last_source_frame_wall_time_ = std::chrono::steady_clock::now();
      have_last_source_frame_wall_time_ = true;

      synthetic_->next_frame(synth_data);

      // Build a BroadbandFrame with our own monotonic sequence + derived stamp.
      const double ns_per_sample = 1e9 / static_cast<double>(cfg_.sample_rate_hz);
      const uint64_t ts = synthetic_start_ns_ +
                          static_cast<uint64_t>(synthetic_seq_ * ns_per_sample);
      synapse::BroadbandFrame out;
      out.set_timestamp_ns(ts);
      out.set_sequence_number(synthetic_seq_);
      out.set_sample_rate_hz(cfg_.sample_rate_hz);
      out.set_unix_timestamp_ns(synapse::get_steady_clock_now().count());
      for (int32_t s : synth_data) out.add_frame_data(s);
      ++synthetic_seq_;

      publish_tap("broadband_out", out);
      initialize_pipeline(synth_data.size());
      ingest_sample(synth_data, ts);

      // Pace roughly to the sample rate so we don't free-run the CPU. This is a
      // coarse throttle, not a hard real-time guarantee.
      std::this_thread::sleep_for(
          std::chrono::nanoseconds(static_cast<int64_t>(ns_per_sample)));
      continue;
    }

    // SAMPLING mode: forward every upstream frame unchanged. Never overwrite
    // the source timestamps (AGENTS.md). receive_multipart() may contain more
    // than one BroadbandFrame, so process the complete batch in wire order.
    ReadBatch batch;
    if (!read_frames(batch)) {
      continue;
    }

    for (const auto& in_frame : batch.frames) {
      publish_tap("broadband_out", in_frame);

      // Feed the pipeline from the real frame.
      const int n = in_frame.frame_data_size();
      std::vector<int32_t> data(n);
      for (int i = 0; i < n; ++i) data[i] = in_frame.frame_data(i);
      initialize_pipeline(static_cast<std::size_t>(n));
      ingest_sample(data, in_frame.timestamp_ns());
    }
  }
}

bool ModeSwitchApp::read_frames(ReadBatch& batch) {
  batch = ReadBatch{};
  auto messages = data_reader_->receive_multipart();
  if (messages.empty()) {
    std::this_thread::sleep_for(std::chrono::microseconds(1));
    return false;
  }

  const auto stats = drain_multipart(
      std::move(messages),
      [](auto&& message) {
        return synapse::parse_protobuf_message<synapse::BroadbandFrame>(
            std::move(message));
      },
      [this, &batch](const synapse::BroadbandFrame& frame) {
    if (have_last_sequence_) {
      const uint64_t expected = last_sequence_number_ + 1;
      if (frame.sequence_number() > expected) {
        spdlog::warn("Dropped {} frames (expected seq {}, got {})",
                     frame.sequence_number() - expected,
                     expected, frame.sequence_number());
      } else if (frame.sequence_number() < expected) {
        spdlog::warn("Non-monotonic broadband sequence (expected seq {}, got {})",
                     expected, frame.sequence_number());
      }
    }
    last_sequence_number_ = frame.sequence_number();
    have_last_sequence_ = true;
    source_connected_ = true;
    last_source_frame_wall_time_ = std::chrono::steady_clock::now();
    have_last_source_frame_wall_time_ = true;

    batch.frames.push_back(frame);
      });

  batch.received_message_count = stats.batch_size;
  batch.parsed_message_count = stats.parsed_count;
  batch.parse_error_count = stats.parse_error_count;
  if (stats.parse_error_count != 0) {
    spdlog::warn("Failed to parse {} broadband frame message(s) in multipart batch",
                 stats.parse_error_count);
  }
  ++receive_batch_count_;
  received_message_count_ += stats.batch_size;
  parsed_message_count_ += stats.parsed_count;
  parse_error_count_ += stats.parse_error_count;
  forwarded_frame_count_ += stats.forwarded_count;
  maybe_log_reader_diagnostics(batch);

  return !batch.frames.empty();
}

void ModeSwitchApp::maybe_log_reader_diagnostics(const ReadBatch& batch) {
  const auto now = std::chrono::steady_clock::now();
  const bool periodic = !have_last_reader_diagnostics_log_ ||
                        now - last_reader_diagnostics_log_ >= std::chrono::seconds(1);
  if (batch.received_message_count <= 1 && batch.parse_error_count == 0 && !periodic) {
    return;
  }

  spdlog::info(
      "Broadband receive diagnostics: batch_size={} parsed_messages={} "
      "forwarded_frames={} parse_errors={} totals(batches={} messages={} parsed={} "
      "forwarded={} parse_errors={})",
      batch.received_message_count, batch.parsed_message_count, batch.frames.size(),
      batch.parse_error_count, receive_batch_count_, received_message_count_,
      parsed_message_count_, forwarded_frame_count_, parse_error_count_);
  last_reader_diagnostics_log_ = now;
  have_last_reader_diagnostics_log_ = true;
}

// ---------------------------------------------------------------------------
// Pipeline: windowing -> featurize -> capture/classify
// ---------------------------------------------------------------------------
void ModeSwitchApp::initialize_pipeline(std::size_t upstream_channels) {
  if (pipeline_ready_ || pipeline_error_ || upstream_channels == 0) {
    return;
  }
  upstream_channels_ = upstream_channels;

  // Resolve the featurized channel map from the configured subset (default all).
  channel_map_.clear();
  if (cfg_.channel_subset.empty()) {
    for (std::size_t i = 0; i < upstream_channels_; ++i) channel_map_.push_back(i);
  } else {
    for (int c : cfg_.channel_subset) {
      if (c >= 0 && static_cast<std::size_t>(c) < upstream_channels_) {
        channel_map_.push_back(static_cast<std::size_t>(c));
      } else {
        spdlog::warn("channel_subset entry {} out of range [0,{}); skipped", c,
                     upstream_channels_);
      }
    }
  }
  featurized_channels_ = channel_map_.size();
  if (featurized_channels_ == 0) {
    spdlog::error("No valid featurized channels; pipeline disabled");
    pipeline_error_ = true;
    pipeline_error_message_ = "no valid featurized channels";
    set_last_error(control::failure(broadband_mode_switch::v1::ERROR_INTERNAL, "pipeline",
                                    pipeline_error_message_));
    return;
  }

  window_samples_ =
      static_cast<std::size_t>(std::llround(cfg_.window_ms * cfg_.sample_rate_hz / 1000.0));
  stride_samples_ =
      std::max<std::size_t>(1, static_cast<std::size_t>(
                                   std::llround(cfg_.stride_ms * cfg_.sample_rate_hz / 1000.0)));
  if (window_samples_ < cfg_.stft_size) {
    spdlog::warn("window_samples ({}) < stft_size ({}); clamping window up",
                 window_samples_, cfg_.stft_size);
    window_samples_ = cfg_.stft_size;
  }

  window_ring_.assign(featurized_channels_, std::vector<float>(window_samples_, 0.0f));
  ring_pos_ = 0;
  samples_seen_ = 0;
  samples_since_stride_ = 0;

  MpfFeaturizer::Config fcfg;
  fcfg.num_channels = featurized_channels_;
  fcfg.stft_size = cfg_.stft_size;
  fcfg.stft_hop = cfg_.stft_hop;
  fcfg.num_bands = cfg_.num_bands;
  featurizer_ = std::make_unique<MpfFeaturizer>(fcfg);

  {
    std::lock_guard<std::mutex> lock(model_mutex_);
    const auto storage_status =
        buffers_.configure(1, cfg_.num_classes, cfg_.ring_capacity, featurizer_->feature_dim());
    if (!storage_status.success) {
      spdlog::error("Failed to configure feature storage: {} ({})", storage_status.message,
                    CollectionStore::error_code_name(storage_status.error));
      pipeline_error_ = true;
      pipeline_error_message_ = storage_status.message;
      set_last_error(control::failure(broadband_mode_switch::v1::ERROR_INTERNAL, "pipeline",
                                      pipeline_error_message_));
      return;
    }
    Mlp::Config mcfg;
    mcfg.input_dim = featurizer_->feature_dim();
    mcfg.hidden_dim = cfg_.mlp_hidden;
    mcfg.num_classes = cfg_.num_classes;
    mcfg.dropout = cfg_.mlp_dropout;
    mcfg.lr = cfg_.mlp_lr;
    mcfg.epochs = cfg_.mlp_epochs;
    mcfg.seed = cfg_.synthetic_seed;
    mlp_.init(mcfg);
  }

  pipeline_ready_ = true;
  pipeline_error_ = false;
  pipeline_error_message_.clear();
  spdlog::info(
      "Pipeline ready: {} featurized ch, window={} samp, stride={} samp, feature_dim={}",
      featurized_channels_, window_samples_, stride_samples_, featurizer_->feature_dim());
  publish_state_snapshot();
}

void ModeSwitchApp::ingest_sample(const std::vector<int32_t>& frame_data,
                                  uint64_t timestamp_ns) {
  if (!pipeline_ready_) {
    return;
  }
  // Write this time-point into the ring at ring_pos_.
  for (std::size_t fc = 0; fc < featurized_channels_; ++fc) {
    const std::size_t up = channel_map_[fc];
    const float v = (up < frame_data.size()) ? static_cast<float>(frame_data[up]) : 0.0f;
    window_ring_[fc][ring_pos_] = v;
  }
  ring_pos_ = (ring_pos_ + 1) % window_samples_;
  ++samples_seen_;
  ++samples_since_stride_;

  // Once the window is full, emit a feature vector every stride_samples_.
  if (samples_seen_ >= window_samples_ && samples_since_stride_ >= stride_samples_) {
    samples_since_stride_ = 0;
    process_window(timestamp_ns);
  }
}

void ModeSwitchApp::process_window(uint64_t window_end_timestamp_ns) {
  // Unroll the ring into contiguous [C][N] order (oldest -> newest) so the STFT
  // sees time-correct blocks.
  std::vector<std::vector<float>> window(featurized_channels_);
  for (std::size_t fc = 0; fc < featurized_channels_; ++fc) {
    window[fc].resize(window_samples_);
    for (std::size_t n = 0; n < window_samples_; ++n) {
      const std::size_t idx = (ring_pos_ + n) % window_samples_;
      window[fc][n] = window_ring_[fc][idx];
    }
  }

  std::vector<float> feature = featurizer_->compute(window);

  // Route to capture and/or classification.
  const bool capturing = active_target_.capture_enabled;
  if (capturing) {
    const std::size_t collection = active_target_.collection_id;
    const std::size_t label = active_target_.label;
    std::lock_guard<std::mutex> lock(model_mutex_);
    const auto append_status = buffers_.append(collection, label, feature);
    if (!append_status.success) {
      spdlog::error("capture rejected: {} ({})", append_status.message,
                    CollectionStore::error_code_name(append_status.error));
      return;
    }
    // Log per-class counts occasionally.
    static thread_local std::size_t log_ctr = 0;
    if ((++log_ctr % 50) == 0) {
      spdlog::info("capture collection={} label={} count={} total={}", collection, label,
                   append_status.count, append_status.total);
    }
  }

  if (model_ready_) {
    std::vector<float> probs;
    {
      std::lock_guard<std::mutex> lock(model_mutex_);
      probs = mlp_.infer(feature);
    }
    if (!probs.empty()) {
      publish_class(probs, window_end_timestamp_ns);
    }
  }
}

void ModeSwitchApp::drain_fit_events() {
  FitWorker::Event event;
  // Drain at most one event per acquisition-loop turn. A fast worker can
  // otherwise queue many completed epochs and make result publication itself
  // monopolize the loop that must continue reading source frames.
  if (!fit_worker_.poll(event)) return;
  if (event.kind == FitWorker::EventKind::kProgress) {
    model_phase_ = broadband_mode_switch::v1::MODEL_RUNNING;
    model_epoch_ = static_cast<std::uint32_t>(event.progress.epoch);
    model_total_epochs_ = static_cast<std::uint32_t>(event.progress.total_epochs);
    model_loss_ = event.progress.loss;
    model_accuracy_ = event.progress.accuracy;
    model_duration_ms_ = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - fit_started_at_)
            .count());

    protocol::FitProgress wire_progress;
    wire_progress.set_epoch(static_cast<std::uint32_t>(event.progress.epoch));
    wire_progress.set_total_epochs(static_cast<std::uint32_t>(event.progress.total_epochs));
    wire_progress.set_loss(event.progress.loss);
    wire_progress.set_accuracy(event.progress.accuracy);
    // Progress is an accepted, non-terminal result. The state snapshot
    // immediately before it carries the same metrics and state_version.
    publish_command_outcome(fit_command_, broadband_mode_switch::v1::RESULT_ACCEPTED,
                            control::success(), &wire_progress);
    return;
  }

  model_duration_ms_ = static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - fit_started_at_)
          .count());

  if (event.kind == FitWorker::EventKind::kSucceeded && event.candidate) {
    // This is the only live-model mutation performed by the worker path.
    // Inference takes the same mutex, so it observes either the old complete
    // model or this complete candidate, never partially updated weights.
    {
      std::lock_guard<std::mutex> lock(model_mutex_);
      std::swap(mlp_, *event.candidate);
    }
    model_ready_ = true;
    model_phase_ = broadband_mode_switch::v1::MODEL_SUCCEEDED;
    model_epoch_ = static_cast<std::uint32_t>(event.progress.epoch);
    model_total_epochs_ = static_cast<std::uint32_t>(event.progress.total_epochs);
    model_loss_ = event.progress.loss;
    model_accuracy_ = event.progress.accuracy;

    protocol::FitProgress final_progress;
    final_progress.set_epoch(model_epoch_);
    final_progress.set_total_epochs(model_total_epochs_);
    final_progress.set_loss(model_loss_);
    final_progress.set_accuracy(model_accuracy_);
    publish_command_outcome(fit_command_, broadband_mode_switch::v1::RESULT_SUCCEEDED,
                            control::success(), &final_progress);
    spdlog::info("fit_mlp: done. final loss={:.4f} accuracy={:.3f}", model_loss_,
                 model_accuracy_);
  } else {
    const auto code = event.malformed ? broadband_mode_switch::v1::ERROR_MALFORMED
                                      : broadband_mode_switch::v1::ERROR_INTERNAL;
    const auto failure =
        control::failure(code, "fit", event.message.empty() ? "fit worker failed" : event.message);
    model_phase_ = broadband_mode_switch::v1::MODEL_FAILED;
    // Keep model_ready_ and mlp_ unchanged: a failed candidate is never
    // allowed to replace a previously usable inference model.
    publish_command_outcome(fit_command_, broadband_mode_switch::v1::RESULT_FAILED, failure);
    spdlog::error("fit_mlp: training failed: {}", failure.message);
  }
  fit_requested_ = false;
  pending_fit_.reset();
}

void ModeSwitchApp::maybe_fit() {
  if (!fit_requested_ || !pending_fit_) return;

  fit_requested_ = false;
  model_phase_ = broadband_mode_switch::v1::MODEL_RUNNING;
  model_epoch_ = 0;
  model_total_epochs_ = static_cast<std::uint32_t>(pending_fit_->config.epochs);
  model_loss_ = 0.0f;
  model_accuracy_ = 0.0f;
  model_duration_ms_ = 0;
  fit_started_at_ = std::chrono::steady_clock::now();

  // Publish RUNNING before starting the thread. Worker events are drained by
  // later main-loop iterations, so accepted/progress/terminal publication
  // remains ordered and all SDK calls stay on the App thread.
  publish_state_snapshot();
  if (!fit_worker_.start(std::move(*pending_fit_))) {
    const auto failure =
        control::failure(broadband_mode_switch::v1::ERROR_BUSY, "fit", "fit is already active");
    model_phase_ = broadband_mode_switch::v1::MODEL_FAILED;
    publish_command_outcome(fit_command_, broadband_mode_switch::v1::RESULT_FAILED, failure);
    pending_fit_.reset();
    return;
  }
  pending_fit_.reset();
}

void ModeSwitchApp::publish_class(const std::vector<float>& probs, uint64_t timestamp_ns) {
  synapse::Tensor t;
  const int k = static_cast<int>(probs.size());
  t.mutable_shape()->Add(k);
  t.set_dtype(synapse::Tensor_DType_DT_FLOAT);
  t.set_endianness(synapse::Tensor_Endianness_TENSOR_LITTLE_ENDIAN);
  t.set_data(synapse::pack_tensor_data(probs));
  t.set_timestamp_ns(timestamp_ns);
  publish_tap("class_out", t);
}

}  // namespace app

int main(const int, const char**) { return synapse::Entrypoint<app::ModeSwitchApp>(); }
