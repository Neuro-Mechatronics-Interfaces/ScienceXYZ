# Axon Custom-Peripheral Protocol — Engineering Reference

Scope: the parts of Science's public custom-peripheral interface that the NML
Synapse Bridge actually needs. Every claim is tagged:

- **[PROVEN]** — directly evidenced by vendored code / protobuf definitions.
- **[DOC]** — stated by Science documentation (vendored READMEs).
- **[INFER]** — reasonable inference from the above; not directly asserted.
- **[UNKNOWN]** — not answerable from vendored material; needs a hardware
  experiment or a question to Science.

Primary oracle: `vendor/axon-peripheral-example/` (Science's
`axon_test_source` reference peripheral). Protocol/message definitions:
`vendor/synapse-api/`.

---

## 0. The headline finding (read this first)

A "custom Axon peripheral" in Science's public reference is **not** an external
MCU that speaks a USB wire protocol to the SciFi. It is a **two-part plugin
that runs inside the SciFi headstage itself**:

1. **FPGA gateware** — a SystemVerilog peripheral module compiled into the
   SciFi's on-board **Lattice FPGA** (target profile `via-devkit`, Radiant
   2024.2), packaged as a `.bit` and installed to
   `/usr/lib/scifi/gateware/<name>.bit`.
   [PROVEN: `src/gateware/peripheral.yaml:6`, `Dockerfiles/gateware.Dockerfile:6-11`,
   `README.md:14-21`]
2. **Host driver plugin** — a C++ `.so` inheriting the SDK's
   `RecordPluginWithLimits`, cross-compiled for ARM64 and installed to
   `/usr/lib/scifi/plugins/<name>.so`. `scifi-server` scans that directory at
   startup, `dlopen`s each plugin, and dispatches matching peripheral IDs to
   its factory.
   [PROVEN: `README.md:16-21`, `src/driver/axon_test_source_plugin.cpp:14-18`,
   `vendor/synapse-python/synapse/cli/peripherals.py:1-14`]

The peripheral RTL never sees USB. It exchanges 32-bit AXI4-Stream frames with
an SDK-provided **transport** block (`decap` / `encap` / `transport`, shipped as
encrypted Lattice IP: `src/gateware/src/{decap,encap,transport}.enc.v`). The
transport handles all framing, routing, and the physical device↔headstage link.
[PROVEN: `src/gateware/src/via_top.sv:127-217`, `src/gateware/README.md:107-130`]

Consequence for this project: **the reference implementation does not define a
USB peripheral protocol an STM32/nRF52 could implement to appear on the SciFi's
peripheral-facing USB port.** The section-by-section answers below make the
boundary between "proven fabric contract" and "unknown USB path" explicit, and
`docs/feasibility-mcu-vs-fpga.md` turns it into a build decision.

---

## 1. How a custom peripheral enumerates to SciFi

- The gateware peripheral is assigned a 16-bit **`dev_peripheral_id`** in the
  user window `0xF001..0xFFFE` (`0x0000..0xF000` reserved for built-ins,
  `0xFFFF` = broadcast). The example uses `0xF001`.
  [PROVEN: `src/gateware/README.md:131-138`, `peripheral.yaml:45`, `manifest.json:7-9`]
- At synthesis, `via_top.sv` wires that ID into the transport's
  `peripheral_ids[]` and stamps outbound frame source addresses as
  `central_address | (id & 0xFF)` — the transport reserves the low byte as the
  enumeration slot, so IDs are allocated such that `low byte == slot`
  (`0xF001 → slot 1`). [PROVEN: `via_top.sv:141-147, 176-184`]
- On the host side, `scifi-server` dispatches an enumerated peripheral ID to
  whichever loaded plugin registered that ID via `SCIFI_REGISTER_PERIPHERAL`.
  [PROVEN: `README.md:19-21`, `axon_test_source_plugin.cpp:14-18`]

So "enumeration" is a **fabric-address** mechanism over the Axon transport, not
USB descriptor enumeration. [INFER, from the two proven halves above]

## 2. Required USB device / interface descriptors

**[UNKNOWN].** No USB device/interface/endpoint descriptors appear anywhere in
the reference: the peripheral side is FPGA fabric behind the encrypted
transport IP, and the host side is a `dlopen`ed plugin. The only USB-adjacent
artifact is the `ftd3xx` vcpkg port (FTDI FT60x SuperSpeed bridge) pulled by
the SDK build, which is the **host↔SciFi** link's chip, not a
peripheral-facing descriptor set.
[PROVEN absence; `ftd3xx` at `external/sciencecorp/vcpkg/ports/ftd3xx`]

