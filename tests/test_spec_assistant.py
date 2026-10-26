"""Specification assistant (owner 2026-10-27): marker parsing, proposal validation, recalc mapping, the proposal
lifecycle (apply = normalise + revision, stale refused, editors only), access defaults, the prompt rules.

No network: the provider call (``spec_assistant.complete``) is stubbed.  All values are made up."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import spec_assistant as A, spec_proposals as P, store
from motor_ai_sim.development.routes import router as dev_router
from motor_ai_sim.development.spec_assistant_prompt import SPEC_PROMPT
from motor_ai_sim.development.spec_assistant_routes import router

H = {"Authorization": "Bearer dev"}
OWN = "own@example.test"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.delenv("DESIGN_AGENT_SPEC_ACCESS", raising=False)
    monkeypatch.delenv("SPEC_ASSISTANT_USER_DAY_MAX", raising=False)
    A.reset_limits()
    current = {"email": OWN, "role": "engineer"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True})
    project = {"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t",
               "updated_at": "t", "owner_engineer": OWN,
               "members": [{"email": OWN, "role": "owner"},
                           {"email": "eng@example.test", "role": "engineer"},
                           {"email": "cust@example.test", "role": "customer"}],
               "linked_motors": [], "notes": "", "pending_invites": []}
    store.create_project(project)
    store.save_json("alpha", "draft.json", {
        "draft": {"schema_version": 2, "fields": {"1_1": {"status": "filled", "value": "quad"}}, "mission_profile": [],
                  "mission_profile_status": "missing", "production": {}, "provenance": {}},
        "revision": 3, "schema_version": 2, "version": None})
    app = FastAPI()
    app.include_router(router)
    app.include_router(dev_router)
    return {"client": TestClient(app), "as": lambda email, role: current.update(email=email, role=role)}


def _wrapper():
    return P.draft_wrapper("alpha")


def _turn(**kw):
    return A.turn("alpha", store.get_project("alpha"), {"email": OWN}, kw.get("messages", []), "en", can_apply=True)


# -- the marker ---------------------------------------------------------------

def test_marker_is_stripped_and_forgiving_about_quoting():
    text = ('Balance grade first.\n[[SPEC_PROPOSAL: field="4_8"; value={"balance_grade":"G2.5","runout_mm":0.05}; '
            'reason="typical for 400 mm at 1200 rpm"; basis="assumed"]]')
    clean, found = A.parse_proposals(text)
    assert clean == "Balance grade first."
    assert found == [{"field": "4_8", "value": {"balance_grade": "G2.5", "runout_mm": 0.05},
                      "reason": "typical for 400 mm at 1200 rpm", "basis": "assumed"}]
    _, found = A.parse_proposals("x [[spec_proposal field=2_11; value='Class H'; reason='winding limit 180 C']] y")
    assert found[0]["field"] == "2_11" and found[0]["value"] == "Class H" and found[0]["reason"] == "winding limit 180 C"
    clean, found = A.parse_proposals('a [[SPEC_PROPOSAL: field=2_3; value={"continuous_A": 40}; reason="P/V"]] b')
    assert " ".join(clean.split()) == "a b"
    assert found[0]["value"] == {"continuous_A": 40}


def test_marker_value_may_contain_brackets_and_unclosed_markers_never_leak():
    text = ('ok [[SPEC_PROPOSAL: field="key_points"; value=[{"name":"hover","torque_Nm":1,"speed_rpm":900,'
            '"duration_s":"continuous"}]; reason="r"]]')
    clean, found = A.parse_proposals(text)
    assert clean == "ok" and found[0]["value"][0]["name"] == "hover" and found[0]["reason"] == "r"
    clean, found = A.parse_proposals('hello [[SPEC_PROPOSAL: field="4_8"; value={"runout_mm": 0.1')
    assert clean == "hello" and "SPEC_PROPOSAL" not in clean
    clean, found = A.parse_proposals("plain text, no marker")
    assert clean == "plain text, no marker" and found == []
    clean, found = A.parse_proposals('[[SPEC_PROPOSAL: field=1_1; value=a; reason=x]] [[SPEC_PROPOSAL: field=1_2; value=b; reason=y]]')
    assert clean == "" and [f["field"] for f in found] == ["1_1", "1_2"]


# -- validation ---------------------------------------------------------------

def test_a_bad_value_is_refused_and_a_good_one_is_normalised(env):
    w = _wrapper()
    with pytest.raises(P.ProposalError, match="outside allowed range"):
        P.validate(w, "4_8", {"runout_mm": -1})
    with pytest.raises(P.ProposalError, match="unknown properties"):
        P.validate(w, "4_8", {"flatness": 1})
    with pytest.raises(P.ProposalError, match="unsupported choice"):
        P.validate(w, "load_type", "rocket")
    with pytest.raises(P.ProposalError, match="unknown field"):
        P.validate(w, "9_9", 1)
    with pytest.raises(P.ProposalError, match="empty"):
        P.validate(w, "1_2", "  ")
    assert P.validate(w, "load_type", "air_propeller") == {"value": "air_propeller", "status": "filled"}
    # a number or an object written as text is accepted
    assert P.validate(w, "load_inertia_kg_m2", "0.4")["value"] == 0.4
    assert P.validate(w, "4_8", '{"balance_grade":"G2.5","runout_mm":0.05}')["value"]["runout_mm"] == 0.05


def test_customer_decisions_and_derived_fields_are_never_proposed(env):
    w = _wrapper()
    for fid, val in (("6_1", {"prototypes": 3}), ("6_2", {"first": "2027-01-01"}), ("4_2", {"target_kg": 5}),
                     ("6_3", {"amount": 10}), ("target_price", 5), ("quantities", {"x": 1})):
        with pytest.raises(P.ProposalError, match="customer decision"):
            P.validate(w, fid, val)
    with pytest.raises(P.ProposalError, match="derived"):
        P.validate(w, "1_4", {"torque_Nm": 1, "speed_rpm": 100})


def test_recalc_mapping_follows_the_field():
    both = P.recalc_for("2_1", "2")
    assert both["needed"] and both["text"]["en"] == "coupled thermal + mechanics of the key points"
    for fid, sec in (("key_points", "1"), ("1_6", "1"), ("3_2", "3"), ("2_3", "2"), ("2_11", "2"), ("load_type", "4")):
        assert P.recalc_for(fid, sec)["kind"] == "thermal_mechanics", fid
    assert P.recalc_for("4_8", "4") == {"needed": True, "kind": "mechanics", "text": P.RECALC_TEXT["mechanics"]}
    for fid, sec in (("5_1", "5"), ("1_3", "1"), ("2_8", "2"), ("4_3", "4"), ("6_4", "6")):
        assert P.recalc_for(fid, sec) == {"needed": False, "kind": "none", "text": P.RECALC_TEXT["none"]}, fid
    assert P.recalc_for("future_field", "3")["needed"] is True       # an unknown physics field is treated with care


# -- lifecycle ----------------------------------------------------------------

def test_proposal_lifecycle_apply_goes_through_normalise_and_revision(env):
    p = P.create("alpha", "4_8", {"balance_grade": "G2.5", "runout_mm": 0.05}, "typical (assumed, to confirm)")
    assert p["status"] == "open" and p["by"] == "assistant" and p["recalc"]["kind"] == "mechanics"
    assert _wrapper()["draft"]["fields"].get("4_8") is None              # nothing is written by proposing
    newer = P.create("alpha", "4_8", {"balance_grade": "G6.3", "runout_mm": 0.1}, "looser (assumed, to confirm)")
    rows = {r["id"]: r for r in P.list_proposals("alpha")}
    assert rows[p["id"]]["status"] == "dismissed" and rows[newer["id"]]["status"] == "open"
    with pytest.raises(P.ProposalConflict) as stale:
        P.apply("alpha", newer["id"], OWN, 2)
    assert stale.value.revision == 3 and _wrapper()["revision"] == 3
    done = P.apply("alpha", newer["id"], OWN, 3)
    assert done["revision"] == 4 and done["proposal"]["status"] == "applied" and done["proposal"]["applied_revision"] == 4
    w = _wrapper()
    assert w["revision"] == 4
    assert w["draft"]["fields"]["4_8"] == {"value": {"balance_grade": "G6.3", "runout_mm": 0.1}, "status": "filled"}
    assert w["draft"]["fields"]["1_1"]["value"] == "quad"                # the rest is untouched
    with pytest.raises(P.ProposalConflict, match="already applied"):
        P.apply("alpha", newer["id"], OWN, 4)
    audit = (store.path_for("alpha") / "audit.jsonl").read_text(encoding="utf-8")
    assert '"spec.proposal.apply"' in audit
    with pytest.raises(LookupError):
        P.apply("alpha", "p-nope", OWN, 4)


def test_apply_revalidates_against_the_current_draft(env):
    p = P.create("alpha", "load_type", "air_propeller", "stated by the customer")

    def break_it(doc):                       # the stored value no longer validates (simulated)
        doc["proposals"][0]["value"] = "rocket"
    store.mutate_json("alpha", "spec_proposals.json", break_it)
    with pytest.raises(P.ProposalError, match="unsupported choice"):
        P.apply("alpha", p["id"], OWN, 3)
    assert _wrapper()["revision"] == 3


def test_assumed_reason_is_tagged_and_stated_is_not(env):
    got = A.take_proposals("alpha", [
        {"field": "4_8", "value": {"runout_mm": 0.05}, "reason": "typical for 400 mm", "basis": "assumed"},
        {"field": "load_type", "value": "air_propeller", "reason": "customer said propeller", "basis": "stated"},
        {"field": "2_11", "value": "Class H", "reason": "winding limit 180 C", "basis": "derived"}], "en")
    by = {g["field_id"]: g for g in got}
    assert by["4_8"]["reason"].endswith("(assumed, to confirm)")
    assert "assumed" not in by["load_type"]["reason"] and "assumed" not in by["2_11"]["reason"]
    zh = A.take_proposals("alpha", [{"field": "4_8", "value": {"runout_mm": 0.07}, "reason": "典型值"}], "zh-CN")
    assert zh[0]["reason"].endswith("（假设，待确认）")


def test_invalid_markers_are_dropped_not_raised(env):
    got = A.take_proposals("alpha", [
        {"field": "4_8", "value": {"runout_mm": -3}, "reason": "x"},
        {"field": "6_1", "value": {"prototypes": 9}, "reason": "x"},
        {"field": "nope", "value": 1, "reason": "x"},
        {"field": "4_8", "reason": "no value"},
        {"field": "4_8", "value": {"runout_mm": 0.05}, "reason": ""},
        {"field": "4_8", "value": {"runout_mm": 0.05}, "reason": "fine", "basis": "stated"}], "en")
    assert [g["field_id"] for g in got] == ["4_8"] and got[0]["value"] == {"runout_mm": 0.05}


# -- turn, context, prompt ----------------------------------------------------

def test_prompt_carries_the_owner_rules():
    low = SPEC_PROMPT.lower()
    assert "never discuss prices" in low and "budgets" in low and "quotes" in low
    assert "customer decisions" in low and "section 6" in low and "mass target" in low
    assert "(assumed, to confirm)" in SPEC_PROMPT
    assert "one field" in low and "short" in low and "chinese" in low
    assert "you never change the specification" in low


def test_context_lists_missing_fields_with_shapes_and_hides_customer_decisions(env):
    ctx = A.build_context(store.get_project("alpha"), _wrapper(), [], lang="en", can_apply=False)
    req = ctx.split("OPTIONAL fields")[0]
    assert "REQUIRED fields still missing" in req
    assert "\n2_1 " in req and "voltage_nominal_V: number [V]" in req       # a required field with its shape
    assert "\n1_1 " not in req and "1_1 Application = " in ctx              # a filled field is listed as filled
    assert "\n4_8 " in ctx.split("OPTIONAL fields")[1] and "balance_grade: string e.g. G6.3/G2.5" in ctx
    assert "6_3" in ctx.split("CUSTOMER DECISIONS")[1] and "\n6_3 " not in ctx
    assert "CANNOT apply" in ctx
    zh = A.build_context(store.get_project("alpha"), _wrapper(), [], lang="zh-CN", can_apply=True)
    assert "Chinese (zh-CN)" in zh and "CAN apply" in zh and "电池" in zh


def test_turn_stores_the_proposal_and_strips_the_marker(env, monkeypatch):
    seen = {}

    def fake(messages, system):
        seen["messages"], seen["system"] = messages, system
        return ('Runout first. Proposal below.\n[[SPEC_PROPOSAL: field="4_8"; '
                'value={"balance_grade":"G2.5","runout_mm":0.05}; reason="typical for 400 mm"; basis="assumed"]]'), \
            "gemini", "flash-lite"
    monkeypatch.setattr(A, "complete", fake)
    out = _turn()
    assert out["reply"] == "Runout first. Proposal below." and out["source"] == "gemini"
    assert seen["messages"][0]["role"] == "user" and "single most important missing field" in seen["messages"][0]["content"]
    assert "SPECIFICATION CONTEXT" in seen["system"] and "never discuss prices" in seen["system"].lower()
    assert len(out["proposals"]) == 1 and out["proposals"][0]["current"]["status"] == "missing"
    assert out["proposals"][0]["reason"].endswith("(assumed, to confirm)")
    # a second turn knows the open proposal and does not need the kickoff
    _turn(messages=[{"role": "user", "content": "ok"}])
    assert "PROPOSALS already waiting" in seen["system"] and "4_8" in seen["system"].split("PROPOSALS already waiting")[1]
    assert seen["messages"] == [{"role": "user", "content": "ok"}]


def test_provider_failure_and_missing_provider_are_answers(env, monkeypatch):
    def boom(messages, system):
        raise RuntimeError("503 overloaded")
    monkeypatch.setattr(A, "complete", boom)
    out = _turn()
    assert out["source"] == "error" and out["proposals"] == []

    def none(messages, system):
        raise A.AssistantUnavailable("none")
    monkeypatch.setattr(A, "complete", none)
    assert _turn()["source"] == "mock"


def test_daily_cap_blocks_before_the_provider(env, monkeypatch):
    monkeypatch.setenv("SPEC_ASSISTANT_USER_DAY_MAX", "1")
    monkeypatch.setattr(A, "complete", lambda m, s: ("hi", "gemini", "m"))
    assert _turn()["source"] == "gemini"
    monkeypatch.setattr(A, "complete", lambda m, s: pytest.fail("provider called over the cap"))
    assert _turn()["source"] == "rate_limited"


# -- routes and access --------------------------------------------------------

def test_default_access_is_staff_only_and_members_opens_it(env, monkeypatch):
    c, as_ = env["client"], env["as"]
    monkeypatch.setattr(A, "complete", lambda m, s: ("Hello", "gemini", "m"))
    assert A.access_mode() == "staff"
    as_("cust@example.test", "user")
    st = c.get("/api/projects/alpha/spec/assistant", headers=H).json()
    assert st["allowed"] is False and st["can_apply"] is False
    assert c.post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": []}).status_code == 403
    assert c.get("/api/projects/alpha/spec/proposals", headers=H).status_code == 403
    as_("eng@example.test", "engineer")                       # staff, not the project owner
    st = c.get("/api/projects/alpha/spec/assistant", headers=H).json()
    assert st["allowed"] is True and st["can_apply"] is False
    assert c.post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": []}).json()["reply"] == "Hello"
    monkeypatch.setenv("DESIGN_AGENT_SPEC_ACCESS", "members")
    as_("cust@example.test", "user")
    st = c.get("/api/projects/alpha/spec/assistant", headers=H).json()
    assert st["allowed"] is True and st["can_apply"] is False
    assert c.post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": []}).status_code == 200
    as_("stranger@example.test", "user")                      # not a member: the project does not exist for them
    assert c.get("/api/projects/alpha/spec/assistant", headers=H).status_code == 404
    monkeypatch.setenv("DESIGN_AGENT_SPEC_ACCESS", "bogus")
    assert A.access_mode() == "staff"


def test_only_the_spec_editor_applies_and_the_page_gets_the_new_revision(env, monkeypatch):
    c, as_ = env["client"], env["as"]
    p = P.create("alpha", "4_8", {"balance_grade": "G2.5", "runout_mm": 0.05}, "typical (assumed, to confirm)")
    url = f"/api/projects/alpha/spec/proposals/{p['id']}"
    monkeypatch.setenv("DESIGN_AGENT_SPEC_ACCESS", "members")
    for email, role in (("cust@example.test", "user"), ("eng@example.test", "engineer")):
        as_(email, role)
        assert c.post(url + "/apply", headers=H, json={"revision": 3}).status_code == 403
        assert c.post(url + "/dismiss", headers=H).status_code == 403
        listed = c.get("/api/projects/alpha/spec/proposals", headers=H).json()
        assert listed["can_apply"] is False and listed["revision"] == 3
        assert listed["proposals"][0]["id"] == p["id"] and listed["proposals"][0]["recalc"]["kind"] == "mechanics"
    as_(OWN, "engineer")                                      # the project owner
    assert c.post(url + "/apply", headers=H, json={}).status_code == 422
    stale = c.post(url + "/apply", headers=H, json={"revision": 1})
    assert stale.status_code == 409 and stale.json()["detail"]["revision"] == 3
    ok = c.post(url + "/apply", headers=H, json={"revision": 3})
    assert ok.status_code == 200 and ok.json()["revision"] == 4
    assert ok.json()["draft"]["fields"]["4_8"]["value"]["balance_grade"] == "G2.5"
    # the page can save on top of it: the normal PUT path accepts the new revision
    saved = c.put("/api/projects/alpha/spec", headers=H, json={"draft": ok.json()["draft"], "revision": 4})
    assert saved.status_code == 200 and saved.json()["revision"] == 5
    assert c.get("/api/projects/alpha/spec/proposals", headers=H).json()["proposals"] == []
    done = c.get("/api/projects/alpha/spec/proposals?status=all", headers=H).json()["proposals"]
    assert done[0]["status"] == "applied" and done[0]["applied_by"] == OWN
    assert c.post(url + "/apply", headers=H, json={"revision": 5}).status_code == 409


def test_dismiss_by_the_owner_closes_the_proposal(env):
    c = env["client"]
    p = P.create("alpha", "load_type", "air_propeller", "customer said propeller")
    r = c.post(f"/api/projects/alpha/spec/proposals/{p['id']}/dismiss", headers=H)
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "dismissed"
    assert c.post(f"/api/projects/alpha/spec/proposals/{p['id']}/dismiss", headers=H).status_code == 409
    assert c.post("/api/projects/alpha/spec/proposals/p-none/dismiss", headers=H).status_code == 404
    assert _wrapper()["revision"] == 3
