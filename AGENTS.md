# AGENTS.md

## Purpose

This repository is for bringing up and evaluating the Science SciFi-2 / Synapse ecosystem and for developing reusable experimental infrastructure around it.

## Current Hardware

Current bench configuration:

- SciFi-2 headstage
  - IP: `192.168.100.157`
- Host laptop
  - IP: `192.168.100.14`
- Science Axon Omnetics Adapter
  - connected to SciFi-2 by USB-C on Port 2
- Additional candidate test sensors:
  - XIAO nRF52840 Sense boards
  - wireless EMG wristbands

Do not hard-code a Synapse peripheral ID. Query the running device and use the ID reported by `synapsectl ... info` or the corresponding C++ API.

## Repository Organization

Keep third-party Science repositories under `vendor/` as Git submodules.

Treat all `vendor/` repositories as upstream/read-only code by default. Do not make local feature changes inside a vendored repository unless explicitly requested.

Changes to Science dependencies should normally consist only of updating the
submodule revision.

**Exception -- `m053m716/`-prefixed branches.** A `vendor/` submodule checked out
on a branch whose name begins with `m053m716/` is an intentional local fork, and
editing it is allowed. Treat that branch as the working copy: make the feature
changes there, commit them in the submodule, and bump the submodule pointer in
this repository. Do not edit a vendored submodule that is on any other branch
(e.g. upstream `main`, a tag, or a detached upstream revision) without an
explicit request; first move it onto an `m053m716/`-prefixed branch. The
read-only default still governs every submodule not on such a branch.

Use these top-level areas as the repository grows:

- `apps/` -- Synapse Apps deployed to SciFi-2
- `host/` -- host-side C++ acquisition, synchronization, and fusion software
- `firmware/` -- experimental peripheral firmware such as XIAO test sensors
- `config/` -- tracked Synapse and experiment configuration
- `scripts/` -- reproducible bring-up/build/test utilities
- `data/` -- local recordings; ignored by Git
- `vendor/` -- upstream dependencies/submodules

## C++ First

Prefer C++ for persistent project implementation.

Use Python where it is the documented or substantially simpler interface,
especially for `synapsectl`, packaging/deployment, one-off diagnostics, or as a reference implementation.

`vendor/synapse-cpp` is the host-side C++ client for the Synapse API. Use it
for device operations, discovery, queries, and Taps where practical.

A Synapse App is different from a `synapse-cpp` client. On-device Apps derive from `synapse::App` and use the `synapse-app-sdk`. Do not attempt to implement a deployed Synapse App by linking it against `vendor/synapse-cpp`.

Use `vendor/synapse-api` as the canonical protocol definition when interpreting or generating Synapse protobuf messages.


## Host Fusion

The preferred multimodal architecture is host-side fusion.

The SciFi-2 should expose neural/device data through Synapse Taps. Additional wireless sensors should have independent host adapters. Normalize all adapters into a common timestamped representation before experiment-specific processing.

At minimum preserve, when available:

- source identifier;
- source sequence number;
- source/device timestamp;
- host receive timestamp;
- host-aligned timestamp;
- source sample rate;
- payload;
- synchronization quality/status.

Never replace a source timestamp with a host receipt timestamp. Preserve both.

Do not silently interpolate, resample, or drop source data. Any such operation must occur in an explicit processing stage and be represented in logs/metadata.

## Synchronization

Treat synchronization as a first-class subsystem rather than an incidental
property of acquisition.For SciFi-to-host synchronization, prefer the synchronization mechanism defined by the current Synapse API.
 
For independent wireless sensors:

- include a monotonically increasing sequence number;
- include a source-generated timestamp or sample counter whenever possible;
- estimate the mapping from each source clock into the host/SciFi timebase;
- log offset, drift, and synchronization uncertainty.

Hardware synchronization through the SciFi GPIO may be evaluated separately,
but do not assume GPIO events are represented in a particular Synapse stream
without verifying the current API/firmware behavior.

## Data and Reproducibility

Raw acquired data is immutable. Derived, filtered, aligned, or resampled data must be distinguishable from raw data and reproducible from tracked code/configuration. Every recorded session should eventually include enough metadata to reconstruct:

