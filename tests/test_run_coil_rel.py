"""The wire cell of a preset run (owner 2026-11-24): a per-run override that reaches the eval subprocess, never the stored settings."""
import copy

import pytest

from motor_ai_sim import run_settings as RS
from motor_ai_sim.optimization import refine_proc as RP
from motor_ai_sim.routes import optimization as O
from tests.test_optimize_headless_prepare_scan import _req
from tests.test_sweep_optimization_preset import _cfg


def _key(**kw):
    return O._eval_cache_key({"tooth_width": 3.0}, 10.0, 48, 80.0, 1.0, 0.0, 1.2, 0.12, 2, None, False, "fp", **kw)


def test_the_env_carries_the_run_wire_cell_only_inside_the_run(monkeypatch):
    monkeypatch.setenv("MOTRES_RUN_COIL_REL", "0.5")                     # a stray value of the server's own environment
    assert "MOTRES_RUN_COIL_REL" not in O._base_eval_env()
    tok = O._RUN_COIL_REL.set(2.0)
    try:
        assert O._base_eval_env()["MOTRES_RUN_COIL_REL"] == "2.0"
        assert O._eval_env_for(4)["MOTRES_RUN_COIL_REL"] == "2.0"
    finally:
        O._RUN_COIL_REL.reset(tok)
    assert "MOTRES_RUN_COIL_REL" not in O._base_eval_env()


def test_a_pool_worker_sees_it_inside_the_run_and_a_reused_worker_does_not_keep_it():
    from motor_ai_sim.workspace import WorkspaceThreadPoolExecutor as Pool
    seen = []

    def work():
        seen.append(O._base_eval_env().get("MOTRES_RUN_COIL_REL"))
    with Pool(max_workers=1) as ex:                                      # ONE worker thread, reused
        O._with_run_coil(lambda: ex.submit(work).result(), {"coil_rel_override": 2.0})()
        ex.submit(work).result()
        O._with_run_coil(lambda: ex.submit(work).result(), {"coil_rel_override": 1.0})()
        O._with_run_coil(lambda: ex.submit(work).result(), {"coil_rel_override": None})()
    assert seen == ["2.0", None, "1.0", None]
    assert O._RUN_COIL_REL.get() is None


def test_no_override_wraps_nothing_and_the_cache_key_is_unchanged():
    f = lambda: 1                                                        # noqa: E731
    assert O._with_run_coil(f, None) is f and O._with_run_coil(f, {"coil_rel_override": None}) is f
    base = _key()
    assert base == _key()
    tok = O._RUN_COIL_REL.set(2.0)
    try:
        k2 = _key()
    finally:
        O._RUN_COIL_REL.reset(tok)
    tok = O._RUN_COIL_REL.set(1.0)
    try:
        k1 = _key()
    finally:
        O._RUN_COIL_REL.reset(tok)
    assert len({base, k1, k2}) == 3                                      # 1h and 2h evaluations never share an entry
    assert _key() == base


def test_the_worker_overrides_the_wire_cell_in_the_one_place_it_reads_the_block(monkeypatch):
    cfg = {"mesh": {"component_mesh": {"stator": 1.2, "coil_rel": 1}}}
    monkeypatch.delenv("MOTRES_RUN_COIL_REL", raising=False)
    assert RP._component_mesh_for_run(cfg) == {"stator": 1.2, "coil_rel": 1}
    assert RP._component_mesh_for_run({}) == {}
    monkeypatch.setenv("MOTRES_RUN_COIL_REL", "2.0")
    got = RP._component_mesh_for_run(cfg)
    assert got == {"stator": 1.2, "coil_rel": 2.0} and cfg["mesh"]["component_mesh"]["coil_rel"] == 1
    monkeypatch.setenv("MOTRES_RUN_COIL_REL", "junk")
    assert RP._component_mesh_for_run(cfg) == {"stator": 1.2, "coil_rel": 1}


