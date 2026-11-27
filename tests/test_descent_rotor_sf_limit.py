"""The opt-in rotor mechanics limit of the Descent (TASK_descent_rotor_sf_limit.md, owner 2026-11-27): sf_min + mech_rpm,
ONE rotor_stress solve per candidate geometry, the averaged safety factor as a HARD limit.  A rotor that cannot be meshed
or is not retained is a failed candidate, never a feasible one.  A request without the fields is the run it always was."""
from __future__ import annotations

import threading

import pytest
from pydantic import ValidationError

from motor_ai_sim.routes import optimization as O


def _m(**kw):
    m = {"efficiency": 0.97, "torque_per_mass_Nm_kg": 4.0, "T_ripple_pct": 5.0, "T_avg_Nm": 41.0,
         "J_coil_A_per_mm2": 11.0, "V_line_peak_V": 500.0, "V_peak": 290.0, "THD_LL_pct": 8.0}
    m.update(kw)
    return m


def _req(**kw):
    body = {"variables": [{"name": "tooth_width", "min": 0.0, "max": 0.0}],
            "operating_point": {"current_a": 10.0, "gamma_deg": 5.0, "rpm": 3000.0},
            "max_iters": 2, "gap_layers": 1, "run_id": "rotor-sf-test"}
    body.update(kw)
    return O.DescentRequest(**body)


@pytest.fixture(autouse=True)
def _clean():
    saved = (dict(O._RUN_LIMITS), dict(O._COG_LIMIT), dict(O._MECH_LIMIT))
    O._reset_run_limits()
    yield
    O._RUN_LIMITS.update(saved[0])
    O._COG_LIMIT.update(saved[1])
    O._MECH_LIMIT.update(saved[2])


def test_a_request_without_the_fields_plans_and_costs_as_before():
    req = _req()
    assert req.sf_min == 0.0 and req.mech_rpm == 0.0
    plan = O.prepare_descent(req)
    assert "mech_limit" not in plan and "mech_limit" not in plan["response"] and "mech_limit" not in plan["init_state"]
    O.begin_descent_state(plan)
    assert not O._rotor_sf_on() and O._MECH_LIMIT["sf_min"] == 0.0
    # with the limit off a rotor_sf on the metrics changes nothing
    assert O._limit_violation(_m(rotor_sf=0.1)) == O._limit_violation(_m()) == 0.0


def test_sf_min_needs_a_speed_and_the_plan_carries_the_limit():
    with pytest.raises(ValidationError):
        _req(sf_min=2.0)
    plan = O.prepare_descent(_req(sf_min=2.0, mech_rpm=14000.0))
    assert plan["mech_limit"] == {"sf_min": 2.0, "rpm": 14000.0, "mesh_mm": 1.0}
    assert plan["response"]["mech_limit"] == plan["init_state"]["mech_limit"] == plan["mech_limit"]
    O.begin_descent_state(plan)
    assert O._rotor_sf_on()
    O.begin_descent_state(O.prepare_descent(_req()))        # the next run does not inherit it
    assert not O._rotor_sf_on()


def test_the_limit_is_hard_and_a_missing_value_is_never_assumed_met():
    O._MECH_LIMIT.update(sf_min=2.0, rpm=14000.0)
    assert O._limit_violation(_m(rotor_sf=2.1)) == 0.0
    assert O._limit_violation(_m(rotor_sf=1.87)) == pytest.approx((2.0 - 1.87) / 2.0)
    assert O._limit_violation(_m(rotor_sf=0.0)) == pytest.approx(1.0)
    assert O._limit_violation(_m()) == 1.0                          # no value: broken
    assert O._limit_violation(_m(rotor_sf=None)) == 1.0
    assert O._limit_violation(_m(rotor_sf=float("nan"))) == 1.0


