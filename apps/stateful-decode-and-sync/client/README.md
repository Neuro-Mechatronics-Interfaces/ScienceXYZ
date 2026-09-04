# Stateful Decode and Sync — host client

Host-side control and monitoring for the `stateful-decode-and-sync` Synapse App on the SciFi-2: a PySide6 control dashboard, a live read-only waveform viewer, a localhost NDJSON loopback service, and small single-purpose CLI tools. This README is the quick-start entry point; the App itself, its node graph, and its configuration parameters are documented in [`../README.md`](../README.md).

Everything here talks to the App over Synapse Taps. The control plane uses the typed `control` tap (state and command-result snapshots); the monitor tools use read-only producer taps (`broadband_out`, `class_out`) and never send a device command, so they are safe to run alongside the dashboard while another operator owns source mode and capture.

## Install

Requires 64-bit CPython 3.13. From the repository root, create or activate a virtual environment and install this package editable:

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -e apps/stateful-decode-and-sync/client
```

The base install pulls in `numpy`, `protobuf`, `science-synapse` (device Taps), and `PySide6` (the GUI). The live waveform viewer additionally needs `pyqtgraph`; install it with the `waveform` extra:

```bash
.venv/Scripts/python -m pip install -e "apps/stateful-decode-and-sync/client[waveform]"
```

The install exposes these console scripts (equivalent to the `run_*.py` and tool scripts in this directory):

| Command | Script | Purpose |
| --- | --- | --- |
| `stateful-decode-and-sync-gui` | `run_gui.py` | Control/state dashboard |
| `stateful-decode-and-sync-waveform` | `run_waveform.py` | Live read-only waveform viewer |
| `stateful-decode-and-sync-service` | `run_service.py` | Localhost NDJSON loopback service |
| `stateful-decode-and-sync-calibration` | `calibration_prompter.py` | Safe calibration prompter (via the service) |
| `stateful-decode-and-sync-fake-demo` | `run_fake_demo.py` | Hardware-free controller smoke test |

## Verify without hardware

```bash
stateful-decode-and-sync-fake-demo

# All hardware-free tests (buffer/reader/controller/service/GUI, offscreen Qt):
QT_QPA_PLATFORM=offscreen PYTHONPATH=apps/stateful-decode-and-sync/client \
  .venv/Scripts/python -m unittest discover \
  -s apps/stateful-decode-and-sync/client/tests -v
```

The fake demo exits after a controller/state round trip. The test suite uses no SciFi-2: taps are faked and the Qt/waveform windows run under `QT_QPA_PLATFORM=offscreen`.

## Quick start

Set the device IP once (the current bench SciFi-2 is `192.168.100.157`; query the connected device before assuming any address or peripheral id):

```bash
DEV=192.168.100.157
```

### Open the control dashboard

```bash
stateful-decode-and-sync-gui --device-ip "$DEV"
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

The waveform viewer subscribes read-only to the App's `broadband_out` producer tap and plots one live trace per channel, arranged in a grid you control. Each `BroadbandFrame` is one time sample across all channels, so the trace is the stream of `frame_data[channel]` values over a rolling time window. It sends no device command and can run at the same time as the dashboard.

```bash
stateful-decode-and-sync-waveform --device-ip "$DEV"
# or: python run_waveform.py --device-ip "$DEV"

# start directly in a 4x8 grid of all 32 channels
stateful-decode-and-sync-waveform --device-ip "$DEV" --channels 0-31 --columns 8
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--tap-name` | `broadband_out` | producer tap to plot |
| `--duration` | `2.0` | seconds of history shown per trace |
| `--sample-rate` | `20000` | expected upstream rate (time axis + buffer size) |
| `--max-channels` | `32` | maximum channels the buffer retains |
| `--columns` | `1` | initial number of grid columns |
| `--channels` | `` (all) | initial channel selection/order (see below) |

#### Change the layout live

The controls bar at the top reconfigures the grid without reconnecting:

- **Columns** — the grid width. The row count follows from the number of channels shown, so 32 channels at 8 columns is a 4×8 grid; at 4 columns, 8×4; at 1 column, a single stacked column.
- **Channels** — which channels to show and in what order, as comma- and/or space-separated tokens. Each token is a single index (`5`) or an inclusive range, ascending (`0-7`) or descending (`7-0`). Order is preserved, so the field doubles as a reordering; a channel may appear more than once. Empty shows every available channel in natural order. Examples: `0-31`, `0,4,8,12`, `0-3, 8-11`, `31-0`. **Apply** (or Enter) commits it; an out-of-range or malformed spec is reported next to the controls and leaves the current layout unchanged.
- **All** resets to every channel in one column, and the **4×8 / 8×4 / 1 col** presets are one-click arrangements of channels `0-31`.

The buffer retains every channel the stream carries (up to `--max-channels`), so hiding channels or reordering them is purely a display change and never drops data.

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
stateful-decode-and-sync-service --device-ip "$DEV" --port 8766
# or: python run_service.py --device-ip "$DEV" --port 8766
```

The service supports `get_state`, `subscribe_state`, `prepare_capture`, `select_collection`, `select_label`, `set_capture`, `fit`, and `flush`. The dependency-light socket client and the safe calibration example use it without importing the Synapse SDK in the calling tool:

```bash
python calibration_prompter.py --host 127.0.0.1 --port 8766 \
  --collection 0 --labels 0 1 2 3 4
```

The prompter queries a state snapshot, then uses one atomic `prepare_capture(..., enabled=false)` per target, enabling capture only for the prompted window and disabling it in a `finally` cleanup before the next target. It never connects to device Taps directly.

## Threading and safety contract

- Synapse Taps are only touched off the Qt thread. Both windows read immutable
  snapshots on a `QTimer`; device I/O runs on worker threads.
- The control dashboard changes targets only through the single atomic
  `prepare_capture` command and gates mutating controls while a request is
  pending.
- The waveform viewer and `broadband_probe.py` / `listen_class.py` are strictly
  read-only producers: they open a tap, read, and disconnect; they issue no
  command.
- Raw source timestamps and sequence numbers are preserved end to end; nothing
  here substitutes a host receipt time for a source time.

## Layout

```text
client/
├── run_gui.py            # control dashboard entry point
├── run_waveform.py       # live waveform viewer entry point
├── run_service.py        # loopback NDJSON service entry point
├── run_fake_demo.py      # hardware-free controller smoke test
├── broadband_probe.py    # read-only frame/rate/sequence probe
├── listen_class.py       # live class_out softmax listener
├── set_source_mode.py    # sampling <-> synthetic source toggle
├── set_capture.py        # labeled capture on/off
├── fit_mlp.py            # trigger on-device MLP training
├── calibration_prompter.py
├── stateful_decode_and_sync/
│   ├── controller.py     # device control plane over typed taps
│   ├── gui.py           # PySide6 control/state dashboard
│   ├── waveform.py       # WaveformBuffer + reader + pyqtgraph window
│   ├── service.py        # NDJSON loopback service
│   ├── client.py         # dependency-light NDJSON socket client
│   ├── model.py          # immutable state / result dataclasses
│   ├── transport.py      # replaceable Tap transport (+ fake)
│   └── proto.py
└── tests/                # hardware-free unit tests (fakes + offscreen Qt)
```
