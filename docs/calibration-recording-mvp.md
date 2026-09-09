# Calibration recording: minimum viable acceptance

Status: host raw recorder, task instructor, Reactions bridge and offline tools implemented, 2026-09-05. The operator recorded 237,360 broadband messages with zero reported failures; read-only inspection found no task events or GPIO edges. Live task/physical alignment acceptance remains open. Follow the [task recording and analysis workflow](calibration-task-workflow.md) next.

## What exists and what is missing

| Piece | Implementation evidence | Remaining work |
| --- | --- | --- |
| Waveforms | `apps/stateful-decode-and-sync/client/stateful_decode_and_sync/waveform.py` | Operator has confirmed display; display success does not establish recording continuity. |
| Device Disk Writer | `apps/stateful-decode-and-sync/config/rhd2132_mode_switch.json` | Verify actual storage target, retrieval, sample/channel schema and timestamps. This is not a host-local recording toggle. |
| Host raw/task HDF5 | `host/recording/CMakeLists.txt`, app `tools/task_recorder_main.cpp`, `src/hdf5_record_sink.cpp` | Standalone host build, exclusive creation, raw-wire batches, checked writes and loopback tests exist. Bench throughput, task-boundary joins and physical acceptance remain open. Clock history is empty without observations. |
| Authoritative epochs | `proto/gui_control.proto`, `src/task_timeline_adapter.cpp` under the app | Task events carry first-governed source sequence/timestamp. Current deployment config has no task definition. |
| Instructor/socket | `client/calibration_task.py`, `client/run_reactions_bridge.py`, `host/web/ScienceXYZTaskAdapter.js` | Terminal/website controllers correlate committed task events; deploy the generated task config and validate a live session. The older calibration_prompter controls feature capture only. |
| Digital sync | `vendor/synapse-api/api/{datatype,channel}.proto` | BroadbandFrame supports GPIO channel ranges; physical pin, firmware exposure and actual observed channels must be verified. |

## Minimum implementation order

Step 2 now has a built implementation and hardware-free tests described below. The requirements remain the acceptance checklist; live throughput and physical validation are still operator work.

1. **Prove the recording inputs.** Obtain current device inventory from the
 operator and identify the physical sync wiring. Inspect a bounded broadband capture's rate, sequence continuity and channel ranges. Observe a known physical edge in the proposed digital channel. If GPIO is not in that stream, establish its real acquisition path before promising a combined file.
2. **One host recording command.** Extend the C++ host consumer to preserve
 complete broadband frames and task events in a new, exclusive-create HDF5 file under `data/<session>/`. Retain frame data, channel types/IDs, rate, source sequence, both source timestamp fields and host receipt time. Preserve original wire payloads where decoding would otherwise lose fields. Record software/config/device provenance and explicit parse, sequence, transport, queue and write failures. Check every write result. Poll independent streams fairly without waiting 100 ms for an idle task stream on every broadband drain. Acknowledge start only after file/subscriptions are ready; stop must flush and mark incomplete tails/backlog honestly. Refuse existing paths: the sink now uses H5F_ACC_EXCL.
3. **One repeatable instructor.** Configure a small task with rest, two instructed
 actions and completion. Subscribe before starting; propose changes through the existing socket API and correlate committed events by request/session/run. Persist the task definition and state-to-label mapping. Use committed effective-frame boundaries for dataset labels. Keep capture controls for on-device feature collection separate from recording controls. On failure, disable capture and record an aborted/incomplete run.
4. **One offline diagnostic command.** Read the raw file without modifying it;
 validate schema, clocks, session/run identity and continuity. Export an epoch table and figures showing electrode traces/features, digital levels/edges, task intervals and gaps on one source-time axis. Label intervals as [start, end); require each authoritative boundary to match a recorded frame. Missing starts/ends, missing events, resets and boundary uncertainty produce explicit unknown/ambiguous regions. Never stretch an epoch across a gap.
5. **Optional baseline training.** Generate explicitly configured feature windows
 wholly inside valid labeled epochs, excluding transition margins and gaps. Persist feature settings, units, window/stride, label mapping and raw-input identity. Split by trial/run/session before fitting preprocessing; overlapping windows from one trial must not cross train/test splits. Save a simple model, held-out confusion matrix and per-class counts. Matching the on-device model additionally requires reproducing its exact feature extraction and export format; this is beyond the first diagnostic demonstration.

Python is appropriate for the offline HDF5/plotting reference pipeline; use the project's 64-bit CPython 3.13 baseline. Persistent acquisition remains C++.

## Host recorder build and use

Build from the repository root in Linux/WSL using one distribution dependency graph (verified with GCC 15.2, protobuf 3.21.12, gRPC 1.51.1 and HDF5 1.14.6):

