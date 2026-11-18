// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D control: kick queue (arch.md §5.1), error capture and HALTED (§8), FENCE_SEQ (§5.2),
// interrupt (§7), STATUS and the soft-reset sequencer (§2.2).
//
// Soft reset: a write of CTRL.SOFT_RESET = 1 raises soft_busy_o and stop_o (no new commands, no new
// AXI bursts; the pixel engine finishes its current row). When the core reports drained_i (nothing outstanding on AXI,
// no engine mid-operation) soft_clr_o pulses for one cycle: it clears every core FSM, queue, state
// register, the kick queue, FENCE_SEQ, the error registers, HALTED and (in g2d_csr_regs) the w1c fields.
// soft_busy_o, stop_o and STATUS.SOFT_RESET_BUSY stay high through that clear cycle: an engine that is
// drained but still holds queued work (stopped engines report idle regardless of their contents) must
// not present a request in the cycle it is cleared.
// CSR configuration (IRQ_ENABLE, ADDR_WIN, MEM_BASE_HI, SCRATCH) and the performance counters are kept.
// Kicks written while the soft reset is in progress are dropped without KICK_OVF.
//
// Errors: CMD_DECODE, BAD_STATE, ADDR_RANGE, AXI_RD, AXI_WR halt the GPU: stop_o stops the command
// side (no new command is fetched or executed); draws issued before the error still complete, and every
// outstanding AXI transaction drains (arch.md §8). The first halting error
// (ERR_STATUS halting bits all clear) captures ERR_ADDR/ERR_CMD_ADDR; later ones only set their bit.
// KICK_OVF does not halt and never captures addresses.

module g2d_ctrl
  import g2d_csr_pkg::*;
  import g2d_pkg::*;
