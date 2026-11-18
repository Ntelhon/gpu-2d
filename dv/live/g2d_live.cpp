// SPDX-License-Identifier: Apache-2.0
// G2D HAL, live RTL backend (Phase 6, backend "RTL live"): the unchanged vg_lite driver runs on a Verilator
// model of g2d_top. This file is the whole testbench:
//   - AXI4-Lite master for the CSR accesses of the HAL (reg_read / reg_write),
//   - AXI4 memory slave over the program's memory, with random latency and back-pressure on every channel,
//     out-of-order completion across IDs (in order per ID) and optional read-beat interleaving,
//   - memory management identical to the software backend (sw/hal/model): one arena at G2D_MODEL_BASE with
//     the same first-fit allocator, or identity mode (G2D_MODEL_IDENTITY=1: GPU address = host pointer, LVGL),
//     so a program produces the same buffers on both backends and its outputs compare byte for byte.
// Time only advances inside HAL calls: CSR accesses take their AXI4-Lite cycles, wait() runs the clock until
// the interrupt rises or the timeout passes (G2D_LIVE_CYCLES_PER_US cycles per microsecond, default 100).
// The dv/sva checkers are bound into the RTL; a failing assertion or an AXI protocol problem seen here
// ends the program with exit code 3.
//
// Environment: G2D_LIVE_SEED (random profile and timing), G2D_LIVE_PROFILE = ideal | random (default) |
// latN (ideal handshakes, every response exactly N cycles late), G2D_LIVE_STATS=<file> (cycles, beats,
// kicks at close), G2D_MODEL_ARENA_MB, G2D_MODEL_IDENTITY.
#include <malloc.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <map>
#include <random>
#include <vector>

#include "Vg2d_top.h"
#include "g2d_hal.h"
#include "g2d_hal_model.h"   // G2D_MODEL_BASE (shared memory layout with the software backend)
#include "generated/g2d_regs.h"
#include "verilated.h"

#ifndef G2D_LIVE_DATA_W
#define G2D_LIVE_DATA_W 64
#endif

