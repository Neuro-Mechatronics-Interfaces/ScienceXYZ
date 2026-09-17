`timescale 1ns / 1ps

// axi4_stream_interface
// ---------------------
// Minimal, contract-faithful stand-in for the Axon peripheral SDK's
// `axi4_stream_interface`, for HARDWARE-FREE simulation of a peripheral that
// binds to the SDK contract. The real interface ships inside the closed
// axon-peripheral-sdk (referenced by scir_sdk.rdf as
// .../cores/science/axi/interface.sv) and is treated as read-only/external by
// this repository, so it is not vendored here.
//
// This shim declares exactly the signals and the two modports
// (`.main` = producer, `.secondary` = consumer) that the SDK peripheral contract
// uses, so a peripheral written against the SDK compiles and simulates
// unchanged. It carries no SDK-specific behavior.
//
// If/when a simulation needs the real SDK interface (e.g. inside the SDK Docker
// image), point the testbench sources at the SDK copy instead of this shim.

interface axi4_stream_interface #(
    parameter int DATA_WIDTH = 32,
    parameter int ID_WIDTH   = 8,
    parameter int DEST_WIDTH = 8,
    parameter int USER_WIDTH = 1
) (
    input logic clk,
    input logic rst
);
    logic                      tvalid;
    logic                      tready;
    logic [DATA_WIDTH-1:0]     tdata;
    logic [(DATA_WIDTH/8)-1:0] tkeep;
    logic                      tlast;
    logic [ID_WIDTH-1:0]       tid;
    logic [DEST_WIDTH-1:0]     tdest;
    logic [USER_WIDTH-1:0]     tuser;

    // Producer side: drives data, observes tready.
    modport main(
        input  clk, rst, tready,
        output tvalid, tdata, tkeep, tlast, tid, tdest, tuser
    );

    // Consumer side: drives tready, observes data.
    modport secondary(
        input  clk, rst, tvalid, tdata, tkeep, tlast, tid, tdest, tuser,
        output tready
    );
endinterface
