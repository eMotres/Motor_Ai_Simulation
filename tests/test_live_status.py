"""Live status for module-wise deploys (2026-10-18): users per tab, requests in flight, jobs, the maintenance notice."""
from __future__ import annotations

import json

from motor_ai_sim import live_status as L


def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "_tabs", {})
    monkeypatch.setattr(L, "_inflight", {})
    monkeypatch.setenv("WORKSPACES_ROOT", str(tmp_path / "ws"))
    monkeypatch.setattr(L, "_notice_path", lambda: tmp_path / "notice.json")


def test_module_of_paths():
    assert L.module_of("/api/production/CILN28/G2-L40/bom") == "production"
    assert L.module_of("/api/design-agent/conversations") == "designer"
    assert L.module_of("/api/propellers/x/s1") == "compare"
    assert L.module_of("/api/whatever") == "other"
    assert L.module_of("/assets/app.js") is None


def test_users_requests_and_jobs_decide_idleness(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    L.note_tab("a@x", "s1", "production", now=1000.0)
    L.note_tab("b@x", "s2", "compare", now=1000.0)
    L.note_tab("c@x", "s3", "compare", now=700.0)            # stale: outside the window
    snap = L.snapshot(include_users=True, now=1050.0)
    assert snap["users_total"] == 2
    assert snap["modules"]["production"]["users"] == 1 and snap["modules"]["compare"]["accounts"] == ["b@x"]
    assert not L.idle(snap, ["production"]) and L.idle(snap, ["designer"]) and not L.idle(snap, ["all"])
    tok = L._begin("/api/design-agent/conversations")
    snap = L.snapshot(now=1050.0)
    assert snap["modules"]["designer"]["requests"] == 1 and not L.idle(snap, ["designer"])
    L._end(tok)
    ws = tmp_path / "ws" / "w1"
    ws.mkdir(parents=True)
    (ws / ".jobs.json").write_text(json.dumps({"jobs": [{"kind": "transient", "state": "running"},
                                                        {"kind": "x", "state": "done"}]}))
    snap = L.snapshot(now=1050.0)
    assert snap["jobs"]["running"] == 1 and not L.idle(snap, ["designer"])


def test_notice_expires(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    n = L.set_notice({"en": "Quick update in 2 minutes", "zh": "2 分钟后短暂更新"}, minutes=2)
    assert L.read_notice(now=n["at"] + 60)["message"]["en"].startswith("Quick")
    assert L.read_notice(now=n["until"] + 1) is None
    L.clear_notice()
    assert L.read_notice() is None


def test_routes_admin_or_loopback(monkeypatch, tmp_path):
    _reset(monkeypatch, tmp_path)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import live as R
    app = FastAPI()
    app.include_router(R.router)
    c = TestClient(app)                                   # client host "testclient": not loopback
    monkeypatch.setattr(R, "caller_identity", lambda a=None: {"id": "u@x", "is_admin": False})
    assert c.get("/api/admin/live").status_code == 403
    assert c.put("/api/admin/notice", json={"message": {"en": "x"}, "minutes": 2}).status_code == 403
    assert c.get("/api/notice").json() == {"notice": None}
    monkeypatch.setattr(R, "caller_identity", lambda a=None: {"id": "a@x", "is_admin": True})
    assert c.get("/api/admin/live").status_code == 200
    assert c.put("/api/admin/notice", json={"message": {"en": "x"}, "minutes": 2}).status_code == 200
    assert c.get("/api/notice").json()["notice"]["message"]["en"] == "x"
    assert c.delete("/api/admin/notice").json() == {"notice": None}
