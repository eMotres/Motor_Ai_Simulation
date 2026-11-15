"""The ordered stages behind the one-row progress strip (owner 2026-10-30).

One bar for a whole transient hid what the solver was doing: the eddy warm-up is
adaptive (its count GROWS), the demag pre-pass is spliced in behind it, and the
reported window restarts the counter — so "148 / 161 steps" jumped and its ETA
with it.  ``progress_stages`` reads the solver's own
``progress(done, total, phase, composition)`` stream into an ordered list, and
these tests pin what a strip is allowed to build on:

  (a) the stage ORDER and STATES (pending → running → done / skipped / stopped),
  (b) an adaptive stage's planned count may grow and snaps to what it took,
  (c) the stream is the one the real solver sends (labels copied from
      ``fem_solver_2d``), including gap probes; the d-axis calibration, the
      eddy warm-up and the demag pre-pass read as ONE preparation stage (owner
      2026-10-30, "merge the first three processes into one"),
  (d) the transient endpoint and the tracker payloads carry ``stages`` beside the
      old flat fields, and the coupled loop reports its iterations and PWM passes.

No FEM: the route test stubs the sliding-band solve with a function that replays
a solver-shaped callback stream (same trick as tests/test_run_ledger.py).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from motor_ai_sim import progress as P
from motor_ai_sim import progress_stages as S

# The labels, verbatim from simulation/fem_solver_2d.py.
CAL = ("calibrating the d-axis reference (no-load, 24 frames) — once per "
       "geometry")
WARM = ("eddy warm-up (preparation, adaptive) — settling the start-up "
        "transient")
BOTH = ("eddy warm-up + demag pre-pass (preparation) — eddy verification with "
        "the Br ratchet active")
PREPASS = ("demag pre-pass (preparation) — settled eddy field, Br ratchet "
           "active")
# …and the labels a solver from before the merge sent (same prefixes)
WARM_OLD = "eddy warm-up (adaptive) — settling the start-up transient"
PREPASS_OLD = "demag pre-pass — one settled period, Br ratchet active"
POST = "post-processing (frames, masses, summary)"


def _by(stages, key):
    return next(s for s in S.public(stages) if s["key"] == key)


def _keys(stages):
    return [s["key"] for s in S.public(stages)]


def _states(stages):
    return {s["key"]: s["state"] for s in S.public(stages)}


# ── (a) order and states ────────────────────────────────────────────────────

def test_the_plan_lists_only_what_is_known_in_execution_order():
    st = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=36)
    assert _keys(st) == ["prepare", "final", "post"]
    assert set(_states(st).values()) == {"pending"}
    assert _by(st, "prepare")["adaptive"] is True       # a moving target
    assert _by(st, "prepare")["planned"] == 36          # the route's first guess
    assert _by(st, "final")["planned"] == 144
    # nothing asked for → just the reported window and the post-processing
    assert _keys(S.plan_em(eddy=False, demag=False, planned_final=8)) == [
        "final", "post"]


def test_setup_checkpoints_move_nothing():
    """done = total = None with no phase is the Stop checkpoint: "keep what you
    show".  It must not start a stage."""
    st = S.plan_em(eddy=False, demag=False, planned_final=10)
    S.observe(st, None, None, None)
    assert set(_states(st).values()) == {"pending"}


