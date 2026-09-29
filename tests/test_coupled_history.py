"""``POST /api/coupled/run`` — the persistent "don't recompute this" layer.

Owner, first sentence of the 2026-09-22 ask: *"если я запускаю те же параметры
каплинга, он не считается, а подгружает уже рассчитанный вариант"*.  This
exercises the wiring added in ``routes/coupled.py`` (``_COUPLED_HISTORY`` /
``_coupled_history_key`` / ``_load_coupled_history_entry``) with the two heavy
halves (``_em_run``, ``_thermal_solve``) faked — the template
``tests/test_coupled_duty_cycle.py`` already established for testing the
LOOP's wiring cheaply. A real two-pass FEM+thermal solve proves the physics
elsewhere (``tests/test_coupled.py``); this file proves the caching contract.
"""
from __future__ import annotations

import pytest

from tests.test_coupled import COOLING as PANEL_COOLING
from tests.test_coupled import EM_BODY, client, sandbox  # noqa: F401 (fixtures)

#: One pass is enough to exercise the history wiring; the real convergence
#: math is somebody else's suite (tests/test_coupled.py).
LOOP_BODY = {**EM_BODY, "thermal_settings": PANEL_COOLING,
            "magnet_temp_c": 90.0, "mechanical": False,
            "cold_constants": False, "max_iter": 1}


def _fake_the_halves(monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    calls = {"em": 0, "th": 0}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        calls["em"] += 1
        return {"summary": {"P_loss_total_W": 700.0, "T_em_avg_Nm": 5.0,
                            "coil_temp_C": float(coil_temp_c), "rpm": 1000.0},
               "geo_fingerprint": "fake-fp", "computed_at": "2026-09-22T00:00:00+00:00"}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        calls["th"] += 1
        return {"ok": True,
               "components": {"winding": {"avg": float(coil_temp_c) + 10.0,
                                          "max": float(coil_temp_c) + 20.0},
                              "magnet": {"avg": 90.0, "max": 95.0}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    return calls


#: Belt and suspenders: ``tests/conftest.py::_clear_run_history`` already
#: empties every run_history kind before each test, but a distinct
#: ``I_phase_rms`` per test below keeps each test's OWN intent readable
#: without depending on fixture ordering.


def test_first_run_solves_and_second_identical_run_does_not(client, sandbox, monkeypatch):
    calls = _fake_the_halves(monkeypatch)
    body = {**LOOP_BODY, "run_id": "run-a", "I_phase_rms": 20.01}

    first = client.post("/api/coupled/run", json=body)
    assert first.status_code == 200, first.text[:600]
    f = first.json()
    assert calls["em"] == 1 and calls["th"] == 1
    assert not f.get("served_from_history")
    assert f["history_key"]

    # A different run_id (a fresh nonce, as every real launch sends) but
    # otherwise byte-identical parameters — must be a history hit.
    second = client.post("/api/coupled/run", json={**body, "run_id": "run-b"})
    assert second.status_code == 200, second.text[:600]
    s = second.json()
    assert calls["em"] == 1 and calls["th"] == 1, "an identical repeat re-solved"
    assert s["served_from_history"] is True
    assert s["computed_at"]      # the history entry's own storage time
    assert s["history_key"] == f["history_key"]
    # The whole coupling block, S1/limit line included, comes back verbatim.
    assert s["coupling"]["warning"] == f["coupling"]["warning"]
    assert s["coupling"]["coil_temp_c"] == f["coupling"]["coil_temp_c"]


def test_fresh_true_always_solves(client, sandbox, monkeypatch):
    calls = _fake_the_halves(monkeypatch)
    body = {**LOOP_BODY, "run_id": "run-c", "I_phase_rms": 20.02}
    client.post("/api/coupled/run", json=body)
    assert calls["em"] == 1

    out = client.post("/api/coupled/run",
                      json={**body, "run_id": "run-d", "fresh": True}).json()
    assert calls["em"] == 2, "fresh=true must ignore the stored history"
    assert not out.get("served_from_history")


def test_a_changed_operating_point_solves_again(client, sandbox, monkeypatch):
    calls = _fake_the_halves(monkeypatch)
    body = {**LOOP_BODY, "run_id": "run-e", "I_phase_rms": 20.03}
    client.post("/api/coupled/run", json=body)
    assert calls["em"] == 1

    client.post("/api/coupled/run",
               json={**body, "run_id": "run-f", "I_phase_rms": 20.13})
    assert calls["em"] == 2, "a different current must not be a history hit"


def test_record_false_never_reads_or_writes_history(client, sandbox, monkeypatch):
    calls = _fake_the_halves(monkeypatch)
    body = {**LOOP_BODY, "run_id": "run-g", "I_phase_rms": 20.04, "record": False}
    a = client.post("/api/coupled/run", json=body).json()
    b = client.post("/api/coupled/run", json={**body, "run_id": "run-h"}).json()
    assert calls["em"] == 2, "record:false must always solve, never cache"
    assert not a.get("served_from_history") and not b.get("served_from_history")
    assert "history_key" not in a and "history_key" not in b


def test_history_list_and_load_endpoints(client, sandbox, monkeypatch):
    calls = _fake_the_halves(monkeypatch)
    body = {**LOOP_BODY, "run_id": "run-i", "I_phase_rms": 20.05}
    solved = client.post("/api/coupled/run", json=body).json()
    key = solved["history_key"]

    listed = client.get("/api/history", params={"kind": "coupled.run"})
    assert listed.status_code == 200, listed.text[:400]
    rows = listed.json()["kinds"]["coupled.run"]
    assert any(r["key"] == key for r in rows)

    loaded = client.post(f"/api/history/{key}/load")
    assert loaded.status_code == 200, loaded.text[:400]
    body_out = loaded.json()
    assert body_out["served_from_history"] is True
    assert calls["em"] == 1, "loading from the History popover must not solve"
    assert body_out["coupling"]["coil_temp_c"] == solved["coupling"]["coil_temp_c"]
