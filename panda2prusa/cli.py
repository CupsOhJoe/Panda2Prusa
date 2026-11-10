"""Command-line interface: python -m panda2prusa ..."""

from __future__ import annotations

import argparse
import sys

from .convert import (
    PLATES_BEDS,
    PLATES_SPLIT,
    PRINTER_AUTO,
    convert_file,
    current_printer,
    describe,
    filament_label,
    installed_printers,
    suggest_extruder_map,
    used_filaments,
)
from . import ns


def _cmd_info(args) -> int:
    project = describe(args.input)
    print(f"File:      {args.input}")
    print(f"Producer:  {project.producer or 'unknown'}")
    print(f"Objects:   {len(project.object_trees) + 1} model part(s)")
    print(f"Build items: {len(project.build_items)}")
    if project.plate_ids:
        print(f"Plates:    {', '.join(str(p) for p in project.plate_ids)}")
        # per-plate object counts
        counts: dict = {}
        for oid, plate in project.plate_of_object.items():
            counts[plate] = counts.get(plate, 0) + 1
        for p in project.plate_ids:
            print(f"   plate {p}: {counts.get(p, 0)} object(s)")
    used = used_filaments(project)
    if used:
        print(f"Filaments used: {', '.join(filament_label(project, f) for f in used)}")
    # painting summary
    painted = 0
    for tree in [project.root_tree, *project.object_trees.values()]:
        for tri in tree.getroot().iter(ns.q(ns.CORE, "triangle")):
            if "paint_color" in tri.attrib:
                painted += 1
    print(f"Painted triangles: {painted}")
    cfg = project.config
    if cfg:
        print("Settings:")
        print(f"   printer:  {cfg.scalar('printer_settings_id') or '?'}  (bed: {cfg.bed_type or '?'})")
        print(f"   process:  {cfg.scalar('print_settings_id') or '?'}  "
              f"(layer {cfg.scalar('layer_height')} mm, {cfg.scalar('wall_loops')} walls, "
              f"{cfg.scalar('sparse_infill_density')} {cfg.scalar('sparse_infill_pattern')} infill, "
              f"supports {'on' if cfg.scalar('enable_support') == '1' else 'off'})")
        ids = cfg.filament_ids
        for slot in used:
            name = ids[slot - 1] if slot - 1 < len(ids) else "?"
            print(f"   filament {filament_label(project, slot)}: {name}")
        for plate, pg in sorted(project.custom_gcodes.items()):
            if pg.items:
                print(f"   plate {plate}: {len(pg.items)} colour change / pause entries")
        if project.layer_ranges:
            print(f"   layer height ranges on {len(project.layer_ranges)} object(s)")
    else:
        print("Settings:  none (no Metadata/project_settings.config)")
    return 0


def _cmd_list_printers(args) -> int:
    names = installed_printers(args.prusa_config_dir)
    if not names:
        print("No PrusaSlicer installation found (or no printers installed in it).", file=sys.stderr)
        return 1
    cur = current_printer(args.prusa_config_dir)
    for n in names:
        print(("* " if n == cur else "  ") + n)
    print("\n* = currently selected in PrusaSlicer (used by --printer auto)")
    return 0


def _parse_map(text: str) -> dict:
    """Parse '3=2,1=1' (also accepts '3:2' and '3->2') into {3: 2, 1: 1}."""
    mapping = {}
    for pair in text.replace("->", "=").replace(":", "=").replace(";", ",").split(","):
        pair = pair.strip()
        if not pair:
            continue
        try:
            old, new = pair.split("=")
            mapping[int(old)] = int(new)
        except ValueError:
            raise SystemExit(f"Bad extruder mapping {pair!r}; expected e.g. 3=2,1=1")
    return mapping


def _prompt_extruder_map(project) -> dict | None:
    """Show the file's filaments and ask how to map them. None = auto-compact."""
    used = used_filaments(project)
    if len(used) < 2 and used == [1]:
        return None  # single color on slot 1 — nothing to decide
    print(f"This file uses {len(used)} filament(s):")
    for f in used:
        print(f"  filament {filament_label(project, f)}")
    suggestion = suggest_extruder_map(used)
    sug = ", ".join(f"{k}->{v}" for k, v in suggestion.items())
    try:
        resp = input(
            f"Map to Prusa extruders [{sug}] (Enter=accept, 'keep' for no change, "
            "or e.g. 3=2,1=1): "
        ).strip()
    except EOFError:
        return None
    if not resp:
        return suggestion
    if resp.lower() in ("keep", "k", "none"):
        return {}
    return _parse_map(resp)


