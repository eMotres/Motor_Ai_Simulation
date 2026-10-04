"""Time-periodic eddy steady state (the default since 2026-09-30) on the
sandbox 30 mm 12s14p: it reproduces the march and passes its own acceptance
gate, the half period equals the full one, a failure at ANY stage is replaced
by a clean march (transactional), what it cannot serve is marched with a note,
parallel frames equal serial ones, the demag pre-pass iterates to a fixed
point in both methods, and Coulomb torque works on its frames.  Real solves
(the 12-step p2_eddy / p2_demag_eddy cases of the physics regression, a few
seconds to a minute each).

Environment: these solves need the geometry-driven mesher (``pip install -r
requirements-triangle.txt`` and ``SB_GEO_MESH=1``).  On the gmsh path the 30 mm
fixture's rotor mesh is not pole-pair periodic, TDM correctly refuses it and
marches with a note, and the tests that expect TDM fail by design."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation import time_periodic as TP
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band

from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)

pytestmark = pytest.mark.slow

LOSS_KEYS = ("P_cu_ac_W", "P_mag_eddy_W", "P_shaft_eddy_W")


def _cold():
    F._SB_WARM_CACHE.clear()
    try:
        p = F._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001
        pass


def _run(case: str = "p2_eddy", **over: Any) -> Dict[str, Any]:
    kw = dict(CASES[case]); kw.update(over)
    _cold()
    set_request_materials(OVERRIDE)
    try:
        return fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                          connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
        _cold()


def _mean(d, k):
    v = d.get(k) or []
    if isinstance(v, (int, float)):
        return float(v)
    return sum(v) / len(v) if v else 0.0


@pytest.fixture(scope="module")
def march():
    return _run(eddy_method="march")


@pytest.fixture(scope="module")
def tdm():
    return _run()                       # the default


@pytest.fixture(scope="module")
def march_demag():
    return _run("p2_demag_eddy", eddy_method="march")


@pytest.fixture(scope="module")
def tdm_demag():
    return _run("p2_demag_eddy")


def _same_as(d, ref, rel=1e-9):
    assert d["T_avg_Nm"] == pytest.approx(ref["T_avg_Nm"], rel=rel)
    # P_loss_total_avg_W is reported to the mW: one rounding step apart is equal
    assert d["P_loss_total_avg_W"] == pytest.approx(
        ref["P_loss_total_avg_W"], rel=rel, abs=(1.001e-3 if rel > 1e-9 else 0.0))
    for k in LOSS_KEYS:
        assert _mean(d, k) == pytest.approx(_mean(ref, k), rel=rel, abs=1e-12), k


def test_the_default_is_tdm_and_it_reproduces_the_march(march, tdm):
    assert tdm["eddy_method"] == "tdm" and tdm["eddy_method_note"] is None
    assert tdm["eddy_method_requested"] == "tdm"
    assert march["eddy_method"] == "march"
    info = tdm["tdm"]
    assert info["solve"]["converged"]
    assert tdm["eddy_warmup_frames"] == 0 and tdm["eddy_settled"]
    assert tdm["steady_state"] is True and tdm["steady_state_note"] is None
    assert tdm["qualified"] is True and tdm["tdm_experimental"] is False
    # the owner's accuracy terms, with a wide margin
    assert tdm["T_avg_Nm"] == pytest.approx(march["T_avg_Nm"], rel=1e-3)
    assert abs(tdm["T_ripple_pct"] - march["T_ripple_pct"]) < 0.05
    assert tdm["P_loss_total_avg_W"] == pytest.approx(march["P_loss_total_avg_W"],
                                                      rel=5e-3)
    for k in ("P_cu_ac_W", "P_mag_eddy_W"):
        assert _mean(tdm, k) == pytest.approx(_mean(march, k), rel=1e-2), k
    groups = set(info["gate"]["orbit"]["P"])
    assert {"cu", "mag", "shaft"} <= groups
    # THE CLOSURE MARCH (finding 1): one whole period from the orbit's start
    # state, frozen Br, closes on the orbit per group, both history levels
    cl = info["closure"]
    assert cl["ok"] is True and cl["frames"] == 12 and cl["frozen_br"] is True
    assert set(cl["state"]["levels"]) == {"frame 11", "frame 10"}
    for lev in cl["state"]["levels"].values():
        assert set(lev) == groups
        assert max(lev.values()) <= TP.CLOSURE_GATE["state_rel"]
    for w in cl["windows"].values():
        assert w["ok"] and w["T_mean_rel"] <= TP.CLOSURE_GATE["T_rel"]
        assert w["ripple_pp"] <= TP.CLOSURE_GATE["ripple_pp"]
    assert info["t"]["closure"] > 0.0
    # THE ORBIT-ERROR ESTIMATE (third / fourth reviews): a first-order estimate
    # with an empirical safeguard (not a proof) mapped to torque / ripple /
    # TOTAL loss (iron computed directly), under ESTIMATE_SAFETY of the owner's
    # terms
    ce = info["orbit_error_estimate"]
    assert "not a proof" in ce["kind"]
    assert ce["ok"] is True and 0.0 <= ce["rho_ritz"] <= ce["rho_eff"] < 1.0
    assert ce["ritz_converged"] is True and len(ce["ritz_history"]) >= 2
    assert ce["orbit_error_gmres"]["converged"] and ce["scale"] >= 1.0
    assert ce["error_norm_rho"] == pytest.approx(
        ce["defect_norm"] / (1.0 - ce["rho_eff"]))
    assert ce["error_norm"] >= max(ce["orbit_error_norm"], ce["error_norm_rho"]) * (1 - 1e-12)
    assert ce["closure_periods"] == 1 and ce["extra_closure_periods"] == 0
    # the iron loss computed directly on the orbit, the perturbed orbit and the
    # closure-march period, with the report's functional
    assert ce["P_fe_orbit_W"] > 0.0
    assert ce["dP_fe_W"] == pytest.approx(
        abs(ce["P_fe_perturbed_W"] - ce["P_fe_orbit_W"])
        + abs(ce["P_fe_closure_W"] - ce["P_fe_orbit_W"]))
    assert ce["P_fe_orbit_W"] == pytest.approx(_mean(tdm, "P_fe_W"), rel=0.05)
    lim = ce["limits"]
    assert lim["T_rel"] == pytest.approx(1e-3)
    assert lim["P_total_rel"] == pytest.approx(5e-3)
    assert ce["estimate_T_rel"] <= lim["T_rel"]
    assert ce["estimate_ripple_pp"] <= lim["ripple_pp"]
    assert ce["estimate_P_total_rel"] <= lim["P_total_rel"]
    assert "certify" not in info
    # THE REPORT GATE (finding 6): hard, per group, both levels, every period
    gate = info["gate"]
    assert gate["ok"] is True and "error" not in gate
    assert list(gate["windows"]) == ["frames 0-11"] == list(gate["state"])
    for w in gate["windows"].values():
        assert w["ok"] and w["T_mean_rel"] < 1e-3 and w["ripple_pp"] < 0.05
    st = gate["state"]["frames 0-11"]
    assert st["ok"] and set(st["levels"]) == {"frame 11", "frame 10"}
    assert max(max(v.values()) for v in st["levels"].values()) <= TP.REPORT_GATE["state_rel"]
    assert info["march_vs_orbit_last_frame"] == max(st["levels"]["frame 11"].values())
    # THE MAP CHECKS (finding 7) of the full period on the fixture: the inverse
    # is exact, the constrained spaces, operators and bodies match to round-off
    mc = info["map_check"]
    assert mc["ok"] is True and mc["failed"] == []
    assert mc["dev"]["inverse"] == 0.0
    assert mc["dev"]["constrained"] < 1e-12
    # the operators on rough random vectors: the mesher's pole-pair images match
    # within its node tolerance (measured 9.4e-4), a map defect is O(1)
    assert mc["dev"]["operators"] < TP.MAP_CHECK_RTOL["operators"]
    assert mc["dev"]["bodies"] < 1e-10
    assert mc["dev"]["source"] < TP.MAP_CHECK_RTOL["source"]
    assert mc["bodies"] > 0 and mc["bodies_moved"] > 0       # the magnets move
    # every Newton step's wrap solve met its forcing term or the bounded
    # inexact-Newton factor; the TRUE residual is recorded
    for rec in info["solve"]["newton"]:
        if "gmres_iterations" in rec:
            assert rec["gmres_converged"] or rec["gmres_accepted_inexact"]
            assert rec["gmres_rel_resid"] <= TP.GMRES_ACCEPT_FACTOR * rec["gmres_rtol"]
    assert TP.FrameFactor.open_handles() == 0


def test_half_period_is_taken_only_at_round_off(tdm, monkeypatch):
    """Finding 2: the opt-in half period (SB_TDM_HALF=1) is taken only when
    the source, operators, bodies and maps are anti-symmetric to ROUND-OFF.
    The fixture's are not (source 7.8e-6, operators 9.4e-4), so the run takes
    the full period and says so; it is the default run, unchanged."""
    assert tdm["tdm"]["period"] == "full"
    monkeypatch.setenv("SB_TDM_HALF", "1")
    half = _run()
    assert half["eddy_method"] == "tdm" and half["tdm"]["period"] == "full"
    assert half["eddy_method_note"].startswith(
        "tdm: half period refused (not anti-symmetric to round-off")
    hc = half["tdm"]["half_check"]
    assert hc["ok"] is False and "source" in hc["failed"]
    assert hc["tol"]["source"] == TP.HALF_SYMMETRY_RTOL == 1e-12
    assert hc["dev"]["source"] > TP.HALF_SYMMETRY_RTOL
    _same_as(half, tdm, rel=1e-12)


def test_a_forced_half_period_is_validated_on_both_halves(monkeypatch, tdm):
    """Finding 2, both halves: with the round-off rule bypassed (test hook
    only) the half period is taken; the closure march covers the WHOLE period
    and rejects it on the SECOND half (measured: shaft state 8 %, ripple
    0.13 pp, magnet loss 0.16 % off; the first half closes to 1e-5); the one
    retry takes the full period with the strict stop and is accepted."""
    monkeypatch.setattr(TP, "HALF_SYMMETRY_RTOL", 1e-2)
    monkeypatch.setenv("SB_TDM_HALF", "1")
    d = _run()
    assert d["eddy_method"] == "tdm" and d["tdm"]["period"] == "full"
    assert d["tdm"]["attempt"] == 2
    assert d["tdm"]["solve"]["stopped_by"] == "state_residual"
    prev = d["tdm"]["previous_attempts"]
    assert [a["stage"] for a in prev] == ["closure"]
    assert prev[0]["retry_full_period"] is True
    cl = prev[0]["tdm"]["closure"]
    assert prev[0]["tdm"]["period"] == "half_antiperiodic" and cl["frames"] == 12
    assert set(cl["windows"]) == {"frames 0-5", "frames 6-11"}
    assert cl["windows"]["frames 0-5"]["ok"] is True          # the first half closes
    assert cl["windows"]["frames 6-11"]["ok"] is False        # the second does not
    lv = cl["state"]["levels"]
    assert set(lv) == {"frame 5", "frame 4", "frame 11", "frame 10"}
    assert max(lv["frame 5"].values()) < TP.CLOSURE_GATE["state_rel"]
    assert max(lv["frame 11"].values()) > TP.CLOSURE_GATE["state_rel"]
    assert d["tdm"]["closure"]["ok"] is True
    _same_as(d, tdm, rel=1e-6)


@pytest.mark.parametrize("stage", ["setup", "static_start", "newton", "closure",
                                   "splice"])
def test_a_fault_at_any_stage_is_replaced_by_a_clean_march(stage, monkeypatch, march):
    monkeypatch.setenv("SB_TDM_FAULT", stage)
    d = _run()
    assert d["eddy_method"] == "march"
    assert d["eddy_method_requested"] == "tdm"
    assert d["eddy_method_note"].startswith("march: TDM failed (%s:" % stage)
    assert d["tdm"]["failed"] and d["tdm"]["attempts"][0]["stage"] == stage
    assert d["eddy_warmup_frames"] > 0          # a real march ran
    _same_as(d, march)
    assert TP.FrameFactor.open_handles() == 0


@pytest.mark.parametrize("stage", ["demag", "resolve"])
def test_a_fault_in_the_demag_stages_is_replaced_by_a_clean_march(
        stage, monkeypatch, march_demag):
    monkeypatch.setenv("SB_TDM_FAULT", stage)
    d = _run("p2_demag_eddy")
    assert d["eddy_method"] == "march"
    assert d["tdm"]["attempts"][0]["stage"] == stage
    _same_as(d, march_demag)
    # the Br history is the march's own, not the half-ratcheted one of the attempt
    s, r = d["demag_settle"], march_demag["demag_settle"]
    assert s["prepass_periods"] == r["prepass_periods"]
    for k in ("per_magnet_mean_max", "area_mean", "element_max"):
        assert s[k] == pytest.approx(r[k], rel=1e-9, abs=1e-15), k


def test_a_failed_gate_retries_strictly_then_marches(monkeypatch, march):
    """The gate rejects the reported period (injected): attempt 1 (owner's
    terms) → attempt 2 (strict 1e-7 state residual) → a clean march."""
    monkeypatch.setenv("SB_TDM_FAULT", "gate")
    d = _run()
    assert d["eddy_method"] == "march"
    att = d["tdm"]["attempts"]
    assert [a["stage"] for a in att] == ["report_gate", "report_gate"]
    assert att[0]["retry_residual"] is True and att[1]["retry_residual"] is False
    assert att[1]["tdm"]["attempt"] == 2
    _same_as(d, march)


def test_a_failed_closure_retries_strictly_then_marches(monkeypatch, march):
    """Finding 1: the closure march's verdict (injected failure) rejects the
    attempt: one retry with the strict 1e-7 state-residual stop on the full
    period, then a clean march."""
    monkeypatch.setenv("SB_TDM_FAULT", "closure_gate")
    d = _run()
    assert d["eddy_method"] == "march" and d["eddy_method_requested"] == "tdm"
    assert d["eddy_method_note"].startswith("march: TDM failed (closure:")
    att = d["tdm"]["attempts"]
    assert [a["stage"] for a in att] == ["closure", "closure"]
    assert att[0]["retry_residual"] is True and att[1]["retry_residual"] is False
    assert att[1]["tdm"]["attempt"] == 2
    assert att[1]["tdm"]["closure"]["injected"] is True
    assert att[1]["tdm"]["solve"]["stopped_by"] == "state_residual"
    _same_as(d, march)


def test_a_failed_error_estimate_retries_strictly_then_marches(monkeypatch, march):
    """An orbit whose estimated error is not below the safety fraction
    (injected) is rejected: one strict retry, then a march."""
    monkeypatch.setenv("SB_TDM_FAULT", "estimate_gate")
    d = _run()
    assert d["eddy_method"] == "march" and d["eddy_method_requested"] == "tdm"
    assert d["eddy_method_note"].startswith("march: TDM failed (error_estimate:")
    att = d["tdm"]["attempts"]
    assert [a["stage"] for a in att] == ["error_estimate", "error_estimate"]
    assert att[0]["retry_residual"] is True
    est = att[1]["tdm"]["orbit_error_estimate"]
    assert est["injected"] is True and est["rho_eff"] < 1.0
    _same_as(d, march)


def test_the_empirical_safeguard_marches_more_closure_periods(monkeypatch, tdm):
    """Fourth review: when the Ritz estimate is not trusted (here: the
    safeguard threshold forced to 0), further closure periods are marched and
    the contraction is OBSERVED; rho_eff = max(rho_ritz, rho_observed); the
    observed orbit error enters the estimate.  The orbit itself is unchanged."""
    monkeypatch.setattr(TP, "RHO_SAFEGUARD", 0.0)
    d = _run()
    assert d["eddy_method"] == "tdm"
    est = d["tdm"]["orbit_error_estimate"]
    assert est["ok"] is True and est["extra_closure_periods"] >= 1
    assert est["closure_periods"] == 1 + est["extra_closure_periods"]
    assert est["rho_observed"] is not None
    assert est["rho_eff"] == pytest.approx(max(est["rho_ritz"], est["rho_observed"]))
    assert len(est["deviation_increments"]) == est["closure_periods"]
    assert est["error_norm_observed"] is not None
    assert est["error_norm"] >= est["error_norm_observed"] * (1 - 1e-12)
    _same_as(d, tdm, rel=1e-12)


def test_an_unknown_settle_is_never_reported_steady():
    """NEW (third review): a run TDM refuses (voltage drive) whose settle is
    NOT measured (coil-only eddy: no settle gauge) used to read
    steady_state True through `is not False`.  Unknown stays unknown:
    eddy_settled None, steady_state False, and the note says why."""
    d = _run("p2_voltage_eddy")
    assert d["eddy_method"] == "march"
    assert d["eddy_method_note"].startswith("march: TDM not applicable (")
    assert d["eddy_settled"] is None
    assert d["steady_state"] is False
    assert "UNKNOWN" in d["steady_state_note"]


def test_an_unknown_pwm_settle_is_never_reported_steady():
    """The carrier-specific case (fourth review): a PWM-driven eddy run whose
    settle the gauge cannot judge (a mixed coarse/fine schedule, no
    carrier-commensurate settle periods) is UNKNOWN, not steady, and the note
    names the PWM gauge."""
    d = _run("p2_voltage_eddy", drive="pwm_voltage", v_bus=20.0,
             f_switch=6.0 * RPM * 7 / 60.0, n_steps_per_period=48)
    assert d["eddy_method"] == "march"
    assert "voltage / PWM drive" in d["eddy_method_note"]
    assert d["eddy_settled"] is None
    assert d["steady_state"] is False
    assert "UNKNOWN" in d["steady_state_note"] and "PWM" in d["steady_state_note"]


def test_a_rejected_attempt_is_not_kept_alive_during_the_retry(monkeypatch):
    """Finding 9: when the retry and the march start, no frame, factor or
    solver object of the rejected attempt is still referenced (its traceback
    frames are cleared and every link to them cut)."""
    import gc
    monkeypatch.setenv("SB_TDM_FAULT", "gate")
    real = F._fem_transient_sliding_band_once
    alive = []

    def spy(**kw):
        gc.collect()
        alive.append(sum(1 for o in gc.get_objects()
                         if isinstance(o, (TP.TdmFrame, TP.TimePeriodicEddy,
                                           TP.FrameFactor))))
        return real(**kw)
    monkeypatch.setattr(F, "_fem_transient_sliding_band_once", spy)
    d = _run()
    assert d["eddy_method"] == "march"
    assert [a["stage"] for a in d["tdm"]["attempts"]] == ["report_gate", "report_gate"]
    # (the d-axis calibration's own solve may come first: >= 3 entries)
    assert len(alive) >= 3 and not any(alive), alive


def test_a_failed_factorization_is_marched_loudly(monkeypatch, march):
    """Finding 10: a PARDISO failure inside TDM (injected into every frame
    factorisation, Cholesky and the LU fallback) is a failed attempt: marched,
    with the reason in the note, no handle left."""
    def boom(self, *a, **k):
        raise RuntimeError("PARDISO: injected factorization failure (error -4)")
    monkeypatch.setattr(TP.FrameFactor, "_factor_spd", boom)
    monkeypatch.setattr(TP.FrameFactor, "_factor_lu", boom)
    d = _run()
    assert d["eddy_method"] == "march" and d["eddy_method_requested"] == "tdm"
    assert "PARDISO: injected factorization failure" in d["eddy_method_note"]
    assert d["tdm"]["attempts"][0]["stage"] in ("static_start", "newton")
    _same_as(d, march)
    assert TP.FrameFactor.open_handles() == 0


def _corrupt_bc_sign(monkeypatch):
    """Wrap period_map_checks so the map it is handed carries a wrong BC sign
    on every other dof the map moves."""
    import numpy as np
    real = TP.period_map_checks

    def corrupted(**kw):
        w, wf = kw["wrap"], kw["wrapf"]
        v = np.random.default_rng(5).standard_normal(kw["P_from"].shape[0])
        moved = np.flatnonzero(np.asarray(w(v)) != v)[::2]

        def w2(x):
            o = np.array(w(x), float); o[moved] *= -1.0; return o

        def wf2(x):
            y = np.array(x, float, copy=True); y[moved] *= -1.0; return wf(y)
        return real(**dict(kw, wrap=w2, wrapf=wf2))
    monkeypatch.setattr(TP, "period_map_checks", corrupted)


def test_a_map_with_a_wrong_bc_sign_refuses_tdm_at_setup(monkeypatch, march):
    """Finding 7: a large map deviation fails the set-up — TDM is refused and
    the run marched, with the failing checks named."""
    _corrupt_bc_sign(monkeypatch)
    d = _run()
    assert d["eddy_method"] == "march"
    assert d["eddy_method_note"].startswith("march: TDM failed (setup:")
    assert "map fails its checks" in d["eddy_method_note"]
    mc = d["tdm"]["attempts"][0]["tdm"]["map_check"]
    # measured: constrained 0.21, bodies inf (a body split), source 1.36,
    # operators 0.015 — each far above its tolerance
    assert mc["ok"] is False
    assert {"constrained", "bodies", "source"} <= set(mc["failed"])
    _same_as(d, march)


def test_a_tdm_newton_failure_is_marched_loudly(monkeypatch, march):
    monkeypatch.setenv("SB_TDM_MAX_NEWTON", "0")     # no Newton step allowed
    d = _run()
    assert d["eddy_method"] == "march"
    assert d["eddy_method_requested"] == "tdm"
    assert d["eddy_method_note"].startswith("march: TDM failed (newton:")
    assert d["tdm"]["failed"]
    assert d["eddy_warmup_frames"] > 0
    _same_as(d, march)


def test_parallel_frames_equal_serial(tdm, monkeypatch):
    """Frames factorised by 3 threads: the same orbit as the serial solve to the
    Newton's own tolerance (the parallel default start differs: every frame's
    static field from frame 0's, not from its neighbour's), and repeated
    parallel runs agree to round-off (no race); no PARDISO handle survives."""
    monkeypatch.setenv("SB_TDM_WORKERS", "3")
    monkeypatch.setenv("SB_TDM_MKL_THREADS", "1")
    runs = [_run() for _ in range(2)]
    for d in runs:
        assert d["eddy_method"] == "tdm" and d["tdm"]["workers"] == 3
        assert d["tdm"]["gate"]["ok"] is True
        _same_as(d, tdm, rel=1e-6)
    _same_as(runs[1], runs[0], rel=1e-12)
    assert TP.FrameFactor.open_handles() == 0


def test_what_tdm_cannot_serve_is_marched_with_a_note():
    d = _run(frozen_nu=True)
    assert d["eddy_method"] == "march"
    assert d["eddy_method_note"].startswith("march: TDM not applicable (")
    assert "frozen_nu" in d["eddy_method_note"]
    assert d["tdm"] is None


def test_march_stays_selectable_by_environment(monkeypatch):
    monkeypatch.setenv("SB_EDDY_METHOD", "march")
    d = _run()
    assert d["eddy_method"] == "march" and d["eddy_method_note"] is None


def test_demag_pre_pass_iterates_to_a_fixed_point_in_both_methods(
        march_demag, tdm_demag):
    for d in (march_demag, tdm_demag):
        s = d["demag_settle"]
        assert s is not None and s["prepass_periods"] >= 1
        assert s["element_tol"] == TP.DEMAG_SETTLE_ELEMENT_TOL
        last = s["prepass"][-1]
        # iterated until a period moved Br by <= tol on the area mean, the
        # rotor-image history is complete and the observables stopped drifting
        # (third review), or the cap; one element still moving is a WARNING
        # (owner 2026-10-04)
        img = last["image_history"]
        assert img["cycle_periods"] == 7          # 12s14p sector: 7 pole-pair images
        stopped = (TP.demag_settled(last, s["tol"])
                   and img["complete"] and last["drift"]["ok"])
        assert stopped or s["prepass_periods"] == s["prepass_periods_max"]
        assert s["prepass_periods_max"] >= img["cycle_periods"]
        assert d["demag_settled"] is (s["moved_settled"]
                                      and s["image_history"]["complete"]
                                      and s["drift"]["ok"])
        assert s["moved_settled"] is TP.demag_settled(s, s["tol"])
        # the per-element number is a warning beside the verdict, never in it
        assert d["demag_warning"] == s["warning"]
        assert (s["warning"] is None) is (
            s["element_max"] <= s["element_tol"]
            and last["element_max"] <= s["element_tol"])
        assert d["steady_state"] is (d["eddy_settled"] is True and d["demag_settled"])
        # the drift is measured on the last two pre-pass periods' torque, by
        # the REPORTED torque method (fourth review)
        assert s["drift"].get("T_mean_rel") is not None
        assert s["drift"]["tol_T_rel"] == pytest.approx(1e-3)
        assert last["torque"]["torque_method"] == d["torque_method"] == \
            "coulomb_virtual_work"
        # the image history is complete ONLY after L pre-pass periods (fourth
        # review: no area-mean shortcut)
        for p_ in s["prepass"]:
            assert p_["image_history"]["complete"] is (
                p_["image_history"]["pre_pass_periods"] >= 7)
        if d["demag_settled"]:
            assert s["prepass_periods"] >= 7
        if d["demag_settled"]:
            assert d["steady_state_note"] is None
        else:
            assert d["steady_state_note"].startswith("demag NOT settled")
    assert tdm_demag["eddy_method"] == "tdm"
    # the same answer (the period counts may differ by one: the march's pre-pass
    # carries the eddy start-up transient of the Br collapse, TDM's does not)
    assert abs(tdm_demag["demag_settle"]["prepass_periods"]
               - march_demag["demag_settle"]["prepass_periods"]) <= 1
    assert tdm_demag["T_avg_Nm"] == pytest.approx(march_demag["T_avg_Nm"], rel=2e-3)


def test_the_demag_drift_uses_the_hybrid_torque_when_the_run_reports_it():
    """Fourth review: a run that reports the hybrid torque (flux-linkage mean,
    Maxwell AC) measures its demag drift with that method, not with the raw
    Maxwell series."""
    d = _run("p2_demag_eddy", torque_method="hybrid_maxwell_ac")
    assert d["eddy_method"] == "tdm"
    meth = d["torque_method"]
    assert meth != "coulomb_virtual_work"
    for p_ in d["demag_settle"]["prepass"]:
        assert p_["torque"]["torque_method"] == meth


def test_the_demag_shortcut_is_labelled_experimental(monkeypatch):
    d = _run("p2_demag_eddy", tdm_demag="shortcut")
    assert d["eddy_method"] == "tdm"
    assert d["eddy_method_note"].startswith("tdm: EXPERIMENTAL demag shortcut")
    assert d["tdm"]["demag"]["experimental"] is True
    assert d["tdm_experimental"] is True and d["qualified"] is False


def test_the_environment_cannot_select_the_shortcut(monkeypatch, tdm_demag):
    """Finding 11: SB_TDM_DEMAG=shortcut no longer reaches the solver — the
    full pre-pass runs, the result is qualified, and the note says the
    variable was ignored."""
    monkeypatch.setenv("SB_TDM_DEMAG", "shortcut")
    d = _run("p2_demag_eddy")
    assert d["eddy_method"] == "tdm" and d["qualified"] is True
    assert d["tdm_experimental"] is False
    assert d["tdm"]["demag"]["mode"] == "full"
    assert "SB_TDM_DEMAG=shortcut ignored" in d["eddy_method_note"]
    _same_as(d, tdm_demag, rel=1e-12)


def test_coulomb_torque_on_the_tdm_frames(march):
    c = _run(torque_method="coulomb")
    assert c["eddy_method"] == "tdm"
    ct = c["coulomb_torque"]
    assert ct["available"] and ct["layer_self_check"]["max_abs_diff_Nm"] is not None
    assert c["T_avg_Nm"] == pytest.approx(c["T_avg_coulomb_Nm"], rel=1e-12)
    assert c["T_avg_coulomb_Nm"] == pytest.approx(march["T_avg_coulomb_Nm"], rel=1e-3)
    mon = c["tdm"]["solve"]["newton"][-1]["monitor"]
    assert mon["torque_method"] == "coulomb_virtual_work"
    assert c["tdm"]["gate"]["orbit"]["torque_method"] == "coulomb_virtual_work"


def test_through_em_transient_eval(tdm):
    """The public solve entry (the gap rule's wrapper) carries the method."""
    kw = dict(CASES["p2_eddy"])
    _cold()
    set_request_materials(OVERRIDE)
    try:
        d = F.em_transient_eval(geo_override=dict(GEO_30MM), rpm=RPM,
                                connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
        _cold()
    assert d["eddy_method"] == "tdm" and d["tdm"]["gate"]["ok"]