def test_a_preset_run_on_a_stored_1h_machine_runs_at_2h_and_the_machine_is_untouched(monkeypatch):
    cfg = _cfg(coil_rel=1)
    before = copy.deepcopy(cfg)
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    rec = {"preset": "optimization", "overrides": {}}
    shown = RS.resolve(rec["preset"], rec["overrides"], cfg)
    plan = O.prepare_scan(_req(run_settings={**rec, "expect_sig": shown["sig"]}))
    mp = plan["kwargs"]["mesh_preset"]
    assert mp["coil_rel"] == 2.0 and mp["coil_rel_override"] == 2.0 and mp["name"] == "optimization" and not mp["custom"]
    seen = []
    O._with_run_coil(lambda: seen.append(O._RUN_COIL_REL.get()), mp)()
    assert seen == [2.0] and cfg == before                               # the stored block still says 1h
    # the same preset on a machine that already stores 2h passes nothing
    cfg2 = _cfg(coil_rel=2)
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg2)
    mp2 = O.prepare_scan(_req(run_settings=rec))["kwargs"]["mesh_preset"]
    assert mp2["coil_rel_override"] is None and O._with_run_coil(print, mp2) is print


def test_a_run_without_run_settings_is_as_before(monkeypatch):
    cfg = _cfg(coil_rel=1)
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    plan = O.prepare_scan(_req())
    assert O._run_coil_of(plan["kwargs"].get("mesh_preset")) is None
    assert O._with_run_coil(O._scan_worker, plan["kwargs"].get("mesh_preset")) is O._scan_worker


def test_descent_and_auto_plans_carry_the_run_wire_cell(monkeypatch):
    cfg = _cfg(coil_rel=1)
    monkeypatch.setattr(O, "get_config", lambda *a, **k: cfg)
    rec = {"preset": "optimization", "overrides": {}}
    d = O.prepare_descent(O.DescentRequest(variables=[{"name": "tooth_width", "min": 0.0, "max": 0.0}], run_settings=rec,
                                           steps_per_period=24))
    mp = d["response"]["mesh_preset"]
    assert mp["coil_rel_override"] == 2.0 and O._with_run_coil(d["worker"], mp) is not d["worker"]
    seen = []
    O._with_run_coil(lambda: seen.append(O._RUN_COIL_REL.get()), mp)()
    assert seen == [2.0]
    shown = RS.resolve("optimization", {}, cfg, "auto")
    assert shown["coil_rel_override"] == 2.0 and shown["settings"]["coil_rel"] == 2.0


# ── round 2: every entry point, the headless memo, the solved cell, a reused worker PROCESS ──
def _probe_job(rec):
    return O._with_run_coil(lambda: (O._RUN_COIL_REL.get(), O._base_eval_env().get("MOTRES_RUN_COIL_REL")), rec)()


def _probe_plain():
    return (O._RUN_COIL_REL.get(), O._base_eval_env().get("MOTRES_RUN_COIL_REL"))


def test_a_reused_worker_process_does_not_keep_the_cell_of_its_first_job():
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("fork")) as ex:        # ONE process, two jobs
        first = ex.submit(_probe_job, {"coil_rel_override": 2.0}).result()
        second = ex.submit(_probe_plain).result()
        third = ex.submit(_probe_job, {"coil_rel_override": None}).result()
    assert first == (2.0, "2.0") and second == (None, None) and third == (None, None)


def test_every_worker_establishes_the_cell_itself_so_a_resume_gets_it():
    for fn in (O._scan_worker, O._descent_worker, O._cmaes_worker, O._auto_worker, O._screen_worker):
        assert hasattr(fn, "__wrapped__"), fn.__name__            # the decorator is on the function every entry point starts
    seen = []

    @O._run_coil_worker(lambda a, k: k.get("mesh_preset"))
    def fake_scan(*a, mesh_preset=None):
        seen.append(O._RUN_COIL_REL.get())
    fake_scan(mesh_preset={"coil_rel_override": 2.0})              # what sweep_resume does with the journal's stamp
    fake_scan(mesh_preset=None)
    fake_scan()
    assert seen == [2.0, None, None] and O._RUN_COIL_REL.get() is None

    @O._run_coil_worker(lambda a, k: (_ for _ in ()).throw(KeyError("no record")))
    def broken():
        seen.append("ran")
    broken()                                                         # an unreadable record is no override, never a failure
    assert seen[-1] == "ran"


