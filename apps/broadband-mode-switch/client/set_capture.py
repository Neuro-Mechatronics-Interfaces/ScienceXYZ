#!/usr/bin/env python3
"""set_capture.py

Route feature windows into a per-class ring buffer on the broadband-mode-switch
App, over the "set_capture" consumer tap.

Payload: a ListValue [label, enable]. While enable is non-zero, each MPF
feature window computed by the App is appended to buffers_[label]. Send
enable=0 to stop capturing.
"""

import argparse
import sys
import time

from google.protobuf.struct_pb2 import ListValue, Value
from synapse.client.taps import Tap


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enable/disable labeled capture")
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="set_capture")
    parser.add_argument("--label", type=int, required=True, help="class index")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true", help="start capturing to --label")
    group.add_argument("--off", action="store_true", help="stop capturing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enable = 1 if args.on else 0

    list_value = ListValue()
    for x in (args.label, enable):
        v = Value()
        v.number_value = float(x)
        list_value.values.append(v)

    tap = Tap(args.device_ip)
    try:
        if not tap.connect(args.tap_name):
            print(f"Failed to connect to tap '{args.tap_name}' at {args.device_ip}",
                  file=sys.stderr)
            sys.exit(1)
        if tap.send(list_value.SerializeToString()):
            print(f"set_capture label={args.label} enable={enable}")
        else:
            print("Failed to send message", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)
    finally:
        tap.disconnect()


if __name__ == "__main__":
    main()
