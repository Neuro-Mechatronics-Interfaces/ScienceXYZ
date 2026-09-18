`timescale 1ns / 1ps

// Thin port adapter for the exact RTL emitted by
// tools/generate_canary.py. The generated module is kept verbatim in
// radiant_hls4ml_canary_generated.sv so synthesis and cocotb use the same
// artifact.
module radiant_hls4ml_canary (
    input  logic        clk,
    input  logic [63:0] x_in_bits,
    output logic [79:0] out
);
    __myproject__myproject u_generated (
        .clk(clk), .x_in_bits(x_in_bits), .out(out)
    );
endmodule
