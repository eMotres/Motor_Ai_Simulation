"""One catalog header per stamped sheet (owner 2026-11-17): die_sheet.sheet_id compares the lamination outline only."""
from motor_ai_sim import die_sheet as DS
from motor_ai_sim import motor_designation as MD

# the 80 mm sheet of the shared die (numbers of the 2026-11-17 identity table)
SHEET_80 = {"stator_diameter": 80.0, "slot_height": 6.9, "core_thickness": 2.3000000000000003, "num_seg": 4,
            "num_slots_per_segment": 6, "num_poles_per_segment": 7, "air_gap": 0.5, "tooth_width": 4.7,
            "tooth2_width": 1.903353557, "cut_width": 1.4, "slot_hs": 0.13, "magnet_height": 7.0, "rotor_house_height": 0.8,
            "shaft_height": 1.9, "magnet_up_gap": 0.2, "rotor_hole": 0.859170503, "magnet_down_height": 0.5,
            "stator_fillet_r": 1.1, "stator_fillet_r1": 0.1, "rotor_fill_r": 0.2}
MOTOR_80 = {"motor_length": 12.0, "wire_width": 3.0, "wire_height": 0.3, "num_wires_per_slot": 17, "wire_parallel": 1,
            "insulation_thickness": 0.07, "magnet_fill_down": 0.918340723, "magnet_fill_up": 0.386390525,
            "magnet_fill_radius": 0.4, "magnet_lamination": 0, "sleeve_thickness": 0,
            "stator_outer_radius": 40.0, "num_slots": 24, "num_poles": 28, "slot_width": 3.34}


def test_the_motor_keys_do_not_split_one_sheet():
    a = DS.sheet_id({**SHEET_80, **MOTOR_80})
    other_motor = {**MOTOR_80, "motor_length": 20.0, "num_wires_per_slot": 9, "magnet_fill_up": 0.36, "magnet_fill_radius": 0.3,
                   "magnet_lamination": 5, "sleeve_thickness": 0.3, "stator_outer_radius": 40.0000001}
    assert a and a == DS.sheet_id({**SHEET_80, **other_motor})
    # a retired key or the builder's default written out is the same sheet
    assert a == DS.sheet_id({**SHEET_80, "shaft_diameter": 5, "magnet_flat": 0, "magnet_pocket_clearance": 0.0, "wire_bore_margin": 0.04})


def test_a_sheet_parameter_splits_even_by_a_thousandth_of_a_millimetre():
    a = DS.sheet_id(SHEET_80)
    r2 = {**SHEET_80, "core_thickness": 2.5, "tooth_width": 5.0, "tooth2_width": 2.0, "cut_width": 1.5, "rotor_hole": 0.73}
    assert DS.sheet_id(r2) != a                                            # the R2 record of 2026-11-17: another sheet
    assert DS.sheet_id({**SHEET_80, "tooth2_width": 1.902320576}) != a     # 0.001 mm: still two until the owner says one
    assert DS.sheet_id({**SHEET_80, "core_thickness": 2.3}) == a          # float noise is not a difference
    assert DS.sheet_id({**SHEET_80, "magnet_flat": 1}) != a
    assert DS.sheet_id({"tooth_width": 4.7}) is None                      # no diameter: nothing to compare


def test_the_catalog_row_carries_the_sheet_of_its_effective_geometry():
    die = {"name": "D", "geometry": {**SHEET_80, **MOTOR_80}}
    plain = MD.catalog_fields("D", die, {"name": "L12", "geometry_overrides": {"motor_length": 20}})
    tip = MD.catalog_fields("D", die, {"name": "L12-x", "geometry_overrides": {"tooth2_width": 2.003353557}})
    assert plain["sheet_id"] == DS.sheet_id(SHEET_80)
    assert tip["sheet_id"] == DS.sheet_id({**SHEET_80, "tooth2_width": 2.003353557}) != plain["sheet_id"]
