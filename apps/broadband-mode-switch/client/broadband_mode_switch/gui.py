"""Small PySide6 dashboard backed by the controller, never by direct Taps."""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor

from .controller import BroadbandController
from .model import AppState, CommandResult


def run_gui(device_ip: str) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
        QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QProgressBar,
        QSpinBox, QVBoxLayout, QWidget,
    )

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Broadband mode switch")
            self.resize(760, 560)
            self.controller: BroadbandController | None = None
            self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gui-command")
            self.events: queue.Queue[tuple[str, object]] = queue.Queue()
            self._building = False
            self._build_ui(device_ip)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._drain_events)
            self.timer.start(50)

        def _build_ui(self, default_ip):
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
            self.model = QLabel("Model: not ready")
            form.addRow("Pipeline", self.pipeline)
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
                button = QPushButton(text); button.clicked.connect(lambda _, s=scope: self._flush(s)); destructive.addWidget(button)
            layout.addLayout(destructive)
            self.message = QLabel(""); layout.addWidget(self.message)

        def _connect(self):
            if self.controller is not None:
                self.controller.disconnect(); self.controller = None
                self.connect_button.setText("Connect"); self.pipeline.setText("Disconnected"); return
            controller = BroadbandController(self.ip.text())
            controller.on_state(lambda value: self.events.put(("state", value)))
            controller.on_result(lambda value: self.events.put(("result", value)))
            self.controller = controller
            self.connect_button.setEnabled(False)
            self.executor.submit(self._do_connect, controller)

        def _do_connect(self, controller):
            try: controller.connect_with_backoff(); self.events.put(("connected", True))
            except Exception as exc: self.events.put(("error", str(exc)))

        def _submit(self, work):
            if self.controller is None: return
            self.apply.setEnabled(False); self.fit_button.setEnabled(False)
            self.executor.submit(self._run_command, work)

        def _run_command(self, work):
            try: self.events.put(("result", work()))
            except Exception as exc: self.events.put(("error", str(exc)))

        def _apply_target(self):
            self._submit(lambda: self.controller.prepare_capture(self.collection.currentData(), self.label.currentData(), self.capture.isChecked()))

        def _fit(self):
            self._submit(lambda: self.controller.fit(self.epochs.value()))

        def _flush(self, scope):
            if self.controller is None: return
            if QMessageBox.question(self, "Confirm", f"Flush {scope}? This cannot be undone.") != QMessageBox.StandardButton.Yes: return
            self._submit(lambda: self.controller.flush(scope, self.collection.currentData() if scope != "all" else None, self.label.currentData() if scope == "label" else None))

        def _drain_events(self):
            while True:
                try: kind, value = self.events.get_nowait()
                except queue.Empty: break
                if kind == "connected": self.connect_button.setEnabled(True); self.connect_button.setText("Disconnect")
                elif kind == "state": self._show_state(value)
                elif kind == "result": self.apply.setEnabled(True); self.fit_button.setEnabled(True); self.message.setText("Command completed")
                elif kind == "error": self.connect_button.setEnabled(True); self.apply.setEnabled(True); self.fit_button.setEnabled(True); self.message.setText(str(value))

        def _show_state(self, state: AppState):
            self.pipeline.setText(f"{state.pipeline_state} / {state.source_mode}")
            self.capture.setChecked(state.active.capture_enabled)
            self.model.setText(f"Model: {state.model.phase}, accuracy {state.model.accuracy:.3f}" + (" (stale)" if state.model.stale else ""))
            self._building = True
            self.collection.clear()
            for item in state.collections: self.collection.addItem(str(item.collection_id), item.collection_id)
            self.collection.setCurrentIndex(max(0, self.collection.findData(state.active.collection_id)))
            self.label.setCurrentIndex(max(0, self.label.findData(state.active.label)))
            self._building = False
            while self.progress_layout.count():
                item = self.progress_layout.takeAt(0); widget = item.widget()
                if widget: widget.deleteLater()
            for collection in state.collections:
                for label in collection.labels:
                    bar = QProgressBar(); bar.setRange(0, max(1, label.capacity)); bar.setValue(min(label.count, label.capacity)); bar.setFormat(f"C{collection.collection_id} / label {label.label}: {label.count} / {label.capacity}")
                    self.progress_layout.addWidget(bar)

        def closeEvent(self, event):
            if self.controller: self.controller.disconnect()
            self.executor.shutdown(wait=False, cancel_futures=True)
            event.accept()

    app = QApplication.instance() or QApplication([])
    window = Window(); window.show(); app.exec()
