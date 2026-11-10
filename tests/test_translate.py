"""Bambu -> PrusaSlicer settings translation (synthetic projects, no files needed)."""

import pytest

from panda2prusa import prusa_schema as S
from panda2prusa.bambu_config import BambuConfig
from panda2prusa.prusa_config import PrusaConfig, enum_values, is_vector
from panda2prusa.translate import (
    MODE_BAMBU,
    MODE_REFERENCE,
    SETTINGS_BAMBU,
    SETTINGS_PRESETS,
    TargetPresets,
    translate_object_settings,
    translate_project,
    validate,
)


def _bambu(**overrides):
    raw = {
        "nozzle_diameter": ["0.4"],
        "printable_area": ["0x0", "256x0", "256x256", "0x256"],
        "printable_height": "256",
        "gcode_flavor": "marlin",
        "single_extruder_multi_material": "1",
        "printer_settings_id": "Bambu Lab P1S 0.4 nozzle",
        "printer_model": "Bambu Lab P1S",
        "print_settings_id": "0.20mm Standard @BBL P1S",
        "layer_height": "0.2",
        "initial_layer_print_height": "0.25",
        "wall_loops": "3",
        "top_shell_layers": "5",
        "bottom_shell_layers": "4",
        "sparse_infill_density": "20%",
        "sparse_infill_pattern": "tri-hexagon",
        "top_surface_pattern": "monotonicline",
        "seam_position": "back",
        "wall_sequence": "outer wall/inner wall",
        "ironing_type": "topmost",
        "ironing_flow": "12%",
        "enable_support": "1",
        "support_type": "tree(auto)",
        "support_style": "default",
        "support_threshold_angle": "35",
        "brim_type": "auto_brim",
        "brim_width": "5",
        "print_sequence": "by object",
        "default_acceleration": "8000",
        "sparse_infill_acceleration": "50%",
        "inner_wall_acceleration": "0",
        "outer_wall_speed": "180",
        "overhang_1_4_speed": "0",
        "overhang_2_4_speed": "50",
        "overhang_3_4_speed": "30",
        "overhang_4_4_speed": "10",
        "overhang_totally_speed": "8",
        "wall_filament": "3",
        "sparse_infill_filament": "0",
        "enable_arc_fitting": "1",
        "top_one_wall_type": "all top",
        "fuzzy_skin": "allwalls",
        "xy_hole_compensation": "0.1",
        "retraction_length": ["0.8"],
        "z_hop": ["0.4"],
        "machine_max_acceleration_x": ["20000", "20000"],
        "machine_max_speed_x": ["500", "500"],
        "curr_bed_type": "Textured PEI Plate",
        "filament_colour": ["#111111", "#222222", "#333333"],
        "filament_type": ["PLA", "TPU", "PETG"],
        "filament_settings_id": ["Bambu PLA Basic", "Bambu TPU 95A", "Bambu PETG Basic"],
        "filament_vendor": ["Bambu Lab", "Bambu Lab", "Bambu Lab"],
        "nozzle_temperature": ["220", "230", "255"],
        "nozzle_temperature_initial_layer": ["225", "230", "260"],
        "textured_plate_temp": ["55", "40", "70"],
        "textured_plate_temp_initial_layer": ["60", "40", "75"],
        "hot_plate_temp": ["99", "99", "99"],
        "filament_flow_ratio": ["0.98", "1.0", "0.95"],
        "filament_shrink": ["100%", "100%", "99.5%"],
        "required_nozzle_HRC": ["3", "3", "55"],
        "filament_max_volumetric_speed": ["21", "3.6", "0"],
        "filament_retraction_length": ["nil", "1.5", "nil"],
        "slow_down_layer_time": ["4", "8", "6"],
        "fan_min_speed": ["100", "40", "60"],
        "flush_volumes_matrix": [
            "0", "100", "200",
            "300", "0", "400",
            "500", "600", "0",
        ],
        "flush_multiplier": ["0.5"],
        "filament_prime_volume": ["30", "45", "45"],
        "wipe_tower_x": ["240"],
        "wipe_tower_y": ["10"],
        "enable_prime_tower": "1",
        "prime_tower_width": "35",
    }
    raw.update(overrides)
    return BambuConfig(raw)


