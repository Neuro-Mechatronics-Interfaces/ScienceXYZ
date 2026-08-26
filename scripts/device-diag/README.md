# device-diag — on-device diagnostics for the SciFi-2 via the deploy channel

Small Debian packages whose `postinst` gathers device state, delivered over `synapsectl peripherals deploy --package` (gRPC `DeployApp`, no SSH required). Developed 2026-08-24 while investigating the disappearance of the Axon Omnetics adapter peripheral (`IntanRHD2132`, ID 200).

## Why this exists

Direct inspection channels on the SciFi-2 are limited:

- `synapsectl file ls/get/rm` (SFTP, user `scifi-sftp`, password on the device under **Settings > Device Info > DiskWriter Pass**) is **jailed to the data directories** — `/usr/lib` etc. are not reachable.
- `synapsectl logs` returns only `scifi-server`'s curated log entries, not the systemd journal, and the deploy channel's "Device Responses" panel reports step milestones without relaying `postinst` stdout.
- The device clock is not reliable across boots (observed jumping backward a day and running a month behind), so `journalctl` output sorted by timestamp can show a *previous* boot at the tail. Always scope with `journalctl -b`.

The workaround is the **mailbox pattern**: the `postinst` writes its report to `/tmp/nml-diag-report.txt` and copies it into the data directories (`/opt/scifi/data`, `.../sdcard`, `.../disk_writer`), where the jailed SFTP account can fetch it.

## Packages

- `nml-diag/` — strictly read-only state gathering. The `postinst` body is edited per investigation (bump `Version:` in `DEBIAN/control` each time); the committed version captures boot-scoped `journalctl -u scifi-server`, a grep for at_usb/axon/peripheral/plugin activity, and the `at_usb` device nodes.
- `nml-quarantine/` — the one mutating tool: moves `axon_test_source.so` from `/usr/lib/scifi/plugins/` to `/opt/scifi/data/quarantine/` to test plugin-interference hypotheses. Reversible by redeploying the original `scifi-axon-test-source` .deb. (Not needed in the 2026-08-24 investigation; the journal exonerated the plugin.)

Constraints that keep these safe: install at most a doc file, never touch shared paths, end `postinst` with `exit 0` unconditionally, never restart `scifi-server` from inside `postinst` (it is the process servicing the deploy), and never call `dpkg` mutating verbs from `postinst` (the dpkg lock is held).

## Build (WSL, Docker; dpkg-deb needs sane permissions, hence the container copy)

```bash
docker run --rm -v "$(pwd):/w" ubuntu:22.04 bash -c \
  "cp -r /w/nml-diag /tmp/ && find /tmp/nml-diag -type d -exec chmod 755 {} + && \
   find /tmp/nml-diag -type f -exec chmod 644 {} + && chmod 755 /tmp/nml-diag/DEBIAN/postinst && \
   dpkg-deb --build --root-owner-group /tmp/nml-diag /w/nml-diag_<VERSION>_all.deb"
```

Run from this directory (`scripts/device-diag/`). Building directly on `/mnt/c` fails: drvfs reports mode 777 and `dpkg-deb` rejects a 777 control directory.

## Deploy and fetch

```bash
synapsectl -u <DEVICE_IP> peripherals deploy driver --package "$(pwd)/nml-diag_<VERSION>_all.deb"
synapsectl --uri <DEVICE_IP> file get nml-diag-report.txt
```

The `Section: synapse-peripherals` in `DEBIAN/control` is what makes the device's `DeployApp` handler accept the package.

## Cleanup

The packages are inert after reporting. With shell access (or via Science): `dpkg -r nml-diag nml-quarantine`.

## Device facts learned while building this (2026-08-24)

- `scifi-server` boot config: `axon_interface: kUSB`, `fpga_clock_freq_hz: 80000000`, `time_source: TIME_SOURCE_SAMPLE_COUNTER`.
- Peripheral-facing USB is not the Linux USB host stack (only `dummy_hcd` exists); the path involves `msm_usb_bridge` char devices `/dev/at_usb0`, `/dev/at_usb1` ("at" matching the SDK's `AXON_TERMINAL` naming) feeding `scifi-server`'s USBDeviceManager → PeripheralRegistry.
- `peripheral_id` values 1–2 in a `BroadbandSourceConfig` are command-range aliases ("first broadband source"), not concrete peripheral IDs; real IDs resolve via a direct record-peripheral lookup.
- Firmware does not ship `libscifi-peripheral-sdk`; the example driver `.deb` provides it in `/usr/lib` (benign, since nothing in firmware links against it).
