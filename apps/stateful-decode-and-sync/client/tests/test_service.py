import asyncio
import json
import unittest

from stateful_decode_and_sync.model import AppState, CommandResult
from stateful_decode_and_sync.service import ControlService, _Client


class DummyController:
    def __init__(self):
        self.calls = []
        self.connected = True

    def on_state(self, callback):
        self.state_callback = callback

    def on_result(self, callback):
        self.result_callback = callback

    def connect(self):
        pass

    def disconnect(self):
        pass

    def _result(self, command, request_id):
        self.calls.append((command, request_id))
        return CommandResult(1, request_id, command, "succeeded", 9)

    def get_state(self, request_id=None): return self._result("get_state", request_id)
    def subscribe_state(self, enabled, request_id=None): return self._result("subscribe_state", request_id)
    def prepare_capture(self, collection_id, label, enabled, request_id=None): return self._result("prepare_capture", request_id)
    def select_collection(self, collection_id, request_id=None): return self._result("select_collection", request_id)
    def select_label(self, label, request_id=None): return self._result("select_label", request_id)
    def set_capture(self, enabled, request_id=None): return self._result("set_capture", request_id)
    def fit(self, epochs=0, request_id=None): return self._result("fit", request_id)
    def flush(self, scope, collection_id=None, label=None, request_id=None): return self._result("flush", request_id)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = DummyController()
        self.service = ControlService(self.controller, port=0)
        await self.service.start()
        self.port = self.service._server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.service.stop()

    async def test_fragmented_and_coalesced_requests(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        first = {"protocol_version": 1, "request_id": "one", "command": "get_state"}
        second = {"protocol_version": 1, "request_id": "two", "command": "select_label", "label": 3}
        data = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()
        writer.write(data[:7]); await writer.drain(); await asyncio.sleep(0)
        writer.write(data[7:]); await writer.drain()
        answers = [json.loads((await reader.readline()).decode()), json.loads((await reader.readline()).decode())]
        self.assertEqual([x["request_id"] for x in answers], ["one", "two"])
        self.assertEqual(answers[1]["status"], "succeeded")
        writer.close(); await writer.wait_closed()

    async def test_bad_json_and_version_are_rejected(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"not json\n" + json.dumps({"protocol_version": 2, "request_id": "v", "command": "get_state"}).encode() + b"\n")
        await writer.drain()
        bad, version = json.loads((await reader.readline()).decode()), json.loads((await reader.readline()).decode())
        self.assertEqual(bad["error"]["code"], "malformed")
        self.assertEqual(version["error"]["code"], "unsupported_version")
        writer.close(); await writer.wait_closed()

    async def test_two_clients_keep_request_correlation_separate(self):
        peers = [await asyncio.open_connection("127.0.0.1", self.port) for _ in range(2)]
        requests = [
            {"protocol_version": 1, "request_id": "same", "command": "select_label", "label": index}
            for index in (1, 2)
        ]
        for (_, writer), request in zip(peers, requests):
            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()
        answers = [json.loads((await reader.readline()).decode()) for reader, _ in peers]
        self.assertEqual([answer["request_id"] for answer in answers], ["same", "same"])
        self.assertEqual([call[0] for call in self.controller.calls], ["select_label", "select_label"])
        for _, writer in peers:
            writer.close(); await writer.wait_closed()

    async def test_slow_subscriber_keeps_only_latest_pending_snapshot(self):
        client = _Client(object(), subscribed=True)
        self.service._clients.add(client)
        started = asyncio.Event()
        release = asyncio.Event()
        delivered = []

        async def slow_write(target, value):
            started.set()
            await release.wait()
            delivered.append(value["state_version"])

        self.service._write = slow_write
        self.service._broadcast_state(AppState(state_version=1))
        await asyncio.wait_for(started.wait(), 1)
        for version in range(2, 20):
            self.service._broadcast_state(AppState(state_version=version))
        self.assertEqual(client.pending_state["state_version"], "19")
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(delivered, ["1", "19"])
        self.assertIsNone(client.state_task)
        self.service._clients.discard(client)


if __name__ == "__main__":
    unittest.main()
