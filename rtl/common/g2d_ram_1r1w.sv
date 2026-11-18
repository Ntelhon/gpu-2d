// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two-port SRAM wrapper (arch.md §6.4, §11): one write port, one read port, same clock.
// Behavioural model; the ASIC macro / FPGA BRAM mapping replaces this file in syn/ and fpga/.
//
// Read: rdata_o holds mem[raddr_i] from the cycle after re_i, and keeps it until the next read.
// Write: per-lane write enables (BE_W lanes of WIDTH/BE_W bits).
// Reading an address in the same cycle it is written gives undefined data on a real macro; the users
// never do it (simulation assertion below; the behavioural model would return the old data).
// coll_o (v1.8, Q-FPGA-10): that collision as a synthesizable flag (combinational, same cycle as the access), so a
// collision that only the board's timing produces is recorded in DBG_STATUS.RAM_COLL (arch.md §9.1).

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

  logic [WIDTH-1:0] mem_q [DEPTH];

  always_ff @(posedge clk_i) begin
    if (we_i) begin
      for (int l = 0; l < int'(BE_W); l++) begin
        if (wbe_i[l]) mem_q[waddr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
      end
    end
    if (re_i) rdata_o <= mem_q[raddr_i];
  end

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_1r1w: WIDTH must be a multiple of BE_W");
  end
  // not at time 0: inputs may come from registers that hold random initial values until the asynchronous
  // reset (which this wrapper does not see) takes effect
  always_ff @(posedge clk_i) begin
    if ($time > 0 && we_i && re_i && waddr_i == raddr_i && wbe_i != '0) begin
      $error("g2d_ram_1r1w: read and write of address %0d in the same cycle", raddr_i);
    end
  end
`endif

endmodule
