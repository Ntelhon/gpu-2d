// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// State registers written by LOAD_STATE (commands.md §2.2). STATE_SIZE words (128 since v1.6b); only the bits of defined
// fields are stored (spec/regs.yaml block `state`, g2d_state_mask), the rest read as 0. Reset and
// soft reset (clr_i) restore the regs.yaml reset values.

module g2d_state
  import g2d_state_pkg::*;
(
  input  logic                  clk_i,
  input  logic                  rst_ni,
  input  logic                  clr_i,
  input  logic                  we_i,
  input  logic [STATE_IDX_BITS-1:0]   idx_i,
  input  logic [31:0]           wdata_i,
  output logic [STATE_SIZE-1:0][31:0] state_o
);

  for (genvar i = 0; i < STATE_SIZE; i++) begin : g_word
    localparam logic [31:0] MASK  = g2d_state_mask(STATE_IDX_BITS'(i));
    localparam logic [31:0] RESET = g2d_state_reset(STATE_IDX_BITS'(i));
    if (MASK == '0) begin : g_none
      assign state_o[i] = '0;
    end else begin : g_reg
      logic [31:0] q;
      always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni)                         q <= RESET;
        else if (clr_i)                      q <= RESET;
        else if (we_i && idx_i == STATE_IDX_BITS'(i)) q <= wdata_i & MASK;
      end
      assign state_o[i] = q & MASK;
    end
  end

endmodule
