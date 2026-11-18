// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// One IHP SG13G2 two-port SRAM macro (v2.0 step C, rtl/ram/ihp/README.md) used as read port A / write port B.
// DEPTH x WIDTH must be one of the sizes in the generate case (the macros of the IHP open PDK, libs.ref/sg13g2_sram).
// Macro pins (PDK datasheets): active-high MEN / WEN / REN, active-high bit mask BM (tied to all ones: lanes are
// split by the caller), DLY tied to 1 (mandatory), BIST inputs tied off. DOUT holds its value while not reading.
// The macro has no read / write conflict handling; g2d never reads and writes one address in the same cycle
// (g2d_ram_1r1w coll_o, DBG_STATUS.RAM_COLL).
module g2d_ihp_2p #(
  parameter int unsigned DEPTH = 256,
  parameter int unsigned WIDTH = 32,
  localparam int unsigned AW = $clog2(DEPTH)
) (
  input  logic             clk_i,
  input  logic             re_i,
  input  logic [AW-1:0]    raddr_i,
  output logic [WIDTH-1:0] rdata_o,
  input  logic             we_i,
  input  logic [AW-1:0]    waddr_i,
  input  logic [WIDTH-1:0] wdata_i
);

  /* verilator lint_off PINCONNECTEMPTY */
  generate
    if (DEPTH == 64 && WIDTH == 22) begin : g_64x22
      RM_IHPSG13_2P_64x22_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(22'h3fffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(22'h3fffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 64 && WIDTH == 32) begin : g_64x32
      RM_IHPSG13_2P_64x32_c2 u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT());
    end else if (DEPTH == 256 && WIDTH == 8) begin : g_256x8
      RM_IHPSG13_2P_256x8_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(8'hff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(8'hff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 16) begin : g_256x16
      RM_IHPSG13_2P_256x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(16'hffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(16'hffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 32) begin : g_256x32
      RM_IHPSG13_2P_256x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(32'hffffffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(32'hffffffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 8) begin : g_512x8
      RM_IHPSG13_2P_512x8_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(8'hff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(8'hff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 16) begin : g_512x16
      RM_IHPSG13_2P_512x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(16'hffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(16'hffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 32) begin : g_512x32
      RM_IHPSG13_2P_512x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(32'hffffffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(32'hffffffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 16) begin : g_1024x16
      RM_IHPSG13_2P_1024x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(16'hffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(16'hffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 32) begin : g_1024x32
      RM_IHPSG13_2P_1024x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(re_i), .A_WEN(1'b0), .A_REN(re_i), .A_ADDR(raddr_i), .A_DIN('0), .A_DLY(1'b1),
      .A_DOUT(rdata_o),
      .B_CLK(clk_i), .B_MEN(we_i), .B_WEN(we_i), .B_REN(1'b0), .B_ADDR(waddr_i), .B_DIN(wdata_i), .B_DLY(1'b1),
      .B_DOUT(),
      .A_BM(32'hffffffff), .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0),
      .B_BM(32'hffffffff), .B_BIST_CLK(1'b0), .B_BIST_EN(1'b0), .B_BIST_MEN(1'b0), .B_BIST_WEN(1'b0), .B_BIST_REN(1'b0),
      .B_BIST_ADDR('0), .B_BIST_DIN('0), .B_BIST_BM('0));
    end else begin : g_bad
`ifndef SYNTHESIS
      initial $fatal(1, "g2d_ihp_2p: no IHP two-port macro of %0d x %0d", DEPTH, WIDTH);
`endif
      assign rdata_o = '0;
    end
  endgenerate
  /* verilator lint_on PINCONNECTEMPTY */

endmodule
