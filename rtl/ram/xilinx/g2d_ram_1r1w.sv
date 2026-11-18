// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two-port RAM, Xilinx 7-series / UltraScale variant (v1.9, B-2, docs/INTEGRATION.md "Memories"). Same module and
// behaviour as rtl/common/g2d_ram_1r1w.sv (drop-in by file list), with the mapping made explicit:
// - block RAM (RAMB18 / RAMB36, simple dual port, common clock) when WIDTH * DEPTH >= BRAM_MIN_BITS, else
//   distributed RAM (LUTRAM with an output register);
// - byte / lane write enables map to the BRAM write-enable bits (lane width 8 or 9 for full use);
// - a same-address read and write in one cycle returns undefined data on hardware ("NO_CHANGE" / read-first
//   depend on the primitive's mode); G2D never does it (coll_o flags it, DBG_STATUS.RAM_COLL, arch.md §9.1).

module g2d_ram_1r1w #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  parameter int unsigned BRAM_MIN_BITS = 4096,
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

  if (WIDTH * DEPTH >= BRAM_MIN_BITS) begin : g_bram
    (* ram_style = "block" *) logic [WIDTH-1:0] mem_q [DEPTH];
    always_ff @(posedge clk_i) begin
      if (we_i) begin
        for (int l = 0; l < int'(BE_W); l++) begin
          if (wbe_i[l]) mem_q[waddr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
        end
      end
      if (re_i) rdata_o <= mem_q[raddr_i];
    end
  end else begin : g_lutram
    (* ram_style = "distributed" *) logic [WIDTH-1:0] mem_q [DEPTH];
    always_ff @(posedge clk_i) begin
      if (we_i) begin
        for (int l = 0; l < int'(BE_W); l++) begin
          if (wbe_i[l]) mem_q[waddr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
        end
      end
      if (re_i) rdata_o <= mem_q[raddr_i];
    end
  end

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
