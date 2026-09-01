#pragma once
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include <synapse-app-sdk/app/app.hpp>
#include <synapse-app-sdk/middleware/conversions.hpp>
#include <synapse-app-sdk/utils/time/time.hpp>

#include "api/datatype.pb.h"

#include <google/protobuf/struct.pb.h>

#include "mlp.hpp"
#include "fit_worker.hpp"
#include "mpf_features.hpp"
#include "collection_store.hpp"
#include "control_command_queue.hpp"
#include "control_state.hpp"
#include "synthetic_source.hpp"
#include "multipart_drain.hpp"

namespace app {

// broadband-mode-switch Synapse App.
//
// Node graph:
//   kBroadbandSource(id=1) -> kApplication(id=2, "broadband-mode-switch")
//
// Consumer taps:
//   control            ControlCommand              canonical v1 control plane
//   set_source_mode  [int mode]                0=SAMPLING 1=SYNTHETIC
//   set_capture      [int label, int enable]   route feature windows to a class
//   fit_mlp          [int epochs?]             trigger a training pass
//
// Producer taps:
//   broadband_out    BroadbandFrame            real (forwarded) or synthetic
//   class_out        Tensor[num_classes]       softmax distribution, on classify
//   state            StateSnapshot              complete replacement snapshot
//   command_result   CommandResult              correlated command outcome
//
// Tap callbacks run on their own threads; they only validate and enqueue small
// requests, then return fast. Control mutations, featurisation, capture,
// classification, worker-event draining, and publication run serially in
// main(); the FitWorker performs only private candidate training off-thread.
class ModeSwitchApp : public synapse::App {
 public:
  ModeSwitchApp() = default;
  virtual bool setup() override;

 protected:
  virtual void main() override;

 private:
  enum class SourceMode : int { kSampling = 0, kSynthetic = 1 };

  // ---- configuration ----
  struct AppConfig {
    uint32_t sample_rate_hz = 20000;
    std::size_t num_classes = 5;
    double window_ms = 200.0;
    double stride_ms = 20.0;
    std::size_t stft_size = 256;
    std::size_t stft_hop = 128;
    std::size_t num_bands = 8;
    std::vector<int> channel_subset;  // empty => all upstream channels
    std::size_t ring_capacity = 2000;
    std::size_t mlp_hidden = 64;
    float mlp_dropout = 0.2f;
    float mlp_lr = 0.01f;
    std::size_t mlp_epochs = 100;
    uint32_t synthetic_seed = 0xACE1;
  };

  bool validate_config(const synapse::ApplicationNodeConfig& configuration);
  bool parse_config(const synapse::ApplicationNodeConfig& configuration);

  // ---- tap callbacks (run off-thread; keep them tiny) ----
  void on_control_command(const protocol::ControlCommand& command);
  void on_set_source_mode(const google::protobuf::ListValue& msg);
  void on_set_capture(const google::protobuf::ListValue& msg);
  void on_fit_mlp(const google::protobuf::ListValue& msg);

  // ---- main-loop helpers ----
  void drain_control_commands();
  void drain_fit_events();
  void apply_control_request(const control::ControlRequest& request);
  void apply_protocol_command(const protocol::ControlCommand& command);
  bool enqueue_legacy_request(control::ControlRequest request, const char* tap_name);
  void publish_command_outcome(const protocol::ControlCommand& command,
                               broadband_mode_switch::v1::ResultStatus status,
                               const control::TransitionResult& outcome,
                               const protocol::FitProgress* progress = nullptr);
  void publish_state_snapshot();
  void publish_periodic_state_if_due();
  void set_last_error(const control::TransitionResult& failure);

  struct ReadBatch {
    std::vector<synapse::BroadbandFrame> frames;
    std::size_t received_message_count = 0;
    std::size_t parsed_message_count = 0;
    std::size_t parse_error_count = 0;
  };