- software revision;
- Synapse/SciFi firmware version;
- signal-chain configuration;
- physical peripheral IDs;
- sample rates/channel configuration;
- host clock information;
- synchronization estimates;
- attached auxiliary sensor identities.

## Build and Test Expectations

Prefer small deterministic smoke tests before hardware-dependent integration
tests. Do not require physical SciFi hardware for ordinary unit tests. Hardware tests should fail clearly when the device is unavailable rather than silently substituting simulated data.

When documentation, installed `synapsectl` behavior, and an example repository disagree, prefer operator-provided installed CLI evidence. Reuse the recorded help below; request only the relevant subcommand's help when its arguments are unknown. Document any version-specific workaround.

## Synapse CLI Execution Boundary

Do not execute `synapsectl` from an agent environment. When its installed
version, help text, device state, configuration validation, tap status, logs,
or any other CLI evidence is needed, ask the user to run one specific command
and provide the relevant output. Interpret that user-provided output against
the canonical API and repository configuration; do not substitute an assumed
local CLI installation or attempt device control directly.

**Scope of this boundary.** It governs the *agent* and any tooling that runs in
the automated agent environment. It does not forbid an *operator-run* tool on
the operator's own machine from invoking `synapsectl`: the calibration launcher
(`run_calibration_session.py`) and the operator GUI (`run_calibration_gui.py` /
`calibrate-gui`) may run `synapsectl` because a human operator launches and
watches them. Such a tool must (1) make the exact command visible before it runs
(the launcher's `--dry-run` and the GUI's shown/copyable command line), (2) treat
the CLI path as configurable rather than hard-coding one install, and (3) never
run any device command from a test, an agent invocation, or a headless/CI path.
The agent itself still never runs `synapsectl`; it only edits and reasons about
these operator tools.

### Recorded operator CLI help (2026-09-05)

The operator supplied `synapsectl --help`; do not ask for the basic help again
merely because a new session starts. Reuse this summary unless the operator
reports a CLI upgrade/change or observed behavior contradicts it. The executable
is **synapsectl**, not synapsectrl. The CLI package version was not included in
this help output; the device's reported Synapse 2.4.1 is separate evidence.

- Global options: `-h`/`--help`, `-u`/`--uri URI` (device IP or name),
  `--version`, `-v`/`--verbose`.
- Commands: `discover`, `info`, `query`, `start`, `stop`, `configure`, `logs`,
  `read`, `plot`, `file`, `taps`, `apps`, `peripherals`, `settings`, `deploy-model`.
- `start` and `stop` operate on the device or an application; `configure` writes
  device configuration; `logs` gets device logs; `query` executes a device query.
- `read` reads a device Broadband Tap into HDF5; `plot` plots recordings;
  `file`, `taps`, `apps`, and `settings` manage their respective resources;
  `peripherals` builds/deploys peripheral plugins; `deploy-model` deploys a model.
- Operator-confirmed forms: `synapsectl -u "$DEV" info` and
  `synapsectl -u "$DEV" start <configuration.json>`.

This summary establishes command availability, not unprovided subcommand flags.
Before requesting help, check this section and the conversation. Whenever the
operator supplies additional CLI help, record its syntax, option meanings, and
observation date here in the same turn. Do not ask for recorded help again unless
the operator reports a CLI change or observed behavior contradicts it. Record
only supplied evidence; a requested help command is not verified syntax.
Static CLI syntax does not substitute for current device status.

#### `synapsectl logs --help` (operator supplied 2026-09-05)

```text
synapsectl logs [-h] [--output OUTPUT] [--quiet]
               [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--follow]
               [--since N] [--start-time START_TIME] [--end-time END_TIME]
```

- `-h` / `--help`: show help and exit.
- `-o` / `--output OUTPUT`: optional file to write logs to.
- `-q` / `--quiet`: suppress stdout output.
- `-l` / `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}`: log level filter.
- `-f` / `--follow`: follow log output.
- `-S` / `--since N`: retrieve the last N **milliseconds**.
- `--start-time START_TIME` / `--end-time END_TIME`: ISO-format time bounds,
  e.g. `2024-03-14T15:30:00`. Help does not specify timezone semantics.
