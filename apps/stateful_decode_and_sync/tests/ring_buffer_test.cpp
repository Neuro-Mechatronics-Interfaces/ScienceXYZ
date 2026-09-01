#include "collection_store.hpp"
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
using app::CollectionStore;
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

void expect_append(CollectionStore& store, std::size_t collection, std::size_t label,
                   float value, const std::string& message) {
  const auto result = store.append(collection, label, {value});
  expect(result.success, message + " (" + result.message + ")");
}

void test_collection_configuration_bounds_and_checked_errors() {
  CollectionStore store;

  expect(!store.append(0, 0, {1.0F}).success,
         "append before configuration returns a structured error");
  expect(store.append(0, 0, {1.0F}).error == CollectionStore::ErrorCode::kNotConfigured,
         "unconfigured append reports not_configured");
  expect(store.configure(0, 1, 1, 1).error ==
             CollectionStore::ErrorCode::kInvalidCollectionCount,
         "zero collections are rejected");
  expect(store.configure(1, 0, 1, 1).error == CollectionStore::ErrorCode::kInvalidLabelCount,
         "zero labels are rejected");
  expect(store.configure(1, 1, 0, 1).error == CollectionStore::ErrorCode::kInvalidCapacity,
         "zero capacity is rejected by the bounded store");
  expect(store.configure(1, 1, 1, 0).error ==
             CollectionStore::ErrorCode::kInvalidFeatureDimension,
         "zero feature dimensions are rejected");
  expect(store.configure(8, 32, 10000, 8192).error ==
             CollectionStore::ErrorCode::kStorageLimitExceeded,
         "unsafe raw storage dimensions are rejected before allocation");

  expect(store.configure(2, 2, 2, 1).success, "valid collection configuration succeeds");
  expect(store.collection_info(2).status.error == CollectionStore::ErrorCode::kInvalidCollection,
         "invalid collection queries return a structured error");
  expect(store.target_info(0, 2).status.error == CollectionStore::ErrorCode::kInvalidLabel,
         "invalid label queries return a structured error");
  expect(store.append(0, 0, {1.0F, 2.0F}).error ==
             CollectionStore::ErrorCode::kFeatureDimensionMismatch,
         "wrong feature dimensions are rejected");
  expect(store.clear(2, 0).error == CollectionStore::ErrorCode::kInvalidCollection,
         "invalid label flush collection is rejected");
}

void test_collections_are_independent_and_fifo_bounded() {
  CollectionStore store;
  expect(store.configure(2, 2, 2, 1).success, "two collections configure successfully");

  expect_append(store, 0, 0, 1.0F, "collection zero append 1");
  expect_append(store, 0, 0, 2.0F, "collection zero append 2");
  expect_append(store, 0, 0, 3.0F, "collection zero append 3");
  expect_append(store, 0, 1, 20.0F, "collection zero label one append");
  expect_append(store, 1, 0, 10.0F, "collection one append 10");

  Features features;
  std::vector<std::size_t> labels;
  expect(store.collect(0, features, labels).success, "collection zero collects");
  expect(labels == std::vector<std::size_t>({0, 0, 1}),
         "collection zero preserves deterministic label/FIFO order");
  expect_features(features.at(0), {2.0F}, "collection zero evicts only its oldest vector");
  expect_features(features.at(1), {3.0F}, "collection zero retains its newest vector");
  expect_features(features.at(2), {20.0F}, "collection zero retains other labels");

  expect(store.collect(1, features, labels).success, "collection one collects");
  expect(labels == std::vector<std::size_t>({0}),
         "collection one has no data copied from collection zero");
  expect_features(features.at(0), {10.0F}, "collection one data remains independent");
}

void test_flush_scopes_and_generation_changes() {
  CollectionStore store;
  expect(store.configure(2, 2, 3, 1).success, "flush store configures successfully");
  expect_append(store, 0, 0, 1.0F, "flush collection zero label zero");
  expect_append(store, 0, 1, 2.0F, "flush collection zero label one");
  expect_append(store, 1, 0, 10.0F, "flush collection one label zero");

  const auto generation_before_label_flush = store.generation(0);
  expect(generation_before_label_flush.success(), "collection generation can be queried");
  const auto label_flush = store.clear(0, 0);
  expect(label_flush.success && label_flush.changed, "label flush succeeds and changes data");
  expect(label_flush.generation > generation_before_label_flush.value,
         "label flush advances the collection generation");
  expect(store.count(0, 0).value == 0, "label flush removes only its label");
  expect(store.count(0, 1).value == 1, "label flush preserves sibling labels");
  expect(store.total(1).value == 1, "label flush preserves other collections");

  const auto empty_label_flush = store.clear(0, 0);
  expect(empty_label_flush.success && !empty_label_flush.changed,
         "flushing an empty label is successful but unchanged");
  expect(empty_label_flush.generation == label_flush.generation,
         "empty label flush does not advance generation");

  const auto collection_flush = store.clear_collection(0);
  expect(collection_flush.success && collection_flush.changed,
         "collection flush succeeds and changes data");
  expect(store.total(0).value == 0, "collection flush removes all labels in its collection");
  expect(store.total(1).value == 1, "collection flush preserves another collection");
  expect(collection_flush.generation > label_flush.generation,
         "collection flush advances the collection generation");

  const auto all_flush = store.clear_all();
  expect(all_flush.success && all_flush.changed, "all flush succeeds and changes data");
  expect(store.total(0).value == 0 && store.total(1).value == 0,
         "all flush removes every collection");
  expect(store.generation(1).value > generation_before_label_flush.value,
         "all flush advances the affected other collection generation");

  const auto second_all_flush = store.clear_all();
  expect(second_all_flush.success && !second_all_flush.changed,
         "repeating an all flush is successful but unchanged");
  expect(second_all_flush.generation == 0,
         "unchanged all flush has no target generation");
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
      {"collection_configuration_bounds_and_checked_errors",
       test_collection_configuration_bounds_and_checked_errors},
      {"collections_are_independent_and_fifo_bounded",
       test_collections_are_independent_and_fifo_bounded},
      {"flush_scopes_and_generation_changes", test_flush_scopes_and_generation_changes},
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
