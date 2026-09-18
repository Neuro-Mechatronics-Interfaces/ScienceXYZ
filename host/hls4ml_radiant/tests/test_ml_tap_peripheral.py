"""cocotb tests for the non-blocking ML sample tap (ml_tap_peripheral_top).

The tap SNOOPS a Science Axon DATA_FRAME (0x0056) stream read-only, builds an
8-feature vector (raw |sample| in passthrough, else an EMA running-energy), runs
the actual RadiantBackend-generated hls4ml core, and drives a
threshold/refractory/pulse trigger FSM.

These tests drive the acquisition bus directly (acq_tvalid/tdata/tlast) and
control acq_tready separately -- the DUT must never source tready. They verify:

    1. all-zero neural input             (test_all_zero)
    2. signed +/- input values           (test_signed_values)
    3. values near ADC extrema           (test_adc_extrema)
    4. trigger just below threshold       (test_threshold_below)
    5. trigger exactly at threshold       (test_threshold_at)
    6. trigger above threshold            (test_threshold_above)
    7. refractory suppression             (test_refractory)
    8. repeated samples while ML busy     (test_busy_overrun)
    9. ML disabled, acquisition continues (test_ml_disabled_acq_continues)
   10. ML reset, acquisition continues    (test_ml_reset_acq_continues)

Hardware-free: uses the acquisition-stream shim, the checked-in generated RTL,
and Icarus/Verilator via cocotb. The integer Python reference is in
``radiant_canary_reference.py``.
"""
from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

CLK_PERIOD_NS = 25.0  # 40 MHz -- the real Science user-peripheral (clkmc) domain

N_IN, IN_W, N_OUT, OUT_W, LATENCY = 8, 8, 4, 20, 2
MAX_CH = 512
SEL_W = (MAX_CH - 1).bit_length()  # $clog2(512) = 9
SIDX_W = (N_OUT - 1).bit_length()  # 2

MSG_DATA_FRAME = 0x0056


def _to_unsigned(val: int, width: int) -> int:
    return val & ((1 << width) - 1)


def _to_signed(val: int, width: int) -> int:
    val &= (1 << width) - 1
    return val - (1 << width) if val & (1 << (width - 1)) else val


def _header_word(msg_type: int, len_bytes: int) -> int:
    return ((msg_type & 0xFFFF) << 16) | (len_bytes & 0xFFFF)


def _pack_chan_sel(sel) -> int:
    acc = 0
    for k, ch in enumerate(sel):
        acc |= (ch & ((1 << SEL_W) - 1)) << (k * SEL_W)
    return acc


def _abs_sat(sample: int) -> int:
    a = abs(_to_signed(sample, SAMPLE_W := 16))
    return min(a, (1 << IN_W) - 1)


from radiant_canary_reference import feature_bytes, quantized_model_outputs


def ref_model_outputs(samples, chan_sel):
    features = feature_bytes([samples[chan_sel[k]] for k in range(N_IN)])
    return quantized_model_outputs(features)


def ref_model_score(samples, chan_sel, score_index):
    return ref_model_outputs(samples, chan_sel)[score_index]


async def _reset(dut):
    dut.rst.value = 1
    dut.acq_tvalid.value = 0
    dut.acq_tdata.value = 0
    dut.acq_tlast.value = 0
    dut.acq_tready_en.value = 1
    dut.ml_enable.value = 1
    dut.feat_passthrough.value = 1
    dut.chan_sel_flat.value = _pack_chan_sel(list(range(N_IN)))
    dut.score_index.value = 0
    dut.threshold.value = 0
    dut.trig_polarity.value = 0
    dut.pulse_width.value = 4
    dut.refractory_cycles.value = 8
    await ClockCycles(dut.clk, 4)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def _send_data_frame(dut, samples, tready_en=True):
    """Play one DATA_FRAME of `samples` (list of ints) on the acq bus.

    Drives one 32-bit word per clock: header then one word per channel, tlast on
    the last. Respects acq_tready: a beat is only 'seen' by the tap when
    tvalid & tready. Sets acq_tready_en for the whole frame.
    """
    dut.acq_tready_en.value = 1 if tready_en else 0
    n = len(samples)
    # Header beat.
    await RisingEdge(dut.clk)
    dut.acq_tvalid.value = 1
    dut.acq_tdata.value = _header_word(MSG_DATA_FRAME, n * 4)
    dut.acq_tlast.value = 0
    await _await_beat(dut)
    # Payload beats.
    for i, s in enumerate(samples):
        dut.acq_tdata.value = _to_unsigned(s, 16)
        dut.acq_tlast.value = 1 if i == n - 1 else 0
        await _await_beat(dut)
    dut.acq_tvalid.value = 0
    dut.acq_tlast.value = 0


