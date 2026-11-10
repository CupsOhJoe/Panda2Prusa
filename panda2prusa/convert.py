"""High-level conversion orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from . import ns
from .bambu_config import BambuConfig
from .paint import used_states, PaintError
from .prusa_config import PrusaConfig, PrusaInstall
from .reader import read_bambu_3mf, BambuProject
from .translate import MODE_BAMBU, MODE_REFERENCE, TargetPresets
from .writer import write_prusa_3mf, WriteStats

PRINTER_AUTO = "auto"
PRINTER_BAMBU = "bambu"
PLATES_SPLIT = "split"  # one output file per plate
PLATES_BEDS = "beds"  # one file, plate k on PrusaSlicer bed k (2.9+)


@dataclass
class ConversionResult:
    input_path: str
    output_path: str
    stats: WriteStats
    producer: str
    plates_available: list
    target_description: str = ""
    outputs: List[str] = field(default_factory=list)  # every file written (one per plate when split)


def used_filaments(project: BambuProject) -> List[int]:
    """Sorted 1-based filament slots a project actually uses.

    Union of object/part extruder assignments and every state referenced by
    paint data. Bambu leaves unassigned objects on filament 1.
    """
    used: set = set()
    for so in project.settings_objects.values():
        for ext in [so.extruder, *(p.extruder for p in so.parts)]:
            if ext:
                try:
                    used.add(int(ext))
                except ValueError:
                    pass
    codes: set = set()
    for tree in [project.root_tree, *project.object_trees.values()]:
        for tri in tree.getroot().iter(ns.q(ns.CORE, "triangle")):
            code = tri.get("paint_color")
            if code:
                codes.add(code)
    for code in codes:
        try:
            used |= used_states(code)
        except PaintError:
            pass
    used.discard(0)
    if not used and (project.build_items or codes):
        used.add(1)  # something is printed and it defaults to filament 1
    return sorted(used)


def suggest_extruder_map(used: List[int]) -> dict:
    """Compact sparse filament slots to sequential extruders 1..K."""
    return {old: i for i, old in enumerate(sorted(used), start=1)}


def filament_label(project: BambuProject, slot: int) -> str:
    """Human-readable description of a filament slot, e.g. '3 (PLA #8E2929)'."""
    bits = []
    if 0 < slot <= len(project.filament_types):
        bits.append(project.filament_types[slot - 1])
    if 0 < slot <= len(project.filament_colors):
        bits.append(project.filament_colors[slot - 1])
    return f"{slot} ({' '.join(bits)})" if bits else str(slot)


# -- target presets ---------------------------------------------------------------------

_GENERIC_TYPE = {
    "PLA": "PLA", "PLA-CF": "PLA", "PLA-AERO": "PLA", "PETG": "PETG", "PETG-CF": "PETG",
    "PCTG": "PETG", "ABS": "ABS", "ABS-GF": "ABS", "ASA": "ASA", "ASA-CF": "ASA",
    "ASA-AERO": "ASA", "TPU": "FLEX", "TPU-AMS": "FLEX", "FLEX": "FLEX", "PC": "PC",
    "PA": "PA", "PA-CF": "PA", "PA-GF": "PA", "PA6-CF": "PA", "NYLON": "PA", "PVA": "PVA",
    "HIPS": "HIPS", "PP": "PP", "PET": "PETG",
}


def _normalized_type(t: Optional[str]) -> str:
    t = (t or "").strip().upper()
    return _GENERIC_TYPE.get(t, t.split("-")[0] if t else "")


def _default_print_name(printer_cfg: Optional[PrusaConfig]) -> str:
    """The printer profile's default_print_profile, if any."""
    if printer_cfg is None:
        return ""
    return (printer_cfg.get("default_print_profile") or "").strip().strip('"')


def _default_filament_name(printer_cfg: Optional[PrusaConfig]) -> str:
    """First entry of the printer profile's default_filament_profile, if any."""
    if printer_cfg is None:
        return ""
    for v in printer_cfg.vector("default_filament_profile") or []:
        v = v.strip().strip('"')
        if v:
            return v
    return ""


