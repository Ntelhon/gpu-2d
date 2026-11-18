// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Texel addressing (rendering.md §3.5, §7.3, §7.4, §7.5). Seven-stage global-stall pipeline.
//
// In: u, v (signed s15.16) and ok from g2d_xform (ok = the coordinate range test of §7.4 for the draw's
// wrap mode).
//   POINT:    c0 = c1 = u >> 16, fx = 0                (same for v)
//   BILINEAR: u' = u - 2^15, c0 = u' >> 16, c1 = c0 + 1, fx = (u' >> 8) & 255
//   WRAP NONE:    pass = ok and sx0 <= u >> 16 < sx1 and sy0 <= v >> 16 < sy1 (source coverage test);
//                 c0, c1 clamped to [sx0, sx1 - 1]
//   WRAP != NONE: pass = ok; c0, c1 mapped per axis (§7.5): COLOR keeps inside coordinates and flags
//                 outside ones as border texels (their address is the clamped coordinate, so no read ever
//                 leaves the source); PAD clamps; REPEAT / REFLECT take r = c0 - sx0 modulo m = n or 2n
//                 (n = sx1 - sx0) and step c1 from c0's remainder.
// Modulo (exact for every |r| < 2^16 + 2^13 and m <= 16384, checked exhaustively in model/tests/test_ops.cpp):
//   q = (r * R) >>> 31 with the per-draw reciprocal R = ceil(2^31 / m); rem = r - q m; rem -= m if rem >= m.
// The reciprocals of both axes are computed by two serial dividers (32 cycles) when a draw with REPEAT or
// REFLECT starts (start_i); in_ready_o is low meanwhile (the walker / transform latency hides most of it).
// WRAP = COLOR with cfg_border_solid_i (BORDER_SOLID, §7.5): addressing as NONE (clamped, no border flags), the
// pixel passes whenever ok, and out_solid_o marks a pixel whose sample centre is outside the source rectangle (it
// gets BORDER_COLOR as a plain colour at the join; its texels are don't-care).
// INDEXED sources are point-sampled: the caller passes cfg_bilinear_i = 0 for them (§3.5).
// Out: byte addresses of the four texels t00 = (x0,y0), t10 = (x1,y0), t01 = (x0,y1), t11 = (x1,y1)
// (rows addressed independently: with wrapping, y1 need not be y0 + 1), the parity of y0 and y1 (cache bank
// selection), fx, fy, per texel the border flag (WRAP = COLOR) and the right shift that extracts a sub-byte
// pixel from its byte (§3.5: SUB_ORDER 0 = first pixel in the low bits). When pass = 0 the other outputs are
// don't-care.

