"""Optimizer items of 2026-09-24 (docs/OPTIMIZER_ITEMS_DE_2026-09-24.md).

D  - the final validation solves baseline A alone, then B and the finalists
     in parallel;
E  - a standard eval may continue a warm state solved at another steps/period
     (flag, standard evals only; the solver hook is one separate function);
5c - stored descent/auto points are re-checked on demand at standard;
4  - one CPU budget shared by concurrently running optimizer jobs.

No FEM runs here: subprocesses and solves are fakes.
"""
import json
import threading
import time

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import optimization as O


# ── 4: one CPU budget across concurrent optimizer jobs ──────────────────────

@pytest.fixture
def ax42(monkeypatch):
    """8 physical cores available -> budget min(8, 8 - 2) = 6 (the AX42)."""
    monkeypatch.delenv("FEM_SCAN_WORKERS", raising=False)
    monkeypatch.setattr(O, "_physical_cores_available", lambda: 8)
    yield


def test_budget_is_shared_by_the_jobs_that_are_running(ax42):
    assert O._optimizer_worker_budget() == 6
    with O._OptimizerJob("scan") as a:
        assert a.workers == 6
        assert O._job_workers() == 6
        with O._OptimizerJob("auto") as b:
            assert b.workers == 3
            assert O._optimizer_share() == 3
        assert O._optimizer_share() == 6          # the second job ended
    assert O._optimizer_active_jobs() == 0
    assert O._OPT_JOB.get() is None


def test_fem_scan_workers_still_overrides_without_sharing(ax42, monkeypatch):
    monkeypatch.setenv("FEM_SCAN_WORKERS", "4")
    with O._OptimizerJob("scan") as a, O._OptimizerJob("descent") as b:
        assert a.workers == 4 and b.workers == 4
        assert O._optimizer_share() == 4


def test_three_jobs_never_get_less_than_one_worker(monkeypatch):
    monkeypatch.delenv("FEM_SCAN_WORKERS", raising=False)
    monkeypatch.setattr(O, "_physical_cores_available", lambda: 4)   # budget 2
    with O._OptimizerJob("a"), O._OptimizerJob("b"), O._OptimizerJob("c") as c:
        assert c.workers == 1


def test_the_limiter_holds_a_job_at_its_live_share(ax42):
    """Two jobs -> share 3 each.  Job A's pool asks for 5 evals at once: only
    3 run; the 4th starts when one of the 3 ends; when job B ends, A grows."""
    ctx = {}
    running = []
    peak = [0]
    lock = threading.Lock()
    gate = threading.Event()

    def one_eval():
        tok = O._optimizer_slot_acquire()
        with lock:
            running.append(1)
            peak[0] = max(peak[0], len(running))
        gate.wait(5.0)
        with lock:
            running.pop()
        O._optimizer_slot_release(tok)

    import contextvars
    job_b = O._OptimizerJob("b")
    ctx_b = contextvars.copy_context()        # job B lives in its own context
    with O._OptimizerJob("a"):
        ctx_b.run(job_b.__enter__)            # a second job is running
        threads = []
        for _ in range(5):
            c = contextvars.copy_context()
            t = threading.Thread(target=c.run, args=(one_eval,))
            t.start()
            threads.append(t)
        time.sleep(0.5)
        with lock:
            assert len(running) == 3          # held at the share
        ctx_b.run(job_b.__exit__, None, None, None)   # B ends -> A's share is 6
        deadline = time.time() + 5
        while time.time() < deadline:
            with lock:
                if len(running) == 5:
                    break
            time.sleep(0.05)
        with lock:
            assert len(running) == 5
        gate.set()
        for t in threads:
            t.join(5)
    assert peak[0] == 5
    assert O._optimizer_active_jobs() == 0


def test_an_eval_outside_any_job_is_not_throttled(ax42):
    assert O._optimizer_slot_acquire() is None
    O._optimizer_slot_release(None)


class _FakeProc:
    def __init__(self, env, payload):
        self.env = env
        self.pid = 4242
        self.returncode = 0
        self._payload = payload

    def communicate(self, input=None, timeout=None):
        return "@@RESULT@@" + json.dumps(self._payload), ""

    def poll(self):
        return 0

    def kill(self):
        pass


@pytest.fixture
def fake_popen(monkeypatch):
    seen = []

    def popen(cmd, **kw):
        p = _FakeProc(kw.get("env") or {}, {"ok": False, "error": "fake"})
        seen.append(p)
        return p

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(O, "_log_eval", lambda *a, **k: None)
    monkeypatch.setattr(O, "_record_eval_seconds", lambda *a, **k: None)
    monkeypatch.setattr(O, "_seed_usable_for", lambda *a, **k: (False, "test"))
    return seen