def _pick_filament_base(inst: PrusaInstall, current_name: str, bambu_type: str, fallback_name: str = ""):
    """The user's current filament preset for a tool, unless its material differs from
    the Bambu filament: then a 'Generic <TYPE>' preset for the same printer family.

    ``fallback_name`` (the printer's default filament profile) stands in when the current
    selection is not an installed preset, e.g. a project preset that a converted file
    opened earlier left selected in PrusaSlicer."""
    current = inst.preset("filament", current_name) if current_name else None
    if current is None and fallback_name:
        current_name = fallback_name
        current = inst.preset("filament", current_name)
    want = _normalized_type(bambu_type)
    if current is not None:
        have = _normalized_type((current.vector("filament_type") or [""])[0])
        if not want or have == want:
            return current, current_name
    if want and "@" in current_name:
        suffix = current_name.split("@", 1)[1].strip()
        for cand in (f"Generic {want} @{suffix}", f"Generic {want} @{suffix.split()[0]}"):
            cfg = inst.preset("filament", cand)
            if cfg is not None:
                return cfg, cand
    return current, current_name


def resolve_target(
    printer: str = PRINTER_AUTO,
    bambu: Optional[BambuConfig] = None,
    slot_of_extruder: Optional[dict] = None,
    use_base_presets: bool = True,
    keep_tool_colours: bool = False,
    prusa_config_dir: Optional[str] = None,
) -> TargetPresets:
    """Decide which PrusaSlicer presets the converted settings are layered on.

    ``printer``: "auto" (the printer currently selected in an installed PrusaSlicer),
    "bambu" (translate the Bambu printer settings instead), the name of an installed
    PrusaSlicer printer preset, or a path to an exported ``.ini`` / PrusaSlicer ``.3mf``.
    """
    spec = (printer or PRINTER_AUTO).strip()
    inst = PrusaInstall.find(prusa_config_dir)
    target = TargetPresets(mode=MODE_BAMBU, keep_tool_colours=keep_tool_colours)

    if spec.lower() in (PRINTER_BAMBU, "none", ""):
        target.description = "Bambu printer settings translated (no PrusaSlicer printer profile)"
        return target

    printer_cfg: Optional[PrusaConfig] = None
    printer_name = ""
    if spec.lower() == PRINTER_AUTO:
        if inst is None:
            target.description = (
                "PrusaSlicer configuration not found; Bambu printer settings translated instead"
            )
            return target
        printer_name = inst.selected("printer") or ""
        printer_cfg = inst.preset("printer", printer_name) if printer_name else None
        if printer_cfg is None:
            target.description = (
                "No printer selected in PrusaSlicer; Bambu printer settings translated instead"
            )
            return target
        source = f"PrusaSlicer {inst.version} (currently selected printer)"
    elif os.path.isfile(spec):
        printer_cfg = PrusaConfig.from_file(spec)
        printer_name = printer_cfg.get("printer_settings_id") or os.path.splitext(os.path.basename(spec))[0]
        source = f"profile file {os.path.basename(spec)}"
    else:
        if inst is None:
            raise ValueError(
                f"PrusaSlicer configuration not found, so printer preset {spec!r} cannot be resolved. "
                "Use --printer bambu, or point --printer at an exported .ini/.3mf."
            )
        printer_cfg = inst.preset("printer", spec)
        if printer_cfg is None:
            names = inst.preset_names("printer")
            hint = ", ".join(names[:12]) + (" ..." if len(names) > 12 else "")
            raise ValueError(f"PrusaSlicer printer preset {spec!r} not found. Installed: {hint}")
        printer_name = spec
        source = f"PrusaSlicer {inst.version} preset"

    target.mode = MODE_REFERENCE
    target.printer = printer_cfg
    target.printer_name = printer_name
    n_ext = target.extruders or 1
    desc = [f"printer '{printer_name}' from {source}"]

    if inst is not None and use_base_presets:
        print_name = inst.selected("print") or ""
        target.print_base = inst.preset("print", print_name) if print_name else None
        if target.print_base is None:
            # The selection is not an installed preset (e.g. a project preset left behind
            # by a converted file opened earlier): the printer's default print profile.
            print_name = _default_print_name(printer_cfg)
            target.print_base = inst.preset("print", print_name) if print_name else None
        target.print_base_name = print_name if target.print_base is not None else ""
        if target.print_base is not None:
            desc.append(f"print base '{print_name}'")
        current = inst.selected_filaments()
        default_fil = _default_filament_name(printer_cfg)
        types = bambu.filament_types if bambu is not None else []
        bases, names = [], []
        for e in range(1, n_ext + 1):
            cur_name = current[e - 1] if e - 1 < len(current) else (current[0] if current else "")
            cur_name = cur_name or default_fil
            slot = (slot_of_extruder or {}).get(e)
            btype = types[slot - 1] if slot and slot - 1 < len(types) else ""
            cfg, name = _pick_filament_base(inst, cur_name, btype, default_fil) if cur_name else (None, "")
            bases.append(cfg)
            names.append(name if cfg is not None else "")
        target.filament_bases = bases
        target.filament_base_names = names
        used_names = sorted({n for n in names if n})
        if used_names:
            desc.append("filament bases " + ", ".join(f"'{n}'" for n in used_names))
    target.description = "; ".join(desc)
    return target


