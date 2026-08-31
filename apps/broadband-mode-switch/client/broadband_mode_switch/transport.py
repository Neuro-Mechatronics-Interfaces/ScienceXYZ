"""Replaceable Tap transport implementations."""

from __future__ import annotations

import queue
import threading
import time
from collections import defaultdict
from typing import Protocol


class TransportError(RuntimeError):
    """A Tap could not be opened or used."""


class TapTransport(Protocol):
    def connect(self, tap_name: str) -> bool: ...
    def send(self, tap_name: str, payload: bytes) -> bool: ...
    def receive(self, tap_name: str, timeout: float | None = None) -> bytes | None: ...
    def disconnect(self, tap_name: str) -> None: ...


class SynapseTapTransport:
    """Adapter around science-synapse's one-Tap-per-connection API."""

    def __init__(self, device_ip: str):
        self.device_ip = device_ip
        self._taps = {}
        self._lock = threading.Lock()

    def connect(self, tap_name: str) -> bool:
        from synapse.client.taps import Tap

        tap = Tap(self.device_ip)
        if not tap.connect(tap_name):
            return False
        with self._lock:
            self._taps[tap_name] = tap
        return True

    def send(self, tap_name: str, payload: bytes) -> bool:
        with self._lock:
            tap = self._taps.get(tap_name)
        if tap is None:
            raise TransportError(f"tap is not connected: {tap_name}")
        return bool(tap.send(payload))

    def receive(self, tap_name: str, timeout: float | None = None) -> bytes | None:
        # Tap.read() has no timeout parameter. Closing the Tap from another
        # thread is the cancellation mechanism used by BroadbandController.
        with self._lock:
            tap = self._taps.get(tap_name)
        if tap is None:
            raise TransportError(f"tap is not connected: {tap_name}")
        return tap.read()

    def disconnect(self, tap_name: str) -> None:
        with self._lock:
            tap = self._taps.pop(tap_name, None)
        if tap is not None:
            tap.disconnect()


class FakeTapTransport:
    """Deterministic in-memory transport for controller and service tests."""

    def __init__(self):
        self.connected: set[str] = set()
        self.sent: list[tuple[str, bytes]] = []
        self.incoming: dict[str, queue.Queue[bytes]] = defaultdict(queue.Queue)
        self.fail_connect: set[str] = set()
        self.fail_receive: dict[str, Exception] = {}
        self.fail_send: set[str] = set()
        self._lock = threading.Lock()

    def connect(self, tap_name: str) -> bool:
        if tap_name in self.fail_connect:
            return False
        with self._lock:
            self.connected.add(tap_name)
        return True

    def send(self, tap_name: str, payload: bytes) -> bool:
        with self._lock:
            if tap_name not in self.connected or tap_name in self.fail_send:
                return False
            self.sent.append((tap_name, payload))
        return True

    def receive(self, tap_name: str, timeout: float | None = None) -> bytes | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            failure = self.fail_receive.get(tap_name)
            if failure is not None:
                raise failure
            wait = 0.05
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
                if wait == 0:
                    return None
            try:
                return self.incoming[tap_name].get(timeout=wait)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    return None

    def disconnect(self, tap_name: str) -> None:
        with self._lock:
            self.connected.discard(tap_name)

    def inject(self, tap_name: str, payload: bytes) -> None:
        self.incoming[tap_name].put(payload)
