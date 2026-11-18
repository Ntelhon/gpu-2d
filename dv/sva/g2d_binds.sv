// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Binds of the simulation-only checkers in dv/sva into the RTL (compiled by dv/common/runner.py into
// every bench; a bind whose target module is not in the design has no effect).

bind g2d_axi_mst g2d_axi_sva #(
  .DATA_W          (DATA_W),
  .ADDR_W          (ADDR_W),
  .ID_W            (ID_W),
  .MAX_BURST_BYTES (MAX_BURST_BYTES),
  .RD_OUTSTANDING  (RD_OUTSTANDING),
  .WR_OUTSTANDING  (WR_OUTSTANDING)
) u_axi_sva (.*);

bind g2d_axil2reg g2d_axil_sva u_axil_sva (.*);

// ---- read clients (arch.md §4.3)
bind g2d_cmd_fetch g2d_rdclient_sva u_rdc_sva (.*);
bind g2d_dst_rd    g2d_rdclient_sva u_rdc_sva (.*);
bind g2d_src_cache g2d_rdclient_sva u_rdc_sva (.*);
bind g2d_edge_load g2d_rdclient_sva u_rdc_sva (.*);
bind g2d_ramp      g2d_rdclient_sva u_rdc_sva (.*);

// ---- write producers
bind g2d_fill  g2d_wrprod_sva #(.DATA_W(DATA_W)) u_wrp_sva (.*);
bind g2d_wcomb g2d_wrprod_sva #(.DATA_W(DATA_W)) u_wrp_sva (.*, .stop_i(1'b0));   // never stops a span

// ---- internal streams
bind g2d_cmd_parse g2d_hs_sva #(.W(128), .NAME("draw issue")) u_draw_sva (
  .clk_i, .rst_ni, .en_i(!stop_i), .valid_i(draw_valid_o), .ready_i(draw_ready_i), .data_i(draw_payload_o));
bind g2d_cmd_fetch g2d_hs_sva #(.W(87), .NAME("command words")) u_word_sva (
  .clk_i, .rst_ni, .en_i(!clr_i), .valid_i(word_valid_o), .ready_i(word_ready_i),
  .data_i({word_o, word_addr_o, word_rem_o, word_err_o}));
bind g2d_walk g2d_hs_sva #(.W(28), .NAME("walker pixels")) u_pix_sva (
  .clk_i, .rst_ni, .en_i(!clr_i), .valid_i(pix_valid_o), .ready_i(pix_ready_i), .data_i({pix_x_o, pix_y_o}));
bind g2d_raster g2d_hs_sva #(.W(8), .NAME("path coverage")) u_cov_sva (
  .clk_i, .rst_ni, .en_i(!clr_i), .valid_i(cov_valid_o), .ready_i(cov_ready_i), .data_i(cov_o));
bind g2d_src_cache g2d_hs_sva #(.W(1 + 128 + 16), .NAME("cache texels")) u_tex_sva (
  .clk_i, .rst_ni, .en_i(!clr_i), .valid_i(out_valid_o), .ready_i(out_ready_i),
  .data_i({out_pass_o, out_tex_o, out_fx_o, out_fy_o}));

// ---- invariants
// SRAM FIFOs that rely on credits (or on a full check before the push) never see a push when full
bind g2d_cmd_fetch g2d_fifo_sva u_fifo_sva (.clk_i, .rst_ni, .in_valid_i(rd_rsp_valid_i), .in_ready_o(bf_in_ready));
bind g2d_dst_rd    g2d_fifo_sva u_fifo_sva (.clk_i, .rst_ni, .in_valid_i(rd_rsp_valid_i), .in_ready_o(bf_in_ready));
bind g2d_edge_load g2d_fifo_sva u_fifo_sva (.clk_i, .rst_ni, .in_valid_i(rd_rsp_valid_i), .in_ready_o(bf_in_ready));
bind g2d_src_cache g2d_fifo_sva u_fifo_sva (.clk_i, .rst_ni, .in_valid_i(t1_fire), .in_ready_o(pf_ready));
bind g2d_cmd_fetch g2d_max_sva #(.W(CB_W), .MAX(FIFO_BEATS), .NAME("fetch credits")) u_cr_sva (
  .clk_i, .rst_ni, .value_i(credit_q));
bind g2d_dst_rd g2d_max_sva #(.W(CR_W), .MAX(BEATS), .NAME("destination credits")) u_cr_sva (
  .clk_i, .rst_ni, .value_i(credit_q));
bind g2d_edge_load g2d_max_sva #(.W(CR_W), .MAX(BUF_BEATS), .NAME("edge credits")) u_cr_sva (
  .clk_i, .rst_ni, .value_i(credit_q));
bind g2d_src_cache g2d_lock_sva #(.N(NB * SETS * NW), .LOCK_W(LOCK_W)) u_lock_sva (
  .clk_i, .rst_ni, .locks_i(lock_q));

// ---- clock-domain crossing (v1.1, arch.md §2.1)
bind g2d_cdc_fifo g2d_cdc_sva #(.WIDTH(WIDTH), .LOG_DEPTH(LOG_DEPTH)) u_cdc_sva (.*);
