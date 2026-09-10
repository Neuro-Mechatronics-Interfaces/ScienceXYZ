import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from scifi2_hub_manager.calibration_task import Journal, TaskInstructor, make_profile
from scifi2_hub_manager.client import SocketClientError
from scifi2_hub_manager.reactions_bridge import BridgeSession


class TaskClient:
    timeout = .1
    host = "127.0.0.1"
    port = 8765

    def __init__(self):
        self.profile = make_profile()
        self.task = {"configured": True, "definition_hash": self.profile["definition_hash"], "app_session_id": "fixture",
                     "run_sequence": 0, "transition_sequence": 0, "current_state_id": None, "lifecycle": "idle"}
        self.event_sequence = 0
        self.calls = []
        self.wrong_request = False

    def connect(self): pass
    def close(self): pass
    def subscribe_task_transitions(self, enabled): self.calls.append("subscribe")
    def get_state_snapshot(self): return {"task": dict(self.task)}
    def set_capture(self, enabled): self.calls.append(("capture", enabled))

    def request(self, command, request_id, **arguments):
        self.calls.append(command)
        previous = self.task["current_state_id"] or 0
        if command == "start_task":
            self.task.update(run_sequence=self.task["run_sequence"] + 1, transition_sequence=1,
                             current_state_id=1, lifecycle="running")
            kind = "start"
        else:
            self.task["transition_sequence"] += 1
            self.task["current_state_id"] = previous + 1 if command == "propose_task_event" else None
            self.task["lifecycle"] = "completed" if self.task["current_state_id"] == 4 else "running"
            kind = "transition" if command == "propose_task_event" else "reset"
        self.event_sequence += 1
        self.event = {"request_id": "wrong" if self.wrong_request else request_id,
                      "app_session_id": "fixture", "run_sequence": str(self.task["run_sequence"]),
                      "transition_sequence": str(self.task["transition_sequence"]),
                      "event_sequence": str(self.event_sequence), "definition_hash": self.profile["definition_hash"],
                      "event_kind": kind, "current_state_id": self.task["current_state_id"] or 0,
                      "previous_state_id": previous, "effective_frame": {"source_id": "rhd2132",
                      "sequence_number": str(self.event_sequence), "timestamp_ns": str(self.event_sequence * 50000)}}
        return {"status": "succeeded", "request_id": request_id}

    def wait_for_task_transition(self, timeout): return self.event


class FakeRecorder:
    def __init__(self, journal): self.journal, self.starts, self.stops = journal, 0, 0
    def check_running(self):
        if self.starts <= self.stops:
            raise RuntimeError("recorder is no longer running")
    async def start(self, metadata):
        self.starts += 1
        self.metadata = metadata
        return {"recording": True}
    async def stop(self):
        self.stops += 1
        return {"recording": False, "local_stop_ok": True}


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.journal = Journal(self.path / "events.ndjson")
        self.recorder = FakeRecorder(self.journal)
        self.client = TaskClient()
        self.session = BridgeSession(self.path, make_profile(), {"fixture": True}, self.recorder, client=self.client)

    async def asyncTearDown(self):
        # Don't invoke real NDJSON cleanup from the test fake.
        self.session.instructor = None
        await self.session.close()
        self.temp.cleanup()

    def request(self, request_id, command):
        return {"api_version": "0.12", "api_request": {"request_id": request_id,
                "sciencexyz_request": {"command": command}}}

    async def test_task_socket_client_envelope_over_real_websocket(self):
        async def handler(socket):
            async for raw in socket:
                await socket.send(json.dumps(await self.session.handle(json.loads(raw))))
        async with serve(handler, "127.0.0.1", 0, origins=["http://localhost:8080"]) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}", origin="http://localhost:8080") as socket:
                for request_id, command in enumerate(["start_recording", "start_task", "propose_task_event"], 1):
                    await socket.send(json.dumps(self.request(request_id, command)))
                    reply = json.loads(await socket.recv())
                    self.assertEqual(reply["api_response"]["request_id"], request_id)
                    self.assertTrue(reply["api_response"]["success"])
                self.assertEqual(reply["api_response"]["result"]["committed_event"]["current_state_id"], 2)
        self.assertLess(self.client.calls.index("subscribe"), self.client.calls.index("start_task"))

    async def test_duplicate_is_not_replayed(self):
        request = self.request(1, "start_recording")
        await self.session.handle(request)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            await self.session.handle(request)
        self.assertEqual(self.recorder.starts, 1)

    async def test_browser_samples_journaled_without_becoming_task_labels(self):
        await self.session.handle({"api_version": "0.12", "api_request": {"request_id": 1,
            "start_stream_request": {"stream_id": "browser_events", "app_id": "nml-wtf-web", "browser_events": {}}}})
        payload = {"api_version": "0.12", "stream_batch": {"stream_id": "browser_events",
            "browser_events": {"samples": [{"time": 1234567.125, "data": {"name": "target", "label": "action_a"}}]}}}
        await self.session.handle(payload)
        lines = [json.loads(line) for line in (self.path / "events.ndjson").read_text().splitlines()]
        self.assertEqual(lines[-1]["message"], payload)
        self.assertNotIn("start_task", self.client.calls)

    async def test_requires_recording_before_task(self):
        with self.assertRaisesRegex(ValueError, "start recording"):
            await self.session.handle(self.request(1, "start_task"))

    async def test_dead_recorder_rejects_task_before_device_command(self):
        await self.session.handle(self.request(1, "start_recording"))
        self.recorder.stops = 1
        with self.assertRaisesRegex(RuntimeError, "no longer running"):
            await self.session.handle(self.request(2, "start_task"))
        self.assertNotIn("start_task", self.client.calls)

    async def test_legacy_recording_control_supported(self):
        await self.session.handle({"api_version": "0.12", "api_request": {"request_id": 1,
            "start_stream_request": {"stream_id": "recording_control"}}})
        def samples(enabled):
            return {"api_version": "0.12", "stream_batch": {"stream_id": "recording_control",
                "recording_control": {"samples": [{"time": 99, "data": {"enabled": enabled}}]}}}
        await self.session.handle(samples(True))
        await self.session.handle(samples(False))
        self.assertEqual((self.recorder.starts, self.recorder.stops), (1, 1))

    async def test_instruction_waits_for_matching_request(self):
        instructor = TaskInstructor(self.client, make_profile(), self.journal)
        instructor.prepare()
        self.client.wrong_request = True
        with self.assertRaisesRegex(SocketClientError, "Unexpected concurrent"):
            instructor.command("start_task")


if __name__ == "__main__": unittest.main()
