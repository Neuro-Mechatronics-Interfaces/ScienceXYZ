`timescale 1ns / 1ps

// Science DATA_FRAME decoder and passive ML-tap boundary.
//
// The decoder is deliberately separate from ml_feature_engine: the latter
// consumes only canonical sample events (valid, channel, signed sample, and
// epoch_end). A future Nixel/NeRV producer can feed the same feature engine
// without changing the ML or trigger logic.
//
// This module is a PASSIVE SNOOP. It observes the acquisition tx_axis and never
// drives tready or any other signal back toward acquisition/transport.

module ml_sample_tap #(
    parameter int SAMPLE_W  = 16,
    parameter int N_TAP     = 8,
    parameter int FEAT_W    = 8,
    parameter int MAX_CH    = 512,
    parameter int ENERGY_SHIFT = 4,
    parameter logic [15:0] MSG_DATA_FRAME = 16'h0056
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic                    acq_tvalid,
    input  logic [31:0]             acq_tdata,
    input  logic                    acq_tlast,
    input  logic                    acq_tready,
    input  logic                    ml_enable,
    input  logic                    feat_passthrough,
    input  logic [$clog2(MAX_CH)-1:0] chan_sel [N_TAP],
    output logic                    feature_valid,
    output logic [N_TAP*FEAT_W-1:0] feature_bits,
    input  logic                    core_busy,
    input  logic                    inference_done,
    output logic [31:0]             samples_seen,
    output logic [31:0]             frames_seen,
    output logic [31:0]             inferences_started,
    output logic [31:0]             inferences_completed,
    output logic [31:0]             inferences_skipped,
    output logic [31:0]             ml_overrun
);
    typedef enum logic [1:0] { S_HDR, S_PAYLOAD, S_SKIP } state_t;
    state_t state;
    logic [$clog2(MAX_CH+1)-1:0] ch_idx;
    wire acq_beat = acq_tvalid & acq_tready;

    // Canonical sample event. No DATA_FRAME header or transport detail crosses
    // the boundary into the ML feature logic.
    wire sample_valid = acq_beat && (state == S_PAYLOAD);
    wire epoch_end = sample_valid && acq_tlast;
    wire [$clog2(MAX_CH)-1:0] sample_channel = ch_idx[$clog2(MAX_CH)-1:0];
    wire signed [SAMPLE_W-1:0] sample_value = $signed(acq_tdata[SAMPLE_W-1:0]);
    wire empty_epoch = acq_beat && (state == S_HDR) &&
                       (acq_tdata[31:16] == MSG_DATA_FRAME) && acq_tlast;

    always_ff @(posedge clk) begin
        if (rst) begin
            state        <= S_HDR;
            ch_idx       <= '0;
            samples_seen <= '0;
            frames_seen  <= '0;
        end else if (acq_beat) begin
            case (state)
                S_HDR: begin
                    ch_idx <= '0;
                    if (acq_tdata[31:16] == MSG_DATA_FRAME && !acq_tlast)
                        state <= S_PAYLOAD;
                    else if (acq_tdata[31:16] == MSG_DATA_FRAME && acq_tlast)
                        frames_seen <= frames_seen + 1'b1;
                    else if (!acq_tlast)
                        state <= S_SKIP;
                end
                S_PAYLOAD: begin
                    samples_seen <= samples_seen + 1'b1;
                    if (acq_tlast) begin
                        frames_seen <= frames_seen + 1'b1;
                        state <= S_HDR;
                    end else begin
                        ch_idx <= ch_idx + 1'b1;
                    end
                end
                S_SKIP: begin
                    if (acq_tlast) state <= S_HDR;
                end
                default: state <= S_HDR;
            endcase
        end
    end

    ml_feature_engine #(
        .SAMPLE_W(SAMPLE_W), .N_TAP(N_TAP), .FEAT_W(FEAT_W),
        .MAX_CH(MAX_CH), .ENERGY_SHIFT(ENERGY_SHIFT)
    ) u_feature_engine (
        .clk(clk), .rst(rst), .sample_valid(sample_valid),
        .epoch_end(epoch_end | empty_epoch), .sample_channel(sample_channel),
        .sample_value(sample_value), .ml_enable(ml_enable),
        .feat_passthrough(feat_passthrough), .chan_sel(chan_sel),
        .core_busy(core_busy), .inference_done(inference_done),
        .feature_valid(feature_valid), .feature_bits(feature_bits),
        .inferences_started(inferences_started),
        .inferences_completed(inferences_completed),
        .inferences_skipped(inferences_skipped), .ml_overrun(ml_overrun)
    );
endmodule