## 3. USB transfer types and endpoints

**[UNKNOWN]** at the peripheral boundary — hidden inside the encrypted
transport. What *is* visible is the on-fabric physical uplink: the transport
serialises to `serial_data_o`, driven onto **IR TX pins** (`ir_txa_o`,
`ir_txb_o`) at an 80 MHz serdes clock (`SDR_CLK_FREQ`). This is the Axon
probe's isolated optical/serial uplink, not USB.
[PROVEN: `via_top.sv:161-166, 213-216`]

## 4. Full-Speed vs High-Speed/SuperSpeed

**[UNKNOWN]** for a hypothetical USB peripheral. For the reference path, the
device↔headstage link is a custom 80 MHz serdes over IR, and the host↔SciFi
link uses FTDI FT60x (USB3 SuperSpeed).
[INFER from `via_top.sv:161-166`, `ftd3xx` port]

## 5. Initialization / handshake at discovery

- Gateware: on synchronous active-high `rst` release, the peripheral resets its
  RX/TX state machines and configuration registers; it is passive until it
  receives a `CONFIGURE`/`START_STREAM` command frame.
  [PROVEN: `axon_test_source_peripheral.sv:92-138`]
- Driver: the SDK calls `to_proto()` (advertises name/vendor/type via
  `from_peripheral_descriptor`) and `self_test()` during bring-up. The example's
  `self_test` just returns `kOk`. [PROVEN: `axon_test_source_peripheral.cpp:12-14,
  117-122`]
- The transport-level handshake (link training, `READ_DEVICE_INFO`, git-hash
  reporting) lives in the encrypted transport IP. **[UNKNOWN]** beyond the fact
  that `READ_DEVICE_INFO` reports the user repo's HEAD via `GIT_HASH`.
  [DOC: `via_top.sv:49-52`]

## 6. How SciFi identifies type / ID / capabilities

- **Peripheral ID:** the `dev_peripheral_id` / `SCIFI_REGISTER_PERIPHERAL` ID
  (§1). [PROVEN]
- **Type:** a peripheral advertises exactly one `synapse.Peripheral.Type`:
  `kBroadbandSource | kElectricalStimulation | kOpticalStimulation |
  kSpikeSource | kCamera`. The example is a broadband source (its base is
  `RecordPluginWithLimits`, and `to_proto()` builds from a peripheral
  descriptor). [PROVEN: `vendor/synapse-api/api/device.proto:10-24`,
  `axon_test_source_peripheral.{h:22-23,cpp:12-14}`]
- **Capabilities/limits:** advertised by the driver via
  `SCIFI_RECORD_PLUGIN_LIMITS(max_sample_rate, max_bit_width, max_gain,
  max_channel_count)`. The example: 1 MHz, 16-bit, gain 1, 256 channels.
  [PROVEN: `axon_test_source_peripheral.h:25`, `axon_test_source_constants.h:14-17`]

## 7. How configuration is sent from Synapse to the peripheral

Two layers:

1. **Synapse → driver.** The user builds a `NodeConfig` graph containing a
   `BroadbandSourceConfig { peripheral_id, bit_width, sample_rate_hz, gain,
   signal }`. The `broadband_source` node binds to the peripheral by
   `peripheral_id`. Applying the config drives the SDK to call the plugin's
   `start_recording_impl(sample_rate, bit_width, channels, gain, hp, lp, …)`.
   [PROVEN: `vendor/synapse-api/api/nodes/broadband_source.proto:8-16`,
   `api/node.proto:34-54`, `axon_test_source_peripheral.cpp:32-66`]
2. **Driver → gateware.** The driver translates that into peripheral command
   frames via `send_packet(opcode, {words...})`. The example sends
   `CONFIGURE {channel_count, sample_period}` then `START_STREAM`.
   [PROVEN: `axon_test_source_peripheral.cpp:54-65`,
   `axon_test_source_constants.h:8-11`]

`sample_period` is derived in the driver as `CLK_FREQ_HZ / sample_rate` where
`CLK_FREQ_HZ = 40 MHz` is the gateware pacing clock (`clkmc = 160 MHz / 4`).
[PROVEN: `axon_test_source_constants.h:20-21`, `axon_test_source_peripheral.cpp:49-50`,
`via_top.sv:64-67`]

