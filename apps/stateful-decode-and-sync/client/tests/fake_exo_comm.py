"""In-process fake of the exo dual-CDC transport for hardware-free tests.

Implements the ``BaseComm`` surface that ``nml_hand_exo.HandExo`` uses and
answers with firmware-shaped replies, so the real SDK command formatting and
firmware gates are exercised with no serial port present.  Modelled on the
``MockComm`` in ``third_party/exo/examples/08_udp/udp_gesture_receiver.py``:
commands that are silent in firmware stay silent here, so the fake can never
make the host look healthier than the real device.
"""

from __future__ import annotations

import collections


class FakeExoComm:
    """Answer like the OpenRB dual-CDC firmware, in process.

    ``firmware`` controls what ``version``/``info`` report so a test can drive
    the worker's firmware gate on either side of 0.6.4.
    """

    def __init__(self, firmware: str = "0.6.4"):
        self.cmd_port = "FAKE-CMD"
        self.telem_port = "FAKE-TELEM"
        self.firmware = firmware
        self.verbose = False
        self.sent: list[str] = []
        self._pending: collections.deque[str] = collections.deque()
        self._open = False

    # -- BaseComm surface --------------------------------------------------

    def connect(self):
        self._open = True

    def close(self):
        self._open = False
        self._pending.clear()

    def is_connected(self) -> bool:
        return self._open

    def flush_input(self):
        self._pending.clear()

    def fast_telemetry_device(self):
        return None

    def send(self, message: str):
        if not self._open:
            raise OSError("FakeExoComm is not connected")
        command = message.strip()
        self.sent.append(command)
        reply = self._reply_for(command)
        if reply is not None:
            self._pending.append(reply)

    def receive(self, wait_until_return: bool = False, timeout=None, *, warn_on_timeout: bool = True) -> str:
        if self._pending:
            return self._pending.popleft()
        return ""

    # -- firmware-shaped replies ------------------------------------------

    def _reply_for(self, command: str) -> str | None:
        head, _, rest = command.partition(":")
        if head == "version":
            return f"Exo Device Version: {self.firmware};"
        if head == "info":
            return (
                f"Name: NMLHandExo;\nVersion: {self.firmware};\nSide: right;\n"
                "Number of Motors: 6;"
            )
        if head == "set_finger_angles":
            return f"OK: finger_angles {rest};"
        if head == "set_total_current_lim":
            return f"OK: total_current_lim {rest};"
        if head == "set_current_lim":
            return f"OK: set_current_lim {rest};"
        if head == "get_gesture_angles":
            # Fixed, plausible pose. The point is that the query ANSWERS.
            joints = ("thumb", "index", "middle", "ring", "pinky", "wrist")
            codes = (50, 0, 25, 100, 75, 50)
            angles = (0.0, -8.0, -4.0, 16.5, 8.25, 0.0)
            body = " ".join(f"{j}={c},{a:.2f}" for j, c, a in zip(joints, codes, angles))
            return f"GESTURE_ANGLES: {body};"
        if head == "set_reply_route":
            return f"OK: reply_route {rest};"
        if head == "home":
            return f"OK: home {rest};"
        # enable / disable are silent in firmware, as in the real device.
        return None
