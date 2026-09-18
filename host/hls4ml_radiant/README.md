# hls4ml → Radiant → Science Axon integration

Science-specific validation consumer of the generic hls4ml `RadiantBackend`
(`third_party/hls4ml`, branch `feature/RadiantBackend`). Nothing here is part of
hls4ml; the backend stays hardware-agnostic and upstreamable, and this directory
adapts a generated inference core to the Axon peripheral AXI-stream contract.

See [`docs/hls4ml-radiant-backend.md`](../../docs/hls4ml-radiant-backend.md) for
the full architecture, exact revisions, measured timing/resource results, and the
remaining operator-run steps.

## Two ways to attach an hls4ml core to Science acquisition

There are **two** integration modules here, for two different relationships:

1. **`hls4ml_axon_peripheral_top`** — a *host-driven request/response* inference
   peripheral. The host sends an `INFER_REQUEST`, the core computes, it returns an
   `INFER_RESULT`. The host owns the timing.

2. **`ml_tap_peripheral_top`** — a *non-blocking tap* on the live neural
   acquisition stream. It **passively snoops** the acquisition peripheral's
   outbound `DATA_FRAME` (0x0056) — reading `tvalid`/`tdata`/`tlast` only, never
   driving `tready`, never in the datapath — decodes channel-sequential 16-bit
   samples, builds an 8-feature causal energy vector, runs the checked-in
   hls4ml-generated `__myproject__myproject` core, and drives a
   threshold/refractory/pulse trigger FSM. If inference is disabled, reset, busy,
   or faulted, the raw acquisition stream is structurally unaffected. This is the
   architecture for on-device inference that must not disturb recording.

## Layout

```
host/hls4ml_radiant/
├── gateware/
│   ├── hls4ml_axon_peripheral.sv   # (1) host-driven request/response wrapper
│   ├── ml_sample_tap.sv            # (2) passive snoop + 8-feature causal adapter
│   ├── ml_trigger_fsm.sv           # (2) generic threshold/refractory/pulse FSM
│   └── ml_tap_peripheral.sv        # (2) tap → core → latency → score → trigger
└── tests/
    ├── tb/
    │   ├── axi4_stream_interface.sv       # contract-faithful SDK-interface shim
    │   ├── hls4ml_core_stub.sv            # deterministic core stub (real port contract)
    │   ├── hls4ml_axon_peripheral_tb.sv   # (1) testbench top (flat AXI ↔ interface)
    │   └── ml_tap_peripheral_tb.sv        # (2) testbench top (snoops an acq stream)
    ├── test_hls4ml_axon_peripheral.py     # (1) cocotb: frame-in → inference → frame-out
    └── test_ml_tap_peripheral.py          # (2) cocotb: snoop → feature → trigger, 10 cases
```

The tap-specific files also include `ml_feature_engine.sv` (the
source-independent canonical sample-event interface),
`radiant_hls4ml_canary.sv` plus `radiant_hls4ml_canary_generated.sv` (the
actual generated `__myproject__myproject` RTL), and
`tests/radiant_canary_reference.py` (the exact fixed-point reference).

## Wrapper contract

`hls4ml_axon_peripheral_top` adapts the SDK frame contract (`clk`, `rst`,
`periph_addr`, `rx_axis`/`tx_axis` AXI-stream; header word
`{msg_type[31:16], len_bytes[15:0]}`, `tlast` on the final beat) to a generated
core's parallel-vector ports (`clk`, `x_in_bits`, `out`). Parameters: `N_IN`,
`IN_W`, `N_OUT`, `OUT_W`, `LATENCY`.

Messages: `INFER_REQUEST (0x0060)` carries `N_IN` input words; `INFER_RESULT
(0x0061)` carries `N_OUT × ceil(OUT_W/32)` little-endian words. One inference is
in flight at a time; the wrapper backpressures `rx_axis` while busy.

The separate request/response wrapper retains `hls4ml_core_stub.sv` as a
deterministic test double. The live tap path uses the checked-in generated
RTL; regenerate it with `tools/generate_canary.py` and use the tracked
Radiant scripts under `synthesis/` for synthesis.

## Running the hardware-free cocotb tests

Requires `cocotb`, `cocotbext-axi`, and a simulator (Icarus or Verilator). No
Science hardware and no Radiant needed.

```bash
pip install cocotb cocotbext-axi
SIM=icarus pytest host/hls4ml_radiant/tests/test_hls4ml_axon_peripheral.py   # (1) request/response
SIM=icarus pytest host/hls4ml_radiant/tests/test_ml_tap_peripheral.py        # (2) non-blocking tap
```

The tap tests run at the real 40 MHz `clkmc` clock and cover all ten required
regression cases, including "ML disabled while acquisition continues" and "ML
reset while acquisition continues".

## Boundaries

The agent never runs `synapsectl`, deploys, or programs hardware. Building the
full Science Axon Radiant project to a `.bit` and deploying it is an operator
step (see the manuscript). Add the wrapper + generated core to a **copy** of the
gateware via `peripheral.yaml`; do not edit the vendored SDK.
