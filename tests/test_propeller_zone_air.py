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
    assert d["air_zones"]["zones"]["stator_ends"] == pytest.approx(7.0) and "stator_ends_partly_modelled" in d["flags"]
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
    assert z["stator_ends"] > 0
