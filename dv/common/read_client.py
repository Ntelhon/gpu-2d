# SPDX-License-Identifier: Apache-2.0
"""Read-engine side of a g2d read client (arch.md §4.3) for block benches: accepts requests
(byte address, length - 1) with random ready, and returns the DATA_W-aligned beats covering each
request in order, after a random latency and with random gaps. Checks that the client accepts every beat
(credit-based clients never back-pressure R).

    rc = ReadResponder(dut, mem_read, data_w, rng, lat=(0, 40), prefix="rd")

mem_read(addr, n) -> bytes. Signals: <prefix>_req_valid_o/_ready_i/_addr_o/_len_o,
<prefix>_rsp_valid_i/_ready_o/_data_i, and <prefix>_rsp_strb_i if the client has it (byte mask of the
requested bytes in each beat, as g2d_axi_rd delivers).
"""

from __future__ import annotations

from collections import deque

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge


class ReadResponder:
    def __init__(self, dut, mem_read, data_w, rng, lat=(0, 40), p_gap=0.2, p_req=0.8, prefix="rd"):
        self.dut, self.mem_read, self.rng = dut, mem_read, rng
        self.db = data_w // 8
        self.lat, self.p_gap, self.p_req = lat, p_gap, p_req
        self.p = prefix
        self.requests = []          # (addr, len) of every accepted request
        self.beats = 0
        self.drop = False           # stop answering (soft-reset tests)
        self.has_strb = hasattr(dut, f"{prefix}_rsp_strb_i")
        cocotb.start_soon(self._run())

    def sig(self, name):
        return getattr(self.dut, f"{self.p}_{name}")

    async def _run(self):
        q = deque()
        cyc = 0
        while True:
            self.sig("req_ready_i").value = int(self.rng.random() < self.p_req)
            if q and cyc >= q[0][0] and self.rng.random() >= self.p_gap:
                self.sig("rsp_valid_i").value = 1
                self.sig("rsp_data_i").value = q[0][1]
                if self.has_strb:
                    self.sig("rsp_strb_i").value = q[0][2]
            else:
                self.sig("rsp_valid_i").value = 0
            await ReadOnly()
            if int(self.sig("req_valid_o").value) and int(self.sig("req_ready_i").value):
                a, ln = int(self.sig("req_addr_o").value), int(self.sig("req_len_o").value)
                self.requests.append((a, ln))
                if not self.drop:
                    t = cyc + self.rng.randint(*self.lat)
                    first, last = a // self.db, (a + ln) // self.db
                    for b in range(first, last + 1):
                        raw = self.mem_read(b * self.db, self.db)
                        strb = sum(1 << i for i in range(self.db) if a <= b * self.db + i <= a + ln)
                        q.append((t, int.from_bytes(raw, "little"), strb))
            if int(self.sig("rsp_valid_i").value):
                assert int(self.sig("rsp_ready_o").value) == 1, "client back-pressured R"
                q.popleft()
                self.beats += 1
            await RisingEdge(self.dut.clk_i)
            cyc += 1
