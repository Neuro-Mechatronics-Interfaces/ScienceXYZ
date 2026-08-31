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

```bash
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

```text
kBroadbandSource -> kApplication -> Tap
```

The initial App should demonstrate:

- receiving BroadbandFrame data;
- preserving source timestamps and sequence numbers;
- detecting/reporting dropped frames;
- publishing a simple derived or diagnostic Tap;
- clean start/stop behavior.

Only after that path is reliable should filtering, spike detection, decoding, or inference be added.

Do not add electrical or optical stimulation behavior unless explicitly requested. Recording and stimulation should remain separate concerns during initial infrastructure development.

## NML Synapse Bridge — Protocol Investigation (2026-08-24)

First-pass investigation of Science's public custom-peripheral interface is complete. See `docs/axon-peripheral-protocol.md` and `docs/feasibility-mcu-vs-fpga.md`.

Established (proven from vendored code):

- A custom Axon peripheral = FPGA gateware on the SciFi's internal Lattice fabric (`via-devkit`, Radiant 2024.2) + an ARM64 driver `.so` dlopened by `scifi-server`. It talks AXI4-Stream to an encrypted SDK transport; it does not speak USB.
- A custom **record source** (`kBroadbandSource`) peripheral is fully supported and is the known-working example.
- Frame contract, opcodes, peripheral-ID window (`0xF001..0xFFFE`), and the `synapsectl peripherals build/deploy/gateware` toolchain are documented.

Open, blocking, needs Science or a hardware experiment:

- [ ] Is there any supported path for an external MCU on the peripheral-facing USB/Axon port to appear as a Synapse peripheral? (gates STM32/nRF52 as a *native* peripheral)
- [ ] Can a user-authored peripheral register as an electrical/optical stimulation **sink**, and with what SDK base class?
- [ ] Is `OpticalStimFrame.timestamp_ns` honoured as a future scheduled execution time, or only a stamp?
- [ ] Exact Lattice FPGA device string + whether Radiant Free suffices.
- [ ] Can a `via-devkit` (or equivalent Axon-transport board) be obtained?
- [ ] How are SciFi GPIO sync edges surfaced in Synapse streams/timestamps?

Bench facts (2026-08-24 `info`, device `SFI2-0-260534`, Synapse 2.4.1): peripherals are IntanRHD2132 (ID 200, kBroadbandSource — the real 32-ch probe ADC path), SciFi Virtual Recording Peripheral (ID 1000), and VirtualOpticalStimPeripheral (ID 1001, kOpticalStimulation). The bare Axon Omnetics adapter does not enumerate on its own.

### Next: record from the real Axon->Omnetics 32-channel probe

We write configuration, not ADC/driver code. `config/axon-omnetics-32ch.json` clones the working virtual graph and parses cleanly as a DeviceConfiguration proto. But the target peripheral ID is NOT yet established. Bench work, in order:

- [ ] **Resolve which peripheral ID the Axon->Omnetics probe presents as.** No vendored code maps the Axon adapter to an ID/type. The `200` (IntanRHD2132) in the config is a candidate, not a fact. Two hypotheses: (1) the Omnetics probe digitizes via an on-board RHD2132 = ID 200; (2) the Axon front-end is a distinct peripheral with a different ID that may only enumerate once the adapter+probe are attached/powered. Re-run `info` with vs. without the adapter attached and diff the peripheral list; confirm with Science.
- [ ] Set `peripheral_id` in the config to the resolved ID.
- [ ] Confirm the target accepts sample_rate_hz / bit_width (real ADC limits; the virtual peripheral accepted anything).
- [ ] Replace the electrode_id/reference_id map with THIS adapter's documented pinout (carried-over values may silently record the wrong electrodes). Needs the adapter datasheet/channel map from Science.
- [ ] `synapsectl stop` -> `synapsectl start config/axon-omnetics-32ch.json` -> `info` to confirm binding -> stream the broadband Tap -> `stop`.
- [ ] Set the analog filter cutoffs to the intended acquisition band.

Decided direction (pending user confirmation):

- Proceed on the **host-side fallback**: MCU (STM32 first) as a host USB/serial device → host C++ adapter → common timestamped record → host fusion; commands via a Synapse **consumer Tap** → C++ App. No FPGA/Radiant needed for this.
- Defer native FPGA peripheral until the device/licensing/board questions and the external-MCU question are answered. Do not write MCU USB-peripheral firmware or install Radiant until then.

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

### 2026-08-26 — RHD2132 SPI-master gateware track (plan only)

Science confirmed the probe front end is a physical **Intan RHD2132** (32-ch SPI ADC/amp), to be driven by the Lattice FPGA on the SciFi-2 headstage. Radiant 2026.1 + node-locked license are installed. **Update 2026-08-26 (evening): the adapter enumerates again.** `info` now lists `IntanRHD2132` (ID 200, kBroadbandSource, Intan Technologies) with the virtual peripherals (1000, 1001). The 2026-08-24 non-enumeration was a transient link-level issue, now cleared — ID 200 was never destroyed by our deploys (see `MISTAKES.md`, software exonerated). **The stock config-only recording path is therefore available now** (bind `peripheral_id: 200`; no custom gateware/Radiant) and is the fast path to actual probe data. The RHD2132 SPI-master gateware effort below is now optional/long-term — a track to own our own peripheral (`0xF200`) for custom on-device processing — not the only way to record.

**DONE 2026-08-26: first real recording from the physical RHD2132 (ID 200).** `config/axon-omnetics-32ch-broadband.json` (broadband-source-only, no app node) streams cleanly: 5 s @ 20 kHz / 16-bit / 32 ch, 100,305 frames / 3.21M samples, 0.00% loss. Captured to HDF5 via 
```bash
synapsectl read <config> --duration N --output <dir>`
```
HDF5 confirms `lsb_uv = 0.195`, `sample_rate_hz = 20000`, and carries the provenance fields AGENTS.md wants: `sequence_number` (monotonic +1, no gaps), `timestamp_ns` (source), `unix_timestamp_ns` (host). Device log confirmed `clkmc 80000000 Hz`, `period 4000 cycles`. 
The blocker was a config bug, not hardware: the real RHD2132 driver validates `electrode_id` to 0-31 and rejected the virtual-peripheral map (122/126/...); an identity 0-31 map fixed it. On Windows, run `synapsectl` with `PYTHONUTF8=1` (its checkmark output crashes under cp1252). See @MISTAKES.md.

