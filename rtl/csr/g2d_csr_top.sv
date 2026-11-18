// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// CSR subsystem: AXI4-Lite adapter, generated register file, control (kick queue, errors, fence, IRQ,
// soft reset) and performance counters.

module g2d_csr_top
  import g2d_csr_pkg::*;
#(
  parameter int unsigned KICK_DEPTH = 4,
  parameter int unsigned DATA_W     = 64,
  parameter int unsigned ADDR_W     = 32,
  parameter bit          CFG_ASYNC  = 1'b0,
  parameter bit          MEM_ASYNC  = 1'b0,
  parameter logic [5:0]  FEATURES   = 6'b11_1111,
  parameter int unsigned PIX_W      = 14
) (
  input  logic         clk_i,
  input  logic         rst_ni,
  // AXI4-Lite slave
  input  logic [31:0]  s_axil_awaddr_i,
  input  logic [2:0]   s_axil_awprot_i,
  input  logic         s_axil_awvalid_i,
  output logic         s_axil_awready_o,
  input  logic [31:0]  s_axil_wdata_i,
  input  logic [3:0]   s_axil_wstrb_i,
  input  logic         s_axil_wvalid_i,
  output logic         s_axil_wready_o,
  output logic [1:0]   s_axil_bresp_o,
  output logic         s_axil_bvalid_o,
  input  logic         s_axil_bready_i,
  input  logic [31:0]  s_axil_araddr_i,
  input  logic [2:0]   s_axil_arprot_i,
  input  logic         s_axil_arvalid_i,
  output logic         s_axil_arready_o,
  output logic [31:0]  s_axil_rdata_o,
  output logic [1:0]   s_axil_rresp_o,
  output logic         s_axil_rvalid_o,
  input  logic         s_axil_rready_i,
  // configuration to the core
  output logic [31:0]  addr_win_lo_o,
  output logic [31:0]  addr_win_hi_o,
  output logic [31:0]  mem_base_hi_o,
  output logic [31:0]  mem_attr_o,      // v1.9: MEM_ATTR register value (field positions as regs.yaml)
  output logic [31:0]  mem_qos_o,       // v1.9: MEM_QOS register value
  // kick queue
  output logic         kick_valid_o,
  input  logic         kick_ready_i,
  output logic [31:0]  kick_addr_o,
  output logic [23:0]  kick_size_o,
  // events from the core
  input  logic         cmd_err_decode_i,
  input  logic         cmd_err_bad_state_i,
  input  logic         cmd_err_addr_range_i,
  input  logic [31:0]  cmd_err_addr_i,
  input  logic [31:0]  cmd_err_cmd_addr_i,
  input  logic         axi_err_rd_i,
  input  logic         axi_err_wr_i,
  input  logic         axi_err_win_i,
  input  logic [31:0]  axi_err_addr_i,
  input  logic [31:0]  cur_cmd_addr_i,
  input  logic         fence_i,
  input  logic [31:0]  fence_value_i,
  input  logic         fence_irq_i,
  input  logic         core_busy_i,
  input  logic         drained_i,
  // performance events
  input  logic             perf_draw_done_i,
  input  logic [PIX_W-1:0] perf_pixels_i,
  input  logic             perf_rd_beat_i,
  input  logic             perf_wr_beat_i,
  input  logic             perf_rd_stall_i,
  input  logic             perf_wr_stall_i,
  input  logic             perf_drain_i,
  input  logic [2:0]       perf_src_miss_i,
  // debug (v1.8, arch.md §9.1): the W beat counted by perf_wr_beat_i and RAM read/write collisions
  input  logic [DATA_W-1:0]   dbg_wr_data_i,
  input  logic [DATA_W/8-1:0] dbg_wr_strb_i,
  input  logic [6:0]          dbg_ram_coll_i,
  // control to the core
  output logic         stop_o,
  output logic         soft_busy_o,
  output logic         halt_cmd_o,
  output logic         halted_o,
  output logic         soft_clr_o,
  output logic         idle_o,
  output logic         irq_o,
  output logic         irq_new_o
);

  g2d_reg_req_t    reg_req;
  g2d_reg_rsp_t    reg_rsp;
  g2d_csr_reg2hw_t reg2hw;
  g2d_csr_hw2reg_t hw2reg;

  g2d_axil2reg u_axil2reg (
    .clk_i,
    .rst_ni,
    .s_axil_awaddr_i,
    .s_axil_awprot_i,
    .s_axil_awvalid_i,
    .s_axil_awready_o,
    .s_axil_wdata_i,
    .s_axil_wstrb_i,
    .s_axil_wvalid_i,
    .s_axil_wready_o,
    .s_axil_bresp_o,
    .s_axil_bvalid_o,
    .s_axil_bready_i,
    .s_axil_araddr_i,
    .s_axil_arprot_i,
    .s_axil_arvalid_i,
    .s_axil_arready_o,
    .s_axil_rdata_o,
    .s_axil_rresp_o,
    .s_axil_rvalid_o,
    .s_axil_rready_i,
    .reg_req_o (reg_req),
    .reg_rsp_i (reg_rsp)
  );

  g2d_csr_regs u_regs (
    .clk_i,
    .rst_ni,
    .soft_clr_i (soft_clr_o),
    .req_i      (reg_req),
    .rsp_o      (reg_rsp),
    .reg2hw_o   (reg2hw),
    .hw2reg_i   (hw2reg)
  );

  g2d_ctrl #(
    .KICK_DEPTH (KICK_DEPTH),
    .DATA_W     (DATA_W),
    .ADDR_W     (ADDR_W),
    .CFG_ASYNC  (CFG_ASYNC),
    .MEM_ASYNC  (MEM_ASYNC),
    .FEATURES   (FEATURES)
  ) u_ctrl (
    .clk_i,
    .rst_ni,
    .reg2hw_i       (reg2hw),
    .hwcfg_o        (hw2reg.hwcfg),
    .status_o       (hw2reg.status),
    .irq_status_o   (hw2reg.irq_status),
    .fence_seq_o    (hw2reg.fence_seq),
    .err_status_o   (hw2reg.err_status),
    .err_addr_o     (hw2reg.err_addr),
    .err_cmd_addr_o (hw2reg.err_cmd_addr),
    .cur_cmd_addr_o (hw2reg.cur_cmd_addr),
    .kick_valid_o,
    .kick_ready_i,
    .kick_addr_o,
    .kick_size_o,
    .cmd_err_decode_i,
    .cmd_err_bad_state_i,
    .cmd_err_addr_range_i,
    .cmd_err_addr_i,
    .cmd_err_cmd_addr_i,
    .axi_err_rd_i,
    .axi_err_wr_i,
    .axi_err_win_i,
    .axi_err_addr_i,
    .cur_cmd_addr_i,
    .fence_i,
    .fence_value_i,
    .fence_irq_i,
    .core_busy_i,
    .drained_i,
    .stop_o,
    .soft_busy_o,
    .halt_cmd_o,
    .halted_o,
    .soft_clr_o,
    .idle_o,
    .irq_o,
    .irq_new_o
  );

  g2d_perf #(.PIX_W(PIX_W)) u_perf (
    .clk_i,
    .rst_ni,
    .ctrl_i           (reg2hw.perf_ctrl),
    .busy_i           (~idle_o),
    .draw_done_i      (perf_draw_done_i),
    .pixels_i         (perf_pixels_i),
    .rd_beat_i        (perf_rd_beat_i),
    .wr_beat_i        (perf_wr_beat_i),
    .rd_stall_i       (perf_rd_stall_i),
    .wr_stall_i       (perf_wr_stall_i),
    .drain_i          (perf_drain_i),
    .src_miss_i       (perf_src_miss_i),
    .cycles_o         (hw2reg.perf_cycles),
    .busy_cycles_o    (hw2reg.perf_busy_cycles),
    .draws_o          (hw2reg.perf_draws),
    .pixels_out_o     (hw2reg.perf_pixels_out),
    .rd_beats_o       (hw2reg.perf_rd_beats),
    .wr_beats_o       (hw2reg.perf_wr_beats),
    .rd_stall_o       (hw2reg.perf_rd_stall),
    .wr_stall_o       (hw2reg.perf_wr_stall),
    .drain_cyc_o      (hw2reg.perf_drain_cyc),
    .src_cache_miss_o (hw2reg.perf_src_cache_miss)
  );

  // debug (v1.8, arch.md §9.1)
  assign hw2reg.dbg_status.ram_coll.set = dbg_ram_coll_i;
  g2d_wsig #(.DATA_W(DATA_W)) u_wsig (
    .clk_i,
    .rst_ni,
    .clear_i (reg2hw.dbg_ctrl.sig_clear.qe & reg2hw.dbg_ctrl.sig_clear.q),
    .beat_i  (perf_wr_beat_i),
    .data_i  (dbg_wr_data_i),
    .strb_i  (dbg_wr_strb_i),
    .sig_o   (hw2reg.wr_sig.value.d)
  );

  assign addr_win_lo_o = {reg2hw.addr_win_lo.addr.q, 6'h00};
  assign addr_win_hi_o = {reg2hw.addr_win_hi.addr.q, 6'h3F};
  assign mem_base_hi_o = reg2hw.mem_base_hi.addr.q;
  assign mem_attr_o    = {4'h0, reg2hw.mem_attr.aw_qos.q, 1'b0, reg2hw.mem_attr.aw_prot.q, reg2hw.mem_attr.aw_cache.q,
                          4'h0, reg2hw.mem_attr.ar_qos.q, 1'b0, reg2hw.mem_attr.ar_prot.q, reg2hw.mem_attr.ar_cache.q};
  assign mem_qos_o     = {reg2hw.mem_qos.wr_gap.q, reg2hw.mem_qos.rd_gap.q, 3'h0, reg2hw.mem_qos.wr_max.q,
                          3'h0, reg2hw.mem_qos.rd_max.q};

endmodule
