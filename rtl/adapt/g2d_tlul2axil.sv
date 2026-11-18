// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// TileLink-UL manager-to-subordinate (32-bit data) to AXI4-Lite master, configuration-side adapter (v1.9, F-6).
// A: PutFullData (0), PutPartialData (1), Get (4); any other opcode is answered with d_error = 1 and no AXI4-Lite
// access. D: AccessAck (0) for puts, AccessAckData (1) for gets, d_size and d_source echoed, d_param = d_sink = 0,
// d_error = SLVERR / DECERR, held until d_ready. One request at a time (a_ready = idle).

module g2d_tlul2axil #(
  parameter int unsigned SRC_W  = 8,
  parameter int unsigned SIZE_W = 2
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  // TL-UL subordinate
  input  logic              a_valid_i,
  output logic              a_ready_o,
  input  logic [2:0]        a_opcode_i,
  input  logic [2:0]        a_param_i,
  input  logic [SIZE_W-1:0] a_size_i,
  input  logic [SRC_W-1:0]  a_source_i,
  input  logic [31:0]       a_address_i,
  input  logic [3:0]        a_mask_i,
  input  logic [31:0]       a_data_i,
  output logic              d_valid_o,
  input  logic              d_ready_i,
  output logic [2:0]        d_opcode_o,
  output logic [2:0]        d_param_o,
  output logic [SIZE_W-1:0] d_size_o,
  output logic [SRC_W-1:0]  d_source_o,
  output logic              d_sink_o,
  output logic [31:0]       d_data_o,
  output logic              d_error_o,
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
  localparam logic [2:0] PUT_FULL = 3'd0, PUT_PARTIAL = 3'd1, GET = 3'd4;
  localparam logic [2:0] ACCESS_ACK = 3'd0, ACCESS_ACK_DATA = 3'd1;

  logic              busy_q, d_q, bad_q, get_q, sent_q;
  logic [SIZE_W-1:0] size_q;
  logic [SRC_W-1:0]  src_q;
  logic              a_take, a_ok;
  logic [31:0]       addr_q, wdata_q, data_q;
  logic [3:0]        strb_q;
  logic              err_q;

  assign a_ok      = (a_opcode_i == PUT_FULL) || (a_opcode_i == PUT_PARTIAL) || (a_opcode_i == GET);
  assign a_ready_o = ~busy_q;
  assign a_take    = a_valid_i & ~busy_q;
  assign req_valid = busy_q & ~bad_q & ~sent_q;
  assign req_write = ~get_q;
  assign req_strb  = get_q ? 4'h0 : strb_q;
  assign req_addr  = addr_q;
  assign req_wdata = wdata_q;

  assign d_valid_o  = d_q;
  assign d_opcode_o = get_q ? ACCESS_ACK_DATA : ACCESS_ACK;
  assign d_param_o  = 3'd0;
  assign d_size_o   = size_q;
  assign d_source_o = src_q;
  assign d_sink_o   = 1'b0;
  assign d_data_o   = data_q;
  assign d_error_o  = err_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q <= 1'b0;
      d_q    <= 1'b0;
      bad_q  <= 1'b0;
      get_q  <= 1'b0;
      sent_q <= 1'b0;
      size_q <= '0;
      src_q  <= '0;
      addr_q <= '0;
      wdata_q <= '0;
      strb_q <= '0;
      data_q <= '0;
      err_q  <= 1'b0;
    end else begin
      if (a_take) begin
        busy_q  <= 1'b1;
        bad_q   <= ~a_ok;
        get_q   <= (a_opcode_i == GET);
        sent_q  <= 1'b0;
        size_q  <= a_size_i;
        src_q   <= a_source_i;
        addr_q  <= a_address_i;
        wdata_q <= a_data_i;
        strb_q  <= a_mask_i;
        d_q     <= ~a_ok;     // unsupported opcode: error response at once
        err_q   <= ~a_ok;
        data_q  <= '0;
      end
      if (req_valid && req_ready) sent_q <= 1'b1;
      if (rsp_valid) begin
        d_q    <= 1'b1;
        err_q  <= rsp_err;
        data_q <= get_q ? rsp_rdata : 32'h0;
      end
      if (d_q && d_ready_i) begin
        d_q    <= 1'b0;
        busy_q <= 1'b0;
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
  assign unused = ^a_param_i;

endmodule
