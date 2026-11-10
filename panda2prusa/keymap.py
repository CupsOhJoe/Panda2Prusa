"""Bambu Studio -> PrusaSlicer configuration key mapping.

Bambu Studio forked PrusaSlicer and renamed most settings; this table maps them
back. Each ``Rule`` names the PrusaSlicer key, the Bambu key it comes from, and an
optional converter for values whose encoding or enum names differ. Converters
return ``None`` to say "no sensible PrusaSlicer value, keep the base profile's".

Rules that need several source keys at once (supports, ironing, bed temperature by
plate type, ...) live in ``translate.py`` as explicit functions; keeping them out of
the table makes the table readable as documentation of what carries over.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import prusa_schema as S


@dataclass(frozen=True)
class Rule:
    prusa: str
    bambu: str
    conv: Optional[Callable[[str], Optional[str]]] = None
    # PrusaSlicer key to resolve a Bambu "NN%" value against when PrusaSlicer wants a
    # plain number (Bambu allows percent-of-default for accelerations).
    percent_base: Optional[str] = None
    # Bambu values that intentionally map to "keep the base value" (no warning).
    quiet: tuple = ()
    note: str = ""


# -- value converters ---------------------------------------------------------------

def to_bool(v: str) -> Optional[str]:
    s = v.strip().lower()
    if s in ("1", "true", "yes", "on"):
        return "1"
    if s in ("0", "false", "no", "off", ""):
        return "0"
    return None


def invert_bool(v: str) -> Optional[str]:
    b = to_bool(v)
    return None if b is None else ("0" if b == "1" else "1")


def non_negative(v: str) -> Optional[str]:
    """Bambu uses -1 for "auto" in a few places PrusaSlicer has no equivalent for."""
    try:
        return v if float(v.rstrip("%")) >= 0 else None
    except ValueError:
        return None


def positive_or_skip(v: str) -> Optional[str]:
    """Bambu speeds where 0 means "don't slow down" / "disabled"."""
    try:
        return v if float(v.rstrip("%")) > 0 else None
    except ValueError:
        return None


def enum_map(mapping: dict, passthrough: Optional[tuple] = None) -> Callable[[str], Optional[str]]:
    """Translate enum names; values listed in ``passthrough`` are kept as-is."""

    def conv(v: str) -> Optional[str]:
        if v in mapping:
            return mapping[v]
        if passthrough and v in passthrough:
            return v
        return None

    return conv


def shrink_to_compensation(v: str) -> Optional[str]:
    """Bambu filament_shrink 98% (part shrinks to 98%) -> PrusaSlicer compensation 2%."""
    try:
        return f"{round(100.0 - float(v.rstrip('%')), 3):g}%"
    except ValueError:
        return None


def hrc_to_abrasive(v: str) -> Optional[str]:
    """Bambu stores the nozzle hardness a filament needs; >= 40 HRC means abrasive."""
    try:
        return "1" if int(float(v)) >= 40 else "0"
    except ValueError:
        return None


def filament_type_name(v: str) -> Optional[str]:
    """PrusaSlicer profiles key their logic on a few type names; normalize Bambu's."""
    t = v.strip()
    upper = t.upper()
    if upper in ("TPU", "TPU-AMS", "TPU-HF"):
        return "FLEX"
    return t or None


def arc_fitting(v: str) -> Optional[str]:
    b = to_bool(v)
    return None if b is None else ("emit_center" if b == "1" else "disabled")


def seam_position(v: str) -> Optional[str]:
    return {"nearest": "nearest", "aligned": "aligned", "back": "rear", "random": "random"}.get(v)


FILL_PATTERN = enum_map(
    {
        "zig-zag": "rectilinear",
        "tri-hexagon": "stars",
        "crosshatch": "grid",
        "crosszag": "zigzag",
        "lockedzag": "zigzag",
        "2dlattice": "grid",
    },
    passthrough=S.OPTIONS["fill_pattern"][2],
)

TOP_BOTTOM_PATTERN = enum_map(
    {
        "zig-zag": "rectilinear",
        "monotonicline": "monotoniclines",
        "globalmonotonicline": "monotoniclines",
    },
    passthrough=S.OPTIONS["top_fill_pattern"][2],
)

SUPPORT_PATTERN = enum_map(
    {"default": "rectilinear", "lightning": "rectilinear", "hollow": "rectilinear"},
    passthrough=S.OPTIONS["support_material_pattern"][2],
)

