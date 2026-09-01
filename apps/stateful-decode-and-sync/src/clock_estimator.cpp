#include "clock_estimator.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace app::wireless {

namespace {

constexpr long double kNsPerSecond = 1'000'000'000.0L;

std::uint64_t abs_difference(std::uint64_t left, std::uint64_t right) {
  return left >= right ? left - right : right - left;
}

}  // namespace

std::uint64_t ClockUncertaintyBudget::total_ns() const {
  const std::uint64_t values[] = {quantization_ns, timestamp_jitter_ns,
                                  sync_dispersion_ns, sync_asymmetry_ns,
                                  fit_residual_ns, drift_extrapolation_ns,
                                  batching_ambiguity_ns, rounding_ns};
  std::uint64_t total = 0;
  for (const auto value : values) {
    if (UINT64_MAX - total < value) return UINT64_MAX;
    total += value;
  }
  return total;
}

AffineClockEstimator::AffineClockEstimator(ClockEstimatorConfig config)
    : config_(std::move(config)) {
  if (!(config_.nominal_source_tick_frequency_hz > 0.0) ||
      !std::isfinite(config_.nominal_source_tick_frequency_hz) ||
      config_.max_sync_samples < 2 || config_.max_epochs == 0 ||
      !std::isfinite(config_.drift_uncertainty_ppm) ||
      config_.drift_uncertainty_ppm < 0.0) {
    throw std::invalid_argument("invalid affine clock estimator configuration");
  }
  current_model_.epoch_id = 1;
  current_model_.model_id = next_model_id_++;
  current_model_.slope_ns_per_source_tick =
      static_cast<double>(kNsPerSecond /
                          static_cast<long double>(
                              config_.nominal_source_tick_frequency_hz));
}

std::uint64_t AffineClockEstimator::saturating_add(std::uint64_t left,
                                                   std::uint64_t right) {
  return UINT64_MAX - left < right ? UINT64_MAX : left + right;
}

std::uint64_t AffineClockEstimator::round_to_u64(long double value) {
  if (!(value > 0.0L)) return 0;
  if (value >= static_cast<long double>(UINT64_MAX)) return UINT64_MAX;
  return static_cast<std::uint64_t>(std::llround(value));
}

std::int64_t AffineClockEstimator::round_to_i64(long double value) {
  constexpr long double kMin = static_cast<long double>(INT64_MIN);
  constexpr long double kMax = static_cast<long double>(INT64_MAX);
  if (value <= kMin) return INT64_MIN;
  if (value >= kMax) return INT64_MAX;
  return static_cast<std::int64_t>(std::llround(value));
}

long double AffineClockEstimator::evaluate(const ClockModel& model,
                                           long double source_tick) {
  return static_cast<long double>(model.valid_from_reference_time_ns) +
         static_cast<long double>(model.slope_ns_per_source_tick) *
             (source_tick - static_cast<long double>(model.valid_from_source_tick));
}

long double AffineClockEstimator::unwrap_tick(
    std::uint64_t source_tick, ClockObservationResult& result) {
  result = ClockObservationResult::kAccepted;
  if (have_last_raw_tick_ && source_tick < last_raw_tick_) {
    if (config_.source_tick_wrap_modulus.has_value() &&
        last_raw_tick_ - source_tick >
            *config_.source_tick_wrap_modulus / 2) {
      if (wrap_count_ == UINT64_MAX) {
        result = ClockObservationResult::kResetRequired;
        return static_cast<long double>(source_tick);
      }
      ++wrap_count_;
      result = ClockObservationResult::kWrapped;
    } else {
      result = ClockObservationResult::kResetRequired;
      return static_cast<long double>(source_tick);
    }
  }
  last_raw_tick_ = source_tick;
  have_last_raw_tick_ = true;
  const long double modulus = config_.source_tick_wrap_modulus.has_value()
                                  ? static_cast<long double>(
                                        *config_.source_tick_wrap_modulus)
                                  : 0.0L;
  return static_cast<long double>(source_tick) +
         static_cast<long double>(wrap_count_) * modulus;
}

