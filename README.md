# Science-XYZ NML Tools

This repository is for bringing up and evaluating the Science SciFi-2 / Synapse ecosystem and for developing reusable experimental infrastructure around it.

The project is intended to support:

* SciFi-2 and Axon peripheral bring-up and configuration;
* C++ Synapse Apps for on-device signal processing;
* host-side C++ acquisition and synchronization tools;
* integration of additional experimental sensor streams;
* reproducible experiment configuration, logging, and diagnostics.

Persistent project implementation should prefer C++ where practical. Python is used primarily for `synapsectl`, Synapse tooling, diagnostics, and lightweight support scripts.

## Current Bench Configuration

* **SciFi-2 headstage:** `192.168.100.157`
* **Host computer:** `192.168.100.14`
* **Axon Omnetics Adapter:** connected to SciFi-2 USB-C Port 2
* **Candidate auxiliary sensors:**

  * XIAO nRF52840 Sense
  * wireless EMG wristbands

Do not assume that these IP addresses or peripheral IDs apply to another installation. Query the connected device before configuring a signal chain.

## Prerequisites

For basic Synapse client use:

- Git
- 64-bit CPython 3.13
- access to the same network as the SciFi-2

Python 3.13 is the current recommended development baseline. The project may advance this baseline as newer stable Python releases and project dependencies mature.

For Synapse App development:

* Docker
* Ubuntu Linux or macOS

Science currently supports and tests Synapse App development on Ubuntu Linux and macOS. Windows users should use an appropriate Linux development environment, such as WSL2, for App build/deployment tooling.

## Quick Start

