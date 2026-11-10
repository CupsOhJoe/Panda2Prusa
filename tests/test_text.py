"""Bambu Studio text parts stay editable in PrusaSlicer's text tool.

A synthetic project with a base cube and a text part (its own transform and a Bambu
``<text_info>``) is converted and the PrusaSlicer volume metadata is checked.
"""

import re
import zipfile

import lxml.etree as ET
import pytest

from panda2prusa.convert import convert_file
from panda2prusa.reader import read_bambu_3mf
from panda2prusa.transform import Transform

TEXT_TRANSFORM = "0 1 0 -1 0 0 0 0 1 10 20 5"  # 90 deg about Z, then translate (10, 20, 5)


def _cube(size=10.0):
    v = [(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0), (0, 0, size), (size, 0, size), (size, size, size), (0, size, size)]
    tris = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return f"<mesh><vertices>{verts}</vertices><triangles>{faces}</triangles></mesh>"


def make_text_project(path, text_info_attrs: str):
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Application">BambuStudio-02.08.02.61</metadata>'
        "<resources>"
        f'<object id="2" type="model">{_cube()}</object>'
        f'<object id="3" type="model">{_cube(4.0)}</object>'
        '<object id="1" type="model"><components>'
        '<component objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        f'<component objectid="3" transform="{TEXT_TRANSFORM}"/>'
        "</components></object>"
        "</resources>"
        '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 100 0" printable="1"/></build></model>'
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8"?><config>'
        '<object id="1"><metadata key="name" value="sign"/><metadata key="extruder" value="1"/>'
        '<part id="2" subtype="normal_part"><metadata key="name" value="base"/><metadata key="extruder" value="1"/></part>'
        '<part id="3" subtype="normal_part"><metadata key="name" value="text_shape"/><metadata key="extruder" value="2"/>'
        f"<text_info {text_info_attrs} />"
        "</part></object>"
        '<plate><metadata key="plater_id" value="1"/>'
        '<model_instance><metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/></model_instance>'
        "</plate></config>"
    )
    project = {
        "printable_area": ["0x0", "256x0", "256x256", "0x256"],
        "printable_height": "256",
        "nozzle_diameter": ["0.4"],
        "layer_height": "0.2",
        "filament_colour": ["#FF0000", "#00FF00"],
        "filament_type": ["PLA", "PLA"],
        "filament_settings_id": ["A", "B"],
        "print_settings_id": "0.20mm Standard @BBL P1S",
        "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
    }
    import json

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/project_settings.config", json.dumps(project))
    return str(path)


BAMBU_2X_TEXT = (
    'text="I&apos;ve heard it &amp; seen it" font_name="Arial Black" font_version="2.5" style_name="Recommend" '
    'boldness="0" skew="0" font_index="-1" font_size="11.000003814697266" thickness="0.80000001192092896" '
    'embeded_depth="0.60000002384185791" rotate_angle="0" text_gap="0.05000000074505806" bold="0" italic="1" '
    'surface_type="1" hit_mesh="0" hit_position="0 0 0" hit_normal="0 0 0"'
)


@pytest.fixture
def converted(tmp_path):
    src = make_text_project(tmp_path / "text.3mf", BAMBU_2X_TEXT)
    out = tmp_path / "text_prusa.3mf"
    result = convert_file(src, str(out), printer="bambu")
    with zipfile.ZipFile(out) as z:
        cfg = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
        model = z.read("3D/3dmodel.model").decode()
    return result, cfg, model


def _volume_named(cfg, name):
    for vol in cfg.iter("volume"):
        names = [m.get("value") for m in vol.findall("metadata") if m.get("key") == "name"]
        if name in names:
            return vol
    raise AssertionError(f"no volume named {name!r}")


def _meta(vol, key):
    for m in vol.findall("metadata"):
        if m.get("key") == key:
            return m.get("value")
    return None


def test_reader_keeps_text_info():
    import tempfile, os

    d = tempfile.mkdtemp()
    src = make_text_project(os.path.join(d, "t.3mf"), BAMBU_2X_TEXT)
    project = read_bambu_3mf(src)
    parts = project.settings_objects["1"].parts
    assert parts[0].text is None
    assert parts[1].text["text"] == "I've heard it & seen it"
    assert parts[1].text["font_name"] == "Arial Black"


