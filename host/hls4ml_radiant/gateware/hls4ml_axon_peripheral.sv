`timescale 1ns / 1ps

// hls4ml_axon_peripheral_top
// ---------------------------
// Science Axon peripheral wrapper around an hls4ml-generated inference core.
//
// This is the ScienceXYZ-specific integration layer described in the RadiantBackend
// bring-up: it is NOT part of hls4ml. It adapts the Axon peripheral SDK's
// AXI-stream frame contract to the parallel-vector interface that the hls4ml XLS /
// Radiant flow produces, so an operator can drop an EMG-decoder core behind the
// SciFi-2 Axon transport.
//
// Frame contract (identical to the SDK's axon_test_source_peripheral): one 32-bit
// word per beat, header word = {msg_type[31:16], len_bytes[15:0]}, payload words
// follow, tlast on the final beat.
//
//   Request  INFER_REQUEST (0x0060): N_IN payload words. Word i low IN_W bits are
//            input feature i (signed, hls4ml input fixed-point, low bits first).
//   Response INFER_RESULT  (0x0061): N_OUT * OUT_WORDS payload words. For each
//            output o, OUT_WORDS little-endian 32-bit words carry the OUT_W-bit
//            signed fixed-point result (low word first, sign-extended in the top
//            word). OUT_WORDS = ceil(OUT_W/32).
//
// The wrapped core has fixed, data-independent latency LATENCY (the hls4ml XLS
// core flops its inputs and outputs: 2 cycles for a purely combinational MLP).
// The wrapper latches the whole input vector, pulses the core, waits LATENCY
// cycles, then streams the packed outputs.
//
// Ports match the SDK peripheral contract exactly:
//   clk, rst (sync active-high), periph_addr[31:0], rx_axis (.secondary),
//   tx_axis (.main).

module hls4ml_axon_peripheral_top #(
    parameter int N_IN    = 8,     // number of input features
    parameter int IN_W    = 16,    // bits per input feature (hls4ml input width)
    parameter int N_OUT   = 4,     // number of output values
    parameter int OUT_W   = 36,    // bits per output value (hls4ml output width)
    parameter int LATENCY = 2      // core input->output latency in clocks
) (
    input  logic                    clk,
    input  logic                    rst,
    input  logic             [31:0] periph_addr,
    axi4_stream_interface.secondary rx_axis,
    axi4_stream_interface.main      tx_axis
);

    // Words needed to carry one OUT_W-bit output over 32-bit AXI beats.
    localparam int OUT_WORDS = (OUT_W + 31) / 32;

    // ---- Message-type opcodes -----------------------------------------------
    localparam logic [15:0] MSG_INFER_REQUEST = 16'h0060;
    localparam logic [15:0] MSG_INFER_RESULT  = 16'h0061;

    // ---- Packed core ports --------------------------------------------------
    logic [N_IN*IN_W-1:0]   core_in_bits;
    logic [N_OUT*OUT_W-1:0] core_out_bits;

    hls4ml_core u_core (
        .clk        (clk),
        .x_in_bits  (core_in_bits),
        .out        (core_out_bits)
    );

    // =========================================================================
    // RX path — collect an INFER_REQUEST's N_IN input words into core_in_bits.
    //
    // The wrapped core services one inference at a time, so the wrapper must not
    // accept a new request while the previous one is still computing or being
    // transmitted; otherwise the pending result is overwritten. `busy` (set at
    // req_valid, cleared when the result frame's last beat leaves) gates the
    // start of a new frame. tready stays high mid-frame so an in-progress frame
    // always completes.
    // =========================================================================
    logic busy;

    typedef enum logic [1:0] { RX_HEADER, RX_PAYLOAD, RX_DRAIN } rx_state_t;
    rx_state_t rx_state;

    // Accept beats except when idle-and-busy (i.e. do not start a new frame
    // while a result is still in flight).
    assign rx_axis.tready = !((rx_state == RX_HEADER) && busy);
    wire rx_beat = rx_axis.tvalid & rx_axis.tready;

    logic [$clog2(N_IN+1)-1:0] in_idx;
    logic                      req_valid;   // pulse: a full request has landed

    always_ff @(posedge clk) begin
        if (rst) begin
            rx_state     <= RX_HEADER;
            in_idx       <= '0;
            req_valid    <= 1'b0;
            core_in_bits <= '0;
        end else begin
            req_valid <= 1'b0;
            case (rx_state)
                RX_HEADER: begin
                    in_idx <= '0;
                    if (rx_beat) begin
                        if (rx_axis.tdata[31:16] == MSG_INFER_REQUEST && !rx_axis.tlast) begin
                            rx_state <= RX_PAYLOAD;
                        end else if (rx_axis.tdata[31:16] == MSG_INFER_REQUEST && rx_axis.tlast) begin
                            // Degenerate zero-length request: ignore.
                            rx_state <= RX_HEADER;
                        end else if (!rx_axis.tlast) begin
                            rx_state <= RX_DRAIN;
                        end
                    end
                end
                RX_PAYLOAD: begin
                    if (rx_beat) begin
                        // Little-endian: input i occupies bits [i*IN_W +: IN_W].
                        core_in_bits[in_idx*IN_W +: IN_W] <= rx_axis.tdata[IN_W-1:0];
                        if (in_idx == N_IN-1 || rx_axis.tlast) begin
                            req_valid <= (in_idx == N_IN-1);
                            if (rx_axis.tlast) rx_state <= RX_HEADER;
                            else               rx_state <= RX_DRAIN;
                        end else begin
                            in_idx <= in_idx + 1'b1;
                        end
                    end
                end
                RX_DRAIN: begin
                    if (rx_beat && rx_axis.tlast) rx_state <= RX_HEADER;
                end
                default: rx_state <= RX_HEADER;
            endcase
        end
    end

    // =========================================================================
    // Latency counter — after req_valid, wait LATENCY cycles for the core output
    // to settle, then raise result_due.
    // =========================================================================
    logic [$clog2(LATENCY+1)-1:0] lat_cnt;
    logic                         counting;
    logic                         result_due;
    logic                         result_taken;

    always_ff @(posedge clk) begin
        if (rst) begin
            lat_cnt    <= '0;
            counting   <= 1'b0;
            result_due <= 1'b0;
        end else begin
            if (req_valid) begin
                counting   <= 1'b1;
                lat_cnt    <= '0;
                result_due <= 1'b0;
            end else if (counting) begin
                if (lat_cnt == LATENCY[$clog2(LATENCY+1)-1:0]) begin
                    counting   <= 1'b0;
                    result_due <= 1'b1;
                end else begin
                    lat_cnt <= lat_cnt + 1'b1;
                end
            end else if (result_taken) begin
                result_due <= 1'b0;
            end
        end
    end

    // Latch the core output when the result becomes due, so streaming is stable
    // even if a new request arrives.
    logic [N_OUT*OUT_W-1:0] out_latched;
    always_ff @(posedge clk) begin
        if (result_due && !counting) out_latched <= core_out_bits;
    end

    // =========================================================================
    // TX path — emit INFER_RESULT: header, then N_OUT*OUT_WORDS payload words.
    // =========================================================================
    typedef enum logic [1:0] { TX_IDLE, TX_HEADER, TX_DATA } tx_state_t;
    tx_state_t tx_state;

    localparam int TOTAL_WORDS = N_OUT * OUT_WORDS;
    logic [$clog2(TOTAL_WORDS+1)-1:0] widx;
    wire  [15:0] payload_len_bytes = 16'(TOTAL_WORDS << 2);
    wire  tx_beat = tx_axis.tvalid & tx_axis.tready;

    assign result_taken = (tx_state == TX_IDLE) & result_due;

    // Sign-extend output value to OUT_WORDS*32 bits, then slice per word.
    localparam int OUT_EXT_W = OUT_WORDS * 32;
    function automatic [31:0] out_word(input int o, input int w);
        logic signed [OUT_W-1:0]     val;
        logic signed [OUT_EXT_W-1:0] ext;
        begin
            val = out_latched[o*OUT_W +: OUT_W];
            ext = OUT_EXT_W'(val);  // sign-extend to OUT_WORDS 32-bit words
            out_word = ext[w*32 +: 32];
        end
    endfunction

    wire int cur_o = widx / OUT_WORDS;
    wire int cur_w = widx % OUT_WORDS;

    always_comb begin
        tx_axis.tdata  = '0;
        tx_axis.tvalid = 1'b0;
        tx_axis.tlast  = 1'b0;
        tx_axis.tkeep  = '1;
        tx_axis.tid    = '0;
        tx_axis.tdest  = '0;
        tx_axis.tuser  = '0;
        case (tx_state)
            TX_HEADER: begin
                tx_axis.tdata  = {MSG_INFER_RESULT, payload_len_bytes};
                tx_axis.tvalid = 1'b1;
            end
            TX_DATA: begin
                tx_axis.tdata  = out_word(cur_o, cur_w);
                tx_axis.tvalid = 1'b1;
                tx_axis.tlast  = (widx == TOTAL_WORDS-1);
            end
            default: ;
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            tx_state <= TX_IDLE;
            widx     <= '0;
        end else begin
            case (tx_state)
                TX_IDLE: begin
                    widx <= '0;
                    if (result_due) tx_state <= TX_HEADER;
                end
                TX_HEADER: begin
                    if (tx_beat) tx_state <= TX_DATA;
                end
                TX_DATA: begin
                    if (tx_beat) begin
                        if (widx == TOTAL_WORDS-1) tx_state <= TX_IDLE;
                        else widx <= widx + 1'b1;
                    end
                end
                default: tx_state <= TX_IDLE;
            endcase
        end
    end

    // busy: a request is in flight (accepted through result fully transmitted).
    // Gates rx_tready so a new frame cannot overwrite a pending result. It is set
    // the moment the final input word is accepted (one cycle before the
    // registered req_valid pulse) so no new header can slip in during the
    // latency window, and cleared when the result frame's last beat leaves.
    wire last_input_beat = (rx_state == RX_PAYLOAD) && rx_beat && (in_idx == N_IN-1);

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
        end else begin
            if (last_input_beat) begin
                busy <= 1'b1;
            end else if (tx_state == TX_DATA && tx_beat && widx == TOTAL_WORDS-1) begin
                busy <= 1'b0;  // result frame's last beat just left
            end
        end
    end

    // periph_addr is unused — the SDK transport routes frames to this peripheral.
    // verilator lint_off UNUSED
    wire _unused = &{1'b0, periph_addr};
    // verilator lint_on UNUSED

endmodule
