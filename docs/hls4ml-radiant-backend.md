# hls4ml → Lattice Radiant backend, with Science Axon integration

This document records the architecture, exact revisions, build path, and measured results for an hls4ml `RadiantBackend` that turns a small neural network into synthesizable SystemVerilog and builds it with Lattice Radiant for the Science Via/Axon development FPGA. It also records the Science-specific AXI-stream wrapper that lets a generated inference core sit behind the Axon peripheral contract.

Written for: an engineer picking this work up — someone who knows hls4ml exists but has not seen this backend, and who needs to reproduce the numbers or decide what to upstream.

## 1. What was built, and where

Two clearly separated layers, matching the split the task requires:

1. **Generic, upstreamable `RadiantBackend` in hls4ml** (`third_party/hls4ml`,
   branch `feature/RadiantBackend`). It carries no dependency on Science
   hardware, Synapse, Axon, or any board. It reuses the existing XLS backend's
   (pure-Python, tool-free) SystemVerilog generation and adds a Lattice Radiant
   project/Tcl generator, a `radiantc` build driver, and a report parser.

2. **Science-specific integration in ScienceXYZ** (`host/hls4ml_radiant/`, not
   vendored). A parameterized AXI-stream wrapper that adapts the Axon peripheral
   SDK frame contract to the generated core's parallel-vector interface, plus a
   hardware-free cocotb harness. This is the hardware-backed *consumer* of the
   generic backend, never part of hls4ml.

### hls4ml files (new)

| File | Purpose |
| --- | --- |
| `hls4ml/backends/radiant/radiant_backend.py` | `RadiantBackend(XLSBackend)`: config, flows, `build()`. |
| `hls4ml/backends/radiant/__init__.py` | Package marker. |
| `hls4ml/writer/radiant_writer.py` | `RadiantWriter(XLSWriter)`: writes the XLS design **plus** the Radiant `.sdc` and `build_radiant.tcl`. |
| `hls4ml/report/radiant_report.py` | `parse_radiant_report()`: parses Synplify/Radiant reports into a plain dict. |
| `test/pytest/test_radiant_backend.py` | Registration, config, RTL-gen, numeric-equivalence, synthesis (gated). |
| `test/pytest/test_radiant_report.py` | Offline report-parser tests against checked-in real-report fixtures. |
| `test/pytest/test_report/Radiant/*` | Real report fixtures captured from a Radiant 2026.1 build. |

Registered in `backends/__init__.py`, `writer/__init__.py`, `report/__init__.py`.

### ScienceXYZ files (new)

| File | Purpose |
| --- | --- |
| `host/hls4ml_radiant/gateware/hls4ml_axon_peripheral.sv` | Parameterized Science AXI-stream wrapper around a generated core. |
| `host/hls4ml_radiant/tests/tb/axi4_stream_interface.sv` | Contract-faithful shim of the SDK interface (SDK copy is closed/read-only). |
| `host/hls4ml_radiant/tests/tb/hls4ml_core_stub.sv` | Deterministic core stub with the real port contract and 2-cycle latency. |
| `host/hls4ml_radiant/tests/tb/hls4ml_axon_peripheral_tb.sv` | Testbench top binding flat AXI signals to the interface. |
| `host/hls4ml_radiant/tests/test_hls4ml_axon_peripheral.py` | cocotb tests: frame-in → inference → frame-out, edges, back-to-back, backpressure. |

## 2. Architecture decision: subclass the XLS backend

The initial hypothesis — reuse the XLS RTL generation and layer Radiant on top — was confirmed by inspecting the current hls4ml architecture:

* The **XLS backend** (`hls4ml/backends/xls`, upstream PR #1475) generates
  SystemVerilog **in pure Python** via the `xls-python` package
  (`pkg.schedule_and_codegen(...).get_verilog_text()`). Its `build()` stops at a
  `.sv` file; it invokes no vendor EDA tool for RTL. It supports `io_parallel`
  only (verified). This is exactly the generator to reuse.
* The **Libero backend** (`hls4ml/backends/libero`) is the vendor-toolflow
  precedent for `build()` returning a parsed report dict, but it is a full
  ~900-line C-HLS writer generating its own HDL — the "reimplement a NN code
  generator" path the task says to avoid.

`RadiantBackend` therefore **subclasses `XLSBackend`** and overrides only:

* `create_initial_config()` — Lattice Nexus part defaults, Radiant project
  options (`Synthesis`, `Top`, `ClockName`, `ExtraSources`, `BuildStage`).
* `build()` — generate RTL via `super().build()` (the reused XLS codegen), then
  run `radiantc` and parse the report.
* `_register_flows()` — a `radiant:`-namespaced mirror of the XLS flow, so the
  backend is independently discoverable as `backend="Radiant"` while reusing
  every XLS optimizer implementation.
* `_init_file_optimizers()` — walks the full class MRO so the shared
  `FPGABackend` passes (e.g. `xnor_pooling`) register under the `radiant:`
  namespace. (The base implementation only walks direct bases + self, which for
  a two-level subclass misses `FPGABackend`.)

The `RadiantWriter` is selected automatically because `self.name == 'Radiant'` resolves `get_writer('Radiant')`.

Why built-in rather than an external plugin: the branch targets an upstreamable `backend="Radiant"`, and every other RTL/vendor backend (XLS, Libero, Coyote) is built-in and registered the same way. The `hls4ml.backends` entry-point plugin mechanism exists and would also work, but built-in matches the surrounding code and is the cleaner upstream target.

## 3. Exact revisions and target

* **hls4ml**: `Neuro-Mechatronics-Interfaces/hls4ml`, branch
  `feature/RadiantBackend`, base commit `0a304519` (identical to upstream
  `fastmachinelearning/hls4ml` `main` at audit time; no prior Radiant work
  existed). XLS backend present (PR #1475), `xls-python` extra `>=0.1.9875`.
* **Axon SDK reference**: `vendor/axon-peripherals` @ `afafa38` (branch
  `m053m716/intan`), treated read-only. Ground-truth files:
  `src/gateware/src/scir_sdk.rdf`, `.sdc`, `.pdc`, `src/gateware/peripheral.yaml`,
  `src/gateware/src/via_top.sv`, `src/gateware/src/axon_test_source_peripheral.sv`.
* **FPGA target**: `LIFCL-17-9SG72C`, performance grade `9_Low-Power_1.0V`,
  package `QFN72`, operation `COM`, family CrossLink-NX / Nexus (`family_int
  je5d00`). Synthesis `synplify` (Synplify Pro), HDL `System Verilog`, top
  `via_top`.
* **Radiant**: the `.rdf` was generated by Radiant `2024.1.1.259.1`, but
  `peripheral.yaml` (`build.radiant_version: '2026.1'`) and
  `Dockerfiles/gateware.Dockerfile` (`ARG RADIANT_VERSION=2026.1`) target
  **2026.1**, and the host install used here is **Radiant 2026.1.0.37.0**
  (`radiantc.exe`). The 2026.1 environment is the authority; the version in the
  checked-in `.rdf` is stale metadata.

### 3.1 Clock: a discrepancy worth stating plainly

The task states an "80 MHz / 12.5 ns Science peripheral clock". The gateware tells a more precise story (`via_top.sv`, `scir_sdk.sdc`):

* `clkmc = 40 MHz (25 ns)` — SoC + **peripherals**. The user peripheral
  `u_user_axon_test_source` is clocked by **`clkmc` (40 MHz)**.
* `clksdr = 80 MHz (12.5 ns)` — serdes TX. The SDK's cocotb testbench also
  clocks the peripheral at **12.5 ns** (`CLK_PERIOD_NS = 12.5`).

So 80 MHz / 12.5 ns is the correct **simulation and serdes** clock and a sound constraint target, but the *shipping user-peripheral domain is 40 MHz*. A core meant to sit behind the real peripheral must ultimately close timing at 40 MHz. The backend defaults to 12.5 ns and lets the caller set `clock_period=25.0` for the `clkmc` domain.

## 4. Minimum validation model

A deterministic EMG-decoder-shaped MLP, no download, fixed RNG seed:

```
8 inputs → Dense(16) → ReLU → Dense(8) → ReLU → Dense(4)
```

Weights are `round(uniform(-0.5,0.5), 3)` / biases `round(uniform(-0.25,0.25), 3)` from `numpy.random.default_rng(1234)`. Fixed-point via hls4ml `ap_fixed<W,I>`. The test fixture lives in `test/pytest/test_radiant_backend.py::_tiny_dense_model`.

## 5. Build path

```
Keras model
  └─ hls4ml.converters.convert_from_keras_model(backend="Radiant", part=..., clock_period=...)
       └─ hls_model.write()      # XLS DSLX design + Radiant .sdc + build_radiant.tcl
       └─ hls_model.compile()    # XLS JIT: firmware/<proj>.opt.ir  (also the SW/emulation model)
       └─ hls_model.build()      # XLS codegen → firmware/<proj>.sv, then radiantc build_radiant.tcl → report
```

`build_radiant.tcl` uses only the long-standing `prj_*` Tcl commands (`prj_create`/`prj_add_source`/`prj_set_impl_opt`/`prj_run`). Stages are selectable via `build_stage` / `build(stage=...)`: `synthesis` (default), `map`, `par`, `bitstream`.

**Why synthesis-only is the default for a bare core.** A bare hls4ml core exposes its *entire* feature and result vector as top-level ports (e.g. 64 input
+ 80 output + clk = 145 pins for the 8-bit model). LIFCL-17-QFN72 has ~39 usable
PIO, so **map/PAR of the un-wrapped core fails I/O fitting even though the logic
fits easily** (see §7). Synthesis alone yields real LUT/DSP/FF and Fmax for the
core-as-IP; full map/PAR/bitstream is meaningful only for a pin-frugal *wrapped*
design (the Science peripheral, §8) or with `extra_sources`.

`radiantc` returns exit code 0 even when a `prj_run` stage fails, so `build()` also scans the console log for `failed` / `doesn't fit into device` and raises on either.

### 5.1 Environment split (this bench)

* `xls-python` ships Linux-only manylinux wheels, so **RTL generation runs under
  WSL Ubuntu** (a dedicated venv `~/.venvs/hls4ml-radiant`, Python 3.13, with
  `hls4ml[xls]`, Keras 3 + TF-CPU, `quantizers`).
* **Radiant synthesis runs from the Windows install** (`radiantc.exe`,
  `LM_LICENSE_FILE` set). There is no Linux Radiant at `/c/lscc/radiant-linux`,
  so the SDK's Docker gateware build (which bind-mounts that path) is blocked
  here; that only affects the full SDK project build (§8), not the backend.

## 6. Numerical validation

For every layer the same input vectors are compared:

```
Keras (float) → hls4ml XLS emulation (fixed-point) → RTL (generated .sv)
```

* **Keras vs XLS emulation** (the XLS JIT, run via `hls_model.predict`) is
  exercised over zero, sign-mixed, near-saturation and range-edge vectors in
  `test_radiant_numeric_equivalence_deterministic`. The emulation is bit-exact
  run-to-run (`assert_array_equal` on two predicts). Against Keras the max
  absolute error is the fixed-point quantization error: **≈ 0.008 at
  `ap_fixed<16,6>`** (10 fractional bits over 3 dense layers) and **≈ 0.47 at
  `ap_fixed<8,4>`** (4 fractional bits — lossy, used only for the fit test). The
  test asserts `< 0.05` at `ap_fixed<16,6>`.
* **RTL vs emulation**: the generated `.sv` is the same IR the XLS JIT runs, so
  they are equal by construction; a direct RTL-simulation cross-check is left as
  a follow-up (needs a co-sim harness on the wide-port core). The wrapper's
  cocotb test (§8) does compare RTL-simulated outputs against an exact software
  reference for the stub core.

## 7. Timing and resource results (measured, not assumed)

Real Radiant 2026.1 build of the generated core (`ap_fixed<8,4>` model, 80 MHz / 12.5 ns request). Parsed by `parse_radiant_report`:

| Metric | Value |
| --- | --- |
| Part | LIFCL-17-9SG72C |
| LUT4 | 7389 / 13824 (53%) |
| Registers (FF) | 210 / 13941 |
| DSP (18×18) | inferred by Synplify (0–24 depending on precision) |
| EBR / LRAM | 0 / 24, 0 / 5 |
| Requested clock | 80 MHz (12.5 ns) |
| **Achieved Fmax** | **34.3 MHz (29.139 ns)** |
| **Worst slack** | **−16.639 ns** |
| Timing met | **No** |
| Inference latency | 2 clocks (flopped input + flopped output; combinational MLP between) |

Two findings are surfaced, not hidden:

1. **The bare, single-cycle-combinational MLP does not meet 80 MHz** (34.3 MHz achieved). The whole dense network is one combinational cloud between the input and output flops; its logic depth is the bottleneck. Closing 80 MHz needs pipelining (XLS pipeline stages / a higher `worst_case_throughput` target) or a relaxed constraint. Note the real user-peripheral domain is 40 MHz (§3.1) — still above 34.3 MHz, so even that needs attention.
2. **Standalone map/PAR fails on I/O, not logic**: 145 top-level pins vs ~39 PIO (390%). The logic fits comfortably (53% LUT). This is inherent to synthesizing a wide-parallel-port core as a *pinned top* and is why the core is validated at the synthesis stage and integrated as a sub-module (§8).

## 8. Science Axon integration

`host/hls4ml_radiant/gateware/hls4ml_axon_peripheral.sv` adapts the SDK frame contract to the core. Interface verified against `axon_test_source_peripheral.sv`: ports `clk`, `rst` (sync active-high), `periph_addr[31:0]`, `rx_axis` (`axi4_stream_interface.secondary`), `tx_axis` (`axi4_stream_interface.main`); one 32-bit word per beat, header word = `{msg_type[31:16], len_bytes[15:0]}`, `tlast` on the final beat.

Message protocol (explicit, versioned constants):

* `INFER_REQUEST (0x0060)` — `N_IN` payload words, low `IN_W` bits of word *i* = feature *i*.
* `INFER_RESULT (0x0061)` — `N_OUT × ceil(OUT_W/32)` little-endian words carrying each signed fixed-point output.

Dataflow: `AXI stream → input frame decoder → feature vector → hls4ml core → output vector → output frame encoder → AXI stream`. The wrapper is parameterized (`N_IN`, `IN_W`, `N_OUT`, `OUT_W`, `LATENCY`) and latches the whole input vector, pulses the core, waits `LATENCY` cycles (2 for the XLS flopped-I/O MLP), then streams the packed outputs.

The cocotb harness (`test_hls4ml_axon_peripheral.py`) is hardware-free: it uses a contract-faithful `axi4_stream_interface` shim (the SDK's own copy is closed and lives only in the SDK Docker image) and a deterministic core stub whose output is predicted exactly in Python. It resets, sends inference requests, checks AXI framing and per-output packing, and verifies edges, back-to-back requests and sink backpressure. It uses `cocotbext.axi` (the same library the SDK's own `test_axon_test_source.py` uses).

Result: all four cocotb tests pass under Icarus Verilog 14 (`test_single_inference`, `test_edge_vectors`, `test_back_to_back`,
`test_backpressure`), with every output matched *exactly* (not argmax) against the software reference. Bringing them up caught two real wrapper bugs recorded in `MISTAKES.md` (2026-09-17): a SystemVerilog width-cast precedence error that doubled every output (`N*W'(x)` parses as `N*(W'(x))`), and a back-to-back frame that overwrote the pending result because `tready` was gated one cycle too late. Both are fixed; the wrapper now backpressures input the instant a request completes and holds one inference in flight at a time.

## 9. Full Science Radiant project (Milestone D / operator step)

The SDK's supported way to add user RTL is `peripheral.yaml` (`fpga.module` / `fpga.sources` / `fpga.constraints`), **not** hand-editing the generated `scir_sdk.rdf`. A validation project would list `via_top.sv`, the Science transport/framework RTL, the `hls4ml_axon_peripheral` wrapper, the generated core `.sv`, the PDC/SDC, and the Science IPX, targeting `LIFCL-17-9SG72C` with the Synplify/Radiant flow.

On this bench the SDK gateware build runs in the `gateware.Dockerfile` image, which bind-mounts a **Linux** Radiant copy that is not installed here, so the full `.deb`/bitstream build is **pending an operator**. The agent does not run `synapsectl` or deploy. When the environment is ready the operator-run build is (exact form to be confirmed from current SDK help before use):

```
synapsectl peripherals build both <project-dir>
```

Treat the bitstream/deploy as a separate acceptance stage from synthesis.

## 10. Limitations and next steps

* `io_parallel` only (inherited from XLS); `io_stream` is out of scope.
* The bare MLP misses 80 MHz; pipeline the core or constrain at the real 40 MHz `clkmc` before claiming timing closure.
* RTL-vs-emulation is equal by construction; a direct co-sim of the generated wide-port core would make that explicit.
* Full map/PAR and a bitstream require the wrapped, pin-frugal project (§8/§9), which is operator-run here.
* A pre-existing **XLS backend** bug was found and left alone (out of scope): `xls_writer.py` hard-codes `myproject_bits` in the top-level DSLX call, so a non-default `project_name` breaks `compile()`. Use the default project name, or fix upstream separately.

## 11. Future extension — Nyx1-512 acquisition on the same FPGA

A question arose about also mediating a Science **Nyx1-512** neural interface IC (up to 512 channels at up to 2 kHz) on the same FPGA. This is **orthogonal** to the generic inference core and does **not** change the `RadiantBackend`: it adds a *separate* Science-specific acquisition subsystem (a sibling of the AXI wrapper), and it changes the device budget. Grounded in the Science docs (`ic-nyx1-512/ hardware-integration`, `ic-nyx1-512/electrical`, `adapt-nerv/electrical`):

* The Nyx1-512 **requires an FPGA** (Science provides a reference implementation); an MCU cannot meet its timing.
* Digital interface: **4-wire SPI control** (32-bit command/address/data words, 40 MHz SCLK, 48 SCLK per transaction) + **four SDR serial data lanes** (`data_ser_out<3:0>`, ≤ 160 Mbit/s each) + **four word clocks** (`wclk_ser_out<3:0>`, 16:1 deserialization) + an FPGA-generated **system clock ≤ 160 MHz with < 50 ps RMS jitter** + `rstb` (low ≥ 6 ms at power-up).
* Electrical: all digital I/O is **1.2 V LVCMOS single-ended** (not LVDS) — the FPGA needs a 1.2 V `VCCIO` bank or level translation. This is the key gate.
* 512 electrodes, configurable 10–16 bit, 2–32 kHz. The **2 kHz target maps to ADC Mode-4 (16-bit), which still requires a 160 MHz system clock** (single-slope ADC timing, not throughput). Aggregate at that point is only 512 × 16 × 2k ≈ 16.4 Mbit/s across four lanes — trivial versus the 4 × 160 Mbit/s raw capacity.
* The **Axon NeRV adapter** breaks the raw Nyx1 signals out on a 34-pin connector at 1.2 V (CLK 19, RSTB 21, SCLK 23, CS 24, SDIN 22, SDOUT 20, DATA0–3, WCLK0–3); in Science's own stack the FPGA lives in the SciFi headstage.

Device-budget implication, using this project's measured numbers: the Nyx1 front end is small in logic (Science estimates "< 5k LUT4 avg"), but a *512-channel decoder core* is far larger than the toy fixture that already used 7389 LUT4 (53%) and missed 80 MHz. On a LIFCL-17 the realistic split is **acquisition + framing on the FPGA, decode on the host**; on-FPGA inference for 512 channels points to a larger Nexus part (LIFCL-40 / CertusPro-NX). The backend already takes `part=...`, so retargeting is a config change, not a rewrite. Tracked as a non-blocking follow-up.
