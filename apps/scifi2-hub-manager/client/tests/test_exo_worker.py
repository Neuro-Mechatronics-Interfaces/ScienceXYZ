"""Hardware-free tests for the exo worker and its NDJSON service.

Drives the real ``nml_hand_exo.HandExo`` against an in-process fake transport
(:class:`tests.fake_exo_comm.FakeExoComm`), so the SDK's command formatting and
firmware gates are exercised with no serial port present.  The SDK lives in the
``third_party/exo`` submodule; its ``src`` is placed on ``sys.path`` here so
``unittest discover`` picks these up in a bare checkout, and the whole module is
skipped when the SDK is not importable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest

# Make the SDK importable from the submodule for an in-repo run.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *[os.pardir] * 4))
_EXO_SRC = os.path.join(_REPO_ROOT, "third_party", "exo", "src")
if os.path.isdir(_EXO_SRC) and _EXO_SRC not in sys.path:
    sys.path.insert(0, _EXO_SRC)

try:
    import nml_hand_exo  # noqa: F401
    _HAVE_SDK = True
except Exception:
    _HAVE_SDK = False

from fake_exo_comm import FakeExoComm

from scifi2_hub_manager.exo_service import ExoService
from scifi2_hub_manager.exo_worker import (
    JOINT_ORDER,
    ExoConfig,
    ExoWorker,
)


def _make_worker(firmware: str = "0.6.4", **overrides):
    comm = FakeExoComm(firmware=firmware)
    config = ExoConfig(
        comm_factory=lambda: comm,
        # Keep tests deterministic: no background poll/watchdog unless asked.
        watchdog_s=overrides.pop("watchdog_s", None),
        poll_interval_s=overrides.pop("poll_interval_s", None),
        **overrides,
    )
    return ExoWorker(config), comm


@unittest.skipUnless(_HAVE_SDK, "nml_hand_exo SDK not importable")
class ExoWorkerTest(unittest.TestCase):
    def setUp(self):
        self.worker, self.comm = _make_worker()
        self.states: list = []
        self.worker.on_state(self.states.append)

    def tearDown(self):
        self.worker.shutdown()

    def test_connect_reports_firmware_ok(self):
        state = self.worker.connect()
        self.assertTrue(state.connected)
        self.assertTrue(state.firmware_ok)
        self.assertIn("0.6.4", state.firmware)

    def test_old_firmware_blocks_arming(self):
        worker, _ = _make_worker(firmware="0.5.0")
        try:
            worker.connect()
            self.assertFalse(worker.state.firmware_ok)
            with self.assertRaises(Exception):
                worker.arm(home=False)
        finally:
            worker.shutdown()

    def test_arm_applies_current_budget_before_enable(self):
        worker, comm = _make_worker(total_current_ma=800, per_motor_current_ma=250)
        try:
            worker.connect()
            worker.arm(home=False)
            # Budget goes out before the per-motor limit, both before enable.
            budget = comm.sent.index("set_total_current_lim:800")
            per_motor = comm.sent.index("set_current_lim:all:250")
            enable = comm.sent.index("enable:all")
            self.assertLess(budget, per_motor)
            self.assertLess(per_motor, enable)
            self.assertTrue(worker.state.armed)
        finally:
            worker.shutdown()

    def test_set_finger_angles_requires_arm(self):
        self.worker.connect()
        with self.assertRaises(Exception):
            self.worker.set_finger_angles({"index": 50})

    def test_set_finger_angles_writes_batch_command(self):
        self.worker.connect()
        self.worker.arm(home=False)
        self.worker.set_finger_angles({"thumb": 70, "index": -40, "wrist": 0})
        batch = [c for c in self.comm.sent if c.startswith("set_finger_angles:")]
        self.assertEqual(len(batch), 1)
        # thumb=70, index=-40, middle/ring/pinky held (empty), wrist=0.
        self.assertEqual(batch[0], "set_finger_angles:70:-40::::0")
        self.assertEqual(self.worker.state.commanded["thumb"], 70)
        self.assertEqual(self.worker.state.commanded["index"], -40)

    def test_finger_value_out_of_range_rejected_without_write(self):
        self.worker.connect()
        self.worker.arm(home=False)
        before = list(self.comm.sent)
        with self.assertRaises(ValueError):
            self.worker.set_finger_angles({"index": 200})
        self.assertEqual(self.comm.sent, before)

    def test_read_pose_populates_state(self):
        self.worker.connect()
        state = self.worker.read_pose()
        self.assertEqual(set(state.pose), set(JOINT_ORDER))
        self.assertEqual(state.pose["ring"]["fraction"], 100)

    def test_watchdog_eases_to_neutral_when_idle(self):
        worker, comm = _make_worker(watchdog_s=0.05, poll_interval_s=None)
        try:
            worker.connect()
            worker.arm(home=False)
            worker.set_finger_angles({"index": 80})
            # Wait past the idle gap; the worker thread should ease to neutral.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not worker.state.watchdog_tripped:
                time.sleep(0.02)
            self.assertTrue(worker.state.watchdog_tripped)
            neutral = [c for c in comm.sent if c == "set_finger_angles:0:0:0:0:0:0"]
            self.assertGreaterEqual(len(neutral), 1)
        finally:
            worker.shutdown()

    def test_disconnect_disarms(self):
        self.worker.connect()
        self.worker.arm(home=False)
        self.worker.disconnect()
        self.assertIn("disable:all", self.comm.sent)
        self.assertFalse(self.worker.state.connected)
        self.assertFalse(self.worker.state.armed)


@unittest.skipUnless(_HAVE_SDK, "nml_hand_exo SDK not importable")
class ExoServiceTest(unittest.TestCase):
    def _roundtrip(self, requests: list[dict]) -> list[dict]:
        async def run():
            worker, _ = _make_worker()
            service = ExoService(worker, host="127.0.0.1", port=0)
            await service.start()
            host, port = service._server.sockets[0].getsockname()[:2]
            reader, writer = await asyncio.open_connection(host, port)
            replies = []
            try:
                for req in requests:
                    writer.write((json.dumps(req) + "\n").encode())
                    await writer.drain()
                    line = await asyncio.wait_for(reader.readline(), timeout=5)
                    replies.append(json.loads(line))
            finally:
                writer.close()
                await service.stop()
            return replies

        return asyncio.run(run())

    def test_connect_then_arm_then_pose(self):
        replies = self._roundtrip([
            {"protocol_version": 1, "request_id": "1", "command": "exo_connect"},
            {"protocol_version": 1, "request_id": "2", "command": "exo_arm", "home": False},
            {"protocol_version": 1, "request_id": "3", "command": "exo_set_finger_angles",
             "values": {"index": 60}},
            {"protocol_version": 1, "request_id": "4", "command": "exo_read_pose"},
        ])
        self.assertEqual([r["status"] for r in replies], ["succeeded"] * 4)
        self.assertTrue(replies[0]["state"]["connected"])
        self.assertTrue(replies[1]["state"]["armed"])
        self.assertEqual(replies[2]["state"]["commanded"]["index"], 60)
        self.assertEqual(set(replies[3]["state"]["pose"]), set(JOINT_ORDER))

    def test_invalid_values_rejected(self):
        replies = self._roundtrip([
            {"protocol_version": 1, "request_id": "1", "command": "exo_connect"},
            {"protocol_version": 1, "request_id": "2", "command": "exo_arm", "home": False},
            {"protocol_version": 1, "request_id": "3", "command": "exo_set_finger_angles",
             "values": {"bogus": 10}},
        ])
        self.assertEqual(replies[2]["status"], "failed")
        self.assertEqual(replies[2]["error"]["code"], "invalid_argument")

    def test_unknown_command(self):
        replies = self._roundtrip([
            {"protocol_version": 1, "request_id": "1", "command": "exo_nope"},
        ])
        self.assertEqual(replies[0]["status"], "failed")
        self.assertEqual(replies[0]["error"]["code"], "unknown_command")

    def test_bad_protocol_version(self):
        replies = self._roundtrip([
            {"protocol_version": 2, "request_id": "1", "command": "exo_connect"},
        ])
        self.assertEqual(replies[0]["error"]["code"], "unsupported_version")


if __name__ == "__main__":
    unittest.main()
