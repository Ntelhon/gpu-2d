// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Gradient ramp RAM and loader (rendering.md §10.1; arch.md §6.4; Phase 4 assumption A4).
//
// 256 x ARGB32 words (A<<24 | R<<16 | G<<8 | B, little-endian in memory), loaded from GRAD_RAMP_ADDR
// (4-byte aligned) through a read client as one 1024-byte request. Storage: WPB = DATA_W/32 SRAM banks,
// ramp word w in bank w mod WPB at address w / WPB. Lane l of response beat k holds the word
// w = k*WPB + l - lead (lead = word offset of the ramp start in its beat); lanes outside the requested
// bytes have zero strobes. The lanes of one beat map to distinct banks, so a whole beat is written per
// clock and the client never back-pressures R.
//
// The loaded copy may be reused by a later draw until a new kick is popped (kick_i: the CPU writes ramps
// before kicking, commands.md §3) or the GPU accepts a write (wr_i: a draw may have written the ramp);
// both only affect later draws, not the draw that is using the ramp (its own pixel writes would
// otherwise invalidate it). need_i (one cycle, at the start of a gradient draw, address need_addr_i)
// starts a load unless the reusable copy holds that address; ready_o: the ramp of the last need_i is
// in the RAM.
//
// Lookup stream: index + PASS_W carried bits in, ramp word + carried bits out, one per clock (the read
// is issued only when the 2-entry output FIFO has room for its result). Feed it only while ready_o.
// stop_i: no new request; idle_o ignores an unfinished load; lookups still pass but read nothing (beats of
// an unfinished load may be landing; the results are discarded). clr_i invalidates and clears everything.

