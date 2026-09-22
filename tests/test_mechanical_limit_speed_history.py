"""``/api/mechanical/limit_speed`` — the persistent "don't recompute" layer.

Owner, 2026-09-22: a repeat launch of the same search parameters must load
the stored answer, not search again. The search is the most expensive of the
four kinds wired this session (``max_solves`` FEM solves), so this is the
one most worth caching. ``solve_rotor_stress`` is monkeypatched to the same
synthetic SF(rpm) curve ``tests/test_mechanical_limit_speed.py`` already
uses — no real FEM.
"""
from __future__ import annotations

import pytest

from tests.test_mechanical_limit_speed import _fake_solve_rotor_stress


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    from motor_ai_sim.routes import mechanical as mech
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    calls = {"n": 0}
    fake = _fake_solve_rotor_stress(20_000.0)

    def _counting(*a, **k):
        calls["n"] += 1
        return fake(*a, **k)

    monkeypatch.setattr(rsm, "solve_rotor_stress", _counting)
    monkeypatch.setattr(rsm, "_CACHE", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    return calls


def _params(rpm: float, **extra) -> dict:
    p = {"rpm": rpm, "loads": "centrifugal", "torque_nm": 0}
    p.update(extra)
    return p


def test_first_call_searches_and_stores_history(client, _patch):
    r = client.post("/api/mechanical/limit_speed", params=_params(9401))
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    assert _patch["n"] > 0
    assert not out.get("served_from_history")
    assert out["history_key"]


def test_identical_repeat_loads_from_history_without_searching(client, _patch):
    p = _params(9402)
    first = client.post("/api/mechanical/limit_speed", params=p).json()
    n_first = _patch["n"]
    assert n_first > 0

    again = client.post("/api/mechanical/limit_speed", params=p).json()
    assert _patch["n"] == n_first, "a repeat of an identical search re-solved"
    assert again["served_from_history"] is True
    assert again["computed_at"]
    assert again["history_key"] == first["history_key"]
    assert again["limit_speed"]["rpm_sf1"] == pytest.approx(
        first["limit_speed"]["rpm_sf1"])


def test_fresh_true_always_searches_again(client, _patch):
    p = _params(9403)
    client.post("/api/mechanical/limit_speed", params=p)
    n_first = _patch["n"]

    out = client.post("/api/mechanical/limit_speed",
                      params={**p, "fresh": True}).json()
    assert _patch["n"] > n_first, "fresh=true must ignore the stored history"
    assert not out.get("served_from_history")


def test_a_changed_target_sf_searches_again(client, _patch):
    p = _params(9404)
    client.post("/api/mechanical/limit_speed", params=p)
    n_first = _patch["n"]

    client.post("/api/mechanical/limit_speed",
               params={**p, "target_sf": 1.5})
    assert _patch["n"] > n_first, (
        "a different search target must not be a history hit")


def test_history_list_endpoint_shows_the_newest_entry(client, _patch):
    r = client.post("/api/mechanical/limit_speed", params=_params(9405))
    key = r.json()["history_key"]

    out = client.get("/api/history", params={"kind": "mechanical.limit_speed"})
    assert out.status_code == 200, out.text[:400]
    rows = out.json()["kinds"]["mechanical.limit_speed"]
    assert any(row["key"] == key for row in rows)
