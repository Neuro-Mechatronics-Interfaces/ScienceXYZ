# SciFi-2 Hub Manager — host client

Host-side control and monitoring for the `scifi2-hub-manager` Synapse App on the SciFi-2: a PySide6 control dashboard, a live read-only waveform viewer, a localhost NDJSON loopback service, and small single-purpose CLI tools. This README is the quick-start entry point; the App itself, its node graph, and its configuration parameters are documented in [`../README.md`](../README.md).

Everything here talks to the App over Synapse Taps. The control plane uses the typed `control` tap (state and command-result snapshots); the monitor tools use read-only producer taps (`broadband_out`, `class_out`) and never send a device command, so they are safe to run alongside the dashboard while another operator owns source mode and capture.

## Install

Requires 64-bit CPython 3.13. From the repository root, create or activate a virtual environment and install this package editable:

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e apps/scifi2-hub-manager/client
```

The base install pulls in `numpy`, `protobuf`, `science-synapse` (device Taps), and `PySide6` (the GUI). The live waveform viewer additionally needs `pyqtgraph`; install it with the `waveform` extra:

```bash
.venv/Scripts/python -m pip install -e "apps/scifi2-hub-manager/client[waveform]"
```

The install exposes these console scripts (equivalent to the `run_*.py` and tool scripts in this directory):

| Command | Script | Purpose |
| --- | --- | --- |
| `scifi2-hub-manager-gui` | `run_gui.py` | Control/state dashboard |
| `scifi2-hub-manager-waveform` | `run_waveform.py` | Live waveform viewer (read-only tap; optional operator synapsectl panel) |
| `scifi2-hub-manager-service` | `run_service.py` | Localhost NDJSON loopback service |
| `scifi2-hub-manager-calibration` | `calibration_prompter.py` | Safe calibration prompter (via the service) |
| `scifi2-hub-manager-fake-demo` | `run_fake_demo.py` | Hardware-free controller smoke test |

## Verify without hardware

```bash
scifi2-hub-manager-fake-demo

# All hardware-free tests (buffer/reader/controller/service/GUI, offscreen Qt):
QT_QPA_PLATFORM=offscreen PYTHONPATH=apps/scifi2-hub-manager/client \
  .venv/Scripts/python -m unittest discover \
  -s apps/scifi2-hub-manager/client/tests -v
```

The fake demo exits after a controller/state round trip. The test suite uses no SciFi-2: taps are faked and the Qt/waveform windows run under `QT_QPA_PLATFORM=offscreen`.

## Quick start

Set the device IP once (the current bench SciFi-2 is `192.168.100.157`; query the connected device before assuming any address or peripheral id):

```bash
DEV=192.168.100.157
```

### Open the control dashboard

```bash
scifi2-hub-manager-gui --device-ip "$DEV"
# or, from this directory: python run_gui.py --device-ip "$DEV"
```

Press **Connect**. The dashboard renders whole immutable state snapshots published by the App at ~2 Hz and after each command:

- **Device** — pipeline/source connectivity and model phase/epoch/loss/accuracy;
- **Capture control** — select collection/label, toggle capture, one atomic
 **Apply target**;
- **Fit** — trigger an on-device training pass (epochs override or configured
 default);
- **Task authority** — task lifecycle, last committed boundary, and
 start/abort/reset/propose actions (reacts only to committed transitions);
- **Collections** — per-label count/capacity progress bars, with label /
 collection / all flushes (each confirmed).

The dashboard is a control and state view. It does not plot signal waveforms; use the waveform viewer below for that.

### View streaming waveforms

The waveform viewer subscribes read-only to the App's `broadband_out` producer tap and plots one live trace per channel, arranged in a grid you control. Each `BroadbandFrame` is one time sample across all channels, so the trace is the stream of `frame_data[channel]` values over a rolling time window. The tap read path issues no device command and can run at the same time as the dashboard.

The top **Device** panel adds optional operator `synapsectl` controls (like the calibration GUI): **Copy start line**, **Run: start/stop device**, and **Run: fetch info** (reports whether the App shows Running: True). The `synapsectl` command and the `start` config path are editable fields, seedable from the CLI with `--synapsectl` and `--device-config` (an empty config restarts an already-configured device). Running `synapsectl` from this operator-launched GUI is permitted under the AGENTS.md Synapse CLI execution boundary scope — a human launches and watches it, the command is shown before it runs, and the path is configurable; it never runs from a test or an agent path.

The window remembers its fields between launches in a per-user INI (`QSettings`, format `NML/WaveformViewer`; on Windows under `%APPDATA%/NML/WaveformViewer.ini`), written on close and reloaded on open, matching the calibration GUI. Persisted: device URI, config path, `synapsectl` command, grid columns, channel spec, full-scale, global and per-channel gains, timescale, and the sync-edges toggle. A field set explicitly on the command line this launch (e.g. `--device-ip`, `--columns`, `--timescale`) overrides the stored value; otherwise the stored value fills in, so a first run with no INI uses the built-in defaults.

The status line reports three sample rates for `broadband_out`: the wire-declared `sample_rate_hz` from each frame, an observed rate estimated from the device timestamps, and (when a `--device-config` is loaded) the expected rate for the selected source. The expected rate is the source's config `sample_rate_hz` **divided by the App's decimation factor** (computed from the `kApplication` `frequency_bands_hz`/guard/window/stride the same way the C++ App does), so it matches the decimated tap; a >5% observed-vs-expected gap shows a ⚠.

When `--device-config` points at a config with one or more `kBroadbandSource` nodes, a **Sources** tab strip lists them (by node id and peripheral id). Selecting a tab shows that source's expected channel metadata (count, types, source rate, and the decimated `broadband_out` rate) and drives the expected-rate comparison. All tabs currently read the single shared `broadband_out` tap; per-source taps are a future App change, so the strip documents each source's expected properties rather than switching between separate live streams. This makes the viewer extensible to multi-peripheral configs without hard-coding channel counts.

```bash
scifi2-hub-manager-waveform --device-ip "$DEV"
# or: python run_waveform.py --device-ip "$DEV"

