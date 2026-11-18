// SPDX-License-Identifier: Apache-2.0
// G2D HAL, software backend: the GPU is the C++ golden model (Phase 6 plan, backend "SW").
//
// - Memory: one host arena attached to the model's address space (gm::Memory::attach), GPU addresses
//   G2D_MODEL_BASE + offset; first-fit allocator. Identity mode (G2D_MODEL_IDENTITY=1): GPU address ==
//   host pointer, for programs that pass pointers as GPU addresses (LVGL); allocations then come from the
//   C heap, kept below 4 GB (the program is linked non-PIE and malloc is kept off mmap).
// - CSRs (spec/regs.yaml) are emulated: a kick executes the buffer at once, so the GPU is idle again when
//   CMD_KICK returns. While halted, kicks are queued (not executed) until a soft reset drops them, as on the
//   hardware. FENCE_SEQ, IRQ_STATUS.FENCE, the error registers and HALTED come from the model.
// - Optional recorder (G2D_RECORD=<file>): every kick, preceded by the pre-kick content of every 4 KB page
//   the kick touches whose content the replay has not seen yet (model memory touch hook), for replay on the
//   RTL (SYS-06, dv/tests/test_sys06.py). Format in g2d_hal_model.h.
#include "g2d_hal_model.h"

#include <malloc.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <array>
#include <map>
#include <unordered_map>
#include <vector>

#include "g2d/gpu.hpp"
#include "g2d_hal.h"
#include "generated/g2d_regs.h"

