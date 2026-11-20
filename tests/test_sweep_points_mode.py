"""The Sweep "list of points" mode (TASK_optimiser_ripple_objective.md item 7): the candidate geometries are exactly the listed override
sets - two configurations at two currents are four runs, not a grid over every key that differs.  A request without `points` is the
sweep it always was."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import optimization as O
from test_mesh_presets import _machine

A = {"tooth_width": 4.0, "wire_width": 2.0, "slot_height": 7.0}
B = {"tooth_width": 4.4, "wire_width": 2.2, "slot_height": 7.6}
OPS = [{"current_a": 80.0, "gamma_deg": 0.0, "rpm": 3000.0}, {"current_a": 100.0, "gamma_deg": 0.0, "rpm": 3000.0}]


@pytest.fixture
def machine(monkeypatch):
    cfg = {"geometry": _machine(12.0, 12, 10)}
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    return cfg


def _req(**kw):
    return O.ScanRequest(operating_points=OPS, **kw)


def _geos(plan, max_geom=24):
    return O._enumerate_geometries(plan["variables"], max_geom, n_opt=4, seed=1)


def test_two_configurations_at_two_currents_are_four_runs(machine):
    plan = O.prepare_scan(_req(points=[A, B]))
    geos = _geos(plan)
    assert geos == [{k: round(v, 4) for k, v in A.items()}, {k: round(v, 4) for k, v in B.items()}]
    assert len(geos) * len(plan["ops"]) == 4                                # not 2**3 = 8 geometries
    assert {v["name"] for v in plan["variables"]} == set(A) and all(v["mode"] == "list" for v in plan["variables"])
    assert plan["response"]["max_geometries"] == 24


def test_points_combine_with_a_grid_variable(machine):
    req = _req(points=[A, B], variables=[{"name": "rotor_house_height", "min": 1.0, "max": 1.2, "mode": "sweep", "step": 0.1}])
    geos = _geos(O.prepare_scan(req))
    assert len(geos) == 2 * 3 and {tuple(sorted(g)) for g in geos} == {tuple(sorted(list(A) + ["rotor_house_height"]))}


def test_a_request_without_points_is_unchanged(machine):
    req = _req(variables=[{"name": "tooth_width", "min": 4.0, "max": 4.4, "mode": "sweep", "step": 0.2}])
    plan = O.prepare_scan(req)
    assert [v["mode"] for v in plan["variables"]] == ["sweep"] and "values" not in plan["variables"][0]
    assert _geos(plan) == [{"tooth_width": 4.0}, {"tooth_width": 4.2}, {"tooth_width": 4.4}]
    assert O._enumerate_geometries([], 24, n_opt=4, seed=1) == [{}]


@pytest.mark.parametrize("points,why", [
    ([A, {"tooth_width": 4.4}], "other keys"),
    ([{"tooth_width": 4.0, "slot_hs": 0.2}, {"tooth_width": 4.4, "slot_hs": 0.3}], "not read by the geometry builder"),
    ([{}, {}], "at least one geometry key"),
    ([A] * 3, "exceed max_geometries"),
])
def test_a_bad_list_refuses_the_request_and_says_why(machine, points, why):
    kw = {"max_geometries": 2} if why.startswith("exceed") else {}
    with pytest.raises(HTTPException) as e:
        O.prepare_scan(_req(points=points, **kw))
    assert e.value.status_code == 422 and why in e.value.detail


def test_a_key_outside_the_whitelist_refuses_the_request(machine):
    machine["sweep_whitelist"] = ["tooth_width"]
    with pytest.raises(HTTPException) as e:
        O.prepare_scan(_req(points=[{"tooth_width": 4.0, "wire_width": 2.0}, {"tooth_width": 4.4, "wire_width": 2.2}]))
    assert e.value.status_code == 422 and "wire_width" in e.value.detail and "sweep_whitelist" in e.value.detail
