"""Ø85 passport inputs (2026-10-05): the machine spec, the two-cooling entry in
config/cooling_options.yaml, the four T-Motor G-series propellers added for it,
and the spec's labelled hot-temperature override (never a silent clamp)."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIE = "CIANO28 85 20SW1200"
NEW_PROPS = ("tmotor_g30x10_5", "tmotor_g32x11", "tmotor_g36x11_5", "tmotor_g40x13_1")


def test_specs_load_and_name_their_machines():
    from motor_ai_sim.passport_v1.spec import load_spec
    d40 = load_spec(ROOT / "config" / "passport_specs" / "CIANO14_40_new.yaml")
    d85 = load_spec(ROOT / "config" / "passport_specs" / "CIANO28_85_20SW1200.yaml")
    assert set(d40["machines"]) == {"L12", "L20"}
    m = d85["machines"]["L13"]
    assert d85["die"] == DIE
    assert m["controller"]["n_parallel"] == 2 and m["controller"]["device"] == "IQE036N08NM6SC"
    assert m["hot_override"]["magnet_c"] == 150.0
    assert {s["cooling"] for s in m["cooling_studies"]} == {"robotics", "propeller_air"}


def test_spec_rejects_unknown_keys(tmp_path):
    from motor_ai_sim.passport_v1.spec import SpecError, load_spec
    p = tmp_path / "x.yaml"
    p.write_text("die: X\ninputs_subdir: d\nmachines:\n  A:\n    config: A\n    rated_duty: r\n"
                 "    version: '12S'\n    owner_bus_V: [1, 2, 3]\n    owner_rated: {rpm: 1, I_arms: 1}\n"
                 "    peak_duty: p\n    typo_key: 1\n", encoding="utf-8")
    with pytest.raises(SpecError):
        load_spec(p)


def test_cooling_options_offer_two_coolings_and_a_valid_default():
    from motor_ai_sim.cooling_options import cooling_options
    c = cooling_options(DIE, "L13")
    assert c["restricted"] and c["cooling_options"] == ["robotics", "propeller_air"]
    assert set(NEW_PROPS) <= set(c["propellers"])
    assert c["default_propeller"] in NEW_PROPS and not c["bad_defaults"]


@pytest.mark.parametrize("pid", NEW_PROPS)
def test_new_props_have_measured_torque_and_cover_the_motor_speeds(pid):
    from motor_ai_sim import propeller as PP
    p = PP.get_propeller(pid)
    s = PP.summary(p)
    assert s["selectable"] and s["power_data"] == "measured_torque"
    # torque rises with speed and stays inside the Ø85's torque range at its speeds
    t1, t2 = PP.torque_Nm(p, 1000.0), PP.torque_Nm(p, 2000.0)
    assert 0.2 < t1 < t2 < 10.0
    # electrical power is never shaft power: the shaft power is tau·omega of the fit
    op = PP.operating_point(p, 2000.0)
    assert abs(op["shaft_power_W"] - op["torque_Nm"] * 2000.0 * 2 * 3.141592653589793 / 60) < 1e-6 * op["shaft_power_W"] + 1e-9
