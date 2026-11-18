// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Winding accumulator and sweep of the path rasterizer (rendering.md §8.3, §8.4; arch.md §6.3).
//
// Two buffers (double-buffered, Phase 4 assumption A6), each 16 banks (one per sample row j) of
// 256 x 8-bit SRAM: delta[j][x], mod 256.
//
// Events: per bank at most one per clock, (buffer, column, dir). Two-stage read-modify-write: E0 reads
// delta[col], E1 writes delta + dir. When an event hits the column written by the event just before it,
// the written value is forwarded and the SRAM is not read (no read/write collision).
// A buffer is FREE (all zero) -> FILL (a pass writes events) -> done_i(buf, w) -> SWEEP -> FREE. Passes
// must use buffer 0, 1, 0, 1, ... (sweeps follow that order).
// done_i must come after the pass's last event has entered (the accumulator waits two cycles for the
// last write itself).
// Sweep: x = 0 .. w-1, one pixel per clock: read all 16 banks at x, running sums wnd_j += delta[j][x],
// inside_j = even-odd ? wnd_j[0] : wnd_j != 0, cov = AA ? (n*255 + 8) >> 4 : (inside_0 ? 255 : 0);
// delta[j][x] is cleared one cycle later. Output through a 2-entry FIFO (valid/ready).
// After reset or clr_i both buffers are cleared (256 cycles, init_o high).

