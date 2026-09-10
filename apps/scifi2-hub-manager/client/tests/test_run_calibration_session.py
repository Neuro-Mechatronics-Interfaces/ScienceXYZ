"""Hardware-free tests for the calibration session launcher.

No synapsectl and no device are ever contacted: the operator device-start line
is only printed, the device gate parses an operator-supplied info capture, and
child processes are started through an injected fake ``spawn``.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_calibration_session as launcher
from scifi2_hub_manager.calibration_task import make_profile


APP = "scifi2-hub-manager"
INFO_RUNNING = f"""
Device
  Status: Running
Applications
  {APP}
    Running: True
"""
INFO_APP_STOPPED = f"""
Device
  Status: Running
Applications
  {APP}
    Running: False
"""
# Overall device running but a *different* app running; the target App absent.
INFO_OTHER_APP = """
Device
  Status: Running
Applications
  some-other-app
    Running: True
"""


class FakeProcess:
    def __init__(self, argv):
        self.argv = argv
        self.pid = 4321
        self.terminated = False
        self.waited = False

    def wait(self):
        self.waited = True

    def terminate(self):
        self.terminated = True


class SpawnRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        process = FakeProcess(argv)
        self.calls.append(process)
        return process


def _base_config(root):
    path = root / "base.json"
    path.write_text(json.dumps({"nodes": [{"application": {"name": APP}}]}))
    return path


def _common_args(root, session, base, **overrides):
    args = ["--device-uri", "192.168.100.157", "--device-tap", "192.168.100.157:647",
            "--base-config", str(base), "--session-dir", str(session),
            "--recorder", str(root / "task-recorder"),
            "--origin", "https://chr.nml.wtf"]
    for key, value in overrides.items():
        args += ["--" + key.replace("_", "-"), str(value)]
    return args


class AppRunningGateTests(unittest.TestCase):
    def test_legacy_app_does_not_satisfy_renamed_app_gate(self):
        legacy = INFO_RUNNING.replace(APP, "stateful-decode-and-sync")
        self.assertFalse(launcher.app_running(legacy))

    def test_wire_identity_survives_python_package_rename(self):
        from scifi2_hub_manager import gui_control_pb2
        self.assertEqual(gui_control_pb2.ControlCommand.DESCRIPTOR.full_name,
                         "stateful_decode_and_sync.v1.ControlCommand")

    def test_true_only_for_named_app_running(self):
        self.assertTrue(launcher.app_running(INFO_RUNNING))
        self.assertFalse(launcher.app_running(INFO_APP_STOPPED))
        self.assertFalse(launcher.app_running(INFO_OTHER_APP))
        self.assertFalse(launcher.app_running(""))


class DryRunTests(unittest.TestCase):
    def test_dry_run_prints_operator_and_child_lines_without_spawning(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            args = _common_args(root, session, base) + ["--dry-run"]
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = launcher.main(args, spawn=spawn)
            text = out.getvalue()
            self.assertEqual(code, 0)
            self.assertEqual(spawn.calls, [])  # nothing started
            self.assertFalse(session.exists())  # nothing generated
            self.assertIn("synapsectl -u 192.168.100.157 start", text)
            self.assertIn("run_service.py", text)
            self.assertIn("run_reactions_bridge.py", text)


class GateStopTests(unittest.TestCase):
    def test_generation_is_exclusive_and_stops_without_info_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            args = _common_args(root, session, base)
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = launcher.main(args, spawn=spawn)
            self.assertEqual(code, 2)  # stopped before host processes
            self.assertEqual(spawn.calls, [])
            self.assertIn("--info-capture", err.getvalue())
            files = {p.name for p in session.iterdir()}
            self.assertEqual(files, {"device-config.json", "task-profile.json", "provenance.json"})

    def test_rerun_reuses_complete_session_without_regenerating(self):
        # The two-pass operator flow reruns the same --session-dir; a complete
        # existing session is reused (never overwritten) rather than colliding
        # on prepare_session's exclusive create.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            args = _common_args(root, session, base)
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                first = launcher.main(args, spawn=spawn)
            self.assertEqual(first, 2)
            before = (session / "task-profile.json").read_text()
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                second = launcher.main(args, spawn=spawn)
            self.assertEqual(second, 2)  # reused, still gated on --info-capture
            self.assertEqual(spawn.calls, [])
            self.assertEqual((session / "task-profile.json").read_text(), before)  # untouched
            self.assertIn("Reused existing session", out.getvalue())

    def test_conflicting_existing_directory_is_rejected(self):
        # A directory that exists but is not a complete session is a conflict,
        # not something to launch against.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            session.mkdir()
            (session / "device-config.json").write_text("{}")  # partial: missing the rest
            args = _common_args(root, session, base)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    launcher.main(args, spawn=SpawnRecorder())

    def test_app_not_running_capture_refuses_to_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            capture = root / "info.txt"
            capture.write_text(INFO_APP_STOPPED)
            args = _common_args(root, session, base, info_capture=capture)
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = launcher.main(args, spawn=spawn)
            self.assertEqual(code, 3)
            self.assertEqual(spawn.calls, [])
            self.assertIn("Running: True", err.getvalue())


class LaunchTests(unittest.TestCase):
    def test_running_capture_spawns_service_then_bridge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            capture = root / "info.txt"
            capture.write_text(INFO_RUNNING)
            args = _common_args(root, session, base, info_capture=capture,
                                output_root=root / "reactions", python="py-test")
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()):
                code = launcher.main(args, spawn=spawn)
            self.assertEqual(code, 0)
            self.assertEqual(len(spawn.calls), 2)
            service, bridge = spawn.calls
            self.assertIn("run_service.py", " ".join(service.argv))
            self.assertIn("--port", service.argv)
            self.assertEqual(service.argv[service.argv.index("--port") + 1], str(18765))
            self.assertIn("run_reactions_bridge.py", " ".join(bridge.argv))
            self.assertIn("--origin", bridge.argv)
            self.assertEqual(bridge.argv[bridge.argv.index("--origin") + 1], "https://chr.nml.wtf")
            self.assertEqual(bridge.argv[bridge.argv.index("--port") + 1], str(9999))
            self.assertEqual(bridge.argv[0], "py-test")
            # The bridge is waited on; the service is torn down when it returns.
            self.assertTrue(bridge.waited)
            self.assertTrue(service.terminated)

    def test_hub_spoke_profile_embeds_gestures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = _base_config(root)
            session = root / "session"
            capture = root / "info.txt"
            capture.write_text(INFO_RUNNING)
            args = _common_args(root, session, base, info_capture=capture) + [
                "--gestures", "Fist", "Paper"]
            spawn = SpawnRecorder()
            with contextlib.redirect_stdout(io.StringIO()):
                code = launcher.main(args, spawn=spawn)
            self.assertEqual(code, 0)
            profile = json.loads((session / "task-profile.json").read_text())
            self.assertEqual(profile["profile_shape"], "hub_and_spoke")
            self.assertEqual(profile["gesture_keys"], ["Fist", "Paper"])
            # Linear MVP differs, so this is genuinely the hub-and-spoke profile.
            self.assertNotEqual(profile["definition_hash"], make_profile()["definition_hash"])


if __name__ == "__main__":
    unittest.main()
