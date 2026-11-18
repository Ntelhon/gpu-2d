// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Test harness for the g2d_pkg arithmetic functions (combinational): format compression (with and without
// dither) and expansion, format code classification, global alpha, mul8 and the div255 helpers, compared
// exhaustively with the golden model in test_pkg.py.

module g2d_pkg_tb
  import g2d_pkg::*;
(
  input  logic [7:0]  fmt,
  input  logic [31:0] argb,
  input  logic [1:0]  mode,
  input  logic [7:0]  g,
  input  logic [7:0]  a,
  input  logic [7:0]  b,
  input  logic [1:0]  dx,
  input  logic [1:0]  dy,
  output logic [31:0] packed_o,
  output logic [31:0] dither_o,
  output logic [31:0] expand_o,
  output logic [31:0] alpha_o,
  output logic [7:0]  mul8_o,
  output logic        valid_o,
  output logic        tgt_o,
  output logic        indexed_o,
  output logic [2:0]  lg_bits_o,
  output logic [1:0]  lg_o
);
  assign packed_o  = fmt_compress(fmt, argb);
  assign dither_o  = fmt_compress_d(fmt, argb, 1'b1, bayer(dx, dy));
  assign expand_o  = fmt_expand(fmt, argb);
  assign alpha_o   = apply_global_alpha(argb, mode, g);
  assign mul8_o    = mul8(a, b);
  assign valid_o   = fmt_valid(fmt);
  assign tgt_o     = fmt_tgt_valid(fmt);
  assign indexed_o = fmt_indexed(fmt);
  assign lg_bits_o = fmt_lg_bits(fmt);
  assign lg_o      = fmt_lg_bytes(fmt);
endmodule
