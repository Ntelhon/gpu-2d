# SPDX-License-Identifier: Apache-2.0
# Project environment. Usage: source scripts/env.sh
# Tool root defaults to ~/tools (override with G2D_TOOLS). Nothing machine-specific is hard-coded here;
# see docs/TOOLS.md for the required versions.
G2D_TOOLS="${G2D_TOOLS:-$HOME/tools}"
if [ -f "$G2D_TOOLS/env.sh" ]; then
  . "$G2D_TOOLS/env.sh"
fi
# cocotb 2.x needs Verilator >= 5.036; prefer the project-local build if present.
if [ -d "$G2D_TOOLS/verilator/bin" ]; then
  export PATH="$G2D_TOOLS/verilator/bin:$PATH"
fi
# OSS CAD Suite (formal proofs only). Not added to PATH: it bundles its own Verilator and Python.
export OSS_CAD="${OSS_CAD:-$G2D_TOOLS/oss-cad-suite-20261001}"
