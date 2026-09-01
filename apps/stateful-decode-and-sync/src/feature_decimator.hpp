#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <vector>

namespace app {

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

}  // namespace app