def _xl_printer(n=5):
    cfg = PrusaConfig(
        {
            "printer_technology": "FFF",
            "bed_shape": "0x0,360x0,360x360,0x360",
            "max_print_height": "360",
            "gcode_flavor": "marlin2",
            "single_extruder_multi_material": "0",
            "start_gcode": "M17 ; real XL start",
            "printer_model": "XL5IS",
            "printer_notes": "PRINTER_MODEL_XLIS",
            "extruder_colour": ";".join(["#FF8000", "#DB5182", "#3EC0FF", "#FF4F4F", "#FBEB7D"][:n]),
        }
    )
    cfg.set_vector("nozzle_diameter", ["0.4"] * n)
    cfg.set_vector("retract_length", ["0.8"] * n)
    return cfg


def _filament_base(name, ftype, temp, extra=None):
    cfg = PrusaConfig(
        {
            "filament_settings_id": f'"{name}"',
            "filament_type": ftype,
            "temperature": temp,
            "first_layer_temperature": temp,
            "filament_colour": "#FF8000",
            "filament_multitool_ramming": "1",
            "filament_retract_length": "nil",
        }
    )
    if extra:
        for k, v in extra.items():
            cfg.set(k, v)
    return cfg


def _reference_target(n=5, settings_mode=SETTINGS_BAMBU):
    bases = [
        _filament_base("Prusament PLA @XLIS", "PLA", "215"),
        _filament_base("Prusament PETG @XLIS", "PETG", "240",
                       {"filament_retract_length": "1.1", "filament_max_volumetric_speed": "8"}),
    ] + [None] * (n - 2)
    return TargetPresets(
        mode=MODE_REFERENCE,
        printer=_xl_printer(n),
        printer_name="Original Prusa XL - 5T Input Shaper 0.4 nozzle",
        print_base=PrusaConfig({"layer_height": "0.15", "gcode_label_objects": "firmware", "wipe_tower_cone_angle": "25"}),
        print_base_name="0.20mm SPEED @XLIS 0.4",
        settings_mode=settings_mode,
        filament_bases=bases,
        filament_base_names=["Prusament PLA @XLIS", "Prusament PETG @XLIS", "", "", ""],
    )


@pytest.fixture
def ref():
    # Bambu slots 1 and 3 used, mapped to tools 1 and 2 (slot 2 unused).
    return translate_project(_bambu(), {1: 1, 3: 2}, _reference_target(), [1, 3])


def test_print_settings_renamed_and_converted(ref):
    c = ref.config
    assert c.get("layer_height") == "0.2"  # Bambu beats the print base
    assert c.get("first_layer_height") == "0.25"
    assert c.get("perimeters") == "3"
    assert c.get("top_solid_layers") == "5"
    assert c.get("bottom_solid_layers") == "4"
    assert c.get("fill_density") == "20%"
    assert c.get("fill_pattern") == "stars"
    assert c.get("top_fill_pattern") == "monotoniclines"
    assert c.get("seam_position") == "rear"
    assert c.get("external_perimeters_first") == "1"
    assert c.get("ironing") == "1" and c.get("ironing_type") == "topmost"
    assert c.get("ironing_flowrate") == "12%"
    assert c.get("support_material") == "1"
    assert c.get("support_material_auto") == "1"
    assert c.get("support_material_style") == "organic"
    assert c.get("support_material_threshold") == "35"
    assert c.get("brim_type") == "outer_only" and c.get("brim_width") == "5"
    assert c.get("complete_objects") == "1"
    assert c.get("arc_fitting") == "emit_center"
    assert c.get("top_one_perimeter_type") == "top"
    assert c.get("fuzzy_skin") == "all"
    assert c.get("external_perimeter_speed") == "180"
    # print-base keys Bambu knows nothing about survive
    assert c.get("gcode_label_objects") == "firmware"
    assert c.get("wipe_tower_cone_angle") == "25"
    assert c.get("print_settings_id") == "0.20mm Standard @BBL P1S"


