// SPDX-License-Identifier: Apache-2.0
// G2D golden model: sparse memory and command-stream executor (spec/commands.md, spec/rendering.md).
#pragma once

#include <array>
#include <cstdint>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include "g2d/ops.hpp"

namespace g2d {

// Sparse byte-addressable 32-bit GPU address space. Unwritten bytes read as 0.
class Memory {
 public:
  static constexpr uint32_t kPage = 4096;

  uint8_t read8(uint32_t addr) const;
  void write8(uint32_t addr, uint8_t v);
  // Little-endian value of n (1..4) bytes.
  uint32_t read_le(uint32_t addr, unsigned n) const;
  void write_le(uint32_t addr, unsigned n, uint32_t v);
  void read(uint32_t addr, uint8_t* dst, size_t n) const;
  void write(uint32_t addr, const uint8_t* src, size_t n);
  void clear() { pages_.clear(); flush(); }
  // Sorted list of page base addresses that have been written (the attached window is not included).
  std::vector<uint32_t> pages() const;
  // Attach a flat host array as the bytes [base, base + size): accesses inside it go to the array (the
  // software backend shares its buffers with the model this way), all others to the sparse pages.
  void attach(uint32_t base, uint8_t* ptr, size_t size) { win_base_ = base; win_ptr_ = ptr; win_size_ = size; flush(); }
  // Identity mode: GPU address a is the host byte at virtual address a (a host program whose GPU-visible
  // memory all lies below 4 GB, e.g. LVGL on the software backend, which passes pointers as addresses).
  void set_identity(bool on) { identity_ = on; flush(); }
  // Called with a page base (kPage granular) before the first access to that page since the last
  // clear_touched(); lets a recorder capture what a kick reads and writes (sw/hal/model).
  void set_touch_hook(void (*hook)(void* ctx, uint32_t page), void* ctx) { hook_ = hook; hook_ctx_ = ctx; }
  void clear_touched() { touched_.clear(); last_page_ = ~0u; }
  // Called with the address of every byte write (after the touch hook): lets the recorder tell bytes the GPU
  // wrote from other changes of the same page (identity mode shares pages with host heap data).
  void set_write_hook(void (*hook)(void* ctx, uint32_t addr), void* ctx) { whook_ = hook; whook_ctx_ = ctx; }

 private:
  using Page = std::array<uint8_t, kPage>;
  std::unordered_map<uint32_t, std::unique_ptr<Page>> pages_;
  uint32_t win_base_ = 0;
  uint8_t* win_ptr_ = nullptr;
  size_t win_size_ = 0;
  bool identity_ = false;
  void (*hook_)(void*, uint32_t) = nullptr;
  void* hook_ctx_ = nullptr;
  void (*whook_)(void*, uint32_t) = nullptr;
  void* whook_ctx_ = nullptr;
  // v1.7 (Q-PERF-1): last page used for reads / writes (page base, pointer to its bytes); reads of a page never
  // written point at a shared zero page, so a write that allocates a page drops the read entry
  mutable uint32_t rc_base_ = 1, wc_base_ = 1;      // 1: no entry (page bases are kPage aligned)
  mutable const uint8_t* rc_ptr_ = nullptr;
  uint8_t* wc_ptr_ = nullptr;
  void flush() const { rc_base_ = 1; wc_base_ = 1; }
  const uint8_t* rpage(uint32_t base) const;
  uint8_t* wpage(uint32_t base);
  mutable std::unordered_set<uint32_t> touched_;
  mutable uint32_t last_page_ = ~0u;
  void touch(uint32_t addr) const {
    uint32_t p = addr & ~(kPage - 1);
    if (p == last_page_) return;
    last_page_ = p;
    if (touched_.insert(p).second) hook_(hook_ctx_, p);
  }
};

// ERR_STATUS bit positions (spec/regs.yaml).
enum ErrBit : uint32_t {
  kErrCmdDecode = 1u << 0,
  kErrBadState = 1u << 1,
  kErrAddrRange = 1u << 2,
  kErrAxiRd = 1u << 3,
  kErrAxiWr = 1u << 4,
  kErrKickOvf = 1u << 5,
};

struct GpuStats {
  uint64_t draws = 0;
  uint64_t pixels_out = 0;
  uint64_t commands = 0;
};

// Executes command buffers against a Memory, with the architectural effects visible through CSRs.
class Gpu {
 public:
  explicit Gpu(Memory& mem);

