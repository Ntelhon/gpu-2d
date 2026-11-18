# SPDX-License-Identifier: Apache-2.0
"""L3 smoke set through every per-bus wrapper rtl/wrap/generated/g2d_top_<bus> (v1.9, F-6, verification-plan.md
ADP-08): the full G2D with its configuration port behind each adapter runs a subset of test_top.py (reset values of
every CSR, IRQ, fills in all formats, random command streams, memory QoS) with the CSR accesses on that bus
(dv/common/csr_if.py G2D_CSR_BUS). Results are checked bit-exact against the golden model as in test_top.py."""

import pytest

BUSES = ["axi", "apb", "ahb", "obi", "regintf", "wb", "tlul"]
TESTS = ["csr01_top_reset_values", "csr05_top_irq", "fil01_all_formats", "random_streams", "mem_qos_system"]


@pytest.mark.parametrize("bus", BUSES)
def test_wrap(bus):
    import runner
    params = dict(DATA_W=64)
    env = {"G2D_CSR_BUS": bus, "G2D_QUICK": "1"}
    if bus == "regintf":
        params["CFG_ASYNC"] = 1          # one bus behind the configuration-port clock crossing as well
        params["MEM_ASYNC"] = 1
        env["G2D_ASYNC"] = "1"
    runner.run(f"g2d_top_{bus}_tb", "test_top", params=params, tag=bus, testcase=TESTS, extra_env=env,
               loops={"s_ahb_hready_i": "s_ahb_hreadyout_o"} if bus == "ahb" else None)
