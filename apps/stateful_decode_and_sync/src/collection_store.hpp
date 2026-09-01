#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <new>
#include <string>
#include <utility>
#include <vector>

#include "ring_buffer.hpp"

namespace app {

// Bounded bank of independent per-label feature stores. The limits are part
// of the v1 control-plane contract and are checked before any allocation or
// state mutation.
class CollectionStore {
 public:
  static constexpr std::size_t kMaxCollections = 8;
  static constexpr std::size_t kMaxLabels = 32;
  static constexpr std::size_t kMaxCapacity = 10000;
  static constexpr std::size_t kMaxFeatureDimensions = 8192;
  static constexpr std::uint64_t kMaxRawStorageBytes = 64ULL * 1024ULL * 1024ULL;

  enum class ErrorCode {
    kNone,
    kNotConfigured,
    kInvalidCollectionCount,
    kInvalidLabelCount,
    kInvalidCapacity,
    kInvalidFeatureDimension,
    kStorageLimitExceeded,
    kInvalidCollection,
    kInvalidLabel,
    kFeatureDimensionMismatch,
    kGenerationExhausted,
    kAllocationFailure,
  };

  struct OperationResult {
    bool success = false;
    ErrorCode error = ErrorCode::kNone;
    std::string message;
    bool changed = false;
    std::size_t count = 0;
    std::size_t total = 0;
    std::uint64_t generation = 0;

    explicit operator bool() const { return success; }
  };

  template <typename T>
  struct QueryResult {
    OperationResult status;
    T value{};

    bool success() const { return status.success; }
  };

  struct CollectionInfo {
    std::size_t collection_id = 0;
    std::size_t label_count = 0;
    std::size_t capacity = 0;
    std::size_t feature_dimension = 0;
    std::size_t total = 0;
    std::uint64_t data_generation = 0;
  };

  struct TargetInfo {
    std::size_t collection_id = 0;
    std::size_t label = 0;
    std::size_t count = 0;
    std::size_t capacity = 0;
    std::size_t feature_dimension = 0;
    std::uint64_t data_generation = 0;
  };

  // Configure all dimensions at once. A valid configuration replaces the
  // previous bank only after all bounds have passed. Reconfiguration clears
  // data and advances the process-wide generation clock.
  OperationResult configure(std::size_t collection_count, std::size_t labels_per_collection,
                            std::size_t capacity, std::size_t feature_dimension) {
    const auto validation = validate_configuration(collection_count, labels_per_collection,
                                                   capacity, feature_dimension);
    if (!validation.success) {
      return validation;
    }

    try {
      std::vector<RingBuffer> replacement(collection_count);
      for (auto& collection : replacement) {
        collection.configure(labels_per_collection, capacity);
      }

      std::uint64_t generation = 0;
      if (configured_) {
        if (!advance_generation(generation)) {
          return failure(ErrorCode::kGenerationExhausted,
                         "data generation counter is exhausted");
        }
      }
      std::vector<std::uint64_t> replacement_generations(collection_count, generation);

      collections_.swap(replacement);
      generations_.swap(replacement_generations);
      labels_per_collection_ = labels_per_collection;
      capacity_ = capacity;
      feature_dimension_ = feature_dimension;
      configured_ = true;

      OperationResult result = success_result(true, generation);
      result.total = 0;
      return result;
    } catch (const std::bad_alloc&) {
      return failure(ErrorCode::kAllocationFailure, "collection storage allocation failed");
    }
  }

  bool configured() const { return configured_; }
  std::size_t collection_count() const { return collections_.size(); }
  std::size_t labels_per_collection() const { return labels_per_collection_; }
  std::size_t capacity() const { return capacity_; }
  std::size_t feature_dimension() const { return feature_dimension_; }

  // Validate a collection/label pair without changing selection state. The
  // application can use this at its command boundary before routing capture.
  OperationResult check_target(std::size_t collection_id, std::size_t label) const {
    const auto collection_status = check_collection(collection_id);
    if (!collection_status.success) {
      return collection_status;
    }
    if (label >= labels_per_collection_) {
      return failure(ErrorCode::kInvalidLabel, "label is outside the configured collection");
    }
    OperationResult result = success_result(false, generations_[collection_id]);
    result.count = collections_[collection_id].count(label);
    result.total = collections_[collection_id].total();
    return result;
  }

  QueryResult<CollectionInfo> collection_info(std::size_t collection_id) const {
    QueryResult<CollectionInfo> result;
    const auto status = check_collection(collection_id);
    if (!status.success) {
      result.status = status;
      return result;
    }
    result.status = success_result(false, generations_[collection_id]);
    result.status.total = collections_[collection_id].total();
    result.value = {collection_id,
                    labels_per_collection_,
                    capacity_,
                    feature_dimension_,
                    result.status.total,
                    generations_[collection_id]};
    return result;
  }