namespace {

constexpr unsigned kDB = G2D_LIVE_DATA_W / 8;

[[noreturn]] void fail(const char* what, uint64_t a = 0) {
  std::fprintf(stderr, "g2d_live: %s (0x%llx)\n", what, (unsigned long long)a);
  std::exit(3);
}

struct Profile {
  double p_awready = 1, p_wready = 1, p_arready = 1, p_rvalid = 1, p_bvalid = 1;
  unsigned r_lat = 0, b_lat = 0;
  bool reorder = false, interleave = false;
  bool fixed_lat = false;   // latN profile: every read and write response exactly N cycles late
};

struct RBurst {
  unsigned id, len, size, beat = 0;
  uint64_t addr, ready_at;
};
struct WBurst {
  unsigned id, len, size, beat = 0;
  uint64_t addr;
};
struct WBeat {
  uint8_t data[kDB];
  uint64_t strb;
  bool last;
};
struct BResp {
  unsigned id;
  uint64_t ready_at;
};

struct Live {
  VerilatedContext ctx;
  Vg2d_top* top = nullptr;
  uint64_t cyc = 0, rbeats = 0, wbeats = 0, kicks = 0;
  std::mt19937_64 rng;
  Profile prof;
  // memory (as sw/hal/model)
  std::vector<uint8_t> arena;
  bool identity = false;
  std::map<uint32_t, uint32_t> used;
  // AXI slave state
  std::deque<RBurst> rq;
  std::deque<WBurst> aw;
  std::deque<WBeat> wq;    // W beats that arrived before their AW (AXI allows W first)
  std::deque<BResp> bq;
  int r_cur = -1;     // index in rq of the burst being returned (no interleaving)
  int b_cur = -1;
  int r_sel = -1;     // burst whose beat is presented this cycle
  FILE* stats = nullptr;
  bool trace = false;      // G2D_LIVE_TRACE=1: AW / W handshakes on stderr
};

Live* g = nullptr;

bool chance(double p) { return p >= 1.0 || std::uniform_real_distribution<double>(0, 1)(g->rng) < p; }
unsigned rnd(unsigned hi) { return g->prof.fixed_lat ? hi : (hi ? unsigned(g->rng() % (hi + 1)) : 0); }

uint8_t* host(uint64_t a, size_t n) {
  if (a >> 32) fail("AXI address above 4 GB", a);
  if (g->identity) return reinterpret_cast<uint8_t*>(uintptr_t(a));
  uint64_t off = a - G2D_MODEL_BASE;
  if (a < G2D_MODEL_BASE || off + n > g->arena.size()) fail("AXI access outside the arena", a);
  return &g->arena[off];
}

// ---- data bus helpers (QData at 64 bit, VlWide at 128) ----
#if G2D_LIVE_DATA_W == 64
void get_bus(const QData& v, uint8_t* b) { for (unsigned i = 0; i < 8; ++i) b[i] = uint8_t(v >> (8 * i)); }
void set_bus(QData& v, const uint8_t* b) {
  v = 0;
  for (unsigned i = 0; i < 8; ++i) v |= QData(b[i]) << (8 * i);
}
#else
template <class W> void get_bus(const W& v, uint8_t* b) {
  for (unsigned i = 0; i < kDB; ++i) b[i] = uint8_t(v[i / 4] >> (8 * (i % 4)));
}
template <class W> void set_bus(W& v, const uint8_t* b) {
  for (unsigned w = 0; w < kDB / 4; ++w)
    v[w] = uint32_t(b[4 * w]) | uint32_t(b[4 * w + 1]) << 8 | uint32_t(b[4 * w + 2]) << 16 | uint32_t(b[4 * w + 3]) << 24;
}
#endif

uint64_t beat_addr(uint64_t a, unsigned size, unsigned beat) {
  uint64_t bytes = 1ull << size;
  return (a & ~(bytes - 1)) + uint64_t(beat) * bytes;
}

// ---- one clock cycle: drive slave outputs, sample handshakes at the rising edge ----
void cycle() {
  Vg2d_top* t = g->top;
  t->clk_i = 0;
  t->clk_cfg_i = 0;
  t->clk_mem_i = 0;
  t->eval();
  // AR / AW / W ready
  t->m_axi_arready_i = chance(g->prof.p_arready);
  t->m_axi_awready_i = chance(g->prof.p_awready);
  t->m_axi_wready_i = chance(g->prof.p_wready);
  // R: pick a burst whose beat is due
  g->r_sel = -1;
  if (!g->rq.empty() && chance(g->prof.p_rvalid)) {
    if (g->r_cur >= 0 && !g->prof.interleave) {
      if (g->rq[size_t(g->r_cur)].ready_at <= g->cyc) g->r_sel = g->r_cur;
    } else {
      std::vector<int> cand;   // first burst of each ID that is due (AXI: in order per ID)
      for (size_t i = 0; i < g->rq.size(); ++i) {
        bool first = true;
        for (size_t j = 0; j < i; ++j)
          if (g->rq[j].id == g->rq[i].id) first = false;
        if (first && g->rq[i].ready_at <= g->cyc) cand.push_back(int(i));
        if (!g->prof.reorder) break;
      }
      if (!cand.empty()) g->r_sel = cand[rnd(unsigned(cand.size() - 1))];
    }
  }
  if (g->r_sel >= 0) {
    RBurst& b = g->rq[size_t(g->r_sel)];
    uint8_t buf[kDB];
    uint64_t ba = beat_addr(b.addr, b.size, b.beat);
    uint64_t lane0 = ba & ~uint64_t(kDB - 1);
    std::memcpy(buf, host(lane0, kDB), kDB);
    set_bus(t->m_axi_rdata_i, buf);
    t->m_axi_rid_i = b.id;
    t->m_axi_rresp_i = 0;
    t->m_axi_rlast_i = b.beat == b.len;
    t->m_axi_rvalid_i = 1;
  } else {
    t->m_axi_rvalid_i = 0;
    t->m_axi_rlast_i = 0;
  }
  // B
  int bsel = -1;
  if (!g->bq.empty() && chance(g->prof.p_bvalid)) {
    for (size_t i = 0; i < g->bq.size(); ++i) {
      bool first = true;
      for (size_t j = 0; j < i; ++j)
        if (g->bq[j].id == g->bq[i].id) first = false;
      if (first && g->bq[i].ready_at <= g->cyc) {
        bsel = int(i);
        if (!g->prof.reorder || chance(0.5)) break;
      }
      if (!g->prof.reorder) break;
    }
  }
  if (bsel >= 0) {
    t->m_axi_bid_i = g->bq[size_t(bsel)].id;
    t->m_axi_bresp_i = 0;
    t->m_axi_bvalid_i = 1;
  } else {
    t->m_axi_bvalid_i = 0;
  }
  t->eval();
  // in reset the DUT outputs are not valid (random initial values): no handshake, all slave outputs low
  if (!t->rst_ni) {
    t->m_axi_arready_i = t->m_axi_awready_i = t->m_axi_wready_i = 0;
    t->m_axi_rvalid_i = t->m_axi_bvalid_i = 0;
    t->eval();
    t->clk_i = t->clk_cfg_i = t->clk_mem_i = 1;
    t->eval();
    g->cyc++;
    return;
  }
  // handshakes of this cycle
  bool ar = t->m_axi_arvalid_o && t->m_axi_arready_i;
  bool awf = t->m_axi_awvalid_o && t->m_axi_awready_i;
  bool wf = t->m_axi_wvalid_o && t->m_axi_wready_i;
  bool rf = t->m_axi_rvalid_i && t->m_axi_rready_o;
  bool bf = t->m_axi_bvalid_i && t->m_axi_bready_o;
  RBurst nr{};
  WBurst nw{};
  uint8_t wdata[kDB];
  uint64_t wstrb = 0;
  if (ar) {
    if (t->m_axi_arburst_o != 1) fail("AR burst type not INCR", t->m_axi_arburst_o);
    nr = RBurst{unsigned(t->m_axi_arid_o), unsigned(t->m_axi_arlen_o), unsigned(t->m_axi_arsize_o), 0,
                uint64_t(t->m_axi_araddr_o), g->cyc + 1 + rnd(g->prof.r_lat)};
  }
  if (awf) {
    if (t->m_axi_awburst_o != 1) fail("AW burst type not INCR", t->m_axi_awburst_o);
    nw = WBurst{unsigned(t->m_axi_awid_o), unsigned(t->m_axi_awlen_o), unsigned(t->m_axi_awsize_o), 0,
                uint64_t(t->m_axi_awaddr_o)};
  }
  if (wf) {
    get_bus(t->m_axi_wdata_o, wdata);
    wstrb = uint64_t(t->m_axi_wstrb_o);
  }
  bool wlast = t->m_axi_wlast_o;
  if (g->trace) {
    if (ar) std::fprintf(stderr, "%llu AR id %u addr 0x%llx len %u size %u\n", (unsigned long long)g->cyc, unsigned(t->m_axi_arid_o), (unsigned long long)t->m_axi_araddr_o, unsigned(t->m_axi_arlen_o), unsigned(t->m_axi_arsize_o));
    if (awf) std::fprintf(stderr, "%llu AW id %u addr 0x%llx len %u size %u\n", (unsigned long long)g->cyc, unsigned(t->m_axi_awid_o), (unsigned long long)t->m_axi_awaddr_o, unsigned(t->m_axi_awlen_o), unsigned(t->m_axi_awsize_o));
    if (wf) std::fprintf(stderr, "%llu W last %d strb 0x%llx\n", (unsigned long long)g->cyc, int(wlast), (unsigned long long)wstrb);
  }
  // rising edge
  t->clk_i = 1;
  t->clk_cfg_i = 1;
  t->clk_mem_i = 1;
  t->eval();
  g->cyc++;
  if (g->ctx.gotError() || g->ctx.gotFinish()) fail("simulation stopped (assertion failure or $finish)", g->cyc);
  // state updates
  if (rf) {
    if (g->r_sel < 0) fail("R handshake without a beat");
    RBurst& b = g->rq[size_t(g->r_sel)];
    g->rbeats++;
    if (b.beat == b.len) {
      g->rq.erase(g->rq.begin() + g->r_sel);
      if (g->r_cur == g->r_sel) g->r_cur = -1;
      else if (g->r_cur > g->r_sel) g->r_cur--;
    } else {
      b.beat++;
      g->r_cur = g->r_sel;
    }
  }
  if (ar) g->rq.push_back(nr);
  if (awf) g->aw.push_back(nw);
  if (wf) {
    WBeat wb;
    std::memcpy(wb.data, wdata, kDB);
    wb.strb = wstrb;
    wb.last = wlast;
    g->wq.push_back(wb);
  }
  while (!g->wq.empty() && !g->aw.empty()) {   // W beats in AW order
    const WBeat& wb = g->wq.front();
    WBurst& b = g->aw.front();
    uint64_t ba = beat_addr(b.addr, b.size, b.beat);
    uint64_t lane0 = ba & ~uint64_t(kDB - 1);
    uint8_t* p = host(lane0, kDB);
    for (unsigned i = 0; i < kDB; ++i)
      if (wb.strb >> i & 1) p[i] = wb.data[i];
    g->wbeats++;
    if ((b.beat == b.len) != wb.last) fail("WLAST does not match AWLEN", b.addr);
    if (b.beat == b.len) {
      g->bq.push_back(BResp{b.id, g->cyc + rnd(g->prof.b_lat)});
      g->aw.pop_front();
    } else {
      b.beat++;
    }
    g->wq.pop_front();
  }
  if (bf) g->bq.erase(g->bq.begin() + bsel);
}

// ---- AXI4-Lite master ----
void axil_write(uint32_t off, uint32_t v) {
  Vg2d_top* t = g->top;
  t->s_axil_awaddr_i = off;
  t->s_axil_awprot_i = 0;
  t->s_axil_wdata_i = v;
  t->s_axil_wstrb_i = 0xF;
  t->s_axil_awvalid_i = 1;
  t->s_axil_wvalid_i = 1;
  t->s_axil_bready_i = 1;
  bool awd = false, wd = false;
  for (int n = 0; !(awd && wd); ++n) {
    t->eval();
    bool a = t->s_axil_awvalid_i && t->s_axil_awready_o, w = t->s_axil_wvalid_i && t->s_axil_wready_o;
    cycle();
    if (a) { awd = true; t->s_axil_awvalid_i = 0; }
    if (w) { wd = true; t->s_axil_wvalid_i = 0; }
    if (n > 10000) fail("AXI4-Lite write not accepted", off);
  }
  for (int n = 0;; ++n) {
    t->eval();
    bool b = t->s_axil_bvalid_o;
    if (b && t->s_axil_bresp_o) fail("AXI4-Lite write error", off);
    cycle();
    if (b) break;
    if (n > 10000) fail("AXI4-Lite B missing", off);
  }
  t->s_axil_bready_i = 0;
}

uint32_t axil_read(uint32_t off) {
  Vg2d_top* t = g->top;
  t->s_axil_araddr_i = off;
  t->s_axil_arprot_i = 0;
  t->s_axil_arvalid_i = 1;
  t->s_axil_rready_i = 1;
  for (int n = 0;; ++n) {
    t->eval();
    bool a = t->s_axil_arready_o;
    cycle();
    if (a) break;
    if (n > 10000) fail("AXI4-Lite read not accepted", off);
  }
  t->s_axil_arvalid_i = 0;
  for (int n = 0;; ++n) {
    t->eval();
    if (t->s_axil_rvalid_o) {
      uint32_t v = t->s_axil_rdata_o;
      if (t->s_axil_rresp_o) fail("AXI4-Lite read error", off);
      cycle();
      t->s_axil_rready_i = 0;
      return v;
    }
    cycle();
    if (n > 10000) fail("AXI4-Lite R missing", off);
  }
}

// ---- HAL ----
uint32_t reg_read(void*, uint32_t off) { return axil_read(off); }

void reg_write(void*, uint32_t off, uint32_t v) {
  if (off == G2D_CSR_CMD_KICK) g->kicks++;
  axil_write(off, v);
}

int mem_alloc(void*, size_t size, size_t align, void** cpu, uint32_t* gpu) {
  if (size == 0) size = 1;
  if (align < 64) align = 64;
  if (g->identity) {
    void* p = nullptr;
    if (posix_memalign(&p, align, size)) return -1;
    if (uintptr_t(p) + size > (uint64_t(1) << 32)) {
      std::free(p);
      return -1;
    }
    *cpu = p;
    *gpu = uint32_t(uintptr_t(p));
    return 0;
  }
  uint64_t pos = 0;
  for (auto& kv : g->used) {             // first fit between live allocations (as sw/hal/model)
    uint64_t start = (pos + align - 1) & ~uint64_t(align - 1);
    if (start + size <= kv.first) {
      pos = start;
      goto found;
    }
    pos = uint64_t(kv.first) + kv.second;
  }
  pos = (pos + align - 1) & ~uint64_t(align - 1);
  if (pos + size > g->arena.size()) return -1;
found:
  g->used[uint32_t(pos)] = uint32_t(size);
  std::memset(&g->arena[pos], 0, size);
  *cpu = &g->arena[pos];
  *gpu = G2D_MODEL_BASE + uint32_t(pos);
  return 0;
}

void mem_free(void*, void* cpu, uint32_t gpu) {
  if (g->identity) {
    std::free(cpu);
    return;
  }
  g->used.erase(gpu - G2D_MODEL_BASE);
}

void* mem_cpu(void*, uint32_t gpu, size_t size) {
  if (g->identity) return reinterpret_cast<void*>(uintptr_t(gpu));
  uint64_t off = uint64_t(gpu) - G2D_MODEL_BASE;
  if (gpu < G2D_MODEL_BASE || off + size > g->arena.size()) return nullptr;
  return &g->arena[off];
}

void cache_nop(void*, const void*, size_t) {}
void cache_nop2(void*, void*, size_t) {}

uint64_t cycles_per_us() {
  const char* s = std::getenv("G2D_LIVE_CYCLES_PER_US");
  return s ? std::strtoull(s, nullptr, 0) : 100;
}

void hal_wait(void*, uint32_t timeout_us) {
  uint64_t n = uint64_t(timeout_us) * cycles_per_us();
  for (uint64_t i = 0; i < n; ++i) {
    cycle();
    if (g->top->irq_o) return;
  }
}

g2d_hal_t g_hal = {nullptr, reg_read, reg_write, mem_alloc, mem_free, mem_cpu, cache_nop, cache_nop2, hal_wait, nullptr, nullptr};

Profile make_profile(std::mt19937_64& r) {
  Profile p;
  auto pick = [&](std::initializer_list<double> v) { return *(v.begin() + r() % v.size()); };
  unsigned lat = unsigned(pick({0, 2, 10, 40, 100}));
  p.p_awready = pick({1.0, 0.7, 0.3});
  p.p_wready = pick({1.0, 0.7, 0.3});
  p.p_arready = pick({1.0, 0.7, 0.3});
  p.p_rvalid = pick({1.0, 0.8, 0.4});
  p.p_bvalid = pick({1.0, 0.8, 0.4});
  p.r_lat = lat;
  p.b_lat = lat;
  p.reorder = r() % 10 < 7;
  p.interleave = r() % 2;
  return p;
}

}  // namespace

