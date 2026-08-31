#!/usr/bin/env python3
"""Read-only broadband_out frame counter and metadata probe.

The probe subscribes to a producer tap for a bounded duration.  It does not
send commands to the device, so it can be run while source mode and capture
state are controlled by another client.  Sequence gaps and timestamp
regressions are counted separately from protobuf parse failures.
"""

from __future__ import annotations

import argparse
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

    def add(self, frame: BroadbandFrame) -> None:
        sequence = int(frame.sequence_number)
        timestamp = int(frame.timestamp_ns)

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
            return False
    except Exception:
        stats.parse_errors += 1
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