SUPPORT_INTERFACE_PATTERN = enum_map(
    {"rectilinear_interlaced": "rectilinear", "grid": "rectilinear"},
    passthrough=S.OPTIONS["support_material_interface_pattern"][2],
)

FUZZY_SKIN = enum_map(
    {"allwalls": "all", "disabled_fuzzy": "none"}, passthrough=S.OPTIONS["fuzzy_skin"][2]
)

SCARF_PLACEMENT = enum_map({"none": "nowhere", "external": "contours", "all": "everywhere"})

TOP_ONE_PERIMETER = enum_map({"not apply": "none", "all top": "top", "topmost": "topmost"})

BRIM_TYPE = enum_map(
    {"auto_brim": "outer_only", "brim_ears": "outer_only"},
    passthrough=S.OPTIONS["brim_type"][2],
)

INFILL_RETRACTION = enum_map({"Enabled": "1", "Disabled": "0"})  # "Auto" keeps the base value

SAME_ENUM = lambda key: enum_map({}, passthrough=S.OPTIONS[key][2])  # noqa: E731


# -- print (process) settings -------------------------------------------------------

PRINT_RULES = [
    # Layers and perimeters
    Rule("layer_height", "layer_height"),
    Rule("first_layer_height", "initial_layer_print_height"),
    Rule("perimeters", "wall_loops"),
    Rule("spiral_vase", "spiral_mode", to_bool),
    Rule("slice_closing_radius", "slice_closing_radius"),
    Rule("slicing_mode", "slicing_mode", SAME_ENUM("slicing_mode")),
    Rule("top_solid_layers", "top_shell_layers"),
    Rule("top_solid_min_thickness", "top_shell_thickness"),
    Rule("bottom_solid_layers", "bottom_shell_layers"),
    Rule("bottom_solid_min_thickness", "bottom_shell_thickness"),
    Rule("ensure_vertical_shell_thickness", "ensure_vertical_shell_thickness",
         SAME_ENUM("ensure_vertical_shell_thickness")),
    Rule("extra_perimeters_on_overhangs", "extra_perimeters_on_overhangs", to_bool),  # OrcaSlicer
    Rule("avoid_crossing_perimeters", "reduce_crossing_wall", to_bool),
    Rule("avoid_crossing_perimeters_max_detour", "max_travel_detour_distance"),
    Rule("thin_walls", "detect_thin_wall", to_bool),
    Rule("overhangs", "detect_overhang_wall", to_bool),
    Rule("seam_position", "seam_position", seam_position),
    Rule("staggered_inner_seams", "staggered_inner_seams", to_bool),  # OrcaSlicer
    Rule("seam_gap_distance", "seam_gap"),
    Rule("only_one_perimeter_first_layer", "only_one_wall_first_layer", to_bool),
    Rule("top_one_perimeter_type", "top_one_wall_type", TOP_ONE_PERIMETER),
    # Scarf seams
    Rule("scarf_seam_placement", "seam_slope_type", SCARF_PLACEMENT),
    Rule("scarf_seam_only_on_smooth", "seam_slope_conditional", to_bool),
    Rule("scarf_seam_start_height", "seam_slope_start_height"),
    Rule("scarf_seam_entire_loop", "seam_slope_entire_loop", to_bool),
    Rule("scarf_seam_length", "seam_slope_min_length"),
    Rule("scarf_seam_on_inner_perimeters", "seam_slope_inner_walls", to_bool),
    # Infill
    Rule("fill_density", "sparse_infill_density"),
    Rule("fill_pattern", "sparse_infill_pattern", FILL_PATTERN),
    Rule("top_fill_pattern", "top_surface_pattern", TOP_BOTTOM_PATTERN),
    Rule("bottom_fill_pattern", "bottom_surface_pattern", TOP_BOTTOM_PATTERN),
    Rule("automatic_infill_combination", "infill_combination", to_bool),
    Rule("fill_angle", "infill_direction"),
    Rule("bridge_angle", "bridge_angle"),
    Rule("solid_infill_below_area", "minimum_sparse_infill_area"),
    Rule("only_retract_when_crossing_perimeters", "reduce_infill_retraction_mode", INFILL_RETRACTION,
         quiet=("Auto",)),
    Rule("infill_first", "is_infill_first", to_bool),
    Rule("infill_overlap", "infill_wall_overlap"),
    Rule("infill_anchor", "sparse_infill_anchor"),
    Rule("infill_anchor_max", "sparse_infill_anchor_max"),
    # Ironing (ironing on/off + type handled in translate.py)
    Rule("ironing_flowrate", "ironing_flow"),
    Rule("ironing_speed", "ironing_speed"),
    Rule("ironing_spacing", "ironing_spacing"),
    # Fuzzy skin
    Rule("fuzzy_skin", "fuzzy_skin", FUZZY_SKIN),
    Rule("fuzzy_skin_thickness", "fuzzy_skin_thickness"),
    Rule("fuzzy_skin_point_dist", "fuzzy_skin_point_distance"),
    # Speeds
    Rule("perimeter_speed", "inner_wall_speed"),
    Rule("small_perimeter_speed", "small_perimeter_speed"),
    Rule("external_perimeter_speed", "outer_wall_speed"),
    Rule("infill_speed", "sparse_infill_speed"),
    Rule("solid_infill_speed", "internal_solid_infill_speed"),
    Rule("top_solid_infill_speed", "top_surface_speed"),
    Rule("support_material_speed", "support_speed"),
    Rule("support_material_interface_speed", "support_interface_speed"),
    Rule("bridge_speed", "bridge_speed"),
    Rule("gap_fill_speed", "gap_infill_speed", positive_or_skip),
    Rule("travel_speed", "travel_speed"),
    Rule("travel_speed_z", "travel_speed_z"),
    Rule("first_layer_speed", "initial_layer_speed"),
    Rule("first_layer_infill_speed", "initial_layer_infill_speed"),
    Rule("enable_dynamic_overhang_speeds", "enable_overhang_speed", to_bool),
    Rule("max_volumetric_extrusion_rate_slope_positive", "max_volumetric_extrusion_rate_slope_positive"),
    Rule("max_volumetric_extrusion_rate_slope_negative", "max_volumetric_extrusion_rate_slope_negative"),
    # Accelerations (0 = use default in both slicers)
    Rule("default_acceleration", "default_acceleration"),
    Rule("perimeter_acceleration", "inner_wall_acceleration", percent_base="default_acceleration"),
    Rule("external_perimeter_acceleration", "outer_wall_acceleration", percent_base="default_acceleration"),
    Rule("infill_acceleration", "sparse_infill_acceleration", percent_base="default_acceleration"),
    Rule("solid_infill_acceleration", "internal_solid_infill_acceleration", percent_base="default_acceleration"),
    Rule("top_solid_infill_acceleration", "top_surface_acceleration", percent_base="default_acceleration"),
    Rule("bridge_acceleration", "bridge_acceleration", percent_base="default_acceleration"),  # OrcaSlicer
    Rule("first_layer_acceleration", "initial_layer_acceleration", percent_base="default_acceleration"),
    Rule("travel_acceleration", "travel_acceleration", percent_base="default_acceleration"),
    Rule("travel_short_distance_acceleration", "travel_short_distance_acceleration",
         percent_base="travel_acceleration"),
    Rule("wipe_tower_acceleration", "prime_tower_acceleration", percent_base="default_acceleration"),
    # Skirt and brim
    Rule("skirts", "skirt_loops"),
    Rule("skirt_distance", "skirt_distance"),
    Rule("skirt_height", "skirt_height"),
    Rule("draft_shield", "draft_shield", SAME_ENUM("draft_shield")),
    Rule("brim_width", "brim_width"),
    Rule("brim_separation", "brim_object_gap"),
    Rule("brim_type", "brim_type", BRIM_TYPE),
    # Supports (support on/off, auto and style handled in translate.py)
    Rule("support_material_threshold", "support_threshold_angle"),
    Rule("support_material_enforce_layers", "enforce_support_layers"),
    Rule("raft_layers", "raft_layers"),
    Rule("raft_first_layer_density", "raft_first_layer_density"),
    Rule("raft_first_layer_expansion", "raft_first_layer_expansion", non_negative),
    Rule("raft_contact_distance", "raft_contact_distance"),
    Rule("raft_expansion", "raft_expansion"),
    Rule("support_material_pattern", "support_base_pattern", SUPPORT_PATTERN),
    Rule("support_material_spacing", "support_base_pattern_spacing"),
    Rule("support_material_synchronize_layers", "independent_support_layer_height", invert_bool),
    Rule("support_material_angle", "support_angle"),
    Rule("support_material_interface_layers", "support_interface_top_layers"),
    Rule("support_material_bottom_interface_layers", "support_interface_bottom_layers"),
    Rule("support_material_interface_pattern", "support_interface_pattern", SUPPORT_INTERFACE_PATTERN),
    Rule("support_material_interface_spacing", "support_interface_spacing"),
    Rule("support_material_interface_contact_loops", "support_interface_loop_pattern", to_bool),
    Rule("support_material_contact_distance", "support_top_z_distance"),
    Rule("support_material_bottom_contact_distance", "support_bottom_z_distance"),
    Rule("support_material_buildplate_only", "support_on_build_plate_only", to_bool),
    Rule("support_material_xy_spacing", "support_object_xy_distance"),
    Rule("support_tree_angle", "tree_support_branch_angle"),
    Rule("support_tree_branch_diameter", "tree_support_branch_diameter"),
    Rule("support_tree_branch_diameter_angle", "tree_support_branch_diameter_angle"),
    Rule("support_tree_branch_distance", "tree_support_branch_distance"),
    Rule("support_tree_tip_diameter", "tree_support_tip_diameter"),
    Rule("support_tree_top_rate", "tree_support_top_rate"),  # OrcaSlicer
    Rule("dont_support_bridges", "bridge_no_support", to_bool),
    Rule("thick_bridges", "thick_bridges", to_bool),
    # Multiple extruders (extruder numbers are remapped in translate.py)
    Rule("perimeter_extruder", "wall_filament"),
    Rule("infill_extruder", "sparse_infill_filament"),
    Rule("solid_infill_extruder", "solid_infill_filament"),
    Rule("support_material_extruder", "support_filament"),
    Rule("support_material_interface_extruder", "support_interface_filament"),
    Rule("ooze_prevention", "ooze_prevention", to_bool),
    Rule("standby_temperature_delta", "standby_temperature_delta"),
    Rule("interface_shells", "interface_shells", to_bool),
    Rule("wipe_tower", "enable_prime_tower", to_bool),
    Rule("wipe_tower_width", "prime_tower_width"),
    Rule("wipe_tower_brim_width", "prime_tower_brim_width", non_negative),
    Rule("wipe_tower_no_sparse_layers", "wipe_tower_no_sparse_layers", to_bool),
    Rule("wipe_tower_extra_spacing", "prime_tower_infill_gap"),
    Rule("mmu_segmented_region_max_width", "mmu_segmented_region_max_width"),
    Rule("mmu_segmented_region_interlocking_depth", "mmu_segmented_region_interlocking_depth"),
    Rule("interlocking_beam", "interlocking_beam", to_bool),
    Rule("interlocking_beam_width", "interlocking_beam_width"),
    Rule("interlocking_orientation", "interlocking_orientation"),
    Rule("interlocking_beam_layer_count", "interlocking_beam_layer_count"),
    Rule("interlocking_depth", "interlocking_depth"),
    Rule("interlocking_boundary_avoidance", "interlocking_boundary_avoidance"),
    # Advanced: extrusion widths, flow, compensation, resolution
    Rule("extrusion_width", "line_width"),
    Rule("first_layer_extrusion_width", "initial_layer_line_width"),
    Rule("perimeter_extrusion_width", "inner_wall_line_width"),
    Rule("external_perimeter_extrusion_width", "outer_wall_line_width"),
    Rule("infill_extrusion_width", "sparse_infill_line_width"),
    Rule("solid_infill_extrusion_width", "internal_solid_infill_line_width"),
    Rule("top_infill_extrusion_width", "top_surface_line_width"),
    Rule("support_material_extrusion_width", "support_line_width"),
    Rule("bridge_flow_ratio", "bridge_flow"),
    Rule("elefant_foot_compensation", "elefant_foot_compensation"),
    Rule("xy_size_compensation", "xy_contour_compensation"),
    Rule("resolution", "resolution"),
    Rule("arc_fitting", "enable_arc_fitting", arc_fitting),
    Rule("perimeter_generator", "wall_generator", SAME_ENUM("perimeter_generator")),
    Rule("wall_transition_length", "wall_transition_length"),
    Rule("wall_transition_filter_deviation", "wall_transition_filter_deviation"),
    Rule("wall_transition_angle", "wall_transition_angle"),
    Rule("wall_distribution_count", "wall_distribution_count"),
    Rule("min_feature_size", "min_feature_size"),
    Rule("min_bead_width", "min_bead_width"),
    Rule("notes", "process_notes"),
]

