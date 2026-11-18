// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// AHB-Lite subordinate (32-bit data) to AXI4-Lite master, configuration-side adapter (v1.9, F-6).
// Address phase sampled when HREADY is high and HSEL & HTRANS = NONSEQ / SEQ; IDLE and BUSY get a zero-wait OKAY.
// Data phase: the AXI4-Lite access starts in its first cycle (HWDATA is valid there and held during wait states),
// HREADYOUT stays low until the response; OKAY ends the data phase with HREADYOUT high; SLVERR / DECERR, a
// transfer wider than 32 bits or an unaligned address give the two-cycle ERROR response (HRESP high, HREADYOUT low
// then high) without (for the last two) an AXI4-Lite access. Byte strobes from HSIZE and HADDR[1:0]; reads return
// the whole word. HBURST, HPROT and HMASTLOCK are ignored (every beat is its own access).

module g2d_ahb2axil (
  input  logic         clk_i,
  input  logic         rst_ni,
  // AHB-Lite subordinate
  input  logic         hsel_i,
  input  logic [31:0]  haddr_i,
  input  logic [1:0]   htrans_i,
  input  logic         hwrite_i,
  input  logic [2:0]   hsize_i,
  input  logic [2:0]   hburst_i,
  input  logic [3:0]   hprot_i,
  input  logic         hmastlock_i,
  input  logic [31:0]  hwdata_i,
  input  logic         hready_i,
  output logic         hreadyout_o,
  output logic         hresp_o,
  output logic [31:0]  hrdata_o,
  // AXI4-Lite master
  output logic [31:0]  m_axil_awaddr_o,
  output logic [2:0]   m_axil_awprot_o,
  output logic         m_axil_awvalid_o,
  input  logic         m_axil_awready_i,
  output logic [31:0]  m_axil_wdata_o,
  output logic [3:0]   m_axil_wstrb_o,
  output logic         m_axil_wvalid_o,
  input  logic         m_axil_wready_i,
  input  logic [1:0]   m_axil_bresp_i,
  input  logic         m_axil_bvalid_i,
  output logic         m_axil_bready_o,
  output logic [31:0]  m_axil_araddr_o,
  output logic [2:0]   m_axil_arprot_o,
  output logic         m_axil_arvalid_o,
  input  logic         m_axil_arready_i,
  input  logic [31:0]  m_axil_rdata_i,
  input  logic [1:0]   m_axil_rresp_i,
  input  logic         m_axil_rvalid_i,
  output logic         m_axil_rready_o
);

  logic        req_valid, req_ready, req_write, rsp_valid, rsp_err;
  logic [31:0] req_addr, req_wdata, rsp_rdata;
  logic [3:0]  req_strb;

  logic        dp_q, wr_q, sent_q, ok_q, err1_q, err2_q;
  logic [31:2] addr_q;
  logic [3:0]  strb_q;
  logic        take, bad;
  logic [3:0]  strb;

  assign take = hready_i & hsel_i & htrans_i[1];
  always_comb begin
    unique case (hsize_i)
      3'd0:    begin strb = 4'b0001 << haddr_i[1:0];           bad = 1'b0;          end
      3'd1:    begin strb = 4'b0011 << {haddr_i[1], 1'b0};     bad = haddr_i[0];    end
      3'd2:    begin strb = 4'b1111;                           bad = |haddr_i[1:0]; end
      default: begin strb = 4'b0000;                           bad = 1'b1;          end
    endcase
  end

  assign req_valid   = dp_q & ~sent_q & ~err1_q & ~err2_q;
  assign req_write   = wr_q;
  assign req_addr    = {addr_q, 2'b00};   // lanes are in the strobes
  assign req_wdata   = hwdata_i;
  assign req_strb    = wr_q ? strb_q : 4'h0;
  assign hreadyout_o = ~dp_q | ok_q | err2_q;
  assign hresp_o     = err1_q | err2_q;
  assign hrdata_o    = rsp_rdata;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      dp_q   <= 1'b0;
      wr_q   <= 1'b0;
      sent_q <= 1'b0;
      ok_q   <= 1'b0;
      err1_q <= 1'b0;
      err2_q <= 1'b0;
      addr_q <= '0;
      strb_q <= '0;
    end else if (hready_i) begin   // end of any data phase; address phase of the next transfer
      dp_q   <= take;
      wr_q   <= hwrite_i;
      addr_q <= haddr_i[31:2];
      strb_q <= strb;
      sent_q <= 1'b0;
      ok_q   <= 1'b0;
      err1_q <= take & bad;
      err2_q <= 1'b0;
    end else begin
      if (req_valid && req_ready) sent_q <= 1'b1;
      if (rsp_valid) begin
        ok_q   <= ~rsp_err;
        err1_q <= rsp_err;
      end
      if (err1_q) begin
        err1_q <= 1'b0;
        err2_q <= 1'b1;
      end
    end
  end

  g2d_axil_single u_axil (
    .clk_i,
    .rst_ni,
    .req_valid_i (req_valid),
    .req_ready_o (req_ready),
    .req_write_i (req_write),
    .req_addr_i  (req_addr),
    .req_wdata_i (req_wdata),
    .req_strb_i  (req_strb),
    .rsp_valid_o (rsp_valid),
    .rsp_rdata_o (rsp_rdata),
    .rsp_err_o   (rsp_err),
    .m_axil_awaddr_o,
    .m_axil_awprot_o,
    .m_axil_awvalid_o,
    .m_axil_awready_i,
    .m_axil_wdata_o,
    .m_axil_wstrb_o,
    .m_axil_wvalid_o,
    .m_axil_wready_i,
    .m_axil_bresp_i,
    .m_axil_bvalid_i,
    .m_axil_bready_o,
    .m_axil_araddr_o,
    .m_axil_arprot_o,
    .m_axil_arvalid_o,
    .m_axil_arready_i,
    .m_axil_rdata_i,
    .m_axil_rresp_i,
    .m_axil_rvalid_i,
    .m_axil_rready_o
  );


  logic unused;
  assign unused = ^{hburst_i, hprot_i, hmastlock_i, htrans_i[0]};

endmodule