Follow-ups before this is a *correct* recording (not just a working stream):
- [ ] Replace the identity electrode/reference map with the adapter's documented Omnetics->RHD2132 channel map (needs the pinout from Science). Identity is a smoke-test placeholder; electrode ids are provenance labels.
- [ ] Understand the BroadbandFrame timestamp granularity: per-frame `timestamp_ns` dt ≈ 2916 ns, not 50000 ns (1 sample @ 20 kHz). Determine whether a frame carries a multi-sample block and how the device sample-counter maps to ns — matters for synchronization/provenance.
- [ ] Decide whether to run the `kBroadbandSource -> kApplication` graph (`config/axon-omnetics-32ch.json`, app `synapse-example-app` v0.1.0 is installed) once the source path is trusted.

The current `m053m716/omnetics-32ch-adapter` scaffold is a renamed copy of the synthetic `axon_test_source` (fake data, no SPI). Full implementation-grounded plan — RHD command/register model, 80 MHz SPI timing, the 2-transaction MISO pipeline gotcha, driver electrical contract (0.195 µV/LSB, 192 V/V, 20 kHz), tests, build/license, and blocking inputs — is in [`docs/rhd2132-gateware-plan.md`](docs/rhd2132-gateware-plan.md). No code written yet. Reference bodies: the RE'd SDK `.so`, the SDK's `axon_test_source` driver, and `third_party/intan/firmware/rhd/xem7310_common/main.v`.

Blocking before hardware bring-up: (1) adapter must electrically enumerate again; (2) SciFi-2 → RHD2132 SPI pinout from Science; (3) confirm a user peripheral may own the RHD SPI pins on the internal fabric (else Via/devkit path). Sim-only RTL + driver + cocotb work can proceed against an SPI-slave model in the meantime.

