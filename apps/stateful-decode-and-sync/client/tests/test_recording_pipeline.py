import copy
import json
import tempfile
import unittest
from pathlib import Path

import h5py
import numpy as np
from synapse.api.datatype_pb2 import BroadbandFrame
from stateful_decode_and_sync import proto
from stateful_decode_and_sync.calibration_task import make_profile
from stateful_decode_and_sync.recording_analysis import read_recording, build_epochs, fit_baseline, gpio_edges, validate_journal


def fixture(path, *, gap=None, missing_event=None, bad_timestamp=False):
    profile = make_profile()
    dtype = np.dtype([("host_receive_time_ns", "<u8"), ("payload", h5py.vlen_dtype(np.dtype("uint8")))])
    rows = np.empty(1300, dtype=dtype)
    for i in range(1300):
        frame = BroadbandFrame(sequence_number=i, timestamp_ns=1_000_000_000 + i * 10_000_000,
                               unix_timestamp_ns=9_000_000_000 + i * 10_000_000, sample_rate_hz=100)
        amplitude = (i // 100) % 4 + 1
        frame.frame_data.extend([(-1 if i % 2 else 1) * amplitude, i % 7, int(i >= 500)])
        frame.channel_ranges.add(type=0, count=2)
        frame.channel_ranges.add(type=1, count=1, channel_ids=[0])
        rows[i] = (5_000_000_000 + i, np.frombuffer(frame.SerializeToString(), dtype="u1"))
    if gap is not None:
        rows = np.delete(rows, gap)
    events = []
    for run in range(1, 4):
        for n in range(4):
            ordinal = (run - 1) * 4 + n + 1
            sequence = (run - 1) * 400 + n * 100 + 20
            event = proto.TaskTransitionEvent(protocol_version=1, definition_id=profile["definition"]["definition_id"],
                definition_revision=1, definition_hash=profile["definition_hash"], app_session_id="fixture",
                run_sequence=run, event_sequence=ordinal, transition_sequence=n + 1,
                event_kind=proto.TASK_EVENT_START if n == 0 else proto.TASK_EVENT_TRANSITION,
                previous_state_id=n, current_state_id=n + 1, transition_id=n,
                trigger_kind=proto.TASK_TRIGGER_START_COMMAND if n == 0 else proto.TASK_TRIGGER_EXTERNAL_EVENT,
                request_id=f"request-{ordinal}")
            event.effective_frame.source_id = "rhd2132"
            event.effective_frame.sequence_number = sequence
            event.effective_frame.timestamp_ns = 1_000_000_000 + sequence * 10_000_000 + (1 if bad_timestamp else 0)
            if ordinal != missing_event:
                events.append((7_000_000_000 + ordinal, np.frombuffer(event.SerializeToString(), dtype="u1")))
    with h5py.File(path, "x") as output:
        output.attrs["raw_schema"] = "sciencexyz.raw_taps.v1"
        output.attrs["metadata_json"] = "{}"
        output.attrs["provenance_reference_source_id"] = "rhd2132"
        output.attrs["recording_status_json"] = json.dumps({"state": "stopped", "raw_reference_messages": len(rows),
            "raw_task_messages": len(events), "queue_loss_known": False, "tail_complete": False})
        output.create_dataset("raw_broadband", data=rows)
        output.create_dataset("raw_task", data=np.asarray(events, dtype=dtype))
        output.create_dataset("events", data=np.asarray([(0,), (1,)], dtype=[("kind", "u1")]))
    return profile


class RecordingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_runs_fit_with_whole_run_holdout(self):
        profile = fixture(self.root / "raw.h5")
        data = read_recording(self.root / "raw.h5")
        epochs, runs = build_epochs(data, profile)
        self.assertEqual(len(epochs), 9)
        self.assertTrue(all(e["valid"] for e in epochs))
        result = fit_baseline(data, epochs, self.root, window_ms=200, stride_ms=100, margin_ms=100)
        self.assertEqual(result["holdout_run"], ["fixture", 3])
        self.assertEqual(set(result["train_counts"]), {"rest", "action_a", "action_b"})
        features = np.load(self.root / "features.npz")
        self.assertTrue(np.all(features["frame_bounds"][features["is_test"], 0] >= 820))
        self.assertTrue(np.all(features["frame_bounds"][~features["is_test"], 1] <= 720))
        self.assertEqual(len(gpio_edges(data)), 1)
        self.assertFalse(gpio_edges(data)[0]["across_gap"])

    def test_gap_invalidates_entire_affected_epoch_not_stretched(self):
        profile = fixture(self.root / "raw.h5", gap=50)
        data = read_recording(self.root / "raw.h5")
        epochs, _ = build_epochs(data, profile)
        self.assertFalse(epochs[0]["valid"])
        self.assertIn("gap", epochs[0]["reason"])
        with self.assertRaisesRegex(ValueError, "three complete"):
            fit_baseline(data, epochs, self.root)

    def test_missing_start_invalidates_run(self):
        profile = fixture(self.root / "raw.h5", missing_event=1)
        epochs, _ = build_epochs(read_recording(self.root / "raw.h5"), profile)
        self.assertFalse(any(e["valid"] for e in epochs if e["run_sequence"] == 1))

    def test_missing_middle_event_and_boundary_timestamp_mismatch(self):
        profile = fixture(self.root / "raw.h5", missing_event=2, bad_timestamp=True)
        epochs, _ = build_epochs(read_recording(self.root / "raw.h5"), profile)
        self.assertFalse(any(e["valid"] for e in epochs))
        self.assertIn("event_discontinuity", epochs[0]["reason"])

    def test_definition_mismatch_never_labels(self):
        profile = fixture(self.root / "raw.h5")
        profile["definition_hash"] = "wrong"
        epochs, _ = build_epochs(read_recording(self.root / "raw.h5"), profile)
        self.assertFalse(any(e["valid"] for e in epochs))

    def test_no_task_events_refuses_fitting(self):
        profile = fixture(self.root / "raw.h5")
        with h5py.File(self.root / "raw.h5", "r+") as source:  # test-owned fixture only
            del source["raw_task"]
            source.attrs["recording_status_json"] = json.dumps({"state": "stopped", "raw_reference_messages": 1300, "raw_task_messages": 0})
        data = read_recording(self.root / "raw.h5")
        epochs, _ = build_epochs(data, profile)
        self.assertEqual(epochs, [])
        with self.assertRaisesRegex(ValueError, "three complete"):
            fit_baseline(data, epochs, self.root)

    def test_memory_limit_fails_instead_of_truncating(self):
        fixture(self.root / "raw.h5")
        with self.assertRaisesRegex(ValueError, "memory limit"):
            read_recording(self.root / "raw.h5", 100)

    def test_journal_checks_commits_and_raw_digest(self):
        fixture(self.root / "raw.h5")
        data = read_recording(self.root / "raw.h5")
        records = [{"kind": "task_committed", "event": e} for e in data["events"]]
        records.append({"kind": "recording_stopped", "local_stop_ok": True, "raw_sha256": data["sha256"]})
        journal = self.root / "journal.ndjson"
        def write():
            journal.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        write()
        self.assertTrue(validate_journal(data, journal)["valid"])
        records[0]["event"] = copy.deepcopy(records[0]["event"])
        records[0]["event"]["effective_frame"]["timestamp_ns"] = "123"
        write()
        self.assertFalse(validate_journal(data, journal)["valid"])
        records.pop(0)
        records[-1]["raw_sha256"] = "wrong"
        write()
        self.assertIn("journal_raw_file_or_stop_mismatch", validate_journal(data, journal)["failures"])

    def test_mixed_run_rates_refuse_training(self):
        profile = fixture(self.root / "raw.h5")
        data = read_recording(self.root / "raw.h5")
        epochs, _ = build_epochs(data, profile)
        data["frames"][820:, 5] = 200
        with self.assertRaisesRegex(ValueError, "single sample rate"):
            fit_baseline(data, epochs, self.root)


if __name__ == "__main__":
    unittest.main()
