# TODO

The initial goals are:

1. Reliably discover, configure, and stream from the SciFi-2.
2. Develop C++ Synapse Apps for on-device neural signal processing.
3. Develop a C++ host application that receives Synapse Taps and merges them
   with additional experimental sensor streams.
4. Establish explicit timestamping, synchronization, logging, and provenance
   conventions before building experiment-specific applications.


## Synapse Bring-Up

The basic hardware smoke test is:

```
synapsectl -u 192.168.100.157 info
```

Before proceeding with an actual peripheral signal chain:

1. Confirm the SciFi-2 responds.
2. Record the reported firmware/Synapse versions.
3. Confirm the Axon adapter appears under Peripherals.
4. Record its reported peripheral ID and type.
5. Keep the signal chain stopped while modifying configuration.

Do not assume example peripheral IDs apply to physical hardware. In particular, IDs used by virtual/simulator configurations are not authoritative for the attached Axon adapter.

## Synapse Apps

Start custom App work from Science's current `synapse-example-app` rather than inventing the Docker/CMake/package structure.

Keep the first custom App intentionally simple:

```
kBroadbandSource -> kApplication -> Tap
```

The initial App should demonstrate:

- receiving BroadbandFrame data;
- preserving source timestamps and sequence numbers;
- detecting/reporting dropped frames;
- publishing a simple derived or diagnostic Tap;
- clean start/stop behavior.

Only after that path is reliable should filtering, spike detection, decoding, or inference be added.

Do not add electrical or optical stimulation behavior unless explicitly
requested. Recording and stimulation should remain separate concerns during
initial infrastructure development.

## NML Synapse Bridge — Protocol Investigation (2026-08-24)

First-pass investigation of Science's public custom-peripheral interface is
complete. See `docs/axon-peripheral-protocol.md` and
`docs/feasibility-mcu-vs-fpga.md`.

Established (proven from vendored code):

- A custom Axon peripheral = FPGA gateware on the SciFi's internal Lattice
  fabric (`via-devkit`, Radiant 2024.2) + an ARM64 driver `.so` dlopened by
  `scifi-server`. It talks AXI4-Stream to an encrypted SDK transport; it does
  not speak USB.
- A custom **record source** (`kBroadbandSource`) peripheral is fully supported
  and is the known-working example.
- Frame contract, opcodes, peripheral-ID window (`0xF001..0xFFFE`), and the
  `synapsectl peripherals build/deploy/gateware` toolchain are documented.

Open, blocking, needs Science or a hardware experiment:

- [ ] Is there any supported path for an external MCU on the peripheral-facing
      USB/Axon port to appear as a Synapse peripheral? (gates STM32/nRF52 as a
      *native* peripheral)
- [ ] Can a user-authored peripheral register as an electrical/optical
      stimulation **sink**, and with what SDK base class?
- [ ] Is `OpticalStimFrame.timestamp_ns` honoured as a future scheduled
      execution time, or only a stamp?
- [ ] Exact Lattice FPGA device string + whether Radiant Free suffices.
- [ ] Can a `via-devkit` (or equivalent Axon-transport board) be obtained?
- [ ] How are SciFi GPIO sync edges surfaced in Synapse streams/timestamps?

Bench facts (2026-08-24 `info`, device `SFI2-0-260534`, Synapse 2.4.1):
peripherals are IntanRHD2132 (ID 200, kBroadbandSource — the real 32-ch probe
ADC path), SciFi Virtual Recording Peripheral (ID 1000), and
VirtualOpticalStimPeripheral (ID 1001, kOpticalStimulation). The bare Axon
Omnetics adapter does not enumerate on its own.

### Next: record from the real Axon->Omnetics 32-channel probe

We write configuration, not ADC/driver code. `config/axon-omnetics-32ch.json`
clones the working virtual graph and parses cleanly as a DeviceConfiguration
proto. But the target peripheral ID is NOT yet established. Bench work, in order:

- [ ] **Resolve which peripheral ID the Axon->Omnetics probe presents as.** No
      vendored code maps the Axon adapter to an ID/type. The `200` (IntanRHD2132)
      in the config is a candidate, not a fact. Two hypotheses: (1) the Omnetics
      probe digitizes via an on-board RHD2132 = ID 200; (2) the Axon front-end is
      a distinct peripheral with a different ID that may only enumerate once the
      adapter+probe are attached/powered. Re-run `info` with vs. without the
      adapter attached and diff the peripheral list; confirm with Science.
