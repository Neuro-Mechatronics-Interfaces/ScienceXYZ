"""Hardware-free tests for the calibration-session GUI console.

No device, no synapsectl, no child processes and no browser: the window is
constructed on Qt's offscreen platform and driven through the same pure-logic
paths the operator clicks -- session generation/reuse and the App-Running gate.
The tests are skipped when PySide6 cannot start (e.g. no Qt platform plugin).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    import stateful_decode_and_sync.session_gui as sg
    _APP = QApplication.instance() or QApplication([])
    _HAVE_QT = True
except Exception:  # pragma: no cover - environment without a usable Qt
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PySide6 offscreen platform unavailable")
class SessionGuiLogicTests(unittest.TestCase):
    def _window(self):
        return sg.create_session_window()()

    def test_generate_then_gate_enables_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "session"
            window = self._window()
            window.session_input.setText(str(session))
            window.gestures.setText("")  # linear MVP
            window._generate()
            self.assertIsNotNone(window.session_dir)
            self.assertTrue(window.start_line.text().startswith("synapsectl -u"))
            self.assertEqual({p.name for p in session.iterdir()},
                             {"device-config.json", "task-profile.json", "provenance.json"})
            # Launch stays disabled until the App-Running gate passes.
            self.assertFalse(window.launch_button.isEnabled())

            info = Path(temp) / "info.txt"
            info.write_text("Applications\n  stateful-decode-and-sync\n    Running: True\n")
            window.app_running = sg.launcher.app_running(info.read_text())
            window._refresh_enabled()
            self.assertTrue(window.launch_button.isEnabled())
            # Recording/task controls remain disabled with no bridge connection.
            self.assertFalse(window.record_button.isEnabled())
            self.assertFalse(window.connect_button.isEnabled())

    def test_regenerate_reuses_existing_session(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "session"
            window = self._window()
            window.session_input.setText(str(session))
            window._generate()
            before = (session / "task-profile.json").read_text()
            # Clicking generate again over a complete session reuses it.
            window._generate()
            self.assertEqual((session / "task-profile.json").read_text(), before)

    def test_synapsectl_argv_uses_configurable_command(self):
        window = self._window()
        window.device_uri.setText("192.168.100.157")
        # Default single-token command.
        self.assertEqual(window._synapsectl_argv("info"),
                         ["synapsectl", "-u", "192.168.100.157", "info"])
        # Multi-token command (e.g. a WSL-prefixed install) splits into argv.
        window.synapsectl.setText("wsl synapsectl")
        self.assertEqual(window._synapsectl_argv("start", "cfg.json"),
                         ["wsl", "synapsectl", "-u", "192.168.100.157", "start", "cfg.json"])
        # Empty field falls back to the bare command rather than an empty argv.
        window.synapsectl.setText("   ")
        self.assertEqual(window._synapsectl_argv("info")[0], "synapsectl")

    def test_start_stop_button_toggles_on_success(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "session"
            window = self._window()
            window.session_input.setText(str(session))
            window._generate()
            self.assertEqual(window.run_start_button.text(), "Run: start device")
            self.assertFalse(window.device_started)

            # A successful start flips the button to offer stop.
            window.synapsectl_process = object()
            window.events.put(("synapsectl_done", ("start", 0, "ok\n", None)))
            window._drain_events()
            self.assertTrue(window.device_started)
            self.assertEqual(window.run_start_button.text(), "Run: stop device")

            # A successful stop flips it back and clears the App-Running gate.
            window.app_running = True
            window.synapsectl_process = object()
            window.events.put(("synapsectl_done", ("stop", 0, "stopped\n", None)))
            window._drain_events()
            self.assertFalse(window.device_started)
            self.assertEqual(window.run_start_button.text(), "Run: start device")
            self.assertFalse(window.app_running)

            # A failed start does NOT toggle.
            window.synapsectl_process = object()
            window.events.put(("synapsectl_done", ("start", 1, "boom\n", None)))
            window._drain_events()
            self.assertFalse(window.device_started)
            self.assertEqual(window.run_start_button.text(), "Run: start device")

    def test_gate_passing_marks_device_started(self):
        # If info shows the App already Running, the device IS started, so the
        # Run button flips to offer stop (device was left running with the config).
        window = self._window()
        self.assertFalse(window.device_started)
        self.assertEqual(window.run_start_button.text(), "Run: start device")
        window._apply_gate("Applications\n  stateful-decode-and-sync\n    Running: True\n", "info")
        self.assertTrue(window.app_running)
        self.assertTrue(window.device_started)
        self.assertEqual(window.run_start_button.text(), "Run: stop device")

    def test_block_spinbox_flows_into_launcher_args(self):
        window = self._window()
        self.assertEqual(window.block.value(), 1)  # default
        window.block.setValue(7)
        self.assertEqual(window._launcher_args().block, 7)

    def test_apply_gate_reads_running_from_captured_info(self):
        window = self._window()
        window._apply_gate("Applications\n  stateful-decode-and-sync\n    Running: True\n", "synapsectl info")
        self.assertTrue(window.app_running)
        window._apply_gate("Applications\n  stateful-decode-and-sync\n    Running: False\n", "synapsectl info")
        self.assertFalse(window.app_running)

    def test_field_defaults_persist_across_launches(self):
        # Redirect the user-scope INI into a temp dir so the real settings file
        # is never touched.
        with tempfile.TemporaryDirectory() as temp:
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temp)
            first = self._window()
            self.assertEqual(first.device_uri.text(), "192.168.100.157")  # built-in default
            first.device_uri.setText("10.0.0.42")
            first.origin.setText("http://localhost:8080")
            first.gestures.setText("Fist Paper")
            first._save_settings()

            # A fresh window loads the saved values, not the hard-coded defaults.
            second = self._window()
            self.assertEqual(second.device_uri.text(), "10.0.0.42")
            self.assertEqual(second.origin.text(), "http://localhost:8080")
            self.assertEqual(second.gestures.text(), "Fist Paper")

    def test_manual_recording_panel_hidden_by_default(self):
        window = self._window()
        self.assertTrue(window.manual_panel.isHidden())
        self.assertFalse(window.show_manual.isChecked())
        window.show_manual.setChecked(True)
        self.assertFalse(window.manual_panel.isHidden())
        window.show_manual.setChecked(False)
        self.assertTrue(window.manual_panel.isHidden())

    def test_absent_settings_keep_builtin_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temp)
            window = self._window()  # no file written yet
            self.assertEqual(window.device_uri.text(), "192.168.100.157")
            self.assertEqual(window.bridge_port.text(), "9999")

    def test_hub_spoke_gestures_embed_in_generated_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            session = Path(temp) / "session"
            window = self._window()
            window.session_input.setText(str(session))
            window.gestures.setText("Fist Paper")
            window._generate()
            profile = json.loads((session / "task-profile.json").read_text())
            self.assertEqual(profile["profile_shape"], "hub_and_spoke")
            self.assertEqual(profile["gesture_keys"], ["Fist", "Paper"])


if __name__ == "__main__":
    unittest.main()
