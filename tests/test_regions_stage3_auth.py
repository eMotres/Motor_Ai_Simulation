"""Two regions, stage 3 (docs/regions/DESIGN.md sections 5 and 8): sign-in across regions, the hand-off ticket, sign-up
uniqueness, the per-account guard and the write freeze.

Two servers are simulated in one process: ``_as(env, "eu")`` / ``_as(env, "us")`` switch the region, the registry, the
sessions, the directory and the hand-off store to that server's own tmp files.  The peer is faked; nothing leaves tmp.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.regions import guard as G
from motor_ai_sim.regions import handoff as H
from motor_ai_sim.regions import peer as P
from motor_ai_sim.regions import routing as RR
from motor_ai_sim.regions import store as RS

SECRET = "k" * 40
PW = "correct horse battery"
US_USER = "us.user@example.com"
EU_USER = "eu.user@example.com"
STAFF = "staff@example.com"
client = TestClient(app)

_REGION_ENV = ("MOTRES_REGION", "MOTRES_PEER_URL", "MOTRES_PEER_REGION", "MOTRES_PEER_HMAC_SECRET",
               "MOTRES_PEER_HMAC_SECRET_FILE", "MOTRES_REGION_GUARD", "MOTRES_CROSS_REGION_COMPUTE",
               "MOTRES_REGION_BASE_URL_EU", "MOTRES_REGION_BASE_URL_US", "WORKSPACES_ROOT")


def _as(env, region: str, *, peer: bool = True) -> None:
    """Make this process the ``region`` server (its own registry, sessions, directory, hand-off store)."""
    mp, d = env["mp"], env["tmp"] / region
    d.mkdir(exist_ok=True)
    mp.setattr(env["U"], "_USERS_FILE", d / "users.json")
    mp.setattr(env["S"], "_SESSIONS_FILE", d / ".sessions.json")
    mp.setattr(env["S"], "_EVENTS_FILE", d / "auth_events.jsonl")
    mp.setenv("AUTH_SECRET", f"auth-secret-of-{region}-not-real")
    mp.setenv("ADMIN_AUDIT_FILE", str(d / "admin_audit.jsonl"))
    mp.setenv("MOTRES_REGION_DIRECTORY_FILE", str(d / "region_directory.json"))
    mp.setenv("MOTRES_REGION_HANDOFF_FILE", str(d / "region_handoff_used.json"))
    mp.setenv("MOTRES_REGION", region)
    if peer:
        mp.setenv("MOTRES_PEER_URL", "http://peer.invalid:8001")
        mp.setenv("MOTRES_PEER_HMAC_SECRET", SECRET)
    else:
        mp.delenv("MOTRES_PEER_URL", raising=False)
        mp.delenv("MOTRES_PEER_HMAC_SECRET", raising=False)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    for k in _REGION_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(A, "AUTH_ENFORCE", False)
    monkeypatch.setattr(A, "_ADMIN_EMAILS", set())
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    E.reset_limits()
    P.reset_nonces()
    e = {"U": U, "S": S, "E": E, "A": A, "tmp": tmp_path, "mp": monkeypatch}
    _as(e, "eu")
    for em, role in ((US_USER, "user"), (EU_USER, "user"), (STAFF, "engineer")):
        U.create_user(em, PW, role=role, name=em.split("@")[0])
    yield e
    E.reset_limits()


def _migrate_record(env, email: str = US_USER) -> dict:
    """What the migration does to the record: copy it to US (same hash), guard it on EU."""
    U = env["U"]
    rec = dict(U.get_user(email))
    RS.set_home_region(email, "us", by="admin")
    RS.set_region_guard(email, True, by="test", bundle="mig-test")
    _as(env, "us")
    with U._LOCK:
        users = U._load()
        users[email] = {**rec, "home_region": "us", "home_region_set": {"by": "admin"}}
        U._save(users)
    _as(env, "eu")
    return rec


def _login(email, pw=PW):
    return client.post("/api/auth/login", json={"email": email, "password": pw}, headers={"X-Forwarded-For": "203.0.113.9"})


def _directory_has(email: str, region: str = "us") -> None:
    RS.store_remote_directory({email: {"id": "abc", "region": region, "tier": "user", "created": "2026"}}, region)


# ── login across regions ─────────────────────────────────────────────────────

def test_login_answers_421_before_any_password_check_for_an_address_the_peer_hosts(env):
    _directory_has("only.on.us@example.com")
    for pw in ("wrong", PW, ""):
        r = _login("only.on.us@example.com", pw)
        assert r.status_code == 421, r.text
        d = r.json()["detail"]
        assert d["code"] == "wrong_region" and d["region"] == "us" and d["base_url"] == "https://us.aerostator.com"
        assert d["signin"] is True and "handoff" not in d                 # no local copy: nothing proves who it is
        assert r.headers["x-motres-region"] == "eu"
    assert not env["E"].LOGIN_ACCOUNT.blocked("only.on.us@example.com")


def test_single_region_mode_and_unmigrated_accounts_sign_in_as_before(env):
    _directory_has("only.on.us@example.com")
    _as(env, "eu", peer=False)
    assert _login("only.on.us@example.com").status_code == 401           # single region: the directory is not consulted
    _as(env, "eu")
    RS.set_home_region(US_USER, "us", by="admin")                          # classified US, NOT migrated
    assert _login(US_USER).status_code == 200
    RS.set_home_region(STAFF, "us", by="admin")
    assert _login(STAFF).status_code == 200


def test_migrated_copy_with_the_right_password_hands_off_without_a_second_password(env):
    _migrate_record(env)
    bad = _login(US_USER, "not the password")
    assert bad.status_code == 421 and "handoff" not in bad.json()["detail"]
    good = _login(US_USER)
    assert good.status_code == 421
    ticket = good.json()["detail"]["handoff"]
    assert ticket.startswith("v1.") and "signin" not in good.json()["detail"]
    _as(env, "us")
    r = client.post("/api/auth/region-handoff", json={"ticket": ticket})
    assert r.status_code == 200, r.text
    assert env["U"].verify_token(r.json()["token"]).ok and r.json()["user"]["email"] == US_USER
    again = client.post("/api/auth/region-handoff", json={"ticket": ticket})
    assert again.status_code == 400 and again.json()["code"] == "auth.region_handoff_invalid"   # single use


def test_ticket_refusals(env):
    rec = _migrate_record(env)
    now = time.time()
    t_ok = H.mint(US_USER, "us", method="session", rec=rec, now=now)
    assert H.mint(US_USER, "eu", method="session", rec=rec) == ""         # never a ticket for this server itself
    assert H.mint(US_USER, "us", method="anything") == ""
    _as(env, "us")
    with pytest.raises(H.HandoffError) as e:
        H.consume(t_ok, now=now + H.TTL_S + H.GRACE_S + 1)
    assert e.value.reason == "expired"
    head, body, sig = t_ok.split(".")
    with pytest.raises(H.HandoffError) as e:
        H.consume(f"{head}.{body}.{'0' * 64}", now=now)
    assert e.value.reason == "bad_signature"
    env["U"].set_password(US_USER, "a brand new password 1")             # changed on the target after the migration
    with pytest.raises(H.HandoffError) as e:
        H.consume(t_ok, now=now)
    assert e.value.reason == "password_changed"
    _as(env, "eu")
    t_eu = H.mint(US_USER, "us", method="google", now=now)
    with pytest.raises(H.HandoffError) as e:
        H.consume(t_eu, now=now)                                          # consumed on the minting server
    assert e.value.reason == "wrong_target"
    _as(env, "us", peer=False)
    with pytest.raises(H.HandoffError) as e:
        H.consume(t_eu, now=now)
    assert e.value.reason == "not_configured"
    _as(env, "us")
    assert H.consume(t_eu, now=now)["method"] == "google"                 # google proof is current: no digest needed


def test_google_sign_in_on_the_wrong_server_carries_a_ticket(env):
    A = env["A"]
    env["mp"].setattr(A, "GOOGLE_CLIENT_ID", "test-client")
    env["mp"].setattr(A, "_verify_google_token", lambda cred: {"email": "g.person@example.com", "email_verified": True})
    _directory_has("g.person@example.com")
    r = client.post("/api/auth/google", json={"credential": "x"})
    assert r.status_code == 421, r.text
    assert r.json()["detail"]["handoff"].startswith("v1.") and env["U"].get_user("g.person@example.com") is None


# ── sign-up uniqueness ───────────────────────────────────────────────────────

def _register(email):
    return client.post("/api/auth/register", json={"email": email, "password": PW, "name": "New Person",
                                                    "accept_terms": True, "terms_version": "x"},
                       headers={"X-Forwarded-For": "203.0.113.10"})


def test_sign_up_asks_the_peer_and_answers_registered_elsewhere(env):
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    asked = []

    def fake_get(path, **kw):
        asked.append((path, kw.get("timeout")))
        return {"region": "us", "entries": {"new.us@example.com": {"id": "i1", "region": "us", "tier": "user",
                                                                    "created": "2026", "name": "dropped"}}}
    env["mp"].setattr(P, "get", fake_get)
    r = _register("new.us@example.com")
    assert r.status_code == 421 and r.json()["detail"]["reason"] == "registered_elsewhere"
    assert asked == [("/directory?email=new.us%40example.com", 2.0)]
    assert env["U"].get_user("new.us@example.com") is None
    entry = RS.load_remote_directory()["entries"]["new.us@example.com"]
    assert set(entry) == set(RS.DIRECTORY_FIELDS)                         # the extra field was dropped


def test_sign_up_proceeds_when_the_peer_is_down_and_the_sync_flags_the_duplicate(env):
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    env["mp"].setattr(P, "get", lambda path, **kw: None)                  # peer down
    assert _register("race@example.com").status_code == 202
    assert env["U"].get_user("race@example.com") is not None
    doc = RS.store_remote_directory({"race@example.com": {"id": "r", "region": "us", "tier": "user"}}, "us")
    assert doc["duplicates"] == ["race@example.com"]
    j = client.get("/api/admin/regions/accounts").json()
    assert j["duplicates"] == ["race@example.com"]
    assert env["U"].get_user("race@example.com") is not None              # never merged automatically


def test_peer_directory_answers_one_address(env):
    h = P.sign_headers("GET", "/api/peer/v1/directory?email=eu.user%40example.com", secret=SECRET, region="us")
    d = client.get("/api/peer/v1/directory?email=eu.user%40example.com", headers=h).json()
    assert list(d["entries"]) == [EU_USER]
    h = P.sign_headers("GET", "/api/peer/v1/directory?email=nobody%40example.com", secret=SECRET, region="us")
    assert client.get("/api/peer/v1/directory?email=nobody%40example.com", headers=h).json()["entries"] == {}


def test_peer_lookup_is_none_when_unreachable(env):
    down = httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert RS.peer_lookup(EU_USER, transport=down) is None
    up = httpx.MockTransport(lambda req: httpx.Response(200, json={"region": "us", "entries": {}}))
    assert RS.peer_lookup(EU_USER, transport=up) == {"hosted": False}


# ── reset / mail links ───────────────────────────────────────────────────────

def test_reset_request_and_mail_links_lead_to_the_home_server(env):
    _directory_has("only.on.us@example.com")
    r = client.post("/api/auth/reset/request", json={"email": "only.on.us@example.com"})
    assert r.status_code == 421 and r.json()["detail"]["signin"] is True
    sid = env["U"].issue_short_link(US_USER, "verify")
    _migrate_record(env)
    page = client.get(f"/api/auth/link/v/{sid}")
    assert page.status_code == 200 and "https://us.aerostator.com/?signin=1" in page.text
    sid_r = env["U"].issue_short_link(US_USER, "reset")
    page = client.get(f"/api/auth/link/r/{sid_r}")
    assert "https://us.aerostator.com/?signin=1" in page.text
    assert RR.serving_region(EU_USER) is None


# ── the guard per account, the freeze ───────────────────────────────────────

def _call(path, token="", method="GET"):
    sent, passed = [], []

    async def inner(scope, receive, send):
        passed.append(True)

    async def send(m):
        sent.append(m)

    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    asyncio.run(G.RegionGuardMiddleware(inner)({"type": "http", "path": path, "method": method, "headers": headers},
                                               None, send))
    if passed:
        return "passed"
    return sent[0]["status"], json.loads(sent[1]["body"])


def test_per_account_guard_works_with_the_global_guard_off(env):
    U = env["U"]
    RS.set_home_region(EU_USER, "us", by="admin")                          # classified US but not migrated
    _migrate_record(env)
    status, body = _call("/api/projects", U.issue_token(US_USER))
    assert status == 421 and body["detail"]["region"] == "us" and body["detail"]["handoff"].startswith("v1.")
    assert _call("/api/projects", U.issue_token(EU_USER)) == "passed"
    assert _call("/api/projects", U.issue_token(STAFF)) == "passed"
    assert _call("/api/auth/sessions", U.issue_token(US_USER)) == "passed"            # sign-in paths stay open
    with pytest.raises(ValueError):
        RS.set_region_guard(STAFF, True)                                   # staff are never guarded
    _as(env, "eu", peer=False)
    status, body = _call("/api/projects", U.issue_token(US_USER))
    assert status == 421 and "handoff" not in body["detail"]              # no peer: still refused, no ticket


def test_guard_ticket_from_a_session_opens_the_target(env):
    U = env["U"]
    _migrate_record(env)
    _, body = _call("/api/projects", U.issue_token(US_USER))
    _as(env, "us")
    r = client.post("/api/auth/region-handoff", json={"ticket": body["detail"]["handoff"]})
    assert r.status_code == 200, r.text


def test_freeze_refuses_writes_and_keeps_reads(env):
    U = env["U"]
    RS.set_region_freeze(EU_USER, True, by="test")
    tok = U.issue_token(EU_USER)
    status, body = _call("/api/projects", tok, "POST")
    assert status == 423 and body["detail"]["code"] == "account_migrating"
    assert _call("/api/projects", tok, "GET") == "passed"
    assert _call("/api/projects", U.issue_token(US_USER), "POST") == "passed"
    with pytest.raises(ValueError):
        RS.set_region_freeze(STAFF, True)
    RS.set_region_freeze(EU_USER, False)
    assert _call("/api/projects", tok, "POST") == "passed"


def test_nothing_flagged_means_no_token_is_verified(env):
    U = env["U"]
    tok = U.issue_token(US_USER)

    def boom(*a, **k):
        raise AssertionError("verify_token called with nothing flagged")
    env["mp"].setattr(U, "verify_token", boom)
    assert _call("/api/projects", tok, "POST") == "passed"
