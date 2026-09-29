"""``/api/history`` — the generic list/load/delete surface over run_history.

Exercised through ``mechanical.rotor_stress``, the one kind wired in so far
(``routes/mechanical.py``); the solver is monkeypatched exactly as in
``tests/test_mechanical_history.py`` so these stay fast and physics-free.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _fake_solve(*args, **kwargs):
    speed = args[2] if len(args) > 2 else kwargs.get("rpm", 0.0)
    return {"cases": {"rated": {"rpm": speed, "rotor_od_growth_um": 1.0}},
           "mesh": {"mesh_s": 0.001, "n_triangles": 10, "n_nodes": 10}}


@pytest.fixture(autouse=True)
def _patch_solver(monkeypatch):
    from motor_ai_sim.routes import mechanical as mech
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve)
    monkeypatch.setattr(rsm, "_CACHE", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    yield


def _params(rpm: float) -> dict:
    return {"loads": "centrifugal", "rpm": rpm, "mesh_size_mm": 3.0, "order": 1,
           "lift_off_solves": 0, "cases": "single"}


def test_known_kind_is_listed(client):
    from motor_ai_sim import run_history as RH
    assert "mechanical.rotor_stress" in RH.known_kinds()


def test_list_shows_a_solved_entry(client):
    r = client.get("/api/mechanical/rotor_stress", params=_params(9301))
    key = r.json()["history_key"]

    out = client.get("/api/history", params={"kind": "mechanical.rotor_stress"})
    assert out.status_code == 200, out.text[:400]
    rows = out.json()["kinds"]["mechanical.rotor_stress"]
    assert any(row["key"] == key for row in rows)
    row = next(row for row in rows if row["key"] == key)
    assert row["current"] is True
    assert "payload" not in row and "cases" not in row   # metadata only


def test_unknown_kind_is_404(client):
    r = client.get("/api/history", params={"kind": "not_a_real_kind"})
    assert r.status_code == 404


def test_load_replays_the_stored_answer(client):
    solved = client.get("/api/mechanical/rotor_stress", params=_params(9302)).json()
    key = solved["history_key"]

    loaded = client.post(f"/api/history/{key}/load")
    assert loaded.status_code == 200, loaded.text[:400]
    body = loaded.json()
    assert body["served_from_history"] is True
    assert body["cases"] == solved["cases"]

    # The mechanical route's own bookkeeping ran too (the registered loader):
    last = client.get("/api/mechanical/last").json()
    assert last["rotor_stress"]["result"]["cases"] == solved["cases"]


def test_load_unknown_key_is_404(client):
    r = client.post("/api/history/deadbeefdeadbeef/load")
    assert r.status_code == 404


def test_delete_then_load_is_404(client):
    solved = client.get("/api/mechanical/rotor_stress", params=_params(9303)).json()
    key = solved["history_key"]

    d = client.delete(f"/api/history/{key}")
    assert d.status_code == 200 and d.json()["deleted"] is True

    d2 = client.delete(f"/api/history/{key}")
    assert d2.status_code == 404

    r = client.post(f"/api/history/{key}/load")
    assert r.status_code == 404
