// SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
// SoC-level testbench (Phase 7): Cheshire (unmodified RTL, config g2d_chs_pkg::gen_cfg(1)) with G2D on
// its external ports, the same g2d_cheshire bridge as the FPGA build, and behavioural models around it:
//   - DRAM: PULP axi_sim_mem on the LLC's memory port (zero latency), preloaded from a memh file, behind
//     the same programmable delay stage as the Genesys2 build (v1.2: Cheshire's USE_RAM_DELAY,
//     axi_fifo_delay_dyn driven by Cheshire's chs_xilinx cfg registers at 0x4300_0000 on the reg port);
//   - boot: Cheshire's passive boot (mode 0); a testbench AXI manager (external manager port 1) writes
//     the ELF entry into SCRATCH[0..1] and sets SCRATCH[2] bit 1 (boot ROM protocol);
//   - end: the program's crt0 writes (ret << 1) | 1 to SCRATCH[2]; the testbench polls it;
//   - UART: decoded from uart_tx_o at +UART_BAUD (the test program is built for the same rate);
//   - VGA (v1.2): with +VGA_CAPTURE=<file>, the first complete frame on the VGA pins after the first vsync pulse
//     is written as little-endian RGB565 words ({red, green, blue} pins), and the sync pulses of every line of
//     that frame are checked against the programmed timing (+VGA_TIMING, default 640x480 at 60 Hz, pixel clock
//     SoC / 2; the bare-metal test programs the same values).
// Plusargs: +MEMH=<file> (lines "<addr> <64-bit word>", hex), +ENTRY=<hex>, +UART_BAUD=<n>,
//           +TIMEOUT_US=<n> (simulated), +UART_LOG=<file>,
//           +VGA_CAPTURE=<file>, +VGA_TIMING=<hvis>,<hfp>,<hsync>,<hbp>,<vvis>,<vfp>,<vsync>,<vbp>,<div>,
//           +DRAM_REFI=<n> +DRAM_RFC=<m> +DRAM_JITTER=<p> +DRAM_SEED=<s> (v1.8): refresh windows and latency jitter
//           in front of the DRAM model (fpga/sim/tb_dram_stall.sv; off by default)
//           +RD_DELAY=<n> / +WR_DELAY=<n>: written into the cfg registers' AR / AW delay before boot (software
//           may change them later, as on the board),
//           +BIN=<file>@<hex addr>: a binary image preloaded into DRAM as well (DOOM: the WAD image),
//           +VGA_TRIGGER=<hex addr>: the VGA capture starts once the program wrote a non-zero byte there,
//           +PS2_KEYS=<file> (Ps2 = 1): lines "<time_us> <hex byte>"; a PS/2 device model sends each byte at that
//           simulated time (or as soon as the previous one is done), +PS2_BIT_NS=<n> PS/2 bit period (default 20000).
// Ps2 = 1 (DOOM side project): chs_ps2 (fpga/periph) as the second external register slave, as in the DOOM bitstream.
// Exit: $finish after printing "TB_RESULT PASS" (return 0) or "TB_RESULT FAIL code=<n>" / "TIMEOUT".

`timescale 1ns / 1ps

`include "cheshire/typedef.svh"