namespace {

struct Model {
  std::vector<uint8_t> arena;
  bool identity = false;
  std::unordered_map<uint32_t, std::vector<uint8_t>> shadow;   // recorder: page contents the replay has
  std::vector<uint32_t> touched;                               // pages touched by the current kick
  using Mask = std::array<uint8_t, 512>;                       // one bit per byte of a 4 KB page
  std::unordered_map<uint32_t, Mask> kick_w;                   // bytes the GPU wrote in the current kick
  std::unordered_map<uint32_t, Mask> gpu_w;                    // bytes whose replay value is a GPU result
  uint32_t last_wp = ~0u;
  Mask* last_wm = nullptr;
  std::map<uint32_t, uint32_t> used;    // offset -> size of live allocations
  g2d::Memory mem;
  g2d::Gpu gpu{mem};
  uint32_t cmd_addr = 0, irq_enable = 0, scratch = 0, base_hi = 0, queued = 0;
  uint32_t mem_attr = G2D_CSR_MEM_ATTR_RESET, mem_qos = 0;   // v2.0: stored for read-back (no timing effect)
  uint32_t err_clear = 0;               // ERR_STATUS bits written 1 since the last model error
  bool kick_ovf = false;
  FILE* rec = nullptr;
  // test controls (v1.6, D-1..D-4)
  bool stall = false, reset_works = true;
  int fail_alloc = -1, live_allocs = 0;
  bool noncoherent = false;
  std::vector<uint8_t> cpu_view;        // non-coherent mode: what the CPU sees
  bool rec_post = false;                // G2D_RECORD_POST=1: POST records after every kick
  uint64_t kicks = 0;
  uint64_t waits = 0, clock_us = 0;   // v2.0: HAL wait calls, simulated HAL clock
  uint32_t clock_step = 0;
};

Model* g_m = nullptr;
int g_refs = 0;      // g2d_hal_open / _close pairs: a test may hold the model across the driver's open / close

constexpr uint32_t kKickDepth = 4;

uint32_t status() {
  uint32_t s = 0;
  bool halted = g_m->gpu.halted();
  if (!halted && g_m->queued == 0) s |= G2D_FIELD_SET(G2D_CSR_STATUS_IDLE, 1);
  if (g_m->stall && !g_m->reset_works) s |= G2D_FIELD_SET(G2D_CSR_STATUS_SOFT_RESET_BUSY, 1);
  if (halted) s |= G2D_FIELD_SET(G2D_CSR_STATUS_HALTED, 1);
  s |= G2D_FIELD_SET(G2D_CSR_STATUS_KICK_FREE, kKickDepth - g_m->queued);
  return s;
}

uint32_t err_status() {
  uint32_t e = g_m->gpu.err_status() & ~g_m->err_clear;
  if (g_m->kick_ovf) e |= G2D_FIELD_SET(G2D_CSR_ERR_STATUS_KICK_OVF, 1);
  return e;
}

// ---- recorder ----
constexpr uint32_t kPage = g2d::Memory::kPage;

void rec_word(uint32_t w) { std::fwrite(&w, 4, 1, g_m->rec); }

// Host bytes of a GPU page (nullptr: not host memory the HAL knows).
const uint8_t* page_ptr(uint32_t page) {
  if (g_m->identity) return reinterpret_cast<const uint8_t*>(uintptr_t(page));
  uint64_t off = uint64_t(page) - G2D_MODEL_BASE;
  if (page < G2D_MODEL_BASE || off + kPage > g_m->arena.size()) return nullptr;
  return &g_m->arena[off];
}

// First access of the current kick to a page: record its pre-kick content unless the replay has it.
void on_touch(void*, uint32_t page) {
  const uint8_t* p = page_ptr(page);
  if (!p) return;
  g_m->touched.push_back(page);
  auto it = g_m->shadow.find(page);
  if (it != g_m->shadow.end() && std::memcmp(it->second.data(), p, kPage) == 0) return;
  rec_word(G2D_REC_MEM);
  rec_word(page);
  rec_word(kPage);
  std::fwrite(p, 1, kPage, g_m->rec);
  g_m->shadow[page].assign(p, p + kPage);
  g_m->gpu_w.erase(page);     // the replay now holds the recorded content (its earlier results are checked first)
}

void on_write(void*, uint32_t addr) {
  uint32_t p = addr & ~(kPage - 1);
  if (p != g_m->last_wp) {
    g_m->last_wm = &g_m->kick_w[p];   // value-initialised (zero) on first use; node pointers stay valid
    g_m->last_wp = p;
  }
  (*g_m->last_wm)[(addr & (kPage - 1)) >> 3] |= uint8_t(1u << (addr & 7));
}

// POST / CHECK record: page, data, mask (bit i of mask byte i/8: byte i is compared)
void rec_masked(uint32_t kind, uint32_t page, const uint8_t* data, const Model::Mask& m) {
  rec_word(kind);
  rec_word(page);
  rec_word(kPage);
  std::fwrite(data, 1, kPage, g_m->rec);
  std::fwrite(m.data(), 1, m.size(), g_m->rec);
}

void do_kick(uint32_t size) {
  if (g_m->gpu.halted() || g_m->stall) {   // stall (test control): accepted, never executed
    if (g_m->queued < kKickDepth)
      g_m->queued++;
    else
      g_m->kick_ovf = true;
    return;
  }
  if (g_m->rec) {
    // the MEM records of the pages this kick touches are written while it runs, before the KICK record
    // that follows: a replay applies them first
    g_m->touched.clear();
    g_m->mem.clear_touched();
    g_m->kick_w.clear();
    g_m->last_wp = ~0u;
  }
  g_m->gpu.kick(g_m->cmd_addr, size);
  g_m->kicks++;
  if (g_m->rec) {
    rec_word(G2D_REC_KICK);
    rec_word(g_m->cmd_addr);
    rec_word(size);
    // The GPU's writes: the replay produces them itself. Only the bytes the GPU wrote are taken over: other
    // bytes of a touched page can change during a kick without a GPU write (identity mode: heap pages shared
    // with allocator data of the program, the recorder and the model).
    std::map<uint32_t, const Model::Mask*> order;
    for (const auto& kv : g_m->kick_w) order[kv.first] = &kv.second;
    for (const auto& kv : order) {
      const uint8_t* p = page_ptr(kv.first);
      if (!p) continue;
      auto& sh = g_m->shadow[kv.first];
      if (sh.size() != kPage) sh.assign(p, p + kPage);
      Model::Mask& gw = g_m->gpu_w[kv.first];
      for (uint32_t i = 0; i < kPage; ++i)
        if ((*kv.second)[i >> 3] >> (i & 7) & 1) sh[i] = p[i];
      for (size_t i = 0; i < gw.size(); ++i) gw[i] |= (*kv.second)[i];
      if (g_m->rec_post) rec_masked(G2D_REC_POST, kv.first, sh.data(), *kv.second);
    }
  }
}

// ---- HAL operations ----
uint32_t reg_read(void*, uint32_t off) {
  const g2d::GpuStats& st = g_m->gpu.stats();
  switch (off) {
    case G2D_CSR_ID: return G2D_CSR_ID_RESET;
    case G2D_CSR_VERSION: return G2D_CSR_VERSION_RESET;
    case G2D_CSR_HWCFG:
      return G2D_FIELD_SET(G2D_CSR_HWCFG_DATA_BYTES, 8) | G2D_FIELD_SET(G2D_CSR_HWCFG_ADDR_W, 32) |
             G2D_FIELD_SET(G2D_CSR_HWCFG_KICK_DEPTH, kKickDepth) |
             (g_m->gpu.features() << G2D_CSR_HWCFG_FEAT_FAMILY_SHIFT);   // v1.7: FEAT_* bits 26..31
    case G2D_CSR_SCRATCH: return g_m->scratch;
    case G2D_CSR_STATUS: return status();
    case G2D_CSR_CMD_ADDR: return g_m->cmd_addr;
    case G2D_CSR_FENCE_SEQ: return g_m->gpu.fence_seq();
    case G2D_CSR_IRQ_STATUS: {
      uint32_t v = 0;
      if (g_m->gpu.irq_fence()) v |= G2D_FIELD_SET(G2D_CSR_IRQ_STATUS_FENCE, 1);
      if (err_status()) v |= G2D_FIELD_SET(G2D_CSR_IRQ_STATUS_ERROR, 1);
      return v;
    }
    case G2D_CSR_IRQ_ENABLE: return g_m->irq_enable;
    case G2D_CSR_ERR_STATUS: return err_status();
    case G2D_CSR_ERR_ADDR: return g_m->gpu.err_addr();
    case G2D_CSR_ERR_CMD_ADDR: return g_m->gpu.err_cmd_addr();
    case G2D_CSR_ADDR_WIN_LO: return g_m->gpu.addr_win_lo();
    case G2D_CSR_ADDR_WIN_HI: return g_m->gpu.addr_win_hi();
    case G2D_CSR_MEM_BASE_HI: return g_m->base_hi;
    case G2D_CSR_MEM_ATTR: return g_m->mem_attr;
    case G2D_CSR_MEM_QOS: return g_m->mem_qos;
    case G2D_CSR_PERF_DRAWS: return uint32_t(st.draws);
    case G2D_CSR_PERF_PIXELS_OUT: return uint32_t(st.pixels_out);
    default: return 0;
  }
}

void reg_write(void*, uint32_t off, uint32_t v) {
  switch (off) {
    case G2D_CSR_SCRATCH: g_m->scratch = v; break;
    case G2D_CSR_CTRL:
      if (G2D_FIELD_GET(G2D_CSR_CTRL_SOFT_RESET, v)) {
        if (g_m->stall && !g_m->reset_works) break;   // the reset never completes (test control)
        g_m->stall = false;
        g_m->gpu.soft_reset();
        g_m->queued = 0;
        g_m->kick_ovf = false;
        g_m->err_clear = 0;
        if (g_m->rec) rec_word(G2D_REC_SOFT_RESET);
      }
      break;
    case G2D_CSR_CMD_ADDR: g_m->cmd_addr = v; break;
    case G2D_CSR_CMD_KICK: do_kick(G2D_FIELD_GET(G2D_CSR_CMD_KICK_SIZE, v)); break;
    case G2D_CSR_IRQ_STATUS:
      if (G2D_FIELD_GET(G2D_CSR_IRQ_STATUS_FENCE, v)) g_m->gpu.clear_irq_fence();
      break;
    case G2D_CSR_IRQ_ENABLE: g_m->irq_enable = v; break;
    case G2D_CSR_ERR_STATUS:
      g_m->err_clear |= v & g_m->gpu.err_status();
      if (G2D_FIELD_GET(G2D_CSR_ERR_STATUS_KICK_OVF, v)) g_m->kick_ovf = false;
      break;
    case G2D_CSR_ADDR_WIN_LO: g_m->gpu.set_addr_win(v, g_m->gpu.addr_win_hi()); break;
    case G2D_CSR_ADDR_WIN_HI: g_m->gpu.set_addr_win(g_m->gpu.addr_win_lo(), v); break;
    case G2D_CSR_MEM_BASE_HI: g_m->base_hi = v; break;
    case G2D_CSR_MEM_ATTR: g_m->mem_attr = v & 0x0F7F0F7Fu; break;
    case G2D_CSR_MEM_QOS: g_m->mem_qos = v & 0xFFFF1F1Fu; break;
    default: break;
  }
}

int mem_alloc_do(size_t size, size_t align, void** cpu, uint32_t* gpu);

int mem_alloc(void*, size_t size, size_t align, void** cpu, uint32_t* gpu) {
  if (g_m->fail_alloc >= 0 && g_m->fail_alloc-- == 0) return -1;   // test control
  int rc = mem_alloc_do(size, align, cpu, gpu);
  if (rc == 0) g_m->live_allocs++;
  return rc;
}

int mem_alloc_do(size_t size, size_t align, void** cpu, uint32_t* gpu) {
  if (size == 0) size = 1;
  if (align < 64) align = 64;
  if (g_m->identity) {
    void* p = nullptr;
    if (posix_memalign(&p, align, size)) return -1;
    if (uintptr_t(p) + size > (uint64_t(1) << 32)) {   // not reachable with 32-bit GPU addresses
      std::free(p);
      return -1;
    }
    *cpu = p;
    *gpu = uint32_t(uintptr_t(p));
    return 0;
  }
  uint64_t pos = 0;
  for (auto& kv : g_m->used) {             // first fit between live allocations
    uint64_t start = (pos + align - 1) & ~uint64_t(align - 1);
    if (start + size <= kv.first) {
      pos = start;
      goto found;
    }
    pos = uint64_t(kv.first) + kv.second;
  }
  pos = (pos + align - 1) & ~uint64_t(align - 1);
  if (pos + size > g_m->arena.size()) return -1;
found:
  g_m->used[uint32_t(pos)] = uint32_t(size);
  *cpu = g_m->noncoherent ? &g_m->cpu_view[pos] : &g_m->arena[pos];
  *gpu = G2D_MODEL_BASE + uint32_t(pos);
  return 0;
}

void mem_free(void*, void* cpu, uint32_t gpu) {
  g_m->live_allocs--;
  if (g_m->identity)
    std::free(cpu);
  else
    g_m->used.erase(gpu - G2D_MODEL_BASE);
}

void* mem_cpu(void*, uint32_t gpu, size_t size) {
  if (g_m->identity) return reinterpret_cast<void*>(uintptr_t(gpu));
  uint64_t off = uint64_t(gpu) - G2D_MODEL_BASE;
  if (gpu < G2D_MODEL_BASE || off + size > g_m->arena.size()) return nullptr;
  return g_m->noncoherent ? &g_m->cpu_view[off] : &g_m->arena[off];
}

// Non-coherent mode: whole 64-byte lines around the range, as a cache maintains them.
void line_range(const void* p, size_t size, size_t* lo, size_t* hi) {
  size_t off = size_t(static_cast<const uint8_t*>(p) - g_m->cpu_view.data());
  *lo = off & ~size_t(63);
  *hi = std::min(g_m->cpu_view.size(), (off + size + 63) & ~size_t(63));
}
void cache_clean(void*, const void* cpu, size_t size) {
  if (!g_m->noncoherent || size == 0) return;
  size_t lo, hi;
  line_range(cpu, size, &lo, &hi);
  std::memcpy(&g_m->arena[lo], &g_m->cpu_view[lo], hi - lo);
}
void cache_invalidate(void*, void* cpu, size_t size) {
  if (!g_m->noncoherent || size == 0) return;
  size_t lo, hi;
  line_range(cpu, size, &lo, &hi);
  std::memcpy(&g_m->cpu_view[lo], &g_m->arena[lo], hi - lo);
}
// kicks execute synchronously: nothing to wait for; v2.0 counts the calls and advances the test clock
void wait_nop(void*, uint32_t) {
  g_m->waits++;
  g_m->clock_us += g_m->clock_step;
}
uint64_t time_us(void*) { return g_m->clock_us; }
uint32_t g_base_hi_value = 0;
uint32_t mem_base_hi(void*) { return g_base_hi_value; }

g2d_hal_t g_hal = {nullptr, reg_read, reg_write, mem_alloc, mem_free, mem_cpu, cache_clean, cache_invalidate, wait_nop,
                   nullptr, nullptr};

}  // namespace

