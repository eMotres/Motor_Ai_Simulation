"""Two regions, stage 1 (docs/regions/DESIGN.md): config, home region + directory, classifier, the signed peer channel,
the routes, the wrong-region guard, the cross-region job payload and the catalog manifest.

Every piece must be INACTIVE in single-region mode (no peer URL / secret): that is what the current EU server runs.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.regions import catalog_manifest as CM
from motor_ai_sim.regions import classifier as CL
from motor_ai_sim.regions import compute as CP
from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import guard as G
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import status as ST
from motor_ai_sim.regions import store as RS

SECRET = "s" * 40
client = TestClient(app)

_REGION_ENV = ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_REGION", "MOTRES_PEER_HMAC_SECRET",
               "MOTRES_PEER_HMAC_SECRET_FILE", "MOTRES_REGION_GUARD", "MOTRES_CROSS_REGION_COMPUTE",
               "MOTRES_REGION_BASE_URL_EU", "MOTRES_REGION_BASE_URL_US")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import users as U
    for k in _REGION_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("MOTRES_REGION_DIRECTORY_FILE", str(tmp_path / "region_directory.json"))
    monkeypatch.setenv("MOTRES_REGION_JOBS_FILE", str(tmp_path / "region_jobs_outbound.json"))
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", set())         # local dev: the unconfigured caller is the admin
    P.reset_nonces()
    U.create_user("eu.user@example.com", "password-eu-user", role="user", name="Eu User")
    U.create_user("us.user@example.com", "password-us-user", role="user", name="Us User")
    U.create_user("staff@example.com", "password-staff-1", role="engineer", name="Staff")
    return {"U": U, "tmp": tmp_path, "mp": monkeypatch}


def _multi(mp, region="eu", guard=False):
    mp.setenv("MOTRES_REGION", region)
    mp.setenv("MOTRES_PEER_URL", "http://peer.invalid:8001")
    mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    if guard:
        mp.setenv("MOTRES_REGION_GUARD", "1")


def _audit(tmp):
    p = tmp / "admin_audit.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


# ── config ───────────────────────────────────────────────────────────────────

def test_single_region_is_the_default(env):
    assert RC.local_region() == "eu" and not RC.peer_configured() and not RC.guard_enabled()
    assert RC.describe()["mode"] == "single" and RC.describe()["peer_region"] is None
    env["mp"].setenv("MOTRES_PEER_URL", "http://x")
    assert not RC.peer_configured()                                       # a URL without a secret is not a peer
    env["mp"].setenv("MOTRES_PEER_HMAC_SECRET", "short")
    assert not RC.peer_configured()                                       # a short secret fails closed
    env["mp"].setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    assert RC.peer_configured() and RC.peer_region() == "us"
    assert not RC.guard_enabled()                                         # the guard needs its own switch
    env["mp"].setenv("MOTRES_PEER_REGION", "eu")
    assert not RC.peer_configured()                                       # a peer of the same region is a misconfiguration


def test_secret_from_file_and_region_parsing(env):
    f = env["tmp"] / "peer.secret"
    f.write_text(SECRET + "\n", encoding="utf-8")
    env["mp"].setenv("MOTRES_PEER_HMAC_SECRET_FILE", str(f))
    env["mp"].setenv("MOTRES_PEER_URL", "http://x/")
    assert RC.peer_secret() == SECRET and RC.peer_url() == "http://x"
    env["mp"].setenv("MOTRES_REGION", " US ")
    assert RC.local_region() == "us" and RC.peer_region() == "eu"
    env["mp"].setenv("MOTRES_REGION", "mars")
    assert RC.local_region() == "eu"
    assert RC.base_url("us") == "https://us.aerostator.com" and RC.base_url("eu") == "https://aerostator.com"


# ── home region + directory ──────────────────────────────────────────────────

def test_home_region_default_override_and_public_user(env):
    U = env["U"]
    u = U.public_user("us.user@example.com")
    assert (u["home_region"], u["home_region_source"]) == ("eu", "default")
    res = RS.set_home_region("us.user@example.com", "US", by="admin", actor="boss@example.com", reason="profile")
    assert res == {"email": "us.user@example.com", "region": "us", "previous": "eu", "changed": True, "source": "admin"}
    u = U.public_user("us.user@example.com")
    assert (u["home_region"], u["home_region_source"]) == ("us", "admin")
    # the classifier never overrides an admin's choice
    res = RS.set_home_region("us.user@example.com", "eu", by="classifier", respect_admin=True)
    assert res["changed"] is False and RS.get_home_region("us.user@example.com") == "us"
    with pytest.raises(ValueError):
        RS.set_home_region("us.user@example.com", "asia", by="admin")
    with pytest.raises(KeyError):
        RS.set_home_region("nobody@example.com", "us", by="admin")


def test_directory_carries_no_personal_data_and_only_the_peer_region(env):
    RS.set_home_region("us.user@example.com", "us", by="admin")
    local = RS.local_directory()
    assert set(local) == {"eu.user@example.com", "staff@example.com"}
    assert set(local["eu.user@example.com"]) == set(RS.DIRECTORY_FIELDS)
    assert "Eu User" not in json.dumps(local)
    with pytest.raises(ValueError):
        RS.store_remote_directory({}, "eu")                               # never the local region into the remote file
    doc = RS.store_remote_directory({
        "Remote@Example.com": {"id": "abc", "region": "us", "tier": "user", "created": "2026-11-01", "name": "PII", "country": "US"},
        "wrong@example.com": {"id": "x", "region": "eu"},                # another region's row is dropped
        "not-an-email": {"id": "y", "region": "us"},
    }, "us")
    assert set(doc["entries"]) == {"remote@example.com"}
    assert set(doc["entries"]["remote@example.com"]) == set(RS.DIRECTORY_FIELDS)
    assert "PII" not in (env["tmp"] / "region_directory.json").read_text(encoding="utf-8")
    assert RS.region_of_email("remote@example.com") == "us" and RS.region_of_email("eu.user@example.com") == "eu"
    assert RS.region_of_email("ghost@example.com") is None
    assert RS.email_taken_elsewhere("remote@example.com") and not RS.email_taken_elsewhere("eu.user@example.com")
    assert RS.region_map()["remote@example.com"] == "us"


# ── classifier ───────────────────────────────────────────────────────────────

def _sources(mp, table):
    """table: {reason: {email: country}}"""
    mp.setattr(CL, "COUNTRY_SOURCES", [(code, (lambda t: lambda em, rec: t.get(em, ""))(table.get(code, {})))
                                       for code, _ in CL.COUNTRY_SOURCES])


def test_classifier_rule_order_and_dry_run(env):
    mp = env["mp"]
    _sources(mp, {"company": {"us.user@example.com": "US"},
                  "agreement": {"us.user@example.com": "DE", "eu.user@example.com": "SI"}})
    rep = CL.classify_all()
    rows = {r["email"]: r for r in rep["rows"]}
    assert rep["dry_run"] is True and rep["applied"] == 0
    assert (rows["us.user@example.com"]["proposed"], rows["us.user@example.com"]["reason"]) == ("us", "company")  # company first
    assert (rows["eu.user@example.com"]["proposed"], rows["eu.user@example.com"]["reason"]) == ("eu", "agreement")
    assert (rows["staff@example.com"]["proposed"], rows["staff@example.com"]["reason"]) == ("eu", "no_country")
    assert RS.get_home_region("us.user@example.com") == "eu"                # dry run wrote nothing
    assert rep["proposed"] == {"eu": 2, "us": 1}


def test_classifier_signup_country_only_when_recorded_and_apply_keeps_admin(env):
    U, mp = env["U"], env["mp"]
    _sources(mp, {})
    with U._LOCK:
        d = U._load()
        d["us.user@example.com"]["signup_country"] = "us"
        U._save(d)
    assert CL.propose("us.user@example.com")["reason"] == "signup_ip_country"
    assert CL.propose("eu.user@example.com")["reason"] == "no_country"
    RS.set_home_region("eu.user@example.com", "us", by="admin")           # an admin decided otherwise
    rep = CL.classify_all(apply=True)
    assert RS.get_home_region("us.user@example.com") == "us"
    assert RS.get_home_region("eu.user@example.com") == "us"                # admin choice kept
    assert {r["email"]: r["reason"] for r in rep["rows"]}["eu.user@example.com"] == "admin_override"


def test_classifier_survives_a_broken_store(env):
    def boom(em, rec):
        raise OSError("disk")
    env["mp"].setattr(CL, "COUNTRY_SOURCES", [("company", boom), ("profile", lambda em, rec: "US")])
    assert CL.propose("eu.user@example.com")["reason"] == "profile"


# ── the signed peer channel ──────────────────────────────────────────────────

def test_sign_verify_roundtrip_and_refusals(env):
    mp = env["mp"]
    h = P.sign_headers("GET", "/api/peer/v1/health", secret=SECRET, region="us")
    assert P.verify("GET", "/api/peer/v1/health", h) == (False, "not_configured")
    _multi(mp, "eu")
    assert P.verify("GET", "/api/peer/v1/health", h) == (True, "ok")
    assert P.verify("GET", "/api/peer/v1/health", h) == (False, "replay")
    h2 = P.sign_headers("GET", "/api/peer/v1/health", secret=SECRET, region="us")
    assert P.verify("GET", "/api/peer/v1/directory", h2)[1] == "bad_signature"     # path is signed
    assert P.verify("POST", "/api/peer/v1/health", h2)[1] == "bad_signature"       # method is signed
    assert P.verify("GET", "/api/peer/v1/health", h2, b"x")[1] == "bad_signature"  # body is signed
    old = P.sign_headers("GET", "/p", secret=SECRET, region="us", now=time.time() - 3600)
    assert P.verify("GET", "/p", old)[1] == "stale"
    eu = P.sign_headers("GET", "/p", secret=SECRET, region="eu")
    assert P.verify("GET", "/p", eu)[1] == "wrong_region"                           # our own region is not the peer
    forged = P.sign_headers("GET", "/p", secret="f" * 40, region="us")
    assert P.verify("GET", "/p", forged)[1] == "bad_signature"


def test_client_is_none_when_single_and_signs_when_multi(env):
    mp = env["mp"]
    assert P.get("/health") is None                                       # single-region: nothing is sent
    _multi(mp, "eu")
    seen = {}

    def handler(req: httpx.Request):
        mp.setenv("MOTRES_REGION", "us")                                  # answer as the US server would verify it
        mp.setenv("MOTRES_PEER_REGION", "eu")
        ok, why = P.verify(req.method, req.url.raw_path.decode(), req.headers, req.content)
        mp.setenv("MOTRES_REGION", "eu")
        mp.delenv("MOTRES_PEER_REGION")
        seen["why"] = why
        return httpx.Response(200 if ok else 404, json={"region": "us"})

    assert P.get("/health", transport=httpx.MockTransport(handler)) == {"region": "us"} and seen["why"] == "ok"
    down = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert P.get("/health", transport=down) is None                       # unreachable = None, never raises


# ── routes ───────────────────────────────────────────────────────────────────

def test_public_region_info_and_peer_routes_closed_in_single_mode(env):
    r = client.get("/api/region")
    assert r.status_code == 200 and r.json()["mode"] == "single" and r.json()["region"] == "eu"
    assert client.get("/api/peer/v1/health").status_code == 404
    h = P.sign_headers("GET", "/api/peer/v1/health", secret=SECRET, region="us")
    assert client.get("/api/peer/v1/health", headers=h).status_code == 404   # signed, but no peer configured


def test_peer_routes_answer_a_signed_request(env):
    _multi(env["mp"], "eu")
    RS.set_home_region("us.user@example.com", "us", by="admin")
    assert client.get("/api/peer/v1/health").status_code == 404           # unsigned
    h = P.sign_headers("GET", "/api/peer/v1/health", secret=SECRET, region="us")
    j = client.get("/api/peer/v1/health", headers=h).json()
    assert j["region"] == "eu" and set(j) >= {"version", "commit", "load", "queue", "disk"}
    h = P.sign_headers("GET", "/api/peer/v1/directory", secret=SECRET, region="us")
    d = client.get("/api/peer/v1/directory", headers=h).json()
    assert d["region"] == "eu" and "us.user@example.com" not in d["entries"]
    h = P.sign_headers("GET", "/api/peer/v1/admin/users", secret=SECRET, region="us")
    rows = client.get("/api/peer/v1/admin/users", headers=h).json()["rows"]
    assert {r["email"] for r in rows} == {"eu.user@example.com", "staff@example.com"} and all(r["region"] == "eu" for r in rows)
    h = P.sign_headers("GET", "/api/peer/v1/admin/secrets", secret=SECRET, region="us")
    assert client.get("/api/peer/v1/admin/secrets", headers=h).status_code == 404


def test_admin_regions_single_mode_shows_only_this_server(env):
    j = client.get("/api/admin/regions").json()
    assert j["mode"] == "single" and j["peer"] is None and j["local"]["region"] == "eu" and j["local"]["role"] == "this_server"
    r = client.get("/api/admin/regions/remote/users").json()
    assert r == {"configured": False, "reachable": False, "region": None, "rows": []}
    assert client.post("/api/admin/regions/directory/sync").status_code == 409


def test_admin_regions_peer_unreachable_is_graceful(env, monkeypatch):
    _multi(env["mp"], "eu")
    monkeypatch.setattr(P, "get", lambda path, **kw: None)
    j = client.get("/api/admin/regions").json()
    assert j["mode"] == "multi" and j["peer"] == {"region": "us", "ok": False, "role": "peer", "reachable": False}
    assert client.get("/api/admin/regions/remote/projects").json()["reachable"] is False


def test_admin_remote_rows_are_tagged_and_never_stored(env, monkeypatch):
    _multi(env["mp"], "eu")
    monkeypatch.setattr(P, "get", lambda path, **kw: {"rows": [{"email": "far@example.com", "region": "eu"}]})
    j = client.get("/api/admin/regions/remote/users").json()
    assert j["rows"] == [{"email": "far@example.com", "region": "us"}]    # tagged with the peer's region
    assert "far@example.com" not in env["U"]._load()                      # nothing written locally
    assert any(a["action"] == "regions.remote_read" for a in _audit(env["tmp"]))


def test_admin_override_route_is_audited(env):
    r = client.post("/api/admin/users/us.user@example.com/region", json={"region": "us", "reason": "company in Texas"})
    assert r.status_code == 200 and r.json()["region"] == "us" and r.json()["previous"] == "eu"
    a = [x for x in _audit(env["tmp"]) if x["action"] == "user.region"]
    assert a and a[-1]["subject"] == "us.user@example.com" and a[-1]["details"]["region"] == "us"
    assert client.post("/api/admin/users/us.user@example.com/region", json={"region": "mars"}).status_code == 422
    assert client.post("/api/admin/users/ghost@example.com/region", json={"region": "us"}).status_code == 404
    users = client.get("/api/auth/users").json()["users"]
    assert {u["email"]: u["home_region"] for u in users}["us.user@example.com"] == "us"


def test_admin_classify_is_a_dry_run(env, monkeypatch):
    _sources(monkeypatch, {"profile": {"us.user@example.com": "US"}})
    j = client.get("/api/admin/regions/classify").json()
    assert j["dry_run"] is True and {r["email"]: r["proposed"] for r in j["rows"]}["us.user@example.com"] == "us"
    assert RS.get_home_region("us.user@example.com") == "eu"


def test_directory_sync_stores_only_directory_fields(env, monkeypatch):
    _multi(env["mp"], "eu")
    monkeypatch.setattr(P, "get", lambda path, **kw: {"region": "us", "entries": {
        "far@example.com": {"id": "1", "region": "us", "tier": "user", "created": None, "name": "Far Away"}}})
    j = client.post("/api/admin/regions/directory/sync").json()
    assert j["entries"] == 1 and "Far Away" not in (env["tmp"] / "region_directory.json").read_text(encoding="utf-8")


# ── the wrong-region guard ───────────────────────────────────────────────────

def _call(path, token=""):
    """Run the guard middleware alone; -> (status, body) of what it sent, or 'passed'."""
    sent, passed = [], []

    async def inner(scope, receive, send):
        passed.append(True)

    async def send(m):
        sent.append(m)

    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    asyncio.run(G.RegionGuardMiddleware(inner)({"type": "http", "path": path, "headers": headers}, None, send))
    if passed:
        return "passed"
    return sent[0]["status"], json.loads(sent[1]["body"])


def test_guard_passes_everything_in_single_mode(env):
    U = env["U"]
    RS.set_home_region("us.user@example.com", "us", by="admin")
    tok = U.issue_token("us.user@example.com")
    env["mp"].setenv("MOTRES_REGION_GUARD", "1")                          # switch on, but no peer
    assert _call("/api/projects", tok) == "passed"


def test_guard_answers_421_with_the_home_base_url(env):
    U = env["U"]
    _multi(env["mp"], "eu", guard=True)
    RS.set_home_region("us.user@example.com", "us", by="admin")
    RS.set_home_region("staff@example.com", "us", by="admin")
    status, body = _call("/api/projects", U.issue_token("us.user@example.com"))
    assert status == 421 and body["detail"]["code"] == "wrong_region"
    assert body["detail"]["region"] == "us" and body["detail"]["base_url"] == "https://us.aerostator.com"
    assert _call("/api/projects", U.issue_token("eu.user@example.com")) == "passed"     # right region
    assert _call("/api/projects", U.issue_token("staff@example.com")) == "passed"       # staff works on both
    assert _call("/api/projects") == "passed"                                            # anonymous: the edge's job
    assert _call("/api/projects", "garbage") == "passed"                                 # invalid: the role gate's job
    for p in ("/api/health", "/api/auth/login", "/api/region", "/api/peer/v1/health", "/api/legal/terms", "/"):
        assert _call(p, U.issue_token("us.user@example.com")) == "passed", p


# ── the cross-region job payload ─────────────────────────────────────────────

IDENTIFYING = ("email", "owner", "name", "company", "ws_id", "run_id", "project_id", "title", "die", "config", "duty",
               "target", "file", "path", "client_name", "user", "account_id")


def test_cross_region_payload_carries_engineering_inputs_only(env):
    body = {"owner": "anna@example.com", "email": "anna@example.com", "name": "Anna", "company": "ACME Motors",
            "ws_id": "0123456789abcdef", "run_id": "run-1", "project_id": "p-acme", "project_title": "ACME pump",
            "die": "CIANO56 400_90 anna 2", "config": "L100", "duty": "cruise", "target": {"die": "x", "config": "y"},
            "file": "motor.step", "path": "/srv/motres/workspaces/0123/x.yaml", "agent": {"client_name": "Claude"},
            "geometry": {"stator_od_mm": 85.0, "slots": 24, "poles": 20, "stack_mm": 65,
                         "layers": [{"material": "M270-35A", "thickness_mm": 0.35, "label": "inner"}]},
            "operating": {"rpm": 4000, "i_phase_a": 120.5, "gamma_deg": -12.0, "connection": "2S"}}
    tok, payload = CP.job_payload("coupled", body, priority=2)
    assert set(payload) == {"v", "token", "kind", "priority", "inputs"} and payload["token"] == tok and len(tok) >= 20
    text = json.dumps(payload)
    for k in IDENTIFYING:
        assert f'"{k}"' not in text, k
    for v in ("anna", "ACME", "CIANO56", "L100", "cruise", "motor.step", "/srv/", "Claude", "0123456789abcdef", "run-1"):
        assert v not in text, v
    assert payload["inputs"]["geometry"]["slots"] == 24 and payload["inputs"]["operating"]["rpm"] == 4000
    assert payload["inputs"]["geometry"]["layers"][0] == {"material": "M270-35A", "thickness_mm": 0.35}


@pytest.mark.parametrize("leak", [{"notes_x": "mail anna@example.com"}, {"cad": "C:\\Users\\anna\\motor.yaml"},
                                  {"src": "/home/anna/x"}, {"ref": "bom_final.xlsx"}, {"anna@example.com": 1}])
def test_cross_region_payload_refuses_what_still_identifies(env, leak):
    with pytest.raises(CP.CrossRegionPayloadError):
        CP.job_payload("coupled", {"geometry": {"slots": 24}, **leak})


def test_token_map_stays_home_and_maps_back(env):
    tok, _ = CP.job_payload("thermal", {"geometry": {"slots": 12}})
    CP.register_outbound(tok, run_id="run-7", owner="eu.user@example.com", ws_id="abc", target={"die": "D"}, region="us")
    assert CP.resolve_token(tok)["owner"] == "eu.user@example.com"
    assert CP.complete_token(tok)["run_id"] == "run-7" and CP.resolve_token(tok) is None
    assert CP.complete_token("unknown") is None
    assert CP.foreign_job_expired(time.time() - CP.RETENTION_S - 1) and not CP.foreign_job_expired(time.time())


def test_pick_region(env):
    idle = {"ok": True, "load": {"cores": 16}, "queue": {"running": 0, "queued": 0}}
    busy = {"ok": True, "load": {"cores": 16}, "queue": {"running": 8, "queued": 12}}
    assert CP.pick_region("eu", busy, idle, enabled=True) == "us"
    assert CP.pick_region("eu", busy, idle, enabled=False) == "eu"
    assert CP.pick_region("eu", idle, busy, enabled=True) == "eu"
    assert CP.pick_region("eu", busy, None, enabled=True) == "eu"                   # peer unknown -> home
    assert CP.pick_region("eu", busy, {**idle, "ok": False}, enabled=True) == "eu"
    almost = {"ok": True, "load": {"cores": 16}, "queue": {"running": 1, "queued": 0}}
    assert CP.pick_region("eu", almost, idle, enabled=True) == "eu"                  # no ping-pong on near-equal load
    assert not CP.cross_region_enabled()                                              # off by default


# ── the catalog manifest ─────────────────────────────────────────────────────

def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_catalog_manifest_is_an_allow_list(env, monkeypatch):
    from motor_ai_sim import motor_access as MA
    MA._CFG_VIS_CACHE.clear()
    (env["tmp"] / "die_access.json").write_text(json.dumps({"OPEN1": {"visibility": "public"}}), encoding="utf-8")
    monkeypatch.setattr(MA, "_DIE_ACCESS_FILE", env["tmp"] / "die_access.json")
    root = env["tmp"] / "shared"
    _w(root / "materials_library.yaml", "steel: {}\n")
    _w(root / "dies" / "OPEN1" / "die.yaml", "name: OPEN1\n")
    _w(root / "dies" / "OPEN1" / "L12.yaml", "x: 1\n")
    _w(root / "dies" / "OPEN1" / ".mesh_cache" / "m.npz", "cache")
    _w(root / "dies" / "CLIENT" / "die.yaml", "name: CLIENT\ntags: [customer]\n")
    _w(root / "dies" / "USPIN" / "die.yaml", "name: USPIN\nhome_region: us\n")
    _w(root / "projects" / "p1" / "project.json", "{}")
    _w(root / "production" / "x" / "a.pdf", "pdf")
    _w(root / "directory" / "companies.json", "[]")
    _w(root / "legal" / "nda_signatures.jsonl", "{}")
    _w(root / "unknown_store" / "a.json", "{}")
    m = CM.build(root, catalog_path=root.parent / "nocatalog.json")
    assert set(m["files"]) == {"materials_library.yaml", "dies/OPEN1/die.yaml", "dies/OPEN1/L12.yaml"}
    assert {e["die"] for e in m["excluded_dies"]} == {"CLIENT", "USPIN"}
    again = CM.build(root, catalog_path=root.parent / "nocatalog.json")
    assert again["root_id"] == m["root_id"]
    _w(root / "dies" / "OPEN1" / "L12.yaml", "x: 2\n")
    changed = CM.build(root, catalog_path=root.parent / "nocatalog.json")
    assert changed["root_id"] != m["root_id"]
    assert CM.diff(changed, m) == {"fetch": ["dies/OPEN1/L12.yaml"], "delete": []}
    assert CM.diff({"files": {}}, m)["delete"] == sorted(m["files"])


def test_local_status_shape(env):
    s = ST.local_status()
    assert s["region"] == "eu" and s["ok"] is True
    assert set(s["load"]) == {"cores", "load1", "load_pct"} and set(s["disk"]) == {"total_gb", "free_gb", "used_pct"}
    assert set(s["queue"]) == {"running", "queued", "oldest_wait_s", "worker_reachable"}
