// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// OBI (Open Bus Interface 1.x, OpenHW Group) subordinate to AXI4-Lite master (32-bit), configuration-side adapter
// (v1.9, F-6). Address phase: req & gnt (gnt = no access in flight, so one access at a time); response phase:
// rvalid for one cycle with rdata and err (= SLVERR or DECERR). The optional rready of OBI 1.2+ is not used:
// the manager must accept rvalid in any cycle (OBI 1.0 behaviour; docs/OPEN_QUESTIONS.md Q-INT-1).

module g2d_obi2axil (
  input  logic         clk_i,
  input  logic         rst_ni,
  // OBI subordinate
  input  logic         req_i,
  output logic         gnt_o,
  input  logic [31:0]  addr_i,
  input  logic         we_i,
  input  logic [3:0]   be_i,
  input  logic [31:0]  wdata_i,
  output logic         rvalid_o,
  output logic [31:0]  rdata_o,
  output logic         err_o,
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

  assign req_valid = req_i;
  assign gnt_o     = req_ready;
  assign req_write = we_i;
  assign req_addr  = addr_i;
  assign req_wdata = wdata_i;
  assign req_strb  = we_i ? be_i : 4'h0;
  assign rvalid_o  = rsp_valid;
  assign rdata_o   = rsp_rdata;
  assign err_o     = rsp_err;

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
