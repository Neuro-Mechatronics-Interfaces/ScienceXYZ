"""In-process device contract fixture used by hardware-free integration tests."""

from __future__ import annotations

import threading
import time

from broadband_mode_switch import proto
from broadband_mode_switch.transport import FakeTapTransport


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
            self._result(command, proto.RESULT_FAILED, 3, "unknown command")

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
        self.transport.inject("state", state.SerializeToString())
