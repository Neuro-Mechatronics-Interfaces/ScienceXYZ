# Mistakes

This file records concrete mistakes encountered while working in this repository. Recurring failure modes may be promoted into durable rules in `AGENTS.md`.

## Entry Template

### YYYY-MM-DD — Short description

**Attempt:** What was being attempted.

**Failure:** What went wrong.

**Cause:** The actual cause, once established.

**Correction:** How it was fixed.

**Candidate rule:** A general safeguard worth considering if this failure recurs.

### 2026-08-24 — Assumed the Axon "custom peripheral" is a USB-attached MCU

**Attempt:** Planned the NML Synapse Bridge as an external MCU (STM32/nRF52) that enumerates on the SciFi's peripheral-facing USB port by implementing an "Axon USB peripheral protocol," and began framing the work as reverse-engineering that USB wire format.

**Failure:** No such public USB peripheral protocol exists to implement. Time budgeted for "USB descriptor / endpoint / handshake reverse engineering" would have been spent chasing an interface that is not exposed to an external device.

**Cause:** Science's public reference (`vendor/axon-peripheral-example`) implements a custom peripheral as **FPGA gateware inside the SciFi's own Lattice fabric** (target `via-devkit`, Radiant 2024.2) plus an **ARM64 driver `.so`** that `scifi-server` `dlopen`s on the SciFi. The peripheral exchanges 32-bit AXI4-Stream frames with an encrypted SDK transport and never speaks USB; the device↔headstage physical link is an IR/optical serdes, and the host↔SciFi link is FTDI FT60x. The reference exposes only a *record source* plugin base (`RecordPluginWithLimits`); no external-MCU path and no stim-sink plugin base are present. (Evidence: `via_top.sv`, `axon_test_source_peripheral.{sv,cpp,h}`, `Dockerfiles/gateware.Dockerfile`, `peripheral.yaml`.)

**Correction:** Documented the actual architecture in `docs/axon-peripheral-protocol.md` with proven/inferred/unknown tags, and recorded a feasibility decision in `docs/feasibility-mcu-vs-fpga.md`: proceed on the proven host-side fallback (MCU as a host USB/serial device feeding host fusion + a Synapse consumer Tap), treat the Lattice FPGA as the only proven *native*-peripheral path (gated on device identification + Radiant licensing + obtaining a via-devkit), and do not write MCU USB-peripheral firmware until Science confirms whether any external-MCU-as-SciFi-peripheral path exists.

**Candidate rule:** Before implementing against an assumed hardware/wire interface, confirm the interface boundary from the vendored reference implementation. Do not treat a "custom peripheral" as an external USB device until the reference shows an external-device enumeration path; here the reference peripheral lives on the SciFi's internal FPGA fabric.

### 2026-08-24 — Asserted the Axon→Omnetics probe maps to peripheral ID 200

**Attempt:** Given `synapsectl info` listing `IntanRHD2132 (ID 200, kBroadbandSource)` as the only real 32-channel broadband source, stated that ID 200 is "the Axon→Omnetics probe path" and wrote a recording config (`config/axon-omnetics-32ch.json`) binding to `peripheral_id: 200`, plus doc text asserting the RHD2132 is the adapter's ADC.

**Failure:** Presented an inference as fact. The user (correctly) noted the RHD2132 is itself the ADC/SPI chip, so it is plausible the Axon front-end sits *in place of* an Intan chip and would enumerate as a different peripheral — meaning ID 200 could be unrelated to the Omnetics probe path.

**Cause:** No vendored code maps the "Axon Omnetics adapter" to any peripheral ID or type (grep across `vendor/` finds no such mapping). ID 200 was the only real 32-ch `kBroadbandSource` in one `info` reading, and its name (`IntanRHD2132`, a real Intan part) was over-read as "the probe path" without evidence that the Omnetics connector routes through that chip.

**Correction:** Reframed the ID as a *candidate to test, not a fact* in the config README, `docs/axon-peripheral-protocol.md` §12, and `TODO.md`, and added a bench step to resolve it (diff `info` with vs. without the adapter attached; confirm with Science) before binding any recording config.

**Candidate rule:** Do not bind configuration or documentation to a specific peripheral ID from a single `info` reading unless the ID→device mapping is confirmed by attaching/ removing the device or by vendor documentation. A peripheral's *name* is not proof of what physical signal path feeds it.

### 2026-08-24 — Attributed the missing IntanRHD2132 (ID 200) to the peripheral-example deploy

