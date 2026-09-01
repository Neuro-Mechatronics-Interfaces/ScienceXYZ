#!/usr/bin/env python3
"""Validate measured cross-source alignment captures.

The input is a JSON document containing one or more trial records.  This tool
does not estimate a clock or repair a stream: it checks measurements produced
by the host fusion pipeline and makes missing bounds, loss, and provenance
visible in a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


DEFAULT_REQUIRED_PAIRS = ((1000.0, 32), (1500.0, 48), (2000.0, 64), (2500.0, 40))
DEFAULT_REQUIRED_MODES = ("loopback", "lan", "wireless")
REQUIRED_TRIAL_FIELDS = (
    "trial_id",
    "mode",
    "source_id",
    "reference_source_id",
    "sample_rate_hz",
    "batch_samples",
    "expected_sample_rate_hz",
    "observed_sample_rate_hz",
    "provenance",
    "continuity",
    "observations",
)
REQUIRED_OBSERVATION_FIELDS = (
    "edge_id",
    "reference_time_ns",
    "observed_time_ns",
    "epsilon_ns",
    "source_tick",
    "source_sequence",
    "batch_sequence",
    "host_receive_time_ns",
    "source_acquisition_time_ns",
    "batch_latency_ns",
    "clock_model_id",
    "boot_session_id",
    "quality",
    "rtt_ns",
    "sync_dispersion_ns",
)
REQUIRED_PROVENANCE_FIELDS = (
    "config_hash",
    "software_revision",
    "firmware_revision",
    "reference_peripheral_id",
)
REQUIRED_CONTINUITY_FIELDS = (
    "unexplained_missing_batches",
    "unexplained_missing_samples",
    "duplicates",
    "reordered",
    "parse_errors",
)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative_number(value: Any, name: str) -> float:
    result = _number(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _stats(values: list[float]) -> dict[str, float | None]:
    return {
        "median": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "observation_count": 0,
        "absolute_error_ns": _stats([]),
        "epsilon_ns": _stats([]),
        "latency_ns": _stats([]),
        "latency_jitter_peak_to_peak_ns": None,
        "rtt_ns": {"min": None, "median": None, "p95": None, "max": None},
        "sync_dispersion_ns": _stats([]),
        "bound_coverage": None,
        "uncovered_observations": 0,
        "unbounded_observations": 0,
        "quality_counts": {"locked": 0, "degraded": 0, "unbounded": 0},
        "rate_drift_ppm": None,
        "source_sequences_strictly_increasing": True,
        "batch_sequences_strictly_increasing": True,
        "model_ids": [],
    }


def _validate_required_fields(record: dict[str, Any], fields: tuple[str, ...], scope: str) -> list[str]:
    return [f"{scope} missing required field '{field}'" for field in fields if field not in record]


def _trial_report(trial: Any) -> dict[str, Any]:
    trial_label = trial.get("trial_id", "<invalid>") if isinstance(trial, dict) else "<invalid>"
    report: dict[str, Any] = {"trial_id": trial_label, "pass": False}
    report["_samples"] = {
        "errors": [],
        "epsilons": [],
        "latencies": [],
        "rtts": [],
        "dispersions": [],
        "drifts": [],
    }
    failures: list[str] = []
    report["failures"] = failures
    if not isinstance(trial, dict):
        failures.append("trial must be an object")
        report["metrics"] = _empty_metrics()
        return report

    failures.extend(_validate_required_fields(trial, REQUIRED_TRIAL_FIELDS, "trial"))
    metrics = _empty_metrics()
    report["metrics"] = metrics
    if failures:
        return report

    try:
        trial_id = _text(trial["trial_id"], "trial_id")
        mode = _text(trial["mode"], f"{trial_id}.mode")
        if mode not in DEFAULT_REQUIRED_MODES:
            raise ValueError(f"{trial_id}.mode must be loopback, lan, or wireless")
        _text(trial["source_id"], f"{trial_id}.source_id")
        _text(trial["reference_source_id"], f"{trial_id}.reference_source_id")
        sample_rate = _number(trial["sample_rate_hz"], f"{trial_id}.sample_rate_hz")
        batch_samples = _integer(trial["batch_samples"], f"{trial_id}.batch_samples")
        expected_rate = _number(trial["expected_sample_rate_hz"], f"{trial_id}.expected_sample_rate_hz")
        observed_rate = _number(trial["observed_sample_rate_hz"], f"{trial_id}.observed_sample_rate_hz")
        if sample_rate <= 0 or batch_samples <= 0 or expected_rate <= 0 or observed_rate <= 0:
            raise ValueError("sample and batch rates must be positive")
        metrics["rate_drift_ppm"] = (observed_rate / expected_rate - 1.0) * 1_000_000.0

        provenance = trial["provenance"]
        continuity = trial["continuity"]
        observations = trial["observations"]
        if not isinstance(provenance, dict):
            raise ValueError("provenance must be an object")
        if not isinstance(continuity, dict):
            raise ValueError("continuity must be an object")
        if not isinstance(observations, list):
            raise ValueError("observations must be an array")
        failures.extend(_validate_required_fields(provenance, REQUIRED_PROVENANCE_FIELDS, f"{trial_id}.provenance"))
        for field in REQUIRED_PROVENANCE_FIELDS:
            if field in provenance:
                _text(provenance[field], f"{trial_id}.provenance.{field}")
        failures.extend(_validate_required_fields(continuity, REQUIRED_CONTINUITY_FIELDS, f"{trial_id}.continuity"))
        for field in REQUIRED_CONTINUITY_FIELDS:
            if field in continuity and _integer(continuity[field], f"{trial_id}.continuity.{field}") != 0:
                failures.append(f"{trial_id}.continuity.{field} is non-zero")
        errors: list[float] = []
        epsilons: list[float] = []
        latencies: list[float] = []
        rtts: list[float] = []
        dispersions: list[float] = []
        source_sequences: list[int] = []
        batch_sequences: list[int] = []
        model_ids: set[str] = set()
        for index, observation in enumerate(observations):
            scope = f"{trial_id}.observations[{index}]"
            if not isinstance(observation, dict):
                failures.append(f"{scope} must be an object")
                continue
            failures.extend(_validate_required_fields(observation, REQUIRED_OBSERVATION_FIELDS, scope))
            if any(field not in observation for field in REQUIRED_OBSERVATION_FIELDS):
                continue
            try:
                _text(observation["edge_id"], f"{scope}.edge_id")
                reference = _number(observation["reference_time_ns"], f"{scope}.reference_time_ns")
                observed = _number(observation["observed_time_ns"], f"{scope}.observed_time_ns")
                epsilon = _nonnegative_number(observation["epsilon_ns"], f"{scope}.epsilon_ns")
                _integer(observation["source_tick"], f"{scope}.source_tick")
                source_sequence = _integer(observation["source_sequence"], f"{scope}.source_sequence")
                batch_sequence = _integer(observation["batch_sequence"], f"{scope}.batch_sequence")
                _nonnegative_number(observation["host_receive_time_ns"], f"{scope}.host_receive_time_ns")
                _nonnegative_number(
                    observation["source_acquisition_time_ns"], f"{scope}.source_acquisition_time_ns"
                )
                batch_latency = _nonnegative_number(observation["batch_latency_ns"], f"{scope}.batch_latency_ns")
                model_id = _text(observation["clock_model_id"], f"{scope}.clock_model_id")
                _text(observation["boot_session_id"], f"{scope}.boot_session_id")
                quality = _text(observation["quality"], f"{scope}.quality")
                if quality not in metrics["quality_counts"]:
                    failures.append(f"{scope}.quality is not locked, degraded, or unbounded")
                    continue
                rtt = _nonnegative_number(observation["rtt_ns"], f"{scope}.rtt_ns")
                dispersion = _nonnegative_number(observation["sync_dispersion_ns"], f"{scope}.sync_dispersion_ns")
            except ValueError as error:
                failures.append(str(error))
                continue

            error = abs(observed - reference)
            errors.append(error)
            epsilons.append(epsilon)
            latencies.append(batch_latency)
            rtts.append(rtt)
            dispersions.append(dispersion)
            source_sequences.append(source_sequence)
            batch_sequences.append(batch_sequence)
            model_ids.add(model_id)
            metrics["quality_counts"][quality] += 1
            if quality == "unbounded":
                metrics["unbounded_observations"] += 1
            if error > epsilon:
                metrics["uncovered_observations"] += 1

        report["_samples"] = {
            "errors": errors,
            "epsilons": epsilons,
            "latencies": latencies,
            "rtts": rtts,
            "dispersions": dispersions,
            "drifts": [metrics["rate_drift_ppm"]],
        }

        metrics["observation_count"] = len(errors)
        metrics["absolute_error_ns"] = _stats(errors)
        metrics["epsilon_ns"] = _stats(epsilons)
        metrics["latency_ns"] = _stats(latencies)
        metrics["rtt_ns"] = {
            "min": min(rtts) if rtts else None,
            **_stats(rtts),
        }
        metrics["sync_dispersion_ns"] = _stats(dispersions)
        metrics["bound_coverage"] = (
            (metrics["observation_count"] - metrics["uncovered_observations"])
            / metrics["observation_count"]
            if metrics["observation_count"]
            else None
        )
        metrics["latency_jitter_peak_to_peak_ns"] = max(latencies) - min(latencies) if latencies else None
        metrics["source_sequences_strictly_increasing"] = all(
            right > left for left, right in zip(source_sequences, source_sequences[1:])
        )
        metrics["batch_sequences_strictly_increasing"] = all(
            right > left for left, right in zip(batch_sequences, batch_sequences[1:])
        )
        metrics["model_ids"] = sorted(model_ids)
        if not errors:
            failures.append(f"{trial_id} has no valid edge observations")
        if metrics["uncovered_observations"]:
            failures.append(f"{trial_id} has observations outside the persisted epsilon bound")
        if metrics["unbounded_observations"]:
            failures.append(f"{trial_id} contains unbounded synchronization observations")
        if not metrics["source_sequences_strictly_increasing"]:
            failures.append(f"{trial_id} source sequence regressed or duplicated")
        if not metrics["batch_sequences_strictly_increasing"]:
            failures.append(f"{trial_id} batch sequence regressed or duplicated")
        report["trial_id"] = trial_id
        report["mode"] = mode
        report["sample_rate_hz"] = sample_rate
        report["batch_samples"] = batch_samples
    except (KeyError, TypeError, ValueError) as error:
        failures.append(f"{trial.get('trial_id', '<invalid>')}: {error}")
    report["pass"] = not failures
    return report


def analyze(
    document: Any,
    required_pairs: tuple[tuple[float, int], ...] = DEFAULT_REQUIRED_PAIRS,
    required_modes: tuple[str, ...] = DEFAULT_REQUIRED_MODES,
) -> dict[str, Any]:
    """Return a JSON-serializable acceptance report for a capture document."""

    failures: list[str] = []
    if not isinstance(document, dict) or not isinstance(document.get("trials"), list):
        return {"pass": False, "failures": ["input must be an object with a trials array"], "trials": []}

    trial_reports = [_trial_report(trial) for trial in document["trials"]]
    samples = {
        "errors": [],
        "epsilons": [],
        "latencies": [],
        "rtts": [],
        "dispersions": [],
        "drifts": [],
    }
    for report in trial_reports:
        trial_samples = report.pop("_samples", {})
        for key in samples:
            samples[key].extend(trial_samples.get(key, []))
    passed_pairs = {
        (float(report.get("sample_rate_hz", -1)), int(report.get("batch_samples", -1)))
        for report in trial_reports
        if report.get("pass")
    }
    missing_pairs = [
        {"sample_rate_hz": rate, "batch_samples": batch}
        for rate, batch in required_pairs
        if (rate, batch) not in passed_pairs
    ]
    if missing_pairs:
        failures.append(f"required rate/batch trials are missing or failed: {missing_pairs}")
    passed_trials = {
        (report.get("mode"), float(report.get("sample_rate_hz", -1)), int(report.get("batch_samples", -1)))
        for report in trial_reports
        if report.get("pass")
    }
    missing_mode_trials = [
        {"mode": mode, "sample_rate_hz": rate, "batch_samples": batch}
        for mode in required_modes
        for rate, batch in required_pairs
        if (mode, rate, batch) not in passed_trials
    ]
    if missing_mode_trials:
        failures.append(f"required mode/rate/batch trials are missing or failed: {missing_mode_trials}")
    failures.extend(
        f"{report.get('trial_id', '<invalid>')}: {failure}"
        for report in trial_reports
        for failure in report.get("failures", [])
    )

    all_metrics = [report["metrics"] for report in trial_reports]
    quality_counts = {quality: sum(m["quality_counts"][quality] for m in all_metrics) for quality in ("locked", "degraded", "unbounded")}
    total_observations = len(samples["errors"])
    total_uncovered = sum(m["uncovered_observations"] for m in all_metrics)
    summary_rtt = {
        "min": min(samples["rtts"]) if samples["rtts"] else None,
        **_stats(samples["rtts"]),
    }
    report = {
        "schema_version": 1,
        "pass": not failures,
        "failures": failures,
        "required_rate_batch_pairs": [
            {"sample_rate_hz": rate, "batch_samples": batch} for rate, batch in required_pairs
        ],
        "required_modes": list(required_modes),
        "missing_rate_batch_pairs": missing_pairs,
        "missing_mode_rate_batch_trials": missing_mode_trials,
        "summary": {
            "trial_count": len(trial_reports),
            "passed_trial_count": sum(1 for trial in trial_reports if trial.get("pass")),
            "observation_count": total_observations,
            "absolute_error_ns": _stats(samples["errors"]),
            "epsilon_ns": _stats(samples["epsilons"]),
            "latency_ns": _stats(samples["latencies"]),
            "latency_jitter_peak_to_peak_ns": (
                max(samples["latencies"]) - min(samples["latencies"])
                if samples["latencies"]
                else None
            ),
            "rtt_ns": summary_rtt,
            "sync_dispersion_ns": _stats(samples["dispersions"]),
            "rate_drift_ppm": _stats(samples["drifts"]),
            "bound_coverage": (
                (total_observations - total_uncovered) / total_observations
                if total_observations
                else None
            ),
            "uncovered_observations": total_uncovered,
            "quality_counts": quality_counts,
            "mode_counts": {
                mode: sum(1 for trial in trial_reports if trial.get("mode") == mode)
                for mode in required_modes
            },
        },
        "trials": trial_reports,
    }
    return report


def _parse_pairs(value: str) -> tuple[tuple[float, int], ...]:
    pairs: list[tuple[float, int]] = []
    for item in value.split(","):
        try:
            rate_text, batch_text = item.split(":", 1)
            rate = _number(float(rate_text), "required sample rate")
            batch = _integer(int(batch_text), "required batch size")
        except (ValueError, TypeError):
            raise argparse.ArgumentTypeError(f"invalid rate:batch pair '{item}'")
        if rate <= 0 or batch <= 0:
            raise argparse.ArgumentTypeError(f"rate and batch size must be positive in '{item}'")
        pairs.append((rate, batch))
    if not pairs:
        raise argparse.ArgumentTypeError("at least one rate:batch pair is required")
    return tuple(pairs)


def _parse_modes(value: str) -> tuple[str, ...]:
    modes = tuple(item.strip() for item in value.split(",") if item.strip())
    if not modes or any(mode not in DEFAULT_REQUIRED_MODES for mode in modes):
        raise argparse.ArgumentTypeError("modes must be loopback, lan, or wireless")
    return modes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON measurement document")
    parser.add_argument(
        "--required-pairs",
        type=_parse_pairs,
        default=DEFAULT_REQUIRED_PAIRS,
        metavar="RATE:BATCH,...",
        help="required sample-rate/batch-size matrix (default: 1000:32,1500:48,2000:64,2500:40)",
    )
    parser.add_argument(
        "--required-modes",
        type=_parse_modes,
        default=DEFAULT_REQUIRED_MODES,
        metavar="MODE,...",
        help="required trial modes (default: loopback,lan,wireless)",
    )
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"cannot read measurement document: {error}", file=sys.stderr)
        return 2
    report = analyze(document, args.required_pairs, args.required_modes)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
