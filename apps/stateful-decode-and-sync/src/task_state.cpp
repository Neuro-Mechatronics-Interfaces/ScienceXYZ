#include "task_state.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <iomanip>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include <google/protobuf/struct.pb.h>

namespace app::task {
namespace {

constexpr std::uint64_t kMaxExactJsonInteger = (1ULL << 53) - 1;

DefinitionResult ok() { return {}; }

DefinitionResult fail(DefinitionError error, std::string field, std::string message) {
  return {error, std::move(field), std::move(message)};
}

bool valid_name(std::string_view value) {
  if (value.empty() || value.size() > kMaxNameLength) return false;
  const auto alpha = [](char c) {
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
  };
  const auto tail = [&](char c) {
    return alpha(c) || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-';
  };
  if (!alpha(value.front())) return false;
  return std::all_of(value.begin() + 1, value.end(), tail);
}

std::string json_string(std::string_view value) {
  std::ostringstream out;
  out << '"';
  for (const unsigned char c : value) {
    switch (c) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\b': out << "\\b"; break;
      case '\f': out << "\\f"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (c < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<unsigned>(c) << std::dec;
        } else {
          out << static_cast<char>(c);
        }
    }
  }
  out << '"';
  return out.str();
}

const char* loss_action_name(LossAction action) {
  return action == LossAction::kHold ? "hold" : "fault";
}

const char* gap_action_name(SequenceGapAction action) {
  return action == SequenceGapAction::kContinue ? "continue" : "fault";
}

// Small self-contained SHA-256 implementation keeps the task core free of a
// platform crypto dependency. It is used only for deterministic definition
// identity, not for secret material.
std::string sha256_hex(std::string_view input) {
  constexpr std::array<std::uint32_t, 64> k{
      0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU,
      0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U,
      0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U,
      0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
      0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U,
      0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
      0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
      0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
      0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U, 0xd192e819U,
      0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U, 0x1e376c08U,
      0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU,
      0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
      0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U};
  std::array<std::uint32_t, 8> h{0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U,
                                 0xa54ff53aU, 0x510e527fU, 0x9b05688cU,
                                 0x1f83d9abU, 0x5be0cd19U};
  std::vector<std::uint8_t> bytes(input.begin(), input.end());
  const std::uint64_t bit_length = static_cast<std::uint64_t>(bytes.size()) * 8U;
  bytes.push_back(0x80U);
  while ((bytes.size() % 64U) != 56U) bytes.push_back(0U);
  for (int shift = 56; shift >= 0; shift -= 8) {
    bytes.push_back(static_cast<std::uint8_t>((bit_length >> shift) & 0xffU));
  }

  for (std::size_t offset = 0; offset < bytes.size(); offset += 64) {
    std::array<std::uint32_t, 64> w{};
    for (std::size_t i = 0; i < 16; ++i) {
      const std::size_t j = offset + i * 4;
      w[i] = (static_cast<std::uint32_t>(bytes[j]) << 24U) |
             (static_cast<std::uint32_t>(bytes[j + 1]) << 16U) |
             (static_cast<std::uint32_t>(bytes[j + 2]) << 8U) |
             static_cast<std::uint32_t>(bytes[j + 3]);
    }
    for (std::size_t i = 16; i < 64; ++i) {
      const std::uint32_t s0 = std::rotr(w[i - 15], 7) ^ std::rotr(w[i - 15], 18) ^
                               (w[i - 15] >> 3U);
      const std::uint32_t s1 = std::rotr(w[i - 2], 17) ^ std::rotr(w[i - 2], 19) ^
                               (w[i - 2] >> 10U);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }

    std::uint32_t a = h[0];
    std::uint32_t b = h[1];
    std::uint32_t c = h[2];
    std::uint32_t d = h[3];
    std::uint32_t e = h[4];
    std::uint32_t f = h[5];
    std::uint32_t g = h[6];
    std::uint32_t hh = h[7];
    for (std::size_t i = 0; i < 64; ++i) {
      const std::uint32_t s1 = std::rotr(e, 6) ^ std::rotr(e, 11) ^ std::rotr(e, 25);
      const std::uint32_t ch = (e & f) ^ (~e & g);
      const std::uint32_t temp1 = hh + s1 + ch + k[i] + w[i];
      const std::uint32_t s0 = std::rotr(a, 2) ^ std::rotr(a, 13) ^ std::rotr(a, 22);
      const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
      const std::uint32_t temp2 = s0 + maj;
      hh = g;
      g = f;
      f = e;
      e = d + temp1;
      d = c;
      c = b;
      b = a;
      a = temp1 + temp2;
    }
    h[0] += a;
    h[1] += b;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += hh;
  }

  std::ostringstream out;
  out << std::hex << std::setfill('0');
  for (const auto word : h) out << std::setw(8) << word;
  return out.str();
}

using FieldMap = google::protobuf::Map<std::string, google::protobuf::Value>;

const google::protobuf::Value* field(const FieldMap& fields, std::string_view name) {
  const auto it = fields.find(std::string(name));
  return it == fields.end() ? nullptr : &it->second;
}

DefinitionResult allowed_fields(const FieldMap& fields,
                                std::initializer_list<std::string_view> allowed,
                                std::string_view path) {
  for (const auto& [key, unused] : fields) {
    (void)unused;
    if (std::find(allowed.begin(), allowed.end(), key) == allowed.end()) {
      return fail(DefinitionError::kUnknownField, std::string(path) + "." + key,
                  "unknown field");
    }
  }
  return ok();
}

DefinitionResult require_struct(const google::protobuf::Value* value, std::string_view path,
                                const FieldMap*& fields) {
  if (value == nullptr) {
    return fail(DefinitionError::kMissingField, std::string(path), "missing required field");
  }
  if (value->kind_case() != google::protobuf::Value::kStructValue) {
    return fail(DefinitionError::kWrongType, std::string(path), "must be an object");
  }
  fields = &value->struct_value().fields();
  return ok();
}

DefinitionResult require_list(const google::protobuf::Value* value, std::string_view path,
                              const google::protobuf::ListValue*& list) {
  if (value == nullptr) {
    return fail(DefinitionError::kMissingField, std::string(path), "missing required field");
  }
  if (value->kind_case() != google::protobuf::Value::kListValue) {
    return fail(DefinitionError::kWrongType, std::string(path), "must be an array");
  }
  list = &value->list_value();
  return ok();
}

DefinitionResult require_string(const google::protobuf::Value* value, std::string_view path,
                                std::string& result) {
  if (value == nullptr) {
    return fail(DefinitionError::kMissingField, std::string(path), "missing required field");
  }
  if (value->kind_case() != google::protobuf::Value::kStringValue) {
    return fail(DefinitionError::kWrongType, std::string(path), "must be a string");
  }
  result = value->string_value();
  return ok();
}

DefinitionResult require_bool(const google::protobuf::Value* value, std::string_view path,
                              bool& result) {
  if (value == nullptr) {
    return fail(DefinitionError::kMissingField, std::string(path), "missing required field");
  }
  if (value->kind_case() != google::protobuf::Value::kBoolValue) {
    return fail(DefinitionError::kWrongType, std::string(path), "must be a boolean");
  }
  result = value->bool_value();
  return ok();
}

DefinitionResult require_uint(const google::protobuf::Value* value, std::string_view path,
                              std::uint64_t maximum, std::uint64_t& result) {
  if (value == nullptr) {
    return fail(DefinitionError::kMissingField, std::string(path), "missing required field");
  }
  if (value->kind_case() != google::protobuf::Value::kNumberValue) {
    return fail(DefinitionError::kWrongType, std::string(path), "must be an integer number");
  }
  const double number = value->number_value();
  if (!std::isfinite(number) || number < 0.0 || std::floor(number) != number ||
      number > static_cast<double>(std::min(maximum, kMaxExactJsonInteger))) {
    return fail(DefinitionError::kOutOfRange, std::string(path),
                "must be an exactly represented integer in range");
  }
  result = static_cast<std::uint64_t>(number);
  return ok();
}

}  // namespace

