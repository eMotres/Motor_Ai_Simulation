"""MCP anonymous tier (docs/MCP_DISCOVERY.md): public discovery tools, the
fail-closed allow-list, the machine-readable auth errors (401 / 403), the
per-IP anonymous fair-use limit, and that nothing about any user leaks.

Same harness as Stage 1: the real SDK behind the real McpGate on a tiny
Starlette app; everything written to tmp_path.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

pytest.importorskip("mcp")

from motor_ai_sim import agent_designs as AD
from motor_ai_sim import agent_keys as K
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_discovery as D
from motor_ai_sim import oauth as O
from motor_ai_sim import users as U

from tests.test_mcp_stage1 import A, B  # noqa: F401
from tests.test_mcp_stage1 import env as _stage1_env  # noqa: F401

BASE = "https://aerostator.test"
ACCEPT = "application/json, text/event-stream"
PROTECTED = sorted(mcp_app.TOOL_SCOPES)


@pytest.fixture()
def denv(_stage1_env, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.delenv("MCP_ANON_RATE_PER_MIN", raising=False)
    monkeypatch.delenv("MCP_ANON_RATE_PER_DAY", raising=False)
    D.reset_hints()
    yield _stage1_env
    D.reset_hints()


def _post(c, body, token=None, ip="203.0.113.7"):
    h = {"Accept": ACCEPT, "Content-Type": "application/json", "X-Forwarded-For": ip}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=h, json=body)


def _rpc(c, method, params=None, token=None, ip="203.0.113.7", mid=1):
    return _post(c, {"jsonrpc": "2.0", "id": mid, "method": method,
                     "params": params or {}}, token, ip)


def _call(c, tool, args=None, token=None, ip="203.0.113.7", meta=None):
    p = {"name": tool, "arguments": args or {}}
    if meta:
        p["_meta"] = meta
    return _rpc(c, "tools/call", p, token, ip)


def _payload(r):
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    if res.get("isError"):
        return {"__error__": res["content"][0]["text"]}
    sc = res.get("structuredContent")
    if sc is not None:
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res["content"][0]["text"])


def _registered_tools():
    return {t.name for t in asyncio.run(mcp_app.build_server().list_tools())}


# ── the tables are consistent (fail closed) ─────────────────────────────────

def test_every_registered_tool_is_classified():
    reg = _registered_tools()
    assert not (mcp_app.PUBLIC_TOOLS & set(mcp_app.TOOL_SCOPES))
    assert reg == mcp_app.PUBLIC_TOOLS | set(mcp_app.TOOL_SCOPES)
    assert set(D.TOOL_SUMMARIES) == reg
    assert set(mcp_app.TOOL_SCOPES.values()) <= set(K.SCOPES)


def test_every_resource_template_and_prompt_is_classified():
    srv = mcp_app.build_server()
    res = {str(r.uri) for r in asyncio.run(srv.list_resources())}
    tpl = {str(t.uri_template) for t in asyncio.run(srv.list_resource_templates())}
    prm = {p.name for p in asyncio.run(srv.list_prompts())}
    for have, pub, priv in ((res, D.PUBLIC_RESOURCES, D.PRIVATE_RESOURCES),
                            (tpl, D.PUBLIC_RESOURCE_TEMPLATES, D.PRIVATE_RESOURCE_TEMPLATES),
                            (prm, D.PUBLIC_PROMPTS, D.PRIVATE_PROMPTS)):
        assert not (pub & priv)
        assert have <= pub | priv, (have, pub, priv)       # nothing unclassified


def test_later_prompts_and_templates_stay_hidden_anonymously(denv):
    """Something registered without being made public on purpose is never
    listed to an anonymous client (the list handlers filter, fail closed)."""
    srv = mcp_app.get_server()

    @srv.prompt(name="internal_prompt", description="not public")
    def internal_prompt() -> str:
        return "x"

    @srv.resource("emotres://private/{item}", name="private_items")
    def private_items(item: str) -> str:
        return item

    c = denv["c"]
    assert _rpc(c, "prompts/list").json()["result"]["prompts"] == []
    assert _rpc(c, "resources/templates/list").json()["result"]["resourceTemplates"] == []
    assert _rpc(c, "prompts/get", {"name": "internal_prompt"}).status_code == 401
    assert _rpc(c, "resources/read", {"uri": "emotres://private/a"}).status_code == 401
    tok, _ = K.create_key(A, "t")
    assert [p["name"] for p in _rpc(c, "prompts/list", token=tok).json()["result"]["prompts"]] == [
        "internal_prompt"]
    assert len(_rpc(c, "resources/templates/list", token=tok).json()["result"]["resourceTemplates"]) == 1


# ── presented Authorization header is never anonymous (review #3) ───────────

def test_empty_and_duplicate_authorization_headers(denv):
    c = denv["c"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    base = {"Accept": ACCEPT, "Content-Type": "application/json"}
    for v in ("", "   ", "Bearer", "Bearer   "):
        r = c.post("/mcp", headers={**base, "Authorization": v}, json=body)
        assert r.status_code == 401, (repr(v), r.text)
        assert 'error="invalid_token"' in r.headers["www-authenticate"]
        assert r.json()["error"]["data"]["required_action"] == "reauthenticate"
    tok, _ = K.create_key(A, "t")
    r = c.post("/mcp", json=body, headers=[
        ("Accept", ACCEPT), ("Content-Type", "application/json"),
        ("Authorization", f"Bearer {tok}"), ("Authorization", "Bearer emk_x_y")])
    assert r.status_code == 400
    assert r.json()["error"]["data"]["error"] == "invalid_request"
    r = c.post("/mcp", json=body, headers=[
        ("Accept", ACCEPT), ("Content-Type", "application/json"),
        ("Authorization", f"Bearer {tok}"), ("Authorization", f"Bearer {tok}")])
    assert r.status_code == 400


# ── audit keeps no argument values of anonymous / refused calls (review #4) ─

def test_audit_keeps_argument_names_only_for_anonymous_and_refused(denv):
    c = denv["c"]
    secret = "hunter2-secret-value"
    _call(c, "describe_service", {"password": secret}, ip="192.0.2.70")      # public
    _call(c, "list_machines", {"password": secret}, ip="192.0.2.70")         # 401
    _call(c, "drop_database", {"password": secret}, ip="192.0.2.70")         # 403 unknown
    ro, _ = K.create_key(A, "ro", scopes=["catalog:read"])
    _call(c, "list_machines", {"password": secret}, token=ro)                # 403 scope
    raw = K.audit_path().read_text(encoding="utf-8")
    assert secret not in raw
    rows = [r for r in K.read_audit(None) if r.get("tool") in
            ("describe_service", "list_machines", "drop_database")]
    assert len(rows) >= 4 and all('"arg_names": ["password"]' in (r["args"] or "") for r in rows)
    # an ACCEPTED signed-in call keeps its summary as before (Stage 1 contract)
    _call(c, "list_catalog", {"kind": "magnets", "query": "N52"}, token=ro)
    assert "N52" in K.read_audit(A)[0]["args"]


# ── the anonymous quota is not escaped by spoofing X-Forwarded-For (#2) ─────

def _proxied(c, real, spoof):
    """What the web container sends (realip on): X-Real-IP = the visitor, the
    visitor's own X-Forwarded-For with the hops appended."""
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                  headers={"Accept": ACCEPT, "Content-Type": "application/json",
                           "X-Real-IP": real, "X-Forwarded-For": f"{spoof}, {real}, {real}"})


