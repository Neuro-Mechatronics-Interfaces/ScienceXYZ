"""Immutable host-side representations of device state and command results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import proto


class ProtocolMessageError(ValueError):
    """A decoded protobuf does not satisfy the host-side protocol contract."""


@dataclass(frozen=True)
class ErrorState:
    code: str
    message: str
    field: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class LabelState:
    label: int
    count: int
    capacity: int

    @property
    def fraction(self) -> float | None:
        return self.count / self.capacity if self.capacity else None


@dataclass(frozen=True)
class CollectionState:
    collection_id: int
    feature_dimension: int
    data_generation: int
    labels: tuple[LabelState, ...]


@dataclass(frozen=True)
class ActiveTarget:
    collection_id: int = 0
    label: int = 0
    capture_enabled: bool = False


@dataclass(frozen=True)
class ModelState:
    phase: str = "idle"
    ready: bool = False
    source_collection_id: int | None = None
    source_generation: int | None = None
    stale: bool = False
    epoch: int = 0
    total_epochs: int = 0
    loss: float = 0.0
    accuracy: float = 0.0
    duration_ms: int = 0


@dataclass(frozen=True)
class TaskFrameBoundary:
    source_id: str
    sequence_number: int
    timestamp_ns: int


@dataclass(frozen=True)
class TaskState:
    configured: bool = False
    definition_id: str = ""
    definition_revision: int = 0
    definition_hash: str = ""
    app_session_id: str = ""
    lifecycle: str = "unspecified"
    run_sequence: int = 0
    event_sequence: int = 0
    transition_sequence: int = 0
    current_state_id: int | None = None
    pending_command_count: int = 0
    source_healthy: bool = False
    fault_reason: str = ""
    last_effective_frame: TaskFrameBoundary | None = None


@dataclass(frozen=True)
class TaskTransition:
    protocol_version: int
    definition_id: str
    definition_revision: int
    definition_hash: str
    app_session_id: str
    run_sequence: int
    event_sequence: int
    transition_sequence: int
    event_kind: str
    transition_id: int
    previous_state_id: int
    current_state_id: int
    trigger_kind: str
    trigger_source: str
    request_id: str
    proposal_receipt_sequence: int
    proposal_receipt_time_ns: int
    effective_frame: TaskFrameBoundary


@dataclass(frozen=True)
class AppState:
    protocol_version: int = 1
    state_version: int = 0
    timestamp_ns: int = 0
    pipeline_state: str = "unspecified"
    source_mode: str = "unspecified"
    active: ActiveTarget = ActiveTarget()
    collections: tuple[CollectionState, ...] = ()
    model: ModelState = ModelState()
    task: TaskState = TaskState()
    last_error: ErrorState | None = None

    @property
    def connected(self) -> bool:
        return self.pipeline_state not in {"disconnected", "unspecified"}

    @property
    def active_collection(self) -> CollectionState | None:
        return next((c for c in self.collections if c.collection_id == self.active.collection_id), None)


@dataclass(frozen=True)
class FitProgress:
    epoch: int
    total_epochs: int
    loss: float
    accuracy: float


@dataclass(frozen=True)
class CommandResult:
    protocol_version: int
    request_id: str
    command: str
    status: str
    state_version: int
    error: ErrorState | None = None
    progress: FitProgress | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


def _error(message) -> ErrorState:
    return ErrorState(
        code=proto.enum_name(message, "code"),
        message=message.message,
        field=message.field,
        retryable=message.retryable,
    )


def _command_name(message) -> str:
    return proto.enum_name(message, "command")


def _frame_from_proto(message) -> TaskFrameBoundary:
    if not message.source_id:
        raise ProtocolMessageError("task frame boundary has no source id")
    return TaskFrameBoundary(message.source_id, message.sequence_number, message.timestamp_ns)


def _task_from_proto(message) -> TaskState:
    lifecycle = proto.enum_name(message, "lifecycle")
    if lifecycle == "unspecified":
        raise ProtocolMessageError("task status has an unknown lifecycle")
    if message.configured and (not message.definition_id or not message.definition_hash or not message.app_session_id):
        raise ProtocolMessageError("configured task status is missing identity")
    return TaskState(
        configured=message.configured,
        definition_id=message.definition_id,
        definition_revision=message.definition_revision,
        definition_hash=message.definition_hash,
        app_session_id=message.app_session_id,
        lifecycle=lifecycle,
        run_sequence=message.run_sequence,
        event_sequence=message.event_sequence,
        transition_sequence=message.transition_sequence,
        current_state_id=message.current_state_id if message.has_current_state_id else None,
        pending_command_count=message.staged_command_count,
        source_healthy=message.source_healthy,
        fault_reason=message.fault_reason,
        last_effective_frame=_frame_from_proto(message.last_effective_frame) if message.has_effective_frame else None,
    )


def task_transition_from_proto(message) -> TaskTransition:
    if message.protocol_version != 1:
        raise ProtocolMessageError("unsupported task-transition protocol version")
    event_kind = proto.enum_name(message, "event_kind")
    trigger_kind = proto.enum_name(message, "trigger_kind")
    if (not message.definition_id or not message.definition_hash or not message.app_session_id
            or message.run_sequence == 0 or message.event_sequence == 0
            or message.transition_sequence == 0 or event_kind == "unspecified"
            or trigger_kind == "unspecified" or not message.HasField("effective_frame")):
        raise ProtocolMessageError("task transition is missing required authority fields")
    return TaskTransition(
        protocol_version=message.protocol_version,
        definition_id=message.definition_id,
        definition_revision=message.definition_revision,
        definition_hash=message.definition_hash,
        app_session_id=message.app_session_id,
        run_sequence=message.run_sequence,
        event_sequence=message.event_sequence,
        transition_sequence=message.transition_sequence,
        event_kind=event_kind,
        transition_id=message.transition_id,
        previous_state_id=message.previous_state_id,
        current_state_id=message.current_state_id,
        trigger_kind=trigger_kind,
        trigger_source=message.trigger_source,
        request_id=message.request_id,
        proposal_receipt_sequence=message.proposal_receipt_sequence,
        proposal_receipt_time_ns=message.proposal_receipt_time_ns,
        effective_frame=_frame_from_proto(message.effective_frame),
    )


def state_from_proto(message) -> AppState:
    if message.protocol_version != 1:
        raise ProtocolMessageError("unsupported state protocol version")
    if not message.HasField("pipeline") or not message.HasField("active") or not message.HasField("model"):
        raise ProtocolMessageError("state snapshot is missing a required section")
    if proto.enum_name(message.pipeline, "state") == "unspecified":
        raise ProtocolMessageError("state snapshot has an unknown pipeline state")
    if proto.enum_name(message.pipeline, "source_mode") == "unspecified":
        raise ProtocolMessageError("state snapshot has an unknown source mode")
    collections = tuple(
        CollectionState(
            collection_id=item.collection_id,
            feature_dimension=item.feature_dimension,
            data_generation=item.data_generation,
            labels=tuple(LabelState(x.label, x.count, x.capacity) for x in item.labels),
        )
        for item in message.collections
    )
    active = ActiveTarget(
        collection_id=message.active.collection_id,
        label=message.active.label,
        capture_enabled=message.active.capture_enabled,
    )
    model = ModelState(
        phase=proto.enum_name(message.model, "phase"),
        ready=message.model.ready,
        source_collection_id=message.model.source_collection_id if message.model.has_source_collection_id else None,
        source_generation=message.model.source_generation if message.model.has_source_collection_id else None,
        stale=message.model.stale,
        epoch=message.model.epoch,
        total_epochs=message.model.total_epochs,
        loss=message.model.loss,
        accuracy=message.model.accuracy,
        duration_ms=message.model.duration_ms,
    )
    task = _task_from_proto(message.task) if message.HasField("task") else TaskState()
    return AppState(
        protocol_version=message.protocol_version,
        state_version=message.state_version,
        timestamp_ns=message.timestamp_ns,
        pipeline_state=proto.enum_name(message.pipeline, "state"),
        source_mode=proto.enum_name(message.pipeline, "source_mode"),
        active=active,
        collections=collections,
        model=model,
        task=task,
        last_error=_error(message.last_error) if message.HasField("last_error") else None,
    )


def result_from_proto(message) -> CommandResult:
    if message.protocol_version != 1:
        raise ProtocolMessageError("unsupported command-result protocol version")
    if not message.request_id:
        raise ProtocolMessageError("command result has no request id")
    if _command_name(message) == "unspecified":
        raise ProtocolMessageError("command result has an unknown command")
    status = proto.enum_name(message, "status")
    if status not in {"accepted", "succeeded", "failed"}:
        raise ProtocolMessageError("command result has an unknown status")
    if status == "failed" and not message.HasField("error"):
        raise ProtocolMessageError("failed command result has no error")
    progress = None
    if message.HasField("progress"):
        progress = FitProgress(
            epoch=message.progress.epoch,
            total_epochs=message.progress.total_epochs,
            loss=message.progress.loss,
            accuracy=message.progress.accuracy,
        )
    return CommandResult(
        protocol_version=message.protocol_version,
        request_id=message.request_id,
        command=_command_name(message),
        status=proto.enum_name(message, "status"),
        state_version=message.state_version,
        error=_error(message.error) if message.HasField("error") else None,
        progress=progress,
    )


def state_to_json(state: AppState) -> dict[str, Any]:
    def err(value):
        return None if value is None else {
            "code": value.code, "message": value.message,
            "field": value.field, "retryable": value.retryable,
        }

    return {
        "type": "state",
        "protocol_version": state.protocol_version,
        "state_version": str(state.state_version),
        "timestamp_ns": str(state.timestamp_ns),
        "pipeline": {"state": state.pipeline_state, "source_mode": state.source_mode},
        "active": {
            "collection_id": state.active.collection_id,
            "label": state.active.label,
            "capture_enabled": state.active.capture_enabled,
        },
        "collections": [
            {
                "collection_id": c.collection_id,
                "feature_dimension": c.feature_dimension,
                "data_generation": str(c.data_generation),
                "labels": [
                    {"label": x.label, "count": x.count, "capacity": x.capacity}
                    for x in c.labels
                ],
            }
            for c in state.collections
        ],
        "model": {
            "phase": state.model.phase,
            "ready": state.model.ready,
            "source_collection_id": state.model.source_collection_id,
            "source_generation": None if state.model.source_generation is None else str(state.model.source_generation),
            "stale": state.model.stale,
            "epoch": state.model.epoch,
            "total_epochs": state.model.total_epochs,
            "loss": state.model.loss,
            "accuracy": state.model.accuracy,
            "duration_ms": str(state.model.duration_ms),
        },
        "task": {
            "configured": state.task.configured,
            "definition_id": state.task.definition_id,
            "definition_revision": str(state.task.definition_revision),
            "definition_hash": state.task.definition_hash,
            "app_session_id": state.task.app_session_id,
            "lifecycle": state.task.lifecycle,
            "run_sequence": str(state.task.run_sequence),
            "event_sequence": str(state.task.event_sequence),
            "transition_sequence": str(state.task.transition_sequence),
            "current_state_id": state.task.current_state_id,
            "pending_command_count": state.task.pending_command_count,
            "source_healthy": state.task.source_healthy,
            "fault_reason": state.task.fault_reason,
            "last_effective_frame": None if state.task.last_effective_frame is None else {
                "source_id": state.task.last_effective_frame.source_id,
                "sequence_number": str(state.task.last_effective_frame.sequence_number),
                "timestamp_ns": str(state.task.last_effective_frame.timestamp_ns),
            },
        },
        "last_error": err(state.last_error),
    }


def task_transition_to_json(event: TaskTransition) -> dict[str, Any]:
    return {
        "type": "task_transition",
        "protocol_version": event.protocol_version,
        "definition_id": event.definition_id,
        "definition_revision": str(event.definition_revision),
        "definition_hash": event.definition_hash,
        "app_session_id": event.app_session_id,
        "run_sequence": str(event.run_sequence),
        "event_sequence": str(event.event_sequence),
        "transition_sequence": str(event.transition_sequence),
        "event_kind": event.event_kind,
        "transition_id": event.transition_id,
        "previous_state_id": event.previous_state_id,
        "current_state_id": event.current_state_id,
        "trigger_kind": event.trigger_kind,
        "trigger_source": event.trigger_source,
        "request_id": event.request_id,
        "proposal_receipt_sequence": str(event.proposal_receipt_sequence),
        "proposal_receipt_time_ns": str(event.proposal_receipt_time_ns),
        "effective_frame": {
            "source_id": event.effective_frame.source_id,
            "sequence_number": str(event.effective_frame.sequence_number),
            "timestamp_ns": str(event.effective_frame.timestamp_ns),
        },
    }


def result_to_json(result: CommandResult) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "result",
        "protocol_version": result.protocol_version,
        "request_id": result.request_id,
        "command": result.command,
        "status": result.status,
        "state_version": str(result.state_version),
        "error": None if result.error is None else {
            "code": result.error.code, "message": result.error.message,
            "field": result.error.field, "retryable": result.error.retryable,
        },
    }
    if result.progress is not None:
        value["progress"] = {
            "epoch": result.progress.epoch,
            "total_epochs": result.progress.total_epochs,
            "loss": result.progress.loss,
            "accuracy": result.progress.accuracy,
        }
    return value
