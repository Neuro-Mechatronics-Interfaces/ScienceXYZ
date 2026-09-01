#include "wireless_source_adapter.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace app::wireless {

namespace {

using sciencexyz::wireless::v1::WirelessBatch;

std::size_t bytes_per_sample(sciencexyz::wireless::v1::SampleFormat format) {
  switch (format) {
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE:
      return sizeof(std::int16_t);
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_INT32_LE:
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE:
      return sizeof(std::int32_t);
    default:
      return 0;
  }
}

std::uint64_t saturating_add(std::uint64_t left, std::uint64_t right) {
  return UINT64_MAX - left < right ? UINT64_MAX : left + right;
}

}  // namespace

WirelessSourceAdapter::WirelessSourceAdapter(AdapterConfig config)
    : config_(std::move(config)), clock_(config_.clock) {
  if (config_.source_id.empty() || config_.expected_sample_rate.numerator_hz == 0 ||
      config_.expected_sample_rate.denominator == 0 ||
      config_.expected_channel_count == 0 || config_.max_batch_samples == 0 ||
      config_.queue_capacity_batches == 0 ||
      bytes_per_sample(config_.expected_sample_format) == 0) {
    construction_error_ = "invalid wireless source adapter configuration";
  }
}

bool WirelessSourceAdapter::validate_batch(const AcceptedBatch& accepted,
                                           std::string& detail) const {
  const auto& batch = accepted.batch;
  if (batch.source_id() != config_.source_id) {
    detail = "source_id does not match adapter configuration";
    return false;
  }
  if (!config_.topic.empty() && accepted.topic != config_.topic) {
    detail = "topic does not match adapter configuration";
    return false;
  }
  if (batch.contract_version() != 1 || batch.boot_session_id().empty() ||
      batch.sample_count() == 0 || batch.sample_count() > config_.max_batch_samples ||
      batch.channel_count() != config_.expected_channel_count ||
      batch.shape_size() != 2 || batch.shape(0) != batch.sample_count() ||
      batch.shape(1) != batch.channel_count() ||
      batch.sample_format() != config_.expected_sample_format ||
      batch.sample_rate_numerator_hz() != config_.expected_sample_rate.numerator_hz ||
      batch.sample_rate_denominator() != config_.expected_sample_rate.denominator ||
      batch.source_tick_frequency_numerator_hz() == 0 ||
      batch.source_tick_frequency_denominator() == 0) {
    detail = "batch identity, shape, format, rate, or source clock is invalid";
    return false;
  }
  const auto element_size = bytes_per_sample(batch.sample_format());
  const auto sample_count = static_cast<std::uint64_t>(batch.sample_count());
  const auto channel_count = static_cast<std::uint64_t>(batch.channel_count());
  if (channel_count != 0 && sample_count > UINT64_MAX / channel_count) {
    detail = "sample shape overflows the v1 payload size calculation";
    return false;
  }
  const auto value_count = sample_count * channel_count;
  if (element_size != 0 && value_count > UINT64_MAX / element_size) {
    detail = "sample shape overflows the v1 payload size calculation";
    return false;
  }
  const auto expected = value_count * element_size;
  if (batch.payload().size() != expected) {
    detail = "payload length does not match sample-major shape and format";
    return false;
  }
  return true;
}

void WirelessSourceAdapter::add_diagnostic(
    AdapterDiagnostic diagnostic, std::vector<AdapterDiagnostic>* batch_diagnostics) {
  if (batch_diagnostics != nullptr) batch_diagnostics->push_back(diagnostic);
  if (diagnostics_.size() == kDiagnosticHistoryCapacity) diagnostics_.pop_front();
  diagnostics_.push_back(std::move(diagnostic));
}

