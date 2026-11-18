// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Command parser (spec/commands.md). Consumes the word stream of g2d_cmd_fetch, one word per cycle.
//
// Header checks (CMD_DECODE, ERR_ADDR = 0, ERR_CMD_ADDR = header address): unknown opcode, non-zero
// reserved header bits, LOAD_STATE COUNT == 0 or INDEX + COUNT > STATE_SIZE, payload crossing the end of the
// buffer, DRAW_PATH payload[3][31:24] != 0 (checked after the payload is read, as in the model).
// LOAD_STATE writes the state registers in order. A draw is validated by g2d_draw_check on the live
// state (the parser is the only writer, so the state is stable) and handed to the pixel engine with a
// snapshot of the state words it needs; a failing draw is not issued (rendering.md §13). A draw waits
// for the engine to be free, so a later LOAD_STATE never affects an issued draw.
// FENCE waits until the pixel engine is idle and every write is acknowledged (arch.md §5.2).
//
// A word flagged with an AXI read error stops the parser (the error itself is reported by the AXI
// master). After any error, and while stop_i is set, the parser consumes nothing; clr_i restarts it.

module g2d_cmd_parse
  import g2d_pkg::*;
  import g2d_state_pkg::STATE_SIZE, g2d_state_pkg::STATE_IDX_BITS;
