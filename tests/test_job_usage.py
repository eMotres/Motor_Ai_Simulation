"""Per-job machine-time accounting (motor_ai_sim.job_usage): metering a fake
job through the real queue, aggregation per user/client/period, admin-only
routes and CSV export."""
from __future__ import annotations

import csv
import io
import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim import job_usage as U
from motor_ai_sim import jobs as J
from motor_ai_sim import workspace as WS
from motor_ai_sim.api import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    monkeypatch.setenv(WS.ENV_WORKSPACES_ROOT, str(tmp_path / "workspaces"))
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    U.reset()
    yield
    J.reset_queue(J.InProcessQueue(workers=2, field_limit=2))
    U.reset()


def _row(run_id, user, client, cpu_s, ts_end, wall_s=10.0, rss=1000):
    return {"run_id": run_id, "ts_start": ts_end - wall_s, "ts_end": ts_end,
            "user": user, "client": client, "kind": "em", "machine": "L155",
            "node": "eu1", "wall_s": wall_s, "cpu_s": cpu_s, "peak_rss": rss,
            "status": "done", "cpu_method": "exclusive"}


def _burn(seconds: float):
    def _w():
        t = time.process_time()
        x = 0
        while time.process_time() - t < seconds:
            x += 1
        return x
    return _w


# ── accounting on a fake job ─────────────────────────────────────────────────
def test_fake_job_is_metered():
    ws = WS.workspace_for_identity("a@x.com")
    with WS.use_workspace(ws), WS.use_caller({"id": "a@x.com", "is_admin": False}):
        rec = J.make_record("em", run_id="run_fake1")
        rec.body = {"agent": {"client_name": "claude-desktop"}, "duty": "L155"}
        J.queue().submit(rec, _burn(0.4), block=True)
    rows = U.jobs(0, time.time() + 1)
    assert len(rows) == 1
    r = rows[0]
    assert r["run_id"] == "run_fake1" and r["client"] == "claude-desktop"
    assert r["kind"] == "em" and r["machine"] == "L155" and r["status"] == "done"
    assert r["user"] == rec.owner and r["node"]
    assert r["wall_s"] >= 0.35
    # measured, not estimated: at least most of the burnt CPU is attributed
    assert 0.3 <= r["cpu_s"] <= r["wall_s"] * 64 + 1
    assert r["peak_rss"] > 0 and r["cpu_method"] == "exclusive"


def test_failed_and_web_job():
    ws = WS.workspace_for_identity("b@x.com")

    def boom():
        raise RuntimeError("x")
    with WS.use_workspace(ws), WS.use_caller({"id": "b@x.com", "is_admin": False}):
        rec = J.make_record("thermal", run_id="run_fail")
        with pytest.raises(RuntimeError):
            J.queue().submit(rec, boom, block=True)
    r = U.jobs(0, time.time() + 1)[0]
    assert r["status"] == "failed" and r["client"] == "web"


def test_apportion_between_concurrent_jobs(monkeypatch):
    """Two active meters: the tree delta is split by thread CPU weights."""
    cpu = {"tree": 100.0, 1: 0.0, 2: 0.0}
    monkeypatch.setattr(U, "tree_cpu_s", lambda fast=False: cpu["tree"])
    monkeypatch.setattr(U, "tree_rss", lambda: 5)
    monkeypatch.setattr(U, "_own_rss", lambda: 5)
    monkeypatch.setattr(U, "_thread_cpu", lambda nid: cpu.get(nid, 0.0))

    class R:
        def __init__(s, rid, owner):
            s.run_id, s.owner, s.kind, s.body = rid, owner, "em", {}
    m1 = U.start(R("r1", "u1"), sampler=False)
    m1.native_id, m1.thread_cpu_last = 1, 0.0
    m2 = U.start(R("r2", "u2"), sampler=False)
    m2.native_id, m2.thread_cpu_last = 2, 0.0
    cpu.update({"tree": 112.0, 1: 3.0, 2: 1.0})
    U._tick()
    assert m1.cpu_s == pytest.approx(9.0) and m2.cpu_s == pytest.approx(3.0)
    assert U.live("r1")["cpu_s"] == 9.0
    row = U.finish(m1, "cancelled")
    assert row["status"] == "stopped" and row["cpu_method"] == "apportioned"


