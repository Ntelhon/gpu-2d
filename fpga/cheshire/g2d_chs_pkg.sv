// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// Cheshire configuration for the G2D FPGA build (Phase 7). Starts from cheshire_pkg::DefaultCfg and the
// Genesys2 changes of Cheshire's target/xilinx/src/cheshire_top_xilinx.sv (gen_cheshire_xilinx_cfg,
// Cheshire 6c6dd5e), then adds the G2D ports. Facts and their source lines: docs/FPGA.md
// and docs/OPEN_QUESTIONS.md (Q-FPGA-1..3).

package g2d_chs_pkg;

  import cheshire_pkg::*;

  // G2D CSR window: inside Cheshire's non-cached, non-idempotent region [0x4000_0000, 0x8000_0000).
  localparam longint unsigned G2dCsrBase = 64'h4800_0000;
  localparam longint unsigned G2dCsrSize = 64'h0000_1000;

  // PLIC source of the G2D interrupt: Cheshire's internal sources are 0..57 (NumIntIntrs = 58); the
  // external inputs follow. The PLIC is generated with 59 sources (fpga/cheshire/cheshire-g2d.patch).
  localparam int unsigned G2dPlicSrc = NumIntIntrs;

  // num_tb_mst: extra external AXI managers for a testbench (0 on the FPGA). Port 0 is G2D.
  // trim (v1.2, K-2 option): USB, iDMA, the bus-error units and I2C off to free LUTs; VGA (FPGA demos, user
  // 2026-10-18), UART, JTAG, SPI host (SD card), GPIO and the LLC stay. The PLIC numbering does not change (Cheshire's
  // internal interrupt vector cheshire_int_intr_t is fixed; disabled units tie their lines to 0): G2D stays source 58.
  // ps2 (DOOM side project): chs_ps2 (fpga/periph) as the second external register slave at Ps2Base.
  localparam longint unsigned Ps2Base = 64'h4300_1000;

  // axi_vga R buffer in beats (v1.8). Must fit Cheshire's 10-bit cheshire_cfg_t.VgaBufferDepth: at most 1023 (a
  // wrapped value arrives in the axi_vga fork as a small depth, which stops the build there).
  localparam int unsigned VgaBufferDepth = 1023;

  // End of the LLC's cached region (v1.8, Q-FPGA-11, cheshire-g2d.patch parameter LlcCachedRegionEnd). The DOOM build
  // keeps the program region [0x8000_0000, 0x8100_0000) cached and sends everything above it (G2D pool with the
  // frame buffers at 0x8100_0000, WAD at 0xA000_0000) through the LLC bypass: scan-out and G2D frame-buffer traffic no
  // longer evict and refill LLC lines (audit §2.1). Other builds keep all of DRAM cached.
  localparam longint unsigned LlcCachedEndDoom = 64'h8100_0000;
  function automatic longint unsigned llc_cached_end(cheshire_cfg_t cfg, bit doom);
    return doom ? LlcCachedEndDoom : cfg.LlcOutRegionEnd;
  endfunction

  // trim: one bit per unit left out (2026-11-02, Q-FPGA-9: the full trim hangs the DDR write path on the board):
  // bit 0 USB, bit 1 iDMA, bit 2 bus-error units, bit 3 I2C; 4'hF = the v1.2 trim.
  function automatic cheshire_cfg_t gen_cfg(int unsigned num_tb_mst, logic [3:0] trim = 4'h0, bit ps2 = 1'b0);
    cheshire_cfg_t ret = DefaultCfg;
    // Genesys2 settings of cheshire_top_xilinx (USE_USB and USE_CFG_REGS defined for genesys2)
    ret.RtcFreq           = 1000000;
    ret.SerialLink        = 0;
    ret.Usb               = 1;
    ret.RegExtNumSlv      = 1;
    ret.RegExtNumRules    = 1;
    ret.RegExtRegionIdx   [0] = 0;
    ret.RegExtRegionStart [0] = 32'h4300_0000;
    ret.RegExtRegionEnd   [0] = 32'h4300_1000;
    // G2D: ID_W 3 needs 3-bit manager IDs (Cheshire maps CVA6 without serialization at width 3)
    ret.AxiMstIdWidth     = 3;
    ret.AxiExtNumMst      = 1 + num_tb_mst;
    ret.AxiExtNumSlv      = 1;
    ret.AxiExtNumRules    = 1;
    ret.AxiExtRegionIdx   [0] = 0;
    ret.AxiExtRegionStart [0] = G2dCsrBase;
    ret.AxiExtRegionEnd   [0] = G2dCsrBase + G2dCsrSize;
    ret.NumExtInIntrs     = 1;
    // axi_vga shows a pixel every visible clock whether its FIFO has one or not, and never re-aligns: with the default
    // 16 R beats (128 B, ~2.5 us at 25 MHz RGB565) the DOOM board run underran under CPU + G2D load and the picture
    // slid (2026-11-04). 256 beats = 2 KB, ~40 us of scan-out. v1.8: the fork's buffer is block RAM (Q-FPGA-11):
    // 1023 beats = ~8 KB, ~160 us of 640x480 RGB565 scan-out, bursts of 64 beats. The field is Cheshire's dw_bt
    // (bit [9:0]): 1024 wraps to 0 without a warning (found by the SoC sim build, 2026-11-11), so 1023 is the limit
    // (g2d_fifo_ram takes any depth).
    ret.VgaBufferDepth    = VgaBufferDepth;
    if (ps2) begin
      ret.RegExtNumSlv          = 2;
      ret.RegExtNumRules        = 2;
      ret.RegExtRegionIdx   [1] = 1;
      ret.RegExtRegionStart [1] = Ps2Base;
      ret.RegExtRegionEnd   [1] = Ps2Base + 64'h1000;
    end
    if (trim[0]) ret.Usb    = 0;
    if (trim[1]) ret.Dma    = 0;
    if (trim[2]) ret.BusErr = 0;
    if (trim[3]) ret.I2c    = 0;
    return ret;
  endfunction

endpackage
