import os
import queue
import time
import unittest
import numpy as np

from synapse.api.channel_pb2 import ChannelType
from synapse.api.datatype_pb2 import BroadbandFrame

from scifi2_hub_manager.waveform import (
    BroadbandStreamReader,
    WaveformBuffer,
    grid_placements,
    parse_channel_spec,
    parse_gain_spec,
    observed_sample_rate_hz,
    broadband_layouts_from_config_path,
    decimation_factor_from_params,
    _info_shows_app_running,
)

try:
    from PySide6.QtWidgets import QApplication

    # Importing the module first pins PYQTGRAPH_QT_LIB=PySide6 before pyqtgraph
    # is imported, so its widgets are real PySide6 widgets.
    from scifi2_hub_manager.waveform import create_waveform_window

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
    def test_sync_edges_use_metadata_outside_display_cap(self):
        buf = WaveformBuffer(duration_s=1, expected_sample_rate_hz=4, max_channels=1)
        def add(seq, ts, a, b):
            f = frame(seq, ts, [7, a, b])
            f.ClearField("channel_ranges")
            f.channel_ranges.add(type=ChannelType.ELECTRODE, count=1)
            f.channel_ranges.add(type=ChannelType.GPIO, count=2, channel_ids=[1, 0])
            buf.add_frame(f)
        add(0, 100, 0, 0)
        add(1, 150, 1, 0)
        add(2, 900, 0, 1)
        snap = buf.snapshot()
        self.assertEqual(snap.timestamps.tolist(), [100, 150, 900])
        self.assertEqual(snap.sync_edges.tolist(), [
            [False, False, True], [False, False, False],
            [False, True, False], [False, False, True]])
        add(4, 950, 1, 0)  # missing frame: no inferred edge
        add(5, 940, 0, 1)  # source clock regression does not change sample continuity
        buf.reset_sync_continuity()
        add(6, 1000, 1, 0)
        self.assertFalse(buf.snapshot().sync_edges[:, [-3, -1]].any())
        self.assertTrue(buf.snapshot().sync_edges[:, -2].any())
        self.assertEqual(buf.snapshot().timestamps.tolist(), [900, 950, 940, 1000])

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

    def test_nominal_spacing_ignores_batched_and_regressing_source_times(self):
        buf = WaveformBuffer(duration_s=1, expected_sample_rate_hz=4)
        for seq, timestamp in enumerate([100, 100, 500000000, 10, 10, 999999999]):
            buf.add_frame(frame(seq, timestamp, [seq], sample_rate=20000))
        snap = buf.snapshot()  # ring wrap must preserve uniform spacing
        np.testing.assert_allclose(np.diff(snap.nominal_times), 1 / 20000)
        self.assertEqual(snap.timestamps.tolist(), [500000000, 10, 10, 999999999])
        self.assertEqual(snap.connections.tolist(), [True, True, True, False])

    def test_nominal_gaps_resets_and_rate_changes_break_lines(self):
        buf = WaveformBuffer()
        for seq, rate in [(10, 20000), (11, 20000), (14, 20000), (0, 20000), (1, 10000), (2, 10000)]:
            buf.add_frame(frame(seq, 100, [seq], sample_rate=rate))
        snap = buf.snapshot()
        np.testing.assert_allclose(np.diff(snap.nominal_times), [1/20000, 3/20000, 1/20000, 1/10000, 1/10000])
        self.assertEqual(snap.connections.tolist(), [True, False, False, False, True, False])

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
        # Establish a +1 stride first, then a delta of 3 is a real gap of 2.
        buf.add_frame(frame(100, 0, (1,)))
        buf.add_frame(frame(101, 1, (2,)))  # +1: learns stride 1
        buf.add_frame(frame(104, 2, (3,)))  # gap of 2 (delta 3 at stride 1)
        self.assertFalse(buf.add_raw(b"not-a-frame"))
        self.assertFalse(buf.add_frame(frame(105, 3, ())))  # empty payload
        snap = buf.snapshot()
        self.assertEqual(snap.missing_sequences, 2)
        self.assertEqual(snap.parse_errors, 2)
        self.assertEqual(snap.sequence_last, 104)

    def test_decimated_stream_stride_is_contiguous(self):
        # broadband_out carries the FIR-center source sequence, which advances by
        # the decimation factor (10). A uniform stride-10 stream must read as
        # contiguous with no missing frames; a stride-20 step is one dropped frame.
        buf = WaveformBuffer(duration_s=1.0, expected_sample_rate_hz=2000, max_channels=1)
        for seq in (165, 175, 185, 195):
            buf.add_frame(frame(seq, seq, (seq,), sample_rate=2000))
        snap = buf.snapshot()
        self.assertEqual(snap.missing_sequences, 0)
        # Every step is one decimated frame apart at 2 kHz.
        np.testing.assert_allclose(np.diff(snap.nominal_times), 1 / 2000)
        # Each sample connects to its successor; the newest has none after it.
        self.assertEqual(snap.connections.tolist(), [True, True, True, False])
        # A single dropped decimated frame: 195 -> 215 skips 205.
        buf.add_frame(frame(215, 215, (215,), sample_rate=2000))
        snap = buf.snapshot()
        self.assertEqual(snap.missing_sequences, 1)
        self.assertFalse(snap.connections.tolist()[-1])


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


