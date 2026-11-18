// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Texel sub-request sequencer (v1.6b, arch.md §6.4), in front of g2d_tex_addr. One pipeline register.
//
// Turns one pixel (u, v, ok, tag from g2d_xform) into the sub-requests the texel path needs; each sub-request is a
// 2 x 2 cross product of two columns and two rows (the shape g2d_tex_addr / g2d_src_cache already serve):
//   * Gaussian filter (cfg_gauss_i, rendering.md §7.3): four groups around the point-sampled texel (x, y):
//       group 0: columns x-1, x   rows y-1, y   texels 1111  weights W2 W1 / W1 W0
//       group 1: column  x+1      rows y-1, y   texels 0101  (t00, t01)  W2 / W1
//       group 2: columns x-1, x   row  y+1      texels 0011  (t00, t10)  W2 W1
//       group 3: column  x+1      row  y+1      texels 0001  W2
//     out_dx_o / out_dy_o: offset of the first column / row from (x, y) (-1 or +1), the second is one further.
//   * YUV sources (rendering.md §3.7): one sub-request per plane in cfg_planes_i (bit p = plane p is read).
//   * otherwise one sub-request (plane 0, no offset): no change in throughput.
// out_gmask_o: the texels this sub-request needs (g2d_tex_split issues only those), out_grp_o: Gaussian group,
// out_emit_o: last sub-request of a group (g2d_tex_merge hands the group to the texel unit), out_last_o: last of
// the pixel (the texel unit outputs the pixel).

module g2d_tex_seq #(
  parameter int unsigned TAG_W = 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              cfg_gauss_i,
  input  logic [2:0]        cfg_planes_i,      // planes read per pixel (bit 0 always set)
  // in (from g2d_xform)
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_ok_i,
  input  logic [31:0]       in_u_i,
  input  logic [31:0]       in_v_i,
  input  logic [TAG_W-1:0]  in_tag_i,
  // out (to g2d_tex_addr)
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_ok_o,
  output logic [31:0]       out_u_o,
  output logic [31:0]       out_v_o,
  output logic [TAG_W-1:0]  out_tag_o,
  output logic [1:0]        out_plane_o,
  output logic signed [1:0] out_dx_o,
  output logic signed [1:0] out_dy_o,
  output logic [3:0]        out_gmask_o,
  output logic [1:0]        out_grp_o,
  output logic              out_emit_o,
  output logic              out_last_o
);

  logic       v_q, fire, done;
  logic [1:0] k_q;           // Gaussian group, or plane

  // next plane after k_q among cfg_planes_i
  logic       more_pl;
  logic [1:0] pl_nx;
  always_comb begin
    more_pl = 1'b0;
    pl_nx   = k_q;
    for (int p = 2; p >= 0; p--) begin
      if (2'(p) > k_q && cfg_planes_i[p]) begin
        more_pl = 1'b1;
        pl_nx   = 2'(p);
      end
    end
  end

  assign out_valid_o = v_q;
  assign out_plane_o = cfg_gauss_i ? 2'd0 : k_q;
  assign out_grp_o   = cfg_gauss_i ? k_q : 2'd0;
  assign out_dx_o    = !cfg_gauss_i ? 2'sd0 : (k_q[0] ? 2'sd1 : -2'sd1);
  assign out_dy_o    = !cfg_gauss_i ? 2'sd0 : (k_q[1] ? 2'sd1 : -2'sd1);
  always_comb begin
    unique case ({cfg_gauss_i, k_q})
      3'b100:  out_gmask_o = 4'b1111;
      3'b101:  out_gmask_o = 4'b0101;
      3'b110:  out_gmask_o = 4'b0011;
      3'b111:  out_gmask_o = 4'b0001;
      default: out_gmask_o = 4'b1111;
    endcase
  end
  assign out_last_o  = cfg_gauss_i ? (k_q == 2'd3) : ~more_pl;
  assign out_emit_o  = cfg_gauss_i | ~more_pl;
  assign fire        = v_q & out_ready_i;
  assign done        = fire & out_last_o;
  assign in_ready_o  = ~v_q | done;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      v_q <= 1'b0;
      k_q <= '0;
    end else if (clr_i) begin
      v_q <= 1'b0;
      k_q <= '0;
    end else begin
      if (in_ready_o) v_q <= in_valid_i;
      if (done)       k_q <= '0;
      else if (fire)  k_q <= cfg_gauss_i ? k_q + 2'd1 : pl_nx;
    end
  end

  always_ff @(posedge clk_i) begin
    if (in_ready_o && in_valid_i) begin
      out_ok_o  <= in_ok_i;
      out_u_o   <= in_u_i;
      out_v_o   <= in_v_i;
      out_tag_o <= in_tag_i;
    end
  end

endmodule
