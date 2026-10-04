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
        # the TDM demag mode the solve would take: the public route never asks
        # for the shortcut (optimizer candidates only, owner 2026-10-04)
        kw = dict(kw, _tdm_demag_request=FS._TDM_DEMAG_REQUEST.get())
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
    assert calls[-1]["_tdm_demag_request"] is None   # …by no other door either
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
    # run_one merges the candidate over the active config's geometry: drop the
    # sandbox machine's DERIVED keys (counts, radii, pitches) so the fixture's
    # own segment form decides, and state its counts explicitly
    cfg["geometry"] = {k: v for k, v in dict(cfg.get("geometry") or {}).items()
                       if k not in ("angle_pole", "angle_slot", "num_poles",
                                    "num_slots", "pole_pitch", "rotor_inner_radius",
                                    "rotor_outer_radius", "slot_pitch", "slot_width",
                                    "stator_inner_radius", "stator_outer_radius")}
    geo = dict(geo, num_slots=int(geo["num_seg"] * geo["num_slots_per_segment"]),
               num_poles=int(geo["num_seg"] * geo["num_poles_per_segment"]))
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


# ── the coupled loop, passports and MCP carry the verdict (third review, #9) ──
_VERDICT = {"eddy_method": "march", "eddy_method_requested": "tdm",
            "eddy_method_note": "march: TDM failed (closure: …) — marched instead",
            "eddy_settled": True, "demag_settled": False, "steady_state": False,
            "steady_state_note": "demag NOT settled after 8 pre-pass period(s): …",
            "qualified": True}


def test_the_coupled_loop_carries_each_passs_verdict(monkeypatch):
    """The REAL coupled loop (route, history, block) with both halves faked
    (as tests/test_coupled.faked_halves): a pass whose EM run was a marched
    fallback on a demag transient says so on its history row, on the block,
    and in `em_steady_state`."""
    from motor_ai_sim.routes import coupled as cp
    from tests.test_coupled import COOLING, EM_BODY

    n = [0]

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        n[0] += 1
        s = {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0, "coil_temp_C": coil_temp_c}
        # pass 1 steady, pass 2 the transient fallback
        s.update(_VERDICT if n[0] >= 2 else {"eddy_method": "tdm",
                                             "eddy_method_requested": "tdm",
                                             "eddy_method_note": None,
                                             "eddy_settled": True,
                                             "demag_settled": None,
                                             "steady_state": True,
                                             "steady_state_note": None,
                                             "qualified": True})
        return {"summary": s}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm,
            bearing_temp_c=None, **_k):
        return {"ok": True,
                "components": {"winding": {"avg": 130.0, "max": 140.0},
                               "magnet": {"avg": 90.0, "max": 95.0}}}
    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False, raising=True)
    monkeypatch.setattr(cp, "_remember_last", lambda out, **k: None, raising=True)
    # the route function itself (as MCP's agent_designs._run_coupled calls it)
    out = cp.run(body={**EM_BODY, "thermal_settings": COOLING, "max_iter": 2,
                       "tol_k": 0.01, "damping": 0.5}, authorization=None)
    c = out["coupling"]
    rows = c["history"]
    assert [row["steady_state"] for row in rows] == [True, False]
    assert rows[1]["eddy_method"] == "march"
    assert rows[1]["eddy_method_note"].startswith("march: TDM failed")
    assert rows[1]["steady_state_note"].startswith("demag NOT settled")
    assert c["em"]["steady_state"] is False and c["em"]["demag_settled"] is False
    assert c["em_steady_state"] is False


def test_a_passport_reports_its_solves_verdicts(monkeypatch):
    """generate_passport on the stub solver of test_passport_loss_fixes: one
    solve that is a demag transient makes the passport say so."""
    from motor_ai_sim import passport as pp
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes import simulation as sim
    from tests.test_passport_loss_fixes import _StubSolver

    geo = dict(get_config().get("geometry") or {})
    L0 = float(geo.get("motor_length") or 12.0)
    stub = _StubSolver("star", L0)
    calls = [0]

    def solver(**kw):
        d = stub(**kw)
        calls[0] += 1
        v = dict(_VERDICT) if calls[0] == 2 else {"eddy_method": "tdm",
                                                  "steady_state": True,
                                                  "eddy_method_note": None}
        d["summary"].update(v)
        return d
    monkeypatch.setattr(sim, "get_fem_transient", solver)
    out = pp.generate_passport(
        machine={"geometry": geo, "connection": None, "star_delta": "star",
                 "materials": {}, "end_winding_factor": 2.0},
        I0=30.0, gamma_deg=10.0, rpm0=1000.0, rpms=[500.0, 1000.0],
        base_steps=6, sweep_steps=6, pwm="off")
    ss = out["passport"]["solve_status"]
    assert ss["solves"] == calls[0] and ss["steady_state"] is False
    assert ss["not_steady"] == [_VERDICT["steady_state_note"]]
    assert ss["eddy_methods"] == ["march", "tdm"]
    assert ss["notes"] == [_VERDICT["eddy_method_note"]]


def test_a_passport_needs_every_solve_to_affirm_steadiness(monkeypatch):
    """Fourth review: verdicts [True, …, None] (one solve that did not say)
    are NOT steady — the unknown solve is named."""
    from motor_ai_sim import passport as pp
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes import simulation as sim
    from tests.test_passport_loss_fixes import _StubSolver

    geo = dict(get_config().get("geometry") or {})
    stub = _StubSolver("star", float(geo.get("motor_length") or 12.0))
    calls = [0]

    def solver(**kw):
        d = stub(**kw)
        calls[0] += 1
        if calls[0] != 2:                    # solve 2 says nothing
            d["summary"].update({"eddy_method": "tdm", "steady_state": True,
                                 "eddy_method_note": None})
        return d
    monkeypatch.setattr(sim, "get_fem_transient", solver)
    out = pp.generate_passport(
        machine={"geometry": geo, "connection": None, "star_delta": "star",
                 "materials": {}, "end_winding_factor": 2.0},
        I0=30.0, gamma_deg=10.0, rpm0=1000.0, rpms=[500.0, 1000.0],
        base_steps=6, sweep_steps=6, pwm="off")
    ss = out["passport"]["solve_status"]
    assert ss["steady_state"] is False
    assert ss["not_steady"] == ["solve 2 did not affirm a steady state (verdict unknown)"]


def test_mcp_headlines_carry_the_verdict():
    """agent_designs.headline — what MCP simulate returns to an agent — for a
    transient answer and for a coupled answer."""
    from motor_ai_sim import agent_designs as AD
    d = {"params": {"speed_rpm": 3000.0, "current_a_rms": 10.0, "mode": "motor"},
         "requirements": {}}
    em = {"summary": {"T_em_avg_Nm": 1.0, "P_loss_total_W": 10.0, **_VERDICT}}
    h = AD.headline(em, d, "em")
    for k in ("steady_state", "steady_state_note", "eddy_method", "eddy_method_note",
              "qualified"):
        assert h[k] == _VERDICT[k], k
    co = {"em": {"T_em_avg_Nm": 1.0, "P_loss_total_W": 10.0, "steady_state": None,
                 "eddy_method_note": _VERDICT["eddy_method_note"]},
          "em_steady_state": False, "coil_temp_c": 100.0}
    h = AD.headline(co, d, "coupled")
    assert h["steady_state"] is False
    assert h["eddy_method_note"] == _VERDICT["eddy_method_note"]


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
