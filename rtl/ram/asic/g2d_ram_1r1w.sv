// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two-port RAM, ASIC variant (v1.9, B-2, docs/INTEGRATION.md "Memories"). Same module and behaviour as
// rtl/common/g2d_ram_1r1w.sv (drop-in by file list). It maps the wrapper onto the port shape of a typical
// compiler-generated two-port SRAM macro, g2d_sram_2p (active-low chip enables, active-low bit-write mask, read
// port A / write port B, one clock). rtl/ram/asic/models/g2d_sram_2p.sv is a behavioural stand-in for simulation;
// the integrator replaces it by the memory compiler's macro (or a wrapper around it with this port list).
//
// MBIST / DFT hooks: the bist_* signals select a test access path in front of the macro, where a memory-BIST
// insertion flow connects its controller; they are tied off here (functional path only). The macro's test pins
// (scan bypass, margin settings) are tied to their functional values in the stand-in.

module g2d_ram_1r1w #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             we_i,
  input  logic [AW-1:0]    waddr_i,
  input  logic [WIDTH-1:0] wdata_i,
  input  logic [BE_W-1:0]  wbe_i,
  input  logic             re_i,
  input  logic [AW-1:0]    raddr_i,
  output logic [WIDTH-1:0] rdata_o,
  output logic             coll_o
);

  assign coll_o = we_i && re_i && (waddr_i == raddr_i) && (wbe_i != '0);

  localparam int unsigned LW = WIDTH / BE_W;

  // MBIST access path (tied off; a BIST insertion flow drives these)
  logic             bist_en, bist_we, bist_re;
  logic [AW-1:0]    bist_waddr, bist_raddr;
  logic [WIDTH-1:0] bist_wdata;
  assign bist_en    = 1'b0;
  assign bist_we    = 1'b0;
  assign bist_re    = 1'b0;
  assign bist_waddr = '0;
  assign bist_raddr = '0;
  assign bist_wdata = '0;

  logic [WIDTH-1:0] bwe;     // bit write enables from the lane enables
  always_comb begin
    for (int l = 0; l < int'(BE_W); l++) bwe[l * LW +: LW] = {LW{wbe_i[l]}};
  end

  logic             m_ceb_a, m_ceb_b;
  logic [AW-1:0]    m_aa, m_ab;
  logic [WIDTH-1:0] m_db, m_bweb, m_qa;
  assign m_ceb_a = ~(bist_en ? bist_re : re_i);
  assign m_aa    = bist_en ? bist_raddr : raddr_i;
  assign m_ceb_b = ~(bist_en ? bist_we : we_i);
  assign m_ab    = bist_en ? bist_waddr : waddr_i;
  assign m_db    = bist_en ? bist_wdata : wdata_i;
  assign m_bweb  = ~(bist_en ? {WIDTH{1'b1}} : bwe);

  g2d_sram_2p #(.WIDTH(WIDTH), .DEPTH(DEPTH)) u_macro (
    .clk_i  (clk_i),
    .ceb_a_i(m_ceb_a),
    .aa_i   (m_aa),
    .qa_o   (m_qa),
    .ceb_b_i(m_ceb_b),
    .ab_i   (m_ab),
    .db_i   (m_db),
    .bweb_i (m_bweb)
  );
  assign rdata_o = m_qa;

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_1r1w: WIDTH must be a multiple of BE_W");
  end
  always_ff @(posedge clk_i) begin
    if ($time > 0 && we_i && re_i && waddr_i == raddr_i && wbe_i != '0) begin
      $error("g2d_ram_1r1w: read and write of address %0d in the same cycle", raddr_i);
    end
  end
`endif

endmodule
