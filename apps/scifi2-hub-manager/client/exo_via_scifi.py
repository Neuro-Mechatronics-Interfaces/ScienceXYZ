#!/usr/bin/env python3
"""Operator-run Exo bench commands through the laptop Synapse bridge."""
import argparse
import json
import time

from scifi2_hub_manager.client import NdjsonClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["probe", "angles", "limits", "move", "off"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--joint", choices=["thumb", "index", "middle", "ring", "pinky", "wrist"], default="index")
    parser.add_argument("--value", type=int, default=10)
    parser.add_argument("--allow-motion", action="store_true")
    args = parser.parse_args()
    if args.command == "move" and (not args.allow_motion or not -100 <= args.value <= 100):
        parser.error("move requires --allow-motion and --value in [-100, 100]")
    with NdjsonClient(port=args.port, timeout=15) as client:
        def send(command, **kwargs):
            print(json.dumps({"sending": command, **kwargs}), flush=True)
            print(json.dumps(client.request(command, **kwargs)), flush=True)

        try:
            if args.command != "off":
                send("set_exo_mode", mode="connected")
                query = {"probe": "version", "angles": "get_gesture_angles:all", "limits": "check_limits"}.get(args.command)
                if query:
                    send("query_exo", query=query)
                else:
                    send("set_exo_mode", mode="external")
                    send("set_exo_pose", joints={args.joint: args.value})
                    # One bounded test pulse; never retry a motion after an uncertain result.
                    time.sleep(0.25)
                print(json.dumps(client.get_state_snapshot()["exo"]), flush=True)
        finally:
            # Failure is surfaced even during cleanup; disconnect is not physical confirmation.
            send("set_exo_mode", mode="off")


if __name__ == "__main__":
    main()
