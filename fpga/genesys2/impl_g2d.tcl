# SPDX-License-Identifier: Apache-2.0
# Cheshire + G2D bitstream for the Genesys2 (Phase 7). Follows Cheshire's target/xilinx/scripts/impl_sys.tcl
# (same part, board, IPs, constraints and strategies) with our generated top g2d_chs_top_xilinx and without the
# ILA insertion.
# Usage (through vivado_boards.tcl): impl_g2d.tcl <out_dir> <cheshire add_sources.tcl> <g2d files list> <ip.xci>...
# Env: CHS_ROOT, G2D_VIVADO_JOBS (default 8), G2D_FPGA_ASYNC (1: async build, adds fpga/genesys2/g2d_cdc.tcl).

set out_dir  [file normalize [lindex $argv 0]]
set add_src  [lindex $argv 1]
set g2d_list [lindex $argv 2]
set ips      [lrange $argv 3 end]
set jobs     [expr {[info exists ::env(G2D_VIVADO_JOBS)] ? $::env(G2D_VIVADO_JOBS) : 8}]
set xdc_dir  $::env(CHS_ROOT)/target/xilinx/constraints

set_param general.maxThreads [expr {$jobs > 8 ? 8 : $jobs}]
file mkdir $out_dir
create_project g2d_chs $out_dir/proj -force -part xc7k325tffg900-2
set_property board_part digilentinc.com:genesys2:part0:1.1 [current_project]

read_ip $ips
import_files -fileset constrs_1 -norecurse $xdc_dir/cheshire.xdc
import_files -fileset constrs_1 -norecurse $xdc_dir/genesys2.xdc
if {[info exists ::env(G2D_FPGA_ASYNC)] && $::env(G2D_FPGA_ASYNC) == 1} {
    set cdc_xdc [file normalize [file dirname [info script]]/g2d_cdc.tcl]
    add_files -fileset constrs_1 -norecurse $cdc_xdc
    set_property USED_IN_SYNTHESIS false [get_files $cdc_xdc]
    set_property PROCESSING_ORDER LATE [get_files $cdc_xdc]
}
if {[info exists ::env(G2D_FPGA_DOOM)] && $::env(G2D_FPGA_DOOM) == 1} {
    add_files -fileset constrs_1 -norecurse [file normalize [file dirname [info script]]/g2d_ps2.xdc]
}
source $add_src
set fh [open $g2d_list]
add_files -norecurse -fileset [current_fileset] [split [string trim [read $fh]] "\n"]
close $fh

set_property source_mgmt_mode None [current_project]
set_property top g2d_chs_top_xilinx [current_fileset]
update_compile_order -fileset sources_1
set_property XPM_LIBRARIES XPM_MEMORY [current_project]
set_property strategy Flow_PerfOptimized_high [get_runs synth_1]

launch_runs -jobs $jobs synth_1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {
    puts "G2D_IMPL_ERROR synthesis failed"
    exit 1
}
open_run synth_1
report_utilization -file $out_dir/utilization.synth.rpt
report_utilization -hierarchical -hierarchical_depth 3 -file $out_dir/utilization_hier.synth.rpt
close_design

set_property strategy Performance_ExtraTimingOpt [get_runs impl_1]
set_property STEPS.POST_ROUTE_PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
launch_runs -jobs $jobs impl_1 -to_step write_bitstream
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {
    puts "G2D_IMPL_ERROR implementation failed"
    exit 1
}
open_run impl_1
report_utilization -file $out_dir/utilization.impl.rpt
report_utilization -hierarchical -hierarchical_depth 3 -file $out_dir/utilization_hier.impl.rpt
report_timing_summary -file $out_dir/timing_summary.impl.rpt
report_timing -max_paths 30 -sort_by slack -file $out_dir/timing_paths.impl.rpt
report_clock_interaction -file $out_dir/clock_interaction.impl.rpt
report_cdc -file $out_dir/cdc.impl.rpt
report_methodology -file $out_dir/methodology.impl.rpt

set trep [report_timing_summary -no_header -no_detailed_paths -return_string]
set bit_name [expr {[info exists ::env(G2D_FPGA_ASYNC)] && $::env(G2D_FPGA_ASYNC) == 1 ? "g2d_chs_async" : "g2d_chs"}]
if {[info exists ::env(G2D_FPGA_TRIM)] && $::env(G2D_FPGA_TRIM) == 1} { append bit_name "_trim" }
if {[info exists ::env(G2D_FPGA_DOOM)] && $::env(G2D_FPGA_DOOM) == 1} { append bit_name "_doom" }
file copy -force $out_dir/proj/g2d_chs.runs/impl_1/g2d_chs_top_xilinx.bit $out_dir/$bit_name.genesys2.bit
if {![string match -nocase {*timing constraints are met*} $trep]} {
    puts "G2D_IMPL_ERROR timing constraints not met (bitstream kept for inspection only)"
    exit 1
}
puts "G2D_IMPL_DONE $out_dir/$bit_name.genesys2.bit"
