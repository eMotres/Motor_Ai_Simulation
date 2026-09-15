"""An IMPULSE duty may be run once — never iterated to a settled temperature.

The coupled loop's whole method is to run EM → thermal → EM until the winding
and the magnet stop moving.  On an S2 (one pull) or an S3 (ED % of a cycle) duty
that fixed point still exists arithmetically, and it means nothing physically:
it is the temperature the machine would reach if the pull never ended.  On the
Ø85 robot joint's peak point (687 W of loss, ΣmC_p ≈ 163 J/K) that is hundreds
of kelvin above anything the two-second pull actually sees, and the loop would
hand it over with a "converged" stamp on it.

So the pre-flight refuses it BEFORE the first transient, by name
(``impulse_duty_not_steady``), and says what to do instead.  One pass
(``max_iter = 1``) stays legal and is not a loop: it is the loss map at a coil
temperature the caller states, which is exactly what the duty-cycle model
calibrates its conductances on.

NOTHING SOLVES HERE.  The refusal happens before any solver is called, and the
legal case is asserted on the pre-flight itself — this file must never cost a
finite-element run.
"""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app

client = TestClient(app)

DIE = "TESTDIE 85"
CFG = "L13"
DUTY = "peak 200C"

#: 25 % of a 60 s cycle, resting unpowered — the robot-joint case.
S3 = {"kind": "S3", "ed_pct": 25, "cycle_s": 60.0, "rest_duty": None}
S2 = {"kind": "S2", "t_on_s": 2.0}

#: The smallest body the pre-flight accepts: a real cycle, the current drive,
#: both conducting solves on.  No geometry, no mesh — nothing here solves.
BODY = {"n_steps_per_period": 4, "drive": "current",
        "eddy": True, "rotor_eddy": True}


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    """A throwaway die whose one duty carries the S3 cycle, with the family
    context pointed at it — the path the web takes: the block is saved on the
    duty, and the coupled run finds it there without being told."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import family as fam

    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "geometry": {"num_slots": 24,
                                                   "num_poles": 28,
                                                   "stator_diameter": 85.0},
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "duties": [{"name": DUTY, "mode": "motor", "current_arms": 45.9,
                    "rpm": 1000.0, "gamma_deg": 2.0, "duty_cycle": dict(S3)}],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    monkeypatch.setattr(dr, "active_context", lambda: (DIE, CFG, DUTY))
    return root


# ── the refusal ──────────────────────────────────────────────────────────────

def test_a_loop_over_an_s3_duty_is_refused_before_any_solve(catalog):
    r = client.post("/api/coupled/run", json={**BODY, "max_iter": 6})
    assert r.status_code == 422, r.text[:400]
    d = r.json()["detail"]
    assert d["error_code"] == "impulse_duty_not_steady"
    assert "max_iter" in [p["field"] for p in d["invalid_parameters"]]
    assert DUTY in d["error"] and "S3" in d["error"]
    assert "25" in d["error"] and "60" in d["error"]      # the cycle it read
    assert "max_iter = 1" in d["error"]                   # and what to do


def test_the_block_may_also_come_in_the_body(catalog):
    """An API caller that states the cycle is refused on the same grounds as a
    catalogued one — and the body wins, because it is the explicit statement."""
    from motor_ai_sim.routes import coupled as cp

    with pytest.raises(Exception) as exc:
        cp._preflight({**BODY, "duty_cycle": dict(S2), "duty": "pull"},
                      max_iter=6)
    d = exc.value.detail
    assert d["error_code"] == "impulse_duty_not_steady"
    assert "S2" in d["error"] and "pull" in d["error"] and "2.0 s" in d["error"]


def test_one_pass_over_the_same_duty_passes_the_preflight(catalog):
    """max_iter = 1 is not a loop: it is the calibration solve the duty-cycle
    model asks for, and refusing it would leave an impulse duty with no thermal
    answer at all."""
    from motor_ai_sim.routes import coupled as cp

    cp._preflight({**BODY}, max_iter=1)                   # must not raise


def test_a_continuous_duty_still_loops(catalog):
    from motor_ai_sim.routes import coupled as cp

    for blk in ({"kind": "S1"},
                {"kind": "segments",
                 "segments": [{"duty": DUTY, "t_s": 2.0}]}):
        cp._preflight({**BODY, "duty_cycle": dict(blk)}, max_iter=6)


def test_a_duty_with_no_cycle_at_all_still_loops(catalog, monkeypatch):
    """Every duty saved before the cycle existed — and every bare Run with no
    catalog context.  Absence is the continuous duty they were always taken to
    be, never a refusal."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import coupled as cp

    monkeypatch.setattr(dr, "active_context", lambda: (DIE, CFG, "rated"))
    cp._preflight({**BODY}, max_iter=6)          # a duty with no block…
    monkeypatch.setattr(dr, "active_context", lambda: None)
    cp._preflight({**BODY}, max_iter=6)          # …and no context at all


def test_the_preflight_without_a_budget_checks_nothing_about_the_cycle(catalog):
    """``max_iter=None`` means "not resolved" — the check is skipped rather than
    guessed at, so an old caller of the pre-flight cannot be refused by it."""
    from motor_ai_sim.routes import coupled as cp

    cp._preflight({**BODY, "duty_cycle": dict(S3)})


# ── reading the cycle ────────────────────────────────────────────────────────

def test_the_cycle_is_read_off_the_duty_the_context_names(catalog):
    from motor_ai_sim.routes import coupled as cp

    blk, name = cp._duty_cycle_of({})
    assert name == DUTY and blk["kind"] == "S3" and blk["ed_pct"] == 25


def test_an_unreadable_catalog_is_no_cycle_and_no_crash(tmp_path, monkeypatch):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import family as fam

    monkeypatch.setattr(fam, "_DIES_DIR", tmp_path / "nothing here")
    monkeypatch.setattr(dr, "active_context", lambda: (DIE, CFG, DUTY))
    assert cp._duty_cycle_of({}) == (None, "")
