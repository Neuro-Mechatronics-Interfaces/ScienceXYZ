# Mistakes

This file records concrete mistakes encountered while working in this repository. Recurring failure modes may be promoted into durable rules in `AGENTS.md`.

## Entry Template

### 2026-09-09 - App host-tests build required CONFIG-mode protobuf absent on the distro

Building the SDK-free host tests to run `test_hub_spoke_cross_language_hash`
(`cmake -S apps/stateful-decode-and-sync -B build/app-tests -DBUILD_DEVICE_APP=OFF`)
failed at configure: `find_package(Protobuf CONFIG REQUIRED)` at
`apps/stateful-decode-and-sync/CMakeLists.txt:109` could not find
`protobuf-config.cmake`. The cause was a protobuf-version mismatch, not a missing
package: the device App is built inside the Synapse SDK image (protobuf >=22, which
ships CMake config files), but the operator's Ubuntu host had protobuf 3.21.12,
whose `libprotobuf-dev` provides only module-mode `FindProtobuf.cmake`. Installing
`libabsl-dev`/`protobuf-compiler` did not help because 3.21.x simply predates the
config-file packaging. The recorder project builds because it uses module-mode
`find_package(Protobuf REQUIRED)`. Corrected by making only the `BUILD_TESTING`
protobuf discovery prefer CONFIG then fall back to `MODULE` (both create the same
`protobuf::libprotobuf` target the tests link); the device-App discovery stays
strict CONFIG. Candidate rule: on a version-mismatch `find_package(... CONFIG)`
failure, verify the installed version against the config-file requirement before
adding packages, and prefer a CONFIG-then-MODULE fallback for targets that only
need a mode-agnostic imported target.

### 2026-09-05 - ARM64 protobuf map lookup used inconsistent Abseil hashes

Reproduced the task parser failure locally using Docker image
`stateful-decode-and-sync:latest-amd64`, GCC 10 ARM64, its shared protobuf 25.1
and static Abseil archives, under `qemu-aarch64-static`. The unmodified
`field()` triggered protobuf map.h:1069's bucket-consistency assertion:
`BucketNumberFromHash(hash_function()(k)) != VariantBucketNumber(...)`.
Native host protobuf tests passed, masking this dependency-boundary failure.
Changed bounded schema lookup to compare keys by iteration, avoiding hashed
lookup across the shared-library boundary. Updated unknown-field test setup
to use protobuf JSON parsing instead of executable-side map insertion (which
also triggered the assertion), and added a genuinely missing-field check.
The ARM64 task-state suite and native suite pass after the change. Device App
rebuild/deployment and live startup verification remain outstanding; no claim
that the installed device binary has been fixed.

### 2026-09-05 - Missing-field error was mistaken for missing uploaded data

Repeated configuration uploads were suggested after the App reported missing
`task_definition.schema_version`, although the host JSON already contained it.
The operator's completed 0.8.0 report now shows the field as numeric 1 in both
the saved device JSON and the same App process's configuration dump, followed
by the missing-field error. Upload loss is not supported by that evidence.
Investigate the deployed C++ parser/build/runtime instead; exact cause remains
unverified. Both operator 0.8.0 deployments completed, and every collection
section in the downloaded report returned exit 0. This supersedes the earlier
installation-blocker assessment without proving why 0.7.0 hung.

### 2026-09-05 - Diagnostic installer lacked time bounds

Diagnostic 0.7.0 repeatedly stalled at installation after App startup while
device queries stayed responsive; it completed quickly in a boot without App
journal entries. The script ran unbounded journal scans and printed the whole
report into installer stdout. The exact blocked operation is unverified;
journal collection and an undrained installer pipe are hypotheses. Version
0.8.0 adds command/copy timeouts, incremental reports, removes full-journal
scans and report stdout. Local shell/build checks pass; device recovery and
0.8.0 behavior remain unverified. Installer diagnostics should be time-bounded
and avoid bulk stdout.

### 2026-09-05 - WSL diagnostic build lost shell variables

The first package-build invocation used `wsl.exe sh -c`; the extra shell parsing
lost the temporary-directory variable and `cp` attempted `/package`, failing
with permission denied. Re-running with `wsl.exe --exec sh -c` preserved the
script and built successfully. Prefer explicit `--exec` for WSL shell scripts.

### 2026-09-05 - Server logs were requested to diagnose an App process

