import os
import time
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from scifi2_hub_manager.exo_gui import create_exo_window
from scifi2_hub_manager.model import AppState, ExoState


def test_gui_motion_is_explicit_and_disarms():
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = False
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    assert not window.move_button.isEnabled()
    window.value.setValue(20)
    controller.set_exo_pose.assert_not_called()
    window.events.put(("state", AppState(pipeline_state="ready", exo=ExoState(link_open=True, mode="connected"))))
    window.drain_events()
    assert not window.move_button.isEnabled()
    window.allow_motion.setChecked(True)
    assert window.move_button.isEnabled()
    window.move_button.click()
    assert not window.move_button.isEnabled()
    deadline = time.monotonic() + 2
    while window.pending and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert not window.pending
    controller.set_exo_pose.assert_called_once_with({"index": 20})
    assert [c.args[0] for c in controller.set_exo_mode.call_args_list] == ["external", "connected"]
    window.events.put(("error", "USB timeout; outcome unknown"))
    window.drain_events()
    assert not window.allow_motion.isChecked()
    assert not window.move_button.isEnabled()
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.closed
    controller.disconnect.assert_called_once()


def test_gui_retains_failed_close_for_operator():
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = True
    controller.set_exo_mode.side_effect = RuntimeError("USB lost")
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    window.show(); window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.isVisible()
    assert "DISARM NOT CONFIRMED" in window.log.toPlainText()
    window.close()
    assert not window.isVisible()