def convert_file(
    input_path: str,
    output_path: str,
    plates: Optional[list] = None,
    extruder_map: Optional[dict] = None,
    compact_extruders: bool = True,
    settings: bool = True,
    printer: str = PRINTER_AUTO,
    use_base_presets: bool = True,
    keep_tool_colours: bool = False,
    prusa_config_dir: Optional[str] = None,
    plate_layout: str = PLATES_SPLIT,
) -> ConversionResult:
    """Convert a Bambu/Orca .3mf at ``input_path`` to a Prusa .3mf at ``output_path``.

    ``plates`` optionally restricts output to the given 1-based plate numbers.
    ``extruder_map`` explicitly maps Bambu filament slots to Prusa extruders
    (``{}`` keeps the original numbering); when None and ``compact_extruders`` is
    true, sparse slots are compacted to sequential extruders automatically.
    ``settings`` enables translation of process/filament/printer settings;
    ``printer`` selects the PrusaSlicer printer profile (see ``resolve_target``).
    When several plates are converted, ``plate_layout`` decides between one file per
    plate (``output_plateN.3mf``, the default) and one file with each plate on its own
    PrusaSlicer bed (``"beds"``, PrusaSlicer 2.9+).
    """
    project = read_bambu_3mf(input_path)

    target: Optional[TargetPresets] = None
    if settings:
        used = used_filaments(project)
        if extruder_map is not None:
            final_map = {s: int(extruder_map.get(s, s)) for s in used}
        elif compact_extruders:
            final_map = suggest_extruder_map(used)
        else:
            final_map = {s: s for s in used}
        slot_of_extruder: dict = {}
        for slot in sorted(final_map):
            slot_of_extruder.setdefault(final_map[slot], slot)
        target = resolve_target(
            printer, project.config, slot_of_extruder, use_base_presets, keep_tool_colours, prusa_config_dir
        )

    wanted = [p for p in (plates or project.plate_ids) if p in project.plate_ids] or [None]
    split = plate_layout != PLATES_BEDS and len(wanted) > 1
    jobs = [([p], _plate_path(output_path, p)) for p in wanted] if split else [(plates, output_path)]
    stats: Optional[WriteStats] = None
    outputs: List[str] = []
    for job_plates, path in jobs:
        st = write_prusa_3mf(
            project,
            path,
            plates=job_plates,
            extruder_map=extruder_map,
            compact_extruders=compact_extruders,
            target=target,
            translate_settings=settings,
        )
        outputs.append(path)
        if stats is None:
            stats = st
        else:  # aggregate per-plate runs
            stats.objects_written += st.objects_written
            stats.volumes_written += st.volumes_written
            stats.build_items_written += st.build_items_written
            stats.painted_triangles += st.painted_triangles
            stats.custom_gcodes_written += st.custom_gcodes_written
            stats.layer_ranges_written += st.layer_ranges_written
            stats.text_volumes += st.text_volumes
            for w in st.warnings:
                if w not in stats.warnings:
                    stats.warnings.append(w)
    assert stats is not None
    return ConversionResult(
        input_path=input_path,
        output_path=output_path,
        stats=stats,
        producer=project.producer,
        plates_available=project.plate_ids,
        target_description=target.description if target else "settings not converted",
        outputs=outputs,
    )


def _plate_path(output_path: str, plate) -> str:
    base, ext = os.path.splitext(output_path)
    return f"{base}_plate{plate}{ext or '.3mf'}"


def describe(input_path: str) -> BambuProject:
    """Read a project for inspection (used by --info and interactive prompts)."""
    return read_bambu_3mf(input_path)


def installed_printers(prusa_config_dir: Optional[str] = None) -> List[str]:
    """Names of PrusaSlicer printer presets available for --printer."""
    inst = PrusaInstall.find(prusa_config_dir)
    return inst.preset_names("printer") if inst else []


def current_printer(prusa_config_dir: Optional[str] = None) -> Optional[str]:
    inst = PrusaInstall.find(prusa_config_dir)
    return inst.selected("printer") if inst else None
