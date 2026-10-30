"""k_end = the mean turn of a tooth coil built by ONE rule for every machine (owner 2026-10-30): rectangular end (straight bridge
across the tooth end + two 90° edgewise bends of inner radius 0.6 x wire width), size-aware overhang and leads, no CAD profile read;
never the semicircle as a silent fallback."""
from __future__ import annotations

import json
import math

import pytest

from motor_ai_sim import coil_build
from motor_ai_sim.cad import store
from motor_ai_sim.masses import cad_coil_dimensions, cad_coil_length, end_winding_factor


class MotorParams:
    def __init__(self, stack_length_m: float, num_slots: int = 12, slot_width_m: float = 0.00282,
                 r_stator_in: float = 0.012, slot_height_m: float = 0.0061):
        self.stack_length = float(stack_length_m)
        self.num_slots = int(num_slots)
        self.slot_width_m = float(slot_width_m)
        self.r_stator_in = float(r_stator_in)
        self.slot_height_m = float(slot_height_m)


#: a 40 mm 12-slot cross-section (no coil keys: the coil comes from the rule)
G40 = {"tooth_width": 3.3, "wire_width": 2.5, "wire_height": 0.6, "num_wires_per_slot": 7, "insulation_thickness": 0.07,
       "wire_spacing_x": 0.1, "wire_spacing_y": 0.1}

