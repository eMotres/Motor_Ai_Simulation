"""P1 of the propeller module: the parametric family 10 / 11 / 12 / 13 in (propeller_design.py).

Checks the construction (every candidate is a valid propeller-2 dict), the explicit scaling rule, the validity rules, the way the
manufacturing profile enters, and the evaluation at the L12 / L20 operating points.  No assertion claims accuracy against a real propeller.
"""
from __future__ import annotations

import copy
import dataclasses
import math

import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_design as pd

D = pd.load_design()


def seed(diam=10, blades=2):
    return pd.seed_vector(D, diam, blades)


def with_profile(**limits):
    raw = copy.deepcopy(D.raw)
    raw["manufacturing"] = {"process": "test", "limits": {k: {"value": v, "source": "test", "confirmed_by": "tester"} for k, v in limits.items()}}
    return dataclasses.replace(D, raw=raw, manufacturing=af.parse_manufacturing(raw["manufacturing"]))


def test_design_file_content_and_what_is_unconfirmed():
    assert D.id == "own_family" and D.diameters_in == (10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14) and D.blades_options == [2, 3]
    assert sorted(D.operating_points) == ["L12", "L20"]
    assert D.operating_points["L12"].torque_limit_Nm == 0.64 and D.operating_points["L12"].rated_rpm_ref == 13628.1
    assert D.operating_points["L20"].torque_limit_Nm == 0.82 and D.operating_points["L20"].rated_rpm_ref == 13000.0
    assert D.inputs_unconfirmed() and D.manufacturing.limits == {}              # nothing invented, nothing confirmed
    assert D.raw["bounds"]["basis"] == "geometric_validity_placeholder"


@pytest.mark.parametrize("diam,blades", [(10, 2), (10, 3), (11, 2), (12, 3), (13, 2), (13, 3)])
def test_every_seed_candidate_is_valid_and_loads_as_propeller2(diam, blades):
    v = seed(diam, blades)
    assert pd.check_candidate(D, v) == []
    raw = pd.candidate_to_propeller2(D, v)
    assert raw["schema"] == "propeller-2" and raw["geometry"]["blades"] == blades and len(raw["blade"]["stations"]) == pd.N_STATIONS
    prop = pd.build_propeller(D, v)
    assert prop.bemt_ready and prop.blade.estimated and prop.blade.geometry_source == "inferred"
    assert prop.blade.tip_radius_m == pytest.approx(diam * 0.0254 / 2.0, rel=1e-9)
    assert not prop.selectable                                                    # no bench data: loads from the BEMT only


def test_seed_twist_is_the_constant_pitch_helix_and_ids_are_stable_and_distinct():
    v = seed()
    assert v["twist_deg@0.2"] == pytest.approx(math.degrees(math.atan(0.5 / (math.pi * 0.2))))
    assert v["twist_deg@1"] == pytest.approx(math.degrees(math.atan(0.5 / math.pi)))
    a, b, c = (pd.candidate_to_propeller2(D, x)["id"] for x in (seed(), seed(), seed(10, 3)))
    assert a == b and a != c
    w = seed()
    w["chord_R@0.45"] += 0.01
    assert pd.candidate_to_propeller2(D, w)["id"] != a


def test_scaling_rule_similar_planform_absolute_hub():
    a, b = pd.candidate_to_propeller2(D, seed(10)), pd.candidate_to_propeller2(D, seed(13))
    sa, sb = a["blade"]["stations"], b["blade"]["stations"]
    # the root cut-out is absolute: the first station sits at the same mm on both
    assert sa[0]["r_mm"] == sb[0]["r_mm"] == 14.0 and a["blade"]["hub_radius_mm"] == b["blade"]["hub_radius_mm"] == 10.0
    # at equal r/R the chord scales with D and the twist does not
    Ra, Rb = 127.0, 13 * 25.4 / 2
    ra = {round(s["r_mm"] / Ra, 3): s for s in sa}
    for s in sb:
        key = round(s["r_mm"] / Rb, 3)
        if key in ra:
            assert s["chord_mm"] / ra[key]["chord_mm"] == pytest.approx(13 / 10, rel=2e-3)
            assert s["twist_deg"] == pytest.approx(ra[key]["twist_deg"], abs=0.02)
    assert sa[-1]["chord_mm"] / Ra == pytest.approx(sb[-1]["chord_mm"] / Rb, rel=1e-3)