class InfoGateParseTests(unittest.TestCase):
    def test_matches_app_section_running_true(self):
        info = ("Applications:\n  scifi2-hub-manager\n    Running: True\n"
                "  other-app\n    Running: False\n")
        self.assertTrue(_info_shows_app_running(info))

    def test_stopped_app_is_false_even_if_another_app_runs(self):
        info = ("scifi2-hub-manager\n    Running: False\n"
                "  other-app\n    Running: True\n")
        self.assertFalse(_info_shows_app_running(info))

    def test_absent_app_is_false(self):
        self.assertFalse(_info_shows_app_running("Status: Running\n"))


class ObservedRateTests(unittest.TestCase):
    def test_estimates_rate_from_timestamps(self):
        # 2 kHz -> 500000 ns between samples.
        ts = np.arange(5, dtype=np.int64) * 500_000
        self.assertAlmostEqual(observed_sample_rate_hz(ts), 2000.0, places=3)

    def test_degenerate_spans_return_none(self):
        self.assertIsNone(observed_sample_rate_hz(np.array([], dtype=np.int64)))
        self.assertIsNone(observed_sample_rate_hz(np.array([5], dtype=np.int64)))
        self.assertIsNone(observed_sample_rate_hz(np.array([10, 10], dtype=np.int64)))
        self.assertIsNone(observed_sample_rate_hz(np.array([10, 5], dtype=np.int64)))


class ConfigLayoutTests(unittest.TestCase):
    def _write(self, obj):
        import json, tempfile, os
        path = os.path.join(tempfile.mkdtemp(prefix="wf-cfg-"), "device.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle)
        return path

    def test_parses_multiple_sources(self):
        cfg = {"nodes": [
            {"type": "kBroadbandSource", "id": 1, "broadbandSource": {
                "peripheral_id": 200, "sample_rate_hz": 20000,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(4)]}}}},
            {"type": "kBroadbandSource", "id": 5, "broadbandSource": {
                "peripheral_id": 201, "sample_rate_hz": 2000,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(2)]}}}},
            {"type": "kApplication", "id": 2},
        ]}
        layouts = broadband_layouts_from_config_path(self._write(cfg))
        self.assertEqual([l.node_id for l in layouts], [1, 5])
        self.assertEqual([l.n_ch for l in layouts], [4, 2])
        self.assertEqual([l.peripheral_id for l in layouts], [200, 201])
        self.assertEqual([l.sample_rate_hz for l in layouts], [20000, 2000])

    def test_missing_or_bad_path_is_empty(self):
        self.assertEqual(broadband_layouts_from_config_path(""), [])
        self.assertEqual(broadband_layouts_from_config_path("/no/such/file.json"), [])


