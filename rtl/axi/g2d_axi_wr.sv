// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4 write engine (arch.md §4.2). One producer, single AWID 0, in-order B.
//
// Producer contract: a request gives a byte address and byte length - 1 (1..65536 bytes); the producer
// then supplies exactly ceil-covering DATA_W-aligned beats for that range, in order, each with a byte
// mask (bytes outside the requested range are masked by the engine, so a producer may pass all ones).
// The engine splits the range into INCR bursts that never cross a MAX_BURST_BYTES-aligned boundary.
//
// AW and W are decoupled: a burst's W descriptor is queued when its AW is raised, so W may run ahead
// of the AW handshake (no deadlock with slaves that wait for WVALID before AWREADY). W goes out through
// a register slice.
//
// A burst outside [win_lo, win_hi] (defence in depth, err_win_o, reported once) turns the rest of its
// request into drop bursts; so does stop_i for every burst produced while it is set. Drop bursts: the producer's beats are consumed and discarded, no AW is issued. So a
// producer that already started a request can always finish it, and stop never violates AXI.
// BRESP SLVERR/DECERR raises err_wr_o with the burst address.

module g2d_axi_wr #(
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned ADDR_W          = 32,
  parameter int unsigned ID_W            = 4,
  parameter int unsigned MAX_BURST_BYTES = 256,
  parameter int unsigned WR_OUTSTANDING  = 8,
  localparam int unsigned DB    = DATA_W / 8,
  localparam int unsigned OFF_W = $clog2(DB)
) (
  input  logic                clk_i,
  input  logic                rst_ni,
  input  logic                clr_i,
  input  logic                stop_i,
  input  logic [31:0]         win_lo_i,
  input  logic [31:0]         win_hi_i,
  input  logic [31:0]         base_hi_i,
  // producer
  input  logic                req_valid_i,
  output logic                req_ready_o,
  input  logic [31:0]         req_addr_i,
  input  logic [15:0]         req_len_i,       // bytes - 1
  input  logic                dat_valid_i,
  output logic                dat_ready_o,
  input  logic [DATA_W-1:0]   dat_data_i,
  input  logic [DB-1:0]       dat_strb_i,
  // AXI4 AW/W/B
  output logic [ID_W-1:0]     m_axi_awid_o,
  output logic [ADDR_W-1:0]   m_axi_awaddr_o,
  output logic [7:0]          m_axi_awlen_o,
  output logic                m_axi_awvalid_o,
  input  logic                m_axi_awready_i,
  output logic [DATA_W-1:0]   m_axi_wdata_o,
  output logic [DB-1:0]       m_axi_wstrb_o,
  output logic                m_axi_wlast_o,
  output logic                m_axi_wvalid_o,
  input  logic                m_axi_wready_i,
  input  logic [ID_W-1:0]     m_axi_bid_i,
  input  logic [1:0]          m_axi_bresp_i,
  input  logic                m_axi_bvalid_i,
  output logic                m_axi_bready_o,
  // status
  output logic                idle_o,
  output logic                err_wr_o,
  output logic                err_win_o,
  output logic [31:0]         err_addr_o
);

  localparam int unsigned MB_W   = $clog2(MAX_BURST_BYTES);
  localparam int unsigned CNT_W  = $clog2(WR_OUTSTANDING + 1);
  localparam int unsigned WD_W   = 8 + 2 * DB + 1;          // {len, head, tail, drop}
  localparam int unsigned WD_DEPTH = WR_OUTSTANDING + 2;     // room for drop bursts beyond the AW limit

  // ---------------------------------------------------------------- splitter
  logic        act_q;
  logic [32:0] cur_q;      // next byte address; bit 32 = the request wrapped past 2^32
  logic [16:0] rem_q;
  logic        bad_q;     // the request hit the window check: drop the rest

  assign req_ready_o = ~act_q;

  logic [MB_W:0] to_bound;
  logic [16:0]   chunk;
  logic [32:0]   end_x;
  logic [31:0]   end_a;
  logic [8:0]    beats;
  logic [DB-1:0] head, tail;
  logic          last_burst, in_win;

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

  logic             aw_valid_q;
  logic [31:0]      aw_addr_q;
  logic [7:0]       aw_len_q;
  logic [CNT_W-1:0] cnt_q;
  logic             wd_in_ready, ba_in_ready;
  logic             drop, emit, issue_aw, b_hs;

  assign drop     = stop_i | bad_q | ~in_win;
  // a burst is emitted (descriptor queued) when there is room; real bursts also need a free AW slot
  assign emit     = act_q & wd_in_ready &
                    (drop | ((~aw_valid_q | m_axi_awready_i) & ba_in_ready & (cnt_q < CNT_W'(WR_OUTSTANDING))));
  assign issue_aw = emit & ~drop;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      act_q      <= 1'b0;
      cur_q      <= '0;
      rem_q      <= '0;
      bad_q      <= 1'b0;
      aw_valid_q <= 1'b0;
      aw_addr_q  <= '0;
      aw_len_q   <= '0;
      cnt_q      <= '0;
    end else if (clr_i) begin
      act_q      <= 1'b0;
      bad_q      <= 1'b0;
      aw_valid_q <= 1'b0;
      cnt_q      <= '0;
    end else begin
      if (m_axi_awready_i) aw_valid_q <= 1'b0;
      if (req_valid_i & req_ready_o) begin
        act_q <= 1'b1;
        bad_q <= 1'b0;
        cur_q <= {1'b0, req_addr_i};
        rem_q <= 17'(req_len_i) + 17'd1;
      end else if (emit) begin
        cur_q <= end_x + 33'd1;
        rem_q <= rem_q - chunk;
        if (err_win_o) bad_q <= 1'b1;
        if (last_burst) act_q <= 1'b0;
      end
      if (issue_aw) begin
        aw_valid_q <= 1'b1;
        aw_addr_q  <= {cur_q[31:OFF_W], {OFF_W{1'b0}}};
        aw_len_q   <= 8'(beats - 9'd1);
      end
      cnt_q <= cnt_q + CNT_W'(issue_aw) - CNT_W'(b_hs);
    end
  end

  assign err_win_o = emit & ~stop_i & ~bad_q & ~in_win;

  assign m_axi_awid_o    = '0;
  assign m_axi_awlen_o   = aw_len_q;
  assign m_axi_awvalid_o = aw_valid_q;
  if (ADDR_W > 32) begin : g_addr_hi
    assign m_axi_awaddr_o = {base_hi_i[ADDR_W-33:0], aw_addr_q};
    if (ADDR_W < 64) begin : g_unused_hi
      logic unused_base;
      assign unused_base = ^base_hi_i[31:ADDR_W-32];
    end
  end else begin : g_addr_lo
    assign m_axi_awaddr_o = aw_addr_q;
    logic unused_base;
    assign unused_base = ^base_hi_i;
  end

  // ---------------------------------------------------------------- W path
  logic            wd_valid, wd_pop;
  logic [WD_W-1:0] wd;
  logic [7:0]      wd_len;
  logic [DB-1:0]   wd_head, wd_tail;
  logic            wd_drop;
  logic [7:0]      beat_q;
  logic            w_first, w_last;

  logic [$clog2(WD_DEPTH + 1)-1:0]       unused_count_wd;
  logic [$clog2(WR_OUTSTANDING + 1)-1:0] unused_count_ba;

  g2d_fifo #(.WIDTH(WD_W), .DEPTH(WD_DEPTH)) u_wdesc (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (emit),
    .in_ready_o  (wd_in_ready),
    .in_data_i   ({8'(beats - 9'd1), head, tail, drop}),
    .out_valid_o (wd_valid),
    .out_ready_i (wd_pop),
    .out_data_o  (wd),
    .count_o     (unused_count_wd)
  );

  assign {wd_len, wd_head, wd_tail, wd_drop} = wd;
  assign w_first = (beat_q == '0);
  assign w_last  = (beat_q == wd_len);

  logic                     ws_in_valid, ws_in_ready, ws_valid;
  logic [DATA_W+DB:0]       ws_out;

  assign ws_in_valid = wd_valid & dat_valid_i & ~wd_drop;
  assign dat_ready_o = wd_valid & (wd_drop | ws_in_ready);
  assign wd_pop      = dat_valid_i & dat_ready_o & w_last;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                       beat_q <= '0;
    else if (clr_i)                    beat_q <= '0;
    else if (dat_valid_i & dat_ready_o) beat_q <= w_last ? '0 : beat_q + 8'd1;
  end

  g2d_spill_reg #(.WIDTH(DATA_W + DB + 1)) u_wslice (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (ws_in_valid),
    .in_ready_o  (ws_in_ready),
    .in_data_i   ({dat_data_i, dat_strb_i & (w_first ? wd_head : '1) & (w_last ? wd_tail : '1), w_last}),
    .out_valid_o (ws_valid),
    .out_ready_i (m_axi_wready_i),
    .out_data_o  (ws_out)
  );

  assign m_axi_wvalid_o = ws_valid;
  assign {m_axi_wdata_o, m_axi_wstrb_o, m_axi_wlast_o} = ws_out;

  // ---------------------------------------------------------------- B path
  logic [31:0] b_addr;
  logic        ba_valid;

  g2d_fifo #(.WIDTH(32), .DEPTH(WR_OUTSTANDING)) u_baddr (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (issue_aw),
    .in_ready_o  (ba_in_ready),
    .in_data_i   (cur_q[31:0]),
    .out_valid_o (ba_valid),
    .out_ready_i (b_hs),
    .out_data_o  (b_addr),
    .count_o     (unused_count_ba)
  );

  assign m_axi_bready_o = 1'b1;
  assign b_hs           = m_axi_bvalid_i;
  assign err_wr_o       = b_hs & m_axi_bresp_i[1];
  assign err_addr_o     = err_win_o ? cur_q[31:0] : b_addr;
  assign idle_o         = ~act_q & ~aw_valid_q & (cnt_q == '0) & ~wd_valid & ~ws_valid;

  logic unused;
  assign unused = ^{m_axi_bid_i, ba_valid, m_axi_bresp_i[0]};

endmodule