extern "C" const g2d_hal_t* g2d_hal_open(void) {
  if (g) return &g_hal;
  g = new Live;
  const char* seed = std::getenv("G2D_LIVE_SEED");
  uint64_t s = seed ? std::strtoull(seed, nullptr, 0) : 1;
  g->rng.seed(s * 0x9E3779B97F4A7C15ull + 7);
  const char* pr = std::getenv("G2D_LIVE_PROFILE");
  if (pr && std::strncmp(pr, "lat", 3) == 0) {          // latN: ideal handshakes, fixed latency N
    g->prof.r_lat = g->prof.b_lat = unsigned(std::atoi(pr + 3));
    g->prof.fixed_lat = true;
  } else if (!pr || std::strcmp(pr, "ideal") != 0) {
    g->prof = make_profile(g->rng);
  }
  g->ctx.randReset(2);
  g->ctx.randSeed(int(s & 0x7FFFFFFF) + 1);
  g->ctx.fatalOnError(false);
  g->top = new Vg2d_top{&g->ctx};
  const char* id = std::getenv("G2D_MODEL_IDENTITY");
  g->identity = id && id[0] == '1';
  if (g->identity) {
    mallopt(M_MMAP_THRESHOLD, 1 << 30);
  } else {
    const char* mb = std::getenv("G2D_MODEL_ARENA_MB");
    g->arena.assign(size_t(mb ? std::atoi(mb) : 256) << 20, 0);
  }
  if (const char* st = std::getenv("G2D_LIVE_STATS")) g->stats = std::fopen(st, "w");
  g->trace = std::getenv("G2D_LIVE_TRACE") != nullptr;
  // reset: asserted before the first edge, released after a few cycles (random initial register values)
  Vg2d_top* t = g->top;
  t->rst_ni = 0;
  t->rst_cfg_ni = 0;
  t->rst_mem_ni = 0;
  t->s_axil_awvalid_i = t->s_axil_wvalid_i = t->s_axil_arvalid_i = 0;
  t->s_axil_bready_i = t->s_axil_rready_i = 0;
  t->m_axi_rvalid_i = t->m_axi_bvalid_i = 0;
  t->eval();
  for (int i = 0; i < 4; ++i) cycle();
  t->rst_ni = 1;
  t->rst_cfg_ni = 1;
  t->rst_mem_ni = 1;
  for (int i = 0; i < 2; ++i) cycle();
  axil_write(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_CLEAR_MASK);   // perf counters on
  std::fprintf(stderr, "g2d_live: DATA_W %d, profile seed %llu: rdy aw/w/ar %.1f/%.1f/%.1f rvalid %.1f bvalid %.1f "
               "lat %u reorder %d interleave %d\n", G2D_LIVE_DATA_W, (unsigned long long)s, g->prof.p_awready,
               g->prof.p_wready, g->prof.p_arready, g->prof.p_rvalid, g->prof.p_bvalid, g->prof.r_lat,
               int(g->prof.reorder), int(g->prof.interleave));
  return &g_hal;
}

