import threading
import time
from unittest.mock import Mock
from scifi2_hub_manager.controller import BroadbandController
from scifi2_hub_manager.transport import FakeTapTransport, SynapseTapTransport


def test_synapse_receive_honors_shutdown_timeout():
    transport = SynapseTapTransport("unused")
    tap = Mock()
    transport._taps["state"] = tap
    transport.receive("state", timeout=0.5)
    tap.read.assert_called_once_with(timeout_ms=500)


def test_disconnect_joins_receivers_before_closing_taps():
    class ReceivingTransport(FakeTapTransport):
        def __init__(self):
            super().__init__()
            self.receiving = set()
            self.entered = threading.Event()
            self.guard = threading.Lock()

        def receive(self, name, timeout=None):
            with self.guard:
                self.receiving.add(name)
            self.entered.set()
            try:
                time.sleep(0.05)
                return None
            finally:
                with self.guard:
                    self.receiving.remove(name)

        def disconnect(self, name):
            with self.guard:
                assert name not in self.receiving
            super().disconnect(name)

    transport = ReceivingTransport()
    controller = BroadbandController("unused", transport)
    controller.connect()
    assert transport.entered.wait(1)
    controller.disconnect()
    assert not controller._threads
