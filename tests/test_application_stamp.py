"""The model file's "Application" stamp decides how PrusaSlicer 3 opens a converted file.

PrusaSlicer 3 passes a 3mf to its 2.x project reader (Slic3r_PE.config and
Slic3r_PE_model.config) only when the stamp is "PrusaSlicer-<version>" with a 2.x
version; everything else is opened as plain geometry ("The 3MF file does not contain
PrusaSlicer configuration"). That reader in turn rejects a project whose printer model
it does not know, so only files that carry a PrusaSlicer printer profile get the stamp.
"""

import re
import zipfile

import pytest

from panda2prusa.convert import convert_file
from test_plates import make_project

# is_project_3mf() in PrusaSlicer's 3mf.cpp: looked for in the first 1024 bytes of the model.
_PRUSA_STAMP = re.compile(r'^\s*<metadata name="Application".*?PrusaSlicer-(.*?)</metadata>$', re.M)
_HEADER_BYTES = 1024
# Model3mf.cpp in PrusaSlicer 3: the last version its 2.x reader takes.
_LAST_LEGACY_VERSION = (2, 99, 1)
# Plater.cpp in PrusaSlicer 2.9: older projects are rearranged onto the beds on load.
_FIRST_MULTI_BED_VERSION = (2, 9, 0)


def _header(path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("3D/3dmodel.model")[:_HEADER_BYTES].decode("utf-8", "replace")


def _application(path) -> str:
    with zipfile.ZipFile(path) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8", "replace")
    found = re.findall(r'<metadata name="Application">([^<]*)</metadata>', model)
    assert len(found) == 1, found
    return found[0]


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path / "in.3mf", [(1, 1, 1, 100.0, 100.0), (2, 1, 2, 50.0, 60.0)])


def _printer_ini(tmp_path, model_line="printer_model = XL2IS\n"):
    p = tmp_path / "printer.ini"
    p.write_text(
        "printer_settings_id = Test XL\n" + model_line + "bed_shape = 0x0,360x0,360x360,0x360\n"
        "max_print_height = 360\nnozzle_diameter = 0.4,0.4\nretract_length = 0.8,0.8\ngcode_flavor = marlin2\n",
        encoding="utf-8",
    )
    return str(p)


@pytest.mark.parametrize("bambu_settings", [False, True])
def test_project_for_a_prusa_printer_is_stamped_as_a_prusaslicer_2_project(project, tmp_path, bambu_settings):
    out = tmp_path / "out.3mf"
    convert_file(project, str(out), printer=_printer_ini(tmp_path), bambu_settings=bambu_settings)
    with zipfile.ZipFile(out) as z:
        assert "Metadata/Slic3r_PE.config" in z.namelist()
    m = _PRUSA_STAMP.search(_header(out))
    assert m, _header(out)
    version = tuple(int(x) for x in m.group(1).split("."))
    assert _FIRST_MULTI_BED_VERSION <= version <= _LAST_LEGACY_VERSION
    assert _application(out) == "PrusaSlicer-" + m.group(1)


def test_stamp_stays_in_the_header_behind_a_long_title(project, tmp_path):
    titled = tmp_path / "titled.3mf"
    with zipfile.ZipFile(project) as zin, zipfile.ZipFile(titled, "w") as zout:
        for name in zin.namelist():
            data = zin.read(name)
            if name == "3D/3dmodel.model":
                title = '<metadata name="Title">' + "long title " * 200 + "</metadata>"
                data = data.replace(b"<resources>", title.encode() + b"<resources>")
            zout.writestr(name, data)
    out = tmp_path / "out.3mf"
    convert_file(str(titled), str(out), printer=_printer_ini(tmp_path))
    with zipfile.ZipFile(out) as z:
        assert b"long title long title" in z.read("3D/3dmodel.model")
    assert _PRUSA_STAMP.search(_header(out))


def test_bambu_printer_mode_is_not_stamped_as_a_prusaslicer_project(project, tmp_path):
    out = tmp_path / "out.3mf"
    convert_file(project, str(out), printer="bambu")
    with zipfile.ZipFile(out) as z:
        assert "Metadata/Slic3r_PE.config" in z.namelist()
    assert _application(out) == "panda2prusa"


def test_printer_profile_without_a_model_is_not_stamped(project, tmp_path):
    out = tmp_path / "out.3mf"
    convert_file(project, str(out), printer=_printer_ini(tmp_path, model_line=""))
    assert _application(out) == "panda2prusa"


def test_geometry_only_is_not_stamped_as_a_prusaslicer_project(project, tmp_path):
    out = tmp_path / "out.3mf"
    convert_file(project, str(out), settings=False)
    with zipfile.ZipFile(out) as z:
        assert "Metadata/Slic3r_PE.config" not in z.namelist()
    assert _application(out) == "panda2prusa"
