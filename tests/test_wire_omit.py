"""`wire_omit` (2026-11-15): EMPTY positions of the wire grid (rows x strips).

The CIANO10 12_40 coil side is 2 radial rows x 3 columns of on-edge strips with FIVE series turns: one grid position is
empty.  The key is ONE integer (bit row*8+col; row 0 = under the yoke, column 0 = next to the tooth) so it travels through
geometry signatures, forms and CSV.  Absent / 0 = the full grid, bit for bit.
"""
import pytest

from motor_ai_sim import winding as W
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.geometry_validation import validate_geometry, validate_parameter_values

# the Fusion v24 numbers on the 30 mm die's remaining keys, wire 0.7 high so the stack fits without wire_bore_margin
G = dict(stator_diameter=12, motor_length=40, air_gap=0.1, sleeve_thickness=0, core_thickness=0.7, slot_height=1.842,
         tooth_width=1.0, tooth2_width=0.6, cut_width=1.2, stator_fillet_r=0.5, stator_fillet_r1=0.05,
         insulation_thickness=0.125, slot_hs=0.11, magnet_height=2.0, magnet_fill_up=0.6, magnet_fill_down=0.85,
         magnet_fill_radius=0.2, magnet_up_gap=0.1, magnet_down_height=0.0, magnet_lamination=0, rotor_hole=0.7,
         rotor_fill_r=0.2, rotor_house_height=0.3, shaft_height=0.5, num_seg=2, num_slots_per_segment=6,
         num_poles_per_segment=5, num_slots=12, num_poles=10, wire_spacing_y=0.05, wire_width=0.2, wire_height=0.7,
         num_wires_per_slot=2, wire_split=3, wire_spacing_x=0.03)
ONE = W.wire_omit_mask([(1, 0)])


def test_mask_roundtrip_and_example():
    assert ONE == 256
    assert W.wire_omit_positions({"wire_omit": ONE}) == [(1, 0)]
    assert W.wire_omit_positions({"wire_omit": W.wire_omit_mask([(0, 2), (1, 1)])}) == [(0, 2), (1, 1)]
    assert W.wire_omit_positions({}) == [] and W.wire_omit_positions({"wire_omit": None}) == []
    assert W.wire_omit_positions({"wire_omit": 0}) == [] and W.wire_omit_positions({"wire_omit": ""}) == []
    for bad in (-1, 1.5, "x", 2 ** 48):
        with pytest.raises(ValueError):
            W.wire_omit_positions({"wire_omit": bad})
    with pytest.raises(ValueError):
        W.wire_omit_mask([(6, 0)])


def test_counts_default_identical_and_minus_empty_positions():
    g = {"num_wires_per_slot": 2, "wire_split": 3}
    assert W.turns_per_coil(g) == 6 and W.conductors_per_slot(g) == 6
    assert W.turns_per_coil({**g, "wire_omit": ONE}) == 5 and W.conductors_per_slot({**g, "wire_omit": ONE}) == 5
    assert W.turns_per_coil({**g, "wire_omit": None}) == 6 and W.turns_per_coil({**g, "wire_omit": 0}) == 6


def test_refusals():
    g = {"num_wires_per_slot": 2, "wire_split": 3}
    assert W.wire_omit_error({**g, "wire_omit": ONE}) is None
    assert "outside" in W.wire_omit_error({**g, "wire_omit": W.wire_omit_mask([(2, 0)])})      # no third row
    assert "outside" in W.wire_omit_error({**g, "wire_omit": W.wire_omit_mask([(0, 3)])})      # no fourth column
    assert "wire_parallel" in W.wire_omit_error({**g, "wire_omit": ONE, "wire_parallel": 2})
    assert "whole wire row" in W.wire_omit_error({**g, "wire_omit": W.wire_omit_mask([(1, 0), (1, 1), (1, 2)])})
    for bad in ({**g, "wire_omit": W.wire_omit_mask([(2, 0)])}, {**g, "wire_omit": ONE, "wire_parallel": 2}):
        with pytest.raises(ValueError):
            W.turns_per_coil(bad)
        with pytest.raises(ValueError):
            W.conductors_per_slot(bad)


def test_validator_names_the_field():
    res = validate_parameter_values({**G, "wire_omit": W.wire_omit_mask([(2, 0)])})
    assert any(b.get("field") == "wire_omit" for b in res)
    assert not any(b.get("field") == "wire_omit" for b in validate_parameter_values({**G, "wire_omit": ONE}))
    assert not any(b.get("field") == "wire_omit" for b in validate_parameter_values({**G}))
    assert any(b.get("field") == "wire_omit" for b in validate_parameter_values({**G, "wire_omit": -3}))