async def _await_beat(dut):
    """Advance one clock; if tready is low, hold tvalid until it goes high."""
    await RisingEdge(dut.clk)
    while dut.acq_tready_en.value == 0:
        await RisingEdge(dut.clk)


async def _start(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit="ns").start())
    await _reset(dut)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_generated_core_vector_and_latency(dut):
    """Exercise the generated module, compare all outputs, and timestamp stages."""
    await _start(dut)
    samples = [10, -10, 20, -20, 30, -30, 40, -40]
    expected = ref_model_outputs(samples, list(range(N_IN)))
    dut.threshold.value = expected[0]
    dut.pulse_width.value = 1
    dut.refractory_cycles.value = 0

    events = {}
    stop = False

    async def monitor():
        cycle = 0
        while not stop:
            await RisingEdge(dut.clk)
            await ReadOnly()
            for name in ("feature_valid", "model_input_accepted", "model_output_valid",
                         "trigger_decision", "trigger_out"):
                signal = getattr(dut.dut, name)
                if name not in events and int(signal.value):
                    events[name] = cycle
            cycle += 1

    task = cocotb.start_soon(monitor())
    await _send_data_frame(dut, samples)
    await ClockCycles(dut.clk, 15)
    stop = True
    await task

    actual = [_to_signed((int(dut.dut.core_out_bits.value) >> (o * OUT_W))
                         & ((1 << OUT_W) - 1), OUT_W) for o in range(N_OUT)]
    assert actual == expected, f"generated vector {actual} != reference {expected}"
    # CORE_LATENCY in ml_tap_peripheral.sv is the accepted-input -> valid-out
    # depth of the generated core (pipeline stages - 1). The flat core was 1;
    # the 40 MHz-closing pipelined core is 3 (4 stages). Keep this in sync with
    # the wrapper's localparam.
    CORE_LATENCY = 3
    assert events["model_input_accepted"] - events["feature_valid"] == 1
    assert events["model_output_valid"] - events["model_input_accepted"] == CORE_LATENCY
    assert events["trigger_decision"] - events["model_output_valid"] == 1
    assert events["trigger_out"] - events["trigger_decision"] == 1


# --------------------------------------------------------------------------- #
# 1. all-zero neural input                                                     #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_all_zero(dut):
    await _start(dut)
    dut.threshold.value = -34  # generated all-zero score is -35
    await _send_data_frame(dut, [0] * N_IN)
    await ClockCycles(dut.clk, 20)
    assert int(dut.frames_seen.value) == 1
    assert int(dut.samples_seen.value) == N_IN
    assert int(dut.inferences_started.value) == 1
    assert int(dut.inferences_completed.value) == 1
    assert _to_signed(int(dut.last_score.value), OUT_W) == -35
    assert int(dut.trigger_count.value) == 0
    assert int(dut.ml_overrun.value) == 0


# --------------------------------------------------------------------------- #
# 2. signed positive/negative input values                                    #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_signed_values(dut):
    await _start(dut)
    dut.threshold.value = 1 << 18  # high -> no fire; just check the score math
    samples = [10, -10, 20, -20, 30, -30, 40, -40] + [0] * (MAX_CH - N_IN)
    sel = list(range(N_IN))
    await _send_data_frame(dut, samples[:N_IN])
    await ClockCycles(dut.clk, 20)
    exp = ref_model_score(samples, sel, 0)
    assert _to_signed(int(dut.last_score.value), OUT_W) == exp, (
        f"score {_to_signed(int(dut.last_score.value), OUT_W)} != {exp}")