Requested `synapsectl logs` for an App startup failure before checking
`scripts/device-diag/README.md`, which already documents that this command
returns curated server logs, not the App journal. Also initially treated July
timestamps as stale evidence despite known device clock skew. The supplied
output included its own GET_LOGS request. Corrected the interpretation and
prepared boot-scoped App diagnostics. Check documented log scope and clock
limitations before requesting more operator output.

### 2026-09-05 - MEGA2560 sync sketch timed edges by hand-counted cycles

**Attempt:** Generate GPIO_0/GPIO_1 sync edges for the RHD2132 adapter with
`firmware/MEGA2560_GPIO_SYNC`, timing toggles by a `while(1)` loop in `setup()`
claimed to take "exactly 16 clock cycles" per iteration.
**Failure:** The LUT's labeled frequencies (16 Hz..2048 Hz) did not correspond
to real output. Branches, 16-bit compares, and a per-edge `random()` call made
iteration cost variable, so the iteration-count thresholds were not microseconds.
`noInterrupts()` was held forever, disabling `micros()/millis()` and `loop()`.
**Cause:** Timing by counting CPU cycles in C is not deterministic once branches
and library calls (`random()`) are in the loop body; the "16 cycles" premise was
false.
**Correction:** Rewrote to a hardware Timer3 CTC compare ISR at the fastest rate
(2048 Hz). D23 is the master clock (toggled every interrupt); D22 hops
pseudo-randomly by counting master ticks, so both lines share one timebase and
stay phase-locked for cross-device alignment. Edge is emitted before `random()`
so the PRNG never jitters an edge. Verified with a throwaway PlatformIO
`megaatmega2560` build (avr-gcc): clean compile/link, `firmware.hex` produced.
**Candidate rule:** For MCU edge timing, use a hardware timer/ISR, not
hand-counted C-loop cycles; never hold `noInterrupts()` across the main program.

### 2026-09-05 - Recorder instructions omitted provenance creation

**Attempt:** Operator followed the host recorder build/run instructions.
**Failure:** The command failed with `cannot read metadata file`.
**Cause:** The documentation named a required provenance file but supplied only
a comment telling the operator to create it, with no template or field guidance.
**Correction:** Added `config/calibration-provenance.template.json`, copy/edit
commands, and guidance distinguishing unknowns, supplied config and live evidence.
**Candidate rule:** Include a usable template whenever a quick-start command
requires a user-authored input file.

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

### 2026-09-01 — Initial T-22 acceptance fixtures masked failures

**Attempt:** Added the first measured-alignment acceptance analyzer and ran its
unit tests.

**Failure:** The fixture's requested edge error was not applied to the measured
timestamp, and an early return on nonzero continuity counters prevented the
report from exposing the available edge metrics.

**Correction:** Made the fixture construct the actual offset, continued metric
collection while retaining continuity failures, and added full mode ×
rate/batch coverage checks.

**Candidate rule:** Acceptance tests must exercise the measurement values they
claim to test and should report independent evidence even when one gate fails.

### 2026-09-04 — Building the task-recorder deps on WSL's default GCC 15

**Attempt:** To build the gated `task-recorder` consumer (T-32) end-to-end,
bootstrapped vcpkg in WSL (Ubuntu 26.04) and drove `vendor/synapse-cpp`'s
manifest build to produce the `synapse` CMake package plus `hdf5`, using the
distro-default compiler (GCC 15.2.0).

**Failure:** Two dead ends. (1) Running the synapse-cpp configure and the `hdf5`
install as two concurrent background vcpkg processes against one vcpkg root: both
died at "Detecting compiler hash … failed" because they race on the shared
`buildtrees/detect_compiler` tree. (2) After serializing, the real blocker
surfaced: the science overlay port set pins **abseil 20240116.2**, which does not
compile on GCC 15 — `absl/container/internal/container_memory.h:66: 'uintptr_t'
does not name a type` (older Abseil relied on a transitive `<cstdint>` include
that GCC 15 no longer provides). grpc/protobuf of the same vintage hit the same
wall.

**Cause:** Two independent issues. vcpkg does not support concurrent installs
against one root (shared `detect_compiler` buildtree). And the pinned overlay
ports were fixed for an older compiler era than the WSL default (GCC 15).

