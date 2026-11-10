"""Write a PrusaSlicer-compatible .3mf from a BambuProject.

Strategy: emit one *merged mesh* per build object — exactly the shape PrusaSlicer's
own writer produces. Each Bambu part (component) becomes a contiguous triangle range
in the merged mesh, recorded as a ``<volume firstid=.. lastid=..>`` entry in
``Metadata/Slic3r_PE_model.config`` carrying the part's name, type, extruder and
per-part settings. This is the only representation in which PrusaSlicer honors
per-part extruder assignments; component-based objects collapse to a single volume
on import and lose them.

Component transforms are baked into the vertex coordinates (volumes therefore need
no matrix metadata), build-item transforms are preserved verbatim, and per-triangle
paint data travels with its triangle: Bambu's ``paint_color`` / ``paint_supports`` /
``paint_seam`` attributes are kept byte-for-byte under PrusaSlicer's ``slic3rpe:``
names, since Bambu Studio forked PrusaSlicer's TriangleSelector and the RLE
bitstream format is shared.

Alongside the geometry the writer emits the translated project settings
(``Metadata/Slic3r_PE.config``), per-layer colour changes / pauses, layer height
ranges and variable layer height profiles, and the wipe tower position.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from xml.sax.saxutils import quoteattr

import lxml.etree as ET

from . import ns
from .paint import transform_states, used_states, PaintError
from .reader import BambuProject, Part, PLATE_GAP, ROOT_MODEL
from .transform import Transform
from .translate import TargetPresets, Translation, translate_object_settings, translate_project

# Attributes to strip from triangles (production + bambu extensions).
_DROP_ATTRS = {
    ns.q(ns.PRODUCTION, "UUID"),
    ns.q(ns.PRODUCTION, "path"),
    "face_property",
}

# Bambu per-triangle paint attribute -> PrusaSlicer attribute, painting-version metadata.
_PAINT_ATTRS = {
    "paint_color": (ns.q(ns.SLIC3RPE, "mmu_segmentation"), "slic3rpe:MmPaintingVersion"),
    "paint_supports": (ns.q(ns.SLIC3RPE, "custom_supports"), "slic3rpe:FdmSupportsPaintingVersion"),
    "paint_seam": (ns.q(ns.SLIC3RPE, "custom_seam"), "slic3rpe:SeamPaintingVersion"),
    "paint_fuzzy_skin": (ns.q(ns.SLIC3RPE, "fuzzy_skin"), "slic3rpe:FuzzySkinPaintingVersion"),
}

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
    ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
    ' <Default Extension="png" ContentType="image/png"/>\n'
    "</Types>\n"
)

PRINT_CONFIG_FILE = "Metadata/Slic3r_PE.config"
MODEL_CONFIG_FILE = "Metadata/Slic3r_PE_model.config"
CUSTOM_GCODE_FILE = "Metadata/Prusa_Slicer_custom_gcode_per_print_z.xml"
LAYER_RANGES_FILE = "Metadata/Prusa_Slicer_layer_config_ranges.xml"
LAYER_HEIGHTS_FILE = "Metadata/Slic3r_PE_layer_heights_profile.txt"
WIPE_TOWER_FILE = "Metadata/Prusa_Slicer_wipe_tower_information.xml"


@dataclass
class WriteStats:
    objects_written: int = 0
    volumes_written: int = 0
    build_items_written: int = 0
    painted_triangles: int = 0
    plates_included: Optional[list] = None
    extruder_map: Optional[dict] = None  # {bambu filament -> prusa extruder} if remapped
    settings_written: bool = False
    extruders: int = 0
    printer_name: str = ""
    print_name: str = ""
    filament_names: List[str] = field(default_factory=list)
    custom_gcodes_written: int = 0
    layer_ranges_written: int = 0
    text_volumes: int = 0  # text parts written with PrusaSlicer's editable text data
    beds_used: int = 1  # plates placed on separate PrusaSlicer beds in this file
    warnings: List[str] = field(default_factory=list)


@dataclass
class _Volume:
    firstid: int
    lastid: int
    leaf_oid: str
    name: str = ""
    extruder: Optional[str] = None
    volume_type: str = "ModelPart"
    settings: Dict[str, str] = field(default_factory=dict)
    text: Optional[Dict[str, str]] = None  # Bambu text_info attributes for a text part
    transform: Transform = field(default_factory=Transform.identity)  # part -> object


@dataclass
class _MergedObject:
    element: object
    volumes: list = field(default_factory=list)
    painted: int = 0
    paint_kinds: set = field(default_factory=set)


def _key(path: str, objectid: str) -> tuple:
    return (path or ROOT_MODEL, objectid)


def _collect_objects(project: BambuProject):
    """Map (model_path, object_id) -> lxml <object> element across all files."""
    objs = {}
    src_path = {}
    trees = {ROOT_MODEL: project.root_tree}
    trees.update(project.object_trees)
    for path, tree in trees.items():
        for obj in tree.getroot().iter(ns.q(ns.CORE, "object")):
            oid = obj.get("id")
            if oid is not None:
                objs[_key(path, oid)] = obj
                src_path[_key(path, oid)] = path
    return objs, src_path


def _component_target(comp, current_path: str) -> tuple:
    p = comp.get(ns.q(ns.PRODUCTION, "path"))
    from .reader import _normalize_path

    path = _normalize_path(p) if p else current_path
    return _key(path, comp.get("objectid"))


def _flatten_leaves(key, objs, src_path, cum=None, _depth=0):
    """DFS a component tree into mesh leaves: [(leaf_key, cumulative Transform)]."""
    if cum is None:
        cum = Transform.identity()
    obj = objs.get(key)
    if obj is None or _depth > 16:
        return []
    leaves = []
    if obj.find(ns.q(ns.CORE, "mesh")) is not None:
        leaves.append((key, cum))
    comps = obj.find(ns.q(ns.CORE, "components"))
    if comps is not None:
        for comp in comps.findall(ns.q(ns.CORE, "component")):
            t = Transform.parse(comp.get("transform"))
            leaves.extend(
                _flatten_leaves(
                    _component_target(comp, src_path[key]),
                    objs,
                    src_path,
                    t.compose(cum),
                    _depth + 1,
                )
            )
    return leaves


def _fmt(v: float) -> str:
    """Shortest round-trip decimal for a coordinate."""
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _merge_object(root_key, objs, src_path, parts: list) -> Optional[_MergedObject]:
    """Merge a build object's component tree into a single-mesh <object> element."""
    leaves = _flatten_leaves(root_key, objs, src_path)
    if not leaves:
        return None

    # Match Bambu parts (name/extruder/type/settings) to leaves: by id when possible, else by order.
    part_by_id = {p.id: p for p in parts}

    def part_for(leaf_idx: int, leaf_oid: str) -> Optional[Part]:
        if leaf_oid in part_by_id:
            return part_by_id[leaf_oid]
        if len(parts) == len(leaves):
            return parts[leaf_idx]
        return None

    out_obj = ET.Element(ns.q(ns.CORE, "object"))
    out_obj.set("type", "model")
    src_root = objs[root_key]
    if src_root.get("name"):
        out_obj.set("name", src_root.get("name"))
    mesh = ET.SubElement(out_obj, ns.q(ns.CORE, "mesh"))
    out_verts = ET.SubElement(mesh, ns.q(ns.CORE, "vertices"))
    out_tris = ET.SubElement(mesh, ns.q(ns.CORE, "triangles"))

    merged = _MergedObject(element=out_obj)
    vbase = 0
    tri_count = 0
    for leaf_idx, (leaf_key, xf) in enumerate(leaves):
        leaf = objs[leaf_key]
        lmesh = leaf.find(ns.q(ns.CORE, "mesh"))
        verts = lmesh.find(ns.q(ns.CORE, "vertices"))
        tris = lmesh.find(ns.q(ns.CORE, "triangles"))
        identity = xf.is_identity(tol=1e-9)
        flip = xf.det3() < 0  # reflection: reverse winding to keep outward normals

        nverts = 0
        if verts is not None:
            for v in verts.findall(ns.q(ns.CORE, "vertex")):
                nv = ET.SubElement(out_verts, ns.q(ns.CORE, "vertex"))
                if identity:
                    nv.set("x", v.get("x", "0"))
                    nv.set("y", v.get("y", "0"))
                    nv.set("z", v.get("z", "0"))
                else:
                    x, y, z = xf.apply_point(
                        float(v.get("x", "0")), float(v.get("y", "0")), float(v.get("z", "0"))
                    )
                    nv.set("x", _fmt(x))
                    nv.set("y", _fmt(y))
                    nv.set("z", _fmt(z))
                nverts += 1

        firstid = tri_count
        if tris is not None:
            for t in tris.findall(ns.q(ns.CORE, "triangle")):
                nt = ET.SubElement(out_tris, ns.q(ns.CORE, "triangle"))
                v1, v2, v3 = t.get("v1"), t.get("v2"), t.get("v3")
                if flip:
                    v1, v2 = v2, v1
                nt.set("v1", str(int(v1) + vbase))
                nt.set("v2", str(int(v2) + vbase))
                nt.set("v3", str(int(v3) + vbase))
                for attr, val in t.attrib.items():
                    if attr in ("v1", "v2", "v3") or attr in _DROP_ATTRS:
                        continue
                    if attr.startswith("{" + ns.BAMBU + "}"):
                        continue
                    paint = _PAINT_ATTRS.get(attr)
                    if paint is not None:
                        if val:
                            nt.set(paint[0], val)
                            merged.paint_kinds.add(attr)
                            if attr == "paint_color":
                                merged.painted += 1
                    else:
                        nt.set(attr, val)
                tri_count += 1
        vbase += nverts

        if tri_count > firstid:
            part = part_for(leaf_idx, leaf_key[1])
            merged.volumes.append(
                _Volume(
                    firstid=firstid,
                    lastid=tri_count - 1,
                    leaf_oid=leaf_key[1],
                    name=(part.name if part else "") or leaf.get("name", ""),
                    extruder=part.extruder if part else None,
                    volume_type=part.volume_type if part else "ModelPart",
                    settings=dict(part.settings) if part else {},
                    text=dict(part.text) if part and part.text else None,
                    transform=xf,
                )
            )
    return merged


