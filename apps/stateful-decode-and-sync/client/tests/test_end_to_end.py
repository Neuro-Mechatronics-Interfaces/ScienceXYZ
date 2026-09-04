import asyncio
import time
import unittest

from stateful_decode_and_sync.client import NdjsonClient, RemoteCommandError
from stateful_decode_and_sync.controller import BroadbandController
from stateful_decode_and_sync.service import ControlService
from stateful_decode_and_sync.transport import FakeTapTransport
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

    async def test_task_actions_wait_for_committed_transition_and_stale_preconditions_fail(self):
        snapshot = await asyncio.to_thread(self.client.get_state_snapshot)
        preconditions = self.client.task_preconditions(snapshot)
        await asyncio.to_thread(self.client.subscribe_task_transitions)
        start = asyncio.create_task(asyncio.to_thread(self.client.start_task, preconditions=preconditions))
        deadline = time.monotonic() + 1
        while not self.device._staged_task_commands and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        self.assertFalse(start.done(), "acceptance must not be treated as a committed task action")
        self.assertTrue(self.device.advance_task_frame())
        self.assertEqual((await start)["status"], "succeeded")
        event = await asyncio.to_thread(self.client.wait_for_task_transition, 1)
        self.assertEqual((event["event_kind"], event["current_state_id"]), ("start", 1))
        self.assertEqual(event["effective_frame"]["sequence_number"], "1")
        with self.assertRaises(RemoteCommandError) as stale:
            await asyncio.to_thread(self.client.propose_task_event, "advance", preconditions=preconditions)
        self.assertIn("stale", stale.exception.result["error"]["message"])

    async def test_cyclic_task_simulates_external_timer_and_decoder_boundaries(self):
        await asyncio.to_thread(self.client.subscribe_task_transitions)

        async def commit(call, *args, automatic=None):
            snapshot = await asyncio.to_thread(self.client.get_state_snapshot)
            request = asyncio.create_task(asyncio.to_thread(call, *args,
                                                            preconditions=self.client.task_preconditions(snapshot)))
            deadline = time.monotonic() + 1
            while not self.device._staged_task_commands and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            self.assertTrue(self.device.advance_task_frame())
            await request
            return await asyncio.to_thread(self.client.wait_for_task_transition, 1)

        start = await commit(self.client.start_task)
        active = await commit(self.client.propose_task_event, "go")
        self.assertTrue(self.device.advance_task_timer())
        timeout = await asyncio.to_thread(self.client.wait_for_task_transition, 1)
        active_again = await commit(self.client.propose_task_transition, 10)
        self.assertTrue(self.device.advance_task_decoder())
        decoded = await asyncio.to_thread(self.client.wait_for_task_transition, 1)
        events = [start, active, timeout, active_again, decoded]
        self.assertEqual([event["event_sequence"] for event in events], ["1", "2", "3", "4", "5"])
        self.assertEqual([event["trigger_kind"] for event in events],
                         ["start_command", "external_event", "source_timeout",
                          "external_event", "decoder_predicate"])
        self.assertEqual(decoded["current_state_id"], 3)


if __name__ == "__main__":
    unittest.main()
