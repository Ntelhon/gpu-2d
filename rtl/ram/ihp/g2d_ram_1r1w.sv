// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Two-port RAM (one read, one write port), IHP SG13G2 variant (v2.0 step C, rtl/ram/ihp/README.md). Same module
// name, ports and behaviour as rtl/common/g2d_ram_1r1w.sv (drop-in by file list): rdata_o holds mem[raddr_i] from the
// cycle after re_i and keeps it until the next read; a read and a write of one address in the same cycle is not
// allowed (coll_o flags it).
//
// Mapping: each byte-enable lane (WIDTH / BE_W bits) is split into IHP two-port macros of the smallest supported
// depth >= DEPTH (64, 256, 512, 1024): 32-bit tiles, the last tile the narrowest macro that holds the rest. The lane
// enable drives the tile's write enable, so no bit masks are needed. Memories smaller than FLOP_BITS bits, or deeper
// than 1024 words, are flops (the behaviour of rtl/common/g2d_ram_1r1w.sv).
module g2d_ram_1r1w #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  parameter int unsigned FLOP_BITS = 1024,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             we_i,
  input  logic [AW-1:0]    waddr_i,
  input  logic [WIDTH-1:0] wdata_i,
  input  logic [BE_W-1:0]  wbe_i,
  input  logic             re_i,
  input  logic [AW-1:0]    raddr_i,
  output logic [WIDTH-1:0] rdata_o,
  output logic             coll_o
);

  assign coll_o = we_i && re_i && (waddr_i == raddr_i) && (wbe_i != '0);

  localparam int unsigned LW = WIDTH / BE_W;

  // smallest macro depth that holds DEPTH words (0: none)
  function automatic int unsigned macro_depth(int unsigned d);
    if (d <= 64)   return 64;
    if (d <= 256)  return 256;
    if (d <= 512)  return 512;
    if (d <= 1024) return 1024;
    return 0;
  endfunction
  // narrowest macro of depth md that holds w <= 32 bits
  function automatic int unsigned macro_width(int unsigned md, int unsigned w);
    if (md == 64)   return (w <= 22) ? 22 : 32;
    if (md == 1024) return (w <= 16) ? 16 : 32;
    return (w <= 8) ? 8 : (w <= 16) ? 16 : 32;
  endfunction

  localparam int unsigned MD     = macro_depth(DEPTH);
  localparam bit          FLOPS  = (MD == 0) || (WIDTH * DEPTH < FLOP_BITS);
  localparam int unsigned MAW    = (MD > 1) ? $clog2(MD) : 1;
  localparam int unsigned NT     = (LW + 31) / 32;          // tiles per lane
  localparam int unsigned REM    = LW - (NT - 1) * 32;      // bits in the last tile
  localparam int unsigned LAST_W = macro_width(MD, REM);

  generate
    if (FLOPS) begin : g_flops
      logic [WIDTH-1:0] mem_q [DEPTH];
      always_ff @(posedge clk_i) begin
        for (int l = 0; l < int'(BE_W); l++) begin
          if (we_i && wbe_i[l]) mem_q[waddr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
        end
        if (re_i) rdata_o <= mem_q[raddr_i];
      end
    end else begin : g_macros
      logic [MAW-1:0] ra, wa;
      assign ra = MAW'(raddr_i);
      assign wa = MAW'(waddr_i);
      for (genvar l = 0; l < BE_W; l++) begin : g_lane
        for (genvar t = 0; t < NT; t++) begin : g_tile
          localparam int unsigned TW = (t == NT - 1) ? LAST_W : 32;   // macro width
          localparam int unsigned UW = (t == NT - 1) ? REM : 32;      // bits used
          logic [TW-1:0] din, dout;
          assign din = TW'(wdata_i[l * LW + t * 32 +: UW]);
          g2d_ihp_2p #(.DEPTH(MD), .WIDTH(TW)) u_tile (
            .clk_i  (clk_i),
            .re_i   (re_i),
            .raddr_i(ra),
            .rdata_o(dout),
            .we_i   (we_i && wbe_i[l]),
            .waddr_i(wa),
            .wdata_i(din)
          );
          assign rdata_o[l * LW + t * 32 +: UW] = dout[UW-1:0];
          if (TW > UW) begin : g_pad
            logic unused;
            assign unused = ^dout[TW-1:UW];
          end
        end
      end
    end
  endgenerate

`ifndef SYNTHESIS
  initial begin
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_1r1w: WIDTH must be a multiple of BE_W");
  end
  always_ff @(posedge clk_i) begin
    if ($time > 0 && we_i && re_i && waddr_i == raddr_i && wbe_i != '0) begin
      $error("g2d_ram_1r1w: read and write of address %0d in the same cycle", raddr_i);
    end
  end
`endif

endmodule