def _paint_attr():
    return ns.q(ns.SLIC3RPE, "mmu_segmentation")


def _used_slots(project: BambuProject, merged: dict) -> set:
    """Bambu filament slots referenced by the objects being written."""
    used: set = set()
    paint_strings: set = set()
    for key, mo in merged.items():
        so = project.settings_objects.get(key[1])
        if so and so.extruder:
            try:
                used.add(int(so.extruder))
            except ValueError:
                pass
        for vol in mo.volumes:
            if vol.extruder:
                try:
                    used.add(int(vol.extruder))
                except ValueError:
                    pass
        for tri in mo.element.iter(ns.q(ns.CORE, "triangle")):
            code = tri.get(_paint_attr())
            if code:
                paint_strings.add(code)
    for code in paint_strings:
        try:
            used |= used_states(code) - {0}
        except PaintError:
            pass
    used.discard(0)
    if not used and merged:
        used.add(1)  # everything prints with filament 1 by default
    return used


def _compute_extruder_map(project: BambuProject, merged: dict) -> Optional[dict]:
    """Map the filaments actually used to sequential extruders 1..K.

    Bambu AMS slots are sparse (a two-color print may use filaments 1 and 3); Prusa
    profiles only have extruders 1..N, so anything past the physical tool count is
    silently unprintable. Compacting {1, 3} -> {1: 1, 3: 2} makes a K-color model
    land on the first K tools. Returns None when already sequential.
    """
    used = _used_slots(project, merged)
    if not used:
        return None
    mapping = {old: i for i, old in enumerate(sorted(used), start=1)}
    if all(old == new for old, new in mapping.items()):
        return None
    return mapping


