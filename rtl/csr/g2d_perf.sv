// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Performance counters (arch.md §9). 32-bit wrapping counters, counting while PERF_CTRL.EN = 1,
// cleared by PERF_CTRL.CLEAR, copied into the readable shadow registers by PERF_CTRL.SNAPSHOT.
// The CSR read value is always the shadow. Counters and shadows survive a soft reset.
// CLEAR and SNAPSHOT in the same write: the snapshot takes the values before the clear.

module g2d_perf
  import g2d_csr_pkg::*;
#(
  parameter int unsigned PIX_W = 14   // width of the per-cycle pixel increment
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  g2d_csr_reg2hw_perf_ctrl_t ctrl_i,
  input  logic             busy_i,
  input  logic             draw_done_i,
  input  logic [PIX_W-1:0] pixels_i,
  input  logic             rd_beat_i,
  input  logic             wr_beat_i,
  input  logic             rd_stall_i,
  input  logic             wr_stall_i,
  input  logic             drain_i,
  input  logic [2:0]       src_miss_i,      // line misses this cycle
  output g2d_csr_hw2reg_perf_cycles_t         cycles_o,
  output g2d_csr_hw2reg_perf_busy_cycles_t    busy_cycles_o,
  output g2d_csr_hw2reg_perf_draws_t          draws_o,
  output g2d_csr_hw2reg_perf_pixels_out_t     pixels_out_o,
  output g2d_csr_hw2reg_perf_rd_beats_t       rd_beats_o,
  output g2d_csr_hw2reg_perf_wr_beats_t       wr_beats_o,
  output g2d_csr_hw2reg_perf_rd_stall_t       rd_stall_o,
  output g2d_csr_hw2reg_perf_wr_stall_t       wr_stall_o,
  output g2d_csr_hw2reg_perf_drain_cyc_t      drain_cyc_o,
  output g2d_csr_hw2reg_perf_src_cache_miss_t src_cache_miss_o
);

  localparam int unsigned N = 10;

  logic [31:0] inc [N];
  logic [31:0] cnt_q [N];
  logic [31:0] shadow_q [N];

  assign inc[0] = 32'd1;
  assign inc[1] = 32'(busy_i);
  assign inc[2] = 32'(draw_done_i);
  assign inc[3] = 32'(pixels_i);
  assign inc[4] = 32'(rd_beat_i);
  assign inc[5] = 32'(wr_beat_i);
  assign inc[6] = 32'(rd_stall_i);
  assign inc[7] = 32'(wr_stall_i);
  assign inc[8] = 32'(drain_i);
  assign inc[9] = 32'(src_miss_i);

  logic do_clear, do_snap;
  assign do_clear = ctrl_i.clear.qe & ctrl_i.clear.q;
  assign do_snap  = ctrl_i.snapshot.qe & ctrl_i.snapshot.q;

  for (genvar i = 0; i < N; i++) begin : g_cnt
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        cnt_q[i]    <= '0;
        shadow_q[i] <= '0;
      end else begin
        if (do_clear)         cnt_q[i] <= '0;
        else if (ctrl_i.en.q) cnt_q[i] <= cnt_q[i] + inc[i];
        if (do_snap)          shadow_q[i] <= cnt_q[i];
      end
    end
  end

  assign cycles_o.value.d         = shadow_q[0];
  assign busy_cycles_o.value.d    = shadow_q[1];
  assign draws_o.value.d          = shadow_q[2];
  assign pixels_out_o.value.d     = shadow_q[3];
  assign rd_beats_o.value.d       = shadow_q[4];
  assign wr_beats_o.value.d       = shadow_q[5];
  assign rd_stall_o.value.d       = shadow_q[6];
  assign wr_stall_o.value.d       = shadow_q[7];
  assign drain_cyc_o.value.d      = shadow_q[8];
  assign src_cache_miss_o.value.d = shadow_q[9];

endmodule