std::string canonical_json(const TaskDefinition& definition) {
  std::vector<const StateDefinition*> states;
  states.reserve(definition.states.size());
  for (const auto& state : definition.states) states.push_back(&state);
  std::sort(states.begin(), states.end(), [](const auto* a, const auto* b) {
    return a->id < b->id;
  });

  std::vector<const TransitionDefinition*> transitions;
  transitions.reserve(definition.transitions.size());
  for (const auto& transition : definition.transitions) transitions.push_back(&transition);
  std::sort(transitions.begin(), transitions.end(), [](const auto* a, const auto* b) {
    return a->id < b->id;
  });

  std::ostringstream out;
  out << "{\"definition_id\":" << json_string(definition.definition_id)
      << ",\"initial_state_id\":" << definition.initial_state_id
      << ",\"revision\":" << definition.revision
      << ",\"schema_version\":" << definition.schema_version
      << ",\"source_policy\":{\"loss_action\":"
      << json_string(loss_action_name(definition.source_policy.loss_action))
      << ",\"loss_timeout_ms\":" << definition.source_policy.loss_timeout_ms
      << ",\"sequence_gap_action\":"
      << json_string(gap_action_name(definition.source_policy.sequence_gap_action))
      << ",\"staged_command_timeout_ms\":"
      << definition.source_policy.staged_command_timeout_ms << "},\"states\":[";
  for (std::size_t i = 0; i < states.size(); ++i) {
    if (i != 0) out << ',';
    out << "{\"id\":" << states[i]->id << ",\"name\":"
        << json_string(states[i]->name) << ",\"terminal\":"
        << (states[i]->terminal ? "true" : "false") << '}';
  }
  out << "],\"transitions\":[";
  for (std::size_t i = 0; i < transitions.size(); ++i) {
    if (i != 0) out << ',';
    const auto& transition = *transitions[i];
    out << "{\"from_state_id\":" << transition.from_state_id
        << ",\"id\":" << transition.id << ",\"name\":"
        << json_string(transition.name) << ",\"priority\":"
        << static_cast<unsigned>(transition.priority) << ",\"to_state_id\":"
        << transition.to_state_id << ",\"trigger\":{";
    if (const auto* external = std::get_if<ExternalEventTrigger>(&transition.trigger)) {
      out << "\"event_name\":" << json_string(external->event_name)
          << ",\"kind\":\"external_event\"";
    } else if (const auto* timer = std::get_if<SourceTimeoutTrigger>(&transition.trigger)) {
      out << "\"after_ns\":" << timer->after_ns << ",\"kind\":\"source_timeout\"";
    } else {
      const auto& decoder = std::get<DecoderPredicateTrigger>(transition.trigger);
      out << "\"dwell_results\":" << decoder.dwell_results
          << ",\"kind\":\"decoder_predicate\",\"label\":" << decoder.label
          << ",\"probability_threshold_ppm\":"
          << decoder.probability_threshold_ppm;
    }
    out << "}}";
  }
  out << "]}";
  return out.str();
}

