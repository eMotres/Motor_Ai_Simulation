"""Two regions: the other server's cluster / load graphs (peer endpoint + admin route).  docs/regions/DESIGN.md.

The peer endpoint is signed, pull-only, 404 in single-region mode and carries server metrics only; the admin route is
admin only, short-timeout and never stores what it fetches.
"""
from __future__ import annotations

from tests.test_regions import SECRET, _multi, client, env  # noqa: F401  (env is a pytest fixture)

from motor_ai_sim.regions import peer as P


def test_peer_cluster_is_404_without_a_peer_and_with_a_bad_signature(env):
    path = "/api/peer/v1/admin/cluster?range=1h&hist=24h"
    h = P.sign_headers("GET", path, secret=SECRET, region="us")
    assert client.get(path, headers=h).status_code == 404                 # single-region: closed even when signed
    _multi(env["mp"], "eu")
    assert client.get(path).status_code == 404                            # unsigned
    bad = P.sign_headers("GET", path, secret="x" * 40, region="us")
    assert client.get(path, headers=bad).status_code == 404               # wrong secret
    other = P.sign_headers("GET", "/api/peer/v1/admin/cluster", secret=SECRET, region="us")
    assert client.get(path, headers=other).status_code == 404             # signature of another path / query


def test_peer_cluster_answers_signed_with_server_metrics_only(env, monkeypatch):
    from motor_ai_sim import cluster_monitor as CM
    from motor_ai_sim import job_usage as JU
    _multi(env["mp"], "eu")
    monkeypatch.setattr(JU, "user_load_series", lambda s, e, top_n=8: {
        "users": ["secret.person@example.com"], "bucket_s": 60,
        "series": [{"ts": 1, "secret.person@example.com": 12.5, "other": 1.0}]})
    monkeypatch.setattr(CM, "jobs_view", lambda local=False: {
        "running": 1, "queued": 0, "per_user": {"secret.person@example.com": {"running": 1}},
        "items": [{"run_id": "r-123", "owner": "secret.person@example.com", "kind": "sim", "state": "running"}]})
    path = "/api/peer/v1/admin/cluster?range=15m&hist=7d"
    h = P.sign_headers("GET", path, secret=SECRET, region="us")
    r = client.get(path, headers=h)
    assert r.status_code == 200
    j = r.json()
    assert j["region"] == "eu" and j["range"] == "15m" and j["hist"] == "7d"
    assert set(j) >= {"nodes", "app", "live", "history"} and "cluster" in j["nodes"] and "nodes_now" in j["live"]
    assert "secret.person@example.com" not in r.text and "r-123" not in r.text   # no account names, no job ids
    assert j["live"]["user_load"]["users"] == ["user 1"] and j["app"]["jobs"]["items"][0]["owner"] == "user 1"


def test_admin_remote_cluster_requires_admin(env, monkeypatch):
    from motor_ai_sim import auth as A
    monkeypatch.setattr(A, "AUTH_ENFORCE", True)
    assert client.get("/api/admin/regions/remote/cluster").status_code in (401, 403)


def test_admin_remote_cluster_single_mode_and_peer_down(env, monkeypatch):
    j = client.get("/api/admin/regions/remote/cluster").json()
    assert j["configured"] is False and j["reachable"] is False and j["live"] is None and j["nodes"]["nodes"] == []
    _multi(env["mp"], "eu")
    seen = {}

    def down(path, **kw):
        seen["path"], seen["timeout"] = path, kw.get("timeout")
        return None
    monkeypatch.setattr(P, "get", down)
    j = client.get("/api/admin/regions/remote/cluster?range=24h").json()
    assert j["configured"] is True and j["reachable"] is False and j["region"] == "us"
    assert j["history"] == {} and j["live"] is None and j["app"] is None
    assert seen["path"].startswith("/admin/cluster?") and "range=24h" in seen["path"]
    assert seen["timeout"] is not None and seen["timeout"] <= 5           # short: the page never hangs


def test_admin_remote_cluster_passes_the_peer_payload_through(env, monkeypatch):
    _multi(env["mp"], "eu")
    payload = {"region": "us", "range": "1h", "hist": "24h",
               "nodes": {"nodes": [{"id": "us1", "name": "us1", "status": "online", "sample": None}],
                         "cluster": {"nodes": 1, "online": 1, "cores": 24, "cpu_used_cores": 6.0,
                                     "mem_used": 1.0, "mem_total": 2.0}},
               "app": {"jobs": {"running": 2, "queued": 1, "items": []}, "api": {}, "mcp": {}},
               "live": {"nodes": {}, "nodes_now": [], "cluster": {}}, "history": {"us1": [{"ts": 1, "cpu": 25}]}}
    monkeypatch.setattr(P, "get", lambda path, **kw: payload)
    j = client.get("/api/admin/regions/remote/cluster").json()
    assert j["reachable"] is True and j["region"] == "us" and j["nodes"]["cluster"]["cores"] == 24
    assert j["history"]["us1"][0]["cpu"] == 25 and j["app"]["jobs"]["running"] == 2
