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

When documentation, installed `synapsectl` behavior, and an example repository disagree, inspect the installed CLI with `synapsectl --help` and prefer the API supported by the installed version. Document any version-specific workaround.

## Synapse CLI Execution Boundary

Do not execute `synapsectl` from an agent environment. When its installed
version, help text, device state, configuration validation, tap status, logs,
or any other CLI evidence is needed, ask the user to run one specific command
and provide the relevant output. Interpret that user-provided output against
the canonical API and repository configuration; do not substitute an assumed
local CLI installation or attempt device control directly.

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