DefinitionResult validate_and_hash(TaskDefinition& definition,
                                   std::uint32_t configured_num_classes) {
  if (definition.schema_version != kTaskSchemaVersion) {
    return fail(DefinitionError::kOutOfRange, "schema_version", "only schema version 1 is valid");
  }
  if (!valid_name(definition.definition_id)) {
    return fail(DefinitionError::kInvalidName, "definition_id", "invalid definition id");
  }
  if (definition.revision == 0 || definition.revision > kMaxExactJsonInteger) {
    return fail(DefinitionError::kOutOfRange, "revision", "revision is outside the v1 bound");
  }
  if (definition.states.empty() || definition.states.size() > kMaxStates) {
    return fail(DefinitionError::kOutOfRange, "states", "state count is outside the v1 bound");
  }
  if (definition.transitions.size() > kMaxTransitions) {
    return fail(DefinitionError::kOutOfRange, "transitions",
                "transition count exceeds the v1 bound");
  }
  if (definition.source_policy.loss_timeout_ms == 0 ||
      definition.source_policy.loss_timeout_ms > kMaxPolicyTimeoutMs) {
    return fail(DefinitionError::kOutOfRange, "source_policy.loss_timeout_ms",
                "loss timeout is outside the v1 bound");
  }
  if (definition.source_policy.staged_command_timeout_ms == 0 ||
      definition.source_policy.staged_command_timeout_ms > kMaxPolicyTimeoutMs) {
    return fail(DefinitionError::kOutOfRange, "source_policy.staged_command_timeout_ms",
                "staged command timeout is outside the v1 bound");
  }

  std::unordered_map<StateId, const StateDefinition*> state_by_id;
  std::unordered_set<std::string> state_names;
  for (const auto& state : definition.states) {
    if (state.id == kNoState) {
      return fail(DefinitionError::kOutOfRange, "states.id", "state id is outside the v1 bound");
    }
    if (!valid_name(state.name)) {
      return fail(DefinitionError::kInvalidName, "states.name", "invalid state name");
    }
    if (!state_by_id.emplace(state.id, &state).second ||
        !state_names.insert(state.name).second) {
      return fail(DefinitionError::kDuplicate, "states", "state ids and names must be unique");
    }
  }
  if (!state_by_id.contains(definition.initial_state_id)) {
    return fail(DefinitionError::kInvalidReference, "initial_state_id",
                "initial state does not exist");
  }

  std::unordered_set<TransitionId> transition_ids;
  std::unordered_set<std::string> transition_names;
  std::unordered_map<StateId, std::size_t> outgoing_counts;
  std::unordered_map<StateId, std::unordered_set<unsigned>> outgoing_priorities;
  std::unordered_map<StateId, std::unordered_set<std::string>> outgoing_events;
  std::unordered_map<StateId, std::vector<StateId>> adjacency;

  for (const auto& transition : definition.transitions) {
    if (transition.id == 0) {
      return fail(DefinitionError::kOutOfRange, "transitions.id",
                  "transition id is outside the v1 bound");
    }
    if (!valid_name(transition.name)) {
      return fail(DefinitionError::kInvalidName, "transitions.name",
                  "invalid transition name");
    }
    if (!transition_ids.insert(transition.id).second ||
        !transition_names.insert(transition.name).second) {
      return fail(DefinitionError::kDuplicate, "transitions",
                  "transition ids and names must be unique");
    }
    if (!state_by_id.contains(transition.from_state_id) ||
        !state_by_id.contains(transition.to_state_id)) {
      return fail(DefinitionError::kInvalidReference, "transitions.state_id",
                  "transition references a missing state");
    }
    if (++outgoing_counts[transition.from_state_id] > kMaxOutgoingTransitions) {
      return fail(DefinitionError::kOutOfRange, "transitions.from_state_id",
                  "state has too many outgoing transitions");
    }
    if (!outgoing_priorities[transition.from_state_id]
             .insert(static_cast<unsigned>(transition.priority))
             .second) {
      return fail(DefinitionError::kDuplicate, "transitions.priority",
                  "outgoing priorities must be unique within a state");
    }

    if (const auto* external = std::get_if<ExternalEventTrigger>(&transition.trigger)) {
      if (!valid_name(external->event_name)) {
        return fail(DefinitionError::kInvalidTrigger, "transitions.trigger.event_name",
                    "invalid external event name");
      }
      if (!outgoing_events[transition.from_state_id].insert(external->event_name).second) {
        return fail(DefinitionError::kDuplicate, "transitions.trigger.event_name",
                    "external event names must be unique within a state");
      }
    } else if (const auto* timer = std::get_if<SourceTimeoutTrigger>(&transition.trigger)) {
      if (timer->after_ns == 0 || timer->after_ns > kMaxTimeoutNs) {
        return fail(DefinitionError::kInvalidTrigger, "transitions.trigger.after_ns",
                    "source timeout is outside the v1 bound");
      }
    } else {
      const auto& decoder = std::get<DecoderPredicateTrigger>(transition.trigger);
      if (configured_num_classes == 0 || decoder.label >= configured_num_classes) {
        return fail(DefinitionError::kInvalidTrigger, "transitions.trigger.label",
                    "decoder label is outside configured classes");
      }
      if (decoder.probability_threshold_ppm == 0 ||
          decoder.probability_threshold_ppm > kProbabilityScalePpm) {
        return fail(DefinitionError::kInvalidTrigger,
                    "transitions.trigger.probability_threshold_ppm",
                    "decoder threshold is outside the v1 bound");
      }
      if (decoder.dwell_results == 0 ||
          decoder.dwell_results > kMaxDecoderDwellResults) {
        return fail(DefinitionError::kInvalidTrigger, "transitions.trigger.dwell_results",
                    "decoder dwell is outside the v1 bound");
      }
    }
    adjacency[transition.from_state_id].push_back(transition.to_state_id);
  }

  for (const auto& state : definition.states) {
    const std::size_t outgoing = outgoing_counts[state.id];
    if (state.terminal && outgoing != 0) {
      return fail(DefinitionError::kInvalidTopology, "states.terminal",
                  "terminal state has an outgoing transition");
    }
    if (!state.terminal && outgoing == 0) {
      return fail(DefinitionError::kInvalidTopology, "states.terminal",
                  "non-terminal state has no outgoing transition");
    }
  }

  std::unordered_set<StateId> reachable;
  std::queue<StateId> pending;
  reachable.insert(definition.initial_state_id);
  pending.push(definition.initial_state_id);
  while (!pending.empty()) {
    const StateId state = pending.front();
    pending.pop();
    for (const StateId next : adjacency[state]) {
      if (reachable.insert(next).second) pending.push(next);
    }
  }
  if (reachable.size() != definition.states.size()) {
    return fail(DefinitionError::kInvalidTopology, "states",
                "every state must be reachable from the initial state");
  }

  const std::string canonical = canonical_json(definition);
  if (canonical.size() > kMaxCanonicalDefinitionBytes) {
    return fail(DefinitionError::kTooLarge, "task_definition",
                "canonical definition exceeds 64 KiB");
  }
  definition.definition_hash = "sha256:" + sha256_hex(canonical);
  return ok();
}

