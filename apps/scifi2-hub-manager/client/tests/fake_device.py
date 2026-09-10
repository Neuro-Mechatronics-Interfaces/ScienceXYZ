"""In-process device contract fixture used by hardware-free integration tests."""

from __future__ import annotations

import threading
import time

from scifi2_hub_manager import proto
from scifi2_hub_manager.transport import FakeTapTransport


class FakeDevice:
    """Small deterministic responder for the generated C++ control protocol."""

    def __init__(self, transport: FakeTapTransport, *, labels: int = 5, fit_epochs: int = 3):
        self.transport = transport
        self.labels = labels
        self.fit_epochs = fit_epochs
        self.commands = []
        self._cursor = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fake-device", daemon=True)
        self._fit_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.collection_id = 0
        self.label = 0
        self.capture = False
        self.generation = 1
        self.counts = [2] * labels
        self.state_version = 0
        self.model_phase = 1
        self.model_ready = False
        self.model_generation = 0
        self.task_definition_id = "fake-cyclic-task"
        self.task_definition_revision = 1
        self.task_definition_hash = "f" * 64
        self.task_app_session_id = "0123456789abcdef0123456789abcdef"
        self.task_lifecycle = proto.TASK_LIFECYCLE_IDLE
        self.task_run_sequence = 0
        self.task_event_sequence = 0
        self.task_transition_sequence = 0
        self.task_current_state_id = None
        self.task_source_sequence = 0
        self._staged_task_commands = []

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(1)
        if self._fit_thread is not None:
            self._fit_thread.join(1)

    def _run(self):
        while not self._stop.is_set():
            while self._cursor < len(self.transport.sent):
                _, raw = self.transport.sent[self._cursor]
                self._cursor += 1
                command = proto.ControlCommand()
                command.ParseFromString(raw)
                self._handle(command)
            time.sleep(0.002)

    def _handle(self, command):
        with self._lock:
            self.commands.append(command)
            kind = command.command
            if kind == proto.COMMAND["subscribe_state"] or kind == proto.COMMAND["get_state"]:
                self._result(command, proto.RESULT_SUCCEEDED)
                self._state()
                return
            if kind == proto.COMMAND["prepare_capture"]:
                self.collection_id = command.prepare_capture.collection_id
                self.label = command.prepare_capture.label
                self.capture = command.prepare_capture.enabled
                self._result(command, proto.RESULT_SUCCEEDED)
                self._state()
                return
            if kind == proto.COMMAND["select_collection"]:
                if self.capture:
                    self._result(command, proto.RESULT_FAILED, 7, "capture is enabled")
                else:
                    self.collection_id = command.select_collection.collection_id
                    self._result(command, proto.RESULT_SUCCEEDED)
                    self._state()
                return
            if kind == proto.COMMAND["select_label"]:
                if self.capture:
                    self._result(command, proto.RESULT_FAILED, 7, "capture is enabled")
                else:
                    self.label = command.select_label.label
                    self._result(command, proto.RESULT_SUCCEEDED)
                    self._state()
                return
            if kind == proto.COMMAND["set_capture"]:
                self.capture = command.set_capture.enabled
                self._result(command, proto.RESULT_SUCCEEDED)
                self._state()
                return
            if kind == proto.COMMAND["flush"]:
                self._flush(command)
                return
            if kind == proto.COMMAND["fit"]:
                if self._fit_thread is not None and self._fit_thread.is_alive():
                    self._result(command, proto.RESULT_FAILED, 8, "fit is busy")
                else:
                    self._fit_thread = threading.Thread(target=self._fit, args=(command,), daemon=True)
                    self._fit_thread.start()
                return
            if kind in {proto.COMMAND["start_task"], proto.COMMAND["propose_task_event"],
                        proto.COMMAND["propose_task_transition"], proto.COMMAND["abort_task"],
                        proto.COMMAND["reset_task"]}:
                self._stage_task_command(command)
                return
            self._result(command, proto.RESULT_FAILED, 3, "unknown command")

    def _stage_task_command(self, command):
        preconditions = getattr(command, command.WhichOneof("payload")).preconditions
        if (preconditions.expected_app_session_id != self.task_app_session_id
                or preconditions.expected_run_sequence != self.task_run_sequence
                or preconditions.expected_transition_sequence != self.task_transition_sequence
                or (preconditions.has_expected_state_id
                    and preconditions.expected_state_id != (self.task_current_state_id or 0))):
            self._result(command, proto.RESULT_FAILED, proto.ERROR_INVALID_ARGUMENT, "stale task preconditions")
            return
        kind = command.command
        valid = ((kind == proto.COMMAND["start_task"] and self.task_lifecycle == proto.TASK_LIFECYCLE_IDLE)
                 or (kind in {proto.COMMAND["propose_task_event"], proto.COMMAND["propose_task_transition"]}
                     and self.task_lifecycle == proto.TASK_LIFECYCLE_RUNNING)
                 or (kind == proto.COMMAND["abort_task"] and self.task_lifecycle == proto.TASK_LIFECYCLE_RUNNING)
                 or (kind == proto.COMMAND["reset_task"]
                     and self.task_lifecycle in {proto.TASK_LIFECYCLE_COMPLETED,
                                                 proto.TASK_LIFECYCLE_ABORTED,
                                                 proto.TASK_LIFECYCLE_FAULT}))
        if not valid:
            self._result(command, proto.RESULT_FAILED, proto.ERROR_INVALID_ARGUMENT, "task command is invalid for lifecycle")
            return
        self._staged_task_commands.append(command)
        self._result(command, proto.RESULT_ACCEPTED)
        self._state()

    def advance_task_frame(self):
        """Commit one staged task request at one synthetic reference frame."""
        with self._lock:
            if not self._staged_task_commands:
                return False
            command = self._staged_task_commands.pop(0)
            self.task_source_sequence += 1
            kind = command.command
            previous = self.task_current_state_id or 0
            transition_id = 0
            if kind == proto.COMMAND["start_task"]:
                self.task_run_sequence += 1
                self.task_transition_sequence = 1
                self.task_current_state_id = 1
                self.task_lifecycle = proto.TASK_LIFECYCLE_RUNNING
                event_kind, trigger_kind, source = (proto.TASK_EVENT_START, proto.TASK_TRIGGER_START_COMMAND, "StartTask")
            elif kind == proto.COMMAND["abort_task"]:
                self.task_transition_sequence += 1
                self.task_current_state_id = None
                self.task_lifecycle = proto.TASK_LIFECYCLE_ABORTED
                event_kind, trigger_kind, source = (proto.TASK_EVENT_ABORT, proto.TASK_TRIGGER_ABORT_COMMAND, "AbortTask")
            elif kind == proto.COMMAND["reset_task"]:
                self.task_transition_sequence += 1
                self.task_current_state_id = None
                self.task_lifecycle = proto.TASK_LIFECYCLE_IDLE
                event_kind, trigger_kind, source = (proto.TASK_EVENT_RESET, proto.TASK_TRIGGER_RESET_COMMAND, "ResetTask")
            else:
                self.task_transition_sequence += 1
                transition_id = (command.propose_task_transition.transition_id
                                 if kind == proto.COMMAND["propose_task_transition"] else 1)
                self.task_current_state_id = 2 if previous == 1 else 1
                event_kind, trigger_kind, source = (proto.TASK_EVENT_TRANSITION, proto.TASK_TRIGGER_EXTERNAL_EVENT,
                                                    command.propose_task_event.event_name if kind == proto.COMMAND["propose_task_event"] else str(transition_id))
            self._emit_task_event(command, previous, event_kind, trigger_kind, source, transition_id)
            return True

    def advance_task_timer(self):
        """Simulate the configured source-time transition from active to waiting."""
        with self._lock:
            if self.task_lifecycle != proto.TASK_LIFECYCLE_RUNNING or self.task_current_state_id != 2:
                return False
            previous = self.task_current_state_id
            self.task_source_sequence += 1
            self.task_transition_sequence += 1
            self.task_current_state_id = 1
            self._emit_task_event(None, previous, proto.TASK_EVENT_TRANSITION,
                                  proto.TASK_TRIGGER_SOURCE_TIMEOUT, "100", 11)
            return True

    def advance_task_decoder(self):
        """Simulate a satisfied decoder dwell entering the terminal state."""
        with self._lock:
            if self.task_lifecycle != proto.TASK_LIFECYCLE_RUNNING or self.task_current_state_id != 2:
                return False
            previous = self.task_current_state_id
            self.task_source_sequence += 1
            self.task_transition_sequence += 1
            self.task_current_state_id = 3
            self.task_lifecycle = proto.TASK_LIFECYCLE_COMPLETED
            self._emit_task_event(None, previous, proto.TASK_EVENT_TRANSITION,
                                  proto.TASK_TRIGGER_DECODER_PREDICATE, "1", 12)
            return True

    def _emit_task_event(self, command, previous, event_kind, trigger_kind, source, transition_id):
        self.task_event_sequence += 1
        event = proto.TaskTransitionEvent()
        event.protocol_version = 1
        event.definition_id = self.task_definition_id
        event.definition_revision = self.task_definition_revision
        event.definition_hash = self.task_definition_hash
        event.app_session_id = self.task_app_session_id
        event.run_sequence = self.task_run_sequence
        event.event_sequence = self.task_event_sequence
        event.transition_sequence = self.task_transition_sequence
        event.event_kind = event_kind
        event.transition_id = transition_id
        event.previous_state_id = previous
        event.current_state_id = self.task_current_state_id or 0
        event.trigger_kind = trigger_kind
        event.trigger_source = source
        event.request_id = "" if command is None else command.request_id
        event.proposal_receipt_sequence = self.task_event_sequence
        event.proposal_receipt_time_ns = self.task_source_sequence * 100
        event.effective_frame.source_id = "fake/reference"
        event.effective_frame.sequence_number = self.task_source_sequence
        event.effective_frame.timestamp_ns = self.task_source_sequence * 1000
        self.transport.inject("task_transition", event.SerializeToString())
        self._state()
        if command is not None:
            self._result(command, proto.RESULT_SUCCEEDED)

    def _flush(self, command):
        scope = command.flush.scope
        if scope == proto.FLUSH_ALL:
            self.counts = [0] * self.labels
            self.generation += 1
        elif scope == proto.FLUSH_COLLECTION:
            self.counts = [0] * self.labels
            self.generation += 1
        elif scope == proto.FLUSH_LABEL:
            label = command.flush.label if command.flush.has_label else self.label
            self.counts[label] = 0
            self.generation += 1
        self._result(command, proto.RESULT_SUCCEEDED)
        self._state()

    def _fit(self, command):
        epochs = command.fit.epochs or self.fit_epochs
        with self._lock:
            source_generation = self.generation
            self.model_phase = 3
            self._result(command, proto.RESULT_ACCEPTED)
            self._state()
        for epoch in range(1, epochs + 1):
            if self._stop.is_set():
                return
            time.sleep(0.01)
            with self._lock:
                self.generation += 1  # represents new samples arriving during fit
                self.model_generation = source_generation
                self._result(command, proto.RESULT_ACCEPTED, progress=(epoch, epochs, 0.5 / epoch, epoch / epochs))
                self._state(epoch, epochs, 0.5 / epoch, epoch / epochs, source_generation)
        with self._lock:
            self.model_phase = 4
            self.model_ready = True
            self._result(command, proto.RESULT_SUCCEEDED, progress=(epochs, epochs, 0.5 / epochs, 1.0))
            self._state(epochs, epochs, 0.5 / epochs, 1.0, source_generation)

    def _result(self, command, status, error_code=None, error_message="", progress=None):
        result = proto.CommandResult()
        result.protocol_version = 1
        result.request_id = command.request_id
        result.command = command.command
        result.status = status
        result.state_version = self.state_version
        if error_code is not None:
            result.error.code = error_code
            result.error.message = error_message
        if progress is not None:
            epoch, total, loss, accuracy = progress
            result.progress.epoch = epoch
            result.progress.total_epochs = total
            result.progress.loss = loss
            result.progress.accuracy = accuracy
        self.transport.inject("command_result", result.SerializeToString())

    def _state(self, epoch=0, total=0, loss=0.0, accuracy=0.0, source_generation=None):
        self.state_version += 1
        state = proto.StateSnapshot()
        state.protocol_version = 1
        state.state_version = self.state_version
        state.timestamp_ns = self.state_version * 1000
        state.pipeline.state = 3
        state.pipeline.source_mode = 1
        state.active.collection_id = self.collection_id
        state.active.label = self.label
        state.active.capture_enabled = self.capture
        collection = state.collections.add()
        collection.collection_id = 0
        collection.feature_dimension = 256
        collection.data_generation = self.generation
        for label, count in enumerate(self.counts):
            item = collection.labels.add()
            item.label = label
            item.count = count
            item.capacity = 10
        state.model.phase = self.model_phase
        state.model.ready = self.model_ready
        state.model.has_source_collection_id = self.model_ready or total > 0
        state.model.source_collection_id = 0
        state.model.source_generation = self.model_generation if source_generation is None else source_generation
        state.model.stale = source_generation is not None and self.generation != source_generation
        state.model.epoch = epoch
        state.model.total_epochs = total
        state.model.loss = loss
        state.model.accuracy = accuracy
        task = state.task
        task.configured = True
        task.definition_id = self.task_definition_id
        task.definition_revision = self.task_definition_revision
        task.definition_hash = self.task_definition_hash
        task.app_session_id = self.task_app_session_id
        task.lifecycle = self.task_lifecycle
        task.run_sequence = self.task_run_sequence
        task.event_sequence = self.task_event_sequence
        task.transition_sequence = self.task_transition_sequence
        if self.task_current_state_id is not None:
            task.has_current_state_id = True
            task.current_state_id = self.task_current_state_id
        task.staged_command_count = len(self._staged_task_commands)
        task.source_healthy = True
        if self.task_source_sequence:
            task.has_effective_frame = True
            task.last_effective_frame.source_id = "fake/reference"
            task.last_effective_frame.sequence_number = self.task_source_sequence
            task.last_effective_frame.timestamp_ns = self.task_source_sequence * 1000
        self.transport.inject("state", state.SerializeToString())