def test_percent_accelerations_and_zero_means_default(ref):
    c = ref.config
    assert c.get("default_acceleration") == "8000"
    assert c.get("infill_acceleration") == "4000"  # 50 % of default
    assert c.get("perimeter_acceleration") == "0"


def test_overhang_speed_order(ref):
    c = ref.config
    assert c.get("overhang_speed_0") == "8"  # fully floating
    assert c.get("overhang_speed_1") == "10"
    assert c.get("overhang_speed_2") == "30"
    assert c.get("overhang_speed_3") == "50"


def test_extruder_indices_follow_the_mapping(ref):
    c = ref.config
    assert c.get("perimeter_extruder") == "2"  # Bambu filament 3 -> tool 2
    assert c.get("infill_extruder") == "1"  # 0 (default) is not a valid PrusaSlicer value


def test_printer_comes_from_reference_profile(ref):
    c = ref.config
    assert ref.extruders == 5
    assert c.get("printer_settings_id") == "Original Prusa XL - 5T Input Shaper 0.4 nozzle"
    assert c.get("bed_shape") == "0x0,360x0,360x360,0x360"
    assert c.get("start_gcode") == "M17 ; real XL start"
    assert c.get("single_extruder_multi_material") == "0"
    assert c.vector("nozzle_diameter") == ["0.4"] * 5


def test_filaments_per_tool(ref):
    c = ref.config
    assert c.vector("filament_settings_id") == [
        "Bambu PLA Basic", "Bambu PETG Basic", "", "", ""
    ][:2] + c.vector("filament_settings_id")[2:]
    assert c.vector("filament_colour")[:2] == ["#111111", "#333333"]
    assert c.vector("filament_type")[:2] == ["PLA", "PETG"]
    assert c.vector("temperature")[:2] == ["220", "255"]
    assert c.vector("first_layer_temperature")[:2] == ["225", "260"]
    # bed temperature follows the plate type that was selected in Bambu Studio
    assert c.vector("bed_temperature")[:2] == ["55", "70"]
    assert c.vector("first_layer_bed_temperature")[:2] == ["60", "75"]
    assert c.vector("extrusion_multiplier")[:2] == ["0.98", "0.95"]
    assert c.vector("filament_shrinkage_compensation_xy")[:2] == ["0%", "0.5%"]
    assert c.vector("filament_abrasive")[:2] == ["0", "1"]
    assert c.vector("slowdown_below_layer_time")[:2] == ["4", "6"]
    assert c.vector("min_fan_speed")[:2] == ["100", "60"]
    # 0 max volumetric speed means "no limit" in Bambu -> the base preset's value stays
    assert c.vector("filament_max_volumetric_speed")[:2] == ["21", "8"]
    # Bambu says "no retraction override" (nil) for both mapped tools, beating the PETG base
    assert c.vector("filament_retract_length") == ["nil"] * 5
    # base-only keys are carried for the tools that have a base preset
    assert c.vector("filament_multitool_ramming")[:2] == ["1", "1"]
    assert c.get("filament_vendor") == "Bambu Lab"
    assert len(c.vector("filament_settings_id")) == 5


def test_tool_colours_follow_filaments_in_reference_mode(ref):
    assert ref.config.vector("extruder_colour") == ["#111111", "#333333", "#3EC0FF", "#FF4F4F", "#FBEB7D"]


