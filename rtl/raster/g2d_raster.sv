// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Path rasterizer (rendering.md §8; arch.md §6.3 stage 2). Produces the coverage of every pixel of the
// clipped draw rectangle [rx0, rx1) x [ry0, ry1) in scanline order, one per clock (cov_valid_o/ready_i).
// Same structure as the golden model's PathRaster (model/src/raster.cpp):
//
//   LOAD   g2d_edge_load streams the kept edges into the edge buffer (constants, SRAM, MAX_EDGES entries).
//   BAND   for every band of BAND_ROWS pixel rows: scan the buffer, list the edges overlapping the band
//          (active list, SRAM) and compute each one's crossing state at the band's first sample row Y0:
//          N0 = Xa*dy + (Y0 - Ya)*dx, xt = ceil(N0/dy) = -floor(-N0/dy), r = xt*dy - N0 (g2d_sdiv),
//          into the state SRAM.
//   ROWS   for every pixel row and every segment of 256 px: one pass over the active list. Each edge
//          enters a 16-stage step pipeline (stage j = sample row j): event p = (xt - xoff_j + 31) >> 5 when
//          Ya <= Y_j < Yb, column 0 if p <= segment start (base winding), dropped if p >= segment end;
//          then xt += qs + (rs > r), r += (rs > r ? dy : 0) - rs. Events go to g2d_wind_acc (passes
//          alternate between its two buffers); an end marker behind the last edge closes the pass. On the
//          row's last segment the stored state takes one row step (qr, rr).
//   Sweeps of g2d_wind_acc give the coverage.
// Band-setup overlap (v1.7, Q-PERF-3 / K-1): the active list and the state RAM are kept twice (parity cur_q for
// the rows, ~cur_q for the band being set up; cur_q flips when the rows of a set-up band start). The first band
// is set up while the list loads (each kept edge enters the setup pipeline from the loader as it is written), and
// without streaming the next band's scan runs during the current band's rows, reading the edge buffer in the
// cycles the passes leave free. Streaming and one-row bands keep the sequential order (their buffer is reloaded).
// Without AA there is one sample row (j = 0, Y = 32*py + 16, xoff = 16, S = 32).
//
// Long lists (Phase 4 Q1 a): one pass handles at most CHUNK edges (CHUNK <= MAX_EDGES, default equal).
//   - more than CHUNK kept edges in the list: streaming. For every band the loader re-reads the list from
//     memory with its row filter set to the band, so the buffer receives exactly the band's active edges
//     (BLOAD);
//   - more than CHUNK active edges in a band: that band is processed one pixel row at a time (each row is
//     a band of one row: the start division runs at the row start), and every segment of the row is
//     accumulated in several passes: pass c reloads the list and keeps the row's active edges number
//     c*CHUNK .. c*CHUNK + CHUNK - 1 (list order); all passes add into the same accumulator buffer and
//     only the last one closes it. The winding sums are mod-256 additions, so the result does not depend
//     on the split (CHUNK < MAX_EDGES forces smaller passes for testing).
// The arithmetic (division, stepping, sweep) is the same in every mode.
// stop_i (soft reset): no new pass or band, the pass being issued ends; idle_o ignores the contents.
// clr_i clears everything.

