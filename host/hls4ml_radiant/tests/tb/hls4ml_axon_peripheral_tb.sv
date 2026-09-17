`timescale 1ns / 1ps

// Testbench top for hls4ml_axon_peripheral_top.
//
// Exposes flat rx_*/tx_* AXI-stream signals (the naming cocotbext.axi's
// AxiStreamBus.from_prefix expects) and wires them to the SDK-style
// axi4_stream_interface instances the peripheral binds to. This mirrors the
// pattern in the SDK's own axon_test_source_tb.sv.
//
// The wrapped inference core is a stub (`hls4ml_core`) in the same test
// directory so the wrapper's framing/latency logic can be verified without the
// real (large) generated RTL. A second target swaps in the generated core.

module hls4ml_axon_peripheral_tb #(
    parameter int N_IN    = 8,
    parameter int IN_W    = 16,
    parameter int N_OUT   = 4,
    parameter int OUT_W   = 36,
    parameter int LATENCY = 2
) (
    input  logic clk,
    input  logic rst,

    // RX (host -> peripheral) flat signals
    input  logic [31:0] rx_tdata,
    input  logic        rx_tvalid,
    output logic        rx_tready,
    input  logic        rx_tlast,

    // TX (peripheral -> host) flat signals
    output logic [31:0] tx_tdata,
    output logic        tx_tvalid,
    input  logic        tx_tready,
    output logic        tx_tlast
);

    axi4_stream_interface #(.DATA_WIDTH(32)) rx_axis (.clk(clk), .rst(rst));
    axi4_stream_interface #(.DATA_WIDTH(32)) tx_axis (.clk(clk), .rst(rst));

    // Bind flat RX signals into the interface (TB drives rx as a producer).
    assign rx_axis.tvalid = rx_tvalid;
    assign rx_axis.tdata  = rx_tdata;
    assign rx_axis.tlast  = rx_tlast;
    assign rx_axis.tkeep  = '1;
    assign rx_axis.tid    = '0;
    assign rx_axis.tdest  = '0;
    assign rx_axis.tuser  = '0;
    assign rx_tready      = rx_axis.tready;

    // Expose TX interface signals to flat outputs (TB reads tx as a consumer).
    assign tx_tdata   = tx_axis.tdata;
    assign tx_tvalid  = tx_axis.tvalid;
    assign tx_tlast   = tx_axis.tlast;
    assign tx_axis.tready = tx_tready;

    hls4ml_axon_peripheral_top #(
        .N_IN(N_IN), .IN_W(IN_W), .N_OUT(N_OUT), .OUT_W(OUT_W), .LATENCY(LATENCY)
    ) dut (
        .clk(clk),
        .rst(rst),
        .periph_addr(32'hF001),
        .rx_axis(rx_axis),
        .tx_axis(tx_axis)
    );

endmodule