void WirelessSourceAdapter::add_gap_diagnostic(
    AdapterDiagnosticKind kind, const WirelessBatch& batch, std::uint64_t observed,
    std::uint64_t expected, std::uint64_t count, std::string detail,
    std::vector<AdapterDiagnostic>& batch_diagnostics) {
  add_diagnostic(AdapterDiagnostic{kind, 0, config_.source_id,
                                   batch.boot_session_id(), observed, expected, count,
                                   std::move(detail)},
                 &batch_diagnostics);
}

void WirelessSourceAdapter::remember_sequence(std::uint64_t batch_sequence) {
  if (sequence_history_set_.insert(batch_sequence).second) {
    sequence_history_.push_back(batch_sequence);
    if (sequence_history_.size() > kSequenceHistoryCapacity) {
      sequence_history_set_.erase(sequence_history_.front());
      sequence_history_.pop_front();
    }
  }
}

std::uint64_t WirelessSourceAdapter::sample_tick(const WirelessBatch& batch,
                                                 std::uint32_t sample_index) {
  const long double numerator =
      static_cast<long double>(batch.source_tick_frequency_numerator_hz()) *
      static_cast<long double>(batch.sample_rate_denominator());
  const long double denominator =
      static_cast<long double>(batch.source_tick_frequency_denominator()) *
      static_cast<long double>(batch.sample_rate_numerator_hz());
  const long double value = static_cast<long double>(batch.first_source_tick()) +
                            static_cast<long double>(sample_index) * numerator / denominator;
  if (value >= static_cast<long double>(UINT64_MAX)) return UINT64_MAX;
  return value <= 0.0L ? 0 : static_cast<std::uint64_t>(std::llround(value));
}

