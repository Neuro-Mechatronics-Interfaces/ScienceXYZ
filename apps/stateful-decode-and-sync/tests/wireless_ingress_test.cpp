#include "wireless_ingress.hpp"

#include <cstddef>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using app::wireless::DiagnosticKind;
using app::wireless::IngressConfig;
using app::wireless::IngressMux;
using app::wireless::MultipartMessage;
using app::wireless::NonblockingReader;
using app::wireless::RationalRate;
using app::wireless::SourceConfig;
using sciencexyz::wireless::v1::SAMPLE_FORMAT_INT16_LE;
using sciencexyz::wireless::v1::WirelessBatch;

void expect(bool condition, const std::string& message) {
  if (!condition) throw std::runtime_error(message);
}

class FakeReader final : public NonblockingReader {
 public:
  explicit FakeReader(std::vector<MultipartMessage> messages)
      : messages_(std::move(messages)) {}

  bool try_receive(MultipartMessage& message) override {
    if (next_ == messages_.size()) return false;
    message = std::move(messages_[next_++]);
    return true;
  }

 private:
  std::vector<MultipartMessage> messages_;
  std::size_t next_ = 0;
};

SourceConfig source_config(const std::string& id, std::size_t queue_capacity = 8) {
  SourceConfig source;
  source.source_id = id;
  source.topic = "wireless/v1/" + id;
  source.expected_sample_rate = RationalRate{1000, 1};
  source.expected_channel_count = 2;
  source.expected_sample_format = SAMPLE_FORMAT_INT16_LE;
  source.max_batch_samples = 16;
  source.queue_capacity_batches = queue_capacity;
  return source;
}

MultipartMessage message(const SourceConfig& source, const std::string& session,
                         std::uint64_t batch_sequence,
                         std::uint64_t first_sample_sequence = 0,
                         std::uint32_t sample_count = 1) {
  WirelessBatch batch;
  batch.set_contract_version(1);
  batch.set_source_id(source.source_id);
  batch.set_boot_session_id(session);
  batch.set_batch_sequence(batch_sequence);
  batch.set_first_sample_sequence(first_sample_sequence);
  batch.set_sample_count(sample_count);
  batch.set_channel_count(2);
  batch.add_shape(sample_count);
  batch.add_shape(2);
  batch.set_sample_format(SAMPLE_FORMAT_INT16_LE);
  batch.set_payload(std::string(sample_count * 2 * sizeof(std::int16_t), '\0'));
  batch.set_sample_rate_numerator_hz(1000);
  batch.set_sample_rate_denominator(1);
  batch.set_first_source_tick(first_sample_sequence * 1000);
  batch.set_source_tick_frequency_numerator_hz(1000000);
  batch.set_source_tick_frequency_denominator(1);
  std::string serialized;
  expect(batch.SerializeToString(&serialized), "test protobuf serializes");
  return MultipartMessage{{source.topic, std::move(serialized)}};
}

IngressConfig ingress_config(std::vector<SourceConfig> sources) {
  IngressConfig config;
  config.sources = std::move(sources);
  config.queue_capacity_batches = 32;
  config.max_batch_bytes = 1024 * 1024;
  return config;
}

void test_four_reader_round_robin_and_value_ownership() {
  std::vector<SourceConfig> sources;
  std::vector<std::unique_ptr<NonblockingReader>> readers;
  for (std::size_t i = 0; i < 4; ++i) {
    sources.push_back(source_config("source-" + std::to_string(i)));
    readers.push_back(std::make_unique<FakeReader>(std::vector<MultipartMessage>{
        message(sources.back(), "boot-a", 0)}));
  }

  IngressMux mux(ingress_config(std::move(sources)), std::move(readers));
  expect(mux.valid(), "four configured readers are accepted");
  const auto stats = mux.poll_once();
  expect(stats.readers_polled == 4 && stats.envelopes_received == 4 &&
             stats.batches_accepted == 4,
         "one nonblocking envelope is polled from every reader");
  for (std::size_t i = 0; i < 4; ++i) {
    const auto batch = mux.pop_next();
    expect(batch.has_value() && batch->source_index == i,
           "accepted batches leave the mux in source round-robin order");
    expect(batch->batch.source_id() == "source-" + std::to_string(i),
           "accepted batch owns its parsed protobuf");
  }
  expect(mux.queued_batches() == 0, "queue accounting reaches zero after draining");
}

