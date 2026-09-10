#!/usr/bin/env python3
"""Launch the PySide6 calibration-session console.

A GUI front end for the same flow as ``calibrate-session``: generate/reuse a
session, show the operator ``synapsectl start`` line, gate on an ``info``
capture, launch the host service + Reactions bridge, and drive recording/task
commands over the bridge WebSocket. It never runs ``synapsectl`` itself.
"""
from scifi2_hub_manager.session_gui import run_session_gui


def main() -> None:
    run_session_gui()


if __name__ == "__main__":
    main()
