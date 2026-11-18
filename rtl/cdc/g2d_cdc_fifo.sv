// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Dual-clock FIFO (arch.md §2.1), Cummings-style gray pointers, valid/ready on both sides.
//
// Each side keeps a binary pointer with one wrap bit (LOG_DEPTH + 1 bits) and a registered gray copy of it. Only
// the gray copies cross, each through a g2d_sync in the other domain. The source is full when its pointer is
// DEPTH ahead of the synchronized read pointer, the destination has data while the synchronized write pointer
// differs from its read pointer; both views are conservative, so neither side can overrun the other.
// Storage is a flop array written in the source domain and read (combinationally, at the read pointer) in the
// destination domain; an entry is only read after its write has become visible through the synchronized
// pointer, so the read data is stable when used.
//
// Resets: both are asserted together (asynchronously) and released each in its own domain (arch.md §2.2).
// Latency: one source cycle plus SYNC_STAGES (+1) destination cycles. With DEPTH >= 2 * (SYNC_STAGES + 2) the
// FIFO streams one word per cycle of the slower clock.

module g2d_cdc_fifo #(
  parameter int unsigned WIDTH       = 8,
  parameter int unsigned LOG_DEPTH   = 1,
  parameter int unsigned SYNC_STAGES = 2,
  localparam int unsigned DEPTH = 1 << LOG_DEPTH,
  localparam int unsigned PW    = LOG_DEPTH + 1
) (
  // source domain
  input  logic             src_clk_i,
  input  logic             src_rst_ni,
  input  logic             src_valid_i,
  output logic             src_ready_o,
  input  logic [WIDTH-1:0] src_data_i,
  // destination domain
  input  logic             dst_clk_i,
  input  logic             dst_rst_ni,
  output logic             dst_valid_o,
  input  logic             dst_ready_i,
  output logic [WIDTH-1:0] dst_data_o
);

  if (LOG_DEPTH < 1) begin : g_bad_depth
    $error("g2d_cdc_fifo: LOG_DEPTH must be >= 1");
  end

  function automatic logic [PW-1:0] bin2gray(logic [PW-1:0] b);
    return b ^ (b >> 1);
  endfunction

  function automatic logic [PW-1:0] gray2bin(logic [PW-1:0] g);
    logic [PW-1:0] b;
    b[PW-1] = g[PW-1];
    for (int i = PW - 2; i >= 0; i--) b[i] = b[i+1] ^ g[i];
    return b;
  endfunction

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [PW-1:0]    wbin_q, wgray_q, rgray_s, rbin_s, wbin_d;   // source domain
  logic [PW-1:0]    rbin_q, rgray_q, wgray_s, rbin_d;           // destination domain
  logic             push, pop;

  // ---- source domain

  g2d_sync #(.STAGES(SYNC_STAGES), .WIDTH(PW)) u_sync_r2w (
    .clk_i  (src_clk_i),
    .rst_ni (src_rst_ni),
    .d_i    (rgray_q),
    .q_o    (rgray_s)
  );

  assign rbin_s      = gray2bin(rgray_s);
  assign src_ready_o = (wbin_q - rbin_s) != PW'(DEPTH);
  assign push        = src_valid_i & src_ready_o;
  assign wbin_d      = wbin_q + PW'(push);

  always_ff @(posedge src_clk_i or negedge src_rst_ni) begin
    if (!src_rst_ni) begin
      wbin_q  <= '0;
      wgray_q <= '0;
    end else begin
      wbin_q  <= wbin_d;
      wgray_q <= bin2gray(wbin_d);
    end
  end

  // Storage needs no reset: an entry is only read after it was written.
  always_ff @(posedge src_clk_i) begin
    if (push) mem_q[wbin_q[LOG_DEPTH-1:0]] <= src_data_i;
  end

  // ---- destination domain

  g2d_sync #(.STAGES(SYNC_STAGES), .WIDTH(PW)) u_sync_w2r (
    .clk_i  (dst_clk_i),
    .rst_ni (dst_rst_ni),
    .d_i    (wgray_q),
    .q_o    (wgray_s)
  );

  assign dst_valid_o = (rgray_q != wgray_s);
  assign dst_data_o  = mem_q[rbin_q[LOG_DEPTH-1:0]];
  assign pop         = dst_valid_o & dst_ready_i;
  assign rbin_d      = rbin_q + PW'(pop);

  always_ff @(posedge dst_clk_i or negedge dst_rst_ni) begin
    if (!dst_rst_ni) begin
      rbin_q  <= '0;
      rgray_q <= '0;
    end else begin
      rbin_q  <= rbin_d;
      rgray_q <= bin2gray(rbin_d);
    end
  end

endmodule
