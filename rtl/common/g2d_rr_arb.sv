// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Round-robin arbiter. Grants one of N requesters; the grant is held stable while the winner's request
// stays up and the downstream does not accept (AXI-style valid stability). A winner that withdraws its
// request (a client stopped by a halt or soft reset) loses the grant at once: valid_o never shows a
// request that is no longer presented. After a handshake the priority moves past the winner.
// REG_PICK = 1 (v1.7, timing): the round-robin choice is registered, so valid_o / idx_o depend on the requests
// only through one AND gate (the picked requester must still be requesting): a request is granted one cycle after
// it appears, and a winner may be granted twice in a row (no starvation: the priority still moves past it).

module g2d_rr_arb #(
  parameter int unsigned N = 2,
  parameter bit          REG_PICK = 1'b0,
  localparam int unsigned IDX_W = (N > 1) ? $clog2(N) : 1
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clr_i,
  input  logic [N-1:0]     req_i,
  input  logic             ack_i,     // downstream accepted the granted request
  output logic             valid_o,
  output logic [IDX_W-1:0] idx_o
);

  logic [IDX_W-1:0] prio_q;   // highest-priority index
  logic             lock_q;   // a grant is pending (valid shown, not yet accepted)
  logic [IDX_W-1:0] lock_idx_q;
  logic [IDX_W-1:0] pick;
  logic             found;

  always_comb begin
    pick  = '0;
    found = 1'b0;
    for (int unsigned k = 0; k < N; k++) begin
      logic [IDX_W:0] i;
      i = {1'b0, prio_q} + (IDX_W + 1)'(k);
      if (i >= (IDX_W + 1)'(N)) i = i - (IDX_W + 1)'(N);
      if (!found && req_i[i[IDX_W-1:0]]) begin
        pick  = i[IDX_W-1:0];
        found = 1'b1;
      end
    end
  end

  logic hold;
  assign hold    = lock_q & req_i[lock_idx_q];
  if (REG_PICK) begin : g_reg
    logic [IDX_W-1:0] pick_q;
    logic             found_q;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        pick_q  <= '0;
        found_q <= 1'b0;
      end else if (clr_i) begin
        pick_q  <= '0;
        found_q <= 1'b0;
      end else begin
        pick_q  <= pick;
        found_q <= found;
      end
    end
    assign valid_o = hold | (found_q & req_i[pick_q]);
    assign idx_o   = hold ? lock_idx_q : pick_q;
  end else begin : g_comb
    assign valid_o = hold | found;
    assign idx_o   = hold ? lock_idx_q : pick;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      prio_q     <= '0;
      lock_q     <= 1'b0;
      lock_idx_q <= '0;
    end else if (clr_i) begin
      prio_q     <= '0;
      lock_q     <= 1'b0;
      lock_idx_q <= '0;
    end else if (valid_o) begin
      if (ack_i) begin
        lock_q <= 1'b0;
        prio_q <= (idx_o == IDX_W'(N - 1)) ? '0 : idx_o + IDX_W'(1);
      end else begin
        lock_q     <= 1'b1;
        lock_idx_q <= idx_o;
      end
    end else begin
      lock_q <= 1'b0;
    end
  end

endmodule