```bash
sudo apt-get install build-essential cmake libhdf5-dev libprotobuf-dev \
  protobuf-compiler libgrpc++-dev protobuf-compiler-grpc cppzmq-dev
cmake -S host/recording -B build/raw-recorder -DCMAKE_BUILD_TYPE=Debug
cmake --build build/raw-recorder -j 6
ctest --test-dir build/raw-recorder --output-on-failure
```

This standalone build compiles read-only `vendor/synapse-cpp` sources with the canonical `vendor/synapse-api` definitions, putting generated code in the build directory. Do not mix a vcpkg protobuf with distribution gRPC/Abseil, or Windows HDF5 headers with Linux libraries. HDF5 discovery uses its C compiler wrapper instead of a potentially inherited Windows package configuration. Reconfigure after changing revisions so the embedded Git revision/dirty marker is current.

Four recorder tests use a temporary HDF5 file or a loopback-only fake gRPC/ZeroMQ service. They check exact wire/sample/channel/timestamp retention, append, overwrite refusal, malformed messages, injected write/flush failures, task boundaries, 2,000 broadband frames while task traffic is idle, known sequence loss, local stop and fatal broadband timeout. They do not establish sustained 20 kHz recording throughput or physical synchronization. A fifth CTest validates the calibration profile hash against the device-side C++ task parser.

For an **operator-run** recording, first create a new session directory and a `provenance.json` containing current operator-provided device inventory/firmware, physical peripheral IDs, the actual running configuration, task definition and label mapping (or explicit absence), wiring and observation date. Do not copy historical IDs/configuration into this file as if they were live evidence.

```bash
mkdir -p data/calibration-unique-session
cp -n config/calibration-provenance.template.json data/calibration-unique-session/provenance.json
nano data/calibration-unique-session/provenance.json
build/raw-recorder/task-recorder --device "$DEV:647" --output data/calibration-unique-session/raw.h5 --session-id calibration-unique-session --metadata-file data/calibration-unique-session/provenance.json
```

The template is valid JSON and can be used for a recording smoke test with unknowns left as `null`. These keys are a documentation convention, not an additional recorder validation schema: the recorder accepts any nonempty JSON object. `null` means unknown, not absent. Set `observation_date` and device IP; copy only currently observed inventory values into `device`. If you know which file you supplied to the start command, use `configuration_input` to record its path, SHA-256, JSON snapshot and operator evidence. This preserves the input even if the original file changes later. A current local snapshot is not proof that the file was unchanged since start. Put the actual running configuration JSON in `running_configuration` when available, rather than assuming the tracked example is deployed. Put task definition and label mapping in `task` when known; leave them unknown if not yet inspected. Describe the physical connection and expected pulse count in `physical_sync` before an edge acceptance trial. A probe can populate `broadband_probe` with its command, reported frame/rate/channel counts and continuity counters; it cannot establish device firmware, physical wiring, task configuration or timing precision.

Metadata is copied into HDF5 when the file opens. Later edits to provenance.json do not alter that recording's embedded metadata. Complete it before recording or retain any later corrections as a separate, dated sidecar without editing raw data. Unknown fields do not block a smoke test, but remain outstanding evidence for calibration acceptance. No software rebuild is needed after metadata edits.

`DEV` is the current device IP without a port. Both named producer taps must already exist. The recorder queries/connects using synapse-cpp; it does not configure or start the device. Agents do not run this command against hardware. Start the task only after the recorder prints its recording acknowledgment. That acknowledgment confirms file creation and local tap connections; Synapse PUB/SUB does not expose a publisher subscription barrier. Startup loss remains unknown and the recorded task start must be verified after acquisition. Ctrl-C requests a local stop; default broadband silence timeout is 5,000 ms, configurable with `--idle-timeout-ms`. Existing output paths are refused. Tap discovery/connect has a 10-second deadline per tap; a stuck upstream query terminates the process before any output file is opened. Metadata must be a nonempty JSON object; its contents are stored verbatim, not verified as live device evidence. The program also stores its build revision, tap names, source label, device URI and host receipt clock identity.

### Raw file contract

Root `raw_schema=sciencexyz.raw_taps.v1` accompanies the existing task timeline schema. `/raw_broadband` and `/raw_task` are append-only compound datasets with `host_receive_time_ns` (uint64) and `payload` (variable-length uint8 bytes). A dataset is absent if no messages arrived. Decode the latter with `synapse.BroadbandFrame` or `stateful_decode_and_sync.v1.TaskTransitionEvent`. Original wire bytes retain every sample, channel range/type/ID, sample rate, sequence, both source timestamp fields and unknown protobuf fields. Malformed and empty wire messages are retained too; they are never replaced by guessed samples. Root `metadata_json` and `provenance_*` attributes provide context.

`/events`, `/transitions`, `/intervals`, `/diagnostics` and `/clock_epochs` retain the existing derived timeline format. No clock observations are currently fed, so an empty clock history is expected. Derived intervals require an offline frame-identity and gap join before use as labels, particularly when events arrive after their reference frames. `timeline_complete` describes the timeline value object, not end-to-end recording completeness.