- Operator-confirmed execution: `synapsectl -u "$DEV" logs --since 1800000`
  retrieves a 30-minute interval. The supplied output contained `scifi-server`
  records, including configuration/start and GET_LOGS requests; it did not
  contain the App-process failure reason. No App selector appears in this help.

#### `synapsectl apps --help` (operator supplied 2026-09-05)

```text
synapsectl apps [-h] {build,deploy,list} ...
```

- `-h` / `--help`: show help and exit.
- `build`: cross-compile and package an application into a `.deb` without deploying.
- `deploy`: deploy an application to a Synapse device.
- `list`: list installed applications on the device.
- Operator-confirmed execution (2026-09-05): `synapsectl -u "$DEV" apps list`.
  Output lists application names and versions. This confirms installation, not
  running status or the exact source revision of a deployed build.
- No App logs subcommand is exposed here. Arguments for `build`, `deploy`, and
  `list` are not established by this help; request only the relevant nested
  command's help if its arguments are needed and have not already been supplied.

#### Pre-built diagnostic package deployment (operator confirmed 2026-09-05)

```bash
synapsectl -u "$DEV" peripherals deploy driver --package "$(pwd)/scripts/device-diag/nml-diag_0.6.0_all.deb"
```

The operator observed all six deployment steps succeed, ending with package
installed successfully (nml-diag 0.6.0, 1,462 bytes). This form emits
`Warning: --driver ignored when --package is provided; deploying the supplied .deb as-is.`
The warning did not prevent installation. Installation success does not verify
the diagnostic report contents or resolve the App startup failure. This records
the tested form, not other unprovided `peripherals deploy` options.

#### Diagnostic report download (operator confirmed 2026-09-05)

```bash
synapsectl -u "$DEV" file get nml-diag-report.txt
```

The operator observed a successful 59.5 kB download. Reuse this confirmed form;
other `file get` arguments are not established here. Check the shared working
directory for the downloaded report before asking the operator to attach it.

#### `synapsectl start --help` (operator supplied 2026-09-05)

```text
synapsectl start [-h] [config_file]
```

- `config_file`: optional device configuration JSON path. When supplied, the
  CLI uploads the configuration first, then starts the device.
- Without an argument, starts the device without reconfiguring it.
- `-h` / `--help`: show help and exit.
- This installed subcommand exposes no App-specific start argument. This
  detailed help supersedes the broader top-level description above; do not
  pass an App name as `config_file` or assume a device start retries a failed
  App while the overall device is already running.

### Operator App build evidence (2026-09-10)

The operator ran `synapsectl apps build --clean apps/stateful-decode-and-sync`.
It reached Docker/vcpkg dependency installation and failed building libusb at
`autoreconf -vfi`; this establishes the `apps build --clean <app-directory>`
form, not a successful package build. The App directory has since been renamed
to `apps/scifi2-hub-manager`. Agents must still not execute `synapsectl`.

## Python Environment

Use 64-bit CPython 3.13 as the current project Python baseline.

Do not lower the supported Python version solely to accommodate an assumed
third-party compatibility issue. Verify the incompatibility first. If a
dependency genuinely requires an older interpreter, document the dependency,
failure, and workaround in `MISTAKES.md`.

The Python baseline may be advanced as newer stable CPython releases become
well-supported by the project's dependencies.

## Handoff MCP

Use the project-scoped `handoff` MCP server as a compact continuity cache, not
as a replacement for Git history or repository documentation.

- At the start of a session, call `handoff_list` for open handoffs before
  repeating discovery work. Use a small result limit and inspect the newest
  relevant entries first. Call `todo_list` when the task may depend on queued
  work; use `project_status` when only counts are needed.
- Verify a breadcrumb against the current working tree and device state before
  acting on it. Handoffs describe the state observed by an earlier session and
  can become stale.
- Before adding an item, list existing open items and update or resolve them
  instead of creating duplicates. Use `todo_add` for one concrete, actionable
  next step and `handoff_add` for session state that another worker needs in
  order to resume efficiently.
- Keep each handoff short but operational: summarize what was accomplished,
  record key decisions and gotchas, give explicit next steps, and reference
  exact file paths, commands, issue IDs, or device IDs where useful. Do not
  paste large logs, secrets, or information already captured in tracked files.
