# Hardware-free wireless ingress spike

The first implementation of the external gateway boundary is the SDK-independent
`app::wireless::IngressMux` in
[`apps/stateful-decode-and-sync/src/wireless_ingress.hpp`](../apps/stateful-decode-and-sync/src/wireless_ingress.hpp).
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

[`wireless_zmq_reader.hpp`](../apps/stateful-decode-and-sync/src/wireless_zmq_reader.hpp)
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
cmake -S apps/stateful-decode-and-sync -B build/stateful-decode-and-sync \
  -DBUILD_DEVICE_APP=OFF -DBUILD_TESTING=ON
cmake --build build/stateful-decode-and-sync \
  --target stateful-decode-and-sync-wireless-ingress-tests
ctest --test-dir build/stateful-decode-and-sync -R wireless-ingress --output-on-failure
```

The current Windows shell does not have CMake/protoc installed; run these
commands in the supported Linux/macOS or WSL development environment.