def test_anonymous_quota_ignores_spoofed_headers(denv, monkeypatch):
    from starlette.testclient import TestClient
    from motor_ai_sim import client_ip as C
    monkeypatch.setattr(C, "resolver", lambda h: ["172.18.0.5"] if h == "web" else [])
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "web")
    C.reset()
    monkeypatch.setenv("MCP_ANON_RATE_PER_MIN", "2")
    # a direct peer rotating X-Forwarded-For / X-Real-IP: one bucket (its own)
    direct = TestClient(denv["c"].app, client=("198.51.100.201", 40000))
    codes = [_proxied(direct, f"203.0.113.{i}", "1.1.1.1").status_code for i in range(3)]
    assert codes == [200, 200, 429]
    # containers on other bridges (compute sandboxes 172.17, the ERP 172.19)
    # are not proxies either, whatever X-Real-IP they claim
    for peer in ("172.17.0.4", "172.19.0.2"):
        other = TestClient(denv["c"].app, client=(peer, 40000))
        codes = [_proxied(other, f"203.0.113.{i}", "1.1.1.1").status_code for i in range(3)]
        assert codes == [200, 200, 429], peer
    # the resolved web container: the visitor is counted, spoofing is useless
    proxy = TestClient(denv["c"].app, client=("172.18.0.5", 40000))
    codes = [_proxied(proxy, "203.0.113.99", f"10.1.1.{i}").status_code for i in range(3)]
    assert codes == [200, 200, 429]
    assert _proxied(proxy, "203.0.113.98", "10.1.1.1").status_code == 200
    C.reset()


