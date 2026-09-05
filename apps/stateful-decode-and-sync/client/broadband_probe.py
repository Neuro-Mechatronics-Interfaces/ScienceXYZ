#!/usr/bin/env python3
"""Read-only broadband_out frame counter and metadata probe.

The probe subscribes to a producer tap for a bounded duration.  It does not
send commands to the device, so it can be run while source mode and capture
state are controlled by another client.  Sequence gaps and timestamp
regressions are counted separately from protobuf parse failures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

from synapse.api.channel_pb2 import ChannelType
from synapse.api.datatype_pb2 import BroadbandFrame
from synapse.client.taps import Tap


@dataclass
class FrameStats:
    """Accumulates transport-independent observations from BroadbandFrames."""

    frames: int = 0
    parse_errors: int = 0
    missing_sequences: int = 0
    non_monotonic_sequences: int = 0
    timestamp_regressions: int = 0
    sequence_first: int | None = None
    sequence_last: int | None = None
    timestamp_first: int | None = None
    timestamp_last: int | None = None
    timestamp_delta_min_ns: int | None = None
    timestamp_delta_max_ns: int | None = None
    timestamp_delta_sum_ns: int = 0
    positive_timestamp_intervals: int = 0
    sample_rates_hz: set[int] = field(default_factory=set)
    channel_counts: set[int] = field(default_factory=set)
    channel_ranges: tuple[str, ...] = ()
    gpio: dict = field(default_factory=dict)
    gpio_previous: dict = field(default_factory=dict)
    gpio_last_edge: dict = field(default_factory=dict)
    previous_rate: int | None = None

    def add(self, frame: BroadbandFrame) -> None:
        sequence = int(frame.sequence_number)
        timestamp = int(frame.timestamp_ns)
        contiguous = (self.sequence_last is not None and sequence == self.sequence_last + 1
                      and self.timestamp_last is not None and timestamp > self.timestamp_last
                      and frame.sample_rate_hz == self.previous_rate)
        if not contiguous:
            self.gpio_previous.clear()
            self.gpio_last_edge.clear()
        current = {}
        offset = 0
        for group in frame.channel_ranges:
            ids = list(group.channel_ids) or list(range(group.count))
            if group.type == ChannelType.GPIO and len(ids) == group.count:
                for j, pin in enumerate(ids):
                    if offset + j < len(frame.frame_data):
                        current[pin] = (offset + j, int(frame.frame_data[offset + j]))
            offset += group.count
        for pin, (position, value) in current.items():
            g = self.gpio.setdefault(pin, {"positions": set(), "low_samples": 0, "high_samples": 0,
                "nonbinary_samples": 0, "value_min": value, "value_max": value,
                "rises": 0, "falls": 0, "value_changes": 0, "intervals": 0})
            g["positions"].add(position)
            g["high_samples" if value else "low_samples"] += 1
            g["nonbinary_samples"] += value not in (0, 1)
            g["value_min"], g["value_max"] = min(g["value_min"], value), max(g["value_max"], value)
            previous = self.gpio_previous.get(pin)
            if previous is None or previous[0] != position:
                self.gpio_last_edge.pop(pin, None)
                continue
            g["value_changes"] += previous[1] != value
            if bool(previous[1]) != bool(value):
                g["rises" if value else "falls"] += 1
                last = self.gpio_last_edge.get(pin)
                if last is not None:
                    g["intervals"] += 1
                    for name, delta in (("edge_delta_samples", sequence - last[0]),
                                        ("edge_delta_source_ns", timestamp - last[1])):
                        g[name + "_min"] = min(g.get(name + "_min", delta), delta)
                        g[name + "_max"] = max(g.get(name + "_max", delta), delta)
                        g[name + "_sum"] = g.get(name + "_sum", 0) + delta
                self.gpio_last_edge[pin] = (sequence, timestamp)
        self.gpio_previous = current
        for pin in list(self.gpio_last_edge):
            if pin not in current:
                self.gpio_last_edge.pop(pin)
        self.previous_rate = int(frame.sample_rate_hz)

        if self.frames == 0:
            self.sequence_first = sequence
            self.timestamp_first = timestamp
            self.channel_ranges = tuple(format_channel_range(r) for r in frame.channel_ranges)
        else:
            assert self.sequence_last is not None
            if sequence > self.sequence_last + 1:
                self.missing_sequences += sequence - self.sequence_last - 1
            elif sequence <= self.sequence_last:
                self.non_monotonic_sequences += 1

            assert self.timestamp_last is not None
            delta = timestamp - self.timestamp_last
            if delta <= 0:
                self.timestamp_regressions += 1
            else:
                self.timestamp_delta_sum_ns += delta
                self.positive_timestamp_intervals += 1
                if self.timestamp_delta_min_ns is None or delta < self.timestamp_delta_min_ns:
                    self.timestamp_delta_min_ns = delta
                if self.timestamp_delta_max_ns is None or delta > self.timestamp_delta_max_ns:
                    self.timestamp_delta_max_ns = delta

        self.frames += 1
        self.sequence_last = sequence
        self.timestamp_last = timestamp
        self.sample_rates_hz.add(int(frame.sample_rate_hz))
        self.channel_counts.add(len(frame.frame_data))

    @property
    def mean_timestamp_delta_ns(self) -> float | None:
        if self.positive_timestamp_intervals == 0:
            return None
        return self.timestamp_delta_sum_ns / self.positive_timestamp_intervals


def format_channel_range(channel_range) -> str:
    """Format the wire channel-range metadata without assuming channel layout."""

    try:
        kind = ChannelType.Name(channel_range.type)
    except ValueError:
        kind = f"UNKNOWN({channel_range.type})"
    ids = ",".join(str(value) for value in channel_range.channel_ids)
    suffix = f" ids=[{ids}]" if ids else ""
    return f"{kind}:{channel_range.count}{suffix}"


def parse_frame(raw: bytes, stats: FrameStats) -> bool:
    """Parse and record one wire payload; return False for malformed protobuf."""

    frame = BroadbandFrame()
    try:
        if not frame.ParseFromString(raw):
            stats.parse_errors += 1
            stats.gpio_previous.clear()
            stats.gpio_last_edge.clear()
            return False
    except Exception:
        stats.parse_errors += 1
        stats.gpio_previous.clear()
        stats.gpio_last_edge.clear()
        return False
    stats.add(frame)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Count and inspect BroadbandFrame messages from a producer tap"
    )
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="broadband_out",
                        help="producer tap to inspect")
    parser.add_argument("--duration", type=float, default=5.0,
                        help="seconds to listen (default: 5)")
    parser.add_argument("--max-frames", type=int, default=None,
                        help="stop after this many valid frames")
    return parser.parse_args()


def print_report(stats: FrameStats, elapsed_s: float) -> None:
    observed_rate = stats.frames / elapsed_s if elapsed_s > 0 else 0.0
    print(f"frames={stats.frames} elapsed_s={elapsed_s:.3f} observed_rate_hz={observed_rate:.1f}")
    print(
        "sequence="
        f"{stats.sequence_first if stats.sequence_first is not None else '-'}.."
        f"{stats.sequence_last if stats.sequence_last is not None else '-'} "
        f"missing={stats.missing_sequences} non_monotonic={stats.non_monotonic_sequences}"
    )
    mean_delta = stats.mean_timestamp_delta_ns
    mean_delta_text = f"{mean_delta:.1f}" if mean_delta is not None else "-"
    print(
        "timestamp_ns="
        f"{stats.timestamp_first if stats.timestamp_first is not None else '-'}.."
        f"{stats.timestamp_last if stats.timestamp_last is not None else '-'} "
        f"regressions={stats.timestamp_regressions} "
        f"delta_min={stats.timestamp_delta_min_ns if stats.timestamp_delta_min_ns is not None else '-'} "
        f"delta_mean={mean_delta_text} "
        f"delta_max={stats.timestamp_delta_max_ns if stats.timestamp_delta_max_ns is not None else '-'}"
    )
    print(f"sample_rate_hz={sorted(stats.sample_rates_hz)} channel_counts={sorted(stats.channel_counts)}")
    print(f"channel_ranges={list(stats.channel_ranges) if stats.channel_ranges else ['(empty: legacy electrode layout)']}")
    print(f"parse_errors={stats.parse_errors}")
    for pin, values in sorted(stats.gpio.items()):
        report = dict(values)
        report["positions"] = sorted(report["positions"])
        for name in ("edge_delta_samples", "edge_delta_source_ns"):
            total = report.pop(name + "_sum", 0)
            report[name + "_mean"] = total / report["intervals"] if report["intervals"] else None
        print(f"gpio_id={pin} " + json.dumps(report, sort_keys=True))
    if not stats.gpio:
        print("gpio: no GPIO-tagged values observed")


def main() -> int:
    args = parse_args()
    if args.duration <= 0:
        print("--duration must be positive", file=sys.stderr)
        return 2
    if args.max_frames is not None and args.max_frames <= 0:
        print("--max-frames must be positive", file=sys.stderr)
        return 2

    tap = Tap(args.device_ip)
    if not tap.connect(args.tap_name):
        print(f"Failed to connect to producer tap '{args.tap_name}' at {args.device_ip}",
              file=sys.stderr)
        return 1

    stats = FrameStats()
    started = time.monotonic()
    deadline = started + args.duration
    print(f"Connected read-only to '{args.tap_name}' at {args.device_ip}")
    try:
        while time.monotonic() < deadline:
            raw = tap.read(timeout_ms=100)
            if raw is None:
                continue
            parse_frame(raw, stats)
            if args.max_frames is not None and stats.frames >= args.max_frames:
                break
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        elapsed = time.monotonic() - started
        tap.disconnect()
        print_report(stats, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
