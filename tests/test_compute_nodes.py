"""User-owned compute nodes (docs/BYO_COMPUTE.md): token scoping, leases,
cancel, version pinning, result filing, fair use, admin views.  No solver:
the only job kind exercised is ``selftest.cpu`` (a tiny bounded loop) and a
slow mock handler for the cancel path."""
from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import compute_nodes as CN
from motor_ai_sim import cluster_monitor as CM
from motor_ai_sim import jobs as J
from motor_ai_sim import job_usage as U
from motor_ai_sim.api import app

client = TestClient(app)
V = "9.9.9"
USERS = {"ua": {"uid": "a", "email": "a@x.com", "tier": "pro"},
         "ub": {"uid": "b", "email": "b@x.com", "tier": "pro"},
         "adm": {"uid": "z", "email": "z@x.com", "tier": "admin"}}

_spec = importlib.util.spec_from_file_location(
    "motres_compute_worker",
    Path(__file__).resolve().parents[1] / "deploy" / "compute-worker" / "motres_compute_worker.py")
W = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(W)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    monkeypatch.setenv("MOTRES_PLATFORM_VERSION", V)

    def _who(a):
        u = USERS.get(str(a or ""))
        return (bool(u and u["tier"] == "admin"), u)
    monkeypatch.setattr(auth, "_is_admin_caller", _who)
    CN.reset_memory()
    J.clear_cancelled()
    yield
    CN.reset_memory()


def _ws(tmp_path, who):
    p = tmp_path / ("ws_" + who)
    p.mkdir(exist_ok=True)
    return str(p)


def _node(owner):
    nid, tok = CN.create_node(owner, "box")
    return nid, tok, CN._node(nid)


def _hdr(tok):
    return {"Authorization": "Bearer " + tok}


def _transport(tok):
    def post(path, body):
        r = client.post(path, json=body, headers=_hdr(tok))
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {}
    return post


# ── token scoping ────────────────────────────────────────────────────────────
def test_node_of_user_a_never_leases_user_b_job(tmp_path):
    na, ta, _ = _node("a@x.com")
    nb, tb, _ = _node("b@x.com")
    jb = CN.submit("b@x.com", "wsb", _ws(tmp_path, "b"), "selftest.cpu", {"n": 10})
    r = client.post("/api/nodes/jobs/lease", json={"version": V}, headers=_hdr(ta))
    assert r.status_code == 200 and r.json()["job"] is None
    # nor can A's node touch B's run by id: 404, not 403 (no existence leak)
    for verb in ("progress", "complete", "fail"):
        r = client.post(f"/api/nodes/jobs/{jb['run_id']}/{verb}", json={}, headers=_hdr(ta))
        assert r.status_code in (400, 404)
    r = client.post("/api/nodes/jobs/lease", json={"version": V}, headers=_hdr(tb))
    assert r.json()["job"]["bundle"]["run_id"] == jb["run_id"]


def test_admin_metrics_token_cannot_lease():
    _, admin_tok = CM.create_node("eu1")
    r = client.post("/api/nodes/jobs/lease", json={"version": V}, headers=_hdr(admin_tok))
    assert r.status_code == 401


def test_revoked_token_rejected_and_lease_requeued(tmp_path):
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    CN.lease(nid, node, V)
    assert client.post("/api/nodes/" + nid + "/revoke", headers={"Authorization": "ub"}).status_code == 404
    assert client.post("/api/nodes/" + nid + "/revoke", headers={"Authorization": "ua"}).status_code == 200
    assert client.post("/api/nodes/jobs/lease", json={"version": V}, headers=_hdr(tok)).status_code == 401
    assert CN.get_job(j["run_id"])["state"] == CN.QUEUED


def test_user_sees_only_own_nodes_and_admin_views_are_admin_only():
    CN.create_node("a@x.com", "mine")
    CN.create_node("b@x.com", "theirs")
    r = client.get("/api/nodes", headers={"Authorization": "ua"})
    assert r.status_code == 200
    assert [n["owner"] for n in r.json()["nodes"]] == ["a@x.com"]
    assert client.get("/api/nodes").status_code == 401
    assert client.get("/api/admin/user-nodes", headers={"Authorization": "ua"}).status_code == 403
    r = client.get("/api/admin/user-nodes", headers={"Authorization": "adm"})
    assert r.status_code == 200 and len(r.json()["nodes"]) == 2
    assert client.get("/api/admin/user-nodes/jobs", headers={"Authorization": "ua"}).status_code == 403
    assert client.get("/api/admin/user-nodes/audit", headers={"Authorization": "adm"}).status_code == 200


