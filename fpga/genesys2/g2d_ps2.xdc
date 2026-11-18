# SPDX-License-Identifier: Apache-2.0 WITH SHL-2.1
# Genesys2 USB HID port (PIC24: USB keyboard / mouse -> PS/2), DOOM side project (fpga/periph/chs_ps2.sv).
# Pins from Digilent's Genesys-2 master XDC (digilent-xdc, "USB HIDs For Both Mouse and Keyboard").
set_property -dict { PACKAGE_PIN AD23  IOSTANDARD LVCMOS33  PULLUP true } [get_ports { ps2_clk_i }]
set_property -dict { PACKAGE_PIN AE20  IOSTANDARD LVCMOS33  PULLUP true } [get_ports { ps2_data_i }]
# asynchronous inputs, synchronised in chs_ps2
set_false_path -from [get_ports { ps2_clk_i ps2_data_i }]
