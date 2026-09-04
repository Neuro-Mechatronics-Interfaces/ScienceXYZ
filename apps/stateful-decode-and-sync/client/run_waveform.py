#!/usr/bin/env python3
"""Live read-only waveform viewer for the broadband_out producer tap."""

import argparse

from stateful_decode_and_sync.waveform import run_waveform


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live broadband waveform viewer (read-only; sends no device commands)"
    )
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="broadband_out",
                        help="producer tap to plot")
    parser.add_argument("--duration", type=float, default=2.0,
                        help="seconds of history shown per trace (default: 2)")
    parser.add_argument("--sample-rate", type=int, default=20000,
                        help="expected upstream sample rate for the time axis and buffer size")
    parser.add_argument("--max-channels", type=int, default=32,
                        help="maximum channels the buffer retains (default: 32)")
    parser.add_argument("--columns", type=int, default=1,
                        help="initial number of grid columns (e.g. 8 for a 4x8 grid of 32 ch)")
    parser.add_argument("--channels", default="",
                        help="initial channel selection/order, e.g. '0-31' or '0,4,8' "
                             "(empty = all). Editable live in the window.")
    args = parser.parse_args()
    run_waveform(
        args.device_ip,
        duration_s=args.duration,
        expected_sample_rate_hz=args.sample_rate,
        max_channels=args.max_channels,
        columns=args.columns,
        channel_spec=args.channels,
        tap_name=args.tap_name,
    )


if __name__ == "__main__":
    main()
