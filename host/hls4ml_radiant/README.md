# hls4ml → Radiant → Science Axon integration

Science-specific validation consumer of the generic hls4ml `RadiantBackend`
(`third_party/hls4ml`, branch `feature/RadiantBackend`). Nothing here is part of
hls4ml; the backend stays hardware-agnostic and upstreamable, and this directory
adapts a generated inference core to the Axon peripheral AXI-stream contract.

See [`docs/hls4ml-radiant-backend.md`](../../docs/hls4ml-radiant-backend.md) for
the full architecture, exact revisions, measured timing/resource results, and the
remaining operator-run steps.

## Layout

```
host/hls4ml_radiant/
├── gateware/
│   └── hls4ml_axon_peripheral.sv   # parameterized Science AXI-stream wrapper
└── tests/
    ├── tb/
    │   ├── axi4_stream_interface.sv       # contract-faithful SDK-interface shim
    │   ├── hls4ml_core_stub.sv            # deterministic core stub (real port contract)
    │   └── hls4ml_axon_peripheral_tb.sv   # testbench top (flat AXI ↔ interface)
    └── test_hls4ml_axon_peripheral.py     # cocotb: frame-in → inference → frame-out
```

## Wrapper contract

`hls4ml_axon_peripheral_top` adapts the SDK frame contract (`clk`, `rst`,
`periph_addr`, `rx_axis`/`tx_axis` AXI-stream; header word
`{msg_type[31:16], len_bytes[15:0]}`, `tlast` on the final beat) to a generated
core's parallel-vector ports (`clk`, `x_in_bits`, `out`). Parameters: `N_IN`,
`IN_W`, `N_OUT`, `OUT_W`, `LATENCY`.

Messages: `INFER_REQUEST (0x0060)` carries `N_IN` input words; `INFER_RESULT
(0x0061)` carries `N_OUT × ceil(OUT_W/32)` little-endian words. One inference is
in flight at a time; the wrapper backpressures `rx_axis` while busy.

To wrap the real generated core instead of the stub, swap
`tests/tb/hls4ml_core_stub.sv` for the hls4ml output `firmware/<project>.sv`
(top `__<project>__<project>`) and match the wrapper parameters to that core's
port widths.

## Running the hardware-free cocotb tests

Requires `cocotb`, `cocotbext-axi`, and a simulator (Icarus or Verilator). No
Science hardware and no Radiant needed.

```bash
pip install cocotb cocotbext-axi
SIM=icarus pytest host/hls4ml_radiant/tests/test_hls4ml_axon_peripheral.py
```

## Boundaries

The agent never runs `synapsectl`, deploys, or programs hardware. Building the
full Science Axon Radiant project to a `.bit` and deploying it is an operator
step (see the manuscript). Add the wrapper + generated core to a **copy** of the
gateware via `peripheral.yaml`; do not edit the vendored SDK.
