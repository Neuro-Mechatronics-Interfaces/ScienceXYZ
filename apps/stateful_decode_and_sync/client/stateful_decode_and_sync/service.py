"""Loopback NDJSON service for the GUI and small external tools."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .controller import BroadbandController, ControllerError, DeviceCommandError
from .model import AppState, CommandResult, result_to_json, state_to_json


MAX_LINE = 64 * 1024


@dataclass(eq=False)
class _Client:
    writer: asyncio.StreamWriter
    subscribed: bool = False
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pending_state: dict[str, Any] | None = None
    state_task: asyncio.Task | None = None
    progress_tasks: set[asyncio.Task] = field(default_factory=set)


class ControlService:
    """Async socket facade; device I/O remains exclusively in the controller."""

    def __init__(self, controller: BroadbandController, host: str = "127.0.0.1", port: int = 8766):
        self.controller = controller
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[_Client] = set()
        self._command_lock = asyncio.Lock()
        self._active_requests: dict[str, tuple[_Client, str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        controller.on_state(self._state_from_thread)
        if hasattr(controller, "on_result"):
            controller.on_result(self._result_from_thread)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.controller.connect)
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for client in tuple(self._clients):
            if client.state_task is not None:
                client.state_task.cancel()
            for task in tuple(client.progress_tasks):
                task.cancel()
            client.writer.close()
            try:
                await client.writer.wait_closed()
            except OSError:
                pass
        self._clients.clear()
        await asyncio.to_thread(self.controller.disconnect)

    def _state_from_thread(self, state: AppState) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._broadcast_state, state)

    def _result_from_thread(self, result: CommandResult) -> None:
        if self._loop is not None and result.status == "accepted" and result.progress is not None:
            self._loop.call_soon_threadsafe(self._forward_progress, result)

    def _forward_progress(self, result: CommandResult) -> None:
        target = self._active_requests.get(result.request_id)
        if target is None:
            return
        client, request_id = target
        event = result_to_json(result) | {"request_id": request_id}
        task = asyncio.create_task(self._write(client, event))
        client.progress_tasks.add(task)
        task.add_done_callback(client.progress_tasks.discard)

    def _broadcast_state(self, state: AppState) -> None:
        for client in tuple(self._clients):
            if client.subscribed:
                # Keep at most one queued snapshot per client. A dashboard that
                # cannot keep up must receive the newest state, not an
                # unbounded backlog of stale snapshots.
                client.pending_state = state_to_json(state)
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
            "type": "result", "protocol_version": 1, "request_id": request_id,
            "command": "unknown", "status": "failed", "state_version": "0",
            "error": {"code": code, "message": message, "field": "", "retryable": retryable},
        }

    async def _dispatch(self, client: _Client, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error("", "malformed", "request must be a JSON object")
        request_id = request.get("request_id")
        command = request.get("command")
        if not isinstance(request_id, str) or not request_id:
            return self._error("", "invalid_argument", "request_id must be a non-empty string")
        if request.get("protocol_version") != 1:
            return self._error(request_id, "unsupported_version", "protocol_version must be 1")
        if not isinstance(command, str):
            return self._error(request_id, "invalid_argument", "command must be a string")

        if command == "subscribe_state":
            enabled = request.get("enabled")
            if not isinstance(enabled, bool):
                return self._error(request_id, "invalid_argument", "enabled must be boolean")

        completed = False
        try:
            async with self._command_lock:
                internal_id = f"socket/{client.session_id}/{request_id}"
                self._active_requests[internal_id] = (client, request_id)
                result = await asyncio.to_thread(self._run_command, command, request, internal_id)
            if command == "subscribe_state":
                client.subscribed = request["enabled"]
                if client.subscribed:
                    current_state = getattr(self.controller, "state", None)
                    if isinstance(current_state, AppState):
                        client.pending_state = state_to_json(current_state)
                        if client.state_task is None or client.state_task.done():
                            client.state_task = asyncio.create_task(self._drain_states(client))
                else:
                    client.pending_state = None
            completed = True
            return result_to_json(result) | {"request_id": request_id}
        except DeviceCommandError as exc:
            return result_to_json(exc.result) | {"request_id": request_id}
        except TimeoutError as exc:
            return self._error(request_id, "timeout", str(exc), retryable=True)
        except ControllerError as exc:
            message = str(exc)
            if message.startswith("duplicate request id:"):
                code = "duplicate_request_id"
            else:
                code = "transport_disconnected" if not self.controller.connected else "internal"
            return self._error(request_id, code, message, retryable=code == "transport_disconnected")
        except (KeyError, TypeError) as exc:
            return self._error(request_id, "invalid_argument", str(exc))
        except ValueError as exc:
            code = "unknown_command" if str(exc).startswith("unknown command:") else "invalid_argument"
            return self._error(request_id, code, str(exc))
        finally:
            if "internal_id" in locals():
                self._active_requests.pop(internal_id, None)
            if not completed:
                for task in tuple(client.progress_tasks):
                    task.cancel()

    def _run_command(self, name: str, request: dict[str, Any], request_id: str):
        c = self.controller
        if name == "get_state":
            return c.get_state(request_id)
        if name == "subscribe_state":
            return c.subscribe_state(request["enabled"], request_id)
        if name == "prepare_capture":
            return c.prepare_capture(self._uint(request, "collection_id"), self._uint(request, "label"), self._bool(request, "enabled"), request_id)
        if name == "select_collection":
            return c.select_collection(self._uint(request, "collection_id"), request_id)
        if name == "select_label":
            return c.select_label(self._uint(request, "label"), request_id)
        if name == "set_capture":
            return c.set_capture(self._bool(request, "enabled"), request_id)
        if name == "fit":
            return c.fit(self._uint(request, "epochs", 0), request_id)
        if name == "flush":
            return c.flush(request["scope"], self._optional_uint(request, "collection_id"), self._optional_uint(request, "label"), request_id)
        raise ValueError(f"unknown command: {name}")

    @staticmethod
    def _bool(request: dict[str, Any], key: str) -> bool:
        value = request[key]
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be boolean")
        return value

    @staticmethod
    def _uint(request: dict[str, Any], key: str, default: int | None = None) -> int:
        value = request[key] if key in request else default
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return value

    @classmethod
    def _optional_uint(cls, request: dict[str, Any], key: str) -> int | None:
        return None if key not in request else cls._uint(request, key)


async def serve(controller: BroadbandController, host: str, port: int) -> None:
    service = ControlService(controller, host, port)
    await service.start()
    try:
        await asyncio.Event().wait()
    finally:
        await service.stop()