def test_add_node_returns_token_once_and_install_command():
    r = client.post("/api/nodes", json={"name": "Hetzner 1"}, headers={"Authorization": "ua"})
    d = r.json()
    assert d["token"].startswith(CN.TOKEN_PREFIX)
    assert d["token"] in d["install"]["script"] and "install.sh" in d["install"]["script"]
    assert d["token"] not in CN._f("user_nodes.json").read_text()
    listed = client.get("/api/nodes", headers={"Authorization": "ua"}).text
    assert d["token"] not in listed


def test_worker_paths_pass_the_door_and_user_paths_do_not():
    assert auth.anonymous_allowed("/api/nodes/jobs/lease")
    assert auth.anonymous_allowed("/api/nodes/jobs/run-abc/progress")
    assert auth.anonymous_allowed("/api/nodes/heartbeat")
    assert not auth.anonymous_allowed("/api/nodes")
    assert not auth.anonymous_allowed("/api/nodes/jobs/submit")


# ── leases ───────────────────────────────────────────────────────────────────
def test_lease_timeout_requeues_then_fails(tmp_path):
    nid, _, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    t = 1000.0
    for attempt in range(1, CN.MAX_ATTEMPTS + 1):
        got = CN.lease(nid, node, V, now=t)
        assert got["bundle"]["attempt"] == attempt
        t += CN.LEASE_S + 1
        CN.reap(t)
    assert CN.get_job(j["run_id"])["state"] == CN.FAILED
    assert "lease" in CN.get_job(j["run_id"])["error"]


def test_progress_renews_lease(tmp_path):
    nid, _, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    CN.lease(nid, node, V, now=1000.0)
    CN.progress(nid, node, j["run_id"], {"frac": 0.5}, now=1000.0 + CN.LEASE_S - 1)
    CN.reap(1000.0 + CN.LEASE_S + 5)
    assert CN.get_job(j["run_id"])["state"] == CN.LEASED


# ── version pinning ──────────────────────────────────────────────────────────
def test_version_mismatch_refused(tmp_path):
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    r = client.post("/api/nodes/jobs/lease", json={"version": "0.0.1"}, headers=_hdr(tok))
    assert r.status_code == 409 and "version" in r.json()["detail"]
    assert CN.get_job(j["run_id"])["state"] == CN.QUEUED
    # a worker handed a bundle for another version refuses it itself too
    got = CN.lease(nid, node, V)
    got["bundle"]["platform_version"] = "1.0.0"
    got["signature"] = W.sign(tok, got["bundle"])
    assert W.execute(_transport(tok), tok, got) == "refused"


def test_worker_refuses_bad_signature(tmp_path):
    nid, tok, node = _node("a@x.com")
    CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    got = CN.lease(nid, node, V)
    got["bundle"]["body"] = {"n": 5}                   # tampered in transit
    assert W.execute(_transport(tok), tok, got) == "refused"


# ── end to end: results land in the owner's workspace ───────────────────────
def test_result_lands_in_owner_workspace_and_job_table_says_where(tmp_path):
    wa, wb = _ws(tmp_path, "a"), _ws(tmp_path, "b")
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", wa, "selftest.cpu", {"n": 100})
    got = CN.lease(nid, node, V)
    assert W.execute(_transport(tok), tok, got, progress_s=0.05) == "done"
    p = CN.result_path(wa, j["run_id"])
    doc = json.loads(p.read_text())
    assert doc["result"]["n"] == 100 and doc["provenance"]["node_id"] == nid
    assert doc["provenance"]["version"] == V
    assert not (Path(wb) / CN.RESULTS_DIR).exists()
    rows = client.get("/api/nodes/jobs/mine", headers={"Authorization": "ua"}).json()["jobs"]
    assert rows[0]["where"] == "node:" + nid and rows[0]["state"] == "done"
    assert client.get("/api/nodes/jobs/mine", headers={"Authorization": "ub"}).json()["jobs"] == []