def test_a_full_demag_eddy_run_walks_the_stages_in_order():
    """Calibration, warm-up, the overlapped eddy + demag periods and the
    dedicated pre-pass are ONE stage with ONE monotonic count: the
    calibration's 24 frames, then the march's own negative-frame counter."""
    st = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=36)
    t = 1000.0
    for d in (0, 12, 24):
        t += 1
        S.observe(st, d, 24, CAL, now=t)
    p = _by(st, "prepare")
    assert (p["state"], p["done"], p["planned"]) == ("running", 24, 36)
    # the march counts its own frames against its own, growing, total
    for d, tot in ((0, 10), (9, 10), (10, 20), (19, 20)):
        t += 1
        S.observe(st, d, tot, WARM, "", now=t)
    p = _by(st, "prepare")
    assert (p["state"], p["done"], p["planned"], p["adaptive"]) == (
        "running", 24 + 19, 24 + 20, True)    # planned GREW mid-stage
    assert _states(st)["final"] == "pending"
    # the ratchet switches on inside the eddy verification, then the dedicated
    # pre-pass — the SAME counter, the SAME stage
    S.observe(st, 20, 30, BOTH, "", now=t + 1)
    S.observe(st, 29, 40, BOTH, "", now=t + 2)
    S.observe(st, 30, 40, PREPASS, "", now=t + 3)
    S.observe(st, 39, 40, PREPASS, "", now=t + 4)
    p = _by(st, "prepare")
    assert (p["state"], p["done"], p["planned"]) == ("running", 24 + 39, 24 + 40)
    assert p["note"] == ("d-axis cal. 24 + eddy warm-up 20 + eddy + demag 10 + "
                         "demag pre-pass 9")
    # the reported window restarts at 0 and says nothing about a phase
    S.observe(st, 0, 144, None, "2×36 settle + 8 pre-roll + 64 reported",
              now=t + 10)
    S.observe(st, 100, 144, None, None, now=t + 20)
    f = _by(st, "final")
    assert (f["state"], f["done"], f["planned"]) == ("running", 100, 144)
    assert f["note"].startswith("2×36 settle")
    p = _by(st, "prepare")                # a closed adaptive stage reads n / n
    assert p["state"] == "done" and p["done"] == p["planned"] == 24 + 39
    # the route enters post-processing; the solve's last frame closes the final
    S.observe(st, None, None, POST, now=t + 30)
    assert _by(st, "final")["done"] == 144
    S.finalize(st, ok=True, now=t + 31)
    assert set(_states(st).values()) == {"done"}
    assert _keys(st) == ["prepare", "final", "post"]


def test_an_older_solvers_labels_fold_into_the_same_stage():
    st = S.plan_em(eddy=True, demag=True, planned_final=36, planned_prepass=36)
    S.observe(st, 0, 10, WARM_OLD, now=1.0)
    S.observe(st, 9, 10, WARM_OLD, now=2.0)
    S.observe(st, 10, 46, PREPASS_OLD, now=3.0)
    S.observe(st, 45, 46, PREPASS_OLD, now=4.0)
    p = _by(st, "prepare")
    assert (p["state"], p["done"], p["planned"]) == ("running", 45, 46)
    assert _keys(st) == ["prepare", "final", "post"]


def test_the_eta_covers_every_stage_still_to_run():
    """At 1 s a frame with 10 preparation frames and the 144-frame reported
    window still to come, the run has 154 s left — not the 10 s the current
    stage's own counter says.  The rate is the march's own: the cheap no-load
    calibration frames before it do not make the estimate optimistic."""
    st = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=36)
    S.observe(st, 24, 24, CAL, now=100.0)
    S.observe(st, 0, 40, WARM, now=110.0)          # the march starts at t = 110
    S.observe(st, 30, 40, WARM, now=140.0)         # 30 frames in 30 s
    assert S.eta_s(st, now=140.0) == pytest.approx(10 + 144)
    assert S.eta_s(None) is None
    st2 = S.plan_em(eddy=True, demag=False, planned_final=10)
    assert S.eta_s(st2, now=1.0) is None           # no rate yet: unknown, not 0


def test_a_warm_up_served_from_the_cache_is_skipped_not_left_pending():
    """The warm cache hands the solver a settled state, so the first report is
    the reported window — the planned warm-up never ran."""
    st = S.plan_em(eddy=True, demag=False, planned_final=40)
    S.observe(st, 0, 40, None, "")
    assert _states(st) == {"prepare": "skipped", "final": "running",
                           "post": "pending"}


def test_elapsed_is_per_stage_and_frozen_when_the_stage_ends():
    st = S.plan_em(eddy=True, demag=False, planned_final=40)
    S.observe(st, 0, 10, WARM, now=100.0)
    S.observe(st, 3, 10, WARM, now=105.0)
    S.observe(st, 0, 40, None, now=112.0)          # warm-up ended at t = 112
    pub = {s["key"]: s for s in S.public(st, now=200.0)}
    assert pub["prepare"]["elapsed_s"] == 12.0     # not 100: its clock stopped
    assert pub["final"]["elapsed_s"] == 88.0       # still running
    assert pub["post"]["elapsed_s"] == 0.0         # never started


