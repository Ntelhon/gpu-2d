# SPDX-License-Identifier: Apache-2.0
# Out-of-context synthesis of g2d_top for the Genesys2 FPGA (xc7k325tffg900-2), Phase 7.
# Usage: vivado -mode batch -source fpga/genesys2/g2d_ooc.tcl -tclargs <out_dir> <period_ns> <file>...
# Parameters as in the Cheshire build (fpga/cheshire/g2d_cheshire.sv): DATA_W 64, ADDR_W 48, ID_W 3.
# Writes utilization, timing summary, the 20 worst paths and the RAM inference report to <out_dir>.
# v1.8 (Q-FPGA-10): also functional netlists of the synthesized and of the routed design (g2d_top_synth_funcsim.v,
# g2d_top_impl_funcsim.v; Xilinx primitives, same top-level ports) for a netlist simulation against the RTL tests,
# and the synthesis log's RAM inference / constant-propagation messages in synth_ram_msgs.txt.

set out_dir   [lindex $argv 0]
set period_ns [lindex $argv 1]
set srcs      [lrange $argv 2 end]
set part      xc7k325tffg900-2

set_param general.maxThreads [expr {[info exists ::env(G2D_VIVADO_THREADS)] ? $::env(G2D_VIVADO_THREADS) : 8}]
file mkdir $out_dir
create_project -in_memory -part $part
read_verilog -sv $srcs
# v1.7: G2D_OOC_FEATURES (6 binary digits, g2d_pkg FEAT_* from bit 5 mask down to bit 0 families) trims optional units
set feat_gen {}
if {[info exists ::env(G2D_OOC_FEATURES)]} { set feat_gen [list -generic "FEATURES=6'b$::env(G2D_OOC_FEATURES)"] }
synth_design -top g2d_top -part $part -mode out_of_context -flatten_hierarchy rebuilt \
  -generic DATA_W=64 -generic ADDR_W=48 -generic ID_W=3 {*}$feat_gen

write_verilog -mode funcsim -force $out_dir/g2d_top_synth_funcsim.v

# Single clock: clk_cfg_i / clk_mem_i are unused with CFG_ASYNC = MEM_ASYNC = 0.
create_clock -name clk -period $period_ns [get_ports clk_i]
set_input_delay  -clock clk [expr {$period_ns * 0.2}] [get_ports -filter {DIRECTION == IN && NAME !~ "clk*"}]
set_output_delay -clock clk [expr {$period_ns * 0.2}] [get_ports -filter {DIRECTION == OUT}]

opt_design
place_design
route_design

report_utilization -file $out_dir/utilization.rpt
report_utilization -hierarchical -hierarchical_depth 4 -file $out_dir/utilization_hier.rpt
report_timing_summary -no_detailed_paths -file $out_dir/timing_summary.rpt
report_timing -max_paths 20 -sort_by slack -file $out_dir/timing_paths.rpt
report_timing -max_paths 100 -nworst 1 -unique_pins -sort_by slack -file $out_dir/timing_endpoints.rpt
report_ram_utilization -file $out_dir/ram.rpt
write_verilog -mode funcsim -force $out_dir/g2d_top_impl_funcsim.v
report_methodology -file $out_dir/methodology.rpt
puts "G2D_OOC_DONE wns=[get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]"
