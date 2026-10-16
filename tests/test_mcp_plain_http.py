"""Plain-HTTP discovery of the MCP server (docs/MCP_DISCOVERY.md "Plain HTTP").

An external checker or web-fetch tool does ``GET https://aerostator.com/mcp``
first, then maybe ``/.well-known/mcp.json`` and ``/llms.txt``.  Before this,
all three said nothing useful (401 / 404 / the SPA's index.html).

Real McpGate + SDK + the discovery router on a small FastAPI app; tmp stores.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from fastapi import FastAPI
from starlette.testclient import TestClient

from motor_ai_sim import agent_keys as K
from motor_ai_sim import auth
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_discovery as D
from motor_ai_sim import users as U
from motor_ai_sim.routes import mcp_discovery as R

BASE = "https://aerostator.test"
A = "alice@example.com"
MCP_ACCEPT = "application/json, text/event-stream"
BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8"


@pytest.fixture()
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "")
    monkeypatch.delenv("MCP_ANON_RATE_PER_MIN", raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(A, "pw-long-enough-" + A, role="user")
    K.reset_quotas()
    D.reset_hints()

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield
    app = FastAPI(lifespan=_ls)
    app.include_router(R.router)
    mcp_app.install(app)
    with TestClient(app) as client:
        yield client


def _registry():
    """name -> description, straight from the SDK registry (as tools/list)."""
    return {t.name: t.description for t in asyncio.run(mcp_app.build_server().list_tools())
            if t.name in mcp_app.PUBLIC_TOOLS}


def _no_user_data(text: str):
    low = text.lower()
    for bad in (A, "alice", "@example.com", "localhost", "127.0.0.1"):
        assert bad not in low, bad


# ── 1. anonymous GET /mcp -> service card ────────────────────────────────────

@pytest.mark.parametrize("accept", [None, "*/*", "application/json"])
def test_plain_get_returns_the_json_service_card(c, accept):
    r = c.get("/mcp", headers={"Accept": accept} if accept else {})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["cache-control"] == "public, max-age=300"
    assert "Accept" in r.headers["vary"] and "Authorization" in r.headers["vary"]
    assert "/.well-known/mcp.json" in r.headers["link"]
    card = r.json()
    assert card["service"] == "AeroStator"
    m = card["mcp"]
    assert m["endpoint"] == BASE + "/mcp" and m["transport"] == "streamable-http"
    assert m["method"] == "POST" and m["json_rpc"] == "2.0"
    assert "2025-06-18" in m["protocol_versions"] and "2025-11-25" in m["protocol_versions"]
    # the public tools come from the SDK registry, not a hand-written list
    reg = _registry()
    rows = {t["name"]: t["description"] for t in card["public_tools"]}
    assert set(rows) == set(reg) == set(mcp_app.PUBLIC_TOOLS)
    for n, d in rows.items():
        assert d and d in " ".join(reg[n].split())
    assert card["try_it"]["initialize"]["body"]["method"] == "initialize"
    assert card["try_it"]["tools_list"]["body"] == {"jsonrpc": "2.0", "id": 2,
                                                    "method": "tools/list", "params": {}}
    a = card["authentication"]
    assert a["resource_metadata_url"] == BASE + "/.well-known/oauth-protected-resource/mcp"
    assert a["authorization_server_metadata_url"] == BASE + "/.well-known/oauth-authorization-server"
    assert a["sign_up_url"] == BASE + "/?signup=1" and a["sign_in_url"] == BASE + "/?signin=1"
    assert card["connect"]["claude_code"] == f"claude mcp add --transport http aerostator {BASE}/mcp"
    assert "claude_ai" in card["connect"] and "chatgpt" in card["connect"]
    assert card["docs_url"].startswith("https://")
    _no_user_data(r.text)


def test_the_sample_requests_in_the_card_work(c):
    card = c.get("/mcp").json()
    for key in ("initialize", "tools_list"):
        s = card["try_it"][key]
        r = c.post("/mcp", headers=s["headers"], json=s["body"])
        assert r.status_code == 200, (key, r.text)
    tools = {t["name"] for t in r.json()["result"]["tools"]}
    assert tools == {t["name"] for t in card["public_tools"]}


def test_browser_gets_html(c):
    r = c.get("/mcp", headers={"Accept": BROWSER})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "public, max-age=300"
    t = r.text
    assert "<h1>AeroStator — MCP server</h1>" in t
    for n in mcp_app.PUBLIC_TOOLS:
        assert f"<code>{n}</code>" in t
    assert "claude mcp add --transport http aerostator" in t
    assert "<script" not in t.lower()
    _no_user_data(t)
    # JSON still wins when the client ranks it higher
    r = c.get("/mcp", headers={"Accept": "application/json, text/html;q=0.5"})
    assert r.headers["content-type"].startswith("application/json")


def test_head_is_the_card_without_a_body(c):
    r = c.head("/mcp")
    assert r.status_code == 200 and r.content == b""
    assert r.headers["content-type"].startswith("application/json")


def test_html_card_escapes_values(monkeypatch):
    card = D.service_card([{"name": "describe_service", "description": "<b>x</b>. y"}])
    card["summary"] = "<script>alert(1)</script>"
    h = D.service_card_html(card)
    assert "<script>alert" not in h and "&lt;script&gt;" in h


# ── 2. anonymous SSE GET / DELETE -> 405 Allow: POST ─────────────────────────

@pytest.mark.parametrize("accept", ["text/event-stream", MCP_ACCEPT])
def test_anonymous_sse_get_is_405(c, accept):
    r = c.get("/mcp", headers={"Accept": accept})
    assert r.status_code == 405 and r.headers["allow"] == "POST"
    assert "www-authenticate" not in r.headers          # nothing to sign in to here
    assert r.json()["error"]["data"]["error"] == "method_not_allowed"


def test_anonymous_delete_is_405(c):
    r = c.delete("/mcp")
    assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_ts_sdk_sequence_after_anonymous_initialize(c):
    """What the TypeScript SDK does: initialize, notifications/initialized,
    then GET with Accept text/event-stream.  405 is benign for it (no OAuth);
    a 401 there would have started the OAuth flow at connect time."""
    h = {"Accept": MCP_ACCEPT, "Content-Type": "application/json"}
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                        "params": {"protocolVersion": "2025-11-25",
                                                   "capabilities": {},
                                                   "clientInfo": {"name": "ts", "version": "1"}}})
    assert r.status_code == 200
    r = c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    r = c.get("/mcp", headers={"Accept": "text/event-stream",
                               "MCP-Protocol-Version": "2025-11-25"})
    assert r.status_code == 405 and "www-authenticate" not in r.headers


# ── credentials presented on GET: unchanged ─────────────────────────────────

def test_get_with_a_bad_token_is_still_401(c):
    for accept in (None, "text/event-stream"):
        h = {"Authorization": "Bearer emk_bad_bad"}
        if accept:
            h["Accept"] = accept
        r = c.get("/mcp", headers=h)
        assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    r = c.delete("/mcp", headers={"Authorization": "Bearer emk_bad_bad"})
    assert r.status_code == 401


def test_authenticated_get_and_delete_go_to_the_sdk(c):
    """Unchanged: a signed-in GET / DELETE is the SDK's business, never the
    anonymous card.  (An authenticated ``GET`` with ``Accept:
    text/event-stream`` opens the SDK's SSE stream, which stays open — not
    exercised here because the in-process test client cannot time it out.)"""
    tok, _ = K.create_key(A, "t")
    for method, accept in (("GET", "application/json"), ("DELETE", MCP_ACCEPT)):
        r = c.request(method, "/mcp", headers={"Authorization": f"Bearer {tok}",
                                               "Accept": accept})
        assert r.status_code != 401, (method, r.status_code)
        assert '"service": "AeroStator"' not in r.text and "public_tools" not in r.text


def test_card_counts_against_the_anonymous_quota(c, monkeypatch):
    monkeypatch.setenv("MCP_ANON_RATE_PER_MIN", "2")
    codes = [c.get("/mcp", headers={"X-Forwarded-For": "198.51.100.44"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


# ── 2026-07-28 clients probe with server/discover ────────────────────────────

_ENVELOPE = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
             "io.modelcontextprotocol/clientCapabilities": {},
             "io.modelcontextprotocol/clientInfo": {"name": "modern", "version": "1"}}


def _modern(c, method, mid):
    return c.post("/mcp", headers={"Accept": MCP_ACCEPT, "Content-Type": "application/json",
                                   "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method},
                  json={"jsonrpc": "2.0", "id": mid, "method": method,
                        "params": {"_meta": _ENVELOPE}})


def test_anonymous_server_discover_and_modern_tools_list(c):
    r = _modern(c, "server/discover", 1)
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    assert "describe_service" in res["instructions"] and "tools" in res["capabilities"]
    r = _modern(c, "tools/list", 2)
    assert {t["name"] for t in r.json()["result"]["tools"]} == set(mcp_app.PUBLIC_TOOLS)
    _no_user_data(json.dumps(res))


# ── 3. /.well-known/mcp.json ─────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/.well-known/mcp.json", "/.well-known/mcp",
                                  "/.well-known/mcp/server-card.json"])
def test_server_card(c, path):
    r = c.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["cache-control"] == "public, max-age=300"
    s = r.json()
    assert s["title"] == "AeroStator" and s["name"] == "com.aerostator/mcp"
    assert s["remotes"] == [{"type": "streamable-http", "url": BASE + "/mcp"}]
    assert s["authentication"]["protectedResourceMetadata"] == \
        BASE + "/.well-known/oauth-protected-resource/mcp"
    assert {t["name"] for t in s["tools"]} == set(mcp_app.PUBLIC_TOOLS)
    assert s["license"] == "Apache-2.0"
    _no_user_data(r.text)


# ── 4. /llms.txt ─────────────────────────────────────────────────────────────

def test_llms_txt(c):
    r = c.get("/llms.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["cache-control"] == "public, max-age=300"
    t = r.text
    assert t.startswith("# AeroStator\n\n> ")
    assert f"({BASE}/mcp)" in t and f"({BASE}/.well-known/mcp.json)" in t
    for n in mcp_app.PUBLIC_TOOLS:
        assert f"`{n}`" in t
    assert "<html" not in t.lower()
    _no_user_data(t)


def test_nginx_serves_llms_txt_from_the_api_before_the_spa():
    conf = (Path(__file__).resolve().parents[1] / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    i = conf.index("location = /llms.txt")
    assert "proxy_pass http://api:8001;" in conf[i:i + 200]
    # /.well-known/ (server card) is already proxied to the API
    assert "location /.well-known/" in conf and "location = /mcp" in conf