extern "C" const g2d_hal_t* g2d_hal_open(void) {
  g_refs++;
  if (g_m) return &g_hal;
  g_m = new Model;
  g_hal.time_us = nullptr;        // v2.0 test controls start off
  g_hal.mem_base_hi = nullptr;
  // G2D_MODEL_FEATURES=<hex> (v1.7): the optional units of the modelled build (HWCFG.FEAT_*), e.g. 0 for an FPGA
  // build with FEATURES = 0, so expected results match that bitstream
  if (const char* f = std::getenv("G2D_MODEL_FEATURES")) g_m->gpu.set_features(unsigned(std::strtoul(f, nullptr, 16)));
  const char* id = std::getenv("G2D_MODEL_IDENTITY");
  g_m->identity = id && std::atoi(id) != 0;
  if (g_m->identity) {
    mallopt(M_MMAP_THRESHOLD, 1 << 30);   // keep heap blocks in the brk heap, below 4 GB in a non-PIE program
    g_m->mem.set_identity(true);
  } else {
    const char* mb = std::getenv("G2D_MODEL_ARENA_MB");
    size_t bytes = size_t(mb ? std::atoi(mb) : 256) << 20;
    g_m->arena.assign(bytes, 0);
    g_m->mem.attach(G2D_MODEL_BASE, g_m->arena.data(), bytes);
  }
  g_m->gpu.reset();
  if (const char* path = std::getenv("G2D_RECORD")) {
    g_m->rec = std::fopen(path, "wb");
    if (g_m->rec) {
      rec_word(G2D_REC_MAGIC);
      rec_word(G2D_REC_VERSION);
      g_m->mem.set_touch_hook(on_touch, nullptr);
      g_m->mem.set_write_hook(on_write, nullptr);
      const char* post = std::getenv("G2D_RECORD_POST");
      g_m->rec_post = post && post[0] == '1';
    }
  }
  return &g_hal;
}

