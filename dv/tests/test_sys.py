# SPDX-License-Identifier: Apache-2.0
"""System tests of g2d_top against the golden model (verification-plan.md SYS-01..05, Phase 5).
Each check compares the whole memory image, the error / fence / IRQ registers, the white-box state and
(where the AXI model can count them) the performance counters (dv/common/g2d_env.py), with every dv/sva
checker bound.

  SYS-01  random command streams of 1..200 commands: fills (blended, clipped), blits of every kind
          (affine kinds, perspective, point / bilinear, image options), paths (solid, linear / radial gradient
          (v1.5), image paint (v1.5), AA and
          not, both fill rules), fences with and without IRQ, NOPs, state noise, misaligned-ramp draws
          (BAD_STATE, predicted by the model), several targets and formats, 1..6 kicks per stream
  SYS-02  a fixed grid of AXI memory profiles (latency, back-pressure, reordering, interleaving, slow
          B / R) on SYS-01 streams
  SYS-03  soft or hard reset at a random point of a running stream; the targets of that stream are
          excluded from the compare (partly drawn); the next stream is exact
  SYS-04  errors mid-stream: malformed command and invalid state (all causes of test_top, predicted
          exactly by the model), window violation (predicted), AXI write error on a target, read errors
          on a source image, an edge list and a command buffer (error bit, halt and error address
          checked, the stream's targets excluded); soft-reset recovery and an exact stream afterwards
  SYS-05  DATA_W 64 vs 128: the SYS-01 memory image of a seed is identical in both configurations
          (test_sys05_same, after test_sys of both configurations ran with that seed)
SYS-06 (LVGL replay) needs Phase 6.
"""

import hashlib
import os
import random
from pathlib import Path

import cocotb

from axi_mem import ErrRange, Profile
from csr_if import CsrIf
from g2d_cmd import REGMAP, STATE, CmdBuf
from g2d_env import ERR
from g2d_top_util import FMTS, QUICK, SEED, fill, make_env, rand_alpha, rand_rect
from test_blit import KINDS, blit, mask_state, mat_affine, mat_persp
from test_path import (bbox_of, draw_path, grad_coeffs, grad_state, radial_coeffs, radial_state, rand_poly, rand_ramp,
                       ramp_bytes)
from g2d_cmd import edges_to_bytes
from test_top import bad_state_cases, malformed_cases

ROOT = Path(__file__).resolve().parents[2]
NOISE_IDX = sorted({v.addr for v in STATE.values()})


# ------------------------------------------------------------------------------------------------
# stream generator
# ------------------------------------------------------------------------------------------------

def new_buffers(env, rng, n=4):
    return [env.new_target(rng.choice(FMTS), rng.randint(1, 64), rng.randint(1, 32)) for _ in range(n)]


