"""Generate panda2prusa/prusa_schema.py and panda2prusa/bambu_schema.py.

The converter needs to know, for every PrusaSlicer and Bambu Studio config key,
its value type (scalar vs. per-extruder vector, number vs. percent vs. enum) and
the enum values PrusaSlicer accepts. Those facts live in the slicers' C++ sources,
so this script parses them and emits plain Python tables. Run it when a new
slicer version changes the option set:

    python tools/gen_schema.py --cache build/slicer-src \\
        --prusa-ref version_2.9.6 --bambu-ref master \\
        --prusa-defaults defaults.ini   # from: prusa-slicer-console --save defaults.ini

Sources are downloaded from GitHub into the cache directory unless already there.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "panda2prusa")

PRUSA_FILES = {
    "PrintConfig.cpp": "src/libslic3r/PrintConfig.cpp",
    "PrintConfig.hpp": "src/libslic3r/PrintConfig.hpp",
    "Preset.cpp": "src/libslic3r/Preset.cpp",
    "PresetBundle.cpp": "src/libslic3r/PresetBundle.cpp",
}
BAMBU_FILES = {
    "PrintConfig.cpp": "src/libslic3r/PrintConfig.cpp",
    "PrintConfig.hpp": "src/libslic3r/PrintConfig.hpp",
    "Preset.cpp": "src/libslic3r/Preset.cpp",
}

TYPE_CODES = {
    "coFloat": "float", "coFloats": "floats", "coInt": "int", "coInts": "ints",
    "coBool": "bool", "coBools": "bools", "coString": "string", "coStrings": "strings",
    "coPercent": "percent", "coPercents": "percents",
    "coFloatOrPercent": "floatorpercent", "coFloatsOrPercents": "floatsorpercents",
    "coEnum": "enum", "coEnums": "enums", "coPoint": "point", "coPoints": "points",
    "coPointsGroups": "pointsgroups",
}


def fetch(repo: str, ref: str, path: str, dest: str) -> str:
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return dest
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    print("downloading", url)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _balanced(block: str, start: int) -> str:
    """Return the text inside the brace group that opens at block[start]."""
    depth = 0
    for k in range(start, len(block)):
        if block[k] == "{":
            depth += 1
        elif block[k] == "}":
            depth -= 1
            if depth == 0:
                return block[start + 1:k]
    return block[start + 1:]


def parse_options(src: str) -> dict:
    """{key: {"type", "nullable", "enum"}} from PrintConfigDef::init_* bodies."""
    pattern = r'this->add(_nullable)?\(\s*"([a-zA-Z_0-9]+)"\s*,\s*(co[A-Za-z]+)\s*\)'
    adds = list(re.finditer(pattern, src))
    # Any add() call (also ones with a computed key, e.g. in loops) ends the block of
    # the previous option, otherwise its def->min/max would leak into the wrong key.
    boundaries = [m.start() for m in re.finditer(r"this->add(?:_nullable)?\s*\(", src)]
    table = {}
    for m in adds:
        nullable_call, key, ctype = m.group(1), m.group(2), m.group(3)
        later = [b for b in boundaries if b > m.start()]
        end = later[0] if later else min(len(src), m.end() + 6000)
        block = src[m.end():end]
        info = {
            "type": TYPE_CODES.get(ctype, ctype),
            "nullable": bool(nullable_call) or bool(re.search(r"def->nullable\s*=\s*true", block)),
        }
        enum = [
            e.group(1)
            for e in re.finditer(r'enum_values\.(?:push_back|emplace_back)\("([^"]*)"\)', block)
        ]
        if not enum:
            se = re.search(r"set_enum(?:<[^>]*>)?\s*\(", block)
            if se:
                j = block.find("{", se.end())
                if j != -1:
                    inner = _balanced(block, j)
                    pairs = re.findall(r'\{\s*"([^"]*)"\s*,', inner)
                    enum = pairs if pairs else re.findall(r'"([^"]*)"', inner)
        if enum and info["type"] in ("enum", "enums"):
            info["enum"] = enum
        for bound in ("min", "max"):
            # Last assignment wins: the source has a few `def->max = 0; def->max = 300;`
            # typos where the first line was meant to be the minimum.
            found = re.findall(r"def->" + bound + r"\s*=\s*(-?\d+(?:\.\d*)?)\s*[f]?\s*;", block)
            if found:
                info[bound] = float(found[-1])
        table[key] = info
    return table


def parse_classes(src: str) -> dict:
    out = {}
    for m in re.finditer(r"PRINT_CONFIG_CLASS_(?:DERIVED_)?DEFINE\s*\(\s*\n?\s*([A-Za-z_0-9]+)\s*,", src):
        start = m.end()
        end = re.search(r"\n\)", src[start:])
        body = src[start:start + end.start()] if end else src[start:start + 20000]
        # ((ConfigOptionFloat, key)) and ((ConfigOptionEnum<InfillPattern>, key))
        out[m.group(1)] = re.findall(
            r"\(\(\s*ConfigOption[A-Za-z]+(?:<[^>]*>)?\s*,\s*([a-zA-Z_0-9]+)\s*\)\)", body
        )
    return out


def parse_list(src: str, name: str) -> list:
    i = src.find(name)
    if i < 0:
        return []
    j = src.find("};", i)
    return re.findall(r'"([a-zA-Z_0-9]+)"', src[i:j])


def parse_defaults_ini(path: str) -> dict:
    out = {}
    for line in read(path).splitlines():
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition(" = ")
        out[k.strip()] = v
    return out


# Keys PrusaSlicer defines in loops (axis names, "filament_" + retract key), which
# the simple regex above cannot see. Types from PrintConfig.cpp.
PRUSA_LOOP_KEYS = {}
for _axis in ("x", "y", "z", "e"):
    PRUSA_LOOP_KEYS[f"machine_max_acceleration_{_axis}"] = ("floats", False)
    PRUSA_LOOP_KEYS[f"machine_max_feedrate_{_axis}"] = ("floats", False)
    PRUSA_LOOP_KEYS[f"machine_max_jerk_{_axis}"] = ("floats", False)
PRUSA_FILAMENT_OVERRIDES = {
    # "filament_" + printer retract key -> nullable vector of the same type
    "retract_length": "floats", "retract_lift": "floats", "retract_lift_above": "floats",
    "retract_lift_below": "floats", "retract_speed": "floats", "deretract_speed": "floats",
    "retract_restart_extra": "floats", "retract_before_travel": "floats",
    "retract_layer_change": "bools", "wipe": "bools", "retract_before_wipe": "percents",
    "retract_length_toolchange": "floats", "retract_restart_extra_toolchange": "floats",
    "travel_ramping_lift": "bools", "travel_slope": "floats", "travel_max_lift": "floats",
    "travel_lift_before_obstacle": "bools", "seam_gap_distance": "floatsorpercents",
}
for _k, _t in PRUSA_FILAMENT_OVERRIDES.items():
    PRUSA_LOOP_KEYS["filament_" + _k] = (_t, True)
# Legacy keys PrusaSlicer < 2.9 reads from the project config (2.9 ignores them and
# uses Prusa_Slicer_wipe_tower_information.xml instead). Emitted for compatibility.
PRUSA_LOOP_KEYS["wipe_tower_x"] = ("floats", False)
PRUSA_LOOP_KEYS["wipe_tower_y"] = ("floats", False)
PRUSA_LOOP_KEYS["wipe_tower_rotation_angle"] = ("floats", False)

EXTRA_PRUSA_KEYS = [
    "print_settings_id", "filament_settings_id", "printer_settings_id",
    "physical_printer_settings_id", "filament_vendor", "printer_vendor", "extruder",
    "compatible_printers_condition_cummulative", "compatible_prints_condition_cummulative",
    "inherits_cummulative", "wipe_tower_x", "wipe_tower_y", "wipe_tower_rotation_angle",
]

BAMBU_LOOP_KEYS = {}
for _axis in ("x", "y", "z", "e"):
    BAMBU_LOOP_KEYS[f"machine_max_acceleration_{_axis}"] = ("floats", False)
    BAMBU_LOOP_KEYS[f"machine_max_speed_{_axis}"] = ("floats", False)
    BAMBU_LOOP_KEYS[f"machine_max_jerk_{_axis}"] = ("floats", False)
BAMBU_FILAMENT_OVERRIDES = {
    "retraction_length": "floats", "z_hop": "floats", "z_hop_types": "enums",
    "retraction_speed": "floats", "deretraction_speed": "floats",
    "retract_restart_extra": "floats", "retraction_minimum_travel": "floats",
    "retract_when_changing_layer": "bools", "wipe": "bools", "wipe_distance": "floats",
    "retract_before_wipe": "percents", "retract_lift_above": "floats",
    "retract_lift_below": "floats", "long_retractions_when_cut": "bools",
    "retraction_distances_when_cut": "floats", "retract_length_toolchange": "floats",
    "retract_restart_extra_toolchange": "floats",
}
for _k, _t in BAMBU_FILAMENT_OVERRIDES.items():
    BAMBU_LOOP_KEYS["filament_" + _k] = (_t, True)


def fmt_set(name: str, items) -> str:
    items = sorted(set(items))
    lines = [f"{name} = frozenset(("]
    row = "   "
    for it in items:
        piece = f' "{it}",'
        if len(row) + len(piece) > 96:
            lines.append(row)
            row = "   "
        row += piece
    lines.append(row)
    lines.append("))")
    return "\n".join(lines)


def write_prusa(cache: str, ref: str) -> None:
    cpp = read(os.path.join(cache, "prusa", "PrintConfig.cpp"))
    opts = parse_options(cpp)
    classes = parse_classes(read(os.path.join(cache, "prusa", "PrintConfig.hpp")))
    preset = read(os.path.join(cache, "prusa", "Preset.cpp"))
    bundle = read(os.path.join(cache, "prusa", "PresetBundle.cpp"))
    print_keys = parse_list(preset, "s_Preset_print_options")
    filament_keys = parse_list(preset, "s_Preset_filament_options")
    printer_keys = parse_list(preset, "s_Preset_printer_options")
    limits_keys = parse_list(preset, "s_Preset_machine_limits_options")
    project_keys = parse_list(bundle, "s_project_options")
    m = re.search(r"m_extruder_option_keys\s*=\s*\{(.*?)\};", cpp, re.S)
    extruder_keys = re.findall(r'"([a-zA-Z_0-9]+)"', m.group(1)) if m else []
    object_keys = classes.get("PrintObjectConfig", [])
    region_keys = classes.get("PrintRegionConfig", [])

    defaults_path = os.path.join(cache, "prusa", "defaults.ini")
    defaults = parse_defaults_ini(defaults_path) if os.path.exists(defaults_path) else {}

    for k, (t, nullable) in PRUSA_LOOP_KEYS.items():
        opts.setdefault(k, {"type": t, "nullable": nullable})

    fff = set(print_keys) | set(filament_keys) | set(printer_keys) | set(limits_keys)
    fff |= set(extruder_keys) | set(project_keys) | set(object_keys) | set(region_keys)
    fff |= set(EXTRA_PRUSA_KEYS) | set(defaults)
    missing = sorted(k for k in fff if k not in opts)
    if missing:
        print("WARNING: no type information for", missing, file=sys.stderr)

    lines = [
        '"""PrusaSlicer FFF config schema (GENERATED by tools/gen_schema.py; do not edit).',
        "",
        f"Source: prusa3d/PrusaSlicer @ {ref}. OPTIONS maps key -> (type, nullable, enum values, min, max).",
        "Types: float(s), int(s), bool(s), string(s), percent(s), floatorpercent(s), enum(s), point(s).",
        "A plural type is a per-extruder / per-filament vector. min/max are None when unbounded.",
        '"""',
        "",
        "OPTIONS = {",
    ]
    for k in sorted(fff):
        if k not in opts:
            continue
        o = opts[k]
        enum = tuple(o["enum"]) if o.get("enum") else None
        lines.append(f'    "{k}": ({o["type"]!r}, {o["nullable"]!r}, {enum!r}, {o.get("min")!r}, {o.get("max")!r}),')
    lines.append("}")
    lines.append("")
    lines.append("# Default values as PrusaSlicer serializes them (from `prusa-slicer-console --save`).")
    lines.append("DEFAULTS = {")
    for k in sorted(defaults):
        if k in fff:
            lines.append(f'    "{k}": {defaults[k]!r},')
    lines.append("}")
    lines.append("")
    lines.append(fmt_set("PRINT_KEYS", print_keys))
    lines.append(fmt_set("FILAMENT_KEYS", filament_keys))
    lines.append(fmt_set("PRINTER_KEYS", list(printer_keys) + list(limits_keys) + list(extruder_keys)))
    lines.append(fmt_set("MACHINE_LIMITS_KEYS", limits_keys))
    lines.append(fmt_set("EXTRUDER_KEYS", extruder_keys))
    lines.append(fmt_set("PROJECT_KEYS", project_keys))
    lines.append(fmt_set("OBJECT_KEYS", object_keys))
    lines.append(fmt_set("REGION_KEYS", region_keys))
    lines.append("")
    with open(os.path.join(PKG, "prusa_schema.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"prusa_schema.py: {len([k for k in fff if k in opts])} keys")


def write_bambu(cache: str, ref: str) -> None:
    opts = parse_options(read(os.path.join(cache, "bambu", "PrintConfig.cpp")))
    classes = parse_classes(read(os.path.join(cache, "bambu", "PrintConfig.hpp")))
    preset = read(os.path.join(cache, "bambu", "Preset.cpp"))
    print_keys = parse_list(preset, "s_Preset_print_options")
    filament_keys = parse_list(preset, "s_Preset_filament_options")
    printer_keys = parse_list(preset, "s_Preset_printer_options")
    limits_keys = parse_list(preset, "s_Preset_machine_limits_options")
    for k, (t, nullable) in BAMBU_LOOP_KEYS.items():
        opts.setdefault(k, {"type": t, "nullable": nullable})
    lines = [
        '"""Bambu Studio config schema (GENERATED by tools/gen_schema.py; do not edit).',
        "",
        f"Source: bambulab/BambuStudio @ {ref}. OPTIONS maps key -> (type, nullable, enum values, min, max).",
        '"""',
        "",
        "OPTIONS = {",
    ]
    for k in sorted(opts):
        o = opts[k]
        enum = tuple(o["enum"]) if o.get("enum") else None
        lines.append(f'    "{k}": ({o["type"]!r}, {o["nullable"]!r}, {enum!r}, {o.get("min")!r}, {o.get("max")!r}),')
    lines.append("}")
    lines.append("")
    lines.append(fmt_set("PRINT_KEYS", print_keys))
    lines.append(fmt_set("FILAMENT_KEYS", filament_keys))
    lines.append(fmt_set("PRINTER_KEYS", list(printer_keys) + list(limits_keys)))
    lines.append(fmt_set("MACHINE_LIMITS_KEYS", limits_keys))
    lines.append(fmt_set("OBJECT_KEYS", classes.get("PrintObjectConfig", [])))
    lines.append(fmt_set("REGION_KEYS", classes.get("PrintRegionConfig", [])))
    lines.append("")
    with open(os.path.join(PKG, "bambu_schema.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"bambu_schema.py: {len(opts)} keys")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(HERE), "build", "slicer-src"))
    ap.add_argument("--prusa-ref", default="version_2.9.6")
    ap.add_argument("--bambu-ref", default="master")
    ap.add_argument("--prusa-defaults", help="ini written by `prusa-slicer-console --save FILE` (optional)")
    args = ap.parse_args()
    for name, path in PRUSA_FILES.items():
        fetch("prusa3d/PrusaSlicer", args.prusa_ref, path, os.path.join(args.cache, "prusa", name))
    for name, path in BAMBU_FILES.items():
        fetch("bambulab/BambuStudio", args.bambu_ref, path, os.path.join(args.cache, "bambu", name))
    if args.prusa_defaults:
        os.makedirs(os.path.join(args.cache, "prusa"), exist_ok=True)
        shutil.copy(args.prusa_defaults, os.path.join(args.cache, "prusa", "defaults.ini"))
    write_prusa(args.cache, args.prusa_ref)
    write_bambu(args.cache, args.bambu_ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