def _apply_extruder_map(merged: dict, mapping: dict) -> None:
    """Rewrite volume extruders and paint bitstreams through the mapping."""
    recoded: dict = {}
    attr = _paint_attr()
    for mo in merged.values():
        for vol in mo.volumes:
            if vol.extruder:
                try:
                    vol.extruder = str(mapping.get(int(vol.extruder), int(vol.extruder)))
                except ValueError:
                    pass
        for tri in mo.element.iter(ns.q(ns.CORE, "triangle")):
            code = tri.get(attr)
            if not code:
                continue
            if code not in recoded:
                try:
                    recoded[code] = transform_states(code, mapping)
                except PaintError:
                    recoded[code] = code  # leave unreadable paint untouched
            tri.set(attr, recoded[code])


def write_prusa_3mf(
    project: BambuProject,
    out_path: str,
    plates: Optional[list] = None,
    extruder_map: Optional[dict] = None,
    compact_extruders: bool = True,
    target: Optional[TargetPresets] = None,
    translate_settings: bool = True,
) -> WriteStats:
    """Write ``project`` as a PrusaSlicer 3mf.

    ``target`` says which PrusaSlicer printer/print/filament presets the translated
    settings are layered on (see ``translate.TargetPresets``); None means "translate
    the Bambu printer settings too". ``translate_settings=False`` writes geometry,
    extruders and painting only (the 1.0 behaviour).
    """
    objs, src_path = _collect_objects(project)
    warnings: List[str] = []

    # Which build items (and thus root objects) to include, filtered by plate.
    selected_items = []
    for bi in project.build_items:
        plate = project.plate_of_object.get(bi.objectid)
        if plates is None or plate is None or plate in plates:
            selected_items.append(bi)

    # Merge each distinct build object once, preserving first-seen order.
    merged: dict = {}
    id_map: dict = {}
    for bi in selected_items:
        key = _key(ROOT_MODEL, bi.objectid)
        if key in merged or key not in objs:
            continue
        so = project.settings_objects.get(bi.objectid)
        mo = _merge_object(key, objs, src_path, so.parts if so else [])
        if mo is not None:
            merged[key] = mo
            id_map[key] = str(len(id_map) + 1)

    # Renumber filament slots: an explicit map wins; otherwise compact sparse
    # slots so a K-color model lands on extruders 1..K.
    used_slots = sorted(_used_slots(project, merged))
    if extruder_map is not None:
        extruder_map = {
            int(k): int(v) for k, v in extruder_map.items() if int(k) != int(v)
        } or None
    elif compact_extruders:
        extruder_map = _compute_extruder_map(project, merged)
    if extruder_map:
        _apply_extruder_map(merged, extruder_map)
    full_map = {s: (extruder_map or {}).get(s, s) for s in used_slots}

    # Translate the project settings.
    translation: Optional[Translation] = None
    if translate_settings and project.has_settings:
        plate_index = 0
        if plates:
            first = sorted(plates)[0]
            if first in project.plate_ids:
                plate_index = project.plate_ids.index(first)
        translation = translate_project(
            project.config, full_map, target or TargetPresets(), used_slots, plate_index
        )
        warnings.extend(translation.warnings)
    elif translate_settings:
        warnings.append("No Metadata/project_settings.config in the source; settings were not converted.")

    # Build the output model tree.
    nsmap = {None: ns.CORE, "slic3rpe": ns.SLIC3RPE}
    model = ET.Element(ns.q(ns.CORE, "model"), nsmap=nsmap)
    model.set("unit", "millimeter")
    model.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

    paint_kinds: set = set()
    for mo in merged.values():
        paint_kinds |= mo.paint_kinds
    _add_metadata(model, project, paint_kinds)

    resources = ET.SubElement(model, ns.q(ns.CORE, "resources"))
    painted_total = 0
    volumes_total = 0
    text_total = 0
    for key, mo in merged.items():
        mo.element.set("id", id_map[key])
        painted_total += mo.painted
        volumes_total += len(mo.volumes)
        text_total += sum(1 for v in mo.volumes if v.text)
        resources.append(mo.element)

    # Plates: Bambu keeps every plate's objects in one world layout (plates side by
    # side, 20 % apart). Move each selected plate back to its own origin, then put the
    # k-th selected plate on PrusaSlicer bed k: PrusaSlicer 2.9+ assigns objects spaced
    # 1.2 x bed width along X to successive virtual beds when a project loads.
    selected_plates = sorted(
        {project.plate_of_object[bi.objectid] for bi in selected_items if bi.objectid in project.plate_of_object}
    )
    bed_index = {p: i for i, p in enumerate(selected_plates)}
    bed_width = (_bed_width(translation.config) if translation else None) or project.bed_size[0]
    if len(selected_plates) > 1:
        warnings.append(
            f"{len(selected_plates)} plates were placed on PrusaSlicer beds 1-{len(selected_plates)} "
            "in one file: open it in the PrusaSlicer 2.9+ GUI (its command line is not bed-aware)"
        )

    build = ET.SubElement(model, ns.q(ns.CORE, "build"))
    items_written = 0
    for bi in selected_items:
        key = _key(ROOT_MODEL, bi.objectid)
        if key not in id_map:
            continue
        item = ET.SubElement(build, ns.q(ns.CORE, "item"))
        item.set("objectid", id_map[key])
        plate = project.plate_of_object.get(bi.objectid)
        origin = project.plate_origins.get(plate, (0.0, 0.0))
        shift = (-origin[0] + bed_index.get(plate, 0) * bed_width * (1.0 + PLATE_GAP), -origin[1])
        transform = _shifted_transform(bi.transform, shift)
        if transform.strip():
            item.set("transform", transform)
        item.set("printable", "1" if bi.printable else "0")
        items_written += 1

    model_bytes = ET.tostring(
        model, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )

    model_config = _build_model_config(project, merged, id_map, full_map, warnings, translate_settings)

    # Per-plate colour changes / pauses, layer ranges, variable layer heights.
    custom_gcode_xml = None
    layer_ranges_xml = None
    layer_heights_txt = None
    if translate_settings:
        pcfg = translation.config if translation else None
        custom_gcode_xml, n_codes = _build_custom_gcodes(project, plates, full_map, pcfg, warnings)
        layer_ranges_xml, n_ranges = _build_layer_ranges(project, selected_items, id_map, full_map, warnings)
        layer_heights_txt = _build_layer_heights(project, selected_items, id_map)
    else:
        n_codes = n_ranges = 0

    # Assemble the package.
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        thumb = _pick_thumbnail(project)
        z.writestr("_rels/.rels", _rels_xml(with_thumbnail=thumb is not None))
        z.writestr(ROOT_MODEL, model_bytes)
        if model_config:
            z.writestr(MODEL_CONFIG_FILE, model_config)
        if translation is not None:
            z.writestr(PRINT_CONFIG_FILE, translation.config.to_slic3r_pe_text())
            if translation.wipe_tower_pos and translation.config.get("wipe_tower") == "1":
                x, y, rot = translation.wipe_tower_pos
                z.writestr(
                    WIPE_TOWER_FILE,
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    f'<wipe_tower_information bed_idx="0" position_x="{x:g}" position_y="{y:g}" rotation_deg="{rot:g}"/>\n',
                )
        if custom_gcode_xml:
            z.writestr(CUSTOM_GCODE_FILE, custom_gcode_xml)
        if layer_ranges_xml:
            z.writestr(LAYER_RANGES_FILE, layer_ranges_xml)
        if layer_heights_txt:
            z.writestr(LAYER_HEIGHTS_FILE, layer_heights_txt)
        if thumb:
            z.writestr("Metadata/thumbnail.png", thumb)

    stats = WriteStats(
        objects_written=len(merged),
        volumes_written=volumes_total,
        build_items_written=items_written,
        painted_triangles=painted_total,
        plates_included=plates,
        extruder_map=extruder_map,
        settings_written=translation is not None,
        custom_gcodes_written=n_codes,
        layer_ranges_written=n_ranges,
        text_volumes=text_total,
        beds_used=max(1, len(selected_plates)),
        warnings=warnings,
    )
    if translation is not None:
        stats.extruders = translation.extruders
        stats.printer_name = translation.config.get("printer_settings_id", "")
        stats.print_name = translation.config.get("print_settings_id", "")
        stats.filament_names = translation.config.vector("filament_settings_id") or []
    return stats


