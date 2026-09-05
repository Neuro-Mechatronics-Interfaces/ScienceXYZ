"""TASK 0.12 envelope adapter; raw acquisition stays in the C++ recorder."""
from __future__ import annotations

import asyncio
import copy
import json
import signal
import uuid
from pathlib import Path

from .calibration_task import Journal, TaskInstructor
from .client import NdjsonClient
from .recording_analysis import sha256_file


class RecorderProcess:
    def __init__(self, executable, device_uri, folder, journal):
        self.executable, self.device_uri, self.folder, self.journal = executable, device_uri, folder, journal
        self.process = None
        self.monitor = None
        self.ready = asyncio.Event()
        self.status = None

    def check_running(self):
        if self.process is None or self.process.returncode is not None or isinstance(self.status, dict):
            raise RuntimeError("recorder is no longer running; stop session and inspect recorder.log")

    async def start(self, metadata):
        if self.process is not None:
            raise ValueError("This connection already owns a recording; reconnect for a new file")
        metadata_path = self.folder / "provenance.json"
        with metadata_path.open("x", encoding="utf-8") as output:
            json.dump(metadata, output, indent=2, allow_nan=False)
        self.process = await asyncio.create_subprocess_exec(
            self.executable, "--device", self.device_uri, "--output", str(self.folder / "raw.h5"),
            "--session-id", self.folder.name, "--metadata-file", str(metadata_path),
            stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL)
        self.monitor = asyncio.create_task(self._monitor())
        try:
            await asyncio.wait_for(self.ready.wait(), 25)
            if self.process.returncode is not None or self.status == "startup_failed":
                raise RuntimeError("recorder failed before readiness; inspect recorder.log")
        except BaseException:
            await self.stop()
            raise
        return {"recording": True, "session_dir": str(self.folder), "head_complete": False}

    async def _monitor(self):
        with (self.folder / "recorder.log").open("x", encoding="utf-8") as log:
            while line := await self.process.stderr.readline():
                text = line.decode("utf-8", errors="replace")
                log.write(text); log.flush()
                if text.startswith("recording raw broadband and task messages to "):
                    self.ready.set()
                if text.startswith("{"):
                    try:
                        self.status = json.loads(text)
                    except json.JSONDecodeError:
                        pass
        await self.process.wait()
        if not self.ready.is_set():
            self.status = "startup_failed"
            self.ready.set()

    async def stop(self):
        if self.process is None:
            return {"recording": False}
        if self.process.returncode is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.monitor:
            await self.monitor
        success = self.process.returncode == 0 and isinstance(self.status, dict) and self.status.get("state") == "stopped"
        raw = self.folder / "raw.h5"
        digest = await asyncio.to_thread(sha256_file, raw) if raw.exists() else None
        result = {"recording": False, "local_stop_ok": success, "exit_code": self.process.returncode,
                  "raw_sha256": digest, "recorder_status": self.status}
        self.journal.write("recording_stopped", **result)
        if not success:
            raise RuntimeError("recorder did not stop successfully; inspect recorder.log and raw file")
        return result


