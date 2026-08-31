#!/usr/bin/env python3
import argparse

from broadband_mode_switch.gui import run_gui

parser = argparse.ArgumentParser(description="Broadband mode-switch GUI")
parser.add_argument("--device-ip", required=True)
run_gui(parser.parse_args().device_ip)
