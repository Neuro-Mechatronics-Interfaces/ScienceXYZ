"""cocotb tests for the Science Axon hls4ml peripheral wrapper.

Drives INFER_REQUEST frames into the wrapper's rx_axis, waits for the wrapped
core's fixed latency, and checks the INFER_RESULT frame on tx_axis against an
exact software reference of the stub core. Verifies AXI framing, per-output
packing, latency handling, back-to-back requests and backpressure.

Hardware-free: uses the ``axi4_stream_interface`` shim and the ``hls4ml_core``
stub in ``tb/`` (see those files). Run with Icarus or Verilator via cocotb.

The reference must match ``hls4ml_core_stub.sv``:
    out[o] = sign_extend(x_in[o]) * 3 - o,  o = 0..N_OUT-1
carried as OUT_WORDS little-endian 32-bit words per output (sign-extended).
"""
from __future__ import annotations

import os
import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

try:
    from cocotbext.axi import AxiStreamBus, AxiStreamFrame, AxiStreamSink, AxiStreamSource
except Exception:  # pragma: no cover - import guard for environments without the ext
    AxiStreamBus = None

CLK_PERIOD_NS = 12.5  # 80 MHz, matching the SDK cocotb harness

# Parameters must match the tb defaults.
N_IN, IN_W, N_OUT, OUT_W, LATENCY = 8, 16, 4, 36, 2
OUT_WORDS = (OUT_W + 31) // 32

MSG_INFER_REQUEST = 0x0060
MSG_INFER_RESULT = 0x0061


def _to_unsigned(val: int, width: int) -> int:
    return val & ((1 << width) - 1)


def _to_signed(val: int, width: int) -> int:
    val &= (1 << width) - 1
    return val - (1 << width) if val & (1 << (width - 1)) else val


def reference(inputs):
    """Software model of hls4ml_core_stub.sv."""
    out = []
    for o in range(N_OUT):
        xi = _to_signed(inputs[o], IN_W)
        out.append(_to_signed(xi * 3 - o, OUT_W))
    return out


def _header_word(msg_type: int, len_bytes: int) -> int:
    return ((msg_type & 0xFFFF) << 16) | (len_bytes & 0xFFFF)


def _build_request(inputs) -> list[int]:
    words = [_header_word(MSG_INFER_REQUEST, N_IN * 4)]
    for x in inputs:
        words.append(_to_unsigned(x, IN_W) & 0xFFFFFFFF)
    return words


def _parse_result(words) -> list[int]:
    header = words[0]
    msg_type = (header >> 16) & 0xFFFF
    assert msg_type == MSG_INFER_RESULT, f'unexpected msg_type {msg_type:#06x}'
    payload = words[1:]
    assert len(payload) == N_OUT * OUT_WORDS, f'expected {N_OUT * OUT_WORDS} payload words, got {len(payload)}'
    outs = []
    for o in range(N_OUT):
        acc = 0
        for w in range(OUT_WORDS):
            acc |= (payload[o * OUT_WORDS + w] & 0xFFFFFFFF) << (32 * w)
        outs.append(_to_signed(acc, OUT_WORDS * 32))
    return outs


async def _reset(dut):
    dut.rst.value = 1
    dut.rx_tvalid.value = 0
    dut.tx_tready.value = 1
    await ClockCycles(dut.clk, 4)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


def _frame_words(frame) -> list[int]:
    # With byte_size=32 each AXI beat is one 32-bit word; tdata is a list of ints.
    tdata = frame.tdata
    if isinstance(tdata, (bytes, bytearray)):
        return list(struct.unpack(f'<{len(tdata) // 4}I', bytes(tdata)))
    return [int(w) & 0xFFFFFFFF for w in tdata]


async def _run_vectors(dut, vectors, backpressure=False):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    await _reset(dut)

    source = AxiStreamSource(AxiStreamBus.from_prefix(dut, 'rx'), dut.clk, dut.rst, reset_active_level=True, byte_size=32)
    sink = AxiStreamSink(AxiStreamBus.from_prefix(dut, 'tx'), dut.clk, dut.rst, reset_active_level=True, byte_size=32)

    if backpressure:
        sink.pause = True

    for vec in vectors:
        words = _build_request(vec)  # list of 32-bit words, one AXI beat each
        await source.send(AxiStreamFrame(tdata=words))

    if backpressure:
        # Let requests pile up, then release the sink.
        await ClockCycles(dut.clk, 50)
        sink.pause = False

    for vec in vectors:
        frame = await sink.recv()
        got = _parse_result(_frame_words(frame))
        exp = reference(vec)
        assert got == exp, f'input={vec}\n got={got}\n exp={exp}'


@cocotb.test(timeout_time=50, timeout_unit='us')
async def test_single_inference(dut):
    """A single INFER_REQUEST returns the correct INFER_RESULT frame."""
    await _run_vectors(dut, [[3, -3, 5, -5, 0, 1, -1, 100]])


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_edge_vectors(dut):
    """Zero, sign transitions, and representable extremes."""
    lo = -(1 << (IN_W - 1))
    hi = (1 << (IN_W - 1)) - 1
    vectors = [
        [0] * N_IN,
        [1, -1, 2, -2, 3, -3, 4, -4],
        [hi, lo, hi, lo, hi, lo, hi, lo],
        [lo, lo, lo, lo, 0, 0, 0, 0],
        [hi, hi, hi, hi, -1, 1, -1, 1],
    ]
    await _run_vectors(dut, vectors)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_back_to_back(dut):
    """Multiple requests streamed back to back all return correctly in order."""
    vectors = [[i, -i, 2 * i, -2 * i, i + 1, -(i + 1), i - 1, -(i - 1)] for i in range(1, 9)]
    await _run_vectors(dut, vectors)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_backpressure(dut):
    """The wrapper holds results under sink backpressure, then drains correctly."""
    vectors = [[5, -5, 5, -5, 5, -5, 5, -5], [10, 20, -30, 40, -50, 60, -70, 80]]
    await _run_vectors(dut, vectors, backpressure=True)


# --------------------------------------------------------------------------- #
# pytest entrypoint (cocotb runner) — Icarus by default.                       #
# --------------------------------------------------------------------------- #


def test_runner():
    try:
        from cocotb_tools.runner import get_runner  # cocotb >= 2.0
    except ImportError:  # pragma: no cover
        from cocotb.runner import get_runner  # cocotb 1.x

    tb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tb')
    gate_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'gateware')

    sources = [
        os.path.join(tb_dir, 'axi4_stream_interface.sv'),
        os.path.join(gate_dir, 'hls4ml_axon_peripheral.sv'),
        os.path.join(tb_dir, 'hls4ml_core_stub.sv'),
        os.path.join(tb_dir, 'hls4ml_axon_peripheral_tb.sv'),
    ]

    sim = os.environ.get('SIM', 'icarus')
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel='hls4ml_axon_peripheral_tb',
        always=True,
    )
    runner.test(hdl_toplevel='hls4ml_axon_peripheral_tb', test_module='test_hls4ml_axon_peripheral')