# ── anonymous initialize / tools/list ───────────────────────────────────────

def test_anonymous_initialize(denv):
    r = _rpc(denv["c"], "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    ins = res["instructions"]
    for w in ("describe_service", "how_to_authenticate", "authentication_required",
              "Never ask for a password"):
        assert w in ins
    assert res["serverInfo"]["title"] == "AeroStator"
    assert _post(denv["c"], {"jsonrpc": "2.0", "method": "notifications/initialized"}
                 ).status_code in (200, 202)
    assert _rpc(denv["c"], "ping").status_code == 200


def test_anonymous_tools_list_is_the_public_subset(denv):
    tools = {t["name"]: t for t in _rpc(denv["c"], "tools/list").json()["result"]["tools"]}
    assert set(tools) == set(mcp_app.PUBLIC_TOOLS)
    for t in tools.values():
        ann = t["annotations"]
        assert ann["readOnlyHint"] is True and ann["openWorldHint"] is False
        assert ann["destructiveHint"] is False
        assert t["_meta"]["securitySchemes"] == [{"type": "noauth"}]
        assert t["description"].startswith("PUBLIC (no sign-in)")


def test_authenticated_tools_list_follows_scopes(denv):
    ro, _ = K.create_key(A, "ro")                           # default read scopes
    names = {t["name"] for t in _rpc(denv["c"], "tools/list", token=ro).json()["result"]["tools"]}
    read = {t for t, s in mcp_app.TOOL_SCOPES.items() if s in K.DEFAULT_SCOPES}
    assert names == set(mcp_app.PUBLIC_TOOLS) | read
    assert "start_design" not in names and "simulate" not in names
    full, _ = K.create_key(A, "full", scopes=list(K.SCOPES))
    tools = {t["name"]: t for t in _rpc(denv["c"], "tools/list", token=full).json()["result"]["tools"]}
    assert set(tools) == set(mcp_app.PUBLIC_TOOLS) | set(mcp_app.TOOL_SCOPES)
    assert tools["simulate"]["_meta"]["securitySchemes"] == [
        {"type": "oauth2", "scopes": ["simulate"]}]
    # public tools work with a key too (no scope needed)
    assert _payload(_call(denv["c"], "describe_service", token=ro))["name"] == "AeroStator"


# ── public tool outputs ──────────────────────────────────────────────────────

def test_public_tool_outputs(denv):
    c = denv["c"]
    ds = _payload(_call(c, "describe_service"))
    assert ds["license"] == "Apache-2.0" and ds["operator"]
    assert ds["mcp_endpoint"] == BASE + "/mcp" and ds["website"] == BASE + "/"

    caps = _payload(_call(c, "list_capabilities"))
    rows = {t["name"]: t for t in caps["tools"]}
    assert set(rows) == _registered_tools()
    for n, t in rows.items():
        assert t["purpose"]
        assert t["requires_auth"] is (n not in mcp_app.PUBLIC_TOOLS)
        assert t["required_scope"] == mcp_app.TOOL_SCOPES.get(n)
        assert t["read_only"] is (n not in mcp_app.WRITE_TOOLS)
    assert {s["scope"] for s in caps["scopes"]} == set(K.SCOPES)
    assert next(s for s in caps["scopes"] if s["scope"] == "simulate")["unlocks"] == ["get_job", "simulate"]

    ct = _payload(_call(c, "list_calculation_types"))["calculation_types"]
    ids = {t["id"]: t for t in ct}
    for i in ("machine_fit_check", "saved_performance", "design_from_requirements",
              "em_fem_transient", "thermal", "coupled_em_thermal", "efficiency_map",
              "mechanical", "controller_losses"):
        assert i in ids
    for t in ct:                           # every MCP tool named really exists
        assert set(t["tools"]) <= _registered_tools()
        assert t["available_via"] in ("mcp", "web")
    assert {ids[i]["simulate_what"] for i in ("em_fem_transient", "thermal",
                                              "coupled_em_thermal")} == set(AD.WHATS)

    req = _payload(_call(c, "get_input_requirements",
                         {"calculation_type": "design_from_requirements"}))
    f = {x["name"]: x for x in req["inputs"]}
    assert set(f) == set(AD.REQUIREMENT_FIELDS)
    assert {n for n, x in f.items() if x["required"]} == {
        "torque_nm", "speed_rpm", "dc_bus_v", "cooling", "duty"}
    assert f["torque_nm"]["unit"] == "N.m" and f["speed_rpm"]["unit"] == "/min"
    assert f["ambient_c"]["unit"] == "Cel"
    assert f["dc_bus_v"]["range"] == list(AD.DC_BUS_RANGE_V)
    assert f["cooling"]["options"] == list(AD.COOLINGS)
    assert f["duty"]["options"] == list(AD.DUTIES)
    sim = _payload(_call(c, "get_input_requirements", {"calculation_type": "coupled_em_thermal"}))
    steps = next(x for x in sim["inputs"] if x["name"] == "steps")
    assert steps["range"] == list(AD.STEPS_RANGE)
    fit = _payload(_call(c, "get_input_requirements", {"calculation_type": "machine_fit_check"}))
    assert {x["name"] for x in fit["inputs"] if x["required"]} == {"torque_nm", "speed_rpm"}
    web = _payload(_call(c, "get_input_requirements", {"calculation_type": "mechanical"}))
    assert web["available_via"] == "web" and web["inputs"] == []
    bad = _payload(_call(c, "get_input_requirements", {"calculation_type": "nope"}))
    assert "unknown calculation_type" in bad["__error__"]

    auth = _payload(_call(c, "how_to_authenticate"))
    o = auth["oauth"]
    assert o["resource_metadata_url"] == BASE + "/.well-known/oauth-protected-resource/mcp"
    assert o["authorization_server_metadata_url"] == BASE + "/.well-known/oauth-authorization-server"
    assert o["authorize_url"] == BASE + "/oauth/authorize"
    assert auth["sign_up_url"] == BASE + "/?signup=1" and auth["api_key"]["how"]
    assert {s["scope"] for s in auth["scopes"]} == set(K.SCOPES)
    assert auth["user_message"]

    su = _payload(_call(c, "start_sign_up"))
    assert su["sign_up_url"] == BASE + "/?signup=1"
    assert su["account_created_by_this_call"] is False and su["user_message"]

    # every URL is built from PUBLIC_BASE_URL (never localhost)
    blob = json.dumps([ds, caps, ct, req, auth, su])
    assert "localhost" not in blob and "127.0.0.1" not in blob


def test_guide_resource_is_public_other_methods_are_not(denv):
    c = denv["c"]
    res = _rpc(c, "resources/list").json()["result"]["resources"]
    assert [r["uri"] for r in res] == ["emotres://guide"]
    txt = _rpc(c, "resources/read", {"uri": "emotres://guide"}).json()["result"]["contents"][0]["text"]
    assert "describe_service" in txt
    assert _rpc(c, "resources/read", {"uri": "emotres://private"}).status_code == 401
    assert _rpc(c, "completion/complete", {}).status_code == 401
    assert _rpc(c, "logging/setLevel", {"level": "debug"}).status_code == 401
    # an anonymous SSE GET: no stream here (405, not a sign-in problem);
    # tests/test_mcp_plain_http.py covers the plain-GET service card
    assert c.get("/mcp", headers={"Accept": ACCEPT}).status_code == 405
    # a tool that exists nowhere: refused (not a sign-in problem)
    r = _call(c, "drop_database")
    assert r.status_code == 403 and r.json()["error"]["data"]["error"] == "unknown_tool"
    # one bad message poisons the whole batch
    r = _post(c, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "list_machines", "arguments": {}}}])
    assert r.status_code == 401 and r.json()["id"] == 2


