#include "wireless_ingress.hpp"

#include <algorithm>
#include <limits>
#include <numeric>
#include <utility>

namespace scifi2_hub::wireless {
namespace {

using sciencexyz::wireless::v1::SampleFormat;
using sciencexyz::wireless::v1::WirelessBatch;

std::size_t bytes_per_sample(SampleFormat format) {
  switch (format) {
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE:
      return 2;
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_INT32_LE:
    case sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE:
      return 4;
    default:
      return 0;
  }
}

bool same_rate(RationalRate left, RationalRate right) {
  if (left.numerator_hz == 0 || left.denominator == 0 ||
      right.numerator_hz == 0 || right.denominator == 0) {
    return false;
  }
  const auto left_gcd = std::gcd(left.numerator_hz, left.denominator);
  const auto right_gcd = std::gcd(right.numerator_hz, right.denominator);
  return left.numerator_hz / left_gcd == right.numerator_hz / right_gcd &&
         left.denominator / left_gcd == right.denominator / right_gcd;
}

bool multiply_fits(std::uint64_t left, std::uint64_t right, std::uint64_t& result) {
  if (right != 0 && left > std::numeric_limits<std::uint64_t>::max() / right) {
    return false;
  }
  result = left * right;
  return true;
}

}  // namespace

IngressMux::IngressMux(IngressConfig config,
                       std::vector<std::unique_ptr<NonblockingReader>> readers)
    : config_(std::move(config)), readers_(std::move(readers)) {
  if (!validate_config()) return;
  source_states_.resize(config_.sources.size());
  source_stats_.resize(config_.sources.size());
}

bool IngressMux::validate_config() {
  if (config_.sources.empty() || config_.sources.size() > kMaxSources) {
    construction_error_ = "wireless ingress requires one to four sources";
    return false;
  }
  if (readers_.size() != config_.sources.size()) {
    construction_error_ = "one independently owned reader is required per source";
    return false;
  }
  if (config_.queue_capacity_batches == 0 || config_.queue_capacity_batches > 4096 ||
      config_.max_batch_bytes < 1024 || config_.max_batch_bytes > 8388608) {
    construction_error_ = "mux queue or batch-byte limit is outside the v1 bounds";
    return false;
  }
  if (!std::all_of(readers_.begin(), readers_.end(),
                   [](const auto& reader) { return reader != nullptr; })) {
    construction_error_ = "wireless source reader ownership cannot contain null readers";
    return false;
  }
  std::unordered_set<std::string> source_ids;
  for (const auto& source : config_.sources) {
    if (source.source_id.empty() || source.source_id.size() > 64 ||
        source.topic.empty() || source.topic.size() > 80 ||
        !source_ids.insert(source.source_id).second ||
        source.expected_channel_count == 0 || source.expected_channel_count > 512 ||
        source.max_batch_samples == 0 || source.max_batch_samples > 65536 ||
        source.queue_capacity_batches == 0 || source.queue_capacity_batches > 1024 ||
        bytes_per_sample(source.expected_sample_format) == 0 ||
        source.expected_sample_rate.numerator_hz == 0 ||
        source.expected_sample_rate.denominator == 0) {
      construction_error_ = "invalid or duplicate wireless source configuration";
      return false;
    }
  }
  return true;
}

bool IngressMux::validate_batch(const SourceConfig& source, const std::string& serialized,
                                WirelessBatch& batch, std::string& detail) const {
  if (serialized.size() > config_.max_batch_bytes) {
    detail = "serialized protobuf exceeds mux max_batch_bytes";
    return false;
  }
  if (!batch.ParseFromString(serialized)) {
    detail = "protobuf parse failed";
    return false;
  }
  if (batch.contract_version() != 1) {
    detail = "unsupported contract_version";
    return false;
  }
  if (batch.source_id() != source.source_id) {
    detail = "payload source_id does not match configured source";
    return false;
  }
  if (!source.expected_gateway_id.empty() &&
      batch.gateway_id() != source.expected_gateway_id) {
    detail = "gateway_id does not match configured source";
    return false;
  }
  if (batch.boot_session_id().empty()) {
    detail = "boot_session_id is required";
    return false;
  }
  if (batch.sample_count() == 0 || batch.channel_count() == 0 ||
      batch.channel_count() != source.expected_channel_count ||
      batch.sample_count() > source.max_batch_samples) {
    detail = "sample or channel shape exceeds configured limits";
    return false;
  }
  if (batch.shape_size() != 2 || batch.shape(0) != batch.sample_count() ||
      batch.shape(1) != batch.channel_count()) {
    detail = "shape must be [sample_count, channel_count]";
    return false;
  }
  if (batch.sample_format() != source.expected_sample_format ||
      bytes_per_sample(batch.sample_format()) == 0) {
    detail = "unsupported or unexpected sample format";
    return false;
  }
  const auto rate = RationalRate{batch.sample_rate_numerator_hz(),
                                batch.sample_rate_denominator()};
  if (!same_rate(rate, source.expected_sample_rate)) {
    detail = "sample rate does not match configured rational rate";
    return false;
  }
  if (batch.source_tick_frequency_numerator_hz() == 0 ||
      batch.source_tick_frequency_denominator() == 0) {
    detail = "source tick frequency must be a nonzero rational rate";
    return false;
  }
  std::uint64_t sample_values = 0;
  std::uint64_t expected_bytes = 0;
  if (!multiply_fits(batch.sample_count(), batch.channel_count(), sample_values) ||
      !multiply_fits(sample_values, bytes_per_sample(batch.sample_format()),
                     expected_bytes) ||
      expected_bytes != batch.payload().size()) {
    detail = "payload byte length does not match shape and format";
    return false;
  }
  if (batch.channels_size() != 0) {
    if (batch.channels_size() != static_cast<int>(batch.channel_count())) {
      detail = "channel descriptors must cover every channel when present";
      return false;
    }
    for (int i = 0; i < batch.channels_size(); ++i) {
      if (batch.channels(i).index() != static_cast<std::uint32_t>(i)) {
        detail = "channel descriptor indices must be contiguous from zero";
        return false;
      }
    }
  }
  if (batch.payload_sha256().size() != 0 && batch.payload_sha256().size() != 32) {
    detail = "payload_sha256 must be exactly 32 bytes when present";
    return false;
  }
  if (!source.allow_replay && batch.replayed()) {
    detail = "replayed batch is disabled for this source";
    return false;
  }
  if (batch.sample_count() > 0 &&
      batch.first_sample_sequence() >
          std::numeric_limits<std::uint64_t>::max() - batch.sample_count()) {
    detail = "sample sequence range overflows uint64";
    return false;
  }
  return true;
}

void IngressMux::add_diagnostic(Diagnostic diagnostic) {
  if (diagnostics_.size() == kDiagnosticHistoryCapacity) diagnostics_.pop_front();
  diagnostics_.push_back(std::move(diagnostic));
}

void IngressMux::add_source_diagnostic(
    std::size_t source_index, DiagnosticKind kind, const WirelessBatch& batch,
    std::uint64_t observed, std::uint64_t expected, std::uint64_t count,
    std::string detail, std::vector<Diagnostic>& batch_diagnostics) {
  Diagnostic diagnostic{kind, source_index, config_.sources[source_index].source_id,
                        batch.boot_session_id(), observed, expected, count,
                        std::move(detail)};
  batch_diagnostics.push_back(diagnostic);
  add_diagnostic(std::move(diagnostic));
}

void IngressMux::remember_batch_sequence(SourceState& state, std::uint64_t sequence) {
  if (state.sequence_history_set.insert(sequence).second) {
    state.sequence_history.push_back(sequence);
    if (state.sequence_history.size() > kSequenceHistoryCapacity) {
      state.sequence_history_set.erase(state.sequence_history.front());
      state.sequence_history.pop_front();
    }
  }
}

std::optional<AcceptedBatch> IngressMux::validate_message(
    std::size_t source_index, const MultipartMessage& message,
    std::vector<Diagnostic>& diagnostics) {
  const auto& source = config_.sources[source_index];
  auto& state = source_states_[source_index];
  auto& stats = source_stats_[source_index];
  ++stats.envelopes_received;
  if (message.frames.size() != 2 || message.frames[0] != source.topic) {
    Diagnostic diagnostic{DiagnosticKind::kMalformedEnvelope,
                          source_index,
                          source.source_id,
                          "",
                          0,
                          2,
                          1,
                          "expected exactly [configured topic, serialized WirelessBatch]"};
    diagnostics.push_back(diagnostic);
    add_diagnostic(std::move(diagnostic));
    return std::nullopt;
  }

  AcceptedBatch accepted;
  accepted.source_index = source_index;
  accepted.topic = message.frames[0];
  std::string detail;
  if (!validate_batch(source, message.frames[1], accepted.batch, detail)) {
    Diagnostic diagnostic{DiagnosticKind::kMalformedBatch,
                          source_index,
                          source.source_id,
                          "",
                          0,
                          0,
                          1,
                          std::move(detail)};
    diagnostics.push_back(diagnostic);
    add_diagnostic(std::move(diagnostic));
    return std::nullopt;
  }

  const auto& batch = accepted.batch;
  if (!state.have_session || state.boot_session_id != batch.boot_session_id()) {
    if (state.have_session) {
      add_source_diagnostic(source_index, DiagnosticKind::kSessionReset, batch, 0, 0, 1,
                            "new boot_session_id starts a new sequence epoch", diagnostics);
    }
    state.have_session = true;
    state.boot_session_id = batch.boot_session_id();
    state.have_last_batch = false;
    state.source_rejected = false;
    state.sequence_history.clear();
    state.sequence_history_set.clear();
  }
  if (state.source_rejected) {
    Diagnostic diagnostic{DiagnosticKind::kSourceRejected,
                          source_index,
                          source.source_id,
                          batch.boot_session_id(),
                          batch.batch_sequence(),
                          0,
                          1,
                          "source remains rejected until a new boot_session_id"};
    diagnostics.push_back(diagnostic);
    add_diagnostic(std::move(diagnostic));
    return std::nullopt;
  }

  bool has_gap = false;
  if (state.sequence_history_set.contains(batch.batch_sequence())) {
    ++stats.duplicate_batches;
    add_source_diagnostic(source_index, DiagnosticKind::kDuplicateBatch, batch,
                          batch.batch_sequence(), state.last_batch_sequence, 1,
                          batch.replayed() ? "replayed batch deduplicated"
                                           : "duplicate batch deduplicated",
                          diagnostics);
    return std::nullopt;
  }
  if (state.have_last_batch) {
    if (batch.batch_sequence() > state.last_batch_sequence +
                                      (state.last_batch_sequence ==
                                               std::numeric_limits<std::uint64_t>::max()
                                           ? 0
                                           : 1)) {
      const auto missing = batch.batch_sequence() - state.last_batch_sequence - 1;
      has_gap = true;
      add_source_diagnostic(source_index, DiagnosticKind::kBatchGap,
                            batch, batch.batch_sequence(), state.last_batch_sequence + 1,
                            missing, "receiver-observed missing batch sequence", diagnostics);
    } else if (batch.batch_sequence() < state.last_batch_sequence) {
      add_source_diagnostic(source_index, DiagnosticKind::kReorderedBatch, batch,
                            batch.batch_sequence(), state.last_batch_sequence, 1,
                            "batch arrived below the session high-water mark", diagnostics);
    }

    if (batch.first_sample_sequence() > state.last_sample_end) {
      has_gap = true;
      add_source_diagnostic(source_index, DiagnosticKind::kSampleGap, batch,
                            batch.first_sample_sequence(), state.last_sample_end,
                            batch.first_sample_sequence() - state.last_sample_end,
                            "receiver-observed missing sample sequence", diagnostics);
    } else if (batch.first_sample_sequence() < state.last_sample_end) {
      add_source_diagnostic(source_index, DiagnosticKind::kReorderedSamples, batch,
                            batch.first_sample_sequence(), state.last_sample_end, 1,
                            "sample range arrived below the expected sample high-water mark",
                            diagnostics);
    }
  }
  if (batch.has_sender_gap() &&
      (batch.sender_gap().dropped_batches_before() != 0 ||
       batch.sender_gap().dropped_samples_before() != 0)) {
    stats.sender_dropped_batches += batch.sender_gap().dropped_batches_before();
    stats.sender_dropped_samples += batch.sender_gap().dropped_samples_before();
    has_gap = true;
    add_source_diagnostic(source_index, DiagnosticKind::kSenderGap, batch,
                          batch.sender_gap().dropped_batches_before(),
                          batch.sender_gap().dropped_samples_before(),
                          batch.sender_gap().dropped_samples_before(),
                          batch.sender_gap().reason(), diagnostics);
  }
  if (has_gap && config_.gap_policy == GapPolicy::kRejectSourceOnGap) {
    state.source_rejected = true;
    Diagnostic diagnostic{DiagnosticKind::kSourceRejected,
                          source_index,
                          source.source_id,
                          batch.boot_session_id(),
                          batch.batch_sequence(),
                          0,
                          1,
                          "gap policy rejected source until session reset"};
    diagnostics.push_back(diagnostic);
    add_diagnostic(std::move(diagnostic));
    return std::nullopt;
  }

  remember_batch_sequence(state, batch.batch_sequence());
  if (!state.have_last_batch || batch.batch_sequence() > state.last_batch_sequence) {
    state.have_last_batch = true;
    state.last_batch_sequence = batch.batch_sequence();
    state.last_sample_end = batch.first_sample_sequence() + batch.sample_count();
  }
  return accepted;
}

PollStats IngressMux::poll_once() {
  PollStats stats;
  if (!valid()) return stats;
  const auto count = readers_.size();
  const auto start = poll_cursor_;
  for (std::size_t offset = 0; offset < count; ++offset) {
    const auto source_index = (start + offset) % count;
    ++stats.readers_polled;
    MultipartMessage message;
    if (!readers_[source_index]->try_receive(message)) continue;
    ++stats.envelopes_received;
    MultipartMessage next = std::move(message);
    std::vector<Diagnostic> batch_diagnostics;
    const auto duplicates_before = source_stats_[source_index].duplicate_batches;
    auto accepted = validate_message(source_index, next, batch_diagnostics);
    if (!accepted.has_value()) {
      ++stats.batches_rejected;
      ++source_stats_[source_index].rejected_batches;
      if (source_stats_[source_index].duplicate_batches > duplicates_before) {
        ++stats.batches_deduplicated;
      }
      continue;
    }
    if (source_states_[source_index].queue.size() >=
            config_.sources[source_index].queue_capacity_batches ||
        queued_batches_ >= config_.queue_capacity_batches) {
      ++source_stats_[source_index].queue_overflow_batches;
      ++source_stats_[source_index].rejected_batches;
      Diagnostic diagnostic{DiagnosticKind::kQueueOverflow,
                            source_index,
                            config_.sources[source_index].source_id,
                            accepted->batch.boot_session_id(),
                            accepted->batch.batch_sequence(),
                            config_.sources[source_index].queue_capacity_batches,
                            1,
                            "valid batch dropped because a bounded ingress queue is full"};
      add_diagnostic(std::move(diagnostic));
      ++stats.batches_rejected;
      continue;
    }
    source_states_[source_index].queue.push_back(std::move(*accepted));
    ++queued_batches_;
    ++source_stats_[source_index].accepted_batches;
    ++stats.batches_accepted;
  }
  poll_cursor_ = (start + 1) % count;
  return stats;
}

std::optional<AcceptedBatch> IngressMux::pop_next() {
  if (!valid() || queued_batches_ == 0) return std::nullopt;
  const auto count = source_states_.size();
  for (std::size_t offset = 0; offset < count; ++offset) {
    const auto source_index = (pop_cursor_ + offset) % count;
    auto& queue = source_states_[source_index].queue;
    if (queue.empty()) continue;
    AcceptedBatch result = std::move(queue.front());
    queue.pop_front();
    --queued_batches_;
    pop_cursor_ = (source_index + 1) % count;
    return result;
  }
  return std::nullopt;
}

std::vector<Diagnostic> IngressMux::drain_diagnostics() {
  std::vector<Diagnostic> result;
  result.reserve(diagnostics_.size());
  while (!diagnostics_.empty()) {
    result.push_back(std::move(diagnostics_.front()));
    diagnostics_.pop_front();
  }
  return result;
}

}  // namespace scifi2_hub::wireless