The host-local raw broadband/task recorder has a standalone C++ build, separate from the device App. See [recorder build, schema and operator workflow](docs/calibration-recording-mvp.md#host-recorder-build-and-use) for Linux/WSL dependencies, hardware-free tests, and exclusive-create recording. The [calibration task workflow](docs/calibration-task-workflow.md) covers the terminal instructor, Reactions WebSocket adapter, epoch verification and offline diagnostic/model-fitting commands. Physical GPIO timing and end-to-end calibration acceptance remain open.

### 1. Clone the repository

```bash
git clone https://github.com/Neuro-Mechatronics-Interfaces/ScienceXYZ.git
cd ScienceXYZ
```

### 2. Initialize vendor submodules

```bash
git submodule update --init --recursive
```

Third-party Science repositories under `vendor/` are maintained as Git submodules and should normally be treated as upstream/read-only dependencies.

### 3. Initialize the Python virtual environment

The recommended interpreter is 64-bit Python 3.13.

On Windows use WSL Ubuntu terminal, to set up: ```bash
deactivate
rm -rf ~/.venvs/sciencexyz

uv python install 3.13
uv venv --python 3.13 --seed ~/.venvs/sciencexyz
```

Then you should be able to cleanly activate your environment:   ```bash
source ~/.venvs/sciencexyz/bin/activate
```

For troubleshooting: ```bash
python --version
python -m pip --version

python -m pip install --upgrade pip
python -m pip install science-synapse==2.7.7

which synapsectl
synapsectl --version
docker info
```

### 4. Verify SciFi-2 connectivity

With the host and SciFi-2 on the same network:

```bash
synapsectl -u 192.168.100.157 info
```

Confirm that:

1. the SciFi-2 responds;
2. its software/firmware information is reported

## Run a Calibration Session

`calibrate-session` (the `run_calibration_session.py` entry point) is the single launcher for a Reactions-driven calibration recording. It generates the session artifacts, gates on the operator's device start, and then runs the host control service and the Reactions WebSocket bridge that owns the built C++ recorder. **It never runs `synapsectl` and never controls the device** — you run the `synapsectl` commands yourself and hand the launcher the resulting `info` capture.

Prerequisites: the client installed (`pip install -e 'apps/stateful-decode-and-sync/client[recording]'`) and the C++ raw recorder built (see [recorder build](docs/calibration-recording-mvp.md#host-recorder-build-and-use)).

### 1. Preview the exact commands (`--dry-run`)

`--dry-run` prints the operator `synapsectl` line and both host child commands without generating a session or starting anything:

```bash
calibrate-session --device-uri 192.168.100.157 --device-tap 192.168.100.157:647 --session-dir data/reactions/session-001 --origin https://chr.nml.wtf --dry-run
```

Use `--device-uri` for the `synapsectl` address and `--device-tap` for the device tap address (`<ip>:<port>`) the recorder connects to. Add `--gestures Fist Paper ...` (MOTION_LUT CamelCase keys) for a hub-and-spoke band profile; omit it for the linear rest/two-action MVP.

### 2. Generate the session and print the operator start line

Run the same command without `--dry-run`. The launcher creates the fresh `--session-dir` (a directory that already holds a complete session is reused, not overwritten) with `device-config.json`, `task-profile.json`, and `provenance.json`, then prints the exact `synapsectl start` line and stops (exit code 2) because no `info` capture was supplied yet:

```bash
calibrate-session --device-uri 192.168.100.157 --device-tap 192.168.100.157:647 --session-dir data/reactions/session-003 --origin https://chr.nml.wtf
```

### 3. Operator: start the device App and capture `info`

Run the printed command yourself, then save an `info` capture to a file:

```bash
synapsectl -u 192.168.100.157 start data/reactions/session-003/device-config.json
synapsectl -u 192.168.100.157 info > data/reactions/session-003/info.txt
```

Confirm the capture shows Application **`stateful-decode-and-sync` → Running: True** (the overall device `Status: Running` is not sufficient).

### 4. Launch the host processes past the device gate

Rerun the launcher with the **same** `--session-dir` plus `--info-capture`. It reuses the session generated in step 2 (it does not regenerate, and saving the capture inside the session directory is fine), parses the capture, and starts the control service (port 18765) and the Reactions bridge (port 9999) only if the App reports Running: True, otherwise it refuses (exit code 3):

```bash
calibrate-session --device-uri 192.168.100.157 --device-tap 192.168.100.157:647 --session-dir data/reactions/session-003 --origin https://chr.nml.wtf --info-capture data/reactions/session-003/info.txt
```

Leave it running. Connect the Reactions page, run the calibration, then stop with Ctrl-C. The bridge creates its own exclusive recording directory under `--output-root` (default `data/reactions/`); analyze it offline with `analyze-recording` against the returned `session_dir`.

The browser integration, transport requirements, and offline verification are detailed in the [calibration task workflow](docs/calibration-task-workflow.md).

### GUI console (`calibrate-gui`)

`calibrate-gui` is a PySide6 front end for the same flow, so the whole session can be driven from one window instead of the terminal. It performs the identical steps with the same boundaries — it **never** runs `synapsectl`. Launch it (or make a shortcut to `calibrate-gui.exe`) and work top to bottom:

1. **Device and session** — set the device URI, tap, origin, optional gestures, and session dir, then **Generate / reuse session** (an existing complete session is reused, matching the terminal launcher).
2. **Operator device start** — the exact `synapsectl start` line is shown with a **Copy** button. You can run it yourself and **Load info capture…**, or use the optional **Run: start device** and **Run: fetch info + gate** buttons, which invoke `synapsectl` directly (the `synapsectl command` field defaults to the name resolved from the active venv/PATH — a Windows-native install in `.venv/Scripts` works with no change; override the field for a differently located or WSL install). Either way the window gates on Application `stateful-decode-and-sync` → Running: True. Running `synapsectl` from this operator GUI is permitted under the AGENTS.md CLI-execution boundary scope; it never happens from tests or an agent path.
3. **Host processes** — **Start service + bridge** (enabled only after the gate passes) launches the control service and Reactions bridge as child processes this window owns, streaming their output into the log pane.
4. **Recording and task control** — once the bridge reports ready, **Connect to bridge** opens a loopback WebSocket using the same envelopes the Reactions page sends, then **Start/Stop recording** and the task-event buttons drive the C++ recorder directly (no browser required). The Reactions page can still connect to the same bridge instead.

The window remembers the stage-1 field values (device URI/tap, origin, gestures, paths, ports, `synapsectl` command) between launches in a per-user INI file (`QSettings`, written on close; on Windows under `%APPDATA%/NML/CalibrationSessionConsole.ini`), so a returning operator sees their last values as defaults. A first run with no file uses the built-in defaults.

**Passive mode.** The stage-1 **Passive mode** checkbox (also `--passive` on `calibrate-session` / `run_reactions_bridge.py`) switches the recording model: instead of the C++ recorder writing device-authoritative `raw.h5` and gestures being proposed to the device task machine, the bridge treats the SciFi-2 as a broadband *source* and records host-side into a single [Cognescent Data Format](data/example/data.hdf5) `data.hdf5` — `/devices/1` broadband from the tap (read in Python) and `/devices/0` browser annotations stamped with the host clock the instant each event arrives at the bridge. This removes the browser→device command round trip (and its Wi-Fi latency) at the cost of looser broadband alignment (host wall clock, not a device-committed transition). Use it when your downstream pipelines consume the Cognescent format and host-clock event marks are acceptable. Passive mode needs no C++ recorder, so it is unaffected by the recorder's platform.

Each browser start-recording request creates a **new** folder named like the operator's existing pipeline — `<date>-<unix>-<shortid>v-<protocol>@<block>` (e.g. `2026-08-04-1785858246-3f06fed4v-nml-wtf_Gestures_pinky_flexion@1`) — where the protocol base comes from the start-recording metadata and `<block>` is an index that starts at the stage-1 **Block (passive)** spinbox value, is reconciled to `max(our block, the browser's @N)`, and increments on each stop. The bridge also answers the browser's `session`-stream block exchange (its `SessionMetaAdapter`) so the page and the recorder agree on the block.

Closing the window stops the recording, bridge, and service. The HTTPS-page → loopback-WebSocket transport caveat for the hosted Reactions page still applies (see the [calibration task workflow](docs/calibration-task-workflow.md)).

## Repository Layout

As the project develops, use the following top-level organization:

```text
ScienceXYZ/
├── apps/ # Synapse Apps deployed to SciFi-2
├── config/ # Tracked Synapse and experiment configurations
├── data/ # Local recordings; ignored by Git
├── firmware/ # Auxiliary sensor firmware
├── host/ # Host-side C++ acquisition/synchronization/fusion
├── protocol/ # Shared versioned wire contracts
├── scripts/ # Reproducible setup and diagnostic utilities
├── vendor/ # Upstream Science dependencies as Git submodules
├── AGENTS.md # Persistent repository-specific development rules
├── MISTAKES.md # Evidence log for recurring development mistakes
├── TODO.md # Current milestones and planned work
└── README.md
```

Directories may be added only when needed; empty scaffolding is not required.

## Development Direction

The initial development path is tracked in [`TODO.md`](TODO.md).

At a high level, the first objective is to establish a reproducible path from:

```text
Axon Omnetics
      │
      ▼
 SciFi-2
      │
      ▼
Synapse signal chain
      │
      ├── on-device C++ Synapse App
      │
      ▼
 Synapse Tap
      │
      ▼
host-side C++ acquisition
      ▲
      │
auxiliary wireless sensors
```

The first multimodal implementation should prioritize explicit timestamps, sequence numbers, dropped-packet detection, and synchronization diagnostics over application-specific signal processing.

The supported wireless architecture uses external phone/tablet/laptop gateways that publish versioned L2CAP batches over LAN ZeroMQ/TCP. The contract, receiver configuration schema, and copy/paste LAN requirements are in [`docs/wireless-batch-contract.md`](docs/wireless-batch-contract.md) and [`docs/wireless-gateway-ingress-requirements.md`](docs/wireless-gateway-ingress-requirements.md). The hardware-free ingress implementation and test command are documented in [`docs/wireless-ingress-spike.md`](docs/wireless-ingress-spike.md). The measured loopback/LAN/wireless acceptance schema and checker are in [`docs/alignment-acceptance.md`](docs/alignment-acceptance.md).

## Synapse Apps

Science Synapse Apps are C++ applications that execute on the SciFi-2 and are integrated into Synapse signal chains through an application node. Start App development from Science's official `synapse-example-app` structure rather than constructing the SDK/build environment from scratch. A minimal initial application should resemble:

```text
kBroadbandSource -> kApplication -> Tap
```

App build and deployment use `synapsectl` and Docker.

Use the locally installed CLI as the authority for command syntax:

```bash
synapsectl --help
synapsectl build --help
synapsectl deploy --help
```

## Project Documentation

The repository separates documentation by purpose:

* `README.md` — concise human-facing setup, build, run, and repository orientation;
* `AGENTS.md` — durable repository rules and implementation constraints for coding agents;
* `TODO.md` — current milestones, investigations, and unfinished work;
* `MISTAKES.md` — evidence log of concrete mistakes and lessons learned.

Avoid duplicating detailed content across these files. When project behavior changes, update the document responsible for that information.

## Data

Raw experimental recordings belong under `data/` or another explicitly ignored recording directory and should not be committed to Git. Small configurations, metadata schemas, test vectors, and deterministic reference fixtures may be tracked when they are useful for reproducibility.
