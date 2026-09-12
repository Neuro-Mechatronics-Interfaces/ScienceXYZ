# OpenRB_Axon_Enum_Proof (bench-only)

Minimum proof that an OpenRB-150 can be **registered** as a Synapse/SciFi
peripheral over `scifi-server`'s USB/Axon peripheral bus, with no Lattice FPGA
and without disturbing the working exoskeleton firmware.

```
USB attach  ->  Axon ENUMERATE (type 3)  ->  reply type 4 advertising 0xF001
            ->  scifi-server PeripheralManager dispatches 0xF001
            ->  /usr/lib/scifi/plugins/axon_test_source.so factory
            ->  peripheral shows up as "Axon Test Source"
```

Streaming (`CONFIGURE` / `START_STREAM` / `DATA_FRAME`) and exoskeleton control
are **out of scope** — this sketch only answers the enumerate handshake.

See [`docs/axon-usb-enumeration-proof.md`](../../docs/axon-usb-enumeration-proof.md)
for the full design, the wire contract, the host-side VID patch, and the bench
test procedure.

## What it is

A standalone Arduino sketch (not part of the `third_party/exo` submodule, which
stays untouched) that registers a single **vendor-specific USB interface with
one BULK OUT + one BULK IN endpoint** via the SAMD core's PluggableUSB, and
parses/answers Axon `0xC0FFEE00` frames.

## USB identity and the interface-0 requirement

* **VID/PID:** the OpenRB-150's real ROBOTIS VID `0x2F5D` / PID `0x2202` (from
  the board definition). The firmware does **not** spoof Science's VID.
  `scifi-server` accepts only `0x399A`/`0x2AC1`, so the bench host runs a
  reversible `scifi-server` patch that **adds** `0x2F5D` as a third accepted VID
  while keeping `0x399A` and `0x2AC1` (so the existing SciNetics/RHD adapter,
  `2AC1:0004`, keeps working). See `scripts/scifi-server-accept-openrb-vid.py`
  (`--self-test` / `--check` / `--revert`) and the design doc.
* **Interface 0:** `scifi-server` calls `libusb_claim_interface(handle, 0)`, so
  the Axon bulk interface must be interface 0. The SAMD core assigns
  PluggableUSB interface numbers in **plug order** (first plugged -> interface
  0). The core's CDC `SerialUSB` also plugs itself, so the Axon module is
  declared with `__attribute__((init_priority(101)))` to construct (and plug)
  **before** `SerialUSB`. CDC is left **enabled** (this OpenRB core does not
  link with CDC fully disabled -- see MISTAKES.md), so the device enumerates as
  a composite: interface 0 = Axon (2 bulk EPs), interfaces 1-2 = CDC (3 EPs);
  2 + 3 + control = 7 = the core's endpoint cap. `scifi-server` ignores the CDC
  interfaces, and Serial-over-USB still works for debugging.

## Build

Uses the ROBOTIS OpenRB-150 SAMD core (`OpenRB-150:samd:OpenRB-150`) with the
stock board defaults -- no extra build properties are required:

```bash
# From firmware/OpenRB_Axon_Enum_Proof/
arduino-cli compile \
  --fqbn OpenRB-150:samd:OpenRB-150 \
  --export-binaries .
```

The `.ino` pulls in `AxonUsbPeripheral.{h,cpp}` from the same folder
automatically (Arduino sketch build includes all sources in the sketch dir).
Expected size: ~12 KB flash, ~3.7 KB RAM.

## Flash

Double-tap reset to enter the bootloader (or use `use_1200bps_touch`), then:

```bash
arduino-cli upload \
  --fqbn OpenRB-150:samd:OpenRB-150 \
  --port <BOOTLOADER_PORT> \
  --input-dir .
```

`<BOOTLOADER_PORT>` is the serial port that appears while the board is in SAM-BA
bootloader mode (VID 0x2F5D / PID 0x0200-ish). Do not flash unless you intend to
replace the currently loaded firmware on that board; this is a separate,
bench-only image.

## Status LED

* slow blink (1 Hz): USB attached but host has not configured the device;
* fast blink (4 Hz): USB configured; waiting for / serving ENUMERATE;
* quick double-blink: an ENUMERATE response (advertising `0xF001`) was just sent.