def _shifted_transform(text: str, shift: tuple) -> str:
    """Add ``shift`` (dx, dy) to a 3MF transform's translation; untouched if zero."""
    if abs(shift[0]) < 1e-9 and abs(shift[1]) < 1e-9:
        return text
    t = Transform.parse(text)
    m = list(t.m)
    m[9] += shift[0]
    m[10] += shift[1]
    return Transform(tuple(m)).to_string()


def _bed_width(cfg) -> Optional[float]:
    xs = []
    for p in cfg.vector("bed_shape") or []:
        try:
            xs.append(float(p.split("x")[0]))
        except (ValueError, IndexError):
            return None
    return (max(xs) - min(xs)) if len(xs) >= 3 else None


def _add_metadata(model, project: BambuProject, paint_kinds: set) -> None:
    def meta(name, text):
        m = ET.SubElement(model, ns.q(ns.CORE, "metadata"))
        m.set("name", name)
        m.text = text

    meta("slic3rpe:Version3mf", "1")
    for attr, (_qname, version_key) in _PAINT_ATTRS.items():
        if attr in paint_kinds or attr == "paint_color":
            meta(version_key, "1")
    # Carry over a title if present.
    title = ""
    for md in project.root_tree.getroot().findall(ns.q(ns.CORE, "metadata")):
        if md.get("name") == "Title" and md.text:
            title = md.text
    if title:
        meta("Title", title)
    meta("Application", "panda2prusa")


