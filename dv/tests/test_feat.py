# SPDX-License-Identifier: Apache-2.0
"""v1.7 optional units (parameter FEATURES, HWCFG.FEAT_*, rendering.md §13.1): a build with every unit and one
without any. Per unit one draw that needs it: drawn bit-exactly when the unit is built, BAD_STATE (nothing written,
the model agrees) when it is not; HWCFG reports the units; a fill and a blit work in both builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g2d_cmd import FMT, STATE, CmdBuf, state_word  # noqa: E402
from g2d_env import CsrIf  # noqa: E402
from g2d_top_util import FMTS, make_env, run  # noqa: E402
from test_blit import blit, mask_state, new_yuv  # noqa: E402

FEATS = int(os.environ.get("G2D_P_FEATURES", "63"))
NAMES = ("family", "gauss", "yuv", "color", "radial", "mask")


@cocotb.test()
async def feat01_optional_units(dut):
    env = await make_env(dut, "feat01")
    rng = env.rng
    hw = await env.csr.read("HWCFG")
    assert (hw >> 26) & 0x3F == FEATS, f"HWCFG.FEAT_* {hw >> 26:#x} != {FEATS:#x}"
    ident = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def case(name, cb, t, s):
        if name == "family":
            blit(cb, t, s, ident, blend=1 | (1 << 4))                     # OpenVG SRC_OVER
        elif name == "gauss":
            blit(cb, t, s, ident, gauss=(1024, 512, 256))
        elif name == "yuv":
            ys, planes = new_yuv(env, rng, FMT["NV12"], 8, 6)
            blit(cb, t, ys, ident, planes=planes)
        elif name == "color":
            blit(cb, t, s, ident, cunit=(1, 1, [256 if i % 5 == 0 else 0 for i in range(16)], [0] * 4))
        elif name == "radial":
            cb.target(t.addr, t.w, t.h, t.fmt, t.stride)
            cb.clip(0, 0, t.w, t.h)
            ramp = env.new_target(FMT["BGRA8888"], 256, 1)
            cb.load_state(STATE["GRAD_RAMP_ADDR"].addr, [ramp.addr])
            cb.load_state(STATE["BLEND"].addr, [0, 0xFFFF00, state_word("PAINT_CTRL", SRC=3), 0xFF00FF00])
            cb.draw_rect(0, 0, t.w, t.h)
        else:                                                              # mask
            m = env.new_target(FMT["A8"], 8, 6)
            mask_state(cb, m.addr, m.stride, m.w, m.h, 1, 1)
            blit(cb, t, s, ident)
            mask_state(cb, m.addr, m.stride, m.w, m.h, 0, 0, en=False)

    for bit, name in enumerate(NAMES):
        t = env.new_target(rng.choice(FMTS), 12, 8)
        s = env.new_target(FMT["BGRA8888"], 8, 6)
        cb = CmdBuf()
        case(name, cb, t, s)
        cb.fence(bit)
        await run(env, cb)
        st = await env.csr.read("STATUS")
        halted = CsrIf.field("STATUS", "HALTED", st)
        assert halted == (0 if FEATS >> bit & 1 else 1), f"{name}: HALTED {halted} with FEATURES {FEATS:#x}"
        if halted:
            await env.soft_reset()
        await env.check()
    # the base functions in every build
    t = env.new_target(FMT["BGRA8888"], 16, 8)
    s = env.new_target(FMT["RGB565"], 9, 7)
    cb = CmdBuf()
    blit(cb, t, s, [[1.5, 0, 1], [0, 1, 0.5], [0, 0, 1]], bilinear=True, blend=1)
    cb.fence(9)
    await run(env, cb)
    assert CsrIf.field("STATUS", "HALTED", await env.csr.read("STATUS")) == 0


CONFIGS = {
    "full": (dict(DATA_W=64), {}),
    "none": (dict(DATA_W=64, FEATURES=0), {}),
}

import pytest  # noqa: E402


@pytest.mark.parametrize("cfg", list(CONFIGS))
def test_feat(cfg):
    import runner
    params, env = CONFIGS[cfg]
    runner.run("g2d_top_tb", "test_feat", params=params, tag=f"feat_{cfg}", extra_env=env)
