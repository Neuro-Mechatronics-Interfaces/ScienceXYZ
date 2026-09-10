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


class PassiveRecorder:
    """Host-side recorder for the passive workflow: no C++ subprocess, no device
    task round trip.

    Mirrors the ``RecorderProcess`` interface (``start``/``stop``/``check_running``)
    so ``BridgeSession`` uses either interchangeably. On ``start`` it opens a
    Cognescent ``data.hdf5`` and drains the SciFi broadband tap (read in a worker
    thread, since the synapse ``Tap`` is blocking) into ``/devices/1``. Browser
    events are recorded as ``/devices/0`` annotations via :meth:`append_annotation`,
    stamped with the host clock the instant they arrive at the bridge -- the
    low-latency host marker, in the same wall clock as the broadband ``time``
    column, with no dependence on a device-committed transition.
    """

    def __init__(self, device_uri, folder, journal, *, broadband_tap="broadband_out", config=None):
        self.device_uri = device_uri
        self.folder = folder
        self.journal = journal
        self.broadband_tap = broadband_tap
        # Broadband channel layout(s) come from the device config (provenance or a
        # device-config dict), not from wire inference. One layout per
        # kBroadbandSource -> its own /devices/<n>. Empty falls back to inference.
        from .broadband_layout import layouts_from_config
        self.layouts = layouts_from_config(config) if config is not None else []
        self.writer = None
        self.reader = None
        self._reader_task = None
        self._failure = None
        self._active_folder = folder

    async def start(self, metadata, folder=None):
        from .broadband_tap_reader import BroadbandTapReader
        from .cognescent_writer import CognescentWriter
        if self.writer is not None:
            raise ValueError("a passive recording is already active; stop it before starting another")
        # Each start/stop cycle records into its own fresh folder (created by the
        # caller and passed here); fall back to the construction folder otherwise.
        self._active_folder = Path(folder) if folder is not None else self.folder
        self._active_folder.mkdir(parents=True, exist_ok=True)
        self.writer = CognescentWriter(self._active_folder / "data.hdf5")
        self.writer.open(metadata=metadata.get("browser_metadata", {}).get("metadata"))
        # Create a broadband stream device per configured kBroadbandSource, with
        # channel count/names/config from the config (source of truth). The single
        # tap here feeds the first source; additional sources on their own taps
        # are a future multi-tap extension (devices are pre-created regardless).
        self._source_key = None
        expected_n_ch = None
        for i, layout in enumerate(self.layouts):
            key = f"source{layout.node_id}"
            self.writer.add_broadband_device(
                key, output_layout=layout.output_layout, sample_rate_hz=layout.sample_rate_hz,
                config_json=layout.config_json)
            if i == 0:
                self._source_key = key
                expected_n_ch = len(layout.output_layout)
        if self._source_key is None:
            # No config layout: fall back to a single inferred source (legacy).
            self._source_key = "source0"
        self.reader = BroadbandTapReader(self.device_uri, self.broadband_tap,
                                         expected_n_ch=expected_n_ch)
        try:
            await asyncio.to_thread(self.reader.connect)
        except Exception as error:
            self.writer.close()
            self.writer = None
            raise RuntimeError(f"passive broadband tap connect failed: {error}")
        self._reader_task = asyncio.create_task(self._drain_broadband())
        return {"recording": True, "session_dir": str(self._active_folder), "mode": "passive",
                "data_file": str(self._active_folder / "data.hdf5")}

    # Broadband frames are handed from the blocking tap thread to the async
    # drainer through a thread-safe queue. A bounded queue with a BLOCKING put
    # applies backpressure (the tap thread waits) instead of raising QueueFull
    # and dropping frames, which is what a full asyncio put_nowait did. The
    # drainer batches many frames into one HDF5 append so a 20 kHz stream is a
    # few large writes per interval rather than thousands of tiny resizes.
    _QUEUE_MAX = 4096
    _BATCH_FRAMES = 200

    async def _drain_broadband(self):
        """Pull broadband frames off the tap thread and append them to the HDF5."""
        import queue as _queue
        import numpy as np
        frames = _queue.Queue(maxsize=self._QUEUE_MAX)
        sentinel = object()

        def pump():
            try:
                for frame in self.reader.frames(timeout_ms=100):
                    frames.put(frame)  # blocks when full -> backpressure, never drops
            except Exception as error:  # surface tap failures to the drain loop
                frames.put((sentinel, error))
            finally:
                frames.put(sentinel)

        pump_thread = asyncio.create_task(asyncio.to_thread(pump))

        self._flush_counter = 0

        def flush_batch(batch):
            if not batch:
                return
            # Legacy fallback: no config layout, so create the device from the
            # first frame's width. With a config layout the device already exists
            # (created in start() with correct n_ch/output_layout/config).
            if self._source_key not in self.writer._raw:
                width = batch[0][2].shape[1]
                self.writer.add_broadband_device(
                    self._source_key, output_layout=[f"ch_{i}" for i in range(width)])
            times = np.concatenate([np.full(rows.shape[0], t, dtype="<f8") for t, _, rows in batch])
            machine = np.concatenate([np.full(rows.shape[0], m, dtype="<f8") for _, m, rows in batch])
            samples = np.concatenate([rows for _, _, rows in batch], axis=0)
            self.writer.append_broadband(self._source_key, times, machine, samples)
            # Flush to disk periodically (not every batch) so a long recording is
            # durable without paying an fsync per write.
            self._flush_counter += 1
            if self._flush_counter % 20 == 0:
                self.writer.flush()

        batch = []
        try:
            while True:
                # Block off the event loop for the next item; drain whatever else
                # is already queued without blocking, then write one batch.
                item = await asyncio.to_thread(frames.get)
                done = False
                while True:
                    if item is sentinel:
                        done = True
                        break
                    if isinstance(item, tuple) and item and item[0] is sentinel:
                        self._failure = str(item[1])
                        self.journal.write("passive_broadband_error", error=self._failure)
                        done = True
                        break
                    batch.append(item)
                    if len(batch) >= self._BATCH_FRAMES:
                        break
                    try:
                        item = frames.get_nowait()
                    except _queue.Empty:
                        break
                if batch:
                    await asyncio.to_thread(flush_batch, batch)
                    batch = []
                if done:
                    break
        finally:
            await pump_thread

    def append_annotation(self, *, type="browser_event", name="", timing="", payload="", time_s=None):
        """Record one browser event as a /devices/0 annotation, host-stamped now."""
        import time as _time
        if self.writer is None:
            raise RuntimeError("recording is not active")
        now = _time.time()
        self.writer.append_annotation(time_s if time_s is not None else now, now,
                                      type=type, name=name, timing=timing, payload=payload)

    def check_running(self):
        if self.writer is None or (self._reader_task is not None and self._reader_task.done()
                                   and self._failure is not None):
            raise RuntimeError(f"passive recorder is no longer running: {self._failure or 'closed'}")

    async def stop(self):
        if self.writer is None:
            return {"recording": False}
        if self.reader is not None:
            self.reader.disconnect()  # ends the tap stream -> pump thread exits
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, 10)
            except (asyncio.TimeoutError, Exception):
                self._reader_task.cancel()
        data_path = self._active_folder / "data.hdf5"
        self.writer.close()
        self.writer = None
        digest = await asyncio.to_thread(sha256_file, data_path) if data_path.exists() else None
        result = {"recording": False, "mode": "passive", "data_file": str(data_path),
                  "data_sha256": digest, "broadband_error": self._failure}
        self.journal.write("passive_recording_stopped", **result)
        # Reset per-recording state so the next start/stop cycle is clean.
        self._reader_task = None
        self._failure = None
        return result


