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
class AppState:
    protocol_version: int = 1
    state_version: int = 0
    timestamp_ns: int = 0
    pipeline_state: str = "unspecified"
    source_mode: str = "unspecified"
    active: ActiveTarget = ActiveTarget()
    collections: tuple[CollectionState, ...] = ()
    model: ModelState = ModelState()
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
    return AppState(
        protocol_version=message.protocol_version,
        state_version=message.state_version,
        timestamp_ns=message.timestamp_ns,
        pipeline_state=proto.enum_name(message.pipeline, "state"),
        source_mode=proto.enum_name(message.pipeline, "source_mode"),
        active=active,
        collections=collections,
        model=model,
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
        "last_error": err(state.last_error),
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