# Bambu overhang speed bands -> PrusaSlicer "speed for N% overlap" slots. Bambu names
# the band by overhang amount (4/4 = 75-100 % overhanging), PrusaSlicer by overlap
# with the layer below (0 % overlap = fully floating), so the order flips.
OVERHANG_SPEED_RULES = [
    ("overhang_speed_0", "overhang_totally_speed"),
    ("overhang_speed_1", "overhang_4_4_speed"),
    ("overhang_speed_2", "overhang_3_4_speed"),
    ("overhang_speed_3", "overhang_2_4_speed"),
]


# -- filament settings (one value per filament) ------------------------------------

FILAMENT_RULES = [
    Rule("filament_colour", "filament_colour"),
    Rule("filament_diameter", "filament_diameter"),
    Rule("filament_type", "filament_type", filament_type_name),
    Rule("filament_soluble", "filament_soluble", to_bool),
    Rule("filament_abrasive", "required_nozzle_HRC", hrc_to_abrasive),
    Rule("filament_notes", "filament_notes"),
    Rule("filament_max_volumetric_speed", "filament_max_volumetric_speed", positive_or_skip),
    Rule("extrusion_multiplier", "filament_flow_ratio"),
    Rule("filament_density", "filament_density"),
    Rule("filament_cost", "filament_cost"),
    Rule("filament_minimal_purge_on_wipe_tower", "filament_minimal_purge_on_wipe_tower"),
    Rule("temperature", "nozzle_temperature"),
    Rule("first_layer_temperature", "nozzle_temperature_initial_layer"),
    # bed temperatures depend on the selected plate type: translate.py
    Rule("fan_always_on", "reduce_fan_stop_start_freq", to_bool),
    Rule("cooling", "slow_down_for_layer_cooling", to_bool),
    Rule("cooling_slowdown_logic", "cooling_slowdown_logic", SAME_ENUM("cooling_slowdown_logic")),
    Rule("cooling_perimeter_transition_distance", "cooling_perimeter_transition_distance"),
    Rule("min_fan_speed", "fan_min_speed"),
    Rule("max_fan_speed", "fan_max_speed"),
    Rule("bridge_fan_speed", "overhang_fan_speed"),
    Rule("disable_fan_first_layers", "close_fan_the_first_x_layers"),
    Rule("full_fan_speed_layer", "full_fan_speed_layer"),
    Rule("fan_below_layer_time", "fan_cooling_layer_time"),
    Rule("slowdown_below_layer_time", "slow_down_layer_time"),
    Rule("min_print_speed", "slow_down_min_speed"),
    Rule("start_filament_gcode", "filament_start_gcode"),
    Rule("end_filament_gcode", "filament_end_gcode"),
    Rule("filament_shrinkage_compensation_xy", "filament_shrink", shrink_to_compensation),
    # Per-filament overrides of the printer's retraction (nil = no override, both sides)
    Rule("filament_retract_length", "filament_retraction_length"),
    Rule("filament_retract_lift", "filament_z_hop"),
    Rule("filament_retract_lift_above", "filament_retract_lift_above"),
    Rule("filament_retract_lift_below", "filament_retract_lift_below"),
    Rule("filament_retract_speed", "filament_retraction_speed"),
    Rule("filament_deretract_speed", "filament_deretraction_speed"),
    Rule("filament_retract_restart_extra", "filament_retract_restart_extra"),
    Rule("filament_retract_before_travel", "filament_retraction_minimum_travel"),
    Rule("filament_retract_layer_change", "filament_retract_when_changing_layer"),
    Rule("filament_wipe", "filament_wipe"),
    Rule("filament_retract_before_wipe", "filament_retract_before_wipe"),
    Rule("filament_retract_length_toolchange", "filament_retract_length_toolchange"),
    Rule("filament_retract_restart_extra_toolchange", "filament_retract_restart_extra_toolchange"),
]

