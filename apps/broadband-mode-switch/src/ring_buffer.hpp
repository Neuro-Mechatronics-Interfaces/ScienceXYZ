#pragma once
#include <cstddef>
#include <deque>
#include <vector>

namespace app {

// Per-class fixed-capacity circular store of feature vectors.
//
// Each class owns an independent bounded deque. Appending past `capacity`
// drops the oldest vector for that class (FIFO eviction). Feature vectors are
// stored by value; all vectors for a given class are expected to share the
// same length (the featurizer output dimension), but the store does not
// enforce that.
class RingBuffer {
 public:
  RingBuffer() = default;

  // Configure the store for `num_classes` categories, each capped at
  // `capacity` feature vectors. Clears any existing contents.
  void configure(std::size_t num_classes, std::size_t capacity) {
    capacity_ = capacity;
    buffers_.assign(num_classes, {});
  }

  std::size_t num_classes() const { return buffers_.size(); }
  std::size_t capacity() const { return capacity_; }

  // Append `features` to class `label`. Drops the oldest entry when the
  // per-class buffer is at capacity. Out-of-range labels are ignored.
  void append(std::size_t label, std::vector<float> features) {
    if (label >= buffers_.size()) {
      return;
    }
    auto& buf = buffers_[label];
    buf.emplace_back(std::move(features));
    while (buf.size() > capacity_) {
      buf.pop_front();
    }
  }

  // Number of feature vectors currently stored for `label`.
  std::size_t count(std::size_t label) const {
    if (label >= buffers_.size()) {
      return 0;
    }
    return buffers_[label].size();
  }

  // Total feature vectors across all classes.
  std::size_t total() const {
    std::size_t n = 0;
    for (const auto& buf : buffers_) {
      n += buf.size();
    }
    return n;
  }

  const std::deque<std::vector<float>>& class_buffer(std::size_t label) const {
    return buffers_.at(label);
  }

  // Flatten the whole store into parallel (feature, label) arrays. Used to
  // build the training set for a fit pass.
  void collect(std::vector<std::vector<float>>& out_features,
               std::vector<std::size_t>& out_labels) const {
    out_features.clear();
    out_labels.clear();
    out_features.reserve(total());
    out_labels.reserve(total());
    for (std::size_t label = 0; label < buffers_.size(); ++label) {
      for (const auto& features : buffers_[label]) {
        out_features.push_back(features);
        out_labels.push_back(label);
      }
    }
  }

  void clear() {
    for (auto& buf : buffers_) {
      buf.clear();
    }
  }

 private:
  std::size_t capacity_ = 0;
  std::vector<std::deque<std::vector<float>>> buffers_;
};

}  // namespace app
