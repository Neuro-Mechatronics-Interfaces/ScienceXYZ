#!/usr/bin/env python3
"""Standalone NDJSON control service for the NML_Hand_Exo dual-CDC link.

Runs a threaded exo worker behind a loopback NDJSON service, independent of the
neural-device Tap controller service.  It owns the exo serial transport only.

Examples:
    run-exo-service --cmd-port COM10 --telem-port COM11
    run-exo-service                       # auto-discover the CDC pair
    run-exo-service --port 18766 --no-watchdog
"""

from __future__ import annotations

import argparse
import asyncio

from scifi2_hub_manager.exo_service import serve
from scifi2_hub_manager.exo_transport import make_dual_serial_factory
from scifi2_hub_manager.exo_worker import ExoConfig, ExoWorker


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="NML Hand Exo dual-CDC loopback control service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--cmd-port", default=None, help="command CDC COM port (auto-discovered if omitted)")
    parser.add_argument("--telem-port", default=None, help="telemetry CDC COM port (auto-discovered if omitted)")
    parser.add_argument("--baud", type=int, default=1_000_000)
    parser.add_argument("--total-current-ma", type=int, default=800,
                        help="combined current budget applied at arm time (0 leaves firmware default)")
    parser.add_argument("--per-motor-current-ma", type=int, default=250,
                        help="per-motor nominal current applied at arm time (0 leaves firmware default)")
    parser.add_argument("--watchdog-s", type=float, default=1.0,
                        help="idle seconds before easing to neutral rest")
    parser.add_argument("--no-watchdog", dest="watchdog_s", action="store_const", const=None,
                        help="disable the inactivity watchdog")
    parser.add_argument("--poll-interval-s", type=float, default=0.2,
                        help="pose read-back cadence")
    parser.add_argument("--no-poll", dest="poll_interval_s", action="store_const", const=None,
                        help="disable periodic pose read-back")
    args = parser.parse_args(argv)

    config = ExoConfig(
        comm_factory=make_dual_serial_factory(args.cmd_port, args.telem_port, args.baud),
        baudrate=args.baud,
        total_current_ma=args.total_current_ma or None,
        per_motor_current_ma=args.per_motor_current_ma or None,
        watchdog_s=args.watchdog_s,
        poll_interval_s=args.poll_interval_s,
    )
    worker = ExoWorker(config)
    asyncio.run(serve(worker, args.host, args.port))


if __name__ == "__main__":
    main()