def _cfg_meta(parent, mtype: str, mkey: str, value: str) -> None:
    m = ET.SubElement(parent, "metadata")
    m.set("type", mtype)
    m.set("key", mkey)
    m.set("value", value)


# PrusaSlicer's SHAPE_SCALE: glyph outlines are kept in 1/1000 mm. The slicer recomputes
# it from the font when the text is edited; it only has to be a sane non-zero value.
_TEXT_SHAPE_SCALE = "0.001"


def _f(v: Optional[str], default: float) -> float:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _g(v: float) -> str:
    return f"{v:g}"


def _windows_font_descriptor(face: str, bold: bool, italic: bool) -> str:
    """A wxWidgets native font description for Windows (what PrusaSlicer stores for a
    text style): version;pointSize;lfHeight;width;escapement;orientation;weight;italic;
    underline;strikeOut;charSet;outPrecision;clipPrecision;quality;pitchAndFamily;face."""
    return f"1;10;-13;0;0;0;{700 if bold else 400};{1 if italic else 0};0;0;1;0;0;2;0;{face}"


def _text_config_attrs(info: Dict[str, str]) -> tuple:
    """Bambu Studio ``text_info`` -> PrusaSlicer ``slic3rpe:text`` and ``slic3rpe:shape``
    attributes, so the part stays editable in PrusaSlicer's text tool.

    Bambu Studio 2.x embosses with the same engine as PrusaSlicer: the mesh is the text
    outline extruded by thickness + embedded depth, in the part's own frame. Sizes are
    in mm, text_gap is in font points, boldness/skew share PrusaSlicer's meaning."""
    face = (info.get("font_name") or "").strip()
    bold = info.get("bold") == "1"
    italic = info.get("italic") == "1"
    depth = _f(info.get("thickness"), 2.0) + _f(info.get("embeded_depth"), 0.0)
    if "surface_type" in info:
        # 0 flat, 1 on the surface, 2 on the surface keeping horizontal, 3 per character
        surface_type = int(_f(info.get("surface_type"), 0))
        use_surface = surface_type in (1, 2, 3)
        per_glyph = surface_type == 3
    else:  # Bambu Studio 1.x
        use_surface = info.get("surface_text") == "1"
        per_glyph = False
    text = {
        "text": info.get("text", ""),
        "style_name": face or "Bambu Studio text",
        "font_descriptor": _windows_font_descriptor(face, bold, italic) if face else "",
        "font_descriptor_type": "wxFontDescriptor_Windows" if face else "undefined",
    }
    char_gap = round(_f(info.get("text_gap"), 0.0))
    if char_gap:
        text["char_gap"] = str(char_gap)
    line_gap = round(_f(info.get("line_gap"), 0.0))
    if line_gap:
        text["line_gap"] = str(line_gap)
    text["line_height"] = _g(max(_f(info.get("font_size"), 10.0), 0.1))
    boldness = _f(info.get("boldness"), 0.0)
    if boldness:
        text["boldness"] = _g(boldness)
    skew = _f(info.get("skew"), 0.0)
    if skew:
        text["skew"] = _g(skew)
    if per_glyph:
        text["per_glyph"] = "1"
    text["horizontal"] = info.get("horizontal") if info.get("horizontal") in ("left", "center", "right") else "center"
    text["vertical"] = info.get("vertical") if info.get("vertical") in ("top", "middle", "bottom") else "middle"
    if face:
        text["face_name"] = face
    text["style"] = "italic" if italic else "normal"
    text["weight"] = "bold" if bold else "normal"
    shape = {"scale": _TEXT_SHAPE_SCALE, "depth": _g(max(depth, 0.01))}
    if use_surface:
        shape["use_surface"] = "1"
    return text, shape


