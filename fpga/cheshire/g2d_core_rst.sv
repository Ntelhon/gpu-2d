// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// G2D core reset for the asynchronous Genesys2 build (v1.1, arch.md §2.1 / §2.2; FPGA integration only). The core clock
// is the 100 MHz output of the clock wizard (g2d_clkwiz, fpga/genesys2/g2d_clkwiz_ip.tcl), the same MMCM as Cheshire's
// 50 MHz SoC clock. The core reset is asserted asynchronously while the SoC reset (rst_ni) is asserted or the clock
// wizard is not locked, and released synchronously to the core clock (2 flops). The SoC reset reaches the core, cfg
// and mem domains of g2d_top together, as arch.md §2.2 requires.

module g2d_core_rst (
  input  logic core_clk_i,
  input  logic locked_i,     // clock wizard locked
  input  logic rst_ni,       // SoC reset (soc_clk domain)
  output logic core_rst_no
);

  logic       arst_n;
  logic [1:0] rst_q;
  assign arst_n = rst_ni & locked_i;

  always_ff @(posedge core_clk_i or negedge arst_n) begin
    if (!arst_n) rst_q <= '0;
    else         rst_q <= {rst_q[0], 1'b1};
  end
  assign core_rst_no = rst_q[1];

endmodule