class BridgeSession:
    def __init__(self, folder, profile, provenance, recorder, *, port=8765, client=None,
                 passive=False, block=1):
        self.folder, self.profile, self.provenance = folder, profile, provenance
        self.journal = recorder.journal
        self.recorder = recorder
        self.client = client or NdjsonClient(port=port)
        self.instructor = None
        self.streams = set()
        self.seen_requests = set()
        self.recording = False
        self.task_failed = False
        self.task_started = False
        # Passive mode: the SciFi-2 is a broadband source, not the task state
        # machine. Browser events are recorded as host-clock annotations in a
        # Cognescent data.hdf5 (via the PassiveRecorder) and never proposed to the
        # device, so there is no device round trip.
        self.passive = passive
        # Passive block counter (Cognescent folder name <...>@<block>). Starts from
        # the operator-set value, is reconciled to max(ours, browser @N) at each
        # start, and increments on each stop. A session_id is minted per bridge
        # session for the 'session' stream block-exchange with the browser.
        self.block = int(block or 1)
        self.session_id = uuid.uuid4().hex[:16]
        # Extra unsolicited messages to push after a reply (e.g. a 'session'
        # stream_batch for the browser's block negotiation). The serve loop
        # drains this after sending each reply.
        self.outbox = []
        # Map GifManager CamelCase gesture keys to on-device motion ids. Only the
        # hub-and-spoke band-calibration profile translates animation events; the
        # LUT is absent for the linear MVP, in which case an animation gesture
        # event has no configured transition and is rejected rather than guessed.
        self._motion_lut = {}
        if profile.get("profile_shape") == "hub_and_spoke":
            from .motion_profile import load_motion_lut
            self._motion_lut = load_motion_lut()["lut"]

    @staticmethod
    def _protocol_base_and_block(browser_metadata):
        """(base_protocol, browser_block) from the start-recording metadata.

        The browser 'protocol' is like 'nml-wtf_Gestures_pinky_flexion@3'; the
        base is everything before '@' and the trailing int is the browser's block
        (None if absent). Falls back to app+filename_prefix_hint when protocol is
        missing."""
        protocol = browser_metadata.get("protocol") if isinstance(browser_metadata, dict) else None
        session = (browser_metadata.get("metadata", {}) or {}).get("session", {}) if isinstance(browser_metadata, dict) else {}
        if not protocol:
            app = session.get("app") or "nml-wtf"
            hint = session.get("filename_prefix_hint") or "session"
            protocol = f"{app}_{hint}"
        base, _, tail = str(protocol).partition("@")
        browser_block = int(tail) if tail.isdigit() else None
        return base, browser_block

    def _passive_folder_name(self, browser_metadata):
        """<date>-<unix>-<shortid>v-<protocol_base>@<block>, matching the operator's
        existing pipeline. Reconciles block = max(our counter, browser @N)."""
        import time as _time
        base, browser_block = self._protocol_base_and_block(browser_metadata)
        if browser_block is not None and browser_block > self.block:
            self.block = browser_block
        now = _time.time()
        date = _time.strftime("%Y-%m-%d", _time.localtime(now))
        shortid = self.session_id[:8]
        return f"{date}-{int(now)}-{shortid}v-{base}@{self.block}"

    def _enqueue_session_sample(self):
        """Push a 'session' stream_batch so the browser's SessionMetaAdapter can
        reconcile its block to max(its, ours).

        SessionMetaAdapter ignores samples without a session_id and adopts
        block = max(sample.block, its block). We send our current block and a
        stable session_id; subject/side are left for the browser to fill from its
        own UI (we do not override them). Delivered as a separate stream_batch
        message (never an api_response, which the browser routes only to pending
        requests, not to onStream)."""
        sample = {"data": {"block": self.block, "session_id": self.session_id,
                           "session": self.session_id, "version": "sciencexyz-passive"}}
        self.outbox.append({"api_version": "0.12", "stream_batch": {
            "stream_id": "session", "session": {"samples": [sample]}}})

    async def start(self, browser_metadata):
        if self.recording:
            raise ValueError("recording already active")
        metadata = copy.deepcopy(self.provenance)
        metadata["task_profile"] = self.profile
        metadata["browser_metadata"] = browser_metadata
        metadata["browser_clock_mapping"] = "unmeasured; preserve browser time fields literally"
        self.journal.write("recording_requested", profile=self.profile, browser_metadata=browser_metadata,
                           block=self.block if self.passive else None)
        if self.passive:
            # A fresh, block-named folder per start/stop cycle, beside the bridge
            # session folder, matching the operator's existing data.hdf5 layout.
            folder = self.folder.parent / self._passive_folder_name(browser_metadata)
            result = await self.recorder.start(metadata, folder)
        else:
            result = await self.recorder.start(metadata)
        self.recording = True
        return result

    def _default_event_name(self):
        """The single external event name for a profile that has exactly one.

        The linear rest/two-action MVP uses ``advance`` for every transition, so
        an explicit ``propose_task_event`` with no gesture argument is
        unambiguous. The hub-and-spoke shape has a distinct event per gesture and
        direction, so it has no default; a caller must name the event (the
        animation translation path does)."""
        names = {t["trigger"]["event_name"] for t in self.profile["definition"]["transitions"]
                 if t["trigger"].get("kind") == "external_event"}
        return next(iter(names)) if len(names) == 1 else None

    async def task_command(self, command, *, event_name=None):
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
            arguments = {}
            if command == "propose_task_event":
                name = event_name or self._default_event_name()
                if not name:
                    raise ValueError("propose_task_event requires an event_name for this profile")
                arguments["event_name"] = name
            event = await asyncio.to_thread(self.instructor.command, command, **arguments)
            self.recorder.check_running()
            return event
        except Exception as error:
            self.task_failed = True
            self.journal.write("task_failed", error=str(error))
            if self.instructor:
                await asyncio.to_thread(self.instructor.cleanup)
            raise

    async def _ensure_task_started(self):
        """Move NO_STATE -> initial (rest hub) exactly once per run.

        The band-calibration page never sends an explicit ``start_task``; it only
        emits animation events. The first such event that would propose a gesture
        transition first starts the run so the device is in the rest hub. A
        committed ``start`` is required before any proposal can be validated."""
        if not self.task_started:
            await self.task_command("start_task")
            self.task_started = True

    async def animation_event(self, sample):
        """Translate one GifManager ``browser`` animation sample into a task edge.

        Returns the committed task_transition event when the sample is a gesture
        transition (``payload.direction`` toActive/toRest), or ``None`` for a
        sample that is not an authoritative gesture edge (terminal frames with a
        null direction, rest-target markers, connect notifications). Only the
        transitioning event names both a direction and a gesture, so it is the
        single edge that drives the device; everything else is journal-only."""
        data = sample.get("data") if isinstance(sample, dict) else None
        if not isinstance(data, dict) or data.get("type") != "browser_event":
            return None
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return None
        direction = payload.get("direction")
        if direction not in ("toActive", "toRest"):
            return None  # terminal/rest-target event; journalled but not a task edge
        key = payload.get("name")
        motion_id = self._motion_lut.get(key) if isinstance(key, str) else None
        if not motion_id:
            raise ValueError(f"animation event gesture key {key!r} is not in the MOTION_LUT")
        event_name = ("to_active." if direction == "toActive" else "to_rest.") + motion_id
        await self._ensure_task_started()
        return await self.task_command("propose_task_event", event_name=event_name)

    def _record_annotation(self, sample):
        """Passive mode: append one browser sample to the Cognescent annotations.

        Returns True when a mark was written. The sample shape mirrors the
        example file's /devices/0 rows: ``type`` (browser_event), ``name``
        (rest/transitioning/active), ``timing`` (instant/start/end) and the full
        ``payload`` JSON. The browser's own event ``time`` (epoch seconds) is
        preserved when present; ``machine time`` is stamped by the recorder now.
        """
        if not self.recording:
            raise ValueError("start recording before browser annotations")
        data = sample.get("data") if isinstance(sample, dict) else None
        if not isinstance(data, dict):
            return False
        payload = data.get("payload")
        payload_json = payload if isinstance(payload, str) else json.dumps(payload, allow_nan=False) if payload is not None else ""
        self.recorder.append_annotation(
            type=str(data.get("type", "browser_event")),
            name=str(data.get("name", "")),
            timing=str(data.get("timing", "")),
            payload=payload_json,
            time_s=sample.get("time") if isinstance(sample.get("time"), (int, float)) else None)
        return True

    async def stop(self):
        self.task_started = False
        if self.instructor:
            # A task that was stopped early is explicitly aborted, never completed.
            await asyncio.to_thread(self.instructor.cleanup)
        result = await self.recorder.stop()
        self.recording = False
        if self.passive:
            # Block indexes recordings within a session; advance it so the next
            # start/stop cycle gets a new @<block> folder.
            self.block += 1
            result = {**result, "next_block": self.block}
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
                    # The browser's SessionMetaAdapter opens the 'session' stream
                    # to exchange the block index; answer with our current block.
                    if stream == "session":
                        self._enqueue_session_sample()
                else:
                    self.streams.discard(stream)
                result = {"stream_id": stream, "mode": "browser journal only; no emulated sensor output"}
            elif "change_parameter_request" in request:
                # The SessionMetaAdapter's _syncCurrentBlock sends a parameter
                # change on transform 'pipeline.session' to prompt a block sync;
                # answer with a session sample. Other transforms are accepted as a
                # no-op (we run no configurable transforms).
                transforms = request["change_parameter_request"].get("transforms", {})
                if "pipeline.session" in transforms:
                    self._enqueue_session_sample()
                result = {"changed": list(transforms)}
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
        elif stream == "browser" and self.passive:
            # Passive mode: record EVERY browser sample as a /devices/0 annotation
            # in the Cognescent data.hdf5, host-stamped on arrival. No device
            # proposal, so no round trip and no device-committed alignment; the
            # marker's clock is the host wall clock (same column as broadband
            # time). This matches the example file's browser annotations exactly.
            recorded = 0
            for sample in samples:
                if self._record_annotation(sample):
                    recorded += 1
            result = {"journaled_samples": len(samples), "stream_id": stream,
                      "annotations_recorded": recorded, "mode": "passive"}
        elif stream == "browser" and self._motion_lut:
            # GifManager animation events. Each gesture-transition sample is
            # translated in wire order into a device-authoritative propose_task_event;
            # non-transition samples (terminal frames, rest-target markers) are
            # journalled only. The committed events are reported for visibility,
            # but behavior clients still react only to the device task_transition
            # tap, never to this reply.
            committed = []
            for sample in samples:
                event = await self.animation_event(sample)
                if event is not None:
                    committed.append(event)
            result = {"journaled_samples": len(samples), "stream_id": stream,
                      "authoritative_task_event": bool(committed), "committed_events": committed}
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
            passive = getattr(args, "passive", False)
            if passive:
                # provenance embeds the device config at configuration_input.snapshot;
                # the recorder derives broadband channel layout(s) from it.
                recorder = PassiveRecorder(args.device_uri, folder, journal, config=provenance)
            else:
                recorder = RecorderProcess(str(Path(args.recorder).resolve()), args.device_uri, folder, journal)
            session = BridgeSession(folder, profile, provenance, recorder,
                                    port=args.service_port, passive=passive,
                                    block=int(getattr(args, "block", 1) or 1))
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
                # Flush any unsolicited pushes the handler queued (e.g. the
                # 'session' block-exchange stream_batch), after the reply.
                while session.outbox:
                    await socket.send(json.dumps(session.outbox.pop(0), allow_nan=False))
        finally:
            try:
                if session:
                    await session.close()
            finally:
                owner = False

    # Bind BOTH loopback families. The Reactions page (CtrlrSocketClient.connect)
    # always dials ws://localhost:<port>; on Windows "localhost" commonly resolves
    # to IPv6 ::1 before IPv4 127.0.0.1, so binding only 127.0.0.1 leaves the
    # browser's ::1 attempt refused even though a 127.0.0.1 client connects. A
    # sequence of hosts is passed through to loop.create_server, which listens on
    # each; this stays loopback-only (no external interface).
    async with serve(handler, ["127.0.0.1", "::1"], args.port, origins=args.origin,
                     max_size=256 * 1024, max_queue=8):
        print(f"Reactions bridge ws://localhost:{args.port} (127.0.0.1 + ::1); "
              f"origins={args.origin}", flush=True)
        await asyncio.Future()
