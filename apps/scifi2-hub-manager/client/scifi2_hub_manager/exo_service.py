"""Loopback NDJSON service exposing the exo worker to the GUI and tools.

A standalone counterpart to :class:`~scifi2_hub_manager.service.ControlService`:
same one-JSON-object-per-line request/reply framing and the same discipline of
keeping all device I/O off the event loop (here, inside :class:`ExoWorker`).
It is a separate service on its own port so the neural-device Tap controller and
the exo serial transport stay in independent processes.

Request shape (one JSON object per line)::

    {"protocol_version": 1, "request_id": "abc", "command": "exo_arm", "home": true}

Every reply carries ``type: "result"`` with ``status`` and echoes ``request_id``.
A client may ``subscribe_exo_state`` to also receive ``type: "exo_state"``
snapshots whenever the worker publishes one.

Commands:

- ``exo_connect`` / ``exo_disconnect``
- ``exo_arm`` (optional ``home: bool``, default true) / ``exo_disarm`` / ``exo_home``
- ``exo_set_finger_angles`` (``values``: joint -> signed ``[-100, 100]`` or null to hold)
- ``exo_read_pose``
- ``exo_get_state``
- ``subscribe_exo_state`` (``enabled: bool``)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .exo_worker import ExoError, ExoNotConnectedError, ExoWorker, state_to_json

MAX_LINE = 64 * 1024
PROTOCOL_VERSION = 1


@dataclass(eq=False)
class _Client:
    writer: asyncio.StreamWriter
    subscribed: bool = False
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pending_state: dict[str, Any] | None = None
    state_task: asyncio.Task | None = None


class ExoService:
    """Async socket facade; all serial I/O stays in the worker thread."""

    def __init__(self, worker: ExoWorker, host: str = "127.0.0.1", port: int = 18766):
        self.worker = worker
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[_Client] = set()
        self._command_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        worker.on_state(self._state_from_thread)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.worker.start()
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in tuple(self._clients):
            if client.state_task is not None:
                client.state_task.cancel()
            client.writer.close()
            try:
                await client.writer.wait_closed()
            except OSError:
                pass
        self._clients.clear()
        await asyncio.to_thread(self.worker.shutdown)

    # -- state broadcast ----------------------------------------------------

    def _state_from_thread(self, state) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._broadcast_state, state_to_json(state))

    def _broadcast_state(self, value: dict[str, Any]) -> None:
        for client in tuple(self._clients):
            if client.subscribed:
                # Coalesce to the newest snapshot: a slow reader must not build
                # an unbounded backlog of stale poses.
                client.pending_state = value
                if client.state_task is None or client.state_task.done():
                    client.state_task = asyncio.create_task(self._drain_states(client))

    async def _drain_states(self, client: _Client) -> None:
        try:
            while client.subscribed and client.pending_state is not None:
                value = client.pending_state
                client.pending_state = None
                await self._write(client, value)
        finally:
            client.state_task = None
            if client in self._clients and client.subscribed and client.pending_state is not None:
                client.state_task = asyncio.create_task(self._drain_states(client))

    async def _write(self, client: _Client, value: dict[str, Any]) -> None:
        try:
            data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
            async with client.write_lock:
                client.writer.write(data)
                await client.writer.drain()
        except (OSError, asyncio.CancelledError):
            client.subscribed = False
            client.pending_state = None
            self._clients.discard(client)

    # -- client loop --------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(writer)
        self._clients.add(client)
        try:
            while True:
                try:
                    line = await reader.readline()
                except asyncio.LimitOverrunError:
                    await self._write(client, self._error("", "malformed", "request line exceeds 64 KiB"))
                    break
                except OSError:
                    break
                if not line:
                    break
                if len(line) > MAX_LINE:
                    await self._write(client, self._error("", "malformed", "request line exceeds 64 KiB"))
                    break
                try:
                    request = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    await self._write(client, self._error("", "malformed", "request must be one UTF-8 JSON object"))
                    continue
                response = await self._dispatch(client, request)
                if response is not None:
                    await self._write(client, response)
        finally:
            self._clients.discard(client)
            writer.close()

    @staticmethod
    def _error(request_id: str, code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
        return {
            "type": "result", "protocol_version": PROTOCOL_VERSION, "request_id": request_id,
            "command": "unknown", "status": "failed",
            "error": {"code": code, "message": message, "retryable": retryable},
        }

    @staticmethod
    def _ok(request_id: str, command: str, state) -> dict[str, Any]:
        return {
            "type": "result", "protocol_version": PROTOCOL_VERSION, "request_id": request_id,
            "command": command, "status": "succeeded", "error": None,
            "state": state_to_json(state),
        }

    async def _dispatch(self, client: _Client, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error("", "malformed", "request must be a JSON object")
        request_id = request.get("request_id")
        command = request.get("command")
        if not isinstance(request_id, str) or not request_id:
            return self._error("", "invalid_argument", "request_id must be a non-empty string")
        if request.get("protocol_version") != PROTOCOL_VERSION:
            return self._error(request_id, "unsupported_version", "protocol_version must be 1")
        if not isinstance(command, str):
            return self._error(request_id, "invalid_argument", "command must be a string")

        if command == "subscribe_exo_state":
            enabled = request.get("enabled")
            if not isinstance(enabled, bool):
                return self._error(request_id, "invalid_argument", "enabled must be boolean")
            client.subscribed = enabled
            if enabled:
                client.pending_state = state_to_json(self.worker.state)
                if client.state_task is None or client.state_task.done():
                    client.state_task = asyncio.create_task(self._drain_states(client))
            else:
                client.pending_state = None
            return self._ok(request_id, command, self.worker.state)

        try:
            async with self._command_lock:
                state = await asyncio.to_thread(self._run_command, command, request)
            return self._ok(request_id, command, state)
        except ExoNotConnectedError as exc:
            return self._error(request_id, "not_connected", str(exc), retryable=True)
        except TimeoutError as exc:
            return self._error(request_id, "timeout", str(exc), retryable=True)
        except (KeyError, TypeError) as exc:
            return self._error(request_id, "invalid_argument", str(exc))
        except ValueError as exc:
            code = "unknown_command" if str(exc).startswith("unknown command:") else "invalid_argument"
            return self._error(request_id, code, str(exc))
        except ExoError as exc:
            return self._error(request_id, "device_error", str(exc))
        except ImportError as exc:
            return self._error(request_id, "sdk_missing",
                               f"nml_hand_exo is not installed: {exc}. Install with "
                               "pip install -e '.[exo]'")

    def _run_command(self, name: str, request: dict[str, Any]):
        w = self.worker
        if name == "exo_connect":
            return w.connect()
        if name == "exo_disconnect":
            return w.disconnect()
        if name == "exo_arm":
            return w.arm(home=self._bool(request, "home", default=True))
        if name == "exo_disarm":
            return w.disarm()
        if name == "exo_home":
            return w.home()
        if name == "exo_set_finger_angles":
            values = request.get("values")
            if not isinstance(values, dict):
                raise ValueError("values must be an object of joint -> value")
            return w.set_finger_angles(values)
        if name == "exo_read_pose":
            return w.read_pose()
        if name == "exo_get_state":
            return w.get_state()
        raise ValueError(f"unknown command: {name}")

    @staticmethod
    def _bool(request: dict[str, Any], key: str, *, default: bool | None = None) -> bool:
        if key not in request:
            if default is None:
                raise ValueError(f"{key} must be boolean")
            return default
        value = request[key]
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return value


async def serve(worker: ExoWorker, host: str, port: int) -> None:
    service = ExoService(worker, host, port)
    await service.start()
    print(f"NDJSON exo control service listening on {host}:{port}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await service.stop()
