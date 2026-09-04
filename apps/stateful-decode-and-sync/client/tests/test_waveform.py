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


if __name__ == "__main__":
    unittest.main()
