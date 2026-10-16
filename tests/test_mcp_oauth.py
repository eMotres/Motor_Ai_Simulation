"""MCP Stage 2 — OAuth 2.1 (docs/MCP_2026-09-28.md): discovery metadata, DCR,
authorization code + PKCE, refresh rotation, revocation, scopes, and that agent
keys keep working.  Reuses the Stage-1 fixture (real SDK behind McpGate) plus
the OAuth router; the web session is stubbed (owner = the bearer's e-mail).
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("mcp")

from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from motor_ai_sim import agent_keys as K
from motor_ai_sim import oauth as O
from motor_ai_sim.routes import oauth as R

from tests.test_mcp_stage1 import A, B, _call, _payload, _rpc  # noqa: F401
from tests.test_mcp_stage1 import env as _stage1_env  # noqa: F401

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture()
def oenv(_stage1_env, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aerostator.com")
    O._touched.clear()

    def _fake_owner(authorization):
        if not isinstance(authorization, str) or not authorization.startswith("Session "):
            raise HTTPException(401, detail="Sign in")
        return authorization.split(" ", 1)[1]
    monkeypatch.setattr(R, "_owner", _fake_owner)
    from contextlib import asynccontextmanager
    from motor_ai_sim import mcp_app

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield
    app = FastAPI(lifespan=_ls)
    app.include_router(R.router)
    mcp_app.install(app)
    with TestClient(app, follow_redirects=False) as c:
        yield {**_stage1_env, "c": c}


def _pkce():
    v = secrets.token_urlsafe(48)
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, ch


def _register(c, **kw):
    body = {"client_name": "Claude", "redirect_uris": [REDIRECT]} | kw
    return c.post("/oauth/register", json=body)


def _authorize(c, cid, challenge, scope="catalog:read machines:read", state="xyz", **kw):
    q = {"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT,
         "code_challenge": challenge, "code_challenge_method": "S256",
         "scope": scope, "state": state, "resource": "https://aerostator.com/mcp"} | kw
    return c.get("/oauth/authorize", params=q)


def _flow(c, owner=A, scope="catalog:read machines:read"):
    cid = _register(c).json()["client_id"]
    v, ch = _pkce()
    r = _authorize(c, cid, ch, scope=scope)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/agent-consent?request=")
    rid = parse_qs(urlsplit(loc).query)["request"][0]
    info = c.get(f"/api/oauth/requests/{rid}", headers={"Authorization": f"Session {owner}"})
    assert info.status_code == 200 and info.json()["client_name"] == "Claude"
    d = c.post(f"/api/oauth/requests/{rid}", json={"approve": True},
               headers={"Authorization": f"Session {owner}"}).json()["redirect"]
    q = parse_qs(urlsplit(d).query)
    assert d.startswith(REDIRECT) and q["state"] == ["xyz"]
    code = q["code"][0]
    t = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": REDIRECT, "client_id": cid,
                                     "code_verifier": v})
    assert t.status_code == 200, t.text
    return cid, t.json()


def test_metadata_documents(oenv):
    c = oenv["c"]
    for p in ("/.well-known/oauth-protected-resource",
              "/.well-known/oauth-protected-resource/mcp"):
        m = c.get(p).json()
        assert m["resource"] == "https://aerostator.com/mcp"
        assert m["authorization_servers"] == ["https://aerostator.com"]
        assert set(m["scopes_supported"]) == {"catalog:read", "machines:read",
                                              "designs:write", "simulate"}
    a = c.get("/.well-known/oauth-authorization-server").json()
    assert a["issuer"] == "https://aerostator.com"
    assert a["code_challenge_methods_supported"] == ["S256"]
    assert a["registration_endpoint"].endswith("/oauth/register")
    assert "refresh_token" in a["grant_types_supported"]


def test_401_carries_resource_metadata(oenv):
    # (anonymous tools/list is public since docs/MCP_DISCOVERY.md; a data tool is not)
    r = _call(oenv["c"], None, "list_machines")
    assert r.status_code == 401
    assert 'resource_metadata="https://aerostator.com/.well-known/oauth-protected-resource/mcp"' \
        in r.headers["www-authenticate"]


def test_dcr_rules(oenv):
    c = oenv["c"]
    ok = _register(c)
    assert ok.status_code == 201 and ok.json()["client_id"].startswith("emc_")
    assert "client_secret" not in ok.json()
    assert _register(c, redirect_uris=["http://evil.example/cb"]).status_code == 400
    assert _register(c, redirect_uris=["http://localhost:3000/cb"]).status_code == 201
    assert _register(c, redirect_uris=["https://x.example/cb#frag"]).status_code == 400
    assert _register(c, redirect_uris=[]).status_code == 400
    conf = _register(c, token_endpoint_auth_method="client_secret_post").json()
    assert conf["client_secret"]


def test_authorize_rejects(oenv):
    c = oenv["c"]
    cid = _register(c).json()["client_id"]
    _, ch = _pkce()
    # redirect URI must match exactly -> error page, never a redirect
    r = _authorize(c, cid, ch, redirect_uri=REDIRECT + "/")
    assert r.status_code == 400
    assert _authorize(c, "emc_nope", ch).status_code == 400
    # plain PKCE refused -> error redirected back to the client
    r = _authorize(c, cid, ch, code_challenge_method="plain")
    assert r.status_code == 302 and "error=invalid_request" in r.headers["location"]
    r = _authorize(c, cid, ch, scope="simulate:run")
    assert "error=invalid_scope" in r.headers["location"]


def test_full_flow_and_scopes(oenv):
    c = oenv["c"]
    _, tok = _flow(c)
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == O.ACCESS_TTL_S
    at = tok["access_token"]
    assert at.startswith("emo_") and tok["refresh_token"].startswith("emr_")
    out = _payload(_call(c, at, "list_machines"))
    assert "__error__" not in out
    # stored hashed only
    raw = O.store_path().read_text(encoding="utf-8")
    assert at not in raw and tok["refresh_token"] not in raw
    # audit + grant listed for the owner, not for B
    assert any(r.get("kind") == "oauth" and r.get("tool") == "list_machines"
               for r in K.read_audit(A))
    gs = c.get("/api/oauth/grants", headers={"Authorization": f"Session {A}"}).json()["grants"]
    assert len(gs) == 1 and gs[0]["client_name"] == "Claude" and gs[0]["active"]
    assert c.get("/api/oauth/grants", headers={"Authorization": f"Session {B}"}).json()["grants"] == []


def test_scope_enforcement(oenv):
    c = oenv["c"]
    _, tok = _flow(c, scope="catalog:read")
    at = tok["access_token"]
    r = _call(c, at, "list_machines")
    assert r.status_code == 403 and "insufficient_scope" in r.headers["www-authenticate"]
    assert "__error__" not in _payload(_call(c, at, "list_catalog", {"kind": "magnets"}))


def test_deny(oenv):
    c = oenv["c"]
    cid = _register(c).json()["client_id"]
    _, ch = _pkce()
    rid = parse_qs(urlsplit(_authorize(c, cid, ch).headers["location"]).query)["request"][0]
    d = c.post(f"/api/oauth/requests/{rid}", json={"approve": False},
               headers={"Authorization": f"Session {A}"}).json()["redirect"]
    assert "error=access_denied" in d and "state=xyz" in d
    # consent needs a session
    assert c.get(f"/api/oauth/requests/{rid}").status_code == 401


def test_wrong_verifier_and_code_reuse(oenv):
    c = oenv["c"]
    cid = _register(c).json()["client_id"]
    v, ch = _pkce()
    rid = parse_qs(urlsplit(_authorize(c, cid, ch).headers["location"]).query)["request"][0]
    d = c.post(f"/api/oauth/requests/{rid}", json={"approve": True},
               headers={"Authorization": f"Session {A}"}).json()["redirect"]
    code = parse_qs(urlsplit(d).query)["code"][0]
    base = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT, "client_id": cid}
    bad = c.post("/oauth/token", data=base | {"code_verifier": _pkce()[0]})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    # the code is burnt even by the failed attempt
    again = c.post("/oauth/token", data=base | {"code_verifier": v})
    assert again.status_code == 400


def test_expired_and_revoked_access_token(oenv, monkeypatch):
    c = oenv["c"]
    _, tok = _flow(c)
    at = tok["access_token"]
    real = time.time
    monkeypatch.setattr(O, "_now", lambda: real() + O.ACCESS_TTL_S + 5)
    r = _rpc(c, at, "tools/list")
    assert r.status_code == 401 and r.json()["reason"] == "expired"
    assert 'error="invalid_token"' in r.headers["www-authenticate"]
    monkeypatch.setattr(O, "_now", real)
    assert _rpc(c, at, "tools/list").status_code == 200
    gid = c.get("/api/oauth/grants", headers={"Authorization": f"Session {A}"}).json()["grants"][0]["id"]
    assert c.delete(f"/api/oauth/grants/{gid}", headers={"Authorization": f"Session {A}"}).status_code == 200
    r = _rpc(c, at, "tools/list")
    assert r.status_code == 401 and r.json()["reason"] == "revoked"
    rr = c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": _flow_cid(c, tok),
                                      "refresh_token": tok["refresh_token"]})
    assert rr.status_code in (400, 401)


def _flow_cid(c, tok):
    gid = tok["refresh_token"][4:].split("_", 1)[0]
    return O._load()["grants"][gid]["client_id"]


def test_refresh_rotation_and_reuse_detection(oenv):
    c = oenv["c"]
    cid, tok = _flow(c)
    r1 = c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid,
                                      "refresh_token": tok["refresh_token"]})
    assert r1.status_code == 200
    new = r1.json()
    assert new["refresh_token"] != tok["refresh_token"]
    assert _rpc(c, new["access_token"], "tools/list").status_code == 200
    assert _rpc(c, tok["access_token"], "tools/list").status_code == 401   # replaced
    # replay the rotated refresh token -> grant revoked, new tokens die too
    r2 = c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid,
                                      "refresh_token": tok["refresh_token"]})
    assert r2.status_code == 400 and "reuse" in r2.json()["error_description"]
    assert _rpc(c, new["access_token"], "tools/list").status_code == 401
    # scope can narrow on refresh only after a fresh grant
    cid2, t2 = _flow(c)
    wide = c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": cid2,
                                        "refresh_token": t2["refresh_token"],
                                        "scope": "catalog:read simulate:run"})
    assert wide.status_code == 400 and wide.json()["error"] == "invalid_scope"


def test_confidential_client_and_rfc7009_revoke(oenv):
    c = oenv["c"]
    reg = _register(c, token_endpoint_auth_method="client_secret_basic").json()
    cid, sec = reg["client_id"], reg["client_secret"]
    v, ch = _pkce()
    rid = parse_qs(urlsplit(_authorize(c, cid, ch).headers["location"]).query)["request"][0]
    d = c.post(f"/api/oauth/requests/{rid}", json={"approve": True},
               headers={"Authorization": f"Session {A}"}).json()["redirect"]
    code = parse_qs(urlsplit(d).query)["code"][0]
    form = {"grant_type": "authorization_code", "code": code,
            "redirect_uri": REDIRECT, "code_verifier": v}
    basic = "Basic " + base64.b64encode(f"{cid}:wrong".encode()).decode()
    assert c.post("/oauth/token", data=form, headers={"Authorization": basic}).status_code == 401
    # (the failed client auth did not burn the code: client auth runs first)
    basic = "Basic " + base64.b64encode(f"{cid}:{sec}".encode()).decode()
    t = c.post("/oauth/token", data=form, headers={"Authorization": basic})
    assert t.status_code == 200, t.text
    at = t.json()["access_token"]
    assert c.post("/oauth/revoke", data={"token": at},
                  headers={"Authorization": basic}).status_code == 200
    assert _rpc(c, at, "tools/list").status_code == 401


def test_agent_keys_still_work(oenv):
    c = oenv["c"]
    tok, _ = K.create_key(A, "k")
    assert "__error__" not in _payload(_call(c, tok, "list_machines"))
    r = _rpc(c, "emk_bad_bad", "tools/list")
    assert r.status_code == 401