void test_validation_and_explicit_gap_diagnostics() {
  const auto source = source_config("source-0");
  std::vector<MultipartMessage> messages{
      message(source, "boot-a", 0, 0, 1),
      message(source, "boot-a", 2, 3, 1),
      message(source, "boot-a", 2, 3, 1),
      message(source, "boot-b", 0, 0, 1),
      MultipartMessage{{source.topic, "not protobuf"}},
      MultipartMessage{{source.topic, "extra-frame", "unexpected"}},
  };
  std::vector<std::unique_ptr<NonblockingReader>> readers;
  readers.push_back(std::make_unique<FakeReader>(std::move(messages)));
  IngressMux mux(ingress_config({source}), std::move(readers));

  for (int i = 0; i < 6; ++i) {
    mux.poll_once();
    while (mux.pop_next().has_value()) {
    }
  }
  const auto diagnostics = mux.drain_diagnostics();
  bool saw_batch_gap = false;
  bool saw_sample_gap = false;
  bool saw_duplicate = false;
  bool saw_session_reset = false;
  bool saw_malformed = false;
  for (const auto& diagnostic : diagnostics) {
    saw_batch_gap |= diagnostic.kind == DiagnosticKind::kBatchGap;
    saw_sample_gap |= diagnostic.kind == DiagnosticKind::kSampleGap;
    saw_duplicate |= diagnostic.kind == DiagnosticKind::kDuplicateBatch;
    saw_session_reset |= diagnostic.kind == DiagnosticKind::kSessionReset;
    saw_malformed |= diagnostic.kind == DiagnosticKind::kMalformedBatch ||
                     diagnostic.kind == DiagnosticKind::kMalformedEnvelope;
  }
  expect(saw_batch_gap && saw_sample_gap, "sequence gaps are explicit diagnostics");
  expect(saw_duplicate, "duplicate batch is explicitly diagnosed and deduplicated");
  expect(saw_session_reset, "new boot session starts an explicit sequence epoch");
  expect(saw_malformed, "malformed protobuf and envelope are observable");
  expect(mux.source_stats().at(0).accepted_batches == 3,
         "valid batches remain available across malformed messages");
  expect(mux.source_stats().at(0).duplicate_batches == 1,
         "duplicate accounting is retained separately");
}

void test_bounded_queue_and_reject_on_gap_policy() {
  const auto source = source_config("source-0", 1);
  std::vector<std::unique_ptr<NonblockingReader>> readers;
  readers.push_back(std::make_unique<FakeReader>(std::vector<MultipartMessage>{
      message(source, "boot-a", 0), message(source, "boot-a", 1),
      message(source, "boot-a", 2), message(source, "boot-a", 4),
      message(source, "boot-b", 0)}));
  auto config = ingress_config({source});
  config.gap_policy = app::wireless::GapPolicy::kRejectSourceOnGap;
  IngressMux mux(std::move(config), std::move(readers));

  expect(mux.poll_once().batches_accepted == 1, "first batch fills bounded queue");
  const auto overflow = mux.poll_once();
  expect(overflow.batches_rejected == 1 && mux.queued_batches() == 1,
         "queue overflow drops a valid batch without exceeding capacity");
  expect(mux.pop_next().has_value(), "the original queued batch remains intact");
  expect(mux.poll_once().batches_accepted == 1,
         "a contiguous batch is accepted after the queue drains");
  mux.pop_next();
  expect(mux.poll_once().batches_rejected == 1,
         "a sequence gap rejects the source under explicit policy");
  expect(mux.poll_once().batches_accepted == 1,
         "a new boot session clears the source rejection epoch");
  bool saw_overflow = false;
  bool saw_rejection = false;
  for (const auto& diagnostic : mux.drain_diagnostics()) {
    saw_overflow |= diagnostic.kind == DiagnosticKind::kQueueOverflow;
    saw_rejection |= diagnostic.kind == DiagnosticKind::kSourceRejected;
  }
  expect(saw_overflow && saw_rejection, "bounded loss and gap rejection are diagnosed");
}

}  // namespace

int main() {
  try {
    test_four_reader_round_robin_and_value_ownership();
    test_validation_and_explicit_gap_diagnostics();
    test_bounded_queue_and_reject_on_gap_policy();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "PASS wireless ingress validation, mux, and diagnostics\n";
  return 0;
}
