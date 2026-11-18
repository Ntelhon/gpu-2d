# SPDX-License-Identifier: Apache-2.0
# CDC constraints of the asynchronous G2D build (v1.1, make fpga FPGA_ASYNC=1; arch.md §2.1). An unmanaged Tcl
# constraint file (XDC files do not allow foreach), implementation only (USED_IN_SYNTHESIS false, impl_g2d.tcl). Recipe (AMD UG903 / UG949 for gray-coded CDC FIFOs):
#   - synchronizer flops ASYNC_REG (placed together, no SRL);
#   - gray pointer -> first synchronizer flop: set_max_delay -datapath_only = the faster period + set_bus_skew;
#   - FIFO storage (written in the source domain, read in the destination domain behind the synchronized
#     pointer): set_max_delay -datapath_only;
#   - the core reset synchronizer's asynchronous clear: false path (recovery is not meaningful across domains);
#   - G2D interrupt (core clock) into Cheshire's interrupt synchronizer: set_max_delay -datapath_only.
# report_cdc then lists as critical only the FIFO storage reads (Vivado does not recognise a FIFO it did not generate;
# safe by the gray-pointer protocol) and the reset synchronizer's clear (docs/FPGA.md §5, FPG-05).
# No set_clock_groups: it would override the max-delay exceptions and leave the crossings unconstrained.
# Cell names follow the RTL: g2d_top g_cfg_cdc.u_cfg_cdc / g_mem_cdc.u_mem_cdc, channels u_aw u_w u_b u_ar u_r,
# g2d_cdc_fifo wgray_q / rgray_q / mem_q, g2d_sync u_sync_w2r / u_sync_r2w q_q.

set g2d_core_clk [get_clocks -of_objects [get_pins i_clkwiz/clk_100]]
set g2d_cdc_max  [get_property PERIOD $g2d_core_clk]

set_property ASYNC_REG TRUE [get_cells -hier -filter {NAME =~ *u_*_cdc/u_*/u_sync_*/q_q_reg*}]
set_property ASYNC_REG TRUE [get_cells -hier -filter {NAME =~ *i_g2d_rst/rst_q_reg*}]

# (instance names: the generate block makes them g_mem_cdc.u_mem_cdc, so the patterns do not start with a "/")
set g2d_cdc_n 0
foreach cdc {u_cfg_cdc u_mem_cdc} {
  foreach ch {u_aw u_w u_b u_ar u_r} {
    foreach {ptr sync} {wgray_q u_sync_w2r rgray_q u_sync_r2w} {
      set from [get_cells -quiet -hier -filter "NAME =~ *${cdc}/${ch}/${ptr}_reg*"]
      set to   [get_cells -quiet -hier -filter "NAME =~ *${cdc}/${ch}/${sync}/q_q_reg*"]
      if {![llength $from] || ![llength $to]} {
        error "g2d_cdc.tcl: no cells for ${cdc}/${ch} ${ptr} -> ${sync}"
      }
      set_max_delay -datapath_only -from $from -to $to $g2d_cdc_max
      set_bus_skew -from $from -to $to $g2d_cdc_max
      incr g2d_cdc_n
    }
    set mem [get_cells -quiet -hier -filter "NAME =~ *${cdc}/${ch}/mem_q_reg*"]
    if {![llength $mem]} {
      error "g2d_cdc.tcl: no storage cells for ${cdc}/${ch}"
    }
    set_max_delay -datapath_only -from $mem $g2d_cdc_max
  }
}

set_false_path -to [get_pins -hier -filter {NAME =~ *i_g2d_rst/rst_q_reg*/CLR}]

set_max_delay -datapath_only -from $g2d_core_clk -through [get_pins -of_objects [get_cells -hier \
    -filter {ORIG_REF_NAME=="sync" || REF_NAME=="sync"}] -filter {NAME=~*serial_i}] $g2d_cdc_max

puts "G2D_CDC_CONSTRAINTS core clock [get_property NAME $g2d_core_clk], max delay $g2d_cdc_max ns, $g2d_cdc_n gray buses"
