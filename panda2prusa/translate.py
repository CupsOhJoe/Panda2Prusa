"""Build a PrusaSlicer project configuration from a Bambu Studio project.

The result is what goes into ``Metadata/Slic3r_PE.config``: a complete FFF config
(print + filament + printer + project keys). It is assembled in layers:

1. PrusaSlicer's defaults;
2. *base* presets, when an installed PrusaSlicer provides them: the printer
   profile that will actually run the job, and the print / filament presets the
   user currently has selected (so printer-specific keys Bambu has no notion of
   keep sensible values);
3. everything Bambu Studio expresses, mapped through ``keymap`` and the special
   cases below;
4. a validation pass that drops anything PrusaSlicer would reject.

Per-object, per-part and per-layer-range settings use the same mapping restricted
to the keys PrusaSlicer allows at that level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import keymap as K
from . import prusa_schema as S
from .bambu_config import BambuConfig
from .prusa_config import PrusaConfig, enum_values, is_nullable, is_vector, option_type

MODE_REFERENCE = "reference"  # printer settings from a PrusaSlicer profile
MODE_BAMBU = "bambu"  # printer settings translated from the Bambu project

# What the print and filament layers of the output are.
SETTINGS_PRESETS = "presets"  # the user's selected PrusaSlicer presets, untouched; Bambu colours as tool colours
SETTINGS_BAMBU = "bambu"  # Bambu process and filament settings translated into project presets

# Preset bookkeeping keys that must not travel into a project config.
_STRIP_KEYS = frozenset(
    {
        "compatible_printers", "compatible_printers_condition", "compatible_prints",
        "compatible_prints_condition", "inherits", "renamed_from", "alias",
        "print_host", "printhost_apikey", "printhost_cafile", "printhost_password",
        "printhost_user", "printhost_port", "printhost_authorization_type",
    }
)
# Filament vector keys that are not "per filament values" but preset metadata.
_FILAMENT_VECTOR_KEYS = frozenset(k for k in S.FILAMENT_KEYS if is_vector(k)) - _STRIP_KEYS
_FILAMENT_SCALAR_KEYS = frozenset(k for k in S.FILAMENT_KEYS if not is_vector(k)) - _STRIP_KEYS
# Feature extruders that must be >= 1 in PrusaSlicer (0 = "default" only for supports).
_REGION_EXTRUDER_KEYS = frozenset({"perimeter_extruder", "infill_extruder", "solid_infill_extruder"})


@dataclass
class TargetPresets:
    """What the output should be based on."""

    mode: str = MODE_BAMBU
    printer: Optional[PrusaConfig] = None
    printer_name: str = ""
    print_base: Optional[PrusaConfig] = None
    print_base_name: str = ""
    filament_bases: List[Optional[PrusaConfig]] = field(default_factory=list)  # per target extruder
    filament_base_names: List[str] = field(default_factory=list)
    keep_tool_colours: bool = False  # leave the printer profile's extruder_colour alone
    settings_mode: str = SETTINGS_BAMBU  # SETTINGS_PRESETS needs MODE_REFERENCE and base presets
    description: str = ""

    @property
    def keeps_presets(self) -> bool:
        return (
            self.settings_mode == SETTINGS_PRESETS
            and self.mode == MODE_REFERENCE
            and self.printer is not None
            and self.print_base is not None
        )

    @property
    def extruders(self) -> Optional[int]:
        if self.printer is None:
            return None
        return max(1, len(self.printer.vector("nozzle_diameter") or []))


@dataclass
class Translation:
    config: PrusaConfig
    warnings: List[str]
    extruders: int
    slot_of_extruder: Dict[int, int]  # prusa extruder -> bambu filament slot
    wipe_tower_pos: Optional[tuple] = None  # (x, y, rotation) if a wipe tower is used


# -- helpers ----------------------------------------------------------------------------

def _num(v: Optional[str]) -> Optional[float]:
    try:
        return float(str(v).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _is_percent(v: str) -> bool:
    return v.strip().endswith("%")


def _apply_rule(rule: K.Rule, value: Optional[str], cfg: PrusaConfig, warnings: list) -> Optional[str]:
    """Convert one Bambu value for ``rule``; returns the PrusaSlicer value or None."""
    if value is None:
        return None
    v = value.strip()
    if v == "" and option_type(rule.prusa) not in ("string", "strings"):
        return None
    if rule.conv is not None:
        out = rule.conv(v)
        if out is None:
            if v != "nil" and v not in ("", "0", "-1") and v not in rule.quiet:
                warnings.append(f"{rule.bambu}={v!r} has no PrusaSlicer equivalent for {rule.prusa}; kept base value")
            return None
        v = out
    if _is_percent(v) and option_type(rule.prusa) in ("float", "floats", "int", "ints"):
        base = _num(cfg.get(rule.percent_base)) if rule.percent_base else None
        pct = _num(v)
        if base is None or pct is None:
            return None
        return f"{base * pct / 100.0:g}"
    if v == "nil" and not is_nullable(rule.prusa):
        return None
    return v


def _remap_extruder(value: Optional[str], extruder_map: Dict[int, int]) -> Optional[str]:
    """Bambu filament number -> Prusa extruder number (0 = default stays 0)."""
    if value is None:
        return None
    try:
        n = int(float(value))
    except ValueError:
        return None
    if n <= 0:
        return "0"
    return str(extruder_map.get(n, n))


def _support_values(support_type: Optional[str], support_style: Optional[str]) -> dict:
    out = {}
    if support_type:
        out["support_material_auto"] = "1" if "auto" in support_type else "0"
        if support_type.startswith("tree"):
            out["support_material_style"] = "organic"
        else:
            style = (support_style or "default").lower()
            out["support_material_style"] = "snug" if style == "snug" else "grid"
    elif support_style:
        style = support_style.lower()
        out["support_material_style"] = (
            "organic" if style.startswith("tree") else "snug" if style == "snug" else "grid"
        )
    return out


def _ironing_values(ironing_type: Optional[str]) -> dict:
    if ironing_type is None:
        return {}
    if ironing_type == "no ironing":
        return {"ironing": "0"}
    if ironing_type in ("top", "topmost", "solid"):
        return {"ironing": "1", "ironing_type": ironing_type}
    return {}


def _wall_sequence(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return "1" if value == "outer wall/inner wall" else "0"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _bed_bounds(cfg: PrusaConfig) -> Optional[tuple]:
    pts = cfg.vector("bed_shape") or []
    xs, ys = [], []
    for p in pts:
        try:
            x, y = p.split("x")
            xs.append(float(x))
            ys.append(float(y))
        except ValueError:
            return None
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# -- validation -------------------------------------------------------------------------

_FLOAT_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?%?$")
_INT_RE = re.compile(r"^[+-]?\d+$")


def _valid_scalar(kind: str, v: str, enum: Optional[tuple], nullable: bool) -> bool:
    if nullable and v == "nil":
        return True
    if kind in ("float", "percent", "floatorpercent"):
        if not _FLOAT_RE.match(v.strip()):
            return False
        return kind != "float" or not v.strip().endswith("%")
    if kind == "int":
        return bool(_INT_RE.match(v.strip()))
    if kind == "bool":
        return v.strip() in ("0", "1")
    if kind == "enum":
        return not enum or v in enum
    if kind == "point":
        parts = v.split("x")
        return len(parts) == 2 and all(_FLOAT_RE.match(p) for p in parts)
    return True  # strings


def _clamp_text(v: str, lo: Optional[float], hi: Optional[float]) -> str:
    """Clamp a numeric (or percent) value into PrusaSlicer's allowed range."""
    if v == "nil" or (lo is None and hi is None):
        return v
    s = v.strip()
    pct = s.endswith("%")
    n = _num(s)
    if n is None:
        return v
    clamped = n
    if lo is not None and n < lo:
        clamped = lo
    if hi is not None and n > hi:
        clamped = hi
    if clamped == n:
        return v
    out = f"{clamped:g}"
    return out + "%" if pct else out


