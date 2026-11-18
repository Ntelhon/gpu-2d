// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4 slave (DATA_W 32..512, any ID width, INCR / FIXED / WRAP bursts) to AXI4-Lite master (32-bit),
// configuration-side adapter (v1.9, F-6; replaces the PULP axi_to_axi_lite + axi_lite_dw_converter pair in the
// Cheshire integration).
// One burst at a time; writes and reads alternate when both wait. Every beat is split into 32-bit word accesses
// (g2d_axil_single): writes access each word of the beat with a non-zero strobe nibble (strobes passed through),
// reads access each word the beat's transfer size covers and return them in their byte lanes (other lanes 0).
// Responses: SLVERR if any word access of the burst (B) or of the beat (R) failed, else OKAY. Exclusive accesses
// (AxLOCK = 1) are treated as normal accesses and answered OKAY, i.e. "exclusive access failed" (AXI4 A7.2).
// AxCACHE / AxPROT / AxQOS / AxREGION are ignored. No AXI5 atomics (AWATOP): a system that can issue them puts an
// atomics filter in front (docs/INTEGRATION.md). Bursts must be legal AXI4 (size <= DATA_W, WRAP length 2/4/8/16).

module g2d_axi2axil #(
  parameter int unsigned DATA_W = 64,
  parameter int unsigned ADDR_W = 32,
  parameter int unsigned ID_W   = 4,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  // AXI4 slave
  input  logic [ID_W-1:0]   s_axi_awid_i,
  input  logic [ADDR_W-1:0] s_axi_awaddr_i,
  input  logic [7:0]        s_axi_awlen_i,
  input  logic [2:0]        s_axi_awsize_i,
  input  logic [1:0]        s_axi_awburst_i,
  input  logic              s_axi_awlock_i,
  input  logic [3:0]        s_axi_awcache_i,
  input  logic [2:0]        s_axi_awprot_i,
  input  logic [3:0]        s_axi_awqos_i,
  input  logic [3:0]        s_axi_awregion_i,
  input  logic              s_axi_awvalid_i,
  output logic              s_axi_awready_o,
  input  logic [DATA_W-1:0] s_axi_wdata_i,
  input  logic [DB-1:0]     s_axi_wstrb_i,
  input  logic              s_axi_wlast_i,
  input  logic              s_axi_wvalid_i,
  output logic              s_axi_wready_o,
  output logic [ID_W-1:0]   s_axi_bid_o,
  output logic [1:0]        s_axi_bresp_o,
  output logic              s_axi_bvalid_o,
  input  logic              s_axi_bready_i,
  input  logic [ID_W-1:0]   s_axi_arid_i,
  input  logic [ADDR_W-1:0] s_axi_araddr_i,
  input  logic [7:0]        s_axi_arlen_i,
  input  logic [2:0]        s_axi_arsize_i,
  input  logic [1:0]        s_axi_arburst_i,
  input  logic              s_axi_arlock_i,
  input  logic [3:0]        s_axi_arcache_i,
  input  logic [2:0]        s_axi_arprot_i,
  input  logic [3:0]        s_axi_arqos_i,
  input  logic [3:0]        s_axi_arregion_i,
  input  logic              s_axi_arvalid_i,
  output logic              s_axi_arready_o,
  output logic [ID_W-1:0]   s_axi_rid_o,
  output logic [DATA_W-1:0] s_axi_rdata_o,
  output logic [1:0]        s_axi_rresp_o,
  output logic              s_axi_rlast_o,
  output logic              s_axi_rvalid_o,
  input  logic              s_axi_rready_i,
  // AXI4-Lite master
  output logic [31:0]       m_axil_awaddr_o,
  output logic [2:0]        m_axil_awprot_o,
  output logic              m_axil_awvalid_o,
  input  logic              m_axil_awready_i,
  output logic [31:0]       m_axil_wdata_o,
  output logic [3:0]        m_axil_wstrb_o,
  output logic              m_axil_wvalid_o,
  input  logic              m_axil_wready_i,
  input  logic [1:0]        m_axil_bresp_i,
  input  logic              m_axil_bvalid_i,
  output logic              m_axil_bready_o,
  output logic [31:0]       m_axil_araddr_o,
  output logic [2:0]        m_axil_arprot_o,
  output logic              m_axil_arvalid_o,
  input  logic              m_axil_arready_i,
  input  logic [31:0]       m_axil_rdata_i,
  input  logic [1:0]        m_axil_rresp_i,
  input  logic              m_axil_rvalid_i,
  output logic              m_axil_rready_o
);

  if (DATA_W < 32 || DATA_W > 512 || (DATA_W & (DATA_W - 1)) != 0) begin : g_chk_dw
    $error("g2d_axi2axil: DATA_W must be a power of 2 in 32..512");
  end
  if (ADDR_W < 32 || ADDR_W > 64) begin : g_chk_aw
    $error("g2d_axi2axil: ADDR_W must be 32..64");
  end

  localparam int unsigned NW  = DATA_W / 32;            // 32-bit words per beat
  localparam int unsigned WIW = (NW > 1) ? $clog2(NW) : 1;
  localparam int unsigned LB  = $clog2(DB);             // byte-lane address bits

  typedef enum logic [2:0] {S_IDLE, S_WDATA, S_WWORDS, S_BRESP, S_RWORDS, S_RBEAT} state_e;
  state_e st_q;

  logic [ID_W-1:0]   id_q;
  logic [ADDR_W-1:0] addr_q;           // address of the current beat (AXI4 beat address rule)
  logic [ADDR_W-1:0] start_q;          // burst start (WRAP boundary)
  logic [7:0]        len_q, beat_q;
  logic [2:0]        size_q;
  logic [1:0]        burst_q;
  logic              err_q;            // write: any word failed in the burst; read: in the beat
  logic              last_wr_q;        // arbitration: the last burst was a write
  logic [DATA_W-1:0] wdata_q, rbuf_q;
  logic [DB-1:0]     wstrb_q;
  logic [WIW-1:0]    w_q;              // current word of the beat
  logic              sent_q;           // word access in flight

  // g2d_axil_single
  logic        req_valid, req_ready, rsp_valid, rsp_err;
  logic [31:0] req_addr, req_wdata, rsp_rdata;
  logic [3:0]  req_strb;

  // ---------------------------------------------------------------- beat geometry
  logic [ADDR_W-1:0] bytes, aligned, next_addr, wrap_len, wrap_lo;
  logic [WIW-1:0]    w_hi;
  logic [3:0]        nib;
  assign bytes    = ADDR_W'(1) << size_q;
  assign aligned  = addr_q & ~(bytes - ADDR_W'(1));
  assign wrap_len = ADDR_W'({1'b0, len_q} + 9'd1) << size_q;
  assign wrap_lo  = start_q & ~(wrap_len - ADDR_W'(1));
  always_comb begin
    next_addr = aligned + bytes;
    unique case (burst_q)
      2'b00:   next_addr = addr_q;                                             // FIXED
      2'b10:   if (next_addr >= wrap_lo + wrap_len) next_addr = wrap_lo;      // WRAP
      default: ;                                                               // INCR
    endcase
  end
  // words of the beat covered by its transfer size (reads); for size < 4 bytes the one word holding the address
  if (NW > 1) begin : g_wide
    logic [LB-1:0] lane_hi;
    assign lane_hi = aligned[LB-1:0] + LB'(bytes - ADDR_W'(1));
    assign w_hi    = WIW'(lane_hi >> 2);
  end else begin : g_narrow
    assign w_hi = '0;
  end
  assign nib = wstrb_q[w_q*4 +: 4];

  // first word a read beat at this address covers (the word holding the address)
  function automatic logic [WIW-1:0] first_word(input logic [LB-1:0] lanes);
    return WIW'(lanes >> 2);
  endfunction

  // ---------------------------------------------------------------- AXI4 slave side
  logic take_aw, take_ar;
  assign take_aw = (st_q == S_IDLE) & s_axi_awvalid_i & (~s_axi_arvalid_i | ~last_wr_q);
  assign take_ar = (st_q == S_IDLE) & s_axi_arvalid_i & ~take_aw;

  assign s_axi_awready_o = take_aw;
  assign s_axi_arready_o = take_ar;
  assign s_axi_wready_o  = (st_q == S_WDATA);
  assign s_axi_bvalid_o  = (st_q == S_BRESP);
  assign s_axi_bid_o     = id_q;
  assign s_axi_bresp_o   = err_q ? 2'b10 : 2'b00;
  assign s_axi_rvalid_o  = (st_q == S_RBEAT);
  assign s_axi_rid_o     = id_q;
  assign s_axi_rdata_o   = rbuf_q;
  assign s_axi_rresp_o   = err_q ? 2'b10 : 2'b00;
  assign s_axi_rlast_o   = (beat_q == len_q);

  // ---------------------------------------------------------------- word accesses
  logic wr_word_skip;   // write word with an all-zero strobe nibble: no access
  assign wr_word_skip = (st_q == S_WWORDS) & (nib == 4'h0);
  assign req_valid    = ((st_q == S_WWORDS) & ~wr_word_skip | (st_q == S_RWORDS)) & ~sent_q;
  assign req_addr     = {addr_q[31:LB], {LB{1'b0}}} + {{(32 - WIW - 2){1'b0}}, w_q, 2'b00};
  assign req_wdata    = wdata_q[w_q*32 +: 32];
  assign req_strb     = (st_q == S_WWORDS) ? nib : 4'h0;

  logic word_done, beat_words_done;
  assign word_done       = wr_word_skip | rsp_valid;
  assign beat_words_done = (st_q == S_WWORDS) ? (w_q == WIW'(NW - 1)) : (w_q == w_hi);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      st_q      <= S_IDLE;
      id_q      <= '0;
      addr_q    <= '0;
      start_q   <= '0;
      len_q     <= '0;
      beat_q    <= '0;
      size_q    <= '0;
      burst_q   <= '0;
      err_q     <= 1'b0;
      last_wr_q <= 1'b0;
      wdata_q   <= '0;
      wstrb_q   <= '0;
      rbuf_q    <= '0;
      w_q       <= '0;
      sent_q    <= 1'b0;
    end else begin
      if (req_valid && req_ready) sent_q <= 1'b1;
      if (rsp_valid)              sent_q <= 1'b0;
      unique case (st_q)
        S_IDLE: begin
          if (take_aw) begin
            st_q <= S_WDATA;  id_q <= s_axi_awid_i;  addr_q <= s_axi_awaddr_i;  start_q <= s_axi_awaddr_i;
            len_q <= s_axi_awlen_i;  size_q <= s_axi_awsize_i;  burst_q <= s_axi_awburst_i;
            beat_q <= '0;  err_q <= 1'b0;  last_wr_q <= 1'b1;
          end else if (take_ar) begin
            st_q <= S_RWORDS;  id_q <= s_axi_arid_i;  addr_q <= s_axi_araddr_i;  start_q <= s_axi_araddr_i;
            len_q <= s_axi_arlen_i;  size_q <= s_axi_arsize_i;  burst_q <= s_axi_arburst_i;
            beat_q <= '0;  err_q <= 1'b0;  last_wr_q <= 1'b0;  rbuf_q <= '0;
            w_q <= first_word(s_axi_araddr_i[LB-1:0]);
          end
        end
        S_WDATA: if (s_axi_wvalid_i) begin
          st_q    <= S_WWORDS;
          wdata_q <= s_axi_wdata_i;
          wstrb_q <= s_axi_wstrb_i;
          w_q     <= '0;
        end
        S_WWORDS: if (word_done) begin
          if (rsp_valid && rsp_err) err_q <= 1'b1;
          if (beat_words_done) begin
            if (beat_q == len_q) st_q <= S_BRESP;
            else begin
              st_q   <= S_WDATA;
              beat_q <= beat_q + 8'd1;
              addr_q <= next_addr;
            end
          end else begin
            w_q <= w_q + WIW'(1);
          end
        end
        S_BRESP: if (s_axi_bready_i) st_q <= S_IDLE;
        S_RWORDS: begin
          if (rsp_valid) begin
            if (rsp_err) err_q <= 1'b1;
            rbuf_q[w_q*32 +: 32] <= rsp_err ? 32'h0 : rsp_rdata;
            if (beat_words_done) st_q <= S_RBEAT;
            else                 w_q  <= w_q + WIW'(1);
          end
        end
        S_RBEAT: if (s_axi_rready_i) begin
          if (beat_q == len_q) st_q <= S_IDLE;
          else begin
            st_q   <= S_RWORDS;
            beat_q <= beat_q + 8'd1;
            addr_q <= next_addr;
            err_q  <= 1'b0;
            rbuf_q <= '0;
            w_q    <= first_word(next_addr[LB-1:0]);
          end
        end
        default: st_q <= S_IDLE;
      endcase
    end
  end

  g2d_axil_single u_axil (
    .clk_i,
    .rst_ni,
    .req_valid_i (req_valid),
    .req_ready_o (req_ready),
    .req_write_i (st_q == S_WWORDS),
    .req_addr_i  (req_addr),
    .req_wdata_i (req_wdata),
    .req_strb_i  (req_strb),
    .rsp_valid_o (rsp_valid),
    .rsp_rdata_o (rsp_rdata),
    .rsp_err_o   (rsp_err),
    .m_axil_awaddr_o,
    .m_axil_awprot_o,
    .m_axil_awvalid_o,
    .m_axil_awready_i,
    .m_axil_wdata_o,
    .m_axil_wstrb_o,
    .m_axil_wvalid_o,
    .m_axil_wready_i,
    .m_axil_bresp_i,
    .m_axil_bvalid_i,
    .m_axil_bready_o,
    .m_axil_araddr_o,
    .m_axil_arprot_o,
    .m_axil_arvalid_o,
    .m_axil_arready_i,
    .m_axil_rdata_i,
    .m_axil_rresp_i,
    .m_axil_rvalid_i,
    .m_axil_rready_o
  );

  logic unused;
  assign unused = ^{s_axi_awlock_i, s_axi_awcache_i, s_axi_awprot_i, s_axi_awqos_i, s_axi_awregion_i, s_axi_wlast_i,
                    s_axi_arlock_i, s_axi_arcache_i, s_axi_arprot_i, s_axi_arqos_i, s_axi_arregion_i};

endmodule