def _build_model_config(
    project: BambuProject,
    merged: dict,
    id_map: dict,
    full_map: dict,
    warnings: list,
    translate_settings: bool,
) -> Optional[bytes]:
    """Emit PrusaSlicer object/volume config: names, types, extruders, per-part settings.

    Volumes are identified by firstid/lastid triangle ranges over the merged mesh,
    matching PrusaSlicer's native writer, so extruder assignments survive import.
    """
    root = ET.Element("config", nsmap={"slic3rpe": ns.SLIC3RPE})
    # Bambu's "flush into infill/objects" are print-wide; PrusaSlicer has them per object.
    global_object_settings: Dict[str, str] = {}
    if translate_settings and project.has_settings:
        for bkey in ("flush_into_infill", "flush_into_objects"):
            v = project.config.scalar(bkey)
            if v is not None:
                global_object_settings[bkey] = v
    for key, mo in merged.items():
        old_id = key[1]
        so = project.settings_objects.get(old_id)
        obj_el = ET.SubElement(root, "object")
        obj_el.set("id", id_map[key])
        name = (so.name if so else "") or mo.element.get("name", "")
        if name:
            _cfg_meta(obj_el, "object", "name", name)
        if so and so.extruder:
            ext = so.extruder
            try:
                ext = str(full_map.get(int(ext), int(ext)))
            except ValueError:
                pass
            _cfg_meta(obj_el, "object", "extruder", ext)
        if translate_settings:
            meta = dict(global_object_settings)
            if so:
                meta.update(so.settings)
            for k, v in sorted(translate_object_settings(meta, full_map, warnings, f"object {name or old_id}").items()):
                if k != "extruder":
                    _cfg_meta(obj_el, "object", k, v)
        for vol in mo.volumes:
            vol_el = ET.SubElement(obj_el, "volume")
            vol_el.set("firstid", str(vol.firstid))
            vol_el.set("lastid", str(vol.lastid))
            _cfg_meta(vol_el, "volume", "name", vol.name or name or f"part {vol.leaf_oid}")
            _cfg_meta(vol_el, "volume", "volume_type", vol.volume_type)
            if vol.volume_type == "ParameterModifier":
                _cfg_meta(vol_el, "volume", "modifier", "1")
            # The merged mesh has the part transform baked into its vertices, like a
            # PrusaSlicer save; the matrix lets PrusaSlicer restore the part's own frame
            # (needed for text parts to stay editable in their plane).
            _cfg_meta(vol_el, "volume", "matrix", vol.transform.to_matrix16())
            if vol.extruder:
                _cfg_meta(vol_el, "volume", "extruder", vol.extruder)
            if translate_settings and vol.settings:
                label = f"part {vol.name or vol.leaf_oid}"
                for k, v in sorted(translate_object_settings(vol.settings, full_map, warnings, label).items()):
                    if k != "extruder":
                        _cfg_meta(vol_el, "volume", k, v)
            if vol.text:
                text_attrs, shape_attrs = _text_config_attrs(vol.text)
                shape_el = ET.SubElement(vol_el, ns.q(ns.SLIC3RPE, "shape"))
                for k, v in shape_attrs.items():
                    shape_el.set(k, v)
                text_el = ET.SubElement(vol_el, ns.q(ns.SLIC3RPE, "text"))
                for k, v in text_attrs.items():
                    text_el.set(k, v)
    if len(root) == 0:
        return None
    return ET.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _build_custom_gcodes(project: BambuProject, plates, full_map: dict, pcfg, warnings: list):
    """Bambu per-layer colour changes / pauses -> PrusaSlicer custom_gcode_per_print_z."""
    if not project.custom_gcodes:
        return None, 0
    candidates = sorted(plates) if plates else sorted(project.custom_gcodes)
    chosen = None
    for p in candidates:
        pg = project.custom_gcodes.get(p)
        if pg and pg.items:
            if chosen is None:
                chosen = pg
            else:
                warnings.append(
                    f"plate {p} also has per-layer colour changes/pauses; only the first plate's were carried over"
                )
    if chosen is None:
        return None, 0

    def gcode_for(item) -> str:
        if pcfg is None:
            return {0: "M600", 1: "M601", 2: "tool_change"}.get(item.type, item.extra)
        return {
            0: pcfg.get("color_change_gcode", "M600"),
            1: pcfg.get("pause_print_gcode", "M601"),
            2: "tool_change",
            3: pcfg.get("template_custom_gcode", ""),
        }.get(item.type, item.extra)

    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<custom_gcodes_per_print_z bed_idx="0">']
    for item in chosen.items:
        ext = full_map.get(item.extruder, item.extruder)
        lines.append(
            f'<code print_z="{item.print_z:g}" type="{item.type}" extruder="{ext}" '
            f"color={quoteattr(item.color)} extra={quoteattr(item.extra)} gcode={quoteattr(gcode_for(item))}/>"
        )
    lines.append(f"<mode value={quoteattr(chosen.mode)}/>")
    lines.append("</custom_gcodes_per_print_z>")
    return "\n".join(lines) + "\n", len(chosen.items)


