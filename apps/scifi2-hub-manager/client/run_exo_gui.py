#!/usr/bin/env python3
import argparse
from scifi2_hub_manager.exo_gui import run_gui


def main():
    parser = argparse.ArgumentParser(description="Exo bench GUI through SciFi-2 over Wi-Fi")
    parser.add_argument("--device-ip", required=False, help="IP address of the SciFi-2 hub to connect to", default="192.168.100.157")
    run_gui(parser.parse_args().device_ip)


if __name__ == "__main__":
    main()