# ── every protected tool: 401 + structured error, anonymously ───────────────

_ARGS = {"list_catalog": {"kind": "dies"}, "get_catalog_entry": {"kind": "dies", "id": "x"},
         "get_machine_performance": {"die": "a", "config": "b", "duty": "c"},
         "check_fit": {"torque_nm": 1, "speed_rpm": 100},
         "start_design": {"requirements": {"torque_nm": 1}},
         "get_design": {"design_id": "d-000000000000"},
         "simulate": {"design_id": "d-000000000000", "what": "em"},
         "get_job": {"job_id": "x"}, "get_design_result": {"design_id": "d-000000000000"},
         "open_in_configure": {"design_id": "d-000000000000"}}


@pytest.mark.parametrize("tool", PROTECTED)
def test_protected_tool_refused_anonymously(denv, tool):
    need = mcp_app.TOOL_SCOPES[tool]
    r = _call(denv["c"], tool, _ARGS.get(tool, {}))
    assert r.status_code == 401, r.text
    h = r.headers["www-authenticate"]
    assert h.startswith("Bearer ")
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in h
    scope = h.split('scope="', 1)[1].split('"', 1)[0].split()
    assert need in scope and "catalog:read" in scope
    assert "error=" not in h                   # RFC 6750: no error without credentials
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1 and body["reason"] == "no_token"
    e = body["error"]
    assert e["code"] == -32001 and e["message"] == "Authentication required"
    d = e["data"]
    assert d["error"] == "authentication_required" and d["required_action"] == "sign_in"
    assert d["required_scope"] == need and d["tool"] == tool
    assert d["sign_up_url"] == BASE + "/?signup=1"
    assert d["authorize_url"] == BASE + "/oauth/authorize"
    assert d["resource_metadata_url"].startswith(BASE)
    assert d["docs_url"].startswith("https://")
    um = d["user_message"]
    assert um and tool in um and um.count(". ") == 0 and um.endswith(".")


