// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Texel merge and sub-byte extraction after the source cache (v1.4). One pipeline register.
//
// Reassembles the quads g2d_tex_split issued as single-texel requests: the texels of a request that is not
// the last of its quad are held; the last request completes the quad (texel s from the request whose smask
// holds s). A quad issued whole (smask = 1111) passes in one step. Then each texel is extracted (rendering.md
// §3.5): formats with fewer than 8 bits take bits [sh + bits - 1 : sh] of the texel's byte, right-aligned
// (sh from g2d_tex_addr); other formats pass unchanged.
// v1.6b, 24-bit sources: a texel that crosses a line arrives in two requests (g2d_tex_split): the low request
// brings the bytes up to the line end (zero beyond), the high request (in_hi_i) the next line from offset 0. sh
// holds the number of low bytes (1 or 2; 0 = the texel does not cross) and the parts are joined here.
// v1.6b sub-requests (g2d_tex_seq): the quad of a sub-request is handed on only when in_emit_i is set (its last
// request carries in_last_i); the tag (with the Gaussian group and the pixel's last flag) passes through.
// v1.6b YUV (rendering.md §3.7): each plane is one sub-request; its completed texels fill the per-texel word
// {A, Y, U, V} (plane 0: Y, and U / V of a YUY2 pair, Y0 / Y1 by the column parity in sh; plane 1: U, V or U;
// plane 2: V or A), which is handed on (U, V exchanged under UV_SWAP) with the last plane.

module g2d_tex_merge #(
  parameter int unsigned TAG_W = 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic [2:0]        cfg_lg_bits_i,     // log2 bits per texel
  input  logic [1:0]        cfg_yuv_i,         // YUV class (v1.6b): 0 none, 1 YUY2, 2 semi-planar, 3 three planes
  input  logic              cfg_yuv_alpha_i,   // plane 2 is the alpha plane
  input  logic              cfg_uv_swap_i,
  // in (from g2d_src_cache)
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_pass_i,
  input  logic [3:0]        in_smask_i,
  input  logic              in_last_i,
  input  logic              in_hi_i,           // high part of 24-bit texels (v1.6b)
  input  logic              in_emit_i,         // last sub-request of a group: hand the quad on (v1.6b)
  input  logic [1:0]        in_plane_i,        // YUV plane of the sub-request (v1.6b)
  input  logic [3:0][31:0]  in_tex_i,
  input  logic [3:0][2:0]   in_sh_i,
  input  logic [7:0]        in_fx_i,
  input  logic [7:0]        in_fy_i,
  input  logic [TAG_W-1:0]  in_tag_i,
  // out (to g2d_texel / the CLUT lookup)
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_pass_o,
  output logic [3:0][31:0]  out_tex_o,
  output logic [7:0]        out_fx_o,
  output logic [7:0]        out_fy_o,
  output logic [TAG_W-1:0]  out_tag_o
);

  logic [3:0][31:0] hold_q;
  logic [3:0][31:0] ayuv_q;      // YUV texels being assembled: A, Y, U, V
  logic             v_q, emit;

  logic done;                                   // the request completes a quad that is handed on
  assign done        = in_last_i & in_emit_i;
  assign emit        = in_valid_i & done;
  assign in_ready_o  = ~done | ~v_q | out_ready_i;
  assign out_valid_o = v_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)              v_q <= 1'b0;
    else if (clr_i)           v_q <= 1'b0;
    else if (~v_q | out_ready_i) v_q <= emit;
  end

  // sub-byte extraction mask: 2^bits - 1
  logic [7:0] mask;
  always_comb begin
    unique case (cfg_lg_bits_i)
      3'd0:    mask = 8'h01;
      3'd1:    mask = 8'h03;
      3'd2:    mask = 8'h0F;
      default: mask = 8'hFF;
    endcase
  end

  always_ff @(posedge clk_i) begin
    if (in_valid_i && in_ready_o) begin
      for (int s = 0; s < 4; s++) begin
        logic [31:0] t, w;
        if (!in_smask_i[s])           t = hold_q[s];
        else if (!in_hi_i)            t = in_tex_i[s];
        else if (in_sh_i[s] == 3'd1)  t = {8'd0, in_tex_i[s][15:0], hold_q[s][7:0]};
        else                          t = {8'd0, in_tex_i[s][7:0], hold_q[s][15:0]};
        // YUV: the plane's bytes into {A, Y, U, V}
        w = (in_plane_i == 2'd0) ? 32'hFF00_0000 : ayuv_q[s];
        unique case (in_plane_i)
          2'd0: begin
            w[23:16] = (cfg_yuv_i == 2'd1 && in_sh_i[s][0]) ? t[23:16] : t[7:0];
            if (cfg_yuv_i == 2'd1) w[15:0] = {t[15:8], t[31:24]};
          end
          2'd1:    if (cfg_yuv_i == 2'd2) w[15:0] = {t[7:0], t[15:8]};     // U V plane: byte 0 U, byte 1 V
                   else                   w[15:8] = t[7:0];               // U plane
          default: if (cfg_yuv_alpha_i)   w[31:24] = t[7:0];
                   else                   w[7:0] = t[7:0];
        endcase
        if (!done) begin
          if (in_smask_i[s]) hold_q[s] <= t;
          if (in_last_i)     ayuv_q[s] <= w;
        end else if (cfg_yuv_i != 2'd0) begin
          out_tex_o[s] <= cfg_uv_swap_i ? {w[31:16], w[7:0], w[15:8]} : w;
        end else begin
          out_tex_o[s] <= (cfg_lg_bits_i < 3'd3) ? {24'd0, (t[7:0] >> in_sh_i[s]) & mask} : t;
        end
      end
      if (done) begin
        out_pass_o <= in_pass_i;
        out_fx_o   <= in_fx_i;
        out_fy_o   <= in_fy_i;
        out_tag_o  <= in_tag_i;
      end
    end
  end

endmodule
