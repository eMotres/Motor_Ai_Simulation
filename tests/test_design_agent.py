"""Design assistant pilot (2026-10-18): the tool loop, money caps, access, the client-view filter of the tools.

No network: the Anthropic client is a scripted fake, and the tools are either faked or run on monkeypatched catalogue
views.  The store goes to tmp_path.
"""
from __future__ import annotations

import json
import types

import pytest

from motor_ai_sim.agent_keys import Principal
from motor_ai_sim.design_agent import runner as R
from motor_ai_sim.design_agent import tools as T

ME = Principal(email="owner@example.com", credential_id="design-agent", kind="web", scopes=("read",))


class _Block(types.SimpleNamespace):
    pass


def _resp(blocks, stop, usage=(1000, 200, 0, 0)):
    i, o, cw, cr = usage
    return types.SimpleNamespace(
        content=[_Block(**b) for b in blocks], stop_reason=stop,
        usage=types.SimpleNamespace(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw,
                                    cache_read_input_tokens=cr))


class _FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DESIGN_AGENT_STORE_DIR", str(tmp_path))
    monkeypatch.setenv("DESIGN_AGENT_API_KEY", "test-key")
    for k in ("DESIGN_AGENT_CONVERSATION_USD", "DESIGN_AGENT_USER_DAY_USD", "DESIGN_AGENT_DAY_USD",
              "DESIGN_AGENT_ACCESS", "DESIGN_AGENT_MODEL"):
        monkeypatch.delenv(k, raising=False)

    class _Inline:                      # run the turn in the calling thread
        def __init__(self, target, args, **_kw):
            self.t, self.a = target, args

        def start(self):
            self.t(*self.a)
    monkeypatch.setattr(R.threading, "Thread", _Inline)
    return tmp_path


def _script_two_steps():
    return [
        _resp([{"type": "text", "text": "Checking."},
               {"type": "tool_use", "id": "tu1", "name": "list_motors", "input": {"application": "propeller"}},
               {"type": "tool_use", "id": "tu2", "name": "present_motor",
                "input": {"die": "D", "config": "L15", "headline": "1.9 kg continuous"}}], "tool_use"),
        _resp([{"type": "text", "text": "Take **D / L15**."}], "end_turn", usage=(50, 100, 0, 3000)),
    ]