class DecimationFactorTests(unittest.TestCase):
    def test_shipped_params_pick_factor_10(self):
        # 20 kHz, 200 ms window, 20 ms stride, top band 800 Hz, guard 1.25 -> 10.
        params = {"window_ms": 200.0, "stride_ms": 20.0, "decimation_guard_ratio": 1.25,
                  "frequency_bands_hz": [[0.0, 400.0], [400.0, 800.0]]}
        self.assertEqual(decimation_factor_from_params(params, 20000), 10)

    def test_top_band_1000_needs_smaller_factor(self):
        # top 1000 Hz, guard 1.25 -> guarded nyquist 1250 -> factor 8 (2.5 kHz).
        params = {"window_ms": 200.0, "stride_ms": 20.0, "decimation_guard_ratio": 1.25,
                  "frequency_bands_hz": [[0.0, 1000.0]]}
        self.assertEqual(decimation_factor_from_params(params, 20000), 8)

    def test_no_bands_disables_decimation(self):
        self.assertEqual(decimation_factor_from_params({"window_ms": 200.0, "stride_ms": 20.0}, 20000), 1)
        self.assertEqual(decimation_factor_from_params({}, 20000), 1)


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
    def test_coincident_gpio_edges_use_opposing_triangles(self):
        window = create_waveform_window("192.0.2.1", reader_factory=self._prefilled_reader(2))
        try:
            window.buffer.reset_sync_continuity()
            for seq, level in [(100, 0), (101, 1)]:
                f = frame(seq, 1000, [3, 4, level, level])
                f.ClearField("channel_ranges")
                f.channel_ranges.add(type=ChannelType.ELECTRODE, count=2)
                f.channel_ranges.add(type=ChannelType.GPIO, count=2, channel_ids=[0, 1])
                window.buffer.add_frame(f)
            window.sync_toggle.setChecked(True)
            window._refresh()
            for curves in window._sync_curves.values():
                x0, y0 = curves[0].getData()
                x1, y1 = curves[2].getData()
                np.testing.assert_array_equal(x0, x1)
                self.assertLess(y0[0], 0)
                self.assertGreater(y1[0], 0)
                self.assertEqual(curves[0].opts["symbol"], "t1")
                self.assertEqual(curves[2].opts["symbol"], "t")
                self.assertIsNone(curves[0].opts["pen"])
        finally:
            window.close()

    def test_sync_toggle_marks_every_subplot_at_nominal_time(self):
        window = create_waveform_window("192.0.2.1", reader_factory=self._prefilled_reader(2))
        try:
            window.buffer.reset_sync_continuity()
            for seq, ts, level in ((100, 1000000000, 0), (101, 1000000000, 1), (102, 9000000000, 1)):
                f = frame(seq, ts, [3, 4, level])
                f.ClearField("channel_ranges")
                f.channel_ranges.add(type=ChannelType.ELECTRODE, count=2)
                f.channel_ranges.add(type=ChannelType.GPIO, count=1, channel_ids=[0])
                window.buffer.add_frame(f)
            window.sync_toggle.setChecked(True)
            window._refresh()
            for curves in window._sync_curves.values():
                self.assertTrue(curves[0].isVisible())
                np.testing.assert_allclose(curves[0].getData()[0], [-1/20000])
            for curve in window._curves.values():
                np.testing.assert_allclose(np.diff(curve.getData()[0][-3:]), 1/20000)
            window.sync_toggle.setChecked(False)
            self.assertTrue(all(not c.isVisible() for cs in window._sync_curves.values() for c in cs))
        finally:
            window.close()

    def test_device_panel_builds_argv_from_configurable_command(self):
        window = create_waveform_window(
            "10.0.0.9", device_config="config/dev.json",
            synapsectl_command="wsl synapsectl",
            reader_factory=self._prefilled_reader(2))
        try:
            # Multi-token command reaches a WSL install; each token is one argv entry.
            self.assertEqual(
                window._synapsectl_argv("start", "config/dev.json"),
                ["wsl", "synapsectl", "-u", "10.0.0.9", "start", "config/dev.json"])
            # Empty config -> argumentless start (restart already-configured device).
            window.device_config_edit.setText("")
            window._copy_start_line()
            self.assertEqual(QApplication.clipboard().text(),
                             "wsl synapsectl -u 10.0.0.9 start")
        finally:
            window.close()

    def test_device_start_button_toggles_to_stop_on_success(self):
        window = create_waveform_window(
            "10.0.0.9", reader_factory=self._prefilled_reader(2))
        try:
            self.assertEqual(window.run_start_button.text(), "Run: start device")
            # Simulate a successful start completion (no real synapsectl spawned).
            window._on_synapsectl_done("start", 0, "installed successfully", None)
            self.assertTrue(window._device_started)
            self.assertEqual(window.run_start_button.text(), "Run: stop device")
            window._on_synapsectl_done("stop", 0, "stopped", None)
            self.assertFalse(window._device_started)
            self.assertEqual(window.run_start_button.text(), "Run: start device")
        finally:
            window.close()

    def test_settings_round_trip_persists_layout_scale_and_device(self):
        import tempfile
        ini = os.path.join(tempfile.mkdtemp(prefix="waveform-rt-"), "s.ini")
        # First window: change fields, then close to save.
        w1 = create_waveform_window(
            "192.0.2.1", settings_path=ini, reader_factory=self._prefilled_reader(4))
        try:
            w1.device_uri_edit.setText("10.1.2.3")
            w1.device_config_edit.setText("config/persist.json")
            w1.synapsectl_edit.setText("wsl synapsectl")
            w1.columns_spin.setValue(2)
            w1.channel_edit.setText("0-3")
            w1.full_scale_spin.setValue(250.0)
            w1.gain_spin.setValue(3.0)
            w1.gain_edit.setText("0:2")
            w1.timescale_spin.setValue(0.5)
            w1.sync_toggle.setChecked(True)
        finally:
            w1.close()  # triggers _save_settings

        # Second window with no CLI overrides restores every stored field.
        w2 = create_waveform_window(
            "192.0.2.9", settings_path=ini, reader_factory=self._prefilled_reader(4))
        try:
            self.assertEqual(w2.device_uri_edit.text(), "10.1.2.3")
            self.assertEqual(w2.device_config_edit.text(), "config/persist.json")
            self.assertEqual(w2.synapsectl_edit.text(), "wsl synapsectl")
            self.assertEqual(w2.columns_spin.value(), 2)
            self.assertEqual(w2.channel_edit.text(), "0-3")
            self.assertEqual(w2.full_scale_spin.value(), 250.0)
            self.assertEqual(w2.gain_spin.value(), 3.0)
            self.assertEqual(w2.gain_edit.text(), "0:2")
            self.assertAlmostEqual(w2.timescale_spin.value(), 0.5, places=4)
            self.assertTrue(w2.sync_toggle.isChecked())
            # Internal state reflects the restored scale (frame-independent).
            self.assertEqual(w2._full_scale, 250.0)
            self.assertEqual(w2._global_gain, 3.0)
            self.assertEqual(w2._channel_gains, {0: 2.0})
            self.assertEqual(w2._active_columns, 2)
        finally:
            w2.close()

    def test_source_strip_builds_tabs_and_reports_expected_rate(self):
        import json, tempfile, os
        cfg = {"nodes": [
            {"type": "kBroadbandSource", "id": 1, "broadbandSource": {
                "peripheral_id": 200, "sample_rate_hz": 2000,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(4)]}}}},
            {"type": "kBroadbandSource", "id": 5, "broadbandSource": {
                "peripheral_id": 201, "sample_rate_hz": 500,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(4)]}}}},
        ]}
        path = os.path.join(tempfile.mkdtemp(prefix="wf-strip-"), "device.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle)
        window = create_waveform_window(
            "192.0.2.1", device_config=path, reader_factory=self._prefilled_reader(4))
        try:
            self.assertEqual(window.source_tabs.count(), 2)
            self.assertEqual(window._selected_layout().node_id, 1)
            self.assertEqual(window._expected_rate_hz(), 2000.0)
            window.source_tabs.setCurrentIndex(1)
            self.assertEqual(window._selected_layout().node_id, 5)
            self.assertEqual(window._expected_rate_hz(), 500.0)
            self.assertIn("peripheral 201", window.source_meta_label.text())
        finally:
            window.close()

    def test_expected_rate_is_decimated_when_app_params_present(self):
        import json, tempfile, os
        cfg = {"nodes": [
            {"type": "kBroadbandSource", "id": 1, "broadbandSource": {
                "peripheral_id": 200, "sample_rate_hz": 20000,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(4)]}}}},
            {"type": "kApplication", "id": 2, "application": {"parameters": {
                "window_ms": 200.0, "stride_ms": 20.0, "decimation_guard_ratio": 1.25,
                "frequency_bands_hz": [[0.0, 400.0], [400.0, 800.0]]}}},
        ]}
        path = os.path.join(tempfile.mkdtemp(prefix="wf-dec-"), "device.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle)
        window = create_waveform_window(
            "192.0.2.1", device_config=path, reader_factory=self._prefilled_reader(4))
        try:
            # Source is 20 kHz but broadband_out is decimated by 10 -> expect 2 kHz.
            self.assertEqual(window._expected_rate_hz(), 2000.0)
            self.assertIn("broadband_out 2000 Hz", window.source_meta_label.text())
        finally:
            window.close()

    def test_no_config_has_no_source_strip(self):
        window = create_waveform_window(
            "192.0.2.1", reader_factory=self._prefilled_reader(2))
        try:
            self.assertFalse(hasattr(window, "source_tabs"))
            self.assertIsNone(window._selected_layout())
            self.assertIsNone(window._expected_rate_hz())
        finally:
            window.close()

    def test_explicit_cli_field_overrides_stored_value(self):
        import tempfile
        ini = os.path.join(tempfile.mkdtemp(prefix="waveform-ovr-"), "s.ini")
        w1 = create_waveform_window(
            "192.0.2.1", settings_path=ini, reader_factory=self._prefilled_reader(2))
        w1.device_uri_edit.setText("10.9.9.9")
        w1.close()
        # device_ip is explicit this launch: the CLI value wins over the stored one.
        w2 = create_waveform_window(
            "10.0.0.5", settings_path=ini, explicit_settings={"device_uri"},
            reader_factory=self._prefilled_reader(2))
        try:
            self.assertEqual(w2.device_uri_edit.text(), "10.0.0.5")
        finally:
            w2.close()

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # A fresh per-test QSettings dir so _save_settings on window close neither
        # touches a developer's real INI nor leaks state into the next test. Each
        # test starts from an empty INI, so constructor-seeded values stand unless
        # the test itself saves and reopens (the round-trip tests use their own).
        import tempfile
        from PySide6.QtCore import QSettings
        self._settings_dir = tempfile.mkdtemp(prefix="waveform-settings-")
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self._settings_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._settings_dir, ignore_errors=True)

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
        from scifi2_hub_manager.appicon import ICON_PATH, load_app_icon

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
        from scifi2_hub_manager.appicon import set_windows_app_id

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