def validate(cfg: PrusaConfig, fallback: PrusaConfig, warnings: list) -> None:
    """Drop or repair values PrusaSlicer would refuse to load."""
    for key in list(cfg.keys()):
        info = S.OPTIONS.get(key)
        if info is None:
            cfg.pop(key)
            continue
        kind, nullable, enum = info[0], info[1], info[2]
        lo, hi = (info[3], info[4]) if len(info) > 4 else (None, None)
        v = cfg.get(key)
        if kind in ("string", "strings", "pointsgroups"):
            continue
        if kind in ("float", "int", "percent", "floatorpercent"):
            fixed = _clamp_text(v, lo, hi)
            if fixed != v:
                warnings.append(f"{key}={v!r} is outside PrusaSlicer's range; clamped to {fixed}")
                cfg.set(key, fixed)
                v = fixed
        elif kind in ("floats", "ints", "percents", "floatsorpercents"):
            vals = cfg.vector(key) or []
            fixed_vals = [_clamp_text(x, lo, hi) for x in vals]
            if fixed_vals != vals:
                warnings.append(f"{key}={v!r} has values outside PrusaSlicer's range; clamped")
                cfg.set_vector(key, fixed_vals)
                v = cfg.get(key)
        if kind in ("points",):
            ok = all(_valid_scalar("point", p, None, False) for p in cfg.vector(key) or [])
        elif is_vector(key):
            scalar_kind = kind[:-1] if kind != "floatsorpercents" else "floatorpercent"
            vals = cfg.vector(key) or []
            fixed = []
            ok = True
            for x in vals:
                if scalar_kind == "bool" and x.strip().lower() in ("true", "false"):
                    x = "1" if x.strip().lower() == "true" else "0"
                if not _valid_scalar(scalar_kind, x, enum, nullable):
                    ok = False
                    break
                fixed.append(x)
            if ok and fixed != vals:
                cfg.set_vector(key, fixed)
        else:
            if kind == "bool" and v.strip().lower() in ("true", "false"):
                v = "1" if v.strip().lower() == "true" else "0"
                cfg.set(key, v)
            ok = _valid_scalar(kind, v, enum, nullable)
        if not ok:
            fb = fallback.get(key)
            if fb is not None and fb != v:
                warnings.append(f"{key}={v!r} is not a valid PrusaSlicer value; using {fb!r}")
                cfg.set(key, fb)
            else:
                warnings.append(f"{key}={v!r} is not a valid PrusaSlicer value; dropped")
                cfg.pop(key)


