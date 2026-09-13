#!/usr/bin/env python3
"""Minimal SNTP/NTP server for the isolated SciFi-2 bench LAN.

The bench network (192.168.100.0/24) has no internet route and the wireless
router runs no NTP service and hands out no DHCP option 42 NTP server, so the
SciFi-2 headstage -- which also lacks a working battery-backed RTC -- boots to a
bogus 1970 clock and never self-corrects. The only correct clock on that segment
is a host machine (Max's Windows laptop at 192.168.100.14, or the M4 MacBook Pro
at 192.168.100.106). This script turns whichever host runs it into an NTP server
so the SciFi-2's stock ``systemd-timesyncd`` (configured to prefer .14 then .106)
syncs from it automatically on every reconnect.

It answers SNTPv4 (RFC 4330 / NTPv3-compatible) client requests on UDP 123 using
the host's own clock. It does NOT discipline the host clock -- point the host at
a real time source first (Windows Time service / macOS network time) so the value
served is accurate. Run it while at the bench; stop with Ctrl-C.

Usage:
    python scripts/bench_ntp_server.py [--bind 192.168.100.14] [--port 123]
                                       [--stratum 4] [--once] [--verbose]

Binding UDP 123 requires privilege on most systems (Administrator on Windows,
root/sudo on Linux/macOS). Use --port for an unprivileged test port; the SciFi-2
expects 123. --bind defaults to all interfaces; pass the bench IP to avoid
answering on unrelated networks.
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time

# Seconds between the NTP epoch (1900-01-01) and the Unix epoch (1970-01-01).
NTP_UNIX_DELTA = 2208988800

# Reference identifier shown to clients; "LOCL" = uncalibrated local clock, the
# honest label for a host that may only be roughly disciplined.
REFID = b"LOCL"


def _to_ntp(ts_unix: float) -> tuple[int, int]:
    """Convert a Unix timestamp to 64-bit NTP (seconds, fraction)."""
    ntp = ts_unix + NTP_UNIX_DELTA
    secs = int(ntp)
    frac = int((ntp - secs) * (2 ** 32)) & 0xFFFFFFFF
    return secs & 0xFFFFFFFF, frac


def build_response(request: bytes, stratum: int, recv_unix: float) -> bytes:
    """Build a 48-byte SNTP server reply for a client request."""
    # Byte 0: LI (0, no warning) | VN (echo client's version, clamp 3..4) | Mode 4 (server).
    client_vn = (request[0] >> 3) & 0x7 if request else 4
    vn = min(max(client_vn, 3), 4)
    li_vn_mode = (0 << 6) | (vn << 3) | 4

    poll = request[2] if len(request) > 2 else 4
    precision = -20  # 2^-20 s ~ microsecond; honest for a host software clock.

    root_delay = 0
    root_dispersion = 0

    # The client's Transmit Timestamp (bytes 40..48) must be echoed back as the
    # Originate Timestamp so the client can compute offset/round-trip.
    originate = request[40:48] if len(request) >= 48 else b"\x00" * 8

    recv_s, recv_f = _to_ntp(recv_unix)
    tx_s, tx_f = _to_ntp(time.time())
    ref_s, ref_f = _to_ntp(recv_unix)  # reference = last time we "synced" (now)

    # Exact 48-byte SNTP layout:
    #   1B  LI|VN|Mode
    #   1B  stratum
    #   1B  poll
    #   1B  precision (signed)
    #   4B  root delay
    #   4B  root dispersion
    #   4B  reference identifier
    #   8B  reference timestamp
    #   8B  originate timestamp (echo of client transmit)
    #   8B  receive timestamp
    #   8B  transmit timestamp
    return (
        struct.pack("!BBBb", li_vn_mode, stratum, poll, precision)
        + struct.pack("!I", root_delay)
        + struct.pack("!I", root_dispersion)
        + REFID
        + struct.pack("!II", ref_s, ref_f)
        + originate
        + struct.pack("!II", recv_s, recv_f)
        + struct.pack("!II", tx_s, tx_f)
    )


def serve(bind: str, port: int, stratum: int, once: bool, verbose: bool) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((bind, port))
    except PermissionError:
        sys.stderr.write(
            f"error: binding UDP {bind}:{port} needs privilege "
            f"(Administrator/root). Re-run elevated, or use --port for a test.\n"
        )
        return 2
    except OSError as exc:
        sys.stderr.write(f"error: cannot bind {bind}:{port}: {exc}\n")
        return 2

    where = bind if bind not in ("", "0.0.0.0") else "0.0.0.0 (all interfaces)"
    print(
        f"bench NTP server listening on {where}:{port}  stratum={stratum}  "
        f"host clock={time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC",
        flush=True,
    )
    print("serving the host's own clock; Ctrl-C to stop.", flush=True)

    try:
        while True:
            data, addr = sock.recvfrom(512)
            recv_unix = time.time()
            if len(data) < 1:
                continue
            reply = build_response(data, stratum, recv_unix)
            sock.sendto(reply, addr)
            if verbose:
                print(
                    f"{time.strftime('%H:%M:%S')} served {addr[0]}:{addr[1]} "
                    f"({len(data)}B req -> {len(reply)}B reply)",
                    flush=True,
                )
            if once:
                break
    except KeyboardInterrupt:
        print("\nstopped.", flush=True)
    finally:
        sock.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="0.0.0.0",
                    help="local address to bind (default all; use 192.168.100.14 "
                         "or 192.168.100.106 to restrict to the bench NIC)")
    ap.add_argument("--port", type=int, default=123,
                    help="UDP port (default 123; the SciFi-2 expects 123)")
    ap.add_argument("--stratum", type=int, default=4,
                    help="advertised stratum (default 4; 1=primary, keep >1 for a "
                         "host that is itself an SNTP client)")
    ap.add_argument("--once", action="store_true",
                    help="answer a single request then exit (for testing)")
    ap.add_argument("--verbose", action="store_true",
                    help="log every request served")
    args = ap.parse_args(argv)
    if not (1 <= args.stratum <= 15):
        ap.error("--stratum must be 1..15")
    return serve(args.bind, args.port, args.stratum, args.once, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
