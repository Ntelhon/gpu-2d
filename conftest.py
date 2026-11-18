# SPDX-License-Identifier: Apache-2.0
"""pytest configuration: import paths for the golden model module, dv/common and scripts."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in (
    Path(os.environ.get("G2D_MODEL_PATH", ROOT / "build" / "model")).resolve(),
    ROOT / "dv" / "common",
    ROOT / "scripts",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