def _cmd_convert(args) -> int:
    plates = None
    if args.plate:
        plates = []
        for chunk in args.plate:
            plates.extend(int(p) for p in str(chunk).replace(",", " ").split())

    extruder_map = None
    if args.map:
        extruder_map = _parse_map(args.map)
    elif args.keep_extruders:
        extruder_map = {}
    elif sys.stdin.isatty() and sys.stdout.isatty():
        extruder_map = _prompt_extruder_map(describe(args.input))

    result = convert_file(
        args.input,
        args.output,
        plates=plates,
        extruder_map=extruder_map,
        settings=not args.no_settings,
        bambu_settings=args.bambu_settings or args.no_base_presets,
        printer=args.printer,
        use_base_presets=not args.no_base_presets,
        keep_tool_colours=args.keep_tool_colours,
        prusa_config_dir=args.prusa_config_dir,
        plate_layout=PLATES_BEDS if args.plates_as_beds else PLATES_SPLIT,
    )
    s = result.stats
    print(f"Converted: {args.input}")
    for out in result.outputs:
        print(f"       ->  {out}")
    print(f"Producer:  {result.producer or 'unknown'}")
    if result.plates_available:
        shown = plates if plates else "all"
        layout = ""
        if len(result.outputs) > 1:
            layout = " (one file per plate)"
        elif s.beds_used > 1:
            layout = f" (on {s.beds_used} PrusaSlicer beds)"
        print(f"Plates:    available {result.plates_available}, converted {shown}{layout}")
    print(f"Objects written:      {s.objects_written}")
    print(f"Volumes written:      {s.volumes_written}")
    print(f"Build items written:  {s.build_items_written}")
    print(f"Painted triangles:    {s.painted_triangles}")
    if s.extruder_map:
        remapped = ", ".join(f"{k}->{v}" for k, v in sorted(s.extruder_map.items()))
        print(f"Extruders remapped:   {remapped}")
    if s.settings_written:
        print(f"Settings:             {result.target_description}")
        print(f"   printer profile:   {s.printer_name}  ({s.extruders} extruder(s))")
        print(f"   print profile:     {s.print_name}")
        for i, name in enumerate(s.filament_names, start=1):
            print(f"   extruder {i}:        {name}")
        if s.custom_gcodes_written:
            print(f"   colour changes/pauses carried over: {s.custom_gcodes_written}")
        if s.layer_ranges_written:
            print(f"   layer height ranges carried over:   {s.layer_ranges_written}")
        if s.text_volumes:
            print(f"   editable text parts:                {s.text_volumes}")
    else:
        print(f"Settings:             not converted ({result.target_description})")
    for w in s.warnings:
        print(f"note: {w}", file=sys.stderr)
    if s.objects_written == 0:
        print("WARNING: no objects were written — check plate selection.", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="panda2prusa",
        description="Convert Bambu Studio / OrcaSlicer .3mf files to PrusaSlicer .3mf.",
    )
    p.add_argument("--info", metavar="FILE", help="inspect a 3mf and exit")
    p.add_argument("--list-printers", action="store_true",
                   help="list the PrusaSlicer printer presets available for --printer, then exit")
    p.add_argument("input", nargs="?", help="input Bambu/Orca .3mf")
    p.add_argument("output", nargs="?", help="output Prusa .3mf")
    p.add_argument(
        "--plate",
        action="append",
        help="only convert these plate number(s), e.g. --plate 1 or --plate 1,2",
    )
    p.add_argument(
        "--map",
        metavar="MAP",
        help="map Bambu filament slots to Prusa extruders, e.g. --map 3=2 or --map 1=2,3=1",
    )
    p.add_argument(
        "--keep-extruders",
        action="store_true",
        help="keep original filament slot numbers (no compaction, no prompt)",
    )
    p.add_argument(
        "--plates-as-beds",
        action="store_true",
        help="put all converted plates in one file, each on its own PrusaSlicer bed "
             "(PrusaSlicer 2.9+ GUI). Default: one output file per plate (output_plateN.3mf)",
    )
    g = p.add_argument_group("settings translation")
    g.add_argument(
        "--printer",
        default=PRINTER_AUTO,
        metavar="WHICH",
        help="PrusaSlicer printer profile for the output: 'auto' (the printer currently "
             "selected in PrusaSlicer; default), an installed preset name (see "
             "--list-printers), an exported .ini / PrusaSlicer .3mf file, or 'bambu' (keep "
             "the Bambu Lab printer profile with generic G-code; only for PCs without "
             "PrusaSlicer, switch printer after opening)",
    )
    g.add_argument(
        "--bambu-settings",
        action="store_true",
        help="translate the Bambu process and filament settings into project presets. "
             "Default: keep the print and filament presets selected in PrusaSlicer and only "
             "apply the Bambu filament colours as tool colours",
    )
    g.add_argument(
        "--no-settings",
        action="store_true",
        help="geometry, extruders and painting only; do not write any slicer settings",
    )
    g.add_argument(
        "--no-base-presets",
        action="store_true",
        help="with --bambu-settings (implied): layer the Bambu settings on PrusaSlicer "
             "defaults instead of on the presets currently selected in PrusaSlicer",
    )
    g.add_argument(
        "--keep-tool-colours",
        action="store_true",
        help="do not overwrite the printer profile's extruder colours with the Bambu filament colours",
    )
    g.add_argument(
        "--prusa-config-dir",
        metavar="DIR",
        help="PrusaSlicer configuration directory (default: auto-detect, e.g. %%APPDATA%%\\PrusaSlicer)",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_printers:
        return _cmd_list_printers(args)
    if args.info:
        args.input = args.info
        return _cmd_info(args)
    if not args.input or not args.output:
        build_parser().print_help()
        return 1
    try:
        return _cmd_convert(args)
    except Exception as exc:  # surface a clean message, not a traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