# Filament keys whose Bambu source is a *printer* scalar (same value for every filament).
FILAMENT_FROM_PRINTER_RULES = [
    Rule("filament_load_time", "machine_load_filament_time"),
    Rule("filament_unload_time", "machine_unload_filament_time"),
]


# -- printer settings (used only when no PrusaSlicer printer profile is given) ------

PRINTER_RULES = [
    Rule("bed_shape", "printable_area"),
    Rule("max_print_height", "printable_height"),
    Rule("gcode_flavor", "gcode_flavor", SAME_ENUM("gcode_flavor")),
    Rule("use_relative_e_distances", "use_relative_e_distances", to_bool),
    Rule("use_firmware_retraction", "use_firmware_retraction", to_bool),
    Rule("single_extruder_multi_material", "single_extruder_multi_material", to_bool),
    Rule("printer_model", "printer_model"),
    Rule("printer_variant", "printer_variant"),
    Rule("printer_notes", "printer_notes"),
    Rule("silent_mode", "silent_mode", to_bool),
    Rule("extruder_clearance_radius", "extruder_clearance_max_radius"),
    Rule("extruder_clearance_height", "extruder_clearance_height_to_rod"),
    Rule("template_custom_gcode", "template_custom_gcode"),
    Rule("pause_print_gcode", "machine_pause_gcode"),
    Rule("default_print_profile", "default_print_profile"),
]

