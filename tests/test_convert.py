"""Round-trip structural tests against real sample files.

These skip automatically if no sample files are present, so the suite still runs
on machines without them. Drop Bambu/Orca .3mf files into samples/ (not committed),
or point B2P_SAMPLES at a directory of them.
"""

import glob
import os
import re
import zipfile

import pytest

from panda2prusa.convert import convert_file, describe
from panda2prusa import ns

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLES_DIR = os.environ.get("B2P_SAMPLES", os.path.join(_HERE, "samples"))
AVAILABLE = [
    p
    for p in sorted(glob.glob(os.path.join(_SAMPLES_DIR, "*.3mf")))
    if not os.path.basename(p).startswith("out_")
]


@pytest.fixture(params=AVAILABLE, ids=[os.path.basename(p) for p in AVAILABLE])
def sample(request):
    if not AVAILABLE:
        pytest.skip("no sample .3mf files available")
    return request.param


def _read_model(path):
    with zipfile.ZipFile(path) as z:
        return z.read("3D/3dmodel.model").decode("utf-8", "replace")


def _outputs(sample, tmp_path):
    """Convert with defaults; multi-plate projects yield one file per plate."""
    out = tmp_path / "out.3mf"
    result = convert_file(sample, str(out))
    assert result.outputs and all(os.path.exists(p) for p in result.outputs)
    return result


def test_output_loads_and_is_wellformed(sample, tmp_path):
    result = _outputs(sample, tmp_path)
    assert result.stats.objects_written > 0
    assert result.stats.build_items_written > 0
    project = describe(sample)
    assert len(result.outputs) == max(1, len(project.plate_ids))
    # output must be a valid zip with the core parts
    for path in result.outputs:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            assert "3D/3dmodel.model" in names
            assert "[Content_Types].xml" in names
            assert "_rels/.rels" in names


def test_build_transforms_preserved(sample, tmp_path):
    """Rotation/scale and Z are untouched; X/Y only move by the plate origin."""
    project = describe(sample)
    out = tmp_path / "out.3mf"
    convert_file(sample, str(out), plate_layout="beds")

    def transforms(text):
        out = []
        for m in re.findall(r"<item [^>]*>", text):
            t = re.search(r'transform="([^"]+)"', m)
            if t:
                nums = [float(x) for x in t.group(1).split()]
                out.append((tuple(round(v, 6) for v in nums[:9]), round(nums[11], 6)))
        return sorted(out)

    assert transforms(_read_model(sample)) == transforms(_read_model(str(out)))
    if len(project.plate_ids) <= 1:
        # single plate: build item transforms are copied verbatim
        def raw(text):
            return sorted(
                re.search(r'transform="([^"]+)"', m).group(1)
                for m in re.findall(r"<item [^>]*>", text)
                if re.search(r'transform="([^"]+)"', m)
            )

        assert raw(_read_model(sample)) == raw(_read_model(str(out)))


def test_no_bambu_or_production_leakage(sample, tmp_path):
    for path in _outputs(sample, tmp_path).outputs:
        model = _read_model(path)
        assert "paint_color" not in model  # renamed to mmu_segmentation
        assert "paint_seam" not in model
        assert "bambulab.com" not in model
        assert 'p:path' not in model  # components inlined -> no cross-file refs


def test_components_reference_earlier_ids(sample, tmp_path):
    """3MF core spec: a component must reference an object defined earlier in the file."""
    import lxml.etree as ET

    for path in _outputs(sample, tmp_path).outputs:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("3D/3dmodel.model")).getroottree().getroot()
        seen = set()
        for obj in root.iter(ns.q(ns.CORE, "object")):
            for comp in obj.iter(ns.q(ns.CORE, "component")):
                assert comp.get("objectid") in seen, (
                    f"component references id {comp.get('objectid')} not yet defined"
                )
            seen.add(obj.get("id"))


def test_plate_selection_subsets(sample, tmp_path):
    project = describe(sample)
    if len(project.plate_ids) < 2:
        pytest.skip("sample has fewer than 2 plates")
    first = project.plate_ids[0]
    out_all = tmp_path / "all.3mf"
    out_one = tmp_path / "one.3mf"
    r_all = convert_file(sample, str(out_all))
    r_one = convert_file(sample, str(out_one), plates=[first])
    assert r_one.stats.build_items_written < r_all.stats.build_items_written
    assert r_one.stats.build_items_written > 0
