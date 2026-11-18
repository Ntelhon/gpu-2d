# SPDX-License-Identifier: Apache-2.0
# Program the Genesys2 with a bitstream through Vivado's hardware manager (Phase 7 board package).
# Usage: vivado -mode batch -source program.tcl -tclargs <file.bit> [hw_server url, default localhost:3121]
set bit [lindex $argv 0]
set url [expr {[llength $argv] > 1 ? [lindex $argv 1] : "localhost:3121"}]
open_hw_manager
connect_hw_server -url $url
open_hw_target
set dev [lindex [get_hw_devices xc7k325t_0] 0]
current_hw_device $dev
set_property PROGRAM.FILE $bit $dev
program_hw_devices $dev
puts "G2D_PROGRAM_DONE [get_property PROGRAM.FILE $dev]"
close_hw_target
disconnect_hw_server
close_hw_manager
