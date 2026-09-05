import os
import queue
import time
import unittest

from synapse.api.channel_pb2 import ChannelType
from synapse.api.datatype_pb2 import BroadbandFrame

from stateful_decode_and_sync.waveform import (
    BroadbandStreamReader,
    WaveformBuffer,
    grid_placements,
    parse_channel_spec,
    parse_gain_spec,
)

try:
    from PySide6.QtWidgets import QApplication

    # Importing the module first pins PYQTGRAPH_QT_LIB=PySide6 before pyqtgraph
    # is imported, so its widgets are real PySide6 widgets.
    from stateful_decode_and_sync.waveform import create_waveform_window

    import pyqtgraph  # noqa: F401

    _GUI_DEPS = True
except ModuleNotFoundError:
    _GUI_DEPS = False


def frame(sequence, timestamp, values, *, sample_rate=20_000):
    f = BroadbandFrame(
        timestamp_ns=timestamp,
        sequence_number=sequence,
        frame_data=list(values),
        sample_rate_hz=sample_rate,
    )
    f.channel_ranges.add(type=ChannelType.ELECTRODE, count=len(values))
    return f


class WaveformBufferTests(unittest.TestCase):
    def test_channel_set_and_time_order(self):
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=4, max_channels=2)
        self.assertEqual(buf.capacity, 4)
        for seq, ts, vals in ((0, 0, (10, 20)), (1, 1, (11, 21)), (2, 2, (12, 22))):
            self.assertTrue(buf.add_frame(frame(seq, ts, vals, sample_rate=4)))

        snap = buf.snapshot()
        self.assertEqual(snap.channel_ids, (0, 1))
        # Oldest sample first, newest last.
        self.assertEqual(list(snap.samples[0]), [10.0, 11.0, 12.0])
        self.assertEqual(list(snap.samples[1]), [20.0, 21.0, 22.0])
        self.assertEqual(snap.frames, 3)
        # The buffer adopts the sample rate declared on the wire frames.
        self.assertEqual(snap.sample_rate_hz, 4)

    def test_ring_wraps_and_preserves_recency(self):
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=3, max_channels=1)
        for i in range(5):
            buf.add_frame(frame(i, i, (i,)))
        snap = buf.snapshot()
        # Capacity 3, so only the newest three samples remain, in order.
        self.assertEqual(list(snap.samples[0]), [2.0, 3.0, 4.0])

    def test_extra_channels_truncated_to_first_frame_width(self):
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=4, max_channels=2)
        buf.add_frame(frame(0, 0, (1, 2)))
        buf.add_frame(frame(1, 1, (3, 4, 5, 6)))  # wider frame is truncated
        snap = buf.snapshot()
        self.assertEqual(snap.samples.shape[0], 2)
        self.assertEqual(list(snap.samples[0]), [1.0, 3.0])

    def test_missing_sequences_and_malformed(self):
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=10, max_channels=1)
        buf.add_frame(frame(100, 0, (1,)))
        buf.add_frame(frame(103, 1, (2,)))  # gap of 2
        self.assertFalse(buf.add_raw(b"not-a-frame"))
        self.assertFalse(buf.add_frame(frame(104, 2, ())))  # empty payload
        snap = buf.snapshot()
        self.assertEqual(snap.missing_sequences, 2)
        self.assertEqual(snap.parse_errors, 2)
        self.assertEqual(snap.sequence_last, 103)


