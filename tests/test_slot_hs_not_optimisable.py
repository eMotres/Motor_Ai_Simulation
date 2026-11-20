"""`slot_hs` (tooth-tip height) is not read by the geometry builder (owner 2026-09-14 / 2026-09-30), so it can never be swept or
optimised: a request naming it says so instead of silently dropping it, even when someone whitelists it."""
from __future__ import annotations

import inspect

import pytest

from motor_ai_sim.routes import optimization as O
from test_mesh_presets import _machine


@pytest.fixture
def machine(monkeypatch):
    g = _machine(12.0, 12, 10)
    cfg = {"geometry": g}
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    return cfg


def _scan(*names):
    return O.ScanRequest(variables=[{"name": n, "min": 1.0, "max": 1.2, "mode": "grid", "step": 0.1} for n in names])


def _descent(*names):
    return O.DescentRequest(variables=[{"name": n, "min": 1.0, "max": 1.2, "step": 0.05} for n in names])


def test_the_builder_does_not_read_slot_hs_so_the_refusal_is_true():
    import motor_ai_sim.cadquery_geometry as CG
    assert "slot_hs" not in inspect.getsource(CG), "slot_hs is read by the builder now: revisit this refusal and the whitelist"


def test_a_sweep_naming_slot_hs_drops_it_visibly(machine):
    plan = O.prepare_scan(_scan("tooth_width", "slot_hs"))
    assert [v["name"] for v in plan["variables"]] == ["tooth_width"]
    assert plan["dropped"] == [{"name": "slot_hs", "reason": "not read by the geometry builder (it cannot change a design)"}]
    assert plan["response"]["dropped"] == plan["dropped"]
    assert O.prepare_scan(_scan("slot_hs"))["variables"] == []                      # the route answers 400 when nothing is left


def test_whitelisting_it_does_not_make_it_a_variable(machine):
    machine["sweep_whitelist"] = ["tooth_width", "slot_hs"]
    plan = O.prepare_scan(_scan("tooth_width", "slot_hs"))
    assert [v["name"] for v in plan["variables"]] == ["tooth_width"] and plan["dropped"][0]["name"] == "slot_hs"
    d = O.prepare_descent(_descent("tooth_width", "slot_hs"))
    assert [s["name"] for s in d["var_specs"]] == ["tooth_width"]
    assert d["dropped"][0]["name"] == "slot_hs" and d["response"]["dropped"] == d["dropped"]


def test_a_request_without_dropped_variables_is_unchanged(machine):
    assert "dropped" not in O.prepare_scan(_scan("tooth_width"))["response"]
    assert "dropped" not in O.prepare_descent(_descent("tooth_width"))["response"]
