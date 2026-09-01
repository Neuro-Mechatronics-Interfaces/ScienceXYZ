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

Python 3.13 is the current recommended development baseline. The project may
advance this baseline as newer stable Python releases and project dependencies
mature.

For Synapse App development:

* Docker
* Ubuntu Linux or macOS

Science currently supports and tests Synapse App development on Ubuntu Linux and macOS. Windows users should use an appropriate Linux development environment, such as WSL2, for App build/deployment tooling.

## Quick Start

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

On Windows use WSL Ubuntu terminal, to set up:
```bash
deactivate
rm -rf ~/.venvs/sciencexyz

uv python install 3.13
uv venv --python 3.13 --seed ~/.venvs/sciencexyz
```

Then you should be able to cleanly activate your environment:  
```bash
source ~/.venvs/sciencexyz/bin/activate
```

For troubleshooting: 
```bash
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

## Repository Layout

As the project develops, use the following top-level organization:

```text
ScienceXYZ/
├── apps/       # Synapse Apps deployed to SciFi-2
├── config/     # Tracked Synapse and experiment configurations
├── data/       # Local recordings; ignored by Git
├── firmware/   # Auxiliary sensor firmware
├── host/       # Host-side C++ acquisition/synchronization/fusion
├── protocol/   # Shared versioned wire contracts
├── scripts/    # Reproducible setup and diagnostic utilities
├── vendor/     # Upstream Science dependencies as Git submodules
├── AGENTS.md   # Persistent repository-specific development rules
├── MISTAKES.md # Evidence log for recurring development mistakes
├── TODO.md     # Current milestones and planned work
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

The supported wireless architecture uses external phone/tablet/laptop gateways
that publish versioned L2CAP batches over LAN ZeroMQ/TCP. The contract,
receiver configuration schema, and copy/paste LAN requirements are in
[`docs/wireless-batch-contract.md`](docs/wireless-batch-contract.md) and
[`docs/wireless-gateway-ingress-requirements.md`](docs/wireless-gateway-ingress-requirements.md).
The hardware-free ingress implementation and test command are documented in
[`docs/wireless-ingress-spike.md`](docs/wireless-ingress-spike.md).
The measured loopback/LAN/wireless acceptance schema and checker are in
[`docs/alignment-acceptance.md`](docs/alignment-acceptance.md).

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
