#include "mode_switch_app.hpp"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <thread>

namespace app {

// Upstream broadband source node id (see config JSON).
static constexpr uint32_t kBroadbandNodeId = 1;

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

  // Control consumer taps. Each callback runs on its own thread and only
  // touches atomics, so no locking is required inside them.
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
// Tap callbacks (off-thread; atomics only)
// ---------------------------------------------------------------------------
void ModeSwitchApp::on_set_source_mode(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  if (v.empty() || !v[0].has_number_value()) {
    spdlog::warn("set_source_mode: expected [int mode]");
    return;
  }
  const int m = static_cast<int>(v[0].number_value());
  if (m != static_cast<int>(SourceMode::kSampling) &&
      m != static_cast<int>(SourceMode::kSynthetic)) {
    spdlog::warn("set_source_mode: invalid mode {}", m);
    return;
  }
  mode_.store(m);
  spdlog::info("set_source_mode -> {}", m == 0 ? "SAMPLING" : "SYNTHETIC");
}

void ModeSwitchApp::on_set_capture(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  if (v.size() < 2 || !v[0].has_number_value() || !v[1].has_number_value()) {
    spdlog::warn("set_capture: expected [int label, int enable]");
    return;
  }
  const int label = static_cast<int>(v[0].number_value());
  const bool enable = v[1].number_value() != 0.0;
  if (label < 0 || static_cast<std::size_t>(label) >= cfg_.num_classes) {
    spdlog::warn("set_capture: label {} out of range [0,{})", label, cfg_.num_classes);
    return;
  }
  active_label_.store(label);
  capture_enabled_.store(enable);
  spdlog::info("set_capture -> label={} enable={}", label, enable);
}

void ModeSwitchApp::on_fit_mlp(const google::protobuf::ListValue& msg) {
  const auto& v = msg.values();
  int epochs = -1;
  if (!v.empty() && v[0].has_number_value()) {
    epochs = static_cast<int>(v[0].number_value());
  }
  fit_epochs_override_.store(epochs);
  fit_requested_.store(true);
  spdlog::info("fit_mlp requested (epochs override={})", epochs);
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------
void ModeSwitchApp::main() {
  synapse::BroadbandFrame in_frame;
  std::vector<int32_t> synth_data;

  while (node_running_) {
    // Handle a pending training request before touching data.
    maybe_fit();

    const auto mode = static_cast<SourceMode>(mode_.load());

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

    // SAMPLING mode: forward the upstream frame unchanged. Never overwrite the
    // source timestamps (AGENTS.md).
    if (!read_one_frame(in_frame)) {
      continue;
    }
    publish_tap("broadband_out", in_frame);

    // Feed the pipeline from the real frame.
    const int n = in_frame.frame_data_size();
    std::vector<int32_t> data(n);
    for (int i = 0; i < n; ++i) data[i] = in_frame.frame_data(i);
    initialize_pipeline(static_cast<std::size_t>(n));
    ingest_sample(data, in_frame.timestamp_ns());
  }
}

bool ModeSwitchApp::read_one_frame(synapse::BroadbandFrame& frame) {
  auto messages = data_reader_->receive_multipart();
  if (messages.empty()) {
    std::this_thread::sleep_for(std::chrono::microseconds(1));
    return false;
  }
  bool got = false;
  for (auto& message : messages) {
    auto maybe = synapse::parse_protobuf_message<synapse::BroadbandFrame>(std::move(message));
    if (!maybe.has_value()) {
      spdlog::warn("Failed to parse broadband frame");
      continue;
    }
    frame = maybe.value();
    if (have_last_sequence_) {
      const uint64_t expected = last_sequence_number_ + 1;
      if (frame.sequence_number() != expected) {
        spdlog::warn("Dropped {} frames (expected seq {}, got {})",
                     static_cast<int64_t>(frame.sequence_number()) -
                         static_cast<int64_t>(expected),
                     expected, frame.sequence_number());
      }
    }
    last_sequence_number_ = frame.sequence_number();
    have_last_sequence_ = true;
    got = true;
  }
  // We overwrite `frame` per message and only keep the last; feeding one frame
  // per read call keeps the sliding window simple. Multipart batches are rare
  // for a per-sample broadband node.
  return got;
}

// ---------------------------------------------------------------------------
// Pipeline: windowing -> featurize -> capture/classify
// ---------------------------------------------------------------------------
void ModeSwitchApp::initialize_pipeline(std::size_t upstream_channels) {
  if (pipeline_ready_ || upstream_channels == 0) {
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
    buffers_.configure(cfg_.num_classes, cfg_.ring_capacity);
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
  spdlog::info(
      "Pipeline ready: {} featurized ch, window={} samp, stride={} samp, feature_dim={}",
      featurized_channels_, window_samples_, stride_samples_, featurizer_->feature_dim());
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
  const bool capturing = capture_enabled_.load();
  if (capturing) {
    const std::size_t label = static_cast<std::size_t>(active_label_.load());
    std::lock_guard<std::mutex> lock(model_mutex_);
    buffers_.append(label, feature);
    // Log per-class counts occasionally.
    static thread_local std::size_t log_ctr = 0;
    if ((++log_ctr % 50) == 0) {
      spdlog::info("capture label={} count={} total={}", label, buffers_.count(label),
                   buffers_.total());
    }
  }

  if (model_ready_.load()) {
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

void ModeSwitchApp::maybe_fit() {
  if (!fit_requested_.exchange(false)) {
    return;
  }
  if (!pipeline_ready_) {
    spdlog::warn("fit_mlp: pipeline not ready yet");
    return;
  }

  std::vector<std::vector<float>> features;
  std::vector<std::size_t> labels;
  float loss = 0.0f, acc = 0.0f;
  {
    std::lock_guard<std::mutex> lock(model_mutex_);
    buffers_.collect(features, labels);
    if (features.empty()) {
      spdlog::warn("fit_mlp: no captured windows; nothing to train");
      return;
    }
    const int override_epochs = fit_epochs_override_.load();
    Mlp::Config mcfg = mlp_.config();
    if (override_epochs > 0) mcfg.epochs = static_cast<std::size_t>(override_epochs);
    mlp_.init(mcfg);  // fresh init for a reproducible fit
    spdlog::info("fit_mlp: training on {} windows ({} classes), {} epochs, input_dim={}",
                 features.size(), mcfg.num_classes, mcfg.epochs, mcfg.input_dim);
    loss = mlp_.fit(features, labels, &acc);
  }

  if (loss < 0.0f) {
    spdlog::error("fit_mlp: training failed (malformed data)");
    return;
  }
  model_ready_.store(true);
  spdlog::info("fit_mlp: done. final loss={:.4f} accuracy={:.3f}", loss, acc);
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