#: a 400 mm tooth-coil machine of the size the owner flagged: tooth 15 mm, flat wire 7.5 x 1.0 on edge, 14 turns, 90 mm stack -
#: generic numbers, not a die
G400 = {"stator_diameter": 400.0, "core_thickness": 14.0, "slot_height": 40.0, "num_seg": 8, "num_slots_per_segment": 6,
        "tooth_width": 15.0, "wire_width": 7.5, "wire_height": 1.0, "num_wires_per_slot": 14, "insulation_thickness": 0.07,
        "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "motor_length": 90.0}


@pytest.fixture(autouse=True)
def _fresh_cache():
    coil_build.clear_cache()
    yield
    coil_build.clear_cache()


def _hand_coil_mm(L, tw, ww, wh, n, ins, sx, sy, rb, extra, lead):
    """The rectangular tooth coil of cad/motor_templates/segmented_flatwire/coil.py, by hand."""
    x1 = tw / 2 + ins + sx / 2                      # inner side of a straight leg
    bridge = 2 * (x1 - rb)                          # straight bridge between the two bend axes
    plus = bridge + math.pi * (rb + ww / 2)         # two 90° edgewise bends, centre line rb + ww/2
    minus = math.hypot(plus, wh + sy)               # -z transition, one layer pitch lower
    return 2 * n * (L + extra) + n * plus + (n - 1) * minus + 2 * lead


def test_coil_length_equals_a_hand_computed_rectangular_coil():
    p = MotorParams(0.020)
    coil = {"wire_beng_r": 1.72, "coil_straight_extra": 1.0, "coil_lead_total": 9.1}
    d = cad_coil_length(p, G40, coil)
    hand = _hand_coil_mm(20.0, 3.3, 2.5, 0.6, 7, 0.07, 0.1, 0.1, 1.72, 1.0, 9.1)
    assert d["total_coil_length_mm"] == pytest.approx(hand, rel=1e-12)
    # L20: x1 = 1.77, bridge 0.1, +z 0.1 + π·2.97 = 9.4305, -z √(9.4305² + 0.7²) = 9.4565
    assert d["end_turn_plus_mm"] == pytest.approx(0.1 + math.pi * 2.97, rel=1e-12)
    assert hand == pytest.approx(2 * 7 * 21 + 7 * 9.43053 + 6 * 9.45648 + 18.2, abs=1e-3)
    assert d["mean_turn_length_mm"] == pytest.approx(hand / 7, rel=1e-12)


def test_the_owner_example_a_rectangular_end_at_0_6_w():
    """7.5 mm strip -> inner bend 4.5 mm (owner); the end is the straight bridge 2·(x1 − r) plus two 90° bends, not a semicircle."""
    c = coil_build.coil_parameters(G400, die="any 400", config="c", cad_overrides={})
    assert c["wire_beng_r"] == pytest.approx(4.5)
    assert c["kind"] == "rule" and c["profile"] is None
    assert c["source"].startswith("coil rule: edgewise bend R = 0.6 x wire width = 4.50 mm, rectangular end")
    x1 = 15.0 / 2 + 0.07 + 0.05
    d = cad_coil_length(MotorParams(0.090), G400, c)
    assert d["bridge_mm"] == pytest.approx(2 * (x1 - 4.5))
    assert d["end_turn_plus_mm"] == pytest.approx(2 * (x1 - 4.5) + math.pi * (4.5 + 7.5 / 2))
    # the same machine with the semicircle end (bend at x1) is longer: the rule is not a semicircle
    semi = cad_coil_length(MotorParams(0.090), G400, dict(c, wire_beng_r=x1))
    assert d["mean_turn_length_mm"] < semi["mean_turn_length_mm"]


def test_the_rule_scales_with_the_machine():
    """Overhang and leads come from the machine's size: on a 400 mm stator the straight legs stand 2 mm proud per end (0.5 % of D),
    a lead is half a slot pitch at the slot's mid radius plus a two-wire-width stub; small machines keep legs = stack + 1 mm."""
    e = coil_build.coil_rule(G400)
    assert e["coil_straight_extra"] == pytest.approx(2 * 0.005 * 400.0)
    r_mid = 400.0 / 2 - 14.0 - 40.0 / 2
    assert e["coil_lead_total"] == pytest.approx(math.pi * r_mid / 48 + 2 * 7.5)
    assert coil_build.coil_rule(dict(G400, stator_diameter=60.0))["coil_straight_extra"] == pytest.approx(1.0)
    # the CIANO14 40 template's constants on the same 400 mm machine, kept only as a comparison
    t = coil_build.template_coil_parameters(G400)
    assert t["coil_straight_extra"] == pytest.approx(1.0) and t["coil_lead_total"] == pytest.approx(9.1)
    assert t["kind"] == "template"


def test_bend_radius_edgewise_flatwise_and_the_semicircle_cap():
    narrow = coil_build.coil_rule(dict(G400, wire_width=3.0))
    assert narrow["wire_beng_r"] == pytest.approx(0.6 * 3.0) and "edgewise" in narrow["source"]
    # flatwise (wire_width <= wire_height, a round wire w = h = d among them): the same 0.6·w, named flatwise
    rnd = coil_build.coil_rule(dict(G400, wire_width=2.0, wire_height=2.0))
    assert rnd["wire_beng_r"] == pytest.approx(1.2) and "flatwise" in rnd["source"]
    # a strip wider than the leg's offset cannot bend at 0.6·w inside it: the semicircle end, said so
    wide = coil_build.coil_rule(dict(G400, wire_width=14.0))
    assert wide["wire_beng_r"] == pytest.approx(15.0 / 2 + 0.07 + 0.05)
    assert "semicircle end" in wide["source"]


def test_no_cad_profile_is_read(monkeypatch, tmp_path):
    """A die WITH its own CAD reference profile gets the same rule as every other machine: the k_end path never reads it."""
    rows = [{"name": "wire_beng_r", "freecad_expression": "1.11 mm", "unit": "mm"},
            {"name": "coil_straight_extra", "freecad_expression": "9 mm", "unit": "mm"},
            {"name": "coil_lead_total", "freecad_expression": "99 mm", "unit": "mm"}]
    ref = tmp_path / "ref"
    ref.mkdir()
    for name in ("GEN_X.json", "CIANO14_40_12.json"):
        (ref / name).write_text(json.dumps({"profile": {}, "user_parameters": rows}), encoding="utf-8")
    monkeypatch.setattr(store, "REFERENCE_DIR", str(ref))
    monkeypatch.setattr(store, "BASE_PROFILE", str(ref / "CIANO14_40_12.json"))

    def _boom(*_a, **_k):
        raise AssertionError("k_end read a CAD reference profile")
    monkeypatch.setattr(store, "reference_for", _boom)
    geo = dict(G40, motor_length=20.0)
    for die in ("GEN X", "CIANO14 40 new", "OTHER"):
        c = coil_build.coil_parameters(geo, die=die, config="c", cad_overrides={})
        assert c == dict(coil_build.coil_rule(geo), typed=[], die=die, config="c")


@pytest.mark.parametrize("L_mm, target_coil_len_mm", [(12.0, 315.916), (20.0, 427.916)])
def test_the_rule_lands_near_a_drawn_coil(L_mm, target_coil_len_mm):
    """On the 40 mm machine whose coil was drawn in Fusion (L12 / L20 conductor lengths), the rule's coil is within 3 %, and
    k_end · 2L · N is that coil."""
    p = MotorParams(L_mm * 1e-3)
    geo = dict(G40, motor_length=L_mm)
    dims = cad_coil_dimensions(p, geo, die="d", config="c", cad_overrides={})
    assert abs(dims["total_coil_length_mm"] - target_coil_len_mm) / target_coil_len_mm < 0.03
    k = end_winding_factor(p, geo, die="d", config="c", cad_overrides={})
    assert k == pytest.approx(dims["k_end_cad"], rel=1e-12)
    assert k * 2 * L_mm * 7 == pytest.approx(dims["total_coil_length_mm"], rel=1e-9)
    assert dims["wire_beng_r_mm"] == pytest.approx(0.6 * 2.5)
    assert dims["coil_straight_extra_mm"] == pytest.approx(1.0)
    assert dims["coil_kind"] == "rule"


def test_semicircle_is_not_the_fallback():
    p = MotorParams(0.012)
    k_auto = end_winding_factor(p, G40, die="any die", config="c", cad_overrides={})
    k_semi = end_winding_factor(p, G40, method="semicircle")
    assert k_semi == pytest.approx((math.pi * 5.8 / 2 + 12) / 12, rel=1e-9)        # 3.3 + 2.5 = 5.8 mm span
    assert k_auto != pytest.approx(k_semi, rel=1e-3)
    assert k_auto == pytest.approx(end_winding_factor(p, G40, method="cad_coil", die="any die", config="c",
                                                      cad_overrides={}), rel=1e-12)
    with pytest.raises(ValueError):
        end_winding_factor(p, G40, method="guess")


def test_the_coil_follows_every_geometry_edit():
    kw = dict(die="d", config="c", cad_overrides={})
    k0 = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20), **kw)
    k_tooth = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20, tooth_width=4.0), **kw)
    k_wire = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20, wire_width=3.0), **kw)
    k_long = end_winding_factor(MotorParams(0.040), dict(G40, motor_length=40), **kw)
    assert k_tooth > k0 and k_wire > k0 and k_long < k0


