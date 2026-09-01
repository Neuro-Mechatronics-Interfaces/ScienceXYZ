import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze_alignment import analyze  # noqa: E402


def observation(sequence: int, *, error: int = 20, epsilon: int = 100, quality: str = "locked") -> dict:
    return {
        "edge_id": f"edge-{sequence}",
        "reference_time_ns": 1_000_000 + sequence * 1_000_000,
        "observed_time_ns": 1_000_000 + sequence * 1_000_000 + error,
        "epsilon_ns": epsilon,
        "source_tick": 100_000 + sequence * 1_000,
        "source_sequence": sequence,
        "batch_sequence": sequence,
        "host_receive_time_ns": 1_005_000 + sequence * 1_000_000,
        "source_acquisition_time_ns": 1_000_000 + sequence * 1_000_000,
        "batch_latency_ns": 5_000,
        "clock_model_id": "clock-model-1",
        "boot_session_id": "boot-1",
        "quality": quality,
        "rtt_ns": 2_000,
        "sync_dispersion_ns": 500,
    }


def trial(rate: int, batch: int, *, quality: str = "locked") -> dict:
    return {
        "trial_id": f"loopback-{rate}-{batch}",
        "mode": "loopback",
        "source_id": f"sim-{rate}",
        "reference_source_id": "omnetics-reference",
        "sample_rate_hz": rate,
        "batch_samples": batch,
        "expected_sample_rate_hz": rate,
        "observed_sample_rate_hz": rate,
        "provenance": {
            "config_hash": "sha256:config",
            "software_revision": "git:clean",
            "firmware_revision": "firmware:bench",
            "reference_peripheral_id": "resolved-live-id",
        },
        "continuity": {
            "unexplained_missing_batches": 0,
            "unexplained_missing_samples": 0,
            "duplicates": 0,
            "reordered": 0,
            "parse_errors": 0,
        },
        "observations": [observation(1, quality=quality), observation(2, quality=quality)],
    }


class AlignmentAcceptanceTests(unittest.TestCase):
    def test_passes_complete_matrix_and_reports_metrics(self) -> None:
        document = {"trials": [trial(1000, 32), trial(1500, 48)]}
        report = analyze(document, ((1000.0, 32), (1500.0, 48)), ("loopback",))
        self.assertTrue(report["pass"])
        self.assertEqual(report["summary"]["quality_counts"]["locked"], 4)
        self.assertEqual(report["trials"][0]["metrics"]["absolute_error_ns"]["p95"], 20.0)
        self.assertEqual(report["trials"][0]["metrics"]["bound_coverage"], 1.0)

    def test_fails_unexplained_loss_and_out_of_bound_edge(self) -> None:
        bad = trial(1000, 32)
        bad["continuity"]["unexplained_missing_batches"] = 1
        bad["observations"][1]["epsilon_ns"] = 10
        report = analyze({"trials": [bad]}, ((1000.0, 32),), ("loopback",))
        self.assertFalse(report["pass"])
        self.assertEqual(report["trials"][0]["metrics"]["uncovered_observations"], 1)
        self.assertTrue(any("unexplained_missing_batches" in item for item in report["failures"]))
        self.assertTrue(any("epsilon bound" in item for item in report["failures"]))

    def test_reports_degraded_but_rejects_unbounded_and_missing_matrix(self) -> None:
        degraded = trial(1000, 32, quality="degraded")
        unbounded = copy.deepcopy(trial(1500, 48, quality="unbounded"))
        report = analyze({"trials": [degraded, unbounded]}, required_modes=("loopback", "lan"))
        self.assertFalse(report["pass"])
        self.assertEqual(report["summary"]["quality_counts"]["degraded"], 2)
        self.assertEqual(report["summary"]["quality_counts"]["unbounded"], 2)
        self.assertEqual(len(report["missing_rate_batch_pairs"]), 3)
        self.assertTrue(any("unbounded synchronization" in item for item in report["failures"]))


if __name__ == "__main__":
    unittest.main()