bool WirelessSourceAdapter::submit(AcceptedBatch batch,
                                    std::uint64_t host_receive_time_ns) {
  if (!valid()) return false;
  std::string detail;
  if (!validate_batch(batch, detail)) {
    add_diagnostic(AdapterDiagnostic{AdapterDiagnosticKind::kInvalidBatch,
                                     0,
                                     config_.source_id,
                                     batch.batch.boot_session_id(),
                                     0,
                                     0,
                                     1,
                                     std::move(detail)});
    return false;
  }

  const auto& source_batch = batch.batch;
  std::vector<AdapterDiagnostic> batch_diagnostics;
  if (!have_session_ || boot_session_id_ != source_batch.boot_session_id()) {
    if (have_session_) {
      add_gap_diagnostic(AdapterDiagnosticKind::kSessionReset, source_batch, 0, 0, 1,
                         "new boot_session_id starts a new clock and sequence epoch",
                         batch_diagnostics);
      clock_.begin_new_epoch(source_batch.first_source_tick(), host_receive_time_ns);
    }
    have_session_ = true;
    boot_session_id_ = source_batch.boot_session_id();
    have_last_batch_ = false;
    sequence_history_.clear();
    sequence_history_set_.clear();
  }

  if (sequence_history_set_.contains(source_batch.batch_sequence())) {
    add_gap_diagnostic(AdapterDiagnosticKind::kDuplicateBatch, source_batch,
                       source_batch.batch_sequence(), last_batch_sequence_, 1,
                       "duplicate batch is deduplicated by session and sequence",
                       batch_diagnostics);
    return false;
  }

  bool has_gap = false;
  if (have_last_batch_) {
    if (source_batch.batch_sequence() > last_batch_sequence_ +
                                           (last_batch_sequence_ == UINT64_MAX ? 0 : 1)) {
      has_gap = true;
      add_gap_diagnostic(AdapterDiagnosticKind::kBatchGap, source_batch,
                         source_batch.batch_sequence(), last_batch_sequence_ + 1,
                         source_batch.batch_sequence() - last_batch_sequence_ - 1,
                         "receiver-observed missing batch sequence", batch_diagnostics);
    } else if (source_batch.batch_sequence() < last_batch_sequence_) {
      add_gap_diagnostic(AdapterDiagnosticKind::kReorderedBatch, source_batch,
                         source_batch.batch_sequence(), last_batch_sequence_, 1,
                         "batch arrived below the session high-water mark",
                         batch_diagnostics);
    }
    if (source_batch.first_sample_sequence() > last_sample_end_) {
      has_gap = true;
      add_gap_diagnostic(AdapterDiagnosticKind::kSampleGap, source_batch,
                         source_batch.first_sample_sequence(), last_sample_end_,
                         source_batch.first_sample_sequence() - last_sample_end_,
                         "receiver-observed missing sample sequence", batch_diagnostics);
    } else if (source_batch.first_sample_sequence() < last_sample_end_) {
      add_gap_diagnostic(AdapterDiagnosticKind::kReorderedSamples, source_batch,
                         source_batch.first_sample_sequence(), last_sample_end_, 1,
                         "sample range arrived below the expected high-water mark",
                         batch_diagnostics);
    }
  }
  if (has_gap && config_.gap_policy == GapPolicy::kRejectSourceOnGap) return false;

  remember_sequence(source_batch.batch_sequence());
  if (!have_last_batch_ || source_batch.batch_sequence() > last_batch_sequence_) {
    have_last_batch_ = true;
    last_batch_sequence_ = source_batch.batch_sequence();
    last_sample_end_ = saturating_add(source_batch.first_sample_sequence(),
                                      source_batch.sample_count());
  }

  NormalizedWirelessBatch normalized;
  normalized.host_receive_time_ns = host_receive_time_ns;
  normalized.clock_model = clock_.current_model().locked
                               ? std::optional<ClockModel>(clock_.current_model())
                               : std::nullopt;
  normalized.diagnostics = batch_diagnostics;
  if (!normalized.clock_model.has_value()) {
    add_gap_diagnostic(AdapterDiagnosticKind::kUnboundedClock, source_batch, 0, 0, 1,
                       "no affine model with at least two sync samples is available",
                       normalized.diagnostics);
  }
  normalized.sample_times.reserve(source_batch.sample_count());
  for (std::uint32_t i = 0; i < source_batch.sample_count(); ++i) {
    const auto tick = sample_tick(source_batch, i);
    const auto sample_sequence = saturating_add(source_batch.first_sample_sequence(), i);
    normalized.sample_times.push_back(
        NormalizedSampleTime{i, sample_sequence, tick,
                             clock_.map_source_tick(
                                 static_cast<long double>(source_batch.first_source_tick()) +
                                     static_cast<long double>(i) *
                                         static_cast<long double>(source_batch.source_tick_frequency_numerator_hz()) *
                                         static_cast<long double>(source_batch.sample_rate_denominator()) /
                                         (static_cast<long double>(source_batch.source_tick_frequency_denominator()) *
                                         static_cast<long double>(source_batch.sample_rate_numerator_hz())),
                                 host_receive_time_ns)
                                     .interval});
  }
  normalized.accepted = std::move(batch);

  if (queue_.size() == config_.queue_capacity_batches) {
    add_diagnostic(AdapterDiagnostic{AdapterDiagnosticKind::kQueueOverflow,
                                     0,
                                     config_.source_id,
                                     source_batch.boot_session_id(),
                                     source_batch.batch_sequence(),
                                     config_.queue_capacity_batches,
                                     1,
                                     "valid batch dropped because adapter queue is full"},
                   &normalized.diagnostics);
    return false;
  }
  queue_.push_back(std::move(normalized));
  return true;
}

std::optional<NormalizedWirelessBatch> WirelessSourceAdapter::pop_next() {
  if (queue_.empty()) return std::nullopt;
  NormalizedWirelessBatch result = std::move(queue_.front());
  queue_.pop_front();
  return result;
}

std::vector<AdapterDiagnostic> WirelessSourceAdapter::drain_diagnostics() {
  std::vector<AdapterDiagnostic> result;
  result.reserve(diagnostics_.size());
  while (!diagnostics_.empty()) {
    result.push_back(std::move(diagnostics_.front()));
    diagnostics_.pop_front();
  }
  return result;
}

