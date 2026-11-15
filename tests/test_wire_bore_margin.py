"""`wire_bore_margin` (2026-11-15): the bore-side margin of the wire stack.

Absent / None / '' = not set = the rule every machine was built with (liner on both radial sides; bit-identical
default).  A number is the CLEARANCE between the stack's bore-side edge and the bore circle: the height bound becomes
(slot - liner - clearance + dy/2) / rows - dy and the lowest row keeps `clearance` above the bore.  The CIANO10 12_40
(Fusion v24 cut) stack stands 0.042 mm from the bore (the die states 0.04; the validator keeps a 1e-6 mm safety margin, so an
exact fit at 0.042 would be refused).
"""
import pytest

from motor_ai_sim import winding
from motor_ai_sim import geometry_constraints as gc
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.geometry_validation import validate_geometry

# the Fusion v24 numbers on the 30 mm die's remaining keys (the stack and the slot are what matters here)
G12 = dict(stator_diameter=12, motor_length=40, air_gap=0.1, sleeve_thickness=0, core_thickness=0.7, slot_height=1.842,
           tooth_width=1.0, tooth2_width=0.6, cut_width=1.2, stator_fillet_r=0.5, stator_fillet_r1=0.05,
           insulation_thickness=0.125, slot_hs=0.11, magnet_height=2.0, magnet_fill_up=0.6, magnet_fill_down=0.85,
           magnet_fill_radius=0.2, magnet_up_gap=0.1, magnet_down_height=0.0, magnet_lamination=0, rotor_hole=0.7,
           rotor_fill_r=0.2, rotor_house_height=0.3, shaft_height=0.5, num_seg=2, num_slots_per_segment=6,
           num_poles_per_segment=5, num_slots=12, num_poles=10, wire_spacing_y=0.05, wire_width=0.2, wire_height=0.8,
           num_wires_per_slot=2, wire_split=3, wire_spacing_x=0.03)


def test_reader_unset_is_none():
    assert winding.wire_bore_margin_mm({"insulation_thickness": 0.15}) is None
    for raw in (None, "", "x", float("nan"), -0.1, True):
        assert winding.wire_bore_margin_mm({"insulation_thickness": 0.15, "wire_bore_margin": raw}) is None
    assert winding.wire_bore_margin_mm({"wire_bore_margin": 0.042}) == 0.042
    assert winding.wire_bore_margin_mm({"wire_bore_margin": 0}) == 0.0
    assert winding.wire_bore_margin_mm({}) is None
    assert winding.bore_floor_offset_mm(0.125, None) == 0.125            # unchanged placement floor


def test_height_bound_default_identical_and_margin_used():
    g = {"slot_height": 6.0, "insulation_thickness": 0.06, "wire_spacing_y": 0.1, "num_wires_per_slot": 7}
    assert gc._wire_height_max(g) == (6.0 - 0.12) / 7 - 0.1
    assert gc._wire_height_max({**g, "wire_bore_margin": None}) == (6.0 - 0.12) / 7 - 0.1
    # a stated clearance equal to liner + dy/2 is the old rule; a smaller one gives more room
    assert gc._wire_height_max({**g, "wire_bore_margin": 0.06 + 0.05}) == pytest.approx((6.0 - 0.12) / 7 - 0.1)
    assert gc._wire_height_max({**g, "wire_bore_margin": 0.0}) == pytest.approx((6.0 - 0.06 + 0.05) / 7 - 0.1)


def _polys(**ov):
    m = CadQueryMotor()
    m.set_parameters({**G12, **ov})
    return m.get_2d_polygons()


def test_default_builder_clamps_exactly_as_before():
    """Without the key the 12_40 stack is clamped to 0.746 mm (the old rule) and the validator refuses it."""
    res = validate_geometry({**G12})
    assert not res.ok and "winding_height_does_not_fit" in [v.code for v in res.errors]
    P = _polys()
    assert max(c.bounds[3] - c.bounds[1] for c in P["coils"]) == pytest.approx(0.746, abs=1e-6)


def test_margin_lets_the_fusion_stack_stand_unclipped():
    res = validate_geometry({**G12, "wire_bore_margin": 0.04})
    assert res.ok, [v.code for v in res.violations]
    assert "winding_clipped_by_slot" not in [v.code for v in res.violations]
    P = _polys(wire_bore_margin=0.04)
    assert P["n_wires_fit"] == 2 and not P["coils_overflow"]
    assert len(P["coils"]) == 6 * 12
    assert all(c.area == pytest.approx(0.16, abs=1e-6) for c in P["coils"])          # 0.2 x 0.8, no clamp
    ys = {round(c.bounds[1], 3) for c in P["coils"]}
    assert 3.5 in ys and 4.35 in ys                           # the two rows stand where the Fusion cut has them (r 3.50 and 4.35)
    assert 3.5 >= 3.458 + 0.04                                # = the stated clearance above the bore circle (3.458)


def test_stack_closer_than_the_margin_still_drops_the_row():
    """The margin is a real clearance: asking for a 0.2 mm clearance with two 0.8 mm rows leaves no room."""
    res = validate_geometry({**G12, "wire_bore_margin": 0.2})
    assert "winding_height_does_not_fit" in [v.code for v in res.errors]


def test_negative_margin_is_refused_by_the_value_validator():
    from motor_ai_sim.geometry_validation import validate_parameter_values
    bad = validate_parameter_values({**G12, "wire_bore_margin": -0.01})
    assert any(b.get("field") == "wire_bore_margin" for b in bad)
    assert not any(b.get("field") == "wire_bore_margin" for b in validate_parameter_values({**G12, "wire_bore_margin": 0.04}))
