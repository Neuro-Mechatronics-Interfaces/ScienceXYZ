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
