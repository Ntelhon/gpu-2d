// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// SoC simulation only (v1.8, Q-FPGA-9 / Q-FPGA-10): makes the DRAM model's timing less ideal, between the delay stage
// and axi_sim_mem. Two effects, both off unless enabled by plusargs:
//   +DRAM_REFI=<n> +DRAM_RFC=<m>  every n cycles a refresh window of m cycles in which no transfer starts on any
//                                 channel (DDR3 at 50 MHz SoC clock: tREFI 7.8 us = 390 cycles, tRFC ~ 260 ns =
//                                 13 cycles; the real MIG adds its own queueing on top)
//   +DRAM_JITTER=<p>              each cycle, with probability p / 256, no transfer starts (variable latency)
//   +DRAM_SEED=<s>                seed of the jitter LFSR (default 1)
// A transfer is blocked by masking both handshake signals of a channel in the same cycle. A VALID that one side has
// already seen without READY is never withdrawn (AXI rule): that channel is not masked until its handshake.
module tb_dram_stall #(
  parameter type axi_req_t = logic,
  parameter type axi_rsp_t = logic
) (
  input  logic     clk_i,
  input  logic     rst_ni,
  input  axi_req_t slv_req_i,
  output axi_rsp_t slv_rsp_o,
  output axi_req_t mst_req_o,
  input  axi_rsp_t mst_rsp_i
);
  int unsigned refi, rfc, jitter, seed;
  logic [31:0] lfsr_q, cyc_q;
  logic        stall;
  // per channel: VALID already shown (toward the receiver) without READY
  logic        ar_q, aw_q, w_q, r_q, b_q;

  initial begin
    refi = 0; rfc = 0; jitter = 0; seed = 1;
    void'($value$plusargs("DRAM_SEED=%d", seed));
    if (seed == 0) seed = 1;
    void'($value$plusargs("DRAM_REFI=%d", refi));
    void'($value$plusargs("DRAM_RFC=%d", rfc));
    void'($value$plusargs("DRAM_JITTER=%d", jitter));
    if (refi != 0 || jitter != 0)
      $display("[tb] DRAM timing: refresh %0d of every %0d cycles, jitter %0d/256", rfc, refi, jitter);
  end

  assign stall = (refi != 0 && cyc_q < rfc) || (jitter != 0 && lfsr_q[7:0] < 8'(jitter));

  always_comb begin
    mst_req_o = slv_req_i;
    slv_rsp_o = mst_rsp_i;
    if (stall) begin
      if (!ar_q) begin mst_req_o.ar_valid = 1'b0; slv_rsp_o.ar_ready = 1'b0; end
      if (!aw_q) begin mst_req_o.aw_valid = 1'b0; slv_rsp_o.aw_ready = 1'b0; end
      if (!w_q)  begin mst_req_o.w_valid  = 1'b0; slv_rsp_o.w_ready  = 1'b0; end
      if (!r_q)  begin slv_rsp_o.r_valid  = 1'b0; mst_req_o.r_ready  = 1'b0; end
      if (!b_q)  begin slv_rsp_o.b_valid  = 1'b0; mst_req_o.b_ready  = 1'b0; end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      cyc_q  <= '0;
      lfsr_q <= seed;
      {ar_q, aw_q, w_q, r_q, b_q} <= '0;
    end else begin
      cyc_q  <= (refi != 0 && cyc_q + 1 >= refi) ? '0 : cyc_q + 1;
      lfsr_q <= {lfsr_q[30:0], lfsr_q[31] ^ lfsr_q[21] ^ lfsr_q[1] ^ lfsr_q[0]};
      ar_q <= mst_req_o.ar_valid & ~mst_rsp_i.ar_ready;
      aw_q <= mst_req_o.aw_valid & ~mst_rsp_i.aw_ready;
      w_q  <= mst_req_o.w_valid  & ~mst_rsp_i.w_ready;
      r_q  <= slv_rsp_o.r_valid  & ~slv_req_i.r_ready;
      b_q  <= slv_rsp_o.b_valid  & ~slv_req_i.b_ready;
    end
  end

endmodule
