# broadband-diagnostic-app

Project-owned Synapse App that characterizes the broadband transport and timing
path before any signal processing is added. It is intentionally a diagnostic,
not a decoder.

Status: **not yet implemented.** This README fixes the intent and contract; the
build/source will be scaffolded from Science's `apps/synapse-example-app`
structure (do not modify the example app itself — see `AGENTS.md`).

## Purpose

Conceptual graph:

```
BroadbandFrame
      │
      ├── preserve sequence number
      ├── preserve source + app timestamps
      ├── detect sequence gaps / dropped frames
      ├── estimate frame rate and sample rate
      └── publish diagnostics
```

Develop and validate against the working **SciFi Virtual Recording Peripheral
(ID 1000, `kBroadbandSource`)** so the App can be characterized without the NML
Bridge hardware.

## Planned outputs

- stream statistics Tap: frames received, samples received, last sequence
  number, sequence gaps, dropped frames, source timestamp, app timestamp,
  elapsed source time, estimated frame/sample rate;
- app logs;
- optionally, republished broadband data if useful downstream.

## Non-goals (for now)

No filtering, spike detection, decoding, or inference until the transport and
timestamp path is well characterized.

## Relationship to the example app

`apps/synapse-example-app` is the upstream reference and remains read-only. This
App reuses its Docker/CMake/packaging structure but lives in its own directory
with its own manifest and node graph.
