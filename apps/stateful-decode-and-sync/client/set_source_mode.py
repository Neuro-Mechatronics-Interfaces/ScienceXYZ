#!/usr/bin/env python3
"""set_source_mode.py

Toggle the stateful_decode_and_sync App between the real RHD2132 probe and its
in-app synthetic source, live, over the "set_source_mode" consumer tap.

Payload: a ListValue [mode] where mode is 0 = SAMPLING (forward the real
upstream frames) or 1 = SYNTHETIC (generate the ported gateware model).
"""

import argparse
import sys
import time

from google.protobuf.struct_pb2 import ListValue, Value
from synapse.client.taps import Tap

MODES = {"sampling": 0, "synthetic": 1, "0": 0, "1": 1}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set broadband source mode")
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="set_source_mode")
    parser.add_argument("mode", choices=sorted(MODES.keys()),
                        help="sampling|synthetic (or 0|1)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode = MODES[args.mode]

    list_value = ListValue()
    v = Value()
    v.number_value = float(mode)
    list_value.values.append(v)

    tap = Tap(args.device_ip)
    try:
        if not tap.connect(args.tap_name):
            print(f"Failed to connect to tap '{args.tap_name}' at {args.device_ip}",
                  file=sys.stderr)
            sys.exit(1)
        # The Tap uses PUB/SUB for consumer taps.  A successful TCP connect
        # does not mean the device subscriber has completed its ZeroMQ
        # subscription handshake; without this settle time the first command
        # can be silently discarded by the PUB socket.
        time.sleep(0.5)
        if tap.send(list_value.SerializeToString()):
            print(f"Set source mode -> {mode} "
                  f"({'SAMPLING' if mode == 0 else 'SYNTHETIC'})")
        else:
            print("Failed to send message", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)
    finally:
        tap.disconnect()


if __name__ == "__main__":
    main()
