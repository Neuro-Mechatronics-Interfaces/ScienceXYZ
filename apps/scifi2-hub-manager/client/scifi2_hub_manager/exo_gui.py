"""Operator Exo GUI using the same typed wireless controller as the NDJSON bridge.

One self-contained window drives the whole wireless-Exo bench flow, matching the
calibration GUIs' conventions and boundaries:

* it talks to the on-device App directly over the Synapse ``control`` Tap through
  :class:`BroadbandController`, so **no separate ``run_service.py`` terminal is
  needed** -- Connect/Version/Limits/Angles and the explicit motion test all go
  through this one controller;
* like ``calibrate-gui`` it can optionally run ``synapsectl`` for the operator
  (start the App with the Exo config, fetch ``info`` and gate on
  ``scifi2-hub-manager`` Running: True) -- permitted for an operator-launched GUI
  under the AGENTS.md Synapse CLI execution boundary; the agent never runs it,
  and Copy + run-it-yourself remains the fallback;
* field values (device URI/tap, synapsectl command) persist between launches in
  a per-user INI via ``QSettings``, matching the session console.

Qt objects are touched only on the UI thread; controller callbacks and the
synapsectl child arrive on other threads and are marshalled back through a
drained ``queue.Queue``.
"""
from __future__ import annotations

import json
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .controller import BroadbandController
from .model import state_to_json

# Reused verbatim from the calibration launcher so the synapsectl start line and
# the App-Running gate parse identically to the other GUIs. Imported at module
# load so a missing client install fails loudly rather than at button-press.
import run_calibration_session as launcher

# The Exo configuration the operator starts (baseline rhd2132.json does not
# enable the Exo worker). It lives beside the App, not under the repo config/.
_EXO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "rhd2132_with_exo.json"