def test_complete_validates_signature_and_size(tmp_path, monkeypatch):
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    CN.lease(nid, node, V)
    prov = {"cpu_s": 1, "wall_s": 1, "version": V}
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/complete", headers=_hdr(tok),
                    json={"result": {"x": 1}, "signature": "00", "provenance": prov})
    assert r.status_code == 400
    monkeypatch.setattr(CN, "MAX_RESULT_BYTES", 10)
    big = {"x": "y" * 100}
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/complete", headers=_hdr(tok),
                    json={"result": big, "provenance": prov,
                          "signature": W.sign(tok, {"run_id": j["run_id"], "result": big})})
    assert r.status_code == 413
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/complete", headers=_hdr(tok),
                    json={"result": {}, "provenance": {"cpu_s": -1, "wall_s": 0}})
    assert r.status_code == 400


# ── cancel ───────────────────────────────────────────────────────────────────
def test_cancel_propagates_to_the_worker(tmp_path):
    started = threading.Event()

    def slow(body):
        started.set()
        for _ in range(400):
            J.check_cancelled()
            time.sleep(0.01)
        return {"finished": True}
    J.register_handler("test.slow", slow)
    try:
        nid, tok, node = _node("a@x.com")
        j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "test.slow", {})
        got = CN.lease(nid, node, V)
        # another account may not stop it
        assert client.post(f"/api/jobs/{j['run_id']}/cancel",
                           headers={"Authorization": "ub"}).status_code == 403
        out = {}
        th = threading.Thread(target=lambda: out.setdefault(
            "s", W.execute(_transport(tok), tok, got, progress_s=0.05)))
        th.start()
        started.wait(5)
        r = client.post(f"/api/jobs/{j['run_id']}/cancel", headers={"Authorization": "ua"})
        assert r.status_code == 200 and r.json()["cancelled"]
        th.join(10)
        assert out["s"] == "cancelled"
        assert CN.get_job(j["run_id"])["state"] == CN.CANCELLED
    finally:
        J.HANDLERS.pop("test.slow", None)


def test_cancel_queued_job_is_immediate(tmp_path):
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    assert CN.cancel(j["run_id"], "a@x.com")["state"] == CN.CANCELLED
    assert CN.lease(nid, node, V) is None


# ── routing + fair use ───────────────────────────────────────────────────────
def test_routing_preferences(tmp_path):
    nid, _, _ = _node("a@x.com")
    assert CN.route("a@x.com", "selftest.cpu") == "platform"          # default
    CN.set_pref("a@x.com", CN.PREF_OWN_FIRST)
    assert CN.route("a@x.com", "selftest.cpu") == "platform"          # node offline
    CN.heartbeat(nid, {"cores": 8}, V)
    assert CN.route("a@x.com", "selftest.cpu") == "remote"
    assert CN.route("a@x.com", "no.such.kind") == "platform"
    CN.set_pref("b@x.com", CN.PREF_OWN_ONLY)
    assert CN.route("b@x.com", "selftest.cpu") == "remote"            # waits for a node
    r = client.put("/api/nodes/prefs", json={"pref": "bogus"}, headers={"Authorization": "ua"})
    assert r.status_code == 400


def test_fair_use_excludes_own_node_cpu(tmp_path):
    t = time.time()
    U.record({"run_id": "p1", "ts_start": t - 10, "ts_end": t - 5, "user": "a@x.com",
              "client": "web", "kind": "k", "machine": "", "node": "eu1", "wall_s": 5,
              "cpu_s": 3600, "peak_rss": 0, "status": "done", "cpu_method": "exclusive",
              "wait_s": 0})
    nid, tok, node = _node("a@x.com")
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {"n": 10})
    CN.lease(nid, node, V)
    res = {"n": 10}
    CN.complete(nid, node, j["run_id"],
                {"result": res, "signature": CN.sign(node["token_hash"],
                                                     {"run_id": j["run_id"], "result": res}),
                 "provenance": {"cpu_s": 7200, "wall_s": 100, "version": V}})
    assert U.cpu_hours("a@x.com", t - 100, t + 100) == pytest.approx(1.0)
    assert U.cpu_hours("a@x.com", t - 100, t + 100, fair_use=False) == pytest.approx(3.0)
    rows = U.jobs(t - 100, t + 100, user="a@x.com")
    own = [r for r in rows if r["own_node"]]
    assert own and own[0]["node"] == CN.HIST_PREFIX + nid
    assert U.summary(t - 100, t + 100, cores=1)["total_cpu_h"] == pytest.approx(1.0)