def _object_index_map(project: BambuProject, selected_items, id_map: dict) -> Dict[str, str]:
    """Bambu stores per-object layer data by the 1-based index of the model object;
    map that index to the output object id."""
    order: list = []
    for bi in project.build_items:
        if bi.objectid not in order:
            order.append(bi.objectid)
    out: Dict[str, str] = {}
    for i, oid in enumerate(order, start=1):
        key = _key(ROOT_MODEL, oid)
        if key in id_map:
            out[str(i)] = id_map[key]
    return out


def _build_layer_ranges(project: BambuProject, selected_items, id_map: dict, full_map: dict, warnings: list):
    if not project.layer_ranges:
        return None, 0
    index_map = _object_index_map(project, selected_items, id_map)
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<objects>"]
    count = 0
    for src_idx, ranges in project.layer_ranges.items():
        out_id = index_map.get(src_idx)
        if out_id is None:
            continue
        lines.append(f' <object id="{out_id}">')
        for rng in ranges:
            settings = translate_object_settings(rng.settings, full_map, warnings, f"layer range {rng.min_z:g}-{rng.max_z:g}")
            lines.append(f'  <range min_z="{rng.min_z:g}" max_z="{rng.max_z:g}">')
            for k, v in sorted(settings.items()):
                lines.append(f'   <option opt_key="{k}">{_xml_text(v)}</option>')
            lines.append("  </range>")
            count += 1
        lines.append(" </object>")
    lines.append("</objects>")
    if count == 0:
        return None, 0
    return "\n".join(lines) + "\n", count