# -- main entry ---------------------------------------------------------------------------

def translate_project(
    bambu: BambuConfig,
    extruder_map: Dict[int, int],
    target: TargetPresets,
    used_slots: List[int],
    plate_index: int = 0,
) -> Translation:
    """Produce the full PrusaSlicer config for a converted project.

    ``extruder_map`` maps every used Bambu filament slot (1-based) to a PrusaSlicer
    extruder (1-based). ``plate_index`` (0-based) selects which Bambu plate's wipe
    tower position to carry over.
    """
    warnings: List[str] = []
    defaults = PrusaConfig(S.DEFAULTS)
    # Keeping the user's presets: write only what the presets define, so PrusaSlicer
    # fills the rest with its own defaults and recognises the presets as unmodified.
    cfg = PrusaConfig() if target.keeps_presets else defaults.copy()

    # Full map including identity for used slots.
    full_map = {s: extruder_map.get(s, s) for s in used_slots}
    slot_of_extruder: Dict[int, int] = {}
    for slot in sorted(full_map):
        slot_of_extruder.setdefault(full_map[slot], slot)

    # --- printer layer ------------------------------------------------------------
    if target.mode == MODE_REFERENCE and target.printer is not None:
        n_ext = target.extruders or 1
        cfg.update_from(target.printer, keys=[k for k in target.printer.keys() if k in S.PRINTER_KEYS])
        cfg.set("printer_settings_id", target.printer_name or target.printer.get("printer_settings_id", ""))
        for k in ("printer_vendor", "printer_model", "printer_variant", "printer_notes",
                  "default_print_profile", "default_filament_profile", "thumbnails", "thumbnails_format"):
            if k in target.printer:
                cfg.set(k, target.printer.get(k))
    else:
        n_ext = max([1] + list(full_map.values()))
        _synthesize_printer(bambu, cfg, n_ext, slot_of_extruder, warnings)

    # --- print and filament layers --------------------------------------------------
    if target.keeps_presets:
        cfg.update_from(target.print_base, keys=[k for k in target.print_base.keys() if k in S.PRINT_KEYS])
        cfg.set("print_settings_id", target.print_base_name)
        _apply_filament_presets(bambu, cfg, n_ext, slot_of_extruder, target)
    else:
        if target.print_base is not None:
            cfg.update_from(target.print_base, keys=[k for k in target.print_base.keys() if k in S.PRINT_KEYS])
        keep = _PRINTER_COUPLED_PRINT_KEYS if target.print_base is not None else frozenset()
        _apply_print_rules(bambu, cfg, full_map, warnings, keep=keep)
        cfg.set("print_settings_id", bambu.scalar("print_settings_id") or "Bambu Studio project")
        _apply_filament_rules(bambu, cfg, n_ext, slot_of_extruder, target, warnings)

    # --- project keys -------------------------------------------------------------
    wipe_pos = _apply_project_keys(bambu, cfg, n_ext, slot_of_extruder, plate_index, target, warnings)

    # --- cleanup ------------------------------------------------------------------
    for k in _STRIP_KEYS:
        cfg.pop(k, None)
    cfg.set("physical_printer_settings_id", "")
    cfg.set("printer_technology", "FFF")
    per_ext_keys = set(S.EXTRUDER_KEYS) | _FILAMENT_VECTOR_KEYS
    per_ext_keys.discard("default_filament_profile")
    cfg.resize_vectors(n_ext, per_ext_keys)
    cfg.resize_vectors(2, S.MACHINE_LIMITS_KEYS)
    validate(cfg, defaults, warnings)
    return Translation(cfg, warnings, n_ext, slot_of_extruder, wipe_pos)


# -- print ----------------------------------------------------------------------------------

