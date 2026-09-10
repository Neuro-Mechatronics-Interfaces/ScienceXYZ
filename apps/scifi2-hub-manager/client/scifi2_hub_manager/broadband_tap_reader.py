"""Read the SciFi-2 broadband tap in Python for the passive workflow.

Passive mode ingests broadband host-side (in the bridge) instead of via the C++
recorder subprocess, so a single bridge process can write one Cognescent
``data.hdf5`` (broadband + browser annotations). This wraps the ``science-synapse``
Python ``Tap`` client: connect to the named broadband tap, receive
``BroadbandFrame`` messages, and yield decoded rows.

Each frame carries ``frame_data`` (interleaved samples), ``sample_rate_hz``,
``timestamp_ns`` (device monotonic) and ``unix_timestamp_ns`` (device wall clock).
The reader yields, per frame:

    (unix_time_s, host_receive_s, samples_2d)

where ``unix_time_s`` is the frame's device wall-clock (``unix_timestamp_ns``),
``host_receive_s`` is ``time.time()`` stamped when the bridge received the frame,
and ``samples_2d`` is shaped ``(rows, n_ch)``. The channel count is inferred from
the first frame's ``frame_data`` length divided by the reported channel count
(``channel_ranges``), or treated as a single row when the layout is unknown.
"""
from __future__ import annotations

import time


class BroadbandTapReader:
    """Iterate decoded broadband rows from a Synapse broadband tap."""

    def __init__(self, device_uri, tap_name="broadband_out", *, verbose=False, expected_n_ch=None):
        self.device_uri = device_uri
        self.tap_name = tap_name
        self.verbose = verbose
        # Channel count is taken from the device config (source of truth), not
        # inferred from a frame's length. When None, fall back to per-frame
        # inference (legacy) but that risks locking a wrong width.
        self._n_ch = int(expected_n_ch) if expected_n_ch else None
        self._from_config = expected_n_ch is not None
        self.decode_errors = 0   # frames whose length was not a multiple of n_ch

    def connect(self):
        from synapse.client.taps import Tap
        self._tap = Tap(self.device_uri, verbose=self.verbose)
        if not self._tap.connect(self.tap_name):
            raise RuntimeError(f"could not connect broadband tap {self.tap_name!r} at {self.device_uri}")
        return self

    @property
    def n_ch(self):
        return self._n_ch

    def _decode(self, raw):
        """Decode one BroadbandFrame -> (unix_time_s, host_recv_s, samples_2d) or None."""
        import numpy as np
        from synapse.api import datatype_pb2
        host_recv_s = time.time()
        frame = datatype_pb2.BroadbandFrame()
        try:
            frame.ParseFromString(raw)
        except Exception:
            return None  # malformed frame; skip (the raw recorder would journal it)
        # frame_data is repeated integer samples; the Cognescent stream dataset is
        # float32, so cast on the way in (values are preserved, not rescaled).
        data = np.asarray(frame.frame_data, dtype="<i4").astype("<f4")
        # Channel count: the config's value if we were given one (authoritative),
        # else legacy inference from channel_ranges / frame length.
        if self._n_ch is None:
            inferred = len(frame.channel_ranges) or data.size
            if inferred <= 0:
                return None
            self._n_ch = int(inferred)
        if self._n_ch <= 0 or data.size % self._n_ch != 0:
            # A frame whose length is not a multiple of the (config) channel count
            # is a decode error -- count and drop it rather than reshape wrongly.
            self.decode_errors += 1
            return None
        rows = data.reshape(-1, self._n_ch)
        unix_time_s = (frame.unix_timestamp_ns or frame.timestamp_ns) / 1e9
        return unix_time_s, host_recv_s, rows

    def frames(self, timeout_ms=100):
        """Yield decoded broadband blocks until the tap stream ends."""
        if self._tap is None:
            raise RuntimeError("BroadbandTapReader is not connected")
        for raw in self._tap.stream(timeout_ms=timeout_ms):
            if not raw:
                continue
            decoded = self._decode(raw)
            if decoded is not None:
                yield decoded

    def sample_rate_hz(self):
        # Best-effort: not all builds populate a static rate before the first
        # frame; callers may pass the per-frame rate through instead.
        return None

    def disconnect(self):
        if self._tap is not None:
            try:
                self._tap.disconnect()
            except Exception:
                pass
            self._tap = None
