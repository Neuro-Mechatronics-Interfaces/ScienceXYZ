#!/usr/bin/env python3
"""Interactive, safety-ordered calibration example for the loopback service."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable

from stateful_decode_and_sync.client import NdjsonClient, SocketClientError


class CalibrationError(RuntimeError):
    pass


def _target_is_available(state: dict[str, Any], collection_id: int, label: int) -> bool:
    collections = state.get("collections") or []
    if not collections:
        return True
    for collection in collections:
        if collection.get("collection_id") != collection_id:
            continue
        labels = collection.get("labels") or []
        return not labels or any(item.get("label") == label for item in labels)
    return False


def calibrate(
    client: NdjsonClient,
    targets: Iterable[tuple[int, int]],
    *,
    prompt: Callable[[str], Any] = input,
) -> None:
    """Run calibration windows, with capture disabled at every target change.

    ``prompt`` is injectable so the sequence can be exercised in offline tests.
    The function always attempts to disable capture after enabling it.
    """
    state = client.get_state_snapshot()
    for collection_id, label in targets:
        if not _target_is_available(state, collection_id, label):
            raise CalibrationError(f"target collection={collection_id}, label={label} is unavailable")
        # This is the sole target-changing operation. It atomically selects the
        # target and leaves capture off before the operator presents a stimulus.
        client.prepare_capture(collection_id, label, False)
        prompt(f"Prepare collection {collection_id}, label {label}; press Enter to present")
        capture_requested = False
        try:
            capture_requested = True
            client.set_capture(True)
            prompt("Present/calibrate now; press Enter when the window is complete")
        finally:
            if capture_requested:
                client.set_capture(False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Safe stateful_decode_and_sync calibration prompter")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--collection", type=int, default=0)
    parser.add_argument("--labels", type=int, nargs="+", required=True)
    args = parser.parse_args()
    try:
        with NdjsonClient(args.host, args.port) as client:
            calibrate(client, ((args.collection, label) for label in args.labels))
    except (CalibrationError, SocketClientError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