**Correction:** (1) Serialize all vcpkg operations against one root. (2) Install
`g++-13` and pin the build to it via a vcpkg custom triplet
(`VCPKG_CHAINLOAD_TOOLCHAIN_FILE` setting `CMAKE_C/CXX_COMPILER=gcc-13/g++-13`)
so the entire pinned overlay set compiles as intended, without editing any
upstream-pinned port. Host unit-test build (13/13) is unaffected — it is SDK/
HDF5-free and uses the distro g++ directly.

**Candidate rule:** Never run two vcpkg installs concurrently against the same
vcpkg root. When building a vendored dependency's pinned overlay-port set, match
the compiler to the ports' era with a custom triplet rather than patching
upstream-pinned ports one by one; the distro-default GCC on a new Ubuntu is
often newer than the ports were fixed for.

**Follow-up (same day):** The custom triplet is necessary but not sufficient in
two further ways. (a) The custom triplet governs only the *target* triplet;
grpc/protobuf codegen pull a **host-tool** `abseil:x64-linux` that still built
under the default GCC 15 and failed identically — fix by passing
`--host-triplet x64-linux-gcc13` as well as `--triplet`. (b) After that, the
build reached a deeper, non-compiler blocker: **protobuf 6.33.4 does not compile
against abseil 20240116.2** (`arena.cc`: `'WithStaticSizes' ... does not name a
template type`, `layout_type does not name a type`). The synapse-cpp manifest's
vcpkg **builtin-baseline** resolves protobuf/grpc/re2 to *current* versions
(protobuf 6.33.4, grpc 1.81.1, re2 2025-11-05) while the science **overlay** pins
abseil to the old 20240116.2 — an internally inconsistent graph (recent protobuf,
ancient abseil). This is a version-skew in the dependency set itself, not a
toolchain gap, and is unresolved: it needs a coherent version set (pin the whole
baseline to abseil's era via an overlay/version constraint, OR override abseil to
a protobuf-6.33-compatible release, OR build a tagged synapse-cpp release whose
lockfile is self-consistent) rather than a compiler change. Left for the next
session; the gcc-13-built deps that DID succeed (abseil, cppzmq, zeromq, c-ares,
openssl, re2, utf8-range, zlib) are cached in the vcpkg binary cache.

**Candidate rule (2):** A vendored dependency's vcpkg manifest that mixes a fresh
`builtin-baseline` with old overlay-port pins can resolve an internally
inconsistent graph (recent protobuf vs. ancient abseil). Prefer building a tagged
release with a self-consistent version set, or pin the transitive versions
explicitly, before assuming a build failure is a local toolchain problem.

**Correction to the WSL-host approach:** The right build path for this consumer
is the app's OWN `apps/stateful-decode-and-sync/Dockerfile` (arm64 cross-compile,
`ubuntu:20.04` + gcc-10-aarch64, CMake 3.28, vcpkg pinned `0f88ecb8`), driven from
the app's own `vcpkg.json` — that manifest resolves a SELF-CONSISTENT set
(protobuf 4.25.1 with abseil 20240116.2, plus hdf5 1.14.4.3, iir1, spdlog, fmt),
so the protobuf/abseil skew above never arises. The skew was an artifact of
building `vendor/synapse-cpp`'s standalone manifest (fresh baseline → protobuf
6.33.4) in isolation. Build the consumer through the app manifest/Docker path, not
a hand-driven synapse-cpp build.

### 2026-09-04 — szip 2.1.1 cross-compile configure needs try_run seeds (guessed the wrong fix first)

**Attempt:** Ran the app's Docker build (`arm64-linux-dynamic-release`, x86 host
cross-compiling to arm64). vcpkg resolved the consistent dependency set; hdf5
pulls szip 2.1.1 transitively, which failed at the CMake *configure* step
(`/usr/bin/ninja -v ... Error code: 1`).

**Failure (and a wrong first fix):** The in-container config log was not captured
by the failed Docker layer, so the cause was initially GUESSED as the well-known
"CMake 3.28 removed pre-3.5 `cmake_minimum_required` compatibility" error, and
`-DCMAKE_POLICY_VERSION_MINIMUM=3.5` was added to the triplets. The rebuild failed
identically. Reproducing szip's configure inside a container built to the COPY
layer (`docker build` truncated Dockerfile → `docker run` the manifest install →
`cat buildtrees/szip/config-arm64-linux-dynamic-release-out.log`) showed the flag
HAD worked (the pre-3.5 error became a mere deprecation warning) but the real,
distinct blocker was:

    CMake Error: try_run() invoked in cross-compiling mode, please set the
    following cache variables appropriately:
       HAVE_DEFAULT_SOURCE_RUN (+ __TRYRUN_OUTPUT)
       TEST_LFS_WORKS_RUN      (+ __TRYRUN_OUTPUT)

