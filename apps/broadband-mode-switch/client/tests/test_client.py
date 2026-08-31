import json
import socket
import threading
import unittest

from broadband_mode_switch.client import NdjsonClient
from calibration_prompter import calibrate


class SocketClientTests(unittest.TestCase):
    def test_request_and_state_snapshot_handle_interleaved_events(self):
        client_socket, server_socket = socket.socketpair()
        client = NdjsonClient(timeout=1)
        client._socket = client_socket
        client._file = client_socket.makefile("rb")

        def serve():
            with server_socket, server_socket.makefile("rb") as incoming:
                for line in incoming:
                    request = json.loads(line)
                    result = {
                        "type": "result", "protocol_version": 1,
                        "request_id": request["request_id"],
                        "command": request["command"], "status": "succeeded",
                        "state_version": "4", "error": None,
                    }
                    server_socket.sendall((json.dumps(result) + "\n").encode())
                    if request["command"] == "subscribe_state":
                        state = {
                            "type": "state", "protocol_version": 1,
                            "state_version": "4", "timestamp_ns": "5",
                            "pipeline": {"state": "ready", "source_mode": "sampling"},
                            "active": {"collection_id": 0, "label": 2, "capture_enabled": False},
                            "collections": [], "model": {"phase": "idle", "ready": False,
                            "source_collection_id": None, "source_generation": None, "stale": False,
                            "epoch": 0, "total_epochs": 0, "loss": 0.0, "accuracy": 0.0,
                            "duration_ms": "0"}, "last_error": None,
                        }
                        server_socket.sendall((json.dumps(state) + "\n").encode())

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            self.assertEqual(client.request("select_label", label=2)["status"], "succeeded")
            state = client.get_state_snapshot()
            self.assertEqual(state["pipeline"]["state"], "ready")
            self.assertEqual(state["active"]["label"], 2)
        finally:
            client.close()
            thread.join(1)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def get_state_snapshot(self):
        self.calls.append(("get_state_snapshot",))
        return {"collections": [{"collection_id": 0, "labels": [{"label": 1}, {"label": 2}]}]}

    def prepare_capture(self, collection_id, label, enabled):
        self.calls.append(("prepare_capture", collection_id, label, enabled))

    def set_capture(self, enabled):
        self.calls.append(("set_capture", enabled))


class CalibrationPrompterTests(unittest.TestCase):
    def test_target_changes_are_atomic_and_capture_is_disabled_first(self):
        client = RecordingClient()
        prompts = []
        calibrate(client, [(0, 1), (0, 2)], prompt=prompts.append)
        self.assertEqual(
            client.calls,
            [("get_state_snapshot",),
             ("prepare_capture", 0, 1, False), ("set_capture", True), ("set_capture", False),
             ("prepare_capture", 0, 2, False), ("set_capture", True), ("set_capture", False)],
        )
        self.assertEqual(len(prompts), 4)

    def test_capture_is_disabled_when_prompt_fails(self):
        client = RecordingClient()
        prompts = iter(["ready", "fail"])
        def prompt(_):
            if next(prompts) == "fail":
                raise RuntimeError("stop")
        with self.assertRaises(RuntimeError):
            calibrate(client, [(0, 1)], prompt=prompt)
        self.assertEqual(client.calls[-1], ("set_capture", False))


if __name__ == "__main__":
    unittest.main()
