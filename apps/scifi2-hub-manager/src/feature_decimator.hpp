#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace scifi2_hub {

inline std::size_t next_power_of_two(std::size_t value) {
  if (value == 0) throw std::invalid_argument("next power of two requires a sample");
  std::size_t result = 1;
  while (result < value) {
    if (result > std::numeric_limits<std::size_t>::max() / 2) {
      throw std::overflow_error("next power of two exceeds size_t");
    }
    result <<= 1;
  }
  return result;
}

struct FeatureDecimationPlan {
  std::size_t factor = 1;
  double source_sample_rate_hz = 0.0;
  double feature_sample_rate_hz = 0.0;
  double passband_edge_hz = 0.0;
  double stopband_edge_hz = 0.0;
  std::size_t group_delay_source_samples = 0;
  std::vector<double> coefficients;
};

// Select the largest integer decimation that:
//   * leaves a guard band above the highest requested MPF frequency;
//   * divides the raw window and stride exactly, avoiding feature-clock drift.
// The anti-alias filter is an odd-length, unity-DC-gain Hamming-windowed sinc.
inline FeatureDecimationPlan make_feature_decimation_plan(
    double source_sample_rate_hz, double highest_feature_hz, double guard_ratio,
    std::size_t raw_window_samples, std::size_t raw_stride_samples) {
  if (!std::isfinite(source_sample_rate_hz) || source_sample_rate_hz <= 0.0 ||
      !std::isfinite(highest_feature_hz) || highest_feature_hz <= 0.0 ||
      highest_feature_hz > source_sample_rate_hz / 2.0 ||
      !std::isfinite(guard_ratio) || guard_ratio <= 1.0 || raw_window_samples == 0 ||
      raw_stride_samples == 0) {
    throw std::invalid_argument("invalid feature decimation parameters");
  }

  FeatureDecimationPlan plan;
  plan.source_sample_rate_hz = source_sample_rate_hz;
  plan.passband_edge_hz = highest_feature_hz;

  const double guarded_nyquist = highest_feature_hz * guard_ratio;
  const auto band_limited_factor = static_cast<std::size_t>(
      std::floor(source_sample_rate_hz / (2.0 * guarded_nyquist)));
  const std::size_t limit = std::max<std::size_t>(
      1, std::min(band_limited_factor, raw_window_samples));

  for (std::size_t candidate = limit; candidate > 1; --candidate) {
    if (raw_window_samples % candidate == 0 && raw_stride_samples % candidate == 0) {
      plan.factor = candidate;
      break;
    }
  }

  plan.feature_sample_rate_hz = source_sample_rate_hz / plan.factor;
  plan.stopband_edge_hz = plan.feature_sample_rate_hz / 2.0;
  if (plan.factor == 1) return plan;

  const double transition_hz = plan.stopband_edge_hz - highest_feature_hz;
  if (transition_hz <= 0.0) {
    throw std::invalid_argument("decimation leaves no anti-alias transition band");
  }

  // For a Hamming window, approximately 3.3 / normalized_transition taps
  // spans the transition. Round up to an odd length for an integer group delay.
  std::size_t tap_count = static_cast<std::size_t>(
      std::ceil(3.3 * source_sample_rate_hz / transition_hz));
  tap_count = std::max<std::size_t>(tap_count, 3);
  if (tap_count % 2 == 0) ++tap_count;
  plan.group_delay_source_samples = (tap_count - 1) / 2;
  plan.coefficients.resize(tap_count);

  const double cutoff_hz = 0.5 * (highest_feature_hz + plan.stopband_edge_hz);
  const double normalized_cutoff = cutoff_hz / source_sample_rate_hz;
  constexpr double kPi = 3.14159265358979323846;
  const double midpoint = static_cast<double>(plan.group_delay_source_samples);
  for (std::size_t index = 0; index < tap_count; ++index) {
    const double offset = static_cast<double>(index) - midpoint;
    const double ideal =
        offset == 0.0
            ? 2.0 * normalized_cutoff
            : std::sin(2.0 * kPi * normalized_cutoff * offset) / (kPi * offset);
    const double window = 0.54 -
                          0.46 * std::cos(2.0 * kPi * static_cast<double>(index) /
                                          static_cast<double>(tap_count - 1));
    plan.coefficients[index] = ideal * window;
  }
  const double gain =
      std::accumulate(plan.coefficients.begin(), plan.coefficients.end(), 0.0);
  for (auto& coefficient : plan.coefficients) coefficient /= gain;
  return plan;
}