**Cause:** szip 2.1.1's `config/cmake/ConfigureChecks.cmake` uses `try_run()` to
probe runtime behavior (`_DEFAULT_SOURCE`, large-file support). Under
cross-compilation CMake cannot execute the arm64 test binary and hard-errors
unless those result variables are pre-seeded. Nothing to do with CMake policy
version. (The `io.h`/`winsock2.h`/`off64_t` lines in the log are unrelated failed
probes, not the fatal error.)

**Correction:** Replaced the policy flag with the correct cross-compile seeds in
both `external/sciencecorp/vcpkg/triplets/{arm64,x64}-linux-dynamic-release.cmake`:
`HAVE_DEFAULT_SOURCE_RUN=0` and `TEST_LFS_WORKS_RUN=0` (both succeed on
arm64/glibc), plus their `__TRYRUN_OUTPUT` companions. Verified by reproducing the
szip configure in the truncated-Dockerfile container. Rebuild pending (USER drives
the Docker build).

**Candidate rule:** Do not apply a fix from a *guessed* cause when the real log
can be captured. For a failed Docker layer, build a Dockerfile truncated to the
last good layer and run the failing command in a `docker run` to read the actual
error. When a legacy port fails to configure while cross-compiling with a
`try_run()` error, pre-seed the named `*_RUN` (and `*_RUN__TRYRUN_OUTPUT`) cache
variables via the triplet's `VCPKG_CMAKE_CONFIGURE_OPTIONS`, not a policy flag.

### 2026-09-04 — `setWindowIcon` did not change the Windows taskbar icon

**Attempt:** Gave the PySide6 GUIs (`gui.py`, `waveform.py`) a custom icon by
loading `assets/icon.svg` into a `QIcon` and calling `setWindowIcon` on both the
window and the `QApplication`, expecting the taskbar/tray icon to update.

**Failure:** The window title-bar and Alt-Tab icon changed, but the Windows
taskbar still showed the generic launcher icon (the "penguin"/Python icon).

**Cause:** On Windows the taskbar groups and icons a running app by its
**AppUserModelID (AppUMID)**, not by `QApplication.windowIcon`. A
Python-launched Qt process inherits the host launcher's AppUMID and therefore
its taskbar icon; `setWindowIcon` cannot override that surface.

**Correction:** Added `set_windows_app_id()` in
`stateful_decode_and_sync/appicon.py`, which calls
`ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(...)` with a
stable id (`NML.ScienceXYZ.StatefulDecodeAndSync`) before any window is shown;
called it from `run_gui`/`run_waveform`. It is a no-op off Windows and swallows
failures. Verified with `GetCurrentProcessExplicitAppUserModelID` (HRESULT 0,
id round-trips).

**Candidate rule:** On Windows, `QApplication.setWindowIcon` fixes only the
title-bar/Alt-Tab icon. To control the **taskbar** icon of a Python/Qt app, set
an explicit AppUserModelID via `SetCurrentProcessExplicitAppUserModelID` before
the first window is shown.

### 2026-09-04 — Taskbar still showed the penguin: the GUI was launched under WSL

**Attempt:** After adding the Windows AppUserModelID fix above, expected the
taskbar icon to be correct. The user still saw the "penguin" when launching
`stateful-decode-and-sync-waveform`.

**Failure:** The AppUMID fix had no effect on the observed taskbar icon.

**Cause:** The command was run from a **WSL Ubuntu** shell
(`maxmu@MAXLENOVO:/mnt/c/...`), so the GUI is a **Linux** process rendered by
**WSLg**. `sys.platform` is `linux`, so `set_windows_app_id()` correctly no-ops;
the Win32 AppUMID API is not the mechanism there. WSLg icons/groups a window by
a matching freedesktop `.desktop` entry keyed to the window's `WM_CLASS`, and
with none installed it shows its default Linux (penguin) icon. Easy to misread
as "the icon fix failed" when the real variable is *which OS the process runs
under* — the same repo path is reachable from both a native-Windows Python and a
WSL Python, and only the native one takes the AppUMID path.

