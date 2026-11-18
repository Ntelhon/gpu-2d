// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Edge loader of the path rasterizer (rendering.md §8.1, §8.3; arch.md §6.3 stage 2).
//
// Reads EDGE_COUNT 16-byte edge records from EDGE_ADDR through its read client (credit-based, chunks
// inside 256-byte blocks), and for every edge:
//   - clamps each s.4 coordinate to [-(2^18 - 1), 2^18 - 1] (§8.1) and scales it to 1/32 px (x2, §8.2);
//   - orients it: (Xa, Ya) the endpoint with the smaller Y, dir = +1 if the edge goes down (y1 > y0);
//   - drops it when it can never cross a sample of the clipped draw rectangle [*, rx1) x [ry0, ry1):
//     horizontal (Ya == Yb), Yb <= 32*ry0, Ya >= 32*ry1 (no sample row inside), or
//     min(Xa, Xb) > 32*rx1 - 1 (strictly right of every sample, §8.3 counts edges at or left);
//     edges left of the rectangle are kept: they give the base winding;
//   - computes the per-edge steps for the sample-row spacing S (2 with AA, 32 without) and for one pixel
//     row: S*dx = qs*dy + rs and 32*dx = qr*dy + rr, 0 <= rs, rr < dy (floor division, two g2d_sdiv in
//     lockstep).
// Kept edges leave in list order on the output stream; done_o pulses when the whole list is processed.
// Value ranges (1/32 px): X, Y in +-(2^19 - 2) (s21), dy in 1..2^20 - 4, |dx| <= 2^20 - 4, |S*dx| < 2^25.
//
// stop_i (soft reset): no new read request; idle_o ignores the contents (requests may have been dropped,
// beats of issued bursts still fit by the credits). clr_i clears everything.

