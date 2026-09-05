"""Operator-run loopback WebSocket bridge for Reactions TaskSocketClient."""
import argparse
import asyncio
import json
from pathlib import Path
from stateful_decode_and_sync.calibration_task import load_profile
from stateful_decode_and_sync.reactions_bridge import serve_bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-uri", required=True)
    parser.add_argument("--recorder", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--origin", action="append", required=True, help="exact Reactions page origin, e.g. http://localhost:8080")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--service-port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve_bridge(args, load_profile(args.profile),
                json.loads(Path(args.provenance).read_text(encoding="utf-8-sig"))))


if __name__ == "__main__":
    main()
