import os
import tempfile
import time
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from scifi2_hub_manager.exo_gui import create_exo_window
from scifi2_hub_manager.model import AppState, ExoState


@pytest.fixture(autouse=True)
def _isolated_settings():
    # The Exo GUI persists a couple of fields to a per-user INI (QSettings). Point
    # it at a throwaway dir so tests never read or write the operator's real file,
    # and so one test's saved value cannot leak into another's built-in defaults.
    with tempfile.TemporaryDirectory() as temp:
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, temp)
        yield


def test_gui_motion_is_explicit_and_disarms():
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = False
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    assert not window.move_button.isEnabled()
    window.value.setValue(20)
    controller.set_exo_pose.assert_not_called()
    # A succeeded "connect" command opens the link (command-driven, not state).
    window.events.put(("op", ("connect", True, "succeeded")))
    window.drain_events()
    assert window.link_open
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
    # A failed op with an uncertain outcome closes the link and clears motion.
    window.events.put(("op", ("query", False, "USB timeout; outcome unknown")))
    window.drain_events()
    assert not window.link_open
    assert not window.allow_motion.isChecked()
    assert not window.move_button.isEnabled()
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.closed
    controller.disconnect.assert_called_once()


def test_link_gating_is_command_driven_not_broadcast_state():
    # The broadcast state.exo has proven stale on the bench (configured:false even
    # after a succeeded set_exo_mode). Link readiness must come from the command
    # results, not the state stream.
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = False
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    window.allow_motion.setChecked(True)
    # A stale/empty broadcast state must NOT drive the link either way.
    window.events.put(("state", AppState(
        pipeline_state="unspecified",
        exo=ExoState(configured=False, link_open=False))))
    window.drain_events()
    assert not window.link_open
    assert not window.move_button.isEnabled()
    # The succeeded connect command opens the link despite that stale state.
    window.events.put(("op", ("connect", True, "succeeded")))
    window.drain_events()
    assert window.link_open
    assert window.move_button.isEnabled()
    # A succeeded "off" closes it.
    window.events.put(("op", ("off", True, "succeeded")))
    window.drain_events()
    assert not window.link_open
    assert not window.move_button.isEnabled()
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)


def test_gui_shows_exo_start_line_and_gates_on_app_running():
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = False
    window = create_exo_window("192.168.100.157", controller_factory=lambda ip: controller)
    # The shown start line targets the App's Exo config (not baseline rhd2132.json).
    line = window.start_line.text()
    assert "synapsectl" in line and "start" in line and "rhd2132_with_exo.json" in line
    assert "192.168.100.157" in line
    # Gate starts unchecked; a non-running info capture keeps Connect ungated.
    assert window.app_running is False
    window._apply_gate("Application other-app\n  Running: True\n", "test")
    assert window.app_running is False
    # An info capture showing the hub App Running flips the gate.
    window._apply_gate("Application scifi2-hub-manager\n  Running: True\n", "test")
    assert window.app_running is True
    assert "PASSED" in window.gate_label.text()
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.closed


def test_gui_close_surfaces_failed_disarm_but_still_closes():
    # A failed disarm on close must WARN but never trap the window open (that was
    # the close-hang). The disconnect is best-effort; the operator verifies safety.
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = True
    controller.set_exo_mode.side_effect = RuntimeError("USB lost")
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    window.link_open = True  # believe the link is open so cleanup attempts disarm
    window.show(); window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.closed
    assert "DISARM NOT CONFIRMED" in window.log.toPlainText()
    controller.disconnect.assert_called_once()  # disconnect still runs


def test_stop_device_disarms_exo_link_first():
    # Run: stop device must disarm + close the exo link BEFORE synapsectl stop,
    # so the App is not killed with the link open (which wedges the OpenRB).
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = True
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    # Record synapsectl launches instead of spawning a real process.
    ran = []
    window._run_synapsectl = lambda tail, capture, label: ran.append((tail, label))
    # Simulate a running device with an open exo link.
    window.device_started = True
    window.events.put(("op", ("connect", True, "succeeded")))
    window.drain_events()
    assert window.link_open
    # Click stop -> disarm runs first (off-thread), synapsectl NOT yet launched.
    window._run_start_device()
    assert ran == []
    controller.set_exo_mode.assert_called_once_with("off")
    # Once the teardown reports back, the link is closed and stop is launched.
    deadline = time.monotonic() + 2
    while not ran and time.monotonic() < deadline:
        window.drain_events(); app.processEvents(); time.sleep(0.01)
    assert not window.link_open
    assert ran == [(["stop"], "stop")]
    window.link_open = False  # avoid a close-time device call in teardown
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)


def test_stop_device_skips_disarm_when_link_closed():
    # If the link is already closed, stop goes straight to synapsectl (no blocking
    # set_exo_mode against a device we are not connected to).
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = True
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    ran = []
    window._run_synapsectl = lambda tail, capture, label: ran.append((tail, label))
    window.device_started = True
    assert window.link_open is False
    window._run_start_device()
    controller.set_exo_mode.assert_not_called()
    assert ran == [(["stop"], "stop")]
    window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)


def test_gui_close_skips_device_command_when_link_not_open():
    # After a synapsectl stop / prior off, link_open is False: cleanup must NOT
    # call set_exo_mode (which would block on a dead App) -- only disconnect.
    app = QApplication.instance() or QApplication([])
    controller = Mock()
    controller.connected = True
    window = create_exo_window("fake", controller_factory=lambda ip: controller)
    assert window.link_open is False
    window.show(); window.close()
    deadline = time.monotonic() + 2
    while not window.closed and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert window.closed
    controller.set_exo_mode.assert_not_called()
    controller.disconnect.assert_called_once()
