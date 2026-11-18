// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Protocol checker for the AXI4 master (arch.md §4.2; verification-plan.md §2). Simulation only: bound into
// every g2d_axi_mst by dv/sva/g2d_binds.sv (so it runs in the AXI block bench and in every top-level
// bench); the port names equal the g2d_axi_mst port names, so the bind connects with .*.
//
// AXI4 master:
//   handshakes   AW, W, AR: VALID held and payload stable while VALID && !READY
//   bursts       INCR only, AxSIZE = log2(DATA_W/8), beat-aligned address, AxLEN + 1 <= MAX_BURST_BYTES
//                / (DATA_W/8), no 4 KB crossing
//   window       every burst inside [win_lo, win_hi] (low 32 bits), upper address bits = MEM_BASE_HI
//   soft reset   no AR/AW is started while a soft reset is in progress (one already presented completes)
//   counts       reads outstanding <= RD_OUTSTANDING, writes <= WR_OUTSTANDING, no R/B without a request;
//                WLAST on exactly the beat AWLEN implies, also when W runs ahead of its AW

module g2d_axi_sva #(
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned ADDR_W          = 32,
  parameter int unsigned ID_W            = 4,
  parameter int unsigned MAX_BURST_BYTES = 256,
  parameter int unsigned RD_OUTSTANDING  = 8,
  parameter int unsigned WR_OUTSTANDING  = 8,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic                clk_i,
  input  logic                rst_ni,
  input  logic                stop_i,        // soft reset in progress
  input  logic [31:0]         win_lo_i,
  input  logic [31:0]         win_hi_i,
  input  logic [31:0]         base_hi_i,
  input  logic [ID_W-1:0]     m_axi_awid_o,
  input  logic [ADDR_W-1:0]   m_axi_awaddr_o,
  input  logic [7:0]          m_axi_awlen_o,
  input  logic [2:0]          m_axi_awsize_o,
  input  logic [1:0]          m_axi_awburst_o,
  input  logic [3:0]          m_axi_awcache_o,
  input  logic [2:0]          m_axi_awprot_o,
  input  logic                m_axi_awvalid_o,
  input  logic                m_axi_awready_i,
  input  logic [DATA_W-1:0]   m_axi_wdata_o,
  input  logic [DB-1:0]       m_axi_wstrb_o,
  input  logic                m_axi_wlast_o,
  input  logic                m_axi_wvalid_o,
  input  logic                m_axi_wready_i,
  input  logic                m_axi_bvalid_i,
  input  logic                m_axi_bready_o,
  input  logic [ID_W-1:0]     m_axi_arid_o,
  input  logic [ADDR_W-1:0]   m_axi_araddr_o,
  input  logic [7:0]          m_axi_arlen_o,
  input  logic [2:0]          m_axi_arsize_o,
  input  logic [1:0]          m_axi_arburst_o,
  input  logic [3:0]          m_axi_arcache_o,
  input  logic [2:0]          m_axi_arprot_o,
  input  logic                m_axi_arvalid_o,
  input  logic                m_axi_arready_i,
  input  logic                m_axi_rlast_i,
  input  logic                m_axi_rvalid_i,
  input  logic                m_axi_rready_o
);

  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  localparam int unsigned SZ = $clog2(DB);
  localparam int unsigned MAXB = MAX_BURST_BYTES / DB;

  // ---------------------------------------------------------------- handshakes
  p_aw_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (m_axi_awvalid_o && !m_axi_awready_i) |=> (m_axi_awvalid_o &&
      $stable({m_axi_awid_o, m_axi_awaddr_o, m_axi_awlen_o, m_axi_awsize_o, m_axi_awburst_o, m_axi_awcache_o,
               m_axi_awprot_o})))
    else $error("p_aw_hold: AW changed or dropped while stalled");
  p_w_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (m_axi_wvalid_o && !m_axi_wready_i) |=> (m_axi_wvalid_o && $stable({m_axi_wdata_o, m_axi_wstrb_o, m_axi_wlast_o})))
    else $error("p_w_hold: W changed or dropped while stalled");
  p_ar_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (m_axi_arvalid_o && !m_axi_arready_i) |=> (m_axi_arvalid_o &&
      $stable({m_axi_arid_o, m_axi_araddr_o, m_axi_arlen_o, m_axi_arsize_o, m_axi_arburst_o, m_axi_arcache_o,
               m_axi_arprot_o})))
    else $error("p_ar_hold: AR changed or dropped while stalled");

  // ---------------------------------------------------------------- burst shape, window
  function automatic logic burst_ok(logic [ADDR_W-1:0] a, logic [7:0] len, logic [2:0] size, logic [1:0] burst);
    logic [12:0] end_off;
    end_off = 13'(a[11:0]) + 13'((int'(len) + 1) * int'(DB));
    return burst == 2'b01 && size == 3'(SZ) && a[SZ-1:0] == '0 && int'(len) + 1 <= int'(MAXB) && end_off <= 13'd4096;
  endfunction

  // low 32 bits inside the window (64-byte granular; bursts are beat-aligned, so whole beats must lie in it)
  function automatic logic window_ok(logic [31:0] a, logic [7:0] len);
    logic [32:0] last;
    last = 33'(a) + 33'((int'(len) + 1) * int'(DB)) - 33'd1;
    return !last[32] && a >= {win_lo_i[31:6], 6'b0} && last[31:0] <= {win_hi_i[31:6], 6'h3F};
  endfunction

  // upper address bits = MEM_BASE_HI
  logic aw_hi_ok, ar_hi_ok;
  if (ADDR_W > 32) begin : g_hi
    assign aw_hi_ok = (m_axi_awaddr_o[ADDR_W-1:32] == base_hi_i[ADDR_W-33:0]);
    assign ar_hi_ok = (m_axi_araddr_o[ADDR_W-1:32] == base_hi_i[ADDR_W-33:0]);
  end else begin : g_nohi
    assign aw_hi_ok = 1'b1;
    assign ar_hi_ok = 1'b1;
  end

  p_aw_burst: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      m_axi_awvalid_o |-> burst_ok(m_axi_awaddr_o, m_axi_awlen_o, m_axi_awsize_o, m_axi_awburst_o))
    else $error("p_aw_burst: illegal write burst addr 0x%0h len %0d", m_axi_awaddr_o, m_axi_awlen_o);
  p_ar_burst: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      m_axi_arvalid_o |-> burst_ok(m_axi_araddr_o, m_axi_arlen_o, m_axi_arsize_o, m_axi_arburst_o))
    else $error("p_ar_burst: illegal read burst addr 0x%0h len %0d", m_axi_araddr_o, m_axi_arlen_o);
  p_aw_window: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      m_axi_awvalid_o |-> (aw_hi_ok && window_ok(m_axi_awaddr_o[31:0], m_axi_awlen_o)))
    else $error("p_aw_window: write burst 0x%0h outside the address window", m_axi_awaddr_o);
  p_ar_window: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      m_axi_arvalid_o |-> (ar_hi_ok && window_ok(m_axi_araddr_o[31:0], m_axi_arlen_o)))
    else $error("p_ar_window: read burst 0x%0h outside the address window", m_axi_araddr_o);

  // ---------------------------------------------------------------- soft reset: no new AR / AW
  // a transaction starts when VALID is high and was not stalled in the previous cycle
  logic aw_stall_q, ar_stall_q, soft_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_stall_q <= 1'b0;
      ar_stall_q <= 1'b0;
      soft_q     <= 1'b0;
    end else if (armed) begin
      aw_stall_q <= m_axi_awvalid_o && !m_axi_awready_i;
      ar_stall_q <= m_axi_arvalid_o && !m_axi_arready_i;
      soft_q     <= stop_i;
    end
  end
  logic aw_start, ar_start;
  assign aw_start = m_axi_awvalid_o && !aw_stall_q;
  assign ar_start = m_axi_arvalid_o && !ar_stall_q;
  p_soft_aw: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (soft_q && stop_i) |-> !aw_start)
    else $error("p_soft_aw: AW started during a soft reset");
  p_soft_ar: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (soft_q && stop_i) |-> !ar_start)
    else $error("p_soft_ar: AR started during a soft reset");

  // ---------------------------------------------------------------- outstanding counts, WLAST
  int rd_out, wr_out, wcnt;
  int aw_q[$];     // beats of write bursts whose AW was accepted before their last W beat
  int w_q[$];      // beats of write bursts whose last W beat came before their AW
  logic ar_hs, r_last_hs, aw_hs, w_hs, b_hs;
  assign ar_hs     = m_axi_arvalid_o && m_axi_arready_i;
  assign r_last_hs = m_axi_rvalid_i && m_axi_rready_o && m_axi_rlast_i;
  assign aw_hs     = m_axi_awvalid_o && m_axi_awready_i;
  assign w_hs      = m_axi_wvalid_o && m_axi_wready_i;
  assign b_hs      = m_axi_bvalid_i && m_axi_bready_o;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rd_out <= 0;
      wr_out <= 0;
      wcnt   <= 0;
      aw_q.delete();
      w_q.delete();
    end else if (armed) begin
      rd_out <= rd_out + (ar_hs ? 1 : 0) - (r_last_hs ? 1 : 0);
      wr_out <= wr_out + (aw_hs ? 1 : 0) - (b_hs ? 1 : 0);
      if (r_last_hs && rd_out + (ar_hs ? 1 : 0) == 0) $error("p_r_count: R burst without an outstanding read");
      if (b_hs && wr_out + (aw_hs ? 1 : 0) == 0) $error("p_b_count: B without an outstanding write");
      if (rd_out > int'(RD_OUTSTANDING)) $error("p_rd_outstanding: %0d reads outstanding", rd_out);
      if (wr_out > int'(WR_OUTSTANDING)) $error("p_wr_outstanding: %0d writes outstanding", wr_out);
      // WLAST: a W burst ends with WLAST exactly on its AWLEN + 1-th beat
      if (aw_hs) begin
        if (w_q.size() != 0) begin
          if (w_q[0] != int'(m_axi_awlen_o) + 1)
            $error("p_wlast: W burst of %0d beats for AWLEN %0d", w_q[0], m_axi_awlen_o);
          void'(w_q.pop_front());
        end else begin
          aw_q.push_back(int'(m_axi_awlen_o) + 1);
        end
      end
      if (w_hs) begin
        if (m_axi_wlast_o) begin
          wcnt <= 0;
          if (aw_q.size() != 0 && !aw_hs) begin
            if (aw_q[0] != wcnt + 1) $error("p_wlast: WLAST on beat %0d of a %0d-beat burst", wcnt + 1, aw_q[0]);
            void'(aw_q.pop_front());
          end else if (aw_q.size() != 0 && aw_hs) begin
            // the AW of an earlier burst was queued: this W burst belongs to aw_q[0]
            if (aw_q[0] != wcnt + 1) $error("p_wlast: WLAST on beat %0d of a %0d-beat burst", wcnt + 1, aw_q[0]);
            void'(aw_q.pop_front());
          end else begin
            w_q.push_back(wcnt + 1);
          end
        end else begin
          wcnt <= wcnt + 1;
          if (aw_q.size() != 0 && wcnt + 1 >= aw_q[0]) $error("p_wlast: missing WLAST after %0d beats", wcnt + 1);
          if (wcnt + 1 >= int'(MAXB)) $error("p_wlast: W burst longer than %0d beats", MAXB);
        end
      end
    end
  end

endmodule
