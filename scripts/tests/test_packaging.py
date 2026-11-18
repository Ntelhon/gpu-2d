# SPDX-License-Identifier: Apache-2.0
"""v1.9 packaging checks (docs/INTEGRATION.md "Packaging"): the generated FuseSoC core, Bender.yml and IP-XACT
component are up to date and consistent with the sources and spec/regs.yaml; the SDC template uses only standard
SDC; Bender (when installed) lists every RTL file once per target."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_sdc  # noqa: E402
import gen_packaging  # noqa: E402
import gen_wrappers  # noqa: E402


def test_generated_up_to_date():
    assert gen_wrappers.main(["--check"]) == 0
    assert gen_packaging.main(["--check"]) == 0


def test_sdc_vocabulary():
    assert check_sdc.main(str(ROOT / "syn/constraints/g2d_top.sdc")) == 0


def test_ipxact_matches_regs():
    from lxml import etree
    ns = {"i": "http://www.accellera.org/XMLSchema/IPXACT/1685-2014"}
    t = etree.parse(str(ROOT / "ipxact/g2d_top.xml"))
    m = yaml.safe_load((ROOT / "spec/regs.yaml").read_text())
    regs = {r["name"]: r for r in m["blocks"]["csr"]["registers"]}
    xregs = t.findall(".//i:register", ns)
    assert len(xregs) == len(regs)
    for xr in xregs:
        r = regs[xr.findtext("i:name", namespaces=ns)]
        assert int(xr.findtext("i:addressOffset", namespaces=ns)) == int(r["offset"])
        xf = {f.findtext("i:name", namespaces=ns): f for f in xr.findall("i:field", ns)}
        assert set(xf) == {f["name"] for f in r["fields"]}
        for f in r["fields"]:
            hi, lo = gen_packaging.bits(f["bits"])
            assert int(xf[f["name"]].findtext("i:bitOffset", namespaces=ns)) == lo
            assert int(xf[f["name"]].findtext("i:bitWidth", namespaces=ns)) == hi - lo + 1
    names = [p.findtext("i:name", namespaces=ns) for p in t.findall(".//i:port", ns)]
    assert len(names) == len(set(names)) and "irq_o" in names and "test_mode_i" in names
    for f in t.findall(".//i:file", ns):
        assert (ROOT / "ipxact" / f.findtext("i:name", namespaces=ns)).resolve().exists()


@pytest.mark.skipif(not (shutil.which("bender") or (Path.home() / ".cargo/bin/bender").exists()),
                    reason="bender not installed")
def test_bender_targets():
    bender = shutil.which("bender") or str(Path.home() / ".cargo/bin/bender")
    for tgt, ram in [([], "rtl/common/g2d_ram_1r1w.sv"), (["-t", "xilinx"], "rtl/ram/xilinx/g2d_ram_1r1w.sv"),
                     (["-t", "asic"], "rtl/ram/asic/g2d_ram_1r1w.sv")]:
        out = subprocess.run([bender, "script", "flist", *tgt], cwd=ROOT, capture_output=True, text=True, check=True)
        files = [ln.strip() for ln in out.stdout.splitlines() if ln.strip().endswith(".sv")]
        assert len(files) == len(set(files)), tgt
        assert sum("g2d_ram_1r1w.sv" in f for f in files) == 1 and any(f.endswith(ram) for f in files), tgt


def test_spdx_identifiers():
    """Every own source file carries its licence identifier (scripts/spdx.py; LICENSE)."""
    import spdx
    assert spdx.main(["spdx.py", "--check"]) == 0


FUSESOC = Path.home() / "tools/fusesoc-venv/bin/fusesoc"


@pytest.mark.skipif(not (shutil.which("fusesoc") or FUSESOC.exists()), reason="fusesoc not installed")
def test_fusesoc_parses():
    exe = shutil.which("fusesoc") or str(FUSESOC)
    m = yaml.safe_load((ROOT / "spec/regs.yaml").read_text())["version"]
    out = subprocess.run([exe, "--cores-root", str(ROOT), "core-info", f"::g2d:{m['major']}.{m['minor']}.{m['patch']}"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "Parse error" not in out.stdout + out.stderr, out.stdout + out.stderr


def test_sdc_in_opensta():
    """syn/constraints/g2d_top.sdc read by OpenSTA in every clock configuration (scripts/sta_sdc_check.py); skipped
    when OpenSTA or a Liberty library is not installed."""
    import sta_sdc_check
    rc = sta_sdc_check.main([])
    if rc == 2:
        pytest.skip("OpenSTA or Liberty library not installed")
    assert rc == 0


def test_ipxact_schema():
    """ipxact/g2d_top.xml validates against the IEEE 1685-2014 schema (v2.0, OPEN_QUESTIONS Q-INT-3).
    IPXACT_XSD = the schema's index.xsd; default build/ext/ipxact/ieee-1685-2014/index.xsd, fetched by
    scripts/fetch_ipxact_schema.sh. Skipped without it (the schema host www.accellera.org redirects to plain http)."""
    import os
    from lxml import etree
    xsd = Path(os.environ.get("IPXACT_XSD", ROOT / "build/ext/ipxact/ieee-1685-2014/index.xsd"))
    if not xsd.exists():
        pytest.skip("IP-XACT schema not fetched (scripts/fetch_ipxact_schema.sh)")
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    doc = etree.parse(str(ROOT / "ipxact/g2d_top.xml"))
    assert schema.validate(doc), schema.error_log.last_error
