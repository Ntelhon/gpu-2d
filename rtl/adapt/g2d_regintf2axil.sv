// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// PULP register interface (reg_intf: valid / write / addr / wdata / wstrb, response ready / rdata / error in the
// cycle that completes the access; the requester holds valid until ready) to AXI4-Lite master (32-bit),
// configuration-side adapter (v1.9, F-6). Used by Cheshire's register bus. One access at a time.

module g2d_regintf2axil (
  input  logic         clk_i,
  input  logic         rst_ni,
  // reg_intf device
  input  logic         reg_valid_i,
  input  logic         reg_write_i,
  input  logic [31:0]  reg_addr_i,
  input  logic [31:0]  reg_wdata_i,
  input  logic [3:0]   reg_wstrb_i,
  output logic         reg_ready_o,
  output logic [31:0]  reg_rdata_o,
  output logic         reg_error_o,
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
  logic        taken_q, done_q;   // access taken / its response has arrived

  assign req_valid   = reg_valid_i & ~taken_q;
  assign req_write   = reg_write_i;
  assign req_addr    = reg_addr_i;
  assign req_wdata   = reg_wdata_i;
  assign req_strb    = reg_wstrb_i;
  assign reg_ready_o = done_q;
  assign reg_rdata_o = rsp_rdata;
  assign reg_error_o = done_q & rsp_err;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      taken_q <= 1'b0;
      done_q  <= 1'b0;
    end else if (reg_valid_i && done_q) begin
      taken_q <= 1'b0;
      done_q  <= 1'b0;
    end else begin
      if (req_valid && req_ready) taken_q <= 1'b1;
      if (rsp_valid)              done_q  <= 1'b1;
    end
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