# How the printer parks idle tools. A Prusa print preset tunes these for the Prusa hardware
# (the XL cools parked tools so they do not ooze); Bambu's values describe a single-nozzle
# machine and would leave every parked XL tool at full temperature.
_PRINTER_COUPLED_PRINT_KEYS = frozenset({"ooze_prevention", "standby_temperature_delta"})


def _apply_print_rules(
    bambu: BambuConfig,
    cfg: PrusaConfig,
    full_map: Dict[int, int],
    warnings: list,
    keep: frozenset = frozenset(),
) -> None:
    """Translate Bambu process settings onto ``cfg``; keys in ``keep`` stay as they are."""
    extruder_keys = {
        "perimeter_extruder", "infill_extruder", "solid_infill_extruder",
        "support_material_extruder", "support_material_interface_extruder",
    }
    for rule in K.PRINT_RULES:
        if not bambu.has(rule.bambu) or rule.prusa in keep:
            continue
        v = _apply_rule(rule, bambu.scalar(rule.bambu), cfg, warnings)
        if v is None:
            continue
        if rule.prusa in extruder_keys:
            v = _remap_extruder(v, full_map)
            if v is None:
                continue
            # Bambu's 0 means "the object's filament"; at print level PrusaSlicer
            # expresses the same with extruder 1 (objects override it with their own).
            if v == "0" and rule.prusa in _REGION_EXTRUDER_KEYS:
                v = "1"
        cfg.set(rule.prusa, v)

    # Supports: Bambu splits on/off, auto/manual and style across three keys.
    if bambu.has("enable_support"):
        cfg.set("support_material", K.to_bool(bambu.scalar("enable_support") or "0") or "0")
    for k, v in _support_values(bambu.scalar("support_type"), bambu.scalar("support_style")).items():
        cfg.set(k, v)

    for k, v in _ironing_values(bambu.scalar("ironing_type")).items():
        cfg.set(k, v)

    v = _wall_sequence(bambu.scalar("wall_sequence"))
    if v is not None:
        cfg.set("external_perimeters_first", v)

    seq = bambu.scalar("print_sequence")
    if seq is not None:
        cfg.set("complete_objects", "1" if seq == "by object" else "0")

    # Older Bambu/Orca: only_one_wall_top (bool) instead of top_one_wall_type (enum).
    if not bambu.has("top_one_wall_type") and bambu.has("only_one_wall_top"):
        b = K.to_bool(bambu.scalar("only_one_wall_top") or "0")
        if b is not None:
            cfg.set("top_one_perimeter_type", "top" if b == "1" else "none")

    for prusa_key, bambu_key in K.OVERHANG_SPEED_RULES:
        if bambu.has(bambu_key):
            v = K.positive_or_skip(bambu.scalar(bambu_key) or "0")
            if v is not None:
                cfg.set(prusa_key, v)

    if bambu.has("gap_infill_speed"):
        n = _num(bambu.scalar("gap_infill_speed"))
        if n is not None:
            cfg.set("gap_fill_enabled", "1" if n > 0 else "0")

    if bambu.has("brim_type") and bambu.scalar("brim_type") in ("auto_brim", "brim_ears"):
        warnings.append(
            f"brim_type={bambu.scalar('brim_type')!r} has no PrusaSlicer equivalent; "
            f"wrote an outer brim of brim_width={cfg.get('brim_width')} mm (set to 0 if unwanted)"
        )

    hole = _num(bambu.scalar("xy_hole_compensation"))
    if hole:
        warnings.append(f"xy_hole_compensation={hole:g} is not supported by PrusaSlicer; only the contour compensation was carried over")

    if bambu.has("fill_density"):  # OrcaSlicer spelling of sparse_infill_density
        v = bambu.scalar("fill_density")
        if v and not bambu.has("sparse_infill_density"):
            cfg.set("fill_density", v if v.endswith("%") else v + "%")


# -- filament ---------------------------------------------------------------------------------

def _base_filament_value(key: str, base: Optional[PrusaConfig], cfg: PrusaConfig) -> Optional[str]:
    """First element of a filament vector from the base preset, the config so far or
    the defaults; ``nil`` for nullable overrides nobody set; None if unknown."""
    if base is not None and key in base:
        vals = base.vector(key)
        if vals:
            return vals[0]
    cur = cfg.vector(key)
    if cur:
        return cur[0]
    vals = PrusaConfig(S.DEFAULTS).vector(key)
    if vals:
        return vals[0]
    return "nil" if is_nullable(key) else None