  QueryResult<TargetInfo> target_info(std::size_t collection_id, std::size_t label) const {
    QueryResult<TargetInfo> result;
    const auto status = check_target(collection_id, label);
    if (!status.success) {
      result.status = status;
      return result;
    }
    result.status = status;
    result.value = {collection_id,
                    label,
                    status.count,
                    capacity_,
                    feature_dimension_,
                    generations_[collection_id]};
    return result;
  }

  QueryResult<std::size_t> count(std::size_t collection_id, std::size_t label) const {
    QueryResult<std::size_t> result;
    const auto status = check_target(collection_id, label);
    result.status = status;
    if (status.success) {
      result.value = status.count;
    }
    return result;
  }

  QueryResult<std::size_t> total(std::size_t collection_id) const {
    QueryResult<std::size_t> result;
    const auto status = check_collection(collection_id);
    result.status = status;
    if (status.success) {
      result.value = collections_[collection_id].total();
      result.status.total = result.value;
    }
    return result;
  }

  QueryResult<std::uint64_t> generation(std::size_t collection_id) const {
    QueryResult<std::uint64_t> result;
    const auto status = check_collection(collection_id);
    result.status = status;
    if (status.success) {
      result.value = generations_[collection_id];
      result.status.generation = result.value;
    }
    return result;
  }

  OperationResult append(std::size_t collection_id, std::size_t label,
                         std::vector<float> features) {
    const auto target_status = check_target(collection_id, label);
    if (!target_status.success) {
      return target_status;
    }
    if (features.size() != feature_dimension_) {
      return failure(ErrorCode::kFeatureDimensionMismatch,
                     "feature vector has the wrong dimension");
    }
    if (!can_advance_generation()) {
      return failure(ErrorCode::kGenerationExhausted, "data generation counter is exhausted");
    }

    collections_[collection_id].append(label, std::move(features));
    const std::uint64_t generation = next_generation();
    generations_[collection_id] = generation;

    OperationResult result = success_result(true, generation);
    result.count = collections_[collection_id].count(label);
    result.total = collections_[collection_id].total();
    return result;
  }

  // Collect one collection in deterministic label/FIFO order.
  OperationResult collect(std::size_t collection_id,
                          std::vector<std::vector<float>>& out_features,
                          std::vector<std::size_t>& out_labels) const {
    out_features.clear();
    out_labels.clear();
    const auto status = check_collection(collection_id);
    if (!status.success) {
      return status;
    }

    out_features.reserve(collections_[collection_id].total());
    out_labels.reserve(collections_[collection_id].total());
    const auto& collection = collections_[collection_id];
    for (std::size_t label = 0; label < labels_per_collection_; ++label) {
      for (const auto& features : collection.class_buffer(label)) {
        out_features.push_back(features);
        out_labels.push_back(label);
      }
    }

    OperationResult result = success_result(false, generations_[collection_id]);
    result.total = out_features.size();
    return result;
  }

  OperationResult clear(std::size_t collection_id, std::size_t label) {
    const auto target_status = check_target(collection_id, label);
    if (!target_status.success) {
      return target_status;
    }
    if (target_status.count == 0) {
      return target_status;
    }
    if (!can_advance_generation()) {
      return failure(ErrorCode::kGenerationExhausted, "data generation counter is exhausted");
    }

    collections_[collection_id].clear_label(label);
    const std::uint64_t generation = next_generation();
    generations_[collection_id] = generation;
    OperationResult result = success_result(true, generation);
    result.total = collections_[collection_id].total();
    return result;
  }

  OperationResult clear_collection(std::size_t collection_id) {
    const auto collection_status = check_collection(collection_id);
    if (!collection_status.success) {
      return collection_status;
    }
    if (collections_[collection_id].total() == 0) {
      return collection_status;
    }
    if (!can_advance_generation()) {
      return failure(ErrorCode::kGenerationExhausted, "data generation counter is exhausted");
    }

    collections_[collection_id].clear();
    const std::uint64_t generation = next_generation();
    generations_[collection_id] = generation;
    OperationResult result = success_result(true, generation);
    result.total = 0;
    return result;
  }

  OperationResult clear_all() {
    if (!configured_) {
      return failure(ErrorCode::kNotConfigured, "collection store is not configured");
    }

    std::size_t changed_collections = 0;
    for (const auto& collection : collections_) {
      if (collection.total() != 0) {
        ++changed_collections;
      }
    }
    if (changed_collections > 0 &&
        generation_clock_ > std::numeric_limits<std::uint64_t>::max() - changed_collections) {
      return failure(ErrorCode::kGenerationExhausted, "data generation counter is exhausted");
    }

    bool changed = false;
    std::uint64_t last_generation = 0;
    for (std::size_t collection_id = 0; collection_id < collections_.size(); ++collection_id) {
      if (collections_[collection_id].total() == 0) {
        continue;
      }
      collections_[collection_id].clear();
      last_generation = next_generation();
      generations_[collection_id] = last_generation;
      changed = true;
    }

    OperationResult result = success_result(changed, last_generation);
    return result;
  }