# --------------------------------------------------------------------------- #
# 3. values near selected ADC extrema                                         #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_adc_extrema(dut):
    await _start(dut)
    dut.threshold.value = 1 << 18
    lo = -(1 << 15)      # -32768
    hi = (1 << 15) - 1   #  32767
    samples = [hi, lo, hi, lo, hi, lo, hi, lo]
    await _send_data_frame(dut, samples)
    await ClockCycles(dut.clk, 20)
    # FEAT_W=8 saturates both ADC extrema to 0xff; the generated core treats
    # that input byte as signed -1, which the integer reference models.
    exp = ref_model_score(samples, list(range(N_IN)), 0)
    assert _to_signed(int(dut.last_score.value), OUT_W) == exp


# --------------------------------------------------------------------------- #
# 4/5/6. threshold below / at / above                                         #
# --------------------------------------------------------------------------- #
async def _one_frame_score(dut, sample0):
    """Send a frame and return the selected generated-core score."""
    samples = [sample0] + [0] * (N_IN - 1)
    await _send_data_frame(dut, samples)
    await ClockCycles(dut.clk, 20)
    return _to_signed(int(dut.last_score.value), OUT_W)


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_threshold_below(dut):
    await _start(dut)
    # score for [100, 0, ...] is -27; threshold above it -> no fire.
    dut.threshold.value = -26
    sc = await _one_frame_score(dut, 100)
    assert sc == -27
    assert int(dut.trigger_count.value) == 0
    assert int(dut.trigger_out.value) == 0


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_threshold_at(dut):
    await _start(dut)
    # score == threshold -> fires (>= comparison).
    dut.threshold.value = -27
    sc = await _one_frame_score(dut, 100)
    assert sc == -27
    assert int(dut.trigger_count.value) == 1


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_threshold_above(dut):
    await _start(dut)
    dut.threshold.value = -28
    sc = await _one_frame_score(dut, 100)  # -27 > -28
    assert sc == -27
    assert int(dut.trigger_count.value) == 1
    # Pulse is pulse_width (4) cycles wide: sample trigger_out during the pulse.
    # (already past here; just check it fired and returned low)
    await ClockCycles(dut.clk, 20)
    assert int(dut.trigger_out.value) == 0


# --------------------------------------------------------------------------- #
# 7. refractory suppression                                                   #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=400, timeout_unit="us")
async def test_refractory(dut):
    await _start(dut)
    dut.threshold.value = -28
    dut.pulse_width.value = 2
    dut.refractory_cycles.value = 40   # long dead time
    # First frame fires. The decision chain (frame_end -> feature_valid -> actual
    # generated core -> score latch -> FSM) is several cycles after the last beat, so
    # allow margin.
    await _send_data_frame(dut, [100] + [0] * (N_IN - 1))
    await ClockCycles(dut.clk, 12)
    assert int(dut.trigger_count.value) == 1
    # Immediately send another above-threshold frame while still refractory.
    await _send_data_frame(dut, [100] + [0] * (N_IN - 1))
    await ClockCycles(dut.clk, 12)
    assert int(dut.trigger_count.value) == 1, "refractory should suppress the 2nd trigger"
    # After the refractory window, a new above-threshold frame fires again.
    await ClockCycles(dut.clk, 60)
    await _send_data_frame(dut, [100] + [0] * (N_IN - 1))
    await ClockCycles(dut.clk, 12)
    assert int(dut.trigger_count.value) == 2


# --------------------------------------------------------------------------- #
# 8. repeated neural samples while ML is busy -> counted skip, no lost sample  #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=400, timeout_unit="us")
async def test_busy_overrun(dut):
    await _start(dut)
    dut.threshold.value = 1 << 18  # don't care about triggering here
    # A DATA_FRAME may legally carry fewer channels than N_IN features. To force
    # the busy/overrun path deterministically with the generated core's busy window, stream
    # several *short* 1-channel frames with no idle gap: each frame_end is ~2
    # cycles apart, well inside the core's ~4-cycle busy window, so all but the
    # first inference in a burst are SKIPPED. Samples must still all be counted.
    NFRAMES = 5
    for i in range(NFRAMES):
        await _send_data_frame(dut, [5 + i])  # 1-channel frame (2 beats)
    await ClockCycles(dut.clk, 20)
    assert int(dut.frames_seen.value) == NFRAMES
    assert int(dut.samples_seen.value) == NFRAMES  # 1 sample per frame, none lost
    assert int(dut.inferences_started.value) >= 1
    # Skips are counted, not silent, and ml_overrun mirrors inferences_skipped.
    assert int(dut.inferences_skipped.value) == int(dut.ml_overrun.value)
    assert int(dut.ml_overrun.value) >= 1, "tight back-to-back frames must record an overrun"
    # Every epoch is accounted for: started + skipped == frames processed.
    assert int(dut.inferences_started.value) + int(dut.inferences_skipped.value) == NFRAMES


