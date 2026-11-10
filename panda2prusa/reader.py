"""Read a Bambu Studio / OrcaSlicer .3mf into an in-memory project.

The reader is deliberately tolerant: it keeps the parsed lxml trees around so the
writer can move ``<mesh>`` XML verbatim (preserving vertices and per-triangle paint
data losslessly), while also extracting the structured bits needed for plate
selection, settings translation and multi-material config.
"""

from __future__ import annotations

import math
import posixpath
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import lxml.etree as ET

from . import ns
from .bambu_config import BambuConfig


ROOT_MODEL = "3D/3dmodel.model"
PROJECT_SETTINGS = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"
CUSTOM_GCODE = "Metadata/custom_gcode_per_layer.xml"
LAYER_CONFIG_RANGES = "Metadata/layer_config_ranges.xml"
LAYER_HEIGHTS_PROFILE = "Metadata/layer_heights_profile.txt"

# Bambu part subtypes -> PrusaSlicer volume types.
VOLUME_TYPES = {
    "normal_part": "ModelPart",
    "negative_part": "NegativeVolume",
    "modifier_part": "ParameterModifier",
    "support_blocker": "SupportBlocker",
    "support_enforcer": "SupportEnforcer",
}

# Bambu Studio lays its plates out side by side in one coordinate system, each plate
# 20 % of its own size away from the next (LOGICAL_PART_PLATE_GAP in PartPlate.cpp).
PLATE_GAP = 0.2


def plate_origin(index: int, count: int, width: float, depth: float) -> tuple:
    """World-space origin of 0-based plate ``index`` in a project with ``count`` plates.

    Plates fill a grid of ceil(sqrt(count)) columns, left to right, with further rows
    going towards -Y (PartPlateList::compute_shape_position).
    """
    cols = math.isqrt(max(count, 1))
    if cols * cols < count:
        cols += 1
    row, col = divmod(max(index, 0), max(cols, 1))
    return (col * width * (1.0 + PLATE_GAP), -row * depth * (1.0 + PLATE_GAP))


# Part / object metadata keys that describe the model rather than print settings.
_NON_SETTING_KEYS = {
    "name", "matrix", "module", "source_file", "source_object_id", "source_volume_id",
    "source_offset_x", "source_offset_y", "source_offset_z", "source_in_inches",
    "source_in_meters", "extruder",
}


@dataclass
class Part:
    """A part/volume inside a Bambu object, from model_settings.config."""

    id: str
    name: str = ""
    extruder: Optional[str] = None
    matrix: Optional[str] = None
    subtype: str = "normal_part"
    settings: Dict[str, str] = field(default_factory=dict)  # per-part print settings
    text: Optional[Dict[str, str]] = None  # Bambu Studio <text_info> attributes of a text part

    @property
    def volume_type(self) -> str:
        return VOLUME_TYPES.get(self.subtype, "ModelPart")


@dataclass
class SettingsObject:
    """A model_settings.config <object> entry (grouping + per-part extruders)."""

    id: str
    name: str = ""
    extruder: Optional[str] = None
    parts: List[Part] = field(default_factory=list)
    settings: Dict[str, str] = field(default_factory=dict)  # per-object print settings


@dataclass
class BuildItem:
    objectid: str
    transform: str  # raw 12-number string as stored in the source
    printable: bool = True
    element: object = None  # the source lxml <item>


@dataclass
class CustomGCode:
    """One entry of Bambu's custom_gcode_per_layer.xml (color change, pause, ...)."""

    print_z: float
    type: int  # 0 ColorChange, 1 PausePrint, 2 ToolChange, 3 Template, 4 Custom
    extruder: int
    color: str = ""
    extra: str = ""


@dataclass
class PlateGCodes:
    mode: str = "SingleExtruder"  # SingleExtruder | MultiAsSingle | MultiExtruder
    items: List[CustomGCode] = field(default_factory=list)


@dataclass
class LayerRange:
    min_z: float
    max_z: float
    settings: Dict[str, str] = field(default_factory=dict)


