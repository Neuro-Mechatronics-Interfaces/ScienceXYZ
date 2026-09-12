#!/usr/bin/env bash
# From repository root in WSL/Linux. No hardware or device CLI calls.
set -euo pipefail
g++ -std=c++17 -Wall -Wextra -Werror -DEXO_AXON_USB=1 -DARDUINO_OpenRB \
  -Ifirmware/axon-exo/tests/stubs -Ithird_party/exo/src/cpp/nml_hand_exo \
  firmware/axon-exo/tests/firmware_transport_test.cpp \
  third_party/exo/src/cpp/nml_hand_exo/AxonUsbPeripheral.cpp -o /tmp/axon-firmware-test
/tmp/axon-firmware-test
g++ -std=c++17 -Wall -Wextra -Werror -Iapps/scifi2-hub-manager/src \
  apps/scifi2-hub-manager/tests/usb_cdc_test.cpp apps/scifi2-hub-manager/src/usb_cdc_port.cpp \
  -o /tmp/axon-cdc-test
/tmp/axon-cdc-test