module g2d_tex_addr #(
  parameter int unsigned TAG_W = 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  // draw configuration
  input  logic              start_i,           // draw accepted, configuration valid (one cycle)
  input  logic              cfg_bilinear_i,
  input  logic [2:0]        cfg_wrap_i,        // 0 NONE, 1 COLOR, 2 PAD, 3 REPEAT, 4 REFLECT
  input  logic              cfg_border_solid_i,
  input  logic [13:0]       cfg_sx0_i,
  input  logic [13:0]       cfg_sy0_i,
  input  logic [13:0]       cfg_sx1_i,
  input  logic [13:0]       cfg_sy1_i,
  input  logic [31:0]       cfg_addr_i,
  input  logic [15:0]       cfg_stride_i,
  input  logic [2:0]        cfg_lg_bits_i,     // log2 bits per texel (0..5)
  input  logic              cfg_sub_order_i,
  input  logic              cfg_b24_i,         // 24-bit source (v1.6b): column byte = 3 x
  input  logic              cfg_gauss_i,       // Gaussian (v1.6b): c0 = point texel + in_dx_i / in_dy_i, c1 = c0 + 1
  // YUV planes (v1.6b, rendering.md §3.7): plane 0 uses cfg_addr_i / cfg_stride_i / cfg_lg_bits_i with
  // cfg_p0_xs_i; planes 1, 2 their own base, stride, log2 bits and subsampling shifts. cfg_yuy2_i: the sub-byte
  // shift field carries the luma column parity (Y0 / Y1 of the 4-byte pair).
  input  logic              cfg_p0_xs_i,
  input  logic [1:0][31:0]  cfg_paddr_i,       // planes 1, 2
  input  logic [1:0][15:0]  cfg_pstride_i,
  input  logic [1:0][2:0]   cfg_plg_i,
  input  logic [1:0]        cfg_pxs_i,
  input  logic [1:0]        cfg_pys_i,
  input  logic              cfg_yuy2_i,
  // in
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_ok_i,
  input  logic [31:0]       in_u_i,
  input  logic [31:0]       in_v_i,
  input  logic signed [1:0] in_dx_i,           // Gaussian group offsets (g2d_tex_seq)
  input  logic signed [1:0] in_dy_i,
  input  logic [1:0]        in_plane_i,        // YUV plane of this sub-request (g2d_tex_seq)
  input  logic [TAG_W-1:0]  in_tag_i,
  // out
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_pass_o,
  output logic              out_solid_o,
  output logic [3:0][31:0]  out_addr_o,
  output logic [1:0]        out_ypar_o,
  output logic [7:0]        out_fx_o,
  output logic [7:0]        out_fy_o,
  output logic [3:0]        out_border_o,
  output logic [3:0][2:0]   out_sh_o,
  output logic [TAG_W-1:0]  out_tag_o
);

  localparam int unsigned NS = 7;

  // PAINT_CTRL.WRAP: 0 NONE, 1 COLOR, 2 PAD, 3 REPEAT, 4 REFLECT (PAD shares the clamp of NONE / COLOR)
  localparam logic [2:0] W_NONE = 3'd0, W_COLOR = 3'd1, W_REPEAT = 3'd3, W_REFLECT = 3'd4;

  // ---------------------------------------------------------------- per-draw reciprocals
  // R = ceil(2^31 / m) = floor((2^31 + m - 1) / m), m = n (REPEAT) or 2n (REFLECT): restoring division,
  // one quotient bit per cycle, both axes in parallel.
  logic        rcp_busy_q;
  logic [5:0]  rcp_cnt_q;
  logic [1:0][14:0] m_q;          // period per axis (x, y)
  logic [1:0][31:0] rcp_q;        // quotient (R when done)
  logic [1:0][31:0] num_q;        // dividend bits still to shift in
  logic [1:0][15:0] rem_q;
  logic        wrap_mod;
  assign wrap_mod = (cfg_wrap_i == W_REPEAT) || (cfg_wrap_i == W_REFLECT);

  logic [1:0][14:0] m_d;
  assign m_d[0] = (cfg_wrap_i == W_REFLECT) ? {cfg_sx1_i - cfg_sx0_i, 1'b0} : {1'b0, cfg_sx1_i - cfg_sx0_i};
  assign m_d[1] = (cfg_wrap_i == W_REFLECT) ? {cfg_sy1_i - cfg_sy0_i, 1'b0} : {1'b0, cfg_sy1_i - cfg_sy0_i};

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      rcp_busy_q <= 1'b0;
      rcp_cnt_q  <= '0;
    end else if (clr_i) begin
      rcp_busy_q <= 1'b0;
      rcp_cnt_q  <= '0;
    end else if (start_i && wrap_mod) begin
      rcp_busy_q <= 1'b1;
      rcp_cnt_q  <= 6'd32;
    end else if (rcp_busy_q) begin
      rcp_cnt_q  <= rcp_cnt_q - 6'd1;
      if (rcp_cnt_q == 6'd1) rcp_busy_q <= 1'b0;
    end
  end

  for (genvar a = 0; a < 2; a++) begin : g_rcp
    logic [16:0] t;
    assign t = {rem_q[a], num_q[a][31]};
    always_ff @(posedge clk_i) begin
      if (start_i) begin
        m_q[a]   <= m_d[a];
        num_q[a] <= 32'h8000_0000 + 32'(m_d[a]) - 32'd1;
        rem_q[a] <= '0;
        rcp_q[a] <= '0;
      end else if (rcp_busy_q) begin
        num_q[a] <= {num_q[a][30:0], 1'b0};
        if (t >= 17'(m_q[a])) begin
          rem_q[a] <= 16'(t - 17'(m_q[a]));
          rcp_q[a] <= {rcp_q[a][30:0], 1'b1};
        end else begin
          rem_q[a] <= t[15:0];
          rcp_q[a] <= {rcp_q[a][30:0], 1'b0};
        end
      end
    end
  end

  // ---------------------------------------------------------------- pipeline control
  logic [NS-1:0] v_q;
  logic          en;
  assign en          = ~v_q[NS-1] | out_ready_i;
  assign in_ready_o  = en & ~rcp_busy_q;
  assign out_valid_o = v_q[NS-1];

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)    v_q <= '0;
    else if (clr_i) v_q <= '0;
    else if (en)    v_q <= {v_q[NS-2:0], in_valid_i & ~rcp_busy_q};
  end

  // ---------------------------------------------------------------- S0: integer coordinates, test, fractions
  logic signed [32:0] up, vp;
  logic signed [16:0] tx, ty, bx0, by0;
  assign tx  = 17'($signed(in_u_i[31:16]));
  assign ty  = 17'($signed(in_v_i[31:16]));
  assign up  = $signed({in_u_i[31], in_u_i}) - 33'sd32768;
  assign vp  = $signed({in_v_i[31], in_v_i}) - 33'sd32768;
  assign bx0 = up[32:16];   // floor(u' / 2^16)
  assign by0 = vp[32:16];

  logic                s0_pass, s0_solid;
  logic signed [16:0]  s0_cx, s0_cy;      // c0 per axis
  logic                solid_mode, in_rect;
  assign solid_mode = (cfg_wrap_i == W_COLOR) && cfg_border_solid_i;
  assign in_rect    = tx >= $signed({3'b0, cfg_sx0_i}) && tx < $signed({3'b0, cfg_sx1_i}) &&
                      ty >= $signed({3'b0, cfg_sy0_i}) && ty < $signed({3'b0, cfg_sy1_i});
  logic                s0_bil;
  logic [1:0]          s0_plane;
  logic [7:0]          s0_fx, s0_fy;
  logic [TAG_W-1:0]    s0_tag;

  always_ff @(posedge clk_i) begin
    if (en) begin
      s0_pass <= in_ok_i && (cfg_wrap_i != W_NONE || in_rect);
      s0_solid <= solid_mode && !in_rect;
      s0_cx   <= cfg_gauss_i ? tx + 17'(in_dx_i) : cfg_bilinear_i ? bx0 : tx;
      s0_cy   <= cfg_gauss_i ? ty + 17'(in_dy_i) : cfg_bilinear_i ? by0 : ty;
      s0_bil  <= cfg_bilinear_i | cfg_gauss_i;   // second column / row = first + 1
      s0_fx   <= (cfg_bilinear_i && !cfg_gauss_i) ? up[15:8] : 8'd0;
      s0_fy   <= (cfg_bilinear_i && !cfg_gauss_i) ? vp[15:8] : 8'd0;
      s0_tag  <= in_tag_i;
      s0_plane <= in_plane_i;
    end
  end

  // ---------------------------------------------------------------- per-axis wrap (stages S1..S4)
  function automatic logic [13:0] clampc(logic signed [17:0] c, logic [13:0] l, logic [13:0] h1);
    if (c < $signed({4'b0, l}))   return l;
    if (c >= $signed({4'b0, h1})) return h1 - 14'd1;
    return c[13:0];
  endfunction

  logic             s0_bil_q1, s0_bil_q2, s0_bil_q3;
  logic [1:0][13:0] s4_c0, s4_c1;      // mapped x (index 0) and y (index 1) of c0, c1
  logic             texel_border;       // COLOR with texel border colour (BORDER_SOLID = 0)
  assign texel_border = (cfg_wrap_i == W_COLOR) && !cfg_border_solid_i;
  logic [1:0]       s4_b0, s4_b1;      // border flags (WRAP = COLOR)

  for (genvar a = 0; a < 2; a++) begin : g_axis
    logic [13:0] lo, hi1;
    assign lo  = (a == 0) ? cfg_sx0_i : cfg_sy0_i;
    assign hi1 = (a == 0) ? cfg_sx1_i : cfg_sy1_i;

    logic signed [16:0] c_s0;
    assign c_s0 = (a == 0) ? s0_cx : s0_cy;

    logic signed [17:0] s1_r, s2_r, s3_r;
    logic signed [16:0] s1_c, s2_c, s3_c;
    logic signed [50:0] s2_p;
    logic signed [35:0] s3_qm;

    // S1: r = c0 - s0;  S2: p = r * R;  S3: q m with q = p >>> 31
    logic signed [19:0] q;
    assign q = s2_p[50:31];
    logic unused_p;
    assign unused_p = ^s2_p[30:0];        // below the quotient
    always_ff @(posedge clk_i) begin
      if (en) begin
        s1_c  <= c_s0;
        s1_r  <= 18'(c_s0) - $signed({4'b0, lo});
        s2_c  <= s1_c;
        s2_r  <= s1_r;
        s2_p  <= 51'(s1_r) * $signed({1'b0, rcp_q[a]});
        s3_c  <= s2_c;
        s3_r  <= s2_r;
        s3_qm <= 36'(q) * $signed({1'b0, m_q[a]});
      end
    end

    // S4: remainder, mapped coordinates of c0 and c1, border flags
    logic signed [35:0] rem_a, m_s;
    logic [15:0]        rem0, rem1, m16, n2;
    logic [14:0]        n;
    logic [13:0]        k0, k1;
    logic signed [17:0] c0, c1;
    always_comb begin
      m_s   = 36'($signed({1'b0, m_q[a]}));
      m16   = 16'(m_q[a]);
      rem_a = 36'(s3_r) - s3_qm;
      rem0  = (rem_a >= m_s) ? 16'(rem_a - m_s) : 16'(rem_a);
      rem1  = (rem0 + 16'd1 == m16) ? 16'd0 : rem0 + 16'd1;
      n     = {1'b0, hi1 - lo};
      n2    = {n, 1'b0};
      // REFLECT: k < n ? k : 2n - 1 - k
      k0    = (rem0 < 16'(n)) ? 14'(rem0) : 14'(n2 - 16'd1 - rem0);
      k1    = (rem1 < 16'(n)) ? 14'(rem1) : 14'(n2 - 16'd1 - rem1);
      c0    = 18'(s3_c);
      c1    = s0_bil_q3 ? c0 + 18'sd1 : c0;
    end

    always_ff @(posedge clk_i) begin
      if (en) begin
        unique case (cfg_wrap_i)
          W_REPEAT: begin
            s4_c0[a] <= lo + 14'(rem0);
            s4_c1[a] <= s0_bil_q3 ? lo + 14'(rem1) : lo + 14'(rem0);
          end
          W_REFLECT: begin
            s4_c0[a] <= lo + k0;
            s4_c1[a] <= s0_bil_q3 ? lo + k1 : lo + k0;
          end
          default: begin   // NONE, COLOR (address of a border texel: the clamped coordinate), PAD
            s4_c0[a] <= clampc(c0, lo, hi1);
            s4_c1[a] <= clampc(c1, lo, hi1);
          end
        endcase
        s4_b0[a] <= texel_border && (c0 < $signed({4'b0, lo}) || c0 >= $signed({4'b0, hi1}));
        s4_b1[a] <= texel_border && (c1 < $signed({4'b0, lo}) || c1 >= $signed({4'b0, hi1}));
      end
    end
  end

  // sideband through S1..S4 (S4 output registers: s4_*)
  logic [3:1]       pass_q, solid_q;
  logic [3:1][7:0]  fx_q, fy_q;
  logic [3:1][TAG_W-1:0] tag_q;
  logic [3:1][1:0]  plane_q;
  logic [1:0]       s4_plane, s5_plane;
  logic             s4_pass, s4_solid;
  logic [7:0]       s4_fx, s4_fy;
  logic [TAG_W-1:0] s4_tag;

  always_ff @(posedge clk_i) begin
    if (en) begin
      s0_bil_q1 <= s0_bil;
      s0_bil_q2 <= s0_bil_q1;
      s0_bil_q3 <= s0_bil_q2;
      pass_q    <= {pass_q[2:1], s0_pass};
      solid_q   <= {solid_q[2:1], s0_solid};
      fx_q      <= {fx_q[2:1], s0_fx};
      fy_q      <= {fy_q[2:1], s0_fy};
      tag_q     <= {tag_q[2:1], s0_tag};
      plane_q   <= {plane_q[2:1], s0_plane};
      s4_plane  <= plane_q[3];
      s4_pass   <= pass_q[3];
      s4_solid  <= solid_q[3];
      s4_fx     <= fx_q[3];
      s4_fy     <= fy_q[3];
      s4_tag    <= tag_q[3];
    end
  end

  // ---------------------------------------------------------------- S5: row offsets, column bits
  logic             s5_pass, s5_solid;
  logic [29:0]      s5_row0, s5_row1;
  logic [1:0][18:0] s5_col;    // column offset in bits of x0, x1
  logic [1:0]       s5_ypar;
  logic [1:0]       s5_xpar;     // luma column parity of x0, x1 (YUY2)
  // plane of the sub-request at S4: subsampled coordinates, stride, log2 bits
  logic [1:0][13:0] px, py;      // [0] = c0, [1] = c1
  logic [15:0]      pstride;
  logic [2:0]       plg;
  logic             pxs, pys;
  always_comb begin
    unique case (s4_plane)
      2'd1:    begin pstride = cfg_pstride_i[0]; plg = cfg_plg_i[0]; pxs = cfg_pxs_i[0]; pys = cfg_pys_i[0]; end
      2'd2:    begin pstride = cfg_pstride_i[1]; plg = cfg_plg_i[1]; pxs = cfg_pxs_i[1]; pys = cfg_pys_i[1]; end
      default: begin pstride = cfg_stride_i;     plg = cfg_lg_bits_i; pxs = cfg_p0_xs_i; pys = 1'b0;     end
    endcase
    px[0] = s4_c0[0] >> pxs;
    px[1] = s4_c1[0] >> pxs;
    py[0] = s4_c0[1] >> pys;
    py[1] = s4_c1[1] >> pys;
  end
  logic [3:0]       s5_border;
  logic [7:0]       s5_fx, s5_fy;
  logic [TAG_W-1:0] s5_tag;

  always_ff @(posedge clk_i) begin
    if (en) begin
      s5_pass   <= s4_pass;
      s5_solid  <= s4_solid;
      s5_row0   <= 30'(py[0]) * 30'(pstride);
      s5_row1   <= 30'(py[1]) * 30'(pstride);
      s5_col[0] <= cfg_b24_i ? 19'(px[0]) * 19'd24 : 19'(px[0]) << plg;
      s5_col[1] <= cfg_b24_i ? 19'(px[1]) * 19'd24 : 19'(px[1]) << plg;
      s5_ypar   <= {py[1][0], py[0][0]};
      s5_xpar   <= {s4_c1[0][0], s4_c0[0][0]};
      s5_plane  <= s4_plane;
      // t00 = (x0,y0), t10 = (x1,y0), t01 = (x0,y1), t11 = (x1,y1)
      s5_border <= {s4_b1[0] | s4_b1[1], s4_b0[0] | s4_b1[1], s4_b1[0] | s4_b0[1], s4_b0[0] | s4_b0[1]};
      s5_fx     <= s4_fx;
      s5_fy     <= s4_fy;
      s5_tag    <= s4_tag;
    end
  end

  // ---------------------------------------------------------------- S6: byte addresses, sub-byte shifts
  // Sub-byte pixel at bit offset o of its byte (o = (x * bits) mod 8): SUB_ORDER 0 -> shift o,
  // SUB_ORDER 1 -> shift 8 - bits - o. Formats of 8 bits or more: shift 0.
  function automatic logic [2:0] sub_shift(logic [2:0] o, logic [2:0] lgb, logic hi_first);
    logic [3:0] bits;
    bits = 4'd1 << lgb;
    if (lgb >= 3'd3) return 3'd0;
    return hi_first ? 3'(4'd8 - bits - 4'(o)) : o;
  endfunction

  logic [31:0] r0, r1, pbase;
  logic [3:0][31:0] a;
  assign pbase = (s5_plane == 2'd1) ? cfg_paddr_i[0] : (s5_plane == 2'd2) ? cfg_paddr_i[1] : cfg_addr_i;
  assign r0 = pbase + 32'(s5_row0);
  assign r1 = pbase + 32'(s5_row1);
  assign a[0] = r0 + 32'(s5_col[0][18:3]);
  assign a[1] = r0 + 32'(s5_col[1][18:3]);
  assign a[2] = r1 + 32'(s5_col[0][18:3]);
  assign a[3] = r1 + 32'(s5_col[1][18:3]);

  // 24-bit (v1.6b): bytes of the texel in its first line when it crosses the line end (offset 63: 1, 62: 2),
  // else 0 (g2d_tex_split / g2d_tex_merge)
  function automatic logic [2:0] b24_low(logic [5:0] off);
    return (off == 6'd63) ? 3'd1 : (off == 6'd62) ? 3'd2 : 3'd0;
  endfunction

  always_ff @(posedge clk_i) begin
    if (en) begin
      out_pass_o    <= s5_pass;
      out_solid_o   <= s5_solid;
      out_addr_o    <= a;
      for (int s = 0; s < 4; s++)
        out_sh_o[s] <= cfg_b24_i ? b24_low(a[s][5:0]) :
                       cfg_yuy2_i ? {2'b0, s5_xpar[s % 2]} :                   // YUY2: Y0 / Y1 (v1.6b)
                       sub_shift(s5_col[s % 2][2:0], cfg_lg_bits_i, cfg_sub_order_i);
      out_ypar_o    <= s5_ypar;
      out_border_o  <= s5_border;
      out_fx_o      <= s5_fx;
      out_fy_o      <= s5_fy;
      out_tag_o     <= s5_tag;
    end
  end

  logic unused;
  assign unused = ^{up[7:0], vp[7:0]};   // below the 8-bit fraction

endmodule
