"""Transport construction for the exo worker.

Kept separate from :mod:`exo_worker` so the worker itself carries no import of
the ``nml_hand_exo`` SDK or ``pyserial``: it takes an already-built
``comm_factory`` callable.  This module supplies the real factory (a
``DualSerialComm`` over the device's two USB-CDC interfaces) and the CDC-pair
discovery helpers; tests supply their own in-process fake instead.
"""

from __future__ import annotations

from typing import Any, Callable

LINE_TERMINATOR = "\r\n"


def resolve_cdc_pair(cmd_port: str | None, telem_port: str | None) -> tuple[str, str]:
    """Resolve the (command, telemetry) COM-port pair for the exo.

    If both ports are given they are used directly (``DualSerialComm`` still
    probes and corrects the direction at connect time).  If neither is given,
    the project VID/PID device is auto-discovered.  If only one is given, its
    CDC sibling is located.
    """
    from serial.tools import list_ports
    from nml_hand_exo.interface._serial_ports import (
        find_cdc_sibling,
        preferred_nml_exo_command_port,
    )

    if cmd_port and telem_port:
        return cmd_port, telem_port

    ports = list(list_ports.comports())
    seed = cmd_port or telem_port or preferred_nml_exo_command_port(ports)
    if seed is None:
        raise RuntimeError(
            "no NML exo USB device found; pass --cmd-port and --telem-port explicitly"
        )
    pair = find_cdc_sibling(seed, ports)
    if pair is None:
        raise RuntimeError(
            f"could not find the dual-CDC sibling of {seed!r}; pass both "
            "--cmd-port and --telem-port explicitly"
        )
    return pair


def make_dual_serial_factory(
    cmd_port: str | None,
    telem_port: str | None,
    baudrate: int,
    *,
    reply_timeout_s: float = 0.5,
) -> Callable[[], Any]:
    """Return a factory that builds a fresh ``DualSerialComm`` on each call.

    The pair is resolved lazily inside the factory so port discovery happens on
    the worker thread at connect time, not when the service is constructed.
    """

    def factory():
        from nml_hand_exo import DualSerialComm

        cmd, telem = resolve_cdc_pair(cmd_port, telem_port)
        return DualSerialComm(
            cmd_port=cmd,
            telem_port=telem,
            baudrate=baudrate,
            response_timeout=reply_timeout_s,
            line_terminator=LINE_TERMINATOR,
        )

    return factory
