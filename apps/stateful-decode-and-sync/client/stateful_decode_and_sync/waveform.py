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
import threading
import time
from dataclasses import dataclass

import numpy as np
from synapse.api.datatype_pb2 import BroadbandFrame

# Pin pyqtgraph to the same Qt binding the control GUI uses. pyqtgraph would
# otherwise pick whichever binding it finds first (PyQt5 is preferred by
# default), producing a widget the PySide6 layout rejects. This is a hard
# assignment, not setdefault, because the client is committed to PySide6; it
# runs at module import time, before the lazy pyqtgraph import below.
os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"


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
        self._filled = 0
        self._write = 0
        self.frames = 0
        self.parse_errors = 0
        self.missing_sequences = 0
        self.sequence_last: int | None = None
        self.timestamp_last: int | None = None
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def available_channels(self) -> int:
        """Number of channels retained (0 until the first frame arrives)."""
        with self._lock:
            return len(self._channel_ids)

    def add_raw(self, raw: bytes) -> bool:
        """Parse one wire payload and append it; return False if malformed."""
        frame = BroadbandFrame()
        try:
            frame.ParseFromString(raw)
        except Exception:
            with self._lock:
                self.parse_errors += 1
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
            self._ring[:, self._write] = column
            self._write = (self._write + 1) % self._capacity
            self._filled = min(self._filled + 1, self._capacity)

            sequence = int(frame.sequence_number)
            if self.sequence_last is not None and sequence > self.sequence_last + 1:
                self.missing_sequences += sequence - self.sequence_last - 1
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
            return WaveformSnapshot(
                channel_ids=self._channel_ids,
                samples=samples,
                sample_rate_hz=self._sample_rate_hz,
                frames=self.frames,
                missing_sequences=self.missing_sequences,
                parse_errors=self.parse_errors,
                sequence_last=self.sequence_last,
                timestamp_last=self.timestamp_last,
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
    reader_factory=None,
):
    """Build the live waveform window. Qt/pyqtgraph imports stay local.

    Layout is interactive: a controls bar sets the number of grid columns and a
    channel spec (see :func:`parse_channel_spec`) selecting which channels are
    shown and in what order. ``columns`` and ``channel_spec`` seed the initial
    layout; ``max_channels`` bounds how many channels the buffer retains.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import (
        QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QSpinBox,
        QVBoxLayout, QWidget,
    )

    try:
        import pyqtgraph as pg
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
        raise ModuleNotFoundError(
            "pyqtgraph is required for the waveform viewer. Install the client's "
            "'waveform' extra: pip install -e 'apps/stateful-decode-and-sync/client[waveform]'"
        ) from exc

    class WaveformWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f"Broadband waveforms — {device_ip} / {tap_name}")
            self.resize(1100, 720)
            self.buffer = WaveformBuffer(
                duration_s=duration_s,
                expected_sample_rate_hz=expected_sample_rate_hz,
                max_channels=max_channels,
            )
            factory = reader_factory or (lambda ip, buf: BroadbandStreamReader(ip, buf, tap_name=tap_name))
            self.reader = factory(device_ip, self.buffer)

            # Active layout: an ordered channel list and a column count. Empty
            # channel list means "all available, natural order" until frames
            # arrive and a concrete list is resolved.
            self._active_channels: list[int] = []
            self._active_columns: int = max(1, columns)
            self._curves: dict[int, object] = {}  # position index -> curve
            self._placements: list[GridPlacement] = []
            self._resolved_available = 0

            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            layout.addLayout(self._build_controls(QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox))
            self.status = QLabel("Connecting…")
            layout.addWidget(self.status)
            self.plot_widget = pg.GraphicsLayoutWidget()
            layout.addWidget(self.plot_widget)
            self._pg = pg

            self.reader.start()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._refresh)
            self.timer.start(50)

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

            # Quick presets for common arrangements over up to 32 channels.
            for text, spec, cols in (("4×8", "0-31", 8), ("8×4", "0-31", 4), ("1 col", "", 1)):
                button = QPushButton(text)
                button.clicked.connect(lambda _=False, s=spec, c=cols: self._apply_preset(s, c))
                bar.addWidget(button)

            self.layout_note = QLabel("")
            bar.addWidget(self.layout_note)
            return bar

        def _apply_preset(self, spec: str, cols: int) -> None:
            self.channel_edit.setText(spec)
            self.columns_spin.setValue(min(max(1, cols), max_channels))
            self._apply_layout()

        def _reset_layout(self) -> None:
            self.channel_edit.setText("")
            self.columns_spin.setValue(1)
            self._apply_layout()

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
            available = self.buffer.available_channels
            self.plot_widget.clear()
            self._curves.clear()
            channels = self._current_channels(available)
            self._placements = grid_placements(channels, self._active_columns)
            self._resolved_available = available
            last_row = max((p.row for p in self._placements), default=-1)
            for index, placement in enumerate(self._placements):
                plot = self.plot_widget.addPlot(row=placement.row, col=placement.col)
                plot.setTitle(f"ch {placement.channel_id}")
                plot.showGrid(x=False, y=True, alpha=0.2)
                plot.setMouseEnabled(x=False, y=True)
                if placement.row < last_row:
                    plot.getAxis("bottom").setStyle(showValues=False)
                else:
                    plot.setLabel("bottom", "time", units="s")
                self._curves[index] = plot.plot(pen=self._pg.mkPen(width=1))

        def _refresh(self) -> None:
            if self.reader.error:
                self.status.setText(self.reader.error)
                self.timer.stop()
                return
            snapshot = self.buffer.snapshot()
            available = len(snapshot.channel_ids)

            # Build (or rebuild) plots once the channel count is known, and again
            # if the stream's channel count changed under a running layout.
            if available and (not self._placements or available != self._resolved_available):
                self._rebuild_plots()

            n = snapshot.samples.shape[1] if snapshot.samples.ndim == 2 else 0
            if n and snapshot.sample_rate_hz and self._placements:
                t = (np.arange(n) - n + 1) / snapshot.sample_rate_hz
                for index, placement in enumerate(self._placements):
                    if placement.channel_id < snapshot.samples.shape[0]:
                        self._curves[index].setData(t, snapshot.samples[placement.channel_id])

            state = "connected" if self.reader.connected else "waiting for frames"
            shown = len(self._placements)
            self.status.setText(
                f"{tap_name} @ {device_ip}: {state} | frames={snapshot.frames} "
                f"missing={snapshot.missing_sequences} parse_errors={snapshot.parse_errors} "
                f"rate={snapshot.sample_rate_hz} Hz | showing {shown}/{available} ch "
                f"in {self._active_columns} col(s) | read-only (no device commands)"
            )

        def closeEvent(self, event):
            self.timer.stop()
            self.reader.stop()
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
) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = create_waveform_window(
        device_ip,
        duration_s=duration_s,
        expected_sample_rate_hz=expected_sample_rate_hz,
        max_channels=max_channels,
        columns=columns,
        channel_spec=channel_spec,
        tap_name=tap_name,
    )
    window.show()
    app.exec()
