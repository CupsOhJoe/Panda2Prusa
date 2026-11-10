"""End-to-end: PrusaSlicer itself must load and slice what we write.

Runs only when a PrusaSlicer console executable is available (PRUSA_SLICER_EXE, or
one of the usual install locations) and sample files are present.
"""

import glob
import os
import re
import shutil
import subprocess

import pytest

from panda2prusa.convert import convert_file
from panda2prusa.prusa_config import PrusaInstall
from panda2prusa.reader import read_bambu_3mf

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES_DIR = os.environ.get("B2P_SAMPLES", os.path.join(_HERE, "samples"))
SAMPLES = [
    p
    for p in sorted(glob.glob(os.path.join(_SAMPLES_DIR, "*.3mf")))
    if not os.path.basename(p).startswith("out_") and not p.endswith("_prusa.3mf")
]

_CANDIDATES = [
    os.environ.get("PRUSA_SLICER_EXE", ""),
    r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
    r"D:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
    "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
    shutil.which("prusa-slicer") or "",
    shutil.which("prusa-slicer-console") or "",
]
EXE = next((c for c in _CANDIDATES if c and os.path.isfile(c)), None)

pytestmark = pytest.mark.skipif(EXE is None, reason="PrusaSlicer executable not found")


@pytest.fixture(params=SAMPLES, ids=[os.path.basename(p) for p in SAMPLES])
def sample(request):
    if not SAMPLES:
        pytest.skip("no sample .3mf files available")
    project = read_bambu_3mf(request.param)
    if not project.has_settings:
        pytest.skip("sample has no project_settings.config")
    return request.param


def _slice(three_mf: str, tmp_path) -> str:
    ini = tmp_path / "ascii.ini"
    ini.write_text("binary_gcode = 0\n", encoding="utf-8")
    gcode = tmp_path / (os.path.basename(three_mf) + ".gcode")
    proc = subprocess.run(
        [EXE, "--export-gcode", "--load", str(ini), "--output", str(gcode), three_mf],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert gcode.exists()
    return gcode.read_text(encoding="utf-8", errors="replace")


def _footer(gcode: str, key: str):
    m = re.search(rf"^; {re.escape(key)} = (.*)$", gcode, re.M)
    return m.group(1) if m else None


@pytest.mark.parametrize("printer", ["auto", "bambu"])
def test_prusaslicer_slices_converted_project(sample, tmp_path, printer):
    if printer == "auto" and PrusaInstall.find() is None:
        pytest.skip("no PrusaSlicer configuration to take the printer from")
    out = tmp_path / "converted.3mf"
    result = convert_file(sample, str(out), printer=printer)  # one file per plate by default
    assert result.stats.settings_written
    project = read_bambu_3mf(sample)
    assert len(result.outputs) == max(1, len(project.plate_ids))
    gcode = ""
    for path in result.outputs:
        gcode = _slice(path, tmp_path)
    assert _footer(gcode, "layer_height") == project.config.scalar("layer_height")
    assert _footer(gcode, "print_settings_id") == result.stats.print_name
    assert _footer(gcode, "printer_settings_id") == result.stats.printer_name
    colours = (_footer(gcode, "filament_colour") or "").split(";")
    assert len(colours) == result.stats.extruders
    if printer == "auto":
        # the printer profile is the user's own, not a translation of Bambu's
        inst = PrusaInstall.find()
        own = inst.preset("printer", inst.selected("printer"))
        assert _footer(gcode, "bed_shape") == own.get("bed_shape")
        assert _footer(gcode, "start_gcode") == own.get("start_gcode")
    else:
        bed = project.config.raw_value("printable_area")
        if isinstance(bed, list) and bed:
            assert _footer(gcode, "bed_shape") == ",".join(bed)
