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

### 2026-08-31 - T-10 host controller complete

The replaceable-transport Python controller now owns the control/state/result
Tap lifecycle, validates decoded protocol envelopes, correlates terminal
results by request id and command, serializes sends, and publishes immutable
state replacements. Malformed protobufs are surfaced as `last_error`; Tap
transport failures publish a disconnected state and wake pending commands.
`connect_with_backoff` and `reconnect_with_backoff` use bounded exponential
delays. The fake transport covers connect/reconnect, malformed state,
rejection, timeout, immutable replacement, and transport-loss behavior.

Verification from the repository CPython 3.13 environment:

```text
PYTHONPATH=apps/broadband-mode-switch/client .venv/Scripts/python.exe -m unittest discover -s apps/broadband-mode-switch/client/tests -v
Ran 11 tests ... OK
```

T-11 (loopback NDJSON service hardening and multi-client coverage) is next.

### 2026-08-31 - T-11 loopback service complete

The asyncio NDJSON service now serializes all mutating requests through one
controller lock, keeps client request correlation isolated by session, and
delivers at most the newest queued state snapshot to each subscriber. Socket
responses distinguish malformed input, unknown commands, timeouts, controller
disconnects, and device rejections. Subscription state changes only after the
controller acknowledges the request, and disconnect cleanup cancels pending
state delivery.

Service tests cover fragmented/coalesced requests, malformed JSON and version
rejection, two clients using the same request id, and a deliberately slow
subscriber. The full client suite passes with 13 tests under CPython 3.13.

T-12 (dependency-light socket client and calibration-prompter example) is next.

### 2026-08-31 - T-12 socket client and calibration prompter complete

Added `client/broadband_mode_switch/client.py`, a standard-library-only
blocking NDJSON client with request correlation, accepted-fit progress
handling, queued state events, timeout/error reporting, and command helpers.
Added `client/calibration_prompter.py`; its injectable calibration routine
queries state, validates available targets, atomically selects each target with
capture off, enables capture only for the prompted window, and disables it in
cleanup before another target can be selected. Offline tests exercise
fragmented/interleaved responses, target ordering, and cleanup after a prompt
failure.

T-13 (read-only PySide6 dashboard and Qt smoke test) is next.

### 2026-08-31 - T-13 dashboard view complete

Extracted `create_dashboard_window()` from the previous monolithic GUI entry
point. The dashboard applies immutable `AppState` replacements on the Qt
thread, renders pipeline/source state, active target, all label
count/capacity progress bars, and model phase/epoch/loss/accuracy/duration
with stale indication. Synapse connection and command work remains in worker
threads. A headless Qt smoke test verifies the rendered state without hardware.

T-14 (safe GUI targeting, fitting, and flush controls) is next.

### 2026-08-31 - T-14 safe GUI controls complete

GUI mutation controls now share a pending-command gate: collection/label
selectors, capture, Apply, Fit, and all flush buttons are disabled until the
correlated terminal result or an error. Non-terminal fit progress leaves the
gate closed. Apply always sends the atomic `prepare_capture` command; state
fields are updated only by acknowledged snapshots. Destructive flush scopes
are confirmed, device rejection/timeout text is shown inline, and unknown
capacities are rendered disabled instead of as a fabricated percentage.

Headless Qt tests cover snapshot rendering, unknown capacity, pending/double
submission suppression, rejection/timeout re-enable, and label/collection/all
flush submission. T-15 (fake-device end-to-end and robustness tests) is next.

### 2026-08-31 - T-15 fake-device integration complete

Added an in-process `FakeDevice` contract fixture and end-to-end tests that
run the fake transport through `BroadbandController`, `ControlService`, and
the socket client. The tests cover atomic target changes and rejection while
capturing, all flush scopes, fit progress with new-generation data and stale
model state, completed request-id replay without reapplying a command, two
socket clients, and reconnect while a fit is active. The controller now keeps
a bounded terminal-result cache for duplicate request IDs.

The complete hardware-free suite passes with 24 tests when the offscreen Qt
tests run, or 24 tests with the Qt cases skipped in environments without the
optional PySide6 dependency. T-16 (packaging, launch commands, documentation,
and architecture diagram refresh) is next.

### 2026-08-31 - T-16 packaging and operator documentation complete

Added `client/pyproject.toml` with the CPython 3.13 package metadata,
dependencies, and console entry points for the service, GUI, calibration
prompter, and no-hardware fake demo. Launch scripts now expose import-safe
`main()` functions. README documentation covers editable installation,
hardware-free verification, service binding safety, protocol usage, GUI state
semantics, and the calibration ordering guarantee.

