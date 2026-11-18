# SPDX-License-Identifier: Apache-2.0
"""Shared helpers of the L3 (g2d_top) benches: configuration from the runner parameters, environment
construction, a solid rect fill with its full state, random rects and alpha settings."""

import os
import random

from g2d_cmd import FMT, STATE, TGT_FMTS, CmdBuf, state_word, YUV_FMTS
from g2d_env import G2dEnv

P = {k[6:]: int(v) for k, v in os.environ.items() if k.startswith("G2D_P_")}
DATA_W = P.get("DATA_W", 64)
ADDR_W = P.get("ADDR_W", 32)
KICK_DEPTH = P.get("KICK_DEPTH", 4)
MAX_BURST = P.get("MAX_BURST_BYTES", 256)
BASE_HI = int(os.environ.get("G2D_BASE_HI", "0"))
QUICK = os.environ.get("G2D_QUICK") == "1"
SEED = int(os.environ.get("SEED", "1"))
FMTS = list(TGT_FMTS)           # target formats (also sources)
SRC_FMTS = [f for f in FMT.values() if f not in YUV_FMTS]   # every one-plane source format (YUV: yuv01)


async def make_env(dut, name, profile=None, seed_off=0):
    env = G2dEnv(dut, data_w=DATA_W, addr_w=ADDR_W, kick_depth=KICK_DEPTH, profile=profile,
                 rng=random.Random(SEED * 1000 + seed_off), base_hi=BASE_HI)
    env.test_name = name
    await env.start()
    return env


def fill(cb, t, rect, color, alpha=(0, 255), clip=None, blend=0, paint=0, dalpha=(0, 255), dither=False):
    """One solid rect fill with its full state (target, scissor, blend/alpha/paint/colour). alpha / dalpha:
    (mode, value) of the source / destination global alpha."""
    cb.target(t.addr, t.w, t.h, t.fmt, t.stride, dither=dither)
    x0, y0, x1, y1 = clip if clip is not None else (0, 0, t.w, t.h)
    cb.clip(x0, y0, x1, y1)
    cb.load_state(STATE["BLEND"].addr, [blend, state_word("ALPHA_CTRL", SRC_MODE=alpha[0], SRC_VALUE=alpha[1],
                                                          DST_MODE=dalpha[0], DST_VALUE=dalpha[1]), paint, color])
    cb.draw_rect(*rect)
    return cb


def rand_rect(rng, t, margin=8):
    x0 = rng.randint(-margin, t.w + margin)
    y0 = rng.randint(-margin, t.h + margin)
    return (x0, y0, x0 + rng.randint(0, t.w + margin), y0 + rng.randint(0, t.h + margin))


def rand_alpha(rng):
    return (rng.choice([0, 0, 1, 2]), rng.getrandbits(8))


async def run(env, cb, check=True, **kw):
    await env.kick(cb)
    await env.wait_done()
    if check:
        await env.check(**kw)
