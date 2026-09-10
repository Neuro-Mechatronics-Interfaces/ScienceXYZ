# T-35 two-wireless host profile gate

T-35's runtime support is ready for a bounded two-source host configuration,
but the deployable profile is intentionally gated on field confirmation. The
external sources remain host-side; the SciFi-2 device start input remains
`apps/scifi2-hub-manager/config/rhd2132.json`.

Before creating `config/host/rhd2132_plus_two_wireless.json`, record these
facts from the actual gateway/source pair and the live SciFi-2 inventory:

| Field | EMG | IMU |
| --- | --- | --- |
| stable logical `source_id` | pending | pending |
| exact WirelessBatch topic | pending | pending |
| reader endpoint(s) | pending | pending |
| gateway ID/session/clock identity | pending | pending |
| sample format and byte order | pending | pending |
| samples per batch | pending | pending |
| source tick frequency/domain | pending | pending |
| channel units and descriptor labels | pending | pending |
| quaternion component order | not applicable | pending |

The target shape is EMG: 8 channels at 2048 Hz, and IMU: 10 channels at 128
Hz ordered accel x/y/z, gyro x/y/z, and four quaternion components. Those
rates, counts, and ordering are design targets supplied by T-35, not evidence
that the current wireless hardware already emits that contract. The simulator
test uses `sim-emg-8ch` and `sim-imu-10ch`, synthetic endpoints/identities,
and fixture-only batch sizes; none may be copied into the hardware profile.

The final host profile must preserve, per source and batch:

- the exact source payload, source ID, boot session, batch/sample sequences,
  source tick and acquisition timestamp;
- gateway ID/session/clock and gateway receive/send timestamps;
- host receive timestamp, aligned sample intervals, clock-model identity and
  uncertainty; and
- sender-reported and receiver-observed gaps, duplicates, reorderings,
  reconnects, and queue loss.

After confirmation, validate the profile against
`config/wireless-gateway.schema.json`, configure one `MultiSourceAdapter` with
the two source contracts, and keep the profile host-only. Do not pass the
composite profile to `synapsectl start`.
