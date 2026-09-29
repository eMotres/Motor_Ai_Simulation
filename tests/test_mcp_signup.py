"""Sign-up through MCP (docs/MCP_DISCOVERY.md "Sign-up"): an AI app starts the
OAuth authorization, the user creates an account ON THE CONSENT PAGE (never in
the chat), confirms the e-mail (the mailed link returns to that consent page,
possibly in another tab), signs in, allows, and the app gets its token â€” one
continuous flow.  Plus the pending-request TTL, the per-IP sign-up limit and
the anonymous ``start_sign_up`` tool.

Real routers (auth_local + oauth) and the real McpGate on a small FastAPI app;
the mail sender is stubbed; every store lives in tmp_path.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("mcp")

from fastapi import FastAPI
from starlette.testclient import TestClient

from motor_ai_sim import agent_keys as K
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_discovery as D
from motor_ai_sim import oauth as O

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
BASE = "https://aerostator.test"
NEW = "new.engineer@example.com"
PW = "correct horse battery staple"
ACCEPT = "application/json, text/event-stream"


@pytest.fixture()
def senv(tmp_path, monkeypatch):
    from motor_ai_sim import auth as A
    from motor_ai_sim import auth_email as E
    from motor_ai_sim import sessions as S
    from motor_ai_sim import users as U
    from motor_ai_sim.routes import auth_local, oauth as R

    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("PUBLIC_APP_URL", BASE)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(S, "_SESSIONS_FILE", tmp_path / ".sessions.json")
    monkeypatch.setattr(S, "_EVENTS_FILE", tmp_path / "auth_events.jsonl")
    monkeypatch.setattr(A, "_ADMIN_EMAILS", {"owner@example.com"})
    monkeypatch.setattr(A, "AUTH_ENFORCE", True)
    monkeypatch.setattr(A, "_reject_seen", {})
    monkeypatch.setattr(S, "_last_touch", {})
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    E.reset_limits()
    K.reset_quotas()
    D.reset_hints()
    O._touched.clear()
    outbox: list = []

    def fake_send(to, subject, body, *, block=False):
        outbox.append({"to": to, "subject": subject, "body": body})
        return True
    monkeypatch.setattr(E, "send", fake_send)

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield
    app = FastAPI(lifespan=_ls)
    app.include_router(auth_local.router)
    app.include_router(R.router)
    mcp_app.install(app)
    with TestClient(app, follow_redirects=False) as c:
        yield {"c": c, "outbox": outbox, "U": U, "E": E}
    E.reset_limits()


def _pkce():
    v = secrets.token_urlsafe(48)
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, ch


def _start_authorization(c):
    """What claude.ai / ChatGPT do on the 401: register, then open /authorize."""
    cid = c.post("/oauth/register", json={"client_name": "Claude",
                                          "redirect_uris": [REDIRECT]}).json()["client_id"]
    v, ch = _pkce()
    r = c.get("/oauth/authorize", params={
        "response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
        "code_challenge": ch, "code_challenge_method": "S256",
        "scope": "catalog:read machines:read", "state": "st8",
        "resource": BASE + "/mcp"})
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/agent-consent?request=")
    rid = parse_qs(urlsplit(loc).query)["request"][0]
    return cid, v, rid, loc


def _link_from(mail) -> str:
    return next(ln.strip() for ln in mail["body"].splitlines() if "verify=" in ln)


def _register(c, return_to=None, email=NEW, ip="203.0.113.40"):
    body = {"email": email, "password": PW, "name": "New Engineer"}
    if return_to is not None:
        body["return_to"] = return_to
    return c.post("/api/auth/register", json=body, headers={"X-Forwarded-For": ip})


def _login(c, email=NEW, ip="203.0.113.40"):
    return c.post("/api/auth/login", json={"email": email, "password": PW},
                  headers={"X-Forwarded-For": ip})


def test_authorize_signup_verify_consent_token(senv):
    c = senv["c"]
    # 1. an anonymous agent asks for user data -> 401 that says how to proceed
    r = c.post("/mcp", headers={"Accept": ACCEPT, "Content-Type": "application/json"},
               json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "list_machines", "arguments": {}}})
    assert r.status_code == 401 and r.json()["error"]["data"]["authorize_url"] == BASE + "/oauth/authorize"
    # 2. the AI app starts OAuth; the browser lands on the consent page
    cid, verifier, rid, consent = _start_authorization(c)
    created = O._load()["requests"][rid]["created_at"]
    # 3. "Create account" on that page (not signed in: the consent API refuses)
    assert c.get(f"/api/oauth/requests/{rid}").status_code == 401
    r = _register(c, return_to=consent)
    assert r.status_code == 202, r.text
    # the pending authorization now survives the e-mail round trip
    exp = O._load()["requests"][rid]["expires_at"]
    assert exp - created > O.REQUEST_TTL_S
    # 4. no account without the e-mail proof
    assert _login(c).status_code == 403
    mail = senv["outbox"][-1]
    assert mail["to"] == NEW
    link = _link_from(mail)
    u = urlsplit(link)
    assert f"{u.scheme}://{u.netloc}" == BASE and u.path == "/agent-consent"
    q = parse_qs(u.query)
    assert q["request"] == [rid]                  # back into the SAME authorization
    token = q["verify"][0]
    # 5. the link opens (maybe in another tab): the web posts the token + page
    v = c.post("/api/auth/verify", json={"token": token, "return_to": consent})
    assert v.status_code == 200 and v.json()["authorization_pending"] is True
    # 6. sign in, see the request, allow
    s = _login(c)
    assert s.status_code == 200, s.text
    h = {"Authorization": f"Bearer {s.json()['token']}"}
    info = c.get(f"/api/oauth/requests/{rid}", headers=h)
    assert info.status_code == 200 and info.json()["account"] == NEW
    d = c.post(f"/api/oauth/requests/{rid}", json={"approve": True}, headers=h).json()["redirect"]
    dq = parse_qs(urlsplit(d).query)
    assert d.startswith(REDIRECT) and dq["state"] == ["st8"]
    # 7. the AI app exchanges the code: signed in, same flow, no restart
    t = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": dq["code"][0],
                                     "redirect_uri": REDIRECT, "client_id": cid,
                                     "code_verifier": verifier})
    assert t.status_code == 200, t.text
    at = t.json()["access_token"]
    r = c.post("/mcp", headers={"Accept": ACCEPT, "Content-Type": "application/json",
                                "Authorization": f"Bearer {at}"},
               json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {x["name"] for x in r.json()["result"]["tools"]}
    assert {"list_machines", "list_catalog", "describe_service"} <= names
    # the password never reached the MCP audit log
    assert PW not in K.audit_path().read_text(encoding="utf-8")


def test_return_to_must_be_our_consent_page(senv):
    c = senv["c"]
    for bad in ("https://evil.example/agent-consent?request=" + "a" * 32,
                "//evil.example/x", "/agent-consent?request=short",
                "/agent-consent?request=" + "a" * 32 + "&next=https://evil.example",
                "/other?request=" + "a" * 32):
        assert _register(c, return_to=bad).status_code == 422, bad
    # a well-formed id of no pending request: loud, nothing mailed
    assert _register(c, return_to="/agent-consent?request=" + "b" * 32).status_code == 410
    assert senv["outbox"] == []
    # without return_to the link goes to the home page as before
    assert _register(c).status_code == 202
    assert urlsplit(_link_from(senv["outbox"][-1])).path == "/"


def test_pending_request_ttl(senv, monkeypatch):
    c = senv["c"]
    _, _, rid, consent = _start_authorization(c)
    t0 = O._load()["requests"][rid]["created_at"]
    real = time.time
    # plain pending request: 15 min
    assert O._load()["requests"][rid]["expires_at"] == pytest.approx(t0 + O.REQUEST_TTL_S, abs=2)
    # a sign-up extends it to 30 min from the step, capped at 1 h after /authorize
    monkeypatch.setattr(O, "_now", lambda: real() + 600)
    assert O.extend_request(rid)
    assert O._load()["requests"][rid]["expires_at"] == pytest.approx(
        t0 + 600 + O.SIGNUP_REQUEST_TTL_S, abs=3)
    monkeypatch.setattr(O, "_now", lambda: real() + 2000)
    assert O.extend_request(rid)
    assert O._load()["requests"][rid]["expires_at"] == pytest.approx(
        t0 + O.SIGNUP_MAX_AGE_S, abs=3)
    # past the cap: gone, and an extension never revives it
    monkeypatch.setattr(O, "_now", lambda: real() + O.SIGNUP_MAX_AGE_S + 5)
    assert O.extend_request(rid) is False
    assert O.describe_request(rid) is None
    assert _register(c, return_to=consent).status_code == 410
    # an untouched request expires at 15 min
    monkeypatch.setattr(O, "_now", real)
    _, _, rid2, _ = _start_authorization(c)
    monkeypatch.setattr(O, "_now", lambda: real() + O.REQUEST_TTL_S + 5)
    assert O.describe_request(rid2) is None


def test_signup_rate_limited_per_ip(senv):
    c = senv["c"]
    for i in range(5):
        assert _register(c, email=f"u{i}@example.com", ip="198.51.100.77").status_code == 202
    r = _register(c, email="u9@example.com", ip="198.51.100.77")
    assert r.status_code == 429
    assert _register(c, email="u9@example.com", ip="198.51.100.78").status_code == 202


def test_start_sign_up_anonymous(senv):
    c = senv["c"]
    h = {"Accept": ACCEPT, "Content-Type": "application/json", "X-Forwarded-For": "192.0.2.90"}
    users_before = senv["U"]._USERS_FILE.read_text() if senv["U"]._USERS_FILE.exists() else ""
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": "start_sign_up", "arguments": {}}})
    assert r.status_code == 200, r.text
    sc = r.json()["result"]["structuredContent"]
    out = sc.get("result", sc)
    assert out["sign_up_url"] == BASE + "/?signup=1"
    assert out["account_created_by_this_call"] is False
    assert "password" in out["never"] and out["user_message"]
    # nothing was created, nothing was mailed
    after = senv["U"]._USERS_FILE.read_text() if senv["U"]._USERS_FILE.exists() else ""
    assert after == users_before and senv["outbox"] == []
    # the tool has no input at all (nothing secret can be passed to it)
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                        "params": {}})
    tool = next(t for t in r.json()["result"]["tools"] if t["name"] == "start_sign_up")
    assert not tool["inputSchema"].get("properties")
    # and the next refusal from this client says sign_up
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                        "params": {"name": "list_machines", "arguments": {}}})
    assert r.status_code == 401
    assert r.json()["error"]["data"]["required_action"] == "sign_up"