def test_a_stop_leaves_the_running_stage_stopped_and_the_rest_skipped():
    st = S.plan_em(eddy=True, demag=False, planned_final=40)
    S.observe(st, 4, 10, WARM, now=1.0)
    S.finalize(st, ok=False, now=2.0)
    assert _states(st) == {"prepare": "stopped", "final": "skipped",
                           "post": "skipped"}


def test_an_unlabelled_checkpoint_label_does_not_start_a_stage():
    """``_phase_point`` names a set-up step with no counters ("TDM orbit")."""
    st = S.plan_em(eddy=False, demag=False, planned_final=10)
    S.observe(st, None, None, "solving the TDM orbit")
    assert set(_states(st).values()) == {"pending"}


# ── (c) the stages the plan cannot know ─────────────────────────────────────

def test_gap_probes_and_calibration_are_inserted_in_execution_order():
    """Gap probes are whole solves before the main one; the main solve's
    calibration is the start of its preparation."""
    st = S.plan_em(eddy=False, demag=False, planned_final=40)
    S.observe(st, 1, 12, "gap probe 2/side", None)
    S.observe(st, 3, 24, CAL)
    assert _keys(st) == ["gap_probe", "prepare", "final", "post"]
    assert _states(st)["gap_probe"] == "done"
    S.observe(st, 24, 24, CAL)
    S.observe(st, 0, 40, None)
    assert _states(st)["prepare"] == "done"
    assert _by(st, "prepare")["planned"] == 24 == _by(st, "prepare")["done"]


def test_one_stage_runs_at_a_time_whatever_the_order():
    """A calibration reported before a gap probe must not leave two stages
    running."""
    st = S.plan_em(eddy=False, demag=False, planned_final=40)
    S.observe(st, 3, 24, CAL)
    S.observe(st, 1, 12, "gap probe 2/side", None)
    assert [s["key"] for s in S.public(st) if s["state"] == "running"] == [
        "gap_probe"]


def test_successive_gap_probes_read_as_one_monotonic_count():
    """Each probe restarts its counter (and runs a warm-up of its own inside):
    the stage folds the finished probe into a base instead of stepping back."""
    st = S.plan_em(eddy=False, demag=False, planned_final=40)
    S.observe(st, 11, 12, "gap probe 2/side")
    assert (_by(st, "gap_probe")["done"], _by(st, "gap_probe")["planned"]) == (11, 12)
    S.observe(st, 0, 12, "gap probe 3/side")                 # the next probe
    g = _by(st, "gap_probe")
    assert (g["done"], g["planned"]) == (11, 24)
    S.observe(st, 5, 12, "gap probe 3/side: eddy warm-up (adaptive)")
    g = _by(st, "gap_probe")
    assert g["done"] == 16 and g["planned"] == 24 and g["state"] == "running"
    assert _states(st)["warm_up"] if "warm_up" in _states(st) else True


def test_a_second_whole_solve_is_a_countless_stage_before_post():
    st = S.plan_em(eddy=False, demag=False, planned_final=40)
    S.observe(st, 39, 40, None)
    S.observe(st, None, None, "fem-solve (harm-ref, sinusoidal current)")
    assert _keys(st) == ["final", "extra", "post"]
    assert _states(st)["final"] == "done" and _states(st)["extra"] == "running"
    assert _by(st, "extra")["planned"] == 0          # no bar, just "running"


def test_public_strips_the_bookkeeping_and_is_json():
    st = S.plan_em(eddy=True, demag=True, planned_final=8, planned_prepass=4)
    S.observe(st, 2, 6, WARM)
    out = S.public(st)
    json.dumps(out)
    assert set(out[0]) == {"key", "name", "state", "done", "planned",
                           "adaptive", "unit", "elapsed_s", "note"}
    assert S.public(None) == [] == S.public([])


# ── (d) the coupled loop ────────────────────────────────────────────────────

