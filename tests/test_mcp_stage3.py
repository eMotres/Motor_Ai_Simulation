"""MCP Stage 3 (docs/MCP_2026-09-28.md "Stage 3"): drafts + simulations.

needs_input, explicit addressing (the live machine and the catalog stay
byte-identical), the daily simulate quota (429), agent jobs in the user's own
queue, the results whitelist, open_in_configure, scopes, two-user isolation,
and the web's /api/agent_designs routes.

No FEM is run: ``agent_designs.SIM_RUNNERS`` is replaced by a stub that checks
it was called INSIDE the draft's sandbox workspace and returns a summary shaped
like the transient's / the coupled loop's.  Everything is written to tmp_path.
"""
from __future__ import annotations

import json
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from starlette.applications import Starlette
from starlette.testclient import TestClient

from motor_ai_sim import agent_designs as AD
from motor_ai_sim import agent_keys as K
from motor_ai_sim import auth
from motor_ai_sim import config as CFG
from motor_ai_sim import jobs as J
from motor_ai_sim import mcp_app
from motor_ai_sim import mcp_tools as T
from motor_ai_sim import users as U
from motor_ai_sim import workspace as WS

A = "alice@example.com"
B = "bob@example.com"
ACCEPT = "application/json, text/event-stream"
_REPO = Path(__file__).resolve().parents[1]
_PICK = ("CILN28", "CIANO14 40_12")
ALL = ["catalog:read", "machines:read", "designs:write", "simulate"]
REQ = {"torque_nm": 0.5, "speed_rpm": 10000, "dc_bus_v": 48, "cooling": "air",
       "duty": "S1"}


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aerostator.test")
    monkeypatch.delenv("MCP_SIMULATE_PER_DAY", raising=False)
    monkeypatch.delenv("MCP_SIMULATE_UNLIMITED", raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"boss@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.delenv("WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    # the whole single-user installation lives in tmp: live config, the
    # active-context file, the dies, the job records, the drafts
    live = tmp_path / "live"
    live.mkdir()
    shutil.copy2(Path(str(CFG.DEFAULT_CONFIG_PATH)), live / "motor_config.yaml")
    (live / ".family_context.json").write_text(
        json.dumps({"die": "CILN28", "config": "G2-L40", "duty": "peak"}), encoding="utf-8")
    monkeypatch.setattr(CFG, "DEFAULT_CONFIG_PATH", live / "motor_config.yaml")
    from motor_ai_sim.routes import family as fam
    dd = live / "dies"
    for n in _PICK:
        shutil.copytree(_REPO / "config" / "dies" / n, dd / n)
    monkeypatch.setattr(fam, "_DIES_DIR", dd, raising=False)
    for e in (A, B):
        U.create_user(e, "pw-" + e, tier="free")
    U.set_motor_grants(A, all_motors=False, dies=["CIANO14 40_12"])
    U.set_motor_grants(B, all_motors=False, dies=["CILN28"])
    K.reset_quotas()
    K._touched.clear()
    J.reset_queue(J.InProcessQueue(workers=2, per_user=0))

    calls = []

    def _stub(d, ws, steps, run_id):
        # EXPLICIT ADDRESSING: the solve sees the draft's sandbox, never the
        # user's live machine
        assert WS.workspace() is ws
        assert Path(str(WS.config_file())) != live / "motor_config.yaml"
        assert Path(str(WS.config_file())).is_file()
        assert J.current_run_id() == run_id
        calls.append({"design": d["id"], "steps": steps, "cfg": str(WS.config_file())})
        return {"summary": {"T_em_avg_Nm": 0.52, "P_mech_W": 544.5,
                            "P_loss_total_W": 40.0, "P_stranded_W": 25.0,
                            "P_core_W": 12.0, "P_mag_W": 1.0, "P_solid_W": 2.0,
                            "efficiency": 0.9316, "V_line_peak_V": 30.1,
                            "I1_phase_rms_A": 21.9, "T_ripple_pct": 3.4,
                            "num_wires_per_slot": 12, "air_gap": 0.4},
                "coil_temp_c": 88.2, "magnet_temp_c": 71.0, "converged": True}
    monkeypatch.setattr(AD, "SIM_RUNNERS", {"em": _stub, "thermal": _stub,
                                            "coupled": _stub})

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield

    app = Starlette(routes=[], lifespan=_ls)
    mcp_app.install(app)
    with TestClient(app) as c:
        yield {"c": c, "tmp": tmp_path, "live": live, "calls": calls}
    J.reset_queue()


def _rpc(c, token, method, params=None):
    h = {"Accept": ACCEPT, "Content-Type": "application/json",
         "Authorization": f"Bearer {token}"}
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1,
                                           "method": method, "params": params or {}})