ParseResult parse_task_definition(const google::protobuf::Value& value,
                                  std::uint32_t configured_num_classes) {
  ParseResult parsed;
  const FieldMap* root = nullptr;
  parsed.result = require_struct(&value, "task_definition", root);
  if (!parsed) return parsed;
  parsed.result = allowed_fields(*root,
                                 {"schema_version", "definition_id", "revision",
                                  "initial_state_id", "states", "transitions",
                                  "source_policy"},
                                 "task_definition");
  if (!parsed) return parsed;

  std::uint64_t number = 0;
  parsed.result = require_uint(field(*root, "schema_version"),
                               "task_definition.schema_version", kTaskSchemaVersion, number);
  if (!parsed) return parsed;
  parsed.definition.schema_version = static_cast<std::uint32_t>(number);
  parsed.result = require_string(field(*root, "definition_id"),
                                 "task_definition.definition_id",
                                 parsed.definition.definition_id);
  if (!parsed) return parsed;
  parsed.result = require_uint(field(*root, "revision"), "task_definition.revision",
                               kMaxExactJsonInteger, parsed.definition.revision);
  if (!parsed) return parsed;
  parsed.result = require_uint(field(*root, "initial_state_id"),
                               "task_definition.initial_state_id", kMaxDefinitionId, number);
  if (!parsed) return parsed;
  parsed.definition.initial_state_id = static_cast<StateId>(number);

  const google::protobuf::ListValue* states = nullptr;
  parsed.result = require_list(field(*root, "states"), "task_definition.states", states);
  if (!parsed) return parsed;
  if (states->values_size() > static_cast<int>(kMaxStates)) {
    parsed.result = fail(DefinitionError::kOutOfRange, "task_definition.states",
                         "too many states");
    return parsed;
  }
  parsed.definition.states.reserve(static_cast<std::size_t>(states->values_size()));
  for (int i = 0; i < states->values_size(); ++i) {
    const std::string path = "task_definition.states[" + std::to_string(i) + "]";
    const FieldMap* state_fields = nullptr;
    parsed.result = require_struct(&states->values(i), path, state_fields);
    if (!parsed) return parsed;
    parsed.result = allowed_fields(*state_fields, {"id", "name", "terminal"}, path);
    if (!parsed) return parsed;
    StateDefinition state;
    parsed.result = require_uint(field(*state_fields, "id"), path + ".id",
                                 kMaxDefinitionId, number);
    if (!parsed) return parsed;
    state.id = static_cast<StateId>(number);
    parsed.result = require_string(field(*state_fields, "name"), path + ".name", state.name);
    if (!parsed) return parsed;
    parsed.result = require_bool(field(*state_fields, "terminal"), path + ".terminal",
                                 state.terminal);
    if (!parsed) return parsed;
    parsed.definition.states.push_back(std::move(state));
  }

  const google::protobuf::ListValue* transitions = nullptr;
  parsed.result = require_list(field(*root, "transitions"), "task_definition.transitions",
                               transitions);
  if (!parsed) return parsed;
  if (transitions->values_size() > static_cast<int>(kMaxTransitions)) {
    parsed.result = fail(DefinitionError::kOutOfRange, "task_definition.transitions",
                         "too many transitions");
    return parsed;
  }
  parsed.definition.transitions.reserve(
      static_cast<std::size_t>(transitions->values_size()));
  for (int i = 0; i < transitions->values_size(); ++i) {
    const std::string path = "task_definition.transitions[" + std::to_string(i) + "]";
    const FieldMap* transition_fields = nullptr;
    parsed.result = require_struct(&transitions->values(i), path, transition_fields);
    if (!parsed) return parsed;
    parsed.result = allowed_fields(*transition_fields,
                                   {"id", "name", "from_state_id", "to_state_id",
                                    "priority", "trigger"},
                                   path);
    if (!parsed) return parsed;
    TransitionDefinition transition;
    parsed.result = require_uint(field(*transition_fields, "id"), path + ".id",
                                 kMaxDefinitionId, number);
    if (!parsed) return parsed;
    transition.id = static_cast<TransitionId>(number);
    parsed.result = require_string(field(*transition_fields, "name"), path + ".name",
                                   transition.name);
    if (!parsed) return parsed;
    parsed.result = require_uint(field(*transition_fields, "from_state_id"),
                                 path + ".from_state_id", kMaxDefinitionId, number);
    if (!parsed) return parsed;
    transition.from_state_id = static_cast<StateId>(number);
    parsed.result = require_uint(field(*transition_fields, "to_state_id"),
                                 path + ".to_state_id", kMaxDefinitionId, number);
    if (!parsed) return parsed;
    transition.to_state_id = static_cast<StateId>(number);
    parsed.result = require_uint(field(*transition_fields, "priority"), path + ".priority",
                                 kMaxPriority, number);
    if (!parsed) return parsed;
    transition.priority = static_cast<std::uint8_t>(number);

    const FieldMap* trigger_fields = nullptr;
    parsed.result = require_struct(field(*transition_fields, "trigger"), path + ".trigger",
                                   trigger_fields);
    if (!parsed) return parsed;
    std::string kind;
    parsed.result = require_string(field(*trigger_fields, "kind"), path + ".trigger.kind",
                                   kind);
    if (!parsed) return parsed;
    if (kind == "external_event") {
      parsed.result = allowed_fields(*trigger_fields, {"kind", "event_name"},
                                     path + ".trigger");
      if (!parsed) return parsed;
      ExternalEventTrigger trigger;
      parsed.result = require_string(field(*trigger_fields, "event_name"),
                                     path + ".trigger.event_name", trigger.event_name);
      if (!parsed) return parsed;
      transition.trigger = std::move(trigger);
    } else if (kind == "source_timeout") {
      parsed.result = allowed_fields(*trigger_fields, {"kind", "after_ns"},
                                     path + ".trigger");
      if (!parsed) return parsed;
      SourceTimeoutTrigger trigger;
      parsed.result = require_uint(field(*trigger_fields, "after_ns"),
                                   path + ".trigger.after_ns", kMaxTimeoutNs,
                                   trigger.after_ns);
      if (!parsed) return parsed;
      transition.trigger = trigger;
    } else if (kind == "decoder_predicate") {
      parsed.result = allowed_fields(
          *trigger_fields,
          {"kind", "label", "probability_threshold_ppm", "dwell_results"},
          path + ".trigger");
      if (!parsed) return parsed;
      DecoderPredicateTrigger trigger;
      parsed.result = require_uint(field(*trigger_fields, "label"), path + ".trigger.label",
                                   std::numeric_limits<std::uint32_t>::max(), number);
      if (!parsed) return parsed;
      trigger.label = static_cast<std::uint32_t>(number);
      parsed.result = require_uint(field(*trigger_fields, "probability_threshold_ppm"),
                                   path + ".trigger.probability_threshold_ppm",
                                   kProbabilityScalePpm, number);
      if (!parsed) return parsed;
      trigger.probability_threshold_ppm = static_cast<std::uint32_t>(number);
      parsed.result = require_uint(field(*trigger_fields, "dwell_results"),
                                   path + ".trigger.dwell_results",
                                   kMaxDecoderDwellResults, number);
      if (!parsed) return parsed;
      trigger.dwell_results = static_cast<std::uint32_t>(number);
      transition.trigger = trigger;
    } else {
      parsed.result = fail(DefinitionError::kInvalidTrigger, path + ".trigger.kind",
                           "unknown trigger kind");
      return parsed;
    }
    parsed.definition.transitions.push_back(std::move(transition));
  }

  const FieldMap* policy = nullptr;
  parsed.result = require_struct(field(*root, "source_policy"),
                                 "task_definition.source_policy", policy);
  if (!parsed) return parsed;
  parsed.result = allowed_fields(*policy,
                                 {"loss_action", "loss_timeout_ms", "sequence_gap_action",
                                  "staged_command_timeout_ms"},
                                 "task_definition.source_policy");
  if (!parsed) return parsed;
  std::string action;
  parsed.result = require_string(field(*policy, "loss_action"),
                                 "task_definition.source_policy.loss_action", action);
  if (!parsed) return parsed;
  if (action == "hold") {
    parsed.definition.source_policy.loss_action = LossAction::kHold;
  } else if (action == "fault") {
    parsed.definition.source_policy.loss_action = LossAction::kFault;
  } else {
    parsed.result = fail(DefinitionError::kOutOfRange,
                         "task_definition.source_policy.loss_action",
                         "loss action must be hold or fault");
    return parsed;
  }
  parsed.result = require_uint(field(*policy, "loss_timeout_ms"),
                               "task_definition.source_policy.loss_timeout_ms",
                               kMaxPolicyTimeoutMs, number);
  if (!parsed) return parsed;
  parsed.definition.source_policy.loss_timeout_ms = static_cast<std::uint32_t>(number);
  parsed.result = require_string(field(*policy, "sequence_gap_action"),
                                 "task_definition.source_policy.sequence_gap_action", action);
  if (!parsed) return parsed;
  if (action == "continue") {
    parsed.definition.source_policy.sequence_gap_action = SequenceGapAction::kContinue;
  } else if (action == "fault") {
    parsed.definition.source_policy.sequence_gap_action = SequenceGapAction::kFault;
  } else {
    parsed.result = fail(DefinitionError::kOutOfRange,
                         "task_definition.source_policy.sequence_gap_action",
                         "sequence gap action must be continue or fault");
    return parsed;
  }
  parsed.result = require_uint(field(*policy, "staged_command_timeout_ms"),
                               "task_definition.source_policy.staged_command_timeout_ms",
                               kMaxPolicyTimeoutMs, number);
  if (!parsed) return parsed;
  parsed.definition.source_policy.staged_command_timeout_ms =
      static_cast<std::uint32_t>(number);

  parsed.result = validate_and_hash(parsed.definition, configured_num_classes);
  return parsed;
}

