"""Passive-mode bridge: host-clock Cognescent data.hdf5, no device round trip.

The SciFi-2 is a broadband source only. The bridge records broadband from the
tap (read here from a fake tap that yields real BroadbandFrame bytes) plus every
browser event as a host-stamped /devices/0 annotation, into one Cognescent
data.hdf5. No NdjsonClient/instructor/device call happens. Hardware-free.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np

from scifi2_hub_manager.calibration_task import Journal, make_profile
from scifi2_hub_manager import reactions_bridge as rb


N_CH = 4


def _frame_bytes(seq, unix_ns):
    from synapse.api import datatype_pb2
    f = datatype_pb2.BroadbandFrame()
    f.sequence_number = seq
    f.unix_timestamp_ns = unix_ns
    f.sample_rate_hz = 2000
    # channel_ranges left empty; the reader infers N_CH from frame_data length.
    f.frame_data.extend([seq] * N_CH)  # one row of N_CH integer samples
    return f.SerializeToString()


N_FRAMES = 1000  # > _BATCH_FRAMES, to exercise batching and backpressure


class _FakeTap:
    """Stands in for synapse.client.taps.Tap: yields a fixed set of frames."""
    def __init__(self, uri, verbose=False):
        self._frames = [_frame_bytes(i, 1_700_000_000_000_000_000 + i) for i in range(N_FRAMES)]

    def connect(self, name):
        return True

    def stream(self, timeout_ms=100):
        for raw in self._frames:
            yield raw
        # stream ends (as it would when the tap is disconnected on stop)

    def read(self, timeout_ms=1000):
        return self._frames.pop(0) if self._frames else None

    def disconnect(self):
        self._frames = []


class PassiveBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_passive_records_broadband_and_annotations_without_device(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "reactions-x"
            folder.mkdir()
            journal = Journal(folder / "browser-events.ndjson")
            # A device config with one kBroadbandSource of N_CH electrode channels;
            # the recorder derives channel count/names/config from it (source of
            # truth), not from wire inference.
            config = {"nodes": [{"type": "kBroadbandSource", "id": 1, "broadbandSource": {
                "peripheral_id": 200, "sample_rate_hz": 2000,
                "signal": {"electrode": {"channels": [{"id": i} for i in range(N_CH)]}}}}]}
            recorder = rb.PassiveRecorder("192.0.2.1:647", folder, journal, config=config)

            # Inject the fake tap so no real device is contacted.
            import scifi2_hub_manager.broadband_tap_reader as btr
            original = btr.BroadbandTapReader.connect

            def fake_connect(self):
                self._tap = _FakeTap(self.device_uri)
                return self
            btr.BroadbandTapReader.connect = fake_connect
            try:
                # A client that raises if the bridge ever tries a device call.
                class _NoDevice:
                    def close(self): pass
                    def connect(self): raise AssertionError("passive mode must not contact the device")
                session = rb.BridgeSession(folder, make_profile(), {}, recorder,
                                           client=_NoDevice(), passive=True)

                start = await session.start(
                    {"protocol": "nml-wtf_Gestures_pinky_flexion@2",
                     "metadata": {"session": {"task_name": "Gestures", "subject": "Max"}}})
                self.assertEqual(start["mode"], "passive")
                data_dir = Path(start["session_dir"])
                # Folder name: <date>-<unix>-<shortid>v-<base>@<block>; block is
                # max(our start=1, browser @2) = 2.
                self.assertRegex(data_dir.name,
                                 r"^\d{4}-\d{2}-\d{2}-\d+-[0-9a-f]{8}v-nml-wtf_Gestures_pinky_flexion@2$")
                self.assertEqual(session.block, 2)

                # Two browser annotations, as the page would send them.
                session.recording  # sanity
                for name, timing in (("rest", "instant"), ("transitioning", "start")):
                    self.assertTrue(session._record_annotation(
                        {"time": 1_700_000_000.5, "data": {"type": "browser_event", "name": name,
                                                           "timing": timing, "payload": {"direction": "toActive"}}}))

                # Let the broadband drain task consume all fake frames. stop()
                # ends the stream and awaits the drain task, so no fixed sleep is
                # needed for correctness; a short yield lets some arrive first.
                await asyncio.sleep(0.1)
                stop = await session.stop()
                self.assertEqual(stop["mode"], "passive")
                self.assertIsNone(stop["broadband_error"])  # no QueueFull / drop
                self.assertEqual(stop["next_block"], 3)  # block advanced on stop
                self.assertEqual(session.block, 3)
            finally:
                btr.BroadbandTapReader.connect = original
                journal.close()

            # The Cognescent data.hdf5 has both devices and the metadata.
            with h5py.File(data_dir / "data.hdf5", "r") as f:
                self.assertEqual(f.attrs["filetype"], "Cognescent Data Format Version 0.0")
                self.assertEqual(f["metadata"].attrs["task_name"], "Gestures")
                ann = f["devices/0"]
                self.assertEqual(ann.attrs["uid"], "browser")
                self.assertEqual(ann["timeseries/time"].shape, (2,))
                self.assertEqual(ann["timeseries/name"][1].decode(), "transitioning")
                raw = f["devices/1"]
                self.assertEqual(raw.attrs["uid"], "raw")
                self.assertEqual(raw.attrs["n_ch"], N_CH)
                # Channel count/names/fs/config come from the device config.
                self.assertEqual(raw.attrs["fs"], 2000.0)
                layout = json.loads(raw.attrs["output_layout"])
                self.assertEqual(layout, [f"electrode_{i}" for i in range(N_CH)])
                self.assertIn("kBroadbandSource", raw.attrs["config"])
                # Every frame's row is present -- batching/backpressure lost none.
                self.assertEqual(raw["timeseries/stream"].shape, (N_FRAMES, N_CH))
                # broadband 'time' is the frame unix timestamp (epoch seconds)
                self.assertAlmostEqual(raw["timeseries/time"][0], 1_700_000_000.0, places=3)
                # rows stay in order (sample value == sequence number)
                self.assertEqual(float(raw["timeseries/stream"][0, 0]), 0.0)
                self.assertEqual(float(raw["timeseries/stream"][N_FRAMES - 1, 0]), float(N_FRAMES - 1))


class SessionNegotiationTests(unittest.IsolatedAsyncioTestCase):
    """The 'session' stream block-exchange (browser SessionMetaAdapter)."""

    def _session(self, temp, block=1):
        folder = Path(temp) / "reactions-y"
        folder.mkdir()
        journal = Journal(folder / "browser-events.ndjson")
        recorder = rb.PassiveRecorder("192.0.2.1:647", folder, journal)

        class _NoDevice:
            def close(self): pass
        return rb.BridgeSession(folder, make_profile(), {}, recorder,
                                client=_NoDevice(), passive=True, block=block), journal

    async def test_opening_session_stream_enqueues_our_block(self):
        with tempfile.TemporaryDirectory() as temp:
            session, journal = self._session(temp, block=5)
            try:
                await session.handle({"api_version": "0.12", "api_request": {
                    "request_id": 1, "start_stream_request": {"stream_id": "session", "app_id": "nml-wtf-web"}}})
                # A session stream_batch is queued for the browser with our block.
                self.assertEqual(len(session.outbox), 1)
                batch = session.outbox[0]["stream_batch"]
                self.assertEqual(batch["stream_id"], "session")
                data = batch["session"]["samples"][0]["data"]
                self.assertEqual(data["block"], 5)
                self.assertTrue(data["session_id"])
            finally:
                journal.close()

    async def test_change_parameter_pipeline_session_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            session, journal = self._session(temp, block=1)
            try:
                reply = await session.handle({"api_version": "0.12", "api_request": {
                    "request_id": 2, "change_parameter_request": {
                        "transforms": {"pipeline.session": {"parameters": {"uuid": "\"abc\""}}}}}})
                # No longer rejected as "unsupported TASK request"; success + a
                # session sample queued.
                self.assertTrue(reply["api_response"]["success"])
                self.assertEqual(len(session.outbox), 1)
                self.assertEqual(session.outbox[0]["stream_batch"]["stream_id"], "session")
            finally:
                journal.close()


if __name__ == "__main__":
    unittest.main()