def _polys(**ov):
    m = CadQueryMotor()
    m.set_parameters({**G, **ov})
    return m.get_2d_polygons()


def test_full_grid_is_untouched_and_one_position_leaves_five_strips():
    P6 = _polys()
    assert len(P6["coils"]) == 6 * 12
    P5 = _polys(wire_omit=ONE)
    assert len(P5["coils"]) == 5 * 12
    # slot unit 0 at angle 0: +x side counts from the tooth (x = 0.637 ...), -x side mirrored
    def cells(P):
        return sorted((round(c.bounds[0], 3), round(c.bounds[1], 3)) for c in P["coils"]
                      if abs(c.centroid.x) < 1.6 and 3.0 < c.centroid.y < 5.4)
    c6, c5 = cells(P6), cells(P5)
    assert len(c6) == 12 and len(c5) == 10                          # the unit's +x and -x sides: 6 / 5 strips each
    missing = [c for c in c6 if c not in c5]
    # row 1 is the lower one (smaller y), column 0 the strip next to the tooth: the same cell on BOTH sides of the unit
    assert len(missing) == 2
    assert all(abs(y - 3.608) < 0.02 or y < 4.0 for _x, y in missing)
    assert abs(min(abs(x) for x, _y in missing) - 0.64) < 0.03
    # enamel fills the empty position: the copper area is 5/6 of the full grid's
    a6 = sum(c.area for c in P6["coils"]); a5 = sum(c.area for c in P5["coils"])
    assert a5 == pytest.approx(a6 * 5 / 6, rel=1e-9)


def test_invalid_mask_stops_the_builder_by_name():
    with pytest.raises(ValueError):
        _polys(wire_omit=W.wire_omit_mask([(2, 0)]))


def test_validator_accepts_the_five_turn_stack():
    res = validate_geometry({**G, "wire_omit": ONE})
    assert res.ok, [v.code for v in res.violations]


def test_copper_loss_and_resistance_follow_five_turns():
    from motor_ai_sim.simulation.field_ops import copper_loss_W

    class P:                                     # the few fields copper_loss_W reads
        stack_length = 40e-3
        num_slots = 12
    P6, k6, R6 = copper_loss_W(P, {**G}, 10.0, 1, coil_temp_c=20.0, end_winding_factor=1.2)
    P5, k5, R5 = copper_loss_W(P, {**G, "wire_omit": ONE}, 10.0, 1, coil_temp_c=20.0, end_winding_factor=1.2)
    assert R5 == pytest.approx(R6 * 5 / 6, rel=1e-12) and P5 == pytest.approx(P6 * 5 / 6, rel=1e-12)


def test_thermal_template_leaves_the_position_as_coating():
    from motor_ai_sim.simulation import iron_template as T
    p = {k: float(v) if not isinstance(v, str) else v for k, v in G.items()}
    p.update(stator_outer_radius=6.0, stator_inner_radius=3.458)

    def coil_area(extra):
        V, Tr, Gt = T.stator_half_mesh({**p, **extra})
        tri = V[Tr]
        a = 0.5 * abs((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1])
                      - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
        return float(a[Gt == T.TAG_COIL].sum())
    a6, a5 = coil_area({}), coil_area({"wire_omit": ONE})
    assert a5 == pytest.approx(a6 * 5 / 6, rel=1e-6)


def test_schema_knows_the_key_and_get_does_not_fill_it():
    from motor_ai_sim.routes._validation import SCHEMA_FALLBACK, known_geometry_keys
    assert SCHEMA_FALLBACK["wire_omit"]["unset_ok"] is True and "wire_omit" in known_geometry_keys()


def test_absent_means_zero_and_key_is_free_with_wire_split():
    from motor_ai_sim.routes import family
    assert family._ABSENT_MEANS["wire_omit"] == 0 and "wire_omit" in family.FREE_GEO_KEYS
    assert "wire_omit" not in family.EDITABLE_UNDER_DIE_LOCK          # locked with wire_split


def test_the_fusion_12_40_coil_side_with_the_bore_margin():
    """The real thing (stacked on `wire_bore_margin`): 2 rows x 3 on-edge 0.2 x 0.8 strips, five turns, validates."""
    g = {**G, "wire_height": 0.8, "wire_bore_margin": 0.04, "wire_omit": ONE}
    res = validate_geometry(g)
    assert res.ok, [v.code for v in res.violations]
    assert W.turns_per_coil(g) == 5
    P = _polys(wire_height=0.8, wire_bore_margin=0.04, wire_omit=ONE)
    assert len(P["coils"]) == 5 * 12 and all(abs(c.area - 0.16) < 1e-6 for c in P["coils"])