// Streaming integer-rate FIR decimator for multi-channel BroadbandFrame data.
//
// This is the "oscilloscope average" stage: raw source samples are pushed in one
// at a time, anti-alias filtered by the plan's windowed-sinc FIR, and one
// decimated sample is emitted every `factor` raw samples once the filter has
// filled. Emitted metadata (sequence number, timestamp) is taken from the raw
// sample at the FIR group-delay center so the decimated stream carries the true
// mid-window time rather than the newest edge. The decimated payload is rounded
// back to int32 counts so a single averaged stream feeds both broadband_out and
// the decoder identically (no separate raw/decimated quantizations to reconcile).
//
// A factor==1 plan is a pass-through: every pushed sample is emitted unchanged
// with its own metadata. The filter runs in double precision; a channel count is
// bound on the first push and must stay constant.
class StreamingDecimator {
 public:
  struct Sample {
    std::vector<std::int32_t> values;
    std::uint64_t source_sequence_number = 0;
    std::uint64_t timestamp_ns = 0;
  };

  StreamingDecimator() = default;
  explicit StreamingDecimator(FeatureDecimationPlan plan) { reset(std::move(plan)); }

  const FeatureDecimationPlan& plan() const { return plan_; }
  std::size_t factor() const { return plan_.factor; }
  double feature_sample_rate_hz() const { return plan_.feature_sample_rate_hz; }

  // Rebind the plan and drop all filter state.
  void reset(FeatureDecimationPlan plan) {
    plan_ = std::move(plan);
    if (plan_.factor == 0) plan_.factor = 1;
    channels_ = 0;
    history_.clear();
    metadata_.clear();
    history_pos_ = 0;
    raw_samples_ = 0;
  }

  // Push one raw multi-channel sample. When a decimated sample is ready, write
  // it into `out` and return true; otherwise return false. For a factor==1 plan
  // this always returns true and copies the input through unchanged.
  bool push(const std::int32_t* values, std::size_t channel_count,
            std::uint64_t source_sequence_number, std::uint64_t timestamp_ns,
            Sample& out) {
    if (channels_ == 0) bind_channels(channel_count);
    if (plan_.factor == 1) {
      out.values.assign(values, values + channel_count);
      out.source_sequence_number = source_sequence_number;
      out.timestamp_ns = timestamp_ns;
      return true;
    }

    const auto taps = plan_.coefficients.size();
    for (std::size_t ch = 0; ch < channels_; ++ch) {
      history_[ch][history_pos_] =
          ch < channel_count ? static_cast<double>(values[ch]) : 0.0;
    }
    metadata_[history_pos_] = {source_sequence_number, timestamp_ns};
    ++raw_samples_;

    const bool filter_ready = raw_samples_ >= taps;
    const bool emit =
        filter_ready && (raw_samples_ - taps) % plan_.factor == 0;
    if (emit) {
      out.values.assign(channels_, 0);
      for (std::size_t ch = 0; ch < channels_; ++ch) {
        double acc = 0.0;
        for (std::size_t lag = 0; lag < taps; ++lag) {
          const auto index = (history_pos_ + taps - lag) % taps;
          acc += plan_.coefficients[lag] * history_[ch][index];
        }
        out.values[ch] = static_cast<std::int32_t>(std::llround(acc));
      }
      const auto center_index =
          (history_pos_ + taps - plan_.group_delay_source_samples) % taps;
      out.source_sequence_number = metadata_[center_index].source_sequence_number;
      out.timestamp_ns = metadata_[center_index].timestamp_ns;
    }
    history_pos_ = (history_pos_ + 1) % taps;
    return emit;
  }

 private:
  struct Metadata {
    std::uint64_t source_sequence_number = 0;
    std::uint64_t timestamp_ns = 0;
  };

  void bind_channels(std::size_t channel_count) {
    channels_ = channel_count;
    if (plan_.factor <= 1) return;
    const auto taps = plan_.coefficients.size();
    history_.assign(channels_, std::vector<double>(taps, 0.0));
    metadata_.assign(taps, {});
    history_pos_ = 0;
    raw_samples_ = 0;
  }

  FeatureDecimationPlan plan_;
  std::size_t channels_ = 0;
  std::vector<std::vector<double>> history_;
  std::vector<Metadata> metadata_;
  std::size_t history_pos_ = 0;
  std::size_t raw_samples_ = 0;
};

}  // namespace scifi2_hub
