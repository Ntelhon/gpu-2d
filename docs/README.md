# G2D documentation

## User documentation

| Document | Contents |
|---|---|
| [INTEGRATION.md](INTEGRATION.md) | SoC integration: ports, parameters, bus adapters, clocks and resets, memories, constraints, packaging (FuseSoC, Bender, IP-XACT) |
| [PROGRAMMING.md](PROGRAMMING.md) | Programming manual: VGLite driver, HAL interface, command buffers, interrupts, caches, operating-system ports |
| [regs.md](regs.md) | Register reference (generated from `spec/regs.yaml`) |
| [TOOLS.md](TOOLS.md) | Tool versions and installation |

## Specifications (`spec/`)

| Document | Contents |
|---|---|
| [arch.md](../spec/arch.md) | Architecture: interfaces, clocks and resets, pipeline, memories, error handling |
| [rendering.md](../spec/rendering.md) | Bit-exact rendering semantics (the reference for the golden model and the RTL) |
| [commands.md](../spec/commands.md) | Command stream format |
| [regs.yaml](../spec/regs.yaml) | Register map, single source for generated RTL, C header and documentation |
| [api-subset.md](../spec/api-subset.md) | VGLite API coverage and the status of every call |
| [vglite-reference.md](../spec/vglite-reference.md) | VGLite API version, sources used and not used |
| [bandwidth.md](../spec/bandwidth.md) | Bandwidth and latency budget (1280 x 720 at 60 fps) |
| [verification-plan.md](../spec/verification-plan.md) | Verification plan: test levels, test IDs, coverage |

## Implementation results

| Document | Contents |
|---|---|
| [FPGA.md](FPGA.md) | FPGA prototype on the Genesys2 with the Cheshire SoC: build, timing, board results |
| [ASIC.md](ASIC.md) | ASIC reference layout on IHP SG13G2: flow, results, known defects, improvements |
| [VERIFICATION_MATRIX.md](VERIFICATION_MATRIX.md) | Cross-checks against third-party renderers (Skia, ThorVG, matplotlib) |

## Project management

| Document | Contents |
|---|---|
| [STATUS.md](STATUS.md) | Status per release, test results, known bugs, decision log |
| [CLOSURE_V2.md](CLOSURE_V2.md) | Outcome of every open item at v2.0 |
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | Open questions and unverified assumptions |
| [ROADMAP_V2.md](ROADMAP_V2.md) | Release roadmap v1 to v2 |
| [PHASES.md](PHASES.md) | Plans per phase and release |
| [FEATURES_LATER.md](FEATURES_LATER.md) | Candidate features for later releases |
| [AUDIT_2026-11.md](AUDIT_2026-11.md) | Repository audit and industry comparison (2026-11) |
| [LVGL_SURVEY.md](LVGL_SURVEY.md) | LVGL 9.6 use of the VGLite API |
| [PROJECT_RULES.md](PROJECT_RULES.md) | Engineering rules (summary for contributors: `CONTRIBUTING.md`) |
| [research/notes/](research/notes/) | Design research notes |

Release history: [CHANGELOG.md](../CHANGELOG.md).