module tb_g2d_chs #(
  parameter bit CoreAsync = 1'b0,  // v1.1: G2D core on its own clock (100 MHz, free phase), ports through the CDC
  parameter bit Trim      = 1'b0,  // v1.2: trimmed Cheshire (g2d_chs_pkg::gen_cfg trim)
  parameter bit Ps2       = 1'b0   // DOOM: chs_ps2 on the second external register slave
);

  import cheshire_pkg::*;

  localparam cheshire_cfg_t Cfg = g2d_chs_pkg::gen_cfg(1, Trim ? 4'hF : 4'h0, Ps2);
  `CHESHIRE_TYPEDEF_ALL(, Cfg)

  localparam time ClkPeriod = 20ns;    // 50 MHz SoC clock (Genesys2)
  localparam time RtcPeriod = 1000ns;  // 1 MHz RTC (Cfg.RtcFreq)

  logic clk, rtc, rst_n;

  initial begin clk = 1'b0; forever #(ClkPeriod / 2) clk = ~clk; end
  initial begin rtc = 1'b0; forever #(RtcPeriod / 2) rtc = ~rtc; end
  initial begin rst_n = 1'b0; #(ClkPeriod * 20); @(posedge clk); #1ns; rst_n = 1'b1; end

  // G2D core clock of the async build: 100 MHz as on the Genesys2, but started at an arbitrary phase (the FPGA's
  // MMCM output is related to the SoC clock; the testbench does not rely on that). Reset asserted with the SoC
  // reset, released synchronously to the core clock (fpga/cheshire/g2d_core_rst.sv).
  localparam time CoreClkPeriod = 10ns;
  logic core_clk, core_rst_n;
  logic [1:0] core_rst_q;
  initial begin core_clk = 1'b0; #3.7ns; forever #(CoreClkPeriod / 2) core_clk = ~core_clk; end
  always_ff @(posedge core_clk or negedge rst_n) begin
    if (!rst_n) core_rst_q <= '0;
    else        core_rst_q <= {core_rst_q[0], 1'b1};
  end
  assign core_rst_n = core_rst_q[1];

  axi_llc_req_t                  llc_req,  dram_req;
  axi_llc_rsp_t                  llc_rsp,  dram_rsp;
  axi_llc_req_t                  dly_req;   // v1.8: between the delay stage and the refresh / jitter stage
  axi_llc_rsp_t                  dly_rsp;
  reg_req_t                      cfg_reg_req;
  reg_rsp_t                      cfg_reg_rsp;
  reg_req_t [Cfg.RegExtNumSlv-1:0] ext_reg_req;
  reg_rsp_t [Cfg.RegExtNumSlv-1:0] ext_reg_rsp;
  axi_mst_req_t [1:0]            ext_mst_req;
  axi_mst_rsp_t [1:0]            ext_mst_rsp;
  axi_slv_req_t                  ext_slv_req;
  axi_slv_rsp_t                  ext_slv_rsp;
  logic                          g2d_irq;
  logic                          uart_tx;
  logic                          vga_hs, vga_vs;
  logic [4:0]                    vga_r, vga_b;
  logic [5:0]                    vga_g;

  cheshire_soc #(
    .Cfg               ( Cfg           ),
    .LlcCachedRegionEnd ( g2d_chs_pkg::llc_cached_end(Cfg, Ps2) ),   // v1.8: DOOM build: G2D pool uncached in the LLC
    .ExtHartinfo       ( '0            ),
    .axi_ext_llc_req_t ( axi_llc_req_t ),
    .axi_ext_llc_rsp_t ( axi_llc_rsp_t ),
    .axi_ext_mst_req_t ( axi_mst_req_t ),
    .axi_ext_mst_rsp_t ( axi_mst_rsp_t ),
    .axi_ext_slv_req_t ( axi_slv_req_t ),
    .axi_ext_slv_rsp_t ( axi_slv_rsp_t ),
    .reg_ext_req_t     ( reg_req_t     ),
    .reg_ext_rsp_t     ( reg_rsp_t     )
  ) i_dut (
    .clk_i             ( clk     ),
    .rst_ni            ( rst_n   ),
    .test_mode_i       ( 1'b0    ),
    .boot_mode_i       ( 2'b00   ),
    .rtc_i             ( rtc     ),
    .axi_llc_mst_req_o ( llc_req ),
    .axi_llc_mst_rsp_i ( llc_rsp ),
    .axi_ext_mst_req_i ( ext_mst_req ),
    .axi_ext_mst_rsp_o ( ext_mst_rsp ),
    .axi_ext_slv_req_o ( ext_slv_req ),
    .axi_ext_slv_rsp_i ( ext_slv_rsp ),
    .reg_ext_slv_req_o ( ext_reg_req ),
    .reg_ext_slv_rsp_i ( ext_reg_rsp ),
    .intr_ext_i        ( g2d_irq ),
    .intr_ext_o        ( ),
    .xeip_ext_o        ( ),
    .mtip_ext_o        ( ),
    .msip_ext_o        ( ),
    .dbg_active_o      ( ),
    .dbg_ext_req_o     ( ),
    .dbg_ext_unavail_i ( '0 ),
    .jtag_tck_i        ( 1'b0 ),
    .jtag_trst_ni      ( 1'b0 ),
    .jtag_tms_i        ( 1'b0 ),
    .jtag_tdi_i        ( 1'b0 ),
    .jtag_tdo_o        ( ),
    .jtag_tdo_oe_o     ( ),
    .uart_tx_o         ( uart_tx ),
    .uart_rx_i         ( 1'b1 ),
    .uart_rts_no       ( ),
    .uart_dtr_no       ( ),
    .uart_cts_ni       ( 1'b0 ),
    .uart_dsr_ni       ( 1'b0 ),
    .uart_dcd_ni       ( 1'b0 ),
    .uart_rin_ni       ( 1'b1 ),
    .i2c_sda_o         ( ),
    .i2c_sda_i         ( 1'b1 ),
    .i2c_sda_en_o      ( ),
    .i2c_scl_o         ( ),
    .i2c_scl_i         ( 1'b1 ),
    .i2c_scl_en_o      ( ),
    .spih_sck_o        ( ),
    .spih_sck_en_o     ( ),
    .spih_csb_o        ( ),
    .spih_csb_en_o     ( ),
    .spih_sd_o         ( ),
    .spih_sd_en_o      ( ),
    .spih_sd_i         ( '0 ),
    .gpio_i            ( '0 ),
    .gpio_o            ( ),
    .gpio_en_o         ( ),
    .slink_rcv_clk_i   ( '0 ),
    .slink_rcv_clk_o   ( ),
    .slink_i           ( '0 ),
    .slink_o           ( ),
    .vga_hsync_o       ( vga_hs ),
    .vga_vsync_o       ( vga_vs ),
    .vga_red_o         ( vga_r  ),
    .vga_green_o       ( vga_g  ),
    .vga_blue_o        ( vga_b  ),
    .usb_clk_i         ( clk   ),
    .usb_rst_ni        ( rst_n ),
    .usb_dm_i          ( '0 ),
    .usb_dm_o          ( ),
    .usb_dm_oe_o       ( ),
    .usb_dp_i          ( '0 ),
    .usb_dp_o          ( ),
    .usb_dp_oe_o       ( )
  );

  // ---------------------------------------------------------------- G2D (same bridge as the FPGA)
  g2d_cheshire #(
    .AxiAddrWidth ( Cfg.AddrWidth            ),
    .AxiDataWidth ( Cfg.AxiDataWidth         ),
    .AxiUserWidth ( Cfg.AxiUserWidth         ),
    .SlvIdWidth   ( $bits(ext_slv_req.aw.id) ),
    .MstIdWidth   ( Cfg.AxiMstIdWidth        ),
    .slv_req_t    ( axi_slv_req_t            ),
    .slv_rsp_t    ( axi_slv_rsp_t            ),
    .mst_req_t    ( axi_mst_req_t            ),
    .mst_rsp_t    ( axi_mst_rsp_t            ),
    .CoreAsync    ( CoreAsync                )
  ) i_g2d (
    .clk_i       ( clk            ),
    .rst_ni      ( rst_n          ),
    .core_clk_i  ( core_clk       ),
    .core_rst_ni ( core_rst_n     ),
    .csr_req_i ( ext_slv_req    ),
    .csr_rsp_o ( ext_slv_rsp    ),
    .mem_req_o ( ext_mst_req[0] ),
    .mem_rsp_i ( ext_mst_rsp[0] ),
    .irq_o     ( g2d_irq        )
  );

  // ---------------------------------------------------------------- cfg registers + DRAM delay
  // As cheshire_top_xilinx (USE_CFG_REGS, USE_RAM_DELAY): Cheshire's own register block and delay stage.
  chs_xilinx_reg_pkg::chs_xilinx_reg2hw_t cfg_reg2hw;

  chs_xilinx_reg_top #(
    .reg_req_t ( reg_req_t ),
    .reg_rsp_t ( reg_rsp_t )
  ) i_cfg_regs (
    .clk_i     ( clk         ),
    .rst_ni    ( rst_n       ),
    .reg_req_i ( cfg_reg_req ),
    .reg_rsp_o ( cfg_reg_rsp ),
    .reg2hw    ( cfg_reg2hw  ),
    .hw2reg    ( '0          ),
    .devmode_i ( 1'b1        )
  );

  axi_fifo_delay_dyn #(
    .aw_chan_t  ( axi_llc_aw_chan_t ),
    .w_chan_t   ( axi_llc_w_chan_t  ),
    .b_chan_t   ( axi_llc_b_chan_t  ),
    .ar_chan_t  ( axi_llc_ar_chan_t ),
    .r_chan_t   ( axi_llc_r_chan_t  ),
    .axi_req_t  ( axi_llc_req_t     ),
    .axi_resp_t ( axi_llc_rsp_t     ),
    .DepthAR    ( 32 ),
    .DepthAW    ( 32 ),
    .DepthR     ( 32 ),
    .DepthW     ( 32 ),
    .DepthB     ( 32 ),
    .MaxDelay   ( 2**15-1 )
  ) i_dram_delay (
    .clk_i      ( clk                       ),
    .rst_ni     ( rst_n                     ),
    .aw_delay_i ( cfg_reg2hw.dram_aw_delay.q ),
    .w_delay_i  ( cfg_reg2hw.dram_w_delay.q  ),
    .b_delay_i  ( cfg_reg2hw.dram_b_delay.q  ),
    .ar_delay_i ( cfg_reg2hw.dram_ar_delay.q ),
    .r_delay_i  ( cfg_reg2hw.dram_r_delay.q  ),
    .slv_req_i  ( llc_req                   ),
    .slv_resp_o ( llc_rsp                   ),
    .mst_req_o  ( dly_req                   ),
    .mst_resp_i ( dly_rsp                   )
  );

  // v1.8: refresh windows and latency jitter (off unless +DRAM_REFI / +DRAM_JITTER, fpga/sim/tb_dram_stall.sv)
  tb_dram_stall #(
    .axi_req_t ( axi_llc_req_t ),
    .axi_rsp_t ( axi_llc_rsp_t )
  ) i_dram_stall (
    .clk_i     ( clk      ),
    .rst_ni    ( rst_n    ),
    .slv_req_i ( dly_req  ),
    .slv_rsp_o ( dly_rsp  ),
    .mst_req_o ( dram_req ),
    .mst_rsp_i ( dram_rsp )
  );

  // ---------------------------------------------------------------- DRAM model
  axi_sim_mem #(
    .AddrWidth         ( Cfg.AddrWidth         ),
    .DataWidth         ( Cfg.AxiDataWidth      ),
    .IdWidth           ( $bits(llc_req.aw.id)  ),
    .UserWidth         ( Cfg.AxiUserWidth      ),
    .axi_req_t         ( axi_llc_req_t         ),
    .axi_rsp_t         ( axi_llc_rsp_t         ),
    .WarnUninitialized ( 1'b0                  ),
    .UninitializedData ( "zeros"               ),
    .ApplDelay         ( 2ns                   ),
    .AcqDelay          ( 18ns                  )
  ) i_dram (
    .clk_i              ( clk     ),
    .rst_ni             ( rst_n   ),
    .axi_req_i          ( dram_req ),
    .axi_rsp_o          ( dram_rsp ),
    .mon_w_valid_o      ( ),
    .mon_w_addr_o       ( ),
    .mon_w_data_o       ( ),
    .mon_w_id_o         ( ),
    .mon_w_user_o       ( ),
    .mon_w_beat_count_o ( ),
    .mon_w_last_o       ( ),
    .mon_r_valid_o      ( ),
    .mon_r_addr_o       ( ),
    .mon_r_data_o       ( ),
    .mon_r_id_o         ( ),
    .mon_r_user_o       ( ),
    .mon_r_beat_count_o ( ),
    .mon_r_last_o       ( )
  );

  // ---------------------------------------------------------------- testbench AXI manager (port 1)
  localparam logic [47:0] RegsBase = 48'h0300_0000;   // Cheshire SoC registers (SCRATCH[0..15])
  localparam logic [47:0] CfgBase  = 48'h4300_0000;   // chs_xilinx cfg registers (DRAM delays at +0x0C..+0x1C)

  initial ext_mst_req[1] = '0;

  // Single 32-bit write; drives on the falling edge, samples handshakes on the rising edge.
  task automatic tb_write32(input logic [47:0] addr, input logic [31:0] data);
    bit aw_done = 0, w_done = 0;
    @(negedge clk);
    ext_mst_req[1]          = '0;
    ext_mst_req[1].aw.addr  = addr;
    ext_mst_req[1].aw.size  = 3'd2;
    ext_mst_req[1].aw.burst = axi_pkg::BURST_INCR;
    ext_mst_req[1].aw_valid = 1'b1;
    ext_mst_req[1].w.data   = {(Cfg.AxiDataWidth / 32){data}};
    ext_mst_req[1].w.strb   = 4'hF << (addr[$clog2(Cfg.AxiDataWidth / 8)-1:0] & ~2'b11);
    ext_mst_req[1].w.last   = 1'b1;
    ext_mst_req[1].w_valid  = 1'b1;
    ext_mst_req[1].b_ready  = 1'b1;
    while (!(aw_done && w_done)) begin
      @(posedge clk);
      if (ext_mst_rsp[1].aw_ready) aw_done = 1;
      if (ext_mst_rsp[1].w_ready)  w_done  = 1;
      @(negedge clk);
      if (aw_done) ext_mst_req[1].aw_valid = 1'b0;
      if (w_done)  ext_mst_req[1].w_valid  = 1'b0;
    end
    do @(posedge clk); while (!ext_mst_rsp[1].b_valid);
    @(negedge clk);
    ext_mst_req[1].b_ready = 1'b0;
  endtask

  task automatic tb_read32(input logic [47:0] addr, output logic [31:0] data);
    @(negedge clk);
    ext_mst_req[1]          = '0;
    ext_mst_req[1].ar.addr  = addr;
    ext_mst_req[1].ar.size  = 3'd2;
    ext_mst_req[1].ar.burst = axi_pkg::BURST_INCR;
    ext_mst_req[1].ar_valid = 1'b1;
    ext_mst_req[1].r_ready  = 1'b1;
    do @(posedge clk); while (!ext_mst_rsp[1].ar_ready);
    @(negedge clk);
    ext_mst_req[1].ar_valid = 1'b0;
    do @(posedge clk); while (!ext_mst_rsp[1].r_valid);
    data = ext_mst_rsp[1].r.data >> (8 * (addr[$clog2(Cfg.AxiDataWidth / 8)-1:0] & ~3'b011));
    @(negedge clk);
    ext_mst_req[1].r_ready = 1'b0;
  endtask

  // ---------------------------------------------------------------- UART receiver
  int unsigned uart_baud = 115200;
  int          uart_fd   = 0;

  initial begin
    time bit_t;
    logic [7:0] ch;
    string uart_log;
    void'($value$plusargs("UART_BAUD=%d", uart_baud));
    if ($value$plusargs("UART_LOG=%s", uart_log)) uart_fd = $fopen(uart_log, "w");
    bit_t = 1s / uart_baud;
    wait (rst_n);
    forever begin
      @(negedge uart_tx);
      #(bit_t / 2);
      if (uart_tx) continue;          // glitch, not a start bit
      for (int i = 0; i < 8; i++) begin
        #(bit_t);
        ch[i] = uart_tx;
      end
      #(bit_t);                       // stop bit
      $write("%c", ch);
      if (uart_fd) begin $fwrite(uart_fd, "%c", ch); $fflush(uart_fd); end
    end
  end

  // ---------------------------------------------------------------- VGA capture (v1.2)
  // The timing FSM of axi_vga is registered and steps once every <div> SoC cycles; a line starts with its visible
  // part. When a vsync pulse ends, the next <vbp> lines are the back porch, then <vvis> visible lines follow, so
  // pixel (x, y) is on the pins for the <div> cycles starting <div> * ((vbp + y) * htotal + x) cycles after that
  // edge. Syncs are active low (negative polarity, CONTROL.*SYNC_POL = 0). Sampled on the falling edge.
  int vga_errs = 0, vga_done = 0;
  initial begin
    string fname, tstr;
    logic [63:0] trig;
    int t[9] = '{640, 16, 96, 48, 480, 10, 2, 33, 2};
    int fd, htot, x, y, n;
    logic [15:0] px;
    if ($value$plusargs("VGA_CAPTURE=%s", fname)) begin
      if ($value$plusargs("VGA_TIMING=%s", tstr))
        n = $sscanf(tstr, "%d,%d,%d,%d,%d,%d,%d,%d,%d", t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8]);
      htot = t[0] + t[1] + t[2] + t[3];
      wait (rst_n);
      @(negedge clk);
      // +VGA_TRIGGER=<hex addr> (DOOM): capture only after the program has written a non-zero byte there
      if ($value$plusargs("VGA_TRIGGER=%h", trig)) begin
        while (!(i_dram.mem.exists(trig) && i_dram.mem[trig] != 8'h00)) repeat (5000) @(negedge clk);
        $display("[tb] VGA: trigger at 0x%h seen (%0t)", trig, $time);
      end
      // First genuine vsync pulse: low for exactly vsync lines. The polarity bits reset to 1 (active high), so the pin
      // idles low until the program selects negative syncs; that first rising edge is not the end of a pulse.
      forever begin
        int low;                                    // static in an initial block: reset explicitly every pass
        low = 0;
        while (vga_vs !== 1'b0) @(negedge clk);
        while (vga_vs === 1'b0) begin low++; @(negedge clk); end
        if (low == t[6] * htot * t[8]) break;       // its end: cycle 0
        $display("[tb] VGA: ignoring a %0d-cycle low phase of vsync at %0t (not a %0d-line pulse)", low, $time, t[6]);
      end
      $display("[tb] VGA: vsync seen at %0t, capturing %0dx%0d", $time, t[0], t[4]);
      fd = $fopen(fname, "wb");
      for (y = -t[7]; y < t[4]; y++) begin
        for (x = 0; x < htot; x++) begin
          // hsync active exactly on [hvis + hfp, hvis + hfp + hsync); vsync inactive over the whole capture
          if (vga_hs !== !(x >= t[0] + t[1] && x < t[0] + t[1] + t[2]) || vga_vs !== 1'b1) begin
            if (vga_errs < 5) $display("[tb] VGA: sync mismatch at line %0d px %0d (hs %b vs %b)", y, x, vga_hs, vga_vs);
            vga_errs++;
          end
          if (y >= 0 && x < t[0]) begin
            px = {vga_r, vga_g, vga_b};
            $fwrite(fd, "%c%c", px[7:0], px[15:8]);
          end else if ({vga_r, vga_g, vga_b} !== '0) begin
            if (vga_errs < 5) $display("[tb] VGA: colour outside the visible area at line %0d px %0d", y, x);
            vga_errs++;
          end
          repeat (t[8]) @(negedge clk);
        end
      end
      $fclose(fd);
      vga_done = 1;
      $display("[tb] VGA: frame captured to %s, sync / blanking errors %0d (%0t)", fname, vga_errs, $time);
    end
  end

  // ---------------------------------------------------------------- external register slaves
  assign cfg_reg_req    = ext_reg_req[0];
  assign ext_reg_rsp[0] = cfg_reg_rsp;

  logic ps2_clk = 1'b1, ps2_data = 1'b1;
  if (Ps2) begin : g_ps2
    logic [31:0] ps2_rdata;
    chs_ps2 i_chs_ps2 (
      .clk_i      ( clk                      ),
      .rst_ni     ( rst_n                    ),
      .ps2_clk_i  ( ps2_clk                  ),
      .ps2_data_i ( ps2_data                 ),
      .vsync_i    ( vga_vs                   ),
      .req_i      ( ext_reg_req[1].valid     ),
      .we_i       ( ext_reg_req[1].write     ),
      .addr_i     ( ext_reg_req[1].addr[3:2] ),
      .wdata_i    ( ext_reg_req[1].wdata     ),
      .rdata_o    ( ps2_rdata                )
    );
    assign ext_reg_rsp[1] = '{rdata: ps2_rdata, error: 1'b0, ready: 1'b1};

    // PS/2 device model: start 0, 8 data bits LSB first, odd parity, stop 1; data changes while the clock is high
    initial begin
      string keys;
      int fd, n;
      longint unsigned t_us;
      logic [7:0] b;
      int unsigned bit_ns = 20000;
      void'($value$plusargs("PS2_BIT_NS=%d", bit_ns));
      if ($value$plusargs("PS2_KEYS=%s", keys)) begin
        fd = $fopen(keys, "r");
        if (fd == 0) $fatal(1, "tb_g2d_chs: cannot open %s", keys);
        while (!$feof(fd)) begin
          logic [10:0] fr;
          n = $fscanf(fd, "%d %h\n", t_us, b);
          if (n != 2) break;
          if ($time < t_us * 1us) #(t_us * 1us - $time);
          fr = {1'b1, ~^b, b, 1'b0};
          for (int i = 0; i < 11; i++) begin
            ps2_data = fr[i];
            #(bit_ns * 1ns / 2);
            ps2_clk = 1'b0;
            #(bit_ns * 1ns / 2);
            ps2_clk = 1'b1;
          end
          ps2_data = 1'b1;
          #(bit_ns * 1ns);
          $display("[tb] PS/2 byte %02h sent (%0t)", b, $time);
        end
        $fclose(fd);
      end
    end
  end

  // ---------------------------------------------------------------- preload, boot, wait for the end
  initial begin
    string memh;
    longint unsigned entry = 64'h8000_0000, timeout_us = 200000;
    int fd, n;
    logic [63:0] a, d;
    logic [31:0] s2;
    longint unsigned words = 0;

    if (!$value$plusargs("MEMH=%s", memh)) $fatal(1, "tb_g2d_chs: +MEMH=<file> missing");
    void'($value$plusargs("ENTRY=%h", entry));
    void'($value$plusargs("TIMEOUT_US=%d", timeout_us));

    wait (rst_n);
    @(posedge clk);
    fd = $fopen(memh, "r");
    if (fd == 0) $fatal(1, "tb_g2d_chs: cannot open %s", memh);
    while (!$feof(fd)) begin
      n = $fscanf(fd, "%h %h\n", a, d);
      if (n != 2) break;
      for (int b = 0; b < 8; b++) i_dram.mem[a + b] = d[8*b +: 8];
      words++;
    end
    $fclose(fd);
    $display("[tb] preloaded %0d words from %s, entry 0x%h", words, memh, entry);
    begin   // +BIN=<file>@<addr>: binary preload (DOOM's WAD image)
      string bin, fn;
      logic [7:0] chunk [65536];
      longint unsigned at, total = 0;
      int at_pos, got;
      if ($value$plusargs("BIN=%s", bin)) begin
        at_pos = -1;
        for (int i = 0; i < bin.len(); i++) if (bin[i] == "@") at_pos = i;
        if (at_pos < 0) $fatal(1, "tb_g2d_chs: +BIN=<file>@<hex addr>");
        fn = bin.substr(0, at_pos - 1);
        // %h into a 64-bit variable: string::atohex returns a 32-bit int, sign-extended for addresses >= 2^31
        if ($sscanf(bin.substr(at_pos + 1, bin.len() - 1), "%h", at) != 1) $fatal(1, "tb_g2d_chs: bad +BIN address");
        fd = $fopen(fn, "rb");
        if (fd == 0) $fatal(1, "tb_g2d_chs: cannot open %s", fn);
        do begin
          got = $fread(chunk, fd);
          for (int i = 0; i < got; i++) i_dram.mem[at + total + longint'(i)] = chunk[i];
          total += longint'(got);
        end while (got == 65536);
        $fclose(fd);
        $display("[tb] preloaded %0d bytes from %s at 0x%h", total, fn, at);
      end
    end

    fork
      begin
        #(timeout_us * 1us);
        $display("\n[tb] TB_RESULT TIMEOUT after %0d us", timeout_us);
        $finish(2);
      end
    join_none

    // Extra DRAM latency (cfg registers, as the board's USE_RAM_DELAY): AW +0x0C, AR +0x18.
    if ($value$plusargs("RD_DELAY=%d", n)) begin
      tb_write32(CfgBase + 48'h18, n);
      $display("[tb] DRAM AR delay %0d cycles", n);
    end
    if ($value$plusargs("WR_DELAY=%d", n)) begin
      tb_write32(CfgBase + 48'h0C, n);
      $display("[tb] DRAM AW delay %0d cycles", n);
    end

    // Passive boot: entry in SCRATCH[1:0], start = SCRATCH[2] bit 1.
    tb_write32(RegsBase + 0, entry[31:0]);
    tb_write32(RegsBase + 4, entry[63:32]);
    tb_write32(RegsBase + 8, 32'h2);
    // The boot ROM clears SCRATCH[2] when it starts the program; crt0's _exit sets bit 0.
    do begin
      repeat (2000) @(posedge clk);
      tb_read32(RegsBase + 8, s2);
    end while (!s2[0]);
    repeat (5000) @(posedge clk);     // let the UART drain
    if (s2[31:1] == 0) $display("\n[tb] TB_RESULT PASS (%0t)", $time);
    else               $display("\n[tb] TB_RESULT FAIL code=%0d (%0t)", s2[31:1], $time);
    if (uart_fd) $fclose(uart_fd);
    $finish(s2[31:1] == 0 ? 0 : 1);
  end

endmodule