@dataclass
class BambuProject:
    source_path: str
    root_tree: object  # lxml ElementTree of 3D/3dmodel.model
    object_trees: dict  # normalized model path -> lxml ElementTree
    build_items: list  # list[BuildItem]
    plate_of_object: dict  # object_id -> plate index (1-based)
    plate_ids: list  # sorted list of plate indices present
    settings_objects: dict  # object_id -> SettingsObject
    raw_entries: dict  # arcname -> bytes, for passthrough assets (thumbnails, etc.)
    producer: str  # detected producer string ("BambuStudio-...", "OrcaSlicer-...", or "")
    filament_colors: list = field(default_factory=list)  # 1-based slots, "#RRGGBB" strings
    filament_types: list = field(default_factory=list)  # e.g. ["PLA", "PETG", ...]
    config: BambuConfig = field(default_factory=BambuConfig.empty)  # project_settings.config
    plate_settings: dict = field(default_factory=dict)  # plate index -> {key: value}
    custom_gcodes: dict = field(default_factory=dict)  # plate index -> PlateGCodes
    layer_ranges: dict = field(default_factory=dict)  # object_id -> list[LayerRange]
    layer_heights: dict = field(default_factory=dict)  # object_id -> "z;h;z;h..." profile
    bed_size: tuple = (256.0, 256.0)  # Bambu printable area (width, depth)
    plate_origins: dict = field(default_factory=dict)  # plate index -> (x, y) world origin

    def object_element(self, path: str, objectid: str):
        """Look up an <object id=..> element in a given model file."""
        tree = self.root_tree if path in (None, ROOT_MODEL) else self.object_trees.get(path)
        if tree is None:
            return None
        root = tree.getroot()
        for obj in root.iter(ns.q(ns.CORE, "object")):
            if obj.get("id") == objectid:
                return obj
        return None

    @property
    def has_settings(self) -> bool:
        return bool(self.config)


def _normalize_path(target: str, base: str = ROOT_MODEL) -> str:
    """Resolve a (possibly absolute) 3mf part path to a zip arcname."""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), target))


def _parse(data: bytes):
    """Parse model XML tolerantly (some producers emit non-utf8 declarations)."""
    parser = ET.XMLParser(recover=True, huge_tree=True)
    return ET.fromstring(data, parser=parser).getroottree()


def _parse_xml(data: bytes):
    return ET.fromstring(data, parser=ET.XMLParser(recover=True))


