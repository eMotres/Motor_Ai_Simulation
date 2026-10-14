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
from typing import Any, Dict

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
USERS = {"ua": {"uid": "a", "email": "a@x.com", "role": "user"},
         "ub": {"uid": "b", "email": "b@x.com", "role": "user"},
         "ue": {"uid": "e", "email": "e@x.com", "role": "engineer"},
         "adm": {"uid": "z", "email": "z@x.com", "role": "admin"}}

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
        return (bool(u and u["role"] == "admin"), u)
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


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 2: idle sharing (docs/BYO_COMPUTE.md section 14)
# ═════════════════════════════════════════════════════════════════════════════
ADMIN = "z@x.com"


def _fresh(nid):
    return CN._node(nid)


def _host(owner="b@x.com", share=True, cores=8, ram_gb=16, **caps):
    """A node of ``owner`` that has reported its size; opted in unless told not."""
    nid, tok, _ = _node(owner)
    CN.heartbeat(nid, {"cores": cores, "mem": {"total": ram_gb * 1024 ** 3, "used": 1}}, V)
    if share:
        CN.set_share(nid, owner, enabled=True, **caps)
    return nid, tok


def _shared_job(tmp_path, body=None, kind="selftest.cpu"):
    return CN.submit(ADMIN, "wsz", _ws(tmp_path, "z"), kind, body or {"n": 10},
                     shareable=True, owner_is_admin=True)


def _events(name):
    return [e for e in CN.audit_tail(None, 500) if e["event"] == name]


# ── rule 1: opt-in, off by default ───────────────────────────────────────────
def test_sharing_is_off_by_default_and_nothing_is_leased(tmp_path):
    nid, tok, _ = _node("b@x.com")
    assert _fresh(nid)["share_idle"] is False
    assert CN.list_nodes("b@x.com")[0]["share_idle"] is False
    _shared_job(tmp_path)
    assert CN.lease(nid, _fresh(nid), V) is None
    assert CN.sharing_nodes() == []
    CN.set_share(nid, "b@x.com", enabled=True)
    assert [n["id"] for n in CN.sharing_nodes()] == [nid]
    assert CN.lease(nid, _fresh(nid), V) is not None


def test_only_the_owner_can_opt_in_over_http():
    nid, _ = _host(share=False)
    r = client.put(f"/api/nodes/{nid}/share", json={"enabled": True}, headers={"Authorization": "ua"})
    assert r.status_code == 404                                   # not a's node
    r = client.put(f"/api/nodes/{nid}/share", json={"enabled": True}, headers={"Authorization": "adm"})
    assert r.status_code == 404                                   # not even an admin
    r = client.put(f"/api/nodes/{nid}/share", json={"enabled": "yes"}, headers={"Authorization": "ub"})
    assert r.status_code == 400
    r = client.put(f"/api/nodes/{nid}/share", json={"enabled": True}, headers={"Authorization": "ub"})
    assert r.status_code == 200 and r.json()["share_idle"] is True


# ── rule 2: only MOTRES own shareable jobs, never a customer job ─────────────
def test_shared_lease_is_signed_capped_audited_and_cross_owner_marked(tmp_path):
    nid, tok = _host(cores=8, ram_gb=16)
    j = _shared_job(tmp_path)
    got = CN.lease(nid, _fresh(nid), V)
    b = got["bundle"]
    assert b["run_id"] == j["run_id"] and b["shared"] is True
    assert b["limits"] == {"max_cores": 4, "max_ram_mb": 8192, "nice": 19, "ionice": "idle"}
    assert b["progress_s"] == CN.SHARED_PROGRESS_S
    assert got["signature"] == W.sign(tok, b)                      # same HMAC scheme as owner bundles
    ev = _events("job.lease.shared")[0]
    assert ev["cross_owner"] is True and ev["job_owner"] == ADMIN
    assert ev["owner"] == "b@x.com" and ev["node"] == nid and ev["run_id"] == j["run_id"]


