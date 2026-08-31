#include "ring_buffer.hpp"

#include <cstddef>
#include <initializer_list>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using app::RingBuffer;
using Features = std::vector<std::vector<float>>;

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void expect_features(const std::vector<float>& actual,
                     std::initializer_list<float> expected,
                     const std::string& message) {
  expect(actual == std::vector<float>(expected), message);
}

void test_configuration_and_fifo_eviction() {
  RingBuffer buffers;
  buffers.configure(2, 2);

  expect(buffers.num_classes() == 2, "configure sets the label count");
  expect(buffers.capacity() == 2, "configure sets the per-label capacity");

  buffers.append(0, {1.0F});
  buffers.append(0, {2.0F});
  buffers.append(0, {3.0F});
  buffers.append(1, {10.0F});

  expect(buffers.count(0) == 2, "FIFO retains only the configured capacity");
  expect(buffers.count(1) == 1, "labels have independent counts");
  expect(buffers.total() == 3, "total sums the independent label buffers");
  expect_features(buffers.class_buffer(0).at(0), {2.0F},
                  "FIFO evicts the oldest vector only");
  expect_features(buffers.class_buffer(0).at(1), {3.0F},
                  "FIFO preserves insertion order");
  expect_features(buffers.class_buffer(1).at(0), {10.0F},
                  "FIFO does not cross label boundaries");
}

void test_collect_is_label_ordered_and_replaces_outputs() {
  RingBuffer buffers;
  buffers.configure(2, 3);
  buffers.append(1, {11.0F, 12.0F});
  buffers.append(0, {1.0F, 2.0F});
  buffers.append(1, {13.0F, 14.0F});

  Features features{{99.0F}};
  std::vector<std::size_t> labels{99};
  buffers.collect(features, labels);

  expect(features.size() == 3, "collect returns every stored vector");
  expect(labels == std::vector<std::size_t>({0, 1, 1}),
         "collect returns labels in deterministic label/FIFO order");
  expect_features(features.at(0), {1.0F, 2.0F},
                  "collect emits label zero first");
  expect_features(features.at(1), {11.0F, 12.0F},
                  "collect preserves FIFO order for label one");
  expect_features(features.at(2), {13.0F, 14.0F},
                  "collect preserves all vectors for a label");
}

void test_empty_and_invalid_operations_are_safe() {
  RingBuffer buffers;
  buffers.configure(1, 0);
  buffers.append(0, {1.0F});
  buffers.append(1, {2.0F});

  expect(buffers.count(0) == 0, "zero capacity stores no vectors");
  expect(buffers.count(1) == 0, "invalid append does not create a label");
  expect(buffers.total() == 0, "invalid and zero-capacity appends stay empty");
  expect(buffers.count(99) == 0, "invalid count is safe");

  Features features{{3.0F}};
  std::vector<std::size_t> labels{4};
  buffers.collect(features, labels);
  expect(features.empty() && labels.empty(),
         "collect clears caller outputs when the store is empty");
}

void test_clear_and_reconfigure_remove_old_data() {
  RingBuffer buffers;
  buffers.configure(2, 2);
  buffers.append(0, {1.0F});
  buffers.append(1, {2.0F});
  buffers.clear();
  expect(buffers.total() == 0, "clear removes all label data");
  expect(buffers.num_classes() == 2 && buffers.capacity() == 2,
         "clear preserves the configured shape");

  buffers.append(1, {3.0F});
  buffers.configure(1, 1);
  expect(buffers.num_classes() == 1 && buffers.capacity() == 1,
         "reconfigure replaces the shape");
  expect(buffers.total() == 0, "reconfigure removes old data");
  buffers.append(0, {4.0F});
  expect_features(buffers.class_buffer(0).at(0), {4.0F},
                  "reconfigured buffer accepts new data");
}

}  // namespace

int main() {
  const std::vector<std::pair<const char*, void (*)()>> tests{
      {"configuration_and_fifo_eviction", test_configuration_and_fifo_eviction},
      {"collect_is_label_ordered_and_replaces_outputs",
       test_collect_is_label_ordered_and_replaces_outputs},
      {"empty_and_invalid_operations_are_safe",
       test_empty_and_invalid_operations_are_safe},
      {"clear_and_reconfigure_remove_old_data",
       test_clear_and_reconfigure_remove_old_data},
  };

  try {
    for (const auto& test : tests) {
      test.second();
      std::cout << "PASS " << test.first << '\n';
    }
  } catch (const std::exception& error) {
    std::cerr << "FAIL " << error.what() << '\n';
    return 1;
  }

  std::cout << "PASS " << tests.size() << " control-plane tests\n";
  return 0;
}
