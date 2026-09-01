#include "multipart_drain.hpp"

#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

struct Message {
  int sequence;
  bool valid;
};

struct Frame {
  int sequence;
  int timestamp;
};

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void test_all_valid_messages_are_forwarded_in_order() {
  std::vector<Message> messages{{40, true}, {41, true}, {42, true}};
  std::vector<std::pair<int, int>> forwarded;

  const auto stats = app::drain_multipart(
      std::move(messages),
      [](Message message) -> std::optional<Frame> {
        if (!message.valid) return std::nullopt;
        return Frame{message.sequence, message.sequence * 10};
      },
      [&forwarded](const Frame& frame) {
        forwarded.emplace_back(frame.sequence, frame.timestamp);
      });

  expect(stats.batch_size == 3, "reports the complete multipart batch size");
  expect(stats.parsed_count == 3, "parses every valid multipart message");
  expect(stats.parse_error_count == 0, "reports no parse errors for valid messages");
  expect(stats.forwarded_count == 3, "forwards every parsed multipart message");
  expect(forwarded == std::vector<std::pair<int, int>>({{40, 400}, {41, 410}, {42, 420}}),
         "forwards frames and their timestamps in multipart receive order");
}

void test_malformed_message_does_not_discard_later_frames() {
  std::vector<Message> messages{{7, true}, {8, false}, {9, true}};
  std::vector<int> forwarded;

  const auto stats = app::drain_multipart(
      std::move(messages),
      [](Message message) -> std::optional<Frame> {
        if (!message.valid) return std::nullopt;
        return Frame{message.sequence, message.sequence * 10};
      },
      [&forwarded](const Frame& frame) { forwarded.push_back(frame.sequence); });

  expect(stats.batch_size == 3, "counts malformed messages in the batch size");
  expect(stats.parsed_count == 2, "counts only valid parsed messages");
  expect(stats.parse_error_count == 1, "reports the malformed message");
  expect(stats.forwarded_count == 2, "forwards valid frames on both sides of an error");
  expect(forwarded == std::vector<int>({7, 9}),
         "does not stop the batch at a malformed message");
}

}  // namespace

int main() {
  try {
    test_all_valid_messages_are_forwarded_in_order();
    test_malformed_message_does_not_discard_later_frames();
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
  std::cout << "multipart drain tests passed\n";
  return 0;
}