def test_nothing_protected_ran_anonymously(denv, monkeypatch):
    """The refusal happens in the gate: no tool body is ever entered."""
    from motor_ai_sim import mcp_tools as T
    hit = []
    for name in ("list_machines", "check_fit", "list_catalog"):
        monkeypatch.setattr(T, name, lambda *a, _n=name, **k: hit.append(_n))
    for tool in ("list_machines", "check_fit", "list_catalog"):
        assert _call(denv["c"], tool, _ARGS.get(tool, {})).status_code == 401
    assert hit == []


def test_sign_up_hint(denv):
    c = denv["c"]
    _payload(_call(c, "start_sign_up", ip="198.51.100.20"))
    d = _call(c, "list_machines", ip="198.51.100.20").json()["error"]["data"]
    assert d["required_action"] == "sign_up" and "Create" in d["user_message"]
    d = _call(c, "list_machines", ip="198.51.100.21").json()["error"]["data"]
    assert d["required_action"] == "sign_in"
    d = _call(c, "list_machines", ip="198.51.100.22",
              meta={"aerostator/account": "none"}).json()["error"]["data"]
    assert d["required_action"] == "sign_up"


# ── presented but bad credentials: 401 reauthenticate, for every method ─────

def test_invalid_expired_revoked_token(denv, monkeypatch):
    c = denv["c"]
    tok, rec = K.create_key(A, "t")
    for bad in ("emk_nope_nope", "garbage", tok[:-3] + "xyz", "emo_x_y"):
        for r in (_rpc(c, "tools/list", token=bad), _call(c, "describe_service", token=bad)):
            assert r.status_code == 401, (bad, r.text)
            assert 'error="invalid_token"' in r.headers["www-authenticate"]
            d = r.json()["error"]["data"]
            assert d["error"] == "invalid_token" and d["required_action"] == "reauthenticate"
    assert K.revoke_key(A, rec["id"])
    r = _call(c, "list_machines", token=tok)
    assert r.status_code == 401 and r.json()["reason"] == "revoked"
    d = r.json()["error"]["data"]
    assert d["required_action"] == "reauthenticate" and d["tool"] == "list_machines"
    # an expired OAuth access token (grant minted directly; the full OAuth
    # flow is tests/test_mcp_oauth.py and tests/test_mcp_signup.py)
    gid = "g1"
    box = {}

    def _mk(d):
        d["grants"][gid] = {"id": gid, "owner": A, "client_id": "emc_test",
                            "scopes": list(K.DEFAULT_SCOPES), "created_at": time.time(),
                            "revoked_at": None}
        box["t"] = O._mint(d["grants"][gid])
    O._mutate(_mk)
    at = box["t"]["access_token"]
    assert _rpc(c, "tools/list", token=at).status_code == 200
    real = time.time
    monkeypatch.setattr(O, "_now", lambda: real() + O.ACCESS_TTL_S + 5)
    r = _rpc(c, "tools/list", token=at)
    assert r.status_code == 401 and r.json()["reason"] == "expired"
    assert r.json()["error"]["data"]["required_action"] == "reauthenticate"


