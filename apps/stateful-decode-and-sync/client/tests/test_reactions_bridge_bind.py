"""The Reactions bridge must accept both loopback families.

The Reaction-Task page (CtrlrSocketClient.connect) always dials
``ws://localhost:<port>``. On Windows ``localhost`` commonly resolves to IPv6
``::1`` before IPv4 ``127.0.0.1``; binding only ``127.0.0.1`` left the browser's
``::1`` attempt refused while a ``127.0.0.1`` client connected, so the GUI worked
but the browser did not. ``serve_bridge`` now binds both families. These tests
open a real bridge and connect over each loopback address; no device, recorder
or NDJSON service is contacted (the connection closes before any request, so the
handler never drives ``BridgeSession``).
"""
import asyncio
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from websockets.asyncio.client import connect

from stateful_decode_and_sync.calibration_task import make_profile
from stateful_decode_and_sync.reactions_bridge import serve_bridge


ORIGIN = "https://chr.nml.wtf"


def _args(output_root, port):
    return Namespace(device_uri="192.0.2.1:647", recorder="build/raw-recorder/task-recorder",
                     output_root=str(output_root), service_port=18765, port=port,
                     origin=[ORIGIN])


class BridgeBindTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_loopback_families_accept(self):
        with tempfile.TemporaryDirectory() as temp:
            port = 9973
            task = asyncio.create_task(serve_bridge(_args(Path(temp), port), make_profile(), {}))
            try:
                await asyncio.sleep(0.3)  # let the server bind
                for host in ("127.0.0.1", "[::1]"):
                    url = f"ws://{host}:{port}"
                    async with connect(url, origin=ORIGIN) as socket:
                        # Connecting and closing exercises the accept path on this
                        # family without driving a recording session.
                        await socket.close()
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def test_localhost_resolution_reaches_the_bridge(self):
        # Whatever localhost resolves to first on this host, the bridge must be
        # reachable at ws://localhost -- the exact URL the browser dials.
        with tempfile.TemporaryDirectory() as temp:
            port = 9974
            task = asyncio.create_task(serve_bridge(_args(Path(temp), port), make_profile(), {}))
            try:
                await asyncio.sleep(0.3)
                async with connect(f"ws://localhost:{port}", origin=ORIGIN) as socket:
                    await socket.close()
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass


if __name__ == "__main__":
    unittest.main()
