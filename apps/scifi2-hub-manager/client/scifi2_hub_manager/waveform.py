"""Read-only live waveform viewer for the ``broadband_out`` producer tap.

This module never sends a device command. It subscribes to the App's
``broadband_out`` producer tap exactly like ``broadband_probe.py`` and can be
run alongside the control GUI or CLI clients while another operator owns source
mode and capture. Each ``BroadbandFrame`` is one time sample across all
channels (``frame_data`` indexed by channel id), so a live trace is built by
appending one ``frame_data[ch]`` value per received frame into a per-channel
rolling ring buffer.

The design mirrors the control GUI's threading contract: Synapse Taps are only
read on a background thread, and the Qt view only ever reads immutable
snapshots produced by the pure :class:`WaveformBuffer`. The buffer and the
reader carry no Qt or plotting dependency so they can be unit tested with
injected frames.

The buffer retains every channel the stream carries (up to a cap); which
channels are displayed, in what order, and in what grid is an independent,
Qt-free view decision computed by :func:`parse_channel_spec` and
:func:`grid_placements`, and adjustable live in the window (column count and an
ordered channel selection). Hiding or reordering channels never drops data.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from synapse.api.datatype_pb2 import BroadbandFrame
from synapse.api.channel_pb2 import GPIO

# Pin pyqtgraph to the same Qt binding the control GUI uses. pyqtgraph would
# otherwise pick whichever binding it finds first (PyQt5 is preferred by
# default), producing a widget the PySide6 layout rejects. This is a hard
# assignment, not setdefault, because the client is committed to PySide6; it
# runs at module import time, before the lazy pyqtgraph import below.
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"


def observed_sample_rate_hz(timestamps: np.ndarray) -> float | None:
    """Estimate the actual sample rate from per-sample device timestamps (ns).

    Returns samples/second over the span of the buffered timestamps, or None when
    fewer than two samples, a non-positive span, or a non-monotone span makes the
    estimate meaningless (e.g. a source-clock regression across a reconnect). This
    is a cross-check against the wire-declared ``sample_rate_hz`` and the config's
    expected rate, not a replacement for either.
    """
    if timestamps is None or len(timestamps) < 2:
        return None
    span_ns = float(timestamps[-1]) - float(timestamps[0])
    if span_ns <= 0.0:
        return None
    return (len(timestamps) - 1) * 1e9 / span_ns


def _kapplication_params(config):
    """The first kApplication node's parameters dict, or {} if none."""
    if not isinstance(config, dict):
        return {}
    for node in config.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "kApplication":
            app = node.get("application") or {}
            return app.get("parameters") or {}
    return {}


def decimation_factor_from_params(params, source_rate_hz):
    """Replicate the C++ App's integer decimation-factor choice for a source.

    Mirrors make_feature_decimation_plan (feature_decimator.hpp): the largest
    integer factor that (1) keeps a guard band above the highest configured MPF
    edge and (2) divides both the raw window and stride exactly. Returns 1 when
    frequency_bands_hz is absent (the App disables decimation) or the params are
    unusable. Lets the viewer predict broadband_out's decimated rate from the same
    config the operator deploys, instead of assuming the raw source rate.
    """
    try:
        bands = params.get("frequency_bands_hz")
        if not bands:
            return 1  # legacy full-Nyquist split => no decimation
        highest = float(bands[-1][1])
        guard = float(params.get("decimation_guard_ratio", 1.25))
        window_ms = float(params.get("window_ms", 0.0))
        stride_ms = float(params.get("stride_ms", 0.0))
        source = float(source_rate_hz)
        if not (highest > 0 and guard > 1.0 and window_ms > 0 and stride_ms > 0 and source > 0):
            return 1
        raw_window = round(window_ms * source / 1000.0)
        raw_stride = max(1, round(stride_ms * source / 1000.0))
        guarded_nyquist = highest * guard
        import math
        band_limited = int(math.floor(source / (2.0 * guarded_nyquist)))
        limit = max(1, min(band_limited, raw_window))
        for candidate in range(limit, 1, -1):
            if raw_window % candidate == 0 and raw_stride % candidate == 0:
                return candidate
        return 1
    except (TypeError, ValueError, IndexError, KeyError):
        return 1


def broadband_layouts_from_config_path(path):
    """Parse kBroadbandSource layouts from a device-config JSON path.

    Returns a list of BroadbandLayout (possibly empty). Any read/parse error
    yields an empty list; the viewer then falls back to wire inference and shows
    no per-source metadata. Kept tolerant so a bad/blank path never blocks the
    viewer from opening.
    """
    if not path:
        return []
    try:
        import json as _json
        from .broadband_layout import layouts_from_config
        with open(path, "r", encoding="utf-8") as handle:
            return layouts_from_config(_json.load(handle))
    except (OSError, ValueError):
        return []


def _info_shows_app_running(info_text: str, app_name: str = "scifi2-hub-manager") -> bool:
    """True when a ``synapsectl info`` capture shows the App reporting Running: True.

    Same parse as the calibration launcher's ``app_running``: match the App
    section by name, then the first ``Running: <bool>`` line before the next
    entry. Kept local so the waveform viewer needs no launcher import; the
    device gate here is advisory (a status label), not a hard start gate.
    """
    lines = info_text.splitlines()
    for index, line in enumerate(lines):
        if app_name not in line:
            continue
        for follow in lines[index + 1:]:
            match = re.search(r"Running\s*:\s*(True|False)", follow, re.IGNORECASE)
            if match:
                return match.group(1).lower() == "true"
            if not follow.strip():
                break
    return False


@dataclass(frozen=True)
class WaveformSnapshot:
    """An immutable view of the rolling buffer for the Qt thread to plot."""

    channel_ids: tuple[int, ...]
    #: Shape ``(num_channels, num_samples)``; column ``-1`` is the newest sample.
    samples: np.ndarray
    sample_rate_hz: int
    frames: int
    missing_sequences: int
    parse_errors: int
    sequence_last: int | None
    timestamp_last: int | None
    timestamps: np.ndarray
    sync_edges: np.ndarray  # rows: GPIO 0 rise/fall, GPIO 1 rise/fall
    nominal_times: np.ndarray  # seconds relative to newest sample; display only
    connections: np.ndarray  # connect sample i to i+1 only within continuity


@dataclass(frozen=True)
class GridPlacement:
    """One displayed channel's position in the plot grid."""

    channel_id: int
    row: int
    col: int


def parse_channel_spec(spec: str, available: int) -> list[int]:
    """Parse a channel selection/order spec into an ordered list of channel ids.

    Accepts comma- and/or whitespace-separated tokens, each either a single
    index (``5``) or an inclusive range (``0-7`` ascending, ``7-0`` descending).
    Order is preserved as written, so the spec doubles as a reordering:
    ``"0-3, 8-11"`` and ``"3,2,1,0"`` are both valid. ``available`` is the
    number of channels the stream currently carries; every referenced id must
    satisfy ``0 <= id < available``. Duplicates are allowed (a channel may be
    shown more than once). An empty spec selects every available channel in
    natural order.

    Raises ``ValueError`` with a human-readable message on malformed input or an
    out-of-range id, so the GUI can show it without changing the live layout.
    """
    if available <= 0:
        raise ValueError("no channels are available yet")
    text = spec.strip()
    if not text:
        return list(range(available))

    ordered: list[int] = []
    for token in text.replace(",", " ").split():
        if "-" in token[1:]:  # allow a leading '-' only inside a range, not negatives
            lo_text, _, hi_text = token.partition("-")
            # A leading '-' (negative index) is rejected by int() range check below.
            try:
                lo = int(lo_text)
                hi = int(hi_text)
            except ValueError:
                raise ValueError(f"invalid channel range: {token!r}")
            step = 1 if hi >= lo else -1
            values = range(lo, hi + step, step)
        else:
            try:
                values = [int(token)]
            except ValueError:
                raise ValueError(f"invalid channel token: {token!r}")
        for value in values:
            if not 0 <= value < available:
                raise ValueError(
                    f"channel {value} is out of range 0..{available - 1}")
            ordered.append(value)
    if not ordered:
        raise ValueError("channel spec selected no channels")
    return ordered


