"""zone-1: per-zone axial air speed at the motor from a propeller's radial profile (propeller_zone_air.py)."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import propeller_design as pd
from motor_ai_sim import propeller_inflow as pi
from motor_ai_sim import propeller_zone_air as za

RADII = {"rotor_outer_radius_m": 0.01175, "stator_outer_radius_m": 0.020}


def field(bands, flags=()):
    return pi.AirField(model_id="test-1", propeller_id="p", rpm=10000.0, rho_kg_m3=1.225, thrust_N=1.0, torque_Nm=0.1,
                       shaft_power_W=100.0, wake_velocity_ms=1.0, air_speed_ms=None, bands=tuple(bands), flags=tuple(flags))


def test_payload_has_exactly_the_interface_keys():
    f = field([pi.Band(0.0, 0.03, 5.0, None)])
    z = za.zone_air(f, RADII)
    assert sorted(z) == ["calibration", "model_id", "source", "zones"]
    assert z["model_id"] == "zone-1" and z["calibration"] is None
    assert set(z["zones"]) <= set(za.ZONE_KEYS)
    assert z["zones"] == {"rotor_ends": 5.0, "stator_ends": 5.0}      # uniform field -> the same speed in every zone it covers


def test_area_weighting_per_zone():
    f = field([pi.Band(0.0, 0.01, 2.0, None), pi.Band(0.01, 0.02, 6.0, None), pi.Band(0.02, 0.03, 9.0, None)])
    d = za.zone_air_detail(f, dict(RADII, housing_outer_radius_m=0.025))
    z = d["air_zones"]["zones"]
    a1, a2 = math.pi * 0.01 ** 2, math.pi * (0.01175 ** 2 - 0.01 ** 2)
    assert z["rotor_ends"] == pytest.approx((a1 * 2.0 + a2 * 6.0) / (a1 + a2))
    assert z["stator_ends"] == pytest.approx(6.0)
    assert z["housing_outer"] == pytest.approx(9.0)
    assert d["coverage"] == {"housing_outer": 1.0, "stator_ends": 1.0, "rotor_ends": 1.0}


def test_zone_inside_the_hub_is_left_out_not_zero():
    f = field([pi.Band(0.014, 0.03, 7.0, 1.0), pi.Band(0.03, 0.06, None, None)])
    d = za.zone_air_detail(f, RADII)
    assert "rotor_ends" not in d["air_zones"]["zones"] and "rotor_ends_not_modelled" in d["flags"]
    # review 2026-11-22 H9: a partly modelled zone is reported with its coverage, not exported as the whole-zone speed
    assert "stator_ends" not in d["air_zones"]["zones"] and "stator_ends_partly_modelled" in d["flags"]
    assert d["partial_zones"]["stator_ends"]["axial_ms"] == pytest.approx(7.0)
    assert d["partial_zones"]["stator_ends"]["coverage"] == d["coverage"]["stator_ends"] < za.FULL_ZONE_COVERAGE
    assert "housing_outer_not_given" in d["flags"] and "housing_outer" not in d["air_zones"]["zones"]


def test_bad_radii_are_refused_by_name():
    f = field([pi.Band(0.0, 0.03, 5.0, None)])
    for bad in ({"stator_outer_radius_m": 0.02}, {"rotor_outer_radius_m": 0.03, "stator_outer_radius_m": 0.02},
                dict(RADII, housing_outer_radius_m=0.019), dict(RADII, rotor_outer_radius_m=-1.0)):
        with pytest.raises(za.ZoneAirError):
            za.zone_air(f, bad)


def test_real_bemt_field_of_the_seed_design():
    D = pd.load_design()
    prop = pd.build_propeller(D, pd.seed_vector(D, 10, 3))
    f = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=12000.0, rho=1.225, r_min_m=0.0, r_max_m=0.02))
    d = za.zone_air_detail(f, RADII)
    z = d["air_zones"]["zones"]
    assert d["air_zones"]["source"].startswith("bemt-1 | ")
    assert all(math.isfinite(v) and v >= 0 for v in z.values())
    # the seed's blade starts at 14 mm (root cut-out), so the rotor zone (r < 11.75 mm) has no modelled band
    assert "rotor_ends" not in z and "rotor_ends_not_modelled" in d["flags"]
    # ... and covers only part of the stator-end annulus: reported, not exported (review 2026-11-22 H9)
    assert "stator_ends" not in z and d["partial_zones"]["stator_ends"]["axial_ms"] > 0


def test_a_small_modelled_sliver_is_not_exported_as_the_zone_speed():
    # review 2026-11-21 H9: a 19-20 mm band covers ~15 % of an 11.75-20 mm stator-end annulus
    radii = dict(RADII, rotor_outer_radius_m=0.01175, stator_outer_radius_m=0.020)
    f = field([pi.Band(0.019, 0.020, 10.0, 1.0)])
    d = za.zone_air_detail(f, radii)
    assert d["coverage"]["stator_ends"] < za.MIN_ZONE_COVERAGE
    assert "stator_ends" not in d["air_zones"]["zones"] and "stator_ends_coverage_too_small" in d["flags"]


def test_a_half_covered_zone_is_reported_not_exported():
    # review 2026-11-22 H9: 51 % coverage still must not become the whole-zone film of the thermal request
    r_rot, r_sta = RADII["rotor_outer_radius_m"], RADII["stator_outer_radius_m"]
    r_in = math.sqrt(r_sta ** 2 - 0.51 * (r_sta ** 2 - r_rot ** 2))
    d = za.zone_air_detail(field([pi.Band(r_in, 0.03, 10.0, 1.0)]), RADII)
    assert d["coverage"]["stator_ends"] == pytest.approx(0.51, abs=1e-4)
    assert "stator_ends" not in d["air_zones"]["zones"] and "stator_ends" not in za.zone_air(field([pi.Band(r_in, 0.03, 10.0, 1.0)]), RADII)["zones"]
    assert d["partial_zones"]["stator_ends"] == {"axial_ms": 10.0, "coverage": d["coverage"]["stator_ends"]}