def test_customer_job_is_never_leased_to_a_node_it_does_not_own(tmp_path):
    nid, tok = _host("b@x.com")                                   # opted in
    # a customer (role user) cannot flag a job shareable
    r = client.post("/api/nodes/jobs/submit-shared", json={"kind": "selftest.cpu"},
                    headers={"Authorization": "ua"})
    assert r.status_code == 403
    with pytest.raises(CN.Refused) as e:
        CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {}, shareable=True,
                  owner_is_admin=False)
    assert e.value.status == 403
    # a "shareable" key inside a customer body means nothing
    CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {"shareable": True})
    # nor does a record tampered into the store (shareable, but not admin-submitted)
    j = CN.submit("a@x.com", "wsa", _ws(tmp_path, "a"), "selftest.cpu", {})
    jobs = CN._load("remote_jobs.json")
    jobs[j["run_id"]].update(shareable=True)
    CN._save("remote_jobs.json", jobs)
    # an admin job that was not flagged shareable stays on the admin's own nodes
    CN.submit(ADMIN, "wsz", _ws(tmp_path, "z"), "selftest.cpu", {})
    r = client.post("/api/nodes/jobs/lease", json={"version": V}, headers=_hdr(tok))
    assert r.status_code == 200 and r.json()["job"] is None
    assert _events("job.lease.shared") == []


def test_a_foreign_node_cannot_touch_a_shared_job_it_does_not_hold(tmp_path):
    nid, tok = _host("b@x.com")
    other, otok = _host("c@x.com")
    j = _shared_job(tmp_path)
    CN.lease(nid, _fresh(nid), V)
    for verb in ("progress", "complete", "fail", "release"):
        r = client.post(f"/api/nodes/jobs/{j['run_id']}/{verb}", json={"reason": "owner_job"},
                        headers=_hdr(otok))
        assert r.status_code in ((400, 404) if verb == "complete" else (404,))
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/progress", json={}, headers=_hdr(tok))
    assert r.status_code == 200


def test_admin_submit_shared_route_and_user_role_gates(tmp_path):
    r = client.post("/api/nodes/jobs/submit-shared", json={"kind": "selftest.cpu", "body": {"n": 5}},
                    headers={"Authorization": "adm"})
    assert r.status_code == 200 and r.json()["shareable"] is True
    r = client.post("/api/nodes/jobs/submit-shared",
                    json={"kind": "selftest.cpu", "body": {"tags": ["nda"]}},
                    headers={"Authorization": "adm"})
    assert r.status_code == 409 and "nda" in r.json()["detail"]
    r = client.post("/api/nodes/jobs/submit-shared", json={"kind": "no.such"},
                    headers={"Authorization": "adm"})
    assert r.status_code == 400
    nid, _ = _host("b@x.com")
    assert client.get("/api/admin/user-nodes/sharing", headers={"Authorization": "ua"}).status_code == 403
    r = client.get("/api/admin/user-nodes/sharing", headers={"Authorization": "adm"})
    assert [n["id"] for n in r.json()["nodes"]] == [nid]


# ── confidential material is refused at the lease ────────────────────────────
@pytest.mark.parametrize("body", [
    {"tags": ["customer"]}, {"machine": {"tags": ["NDA", "x"]}}, {"cfg": {"confidential": "customer"}},
    {"customer": "ACME"}, {"nda": True}])
def test_confidential_body_is_refused_at_submit_and_at_lease(tmp_path, body):
    with pytest.raises(CN.Refused) as e:
        _shared_job(tmp_path, body)
    assert e.value.status == 409
    # a clean job edited after queueing is caught at the lease, blocked for good, audited once
    nid, _ = _host("b@x.com")
    j = _shared_job(tmp_path)
    jobs = CN._load("remote_jobs.json")
    jobs[j["run_id"]]["body"] = body
    CN._save("remote_jobs.json", jobs)
    assert CN.lease(nid, _fresh(nid), V) is None
    assert CN.lease(nid, _fresh(nid), V) is None
    assert len(_events("lease.refused_confidential")) == 1
    assert CN.get_job(j["run_id"])["blocked"] and CN.get_job(j["run_id"])["state"] == CN.QUEUED