The poll loop alternates batches of at most 256 messages per tap, using zero read timeout and a 1 ms sleep only if neither stream supplied data. Every raw batch and timeline write/flush is checked. Non-timeout transport errors, write failures and broadband silence terminate with nonzero exit. Parse failures and sequence gaps are retained as explicit diagnostics/counters; normal local stop can still exit zero with known loss, which is **not** calibration acceptance. Stop control-event discarded counts refer to read batches whose persistence could not be confirmed; malformed but successfully stored wire is counted in the separate parse-error counters.

`recording_status_json` begins as incomplete, is marked finalizing before the stop snapshot, and becomes stopped only after the stop event is flushed. It records raw counts, parse/rejection counts, sequence gaps/regressions, discontinuities, transport and write errors. A failed write may prevent even the failure record from reaching disk; process exit/stderr are part of acceptance. PUB/SUB supplies no end barrier or exact queue-drop count: `head_complete=false`, `tail_complete=false`, `queue_loss_known=false` and the stop event's `discarded_counts_complete=false` remain honest unknowns. HDF5 flush is not a power-loss/crash-recovery guarantee. Require the local stop acknowledgment, successful close, a fresh-process reopen and the bounded task/edge acceptance checks below; never infer complete acquisition from a file merely opening.

## Timing claims

A committed task boundary establishes which acquired frame first belongs to a task state. It does not measure when a display physically illuminated or when a participant moved. Persist instructor request/presentation timestamps with their host clock identity separately. For measured stimulus timing, record an actual trigger/photodiode edge. For another host or wireless source, retain its native timestamps and clock mapping/uncertainty; socket receipt time alone is not synchronization. Do not populate clock history with an unmeasured mapping.

For the first demo, use the existing loopback task socket and one physical sync input. Add independent external sources after this acceptance passes. The recording command is the initial proposed trigger; a GUI button can invoke the same recording lifecycle once the backend is proven.

## Acceptance run

- Start recording before the task; verify frames are reaching disk.
- Run at least three repetitions of rest and each of two actions; inject known
 sync edges with recorded expected count/order. Finish the task before stopping.
- Reopen the file in a fresh process. Confirm sample payloads, GPIO identity and
 observed edges, all task events, provenance and a clean stop marker.
- Require zero unexplained gaps for a successful calibration run. Explicitly
 report known loss and reject affected training windows; a file merely opening is insufficient. Confirm each epoch boundary's sequence and timestamp exist.
- Produce the epoch CSV plus an annotated figure directly from the recording.
 Report measured edge alignment error and uncertainty when an external clock is involved; do not claim a numerical precision before measuring it.
- Exercise a disconnect and attempted overwrite: recording must visibly fail or
 be marked incomplete, and the original raw file must remain untouched.

## First bench evidence

Follow-up operator probe: 99,121 frames in 5.004 s; no missing or non-monotonic sequences, no parse errors, 20 kHz, 34 channels, `ELECTRODE:32` and `GPIO:2 ids=[0,1]`. Operator confirms sync may use GPIO 0 or 1 (frame positions 32/33). Timestamp deltas were 520..17,881,198 ns, mean 50,020.9 ns, with no regressions. Thus sequence continuity passed this bounded trial; uniform timestamp spacing and physical edge timing have not been established. The waveform viewer now offers both-polarity GPIO overlays on a source-time axis, covered by buffer and offscreen GUI tests. Physical-edge validation and bench recording acceptance remain open.

Operator-provided inventory (2026-09-05): device SFI2-0-260534 is running, Synapse 2.4.1 / firmware 3164583911. Physical IntanRHD2132 ID 200 is connected to BroadbandSource node 1 at 20,000 Hz / 16 bits. The CLI lists 34 channels, including two `?` entries; this is not sufficient evidence of GPIO identity. The running configuration lists source and application only, with no Disk Writer. Disk ID 1 and SD card ID 2 are available storage, not proof of active recording. The displayed July logs are historical, not current continuity evidence. The checked-in configuration therefore does not describe the full live channel layout and must not be reapplied blindly.

Next read-only operator probe from the repository root:

```bash
python apps/stateful-decode-and-sync/client/broadband_probe.py --device-ip "$DEV" --duration 5
```

Inspect actual `channel_ranges`, sample rate, channel count and loss counters. GPIO channel identification still requires the operator's physical pin/wiring description and a measured edge; the probe alone does not establish edge timing.

The operator runs `synapsectl -u 192.168.100.157 info` and supplies the output. Use its current physical peripheral IDs and firmware/configuration evidence; do not infer them from historical handoffs. Subsequent CLI commands depend on the supported installed syntax and this result. AGENTS.md prohibits agents from executing synapsectl or substituting direct device control.