  static const char* error_code_name(ErrorCode code) {
    switch (code) {
      case ErrorCode::kNone:
        return "none";
      case ErrorCode::kNotConfigured:
        return "not_configured";
      case ErrorCode::kInvalidCollectionCount:
        return "invalid_collection_count";
      case ErrorCode::kInvalidLabelCount:
        return "invalid_label_count";
      case ErrorCode::kInvalidCapacity:
        return "invalid_capacity";
      case ErrorCode::kInvalidFeatureDimension:
        return "invalid_feature_dimension";
      case ErrorCode::kStorageLimitExceeded:
        return "storage_limit_exceeded";
      case ErrorCode::kInvalidCollection:
        return "invalid_collection";
      case ErrorCode::kInvalidLabel:
        return "invalid_label";
      case ErrorCode::kFeatureDimensionMismatch:
        return "feature_dimension_mismatch";
      case ErrorCode::kGenerationExhausted:
        return "generation_exhausted";
      case ErrorCode::kAllocationFailure:
        return "allocation_failure";
    }
    return "unknown";
  }

 private:
  static OperationResult success_result(bool changed, std::uint64_t generation) {
    OperationResult result;
    result.success = true;
    result.error = ErrorCode::kNone;
    result.changed = changed;
    result.generation = generation;
    return result;
  }

  static OperationResult failure(ErrorCode code, std::string message) {
    OperationResult result;
    result.error = code;
    result.message = std::move(message);
    return result;
  }

  OperationResult check_collection(std::size_t collection_id) const {
    if (!configured_) {
      return failure(ErrorCode::kNotConfigured, "collection store is not configured");
    }
    if (collection_id >= collections_.size()) {
      return failure(ErrorCode::kInvalidCollection,
                     "collection is outside the configured bank");
    }
    return success_result(false, generations_[collection_id]);
  }

  static bool checked_multiply(std::uint64_t lhs, std::uint64_t rhs, std::uint64_t& result) {
    if (lhs != 0 && rhs > std::numeric_limits<std::uint64_t>::max() / lhs) {
      return false;
    }
    result = lhs * rhs;
    return true;
  }

  static OperationResult validate_configuration(std::size_t collection_count,
                                                std::size_t labels_per_collection,
                                                std::size_t capacity,
                                                std::size_t feature_dimension) {
    if (collection_count == 0 || collection_count > kMaxCollections) {
      return failure(ErrorCode::kInvalidCollectionCount,
                     "collection count must be in the range 1..8");
    }
    if (labels_per_collection == 0 || labels_per_collection > kMaxLabels) {
      return failure(ErrorCode::kInvalidLabelCount,
                     "labels per collection must be in the range 1..32");
    }
    if (capacity == 0 || capacity > kMaxCapacity) {
      return failure(ErrorCode::kInvalidCapacity,
                     "vectors per label must be in the range 1..10000");
    }
    if (feature_dimension == 0 || feature_dimension > kMaxFeatureDimensions) {
      return failure(ErrorCode::kInvalidFeatureDimension,
                     "feature dimension must be in the range 1..8192");
    }

    std::uint64_t bytes = 1;
    const std::size_t dimensions[] = {collection_count, labels_per_collection, capacity,
                                      feature_dimension, sizeof(float)};
    for (const std::size_t dimension : dimensions) {
      if (!checked_multiply(bytes, static_cast<std::uint64_t>(dimension), bytes)) {
        return failure(ErrorCode::kStorageLimitExceeded,
                       "raw feature storage size overflows the checked bound");
      }
    }
    if (bytes > kMaxRawStorageBytes) {
      return failure(ErrorCode::kStorageLimitExceeded,
                     "raw feature storage exceeds the 64 MiB limit");
    }
    return success_result(false, 0);
  }

  bool advance_generation(std::uint64_t& out_generation) {
    if (generation_clock_ == std::numeric_limits<std::uint64_t>::max()) {
      return false;
    }
    out_generation = ++generation_clock_;
    return true;
  }

  bool can_advance_generation() const {
    return generation_clock_ != std::numeric_limits<std::uint64_t>::max();
  }

  std::uint64_t next_generation() {
    // A process cannot realistically exhaust this counter; configure/append
    // still use a checked path where failure could occur before mutation.
    return ++generation_clock_;
  }

  bool configured_ = false;
  std::size_t labels_per_collection_ = 0;
  std::size_t capacity_ = 0;
  std::size_t feature_dimension_ = 0;
  std::uint64_t generation_clock_ = 0;
  std::vector<RingBuffer> collections_;
  std::vector<std::uint64_t> generations_;
};

}  // namespace app
