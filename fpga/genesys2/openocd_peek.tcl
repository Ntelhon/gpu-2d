# SPDX-License-Identifier: Apache-2.0
# OpenOCD: read memory / registers of the Cheshire + G2D design on the Genesys2 through the debug module's system
# bus access (SBA) WITHOUT halting the CPU (it may be stuck on a bus access and impossible to halt). Same adapter and
# TAP as openocd_g2d.tcl; the target is created with -defer-examine, so OpenOCD does not try to halt it.
#
# usage:  openocd -f openocd_peek.tcl -c "sbdump 0x48000000 44" -c shutdown
#    or:  openocd -c "set TELNET_PORT 4444" -f openocd_peek.tcl      then in telnet: sbdump 0x48000000 44
# Commands: sbr <addr> (one 32-bit word), sbdump <addr> <words>, sbw <addr> <value>.
# A read that never completes (the bus slave is stuck too) is reported as "sbbusy" instead of hanging.

adapter driver ftdi
ftdi vid_pid 0x0403 0x6010
ftdi channel 0
ftdi layout_init 0x00e8 0x60eb
if { ![info exists JTAG_KHZ] } { set JTAG_KHZ 8000 }
adapter speed $JTAG_KHZ
transport select jtag
reset_config none
if { ![info exists TELNET_PORT] } { set TELNET_PORT disabled }
gdb_port disabled
telnet_port $TELNET_PORT
tcl_port disabled

jtag newtap riscv cpu -irlen 5 -expected-id 0x1c5e5db3
target create riscv.cpu riscv -chain-position riscv.cpu -coreid 0 -defer-examine

init

# Raw DMI access through JTAG scans (OpenOCD's riscv dmi_* commands need an examined target, i.e. a halt).
# RISC-V debug spec 0.13: IR 0x10 = dtmcs (abits in bits 9:4), IR 0x11 = dmi = {address[abits], data[32], op[2]};
# op 1 read, 2 write; the result of an access comes out of the next scan (op 0 = nop), status 0 = ok, 3 = busy.
proc dtm_abits {} {
    irscan riscv.cpu 0x10
    set v [drscan riscv.cpu 32 0]
    return [expr {(("0x$v") >> 4) & 0x3f}]
}
set ABITS [dtm_abits]
echo [format "dtmcs abits = %d" $ABITS]
proc dmi_op {op data addr} {
    global ABITS
    irscan riscv.cpu 0x11
    drscan riscv.cpu 2 $op 32 $data $ABITS $addr
    runtest 64
    for {set k 0} {$k < 100} {incr k} {
        set r [drscan riscv.cpu 2 0 32 0 $ABITS 0]
        runtest 64
        set st [expr {"0x[lindex $r 0]"}]
        if {$st == 0} { return [expr {"0x[lindex $r 1]"}] }
        if {$st == 3} {      ;# busy: clear dmireset in dtmcs and retry the access
            irscan riscv.cpu 0x10
            drscan riscv.cpu 32 [expr {1 << 16}]
            irscan riscv.cpu 0x11
            drscan riscv.cpu 2 $op 32 $data $ABITS $addr
            runtest 256
        } else {
            return -1
        }
    }
    return -1
}
proc dmi_write {addr data} { dmi_op 2 $data $addr }
proc dmi_read {addr} { return [dmi_op 1 0 $addr] }

# Debug-module registers: dmcontrol 0x10, sbcs 0x38, sbaddress0 0x39, sbaddress1 0x3a, sbdata0 0x3c. sbcs:
# sbbusyerror bit 22 (w1c), sbbusy 21, sbreadonaddr 20, sbaccess 19:17 (2 = 32 bit), sberror 14:12 (w1c).
proc sb_setup {} {
    dmi_write 0x10 1
    dmi_write 0x38 [expr {(1 << 22) | (7 << 12)}]
    dmi_write 0x38 [expr {(1 << 20) | (2 << 17)}]
}
proc sb_wait {} {
    for {set i 0} {$i < 200} {incr i} {
        set cs [dmi_read 0x38]
        if {$cs < 0} { return -1 }
        if {!($cs & (1 << 21))} { return $cs }
    }
    return -1
}
proc sbr {addr} {
    sb_setup
    dmi_write 0x3a [expr {($addr >> 32) & 0xffffffff}]
    dmi_write 0x39 [expr {$addr & 0xffffffff}]
    set cs [sb_wait]
    if {$cs < 0} { return "sbbusy" }
    if {($cs >> 12) & 7} { return [format "sberror%d" [expr {($cs >> 12) & 7}]] }
    return [format "%08x" [dmi_read 0x3c]]
}
proc sbdump {addr n} {
    for {set i 0} {$i < $n} {incr i 4} {
        set line [format "0x%08x:" [expr {$addr + 4 * $i}]]
        for {set j $i} {$j < $i + 4 && $j < $n} {incr j} { append line " " [sbr [expr {$addr + 4 * $j}]] }
        echo $line
    }
}
proc sbw {addr value} {
    dmi_write 0x10 1
    dmi_write 0x38 [expr {(1 << 22) | (7 << 12)}]
    dmi_write 0x38 [expr {2 << 17}]
    dmi_write 0x3a [expr {($addr >> 32) & 0xffffffff}]
    dmi_write 0x39 [expr {$addr & 0xffffffff}]
    dmi_write 0x3c $value
    set cs [sb_wait]
    if {$cs < 0} { echo "sbbusy" } elseif {($cs >> 12) & 7} { echo [format "sberror%d" [expr {($cs >> 12) & 7}]] }
}
echo "openocd_peek ready (sbr / sbdump / sbw)"
