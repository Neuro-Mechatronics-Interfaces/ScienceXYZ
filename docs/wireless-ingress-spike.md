# Hardware-free wireless ingress spike

The first implementation of the external gateway boundary is the SDK-independent
`scifi2_hub::wireless::IngressMux` in
[`apps/scifi2-hub-manager/src/wireless_ingress.hpp`](../apps/scifi2-hub-manager/src/wireless_ingress.hpp).
It is intentionally driven by an injected `NonblockingReader`, so deterministic
tests can exercise the complete acceptance and loss-accounting path without a
SciFi-2, wireless sensor, or live LAN publisher.

## Ownership and scheduling

The mux takes one `std::unique_ptr<NonblockingReader>` per configured source and
requires one to four readers. The mux is the sole owner/caller of each reader.
`poll_once()` calls each reader exactly once, beginning at a rotating cursor, and
admits at most one nonblocking multipart message per reader. Accepted batches are
held in per-source bounded queues and are returned by `pop_next()` in source
round-robin order. Both the per-source and aggregate queue capacities are
enforced; a valid batch that cannot be queued produces a `kQueueOverflow`
diagnostic and increments the source drop counter.

The transport boundary is the exact v1 envelope: two frames containing the
configured topic and serialized `sciencexyz.wireless.v1.WirelessBatch`. The
validator preserves the parsed protobuf and topic by value after checking
contract version, configured source/gateway identity, shape, format, payload
length, rational rate, source tick frequency, replay policy, and configured
limits.

[`wireless_zmq_reader.hpp`](../apps/scifi2-hub-manager/src/wireless_zmq_reader.hpp)
provides the production transport boundary used by the app target. Each
`ZmqNonblockingReader` owns one SUB socket, subscribes to one exact topic, and
retains at most three frames while draining the complete multipart message.
`connect_next()` is an explicit endpoint-failover operation; reconnect policy
and diagnostics remain above the transport boundary.

## Diagnostics

`drain_diagnostics()` exposes bounded diagnostic history. Counters remain in
`source_stats()` after history is drained. The implementation distinguishes
sender-reported drops, receiver-observed batch/sample gaps, duplicates/replays,
reordering, boot-session resets, malformed envelopes/batches, source rejection,
and queue overflow. `kDiagnoseAndPreserve` keeps valid gapped batches; the
explicit `kRejectSourceOnGap` policy quarantines a source until a new
`boot_session_id` arrives.

The test target is generated from the canonical proto rather than a handwritten
message substitute:

```bash
cmake -S apps/scifi2-hub-manager -B build/scifi2-hub-manager \
  -DBUILD_DEVICE_APP=OFF -DBUILD_TESTING=ON
cmake --build build/scifi2-hub-manager \
  --target scifi2-hub-manager-wireless-ingress-tests
ctest --test-dir build/scifi2-hub-manager -R wireless-ingress --output-on-failure
```

The current Windows shell does not have CMake/protoc installed; run these
commands in the supported Linux/macOS or WSL development environment.

## Clock mapping and four-source simulation

`src/clock_estimator.{hpp,cpp}` implements the T-21 affine map
`t_ref = a * t_source + b`. It rejects reordered sync observations, recognizes
configured source-clock wraps, requires an explicit new epoch for a reset, and
retains completed model epochs. A locked model records slope/offset, drift,
sample count, RTT statistics, residuals, validity, and a decomposed epsilon
budget. `MappedTime::interval` exposes the conservative
`[t_hat-epsilon, t_hat+epsilon]`; before two ordered sync observations, and
after the configured model age, mapping is explicitly unbounded.

`src/wireless_source_adapter.{hpp,cpp}` is the T-25 reusable normalizer. It
accepts value-owned `AcceptedBatch` objects, performs a bounded contract check,
detects duplicate/gap/reorder/session conditions, preserves the complete
source/gateway protobuf metadata, adds the host receipt stamp, and derives
per-sample source ticks and optional affine intervals. `MultiSourceAdapter`
owns one to four configured instances and adds aggregate queue scheduling
without copying source-specific implementations. `FourSourceAdapter` remains a
source-compatible alias for existing four-source callers.

`src/wireless_simulator.{hpp,cpp}` emits the same `AcceptedBatch` shape for one
to four independent rates. Its deterministic controls cover clock drift,
gateway jitter, batch loss, adjacent reordering, and a boot-session reset. The
fixture can also carry an explicit topic, source time domain, and channel
descriptors so recorder-boundary tests exercise units and channel ordering. It
never repairs a gap or replaces a source/gateway timestamp with the host
timestamp. `FourSourceSimulator` remains a source-compatible alias.

The application Docker build uses the app directory as its context. Therefore
`proto/wireless/v1/wireless_batch.proto` is a context-local mirror of the
repository-level contract in `protocol/wireless/v1/`; CMake uses the local copy
so `synapsectl apps build` can generate bindings inside the image.

The reproducible four-source settings are in
[`config/host/wireless_four_source_simulator.json`](../config/host/wireless_four_source_simulator.json).
This is a host-side profile for the simulator and `FourSourceAdapter`, not a
deployable Synapse device configuration. It deliberately contains no physical
peripheral ID or live gateway endpoint. Replace only the source identities and
gateway transport settings when connecting real external gateways, while
retaining the explicit source/gateway/host timestamp fields and diagnostics.

For the smallest currently checked-in mixed-source setup, use
[`config/host/rhd2132_plus_one_wireless.json`](../config/host/rhd2132_plus_one_wireless.json).
It combines the existing RHD2132 app-config reference with one `emg-left`
WirelessBatch v1 source and the same affine-clock/diagnostic policy. The file
is an orchestration profile for host fusion; it is not a `synapsectl start`
input because the external wireless source is not a Synapse graph node in the
supported topology. Resolve the actual RHD2132 peripheral identity from live
`synapsectl info` output before deployment.

T-35 does not add a deployable two-wireless profile yet. The repository has no
confirmed EMG/IMU logical IDs, exact topics/endpoints, gateway/session/clock
identities, wire formats, batch sizes, source tick domains, units, or
quaternion component order. Adding values from the simulator or the existing
one-source example would create a misleading hardware profile. The required
confirmation checklist and the intended host-only profile boundary are tracked
in [`t35-two-wireless-profile-gate.md`](t35-two-wireless-profile-gate.md).

For the measured cross-source acceptance gate, use the JSON report checker and
bench procedure in [`alignment-acceptance.md`](alignment-acceptance.md). It
requires explicit edge observations, continuity counters, persisted epsilon,
and live-device provenance; it does not infer missing measurements.