# Per-extruder printer vectors.
EXTRUDER_RULES = [
    Rule("nozzle_diameter", "nozzle_diameter"),
    Rule("min_layer_height", "min_layer_height"),
    Rule("max_layer_height", "max_layer_height"),
    Rule("retract_length", "retraction_length"),
    Rule("retract_lift", "z_hop"),
    Rule("retract_lift_above", "retract_lift_above"),
    Rule("retract_lift_below", "retract_lift_below"),
    Rule("retract_speed", "retraction_speed"),
    Rule("deretract_speed", "deretraction_speed"),
    Rule("retract_before_wipe", "retract_before_wipe"),
    Rule("retract_restart_extra", "retract_restart_extra"),
    Rule("retract_before_travel", "retraction_minimum_travel"),
    Rule("wipe", "wipe", to_bool),
    Rule("retract_layer_change", "retract_when_changing_layer", to_bool),
    Rule("retract_length_toolchange", "retract_length_toolchange"),
    Rule("retract_restart_extra_toolchange", "retract_restart_extra_toolchange"),
]

# (PrusaSlicer machine limit, Bambu machine limit)
MACHINE_LIMIT_RULES = [
    ("machine_max_acceleration_x", "machine_max_acceleration_x"),
    ("machine_max_acceleration_y", "machine_max_acceleration_y"),
    ("machine_max_acceleration_z", "machine_max_acceleration_z"),
    ("machine_max_acceleration_e", "machine_max_acceleration_e"),
    ("machine_max_acceleration_extruding", "machine_max_acceleration_extruding"),
    ("machine_max_acceleration_retracting", "machine_max_acceleration_retracting"),
    ("machine_max_acceleration_travel", "machine_max_acceleration_travel"),
    ("machine_max_feedrate_x", "machine_max_speed_x"),
    ("machine_max_feedrate_y", "machine_max_speed_y"),
    ("machine_max_feedrate_z", "machine_max_speed_z"),
    ("machine_max_feedrate_e", "machine_max_speed_e"),
    ("machine_max_jerk_x", "machine_max_jerk_x"),
    ("machine_max_jerk_y", "machine_max_jerk_y"),
    ("machine_max_jerk_z", "machine_max_jerk_z"),
    ("machine_max_jerk_e", "machine_max_jerk_e"),
    ("machine_min_extruding_rate", "machine_min_extruding_rate"),
    ("machine_min_travel_rate", "machine_min_travel_rate"),
]

