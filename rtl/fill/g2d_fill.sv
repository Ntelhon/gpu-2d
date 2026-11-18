// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Rect fill fast path (arch.md §6.1–6.3, rendering.md §4 and §11): DRAW_RECT with SOLID paint, blend
// NONE and coverage 255, so no destination read. The colour is computed once per draw:
// S = apply_global_alpha(PAINT_COLOR) (§5.3), stored pixel = compress(TGT_FMT, S) (§3.4), replicated
// over a DATA_W beat. Bytes are only written inside the clipped rectangle (the write engine masks the
// row ends), so every beat of a draw carries the same data.
//
// The rectangle is clipped against the target and the scissor (§6). Each row is one write request;
// a separate emitter supplies the beats the rows owe, one beat per clock.
//
// The state words are snapshotted when the draw is accepted. stop_i (soft reset only; a halt lets an
// issued draw complete): no new row is requested; the beats already owed are still supplied (the write
// engine drops them under stop), then the draw is abandoned without draw_done. The command parser only issues draws this engine supports (Phase 2:
// g2d_draw_check RESTRICT_P2).

module g2d_fill
  import g2d_pkg::*;
  import g2d_state_pkg::*;
#(
  parameter int unsigned DATA_W = 64,
  localparam int unsigned DB = DATA_W / 8,
  localparam int unsigned OFF_W = $clog2(DB)
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // draw from the parser
  input  logic              draw_valid_i,
  output logic              draw_ready_o,
  input  logic [3:0][31:0]  draw_payload_i,
  input  logic [STATE_SIZE-1:0][31:0] state_i,
  // write engine
  output logic              wr_req_valid_o,
  input  logic              wr_req_ready_i,
  output logic [31:0]       wr_req_addr_o,
  output logic [15:0]       wr_req_len_o,
  output logic              wr_dat_valid_o,
  input  logic              wr_dat_ready_i,
  output logic [DATA_W-1:0] wr_dat_data_o,
  output logic [DB-1:0]     wr_dat_strb_o,
  // status / performance
  output logic              idle_o,
  output logic              draw_done_o,
  output logic [13:0]       pixels_o       // pixels requested this cycle (one row)
);

  typedef enum logic [1:0] {S_IDLE, S_SETUP1, S_SETUP2, S_RUN} state_e;
  state_e st_q;

  // ---------------------------------------------------------------- snapshot at accept
  logic [31:0] t_addr_q;
  logic [31:0] stride_q;       // row step (v1.6: negative with TGT_FMT.FLIP_Y, rendering.md §5.8)
  logic [13:0] t_w_q, t_h_q, c_x0_q, c_y0_q, c_x1_q, c_y1_q;
  logic [7:0]  fmt_q;
  logic [31:0] color_q;
  logic [1:0]  a_mode_q;
  logic [7:0]  a_val_q;
  logic signed [15:0] px0_q, py0_q, px1_q, py1_q;

  assign draw_ready_o = (st_q == S_IDLE) & ~stop_i;

  // ---------------------------------------------------------------- setup 1: clip, colour
  // Everything as signed 17-bit: payload coordinates are signed 16-bit, clip/size unsigned 14-bit.
  function automatic logic signed [16:0] smax(logic signed [16:0] a, logic signed [16:0] b);
    return (a > b) ? a : b;
  endfunction
  function automatic logic signed [16:0] smin(logic signed [16:0] a, logic signed [16:0] b);
    return (a < b) ? a : b;
  endfunction

  logic signed [16:0] rx0_d, ry0_d, rx1_d, ry1_d;
  assign rx0_d = smax(17'(px0_q), $signed({3'b0, c_x0_q}));
  assign ry0_d = smax(17'(py0_q), $signed({3'b0, c_y0_q}));
  assign rx1_d = smin(smin(17'(px1_q), $signed({3'b0, c_x1_q})), $signed({3'b0, t_w_q}));
  assign ry1_d = smin(smin(17'(py1_q), $signed({3'b0, c_y1_q})), $signed({3'b0, t_h_q}));

  // v1.7: global alpha in SETUP1, compression + replication in SETUP2 (one per state, timing T-1)
  logic [31:0] s_argb, s_argb_q, pix;
  assign s_argb = apply_global_alpha(color_q, a_mode_q, a_val_q);
  assign pix    = fmt_compress(fmt_q, s_argb_q);

  logic [13:0] rx0_q, ry0_q, w_q, h_q;
  logic        empty_q;
  logic [1:0]  lg_q;
  logic [DATA_W-1:0] pattern_q;

  // ---------------------------------------------------------------- run
  logic [31:0] row_addr_q;
  logic [16:0] row_bytes_q;      // 1..32768
  logic [13:0] rows_q;
  logic [16:0] owed_q;           // beats owed to the write engine

  logic        req_fire, dat_fire;
  logic [12:0] row_beats;
  assign row_beats = 13'((17'(row_addr_q[OFF_W-1:0]) + row_bytes_q - 17'd1) >> OFF_W) + 13'd1;

  assign wr_req_valid_o = (st_q == S_RUN) && rows_q != '0 && !stop_i;
  assign wr_req_addr_o  = row_addr_q;
  assign wr_req_len_o   = 16'(row_bytes_q - 17'd1);
  assign req_fire       = wr_req_valid_o & wr_req_ready_i;

  assign wr_dat_valid_o = owed_q != '0;
  assign wr_dat_data_o  = pattern_q;
  assign wr_dat_strb_o  = '1;
  assign dat_fire       = wr_dat_valid_o & wr_dat_ready_i;

  logic finish;
  assign finish = (st_q == S_RUN) && owed_q == '0 && (rows_q == '0 || stop_i);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      st_q   <= S_IDLE;
      rows_q <= '0;
      owed_q <= '0;
    end else if (clr_i) begin
      st_q   <= S_IDLE;
      rows_q <= '0;
      owed_q <= '0;
    end else begin
      owed_q <= owed_q + (req_fire ? 17'(row_beats) : 17'd0) - 17'(dat_fire);
      unique case (st_q)
        S_IDLE:   if (draw_valid_i && draw_ready_o) st_q <= S_SETUP1;
        S_SETUP1: st_q <= stop_i ? S_IDLE : S_SETUP2;
        S_SETUP2: begin
          st_q   <= (empty_q || stop_i) ? S_IDLE : S_RUN;
          rows_q <= empty_q ? 14'd0 : h_q;
        end
        S_RUN: begin
          if (req_fire) rows_q <= rows_q - 14'd1;
          if (finish)   st_q <= S_IDLE;
        end
        default: st_q <= S_IDLE;
      endcase
    end
  end

  // Datapath registers (qualified by the FSM, no reset needed).
  always_ff @(posedge clk_i) begin
    if (st_q == S_IDLE && draw_valid_i && draw_ready_o) begin
      // §5.8 FLIP_Y: row y at (TGT_ADDR + (H - 1) * stride) - y * stride
      if (state_i[G2D_ST_TGT_FMT][G2D_ST_TGT_FMT_FLIP_Y_LSB]) begin
        t_addr_q <= state_i[G2D_ST_TGT_ADDR] +
                    32'(state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_H_LSB +: 14] - 14'd1) * 32'(state_i[G2D_ST_TGT_STRIDE][15:0]);
        stride_q <= -32'(state_i[G2D_ST_TGT_STRIDE][15:0]);
      end else begin
        t_addr_q <= state_i[G2D_ST_TGT_ADDR];
        stride_q <= 32'(state_i[G2D_ST_TGT_STRIDE][15:0]);
      end
      t_w_q    <= state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_W_LSB +: 14];
      t_h_q    <= state_i[G2D_ST_TGT_SIZE][G2D_ST_TGT_SIZE_H_LSB +: 14];
      fmt_q    <= state_i[G2D_ST_TGT_FMT][7:0];
      c_x0_q   <= state_i[G2D_ST_CLIP_MIN][G2D_ST_CLIP_MIN_X0_LSB +: 14];
      c_y0_q   <= state_i[G2D_ST_CLIP_MIN][G2D_ST_CLIP_MIN_Y0_LSB +: 14];
      c_x1_q   <= state_i[G2D_ST_CLIP_MAX][G2D_ST_CLIP_MAX_X1_LSB +: 14];
      c_y1_q   <= state_i[G2D_ST_CLIP_MAX][G2D_ST_CLIP_MAX_Y1_LSB +: 14];
      color_q  <= state_i[G2D_ST_PAINT_COLOR];
      a_mode_q <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_SRC_MODE_LSB +: 2];
      a_val_q  <= state_i[G2D_ST_ALPHA_CTRL][G2D_ST_ALPHA_CTRL_SRC_VALUE_LSB +: 8];
      px0_q    <= $signed(draw_payload_i[0][15:0]);
      py0_q    <= $signed(draw_payload_i[0][31:16]);
      px1_q    <= $signed(draw_payload_i[1][15:0]);
      py1_q    <= $signed(draw_payload_i[1][31:16]);
    end
    if (st_q == S_SETUP1) begin
      empty_q <= (rx1_d <= rx0_d) || (ry1_d <= ry0_d);
      rx0_q   <= rx0_d[13:0];
      ry0_q   <= ry0_d[13:0];
      w_q     <= 14'(rx1_d - rx0_d);
      h_q     <= 14'(ry1_d - ry0_d);
      lg_q    <= fmt_lg_bytes(fmt_q);
      s_argb_q <= s_argb;
    end
    if (st_q == S_SETUP2) begin
      unique case (lg_q)
        2'd0:    pattern_q <= {DB{pix[7:0]}};
        2'd1:    pattern_q <= {(DB / 2){pix[15:0]}};
        default: pattern_q <= {(DB / 4){pix}};
      endcase
    end
    if (st_q == S_SETUP2) begin
      row_addr_q  <= t_addr_q + 32'(ry0_q) * stride_q + (32'(rx0_q) << lg_q);
      row_bytes_q <= 17'(w_q) << lg_q;
    end else if (req_fire) begin
      row_addr_q <= row_addr_q + stride_q;
    end
  end

  assign idle_o      = (st_q == S_IDLE);
  assign draw_done_o = finish & (rows_q == '0) |
                       ((st_q == S_SETUP2) & empty_q & ~stop_i);
  assign pixels_o    = req_fire ? w_q : '0;

  // Only the state words and payload words of a rect fill are used (DRAW_PATH never reaches this
  // engine in Phase 2).
  logic unused;
  assign unused = ^{state_i, draw_payload_i[3:2]};

endmodule
