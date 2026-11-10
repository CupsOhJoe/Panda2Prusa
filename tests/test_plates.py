"""Multi-plate projects: plate origins, bed placement and per-plate output files.

Uses a tiny synthetic Bambu project built on the fly, so no sample files are needed.
"""

import json
import os
import re
import zipfile

import pytest

from panda2prusa.convert import PLATES_BEDS, convert_file
from panda2prusa.reader import plate_origin, read_bambu_3mf

BED = 256.0
STRIDE = BED * 1.2  # Bambu plate pitch


def _cube(size=10.0):
    v = [(0, 0, 0), (size, 0, 0), (size, size, 0), (0, size, 0), (0, 0, size), (size, 0, size), (size, size, size), (0, size, size)]
    tris = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    verts = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in v)
    faces = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
    return f"<mesh><vertices>{verts}</vertices><triangles>{faces}</triangles></mesh>"


def make_project(path, plates_of_objects):
    """plates_of_objects: list of (object_id, plate_no, extruder, local_x, local_y)."""
    n_plates = max(p for _, p, _, _, _ in plates_of_objects)
    objects = "".join(
        f'<object id="{oid}" type="model">{_cube()}</object>' for oid, _, _, _, _ in plates_of_objects
    )
    items = ""
    for oid, plate, _ext, lx, ly in plates_of_objects:
        ox, oy = plate_origin(plate - 1, n_plates, BED, BED)
        items += f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {lx + ox:g} {ly + oy:g} 5" printable="1"/>'
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Application">BambuStudio-02.08.00.50</metadata>'
        f"<resources>{objects}</resources><build>{items}</build></model>"
    )
    settings = '<?xml version="1.0" encoding="UTF-8"?><config>'
    for oid, _plate, ext, _lx, _ly in plates_of_objects:
        settings += (
            f'<object id="{oid}"><metadata key="name" value="cube {oid}"/><metadata key="extruder" value="{ext}"/>'
            f'<part id="{oid}" subtype="normal_part"><metadata key="name" value="cube {oid}"/></part></object>'
        )
    for plate in range(1, n_plates + 1):
        settings += f'<plate><metadata key="plater_id" value="{plate}"/>'
        for oid, p, _e, _x, _y in plates_of_objects:
            if p == plate:
                settings += f'<model_instance><metadata key="object_id" value="{oid}"/><metadata key="instance_id" value="0"/></model_instance>'
        settings += "</plate>"
    settings += "</config>"
    project = {
        "printable_area": ["0x0", "256x0", "256x256", "0x256"],
        "printable_height": "256",
        "nozzle_diameter": ["0.4"],
        "layer_height": "0.2",
        "wall_loops": "2",
        "filament_colour": ["#FF0000", "#00FF00", "#0000FF"],
        "filament_type": ["PLA", "PLA", "PLA"],
        "filament_settings_id": ["A", "B", "C"],
        "print_settings_id": "0.20mm Standard @BBL P1S",
        "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
        "curr_bed_type": "Textured PEI Plate",
        "wipe_tower_x": ["200", "200"],
        "wipe_tower_y": ["200", "200"],
    }
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/project_settings.config", json.dumps(project))
    return str(path)


def _items(path):
    with zipfile.ZipFile(path) as z:
        text = z.read("3D/3dmodel.model").decode()
    out = []
    for m in re.finditer(r'<item objectid="(\d+)"[^>]*transform="([^"]+)"', text):
        nums = [float(x) for x in m.group(2).split()]
        out.append((m.group(1), nums[9], nums[10]))
    return out


@pytest.mark.parametrize(
    "index, count, expected",
    [
        (0, 1, (0.0, 0.0)),
        (1, 2, (STRIDE, 0.0)),
        (2, 3, (0.0, -STRIDE)),  # 2 columns (ceil(sqrt 3)): third plate starts row two
        (2, 4, (0.0, -STRIDE)),
        (3, 4, (STRIDE, -STRIDE)),
        (4, 5, (STRIDE, -STRIDE)),  # 3 columns
    ],
)
def test_plate_origin_grid(index, count, expected):
    assert plate_origin(index, count, BED, BED) == pytest.approx(expected)


@pytest.fixture
def two_plates(tmp_path):
    return make_project(
        tmp_path / "two.3mf",
        [(1, 1, 1, 100.0, 100.0), (2, 2, 2, 50.0, 60.0), (3, 2, 3, 150.0, 60.0)],
    )


