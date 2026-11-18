// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D AXI4 master (arch.md §4.2): read engine (N_RD clients) + write engine, AXI attributes, request
// throttle, error reporting and performance events.
//   AxSIZE = log2(DATA_W/8), AxBURST = INCR, AxLOCK = 0, AxREGION = 0. Read IDs = client index, write ID = 0.
//   AxCACHE / AxPROT / AxQOS from CSR MEM_ATTR (v1.9; reset = the former constants 4'b0011 / 0 / 0), sampled
//   while no request waits on that channel, so a waiting request keeps its payload.
//   Throttle (v1.9, CSR MEM_QOS, g2d_axi_qos per address channel); off = cycle-identical to v1.8.
// Errors in the same cycle: window violation first, then read, then write (one address to g2d_ctrl;
// every error bit is still reported).

module g2d_axi_mst #(
  parameter int unsigned N_RD            = 1,
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned ADDR_W          = 32,
  parameter int unsigned ID_W            = 4,
  parameter int unsigned MAX_BURST_BYTES = 256,
  parameter int unsigned RD_OUTSTANDING  = 16,  // v1.7 (Q-PERF-2): was 8
  parameter int unsigned WR_OUTSTANDING  = 8,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,
  input  logic                    clr_i,
  input  logic                    stop_i,
  input  logic [31:0]             win_lo_i,
  input  logic [31:0]             win_hi_i,
  input  logic [31:0]             base_hi_i,
  // memory attributes and throttle (v1.9, CSR MEM_ATTR / MEM_QOS)
  input  logic [3:0]              ar_cache_i,
  input  logic [2:0]              ar_prot_i,
  input  logic [3:0]              ar_qos_i,
  input  logic [3:0]              aw_cache_i,
  input  logic [2:0]              aw_prot_i,
  input  logic [3:0]              aw_qos_i,
  input  logic [4:0]              rd_max_out_i,
  input  logic [4:0]              wr_max_out_i,
  input  logic [7:0]              rd_gap_i,
  input  logic [7:0]              wr_gap_i,
  // read clients
  input  logic [N_RD-1:0]         rd_req_valid_i,
  output logic [N_RD-1:0]         rd_req_ready_o,
  input  logic [N_RD-1:0][31:0]   rd_req_addr_i,
  input  logic [N_RD-1:0][15:0]   rd_req_len_i,
  output logic [N_RD-1:0]         rd_rsp_valid_o,
  input  logic [N_RD-1:0]         rd_rsp_ready_i,
  output logic [DATA_W-1:0]       rd_rsp_data_o,
  output logic [DB-1:0]           rd_rsp_strb_o,
  output logic                    rd_rsp_last_o,
  output logic                    rd_rsp_err_o,
  // write producer
  input  logic                    wr_req_valid_i,
  output logic                    wr_req_ready_o,
  input  logic [31:0]             wr_req_addr_i,
  input  logic [15:0]             wr_req_len_i,
  input  logic                    wr_dat_valid_i,
  output logic                    wr_dat_ready_o,
  input  logic [DATA_W-1:0]       wr_dat_data_i,
  input  logic [DB-1:0]           wr_dat_strb_i,
  // AXI4 master
  output logic [ID_W-1:0]         m_axi_awid_o,
  output logic [ADDR_W-1:0]       m_axi_awaddr_o,
  output logic [7:0]              m_axi_awlen_o,
  output logic [2:0]              m_axi_awsize_o,
  output logic [1:0]              m_axi_awburst_o,
  output logic                    m_axi_awlock_o,
  output logic [3:0]              m_axi_awcache_o,
  output logic [2:0]              m_axi_awprot_o,
  output logic [3:0]              m_axi_awqos_o,
  output logic [3:0]              m_axi_awregion_o,
  output logic                    m_axi_awvalid_o,
  input  logic                    m_axi_awready_i,
  output logic [DATA_W-1:0]       m_axi_wdata_o,
  output logic [DB-1:0]           m_axi_wstrb_o,
  output logic                    m_axi_wlast_o,
  output logic                    m_axi_wvalid_o,
  input  logic                    m_axi_wready_i,
  input  logic [ID_W-1:0]         m_axi_bid_i,
  input  logic [1:0]              m_axi_bresp_i,
  input  logic                    m_axi_bvalid_i,
  output logic                    m_axi_bready_o,
  output logic [ID_W-1:0]         m_axi_arid_o,
  output logic [ADDR_W-1:0]       m_axi_araddr_o,
  output logic [7:0]              m_axi_arlen_o,
  output logic [2:0]              m_axi_arsize_o,
  output logic [1:0]              m_axi_arburst_o,
  output logic                    m_axi_arlock_o,
  output logic [3:0]              m_axi_arcache_o,
  output logic [2:0]              m_axi_arprot_o,
  output logic [3:0]              m_axi_arqos_o,
  output logic [3:0]              m_axi_arregion_o,
  output logic                    m_axi_arvalid_o,
  input  logic                    m_axi_arready_i,
  input  logic [ID_W-1:0]         m_axi_rid_i,
  input  logic [DATA_W-1:0]       m_axi_rdata_i,
  input  logic [1:0]              m_axi_rresp_i,
  input  logic                    m_axi_rlast_i,
  input  logic                    m_axi_rvalid_i,
  output logic                    m_axi_rready_o,
  // status
  output logic                    rd_idle_o,
  output logic                    wr_idle_o,
  output logic                    err_rd_o,
  output logic                    err_wr_o,
  output logic                    err_win_o,
  output logic [31:0]             err_addr_o,
  // performance events
  output logic                    perf_rd_beat_o,
  output logic                    perf_wr_beat_o,
  output logic                    perf_rd_stall_o,
  output logic                    perf_wr_stall_o
);

  // Elaboration checks (arch.md §3 parameter ranges).
  if (DATA_W != 64 && DATA_W != 128) begin : g_chk_dw
    $error("DATA_W must be 64 or 128");
  end
  if (ADDR_W < 32 || ADDR_W > 64) begin : g_chk_aw
    $error("ADDR_W must be 32..64");
  end
  if ((MAX_BURST_BYTES & (MAX_BURST_BYTES - 1)) != 0 || MAX_BURST_BYTES < 64 || MAX_BURST_BYTES > 4096 ||
      MAX_BURST_BYTES / DB > 256) begin : g_chk_mb
    $error("MAX_BURST_BYTES must be a power of 2 in 64..4096 and at most 256 beats");
  end
  if (N_RD > (1 << ID_W)) begin : g_chk_id
    $error("ID_W too small for N_RD read clients");
  end

  localparam logic [2:0] SIZE = 3'($clog2(DB));

  logic rd_err_win, wr_err_win, rd_err, wr_err;
  logic eng_arvalid, eng_arready, eng_awvalid, eng_awready, ar_allow, aw_allow;
  logic [31:0] rd_err_addr, wr_err_addr;

  g2d_axi_rd #(
    .N_CLIENTS       (N_RD),
    .DATA_W          (DATA_W),
    .ADDR_W          (ADDR_W),
    .ID_W            (ID_W),
    .MAX_BURST_BYTES (MAX_BURST_BYTES),
    .RD_OUTSTANDING  (RD_OUTSTANDING)
  ) u_rd (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .win_lo_i,
    .win_hi_i,
    .base_hi_i,
    .req_valid_i     (rd_req_valid_i),
    .req_ready_o     (rd_req_ready_o),
    .req_addr_i      (rd_req_addr_i),
    .req_len_i       (rd_req_len_i),
    .rsp_valid_o     (rd_rsp_valid_o),
    .rsp_ready_i     (rd_rsp_ready_i),
    .rsp_data_o      (rd_rsp_data_o),
    .rsp_strb_o      (rd_rsp_strb_o),
    .rsp_last_o      (rd_rsp_last_o),
    .rsp_err_o       (rd_rsp_err_o),
    .m_axi_arid_o,
    .m_axi_araddr_o,
    .m_axi_arlen_o,
    .m_axi_arvalid_o (eng_arvalid),
    .m_axi_arready_i (eng_arready),
    .m_axi_rid_i,
    .m_axi_rdata_i,
    .m_axi_rresp_i,
    .m_axi_rlast_i,
    .m_axi_rvalid_i,
    .m_axi_rready_o,
    .idle_o          (rd_idle_o),
    .err_rd_o        (rd_err),
    .err_win_o       (rd_err_win),
    .err_addr_o      (rd_err_addr)
  );

  g2d_axi_wr #(
    .DATA_W          (DATA_W),
    .ADDR_W          (ADDR_W),
    .ID_W            (ID_W),
    .MAX_BURST_BYTES (MAX_BURST_BYTES),
    .WR_OUTSTANDING  (WR_OUTSTANDING)
  ) u_wr (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .win_lo_i,
    .win_hi_i,
    .base_hi_i,
    .req_valid_i     (wr_req_valid_i),
    .req_ready_o     (wr_req_ready_o),
    .req_addr_i      (wr_req_addr_i),
    .req_len_i       (wr_req_len_i),
    .dat_valid_i     (wr_dat_valid_i),
    .dat_ready_o     (wr_dat_ready_o),
    .dat_data_i      (wr_dat_data_i),
    .dat_strb_i      (wr_dat_strb_i),
    .m_axi_awid_o,
    .m_axi_awaddr_o,
    .m_axi_awlen_o,
    .m_axi_awvalid_o (eng_awvalid),
    .m_axi_awready_i (eng_awready),
    .m_axi_wdata_o,
    .m_axi_wstrb_o,
    .m_axi_wlast_o,
    .m_axi_wvalid_o,
    .m_axi_wready_i,
    .m_axi_bid_i,
    .m_axi_bresp_i,
    .m_axi_bvalid_i,
    .m_axi_bready_o,
    .idle_o          (wr_idle_o),
    .err_wr_o        (wr_err),
    .err_win_o       (wr_err_win),
    .err_addr_o      (wr_err_addr)
  );

  // Throttle (v1.9): one gate per address channel. Completion = last R beat / B response.
  localparam int unsigned QCNT_W = $clog2((RD_OUTSTANDING > WR_OUTSTANDING ? RD_OUTSTANDING : WR_OUTSTANDING) + 1)
                                   + 1;

  g2d_axi_qos #(.CNT_W(QCNT_W)) u_ar_qos (
    .clk_i,
    .rst_ni,
    .clr_i,
    .max_out_i (rd_max_out_i),
    .gap_i     (rd_gap_i),
    .valid_i   (eng_arvalid),
    .ready_i   (m_axi_arready_i),
    .done_i    (m_axi_rvalid_i & m_axi_rready_o & m_axi_rlast_i),
    .allow_o   (ar_allow)
  );

  g2d_axi_qos #(.CNT_W(QCNT_W)) u_aw_qos (
    .clk_i,
    .rst_ni,
    .clr_i,
    .max_out_i (wr_max_out_i),
    .gap_i     (wr_gap_i),
    .valid_i   (eng_awvalid),
    .ready_i   (m_axi_awready_i),
    .done_i    (m_axi_bvalid_i & m_axi_bready_o),
    .allow_o   (aw_allow)
  );

  assign m_axi_arvalid_o = eng_arvalid & ar_allow;
  assign eng_arready     = m_axi_arready_i & ar_allow;
  assign m_axi_awvalid_o = eng_awvalid & aw_allow;
  assign eng_awready     = m_axi_awready_i & aw_allow;

  // Attributes (v1.9): sampled while no request waits on the channel (AXI payload stable while valid).
  logic [10:0] ar_attr_q, aw_attr_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ar_attr_q <= {4'b0000, 3'b000, 4'b0011};
      aw_attr_q <= {4'b0000, 3'b000, 4'b0011};
    end else begin
      if (!(m_axi_arvalid_o && !m_axi_arready_i)) ar_attr_q <= {ar_qos_i, ar_prot_i, ar_cache_i};
      if (!(m_axi_awvalid_o && !m_axi_awready_i)) aw_attr_q <= {aw_qos_i, aw_prot_i, aw_cache_i};
    end
  end

  assign m_axi_awsize_o   = SIZE;
  assign m_axi_awburst_o  = 2'b01;
  assign m_axi_awlock_o   = 1'b0;
  assign m_axi_awcache_o  = aw_attr_q[3:0];
  assign m_axi_awprot_o   = aw_attr_q[6:4];
  assign m_axi_awqos_o    = aw_attr_q[10:7];
  assign m_axi_awregion_o = 4'b0000;
  assign m_axi_arsize_o   = SIZE;
  assign m_axi_arburst_o  = 2'b01;
  assign m_axi_arlock_o   = 1'b0;
  assign m_axi_arcache_o  = ar_attr_q[3:0];
  assign m_axi_arprot_o   = ar_attr_q[6:4];
  assign m_axi_arqos_o    = ar_attr_q[10:7];
  assign m_axi_arregion_o = 4'b0000;

  assign err_win_o  = rd_err_win | wr_err_win;
  assign err_rd_o   = rd_err;
  assign err_wr_o   = wr_err;
  assign err_addr_o = rd_err_win ? rd_err_addr :
                      wr_err_win ? wr_err_addr :
                      rd_err     ? rd_err_addr : wr_err_addr;

  assign perf_rd_beat_o  = m_axi_rvalid_i & m_axi_rready_o;
  assign perf_wr_beat_o  = m_axi_wvalid_o & m_axi_wready_i;
  assign perf_rd_stall_o = (m_axi_arvalid_o & ~m_axi_arready_i) | (m_axi_rvalid_i & ~m_axi_rready_o);
  assign perf_wr_stall_o = (m_axi_awvalid_o & ~m_axi_awready_i) | (m_axi_wvalid_o & ~m_axi_wready_i) |
                           (m_axi_bvalid_i & ~m_axi_bready_o);

endmodule