def _call(c, token, tool, args=None):
    return _rpc(c, token, "tools/call", {"name": tool, "arguments": args or {}})


def _payload(r):
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    if res.get("isError"):
        return {"__error__": res["content"][0]["text"]}
    sc = res.get("structuredContent")
    if sc is not None:
        return sc.get("result", sc) if set(sc) == {"result"} else sc
    return json.loads(res["content"][0]["text"])


def _wait(c, tok, job_id, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = _payload(_call(c, tok, "get_job", {"job_id": job_id}))
        if j.get("state") in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _design(env, tok, req=None):
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": req or REQ}))
    assert "design_id" in out, out
    return out


# ── schemas / scopes ─────────────────────────────────────────────────────────

def test_stage3_tools_listed_with_scopes(env):
    tok, _ = K.create_key(A, "Claude Desktop", scopes=ALL)
    tools = {t["name"]: t for t in _rpc(env["c"], tok, "tools/list").json()["result"]["tools"]}
    for n in ("start_design", "simulate", "get_job", "get_design_result",
              "open_in_configure", "get_design"):
        assert n in tools and n in mcp_app.TOOL_SCOPES
    assert tools["start_design"]["annotations"]["readOnlyHint"] is False
    assert "needs_input" in tools["start_design"]["description"]
    req = tools["start_design"]["inputSchema"]
    assert "requirements" in req["required"]
    assert set(tools["simulate"]["inputSchema"]["required"]) == {"design_id", "what"}


def test_scopes_enforced(env):
    ro, _ = K.create_key(A, "ro")                        # default = read-only
    assert K.list_keys(A)[0]["scopes"] == ["catalog:read", "machines:read"]
    r = _call(env["c"], ro, "start_design", {"requirements": REQ})
    assert r.status_code == 403 and "designs:write" in r.json()["error"]["message"]
    dw, _ = K.create_key(A, "dw", scopes=["designs:write", "machines:read"])
    did = _design(env, dw)["design_id"]
    r = _call(env["c"], dw, "simulate", {"design_id": did, "what": "em"})
    assert r.status_code == 403 and "simulate" in r.json()["error"]["message"]
    assert _call(env["c"], dw, "get_job", {"job_id": "x"}).status_code == 403


# ── needs_input ──────────────────────────────────────────────────────────────

def test_needs_input_missing_voltage_cooling_duty(env):
    tok, _ = K.create_key(A, "c", scopes=ALL)
    out = _payload(_call(env["c"], tok, "start_design",
                         {"requirements": {"torque_nm": 0.5, "speed_rpm": 10000}}))
    assert out["status"] == "needs_input"
    fields = {x["field"]: x for x in out["needs_input"]}
    assert set(fields) == {"dc_bus_v", "cooling", "duty"}
    assert fields["dc_bus_v"]["why"] and fields["dc_bus_v"]["range"]
    assert fields["cooling"]["options"] == ["air", "liquid", "robotics"]
    assert "design_id" not in out
    assert not (env["live"] / AD.DESIGNS_DIR).exists()      # nothing created


def test_needs_input_contradiction_and_loud_errors(env):
    tok, _ = K.create_key(A, "c", scopes=ALL)
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": {
        **REQ, "power_kw": 5.0}}))                        # 0.5 N*m @ 10k = 0.52 kW
    assert out["status"] == "needs_input"
    f = out["needs_input"][0]
    assert f["field"] == "power_kw" and "contradiction" in f["why"]
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": {
        **REQ, "max_speed_rpm": 5000}}))
    assert [x["field"] for x in out["needs_input"]] == ["max_speed_rpm"]
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": {**REQ, "cooling": "fan"}}))
    assert "cooling must be one of" in out["__error__"]
    # nothing asked is missing either
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": {}}))
    assert {"torque_nm", "speed_rpm"} <= {x["field"] for x in out["needs_input"]}


def test_no_fit_asks_which_limit_moves(env):
    tok, _ = K.create_key(A, "c", scopes=ALL)
    out = _payload(_call(env["c"], tok, "start_design", {"requirements": {
        **REQ, "max_outer_diameter_mm": 20}}))
    assert out["status"] == "no_fit"
    assert out["needs_input"][0]["field"] == "max_outer_diameter_mm"


# ── the draft, explicit addressing, the queue ───────────────────────────────

