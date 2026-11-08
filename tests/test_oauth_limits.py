"""The OAuth endpoints that take no credentials: a per-IP limit on what writes the store, a cap on pending requests, and a full
client table that no longer gives up clients in use or just registered."""
from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import agent_keys, auth_email as E, oauth as O
from motor_ai_sim.routes import oauth as R

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_keys, "_config_dir", lambda: tmp_path)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aerostator.com")
    E.reset_limits()
    app = FastAPI()
    app.include_router(R.router)
    with TestClient(app, follow_redirects=False) as c:
        yield c
    E.reset_limits()


def _meta(**kw) -> dict:
    return {"redirect_uris": [REDIRECT], "client_name": "Test", **kw}


def _challenge() -> str:
    verifier = secrets.token_urlsafe(48)
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _authorize(c, cid: str, ip: str = "203.0.113.7"):
    return c.get("/oauth/authorize", headers={"X-Real-IP": ip},
                 params={"client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
                         "code_challenge": _challenge(), "code_challenge_method": "S256", "state": "xyz"})


def _register(c, ip: str = "203.0.113.7"):
    return c.post("/oauth/register", headers={"X-Real-IP": ip}, json=_meta())


def _store() -> dict:
    return O._load()


def test_authorizations_from_one_address_stop_and_nothing_more_is_stored(client):
    cid = _register(client).json()["client_id"]
    for _ in range(E.OAUTH_AUTHORIZE_IP.max_hits):
        assert _authorize(client, cid).status_code == 302
    before = dict(_store()["requests"])
    r = _authorize(client, cid)
    assert r.status_code == 429
    assert r.headers["retry-after"] and r.json()["error"] == "temporarily_unavailable"
    assert _store()["requests"] == before


def test_another_address_still_connects(client):
    cid = _register(client).json()["client_id"]
    for _ in range(E.OAUTH_AUTHORIZE_IP.max_hits):
        _authorize(client, cid)
    r = _authorize(client, cid, ip="198.51.100.9")
    assert r.status_code == 302 and r.headers["location"].startswith("/agent-consent?request=")


def test_a_refused_authorization_does_not_count(client):
    """A request that stores nothing writes nothing, so a broken client is not locked out by its own retries."""
    cid = _register(client).json()["client_id"]
    for _ in range(E.OAUTH_AUTHORIZE_IP.max_hits + 2):
        assert client.get("/oauth/authorize", headers={"X-Real-IP": "203.0.113.7"},
                          params={"client_id": "nope"}).status_code == 400
    assert _authorize(client, cid).status_code == 302


def test_registrations_from_one_address_stop(client):
    for _ in range(E.OAUTH_REGISTER_IP.max_hits):
        assert _register(client).status_code == 201
    count = len(_store()["clients"])
    r = _register(client)
    assert r.status_code == 429 and r.headers["retry-after"]
    assert len(_store()["clients"]) == count
    assert _register(client, ip="198.51.100.9").status_code == 201


def test_the_pending_cap_refuses_a_new_request_and_keeps_the_pending_ones(client, monkeypatch):
    monkeypatch.setattr(O, "MAX_PENDING_REQUESTS", 2, raising=False)
    cid = _register(client).json()["client_id"]
    first = _authorize(client, cid, ip="198.51.100.1")
    assert first.status_code == 302 and _authorize(client, cid, ip="198.51.100.2").status_code == 302
    before = dict(_store()["requests"])
    refused = _authorize(client, cid, ip="198.51.100.3")
    assert refused.status_code == 302
    assert refused.headers["location"].startswith(REDIRECT) and "error=temporarily_unavailable" in refused.headers["location"]
    assert "state=xyz" in refused.headers["location"]
    assert _store()["requests"] == before


def test_expired_requests_do_not_hold_the_cap(client, monkeypatch):
    monkeypatch.setattr(O, "MAX_PENDING_REQUESTS", 1, raising=False)
    cid = _register(client).json()["client_id"]
    assert _authorize(client, cid, ip="198.51.100.1").status_code == 302
    now = O._now()
    monkeypatch.setattr(O, "_now", lambda: now + O.REQUEST_TTL_S + 1)
    r = _authorize(client, cid, ip="198.51.100.2")
    assert r.status_code == 302 and r.headers["location"].startswith("/agent-consent?request=")


def _fill_table(monkeypatch, age_s: float) -> list:
    """A full client table of idle clients registered ``age_s`` ago."""
    monkeypatch.setattr(O, "MAX_CLIENTS", 3)
    now = O._now()
    monkeypatch.setattr(O, "_now", lambda: now - age_s)
    ids = [O.register_client(_meta())["client_id"] for _ in range(3)]
    monkeypatch.setattr(O, "_now", lambda: now)
    return ids


def test_a_full_table_does_not_give_up_a_fresh_client(client, monkeypatch):
    ids = _fill_table(monkeypatch, age_s=60.0)
    r = _register(client, ip="198.51.100.1")
    assert r.status_code == 503 and r.json()["error"] == "temporarily_unavailable"
    assert sorted(_store()["clients"]) == sorted(ids)


def test_a_full_table_gives_up_an_old_idle_client(client, monkeypatch):
    ids = _fill_table(monkeypatch, age_s=2 * O.CLIENT_EVICT_MIN_AGE_S)
    assert _register(client, ip="198.51.100.1").status_code == 201
    assert len(_store()["clients"]) == 3
    assert len(set(ids) & set(_store()["clients"])) == 2


def test_a_full_table_keeps_a_client_with_an_authorization_in_progress(client, monkeypatch):
    ids = _fill_table(monkeypatch, age_s=2 * O.CLIENT_EVICT_MIN_AGE_S)
    for cid in ids:
        assert _authorize(client, cid, ip="198.51.100.1").status_code == 302
    assert _register(client, ip="198.51.100.2").status_code == 503
    assert sorted(_store()["clients"]) == sorted(ids)


def test_the_proposed_nginx_limits_are_not_wired_in():
    """The proposal is for the owner to review: nothing the deploy runs may load it, and it alone starts no deploy."""
    proposal = ROOT / "deploy" / "nginx_rate_limits_proposed.conf"
    text = proposal.read_text(encoding="utf-8")
    for needed in ("limit_req_zone", "limit_req ", "/api/auth/login", "/api/auth/register", "link/", "/oauth/register"):
        assert needed in text, needed
    assert "nginx_rate_limits_proposed" not in (ROOT / "deploy" / "Dockerfile.web").read_text(encoding="utf-8")
    assert "limit_req" not in (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location("plan_deploy", ROOT / "deploy" / "plan_deploy.py")
    plan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plan)
    assert not plan._back(["deploy/nginx_rate_limits_proposed.conf"])
    assert not plan._web(["deploy/nginx_rate_limits_proposed.conf"])