module g2d_ramp #(
  parameter int unsigned DATA_W = 64,
  parameter int unsigned PASS_W = 8,
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned WPB = DATA_W / 32,
  localparam int unsigned LW = $clog2(WPB),
  localparam int unsigned GI = $clog2(256 / WPB)
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  input  logic              kick_i,
  input  logic              wr_i,
  input  logic              need_i,
  input  logic [31:0]       need_addr_i,
  output logic              ready_o,
  // read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  input  logic [DB-1:0]     rd_rsp_strb_i,
  // lookup
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic [7:0]        in_idx_i,
  input  logic [PASS_W-1:0] in_pass_i,
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic [31:0]       out_word_o,
  output logic [PASS_W-1:0] out_pass_o,
  output logic              idle_o,
  // v1.8: RAM read/write collision this cycle (DBG_STATUS.RAM_COLL, arch.md §9.1)
  output logic              ram_coll_o
);

  typedef enum logic [1:0] {L_IDLE, L_REQ, L_DATA} lstate_e;
  lstate_e     ls_q;
  logic        valid_q;         // the RAM holds a reusable copy of the ramp at addr_q
  logic        loaded_q;        // the RAM holds the ramp of the last need_i
  logic [31:0] addr_q;
  logic [8:0]  wbase_q;        // ramp word of lane 0 of the next beat, plus `lead` (k*WPB)

  assign ready_o        = loaded_q && ls_q == L_IDLE;
  assign rd_req_valid_o = (ls_q == L_REQ) && !stop_i;
  assign rd_req_addr_o  = addr_q;
  assign rd_req_len_o   = 16'd1023;
  assign rd_rsp_ready_o = 1'b1;

  // ---------------------------------------------------------------- beat -> banks
  logic                 rd_beat, last_word;
  logic [WPB-1:0]       bank_we;
  logic [WPB-1:0][GI-1:0] bank_waddr;
  logic [WPB-1:0][31:0] bank_wdata;
  logic [LW:0]          lead;

  assign rd_beat = rd_rsp_valid_i && ls_q == L_DATA;
  if (WPB > 1) begin : g_lead
    assign lead = (LW + 1)'(addr_q[$clog2(DB)-1:2]);
  end else begin : g_nolead
    assign lead = '0;
  end

  always_comb begin
    bank_we    = '0;
    bank_waddr = '0;
    bank_wdata = '0;
    last_word  = 1'b0;
    for (int l = 0; l < int'(WPB); l++) begin
      logic [9:0] w;      // ramp word + WPB (kept non-negative before the lead is subtracted)
      logic [9:0] wi;     // ramp word index, 0..255 for requested lanes
      w  = 10'(wbase_q) + 10'(l) + 10'(WPB) - 10'(lead);
      wi = w - 10'(WPB);
      if (rd_beat && rd_rsp_strb_i[4 * l]) begin
        bank_we[wi[LW-1:0]]    = 1'b1;
        bank_waddr[wi[LW-1:0]] = wi[7:LW];
        bank_wdata[wi[LW-1:0]] = rd_rsp_data_i[32 * l +: 32];
        if (wi == 10'd255) last_word = 1'b1;
      end
    end
  end

  // ---------------------------------------------------------------- lookup
  logic                 rd_issue, inflight_q;
  logic [1:0]           of_cnt;
  logic                 of_ready_unused;
  logic [PASS_W-1:0]    pass_q;
  logic [LW-1:0]        lane_q;       // bank of the word being read
  logic [WPB-1:0][31:0] bank_rdata;
  logic [WPB-1:0]       bank_coll;
  assign ram_coll_o = |bank_coll;

  assign rd_issue   = in_valid_i && (2'(of_cnt) + 2'(inflight_q) - 2'(out_valid_o & out_ready_i) <= 2'd1);
  assign in_ready_o = rd_issue;

  for (genvar b = 0; b < WPB; b++) begin : g_bank
    g2d_ram_1r1w #(.WIDTH(32), .DEPTH(256 / WPB), .BE_W(1)) u_ram (
      .clk_i,
      .we_i    (bank_we[b]),
      .waddr_i (bank_waddr[b]),
      .wdata_i (bank_wdata[b]),
      .wbe_i   (1'b1),
      .re_i    (rd_issue & ~stop_i),
      .raddr_i (in_idx_i[7:LW]),
      .rdata_o (bank_rdata[b]),
      .coll_o  (bank_coll[b])
    );
  end

  always_ff @(posedge clk_i) begin
    if (rd_issue) begin
      pass_q <= in_pass_i;
      lane_q <= in_idx_i[LW-1:0];
    end
  end

  g2d_fifo #(.WIDTH(32 + PASS_W), .DEPTH(2)) u_of (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (inflight_q),
    .in_ready_o  (of_ready_unused),
    .in_data_i   ({bank_rdata[lane_q], pass_q}),
    .out_valid_o (out_valid_o),
    .out_ready_i (out_ready_i),
    .out_data_o  ({out_word_o, out_pass_o}),
    .count_o     (of_cnt)
  );

  // ---------------------------------------------------------------- control
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ls_q       <= L_IDLE;
      valid_q    <= 1'b0;
      loaded_q   <= 1'b0;
      inflight_q <= 1'b0;
    end else if (clr_i) begin
      ls_q       <= L_IDLE;
      valid_q    <= 1'b0;
      loaded_q   <= 1'b0;
      inflight_q <= 1'b0;
    end else begin
      inflight_q <= rd_issue;
      if (kick_i || wr_i) valid_q <= 1'b0;
      unique case (ls_q)
        L_IDLE: if (need_i) begin
          if (valid_q && !kick_i && !wr_i && addr_q == need_addr_i) begin
            loaded_q <= 1'b1;                        // reuse
          end else begin
            ls_q     <= L_REQ;
            valid_q  <= 1'b0;
            loaded_q <= 1'b0;
            addr_q   <= need_addr_i;
          end
        end
        L_REQ: if (rd_req_ready_i && !stop_i) begin
          ls_q    <= L_DATA;
          wbase_q <= '0;
        end
        L_DATA: if (rd_beat) begin
          wbase_q <= wbase_q + 9'(WPB);
          if (last_word) begin
            ls_q     <= L_IDLE;
            loaded_q <= 1'b1;
            valid_q  <= !(kick_i || wr_i);
          end
        end
        default: ls_q <= L_IDLE;
      endcase
    end
  end

  assign idle_o = stop_i | (ls_q == L_IDLE);

  logic unused;
  always_comb begin
    unused = of_ready_unused;
    for (int l = 0; l < int'(DB); l++) if (l % 4 != 0) unused ^= rd_rsp_strb_i[l];   // word-granular strobes
  end

endmodule
