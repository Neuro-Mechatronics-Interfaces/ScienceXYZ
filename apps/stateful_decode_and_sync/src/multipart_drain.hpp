#pragma once

#include <cstddef>
#include <utility>

namespace app {

// Statistics for one receive_multipart() result. `forwarded_count` counts
// successful visitor calls, preserving the distinction between malformed
// messages and parsed frames handed to the caller.
struct MultipartDrainStats {
  std::size_t batch_size = 0;
  std::size_t parsed_count = 0;
  std::size_t parse_error_count = 0;
  std::size_t forwarded_count = 0;
};

// Parse and visit every message in one multipart batch, in receive order.
// Parse must return an optional-like value and visitor receives each parsed
// value by const reference. Keeping this generic makes the multipart contract
// regression-testable without constructing a Synapse reader.
template <typename MessageRange, typename Parse, typename Visitor>
MultipartDrainStats drain_multipart(MessageRange&& messages, Parse&& parse,
                                    Visitor&& visitor) {
  MultipartDrainStats stats;
  stats.batch_size = messages.size();

  for (auto& message : messages) {
    auto maybe_value = parse(std::move(message));
    if (!maybe_value.has_value()) {
      ++stats.parse_error_count;
      continue;
    }

    ++stats.parsed_count;
    visitor(*maybe_value);
    ++stats.forwarded_count;
  }

  return stats;
}

}  // namespace app
