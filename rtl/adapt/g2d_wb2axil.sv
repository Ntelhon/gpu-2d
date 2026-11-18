// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Wishbone B4 pipelined slave (32-bit, byte select) to AXI4-Lite master, configuration-side adapter (v1.9, F-6).
// A request is taken when cyc & stb & ~stall; stall is high while an access is in flight (one at a time). The
// response is ack (OKAY) or err (SLVERR / DECERR) for one cycle. If the master drops cyc before the response
// (abort), the AXI4-Lite access still completes and its response is not shown.

module g2d_wb2axil (
  input  logic         clk_i,
  input  logic         rst_ni,
  // Wishbone slave
  input  logic         wb_cyc_i,
  input  logic         wb_stb_i,
  input  logic         wb_we_i,
  input  logic [31:0]  wb_adr_i,
  input  logic [31:0]  wb_dat_w_i,
  input  logic [3:0]   wb_sel_i,
  output logic         wb_stall_o,
  output logic         wb_ack_o,
  output logic         wb_err_o,
  output logic [31:0]  wb_dat_r_o,
  // AXI4-Lite master
  output logic [31:0]  m_axil_awaddr_o,
  output logic [2:0]   m_axil_awprot_o,
  output logic         m_axil_awvalid_o,
  input  logic         m_axil_awready_i,
  output logic [31:0]  m_axil_wdata_o,
  output logic [3:0]   m_axil_wstrb_o,
  output logic         m_axil_wvalid_o,
  input  logic         m_axil_wready_i,
  input  logic [1:0]   m_axil_bresp_i,
  input  logic         m_axil_bvalid_i,
  output logic         m_axil_bready_o,
  output logic [31:0]  m_axil_araddr_o,
  output logic [2:0]   m_axil_arprot_o,
  output logic         m_axil_arvalid_o,
  input  logic         m_axil_arready_i,
  input  logic [31:0]  m_axil_rdata_i,
  input  logic [1:0]   m_axil_rresp_i,
  input  logic         m_axil_rvalid_i,
  output logic         m_axil_rready_o
);

  logic        req_valid, req_ready, req_write, rsp_valid, rsp_err;
  logic [31:0] req_addr, req_wdata, rsp_rdata;
  logic [3:0]  req_strb;
  logic        live_q;   // the access in flight belongs to the current cycle (cyc stayed high)

  assign req_valid  = wb_cyc_i & wb_stb_i;
  assign wb_stall_o = ~req_ready;
  assign req_write  = wb_we_i;
  assign req_addr   = wb_adr_i;
  assign req_wdata  = wb_dat_w_i;
  assign req_strb   = wb_we_i ? wb_sel_i : 4'h0;
  assign wb_ack_o   = rsp_valid & live_q & wb_cyc_i & ~rsp_err;
  assign wb_err_o   = rsp_valid & live_q & wb_cyc_i & rsp_err;
  assign wb_dat_r_o   = rsp_rdata;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                      live_q <= 1'b0;
    else if (req_valid && req_ready)  live_q <= 1'b1;
    else if (!wb_cyc_i || rsp_valid)  live_q <= 1'b0;
  end

  g2d_axil_single u_axil (
    .clk_i,
    .rst_ni,
    .req_valid_i (req_valid),
    .req_ready_o (req_ready),
    .req_write_i (req_write),
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

endmodule