def add_cmd(env, cb, rng, bufs, safe=False):
    """Append one random command (with all the state it uses) to cb. safe: no command that can raise an
    error (no state noise, no BAD_STATE draw)."""
    r = rng.random() * (0.95 if safe else 1.0)
    t, s = rng.sample(bufs, 2)
    if rng.random() < 0.08:              # v1.6: switch the mask layer on (random buffer and origin) or off
        mw, mh = rng.randint(1, 48), rng.randint(1, 32)
        maddr = env.place(bytes(rng.getrandbits(8) for _ in range(mw * mh)))
        mask_state(cb, maddr, mw, mw, mh, rng.randint(-mw, 64), rng.randint(-mh, 32), en=rng.random() < 0.6)
    if r < 0.22:
        clip = None
        if rng.random() < 0.3:
            clip = (rng.randint(0, t.w), rng.randint(0, t.h), rng.randint(0, t.w + 4), rng.randint(0, t.h + 4))
        fill(cb, t, rand_rect(rng, t), rng.getrandbits(32), rand_alpha(rng), clip=clip,
             blend=rng.choice([0, 0, rng.randint(1, 15)]), dalpha=rand_alpha(rng))
    elif r < 0.55:
        m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
        blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, rng.randint(0, 15)]),
             color=rng.getrandbits(32), alpha=rand_alpha(rng), dalpha=rand_alpha(rng), img_mul=rng.random() < 0.2,
             premul=rng.random() < 0.2, opaque=rng.random() < 0.2)
    elif r < 0.8:
        paint = 0
        kind = rng.random()
        if kind < 0.12:          # v1.5 (F-1): image paint on the path
            edges = rand_poly(rng, t.w / 2, t.h / 2, max(t.w, t.h) * 0.7, rng.randint(3, 40))
            m = mat_persp(rng, s, t) if rng.random() < 0.2 else mat_affine(rng, s, t, rng.choice(KINDS))
            blit(cb, t, s, m, bilinear=rng.random() < 0.5, blend=rng.choice([0, rng.randint(0, 15)]),
                 color=rng.getrandbits(32), alpha=rand_alpha(rng), dalpha=rand_alpha(rng), img_mul=rng.random() < 0.2,
                 premul=rng.random() < 0.2, wrap=rng.randint(0, 4), border=rng.getrandbits(32),
                 path=(env.place(edges_to_bytes(edges)), len(edges), bbox_of(edges), rng.random() < 0.7,
                       rng.random() < 0.4))
            return
        if kind < 0.35:
            ramp = env.place(ramp_bytes(rand_ramp(rng)))
            p, q, rr = grad_coeffs(rng, t, rng.choice(["0", "90", "45", "other"]), rng.choice([3, 40, 400]))
            paint = grad_state(cb, p, q, rr, ramp, rng.randrange(3))
        elif kind < 0.5:         # v1.5 (F-4): radial gradient
            ramp = env.place(ramp_bytes(rand_ramp(rng)))
            paint = radial_state(cb, *radial_coeffs(rng, t, rng.choice(["centred", "focal", "outside", "zero"])),
                                 ramp, rng.randrange(4))
        draw_path(env, cb, t, rand_poly(rng, t.w / 2, t.h / 2, max(t.w, t.h) * 0.7, rng.randint(3, 40)),
                  aa=rng.random() < 0.7, eo=rng.random() < 0.4, color=rng.getrandbits(32),
                  blend=rng.choice([0, rng.randint(0, 15)]), alpha=rand_alpha(rng), dalpha=rand_alpha(rng),
                  paint=paint)
    elif r < 0.88:
        cb.fence(rng.getrandbits(32), irq=rng.random() < 0.3)
    elif r < 0.95:
        cb.nop(rng.randint(0, 4), fill=rng.getrandbits(32))
    elif r < 0.99:
        # state noise: every draw generator rewrites the state it uses, so this only shows up if a draw
        # depended on a register it did not set (or if LOAD_STATE itself misbehaves)
        i = rng.choice(NOISE_IDX)
        cb.load_state(i, [rng.getrandbits(32) for _ in range(min(rng.randint(1, 4), REGMAP.state_size - i))])
    else:
        cb.load_state(STATE["GRAD_RAMP_ADDR"].addr, [0x0100_0002])   # misaligned ramp: BAD_STATE
        fill(cb, t, rand_rect(rng, t), 0, paint=2)


