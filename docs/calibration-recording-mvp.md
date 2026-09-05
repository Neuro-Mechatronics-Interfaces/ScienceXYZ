# Calibration recording: minimum viable acceptance

Status: planning and implementation audit, 2026-09-05. The operator confirms
the waveforms GUI works. End-to-end recording and alignment remain unverified.

## What exists and what is missing

| Piece | Implementation evidence | Remaining work |
| --- | --- | --- |
| Waveforms | `apps/stateful-decode-and-sync/client/stateful_decode_and_sync/waveform.py` | Operator has confirmed display; display success does not establish recording continuity. |
| Device Disk Writer | `apps/stateful-decode-and-sync/config/rhd2132_mode_switch.json` | Verify actual storage target, retrieval, sample/channel schema and timestamps. This is not a host-local recording toggle. |
| Host task HDF5 | `apps/stateful-decode-and-sync/tools/task_recorder_main.cpp`, `src/hdf5_record_sink.cpp` | Build gate remains; consumer checks broadband continuity but does **not** persist sample payloads. Clock estimator is constructed without observations. |
| Authoritative epochs | `proto/gui_control.proto`, `src/task_timeline_adapter.cpp` under the app | Task events carry first-governed source sequence/timestamp. Current deployment config has no task definition. |
| Instructor/socket | `client/calibration_prompter.py`, `client/stateful_decode_and_sync/client.py` under the app | Existing prompter controls feature capture, not task epochs. Socket client already exposes task commands/subscriptions. |
| Digital sync | `vendor/synapse-api/api/{datatype,channel}.proto` | BroadbandFrame supports GPIO channel ranges; physical pin, firmware exposure and actual observed channels must be verified. |

## Minimum implementation order

1. **Prove the recording inputs.** Obtain current device inventory from the
   operator and identify the physical sync wiring. Inspect a bounded broadband
   capture's rate, sequence continuity and channel ranges. Observe a known
   physical edge in the proposed digital channel. If GPIO is not in that
   stream, establish its real acquisition path before promising a combined file.
2. **One host recording command.** Extend the C++ host consumer to preserve
   complete broadband frames and task events in a new, exclusive-create HDF5
   file under `data/<session>/`. Retain frame data, channel types/IDs, rate,
   source sequence, both source timestamp fields and host receipt time. Preserve
   original wire payloads where decoding would otherwise lose fields. Record
   software/config/device provenance and explicit parse, sequence, transport,
   queue and write failures. Check every write result. Poll independent streams
   fairly without waiting 100 ms for an idle task stream on every broadband
   drain. Acknowledge start only after file/subscriptions are ready; stop must
   flush and mark incomplete tails/backlog honestly. Refuse existing paths:
   the current task sink uses H5F_ACC_TRUNC and must change before raw use.
3. **One repeatable instructor.** Configure a small task with rest, two instructed
   actions and completion. Subscribe before starting; propose changes through
   the existing socket API and correlate committed events by request/session/run.
   Persist the task definition and state-to-label mapping. Use committed
   effective-frame boundaries for dataset labels. Keep capture controls for
   on-device feature collection separate from recording controls. On failure,
   disable capture and record an aborted/incomplete run.
4. **One offline diagnostic command.** Read the raw file without modifying it;
   validate schema, clocks, session/run identity and continuity. Export an epoch
   table and figures showing electrode traces/features, digital levels/edges,
   task intervals and gaps on one source-time axis. Label intervals as
   [start, end); require each authoritative boundary to match a recorded frame.
   Missing starts/ends, missing events, resets and boundary uncertainty produce
   explicit unknown/ambiguous regions. Never stretch an epoch across a gap.
5. **Optional baseline training.** Generate explicitly configured feature windows
   wholly inside valid labeled epochs, excluding transition margins and gaps.
   Persist feature settings, units, window/stride, label mapping and raw-input
   identity. Split by trial/run/session before fitting preprocessing; overlapping
   windows from one trial must not cross train/test splits. Save a simple model,
   held-out confusion matrix and per-class counts. Matching the on-device model
   additionally requires reproducing its exact feature extraction and export
   format; this is beyond the first diagnostic demonstration.

