// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// APB3 / APB4 slave to AXI4-Lite master (32-bit), configuration-side adapter (v1.9, F-6, docs/INTEGRATION.md).
// One transfer at a time (g2d_axil_single). The AXI4-Lite access starts in the APB setup phase (PSEL without
// PENABLE: address, direction and write data are valid there); PREADY stays low in the access phase until the
// B / R response has arrived, then is high for one cycle with PRDATA and PSLVERR (= SLVERR or DECERR).
// APB3 requesters tie PSTRB to 4'hF. PPROT is ignored. PADDR goes to the AXI4-Lite address unchanged.

module g2d_apb2axil (
  input  logic         clk_i,
  input  logic         rst_ni,
  // APB slave
  input  logic         psel_i,
  input  logic         penable_i,
  input  logic         pwrite_i,
  input  logic [31:0]  paddr_i,
  input  logic [31:0]  pwdata_i,
  input  logic [3:0]   pstrb_i,
  input  logic [2:0]   pprot_i,
  output logic         pready_o,
  output logic [31:0]  prdata_o,
  output logic         pslverr_o,
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

  logic taken_q, done_q;     // transfer taken from APB / its response has arrived
  logic req_valid, req_ready, rsp_valid, rsp_err;

  assign req_valid = psel_i & ~taken_q;
  assign pready_o  = done_q;
  assign pslverr_o = done_q & rsp_err;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      taken_q <= 1'b0;
      done_q  <= 1'b0;
    end else if (psel_i && penable_i && done_q) begin
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
    .req_write_i (pwrite_i),
    .req_addr_i  (paddr_i),
    .req_wdata_i (pwdata_i),
    .req_strb_i  (pstrb_i),
    .rsp_valid_o (rsp_valid),
    .rsp_rdata_o (prdata_o),
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

  logic unused;
  assign unused = ^pprot_i;

endmodule
