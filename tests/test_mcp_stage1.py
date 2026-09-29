"""MCP Stage 1 (docs/MCP_2026-09-28.md): schemas, auth, scopes, isolation,
quota, audit, and the geometry denylist.

Runs the real SDK over the real McpGate, mounted on a tiny Starlette app (not
the whole API: no solver import, no lifespan side effects).  Reads the repo's
own config/dies catalog; writes only to tmp_path.
"""
from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from starlette.applications import Starlette
from starlette.testclient import TestClient

from motor_ai_sim import agent_keys as K
from motor_ai_sim import auth
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_tools as T
from motor_ai_sim import users as U

ADMIN = "boss@example.com"
A = "alice@example.com"
B = "bob@example.com"
ACCEPT = "application/json, text/event-stream"


_REPO_DIES = Path(__file__).resolve().parents[1] / "config" / "dies"
#: two real dies from the repo catalog, copied into tmp (the test sandbox has none)
_PICK = ("CILN28", "CIANO14 40_12")


def _die_names():
    from motor_ai_sim.routes import family as fam
    return sorted(str(e["name"]) for e in fam._iter_die_entries())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    from motor_ai_sim.routes import family as fam
    dd = tmp_path / "dies"
    for n in _PICK:
        shutil.copytree(_REPO_DIES / n, dd / n)
    monkeypatch.setattr(fam, "_DIES_DIR", dd, raising=False)
    for e in (A, B):
        U.create_user(e, "pw-" + e, role="user")
    dies = _die_names()
    assert len(dies) >= 2, "the repo catalog must hold two dies"
    U.set_motor_grants(A, all_motors=False, dies=[dies[0]])
    U.set_motor_grants(B, all_motors=False, dies=[dies[1]])
    K.reset_quotas()
    K._touched.clear()

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield

    app = Starlette(routes=[], lifespan=_ls)
    mcp_app.install(app)
    with TestClient(app) as c:
        yield {"c": c, "dies": dies, "tmp": tmp_path}


def _rpc(c, token, method, params=None, mid=1):
    h = {"Accept": ACCEPT, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": mid,
                                           "method": method, "params": params or {}})


def _call(c, token, tool, args=None):
    r = _rpc(c, token, "tools/call", {"name": tool, "arguments": args or {}})
    return r


def _payload(r):
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    if res.get("isError"):
        return {"__error__": res["content"][0]["text"]}
    if res.get("structuredContent") is not None:
        sc = res["structuredContent"]
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res["content"][0]["text"])


# ── schemas ──────────────────────────────────────────────────────────────────

def test_tool_list_and_schemas(env):
    # every scope -> every tool (tools/list follows the key's scopes since the
    # anonymous tier, docs/MCP_DISCOVERY.md; the scoped subsets are covered in
    # tests/test_mcp_discovery.py)
    tok, _ = K.create_key(A, "t", scopes=list(K.SCOPES))
    r = _rpc(env["c"], tok, "tools/list")
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == set(mcp_app.TOOL_SCOPES) | mcp_app.PUBLIC_TOOLS
    for n, t in tools.items():
        assert t["description"] and len(t["description"]) > 30
        assert t["inputSchema"]["type"] == "object"
        # Stage 3: only the draft/queue tools change anything
        assert (t.get("annotations") or {}).get("readOnlyHint") is (
            n not in mcp_app.WRITE_TOOLS)
    fit = tools["check_fit"]["inputSchema"]
    assert set(fit["required"]) == {"torque_nm", "speed_rpm"}
    for f in ("torque_nm", "speed_rpm", "voltage_v", "max_diameter_mm",
              "max_length_mm", "max_mass_kg", "cooling"):
        assert f in fit["properties"] and fit["properties"][f].get("description")
    assert tools["get_machine_performance"]["inputSchema"]["required"] == [
        "die", "config", "duty"]


def test_guide_resource(env):
    tok, _ = K.create_key(A, "t")
    r = _rpc(env["c"], tok, "resources/read", {"uri": "emotres://guide"})
    assert "list_machines" in r.json()["result"]["contents"][0]["text"]


# ── auth ─────────────────────────────────────────────────────────────────────

