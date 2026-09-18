`timescale 1ns / 1ps

// Source-independent feature engine. It consumes canonical sample events:
// valid, epoch_end, channel, and signed sample. The default feature is a
// causal EMA of rectified magnitude; passthrough selects the latest magnitude.

module ml_feature_engine #(
    parameter int SAMPLE_W = 16,
    parameter int N_TAP = 8,
    parameter int FEAT_W = 8,
    parameter int MAX_CH = 512,
    parameter int ENERGY_SHIFT = 4
) (
    input  logic clk,
    input  logic rst,
    input  logic sample_valid,
    input  logic epoch_end,
    input  logic [$clog2(MAX_CH)-1:0] sample_channel,
    input  logic signed [SAMPLE_W-1:0] sample_value,
    input  logic ml_enable,
    input  logic feat_passthrough,
    input  logic [$clog2(MAX_CH)-1:0] chan_sel [N_TAP],
    input  logic core_busy,
    input  logic inference_done,
    output logic feature_valid,
    output logic [N_TAP*FEAT_W-1:0] feature_bits,
    output logic [31:0] inferences_started,
    output logic [31:0] inferences_completed,
    output logic [31:0] inferences_skipped,
    output logic [31:0] ml_overrun
);
    logic [FEAT_W-1:0] energy [N_TAP];
    logic [FEAT_W-1:0] last_mag [N_TAP];

    function automatic [FEAT_W-1:0] abs_sat(input logic signed [SAMPLE_W-1:0] s);
        logic signed [SAMPLE_W:0] sx;
        logic signed [SAMPLE_W:0] a;
        begin
            sx = {{1{s[SAMPLE_W-1]}}, s};
            a = (sx < 0) ? -sx : sx;
            abs_sat = (a > (1 << FEAT_W) - 1) ? {FEAT_W{1'b1}} : a[FEAT_W-1:0];
        end
    endfunction

    function automatic [FEAT_W-1:0] ema_step(
        input logic [FEAT_W-1:0] e, input logic [FEAT_W-1:0] mag
    );
        logic [FEAT_W:0] nxt;
        begin
            nxt = {1'b0, e} - ({1'b0, e} >> ENERGY_SHIFT)
                + ({1'b0, mag} >> ENERGY_SHIFT);
            ema_step = nxt[FEAT_W] ? {FEAT_W{1'b1}} : nxt[FEAT_W-1:0];
        end
    endfunction

    wire [FEAT_W-1:0] current_mag = abs_sat(sample_value);
    integer k;
    always_ff @(posedge clk) begin
        if (rst) begin
            feature_valid <= 1'b0;
            feature_bits <= '0;
            inferences_started <= '0;
            inferences_completed <= '0;
            inferences_skipped <= '0;
            ml_overrun <= '0;
            for (k = 0; k < N_TAP; k++) begin
                energy[k] <= '0;
                last_mag[k] <= '0;
            end
        end else begin
            feature_valid <= 1'b0;
            if (inference_done)
                inferences_completed <= inferences_completed + 1'b1;

            if (sample_valid) begin
                for (k = 0; k < N_TAP; k++) begin
                    if (chan_sel[k] == sample_channel) begin
                        last_mag[k] <= current_mag;
                        energy[k] <= ema_step(energy[k], current_mag);
                    end
                end
            end

            if (epoch_end && ml_enable) begin
                // Use next-state for the last beat so the final sample belongs
                // to the epoch being presented to the model.
                for (k = 0; k < N_TAP; k++) begin
                    if (sample_valid && (chan_sel[k] == sample_channel)) begin
                        feature_bits[k*FEAT_W +: FEAT_W] <= feat_passthrough
                            ? current_mag : ema_step(energy[k], current_mag);
                    end else begin
                        feature_bits[k*FEAT_W +: FEAT_W] <= feat_passthrough
                            ? last_mag[k] : energy[k];
                    end
                end
                if (!core_busy) begin
                    feature_valid <= 1'b1;
                    inferences_started <= inferences_started + 1'b1;
                end else begin
                    inferences_skipped <= inferences_skipped + 1'b1;
                    ml_overrun <= ml_overrun + 1'b1;
                end
            end
        end
    end
endmodule
