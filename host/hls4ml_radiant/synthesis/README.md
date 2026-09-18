# Radiant validation builds

These scripts are reproducible entry points for the open RTL validation
designs. They target LIFCL-17-9SG72C with Radiant 2026.1 and a 25 ns clock.
They intentionally do not edit `vendor/axon-peripherals` or claim a complete
Science `.bit` build.

## Measured 2026-09-17

`build_core.tcl` synthesizes the actual hls4ml/XLS-generated
`__myproject__myproject` module:

| Result | Value |
| --- | --- |
| LUT4 / FF | 1857 / 205 |
| DSP / EBR / LRAM | 0 / 0 / 0 |
| Requested clock | 40 MHz (25 ns) |
| Estimated Fmax / period | 34.3 MHz / 29.125 ns |
| Worst slack | -4.125 ns |

`build_integrated.tcl` synthesizes `ml_tap_peripheral_top`, including the
passive DATA_FRAME parser, canonical feature engine, actual generated core,
score selection, and trigger FSM:

| Result | Value |
| --- | --- |
| LUT4 / FF | 1633 / 1234 |
| DSP / EBR / LRAM | 0 / 0 / 0 |
| I/O cells | 428 |
| Estimated Fmax / period | 62.2 MHz / 16.090 ns |
| Worst slack | +8.911 ns |
| Generated-core critical path | 15.879 ns, 25 logic levels |

The two resource totals are different synthesis contexts and must not be
added. The integrated estimate is not final place-and-route timing.

## Pipeline sweep 2026-09-18 (standalone core closes 40 MHz)

The baseline standalone core fails 40 MHz because the whole MLP is one
combinational cloud between the input and output flops (XLS schedules against
the `asap7` 7 nm delay model, which vastly under-counts Lattice LUT4 delay, so
it inserts no internal stages). Requesting a tighter `clock_period` forces the
XLS scheduler to insert pipeline registers. `tools/pipeline_sweep_gen.py`
regenerates the same 8->16->8->4 model at a range of requested periods (WSL,
`xls-python`), and `tools/characterize_sweep.ps1` /
`tools/characterize_core.tcl` synthesize each variant at the real 25 ns SDC
(Radiant 2026.1). Same weights and I/O every time (`clk`, `x_in_bits[63:0]`,
`out[79:0]`); pipelining changes only latency (= stages - 1 cycles).

| Gen period | Stages | Latency | LUT4 | FF | Est. Fmax | Slack @25 ns | Meets 40 MHz |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 12.5 ns (flat baseline) | 2 | 1 | 1857 | 205 | 34.3 MHz | -4.125 ns | no |
| 6 ns | 3 | 2 | 1292 | 189 | 49.8 MHz | +4.909 ns | yes |
| 4 ns | 3 | 2 | 1440 | 1067 | 56.0 MHz | +7.158 ns | yes |
| 3 ns | 4 | 3 | 1274 | 761 | 81.5 MHz | +12.736 ns | yes |
| 2 ns | 5 | 4 | 1266 | 870 | 88.6 MHz | +13.711 ns | yes |
| 1.5 ns | 7 | 6 | 885 | 1591 | 134.2 MHz | +17.547 ns | yes |
| 1 ns | 10 | 9 | 1095 | 2579 | 141.1 MHz | +17.913 ns | yes |

Every pipelined variant closes 40 MHz. A gen period of ~3 ns (4 stages, 3-cycle
latency, 81.5 MHz / +12.7 ns, 1274 LUT4) is a good default: 2x timing margin,
fewer LUT4 than the failing baseline, and only 3 cycles = 75 ns of latency
against the 500 us (2 kHz) epoch. These are synthesis estimates, not final PAR.

### 3 ns / 4-stage variant applied and cocotb-verified 2026-09-18

The 3 ns variant is now the checked-in
`gateware/radiant_hls4ml_canary_generated.sv`. Two integration facts:

* The core is a fixed-latency pipeline with no valid/ready handshake, so
  `ml_tap_peripheral.sv` tracks validity with a `CORE_LATENCY`-deep shift
  register. `CORE_LATENCY` = generated stages - 1 = 3 for this variant (was a
  hard-coded 2-bit pipe for the 1-cycle flat core).
* Raw XLS emits two whole-unpacked-array flop assignments
  (`x_in_bits__input_flop <= ...`, `out__output_flop <= ...`) that Icarus 12
  rejects ("Assignment to an entire array ... not yet supported"); both are
  rewritten element-wise in a `for` loop, exactly as the prior flat core was.
  Radiant re-synthesis of the element-wise form is bit-identical in
  resources/timing (1274 LUT4 / 761 FF / 81.5 MHz / +12.736 ns), confirming the
  rewrite is transparent to synthesis.

All 11 `test_ml_tap_peripheral.py` cocotb tests pass under Icarus 12 against the
pipelined core, including the exact-output check (`[-72, -30, -130, -24]`) and
the updated 3-cycle core-latency assertion. Weights, precision, and I/O are
unchanged from the flat baseline; only the pipeline depth (latency) differs.

`build_integrated_map.tcl` repeats synthesis and attempts mapping. Mapping
fails as expected because this pin-rich validation top exposes 428 I/O cells
while the QFN72 target provides 39 PIO. It reaches `Mapper successful!` for
logic mapping before the device-fit check; no PAR or bitstream result is
claimed. A full Science project must keep these nets internal behind its
actual transport/framework top.

Radiant writes reports and project state into `impl_1/`; those generated files
are ignored. The values above are the checked-in acceptance record.