struct TaskRuntime::StagedProposal {
  ProposalKind kind = ProposalKind::kExternalEvent;
  std::string request_id;
  std::string event_name;
  TransitionId transition_id = 0;
  std::uint64_t receipt_sequence = 0;
  std::uint64_t receipt_time_ns = 0;
};

struct TaskRuntime::DecoderState {
  TransitionId transition_id = 0;
  std::uint32_t consecutive = 0;
  bool ready = false;
  std::uint64_t earliest_frame_ordinal = 0;
  std::uint64_t receipt_sequence = 0;
  std::uint64_t receipt_time_ns = 0;
};

struct TaskRuntime::Candidate {
  EventKind event_kind = EventKind::kTransition;
  TriggerKind trigger_kind = TriggerKind::kExternalEvent;
  std::string trigger_source;
  const TransitionDefinition* transition = nullptr;
  const StagedProposal* proposal = nullptr;
  std::uint8_t priority = 0;
  std::uint64_t receipt_sequence = 0;
  std::uint64_t receipt_time_ns = 0;
  int lifecycle_rank = 1;
};

TaskRuntime::TaskRuntime(TaskDefinition definition, std::string app_session_id,
                         CounterSeed counters)
    : definition_(std::move(definition)),
      app_session_id_(std::move(app_session_id)),
      run_sequence_(counters.run_sequence),
      event_sequence_(counters.event_sequence),
      transition_sequence_(counters.transition_sequence) {
  if (app_session_id_.size() != 32 ||
      !std::all_of(app_session_id_.begin(), app_session_id_.end(), [](char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
      })) {
    throw std::invalid_argument("app_session_id must be 32 lowercase hexadecimal characters");
  }
  if (definition_.definition_hash.size() != 71 ||
      !definition_.definition_hash.starts_with("sha256:") ||
      !std::all_of(definition_.definition_hash.begin() + 7,
                   definition_.definition_hash.end(), [](char c) {
                     return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
                   })) {
    throw std::invalid_argument("task definition must be validated and hashed");
  }
  decoder_states_.reserve(definition_.transitions.size());
  for (const auto& transition : definition_.transitions) {
    if (std::holds_alternative<DecoderPredicateTrigger>(transition.trigger)) {
      decoder_states_.push_back(DecoderState{transition.id});
    }
  }
}

TaskRuntime::~TaskRuntime() = default;

const StateDefinition* TaskRuntime::find_state(StateId id) const {
  const auto it = std::find_if(definition_.states.begin(), definition_.states.end(),
                               [id](const auto& state) { return state.id == id; });
  return it == definition_.states.end() ? nullptr : &*it;
}

const TransitionDefinition* TaskRuntime::find_transition(TransitionId id) const {
  const auto it = std::find_if(definition_.transitions.begin(), definition_.transitions.end(),
                               [id](const auto& transition) { return transition.id == id; });
  return it == definition_.transitions.end() ? nullptr : &*it;
}

RuntimeSnapshot TaskRuntime::snapshot() const {
  return {lifecycle_,          app_session_id_,       run_sequence_,
          event_sequence_,    transition_sequence_, current_state_id_,
          source_healthy_,    fault_reason_,          staged_.size(),
          last_effective_frame_};
}