def _die(root: Path, name: str, **meta) -> Path:
    d = root / name
    d.mkdir(parents=True)
    doc = {"owner_org": "MOTRES"}
    doc.update(meta)
    (d / "die.yaml").write_text(json.dumps(doc), encoding="utf-8")        # JSON is YAML
    return d


def test_die_tags_and_ownership_decide(tmp_path, monkeypatch):
    from motor_ai_sim import data_sources as DS
    dies = {"good": _die(tmp_path, "good"),
            "cust": _die(tmp_path, "cust", tags=["customer"]),
            "nda": _die(tmp_path, "nda", nda=True),
            "foreign": _die(tmp_path, "foreign", owner_org="ACME")}
    cfgdie = _die(tmp_path, "cfgdie")
    (cfgdie / "m1.yaml").write_text(json.dumps({"tags": ["confidential"]}), encoding="utf-8")
    dies["cfgdie"] = cfgdie
    monkeypatch.setattr(DS, "locate", lambda n: dies.get(n))
    assert CN.confidential_reason({"die": "good", "config": "m1"}) is None
    assert "customer" in CN.confidential_reason({"die": "cust"})
    assert "NDA" in CN.confidential_reason({"die": "nda"})
    assert "MOTRES" in CN.confidential_reason({"die": "foreign"})
    assert "confidential" in CN.confidential_reason({"die": "cfgdie", "config": "m1"})
    assert "not a curated" in CN.confidential_reason({"nested": {"die": "unknown"}})
    # nested under any key still counts
    assert CN.confidential_reason({"a": [{"machine": {"tags": ["customer"]}}]})


# ── rule 3: owner first, pre-emption, re-queue without an attempt ────────────
def test_owner_jobs_come_before_shared_ones_and_block_sharing(tmp_path):
    nid, tok = _host("b@x.com")
    _shared_job(tmp_path)
    own = CN.submit("b@x.com", "wsb", _ws(tmp_path, "b"), "selftest.cpu", {"n": 1})
    first = CN.lease(nid, _fresh(nid), V)["bundle"]
    assert first["run_id"] == own["run_id"] and "shared" not in first
    # the owner job is leased, nothing of the owner is queued: now sharing may start
    second = CN.lease(nid, _fresh(nid), V)["bundle"]
    assert second.get("shared") is True
    # with the owner queue non-empty a shared job is never started
    nid2, _ = _host("c@x.com")
    mine = CN.submit("c@x.com", "wsc", _ws(tmp_path, "c"), "selftest.cpu", {})
    _shared_job(tmp_path)
    assert CN.lease(nid2, _fresh(nid2), V)["bundle"]["run_id"] == mine["run_id"]


def test_owner_job_preempts_the_shared_job_and_requeues_without_an_attempt(tmp_path):
    nid, tok = _host("b@x.com")
    j = _shared_job(tmp_path)
    node = _fresh(nid)
    for _ in range(CN.MAX_ATTEMPTS + 2):                           # more than MAX_ATTEMPTS preemptions
        got = CN.lease(nid, _fresh(nid), V)
        assert got["bundle"]["run_id"] == j["run_id"]
        assert CN.progress(nid, node, j["run_id"], {"phase": "running"})["preempt"] == ""
        own = CN.submit("b@x.com", "wsb", _ws(tmp_path, "b"), "selftest.cpu", {})
        assert CN.progress(nid, node, j["run_id"], {})["preempt"] == CN.PREEMPT_OWNER_JOB
        out = CN.release(nid, node, j["run_id"], CN.PREEMPT_OWNER_JOB, cpu_s=1.5)
        assert out["state"] == CN.QUEUED
        rec = CN.get_job(j["run_id"])
        assert rec["attempts"] == 0 and rec["shared_on"] == "" and rec["node"] == ""
        CN.cancel(own["run_id"], "b@x.com")                         # owner queue empty again
    assert CN.get_job(j["run_id"])["state"] == CN.QUEUED           # never FAILED
    assert CN.get_job(j["run_id"])["preemptions"] == CN.MAX_ATTEMPTS + 2
    assert len(_events("job.preempt")) == CN.MAX_ATTEMPTS + 2


