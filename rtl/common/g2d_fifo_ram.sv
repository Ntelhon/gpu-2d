// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Synchronous FIFO with SRAM storage (g2d_ram_1r1w) and a two-entry flop output stage that hides the
// one-cycle read latency, so it moves one item per cycle. Capacity: DEPTH entries in the RAM plus up to
// two in the output stage (in_ready_o only looks at the RAM, so at least DEPTH items always fit).
// No combinational path from out_ready_i to in_ready_o. clr_i empties the FIFO synchronously.
// Latency: an item pushed into an empty FIFO is visible at the output two cycles later.

module g2d_fifo_ram #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1,
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
  output logic [WIDTH-1:0] out_data_o
);

  logic [AW-1:0]    wr_q, rd_q;
  logic [CNT_W-1:0] cnt_q;       // entries in the RAM not yet read
  logic             inflight_q;  // a RAM read was issued last cycle
  logic             push, rd_en, pop;
  logic [WIDTH-1:0] rdata;
  /* verilator lint_off UNUSEDSIGNAL */
  logic             ram_coll;    // never set: a read needs an entry, a write needs room (the pointers differ)
  /* verilator lint_on UNUSEDSIGNAL */
  logic [1:0]       ob_cnt;
  logic             ob_in_ready;

  function automatic logic [AW-1:0] inc(logic [AW-1:0] p);
    return (p == AW'(DEPTH - 1)) ? '0 : p + AW'(1);
  endfunction

  assign in_ready_o = (cnt_q != CNT_W'(DEPTH));
  assign push       = in_valid_i & in_ready_o;
  assign pop        = out_valid_o & out_ready_i;
  // read when the output stage has room for the item when it arrives next cycle
  assign rd_en      = (cnt_q != '0) && (2'(ob_cnt) + 2'(inflight_q) - 2'(pop) <= 2'd1);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      wr_q       <= '0;
      rd_q       <= '0;
      cnt_q      <= '0;
      inflight_q <= 1'b0;
    end else if (clr_i) begin
      wr_q       <= '0;
      rd_q       <= '0;
      cnt_q      <= '0;
      inflight_q <= 1'b0;
    end else begin
      if (push)  wr_q <= inc(wr_q);
      if (rd_en) rd_q <= inc(rd_q);
      cnt_q      <= cnt_q + CNT_W'(push) - CNT_W'(rd_en);
      inflight_q <= rd_en;
    end
  end

  // While the RAM holds an item, wr_q != rd_q unless it is full, and a full RAM accepts no push, so a
  // read never hits the address being written.
  g2d_ram_1r1w #(.WIDTH(WIDTH), .DEPTH(DEPTH), .BE_W(1)) u_ram (
    .clk_i,
    .we_i    (push),
    .waddr_i (wr_q),
    .wdata_i (in_data_i),
    .wbe_i   (1'b1),
    .re_i    (rd_en),
    .raddr_i (rd_q),
    .coll_o  (ram_coll),
    .rdata_o (rdata)
  );

  g2d_fifo #(.WIDTH(WIDTH), .DEPTH(2)) u_out (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (inflight_q),
    .in_ready_o  (ob_in_ready),
    .in_data_i   (rdata),
    .out_valid_o,
    .out_ready_i,
    .out_data_o,
    .count_o     (ob_cnt)
  );

  logic unused;
  assign unused = ob_in_ready;  // always 1 when inflight_q is set (rd_en condition)

endmodule