module g2d_wind_acc (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clr_i,
  input  logic             cfg_aa_i,
  input  logic             cfg_eo_i,
  output logic             init_o,
  // events
  input  logic             ev_buf_i,
  input  logic [15:0]      ev_valid_i,
  input  logic [15:0][7:0] ev_col_i,
  input  logic [15:0]      ev_dir_i,          // 1: +1, 0: -1
  // buffer control
  output logic [1:0]       free_o,
  input  logic             done_i,
  input  logic             done_buf_i,
  input  logic [8:0]       done_w_i,          // 1..256
  // coverage out
  output logic             cov_valid_o,
  input  logic             cov_ready_i,
  output logic [7:0]       cov_o,
  // v1.8: RAM read/write collision this cycle (DBG_STATUS.RAM_COLL, arch.md §9.1)
  output logic             ram_coll_o
);

  typedef enum logic [1:0] {B_FREE, B_FILL, B_WAIT, B_SWEEP} bstate_e;

  // ---------------------------------------------------------------- per-buffer state
  bstate_e [1:0] bs_q;
  logic [1:0][8:0] bw_q;
  logic [1:0][1:0] wait_q;      // cycles left until the last event write has landed

  // ---------------------------------------------------------------- init clear
  logic       init_q;
  logic [7:0] ix_q;
  assign init_o = init_q;

  // ---------------------------------------------------------------- sweep control
  logic       sw_act_q, sw_buf_q;
  logic       next_sw_q;          // buffer to sweep next (buffers fill alternately)
  logic [8:0] sx_q, sw_w_q;          // next column to read, width
  logic       s1_v_q, s1_last_q;     // read issued last cycle
  logic [7:0] s1_x_q;
  logic       sw_issue;
  logic [1:0] of_cnt;
  logic       of_pop;
  assign of_pop = cov_valid_o & cov_ready_i;
  assign sw_issue = sw_act_q && (sx_q < sw_w_q) && (2'(of_cnt) + 2'(s1_v_q) - 2'(of_pop) <= 2'd1);

  // ---------------------------------------------------------------- banks
  logic [1:0][15:0]      we, re;
  logic [1:0][15:0][7:0] waddr, raddr, wdata, rdata;

  // event pipeline per bank
  logic [15:0]      e1_v_q, e1_buf_q, e1_dir_q, e1_fwd_q;
  logic [15:0][7:0] e1_col_q, fwd_val_q, e1_new;

  always_comb begin
    for (int j = 0; j < 16; j++) begin
      logic [7:0] base;
      base      = e1_fwd_q[j] ? fwd_val_q[j] : rdata[e1_buf_q[j]][j];
      e1_new[j] = base + (e1_dir_q[j] ? 8'd1 : 8'hFF);
    end
  end

  always_comb begin
    we    = '0;
    re    = '0;
    waddr = '0;
    raddr = '0;
    wdata = '0;
    for (int b = 0; b < 2; b++) begin
      for (int j = 0; j < 16; j++) begin
        if (init_q) begin
          we[b][j]    = 1'b1;
          waddr[b][j] = ix_q;
        end else if (sw_act_q && sw_buf_q == 1'(b)) begin
          // sweep: read column sx, clear the column read the cycle before
          re[b][j]    = sw_issue;
          raddr[b][j] = sx_q[7:0];
          we[b][j]    = s1_v_q;
          waddr[b][j] = s1_x_q;
        end
        // events (never into the buffer being swept or cleared)
        if (ev_valid_i[j] && ev_buf_i == 1'(b) &&
            !(e1_v_q[j] && e1_buf_q[j] == 1'(b) && e1_col_q[j] == ev_col_i[j])) begin
          re[b][j]    = 1'b1;
          raddr[b][j] = ev_col_i[j];
        end
        if (e1_v_q[j] && e1_buf_q[j] == 1'(b)) begin
          we[b][j]    = 1'b1;
          waddr[b][j] = e1_col_q[j];
          wdata[b][j] = e1_new[j];
        end
      end
    end
  end

  logic [1:0][15:0] coll;
  assign ram_coll_o = |coll;
  for (genvar b = 0; b < 2; b++) begin : g_buf
    for (genvar j = 0; j < 16; j++) begin : g_bank
      g2d_ram_1r1w #(.WIDTH(8), .DEPTH(256), .BE_W(1)) u_ram (
        .clk_i,
        .we_i    (we[b][j]),
        .waddr_i (waddr[b][j]),
        .wdata_i (wdata[b][j]),
        .wbe_i   (1'b1),
        .re_i    (re[b][j]),
        .raddr_i (raddr[b][j]),
        .rdata_o (rdata[b][j]),
        .coll_o  (coll[b][j])
      );
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)     e1_v_q <= '0;
    else if (clr_i)  e1_v_q <= '0;
    else             e1_v_q <= ev_valid_i;
  end

  always_ff @(posedge clk_i) begin
    for (int j = 0; j < 16; j++) begin
      e1_buf_q[j] <= ev_buf_i;
      e1_col_q[j] <= ev_col_i[j];
      e1_dir_q[j] <= ev_dir_i[j];
      e1_fwd_q[j] <= e1_v_q[j] && e1_buf_q[j] == ev_buf_i && e1_col_q[j] == ev_col_i[j];
      fwd_val_q[j] <= e1_new[j];
    end
  end

  // ---------------------------------------------------------------- sweep datapath
  logic [15:0][7:0] wnd_q;
  logic [7:0]       cov_d;
  logic [15:0][7:0] wnd_d;
  logic [15:0]      in_s;          // sample inside (fill rule)
  logic [4:0]       n_in;

  always_comb begin
    n_in = '0;
    for (int j = 0; j < 16; j++) begin
      wnd_d[j]  = wnd_q[j] + rdata[sw_buf_q][j];
      in_s[j] = cfg_eo_i ? wnd_d[j][0] : (wnd_d[j] != '0);
      n_in      = n_in + 5'(in_s[j]);
    end
    if (cfg_aa_i) cov_d = 8'((13'(n_in) * 13'd255 + 13'd8) >> 4);
    else          cov_d = in_s[0] ? 8'hFF : 8'h00;
  end

  logic of_ready;
  g2d_fifo #(.WIDTH(8), .DEPTH(2)) u_of (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (s1_v_q),
    .in_ready_o  (of_ready),
    .in_data_i   (cov_d),
    .out_valid_o (cov_valid_o),
    .out_ready_i (cov_ready_i),
    .out_data_o  (cov_o),
    .count_o     (of_cnt)
  );

  // ---------------------------------------------------------------- control
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      init_q   <= 1'b1;
      ix_q     <= '0;
      bs_q     <= {B_FREE, B_FREE};
      sw_act_q <= 1'b0;
      s1_v_q   <= 1'b0;
      next_sw_q <= 1'b0;
    end else if (clr_i) begin
      init_q   <= 1'b1;
      ix_q     <= '0;
      bs_q     <= {B_FREE, B_FREE};
      sw_act_q <= 1'b0;
      s1_v_q   <= 1'b0;
      next_sw_q <= 1'b0;
    end else begin
      if (init_q) begin
        ix_q <= ix_q + 8'd1;
        if (ix_q == 8'hFF) init_q <= 1'b0;
      end
      // a buffer starts filling with its first event
      for (int b = 0; b < 2; b++) begin
        if (bs_q[b] == B_FREE && |ev_valid_i && ev_buf_i == 1'(b)) bs_q[b] <= B_FILL;
      end
      if (done_i) begin
        bs_q[done_buf_i]   <= B_WAIT;
        bw_q[done_buf_i]   <= done_w_i;
        wait_q[done_buf_i] <= 2'd2;
      end
      for (int b = 0; b < 2; b++) begin
        if (bs_q[b] == B_WAIT && !(done_i && done_buf_i == 1'(b))) begin
          if (wait_q[b] != '0) wait_q[b] <= wait_q[b] - 2'd1;
          else if (!sw_act_q && !s1_v_q && next_sw_q == 1'(b)) begin
            // sweeps run in fill order (the passes alternate between the buffers)
            bs_q[b]  <= B_SWEEP;
            sw_act_q <= 1'b1;
            sw_buf_q <= 1'(b);
            sx_q     <= '0;
            sw_w_q   <= bw_q[b];
            next_sw_q <= ~next_sw_q;
          end
        end
      end
      s1_v_q <= sw_issue;
      if (sw_issue) begin
        sx_q   <= sx_q + 9'd1;
        s1_x_q <= sx_q[7:0];
        s1_last_q <= (sx_q + 9'd1 == sw_w_q);
      end
      if (s1_v_q && s1_last_q) begin
        sw_act_q        <= 1'b0;
        bs_q[sw_buf_q]  <= B_FREE;
      end
    end
  end

  // running sums: reset at the start of every sweep
  always_ff @(posedge clk_i) begin
    if (sw_act_q && sx_q == '0 && !s1_v_q) wnd_q <= '0;
    else if (s1_v_q)                        wnd_q <= wnd_d;
  end

  assign free_o = {bs_q[1] == B_FREE, bs_q[0] == B_FREE};

  logic unused;
  assign unused = of_ready;   // the issue condition keeps room in the output FIFO

`ifndef SYNTHESIS
  // not at time 0: before the asynchronous reset the registers hold random initial values
  always_ff @(posedge clk_i) begin
    if ($time > 0 && !init_q && |ev_valid_i && bs_q[ev_buf_i] inside {B_WAIT, B_SWEEP})
      $error("g2d_wind_acc: event into a buffer that is waiting or being swept");
  end
`endif

endmodule
