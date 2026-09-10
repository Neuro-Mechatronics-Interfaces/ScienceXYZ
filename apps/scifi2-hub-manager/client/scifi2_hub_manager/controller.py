"""Threaded, replaceable-transport controller for the device app."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Callable
from google.protobuf.message import DecodeError

from . import proto
from .model import (AppState, CommandResult, ErrorState, ProtocolMessageError,
                    TaskState, TaskTransition, result_from_proto, state_from_proto,
                    task_transition_from_proto)
from .transport import SynapseTapTransport, TapTransport, TransportError


class ControllerError(RuntimeError):
    pass


class DeviceCommandError(ControllerError):
    def __init__(self, result: CommandResult):
        self.result = result
        detail = result.error.message if result.error else result.status
        super().__init__(f"{result.command} failed: {detail}")


class StaleTaskProposalError(ControllerError):
    """A caller tried to use task preconditions older than the current snapshot."""


@dataclass(frozen=True)
class TaskPreconditions:
    app_session_id: str
    run_sequence: int
    transition_sequence: int
    state_id: int | None


@dataclass
class _Pending:
    event: threading.Event
    command: int
    terminal: CommandResult | None = None
    failure: Exception | None = None


class BroadbandController:
    """Own all app Tap connections and publish immutable state replacements."""

    CONTROL = "control"
    STATE = "state"
    RESULTS = "command_result"
    TASK_TRANSITIONS = "task_transition"

    def __init__(self, device_ip: str, transport: TapTransport | None = None, *, timeout: float = 5.0):
        self.device_ip = device_ip
        self.transport = transport or SynapseTapTransport(device_ip)
        self.timeout = timeout
        self._connected = False
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._completed: dict[str, CommandResult] = {}
        self._completed_limit = 1024
        self._state = AppState()
        self._state_lock = threading.Lock()
        self._state_callbacks: list[Callable[[AppState], None]] = []
        self._result_callbacks: list[Callable[[CommandResult], None]] = []
        self._task_transition_callbacks: list[Callable[[TaskTransition], None]] = []
        self._threads: list[threading.Thread] = []
        self._lifecycle_lock = threading.Lock()
        self._snapshot_version: int | None = None
        self._task_snapshot_ready = threading.Event()
        self._task_event_session_id: str | None = None
        self._task_event_sequence: int | None = None

    @property
    def connected(self) -> bool:
        with self._lifecycle_lock:
            return self._connected

    @property
    def state(self) -> AppState:
        with self._state_lock:
            return self._state

    def on_state(self, callback: Callable[[AppState], None]) -> None:
        with self._state_lock:
            self._state_callbacks.append(callback)

    def on_result(self, callback: Callable[[CommandResult], None]) -> None:
        with self._state_lock:
            self._result_callbacks.append(callback)

    def on_task_transition(self, callback: Callable[[TaskTransition], None]) -> None:
        with self._state_lock:
            self._task_transition_callbacks.append(callback)

    def connect(self) -> None:
        with self._lifecycle_lock:
            if self._connected:
                return
            opened = []
            try:
                for name in (self.CONTROL, self.STATE, self.RESULTS, self.TASK_TRANSITIONS):
                    if not self.transport.connect(name):
                        raise ControllerError(f"could not connect to tap '{name}'")
                    opened.append(name)

                self._stop.clear()
                self._connected = True
                self._snapshot_version = None
                self._task_snapshot_ready.clear()
                self._task_event_session_id = None
                self._task_event_sequence = None
                for name, target in ((self.STATE, self._read_state), (self.RESULTS, self._read_result),
                                     (self.TASK_TRANSITIONS, self._read_task_transition)):
                    thread = threading.Thread(target=target, name=f"synapse-{name}", daemon=True)
                    thread.start()
                    self._threads.append(thread)

                # Ask for the initial complete snapshot without making connection
                # depend on a command-result race during the Tap slow-joiner window.
                self._send_no_wait(self._new_command("subscribe_state"), enabled=True)
                self._send_no_wait(self._new_command("get_state"))
            except Exception:
                self._connected = False
                self._stop.set()
                for name in opened:
                    try:
                        self.transport.disconnect(name)
                    except Exception:
                        pass
                for thread in self._threads:
                    thread.join(timeout=0.25)
                self._threads.clear()
                self._publish_disconnected_state("connect failed")
                raise

    def connect_with_backoff(self, attempts: int = 4, initial_delay: float = 0.25) -> None:
        """Connect with bounded retry delays for a temporarily unavailable device."""
        if attempts < 1:
            raise ValueError("attempts must be positive")
        if initial_delay < 0:
            raise ValueError("initial_delay must be non-negative")
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                self.connect()
                return
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(initial_delay * (2 ** attempt), 2.0))
        raise ControllerError(f"unable to connect after {attempts} attempts: {last_error}") from last_error

    def reconnect_with_backoff(self, attempts: int = 4, initial_delay: float = 0.25) -> None:
        """Close the current session, then reconnect with bounded exponential backoff."""
        self.disconnect()
        self.connect_with_backoff(attempts, initial_delay)

    def disconnect(self) -> None:
        with self._lifecycle_lock:
            was_connected = self._connected
            self._connected = False
            self._stop.set()
        self._task_snapshot_ready.clear()
        for name in (self.CONTROL, self.STATE, self.RESULTS, self.TASK_TRANSITIONS):
            try:
                self.transport.disconnect(name)
            except Exception:
                pass
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.failure = ControllerError("controller disconnected")
            item.event.set()
        for thread in self._threads:
            thread.join(timeout=0.25)
        self._threads.clear()
        if was_connected or self.state.pipeline_state != "disconnected":
            self._mark_disconnected("controller disconnected")

    def _mark_disconnected(self, message: str) -> None:
        with self._lifecycle_lock:
            self._connected = False
            self._stop.set()
        self._publish_disconnected_state(message)

    def _publish_disconnected_state(self, message: str) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.failure = ControllerError("transport disconnected")
            item.event.set()
        with self._state_lock:
            current = self._state
            value = replace(
                current,
                pipeline_state="disconnected",
                last_error=ErrorState("transport_disconnected", message, retryable=True),
            )
            self._state = value
            callbacks = tuple(self._state_callbacks)
        self._notify_state(callbacks, value)

    def _record_protocol_error(self, message: str) -> None:
        with self._state_lock:
            current = self._state
            value = replace(current, last_error=ErrorState("malformed", message))
            self._state = value
            callbacks = tuple(self._state_callbacks)
        self._notify_state(callbacks, value)

    @staticmethod
    def _notify_state(callbacks, value: AppState) -> None:
        for callback in callbacks:
            try:
                callback(value)
            except Exception:
                pass

    def _read_state(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.transport.receive(self.STATE, timeout=0.5)
                if raw is None:
                    continue
                message = proto.StateSnapshot()
                message.ParseFromString(raw)
                value = state_from_proto(message)
                with self._state_lock:
                    if self._snapshot_version is not None and value.state_version < self._snapshot_version:
                        continue
                    self._snapshot_version = value.state_version
                    self._state = value
                    self._task_snapshot_ready.set()
                    callbacks = tuple(self._state_callbacks)
                self._notify_state(callbacks, value)
            except (DecodeError, ProtocolMessageError, ValueError, TypeError) as exc:
                self._record_protocol_error(str(exc))
            except (TransportError, ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(str(exc))
                    return
            except Exception as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(f"state tap failed: {exc}")
                    return

    def _read_task_transition(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.transport.receive(self.TASK_TRANSITIONS, timeout=0.5)
                if raw is None:
                    continue
                message = proto.TaskTransitionEvent()
                message.ParseFromString(raw)
                value = task_transition_from_proto(message)
                gap_message = None
                with self._state_lock:
                    if value.app_session_id != self._task_event_session_id:
                        self._task_event_session_id = value.app_session_id
                        self._task_event_sequence = None
                    if (self._task_event_sequence is not None
                            and value.event_sequence != self._task_event_sequence + 1):
                        gap_message = (
                            f"task transition sequence gap: expected {self._task_event_sequence + 1}, got {value.event_sequence}")
                    self._task_event_sequence = value.event_sequence
                    callbacks = tuple(self._task_transition_callbacks)
                if gap_message is not None:
                    self._record_protocol_error(gap_message)
                for callback in callbacks:
                    try:
                        callback(value)
                    except Exception:
                        pass
            except (DecodeError, ProtocolMessageError, ValueError, TypeError) as exc:
                self._record_protocol_error(str(exc))
            except (TransportError, ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(str(exc))
                    return
            except Exception as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(f"task-transition tap failed: {exc}")
                    return

    def _read_result(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.transport.receive(self.RESULTS, timeout=0.5)
                if raw is None:
                    continue
                message = proto.CommandResult()
                message.ParseFromString(raw)
                result = result_from_proto(message)
                with self._state_lock:
                    callbacks = tuple(self._result_callbacks)
                for callback in callbacks:
                    try:
                        callback(result)
                    except Exception:
                        pass
                if result.terminal:
                    with self._pending_lock:
                        pending = self._pending.get(result.request_id)
                        if pending is not None and pending.command != proto.COMMAND[result.command]:
                            pending = None
                        elif pending is not None:
                            self._pending.pop(result.request_id, None)
                            self._completed[result.request_id] = result
                            if len(self._completed) > self._completed_limit:
                                self._completed.pop(next(iter(self._completed)))
                    if pending is not None:
                        pending.terminal = result
                        pending.event.set()
            except (DecodeError, ProtocolMessageError, ValueError, TypeError) as exc:
                self._record_protocol_error(str(exc))
            except (TransportError, ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(str(exc))
                    return
            except Exception as exc:
                if not self._stop.is_set():
                    self._mark_disconnected(f"result tap failed: {exc}")
                    return

    def _new_command(self, name: str, request_id: str | None = None):
        command = proto.ControlCommand()
        command.protocol_version = 1
        command.request_id = request_id or f"client/{uuid.uuid4().hex}"
        command.command = proto.COMMAND[name]
        if name == "get_state":
            command.get_state.SetInParent()
        return command

    def _send_no_wait(self, command, *, enabled: bool | None = None) -> None:
        if enabled is not None:
            command.subscribe_state.enabled = enabled
        with self._send_lock:
            if not self.transport.send(self.CONTROL, command.SerializeToString()):
                raise TransportError("failed to send control command")

    def execute(self, command, *, timeout: float | None = None) -> CommandResult:
        if not self.connected:
            raise ControllerError("controller is disconnected")
        pending = _Pending(threading.Event(), command.command)
        with self._pending_lock:
            if command.request_id in self._pending:
                raise ControllerError(f"duplicate request id: {command.request_id}")
            cached = self._completed.get(command.request_id)
            if cached is not None:
                if cached.command != proto.enum_name(command, "command"):
                    raise ControllerError(f"duplicate request id: {command.request_id}")
                if cached.ok:
                    return cached
                raise DeviceCommandError(cached)
            self._pending[command.request_id] = pending
        try:
            with self._send_lock:
                sent = self.transport.send(self.CONTROL, command.SerializeToString())
            if not sent:
                self._mark_disconnected("failed to send control command")
                raise TransportError("failed to send control command")
            if not pending.event.wait(timeout if timeout is not None else self.timeout):
                with self._pending_lock:
                    self._pending.pop(command.request_id, None)
                raise TimeoutError(f"timed out waiting for {command.request_id}")
            if pending.failure is not None:
                raise pending.failure
            if pending.terminal is None:
                raise ControllerError("command ended without a terminal result")
            if not pending.terminal.ok:
                raise DeviceCommandError(pending.terminal)
            return pending.terminal
        except Exception:
            with self._pending_lock:
                self._pending.pop(command.request_id, None)
            raise

    def get_state(self, request_id: str | None = None):
        return self.execute(self._new_command("get_state", request_id))

    def subscribe_state(self, enabled: bool = True, request_id: str | None = None):
        command = self._new_command("subscribe_state", request_id)
        command.subscribe_state.enabled = enabled
        return self.execute(command)

    def prepare_capture(self, collection_id: int, label: int, enabled: bool, request_id: str | None = None):
        command = self._new_command("prepare_capture", request_id)
        command.prepare_capture.collection_id = collection_id
        command.prepare_capture.label = label
        command.prepare_capture.enabled = enabled
        return self.execute(command)

    def select_collection(self, collection_id: int, request_id: str | None = None):
        command = self._new_command("select_collection", request_id)
        command.select_collection.collection_id = collection_id
        return self.execute(command)

    def select_label(self, label: int, request_id: str | None = None):
        command = self._new_command("select_label", request_id)
        command.select_label.label = label
        return self.execute(command)

    def set_capture(self, enabled: bool, request_id: str | None = None):
        command = self._new_command("set_capture", request_id)
        command.set_capture.enabled = enabled
        return self.execute(command)

    def fit(self, epochs: int = 0, request_id: str | None = None):
        command = self._new_command("fit", request_id)
        command.fit.epochs = epochs
        return self.execute(command)

    def flush(self, scope: str, collection_id: int | None = None, label: int | None = None, request_id: str | None = None):
        scope_value = {"label": proto.FLUSH_LABEL, "collection": proto.FLUSH_COLLECTION, "all": proto.FLUSH_ALL}.get(scope)
        if scope_value is None:
            raise ValueError("scope must be label, collection, or all")
        command = self._new_command("flush", request_id)
        command.flush.scope = scope_value
        if collection_id is not None:
            command.flush.has_collection_id = True
            command.flush.collection_id = collection_id
        if label is not None:
            command.flush.has_label = True
            command.flush.label = label
        return self.execute(command)

    def task_preconditions(self, supplied: TaskPreconditions | None = None) -> TaskPreconditions:
        """Return the current post-connect task snapshot, rejecting stale caller state."""
        if not self._task_snapshot_ready.wait(self.timeout):
            raise ControllerError("task command requires a replacement state snapshot")
        task: TaskState = self.state.task
        current = TaskPreconditions(task.app_session_id, task.run_sequence,
                                   task.transition_sequence, task.current_state_id)
        if not current.app_session_id:
            raise ControllerError("task command requires a configured app session")
        if supplied is not None and supplied != current:
            raise StaleTaskProposalError("task proposal preconditions are stale; obtain a replacement snapshot")
        return current

    @staticmethod
    def _set_task_preconditions(target, values: TaskPreconditions) -> None:
        target.expected_app_session_id = values.app_session_id
        target.expected_run_sequence = values.run_sequence
        target.expected_transition_sequence = values.transition_sequence
        if values.state_id is not None:
            target.has_expected_state_id = True
            target.expected_state_id = values.state_id

    def start_task(self, *, preconditions: TaskPreconditions | None = None, request_id: str | None = None):
        command = self._new_command("start_task", request_id)
        self._set_task_preconditions(command.start_task.preconditions, self.task_preconditions(preconditions))
        return self.execute(command)

    def propose_task_event(self, event_name: str, *, preconditions: TaskPreconditions | None = None,
                           request_id: str | None = None):
        if not isinstance(event_name, str) or not event_name:
            raise ValueError("event_name must be a non-empty string")
        command = self._new_command("propose_task_event", request_id)
        self._set_task_preconditions(command.propose_task_event.preconditions, self.task_preconditions(preconditions))
        command.propose_task_event.event_name = event_name
        return self.execute(command)

    def propose_task_transition(self, transition_id: int, *, preconditions: TaskPreconditions | None = None,
                                request_id: str | None = None):
        if not isinstance(transition_id, int) or isinstance(transition_id, bool) or transition_id <= 0:
            raise ValueError("transition_id must be a positive integer")
        command = self._new_command("propose_task_transition", request_id)
        self._set_task_preconditions(command.propose_task_transition.preconditions, self.task_preconditions(preconditions))
        command.propose_task_transition.transition_id = transition_id
        return self.execute(command)

    def query_exo(self, query: str, request_id: str | None = None):
        if query not in {"version", "check_limits", "get_gesture_angles:all"}:
            raise ValueError("query must be version, check_limits, or get_gesture_angles:all")
        command = self._new_command("query_exo", request_id)
        command.query_exo.query = query
        return self.execute(command)

    def set_exo_mode(self, mode: str, request_id: str | None = None):
        """Engage or disengage the on-device exo link.

        ``mode`` is ``"off"`` (default; hand disarmed and untouched),
        ``"external"`` (host commands poses via :meth:`set_exo_pose`), or
        ``"decode"`` (the App's decode output drives the hand). Requires the
        device App to be built with exo support enabled in its config.
        """
        if not isinstance(mode, str):
            raise ValueError("mode must be a string")
        mode_value = proto.EXO_MODE.get(mode.strip().lower())
        if mode_value is None:
            raise ValueError("mode must be off, connected, external, or decode")
        command = self._new_command("set_exo_mode", request_id)
        command.set_exo_mode.mode = mode_value
        return self.execute(command)

    def set_exo_pose(self, joints: dict[str, int], request_id: str | None = None):
        """Command an exo pose while in ``external`` mode.

        ``joints`` maps a joint name (``thumb``/``index``/``middle``/``ring``/
        ``pinky``/``wrist``) to a signed value in ``[-100, 100]`` on the
        rest-anchored axis: -100 extend, 0 rest, +100 flex. A joint omitted from
        the mapping is held unchanged by the firmware.
        """
        if not isinstance(joints, dict) or not joints:
            raise ValueError("joints must name at least one joint")
        command = self._new_command("set_exo_pose", request_id)
        for name, value in joints.items():
            if not isinstance(name, str):
                raise ValueError("joint names must be strings")
            joint_value = proto.EXO_JOINT.get(name.strip().lower())
            if joint_value is None:
                raise ValueError(f"unknown joint: {name!r}")
            if not isinstance(value, int) or isinstance(value, bool) or not -100 <= value <= 100:
                raise ValueError(f"value for {name!r} must be an int in [-100, 100]")
            entry = command.set_exo_pose.joints.add()
            entry.joint = joint_value
            entry.value = value
        return self.execute(command)

    def abort_task(self, *, preconditions: TaskPreconditions | None = None, request_id: str | None = None):
        command = self._new_command("abort_task", request_id)
        self._set_task_preconditions(command.abort_task.preconditions, self.task_preconditions(preconditions))
        return self.execute(command)

    def reset_task(self, *, preconditions: TaskPreconditions | None = None, request_id: str | None = None):
        command = self._new_command("reset_task", request_id)
        self._set_task_preconditions(command.reset_task.preconditions, self.task_preconditions(preconditions))
        return self.execute(command)