# start directly in a 4x8 grid of all 32 channels
scifi2-hub-manager-waveform --device-ip "$DEV" --channels 0-31 --columns 8
```

> **Taskbar icon (Windows).** The GUIs set a custom taskbar icon. On native
> Windows this uses an AppUserModelID and works from PowerShell/CMD (e.g.
> `C:\...\.venv\Scripts\scifi2-hub-manager-waveform.exe`). Launched from
> **WSL**, the same command runs as a Linux process under WSLg; the app installs
> a freedesktop `.desktop` entry so WSLg *can* pick up the icon, but coverage
> depends on the WSLg version. For a guaranteed-correct taskbar icon, run the
> native Windows interpreter rather than WSL.

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--tap-name` | `broadband_out` | producer tap to plot |
| `--duration` | `2.0` | seconds of history shown per trace |
| `--sample-rate` | `20000` | expected upstream rate (time axis + buffer size) |
| `--max-channels` | `32` | maximum channels the buffer retains |
| `--columns` | `1` | initial number of grid columns |
| `--channels` | `` (all) | initial channel selection/order (see below) |
| `--full-scale` | `1000` | fixed y half-amplitude (±) at gain 1 |
| `--timescale` | `0` (= `--duration`) | initial shared x-window in seconds |

#### Change the layout live

The controls bar at the top reconfigures the grid without reconnecting:

- **Columns** — the grid width. The row count follows from the number of channels shown, so 32 channels at 8 columns is a 4×8 grid; at 4 columns, 8×4; at 1 column, a single stacked column.
- **Channels** — which channels to show and in what order, as comma- and/or space-separated tokens. Each token is a single index (`5`) or an inclusive range, ascending (`0-7`) or descending (`7-0`). Order is preserved, so the field doubles as a reordering; a channel may appear more than once. Empty shows every available channel in natural order. Examples: `0-31`, `0,4,8,12`, `0-3, 8-11`, `31-0`. **Apply** (or Enter) commits it; an out-of-range or malformed spec is reported next to the controls and leaves the current layout unchanged.
- **All** resets to every channel in one column, and the **4×8 / 8×4 / 1 col** presets are one-click arrangements of channels `0-31`.
- **Reconnect** tears down the current tap subscription and opens a fresh one, recovering from a dropped stream or a connect error without restarting the process. The rolling buffer, its history, and its integrity counters are preserved across the reconnect; only the transport is replaced.

The buffer retains every channel the stream carries (up to `--max-channels`), so hiding channels or reordering them is purely a display change and never drops data.

#### Fixed scale, gain, and timescale

Amplitude is a **fixed scale**, not auto-ranged: every plot shows the same explicit y-window so channels and time are directly comparable and the trace never jumps as the signal grows. The **Scale** sub-panel controls it (**Apply scale** or Enter commits; an invalid entry is reported next to the panel and leaves the scale unchanged):

