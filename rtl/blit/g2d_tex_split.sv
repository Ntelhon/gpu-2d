// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Texel request split in front of the source cache (v1.4, arch.md §6.4). One pipeline register.
//
// g2d_src_cache serves at most one line per bank per request; its bank is {row parity, line parity}, which
// keeps the four texels of a bilinear quad conflict-free as long as x1 - x0, y1 - y0 are 0 or 1. The wrap
// modes of rendering.md §7.5 break that at a wrap seam (x1 = sx0 next to x0 = sx1 - 1, rows likewise): two
// texels may then need different lines of one bank. Such a quad (pass = 1 and a conflict) is issued as
// single-texel requests (smask = 0001, 0010, 0100, 1000); every other quad as one request (smask = 1111).
//
// v1.6b, 24-bit sources (cfg_b24_i): a 3-byte texel at line offset 62 or 63 continues in the next line. The cache
// returns the bytes up to the line end (zero beyond), so such texels get a second, "high" request for the next
// line (address = next line start; same row, so the same row parity and the other line parity): one request with
// smask = the straddling texels, or single-texel requests if those high lines conflict (wrap seams). out_hi_o marks
// the high requests; g2d_tex_merge joins the parts (the per-texel byte count of the low part travels in the tag).
// out_last_o marks the last request of the quad. Everything else passes unchanged.
// v1.6b sub-requests (g2d_tex_seq): in_gmask_i names the texels the sub-request needs (Gaussian groups use 2 or 1
// of the four); only those are requested, checked for conflicts and, in single mode, issued one by one.

module g2d_tex_split #(
  parameter int unsigned TAG_W = 8
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              cfg_b24_i,         // 24-bit source (v1.6b)
  // in (from g2d_tex_addr)
  input  logic              in_valid_i,
  output logic              in_ready_o,
  input  logic              in_pass_i,
  input  logic [3:0]        in_gmask_i,        // texels of this sub-request (v1.6b)
  input  logic [3:0][31:0]  in_addr_i,
  input  logic [1:0]        in_ypar_i,
  input  logic [7:0]        in_fx_i,
  input  logic [7:0]        in_fy_i,
  input  logic [TAG_W-1:0]  in_tag_i,
  // out (to g2d_src_cache)
  output logic              out_valid_o,
  input  logic              out_ready_i,
  output logic              out_pass_o,
  output logic [3:0]        out_smask_o,
  output logic              out_last_o,
  output logic              out_hi_o,
  output logic [3:0][31:0]  out_addr_o,
  output logic [1:0]        out_ypar_o,
  output logic [7:0]        out_fx_o,
  output logic [7:0]        out_fy_o,
  output logic [TAG_W-1:0]  out_tag_o
);

  logic             v_q, hi_q;
  logic [1:0]       k_q;
  logic [3:0]       gmask_q, mcur;
  logic [3:0][31:0] addr_q;
  logic [3:0][31:0] hi_addr;
  logic [3:0]       strad;
  logic             conf_lo, conf_hi, conf, fire, done;
  logic [3:0]       sel;          // texels of this request in single mode
  logic [1:0]       k_nx;         // next texel of mcur after k_q (single mode)
  logic             more;         // single mode: another texel of mcur follows k_q

  // two texels of a request in one bank ({row parity, address bit 6}) on different 64-byte lines
  function automatic logic conflict(logic [3:0][31:0] a, logic [3:0] m, logic [1:0] yp);
    logic c;
    c = 1'b0;
    for (int s = 0; s < 4; s++) begin
      for (int t = s + 1; t < 4; t++) begin
        if (m[s] && m[t] && {yp[s / 2], a[s][6]} == {yp[t / 2], a[t][6]} && a[s][31:6] != a[t][31:6]) c = 1'b1;
      end
    end
    return c;
  endfunction

  always_comb begin
    for (int s = 0; s < 4; s++) begin
      strad[s]   = cfg_b24_i & out_pass_o & gmask_q[s] & (addr_q[s][5:1] == 5'h1F);   // line offset 62 or 63
      hi_addr[s] = {addr_q[s][31:6] + 26'd1, 6'd0};
    end
    conf_lo = out_pass_o & conflict(addr_q, gmask_q, out_ypar_o);
    conf_hi = conflict(hi_addr, strad, out_ypar_o);
    conf    = hi_q ? conf_hi : conf_lo;
    mcur    = hi_q ? strad : gmask_q;
    // next texel of mcur after k_q
    more = 1'b0;
    k_nx = k_q;
    for (int s = 3; s >= 0; s--) begin
      if (2'(s) > k_q && mcur[s]) begin
        more = 1'b1;
        k_nx = 2'(s);
      end
    end
    sel = 4'b0001 << k_q;
  end

  // first straddling texel (start of single high mode)
  logic [1:0] k_first;
  always_comb begin
    k_first = 2'd0;
    for (int s = 3; s >= 0; s--) if (strad[s]) k_first = 2'(s);
  end

  assign out_valid_o = v_q;
  assign out_hi_o    = hi_q;
  assign out_addr_o  = hi_q ? hi_addr : addr_q;
  assign out_smask_o = conf ? sel : mcur;
  // the low part ends at its last single (or the whole request); the quad ends there unless a high part follows
  logic lo_end;
  assign lo_end      = ~hi_q & (~conf | ~more);
  assign out_last_o  = hi_q ? (~conf | ~more) : (lo_end & ~|strad);
  assign fire        = v_q & out_ready_i;
  assign done        = fire & out_last_o;
  assign in_ready_o  = ~v_q | done;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      v_q  <= 1'b0;
      k_q  <= '0;
      hi_q <= 1'b0;
    end else if (clr_i) begin
      v_q  <= 1'b0;
      k_q  <= '0;
      hi_q <= 1'b0;
    end else begin
      if (in_ready_o) v_q <= in_valid_i;
      if (done) begin
        k_q  <= '0;
        hi_q <= 1'b0;
      end else if (fire) begin
        if (lo_end) begin                     // low part done, straddling texels follow
          hi_q <= 1'b1;
          k_q  <= k_first;
        end else begin
          k_q  <= k_nx;                       // single requests: the next texel of the current mask
        end
      end
    end
  end

  always_ff @(posedge clk_i) begin
    if (in_ready_o && in_valid_i) begin
      out_pass_o <= in_pass_i;
      gmask_q    <= in_gmask_i;
      addr_q     <= in_addr_i;
      out_ypar_o <= in_ypar_i;
      out_fx_o   <= in_fx_i;
      out_fy_o   <= in_fy_i;
      out_tag_o  <= in_tag_i;
    end
  end

endmodule
