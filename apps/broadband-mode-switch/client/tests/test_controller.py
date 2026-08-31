import threading
import time
import unittest

from broadband_mode_switch import proto
from broadband_mode_switch.controller import BroadbandController, ControllerError, DeviceCommandError
from broadband_mode_switch.model import state_from_proto
from broadband_mode_switch.transport import FakeTapTransport, TransportError


def result_payload(request_id, command, status=proto.RESULT_SUCCEEDED):
    value = proto.CommandResult()
    value.protocol_version = 1
    value.request_id = request_id
    value.command = proto.COMMAND[command]
    value.status = status
    return value.SerializeToString()


def state_payload(version=1):
    value = proto.StateSnapshot()
    value.protocol_version = 1
    value.state_version = version
    value.timestamp_ns = 10
    value.pipeline.state = 3
    value.pipeline.source_mode = 1
    value.active.collection_id = 0
    value.active.label = 2
    value.model.phase = 1
    collection = value.collections.add()
    collection.collection_id = 0
    collection.feature_dimension = 256
    collection.data_generation = 7
    label = collection.labels.add()
    label.label = 2
    label.count = 12
    label.capacity = 2000
    return value.SerializeToString()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTapTransport()
        self.controller = BroadbandController("fake", self.transport, timeout=0.25)
        self.controller.connect()

    def tearDown(self):
        self.controller.disconnect()

    def test_state_is_replaced_immutably(self):
        self.transport.inject("state", state_payload(4))
        deadline = time.monotonic() + 1
        while self.controller.state.state_version != 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.controller.state.pipeline_state, "ready")
        self.assertEqual(self.controller.state.active_collection.labels[0].count, 12)
        self.assertEqual(self.controller.state.active_collection.labels[0].fraction, 12 / 2000)

    def test_command_is_serialized_and_correlated(self):
        outcome = []

        def issue():
            outcome.append(self.controller.select_label(3))

        thread = threading.Thread(target=issue)
        thread.start()
        deadline = time.monotonic() + 1
        while not self.transport.sent and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 1
        command_payload = None
        while command_payload is None and time.monotonic() < deadline:
            for candidate_name, candidate_raw in self.transport.sent:
                candidate = proto.ControlCommand()
                candidate.ParseFromString(candidate_raw)
                if candidate.command == proto.COMMAND["select_label"]:
                    command_payload = (candidate_name, candidate_raw)
                    break
            time.sleep(0.01)
        self.assertIsNotNone(command_payload)
        name, raw = command_payload
        command = proto.ControlCommand()
        command.ParseFromString(raw)
        self.assertEqual(name, "control")
        self.assertEqual(command.command, proto.COMMAND["select_label"])
        self.assertEqual(command.select_label.label, 3)
        self.transport.inject("command_result", result_payload(command.request_id, "select_label"))
        thread.join(1)
        self.assertEqual(len(outcome), 1)
        self.assertTrue(outcome[0].ok)

    def test_device_rejection_is_visible(self):
        error = proto.CommandResult()
        error.protocol_version = 1
        error.request_id = "pending"
        error.command = proto.COMMAND["set_capture"]
        error.status = proto.RESULT_FAILED
        error.error.code = 7
        error.error.message = "capture is already enabled"

        def issue():
            with self.assertRaises(DeviceCommandError) as raised:
                self.controller.set_capture(False)
            self.assertEqual(raised.exception.result.error.code, "capture_enabled")

        thread = threading.Thread(target=issue)
        thread.start()
        deadline = time.monotonic() + 1
        while not self.transport.sent and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 1
        command = None
        while command is None and time.monotonic() < deadline:
            for _, raw in self.transport.sent:
                candidate = proto.ControlCommand()
                candidate.ParseFromString(raw)
                if candidate.command == proto.COMMAND["set_capture"]:
                    command = candidate
                    break
            time.sleep(0.01)
        self.assertIsNotNone(command)
        error.request_id = command.request_id
        self.transport.inject("command_result", error.SerializeToString())
        thread.join(1)

    def test_timeout_does_not_leave_pending_request(self):
        with self.assertRaises(TimeoutError):
            self.controller.select_collection(0)
        self.assertFalse(self.controller._pending)

    def test_completed_request_id_is_not_reapplied(self):
        request_id = "fixed-request"
        outcome = []

        def issue():
            outcome.append(self.controller.select_label(3, request_id=request_id))

        thread = threading.Thread(target=issue)
        thread.start()
        command = None
        deadline = time.monotonic() + 1
        while command is None and time.monotonic() < deadline:
            for _, raw in self.transport.sent:
                candidate = proto.ControlCommand()
                candidate.ParseFromString(raw)
                if candidate.request_id == request_id:
                    command = candidate
                    break
            time.sleep(0.01)
        self.assertIsNotNone(command)
        self.transport.inject("command_result", result_payload(request_id, "select_label"))
        thread.join(1)
        sent_count = len(self.transport.sent)
        self.assertTrue(outcome[0].ok)
        self.assertTrue(self.controller.select_label(3, request_id=request_id).ok)
        self.assertEqual(len(self.transport.sent), sent_count)

    def test_malformed_state_is_reported_without_disconnecting(self):
        updates = []
        self.controller.on_state(updates.append)
        self.transport.inject("state", b"not a protobuf")
        deadline = time.monotonic() + 1
        while (not updates or updates[-1].last_error is None) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.controller.connected)
        self.assertEqual(self.controller.state.last_error.code, "malformed")

    def test_transport_loss_wakes_pending_and_publishes_disconnected(self):
        updates = []
        self.controller.on_state(updates.append)
        self.transport.fail_receive["command_result"] = TransportError("tap closed")
        outcome = []

        def issue():
            try:
                self.controller.select_label(1)
            except Exception as exc:
                outcome.append(exc)

        thread = threading.Thread(target=issue)
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(outcome[0], ControllerError)
        self.assertFalse(self.controller.connected)
        self.assertEqual(self.controller.state.pipeline_state, "disconnected")
        self.assertTrue(updates)

    def test_reconnect_with_bounded_backoff(self):
        self.controller.disconnect()
        self.transport.fail_connect.update({"control", "state", "command_result"})
        attempts = []
        original = self.transport.connect

        def flaky(name):
            attempts.append(name)
            if len(attempts) <= 3:
                return False
            self.transport.fail_connect.clear()
            return original(name)

        self.transport.connect = flaky
        self.controller.connect_with_backoff(attempts=4, initial_delay=0)
        self.assertTrue(self.controller.connected)
        self.assertGreaterEqual(len(attempts), 4)


if __name__ == "__main__":
    unittest.main()