### 2026-08-26 — broadband-mode-switch App implemented (offline; bench-verify pending)

New on-device App `apps/broadband-mode-switch/` implementing all five stages of `PLAN.md`: live real↔synthetic source toggle, labeled per-class ring buffer, Kaifosh-2025 multivariate MPF features (STFT → CSD → band-average → Hermitian matrix-log via a hand-rolled cyclic-Jacobi eigensolver, no `eigen3`), and a hand-rolled 2-hidden-layer MLP (backprop + SGD, feature z-scoring, dropout). Three `ListValue` consumer taps (`set_source_mode`, `set_capture`, `fit_mlp`), two producer taps (`broadband_out` `BroadbandFrame`, `class_out` `Tensor`), plus `config/rhd2132_mode_switch.json` (binds ID 200) and four client scripts.

Status: the four SDK-independent modules compile clean under 
```bash
g++ -std=c++20 -Wall -Wextra -Wshadow
``` 
and pass an offline smoke test; `mode_switch_app.cpp` mirrors the proven example-app SDK usage but needs the Docker build to compile (SDK headers live in the builder image). See `PLAN.md` for the vs-plan deltas.

Next (bench, staged per PLAN.md §7):
- [ ] `synapsectl apps build apps/broadband-mode-switch` — confirm it compiles; resolves open-question #1 (`create_tap<synapse::BroadbandFrame>` allowed?). A `Tensor` fallback for `broadband_out` is documented at the call site.
- [ ] Deploy + start on the ID-200 chain; confirm `broadband_out` streams (Stage 0).
- [ ] Toggle real↔synthetic via `client/set_source_mode.py` (Stage 1).
- [ ] Capture labeled windows; confirm per-class counts (Stage 2).
- [ ] Validate MPF feature dim + numerical sanity vs a NumPy recomputation (Stage 3).
- [ ] `fit_mlp` + `listen_class.py`; confirm separable synthetic classes learn (Stage 4).
- [ ] Measure on-device `fit` cost; move training to a worker thread if it stalls the `main()` loop.

### 2026-08-31 - broadband-mode-switch bounded producer validation

The deployed app was verified read-only with `synapsectl -u 192.168.100.157
info`: device `SFI2-0-260534` is running Synapse 2.4.1, firmware
3164583911, with `IntanRHD2132` peripheral ID 200 and a 20 kHz, 32-channel
source. The app was left in sampling mode with capture disabled.

The bounded `client/broadband_probe.py --duration 5` checks produced:

- Synthetic: 7,614 frames in 5.089 s (1,496.2 Hz), no missing or reordered
  sequences, no timestamp regressions, fixed 50,000 ns timestamp delta, 20 kHz,
  32 channels, empty `channel_ranges` (legacy electrode layout), 0 parse
  errors.
- Sampling: 6,222 frames in 5.010 s (1,242.0 Hz), 118,199 missing sequence
  numbers, no reordered sequences, no timestamp regressions, timestamp delta
  20,104..18,159,427 ns (mean 1,001,091.9 ns), 20 kHz, 32 channels,
  `ELECTRODE:32`, 0 parse errors.

The app log also recorded the synthetic -> sampling transitions, a sampling
dropped-frame warning (`154340`), and the final capture-off command. Existing
fit evidence includes `capture ... count=26 total=50` and final loss 0.0037 /
accuracy 1.000; a fresh per-epoch fit-progress line was not present in the
available app log. The complete GUI/socket workflow remains open under T-17.

## Initial Definition of Done

The first repository milestone is complete when:

1. The SciFi-2 and Axon adapter are reproducibly discoverable.
2. An official/example Synapse App can be built and deployed.
3. A small custom C++ Synapse App can publish a Tap.
4. A C++ host program can consume that Tap.
5. One independent wireless test source can stream into the same host program.
6. Both streams are logged with explicit source and host timestamps.
7. Synchronization offset/drift diagnostics are recorded alongside the data.