def test_every_eval_gives_its_slot_back(ax42, fake_popen):
    with O._OptimizerJob("scan") as job:
        out = O._subprocess_eval({}, 10.0, 12, 100.0, sampling_purpose="optimization")
        assert out == {"ok": False, "error": "fake"}
        with O._opt_jobs_cond:
            assert O._opt_jobs_inflight[job.token] == 0


# ── E: the flag reaches only FINAL-QUALITY (cogging_quality) evals ─────────

@pytest.mark.parametrize("flag,purpose,expect", [
    ("1", "cogging_quality", True),
    ("1", "standard", False),
    ("1", "optimization", False),
    ("0", "cogging_quality", False),
])
def test_warm_start_across_steps_env_only_for_standard_evals(
        monkeypatch, fake_popen, flag, purpose, expect):
    monkeypatch.setenv("OPT_FINAL_WARM_START", flag)
    monkeypatch.setenv("SB_SEED_ACROSS_STEPS", "1")     # never leaks from the parent
    O._subprocess_eval({}, 10.0, 12, 100.0, sampling_purpose=purpose)
    env = fake_popen[-1].env
    assert (env.get("SB_SEED_ACROSS_STEPS") == "1") is expect


def test_warm_start_default_is_the_documented_constant(monkeypatch):
    monkeypatch.delenv("OPT_FINAL_WARM_START", raising=False)
    assert O._final_warm_start_enabled() is bool(O._FINAL_WARM_START_DEFAULT)


def test_solver_hook_relaxes_only_the_steps_term(monkeypatch):
    from motor_ai_sim.simulation import fem_solver_2d as F
    meta = {"nspp": 72, "npd": 1.0, "conn": "np1", "temp": 120.0, "mscale": 1.0}
    wc = {"meta": dict(meta, nspp=48), "rpm": 15000.0, "I": 32.0, "gam": 6.0,
          "nsect": 2}
    monkeypatch.setenv("SB_SEED_FROM_PREVIOUS", "1")
    monkeypatch.delenv("SB_SEED_ACROSS_STEPS", raising=False)
    assert F._warm_seed_accept(wc, meta, 32.0, 15000.0, 6.0, n_sectors=2)[0] is False
    monkeypatch.setenv("SB_SEED_ACROSS_STEPS", "1")
    assert F._warm_seed_accept(wc, meta, 32.0, 15000.0, 6.0, n_sectors=2)[0] is True
    # every OTHER term stays a hard refusal
    for k, v in (("temp", 121.0), ("conn", "np2"), ("npd", 2.0), ("mscale", 0.9)):
        bad = dict(wc, meta=dict(wc["meta"], **{k: v}))
        assert F._warm_seed_accept(bad, meta, 32.0, 15000.0, 6.0, n_sectors=2)[0] is False
    assert F._warm_seed_accept(dict(wc, nsect=1), meta, 32.0, 15000.0, 6.0,
                               n_sectors=2)[0] is False
    assert F._warm_seed_accept(wc, meta, 32.0, 12000.0, 6.0, n_sectors=2)[0] is False


# ── final purpose after 1883ba7: "cogging_quality" carries the 6-sample flag ──

def _final(purpose, flag):
    return {"ok": True, "res": {"T_em_Nm": 1.0, "nonlinear_converged": True,
                                "cogging_sampling_purpose": purpose,
                                "cogging_sampling_final_quality_sufficient": flag}}


def test_final_quality_is_the_six_sample_flag_on_a_final_purpose_solve():
    assert O._FINAL_PURPOSE == "cogging_quality"
    assert O._standard_quality(_final("cogging_quality", True))[0]
    # "standard" keeps the requested steps since 1883ba7: without the flag it
    # is the candidates' own resolution and certifies nothing
    assert not O._standard_quality(_final("standard", False))[0]
    assert O._standard_quality(_final("standard", True))[0]
    assert not O._standard_quality(_final("optimization", True))[0]
    assert not O._standard_quality(_final("cogging_quality", False))[0]
    assert O._pt(dict(_final("cogging_quality", True), overrides={}),
                 "x")["sampling_quality"] == "standard"
    assert O._pt(dict(_final("standard", False), overrides={}),
                 "x")["sampling_quality"] == "preliminary"


