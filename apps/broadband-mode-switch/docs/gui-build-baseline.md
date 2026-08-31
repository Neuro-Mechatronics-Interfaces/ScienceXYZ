# GUI control-plane build baseline

Recorded 2026-08-31 before implementation work beyond the protocol design.

## Expected workflow

The app README documents this cross-build command from the repository root:

```text
synapsectl apps build apps/broadband-mode-switch
```

The documented deployment workflow also uses `synapsectl -u <device> apps
deploy ...`, `synapsectl -u <device> start ...`, and `synapsectl -u <device>
taps list`. These command forms could not be verified against the installed
CLI because no `synapsectl` executable is installed or available on `PATH`.

## Commands and results

| Command | Result |
| --- | --- |
| `synapsectl --help` | Not run by an installed executable; PowerShell reported `synapsectl` is not recognized. |
| `synapsectl apps --help` | Same missing-command result. |
| `synapsectl apps build --help` | Same missing-command result. |
| `synapsectl apps build apps/broadband-mode-switch` | Baseline build could not start for the same reason; no source compilation result was produced. |
| `py -3.13 --version` | `Python 3.13.15`. |
| `py -3.13 -m pip show science-synapse` | Package not found. |
| `docker info` | Docker client 29.7.2 is installed, but the Linux daemon is unavailable (`dockerDesktopLinuxEngine` pipe not found). |
| `cmake --version` | CMake is not available on the Windows `PATH`. |
| `g++ --version` | MSYS2 MinGW g++ 15.2.0 is available, but it is not the app's arm64 SDK/cross-build environment. |

An existing `build/aarch64/CMakeCache.txt` and executable are present, but the
cache identifies a Linux build rooted at `/home/workspace`, with
`BUILD_FOR_ARM64=ON` and `/usr/bin/c++`; its 2026-08-26 timestamp predates
this baseline and it was not rebuilt. It is retained as an artifact, not
reported as a successful verification of the current sources.

## Tap baseline

No device was contacted and no deploy/start/stop command was attempted because
the required CLI is unavailable. Therefore the currently compiled tap types
remain source-level facts only:

- consumer `set_source_mode`: protobuf `ListValue`;
- consumer `set_capture`: protobuf `ListValue`;
- consumer `fit_mlp`: protobuf `ListValue`;
- producer `broadband_out`: `synapse::BroadbandFrame`; and
- producer `class_out`: `synapse::Tensor`.

The exact installed CLI help, current device tap inventory, and whether the
current `BroadbandFrame` producer tap is accepted by the SDK remain pending
until `synapsectl`, Docker's Linux daemon, and the Python Synapse package are
available. This is a pre-existing environment blocker, independent of the GUI
protocol documentation.

## Hardware-free control-plane tests

The app now has an SDK-independent CTest target for the reusable control-plane
data layer. Configure the root project with the device application disabled so
the missing Synapse SDK and device-side dependencies are not needed:

```text
cmake -S apps/broadband-mode-switch -B apps/broadband-mode-switch/build/offline-tests -DBUILD_DEVICE_APP=OFF -DBUILD_TESTING=ON
cmake --build apps/broadband-mode-switch/build/offline-tests
ctest --test-dir apps/broadband-mode-switch/build/offline-tests --output-on-failure
```

The registered test is `broadband-mode-switch-control`. It uses fixed inputs
and covers the underlying `RingBuffer` plus the bounded `CollectionStore`:
per-label FIFO capacity, independent collections, checked dimensions and IDs,
deterministic collection, label/collection/all flush scopes, and monotonic data
generation changes. It remains SDK-independent and does not link a third-party
test framework. The documented CMake/CTest workflow was subsequently run in
Ubuntu WSL and passed; the direct g++ fallback also passes on this Windows
host.