def test_the_descent_worker_reads_the_stamp_of_the_run_state(monkeypatch):
    with O._descent_lock:
        O._descent_state["mesh_preset"] = {"coil_rel_override": 2.0}
    try:
        assert O._descent_rec((), {}) == {"coil_rel_override": 2.0}
    finally:
        with O._descent_lock:
            O._descent_state.pop("mesh_preset", None)


def test_the_headless_memo_never_answers_a_cell_request_with_another_cell(tmp_path):
    from motor_ai_sim.optimize_headless import EvalMemo
    m = EvalMemo(O, tmp_path / "memo.jsonl", None)
    kw = {"n_periods": 1.0}
    plain = m.key({"tooth_width": 3.0}, 10.0, 48, 80.0, kw)
    assert plain == m.key({"tooth_width": 3.0}, 10.0, 48, 80.0, kw)
    tok = O._RUN_COIL_REL.set(1.0)
    try:
        one = m.key({"tooth_width": 3.0}, 10.0, 48, 80.0, kw)
    finally:
        O._RUN_COIL_REL.reset(tok)
    tok = O._RUN_COIL_REL.set(2.0)
    try:
        two = m.key({"tooth_width": 3.0}, 10.0, 48, 80.0, kw)
    finally:
        O._RUN_COIL_REL.reset(tok)
    assert len({plain, one, two}) == 3


def _pts(n, **extra):
    return [dict({"fem": True, "feasible": True, "n_steps_per_period_solved": 48, "gap_layers_per_side": 1.0}, **extra) for _ in range(n)]


def test_the_stamp_verifies_the_cell_the_mesher_used():
    rec = O._sweep_settings_record({"name": "optimization", "custom": False, "sig": 99, "coil_rel": 2.0, "sig_scope": 2}, 48, 1.0)
    assert rec["requested"]["coil_rel"] == 2.0 and rec["effective_source"]["coil_rel"] == "requested"
    ok = O._stamp_solved(rec, _pts(2, coil_rel_solved=2.0))
    assert ok["effective_source"]["coil_rel"] == "solved" and ok["sig"] == 99 and not ok.get("custom")
    absent = O._stamp_solved(rec, _pts(2))                          # a point that does not report the cell: unknown identity
    assert absent["effective_source"]["coil_rel"] == "requested" and absent["sig"] is None
    wrong = O._stamp_solved(rec, _pts(2, coil_rel_solved=1.0))      # the mesher used another cell than the label says
    assert wrong["custom"] and wrong["name"] == "custom" and wrong["sig"] is None and wrong["effective"]["coil_rel"] == 1.0
    from motor_ai_sim import sweep_saves as SS
    assert SS.sweep_settings_of({"mesh_preset": ok})["sig"] == 99
    assert SS.sweep_settings_of({"mesh_preset": absent})["sig"] is None
    assert SS.sweep_settings_of({"mesh_preset": wrong})["sig"] is None


def test_refine_proc_reports_the_cell_it_gave_the_mesher(monkeypatch):
    monkeypatch.delenv("MOTRES_RUN_COIL_REL", raising=False)
    assert RP._coil_rel_solved({}) == 1.0
    assert RP._coil_rel_solved({"mesh": {"component_mesh": {"coil_rel": 2}}}) == 2.0
    monkeypatch.setenv("MOTRES_RUN_COIL_REL", "2.0")
    assert RP._coil_rel_solved({"mesh": {"component_mesh": {"coil_rel": 1}}}) == 2.0