def test_release_resumes_from_checkpoint_for_kinds_that_support_it(tmp_path, monkeypatch):
    monkeypatch.setattr(CN, "CHECKPOINT_KINDS", {"selftest.cpu"})
    nid, tok = _host("b@x.com")
    j = _shared_job(tmp_path)
    node = _fresh(nid)
    assert "resume_from" not in CN.lease(nid, node, V)["bundle"]
    CN.progress(nid, node, j["run_id"], {"checkpoint": {"step": 7}})
    CN.release(nid, node, j["run_id"], CN.PREEMPT_STOP_SHARING, checkpoint={"step": 9})
    CN.set_share(nid, "b@x.com", enabled=True)
    assert CN.lease(nid, _fresh(nid), V)["bundle"]["resume_from"] == {"step": 9}
    # a kind without checkpoint support restarts from scratch: no resume key
    monkeypatch.setattr(CN, "CHECKPOINT_KINDS", set())
    CN.release(nid, node, j["run_id"], CN.PREEMPT_OWNER_JOB)
    assert "resume_from" not in CN.lease(nid, _fresh(nid), V)["bundle"]


def test_ram_cap_release_counts_an_attempt_so_it_cannot_loop(tmp_path):
    nid, _ = _host("b@x.com")
    j = _shared_job(tmp_path)
    node = _fresh(nid)
    for _ in range(CN.MAX_ATTEMPTS):
        assert CN.lease(nid, _fresh(nid), V) is not None
        CN.release(nid, node, j["run_id"], CN.PREEMPT_RAM_CAP)
    assert CN.get_job(j["run_id"])["state"] == CN.FAILED


def test_stop_sharing_now_ends_the_running_shared_job(tmp_path):
    nid, tok = _host("b@x.com")
    j = _shared_job(tmp_path)
    CN.lease(nid, _fresh(nid), V)
    r = client.post(f"/api/nodes/{nid}/share/stop", headers={"Authorization": "ua"})
    assert r.status_code == 404
    r = client.post(f"/api/nodes/{nid}/share/stop", headers={"Authorization": "ub"})
    assert r.status_code == 200 and r.json()["stopping"] == [j["run_id"]]
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/progress", json={}, headers=_hdr(tok))
    assert r.json()["preempt"] == CN.PREEMPT_STOP_SHARING
    assert _fresh(nid)["share_idle"] is False
    assert _events("share.stop") and _events("share.set")
    r = client.post(f"/api/nodes/jobs/{j['run_id']}/release", json={"reason": "stop_sharing"},
                    headers=_hdr(tok))
    assert r.status_code == 200 and r.json()["state"] == CN.QUEUED
    assert CN.lease(nid, _fresh(nid), V) is None                   # opted out now


# ── rule 4: caps ─────────────────────────────────────────────────────────────
def test_caps_default_to_half_and_the_owner_setting_goes_into_the_bundle(tmp_path):
    nid, _ = _host(cores=16, ram_gb=64)
    assert CN.list_nodes("b@x.com")[0]["share_max_cores"] == 8
    assert CN.list_nodes("b@x.com")[0]["share_max_ram_mb"] == 32 * 1024
    out = CN.set_share(nid, "b@x.com", max_cores=3, max_ram_mb=2048)
    assert (out["max_cores"], out["max_ram_mb"]) == (3, 2048)
    _shared_job(tmp_path)
    lim = CN.lease(nid, _fresh(nid), V)["bundle"]["limits"]
    assert lim == {"max_cores": 3, "max_ram_mb": 2048, "nice": 19, "ionice": "idle"}
    for bad in ({"max_cores": 99}, {"max_cores": 0}, {"max_ram_mb": 10}, {"max_cores": 1.5}):
        with pytest.raises(CN.Refused) as e:
            CN.set_share(nid, "b@x.com", **bad)
        assert e.value.status == 400
    CN.set_share(nid, "b@x.com", max_cores=None, max_ram_mb=None)       # back to the default
    assert CN.list_nodes("b@x.com")[0]["share_max_cores"] == 8