def test_no_key_wrong_key_revoked_key(env):
    c = env["c"]
    # no key: the anonymous tier lists the public tools, a data tool is 401
    assert _rpc(c, None, "tools/list").status_code == 200
    r = _call(c, None, "list_machines")
    assert r.status_code == 401 and "www-authenticate" in r.headers
    assert _rpc(c, "emk_nope_nope", "tools/list").status_code == 401
    assert _rpc(c, "some-session-token", "tools/list").status_code == 401
    tok, rec = K.create_key(A, "t")
    assert _rpc(c, tok, "tools/list").status_code == 200
    # a key with the right id and a wrong secret is not the key
    assert _rpc(c, tok[:-3] + "xyz", "tools/list").status_code == 401
    assert K.revoke_key(A, rec["id"])
    r = _rpc(c, tok, "tools/list")
    assert r.status_code == 401 and r.json()["reason"] == "revoked"


def test_other_user_cannot_revoke_my_key(env):
    _, rec = K.create_key(A, "t")
    assert not K.revoke_key(B, rec["id"])
    assert K.list_keys(A)[0]["active"] is True


def test_key_is_stored_hashed_only(env):
    tok, rec = K.create_key(A, "t")
    raw = (env["tmp"] / "agent_keys.json").read_text(encoding="utf-8")
    assert tok not in raw and tok.split("_", 2)[2] not in raw
    assert "hash" not in rec


def test_disabled_account_key_stops_working(env):
    tok, _ = K.create_key(A, "t")
    U.update_user(A, disabled=True)
    r = _rpc(env["c"], tok, "tools/list")
    assert r.status_code == 401 and r.json()["reason"] == "disabled"


def test_wrong_scope_is_403(env):
    tok, _ = K.create_key(A, "t", scopes=["catalog:read"])
    assert _call(env["c"], tok, "list_catalog", {"kind": "magnets"}).status_code == 200
    r = _call(env["c"], tok, "list_machines")
    assert r.status_code == 403 and "machines:read" in r.json()["error"]["message"]
    tok2, _ = K.create_key(A, "t2", scopes=["machines:read"])
    assert _call(env["c"], tok2, "list_catalog", {"kind": "magnets"}).status_code == 403


def test_unknown_scope_refused():
    with pytest.raises(ValueError):
        K.create_key(A, "t", scopes=["simulate:run"])


# ── permission isolation ────────────────────────────────────────────────────

def test_two_users_see_only_their_granted_machines(env):
    d0, d1 = env["dies"][0], env["dies"][1]
    ta, _ = K.create_key(A, "a")
    tb, _ = K.create_key(B, "b")
    ma = {m["die"] for m in _payload(_call(env["c"], ta, "list_machines"))["machines"]}
    mb = {m["die"] for m in _payload(_call(env["c"], tb, "list_machines"))["machines"]}
    assert ma <= {d0} and mb <= {d1}
    da = {r["id"] for r in _payload(_call(env["c"], ta, "list_catalog", {"kind": "dies"}))["entries"]}
    assert da == {d0}
    # B's machine asked by A's key: the same "not found" as a machine that does not exist
    fam_b = _payload(_call(env["c"], tb, "list_machines"))["machines"]
    tgt = next((m for m in fam_b if m["duties"]), None)
    if tgt is not None:
        args = {"die": tgt["die"], "config": tgt["config"], "duty": tgt["duties"][0]["duty"]}
        ok = _payload(_call(env["c"], tb, "get_machine_performance", args))
        assert ok.get("die") == tgt["die"]
        denied = _payload(_call(env["c"], ta, "get_machine_performance", args))
        assert "not found" in denied["__error__"]
        fits = _payload(_call(env["c"], ta, "check_fit", {"torque_nm": 0.001, "speed_rpm": 1}))
        assert all(r["die"] == d0 for r in fits["fits"] + fits["rejected"])


def test_admin_key_sees_everything(env):
    U.create_user(ADMIN, "pw-long-enough", role="user")
    tok, _ = K.create_key(ADMIN, "boss")
    dies = {r["id"] for r in _payload(_call(env["c"], tok, "list_catalog", {"kind": "dies"}))["entries"]}
    assert dies == set(env["dies"])


# ── quota ────────────────────────────────────────────────────────────────────