def test_every_worker_validates_its_winner_at_the_final_purpose():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "routes"
           / "optimization.py").read_text(encoding="utf-8")
    assert src.count("evaluate=lambda d, cur: _eval_at(d, cur, _FINAL_PURPOSE)") == 4
    assert '_eval_at(d, cur, "standard")' not in src


# ── D: A alone, then B + finalists in parallel ──────────────────────────────

def _std(x, current):
    eff = {0: 0.50, 1: 0.70, 2: 0.95, 3: 0.60}[int(x["g"])]
    if int(x["g"]) == 0 and current > 10:
        eff = 0.45
    return {"ok": True, "res": {
        "T_em_Nm": 1.0, "efficiency": eff, "torque_per_mass_Nm_kg": 2.0 + x["g"],
        "T_ripple_pct": 4.0, "current_a": current, "nonlinear_converged": True,
        "cogging_sampling_purpose": "standard",
        "cogging_sampling_final_quality_sufficient": True}}


def test_b_and_finalists_run_concurrently_after_a(monkeypatch):
    monkeypatch.delenv("FEM_SCAN_WORKERS", raising=False)
    monkeypatch.setattr(O, "_physical_cores_available", lambda: 12)
    order = []
    inflight = [0]
    peak = [0]
    lock = threading.Lock()
    a_done = threading.Event()
    barrier = threading.Barrier(4, timeout=10)      # B + 3 finalists together

    def evaluate(x, current):
        with lock:
            order.append((x["g"], current, a_done.is_set()))
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
        try:
            if x["g"] == 0 and current == 10.0:
                assert inflight[0] == 1               # A is alone
                a_done.set()
            else:
                barrier.wait()                        # all four must overlap
            return _std(x, current)
        finally:
            with lock:
                inflight[0] -= 1

    points = [{"kind": "cmaes", "overrides": {"g": g}, "current_a": 10.0,
               "eff": 0.6 + 0.1 * g, "td": 3.0, "ripple": 4.0, "v_peak": 2.0,
               "thd": 1.0} for g in (2, 3)]
    final = O._finalize_standard_shortlist(
        points=points, best_x={"g": 1},
        best_metrics={"efficiency": 0.9, "current_a": 10.0},
        coarse_base={"efficiency": 0.5, "_bline": {"w_td": 1.0, "w_eff": 1.0}},
        base_x={"g": 0}, base_current=10.0, bump_pct=10.0, evaluate=evaluate,
        score=lambda m, b: (-float(m["efficiency"]), float(m["efficiency"])))
    assert final["status"] == "certified", final.get("reason")
    assert order[0] == (0, 10.0, False)
    assert all(done for _, _, done in order[1:])
    assert peak[0] == 4
    assert final["timings"]["parallel_jobs"] == 4
    assert final["winner"]["x"] == {"g": 2}


# ── 5c: on-demand re-check of a stored optimizer point ──────────────────────

def _stored_run(**extra):
    st = {"running": False, "run_id": "r-1", "cancel": False,
          "eval_params": {"steps_per_period": 48, "n_sectors": 2, "gap_layers": 1.0,
                          "coil_temp_c": 120.0, "pole_copy": None,
                          "torque_filter": False, "rotor_eddy": True,
                          "end_winding_factor": 1.675, "structured_gap": True,
                          "airgap_macro": False, "iron_template": True,
                          "geo_mesh": True, "mesh_size_mm": 1.0,
                          "min_size_mm": 0.3},
          "best": {"x": {"magnet_fill_up": 0.62}, "metrics": {"current_a": 32.0}},
          "points": [{"kind": "cmaes", "overrides": {"magnet_fill_up": 0.6},
                      "current_a": 32.0, "sampling_quality": "preliminary"}],
          "result": {"operating_point": {"current_a": 32.0, "rpm": 15000.0,
                                         "gamma_deg": 6.0}},
          "final_validation_status": "preliminary", "apply_eligible": False}
    st.update(extra)
    return st


def _std_eval(**kw):
    return {"ok": True, "res": {
        "T_em_Nm": 0.25, "T_ripple_pct": 2.5, "efficiency": 0.85,
        "mass_total_kg": 0.15, "torque_per_mass_Nm_kg": 1.67,
        "P_loss_total_W": 12.0, "nonlinear_converged": True, "eddy_settled": True,
        "cogging_sampling_purpose": "standard",
        "cogging_sampling_final_quality_sufficient": True}}