  // Pull and parse every frame from one receive_multipart() result. Frames are
  // returned in wire order; false means no valid frame was available.
  bool read_frames(ReadBatch& batch);
  void maybe_log_reader_diagnostics(const ReadBatch& batch);
  // Lazily size window/stride/featurizer/MLP once we know the channel layout.
  void initialize_pipeline(std::size_t upstream_channels);
  // Push one time-point (all channels) into the sliding window ring; when the
  // window advances by one stride, featurise + capture/classify.
  void ingest_sample(const std::vector<int32_t>& frame_data, uint64_t timestamp_ns);
  // Featurise the current window, route to capture and/or classify.
  void process_window(uint64_t window_end_timestamp_ns);
  // Start a queued training pass and drain_fit_events() handles worker output.
  void maybe_fit();
  void publish_class(const std::vector<float>& probs, uint64_t timestamp_ns);

  AppConfig cfg_;
  synapse::ApplicationNodeConfig application_config_;

  // ---- queued control state ----
  control::ControlCommandQueue control_queue_;
  control::ActiveTarget active_target_;
  SourceMode mode_ = SourceMode::kSampling;
  bool fit_requested_ = false;
  bool state_subscription_enabled_ = false;
  bool model_ready_ = false;
  broadband_mode_switch::v1::ModelPhase model_phase_ =
      broadband_mode_switch::v1::MODEL_IDLE;
  bool model_has_source_collection_ = false;
  std::uint32_t model_source_collection_ = 0;
  std::uint64_t model_source_generation_ = 0;
  std::uint32_t model_epoch_ = 0;
  std::uint32_t model_total_epochs_ = 0;
  float model_loss_ = 0.0f;
  float model_accuracy_ = 0.0f;
  std::uint64_t model_duration_ms_ = 0;
  std::chrono::steady_clock::time_point fit_started_at_;

  std::uint64_t state_version_ = 0;
  bool has_last_error_ = false;
  protocol::Error last_error_;

  // ---- pipeline state (main-thread owned unless noted) ----
  bool pipeline_ready_ = false;
  bool pipeline_error_ = false;
  std::string pipeline_error_message_;
  bool source_connected_ = false;
  bool have_last_source_frame_wall_time_ = false;
  std::chrono::steady_clock::time_point last_source_frame_wall_time_;
  bool have_last_state_publication_ = false;
  std::chrono::steady_clock::time_point last_state_publication_;
  const std::chrono::milliseconds state_publication_period_{500};
  const std::chrono::seconds source_disconnect_timeout_{1};
  std::size_t upstream_channels_ = 0;
  std::size_t featurized_channels_ = 0;
  std::vector<std::size_t> channel_map_;  // featurized index -> upstream index
  std::size_t window_samples_ = 0;
  std::size_t stride_samples_ = 0;

  // Sliding per-channel window buffers ([featurized_channels_][window_samples_]
  // ring) plus bookkeeping for the stride trigger.
  std::vector<std::vector<float>> window_ring_;
  std::size_t ring_pos_ = 0;
  std::size_t samples_seen_ = 0;
  std::size_t samples_since_stride_ = 0;

  std::unique_ptr<MpfFeaturizer> featurizer_;

  // Collection/model access is guarded by model_mutex_. Collection snapshots
  // are copied under this lock, while FitWorker trains an independent
  // candidate without holding it. The main loop swaps a successful candidate
  // into inference under the same lock.
  std::mutex model_mutex_;
  CollectionStore buffers_;
  Mlp mlp_;
  FitWorker fit_worker_;
  std::optional<FitWorker::Request> pending_fit_;
  protocol::ControlCommand fit_command_;

  // Synthetic generator (only used in SYNTHETIC mode).
  std::unique_ptr<SyntheticSource> synthetic_;
  uint64_t synthetic_seq_ = 0;
  uint64_t synthetic_start_ns_ = 0;

  // Frame-drop detection on the upstream reader.
  uint64_t last_sequence_number_ = 0;
  bool have_last_sequence_ = false;
  uint64_t receive_batch_count_ = 0;
  uint64_t received_message_count_ = 0;
  uint64_t parsed_message_count_ = 0;
  uint64_t parse_error_count_ = 0;
  uint64_t forwarded_frame_count_ = 0;
  bool have_last_reader_diagnostics_log_ = false;
  std::chrono::steady_clock::time_point last_reader_diagnostics_log_;
};

}  // namespace app
