`timescale 1ns / 1ps

// ml_trigger_fsm
// --------------
// Generic, neuroscience-agnostic decision/trigger state machine that turns a
// scalar signed score into a timed digital trigger pulse. It embeds NO model or
// electrode semantics: the caller picks which core output becomes `score` (a
// selected output neuron, a class-index score, or a scalar), so the same FSM
// serves argmax-style and threshold-style decoders alike.
//
// Behaviour (deterministic, characterized in cycles):
//
//   when (enable AND score_valid AND !refractory AND
//         (polarity ? score <= threshold : score >= threshold)):
//       assert trigger_out for PULSE cycles (pulse_width, runtime)
//       then enter refractory for REFRACTORY cycles (refractory_cycles, runtime)
//
// All comparisons are signed. `polarity`:
//   0 -> fire when score >= threshold   (above-threshold detector, default)
//   1 -> fire when score <= threshold   (below-threshold detector)
//
// The raw `score` and a sticky `trigger_count` are exposed so the model output
// stays observable independently of the trigger (the task requires that argmax
// is not the only usable output). Everything is registered off one fabric clock;
// there is no combinational path from score to trigger_out.
//
// Runtime-configurable inputs (Level-1 trigger configuration): enable, threshold,
// polarity, pulse_width, refractory_cycles. Hold them stable while a score is
// presented; they are sampled at the accept edge.

module ml_trigger_fsm #(
    parameter int SCORE_W = 36,   // width of the signed score
    parameter int TIME_W  = 24    // width of pulse/refractory counters (cycles)
) (
    input  logic                     clk,
    input  logic                     rst,          // sync active-high

    // Decision input (one-cycle strobe with a stable score).
    input  logic                     score_valid,
    input  logic signed [SCORE_W-1:0] score,

    // Runtime configuration.
    input  logic                     enable,
    input  logic signed [SCORE_W-1:0] threshold,
    input  logic                     polarity,         // 0: >=, 1: <=
    input  logic [TIME_W-1:0]        pulse_width,      // trigger high cycles (>=1)
    input  logic [TIME_W-1:0]        refractory_cycles,// dead cycles after a pulse

    // Outputs.
    output logic                     trigger_out,
    output logic signed [SCORE_W-1:0] last_score,      // raw score, always observable
    output logic                     refractory,       // 1 while suppressing
    output logic [31:0]              trigger_count     // sticky count of fired triggers
);

    typedef enum logic [1:0] { T_IDLE, T_PULSE, T_REFRACT } tstate_t;
    tstate_t tstate;

    logic [TIME_W-1:0] pulse_cnt;
    logic [TIME_W-1:0] refr_cnt;

    // A fire is allowed only from IDLE (so an in-progress pulse or refractory
    // window is never re-triggered) and only when enabled.
    wire cmp_hit  = polarity ? (score <= threshold) : (score >= threshold);
    wire can_fire = (tstate == T_IDLE) && enable && score_valid && cmp_hit;

    assign trigger_out = (tstate == T_PULSE);
    assign refractory  = (tstate == T_REFRACT);

    always_ff @(posedge clk) begin
        if (rst) begin
            tstate        <= T_IDLE;
            pulse_cnt     <= '0;
            refr_cnt      <= '0;
            last_score    <= '0;
            trigger_count <= '0;
        end else begin
            // Capture the score whenever a decision is presented, regardless of
            // whether it fires, so the host can read the raw model output.
            if (score_valid) last_score <= score;

            case (tstate)
                T_IDLE: begin
                    if (can_fire) begin
                        // pulse_width==0 would produce a zero-length pulse; clamp
                        // to one cycle so a fire is always observable.
                        pulse_cnt     <= (pulse_width == 0) ? '0 : (pulse_width - 1'b1);
                        tstate        <= T_PULSE;
                        trigger_count <= trigger_count + 1'b1;
                    end
                end
                T_PULSE: begin
                    if (pulse_cnt == 0) begin
                        if (refractory_cycles == 0) begin
                            tstate <= T_IDLE;
                        end else begin
                            refr_cnt <= refractory_cycles - 1'b1;
                            tstate   <= T_REFRACT;
                        end
                    end else begin
                        pulse_cnt <= pulse_cnt - 1'b1;
                    end
                end
                T_REFRACT: begin
                    if (refr_cnt == 0) tstate <= T_IDLE;
                    else               refr_cnt <= refr_cnt - 1'b1;
                end
                default: tstate <= T_IDLE;
            endcase
        end
    end

endmodule