module g2d_edge_load #(
  parameter int unsigned DATA_W = 64,
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // start of a list
  input  logic              start_i,
  input  logic [31:0]       cfg_addr_i,       // 16-byte aligned
  input  logic [23:0]       cfg_count_i,      // >= 1
  input  logic              cfg_aa_i,
  input  logic [13:0]       cfg_rx1_i,        // clipped draw rectangle (non-empty)
  input  logic [13:0]       cfg_ry0_i,
  input  logic [13:0]       cfg_ry1_i,
  // read client
  output logic              rd_req_valid_o,
  input  logic              rd_req_ready_i,
  output logic [31:0]       rd_req_addr_o,
  output logic [15:0]       rd_req_len_o,
  input  logic              rd_rsp_valid_i,
  output logic              rd_rsp_ready_o,
  input  logic [DATA_W-1:0] rd_rsp_data_i,
  // kept edges
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic signed [20:0] out_xa_o,
  output logic signed [20:0] out_ya_o,
  output logic signed [20:0] out_yb_o,
  output logic signed [20:0] out_dx_o,
  output logic [20:0]       out_dy_o,
  output logic              out_dir_o,         // 1: +1 (downwards), 0: -1
  output logic signed [26:0] out_qs_o,
  output logic [20:0]       out_rs_o,
  output logic signed [26:0] out_qr_o,
  output logic [20:0]       out_rr_o,
  // status
  output logic              busy_o,
  output logic              done_o,
  output logic              idle_o
);

  localparam int unsigned BUF_BEATS = 512 / DB;            // 512-byte beat buffer (32 edges)
  localparam int unsigned CR_W = $clog2(BUF_BEATS + 1);
  localparam int unsigned EB = 16 / DB;                     // beats per edge (2 or 1)

  // ---------------------------------------------------------------- requests
  logic        run_q;
  logic [31:0] raddr_q;
  logic [27:0] rleft_q;           // bytes still to request
  logic [24:0] eleft_q;           // edges still to process (assembled)
  logic [CR_W-1:0] credit_q;
  logic [8:0]  to_blk;
  logic [27:0] chunk;
  logic [CR_W-1:0] chunk_beats;
  logic        req_fire, beat_pop;

  assign to_blk      = 9'd256 - 9'(raddr_q[7:0]);
  assign chunk       = (rleft_q < 28'(to_blk)) ? rleft_q : 28'(to_blk);
  assign chunk_beats = CR_W'(chunk >> $clog2(DB));          // 16-byte aligned: whole beats
  assign rd_req_valid_o = run_q & (rleft_q != '0) & ~stop_i & (credit_q >= chunk_beats);
  assign rd_req_addr_o  = raddr_q;
  assign rd_req_len_o   = 16'(chunk - 28'd1);
  assign req_fire       = rd_req_valid_o & rd_req_ready_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)     credit_q <= CR_W'(BUF_BEATS);
    else if (clr_i)  credit_q <= CR_W'(BUF_BEATS);
    else             credit_q <= credit_q - (req_fire ? chunk_beats : '0) + CR_W'(beat_pop);
  end

  // ---------------------------------------------------------------- beat buffer, edge assembly
  logic              bf_valid, bf_in_ready;
  logic [DATA_W-1:0] bf_data;
  assign rd_rsp_ready_o = bf_in_ready;   // always 1 (credits)

  g2d_fifo_ram #(.WIDTH(DATA_W), .DEPTH(BUF_BEATS)) u_buf (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (rd_rsp_valid_i),
    .in_ready_o  (bf_in_ready),
    .in_data_i   (rd_rsp_data_i),
    .out_valid_o (bf_valid),
    .out_ready_i (beat_pop),
    .out_data_o  (bf_data)
  );

  logic [127:0] edge_w;          // x0, y0, x1, y1 (little-endian words)
  logic         edge_v;          // an assembled edge is presented
  logic         edge_take;       // ... and consumed this cycle

  if (EB == 2) begin : g_two
    logic         part_q;        // first half of the edge held
    logic [63:0]  half_q;
    assign edge_v   = bf_valid & part_q;
    assign edge_w   = {bf_data[63:0], half_q};
    assign beat_pop = bf_valid & (~part_q | edge_take);
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)                     part_q <= 1'b0;
      else if (clr_i)                  part_q <= 1'b0;
      else if (bf_valid && !part_q)    part_q <= 1'b1;
      else if (edge_take)              part_q <= 1'b0;
    end
    always_ff @(posedge clk_i) begin
      if (bf_valid && !part_q) half_q <= bf_data[63:0];
    end
  end else begin : g_one
    assign edge_v   = bf_valid;
    assign edge_w   = bf_data[127:0];
    assign beat_pop = edge_take;     // DATA_W 128: one beat per edge
  end

  // ---------------------------------------------------------------- per-edge setup
  function automatic logic signed [20:0] clamp2(logic [31:0] v);
    logic signed [31:0] s;
    s = $signed(v);
    if (s > 32'sd262143)  s = 32'sd262143;
    if (s < -32'sd262143) s = -32'sd262143;
    return {s[19:0], 1'b0};     // x2: 1/32 px
  endfunction

  logic signed [20:0] x0, y0, x1, y1, xa, ya, xb, yb, xmin;
  logic signed [21:0] dx_w, dy_w;
  logic               down, drop;
  logic signed [21:0] ylo, yhi, xlim;

  assign x0   = clamp2(edge_w[31:0]);
  assign y0   = clamp2(edge_w[63:32]);
  assign x1   = clamp2(edge_w[95:64]);
  assign y1   = clamp2(edge_w[127:96]);
  assign down = y1 > y0;
  assign xa   = down ? x0 : x1;
  assign ya   = down ? y0 : y1;
  assign xb   = down ? x1 : x0;
  assign yb   = down ? y1 : y0;
  assign dx_w = 22'(xb) - 22'(xa);
  assign dy_w = 22'(yb) - 22'(ya);
  assign xmin = (xa < xb) ? xa : xb;
  assign ylo  = $signed({3'b0, cfg_ry0_i, 5'b0});
  assign yhi  = $signed({3'b0, cfg_ry1_i, 5'b0});
  assign xlim = $signed({3'b0, cfg_rx1_i, 5'b0}) - 22'sd1;
  assign drop = (y0 == y1) || (22'(yb) <= ylo) || (22'(ya) >= yhi) || (22'(xmin) > xlim);

  // divider: n = S*dx, d = dy; the edge rides along as the tag
  localparam int unsigned TW = 21 * 4 + 1;
  logic              dv_in_valid, dv_in_ready, dv_ovf;
  logic signed [26:0] dv_n, dv_q;
  logic [20:0]       dv_r;
  logic [TW-1:0]     dv_tag_in, dv_tag_out;

  assign dv_n        = cfg_aa_i ? 27'(dx_w) <<< 1 : 27'(dx_w) <<< 5;
  assign dv_tag_in   = {xa, ya, yb, dx_w[20:0], down};
  assign dv_in_valid = edge_v & ~drop & (eleft_q != '0);
  assign edge_take   = edge_v & (eleft_q != '0) & (drop | dv_in_ready);

  // row step 32*dx/dy: same pipeline, same handshake (identical latency and stall behaviour)
  logic              dr_valid_unused, dr_ready_unused, dr_ovf;
  logic signed [26:0] dr_q;
  logic [20:0]       dr_r;
  logic [0:0]        dr_tag_unused;
  g2d_sdiv #(.NW(27), .DW(21), .QW(26), .TAG_W(1)) u_div_row (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (dv_in_valid),
    .in_ready_o  (dr_ready_unused),
    .in_n_i      (27'(dx_w) <<< 5),
    .in_d_i      (dy_w[20:0]),
    .in_tag_i    (1'b0),
    .out_valid_o (dr_valid_unused),
    .out_ready_i (out_ready_i),
    .out_q_o     (dr_q),
    .out_r_o     (dr_r),
    .out_ovf_o   (dr_ovf),
    .out_tag_o   (dr_tag_unused)
  );

  g2d_sdiv #(.NW(27), .DW(21), .QW(26), .TAG_W(TW)) u_div (
    .clk_i,
    .rst_ni,
    .clr_i,
    .in_valid_i  (dv_in_valid),
    .in_ready_o  (dv_in_ready),
    .in_n_i      (dv_n),
    .in_d_i      (dy_w[20:0]),
    .in_tag_i    (dv_tag_in),
    .out_valid_o (out_valid_o),
    .out_ready_i (out_ready_i),
    .out_q_o     (dv_q),
    .out_r_o     (dv_r),
    .out_ovf_o   (dv_ovf),
    .out_tag_o   (dv_tag_out)
  );

  assign {out_xa_o, out_ya_o, out_yb_o, out_dx_o, out_dir_o} = dv_tag_out;
  assign out_dy_o = 21'(out_yb_o - out_ya_o);
  assign out_qs_o = dv_q;
  assign out_rs_o = dv_r;
  assign out_qr_o = dr_q;
  assign out_rr_o = dr_r;

  // ---------------------------------------------------------------- control
  logic [5:0] inflight_q;        // edges inside the divider
  logic       out_fire, in_fire_q, out_fire_q;   // v1.7: counted one cycle later (timing, T-1)
  assign out_fire = out_valid_o & out_ready_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      run_q      <= 1'b0;
      raddr_q    <= '0;
      rleft_q    <= '0;
      eleft_q    <= '0;
      inflight_q <= '0;
      in_fire_q  <= 1'b0;
      out_fire_q <= 1'b0;
    end else if (clr_i) begin
      run_q      <= 1'b0;
      rleft_q    <= '0;
      eleft_q    <= '0;
      inflight_q <= '0;
      in_fire_q  <= 1'b0;
      out_fire_q <= 1'b0;
    end else begin
      if (start_i) begin
        run_q   <= 1'b1;
        raddr_q <= cfg_addr_i;
        rleft_q <= {cfg_count_i, 4'b0000};
        eleft_q <= 25'(cfg_count_i);
      end else begin
        if (req_fire) begin
          raddr_q <= raddr_q + 32'(chunk);
          rleft_q <= rleft_q - chunk;
        end
        if (edge_take) eleft_q <= eleft_q - 25'd1;
        if (done_o)    run_q <= 1'b0;
      end
      in_fire_q  <= dv_in_valid & dv_in_ready;
      out_fire_q <= out_fire;
      inflight_q <= inflight_q + 6'(in_fire_q) - 6'(out_fire_q);
    end
  end

  assign done_o = run_q && !start_i && eleft_q == '0 && inflight_q == '0 && !in_fire_q && !out_fire_q;
  assign busy_o = run_q;
  assign idle_o = stop_i | (~run_q & (credit_q == CR_W'(BUF_BEATS)));

  logic unused;
  assign unused = ^{dx_w[21], dy_w[21], dv_ovf, dr_ovf, dr_valid_unused, dr_ready_unused, dr_tag_unused};
  // |dx|, dy < 2^20, so the divider contract always holds; u_div_row mirrors u_div cycle for cycle

`ifndef SYNTHESIS
  // not at time 0: before the asynchronous reset the registers hold random initial values
  always_ff @(posedge clk_i) begin
    if ($time > 0 && out_fire && (dv_ovf || dr_ovf)) $error("g2d_edge_load: edge step division out of range");
  end
`endif

endmodule
