// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AXI4-Lite slave (32-bit) to the bus-agnostic register interface (arch.md §4.1).
// AW, W and AR are captured in holding registers (ready = holding register empty, no combinational path
// from the bus to its own ready). A write is issued when both AW and W are held and no B is pending; a
// read when AR is held and no R is pending. If both are possible in the same cycle they alternate.
// The register interface answers in the same cycle (g2d_csr_regs), so B/R are valid one cycle later.
// rsp.error maps to SLVERR. AxPROT is ignored.

module g2d_axil2reg
  import g2d_csr_pkg::*;
(
  input  logic         clk_i,
  input  logic         rst_ni,
  // AXI4-Lite slave
  input  logic [31:0]  s_axil_awaddr_i,
  input  logic [2:0]   s_axil_awprot_i,
  input  logic         s_axil_awvalid_i,
  output logic         s_axil_awready_o,
  input  logic [31:0]  s_axil_wdata_i,
  input  logic [3:0]   s_axil_wstrb_i,
  input  logic         s_axil_wvalid_i,
  output logic         s_axil_wready_o,
  output logic [1:0]   s_axil_bresp_o,
  output logic         s_axil_bvalid_o,
  input  logic         s_axil_bready_i,
  input  logic [31:0]  s_axil_araddr_i,
  input  logic [2:0]   s_axil_arprot_i,
  input  logic         s_axil_arvalid_i,
  output logic         s_axil_arready_o,
  output logic [31:0]  s_axil_rdata_o,
  output logic [1:0]   s_axil_rresp_o,
  output logic         s_axil_rvalid_o,
  input  logic         s_axil_rready_i,
  // register interface
  output g2d_reg_req_t reg_req_o,
  input  g2d_reg_rsp_t reg_rsp_i
);

  logic        aw_full_q, w_full_q, ar_full_q;
  logic [11:2] aw_addr_q, ar_addr_q;
  logic [31:0] w_data_q;
  logic [3:0]  w_strb_q;
  logic        b_valid_q, r_valid_q;
  logic        b_err_q, r_err_q;
  logic [31:0] r_data_q;
  logic        last_was_write_q;

  logic wr_possible, rd_possible, do_wr, do_rd;
  assign wr_possible = aw_full_q & w_full_q & ~b_valid_q;
  assign rd_possible = ar_full_q & ~r_valid_q;
  assign do_wr = wr_possible & (~rd_possible | ~last_was_write_q);
  assign do_rd = rd_possible & ~do_wr;

  assign s_axil_awready_o = ~aw_full_q;
  assign s_axil_wready_o  = ~w_full_q;
  assign s_axil_arready_o = ~ar_full_q;
  assign s_axil_bvalid_o  = b_valid_q;
  assign s_axil_bresp_o   = b_err_q ? 2'b10 : 2'b00;
  assign s_axil_rvalid_o  = r_valid_q;
  assign s_axil_rresp_o   = r_err_q ? 2'b10 : 2'b00;
  assign s_axil_rdata_o   = r_data_q;

  always_comb begin
    reg_req_o       = '0;
    reg_req_o.valid = do_wr | do_rd;
    reg_req_o.write = do_wr;
    reg_req_o.addr  = do_wr ? aw_addr_q : ar_addr_q;
    reg_req_o.wdata = do_wr ? w_data_q : '0;
    reg_req_o.wstrb = do_wr ? w_strb_q : '0;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_full_q        <= 1'b0;
      w_full_q         <= 1'b0;
      ar_full_q        <= 1'b0;
      b_valid_q        <= 1'b0;
      r_valid_q        <= 1'b0;
      b_err_q          <= 1'b0;
      r_err_q          <= 1'b0;
      r_data_q         <= '0;
      last_was_write_q <= 1'b0;
    end else begin
      if (s_axil_awvalid_i & s_axil_awready_o) aw_full_q <= 1'b1;
      else if (do_wr)                           aw_full_q <= 1'b0;
      if (s_axil_wvalid_i & s_axil_wready_o)   w_full_q <= 1'b1;
      else if (do_wr)                           w_full_q <= 1'b0;
      if (s_axil_arvalid_i & s_axil_arready_o) ar_full_q <= 1'b1;
      else if (do_rd)                           ar_full_q <= 1'b0;

      if (do_wr) begin
        b_valid_q <= 1'b1;
        b_err_q   <= reg_rsp_i.error;
      end else if (s_axil_bready_i) begin
        b_valid_q <= 1'b0;
      end
      if (do_rd) begin
        r_valid_q <= 1'b1;
        r_err_q   <= reg_rsp_i.error;
        r_data_q  <= reg_rsp_i.rdata;
      end else if (s_axil_rready_i) begin
        r_valid_q <= 1'b0;
      end
      if (do_wr | do_rd) last_was_write_q <= do_wr;
    end
  end

  // Captured address/data need no reset: qualified by the *_full flags.
  always_ff @(posedge clk_i) begin
    if (s_axil_awvalid_i & s_axil_awready_o) aw_addr_q <= s_axil_awaddr_i[11:2];
    if (s_axil_arvalid_i & s_axil_arready_o) ar_addr_q <= s_axil_araddr_i[11:2];
    if (s_axil_wvalid_i & s_axil_wready_o) begin
      w_data_q <= s_axil_wdata_i;
      w_strb_q <= s_axil_wstrb_i;
    end
  end

  // Only the 4 KB CSR window is decoded; upper address bits, the byte offset and AxPROT are ignored.
  logic unused;
  assign unused = ^{s_axil_awaddr_i[31:12], s_axil_awaddr_i[1:0], s_axil_araddr_i[31:12],
                    s_axil_araddr_i[1:0], s_axil_awprot_i, s_axil_arprot_i, reg_rsp_i.ready};

endmodule