def gen_stream(env, rng, bufs, n_cmds):
    """n_cmds commands split into 1..6 command buffers."""
    kicks, cb = [], CmdBuf()
    per = max(1, n_cmds // rng.randint(1, 6))
    for i in range(n_cmds):
        add_cmd(env, cb, rng, bufs)
        if (i + 1) % per == 0:
            kicks.append(cb)
            cb = CmdBuf()
    if cb.words:
        kicks.append(cb)
    return kicks


async def run_stream(env, kicks):
    """Kick, wait, compare everything; recover from a (predicted) halt."""
    for cb in kicks:
        if await env.kick(cb) is None:
            break
    await env.wait_done()
    await env.check()
    if CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")):
        await env.soft_reset()
        await env.check()
    await env.csr.write("IRQ_STATUS", 0b111)
    env.model.clear_irq_fence()


def mask_all(env, bufs):
    for t in bufs:
        env.mask.append((t.addr, t.addr + t.nbytes - 1))


# ------------------------------------------------------------------------------------------------
# SYS-01 / SYS-05
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def sys01_random_streams(dut):
    rng = random.Random(SEED * 7919 + 1)
    env = await make_env(dut, "sys01", profile=Profile.random(rng, max_lat=60))
    all_bufs = []
    for _ in range(1 if QUICK else 3):
        env.axi.profile = Profile.random(rng, max_lat=rng.choice([0, 20, 120]))
        bufs = new_buffers(env, rng)
        all_bufs += bufs
        await run_stream(env, gen_stream(env, rng, bufs, rng.randint(1, 40 if QUICK else 200)))
    out = os.environ.get("G2D_SYS_DIGEST")
    if out:
        h = hashlib.sha256()
        for t in all_bufs:
            h.update(env.read_dut(t.addr, t.nbytes))
        Path(out).write_text(h.hexdigest() + "\n")


# ------------------------------------------------------------------------------------------------
# SYS-02
# ------------------------------------------------------------------------------------------------

GRID = {
    "ideal": Profile.ideal(),
    "lat200": Profile(r_lat=(150, 200), b_lat=(150, 200)),
    "ready30": Profile(p_awready=0.3, p_wready=0.3, p_arready=0.3, p_rvalid=0.3, p_bvalid=0.3),
    "reorder": Profile(r_lat=(0, 60), b_lat=(0, 60), reorder=True, interleave=True),
    "slow_b": Profile(b_lat=(200, 300), p_bvalid=0.5),
    "slow_r": Profile(r_lat=(100, 200), p_rvalid=0.5),
    "w_backpressure": Profile(p_wready=0.1, p_awready=0.5),
    "ready_after_valid": Profile(ready_after_valid=True, p_awready=0.6, p_wready=0.6, p_arready=0.6,
                                 r_lat=(0, 40), b_lat=(0, 40)),
    "mixed": Profile(p_awready=0.7, p_wready=0.7, p_arready=0.7, p_rvalid=0.8, p_bvalid=0.8, r_lat=(0, 90),
                     b_lat=(0, 90), reorder=True, interleave=True),
}


@cocotb.test()
async def sys02_profile_grid(dut):
    rng = random.Random(SEED * 7919 + 2)
    env = await make_env(dut, "sys02")
    for name in (rng.sample(sorted(GRID), 2) if QUICK else sorted(GRID)):
        env.axi.profile = GRID[name]
        dut._log.info(f"SYS-02 profile {name}")
        await run_stream(env, gen_stream(env, rng, new_buffers(env, rng, 3), rng.randint(5, 15 if QUICK else 40)))


# ------------------------------------------------------------------------------------------------
# SYS-03
# ------------------------------------------------------------------------------------------------

@cocotb.test()
async def sys03_resets(dut):
    rng = random.Random(SEED * 7919 + 3)
    env = await make_env(dut, "sys03", profile=Profile.random(rng, max_lat=60))
    env.perf_checked = False
    for it in range(2 if QUICK else 6):
        bufs = new_buffers(env, rng)
        for cb in gen_stream(env, rng, bufs, rng.randint(10, 60)):
            if await env.kick(cb, to_model=False) is None:
                break
        await env.cycles(rng.randint(10, 4000))
        hard = rng.random() < 0.5 if not QUICK else it == 1
        if hard:
            await env.hard_reset(rng.randint(1, 5))
        else:
            await env.soft_reset()
        mask_all(env, bufs)
        await env.check()
        await run_stream(env, gen_stream(env, rng, new_buffers(env, rng, 3), rng.randint(5, 30)))


# ------------------------------------------------------------------------------------------------
# SYS-04
# ------------------------------------------------------------------------------------------------

AXI_KINDS = ["axi_wr", "axi_rd_src", "axi_rd_edge", "axi_rd_cmd"]
PRED_KINDS = ["decode", "bad_state", "window"]


async def predicted_error(env, rng, bufs, kind):
    """A stream that halts on an error the model predicts exactly."""
    cb = CmdBuf()
    for _ in range(rng.randint(0, 5)):
        add_cmd(env, cb, rng, bufs)
    if kind == "decode":
        name, words = rng.choice(malformed_cases())
        cb.words += words
    elif kind == "bad_state":
        name, build = rng.choice(bad_state_cases(env, rng))
        build(cb)
    else:
        name = "window"
        await env.set_window(0x0000_0000, 0x0800_0000 - 1)    # targets live at 0x1000_0000 and above
        fill(cb, bufs[0], (0, 0, bufs[0].w, bufs[0].h), rng.getrandbits(32))
    for _ in range(rng.randint(0, 5)):
        add_cmd(env, cb, rng, bufs)                           # never executed
    env.dut._log.info(f"SYS-04 {kind}: {name}")
    await run_stream(env, [cb])
    await env.set_window(0, 0xFFFF_FFFF)


async def axi_error(env, rng, bufs, kind):
    """A stream that hits an AXI error response; the model cannot predict where the stream stops, so the
    error class, halt and error address are checked and the stream's targets are excluded."""
    cb = CmdBuf()
    for _ in range(rng.randint(0, 4)):
        add_cmd(env, cb, rng, bufs, safe=True)
    victim, other = rng.sample(bufs, 2)
    resp = rng.choice([2, 3])
    if kind == "axi_wr":
        lo, hi = victim.addr, victim.addr + victim.nbytes - 1
        fill(cb, victim, (0, 0, victim.w, victim.h), rng.getrandbits(32))
        env.axi.err_ranges = [ErrRange(env.off + lo, env.off + hi, resp, on_read=False)]
        exp = ERR["AXI_WR"]
    elif kind == "axi_rd_src":
        lo, hi = victim.addr, victim.addr + victim.nbytes - 1
        blit(cb, other, victim, [[1, 0, 0], [0, 1, 0], [0, 0, 1]])     # identity: reads texel (0, 0)
        env.axi.err_ranges = [ErrRange(env.off + lo, env.off + hi, resp, on_write=False)]
        exp = ERR["AXI_RD"]
    elif kind == "axi_rd_edge":
        lo = env.next_cmd                                              # draw_path places the edges here
        edges = rand_poly(rng, victim.w / 2, victim.h / 2, max(victim.w, victim.h) * 0.6, rng.randint(3, 20))
        draw_path(env, cb, victim, edges)
        hi = lo + 16 * len(edges) - 1
        env.axi.err_ranges = [ErrRange(env.off + lo, env.off + hi, resp, on_write=False)]
        exp = ERR["AXI_RD"]
    for _ in range(rng.randint(0, 4)):
        add_cmd(env, cb, rng, bufs, safe=True)
    if not cb.words:                    # every command was a rejected blit (emits nothing)
        fill(cb, other, (0, 0, other.w, other.h), rng.getrandbits(32))
    data = cb.to_bytes()
    addr = env.place(data)
    if kind == "axi_rd_cmd":
        lo = addr + rng.randrange(0, len(data), 8)
        hi = addr + len(data) - 1
        env.axi.err_ranges = [ErrRange(env.off + lo, env.off + hi, resp, on_write=False)]
        exp = ERR["AXI_RD"]
    env.dut._log.info(f"SYS-04 {kind}: error range 0x{lo:08x}..0x{hi:08x}")
    env.axi.err_bursts = []
    await env.csr.write("CMD_ADDR", addr)
    await env.csr.write("CMD_KICK", len(data))
    await env.wait_done()
    st = await env.csr.read("ERR_STATUS")
    ea = await env.csr.read("ERR_ADDR")
    eca = await env.csr.read("ERR_CMD_ADDR")
    assert st & exp, f"{kind}: ERR_STATUS 0x{st:x} lacks 0x{exp:x}"
    assert CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")), f"{kind}: not halted"
    # ERR_ADDR: start of a burst the memory answered with an error (regs.yaml: first faulting transaction)
    # ERR_ADDR (arch.md §8): the first requested byte of a burst the memory answered with an error
    errs = [(a - env.off, n, rd) for a, n, rd in env.axi.err_bursts]
    assert any(a <= ea < a + n for a, n, rd in errs), \
        f"{kind}: ERR_ADDR 0x{ea:08x} not in an erroring burst ({[(hex(a), n) for a, n, _ in errs[:6]]})"
    # ERR_CMD_ADDR: the parser position, just past the buffer when it consumed all of it (arch.md §8)
    assert addr <= eca <= addr + len(data), \
        f"{kind}: ERR_CMD_ADDR 0x{eca:08x} outside the buffer 0x{addr:08x}..0x{addr + len(data) - 1:08x}"
    env.axi.err_ranges = []
    mask_all(env, bufs)
    await env.soft_reset()
    await env.check(perf=False)


@cocotb.test()
async def sys04_errors(dut):
    rng = random.Random(SEED * 7919 + 4)
    env = await make_env(dut, "sys04", profile=Profile.random(rng, max_lat=40))
    env.perf_checked = False
    kinds = PRED_KINDS + AXI_KINDS
    for kind in (rng.sample(PRED_KINDS, 1) + rng.sample(AXI_KINDS, 2) if QUICK else rng.sample(kinds, len(kinds))):
        bufs = new_buffers(env, rng)
        await run_stream(env, gen_stream(env, rng, bufs, rng.randint(1, 15)))     # exact lead-in
        if kind in PRED_KINDS:
            await predicted_error(env, rng, bufs, kind)
        else:
            await axi_error(env, rng, bufs, kind)
        await run_stream(env, gen_stream(env, rng, new_buffers(env, rng, 3), rng.randint(3, 20)))


# ------------------------------------------------------------------------------------------------
# pytest entry points
# ------------------------------------------------------------------------------------------------

import pytest  # noqa: E402

from test_top import CONFIGS  # noqa: E402  (same builds as test_top)


def digest_file(cfg):
    return ROOT / "build" / "dv" / f"sys05_{cfg}_s{os.environ.get('SEED', '1')}.txt"


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_sys(cfg):
    import runner
    params, env = CONFIGS[cfg]
    digest_file(cfg).unlink(missing_ok=True)
    runner.run("g2d_top_tb", "test_sys", params=params, tag=cfg,
               extra_env={**env, "G2D_QUICK": "0" if os.environ.get("G2D_FULL") == "1" else "1",
                          "G2D_SYS_DIGEST": str(digest_file(cfg))})


def test_sys05_same():
    """SYS-05: identical SYS-01 memory images at DATA_W 64 and 128 for the same seed."""
    d = {c: digest_file(c).read_text() for c in CONFIGS}
    assert len(set(d.values())) == 1, d      # every build, the async ones included, writes the same memory