RuntimeError TaskRuntime::validate_preconditions(const Preconditions& preconditions) const {
  if (preconditions.expected_app_session_id != app_session_id_ ||
      preconditions.expected_run_sequence != run_sequence_ ||
      preconditions.expected_transition_sequence != transition_sequence_) {
    return RuntimeError::kStalePrecondition;
  }
  if (current_state_id_.has_value() != preconditions.expected_state_id.has_value()) {
    return RuntimeError::kStalePrecondition;
  }
  if (current_state_id_ && *current_state_id_ != *preconditions.expected_state_id) {
    return RuntimeError::kStalePrecondition;
  }
  return RuntimeError::kNone;
}

StageResult TaskRuntime::stage(ProposalKind kind, std::string request_id,
                               std::string event_name, TransitionId transition_id,
                               const Preconditions& preconditions,
                               std::uint64_t receipt_time_ns) {
  const RuntimeError precondition_error = validate_preconditions(preconditions);
  if (precondition_error != RuntimeError::kNone) {
    return {false, precondition_error, "task precondition is stale", 0};
  }
  if (request_id.empty() || request_id.size() > 128) {
    return {false, RuntimeError::kInvalidProposal, "invalid request id", 0};
  }
  if (std::any_of(staged_.begin(), staged_.end(), [&](const auto& proposal) {
        return proposal.request_id == request_id;
      })) {
    return {false, RuntimeError::kDuplicate, "request id is already staged", 0};
  }
  if (staged_.size() >= kMaxStagedCommands) {
    return {false, RuntimeError::kQueueFull, "staged task command queue is full", 0};
  }

  switch (kind) {
    case ProposalKind::kStart:
      if (lifecycle_ != Lifecycle::kIdle) {
        return {false, RuntimeError::kInvalidLifecycle, "start requires IDLE", 0};
      }
      break;
    case ProposalKind::kAbort:
      if (lifecycle_ != Lifecycle::kRunning) {
        return {false, RuntimeError::kInvalidLifecycle, "abort requires RUNNING", 0};
      }
      break;
    case ProposalKind::kReset:
      if ((lifecycle_ != Lifecycle::kCompleted && lifecycle_ != Lifecycle::kAborted &&
           lifecycle_ != Lifecycle::kFault) ||
          !source_healthy_) {
        return {false, RuntimeError::kInvalidLifecycle,
                "reset requires a terminal lifecycle and healthy source", 0};
      }
      break;
    case ProposalKind::kExternalEvent:
    case ProposalKind::kTransition:
      if (lifecycle_ != Lifecycle::kRunning || !current_state_id_) {
        return {false, RuntimeError::kInvalidLifecycle,
                "transition proposal requires RUNNING", 0};
      }
      break;
  }

  if (kind == ProposalKind::kExternalEvent) {
    if (!valid_name(event_name)) {
      return {false, RuntimeError::kInvalidProposal, "invalid external event name", 0};
    }
    const bool matches = std::any_of(
        definition_.transitions.begin(), definition_.transitions.end(),
        [&](const auto& transition) {
          const auto* trigger = std::get_if<ExternalEventTrigger>(&transition.trigger);
          return transition.from_state_id == *current_state_id_ && trigger != nullptr &&
                 trigger->event_name == event_name;
        });
    if (!matches) {
      return {false, RuntimeError::kNoMatchingTransition,
              "event does not match an outgoing transition", 0};
    }
  } else if (kind == ProposalKind::kTransition) {
    const auto* transition = find_transition(transition_id);
    if (transition == nullptr || transition->from_state_id != *current_state_id_ ||
        !std::holds_alternative<ExternalEventTrigger>(transition->trigger)) {
      return {false, RuntimeError::kNoMatchingTransition,
              "transition is not an external outgoing transition", 0};
    }
  }

  if (next_proposal_receipt_sequence_ == std::numeric_limits<std::uint64_t>::max()) {
    return {false, RuntimeError::kCounterOverflow, "proposal sequence exhausted", 0};
  }
  const std::uint64_t sequence = next_proposal_receipt_sequence_++;
  staged_.push_back(StagedProposal{kind, std::move(request_id), std::move(event_name),
                                   transition_id, sequence, receipt_time_ns});
  return {true, RuntimeError::kNone, {}, sequence};
}

StageResult TaskRuntime::stage_start(std::string request_id,
                                     const Preconditions& preconditions,
                                     std::uint64_t receipt_time_ns) {
  return stage(ProposalKind::kStart, std::move(request_id), {}, 0, preconditions,
               receipt_time_ns);
}

StageResult TaskRuntime::stage_external_event(std::string request_id,
                                              std::string event_name,
                                              const Preconditions& preconditions,
                                              std::uint64_t receipt_time_ns) {
  return stage(ProposalKind::kExternalEvent, std::move(request_id), std::move(event_name), 0,
               preconditions, receipt_time_ns);
}

StageResult TaskRuntime::stage_transition(std::string request_id,
                                          TransitionId transition_id,
                                          const Preconditions& preconditions,
                                          std::uint64_t receipt_time_ns) {
  return stage(ProposalKind::kTransition, std::move(request_id), {}, transition_id,
               preconditions, receipt_time_ns);
}

StageResult TaskRuntime::stage_abort(std::string request_id,
                                     const Preconditions& preconditions,
                                     std::uint64_t receipt_time_ns) {
  return stage(ProposalKind::kAbort, std::move(request_id), {}, 0, preconditions,
               receipt_time_ns);
}

StageResult TaskRuntime::stage_reset(std::string request_id,
                                     const Preconditions& preconditions,
                                     std::uint64_t receipt_time_ns) {
  return stage(ProposalKind::kReset, std::move(request_id), {}, 0, preconditions,
               receipt_time_ns);
}

void TaskRuntime::observe_decoder(std::uint32_t label, std::uint32_t probability_ppm,
                                  std::uint64_t receipt_time_ns) {
  if (lifecycle_ != Lifecycle::kRunning || !current_state_id_) return;
  for (auto& decoder_state : decoder_states_) {
    const auto* transition = find_transition(decoder_state.transition_id);
    if (transition == nullptr || transition->from_state_id != *current_state_id_) {
      decoder_state = DecoderState{decoder_state.transition_id};
      continue;
    }
    const auto& trigger = std::get<DecoderPredicateTrigger>(transition->trigger);
    if (decoder_state.ready) continue;
    if (trigger.label == label && probability_ppm >= trigger.probability_threshold_ppm) {
      ++decoder_state.consecutive;
      if (decoder_state.consecutive >= trigger.dwell_results) {
        if (next_proposal_receipt_sequence_ ==
            std::numeric_limits<std::uint64_t>::max()) {
          enter_fault("proposal sequence exhausted");
          return;
        }
        decoder_state.ready = true;
        decoder_state.earliest_frame_ordinal = observed_frame_ordinal_ + 1;
        decoder_state.receipt_time_ns = receipt_time_ns;
        decoder_state.receipt_sequence = next_proposal_receipt_sequence_++;
      }
    } else {
      decoder_state.consecutive = 0;
    }
  }
}

