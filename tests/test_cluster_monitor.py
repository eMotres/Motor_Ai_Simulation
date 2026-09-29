"""Admin -> Servers: node-token ingest auth, history downsampling, offline
detection, admin-only routes and redaction (motor_ai_sim.cluster_monitor)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim import job_usage as U
from motor_ai_sim.api import app
from motor_ai_sim.routes import cluster as cluster_routes

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
                        lambda a: (True, {"uid": "a", "email": "a@x", "role": "admin"}))


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
    ("get", "/api/admin/load/live"),
])
def test_admin_only(monkeypatch, method, path):
    monkeypatch.setattr(auth, "_is_admin_caller", lambda a: (False, None))
    assert getattr(client, method)(path).status_code == 401
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda a: (False, {"uid": "u", "email": "u@x", "role": "user"}))
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


# ── out-of-app load (heavy work outside the job queue: containers/host) ─────
def _minute() -> int:
    return int(time.time() // 60) * 60


def test_outside_app_subtracts_job_cpu_and_clamps_at_zero(monkeypatch, caplog):
    """deploy-api-1's own docker-stats CPU minus the job CPU job_usage already
    attributed to users = "app-overhead"; if job CPU somehow reads HIGHER than
    the container's (sampling skew) it clamps at 0 and logs, never goes
    negative."""
    m0 = _minute()
    nid, _ = CM.create_node("eu1")
    # 6 CPU-seconds of job time attributed to alice in this minute, on "eu1"
    U._USER_ACC[(m0, nid, "alice", "web")] = 6.0
    assert U.flush_user_attribution(now=m0) == 1

    # deploy-api-1 reads 20 % over the bucket -> job's share is 100*6/60 = 10 %
    # -> 10 % left over as app-overhead
    CM.ingest(nid, _sample(containers=[{"name": "deploy-api-1", "cpu": 20.0, "mem": 1e8}]),
              now=m0 + 5)
    out = CM.outside_app_series(m0 - 60, m0 + 60, top_n=8)
    row = next(p for p in out["series"] if p["ts"] == m0)
    assert row[CM.APP_OVERHEAD_KEY] == pytest.approx(10.0, abs=0.05)
    assert out["app_overhead_key"] == CM.APP_OVERHEAD_KEY

    # now make the job CPU (12 s = 20 %) exceed the container's own 20 % reading
    U._USER_ACC[(m0, nid, "alice", "web")] = 12.0
    U.flush_user_attribution(now=m0)
    caplog.clear()
    with caplog.at_level("WARNING"):
        out2 = CM.outside_app_series(m0 - 60, m0 + 60, top_n=8)
    row2 = next(p for p in out2["series"] if p["ts"] == m0)
    assert row2.get(CM.APP_OVERHEAD_KEY, 0.0) == 0.0            # clamped, never negative
    assert any("clamped to 0" in r.message for r in caplog.records)


def test_outside_app_series_ranks_containers_and_buckets_the_rest_as_other():
    m0 = _minute()
    nid, _ = CM.create_node("eu1")
    CM.ingest(nid, _sample(containers=[
        {"name": "mesher_1", "cpu": 300.0, "mem": 1e9},
        {"name": "prof_a", "cpu": 50.0, "mem": 1e8},
        {"name": "prof_b", "cpu": 10.0, "mem": 1e8},
        {"name": "deploy-web-1", "cpu": 1.0, "mem": 1e7},   # never runs jobs -> all overhead
    ], procs=[
        {"name": "stagea_worker", "cpu": 40.0, "rss": 2e8, "container": ""},
        {"name": "sshd", "cpu": 0.5, "rss": 1e7, "container": ""},
    ]), now=m0 + 5)
    out = CM.outside_app_series(m0 - 60, m0 + 60, top_n=2)
    assert out["items"] == ["mesher_1", "prof_a"]              # top 2 by CPU (300, 50 > host's 40.5)
    row = next(p for p in out["series"] if p["ts"] == m0)
    assert row["mesher_1"] == pytest.approx(300.0)
    assert row["prof_a"] == pytest.approx(50.0)
    assert row[CM.OTHER_KEY] == pytest.approx(50.5)             # host (40.5) + prof_b (10)
    assert row[CM.APP_OVERHEAD_KEY] == pytest.approx(1.0)      # deploy-web-1, no job to subtract
    # NOT the literal "other": job_usage.user_load_series uses that key for its
    # own overflow bucket, and the web panel merges both series into one chart
    # by ts (mergeLoadSeries) -- a shared key would silently clobber one value.
    assert CM.OTHER_KEY != "other"
    assert "other" not in row


def test_outside_app_now_lists_non_app_containers_with_uptime():
    nid, _ = CM.create_node("eu1")
    now = time.time()
    CM.ingest(nid, _sample(containers=[
        {"name": "deploy-api-1", "cpu": 15.0, "mem": 1e8, "created": now - 3600},
        {"name": "mesher_1", "cpu": 210.0, "mem": 2e9, "created": now - 120},
    ]), now=now)
    items = CM.outside_app_now()
    assert [i["name"] for i in items] == ["mesher_1"]          # app container excluded
    assert items[0]["cpu"] == 210.0 and items[0]["node"] == nid
    assert items[0]["uptime_s"] == pytest.approx(120.0, abs=2)


def test_load_live_includes_outside_app_and_monitoring_since(as_admin):
    cluster_routes._LOAD_CACHE.update(key=None, ts=0.0, data=None)   # a prior test's cache
    nid, _ = CM.create_node("eu1")
    CM.ingest(nid, _sample(containers=[{"name": "mesher_1", "cpu": 80.0, "mem": 1e9}]))
    r = client.get("/api/admin/load/live?range=1h").json()
    assert "outside_app" in r and "series" in r["outside_app"] and "now" in r["outside_app"]
    assert r["outside_app"]["now"][0]["name"] == "mesher_1"
    assert r["monitoring_since"] is not None


def test_load_live_nodes_now_carries_cores_and_ram_for_the_per_server_strip(as_admin):
    """The left "CPU % / RAM % per server" chart's legend/tooltip and the
    per-server strip above it need each node's thread/physical-core count and
    total RAM -- not just the history points, which only carry cpu/mem %."""
    cluster_routes._LOAD_CACHE.update(key=None, ts=0.0, data=None)
    nid, _ = CM.create_node("eu1")
    CM.ingest(nid, _sample(cpu=62.0, cores_physical=8,
                          mem={"used": 4e9, "total": 16e9}))
    r = client.get("/api/admin/load/live?range=1h").json()
    now = next(n for n in r["nodes_now"] if n["id"] == nid)
    assert now["cores"] == 4                                    # 4 per_core entries in _sample()
    assert now["cores_physical"] == 8
    assert now["cpu"] == 62.0
    assert now["mem_total"] == pytest.approx(16e9)
    # mem_used: already in every sample the node agent has ever sent
    # (meminfo(), unchanged) -- this only asserts the route selects it too,
    # for the per-server strip's "23.4 / 62.7 GB (37 %)" RAM label. No node
    # agent change or reinstall needed.
    assert now["mem_used"] == pytest.approx(4e9)
    # RAM history is already there (mergeNodeSeries/CPU-RAM chart bug was
    # frontend-only -- point_of() has always carried "mem"): a fresh
    # regression guard so this doesn't silently regress again.
    assert CM.history(nid, "24h")[0]["mem"] == pytest.approx(25.0)


def test_load_live_nodes_now_cores_physical_is_none_for_an_older_agent(as_admin):
    """An agent from before cores_physical existed (or physical_cores()
    returning 0, "unknown") must not surface as a nonsensical "0 cores"."""
    cluster_routes._LOAD_CACHE.update(key=None, ts=0.0, data=None)
    nid, _ = CM.create_node("eu1")
    CM.ingest(nid, _sample())                                     # no cores_physical key at all
    r = client.get("/api/admin/load/live?range=1h").json()
    now = next(n for n in r["nodes_now"] if n["id"] == nid)
    assert now["cores_physical"] is None