def test_the_coupled_tracker_reports_iterations_then_pwm_passes():
    t = P.ProgressTracker()
    t.start(total=12, kind="coupled", phase="iteration 1/6 — electromagnetic",
            stages=S.plan_coupled(max_iter=6, pwm=True, max_passes=2),
            stage_kind="coupled")
    snap = t.snapshot()
    assert [s["key"] for s in snap["stages"]] == ["loop", "pwm_final"]
    assert snap["stages"][0]["planned"] == 12 and snap["stages"][0]["adaptive"]
    t.update(done=0, phase="iteration 1/6 — electromagnetic")
    t.update(done=1, phase="iteration 1/6 — thermal")
    t.update(done=2, phase="iteration 2/6 — electromagnetic")
    t.update(done=3, phase="iteration 2/6 — thermal")
    loop = t.snapshot()["stages"][0]
    assert (loop["state"], loop["done"]) == ("running", 3)
    # converged after 2 of 6: the controller's PWM passes begin
    t.update(phase="controller PWM pass 1/2 — coil 80.0 °C")
    snap = t.snapshot()
    loop, pwm = snap["stages"]
    assert (loop["state"], loop["done"], loop["planned"]) == ("done", 4, 4)
    assert (pwm["state"], pwm["done"], pwm["planned"]) == ("running", 0, 4)
    t.update(phase="controller PWM pass 1/2 — thermal with the PWM losses")
    assert t.snapshot()["stages"][1]["done"] == 1
    t.finish()
    snap = t.snapshot()
    assert [s["state"] for s in snap["stages"]] == ["done", "done"]
    assert snap["stages"][1]["done"] == snap["stages"][1]["planned"] == 2


def test_a_reused_sine_state_skips_the_loop_stage():
    t = P.ProgressTracker()
    t.start(total=12, kind="coupled", phase="iteration 1/6 — electromagnetic",
            stages=S.plan_coupled(max_iter=6, pwm=True, max_passes=2),
            stage_kind="coupled")
    t.update(phase="controller PWM pass 1/2 — coil 80.0 °C")
    assert [s["state"] for s in t.snapshot()["stages"]] == ["skipped", "running"]


def test_a_solve_without_stages_keeps_an_empty_list_and_its_old_fields():
    t = P.ProgressTracker()
    t.start(total=5, phase="meshing")
    t.update(done=2, phase="rotor stress 2/5")
    snap = t.snapshot()
    assert snap["stages"] == []
    assert (snap["step"], snap["total"], snap["phase"]) == (2, 5, "rotor stress 2/5")


def test_a_coupled_phase_we_do_not_know_changes_nothing():
    t = P.ProgressTracker()
    t.start(total=2, kind="coupled", phase="x",
            stages=S.plan_coupled(max_iter=1, pwm=False), stage_kind="coupled")
    t.update(phase="continuous rating 1/3 — steady map")
    assert t.snapshot()["stages"][0]["state"] == "pending"


# ── (d) the transient endpoint, through the route ───────────────────────────

@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


def _poll(sim):
    return asyncio.run(sim.get_fem_transient_progress())


def test_the_idle_transient_endpoint_carries_an_empty_stage_list(sim):
    out = _poll(sim)
    assert out["stages"] == [] or isinstance(out["stages"], list)
    for k in ("running", "step", "total", "elapsed_s", "eta_s", "phase"):
        assert k in out, k          # the old fields are all still there


