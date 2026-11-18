# SPDX-License-Identifier: Apache-2.0
"""axi_vga per-frame re-synchronisation (DOOM board run 2026-11-05: the picture slid under memory load).

The original axi_vga shows a pixel every visible clock whether its fetcher has one or not and never re-aligns: a
late beat shifts the rest of the picture for good. The G2D copy (fpga/cheshire/axi_vga_g2d) drains and restarts the
fetcher at every vertical sync. Bench (dv/tb/tb_axi_vga.sv): 64 x 8 visible, framebuffer in a cocotbext-axi AxiRam;
every visible pixel is compared with the framebuffer; in the middle the R channel is stalled long enough to starve
the stream for about two frames.
  g2d variant:  every frame before the stall and every frame from the second after it is bit-exact
  orig variant: (the bug, for comparison) frames after the stall stay shifted
Burst lengths 16 and 256 beats (BURST_LEN 15 / 255). Needs bender + Cheshire (CHS_ROOT), as the SoC simulation.

v1.8 (Q-FPGA-11): the fork issues real bursts (the original splits every burst into one-beat ARs) into a block-RAM
buffer and has debug counters at 0x40 (underrun pixels / frames, minimum fill, worst AR-to-R latency, frames, info).
Mode "arb": the AR channel is granted only once every ARB_PERIOD cycles, as in a crossbar shared with a manager
issuing long bursts; one-beat ARs cannot keep up (the scan-out needs a beat every 4 cycles here), bursts can. Every
mode checks the counters against the frames seen (no underrun where the frames are bit-exact).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from cocotbext.axi import AxiBus, AxiRam

ROOT = Path(__file__).resolve().parents[2]
HV, HF, HS, HB = 64, 4, 4, 4
VV, VF, VS, VB = 8, 2, 2, 6
FB = 0x1000
NPIX = HV * VV
VARIANT = os.environ.get("VGA_VARIANT", "g2d")
BURST = int(os.environ.get("VGA_BURST", "15"))
MODE = os.environ.get("VGA_MODE", "stall")
# latency mode: the R channel stalls LAT_STALL of every LAT_PERIOD cycles (memory latency under load). The scan-out
# needs one 64-bit beat every 4 cycles here; the original axi_vga bridges only ~3 beats (~12 cycles) at a burst
# boundary, the prefetching copy ~3/4 of its buffer.
LAT_STALL, LAT_PERIOD = 100, 300
ARB_PERIOD = 24
DBG = 0x40      # v1.8 debug counter window of the fork


def fb_pixel(i: int) -> int:
    return (i * 37 + 11) & 0xFFFF or 1


async def reg_read(dut, addr: int) -> tuple[int, int]:
    await FallingEdge(dut.clk)
    dut.reg_valid.value = 1
    dut.reg_write.value = 0
    dut.reg_addr.value = addr
    while True:
        await RisingEdge(dut.clk)
        if int(dut.reg_ready.value):
            data, err = int(dut.reg_rdata.value), int(dut.reg_error.value)
            break
    await FallingEdge(dut.clk)
    dut.reg_valid.value = 0
    return data, err


async def reg_write(dut, addr: int, data: int):
    await FallingEdge(dut.clk)
    dut.reg_valid.value = 1
    dut.reg_write.value = 1
    dut.reg_addr.value = addr
    dut.reg_wdata.value = data
    while True:
        await RisingEdge(dut.clk)
        if int(dut.reg_ready.value):
            break
    await FallingEdge(dut.clk)
    dut.reg_valid.value = 0


@cocotb.test()
async def vga_resync(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    dut.reg_valid.value = 0
    dut.reg_write.value = 0
    dut.reg_addr.value = 0
    dut.reg_wdata.value = 0
    ram = AxiRam(AxiBus.from_prefix(dut, "m_axi"), dut.clk, dut.rst_n, reset_active_level=False, size=1 << 16)
    stall = [False]

    def pauses():
        while True:
            yield stall[0]
    ram.read_if.r_channel.set_pause_generator(pauses())
    if MODE == "arb":
        def ar_grant():
            while True:
                yield from [True] * (ARB_PERIOD - 1)
                yield False
        ram.read_if.ar_channel.set_pause_generator(ar_grant())
    arlens = []

    async def watch_ar():
        while True:
            await RisingEdge(dut.clk)
            if int(dut.m_axi_arvalid.value) and int(dut.m_axi_arready.value):
                arlens.append(int(dut.m_axi_arlen.value))
    cocotb.start_soon(watch_ar())
    ram.write(FB, b"".join(fb_pixel(i).to_bytes(2, "little") for i in range(NPIX)))
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    for off, val in ((0x04, 1), (0x08, HV), (0x0C, HF), (0x10, HS), (0x14, HB), (0x18, VV), (0x1C, VF), (0x20, VS),
                     (0x24, VB), (0x28, FB), (0x2C, 0), (0x30, NPIX * 2), (0x34, BURST)):
        await reg_write(dut, off, val)
    if VARIANT == "g2d":
        info, err = await reg_read(dut, DBG + 0x14)
        assert not err and info & 0xFFFF == 256, hex(info)
        burst_beats = info >> 16
        _, err = await reg_read(dut, 0x80)
        assert err, "offsets above the counter window must answer with an error"
        vend, err = await reg_read(dut, 0x08)
        assert not err and vend == HV, "the vendor registers must not alias the counter window"
    await reg_write(dut, 0x00, 1)          # enable, negative syncs (the sampling loop starts right after it)

    frames, cur, prev_vis = [], [], 0
    want = [fb_pixel(i) for i in range(NPIX)]
    line = HV + HF + HS + HB
    stall_from, stall_to = 4 * line * (VV + VF + VS + VB), 6 * line * (VV + VF + VS + VB)
    for cyc in range(12 * line * (VV + VF + VS + VB)):
        await RisingEdge(dut.clk)
        if MODE == "latency":
            stall[0] = cyc % LAT_PERIOD < LAT_STALL
        elif MODE == "arb":
            stall[0] = False                    # only the AR channel is throttled
        else:
            stall[0] = stall_from <= cyc < stall_to
        vis = int(dut.visible.value)
        if vis:
            cur.append(int(dut.pixel.value) if dut.pixel.value.is_resolvable else -1)
        if len(cur) == NPIX:
            frames.append(cur)
            cur = []
            if VARIANT == "g2d" and len(frames) == 1:
                # clear the counters after the startup frame; in its own coroutine so no pixel sample is missed
                cocotb.start_soon(reg_write(dut, DBG, 0))
        prev_vis = vis
    ok = [f == want for f in frames]
    for n, f in enumerate(frames[:4]):
        bad = [i for i in range(NPIX) if f[i] != want[i]]
        if bad:
            shift = [k for k in range(-NPIX + 1, NPIX) if all(f[i] == want[i - k] for i in range(max(k, 0), min(NPIX, NPIX + k)) if f[i] != 0)]
            dut._log.info(f"frame {n}: {len(bad)} wrong, first {bad[0]} got {f[bad[0]]:#x} want {want[bad[0]]:#x}, "
                          f"zeros {f.count(0)}, consistent shifts {shift[:5]}")
    dut._log.info(f"variant {VARIANT}, burst {BURST + 1}, mode {MODE}: {len(frames)} frames, bit-exact: {ok}")
    dut._log.info(f"AR lengths seen: max {max(arlens) + 1 if arlens else 0} beats, {len(arlens)} ARs")
    if VARIANT == "g2d":
        upx, ufr, minfill, lat, nfr = [(await reg_read(dut, DBG + 4 * i))[0] for i in range(5)]
        dut._log.info(f"counters: underrun px {upx}, frames {ufr}, min fill {minfill}, max lat {lat}, frames {nfr}")
        want_burst = min(BURST + 1, burst_beats)
        assert max(arlens) + 1 == want_burst, f"bursts of {max(arlens) + 1} beats, want {want_burst}"
        wrong = sum(1 for o in ok[1:] if not o)
        if wrong == 0:
            assert upx == 0 and ufr == 0, (upx, ufr)
        else:
            assert upx > 0 and ufr >= 1, (upx, ufr, ok)
        assert nfr >= len(frames) - 2 and lat > 0
    else:
        assert max(arlens) == 0, "the original axi_vga splits every burst into one-beat ARs"
    if MODE == "arb":
        if VARIANT == "g2d":
            assert len(frames) >= 10 and all(ok[1:]), ok
        else:
            assert not all(ok[1:]), "one-beat ARs kept up with the shared AR channel (the bug did not show)"
        return
    if MODE == "latency":
        # every frame after the first (startup) must be bit-exact; the original is only logged (it shows the bug)
        if VARIANT == "g2d":
            assert len(frames) >= 10 and all(ok[1:]), ok
        return
    clean_before = [ok[i] for i in range(1, 3)]
    clean_after = [ok[i] for i in range(7, len(frames))]
    if VARIANT == "g2d":
        assert all(clean_before) and all(clean_after), ok
    else:
        assert not all(clean_after), "the original axi_vga re-aligned after the stall (the bug did not show)"


@pytest.mark.parametrize("mode", ["stall", "latency", "arb"])
@pytest.mark.parametrize("variant", ["g2d", "orig"])
@pytest.mark.parametrize("burst", [15, 255])
def test_axi_vga(variant, burst, mode):
    from cocotb_tools.runner import get_runner
    sys.path.insert(0, str(ROOT / "fpga" / "sim"))
    import socsim
    flist = socsim.cheshire_flist(orig_vga=True)
    vga_orig = [a for a in flist if a.endswith(socsim.VGA_ORIG)]
    files, incs = [], []
    for a in flist:
        if a.startswith("+incdir+"):
            incs.append(a[len("+incdir+"):])
        elif a.startswith("+define+") or a.startswith("-"):
            continue
        elif a.endswith(socsim.VGA_ORIG) and variant == "g2d":
            continue
        else:
            files.append(a)
    if variant == "g2d":
        files += [str(ROOT / f) for f in socsim.VGA_G2D_DEPS + socsim.VGA_G2D]
    assert vga_orig, "axi_vga not in Cheshire's file list"
    files.append(str(ROOT / "dv/tb/tb_axi_vga.sv"))
    build = ROOT / "build" / "dv" / f"tb_axi_vga_{variant}_{burst}_{mode}"   # one per run: no sharing in parallel
    r = get_runner("verilator")
    r.build(sources=files, includes=incs, hdl_toplevel="tb_axi_vga", build_dir=build,
            build_args=["--timing", "-Wno-fatal", "-Wno-lint", "-Wno-style", "-Wno-UNOPTFLAT", "-Wno-MULTIDRIVEN",
                        "-Wno-TIMESCALEMOD", "-Wno-SELRANGE", "-Wno-ZERODLY", "-Wno-SYMRSVDWORD", "-Wno-ZEROREPL"],
            timescale=("1ns", "1ps"))
    r.test(hdl_toplevel="tb_axi_vga", test_module="test_axi_vga", build_dir=build,
           test_dir=str(Path(__file__).parent), extra_env={"VGA_VARIANT": variant, "VGA_BURST": str(burst), "VGA_MODE": mode})