def _apply_filament_rules(
    bambu: BambuConfig,
    cfg: PrusaConfig,
    n_ext: int,
    slot_of_extruder: Dict[int, int],
    target: TargetPresets,
    warnings: list,
) -> None:
    bed_key, bed_first_key = bambu.bed_temp_keys()
    rules = list(K.FILAMENT_RULES) + [
        K.Rule("bed_temperature", bed_key),
        K.Rule("first_layer_bed_temperature", bed_first_key),
    ]
    source_cache: Dict[str, Optional[list]] = {}

    def source(key: str) -> Optional[list]:
        if key not in source_cache:
            source_cache[key] = bambu.per_filament(key)
        return source_cache[key]

    vectors: Dict[str, list] = {key: [None] * n_ext for key in _FILAMENT_VECTOR_KEYS}
    ids: list = []
    colours: list = []
    capped: list = []
    for e in range(1, n_ext + 1):
        base = target.filament_bases[e - 1] if e - 1 < len(target.filament_bases) else None
        slot = slot_of_extruder.get(e)
        for key in _FILAMENT_VECTOR_KEYS:
            vectors[key][e - 1] = _base_filament_value(key, base, cfg)
        base_id = ""
        if base is not None:
            base_id = _base_filament_value("filament_settings_id", base, cfg) or ""
            if e - 1 < len(target.filament_base_names) and target.filament_base_names[e - 1]:
                base_id = target.filament_base_names[e - 1]
        if slot is None:
            ids.append(base_id)
            colours.append(None)
            continue
        for rule in rules:
            vals = source(rule.bambu)
            if vals is None or slot - 1 >= len(vals):
                continue
            v = _apply_rule(rule, vals[slot - 1], cfg, warnings)
            if v is None:
                continue
            vectors[rule.prusa][e - 1] = v
        for rule in K.FILAMENT_FROM_PRINTER_RULES:
            if bambu.has(rule.bambu):
                v = _apply_rule(rule, bambu.scalar(rule.bambu), cfg, warnings)
                if v is not None:
                    vectors[rule.prusa][e - 1] = v
        # Bambu tunes the volumetric limit for its own hotends; on a Prusa the filament
        # preset knows what the Nextruder can melt. The wipe tower purges at this limit.
        if target.mode == MODE_REFERENCE and base is not None and "filament_max_volumetric_speed" in base:
            limit = _num((base.vector("filament_max_volumetric_speed") or [None])[0])
            asked = _num(vectors["filament_max_volumetric_speed"][e - 1])
            if limit and asked and asked > limit:
                vectors["filament_max_volumetric_speed"][e - 1] = f"{limit:g}"
                capped.append(f"tool {e}: {asked:g} -> {limit:g}")
        if target.mode == MODE_BAMBU:
            chamber = source("chamber_temperatures")
            if chamber and slot - 1 < len(chamber) and _num(chamber[slot - 1]) is not None:
                vectors["chamber_temperature"][e - 1] = chamber[slot - 1]
        fid = source("filament_settings_id")
        ids.append(fid[slot - 1] if fid and slot - 1 < len(fid) else base_id or f"Bambu filament {slot}")
        colours.append(vectors["filament_colour"][e - 1])

    if capped:
        warnings.append(
            "filament_max_volumetric_speed capped at the PrusaSlicer filament preset's value "
            "(the Prusa hotend's limit) in mm3/s: " + ", ".join(capped)
        )

    for key, vals in vectors.items():
        if all(v is None for v in vals):
            continue  # PrusaSlicer will use its own default
        fill = "nil" if is_nullable(key) else next(v for v in vals if v is not None)
        cfg.set_vector(key, [v if v is not None else fill for v in vals])
    cfg.set_vector("filament_settings_id", ids)

    # PrusaSlicer paints the 3D view with the *tool* colour when the printer profile
    # defines one (Prusa XL profiles do), so the Bambu filament colours would never
    # show. Give mapped tools their filament colour; untouched tools keep theirs.
    if target.mode == MODE_REFERENCE and target.keep_tool_colours is False:
        tool_colours = cfg.vector("extruder_colour") or []
        tool_colours = (tool_colours + [""] * n_ext)[:n_ext]
        for e in range(1, n_ext + 1):
            c = colours[e - 1]
            if c:
                tool_colours[e - 1] = c
        cfg.set_vector("extruder_colour", tool_colours)

    vendors = source("filament_vendor")
    first_slot = slot_of_extruder.get(1) or (min(slot_of_extruder.values()) if slot_of_extruder else None)
    if vendors and first_slot and first_slot - 1 < len(vendors) and vendors[first_slot - 1]:
        cfg.set("filament_vendor", vendors[first_slot - 1])
    elif target.filament_bases and target.filament_bases[0] is not None and "filament_vendor" in target.filament_bases[0]:
        cfg.set("filament_vendor", target.filament_bases[0].get("filament_vendor"))


