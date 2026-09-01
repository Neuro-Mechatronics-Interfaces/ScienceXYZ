#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace app::wireless {

struct ClockSyncSample {
  std::uint64_t source_tick = 0;
  std::uint64_t reference_time_ns = 0;
  std::uint64_t round_trip_time_ns = 0;
  std::uint64_t uncertainty_ns = 0;
  std::optional<std::uint64_t> source_sequence;
};

enum class ClockObservationResult {
  kAccepted,
  kReordered,
  kResetRequired,
  kWrapped,
};

struct ClockEstimatorConfig {
  double nominal_source_tick_frequency_hz = 1'000'000.0;
  std::optional<std::uint64_t> source_tick_wrap_modulus;
  std::size_t max_sync_samples = 128;
  std::size_t max_epochs = 16;
  std::uint64_t source_tick_quantization_ns = 0;
  std::uint64_t timestamp_jitter_ns = 0;
  std::uint64_t sync_asymmetry_ns = 0;
  std::uint64_t batching_ambiguity_ns = 0;
  std::uint64_t rounding_ns = 1;
  double drift_uncertainty_ppm = 5.0;
  std::uint64_t max_model_age_ns = 5'000'000'000ULL;
};

struct ClockUncertaintyBudget {
  std::uint64_t quantization_ns = 0;
  std::uint64_t timestamp_jitter_ns = 0;
  std::uint64_t sync_dispersion_ns = 0;
  std::uint64_t sync_asymmetry_ns = 0;
  std::uint64_t fit_residual_ns = 0;
  std::uint64_t drift_extrapolation_ns = 0;
  std::uint64_t batching_ambiguity_ns = 0;
  std::uint64_t rounding_ns = 0;

  std::uint64_t total_ns() const;
};

struct ClockModel {
  std::uint64_t epoch_id = 0;
  std::uint64_t model_id = 0;
  std::uint64_t valid_from_source_tick = 0;
  std::uint64_t valid_from_reference_time_ns = 0;
  std::optional<std::uint64_t> valid_until_reference_time_ns;
  std::uint64_t last_update_reference_time_ns = 0;
  std::uint64_t last_update_age_ns = 0;
  double slope_ns_per_source_tick = 0.0;
  double offset_ns_at_anchor = 0.0;
  double drift_ppm = 0.0;
  std::size_t sync_sample_count = 0;
  std::uint64_t rtt_min_ns = 0;
  std::uint64_t rtt_median_ns = 0;
  std::uint64_t rtt_max_ns = 0;
  double residual_rms_ns = 0.0;
  double max_residual_ns = 0.0;
  ClockUncertaintyBudget uncertainty;
  bool locked = false;
};

struct TimeInterval {
  std::int64_t estimated_reference_time_ns = 0;
  std::int64_t lower_bound_ns = 0;
  std::int64_t upper_bound_ns = 0;
  std::uint64_t epsilon_ns = 0;
};

struct MappedTime {
  std::uint64_t epoch_id = 0;
  std::int64_t estimated_reference_time_ns = 0;
  std::uint64_t epsilon_ns = UINT64_MAX;
  bool bounded = false;
  std::optional<TimeInterval> interval;
};

class AffineClockEstimator {
 public:
  explicit AffineClockEstimator(ClockEstimatorConfig config = {});

  ClockObservationResult observe(const ClockSyncSample& sample);

  // Start a new source-clock epoch. The previous model remains in epochs().
  // A source boot/session reset must use this instead of stitching clocks.
  void begin_new_epoch(std::uint64_t first_source_tick,
                       std::uint64_t reference_time_ns = 0);

  MappedTime map_source_tick(long double source_tick,
                             std::optional<std::uint64_t> reference_now_ns =
                                 std::nullopt) const;
  MappedTime map_source_tick(std::uint64_t source_tick,
                             std::optional<std::uint64_t> reference_now_ns =
                                 std::nullopt) const {
    return map_source_tick(static_cast<long double>(source_tick),
                           reference_now_ns);
  }

  const ClockEstimatorConfig& config() const { return config_; }
  const ClockModel& current_model() const { return current_model_; }
  const std::vector<ClockModel>& epochs() const { return epochs_; }
  std::size_t accepted_sample_count() const { return samples_.size(); }

 private:
  struct FitSample {
    long double source_tick = 0.0L;
    std::uint64_t reference_time_ns = 0;
    std::uint64_t round_trip_time_ns = 0;
    std::uint64_t uncertainty_ns = 0;
  };

  static std::uint64_t saturating_add(std::uint64_t left,
                                      std::uint64_t right);
  static std::uint64_t round_to_u64(long double value);
  static std::int64_t round_to_i64(long double value);
  static long double evaluate(const ClockModel& model, long double source_tick);

  void refit();
  void update_current_epoch_snapshot();
  long double unwrap_tick(std::uint64_t source_tick,
                          ClockObservationResult& result);

  ClockEstimatorConfig config_;
  std::vector<FitSample> samples_;
  std::vector<ClockModel> epochs_;
  ClockModel current_model_;
  std::uint64_t next_model_id_ = 1;
  std::uint64_t wrap_count_ = 0;
  bool have_last_raw_tick_ = false;
  std::uint64_t last_raw_tick_ = 0;
  bool have_last_source_sequence_ = false;
  std::uint64_t last_source_sequence_ = 0;
};

}  // namespace app::wireless
