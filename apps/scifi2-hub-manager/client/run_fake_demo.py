#!/usr/bin/env python3
"""One-process install smoke test for the controller without a SciFi-2."""

from __future__ import annotations

import time

from scifi2_hub_manager import proto
from scifi2_hub_manager.controller import BroadbandController
from scifi2_hub_manager.transport import FakeTapTransport


def _state_payload() -> bytes:
    state = proto.StateSnapshot()
    state.protocol_version = 1
    state.state_version = 1
    state.timestamp_ns = 1
    state.pipeline.state = 3
    state.pipeline.source_mode = 2
    state.active.collection_id = 0
    state.active.label = 0
    state.model.phase = 1
    return state.SerializeToString()


def main() -> None:
    transport = FakeTapTransport()
    controller = BroadbandController("fake", transport, timeout=1)
    controller.connect()
    try:
        transport.inject("state", _state_payload())
        deadline = time.monotonic() + 1
        while controller.state.state_version != 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        if controller.state.state_version != 1:
            raise SystemExit("fake demo did not receive a state snapshot")
        print(f"fake demo connected={controller.connected} pipeline={controller.state.pipeline_state} source={controller.state.source_mode}")
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