def _bambu_tool_colours(bambu: BambuConfig, cfg: PrusaConfig, n_ext: int, slot_of_extruder: Dict[int, int]) -> None:
    """Give mapped tools their Bambu filament colour as the tool colour (PrusaSlicer paints
    the 3D view with extruder_colour when it is set, which Prusa XL profiles do)."""
    colours = bambu.filament_colours
    tool_colours = ((cfg.vector("extruder_colour") or []) + [""] * n_ext)[:n_ext]
    for e, slot in slot_of_extruder.items():
        if 1 <= e <= n_ext and 0 < slot <= len(colours) and colours[slot - 1]:
            tool_colours[e - 1] = colours[slot - 1]
    cfg.set_vector("extruder_colour", tool_colours)


def _apply_filament_presets(
    bambu: BambuConfig, cfg: PrusaConfig, n_ext: int, slot_of_extruder: Dict[int, int], target: TargetPresets
) -> None:
    """Write the user's selected filament presets exactly as they are, one per tool, so
    PrusaSlicer selects them unmodified. Only the tool colours come from the Bambu file."""
    bases = [target.filament_bases[e] if e < len(target.filament_bases) else None for e in range(n_ext)]
    names = [target.filament_base_names[e] if e < len(target.filament_base_names) else "" for e in range(n_ext)]
    keys = set()
    for base in bases:
        if base is not None:
            keys |= {k for k in base.keys() if k in S.FILAMENT_KEYS and k not in _STRIP_KEYS}
    for key in sorted(keys):
        if is_vector(key):
            vals = []
            for base in bases:
                v = (base.vector(key) or [None])[0] if base is not None and key in base else None
                vals.append(v if v is not None else _base_filament_value(key, None, cfg) or "")
            cfg.set_vector(key, vals)
        else:
            first = next(b for b in bases if b is not None and key in b)
            cfg.set(key, first.get(key))
    ids = []
    for base, name in zip(bases, names):
        if not name and base is not None:
            name = (base.vector("filament_settings_id") or [""])[0]
        ids.append(name)
    cfg.set_vector("filament_settings_id", ids)
    if not target.keep_tool_colours:
        _bambu_tool_colours(bambu, cfg, n_ext, slot_of_extruder)


# -- printer (bambu mode) -----------------------------------------------------------------

def _synthesize_printer(
    bambu: BambuConfig, cfg: PrusaConfig, n_ext: int, slot_of_extruder: Dict[int, int], warnings: list
) -> None:
    for rule in K.PRINTER_RULES:
        if not bambu.has(rule.bambu):
            continue
        if rule.prusa == "bed_shape":
            pts = [str(p) for p in (bambu.raw_value(rule.bambu) or []) if "x" in str(p)]
            if len(pts) >= 3:
                cfg.set_vector("bed_shape", pts)
            continue
        v = _apply_rule(rule, bambu.scalar(rule.bambu), cfg, warnings)
        if v is not None:
            cfg.set(rule.prusa, v)
    for rule in K.EXTRUDER_RULES:
        if not bambu.has(rule.bambu):
            continue
        vals = bambu.per_extruder(rule.bambu) or []
        out = []
        for e in range(n_ext):
            src = vals[e] if e < len(vals) else (vals[-1] if vals else None)
            v = _apply_rule(rule, src, cfg, warnings) if src is not None else None
            out.append(v if v is not None else _base_filament_value(rule.prusa, None, cfg))
        if all(v is not None for v in out):
            cfg.set_vector(rule.prusa, out)
    for prusa_key, bambu_key in K.MACHINE_LIMIT_RULES:
        pair = bambu.machine_limit(bambu_key)
        if pair and all(_num(p) is not None for p in pair):
            cfg.set_vector(prusa_key, list(pair))
    # Bambu firmware does not take M201/M203 limits; keep them for time estimates only.
    cfg.set("machine_limits_usage", "time_estimate_only")
    cfg.set("extruder_offset", ["0x0"] * n_ext)
    colours = bambu.filament_colours
    cfg.set_vector(
        "extruder_colour",
        [colours[slot_of_extruder[e] - 1] if e in slot_of_extruder and slot_of_extruder[e] - 1 < len(colours) else ""
         for e in range(1, n_ext + 1)],
    )
    high_flow = [("High Flow" in v) for v in bambu.active_variants] or [False]
    cfg.set_vector("nozzle_high_flow", ["1" if (high_flow[0] if len(high_flow) == 1 else high_flow[min(e, len(high_flow) - 1)]) else "0" for e in range(n_ext)])
    lift_types = bambu.per_extruder("z_hop_types") or []
    cfg.set_vector("travel_ramping_lift", ["1" if (lift_types[e] if e < len(lift_types) else "") == "Slope Lift" else "0" for e in range(n_ext)])
    cfg.set("start_gcode", K.GENERIC_START_GCODE)
    cfg.set("end_gcode", K.GENERIC_END_GCODE)
    cfg.set("before_layer_gcode", K.GENERIC_BEFORE_LAYER_GCODE)
    cfg.set("layer_gcode", K.GENERIC_LAYER_GCODE)
    cfg.set("toolchange_gcode", "")
    cfg.set("color_change_gcode", "M600")
    cfg.set("printer_settings_id", bambu.scalar("printer_settings_id") or "Bambu printer")
    cfg.set("printer_vendor", "")
    cfg.set("binary_gcode", "0")
    cfg.set("thumbnails", "")
    cfg.set("remaining_times", "0")
    if n_ext > 1 and not bambu.single_extruder_multi_material:
        cfg.set("single_extruder_multi_material", "0")
    elif n_ext > 1:
        cfg.set("single_extruder_multi_material", "1")
    bounds = _bed_bounds(cfg)
    bed = f"{bounds[2] - bounds[0]:g}x{bounds[3] - bounds[1]:g} mm bed" if bounds else "Bambu bed"
    warnings.append(
        f"No PrusaSlicer printer profile was used: the file carries the Bambu Lab printer profile "
        f"({bed}, {n_ext} filament(s) through one nozzle, generic start/end G-code). It will not "
        "print correctly on a Prusa as-is: select your real printer in PrusaSlicer after opening "
        "it, or convert again with the printer set to Auto / --printer <your printer>."
    )