- Mark completed or abandoned TODOs with `todo_update`, and call
  `handoff_resolve` when a handoff is completed or superseded. Open records
  should represent work that is genuinely still resumable.
- When accumulated searches, logs, or finished subtasks are crowding the
  context window, call `context_report` and then `context_compact`. Persist a
  summary during compaction only when it will help a later session resume.
- Put durable rules in `AGENTS.md`, developer workflows in `README.md`, current
  tracked milestones in `TODO.md`, and concrete failure evidence in
  `MISTAKES.md`. Keep MCP handoffs transient and project-specific.
- Before ending a session that materially changes resumable work, reconcile
  affected MCP TODOs and write one compact handoff for that workstream. Include
  the current Git revision, whether changes are uncommitted, exact relevant
  paths, verification performed, remaining limitations, and the first concrete
  next action. Do not create handoffs for routine work with nothing to resume.
- Separate implementation, automated-test evidence, operator-reported bench
  evidence, and unverified assumptions. Date bench observations; a working GUI
  or passing unit test does not complete recording or physical-sync acceptance.
  Historical CLI logs and cached device IDs are not live evidence. Follow the
  Synapse CLI execution boundary when new device evidence is needed.
- Keep one canonical TODO per actionable outcome. Update its remaining scope
  as partial work lands; mark it done only when its acceptance is met. Mark a
  duplicate dropped with the surviving TODO ID, not done. Preserve unrelated
  open work and do not turn stale build blockers into current facts without
  verification.
- When replacing a handoff, create and verify the replacement first, naming
  the records it supersedes; then resolve those old records. Carry forward
  unresolved scope or reference its canonical TODO so it is not lost. Read
  back affected records after mutations and report the relevant IDs at handoff.

## Repository Documentation

Keep repository documentation separated by purpose:

* `README.md` is the human-facing entry point.
* `AGENTS.md` contains durable repository-specific rules and implementation constraints.
* `TODO.md` contains current milestones, investigations, and unfinished work.
* `MISTAKES.md` records concrete mistakes encountered while working in this repository.

### README.md

Keep @README.md concise, executable, and current.

It should contain:

* the purpose of the repository;
* prerequisites required for a new developer;
* clone, submodule, and environment setup;
* the minimum commands needed to verify the installation;
* the current high-level repository layout;
* the normal build/run workflow;
* links or references to `TODO.md`, `AGENTS.md`, and other detailed documentation where appropriate.

When a change makes an existing README command, prerequisite, path, or workflow incorrect, update the README as part of that change.

Do not use `README.md` for:

* transient implementation plans;
* speculative future architecture;
* long agent-specific instructions;
* detailed debugging history;
* individual mistakes or postmortems.

Put current work in @TODO.md, durable agent rules in @AGENTS.md, and mistake evidence in @MISTAKES.md.

### MISTAKES.md

Maintain `MISTAKES.md` as a concise evidence log of mistakes encountered while developing or operating this repository.

Record a mistake when it produces incorrect behavior, wasted investigation, a broken build/test/deployment, data-integrity risk, or a misleading conclusion that future work could reasonably repeat.

Each entry should state:

* what was attempted;
* what went wrong;
* the actual cause, once known;
* how it was corrected;
* any candidate rule that might prevent recurrence.

Do not add hypothetical mistakes or generic programming advice.

Do not immediately turn every mistake into an `AGENTS.md` rule. `MISTAKES.md` is the evidence layer; `AGENTS.md` is the policy layer.

Periodically review `MISTAKES.md`. When multiple entries demonstrate the same recurring failure mode, or when a single failure has sufficiently serious consequences to justify a permanent safeguard, promote the lesson into a short, general rule in `AGENTS.md`.

When a rule is promoted:

1. write the rule in terms of the underlying failure mode rather than the specific incident;
2. place it in the relevant section of `AGENTS.md`;
3. retain the original `MISTAKES.md` entries as historical evidence;
4. note in the relevant mistake entry that a preventative rule was added to `AGENTS.md`.

Keep entries factual and brief. `MISTAKES.md` should improve repository practice over time rather than becoming a chronological activity log.
