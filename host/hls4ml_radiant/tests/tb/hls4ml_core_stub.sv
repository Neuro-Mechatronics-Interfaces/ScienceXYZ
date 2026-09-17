`timescale 1ns / 1ps

// hls4ml_core (stub)
// ------------------
// A stand-in for the hls4ml-generated inference core with the SAME port
// contract (clk, x_in_bits, out) and the SAME 2-cycle flopped-I/O latency, so
// the Science AXI wrapper's framing and latency handling can be verified with a
// core whose output is exactly predictable in the cocotb reference.
//
// The "computation" is a trivial, deterministic per-lane transform:
//   out[o] = sign_extend(x_in_bits[o]) * 3 - o    (o = 0..N_OUT-1)
// using the low N_OUT inputs. It exercises sign, multiply-width growth and a
// per-output offset without depending on the real network. Swap this module for
// firmware/myproject.sv (top __<proj>__<proj>) to test the generated core.

module hls4ml_core #(
    parameter int N_IN  = 8,
    parameter int IN_W  = 16,
    parameter int N_OUT = 4,
    parameter int OUT_W = 36
) (
    input  logic                    clk,
    input  logic [N_IN*IN_W-1:0]    x_in_bits,
    output logic [N_OUT*OUT_W-1:0]  out
);
    // Stage 0: register inputs (matches XLS flop_inputs).
    logic [IN_W-1:0] in_flop [N_IN];
    always_ff @(posedge clk) begin
        for (int i = 0; i < N_IN; i++) in_flop[i] <= x_in_bits[i*IN_W +: IN_W];
    end

    // Combinational transform.
    logic signed [OUT_W-1:0] comb [N_OUT];
    always_comb begin
        for (int o = 0; o < N_OUT; o++) begin
            logic signed [IN_W-1:0] xi;
            xi = in_flop[o];
            comb[o] = OUT_W'(xi) * 3 - o;
        end
    end

    // Stage 1: register outputs (matches XLS flop_outputs).
    logic [OUT_W-1:0] out_flop [N_OUT];
    always_ff @(posedge clk) begin
        for (int o = 0; o < N_OUT; o++) out_flop[o] <= comb[o];
    end

    always_comb begin
        for (int o = 0; o < N_OUT; o++) out[o*OUT_W +: OUT_W] = out_flop[o];
    end
endmodule
