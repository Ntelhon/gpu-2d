# SPDX-License-Identifier: Apache-2.0
# Clock wizard of the asynchronous G2D build (v1.1, make fpga FPGA_ASYNC=1): Cheshire's Genesys2 clock wizard
# (200 MHz in, 50 / 48 / 20 MHz out, one MMCM) with its unused fourth output (Cheshire: clk_10, not
# connected in its top) set to 100 MHz for the G2D core. Module g2d_clkwiz; the generated top instantiates it in
# place of Cheshire's clkwiz (fpga/scripts/gen_top.py --async), so the SoC keeps exactly its clock structure (no extra
# MMCM: an extra one made the clock placer fail, docs/FPGA.md §5).
# Usage (through vivado_boards.tcl, from build_fpga.sh): g2d_clkwiz_ip.tcl <out_dir>

set out_dir [file normalize [lindex $argv 0]]
file mkdir $out_dir
create_project g2d_clkwiz $out_dir/proj -force -part xc7k325tffg900-2
set_property board_part digilentinc.com:genesys2:part0:1.1 [current_project]

create_ip -name clk_wiz -vendor xilinx.com -library ip -version 6.0 -module_name g2d_clkwiz
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ {200.000} \
    CONFIG.CLKIN1_JITTER_PS {50.0} \
    CONFIG.CLKOUT1_USED {true} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT3_USED {true} \
    CONFIG.CLKOUT4_USED {true} \
    CONFIG.NUM_OUT_CLKS {4} \
    CONFIG.CLK_OUT1_PORT {clk_50} \
    CONFIG.CLK_OUT2_PORT {clk_48} \
    CONFIG.CLK_OUT3_PORT {clk_20} \
    CONFIG.CLK_OUT4_PORT {clk_100} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {48.000} \
    CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {20.000} \
    CONFIG.CLKOUT4_REQUESTED_OUT_FREQ {100.000} \
    ] [get_ips g2d_clkwiz]

set xci [get_files g2d_clkwiz.xci]
generate_target all $xci
create_ip_run $xci
launch_run -jobs 8 g2d_clkwiz_synth_1
wait_on_run g2d_clkwiz_synth_1
# the wizard derives the MMCM settings (VCO 1200 MHz: /24, /25, /60, /12); print what it chose
set ip [get_ips g2d_clkwiz]
puts "G2D_CLKWIZ divide out1 [get_property CONFIG.MMCM_CLKOUT0_DIVIDE_F $ip] out2 [get_property CONFIG.MMCM_CLKOUT1_DIVIDE $ip]\
 out3 [get_property CONFIG.MMCM_CLKOUT2_DIVIDE $ip] out4 [get_property CONFIG.MMCM_CLKOUT3_DIVIDE $ip]"
puts "G2D_CLKWIZ mult [get_property CONFIG.MMCM_CLKFBOUT_MULT_F $ip] divclk [get_property CONFIG.MMCM_DIVCLK_DIVIDE $ip]"
file delete -force $out_dir/out.xci
file link -symbolic $out_dir/out.xci [file normalize $xci]
puts "G2D_CLKWIZ_DONE [file normalize $xci]"