def test_disabled_account(denv):
    tok, _ = K.create_key(A, "t")
    U.update_user(A, disabled=True)
    r = _call(denv["c"], "list_machines", token=tok)
    assert r.status_code == 401 and r.json()["reason"] == "disabled"
    assert r.json()["error"]["data"]["required_action"] == "contact_support"


# ── valid token, wrong scope: 403 insufficient_scope, grant_scope ───────────

def test_insufficient_scope(denv):
    c = denv["c"]
    tok, _ = K.create_key(A, "t", scopes=["catalog:read"])
    r = _call(c, "list_machines", token=tok)
    assert r.status_code == 403
    h = r.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in h and 'resource_metadata="' in h
    assert 'scope="catalog:read machines:read"' in h      # held + missing
    e = r.json()["error"]
    assert e["code"] == -32001 and "machines:read" in e["message"]
    d = e["data"]
    assert d["error"] == "insufficient_scope" and d["required_action"] == "grant_scope"
    assert d["required_scope"] == "machines:read" and d["granted_scopes"] == ["catalog:read"]
    assert "Access for agents" in d["user_message"]          # a key: make a new key
    # the same for a write tool
    r = _call(c, "start_design", {"requirements": {}}, token=tok)
    assert r.status_code == 403
    assert 'scope="catalog:read designs:write"' in r.headers["www-authenticate"]


# ── fair use per IP, audit ───────────────────────────────────────────────────

def test_anonymous_rate_limit_per_ip(denv, monkeypatch):
    monkeypatch.setenv("MCP_ANON_RATE_PER_MIN", "3")
    c = denv["c"]
    for _ in range(3):
        assert _rpc(c, "tools/list", ip="192.0.2.50").status_code == 200
    r = _call(c, "describe_service", ip="192.0.2.50")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["data"]["error"] == "rate_limited"
    assert _rpc(c, "tools/list", ip="192.0.2.51").status_code == 200   # own bucket
    tok, _ = K.create_key(A, "t")                                        # keys unaffected
    assert _rpc(c, "tools/list", ip="192.0.2.50", token=tok).status_code == 200


def test_audit_lines_and_no_tokens_logged(denv):
    c = denv["c"]
    tok, rec = K.create_key(A, "t")
    _call(c, "describe_service", ip="192.0.2.60")
    _call(c, "list_machines", ip="192.0.2.60")
    _rpc(c, "tools/list", token=tok[:-3] + "zzz")
    _call(c, "list_catalog", {"kind": "wires"}, token=tok)
    rows = K.read_audit(None)
    anon = [r for r in rows if r.get("email") is None and r.get("ip") == "192.0.2.60"]
    assert {(r["tool"], r["status"]) for r in anon} >= {("describe_service", 200),
                                                         ("list_machines", 401)}
    raw = K.audit_path().read_text(encoding="utf-8")
    assert tok not in raw and tok.split("_", 2)[2] not in raw and "zzz" not in raw


# ── no user data anonymously ────────────────────────────────────────────────

def test_anonymous_responses_carry_no_user_identifiers(denv):
    c = denv["c"]
    ta, rec_a = K.create_key(A, "Alice's Claude", scopes=list(K.SCOPES))
    tb, rec_b = K.create_key(B, "Bob's ChatGPT")
    # the users have real data behind them
    assert _payload(_call(c, "list_machines", token=ta))["count"] >= 0
    texts = [
        _rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "p", "version": "0"}}).text,
        _rpc(c, "tools/list").text, _rpc(c, "resources/list").text,
        _rpc(c, "resources/read", {"uri": "emotres://guide"}).text,
        _call(c, "list_machines").text, _call(c, "start_design", {"requirements": {}}).text,
    ]
    for tool in sorted(mcp_app.PUBLIC_TOOLS):
        args = {"calculation_type": "design_from_requirements"} if tool == "get_input_requirements" else {}
        texts.append(_call(c, tool, args).text)
    for ct in _payload(_call(c, "list_calculation_types"))["calculation_types"]:
        texts.append(_call(c, "get_input_requirements", {"calculation_type": ct["id"]}).text)
    blob = "\n".join(texts).lower()
    forbidden = [A, B, "alice", "bob", "@example.com", rec_a["id"], rec_b["id"],
                 "claude desktop", *[d.lower() for d in denv["dies"]]]
    for f in forbidden:
        assert f.lower() not in blob, f