extern "C" void g2d_hal_close(const g2d_hal_t*) {
  if (!g) return;
  for (int i = 0; i < 64 && !(g->rq.empty() && g->aw.empty() && g->wq.empty() && g->bq.empty()); ++i) cycle();
  if (g->stats) {
    std::fprintf(g->stats, "cycles %llu\nrd_beats %llu\nwr_beats %llu\nkicks %llu\n", (unsigned long long)g->cyc,
                 (unsigned long long)g->rbeats, (unsigned long long)g->wbeats, (unsigned long long)g->kicks);
    std::fclose(g->stats);
  }
  g->top->final();
  delete g->top;
  delete g;
  g = nullptr;
}

// Performance counters (snapshot, then read): busy cycles, draws, pixels out, read / write beats, read / write
// stalls, total cycles of the simulation. Returns 1 (0 from the weak default in programs without this backend).
extern "C" int g2d_hal_perf(uint64_t out[8]) {
  if (!g) return 0;
  axil_write(G2D_CSR_PERF_CTRL, G2D_CSR_PERF_CTRL_EN_MASK | G2D_CSR_PERF_CTRL_SNAPSHOT_MASK);
  out[0] = axil_read(G2D_CSR_PERF_BUSY_CYCLES);
  out[1] = axil_read(G2D_CSR_PERF_DRAWS);
  out[2] = axil_read(G2D_CSR_PERF_PIXELS_OUT);
  out[3] = axil_read(G2D_CSR_PERF_RD_BEATS);
  out[4] = axil_read(G2D_CSR_PERF_WR_BEATS);
  out[5] = axil_read(G2D_CSR_PERF_RD_STALL);
  out[6] = axil_read(G2D_CSR_PERF_WR_STALL);
  out[7] = g->cyc;
  return 1;
}

// The software backend's test extras, for programs written against it (none in live mode).
extern "C" uint8_t* g2d_hal_model_arena(uint32_t* base, size_t* size) {
  if (!g || g->identity) return nullptr;
  if (base) *base = G2D_MODEL_BASE;
  if (size) *size = g->arena.size();
  return g->arena.data();
}
extern "C" uint64_t g2d_hal_model_kicks(void) { return g ? g->kicks : 0; }
