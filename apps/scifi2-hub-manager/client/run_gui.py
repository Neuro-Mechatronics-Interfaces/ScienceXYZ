#!/usr/bin/env python3
import argparse

from scifi2_hub_manager.gui import run_gui

def main() -> None:
    parser = argparse.ArgumentParser(description="Broadband mode-switch GUI")
    parser.add_argument("--device-ip", required=True)
    run_gui(parser.parse_args().device_ip)


if __name__ == "__main__":
    main()