def test_explicit_die_keys_override_the_rule():
    p = MotorParams(0.035, num_slots=24)
    geo = dict(G40, tooth_width=9.8, wire_width=5.0, num_wires_per_slot=14, insulation_thickness=0.15, wire_spacing_y=0.13,
               motor_length=35, wire_beng_r=2.5, coil_lead_total=20.0)
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={})
    assert c["wire_beng_r"] == 2.5 and c["coil_lead_total"] == 20.0
    assert c["coil_straight_extra"] == pytest.approx(1.0)               # not typed: the rule (no stator diameter -> 40 mm)
    assert c["typed"] == ["wire_beng_r", "coil_lead_total"]
    assert c["source"].startswith("geometry keys wire_beng_r, coil_lead_total typed per die; the rest from the coil rule")
    # a 150 mm 24-slot coil of a bill of materials: 1604.58 mm
    dims = cad_coil_dimensions(p, dict(geo, coil_straight_extra=1.0), die="d", config="c", cad_overrides={})
    assert abs(dims["total_coil_length_mm"] - 1604.58) / 1604.58 < 0.03


def test_cad_overrides_of_the_leads_reach_k_end():
    """Geometry > Full edits the lead extension / stub length: the coil's lead changes, so does k_end."""
    p = MotorParams(0.020)
    geo = dict(G40, motor_length=20)
    k0 = end_winding_factor(p, geo, die="d", config="c", cad_overrides={})
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={"coil_lead_len": 15.0})
    assert c["coil_lead_total"] - coil_build.coil_rule(geo)["coil_lead_total"] == pytest.approx(15.0 - 5.0)
    k1 = end_winding_factor(p, geo, die="d", config="c", cad_overrides={"coil_lead_len": 15.0})
    assert k1 - k0 == pytest.approx(2 * 10.0 / 7 / 40.0, rel=1e-9)     # two leads 10 mm longer over 7 turns of 2·20 mm


def test_the_template_comparison_sits_beside_it():
    p = MotorParams(0.090, num_slots=48, r_stator_in=0.146, slot_height_m=0.040)
    d = cad_coil_dimensions(p, G400, die="any 400", config="c", cad_overrides={})
    assert d["coil_kind"] == "rule"
    assert d["k_end_template"] > 1.0 and d["k_end_semicircle"] > 1.0


def test_edge_cases():
    assert end_winding_factor(MotorParams(0.0)) == 1.0
    assert end_winding_factor(MotorParams(-0.01)) == 1.0
    # no geometry at all still builds a coil (tooth derived from the slot pitch, template wire) - never a silent semicircle
    k = end_winding_factor(MotorParams(0.02), None, die="d", config="c", cad_overrides={})
    assert k > 1.0 and k != pytest.approx(end_winding_factor(MotorParams(0.02), None, method="semicircle"), rel=1e-3)
