"""Cross-region compute, stage 2 (docs/regions/DESIGN.md section 9): the ``jobs`` wiring (off by default), the home server's
dispatch (scrubbed payload, outbound token map, refusal, fall back to home), the computing server's foreign-job store
(24 h retention, hand-back with retry, never in a backup) and the signed peer routes.

No solver, no network: the peer is a fake, the only job kind is ``selftest.cpu`` (a tiny bounded loop).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import compute_nodes as CN
from motor_ai_sim import jobs as J
from motor_ai_sim.api import app
from motor_ai_sim.regions import compute as CP
from motor_ai_sim.regions import foreign as F
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import status as ST

SECRET = "s" * 40
OWNER = "alice@example.com"
_ENV = ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_REGION", "MOTRES_PEER_HMAC_SECRET", "MOTRES_CROSS_REGION_COMPUTE",
        "MOTRES_FOREIGN_SLOTS")

IDLE = {"ok": True, "load": {"cores": 8}, "queue": {"running": 0, "queued": 0}}
BUSY = {"ok": True, "load": {"cores": 8}, "queue": {"running": 8, "queued": 8}}


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLUSTER_MONITOR_DIR", str(tmp_path / "cluster"))
    monkeypatch.setenv("MOTRES_REGION_JOBS_FILE", str(tmp_path / "outbound.json"))
    monkeypatch.setenv("MOTRES_FOREIGN_JOBS_DIR", str(tmp_path / "run" / "foreign_jobs"))
    CN.reset_memory()
    F._THREADS.clear()
    F._PEER_STATUS.update(ts=0.0, value=None)
    yield
    CN.reset_memory()


def _multi(mp, region="eu", cross=True):
    mp.setenv("MOTRES_REGION", region)
    mp.setenv("MOTRES_PEER_URL", "http://peer.invalid:8001")
    mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    if cross:
        mp.setenv("MOTRES_CROSS_REGION_COMPUTE", "1")


class FakePeer:
    """Stands in for ``peer.get`` / ``peer.post``: records what would cross and answers from a script."""

    def __init__(self, status=IDLE, submit=None, result=None):
        self.status, self.submit, self.result = status, submit, result
        self.sent = []

    def get(self, path, **kw):
        return self.status if path == "/health" else None

    def post(self, path, payload, **kw):
        self.sent.append((path, payload))
        if path == "/foreign/submit":
            return self.submit(payload) if self.submit else {"ok": True, "token": payload["token"]}
        if path == "/foreign/result":
            return self.result(payload) if self.result else {"ok": True}
        return None


@pytest.fixture()
def peer(monkeypatch):
    fp = FakePeer()
    monkeypatch.setattr(P, "get", fp.get)
    monkeypatch.setattr(P, "post", fp.post)
    return fp


def _rec(tmp_path, body=None, **kw):
    rid = kw.pop("run_id", "run_test_1")
    return J.JobRecord(run_id=rid, ws_id="ws-alice", owner=OWNER, kind="selftest.cpu", priority=2,
                       body=body if body is not None else {"n": 1000}, queued_at=time.time(),
                       target={"die": "SECRETDIE", "config": "C1"}, **kw)


@pytest.fixture()
def home(tmp_path, monkeypatch, peer):
    """A busy EU home server whose peer is idle, with a workspace for the owner."""
    _multi(monkeypatch, "eu")
    monkeypatch.setattr(ST, "local_status", lambda now=None: BUSY)
    peer.status = IDLE
    from motor_ai_sim import workspace as W

    class _WS:
        id = "ws-alice"
        root = tmp_path / "ws_alice"
    _WS.root.mkdir()
    monkeypatch.setattr(W, "workspace", lambda *a, **k: _WS)
    return peer


# ── the jobs.py wiring ───────────────────────────────────────────────────────

def test_off_by_default_nothing_is_imported_or_sent(tmp_path, monkeypatch, peer):
    assert J._region_router(_rec(tmp_path)) is False              # no env: one read, False
    _multi(monkeypatch, "eu", cross=False)                         # peer configured, switch off
    assert J._region_router(_rec(tmp_path)) is False
    assert peer.sent == []


def test_run_job_offers_a_handler_kind_to_the_region_router(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(J, "REGION_ROUTER", lambda rec: seen.append(rec.kind) or True)
    with pytest.raises(J.JobAccepted):
        J.run_job("selftest.cpu", lambda: None, run_id="r1", body={"n": 1})
    assert seen == ["selftest.cpu"]
    seen.clear()                                                   # a kind without a handler is never offered
    monkeypatch.setattr(J, "REGION_ROUTER", lambda rec: seen.append(rec.kind) or True)
    monkeypatch.setattr(J, "queue", lambda: type("Q", (), {"submit": lambda s, rec, work, block=None: "ran"})())
    assert J.run_job("no.handler.kind", lambda: "x", run_id="r2") == "ran" and seen == []


def test_queued_decorator_reaches_the_router(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "REGION_ROUTER", lambda rec: rec.kind == "selftest.cpu")

    @J.queued("selftest.cpu", body_keys=("n",))
    def route(n=5):
        return {"local": True}

    out = route(n=7)                                               # the 202 answer, not the route's own result
    assert getattr(out, "status_code", None) == 202


# ── home: dispatch ───────────────────────────────────────────────────────────

def test_route_sends_the_unmodified_payload_and_keeps_the_token_map(tmp_path, home):
    rec = _rec(tmp_path, {"n": 1000})
    assert F.route(rec) is True
    path, payload = home.sent[0]
    blob = json.dumps(payload)
    assert path == "/foreign/submit"
    for leak in (OWNER, "alice", "ws-alice", "run_test_1", "SECRETDIE"):
        assert leak not in blob
    assert payload["inputs"] == {"n": 1000} and set(payload) == {"v", "token", "kind", "priority", "inputs"}
    ent = CP.resolve_token(payload["token"])
    assert ent["owner"] == OWNER and ent["run_id"] == "run_test_1" and ent["region"] == "us"
    job = CN.get_job("run_test_1")                                # the home record: visible in /api/jobs, leased to the region
    assert job["state"] == CN.LEASED and job["node"] == "region:us"
    assert CN.reap(time.time() + 3600) == 0                       # reap never re-queues it


def test_a_job_is_never_sent_in_a_modified_form(tmp_path, home):
    """M6: a body the scrub would change (owner / label / nested e-mail key) or with a key outside the kind's allow-list runs at
    home with the inputs the user gave - not as a different job elsewhere."""
    for body in ({"n": 1000, "owner": OWNER}, {"n": 1000, "label": "alice's run"}, {"n": 1, "nested": {"email": OWNER}},
                 {"n": 1000, "extra": 3}, {}):
        assert F.route(_rec(tmp_path, body or {"zzz": 1})) is False
    assert home.sent == [] and CP._load_out() == {} and CN.get_job("run_test_1") is None


def test_a_kind_without_an_input_allow_list_never_crosses(tmp_path, home, monkeypatch):
    monkeypatch.setitem(J.HANDLERS, "other.kind", lambda body: body)
    rec = J.JobRecord(run_id="run_x", ws_id="w", owner=OWNER, kind="other.kind", body={"n": 1}, queued_at=time.time())
    assert F.route(rec) is False and home.sent == []
    F.register_inputs("other.kind", ["n"])
    try:
        assert F.route(rec) is True
    finally:
        F.INPUT_KEYS.pop("other.kind", None)


def test_identifying_payload_is_refused_and_runs_at_home(tmp_path, home):
    assert F.route(_rec(tmp_path, {"n": 1, "spec": "mail alice@example.com"})) is False
    assert F.route(_rec(tmp_path, {"n": 1, "src": "C:\\Users\\alice\\motor.yaml"})) is False
    assert F.route(_rec(tmp_path, {"n": 1, "src": "/srv/motres/workspaces/x"})) is False
    assert F.route(_rec(tmp_path, {"n": 1, "f": "motor_final.step"})) is False
    assert home.sent == [] and CP._load_out() == {} and CN.get_job("run_test_1") is None


def test_peer_refusal_or_silence_runs_at_home(tmp_path, home):
    home.submit = lambda p: {"ok": False, "code": "busy"}
    assert F.route(_rec(tmp_path)) is False
    assert CP._load_out() == {} and CN.get_job("run_test_1") is None      # the token was forgotten again
    home.submit = lambda p: None                                          # unreachable
    assert F.route(_rec(tmp_path, run_id="run_test_2")) is False
    assert CP._load_out() == {}


def test_not_used_when_load_is_equal_or_peer_unknown_or_job_already_on_a_node(tmp_path, home, monkeypatch):
    home.status = BUSY
    assert F.route(_rec(tmp_path)) is False                               # equal load: no ping-pong
    F._PEER_STATUS.update(ts=0.0, value=None)
    home.status = None                                                    # peer unknown
    assert F.route(_rec(tmp_path)) is False
    home.status = IDLE
    F._PEER_STATUS.update(ts=0.0, value=None)
    assert F.route(_rec(tmp_path, node="n1")) is False
    monkeypatch.delenv("MOTRES_CROSS_REGION_COMPUTE")
    assert F.route(_rec(tmp_path)) is False
    assert home.sent == []


def test_non_json_body_stays_home(tmp_path, home):
    assert F.route(_rec(tmp_path, {"n": object()})) is False and home.sent == []


# ── home: the result comes back ──────────────────────────────────────────────

def _sent_token(home):
    return next(p["token"] for path, p in home.sent if path == "/foreign/submit")


def test_result_is_filed_under_the_owner_and_the_token_forgotten(tmp_path, home):
    assert F.route(_rec(tmp_path)) is True
    tok = _sent_token(home)
    assert F.receive_result({"token": tok, "ok": True, "result": {"sum_sq": 42}, "wall_s": 1.5}) == {"ok": True, "known": True}
    job = CN.get_job("run_test_1")
    assert job["state"] == CN.DONE and job["provenance"]["wall_s"] == 1.5
    out = json.loads((tmp_path / "ws_alice" / "remote_results" / "run_test_1.json").read_text(encoding="utf-8"))
    assert out["result"] == {"sum_sq": 42}
    assert CP.resolve_token(tok) is None
    assert F.receive_result({"token": tok, "ok": True, "result": {"sum_sq": 1}}) == {"ok": True, "known": False}   # repeat: no-op
    assert CN.get_job("run_test_1")["state"] == CN.DONE


def test_failed_result_and_unknown_token(tmp_path, home):
    assert F.route(_rec(tmp_path)) is True
    tok = _sent_token(home)
    F.receive_result({"token": tok, "ok": False, "error": "boom"})
    assert CN.get_job("run_test_1")["state"] == CN.FAILED and "boom" in CN.get_job("run_test_1")["error"]
    assert F.receive_result({"token": "x" * 20, "ok": True}) == {"ok": True, "known": False}
    assert F.receive_result("nonsense")["ok"] is False


def test_result_before_the_home_record_exists_is_asked_again(tmp_path, home):
    CP.register_outbound("t" * 24, run_id="run_early", owner=OWNER, ws_id="ws-alice", region="us")
    assert F.receive_result({"token": "t" * 24, "ok": True, "result": {}}) == {"ok": False, "code": "not_yet"}
    assert CP.resolve_token("t" * 24) is not None


def test_lost_job_is_rerun_at_home_after_24_hours(tmp_path, home):
    assert F.route(_rec(tmp_path)) is True
    tok = _sent_token(home)
    assert F.home_sweep() == 0                                            # young token: wait
    assert F.home_sweep(time.time() + CP.RETENTION_S + 5) == 1
    assert CP.resolve_token(tok) is None
    for _ in range(100):
        if CN.get_job("run_test_1")["state"] == CN.DONE:
            break
        time.sleep(0.05)
    assert CN.get_job("run_test_1")["state"] == CN.DONE
    queued = J.queue().status("run_test_1-home")                          # it went THROUGH the job queue (M7), not a bare thread
    assert queued is not None and queued.kind == "selftest.cpu" and queued.owner == OWNER


def test_expiry_handback_and_sweep_go_on_with_the_switch_off(tmp_path, monkeypatch, home):
    """M7: turning MOTRES_CROSS_REGION_COMPUTE off must not leave foreign files or outbound tokens behind for ever."""
    assert F.route(_rec(tmp_path)) is True
    tok = _sent_token(home)
    _multi(monkeypatch, "eu", cross=False)
    old = time.time() - CP.RETENTION_S - 10
    F._write({"v": 1, "token": "o" * 24, "kind": "selftest.cpu", "priority": 0, "inputs": {"n": 1}, "state": "done",
              "created_at": old, "started_at": old, "ended_at": old, "result": {"n": 1}, "error": "", "tries": 3, "next_at": 0.0})
    out = F.tick(time.time() + CP.RETENTION_S + 5)
    assert out["expired"] == 1 and F.list_jobs() == []
    assert out["swept"] == 1 and CP.resolve_token(tok) is None
    # a finished job is still handed back (retry) with the switch off
    peer_calls = len(home.sent)
    F._write({"v": 1, "token": "p" * 24, "kind": "selftest.cpu", "priority": 0, "inputs": {"n": 1}, "state": "done",
              "created_at": time.time(), "started_at": time.time(), "ended_at": time.time(), "result": {"n": 1}, "error": "",
              "tries": 1, "next_at": 0.0})
    assert F.tick()["handbacks"] == 1 and len(home.sent) == peer_calls + 1 and F.list_jobs() == []


def test_the_worker_loop_always_starts_even_with_the_switch_off(monkeypatch):
    import threading
    stop, ticked = threading.Event(), threading.Event()
    monkeypatch.setattr(F, "tick", lambda *a, **k: ticked.set())
    F._LOOP = None
    try:
        assert CP.cross_region_enabled() is False
        assert F.start_loop(interval_s=0.01, stop=stop) is True
        assert ticked.wait(5)
    finally:
        stop.set()
        F._LOOP.join(5)
        F._LOOP = None


# ── computing server ─────────────────────────────────────────────────────────

def _payload(tok="a" * 24, **kw):
    return {"v": 1, "token": tok, "kind": "selftest.cpu", "priority": 0, "inputs": {"n": 5000}, **kw}


def test_accept_stores_a_foreign_job_only_when_enabled_and_clean(tmp_path, monkeypatch, peer):
    assert F.accept(_payload()) == {"ok": False, "code": "off"}
    _multi(monkeypatch, "us")
    assert F.accept(_payload()) == {"ok": True, "token": "a" * 24}
    assert F.accept(_payload()) == {"ok": True, "token": "a" * 24}        # same job again: idempotent
    assert len(F.list_jobs()) == 1
    f = Path(F.foreign_dir()) / ("a" * 24 + ".json")
    assert f.is_file() and "alice" not in f.read_text(encoding="utf-8")
    assert F.accept(_payload("b" * 24, inputs={"n": OWNER}))["code"] == "identifying"
    assert F.accept(_payload("c" * 24, inputs={"n": 1, "owner": "x"}))["code"] == "unexpected_input"   # outside the allow-list
    assert F.accept(_payload("d" * 24, kind="no.such.kind"))["code"] == "unknown_kind"
    assert F.accept(_payload("short"))["code"] == "bad_payload"
    assert F.accept({**_payload("e" * 24), "v": 2})["code"] == "bad_payload"
    assert F.accept(_payload("f" * 24, inputs={"n": "x" * (F.MAX_INPUT_BYTES + 1)}))["code"] == "too_large"


def test_accept_applies_back_pressure(tmp_path, monkeypatch, peer):
    _multi(monkeypatch, "us")
    for i in range(F.MAX_PENDING):
        assert F.accept(_payload(chr(97 + i) * 24))["ok"]
    assert F.accept(_payload("z" * 24)) == {"ok": False, "code": "busy"}


def test_tick_runs_the_job_and_hands_the_result_back_then_deletes(tmp_path, monkeypatch, peer):
    _multi(monkeypatch, "us")
    F.accept(_payload())
    out = F.tick()
    assert out["started"] == 1
    for th in list(F._THREADS.values()):
        th.join(5)
    path, body = peer.sent[-1]
    assert path == "/foreign/result" and body["ok"] is True and body["result"]["n"] == 5000
    assert set(body) <= {"token", "ok", "result", "wall_s"}               # nothing about whom it ran for
    assert F.list_jobs() == []                                            # confirmed: deleted at once


def test_unconfirmed_handback_is_retried_with_a_growing_delay_and_expires_after_24h(tmp_path, monkeypatch, peer):
    _multi(monkeypatch, "us")
    peer.result = lambda p: None                                          # home unreachable
    F.accept(_payload())
    F.tick()
    for th in list(F._THREADS.values()):
        th.join(5)
    (job,) = F.list_jobs()
    assert job["state"] == "done" and job["tries"] == 1 and job["next_at"] > time.time()
    gap1 = job["next_at"] - time.time()
    n = len(peer.sent)
    F.tick()                                                              # not due yet: no new attempt
    assert len(peer.sent) == n
    t1 = job["next_at"] + 1
    F.tick(t1)
    (job,) = F.list_jobs()
    gap2 = job["next_at"] - t1
    assert job["tries"] == 2 and gap2 > gap1 > F.HANDBACK_FIRST_S - 1      # the delay grows
    peer.result = lambda p: {"ok": True}                                  # home is back
    F.tick(job["next_at"] + 1)
    assert F.list_jobs() == []
    # unconfirmed to the end: everything is deleted 24 h after it ended
    peer.result = lambda p: None
    F.accept(_payload("q" * 24))
    F.tick()
    for th in list(F._THREADS.values()):
        th.join(5)
    (job,) = F.list_jobs()
    sent = len(peer.sent)
    F.tick(job["ended_at"] + CP.RETENTION_S + 1)
    assert F.list_jobs() == [] and len(peer.sent) == sent                 # deleted, no further hand-back


def test_a_queued_job_nobody_started_also_expires(tmp_path, monkeypatch, peer):
    _multi(monkeypatch, "us")
    F.accept(_payload())
    monkeypatch.setattr(F, "_local_queue_free", lambda: False)             # own users come first: not started
    assert F.tick()["started"] == 0
    (job,) = F.list_jobs()
    F.tick(job["created_at"] + CP.RETENTION_S + 1)
    assert F.list_jobs() == []


def test_failed_job_hands_back_the_error_only(tmp_path, monkeypatch, peer):
    _multi(monkeypatch, "us")

    def boom(body):
        raise ValueError("solver exploded")
    monkeypatch.setitem(J.HANDLERS, "boom.kind", boom)
    monkeypatch.setitem(F.INPUT_KEYS, "boom.kind", frozenset({"n"}))
    F.accept(_payload(kind="boom.kind"))
    F.tick()
    for th in list(F._THREADS.values()):
        th.join(5)
    path, body = peer.sent[-1]
    assert body["ok"] is False and "solver exploded" in body["error"] and "result" not in body


def test_foreign_jobs_are_outside_every_backed_up_tree():
    base = Path(__file__).resolve().parents[1] / "deploy" / "backup"
    include = [ln.strip() for ln in (base / "restic-include.txt").read_text(encoding="utf-8").splitlines()
               if ln.strip() and not ln.startswith("#")]
    exclude = (base / "restic-exclude.txt").read_text(encoding="utf-8")
    assert "foreign_jobs/" in exclude
    default = "/srv/motres/run/foreign_jobs"
    assert not any(default == i or default.startswith(i.rstrip("/") + "/") for i in include)
    us_include = [ln.strip() for ln in (base / "restic-include-us.txt").read_text(encoding="utf-8").splitlines()
                  if ln.strip() and not ln.startswith("#")]
    assert not any(default.startswith(i.rstrip("/") + "/") for i in us_include)


def test_default_foreign_dir_is_run_foreign_jobs_beside_identity(monkeypatch):
    from motor_ai_sim import users as U
    monkeypatch.delenv("MOTRES_FOREIGN_JOBS_DIR")
    monkeypatch.setattr(U, "_USERS_FILE", Path("/srv/motres/identity/users.json"))
    assert F.foreign_dir().as_posix() == "/srv/motres/run/foreign_jobs"


# ── the signed routes ────────────────────────────────────────────────────────

def _signed(method, path, body=b"", region="us"):
    return P.sign_headers(method, path, body, secret=SECRET, region=region)


def test_foreign_routes_are_404_unsigned_and_work_signed(tmp_path, monkeypatch):
    c = TestClient(app)
    P.reset_nonces()
    path = P.PREFIX + "/foreign/submit"
    body = json.dumps(_payload(), sort_keys=True, separators=(",", ":")).encode()
    assert c.post(path, content=body).status_code == 404                       # single-region
    _multi(monkeypatch, "eu")                                                   # this server is EU, the caller is the US peer
    assert c.post(path, content=body).status_code == 404                        # unsigned
    h = {**_signed("POST", path, body), "Content-Type": "application/json"}
    r = c.post(path, content=body, headers=h)
    assert r.status_code == 200 and r.json() == {"ok": True, "token": "a" * 24}
    assert c.post(path, content=body, headers=h).status_code == 404            # replayed nonce
    rpath = P.PREFIX + "/foreign/result"
    rbody = json.dumps({"token": "zzzzzzzzzzzzzzzzzzzz", "ok": True, "result": {}}, sort_keys=True, separators=(",", ":")).encode()
    r2 = c.post(rpath, content=rbody, headers={**_signed("POST", rpath, rbody), "Content-Type": "application/json"})
    assert r2.status_code == 200 and r2.json() == {"ok": True, "known": False}


def test_extracts_route_is_eu_only_and_signed(tmp_path, monkeypatch):
    c = TestClient(app)
    P.reset_nonces()
    path = P.PREFIX + "/catalog/extracts"
    assert c.get(path).status_code == 404
    _multi(monkeypatch, "us")                                                   # a US server publishes nothing
    r = c.get(path, headers=_signed("GET", path, region="eu"))
    assert r.status_code == 404
    _multi(monkeypatch, "eu")
    P.reset_nonces()
    from motor_ai_sim.regions import catalog_manifest as CM
    monkeypatch.setattr(CM, "build_all", lambda *a, **k: ({}, {"v": 1, "id": "x", "dies": {}, "passports": {}, "counts": {}}))
    r = c.get(path, headers=_signed("GET", path))
    assert r.status_code == 200 and r.json()["id"] == "x"