  // Hardware reset: state registers and CSR-visible status to reset values, address window open.
  void reset();
  // Soft reset (arch.md §2.2): clears status, errors, fence and state; keeps the address window.
  void soft_reset();

  // CSR side.
  void set_addr_win(uint32_t lo, uint32_t hi);  // applies the 64-byte granularity of regs.yaml
  // v1.7: optional units of the build (g2d_pkg FEAT_*: bit 0 families, 1 Gaussian, 2 YUV, 3 colour unit, 4 radial,
  // 5 mask); a draw needing an absent one is BAD_STATE (rendering.md §13.1). Default: all.
  void set_features(uint32_t mask) { features_ = mask & 0x3F; }
  uint32_t features() const { return features_; }
  uint32_t addr_win_lo() const { return win_lo_; }
  uint32_t addr_win_hi() const { return win_hi_; }

  // Executes one kicked command buffer (CMD_ADDR, CMD_KICK.SIZE). Returns false if the GPU is halted
  // (before or after). Kicks while halted are ignored.
  bool kick(uint32_t addr, uint32_t size);

  // State registers (LOAD_STATE view), for tests.
  uint32_t state(unsigned idx) const { return state_.at(idx); }

  // CSR-visible status.
  bool halted() const { return halted_; }
  uint32_t fence_seq() const { return fence_seq_; }
  bool irq_fence() const { return irq_fence_; }
  void clear_irq_fence() { irq_fence_ = false; }
  uint32_t err_status() const { return err_status_; }
  uint32_t err_addr() const { return err_addr_; }
  uint32_t err_cmd_addr() const { return err_cmd_addr_; }
  const GpuStats& stats() const { return stats_; }

 private:
  struct Rect {
    int x0, y0, x1, y1;
    bool empty() const { return x1 <= x0 || y1 <= y0; }
  };
  struct Buffer {
    uint32_t addr, stride;
    int w, h;
    uint8_t fmt;
    unsigned bytes;  // 0 for sub-byte formats
    unsigned bits;
  };

  void error(uint32_t bit, uint32_t err_addr, uint32_t cmd_addr);
  void load_state(unsigned idx, uint32_t v);
  bool validate_draw(bool is_path, uint32_t edge_addr, uint32_t edge_count, uint32_t cmd_addr);
  bool extent_ok(uint32_t start, uint64_t len) const;
  Buffer target() const;
  Buffer source() const;
  Rect clip_rect() const;
  void draw(const Rect& r, const std::vector<Edge>* edges, bool aa, bool even_odd);
  Argb fetch_texel(const Buffer& src, int x, int y) const;
  Argb source_texel(const Buffer& src, int x, int y) const;  // §5.2 steps 1-4 (per-draw fields: src_cfg_)
  // v1.7 (Q-PERF-1): source fields decoded once per draw (load_src_cfg) instead of per texel
  struct SrcCfg {
    ColorKey keys[4];
    bool force_opaque = false, premul = false;
  };
  SrcCfg src_cfg_;
  void load_src_cfg();

  std::array<uint32_t, 256> clut_{};  // CLUT of the current draw (indexed sources, rendering.md §3.5)

  Memory& mem_;
  std::array<uint32_t, 128> state_{};
  uint32_t features_ = 0x3F;   // G2D_ST_SIZE (static_assert in gpu.cpp)
  uint32_t win_lo_ = 0, win_hi_ = 0xFFFFFFFFu;
  bool halted_ = false;
  uint32_t fence_seq_ = 0;
  bool irq_fence_ = false;
  uint32_t err_status_ = 0, err_addr_ = 0, err_cmd_addr_ = 0;
  GpuStats stats_;
  // Writes of the current draw, committed when the draw ends (inputs read as before the draw, §13.3).
  std::vector<std::pair<uint32_t, std::pair<uint32_t, unsigned>>> pending_;
};

}  // namespace g2d