def create_exo_window(device_ip, *, controller_factory=None):
    from PySide6.QtCore import QProcess, QSettings, QTimer
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox,
                                 QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                                 QMainWindow, QPlainTextEdit, QPushButton,
                                 QSpinBox, QVBoxLayout, QWidget)

    class ExoWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("SciFi-2 Exo bench control")
            from .appicon import apply_app_icon
            apply_app_icon(self)
            self.resize(760, 680)
            self.events = queue.Queue()
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="exo-gui")
            self.controller_factory = controller_factory or (lambda ip: BroadbandController(ip, timeout=15))
            self.controller = self.controller_factory(device_ip)
            self.controller.on_state(lambda state: self.events.put(("state", state)))
            self.pending = False
            self.closing = False
            self.closed = False
            self.connected = False
            self.link_open = False
            # synapsectl (optional operator convenience) + App-Running gate state.
            self.synapsectl_process: QProcess | None = None
            self.device_started = False
            self.app_running = False
            # Persisted field defaults (device URI/tap, synapsectl command) in a
            # per-user INI, matching CalibrationSessionConsole; absent keys keep
            # the built-in defaults so a first run is unchanged.
            self.settings = QSettings(QSettings.IniFormat, QSettings.UserScope,
                                      "NML", "ExoBenchConsole")
            self._build_ui(device_ip)
            self._load_settings()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.drain_events)
            self.timer.start(50)
            self.update_controls()

        # -- UI construction --------------------------------------------------
        def _build_ui(self, device_ip):
            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.addWidget(QLabel("OpenRB connected to the SciFi-2 headstage USB; commands go over "
                                    "the Synapse control Tap (no separate service needed)."))

            # (1) device + optional synapsectl start/gate
            box1 = QGroupBox("1. Device and App start")
            form = QFormLayout(box1)
            self.device_uri = QLineEdit(device_ip)
            self.device_uri.setToolTip("SciFi-2 address for both the Synapse controller and synapsectl.")
            self.synapsectl = QLineEdit("synapsectl")
            self.synapsectl.setToolTip(
                "Command for the optional Run synapsectl buttons. The default resolves from the "
                "active venv/PATH; override with an absolute path or a prefixed form such as "
                "'wsl synapsectl' only if your install lives elsewhere.")
            form.addRow("Device URI", self.device_uri)
            form.addRow("synapsectl command", self.synapsectl)
            self.start_line = QLineEdit()
            self.start_line.setReadOnly(True)
            form.addRow("Start line", self.start_line)
            run_row = QHBoxLayout()
            self.copy_button = QPushButton("Copy start line")
            self.copy_button.clicked.connect(self._copy_start_line)
            self.run_start_button = QPushButton("Run: start device")
            self.run_start_button.clicked.connect(self._run_start_device)
            self.run_info_button = QPushButton("Run: fetch info + gate")
            self.run_info_button.clicked.connect(self._run_fetch_info)
            self.load_info_button = QPushButton("Load info capture...")
            self.load_info_button.clicked.connect(self._load_info)
            for w in (self.copy_button, self.run_start_button, self.run_info_button, self.load_info_button):
                run_row.addWidget(w)
            form.addRow(run_row)
            self.gate_label = QLabel("App Running gate: not checked")
            self.gate_label.setWordWrap(True)
            form.addRow(self.gate_label)
            layout.addWidget(box1)

            # (2) USB link + read-only queries
            box2 = QGroupBox("2. Exo USB link and read-only queries")
            v2 = QVBoxLayout(box2)
            self.status = QLabel("Disconnected")
            self.status.setWordWrap(True); v2.addWidget(self.status)
            row = QHBoxLayout(); v2.addLayout(row)
            self.connect_button = QPushButton("Connect / check USB")
            self.connect_button.clicked.connect(lambda: self.submit(self.connect_exo, op="connect"))
            row.addWidget(self.connect_button)
            self.query_buttons = []
            for title, query in [("Version", "version"), ("Limits", "check_limits"), ("Angles", "get_gesture_angles:all")]:
                button = QPushButton(title)
                button.clicked.connect(lambda checked=False, q=query: self.submit(lambda: self.controller.query_exo(q), op="query"))
                row.addWidget(button); self.query_buttons.append(button)
            self.off_button = QPushButton("Disarm / disconnect USB")
            self.off_button.clicked.connect(lambda: self.submit(lambda: self.controller.set_exo_mode("off"), op="off"))
            v2.addWidget(self.off_button)
            layout.addWidget(box2)

            # (3) explicit motion test
            box3 = QGroupBox("3. Explicit motion test (unloaded mechanism)")
            v3 = QVBoxLayout(box3)
            self.allow_motion = QCheckBox("Enable motion testing (unloaded mechanism; current limits checked)")
            self.allow_motion.toggled.connect(self.update_controls); v3.addWidget(self.allow_motion)
            mrow = QHBoxLayout(); v3.addLayout(mrow)
            self.joint = QComboBox(); self.joint.addItems(["thumb", "index", "middle", "ring", "pinky", "wrist"])
            self.joint.setCurrentText("index")
            self.value = QSpinBox(); self.value.setRange(-100, 100); self.value.setValue(10)
            mrow.addWidget(self.joint); mrow.addWidget(QLabel("Target (-100 extend, 0 rest, +100 flex)")); mrow.addWidget(self.value)
            self.move_button = QPushButton("Enable + send 250 ms test")
            self.move_button.clicked.connect(self.test_pose); v3.addWidget(self.move_button)
            note = QLabel("Each test enables all motors, sends one joint target, then disarms. No automatic homing. "
                          "Disarm is a USB command, not an emergency stop; loss of USB can leave torque enabled. "
                          "The device configuration must also enable motion (exo_motion_enabled).")
            note.setWordWrap(True); v3.addWidget(note)
            layout.addWidget(box3)

            # (4) raw firmware terminal (bench serial-monitor passthrough)
            box4 = QGroupBox("4. Firmware terminal (raw passthrough)")
            v4 = QVBoxLayout(box4)
            trow = QHBoxLayout(); v4.addLayout(trow)
            self.raw_input = QLineEdit()
            self.raw_input.setPlaceholderText("firmware command, e.g. version, help, get_enable:all, home:all")
            self.raw_input.returnPressed.connect(self.send_raw)
            self.raw_button = QPushButton("Send")
            self.raw_button.clicked.connect(self.send_raw)
            trow.addWidget(self.raw_input, 1); trow.addWidget(self.raw_button)
            rawnote = QLabel("Sends the line verbatim to the exo firmware (like the Arduino monitor) and shows the "
                             "reply below. This bypasses the read-only allowlist and CAN command motion, so the "
                             "device config must set exo_raw_enabled true; otherwise the App rejects it. Requires "
                             "a connected link.")
            rawnote.setWordWrap(True); v4.addWidget(rawnote)
            layout.addWidget(box4)

            self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(400)
            layout.addWidget(self.log, 1)
            self._refresh_start_line()

        # -- helpers ----------------------------------------------------------
        def _synapsectl_argv(self, *tail):
            """Build the synapsectl argv from the configurable command field.

            The field may carry more than one token (e.g. ``wsl synapsectl``) so a
            Windows GUI can reach a WSL install; each token becomes an argv
            element. Operator-machine only, per the AGENTS.md boundary scope."""
            base = [token for token in self.synapsectl.text().split() if token] or ["synapsectl"]
            return [*base, "-u", self.device_uri.text().strip(), *tail]

        def _refresh_start_line(self):
            start = launcher.synapsectl_start_line(self.device_uri.text().strip(), _EXO_CONFIG)
            self.start_line.setText(" ".join(start))

        def update_controls(self):
            idle = not self.pending and not self.closing
            self.connect_button.setEnabled(idle)
            self.off_button.setEnabled(idle and self.connected)
            for button in self.query_buttons: button.setEnabled(idle and self.link_open)
            self.move_button.setEnabled(idle and self.link_open and self.allow_motion.isChecked())
            self.allow_motion.setEnabled(idle)
            self.joint.setEnabled(idle); self.value.setEnabled(idle)
            self.raw_input.setEnabled(idle and self.link_open)
            self.raw_button.setEnabled(idle and self.link_open)
            self.copy_button.setEnabled(bool(self.start_line.text()))
            run_idle = self.synapsectl_process is None
            self.run_start_button.setEnabled(run_idle)
            self.run_info_button.setEnabled(run_idle)

        # -- (1) synapsectl start / info gate --------------------------------
        def _copy_start_line(self):
            from PySide6.QtWidgets import QApplication
            self._refresh_start_line()
            QApplication.clipboard().setText(self.start_line.text())
            self.log.appendPlainText("copied the synapsectl start line to the clipboard (run it yourself)")

        def _run_synapsectl(self, tail, capture_to, label):
            if self.synapsectl_process is not None:
                return
            argv = self._synapsectl_argv(*tail)
            self.log.appendPlainText(f"running: {' '.join(argv)}")
            process = QProcess(self)
            process.setProgram(argv[0]); process.setArguments(argv[1:])
            process.setProcessChannelMode(QProcess.MergedChannels)
            buffer: list[str] = []
            process.readyReadStandardOutput.connect(
                lambda p=process, b=buffer: b.append(bytes(p.readAllStandardOutput()).decode("utf-8", "replace")))
            process.errorOccurred.connect(
                lambda err, l=label: self.events.put(("synapsectl_error", (l, str(err)))))
            process.finished.connect(
                lambda code, status, b=buffer, c=capture_to, l=label:
                    self.events.put(("synapsectl_done", (l, code, "".join(b), str(c) if c else None))))
            self.synapsectl_process = process
            self.update_controls()
            process.start()

        def _run_start_device(self):
            if self.device_started:
                # Always disarm + close the exo link BEFORE stopping the device.
                # synapsectl stop kills the App; if the exo link were still open
                # the App would die without de-asserting DTR / releasing the CDC,
                # which can wedge the OpenRB until a physical replug. Tearing down
                # first lets the App close the link cleanly. Best-effort and
                # bounded so it can never trap the stop.
                if self.link_open and self.controller.connected and self.synapsectl_process is None:
                    self.log.appendPlainText("disarming + closing the exo link before stopping the device...")
                    self.pending = True; self.update_controls()
                    def teardown():
                        error = None
                        try:
                            self.controller.set_exo_mode("off")
                        except Exception as exc:
                            error = str(exc)
                        self.events.put(("pre_stop_teardown", error))
                    self.executor.submit(teardown)
                else:
                    self._run_synapsectl(["stop"], None, "stop")
            else:
                self._run_synapsectl(["start", str(_EXO_CONFIG)], None, "start")

        def _run_fetch_info(self):
            self._run_synapsectl(["info"], None, "info")

        def _load_info(self):
            path, _ = QFileDialog.getOpenFileName(self, "Select synapsectl info capture")
            if not path:
                return
            try:
                info_text = Path(path).read_text(encoding="utf-8-sig")
            except OSError as error:
                self.log.appendPlainText(f"cannot read info capture: {error}")
                return
            self._apply_gate(info_text, Path(path).name)

        def _apply_gate(self, info_text, source):
            self.app_running = launcher.app_running(info_text)
            if self.app_running:
                self.gate_label.setText(f"App Running gate: PASSED ({source})")
                self.log.appendPlainText(f"info shows {launcher._APP_NAME} Running: True -- Connect / check USB is ready")
                if not self.device_started:
                    self.device_started = True
                    self.run_start_button.setText("Run: stop device")
            else:
                self.gate_label.setText(f"App Running gate: FAILED ({source})")
                self.log.appendPlainText(
                    f"info does NOT show {launcher._APP_NAME} Running: True -- resolve the device App start "
                    "(overall device Status: Running is not sufficient)")
            self.update_controls()

        # -- (2) exo link -----------------------------------------------------
        def connect_exo(self):
            # A restarted App publishes new Tap endpoints. connect() alone is
            # a no-op while the controller still considers its old session live.
            # This runs on the executor, only for an explicit USB Connect.
            self.controller.disconnect()
            self.controller.connect()
            # Establish a correlated round trip before the USB command, rather
            # than losing it during PUB/SUB subscription propagation.
            for attempt in range(3):
                try:
                    self.controller.get_state(timeout=1.0)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
            self.events.put(("connected", None))
            return self.controller.set_exo_mode("connected")

        def submit(self, work, op="op"):
            if self.pending or self.closing: return
            self.pending = True; self.update_controls()
            def run():
                try:
                    result = work()
                    self.events.put(("op", (op, True, str(result))))
                except Exception as error:
                    self.events.put(("op", (op, False, str(error))))
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
            self.submit(move, op="move")

        def send_raw(self):
            if not self.link_open or self.pending or self.closing:
                return
            text = self.raw_input.text().strip()
            if not text:
                return
            self.log.appendPlainText(f">>> {text}")
            self.raw_input.clear()
            self.submit(lambda: self.controller.exo_raw(text), op="raw")

        def drain_events(self):
            while not self.events.empty():
                kind, value = self.events.get_nowait()
                if kind == "state":
                    # State is shown for operator visibility only; it is NOT used
                    # to gate controls. The broadcast state.exo has proven stale on
                    # the bench (configured/link_open lagging or wrong even after a
                    # succeeded set_exo_mode), so link readiness is driven by the
                    # command results below (kind == "op") instead.
                    exo = value.exo
                    self.status.setText(f"USB {'open (per last command)' if self.link_open else 'closed'} | "
                                        f"state.mode {exo.mode} | armed {exo.armed} | "
                                        f"firmware {exo.firmware or 'unknown'} | watchdog {exo.watchdog_tripped}\n"
                                        f"{exo.last_error}")
                    text = json.dumps(state_to_json(value)["exo"])
                    if text != getattr(self, "last_status", None):
                        self.last_status = text; self.log.appendPlainText(text)
                elif kind == "op":
                    # Command-driven link state: the set_exo_mode/query results are
                    # authoritative (a succeeded "connected" means the device's exo
                    # worker has an open USB link), unlike the lagging broadcast.
                    op, ok, detail = value
                    self.pending = False
                    self.log.appendPlainText(f"{op}: {'ok' if ok else 'FAILED'} {detail}")
                    if op == "connect":
                        self.connected = self.connected or ok
                        self.link_open = ok
                    elif op == "off":
                        self.link_open = False
                    elif not ok:
                        # A failed query/move leaves the link's state per its last
                        # known value, but a transport failure closes it.
                        if "link closed" in detail or "outcome unknown" in detail:
                            self.link_open = False
                        self.allow_motion.setChecked(False)
                elif kind == "pre_stop_teardown":
                    # Exo link torn down (best-effort) ahead of synapsectl stop.
                    self.pending = False
                    self.link_open = False
                    if value:
                        self.log.appendPlainText(
                            f"pre-stop disarm did not confirm: {value}. Stopping anyway; verify the hand is safe.")
                    else:
                        self.log.appendPlainText("exo link closed; stopping the device.")
                    self.update_controls()
                    self._run_synapsectl(["stop"], None, "stop")
                elif kind == "connected": self.connected = True
                elif kind == "synapsectl_error":
                    label, error = value
                    self.synapsectl_process = None
                    self.log.appendPlainText(
                        f"[synapsectl {label}] could not run: {error} "
                        "(check the 'synapsectl command' field or use Copy + run it yourself)")
                elif kind == "synapsectl_done":
                    label, code, output, _capture = value
                    self.synapsectl_process = None
                    for line in output.splitlines():
                        self.log.appendPlainText(f"[synapsectl {label}] {line}")
                    self.log.appendPlainText(f"[synapsectl {label}] exited with code {code}")
                    if label == "info":
                        self._apply_gate(output, "synapsectl info")
                    elif label == "start" and code == 0:
                        self.device_started = True
                        self.run_start_button.setText("Run: stop device")
                        self.log.appendPlainText("device start succeeded -- fetch info + gate, then Connect / check USB")
                    elif label == "stop" and code == 0:
                        self.device_started = False
                        self.app_running = False
                        self.run_start_button.setText("Run: start device")
                        self.gate_label.setText("App Running gate: device stopped")
                elif kind == "cleanup_done":
                    # value is None on clean disarm/disconnect, or an error string.
                    fallback = getattr(self, "_close_fallback", None)
                    if fallback is not None:
                        fallback.stop()
                    if value:
                        self.log.appendPlainText(f"DISARM NOT CONFIRMED: {value}. Verify the hand is safe.")
                    self.closed = True
                    self.close()
                    return
                self.update_controls()

        # -- persisted field defaults ----------------------------------------
        def _persisted_fields(self):
            return {"device_uri": self.device_uri, "synapsectl": self.synapsectl}

        def _load_settings(self):
            for key, widget in self._persisted_fields().items():
                stored = self.settings.value(f"fields/{key}")
                if isinstance(stored, str) and stored:
                    widget.setText(stored)
            self._refresh_start_line()

        def _save_settings(self):
            for key, widget in self._persisted_fields().items():
                self.settings.setValue(f"fields/{key}", widget.text())
            self.settings.sync()

        def closeEvent(self, event):
            if self.closed:
                self._save_settings()
                self.timer.stop(); self.executor.shutdown(wait=False); event.accept(); return
            event.ignore()
            # A second close while cleanup is still running forces the window shut:
            # the disarm/disconnect is best-effort and must never trap the operator.
            if self.closing:
                self.log.appendPlainText("Forcing close; disarm/disconnect was not confirmed.")
                self.closed = True
                self.close()
                return
            self.closing = True; self.update_controls()
            self.log.appendPlainText("Closing: attempting USB disarm/disconnect (bounded; close again to force).")
            # Arm a fallback so a hung device call cannot trap the window: if
            # cleanup has not reported back shortly, allow the close anyway.
            from PySide6.QtCore import QTimer
            self._close_fallback = QTimer(self)
            self._close_fallback.setSingleShot(True)
            self._close_fallback.timeout.connect(self._force_close_after_timeout)
            self._close_fallback.start(8000)
            def cleanup():
                # Only command the device if we believe the link is actually open;
                # after a synapsectl stop or a prior "off" the App may be gone and
                # a blind set_exo_mode("off") would block on a reply that never
                # comes. disconnect() is always safe (bounded, best-effort).
                error = None
                try:
                    if self.link_open and self.controller.connected:
                        self.controller.set_exo_mode("off")
                except Exception as exc:
                    error = str(exc)
                finally:
                    try:
                        self.controller.disconnect()
                    except Exception:
                        pass
                self.events.put(("cleanup_done", error))
            self.executor.submit(cleanup)

        def _force_close_after_timeout(self):
            if not self.closed:
                self.log.appendPlainText("Disarm/disconnect timed out; closing anyway. Verify the hand is safe.")
                self.closed = True
                self.close()

    return ExoWindow()


def run_gui(device_ip):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    window = create_exo_window(device_ip)
    window.show()
    app.exec()