@pytest.fixture
def stored(monkeypatch):
    st = _stored_run()
    calls = []
    fp = {"now": "machinefp"}
    monkeypatch.setattr(O, "_descent_state", st)
    monkeypatch.setattr(O, "_save_descent_state", lambda: None)
    monkeypatch.setattr(O, "_config_fingerprint", lambda exclude=(): fp["now"])
    monkeypatch.setattr(O, "_subprocess_eval",
                        lambda **kw: calls.append(kw) or _std_eval(**kw))
    return st, calls, fp


def test_legacy_run_best_is_rechecked_with_its_own_eval_settings(stored):
    st, calls, _ = stored
    got = O.descent_validate_point(O.DescentValidateRequest(run_id="r-1", target="best"))
    kw = calls[0]
    assert kw["sampling_purpose"] == "cogging_quality"
    assert kw["overrides"] == {"magnet_fill_up": 0.62}
    assert (kw["current_a"], kw["gamma_deg"], kw["rpm"]) == (32.0, 6.0, 15000.0)
    assert kw["steps"] == 48 and kw["n_sectors"] == 2 and kw["mesh_size_mm"] == 1.0
    assert got["provenance"] == "legacy_unpinned"
    p = got["point"]
    assert p["apply_eligible"] is True and p["sampling_quality"] == "standard"
    assert p["metrics"]["T_em_Nm"] == 0.25          # the fresh standard number
    assert st["rechecks"][-1]["target"] == "best"


def test_picked_point_must_be_a_stored_point_of_the_run(stored):
    st, calls, _ = stored
    got = O.descent_validate_point(O.DescentValidateRequest(
        run_id="r-1", target="point", overrides={"magnet_fill_up": 0.6}, current_a=32.0))
    assert calls[0]["overrides"] == {"magnet_fill_up": 0.6}
    assert got["point"]["apply_eligible"] is True
    with pytest.raises(HTTPException) as ei:            # forged geometry
        O.descent_validate_point(O.DescentValidateRequest(
            run_id="r-1", target="point", overrides={"magnet_fill_up": 0.99},
            current_a=32.0))
    assert ei.value.status_code == 422
    assert len(calls) == 1


def test_mtpa_gamma_and_pinned_machine(stored):
    st, calls, fp = stored
    st.update(mtpa_gamma_deg=9.5, machine_fp="machinefp",
              machine_fp_excl=["magnet_fill_up"])
    got = O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert calls[0]["gamma_deg"] == 9.5
    assert got["provenance"] == "pinned"
    fp["now"] = "another-motor"
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert ei.value.status_code == 409
    assert len(calls) == 1


def test_recheck_refuses_running_or_other_run_and_failed_quality(stored, monkeypatch):
    st, calls, _ = stored
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="other"))
    assert ei.value.status_code == 409
    st["running"] = True
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert ei.value.status_code == 409
    st["running"] = False
    assert not calls
    monkeypatch.setattr(O, "_subprocess_eval", lambda **kw: dict(
        _std_eval(), res=dict(_std_eval()["res"],
                              cogging_sampling_final_quality_sufficient=False)))
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert ei.value.status_code == 422
    monkeypatch.setattr(O, "_subprocess_eval", lambda **kw: dict(
        _std_eval(), res=dict(_std_eval()["res"], eddy_settled=False)))
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert ei.value.status_code == 422
    assert not O._descent_recheck_running          # in-flight marker cleared


def test_run_without_eval_settings_is_refused(stored):
    st, calls, _ = stored
    st.pop("eval_params")
    with pytest.raises(HTTPException) as ei:
        O.descent_validate_point(O.DescentValidateRequest(run_id="r-1"))
    assert ei.value.status_code == 422
    assert not calls


# ── picard stamp: fail-closed, but a thin payload is named for what it lacks ─

def test_missing_picard_stamp_still_rejects_a_scored_payload(monkeypatch):
    from motor_ai_sim.contracts.result_ir import ResultIR
    from motor_ai_sim.optimization import refine_proc as R

    class _K:
        def run(self, capability, payload):
            return {"ok": True, "result": ResultIR(
                physics="em_transient", ok=True,
                raw={"n_steps": 8, "T_avg_Nm": 1.0, "P_cu_W": [1.0],
                     "P_fe_W": [1.0], "P_mag_eddy_W": [0.0]})}

    monkeypatch.setattr(R, "_kernel", lambda: _K())
    with pytest.raises(RuntimeError, match="picard_converged"):
        R.run_one({}, 50.0, 8, 100.0, n_periods=1.0, gamma_deg=0.0,
                  mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=4,
                  element_order=2, rpm=3000.0)
