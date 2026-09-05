"""Prepare a task config or run an operator-controlled terminal instructor."""
import argparse
import copy
import json
import time
from pathlib import Path
from stateful_decode_and_sync.calibration_task import make_profile, load_profile, Journal, TaskInstructor
from stateful_decode_and_sync.client import NdjsonClient, SocketClientError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prepare = subs.add_parser("prepare")
    prepare.add_argument("--base-config", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--provenance", help="existing metadata to carry into the new session")
    run = subs.add_parser("run")
    run.add_argument("--profile", required=True)
    run.add_argument("--journal", required=True)
    run.add_argument("--port", type=int, default=8765)
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--hold-seconds", type=float, default=3.0)
    args = parser.parse_args()
    if args.command == "prepare":
        # Validate inputs before creating any output, including optional metadata.
        try:
            config = json.loads(Path(args.base_config).read_text(encoding="utf-8-sig"))
            metadata = json.loads(Path(args.provenance).read_text(encoding="utf-8-sig")) if args.provenance else {
                "purpose": "calibration task acceptance", "device_inventory": None, "physical_sync": None}
        except (OSError, ValueError) as error:
            parser.error(f"cannot read preparation input: {error}")
        if not isinstance(metadata, dict) or not metadata:
            parser.error("provenance must be a nonempty JSON object")
        if not isinstance(config, dict) or not isinstance(config.get("nodes"), list):
            parser.error("base config must contain a nodes list")
        app = [n for n in config["nodes"] if n.get("application", {}).get("name") == "stateful-decode-and-sync"]
        if len(app) != 1:
            parser.error("base config must contain exactly one stateful-decode-and-sync App")
        profile = make_profile()
        app[0]["application"].setdefault("parameters", {}).update(
            task_definition=copy.deepcopy(profile["definition"]), task_reference_source_id=profile["reference_source_id"])
        out = Path(args.output_dir)
        try:
            out.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            parser.error(f"cannot create new output directory {out}: {error}; choose a fresh --output-dir")
        (out / "device-config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        (out / "task-profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        metadata["task_profile"] = profile
        metadata["configuration_input"] = {"path": str(out / "device-config.json"), "snapshot": config,
            "evidence": "Generated configuration for operator deployment; not a live device readback"}
        metadata["running_configuration"] = None
        (out / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"Prepared {out}; definition {profile['definition_hash']}. No device commands executed.")
        return
    if args.repetitions < 1 or not 0 < args.hold_seconds <= 3600:
        parser.error("positive repetitions and hold-seconds in (0, 3600] required")
    profile = load_profile(args.profile)
    journal = Journal(args.journal)
    journal.write("instructor_start", profile=profile, repetitions=args.repetitions,
                  hold_seconds=args.hold_seconds, presentation_clock="host monotonic; terminal print is not measured stimulus onset")
    client = NdjsonClient(port=args.port)
    try:
        client.connect()
    except SocketClientError as error:
        try:
            journal.write("instructor_failed", stage="connect", error=str(error))
        finally:
            client.close()
            journal.close()
        parser.exit(2, f"{error}\nStart run_service.py in the same OS/environment with --port {args.port}, "
                       "then retry with a new --journal path. No task commands were sent.\n")
    with client:
        instructor = TaskInstructor(client, profile, journal)
        try:
            instructor.prepare()
            for trial in range(args.repetitions):
                if trial:
                    instructor.command("reset_task")
                event = instructor.command("start_task")
                for state in range(1, 4):
                    print(f"Trial {trial + 1}: {profile['instructions'][str(state)]}", flush=True)
                    journal.write("presentation", state_id=state, committed_event=event)
                    time.sleep(args.hold_seconds)
                    event = instructor.command("propose_task_event", event_name="advance")
                print("Complete", flush=True)
            instructor.command("reset_task")
            journal.write("instructor_complete")
        except BaseException as error:
            journal.write("instructor_failed", error=str(error))
            instructor.cleanup()
            raise
        finally:
            journal.close()


if __name__ == "__main__":
    main()
