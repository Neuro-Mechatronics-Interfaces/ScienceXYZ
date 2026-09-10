#!/usr/bin/env python3
import argparse
import asyncio

from scifi2_hub_manager.controller import BroadbandController
from scifi2_hub_manager.service import serve

def main() -> None:
    parser = argparse.ArgumentParser(description="SciFi-2 hub loopback control service")
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--timeout", type=float, default=15.0, help="device command timeout in seconds")
    args = parser.parse_args()
    asyncio.run(serve(BroadbandController(args.device_ip, timeout=args.timeout), args.host, args.port))


if __name__ == "__main__":
    main()
