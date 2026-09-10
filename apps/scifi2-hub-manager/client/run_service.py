#!/usr/bin/env python3
import argparse
import asyncio

from scifi2_hub_manager.controller import BroadbandController
from scifi2_hub_manager.service import serve

def main() -> None:
    parser = argparse.ArgumentParser(description="Broadband mode-switch loopback service")
    parser.add_argument("--device-ip", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(BroadbandController(args.device_ip), args.host, args.port))


if __name__ == "__main__":
    main()
