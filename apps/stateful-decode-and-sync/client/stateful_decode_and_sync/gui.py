"""PySide6 dashboard backed by controller state, never by direct Taps."""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .controller import BroadbandController
from .model import AppState


def create_dashboard_window(
    device_ip: str,
    *,
    controller_factory: Callable[[str], BroadbandController] = BroadbandController,
):
    """Create a dashboard window; all Qt imports remain optional until GUI use."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
        QLabel, QMainWindow, QMessageBox, QPushButton, QProgressBar, QSpinBox,
        QVBoxLayout, QWidget,
    )

    class DashboardWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Stateful Decode and Sync")
            self.resize(760, 560)
            self.controller: BroadbandController | None = None
            self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gui-command")
            self.events: queue.Queue[tuple[str, object]] = queue.Queue()
            self._command_pending = False
            self.flush_buttons = {}
            self._build_ui(device_ip)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._drain_events)
            self.timer.start(50)

        def _build_ui(self, default_ip: str):
            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)

            connection = QGroupBox("Device")
            form = QFormLayout(connection)
            self.ip = QLabel(default_ip)
            self.connect_button = QPushButton("Connect")
            self.connect_button.clicked.connect(self._connect)
            row = QHBoxLayout(); row.addWidget(self.ip); row.addWidget(self.connect_button)
            form.addRow("Device IP", row)
            self.pipeline = QLabel("Disconnected")
            self.source = QLabel("Source: unknown")
            self.model = QLabel("Model: not ready")
            form.addRow("Pipeline", self.pipeline)
            form.addRow("Source", self.source)
            form.addRow("Model", self.model)
            layout.addWidget(connection)

            controls = QGroupBox("Capture control")
            grid = QGridLayout(controls)
            self.collection = QComboBox(); self.collection.addItem("0", 0)
            self.label = QComboBox(); [self.label.addItem(str(i), i) for i in range(5)]
            self.capture = QCheckBox("Capture enabled")
            self.apply = QPushButton("Apply target")
            self.apply.clicked.connect(self._apply_target)
            grid.addWidget(QLabel("Collection"), 0, 0); grid.addWidget(self.collection, 0, 1)
            grid.addWidget(QLabel("Label"), 0, 2); grid.addWidget(self.label, 0, 3)
            grid.addWidget(self.capture, 1, 0, 1, 2); grid.addWidget(self.apply, 1, 2, 1, 2)
            layout.addWidget(controls)

            fitbox = QGroupBox("Fit")
            fitrow = QHBoxLayout(fitbox)
            self.epochs = QSpinBox(); self.epochs.setRange(0, 10000); self.epochs.setValue(100)
            self.fit_button = QPushButton("Fit active collection"); self.fit_button.clicked.connect(self._fit)
            fitrow.addWidget(QLabel("Epochs (0 = configured default)")); fitrow.addWidget(self.epochs)
            fitrow.addWidget(self.fit_button)
            layout.addWidget(fitbox)

            self.progress_group = QGroupBox("Collections")
            self.progress_layout = QVBoxLayout(self.progress_group)
            layout.addWidget(self.progress_group)
            destructive = QHBoxLayout()
            for text, scope in (("Flush label", "label"), ("Flush collection", "collection"), ("Flush all", "all")):
                button = QPushButton(text); button.clicked.connect(lambda _, s=scope: self._flush(s)); self.flush_buttons[scope] = button; destructive.addWidget(button)
            layout.addLayout(destructive)
            self.message = QLabel(""); layout.addWidget(self.message)

        def _connect(self):
            if self.controller is not None:
                self.controller.disconnect(); self.controller = None
                self.connect_button.setText("Connect"); self.pipeline.setText("Disconnected"); return
            controller = controller_factory(self.ip.text())
            controller.on_state(lambda value: self.events.put(("state", value)))
            controller.on_result(lambda value: self.events.put(("result", value)))
            self.controller = controller
            self.connect_button.setEnabled(False)
            self.executor.submit(self._do_connect, controller)

        def _do_connect(self, controller):
            try:
                controller.connect_with_backoff()
                self.events.put(("connected", True))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        def _set_command_controls_enabled(self, enabled: bool):
            for widget in (self.collection, self.label, self.capture, self.apply, self.fit_button, *self.flush_buttons.values()):
                widget.setEnabled(enabled)

        def _submit(self, work):
            if self.controller is None or self._command_pending:
                return
            self._command_pending = True
            self._set_command_controls_enabled(False)
            self.executor.submit(self._run_command, work)

        def _run_command(self, work):
            try:
                self.events.put(("result", work()))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        def _apply_target(self):
            self._submit(lambda: self.controller.prepare_capture(
                self.collection.currentData(), self.label.currentData(), self.capture.isChecked()))

        def _fit(self):
            self._submit(lambda: self.controller.fit(self.epochs.value()))

        def _flush(self, scope):
            if self.controller is None:
                return
            if QMessageBox.question(self, "Confirm", f"Flush {scope}? This cannot be undone.") != QMessageBox.StandardButton.Yes:
                return
            self._submit(lambda: self.controller.flush(
                scope,
                self.collection.currentData() if scope != "all" else None,
                self.label.currentData() if scope == "label" else None))

        def _drain_events(self):
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "connected":
                    self.connect_button.setEnabled(True); self.connect_button.setText("Disconnect")
                elif kind == "state":
                    self.show_state(value)
                elif kind == "result":
                    if value.terminal:
                        self._command_pending = False
                        self._set_command_controls_enabled(True)
                        self.message.setText("Command completed")
                    else:
                        self.message.setText("Fit in progress")
                elif kind == "error":
                    self._command_pending = False
                    self.connect_button.setEnabled(True)
                    self._set_command_controls_enabled(True)
                    self.message.setText(str(value))

        def show_state(self, state: AppState):
            """Apply one immutable replacement snapshot on the Qt thread."""
            self.pipeline.setText(state.pipeline_state)
            self.source.setText(f"Source: {state.source_mode}")
            model = state.model
            source = "unknown"
            if model.source_collection_id is not None:
                source = f"collection {model.source_collection_id}, generation {model.source_generation}"
            progress = f"{model.phase}, {source}, epoch {model.epoch}/{model.total_epochs}, loss {model.loss:.4g}, accuracy {model.accuracy:.3f}, {model.duration_ms} ms"
            self.model.setText("Model: " + progress + (" (stale)" if model.stale else ""))
            self.capture.setChecked(state.active.capture_enabled)
            self.collection.clear()
            for item in state.collections:
                self.collection.addItem(str(item.collection_id), item.collection_id)
            self.collection.setCurrentIndex(max(0, self.collection.findData(state.active.collection_id)))
            self.label.setCurrentIndex(max(0, self.label.findData(state.active.label)))
            while self.progress_layout.count():
                item = self.progress_layout.takeAt(0); widget = item.widget()
                if widget:
                    widget.deleteLater()
            for collection in state.collections:
                for label in collection.labels:
                    bar = QProgressBar()
                    if label.capacity:
                        bar.setRange(0, label.capacity)
                        bar.setValue(min(label.count, label.capacity))
                        bar.setFormat(f"C{collection.collection_id} / label {label.label}: {label.count} / {label.capacity}")
                    else:
                        bar.setRange(0, 1)
                        bar.setValue(0)
                        bar.setEnabled(False)
                        bar.setFormat(f"C{collection.collection_id} / label {label.label}: {label.count} / unknown capacity")
                    self.progress_layout.addWidget(bar)
            self.message.setText(state.last_error.message if state.last_error else "")

        def closeEvent(self, event):
            if self.controller:
                self.controller.disconnect()
            self.executor.shutdown(wait=False, cancel_futures=True)
            event.accept()

    return DashboardWindow()


def run_gui(device_ip: str) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = create_dashboard_window(device_ip)
    window.show()
    app.exec()
