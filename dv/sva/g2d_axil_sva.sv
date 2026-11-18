// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Protocol checker for the AXI4-Lite slave (arch.md §4.1). Simulation only: bound into every g2d_axil2reg
// by dv/sva/g2d_binds.sv; port names equal the g2d_axil2reg port names (bind with .*).
//   BVALID / RVALID held and BRESP / RDATA / RRESP stable until accepted; never more responses than
//   requests.

module g2d_axil_sva (
  input  logic                clk_i,
  input  logic                rst_ni,
  input  logic                s_axil_awvalid_i,
  input  logic                s_axil_awready_o,
  input  logic                s_axil_wvalid_i,
  input  logic                s_axil_wready_o,
  input  logic [1:0]          s_axil_bresp_o,
  input  logic                s_axil_bvalid_o,
  input  logic                s_axil_bready_i,
  input  logic                s_axil_arvalid_i,
  input  logic                s_axil_arready_o,
  input  logic [31:0]         s_axil_rdata_o,
  input  logic [1:0]          s_axil_rresp_o,
  input  logic                s_axil_rvalid_o,
  input  logic                s_axil_rready_i
);

  // Armed by the first reset: before it the design holds random initial values (--x-initial unique).
  bit armed = 1'b0;
  always @(negedge rst_ni) armed <= 1'b1;

  // ---------------------------------------------------------------- AXI4-Lite slave
  p_b_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (s_axil_bvalid_o && !s_axil_bready_i) |=> (s_axil_bvalid_o && $stable(s_axil_bresp_o)))
    else $error("p_b_hold: AXI4-Lite B changed or dropped before acceptance");
  p_r_hold: assert property (@(posedge clk_i) disable iff (!rst_ni || !armed)
      (s_axil_rvalid_o && !s_axil_rready_i) |=> (s_axil_rvalid_o && $stable({s_axil_rdata_o, s_axil_rresp_o})))
    else $error("p_r_hold: AXI4-Lite R changed or dropped before acceptance");

  int l_aw, l_w, l_b, l_ar, l_r;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      l_aw <= 0; l_w <= 0; l_b <= 0; l_ar <= 0; l_r <= 0;
    end else if (armed) begin
      l_aw <= l_aw + ((s_axil_awvalid_i && s_axil_awready_o) ? 1 : 0);
      l_w  <= l_w  + ((s_axil_wvalid_i && s_axil_wready_o) ? 1 : 0);
      l_ar <= l_ar + ((s_axil_arvalid_i && s_axil_arready_o) ? 1 : 0);
      if (s_axil_bvalid_o && s_axil_bready_i) begin
        l_b <= l_b + 1;
        if (l_b + 1 > l_aw || l_b + 1 > l_w) $error("p_l_b_count: AXI4-Lite B before its AW and W");
      end
      if (s_axil_rvalid_o && s_axil_rready_i) begin
        l_r <= l_r + 1;
        if (l_r + 1 > l_ar) $error("p_l_r_count: AXI4-Lite R before its AR");
      end
    end
  end

endmodule
