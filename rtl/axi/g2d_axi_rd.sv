// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4 read engine (arch.md §4.2). N_CLIENTS read clients, each with a fixed ARID = client index.
//
// Client request: byte address + byte length - 1 (1..65536 bytes). The engine splits it into INCR bursts
// that never cross a MAX_BURST_BYTES-aligned boundary (hence never a 4 KB boundary), with full-width
// beats. Responses for a client come back in request order as DATA_W-aligned beats with a byte mask
// (strb) of the requested bytes and `last` on the final beat of the request. A burst answered with
// SLVERR/DECERR still delivers its beats (err = 1) and raises err_rd_o with the burst address.
//
// Each client must accept every beat it requested without depending on other clients (credit-based
// clients). RREADY is the ready of the client addressed by RID.
//
// Defence in depth: a burst outside [win_lo, win_hi] is not issued; err_win_o pulses and the rest of
// that request is dropped (its client receives nothing; the GPU halts on the error).
// stop_i: no new burst is issued and the rest of the current request is dropped. Outstanding bursts
// still complete. clr_i (soft reset, only after drain) clears all state.

module g2d_axi_rd #(
  parameter int unsigned N_CLIENTS       = 1,
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned ADDR_W          = 32,
  parameter int unsigned ID_W            = 4,
  parameter int unsigned MAX_BURST_BYTES = 256,
  parameter int unsigned RD_OUTSTANDING  = 16,  // v1.7 (Q-PERF-2): was 8
  localparam int unsigned DB    = DATA_W / 8,
  localparam int unsigned OFF_W = $clog2(DB),
  localparam int unsigned CI_W  = (N_CLIENTS > 1) ? $clog2(N_CLIENTS) : 1
) (
  input  logic                        clk_i,
  input  logic                        rst_ni,
  input  logic                        clr_i,
  input  logic                        stop_i,
  input  logic [31:0]                 win_lo_i,
  input  logic [31:0]                 win_hi_i,
  input  logic [31:0]                 base_hi_i,
  // client requests
  input  logic [N_CLIENTS-1:0]        req_valid_i,
  output logic [N_CLIENTS-1:0]        req_ready_o,
  input  logic [N_CLIENTS-1:0][31:0]  req_addr_i,
  input  logic [N_CLIENTS-1:0][15:0]  req_len_i,      // bytes - 1
  // client responses (data shared, valid one-hot)
  output logic [N_CLIENTS-1:0]        rsp_valid_o,
  input  logic [N_CLIENTS-1:0]        rsp_ready_i,
  output logic [DATA_W-1:0]           rsp_data_o,
  output logic [DB-1:0]               rsp_strb_o,
  output logic                        rsp_last_o,
  output logic                        rsp_err_o,
  // AXI4 AR/R
  output logic [ID_W-1:0]             m_axi_arid_o,
  output logic [ADDR_W-1:0]           m_axi_araddr_o,
  output logic [7:0]                  m_axi_arlen_o,
  output logic                        m_axi_arvalid_o,
  input  logic                        m_axi_arready_i,
  input  logic [ID_W-1:0]             m_axi_rid_i,
  input  logic [DATA_W-1:0]           m_axi_rdata_i,
  input  logic [1:0]                  m_axi_rresp_i,
  input  logic                        m_axi_rlast_i,
  input  logic                        m_axi_rvalid_i,
  output logic                        m_axi_rready_o,
  // status
  output logic                        idle_o,
  output logic                        err_rd_o,
  output logic                        err_win_o,
  output logic [31:0]                 err_addr_o
);

  localparam int unsigned MB_W  = $clog2(MAX_BURST_BYTES);
  localparam int unsigned CNT_W = $clog2(RD_OUTSTANDING + 1);
  localparam int unsigned META_W = 32 + 2 * DB + 1;

  // ---------------------------------------------------------------- request arbitration
  logic            arb_valid;
  logic [CI_W-1:0] arb_idx;
  logic            req_take;

  g2d_rr_arb #(.N(N_CLIENTS), .REG_PICK(1'b1)) u_arb (   // v1.7: registered choice (timing, T-1)
    .clk_i,
    .rst_ni,
    .clr_i,
    .req_i   (req_valid_i),
    .ack_i   (req_take),
    .valid_o (arb_valid),
    .idx_o   (arb_idx)
  );

  // ---------------------------------------------------------------- splitter
  logic            act_q;
  logic [CI_W-1:0] cli_q;
  logic [32:0]     cur_q;     // next byte address; bit 32 = the request wrapped past 2^32
  logic [16:0]     rem_q;     // bytes remaining (1..65536)

  assign req_take = arb_valid & ~act_q;
  always_comb begin
    req_ready_o = '0;
    req_ready_o[arb_idx] = req_take;
  end

  // current burst
  logic [MB_W:0]   to_bound;
  logic [16:0]     chunk;
  logic [32:0]     end_x;     // last byte, 33 bits to see wrap
  logic [31:0]     end_a;
  logic [8:0]      beats;
  logic [DB-1:0]   head, tail;
  logic            last_burst, in_win;

  assign to_bound   = (MB_W + 1)'(MAX_BURST_BYTES) - {1'b0, cur_q[MB_W-1:0]};
  assign chunk      = (rem_q < 17'(to_bound)) ? rem_q : 17'(to_bound);
  assign last_burst = (chunk == rem_q);
  assign end_x      = cur_q + 33'(chunk) - 33'd1;
  assign end_a      = end_x[31:0];
  assign beats      = 9'((end_a[MB_W-1:OFF_W] - cur_q[MB_W-1:OFF_W])) + 9'd1;
  assign in_win     = ~end_x[32] & (cur_q[31:0] >= win_lo_i) & (end_a <= win_hi_i);

  always_comb begin
    for (int unsigned b = 0; b < DB; b++) begin
      head[b] = (b >= int'(cur_q[OFF_W-1:0]));
      tail[b] = (b <= int'(end_a[OFF_W-1:0]));
    end
  end

  // AR register
  logic              ar_valid_q;
  logic [CI_W-1:0]   ar_id_q;
  logic [31:0]       ar_addr_q;
  logic [7:0]        ar_len_q;
  logic [CNT_W-1:0]  cnt_q;
  logic [N_CLIENTS-1:0] meta_in_ready;
  logic              ar_free, can_issue, issue, drop;

  assign ar_free   = ~ar_valid_q | m_axi_arready_i;
  assign can_issue = act_q & ar_free & meta_in_ready[cli_q] & (cnt_q < CNT_W'(RD_OUTSTANDING));
  assign drop      = act_q & (stop_i | ~in_win);
  assign issue     = can_issue & ~drop;

  logic r_done;  // last beat of a burst accepted

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      act_q      <= 1'b0;
      cli_q      <= '0;
      cur_q      <= '0;
      rem_q      <= '0;
      ar_valid_q <= 1'b0;
      ar_id_q    <= '0;
      ar_addr_q  <= '0;
      ar_len_q   <= '0;
      cnt_q      <= '0;
    end else if (clr_i) begin
      act_q      <= 1'b0;
      ar_valid_q <= 1'b0;
      cnt_q      <= '0;
    end else begin
      if (m_axi_arready_i) ar_valid_q <= 1'b0;
      if (req_take) begin
        act_q <= 1'b1;
        cli_q <= arb_idx;
        cur_q <= {1'b0, req_addr_i[arb_idx]};
        rem_q <= 17'(req_len_i[arb_idx]) + 17'd1;
      end else if (drop) begin
        act_q <= 1'b0;
      end else if (issue) begin
        ar_valid_q <= 1'b1;
        ar_id_q    <= cli_q;
        ar_addr_q  <= {cur_q[31:OFF_W], {OFF_W{1'b0}}};
        ar_len_q   <= 8'(beats - 9'd1);
        cur_q      <= end_x + 33'd1;
        rem_q      <= rem_q - chunk;
        if (last_burst) act_q <= 1'b0;
      end
      cnt_q <= cnt_q + CNT_W'(issue) - CNT_W'(r_done);
    end
  end

  assign err_win_o  = act_q & ~stop_i & ~in_win;

  assign m_axi_arid_o    = ID_W'(ar_id_q);
  assign m_axi_arlen_o   = ar_len_q;
  assign m_axi_arvalid_o = ar_valid_q;
  if (ADDR_W > 32) begin : g_addr_hi
    assign m_axi_araddr_o = {base_hi_i[ADDR_W-33:0], ar_addr_q};
    if (ADDR_W < 64) begin : g_unused_hi
      logic unused_base;
      assign unused_base = ^base_hi_i[31:ADDR_W-32];
    end
  end else begin : g_addr_lo
    assign m_axi_araddr_o = ar_addr_q;
    logic unused_base;
    assign unused_base = ^base_hi_i;
  end

  // ---------------------------------------------------------------- per-client burst metadata
  // {addr, head, tail, last_of_request}
  logic [N_CLIENTS-1:0]              meta_valid, meta_pop;
  logic [N_CLIENTS-1:0][META_W-1:0]  meta_out;
  logic [N_CLIENTS-1:0]              first_q;  // next beat of this client is the first of a burst

  for (genvar c = 0; c < N_CLIENTS; c++) begin : g_meta
    logic [$clog2(RD_OUTSTANDING + 1)-1:0] unused_count;
    g2d_fifo #(.WIDTH(META_W), .DEPTH(RD_OUTSTANDING)) u_meta (
      .clk_i,
      .rst_ni,
      .clr_i,
      .in_valid_i  (issue && cli_q == CI_W'(c)),
      .in_ready_o  (meta_in_ready[c]),
      .in_data_i   ({cur_q[31:0], head, tail, last_burst}),
      .out_valid_o (meta_valid[c]),
      .out_ready_i (meta_pop[c]),
      .out_data_o  (meta_out[c]),
      .count_o     (unused_count)
    );
  end

  // ---------------------------------------------------------------- R path
  logic            rid_ok;
  logic [CI_W-1:0] rcli;
  logic [META_W-1:0] m;
  logic            r_hs;

  assign rid_ok = (m_axi_rid_i < ID_W'(N_CLIENTS));
  assign rcli   = CI_W'(m_axi_rid_i);
  assign m      = meta_out[rcli];
  assign m_axi_rready_o = rid_ok ? rsp_ready_i[rcli] : 1'b1;   // unknown ID: accept and drop
  assign r_hs   = m_axi_rvalid_i & m_axi_rready_o;
  assign r_done = r_hs & m_axi_rlast_i & rid_ok;

  always_comb begin
    rsp_valid_o = '0;
    rsp_valid_o[rcli] = m_axi_rvalid_i & rid_ok & meta_valid[rcli];
    meta_pop = '0;
    meta_pop[rcli] = r_done;
  end

  logic [DB-1:0] m_head, m_tail;
  assign m_head = m[1 + DB +: DB];
  assign m_tail = m[1 +: DB];

  assign rsp_data_o = m_axi_rdata_i;
  assign rsp_strb_o = (first_q[rcli] ? m_head : '1) & (m_axi_rlast_i ? m_tail : '1);
  assign rsp_last_o = m_axi_rlast_i & m[0];
  assign rsp_err_o  = m_axi_rresp_i[1];

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      first_q <= '1;
    end else if (clr_i) begin
      first_q <= '1;
    end else if (r_hs & rid_ok) begin
      first_q[rcli] <= m_axi_rlast_i;
    end
  end

  assign err_rd_o   = r_hs & rid_ok & m_axi_rresp_i[1];
  assign err_addr_o = err_win_o ? cur_q[31:0] : m[1 + 2 * DB +: 32];
  assign idle_o     = ~act_q & ~ar_valid_q & (cnt_q == '0);

  logic unused;
  assign unused = m_axi_rresp_i[0];   // SLVERR and DECERR are treated alike

endmodule