# Minimal machine-agnostic G-code used in place of Bambu's start/end macros, which
# reference variables PrusaSlicer's macro processor does not define.
GENERIC_START_GCODE = (
    "M104 S[first_layer_temperature] ; set extruder temp\\n"
    "M140 S[first_layer_bed_temperature] ; set bed temp\\n"
    "G28 ; home all\\n"
    "M190 S[first_layer_bed_temperature] ; wait for bed temp\\n"
    "M109 S[first_layer_temperature] ; wait for extruder temp\\n"
    "G1 Z5 F3000 ; lift nozzle\\n"
    "G92 E0"
)
GENERIC_BEFORE_LAYER_GCODE = ";BEFORE_LAYER_CHANGE\\nG92 E0.0\\n;[layer_z]"
GENERIC_LAYER_GCODE = ";AFTER_LAYER_CHANGE\\n;[layer_z]\\nG92 E0"
GENERIC_END_GCODE = (
    "M104 S0 ; turn off extruder\\n"
    "M140 S0 ; turn off bed\\n"
    "G91 ; relative\\n"
    "G1 Z10 F3000 ; lift\\n"
    "G90 ; absolute\\n"
    "M84 ; disable motors"
)

# Keys that are per-object / per-volume in PrusaSlicer. Object metadata from Bambu's
# model_settings.config is mapped through PRINT_RULES restricted to these.
OBJECT_SCOPE_KEYS = frozenset(S.OBJECT_KEYS | S.REGION_KEYS | {"extruder"})

PRINT_RULES_BY_BAMBU = {r.bambu: r for r in PRINT_RULES}