ClockObservationResult AffineClockEstimator::observe(
    const ClockSyncSample& sample) {
  if (sample.source_sequence.has_value() && have_last_source_sequence_ &&
      *sample.source_sequence < last_source_sequence_) {
    return ClockObservationResult::kReordered;
  }

  ClockObservationResult result = ClockObservationResult::kAccepted;
  const long double source_tick = unwrap_tick(sample.source_tick, result);
  if (result == ClockObservationResult::kResetRequired) return result;

  if (sample.source_sequence.has_value()) {
    last_source_sequence_ = *sample.source_sequence;
    have_last_source_sequence_ = true;
  }
  samples_.push_back(FitSample{source_tick, sample.reference_time_ns,
                               sample.round_trip_time_ns,
                               sample.uncertainty_ns});
  if (samples_.size() > config_.max_sync_samples) samples_.erase(samples_.begin());
  refit();
  return result;
}

void AffineClockEstimator::begin_new_epoch(std::uint64_t first_source_tick,
                                           std::uint64_t reference_time_ns) {
  if (current_model_.locked) {
    current_model_.valid_until_reference_time_ns =
        current_model_.last_update_reference_time_ns;
    epochs_.push_back(current_model_);
    if (epochs_.size() > config_.max_epochs) epochs_.erase(epochs_.begin());
  }
  current_model_ = ClockModel{};
  current_model_.epoch_id = current_model_.epoch_id + 1;
  current_model_.model_id = next_model_id_++;
  current_model_.valid_from_source_tick = first_source_tick;
  current_model_.valid_from_reference_time_ns = reference_time_ns;
  current_model_.slope_ns_per_source_tick =
      static_cast<double>(kNsPerSecond /
                          static_cast<long double>(
                              config_.nominal_source_tick_frequency_hz));
  samples_.clear();
  wrap_count_ = 0;
  have_last_raw_tick_ = false;
  have_last_source_sequence_ = false;
}

