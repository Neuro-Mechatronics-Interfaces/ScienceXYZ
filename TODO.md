# TODO

The initial goals are:

1. Reliably discover, configure, and stream from the SciFi-2.
2. Develop C++ Synapse Apps for on-device neural signal processing.
3. Develop a C++ host application that receives Synapse Taps and merges them
   with additional experimental sensor streams.
4. Establish explicit timestamping, synchronization, logging, and provenance
   conventions before building experiment-specific applications.


## Synapse Bring-Up

The basic hardware smoke test is:

```
synapsectl -u 10.0.0.15 info
```

Before proceeding with an actual peripheral signal chain:

1. Confirm the SciFi-2 responds.
2. Record the reported firmware/Synapse versions.
3. Confirm the Axon adapter appears under Peripherals.
4. Record its reported peripheral ID and type.
5. Keep the signal chain stopped while modifying configuration.

Do not assume example peripheral IDs apply to physical hardware. In particular, IDs used by virtual/simulator configurations are not authoritative for the attached Axon adapter.

## Synapse Apps

Start custom App work from Science's current `synapse-example-app` rather than inventing the Docker/CMake/package structure.

Keep the first custom App intentionally simple:

```
kBroadbandSource -> kApplication -> Tap
```

The initial App should demonstrate:

- receiving BroadbandFrame data;
- preserving source timestamps and sequence numbers;
- detecting/reporting dropped frames;
- publishing a simple derived or diagnostic Tap;
- clean start/stop behavior.

Only after that path is reliable should filtering, spike detection, decoding, or inference be added.

Do not add electrical or optical stimulation behavior unless explicitly
requested. Recording and stimulation should remain separate concerns during
initial infrastructure development.

## Initial Definition of Done

The first repository milestone is complete when:

1. The SciFi-2 and Axon adapter are reproducibly discoverable.
2. An official/example Synapse App can be built and deployed.
3. A small custom C++ Synapse App can publish a Tap.
4. A C++ host program can consume that Tap.
5. One independent wireless test source can stream into the same host program.
6. Both streams are logged with explicit source and host timestamps.
7. Synchronization offset/drift diagnostics are recorded alongside the data.