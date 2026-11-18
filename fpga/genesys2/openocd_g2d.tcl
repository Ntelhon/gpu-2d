# SPDX-License-Identifier: Apache-2.0
# OpenOCD: CVA6 debug module of the Cheshire + G2D design on the Genesys2, through the board's USB-JTAG
# (FT2232H 0403:6010, channel 0). Same adapter, layout and TAP values as Cheshire's util/openocd.genesys2.tcl, in
# the current OpenOCD syntax (no deprecated commands), plus a CRC-checked loader (load_verified).
#
# Vivado's hw_server must not hold the cable while OpenOCD runs (program the FPGA first, then close it).
#
# usage: openocd [-c "set JTAG_KHZ 4000"] [-c "set GDB_PORT 3333"] [-c "set TELNET_PORT 4444"] -f openocd_g2d.tcl
#        The GDB / telnet / Tcl ports are off unless set: a script run needs none, so an OpenOCD (or anything else)
#        already listening on 3333 does not matter.

adapter driver ftdi
ftdi vid_pid 0x0403 0x6010
ftdi channel 0
ftdi layout_init 0x00e8 0x60eb
if { ![info exists JTAG_KHZ] } { set JTAG_KHZ 8000 }
adapter speed $JTAG_KHZ
transport select jtag
reset_config none

if { ![info exists GDB_PORT] } { set GDB_PORT disabled }
if { ![info exists TELNET_PORT] } { set TELNET_PORT disabled }
gdb_port $GDB_PORT
telnet_port $TELNET_PORT
tcl_port disabled

jtag newtap riscv cpu -irlen 5 -expected-id 0x1c5e5db3
target create riscv.cpu riscv -chain-position riscv.cpu -coreid 0

riscv set_reset_timeout_sec 120
riscv set_command_timeout_sec 120
riscv set_mem_access sysbus progbuf

# Scratch RAM for the on-target CRC of verify_image_checksum: DRAM between the test program's stack (ends at
# 0x8080_0000) and the GPU pool (0x8100_0000), which nothing in the test uses.
riscv.cpu configure -work-area-phys 0x80F00000 -work-area-size 0x10000 -work-area-backup 0

# load_verified <file> <address> [chunk dir] [chunk bytes]: load a binary in chunks, check each chunk with a CRC
# computed by CVA6 (only 4 bytes travel back) and reload chunks that do not match, up to 3 times. Call it with the
# hart halted (after programming or a CPU reset).
proc load_verified {file addr {dir chunks} {chunk 0x40000}} {
    file mkdir $dir
    set fd [open $file rb]
    set i 0
    set bad 0
    set t0 [clock seconds]
    while {1} {
        set data [read $fd $chunk]
        if {[string length $data] == 0} { break }
        set cf [format "%s/c%04d.bin" $dir $i]
        set out [open $cf wb]
        puts -nonewline $out $data
        close $out
        set a [expr {$addr + $i * $chunk}]
        set ok 0
        for {set try 1} {$try <= 3 && !$ok} {incr try} {
            load_image $cf $a bin
            if {[catch {verify_image_checksum $cf $a bin}] == 0} {
                set ok 1
            } else {
                echo [format "chunk %d at 0x%x: CRC mismatch (try %d)" $i $a $try]
            }
        }
        if {!$ok} { incr bad }
        file delete $cf
        incr i
    }
    close $fd
    echo [format "load_verified: %d chunks, %d bad, %d s" $i $bad [expr {[clock seconds] - $t0}]]
    if {$bad} { error "load_verified: $bad chunk(s) still wrong" }
}

gdb_report_data_abort enable
gdb_report_register_access_error enable

init
halt
echo "Ready for Remote Connections."