**Attempt:** After deploying the `scifi-axon-test-source` driver .deb, `synapsectl info` no longer listed `IntanRHD2132` (ID 200) and the device screen showed 0 peripherals. Concluded the deploy had likely broken the stock driver — the .deb installs `libscifi-peripheral-sdk.so{,.0,.0.2.0}` into system `/usr/lib`, so a symlink-clobber over a firmware copy was hypothesized — and recovery planning (symlink repair, driver reinstall) began on that basis.

**Failure:** The hypothesis was wrong. On-device evidence showed no clobber was possible: the firmware never ships `libscifi-peripheral-sdk` (the .deb's copy is the only one), the Intan driver is not a plugin file (`/usr/lib/scifi/plugins/` held only `axon_test_source.so`; no Intan package exists in `dpkg -l`), and the boot journal shows the plugin loading cleanly (`ABI v3, ID 0xf001`) followed by `PeripheralRegistry initialized successfully`. Unplugging/replugging the adapter produced zero kernel, `usbd`, or `scifi-server` log activity — detection fails at the electrical/link level (adapter, cable, or port), not in software.

**Cause:** Post-hoc reasoning from coincidence (deploy happened; peripheral vanished) plus limited visibility: the `scifi-sftp` account is jailed to data directories, the `GET_LOGS` RPC returns only curated `scifi-server` entries, and the device clock jumps across boots so timestamp-sorted `journalctl` tails showed the wrong boot. Evidence had to be gathered by shipping read-only diagnostic .debs over the DeployApp channel with a data-jail mailbox (see `scripts/device-diag/`).

**Correction:** Software exonerated; escalated to Science as a hardware/link failure with the journal evidence. Diagnostic tooling and the device facts learned along the way are preserved in `scripts/device-diag/README.md`. Also recorded there: IDs 1–2 in `BroadbandSourceConfig.peripheral_id` are command-range aliases ("first broadband source"), not concrete IDs — a port number is never a peripheral ID.

**Candidate rule:** Correlation with a recent software change is a hypothesis, not a diagnosis: before planning recovery from an assumed software regression on a device, capture the device's actual state through a read-only channel (package list, plugin dir, boot-scoped journal). On devices with unstable clocks, scope journal queries with `journalctl -b`; a curated log RPC returning nothing is not evidence of absence.

### 2026-08-26 — Carried-over virtual electrode map rejected by the real RHD2132

**Attempt:** First real recording from the physical Axon Omnetics 32-ch probe (Intan RHD2132, `peripheral_id` 200) reused the electrode/reference map copied from the virtual peripheral example (`id` 1000): `electrode_id` values like 122, 126, 116, and `reference_id` 512/513. `synapsectl start` was expected to stream.

**Failure:** `start` failed with a misleading top-level "Failed to start device: connection error" from the CLI. The device logs showed the true cause: `IntanRhd2132: Invalid electrode ID: 32 (max 31)` -> `Failed to configure channels` -> `BroadbandNode: Failed to start peripheral recording`.

**Cause:** The virtual peripheral (id 1000) accepts arbitrary electrode/reference ids; the **real RHD2132 driver validates `electrode_id` to its 32 physical amplifier channels, 0-31**, and hard-rejects anything above. The carried-over map was never a valid physical channel map (this exact risk was pre-flagged in `TODO.md`). The CLI's "connection error" is a downstream symptom, not the cause -- the device log is authoritative.

**Correction:** Set an identity map (`electrode_id` = channel `id`, 0..31; `reference_id` 0) in `config/axon-omnetics-32ch-broadband.json`. Recording then streamed cleanly: 5 s @ 20 kHz, 100,305 frames / 3,209,760 samples, 0.00% loss; HDF5 attrs confirm `lsb_uv = 0.195`, `sample_rate_hz = 20000`. Two device-side facts also confirmed in the logs: `clkmc 80000000 Hz`, `period 4000 cycles` at 20 kHz.

**Candidate rule:** Do not carry an electrode/reference map from a virtual/simulator peripheral to a physical one; physical front ends validate ids against real channel counts. When a `synapsectl` action fails with a generic CLI-level error, read `synapsectl ... logs` for the device-side cause before diagnosing. (Also: `synapsectl` on Windows crashes printing a U+2713 checkmark under the console's cp1252 codec -- set `PYTHONUTF8=1` for its commands.)

### 2026-08-31 — Imported a generated protobuf enum from the wrong module

**Attempt:** Added the read-only `broadband_out` Python probe and imported both
`BroadbandFrame` and `ChannelType` from `synapse.api.datatype_pb2`.

**Failure:** The CPython 3.13 test run failed during module import because the
installed generated bindings expose the imported `ChannelType` enum through
`synapse.api.channel_pb2`, not `datatype_pb2`.

**Cause:** Protobuf Python generation keeps imported declarations in their
source module; the `datatype.proto` reference does not re-export the enum.

**Correction:** Imported `BroadbandFrame` from `datatype_pb2` and `ChannelType`
from `channel_pb2`, then added offline parser tests. The full client suite
passed.

**Candidate rule:** When adding a generated-protobuf client, inspect the
installed binding modules for imported message/enum ownership before writing
imports; compile/import the smallest consumer immediately.

### 2026-08-26 — On-device MLP would not learn separable MPF classes (feature scale)

**Attempt:** Offline smoke test of the `stateful_decode_and_sync` MLP: two synthetic classes, one amplitude-scaled 4x (an obvious, large feature difference), trained with the config default `mlp_lr` (~0.01-0.02) on the raw MPF feature vectors.

**Failure:** Training accuracy stuck at ~0.5 and loss pinned exactly at ln(2)=0.693 (uniform softmax); the network never moved. First read as an MLP/backprop bug.

**Cause:** Not a code bug. The MLP is correct (a 2-feature toy problem trained to 100%). Raw MPF features are the upper triangle of a Hermitian matrix-log of a
cross-spectral density: values span orders of magnitude and reach |x|~10. At
lr>=~0.01 the first-layer gradients explode and the softmax collapses to uniform; at lr~=0.001 the same data trains to 95-100%. The default lr was simply too large for the unnormalized feature scale.

**Correction:** Added per-feature standardization (z-scoring) to the MLP: fit mean/std over the captured training set, apply identically at inference. With standardized inputs the default lr converges to 100% on the smoke test. Documented in the app README and PLAN.md.

**Candidate rule:** For a hand-rolled on-device classifier, standardize features before SGD rather than hand-tuning the learning rate to the feature scale; a loss frozen exactly at ln(num_classes) with no weight movement is diverging (exploding gradients), not a stuck optimizer -- check input magnitude and lr before suspecting backprop.

### 2026-08-31 — Omitted a helper while adding the bounded collection store

**Attempt:** Compiled the new SDK-independent collection-store tests with strict warnings after adding generation-overflow checks.

**Failure:** The compiler rejected references to `can_advance_generation()` because the helper had been called but not defined.

**Cause:** The generation guard was added in the public mutation methods without completing the corresponding private helper in the same header-only implementation.

**Correction:** Added the helper, then reran the strict direct g++ build and the WSL CMake/ CTest workflow; all seven tests passed.

**Candidate rule:** After adding a header-only API guard, run the smallest strict compile before continuing documentation or integration work.

### 2026-08-31 — T-5 protobuf test integration exposed generated-name and target mistakes

**Attempt:** Added the typed GUI protocol schema and put its serialization tests beside the existing collection-store test in one CMake executable.

**Failure:** The first generated-code compile used nested enum names that this `protoc` version emits as namespace-level names, and the combined test target linked two `main` functions. A direct WSL test configuration also initially used CMake's module Protobuf target against the static vcpkg library without its Abseil dependencies.

**Cause:** Generated C++ enum naming is determined by the protobuf generator, not by the source enum's visual nesting. The existing test already owns the executable entry point. The module-mode imported target did not carry the static package's transitive Abseil link interface in that ad-hoc configuration.

**Correction:** Used the generated `Flush_Scope_*` names, split protocol tests into their own executable, and selected the vcpkg Protobuf config package so its dependency targets are linked. The WSL build then passed both CTest targets.

**Candidate rule:** Compile a small generated-protobuf consumer immediately after changing a local schema; keep independently owned test programs in separate executables and use the package-manager config target for static dependency graphs.

### 2026-08-31 — Fit failure status was default-initialized as unsuccessful

**Attempt:** Added per-epoch MLP progress publication to the existing synchronous fit path.

**Failure:** The fit path's local `TransitionResult failure` started with its default `success=false`, so the terminal check classified every otherwise successful training pass as failed.

**Cause:** The result type intentionally defaults to failure for safety, but the local variable represented an as-yet-unset error and was tested as though it represented a successful operation.

**Correction:** Initialized the local result with `control::success()` and added an MLP regression test covering successful progress and malformed data. The terminal fit check now also rejects non-finite metrics and reports `ERROR_MALFORMED`.

**Candidate rule:** Initialize deferred error/result variables explicitly to the success identity when the surrounding branch uses success as the no-error sentinel.

### 2026-08-31 — Assumed a successful Tap send meant the device received the command

**Attempt:** Used the legacy Python control scripts to switch source mode, capture labels, and start an MLP fit after deploying `stateful_decode_and_sync`.

**Failure:** The scripts printed successful sends, but the device emitted no corresponding app logs or `class_out` data. The `broadband_out` producer was healthy, so the apparent control-plane success was misleading.

**Cause:** Synapse consumer taps use ZeroMQ PUB/SUB. The client scripts sent immediately after connecting, during the PUB/SUB slow-joiner window; `Tap.send()` only confirmed a local socket send and could not confirm device receipt. The scripts' 0.1–1 ms connection settle delay was insufficient.

**Correction:** Added a 500 ms post-connect settle delay to the project command scripts before their first send. Device-side `app_logs` and `taps stream` should be monitored when validating command delivery; a local `send()` result is not an application acknowledgement.

**Candidate rule:** Treat PUB/SUB command delivery as asynchronous: allow the subscription handshake to settle and verify receipt through an application result/state/log stream rather than trusting the publisher's local send return value.

### 2026-08-31 — Windows Proactor socket cleanup raised an unhandled reset

**Attempt:** Closed a loopback NDJSON client during the reconnect integration test and caught `ConnectionError` around `StreamWriter.wait_closed()`.

**Failure:** Windows reported `OSError: [WinError 64]` from the Proactor transport after the peer reset, leaving an unhandled exception in the server callback even though the test passed.

**Cause:** The platform surfaced this peer-close path as a broad `OSError`, not the narrower exception covered by the cleanup handler.

**Correction:** Service shutdown and per-client cleanup now catch `OSError` around `wait_closed()`.

**Candidate rule:** Treat socket close cleanup as best-effort and catch platform-level `OSError` when awaiting peer shutdown.

### 2026-09-01 — Started an unconfirmed application rebuild

**Attempt:** After applying the raw-forwarding feature-work gate, started `synapsectl apps build --clean apps/stateful-decode-and-sync` to produce a package for bench validation.

**Failure:** The user intended to perform the build themselves and had not approved a build action. The long Docker dependency build was stopped before application compilation; no replacement package was deployed.

**Cause:** Interpreted authorization to implement the requested fix as authorization to run the separate, long-running build workflow.

**Correction:** Stopped the build on request. The code change remains in the working tree for the user's build.

**Candidate rule:** Ask for explicit confirmation before starting any build, even when the code change itself was requested.

### 2026-09-01 — App Docker context omitted the shared wireless proto

**Attempt:** The user ran `synapsectl apps build --clean apps/stateful-decode-and-sync` in WSL and supplied the build transcript.

**Failure:** Docker image creation succeeded, but CMake stopped at
`tests/CMakeLists.txt:43` with `protobuf_generate could not find any .proto files`.

**Cause:** `synapsectl apps build` supplies the app directory as Docker context;
the CMake test path referenced the repository-level `protocol/` directory,
which is outside that context and therefore absent in the image.

**Correction:** Added the wireless contract under the app's context-local
`proto/wireless/v1/` path and changed app/test CMake generation to use it. The
root `protocol/wireless/v1/` contract remains the repository-level reference.

**Candidate rule:** Treat app Docker contexts as self-contained; every proto,
script, and include needed by an app build must be inside the app context or be
explicitly copied into it.

### 2026-09-01 — Ran Axon gateware generation from the source subdirectory

**Attempt:** Ran `synapsectl peripherals gateware generate` from
`firmware/axon-virtual-sources/src/gateware/`, following the copied SDK README's
command examples.

**Failure:** The installed CLI searched for
`src/gateware/Dockerfiles/gateware.Dockerfile` and stopped before generation.

**Cause:** This CLI resolves the peripheral-plugin root from the current
directory, whereas the copied SDK README did not state that its commands must
be run from the directory containing `Dockerfiles/gateware.Dockerfile`.

**Correction:** Documented the required plugin-root working directory in the
parent-owned peripheral README and gateware README. No files were generated or
deployed.

**Candidate rule:** When a CLI wraps a project Dockerfile, state the required
working directory explicitly and perform a generation-only smoke check before
asking for a build or deployment.

### 2026-09-01 — Ran Axon gateware generation through the Windows CLI

**Attempt:** Ran the root-level `synapsectl peripherals gateware generate`
command from Windows PowerShell after correcting its working directory.

**Failure:** The CLI failed while starting its gateware Docker image with
`[WinError 2] The system cannot find the file specified`.

**Cause:** The installed Windows CLI cannot launch the Docker-backed Axon
gateware workflow in this environment. The user's working environment for this
toolchain is Ubuntu/WSL.

**Correction:** Updated the parent-owned peripheral instructions to require
Ubuntu/WSL with Docker for gateware commands. No generated files, package, or
device state changed.

**Candidate rule:** Use the established Linux/WSL toolchain for Docker-backed
peripheral gateware commands; reserve the Windows host for source editing and
non-gateware workflows unless a successful Windows smoke check exists.

### 2026-09-01 — Axon gateware CLI omitted its required named Docker context

**Attempt:** Ran `synapsectl peripherals gateware generate` from the correct
parent-owned peripheral root in Ubuntu/WSL with Docker available.

**Failure:** Docker failed before code generation, attempting to pull
`radiant_installer:latest` and reporting access denied.

**Cause:** `Dockerfiles/gateware.Dockerfile` mounts
`from=radiant_installer`, which requires a named BuildKit context containing the
Radiant installer ZIP. The installed CLI invoked `docker build ... .` without
`--build-context radiant_installer=...`, so Docker interpreted the context name
as an image name.

**Correction:** Marked the gateware generation workflow blocked and documented
the exact CLI/Docker mismatch. No generated gateware, package, or device state
changed.

**Candidate rule:** Before relying on a Dockerfile's named build context,
inspect the wrapper CLI's emitted `docker build` command and require it to pass
every named context explicitly.

### 2026-09-01 — Gateware image unpacked Radiant before installing `unzip`

**Attempt:** Built the parent-owned gateware image with the supplied
`radiant_installer` named context.

**Failure:** The first installer layer stopped with `/bin/sh: 1: unzip: not found`.

**Cause:** `Dockerfiles/gateware.Dockerfile` invoked `unzip` before its later
package-install layer provisioned build dependencies.

**Correction:** Added an explicit `apt-get install ... unzip` to the installer
layer before unpacking Radiant. No generated gateware, package, or device state
changed.

**Candidate rule:** Each Dockerfile layer must install every executable it uses;
do not rely on a later layer to provide an earlier layer's prerequisites.

### 2026-09-01 — Radiant installer lacked its XKB runtime dependency

**Attempt:** Rebuilt the parent-owned gateware image after provisioning
`unzip` in its installer layer.

**Failure:** The Radiant installer started but exited with
`libxkbcommon-x11.so.0: cannot open shared object file`.

**Cause:** The installer executable dynamically links the XKB/X11 runtime even
in console mode; the Ubuntu base image did not provide that library.

**Correction:** Added the Ubuntu `libxkbcommon-x11-0` runtime package before
invoking the installer. No generated gateware, package, or device state changed.

**Candidate rule:** Treat hardware-tool installers as native GUI-linked
executables until their runtime library dependencies have been verified, even
when invoked in a console-only mode.

### 2026-09-01 — Radiant installer exposed a second Qt/X11 dependency

**Attempt:** Rebuilt the image after adding the XKB runtime required by the
Radiant installer.

**Failure:** Dynamic loading then stopped at `libxcb-cursor.so.0`.

**Cause:** The console installer has a broader Qt/X11 runtime dependency set
than the minimal Ubuntu base provides.

**Correction:** Provisioned the small X11/Qt runtime closure (including
`libxcb-cursor0`) in the installer layer rather than discovering one missing
library per rebuild. No generated gateware, package, or device state changed.

**Candidate rule:** When a GUI-linked native tool fails in a minimal container,
install its runtime dependency set as a group and then re-test.

### 2026-09-01 — Radiant installer also required the GLVND OpenGL ABI library

**Attempt:** Rebuilt the image with the X11/Qt runtime dependency set.

**Failure:** Dynamic loading then stopped at `libOpenGL.so.0`.

**Cause:** Ubuntu packages the GLVND OpenGL ABI library (`libopengl0`) separately
from `libgl1`; the initial runtime set included only the latter.

**Correction:** Added `libopengl0` to the installer layer. No generated
gateware, package, or device state changed.

**Candidate rule:** For GLVND applications, include both the legacy `libgl1`
loader and the `libopengl0` ABI library when the binary's dependencies require
them.

### 2026-09-01 — Gateware image cloned Verilator without installing Git

**Attempt:** Continued the parent-owned gateware image build after the Radiant
installer dependencies were supplied.

**Failure:** The Verilator source layer stopped at `/bin/sh: 1: git: not found`.

**Cause:** The layer runs `git clone` but the preceding build-tool package list
did not include Git.

**Correction:** Added `git` to the build-tool installation layer. No generated
gateware, package, or device state changed.

**Candidate rule:** Audit each source-fetching Docker layer for its VCS client
as well as compiler and build-tool prerequisites.
