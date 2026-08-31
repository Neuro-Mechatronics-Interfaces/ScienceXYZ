import asyncio
import time
import unittest

from broadband_mode_switch.client import NdjsonClient, RemoteCommandError
from broadband_mode_switch.controller import BroadbandController
from broadband_mode_switch.service import ControlService
from broadband_mode_switch.transport import FakeTapTransport
from fake_device import FakeDevice


class FakeDeviceEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = FakeTapTransport()
        self.device = FakeDevice(self.transport)
        self.device.start()
        self.controller = BroadbandController("fake", self.transport, timeout=1)
        self.service = ControlService(self.controller, port=0)
        await self.service.start()
        port = self.service._server.sockets[0].getsockname()[1]
        self.client = NdjsonClient(port=port, timeout=1)
        await asyncio.to_thread(self.client.connect)

    async def asyncTearDown(self):
        self.client.close()
        await self.service.stop()
        self.device.stop()

    async def test_state_targeting_flush_fit_and_duplicate_request(self):
        state = await asyncio.to_thread(self.client.get_state_snapshot)
        self.assertEqual(state["pipeline"]["state"], "ready")
        await asyncio.to_thread(self.client.prepare_capture, 0, 2, True)
        with self.assertRaises(RemoteCommandError) as rejected:
            await asyncio.to_thread(self.client.select_label, 1)
        self.assertEqual(rejected.exception.result["error"]["code"], "capture_enabled")
        await asyncio.to_thread(self.client.set_capture, False)
        await asyncio.to_thread(self.client.prepare_capture, 0, 1, False)
        before_flush = len(self.device.commands)
        await asyncio.to_thread(self.client.request, "select_label", request_id="once", label=1)
        after_first = len(self.device.commands)
        await asyncio.to_thread(self.client.request, "select_label", request_id="once", label=2)
        self.assertEqual(len(self.device.commands), after_first)
        self.assertGreater(after_first, before_flush)
        await asyncio.to_thread(self.client.flush, "label", collection_id=0, label=1)
        await asyncio.to_thread(self.client.flush, "collection", collection_id=0)
        await asyncio.to_thread(self.client.flush, "all")

        result = await asyncio.to_thread(self.client.fit, 3)
        self.assertEqual(result["progress"]["epoch"], 3)
        self.assertEqual(result["progress"]["total_epochs"], 3)
        progress = []
        latest = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (progress != [1, 2, 3] or latest is None or latest["model"]["phase"] != "succeeded"):
            try:
                event = await asyncio.to_thread(self.client.receive, 0.2)
            except Exception:
                continue
            if event.get("type") == "state":
                latest = event
            elif event.get("type") == "result" and event.get("progress"):
                progress.append(event["progress"]["epoch"])
        self.assertEqual(progress, [1, 2, 3])
        if latest is None:
            latest = await asyncio.to_thread(self.client.get_state_snapshot)
        self.assertEqual(latest["model"]["phase"], "succeeded")
        self.assertTrue(latest["model"]["stale"])

    async def test_two_socket_clients_and_reconnect(self):
        second = NdjsonClient(port=self.client.port, timeout=1)
        await asyncio.to_thread(second.connect)
        try:
            first = await asyncio.to_thread(self.client.request, "select_label", label=1)
            other = await asyncio.to_thread(second.request, "select_label", label=2)
            self.assertEqual(first["status"], "succeeded")
            self.assertEqual(other["status"], "succeeded")
            await asyncio.to_thread(self.controller.reconnect_with_backoff, 1, 0)
            state = await asyncio.to_thread(self.client.get_state_snapshot)
            self.assertEqual(state["pipeline"]["state"], "ready")
        finally:
            second.close()

    async def test_reconnect_while_fit_is_running_does_not_replay_fit(self):
        await asyncio.to_thread(self.client.prepare_capture, 0, 0, False)
        fitting = asyncio.create_task(asyncio.to_thread(self.client.fit, 20))
        await asyncio.sleep(0.03)
        await asyncio.to_thread(self.controller.disconnect)
        with self.assertRaises(RemoteCommandError):
            await fitting
        fit_commands = [command for command in self.device.commands if command.command == 7]
        await asyncio.to_thread(self.controller.connect_with_backoff, 1, 0)
        self.client.close()
        replacement = NdjsonClient(port=self.client.port, timeout=1)
        await asyncio.to_thread(replacement.connect)
        try:
            state = await asyncio.to_thread(replacement.get_state_snapshot)
            self.assertEqual(state["pipeline"]["state"], "ready")
        finally:
            replacement.close()
        self.assertEqual(len([command for command in self.device.commands if command.command == 7]), len(fit_commands))


if __name__ == "__main__":
    unittest.main()
