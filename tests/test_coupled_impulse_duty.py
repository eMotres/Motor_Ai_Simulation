"""An IMPULSE duty is solved for its REGIME — it is no longer refused.

THE REFUSAL THAT WENT AWAY (user, 2026-09-16: *"каплинг на цикле S3 подбирает
скважность для того чтобы можно было влезть в лимиты"*).  Until today this file
pinned a 422: the coupled loop's method is to iterate the electromagnetic run and
the thermal solve until the winding and the magnet stop moving, and on a duty
that runs 25 % of a 60 s cycle that fixed point is the temperature the machine
would reach if the pull never ended — on the Ø85 robot joint, hundreds of kelvin
above anything the cycle sees.  The reasoning was right and the conclusion was
not: the answer to a question asked wrongly is to ask it properly, which is what
``coupled_duty_cycle`` does inside every pass — find the duty ratio the machine
can hold, and feed back the temperatures AT it.

So what this file pins now is:

  * an S2/S3 duty passes the pre-flight at any iteration budget — the loop runs;
  * a cycle that is MALFORMED is still refused before a solve, by name, because
    everything a block can be wrong about structurally is knowable without one;
  * the cycle is still read off the duty the catalog context names, and an
    unreadable catalog is still no cycle and no crash;
  * an S1 duty, an explicit segment list, a duty with no block at all and a
    machine with no context at all are all untouched.

NOTHING SOLVES HERE.  Every assertion is on the pre-flight and on the two readers
beside it; this file must never cost a finite-element run.
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


@pytest.fixture(autouse=True)
def duty_cycle_on(monkeypatch):
    """THE FEATURE FLAG, on (owner 2026-09-17).

    Everything in this file is about what the coupled loop does with an impulse
    duty, and since 2026-09-17 it does any of it only when
    ``DUTY_CYCLE_ENABLED`` says so — the owner asked for the duty cycle to be
    out of the way "for now", and the switch that puts it back is this variable.
    So every test here runs with the feature ON and pins it unchanged; what the
    loop does with the feature OFF is pinned beside the gate, below.
    """
    monkeypatch.setenv("DUTY_CYCLE_ENABLED", "1")


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


# ── the loop runs ────────────────────────────────────────────────────────────

def test_an_s3_duty_is_no_longer_refused_by_the_preflight(catalog):
    """THE change of 2026-09-16, at the place that used to stop it."""
    from motor_ai_sim.routes import coupled as cp

    cp._preflight({**BODY}, max_iter=6)                     # must not raise
    cp._preflight({**BODY, "duty_cycle": dict(S3)}, max_iter=6)
    cp._preflight({**BODY, "duty_cycle": dict(S2), "duty": "pull"}, max_iter=6)


def test_one_pass_over_the_same_duty_still_passes(catalog):
    from motor_ai_sim.routes import coupled as cp

    cp._preflight({**BODY}, max_iter=1)                     # must not raise


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


# ── …and a malformed cycle is still refused, before any solve ────────────────

@pytest.mark.parametrize("blk, code", [
    ({"kind": "S3", "ed_pct": 25, "cycle_s": 0}, "duty_cycle_bad_cycle"),
    ({"kind": "S3", "ed_pct": 0, "cycle_s": 60}, "duty_cycle_bad_ed"),
    ({"kind": "S3", "ed_pct": 25, "cycle_s": 60,
      "rest_duty": "no such duty"}, "duty_cycle_unknown_duty"),
    ({"kind": "S2", "t_on_s": 0}, "duty_cycle_bad_s2"),
])
def test_a_malformed_cycle_is_named_before_a_solve_is_spent(catalog, blk, code):
    """The structural half of the old refusal survives, and it is the half that
    was always right: a cycle time of zero or a segment naming a duty this
    configuration does not have would otherwise surface as a DutyCycleError on
    the far side of a two-minute transient."""
    from motor_ai_sim.routes import coupled as cp

    with pytest.raises(Exception) as exc:
        cp._preflight({**BODY, "duty_cycle": dict(blk)}, max_iter=6)
    d = exc.value.detail
    assert d["error_code"] == code, d
    assert "duty_cycle" in [p["field"] for p in d["invalid_parameters"]]


def test_the_refusal_names_the_duty_and_says_what_is_wrong(catalog):
    from motor_ai_sim.routes import coupled as cp

    with pytest.raises(Exception) as exc:
        cp._preflight({**BODY, "duty_cycle": {"kind": "S3", "cycle_s": 60,
                                              "ed_pct": 25,
                                              "rest_duty": "hold"}},
                      max_iter=6)
    assert DUTY in exc.value.detail["error"]
    assert "hold" in exc.value.detail["error"]


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


# ── which runs get a regime at all ───────────────────────────────────────────

def test_only_an_impulse_duty_gets_a_cycle_context(catalog):
    """The gate: S2 and S3 are regimes, everything else is the settled point the
    loop has always solved."""
    from motor_ai_sim.routes import coupled as cp

    assert cp._cycle_inputs({}, {})["duty"] == DUTY
    for blk in ({"kind": "S1"}, {"kind": "segments",
                                 "segments": [{"duty": DUTY, "t_s": 2.0}]}):
        assert cp._cycle_inputs({"duty_cycle": dict(blk)}, {}) is None


def test_with_the_feature_off_no_duty_gets_one(catalog, monkeypatch):
    """THE REMOVAL of 2026-09-17, at the gate.  With ``DUTY_CYCLE_ENABLED``
    unset the loop is the standard loop for every duty — the S3 block stored on
    this one is read as the continuous point it was read as before cycles
    existed, and a malformed block refuses nothing, because nothing is going to
    solve it."""
    from motor_ai_sim.routes import coupled as cp

    monkeypatch.delenv("DUTY_CYCLE_ENABLED", raising=False)
    assert cp._cycle_inputs({}, {}) is None
    assert cp._cycle_inputs({"duty_cycle": dict(S3)}, {}) is None
    assert cp._cycle_inputs({"duty_cycle": dict(S2), "duty": "pull"}, {}) is None
    # the block is still THERE — hidden, never deleted
    blk, name = cp._duty_cycle_of({})
    assert name == DUTY and blk["kind"] == "S3"
    # …and the pre-flight lets a cycle nobody will solve through
    cp._preflight({**BODY, "duty_cycle": {"kind": "S3", "ed_pct": 25,
                                          "cycle_s": 0}}, max_iter=6)


def test_an_errand_never_gets_one(catalog):
    """``record: false`` runs somebody ELSE's operating point while this duty is
    loaded (the duty-cycle editor's calibration hatch).  A regime found for the
    wrong point would be worse than no regime, so the loop does not look for
    one — asserted on the route, which is where the suppression is read."""
    from motor_ai_sim import run_recording as rr
    from motor_ai_sim.routes import coupled as cp
    import inspect

    src = inspect.getsource(cp._run)
    assert "_rr.suppressed() else _cycle_inputs" in src
    tok = rr.suppress()
    try:
        assert rr.suppressed() is True
    finally:
        rr.restore(tok)
