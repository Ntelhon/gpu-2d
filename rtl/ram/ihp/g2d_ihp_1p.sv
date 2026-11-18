// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// One IHP SG13G2 single-port SRAM macro (v2.0 step C, rtl/ram/ihp/README.md). en_i with we_i writes wdata_i,
// with !we_i reads (rdata_o holds the value until the next read, also during writes). Pins as in g2d_ihp_2p
// (BM all ones, DLY 1, BIST tied off). DEPTH x WIDTH must be one of the sizes in the generate case.
module g2d_ihp_1p #(
  parameter int unsigned DEPTH = 256,
  parameter int unsigned WIDTH = 32,
  localparam int unsigned AW = $clog2(DEPTH)
) (
  input  logic             clk_i,
  input  logic             en_i,
  input  logic             we_i,
  input  logic [AW-1:0]    addr_i,
  input  logic [WIDTH-1:0] wdata_i,
  output logic [WIDTH-1:0] rdata_o
);

  generate
    if (DEPTH == 64 && WIDTH == 64) begin : g_64x64
      RM_IHPSG13_1P_64x64_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(64'hffffffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 8) begin : g_256x8
      RM_IHPSG13_1P_256x8_c3_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(8'hff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 16) begin : g_256x16
      RM_IHPSG13_1P_256x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(16'hffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 32) begin : g_256x32
      RM_IHPSG13_1P_256x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(32'hffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 48) begin : g_256x48
      RM_IHPSG13_1P_256x48_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(48'hffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 256 && WIDTH == 64) begin : g_256x64
      RM_IHPSG13_1P_256x64_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(64'hffffffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 8) begin : g_512x8
      RM_IHPSG13_1P_512x8_c3_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(8'hff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 16) begin : g_512x16
      RM_IHPSG13_1P_512x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(16'hffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 32) begin : g_512x32
      RM_IHPSG13_1P_512x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(32'hffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 512 && WIDTH == 64) begin : g_512x64
      RM_IHPSG13_1P_512x64_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(64'hffffffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 8) begin : g_1024x8
      RM_IHPSG13_1P_1024x8_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(8'hff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 16) begin : g_1024x16
      RM_IHPSG13_1P_1024x16_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(16'hffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 32) begin : g_1024x32
      RM_IHPSG13_1P_1024x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(32'hffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 1024 && WIDTH == 64) begin : g_1024x64
      RM_IHPSG13_1P_1024x64_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(64'hffffffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 2048 && WIDTH == 32) begin : g_2048x32
      RM_IHPSG13_1P_2048x32_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(32'hffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 2048 && WIDTH == 64) begin : g_2048x64
      RM_IHPSG13_1P_2048x64_c2_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(64'hffffffffffffffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 4096 && WIDTH == 8) begin : g_4096x8
      RM_IHPSG13_1P_4096x8_c3_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(8'hff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else if (DEPTH == 4096 && WIDTH == 16) begin : g_4096x16
      RM_IHPSG13_1P_4096x16_c3_bm_bist u_macro (
      .A_CLK(clk_i), .A_MEN(en_i), .A_WEN(we_i), .A_REN(~we_i), .A_ADDR(addr_i), .A_DIN(wdata_i), .A_DLY(1'b1),
      .A_DOUT(rdata_o), .A_BM(16'hffff),
      .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0),
      .A_BIST_ADDR('0), .A_BIST_DIN('0), .A_BIST_BM('0));
    end else begin : g_bad
`ifndef SYNTHESIS
      initial $fatal(1, "g2d_ihp_1p: no IHP single-port macro of %0d x %0d", DEPTH, WIDTH);
`endif
      assign rdata_o = '0;
    end
  endgenerate

endmodule
