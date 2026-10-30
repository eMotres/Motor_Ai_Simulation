"""k_end = the mean turn of the coil the CAD build draws (owner 2026-10-30), never the semicircle as a silent fallback."""
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


#: CIANO14 40 cross-section (no coil keys: the coil comes from the CAD build's rules)
G40 = {"tooth_width": 3.3, "wire_width": 2.5, "wire_height": 0.6, "num_wires_per_slot": 7, "insulation_thickness": 0.07,
       "wire_spacing_x": 0.1, "wire_spacing_y": 0.1}


@pytest.fixture(autouse=True)
def _fresh_cache():
    coil_build.clear_cache()
    yield
    coil_build.clear_cache()


@pytest.fixture
def no_reference(monkeypatch, tmp_path):
    """A host without the private CAD reference files: the template's base rules must answer, and say so."""
    monkeypatch.setattr(store, "REFERENCE_DIR", str(tmp_path / "no_reference"))
    monkeypatch.setattr(store, "BASE_PROFILE", str(tmp_path / "no_reference" / "CIANO14_40_12.json"))


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


@pytest.mark.parametrize("config, L_mm, target_coil_len_mm", [("L12", 12.0, 315.916), ("L20", 20.0, 427.916)])
def test_built_coil_reproduces_fusion_coil_lengths(no_reference, config, L_mm, target_coil_len_mm):
    """No coil keys in the geometry: the built coil (base rules: bend = x1 - 0.05, legs L + 1, leads 4.1 + 5) lands on the Fusion
    conductor length of CIANO14 40 within 3 %, and k_end · 2L · N is that length."""
    p = MotorParams(L_mm * 1e-3)
    geo = dict(G40, motor_length=L_mm)
    dims = cad_coil_dimensions(p, geo, die="CIANO14 40 new", config=config, cad_overrides={})
    assert abs(dims["total_coil_length_mm"] - target_coil_len_mm) / target_coil_len_mm < 0.03
    k = end_winding_factor(p, geo, die="CIANO14 40 new", config=config, cad_overrides={})
    assert k == pytest.approx(dims["k_end_cad"], rel=1e-12)
    assert k * 2 * L_mm * 7 == pytest.approx(dims["total_coil_length_mm"], rel=1e-9)
    assert dims["wire_beng_r_mm"] == pytest.approx(1.72)
    assert dims["coil_straight_extra_mm"] == pytest.approx(1.0)
    assert dims["coil_lead_total_mm"] == pytest.approx(9.1)
    assert "template base coil rules" in dims["coil_source"]


def test_semicircle_is_no_longer_the_fallback(no_reference):
    """A geometry WITHOUT wire_beng_r / coil_straight_extra / coil_lead_total used to get the semicircle.  It
    now gets the built coil; the semicircle is only the named comparison method."""
    p = MotorParams(0.012)
    k_auto = end_winding_factor(p, G40, die="any die", config="c", cad_overrides={})
    k_semi = end_winding_factor(p, G40, method="semicircle")
    assert k_semi == pytest.approx((math.pi * 5.8 / 2 + 12) / 12, rel=1e-9)        # 3.3 + 2.5 = 5.8 mm span
    assert k_auto != pytest.approx(k_semi, rel=1e-3)
    assert k_auto == pytest.approx(end_winding_factor(p, G40, method="cad_coil", die="any die", config="c",
                                                      cad_overrides={}), rel=1e-12)
    assert k_auto > k_semi                    # bends, overhang and lead tails make the built coil longer
    with pytest.raises(ValueError):
        end_winding_factor(p, G40, method="guess")


def test_the_built_coil_follows_every_geometry_edit(no_reference):
    """The base rule ties the bend radius to the tooth (bend = tooth/2 + ins + sx/2 - 0.05) and the legs to the stack: a wider tooth,
    a wider wire and a longer stack each move k_end the way they move the built coil - no stale number survives an edit."""
    kw = dict(die="d", config="c", cad_overrides={})
    k0 = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20), **kw)
    k_tooth = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20, tooth_width=4.0), **kw)
    k_wire = end_winding_factor(MotorParams(0.020), dict(G40, motor_length=20, wire_width=3.0), **kw)
    k_long = end_winding_factor(MotorParams(0.040), dict(G40, motor_length=40), **kw)
    assert k_tooth > k0 and k_wire > k0 and k_long < k0
    c = coil_build.coil_parameters(dict(G40, tooth_width=4.0), **kw)
    assert c["wire_beng_r"] == pytest.approx(4.0 / 2 + 0.07 + 0.05 - 0.05)


def test_explicit_die_keys_override_the_built_coil(no_reference):
    p = MotorParams(0.035, num_slots=24)
    geo = dict(G40, tooth_width=9.8, wire_width=5.0, num_wires_per_slot=14, insulation_thickness=0.15, wire_spacing_y=0.13,
               motor_length=35, wire_beng_r=2.5, coil_lead_total=20.0)
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={})
    assert c["wire_beng_r"] == 2.5 and c["coil_lead_total"] == 20.0
    assert c["coil_straight_extra"] == pytest.approx(1.0)               # not typed: the build's rule
    assert c["typed"] == ["wire_beng_r", "coil_lead_total"]
    # CIANO28 150_35 BOM: 1604.58 mm
    dims = cad_coil_dimensions(p, dict(geo, coil_straight_extra=1.0), die="d", config="c", cad_overrides={})
    assert abs(dims["total_coil_length_mm"] - 1604.58) / 1604.58 < 0.03