def test_draft_created_and_live_state_untouched(env):
    live = env["live"]
    before = _snapshot(live)
    tok, rec = K.create_key(A, "Claude Desktop", scopes=ALL)
    d = _design(env, tok)
    assert d["status"] == "draft"
    assert d["created_by"]["client_name"] == "Claude Desktop"
    assert d["starting_point"]["die"] == "CIANO14 40_12"
    assert d["why"] and d["parameters"]["active_length_mm"] > 0
    job = _payload(_call(env["c"], tok, "simulate",
                         {"design_id": d["design_id"], "what": "em", "steps": 24}))
    assert job["state"] == "queued" and job["job_id"]
    fin = _wait(env["c"], tok, job["job_id"])
    assert fin["state"] == "done", fin
    assert env["calls"] and env["calls"][0]["steps"] == 24
    after = _snapshot(live)
    # the live machine, the active context and the whole catalog: byte-identical
    for k in ("motor_config.yaml", ".family_context.json"):
        assert after[k] == before[k]
    for k, v in before.items():
        assert after.get(k) == v, k
    new = sorted(set(after) - set(before))
    assert new and all(k.startswith(AD.DESIGNS_DIR) or k == J.JOBS_FILE for k in new), new
    # the sandbox machine is the draft's (scaled stack), not the live one
    import yaml
    sb = yaml.safe_load(Path(env["calls"][0]["cfg"]).read_text(encoding="utf-8"))
    assert sb["geometry"]["motor_length"] == d["parameters"]["active_length_mm"]
    assert sb["simulation"]["rpm"] == REQ["speed_rpm"]


def test_job_in_users_queue_with_agent_tag(env):
    tok, _ = K.create_key(A, "Claude Desktop", scopes=ALL)
    did = _design(env, tok)["design_id"]
    job = _payload(_call(env["c"], tok, "simulate", {"design_id": did, "what": "coupled"}))
    _wait(env["c"], tok, job["job_id"])
    recs = J.queue().list_for_owner(A)
    mine = [r for r in recs if r.run_id == job["job_id"]]
    assert mine and mine[0].kind == "agent.coupled" and mine[0].owner == A
    assert mine[0].body["agent"]["client_name"] == "Claude Desktop"
    assert mine[0].body["design_id"] == did
    rows = json.loads((env["live"] / J.JOBS_FILE).read_text(encoding="utf-8"))["jobs"]
    assert any(r["run_id"] == job["job_id"] and r["body"]["agent"] for r in rows)
    # the web's Stop is the owner's own cancel
    assert J.queue().cancel(job["job_id"], requester=A)["found"] is True


def test_results_whitelist_and_configure_url(env):
    tok, _ = K.create_key(A, "c", scopes=ALL)
    did = _design(env, tok)["design_id"]
    j = _payload(_call(env["c"], tok, "simulate", {"design_id": did, "what": "thermal"}))
    _wait(env["c"], tok, j["job_id"])
    res = _payload(_call(env["c"], tok, "get_design_result", {"design_id": did}))
    th = res["results"]["thermal"]
    assert th["torque_nm"] == 0.52 and th["temperatures_c"]["coil"] == 88.2
    assert th["efficiency_electromagnetic_pct"] == 93.16
    assert th["losses_w"]["copper"] == 25.0
    assert res["requirement_checks"]["torque_met"] is True
    for o in (res, _payload(_call(env["c"], tok, "get_design", {"design_id": did}))):
        T.assert_no_geometry(o)
        blob = json.dumps(o)
        for bad in ("num_wires_per_slot", "air_gap", "conductors", "internal",
                    "wire_height", "<svg"):
            assert bad not in blob, bad
    url = _payload(_call(env["c"], tok, "open_in_configure", {"design_id": did}))["url"]
    assert url == f"https://aerostator.test/?tab=configure&design={did}"