def _build_layer_heights(project: BambuProject, selected_items, id_map: dict):
    if not project.layer_heights:
        return None
    index_map = _object_index_map(project, selected_items, id_map)
    lines = []
    for src_idx, profile in project.layer_heights.items():
        out_id = index_map.get(src_idx)
        if out_id is None:
            continue
        values = [v for v in profile.split(";") if v.strip()]
        if len(values) <= 4 or len(values) % 2:
            continue
        lines.append(f"object_id={out_id}|" + ";".join(values))
    return ("\n".join(lines) + "\n") if lines else None


def _xml_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rels_xml(with_thumbnail: bool) -> str:
    thumb = (
        f' <Relationship Target="/Metadata/thumbnail.png" Id="rel-2" Type="{ns.REL_TYPE_THUMBNAIL}"/>\n'
        if with_thumbnail
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Relationships xmlns="{ns.RELS}">\n'
        f' <Relationship Target="/{ROOT_MODEL}" Id="rel-1" Type="{ns.REL_TYPE_STARTPART}"/>\n'
        f"{thumb}"
        "</Relationships>\n"
    )


def _pick_thumbnail(project: BambuProject) -> Optional[bytes]:
    # Prefer a plate thumbnail, then any thumbnail.
    for name, data in project.raw_entries.items():
        low = name.lower()
        if "plate_1.png" in low and "small" not in low and "no_light" not in low:
            return data
    for name, data in project.raw_entries.items():
        if "thumbnail" in name.lower():
            return data
    return None
