"""Dependency-light synchronous client for the loopback NDJSON service."""

from __future__ import annotations

import json
import socket
import threading
import uuid
from collections import deque
from typing import Any


MAX_LINE = 64 * 1024


class SocketClientError(RuntimeError):
    """The loopback service could not be used or returned an invalid event."""


class RemoteCommandError(SocketClientError):
    """The service returned a terminal failed result."""

    def __init__(self, result: dict[str, Any]):
        self.result = result
        error = result.get("error") or {}
        super().__init__(error.get("message", "remote command failed"))


class NdjsonClient:
    """Small blocking client; callers own one instance per socket connection."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765, *, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._file = None
        self._events: deque[dict[str, Any]] = deque()
        self._request_lock = threading.Lock()

    def connect(self) -> None:
        if self._socket is not None:
            return
        try:
            self._socket = socket.create_connection((self.host, self.port), self.timeout)
            self._socket.settimeout(self.timeout)
            self._file = self._socket.makefile("rb")
        except OSError as exc:
            self.close()
            raise SocketClientError(f"could not connect to {self.host}:{self.port}: {exc}") from exc

    def close(self) -> None:
        file, self._file = self._file, None
        if file is not None:
            file.close()
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()

    def __enter__(self) -> "NdjsonClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def request(self, command: str, *, request_id: str | None = None, **arguments) -> dict[str, Any]:
        request_id = request_id or f"client/{uuid.uuid4().hex}"
        request = {"protocol_version": 1, "request_id": request_id, "command": command}
        request.update(arguments)
        with self._request_lock:
            self._send(request)
            while True:
                event = self._read_event()
                if event.get("type") == "state":
                    self._events.append(event)
                    continue
                if event.get("type") != "result":
                    raise SocketClientError("service returned an event with no type")
                if event.get("request_id") != request_id:
                    self._events.append(event)
                    continue
                if event.get("status") == "accepted":
                    self._events.append(event)
                    continue
                if event.get("status") == "failed":
                    raise RemoteCommandError(event)
                if event.get("status") != "succeeded":
                    raise SocketClientError("service returned a non-terminal result")
                return event

    def receive(self, timeout: float | None = None) -> dict[str, Any]:
        """Read the next queued or wire event, temporarily applying ``timeout``."""
        if self._events:
            return self._events.popleft()
        if self._socket is None or self._file is None:
            raise SocketClientError("client is not connected")
        previous = self._socket.gettimeout()
        self._socket.settimeout(self.timeout if timeout is None else timeout)
        try:
            return self._read_event()
        finally:
            self._socket.settimeout(previous)

    def wait_for_state(self, timeout: float | None = None) -> dict[str, Any]:
        """Return the next state event, retaining unrelated result events."""
        deadline = None if timeout is None else socket.getdefaulttimeout()
        if timeout is not None:
            import time
            deadline = time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            event = self.receive(remaining)
            if event.get("type") == "state":
                return event
            self._events.append(event)

    def get_state_snapshot(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Subscribe and return the service's current complete state snapshot."""
        self.subscribe_state(True)
        return self.wait_for_state(timeout)

    def subscribe_state(self, enabled: bool = True) -> dict[str, Any]:
        return self.request("subscribe_state", enabled=enabled)

    def prepare_capture(self, collection_id: int, label: int, enabled: bool) -> dict[str, Any]:
        return self.request("prepare_capture", collection_id=collection_id, label=label, enabled=enabled)

    def select_collection(self, collection_id: int) -> dict[str, Any]:
        return self.request("select_collection", collection_id=collection_id)

    def select_label(self, label: int) -> dict[str, Any]:
        return self.request("select_label", label=label)

    def set_capture(self, enabled: bool) -> dict[str, Any]:
        return self.request("set_capture", enabled=enabled)

    def fit(self, epochs: int = 0) -> dict[str, Any]:
        return self.request("fit", epochs=epochs)

    def flush(self, scope: str, *, collection_id: int | None = None, label: int | None = None) -> dict[str, Any]:
        arguments = {"scope": scope}
        if collection_id is not None:
            arguments["collection_id"] = collection_id
        if label is not None:
            arguments["label"] = label
        return self.request("flush", **arguments)

    def _send(self, request: dict[str, Any]) -> None:
        if self._socket is None:
            raise SocketClientError("client is not connected")
        try:
            self._socket.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError as exc:
            raise SocketClientError(f"could not send request: {exc}") from exc

    def _read_event(self) -> dict[str, Any]:
        if self._file is None:
            raise SocketClientError("client is not connected")
        try:
            line = self._file.readline()
        except OSError as exc:
            raise SocketClientError(f"could not read service response: {exc}") from exc
        if not line:
            raise SocketClientError("service closed the connection")
        if len(line) > MAX_LINE:
            raise SocketClientError("service response exceeds 64 KiB")
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SocketClientError("service returned malformed JSON") from exc
        if not isinstance(event, dict):
            raise SocketClientError("service returned a non-object JSON event")
        return event
