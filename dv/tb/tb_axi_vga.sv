// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Bench wrapper for axi_vga (fpga/cheshire/axi_vga_g2d, the G2D-modified copy, or the original for comparison):
// flat AXI4 master ports for cocotbext-axi's AxiRam (prefix m_axi), a flat register bus, the VGA pins and the
// timing FSM's visible flag. Used by dv/tests/test_axi_vga.py.

`include "axi/typedef.svh"
`include "register_interface/typedef.svh"

module tb_axi_vga #(
  parameter int unsigned BufferDepth = 256,
  parameter int unsigned AW = 32,
  parameter int unsigned DW = 64,
  parameter int unsigned IW = 2
) (
  input  logic          clk,
  input  logic          rst_n,
  // register bus
  input  logic          reg_valid,
  input  logic          reg_write,
  input  logic [31:0]   reg_addr,
  input  logic [31:0]   reg_wdata,
  output logic          reg_ready,
  output logic [31:0]   reg_rdata,
  output logic          reg_error,
  // AXI4 master (read only in practice)
  output logic [IW-1:0] m_axi_awid,   output logic [AW-1:0] m_axi_awaddr, output logic [7:0] m_axi_awlen,
  output logic [2:0]    m_axi_awsize, output logic [1:0]    m_axi_awburst, output logic m_axi_awvalid,
  input  logic          m_axi_awready,
  output logic [DW-1:0] m_axi_wdata,  output logic [DW/8-1:0] m_axi_wstrb, output logic m_axi_wlast,
  output logic          m_axi_wvalid, input  logic m_axi_wready,
  input  logic [IW-1:0] m_axi_bid,    input  logic [1:0] m_axi_bresp, input logic m_axi_bvalid,
  output logic          m_axi_bready,
  output logic [IW-1:0] m_axi_arid,   output logic [AW-1:0] m_axi_araddr, output logic [7:0] m_axi_arlen,
  output logic [2:0]    m_axi_arsize, output logic [1:0]    m_axi_arburst, output logic m_axi_arvalid,
  input  logic          m_axi_arready,
  input  logic [IW-1:0] m_axi_rid,    input  logic [DW-1:0] m_axi_rdata, input logic [1:0] m_axi_rresp,
  input  logic          m_axi_rlast,  input  logic m_axi_rvalid, output logic m_axi_rready,
  // VGA
  output logic          hsync,
  output logic          vsync,
  output logic [15:0]   pixel,        // {red, green, blue}
  output logic          visible
);
  typedef logic [AW-1:0]   addr_t;
  typedef logic [DW-1:0]   data_t;
  typedef logic [DW/8-1:0] strb_t;
  typedef logic [IW-1:0]   id_t;
  typedef logic [0:0]      user_t;
  `AXI_TYPEDEF_ALL(axi, addr_t, id_t, data_t, strb_t, user_t)
  typedef logic [31:0] raddr_t;
  typedef logic [31:0] rdata_t;
  typedef logic [3:0]  rstrb_t;
  `REG_BUS_TYPEDEF_ALL(reg, raddr_t, rdata_t, rstrb_t)

  axi_req_t  req;
  axi_resp_t rsp;
  reg_req_t  rreq;
  reg_rsp_t  rrsp;
  logic [4:0] r;
  logic [5:0] g;
  logic [4:0] b;

  assign rreq = '{addr: reg_addr, write: reg_write, wdata: reg_wdata, wstrb: 4'hF, valid: reg_valid};
  assign reg_ready = rrsp.ready;
  assign reg_rdata = rrsp.rdata;
  assign reg_error = rrsp.error;

  axi_vga #(
    .RedWidth     ( 5 ), .GreenWidth ( 6 ), .BlueWidth ( 5 ),
    .HCountWidth  ( 24 ), .VCountWidth ( 24 ),
    .AXIAddrWidth ( AW ), .AXIDataWidth ( DW ), .AXIIdWidth ( IW ), .AXIUserWidth ( 1 ), .AXIStrbWidth ( DW / 8 ),
    .BufferDepth  ( BufferDepth ), .MaxReadTxns ( 24 ),
    .axi_req_t    ( axi_req_t ), .axi_resp_t ( axi_resp_t ), .axi_r_chan_t ( axi_r_chan_t ),
    .reg_req_t    ( reg_req_t ), .reg_resp_t ( reg_rsp_t )
  ) i_vga (
    .clk_i          ( clk   ),
    .rst_ni         ( rst_n ),
    .test_mode_en_i ( 1'b0  ),
    .reg_req_i      ( rreq  ),
    .reg_rsp_o      ( rrsp  ),
    .axi_req_o      ( req   ),
    .axi_resp_i     ( rsp   ),
    .hsync_o        ( hsync ),
    .vsync_o        ( vsync ),
    .red_o          ( r     ),
    .green_o        ( g     ),
    .blue_o         ( b     )
  );
  assign pixel   = {r, g, b};
  assign visible = i_vga.i_axi_vga_timing_fsm.visible;

  assign m_axi_awid = req.aw.id;  assign m_axi_awaddr = req.aw.addr; assign m_axi_awlen = req.aw.len;
  assign m_axi_awsize = req.aw.size; assign m_axi_awburst = req.aw.burst; assign m_axi_awvalid = req.aw_valid;
  assign m_axi_wdata = req.w.data; assign m_axi_wstrb = req.w.strb; assign m_axi_wlast = req.w.last;
  assign m_axi_wvalid = req.w_valid; assign m_axi_bready = req.b_ready;
  assign m_axi_arid = req.ar.id;  assign m_axi_araddr = req.ar.addr; assign m_axi_arlen = req.ar.len;
  assign m_axi_arsize = req.ar.size; assign m_axi_arburst = req.ar.burst; assign m_axi_arvalid = req.ar_valid;
  assign m_axi_rready = req.r_ready;
  always_comb begin
    rsp = '0;
    rsp.aw_ready = m_axi_awready;
    rsp.w_ready  = m_axi_wready;
    rsp.b_valid  = m_axi_bvalid;
    rsp.b.id     = m_axi_bid;
    rsp.b.resp   = m_axi_bresp;
    rsp.ar_ready = m_axi_arready;
    rsp.r_valid  = m_axi_rvalid;
    rsp.r.id     = m_axi_rid;
    rsp.r.data   = m_axi_rdata;
    rsp.r.resp   = m_axi_rresp;
    rsp.r.last   = m_axi_rlast;
  end
endmodule