#(
  parameter int unsigned KICK_DEPTH = 4,
  parameter int unsigned DATA_W     = 64,
  parameter int unsigned ADDR_W     = 32,
  parameter bit          CFG_ASYNC  = 1'b0,
  parameter bit          MEM_ASYNC  = 1'b0,
  parameter logic [5:0]  FEATURES   = 6'b11_1111
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  g2d_csr_reg2hw_t  reg2hw_i,
  // fields of hw2reg owned by this block (the rest is driven by g2d_perf)
  output g2d_csr_hw2reg_hwcfg_t        hwcfg_o,
  output g2d_csr_hw2reg_status_t       status_o,
  output g2d_csr_hw2reg_irq_status_t   irq_status_o,
  output g2d_csr_hw2reg_fence_seq_t    fence_seq_o,
  output g2d_csr_hw2reg_err_status_t   err_status_o,
  output g2d_csr_hw2reg_err_addr_t     err_addr_o,
  output g2d_csr_hw2reg_err_cmd_addr_t err_cmd_addr_o,
  output g2d_csr_hw2reg_cur_cmd_addr_t cur_cmd_addr_o,
  // kick queue to the command fetch
  output logic             kick_valid_o,
  input  logic             kick_ready_i,
  output logic [31:0]      kick_addr_o,
  output logic [23:0]      kick_size_o,
  // events from the core
  input  logic             cmd_err_decode_i,
  input  logic             cmd_err_bad_state_i,
  input  logic             cmd_err_addr_range_i,
  input  logic [31:0]      cmd_err_addr_i,
  input  logic [31:0]      cmd_err_cmd_addr_i,
  input  logic             axi_err_rd_i,
  input  logic             axi_err_wr_i,
  input  logic             axi_err_win_i,      // AXI-level window violation (reported as ADDR_RANGE)
  input  logic [31:0]      axi_err_addr_i,
  input  logic [31:0]      cur_cmd_addr_i,
  input  logic             fence_i,
  input  logic [31:0]      fence_value_i,
  input  logic             fence_irq_i,
  input  logic             core_busy_i,        // any command, draw or AXI activity in the core
  input  logic             drained_i,          // no AXI outstanding, no engine mid-operation
  // control to the core
  output logic             stop_o,             // soft reset in progress or halted: no new command
  output logic             soft_busy_o,        // soft reset in progress: engines and AXI issue nothing new
  output logic             halt_cmd_o,         // halted by an error other than AXI_RD (parser stops at once)
  output logic             halted_o,
  output logic             soft_clr_o,
  output logic             idle_o,
  output logic             irq_o,
  output logic             irq_new_o   // v1.9: one-cycle pulse per cycle in which an enabled IRQ bit becomes
                                       // pending; pulses are separated by at least one low cycle
);

  localparam int unsigned KCNT_W = $clog2(KICK_DEPTH + 1);

  // ---------------------------------------------------------------- soft reset sequencer
  logic soft_busy_q, soft_clr_q;
  logic soft_req;
  assign soft_req = reg2hw_i.ctrl.soft_reset.qe & reg2hw_i.ctrl.soft_reset.q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      soft_busy_q <= 1'b0;
      soft_clr_q  <= 1'b0;
    end else begin
      soft_clr_q <= 1'b0;
      if (soft_req) begin
        soft_busy_q <= 1'b1;
      end else if (soft_busy_q & drained_i & ~soft_clr_q) begin
        soft_clr_q  <= 1'b1;
        soft_busy_q <= 1'b0;
      end
    end
  end
  assign soft_clr_o = soft_clr_q;

  // ---------------------------------------------------------------- kick queue
  logic              kick_push, kick_in_ready;
  logic [KCNT_W-1:0] kick_count;
  logic soft_any;
  assign soft_any  = soft_busy_q | soft_clr_q;
  assign kick_push = reg2hw_i.cmd_kick.size.qe & ~soft_any;

  g2d_fifo #(.WIDTH(29 + 24), .DEPTH(KICK_DEPTH)) u_kick_q (
    .clk_i,
    .rst_ni,
    .clr_i       (soft_clr_q),
    .in_valid_i  (kick_push),
    .in_ready_o  (kick_in_ready),
    .in_data_i   ({reg2hw_i.cmd_addr.addr.q, reg2hw_i.cmd_kick.size.q}),
    .out_valid_o (kick_valid_o),
    .out_ready_i (kick_ready_i),
    .out_data_o  ({kick_addr_o[31:3], kick_size_o}),
    .count_o     (kick_count)
  );
  assign kick_addr_o[2:0] = 3'b000;

  logic kick_ovf;
  assign kick_ovf = kick_push & ~kick_in_ready;

  // ---------------------------------------------------------------- errors, halt
  logic        halted_q, halt_cmd_q;
  logic [31:0] err_addr_q, err_cmd_addr_q;
  logic        any_halt_err, first_err;
  logic [31:0] cap_addr, cap_cmd_addr;
  logic        st_decode, st_bad, st_range, st_rd, st_wr;

  assign st_decode = cmd_err_decode_i;
  assign st_bad    = cmd_err_bad_state_i;
  assign st_range  = cmd_err_addr_range_i | axi_err_win_i;
  assign st_rd     = axi_err_rd_i;
  assign st_wr     = axi_err_wr_i;
  assign any_halt_err = st_decode | st_bad | st_range | st_rd | st_wr;
  assign first_err = any_halt_err & ~(reg2hw_i.err_status.cmd_decode.q | reg2hw_i.err_status.bad_state.q |
                                      reg2hw_i.err_status.addr_range.q | reg2hw_i.err_status.axi_rd.q |
                                      reg2hw_i.err_status.axi_wr.q);

  // Same-cycle errors: the command processor's error is the one being executed, so it wins.
  always_comb begin
    if (cmd_err_decode_i | cmd_err_bad_state_i | cmd_err_addr_range_i) begin
      cap_addr     = cmd_err_addr_i;
      cap_cmd_addr = cmd_err_cmd_addr_i;
    end else begin
      cap_addr     = axi_err_addr_i;
      cap_cmd_addr = cur_cmd_addr_i;
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      halted_q       <= 1'b0;
      halt_cmd_q     <= 1'b0;
      err_addr_q     <= '0;
      err_cmd_addr_q <= '0;
    end else if (soft_clr_q) begin
      halted_q       <= 1'b0;
      halt_cmd_q     <= 1'b0;
      err_addr_q     <= '0;
      err_cmd_addr_q <= '0;
    end else begin
      if (any_halt_err) halted_q <= 1'b1;
      if (st_decode | st_bad | st_range | st_wr) halt_cmd_q <= 1'b1;
      if (first_err) begin
        err_addr_q     <= cap_addr;
        err_cmd_addr_q <= cap_cmd_addr;
      end
    end
  end

  // ---------------------------------------------------------------- fence
  logic [31:0] fence_seq_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)         fence_seq_q <= '0;
    else if (soft_clr_q) fence_seq_q <= '0;
    else if (fence_i)    fence_seq_q <= fence_value_i;
  end

  // ---------------------------------------------------------------- idle, irq
  logic idle, idle_q, irq_q, irq_new_q, irq_want_q;
  logic [2:0] pend, pend_q;
  assign pend = {reg2hw_i.irq_status.idle.q  & reg2hw_i.irq_enable.idle.q,
                 reg2hw_i.irq_status.error.q & reg2hw_i.irq_enable.error.q,
                 reg2hw_i.irq_status.fence.q & reg2hw_i.irq_enable.fence.q};
  assign idle = ~core_busy_i & ~kick_valid_o & ~soft_busy_q & ~soft_clr_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      idle_q    <= 1'b1;
      irq_q     <= 1'b0;
      irq_new_q  <= 1'b0;
      irq_want_q <= 1'b0;
      pend_q     <= '0;
    end else begin
      idle_q    <= idle;
      irq_q     <= |pend;
      pend_q    <= pend;
      // a new pending bit while a pulse is out is shown as its own pulse after one low cycle (edge-triggered
      // interrupt controllers count edges)
      if (irq_new_q) begin
        irq_new_q  <= 1'b0;
        irq_want_q <= irq_want_q | (|(pend & ~pend_q));
      end else begin
        irq_new_q  <= irq_want_q | (|(pend & ~pend_q));
        irq_want_q <= 1'b0;
      end
    end
  end

  assign stop_o      = soft_any | halted_q;
  assign soft_busy_o = soft_any;
  assign halt_cmd_o  = halt_cmd_q;
  assign halted_o = halted_q;
  assign idle_o   = idle;
  assign irq_o    = irq_q;
  assign irq_new_o = irq_new_q;

  // ---------------------------------------------------------------- hw2reg
  assign hwcfg_o.data_bytes.d = 8'(DATA_W / 8);
  assign hwcfg_o.addr_w.d     = 8'(ADDR_W);
  assign hwcfg_o.kick_depth.d = 5'(KICK_DEPTH);
  assign hwcfg_o.cfg_async.d  = CFG_ASYNC;
  assign hwcfg_o.mem_async.d  = MEM_ASYNC;
  assign hwcfg_o.feat_family.d = FEATURES[0];   // v1.7 (g2d_pkg FEAT_*)
  assign hwcfg_o.feat_gauss.d  = FEATURES[1];
  assign hwcfg_o.feat_yuv.d    = FEATURES[2];
  assign hwcfg_o.feat_color.d  = FEATURES[3];
  assign hwcfg_o.feat_radial.d = FEATURES[4];
  assign hwcfg_o.feat_mask.d   = FEATURES[5];

  assign status_o.idle.d            = idle;
  assign status_o.halted.d          = halted_q;
  assign status_o.soft_reset_busy.d = soft_any;
  // Q-RTL-9 (v1.4): commands still executing. core_busy_i excludes the kick queue, and after an AXI_RD halt it
  // only covers the commands the parser can still run (cmd_live, arch.md §8), so 0 = the halt has settled.
  assign status_o.cmd_active.d      = core_busy_i;
  assign status_o.kick_free.d       = 5'(KICK_DEPTH) - 5'(kick_count);

  assign irq_status_o.fence.set = fence_i & fence_irq_i;
  assign irq_status_o.error.set = any_halt_err | kick_ovf;
  assign irq_status_o.idle.set  = idle & ~idle_q;

  assign fence_seq_o.value.d = fence_seq_q;

  assign err_status_o.cmd_decode.set = st_decode;
  assign err_status_o.bad_state.set  = st_bad;
  assign err_status_o.addr_range.set = st_range;
  assign err_status_o.axi_rd.set     = st_rd;
  assign err_status_o.axi_wr.set     = st_wr;
  assign err_status_o.kick_ovf.set   = kick_ovf;

  assign err_addr_o.addr.d     = err_addr_q;
  assign err_cmd_addr_o.addr.d = err_cmd_addr_q;
  assign cur_cmd_addr_o.addr.d = cur_cmd_addr_i;

  // Fields of reg2hw used elsewhere (scratch, window, base, perf, debug) or not at all (DBG_STATUS: read back only).
  logic unused;
  assign unused = ^{reg2hw_i.scratch, reg2hw_i.mem_base_hi, reg2hw_i.addr_win_lo, reg2hw_i.addr_win_hi,
                    reg2hw_i.perf_ctrl, reg2hw_i.err_status.kick_ovf.q, reg2hw_i.dbg_ctrl, reg2hw_i.dbg_status,
                    reg2hw_i.mem_attr, reg2hw_i.mem_qos};

endmodule
