import os
import unittest

try:
    from PySide6.QtWidgets import QApplication
    from broadband_mode_switch.gui import create_dashboard_window
    from broadband_mode_switch.model import ActiveTarget, AppState, CollectionState, CommandResult, LabelState, ModelState
except ModuleNotFoundError:
    QApplication = None


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_state_snapshot_renders_progress_and_model(self):
        window = create_dashboard_window("fake")
        state = AppState(
            pipeline_state="ready",
            source_mode="sampling",
            active=ActiveTarget(0, 1, False),
            collections=(CollectionState(0, 256, 4, (LabelState(0, 10, 20), LabelState(1, 20, 20))),),
            model=ModelState("running", True, 0, 3, False, 3, 10, 0.12, 0.9, 42),
        )
        window.show_state(state)
        self.assertEqual(window.pipeline.text(), "ready")
        self.assertEqual(window.source.text(), "Source: sampling")
        self.assertIn("epoch 3/10", window.model.text())
        self.assertEqual(window.progress_layout.count(), 2)
        window.close()

    def test_zero_capacity_is_rendered_as_unknown(self):
        window = create_dashboard_window("fake")
        window.show_state(AppState(collections=(CollectionState(0, 256, 0, (LabelState(0, 0, 0),)),)))
        bar = window.progress_layout.itemAt(0).widget()
        self.assertFalse(bar.isEnabled())
        self.assertIn("unknown capacity", bar.format())
        window.close()

    def test_pending_gate_rejection_and_timeout_reenable_controls(self):
        window = create_dashboard_window("fake")
        window._command_pending = True
        window._set_command_controls_enabled(False)
        window.controller = object()
        window._submit(lambda: None)
        self.assertTrue(window._command_pending)
        self.assertFalse(window.apply.isEnabled())
        for message in ("device rejected", "timed out"):
            window.events.put(("error", message))
            window._drain_events()
            self.assertFalse(window._command_pending)
            self.assertTrue(window.apply.isEnabled())
            self.assertEqual(window.message.text(), message)
            window._command_pending = True
            window._set_command_controls_enabled(False)
        window.controller = None
        window.close()

    def test_every_flush_scope_is_confirmed_and_submitted(self):
        class ImmediateExecutor:
            def submit(self, work, *args):
                work(*args)
            def shutdown(self, **kwargs):
                pass

        class FakeController:
            def __init__(self):
                self.flushes = []
            def flush(self, scope, collection_id=None, label=None):
                self.flushes.append((scope, collection_id, label))
                return CommandResult(1, "x", "flush", "succeeded", 1)
            def disconnect(self):
                pass

        window = create_dashboard_window("fake")
        fake = FakeController()
        window.controller = fake
        window.executor.shutdown(wait=False, cancel_futures=True)
        window.executor = ImmediateExecutor()
        from PySide6.QtWidgets import QMessageBox
        original = QMessageBox.question
        QMessageBox.question = lambda *args, **kwargs: QMessageBox.StandardButton.Yes
        try:
            for scope in ("label", "collection", "all"):
                window._flush(scope)
                window._drain_events()
        finally:
            QMessageBox.question = original
        self.assertEqual(fake.flushes, [("label", 0, 0), ("collection", 0, None), ("all", None, None)])
        window.controller = None
        window.close()


if __name__ == "__main__":
    unittest.main()
