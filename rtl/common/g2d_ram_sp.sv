// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Single-port SRAM wrapper (arch.md §6.4, §11). Behavioural model; the ASIC macro / FPGA BRAM mapping
// replaces this file in syn/ and fpga/.
//
// One access per cycle: en_i with we_i = 1 writes (per-lane enables, BE_W lanes), with we_i = 0 reads.
// rdata_o holds the read data from the cycle after the read and keeps it until the next read (a write
// leaves rdata_o unchanged).

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

  logic [WIDTH-1:0] mem_q [DEPTH];

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

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_sp: WIDTH must be a multiple of BE_W");
  end
`endif

endmodule