def test_validity_rules_name_the_variable():
    def rules(**kw):
        v = seed()
        v.update(kw)
        return {x["rule"] for x in pd.check_candidate(D, v)}

    assert rules() == set()
    assert "out_of_bounds" in rules(**{"chord_R@0.45": 0.5})
    assert "out_of_bounds" in rules(**{"twist_deg@0.65": -1.0})
    assert "out_of_bounds" in rules(hub_radius_mm=3.0)
    assert "out_of_bounds" in rules(**{"chord_R@0.2": float("nan")})
    assert "twist_must_not_increase_outward" in rules(**{"twist_deg@1": 30.0})
    assert "root_cutout_inside_hub" in rules(hub_radius_mm=20.0, root_cutout_mm=12.0)
    assert "root_cutout_beyond_inner_zone" in rules(root_cutout_mm=60.0)
    assert "blades_not_offered" in rules(blades=4)
    assert "diameter_not_in_family" in rules(diameter_in=14.5)
    assert "airfoil_not_offered" in rules(airfoil_tip="naca4-9909")
    v = seed()
    del v["hub_radius_mm"]
    assert pd.check_candidate(D, v)[0]["rule"] == "missing"


def test_the_manufacturing_profile_reaches_the_stations_and_the_blade_checks():
    ideal = pd.candidate_to_propeller2(D, seed())["blade"]["stations"]
    prof = with_profile(wall_min_mm=1.5)
    made = pd.candidate_to_propeller2(prof, seed())["blade"]["stations"]
    assert all(m["thickness_rel"] >= i["thickness_rel"] - 1e-9 for m, i in zip(made, ideal))
    assert any(m["thickness_rel"] > i["thickness_rel"] for m, i in zip(made, ideal))        # the thin tip is thickened
    assert all(m["thickness_rel"] * m["chord_mm"] >= 1.5 - 0.02 for m in made)
    assert pd.check_candidate(prof, seed()) == []                                            # thickened to the minimum: no violation
    twisty = with_profile(twist_release_max_deg=5.0)
    assert [x["rule"] for x in pd.check_candidate(twisty, seed())] == ["manufacturing:twist_release_max_deg"]


def test_variables_table_groups():
    rows = pd.variables_table(D, seed())
    by = {r["name"]: r for r in rows}
    assert by["chord_R@0.2"]["group"] == by["twist_deg@0.3"]["group"] == by["hub_radius_mm"]["group"] == "inner"
    assert by["chord_R@0.45"]["group"] == by["twist_deg@1"]["group"] == "outer"
    assert by["blades"]["options"] == [2, 3] and by["diameter_in"]["options"] == [10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14]
    assert all(r["confirmed"] is False for r in rows if r["group"] in ("inner", "outer"))
    assert len(rows) == len(pd.variable_names(D))


def test_evaluation_at_the_motor_rated_points():
    r = pd.evaluate_candidate(D, seed(10, 2), "L12")
    assert r["model_id"] == "bemt-1" and r["estimated"] is True and r["validated_against"] is None and r["banner"] == pd.BANNER
    assert r["operating"]["torque_limit_Nm"] == 0.64
    assert r["torque_Nm"] == pytest.approx(0.64, rel=0.02) or "rpm_capped" in r["flags"]
    assert r["thrust_N"] > 0 and r["objectives"]["thrust_per_shaft_W"] == pytest.approx(r["thrust_N"] / r["shaft_power_W"])
    assert r["objectives"]["cooling_air_axial_ms"] is not None and r["objectives"]["cooling_air_axial_ms"] > 0
    for f in (pd.F_ROOT, pd.F_HUB, pd.F_AIRFOIL, pd.F_UNCONFIRMED, af.FLAG_UNCONFIRMED, "polar_estimated", "band_partly_inside_hub"):
        assert f in r["flags"], f
    assert r["constraints"]["torque_within_limit"] and r["constraints"]["bemt_converged"]
    again = pd.evaluate_candidate(D, seed(10, 2), "L12")
    assert again["thrust_N"] == r["thrust_N"] and again["design_id"] == r["design_id"]          # deterministic


def test_two_and_three_blades_are_different_designs_and_both_evaluate():
    a, b = pd.evaluate_candidate(D, seed(10, 2), "L12"), pd.evaluate_candidate(D, seed(10, 3), "L12")
    assert a["design_id"] != b["design_id"] and a["thrust_N"] != b["thrust_N"]
    assert a["objectives"]["thrust_per_shaft_W"] > 0 and b["objectives"]["thrust_per_shaft_W"] > 0


def test_l20_and_a_large_propeller_report_the_tip_speed_constraint():
    r = pd.evaluate_candidate(D, seed(13, 2), "L20")
    assert r["operating"]["torque_limit_Nm"] == 0.82
    assert r["constraints"]["tip_mach_within_limit"] == (r["tip_mach"] <= D.tip_mach_limit)
    assert r["tip_mach"] == pytest.approx(r["operating"]["rpm"] * 2 * math.pi / 60 * 13 * 0.0254 / 2 / 340.3, rel=0.01)


def test_refusals():
    with pytest.raises(pd.DesignError):
        pd.evaluate_candidate(D, seed(), "L99")
    bad = seed()
    bad["blades"] = 5
    with pytest.raises(pd.DesignError):
        pd.evaluate_candidate(D, bad, "L12")
    with pytest.raises(pd.DesignError):
        pd.candidate_to_propeller2(D, {})
    with pytest.raises(pd.DesignError):
        pd.load_design(__file__)
