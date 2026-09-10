#!/usr/bin/env python3
"""Live read-only waveform viewer for the broadband_out producer tap."""

import argparse
import sys

from stateful_decode_and_sync.waveform import run_waveform

# CLI flag -> QSettings field key. A flag present in argv this launch means the
# operator explicitly chose that value, so it overrides the stored one; absent
# flags fall back to whatever the per-user INI holds.
_FLAG_TO_SETTING = {
    "--device-ip": "device_uri",
    "--device-config": "device_config",
    "--synapsectl": "synapsectl",
    "--channels": "channels",
    "--columns": "columns",
    "--full-scale": "full_scale",
    "--timescale": "timescale",
}


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
    parser.add_argument("--sample-rate", type=int, default=2000,
                        help="expected broadband_out sample rate (Hz) for the initial time "
                             "axis and buffer size. Default 2000 matches the decimated "
                             "broadband_out; the per-frame sample_rate_hz overrides it live. "
                             "Use 20000 only for an undecimated (full-Nyquist) build.")
    parser.add_argument("--max-channels", type=int, default=32,
                        help="maximum channels the buffer retains (default: 32)")
    parser.add_argument("--columns", type=int, default=1,
                        help="initial number of grid columns (e.g. 8 for a 4x8 grid of 32 ch)")
    parser.add_argument("--channels", default="",
                        help="initial channel selection/order, e.g. '0-31' or '0,4,8' "
                             "(empty = all). Editable live in the window.")
    parser.add_argument("--full-scale", type=float, default=1000.0,
                        help="fixed y half-amplitude (±) at gain 1 (default: 1000). "
                             "Editable live in the Scale panel.")
    parser.add_argument("--timescale", type=float, default=0.0,
                        help="initial shared x-window in seconds (0 = full --duration). "
                             "Editable live in the Scale panel.")
    parser.add_argument("--device-config", default="",
                        help="device-config.json for the operator 'Run: start device' button "
                             "(empty = restart an already-configured device). Editable in the "
                             "Device panel.")
    parser.add_argument("--synapsectl", default="synapsectl",
                        help="command for the Device-panel synapsectl buttons (e.g. "
                             "'wsl synapsectl' to reach a WSL install). Editable in the panel.")
    args = parser.parse_args()
    # Which persisted fields the operator set explicitly this launch (so the CLI
    # value wins over the stored INI value). A flag with either "--flag value" or
    # "--flag=value" spelling counts.
    explicit = {
        key for flag, key in _FLAG_TO_SETTING.items()
        if any(tok == flag or tok.startswith(flag + "=") for tok in sys.argv[1:])
    }
    run_waveform(
        args.device_ip,
        duration_s=args.duration,
        expected_sample_rate_hz=args.sample_rate,
        max_channels=args.max_channels,
        columns=args.columns,
        channel_spec=args.channels,
        tap_name=args.tap_name,
        y_full_scale=args.full_scale,
        timescale_s=args.timescale,
        device_config=args.device_config,
        synapsectl_command=args.synapsectl,
        explicit_settings=explicit,
    )


if __name__ == "__main__":
    main()