- [ ] Set `peripheral_id` in the config to the resolved ID.
- [ ] Confirm the target accepts sample_rate_hz / bit_width (real ADC limits;
      the virtual peripheral accepted anything).
- [ ] Replace the electrode_id/reference_id map with THIS adapter's documented
      pinout (carried-over values may silently record the wrong electrodes).
      Needs the adapter datasheet/channel map from Science.
- [ ] `synapsectl stop` -> `synapsectl start config/axon-omnetics-32ch.json` ->
      `info` to confirm binding -> stream the broadband Tap -> `stop`.
- [ ] Set the analog filter cutoffs to the intended acquisition band.

Decided direction (pending user confirmation):

- Proceed on the **host-side fallback**: MCU (STM32 first) as a host USB/serial
  device → host C++ adapter → common timestamped record → host fusion; commands
  via a Synapse **consumer Tap** → C++ App. No FPGA/Radiant needed for this.
- Defer native FPGA peripheral until the device/licensing/board questions and
  the external-MCU question are answered. Do not write MCU USB-peripheral
  firmware or install Radiant until then.

### 2026-08-24 — Peripheral-source plan (supersedes conflicting bullets above)

Full repo audit and detailed plan for (1) the physical Axon Omnetics 32-ch source and (2) the STM32 NUCLEO-U5A5ZJ-Q peripheral hub: see `docs/peripheral-sources-plan.md`.

Key updates to the "Decided direction" and open questions above: a Radiant floating license is now on order (the "do not install Radiant" bullet is superseded); the physical adapter is a config-only path per Science's adapter datasheet (no custom gateware/driver); custom-peripheral gateware targets the separate Via devkit (LIFCL-17-9SG72C), not the SciFi's internal fabric; the Radiant free tier covers CrossLink-NX including bitstream, so licensing no longer gates the FPGA path — Via devkit hardware does. The config's electrode map is now validated against the datasheet pinout (channel k = Omnetics pin k+3), but the config still binds virtual peripheral 1000 and a 10 kHz rate that is not in the documented physical-rate set.

### Bench status 2026-08-24 (afternoon) — adapter not enumerating; Track A blocked on hardware

The Axon Omnetics adapter no longer enumerates: device screen shows 0 peripherals, `info` no longer lists `IntanRHD2132` (ID 200), and an unplug/replug produces zero kernel or `scifi-server` log activity. Software is exonerated (see MISTAKES.md entry and `scripts/device-diag/README.md` for the evidence and the diagnostic tooling). Escalated to Science; awaiting reply.

- [ ] Try a different USB-C cable and the other SciFi port; firm reseat; `synapsectl settings get` dump.
- [ ] Science: adapter failure/RMA; also ask which product the adapter is (SciNeticsRHD, USB PID 0x000C, Intan front end vs NeRV512U, PID 0x0001, NYX1-512) and which parameter table governs it.
- [ ] After recovery: cleanup on device (`dpkg -r nml-diag nml-quarantine`, optionally `scifi-axon-test-source`).
- [ ] Resume Track A at step A0 of `docs/peripheral-sources-plan.md`.

Bench facts learned during the investigation: `peripheral_id` 1–2 are command-range aliases ("first broadband source"), never port numbers; the device clock is unreliable across boots (jumped backward a day; runs ~1 month behind — relevant to all future provenance/synchronization work); `axon_interface: kUSB` with the peripheral-facing USB handled by `at_usb0/1` bridge devices, invisible to the Linux USB host stack; `TIME_SOURCE_SAMPLE_COUNTER` confirmed as the device time source; the deploy channel accepts any .deb with `Section: synapse-peripherals` and can carry diagnostic payloads (mailbox pattern, `scripts/device-diag/`).

## Initial Definition of Done

The first repository milestone is complete when:

1. The SciFi-2 and Axon adapter are reproducibly discoverable.
2. An official/example Synapse App can be built and deployed.
3. A small custom C++ Synapse App can publish a Tap.
4. A C++ host program can consume that Tap.
5. One independent wireless test source can stream into the same host program.
6. Both streams are logged with explicit source and host timestamps.
7. Synchronization offset/drift diagnostics are recorded alongside the data.