class BridgeSession:
    def __init__(self, folder, profile, provenance, recorder, *, port=8765, client=None):
        self.folder, self.profile, self.provenance = folder, profile, provenance
        self.journal = recorder.journal
        self.recorder = recorder
        self.client = client or NdjsonClient(port=port)
        self.instructor = None
        self.streams = set()
        self.seen_requests = set()
        self.recording = False
        self.task_failed = False

    async def start(self, browser_metadata):
        if self.recording:
            raise ValueError("recording already active")
        metadata = copy.deepcopy(self.provenance)
        metadata["task_profile"] = self.profile
        metadata["browser_metadata"] = browser_metadata
        metadata["browser_clock_mapping"] = "unmeasured; preserve browser time fields literally"
        self.journal.write("recording_requested", profile=self.profile, browser_metadata=browser_metadata)
        result = await self.recorder.start(metadata)
        self.recording = True
        return result

    async def task_command(self, command):
        if self.task_failed:
            raise ValueError("task outcome uncertain; close session and inspect logs before a new run")
        if not self.recording:
            raise ValueError("start recording and await acknowledgment before task commands")
        if command not in {"start_task", "propose_task_event", "reset_task", "abort_task"}:
            raise ValueError("unsupported task command")
        try:
            self.recorder.check_running()
            if self.instructor is None:
                await asyncio.to_thread(self.client.connect)
                self.instructor = TaskInstructor(self.client, self.profile, self.journal)
                await asyncio.to_thread(self.instructor.prepare)
            arguments = {"event_name": "advance"} if command == "propose_task_event" else {}
            event = await asyncio.to_thread(self.instructor.command, command, **arguments)
            self.recorder.check_running()
            return event
        except Exception as error:
            self.task_failed = True
            self.journal.write("task_failed", error=str(error))
            if self.instructor:
                await asyncio.to_thread(self.instructor.cleanup)
            raise

    async def stop(self):
        if self.instructor:
            # A task that was stopped early is explicitly aborted, never completed.
            await asyncio.to_thread(self.instructor.cleanup)
        result = await self.recorder.stop()
        self.recording = False
        return result

    async def handle(self, message):
        if not isinstance(message, dict) or message.get("api_version") != "0.12":
            raise ValueError("TASK api_version 0.12 required")
        request = message.get("api_request")
        if isinstance(request, dict):
            request_id = request.get("request_id")
            if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
                raise ValueError("request_id must be an integer or string")
            if request_id in self.seen_requests:
                raise ValueError("duplicate request_id rejected; uncertain commands are never replayed")
            if len(self.seen_requests) >= 10000:
                raise ValueError("request limit reached; close this session")
            self.seen_requests.add(request_id)
            self.journal.write("web_request", message=message)
            if "start_stream_request" in request or "end_stream_request" in request:
                opening = "start_stream_request" in request
                body = request["start_stream_request" if opening else "end_stream_request"]
                stream = body.get("stream_id")
                if not isinstance(stream, str) or not stream or len(stream) > 128:
                    raise ValueError("invalid stream_id")
                if opening:
                    if len(self.streams) >= 64:
                        raise ValueError("stream limit reached")
                    self.streams.add(stream)
                else:
                    self.streams.discard(stream)
                result = {"stream_id": stream, "mode": "browser journal only; no emulated sensor output"}
            elif "sciencexyz_request" in request:
                body = request["sciencexyz_request"]
                command = body.get("command")
                if command == "start_recording":
                    result = await self.start(body.get("metadata", {}))
                elif command == "stop_recording":
                    result = await self.stop()
                elif command == "get_profile":
                    result = self.profile
                else:
                    result = {"committed_event": await self.task_command(command)}
            else:
                raise ValueError("unsupported TASK request; no haptics, whitening or legacy sensor emulation")
            reply = {"api_version": "0.12", "api_response": {"request_id": request_id, "success": True, "result": result}}
            self.journal.write("web_response", message=reply)
            return reply
        batch = message.get("stream_batch")
        if not isinstance(batch, dict):
            raise ValueError("api_request or stream_batch required")
        stream = batch.get("stream_id")
        if stream not in self.streams:
            raise ValueError("stream must be registered before samples")
        samples = batch.get(stream, {}).get("samples")
        if not isinstance(samples, list) or len(samples) > 1024:
            raise ValueError("samples must be a bounded list of at most 1024 items")
        self.journal.write("browser_stream", message=message, browser_clock_mapping="unmeasured")
        if stream == "recording_control":
            if len(samples) != 1 or not isinstance(samples[0].get("data", {}).get("enabled"), bool):
                raise ValueError("recording_control requires one boolean enabled sample")
            body = samples[0]["data"]
            result = await self.start(body) if body["enabled"] else await self.stop()
        else:
            result = {"journaled_samples": len(samples), "stream_id": stream,
                      "authoritative_task_event": False}
        return {"api_version": "0.12", "stream_batch": {"stream_id": "sciencexyz_status",
                "sciencexyz_status": {"samples": [{"data": result}]}}}

    async def close(self):
        try:
            if self.recording:
                self.journal.write("connection_lost", run_complete=False)
                await self.stop()
        finally:
            self.client.close()
            self.journal.close()


async def serve_bridge(args, profile, provenance):
    from websockets.asyncio.server import serve
    owner = False

    async def handler(socket):
        nonlocal owner
        if owner:
            await socket.close(code=1013, reason="one recording controller at a time")
            return
        owner = True
        session = None
        try:
            folder = Path(args.output_root).resolve() / ("reactions-" + uuid.uuid4().hex)
            folder.mkdir(parents=True, exist_ok=False)
            journal = Journal(folder / "browser-events.ndjson")
            journal.write("bridge_session", profile=profile, session_dir=str(folder),
                          browser_time="original units preserved; not source time")
            recorder = RecorderProcess(str(Path(args.recorder).resolve()), args.device_uri, folder, journal)
            session = BridgeSession(folder, profile, provenance, recorder, port=args.service_port)
            async for text in socket:
                message = None
                try:
                    message = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                    reply = await session.handle(message)
                except Exception as error:
                    session.journal.write("request_failed", error=str(error))
                    request = message.get("api_request", {}) if isinstance(message, dict) else {}
                    reply = {"api_version": "0.12", "api_response": {"request_id": request.get("request_id"),
                             "success": False, "error": str(error)}}
                await socket.send(json.dumps(reply, allow_nan=False))
        finally:
            try:
                if session:
                    await session.close()
            finally:
                owner = False

    async with serve(handler, "127.0.0.1", args.port, origins=args.origin,
                     max_size=256 * 1024, max_queue=8):
        print(f"Reactions bridge ws://127.0.0.1:{args.port}; origins={args.origin}", flush=True)
        await asyncio.Future()