class LayoutSpecTests(unittest.TestCase):
    def test_empty_spec_selects_all_in_order(self):
        self.assertEqual(parse_channel_spec("", 4), [0, 1, 2, 3])

    def test_ranges_ascending_and_descending(self):
        self.assertEqual(parse_channel_spec("0-3", 32), [0, 1, 2, 3])
        self.assertEqual(parse_channel_spec("3-0", 32), [3, 2, 1, 0])

    def test_lists_mixed_and_reorder_preserve_written_order(self):
        self.assertEqual(parse_channel_spec("0,4,8,12", 32), [0, 4, 8, 12])
        self.assertEqual(parse_channel_spec("0-3, 8-11", 32), [0, 1, 2, 3, 8, 9, 10, 11])
        self.assertEqual(parse_channel_spec("3,2,1,0", 4), [3, 2, 1, 0])

    def test_out_of_range_and_malformed_and_empty_available_raise(self):
        with self.assertRaises(ValueError):
            parse_channel_spec("0-40", 32)
        with self.assertRaises(ValueError):
            parse_channel_spec("0-x", 32)
        with self.assertRaises(ValueError):
            parse_channel_spec("-5", 32)
        with self.assertRaises(ValueError):
            parse_channel_spec("0", 0)

    def test_grid_is_row_major_and_columns_clamp(self):
        placements = grid_placements(range(32), 8)
        self.assertEqual(len(placements), 32)
        self.assertEqual(max(p.row for p in placements) + 1, 4)  # 4x8 grid
        self.assertEqual(max(p.col for p in placements) + 1, 8)
        self.assertEqual((placements[9].row, placements[9].col), (1, 1))
        # Columns clamp into [1, len]; 0 -> 1 column, huge -> len columns.
        self.assertTrue(all(p.col == 0 for p in grid_placements([0, 1, 2], 0)))
        self.assertEqual({p.col for p in grid_placements([0, 1, 2], 100)}, {0, 1, 2})

    def test_empty_channel_list_places_nothing(self):
        self.assertEqual(grid_placements([], 4), [])


class GainSpecTests(unittest.TestCase):
    def test_empty_spec_is_no_overrides(self):
        self.assertEqual(parse_gain_spec(""), {})
        self.assertEqual(parse_gain_spec("   "), {})

    def test_parses_channel_colon_gain_tokens(self):
        self.assertEqual(parse_gain_spec("0:2, 4:0.5 8:10"), {0: 2.0, 4: 0.5, 8: 10.0})

    def test_last_value_wins_for_duplicates(self):
        self.assertEqual(parse_gain_spec("3:2 3:9"), {3: 9.0})

    def test_malformed_and_nonpositive_raise(self):
        for bad in ("5", "x:2", "0:y", "-1:2", "0:0", "0:-3", "0:inf"):
            with self.assertRaises(ValueError):
                parse_gain_spec(bad)


class FakeTap:
    """Minimal read-only Tap stand-in matching broadband_probe's usage."""

    def __init__(self, device_ip):
        self.device_ip = device_ip
        self.payloads: "queue.Queue[bytes]" = queue.Queue()
        self.connected = False
        self.disconnected = False

    def connect(self, tap_name):
        self.connected = True
        return True

    def read(self, timeout_ms=100):
        try:
            return self.payloads.get(timeout=timeout_ms / 1000)
        except queue.Empty:
            return None

    def disconnect(self):
        self.disconnected = True


class BroadbandStreamReaderTests(unittest.TestCase):
    def test_reader_fills_buffer_and_never_sends(self):
        tap = FakeTap("fake")
        for i in range(3):
            tap.payloads.put(frame(i, i, (i, i + 1)).SerializeToString())
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=20, max_channels=2)
        reader = BroadbandStreamReader("fake", buf, tap_factory=lambda ip: tap)
        reader.start()
        deadline = time.monotonic() + 2
        while buf.snapshot().frames < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        reader.stop()
        self.assertEqual(buf.snapshot().frames, 3)
        self.assertTrue(tap.disconnected)
        self.assertFalse(hasattr(tap, "sent"))  # read-only path only

    def test_reader_reports_connect_failure(self):
        class FailingTap(FakeTap):
            def connect(self, tap_name):
                return False

        tap = FailingTap("fake")
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=20)
        reader = BroadbandStreamReader("fake", buf, tap_factory=lambda ip: tap)
        reader.start()
        deadline = time.monotonic() + 2
        while reader.error is None and time.monotonic() < deadline:
            time.sleep(0.01)
        reader.stop()
        self.assertIsNotNone(reader.error)
        self.assertIn("broadband_out", reader.error)


