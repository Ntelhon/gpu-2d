// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Read-client contract (arch.md §4.3): a request is held and stable while stalled (except while the
// client is stopped by a soft reset, which withdraws requests), and every response beat is accepted at
// once (clients request only what they can buffer, so R never waits on a client: the basis of the
// no-deadlock argument, Phase 4 condition A1). Simulation only; bound into every read client with .*.

module g2d_rdclient_sva (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        stop_i,
  input  logic        rd_req_valid_o,
  input  logic        rd_req_ready_i,
  input  logic [31:0] rd_req_addr_o,
  input  logic [15:0] rd_req_len_o,
  input  logic        rd_rsp_valid_i,
  input  logic        rd_rsp_ready_o
);
  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  p_req_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (!stop_i && rd_req_valid_o && !rd_req_ready_i) |=>
      (stop_i || (rd_req_valid_o && $stable({rd_req_addr_o, rd_req_len_o}))))
    else $error("p_req_hold (%m): read request dropped or changed while stalled");
  p_rsp_accept: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed) rd_rsp_valid_i |-> rd_rsp_ready_o)
    else $error("p_rsp_accept (%m): read client back-pressured a response beat");
endmodule
