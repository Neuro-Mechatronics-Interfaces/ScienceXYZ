#include "wireless_simulator.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <utility>

namespace scifi2_hub::wireless {

namespace {

using sciencexyz::wireless::v1::SAMPLE_FORMAT_FLOAT32_LE;
using sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE;
using sciencexyz::wireless::v1::SAMPLE_FORMAT_INT32_LE;
using sciencexyz::wireless::v1::TIMESTAMP_QUALITY_SOURCE_TICK_AND_TIME;

std::size_t bytes_per_value(sciencexyz::wireless::v1::SampleFormat format) {
  switch (format) {
    case SAMPLE_FORMAT_INT16_LE:
      return 2;
    case SAMPLE_FORMAT_INT32_LE:
    case SAMPLE_FORMAT_FLOAT32_LE:
      return 4;
    default:
      return 0;
  }
}

std::uint64_t clamp_u64(long double value) {
  if (value <= 0.0L) return 0;
  if (value >= static_cast<long double>(UINT64_MAX)) return UINT64_MAX;
  return static_cast<std::uint64_t>(std::llround(value));
}

std::int64_t signed_jitter(std::uint64_t magnitude, std::uint64_t sequence) {
  if (magnitude == 0) return 0;
  switch (sequence % 3) {
    case 0:
      return -static_cast<std::int64_t>(magnitude);
    case 1:
      return 0;
    default:
      return static_cast<std::int64_t>(magnitude);
  }
}

void put_little_endian(std::string& payload, std::size_t offset,
                       std::uint32_t value, std::size_t width) {
  for (std::size_t byte = 0; byte < width; ++byte) {
    payload[offset + byte] = static_cast<char>((value >> (byte * 8)) & 0xffU);
  }
}

}  // namespace

MultiSourceSimulator::MultiSourceSimulator(MultiSourceSimulatorConfig config)
    : config_(std::move(config)) {
  if (config_.sources.empty() || config_.sources.size() > kSourceCount ||
      config_.max_output_batches == 0) {
    construction_error_ = "multi-source simulator requires one to four sources";
    return;
  }
  for (std::size_t i = 0; i < config_.sources.size(); ++i) {
    const auto& source = config_.sources[i];
    if (source.source_id.empty() || source.sample_rate.numerator_hz == 0 ||
        source.sample_rate.denominator == 0 || source.channel_count == 0 ||
        source.source_tick_frequency_hz == 0 || source.samples_per_batch == 0 ||
        bytes_per_value(source.sample_format) == 0) {
      construction_error_ = "invalid multi-source simulator configuration";
      return;
    }
    if (!source.channels.empty() && source.channels.size() != source.channel_count) {
      construction_error_ = "simulator channel descriptors must cover every channel";
      return;
    }
    for (std::size_t j = 0; j < i; ++j) {
      if (source.source_id == config_.sources[j].source_id) {
        construction_error_ = "simulator source identities must be unique";
        return;
      }
    }
  }
}

std::vector<SimulatedAcceptedBatch> MultiSourceSimulator::generate() const {
  std::vector<SimulatedAcceptedBatch> result;
  if (!valid()) return result;

  for (const auto& source : config_.sources) {
    std::vector<SimulatedAcceptedBatch> source_batches;
    source_batches.reserve(static_cast<std::size_t>(source.batch_count));
    const long double nominal_ticks_per_sample =
        static_cast<long double>(source.source_tick_frequency_hz) *
        static_cast<long double>(source.sample_rate.denominator) /
        static_cast<long double>(source.sample_rate.numerator_hz);
    const long double actual_ticks_per_sample =
        nominal_ticks_per_sample *
        (1.0L + static_cast<long double>(source.clock_drift_ppm) / 1'000'000.0L);
    const long double batch_duration_ns =
        static_cast<long double>(source.samples_per_batch) * 1'000'000'000.0L *
        static_cast<long double>(source.sample_rate.denominator) /
        static_cast<long double>(source.sample_rate.numerator_hz);

    for (std::uint64_t logical_batch = 0; logical_batch < source.batch_count;
         ++logical_batch) {
      if (source.loss_every_n_batches != 0 &&
          (logical_batch + 1) % source.loss_every_n_batches == 0) {
        continue;
      }
      const bool reset = source.reset_at_batch.has_value() &&
                         logical_batch >= *source.reset_at_batch;
      const std::uint64_t epoch_batch =
          reset ? logical_batch - *source.reset_at_batch : logical_batch;
      const std::string boot_session =
          source.source_id + (reset ? "-boot-b" : "-boot-a");
      const auto first_tick = clamp_u64(
          static_cast<long double>(source.start_source_tick) +
          static_cast<long double>(reset ? epoch_batch : logical_batch) *
              static_cast<long double>(source.samples_per_batch) * actual_ticks_per_sample);
      const auto first_sample = epoch_batch * source.samples_per_batch;
      const auto reference_time = clamp_u64(
          static_cast<long double>(source.start_reference_time_ns) +
          static_cast<long double>(logical_batch) * batch_duration_ns);
      const auto jitter = signed_jitter(source.gateway_jitter_ns, logical_batch);
      const auto gateway_receive = clamp_u64(
          static_cast<long double>(reference_time) + static_cast<long double>(jitter));
      const auto gateway_send = clamp_u64(static_cast<long double>(gateway_receive) + 1'000.0L);
      const auto host_receive = clamp_u64(
          static_cast<long double>(reference_time) + 5'000'000.0L +
          static_cast<long double>(jitter) / 2.0L);

      const auto element_size = bytes_per_value(source.sample_format);
      std::string payload(static_cast<std::size_t>(source.samples_per_batch) *
                              source.channel_count * element_size,
                          '\0');
      for (std::uint32_t sample = 0; sample < source.samples_per_batch; ++sample) {
        for (std::uint32_t channel = 0; channel < source.channel_count; ++channel) {
          const auto sample_number =
              (logical_batch * source.samples_per_batch + sample) * (channel + 1) +
              (static_cast<std::uint32_t>(source.source_id.size()) << 4);
          std::uint32_t value = static_cast<std::uint32_t>(sample_number);
          if (source.sample_format == SAMPLE_FORMAT_FLOAT32_LE) {
            const float float_value = static_cast<float>(sample_number);
            std::memcpy(&value, &float_value, sizeof(value));
          }
          const auto offset = (static_cast<std::size_t>(sample) * source.channel_count +
                               channel) * element_size;
          put_little_endian(payload, offset, value, element_size);
        }
      }

      sciencexyz::wireless::v1::WirelessBatch batch;
      batch.set_contract_version(1);
      batch.set_source_id(source.source_id);
      batch.set_boot_session_id(boot_session);
      batch.set_batch_sequence(epoch_batch);
      batch.set_first_sample_sequence(first_sample);
      batch.set_sample_count(source.samples_per_batch);
      batch.set_channel_count(source.channel_count);
      batch.add_shape(source.samples_per_batch);
      batch.add_shape(source.channel_count);
      batch.set_sample_format(source.sample_format);
      batch.set_payload(std::move(payload));
      batch.set_sample_rate_numerator_hz(source.sample_rate.numerator_hz);
      batch.set_sample_rate_denominator(source.sample_rate.denominator);
      batch.set_first_source_tick(first_tick);
      batch.set_source_tick_frequency_numerator_hz(source.source_tick_frequency_hz);
      batch.set_source_tick_frequency_denominator(1);
      batch.set_source_acquisition_time_ns(reference_time);
      batch.set_source_time_domain(source.source_time_domain);
      batch.set_gateway_id(source.gateway_id);
      batch.set_gateway_session_id(source.gateway_session_id);
      batch.set_gateway_receive_time_ns(gateway_receive);
      batch.set_gateway_send_time_ns(gateway_send);
      batch.set_gateway_clock_id(source.gateway_clock_id);
      batch.set_timestamp_quality(TIMESTAMP_QUALITY_SOURCE_TICK_AND_TIME);
      for (std::size_t channel = 0; channel < source.channels.size(); ++channel) {
        auto* descriptor = batch.add_channels();
        *descriptor = source.channels[channel];
        descriptor->set_index(static_cast<std::uint32_t>(channel));
      }

      SimulatedAcceptedBatch simulated;
      simulated.accepted.source_index = 0;
      simulated.accepted.topic = source.topic.empty()
                                     ? "wireless/v1/" + source.source_id
                                     : source.topic;
      simulated.accepted.batch = std::move(batch);
      simulated.host_receive_time_ns = host_receive;
      source_batches.push_back(std::move(simulated));
    }
    if (source.reorder_adjacent_batches) {
      for (std::size_t i = 1; i < source_batches.size(); i += 2) {
        std::swap(source_batches[i - 1], source_batches[i]);
      }
    }
    if (result.size() + source_batches.size() > config_.max_output_batches) {
      const auto remaining = config_.max_output_batches - result.size();
      result.insert(result.end(), source_batches.begin(),
                    source_batches.begin() + static_cast<std::ptrdiff_t>(remaining));
      break;
    }
    result.insert(result.end(), source_batches.begin(), source_batches.end());
  }
  return result;
}

}  // namespace scifi2_hub::wireless