def test_one_solve_per_geometry_and_the_value_rides_on_the_result(monkeypatch):
    calls = []
    monkeypatch.setattr(O, "_rotor_sf_of", lambda dd, rpm, mesh: calls.append((dict(dd), rpm, mesh)) or {"sf": 2.4, "error": None})
    O._MECH_LIMIT.update(sf_min=2.0, rpm=14000.0, mesh_mm=1.0)
    memo, lock = {}, threading.Lock()
    a = O._attach_rotor_sf({"ok": True, "res": {}}, {"tooth_width": 11.3, "gamma_deg": 5.0}, memo, lock)
    b = O._attach_rotor_sf({"ok": True, "res": {}}, {"tooth_width": 11.3}, memo, lock)       # same geometry, other current
    c = O._attach_rotor_sf({"ok": True, "res": {}}, {"tooth_width": 11.4}, memo, lock)
    assert len(calls) == 2 and calls[0][0] == {"tooth_width": 11.3} and calls[0][1] == 14000.0
    assert a["res"]["rotor_sf"] == b["res"]["rotor_sf"] == c["res"]["rotor_sf"] == 2.4
    assert a["res"]["rotor_sf_rpm"] == 14000.0 and "rotor_sf_error" not in a["res"]


def test_a_failed_eval_or_an_off_run_is_not_touched(monkeypatch):
    monkeypatch.setattr(O, "_rotor_sf_of", lambda *a: pytest.fail("no solve expected"))
    memo, lock = {}, threading.Lock()
    off = {"ok": True, "res": {}}
    assert O._attach_rotor_sf(off, {"x": 1.0}, memo, lock) is off and "rotor_sf" not in off["res"]
    O._MECH_LIMIT.update(sf_min=2.0, rpm=14000.0)
    bad = {"ok": False, "error": "x"}
    assert O._attach_rotor_sf(bad, {"x": 1.0}, memo, lock) == {"ok": False, "error": "x"}


def test_a_rotor_that_cannot_be_meshed_or_held_is_a_failed_candidate(monkeypatch):
    from motor_ai_sim.routes import mechanical as M
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    def boom(*_a, **_k):
        raise RuntimeError("Curve loop is not closed")
    monkeypatch.setattr(M, "run_rotor_stress_at", boom)
    r = O._rotor_sf_of({"magnet_fill_down": 1.0}, 14000.0, 1.0)
    assert r["sf"] is None and "Curve loop" in r["error"]
    O._MECH_LIMIT.update(sf_min=2.0, rpm=14000.0)
    assert O._limit_violation({"rotor_sf": r["sf"]}) == 1.0

    monkeypatch.setattr(M, "run_rotor_stress_at", lambda *_a, **_k: {"cases": {"14000": {"retention": {"status": "fail"}}}})
    monkeypatch.setattr(rsm, "case_failed", lambda case: True)
    held = O._rotor_sf_of({"magnet_fill_down": 0.9}, 14000.0, 1.0)
    assert held["sf"] == 0.0 and "not retained" in held["error"]

    monkeypatch.setattr(rsm, "case_failed", lambda case: False)
    monkeypatch.setattr(M, "run_rotor_stress_at", lambda *_a, **_k: {"cases": {"14000": {"sf_min": 2.12}}})
    ok = O._rotor_sf_of({"magnet_fill_down": 0.85}, 14000.0, 1.0)
    assert ok == {"sf": 2.12, "error": None}
    monkeypatch.setattr(M, "run_rotor_stress_at", lambda *_a, **_k: {"cases": {"14000": {"sf_min": None}}})
    assert O._rotor_sf_of({}, 14000.0, 1.0)["sf"] is None


def test_the_call_goes_through_the_apps_route_hook_with_the_geometry_and_one_centrifugal_case(monkeypatch):
    from motor_ai_sim.routes import mechanical as M
    seen = {}
    monkeypatch.setattr(M, "run_rotor_stress_at", lambda temps, **kw: seen.update(temps=temps, **kw) or {"cases": {"x": {"sf_min": 2.5}}})
    O._rotor_sf_of({"magnet_fill_down": 0.9, "rotor_hole": 0.87}, 14000.0, 1.0)
    assert seen["temps"] == {} and seen["rpm"] == 14000.0 and seen["cases"] == "single" and seen["loads"] == "centrifugal"
    assert seen["overspeed_factor"] == 1.0 and seen["field"] is False and seen["interference_mm"] == 0.0
    import json
    assert json.loads(seen["geo"]) == {"magnet_fill_down": 0.9, "rotor_hole": 0.87}
