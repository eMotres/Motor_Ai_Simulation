"""``/api/thermal/field`` — the persistent "don't recompute" layer.

Owner, 2026-09-22: a repeat launch of the same solve parameters must load the
stored answer, not solve again, and a "Recompute" (``fresh=true``) must
always solve. Mirrors ``tests/test_mechanical_history.py`` — the solver
(``solve_thermal_field``) is monkeypatched out so these stay fast and
physics-free; the real solve is proven elsewhere (``tests/test_thermal_routes.py``).
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _fake_solve(*args, **kwargs):
    _fake_solve.calls += 1
    ambient = kwargs.get("ambient_temp", 25.0)
    return {
        "ok": True, "T_max": float(ambient) + 80.0, "T_min": float(ambient) + 5.0,
        "geometry_fingerprint": "fake-fp", "cached": False,
        "n_triangles": 10, "n_vertices": 10, "vertices": [], "triangles": [],
        "domain_per_tri": [], "temperature_per_node": [], "cooling": {},
    }


@pytest.fixture(autouse=True)
def _patch_solver(monkeypatch):
    from motor_ai_sim.routes import thermal as th

    _fake_solve.calls = 0
    monkeypatch.setattr(th, "solve_thermal_field", _fake_solve, raising=True)
    monkeypatch.setattr(th, "_FIELD_CACHE", {}, raising=True)
    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: __import__("tempfile").mktemp())
    yield


def _params(ambient: float) -> dict:
    return {"ambient_temp": ambient, "mesh_size_mm": 3.0, "min_size_mm": 0.5}


def test_first_call_solves_and_stores_history(client):
    r = client.get("/api/thermal/field", params=_params(25.01))
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    assert _fake_solve.calls == 1
    assert not out.get("served_from_history")
    assert out["history_key"]


def test_identical_repeat_loads_from_history_without_solving(client):
    p = _params(25.02)
    first = client.get("/api/thermal/field", params=p).json()
    assert _fake_solve.calls == 1

    from motor_ai_sim.routes import thermal as th
    th._FIELD_CACHE.clear()

    again = client.get("/api/thermal/field", params=p).json()
    assert _fake_solve.calls == 1, "a repeat of an identical request re-solved"
    assert again["served_from_history"] is True
    assert again["computed_at"]
    assert again["history_key"] == first["history_key"]
    assert again["T_max"] == first["T_max"]


def test_fresh_true_always_solves(client):
    p = _params(25.03)
    client.get("/api/thermal/field", params=p)
    from motor_ai_sim.routes import thermal as th
    th._FIELD_CACHE.clear()
    assert _fake_solve.calls == 1

    out = client.get("/api/thermal/field", params={**p, "fresh": True}).json()
    assert _fake_solve.calls == 2, "fresh=true must ignore the stored history"
    assert not out.get("served_from_history")


def test_a_changed_field_solves_again(client):
    p = _params(25.04)
    client.get("/api/thermal/field", params=p)
    from motor_ai_sim.routes import thermal as th
    th._FIELD_CACHE.clear()
    assert _fake_solve.calls == 1

    client.get("/api/thermal/field", params={**p, "h_conv": 25.0})
    assert _fake_solve.calls == 2


def test_history_list_endpoint_shows_the_newest_entry(client):
    r = client.get("/api/thermal/field", params=_params(25.05))
    key = r.json()["history_key"]

    out = client.get("/api/history", params={"kind": "thermal.field"})
    assert out.status_code == 200, out.text[:400]
    rows = out.json()["kinds"]["thermal.field"]
    assert any(row["key"] == key for row in rows)
