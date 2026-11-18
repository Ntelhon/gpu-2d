// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Single-port RAM, ASIC variant (v1.9, B-2). Same module and behaviour as rtl/common/g2d_ram_sp.sv (drop-in by
// file list), mapped onto the port shape of a typical single-port SRAM macro, g2d_sram_1p (active-low chip and
// write enables, active-low bit-write mask, output held while not reading). Behavioural stand-in for simulation:
// rtl/ram/asic/models/g2d_sram_1p.sv. MBIST hooks as in g2d_ram_1r1w (tied off).

module g2d_ram_sp #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             en_i,
  input  logic             we_i,
  input  logic [AW-1:0]    addr_i,
  input  logic [WIDTH-1:0] wdata_i,
  input  logic [BE_W-1:0]  wbe_i,
  output logic [WIDTH-1:0] rdata_o
);

  localparam int unsigned LW = WIDTH / BE_W;

  logic             bist_en, bist_ce, bist_we;
  logic [AW-1:0]    bist_addr;
  logic [WIDTH-1:0] bist_wdata;
  assign bist_en    = 1'b0;
  assign bist_ce    = 1'b0;
  assign bist_we    = 1'b0;
  assign bist_addr  = '0;
  assign bist_wdata = '0;

  logic [WIDTH-1:0] bwe;
  always_comb begin
    for (int l = 0; l < int'(BE_W); l++) bwe[l * LW +: LW] = {LW{wbe_i[l]}};
  end

  g2d_sram_1p #(.WIDTH(WIDTH), .DEPTH(DEPTH)) u_macro (
    .clk_i  (clk_i),
    .ceb_i  (~(bist_en ? bist_ce : en_i)),
    .web_i  (~(bist_en ? bist_we : we_i)),
    .a_i    (bist_en ? bist_addr : addr_i),
    .d_i    (bist_en ? bist_wdata : wdata_i),
    .bweb_i (~(bist_en ? {WIDTH{1'b1}} : bwe)),
    .q_o    (rdata_o)
  );

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_sp: WIDTH must be a multiple of BE_W");
  end
`endif

endmodule