# ── aggregation ──────────────────────────────────────────────────────────────
def test_summary_per_user_client_period():
    now = 1_000_000.0
    U.record(_row("a1", "alice", "web", 3600, now - 100))
    U.record(_row("a2", "alice", "claude", 7200, now - 200, rss=9000))
    U.record(_row("b1", "bob", "claude", 1800, now - 300))
    U.record(_row("old", "bob", "web", 99999, now - 10 * 86400))
    s = U.summary(now - 86400, now, by="user", cores=16)
    assert [r["key"] for r in s["rows"]] == ["alice", "bob"]      # sorted by CPU-h
    a = s["rows"][0]
    assert a["jobs"] == 2 and a["cpu_h"] == 3.0 and a["peak_rss"] == 9000
    assert a["share_jobs_pct"] == pytest.approx(100 * 3 / 3.5, abs=0.01)
    assert a["share_cluster_pct"] == pytest.approx(100 * 10800 / (16 * 86400), abs=1e-3)
    c = U.summary(now - 86400, now, by="client", cores=16)
    assert {r["key"]: r["cpu_h"] for r in c["rows"]} == {"claude": 2.5, "web": 1.0}
    assert U.summary(now - 30 * 86400, now, cores=16)["rows"][0]["key"] == "bob"
    assert U.cpu_hours("alice", now - 86400, now) == 3.0
    assert U.cpu_hours("bob", now - 86400, now) == 0.5


# ── routes: admin-only + CSV ─────────────────────────────────────────────────
@pytest.mark.parametrize("path", ["/api/admin/usage", "/api/admin/usage/jobs",
                                  "/api/admin/usage.csv"])
def test_usage_admin_only(monkeypatch, path):
    monkeypatch.setattr(auth, "_is_admin_caller", lambda a: (False, None))
    assert client.get(path).status_code == 401
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (False, {"uid": "u", "email": "u@x", "tier": "pro"}))
    assert client.get(path).status_code == 403


def test_usage_routes_and_csv(monkeypatch):
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (True, {"uid": "a", "email": "a@x", "tier": "admin"}))
    now = time.time()
    U.record(_row("a1", "alice", "web", 3600, now - 100))
    U.record(_row("x1", "=cmd|evil", "claude", 60, now - 50))
    r = client.get("/api/admin/usage?days=1&by=user").json()
    assert r["rows"][0]["key"] == "alice"
    j = client.get("/api/admin/usage/jobs?days=1&user=alice").json()["jobs"]
    assert [x["run_id"] for x in j] == ["a1"]
    c = client.get("/api/admin/usage.csv?days=1&by=user")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(c.text)))
    assert rows[0]["key"] == "alice" and float(rows[0]["cpu_h"]) == 1.0
    assert rows[1]["key"].startswith("'=")                         # formula-safe
    d = client.get("/api/admin/usage.csv?days=1&detail=jobs").text
    assert "run_id" in d.splitlines()[0] and "a1" in d
    assert client.get(f"/api/admin/usage?start={now}&end={now - 5}").status_code == 400


def test_jobs_view_shows_live_cpu(monkeypatch):
    class R:
        run_id, owner, kind, body = "live1", "u", "em", {}
    m = U.start(R(), sampler=False)
    m.cpu_s = 42.0

    class Q:
        def snapshot(self):
            return {"running": 1, "queued": 0,
                    "items": [{"run_id": "live1", "owner": "u", "state": "running", "body": {}}]}
    monkeypatch.setattr(J, "queue", lambda: Q())
    assert CM.jobs_view()["items"][0]["cpu_s"] >= 42.0
