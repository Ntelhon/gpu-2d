# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# G2D timing constraints template (v1.9, D-2; docs/INTEGRATION.md "Clocks, resets and constraints").
# Standard SDC (no tool-specific extensions) for g2d_top and the per-bus wrappers g2d_top_<bus>
# (rtl/wrap/generated). Source it in the synthesis / STA flow after reading the design, with the top's ports.
#
# Settings (define before sourcing to override; periods in ns):
#   G2D_CORE_PERIOD   core clock clk_i                          default 1.667 (600 MHz, ASIC target)
#   G2D_CFG_ASYNC     1 if the build has CFG_ASYNC = 1          default 0 (configuration port on clk_i)
#   G2D_CFG_PERIOD    clk_cfg_i period (CFG_ASYNC = 1 only)     default 10.0
#   G2D_MEM_ASYNC     1 if the build has MEM_ASYNC = 1          default 0 (memory port on clk_i)
#   G2D_MEM_PERIOD    clk_mem_i period (MEM_ASYNC = 1 only)     default 1.667
#   G2D_IN_FRAC       part of a period taken outside G2D before an input   default 0.6
#   G2D_OUT_FRAC      part of a period taken outside G2D after an output   default 0.6
#   G2D_UNCERTAINTY   clock uncertainty (jitter + skew margin)  default 0.05
#   G2D_CFG_PORTS     configuration-port pattern, without _i/_o default "s_*" (s_axil_* or a wrapper's bus)
#   G2D_MEM_PORTS     memory-port pattern, without _i/_o        default "m_axi_*"
#
# Clock-domain crossings (CFG_ASYNC / MEM_ASYNC builds, arch.md §2.1): every crossing inside G2D is a gray-coded
# pointer into a g2d_sync synchronizer or a read of g2d_cdc_fifo storage behind such a pointer. All paths between two
# G2D clocks get a maximum delay of the faster period and no hold check, which bounds the skew of each gray pointer
# bus to less than one destination period (the FPGA recipe in fpga/genesys2/g2d_cdc.tcl does the same with Vivado's
# -datapath_only and set_bus_skew). No set_clock_groups: it would remove these bounds.
# Synchronizer flops (instances u_sync_*, registers q_q) should be marked for the flow's synchronizer handling (no
# retiming, placed together; e.g. a "dont_touch" / synchronizer cell mapping), which is library specific.
#
# Resets (arch.md §2.2): rst_ni / rst_cfg_ni / rst_mem_ni are asserted asynchronously and released synchronously to
# their own clock by the integrator's reset synchronizer: they are timed as inputs of their domain (recovery /
# removal checks inside G2D). test_mode_i is 0 in functional timing (set_case_analysis).

if {![info exists G2D_CORE_PERIOD]} { set G2D_CORE_PERIOD 1.667 }
if {![info exists G2D_CFG_ASYNC]}   { set G2D_CFG_ASYNC 0 }
if {![info exists G2D_CFG_PERIOD]}  { set G2D_CFG_PERIOD 10.0 }
if {![info exists G2D_MEM_ASYNC]}   { set G2D_MEM_ASYNC 0 }
if {![info exists G2D_MEM_PERIOD]}  { set G2D_MEM_PERIOD 1.667 }
if {![info exists G2D_IN_FRAC]}     { set G2D_IN_FRAC 0.6 }
if {![info exists G2D_OUT_FRAC]}    { set G2D_OUT_FRAC 0.6 }
if {![info exists G2D_UNCERTAINTY]} { set G2D_UNCERTAINTY 0.05 }
if {![info exists G2D_CFG_PORTS]}   { set G2D_CFG_PORTS "s_*" }
if {![info exists G2D_MEM_PORTS]}   { set G2D_MEM_PORTS "m_axi_*" }

# ---------------------------------------------------------------- clocks
create_clock -name g2d_core -period $G2D_CORE_PERIOD [get_ports clk_i]
set g2d_cfg_clk g2d_core
set g2d_cfg_period $G2D_CORE_PERIOD
set g2d_mem_clk g2d_core
set g2d_mem_period $G2D_CORE_PERIOD
if {$G2D_CFG_ASYNC} {
  create_clock -name g2d_cfg -period $G2D_CFG_PERIOD [get_ports clk_cfg_i]
  set g2d_cfg_clk g2d_cfg
  set g2d_cfg_period $G2D_CFG_PERIOD
}
if {$G2D_MEM_ASYNC} {
  create_clock -name g2d_mem -period $G2D_MEM_PERIOD [get_ports clk_mem_i]
  set g2d_mem_clk g2d_mem
  set g2d_mem_period $G2D_MEM_PERIOD
}
set_clock_uncertainty $G2D_UNCERTAINTY [all_clocks]

# ---------------------------------------------------------------- inputs and outputs
# Direction from the port names (the RTL rule: inputs end in _i, outputs in _o); no tool-specific -filter syntax.
set_input_delay  -clock $g2d_cfg_clk [expr {$G2D_IN_FRAC  * $g2d_cfg_period}] [get_ports ${G2D_CFG_PORTS}_i]
set_output_delay -clock $g2d_cfg_clk [expr {$G2D_OUT_FRAC * $g2d_cfg_period}] [get_ports ${G2D_CFG_PORTS}_o]
set_input_delay  -clock $g2d_mem_clk [expr {$G2D_IN_FRAC  * $g2d_mem_period}] [get_ports ${G2D_MEM_PORTS}_i]
set_output_delay -clock $g2d_mem_clk [expr {$G2D_OUT_FRAC * $g2d_mem_period}] [get_ports ${G2D_MEM_PORTS}_o]
set_input_delay  -clock g2d_core     [expr {$G2D_IN_FRAC  * $G2D_CORE_PERIOD}] [get_ports rst_ni]
set_input_delay  -clock $g2d_cfg_clk [expr {$G2D_IN_FRAC  * $g2d_cfg_period}] [get_ports rst_cfg_ni]
set_input_delay  -clock $g2d_mem_clk [expr {$G2D_IN_FRAC  * $g2d_mem_period}] [get_ports rst_mem_ni]
set_output_delay -clock g2d_core     [expr {$G2D_OUT_FRAC * $G2D_CORE_PERIOD}] [get_ports irq_o]

# DFT input: functional mode
set_case_analysis 0 [get_ports test_mode_i]

# ---------------------------------------------------------------- clock-domain crossings
proc g2d_cdc {a pa b pb} {
  set lim [expr {$pa < $pb ? $pa : $pb}]
  set_max_delay $lim -from [get_clocks $a] -to [get_clocks $b]
  set_max_delay $lim -from [get_clocks $b] -to [get_clocks $a]
  set_false_path -hold -from [get_clocks $a] -to [get_clocks $b]
  set_false_path -hold -from [get_clocks $b] -to [get_clocks $a]
}
if {$G2D_CFG_ASYNC} { g2d_cdc g2d_core $G2D_CORE_PERIOD g2d_cfg $G2D_CFG_PERIOD }
if {$G2D_MEM_ASYNC} { g2d_cdc g2d_core $G2D_CORE_PERIOD g2d_mem $G2D_MEM_PERIOD }
if {$G2D_CFG_ASYNC && $G2D_MEM_ASYNC} { g2d_cdc g2d_cfg $G2D_CFG_PERIOD g2d_mem $G2D_MEM_PERIOD }