**Correction:** Added a Linux/WSLg path in `appicon.py`: `install_desktop_entry`
writes `~/.local/share/applications/<app_id>.desktop` (with `StartupWMClass` =
app id) plus a themed PNG under `~/.local/share/icons/hicolor/256x256/apps/`, and
`set_desktop_file_name` calls `QGuiApplication.setDesktopFileName` so Qt sets the
matching `WM_CLASS`. `setup_taskbar_identity(app_leaf, name)` dispatches
per-platform and is called from both entry points. Shipped a rasterized
`assets/icon.png` (WSLg wants a PNG, not the SVG) and added it to
`package-data`. For a guaranteed-correct taskbar icon, run the **native
Windows** venv from PowerShell/CMD, not WSL.

**Candidate rule:** Before diagnosing a GUI desktop-integration symptom
(taskbar/tray icon, window grouping), confirm which OS the process actually runs
under. A `/mnt/c` path in a `user@HOST` prompt means WSL/WSLg (Linux), where
Win32 APIs no-op and freedesktop `.desktop`/`WM_CLASS` is the mechanism — not the
Windows AppUserModelID.

### 2026-09-05 - WSL recorder selected Windows HDF5 and omitted nested protos

**Attempt:** Build the standalone raw recorder with distribution host dependencies.

**Failure:** Linux compilation found Windows HDF5 1.14.3 headers and failed on a
conflicting ssize_t definition; API node headers were also missing.

**Cause:** HDF5 package-config discovery inherited a Windows installation; the
initial protobuf glob included only top-level API definitions.

**Correction:** Use HDF5 C-wrapper discovery (HDF5_NO_FIND_PACKAGE_CONFIG_FILE),
clear cached HDF5 variables, and recursively generate canonical API protos with
relative paths preserved. The Linux host recorder and tests subsequently built.

**Candidate rule:** Keep host library/header discovery within one platform and
preserve imported protobuf directory structure during code generation.

### 2026-09-05 - Mixed browser time units in supplied task client

**Attempt:** Map the supplied Reactions TaskSocketClient timestamps into recording
provenance. Inspection found `_t0 = performance.timeOrigin` (milliseconds) added
to `performance.now()/1000` (seconds), so the resulting value cannot be interpreted
as a consistent epoch clock.

**Correction:** Preserve incoming fields literally in the browser journal. The
companion adapter emits explicitly named millisecond clock fields; authoritative
labels use recorded device task boundaries. The supplied reference JS is unchanged.
No browser-to-source clock mapping or physical onset is claimed.

**Candidate rule:** Check time units at every clock composition before assigning
an epoch or aligning browser events with source samples.

### 2026-09-05 - Short diagnostic traces disappeared

**Attempt:** Render the synthetic 1,300-frame calibration diagnostic using a
min/max envelope. Its one-sample bins produced zero-length vertical lines and
invisible electrode traces.

**Correction:** Plot actual samples within each continuity segment when bins
contain one sample; retain min/max aggregation for long recordings. Inspect the
rendered short fixture as well as the real high-rate recording.

### 2026-09-05 - Preparation depended on missing session provenance

The workflow referenced an ignored previous-session provenance file and claimed
a prepared directory existed. The operator encountered an existing output path,
then a missing provenance input. The preparer wrote config/profile before reading
metadata, leaving partial output on that failure. It now validates inputs before
creating output and reports existing directories without a traceback. The guide
uses the tracked template and a fresh-directory recovery path. Two regression
tests verify no output on missing metadata and no overwrite of existing files.

### 2026-09-05 - Coincident GPIO overlays hid one channel

Full-height GPIO markers were drawn in GPIO 0 then GPIO 1 order. Synchronous
master/hopping edges could overlap exactly, making GPIO 0 appear absent. Each
GPIO now uses a separate vertical half of the subplot without shifting time;
an offscreen regression test verifies both lanes at the same timestamp. Missing
visible markers alone do not prove missing raw GPIO transitions.

### 2026-09-05 - Calibration guide mixed terminal roles and session names

The operator encountered repeated connection, existing-journal and task-definition
errors while following a guide that mixed setup v1/v2, port defaults and service
versus browser roles. The operator also corrected the CLI spelling to synapsectl.
Rewrote the guide with numbered WSL terminals, one setup/port convention, fresh
recording names, expected readiness messages, separate terminal/browser routes,
and an explicit App Running check. Latest info reports App Running False despite
overall device Running; old July log output is not current failure evidence.
