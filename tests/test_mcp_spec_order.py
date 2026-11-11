"""The MCP order flow (owner 2026-11-02, ``development.spec_order``): an AI agent turns a customer's requirements into a
development project with a filled, submitted specification.

Pins: the five tools and their schemas / scopes, the form generated from schema.json, a happy path to a submitted
specification version, what needs_input says, unit handling, the optimistic revision, only the caller's own projects,
only the project owner edits, the commercial field never offered, and no money / delivery-date wording in the tool
descriptions, the form or the guide.  No solver, no network: the MCP gate runs in-process and every store lives in tmp_path.
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("mcp")

from starlette.applications import Starlette
from starlette.testclient import TestClient

from motor_ai_sim import agent_keys as K
from motor_ai_sim import auth, mcp_app, mcp_discovery as D, mcp_tools as T
from motor_ai_sim import platform_agreement as PA
from motor_ai_sim import users as U
from motor_ai_sim.development import models, spec_order as SO, store

A = "alice@drone.example"
B = "bob@other.example"
ACCEPT = "application/json, text/event-stream"
ORDER = ["projects:read", "projects:write"]
ORDER_TOOLS = {"get_spec_form", "create_order", "update_spec", "submit_spec", "get_order_status"}

#: money and delivery-date words that must never appear in what an agent is told (the explicit NO PRICES rule aside).
#: "costs torque and efficiency" is the owner's ripple trade-off warning, not money.
MONEY = re.compile(r"\b(price[sd]?|pricing|quot(?:e|es|ed|ation|ations)|budgets?|lead[- ]?times?|costs?(?! torque)|"
                   r"costing|invoices?|payments?|currency|discounts?|eur|usd)\b|[€$]", re.IGNORECASE)

FULL = {
    "1_1": "Drone propulsion",
    "1_2": "Motor, one direction",
    "key_points": [{"name": "Hover", "torque_Nm": 4.2, "speed_rpm": 5200, "duration_s": "continuous"},
                   {"name": "Climb", "torque_Nm": "7.5 N·m", "speed_rpm": "6100 rpm", "duration_s": 30}],
    "1_6": 8000,
    "2_1": {"supply_type": "battery", "chemistry": "Li-ion", "series_cells": 12, "voltage_nominal_V": "44.4 V",
            "voltage_min_V": 39.6},
    "2_4": "own design",
    "3_1": {"cooling_medium": "air", "airflow": {"mode": "propeller wash"}},
    "3_2": {"temperature_max_C": 45},
    "3_3": "Open (air flows through the motor)",
    "4_1": {"outer_diameter_mm": 90},
    "4_2": "not_known_yet",
    "4_6": {"axial_thrust_N": 60},
    "6_2": {"prototype_date": "2027-03"},
    "mission_profile": "not_known_yet",
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_KEYS_DIR", str(tmp_path / "keys"))
    monkeypatch.setenv("TRUSTED_PROXY_HOSTS", "")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://aerostator.test")
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.delenv("PLATFORM_AGREEMENT_DISABLED", raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {"boss@example.com"})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    PA._CACHE.update(sig=None, rows=[])
    for e in (A, B):
        U.create_user(e, "pw-" + e, role="user")
    K.reset_quotas()
    K._touched.clear()

    @asynccontextmanager
    async def _ls(_app):
        async with mcp_app.lifespan():
            yield

    app = Starlette(routes=[], lifespan=_ls)
    mcp_app.install(app)
    with TestClient(app) as c:
        yield {"c": c, "tmp": tmp_path}


def _rpc(c, token, method, params=None):
    h = {"Accept": ACCEPT, "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


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


def _key(who=A, scopes=ORDER):
    return K.create_key(who, "Customer's Claude", scopes=list(scopes))[0]


def _order(env, tok, **kw):
    out = _payload(_call(env["c"], tok, "create_order", {"title": "7-inch prop motor", **kw}))
    assert out.get("status") == "created", out
    return out


def _audit(project_id):
    path = store._file(project_id, "audit.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── tools, schemas, scopes ───────────────────────────────────────────────────────────────────────────────────────────

def test_order_tools_listed_with_schemas_and_scopes(env):
    tok = _key()
    tools = {t["name"]: t for t in _rpc(env["c"], tok, "tools/list").json()["result"]["tools"]}
    assert ORDER_TOOLS <= set(tools)
    for name in ("create_order", "update_spec", "submit_spec"):
        assert mcp_app.TOOL_SCOPES[name] == "projects:write" and tools[name]["annotations"]["readOnlyHint"] is False
    for name in ("get_spec_form", "get_order_status"):
        assert mcp_app.TOOL_SCOPES[name] == "projects:read" and tools[name]["annotations"]["readOnlyHint"] is True
    assert tools["create_order"]["inputSchema"]["required"] == ["title"]
    assert set(tools["update_spec"]["inputSchema"]["required"]) == {"project_id", "revision", "answers"}
    assert set(tools["submit_spec"]["inputSchema"]["required"]) == {"project_id", "revision"}
    assert tools["update_spec"]["_meta"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["projects:write"]}]
    assert {"projects:read", "projects:write"} <= set(K.SCOPES)
    assert set(K.DEFAULT_SCOPES) == {"catalog:read", "machines:read"}         # new keys stay read-only


def test_order_tools_need_their_scopes(env):
    ro = K.create_key(A, "ro")[0]                               # default read-only key
    tools = {t["name"] for t in _rpc(env["c"], ro, "tools/list").json()["result"]["tools"]}
    assert not (ORDER_TOOLS & tools)
    r = _call(env["c"], ro, "create_order", {"title": "x"})
    assert r.status_code == 403 and r.json()["error"]["data"]["required_scope"] == "projects:write"
    read_only = _key(scopes=["projects:read"])
    assert "sections" in _payload(_call(env["c"], read_only, "get_spec_form"))
    assert _call(env["c"], read_only, "update_spec", {"project_id": "p", "revision": 0, "answers": {"1_1": "x"}}).status_code == 403
    # anonymous: refused with the sign-in error, never run
    r = env["c"].post("/mcp", headers={"Accept": ACCEPT, "Content-Type": "application/json"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "create_order", "arguments": {"title": "x"}}})
    assert r.status_code == 401 and store.list_projects() == []


def test_discovery_and_guide_describe_the_flow(env):
    caps = D.capabilities()
    rows = {t["name"]: t for t in caps["tools"]}
    assert ORDER_TOOLS <= set(rows) and all(rows[n]["purpose"] and rows[n]["requires_auth"] for n in ORDER_TOOLS)
    assert rows["submit_spec"]["read_only"] is False and rows["get_order_status"]["read_only"] is True
    scopes = {s["scope"]: s for s in caps["scopes"]}
    assert set(scopes["projects:write"]["unlocks"]) == {"create_order", "update_spec", "submit_spec"}
    types = {t["id"]: t for t in D.calculation_types()["calculation_types"]}
    assert set(types["motor_order"]["tools"]) == ORDER_TOOLS
    req = D.input_requirements("motor_order")
    assert {i["name"] for i in req["inputs"]} == set(SO.spec_form()["required_fields"])
    assert "get_spec_form" in D.describe_service()["order_flow"]
    assert "create_order" in D.llms_txt([])
    for text in (T.GUIDE, mcp_app.INSTRUCTIONS):
        flow = [text.index(t) for t in ("get_spec_form", "create_order", "update_spec", "submit_spec", "get_order_status")]
        assert flow == sorted(flow)
        assert T.NO_PRICE_RULE in text                                    # the guide says it, once, explicitly


# ── the form ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_spec_form_is_generated_from_the_schema(env):
    tok = _key()
    form = _payload(_call(env["c"], tok, "get_spec_form"))
    schema = models.schema(2)
    listed = {f["id"]: f for s in form["sections"] for f in s["fields"]}
    for section in schema["sections"]:
        for field in section["fields"]:
            fid = field["id"]
            if section["id"] == "production" or fid in ("6_3", "load_characteristic", "1_4", "1_5", "1_3"):
                assert fid not in listed, fid
                continue
            assert fid in listed, fid
            assert listed[fid]["label"] == field["label"]["en"]
            assert listed[fid]["required"] is bool(field.get("required"))
            assert listed[fid]["accepts_not_known_yet"] is bool(field.get("allow_not_known_yet"))
    assert form["required_fields"] == [f["id"] for s in schema["sections"] for f in s["fields"]
                                       if f.get("required") and s["id"] not in ("production",)
                                       and f["id"] != "mission_profile"] + ["mission_profile"]
    want =[o["value"] for s in schema["sections"] for f in s["fields"] if f["id"] == "load_type" for o in f["options"]]
    assert [c["value"] for c in listed["load_type"]["choices"]] == want and "air_propeller" in want
    cooling = next(p for p in listed["3_1"]["properties"] if p["id"] == "cooling_medium")
    assert [c["value"] for c in cooling["choices"]] == ["air", "liquid", "submerged"]
    assert "depth_m" not in {p["id"] for p in listed["3_1"]["properties"]}
    row = {p["id"]: p for p in listed["key_points"]["item"]["properties"]}
    assert row["torque_Nm"]["unit"] == "N·m" and row["duration_s"]["or_text"] == "continuous"
    assert [c["id"] for c in listed["mission_profile"]["row"]] == list(SO.MISSION_COLUMNS)
    assert listed["1_6"]["plain_value_goes_to"] == "maximum_rpm"
    assert "condition" in listed["3_2"]                                  # air cooling only
    assert form["guidance"] and form["answer_format"]["example"]
    zh = _payload(_call(env["c"], tok, "get_spec_form", {"lang": "zh-CN"}))
    zh_listed = {f["id"]: f for s in zh["sections"] for f in s["fields"]}
    assert zh_listed["key_points"]["label"] == "关键工作点" and zh["language"] == "zh-CN"
    assert "__error__" in _payload(_call(env["c"], tok, "get_spec_form", {"lang": "fr"}))


# ── happy path ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_happy_path_to_a_submitted_specification(env):
    tok = _key()
    first = {k: FULL[k] for k in ("1_2", "key_points", "1_6")}
    out = _order(env, tok, customer_org="Drone Co", application="Drone propulsion", answers=first)
    pid = out["project_id"]
    assert out["link"] == f"https://aerostator.test/projects/{pid}/specification"
    assert set(out["accepted"]) == {"1_1", "1_2", "key_points", "1_6"} and out["rejected"] == []
    assert out["revision"] == 1 and out["ready_to_submit"] is False
    missing = {n["field_id"] for n in out["needs_input"]}
    assert missing == {"2_1", "2_4", "3_1", "3_2", "3_3", "4_1", "4_2", "4_6", "6_2", "mission_profile"}
    project = store.get_project(pid)
    assert project["members"] == [{"email": A, "role": "owner"}] and project["customer_org"] == "Drone Co"
    # the S1 / peak points follow the key points (derived, never written by the agent)
    stored = store.load_json(pid, "draft.json", {})["draft"]["fields"]
    assert stored["1_4"]["value"]["torque_Nm"] == 4.2 and stored["1_5"]["value"]["torque_Nm"] == 7.5
    assert stored["key_points"]["value"][1]["speed_rpm"] == 6100                # "6100 rpm" read as 6100

    rest = {k: v for k, v in FULL.items() if k not in first and k != "1_1"}
    upd = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": out["revision"], "answers": rest}))
    assert upd["status"] == "saved" and upd["rejected"] == [], upd
    assert upd["needs_input"] == [] and upd["ready_to_submit"] is True and upd["completeness"]["complete"] is True
    assert any(r["field_id"] == "2_1" for r in upd["recalculations"])        # the owner rule: what it recalculates
    stored = store.load_json(pid, "draft.json", {})["draft"]["fields"]
    assert stored["2_1"]["value"]["voltage_nominal_V"] == 44.4
    assert stored["2_4"]["value"] == {"manufacturer_model": "own design"}

    sub = _payload(_call(env["c"], tok, "submit_spec", {"project_id": pid, "revision": upd["revision"]}))
    assert sub["status"] == "submitted" and sub["spec_version"] == 1, sub
    assert sub["next_steps"] and "MOTRES" in sub["user_message"]
    assert store._file(pid, "spec/v1.json").is_file()
    actions = [(r["action"], r["details"].get("via")) for r in _audit(pid)]
    assert ("create", "mcp") in actions and ("autosave", "mcp") in actions and ("submit", "mcp") in actions

    status = _payload(_call(env["c"], tok, "get_order_status"))
    assert status["count"] == 1
    row = status["projects"][0]
    assert row["project_id"] == pid and row["stage"] == "specification" and row["spec"]["submitted_version"] == 1
    assert row["spec"]["changed_since_submission"] is False and row["unread_messages"] == 0
    assert row["linked_motors"] == [] and row["completeness"]["complete"] is True
    one = _payload(_call(env["c"], tok, "get_order_status", {"project_id": pid}))["project"]
    assert one["answers"]["1_1"]["value"] == "Drone propulsion" and one["needs_input"] == []
    assert one["answers"]["mission_profile"]["status"] == "not_known_yet"


def test_submit_refuses_an_incomplete_specification(env):
    tok = _key()
    out = _order(env, tok, answers={"1_1": "Robot joint"})
    sub = _payload(_call(env["c"], tok, "submit_spec", {"project_id": out["project_id"], "revision": out["revision"]}))
    assert sub["status"] == "incomplete" and sub["submitted"] is False
    assert {n["field_id"] for n in sub["needs_input"]} >= {"key_points", "2_1", "mission_profile"}
    assert not store._file(out["project_id"], "spec/v1.json").exists()


# ── needs_input, units, refused answers ──────────────────────────────────────────────────────────────────────────────

def test_needs_input_rows_say_what_and_why(env):
    tok = _key()
    out = _order(env, tok)
    rows = {n["field"]: n for n in out["needs_input"]}
    assert set(rows) == {"1_1", "1_2", "key_points", "1_6", "2_1", "2_4", "3_1", "3_2", "3_3", "4_1", "4_2", "4_6",
                         "6_2", "mission_profile"}
    sim = {"key_points", "1_6", "2_1", "3_1", "3_3"}               # the simulation minimum (owner 2026-11-11): never "not known yet"
    assert all(r["label"] and r["reason"] and r["accepts_not_known_yet"] is (k not in sim) for k, r in rows.items())
    pid = out["project_id"]
    upd = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": out["revision"], "answers": {
        "key_points": [{"name": "Cruise", "speed_rpm": 4000, "duration_s": "continuous"}],
        "3_1": {"cooling_medium": "liquid"}}}))
    rows = {n["field"]: n for n in upd["needs_input"]}
    torque = rows["key_points[0].torque_Nm"]
    assert torque["label"] == "Key operating points - Torque" and torque["unit"] == "N·m"
    inlet = rows["3_1.inlet_temperature_C"]                    # liquid cooling needs the coolant inlet temperature
    assert inlet["unit"] == "°C" and "inlet temperature" in inlet["reason"] and inlet["accepts_not_known_yet"] is False
    assert "3_2" not in rows                                    # the ambient temperature is asked for air cooling only


def test_units_and_refused_answers_are_reported_per_field(env):
    tok = _key()
    out = _order(env, tok)
    pid = out["project_id"]
    upd = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": out["revision"], "answers": {
        "1_1": "Marine propulsion",
        "4_1.outer_diameter_mm": "0.12 m",                   # wrong unit for a field in mm
        "2_3": {"continuous_A": "about 40"},                 # not a number
        "6_3": {"amount": 500},                              # the commercial field is not on the agent form
        "1_4": {"torque_Nm": 3},                             # derived from the key points
        "production.target_price": 3,
        "nonsense": 1,
        "additional_requirements": "not_known_yet",         # this field cannot be marked not known yet
    }}))
    assert upd["status"] == "saved" and upd["accepted"] == ["1_1"]
    reasons = {r["field_id"]: r["reason"] for r in upd["rejected"]}
    assert set(reasons) == {"4_1", "2_3", "6_3", "1_4", "production", "nonsense", "additional_requirements"}
    assert "mm" in reasons["4_1"] and "convert" in reasons["4_1"]
    assert "not a number" in reasons["2_3"]
    assert "key_points" in reasons["1_4"]
    fields = store.load_json(pid, "draft.json", {})["draft"]["fields"]
    assert "6_3" not in fields and "4_1" not in fields and fields["1_1"]["value"] == "Marine propulsion"
    # nothing valid at all: nothing saved, the revision stays
    bad = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": upd["revision"],
                                                        "answers": {"1_6": "fast"}}))
    assert bad["status"] == "invalid" and bad["saved"] is False and bad["revision"] == upd["revision"]


def test_stale_revision_is_a_conflict_and_saves_nothing(env):
    tok = _key()
    out = _order(env, tok, answers={"1_1": "Drone propulsion"})
    pid = out["project_id"]
    stale = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": 0, "answers": {"1_2": "Generator"}}))
    assert stale["status"] == "conflict" and stale["revision"] == out["revision"] and stale["saved"] is False
    assert "1_2" not in store.load_json(pid, "draft.json", {})["draft"]["fields"]
    sub = _payload(_call(env["c"], tok, "submit_spec", {"project_id": pid, "revision": 0}))
    assert sub["status"] == "conflict" and sub["submitted"] is False


def test_merge_keeps_known_properties_and_null_clears(env):
    tok = _key()
    out = _order(env, tok, answers={"2_1": {"chemistry": "LiPo", "series_cells": 6}})
    pid = out["project_id"]
    upd = _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": out["revision"], "answers": {
        "2_1.voltage_nominal_V": 22.2, "2_1.chemistry": None}}))
    assert upd["accepted"] == ["2_1"]
    value = store.load_json(pid, "draft.json", {})["draft"]["fields"]["2_1"]["value"]
    assert value == {"series_cells": 6, "voltage_nominal_V": 22.2}


def test_size_limits(env):
    tok = _key()
    out = _order(env, tok)
    many = {f"x{i}": 1 for i in range(SO.MAX_ANSWERS + 1)}
    r = _payload(_call(env["c"], tok, "update_spec", {"project_id": out["project_id"], "revision": 0, "answers": many}))
    assert "__error__" in r and "at most" in r["__error__"]
    big = _payload(_call(env["c"], tok, "update_spec", {"project_id": out["project_id"], "revision": 0,
                                                        "answers": {"1_1": "x" * (SO.MAX_ANSWERS_BYTES + 10)}}))
    assert "__error__" in big and "too large" in big["__error__"]


# ── access: own projects only, owner edits ───────────────────────────────────────────────────────────────────────────

def test_a_foreign_project_is_refused(env):
    alice, bob = _key(A), _key(B)
    pid = _order(env, alice, answers={"1_1": "Drone propulsion"})["project_id"]
    for tool, args in (("update_spec", {"project_id": pid, "revision": 1, "answers": {"1_2": "Generator"}}),
                       ("submit_spec", {"project_id": pid, "revision": 1}),
                       ("get_order_status", {"project_id": pid})):
        r = _payload(_call(env["c"], bob, tool, args))
        assert "__error__" in r and "not found" in r["__error__"], (tool, r)
    assert _payload(_call(env["c"], bob, "get_order_status"))["projects"] == []
    assert "1_2" not in store.load_json(pid, "draft.json", {})["draft"]["fields"]
    # staff rights do not widen what an agent sees: an admin's agent lists only the admin's own projects
    U.create_user("boss@example.com", "pw-boss-long", role="admin")
    boss = _key("boss@example.com")
    assert _payload(_call(env["c"], boss, "get_order_status"))["projects"] == []


def test_a_member_who_is_not_the_owner_cannot_edit_the_spec(env):
    alice, bob = _key(A), _key(B)
    pid = _order(env, alice, answers={"1_1": "Drone propulsion"})["project_id"]

    def add_bob(doc):
        old = list(doc["members"])
        doc["members"] = old + [{"email": B, "role": "customer"}]
        store.sync_membership_index(pid, old, doc["members"])
    store.mutate_project(pid, add_bob)
    seen = _payload(_call(env["c"], bob, "get_order_status", {"project_id": pid}))["project"]
    assert seen["can_edit_spec"] is False and seen["role"] == "customer"
    for tool, args in (("update_spec", {"project_id": pid, "revision": 1, "answers": {"1_2": "Generator"}}),
                       ("submit_spec", {"project_id": pid, "revision": 1})):
        r = _payload(_call(env["c"], bob, tool, args))
        assert "__error__" in r and "owner" in r["__error__"], (tool, r)
    assert "1_2" not in store.load_json(pid, "draft.json", {})["draft"]["fields"]


def test_the_agreement_gate_is_relayed_not_accepted(env):
    legal = env["tmp"] / "legal" / "platform_agreement"
    (legal / "v3-final").mkdir(parents=True)
    (legal / "v3-final" / "platform_agreement_en.md").write_text("# Agreement\n\nSigned by {{signatory}}.\n", encoding="utf-8")
    (legal / "v3-final" / "platform_agreement_zh-CN.md").write_text("# 协议\n\n签署人 {{signatory}}。\n", encoding="utf-8")
    (legal / "current.txt").write_text("v3-final\n", encoding="utf-8")
    PA._CACHE.update(sig=None, rows=[])
    assert PA.enforced() is True
    out = _payload(_call(env["c"], _key(), "create_order", {"title": "x"}))
    assert out["status"] == "agreement_required" and "accept it yourself" in out["user_message"]
    assert store.list_projects() == []


def test_the_project_limit_is_relayed(env):
    tok = _key()
    from motor_ai_sim.development import self_service
    for n in range(self_service.MAX_OPEN_OWNED_PROJECTS):
        _order(env, tok, )
    out = _payload(_call(env["c"], tok, "create_order", {"title": "one more"}))
    assert out["status"] == "project_limit" and out["user_message"]


# ── no money, no delivery dates ──────────────────────────────────────────────────────────────────────────────────────

def test_no_money_or_delivery_wording_anywhere_an_agent_reads(env):
    tok = _key(scopes=K.SCOPES)
    tools = _rpc(env["c"], tok, "tools/list").json()["result"]["tools"]
    for t in tools:
        assert not MONEY.search(t.get("description") or ""), (t["name"], MONEY.search(t["description"]).group(0))
        schema_text = json.dumps(t.get("inputSchema") or {}, ensure_ascii=False).replace("$ref", "").replace("$defs", "")
        assert not MONEY.search(schema_text), t["name"]
    for name in ORDER_TOOLS:
        assert not MONEY.search(D.TOOL_SUMMARIES[name]), name
    form = SO.spec_form()
    hits = [s for s in SO.all_text(form) if MONEY.search(s)]
    assert hits == [], hits
    assert not [s for s in SO.all_text(SO.spec_form("zh-CN")) if re.search("价格|预算|报价|成本", s)]
    rule = T.NO_PRICE_RULE
    assert not MONEY.search(T.GUIDE.replace(rule, "")), MONEY.search(T.GUIDE.replace(rule, "")).group(0)
    assert not MONEY.search(mcp_app.INSTRUCTIONS.replace(rule, ""))
    for text in (json.dumps(D.input_requirements("motor_order")), json.dumps(SO.NEXT_STEPS)):
        assert not MONEY.search(text)
    # the internal commercial status reads as MOTRES's review step
    out = _order(env, _key())
    store.mutate_project(out["project_id"], lambda d: d.update(status="quote"))
    row = _payload(_call(env["c"], _key(), "get_order_status"))["projects"][0]
    assert row["stage"] == "motres_review" and not MONEY.search(json.dumps(row))


def test_a_stored_commercial_answer_is_never_echoed(env):
    tok = _key()
    out = _order(env, tok, answers={"1_1": "Drone propulsion"})
    pid = out["project_id"]

    def put_budget(doc):
        doc["draft"]["fields"]["6_3"] = {"status": "filled", "value": {"amount": 900, "currency": "EUR"}}
        doc["draft"]["production"]["target_price"] = {"status": "filled", "value": 100}
    store.mutate_json(pid, "draft.json", put_budget, default={})
    one = _payload(_call(env["c"], tok, "get_order_status", {"project_id": pid}))["project"]
    assert "6_3" not in one["answers"] and not MONEY.search(json.dumps(one))
    # an agent update keeps what the customer typed in the web form untouched
    _payload(_call(env["c"], tok, "update_spec", {"project_id": pid, "revision": one["spec"]["draft_revision"],
                                                  "answers": {"1_2": "Generator"}}))
    stored = store.load_json(pid, "draft.json", {})["draft"]
    assert stored["fields"]["6_3"]["value"]["amount"] == 900 and stored["production"]["target_price"]["value"] == 100
