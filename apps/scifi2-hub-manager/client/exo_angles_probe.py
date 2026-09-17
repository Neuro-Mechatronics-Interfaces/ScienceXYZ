"""Operator read-only Exo angle Tap viewer. Uses the configured motor mapping.

Reads the Exo source node's own device tap (``broadband_source_<node_id>``),
which the server exposes for every configured ``kBroadbandSource`` regardless of
graph edges. This is the correct source for an edgeless auxiliary node: the App
does not (and cannot) republish an edgeless source to its ``exo_angles`` tap
because ``setup_reader(node_id)`` binds only to graph-connected inputs (see
docs/t19-synapse-multisource-capability-audit.md and MISTAKES.md 2026-09-13).
Override the tap with ``--tap`` if a future App genuinely republishes one.
"""
import argparse
import json
import time
from pathlib import Path
from synapse.api.datatype_pb2 import BroadbandFrame
from synapse.client.taps import Tap
from scifi2_hub_manager.exo_angles import decode_angles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument(
        "--tap", default=None,
        help="Tap name to read (default: broadband_source_<exo_source_node_id>, "
             "the source node's own device tap)")
    args = parser.parse_args()
    if not 0 < args.duration <= 3600:
        parser.error("duration must be in (0, 3600]")
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    app = next(n for n in config["nodes"] if n.get("application", {}).get("name") == "scifi2-hub-manager")
    source_id = app["application"]["parameters"]["exo_source_node_id"]
    source = next(n["broadbandSource"] for n in config["nodes"] if n["id"] == source_id)
    channels = source["signal"]["electrode"]["channels"]
    motors = [c["electrode_id"] // 8 for c in channels[::8]]
    for i, c in enumerate(channels):
        if c["id"] != i or c["electrode_id"] != 8*motors[i//8]+i%8:
            raise ValueError("invalid Exo channel order")
    tap_name = args.tap or f"broadband_source_{source_id}"
    tap = Tap(args.device_ip)
    if not tap.connect(tap_name):
        raise RuntimeError(
            f"{tap_name} Tap unavailable; check 'synapsectl -u <dev> taps list' "
            "for the source node's tap and that the device/App is running")
    received = 0
    try:
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            raw = tap.read(timeout_ms=100)
            if not raw:
                continue
            frame = BroadbandFrame(); frame.ParseFromString(raw)
            decoded = decode_angles(frame, motors)
            decoded["host_receive_steady_ns"] = time.monotonic_ns()
            print(json.dumps(decoded), flush=True)
            received += 1
    finally:
        tap.disconnect()
    if not received:
        raise RuntimeError(
            f"no Exo angle frames received on {tap_name}; no simulated data substituted")


if __name__ == "__main__":
    main()
