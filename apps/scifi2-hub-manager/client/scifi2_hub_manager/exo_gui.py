"""Operator Exo GUI using the same typed wireless controller as the NDJSON bridge."""
import json
import queue
import time
from concurrent.futures import ThreadPoolExecutor

from .controller import BroadbandController
from .model import state_to_json


def create_exo_window(device_ip, *, controller_factory=None):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                                 QMainWindow, QPlainTextEdit, QPushButton,
                                 QSpinBox, QVBoxLayout, QWidget)

    class ExoWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("SciFi-2 Exo bench control")
            from .appicon import apply_app_icon
            apply_app_icon(self)
            self.resize(740, 540)
            self.events = queue.Queue()
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exo-gui")
            self.controller = (controller_factory or (lambda ip: BroadbandController(ip, timeout=15)))(device_ip)
            self.controller.on_state(lambda state: self.events.put(("state", state)))
            self.pending = False
            self.closing = False
            self.closed = False
            self.connected = False
            self.link_open = False
            root = QWidget(); self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.addWidget(QLabel(f"SciFi-2: {device_ip} — OpenRB connected to headstage USB"))
            self.status = QLabel("Disconnected")
            self.status.setWordWrap(True); layout.addWidget(self.status)
            row = QHBoxLayout(); layout.addLayout(row)
            self.connect_button = QPushButton("Connect / check USB")
            self.connect_button.clicked.connect(lambda: self.submit(self.connect_exo))
            row.addWidget(self.connect_button)
            self.query_buttons = []
            for title, query in [("Version", "version"), ("Limits", "check_limits"), ("Angles", "get_gesture_angles:all")]:
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, q=query: self.submit(lambda: self.controller.query_exo(q)))
                row.addWidget(button); self.query_buttons.append(button)
            self.off_button = QPushButton("Disarm / disconnect USB")
            self.off_button.clicked.connect(lambda: self.submit(lambda: self.controller.set_exo_mode("off")))
            layout.addWidget(self.off_button)
            self.allow_motion = QCheckBox("Enable motion testing (unloaded mechanism; current limits checked)")
            self.allow_motion.toggled.connect(self.update_controls); layout.addWidget(self.allow_motion)
            row = QHBoxLayout(); layout.addLayout(row)
            self.joint = QComboBox(); self.joint.addItems(["thumb", "index", "middle", "ring", "pinky", "wrist"])
            self.joint.setCurrentText("index")
            self.value = QSpinBox(); self.value.setRange(-100, 100); self.value.setValue(10)
            row.addWidget(self.joint); row.addWidget(QLabel("Target (−100 extend, 0 rest, +100 flex)")); row.addWidget(self.value)
            self.move_button = QPushButton("Enable + send 250 ms test")
            self.move_button.clicked.connect(self.test_pose); layout.addWidget(self.move_button)
            note = QLabel("Each test enables all motors, sends one joint target, then disarms. No automatic homing. "
                          "Disarm is a USB command, not an emergency stop; loss of USB can leave torque enabled.")
            note.setWordWrap(True); layout.addWidget(note)
            self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(400)
            layout.addWidget(self.log)
            self.timer = QTimer(self); self.timer.timeout.connect(self.drain_events); self.timer.start(50)
            self.update_controls()

        def update_controls(self):
            idle = not self.pending and not self.closing
            self.connect_button.setEnabled(idle)
            self.off_button.setEnabled(idle and self.connected)
            for button in self.query_buttons: button.setEnabled(idle and self.link_open)
            self.move_button.setEnabled(idle and self.link_open and self.allow_motion.isChecked())
            self.allow_motion.setEnabled(idle)
            self.joint.setEnabled(idle); self.value.setEnabled(idle)

        def connect_exo(self):
            self.controller.connect()
            self.events.put(("connected", None))
            return self.controller.set_exo_mode("connected")

        def submit(self, work):
            if self.pending or self.closing: return
            self.pending = True; self.update_controls()
            def run():
                try:
                    result = work()
                    self.events.put(("done", str(result)))
                except Exception as error:
                    self.events.put(("error", str(error)))
            self.executor.submit(run)

        def test_pose(self):
            if not self.allow_motion.isChecked() or not self.link_open: return
            joints = {self.joint.currentText(): self.value.value()}
            self.log.appendPlainText(f"Requested 250 ms test: {joints}")
            def move():
                try:
                    self.controller.set_exo_mode("external")
                    result = self.controller.set_exo_pose(joints)
                    time.sleep(0.25)
                    return result
                finally:
                    self.controller.set_exo_mode("connected")  # disarm, retain read-only link
            self.submit(move)

        def drain_events(self):
            while not self.events.empty():
                kind, value = self.events.get_nowait()
                if kind == "state":
                    exo = value.exo
                    self.link_open = value.connected and exo.link_open
                    self.status.setText(f"USB {'open' if self.link_open else 'closed'} | mode {exo.mode} | "
                                        f"armed intent {exo.armed} | firmware {exo.firmware or 'unknown'} | "
                                        f"watchdog {exo.watchdog_tripped}\n{exo.last_error}")
                    # Periodic state is frequent; append only changed Exo status.
                    text = json.dumps(state_to_json(value)["exo"])
                    if text != getattr(self, "last_status", None):
                        self.last_status = text; self.log.appendPlainText(text)
                elif kind == "connected": self.connected = True
                elif kind == "cleanup_error":
                    self.closed = True
                    self.log.appendPlainText(f"DISARM NOT CONFIRMED: {value}. Check physical stop. Close again to exit.")
                elif kind == "closed":
                    self.closed = True; self.close(); return
                else:
                    self.pending = False
                    self.log.appendPlainText(f"{kind}: {value}")
                    if kind == "error": self.allow_motion.setChecked(False)
                self.update_controls()

        def closeEvent(self, event):
            if self.closed:
                self.timer.stop(); self.executor.shutdown(wait=False); event.accept(); return
            event.ignore()
            if self.closing: return
            self.closing = True; self.update_controls()
            self.log.appendPlainText("Closing: waiting for current operation, then attempting USB disarm/disconnect.")
            def cleanup():
                try:
                    if self.controller.connected:
                        self.controller.set_exo_mode("off")
                except Exception as error:
                    # Keep the window visible so failed physical cleanup is not hidden.
                    self.events.put(("cleanup_error", str(error)))
                    return
                finally:
                    self.controller.disconnect()
                self.events.put(("closed", None))
            self.executor.submit(cleanup)

    return ExoWindow()


def run_gui(device_ip):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    window = create_exo_window(device_ip)
    window.show()
    app.exec()
