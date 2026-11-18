// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D core, single clock domain (arch.md §1): CSR subsystem, command fetch/parse, state registers,
// draw check, pixel engines (rect fill fast path, general pipeline) and AXI4 master.
// Read clients: 0 = command fetch, 1 = destination reader, 2 = source cache, 3 = path edges,
// 4 = gradient ramp.
//
// Dispatch: SOLID + blend NONE rects go to g2d_fill, all other draws (and every path) to g2d_pix. A draw is issued only when
// both engines are idle (one write producer at a time). A g2d_pix draw reads memory, so it also waits
// until every earlier write is acknowledged (arch.md §5.3); those cycles count as PERF_DRAIN_CYC.

module g2d_core
  import g2d_pkg::*;
  import g2d_state_pkg::*;
#(
  parameter int unsigned DATA_W          = 64,
  parameter int unsigned ADDR_W          = 32,
  parameter int unsigned ID_W            = 4,
  parameter int unsigned MAX_BURST_BYTES = 256,
  parameter int unsigned RD_OUTSTANDING  = 16,  // v1.7 (Q-PERF-2): was 8
  parameter int unsigned WR_OUTSTANDING  = 8,
  parameter int unsigned KICK_DEPTH      = 4,
  parameter int unsigned SRC_CACHE_BYTES = 8192,
  parameter bit          CFG_ASYNC       = 1'b0,
  parameter bit          MEM_ASYNC       = 1'b0,
  parameter logic [5:0]  FEATURES        = 6'b11_1111,   // v1.7: optional units built (g2d_pkg FEAT_*)
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic                clk_i,
  input  logic                rst_ni,
  // AXI4-Lite slave
  input  logic [31:0]         s_axil_awaddr_i,
  input  logic [2:0]          s_axil_awprot_i,
  input  logic                s_axil_awvalid_i,
  output logic                s_axil_awready_o,
  input  logic [31:0]         s_axil_wdata_i,
  input  logic [3:0]          s_axil_wstrb_i,
  input  logic                s_axil_wvalid_i,
  output logic                s_axil_wready_o,
  output logic [1:0]          s_axil_bresp_o,
  output logic                s_axil_bvalid_o,
  input  logic                s_axil_bready_i,
  input  logic [31:0]         s_axil_araddr_i,
  input  logic [2:0]          s_axil_arprot_i,
  input  logic                s_axil_arvalid_i,
  output logic                s_axil_arready_o,
  output logic [31:0]         s_axil_rdata_o,
  output logic [1:0]          s_axil_rresp_o,
  output logic                s_axil_rvalid_o,
  input  logic                s_axil_rready_i,
  // AXI4 master
  output logic [ID_W-1:0]     m_axi_awid_o,
  output logic [ADDR_W-1:0]   m_axi_awaddr_o,
  output logic [7:0]          m_axi_awlen_o,
  output logic [2:0]          m_axi_awsize_o,
  output logic [1:0]          m_axi_awburst_o,
  output logic                m_axi_awlock_o,
  output logic [3:0]          m_axi_awcache_o,
  output logic [2:0]          m_axi_awprot_o,
  output logic [3:0]          m_axi_awqos_o,
  output logic [3:0]          m_axi_awregion_o,
  output logic                m_axi_awvalid_o,
  input  logic                m_axi_awready_i,
  output logic [DATA_W-1:0]   m_axi_wdata_o,
  output logic [DB-1:0]       m_axi_wstrb_o,
  output logic                m_axi_wlast_o,
  output logic                m_axi_wvalid_o,
  input  logic                m_axi_wready_i,
  input  logic [ID_W-1:0]     m_axi_bid_i,
  input  logic [1:0]          m_axi_bresp_i,
  input  logic                m_axi_bvalid_i,
  output logic                m_axi_bready_o,
  output logic [ID_W-1:0]     m_axi_arid_o,
  output logic [ADDR_W-1:0]   m_axi_araddr_o,
  output logic [7:0]          m_axi_arlen_o,
  output logic [2:0]          m_axi_arsize_o,
  output logic [1:0]          m_axi_arburst_o,
  output logic                m_axi_arlock_o,
  output logic [3:0]          m_axi_arcache_o,
  output logic [2:0]          m_axi_arprot_o,
  output logic [3:0]          m_axi_arqos_o,
  output logic [3:0]          m_axi_arregion_o,
  output logic                m_axi_arvalid_o,
  input  logic                m_axi_arready_i,
  input  logic [ID_W-1:0]     m_axi_rid_i,
  input  logic [DATA_W-1:0]   m_axi_rdata_i,
  input  logic [1:0]          m_axi_rresp_i,
  input  logic                m_axi_rlast_i,
  input  logic                m_axi_rvalid_i,
  output logic                m_axi_rready_o,
  // interrupt
  output logic                irq_o,
  output logic                irq_new_o   // v1.9: newly pending enabled IRQ bit (one cycle)
);

  localparam int unsigned N_RD = 6;   // cmd, dst, src, edge, ramp, mask (v1.6)

  // ---------------------------------------------------------------- signals
  logic [31:0] win_lo, win_hi, base_hi;
  logic [31:0] mem_attr, mem_qos;   // v1.9 CSR MEM_ATTR / MEM_QOS
  logic        kick_valid, kick_ready;
  logic [31:0] kick_addr;
  logic [23:0] kick_size;
  logic        stop, soft_busy, halt_cmd, halted, soft_clr, idle_unused;

  // fetch
  logic        f_err_decode, f_err_range, f_busy, f_pend;
  logic [31:0] f_err_addr, f_err_cmd_addr;
  logic        word_valid, word_ready, word_err;
  logic [31:0] word, word_addr;
  logic [21:0] word_rem;

  // parser
  logic        p_err_decode, p_err_bad, p_err_range, p_busy, p_halt, p_exec;
  logic [31:0] p_err_addr, p_err_cmd_addr, cur_cmd_addr;
  logic        st_we;
  logic [STATE_IDX_BITS-1:0] st_idx;
  logic [31:0] st_wdata;
  logic [STATE_SIZE-1:0][31:0] state;
  logic        chk_start, chk_path, chk_done, chk_bad, chk_range;
  logic [31:0] chk_edge_addr, chk_err_addr;
  logic [23:0] chk_edge_count;
  logic        draw_valid, draw_ready, draw_path;
  logic [3:0][31:0] draw_payload;
  logic        fence, fence_irq;
  logic [31:0] fence_value;

  // engines
  logic        eng_idle, draw_done;
  logic [13:0] pixels;
  logic        fill_idle, fill_done, fill_valid, fill_ready;
  logic [13:0] fill_pixels;
  logic        pix_idle, pix_done, pix_valid, pix_ready, pix_pixel;
  logic [2:0]  src_miss;
  logic [6:0]  ram_coll;     // v1.8: RAM read/write collisions (DBG_STATUS.RAM_COLL)
  logic        is_fill, drain_wait;
  logic        f_wr_req_valid, f_wr_dat_valid, p_wr_req_valid, p_wr_dat_valid, p_wr_req_ready, p_wr_dat_ready;
  logic        f_wr_req_ready, f_wr_dat_ready;
  logic [31:0] f_wr_req_addr, p_wr_req_addr;
  logic [15:0] f_wr_req_len, p_wr_req_len;
  logic [DATA_W-1:0] f_wr_dat_data, p_wr_dat_data;
  logic [DB-1:0]     f_wr_dat_strb, p_wr_dat_strb;

  // AXI master
  logic [N_RD-1:0]       rd_req_valid, rd_req_ready, rd_rsp_valid, rd_rsp_ready;
  logic [N_RD-1:0][31:0] rd_req_addr;
  logic [N_RD-1:0][15:0] rd_req_len;
  logic [DATA_W-1:0]     rd_rsp_data;
  logic [DB-1:0]         rd_rsp_strb;
  logic                  rd_rsp_last_unused, rd_rsp_err;
  logic                  wr_req_valid, wr_req_ready, wr_dat_valid, wr_dat_ready;
  logic [31:0]           wr_req_addr;
  logic [15:0]           wr_req_len;
  logic [DATA_W-1:0]     wr_dat_data;
  logic [DB-1:0]         wr_dat_strb;
  logic                  rd_idle, wr_idle, axi_err_rd, axi_err_wr, axi_err_win;
  logic [31:0]           axi_err_addr;
  logic                  perf_rd_beat, perf_wr_beat, perf_rd_stall, perf_wr_stall;

  // stop (halted or soft reset): the command side issues nothing new. soft_busy (soft reset only): the
  // engines and the AXI master issue nothing new either. On a halt, draws issued earlier complete.
  // The parser stops at once on halt_cmd; after an AXI_RD error it stops at the faulting word.
  // After an AXI_RD halt (halt_cmd clear) the parser goes on with the commands before the faulting word
  // (arch.md §8): until it reaches that word, or runs out of words that can still arrive, the command
  // side is live and the core is not drained (B-6.4: it looked drained between two commands, so a
  // waiter saw the halt as settled while earlier commands were still to run). Live = the parser can
  // still progress: it is checking / issuing / fencing (needs no new word), or command beats are
  // buffered or in flight. A parser waiting in a header or payload for words the stopped fetch will
  // never request is settled (B-6.5).
  logic core_busy, drained, cmd_live;
  assign cmd_live  = halted & ~halt_cmd & ~soft_busy & ~p_halt & (p_exec | f_pend);
  assign drained   = rd_idle & wr_idle & eng_idle & ~cmd_live;
  assign core_busy = ~drained | ((f_busy | p_busy) & ~halted);

  // Command errors: kick checks (fetch) and command/draw errors (parser) never coincide (the parser
  // consumes words of a buffer only after its kick passed), so they are simply merged.
  logic        c_err_decode, c_err_bad, c_err_range;
  logic [31:0] c_err_addr, c_err_cmd_addr;
  assign c_err_decode   = f_err_decode | p_err_decode;
  assign c_err_bad      = p_err_bad;
  assign c_err_range    = f_err_range | p_err_range;
  assign c_err_addr     = (f_err_decode | f_err_range) ? f_err_addr : p_err_addr;
  assign c_err_cmd_addr = (f_err_decode | f_err_range) ? f_err_cmd_addr : p_err_cmd_addr;

  // ---------------------------------------------------------------- CSR
  g2d_csr_top #(
    .KICK_DEPTH (KICK_DEPTH),
    .DATA_W     (DATA_W),
    .ADDR_W     (ADDR_W),
    .CFG_ASYNC  (CFG_ASYNC),
    .MEM_ASYNC  (MEM_ASYNC),
    .FEATURES   (FEATURES),
    .PIX_W      (14)
  ) u_csr (
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
    .addr_win_lo_o        (win_lo),
    .addr_win_hi_o        (win_hi),
    .mem_base_hi_o        (base_hi),
    .mem_attr_o           (mem_attr),
    .mem_qos_o            (mem_qos),
    .kick_valid_o         (kick_valid),
    .kick_ready_i         (kick_ready),
    .kick_addr_o          (kick_addr),
    .kick_size_o          (kick_size),
    .cmd_err_decode_i     (c_err_decode),
    .cmd_err_bad_state_i  (c_err_bad),
    .cmd_err_addr_range_i (c_err_range),
    .cmd_err_addr_i       (c_err_addr),
    .cmd_err_cmd_addr_i   (c_err_cmd_addr),
    .axi_err_rd_i         (axi_err_rd),
    .axi_err_wr_i         (axi_err_wr),
    .axi_err_win_i        (axi_err_win),
    .axi_err_addr_i       (axi_err_addr),
    .cur_cmd_addr_i       (cur_cmd_addr),
    .fence_i              (fence),
    .fence_value_i        (fence_value),
    .fence_irq_i          (fence_irq),
    .core_busy_i          (core_busy),
    .drained_i            (drained),
    .perf_draw_done_i     (draw_done),
    .perf_pixels_i        (pixels),
    .perf_rd_beat_i       (perf_rd_beat),
    .perf_wr_beat_i       (perf_wr_beat),
    .perf_rd_stall_i      (perf_rd_stall),
    .perf_wr_stall_i      (perf_wr_stall),
    .perf_drain_i         (drain_wait),
    .perf_src_miss_i      (src_miss),
    .dbg_wr_data_i        (m_axi_wdata_o),
    .dbg_wr_strb_i        (m_axi_wstrb_o),
    .dbg_ram_coll_i       (ram_coll),
    .stop_o               (stop),
    .soft_busy_o          (soft_busy),
    .halt_cmd_o           (halt_cmd),
    .halted_o             (halted),
    .soft_clr_o           (soft_clr),
    .idle_o               (idle_unused),
    .irq_o,
    .irq_new_o
  );

  // ---------------------------------------------------------------- command processor
  g2d_cmd_fetch #(
    .DATA_W          (DATA_W),
    .MAX_BURST_BYTES (MAX_BURST_BYTES)
  ) u_fetch (
    .clk_i,
    .rst_ni,
    .clr_i          (soft_clr),
    .stop_i         (stop),
    .parse_busy_i   (p_busy),
    .win_lo_i       (win_lo),
    .win_hi_i       (win_hi),
    .kick_valid_i   (kick_valid),
    .kick_ready_o   (kick_ready),
    .kick_addr_i    (kick_addr),
    .kick_size_i    (kick_size),
    .rd_req_valid_o (rd_req_valid[0]),
    .rd_req_ready_i (rd_req_ready[0]),
    .rd_req_addr_o  (rd_req_addr[0]),
    .rd_req_len_o   (rd_req_len[0]),
    .rd_rsp_valid_i (rd_rsp_valid[0]),
    .rd_rsp_ready_o (rd_rsp_ready[0]),
    .rd_rsp_data_i  (rd_rsp_data),
    .rd_rsp_strb_i  (rd_rsp_strb),
    .rd_rsp_err_i   (rd_rsp_err),
    .word_valid_o   (word_valid),
    .word_ready_i   (word_ready),
    .word_o         (word),
    .word_addr_o    (word_addr),
    .word_rem_o     (word_rem),
    .word_err_o     (word_err),
    .err_decode_o   (f_err_decode),
    .err_range_o    (f_err_range),
    .err_addr_o     (f_err_addr),
    .err_cmd_addr_o (f_err_cmd_addr),
    .busy_o         (f_busy),
    .pend_o         (f_pend)
  );

  g2d_cmd_parse u_parse (
    .clk_i,
    .rst_ni,
    .clr_i            (soft_clr),
    .stop_i           (soft_busy | halt_cmd),
    .word_valid_i     (word_valid),
    .word_ready_o     (word_ready),
    .word_i           (word),
    .word_addr_i      (word_addr),
    .word_rem_i       (word_rem),
    .word_err_i       (word_err),
    .st_we_o          (st_we),
    .st_idx_o         (st_idx),
    .st_wdata_o       (st_wdata),
    .chk_start_o      (chk_start),
    .chk_path_o       (chk_path),
    .chk_edge_addr_o  (chk_edge_addr),
    .chk_edge_count_o (chk_edge_count),
    .chk_done_i       (chk_done),
    .chk_bad_state_i  (chk_bad),
    .chk_addr_range_i (chk_range),
    .chk_err_addr_i   (chk_err_addr),
    .draw_valid_o     (draw_valid),
    .draw_ready_i     (draw_ready),
    .draw_path_o      (draw_path),
    .draw_payload_o   (draw_payload),
    .engine_idle_i    (eng_idle),
    .wr_idle_i        (wr_idle),
    .fence_o          (fence),
    .fence_value_o    (fence_value),
    .fence_irq_o      (fence_irq),
    .err_decode_o     (p_err_decode),
    .err_bad_state_o  (p_err_bad),
    .err_range_o      (p_err_range),
    .err_addr_o       (p_err_addr),
    .err_cmd_addr_o   (p_err_cmd_addr),
    .cur_cmd_addr_o   (cur_cmd_addr),
    .busy_o           (p_busy),
    .halt_o           (p_halt),
    .exec_o           (p_exec)
  );

  g2d_state u_state (
    .clk_i,
    .rst_ni,
    .clr_i   (soft_clr),
    .we_i    (st_we),
    .idx_i   (st_idx),
    .wdata_i (st_wdata),
    .state_o (state)
  );

  g2d_draw_check #(.FEATURES(FEATURES)) u_check (
    .clk_i,
    .rst_ni,
    .start_i      (chk_start),
    .state_i      (state),
    .is_path_i    (chk_path),
    .edge_addr_i  (chk_edge_addr),
    .edge_count_i (chk_edge_count),
    .win_lo_i     (win_lo),
    .win_hi_i     (win_hi),
    .done_o       (chk_done),
    .bad_state_o  (chk_bad),
    .addr_range_o (chk_range),
    .err_addr_o   (chk_err_addr)
  );

  // ---------------------------------------------------------------- dispatch
  // The parser holds the state stable while a draw waits to be issued.
  assign is_fill    = state[G2D_ST_PAINT_CTRL][G2D_ST_PAINT_CTRL_SRC_LSB +: 2] == PAINT_SOLID &&
                      state[G2D_ST_BLEND][G2D_ST_BLEND_MODE_LSB +: 4] == 4'd0 && !draw_path &&
                      state[G2D_ST_BLEND][G2D_ST_BLEND_FAMILY_LSB +: 2] == 2'd0 &&      // v1.6 families
                      !state[G2D_ST_TGT_FMT][G2D_ST_TGT_FMT_DITHER_LSB] &&  // dithered fills vary per pixel (v1.4)
                      !state[G2D_ST_MASK_CTRL][G2D_ST_MASK_CTRL_EN_LSB];    // masked fills too (v1.6)
  assign fill_valid = draw_valid & is_fill & pix_idle;
  assign pix_valid  = draw_valid & ~is_fill & fill_idle & wr_idle;
  assign draw_ready = is_fill ? (fill_ready & pix_idle) : (pix_ready & fill_idle & wr_idle);
  assign drain_wait = draw_valid & ~is_fill & pix_idle & ~(fill_idle & wr_idle);

  assign eng_idle  = fill_idle & pix_idle;
  assign draw_done = fill_done | pix_done;
  assign pixels    = fill_pixels + 14'(pix_pixel);

  // ---------------------------------------------------------------- pixel engines
  g2d_fill #(.DATA_W(DATA_W)) u_fill (
    .clk_i,
    .rst_ni,
    .clr_i          (soft_clr),
    .stop_i         (soft_busy),
    .draw_valid_i   (fill_valid),
    .draw_ready_o   (fill_ready),
    .draw_payload_i (draw_payload),
    .state_i        (state),
    .wr_req_valid_o (f_wr_req_valid),
    .wr_req_ready_i (f_wr_req_ready),
    .wr_req_addr_o  (f_wr_req_addr),
    .wr_req_len_o   (f_wr_req_len),
    .wr_dat_valid_o (f_wr_dat_valid),
    .wr_dat_ready_i (f_wr_dat_ready),
    .wr_dat_data_o  (f_wr_dat_data),
    .wr_dat_strb_o  (f_wr_dat_strb),
    .idle_o         (fill_idle),
    .draw_done_o    (fill_done),
    .pixels_o       (fill_pixels)
  );

  g2d_pix #(.DATA_W(DATA_W), .SRC_CACHE_BYTES(SRC_CACHE_BYTES), .FEATURES(FEATURES)) u_pix (
    .clk_i,
    .rst_ni,
    .clr_i           (soft_clr),
    .stop_i          (soft_busy),
    .draw_valid_i    (pix_valid),
    .draw_ready_o    (pix_ready),
    .draw_path_i     (draw_path),
    .draw_payload_i  (draw_payload),
    .state_i         (state),
    .dst_req_valid_o (rd_req_valid[1]),
    .dst_req_ready_i (rd_req_ready[1]),
    .dst_req_addr_o  (rd_req_addr[1]),
    .dst_req_len_o   (rd_req_len[1]),
    .dst_rsp_valid_i (rd_rsp_valid[1]),
    .dst_rsp_ready_o (rd_rsp_ready[1]),
    .src_req_valid_o (rd_req_valid[2]),
    .src_req_ready_i (rd_req_ready[2]),
    .src_req_addr_o  (rd_req_addr[2]),
    .src_req_len_o   (rd_req_len[2]),
    .src_rsp_valid_i (rd_rsp_valid[2]),
    .src_rsp_ready_o (rd_rsp_ready[2]),
    .edge_req_valid_o (rd_req_valid[3]),
    .edge_req_ready_i (rd_req_ready[3]),
    .edge_req_addr_o  (rd_req_addr[3]),
    .edge_req_len_o   (rd_req_len[3]),
    .edge_rsp_valid_i (rd_rsp_valid[3]),
    .edge_rsp_ready_o (rd_rsp_ready[3]),
    .ramp_req_valid_o (rd_req_valid[4]),
    .ramp_req_ready_i (rd_req_ready[4]),
    .ramp_req_addr_o  (rd_req_addr[4]),
    .ramp_req_len_o   (rd_req_len[4]),
    .ramp_rsp_valid_i (rd_rsp_valid[4]),
    .ramp_rsp_ready_o (rd_rsp_ready[4]),
    .mask_req_valid_o (rd_req_valid[5]),
    .mask_req_ready_i (rd_req_ready[5]),
    .mask_req_addr_o  (rd_req_addr[5]),
    .mask_req_len_o   (rd_req_len[5]),
    .mask_rsp_valid_i (rd_rsp_valid[5]),
    .mask_rsp_ready_o (rd_rsp_ready[5]),
    .rsp_data_i      (rd_rsp_data),
    .rsp_strb_i      (rd_rsp_strb),
    .kick_i          (kick_valid & kick_ready),
    .wr_i            (wr_req_valid & wr_req_ready),
    .wr_req_valid_o  (p_wr_req_valid),
    .wr_req_ready_i  (p_wr_req_ready),
    .wr_req_addr_o   (p_wr_req_addr),
    .wr_req_len_o    (p_wr_req_len),
    .wr_dat_valid_o  (p_wr_dat_valid),
    .wr_dat_ready_i  (p_wr_dat_ready),
    .wr_dat_data_o   (p_wr_dat_data),
    .wr_dat_strb_o   (p_wr_dat_strb),
    .idle_o          (pix_idle),
    .draw_done_o     (pix_done),
    .pixel_o         (pix_pixel),
    .src_miss_o      (src_miss),
    .ram_coll_o      (ram_coll)
  );

  // write port: the busy engine drives it (never both, see dispatch)
  assign wr_req_valid   = pix_idle ? f_wr_req_valid : p_wr_req_valid;
  assign wr_req_addr    = pix_idle ? f_wr_req_addr  : p_wr_req_addr;
  assign wr_req_len     = pix_idle ? f_wr_req_len   : p_wr_req_len;
  assign wr_dat_valid   = pix_idle ? f_wr_dat_valid : p_wr_dat_valid;
  assign wr_dat_data    = pix_idle ? f_wr_dat_data  : p_wr_dat_data;
  assign wr_dat_strb    = pix_idle ? f_wr_dat_strb  : p_wr_dat_strb;
  assign f_wr_req_ready = pix_idle & wr_req_ready;
  assign f_wr_dat_ready = pix_idle & wr_dat_ready;
  assign p_wr_req_ready = ~pix_idle & wr_req_ready;
  assign p_wr_dat_ready = ~pix_idle & wr_dat_ready;

  // ---------------------------------------------------------------- AXI4 master
  g2d_axi_mst #(
    .N_RD            (N_RD),
    .DATA_W          (DATA_W),
    .ADDR_W          (ADDR_W),
    .ID_W            (ID_W),
    .MAX_BURST_BYTES (MAX_BURST_BYTES),
    .RD_OUTSTANDING  (RD_OUTSTANDING),
    .WR_OUTSTANDING  (WR_OUTSTANDING)
  ) u_axi (
    .clk_i,
    .rst_ni,
    .clr_i           (soft_clr),
    .stop_i          (soft_busy),
    .win_lo_i        (win_lo),
    .win_hi_i        (win_hi),
    .base_hi_i       (base_hi),
    .ar_cache_i      (mem_attr[3:0]),
    .ar_prot_i       (mem_attr[6:4]),
    .ar_qos_i        (mem_attr[11:8]),
    .aw_cache_i      (mem_attr[19:16]),
    .aw_prot_i       (mem_attr[22:20]),
    .aw_qos_i        (mem_attr[27:24]),
    .rd_max_out_i    (mem_qos[4:0]),
    .wr_max_out_i    (mem_qos[12:8]),
    .rd_gap_i        (mem_qos[23:16]),
    .wr_gap_i        (mem_qos[31:24]),
    .rd_req_valid_i  (rd_req_valid),
    .rd_req_ready_o  (rd_req_ready),
    .rd_req_addr_i   (rd_req_addr),
    .rd_req_len_i    (rd_req_len),
    .rd_rsp_valid_o  (rd_rsp_valid),
    .rd_rsp_ready_i  (rd_rsp_ready),
    .rd_rsp_data_o   (rd_rsp_data),
    .rd_rsp_strb_o   (rd_rsp_strb),
    .rd_rsp_last_o   (rd_rsp_last_unused),
    .rd_rsp_err_o    (rd_rsp_err),
    .wr_req_valid_i  (wr_req_valid),
    .wr_req_ready_o  (wr_req_ready),
    .wr_req_addr_i   (wr_req_addr),
    .wr_req_len_i    (wr_req_len),
    .wr_dat_valid_i  (wr_dat_valid),
    .wr_dat_ready_o  (wr_dat_ready),
    .wr_dat_data_i   (wr_dat_data),
    .wr_dat_strb_i   (wr_dat_strb),
    .m_axi_awid_o,
    .m_axi_awaddr_o,
    .m_axi_awlen_o,
    .m_axi_awsize_o,
    .m_axi_awburst_o,
    .m_axi_awlock_o,
    .m_axi_awcache_o,
    .m_axi_awprot_o,
    .m_axi_awqos_o,
    .m_axi_awregion_o,
    .m_axi_awvalid_o,
    .m_axi_awready_i,
    .m_axi_wdata_o,
    .m_axi_wstrb_o,
    .m_axi_wlast_o,
    .m_axi_wvalid_o,
    .m_axi_wready_i,
    .m_axi_bid_i,
    .m_axi_bresp_i,
    .m_axi_bvalid_i,
    .m_axi_bready_o,
    .m_axi_arid_o,
    .m_axi_araddr_o,
    .m_axi_arlen_o,
    .m_axi_arsize_o,
    .m_axi_arburst_o,
    .m_axi_arlock_o,
    .m_axi_arcache_o,
    .m_axi_arprot_o,
    .m_axi_arqos_o,
    .m_axi_arregion_o,
    .m_axi_arvalid_o,
    .m_axi_arready_i,
    .m_axi_rid_i,
    .m_axi_rdata_i,
    .m_axi_rresp_i,
    .m_axi_rlast_i,
    .m_axi_rvalid_i,
    .m_axi_rready_o,
    .rd_idle_o       (rd_idle),
    .wr_idle_o       (wr_idle),
    .err_rd_o        (axi_err_rd),
    .err_wr_o        (axi_err_wr),
    .err_win_o       (axi_err_win),
    .err_addr_o      (axi_err_addr),
    .perf_rd_beat_o  (perf_rd_beat),
    .perf_wr_beat_o  (perf_wr_beat),
    .perf_rd_stall_o (perf_rd_stall),
    .perf_wr_stall_o (perf_wr_stall)
  );

  logic unused;
  assign unused = ^{idle_unused, rd_rsp_last_unused, mem_attr[31:28], mem_attr[23], mem_attr[15:12], mem_attr[7],
                    mem_qos[15:13], mem_qos[7:5]};   // reserved register bits

endmodule
