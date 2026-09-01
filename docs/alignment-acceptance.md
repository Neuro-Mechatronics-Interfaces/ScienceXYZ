# T-22 alignment-bound acceptance

This is the acceptance boundary for measured alignment between the Omnetics
reference stream and an auxiliary source. The repository provides the report
checker; it does not invent edge matches, repair gaps, or turn an unbounded
clock estimate into a bound.

## Required trial matrix

The default matrix matches the four-source host profile:

| Trial | Sample rate | Samples per batch |
| --- | ---: | ---: |
| 1 | 1000 Hz | 32 |
| 2 | 1500 Hz | 48 |
| 3 | 2000 Hz | 64 |
| 4 | 2500 Hz | 40 |

Each rate/batch pair is required for `loopback` (same host), `lan` (separate
networked OS), and `wireless` (the real gateway/sensor path). The shared pulse,
edge, or injected correlated waveform must have an edge record in both the
reference capture and the auxiliary capture. Use the actual configured matrix
or a focused mode gate with `--required-pairs RATE:BATCH,...` and
`--required-modes MODE,...`.

## Measurement document

The analyzer consumes a JSON document with a top-level `trials` array. Every
trial must contain identity/configuration (`trial_id`, `mode`, `source_id`,
`reference_source_id`, `sample_rate_hz`, `batch_samples`,
`expected_sample_rate_hz`, and `observed_sample_rate_hz`), provenance, loss
accounting, and one `observations` record per matched edge.

Provenance must include `config_hash`, `software_revision`,
`firmware_revision`, and `reference_peripheral_id` copied from the live device
inventory. Continuity must include
`unexplained_missing_batches`, `unexplained_missing_samples`, `duplicates`,
`reordered`, and `parse_errors`; all must be zero for acceptance.

Each edge observation records the reference-domain `reference_time_ns`, the
auxiliary edge `observed_time_ns`, the persisted `epsilon_ns`, source and batch
sequences, `source_tick`, both source/host timestamps, an explicit
`batch_latency_ns`, `clock_model_id`, `boot_session_id`, `quality`, `rtt_ns`,
and `sync_dispersion_ns`. `batch_latency_ns` must be calculated by the capture
pipeline; the checker never subtracts timestamps from unrelated clock domains.
The accepted quality values are `locked`, `degraded`, and `unbounded`.
Degraded observations remain visible in the report, while unbounded
observations fail acceptance because they cannot cover a measured error.

A minimal observation looks like this:

```json
{
  "edge_id": "pulse-0001",
  "reference_time_ns": 1000000000,
  "observed_time_ns": 1000000042,
  "epsilon_ns": 100,
  "source_tick": 500000,
  "source_sequence": 1200,
  "batch_sequence": 75,
  "host_receive_time_ns": 1000009000,
  "source_acquisition_time_ns": 1000004000,
  "batch_latency_ns": 5000,
  "clock_model_id": "wireless-sim-1000-epoch-1-model-4",
  "boot_session_id": "sensor-boot-7",
  "quality": "locked",
  "rtt_ns": 1800,
  "sync_dispersion_ns": 900
}
```

`source_sequence` and `batch_sequence` must be strictly increasing in the
observation arrival order. Because matched edges can be sparse within a raw
stream, continuity counters are the authoritative loss check for the complete
capture. Sender-reported drops may be retained as explained-loss metadata, but
the unexplained counters must remain zero.

## Run the checker

```bash
python scripts/alignment_acceptance/analyze_alignment.py data/t22-measurements.json \
  > data/t22-alignment-report.json
```

The process exits 0 only when every required mode/rate/batch combination passes, every
observed edge lies within its persisted interval
`[t_hat - epsilon, t_hat + epsilon]`, continuity is clean, and provenance is
complete. The report includes median, p95, and maximum absolute error and
epsilon, bound coverage, rate drift, RTT/dispersion, batch latency/jitter, and
locked/degraded/unbounded counts. It is safe to retain the report under an
ignored `data/` directory with the raw captures.

## Bench prerequisite and trial order

Before deployment, query the live peripheral inventory and provide the output
to the operator record. Run this exact read-only command in the supported
Synapse environment:

```bash
synapsectl -u 192.168.100.157 info
```

Use the peripheral ID reported by that command in the trial provenance and
generated deployment configuration; never copy an ID from an example or this
document. Then run, in order:

1. loopback with the shared edge and each required rate/batch pair;
2. LAN with the gateway on a separate networked OS;
3. wireless with the real sensor/gateway path.

Save the exact configuration, live `info` output, tap/source metadata, raw
captures, model epochs, analyzer report, and logs for each trial. A trial with
no matched edges, missing continuity evidence, an unexplained gap, an
out-of-bound edge, or an unbounded model is a failed/degraded result—not a
sub-millisecond claim. T-22 is complete only after these measured reports
cover the required matrix and the observed errors are covered by their
persisted bounds.
