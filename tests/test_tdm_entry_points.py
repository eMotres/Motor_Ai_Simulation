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


_DOORS = {
    "routes/coupled.py": "get_fem_transient",
    "passport.py": "get_fem_transient",
    "agent_designs.py": "get_fem_transient",
    "modules/solvers.py": "get_fem_transient",
    "solve_pool_child.py": "em_transient_eval",
    "routes/simulation.py": "em_transient_eval",
}


@pytest.mark.parametrize("rel, callee", sorted(_DOORS.items()))
def test_every_door_goes_through_the_one_route(rel, callee):
    txt = (SRC / rel).read_text(encoding="utf-8")
    assert callee in txt, (rel, callee)


def test_the_optimizer_and_mcp_reach_the_route():
    assert '"solver.em_transient"' in (SRC / "optimization/refine_proc.py").read_text(
        encoding="utf-8")
    assert "get_fem_transient" in (SRC / "modules/solvers.py").read_text(encoding="utf-8")
    mcp = (SRC / "mcp_app.py").read_text(encoding="utf-8")
    assert "agent_designs" in mcp


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