@pytest.fixture
def xl_printer_ini(tmp_path):
    p = tmp_path / "xl.ini"
    p.write_text(
        "printer_settings_id = Test XL\nbed_shape = 0x0,360x0,360x360,0x360\nmax_print_height = 360\n"
        "nozzle_diameter = 0.4,0.4,0.4\nretract_length = 0.8,0.8,0.8\ngcode_flavor = marlin2\n",
        encoding="utf-8",
    )
    return str(p)


def test_reader_computes_plate_origins(two_plates):
    project = read_bambu_3mf(two_plates)
    assert project.plate_ids == [1, 2]
    assert project.bed_size == (256.0, 256.0)
    assert project.plate_origins[1] == (0.0, 0.0)
    assert project.plate_origins[2] == pytest.approx((STRIDE, 0.0))
    assert project.plate_of_object == {"1": 1, "2": 2, "3": 2}


def test_single_plate_selection_is_moved_to_bed_origin(two_plates, tmp_path, xl_printer_ini):
    out = tmp_path / "plate2.3mf"
    r = convert_file(two_plates, str(out), plates=[2], printer=xl_printer_ini)
    assert r.stats.build_items_written == 2 and r.stats.beds_used == 1
    assert sorted(_items(out)) == [("1", pytest.approx(50.0), pytest.approx(60.0)), ("2", pytest.approx(150.0), pytest.approx(60.0))]


def test_all_plates_go_onto_successive_prusa_beds(two_plates, tmp_path, xl_printer_ini):
    out = tmp_path / "all.3mf"
    r = convert_file(two_plates, str(out), printer=xl_printer_ini, plate_layout=PLATES_BEDS)
    assert r.stats.build_items_written == 3 and r.stats.beds_used == 2
    items = dict((oid, (x, y)) for oid, x, y in _items(out))
    assert items["1"] == (pytest.approx(100.0), pytest.approx(100.0))
    # plate 2 sits on PrusaSlicer bed 2: 1.2 x the *target* bed width (360) to the right
    assert items["2"] == (pytest.approx(50.0 + 360 * 1.2), pytest.approx(60.0))
    assert items["3"] == (pytest.approx(150.0 + 360 * 1.2), pytest.approx(60.0))
    assert any("beds 1-2" in w for w in r.stats.warnings)


def test_bambu_printer_mode_uses_bambu_bed_width(two_plates, tmp_path):
    out = tmp_path / "all_bambu.3mf"
    convert_file(two_plates, str(out), printer="bambu", plate_layout=PLATES_BEDS)
    items = dict((oid, (x, y)) for oid, x, y in _items(out))
    assert items["2"] == (pytest.approx(50.0 + STRIDE), pytest.approx(60.0))


def test_default_writes_one_file_per_plate(two_plates, tmp_path, xl_printer_ini):
    out = tmp_path / "sub" / "clicker.3mf"
    os.makedirs(out.parent)
    r = convert_file(two_plates, str(out), printer=xl_printer_ini)
    assert [os.path.basename(p) for p in r.outputs] == ["clicker_plate1.3mf", "clicker_plate2.3mf"]
    assert all(os.path.exists(p) for p in r.outputs)
    assert r.stats.build_items_written == 3  # aggregated over both files
    assert _items(r.outputs[0]) == [("1", pytest.approx(100.0), pytest.approx(100.0))]
    assert sorted(x for _, x, _ in _items(r.outputs[1])) == [pytest.approx(50.0), pytest.approx(150.0)]
    # each file carries the full settings and the plate's wipe tower position
    with zipfile.ZipFile(r.outputs[1]) as z:
        names = z.namelist()
        assert "Metadata/Slic3r_PE.config" in names
        assert "Metadata/Slic3r_PE_model.config" in names


def test_no_settings_mode_still_rebases_plates(two_plates, tmp_path):
    out = tmp_path / "geom.3mf"
    convert_file(two_plates, str(out), plates=[2], settings=False)
    assert sorted(x for _, x, _ in _items(out)) == [pytest.approx(50.0), pytest.approx(150.0)]


def test_single_plate_project_keeps_its_name_and_positions(tmp_path):
    src = make_project(tmp_path / "one.3mf", [(1, 1, 1, 30.0, 40.0)])
    out = tmp_path / "one_prusa.3mf"
    r = convert_file(src, str(out), printer="bambu")
    assert r.outputs == [str(out)]
    assert _items(out) == [("1", pytest.approx(30.0), pytest.approx(40.0))]