def test_cad_overrides_of_the_leads_reach_k_end(no_reference):
    """Geometry > Full edits the lead extension / stub length (CAD-only rows): the built coil changes, so does k_end."""
    p = MotorParams(0.020)
    geo = dict(G40, motor_length=20)
    k0 = end_winding_factor(p, geo, die="d", config="c", cad_overrides={})
    c = coil_build.coil_parameters(geo, die="d", config="c", cad_overrides={"coil_lead_len": 15.0})
    assert c["coil_lead_total"] == pytest.approx(4.1 + 15.0)
    k1 = end_winding_factor(p, geo, die="d", config="c", cad_overrides={"coil_lead_len": 15.0})
    assert k1 - k0 == pytest.approx(2 * 10.0 / 7 / 40.0, rel=1e-9)     # two leads 10 mm longer over 7 turns of 2·20 mm


def test_die_reference_profile_drives_the_coil(monkeypatch, tmp_path):
    """A die with its own CAD reference profile builds ITS coil: constant bend radius, its own straight overhang and leads, all
    evaluated through the same rows the CAD build evaluates, with the simulation geometry laid on top."""
    rows = [
        {"name": "tooth_width", "freecad_expression": "10 mm", "unit": "mm"},
        {"name": "insulation_thickness", "freecad_expression": "0.1 mm", "unit": "mm"},
        {"name": "wire_spacing_x", "freecad_expression": "0.1 mm", "unit": "mm"},
        {"name": "motor_length", "freecad_expression": "40 mm", "unit": "mm"},
        {"name": "coil_x_in", "freecad_expression": "tooth_width / 2 + insulation_thickness + wire_spacing_x / 2", "unit": "mm"},
        {"name": "wire_beng_r", "freecad_expression": "4.5 mm", "unit": "mm"},
        {"name": "coil_straight_len", "freecad_expression": "motor_length + 1.5 mm", "unit": "mm"},
        {"name": "coil_straight_extra", "freecad_expression": "coil_straight_len - motor_length", "unit": "mm"},
        {"name": "coil_lead_ext", "freecad_expression": "10 mm", "unit": "mm"},
        {"name": "coil_lead_len", "freecad_expression": "12 mm", "unit": "mm"},
        {"name": "coil_lead_total", "freecad_expression": "coil_lead_ext + coil_lead_len", "unit": "mm"},
    ]
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "GEN_X.json").write_text(json.dumps({"profile": {}, "user_parameters": rows}), encoding="utf-8")
    monkeypatch.setattr(store, "REFERENCE_DIR", str(ref))
    monkeypatch.setattr(store, "BASE_PROFILE", str(ref / "CIANO14_40_12.json"))
    c = coil_build.coil_parameters({"tooth_width": 10.0, "motor_length": 160.0}, die="GEN X", config="G1", cad_overrides={})
    assert c["wire_beng_r"] == pytest.approx(4.5)
    assert c["coil_straight_extra"] == pytest.approx(1.5)                # motor_length + 1.5 mm, at the LIVE stack
    assert c["coil_lead_total"] == pytest.approx(22.0)
    assert c["profile"] == "GEN_X.json" and "GEN_X.json" in c["source"]
    c2 = coil_build.coil_parameters({"tooth_width": 10.0, "motor_length": 160.0}, die="GEN X", config="G1",
                                    cad_overrides={"coil_lead_ext": 20.0})
    assert c2["coil_lead_total"] == pytest.approx(32.0)
    # a die WITHOUT its own profile and no base profile on the host: the template rules, named
    c3 = coil_build.coil_parameters({"tooth_width": 10.0, "motor_length": 160.0}, die="OTHER", config="c", cad_overrides={})
    assert c3["profile"] is None and "template base coil rules" in c3["source"]


def test_template_rules_match_the_base_reference_profile_when_present():
    """TEMPLATE_BASE_RULES is the fallback for hosts without the private files; where the base profile IS available it must say the
    same thing (bend radius, straight overhang, leads) for any geometry."""
    if not store.reference_available() or not __import__("os").path.exists(store.BASE_PROFILE):
        pytest.skip("CAD reference files are private and not on this host")
    geo = dict(G40, tooth_width=7.7, insulation_thickness=0.12, wire_spacing_x=0.2, motor_length=95.0)
    ref = coil_build.coil_parameters(geo, die="CIANO14 40 new", config="c", cad_overrides={})
    tpl = coil_build._from_template(coil_build._numeric_geo(geo), {})
    for k in coil_build.COIL_KEYS:
        assert ref[k] == pytest.approx(tpl[k], abs=1e-9), k


def test_edge_cases():
    assert end_winding_factor(MotorParams(0.0)) == 1.0
    assert end_winding_factor(MotorParams(-0.01)) == 1.0
    # no geometry at all still builds a coil (tooth derived from the slot pitch, template wire) - never a silent semicircle
    k = end_winding_factor(MotorParams(0.02), None, die="d", config="c", cad_overrides={})
    assert k > 1.0 and k != pytest.approx(end_winding_factor(MotorParams(0.02), None, method="semicircle"), rel=1e-3)