std::vector<ProposalResolution> TaskRuntime::expire_staged(std::uint64_t steady_time_ns) {
  std::vector<ProposalResolution> expired;
  const std::uint64_t timeout_ns =
      static_cast<std::uint64_t>(definition_.source_policy.staged_command_timeout_ms) *
      1'000'000ULL;
  auto it = staged_.begin();
  while (it != staged_.end()) {
    const bool timed_out = steady_time_ns >= it->receipt_time_ns &&
                           steady_time_ns - it->receipt_time_ns >= timeout_ns;
    if (!timed_out) {
      ++it;
      continue;
    }
    expired.push_back({it->request_id, it->kind, RuntimeError::kExpired,
                       "staged command expired before a reference boundary"});
    it = staged_.erase(it);
  }
  return expired;
}

void TaskRuntime::enter_fault(std::string reason) {
  lifecycle_ = Lifecycle::kFault;
  source_healthy_ = false;
  fault_reason_ = std::move(reason);
}

SourcePollResult TaskRuntime::poll_source_loss(std::uint64_t steady_time_ns) {
  SourcePollResult result;
  if (definition_.source_policy.loss_action != LossAction::kFault ||
      (lifecycle_ != Lifecycle::kRunning && lifecycle_ != Lifecycle::kCompleted) ||
      !have_last_observed_frame_) {
    return result;
  }
  const std::uint64_t timeout_ns =
      static_cast<std::uint64_t>(definition_.source_policy.loss_timeout_ms) * 1'000'000ULL;
  if (steady_time_ns < last_observed_frame_.steady_time_ns ||
      steady_time_ns - last_observed_frame_.steady_time_ns < timeout_ns) {
    return result;
  }
  enter_fault("reference source loss timeout");
  result.entered_fault = true;
  result.fault_message = fault_reason_;
  for (const auto& proposal : staged_) {
    result.failed_proposals.push_back({proposal.request_id, proposal.kind,
                                       RuntimeError::kSourceFault, fault_reason_});
  }
  staged_.clear();
  return result;
}

void TaskRuntime::mark_source_healthy(bool healthy) { source_healthy_ = healthy; }

bool TaskRuntime::next_event_counters(bool starts_run) {
  if (event_sequence_ == std::numeric_limits<std::uint64_t>::max()) return false;
  if (starts_run) {
    if (run_sequence_ == std::numeric_limits<std::uint64_t>::max()) return false;
    ++run_sequence_;
    transition_sequence_ = 1;
  } else {
    if (transition_sequence_ == std::numeric_limits<std::uint64_t>::max()) return false;
    ++transition_sequence_;
  }
  ++event_sequence_;
  return true;
}

TransitionEvent TaskRuntime::make_event(EventKind kind, TriggerKind trigger_kind,
                                        std::string trigger_source,
                                        TransitionId transition_id,
                                        StateId previous_state_id,
                                        StateId current_state_id,
                                        const FrameBoundary& frame,
                                        const StagedProposal* proposal) const {
  TransitionEvent event;
  event.definition_id = definition_.definition_id;
  event.definition_revision = definition_.revision;
  event.definition_hash = definition_.definition_hash;
  event.app_session_id = app_session_id_;
  event.run_sequence = run_sequence_;
  event.event_sequence = event_sequence_;
  event.transition_sequence = transition_sequence_;
  event.event_kind = kind;
  event.transition_id = transition_id;
  event.previous_state_id = previous_state_id;
  event.current_state_id = current_state_id;
  event.trigger_kind = trigger_kind;
  event.trigger_source = std::move(trigger_source);
  if (proposal != nullptr) {
    event.request_id = proposal->request_id;
    event.proposal_receipt_sequence = proposal->receipt_sequence;
    event.proposal_receipt_time_ns = proposal->receipt_time_ns;
  }
  event.effective_frame = frame;
  return event;
}

void TaskRuntime::fail_all_staged(BoundaryResult& result, RuntimeError error,
                                  std::string_view message,
                                  const StagedProposal* winner) {
  for (const auto& proposal : staged_) {
    if (winner != nullptr && proposal.receipt_sequence == winner->receipt_sequence) continue;
    result.failed_proposals.push_back(
        {proposal.request_id, proposal.kind, error, std::string(message)});
  }
  staged_.clear();
}