extern "C" void g2d_hal_close(const g2d_hal_t*) {
  if (!g_m) return;
  if (g_refs > 0 && --g_refs > 0) return;   // still open elsewhere
  if (g_m->rec) {
    // CHECK: per page the bytes whose replay value is a GPU result, as the software backend computed them
    std::map<uint32_t, const Model::Mask*> pages;
    for (const auto& kv : g_m->gpu_w) pages[kv.first] = &kv.second;
    for (const auto& kv : pages) rec_masked(G2D_REC_CHECK, kv.first, g_m->shadow[kv.first].data(), *kv.second);
    rec_word(G2D_REC_END);
    std::fclose(g_m->rec);
  }
  delete g_m;
  g_m = nullptr;
}

extern "C" uint8_t* g2d_hal_model_arena(uint32_t* base, size_t* size) {
  if (!g_m || g_m->identity) return nullptr;
  if (base) *base = G2D_MODEL_BASE;
  if (size) *size = g_m->arena.size();
  return g_m->arena.data();
}

extern "C" uint64_t g2d_hal_model_kicks(void) { return g_m ? g_m->kicks : 0; }

extern "C" uint64_t g2d_hal_model_waits(void) { return g_m ? g_m->waits : 0; }

extern "C" void g2d_hal_model_clock(uint32_t us_per_wait) {
  g_m->clock_step = us_per_wait;
  g_hal.time_us = us_per_wait ? time_us : nullptr;
}

extern "C" void g2d_hal_model_base_hi(int on, uint32_t value) {
  g_base_hi_value = value;
  g_hal.mem_base_hi = on ? mem_base_hi : nullptr;
}

extern "C" void g2d_hal_model_stall(int stall, int reset_works) {
  if (!g_m) return;
  g_m->stall = stall != 0;
  g_m->reset_works = reset_works != 0;
  if (!g_m->stall) {        // the hung kicks are dropped as a soft reset would
    g_m->queued = 0;
  }
}

extern "C" void g2d_hal_model_fail_alloc(int n) {
  if (g_m) g_m->fail_alloc = n;
}

extern "C" int g2d_hal_model_live_allocs(void) { return g_m ? g_m->live_allocs : 0; }

extern "C" void g2d_hal_model_features(unsigned mask) {
  if (g_m) g_m->gpu.set_features(mask);
}

extern "C" void g2d_hal_model_noncoherent(int on) {
  if (!g_m || g_m->identity) return;
  g_m->noncoherent = on != 0;
  if (g_m->noncoherent) g_m->cpu_view = g_m->arena;     // both views equal at the switch
}
