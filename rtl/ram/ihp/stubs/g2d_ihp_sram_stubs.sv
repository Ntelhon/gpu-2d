// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Port-only stand-ins for the IHP SG13G2 SRAM macros used by rtl/ram/ihp (lint without the PDK; `make lint`).
// Not for simulation or synthesis: the ASIC flow uses the PDK's LEF / Liberty views, simulations the PDK's Verilog
// models (rtl/ram/ihp/README.md). Outputs read as zero.
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off DECLFILENAME */
module RM_IHPSG13_2P_64x22_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [5:0] A_ADDR,
  input logic [21:0] A_DIN,
  input logic A_DLY,
  output logic [21:0] A_DOUT,
  input logic [21:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [5:0] A_BIST_ADDR,
  input logic [21:0] A_BIST_DIN,
  input logic [21:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [5:0] B_ADDR,
  input logic [21:0] B_DIN,
  input logic B_DLY,
  output logic [21:0] B_DOUT,
  input logic [21:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [5:0] B_BIST_ADDR,
  input logic [21:0] B_BIST_DIN,
  input logic [21:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_64x32_c2 (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [5:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [5:0] B_ADDR,
  input logic [31:0] B_DIN,
  input logic B_DLY,
  output logic [31:0] B_DOUT
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_256x8_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [7:0] B_ADDR,
  input logic [7:0] B_DIN,
  input logic B_DLY,
  output logic [7:0] B_DOUT,
  input logic [7:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [7:0] B_BIST_ADDR,
  input logic [7:0] B_BIST_DIN,
  input logic [7:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_256x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [7:0] B_ADDR,
  input logic [15:0] B_DIN,
  input logic B_DLY,
  output logic [15:0] B_DOUT,
  input logic [15:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [7:0] B_BIST_ADDR,
  input logic [15:0] B_BIST_DIN,
  input logic [15:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_256x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [7:0] B_ADDR,
  input logic [31:0] B_DIN,
  input logic B_DLY,
  output logic [31:0] B_DOUT,
  input logic [31:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [7:0] B_BIST_ADDR,
  input logic [31:0] B_BIST_DIN,
  input logic [31:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_512x8_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [8:0] B_ADDR,
  input logic [7:0] B_DIN,
  input logic B_DLY,
  output logic [7:0] B_DOUT,
  input logic [7:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [8:0] B_BIST_ADDR,
  input logic [7:0] B_BIST_DIN,
  input logic [7:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_512x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [8:0] B_ADDR,
  input logic [15:0] B_DIN,
  input logic B_DLY,
  output logic [15:0] B_DOUT,
  input logic [15:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [8:0] B_BIST_ADDR,
  input logic [15:0] B_BIST_DIN,
  input logic [15:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_512x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [8:0] B_ADDR,
  input logic [31:0] B_DIN,
  input logic B_DLY,
  output logic [31:0] B_DOUT,
  input logic [31:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [8:0] B_BIST_ADDR,
  input logic [31:0] B_BIST_DIN,
  input logic [31:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_1024x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [9:0] B_ADDR,
  input logic [15:0] B_DIN,
  input logic B_DLY,
  output logic [15:0] B_DOUT,
  input logic [15:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [9:0] B_BIST_ADDR,
  input logic [15:0] B_BIST_DIN,
  input logic [15:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_2P_1024x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM,
  input logic B_CLK,
  input logic B_MEN,
  input logic B_WEN,
  input logic B_REN,
  input logic [9:0] B_ADDR,
  input logic [31:0] B_DIN,
  input logic B_DLY,
  output logic [31:0] B_DOUT,
  input logic [31:0] B_BM,
  input logic B_BIST_CLK,
  input logic B_BIST_EN,
  input logic B_BIST_MEN,
  input logic B_BIST_WEN,
  input logic B_BIST_REN,
  input logic [9:0] B_BIST_ADDR,
  input logic [31:0] B_BIST_DIN,
  input logic [31:0] B_BIST_BM
);
  assign A_DOUT = '0;
  assign B_DOUT = '0;
endmodule

module RM_IHPSG13_1P_64x64_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [5:0] A_ADDR,
  input logic [63:0] A_DIN,
  input logic A_DLY,
  output logic [63:0] A_DOUT,
  input logic [63:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [5:0] A_BIST_ADDR,
  input logic [63:0] A_BIST_DIN,
  input logic [63:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_256x8_c3_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_256x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_256x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_256x48_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [47:0] A_DIN,
  input logic A_DLY,
  output logic [47:0] A_DOUT,
  input logic [47:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [47:0] A_BIST_DIN,
  input logic [47:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_256x64_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [7:0] A_ADDR,
  input logic [63:0] A_DIN,
  input logic A_DLY,
  output logic [63:0] A_DOUT,
  input logic [63:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [7:0] A_BIST_ADDR,
  input logic [63:0] A_BIST_DIN,
  input logic [63:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_512x8_c3_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_512x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_512x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_512x64_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [8:0] A_ADDR,
  input logic [63:0] A_DIN,
  input logic A_DLY,
  output logic [63:0] A_DOUT,
  input logic [63:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [8:0] A_BIST_ADDR,
  input logic [63:0] A_BIST_DIN,
  input logic [63:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_1024x8_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_1024x16_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_1024x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_1024x64_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [9:0] A_ADDR,
  input logic [63:0] A_DIN,
  input logic A_DLY,
  output logic [63:0] A_DOUT,
  input logic [63:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [9:0] A_BIST_ADDR,
  input logic [63:0] A_BIST_DIN,
  input logic [63:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_2048x32_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [10:0] A_ADDR,
  input logic [31:0] A_DIN,
  input logic A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [10:0] A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN,
  input logic [31:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_2048x64_c2_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [10:0] A_ADDR,
  input logic [63:0] A_DIN,
  input logic A_DLY,
  output logic [63:0] A_DOUT,
  input logic [63:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [10:0] A_BIST_ADDR,
  input logic [63:0] A_BIST_DIN,
  input logic [63:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_4096x8_c3_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [11:0] A_ADDR,
  input logic [7:0] A_DIN,
  input logic A_DLY,
  output logic [7:0] A_DOUT,
  input logic [7:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [11:0] A_BIST_ADDR,
  input logic [7:0] A_BIST_DIN,
  input logic [7:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

module RM_IHPSG13_1P_4096x16_c3_bm_bist (
  input logic A_CLK,
  input logic A_MEN,
  input logic A_WEN,
  input logic A_REN,
  input logic [11:0] A_ADDR,
  input logic [15:0] A_DIN,
  input logic A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic A_BIST_CLK,
  input logic A_BIST_EN,
  input logic A_BIST_MEN,
  input logic A_BIST_WEN,
  input logic A_BIST_REN,
  input logic [11:0] A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN,
  input logic [15:0] A_BIST_BM
);
  assign A_DOUT = '0;
endmodule

/* verilator lint_on DECLFILENAME */
/* verilator lint_on UNUSEDSIGNAL */
