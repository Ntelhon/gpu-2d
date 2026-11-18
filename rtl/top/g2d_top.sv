// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D top level (arch.md §1, §2.1, §4.1). Three clock/reset pairs: cfg (AXI4-Lite side), core and mem
// (AXI4 side). CFG_ASYNC = 0 / MEM_ASYNC = 0: the integrator ties the port clock to clk_i and the port is
// wired straight to the core (clk_cfg_i / clk_mem_i and their resets unused). CFG_ASYNC = 1 puts g2d_cdc_axil
// between s_axil_* (cfg domain) and the core, MEM_ASYNC = 1 puts g2d_cdc_axi between the core and m_axi_*
// (mem domain). With either set, the three resets are asserted together (arch.md §2.2). irq_o is always in
// the core domain: a level (default) or, with IRQ_PULSE, one cycle per newly pending enabled IRQ_STATUS bit;
// active low with IRQ_ACTIVE_LOW (v1.9). test_mode_i (v1.9, DFT): 1 bypasses the reset distribution stage so the
// core reset is directly controllable from rst_ni during scan; tie to 0 in functional use.

module g2d_top #(
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
  parameter int unsigned RST_STAGES      = 2,      // v1.7: reset distribution stages per domain (g2d_rst_sync)
  parameter int unsigned SYNC_STAGES     = 2,
  parameter int unsigned CFG_CDC_LOG_DEPTH = 1,
  parameter int unsigned MEM_CDC_LOG_DEPTH = 3,
  parameter bit          IRQ_PULSE       = 1'b0,   // v1.9: irq_o as a one-cycle pulse per newly pending bit
  parameter bit          IRQ_ACTIVE_LOW  = 1'b0,   // v1.9: irq_o active low
  localparam int unsigned DB = DATA_W / 8
) (
  input  logic                clk_cfg_i,
  input  logic                rst_cfg_ni,
  input  logic                clk_i,
  input  logic                rst_ni,
  input  logic                clk_mem_i,
  input  logic                rst_mem_ni,
  input  logic                test_mode_i,     // v1.9 DFT: 1 = reset stages bypassed (scan); 0 in functional use
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
  output logic                irq_o
);

  // core-side ports (core domain)
  logic [31:0]         c_axil_awaddr;
  logic [2:0]          c_axil_awprot;
  logic                c_axil_awvalid;
  logic                c_axil_awready;
  logic [31:0]         c_axil_wdata;
  logic [3:0]          c_axil_wstrb;
  logic                c_axil_wvalid;
  logic                c_axil_wready;
  logic [1:0]          c_axil_bresp;
  logic                c_axil_bvalid;
  logic                c_axil_bready;
  logic [31:0]         c_axil_araddr;
  logic [2:0]          c_axil_arprot;
  logic                c_axil_arvalid;
  logic                c_axil_arready;
  logic [31:0]         c_axil_rdata;
  logic [1:0]          c_axil_rresp;
  logic                c_axil_rvalid;
  logic                c_axil_rready;
  logic [ID_W-1:0]     c_axi_awid;
  logic [ADDR_W-1:0]   c_axi_awaddr;
  logic [7:0]          c_axi_awlen;
  logic [2:0]          c_axi_awsize;
  logic [1:0]          c_axi_awburst;
  logic                c_axi_awlock;
  logic [3:0]          c_axi_awcache;
  logic [2:0]          c_axi_awprot;
  logic [3:0]          c_axi_awqos;
  logic [3:0]          c_axi_awregion;
  logic                c_axi_awvalid;
  logic                c_axi_awready;
  logic [DATA_W-1:0]   c_axi_wdata;
  logic [DB-1:0]       c_axi_wstrb;
  logic                c_axi_wlast;
  logic                c_axi_wvalid;
  logic                c_axi_wready;
  logic [ID_W-1:0]     c_axi_bid;
  logic [1:0]          c_axi_bresp;
  logic                c_axi_bvalid;
  logic                c_axi_bready;
  logic [ID_W-1:0]     c_axi_arid;
  logic [ADDR_W-1:0]   c_axi_araddr;
  logic [7:0]          c_axi_arlen;
  logic [2:0]          c_axi_arsize;
  logic [1:0]          c_axi_arburst;
  logic                c_axi_arlock;
  logic [3:0]          c_axi_arcache;
  logic [2:0]          c_axi_arprot;
  logic [3:0]          c_axi_arqos;
  logic [3:0]          c_axi_arregion;
  logic                c_axi_arvalid;
  logic                c_axi_arready;
  logic [ID_W-1:0]     c_axi_rid;
  logic [DATA_W-1:0]   c_axi_rdata;
  logic [1:0]          c_axi_rresp;
  logic                c_axi_rlast;
  logic                c_axi_rvalid;
  logic                c_axi_rready;

  // v1.7 (arch.md §2): the core-domain reset through a distribution stage (de-assert RST_STAGES edges later), which
  // cuts its fan-out; the port domains of the async build hold only one side of the CDC FIFOs and use their resets
  // directly (OPEN_QUESTIONS Q-TEST-5: a stage there made the Verilator simulation spin)
  logic rst_core_n;
  g2d_rst_sync #(.STAGES(RST_STAGES)) u_rst_core (.clk_i, .rst_ni, .test_mode_i, .rst_no (rst_core_n));

  logic irq_lvl, irq_new;
  assign irq_o = IRQ_ACTIVE_LOW ^ (IRQ_PULSE ? irq_new : irq_lvl);

  g2d_core #(
    .DATA_W          (DATA_W),
    .ADDR_W          (ADDR_W),
    .ID_W            (ID_W),
    .MAX_BURST_BYTES (MAX_BURST_BYTES),
    .RD_OUTSTANDING  (RD_OUTSTANDING),
    .WR_OUTSTANDING  (WR_OUTSTANDING),
    .KICK_DEPTH      (KICK_DEPTH),
    .SRC_CACHE_BYTES (SRC_CACHE_BYTES),
    .CFG_ASYNC       (CFG_ASYNC),
    .MEM_ASYNC       (MEM_ASYNC),
    .FEATURES        (FEATURES)
  ) u_core (
    .clk_i,
    .rst_ni          (rst_core_n),
    .s_axil_awaddr_i (c_axil_awaddr),
    .s_axil_awprot_i (c_axil_awprot),
    .s_axil_awvalid_i (c_axil_awvalid),
    .s_axil_awready_o (c_axil_awready),
    .s_axil_wdata_i (c_axil_wdata),
    .s_axil_wstrb_i (c_axil_wstrb),
    .s_axil_wvalid_i (c_axil_wvalid),
    .s_axil_wready_o (c_axil_wready),
    .s_axil_bresp_o (c_axil_bresp),
    .s_axil_bvalid_o (c_axil_bvalid),
    .s_axil_bready_i (c_axil_bready),
    .s_axil_araddr_i (c_axil_araddr),
    .s_axil_arprot_i (c_axil_arprot),
    .s_axil_arvalid_i (c_axil_arvalid),
    .s_axil_arready_o (c_axil_arready),
    .s_axil_rdata_o (c_axil_rdata),
    .s_axil_rresp_o (c_axil_rresp),
    .s_axil_rvalid_o (c_axil_rvalid),
    .s_axil_rready_i (c_axil_rready),
    .m_axi_awid_o (c_axi_awid),
    .m_axi_awaddr_o (c_axi_awaddr),
    .m_axi_awlen_o (c_axi_awlen),
    .m_axi_awsize_o (c_axi_awsize),
    .m_axi_awburst_o (c_axi_awburst),
    .m_axi_awlock_o (c_axi_awlock),
    .m_axi_awcache_o (c_axi_awcache),
    .m_axi_awprot_o (c_axi_awprot),
    .m_axi_awqos_o (c_axi_awqos),
    .m_axi_awregion_o (c_axi_awregion),
    .m_axi_awvalid_o (c_axi_awvalid),
    .m_axi_awready_i (c_axi_awready),
    .m_axi_wdata_o (c_axi_wdata),
    .m_axi_wstrb_o (c_axi_wstrb),
    .m_axi_wlast_o (c_axi_wlast),
    .m_axi_wvalid_o (c_axi_wvalid),
    .m_axi_wready_i (c_axi_wready),
    .m_axi_bid_i (c_axi_bid),
    .m_axi_bresp_i (c_axi_bresp),
    .m_axi_bvalid_i (c_axi_bvalid),
    .m_axi_bready_o (c_axi_bready),
    .m_axi_arid_o (c_axi_arid),
    .m_axi_araddr_o (c_axi_araddr),
    .m_axi_arlen_o (c_axi_arlen),
    .m_axi_arsize_o (c_axi_arsize),
    .m_axi_arburst_o (c_axi_arburst),
    .m_axi_arlock_o (c_axi_arlock),
    .m_axi_arcache_o (c_axi_arcache),
    .m_axi_arprot_o (c_axi_arprot),
    .m_axi_arqos_o (c_axi_arqos),
    .m_axi_arregion_o (c_axi_arregion),
    .m_axi_arvalid_o (c_axi_arvalid),
    .m_axi_arready_i (c_axi_arready),
    .m_axi_rid_i (c_axi_rid),
    .m_axi_rdata_i (c_axi_rdata),
    .m_axi_rresp_i (c_axi_rresp),
    .m_axi_rlast_i (c_axi_rlast),
    .m_axi_rvalid_i (c_axi_rvalid),
    .m_axi_rready_o (c_axi_rready),
    .irq_o           (irq_lvl),
    .irq_new_o       (irq_new)
  );

  if (CFG_ASYNC) begin : g_cfg_cdc
    g2d_cdc_axil #(
      .LOG_DEPTH   (CFG_CDC_LOG_DEPTH),
      .SYNC_STAGES (SYNC_STAGES)
    ) u_cfg_cdc (
      .s_clk_i  (clk_cfg_i),
      .s_rst_ni (rst_cfg_ni),
      .m_clk_i  (clk_i),
      .m_rst_ni (rst_core_n),
      .s_axil_awaddr_i        (s_axil_awaddr_i),
      .m_axil_awaddr_o        (c_axil_awaddr),
      .s_axil_awprot_i        (s_axil_awprot_i),
      .m_axil_awprot_o        (c_axil_awprot),
      .s_axil_awvalid_i       (s_axil_awvalid_i),
      .m_axil_awvalid_o       (c_axil_awvalid),
      .s_axil_awready_o       (s_axil_awready_o),
      .m_axil_awready_i       (c_axil_awready),
      .s_axil_wdata_i         (s_axil_wdata_i),
      .m_axil_wdata_o         (c_axil_wdata),
      .s_axil_wstrb_i         (s_axil_wstrb_i),
      .m_axil_wstrb_o         (c_axil_wstrb),
      .s_axil_wvalid_i        (s_axil_wvalid_i),
      .m_axil_wvalid_o        (c_axil_wvalid),
      .s_axil_wready_o        (s_axil_wready_o),
      .m_axil_wready_i        (c_axil_wready),
      .s_axil_bresp_o         (s_axil_bresp_o),
      .m_axil_bresp_i         (c_axil_bresp),
      .s_axil_bvalid_o        (s_axil_bvalid_o),
      .m_axil_bvalid_i        (c_axil_bvalid),
      .s_axil_bready_i        (s_axil_bready_i),
      .m_axil_bready_o        (c_axil_bready),
      .s_axil_araddr_i        (s_axil_araddr_i),
      .m_axil_araddr_o        (c_axil_araddr),
      .s_axil_arprot_i        (s_axil_arprot_i),
      .m_axil_arprot_o        (c_axil_arprot),
      .s_axil_arvalid_i       (s_axil_arvalid_i),
      .m_axil_arvalid_o       (c_axil_arvalid),
      .s_axil_arready_o       (s_axil_arready_o),
      .m_axil_arready_i       (c_axil_arready),
      .s_axil_rdata_o         (s_axil_rdata_o),
      .m_axil_rdata_i         (c_axil_rdata),
      .s_axil_rresp_o         (s_axil_rresp_o),
      .m_axil_rresp_i         (c_axil_rresp),
      .s_axil_rvalid_o        (s_axil_rvalid_o),
      .m_axil_rvalid_i        (c_axil_rvalid),
      .s_axil_rready_i        (s_axil_rready_i),
      .m_axil_rready_o        (c_axil_rready)
    );
  end else begin : g_cfg_wire
    assign c_axil_awaddr = s_axil_awaddr_i;
    assign c_axil_awprot = s_axil_awprot_i;
    assign c_axil_awvalid = s_axil_awvalid_i;
    assign s_axil_awready_o = c_axil_awready;
    assign c_axil_wdata = s_axil_wdata_i;
    assign c_axil_wstrb = s_axil_wstrb_i;
    assign c_axil_wvalid = s_axil_wvalid_i;
    assign s_axil_wready_o = c_axil_wready;
    assign s_axil_bresp_o = c_axil_bresp;
    assign s_axil_bvalid_o = c_axil_bvalid;
    assign c_axil_bready = s_axil_bready_i;
    assign c_axil_araddr = s_axil_araddr_i;
    assign c_axil_arprot = s_axil_arprot_i;
    assign c_axil_arvalid = s_axil_arvalid_i;
    assign s_axil_arready_o = c_axil_arready;
    assign s_axil_rdata_o = c_axil_rdata;
    assign s_axil_rresp_o = c_axil_rresp;
    assign s_axil_rvalid_o = c_axil_rvalid;
    assign c_axil_rready = s_axil_rready_i;
  end

  if (MEM_ASYNC) begin : g_mem_cdc
    g2d_cdc_axi #(
      .DATA_W      (DATA_W),
      .ADDR_W      (ADDR_W),
      .ID_W        (ID_W),
      .LOG_DEPTH   (MEM_CDC_LOG_DEPTH),
      .SYNC_STAGES (SYNC_STAGES)
    ) u_mem_cdc (
      .s_clk_i  (clk_i),
      .s_rst_ni (rst_core_n),
      .m_clk_i  (clk_mem_i),
      .m_rst_ni (rst_mem_ni),
      .s_axi_awid_i          (c_axi_awid),
      .m_axi_awid_o           (m_axi_awid_o),
      .s_axi_awaddr_i        (c_axi_awaddr),
      .m_axi_awaddr_o         (m_axi_awaddr_o),
      .s_axi_awlen_i         (c_axi_awlen),
      .m_axi_awlen_o          (m_axi_awlen_o),
      .s_axi_awsize_i        (c_axi_awsize),
      .m_axi_awsize_o         (m_axi_awsize_o),
      .s_axi_awburst_i       (c_axi_awburst),
      .m_axi_awburst_o        (m_axi_awburst_o),
      .s_axi_awlock_i        (c_axi_awlock),
      .m_axi_awlock_o         (m_axi_awlock_o),
      .s_axi_awcache_i       (c_axi_awcache),
      .m_axi_awcache_o        (m_axi_awcache_o),
      .s_axi_awprot_i        (c_axi_awprot),
      .m_axi_awprot_o         (m_axi_awprot_o),
      .s_axi_awqos_i         (c_axi_awqos),
      .m_axi_awqos_o          (m_axi_awqos_o),
      .s_axi_awregion_i      (c_axi_awregion),
      .m_axi_awregion_o       (m_axi_awregion_o),
      .s_axi_awvalid_i       (c_axi_awvalid),
      .m_axi_awvalid_o        (m_axi_awvalid_o),
      .s_axi_awready_o       (c_axi_awready),
      .m_axi_awready_i        (m_axi_awready_i),
      .s_axi_wdata_i         (c_axi_wdata),
      .m_axi_wdata_o          (m_axi_wdata_o),
      .s_axi_wstrb_i         (c_axi_wstrb),
      .m_axi_wstrb_o          (m_axi_wstrb_o),
      .s_axi_wlast_i         (c_axi_wlast),
      .m_axi_wlast_o          (m_axi_wlast_o),
      .s_axi_wvalid_i        (c_axi_wvalid),
      .m_axi_wvalid_o         (m_axi_wvalid_o),
      .s_axi_wready_o        (c_axi_wready),
      .m_axi_wready_i         (m_axi_wready_i),
      .s_axi_bid_o           (c_axi_bid),
      .m_axi_bid_i            (m_axi_bid_i),
      .s_axi_bresp_o         (c_axi_bresp),
      .m_axi_bresp_i          (m_axi_bresp_i),
      .s_axi_bvalid_o        (c_axi_bvalid),
      .m_axi_bvalid_i         (m_axi_bvalid_i),
      .s_axi_bready_i        (c_axi_bready),
      .m_axi_bready_o         (m_axi_bready_o),
      .s_axi_arid_i          (c_axi_arid),
      .m_axi_arid_o           (m_axi_arid_o),
      .s_axi_araddr_i        (c_axi_araddr),
      .m_axi_araddr_o         (m_axi_araddr_o),
      .s_axi_arlen_i         (c_axi_arlen),
      .m_axi_arlen_o          (m_axi_arlen_o),
      .s_axi_arsize_i        (c_axi_arsize),
      .m_axi_arsize_o         (m_axi_arsize_o),
      .s_axi_arburst_i       (c_axi_arburst),
      .m_axi_arburst_o        (m_axi_arburst_o),
      .s_axi_arlock_i        (c_axi_arlock),
      .m_axi_arlock_o         (m_axi_arlock_o),
      .s_axi_arcache_i       (c_axi_arcache),
      .m_axi_arcache_o        (m_axi_arcache_o),
      .s_axi_arprot_i        (c_axi_arprot),
      .m_axi_arprot_o         (m_axi_arprot_o),
      .s_axi_arqos_i         (c_axi_arqos),
      .m_axi_arqos_o          (m_axi_arqos_o),
      .s_axi_arregion_i      (c_axi_arregion),
      .m_axi_arregion_o       (m_axi_arregion_o),
      .s_axi_arvalid_i       (c_axi_arvalid),
      .m_axi_arvalid_o        (m_axi_arvalid_o),
      .s_axi_arready_o       (c_axi_arready),
      .m_axi_arready_i        (m_axi_arready_i),
      .s_axi_rid_o           (c_axi_rid),
      .m_axi_rid_i            (m_axi_rid_i),
      .s_axi_rdata_o         (c_axi_rdata),
      .m_axi_rdata_i          (m_axi_rdata_i),
      .s_axi_rresp_o         (c_axi_rresp),
      .m_axi_rresp_i          (m_axi_rresp_i),
      .s_axi_rlast_o         (c_axi_rlast),
      .m_axi_rlast_i          (m_axi_rlast_i),
      .s_axi_rvalid_o        (c_axi_rvalid),
      .m_axi_rvalid_i         (m_axi_rvalid_i),
      .s_axi_rready_i        (c_axi_rready),
      .m_axi_rready_o         (m_axi_rready_o)
    );
  end else begin : g_mem_wire
    assign m_axi_awid_o = c_axi_awid;
    assign m_axi_awaddr_o = c_axi_awaddr;
    assign m_axi_awlen_o = c_axi_awlen;
    assign m_axi_awsize_o = c_axi_awsize;
    assign m_axi_awburst_o = c_axi_awburst;
    assign m_axi_awlock_o = c_axi_awlock;
    assign m_axi_awcache_o = c_axi_awcache;
    assign m_axi_awprot_o = c_axi_awprot;
    assign m_axi_awqos_o = c_axi_awqos;
    assign m_axi_awregion_o = c_axi_awregion;
    assign m_axi_awvalid_o = c_axi_awvalid;
    assign c_axi_awready = m_axi_awready_i;
    assign m_axi_wdata_o = c_axi_wdata;
    assign m_axi_wstrb_o = c_axi_wstrb;
    assign m_axi_wlast_o = c_axi_wlast;
    assign m_axi_wvalid_o = c_axi_wvalid;
    assign c_axi_wready = m_axi_wready_i;
    assign c_axi_bid = m_axi_bid_i;
    assign c_axi_bresp = m_axi_bresp_i;
    assign c_axi_bvalid = m_axi_bvalid_i;
    assign m_axi_bready_o = c_axi_bready;
    assign m_axi_arid_o = c_axi_arid;
    assign m_axi_araddr_o = c_axi_araddr;
    assign m_axi_arlen_o = c_axi_arlen;
    assign m_axi_arsize_o = c_axi_arsize;
    assign m_axi_arburst_o = c_axi_arburst;
    assign m_axi_arlock_o = c_axi_arlock;
    assign m_axi_arcache_o = c_axi_arcache;
    assign m_axi_arprot_o = c_axi_arprot;
    assign m_axi_arqos_o = c_axi_arqos;
    assign m_axi_arregion_o = c_axi_arregion;
    assign m_axi_arvalid_o = c_axi_arvalid;
    assign c_axi_arready = m_axi_arready_i;
    assign c_axi_rid = m_axi_rid_i;
    assign c_axi_rdata = m_axi_rdata_i;
    assign c_axi_rresp = m_axi_rresp_i;
    assign c_axi_rlast = m_axi_rlast_i;
    assign c_axi_rvalid = m_axi_rvalid_i;
    assign m_axi_rready_o = c_axi_rready;
  end

  // Port clocks / resets of a synchronous port are not used (tied to clk_i / rst_ni by the integrator).
  if (!CFG_ASYNC && !MEM_ASYNC) begin : g_unused_both
    logic unused;
    assign unused = ^{clk_cfg_i, rst_cfg_ni, clk_mem_i, rst_mem_ni};
  end else if (!CFG_ASYNC) begin : g_unused_cfg
    logic unused;
    assign unused = ^{clk_cfg_i, rst_cfg_ni};
  end else if (!MEM_ASYNC) begin : g_unused_mem
    logic unused;
    assign unused = ^{clk_mem_i, rst_mem_ni};
  end

endmodule
