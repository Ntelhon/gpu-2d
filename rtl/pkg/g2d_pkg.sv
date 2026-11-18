// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D common package: format helpers and integer arithmetic shared by the pipeline blocks.
// Arithmetic follows spec/rendering.md §2–3 bit-exactly.

package g2d_pkg;

  // Command opcodes (spec/commands.md §1).
  localparam logic [7:0] OP_NOP        = 8'h00;
  localparam logic [7:0] OP_LOAD_STATE = 8'h01;
  localparam logic [7:0] OP_DRAW_RECT  = 8'h10;
  localparam logic [7:0] OP_DRAW_PATH  = 8'h11;
  localparam logic [7:0] OP_FENCE      = 8'h20;

  // PAINT_CTRL.SRC values (rendering.md §5.1).
  localparam logic [1:0] PAINT_SOLID = 2'd0;
  localparam logic [1:0] PAINT_IMAGE = 2'd1;
  localparam logic [1:0] PAINT_GRAD  = 2'd2;
  localparam logic [1:0] PAINT_RADIAL = 2'd3;   // v1.5, rendering.md §10.4

  // AXI response codes.
  localparam logic [1:0] AXI_RESP_OKAY   = 2'b00;
  localparam logic [1:0] AXI_RESP_SLVERR = 2'b10;
  localparam logic [1:0] AXI_RESP_DECERR = 2'b11;

  // Halting error causes reported by the core to g2d_ctrl (ERR_STATUS bit order).
  typedef struct packed {
    logic axi_wr;
    logic axi_rd;
    logic addr_range;
    logic bad_state;
    logic cmd_decode;
  } g2d_err_t;

  // ---- optional units (v1.7, arch.md §3, parameter FEATURES; HWCFG.FEAT_*) ----
  localparam int unsigned FEAT_FAMILY = 0;   // blend families OpenVG / SVG2 / Porter-Duff
  localparam int unsigned FEAT_GAUSS  = 1;   // Gaussian filter
  localparam int unsigned FEAT_YUV    = 2;   // YUV sources
  localparam int unsigned FEAT_COLOR  = 3;   // colour unit
  localparam int unsigned FEAT_RADIAL = 4;   // radial gradient
  localparam int unsigned FEAT_MASK   = 5;   // mask layer
  localparam logic [5:0]  FEAT_ALL    = 6'b11_1111;

  // ---- pixel formats (rendering.md §3.2) ----

  // True for every FMT code of rendering.md §3.2 (targets and the source-only codes).
  function automatic logic fmt_valid(logic [7:0] fmt);
    unique case (fmt)
      8'h00, 8'h01, 8'h02, 8'h12,
      8'h03, 8'h13, 8'h23, 8'h33, 8'h43, 8'h53, 8'h63, 8'h73,
      8'h04, 8'h14, 8'h24, 8'h34, 8'h05, 8'h15, 8'h25, 8'h35, 8'h06, 8'h16, 8'h26, 8'h36,
      8'h07, 8'h08, 8'h09, 8'h0A, 8'h0B, 8'h0C, 8'h0D, 8'h0E,
      8'h0F,                                                                        // A8L8 (v1.6)
      8'h80, 8'h90, 8'h81, 8'h91, 8'h82, 8'h92,                                     // 24-bit (v1.6b)
      8'h83, 8'hC3, 8'h84, 8'hC4, 8'h94, 8'hA4, 8'h85, 8'h95, 8'hA5: return 1'b1;   // YUV (v1.6b)
      default: return 1'b0;
    endcase
  endfunction

  // Source-only classes (v1.4): A4 A2 A1 L4 (sub-byte) and I8 I4 I2 I1 (indexed); v1.6: A8L8; v1.6b: every
  // extended code (FMT bit 7: 24-bit, YUV). Valid codes only.
  function automatic logic fmt_src_only(logic [7:0] fmt);
    return fmt[7] || fmt[3:0] >= 4'h7;
  endfunction

  // YUV sources (v1.6b, extended classes 3..5, rendering.md §3.7).
  function automatic logic fmt_yuv(logic [7:0] fmt);
    return fmt[7] && fmt[3:0] >= 4'h3 && fmt[3:0] <= 4'h5;
  endfunction

  // (t >>> 10) clamped to [0, 255]
  function automatic logic [7:0] sat_c(logic signed [22:0] t);
    logic signed [12:0] q;
    q = 13'(t >>> 10);
    return (q < 0) ? 8'd0 : (q > 13'sd255) ? 8'd255 : q[7:0];
  endfunction

  // §3.7 conversion of an assembled YUV texel {A, Y, U, V}, limited range: (K * x + 512) >> 10, clamped.
  function automatic logic [31:0] yuv_expand(logic [31:0] ayuv, logic bt709);
    logic signed [9:0]  y, u, v;
    logic signed [22:0] r, g, b;
    logic [11:0] krv, kgu, kgv, kbu;
    y   = $signed({2'b0, ayuv[23:16]}) - 10'sd16;
    u   = $signed({2'b0, ayuv[15:8]}) - 10'sd128;
    v   = $signed({2'b0, ayuv[7:0]}) - 10'sd128;
    krv = bt709 ? 12'd1836 : 12'd1634;
    kgu = bt709 ? 12'd218  : 12'd401;
    kgv = bt709 ? 12'd546  : 12'd832;
    kbu = bt709 ? 12'd2163 : 12'd2066;
    r = 23'sd1192 * 23'(y) + $signed({11'b0, krv}) * 23'(v) + 23'sd512;
    g = 23'sd1192 * 23'(y) - $signed({11'b0, kgu}) * 23'(u) - $signed({11'b0, kgv}) * 23'(v) + 23'sd512;
    b = 23'sd1192 * 23'(y) + $signed({11'b0, kbu}) * 23'(u) + 23'sd512;
    return {ayuv[31:24], sat_c(r), sat_c(g), sat_c(b)};
  endfunction

  // 24-bit sources (v1.6b, extended classes 0..2): 3-byte texels.
  function automatic logic fmt_b24(logic [7:0] fmt);
    return fmt[7] && fmt[3:0] <= 4'h2;
  endfunction

  function automatic logic fmt_tgt_valid(logic [7:0] fmt);
    return fmt_valid(fmt) && !fmt_src_only(fmt);
  endfunction

  function automatic logic fmt_indexed(logic [7:0] fmt);
    return !fmt[7] && fmt[3:0] >= 4'hB && fmt[3:0] <= 4'hE;
  endfunction

  // log2 of the pixel size in bits (0..5). Only meaningful for valid formats; 24-bit (v1.6b): 5 (the texel is
  // read as 32 bits, its address comes from fmt_b24).
  function automatic logic [2:0] fmt_lg_bits(logic [7:0] fmt);
    if (fmt[7]) return 3'd5;
    unique case (fmt[3:0])
      4'h0, 4'h1, 4'h6, 4'hB: return 3'd3;   // 8 bit
      4'h2, 4'h4, 4'h5, 4'hF: return 3'd4;   // 16 bit (A8L8, v1.6)
      4'h3:                   return 3'd5;   // 32 bit
      4'h7, 4'hA, 4'hC:       return 3'd2;   // 4 bit
      4'h8, 4'hD:             return 3'd1;   // 2 bit
      default:                return 3'd0;   // 1 bit (A1, I1)
    endcase
  endfunction

  // log2 of the pixel size in bytes (0, 1, 2) for formats of 8 bits or more (every target format).
  function automatic logic [1:0] fmt_lg_bytes(logic [7:0] fmt);
    logic [2:0] lb;
    lb = fmt_lg_bits(fmt);
    return (lb >= 3'd3) ? 2'(lb - 3'd3) : 2'd0;
  endfunction

  // ---- integer helpers (rendering.md §2) ----

  // Rounded division by 255 for v <= 65535.
  function automatic logic [7:0] div255r(logic [15:0] v);
    logic [16:0] t;
    logic [17:0] s;
    t = {1'b0, v} + 17'd128;
    s = {1'b0, t} + {9'd0, t[16:8]};
    return s[15:8];
  endfunction

  function automatic logic [7:0] mul8(logic [7:0] a, logic [7:0] b);
    return div255r(16'(a) * 16'(b));
  endfunction

  // floor(v / 255) for v < 2^16 - 1: (v + 1 + (v >> 8)) >> 8.
  function automatic logic [7:0] div255f(logic [15:0] v);
    logic [16:0] s;
    s = {1'b0, v} + 17'd1 + {9'd0, v[15:8]};
    return s[15:8];
  endfunction

  // Global alpha on the A channel (rendering.md §5.3): 0 NORMAL, 1 GLOBAL, 2 SCALED.
  function automatic logic [31:0] apply_global_alpha(logic [31:0] argb, logic [1:0] mode, logic [7:0] g);
    logic [31:0] o;
    o = argb;
    if (mode == 2'd1)      o[31:24] = g;
    else if (mode == 2'd2) o[31:24] = mul8(argb[31:24], g);
    return o;
  endfunction

  // Packed 16/8-bit classes with four channel orders (rendering.md §3.2, v1.4): the bit position of each
  // channel. order: 0 R,G,B,A  1 B,G,R,A  2 A,R,G,B  3 A,B,G,R (from bit 0); cw / aw colour / alpha widths.
  typedef struct packed {
    logic [3:0] pa, pr, pg, pb;
  } pos_t;

  function automatic pos_t packed_pos(logic [1:0] order, logic [2:0] cw, logic [2:0] aw);
    pos_t p;
    logic [3:0] c1, c2, a1;
    c1 = {1'b0, cw};
    c2 = {cw, 1'b0};
    a1 = {1'b0, aw};
    unique case (order)
      2'd0:    p = '{pr: 4'd0, pg: c1, pb: c2, pa: c2 + c1};
      2'd1:    p = '{pb: 4'd0, pg: c1, pr: c2, pa: c2 + c1};
      2'd2:    p = '{pa: 4'd0, pr: a1, pg: a1 + c1, pb: a1 + c2};
      default: p = '{pa: 4'd0, pb: a1, pg: a1 + c1, pr: a1 + c2};
    endcase
    return p;
  endfunction

  // §3.3 bit replication of an n-bit field (n = 1, 2, 4, 5, 6) to 8 bits.
  function automatic logic [7:0] rep_n(logic [5:0] c, logic [2:0] n);
    unique case (n)
      3'd1:    return {8{c[0]}};
      3'd2:    return {4{c[1:0]}};
      3'd4:    return {2{c[3:0]}};
      3'd5:    return {c[4:0], c[4:2]};
      default: return {c[5:0], c[5:4]};
    endcase
  endfunction

  // 2^n - 1 for the channel widths n = 1, 2, 4, 5, 6.
  function automatic logic [5:0] max_n(logic [2:0] n);
    unique case (n)
      3'd1:    return 6'd1;
      3'd2:    return 6'd3;
      3'd4:    return 6'd15;
      3'd5:    return 6'd31;
      default: return 6'd63;
    endcase
  endfunction

  function automatic logic [5:0] field_at(logic [15:0] raw, logic [3:0] pos, logic [2:0] w);
    logic [15:0] t;
    t = raw >> pos;
    return t[5:0] & max_n(w);
  endfunction

  // Stored pixel (little-endian value of its bytes, right-aligned in 32 bits; sub-byte formats: the pixel's
  // bits right-aligned; indexed formats: the CLUT word) -> ARGB32 (rendering.md §3.3).
  function automatic logic [31:0] fmt_expand(logic [7:0] fmt, logic [31:0] raw);
    logic [7:0] lo5, mid6, hi5, a, l4;
    logic [2:0] cw, aw;
    pos_t       p;
    logic [31:0] o;
    lo5  = {raw[4:0], raw[4:2]};
    mid6 = {raw[10:5], raw[10:9]};
    hi5  = {raw[15:11], raw[15:13]};
    l4   = {2{raw[3:0]}};
    cw   = (fmt[3:0] == 4'h5) ? 3'd5 : (fmt[3:0] == 4'h4) ? 3'd4 : 3'd2;
    aw   = (fmt[3:0] == 4'h5) ? 3'd1 : cw;
    p    = packed_pos(fmt[5:4], cw, aw);
    o    = '0;
    if (fmt_yuv(fmt)) return yuv_expand(raw, 1'b0);      // §3.7 (v1.6b): the assembled {A, Y, U, V} texel
    if (fmt[7]) begin                                     // 24-bit (v1.6b): order 0 = R in the lowest field
      logic [15:0] c;
      logic [7:0]  a8, l5, m6, h5;
      c  = (fmt[3:0] == 4'h1) ? raw[23:8] : raw[15:0];
      a8 = (fmt[3:0] == 4'h1) ? raw[7:0] : raw[23:16];
      l5 = {c[4:0], c[4:2]};
      m6 = {c[10:5], c[10:9]};
      h5 = {c[15:11], c[15:13]};
      if (fmt[3:0] == 4'h0) o = fmt[4] ? {8'hFF, raw[23:16], raw[15:8], raw[7:0]} : {8'hFF, raw[7:0], raw[15:8], raw[23:16]};
      else                  o = fmt[4] ? {a8, h5, m6, l5} : {a8, l5, m6, h5};
      return o;
    end
    unique case (fmt[3:0])
      4'h0: o = {raw[7:0], 24'hFFFFFF};
      4'h1: o = {8'hFF, raw[7:0], raw[7:0], raw[7:0]};
      4'h2: o = (fmt[5:4] == 2'd0) ? {8'hFF, lo5, mid6, hi5} : {8'hFF, hi5, mid6, lo5};
      4'h4, 4'h5, 4'h6:
            o = {rep_n(field_at(raw[15:0], p.pa, aw), aw), rep_n(field_at(raw[15:0], p.pr, cw), cw),
                 rep_n(field_at(raw[15:0], p.pg, cw), cw), rep_n(field_at(raw[15:0], p.pb, cw), cw)};
      4'h7: o = {l4, 24'hFFFFFF};                         // A4
      4'h8: o = {{4{raw[1:0]}}, 24'hFFFFFF};              // A2
      4'h9: o = {{8{raw[0]}}, 24'hFFFFFF};                // A1
      4'hA: o = {8'hFF, l4, l4, l4};                      // L4
      4'hB, 4'hC, 4'hD, 4'hE: o = raw;                    // indexed: the CLUT entry (§3.5)
      4'hF: o = {raw[7:0], raw[15:8], raw[15:8], raw[15:8]};   // A8L8: A bits 7:0, L bits 15:8 (v1.6)
      default: begin
        unique case (fmt[5:4])
          2'd0:    begin a = raw[31:24]; o = {a, raw[7:0],   raw[15:8],  raw[23:16]}; end  // R,G,B,A
          2'd1:    begin a = raw[31:24]; o = {a, raw[23:16], raw[15:8],  raw[7:0]};   end  // B,G,R,A
          2'd2:    begin a = raw[7:0];   o = {a, raw[15:8],  raw[23:16], raw[31:24]}; end  // A,R,G,B
          default: begin a = raw[7:0];   o = {a, raw[31:24], raw[23:16], raw[15:8]};  end  // A,B,G,R
        endcase
        if (fmt[6]) o[31:24] = 8'hFF;
      end
    endcase
    return o;
  endfunction

  // §3.4 rounding of an 8-bit channel to n bits (n = 1, 2, 4, 5, 6): (c * (2^n - 1) + 127) / 255; with dither
  // (§3.6, d = Bayer value 0..15): floor((32 c (2^n - 1) + 255 (2d + 1)) / 8160), computed as
  // q = floor(A / 255), r = A - 255 q (A = c (2^n - 1)), result q + (32 r + 255 (2d + 1) >= 8160).
  function automatic logic [5:0] pack_n(logic [7:0] c, logic [2:0] n, logic dith, logic [3:0] d);
    logic [13:0] a;
    logic [7:0]  q;
    logic [7:0]  r;
    logic [13:0] t;
    a = 14'(c) * 14'(max_n(n));
    if (!dith) return 6'(div255f(16'(a) + 16'd127));
    q = div255f(16'(a));
    r = 8'(a - 14'(q) * 14'd255);
    t = 14'({r, 5'd0}) + 14'd255 * 14'({d, 1'b1});
    return 6'(q) + 6'(t >= 14'd8160);
  endfunction

  // Bayer value of target pixel (x & 3, y & 3) (rendering.md §3.6).
  function automatic logic [3:0] bayer(logic [1:0] x, logic [1:0] y);
    logic [3:0] row;
    unique case ({y, x})
      4'h0: row = 4'd0;  4'h1: row = 4'd8;  4'h2: row = 4'd2;  4'h3: row = 4'd10;
      4'h4: row = 4'd12; 4'h5: row = 4'd4;  4'h6: row = 4'd14; 4'h7: row = 4'd6;
      4'h8: row = 4'd3;  4'h9: row = 4'd11; 4'hA: row = 4'd1;  4'hB: row = 4'd9;
      4'hC: row = 4'd15; 4'hD: row = 4'd7;  4'hE: row = 4'd13; default: row = 4'd5;
    endcase
    return row;
  endfunction

  // ARGB32 -> stored pixel, right-aligned in 32 bits (rendering.md §3.4); dith applies the §3.6 dither with
  // Bayer value d to the R, G, B channels packed to fewer than 8 bits. Target formats only.
  // §3.4 / §3.6 in two parts (v1.7, so a pipeline can register in between): fmt_pack rounds every channel to the
  // widths the format needs (with dither), fmt_assemble places the bits; fmt_compress_d is their composition.
  typedef struct packed {
    logic [5:0] r5, g6, b5;      // 565
    logic [5:0] pa, pr, pg, pb;  // 4444 / 5551 / 2222 channels
    logic [7:0] l;               // L8
  } fmt_pk_t;

  function automatic fmt_pk_t fmt_pack(logic [7:0] fmt, logic [31:0] argb, logic dith, logic [3:0] d);
    fmt_pk_t     k;
    logic [17:0] l;
    logic [2:0]  cw, aw;
    cw   = (fmt[3:0] == 4'h5) ? 3'd5 : (fmt[3:0] == 4'h4) ? 3'd4 : 3'd2;
    aw   = (fmt[3:0] == 4'h5) ? 3'd1 : cw;
    k.r5 = pack_n(argb[23:16], 3'd5, dith, d);
    k.g6 = pack_n(argb[15:8], 3'd6, dith, d);
    k.b5 = pack_n(argb[7:0], 3'd5, dith, d);
    k.pa = pack_n(argb[31:24], aw, 1'b0, 4'd0);
    k.pr = pack_n(argb[23:16], cw, dith, d);
    k.pg = pack_n(argb[15:8], cw, dith, d);
    k.pb = pack_n(argb[7:0], cw, dith, d);
    l    = 18'(argb[23:16]) * 18'd77 + 18'(argb[15:8]) * 18'd150 + 18'(argb[7:0]) * 18'd29 + 18'd128;
    k.l  = l[15:8];
    return k;
  endfunction

  function automatic logic [31:0] fmt_assemble(logic [7:0] fmt, logic [31:0] argb, fmt_pk_t k);
    logic [7:0]  a, r, g, b;
    logic [2:0]  cw, aw;
    pos_t        p;
    logic [15:0] pk;
    logic [31:0] o;
    a  = fmt[6] ? 8'hFF : argb[31:24];
    r  = argb[23:16];
    g  = argb[15:8];
    b  = argb[7:0];
    cw = (fmt[3:0] == 4'h5) ? 3'd5 : (fmt[3:0] == 4'h4) ? 3'd4 : 3'd2;
    aw = (fmt[3:0] == 4'h5) ? 3'd1 : cw;
    p  = packed_pos(fmt[5:4], cw, aw);
    pk = (16'(k.pa) << p.pa) | (16'(k.pr) << p.pr) | (16'(k.pg) << p.pg) | (16'(k.pb) << p.pb);
    o  = '0;
    unique case (fmt[3:0])
      4'h0: o[7:0] = argb[31:24];
      4'h1: o[7:0] = k.l;
      4'h2: o[15:0] = (fmt[5:4] == 2'd0) ? {k.b5[4:0], k.g6[5:0], k.r5[4:0]}
                                         : {k.r5[4:0], k.g6[5:0], k.b5[4:0]};
      4'h4, 4'h5: o[15:0] = pk;
      4'h6: o[7:0] = pk[7:0];
      default: begin
        unique case (fmt[5:4])
          2'd0: o = {a, b, g, r};  // RGBA: byte0 R
          2'd1: o = {a, r, g, b};  // BGRA: byte0 B
          2'd2: o = {b, g, r, a};  // ARGB: byte0 A
          default: o = {r, g, b, a};  // ABGR: byte0 A, byte1 B
        endcase
      end
    endcase
    return o;
  endfunction

  function automatic logic [31:0] fmt_compress_d(logic [7:0] fmt, logic [31:0] argb, logic dith, logic [3:0] d);
    return fmt_assemble(fmt, argb, fmt_pack(fmt, argb, dith, d));
  endfunction

  function automatic logic [31:0] fmt_compress(logic [7:0] fmt, logic [31:0] argb);
    return fmt_compress_d(fmt, argb, 1'b0, 4'd0);
  endfunction

endpackage
