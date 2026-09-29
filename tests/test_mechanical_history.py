"""``/api/mechanical/rotor_stress`` — the persistent "don't recompute" layer.

Owner, 2026-09-22: a repeat launch of the SAME parameters must load the
stored answer, not solve again, and a "Recompute" (``fresh=true``) must
always solve.  This exercises the wiring added in
``routes/mechanical.py`` (``_ROTOR_STRESS_HISTORY``) with the FEM solver
itself monkeypatched out — a unit test of the caching contract, not of the
physics (that is every OTHER ``test_mechanical_*`` module).

The real ``_live_polys``/geometry build still runs (cheap — no FEM), against
the sandbox config ``tests/conftest.py`` already redirects
``MOTOR_AI_SIM_CONFIG`` to, so ``run_history``'s files land under that tmp
sandbox and never touch the real ``config/``.
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
    speed = args[2] if len(args) > 2 else kwargs.get("rpm", 0.0)
    case_mode = kwargs.get("case_mode", "three")
    case_name = f"{speed:,.0f} rpm" if case_mode == "single" else "rated"
    return {
        "cases": {
            case_name: {
                "rpm": speed,
                "rotor_od_growth_um": 1.23,
                "parts": {"rotor_core": {"safety_factor": 2.5}},
            }
        },
        "mesh": {"mesh_s": 0.001, "n_triangles": 10, "n_nodes": 10},
        "case_mode": case_mode,
    }


@pytest.fixture(autouse=True)
def _patch_solver(monkeypatch):
    from motor_ai_sim.routes import mechanical as mech
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    _fake_solve.calls = 0
    monkeypatch.setattr(rsm, "solve_rotor_stress", _fake_solve)
    # The in-memory session cache is process-wide module state; a run left by
    # another test module (or an earlier test here) must not turn a "must
    # solve" assertion below into an accidental in-memory hit.
    monkeypatch.setattr(rsm, "_CACHE", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    yield


def _params(rpm: float, **extra) -> dict:
    p = {"loads": "centrifugal", "rpm": rpm, "mesh_size_mm": 3.0, "order": 1,
         "lift_off_solves": 0, "cases": "single"}
    p.update(extra)
    return p


def test_first_call_solves_and_stores_history(client):
    r = client.get("/api/mechanical/rotor_stress", params=_params(9001))
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    assert _fake_solve.calls == 1
    assert out["cached"] is False
    assert not out.get("served_from_history")
    assert out["history_key"]


def test_identical_repeat_loads_from_history_without_solving(client):
    p = _params(9002)
    first = client.get("/api/mechanical/rotor_stress", params=p).json()
    assert _fake_solve.calls == 1

    # Clear the in-memory session cache only, so the SECOND call can only be
    # answered by the persistent history layer this task adds.
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    rsm._CACHE.clear()

    again = client.get("/api/mechanical/rotor_stress", params=p).json()
    assert _fake_solve.calls == 1, "a repeat of an identical request re-solved"
    assert again["served_from_history"] is True
    assert again["cached"] is True
    assert again["computed_at"]
    assert again["history_key"] == first["history_key"]
    assert again["cases"] == first["cases"]


def test_fresh_true_always_solves(client):
    p = _params(9003)
    client.get("/api/mechanical/rotor_stress", params=p)
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    rsm._CACHE.clear()
    assert _fake_solve.calls == 1

    out = client.get("/api/mechanical/rotor_stress",
                     params={**p, "fresh": True}).json()
    assert _fake_solve.calls == 2, "fresh=true must ignore the stored history"
    assert not out.get("served_from_history")
    assert out["cached"] is False


def test_a_changed_field_solves_again(client):
    p9004 = _params(9004)
    client.get("/api/mechanical/rotor_stress", params=p9004)
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm
    rsm._CACHE.clear()
    assert _fake_solve.calls == 1

    # A different rpm is a different machine question, not a repeat.
    client.get("/api/mechanical/rotor_stress", params=_params(9005))
    assert _fake_solve.calls == 2

    rsm._CACHE.clear()
    # Same rpm, different interference fit — also not a repeat.  (Not
    # overspeed_factor: cases="single" pins that to 1.0 before the key is
    # taken, so it would not be a fair test of "a changed field re-solves".)
    client.get("/api/mechanical/rotor_stress",
              params={**p9004, "interference_mm": 0.05})
    assert _fake_solve.calls == 3


def test_history_is_capped_at_ten_entries(client):
    from motor_ai_sim.routes import mechanical as mech

    for i in range(12):
        client.get("/api/mechanical/rotor_stress", params=_params(9100 + i))
    assert len(mech._ROTOR_STRESS_HISTORY) <= 10


def test_history_list_endpoint_shows_the_newest_entry(client):
    r = client.get("/api/mechanical/rotor_stress", params=_params(9200))
    key = r.json()["history_key"]

    from motor_ai_sim import run_history as RH
    rows = RH.history_for("mechanical.rotor_stress").list(limit=1)
    assert rows and rows[0]["key"] == key
    assert "9,200" in rows[0]["summary"] or "9200" in rows[0]["summary"]