def test_simulate_quota_429(env, monkeypatch):
    monkeypatch.setenv("MCP_SIMULATE_PER_DAY", "1")
    tok, _ = K.create_key(A, "c", scopes=ALL)
    did = _design(env, tok)["design_id"]
    j = _payload(_call(env["c"], tok, "simulate", {"design_id": did, "what": "em"}))
    _wait(env["c"], tok, j["job_id"])
    r = _call(env["c"], tok, "simulate", {"design_id": did, "what": "em"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert "quota" in r.json()["error"]["message"]
    # another key of the SAME account shares the account's quota
    tok2, _ = K.create_key(A, "c2", scopes=ALL)
    assert _call(env["c"], tok2, "simulate", {"design_id": did, "what": "em"}).status_code == 429
    # the owner (admin) is unlimited
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {A})
    assert AD.daily_limit(K.Principal(email=A, credential_id="x")) is None


def test_two_users_isolated(env):
    ta, _ = K.create_key(A, "a", scopes=ALL)
    tb, _ = K.create_key(B, "b", scopes=ALL)
    da = _design(env, ta)
    assert da["starting_point"]["die"] == "CIANO14 40_12"     # A's grant only
    for tool, args in (("get_design", {"design_id": da["design_id"]}),
                       ("get_design_result", {"design_id": da["design_id"]}),
                       ("open_in_configure", {"design_id": da["design_id"]}),
                       ("simulate", {"design_id": da["design_id"], "what": "em"})):
        out = _payload(_call(env["c"], tb, tool, args))
        assert "not found" in out["__error__"], (tool, out)
    j = _payload(_call(env["c"], ta, "simulate", {"design_id": da["design_id"], "what": "em"}))
    assert "not found" in _payload(_call(env["c"], tb, "get_job", {"job_id": j["job_id"]}))["__error__"]
    # B cannot address A's granted die as a base either
    out = _payload(_call(env["c"], tb, "start_design", {"requirements": REQ,
                   "base": {"die": "CIANO14 40_12", "config": "L12"}}))
    assert "not found" in out["__error__"]


def test_web_routes_list_edit_revert_delete(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as FTC
    from motor_ai_sim.routes import agent_designs as R
    from motor_ai_sim.routes import agent_keys as RK
    tok, _ = K.create_key(A, "Claude Desktop", scopes=ALL)
    did = _design(env, tok)["design_id"]
    who = {"id": A}
    monkeypatch.setattr(RK, "caller_identity", lambda a=None: dict(who))
    app = FastAPI()
    app.include_router(R.router)
    c = FTC(app)
    rows = c.get("/api/agent_designs").json()["designs"]
    assert [r["design_id"] for r in rows] == [did]
    assert rows[0]["created_by"]["client_name"] == "Claude Desktop"
    assert "internal" not in rows[0]
    L0 = rows[0]["params"]["stack_mm"]
    r = c.patch(f"/api/agent_designs/{did}", json={"stack_mm": L0 + 5})
    assert r.status_code == 200 and r.json()["params"]["stack_mm"] == L0 + 5
    assert r.json()["edited_by_user"] is True
    assert c.patch(f"/api/agent_designs/{did}", json={"air_gap": 1}).status_code == 400
    r = c.post(f"/api/agent_designs/{did}/revert")
    assert r.json()["params"]["stack_mm"] == L0 and r.json()["edited_by_user"] is False
    who["id"] = B
    assert c.get(f"/api/agent_designs/{did}").status_code == 404
    assert c.delete(f"/api/agent_designs/{did}").status_code == 404
    who["id"] = A
    assert c.delete(f"/api/agent_designs/{did}").status_code == 200
    assert c.get("/api/agent_designs").json()["designs"] == []


def test_oauth_consent_can_narrow_scopes(env):
    from motor_ai_sim import oauth as O
    cl = O.register_client({"redirect_uris": ["https://claude.ai/cb"],
                            "client_name": "Claude", "token_endpoint_auth_method": "none"})
    rid = O.start_authorization({"client_id": cl["client_id"], "redirect_uri": "https://claude.ai/cb",
                                 "response_type": "code", "code_challenge": "x" * 43,
                                 "code_challenge_method": "S256"})
    assert set(O.describe_request(rid)["scopes"]) == set(K.SCOPES)
    O.decide(rid, A, True, ["catalog:read", "designs:write"])
    code = next(iter(O._load()["codes"].values()))
    assert code["scopes"] == ["catalog:read", "designs:write"]


def test_done_job_reports_full_progress(env, monkeypatch):
    """Server run 2026-09-29: the solver's last tick was 23/24 before the
    post-processing, so a finished job read 95.8 %.  Done means 100 %."""
    from motor_ai_sim import progress as P
    inner = AD.SIM_RUNNERS["em"]

    def _ticking(d, ws, steps, run_id):
        e = P.registry().entry(run_id, create=True)
        e.tracker.start(24, "transient")
        e.tracker.update(done=23, phase="post-processing")
        return inner(d, ws, steps, run_id)
    monkeypatch.setitem(AD.SIM_RUNNERS, "em", _ticking)
    tok, _ = K.create_key(A, "Claude Desktop", scopes=ALL)
    did = _design(env, tok)["design_id"]
    job = _payload(_call(env["c"], tok, "simulate", {"design_id": did, "what": "em"}))
    j = _wait(env["c"], tok, job["job_id"])
    assert j["state"] == "done", j
    pr = j["progress"]
    assert pr["pct"] == 100.0 and pr["step"] == 24 and pr["eta_s"] == 0.0
