// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Source texel cache (arch.md §6.4), latency-hiding (Phase 3 decision Q1 a). Read-only, 64-byte lines,
// CACHE_BYTES total in 4 banks x SETS sets x 4 ways, LRU replacement among unlocked ways, invalidated per
// draw (inv_i, only while idle). One pixel (up to four texels) per clock.
//
// Banks: a texel's bank is {its row parity, line-address parity}; the set is a hash of the line address. The texels of a bilinear quad are
// (x0,y0), (x1,y0), (x0,y1), (x1,y1) with x1 - x0, y1 - y0 in {0, 1}, so two texels in the same bank are
// always in the same line, and each bank serves at most one line per pixel (one SRAM read per bank).
// v1.4 wrap modes can break that at a wrap seam; g2d_tex_split then issues the quad as single-texel requests
// (in_smask_i: the texels this request reads; out_tex_o of the others is don't-care), so the rule holds for
// every request that reaches this cache.
// The same memory line can be cached in two banks (rows of different parity); it is read-only, so that
// is harmless.
//
// Pipeline:
//   T0  line addresses, banks, offsets of the four texels; per bank the line it needs
//   T1  tag lookup per needed bank. Miss: allocate the LRU way among ways with lock = 0 (no pixel in
//       flight refers to it), mark it pending and queue a fill. Every referenced way's lock is
//       incremented. Stalls when a bank has no free way or its miss queue is full.
//   FIFO PIX_DEPTH pixels (SRAM), so lookups run a memory latency ahead of the data reads (256: Phase 4
//        Q-PERF-2 measurement, 128 left the rotated blit at 0.86 px/clk with 90-clock latency).
//   D0  the head pixel is popped when all its lines are filled (or stop_i): one line read per bank, the
//       locks are decremented.
//   D1  texel extraction (32 bits from the texel's byte offset; the texel unit uses the low bytes).
// Fills: per-bank miss queues -> round-robin -> read client (64-byte request) -> beats written into the
// way's line (one SRAM lane per beat) -> pending cleared after the last beat. The read client accepts
// every beat (rsp_ready_o = 1), so fills never depend on the pixel pipeline and the stalls above cannot
// deadlock: every locked line is either filled or has a fill in flight.
// stop_i (soft reset): no new fill request; the data stage stops waiting for fills (their requests may
// have been dropped) and passes don't-care texels on without reading the SRAM (beats of issued fills may
// still be written), so the pipeline drains; clr_i then clears all.

