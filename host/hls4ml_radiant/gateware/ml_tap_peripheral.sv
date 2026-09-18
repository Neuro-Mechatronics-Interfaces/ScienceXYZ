`timescale 1ns / 1ps

// ml_tap_peripheral_top
// ---------------------
// Science-integrated ML inference TAP: the full non-blocking chain
//
//   acquisition tx_axis (DATA_FRAME, snooped read-only)
//        -> ml_sample_tap  (channel decode + causal 8-feature energy adapter)
//        -> __myproject__myproject (RadiantBackend-generated 8->16->8->4 MLP)
//        -> generated-core valid alignment
//        -> score select   (pick one core output as the scalar decision score)
//        -> ml_trigger_fsm (threshold / refractory / pulse)
//        -> trigger_out
//
// This is the ScienceXYZ integration layer. It is NOT part of hls4ml, and it is
// distinct from hls4ml_axon_peripheral_top: that module is a host-driven
// request/response inference peripheral; THIS module is a passive consumer of the
// live neural acquisition stream. The two can coexist.
//
// ACQUISITION INDEPENDENCE (the primary acceptance criterion):
//   The acquisition stream enters only as read-only snoop inputs
//   (acq_tvalid/tdata/tlast/tready). This module drives NO signal back into the
//   acquisition path. Holding ml_enable=0 or rst=1, or a stuck core, cannot stall
//   or alter the neural stream -- see ml_sample_tap.sv header. trigger_out is an
//   internal signal; it is intentionally NOT assigned to a package pin here (pin
//   mapping is a separate, operator-gated concern).
//
// Clock domain: intended to run on the Science user-peripheral domain clkmc
// (40 MHz). The acquisition frame cadence (e.g. 2 kHz) is a data rate on that
// clock, decoupled from the fabric clock: there are ~20000 fabric cycles between
// 2 kHz epochs, far more than the core's LATENCY, so acquisition never waits.

module ml_tap_peripheral_top #(
    parameter int SAMPLE_W = 16,   // acquisition sample width (RHD2132: 16)
    parameter int N_IN     = 8,    // core input width  == number of tapped features
    parameter int IN_W     = 8,    // generated core input significand width
    parameter int N_OUT    = 4,    // core output width
    parameter int OUT_W    = 20,   // generated core output significand width
    parameter int MAX_CH   = 512,  // max source channels addressable by chan_sel
    parameter int ENERGY_SHIFT = 4
) (
    input  logic                    clk,
    input  logic                    rst,

    // ---- Read-only snoop of the acquisition peripheral's tx_axis ------------
    input  logic                    acq_tvalid,
    input  logic [31:0]             acq_tdata,
    input  logic                    acq_tlast,
    input  logic                    acq_tready,

    // ---- Runtime control (Level-1 trigger config + Level-2 channel select) --
    input  logic                    ml_enable,
    input  logic                    feat_passthrough,
    input  logic [$clog2(MAX_CH)-1:0] chan_sel [N_IN],
    input  logic [$clog2(N_OUT)-1:0]  score_index,        // which core output is the score
    input  logic signed [OUT_W-1:0] threshold,
    input  logic                    trig_polarity,        // 0: >=, 1: <=
    input  logic [23:0]             pulse_width,
    input  logic [23:0]             refractory_cycles,

    // ---- Outputs ------------------------------------------------------------
    output logic                    trigger_out,
    output logic signed [OUT_W-1:0] last_score,
    output logic                    refractory,

    // ---- Status (all read-only) ---------------------------------------------
    output logic [31:0]             samples_seen,
    output logic [31:0]             frames_seen,
    output logic [31:0]             inferences_started,
    output logic [31:0]             inferences_completed,
    output logic [31:0]             inferences_skipped,
    output logic [31:0]             ml_overrun,
    output logic [31:0]             trigger_count
);

    // ---- Tap: snoop + feature adapter ---------------------------------------
    logic                    feature_valid;
    logic [N_IN*IN_W-1:0]    feature_bits;
    logic                    core_busy;
    logic                    inference_done;

    ml_sample_tap #(
        .SAMPLE_W    (SAMPLE_W),
        .N_TAP       (N_IN),
        .FEAT_W      (IN_W),
        .MAX_CH      (MAX_CH),
        .ENERGY_SHIFT(ENERGY_SHIFT)
    ) u_tap (
        .clk                  (clk),
        .rst                  (rst),
        .acq_tvalid           (acq_tvalid),
        .acq_tdata            (acq_tdata),
        .acq_tlast            (acq_tlast),
        .acq_tready           (acq_tready),
        .ml_enable            (ml_enable),
        .feat_passthrough     (feat_passthrough),
        .chan_sel             (chan_sel),
        .feature_valid        (feature_valid),
        .feature_bits         (feature_bits),
        .core_busy            (core_busy),
        .inference_done       (inference_done),
        .samples_seen         (samples_seen),
        .frames_seen          (frames_seen),
        .inferences_started   (inferences_started),
        .inferences_completed (inferences_completed),
        .inferences_skipped   (inferences_skipped),
        .ml_overrun           (ml_overrun)
    );

    // ---- Actual RadiantBackend-generated core -------------------------------
    // This adapter contains the checked-in __myproject__myproject RTL. The
    // integration simulation compiles that same generated source.
    logic [N_OUT*OUT_W-1:0] core_out_bits;
    radiant_hls4ml_canary u_core (
        .clk       (clk),
        .x_in_bits (feature_bits),
        .out       (core_out_bits)
    );

    // The generated core is a fixed-latency pipeline with a registered input
    // boundary and a registered output boundary; it exposes no valid/ready
    // handshake, so the wrapper tracks validity with a shift register matched
    // to the core's pipeline depth. CORE_LATENCY is the number of clocks from
    // an accepted input to a valid `out`, i.e. (generated pipeline stages - 1).
    // The flat single-cycle core has CORE_LATENCY = 1; a pipelined core built
    // at a tighter clock_period (see synthesis/README.md pipeline sweep) has a
    // larger value. Bit 0 marks the input-accept edge; the top bit marks
    // generated-output availability.
    localparam int CORE_LATENCY = 3;  // 4-stage generated core (3ns / 40MHz-closing variant)
    logic [CORE_LATENCY:0] core_valid_pipe;
    logic                  model_input_accepted;
    logic                  model_output_valid;

    always_ff @(posedge clk) begin
        if (rst) begin
            core_valid_pipe <= '0;
        end else begin
            core_valid_pipe[0] <= feature_valid;
            for (int s = 1; s <= CORE_LATENCY; s++) begin
                core_valid_pipe[s] <= core_valid_pipe[s-1];
            end
        end
    end
    assign model_input_accepted = core_valid_pipe[0];
    assign model_output_valid = core_valid_pipe[CORE_LATENCY];
    assign inference_done = model_output_valid;
    assign core_busy = core_valid_pipe != '0;

    // ---- Score select + trigger FSM -----------------------------------------
    // Latch the selected core output at inference_done into the FSM's score.
    logic signed [OUT_W-1:0] score_reg;
    logic                    score_valid;
    // Mirrors the FSM's registered acceptance condition for latency probes.
    // It is an internal observability net, not a second trigger path.
    wire trigger_decision = score_valid && ml_enable && !refractory &&
        (trig_polarity ? (score_reg <= threshold) : (score_reg >= threshold));

    always_ff @(posedge clk) begin
        if (rst) begin
            score_reg   <= '0;
            score_valid <= 1'b0;
        end else begin
            score_valid <= 1'b0;
            if (model_output_valid) begin
                score_reg   <= core_out_bits[score_index*OUT_W +: OUT_W];
                score_valid <= 1'b1;
            end
        end
    end

    ml_trigger_fsm #(
        .SCORE_W(OUT_W),
        .TIME_W (24)
    ) u_trig (
        .clk               (clk),
        .rst               (rst),
        .score_valid       (score_valid),
        .score             (score_reg),
        .enable            (ml_enable),
        .threshold         (threshold),
        .polarity          (trig_polarity),
        .pulse_width       (pulse_width),
        .refractory_cycles (refractory_cycles),
        .trigger_out       (trigger_out),
        .last_score        (last_score),
        .refractory        (refractory),
        .trigger_count     (trigger_count)
    );

endmodule
