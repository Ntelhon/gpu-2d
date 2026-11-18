// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D attached to the Cheshire SoC (Phase 7, FPGA integration only; not part of the IP in rtl/).
//
// CSR side:    Cheshire external AXI4 slave (AxiDataWidth, AxiSlvIdWidth, ATOPs possible)
//              -> PULP axi_atop_filter (answers AXI5 atomics with an error; G2D is an AXI4 subordinate)
//              -> g2d_top_axi s_axi_* (v1.9: our AXI4 adapter g2d_axi2axil: bursts split into 32-bit accesses, ID
//                 reflection, any data width; low 12 address bits decoded). v1.0..v1.8 used the PULP
//                 axi_to_axi_lite + axi_lite_dw_converter pair here.
// Memory side: g2d_top m_axi_* -> Cheshire external AXI4 master (same data width, ID width
//              G2D_ID_W = Cfg.AxiMstIdWidth, no ID conversion; user bits 0 = no AMO).
// Interrupt:   g2d_top irq_o (level, clk_i) -> Cheshire intr_ext_i (synchronised there).
//
// Clocks: CoreAsync = 0 (Phase 7 bring-up): clk_cfg_i, clk_i and clk_mem_i of g2d_top all take the SoC clock
// (core_clk_i / core_rst_ni unused). CoreAsync = 1 (v1.1): g2d_top's core runs on core_clk_i (100 MHz on the
// Genesys2: clk_100 of g2d_clkwiz, reset from fpga/cheshire/g2d_core_rst.sv) with CFG_ASYNC = MEM_ASYNC = 1; both
// ports stay on the SoC clock, so the PULP converters and Cheshire see a single-clock subordinate / manager as before.
// The PULP ATOP filter is used only here (integration, outside the IP; docs/INTEGRATION.md).

