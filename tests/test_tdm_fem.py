"""Time-periodic eddy steady state (the default since 2026-09-30) on the
sandbox 30 mm 12s14p: it reproduces the march and passes its own acceptance
gate, the half period equals the full one, a failure at ANY stage is replaced
by a clean march (transactional), what it cannot serve is marched with a note,
parallel frames equal serial ones, the demag pre-pass iterates to a fixed
point in both methods, and Coulomb torque works on its frames.  Real solves
(the 12-step p2_eddy / p2_demag_eddy cases of the physics regression, a few
seconds to a minute each).

Environment: these solves need the geometry-driven mesher on a CDT backend
whose rotor mesh is pole-pair periodic.  Netgen is the default backend
(``netgen-mesher==6.2.2607``, Linux/WSL2); gmsh and Triangle are periodic too
(tests/test_mesh_periodicity.py).  A non-periodic rotor mesh is a REFUSAL
(``TdmMeshNotPeriodic``), never a silent march."""
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
    assert info["maps_inverse_dev"] is not None     # recorded (diagnostic)
    assert tdm["eddy_warmup_frames"] == 0 and tdm["eddy_settled"]
    assert tdm["steady_state"] is True
    # the owner's accuracy terms, with a wide margin
    assert tdm["T_avg_Nm"] == pytest.approx(march["T_avg_Nm"], rel=1e-3)
    assert abs(tdm["T_ripple_pct"] - march["T_ripple_pct"]) < 0.05
    assert tdm["P_loss_total_avg_W"] == pytest.approx(march["P_loss_total_avg_W"],
                                                      rel=5e-3)
    for k in ("P_cu_ac_W", "P_mag_eddy_W"):
        assert _mean(tdm, k) == pytest.approx(_mean(march, k), rel=1e-2), k
    # the reported period marched from the orbit stayed on it …
    assert info["march_vs_orbit_last_frame"] < 1e-3
    # … and passed the HARD acceptance gate on its first and last window
    gate = info["gate"]
    assert gate["ok"] is True
    assert "first" in gate["windows"]
    for w in gate["windows"].values():
        assert w["ok"] and w["T_mean_rel"] < 1e-3 and w["ripple_pp"] < 0.05
    # every Newton step's wrap GMRES converged (true residual recorded)
    for rec in info["solve"]["newton"]:
        if "gmres_iterations" in rec:
            assert rec["gmres_converged"] is True
    assert TP.FrameFactor.open_handles() == 0


def test_half_period_equals_full_period(tdm, monkeypatch):
    """The FULL period is the default; the opt-in anti-periodic half period
    (SB_TDM_HALF=1, on a mesh whose magnet source passes the tight symmetry
    test) gives the same reported period."""
    assert tdm["tdm"]["period"] == "full"
    monkeypatch.setenv("SB_TDM_HALF", "1")
    half = _run()
    assert half["eddy_method"] == "tdm", half.get("eddy_method_note")
    assert half["tdm"]["period"] == "half_antiperiodic"
    assert half["tdm"]["magnet_source_half_asymmetry"] < 1e-5
    assert half["T_avg_Nm"] == pytest.approx(tdm["T_avg_Nm"], rel=1e-5)
    assert abs(half["T_ripple_pct"] - tdm["T_ripple_pct"]) < 1e-3
    assert half["P_loss_total_avg_W"] == pytest.approx(tdm["P_loss_total_avg_W"],
                                                       rel=1e-5)
    # measured: copper AC +1e-4, magnet +9e-6, but the SHAFT +0.56 % — the
    # discrete orbit is not exactly anti-periodic in the shaft, which is why
    # the full period is the default
    for k, rel in (("P_cu_ac_W", 5e-4), ("P_mag_eddy_W", 1e-4), ("P_shaft_eddy_W", 2e-2)):
        assert _mean(half, k) == pytest.approx(_mean(tdm, k), rel=rel, abs=1e-9), k


@pytest.mark.parametrize("stage", ["setup", "static_start", "newton", "splice"])
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
        last = s["prepass"][-1]["per_magnet_mean_max"]
        # iterated until a period moved Br by <= tol, or the cap
        assert last <= s["tol"] or s["prepass_periods"] == s["prepass_periods_max"]
        assert d["demag_settled"] is (s["per_magnet_mean_max"] <= s["tol"])
        assert d["steady_state"] is (d["eddy_settled"] and d["demag_settled"])
    assert tdm_demag["eddy_method"] == "tdm"
    # the same answer (the period counts may differ by one: the march's pre-pass
    # carries the eddy start-up transient of the Br collapse, TDM's does not)
    assert abs(tdm_demag["demag_settle"]["prepass_periods"]
               - march_demag["demag_settle"]["prepass_periods"]) <= 1
    assert tdm_demag["T_avg_Nm"] == pytest.approx(march_demag["T_avg_Nm"], rel=2e-3)


def test_the_demag_shortcut_is_labelled_experimental(monkeypatch):
    d = _run("p2_demag_eddy", tdm_demag="shortcut")
    assert d["eddy_method"] == "tdm"
    assert d["eddy_method_note"].startswith("tdm: EXPERIMENTAL demag shortcut")
    assert d["tdm"]["demag"]["experimental"] is True


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
