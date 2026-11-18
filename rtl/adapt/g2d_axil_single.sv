// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// One AXI4-Lite access at a time (v1.9, shared core of the configuration-side adapters rtl/adapt/g2d_*2axil).
// req_valid_i & req_ready_o starts an access (req_ready_o = no access in flight). AW and W are presented together
// for a write, AR for a read; each VALID is held until its READY. When the B / R response arrives, rsp_valid_o is
// high for one cycle; rsp_rdata_o / rsp_err_o (SLVERR or DECERR) hold their value until the next response.
// req_ready_o is high again in the response cycle, so the next access may start there.

module g2d_axil_single (
  input  logic         clk_i,
  input  logic         rst_ni,
  input  logic         req_valid_i,
  output logic         req_ready_o,
  input  logic         req_write_i,
  input  logic [31:0]  req_addr_i,
  input  logic [31:0]  req_wdata_i,
  input  logic [3:0]   req_strb_i,
  output logic         rsp_valid_o,
  output logic [31:0]  rsp_rdata_o,
  output logic         rsp_err_o,
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

  logic        busy_q, aw_q, w_q, ar_q, wr_q, rsp_q, err_q;
  logic [31:0] addr_q, wdata_q, rdata_q;
  logic [3:0]  strb_q;
  logic        done;

  assign req_ready_o = ~busy_q;
  // only the response of the access in flight is taken (B for a write, R for a read)
  assign done = busy_q & (wr_q ? (m_axil_bvalid_i & ~aw_q & ~w_q) : (m_axil_rvalid_i & ~ar_q));

  assign rsp_valid_o = rsp_q;
  assign rsp_rdata_o = rdata_q;
  assign rsp_err_o   = err_q;

  assign m_axil_awaddr_o  = addr_q;
  assign m_axil_awprot_o  = 3'b000;
  assign m_axil_awvalid_o = aw_q;
  assign m_axil_wdata_o   = wdata_q;
  assign m_axil_wstrb_o   = strb_q;
  assign m_axil_wvalid_o  = w_q;
  assign m_axil_bready_o  = busy_q & wr_q & ~aw_q & ~w_q;
  assign m_axil_araddr_o  = addr_q;
  assign m_axil_arprot_o  = 3'b000;
  assign m_axil_arvalid_o = ar_q;
  assign m_axil_rready_o  = busy_q & ~wr_q & ~ar_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q  <= 1'b0;
      aw_q    <= 1'b0;
      w_q     <= 1'b0;
      ar_q    <= 1'b0;
      wr_q    <= 1'b0;
      rsp_q   <= 1'b0;
      err_q   <= 1'b0;
      addr_q  <= '0;
      wdata_q <= '0;
      strb_q  <= '0;
      rdata_q <= '0;
    end else begin
      rsp_q <= done;
      if (req_valid_i && !busy_q) begin
        busy_q  <= 1'b1;
        wr_q    <= req_write_i;
        addr_q  <= req_addr_i;
        wdata_q <= req_wdata_i;
        strb_q  <= req_strb_i;
        aw_q    <= req_write_i;
        w_q     <= req_write_i;
        ar_q    <= ~req_write_i;
      end else begin
        if (m_axil_awready_i) aw_q <= 1'b0;
        if (m_axil_wready_i)  w_q  <= 1'b0;
        if (m_axil_arready_i) ar_q <= 1'b0;
      end
      if (done) begin
        busy_q <= 1'b0;
        err_q  <= wr_q ? m_axil_bresp_i[1] : m_axil_rresp_i[1];
        if (!wr_q) rdata_q <= m_axil_rdata_i;
      end
    end
  end

  logic unused;
  assign unused = ^{m_axil_bresp_i[0], m_axil_rresp_i[0]};   // SLVERR and DECERR both report an error

endmodule