# --------------------------------------------------------------------------- #
# 9. ML disabled while raw acquisition continues                              #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_ml_disabled_acq_continues(dut):
    await _start(dut)
    dut.ml_enable.value = 0
    dut.threshold.value = 0  # would fire if enabled
    await _send_data_frame(dut, [100] * N_IN)
    await ClockCycles(dut.clk, 20)
    # Acquisition counters still advance: the stream is snooped regardless.
    assert int(dut.frames_seen.value) == 1
    assert int(dut.samples_seen.value) == N_IN
    # But no inference and no trigger while disabled.
    assert int(dut.inferences_started.value) == 0
    assert int(dut.trigger_count.value) == 0
    # acq_tready is owned by the tb, never by the DUT: it stayed as we set it.
    assert int(dut.acq_tready.value) == 1


# --------------------------------------------------------------------------- #
# 10. ML reset while raw acquisition continues                                #
# --------------------------------------------------------------------------- #
@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_ml_reset_acq_continues(dut):
    await _start(dut)
    # First, a normal frame produces an inference.
    dut.threshold.value = 1 << 18
    await _send_data_frame(dut, [50] * N_IN)
    await ClockCycles(dut.clk, 10)
    assert int(dut.inferences_started.value) == 1

    # Assert reset (models the ML core held in reset) WHILE a frame is streaming.
    # The acquisition stream on the bus is unaffected; only the tap's own state
    # clears. We prove acquisition "continues" by driving the bus during reset and
    # confirming the tb-owned tready is still honored (no DUT interference).
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    # Bus keeps moving during reset; the tb still drives tready.
    dut.acq_tvalid.value = 1
    dut.acq_tready_en.value = 1
    dut.acq_tdata.value = _header_word(MSG_DATA_FRAME, N_IN * 4)
    await ClockCycles(dut.clk, 3)
    dut.acq_tvalid.value = 0
    assert int(dut.acq_tready.value) == 1  # DUT never drove tready low
    # Counters cleared by reset.
    assert int(dut.samples_seen.value) == 0
    assert int(dut.inferences_started.value) == 0

    # Release reset; acquisition + inference resume cleanly.
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)
    await _send_data_frame(dut, [60] * N_IN)
    await ClockCycles(dut.clk, 10)
    assert int(dut.frames_seen.value) == 1
    assert int(dut.inferences_started.value) == 1


# --------------------------------------------------------------------------- #
# pytest entrypoint                                                           #
# --------------------------------------------------------------------------- #
def test_runner():
    try:
        from cocotb_tools.runner import get_runner  # cocotb >= 2.0
    except ImportError:  # pragma: no cover
        from cocotb.runner import get_runner  # cocotb 1.x

    tb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tb")
    gate_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gateware")

    sources = [
        os.path.join(tb_dir, "axi4_stream_interface.sv"),
        os.path.join(gate_dir, "ml_trigger_fsm.sv"),
        os.path.join(gate_dir, "ml_sample_tap.sv"),
        os.path.join(gate_dir, "ml_feature_engine.sv"),
        os.path.join(gate_dir, "ml_tap_peripheral.sv"),
        os.path.join(gate_dir, "radiant_hls4ml_canary_generated.sv"),
        os.path.join(gate_dir, "radiant_hls4ml_canary.sv"),
        os.path.join(tb_dir, "ml_tap_peripheral_tb.sv"),
    ]

    sim = os.environ.get("SIM", "icarus")
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="ml_tap_peripheral_tb",
        always=True,
    )
    runner.test(hdl_toplevel="ml_tap_peripheral_tb", test_module="test_ml_tap_peripheral")
