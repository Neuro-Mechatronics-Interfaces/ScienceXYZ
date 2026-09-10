"""Write a Cognescent Data Format HDF5 for the passive calibration workflow.

Passive mode treats the SciFi-2 as a broadband *source*, not the task state
machine. The calibration task lives in the browser/bridge, so this file is
produced entirely host-side and matches the ``data.hdf5`` layout the operator's
existing analysis pipelines already read (see data/example/data.hdf5):

    /                       @filetype = "Cognescent Data Format Version 0.0"
    /metadata               @app, @task_name, @dataset_suffix, ... (from the
                            browser start-recording metadata)
    /devices/0/             @uid="browser" @devicetype="annotations"
        timeseries/time, machine time, type, name, timing, payload
    /devices/1/             @uid="raw" @devicetype="stream" @n_ch @fs @start
        timeseries/time, machine time, stream (N, n_ch)

Clock convention (matches the example; both columns are epoch seconds float64):
- browser annotations: ``time`` = browser event time, ``machine time`` = host
  receive time stamped at the bridge on arrival.
- raw broadband: ``time`` = the frame's device ``unix_timestamp_ns`` (so
  broadband and annotations share a wall clock), ``machine time`` = the host
  receive time stamped at the bridge. Device per-sample timing is preserved via
  ``time``; the raw source timestamps are never discarded.

Only the two devices the passive SciFi workflow produces are written; the many
Ceres-specific devices in the reference file (battery, pointer, udp_latency, ...)
are not applicable and are intentionally omitted.
"""
from __future__ import annotations

from pathlib import Path

FILETYPE = "Cognescent Data Format Version 0.0"

# Metadata keys carried verbatim from the browser start-recording metadata's
# session block into /metadata attrs. Missing keys are simply skipped.
_METADATA_KEYS = (
    "app", "task_name", "task_parameters", "dataset_suffix", "filename_prefix_hint",
    "session_hash", "block", "subject", "experimenter", "user", "protocol",
    "study_version_id", "sid", "project_team", "location", "platform",
    "web_timestamp", "notes",
)


