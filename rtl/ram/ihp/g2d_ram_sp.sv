// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Single-port RAM, IHP SG13G2 variant (v2.0 step C, rtl/ram/ihp/README.md). Same module name, ports and behaviour as
// rtl/common/g2d_ram_sp.sv (drop-in by file list): en_i with we_i writes the enabled lanes, with !we_i reads; rdata_o
// holds the read data until the next read (a write leaves it unchanged).
//
// Mapping: each byte-enable lane is split into IHP single-port macros of the smallest supported depth >= DEPTH (64 ..
// 4096) and the widest width of that depth (64, or 16 at 4096), the last tile the narrowest macro that holds the
// rest. A tile whose lane is not enabled during a write is not selected (its output keeps the last read). Memories
// smaller than FLOP_BITS bits, or deeper than 4096 words, are flops.
module g2d_ram_sp #(
  parameter int unsigned WIDTH = 64,
  parameter int unsigned DEPTH = 64,
  parameter int unsigned BE_W  = 1,
  parameter int unsigned FLOP_BITS = 1024,
  localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
  input  logic             clk_i,
  input  logic             en_i,
  input  logic             we_i,
  input  logic [AW-1:0]    addr_i,
  input  logic [WIDTH-1:0] wdata_i,
  input  logic [BE_W-1:0]  wbe_i,
  output logic [WIDTH-1:0] rdata_o
);

  localparam int unsigned LW = WIDTH / BE_W;

  function automatic int unsigned macro_depth(int unsigned d);
    if (d <= 64)   return 64;
    if (d <= 256)  return 256;
    if (d <= 512)  return 512;
    if (d <= 1024) return 1024;
    if (d <= 2048) return 2048;
    if (d <= 4096) return 4096;
    return 0;
  endfunction
  function automatic int unsigned macro_max_width(int unsigned md);
    return (md == 4096) ? 16 : 64;
  endfunction
  // narrowest macro of depth md that holds w bits (w <= macro_max_width(md))
  function automatic int unsigned macro_width(int unsigned md, int unsigned w);
    if (md == 64)   return 64;
    if (md == 2048) return (w <= 32) ? 32 : 64;
    if (md == 4096) return (w <= 8) ? 8 : 16;
    if (md == 256)  return (w <= 8) ? 8 : (w <= 16) ? 16 : (w <= 32) ? 32 : (w <= 48) ? 48 : 64;
    return (w <= 8) ? 8 : (w <= 16) ? 16 : (w <= 32) ? 32 : 64;
  endfunction

  localparam int unsigned MD     = macro_depth(DEPTH);
  localparam bit          FLOPS  = (MD == 0) || (WIDTH * DEPTH < FLOP_BITS);
  localparam int unsigned MAW    = (MD > 1) ? $clog2(MD) : 1;
  localparam int unsigned MW     = (MD == 0) ? 64 : macro_max_width(MD);
  localparam int unsigned NT     = (LW + MW - 1) / MW;
  localparam int unsigned REM    = LW - (NT - 1) * MW;
  localparam int unsigned LAST_W = macro_width(MD, REM);

  generate
    if (FLOPS) begin : g_flops
      logic [WIDTH-1:0] mem_q [DEPTH];
      always_ff @(posedge clk_i) begin
        if (en_i) begin
          if (we_i) begin
            for (int l = 0; l < int'(BE_W); l++) begin
              if (wbe_i[l]) mem_q[addr_i][l * LW +: LW] <= wdata_i[l * LW +: LW];
            end
          end else begin
            rdata_o <= mem_q[addr_i];
          end
        end
      end
    end else begin : g_macros
      logic [MAW-1:0] a;
      assign a = MAW'(addr_i);
      for (genvar l = 0; l < BE_W; l++) begin : g_lane
        for (genvar t = 0; t < NT; t++) begin : g_tile
          localparam int unsigned TW = (t == NT - 1) ? LAST_W : MW;
          localparam int unsigned UW = (t == NT - 1) ? REM : MW;
          logic [TW-1:0] din, dout;
          assign din = TW'(wdata_i[l * LW + t * MW +: UW]);
          g2d_ihp_1p #(.DEPTH(MD), .WIDTH(TW)) u_tile (
            .clk_i  (clk_i),
            .en_i   (en_i && (!we_i || wbe_i[l])),
            .we_i   (we_i),
            .addr_i (a),
            .wdata_i(din),
            .rdata_o(dout)
          );
          assign rdata_o[l * LW + t * MW +: UW] = dout[UW-1:0];
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
    if (WIDTH % BE_W != 0) $fatal(1, "g2d_ram_sp: WIDTH must be a multiple of BE_W");
  end
`endif

endmodule