def test_a_turn_runs_tools_records_steps_cards_and_cost(env, monkeypatch):
    fake = _FakeClient(_script_two_steps())
    monkeypatch.setattr(R, "_client", lambda: fake)
    seen = []

    def _tool(p, name, args):
        seen.append((p.email, name))
        if name == "present_motor":
            return {"shown": True, "card": {"die": "D", "config": "L15", "duty": "rated", "propeller_id": None,
                                            "headline": args["headline"]}}
        return {"count": 1, "motors": [{"die": "D", "config": "L15"}]}
    monkeypatch.setattr(T, "run_tool", _tool)

    v = R.start_turn(ME, "I need a motor for a 4 kg quad, 6S")
    cid = v["id"]
    out = R.get_conversation(ME.email, cid)
    assert out["state"] == "idle" and out["error"] is None and out["pending"] is None
    assert seen == [(ME.email, "list_motors"), (ME.email, "present_motor")]
    last = out["display"][-1]
    assert last["role"] == "assistant" and last["text"] == "Take **D / L15**."
    assert [s["tool"] for s in last["steps"]] == ["list_motors", "present_motor"]
    assert last["cards"][0]["config"] == "L15"
    # priced from the reported usage: (1000*2 + 200*10)/1e6 + (50*2 + 100*10 + 3000*0.1)/1e6
    assert out["cost_usd"] == pytest.approx(0.004 + 0.0014, abs=1e-6)
    assert R.spent_today(ME.email) == pytest.approx(out["cost_usd"], abs=1e-6)
    # the history alternates and every tool_use is answered
    doc = R._load(ME.email, cid)
    roles = [m["role"] for m in doc["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert {r["tool_use_id"] for r in doc["messages"][2]["content"]} == {"tu1", "tu2"}
    # one cache breakpoint on the last message only, plus system and tools
    second = fake.calls[1]
    marks = [b for m in second["messages"] if isinstance(m["content"], list) for b in m["content"]
             if "cache_control" in b]
    assert len(marks) == 1 and second["messages"][-1]["content"][-1] is marks[0]
    assert "cache_control" in second["tools"][-1] and "cache_control" in second["system"][0]
    assert "cache_control" not in json.dumps(doc["messages"])           # never stored


def test_a_tool_error_is_reported_to_the_model_not_raised(env, monkeypatch):
    fake = _FakeClient([
        _resp([{"type": "tool_use", "id": "tu1", "name": "propeller_s1",
                "input": {"die": "X", "config": "Y", "propeller_id": "p"}}], "tool_use"),
        _resp([{"type": "text", "text": "That motor is not in your catalogue."}], "end_turn")])
    monkeypatch.setattr(R, "_client", lambda: fake)

    def _tool(p, name, args):
        raise T.AgentToolError("motor 'X / Y' is not in this account's catalogue")
    monkeypatch.setattr(T, "run_tool", _tool)
    cid = R.start_turn(ME, "X Y?")["id"]
    doc = R._load(ME.email, cid)
    res = doc["messages"][2]["content"][0]
    assert res["is_error"] is True and "not in this account" in res["content"]
    out = R.get_conversation(ME.email, cid)
    assert out["state"] == "idle" and out["display"][-1]["steps"][0]["ok"] is False


def test_caps_refuse_before_spending(env, monkeypatch):
    monkeypatch.setattr(R, "_client", lambda: _FakeClient([_resp([{"type": "text", "text": "ok"}], "end_turn",
                                                                  usage=(100000, 0, 0, 0))]))
    cid = R.start_turn(ME, "hello")["id"]                    # $0.20
    monkeypatch.setenv("DESIGN_AGENT_CONVERSATION_USD", "0.1")
    with pytest.raises(R.AgentError, match="cost limit"):
        R.start_turn(ME, "again", cid)
    monkeypatch.setenv("DESIGN_AGENT_CONVERSATION_USD", "1")
    monkeypatch.setenv("DESIGN_AGENT_USER_DAY_USD", "0.15")
    with pytest.raises(R.AgentError, match="daily limit"):
        R.start_turn(ME, "new one")
    monkeypatch.setenv("DESIGN_AGENT_USER_DAY_USD", "5")
    monkeypatch.setenv("DESIGN_AGENT_DAY_USD", "0.1")
    other = Principal(email="other@example.com", credential_id="design-agent", kind="web")
    with pytest.raises(R.AgentError, match="busy today"):
        R.start_turn(other, "hi")


def test_an_api_failure_leaves_a_continuable_conversation(env, monkeypatch):
    class _Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise RuntimeError("network down")
    monkeypatch.setattr(R, "_client", lambda: _Boom())
    cid = R.start_turn(ME, "first")["id"]
    out = R.get_conversation(ME.email, cid)
    assert out["state"] == "error" and "unavailable" in out["error"]
    monkeypatch.setattr(R, "_client", lambda: _FakeClient([_resp([{"type": "text", "text": "back"}], "end_turn")]))
    R.start_turn(ME, "second", cid)
    doc = R._load(ME.email, cid)
    roles = [m["role"] for m in doc["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
    assert R.get_conversation(ME.email, cid)["state"] == "idle"


def test_conversations_are_private_to_their_owner(env, monkeypatch):
    monkeypatch.setattr(R, "_client", lambda: _FakeClient([_resp([{"type": "text", "text": "x"}], "end_turn")]))
    cid = R.start_turn(ME, "mine")["id"]
    with pytest.raises(R.AgentError, match="not found"):
        R.get_conversation("other@example.com", cid)
    assert R.list_conversations("other@example.com") == []
    assert [c["id"] for c in R.list_conversations(ME.email)] == [cid]
    with pytest.raises(R.AgentError):
        R.get_conversation(ME.email, "../../etc")


def test_access_modes(monkeypatch):
    admin = {"id": "a@x", "is_admin": True, "role": "admin"}
    eng = {"id": "e@x", "is_admin": False, "role": "engineer"}
    user = {"id": "u@x", "is_admin": False, "role": "user"}
    anon = {"id": "anonymous", "is_admin": False, "role": "anon"}
    monkeypatch.delenv("DESIGN_AGENT_ACCESS", raising=False)
    assert [R.may_use(x) for x in (admin, eng, user, anon)] == [True, False, False, False]
    monkeypatch.setenv("DESIGN_AGENT_ACCESS", "staff")
    assert [R.may_use(x) for x in (admin, eng, user, anon)] == [True, True, False, False]
    monkeypatch.setenv("DESIGN_AGENT_ACCESS", "signed_in")
    assert [R.may_use(x) for x in (admin, eng, user, anon)] == [True, True, True, False]


def test_routes_refuse_anonymous_and_closed_accounts(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import design_agent as D
    app = FastAPI()
    app.include_router(D.router)
    c = TestClient(app)
    who = {"v": {"id": "anonymous", "is_admin": False, "role": "anon"}}
    monkeypatch.setattr(D, "caller_identity", lambda a=None: who["v"])
    assert c.get("/api/design-agent/conversations").status_code == 401
    assert c.get("/api/design-agent/status").json() == {"allowed": False, "configured": True}
    who["v"] = {"id": "u@x", "is_admin": False, "role": "user"}
    assert c.post("/api/design-agent/conversations", json={"text": "hi"}).status_code == 403
    who["v"] = {"id": "a@x", "is_admin": True, "role": "admin"}
    monkeypatch.setattr(R, "_client", lambda: _FakeClient([_resp([{"type": "text", "text": "hello"}], "end_turn")]))
    r = c.post("/api/design-agent/conversations", json={"text": "hi"})
    assert r.status_code == 200 and r.json()["state"] in ("running", "idle")
    cid = r.json()["id"]
    assert c.get(f"/api/design-agent/conversations/{cid}").json()["display"][-1]["text"] == "hello"
    assert c.post("/api/design-agent/conversations", json={"text": ""}).status_code == 400


# ── the tools' client-view filter and the S1 helpers ─────────────────────────

def _tree():
    return [{"name": "D", "stator_diameter": 50, "configs": [
        {"name": "L15", "card_date": "2026-10-18", "default_duty": "rated", "duties": [{"name": "rated"}]},
        {"name": "L99", "card_date": None, "default_duty": "rated", "duties": []}]}]


def test_list_motors_shows_only_computed_configurations(monkeypatch):
    from motor_ai_sim import cooling_options as CO
    from motor_ai_sim import mcp_tools as MT
    monkeypatch.setattr(MT, "_visible_tree", _tree)
    monkeypatch.setattr(MT, "_machines", lambda: [
        {"die": "D", "config": c, "outer_diameter_mm": 50.0, "mass_kg": 0.2, "duties": []} for c in ("L15", "L99")])
    monkeypatch.setattr(CO, "_applications_of", lambda d, c: ["propeller"])
    monkeypatch.setattr(CO, "cooling_options", lambda d, c=None, applications=None: {
        "propellers": ["p15"], "default_propeller": "p15"})
    monkeypatch.setattr(T, "_has_s1", lambda d, c: True)
    out = T.list_motors("propeller")
    assert [(m["die"], m["config"]) for m in out["motors"]] == [("D", "L15")]
    assert out["motors"][0]["propellers"] == ["p15"] and out["motors"][0]["propeller_s1_available"] is True
    assert T.list_motors("robot")["count"] == 0
    with pytest.raises(T.AgentToolError):
        T._find("D", "L99")


def test_present_motor_checks_the_propeller_is_allowed(monkeypatch):
    from motor_ai_sim import cooling_options as CO
    from motor_ai_sim import mcp_tools as MT
    monkeypatch.setattr(MT, "_visible_tree", _tree)
    monkeypatch.setattr(CO, "cooling_options", lambda d, c=None, applications=None: {"propellers": ["p15"]})
    card = T.present_motor("D", "L15", "ok", propeller_id="p15")["card"]
    assert card == {"die": "D", "config": "L15", "duty": "rated", "propeller_id": "p15", "headline": "ok"}
    with pytest.raises(T.AgentToolError, match="not a propeller"):
        T.present_motor("D", "L15", "x", propeller_id="p20")
    with pytest.raises(T.AgentToolError, match="no duty"):
        T.present_motor("D", "L15", "x", duty="peak")


def test_thrust_interpolation_between_rows():
    rows = [{"rpm": 5000, "thrust_g": 1000, "current_A": 10, "ok": True, "binding": None},
            {"rpm": 6000, "thrust_g": 1500, "current_A": 16, "ok": False, "binding": "winding"}]
    at = T._at_thrust(rows, 1250)
    assert at["rpm"] == 5500 and at["current_A"] == 13 and at["ok"] is False and at["binding"] == "winding"
    assert T._at_thrust(rows, 2000) is None


def test_tool_specs_are_valid_and_named_once():
    names = [s["name"] for s in T.TOOL_SPECS]
    assert len(names) == len(set(names))
    assert set(names) == {"list_motors", "list_propellers", "propeller_s1", "check_fit", "get_machine_performance",
                          "present_motor"}
    for s in T.TOOL_SPECS:
        assert s["input_schema"]["type"] == "object" and s["description"]