def parse_gain_spec(spec: str) -> dict[int, float]:
    """Parse per-channel vertical-gain overrides into ``{channel_id: gain}``.

    Accepts comma- and/or whitespace-separated ``channel:gain`` tokens, e.g.
    ``"0:2, 4:0.5 8:10"``. ``channel`` is a non-negative integer id; ``gain`` is
    a positive float multiplier applied to that channel's displayed amplitude
    (a larger gain magnifies the trace). An empty spec is no overrides. The
    last value wins if a channel is listed twice.

    Raises ``ValueError`` with a human-readable message on a malformed token or
    a non-positive gain, so the GUI can report it without changing the layout.
    """
    text = spec.strip()
    if not text:
        return {}
    gains: dict[int, float] = {}
    for token in text.replace(",", " ").split():
        if ":" not in token:
            raise ValueError(f"expected 'channel:gain', got {token!r}")
        ch_text, _, gain_text = token.partition(":")
        try:
            channel = int(ch_text)
        except ValueError:
            raise ValueError(f"invalid channel in {token!r}")
        try:
            gain = float(gain_text)
        except ValueError:
            raise ValueError(f"invalid gain in {token!r}")
        if channel < 0:
            raise ValueError(f"channel {channel} is negative")
        if not gain > 0 or not np.isfinite(gain):
            raise ValueError(f"gain for channel {channel} must be positive")
        gains[channel] = gain
    return gains


