// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Write-producer contract (arch.md §4.3): request and data beats held and stable while stalled (a request
// not yet accepted may be withdrawn only while the producer is stopped by a soft reset); the producer
// supplies exactly the DATA_W-aligned beats covering each request, so a data beat is never presented
// when no request owes one. Simulation only; bound into g2d_fill and g2d_wcomb.

module g2d_wrprod_sva #(
  parameter int unsigned DATA_W = 64,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              stop_i,
  input  logic              wr_req_valid_o,
  input  logic              wr_req_ready_i,
  input  logic [31:0]       wr_req_addr_o,
  input  logic [15:0]       wr_req_len_o,
  input  logic              wr_dat_valid_o,
  input  logic              wr_dat_ready_i,
  input  logic [DATA_W-1:0] wr_dat_data_o,
  input  logic [DB-1:0]     wr_dat_strb_o
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  p_req_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (!stop_i && wr_req_valid_o && !wr_req_ready_i) |=>
      (stop_i || (wr_req_valid_o && $stable({wr_req_addr_o, wr_req_len_o}))))
    else $error("p_req_hold (%m): write request dropped or changed while stalled");
  p_dat_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (wr_dat_valid_o && !wr_dat_ready_i) |=> (wr_dat_valid_o && $stable({wr_dat_data_o, wr_dat_strb_o})))
    else $error("p_dat_hold (%m): write beat dropped or changed while stalled");

  // beats owed by the accepted requests
  longint owed;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      owed <= 0;
    end else if (armed) begin
      longint add;
      add = 0;
      if (wr_req_valid_o && wr_req_ready_i)
        add = (longint'(wr_req_addr_o[$clog2(DB)-1:0]) + longint'(wr_req_len_o)) / longint'(DB) + 64'sd1;
      if (wr_dat_valid_o && owed + add == 0)
        $error("p_beats (%m): write beat presented with no request owing it");
      owed <= owed + add - longint'(wr_dat_valid_o && wr_dat_ready_i);
    end
  end
endmodule