`include "axi/typedef.svh"

module g2d_cheshire #(
  parameter int unsigned AxiAddrWidth = 48,
  parameter int unsigned AxiDataWidth = 64,
  parameter int unsigned AxiUserWidth = 2,
  parameter int unsigned SlvIdWidth   = 1,     // CSR port: Cheshire's AxiSlvIdWidth
  parameter int unsigned MstIdWidth   = 3,     // memory port: Cheshire's AxiMstIdWidth (= G2D ID_W)
  parameter type         slv_req_t    = logic, // Cheshire axi_ext_slv_req_t
  parameter type         slv_rsp_t    = logic,
  parameter type         mst_req_t    = logic, // Cheshire axi_ext_mst_req_t
  parameter type         mst_rsp_t    = logic,
  parameter bit          CoreAsync    = 1'b0,  // G2D core on core_clk_i, ports through the CDC (v1.1)
  parameter logic [5:0]  Features     = 6'b11_1111   // g2d_top FEATURES (v1.7: optional units built)
) (
  input  logic     clk_i,
  input  logic     rst_ni,
  input  logic     core_clk_i,
  input  logic     core_rst_ni,
  input  slv_req_t csr_req_i,
  output slv_rsp_t csr_rsp_o,
  output mst_req_t mem_req_o,
  input  mst_rsp_t mem_rsp_i,
  output logic     irq_o
);

  localparam int unsigned DB = AxiDataWidth / 8;

  typedef logic [AxiAddrWidth-1:0] addr_t;
  typedef logic [AxiDataWidth-1:0] wdata_t;
  typedef logic [DB-1:0]           wstrb_t;
  slv_req_t csr_req;   // after the ATOP filter
  slv_rsp_t csr_rsp;

  axi_atop_filter #(
    .AxiIdWidth      ( SlvIdWidth ),
    .AxiMaxWriteTxns ( 2          ),
    .axi_req_t       ( slv_req_t  ),
    .axi_resp_t      ( slv_rsp_t  )
  ) i_atop_filter (
    .clk_i,
    .rst_ni,
    .slv_req_i  ( csr_req_i ),
    .slv_resp_o ( csr_rsp_o ),
    .mst_req_o  ( csr_req   ),
    .mst_resp_i ( csr_rsp   )
  );

  // response fields the adapter does not drive
  assign csr_rsp.b.user = '0;
  assign csr_rsp.r.user = '0;

  // Memory port: G2D drives everything but atop / user (AXI4, no atomics, user 0).
  logic [MstIdWidth-1:0]   awid, arid;
  logic [AxiAddrWidth-1:0] awaddr, araddr;
  logic [7:0]              awlen, arlen;
  logic [2:0]              awsize, arsize, awprot, arprot;
  logic [1:0]              awburst, arburst;
  logic                    awlock, arlock, awvalid, arvalid, wlast, wvalid, bready, rready;
  logic [3:0]              awcache, arcache, awqos, arqos, awregion, arregion;
  wdata_t                  wdata;
  wstrb_t                  wstrb;

  always_comb begin
    mem_req_o           = '0;
    mem_req_o.aw.id     = awid;
    mem_req_o.aw.addr   = awaddr;
    mem_req_o.aw.len    = awlen;
    mem_req_o.aw.size   = awsize;
    mem_req_o.aw.burst  = awburst;
    mem_req_o.aw.lock   = awlock;
    mem_req_o.aw.cache  = awcache;
    mem_req_o.aw.prot   = awprot;
    mem_req_o.aw.qos    = awqos;
    mem_req_o.aw.region = awregion;
    mem_req_o.aw_valid  = awvalid;
    mem_req_o.w.data    = wdata;
    mem_req_o.w.strb    = wstrb;
    mem_req_o.w.last    = wlast;
    mem_req_o.w_valid   = wvalid;
    mem_req_o.b_ready   = bready;
    mem_req_o.ar.id     = arid;
    mem_req_o.ar.addr   = araddr;
    mem_req_o.ar.len    = arlen;
    mem_req_o.ar.size   = arsize;
    mem_req_o.ar.burst  = arburst;
    mem_req_o.ar.lock   = arlock;
    mem_req_o.ar.cache  = arcache;
    mem_req_o.ar.prot   = arprot;
    mem_req_o.ar.qos    = arqos;
    mem_req_o.ar.region = arregion;
    mem_req_o.ar_valid  = arvalid;
    mem_req_o.r_ready   = rready;
  end

  logic g2d_clk, g2d_rst_n;
  assign g2d_clk   = CoreAsync ? core_clk_i  : clk_i;
  assign g2d_rst_n = CoreAsync ? core_rst_ni : rst_ni;

  g2d_top_axi #(
    .DATA_W     ( AxiDataWidth ),
    .ADDR_W     ( AxiAddrWidth ),
    .ID_W       ( MstIdWidth   ),
    .CFG_ASYNC  ( CoreAsync    ),
    .MEM_ASYNC  ( CoreAsync    ),
    .FEATURES   ( Features     ),
    .CFG_DATA_W ( AxiDataWidth ),
    .CFG_ADDR_W ( AxiAddrWidth ),
    .CFG_ID_W   ( SlvIdWidth   )
  ) i_g2d (
    .clk_cfg_i        ( clk_i     ),
    .rst_cfg_ni       ( rst_ni    ),
    .clk_i            ( g2d_clk   ),
    .rst_ni           ( g2d_rst_n ),
    .clk_mem_i        ( clk_i     ),
    .rst_mem_ni       ( rst_ni    ),
    .test_mode_i      ( 1'b0      ),
    .s_axi_awid_i     ( csr_req.aw.id         ),
    .s_axi_awaddr_i   ( csr_req.aw.addr       ),
    .s_axi_awlen_i    ( csr_req.aw.len        ),
    .s_axi_awsize_i   ( csr_req.aw.size       ),
    .s_axi_awburst_i  ( csr_req.aw.burst      ),
    .s_axi_awlock_i   ( csr_req.aw.lock       ),
    .s_axi_awcache_i  ( csr_req.aw.cache      ),
    .s_axi_awprot_i   ( csr_req.aw.prot       ),
    .s_axi_awqos_i    ( csr_req.aw.qos        ),
    .s_axi_awregion_i ( csr_req.aw.region     ),
    .s_axi_awvalid_i  ( csr_req.aw_valid      ),
    .s_axi_awready_o  ( csr_rsp.aw_ready      ),
    .s_axi_wdata_i    ( csr_req.w.data        ),
    .s_axi_wstrb_i    ( csr_req.w.strb        ),
    .s_axi_wlast_i    ( csr_req.w.last        ),
    .s_axi_wvalid_i   ( csr_req.w_valid       ),
    .s_axi_wready_o   ( csr_rsp.w_ready       ),
    .s_axi_bid_o      ( csr_rsp.b.id          ),
    .s_axi_bresp_o    ( csr_rsp.b.resp        ),
    .s_axi_bvalid_o   ( csr_rsp.b_valid       ),
    .s_axi_bready_i   ( csr_req.b_ready       ),
    .s_axi_arid_i     ( csr_req.ar.id         ),
    .s_axi_araddr_i   ( csr_req.ar.addr       ),
    .s_axi_arlen_i    ( csr_req.ar.len        ),
    .s_axi_arsize_i   ( csr_req.ar.size       ),
    .s_axi_arburst_i  ( csr_req.ar.burst      ),
    .s_axi_arlock_i   ( csr_req.ar.lock       ),
    .s_axi_arcache_i  ( csr_req.ar.cache      ),
    .s_axi_arprot_i   ( csr_req.ar.prot       ),
    .s_axi_arqos_i    ( csr_req.ar.qos        ),
    .s_axi_arregion_i ( csr_req.ar.region     ),
    .s_axi_arvalid_i  ( csr_req.ar_valid      ),
    .s_axi_arready_o  ( csr_rsp.ar_ready      ),
    .s_axi_rid_o      ( csr_rsp.r.id          ),
    .s_axi_rdata_o    ( csr_rsp.r.data        ),
    .s_axi_rresp_o    ( csr_rsp.r.resp        ),
    .s_axi_rlast_o    ( csr_rsp.r.last        ),
    .s_axi_rvalid_o   ( csr_rsp.r_valid       ),
    .s_axi_rready_i   ( csr_req.r_ready       ),
    .m_axi_awid_o     ( awid                  ),
    .m_axi_awaddr_o   ( awaddr                ),
    .m_axi_awlen_o    ( awlen                 ),
    .m_axi_awsize_o   ( awsize                ),
    .m_axi_awburst_o  ( awburst               ),
    .m_axi_awlock_o   ( awlock                ),
    .m_axi_awcache_o  ( awcache               ),
    .m_axi_awprot_o   ( awprot                ),
    .m_axi_awqos_o    ( awqos                 ),
    .m_axi_awregion_o ( awregion              ),
    .m_axi_awvalid_o  ( awvalid               ),
    .m_axi_awready_i  ( mem_rsp_i.aw_ready    ),
    .m_axi_wdata_o    ( wdata                 ),
    .m_axi_wstrb_o    ( wstrb                 ),
    .m_axi_wlast_o    ( wlast                 ),
    .m_axi_wvalid_o   ( wvalid                ),
    .m_axi_wready_i   ( mem_rsp_i.w_ready     ),
    .m_axi_bid_i      ( mem_rsp_i.b.id        ),
    .m_axi_bresp_i    ( mem_rsp_i.b.resp      ),
    .m_axi_bvalid_i   ( mem_rsp_i.b_valid     ),
    .m_axi_bready_o   ( bready                ),
    .m_axi_arid_o     ( arid                  ),
    .m_axi_araddr_o   ( araddr                ),
    .m_axi_arlen_o    ( arlen                 ),
    .m_axi_arsize_o   ( arsize                ),
    .m_axi_arburst_o  ( arburst               ),
    .m_axi_arlock_o   ( arlock                ),
    .m_axi_arcache_o  ( arcache               ),
    .m_axi_arprot_o   ( arprot                ),
    .m_axi_arqos_o    ( arqos                 ),
    .m_axi_arregion_o ( arregion              ),
    .m_axi_arvalid_o  ( arvalid               ),
    .m_axi_arready_i  ( mem_rsp_i.ar_ready    ),
    .m_axi_rid_i      ( mem_rsp_i.r.id        ),
    .m_axi_rdata_i    ( mem_rsp_i.r.data      ),
    .m_axi_rresp_i    ( mem_rsp_i.r.resp      ),
    .m_axi_rlast_i    ( mem_rsp_i.r.last      ),
    .m_axi_rvalid_i   ( mem_rsp_i.r_valid     ),
    .m_axi_rready_o   ( rready                ),
    .irq_o
  );

endmodule
