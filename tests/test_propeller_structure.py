"""Structural root check of the own propeller (propeller_structure.py, plan section 9.5).

Hand-checkable beam cases on a uniform rectangular blade, monotonicity, scaling, the placeholder labels, and one run through a real
candidate.  No assertion claims accuracy against a real propeller.
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_structure as ps

R0, R1 = 0.02, 0.12                      # root and tip radius of the test blade, m
B_W, H = 0.02, 0.004                     # rectangular section: chord (width) and thickness, m
STEEL_LIKE = {"density_kg_m3": 2000.0, "tensile_allowable_Pa": 1.0e12, "bending_allowable_Pa": 1.0e12, "source": "test"}


def rect(r, b=B_W, h=H, twist=0.0):
    return {"r_m": r, "chord_m": b, "twist_deg": twist, "thickness_rel": h / b, "area_m2": b * h, "centroid_from_le_m": b / 2.0,
            "i_flap_m4": b * h ** 3 / 12.0, "i_edge_m4": h * b ** 3 / 12.0, "n_max_m": h / 2.0, "c_max_m": b / 2.0, "method": "test_rectangle"}


def blade(**kw):
    return [rect(R0 + (R1 - R0) * i / 5.0, **kw) for i in range(6)]


def uniform_loads(w_t=0.0, w_q=0.0):
    rs = [R0 + (R1 - R0) * i / 50.0 for i in range(51)]
    return {"r_m": rs, "dT_dr_N_per_m": [w_t] * 51, "dQ_dr_Nm_per_m": [w_q * r for r in rs]}     # tangential force per length = w_q


def stress(sections, rpm=6000.0, material=STEEL_LIKE, loads=None, **kw):
    return ps.root_stress_from_sections(sections, rpm, material, loads or uniform_loads(), **kw)


def test_centrifugal_tension_of_a_uniform_blade():
    rpm = 6000.0
    om = rpm * 2.0 * math.pi / 60.0
    out = stress(blade(), rpm)
    expected = 2000.0 * (B_W * H) * om ** 2 * (R1 ** 2 - R0 ** 2) / 2.0
    assert out["forces"]["centrifugal_tension_N"] == pytest.approx(expected, rel=1e-6)
    assert out["stress_Pa"]["centrifugal_axial"] == pytest.approx(expected / (B_W * H), rel=1e-6)
    assert out["forces"]["blade_mass_kg"] == pytest.approx(2000.0 * B_W * H * (R1 - R0), rel=1e-6)


def test_bending_of_a_uniform_load_flapwise():
    w = 50.0                                                  # N/m of axial load
    out = stress(blade(), 1.0, loads=uniform_loads(w_t=w))
    m = w * (R1 - R0) ** 2 / 2.0
    assert out["forces"]["root_moment_flap_Nm"] == pytest.approx(m, rel=1e-6)
    assert out["forces"]["thrust_per_blade_N"] == pytest.approx(w * (R1 - R0), rel=1e-6)
    sigma = m * (H / 2.0) / (B_W * H ** 3 / 12.0)
    assert out["stress_Pa"]["bending"] == pytest.approx(sigma, rel=1e-6)


def test_bending_of_a_uniform_load_edgewise_uses_the_chordwise_stiffness():
    w = 30.0
    out = stress(blade(), 1.0, loads=uniform_loads(w_q=w))
    m = w * (R1 - R0) ** 2 / 2.0
    assert out["forces"]["root_moment_edge_Nm"] == pytest.approx(m, rel=1e-6)
    assert out["stress_Pa"]["bending"] == pytest.approx(m * (B_W / 2.0) / (H * B_W ** 3 / 12.0), rel=1e-6)


def test_twist_turns_the_load_onto_the_other_axis():
    w = 50.0
    flat = stress(blade(twist=0.0), 1.0, loads=uniform_loads(w_t=w))["stress_Pa"]["bending"]
    steep = stress(blade(twist=60.0), 1.0, loads=uniform_loads(w_t=w))["stress_Pa"]["bending"]
    assert steep < flat                                       # the thin direction carries less of the load as the section turns


def test_combined_stress_and_safety_factor():
    mat = {"density_kg_m3": 1000.0, "tensile_allowable_Pa": 40.0e6, "bending_allowable_Pa": 80.0e6, "source": "test"}
    out = stress(blade(), 5000.0, mat, uniform_loads(w_t=20.0))
    s = out["stress_Pa"]
    assert s["combined"] == pytest.approx(s["centrifugal_axial"] + s["bending"])
    assert out["utilisation"] == pytest.approx(s["centrifugal_axial"] / 40.0e6 + s["bending"] / 80.0e6)
    assert out["safety_factor"] == pytest.approx(1.0 / out["utilisation"])
    assert out["allowable_Pa"]["combined_equivalent"] == pytest.approx(s["combined"] * out["safety_factor"])
    assert out["passes"] is (out["safety_factor"] >= 3.0)


def test_safety_factor_falls_with_rpm_and_rises_with_thickness():
    mat = {"density_kg_m3": 1300.0, "tensile_allowable_Pa": 40.0e6, "bending_allowable_Pa": 55.0e6, "source": "test"}
    loads = uniform_loads(w_t=10.0, w_q=2.0)
    f = [stress(blade(), rpm, mat, loads)["safety_factor"] for rpm in (3000.0, 5000.0, 8000.0, 12000.0)]
    assert all(a > b for a, b in zip(f, f[1:]))
    g = [stress(blade(h=h), 6000.0, mat, loads)["safety_factor"] for h in (0.002, 0.003, 0.004, 0.006)]
    assert all(a < b for a, b in zip(g, g[1:]))


def test_centrifugal_stress_scales_with_density_and_rpm_squared():
    m1 = dict(STEEL_LIKE, density_kg_m3=1000.0)
    m2 = dict(STEEL_LIKE, density_kg_m3=2500.0)
    a = stress(blade(), 4000.0, m1)["stress_Pa"]["centrifugal_axial"]
    b = stress(blade(), 4000.0, m2)["stress_Pa"]["centrifugal_axial"]
    c = stress(blade(), 8000.0, m1)["stress_Pa"]["centrifugal_axial"]
    assert b == pytest.approx(2.5 * a, rel=1e-9)
    assert c == pytest.approx(4.0 * a, rel=1e-9)


def test_a_weak_blade_fails_the_factor_three():
    mat = {"density_kg_m3": 1300.0, "tensile_allowable_Pa": 5.0e6, "bending_allowable_Pa": 5.0e6, "source": "test"}
    out = stress(blade(), 9000.0, mat, uniform_loads(w_t=40.0))
    assert out["safety_factor"] < 3.0
    assert out["passes"] is False
    assert out["required_factor"] == 3.0


def test_the_material_table_is_labelled_placeholder():
    assert set(ps.MATERIALS) >= {"pa6_gf30", "pa6_cf30", "cfrp_laminate"}
    for k, m in ps.MATERIALS.items():
        assert m["source"] == "placeholder - supplier data sheet / factory question", k
        assert m["density_kg_m3"] > 0 and m["tensile_allowable_Pa"] > 0 and m["bending_allowable_Pa"] > 0
        assert m["direction_note"]
    with pytest.raises(ps.StructureError):
        ps.get_material("unobtainium")
    with pytest.raises(ps.StructureError):
        ps.get_material({"density_kg_m3": 1000.0})


def test_real_candidate_end_to_end_is_labelled_not_validated():
    d = pd.load_design()
    raw = pd.candidate_to_propeller2(d, pd.seed_vector(d, 10, 2))
    secs = ps.blade_sections(raw)
    assert len(secs) == len(raw["blade"]["stations"])
    assert all(s["area_m2"] > 0 and s["i_flap_m4"] > 0 and s["i_edge_m4"] > s["i_flap_m4"] for s in secs)
    assert all(s["method"].startswith("airfoil_outline") for s in secs)
    out = ps.root_stress(raw, 8000.0, "pa6_gf30")
    assert out["validated"] is False
    assert out["basis"] == "beam model, placeholder material, root flow not validated"
    assert out["material"]["source"] == "placeholder - supplier data sheet / factory question"
    assert out["rpm_max_assumed"] is True and out["rpm_check"] == pytest.approx(1.2 * 8000.0)
    assert out["overspeed_assumption"]
    assert out["section_used"]["r_m"] == pytest.approx(secs[0]["r_m"])
    assert out["stress_Pa"]["combined"] > 0 and out["safety_factor"] > 0
    assert isinstance(out["passes"], bool)
    given = ps.root_stress(raw, 8000.0, "pa6_gf30", rpm_max=9000.0)
    assert given["rpm_max_assumed"] is False and given["rpm_check"] == 9000.0 and given["overspeed_assumption"] is None
    assert given["stress_Pa"]["centrifugal_axial"] < out["stress_Pa"]["centrifugal_axial"]
    stronger = ps.root_stress(raw, 8000.0, "cfrp_laminate")
    assert stronger["safety_factor"] > out["safety_factor"]


def test_thin_ellipse_fallback_is_used_without_coordinates():
    d = pd.load_design()
    raw = pd.candidate_to_propeller2(d, pd.seed_vector(d, 10, 2))
    for s in raw["blade"]["stations"]:
        s["airfoil"] = "no-such-airfoil"
    secs = ps.blade_sections(raw)
    assert all(s["method"] == "thin_ellipse_approximation" for s in secs)
    s = secs[0]
    assert s["area_m2"] == pytest.approx(math.pi / 4.0 * s["chord_m"] * s["thickness_rel"] * s["chord_m"], rel=1e-9)