## 8. Data-frame format (the contract you would re-implement)

**Peripheral ⇄ transport AXI4-Stream frame** (32-bit `tdata`, 4-bit `tkeep`,
8-bit `tid`, 1-bit `tdest`, 1-bit `tuser`):

```
        31              16 15               0
       +------------------+------------------+
word0: |    msg_type      |   len (bytes)    |   header (beat 0)
       +------------------+------------------+
word1..N:            payload[i]                  tlast on final beat
       +-------------------------------------+
```

- Header high half = `msg_type`, low half = payload length in **bytes**.
- Payload packed little-endian into 32-bit words (first payload byte → bits
  [7:0] of beat 1). `tlast` on the final beat.
[PROVEN: `src/gateware/README.md:118-130`, `axon_test_source_peripheral.sv:30-46`]

**Example opcodes** (peripheral-defined, not global):

| Opcode | Value  | Dir          | Payload |
|--------|--------|--------------|---------|
| `CONFIGURE`    | `0x0052` | host→fpga | word0 = channel_count, word1 = sample_period (clks) |
| `START_STREAM` | `0x0054` | host→fpga | none |
| `STOP_STREAM`  | `0x0055` | host→fpga | none |
| `DATA_FRAME`   | `0x0056` | fpga→host | channel_count words; low 16 bits of each = signed sample |

[PROVEN: `peripheral.yaml:19-40`, `axon_test_source_constants.h:8-11`,
`axon_test_source_peripheral.sv:56-60`]

