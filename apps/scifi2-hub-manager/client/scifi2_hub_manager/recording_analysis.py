"""Read-only raw-wire diagnostics and conservative task labels. Never edits HDF5."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import h5py
import numpy as np
from google.protobuf.message import DecodeError
from synapse.api.datatype_pb2 import BroadbandFrame
from . import proto
from .model import task_transition_from_proto, task_transition_to_json


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def channel_layout(frame):
    if not frame.channel_ranges:
        return [("ELECTRODE", i) for i in range(len(frame.frame_data))]
    layout = []
    for channel_range in frame.channel_ranges:
        ids = list(channel_range.channel_ids) or list(range(channel_range.count))
        if len(ids) != channel_range.count or channel_range.type not in (0, 1):
            raise ValueError("invalid/unknown channel range")
        layout.extend(("GPIO" if channel_range.type == 1 else "ELECTRODE", i) for i in ids)
    if len(layout) != len(frame.frame_data) or len(layout) != len(set(layout)):
        raise ValueError("channel ranges disagree with frame payload or duplicate identity")
    return layout


def read_recording(path, max_frames=2_000_000):
    records, events, problems = [], [], []
    counters = Counter()
    previous = None
    layout = None
    segment = 0
    with h5py.File(path, "r") as source:
        if source.attrs.get("raw_schema") != "sciencexyz.raw_taps.v1":
            raise ValueError("unsupported/missing raw schema")
        status = json.loads(source.attrs["recording_status_json"])
        metadata = json.loads(source.attrs["metadata_json"])
        source_id = str(source.attrs.get("provenance_reference_source_id", ""))
        if not source_id:
            raise ValueError("missing reference source identity")
        if "raw_broadband" not in source:
            raise ValueError("recording has no raw broadband messages")
        data = source["raw_broadband"]
        if len(data) > max_frames:
            raise ValueError(f"{len(data)} frames exceeds explicit memory limit {max_frames}; increase --max-frames")
        for begin in range(0, len(data), 4096):
            for raw_index, row in enumerate(data[begin:begin + 4096], begin):
                frame = BroadbandFrame()
                try:
                    frame.ParseFromString(bytes(row["payload"]))
                    if not frame.frame_data or frame.sample_rate_hz <= 0:
                        raise ValueError("empty frame or zero sample rate")
                    current_layout = channel_layout(frame)
                    if layout is None:
                        layout = current_layout
                    if current_layout != layout:
                        raise ValueError("channel layout changed; separate recordings required")
                except (DecodeError, ValueError):
                    counters["invalid_reference_messages"] += 1
                    segment += 1
                    previous = None
                    continue
                sequence, timestamp = int(frame.sequence_number), int(frame.timestamp_ns)
                if previous:
                    last_seq, last_ts, last_rate = previous
                    if sequence > last_seq + 1:
                        counters["missing_reference_sequences"] += sequence - last_seq - 1
                    if sequence <= last_seq:
                        counters["sequence_regressions"] += 1
                    if timestamp <= last_ts:
                        counters["timestamp_regressions"] += 1
                    if sequence != last_seq + 1 or timestamp <= last_ts or frame.sample_rate_hz != last_rate:
                        segment += 1
                        counters["continuity_breaks"] += 1
                records.append((raw_index, sequence, timestamp, int(frame.unix_timestamp_ns),
                                int(row["host_receive_time_ns"]), int(frame.sample_rate_hz), segment,
                                np.asarray(frame.frame_data, dtype=np.int32)))
                previous = sequence, timestamp, frame.sample_rate_hz
        raw_task_count = 0
        if "raw_task" in source:
            raw_task_count = len(source["raw_task"])
            for begin in range(0, raw_task_count, 1024):
                for row in source["raw_task"][begin:begin + 1024]:
                    try:
                        wire = proto.TaskTransitionEvent()
                        wire.ParseFromString(bytes(row["payload"]))
                        event = task_transition_to_json(task_transition_from_proto(wire))
                        event["host_receive_time_ns"] = int(row["host_receive_time_ns"])
                        events.append(event)
                    except (DecodeError, ValueError):
                        counters["invalid_task_messages"] += 1
        control = source["events"][:] if "events" in source else []
        stopped = (len(control) == 2 and int(control[0]["kind"]) == 0
                   and int(control[-1]["kind"]) == 1 and status.get("state") == "stopped")
        if not stopped:
            problems.append("missing successful local start/stop lifecycle")
        for key in ("write_errors", "transport_errors", "rejected_task_events"):
            if status.get(key, 0):
                problems.append(f"recorder reports {key}={status[key]}")
        if status.get("raw_reference_messages") != len(data) or status.get("raw_task_messages") != raw_task_count:
            problems.append("recorder message counters do not match stored rows")
    if not records:
        raise ValueError("no decodable reference frames")
    scalars = np.asarray([r[:7] for r in records], dtype=np.uint64)
    return {"path": str(Path(path).resolve()), "sha256": sha256_file(path), "metadata": metadata,
            "status": status, "problems": problems, "counters": dict(counters), "layout": layout,
            "frames": scalars, "samples": np.stack([r[7] for r in records]), "events": events,
            "source_id": source_id, "local_stop": stopped}


def build_epochs(recording, profile):
    frames = recording["frames"]
    indices = {}
    for i, frame in enumerate(frames):
        key = (int(frame[1]), int(frame[2]))
        indices[key] = i if key not in indices else None
    events = recording["events"]
    grouped = {}
    for event in events:
        grouped.setdefault((event["app_session_id"], int(event["run_sequence"])), []).append(event)
    epochs = []
    run_reports = []
    for (session, run), group in grouped.items():
        labels = profile["state_to_label"]
        starts = [e for e in group if e["event_kind"] == "start"]
        run_ok = (len(starts) == 1 and group[0]["event_kind"] == "start" and
                  int(group[0]["current_state_id"]) == 1 and int(group[0]["transition_sequence"]) == 1)
        completed = any(e["event_kind"] == "transition" and int(e["current_state_id"]) == 4 for e in group)
        run_ok &= completed and not any(e["event_kind"] == "abort" for e in group)
        run_reports.append({"app_session_id": session, "run_sequence": run,
                            "has_start": len(starts) == 1, "has_completion": completed})
        for j, event in enumerate(group):
            state = int(event["current_state_id"])
            if event["event_kind"] not in ("start", "transition") or labels.get(str(state)) is None:
                continue
            end = group[j + 1] if j + 1 < len(group) else None
            reasons = []
            if not run_ok:
                reasons.append("incomplete_or_aborted_run")
            for boundary_event in [event, end]:
                if boundary_event is not None and (boundary_event["definition_hash"] != profile["definition_hash"]
                        or boundary_event["definition_id"] != profile["definition"]["definition_id"]
                        or int(boundary_event["definition_revision"]) != profile["definition"]["revision"]):
                    reasons.append("definition_mismatch")
            start_frame = event["effective_frame"]
            end_frame = end["effective_frame"] if end else {}
            left = indices.get((int(start_frame["sequence_number"]), int(start_frame["timestamp_ns"])))
            right = indices.get((int(end_frame.get("sequence_number", -1)), int(end_frame.get("timestamp_ns", -1))))
            if start_frame["source_id"] != recording["source_id"] or end_frame.get("source_id") != recording["source_id"]:
                reasons.append("source_identity_mismatch")
            if left is None or right is None:
                reasons.append("boundary_missing_or_duplicated")
            elif right <= left or int(frames[right, 2]) <= int(frames[left, 2]):
                reasons.append("boundary_regression")
            elif frames[left, 6] != frames[right, 6]:
                reasons.append("gap_or_clock_or_rate_change")
            if end is None or int(end["previous_state_id"]) != state or (
                    int(end["event_sequence"]) != int(event["event_sequence"]) + 1 or
                    int(end["transition_sequence"]) != int(event["transition_sequence"]) + 1):
                reasons.append("event_discontinuity")
            if end is not None and (end["event_kind"] != "transition" or int(end["current_state_id"]) != state + 1):
                reasons.append("unexpected_task_transition")
            if recording["counters"].get("invalid_task_messages"):
                reasons.append("unparseable_task_history")
            if not recording["local_stop"] or recording["problems"]:
                reasons.append("recording_failure")
            epochs.append({"app_session_id": session, "run_sequence": run, "state_id": state,
                           "label": labels[str(state)], "start_index": left, "end_index": right,
                           "start_sequence": int(start_frame["sequence_number"]),
                           "end_sequence": int(end_frame["sequence_number"]) if end else None,
                           "start_timestamp_ns": int(start_frame["timestamp_ns"]),
                           "end_timestamp_ns": int(end_frame["timestamp_ns"]) if end else None,
                           "valid": not reasons, "reason": ";".join(sorted(set(reasons)))})
    # Ambiguous overlapping runs/sessions must never supply two labels per frame.
    for i, a in enumerate(epochs):
        for b in epochs[i + 1:]:
            if None not in (a["start_index"], a["end_index"], b["start_index"], b["end_index"]) and max(
                    a["start_index"], b["start_index"]) < min(a["end_index"], b["end_index"]):
                for epoch in (a, b):
                    epoch["valid"] = False
                    epoch["reason"] += ";overlapping_epochs"
    return epochs, run_reports


def validate_journal(recording, path):
    with Path(path).open(encoding="utf-8") as source:
        records = [json.loads(line) for line in source if line.strip()]
    committed = [r["event"] for r in records if r["kind"] == "task_committed"]
    def identity(e):
        f = e["effective_frame"]
        return (e["app_session_id"], int(e["run_sequence"]), int(e["event_sequence"]),
                e.get("request_id"), f["source_id"], int(f["sequence_number"]), int(f["timestamp_ns"]),
                e["definition_hash"], int(e["current_state_id"]), e["event_kind"])
    recorded = Counter(identity(e) for e in recording["events"])
    missing = [e["request_id"] for e in committed if recorded[identity(e)] != 1]
    stops = [r for r in records if r["kind"] == "recording_stopped"]
    failures = [r["kind"] for r in records if r["kind"] in {
        "connection_lost", "instructor_failed", "cleanup_failed", "task_failed", "recorder_failed"}]
    if stops and (stops[-1].get("raw_sha256") != recording["sha256"] or not stops[-1].get("local_stop_ok")):
        failures.append("journal_raw_file_or_stop_mismatch")
    if not stops and not any(r["kind"] == "instructor_complete" for r in records):
        failures.append("journal_missing_completion")
    if not committed:
        failures.append("journal_has_no_committed_events")
    return {"path": str(Path(path).resolve()), "sha256": sha256_file(path),
            "committed_events": len(committed), "missing_or_duplicate_raw_requests": missing,
            "failures": failures, "valid": not missing and not failures,
            "browser_clock_mapping": "unmeasured; original time fields are not source-time labels"}


def write_csv(path, rows, fields):
    with Path(path).open("x", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def gpio_edges(recording):
    result = []
    frames, samples = recording["frames"], recording["samples"]
    for ch, (kind, identity) in enumerate(recording["layout"]):
        if kind != "GPIO":
            continue
        values = samples[:, ch]
        for i in np.flatnonzero(values[1:] != values[:-1]) + 1:
            result.append({"gpio_id": identity, "sequence": int(frames[i, 1]),
                           "timestamp_ns": int(frames[i, 2]), "previous_level": int(values[i - 1]),
                           "level": int(values[i]), "across_gap": bool(frames[i, 6] != frames[i - 1, 6])})
    return result


def diagnostic_plot(recording, epochs, path):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    frames, samples = recording["frames"], recording["samples"]
    origin = int(frames[0, 2])
    t = np.asarray([(int(v) - origin) / 1e9 for v in frames[:, 2]])
    electrodes = [i for i, (kind, _) in enumerate(recording["layout"]) if kind == "ELECTRODE"][:3]
    fig, axes = plt.subplots(len(electrodes) + 2, 1, figsize=(13, 9), sharex=True, layout="constrained")
    # Min/max envelopes are display-only aggregation. Analysis uses every frame.
    width = max(1, len(t) // 4000)
    for ax, ch in zip(axes, electrodes):
        for segment in np.unique(frames[:, 6]):
            selected = np.flatnonzero(frames[:, 6] == segment)
            if width == 1:
                ax.plot(t[selected], samples[selected, ch], color="steelblue", linewidth=.6,
                        marker="." if len(selected) == 1 else None)
                continue
            for begin in range(0, len(selected), width):
                ix = selected[begin:begin + width]
                ax.vlines(t[ix[0]], samples[ix, ch].min(), samples[ix, ch].max(), color="steelblue", linewidth=.6)
        ax.set_ylabel(f"E{recording['layout'][ch][1]}\nADC counts")
    for ch, (kind, identity) in enumerate(recording["layout"]):
        if kind == "GPIO":
            # Separate artists prevent drawing a known level through missing data.
            for n, segment in enumerate(np.unique(frames[:, 6])):
                selected = np.flatnonzero(frames[:, 6] == segment)
                values = samples[selected, ch]
                changes = np.flatnonzero(values[1:] != values[:-1]) + 1
                ix = selected[np.unique(np.r_[0, changes - 1, changes, len(selected) - 1])]
                axes[-2].step(t[ix], samples[ix, ch], where="post", color=f"C{identity % 10}",
                              label=f"GPIO {identity}" if n == 0 else None)
    axes[-2].set_ylabel("Digital level")
    if any(k == "GPIO" for k, _ in recording["layout"]):
        axes[-2].legend(loc="upper right")
    for epoch in epochs:
        if epoch["end_timestamp_ns"] is not None:
            a, b = (epoch["start_timestamp_ns"] - origin) / 1e9, (epoch["end_timestamp_ns"] - origin) / 1e9
            axes[-1].axvspan(a, b, color="green" if epoch["valid"] else "red", alpha=.2)
            axes[-1].text((a + b) / 2, .5, epoch["label"], rotation=30, ha="center")
    if not epochs:
        axes[-1].text(.5, .5, "No task events: no labels or fitting", transform=axes[-1].transAxes, ha="center")
    axes[-1].set_ylabel("Task epochs")
    axes[-1].set_yticks([])
    axes[-1].set_xlabel(f"Source time relative to {origin} ns (seconds)")
    fig.suptitle("Raw recording diagnostic — GPIO timing is not physically verified")
    fig.savefig(path, dpi=130)
    plt.close(fig)


def fit_baseline(recording, epochs, out, *, window_ms=200., stride_ms=100., margin_ms=250.):
    if not all(np.isfinite(v) and v > 0 for v in (window_ms, stride_ms)) or not np.isfinite(margin_ms) or margin_ms < 0:
        raise ValueError("window/stride must be positive and margin nonnegative")
    complete_runs = {}
    for e in epochs:
        complete_runs.setdefault((e["app_session_id"], e["run_sequence"]), []).append(e)
    usable = {key: values for key, values in complete_runs.items()
              if len(values) == 3 and all(e["valid"] for e in values) and {e["state_id"] for e in values} == {1, 2, 3}}
    if len(usable) < 3:
        raise ValueError("fitting requires at least three complete, valid task runs; no invented labels")
    holdout = sorted(usable)[-1]
    channels = [i for i, (kind, _) in enumerate(recording["layout"]) if kind == "ELECTRODE"]
    features, labels, groups, bounds = [], [], [], []
    frames, samples = recording["frames"], recording["samples"]
    rates = {int(frames[e["start_index"], 5]) for values in usable.values() for e in values}
    if len(rates) != 1 or not channels:
        raise ValueError("fitting requires electrode channels and a single sample rate across runs")
    for key, values in usable.items():
        for e in values:
            rate = int(frames[e["start_index"], 5])
            width, stride = round(rate * window_ms / 1000), round(rate * stride_ms / 1000)
            if min(width, stride) < 1:
                raise ValueError("feature window/stride smaller than one sample")
            margin = int(np.ceil(rate * margin_ms / 1000))
            for left in range(e["start_index"] + margin, e["end_index"] - margin - width + 1, stride):
                right = left + width
                if (int(frames[left, 2]) < e["start_timestamp_ns"] + margin_ms * 1e6 or
                    int(frames[right - 1, 2]) >= e["end_timestamp_ns"] - margin_ms * 1e6):
                    continue
                block = samples[left:right, channels].astype(np.float64)
                features.append(np.log1p(np.var(block, axis=0)))
                labels.append(e["label"])
                groups.append(key == holdout)
                bounds.append((left, right))
    X, y, test = np.asarray(features), np.asarray(labels), np.asarray(groups, dtype=bool)
    classes = sorted({e["label"] for values in usable.values() for e in values})
    if not features or any(not np.any(y[test] == c) or not np.any(y[~test] == c) for c in classes):
        raise ValueError("each class needs training and held-out windows; lengthen task holds")
    mean, scale = X[~test].mean(axis=0), X[~test].std(axis=0)
    scale[scale == 0] = 1
    Z = np.c_[(X - mean) / scale, np.ones(len(X))]
    target = np.asarray([[label == c for c in classes] for label in y], dtype=float)
    penalty = np.eye(Z.shape[1]); penalty[-1, -1] = 0
    weights = np.linalg.solve(Z[~test].T @ Z[~test] + penalty, Z[~test].T @ target[~test])
    predicted = np.argmax(Z[test] @ weights, axis=1)
    confusion = np.zeros((len(classes), len(classes)), dtype=int)
    for label, p in zip(y[test], predicted):
        confusion[classes.index(label), p] += 1
    details = {"method": "ridge least-squares classifier, alpha=1; offline baseline, not device MLP",
               "feature": "log1p population variance per electrode in raw ADC counts",
               "window_ms": window_ms, "stride_ms": stride_ms, "margin_ms": margin_ms,
               "sample_rate_hz": next(iter(rates)),
               "sample_grid": "native sample count at recorded sample_rate_hz; no resampling",
               "raw_sha256": recording["sha256"], "electrode_channels": channels,
               "holdout_run": list(holdout), "classes": classes,
               "train_counts": dict(Counter(y[~test].tolist())), "test_counts": dict(Counter(y[test].tolist())),
               "confusion_matrix_true_rows_predicted_columns": confusion.tolist(),
               "accuracy": float(np.trace(confusion) / confusion.sum())}
    np.savez(out / "baseline.npz", mean=mean, scale=scale, weights=weights, classes=np.asarray(classes),
             electrode_channels=np.asarray(channels), metadata_json=json.dumps(details))
    np.savez(out / "features.npz", X=X, labels=y, is_test=test, frame_bounds=np.asarray(bounds))
    (out / "fit.json").write_text(json.dumps(details, indent=2), encoding="utf-8")
    return details
