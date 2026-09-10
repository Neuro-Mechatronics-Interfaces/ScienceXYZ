"""Operator launcher: generate a calibration session, gate on the operator's
device start, then run the host control service and Reactions bridge.

This tool performs no device control. It never invokes ``synapsectl`` and never
talks to the SciFi-2. It only:

1. generates ``device-config.json`` / ``task-profile.json`` / ``provenance.json``
   into a fresh, exclusive session directory (via
   ``stateful_decode_and_sync.calibration_task.prepare_session``; the profile is
   the linear rest/two-action MVP by default, or a hub-and-spoke band profile
   when ``--gestures`` is given);
2. prints the exact ``synapsectl start`` line for the operator to run, and gates
   on an operator-supplied ``synapsectl info`` capture proving the device App
   ``stateful-decode-and-sync`` reports ``Running: True``;
3. launches the two host child processes -- ``run_service.py`` (NDJSON control
   service) and the Reactions WebSocket bridge that owns the built C++ recorder.

The operator remains responsible for running ``synapsectl start`` and
``synapsectl info`` themselves (see AGENTS.md, Synapse CLI execution boundary and
docs/calibration-task-workflow.md). ``--dry-run`` prints every command the
launcher would issue -- the operator ``synapsectl`` line and both child process
lines -- and exits without generating a session or starting anything.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from stateful_decode_and_sync.calibration_task import load_profile, prepare_session

# Repository root is four levels up from apps/stateful-decode-and-sync/client.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_DIR = Path(__file__).resolve().parent
_APP_NAME = "stateful-decode-and-sync"


_SESSION_ARTIFACTS = ("device-config.json", "task-profile.json", "provenance.json")


def build_profile(gestures, base_config, output_dir, provenance):
    """Return the profile to embed; linear MVP unless ``gestures`` is supplied."""
    profile = None
    if gestures:
        # Imported lazily so a linear-MVP launch does not require the MOTION_LUT.
        from stateful_decode_and_sync.motion_profile import make_hub_spoke_profile
        profile = make_hub_spoke_profile(gestures)
    return prepare_session(base_config, output_dir, provenance_path=provenance, profile=profile)


def obtain_session(gestures, base_config, output_dir, provenance):
    """Generate the session, or reuse an already-generated one at ``output_dir``.

    The two-pass operator flow runs this launcher twice with the *same*
    ``--session-dir``: first to generate and print the ``synapsectl start`` line,
    then again with ``--info-capture`` to gate and launch. ``prepare_session``
    creates the directory exclusively, so the second pass would collide with the
    first. When the directory already holds a complete, valid session, reuse it
    (validating ``task-profile.json`` via ``load_profile``) instead of
    regenerating. Returns ``(profile, reused)``.

    A directory that exists but lacks any of the three expected artifacts is a
    conflict, not a reusable session, and is left to ``prepare_session`` to
    reject rather than silently launched against. Extra files are tolerated so
    the operator may save the ``synapsectl info`` capture inside the session
    directory (as the documented workflow does) without defeating reuse.
    """
    session = Path(output_dir)
    if session.exists():
        present = {p.name for p in session.iterdir()}
        if set(_SESSION_ARTIFACTS) <= present:
            profile = load_profile(session / "task-profile.json")
            return profile, True
        # Fall through: an existing directory missing a core artifact is a
        # conflict; prepare_session's exclusive create surfaces it clearly.
    return build_profile(gestures, base_config, session, provenance), False


def synapsectl_start_line(device_uri, config_path):
    """The exact operator command that deploys and starts the device App.

    Returned as an argv list; the operator runs this, not the launcher."""
    return ["synapsectl", "-u", device_uri, "start", str(config_path)]


def app_running(info_text, app_name=_APP_NAME):
    """True when a ``synapsectl info`` capture shows the App reporting Running: True.

    Parses the operator-supplied text rather than querying the device. The App
    section is matched by name; the first ``Running: <bool>`` line after that
    name (before the next application entry) is its running state. Any other
    application's ``Running`` line is ignored so a running device with a stopped
    App is correctly rejected (the standing bench blocker)."""
    lines = info_text.splitlines()
    for index, line in enumerate(lines):
        if app_name not in line:
            continue
        for follow in lines[index + 1:]:
            match = re.search(r"Running\s*:\s*(True|False)", follow, re.IGNORECASE)
            if match:
                return match.group(1).lower() == "true"
            # A new "Running:" belonging to another application ends this block;
            # handled by the first-match return above. Stop at a blank separator.
            if not follow.strip():
                break
    return False


def service_command(python, device_ip, service_port):
    return [python, str(_CLIENT_DIR / "run_service.py"),
            "--device-ip", device_ip, "--port", str(service_port)]


def bridge_command(python, args, session_dir):
    return [python, str(_CLIENT_DIR / "run_reactions_bridge.py"),
            "--device-uri", args.device_tap, "--recorder", str(Path(args.recorder).resolve()),
            "--profile", str(session_dir / "task-profile.json"),
            "--provenance", str(session_dir / "provenance.json"),
            "--output-root", str(Path(args.output_root).resolve()),
            "--service-port", str(args.service_port), "--port", str(args.bridge_port),
            *sum((["--origin", origin] for origin in args.origin), []),
            *(["--passive"] if getattr(args, "passive", False) else []),
            *(["--block", str(args.block)] if getattr(args, "block", None) else [])]


def _print_lines(header, commands):
    print(header)
    for command in commands:
        print("  " + " ".join(command))


def main(argv=None, *, spawn=subprocess.Popen):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device-uri", required=True,
                        help="device address for the operator synapsectl start line, e.g. 192.168.100.157")
    parser.add_argument("--device-tap", required=True,
                        help="device tap address the recorder/bridge connect to, e.g. 192.168.100.157:647")
    parser.add_argument("--base-config", default=str(_REPO_ROOT / "config" / "rhd2132.json"),
                        help="base device configuration to embed the task definition into")
    parser.add_argument("--provenance", default=str(_REPO_ROOT / "config" / "calibration-provenance.template.json"),
                        help="provenance metadata carried into the session")
    parser.add_argument("--session-dir", required=True,
                        help="fresh (nonexistent) session directory to generate into")
    parser.add_argument("--gestures", nargs="+", metavar="MOTION_KEY",
                        help="MOTION_LUT CamelCase keys for a hub-and-spoke band profile; "
                             "omit for the linear rest/two-action MVP")
    parser.add_argument("--recorder", default=str(_REPO_ROOT / "build" / "raw-recorder" / "task-recorder"),
                        help="path to the built C++ raw recorder")
    parser.add_argument("--origin", action="append", required=True,
                        help="exact Reactions page origin, e.g. https://chr.nml.wtf (repeatable)")
    parser.add_argument("--service-port", type=int, default=18765)
    parser.add_argument("--bridge-port", type=int, default=9999)
    parser.add_argument("--output-root", default=str(_REPO_ROOT / "data" / "reactions"),
                        help="root under which the bridge creates its exclusive recording directory")
    parser.add_argument("--info-capture",
                        help="path to a saved 'synapsectl info' capture proving the App is Running; "
                             "required unless --dry-run")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter used for the host child processes")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the operator synapsectl line and child commands, then exit; "
                             "generates no session and starts nothing")
    parser.add_argument("--passive", action="store_true",
                        help="passive mode: the bridge records broadband + host-clock browser "
                             "annotations into a Cognescent data.hdf5 (no C++ recorder, no device "
                             "task round trip). The device App is still started for the broadband tap.")
    parser.add_argument("--block", type=int, default=1,
                        help="passive mode: starting block index for the Cognescent folder name; "
                             "increments on each stop and reconciles with the browser block")
    args = parser.parse_args(argv)

    start_line = synapsectl_start_line(args.device_uri, Path(args.session_dir) / "device-config.json")
    if args.dry_run:
        _print_lines("Operator runs (device control; NOT run by this launcher):", [start_line])
        _print_lines("Then this launcher would start:", [
            service_command(args.python, args.device_uri, args.service_port),
            bridge_command(args.python, args, Path(args.session_dir))])
        print("Dry run: no session generated, no processes started.")
        return 0

    session_dir = Path(args.session_dir)
    try:
        profile, reused = obtain_session(args.gestures, args.base_config, session_dir, args.provenance)
    except (OSError, ValueError) as error:
        parser.error(f"cannot generate calibration session: {error}; choose a fresh --session-dir")
    verb = "Reused existing session" if reused else "Generated session"
    print(f"{verb} {session_dir}; definition {profile['definition_hash']}. "
          "No device commands executed.")

    _print_lines("Operator step -- run this yourself to deploy and start the device App:", [start_line])
    print(f"Then capture 'synapsectl -u {args.device_uri} info' to a file and rerun with "
          "--info-capture <file> (the same generated --session-dir).")

    if not args.info_capture:
        print("Stopping before host processes: supply --info-capture once the operator confirms "
              f"Application {_APP_NAME} Running: True.", file=sys.stderr)
        return 2
    try:
        info_text = Path(args.info_capture).read_text(encoding="utf-8-sig")
    except OSError as error:
        parser.error(f"cannot read --info-capture: {error}")
    if not app_running(info_text):
        print(f"Device gate failed: {args.info_capture} does not show Application {_APP_NAME} "
              "Running: True. Resolve the device App start before launching host processes.",
              file=sys.stderr)
        return 3

    service = service_command(args.python, args.device_uri, args.service_port)
    bridge = bridge_command(args.python, args, session_dir)
    _print_lines("Device App Running confirmed. Launching host processes:", [service, bridge])
    service_process = spawn(service)
    try:
        bridge_process = spawn(bridge)
    except BaseException:
        service_process.terminate()
        raise
    print(f"Control service pid {service_process.pid} (port {args.service_port}); "
          f"bridge pid {bridge_process.pid} (port {args.bridge_port}).")
    print("Connect the Reactions page, run the calibration, then stop with Ctrl-C. "
          "Analyze offline with analyze_recording.py against the returned session_dir.")
    try:
        bridge_process.wait()
    finally:
        service_process.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
