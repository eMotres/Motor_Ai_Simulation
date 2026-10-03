"""Every steady-state eddy run gets TDM by default, whichever door it comes
through (Codex review 2026-09-30, item "integration tests for every entry
point").

The doors: the EM tab (routes.simulation.get_fem_transient), the coupled loop
(routes.coupled -> get_fem_transient), sweeps / the optimizer (refine_proc ->
kernel "solver.em_transient" -> modules.solvers -> get_fem_transient), passports
(passport -> get_fem_transient), agent drafts / MCP simulate (mcp_app ->
agent_designs -> get_fem_transient), and the solve pool's child process
(solve_pool -> solve_pool_child -> fem_solver_2d.em_transient_eval).

  * the ROUTE is run for real up to the solver (a stub in place of the FEM,
    as tests/test_run_ledger.py does): it passes no eddy_method, so the
    solver's default decides, and the method, its note and the steady-state
    verdict the solver returns reach the summary the UI and reports read;
  * every other door is checked on the source: it goes through
    get_fem_transient, and nothing in src/ pins a method or the shortcut;
  * the solve pool's CHILD inherits SB_EDDY_METHOD / SB_TDM_* and resolves
    them itself (a real subprocess).

The real solves (TDM result, fallbacks to a clean march, notes) are in
tests/test_tdm_fem.py.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from motor_ai_sim.simulation import fem_solver_2d as FS

SRC = Path(FS.__file__).resolve().parents[1]      # src/motor_ai_sim


@pytest.fixture
def sim():
    from motor_ai_sim.routes import simulation as s
    return s


@pytest.fixture
def spy(monkeypatch, sim, tmp_path):
    sim.clear_simulation_caches(reason="tdm entry-point test setup")
    monkeypatch.setattr(sim, "_ledger_dir", lambda: tmp_path / ".run_ledger")
    calls = []
    answer = {}

    def fake_eval(**kw):
        calls.append(kw)
        out = {"time_s": [0.0, 0.5, 1.0], "T_avg_Nm": 1.234,
               "T_em_Nm": [1.2, 1.25, 1.23], "rpm": 1000.0, "f_elec_Hz": 100.0,
               "P_loss_total_W": [10.0, 10.0, 10.0], "n_frames_solved": 3}
        out.update(answer)
        return out

    monkeypatch.setattr("motor_ai_sim.simulation.fem_solver_2d.em_transient_eval",
                        fake_eval)
    monkeypatch.setattr(sim, "_bench_read", lambda *a, **k: {"stubbed": True})
    monkeypatch.setattr(sim, "_bench_compute", lambda *a, **k: None)
    monkeypatch.setattr(sim, "_append_run_journal", lambda *a, **k: None)
    yield calls, answer
    sim.clear_simulation_caches(reason="tdm entry-point test teardown")


RUN = dict(n_steps_per_period=4, n_periods=1.0, gamma_deg=0.0,
           I_phase_rms=10.0, daxis_deg=0.0, eddy=True, fresh=True, ledger=False)


def _summary(res):
    return res.get("summary") or res


@pytest.mark.parametrize("answer", [
    {"eddy_method": "tdm", "eddy_method_requested": "tdm", "eddy_method_note": None,
     "demag_settled": True, "steady_state": True},
    {"eddy_method": "march", "eddy_method_requested": "tdm",
     "eddy_method_note": "march: TDM failed (report_gate: …) — marched instead",
     "demag_settled": None, "steady_state": True},
    {"eddy_method": "tdm", "eddy_method_requested": "tdm", "eddy_method_note": None,
     "demag_settled": False, "steady_state": False},
])
def test_the_em_tab_route_leaves_the_method_to_the_solver_and_reports_it(
        sim, spy, answer):
    calls, ans = spy
    ans.update(answer)
    res = sim.get_fem_transient(**RUN)
    assert calls, "the route did not reach the solver"
    assert calls[-1].get("eddy_method") is None      # the solver's default decides
    assert calls[-1].get("tdm_demag") is None        # never the shortcut
    s = _summary(res)
    for k, v in answer.items():
        assert s.get(k) == v, (k, s.get(k))


# ── REAL calls on the 30 mm fixture (second Codex review, finding 10) ────────
# The source-string checks that used to stand here are replaced by real solves
# through the EM-tab route function and through the optimizer's result path
# (refine_proc.run_one -> kernel "solver.em_transient" -> modules.solvers ->
# get_fem_transient -> em_transient_eval -> the solver), on the physics
# regression's 12-step 30 mm fixture.  Environment: the geometry-driven mesher
# (requirements-triangle.txt, SB_GEO_MESH=1), as tests/test_tdm_fem.py.
def _fixture():
    from tests.test_physics_regression import (CONNECTION, GEO_30MM, OVERRIDE,
                                               RPM)
    return CONNECTION, dict(GEO_30MM), OVERRIDE, RPM


def _cold():
    FS._SB_WARM_CACHE.clear()
    try:
        p = FS._warm_cache_path()
        if p.exists():
            p.unlink()
    except Exception:      # noqa: BLE001
        pass


@pytest.mark.slow
def test_the_em_route_solves_the_fixture_with_tdm_for_real(sim):
    """get_fem_transient, unstubbed: it passes no method, the solver takes
    TDM, the closure march and the report gate pass, and the summary the UI
    and reports read carries the method, the verdict and no note."""
    import json
    from motor_ai_sim.material_context import set_request_materials
    conn, geo, over, rpm = _fixture()
    sim.clear_simulation_caches(reason="tdm entry-point real route")
    _cold()
    set_request_materials(over)
    try:
        res = sim.get_fem_transient(
            n_steps_per_period=12, n_periods=1.0, gamma_deg=0.0, I_phase_rms=60.0,
            rpm=rpm, connection=conn, daxis_deg=60.0, mesh_size_mm=1.4,
            min_size_mm=0.35, gap_layers=1.0, n_sectors=2, structured_gap=True,
            iron_template=True, geo_mesh=True, coil_temp_c=120.0, eddy=True,
            rotor_eddy=True, demag=False, fresh=True, ledger=False,
            geo=json.dumps(geo))
    finally:
        set_request_materials(None)
        _cold()
        sim.clear_simulation_caches(reason="tdm entry-point real route done")
    s = _summary(res)
    assert s["eddy_method"] == "tdm" and s["eddy_method_requested"] == "tdm"
    assert s["eddy_method_note"] is None
    assert s["steady_state"] is True and s["steady_state_note"] is None
    assert s["qualified"] is True and s["tdm_experimental"] is False
    info = res.get("tdm") or {}
    assert info.get("closure", {}).get("ok") is True
    assert info.get("gate", {}).get("ok") is True


@pytest.mark.slow
def test_the_optimizer_result_path_solves_the_fixture_with_tdm_for_real(monkeypatch):
    """refine_proc.run_one with the REAL kernel, module and route: the scored
    result carries steady_state, demag_settled, the method and its note."""
    from motor_ai_sim import config as C
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.optimization import refine_proc as R
    conn, geo, over, rpm = _fixture()
    real = C.get_config()
    try:
        from omegaconf import OmegaConf
        base = (OmegaConf.to_container(real, resolve=True)
                if not isinstance(real, dict) else real)
    except ImportError:              # pragma: no cover — plain dict config
        base = real
    import copy
    cfg = copy.deepcopy(dict(base))
    sim_cfg = dict(cfg.get("simulation") or {})
    sim_cfg.update({"eddy": True, "demag": False, "drive": "current"})
    cfg["simulation"] = sim_cfg
    monkeypatch.setattr(C, "get_config", lambda *a, **k: cfg)
    _cold()
    set_request_materials(over)
    try:
        out = R.run_one(geo, 60.0, 12, 120.0, n_periods=1.0, gamma_deg=0.0,
                        mesh_size_mm=1.4, min_size_mm=0.35, n_sectors=2,
                        gap_layers=1.0, rotor_eddy=True, structured_gap=True,
                        iron_template=True, geo_mesh=True, element_order=2,
                        rpm=rpm, connection=conn, demag=False,
                        sampling_purpose="optimization")
    finally:
        set_request_materials(None)
        _cold()
    assert out["eddy_method"] == "tdm" and out["eddy_method_requested"] == "tdm"
    assert out["eddy_method_note"] is None
    assert out["steady_state"] is True and out["steady_state_note"] is None
    assert out["demag_settled"] is None              # demag off: no verdict
    assert out["qualified"] is True and out["tdm_experimental"] is False
    assert out["eddy_settled"] is True
    # and the optimizer's final certification reads the verdict
    from motor_ai_sim.routes import optimization as O
    for bad, why in (({"steady_state": False, "steady_state_note": "demag NOT settled"},
                      "not a steady state"),
                     ({"qualified": False, "tdm_experimental": True}, "experimental")):
        r = dict(out, cogging_sampling_purpose="cogging_quality",
                 cogging_sampling_final_quality_sufficient=True, **bad)
        ok, reason = O._standard_quality({"ok": True, "res": r})
        assert not ok and why in reason, reason
    for k in ("steady_state", "steady_state_note", "demag_settled", "eddy_method",
              "eddy_method_note", "qualified", "tdm_experimental"):
        assert k in O._RES_KEYS, k


_SOLVE_CALLS = ("get_fem_transient", "em_transient_eval", "fem_transient_sliding_band",
                "_fem_transient_sliding_band_once", "_call_filtered", "run")


def test_no_caller_pins_a_method_or_the_shortcut():
    """AST, not a regex: no call anywhere in src/ passes a CONSTANT eddy_method
    or tdm_demag (the solver's own fallback to the march passes it as an
    expression), and nothing sets SB_EDDY_METHOD / SB_TDM_DEMAG."""
    hits = []
    for p in SRC.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in ("eddy_method", "tdm_demag") and isinstance(
                            kw.value, ast.Constant) and kw.value.value is not None:
                        hits.append("%s:%d %s=%r" % (p.name, node.lineno, kw.arg,
                                                     kw.value.value))
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (isinstance(tgt, ast.Subscript)
                            and isinstance(tgt.slice, ast.Constant)
                            and tgt.slice.value in ("SB_EDDY_METHOD", "SB_TDM_DEMAG",
                                                    "SB_TDM_STOP", "SB_TDM_HALF")):
                        hits.append("%s:%d sets %s" % (p.name, node.lineno,
                                                       tgt.slice.value))
    # the one allowed site: the transactional wrapper's own march fallback
    allowed = [h for h in hits if h.startswith("fem_solver_2d.py")
               and "eddy_method='march'" in h]
    assert sorted(set(hits) - set(allowed)) == [], hits


def test_the_solve_pool_child_inherits_and_resolves_the_choice(monkeypatch):
    """A REAL child process built by the pool's own environment builder."""
    from motor_ai_sim import solve_pool as SP
    code = ("import os; from motor_ai_sim.simulation.time_periodic import "
            "resolve_eddy_method as r; "
            "print(r(None, {}), os.environ.get('SB_TDM_DEMAG'))")
    for env_val, want in (("march", "march"), (None, "tdm")):
        if env_val is None:
            monkeypatch.delenv("SB_EDDY_METHOD", raising=False)
        else:
            monkeypatch.setenv("SB_EDDY_METHOD", env_val)
        monkeypatch.setenv("SB_TDM_DEMAG", "full")
        env = SP._child_env(None, 1)
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr
        assert out.stdout.split() == [want, "full"], out.stdout


def test_config_beats_the_default_and_env_beats_config_in_a_child(monkeypatch):
    from motor_ai_sim import solve_pool as SP
    code = ("from motor_ai_sim.simulation.time_periodic import resolve_eddy_method "
            "as r; print(r(None, {'eddy_method': 'march'}))")
    monkeypatch.delenv("SB_EDDY_METHOD", raising=False)
    out = subprocess.run([sys.executable, "-c", code], env=SP._child_env(None, 1),
                         capture_output=True, text=True, timeout=300)
    assert out.stdout.strip() == "march", out.stderr
    monkeypatch.setenv("SB_EDDY_METHOD", "tdm")
    out = subprocess.run([sys.executable, "-c", code], env=SP._child_env(None, 1),
                         capture_output=True, text=True, timeout=300)
    assert out.stdout.strip() == "tdm", out.stderr
