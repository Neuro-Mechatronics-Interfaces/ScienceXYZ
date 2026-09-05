"""Reopen raw HDF5, validate epochs, plot diagnostics and optionally fit a baseline."""
import argparse
import json
from pathlib import Path
from stateful_decode_and_sync.calibration_task import load_profile
from stateful_decode_and_sync.recording_analysis import (
    read_recording, build_epochs, gpio_edges, write_csv, diagnostic_plot, fit_baseline, sha256_file, validate_journal)


def analyze(raw, output, profile=None, fit=False, max_frames=2_000_000, journal=None, **feature_options):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    data = read_recording(raw, max_frames)
    embedded = data["metadata"].get("task_profile")
    if profile and embedded and (profile["definition_hash"] != embedded.get("definition_hash") or
                                profile["state_to_label"] != embedded.get("state_to_label")):
        data["problems"].append("supplied profile/labels differ from recorded profile")
    journal_report = validate_journal(data, journal) if journal else None
    if journal_report and not journal_report["valid"]:
        data["problems"].append("instructor/browser journal verification failed")
    epochs, runs = build_epochs(data, profile) if profile else ([], [])
    edges = gpio_edges(data)
    report = {key: data[key] for key in ("path", "sha256", "status", "problems", "counters", "layout", "local_stop")}
    report.update(frame_count=len(data["frames"]), task_events=len(data["events"]),
                  valid_epochs=sum(e["valid"] for e in epochs), invalid_epochs=sum(not e["valid"] for e in epochs),
                  runs=runs, gpio_changes=len(edges), profile=profile,
                  label_validation="not_requested" if not profile else "no_task_events" if not data["events"] else "evaluated",
                  physical_sync_verified=False)
    report["journal"] = journal_report
    report["label_mapping_source"] = "embedded_and_supplied" if embedded and profile else "supplied_profile" if profile else "none"
    fields = ["app_session_id", "run_sequence", "state_id", "label", "start_index", "end_index", "start_sequence",
              "end_sequence", "start_timestamp_ns", "end_timestamp_ns", "valid", "reason"]
    write_csv(out / "epochs.csv", epochs, fields)
    write_csv(out / "gpio-edges.csv", edges, ["gpio_id", "sequence", "timestamp_ns", "previous_level", "level", "across_gap"])
    (out / "task-events.json").write_text(json.dumps(data["events"], indent=2), encoding="utf-8")
    diagnostic_plot(data, epochs, out / "diagnostic.png")
    if fit:
        try:
            report["fit"] = fit_baseline(data, epochs, out, **feature_options)
        except ValueError as error:
            report["fit_refused"] = str(error)
    report["raw_unchanged"] = sha256_file(raw) == data["sha256"]
    if not report["raw_unchanged"]:
        raise RuntimeError("Raw input changed during analysis; reject outputs")
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("frame_count", "task_events", "valid_epochs", "invalid_epochs", "gpio_changes", "raw_unchanged")}))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("raw")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--profile")
    p.add_argument("--fit", action="store_true")
    p.add_argument("--journal", help="browser-events.ndjson or terminal instructor journal to cross-check")
    p.add_argument("--max-frames", type=int, default=2_000_000)
    p.add_argument("--window-ms", type=float, default=200.)
    p.add_argument("--stride-ms", type=float, default=100.)
    p.add_argument("--margin-ms", type=float, default=250.)
    args = p.parse_args()
    report = analyze(args.raw, args.output_dir, load_profile(args.profile) if args.profile else None,
                     args.fit, args.max_frames, args.journal, window_ms=args.window_ms, stride_ms=args.stride_ms, margin_ms=args.margin_ms)
    if "fit_refused" in report:
        p.exit(2, report["fit_refused"] + "\n")
    if report["problems"] or any(report["counters"].values()) or report["invalid_epochs"] or (args.profile and not report["valid_epochs"]):
        p.exit(2, "Diagnostics written; recording/label acceptance did not pass. See report.json.\n")


if __name__ == "__main__":
    main()