def test_keep_tool_colours_flag():
    t = _reference_target()
    t.keep_tool_colours = True
    tr = translate_project(_bambu(), {1: 1, 3: 2}, t, [1, 3])
    assert tr.config.vector("extruder_colour") == ["#FF8000", "#DB5182", "#3EC0FF", "#FF4F4F", "#FBEB7D"]


def test_purge_volumes_are_left_to_prusaslicer(ref):
    """Bambu flush volumes are purge-chute waste, not wipe tower purge: never copied."""
    c = ref.config
    m = c.vector("wiping_volumes_matrix")
    assert len(m) == 25
    assert m[0] == "0" and m[6] == "0" and m[1] == "140"
    assert c.get("wiping_volumes_use_custom_matrix") == "0"


def test_bambu_mode_sizes_purging_like_the_prime_tower():
    tr = translate_project(_bambu(), {1: 1, 3: 2}, TargetPresets(mode=MODE_BAMBU), [1, 3])
    c = tr.config
    assert c.get("multimaterial_purging") == "45"  # largest filament_prime_volume in use
    assert c.vector("filament_purge_multiplier") == ["67%", "100%"]  # slot 1: 30, slot 3: 45
    assert c.get("wiping_volumes_use_custom_matrix") == "0"
    assert c.get("single_extruder_multi_material_priming") == "0"
    assert any("purge volumes" in w for w in tr.warnings)


def test_wipe_tower_pulled_forward_to_fit_on_the_bed():
    # Bambu parked the tower 30 mm from the back edge; an MMU-style tower purging
    # 150 mm3 per change for three filaments is deeper than that.
    tr = translate_project(
        _bambu(wipe_tower_y=["226"], filament_prime_volume=["150", "150", "150"]),
        {1: 1, 2: 2, 3: 3},
        TargetPresets(mode=MODE_BAMBU),
        [1, 2, 3],
    )
    _, y, _ = tr.wipe_tower_pos
    assert 0 <= y <= 256 - 50
    assert tr.config.get("wipe_tower_y") == f"{y:g}"


def test_bambu_settings_keep_the_prusa_tool_parking_behaviour():
    # the XL cools parked tools (ooze prevention); Bambu's single-nozzle values must not switch that off
    target = _reference_target()
    target.print_base.set("ooze_prevention", "1")
    target.print_base.set("standby_temperature_delta", "-110")
    bambu = _bambu(ooze_prevention="0", standby_temperature_delta="-5")
    c = translate_project(bambu, {1: 1, 3: 2}, target, [1, 3]).config
    assert c.get("ooze_prevention") == "1"
    assert c.get("standby_temperature_delta") == "-110"
    # with the Bambu printer as-is the Bambu values are the ones that apply
    c = translate_project(bambu, {1: 1, 2: 2, 3: 3}, TargetPresets(mode=MODE_BAMBU), [1, 2, 3]).config
    assert c.get("ooze_prevention") == "0"
    assert c.get("standby_temperature_delta") == "-5"


def test_bambu_volumetric_speed_capped_at_the_prusa_filament_preset():
    target = _reference_target()
    target.filament_bases[0] = _filament_base(
        "Prusament PLA @XLIS", "PLA", "215", {"filament_max_volumetric_speed": "15"}
    )
    tr = translate_project(_bambu(), {1: 1, 3: 2}, target, [1, 3])
    # Bambu PLA Basic says 21 mm3/s, the Prusa filament preset 15: the hotend's limit wins
    assert tr.config.vector("filament_max_volumetric_speed")[:2] == ["15", "8"]
    assert any("filament_max_volumetric_speed" in w and "21 -> 15" in w for w in tr.warnings)


def test_wipe_tower_position_clamped_to_bed(ref):
    x, y, rot = ref.wipe_tower_pos
    assert 0 <= x <= 360 - 35 and y == 10 and rot == 0
    assert ref.config.get("wipe_tower") == "1"
    assert ref.config.get("wipe_tower_width") == "35"


def test_warnings_mention_lossy_conversions(ref):
    text = "\n".join(ref.warnings)
    assert "auto_brim" in text
    assert "xy_hole_compensation" in text