def grid_placements(channel_ids, columns: int) -> list[GridPlacement]:
    """Place ``channel_ids`` row-major into a grid ``columns`` wide.

    ``columns`` clamps to ``[1, len(channel_ids)]``. The number of rows follows
    from the count, so 32 channels at 8 columns give a 4x8 grid; the same 32 at
    1 column give a single stacked column. Placement order matches the input
    order, so the caller controls both selection and arrangement.
    """
    ids = list(channel_ids)
    if not ids:
        return []
    columns = max(1, min(int(columns), len(ids)))
    return [
        GridPlacement(channel_id=cid, row=index // columns, col=index % columns)
        for index, cid in enumerate(ids)
    ]


class WaveformBuffer:
    """Pure, Qt-free per-channel rolling buffer built from ``BroadbandFrame``.

    The buffer is sized to ``duration_s * expected_sample_rate_hz`` samples and
    retains up to ``max_channels`` channels. The channel set is discovered from
    the first frame and held fixed; frames whose width changes afterwards are
    truncated/zero-extended to the established width so the plot geometry is
    stable. Sequence gaps and protobuf parse failures are counted separately,
    matching ``broadband_probe.FrameStats``.

    The buffer retains *all* channels the stream carries (up to ``max_channels``,
    32 by default). Which retained channels are displayed, in what order, and in
    what grid is a pure-view decision (see :func:`parse_channel_spec` and
    :func:`grid_placements`); the buffer itself imposes no display layout.
    """

    def __init__(
        self,
        *,
        duration_s: float = 2.0,
        expected_sample_rate_hz: int = 20_000,
        max_channels: int = 32,
    ) -> None:
        if duration_s <= 0:
            raise ValueError("duration_s must be positive")
        if expected_sample_rate_hz <= 0:
            raise ValueError("expected_sample_rate_hz must be positive")
        if max_channels <= 0:
            raise ValueError("max_channels must be positive")
        self._capacity = max(1, int(round(duration_s * expected_sample_rate_hz)))
        self._max_channels = max_channels
        self._sample_rate_hz = expected_sample_rate_hz
        self._channel_ids: tuple[int, ...] = ()
        self._ring: np.ndarray | None = None
        self._timestamps = np.zeros(self._capacity, dtype=np.int64)
        self._steps = np.zeros(self._capacity, dtype=np.float64)
        self._continuous = np.zeros(self._capacity, dtype=bool)
        self._break_pending = True
        self._edges = np.zeros((4, self._capacity), dtype=bool)
        self._gpio_previous = {}
        self._filled = 0
        self._write = 0
        self.frames = 0
        self.parse_errors = 0
        self.missing_sequences = 0
        self.sequence_last: int | None = None
        self.timestamp_last: int | None = None
        # Per-frame sequence-number stride of the stream. broadband_out carries
        # the FIR-center *source* sequence, which advances by the decimation
        # factor (e.g. 10 for 20 kHz -> 2 kHz), not by 1. Learn the stride as the
        # smallest positive delta observed so a uniform decimated stream reads as
        # contiguous and a genuine drop (a delta that is a larger multiple of the
        # stride) is still counted. None until the first positive delta is seen.
        self._sequence_stride: int | None = None
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def available_channels(self) -> int:
        """Number of channels retained (0 until the first frame arrives)."""
        with self._lock:
            return len(self._channel_ids)

    def reset_sync_continuity(self) -> None:
        with self._lock:
            self._gpio_previous = {}
            self._break_pending = True

    def add_raw(self, raw: bytes) -> bool:
        """Parse one wire payload and append it; return False if malformed."""
        frame = BroadbandFrame()
        try:
            frame.ParseFromString(raw)
        except Exception:
            with self._lock:
                self.parse_errors += 1
                self._gpio_previous = {}
                self._break_pending = True
            return False
        # A payload that decodes to zero channels is counted as malformed in
        # add_frame, so both parse and empty-frame failures land in parse_errors.
        return self.add_frame(frame)

    def add_frame(self, frame: BroadbandFrame) -> bool:
        """Append one already-decoded frame; return False if it carries no data."""
        values = np.asarray(frame.frame_data, dtype=np.float64)
        if values.size == 0:
            with self._lock:
                self.parse_errors += 1
                self._gpio_previous = {}
                self._break_pending = True
            return False

        with self._lock:
            if self._ring is None:
                width = min(values.size, self._max_channels)
                self._channel_ids = tuple(range(width))
                self._ring = np.zeros((width, self._capacity), dtype=np.float64)

            width = len(self._channel_ids)
            column = np.zeros(width, dtype=np.float64)
            usable = min(width, values.size)
            column[:usable] = values[:usable]
            # Detect from the full wire frame, independent of displayed channels.
            sequence = int(frame.sequence_number)
            timestamp = int(frame.timestamp_ns)
            rate = int(frame.sample_rate_hz) or self._sample_rate_hz
            delta = sequence - self.sequence_last if self.sequence_last is not None else 0
            # Learn the stream's per-frame sequence stride (the decimation factor
            # for broadband_out; 1 for an undecimated stream) as the smallest
            # positive delta seen. Until learned, treat the frame as a fresh line.
            if delta > 0 and (self._sequence_stride is None or delta < self._sequence_stride):
                self._sequence_stride = delta
            stride = self._sequence_stride or 1
            # A stride-sized step is contiguous; an integer multiple of the stride
            # is a real gap of (delta/stride) frames. Non-multiples imply a stride
            # change (e.g. reconfiguration) and break the line.
            on_grid = delta > 0 and delta % stride == 0
            frame_gap = delta // stride if on_grid else 1
            contiguous = (self.sequence_last is not None
                          and delta == stride and rate == self._sample_rate_hz
                          and not self._break_pending)
            # Reconstruct display spacing from sample identity, never arrival/source
            # timestamp jitter. Positive gaps retain missing-frame duration measured
            # in stride units. A reset/reconnect/rate change breaks the line.
            self._steps[self._write] = (frame_gap if rate == self._sample_rate_hz else 1) / rate
            self._continuous[self._write] = contiguous
            self._break_pending = False
            current = {}
            offset = 0
            for channel_range in frame.channel_ranges:
                ids = list(channel_range.channel_ids) or list(range(channel_range.count))
                if channel_range.type == GPIO and len(ids) == channel_range.count:
                    for j, pin in enumerate(ids):
                        if pin in (0, 1) and offset + j < values.size:
                            current[pin] = (offset + j, bool(values[offset + j]))
                offset += channel_range.count
            self._edges[:, self._write] = False
            for pin, (position, high) in current.items():
                previous = self._gpio_previous.get(pin)
                if contiguous and previous is not None and previous[0] == position and previous[1] != high:
                    self._edges[2 * pin + (0 if high else 1), self._write] = True
            self._gpio_previous = current
            self._timestamps[self._write] = timestamp
            self._ring[:, self._write] = column
            self._write = (self._write + 1) % self._capacity
            self._filled = min(self._filled + 1, self._capacity)

            # Count dropped frames in stride units: a stride-sized step is zero
            # missing; an N*stride step is (N-1) missing decimated frames.
            if self.sequence_last is not None and on_grid and frame_gap > 1:
                self.missing_sequences += frame_gap - 1
            self.sequence_last = sequence
            self.timestamp_last = int(frame.timestamp_ns)
            if frame.sample_rate_hz:
                self._sample_rate_hz = int(frame.sample_rate_hz)
            self.frames += 1
        return True

    def snapshot(self) -> WaveformSnapshot:
        """Return an immutable, time-ordered copy of the current buffer."""
        with self._lock:
            if self._ring is None or self._filled == 0:
                samples = np.zeros((len(self._channel_ids), 0), dtype=np.float64)
            elif self._filled < self._capacity:
                samples = self._ring[:, : self._write].copy()
            else:
                samples = np.concatenate(
                    (self._ring[:, self._write :], self._ring[:, : self._write]),
                    axis=1,
                )
            indices = (np.arange(self._filled) + self._write - self._filled) % self._capacity
            nominal_times = np.zeros(self._filled, dtype=np.float64)
            if self._filled > 1:
                nominal_times[:-1] = -np.cumsum(self._steps[indices[1:]][::-1])[::-1]
            connections = np.zeros(self._filled, dtype=bool)
            if self._filled > 1:
                connections[:-1] = self._continuous[indices[1:]]
            return WaveformSnapshot(
                channel_ids=self._channel_ids,
                samples=samples,
                sample_rate_hz=self._sample_rate_hz,
                frames=self.frames,
                missing_sequences=self.missing_sequences,
                parse_errors=self.parse_errors,
                sequence_last=self.sequence_last,
                timestamp_last=self.timestamp_last,
                timestamps=self._timestamps[indices].copy(),
                sync_edges=self._edges[:, indices].copy(),
                nominal_times=nominal_times,
                connections=connections,
            )


class BroadbandStreamReader:
    """Background reader that fills a :class:`WaveformBuffer` from a Tap.

    Cancellation follows ``broadband_probe.py``: the reader polls
    ``tap.read(timeout_ms=...)`` so a stop flag is observed between reads, and
    the tap is disconnected on stop. A ``tap_factory`` is injectable so the
    reader can be exercised without a device.
    """

    def __init__(
        self,
        device_ip: str,
        buffer: WaveformBuffer,
        *,
        tap_name: str = "broadband_out",
        read_timeout_ms: int = 100,
        tap_factory=None,
    ) -> None:
        self._device_ip = device_ip
        self._buffer = buffer
        self._tap_name = tap_name
        self._read_timeout_ms = read_timeout_ms
        self._tap_factory = tap_factory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.connected = False

    def _make_tap(self):
        if self._tap_factory is not None:
            return self._tap_factory(self._device_ip)
        from synapse.client.taps import Tap

        return Tap(self._device_ip)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="waveform-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        tap = self._make_tap()
        if not tap.connect(self._tap_name):
            self.error = f"failed to connect to producer tap '{self._tap_name}' at {self._device_ip}"
            return
        self.connected = True
        try:
            while not self._stop.is_set():
                raw = tap.read(timeout_ms=self._read_timeout_ms)
                if raw is None:
                    continue
                self._buffer.add_raw(raw)
        except Exception as exc:  # surfaced to the view; never raised on the Qt thread
            self.error = str(exc)
        finally:
            self.connected = False
            try:
                tap.disconnect()
            except Exception:
                pass

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None


def create_waveform_window(
    device_ip: str,
    *,
    duration_s: float = 2.0,
    expected_sample_rate_hz: int = 20_000,
    max_channels: int = 32,
    columns: int = 1,
    channel_spec: str = "",
    tap_name: str = "broadband_out",
    y_full_scale: float = 1000.0,
    timescale_s: float = 0.0,
    device_config: str = "",
    synapsectl_command: str = "synapsectl",
    explicit_settings=None,
    settings_path=None,
    reader_factory=None,
):
    """Build the live waveform window. Qt/pyqtgraph imports stay local.

    Layout is interactive: a controls bar sets the number of grid columns and a
    channel spec (see :func:`parse_channel_spec`) selecting which channels are
    shown and in what order. ``columns`` and ``channel_spec`` seed the initial
    layout; ``max_channels`` bounds how many channels the buffer retains.

    Amplitude is a fixed scale, not auto-ranged: ``y_full_scale`` is the base
    half-amplitude of every plot's y-window, and a Scale sub-panel adjusts a
    global and per-channel vertical gain (see :func:`parse_gain_spec`) plus a
    shared timescale (the x-window duration). ``timescale_s`` seeds that
    timescale; ``0`` uses the full buffer ``duration_s``.
    """
    from PySide6.QtCore import QProcess, QSettings, QTimer
    from PySide6.QtWidgets import (
        QDoubleSpinBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
        QMainWindow, QPushButton, QSpinBox, QTabBar, QVBoxLayout, QWidget, QCheckBox,
    )

    try:
        import pyqtgraph as pg
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
        raise ModuleNotFoundError(
            "pyqtgraph is required for the waveform viewer. Install the client's "
            "'waveform' extra: pip install -e 'apps/scifi2-hub-manager/client[waveform]'"
        ) from exc

    # Expected per-source layouts from the device config (empty if none/bad path).
    # Drives the source-metadata selector; all sources currently read the single
    # broadband_out tap (per-source taps are a future App change). Alongside each
    # layout, precompute the rate broadband_out actually emits for that source:
    # the source rate divided by the App's integer decimation factor (from the
    # kApplication params), so the expected-rate check matches the decimated tap
    # rather than the raw source rate.
    source_layouts = broadband_layouts_from_config_path(device_config)
    _app_params = {}
    if device_config:
        try:
            import json as _json
            with open(device_config, "r", encoding="utf-8") as _handle:
                _app_params = _kapplication_params(_json.load(_handle))
        except (OSError, ValueError):
            _app_params = {}
    expected_broadband_rate = {}
    for _lay in source_layouts:
        if _lay.sample_rate_hz:
            factor = decimation_factor_from_params(_app_params, _lay.sample_rate_hz)
            expected_broadband_rate[_lay.node_id] = float(_lay.sample_rate_hz) / factor

    class WaveformWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"Broadband waveforms — {device_ip} / {tap_name}")
            from .appicon import apply_app_icon
            apply_app_icon(self)
            self.resize(1100, 720)
            self.buffer = WaveformBuffer(
                duration_s=duration_s,
                expected_sample_rate_hz=expected_sample_rate_hz,
                max_channels=max_channels,
            )
            # A reader is single-use (its stop flag stays set after stop()), so
            # Reconnect builds a fresh one from this factory rather than
            # restarting the old instance. The buffer is shared/reused across
            # readers, preserving history and counters across a reconnect.
            self._reader_factory = reader_factory or (
                lambda ip, buf: BroadbandStreamReader(ip, buf, tap_name=tap_name)
            )
            self.reader = self._reader_factory(device_ip, self.buffer)

            # Active layout: an ordered channel list and a column count. Empty
            # channel list means "all available, natural order" until frames
            # arrive and a concrete list is resolved.
            self._active_channels: list[int] = []
            self._active_columns: int = max(1, columns)
            self._curves: dict[int, object] = {}  # position index -> curve
            self._sync_curves = {}
            self._plots: dict[int, object] = {}  # position index -> PlotItem
            self._placements: list[GridPlacement] = []
            self._resolved_available = 0
            # Cached time axis so the per-tick redraw allocates a new x-vector
            # only when the sample count changes, not on every frame.
            self._time_axis: np.ndarray | None = None
            self._time_axis_key: tuple[int, int] | None = None

            # Fixed vertical scale and shared timescale. Plots do not auto-range;
            # each shows a fixed y-window of [-full_scale/gain, +full_scale/gain]
            # (per-channel gain magnifies the trace) and a shared x-window of the
            # most recent _timescale_s seconds. Seeded to the buffer duration.
            self._full_scale: float = float(y_full_scale)
            self._timescale_s: float = float(timescale_s or duration_s)
            self._buffer_duration_s: float = float(duration_s)
            self._global_gain: float = 1.0
            self._channel_gains: dict[int, float] = {}
            # Multiplicative step per wheel notch (120 eighths of a degree):
            # plain wheel scales the vertical gain, Shift+wheel scales the shared
            # timescale. >1 so wheel-up magnifies / lengthens; the inverse is
            # applied for wheel-down.
            self._wheel_gain_step: float = 1.2
            self._wheel_time_step: float = 1.2

            # Operator device-control state. Running synapsectl from this
            # operator-launched GUI is permitted under the AGENTS.md Synapse CLI
            # Execution Boundary scope (a human launches and watches it, the exact
            # command is shown, and the path is configurable); it never runs from
            # a test or an agent path. self._synapsectl_process is the single
            # in-flight child, or None when idle.
            self._synapsectl_process: "QProcess | None" = None
            self._device_started = False

            # Persisted field defaults live in a per-user INI (QSettings), mirroring
            # the calibration GUI. Device fields, the grid layout, and the scale/
            # timescale/gains carry over between launches; absent keys keep the
            # seeded defaults so a first run is unchanged. A field named in
            # `explicit_settings` was set on the command line this launch, so its
            # CLI value wins over any stored value. INI format keeps it readable.
            # settings_path isolates the INI for tests; production uses the
            # per-user NML/WaveformViewer scope.
            if settings_path is not None:
                self._settings = QSettings(str(settings_path), QSettings.IniFormat)
            else:
                self._settings = QSettings(QSettings.IniFormat, QSettings.UserScope,
                                           "NML", "WaveformViewer")
            self._explicit = set(explicit_settings or ())
            # A stored channel spec is applied once the stream's channel count is
            # known (parse_channel_spec needs it); consumed in _refresh.
            self._pending_channel_spec = False

            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.addWidget(self._build_device_panel(
                QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QProcess))
            source_strip = self._build_source_strip(QGroupBox, QVBoxLayout, QLabel, QTabBar)
            if source_strip is not None:
                layout.addWidget(source_strip)
            layout.addLayout(self._build_controls(QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox))
            layout.addWidget(self._build_scale_panel(
                QGroupBox, QHBoxLayout, QLabel, QLineEdit, QDoubleSpinBox, QPushButton))
            self.sync_toggle = QCheckBox("Sync edges: bottom ▲ GPIO 0 rise (cyan), fall (blue); top ▼ GPIO 1 rise (orange), fall (magenta)")
            self.sync_toggle.toggled.connect(lambda _: self._refresh())
            layout.addWidget(self.sync_toggle)
            self.status = QLabel("Connecting…")
            layout.addWidget(self.status)
            self.plot_widget = pg.GraphicsLayoutWidget()
            layout.addWidget(self.plot_widget)
            # Intercept wheel events over the plots to drive the fixed scale
            # (plain = vertical gain, Shift = horizontal timescale). The plots'
            # own mouse handling is disabled, so the wheel is free for this.
            self.plot_widget.viewport().installEventFilter(self)
            self._pg = pg

            self.reader.start()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._refresh)
            self.timer.start(50)

            # Restore persisted device fields, layout and scale. Done after the
            # timer exists because _load_settings applies the scale (which calls
            # _refresh). A restored channel spec is applied on the first frame.
            self._load_settings()

        def _build_device_panel(self, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QFileDialog, QProcess):
            """Operator device controls: start/stop the device App and fetch info.

            Mirrors the calibration GUI's synapsectl panel. The exact command is
            shown before it runs (Copy start line), the synapsectl path is
            configurable (supporting e.g. 'wsl synapsectl'), and every button is
            operator-driven. Permitted under the AGENTS.md CLI boundary scope for
            an operator-run GUI; never invoked from tests or an agent path.
            """
            self._QFileDialog = QFileDialog
            self._QProcess = QProcess
            box = QGroupBox("Device (operator runs synapsectl; shown before it runs)")
            bar = QHBoxLayout(box)

            bar.addWidget(QLabel("Device URI"))
            self.device_uri_edit = QLineEdit(device_ip)
            self.device_uri_edit.setToolTip("synapsectl -u <this> ...")
            bar.addWidget(self.device_uri_edit)

            bar.addWidget(QLabel("Config"))
            self.device_config_edit = QLineEdit(device_config)
            self.device_config_edit.setPlaceholderText(
                "device-config.json for 'start' (empty = restart already-configured device)")
            self.device_config_edit.setToolTip(
                "Config JSON uploaded+started by 'Run: start device'. Empty starts the "
                "device without reconfiguring it.")
            bar.addWidget(self.device_config_edit, 1)
            self.device_config_browse = QPushButton("…")
            self.device_config_browse.setFixedWidth(28)
            self.device_config_browse.clicked.connect(self._browse_device_config)
            bar.addWidget(self.device_config_browse)

            bar.addWidget(QLabel("synapsectl"))
            self.synapsectl_edit = QLineEdit(synapsectl_command)
            self.synapsectl_edit.setToolTip(
                "Command for the synapsectl buttons. Default resolves from PATH/venv; "
                "use 'wsl synapsectl' only if your install lives elsewhere.")
            bar.addWidget(self.synapsectl_edit)

            self.copy_start_button = QPushButton("Copy start line")
            self.copy_start_button.clicked.connect(self._copy_start_line)
            bar.addWidget(self.copy_start_button)
            self.run_start_button = QPushButton("Run: start device")
            self.run_start_button.clicked.connect(self._run_start_device)
            bar.addWidget(self.run_start_button)
            self.run_info_button = QPushButton("Run: fetch info")
            self.run_info_button.clicked.connect(self._run_fetch_info)
            bar.addWidget(self.run_info_button)

            self.device_status_label = QLabel("idle")
            bar.addWidget(self.device_status_label)
            return box

        def _build_source_strip(self, QGroupBox, QVBoxLayout, QLabel, QTabBar):
            """Per-source tab strip driven by the device config's kBroadbandSource
            nodes. Each tab names one source; selecting it shows that source's
            expected channel metadata and its config sample rate, against which the
            observed broadband_out rate is compared. Returns None when the config
            declares no sources (single implicit source; no strip shown).

            All tabs currently read the one broadband_out tap; per-source taps are
            a future App change, so the strip documents expectations and drives the
            expected-rate comparison rather than switching live streams.
            """
            if not source_layouts:
                return None
            box = QGroupBox("Sources (from device config; all read broadband_out for now)")
            col = QVBoxLayout(box)
            self.source_tabs = QTabBar()
            for lay in source_layouts:
                label = f"device {lay.node_id}"
                if lay.peripheral_id is not None:
                    label += f" (periph {lay.peripheral_id})"
                self.source_tabs.addTab(label)
            self.source_tabs.currentChanged.connect(lambda _=0: self._on_source_changed())
            col.addWidget(self.source_tabs)
            self.source_meta_label = QLabel("")
            self.source_meta_label.setWordWrap(True)
            col.addWidget(self.source_meta_label)
            self._update_source_meta_label()
            return box

        def _selected_layout(self):
            """The BroadbandLayout for the active source tab, or None if no config
            sources were parsed."""
            if not source_layouts:
                return None
            index = self.source_tabs.currentIndex() if hasattr(self, "source_tabs") else 0
            if index < 0 or index >= len(source_layouts):
                return None
            return source_layouts[index]

        def _expected_rate_hz(self):
            """Expected broadband_out rate for the active source (source rate /
            decimation factor), or None. This matches the decimated tap, so the
            observed-rate check does not falsely flag decimation as a mismatch."""
            lay = self._selected_layout()
            if lay is None:
                return None
            return expected_broadband_rate.get(lay.node_id)

        def _update_source_meta_label(self):
            lay = self._selected_layout()
            if lay is None:
                return
            types = ", ".join(sorted(set(lay.channel_types))) or "unknown"
            src = f"{lay.sample_rate_hz:g} Hz" if lay.sample_rate_hz else "unknown"
            decimated = expected_broadband_rate.get(lay.node_id)
            tap_rate = f"{decimated:g} Hz" if decimated is not None else "unknown"
            self.source_meta_label.setText(
                f"node id {lay.node_id} | peripheral {lay.peripheral_id} | "
                f"{lay.n_ch} channels ({types}) | source {src} → broadband_out {tap_rate} "
                "(decimated, shared tap)")

        def _on_source_changed(self):
            self._update_source_meta_label()
            self._refresh()

        def _synapsectl_argv(self, *tail):
            """Build the synapsectl argv from the configurable command field.

            The field may carry more than one token (e.g. 'wsl synapsectl') so a
            Windows GUI can reach a WSL install; each token becomes an argv
            element. Operator-machine only, per the AGENTS.md boundary scope.
            """
            base = [t for t in self.synapsectl_edit.text().split() if t] or ["synapsectl"]
            return [*base, "-u", self.device_uri_edit.text().strip(), *tail]

        def _synapsectl_busy(self):
            return self._synapsectl_process is not None

        def _refresh_device_buttons(self):
            idle = not self._synapsectl_busy()
            for button in (self.run_start_button, self.run_info_button,
                           self.copy_start_button, self.device_config_browse):
                button.setEnabled(idle)

        def _copy_start_line(self):
            config = self.device_config_edit.text().strip()
            argv = self._synapsectl_argv("start", *([config] if config else []))
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(" ".join(argv))
            self.device_status_label.setText("copied start line — run it yourself if you prefer")

        def _browse_device_config(self):
            start = self.device_config_edit.text().strip() or str(Path.cwd())
            path, _ = self._QFileDialog.getOpenFileName(
                self, "Select device-config.json", start, "JSON (*.json);;All files (*)")
            if path:
                self.device_config_edit.setText(path)

        def _run_synapsectl(self, tail, capture_to, label):
            """Spawn one operator synapsectl child. QProcess signals fire on the
            Qt thread, so the finished/error slots update widgets directly."""
            if self._synapsectl_busy():
                return
            argv = self._synapsectl_argv(*tail)
            self.device_status_label.setText(f"running: {' '.join(argv)}")
            process = self._QProcess(self)
            process.setProgram(argv[0])
            process.setArguments(argv[1:])
            process.setProcessChannelMode(self._QProcess.MergedChannels)
            buffer: list[str] = []
            process.readyReadStandardOutput.connect(
                lambda p=process, b=buffer: b.append(
                    bytes(p.readAllStandardOutput()).decode("utf-8", "replace")))
            process.errorOccurred.connect(
                lambda err, l=label: self._on_synapsectl_error(l, err))
            process.finished.connect(
                lambda code, status, b=buffer, c=capture_to, l=label:
                    self._on_synapsectl_done(l, code, "".join(b), c))
            self._synapsectl_process = process
            self._refresh_device_buttons()
            process.start()

        def _run_start_device(self):
            # After a successful start this button offers stop, sending
            # `synapsectl -u <uri> stop` (device-level stop, the analogue of the
            # argumentless start form).
            if self._device_started:
                self._run_synapsectl(["stop"], None, "stop")
            else:
                config = self.device_config_edit.text().strip()
                self._run_synapsectl(["start", *([config] if config else [])], None, "start")

        def _run_fetch_info(self):
            self._run_synapsectl(["info"], None, "info")

        def _on_synapsectl_error(self, label, err):
            self._synapsectl_process = None
            self.device_status_label.setText(
                f"[{label}] could not run (check the synapsectl field or Copy + run it yourself)")
            self._refresh_device_buttons()

        def _on_synapsectl_done(self, label, code, output, capture):
            self._synapsectl_process = None
            tail = output.strip().splitlines()[-1] if output.strip() else ""
            if label == "start" and code == 0:
                self._device_started = True
                self.run_start_button.setText("Run: stop device")
                self.device_status_label.setText("device start succeeded — this button now stops it")
            elif label == "stop" and code == 0:
                self._device_started = False
                self.run_start_button.setText("Run: start device")
                self.device_status_label.setText("device stop succeeded")
            elif label == "info":
                if code == 0:
                    running = _info_shows_app_running(output)
                    self.device_status_label.setText(
                        f"info: App scifi2-hub-manager Running={running}")
                else:
                    self.device_status_label.setText(f"info exited code {code}: {tail}")
            else:
                self.device_status_label.setText(f"[{label}] exited code {code}: {tail}")
            self._refresh_device_buttons()

        def _build_controls(self, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox):
            bar = QHBoxLayout()
            bar.addWidget(QLabel("Columns"))
            self.columns_spin = QSpinBox()
            self.columns_spin.setRange(1, max_channels)
            self.columns_spin.setValue(max(1, columns))
            bar.addWidget(self.columns_spin)

            bar.addWidget(QLabel("Channels"))
            self.channel_edit = QLineEdit()
            self.channel_edit.setPlaceholderText("e.g. 0-31, or 0,4,8,12 (empty = all)")
            self.channel_edit.setText(channel_spec)
            self.channel_edit.returnPressed.connect(self._apply_layout)
            bar.addWidget(self.channel_edit, 1)

            self.apply_button = QPushButton("Apply")
            self.apply_button.clicked.connect(self._apply_layout)
            bar.addWidget(self.apply_button)
            self.reset_button = QPushButton("All")
            self.reset_button.clicked.connect(self._reset_layout)
            bar.addWidget(self.reset_button)

            # Reconnect tears down the current reader and starts a fresh tap
            # subscription, recovering from a dropped stream or a connect error
            # without restarting the process.
            self.reconnect_button = QPushButton("Reconnect")
            self.reconnect_button.clicked.connect(self._reconnect)
            bar.addWidget(self.reconnect_button)

            # Quick presets for common arrangements over up to 32 channels.
            for text, spec, cols in (("4×8", "0-31", 8), ("8×4", "0-31", 4), ("1 col", "", 1)):
                button = QPushButton(text)
                button.clicked.connect(lambda _=False, s=spec, c=cols: self._apply_preset(s, c))
                bar.addWidget(button)

            self.layout_note = QLabel("")
            bar.addWidget(self.layout_note)
            return bar

        def _build_scale_panel(self, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QDoubleSpinBox, QPushButton):
            """Sub-panel for the fixed vertical scale, gains, and timescale."""
            box = QGroupBox("Scale")
            bar = QHBoxLayout(box)

            bar.addWidget(QLabel("Full-scale ±"))
            self.full_scale_spin = QDoubleSpinBox()
            self.full_scale_spin.setDecimals(3)
            self.full_scale_spin.setRange(1e-6, 1e12)
            self.full_scale_spin.setValue(self._full_scale)
            self.full_scale_spin.setToolTip("Base y half-amplitude at gain 1")
            bar.addWidget(self.full_scale_spin)

            bar.addWidget(QLabel("Gain ×"))
            self.gain_spin = QDoubleSpinBox()
            self.gain_spin.setDecimals(3)
            self.gain_spin.setRange(1e-6, 1e9)
            self.gain_spin.setValue(self._global_gain)
            self.gain_spin.setToolTip("Global vertical gain applied to every axis")
            bar.addWidget(self.gain_spin)

            bar.addWidget(QLabel("Per-ch gain"))
            self.gain_edit = QLineEdit()
            self.gain_edit.setPlaceholderText("e.g. 0:2, 4:0.5 (overrides global)")
            self.gain_edit.returnPressed.connect(self._apply_scale)
            bar.addWidget(self.gain_edit, 1)

            bar.addWidget(QLabel("Timescale (s)"))
            self.timescale_spin = QDoubleSpinBox()
            self.timescale_spin.setDecimals(4)
            # The shown window cannot exceed the buffered history.
            self.timescale_spin.setRange(1e-4, float(duration_s))
            self.timescale_spin.setValue(min(self._timescale_s, float(duration_s)))
            self.timescale_spin.setToolTip("Shared x-window duration across all axes")
            bar.addWidget(self.timescale_spin)

            apply_scale = QPushButton("Apply scale")
            apply_scale.clicked.connect(self._apply_scale)
            bar.addWidget(apply_scale)

            self.scale_note = QLabel("")
            bar.addWidget(self.scale_note)
            return box

        def _apply_scale(self) -> None:
            """Validate and commit the fixed-scale / gain / timescale settings."""
            try:
                gains = parse_gain_spec(self.gain_edit.text())
            except ValueError as exc:
                self.scale_note.setText(f"invalid: {exc}")
                return
            self._full_scale = float(self.full_scale_spin.value())
            self._global_gain = float(self.gain_spin.value())
            self._channel_gains = gains
            self._timescale_s = float(self.timescale_spin.value())
            self.scale_note.setText("")
            # Re-apply fixed ranges to existing plots and redraw immediately.
            self._apply_axis_ranges()
            self._refresh()

        # -- persistence (QSettings) -----------------------------------------
        def _persisted_line_edits(self):
            """settings-key -> QLineEdit map. A key in self._explicit is CLI-set
            this launch, so its seeded value wins over the stored one."""
            return {
                "device_uri": self.device_uri_edit,
                "device_config": self.device_config_edit,
                "synapsectl": self.synapsectl_edit,
                "channels": self.channel_edit,
                "per_channel_gain": self.gain_edit,
            }

        def _load_settings(self) -> None:
            s = self._settings
            for key, widget in self._persisted_line_edits().items():
                if key in self._explicit:
                    continue  # an explicit CLI value overrides the stored one
                stored = s.value(f"fields/{key}")
                if isinstance(stored, str) and stored:
                    widget.setText(stored)
            # Numeric layout/scale state. QSettings stores strings; coerce and
            # clamp to each widget's range, ignoring anything unparseable.
            def _num(key, caster):
                raw = s.value(f"fields/{key}")
                if raw is None:
                    return None
                try:
                    return caster(raw)
                except (TypeError, ValueError):
                    return None
            columns = _num("columns", int)
            if columns is not None and "columns" not in self._explicit:
                self.columns_spin.setValue(max(1, min(columns, self.columns_spin.maximum())))
            full_scale = _num("full_scale", float)
            if full_scale is not None and "full_scale" not in self._explicit:
                self.full_scale_spin.setValue(full_scale)
            gain = _num("global_gain", float)
            if gain is not None:
                self.gain_spin.setValue(gain)
            timescale = _num("timescale", float)
            if timescale is not None and "timescale" not in self._explicit:
                # Clamp into the buffered-history range the spin enforces.
                self.timescale_spin.setValue(
                    max(self.timescale_spin.minimum(), min(timescale, self.timescale_spin.maximum())))
            sync = s.value("fields/sync_edges")
            if isinstance(sync, str):
                self.sync_toggle.setChecked(sync.lower() in ("1", "true", "yes"))
            # Push the restored scale/gain/timescale into internal state and the
            # active column count so the first auto-built layout honors them
            # (frame-independent). _apply_scale re-parses the per-channel gains.
            self._active_columns = self.columns_spin.value()
            self._apply_scale()
            # A restored channel spec needs the channel count to resolve; apply it
            # once the first frame arrives (see _refresh).
            self._pending_channel_spec = bool(self.channel_edit.text().strip())

        def _save_settings(self) -> None:
            s = self._settings
            for key, widget in self._persisted_line_edits().items():
                s.setValue(f"fields/{key}", widget.text())
            s.setValue("fields/columns", str(self.columns_spin.value()))
            s.setValue("fields/full_scale", str(self.full_scale_spin.value()))
            s.setValue("fields/global_gain", str(self.gain_spin.value()))
            s.setValue("fields/timescale", str(self.timescale_spin.value()))
            s.setValue("fields/sync_edges", "true" if self.sync_toggle.isChecked() else "false")
            s.sync()

        def _gain_for(self, channel_id: int) -> float:
            """Effective vertical gain for one channel (override else global)."""
            return self._channel_gains.get(channel_id, self._global_gain)

        def _apply_axis_ranges(self) -> None:
            """Set every plot's fixed y-window and the shared x-window.

            Plots never auto-range: the y-window is
            ``[-full_scale/gain, +full_scale/gain]`` (so a larger per-channel
            gain magnifies the trace within a fixed pixel height), and the
            x-window is the most recent ``timescale_s`` seconds, shared across
            all axes.
            """
            for index, placement in enumerate(self._placements):
                plot = self._plots.get(index)
                if plot is None:
                    continue
                gain = self._gain_for(placement.channel_id)
                half = self._full_scale / gain if gain else self._full_scale
                plot.setYRange(-half, half, padding=0.0)
                plot.setXRange(-self._timescale_s, 0.0, padding=0.0)

        def eventFilter(self, obj, event):
            # Wheel over the plot area drives the fixed scale: plain wheel = the
            # vertical gain, Shift+wheel = the shared timescale. Consuming the
            # event keeps the fixed y/x windows authoritative.
            from PySide6.QtCore import QEvent, Qt

            if obj is self.plot_widget.viewport() and event.type() == QEvent.Wheel:
                delta = event.angleDelta().y()
                if delta != 0:
                    notches = delta / 120.0  # one wheel detent = 120 units
                    if event.modifiers() & Qt.ShiftModifier:
                        self._zoom_timescale(notches)
                    else:
                        self._zoom_gain(notches)
                    return True  # handled; do not pass to the plots
            return super().eventFilter(obj, event)

        def _zoom_gain(self, notches: float) -> None:
            """Scale the global vertical gain by the wheel; wheel-up magnifies.

            Adjusts the same global gain the Scale panel edits (per-channel
            overrides are unaffected), syncs the panel spin box, and re-applies
            the fixed y-windows.
            """
            factor = self._wheel_gain_step ** notches
            new_gain = self._clamp_to_spin(self.gain_spin, self._global_gain * factor)
            # Adopt the spin box's rounded value as authoritative so the wheel
            # state and the panel value never drift apart by the display step.
            self._set_spin_silently(self.gain_spin, new_gain)
            new_gain = self.gain_spin.value()
            if new_gain == self._global_gain:
                return
            self._global_gain = new_gain
            self._apply_axis_ranges()
            self._refresh()

        def _zoom_timescale(self, notches: float) -> None:
            """Scale the shared timescale by the wheel; wheel-up lengthens it.

            Clamped to the buffered history; syncs the panel spin box and
            re-applies the shared x-window to every axis.
            """
            factor = self._wheel_time_step ** notches
            target = self._clamp_to_spin(self.timescale_spin, self._timescale_s * factor)
            # Adopt the spin box's rounded value as authoritative so the wheel
            # state and the panel value stay exactly consistent.
            self._set_spin_silently(self.timescale_spin, target)
            new_ts = self.timescale_spin.value()
            if new_ts == self._timescale_s:
                return
            self._timescale_s = new_ts
            self._apply_axis_ranges()
            self._refresh()

        @staticmethod
        def _clamp_to_spin(spin, value: float) -> float:
            """Clamp ``value`` into a spin box's [min, max] range."""
            return max(spin.minimum(), min(spin.maximum(), value))

        @staticmethod
        def _set_spin_silently(spin, value: float) -> None:
            """Set a spin box value without emitting its change signals."""
            blocked = spin.blockSignals(True)
            try:
                spin.setValue(value)
            finally:
                spin.blockSignals(blocked)

        def _apply_preset(self, spec: str, cols: int) -> None:
            self.channel_edit.setText(spec)
            self.columns_spin.setValue(min(max(1, cols), max_channels))
            self._apply_layout()

        def _reset_layout(self) -> None:
            self.channel_edit.setText("")
            self.columns_spin.setValue(1)
            self._apply_layout()

        def _reconnect(self) -> None:
            """Stop the current reader and start a fresh tap subscription.

            Readers are single-use, so a fresh instance is built from the
            factory. The buffer and its counters/history are preserved. This
            also re-arms the refresh timer if it had been stopped by a prior
            reader error.
            """
            self.reconnect_button.setEnabled(False)
            try:
                self.status.setText("Reconnecting…")
                self.reader.stop()
                self.buffer.reset_sync_continuity()
                self.reader = self._reader_factory(device_ip, self.buffer)
                self.reader.start()
                # A prior reader error stops the timer permanently; restart it so
                # the new reader's frames are drawn again.
                if not self.timer.isActive():
                    self.timer.start(50)
                self._refresh()
            finally:
                self.reconnect_button.setEnabled(True)

        def _apply_layout(self) -> None:
            """Validate the requested layout and force a plot rebuild."""
            available = self.buffer.available_channels
            if available <= 0:
                self.layout_note.setText("waiting for the first frame to learn channel count")
                return
            try:
                channels = parse_channel_spec(self.channel_edit.text(), available)
            except ValueError as exc:
                self.layout_note.setText(f"invalid: {exc}")
                return
            self._active_channels = channels
            self._active_columns = self.columns_spin.value()
            self.layout_note.setText("")
            self._rebuild_plots()
            # Redraw current data and refresh the status line immediately rather
            # than waiting for the next timer tick.
            self._refresh()

        def _current_channels(self, available: int) -> list[int]:
            if self._active_channels:
                # Drop any ids that exceed a now-smaller stream; keep order.
                return [c for c in self._active_channels if c < available]
            return list(range(available))

        def _rebuild_plots(self) -> None:
            """Construct the PlotItem/curve objects for the active layout once.

            This is the only place Qt graphics objects are created or destroyed.
            It runs on a layout change or when the stream's channel count
            changes — never on an ordinary frame tick, where :meth:`_refresh`
            only pushes new data into the existing curves via ``setData``.
            """
            available = self.buffer.available_channels
            self.plot_widget.clear()
            self._curves.clear()
            self._sync_curves.clear()
            self._plots.clear()
            # Force the time axis to be recomputed for the rebuilt curves.
            self._time_axis = None
            self._time_axis_key = None
            channels = self._current_channels(available)
            self._placements = grid_placements(channels, self._active_columns)
            self._resolved_available = available
            last_row = max((p.row for p in self._placements), default=-1)
            for index, placement in enumerate(self._placements):
                plot = self.plot_widget.addPlot(row=placement.row, col=placement.col)
                plot.setTitle(f"ch {placement.channel_id}")
                plot.showGrid(x=False, y=True, alpha=0.2)
                # Fixed scale: disable pyqtgraph auto-ranging so the y-window is
                # the explicit fixed full-scale (adjusted per-channel by gain)
                # and does not chase the data. The user cannot drag either axis
                # out of the fixed window.
                plot.setMouseEnabled(x=False, y=False)
                plot.disableAutoRange()
                if placement.row < last_row:
                    plot.getAxis("bottom").setStyle(showValues=False)
                else:
                    plot.setLabel("bottom", "nominal sample time", units="s")
                self._plots[index] = plot
                self._curves[index] = plot.plot(pen=self._pg.mkPen(width=1))
                self._sync_curves[index] = [
                    plot.plot(pen=None, symbol="t1" if edge_index < 2 else "t",
                              symbolSize=9, symbolPen=color, symbolBrush=color)
                    for edge_index, color in enumerate(("#00d5ff", "#4677ff", "#ffb000", "#ff55cc"))
                ]
                for curve in self._sync_curves[index]:
                    curve.setZValue(10)
            # Apply the fixed y-window and shared x-window to the fresh plots.
            self._apply_axis_ranges()
            # A layout change alters the grid's row/column count, so the plots
            # must reflow to fill the viewport exactly as they do on a manual
            # window resize. Fire the same reflow pass here so the arrangement
            # fits the screen immediately instead of only after the user drags
            # the window edge.
            self._relayout_plots()

        def _relayout_plots(self) -> None:
            """Reflow the plot grid to the current viewport size.

            Shared by the resize event and every layout change so both take the
            identical geometry path: invalidate the pyqtgraph GraphicsLayout and
            re-apply the view rect, which recomputes each plot's size for the
            active row/column count. Safe to call before any plot exists.
            """
            # resizeEvent can fire during construction before plot_widget is
            # assigned, so guard the attribute as well as the layout item.
            plot_widget = getattr(self, "plot_widget", None)
            layout = getattr(plot_widget, "ci", None)  # the central GraphicsLayout
            if layout is None:
                return
            layout_obj = layout.layout  # the underlying QGraphicsGridLayout
            if layout_obj is not None:
                layout_obj.invalidate()
            # Re-apply the current viewport rect so the grid recomputes sizes,
            # matching what a manual resize triggers internally.
            self.plot_widget.setSceneRect(self.plot_widget.viewRect())

        def _refresh(self) -> None:
            if self.reader.error:
                self.status.setText(self.reader.error)
                self.timer.stop()
                return
            snapshot = self.buffer.snapshot()
            available = len(snapshot.channel_ids)

            # Apply a restored channel spec the first time the channel count is
            # known (parse_channel_spec needs it). _apply_layout resolves the spec,
            # sets _active_columns from the spin, and rebuilds the plots.
            if available and self._pending_channel_spec:
                self._pending_channel_spec = False
                self._apply_layout()

            # Build (or rebuild) plots once the channel count is known, and again
            # if the stream's channel count changed under a running layout.
            if available and (not self._placements or available != self._resolved_available):
                self._rebuild_plots()

            n = snapshot.samples.shape[1] if snapshot.samples.ndim == 2 else 0
            if n and snapshot.sample_rate_hz and self._placements:
                t = snapshot.nominal_times
                for index, placement in enumerate(self._placements):
                    if placement.channel_id < snapshot.samples.shape[0]:
                        self._curves[index].setData(t, snapshot.samples[placement.channel_id],
                                                    connect=snapshot.connections)
                    half = self._full_scale / self._gain_for(placement.channel_id)
                    for edge_index, curve in enumerate(self._sync_curves[index]):
                        visible = self.sync_toggle.isChecked()
                        curve.setVisible(visible)
                        if visible:
                            positions = t[snapshot.sync_edges[edge_index] & (t >= -self._timescale_s) & (t <= 0)]
                            # Inset pixel-sized triangles to avoid clipping at the
                            # plot boundary. Both channels keep exact edge times.
                            height = max(1, self._plots[index].getViewBox().height())
                            inset = min(half, 2 * half * 7 / height)
                            y = -half + inset if edge_index < 2 else half - inset
                            curve.setData(positions, np.full(len(positions), y))

            state = "connected" if self.reader.connected else "waiting for frames"
            shown = len(self._placements)
            gain_txt = f"{self._global_gain:g}×"
            if self._channel_gains:
                gain_txt += f" (+{len(self._channel_gains)} per-ch)"
            # Rate reporting: the wire-declared sample_rate_hz from the frame, an
            # observed rate estimated from the device timestamps, and (when a
            # config was loaded) the selected source's expected rate for a check.
            observed = observed_sample_rate_hz(snapshot.timestamps)
            rate_txt = f"rate={snapshot.sample_rate_hz} Hz (wire)"
            if observed is not None:
                rate_txt += f", ~{observed:.1f} Hz observed"
            expected = self._expected_rate_hz()
            if expected is not None:
                rate_txt += f", expect {expected:g} Hz"
                # Flag a >5% observed-vs-expected mismatch as a quick sanity signal.
                if observed is not None and expected > 0 and abs(observed - expected) / expected > 0.05:
                    rate_txt += " ⚠"
            self.status.setText(
                f"{tap_name} @ {device_ip}: {state} | frames={snapshot.frames} "
                f"missing={snapshot.missing_sequences} parse_errors={snapshot.parse_errors} "
                f"| {rate_txt} | showing {shown}/{available} ch "
                f"in {self._active_columns} col(s) | ±{self._full_scale:g} gain {gain_txt} "
                f"t={self._timescale_s:g}s | nominal sample time | tap read-only "
                "(device start/stop via the operator synapsectl panel)"
            )

        def resizeEvent(self, event):
            # The window resize callback. pyqtgraph already reflows on resize;
            # routing it through the same _relayout_plots the layout-change path
            # uses keeps a single, shared fit-to-screen code path.
            super().resizeEvent(event)
            self._relayout_plots()

        def closeEvent(self, event):
            self._save_settings()
            self.timer.stop()
            self.reader.stop()
            process = self._synapsectl_process
            if process is not None and process.state() != self._QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1000)
            event.accept()

    return WaveformWindow()


