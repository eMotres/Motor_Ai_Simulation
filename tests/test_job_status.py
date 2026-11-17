"""Running work per motor and server for the admin's catalog (owner 2026-11-17; ``motor_ai_sim.job_status``): the launcher's
status file (fresh, stale, missing), the application's own queue, the peer server, the per-motor join; admins only.
Fixture files, neutral names, nothing runs."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import job_status as JS

_REAL_APP_JOBS = JS._app_jobs          # before any fixture replaces it
USERS = {"admin": {"email": "admin@example.test", "role": "admin"}, "customer": {"email": "c@example.test", "role": "user"},
         "eng": {"email": "e@example.test", "role": "engineer"}}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write(path, age_s, jobs):
    path.write_text(json.dumps({"schema": 1, "server": "us", "written_at": _iso(time.time() - age_s),
                                "budget": {"cpus": 25, "mem_gb": 80}, "totals": {"cpus_reserved": 3, "cpus_used": 1.5,
                                "mem_reserved_gb": 12, "mem_used_gb": 4.0, "queued": 1}, "jobs": jobs}))


JOBS = [
    {"tag": "opt-1", "state": "running", "server": "us", "kind": "optimisation", "session": "US · a", "die": "DIE A",
     "configuration": "L12", "started_at": "2026-11-17T01:00:00Z", "cpus_reserved": 2, "cpu_used": 1.9, "mem_reserved_gb": 6,
     "mem_now_gb": 3.1, "progress": {"done": 12, "total": 40, "unit": "points"}},
    {"tag": "die-1", "state": "queued", "server": "us", "kind": "study", "die": "DIE A"},
    {"tag": "old-1", "state": "running", "server": "us", "kind": "single", "die": "RENAMED AWAY", "configuration": "L1"},
    {"tag": "anon", "state": "running", "server": "us", "kind": "other"},
    {"tag": "done-1", "state": "finished", "server": "us", "kind": "single", "die": "DIE A", "configuration": "L12"},
]


@pytest.fixture
def status(tmp_path, monkeypatch):
    p = tmp_path / "jobs_status.json"
    monkeypatch.setenv("MOTRES_JOBS_STATUS", str(p))
    monkeypatch.setattr(JS, "_app_jobs", lambda: [])
    monkeypatch.setattr(JS, "_region", lambda: "us")
    return p


def test_a_fresh_file_gives_the_live_jobs_and_the_server_load(status):
    _write(status, 5, JOBS)
    v = JS.local_view()
    assert v["state"] == "live" and v["age_s"] <= 10 and v["budget"] == {"cpus": 25, "mem_gb": 80}
    assert [j["tag"] for j in v["jobs"]] == ["opt-1", "die-1", "old-1", "anon"]        # finished jobs are not running work
    j = v["jobs"][0]
    assert (j["kind"], j["server"], j["state"], j["session"], j["progress"]["done"], j["cpus_reserved"]) == (
        "optimisation", "us", "running", "US · a", 12, 2)


def test_a_stale_file_says_so_with_its_age_and_a_missing_one_is_no_status(status):
    _write(status, 600, JOBS)
    v = JS.local_view()
    assert v["state"] == "stale" and v["age_s"] >= 590
    status.unlink()
    v = JS.local_view()
    assert v["state"] == "missing" and v["totals"] is None and v["jobs"] == []         # never "zero load"
    status.write_text("{not json")
    assert JS.local_view()["state"] == "missing"


def test_the_join_per_motor_per_die_unknown_and_unnamed(status):
    _write(status, 5, JOBS)
    j = JS.by_motor([JS.local_view()], known_dies={"DIE A"})
    assert [x["tag"] for x in j["motors"]["DIE A/L12"]] == ["opt-1"]
    assert [x["tag"] for x in j["dies"]["DIE A"]] == ["die-1"]
    assert [x["tag"] for x in j["unknown"]] == ["old-1"] and [x["tag"] for x in j["unnamed"]] == ["anon"]


def test_the_applications_own_queue_names_its_motor(status, monkeypatch):
    from motor_ai_sim import jobs as J
    status.unlink(missing_ok=True)
    rec = J.JobRecord(run_id="run-7", ws_id="w", owner="admin@example.test", kind="optimizer.scan", state=J.JobState.RUNNING,
                      started_at=time.time() - 30, target={"die": "DIE A", "config": "L20"})

    class Q:
        def live_records(self):
            return [rec]
    monkeypatch.setattr(J, "queue", lambda: Q())
    monkeypatch.setattr(JS, "_app_jobs", _REAL_APP_JOBS)
    v = JS.local_view()
    assert v["state"] == "missing" and [(x["source"], x["kind"], x["die"], x["configuration"]) for x in v["jobs"]] == [
        ("app", "optimizer.scan", "DIE A", "L20")]



def test_the_peer_server_is_read_through_the_signed_link_and_a_silent_peer_is_no_status(monkeypatch):
    from motor_ai_sim.regions import config as RC
    from motor_ai_sim.regions import peer as P
    JS._PEER.update(ts=0.0, value=None)
    monkeypatch.setattr(RC, "peer_configured", lambda: False)
    assert JS.peer_view() is None                                                      # single-region: no second server
    monkeypatch.setattr(RC, "peer_configured", lambda: True)
    monkeypatch.setattr(RC, "peer_region", lambda: "eu")
    calls = []
    monkeypatch.setattr(P, "get", lambda path, **k: calls.append(path) or None)
    v = JS.peer_view(now=1000.0)
    assert v == {"server": "eu", "state": "no_status", "age_s": None, "budget": None, "totals": None, "jobs": []}
    assert calls == ["/jobs/status"]
    JS.peer_view(now=1005.0)
    assert calls == ["/jobs/status"]                                                   # cached ~15 s
    monkeypatch.setattr(P, "get", lambda path, **k: {"server": "eu", "state": "live", "jobs": [{"tag": "x"}]})
    assert JS.peer_view(now=1020.0)["state"] == "live"
    JS._PEER.update(ts=0.0, value=None)


def test_the_route_is_for_admins_only(status, monkeypatch):
    from motor_ai_sim.routes.job_status import router
    _write(status, 5, JOBS)
    monkeypatch.setattr(auth, "resolve_user", lambda a, **_k: USERS.get(str(a or "").split(" ", 1)[-1]))
    monkeypatch.setattr(JS, "peer_view", lambda now=None: None)
    from motor_ai_sim.routes import family as F
    monkeypatch.setattr(F, "_iter_die_entries", lambda: [{"die": "DIE A", "name": "DIE A"}])
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    for who in ("customer", "eng"):
        assert c.get("/api/catalog/jobs", headers={"Authorization": f"Bearer {who}"}).status_code == 403
    r = c.get("/api/catalog/jobs", headers={"Authorization": "Bearer admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servers"][0]["state"] == "live" and body["servers"][0]["jobs"] == 4
    assert "DIE A/L12" in body["motors"] and [x["tag"] for x in body["unnamed"]] == ["anon"]
    assert c.get("/api/peer/v1/jobs/status").status_code == 404                        # unsigned peer call: nothing