void AffineClockEstimator::refit() {
  if (samples_.empty()) return;

  const long double x0 = samples_.front().source_tick;
  const long double y0 = static_cast<long double>(samples_.front().reference_time_ns);
  long double mean_x = 0.0L;
  long double mean_y = 0.0L;
  for (const auto& sample : samples_) {
    mean_x += sample.source_tick - x0;
    mean_y += static_cast<long double>(sample.reference_time_ns) - y0;
  }
  mean_x /= static_cast<long double>(samples_.size());
  mean_y /= static_cast<long double>(samples_.size());

  long double denominator = 0.0L;
  long double numerator = 0.0L;
  for (const auto& sample : samples_) {
    const long double dx = (sample.source_tick - x0) - mean_x;
    const long double dy = (static_cast<long double>(sample.reference_time_ns) - y0) - mean_y;
    denominator += dx * dx;
    numerator += dx * dy;
  }
  if (samples_.size() < 2 || denominator <= 0.0L) {
    current_model_.sync_sample_count = samples_.size();
    current_model_.last_update_reference_time_ns =
        samples_.back().reference_time_ns;
    current_model_.locked = false;
    return;
  }

  const long double slope = numerator / denominator;
  if (!(slope > 0.0L) || !std::isfinite(static_cast<double>(slope))) {
    current_model_.locked = false;
    return;
  }
  const long double anchor_source = samples_.front().source_tick;
  const long double anchor_reference = y0;
  current_model_.valid_from_source_tick = round_to_u64(anchor_source);
  current_model_.valid_from_reference_time_ns = samples_.front().reference_time_ns;
  current_model_.last_update_reference_time_ns = samples_.back().reference_time_ns;
  current_model_.slope_ns_per_source_tick = static_cast<double>(slope);
  current_model_.offset_ns_at_anchor = static_cast<double>(anchor_reference);
  const long double nominal_slope =
      kNsPerSecond / static_cast<long double>(config_.nominal_source_tick_frequency_hz);
  current_model_.drift_ppm = static_cast<double>((slope / nominal_slope - 1.0L) * 1'000'000.0L);
  current_model_.sync_sample_count = samples_.size();

  std::vector<long double> residuals;
  residuals.reserve(samples_.size());
  std::vector<std::uint64_t> rtts;
  rtts.reserve(samples_.size());
  std::uint64_t max_uncertainty = 0;
  for (const auto& sample : samples_) {
    const long double predicted =
        anchor_reference + slope * (sample.source_tick - anchor_source);
    residuals.push_back(std::fabs(predicted -
                                  static_cast<long double>(sample.reference_time_ns)));
    rtts.push_back(sample.round_trip_time_ns);
    max_uncertainty = std::max(max_uncertainty, sample.uncertainty_ns);
  }
  const long double residual_square_sum = std::accumulate(
      residuals.begin(), residuals.end(), 0.0L,
      [](long double sum, long double residual) { return sum + residual * residual; });
  current_model_.residual_rms_ns = static_cast<double>(std::sqrt(
      residual_square_sum / static_cast<long double>(residuals.size())));
  current_model_.max_residual_ns = static_cast<double>(
      *std::max_element(residuals.begin(), residuals.end()));

  std::sort(rtts.begin(), rtts.end());
  current_model_.rtt_min_ns = rtts.front();
  current_model_.rtt_median_ns = rtts[rtts.size() / 2];
  current_model_.rtt_max_ns = rtts.back();
  current_model_.uncertainty.quantization_ns = config_.source_tick_quantization_ns;
  if (current_model_.uncertainty.quantization_ns == 0) {
    current_model_.uncertainty.quantization_ns = round_to_u64(
        kNsPerSecond /
        (2.0L * static_cast<long double>(config_.nominal_source_tick_frequency_hz)));
  }
  current_model_.uncertainty.timestamp_jitter_ns = config_.timestamp_jitter_ns;
  current_model_.uncertainty.sync_dispersion_ns =
      current_model_.rtt_max_ns / 2 + max_uncertainty;
  current_model_.uncertainty.sync_asymmetry_ns = config_.sync_asymmetry_ns;
  current_model_.uncertainty.fit_residual_ns = round_to_u64(current_model_.max_residual_ns);
  current_model_.uncertainty.batching_ambiguity_ns = config_.batching_ambiguity_ns;
  current_model_.uncertainty.rounding_ns = config_.rounding_ns;
  current_model_.valid_until_reference_time_ns = saturating_add(
      current_model_.last_update_reference_time_ns, config_.max_model_age_ns);
  current_model_.model_id = next_model_id_++;
  current_model_.locked = true;
  update_current_epoch_snapshot();
}

void AffineClockEstimator::update_current_epoch_snapshot() {
  // epochs() stores completed epochs. The current epoch is exposed separately
  // through current_model(), so no duplicate snapshots are accumulated here.
}

MappedTime AffineClockEstimator::map_source_tick(
    long double source_tick, std::optional<std::uint64_t> reference_now_ns) const {
  MappedTime result;
  result.epoch_id = current_model_.epoch_id;
  if (!current_model_.locked) return result;

  const long double estimate = evaluate(current_model_, source_tick);
  result.estimated_reference_time_ns = round_to_i64(estimate);
  auto budget = current_model_.uncertainty;
  if (reference_now_ns.has_value() &&
      *reference_now_ns > current_model_.last_update_reference_time_ns) {
    const auto age = *reference_now_ns - current_model_.last_update_reference_time_ns;
    const long double extrapolation =
        static_cast<long double>(age) * config_.drift_uncertainty_ppm / 1'000'000.0L;
    budget.drift_extrapolation_ns = round_to_u64(extrapolation);
  }
  result.epsilon_ns = budget.total_ns();
  const bool age_valid =
      !reference_now_ns.has_value() ||
      *reference_now_ns <= saturating_add(current_model_.last_update_reference_time_ns,
                                          config_.max_model_age_ns);
  result.bounded = age_valid && result.epsilon_ns != UINT64_MAX;
  if (!result.bounded) return result;

  const auto epsilon = static_cast<long double>(result.epsilon_ns);
  result.interval = TimeInterval{result.estimated_reference_time_ns,
                                 round_to_i64(estimate - epsilon),
                                 round_to_i64(estimate + epsilon), result.epsilon_ns};
  return result;
}

}  // namespace app::wireless