module g2d_raster #(
  parameter int unsigned DATA_W    = 64,
  parameter int unsigned MAX_EDGES = 512,
  parameter int unsigned CHUNK     = MAX_EDGES,
  localparam int unsigned EI = $clog2(MAX_EDGES)
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // draw
  input  logic              start_i,
  input  logic [31:0]       cfg_addr_i,
  input  logic [23:0]       cfg_count_i,      // >= 1
  input  logic              cfg_aa_i,
  input  logic              cfg_eo_i,
  input  logic [13:0]       cfg_rx0_i,        // clipped rectangle, non-empty
  input  logic [13:0]       cfg_ry0_i,
  input  logic [13:0]       cfg_rx1_i,
  input  logic [13:0]       cfg_ry1_i,
  // edge read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  // coverage
  output logic              cov_valid_o,
  input  logic              cov_ready_i,
  output logic [7:0]        cov_o,
  // status
  output logic              busy_o,
  output logic              idle_o,
  // v1.8: RAM read/write collision this cycle (DBG_STATUS.RAM_COLL bits 3:0, arch.md §9.1):
  // [0] edge buffer, [1] state buffers, [2] active list, [3] winding accumulator
  output logic [3:0]        ram_coll_o
);

  localparam int unsigned BAND_ROWS = 16;

  // per-edge constants in the edge buffer
  typedef struct packed {
    logic signed [20:0] xa;
    logic signed [20:0] ya;
    logic signed [20:0] yb;
    logic signed [20:0] dx;
    logic [20:0]        dy;
    logic               dir;
    logic signed [26:0] qs;
    logic [20:0]        rs;
    logic signed [26:0] qr;
    logic [20:0]        rr;
  } edge_t;

  typedef struct packed {
    logic signed [30:0] xt;
    logic [20:0]        r;
  } st_t;

  typedef enum logic [2:0] {S_IDLE, S_INIT, S_LOAD, S_BLOAD, S_BAND, S_BWAIT, S_ROWS, S_END} state_e;
  state_e st_q;

  // ---------------------------------------------------------------- edge loader
  logic        ld_start, ld_valid, ld_busy, ld_done, ld_idle;
  logic [13:0] ld_ry0, ld_ry1;      // loader row filter: the draw rows, or the band rows when streaming
  logic        strm_q;              // streaming mode (more than MAX_EDGES kept edges)
  logic [24:0] cnt_q;               // kept edges seen in the current load (may exceed MAX_EDGES)
  logic        bload_go_q;          // start pulse for a band load
  logic        mrow_q;              // multi-pass rows: one-row bands, several passes per segment
  logic [13:0] mrow_end_q;          // first row after the overflowing band (normal bands resume there)
  logic [24:0] cbase_q;             // first active edge of the current pass (chunk * CHUNK)
  logic        last_chunk_q;        // the current pass is the segment's last
  edge_t       ld_edge;

  g2d_edge_load #(.DATA_W(DATA_W)) u_load (
    .clk_i,
    .rst_ni,
    .clr_i,
    .stop_i,
    .start_i        (ld_start),
    .cfg_addr_i,
    .cfg_count_i,
    .cfg_aa_i,
    .cfg_rx1_i,
    .cfg_ry0_i      (ld_ry0),
    .cfg_ry1_i      (ld_ry1),
    .rd_req_valid_o,
    .rd_req_ready_i,
    .rd_req_addr_o,
    .rd_req_len_o,
    .rd_rsp_valid_i,
    .rd_rsp_ready_o,
    .rd_rsp_data_i,
    .out_valid_o    (ld_valid),
    .out_ready_i    (1'b1),
    .out_xa_o       (ld_edge.xa),
    .out_ya_o       (ld_edge.ya),
    .out_yb_o       (ld_edge.yb),
    .out_dx_o       (ld_edge.dx),
    .out_dy_o       (ld_edge.dy),
    .out_dir_o      (ld_edge.dir),
    .out_qs_o       (ld_edge.qs),
    .out_rs_o       (ld_edge.rs),
    .out_qr_o       (ld_edge.qr),
    .out_rr_o       (ld_edge.rr),
    .busy_o         (ld_busy),
    .done_o         (ld_done),
    .idle_o         (ld_idle)
  );

  // ---------------------------------------------------------------- memories
  logic          eb_we, eb_re, ab_we, ab_re, sb_re;
  logic [1:0]    sb_we;
  logic [EI-1:0] eb_waddr, eb_raddr, sb_raddr, ab_wdata, ab_rdata;
  logic [1:0][EI-1:0] sb_waddr;
  logic [EI:0]   ab_waddr, ab_raddr;    // {parity, position}
  edge_t         eb_wdata, eb_rdata;
  st_t [1:0]     sb_wdata, sb_rdata_p;
  st_t           sb_rdata;

  logic          coll_eb, coll_ab, coll_acc;
  logic [1:0]    coll_sb;
  g2d_ram_1r1w #(.WIDTH($bits(edge_t)), .DEPTH(MAX_EDGES)) u_ebuf (
    .clk_i, .we_i(eb_we), .waddr_i(eb_waddr), .wdata_i(eb_wdata), .wbe_i(1'b1),
    .re_i(eb_re), .raddr_i(eb_raddr), .rdata_o(eb_rdata), .coll_o(coll_eb));
  // state per parity, two RAMs: band-setup divisions (~cur_q) and row write-backs (the pass's parity) never share
  // a write port
  for (genvar b = 0; b < 2; b++) begin : g_sbuf
    g2d_ram_1r1w #(.WIDTH($bits(st_t)), .DEPTH(MAX_EDGES)) u_sbuf (
      .clk_i, .we_i(sb_we[b]), .waddr_i(sb_waddr[b]), .wdata_i(sb_wdata[b]), .wbe_i(1'b1),
      .re_i(sb_re && pass_par_q == 1'(b)), .raddr_i(sb_raddr), .rdata_o(sb_rdata_p[b]),   // only the pass's parity
      .coll_o(coll_sb[b]));
  end
  assign sb_rdata = sb_rdata_p[pass_par_q];
  g2d_ram_1r1w #(.WIDTH(EI), .DEPTH(2 * MAX_EDGES)) u_abuf (
    .clk_i, .we_i(ab_we), .waddr_i(ab_waddr), .wdata_i(ab_wdata), .wbe_i(1'b1),
    .re_i(ab_re), .raddr_i(ab_raddr), .rdata_o(ab_rdata), .coll_o(coll_ab));
  assign ram_coll_o = {coll_acc, coll_ab, |coll_sb, coll_eb};

  // ---------------------------------------------------------------- draw registers
  logic          aa_q, eo_q;
  logic [13:0]   rx0_q, rx1_q, ry0_q, ry1_q;
  logic [EI:0]   n_q;            // kept edges
  logic [1:0][EI:0] na_q;        // active edges of the band, per parity
  logic          cur_q;          // parity of the rows' active list / state (setup writes ~cur_q)
  // the other parity as a 1-bit signal for indexing: sv2v turns `na_q[~cur_q]` into `~cur_q * (EI + 1)`, where
  // Verilog widens cur_q to 32 bits before the NOT, so the ASIC netlist read out of range and lost every path
  // (v2.1, docs/ASIC.md defect 0; dv/tests/test_sv2v_equiv.py)
  logic          nxt_par;
  assign nxt_par = ~cur_q;
  logic          pass_par_q;     // parity of the pass in flight
  logic [13:0]   bs_lo_q, bs_hi_q;   // rows of the band being set up
  logic [13:0]   b_end_q;        // first row after the band
  logic [13:0]   py_q;           // current pixel row
  logic [13:0]   sx0_q;          // current segment start

  // ---------------------------------------------------------------- band setup
  logic          bs_scan_q;      // scanning
  logic [EI:0]   bs_i_q;         // next index to read
  logic          bs_v1_q;        // const read issued last cycle
  logic [EI-1:0] bs_i1_q;
  logic          bs_mv_q;        // multiply stage valid
  logic signed [43:0] bs_n0_q;
  logic [20:0]   bs_dy_q;
  logic [EI-1:0] bs_im_q;
  logic [6:0]    bs_inflight_q;  // divisions in flight
  logic          bs_ld_q;        // the setup stage-1 edge comes from the loader (first band during the load)
  edge_t         ld_e1_q;        // loader edge of the previous cycle
  edge_t         bs_e;           // setup stage-1 edge
  logic          otf_in;         // a kept edge enters the setup now (first band during the load)

  logic signed [21:0] y0_band, ylo_band, yhi_band;
  assign ylo_band = $signed({3'b0, bs_lo_q, 5'b0});
  assign yhi_band = $signed({3'b0, bs_hi_q, 5'b0});
  assign y0_band  = ylo_band + (aa_q ? 22'sd1 : 22'sd16);

  logic bs_active, bs_rd;
  // never past the kept edges; the passes own the edge-buffer read port when they need it
  assign bs_rd = bs_scan_q && (bs_i_q < n_q) && !ps_v1_q;
  assign otf_in = (st_q == S_LOAD) && eb_we;
  assign bs_e = bs_ld_q ? ld_e1_q : eb_rdata;
  assign bs_active = (22'(bs_e.ya) < yhi_band) && (22'(bs_e.yb) > ylo_band);

  logic              dv_out_valid, dv_ovf, dv_ready_unused;
  logic signed [31:0] dv_q;
  logic [20:0]       dv_r;
  logic [EI-1:0]     dv_tag;

  g2d_sdiv #(.NW(44), .DW(21), .QW(31), .TAG_W(EI)) u_div (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (bs_mv_q),
    .in_ready_o  (dv_ready_unused),
    .in_n_i      (-bs_n0_q),
    .in_d_i      (bs_dy_q),
    .in_tag_i    (bs_im_q),
    .out_valid_o (dv_out_valid),
    .out_ready_i (1'b1),
    .out_q_o     (dv_q),
    .out_r_o     (dv_r),
    .out_ovf_o   (dv_ovf),
    .out_tag_o   (dv_tag)
  );

  // ---------------------------------------------------------------- row passes
  logic          ps_act_q;       // a pass is issuing
  logic          ps_buf_q;       // its accumulator buffer
  logic [EI:0]   ps_k_q;         // next active-list position
  logic          ps_last_seg_q;  // the pass is the row's last segment
  logic          ps_v1_q, ps_v2_q;
  logic          ps_mark1_q, ps_mark2_q;   // end marker riding behind the last edge
  logic [EI-1:0] ps_idx2_q;
  logic          ps_ls1_q, ps_ls2_q;
  logic [1:0]    wb_pend_q;      // state write-backs in flight (row boundary hazard)
  logic [1:0]    free;
  logic          init;
  logic [13:0]   seg_end;
  assign seg_end = ((15'(sx0_q) + 15'd256) < 15'(rx1_q)) ? sx0_q + 14'd256 : rx1_q;

  // stage-0 entry of the step pipeline (cycle after the const/state read)
  logic          p0_v, p0_mark;
  edge_t         p0_e;
  st_t           p0_s;

  // ---------------------------------------------------------------- step pipeline
  typedef struct packed {
    logic [13:0]        py;
    logic [13:0]        sx0;
    logic [13:0]        sx1;
    logic signed [30:0] xt;
    logic [20:0]        r;
    logic signed [20:0] ya;
    logic signed [20:0] yb;
    logic [20:0]        dy;
    logic               dir;
    logic signed [26:0] qs;
    logic [20:0]        rs;
  } stage_t;

  stage_t [15:0] sp_q;           // stage data (no reset; qualified by sv_q)
  logic   [15:0] sv_q, sm_q;     // stage holds an edge / the pass end marker
  stage_t        sp_in;
  logic          sp_in_v, sp_in_m;
  logic [3:0][3:0] unused_q;

  localparam logic [15:0][3:0] SAMPLE_Q = {4'd11, 4'd3, 4'd7, 4'd14, 4'd0, 4'd9, 4'd5, 4'd13,
                                           4'd2, 4'd10, 4'd6, 4'd15, 4'd1, 4'd8, 4'd12, 4'd4};

  logic [15:0][4:0] ev_t_low;
  logic [15:0]      ev_valid;
  logic [15:0][7:0] ev_col;
  logic [15:0]      ev_dir;
  logic             ev_buf;

  always_comb begin
    for (int j = 0; j < 16; j++) begin
      logic signed [21:0] yj;
      logic signed [30:0] xoff;
      logic signed [30:0] t;       // t[4:0] are below the pixel
      logic signed [25:0] p;
      logic               in_y, act;
      yj   = $signed({3'b0, sp_q[j].py, 5'b0}) + (aa_q ? 22'(2 * j + 1) : 22'sd16);
      xoff = aa_q ? 31'(2 * int'(SAMPLE_Q[j]) + 1) : 31'sd16;
      t    = sp_q[j].xt - xoff + 31'sd31;
      p    = t[30:5];
      ev_t_low[j] = t[4:0];
      in_y = (22'(sp_q[j].ya) <= yj) && (yj < 22'(sp_q[j].yb));
      act  = sv_q[j] && in_y && (aa_q || j == 0);
      ev_valid[j] = act && (p < $signed({12'b0, sp_q[j].sx1}));
      ev_col[j]   = (p <= $signed({12'b0, sp_q[j].sx0})) ? 8'd0 : 8'(p - $signed({12'b0, sp_q[j].sx0}));
      ev_dir[j]   = sp_q[j].dir;
    end
  end
  // g2d_wind_acc takes one buffer for all banks: a pass starts only when the previous one has left the
  // pipeline (ps_start), and the buffer of the pass in flight is latched at its start
  logic pipe_buf_q;
  assign ev_buf = pipe_buf_q;
  // row and segment of the pass in flight (py_q / sx0_q move on at the end of the pass, while its last
  // edges are still in the read pipeline)
  logic [13:0] pass_py_q, pass_sx0_q, pass_sx1_q;

  function automatic stage_t step(stage_t s);
    stage_t o;
    o = s;
    if (s.rs > s.r) begin
      o.xt = s.xt + 31'(s.qs) + 31'sd1;
      o.r  = s.r + s.dy - s.rs;
    end else begin
      o.xt = s.xt + 31'(s.qs);
      o.r  = s.r - s.rs;
    end
    return o;
  endfunction

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sv_q <= '0;
      sm_q <= '0;
    end else if (clr_i) begin
      sv_q <= '0;
      sm_q <= '0;
    end else begin
      sv_q <= {sv_q[14:0], sp_in_v};
      sm_q <= {sm_q[14:0], sp_in_m};
    end
  end

  always_ff @(posedge clk_i) begin
    sp_q[0] <= sp_in;
    for (int j = 1; j < 16; j++) sp_q[j] <= step(sp_q[j-1]);
  end

  logic pipe_busy;
  always_comb begin
    pipe_busy = ps_v1_q | ps_v2_q | ps_mark1_q | ps_mark2_q | (|sv_q) | (|sm_q);
  end

  // ---------------------------------------------------------------- accumulator
  logic done_pass;
  assign done_pass = sm_q[15];

  g2d_wind_acc u_acc (
    .clk_i,
    .rst_ni,
    .clr_i,
    .cfg_aa_i    (aa_q),
    .cfg_eo_i    (eo_q),
    .init_o      (init),
    .ev_buf_i    (ev_buf),
    .ev_valid_i  (ev_valid),
    .ev_col_i    (ev_col),
    .ev_dir_i    (ev_dir),
    .free_o      (free),
    .done_i      (done_pass),
    .done_buf_i  (pipe_buf_q),
    .done_w_i    (9'(sp_q[15].sx1 - sp_q[15].sx0)),
    .cov_valid_o,
    .cov_ready_i,
    .cov_o,
    .ram_coll_o  (coll_acc)
  );

  // ---------------------------------------------------------------- control
  logic ps_start, ps_issue, ps_end, row_last_seg;
  logic [EI:0] rows_left_unused;
  assign row_last_seg = (seg_end == rx1_q);
  // a pass starts when its buffer is free, the pipeline holds no other pass (one buffer per pass in the
  // pipeline) and the previous row's state write-backs have landed
  assign ps_start = (st_q == S_ROWS) && !ps_act_q && !stop_i && (free[ps_buf_q] || (mrow_q && cbase_q != '0)) &&
                    !pipe_busy &&
                    (wb_pend_q == '0);
  assign ps_issue = ps_act_q && (ps_k_q < na_q[pass_par_q]);
  assign ps_end   = ps_act_q && (ps_k_q == na_q[pass_par_q]);

  always_comb begin
    eb_we    = ld_valid && cnt_q >= cbase_q && cnt_q < cbase_q + 25'(CHUNK);
    eb_waddr = EI'(cnt_q - cbase_q);
    eb_wdata = ld_edge;
    eb_re = 1'b0; eb_raddr = bs_i_q[EI-1:0];
    sb_re = 1'b0; sb_raddr = ab_rdata;
    ab_we = 1'b0; ab_waddr = {nxt_par, na_q[nxt_par][EI-1:0]}; ab_wdata = bs_i1_q;
    ab_re = 1'b0; ab_raddr = {pass_par_q, ps_k_q[EI-1:0]};
    sb_we = '0;
    sb_waddr = {2{dv_tag}};
    sb_wdata = {2{st_t'{xt: 31'(-dv_q), r: dv_r}}};
    if (bs_rd) eb_re = 1'b1;
    if (bs_v1_q && bs_active) ab_we = 1'b1;
    if (dv_out_valid) sb_we[nxt_par] = 1'b1;     // band setup: always the parity not used by the rows
    if (ps_issue) ab_re = 1'b1;
    if (ps_v1_q) begin
      eb_re    = 1'b1;
      eb_raddr = ab_rdata;
      sb_re    = 1'b1;
    end
    // state write-back after the row's last segment: one row step
    if (ps_v2_q && ps_ls2_q) begin
      sb_we[pass_par_q]    = 1'b1;
      sb_waddr[pass_par_q] = ps_idx2_q;
      if (eb_rdata.rr > sb_rdata.r) sb_wdata[pass_par_q] = st_t'{xt: sb_rdata.xt + 31'(eb_rdata.qr) + 31'sd1,
                                                                r: sb_rdata.r + eb_rdata.dy - eb_rdata.rr};
      else                          sb_wdata[pass_par_q] = st_t'{xt: sb_rdata.xt + 31'(eb_rdata.qr),
                                                                r: sb_rdata.r - eb_rdata.rr};
    end
  end

  // stage-0 entry: the edge read two cycles ago, or the end marker
  always_comb begin
    p0_v    = ps_v2_q;
    p0_mark = ps_mark2_q;
    p0_e    = eb_rdata;
    p0_s    = sb_rdata;
    sp_in   = '0;
    sp_in_v = p0_v;
    sp_in_m = p0_mark;
    sp_in.py   = pass_py_q;
    sp_in.sx0  = pass_sx0_q;
    sp_in.sx1  = pass_sx1_q;
    sp_in.xt   = p0_s.xt;
    sp_in.r    = p0_s.r;
    sp_in.ya   = p0_e.ya;
    sp_in.yb   = p0_e.yb;
    sp_in.dy   = p0_e.dy;
    sp_in.dir  = p0_e.dir;
    sp_in.qs   = p0_e.qs;
    sp_in.rs   = p0_e.rs;
  end

  assign ld_start = ((st_q == S_INIT) && !init) || bload_go_q;
  assign ld_ry0   = strm_q ? py_q : ry0_q;
  assign ld_ry1   = strm_q ? b_end_q : ry1_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      st_q          <= S_IDLE;
      n_q           <= '0;
      cnt_q         <= '0;
      strm_q        <= 1'b0;
      bload_go_q    <= 1'b0;
      mrow_q        <= 1'b0;
      cbase_q       <= '0;
      bs_scan_q     <= 1'b0;
      bs_v1_q       <= 1'b0;
      bs_mv_q       <= 1'b0;
      bs_inflight_q <= '0;
      bs_ld_q       <= 1'b0;
      na_q          <= '0;
      cur_q         <= 1'b0;
      pass_par_q    <= 1'b0;
      ps_act_q      <= 1'b0;
      ps_buf_q      <= 1'b0;
      ps_v1_q       <= 1'b0;
      ps_v2_q       <= 1'b0;
      ps_mark1_q    <= 1'b0;
      ps_mark2_q    <= 1'b0;
      wb_pend_q     <= '0;
    end else if (clr_i) begin
      st_q          <= S_IDLE;
      n_q           <= '0;
      cnt_q         <= '0;
      strm_q        <= 1'b0;
      bload_go_q    <= 1'b0;
      mrow_q        <= 1'b0;
      cbase_q       <= '0;
      bs_scan_q     <= 1'b0;
      bs_v1_q       <= 1'b0;
      bs_mv_q       <= 1'b0;
      bs_inflight_q <= '0;
      bs_ld_q       <= 1'b0;
      na_q          <= '0;
      cur_q         <= 1'b0;
      pass_par_q    <= 1'b0;
      ps_act_q      <= 1'b0;
      ps_buf_q      <= 1'b0;
      ps_v1_q       <= 1'b0;
      ps_v2_q       <= 1'b0;
      ps_mark1_q    <= 1'b0;
      ps_mark2_q    <= 1'b0;
      wb_pend_q     <= '0;
    end else begin
      // read pipeline of the passes
      ps_v1_q    <= ps_issue;
      ps_v2_q    <= ps_v1_q;
      ps_mark1_q <= ps_end && (!mrow_q || last_chunk_q);   // only the last pass closes the buffer
      ps_mark2_q <= ps_mark1_q;
      ps_ls1_q   <= ps_last_seg_q;
      ps_ls2_q   <= ps_ls1_q;
      ps_idx2_q  <= ab_rdata;
      wb_pend_q  <= 2'(ps_issue & ps_last_seg_q) + 2'(ps_v1_q & ps_ls1_q);
      bload_go_q <= 1'b0;
      // band setup pipeline
      bs_v1_q <= bs_rd || otf_in;
      bs_ld_q <= otf_in;
      bs_i1_q <= otf_in ? eb_waddr : bs_i_q[EI-1:0];
      bs_mv_q <= bs_v1_q && bs_active;
      bs_inflight_q <= bs_inflight_q + 7'(bs_mv_q) - 7'(dv_out_valid);
      if (bs_v1_q && bs_active) na_q[nxt_par] <= na_q[nxt_par] + 1'b1;
      // scan (sequential band setup, or the next band during the rows)
      if (bs_rd) bs_i_q <= bs_i_q + 1'b1;
      if (bs_scan_q && ((bs_rd && bs_i_q + 1'b1 >= n_q) || bs_i_q >= n_q)) bs_scan_q <= 1'b0;

      unique case (st_q)
        S_IDLE: if (start_i) st_q <= S_INIT;
        S_INIT: if (!init) begin
          st_q    <= S_LOAD;
          // first band, set up while the list loads
          bs_lo_q <= ry0_q;
          bs_hi_q <= ((15'(ry0_q) + 15'(BAND_ROWS)) < 15'(ry1_q)) ? ry0_q + 14'(BAND_ROWS) : ry1_q;
          na_q[nxt_par] <= '0;
          sx0_q   <= rx0_q;   // later rows reset it when they start
          cnt_q   <= '0;
          strm_q  <= 1'b0;
          mrow_q  <= 1'b0;
          cbase_q <= '0;
        end
        S_LOAD, S_BLOAD: begin
          if (ld_valid) cnt_q <= cnt_q + 25'd1;
          if (stop_i) st_q <= S_END;
          else if (ld_done) begin
            py_q    <= (st_q == S_LOAD) ? ry0_q : py_q;
            b_end_q <= (st_q == S_LOAD) ? (((15'(ry0_q) + 15'(BAND_ROWS)) < 15'(ry1_q)) ? ry0_q + 14'(BAND_ROWS) : ry1_q)
                                        : b_end_q;
            if (st_q == S_LOAD && cnt_q > 25'(CHUNK)) begin
              // too many edges for one pass: stream, one load per band (first band now)
              st_q       <= S_BLOAD;
              strm_q     <= 1'b1;
              cnt_q      <= '0;
              bload_go_q <= 1'b1;
            end else if (st_q == S_BLOAD && !mrow_q && cnt_q > 25'(CHUNK)) begin
              // too many active edges in this band: one-row bands with several passes per segment
              st_q       <= S_BLOAD;
              mrow_q     <= 1'b1;
              mrow_end_q <= b_end_q;
              b_end_q    <= py_q + 14'd1;
              cbase_q    <= '0;
              cnt_q      <= '0;
              bload_go_q <= 1'b1;
            end else begin
              st_q         <= S_BAND;
              n_q          <= (cnt_q <= cbase_q) ? '0 :
                              (cnt_q - cbase_q > 25'(CHUNK)) ? (EI + 1)'(CHUNK) : (EI + 1)'(cnt_q - cbase_q);
              last_chunk_q <= !mrow_q || (cbase_q + 25'(CHUNK) >= cnt_q);
              if (st_q == S_BLOAD) begin
                // reloaded list (streaming / one-row bands): scan it now
                bs_scan_q    <= 1'b1;
                bs_i_q       <= '0;
                na_q[nxt_par] <= '0;
                bs_lo_q      <= py_q;
                bs_hi_q      <= b_end_q;
              end
              // S_LOAD: the first band was set up during the load
            end
          end
        end
        S_BAND: begin
          if (!bs_scan_q && !bs_v1_q && !bs_mv_q) st_q <= S_BWAIT;
          if (stop_i) st_q <= S_END;
        end
        S_BWAIT: begin
          if (bs_inflight_q == '0 && !dv_out_valid) begin
            st_q  <= S_ROWS;   // the segment is kept: a reload in the middle of a row continues it
            cur_q <= ~cur_q;   // the rows use the band just set up
            if (!strm_q && b_end_q < ry1_q) begin
              // next band's setup during these rows (into the parity just left)
              bs_scan_q   <= 1'b1;
              bs_i_q      <= '0;
              na_q[cur_q] <= '0;
              bs_lo_q     <= b_end_q;
              bs_hi_q     <= ((15'(b_end_q) + 15'(BAND_ROWS)) < 15'(ry1_q)) ? b_end_q + 14'(BAND_ROWS) : ry1_q;
            end
          end
          if (stop_i) st_q <= S_END;
        end
        S_ROWS: begin
          if (stop_i) st_q <= S_END;
          if (ps_start) begin
            pipe_buf_q    <= ps_buf_q;
            pass_par_q    <= cur_q;
            pass_py_q     <= py_q;
            pass_sx0_q    <= sx0_q;
            pass_sx1_q    <= seg_end;
            ps_act_q      <= 1'b1;
            ps_k_q        <= '0;
            ps_last_seg_q <= row_last_seg && !mrow_q;   // one-row bands need no row step
          end
          if (ps_issue) ps_k_q <= ps_k_q + 1'b1;
          if (ps_end && mrow_q && !last_chunk_q) begin
            // next pass of the same segment: reload the next chunk, keep accumulating into this buffer
            ps_act_q   <= 1'b0;
            st_q       <= S_BLOAD;
            cbase_q    <= cbase_q + 25'(CHUNK);
            cnt_q      <= '0;
            bload_go_q <= 1'b1;
          end else if (ps_end) begin
            ps_act_q <= 1'b0;
            ps_buf_q <= ~ps_buf_q;
            if (!row_last_seg) begin
              sx0_q <= seg_end;
              if (mrow_q && cbase_q != '0) begin
                // the buffer holds the last chunk: the next segment starts again with chunk 0
                st_q       <= S_BLOAD;
                cbase_q    <= '0;
                cnt_q      <= '0;
                bload_go_q <= 1'b1;
              end
            end else begin
              sx0_q <= rx0_q;
              if (py_q + 14'd1 == ry1_q) begin
                st_q <= S_END;
              end else if (mrow_q) begin
                // next row of the overflowing band, or back to normal bands after it
                py_q       <= py_q + 14'd1;
                st_q       <= S_BLOAD;
                cbase_q    <= '0;
                cnt_q      <= '0;
                bload_go_q <= 1'b1;
                if (py_q + 14'd1 == mrow_end_q) begin
                  mrow_q  <= 1'b0;
                  b_end_q <= ((15'(mrow_end_q) + 15'(BAND_ROWS)) < 15'(ry1_q)) ? mrow_end_q + 14'(BAND_ROWS) : ry1_q;
                end else begin
                  b_end_q <= py_q + 14'd2;
                end
              end else if (py_q + 14'd1 == b_end_q) begin
                py_q      <= py_q + 14'd1;
                b_end_q   <= ((15'(b_end_q) + 15'(BAND_ROWS)) < 15'(ry1_q)) ? b_end_q + 14'(BAND_ROWS) : ry1_q;
                if (strm_q) begin
                  st_q       <= S_BLOAD;              // reload the list for the next band
                  cnt_q      <= '0;
                  bload_go_q <= 1'b1;
                end else begin
                  st_q <= S_BAND;   // its setup started with these rows (and may still run)
                end
              end else begin
                py_q <= py_q + 14'd1;
              end
            end
          end
        end
        S_END: begin
          // last coverage swept (or abandoned on stop)
          if (stop_i || (!pipe_busy && free == 2'b11 && !cov_valid_o)) st_q <= S_IDLE;
        end
        default: st_q <= S_IDLE;
      endcase
      // stop: the pass being issued ends too (ps_k_q only advances in S_ROWS; an issuing pass left
      // behind would re-read one edge every cycle and collide with its row write-back, B-5.4)
      if (stop_i) ps_act_q <= 1'b0;
    end
  end

  // band setup multiply: N0 = Xa*dy + (Y0 - Ya)*dx, from the const read of the previous cycle
  always_ff @(posedge clk_i) begin
    bs_n0_q <= 44'($signed(bs_e.xa) * $signed({1'b0, bs_e.dy})) +
               44'($signed(y0_band - 22'(bs_e.ya)) * $signed(bs_e.dx));
    bs_dy_q <= bs_e.dy;
    bs_im_q <= bs_i1_q;
    ld_e1_q <= ld_edge;
  end

  always_ff @(posedge clk_i) begin
    if (start_i && st_q == S_IDLE) begin
      aa_q  <= cfg_aa_i;
      eo_q  <= cfg_eo_i;
      rx0_q <= cfg_rx0_i;
      rx1_q <= cfg_rx1_i;
      ry0_q <= cfg_ry0_i;
      ry1_q <= cfg_ry1_i;
    end
  end

  assign busy_o = (st_q != S_IDLE);
  assign idle_o = stop_i ? ld_idle : (st_q == S_IDLE) && ld_idle;

  assign unused_q = '0;
  assign rows_left_unused = '0;
  logic unused;
  always_comb begin
    unused = ^{ld_busy, dv_ready_unused, unused_q, rows_left_unused, p0_e, bs_e, dv_q[31], dv_ovf};
    for (int j = 0; j < 16; j++) unused ^= ^ev_t_low[j];
  end

`ifndef SYNTHESIS
  // not at time 0: before the asynchronous reset the registers hold random initial values
  always_ff @(posedge clk_i) begin
    if ($time > 0 && start_i && st_q == S_IDLE && CHUNK > MAX_EDGES) $error("g2d_raster: CHUNK > MAX_EDGES");
    if ($time > 0 && dv_out_valid && dv_ovf) $error("g2d_raster: band start division out of range");
  end
`endif

endmodule