module g2d_src_cache #(
  parameter int unsigned DATA_W      = 64,
  parameter int unsigned TAG_W       = 8,
  parameter int unsigned CACHE_BYTES = 8192,
  parameter int unsigned PIX_DEPTH   = 256,
  parameter int unsigned TRK_DEPTH   = 16,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  input  logic              inv_i,
  // in (from g2d_tex_addr)
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_pass_i,
  input  logic [3:0]        in_smask_i,
  input  logic [3:0][31:0]  in_addr_i,
  input  logic [1:0]        in_ypar_i,
  input  logic [7:0]        in_fx_i,
  input  logic [7:0]        in_fy_i,
  input  logic [TAG_W-1:0]  in_tag_i,
  // out (to g2d_texel)
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_pass_o,
  output logic [3:0][31:0]  out_tex_o,
  output logic [7:0]        out_fx_o,
  output logic [7:0]        out_fy_o,
  output logic [TAG_W-1:0]  out_tag_o,
  // read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  // status
  output logic              idle_o,
  output logic [2:0]        miss_o,         // line misses this cycle (PERF_SRC_CACHE_MISS)
  // v1.8: RAM read/write collision this cycle (DBG_STATUS.RAM_COLL, arch.md §9.1)
  output logic              ram_coll_o
);

  localparam int unsigned NB     = 4;                          // banks
  localparam int unsigned NW     = 4;                          // ways
  localparam int unsigned SETS   = CACHE_BYTES / (NB * NW * 64);
  localparam int unsigned SB     = $clog2(SETS);               // set index bits
  localparam int unsigned TB     = 25;                         // tag: line address without the bank bit
  localparam int unsigned LANES  = 64 / DB;                    // beats per line
  localparam int unsigned LB     = $clog2(LANES);
  localparam int unsigned LOCK_W = $clog2(PIX_DEPTH + 16) + 1;
  localparam int unsigned LINES  = SETS * NW;                  // lines per bank

  // ================================================================ T0
  logic             t0_v, t1_v, t0_move, t1_fire;
  logic             t0_pass;
  logic [3:0]       t0_smask;
  logic [3:0][25:0] t0_line;
  logic [3:0][5:0]  t0_off;
  logic [3:0][1:0]  t0_bank;
  logic [7:0]       t0_fx, t0_fy;
  logic [TAG_W-1:0] t0_tag;

  assign t0_move    = t0_v & (~t1_v | t1_fire);
  assign in_ready_o = ~t0_v | t0_move;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                 t0_v <= 1'b0;
    else if (clr_i)              t0_v <= 1'b0;
    else if (in_ready_o)         t0_v <= in_valid_i;
  end

  always_ff @(posedge clk_i) begin
    if (in_ready_o && in_valid_i) begin
      t0_pass  <= in_pass_i;
      t0_smask <= in_smask_i;
      for (int s = 0; s < 4; s++) begin
        t0_line[s] <= in_addr_i[s][31:6];
        t0_off[s]  <= in_addr_i[s][5:0];
        t0_bank[s] <= {in_ypar_i[s / 2], in_addr_i[s][6]};
      end
      t0_fx  <= in_fx_i;
      t0_fy  <= in_fy_i;
      t0_tag <= in_tag_i;
    end
  end

  // per bank: needed? which line? (lowest slot mapping to the bank)
  logic [NB-1:0]       t0_need;
  logic [NB-1:0][25:0] t0_bline;
  always_comb begin
    t0_need  = '0;
    t0_bline = '0;
    for (int s = 3; s >= 0; s--) begin
      if (t0_pass && t0_smask[s]) begin
        t0_need[t0_bank[s]]  = 1'b1;
        t0_bline[t0_bank[s]] = t0_line[s];
      end
    end
  end

  // ================================================================ T1 registers
  logic                    t1_pass;
  logic [NB-1:0]           t1_need;
  logic [NB-1:0][25:0]     t1_bline;
  logic [3:0][5:0]         t1_off;
  logic [3:0][1:0]         t1_bank;
  logic [7:0]              t1_fx, t1_fy;
  logic [TAG_W-1:0]        t1_tag;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                     t1_v <= 1'b0;
    else if (clr_i)                  t1_v <= 1'b0;
    else if (~t1_v | t1_fire)        t1_v <= t0_v;
  end

  always_ff @(posedge clk_i) begin
    if (t0_move) begin
      t1_pass  <= t0_pass;
      t1_need  <= t0_need;
      t1_bline <= t0_bline;
      t1_off   <= t0_off;
      t1_bank  <= t0_bank;
      t1_fx    <= t0_fx;
      t1_fy    <= t0_fy;
      t1_tag   <= t0_tag;
    end
  end

  // ================================================================ tag state (flops)
  logic [NB-1:0][SETS-1:0][NW-1:0]             val_q, pend_q;
  logic [NB-1:0][SETS-1:0][NW-1:0][TB-1:0]     tag_q;
  logic [NB-1:0][SETS-1:0][NW-1:0][LOCK_W-1:0] lock_q;
  logic [NB-1:0][SETS-1:0][NW-1:0][1:0]        age_q;     // 0 = most recently used

  // ================================================================ T1 lookup
  // Set index: XOR fold of the whole line address (without the bank bit). With power-of-two strides the
  // low line-address bits repeat from row to row, so a direct index maps a column of a rotated tile walk
  // onto one set; the fold spreads consecutive rows over the sets.
  function automatic logic [SB-1:0] set_hash(logic [24:0] l);
    logic [SB-1:0] h;
    h = '0;
    for (int i = 0; i < 25; i += SB) begin
      for (int j = 0; j < int'(SB); j++) begin
        if (i + j < 25) h[j] = h[j] ^ l[i + j];
      end
    end
    return h;
  endfunction

  // Under stop_i no lookups happen: pixels pass with no line references (nothing locked or queued).
  logic [NB-1:0] t1_nd;
  assign t1_nd = t1_need & {NB{~stop_i}};

  logic [NB-1:0][SB-1:0] t1_set;
  logic [NB-1:0][TB-1:0] t1_tagv;
  logic [NB-1:0]         t1_hit, t1_vfound;
  logic [NB-1:0][1:0]    t1_hway, t1_vway, t1_way;
  logic [NB-1:0]         mq_ready;
  logic                  pf_ready;
  logic                  t1_stall;

  always_comb begin
    for (int b = 0; b < NB; b++) begin
      logic [1:0] best_age;
      t1_set[b]  = set_hash(t1_bline[b][25:1]);
      t1_tagv[b] = t1_bline[b][25:1];
      t1_hit[b]  = 1'b0;
      t1_hway[b] = '0;
      for (int w = NW - 1; w >= 0; w--) begin
        if (val_q[b][t1_set[b]][w] && tag_q[b][t1_set[b]][w] == t1_tagv[b]) begin
          t1_hit[b]  = 1'b1;
          t1_hway[b] = 2'(w);
        end
      end
      // victim: an invalid way first, else the least recently used way with lock = 0
      t1_vfound[b] = 1'b0;
      t1_vway[b]   = '0;
      best_age     = '0;
      for (int w = NW - 1; w >= 0; w--) begin
        if (!val_q[b][t1_set[b]][w]) begin
          t1_vfound[b] = 1'b1;
          t1_vway[b]   = 2'(w);
        end
      end
      if (!t1_vfound[b]) begin
        for (int w = 0; w < NW; w++) begin
          if (lock_q[b][t1_set[b]][w] == '0 && (!t1_vfound[b] || age_q[b][t1_set[b]][w] > best_age)) begin
            t1_vfound[b] = 1'b1;
            t1_vway[b]   = 2'(w);
            best_age     = age_q[b][t1_set[b]][w];
          end
        end
      end
      t1_way[b] = t1_hit[b] ? t1_hway[b] : t1_vway[b];
    end
  end

  logic [NB-1:0] t1_miss;
  always_comb begin
    t1_stall = ~pf_ready;
    for (int b = 0; b < NB; b++) begin
      t1_miss[b] = t1_nd[b] & ~t1_hit[b];
      if (t1_miss[b] && (!t1_vfound[b] || !mq_ready[b])) t1_stall = 1'b1;
    end
  end
  assign t1_fire = t1_v & ~t1_stall;

  // ================================================================ pixel FIFO
  typedef struct packed {
    logic                  pass;
    logic [7:0]            fx;
    logic [7:0]            fy;
    logic [TAG_W-1:0]      tag;
    logic [NB-1:0]         need;
    logic [NB-1:0][SB-1:0] set;
    logic [NB-1:0][1:0]    way;
    logic [3:0][1:0]       bank;
    logic [3:0][5:0]       off;
  } pix_t;

  pix_t pf_in, pf_out;
  logic pf_valid, pf_pop;

  assign pf_in = '{pass: t1_pass, fx: t1_fx, fy: t1_fy, tag: t1_tag, need: t1_nd, set: t1_set,
                   way: t1_way, bank: t1_bank, off: t1_off};

  g2d_fifo_ram #(.WIDTH($bits(pix_t)), .DEPTH(PIX_DEPTH)) u_pix (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (t1_fire),
    .in_ready_o  (pf_ready),
    .in_data_i   (pf_in),
    .out_valid_o (pf_valid),
    .out_ready_i (pf_pop),
    .out_data_o  (pf_out)
  );

  // ================================================================ miss queues and fills
  typedef struct packed {
    logic [25:0]   line;
    logic [SB-1:0] set;
    logic [1:0]    way;
  } miss_t;

  typedef struct packed {
    logic [1:0]    bank;
    logic [SB-1:0] set;
    logic [1:0]    way;
  } trk_t;

  miss_t [NB-1:0] mq_out;
  logic  [NB-1:0] mq_valid, mq_pop;
  logic           arb_valid, req_fire;
  logic [1:0]     arb_idx;
  logic           trk_ready, trk_valid, fill_last;
  trk_t           trk_out;

  for (genvar b = 0; b < NB; b++) begin : g_mq
    logic [2:0] unused_cnt;
    g2d_fifo #(.WIDTH($bits(miss_t)), .DEPTH(4)) u_mq (
      .clk_i,
      .rst_ni,
      .clr_i,
      .in_valid_i  (t1_fire & t1_miss[b]),
      .in_ready_o  (mq_ready[b]),
      .in_data_i   (miss_t'{line: t1_bline[b], set: t1_set[b], way: t1_vway[b]}),
      .out_valid_o (mq_valid[b]),
      .out_ready_i (mq_pop[b]),
      .out_data_o  (mq_out[b]),
      .count_o     (unused_cnt)
    );
  end

  g2d_rr_arb #(.N(NB)) u_arb (
    .clk_i,
    .rst_ni,
    .clr_i,
    .req_i   (mq_valid & {NB{~stop_i}}),
    .ack_i   (req_fire),
    .valid_o (arb_valid),
    .idx_o   (arb_idx)
  );

  assign rd_req_valid_o = arb_valid & trk_ready & ~stop_i;
  assign rd_req_addr_o  = {mq_out[arb_idx].line, 6'b0};
  assign rd_req_len_o   = 16'd63;
  assign req_fire       = rd_req_valid_o & rd_req_ready_i;
  always_comb begin
    mq_pop = '0;
    mq_pop[arb_idx] = req_fire;
  end

  logic [$clog2(TRK_DEPTH + 1)-1:0] trk_cnt;
  g2d_fifo #(.WIDTH($bits(trk_t)), .DEPTH(TRK_DEPTH)) u_trk (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (req_fire),
    .in_ready_o  (trk_ready),
    .in_data_i   (trk_t'{bank: arb_idx, set: mq_out[arb_idx].set, way: mq_out[arb_idx].way}),
    .out_valid_o (trk_valid),
    .out_ready_i (fill_last),
    .out_data_o  (trk_out),
    .count_o     (trk_cnt)
  );

  logic [LB-1:0] beat_q;
  logic          fill_we;
  assign rd_rsp_ready_o = 1'b1;
  assign fill_we        = rd_rsp_valid_i & trk_valid;
  assign fill_last      = fill_we & (beat_q == LB'(LANES - 1));

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)       beat_q <= '0;
    else if (clr_i)    beat_q <= '0;
    else if (fill_we)  beat_q <= beat_q + LB'(1);
  end

  // ================================================================ data stage
  logic                 d_en, da_v, db_v, d_issue, head_ok;
  pix_t                 da;
  logic [NB-1:0][511:0] rdata;

  always_comb begin
    head_ok = pf_valid;
    for (int b = 0; b < NB; b++) begin
      if (pf_out.need[b] && pend_q[b][pf_out.set[b]][pf_out.way[b]] && !stop_i) head_ok = 1'b0;
    end
  end

  assign d_en    = ~db_v | out_ready_i;
  assign d_issue = d_en & head_ok;
  assign pf_pop  = d_issue;

  logic [NB-1:0] data_coll;
  assign ram_coll_o = |data_coll;
  for (genvar b = 0; b < NB; b++) begin : g_bank
    g2d_ram_1r1w #(.WIDTH(512), .DEPTH(LINES), .BE_W(LANES)) u_data (
      .clk_i,
      .we_i    (fill_we && trk_out.bank == 2'(b)),
      .waddr_i ({trk_out.set, trk_out.way}),
      .wdata_i ({LANES{rd_rsp_data_i}}),
      .wbe_i   (LANES'(1) << beat_q),
      .re_i    (d_issue & pf_out.need[b] & ~stop_i),   // under stop: no read (fills may be landing)
      .raddr_i ({pf_out.set[b], pf_out.way[b]}),
      .rdata_o (rdata[b]),
      .coll_o  (data_coll[b])
    );
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      da_v <= 1'b0;
      db_v <= 1'b0;
    end else if (clr_i) begin
      da_v <= 1'b0;
      db_v <= 1'b0;
    end else if (d_en) begin
      da_v <= d_issue;
      db_v <= da_v;
    end
  end

  // texel extraction: 32 bits from the texel's byte offset (zero beyond the line end)
  logic [3:0][543:0] ext;
  always_comb begin
    for (int s = 0; s < 4; s++) ext[s] = {32'd0, rdata[da.bank[s]]} >> {da.off[s], 3'b000};
  end

  always_ff @(posedge clk_i) begin
    if (d_issue) da <= pf_out;
    if (d_en) begin
      for (int s = 0; s < 4; s++) out_tex_o[s] <= ext[s][31:0];
      out_pass_o <= da.pass;
      out_fx_o   <= da.fx;
      out_fy_o   <= da.fy;
      out_tag_o  <= da.tag;
    end
  end
  assign out_valid_o = db_v;

  // ================================================================ tag state update
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      val_q  <= '0;
      pend_q <= '0;
      lock_q <= '0;
      age_q  <= '0;
    end else if (clr_i) begin
      val_q  <= '0;
      pend_q <= '0;
      lock_q <= '0;
      age_q  <= '0;
    end else begin
      if (inv_i) val_q <= '0;
      for (int b = 0; b < NB; b++) begin
        for (int s = 0; s < SETS; s++) begin
          for (int w = 0; w < NW; w++) begin
            logic inc, dec;
            inc = t1_fire && t1_nd[b] && t1_set[b] == SB'(s) && t1_way[b] == 2'(w);
            dec = d_issue && pf_out.need[b] && pf_out.set[b] == SB'(s) && pf_out.way[b] == 2'(w);
            lock_q[b][s][w] <= lock_q[b][s][w] + LOCK_W'(inc) - LOCK_W'(dec);
            if (inc && t1_miss[b]) begin
              val_q[b][s][w]  <= 1'b1;
              pend_q[b][s][w] <= 1'b1;
              tag_q[b][s][w]  <= t1_tagv[b];
            end
            if (fill_last && trk_out.bank == 2'(b) && trk_out.set == SB'(s) && trk_out.way == 2'(w)) begin
              pend_q[b][s][w] <= 1'b0;
            end
            // LRU (age counters, 0 = most recent): the accessed way becomes 0; the ways that were more
            // recent than it (on a miss: all ways) age by one, saturating at 3
            if (t1_fire && t1_nd[b] && t1_set[b] == SB'(s)) begin
              if (w == int'(t1_way[b])) begin
                age_q[b][s][w] <= '0;
              end else if (t1_miss[b] || age_q[b][s][w] < age_q[b][s][t1_way[b]]) begin
                age_q[b][s][w] <= (age_q[b][s][w] == 2'd3) ? 2'd3 : age_q[b][s][w] + 2'd1;
              end
            end
          end
        end
      end
    end
  end

  // ================================================================ status
  logic [$clog2(PIX_DEPTH + 8)-1:0] pix_cnt_q;   // pixels in the FIFO
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)      pix_cnt_q <= '0;
    else if (clr_i)   pix_cnt_q <= '0;
    else              pix_cnt_q <= pix_cnt_q + ($bits(pix_cnt_q))'(t1_fire) - ($bits(pix_cnt_q))'(pf_pop);
  end

  // Under stop_i, queued and outstanding fills do not count: their requests are not issued or may have
  // been dropped by the AXI master (beats of issued bursts still arrive and are written harmlessly).
  assign idle_o = ~t0_v & ~t1_v & (pix_cnt_q == '0) & ~da_v & ~db_v & (stop_i | (~trk_valid & ~|mq_valid));
  assign miss_o = t1_fire ? 3'(t1_miss[0]) + 3'(t1_miss[1]) + 3'(t1_miss[2]) + 3'(t1_miss[3]) : 3'd0;

  logic unused;
  always_comb begin
    unused = ^{trk_cnt, da.need, da.set, da.way};
    for (int k = 0; k < 4; k++) unused ^= ^ext[k][543:32];
  end

`ifndef SYNTHESIS
  // Simulation-only stall-cause counters (Phase 4 A9, docs/OPEN_QUESTIONS.md Q-PERF-2), read white-box
  // by the throughput tests; cleared by clr_i. Not part of the design.
  int unsigned sim_cyc_q, sim_novictim_q, sim_mqfull_q, sim_pffull_q, sim_headwait_q, sim_outstall_q, sim_lookups_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sim_cyc_q <= 0; sim_novictim_q <= 0; sim_mqfull_q <= 0; sim_pffull_q <= 0;
      sim_headwait_q <= 0; sim_outstall_q <= 0; sim_lookups_q <= 0;
    end else if (clr_i) begin
      sim_cyc_q <= 0; sim_novictim_q <= 0; sim_mqfull_q <= 0; sim_pffull_q <= 0;
      sim_headwait_q <= 0; sim_outstall_q <= 0; sim_lookups_q <= 0;
    end else begin
      sim_cyc_q <= sim_cyc_q + 1;
      if (t1_fire) sim_lookups_q <= sim_lookups_q + 1;
      if (t1_v && !pf_ready) sim_pffull_q <= sim_pffull_q + 1;
      else if (t1_v && t1_stall) begin
        logic nv;
        nv = 1'b0;
        for (int b = 0; b < NB; b++) if (t1_miss[b] && !t1_vfound[b]) nv = 1'b1;
        if (nv) sim_novictim_q <= sim_novictim_q + 1;
        else    sim_mqfull_q   <= sim_mqfull_q + 1;
      end
      if (pf_valid && !head_ok) sim_headwait_q <= sim_headwait_q + 1;
      if (db_v && !out_ready_i) sim_outstall_q <= sim_outstall_q + 1;
    end
  end

  // two requested texels in one bank must be in the same line (see the header); armed after reset and not at time 0
  // (before the reset, the registers, chk_q included, hold random initial values)
  logic chk_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) chk_q <= 1'b0;
    else         chk_q <= 1'b1;
  end
  always_ff @(posedge clk_i) begin
    if ($time > 0 && chk_q && t0_v && t0_pass) begin
      for (int s = 0; s < 4; s++) begin
        for (int r = 0; r < 4; r++) begin
          if (t0_smask[s] && t0_smask[r] && t0_bank[s] == t0_bank[r] && t0_line[s] != t0_line[r]) begin
            $error("g2d_src_cache: texels %0d and %0d share bank %0d with different lines", s, r, t0_bank[s]);
          end
        end
      end
    end
  end
`endif

endmodule