Updated the version-controlled GraphViz architecture source and regenerated
its bounded parent/inner SVG artifacts to show the packaged NDJSON client and
fake-device contract fixture. The protocol document references the refreshed
diagram.

### 2026-08-31 - T-17 partial real-device bench evidence

The installed tool was run as
`\.venv\Scripts\synapsectl.exe -u 192.168.100.157 info` and reported device
`SFI2-0-260534`, Synapse 2.4.1, firmware 3164583911, but only virtual
peripherals 1000/1001. The configured broadband source reported `Connected
to: Unknown`; IntanRHD2132 ID 200 was absent. `taps list` nevertheless showed
the deployed typed `control`, `state`, and `command_result` taps plus
`broadband_out` and `class_out`.

Using the updated host service on loopback port 18765 and the packaged socket
client, `get_state_snapshot` returned state version 7214 with
`pipeline=disconnected`, `source_mode=sampling`, `capture_enabled=false`,
collection generation 54 and counts 24/30/0/0/0. A fresh socket `fit(3)`
completed at state version 7220 with loss 0.076810 and accuracy 1.000. The
socket delivered accepted progress for epochs 1/2/3: losses 1.303458,
0.182930, and 0.076810, with accuracies 0.666667, 0.962963, and 1.000000.
The host service was stopped afterward; the device remained sampling with
capture disabled.

This validates the live controller/service/socket and fresh fit-progress path,
but T-17 remains open because the missing Intan peripheral blocks connected
sampling/synthetic stream validation, classification continuity, full GUI
reconnect, and calibration-prompter collection switching on the real device.
The next bench action is to restore or re-enumerate the adapter, then rerun the
documented workflow with a fresh deployed app and record the missing stream
checks.

### 2026-08-31 - T-17 real-device bench validation completed with sampling blocker

The adapter re-enumerated before the fresh run. `\.venv\\Scripts\\synapsectl.exe
-u 192.168.100.157 info` reported device `SFI2-0-260534`, Synapse 2.4.1,
firmware 3164583911, and `IntanRHD2132` as peripheral ID 200. A clean
`stop`/`start apps/broadband-mode-switch/config/rhd2132_mode_switch.json`
bound the broadband source to `IntanRHD2132 (id: 200)` and started the app with
the typed control/state/result taps.

Fresh bounded producer probes (`broadband_probe.py --duration 5`) reported:

- Synthetic: 7,768 frames in 5.001 s (1,553.2 Hz), no missing or reordered
  sequences, no timestamp regressions, fixed 50,000 ns deltas, 20 kHz, 32
  channels, empty legacy channel ranges, and 0 parse errors.
- Sampling: 5,156 frames in 5.021 s (1,026.9 Hz), 97,945 missing sequences,
  no reordered sequences, 0 timestamp regressions, timestamp deltas
  20,313..18,834,792 ns (mean 1,001,349.5 ns), 20 kHz, 32 channels,
  `ELECTRODE:32`, and 0 parse errors. The application log also recorded a
  larger dropped-frame interval while the source was sampling.

On loopback port 18765, the packaged socket client verified successful label,
collection, and all flush scopes; atomic target preparation with capture off;
capture counts 6/6 for labels 0/1; and fit progress epochs 1/2/3. The terminal
fit reported loss 0.553772 and accuracy 0.916667. A later capture advanced the
collection generation 12 -> 26 and the state correctly marked the still-ready
model `stale=true`. `class_out` delivered 12 valid normalized five-class
softmax tensors in 3 s.

The offscreen PySide6 dashboard connected, disconnected, and reconnected to
the real device, reaching `ready` after both connections. The actual
calibration prompter safely switched targets collection 0,label 2 ->
collection 0,label 3, with capture disabled between targets and after cleanup.
The deployed app config contains one collection (0); collection 1 was
rejected as unavailable before prompting, so cross-collection switching is a
configuration limitation rather than an untested success.

T-17 is complete for the available deployed configuration, with the real
sampling loss/backpressure behavior explicitly evidenced above. Do not treat
the sampling probe as loss-free acquisition until the source-drop bottleneck
is isolated; synthetic/control-plane/classification checks passed. The device
was returned to sampling with capture disabled and the host service stopped.

## Initial Definition of Done

The first repository milestone is complete when:

1. The SciFi-2 and Axon adapter are reproducibly discoverable.
2. An official/example Synapse App can be built and deployed.
3. A small custom C++ Synapse App can publish a Tap.
4. A C++ host program can consume that Tap.
5. One independent wireless test source can stream into the same host program.
6. Both streams are logged with explicit source and host timestamps.
7. Synchronization offset/drift diagnostics are recorded alongside the data.
