"""PySide6 operator console for a full calibration session.

One window drives the same flow as the ``calibrate-session`` launcher, with the
same boundaries:

* it **never** runs ``synapsectl`` -- the operator runs the shown ``start`` line
  and supplies an ``info`` capture, which the window gates on via
  :func:`run_calibration_session.app_running` (see AGENTS.md, Synapse CLI
  execution boundary);
* it reuses the proven backend verbatim -- session generation goes through
  :func:`run_calibration_session.obtain_session`, and the host processes are the
  unchanged ``run_service.py`` and ``run_reactions_bridge.py`` children the
  launcher spawns;
* recording and task control go over a loopback WebSocket to the bridge using
  the exact ``sciencexyz_request`` envelopes the Reactions page sends, so the
  C++ recorder keeps a single owner and one code path.

The window touches Qt objects only on the UI thread: child-process output and
WebSocket results arrive on other threads and are marshalled back through a
drained ``queue.Queue``, matching ``stateful_decode_and_sync.gui``.
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Callable

# Backend reused as-is; imported at module load so a missing client install
# fails loudly rather than at button-press time. Qt is imported lazily inside
# the factory so headless tooling can import this module without a display.
import run_calibration_session as launcher


class _AsyncClient:
    """A loopback WebSocket client to the bridge, run on its own asyncio loop.

    Speaks the Reactions ``sciencexyz_request`` envelope: ``start_recording`` and
    ``stop_recording`` bracket a recording, and any other command is forwarded as
    a task command. One request is in flight at a time (the bridge admits a
    single recording controller), so replies are correlated by order.
    """

    def __init__(self, url: str, origin: str, emit: Callable[[str, object], None]):
        self._url = url
        self._origin = origin
        self._emit = emit
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="session-ws", daemon=True)
        self._socket = None
        self._request_seq = 0

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def connect(self) -> None:
        asyncio.run_coroutine_threadsafe(self._connect(), self._loop)

    def send_request(self, command: str, **arguments) -> None:
        asyncio.run_coroutine_threadsafe(self._send_request(command, arguments), self._loop)

    def close(self) -> None:
        if self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)
        self._loop.call_soon_threadsafe(self._loop.stop)

    # -- coroutines ----------------------------------------------------------
    async def _connect(self) -> None:
        from websockets.asyncio.client import connect
        try:
            # ``origin`` must match the bridge's allowlist; the bridge rejects a
            # mismatched or missing Origin exactly as it does for the browser.
            self._socket = await connect(self._url, origin=self._origin, max_size=256 * 1024)
            self._emit("ws_connected", self._url)
        except Exception as error:  # surfaced to the UI, never raised into Qt
            self._emit("ws_error", f"connect failed: {error}")

    async def _send_request(self, command: str, arguments: dict) -> None:
        if self._socket is None:
            self._emit("ws_error", "not connected to bridge")
            return
        self._request_seq += 1
        request_id = f"gui/{self._request_seq}"
        envelope = {"api_version": "0.12", "api_request": {
            "request_id": request_id, "sciencexyz_request": {"command": command, **arguments}}}
        try:
            await self._socket.send(json.dumps(envelope, allow_nan=False))
            reply = json.loads(await self._socket.recv())
        except Exception as error:
            self._emit("ws_error", f"{command} failed: {error}")
            return
        response = reply.get("api_response", {})
        if response.get("success"):
            self._emit("ws_result", (command, response.get("result")))
        else:
            self._emit("ws_error", f"{command} refused: {response.get('error')}")

    async def _close(self) -> None:
        if self._socket is not None:
            try:
                await self._socket.close()
            except Exception:
                pass
            self._socket = None


def create_session_window():
    """Create the calibration-session console window."""
    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtWidgets import (
        QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
        QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
    )

    class SessionWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Calibration Session Console")
            try:
                from .appicon import apply_app_icon
                apply_app_icon(self)
            except Exception:
                pass
            self.resize(820, 720)
            self.events: queue.Queue[tuple[str, object]] = queue.Queue()
            self.service: QProcess | None = None
            self.bridge: QProcess | None = None
            self.client: _AsyncClient | None = None
            self.session_dir: Path | None = None
            self.app_running = False
            self.bridge_ready = False
            self.recording = False
            self._build_ui()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._drain_events)
            self.timer.start(50)
            self._refresh_enabled()

        # -- UI construction --------------------------------------------------
        def _build_ui(self):
            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)

            # (1) device + session inputs
            box1 = QGroupBox("1. Device and session")
            form = QFormLayout(box1)
            self.device_uri = QLineEdit("192.168.100.157")
            self.device_tap = QLineEdit("192.168.100.157:647")
            self.origin = QLineEdit("https://chr.nml.wtf")
            self.gestures = QLineEdit()
            self.gestures.setPlaceholderText("optional MOTION_LUT keys, e.g. Fist Paper (blank = linear MVP)")
            self.session_input = QLineEdit("data/reactions/session-001")
            self.output_root = QLineEdit("data/reactions")
            self.recorder = QLineEdit("build/raw-recorder/task-recorder")
            self.service_port = QLineEdit("18765")
            self.bridge_port = QLineEdit("9999")
            for label, widget in [
                ("Device URI (synapsectl)", self.device_uri), ("Device tap (ip:port)", self.device_tap),
                ("Reactions origin", self.origin), ("Gestures", self.gestures),
                ("Session dir", self.session_input), ("Output root", self.output_root),
                ("Recorder path", self.recorder), ("Service port", self.service_port),
                ("Bridge port", self.bridge_port)]:
                form.addRow(label, widget)
            self.generate_button = QPushButton("Generate / reuse session")
            self.generate_button.clicked.connect(self._generate)
            form.addRow(self.generate_button)
            layout.addWidget(box1)

            # (2) operator device start + info gate
            box2 = QGroupBox("2. Operator: start the device App (synapsectl is NOT run here)")
            v2 = QVBoxLayout(box2)
            self.start_line = QLineEdit()
            self.start_line.setReadOnly(True)
            self.start_line.setPlaceholderText("generate a session to see the exact synapsectl start line")
            copy_row = QHBoxLayout()
            self.copy_button = QPushButton("Copy start line")
            self.copy_button.clicked.connect(self._copy_start_line)
            self.load_info_button = QPushButton("Load info capture...")
            self.load_info_button.clicked.connect(self._load_info)
            copy_row.addWidget(self.copy_button)
            copy_row.addWidget(self.load_info_button)
            self.gate_label = QLabel("App Running gate: not checked")
            v2.addWidget(self.start_line)
            v2.addLayout(copy_row)
            v2.addWidget(self.gate_label)
            layout.addWidget(box2)

            # (3) host processes
            box3 = QGroupBox("3. Host processes")
            v3 = QVBoxLayout(box3)
            self.launch_button = QPushButton("Start service + bridge")
            self.launch_button.clicked.connect(self._launch_hosts)
            self.stop_hosts_button = QPushButton("Stop host processes")
            self.stop_hosts_button.clicked.connect(self._stop_hosts)
            row3 = QHBoxLayout(); row3.addWidget(self.launch_button); row3.addWidget(self.stop_hosts_button)
            self.hosts_label = QLabel("Host processes: not started")
            v3.addLayout(row3)
            v3.addWidget(self.hosts_label)
            layout.addWidget(box3)

            # (4) recording + task control
            box4 = QGroupBox("4. Recording and task control")
            v4 = QVBoxLayout(box4)
            self.connect_button = QPushButton("Connect to bridge")
            self.connect_button.clicked.connect(self._connect_bridge)
            self.record_button = QPushButton("Start recording")
            self.record_button.clicked.connect(self._toggle_recording)
            self.event_input = QLineEdit()
            self.event_input.setPlaceholderText("task event name, e.g. advance or to_active.hand_squeeze")
            self.event_button = QPushButton("Propose task event")
            self.event_button.clicked.connect(self._propose_event)
            self.reset_button = QPushButton("Reset task")
            self.reset_button.clicked.connect(lambda: self._task_command("reset_task"))
            row4a = QHBoxLayout(); row4a.addWidget(self.connect_button); row4a.addWidget(self.record_button)
            row4b = QHBoxLayout()
            row4b.addWidget(self.event_input); row4b.addWidget(self.event_button); row4b.addWidget(self.reset_button)
            self.record_label = QLabel("Recording: idle")
            v4.addLayout(row4a)
            v4.addLayout(row4b)
            v4.addWidget(self.record_label)
            layout.addWidget(box4)

            # log pane
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(2000)
            layout.addWidget(self.log, 1)

        # -- helpers ----------------------------------------------------------
        def _emit(self, kind: str, payload: object) -> None:
            self.events.put((kind, payload))

        def _log(self, text: str) -> None:
            self.log.appendPlainText(text.rstrip())

        def _origins(self) -> list[str]:
            return [o for o in self.origin.text().split() if o]

        def _launcher_args(self):
            """Argument namespace the reused launcher/bridge helpers expect."""
            from argparse import Namespace
            return Namespace(
                device_uri=self.device_uri.text().strip(),
                device_tap=self.device_tap.text().strip(),
                recorder=self.recorder.text().strip(),
                output_root=self.output_root.text().strip(),
                service_port=int(self.service_port.text()),
                bridge_port=int(self.bridge_port.text()),
                origin=self._origins())

        def _refresh_enabled(self) -> None:
            generated = self.session_dir is not None
            self.copy_button.setEnabled(bool(self.start_line.text()))
            self.load_info_button.setEnabled(generated)
            self.launch_button.setEnabled(generated and self.app_running and self.service is None)
            self.stop_hosts_button.setEnabled(self.service is not None or self.bridge is not None)
            self.connect_button.setEnabled(self.bridge_ready and self.client is None)
            connected = self.client is not None
            self.record_button.setEnabled(connected)
            self.event_button.setEnabled(connected and self.recording)
            self.reset_button.setEnabled(connected and self.recording)

        # -- (1) generate -----------------------------------------------------
        def _generate(self) -> None:
            session = Path(self.session_input.text().strip())
            gestures = self.gestures.text().split() or None
            try:
                profile, reused = launcher.obtain_session(
                    gestures, str(_REPO_ROOT / "config" / "rhd2132.json"), session,
                    str(_REPO_ROOT / "config" / "calibration-provenance.template.json"))
            except (OSError, ValueError) as error:
                self._log(f"cannot generate session: {error}; choose a fresh session dir")
                return
            self.session_dir = session
            verb = "Reused existing" if reused else "Generated"
            self._log(f"{verb} session {session}; definition {profile['definition_hash']}.")
            start = launcher.synapsectl_start_line(self.device_uri.text().strip(), session / "device-config.json")
            self.start_line.setText(" ".join(start))
            self._refresh_enabled()

        def _copy_start_line(self) -> None:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.start_line.text())
            self._log("copied the synapsectl start line to the clipboard (run it yourself)")

        # -- (2) info gate ----------------------------------------------------
        def _load_info(self) -> None:
            start = str(self.session_dir) if self.session_dir else ""
            path, _ = QFileDialog.getOpenFileName(self, "Select synapsectl info capture", start)
            if not path:
                return
            try:
                info_text = Path(path).read_text(encoding="utf-8-sig")
            except OSError as error:
                self._log(f"cannot read info capture: {error}")
                return
            self.app_running = launcher.app_running(info_text)
            if self.app_running:
                self.gate_label.setText(f"App Running gate: PASSED ({Path(path).name})")
                self._log(f"info capture shows {launcher._APP_NAME} Running: True -- host launch enabled")
            else:
                self.gate_label.setText(f"App Running gate: FAILED ({Path(path).name})")
                self._log(f"info capture does NOT show {launcher._APP_NAME} Running: True -- resolve the device App start")
            self._refresh_enabled()

        # -- (3) host processes ----------------------------------------------
        def _spawn(self, name: str, argv: list[str]) -> "QProcess":
            process = QProcess(self)
            process.setProgram(argv[0])
            process.setArguments(argv[1:])
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(lambda p=process, n=name: self._read_process(p, n))
            process.finished.connect(lambda code, status, n=name: self._emit("host_exit", (n, code)))
            process.start()
            return process

        def _read_process(self, process, name: str) -> None:
            text = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
            for line in text.splitlines():
                self._emit("host_log", (name, line))

        def _launch_hosts(self) -> None:
            if self.session_dir is None or not self.app_running:
                return
            args = self._launcher_args()
            python = launcher.sys.executable
            self.service = self._spawn("service", launcher.service_command(python, args.device_uri, args.service_port))
            self.bridge = self._spawn("bridge", launcher.bridge_command(python, args, self.session_dir))
            self.hosts_label.setText("Host processes: starting...")
            self._log("launching service + bridge (this window owns them; close or Stop to end)")
            self._refresh_enabled()

        def _stop_hosts(self) -> None:
            if self.client is not None:
                self.client.close()
                self.client = None
                self.recording = False
                self.record_button.setText("Start recording")
            for process in (self.bridge, self.service):
                if process is not None and process.state() != QProcess.NotRunning:
                    process.terminate()
                    if not process.waitForFinished(4000):
                        process.kill()
            self.bridge = self.service = None
            self.bridge_ready = False
            self.hosts_label.setText("Host processes: stopped")
            self._refresh_enabled()

        # -- (4) recording + task via the bridge WebSocket -------------------
        def _connect_bridge(self) -> None:
            origins = self._origins()
            if not origins:
                self._log("set at least one origin before connecting")
                return
            url = f"ws://127.0.0.1:{int(self.bridge_port.text())}"
            self.client = _AsyncClient(url, origins[0], self._emit)
            self.client.start()
            self.client.connect()
            self._refresh_enabled()

        def _toggle_recording(self) -> None:
            if self.client is None:
                return
            if not self.recording:
                self.client.send_request("start_recording", metadata={
                    "operator_gui": True, "note": "started from the calibration session console"})
            else:
                self.client.send_request("stop_recording")

        def _propose_event(self) -> None:
            name = self.event_input.text().strip()
            if not name:
                self._log("enter a task event name first")
                return
            self._task_command("propose_task_event", event_name=name)

        def _task_command(self, command: str, **arguments) -> None:
            if self.client is not None:
                self.client.send_request(command, **arguments)

        # -- event drain (UI thread) -----------------------------------------
        def _drain_events(self) -> None:
            while True:
                try:
                    kind, payload = self.events.get_nowait()
                except queue.Empty:
                    return
                if kind == "host_log":
                    name, line = payload
                    self._log(f"[{name}] {line}")
                    if name == "bridge" and "Reactions bridge ws://" in line:
                        self.bridge_ready = True
                        self.hosts_label.setText("Host processes: running (bridge ready)")
                        self._refresh_enabled()
                elif kind == "host_exit":
                    name, code = payload
                    self._log(f"[{name}] exited with code {code}")
                    if name == "bridge":
                        self.bridge_ready = False
                    self._refresh_enabled()
                elif kind == "ws_connected":
                    self._log(f"connected to bridge at {payload}")
                    self._refresh_enabled()
                elif kind == "ws_error":
                    self._log(f"bridge error: {payload}")
                elif kind == "ws_result":
                    command, result = payload
                    self._log(f"{command} -> {json.dumps(result, default=str)}")
                    if command == "start_recording":
                        self.recording = True
                        self.record_button.setText("Stop recording")
                        self.record_label.setText(f"Recording: {result.get('session_dir', 'active')}")
                    elif command == "stop_recording":
                        self.recording = False
                        self.record_button.setText("Start recording")
                        self.record_label.setText("Recording: stopped")
                    self._refresh_enabled()

        def closeEvent(self, event):  # noqa: N802 (Qt override)
            self._stop_hosts()
            super().closeEvent(event)

    return SessionWindow


# Repository root: apps/stateful-decode-and-sync/client -> repo is three up.
_REPO_ROOT = launcher._REPO_ROOT


def run_session_gui() -> None:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    try:
        from .appicon import apply_app_icon
        apply_app_icon(app)
    except Exception:
        pass
    window = create_session_window()()
    window.show()
    app.exec()