Python is appropriate for the offline HDF5/plotting reference pipeline; use the
project's 64-bit CPython 3.13 baseline. Persistent acquisition remains C++.

## Timing claims

A committed task boundary establishes which acquired frame first belongs to a
task state. It does not measure when a display physically illuminated or when
a participant moved. Persist instructor request/presentation timestamps with
their host clock identity separately. For measured stimulus timing, record an
actual trigger/photodiode edge. For another host or wireless source, retain its
native timestamps and clock mapping/uncertainty; socket receipt time alone is
not synchronization. Do not populate clock history with an unmeasured mapping.

For the first demo, use the existing loopback task socket and one physical
sync input. Add independent external sources after this acceptance passes.
The recording command is the initial proposed trigger; a GUI button can invoke
the same recording lifecycle once the backend is proven.

## Acceptance run

- Start recording before the task; verify frames are reaching disk.
- Run at least three repetitions of rest and each of two actions; inject known
  sync edges with recorded expected count/order. Finish the task before stopping.
- Reopen the file in a fresh process. Confirm sample payloads, GPIO identity and
  observed edges, all task events, provenance and a clean stop marker.
- Require zero unexplained gaps for a successful calibration run. Explicitly
  report known loss and reject affected training windows; a file merely opening
  is insufficient. Confirm each epoch boundary's sequence and timestamp exist.
- Produce the epoch CSV plus an annotated figure directly from the recording.
  Report measured edge alignment error and uncertainty when an external clock
  is involved; do not claim a numerical precision before measuring it.
- Exercise a disconnect and attempted overwrite: recording must visibly fail or
  be marked incomplete, and the original raw file must remain untouched.

## First bench evidence

Follow-up operator probe: 99,121 frames in 5.004 s; no missing or non-monotonic
sequences, no parse errors, 20 kHz, 34 channels, `ELECTRODE:32` and
`GPIO:2 ids=[0,1]`. Operator confirms sync may use GPIO 0 or 1 (frame positions
32/33). Timestamp deltas were 520..17,881,198 ns, mean 50,020.9 ns, with no
regressions. Thus sequence continuity passed this bounded trial; uniform
timestamp spacing and physical edge timing have not been established.
The waveform viewer now offers both-polarity GPIO overlays on a source-time
axis, covered by buffer and offscreen GUI tests. Physical-edge validation and
HDF5 recording remain open.

Operator-provided inventory (2026-09-05): device SFI2-0-260534 is running,
Synapse 2.4.1 / firmware 3164583911. Physical IntanRHD2132 ID 200 is connected
to BroadbandSource node 1 at 20,000 Hz / 16 bits. The CLI lists 34 channels,
including two `?` entries; this is not sufficient evidence of GPIO identity.
The running configuration lists source and application only, with no Disk
Writer. Disk ID 1 and SD card ID 2 are available storage, not proof of active
recording. The displayed July logs are historical, not current continuity
evidence. The checked-in configuration therefore does not describe the full
live channel layout and must not be reapplied blindly.

Next read-only operator probe from the repository root:

```bash
python apps/stateful-decode-and-sync/client/broadband_probe.py --device-ip "$DEV" --duration 5
```

Inspect actual `channel_ranges`, sample rate, channel count and loss counters.
GPIO channel identification still requires the operator's physical pin/wiring
description and a measured edge; the probe alone does not establish edge timing.

The operator runs `synapsectl -u 192.168.100.157 info` and supplies the output.
Use its current physical peripheral IDs and firmware/configuration evidence;
do not infer them from historical handoffs. Subsequent CLI commands depend on
the supported installed syntax and this result. AGENTS.md prohibits agents
from executing synapsectl or substituting direct device control.