class CognescentWriter:
    """Live writer for a passive-mode ``data.hdf5`` (browser + broadband)."""

    def __init__(self, path):
        self.path = Path(path)
        self._file = None
        self._ann = {}          # /devices/0 annotation datasets by column
        self._raw = {}          # source_key -> {n_ch, cols:{time,machine time,stream}}
        self._next_device = 1   # /devices/0 is the browser; broadband starts at 1

    # -- lifecycle -----------------------------------------------------------
    def open(self, *, metadata=None):
        import h5py
        # Exclusive create: never silently overwrite an existing capture.
        self._file = h5py.File(self.path, "x")
        self._file.attrs["filetype"] = FILETYPE
        self._write_metadata(metadata or {})
        self._create_browser_device()
        self._file.flush()
        return self

    def _write_metadata(self, metadata):
        group = self._file.create_group("metadata")
        # The browser start-recording payload nests operator metadata under
        # data.metadata.session; accept either that or a flat dict.
        session = metadata.get("session", metadata) if isinstance(metadata, dict) else {}
        for key in _METADATA_KEYS:
            if key in session and session[key] is not None:
                group.attrs[key] = str(session[key])

    def _vlen(self):
        import h5py
        return h5py.string_dtype(encoding="utf-8")

    def _create_browser_device(self):
        import h5py
        dev = self._file.create_group("devices/0")
        dev.attrs["uid"] = "browser"
        dev.attrs["device"] = "browser"
        dev.attrs["devicetype"] = "annotations"
        dev.attrs["stream_name"] = "browser"
        dev.attrs["encoding"] = "json"
        ts = dev.create_group("timeseries")
        vlen = self._vlen()
        self._ann["time"] = ts.create_dataset("time", shape=(0,), maxshape=(None,), dtype="<f8", chunks=(256,))
        self._ann["machine time"] = ts.create_dataset("machine time", shape=(0,), maxshape=(None,), dtype="<f8", chunks=(256,))
        for col in ("type", "name", "timing", "payload"):
            self._ann[col] = ts.create_dataset(col, shape=(0,), maxshape=(None,), dtype=vlen, chunks=(256,))

    def add_broadband_device(self, source_key, *, output_layout, sample_rate_hz=None,
                             config_json=None, uid="raw", start_unix_s=None):
        """Create a broadband stream device (its own /devices/<n>) for one source.

        ``output_layout`` is the ordered channel-name list from the config (its
        length is the channel count, the source of truth -- never inferred from
        the wire). ``config_json`` records exactly which kBroadbandSource config
        produced this stream. Multiple sources each get their own device group.
        """
        if source_key in self._raw:
            return  # already created
        import json as _json
        n_ch = len(output_layout)
        index = self._next_device
        self._next_device += 1
        dev = self._file.create_group(f"devices/{index}")
        dev.attrs["uid"] = str(uid)
        dev.attrs["device"] = "scifi:broadband"
        dev.attrs["devicetype"] = "stream"
        dev.attrs["stream_name"] = str(uid)
        dev.attrs["encoding"] = "json"
        dev.attrs["n_ch"] = n_ch
        dev.attrs["output_layout"] = _json.dumps(list(output_layout))
        if sample_rate_hz is not None:
            dev.attrs["fs"] = float(sample_rate_hz)
        if config_json is not None:
            dev.attrs["config"] = str(config_json)  # the kBroadbandSource config used
        if start_unix_s is not None:
            dev.attrs["start"] = float(start_unix_s)
        ts = dev.create_group("timeseries")
        cols = {
            "time": ts.create_dataset("time", shape=(0,), maxshape=(None,), dtype="<f8", chunks=(1024,)),
            "machine time": ts.create_dataset("machine time", shape=(0,), maxshape=(None,), dtype="<f8", chunks=(1024,)),
            "stream": ts.create_dataset("stream", shape=(0, n_ch), maxshape=(None, n_ch),
                                        dtype="<f4", chunks=(1024, n_ch)),
        }
        self._raw[source_key] = {"n_ch": n_ch, "cols": cols}
        self._file.flush()

    # -- appends -------------------------------------------------------------
    def append_annotation(self, time_s, machine_time_s, *, type="", name="", timing="", payload=""):
        index = self._ann["time"].shape[0]
        for col, value in (("time", float(time_s)), ("machine time", float(machine_time_s)),
                           ("type", str(type)), ("name", str(name)),
                           ("timing", str(timing)), ("payload", str(payload))):
            ds = self._ann[col]
            ds.resize((index + 1,))
            ds[index] = value
        self._file.flush()  # annotations are sparse; keep them across a crash

    def append_broadband(self, source_key, times_s, machine_times_s, samples):
        """Append a block of broadband rows for one source. ``samples`` is (rows, n_ch)."""
        import numpy as np
        entry = self._raw.get(source_key)
        if entry is None:
            raise KeyError(f"broadband device {source_key!r} was not created; call add_broadband_device first")
        n_ch = entry["n_ch"]
        cols = entry["cols"]
        samples = np.asarray(samples, dtype="<f4")
        if samples.ndim != 2 or samples.shape[1] != n_ch:
            raise ValueError(f"samples must be (rows, {n_ch}); got {samples.shape}")
        rows = samples.shape[0]
        base = cols["stream"].shape[0]
        cols["stream"].resize((base + rows, n_ch))
        cols["stream"][base:base + rows] = samples
        for col, values in (("time", times_s), ("machine time", machine_times_s)):
            ds = cols[col]
            ds.resize((base + rows,))
            ds[base:base + rows] = np.asarray(values, dtype="<f8")

    def flush(self):
        if self._file is not None:
            self._file.flush()

    def close(self):
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            finally:
                self._file = None
                self._ann = {}
                self._raw = {}
