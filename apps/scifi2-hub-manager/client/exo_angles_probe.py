"""Operator read-only Exo angle Tap viewer. Uses the configured motor mapping."""
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
    args = parser.parse_args()
    if not 0 < args.duration <= 3600:
        parser.error("duration must be in (0, 3600]")
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    app = next(n for n in config["nodes"] if n.get("application", {}).get("name") == "scifi2-hub-manager")
    source_id = app["application"]["parameters"]["exo_source_node_id"]
    source = next(n["broadbandSource"] for n in config["nodes"] if n["id"] == source_id)
    channels = source["signal"]["electrode"]["channels"]
    motors = [c["electrode_id"] // 4 for c in channels[::4]]
    for i, c in enumerate(channels):
        if c["id"] != i or c["electrode_id"] != 4*motors[i//4]+i%4:
            raise ValueError("invalid Exo channel order")
    tap = Tap(args.device_ip)
    if not tap.connect("exo_angles"):
        raise RuntimeError("exo_angles Tap unavailable; check App and source configuration")
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
        raise RuntimeError("no Exo angle frames received; no simulated data substituted")


if __name__ == "__main__":
    main()
