#!/usr/bin/env python3
import argparse
from scifi2_hub_manager.exo_gui import run_gui


def main():
    parser = argparse.ArgumentParser(description="Exo bench GUI through SciFi-2 over Wi-Fi")
    parser.add_argument("--device-ip", required=True)
    run_gui(parser.parse_args().device_ip)


if __name__ == "__main__":
    main()