def test_every_key_is_known_and_enums_are_valid(ref):
    c = ref.config
    for k in c.keys():
        assert k in S.OPTIONS, k
        kind, _n, enum = S.OPTIONS[k][0], S.OPTIONS[k][1], S.OPTIONS[k][2]
        if kind == "enum" and enum:
            assert c.get(k) in enum, (k, c.get(k))
        if kind == "enums" and enum:
            assert all(v in enum for v in c.vector(k)), (k, c.get(k))
    for k in S.FILAMENT_KEYS:
        if k in c and is_vector(k):
            assert len(c.vector(k)) == 5, k
    for k in S.EXTRUDER_KEYS:
        if k in c and k != "default_filament_profile":
            assert len(c.vector(k)) == 5, k
    assert "inherits" not in c and "compatible_printers_condition" not in c


def test_bambu_printer_mode_synthesizes_printer():
    tr = translate_project(_bambu(), {1: 1, 3: 2}, TargetPresets(mode=MODE_BAMBU), [1, 3])
    c = tr.config
    assert tr.extruders == 2
    assert c.get("bed_shape") == "0x0,256x0,256x256,0x256"
    assert c.get("max_print_height") == "256"
    assert c.vector("nozzle_diameter") == ["0.4", "0.4"]
    assert c.vector("retract_length") == ["0.8", "0.8"]
    assert c.vector("retract_lift") == ["0.4", "0.4"]
    assert c.vector("machine_max_acceleration_x") == ["20000", "20000"]
    assert c.vector("machine_max_feedrate_x") == ["500", "500"]
    assert c.get("single_extruder_multi_material") == "1"
    assert c.get("printer_settings_id") == "Bambu Lab P1S 0.4 nozzle"
    assert c.vector("extruder_colour") == ["#111111", "#333333"]
    assert c.get("gcode_flavor") == "marlin"
    assert "[first_layer_temperature]" in c.get("start_gcode")
    assert c.get("machine_limits_usage") == "time_estimate_only"
    assert c.vector("filament_settings_id") == ["Bambu PLA Basic", "Bambu PETG Basic"]
    assert c.vector("filament_type") == ["PLA", "PETG"]
    assert any("No PrusaSlicer printer profile" in w for w in tr.warnings)
    x, y, _ = tr.wipe_tower_pos
    assert x == 240 - 19 and y == 10  # clamped so the 35 mm tower stays on the 256 mm bed


def test_tpu_becomes_flex_and_nil_overrides_survive():
    tr = translate_project(_bambu(), {2: 1}, TargetPresets(mode=MODE_BAMBU), [2])
    c = tr.config
    assert c.vector("filament_type") == ["FLEX"]
    assert c.vector("filament_retract_length") == ["1.5"]
    assert c.vector("temperature") == ["230"]
    assert c.vector("filament_max_volumetric_speed") == ["3.6"]


def test_validate_drops_unknown_and_repairs_bad_values():
    cfg = PrusaConfig(
        {
            "layer_height": "0.2",
            "not_a_key": "1",
            "fill_pattern": "crosszag",
            "infill_extruder": "0",
            "min_fan_speed": "250,20",
            "spiral_vase": "true",
            "temperature": "abc,200",
            "cooling_slowdown_logic": "uniform_cooling,weird",
        }
    )
    fallback = PrusaConfig({"fill_pattern": "grid", "temperature": "200,200"})
    warnings = []
    validate(cfg, fallback, warnings)
    assert "not_a_key" not in cfg
    assert cfg.get("fill_pattern") == "grid"
    assert cfg.get("infill_extruder") == "1"
    assert cfg.get("min_fan_speed") == "100,20"
    assert cfg.get("spiral_vase") == "1"
    assert cfg.get("temperature") == "200,200"
    assert "cooling_slowdown_logic" not in cfg
    assert warnings


