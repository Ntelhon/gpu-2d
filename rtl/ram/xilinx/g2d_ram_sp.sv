// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Single-port RAM, Xilinx variant (v1.9, B-2). Same module and behaviour as rtl/common/g2d_ram_sp.sv (drop-in by
// file list): block RAM in NO_CHANGE mode (a write leaves the output register unchanged, as the behavioural model)
// when WIDTH * DEPTH >= BRAM_MIN_BITS, else distributed RAM.

module g2d_ram_sp #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  parameter int unsigned BRAM_MIN_BITS = 4096,
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

  if (WIDTH * DEPTH >= BRAM_MIN_BITS) begin : g_bram
    (* ram_style = "block" *) logic [WIDTH-1:0] mem_q [DEPTH];
    always_ff @(posedge clk_i) begin
      if (en_i) begin
        if (we_i) begin
          for (int l = 0; l < int'(BE_W); l++) begin
            if (wbe_i[l]) mem_q[addr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
          end
        end else begin
          rdata_o <= mem_q[addr_i];
        end
      end
    end
  end else begin : g_lutram
    (* ram_style = "distributed" *) logic [WIDTH-1:0] mem_q [DEPTH];
    always_ff @(posedge clk_i) begin
      if (en_i) begin
        if (we_i) begin
          for (int l = 0; l < int'(BE_W); l++) begin
            if (wbe_i[l]) mem_q[addr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
          end
        end else begin
          rdata_o <= mem_q[addr_i];
        end
      end
    end
  end

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_sp: WIDTH must be a multiple of BE_W");
  end
`endif

endmodule
