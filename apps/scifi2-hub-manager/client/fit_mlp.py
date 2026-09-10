#!/usr/bin/env python3
"""fit_mlp.py

Trigger an on-device training pass of the scifi2_hub_manager App's MLP over
all currently captured feature windows, via the "fit_mlp" consumer tap.

Payload: a ListValue [epochs?]. If epochs is omitted, the App uses its
configured mlp_epochs. Training runs on the App's managed fit worker; watch the
App logs or command_result tap for progress and final loss/accuracy.
"""

import argparse
import sys
import time

from google.protobuf.struct_pb2 import ListValue, Value
from synapse.client.taps import Tap


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger on-device MLP training")
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="fit_mlp")
    parser.add_argument("--epochs", type=int, default=None,
                        help="optional epochs override for this fit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    list_value = ListValue()
    if args.epochs is not None:
        v = Value()
        v.number_value = float(args.epochs)
        list_value.values.append(v)

    tap = Tap(args.device_ip)
    try:
        if not tap.connect(args.tap_name):
            print(f"Failed to connect to tap '{args.tap_name}' at {args.device_ip}",
                  file=sys.stderr)
            sys.exit(1)
        # Allow the device-side SUB socket to finish its ZeroMQ handshake;
        # PUB silently drops messages sent during the slow-joiner window.
        time.sleep(0.5)
        if tap.send(list_value.SerializeToString()):
            override = "" if args.epochs is None else f" (epochs={args.epochs})"
            print(f"fit_mlp requested{override}; check App logs for loss/accuracy")
        else:
            print("Failed to send message", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.5)
    finally:
        tap.disconnect()


if __name__ == "__main__":
    main()