# -- project keys ---------------------------------------------------------------------------

# Bambu Studio's prime_volume default: what its prime tower purges per filament change
# when the project says nothing else.
_BAMBU_PRIME_VOLUME = 45.0
# Depth PrusaSlicer's wipe tower spends per tool change on ramming/transition lines on
# top of the purge itself. Rough; only used to keep the tower on the bed.
_TOWER_TRANSITION_DEPTH = 6.0


def _prime_volume(bambu: BambuConfig, slot: Optional[int]) -> Optional[float]:
    """Bambu's per-change purge on the prime tower for a filament slot."""
    vals = bambu.per_filament("filament_prime_volume") or []
    if slot is not None and slot - 1 < len(vals):
        v = _num(vals[slot - 1])
        if v is not None and v > 0:
            return v
    v = _num(bambu.scalar("prime_volume"))
    return v if v is not None and v > 0 else None


def _estimated_tower_depth(cfg: PrusaConfig, n_ext: int, width: float) -> float:
    """Upper-bound guess of PrusaSlicer's wipe tower depth: every tool changing once per
    layer, each change purging the configured volume plus a ramming allowance."""
    if cfg.get("single_extruder_multi_material") == "1":
        purge = _num(cfg.get("multimaterial_purging")) or 140.0
    else:
        minimal = [_num(v) or 0.0 for v in (cfg.vector("filament_minimal_purge_on_wipe_tower") or [])]
        purge = max(minimal + [15.0])
    spacing = (_num(cfg.get("wipe_tower_extra_spacing")) or 100.0) / 100.0
    layer_h = max(_num(cfg.get("layer_height")) or 0.2, 0.05)
    per_change = _TOWER_TRANSITION_DEPTH + purge * spacing / (layer_h * max(width, 1.0))
    brim = _num(cfg.get("wipe_tower_brim_width")) or 0.0
    return brim + max(n_ext - 1, 1) * per_change


def _apply_project_keys(
    bambu: BambuConfig,
    cfg: PrusaConfig,
    n_ext: int,
    slot_of_extruder: Dict[int, int],
    plate_index: int,
    target: TargetPresets,
    warnings: list,
) -> Optional[tuple]:
    cfg.set("colorprint_heights", "")

    # Purging. Bambu's flush_volumes_matrix (hundreds of mm³ per change) is what the
    # printer dumps into its purge chute; the prime tower itself only gets the small
    # prime volume. PrusaSlicer purges everything on the wipe tower, so copying those
    # volumes builds a tower hundreds of millimetres deep, off the bed. Purge amounts
    # are therefore left to PrusaSlicer's own settings (the printer profile's
    # multimaterial_purging and the filaments' purge multipliers; multi-tool printers
    # ignore them anyway). In Bambu mode they are sized like Bambu's prime tower.
    if target.mode == MODE_BAMBU:
        vols = {e: _prime_volume(bambu, s) for e, s in slot_of_extruder.items()}
        vols = {e: v for e, v in vols.items() if v is not None}
        purge = max(vols.values()) if vols else _BAMBU_PRIME_VOLUME
        cfg.set("multimaterial_purging", f"{purge:g}")
        cfg.set_vector(
            "filament_purge_multiplier",
            [f"{round(100 * vols.get(e, purge) / purge):d}%" for e in range(1, n_ext + 1)],
        )
        # No MMU-style priming strip along the front edge of the bed.
        cfg.set("single_extruder_multi_material_priming", "0")
        if n_ext > 1:
            warnings.append(
                "purge volumes: Bambu's flush volumes go into the purge chute, which PrusaSlicer "
                f"has no equivalent for; the wipe tower purges {purge:g} mm3 per change like "
                "Bambu's prime tower, so filament and time estimates read lower than in Bambu Studio"
            )
    purge = _num(cfg.get("multimaterial_purging")) or 140.0
    cfg.set_vector(
        "wiping_volumes_matrix",
        ["0" if i == j else f"{purge:g}" for i in range(n_ext) for j in range(n_ext)],
    )
    cfg.set("wiping_volumes_use_custom_matrix", "0")

    # Wipe tower position (per plate in Bambu), kept where the tower fits on the bed.
    pos = None
    xs = bambu.raw_value("wipe_tower_x")
    ys = bambu.raw_value("wipe_tower_y")
    if isinstance(xs, list) and isinstance(ys, list) and xs and ys:
        i = min(plate_index, len(xs) - 1, len(ys) - 1)
        x, y = _num(xs[i]), _num(ys[i])
        if x is not None and y is not None:
            rot = _num(bambu.scalar("wipe_tower_rotation_angle")) or 0.0
            bounds = _bed_bounds(cfg)
            width = _num(cfg.get("wipe_tower_width")) or 60.0
            if bounds:
                depth = max(width, _estimated_tower_depth(cfg, n_ext, width))
                x = _clamp(x, bounds[0], max(bounds[0], bounds[2] - width))
                y = _clamp(y, bounds[1], max(bounds[1], bounds[3] - depth))
            pos = (x, y, rot)
            cfg.set("wipe_tower_x", f"{x:g}")
            cfg.set("wipe_tower_y", f"{y:g}")
            cfg.set("wipe_tower_rotation_angle", f"{rot:g}")
    return pos


