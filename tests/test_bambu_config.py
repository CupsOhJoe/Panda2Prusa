"""Bambu project_settings.config access, including variant-expanded vectors."""

import json

from panda2prusa.bambu_config import BambuConfig


def _variant_project():
    # A P2S-style project: one extruder with two nozzle "variants", two filaments.
    return BambuConfig(
        {
            "nozzle_diameter": ["0.4"],
            "extruder_type": ["Direct Drive"],
            "nozzle_volume_type": ["Standard"],
            "print_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"],
            "printer_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"],
            "filament_extruder_variant": [
                "Direct Drive Standard", "Direct Drive High Flow",
                "Direct Drive Standard", "Direct Drive High Flow",
            ],
            "filament_self_index": ["1", "1", "2", "2"],
            "filament_map": ["1", "1"],
            "filament_colour": ["#112233", "#445566"],
            "filament_type": ["PLA", "PETG"],
            "nozzle_temperature": ["220", "225", "250", "255"],
            "filament_max_volumetric_speed": ["21", "30", "12", "20"],
            "outer_wall_speed": ["200", "500"],
            "layer_height": "0.2",
            "retraction_length": ["0.8", "1.2"],
            "machine_max_acceleration_x": ["20000", "15000", "9000", "8000"],
            "machine_max_jerk_x": ["9", "9"],
            "curr_bed_type": "Textured PEI Plate",
            "single_extruder_multi_material": "1",
            "filament_settings_id": ["A", "B"],
            "filament_notes": "",
        }
    )


def test_counts_and_variants():
    b = _variant_project()
    assert b.num_filaments == 2
    assert b.extruder_count == 1
    assert b.active_variants == ["Direct Drive Standard"]


def test_scalar_picks_active_variant():
    b = _variant_project()
    assert b.scalar("outer_wall_speed") == "200"
    assert b.scalar("layer_height") == "0.2"
    assert b.scalar("missing") is None


def test_per_filament_collapses_self_index_by_variant():
    b = _variant_project()
    assert b.per_filament("nozzle_temperature") == ["220", "250"]
    assert b.per_filament("filament_max_volumetric_speed") == ["21", "12"]
    assert b.per_filament("filament_colour") == ["#112233", "#445566"]
    assert b.per_filament("filament_notes") == ["", ""]  # scalar broadcast
    assert b.per_filament("nope") is None


def test_per_filament_high_flow_variant_when_active():
    raw = json.loads(json.dumps(_variant_project().raw))
    raw["nozzle_volume_type"] = ["High Flow"]
    b = BambuConfig(raw)
    assert b.active_variants == ["Direct Drive High Flow"]
    assert b.per_filament("nozzle_temperature") == ["225", "255"]
    assert b.scalar("outer_wall_speed") == "500"
    assert b.per_extruder("retraction_length") == ["1.2"]


def test_per_extruder_and_machine_limits():
    b = _variant_project()
    assert b.per_extruder("retraction_length") == ["0.8"]
    assert b.machine_limit("machine_max_acceleration_x") == ("20000", "15000")
    assert b.machine_limit("machine_max_jerk_x") == ("9", "9")
    assert b.machine_limit("missing") is None


def test_bed_type_and_flags():
    b = _variant_project()
    assert b.bed_type == "Textured PEI Plate"
    assert b.bed_temp_keys() == ("textured_plate_temp", "textured_plate_temp_initial_layer")
    assert b.single_extruder_multi_material is True
    assert b.filament_ids == ["A", "B"]


def test_legacy_project_without_variants():
    b = BambuConfig(
        {
            "nozzle_diameter": ["0.4"],
            "filament_colour": ["#000000", "#FFFFFF", "#FF0000"],
            "nozzle_temperature": ["200", "210", "220"],
            "outer_wall_speed": ["150"],
            "cool_plate_temp": ["35"],  # too short: padded with the last value
            "curr_bed_type": "Cool Plate",
        }
    )
    assert b.num_filaments == 3
    assert b.per_filament("nozzle_temperature") == ["200", "210", "220"]
    assert b.per_filament("cool_plate_temp") == ["35", "35", "35"]
    assert b.scalar("outer_wall_speed") == "150"
    assert b.bed_temp_keys() == ("cool_plate_temp", "cool_plate_temp_initial_layer")


def test_from_bytes_tolerates_garbage():
    assert not BambuConfig.from_bytes(b"not json")
    assert BambuConfig.from_bytes(b'{"layer_height": "0.1"}').scalar("layer_height") == "0.1"