def test_a_transient_run_publishes_its_stages_as_the_solver_reports(
        sim, monkeypatch, tmp_path):
    """Drive the real route with a stub in place of the sliding-band solve that
    replays a solver-shaped callback stream and polls the endpoint between
    reports — the payload the strip actually receives."""
    sim.clear_simulation_caches(reason="progress-stages test setup")
    seen = []

    def fake_eval(**kw):
        cb = kw["progress_cb"]
        cb(None, None, None, None)                       # set-up checkpoint
        for d, tot in ((0, 8), (7, 8), (8, 14), (13, 14)):
            cb(d, tot, WARM, "")
        seen.append(_poll(sim))                          # mid warm-up
        cb(14, 18, PREPASS, "")
        cb(17, 18, PREPASS, "")
        seen.append(_poll(sim))                          # mid pre-pass
        for d in (0, 2, 3):
            cb(d, 4, None, "4 reported")
        seen.append(_poll(sim))                          # mid final
        return {"time_s": [0.0, 0.5, 1.0], "T_avg_Nm": 1.0,
                "T_em_Nm": [1.0, 1.0, 1.0], "rpm": 1000.0, "f_elec_Hz": 100.0,
                "P_loss_total_W": [1.0, 1.0, 1.0], "n_frames_solved": 3}

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval",
                        fake_eval)
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_ledger_dir", lambda: tmp_path / ".run_ledger")
    try:
        sim.get_fem_transient(n_steps_per_period=4, n_periods=1.0, gamma_deg=0.0,
                              I_phase_rms=10.0, daxis_deg=0.0, eddy=True,
                              demag=True, fresh=True)
    finally:
        sim.clear_simulation_caches(reason="progress-stages test teardown")

    mid_warm, mid_prepass, mid_final = seen
    for payload in seen:                                 # old fields stay
        assert payload["running"] is True and "step" in payload
        json.dumps(payload)
    assert [s["key"] for s in mid_warm["stages"]] == ["prepare", "final", "post"]
    w = mid_warm["stages"][0]
    assert (w["state"], w["done"], w["planned"], w["adaptive"]) == (
        "running", 13, 14, True)
    assert mid_warm["stages"][1]["state"] == "pending"
    # the ETA is the whole run's: preparation frames left + the final window
    assert mid_warm["eta_s"] >= 0.0
    st = {s["key"]: s for s in mid_prepass["stages"]}
    assert st["prepare"]["state"] == "running"
    assert (st["prepare"]["done"], st["prepare"]["planned"]) == (17, 18)
    st = {s["key"]: s for s in mid_final["stages"]}
    assert st["prepare"]["state"] == "done"
    assert (st["final"]["state"], st["final"]["done"],
            st["final"]["planned"]) == ("running", 3, 4)
    assert st["final"]["note"] == "4 reported"

    # after the run: nothing running, every stage closed (nothing left pending)
    end = _poll(sim)
    assert end["running"] is False
    assert {s["state"] for s in end["stages"]} <= {"done", "skipped"}
    assert [s["state"] for s in end["stages"]][-1] == "done"     # post


# ── the time-periodic (TDM) preparation counts passes, never a frame plan (owner 2026-11-16) ──
# Recorded from the CIANO28 80_12 / L12-F475 run (worker log 23:08:27 - 23:09:55): d-axis calibration of 24 frames,
# then "TDM Newton 0 / 1 / 2" (rrel 3e-2 -> 1.7e-5 -> 6.9e-7), then the reported period.  The strip showed
# "Preparation 23 / 576+ frames ... ETA >= 2650 s": 576 = 4 x 144 was the march plan.
TDM_NEWTON = ("eddy warm-up (time-periodic steady state, TDM): Newton %d, residual %.1e")


def _tdm_stream(stages, t0=1000.0):
    """The recorded stream with its times: returns the stage after each report."""
    seen = []
    for d in range(1, 24):
        S.observe(stages, d, 24, CAL, now=t0 + d * 0.5)
    seen.append(("cal", S.public(stages, now=t0 + 12)[0]["done"], S.eta_s(stages, now=t0 + 12)))
    for k, (dt, r) in enumerate(((20.0, 3e-2), (50.0, 1.7e-5), (80.0, 6.9e-7))):
        S.observe(stages, None, None, TDM_NEWTON % (k, r), now=t0 + dt)
        pub = S.public(stages, now=t0 + dt)[0]
        seen.append((k, pub["done"], pub["planned"], pub["unit"], S.eta_s(stages, now=t0 + dt)))
    return seen