(
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clr_i,
  input  logic              stop_i,
  // word stream
  input  logic              word_valid_i,
  output logic              word_ready_o,
  input  logic [31:0]       word_i,
  input  logic [31:0]       word_addr_i,
  input  logic [21:0]       word_rem_i,
  input  logic              word_err_i,
  // state registers
  output logic              st_we_o,
  output logic [STATE_IDX_BITS-1:0] st_idx_o,
  output logic [31:0]       st_wdata_o,
  // draw check
  output logic              chk_start_o,
  output logic              chk_path_o,
  output logic [31:0]       chk_edge_addr_o,
  output logic [23:0]       chk_edge_count_o,
  input  logic              chk_done_i,
  input  logic              chk_bad_state_i,
  input  logic              chk_addr_range_i,
  input  logic [31:0]       chk_err_addr_i,
  // draw issue to the pixel engine
  output logic              draw_valid_o,
  input  logic              draw_ready_i,
  output logic              draw_path_o,
  output logic [3:0][31:0]  draw_payload_o,
  input  logic              engine_idle_i,
  input  logic              wr_idle_i,
  // fence
  output logic              fence_o,
  output logic [31:0]       fence_value_o,
  output logic              fence_irq_o,
  // errors and status
  output logic              err_decode_o,
  output logic              err_bad_state_o,
  output logic              err_range_o,
  output logic [31:0]       err_addr_o,
  output logic [31:0]       err_cmd_addr_o,
  output logic [31:0]       cur_cmd_addr_o,
  output logic              busy_o,
  output logic              halt_o,         // stopped for good (error seen), until clr_i
  output logic              exec_o          // checking, issuing or fencing: progresses without new words
);

  typedef enum logic [2:0] {S_HDR, S_PAYLOAD, S_CHECK, S_ISSUE, S_FENCE, S_HALT} state_e;

  state_e      st_q;
  logic [7:0]  op_q;
  logic [15:0] left_q;        // payload words still to read
  logic [STATE_IDX_BITS-1:0] idx_q; // LOAD_STATE write index
  logic [1:0]  pi_q;          // payload word index for draws
  logic [3:0][31:0] pay_q;
  logic        fence_irq_q;
  logic [31:0] hdr_addr_q;
  logic        chk_wait_q;

  // ---------------------------------------------------------------- header decode
  logic [7:0]  op;
  logic        reserved_bad, payload_known;
  logic [15:0] payload;
  logic [8:0]  ls_end;

  assign op     = word_i[31:24];
  assign ls_end = 9'(word_i[7:0]) + 9'(word_i[23:16]);

  always_comb begin
    reserved_bad  = 1'b0;
    payload       = '0;
    payload_known = 1'b1;
    unique case (op)
      OP_NOP: begin
        reserved_bad = word_i[23:16] != '0;
        payload      = word_i[15:0];
      end
      OP_LOAD_STATE: begin
        reserved_bad = word_i[15:8] != '0 || word_i[23:16] == '0 || ls_end > 9'(STATE_SIZE);
        payload      = 16'(word_i[23:16]);
      end
      OP_DRAW_RECT: begin
        reserved_bad = word_i[23:0] != '0;
        payload      = 16'd2;
      end
      OP_DRAW_PATH: begin
        reserved_bad = word_i[23:0] != '0;
        payload      = 16'd4;
      end
      OP_FENCE: begin
        reserved_bad = word_i[23:1] != '0;
        payload      = 16'd1;
      end
      default: payload_known = 1'b0;
    endcase
  end

  logic hdr_bad;
  assign hdr_bad = ~payload_known | reserved_bad | (22'(payload) > word_rem_i - 22'd1);

  // ---------------------------------------------------------------- FSM
  logic consume;
  assign word_ready_o = consume;

  always_comb begin
    consume = 1'b0;
    if (!stop_i && word_valid_i && !word_err_i) begin
      unique case (st_q)
        S_HDR:     consume = 1'b1;
        S_PAYLOAD: consume = 1'b1;
        default:   consume = 1'b0;
      endcase
    end
  end

  logic path_tail_bad;
  assign path_tail_bad = (op_q == OP_DRAW_PATH) && (pay_q[3][31:24] != '0);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      st_q        <= S_HDR;
      op_q        <= '0;
      left_q      <= '0;
      idx_q       <= '0;
      pi_q        <= '0;
      pay_q       <= '0;
      fence_irq_q <= 1'b0;
      hdr_addr_q  <= '0;
      chk_wait_q  <= 1'b0;
    end else if (clr_i) begin
      st_q        <= S_HDR;
      hdr_addr_q  <= '0;
      chk_wait_q  <= 1'b0;
    end else begin
      chk_wait_q <= 1'b0;
      unique case (st_q)
        S_HDR: begin
          if (!stop_i && word_valid_i) begin
            if (word_err_i) begin
              st_q <= S_HALT;
            end else if (hdr_bad) begin
              st_q <= S_HALT;
            end else begin
              op_q        <= op;
              left_q      <= payload;
              idx_q       <= word_i[STATE_IDX_BITS-1:0];
              pi_q        <= '0;
              fence_irq_q <= word_i[0];
              hdr_addr_q  <= word_addr_i;
              if (payload == '0) st_q <= S_HDR;   // NOP with SKIP = 0
              else               st_q <= S_PAYLOAD;
            end
          end
        end
        S_PAYLOAD: begin
          if (!stop_i && word_valid_i) begin
            if (word_err_i) begin
              st_q <= S_HALT;
            end else begin
              left_q <= left_q - 16'd1;
              idx_q  <= idx_q + STATE_IDX_BITS'(1);
              pi_q   <= pi_q + 2'd1;
              if (op_q == OP_DRAW_RECT || op_q == OP_DRAW_PATH) pay_q[pi_q] <= word_i;
              if (op_q == OP_FENCE) pay_q[0] <= word_i;
              if (left_q == 16'd1) begin
                unique case (op_q)
                  OP_DRAW_RECT, OP_DRAW_PATH: begin
                    st_q       <= S_CHECK;
                    chk_wait_q <= 1'b1;
                  end
                  OP_FENCE: st_q <= S_FENCE;
                  default:  st_q <= S_HDR;
                endcase
              end
            end
          end
        end
        S_CHECK: begin
          if (chk_wait_q) begin
            if (path_tail_bad) st_q <= S_HALT;   // reported as CMD_DECODE below
          end else if (chk_done_i) begin
            st_q <= (chk_bad_state_i || chk_addr_range_i) ? S_HALT : S_ISSUE;
          end
        end
        S_ISSUE: begin
          if (stop_i) st_q <= S_HALT;
          else if (draw_ready_i) st_q <= S_HDR;
        end
        S_FENCE: begin
          if (stop_i) st_q <= S_HALT;
          else if (engine_idle_i && wr_idle_i) st_q <= S_HDR;
        end
        S_HALT: ;
        default: st_q <= S_HALT;
      endcase
    end
  end

  // state writes
  assign st_we_o    = consume && st_q == S_PAYLOAD && op_q == OP_LOAD_STATE;
  assign st_idx_o   = idx_q;
  assign st_wdata_o = word_i;

  // draw check: started on the cycle after the last payload word (pay_q complete), unless the
  // DRAW_PATH tail is malformed
  assign chk_start_o      = st_q == S_CHECK && chk_wait_q && !path_tail_bad;
  assign chk_path_o       = op_q == OP_DRAW_PATH;
  assign chk_edge_addr_o  = pay_q[2];
  assign chk_edge_count_o = pay_q[3][23:0];

  assign draw_valid_o   = st_q == S_ISSUE && !stop_i;
  assign draw_path_o    = op_q == OP_DRAW_PATH;
  assign draw_payload_o = pay_q;

  assign fence_o       = st_q == S_FENCE && !stop_i && engine_idle_i && wr_idle_i;
  assign fence_value_o = pay_q[0];
  assign fence_irq_o   = fence_irq_q;

  // errors (one-cycle pulses)
  logic hdr_err, tail_err, chk_err;
  assign hdr_err  = st_q == S_HDR && !stop_i && word_valid_i && !word_err_i && hdr_bad;
  assign tail_err = st_q == S_CHECK && chk_wait_q && path_tail_bad;
  assign chk_err  = st_q == S_CHECK && !chk_wait_q && chk_done_i;

  assign err_decode_o    = hdr_err | tail_err;
  assign err_bad_state_o = chk_err & chk_bad_state_i;
  assign err_range_o     = chk_err & chk_addr_range_i;
  assign err_addr_o      = (hdr_err | tail_err) ? 32'd0 : chk_err_addr_i;
  assign err_cmd_addr_o  = hdr_err ? word_addr_i : hdr_addr_q;
  assign cur_cmd_addr_o  = (st_q == S_HDR) ? word_addr_i : hdr_addr_q;

  assign busy_o = st_q != S_HDR && st_q != S_HALT;
  assign halt_o = st_q == S_HALT;
  assign exec_o = st_q == S_CHECK || st_q == S_ISSUE || st_q == S_FENCE;

endmodule