MultiSourceAdapter::MultiSourceAdapter(std::vector<AdapterConfig> configs,
                                       std::size_t aggregate_queue_capacity)
    : aggregate_queue_capacity_(aggregate_queue_capacity) {
  if (configs.empty() || configs.size() > kMaxSources ||
      aggregate_queue_capacity == 0) {
    construction_error_ = "multi-source adapter requires one to four bounded instances";
    return;
  }
  for (std::size_t i = 0; i < configs.size(); ++i) {
    for (std::size_t j = 0; j < i; ++j) {
      if (configs[i].source_id == configs[j].source_id ||
          (!configs[i].topic.empty() && configs[i].topic == configs[j].topic)) {
        construction_error_ = "multi-source adapter identities must be unique";
      }
    }
    auto adapter = std::make_unique<WirelessSourceAdapter>(std::move(configs[i]));
    if (!adapter->valid()) construction_error_ = adapter->construction_error();
    adapters_.push_back(std::move(adapter));
  }
}

void MultiSourceAdapter::add_diagnostic(AdapterDiagnostic diagnostic) {
  if (diagnostics_.size() == kDiagnosticHistoryCapacity) diagnostics_.pop_front();
  diagnostics_.push_back(std::move(diagnostic));
}

bool MultiSourceAdapter::submit(AcceptedBatch batch,
                                std::uint64_t host_receive_time_ns) {
  if (!valid()) return false;
  for (std::size_t i = 0; i < adapters_.size(); ++i) {
    if (batch.batch.source_id() == adapters_[i]->source_id()) {
      if (queued_batches_ == aggregate_queue_capacity_) {
        add_diagnostic(AdapterDiagnostic{AdapterDiagnosticKind::kQueueOverflow,
                                         i,
                                         batch.batch.source_id(),
                                         batch.batch.boot_session_id(),
                                         batch.batch.batch_sequence(),
                                         aggregate_queue_capacity_,
                                         1,
                                         "valid batch dropped because aggregate adapter queue is full"});
        return false;
      }
      if (!adapters_[i]->submit(std::move(batch), host_receive_time_ns)) return false;
      ++queued_batches_;
      return true;
    }
  }
  add_diagnostic(AdapterDiagnostic{AdapterDiagnosticKind::kUnknownSource,
                                   0,
                                   batch.batch.source_id(),
                                   batch.batch.boot_session_id(),
                                   0,
                                   0,
                                   1,
                                   "no configured adapter matches source identity"});
  return false;
}

std::optional<NormalizedWirelessBatch> MultiSourceAdapter::pop_next() {
  if (!valid() || queued_batches_ == 0) return std::nullopt;
  for (std::size_t offset = 0; offset < adapters_.size(); ++offset) {
    const auto index = (pop_cursor_ + offset) % adapters_.size();
    if (auto result = adapters_[index]->pop_next(); result.has_value()) {
      --queued_batches_;
      pop_cursor_ = (index + 1) % adapters_.size();
      return result;
    }
  }
  return std::nullopt;
}

std::optional<ClockObservationResult> MultiSourceAdapter::observe_clock_sync(
    const std::string& source_id, const ClockSyncSample& sample) {
  for (const auto& adapter : adapters_) {
    if (adapter->source_id() == source_id) return adapter->observe_clock_sync(sample);
  }
  return std::nullopt;
}

std::vector<AdapterDiagnostic> MultiSourceAdapter::drain_diagnostics() {
  std::vector<AdapterDiagnostic> result;
  while (!diagnostics_.empty()) {
    result.push_back(std::move(diagnostics_.front()));
    diagnostics_.pop_front();
  }
  for (std::size_t index = 0; index < adapters_.size(); ++index) {
    auto source_diagnostics = adapters_[index]->drain_diagnostics();
    for (auto& diagnostic : source_diagnostics) diagnostic.source_index = index;
    result.insert(result.end(), source_diagnostics.begin(), source_diagnostics.end());
  }
  return result;
}

}  // namespace app::wireless