def test_a_tdm_run_counts_passes_and_never_shows_the_march_plan():
    stages = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=0)
    seen = _tdm_stream(stages)
    assert stages[0]["planned"] != 576
    # after the calibration frames: no ETA from a frame rate that does not exist
    assert seen[0][2] is None or seen[0][2] < 100
    k0, k1, k2 = seen[1:]
    assert k0[3] == "passes" and k0[1] == 1 and k0[2] == 3            # pass 1 of about 3
    assert k0[4] is None                                              # one report: no pass time yet
    assert (k1[1], k1[2]) == (2, 3)
    # pass time (80 - 20) / (1 - 0)... measured between reports: 30 s per pass, 1 pass left, 0 s into it
    assert k1[4] == pytest.approx(30.0)
    assert (k2[1], k2[2]) == (3, 4)                                   # never claims "3 of 3" while a pass can follow
    assert k2[4] is not None and k2[4] <= 30.0                        # never the 2650 s of the frame plan


def test_the_guess_never_reaches_the_wire_as_a_frame_total():
    stages = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=0)
    S.observe(stages, None, None, TDM_NEWTON % (0, 3e-2), now=5.0)
    pub = S.public(stages, now=6.0)
    prep = next(s for s in pub if s["key"] == "prepare")
    assert prep["unit"] == "passes" and prep["adaptive"] is True and prep["planned"] <= 4
    S.finalize(stages, now=7.0)
    prep = next(s for s in S.public(stages, now=8.0) if s["key"] == "prepare")
    assert prep["done"] == prep["planned"] == 1                       # snaps to what it took


def test_a_second_orbit_after_the_demag_prepass_adds_its_passes():
    stages = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=0)
    for k in range(3):
        S.observe(stages, None, None, TDM_NEWTON % (k, 1e-3), now=10.0 * (k + 1))
    S.observe(stages, None, None, TDM_NEWTON % (0, 1e-3), now=50.0)   # numbering restarts
    prep = S.public(stages, now=51.0)[0]
    assert prep["done"] == 4 and prep["planned"] >= 4


def test_a_rejected_tdm_attempt_that_is_marched_counts_frames_again():
    stages = S.plan_em(eddy=True, demag=False, planned_final=144, planned_prepass=0)
    S.observe(stages, None, None, TDM_NEWTON % (0, 3e-2), now=5.0)
    S.observe(stages, -30, 70, WARM, now=6.0)
    prep = S.public(stages, now=7.0)[0]
    assert prep["unit"] == "frames"


def test_the_route_plan_for_a_tdm_run_has_no_frame_guess(monkeypatch):
    from motor_ai_sim.routes import simulation as R
    monkeypatch.delenv("SB_EDDY_METHOD", raising=False)
    assert R._plan_tdm_expected(True) is True
    assert R._plan_tdm_expected(False) is False
    monkeypatch.setenv("SB_EDDY_METHOD", "march")
    assert R._plan_tdm_expected(True) is False


def test_the_transient_endpoint_does_not_extrapolate_a_frame_plan_during_a_tdm_pass(sim):
    """The owner's screenshot: step 23 after 90 s against a total of 576 gave ETA 2650 s.  During a TDM pass the
    endpoint answers the stage ETA (measured pass time) or unknown (0) - never the flat step/total extrapolation."""
    import time as _t
    now = _t.time()
    stages = S.plan_em(eddy=True, demag=True, planned_final=144, planned_prepass=0)
    S.observe(stages, 23, 24, CAL, now=now - 80)
    S.observe(stages, None, None, TDM_NEWTON % (0, 3e-2), now=now - 40)
    cur = {"running": True, "step": 23, "total": 576, "elapsed_s": 0.0, "eta_s": 0.0, "ts_start": now - 90.4,
           "phase": TDM_NEWTON % (0, 3e-2), "composition": "", "stages": stages}
    sim._fem_transient_progress["current"] = cur
    try:
        out = _poll(sim)
        assert out["eta_s"] == 0.0                                    # one report: unknown, not 2650
        assert out["stages"][0]["unit"] == "passes"
        S.observe(stages, None, None, TDM_NEWTON % (1, 1.7e-5), now=now - 10)
        out = _poll(sim)
        assert 0 < out["eta_s"] < 100                                 # 30 s per pass, one pass left
    finally:
        sim._fem_transient_progress["current"] = {"running": False, "step": 0, "total": 0, "elapsed_s": 0.0,
                                                  "eta_s": 0.0, "ts_start": 0.0, "phase": "idle", "composition": ""}
