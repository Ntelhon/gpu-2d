// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4-Lite clock-domain crossing (arch.md §2.1, CFG_ASYNC = 1): one g2d_cdc_fifo per channel. The slave side
// (s_axil_*, the integrator's config bus) runs on the cfg clock, the master side (m_axil_*, towards the core's
// g2d_axil2reg) on the core clock. Protocol is passed through unchanged (prot is carried).

module g2d_cdc_axil #(
  parameter int unsigned LOG_DEPTH   = 1,
  parameter int unsigned SYNC_STAGES = 2
) (
  input  logic        s_clk_i,
  input  logic        s_rst_ni,
  input  logic        m_clk_i,
  input  logic        m_rst_ni,
  // slave side (s_clk)
  input  logic [31:0] s_axil_awaddr_i,
  input  logic [2:0]  s_axil_awprot_i,
  input  logic        s_axil_awvalid_i,
  output logic        s_axil_awready_o,
  input  logic [31:0] s_axil_wdata_i,
  input  logic [3:0]  s_axil_wstrb_i,
  input  logic        s_axil_wvalid_i,
  output logic        s_axil_wready_o,
  output logic [1:0]  s_axil_bresp_o,
  output logic        s_axil_bvalid_o,
  input  logic        s_axil_bready_i,
  input  logic [31:0] s_axil_araddr_i,
  input  logic [2:0]  s_axil_arprot_i,
  input  logic        s_axil_arvalid_i,
  output logic        s_axil_arready_o,
  output logic [31:0] s_axil_rdata_o,
  output logic [1:0]  s_axil_rresp_o,
  output logic        s_axil_rvalid_o,
  input  logic        s_axil_rready_i,
  // master side (m_clk)
  output logic [31:0] m_axil_awaddr_o,
  output logic [2:0]  m_axil_awprot_o,
  output logic        m_axil_awvalid_o,
  input  logic        m_axil_awready_i,
  output logic [31:0] m_axil_wdata_o,
  output logic [3:0]  m_axil_wstrb_o,
  output logic        m_axil_wvalid_o,
  input  logic        m_axil_wready_i,
  input  logic [1:0]  m_axil_bresp_i,
  input  logic        m_axil_bvalid_i,
  output logic        m_axil_bready_o,
  output logic [31:0] m_axil_araddr_o,
  output logic [2:0]  m_axil_arprot_o,
  output logic        m_axil_arvalid_o,
  input  logic        m_axil_arready_i,
  input  logic [31:0] m_axil_rdata_i,
  input  logic [1:0]  m_axil_rresp_i,
  input  logic        m_axil_rvalid_i,
  output logic        m_axil_rready_o
);

  g2d_cdc_fifo #(.WIDTH(35), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_aw (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axil_awvalid_i), .src_ready_o (s_axil_awready_o),
    .src_data_i ({s_axil_awaddr_i, s_axil_awprot_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axil_awvalid_o), .dst_ready_i (m_axil_awready_i),
    .dst_data_o ({m_axil_awaddr_o, m_axil_awprot_o})
  );

  g2d_cdc_fifo #(.WIDTH(36), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_w (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axil_wvalid_i), .src_ready_o (s_axil_wready_o),
    .src_data_i ({s_axil_wdata_i, s_axil_wstrb_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axil_wvalid_o), .dst_ready_i (m_axil_wready_i),
    .dst_data_o ({m_axil_wdata_o, m_axil_wstrb_o})
  );

  g2d_cdc_fifo #(.WIDTH(2), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_b (
    .src_clk_i (m_clk_i), .src_rst_ni (m_rst_ni),
    .src_valid_i (m_axil_bvalid_i), .src_ready_o (m_axil_bready_o),
    .src_data_i (m_axil_bresp_i),
    .dst_clk_i (s_clk_i), .dst_rst_ni (s_rst_ni),
    .dst_valid_o (s_axil_bvalid_o), .dst_ready_i (s_axil_bready_i),
    .dst_data_o (s_axil_bresp_o)
  );

  g2d_cdc_fifo #(.WIDTH(35), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_ar (
    .src_clk_i (s_clk_i), .src_rst_ni (s_rst_ni),
    .src_valid_i (s_axil_arvalid_i), .src_ready_o (s_axil_arready_o),
    .src_data_i ({s_axil_araddr_i, s_axil_arprot_i}),
    .dst_clk_i (m_clk_i), .dst_rst_ni (m_rst_ni),
    .dst_valid_o (m_axil_arvalid_o), .dst_ready_i (m_axil_arready_i),
    .dst_data_o ({m_axil_araddr_o, m_axil_arprot_o})
  );

  g2d_cdc_fifo #(.WIDTH(34), .LOG_DEPTH(LOG_DEPTH), .SYNC_STAGES(SYNC_STAGES)) u_r (
    .src_clk_i (m_clk_i), .src_rst_ni (m_rst_ni),
    .src_valid_i (m_axil_rvalid_i), .src_ready_o (m_axil_rready_o),
    .src_data_i ({m_axil_rdata_i, m_axil_rresp_i}),
    .dst_clk_i (s_clk_i), .dst_rst_ni (s_rst_ni),
    .dst_valid_o (s_axil_rvalid_o), .dst_ready_i (s_axil_rready_i),
    .dst_data_o ({s_axil_rdata_o, s_axil_rresp_o})
  );

endmodule
