"""The deploy guard sees the Optimize tab's runs (measured 2026-11-25: an auto run with 6 workers answered jobs.running 0 on
/api/admin/live, so ``busy()`` of deploy/deploy_module.sh would have restarted the worker).  A running descent / auto optimisation,
sweep, DOE or refine counts as a running job with kind, owner bucket and progress; nothing is run here (the state flags are set)."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from motor_ai_sim import live_status as L
from motor_ai_sim.routes import optimization as O


@pytest.fixture()
def idle_states():
    """The process workspace's optimiser states, restored afterwards."""
    keep = {}
    for name, st in (("descent", O._descent_state), ("scan", O._scan_state), ("doe", O._doe_state), ("refine", O._refine_state)):
        keep[name] = (st, {k: st.get(k) for k in ("running", "done", "total", "iter", "max_iters", "run_id", "phase", "auto", "algorithm")
                           if k in st})
    for st, _old in keep.values():
        st["running"] = False
    yield
    for st, old in keep.values():
        for k, v in old.items():
            st[k] = v


def test_an_idle_optimizer_adds_nothing(idle_states):
    assert L.optimizer_rows() == []
    snap = L.local_snapshot()
    assert snap["jobs"]["running"] == 0 or "descent" not in snap["jobs"]["kinds"]


def test_a_running_auto_run_is_a_running_job_with_kind_owner_and_progress(idle_states):
    O._descent_state.update({"running": True, "run_id": "auto_1", "iter": 7, "max_iters": 40, "phase": "optimizing",
                             "auto": {"objective": "x"}})
    rows = L.optimizer_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "auto" and r["state"] == "running" and r["run_id"] == "auto_1" and r["phase"] == "optimizing"
    assert r["progress"] == {"done": 7, "total": 40} and r["owner"]
    assert "@" not in r["owner"]                                              # a workspace bucket, never an e-mail
    assert L.local_snapshot()["jobs"]["running"] >= 1 and L.local_snapshot()["jobs"]["kinds"].get("auto", 0) == 1
    assert L._jobs()["running"] >= 1 and L._jobs()["kinds"]["auto"] == 1      # a single-process deployment
    assert L._jobs_memory()["running"] >= 1 and L._jobs_memory()["kinds"]["auto"] == 1   # the compute worker
    assert any(x["kind"] == "auto" and x["state"] == "running" for x in L.job_rows())     # the Admin / worker state rows
    assert L.idle(L.local_snapshot(), ["all"]) is False                       # what busy() reads


def test_a_manual_descent_a_sweep_and_a_doe_count_too(idle_states):
    O._descent_state.update({"running": True, "run_id": "d1", "iter": 2, "max_iters": 10, "phase": "", "auto": None, "algorithm": "cmaes"})
    O._scan_state.update({"running": True, "run_id": "s1", "done": 3, "total": 24})
    O._doe_state.update({"running": True, "done": 1, "total": 9})
    by = {r["kind"]: r for r in L.optimizer_rows()}
    assert set(by) == {"descent", "sweep", "doe"}
    assert by["sweep"]["progress"] == {"done": 3, "total": 24} and by["descent"]["progress"] == {"done": 2, "total": 10}
    assert L._jobs_memory()["kinds"].get("sweep") == 1


def test_every_workspace_is_read_and_none_is_created(idle_states, monkeypatch):
    def ws(wid, running):
        st = {"running": running, "done": 1, "total": 2, "run_id": "r" + wid}
        return SimpleNamespace(id=wid, state=SimpleNamespace(lock=threading.RLock(), slots={"optimization.scan_state": st}))
    monkeypatch.setattr("motor_ai_sim.workspace.live_workspaces", lambda: [ws("aaa", True), ws("bbb", False), ws("ccc", True)])
    rows = L.optimizer_rows()
    assert sorted(r["owner"] for r in rows) == ["aaa", "ccc"] and all(r["kind"] == "sweep" for r in rows)


def test_a_state_that_cannot_be_read_is_skipped(idle_states, monkeypatch):
    class _Boom:
        def get(self, *_a):
            raise RuntimeError("racing")
    bad = SimpleNamespace(id="x", state=SimpleNamespace(lock=threading.RLock(), slots={"optimization.scan_state": _Boom()}))
    monkeypatch.setattr("motor_ai_sim.workspace.live_workspaces", lambda: [bad])
    assert L.optimizer_rows() == []


def test_nothing_is_imported_when_the_optimizer_was_never_loaded(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "motor_ai_sim.routes.optimization", raising=False)
    assert L.optimizer_rows() == []