BoundaryResult TaskRuntime::on_frame(const FrameBoundary& frame) {
  BoundaryResult result;
  auto expired = expire_staged(frame.steady_time_ns);
  result.failed_proposals.insert(result.failed_proposals.end(),
                                 std::make_move_iterator(expired.begin()),
                                 std::make_move_iterator(expired.end()));

  if (frame.source_id.empty() || frame.source_id.size() > kMaxSourceIdLength) {
    enter_fault("reference source id is empty or too long");
  } else if (have_last_observed_frame_ &&
             (frame.source_id != last_observed_frame_.source_id ||
              frame.sequence_number <= last_observed_frame_.sequence_number ||
              frame.timestamp_ns <= last_observed_frame_.timestamp_ns)) {
    enter_fault("reference source sequence or timestamp is non-monotonic");
  } else if (have_last_observed_frame_ &&
             definition_.source_policy.sequence_gap_action == SequenceGapAction::kFault &&
             frame.sequence_number != last_observed_frame_.sequence_number + 1) {
    enter_fault("reference source sequence gap");
  }
  if (lifecycle_ == Lifecycle::kFault && !fault_reason_.empty() && !source_healthy_) {
    result.entered_fault = true;
    result.fault_error = RuntimeError::kSourceFault;
    result.fault_message = fault_reason_;
    fail_all_staged(result, RuntimeError::kSourceFault, fault_reason_, nullptr);
    return result;
  }

  source_healthy_ = true;
  have_last_observed_frame_ = true;
  last_observed_frame_ = frame;
  ++observed_frame_ordinal_;

  std::vector<Candidate> candidates;
  for (const auto& proposal : staged_) {
    if (proposal.kind == ProposalKind::kStart && lifecycle_ == Lifecycle::kIdle) {
      candidates.push_back({EventKind::kStart, TriggerKind::kStartCommand, "StartTask",
                            nullptr, &proposal, kMaxPriority, proposal.receipt_sequence,
                            proposal.receipt_time_ns, 0});
    } else if (proposal.kind == ProposalKind::kAbort && lifecycle_ == Lifecycle::kRunning) {
      candidates.push_back({EventKind::kAbort, TriggerKind::kAbortCommand, "AbortTask",
                            nullptr, &proposal, kMaxPriority, proposal.receipt_sequence,
                            proposal.receipt_time_ns, -1});
    } else if (proposal.kind == ProposalKind::kReset &&
               (lifecycle_ == Lifecycle::kCompleted || lifecycle_ == Lifecycle::kAborted ||
                lifecycle_ == Lifecycle::kFault)) {
      candidates.push_back({EventKind::kReset, TriggerKind::kResetCommand, "ResetTask",
                            nullptr, &proposal, kMaxPriority, proposal.receipt_sequence,
                            proposal.receipt_time_ns, 0});
    }
  }

  if (lifecycle_ == Lifecycle::kRunning && current_state_id_) {
    for (const auto& transition : definition_.transitions) {
      if (transition.from_state_id != *current_state_id_) continue;
      if (const auto* timer = std::get_if<SourceTimeoutTrigger>(&transition.trigger)) {
        if (frame.timestamp_ns >= state_entry_timestamp_ns_ &&
            frame.timestamp_ns - state_entry_timestamp_ns_ >= timer->after_ns) {
          candidates.push_back({EventKind::kTransition, TriggerKind::kSourceTimeout,
                                transition.name, &transition, nullptr, transition.priority,
                                0, 0, 1});
        }
      }
    }
    for (const auto& decoder : decoder_states_) {
      if (!decoder.ready || decoder.earliest_frame_ordinal > observed_frame_ordinal_) continue;
      const auto* transition = find_transition(decoder.transition_id);
      if (transition == nullptr || transition->from_state_id != *current_state_id_) continue;
      const auto& trigger = std::get<DecoderPredicateTrigger>(transition->trigger);
      candidates.push_back({EventKind::kTransition, TriggerKind::kDecoderPredicate,
                            "decoder_label_" + std::to_string(trigger.label), transition,
                            nullptr, transition->priority, decoder.receipt_sequence,
                            decoder.receipt_time_ns, 1});
    }
    for (const auto& proposal : staged_) {
      if (proposal.kind != ProposalKind::kExternalEvent &&
          proposal.kind != ProposalKind::kTransition) {
        continue;
      }
      for (const auto& transition : definition_.transitions) {
        if (transition.from_state_id != *current_state_id_) continue;
        const auto* external = std::get_if<ExternalEventTrigger>(&transition.trigger);
        if (external == nullptr) continue;
        const bool match = proposal.kind == ProposalKind::kTransition
                               ? proposal.transition_id == transition.id
                               : proposal.event_name == external->event_name;
        if (match) {
          candidates.push_back({EventKind::kTransition, TriggerKind::kExternalEvent,
                                external->event_name, &transition, &proposal,
                                transition.priority, proposal.receipt_sequence,
                                proposal.receipt_time_ns, 1});
        }
      }
    }
  }

  if (candidates.empty()) return result;
  std::sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
    if (a.lifecycle_rank != b.lifecycle_rank) return a.lifecycle_rank < b.lifecycle_rank;
    if (a.priority != b.priority) return a.priority > b.priority;
    const TransitionId a_id = a.transition == nullptr ? 0 : a.transition->id;
    const TransitionId b_id = b.transition == nullptr ? 0 : b.transition->id;
    if (a_id != b_id) return a_id < b_id;
    return a.receipt_sequence < b.receipt_sequence;
  });
  const Candidate winner = candidates.front();
  const StateId previous = current_state_id_.value_or(kNoState);
  StateId current = previous;
  const bool starts_run = winner.event_kind == EventKind::kStart;
  if (!next_event_counters(starts_run)) {
    enter_fault("task event counter overflow");
    result.entered_fault = true;
    result.fault_error = RuntimeError::kCounterOverflow;
    result.fault_message = fault_reason_;
    fail_all_staged(result, RuntimeError::kCounterOverflow, fault_reason_, nullptr);
    return result;
  }

  if (winner.event_kind == EventKind::kStart) {
    current = definition_.initial_state_id;
    current_state_id_ = current;
    lifecycle_ = find_state(current)->terminal ? Lifecycle::kCompleted : Lifecycle::kRunning;
    state_entry_timestamp_ns_ = frame.timestamp_ns;
    fault_reason_.clear();
  } else if (winner.event_kind == EventKind::kAbort) {
    current = kNoState;
    current_state_id_.reset();
    lifecycle_ = Lifecycle::kAborted;
  } else if (winner.event_kind == EventKind::kReset) {
    current = kNoState;
    current_state_id_.reset();
    lifecycle_ = Lifecycle::kIdle;
    fault_reason_.clear();
  } else {
    current = winner.transition->to_state_id;
    current_state_id_ = current;
    lifecycle_ = find_state(current)->terminal ? Lifecycle::kCompleted : Lifecycle::kRunning;
    state_entry_timestamp_ns_ = frame.timestamp_ns;
  }

  auto event = make_event(winner.event_kind, winner.trigger_kind, winner.trigger_source,
                          winner.transition == nullptr ? 0 : winner.transition->id,
                          previous, current, frame, winner.proposal);
  if (winner.proposal == nullptr) {
    event.proposal_receipt_sequence = winner.receipt_sequence;
    event.proposal_receipt_time_ns = winner.receipt_time_ns;
  }
  result.event = std::move(event);
  last_effective_frame_ = frame;
  fail_all_staged(result, RuntimeError::kConflict,
                  "proposal lost deterministic boundary selection", winner.proposal);
  for (auto& decoder : decoder_states_) decoder = DecoderState{decoder.transition_id};
  return result;
}

}  // namespace app::task
