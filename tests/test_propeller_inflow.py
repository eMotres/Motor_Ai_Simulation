"""A0 of the propeller module (docs/PROPELLER_MODULE_PLAN_2026-11-15.md): one inflow interface,
with the momentum model behind it bit-identical to ``propeller.py``.

``tests/data/propeller_momentum_golden.json`` holds numbers generated from the UNCHANGED
``propeller.operating_point`` (see its ``_how`` field).  Equality is exact (``==``): the
wrapper may not change a single bit of any stored number.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_inflow as pi

GOLDEN = json.loads((Path(__file__).parent / "data" / "propeller_momentum_golden.json").read_text())


def test_golden_is_not_empty_and_covers_every_selectable_propeller():
    ids = {p["id"] for p in GOLDEN["points"]}
    assert ids == {p.id for p in pp.list_propellers() if p.selectable}
    assert len(GOLDEN["points"]) >= 300


def test_momentum_model_is_bit_identical_to_the_golden_numbers():
    for g in GOLDEN["points"]:
        prop = pp.get_propeller(g["id"])
        f = pi.get_model("momentum-1").evaluate(
            pi.InflowRequest(propeller=prop, rpm=g["rpm"], rho=g["rho"], **g["kw"]))
        assert f.model_id == "momentum-1"
        assert f.thrust_N == g["thrust_N"], g
        assert f.torque_Nm == g["torque_Nm"], g
        assert f.shaft_power_W == g["shaft_power_W"], g
        assert f.wake_velocity_ms == g["wake_velocity_ms"], g
        assert f.air_speed_ms == g["air_speed_ms"], g
        assert f.extra["slipstream_factor"] == g["slipstream_factor"], g


def test_the_existing_functions_still_give_the_golden_numbers():
    # the other half of "changes no stored number": propeller.py itself is untouched
    for g in GOLDEN["points"]:
        op = pp.operating_point(pp.get_propeller(g["id"]), g["rpm"], g["rho"], **g["kw"])
        assert op["air_speed_ms"] == g["air_speed_ms"]
        assert op["thrust_N"] == g["thrust_N"]


def test_air_speed_for_thermal_keeps_its_keys():
    # A0 adds NO key to the existing responses (the model id lives on AirField only)
    op = pp.air_speed_for_thermal(next(p.id for p in pp.list_propellers() if p.selectable), 5000.0)
    assert "air_model" not in op and "model_id" not in op
    assert op["air_speed_mps"] == op["air_speed_ms"]


def test_missing_quantities_are_none_not_zero_and_flagged():
    pid = next(p.id for p in pp.list_propellers() if p.selectable)
    f = pi.air_field(pid, 5000.0, r_min_m=0.0, r_max_m=0.02)
    assert len(f.bands) == 1
    assert f.bands[0].swirl_ms is None
    assert (f.bands[0].r_min_m, f.bands[0].r_max_m) == (0.0, 0.02)
    assert f.bands[0].axial_ms == f.air_speed_ms
    assert "uniform_profile" in f.flags and "swirl_not_modelled" in f.flags


def test_static_only_and_unknown_model_are_refused():
    pid = next(p.id for p in pp.list_propellers() if p.selectable)
    with pytest.raises(pi.InflowModelError):
        pi.air_field(pid, 5000.0, v_inf_ms=3.0)
    with pytest.raises(pi.InflowModelError):
        pi.get_model("nope")
    with pytest.raises(pp.PropellerNotFound):
        pi.air_field("no_such_prop", 5000.0)


def test_registry_lists_momentum_and_accepts_another_model():
    assert "momentum-1" in pi.available_models()

    class Dummy:
        model_id = "dummy-test"

        def evaluate(self, req):
            return pi.get_model("momentum-1").evaluate(req)

    saved = dict(pi._MODELS)
    try:
        pi.register_model(Dummy())
        assert "dummy-test" in pi.available_models()
    finally:
        pi._MODELS.clear()
        pi._MODELS.update(saved)