def test_object_settings_translation():
    warnings = []
    out = translate_object_settings(
        {
            "name": "part",
            "extruder": "3",
            "wall_loops": "4",
            "sparse_infill_density": "30%",
            "sparse_infill_pattern": "gyroid",
            "enable_support": "1",
            "support_type": "normal(manual)",
            "support_style": "snug",
            "layer_height": "0.1",
            "wall_filament": "3",
            "ironing_type": "top",
            "outer_wall_speed": "120",
            "seam_position": "random",
            "travel_speed": "500",  # print-level only in PrusaSlicer: must not leak into the object
            "bambu_only_thing": "1",
        },
        {3: 2},
        warnings,
    )
    assert out["extruder"] == "2"
    assert out["perimeters"] == "4"
    assert out["fill_density"] == "30%"
    assert out["fill_pattern"] == "gyroid"
    assert out["support_material"] == "1"
    assert out["support_material_auto"] == "0"
    assert out["support_material_style"] == "snug"
    assert out["layer_height"] == "0.1"
    assert out["perimeter_extruder"] == "2"
    assert out["ironing"] == "1" and out["ironing_type"] == "top"
    assert out["external_perimeter_speed"] == "120"
    assert "travel_speed" not in out
    assert out["seam_position"] == "random"  # PrusaSlicer allows seam position per object
    assert any("bambu_only_thing" in w for w in warnings)
    for k in out:
        assert k == "extruder" or k in S.OBJECT_KEYS or k in S.REGION_KEYS


# -- keeping the user's PrusaSlicer presets (the default) ---------------------------------

def test_keep_presets_mode_writes_the_user_presets_unchanged():
    tr = translate_project(_bambu(), {1: 1, 3: 2}, _reference_target(settings_mode=SETTINGS_PRESETS), [1, 3])
    c = tr.config
    assert c.get("print_settings_id") == "0.20mm SPEED @XLIS 0.4"
    assert c.get("layer_height") == "0.15"  # the user's print preset, not Bambu's 0.2
    assert c.get("gcode_label_objects") == "firmware"
    assert "fill_density" not in c and "support_material" not in c  # nothing from Bambu's process
    assert c.vector("filament_settings_id")[:2] == ["Prusament PLA @XLIS", "Prusament PETG @XLIS"]
    assert c.vector("temperature")[:2] == ["215", "240"]  # the presets' values, not Bambu's
    assert c.vector("filament_retract_length")[:2] == ["nil", "1.1"]
    assert c.vector("filament_colour")[:2] == ["#FF8000", "#FF8000"]  # filament presets untouched
    # only the tool colours carry the Bambu colours (slot 1 -> tool 1, slot 3 -> tool 2)
    assert c.vector("extruder_colour") == ["#111111", "#333333", "#3EC0FF", "#FF4F4F", "#FBEB7D"]
    assert c.get("printer_settings_id") == "Original Prusa XL - 5T Input Shaper 0.4 nozzle"
    assert c.get("bed_shape") == "0x0,360x0,360x360,0x360"
    assert tr.extruders == 5


def test_keep_presets_mode_can_leave_tool_colours_alone():
    t = _reference_target(settings_mode=SETTINGS_PRESETS)
    t.keep_tool_colours = True
    tr = translate_project(_bambu(), {1: 1, 3: 2}, t, [1, 3])
    assert tr.config.vector("extruder_colour") == ["#FF8000", "#DB5182", "#3EC0FF", "#FF4F4F", "#FBEB7D"]


def test_keep_presets_mode_needs_presets():
    # without a print preset to keep, the Bambu settings are translated instead
    t = _reference_target(settings_mode=SETTINGS_PRESETS)
    t.print_base = None
    tr = translate_project(_bambu(), {1: 1, 3: 2}, t, [1, 3])
    assert tr.config.get("print_settings_id") == "0.20mm Standard @BBL P1S"
    assert tr.config.get("layer_height") == "0.2"