@unittest.skipUnless(_GUI_DEPS, "PySide6 and pyqtgraph are required for the waveform window")
class WaveformWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _prefilled_reader(num_channels, samples=20):
        def fake_reader(ip, buf):
            reader = BroadbandStreamReader(ip, buf, tap_factory=None)
            reader.connected = True
            for i in range(samples):
                values = tuple((i + ch) % 17 for ch in range(num_channels))
                buf.add_frame(frame(i, i, values))
            reader.start = lambda: None
            reader.stop = lambda timeout=1.0: None
            return reader

        return fake_reader

    @staticmethod
    def _send_wheel(window, dy, shift=False):
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtCore import Qt, QPoint, QPointF

        mods = Qt.ShiftModifier if shift else Qt.NoModifier
        viewport = window.plot_widget.viewport()
        event = QWheelEvent(
            QPointF(10, 10), viewport.mapToGlobal(QPoint(10, 10)),
            QPoint(0, 0), QPoint(0, dy), Qt.NoButton, mods,
            Qt.NoScrollPhase, False,
        )
        QApplication.instance().sendEvent(viewport, event)

    def test_window_plots_injected_frames_read_only(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(2),
        )
        window._refresh()
        self.assertEqual(len(window._curves), 2)
        xs, _ = window._curves[0].getData()
        self.assertEqual(len(xs), 20)
        self.assertIn("read-only", window.status.text())
        window.close()

    def test_window_uses_the_shared_app_icon(self):
        # The icon.svg asset must load and be set on the window; a packaging
        # slip that drops the asset would surface here rather than silently.
        from stateful_decode_and_sync.appicon import ICON_PATH, load_app_icon

        self.assertTrue(ICON_PATH.is_file(), f"missing icon asset: {ICON_PATH}")
        self.assertFalse(load_app_icon().isNull())
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(2),
        )
        self.assertFalse(window.windowIcon().isNull())
        window.close()

    def test_plots_use_fixed_scale_not_autorange(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            y_full_scale=500.0, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        plot = window._plots[0]
        # Auto-ranging is off on both axes; the y-window is the fixed full-scale.
        self.assertFalse(plot.vb.autoRangeEnabled()[0])
        self.assertFalse(plot.vb.autoRangeEnabled()[1])
        (y_lo, y_hi) = plot.vb.viewRange()[1]
        self.assertAlmostEqual(y_lo, -500.0)
        self.assertAlmostEqual(y_hi, 500.0)
        window.close()

    def test_per_channel_and_global_gain_scale_the_y_window(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            y_full_scale=500.0, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        window.full_scale_spin.setValue(500.0)
        window.gain_spin.setValue(2.0)      # global: half = 500/2 = 250
        window.gain_edit.setText("0:5")     # ch0 override: half = 500/5 = 100
        window._apply_scale()
        self.assertEqual(window._plots[0].vb.viewRange()[1], [-100.0, 100.0])
        self.assertEqual(window._plots[1].vb.viewRange()[1], [-250.0, 250.0])
        window.close()

    def test_shared_timescale_sets_x_window_on_all_axes(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.02, expected_sample_rate_hz=20_000,
            columns=2, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        window.timescale_spin.setValue(0.005)
        window._apply_scale()
        for plot in window._plots.values():
            self.assertEqual(plot.vb.viewRange()[0], [-0.005, 0.0])
        window.close()

    def test_plain_wheel_changes_vertical_gain(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.02, expected_sample_rate_hz=20_000,
            y_full_scale=500.0, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        self.assertEqual(window._global_gain, 1.0)
        self.assertEqual(window._plots[0].vb.viewRange()[1], [-500.0, 500.0])

        self._send_wheel(window, 120)  # wheel up -> magnify (gain up, y-window shrinks)
        self.assertGreater(window._global_gain, 1.0)
        y_hi = window._plots[0].vb.viewRange()[1][1]
        self.assertLess(y_hi, 500.0)
        self.assertAlmostEqual(window.gain_spin.value(), window._global_gain)  # panel synced

        self._send_wheel(window, -120)  # wheel down -> back toward 1.0
        self.assertAlmostEqual(window._global_gain, 1.0)
        window.close()

    def test_shift_wheel_changes_shared_timescale(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.02, expected_sample_rate_hz=20_000,
            timescale_s=0.02, columns=2, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        self.assertEqual(window._timescale_s, 0.02)

        self._send_wheel(window, -120, shift=True)  # shorter window
        self.assertLess(window._timescale_s, 0.02)
        for plot in window._plots.values():  # shared across all axes
            self.assertEqual(plot.vb.viewRange()[0], [-window._timescale_s, 0.0])
        self.assertAlmostEqual(window.timescale_spin.value(), window._timescale_s)

        # Plain wheel must not have touched the timescale.
        gain_before = window._global_gain
        self._send_wheel(window, 120, shift=True)  # longer, clamped to duration
        self.assertLessEqual(window._timescale_s, 0.02)
        self.assertEqual(window._global_gain, gain_before)
        window.close()

    def test_shift_wheel_timescale_clamps_to_buffer_duration(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.02, expected_sample_rate_hz=20_000,
            timescale_s=0.02, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        for _ in range(30):  # many wheel-ups must not exceed the buffered history
            self._send_wheel(window, 120, shift=True)
        self.assertLessEqual(window._timescale_s, 0.02 + 1e-12)
        self.assertEqual(window._timescale_s, window.timescale_spin.maximum())
        window.close()

    def test_invalid_gain_spec_is_reported_and_scale_unchanged(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            y_full_scale=500.0, reader_factory=self._prefilled_reader(4),
        )
        window._refresh()
        before = window._plots[0].vb.viewRange()[1]
        window.gain_edit.setText("0:not-a-number")
        window._apply_scale()
        self.assertEqual(window._plots[0].vb.viewRange()[1], before)  # unchanged
        self.assertIn("invalid", window.scale_note.text())
        window.close()

    def test_set_windows_app_id_is_safe_to_call(self):
        # Best-effort taskbar identity: a no-op off Windows and swallowing any
        # failure on it, so it must never raise on any platform.
        from stateful_decode_and_sync.appicon import set_windows_app_id

        set_windows_app_id()  # default id
        set_windows_app_id("NML.ScienceXYZ.Test")  # custom id

    def test_layout_reconfigures_to_4x8_grid(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(32),
        )
        window._refresh()  # resolves 32 channels, default 1 column
        self.assertEqual(len(window._placements), 32)
        self.assertEqual(max(p.col for p in window._placements), 0)

        window.channel_edit.setText("0-31")
        window.columns_spin.setValue(8)
        window._apply_layout()
        self.assertEqual(len(window._placements), 32)
        self.assertEqual(max(p.row for p in window._placements) + 1, 4)
        self.assertEqual(max(p.col for p in window._placements) + 1, 8)
        self.assertIn("in 8 col(s)", window.status.text())
        window.close()

    def test_channel_selection_and_reorder_apply(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(32),
        )
        window._refresh()
        window.channel_edit.setText("4,2,0")
        window.columns_spin.setValue(3)
        window._apply_layout()
        self.assertEqual([p.channel_id for p in window._placements], [4, 2, 0])
        self.assertEqual([(p.row, p.col) for p in window._placements], [(0, 0), (0, 1), (0, 2)])
        self.assertIn("showing 3/32 ch", window.status.text())
        window.close()

    def test_invalid_spec_is_reported_and_layout_unchanged(self):
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(8),
        )
        window._refresh()
        window.channel_edit.setText("0-3")
        window._apply_layout()
        before = list(window._placements)
        window.channel_edit.setText("0-99")  # out of range
        window._apply_layout()
        self.assertEqual(window._placements, before)  # unchanged on invalid input
        self.assertIn("invalid", window.layout_note.text())
        window.close()

    def test_frame_ticks_reuse_curve_objects_without_rebuilding(self):
        # A steady stream must not delete/reconstruct Qt graphics objects each
        # tick: the same curve instances persist and only their data changes.
        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(4),
        )
        window._refresh()  # first tick builds the curves
        curves_before = dict(window._curves)
        axis_before = window._time_axis
        rebuilds = 0
        original_rebuild = window._rebuild_plots

        def counting_rebuild():
            nonlocal rebuilds
            rebuilds += 1
            original_rebuild()

        window._rebuild_plots = counting_rebuild
        for _ in range(5):
            window._refresh()
        # No rebuild on plain frame ticks, same curve objects, cached time axis.
        self.assertEqual(rebuilds, 0)
        self.assertEqual({id(c) for c in window._curves.values()},
                         {id(c) for c in curves_before.values()})
        self.assertIs(window._time_axis, axis_before)
        window.close()

    def test_reconnect_builds_fresh_reader_and_rearms_timer(self):
        made = []

        def counting_factory(ip, buf):
            reader = BroadbandStreamReader(ip, buf, tap_factory=None)
            reader.connected = True
            for i in range(20):
                buf.add_frame(frame(i, i, tuple((i + ch) % 17 for ch in range(4))))
            reader.start = lambda: None
            reader.stop = lambda timeout=1.0: None
            made.append(reader)
            return reader

        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=counting_factory,
        )
        window._refresh()
        first_reader = window.reader
        self.assertEqual(len(made), 1)

        # Simulate a stream error that stops the timer, as _refresh would.
        window.timer.stop()

        window._reconnect()
        self.assertEqual(len(made), 2)
        self.assertIsNot(window.reader, first_reader)  # fresh instance
        self.assertTrue(window.timer.isActive())       # timer re-armed
        self.assertTrue(window.reconnect_button.isEnabled())
        window.close()

    def test_layout_change_and_resize_share_the_reflow_path(self):
        # Changing the arrangement must fire the same reflow the resize event
        # uses, so channels fit the screen without a manual window drag.
        from PySide6.QtGui import QResizeEvent
        from PySide6.QtCore import QSize

        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=self._prefilled_reader(32),
        )
        window._refresh()

        calls = {"n": 0}
        original = window._relayout_plots

        def counting_relayout():
            calls["n"] += 1
            original()

        window._relayout_plots = counting_relayout

        # A layout change routes through the shared reflow.
        window.channel_edit.setText("0-31")
        window.columns_spin.setValue(8)
        window._apply_layout()
        self.assertGreaterEqual(calls["n"], 1)

        # A window resize routes through the same reflow.
        before = calls["n"]
        window.resizeEvent(QResizeEvent(QSize(900, 600), QSize(1100, 720)))
        self.assertGreater(calls["n"], before)
        window.close()

    def test_reconnect_recovers_from_reader_error(self):
        # After a connect error the timer stops and the error shows; Reconnect
        # must clear it by swapping in a healthy reader.
        class ErroringReader(BroadbandStreamReader):
            pass

        state = {"first": True}

        def flaky_factory(ip, buf):
            reader = ErroringReader(ip, buf, tap_factory=None)
            if state["first"]:
                reader.error = "failed to connect to producer tap 'broadband_out'"
                state["first"] = False
            else:
                reader.connected = True
                for i in range(20):
                    buf.add_frame(frame(i, i, tuple((i + ch) % 17 for ch in range(4))))
            reader.start = lambda: None
            reader.stop = lambda timeout=1.0: None
            return reader

        window = create_waveform_window(
            "192.0.2.1", duration_s=0.01, expected_sample_rate_hz=20_000,
            reader_factory=flaky_factory,
        )
        window._refresh()  # observes the error, stops the timer
        self.assertIn("failed to connect", window.status.text())
        self.assertFalse(window.timer.isActive())

        window._reconnect()
        self.assertIsNone(window.reader.error)
        self.assertTrue(window.timer.isActive())
        self.assertIn("read-only", window.status.text())
        window.close()


if __name__ == "__main__":
    unittest.main()
