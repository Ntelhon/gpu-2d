// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Synchronous FIFO, flop storage, valid/ready on both sides. No combinational path from out_ready_i
// to in_ready_o (a push into a full FIFO waits one cycle even if a pop happens in the same cycle).
// clr_i empties the FIFO synchronously (soft reset).

module g2d_fifo #(
  parameter int unsigned WIDTH = 8,
  parameter int unsigned DEPTH = 4,
  localparam int unsigned CNT_W = $clog2(DEPTH + 1)
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clr_i,
  input  logic             in_valid_i,
  output logic             in_ready_o,
  input  logic [WIDTH-1:0] in_data_i,
  output logic             out_valid_o,
  input  logic             out_ready_i,
  output logic [WIDTH-1:0] out_data_o,
  output logic [CNT_W-1:0] count_o
);

  localparam int unsigned PTR_W = (DEPTH > 1) ? $clog2(DEPTH) : 1;

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PTR_W-1:0] wr_q, rd_q;
  logic [CNT_W-1:0] cnt_q;
  logic             push, pop;

  assign in_ready_o  = (cnt_q != CNT_W'(DEPTH));
  assign out_valid_o = (cnt_q != '0);
  assign out_data_o  = mem_q[rd_q];
  assign count_o     = cnt_q;
  assign push        = in_valid_i & in_ready_o;
  assign pop         = out_valid_o & out_ready_i;

  function automatic logic [PTR_W-1:0] inc(logic [PTR_W-1:0] p);
    return (p == PTR_W'(DEPTH - 1)) ? '0 : p + PTR_W'(1);
  endfunction

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wr_q  <= '0;
      rd_q  <= '0;
      cnt_q <= '0;
    end else if (clr_i) begin
      wr_q  <= '0;
      rd_q  <= '0;
      cnt_q <= '0;
    end else begin
      if (push) wr_q <= inc(wr_q);
      if (pop)  rd_q <= inc(rd_q);
      cnt_q <= cnt_q + CNT_W'(push) - CNT_W'(pop);
    end
  end

  // Data storage needs no reset: entries are qualified by the count.
  always_ff @(posedge clk_i) begin
    if (push) mem_q[wr_q] <= in_data_i;
  end

endmodule
