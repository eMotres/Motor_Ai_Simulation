"""Stop must stop the coupled run — every phase (owner, production 2026-09-25).

A drive=inverter coupled run on the server was in its EM pass when Stop was
pressed: both cancel endpoints answered 200, the browser's request closed, and
the solve went on at ~970 % CPU until the container was restarted.  The frame
march DID answer the cancel — with ``HTTPException(499)`` — but the loop's
later phases catch ``HTTPException`` to keep a solved state when a pass is
REFUSED, and read the 499 as a refusal: the pass at the limit, the S1
verification and the 20 °C constants each swallowed it and the loop went on
(and, with nothing after them, filed the half-finished run as its answer).
The long loops with no progress callback of their own — the thermal solve's
phases, the contact iterations, the limit-speed search — never looked at the
cancel flag at all.

The transient is faked here as a frame march that checks the cancel registry
exactly as the real one does (``simulation._sb_progress``), so what is pinned
is the loop's handling, not the solver.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.test_coupled_limited_state import LOOP_BODY, _ttl_block

FRAMES = 50


def _rig(monkeypatch, *, cancel_on_call: int, ttl=None):
    """Fake transient (a cancellable frame march), thermal, ttl and record
    seams.  The cancel is pressed at frame 5 of the ``cancel_on_call``-th EM
    run; ``seen`` says what ran after it."""
    from motor_ai_sim import jobs as J
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import simulation as sim

    seen = {"em": 0, "frames_after_cancel": 0, "thermal": 0, "remembered": 0,
            "cancelled_at": None}

    def fake_transient(run_id=None, coil_temp_c=None, magnet_temp_c=None,
                       drive=None, I_phase_rms=None, fresh=None, ledger=None):
        seen["em"] += 1
        for i in range(FRAMES):
            if seen["em"] == cancel_on_call and i == 5:
                J.cancel_run(run_id, requester="", is_admin=True)
                seen["cancelled_at"] = seen["em"]
            if run_id and J.is_cancelled(run_id):     # the real march's check
                raise HTTPException(status_code=499, detail="simulation stopped")
            if seen["cancelled_at"] is not None:
                seen["frames_after_cancel"] += 1
        return {"summary": {"P_loss_total_W": 700.0, "T_em_avg_Nm": 5.0,
                            "rpm": 1000.0,
                            "coil_temp_C": float(coil_temp_c or 0.0)},
                "I_phase_rms_solved_A": 20.0}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        seen["thermal"] += 1
        return {"ok": True,
                "components": {"winding": {"avg": 400.0, "max": 430.0},
                               "magnet": {"avg": 93.0, "max": 95.0}}}

    monkeypatch.setattr(sim, "get_fem_transient", fake_transient, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_ttl_step",
                        lambda *a, **k: None if ttl is None else dict(ttl),
                        raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)

    def _rem(out, **k):
        seen["remembered"] += 1
    monkeypatch.setattr(cp, "_remember_last", _rem, raising=True)
    return seen


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _post(client, rid, **body):
    return client.post("/api/coupled/run",
                       json={**LOOP_BODY, "max_iter": 3, "tol_k": 1.0,
                             "cold_constants": False, "run_id": rid,
                             **body})


def _stopped(r, seen, cp):
    assert r.status_code == 499, r.text[:400]
    # the march stopped at the frame it was cancelled on — not one more frame
    assert seen["frames_after_cancel"] == 0
    # no answer was filed as this machine's result
    assert seen["remembered"] == 0
    # …and the loop let go of the machine
    assert not cp._LOCK.locked()


def test_cancel_in_a_later_loop_pass_stops_the_loop(client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    seen = _rig(monkeypatch, cancel_on_call=2)
    r = _post(client, "cancel-t1")
    _stopped(r, seen, cp)
    assert seen["em"] == 2 and seen["thermal"] == 1   # nothing after the Stop


def test_cancel_in_the_pass_at_the_limit_is_not_a_refusal(client, monkeypatch):
    """The production case's shape: a 499 in a later phase used to be read as
    'refused', the loop went on and — with nothing after it — returned 200
    with a half-finished record."""
    from motor_ai_sim.routes import coupled as cp

    seen = _rig(monkeypatch, cancel_on_call=2, ttl=_ttl_block())
    r = _post(client, "cancel-t2", solve_to="limits")
    _stopped(r, seen, cp)
    assert seen["em"] == 2


def test_cancel_in_the_20c_constants_pass_stops(client, monkeypatch):
    from motor_ai_sim.routes import coupled as cp

    seen = _rig(monkeypatch, cancel_on_call=99)       # never inside the loop
    # the loop converges on pass 2 (the fake map is 400 °C both times); the
    # third EM run is the 20 °C constants pass — cancel THAT one.
    seen_calls = {"n": 0}
    from motor_ai_sim.routes import simulation as sim
    inner = sim.get_fem_transient

    def counting(**kw):
        seen_calls["n"] += 1
        if seen_calls["n"] == 3:
            from motor_ai_sim import jobs as J
            J.cancel_run(kw.get("run_id"), requester="", is_admin=True)
            seen["cancelled_at"] = 3
        return inner(**kw)
    import inspect
    counting.__signature__ = inspect.signature(inner)
    monkeypatch.setattr(sim, "get_fem_transient", counting, raising=True)
    r = _post(client, "cancel-t3", cold_constants=True)
    _stopped(r, seen, cp)


def test_check_cancelled_raises_inside_a_cancelled_job_only():
    from motor_ai_sim import jobs as J

    J.check_cancelled()                       # outside any job: a no-op
    with J.admit("test.cancel", run_id="cancel-t4"):
        J.check_cancelled()                   # not cancelled: a no-op
        J.cancel_run("cancel-t4", requester="", is_admin=True)
        with pytest.raises(J.JobCancelled):
            J.check_cancelled()


def test_a_thermal_solve_inside_a_cancelled_job_stops_at_its_first_phase():
    from motor_ai_sim import jobs as J
    from motor_ai_sim.routes import thermal as th

    with pytest.raises(J.JobCancelled):
        with J.admit("test.cancel", run_id="cancel-t5"):
            J.cancel_run("cancel-t5", requester="", is_admin=True)
            th.solve_thermal_field()