- **Full-scale ±** — the base y half-amplitude at gain 1. A plot's y-window is `[−full_scale/gain, +full_scale/gain]`.
- **Gain ×** — a global vertical gain applied to every axis. A larger gain magnifies the trace (shrinks the y-window) within the same pixel height.
- **Per-ch gain** — per-channel gain overrides as comma- and/or space-separated `channel:gain` tokens, e.g. `0:2, 4:0.5 8:10`. An override replaces the global gain for that channel only; the gain must be positive.
- **Timescale (s)** — the shared x-window duration, applied identically to all axes (the most recent *t* seconds). It is capped at `--duration` (the buffered history); widen `--duration` to show more.

The mouse wheel over the plots adjusts the same scale live: **scroll** changes the vertical gain (up magnifies), and **Shift+scroll** changes the shared timescale (up lengthens the window, capped at `--duration`). The wheel drives the global gain and the shared timescale, so the panel's spin boxes track it; per-channel gain overrides are unaffected by the wheel.

The status line appends the active full-scale, gain (and how many per-channel overrides are set), and timescale.

The status line reports connection state, cumulative frame count, missing sequence numbers, protobuf parse errors, the observed sample rate, and how many channels are shown in how many columns — the same transport-integrity signals as `broadband_probe.py`, streamed live. In `SAMPLING` mode this is the forwarded RHD2132 stream; in `SYNTHETIC` mode it is the App's generated stream. Switch modes with `set_source_mode.py` (below).

> A producer subscription can miss frames before the subscriber is ready. Read
> the sustained count/rate and the missing-sequence counter rather than treating
> the first sample as a zero-based stream origin.

### Inspect the stream without plotting

For a bounded, scriptable read-only check of the same tap (frame count and observed rate, sequence gaps and reordering, timestamp regressions/deltas, sample rate, channel count, `channel_ranges`, malformed-payload count):

```bash
python broadband_probe.py --device-ip "$DEV" --duration 5
```

Watch the live classifier output (per-window softmax and argmax class) once an MLP is trained and running:

```bash
python listen_class.py --device-ip "$DEV"
```

## Live control tools

### GPIO sync overlays in the waveform viewer

Enable **Sync edges** to draw vertical markers on every displayed subplot:
GPIO 0 rising = cyan, falling = blue; GPIO 1 rising = orange, falling = magenta.
Detection uses GPIO channel-range metadata from the full incoming frame, even
when those channels are hidden or outside the waveform channel cap. On the
confirmed 34-channel bench stream, GPIO 0/1 occupy frame positions 32/33.
Zero is low and nonzero is high. The first observed state is a baseline, not
an edge; missing sequences, non-increasing timestamps and reconnects break
edge continuity. An edge is placed at the first observed sample of the new
state, not an interpolated physical transition time.

Traces and markers share source-frame timestamps relative to the newest frame.
The toggle controls visualization only; it does not start HDF5 recording.

These single-purpose tools drive the App's legacy consumer taps directly. The dashboard and loopback service issue the same effects through the typed control plane; use whichever fits the task.

```bash
# switch the broadband source live
python set_source_mode.py --device-ip "$DEV" synthetic   # in-app synthetic source
python set_source_mode.py --device-ip "$DEV" sampling    # real RHD2132 probe

# capture labeled feature windows (repeat per class; toggle stimulus between)
python set_capture.py --device-ip "$DEV" --label 0 --on
#   ... let windows accumulate ...
python set_capture.py --device-ip "$DEV" --label 0 --off

# train the on-device MLP over everything captured
python fit_mlp.py --device-ip "$DEV"                 # configured epochs
python fit_mlp.py --device-ip "$DEV" --epochs 200
```

## Loopback NDJSON service

For external tools, the same controller can expose a versioned NDJSON service. It binds to localhost only by default; a non-loopback `--host` is an explicit, unauthenticated operator choice.

```bash
scifi2-hub-manager-service --device-ip "$DEV" --port 8766
# or: python run_service.py --device-ip "$DEV" --port 8766
```

The service supports `get_state`, `subscribe_state`, `prepare_capture`, `select_collection`, `select_label`, `set_capture`, `fit`, and `flush`. The dependency-light socket client and the safe calibration example use it without importing the Synapse SDK in the calling tool:

```bash
python calibration_prompter.py --host 127.0.0.1 --port 8766 \
  --collection 0 --labels 0 1 2 3 4
```

The prompter queries a state snapshot, then uses one atomic `prepare_capture(..., enabled=false)` per target, enabling capture only for the prompted window and disabling it in a `finally` cleanup before the next target. It never connects to device Taps directly.

## NML_Hand_Exo control service

