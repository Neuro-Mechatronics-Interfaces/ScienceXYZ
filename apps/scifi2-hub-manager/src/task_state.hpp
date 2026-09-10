#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace google::protobuf {
class Value;
}

namespace scifi2_hub::task {

constexpr std::uint32_t kTaskSchemaVersion = 1;
constexpr std::size_t kMaxCanonicalDefinitionBytes = 64 * 1024;
constexpr std::size_t kMaxNameLength = 64;
constexpr std::size_t kMaxSourceIdLength = 128;
constexpr std::size_t kMaxStates = 64;
constexpr std::size_t kMaxTransitions = 256;
constexpr std::size_t kMaxOutgoingTransitions = 32;
constexpr std::uint16_t kMaxDefinitionId = 65535;
constexpr std::uint8_t kMaxPriority = 255;
constexpr std::uint64_t kMaxTimeoutNs = 86'400'000'000'000ULL;
constexpr std::uint32_t kProbabilityScalePpm = 1'000'000;
constexpr std::uint32_t kMaxDecoderDwellResults = 10'000;
constexpr std::uint32_t kMaxPolicyTimeoutMs = 60'000;
constexpr std::size_t kMaxStagedCommands = 64;

using StateId = std::uint16_t;
using TransitionId = std::uint16_t;
constexpr StateId kNoState = 0;

enum class LossAction { kHold, kFault };
enum class SequenceGapAction { kContinue, kFault };

struct SourcePolicy {
  LossAction loss_action = LossAction::kFault;
  std::uint32_t loss_timeout_ms = 0;
  SequenceGapAction sequence_gap_action = SequenceGapAction::kContinue;
  std::uint32_t staged_command_timeout_ms = 0;
};

struct ExternalEventTrigger {
  std::string event_name;
};

struct SourceTimeoutTrigger {
  std::uint64_t after_ns = 0;
};

struct DecoderPredicateTrigger {
  std::uint32_t label = 0;
  std::uint32_t probability_threshold_ppm = 0;
  std::uint32_t dwell_results = 0;
};

using TransitionTrigger =
    std::variant<ExternalEventTrigger, SourceTimeoutTrigger, DecoderPredicateTrigger>;

struct StateDefinition {
  StateId id = kNoState;
  std::string name;
  bool terminal = false;
};

struct TransitionDefinition {
  TransitionId id = 0;
  std::string name;
  StateId from_state_id = kNoState;
  StateId to_state_id = kNoState;
  std::uint8_t priority = 0;
  TransitionTrigger trigger;
};

struct TaskDefinition {
  std::uint32_t schema_version = kTaskSchemaVersion;
  std::string definition_id;
  std::uint64_t revision = 0;
  StateId initial_state_id = kNoState;
  std::vector<StateDefinition> states;
  std::vector<TransitionDefinition> transitions;
  SourcePolicy source_policy;
  std::string definition_hash;
};

enum class DefinitionError {
  kNone,
  kWrongType,
  kUnknownField,
  kMissingField,
  kOutOfRange,
  kInvalidName,
  kDuplicate,
  kInvalidReference,
  kInvalidTopology,
  kInvalidTrigger,
  kTooLarge,
  kInternal,
};

struct DefinitionResult {
  DefinitionError error = DefinitionError::kNone;
  std::string field;
  std::string message;

  explicit operator bool() const { return error == DefinitionError::kNone; }
};

struct ParseResult {
  TaskDefinition definition;
  DefinitionResult result;