def read_bambu_3mf(path: str) -> BambuProject:
    with zipfile.ZipFile(path, "r") as z:
        names = set(z.namelist())
        if ROOT_MODEL not in names:
            raise ValueError(
                f"{path!r} does not contain {ROOT_MODEL}; not a recognizable 3mf project."
            )
        root_tree = _parse(z.read(ROOT_MODEL))

        # Gather every referenced object model file: from the rels and from any
        # component p:path attributes in the root (belt and suspenders).
        object_paths: set = set()
        rels_name = "3D/_rels/3dmodel.model.rels"
        if rels_name in names:
            rels_root = ET.fromstring(z.read(rels_name))
            for rel in rels_root.iter(ns.q(ns.RELS, "Relationship")):
                tgt = rel.get("Target")
                if tgt and tgt.lower().endswith(".model"):
                    object_paths.add(_normalize_path(tgt))
        for comp in root_tree.getroot().iter(ns.q(ns.CORE, "component")):
            p = comp.get(ns.q(ns.PRODUCTION, "path"))
            if p:
                object_paths.add(_normalize_path(p))

        object_trees: dict = {}
        for op in sorted(object_paths):
            if op in names and op != ROOT_MODEL:
                object_trees[op] = _parse(z.read(op))

        # Build items (with their real transforms).
        build_items: List[BuildItem] = []
        build_el = root_tree.getroot().find(ns.q(ns.CORE, "build"))
        if build_el is not None:
            for item in build_el.findall(ns.q(ns.CORE, "item")):
                oid = item.get("objectid")
                if oid is None:
                    continue
                build_items.append(
                    BuildItem(
                        objectid=oid,
                        transform=item.get("transform", ""),
                        printable=item.get("printable", "1") not in ("0", "false", "False"),
                        element=item,
                    )
                )

        # Producer detection.
        producer = ""
        for md in root_tree.getroot().findall(ns.q(ns.CORE, "metadata")):
            if md.get("name") == "Application" and md.text:
                producer = md.text.strip()
                break

        # model_settings.config: plates + per-part extruders + per-object settings.
        settings_objects: dict = {}
        plate_of_object: dict = {}
        plate_ids: list = []
        plate_settings: dict = {}
        if MODEL_SETTINGS in names:
            settings_objects, plate_of_object, plate_ids, plate_settings = _parse_model_settings(
                z.read(MODEL_SETTINGS)
            )

        # Project settings (process + filament + machine), JSON.
        config = BambuConfig.from_bytes(z.read(PROJECT_SETTINGS)) if PROJECT_SETTINGS in names else BambuConfig.empty()
        filament_colors = list(config.filament_colours)
        filament_types = list(config.filament_types)

        bed_size = _bed_size(config)
        plate_origins = {
            plate_no: plate_origin(plate_no - 1, len(plate_ids), bed_size[0], bed_size[1])
            for plate_no in plate_ids
        }

        custom_gcodes = _parse_custom_gcodes(z.read(CUSTOM_GCODE)) if CUSTOM_GCODE in names else {}
        layer_ranges = _parse_layer_ranges(z.read(LAYER_CONFIG_RANGES)) if LAYER_CONFIG_RANGES in names else {}
        layer_heights = _parse_layer_heights(z.read(LAYER_HEIGHTS_PROFILE)) if LAYER_HEIGHTS_PROFILE in names else {}

        # Passthrough assets we want to keep (thumbnails); skip Bambu-only config.
        raw_entries: dict = {}
        for n in names:
            low = n.lower()
            if low.endswith(".png") and ("thumbnail" in low or "plate" in low or "/metadata/" in low):
                raw_entries[n] = z.read(n)

    return BambuProject(
        source_path=path,
        root_tree=root_tree,
        object_trees=object_trees,
        build_items=build_items,
        plate_of_object=plate_of_object,
        plate_ids=plate_ids,
        settings_objects=settings_objects,
        raw_entries=raw_entries,
        producer=producer,
        filament_colors=filament_colors,
        filament_types=filament_types,
        config=config,
        plate_settings=plate_settings,
        custom_gcodes=custom_gcodes,
        layer_ranges=layer_ranges,
        layer_heights=layer_heights,
        bed_size=bed_size,
        plate_origins=plate_origins,
    )


def _bed_size(config: BambuConfig) -> tuple:
    """(width, depth) of the Bambu printable area; 256 x 256 if unknown."""
    pts = config.raw_value("printable_area")
    xs, ys = [], []
    for p in pts if isinstance(pts, list) else []:
        try:
            x, y = str(p).split("x")
            xs.append(float(x))
            ys.append(float(y))
        except ValueError:
            return (256.0, 256.0)
    if len(xs) < 3:
        return (256.0, 256.0)
    return (max(xs) - min(xs), max(ys) - min(ys))


def _metadata_dict(el) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for md in el.findall("metadata"):
        key, val = md.get("key"), md.get("value")
        if key is not None and val is not None:
            out[key] = val
    return out