def test_worker_boxes_a_shared_job_in_by_its_limits():
    plan = W.shared_plan({"max_cores": 2, "max_ram_mb": 1024, "nice": 19, "ionice": "idle"})
    assert plan["cores"] == min(2, W.os.cpu_count() or 1) and plan["ram_mb"] == 1024
    assert plan["nice"] == 19 and plan["ionice_idle"] is True
    assert plan["env"]["OMP_NUM_THREADS"] == str(plan["cores"])
    half = W.shared_plan({})
    assert half["cores"] == max(1, (W.os.cpu_count() or 1) // 2)


# ── rule 3 on the worker side + rule 5 transparency ──────────────────────────
def test_worker_stops_a_shared_job_when_the_owner_submits_and_hands_it_back(tmp_path):
    started = threading.Event()

    def slow(body):
        started.set()
        for _ in range(600):
            J.check_cancelled()
            time.sleep(0.01)
        return {"finished": True}
    J.register_handler("test.slowshared", slow)
    try:
        nid, tok = _host("b@x.com")
        j = _shared_job(tmp_path, kind="test.slowshared")
        got = CN.lease(nid, _fresh(nid), V)
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("s", W.execute(
            _transport(tok), tok, got, progress_s=0.05, runner_factory=W.ThreadRunner)))
        th.start()
        started.wait(5)
        time.sleep(0.2)
        assert CN.get_job(j["run_id"])["state"] == CN.LEASED
        own = CN.submit("b@x.com", "wsb", _ws(tmp_path, "b"), "selftest.cpu", {})   # the owner needs the box
        th.join(10)
        assert out["s"] == "preempted"
        rec = CN.get_job(j["run_id"])
        assert rec["state"] == CN.QUEUED and rec["attempts"] == 0 and rec["error"] == ""
        # transparency: the host owner sees the stretch that ran
        runs = CN.shared_runs("b@x.com")
        assert len(runs) == 1 and runs[0]["outcome"] == "preempted:owner_job"
        assert runs[0]["wall_s"] > 0 and "body" not in runs[0] and "owner" not in runs[0]
        assert CN.shared_runs("a@x.com") == []
        assert CN.get_job(own["run_id"])["state"] == CN.QUEUED
    finally:
        J.HANDLERS.pop("test.slowshared", None)


def test_shared_job_completes_into_the_submitter_workspace_and_is_listed_for_the_host(tmp_path):
    nid, tok = _host("b@x.com")
    j = _shared_job(tmp_path, {"n": 50})
    got = CN.lease(nid, _fresh(nid), V)
    assert W.execute(_transport(tok), tok, got, progress_s=0.05,
                     runner_factory=W.ThreadRunner) == "done"
    assert CN.get_job(j["run_id"])["state"] == CN.DONE
    assert CN.result_path(_ws(tmp_path, "z"), j["run_id"]).is_file()    # the admin workspace
    runs = client.get("/api/nodes/shared-runs", headers={"Authorization": "ub"}).json()
    assert runs["runs"][0]["outcome"] == "done" and runs["summary"]["runs"] == 1
    assert "cpu_hours" in runs["runs"][0]
    assert client.get("/api/nodes/shared-runs", headers={"Authorization": "ua"}).json()["runs"] == []
    assert any(e.get("cross_owner") for e in _events("job.complete"))
    assert client.get("/api/nodes", headers={"Authorization": "ub"}).json()["shared"]["runs"] == 1


def test_shared_job_really_runs_in_a_limited_child_process(tmp_path):
    """No runner override: the default for a shared bundle is the child process."""
    nid, tok = _host("b@x.com", max_cores=1)
    j = _shared_job(tmp_path, {"n": 1000})
    got = CN.lease(nid, _fresh(nid), V)
    assert W.execute(_transport(tok), tok, got, progress_s=0.2) == "done"
    assert CN.get_job(j["run_id"])["state"] == CN.DONE


# ── rule 5: engineer reaches My compute nodes ────────────────────────────────
def test_engineer_role_can_use_the_nodes_api():
    h = {"Authorization": "ue"}
    assert client.get("/api/nodes", headers=h).status_code == 200
    d = client.post("/api/nodes", json={"name": "eng box"}, headers=h).json()
    assert client.put(f"/api/nodes/{d['id']}/share", json={"enabled": False}, headers=h).status_code == 200
