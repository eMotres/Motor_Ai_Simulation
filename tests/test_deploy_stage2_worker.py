"""Deploy stage 2: the API process never runs a queued computation, the worker drains, and the live status shows both."""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import jobs as J
from motor_ai_sim import live_status as L
from motor_ai_sim import roles


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(roles.ENV_ROLE, raising=False)
    J.set_draining(False)
    J.reset_queue(J.InProcessQueue(workers=2, persist=False))
    yield
    J.set_draining(False)
    J.reset_queue()
    from motor_ai_sim import job_usage as _U          # a metered job leaves CPU-seconds in the process-wide accumulator
    _U._USER_ACC.clear()


def _app():
    app = FastAPI()

    @app.get("/work")
    @J.queued("test.work")
    def work():
        return {"done": True}
    return TestClient(app)


# ── the admission guard ──────────────────────────────────────────────────────

def test_all_role_runs_the_job_as_before():
    assert _app().get("/work").json()["done"] is True


def test_api_role_refuses_a_queued_computation_with_503(monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    r = _app().get("/work")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert "compute worker" in r.json()["detail"]


def test_api_role_refuses_admit_and_direct_submit(monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    with pytest.raises(J.ComputeNotHere):
        with J.admit("optimizer.scan"):
            pass
    with pytest.raises(J.ComputeNotHere):
        J.queue().submit(J.make_record("agent.coupled"), lambda: 1, block=False)


def test_a_draining_worker_takes_no_new_job_but_a_running_one_finishes(monkeypatch):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    q = J.queue()
    seen = {}

    def slow():
        time.sleep(0.2)
        return "finished"

    import threading
    t = threading.Thread(target=lambda: seen.setdefault("out", J.run_job("test.slow", slow)))
    t.start()
    time.sleep(0.05)
    assert q.snapshot()["running"] == 1
    assert J.set_draining(True) is True
    r = _app().get("/work")
    assert r.status_code == 503 and "being updated" in r.json()["detail"]
    t.join(5)
    assert seen["out"] == "finished", "the job that was running when the drain began must finish"
    assert q.snapshot()["running"] == 0
    assert J.set_draining(False) is False
    assert _app().get("/work").status_code == 200


def test_a_nested_solve_inside_an_admitted_job_is_not_refused_by_the_drain(monkeypatch):
    """Re-entrancy: the inner call of a job that is already admitted runs inline - a drain must not break it."""
    monkeypatch.setenv(roles.ENV_ROLE, "worker")

    def outer():
        J.set_draining(True)                # the drain begins while the job runs ...
        return J.run_job("test.inner", lambda: "inner ok")      # ... its own nested call still runs

    assert J.run_job("test.outer", outer) == "inner ok"


# ── stale records ────────────────────────────────────────────────────────────

def test_recover_stale_records_marks_running_and_queued_interrupted(tmp_path):
    ws = tmp_path / "w1"
    ws.mkdir()
    (ws / J.JOBS_FILE).write_text(json.dumps({"jobs": [
        {"run_id": "a", "state": "running", "position": 0}, {"run_id": "b", "state": "queued", "position": 2},
        {"run_id": "c", "state": "done"}]}), encoding="utf-8")
    assert J.recover_stale_records(tmp_path) == 2
    rows = json.loads((ws / J.JOBS_FILE).read_text(encoding="utf-8"))["jobs"]
    assert [r["state"] for r in rows] == ["interrupted", "interrupted", "done"]
    assert J.recover_stale_records(tmp_path) == 0


# ── the worker's state + drain routes ────────────────────────────────────────

def _state_client(monkeypatch, client_host="testclient"):
    from motor_ai_sim.routes import worker_state as W
    app = FastAPI()
    app.include_router(W.router)
    return TestClient(app, client=(client_host, 50000)), W


def test_state_and_drain_are_closed_to_an_outside_caller(monkeypatch):
    c, _ = _state_client(monkeypatch)
    assert c.get("/api/worker/state").status_code == 403
    assert c.post("/api/worker/drain", json={"on": True}).status_code == 403
    assert not J.is_draining()


def test_state_and_drain_open_to_loopback_and_to_the_internal_key(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    monkeypatch.setenv(roles.ENV_INSTANCE, "worker")
    c, _ = _state_client(monkeypatch, "127.0.0.1")
    st = c.get("/api/worker/state").json()
    assert st["role"] == "worker" and st["draining"] is False and "live" in st and "jobs" in st
    assert c.post("/api/worker/drain", json={"on": True}).json()["draining"] is True
    assert c.get("/api/worker/state").json()["draining"] is True
    assert c.post("/api/worker/drain", json={"on": False}).json()["draining"] is False
    # from another container: only with the deployment key
    c2, _ = _state_client(monkeypatch, "172.18.0.5")
    assert c2.get("/api/worker/state").status_code == 403
    assert c2.get("/api/worker/state", headers={roles.INTERNAL_HEADER: "wrong"}).status_code == 403
    assert c2.get("/api/worker/state", headers=roles.internal_headers()).status_code == 200
    # a loopback call that came through a proxy is not the deploy script
    assert c.get("/api/worker/state", headers={"X-Real-IP": "1.2.3.4"}).status_code == 403


def test_the_door_lets_the_internal_key_through_on_a_closed_server(monkeypatch):
    from types import SimpleNamespace
    from motor_ai_sim.auth import _deploy_loopback
    monkeypatch.setenv("AUTH_SECRET", "unit-test-secret")

    def req(path, host, headers=None):
        return SimpleNamespace(url=SimpleNamespace(path=path), client=SimpleNamespace(host=host), headers=headers or {})
    key = roles.internal_headers()
    assert _deploy_loopback(req("/api/worker/state", "172.18.0.5", key))
    assert _deploy_loopback(req("/api/worker/drain", "172.18.0.5", key))
    assert not _deploy_loopback(req("/api/worker/state", "172.18.0.5"))
    assert not _deploy_loopback(req("/api/admin/users", "172.18.0.5", key)), "the key opens only the worker pair"
    assert not _deploy_loopback(req("/api/admin/live", "172.18.0.5", key)), "live stays loopback-only for the deploy script"


def test_internal_key_follows_the_secret(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "one")
    a = roles.internal_key()
    monkeypatch.setenv("AUTH_SECRET", "two")
    assert a and roles.internal_key() != a and len(a) == 64


# ── live status: the API shows the worker ────────────────────────────────────

def _worker_state(running=1, queued=0, requests=2, draining=False):
    return {"draining": draining, "build": "abc123", "instance": "worker", "uptime_s": 120.0,
            "live": {"requests_total": requests,
                     "jobs": {"running": running, "queued": queued, "kinds": {"coupled.run": running}},
                     "modules": {"thermal": {"users": 0, "requests": requests, "oldest_request_s": 410.0}}},
            "jobs": [{"run_id": "r1", "kind": "coupled.run", "owner": "a@x", "state": "running"}]}


def _api_snapshot(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(L, "_tabs", {})
    monkeypatch.setattr(L, "_inflight", {})
    monkeypatch.setattr(L, "_notice_path", lambda: tmp_path / "notice.json")
    L.note_tab("u@x", "s1", "thermal", now=1000.0)
    return L.local_snapshot(now=1010.0)


def test_api_snapshot_merges_the_workers_jobs_and_requests(monkeypatch, tmp_path):
    snap = L.merge_worker(_api_snapshot(monkeypatch, tmp_path), _worker_state())
    assert snap["jobs"]["running"] == 1 and snap["jobs"]["kinds"] == {"coupled.run": 1}
    t = snap["modules"]["thermal"]
    assert t["users"] == 1 and t["requests"] == 2 and t["oldest_request_s"] == 410.0
    assert snap["requests_total"] == 2
    w = snap["worker"]
    assert w["reachable"] and w["draining"] is False and w["build"] == "abc123" and w["items"][0]["run_id"] == "r1"
    assert not L.idle(snap, ["all"]) and not L.idle(snap, ["thermal"])
    assert L.idle(snap, ["production"]) is False, "a running job anywhere keeps every module busy (stage-1 rule)"


def test_an_idle_worker_leaves_the_site_idle(monkeypatch, tmp_path):
    snap = _api_snapshot(monkeypatch, tmp_path)
    snap["modules"]["thermal"]["users"] = 0
    snap = L.merge_worker(snap, _worker_state(running=0, requests=0))
    assert L.idle(snap, ["all"]) and snap["worker"]["reachable"]


def test_an_unreachable_worker_is_reported_and_keeps_the_record_file_view(monkeypatch, tmp_path):
    snap = _api_snapshot(monkeypatch, tmp_path)
    ws = tmp_path / "ws" / "w1"
    ws.mkdir(parents=True)
    (ws / ".jobs.json").write_text(json.dumps({"jobs": [{"kind": "transient", "state": "queued"}]}))
    snap = L.local_snapshot(now=1010.0)
    snap = L.merge_worker(snap, {})                       # fetch_state() -> None / empty
    assert snap["worker"]["reachable"] is False
    assert snap["jobs"]["queued"] == 1, "unknown worker: stay conservative, the stale-able file view says busy"


def test_snapshot_asks_the_worker_only_in_the_api_role(monkeypatch, tmp_path):
    from motor_ai_sim import worker_client
    calls = []
    monkeypatch.setattr(worker_client, "fetch_state", lambda timeout=2.0: calls.append(1) or _worker_state())
    _api_snapshot(monkeypatch, tmp_path)
    assert "worker" not in L.snapshot(now=1010.0) and not calls            # role all: untouched
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    assert L.snapshot(now=1010.0)["worker"]["reachable"] and calls == [1]


def test_worker_role_counts_its_own_queue_not_the_record_files(monkeypatch, tmp_path):
    monkeypatch.setenv(roles.ENV_ROLE, "worker")
    _api_snapshot(monkeypatch, tmp_path)
    ws = tmp_path / "ws" / "w1"
    ws.mkdir(parents=True)
    (ws / ".jobs.json").write_text(json.dumps({"jobs": [{"kind": "transient", "state": "queued"}]}))
    assert L.local_snapshot(now=1010.0)["jobs"] == {"running": 0, "queued": 0, "kinds": {}}


def test_live_route_local_flag_skips_the_worker(monkeypatch, tmp_path):
    from motor_ai_sim.routes import live as R
    from motor_ai_sim import worker_client
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    _api_snapshot(monkeypatch, tmp_path)
    monkeypatch.setattr(worker_client, "fetch_state", lambda timeout=2.0: _worker_state(requests=7))
    app = FastAPI()
    app.include_router(R.router)
    c = TestClient(app, client=("127.0.0.1", 5))
    assert c.get("/api/admin/live").json()["requests_total"] == 7            # merged
    assert c.get("/api/admin/live?local=1").json()["requests_total"] == 0     # this process only (the drain check)


def test_jobs_view_of_the_api_comes_from_the_worker(monkeypatch):
    from motor_ai_sim import cluster_monitor as CM
    from motor_ai_sim import worker_client
    monkeypatch.setenv(roles.ENV_ROLE, "api")
    view = {"running": 1, "queued": 0, "workers": 2, "oldest_wait_s": 0.0, "per_user": {}, "items": [{"run_id": "r1"}]}
    monkeypatch.setattr(worker_client, "fetch_state", lambda timeout=2.0: {"jobs_view": view})
    assert CM.jobs_view() == view
    monkeypatch.setattr(worker_client, "fetch_state", lambda timeout=2.0: None)
    out = CM.jobs_view()
    assert out["worker_reachable"] is False and out["items"] == []


def test_worker_cli_idle_rule():
    from motor_ai_sim import worker
    assert worker.idle({"live": {"jobs": {"running": 0, "queued": 0}, "requests_total": 0}})
    assert not worker.idle({"live": {"jobs": {"running": 1, "queued": 0}, "requests_total": 0}})
    assert not worker.idle({"live": {"jobs": {"running": 0, "queued": 1}, "requests_total": 0}})
    assert not worker.idle({"live": {"jobs": {"running": 0, "queued": 0}, "requests_total": 1}})
    assert worker.main(["bogus"]) == 64


# ── background work: what a blue-green drain must wait for besides requests ────────────────────────────────────────────

def test_background_threads_count_as_busy_for_the_drain(monkeypatch, tmp_path):
    _api_snapshot(monkeypatch, tmp_path)
    assert L.local_snapshot(now=1010.0)["busy_total"] == 0
    with L.background("design-agent"):
        snap = L.local_snapshot(now=1010.0)
        assert snap["busy_total"] == 1 and snap["background"] == {"total": 1, "kinds": {"design-agent": 1}}
        tok = L._begin("/api/support/chat")
        assert L.local_snapshot(now=1010.0)["busy_total"] == 2          # a request in flight counts too
        L._end(tok)
    assert L.local_snapshot(now=1010.0)["busy_total"] == 0


def test_the_design_assistants_answer_thread_is_tracked():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "design_agent" / "runner.py").read_text(encoding="utf-8")
    assert "target=_run_turn_tracked" in src and 'live_status.background("design-agent")' in src
