"""Admin -> Servers: node-token ingest auth, history downsampling, offline
detection, admin-only routes and redaction (motor_ai_sim.cluster_monitor)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim.api import app

client = TestClient(app)


def _sample(cpu=50.0, **extra):
    s = {"hostname": "h", "cpu": {"total": cpu, "per_core": [cpu] * 4},
         "load": [1.0, 0.5, 0.2], "mem": {"used": 4e9, "total": 16e9},
         "disks": [{"mount": "/", "used": 50, "total": 100}],
         "net": {"rx_bps": 10, "tx_bps": 20}, "procs": [], "containers": []}
    s.update(extra)
    return s


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path))
    CM.reset_memory()
    yield
    CM.reset_memory()


@pytest.fixture()
def as_admin(monkeypatch):
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (True, {"uid": "a", "email": "a@x", "tier": "admin"}))


def _post(tok, body):
    h = {"Authorization": f"Bearer {tok}"} if tok else {}
    return client.post("/api/admin/nodes/metrics", json=body, headers=h)


# ── ingest auth ──────────────────────────────────────────────────────────────
def test_ingest_accepts_valid_token():
    nid, tok = CM.create_node("eu1")
    r = _post(tok, _sample())
    assert r.status_code == 200 and r.json()["node"] == nid
    assert CM.list_nodes()["nodes"][0]["status"] == "online"


def test_token_stored_hashed_only():
    _, tok = CM.create_node("eu1")
    raw = CM._nodes_file().read_text()
    assert tok not in raw and CM._hash(tok) in raw


@pytest.mark.parametrize("tok", [None, "garbage", "mnode_wrong", "Basic xx"])
def test_ingest_rejects_bad_token(tok):
    CM.create_node("eu1")
    assert _post(tok, _sample()).status_code == 401


def test_ingest_rejects_revoked_token():
    nid, tok = CM.create_node("eu1")
    assert _post(tok, _sample()).status_code == 200
    CM.revoke_node(nid)
    assert _post(tok, _sample()).status_code == 401
    assert CM.list_nodes()["nodes"][0]["status"] == "revoked"


def test_rekey_invalidates_old_token():
    _, old = CM.create_node("eu1")
    _, new = CM.create_node("eu1")
    assert _post(old, _sample()).status_code == 401
    assert _post(new, _sample()).status_code == 200


# ── history downsampling ─────────────────────────────────────────────────────
def test_history_buckets_average_and_resolutions():
    nid, _ = CM.create_node("n")
    import time
    t0 = CM.bucket(time.time() - 3600, CM.COARSE_BUCKET_S)
    # two samples in one minute -> one fine point, mean 30
    CM.ingest(nid, _sample(20), now=t0 + 1)
    CM.ingest(nid, _sample(40), now=t0 + 30)
    # one in the next minute, same 15-min bucket
    CM.ingest(nid, _sample(90), now=t0 + 61)
    fine = CM.history(nid, "24h")
    assert [p["cpu"] for p in fine] == [30.0, 90.0]
    coarse = CM.history(nid, "7d")
    assert len(coarse) == 1 and coarse[0]["cpu"] == pytest.approx(50.0)
    assert fine[0]["mem"] == pytest.approx(25.0) and fine[0]["disk"] == 50.0


def test_history_prunes_old_fine_points():
    nid, _ = CM.create_node("n")
    import time
    now = time.time()
    CM.ingest(nid, _sample(10), now=now - 2 * 86400)   # beyond 24 h, within 7 d
    CM.ingest(nid, _sample(10), now=now)
    assert len(CM.history(nid, "24h")) == 1
    assert len(CM.history(nid, "7d")) == 2


# ── offline detection ────────────────────────────────────────────────────────
def test_offline_after_60s():
    nid, _ = CM.create_node("n")
    CM.ingest(nid, _sample(), now=1000.0)
    assert CM.list_nodes(now=1059.0)["nodes"][0]["status"] == "online"
    v = CM.list_nodes(now=1061.0)
    assert v["nodes"][0]["status"] == "offline"
    assert v["cluster"]["online"] == 0
    CM.create_node("silent")
    assert {n["id"]: n["status"] for n in CM.list_nodes(now=1000.0)["nodes"]}["silent"] == "never"


def test_cluster_summary_totals():
    a, _ = CM.create_node("a")
    b, _ = CM.create_node("b")
    CM.ingest(a, _sample(50), now=1000.0)
    CM.ingest(b, _sample(25), now=1000.0)
    c = CM.list_nodes(now=1001.0)["cluster"]
    assert c["cores"] == 8 and c["cpu_used_cores"] == 3.0 and c["online"] == 2


# ── admin-only ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,path", [
    ("get", "/api/admin/nodes"), ("post", "/api/admin/nodes"),
    ("get", "/api/admin/nodes/x/history"), ("post", "/api/admin/nodes/x/revoke"),
    ("get", "/api/admin/cluster/app"), ("post", "/api/admin/cluster/jobs/r/stop"),
])
def test_admin_only(monkeypatch, method, path):
    monkeypatch.setattr(auth, "_is_admin_caller", lambda a: (False, None))
    assert getattr(client, method)(path).status_code == 401
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (False, {"uid": "u", "email": "u@x", "tier": "pro"}))
    assert getattr(client, method)(path).status_code == 403


def test_node_token_is_not_admin(monkeypatch):
    monkeypatch.setattr(auth, "_is_admin_caller", lambda a: (False, None))
    _, tok = CM.create_node("n")
    r = client.get("/api/admin/nodes", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_admin_flow(as_admin):
    r = client.post("/api/admin/nodes", json={"name": "EU 1"})
    assert r.status_code == 200
    nid, tok = r.json()["id"], r.json()["token"]
    assert nid == "eu-1" and tok.startswith("mnode_")
    assert _post(tok, _sample()).status_code == 200
    v = client.get("/api/admin/nodes").json()
    assert v["nodes"][0]["sample"]["cpu"]["total"] == 50.0
    assert "token_hash" not in str(v)
    assert client.get(f"/api/admin/nodes/{nid}/history").json()["points"]
    app_v = client.get("/api/admin/cluster/app").json()
    assert "p95_ms" in app_v["api"] and "items" in app_v["jobs"]


# ── redaction ────────────────────────────────────────────────────────────────
def test_redact_patterns():
    s = CM.redact("python x.py --token abc123 API_KEY=zzz "
                  "Authorization: Bearer eyJhbGciOi.xyz postgres://u:pw@db/x "
                  "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    for leak in ("abc123", "zzz", "eyJhbGciOi", ":pw@", "ghp_ABCDEF"):
        assert leak not in s
    assert CM.redact("uvicorn") == "uvicorn"


def test_ingest_drops_env_and_cmdline():
    nid, tok = CM.create_node("n")
    body = _sample(procs=[{"name": "python --password hunter2", "cpu": 5,
                           "cmdline": "secret stuff", "env": {"AUTH_SECRET": "s"}}],
                   environ={"X": "y"})
    assert _post(tok, body).status_code == 200
    s = CM.list_nodes()["nodes"][0]["sample"]
    assert "environ" not in s
    p = s["procs"][0]
    assert "cmdline" not in p and "env" not in p and "hunter2" not in p["name"]


def test_mcp_and_latency_counters():
    CM.note_mcp_call(200, now=1000.0)
    CM.note_mcp_call(429, now=1001.0)
    for ms in (10, 20, 30, 40, 1000):
        CM.note_latency(ms, now=1000.0)
    m = CM.app_metrics(now=1010.0)
    assert m["mcp"]["throttled_429"] == 1 and m["api"]["p50_ms"] == 30
    assert m["api"]["p95_ms"] == 1000
    assert CM.app_metrics(now=2000.0)["api"]["requests"] == 0
