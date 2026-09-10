import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from calibration_task import main
from scifi2_hub_manager.calibration_task import make_profile
from scifi2_hub_manager.client import SocketClientError


class PrepareTests(unittest.TestCase):
    def test_connection_failure_is_journaled_without_task_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / "profile.json"
            profile.write_text(json.dumps(make_profile()))
            journal = root / "instructor.ndjson"
            args = ["calibration_task", "run", "--profile", str(profile), "--journal", str(journal), "--port", "18765"]
            with patch("sys.argv", args), patch("calibration_task.NdjsonClient") as client, contextlib.redirect_stderr(io.StringIO()) as errors:
                client.return_value.connect.side_effect = SocketClientError("connection refused")
                with self.assertRaises(SystemExit) as result:
                    main()
                self.assertEqual(result.exception.code, 2)
                client.return_value.close.assert_called_once()
                client.return_value.request.assert_not_called()
            self.assertIn("--port 18765", errors.getvalue())
            records = [json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual(records[-1]["kind"], "instructor_failed")
            self.assertEqual(records[-1]["stage"], "connect")

    def test_missing_provenance_does_not_create_partial_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base.json"
            base.write_text(json.dumps({"nodes": [{"application": {"name": "scifi2-hub-manager"}}]}))
            args = ["calibration_task", "prepare", "--base-config", str(base),
                    "--provenance", str(root / "missing.json"), "--output-dir", str(root / "out")]
            with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit) as result:
                    main()
            self.assertEqual(result.exception.code, 2)
            self.assertIn("cannot prepare calibration session", errors.getvalue())
            self.assertFalse((root / "out").exists())

    def test_prepare_and_existing_directory_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base = root / "base.json"
            base.write_text(json.dumps({"nodes": [{"application": {"name": "scifi2-hub-manager"}}]}))
            args = ["calibration_task", "prepare", "--base-config", str(base), "--output-dir", str(root / "out")]
            with patch("sys.argv", args), contextlib.redirect_stdout(io.StringIO()):
                main()
            files = {p.name: p.read_bytes() for p in (root / "out").iterdir()}
            self.assertEqual(set(files), {"device-config.json", "task-profile.json", "provenance.json"})
            with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    main()
            self.assertEqual(files, {p.name: p.read_bytes() for p in (root / "out").iterdir()})