Note: these opcodes and the frame *payload* layout are **chosen by the
peripheral author** (they must match between the RTL and its driver). The only
fixed part is the header-word framing above. [INFER; the SDK "handles all
upstream/downstream framing," README.md:129-130]

## 9. Where sequence / sample-counter / timestamp / channel / rate / bit-width live

Critically, **the example's on-wire `DATA_FRAME` carries only raw samples — no
sequence number, sample counter, or timestamp.** Those fields are synthesised
on the **host** side into the `synapse.BroadbandFrame`:

- `BroadbandFrame.sequence_number`, `.timestamp_ns` (= `start_timestamp_ns +
  (seq − start_seq)·1e9/rate`), `.sample_rate_hz`, `.channel_ranges`,
  `.unix_timestamp_ns` are populated by the SDK/host, not the peripheral.
  [PROVEN: `vendor/synapse-api/api/datatype.proto:59-87`]
- `channel_ranges` distinguishes `ELECTRODE` vs `GPIO` channels within
  `frame_data`. [PROVEN: `datatype.proto:76-81`, `api/channel.proto:5-23`]
- Timebase is governed by device `TimeSource`
  (`TIME_SOURCE_SAMPLE_COUNTER` ties timestamp to sample counter × rate).
  [PROVEN: `api/time.proto:41-53`]

**Implication for the bridge:** if we want *device-side* sequence numbers and
hardware timestamps (which the project's synchronization rules require), we
must carry them **inside our own `DATA_FRAME` payload** and unpack them in our
driver — the reference peripheral does not, and relying on host-derived
timestamps discards true source timing. [INFER from the two proven points]

## 10. Flow control / backpressure

Standard AXI-Stream `tvalid`/`tready` handshake on both directions. In the
example the peripheral holds `rx_axis.tready` high (commands are small/rare) and
gates TX on `tx_axis.tready`. Upstream buffering/backpressure across the
transport is inside the encrypted IP. [PROVEN: `axon_test_source_peripheral.sv:80,
303-364`; UNKNOWN beyond the fabric edge]

The driver drains stale RX before starting a stream (`drain_rx()`) and
subscribes to `DATA_FRAME` before issuing `START_STREAM`.
[PROVEN: `axon_test_source_peripheral.cpp:57-65`]

## 11. Can one physical peripheral expose multiple logical capabilities?

- **Multiple peripherals per bitstream:** yes — `peripheral.yaml` supports a
  list under `peripherals:`, and `via_top.sv` scales the transport switch
  `N_USER` 1:1. [PROVEN: `src/gateware/README.md:39-55`, `via_top.sv:29-31, 34-42`]
- **Multiple *types* from a single driver plugin object:** not demonstrated —
  each plugin advertises one `Peripheral.Type`. Composing a source + a
  sink most likely means two peripheral entries (two IDs), not one dual-role
  object. **[INFER / partially UNKNOWN].**

## 12. Can a custom peripheral expose a recording source (e.g. `kBroadbandSource`)?

**[PROVEN — yes.]** That is exactly what `axon_test_source` is: a
`RecordPluginWithLimits` advertising `kBroadbandSource`, streaming
`DATA_FRAME`s that the host republishes as `BroadbandFrame` on a Tap
(`broadband_source_1`). This path is the known-working example already run on
the bench. [PROVEN: whole `src/driver/`, confirmed by bench notes]

**Bench observation (2026-08-24, `synapsectl -u 192.168.100.157 info`).**
Device `wooden-futuristic-oxpecker`, serial `SFI2-0-260534`, Synapse 2.4.1,
firmware 3164583911. Enumerated peripherals:

| ID   | Name                                | Type                   |
|------|-------------------------------------|------------------------|
| 200  | IntanRHD2132 (Intan Technologies)   | `kBroadbandSource`     |
| 1000 | SciFi Virtual Recording Peripheral  | `kBroadbandSource`     |
| 1001 | VirtualOpticalStimPeripheral        | `kOpticalStimulation`  |

Notes:
- The **Intan RHD2132 (ID 200)** is a real, firmware-provided broadband source.
  The RHD2132 is a 32-channel electrophysiology ADC + SPI chip (it digitizes
  analog electrode inputs and SPIs the result to the FPGA). Its driver is **not**
  in any vendored repo (grep across `vendor/` finds no `rhd`/`intan`), so it
  lives in SciFi firmware; its accepted rates/bit widths/electrode-ID range are
  **[UNKNOWN]** from vendored code. [PROVEN existence via `info`]
- A real **stim sink exists** (ID 1001, `kOpticalStimulation`) — usable to test
  the stim control plane without new hardware, but out of current scope.

**[UNKNOWN — do not assume] Which peripheral ID does the Axon→Omnetics probe
present as?** There is **no mapping anywhere in vendored code** between the
"Axon Omnetics adapter" and any peripheral ID or type (every such association in
this repo was authored by us, not derived from Science's code). Two hypotheses,
unresolved:
  1. **RHD2132 is the front-end** for the probe path: the SciFi has an Intan
     RHD2132 SPI'd to the FPGA, the Omnetics connector feeds analog into the
     RHD, and ID 200 is that chip. In this case the ADC is the Intan chip and
     "Axon" names the probe/cabling.
  2. **The Axon front-end is distinct** from the RHD2132 and presents as a
     *different* peripheral ID (possibly not yet enumerated in the observed
     `info`, e.g. because the adapter/probe was not fully attached/powered).
     ID 200 (`IntanRHD2132`) would then be an unrelated front-end option, not
     the Axon path.

The chip name `IntanRHD2132` is a specific commercial Intan part, which is
evidence *for* hypothesis 1 but does not prove the Omnetics adapter routes
through it. This must be resolved on the bench / with Science before binding a
recording config to any ID — see `config/axon-omnetics-32ch.README.md`. The
`peripheral_id: 200` in `config/axon-omnetics-32ch.json` is therefore a
**candidate to test, not an established fact.**

## 13. Can a custom peripheral expose a command/stimulation sink?

**[UNKNOWN — not demonstrated publicly.]** Synapse *defines* the sink side:

- Node/peripheral types `kElectricalStimulation` and `kOpticalStimulation`
  exist. [PROVEN: `api/node.proto:22-32`, `api/device.proto:10-18`]
- The synapse-cpp / synapse-python **clients** can build these nodes.
  [PROVEN: `vendor/synapse-cpp/.../nodes/electrical_stimulation.h`,
  `vendor/synapse-python/.../nodes/electrical_stimulation.py`]
- Taps are explicitly bidirectional: *"Producer taps emit data … consumer taps
  accept data (e.g., stimulation commands)."*
  [DOC: `vendor/synapse-cpp/include/science/synapse/tap.h:14-20`]

But the peripheral **SDK** side we can see only ships a *record* plugin base
(`RecordPluginWithLimits`); no stimulation/sink plugin base class or example is
vendored (the SDK headers themselves arrive via `.deb`, not in the repo).
Whether a user-authored peripheral can register as a stim **sink** and receive
downstream command frames over the transport is **not answerable from vendored
code** and must be asked of Science or tested against an installed SDK. [PROVEN
absence of a sink example; UNKNOWN capability]

## 14. If stimulation/output sinks are supported…

- **Protobuf messages:** `ElectricalStimulationConfig { peripheral_id,
  channels, bit_width, sample_rate, lsb }` configures the node;
  `OpticalStimFrame { frame_id, sequence_number, timestamp_ns, rows, columns,
  intensity[], duration_us }` is the per-frame optical data type.
  [PROVEN: `api/nodes/electrical_stimulation.proto:7-13`,
  `api/nodes/optical_stimulation.proto:12-44`]
- **Delivery over the link:** for optical, `send_receipts` instructs the FPGA
  to emit a 1×N receipt vector *with each Axon frame* — direct evidence that
  sink frames traverse the same Axon transport as source frames.
  [PROVEN: `optical_stimulation.proto:19-21`]
- **Scheduled vs immediate execution:**
  - `ElectricalStimulationConfig` has **no execution-time field** — it is a
    channel/rate/lsb *configuration*, implying the stim *waveform* is streamed
    as node data rather than scheduled here. [PROVEN absence]
  - `OpticalStimFrame` **does** carry `timestamp_ns` and `duration_us`, i.e.
    per-frame timing/duration — the closest thing to advance scheduling in the
    public API. [PROVEN: `optical_stimulation.proto:29-44`]
  - Whether `timestamp_ns` is honoured as a *future* execution time or is
    merely a stamp is **[UNKNOWN]**.

## 15. Can Synapse config / `synapsectl` drive that sink through nodes/Taps?

Partially provable: the node graph and clients model stim nodes bound to a
`peripheral_id`, and `synapsectl info` renders stim peripherals/nodes. So the
**control plane** exists. Whether a *custom* (user-authored) peripheral can be
the target of that plane depends on §13, which is UNKNOWN.
[PROVEN control plane: `device_info_display.py:54-67`, `api/node.proto`;
UNKNOWN for custom peripherals]

---

## Build / deploy toolchain (for reproducing the reference)

- `synapsectl peripherals build {driver|gateware|both} <dir>` — cross-compiles
  the `.so` (ARM64, in an Ubuntu 20.04 vcpkg container) and/or the `.bit` (in an
  Ubuntu 22.04 Radiant 2024.2 container), packages via `fpm` into a `.deb` with
  `Section: synapse-peripherals`.
- `synapsectl peripherals deploy {…} <dir>` — streams the `.deb` over the
  existing `DeployApp` gRPC method; the device runs `dpkg -i`. Restart
  `scifi-server` after deploy so it rescans `/usr/lib/scifi/plugins/`.
- `synapsectl peripherals gateware <verb>` — passthrough to
  `axon-peripheral-sdk` inside the gateware container.

[PROVEN: `vendor/synapse-python/synapse/cli/peripherals.py:1-14, 66-68, 97-159`,
`README.md:8-21`, both Dockerfiles]

**Radiant note:** the gateware half requires **Lattice Radiant 2024.2** with a
runtime-supplied license (`LM_LICENSE_FILE` / `/opt/lattice/license.dat`), and
the design instantiates **encrypted Lattice IP** (`*.enc.v`). See
`docs/feasibility-mcu-vs-fpga.md` for what this implies.
[PROVEN: `Dockerfiles/gateware.Dockerfile:6-25, 96-97`,
`src/gateware/src/{decap,encap,transport}.enc.v`]

---

## Open questions to put to Science (or resolve on hardware)

1. Can a user-authored peripheral register as an **electrical/optical
   stimulation sink**, and if so what SDK base class / registration is used?
   (§13)
2. Is `OpticalStimFrame.timestamp_ns` honoured as a **future scheduled**
   execution time, or only a stamp? Is there any scheduled-trigger primitive?
   (§14)
3. Is there **any** supported path for a peripheral that is *not* on the SciFi's
   internal Lattice fabric — e.g. an external device on the peripheral-facing
   USB/Axon port — to appear as a Synapse peripheral? (§2–4) This is the crux of
   MCU feasibility.
4. How are **SciFi GPIO** sync edges surfaced in Synapse streams/timestamps?
   (Needed for the cross-timebase sync experiment; not in vendored API.)
5. What does the transport's `READ_DEVICE_INFO`/handshake require of a
   peripheral beyond `GIT_HASH` reporting? (§5)
