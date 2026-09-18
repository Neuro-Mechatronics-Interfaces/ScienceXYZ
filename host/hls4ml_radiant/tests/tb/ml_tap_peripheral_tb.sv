`timescale 1ns / 1ps

// Testbench top for ml_tap_peripheral_top.
//
// Models the real topology: an acquisition peripheral drives a DATA_FRAME stream
// (acq_*), the Science transport consumes it (acq_tready driven here), and the ML
// tap SNOOPS that same stream read-only. cocotb plays frames on the acq_* bus and
// controls acq_tready to prove the tap works regardless of transport pacing, and
// that the tap never sources acq_tready itself.
//
// Flat ports are exposed so cocotbext.axi's AxiStreamSource can drive the acq bus
// with the `acq` prefix. chan_sel and the runtime config are flat ports too.

module ml_tap_peripheral_tb #(
    parameter int SAMPLE_W = 16,
    parameter int N_IN     = 8,
    parameter int IN_W     = 8,
    parameter int N_OUT    = 4,
    parameter int OUT_W    = 20,
    parameter int MAX_CH   = 512,
    parameter int SEL_W    = $clog2(MAX_CH),
    parameter int SIDX_W   = $clog2(N_OUT)
) (
    input  logic                 clk,
    input  logic                 rst,

    // Acquisition stream the tap snoops (driven by cocotb source + tb tready).
    input  logic                 acq_tvalid,
    input  logic [31:0]          acq_tdata,
    input  logic                 acq_tlast,
    output logic                 acq_tready,      // driven by the tb, NOT the DUT
    input  logic                 acq_tready_en,   // tb control: gate transport acceptance

    // Runtime control.
    input  logic                 ml_enable,
    input  logic                 feat_passthrough,
    input  logic [N_IN*SEL_W-1:0] chan_sel_flat,  // packed chan_sel
    input  logic [SIDX_W-1:0]    score_index,
    input  logic signed [OUT_W-1:0] threshold,
    input  logic                 trig_polarity,
    input  logic [23:0]          pulse_width,
    input  logic [23:0]          refractory_cycles,

    // Outputs / status.
    output logic                 trigger_out,
    output logic signed [OUT_W-1:0] last_score,
    output logic                 refractory,
    output logic [31:0]          samples_seen,
    output logic [31:0]          frames_seen,
    output logic [31:0]          inferences_started,
    output logic [31:0]          inferences_completed,
    output logic [31:0]          inferences_skipped,
    output logic [31:0]          ml_overrun,
    output logic [31:0]          trigger_count
);

    // Transport acceptance: cocotb toggles acq_tready_en to model backpressure
    // from the real transport. The DUT never drives this.
    assign acq_tready = acq_tready_en;

    // Unpack chan_sel.
    logic [SEL_W-1:0] chan_sel [N_IN];
    genvar g;
    generate
        for (g = 0; g < N_IN; g++) begin : g_sel
            assign chan_sel[g] = chan_sel_flat[g*SEL_W +: SEL_W];
        end
    endgenerate

    ml_tap_peripheral_top #(
        .SAMPLE_W(SAMPLE_W),
        .N_IN    (N_IN),
        .IN_W    (IN_W),
        .N_OUT   (N_OUT),
        .OUT_W   (OUT_W),
        .MAX_CH  (MAX_CH)
    ) dut (
        .clk                  (clk),
        .rst                  (rst),
        .acq_tvalid           (acq_tvalid),
        .acq_tdata            (acq_tdata),
        .acq_tlast            (acq_tlast),
        .acq_tready           (acq_tready),
        .ml_enable            (ml_enable),
        .feat_passthrough     (feat_passthrough),
        .chan_sel             (chan_sel),
        .score_index          (score_index),
        .threshold            (threshold),
        .trig_polarity        (trig_polarity),
        .pulse_width          (pulse_width),
        .refractory_cycles    (refractory_cycles),
        .trigger_out          (trigger_out),
        .last_score           (last_score),
        .refractory           (refractory),
        .samples_seen         (samples_seen),
        .frames_seen          (frames_seen),
        .inferences_started   (inferences_started),
        .inferences_completed (inferences_completed),
        .inferences_skipped   (inferences_skipped),
        .ml_overrun           (ml_overrun),
        .trigger_count        (trigger_count)
    );

endmodule
