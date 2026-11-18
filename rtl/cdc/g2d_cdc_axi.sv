// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4 clock-domain crossing (arch.md §2.1, MEM_ASYNC = 1): one g2d_cdc_fifo per channel. The slave side
// (s_axi_*, driven by the core's g2d_axi_mst) runs on the core clock, the master side (m_axi_*, the memory port)
// on the mem clock. Every field of the port list (arch.md §4.1) is carried unchanged; AXI handshakes bound the
// transactions in flight, so the depth only sets the streaming rate across the crossing.

module g2d_cdc_axi #(
  parameter int unsigned DATA_W      = 64,
  parameter int unsigned ADDR_W      = 32,
  parameter int unsigned ID_W        = 4,
  parameter int unsigned LOG_DEPTH   = 3,
  parameter int unsigned SYNC_STAGES = 2,
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned AW = ID_W + ADDR_W + 8 + 3 + 2 + 1 + 4 + 3 + 4 + 4
) (
  input  logic              s_clk_i,
  input  logic              s_rst_ni,
  input  logic              m_clk_i,
  input  logic              m_rst_ni,
  // slave side (s_clk)
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
  // master side (m_clk)
  output logic [ID_W-1:0]   m_axi_awid_o,
  output logic [ADDR_W-1:0] m_axi_awaddr_o,
  output logic [7:0]        m_axi_awlen_o,
  output logic [2:0]        m_axi_awsize_o,
  output logic [1:0]        m_axi_awburst_o,
  output logic              m_axi_awlock_o,
  output logic [3:0]        m_axi_awcache_o,
  output logic [2:0]        m_axi_awprot_o,
  output logic [3:0]        m_axi_awqos_o,
  output logic [3:0]        m_axi_awregion_o,
  output logic              m_axi_awvalid_o,
  input  logic              m_axi_awready_i,
  output logic [DATA_W-1:0] m_axi_wdata_o,
  output logic [DB-1:0]     m_axi_wstrb_o,
  output logic              m_axi_wlast_o,
  output logic              m_axi_wvalid_o,
  input  logic              m_axi_wready_i,
  input  logic [ID_W-1:0]   m_axi_bid_i,
  input  logic [1:0]        m_axi_bresp_i,
  input  logic              m_axi_bvalid_i,
  output logic              m_axi_bready_o,
  output logic [ID_W-1:0]   m_axi_arid_o,
  output logic [ADDR_W-1:0] m_axi_araddr_o,
  output logic [7:0]        m_axi_arlen_o,
  output logic [2:0]        m_axi_arsize_o,
  output logic [1:0]        m_axi_arburst_o,
  output logic              m_axi_arlock_o,
  output logic [3:0]        m_axi_arcache_o,
  output logic [2:0]        m_axi_arprot_o,
  output logic [3:0]        m_axi_arqos_o,
  output logic [3:0]        m_axi_arregion_o,
  output logic              m_axi_arvalid_o,
  input  logic              m_axi_arready_i,
  input  logic [ID_W-1:0]   m_axi_rid_i,
  input  logic [DATA_W-1:0] m_axi_rdata_i,
  input  logic [1:0]        m_axi_rresp_i,
  input  logic              m_axi_rlast_i,
  input  logic              m_axi_rvalid_i,
  output logic              m_axi_rready_o
);

  g2d_cdc_fifo #(.WIDTH(AW), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_aw (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axi_awvalid_i), .src_ready_o (s_axi_awready_o),
    .src_data_i ({s_axi_awid_i, s_axi_awaddr_i, s_axi_awlen_i, s_axi_awsize_i, s_axi_awburst_i, s_axi_awlock_i,
                  s_axi_awcache_i, s_axi_awprot_i, s_axi_awqos_i, s_axi_awregion_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axi_awvalid_o), .dst_ready_i (m_axi_awready_i),
    .dst_data_o ({m_axi_awid_o, m_axi_awaddr_o, m_axi_awlen_o, m_axi_awsize_o, m_axi_awburst_o, m_axi_awlock_o,
                  m_axi_awcache_o, m_axi_awprot_o, m_axi_awqos_o, m_axi_awregion_o})
  );

  g2d_cdc_fifo #(.WIDTH(DATA_W + DB + 1), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_w (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axi_wvalid_i), .src_ready_o (s_axi_wready_o),
    .src_data_i ({s_axi_wdata_i, s_axi_wstrb_i, s_axi_wlast_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axi_wvalid_o), .dst_ready_i (m_axi_wready_i),
    .dst_data_o ({m_axi_wdata_o, m_axi_wstrb_o, m_axi_wlast_o})
  );

  g2d_cdc_fifo #(.WIDTH(ID_W + 2), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_b (
    .src_clk_i (m_clk_i), .src_rst_ni (m_rst_ni),
    .src_valid_i (m_axi_bvalid_i), .src_ready_o (m_axi_bready_o),
    .src_data_i ({m_axi_bid_i, m_axi_bresp_i}),
    .dst_clk_i (s_clk_i), .dst_rst_ni (s_rst_ni),
    .dst_valid_o (s_axi_bvalid_o), .dst_ready_i (s_axi_bready_i),
    .dst_data_o ({s_axi_bid_o, s_axi_bresp_o})
  );

  g2d_cdc_fifo #(.WIDTH(AW), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_ar (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axi_arvalid_i), .src_ready_o (s_axi_arready_o),
    .src_data_i ({s_axi_arid_i, s_axi_araddr_i, s_axi_arlen_i, s_axi_arsize_i, s_axi_arburst_i, s_axi_arlock_i,
                  s_axi_arcache_i, s_axi_arprot_i, s_axi_arqos_i, s_axi_arregion_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axi_arvalid_o), .dst_ready_i (m_axi_arready_i),
    .dst_data_o ({m_axi_arid_o, m_axi_araddr_o, m_axi_arlen_o, m_axi_arsize_o, m_axi_arburst_o, m_axi_arlock_o,
                  m_axi_arcache_o, m_axi_arprot_o, m_axi_arqos_o, m_axi_arregion_o})
  );

  g2d_cdc_fifo #(.WIDTH(ID_W + DATA_W + 2 + 1), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_r (
    .src_clk_i (m_clk_i), .src_rst_ni (m_rst_ni),
    .src_valid_i (m_axi_rvalid_i), .src_ready_o (m_axi_rready_o),
    .src_data_i ({m_axi_rid_i, m_axi_rdata_i, m_axi_rresp_i, m_axi_rlast_i}),
    .dst_clk_i (s_clk_i), .dst_rst_ni (s_rst_ni),
    .dst_valid_o (s_axi_rvalid_o), .dst_ready_i (s_axi_rready_i),
    .dst_data_o ({s_axi_rid_o, s_axi_rdata_o, s_axi_rresp_o, s_axi_rlast_o})
  );

endmodule
