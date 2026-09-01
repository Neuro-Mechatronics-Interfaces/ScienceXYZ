#!/usr/bin/env python3
"""listen_class.py

Subscribe to the stateful_decode_and_sync App's "class_out" tap and print the
per-window softmax distribution and argmax class produced once the MLP is
trained (fit_mlp) and running.

The tap carries a Tensor of shape [num_classes], little-endian float32.
"""

import argparse
import struct
import sys

import numpy as np
from synapse.api.datatype_pb2 import Tensor
from synapse.client.taps import Tap


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Listen to class_out softmax tensors")
    parser.add_argument("--device-ip", default="192.168.100.157",
                        help="IP address of the Synapse device")
    parser.add_argument("--tap-name", default="class_out")
    return parser.parse_args()


def tensor_to_probs(tensor: Tensor) -> np.ndarray:
    data = tensor.data
    if tensor.endianness == Tensor.Endianness.TENSOR_BIG_ENDIAN:
        vals = struct.unpack(f">{len(data) // 4}f", data)
        return np.array(vals, dtype=np.float32)
    return np.frombuffer(data, dtype=np.float32)


def main() -> None:
    args = parse_args()

    tap = Tap(args.device_ip)
    if not tap.connect(args.tap_name):
        print(f"Failed to connect to tap '{args.tap_name}' at {args.device_ip}",
              file=sys.stderr)
        sys.exit(1)
    print(f"Connected to '{args.tap_name}' at {args.device_ip}. Ctrl-C to exit.")

    try:
        while True:
            raw = tap.read()
            if raw is None:
                continue
            tensor = Tensor()
            tensor.ParseFromString(raw)
            probs = tensor_to_probs(tensor)
            if probs.size == 0:
                continue
            argmax = int(np.argmax(probs))
            dist = " ".join(f"{p:.3f}" for p in probs)
            print(f"class={argmax}  probs=[{dist}]")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        tap.disconnect()


if __name__ == "__main__":
    main()