def test_quota_per_minute_and_day(env, monkeypatch):
    monkeypatch.setenv("MCP_RATE_PER_MIN", "2")
    tok, rec = K.create_key(A, "t")
    for _ in range(2):
        assert _call(env["c"], tok, "list_catalog", {"kind": "wires"}).status_code == 200
    r = _call(env["c"], tok, "list_catalog", {"kind": "wires"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    # non-tool traffic (tools/list) is never counted
    assert _rpc(env["c"], tok, "tools/list").status_code == 200
    # a different key has its own bucket
    tok2, _ = K.create_key(A, "t2")
    assert _call(env["c"], tok2, "list_catalog", {"kind": "wires"}).status_code == 200
    # day limit, with the clock moved past the minute window
    monkeypatch.setenv("MCP_RATE_PER_MIN", "100")
    monkeypatch.setenv("MCP_RATE_PER_DAY", "3")
    K.reset_quotas()
    t0 = 1_800_000_000.0
    assert [K.take_quota("x", t0 + i * 61)[0] for i in range(4)] == [True, True, True, False]
    assert K.take_quota("x", t0 + 86400)[0] is True


# ── audit ────────────────────────────────────────────────────────────────────

def test_audit_log_written(env):
    tok, rec = K.create_key(A, "t")
    _call(env["c"], tok, "list_catalog", {"kind": "magnets", "query": "N52"})
    _call(env["c"], None, "list_machines")          # anonymous -> 401, audited
    rows = K.read_audit(A)
    assert rows and rows[0]["tool"] == "list_catalog" and rows[0]["key"] == rec["id"]
    assert "N52" in rows[0]["args"] and rows[0]["status"] == 200
    assert any(r["status"] == 401 for r in K.read_audit(None))
    assert K.list_keys(A)[0]["last_used_at"] is not None


# ── no geometry leaks ────────────────────────────────────────────────────────

def test_no_geometry_fields_leak(env):
    U.create_user(ADMIN, "pw-long-enough", role="user")
    tok, _ = K.create_key(ADMIN, "boss")
    c = env["c"]
    outs = [_payload(_call(c, tok, "list_machines"))]
    for kind in T.CATALOG_KINDS:
        outs.append(_payload(_call(c, tok, "list_catalog", {"kind": kind})))
    for m in outs[0]["machines"]:
        for d in m["duties"][:1]:
            outs.append(_payload(_call(c, tok, "get_machine_performance",
                                       {"die": m["die"], "config": m["config"],
                                        "duty": d["duty"]})))
    outs.append(_payload(_call(c, tok, "check_fit", {"torque_nm": 1, "speed_rpm": 100})))
    for o in outs:
        assert "__error__" not in o, o
        T.assert_no_geometry(o)
    # and the raw text never carries an SVG drawing or a B-H curve
    for o in outs:
        blob = json.dumps(o)
        i = blob.find("<svg")
        assert i < 0, blob[max(0, i - 200):i + 40]
        assert "\"bh_curve\":" not in blob


def test_denylist_guard_trips():
    with pytest.raises(AssertionError):
        T.assert_no_geometry({"machines": [{"geometry": {"air_gap": 1}}]})
    with pytest.raises(AssertionError):
        T.assert_no_geometry({"a": [{"b": {"slot_height": 3}}]})


# ── the web's "Access for agents" routes ────────────────────────────────────

def test_agent_keys_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as FTC
    from motor_ai_sim.routes import agent_keys as R
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    who = {"id": "anonymous"}
    monkeypatch.setattr(R, "caller_identity", lambda a=None: dict(who))
    app = FastAPI()
    app.include_router(R.router)
    c = FTC(app)
    assert c.get("/api/agent_keys").status_code == 401
    who["id"] = A
    r = c.post("/api/agent_keys", json={"name": "claude", "scopes": ["catalog:read"]})
    assert r.status_code == 200 and r.json()["token"].startswith("emk_")
    kid = r.json()["key"]["id"]
    rows = c.get("/api/agent_keys").json()["keys"]
    assert [k["id"] for k in rows] == [kid] and "token" not in rows[0] and "hash" not in rows[0]
    assert c.post("/api/agent_keys", json={"scopes": ["simulate:run"]}).status_code == 400
    who["id"] = B
    assert c.delete(f"/api/agent_keys/{kid}").status_code == 404
    who["id"] = A
    assert c.delete(f"/api/agent_keys/{kid}").status_code == 200
    assert c.get("/api/agent_keys").json()["keys"][0]["active"] is False