def test_text_volume_gets_prusaslicer_text_and_shape(converted):
    result, cfg, _ = converted
    assert result.stats.text_volumes == 1
    vol = _volume_named(cfg, "text_shape")
    text = vol.find("{http://schemas.slic3r.org/3mf/2017/06}text")
    shape = vol.find("{http://schemas.slic3r.org/3mf/2017/06}shape")
    assert text is not None and shape is not None
    assert text.get("text") == "I've heard it & seen it"
    assert text.get("face_name") == "Arial Black"
    assert text.get("line_height") == "11"
    assert text.get("style") == "italic" and text.get("weight") == "normal"
    assert text.get("font_descriptor_type") == "wxFontDescriptor_Windows"
    assert text.get("font_descriptor").endswith(";400;1;0;0;1;0;0;2;0;Arial Black")
    assert text.get("horizontal") == "center" and text.get("vertical") == "middle"
    assert "char_gap" not in text.attrib  # 0.05 font points rounds to nothing
    assert shape.get("depth") == "1.4"  # thickness + embedded depth = the Bambu mesh height
    assert shape.get("use_surface") == "1"
    assert float(shape.get("scale")) > 0
    # the base part carries no text data
    base = _volume_named(cfg, "base")
    assert base.find("{http://schemas.slic3r.org/3mf/2017/06}text") is None


def test_text_element_is_serialised_with_prusaslicer_prefix(converted):
    _, _, _ = converted
    # PrusaSlicer's parser matches the literal tag names "slic3rpe:text" / "slic3rpe:shape"
    out_text = ET.tostring(converted[1]).decode()
    assert "<slic3rpe:text " in out_text and "<slic3rpe:shape " in out_text


def test_volume_matrix_restores_the_part_frame(converted):
    _, cfg, model = converted
    # PrusaSlicer stores volume vertices transformed and the part transform as a 4x4
    # row-major matrix with the translation in the last column.
    assert _meta(_volume_named(cfg, "base"), "matrix") == "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
    assert _meta(_volume_named(cfg, "text_shape"), "matrix") == "0 -1 0 10 1 0 0 20 0 0 1 5 0 0 0 1"
    # ...and the text part's vertices really are baked: the 4 mm cube now sits at z 5..9
    vol = _volume_named(cfg, "text_shape")
    first, last = int(vol.get("firstid")), int(vol.get("lastid"))
    tris = re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', model)
    verts = [tuple(float(x) for x in m) for m in re.findall(r'<vertex x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', model)]
    used = {int(i) for t in tris[first:last + 1] for i in t}
    zs = {verts[i][2] for i in used}
    assert zs == {5.0, 9.0}
    xs = {round(verts[i][0], 6) for i in used}
    assert xs == {6.0, 10.0}  # x' = -y + 10 for y in {0, 4}


def test_old_bambu_text_attributes_are_understood(tmp_path):
    old = (
        'text="Hello" font_name="Arial" font_index="0" font_size="16" thickness="2" embeded_depth="0" '
        'rotate_angle="0" text_gap="3" bold="1" italic="0" surface_text="0" keep_horizontal="0" '
        'hit_mesh="0" hit_position="0 0 0" hit_normal="0 0 0"'
    )
    src = make_text_project(tmp_path / "old.3mf", old)
    out = tmp_path / "old_prusa.3mf"
    convert_file(src, str(out), printer="bambu")
    with zipfile.ZipFile(out) as z:
        cfg = ET.fromstring(z.read("Metadata/Slic3r_PE_model.config"))
    vol = _volume_named(cfg, "text_shape")
    text = vol.find("{http://schemas.slic3r.org/3mf/2017/06}text")
    shape = vol.find("{http://schemas.slic3r.org/3mf/2017/06}shape")
    assert text.get("weight") == "bold" and text.get("style") == "normal"
    assert text.get("font_descriptor").split(";")[6] == "700"
    assert text.get("char_gap") == "3"
    assert shape.get("depth") == "2" and "use_surface" not in shape.attrib


def test_transform_to_matrix16_is_the_transpose():
    assert Transform.identity().to_matrix16() == "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
    assert Transform.parse(TEXT_TRANSFORM).to_matrix16() == "0 -1 0 10 1 0 0 20 0 0 1 5 0 0 0 1"