# -- per-object / per-volume / per-range settings -------------------------------------------

_OBJECT_SPECIAL_SOURCES = (
    "enable_support", "support_type", "support_style", "ironing_type", "wall_sequence",
    "print_sequence", "only_one_wall_top", "gap_infill_speed", "flush_into_infill",
    "flush_into_objects", "extruder",
) + tuple(b for _p, b in K.OVERHANG_SPEED_RULES)


def translate_object_settings(
    meta: Dict[str, str], extruder_map: Dict[int, int], warnings: list, label: str = "object"
) -> Dict[str, str]:
    """Map Bambu per-object/part metadata (key -> value) to PrusaSlicer object config."""
    out: Dict[str, str] = {}
    scratch = PrusaConfig(S.DEFAULTS)
    extruder_keys = {
        "perimeter_extruder", "infill_extruder", "solid_infill_extruder",
        "support_material_extruder", "support_material_interface_extruder",
    }
    for bkey, bval in meta.items():
        if bkey == "extruder":
            v = _remap_extruder(bval, extruder_map)
            if v is not None:
                out["extruder"] = v
            continue
        rule = K.PRINT_RULES_BY_BAMBU.get(bkey)
        if rule is None:
            if bkey not in _OBJECT_SPECIAL_SOURCES and bkey not in ("name", "matrix", "module") and not bkey.startswith("source_"):
                warnings.append(f"{label}: setting {bkey!r} has no PrusaSlicer equivalent; dropped")
            continue
        if rule.prusa not in K.OBJECT_SCOPE_KEYS:
            continue
        v = _apply_rule(rule, bval, scratch, warnings)
        if v is None:
            continue
        if rule.prusa in extruder_keys:
            v = _remap_extruder(v, extruder_map)
            # 0 = "use the object's extruder": PrusaSlicer does that when the key is absent.
            if v is None or v == "0":
                continue
        out[rule.prusa] = v
    if "enable_support" in meta:
        b = K.to_bool(meta["enable_support"])
        if b is not None:
            out["support_material"] = b
    for k, v in _support_values(meta.get("support_type"), meta.get("support_style")).items():
        out[k] = v
    for k, v in _ironing_values(meta.get("ironing_type")).items():
        out[k] = v
    v = _wall_sequence(meta.get("wall_sequence"))
    if v is not None:
        out["external_perimeters_first"] = v
    for prusa_key, bambu_key in K.OVERHANG_SPEED_RULES:
        if bambu_key in meta:
            v = K.positive_or_skip(meta[bambu_key])
            if v is not None:
                out[prusa_key] = v
    for bkey, pkey in (("flush_into_infill", "wipe_into_infill"), ("flush_into_objects", "wipe_into_objects")):
        if bkey in meta:
            b = K.to_bool(meta[bkey])
            if b is not None:
                out[pkey] = b
    # Validate through the normal machinery, restricted to object-scope keys.
    tmp = PrusaConfig({k: v for k, v in out.items() if k in K.OBJECT_SCOPE_KEYS})
    validate(tmp, PrusaConfig(), warnings)
    return {k: tmp.get(k) for k in tmp.keys()}