  explicit operator bool() const { return static_cast<bool>(result); }
};

// Parses the nested ApplicationNodeConfig.parameters["task_definition"] Value.
// This depends only on protobuf Struct, not on the Synapse SDK.
ParseResult parse_task_definition(const google::protobuf::Value& value,
                                  std::uint32_t configured_num_classes);

// Validates a programmatically constructed definition, normalizes it for
// hashing, and writes the computed sha256:... digest to definition_hash.
DefinitionResult validate_and_hash(TaskDefinition& definition,
                                   std::uint32_t configured_num_classes);

std::string canonical_json(const TaskDefinition& definition);

enum class Lifecycle { kIdle, kRunning, kCompleted, kAborted, kFault };
enum class ProposalKind { kStart, kExternalEvent, kTransition, kAbort, kReset };
enum class EventKind { kStart, kTransition, kAbort, kReset };
enum class TriggerKind {
  kStartCommand,
  kExternalEvent,
  kSourceTimeout,
  kDecoderPredicate,
  kAbortCommand,
  kResetCommand,
};

enum class RuntimeError {
  kNone,
  kInvalidLifecycle,
  kStalePrecondition,
  kInvalidProposal,
  kNoMatchingTransition,
  kQueueFull,
  kDuplicate,
  kExpired,
  kConflict,
  kSourceFault,
  kCounterOverflow,
};

struct Preconditions {
  std::string expected_app_session_id;
  std::uint64_t expected_run_sequence = 0;
  std::uint64_t expected_transition_sequence = 0;
  std::optional<StateId> expected_state_id;
};

struct StageResult {
  bool accepted = false;
  RuntimeError error = RuntimeError::kNone;
  std::string message;
  std::uint64_t proposal_receipt_sequence = 0;
};

struct ProposalResolution {
  std::string request_id;
  ProposalKind kind = ProposalKind::kExternalEvent;
  RuntimeError error = RuntimeError::kNone;
  std::string message;
};

struct FrameBoundary {
  std::string source_id;
  std::uint64_t sequence_number = 0;
  std::uint64_t timestamp_ns = 0;
  std::uint64_t steady_time_ns = 0;
};

struct TransitionEvent {
  std::string definition_id;
  std::uint64_t definition_revision = 0;
  std::string definition_hash;
  std::string app_session_id;
  std::uint64_t run_sequence = 0;
  std::uint64_t event_sequence = 0;
  std::uint64_t transition_sequence = 0;
  EventKind event_kind = EventKind::kTransition;
  TransitionId transition_id = 0;
  StateId previous_state_id = kNoState;
  StateId current_state_id = kNoState;
  TriggerKind trigger_kind = TriggerKind::kExternalEvent;
  std::string trigger_source;
  std::string request_id;
  std::uint64_t proposal_receipt_sequence = 0;
  std::uint64_t proposal_receipt_time_ns = 0;
  FrameBoundary effective_frame;
};

struct BoundaryResult {
  std::optional<TransitionEvent> event;
  std::vector<ProposalResolution> failed_proposals;
  bool entered_fault = false;
  RuntimeError fault_error = RuntimeError::kNone;
  std::string fault_message;
};

struct SourcePollResult {
  bool entered_fault = false;
  std::vector<ProposalResolution> failed_proposals;
  std::string fault_message;
};

struct RuntimeSnapshot {
  Lifecycle lifecycle = Lifecycle::kIdle;
  std::string app_session_id;
  std::uint64_t run_sequence = 0;
  std::uint64_t event_sequence = 0;
  std::uint64_t transition_sequence = 0;
  std::optional<StateId> current_state_id;
  bool source_healthy = true;
  std::string fault_reason;
  std::size_t staged_commands = 0;
  std::optional<FrameBoundary> last_effective_frame;
};

struct CounterSeed {
  std::uint64_t run_sequence = 0;
  std::uint64_t event_sequence = 0;
  std::uint64_t transition_sequence = 0;
};

class TaskRuntime {
 public:
  TaskRuntime(TaskDefinition definition, std::string app_session_id,
              CounterSeed counters = {});
  ~TaskRuntime();

  const TaskDefinition& definition() const { return definition_; }
  RuntimeSnapshot snapshot() const;

  StageResult stage_start(std::string request_id, const Preconditions& preconditions,
                          std::uint64_t receipt_time_ns);
  StageResult stage_external_event(std::string request_id, std::string event_name,
                                   const Preconditions& preconditions,
                                   std::uint64_t receipt_time_ns);
  StageResult stage_transition(std::string request_id, TransitionId transition_id,
                               const Preconditions& preconditions,
                               std::uint64_t receipt_time_ns);
  StageResult stage_abort(std::string request_id, const Preconditions& preconditions,
                          std::uint64_t receipt_time_ns);
  StageResult stage_reset(std::string request_id, const Preconditions& preconditions,
                          std::uint64_t receipt_time_ns);

  // Called after a decoder result is produced. A qualifying dwell can only
  // commit on a later on_frame() call.
  void observe_decoder(std::uint32_t label, std::uint32_t probability_ppm,
                       std::uint64_t receipt_time_ns);

  BoundaryResult on_frame(const FrameBoundary& frame);
  std::vector<ProposalResolution> expire_staged(std::uint64_t steady_time_ns);
  SourcePollResult poll_source_loss(std::uint64_t steady_time_ns);
  void mark_source_healthy(bool healthy);
  // An integration publication failure invalidates the externally observable
  // authoritative timeline. It faults without fabricating another event.
  void fault_for_publication_failure(std::string reason);

 private:
  struct StagedProposal;
  struct DecoderState;
  struct Candidate;

  StageResult stage(ProposalKind kind, std::string request_id, std::string event_name,
                    TransitionId transition_id, const Preconditions& preconditions,
                    std::uint64_t receipt_time_ns);
  RuntimeError validate_preconditions(const Preconditions& preconditions) const;
  const StateDefinition* find_state(StateId id) const;
  const TransitionDefinition* find_transition(TransitionId id) const;
  void enter_fault(std::string reason);
  bool next_event_counters(bool starts_run);
  TransitionEvent make_event(EventKind kind, TriggerKind trigger_kind,
                             std::string trigger_source, TransitionId transition_id,
                             StateId previous_state_id, StateId current_state_id,
                             const FrameBoundary& frame,
                             const StagedProposal* proposal) const;
  void fail_all_staged(BoundaryResult& result, RuntimeError error,
                       std::string_view message, const StagedProposal* winner);

  TaskDefinition definition_;
  std::string app_session_id_;
  Lifecycle lifecycle_ = Lifecycle::kIdle;
  std::optional<StateId> current_state_id_;
  std::uint64_t run_sequence_ = 0;
  std::uint64_t event_sequence_ = 0;
  std::uint64_t transition_sequence_ = 0;
  std::uint64_t next_proposal_receipt_sequence_ = 1;
  std::uint64_t observed_frame_ordinal_ = 0;
  std::uint64_t state_entry_timestamp_ns_ = 0;
  bool source_healthy_ = true;
  bool have_last_observed_frame_ = false;
  FrameBoundary last_observed_frame_;
  std::optional<FrameBoundary> last_effective_frame_;
  std::string fault_reason_;
  std::vector<StagedProposal> staged_;
  std::vector<DecoderState> decoder_states_;
};

}  // namespace scifi2_hub::task