A separate loopback NDJSON service drives the NML Hand Exoskeleton over its dual
USB-CDC link, independent of the neural-device controller above. It owns the exo
serial transport on one worker thread (`ExoWorker`) and exposes a *position*
control model — connect/arm/home/disarm, batched `set_finger_angles` poses
(signed `[-100, 100]` per joint, rest-anchored), pose read-back, and an
inactivity watchdog that eases the hand back to neutral rest when commands stop.
This mirrors the `position` control mode of the exo SDK's `08_udp` example.

The exo SDK (`nml_hand_exo`) is an optional dependency shipped as the
`third_party/exo` submodule. Install it editable alongside this client:

```bash
git submodule update --init third_party/exo
.venv/Scripts/python -m pip install -e third_party/exo
.venv/Scripts/python -m pip install -e "apps/scifi2-hub-manager/client[exo]"
```

Run the service (auto-discovers the CDC pair by USB VID/PID, or pass both ports):

```bash
exo-service --cmd-port COM10 --telem-port COM11 --port 18766
# or: python run_exo_service.py            # auto-discover the pair
```

Commands: `exo_connect`, `exo_disconnect`, `exo_arm` (optional `home`),
`exo_disarm`, `exo_home`, `exo_set_finger_angles` (`values`: joint → signed
value or `null` to hold), `exo_read_pose`, `exo_get_state`, and
`subscribe_exo_state`. The device requires exo firmware ≥ 0.6.4 (the
`set_finger_angles` batch command); the worker reports `firmware_ok: false` and
refuses to arm below that.

The exo tests are hardware-free: they drive the real SDK against an in-process
fake transport, and skip when the SDK is not importable.

## Threading and safety contract

- Synapse Taps are only touched off the Qt thread. Both windows read immutable
 snapshots on a `QTimer`; device I/O runs on worker threads.
- The exo serial link is owned exclusively by one `ExoWorker` thread. The async
 exo service and any GUI enqueue jobs and read immutable `ExoState` snapshots;
 nothing else touches the port. Torque is applied only after the current budget
 is set, disarm releases torque on every exit path, and the watchdog returns the
 hand to neutral rest when commands stop arriving.
- The control dashboard changes targets only through the single atomic
 `prepare_capture` command and gates mutating controls while a request is pending.
- `broadband_probe.py` / `listen_class.py` are strictly read-only producers: they
 open a tap, read, and disconnect; they issue no command. The waveform viewer's
 tap path is likewise read-only; its Device panel is the one exception, running
 operator `synapsectl` start/stop/info under the AGENTS.md CLI boundary scope
 (operator-launched, command shown, path configurable; never from tests/agents).
- Raw source timestamps and sequence numbers are preserved end to end; nothing
 here substitutes a host receipt time for a source time.

## Layout

```text
client/
├── run_gui.py            # control dashboard entry point
├── run_waveform.py       # live waveform viewer entry point
├── run_service.py        # loopback NDJSON service entry point
├── run_exo_service.py    # NML_Hand_Exo dual-CDC NDJSON service entry point
├── run_fake_demo.py      # hardware-free controller smoke test
├── broadband_probe.py    # read-only frame/rate/sequence probe
├── listen_class.py       # live class_out softmax listener
├── set_source_mode.py    # sampling <-> synthetic source toggle
├── set_capture.py        # labeled capture on/off
├── fit_mlp.py            # trigger on-device MLP training
├── calibration_prompter.py
├── scifi2_hub_manager/
│   ├── controller.py     # device control plane over typed taps
│   ├── gui.py           # PySide6 control/state dashboard
│   ├── waveform.py       # WaveformBuffer + reader + pyqtgraph window
│   ├── service.py        # NDJSON loopback service
│   ├── exo_worker.py     # threaded NML_Hand_Exo owner (position control)
│   ├── exo_service.py    # NDJSON loopback service for the exo worker
│   ├── exo_transport.py  # dual-CDC comm factory + CDC-pair discovery
│   ├── client.py         # dependency-light NDJSON socket client
│   ├── model.py          # immutable state / result dataclasses
│   ├── transport.py      # replaceable Tap transport (+ fake)
│   └── proto.py
└── tests/                # hardware-free unit tests (fakes + offscreen Qt)
```

## Exo attached to SciFi-2 (wireless laptop control)

With the updated App running `config/rhd2132_with_exo.json`, launch
`gui-exo --device-ip 192.168.100.157`. The GUI connects directly to the App's
Synapse Taps; no separate bridge process or laptop COM port is needed.
For Python/other-language clients, `run_service.py` exposes the same typed
commands over loopback NDJSON; `exo-via-scifi probe` uses that service.
See the [deployment, probe and motion-test workflow](../docs/exo-integration.md).
The existing `exo-service` above is for an Exo plugged into the **laptop**;
it is a different transport placement and is not used for headstage USB.