def _parse_model_settings(data: bytes):
    """Extract per-object parts/extruders/settings and plate membership."""
    root = _parse_xml(data)
    settings_objects: dict = {}
    for obj in root.findall("object"):
        oid = obj.get("id")
        if oid is None:
            continue
        meta = _metadata_dict(obj)
        so = SettingsObject(
            id=oid,
            name=meta.get("name", ""),
            extruder=meta.get("extruder"),
            settings={k: v for k, v in meta.items() if k not in _NON_SETTING_KEYS},
        )
        for part in obj.findall("part"):
            pm = _metadata_dict(part)
            text_el = part.find("text_info")
            so.parts.append(
                Part(
                    id=part.get("id", ""),
                    name=pm.get("name", ""),
                    extruder=pm.get("extruder"),
                    matrix=pm.get("matrix"),
                    subtype=part.get("subtype", "normal_part") or "normal_part",
                    settings={k: v for k, v in pm.items() if k not in _NON_SETTING_KEYS},
                    text=dict(text_el.attrib) if text_el is not None else None,
                )
            )
        settings_objects[oid] = so

    plate_of_object: dict = {}
    plate_ids: list = []
    plate_settings: dict = {}
    for idx, plate in enumerate(root.findall("plate"), start=1):
        meta = _metadata_dict(plate)
        plate_no = idx
        for key in ("plater_id", "plate_id", "index"):
            if meta.get(key):
                try:
                    plate_no = int(meta[key])
                    break
                except ValueError:
                    pass
        plate_ids.append(plate_no)
        plate_settings[plate_no] = {
            k: v for k, v in meta.items()
            if k in ("bed_type", "print_sequence", "spiral_mode", "first_layer_print_sequence",
                     "other_layers_print_sequence", "plater_name")
        }
        for inst in plate.findall("model_instance"):
            for md in inst.findall("metadata"):
                if md.get("key") == "object_id" and md.get("value"):
                    plate_of_object[md.get("value")] = plate_no
    return settings_objects, plate_of_object, sorted(set(plate_ids)), plate_settings


def _parse_custom_gcodes(data: bytes) -> dict:
    """Bambu custom_gcode_per_layer.xml -> {plate index: PlateGCodes}."""
    root = _parse_xml(data)
    out: dict = {}
    plates = root.findall("plate")
    if not plates:  # very old files: no <plate> wrapper, everything is plate 1
        plates = [root]
    for i, plate in enumerate(plates, start=1):
        plate_no = i
        info = plate.find("plate_info")
        if info is not None and info.get("id"):
            try:
                plate_no = int(info.get("id"))
            except ValueError:
                pass
        pg = PlateGCodes()
        mode = plate.find("mode")
        if mode is not None and mode.get("value"):
            pg.mode = mode.get("value")
        for layer in plate.findall("layer"):
            try:
                z = float(layer.get("top_z", ""))
            except ValueError:
                continue
            try:
                ctype = int(layer.get("type", "4"))
            except ValueError:
                ctype = 4
            try:
                ext = int(layer.get("extruder", "1"))
            except ValueError:
                ext = 1
            pg.items.append(
                CustomGCode(
                    print_z=z,
                    type=ctype,
                    extruder=ext,
                    color=layer.get("color", "") or "",
                    extra=layer.get("extra", "") or layer.get("gcode", "") or "",
                )
            )
        out[plate_no] = pg
    return out


def _parse_layer_ranges(data: bytes) -> dict:
    """Bambu layer_config_ranges.xml -> {object id: [LayerRange]}."""
    root = _parse_xml(data)
    out: dict = {}
    for obj in root.findall("object"):
        oid = obj.get("id")
        if oid is None:
            continue
        ranges = []
        for rng in obj.findall("range"):
            try:
                lo, hi = float(rng.get("min_z", "")), float(rng.get("max_z", ""))
            except ValueError:
                continue
            settings = {}
            for opt in rng.findall("option"):
                key = opt.get("opt_key")
                if key:
                    settings[key] = (opt.text or "").strip()
            ranges.append(LayerRange(lo, hi, settings))
        if ranges:
            out[oid] = ranges
    return out


def _parse_layer_heights(data: bytes) -> dict:
    """Bambu layer_heights_profile.txt -> {object id: "z;h;z;h..."}."""
    out: dict = {}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        head, _, profile = line.partition("|")
        _, _, oid = head.partition("=")
        if oid.strip() and profile.strip():
            out[oid.strip()] = profile.strip()
    return out