def run_waveform(
    device_ip: str,
    *,
    duration_s: float = 2.0,
    expected_sample_rate_hz: int = 20_000,
    max_channels: int = 32,
    columns: int = 1,
    channel_spec: str = "",
    tap_name: str = "broadband_out",
    y_full_scale: float = 1000.0,
    timescale_s: float = 0.0,
    device_config: str = "",
    synapsectl_command: str = "synapsectl",
    explicit_settings=None,
) -> None:
    from PySide6.QtWidgets import QApplication

    from .appicon import apply_app_icon, setup_taskbar_identity

    app = QApplication.instance() or QApplication([])
    # Taskbar identity per platform (Windows AppUserModelID / Linux+WSLg
    # .desktop entry), before any window is shown.
    setup_taskbar_identity("waveform", "Broadband Waveforms")
    apply_app_icon(app)  # taskbar icon
    window = create_waveform_window(
        device_ip,
        duration_s=duration_s,
        expected_sample_rate_hz=expected_sample_rate_hz,
        max_channels=max_channels,
        columns=columns,
        channel_spec=channel_spec,
        tap_name=tap_name,
        y_full_scale=y_full_scale,
        timescale_s=timescale_s,
        device_config=device_config,
        synapsectl_command=synapsectl_command,
        explicit_settings=explicit_settings,
    )
    window.show()
    app.exec()
