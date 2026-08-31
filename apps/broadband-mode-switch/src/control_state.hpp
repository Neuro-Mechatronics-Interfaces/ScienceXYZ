#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

#include "control_protocol.hpp"

namespace app::control {

struct ActiveTarget {
  std::uint32_t collection_id = 0;
  std::uint32_t label = 0;
  bool capture_enabled = false;
};

struct TransitionResult {
  bool success = false;
  protocol::ErrorCode code = broadband_mode_switch::v1::ERROR_NONE;
  std::string field;
  std::string message;

  explicit operator bool() const { return success; }
};

inline TransitionResult success() { return {true, broadband_mode_switch::v1::ERROR_NONE, {}, {}}; }

inline TransitionResult failure(protocol::ErrorCode code, std::string field,
                                std::string message) {
  return {false, code, std::move(field), std::move(message)};
}

inline TransitionResult validate_target(std::uint32_t collection_id, std::uint32_t label,
                                        std::size_t collection_count,
                                        std::size_t labels_per_collection) {
  if (collection_count == 0 || collection_id >= collection_count) {
    return failure(broadband_mode_switch::v1::ERROR_OUT_OF_RANGE, "collection_id",
                   "collection is outside the configured bank");
  }
  if (labels_per_collection == 0 || label >= labels_per_collection) {
    return failure(broadband_mode_switch::v1::ERROR_OUT_OF_RANGE, "label",
                   "label is outside the configured collection");
  }
  return success();
}

// Validate the complete replacement before assigning any active field.  The
// caller can therefore use this as the atomic prepare_capture transition.
inline TransitionResult prepare_capture(ActiveTarget& current, std::uint32_t collection_id,
                                         std::uint32_t label, bool enabled,
                                         std::size_t collection_count,
                                         std::size_t labels_per_collection) {
  const auto validation =
      validate_target(collection_id, label, collection_count, labels_per_collection);
  if (!validation) {
    return validation;
  }
  current = {collection_id, label, enabled};
  return success();
}

inline TransitionResult select_collection(ActiveTarget& current, std::uint32_t collection_id,
                                           std::size_t collection_count,
                                           std::size_t labels_per_collection) {
  if (current.capture_enabled) {
    return failure(broadband_mode_switch::v1::ERROR_CAPTURE_ENABLED, "capture_enabled",
                   "collection cannot change while capture is enabled");
  }
  const auto validation =
      validate_target(collection_id, current.label, collection_count, labels_per_collection);
  if (!validation) {
    return validation;
  }
  current.collection_id = collection_id;
  return success();
}

inline TransitionResult select_label(ActiveTarget& current, std::uint32_t label,
                                     std::size_t labels_per_collection) {
  if (current.capture_enabled) {
    return failure(broadband_mode_switch::v1::ERROR_CAPTURE_ENABLED, "capture_enabled",
                   "label cannot change while capture is enabled");
  }
  if (labels_per_collection == 0 || label >= labels_per_collection) {
    return failure(broadband_mode_switch::v1::ERROR_OUT_OF_RANGE, "label",
                   "label is outside the configured collection");
  }
  current.label = label;
  return success();
}

}  // namespace app::control
