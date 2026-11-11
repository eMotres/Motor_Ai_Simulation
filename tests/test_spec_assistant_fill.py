"""Specification assistant: fill mode (it applies valid markers to the form, with Undo) and the kept conversation
(owner 2026-11-11).  Synthetic data only."""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import spec_assistant as A, spec_assistant_history as HI, spec_proposals as P, store
from motor_ai_sim.development.routes import router as dev_router
from motor_ai_sim.support_prompt import SPEC_SECTION as SPEC_PROMPT
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
    store.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "spec", "created_at": "t",
                          "updated_at": "t", "owner_engineer": OWN,
                          "members": [{"email": OWN, "role": "owner"},
                                      {"email": "eng@example.test", "role": "engineer"},
                                      {"email": "cust@example.test", "role": "customer"}],
                          "linked_motors": [], "notes": "", "pending_invites": []})
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


def _marker(field, value, reason="typical", basis="assumed"):
    return f'[[SPEC_PROPOSAL: field="{field}"; value={json.dumps(value)}; reason="{reason}"; basis="{basis}"]]'


def _fill_turn(monkeypatch, markers, *, can_apply=True, fill=True, email=OWN):
    monkeypatch.setattr(A, "complete", lambda m, s: ("Okay. " + " ".join(markers), "x", "y"))
    return A.turn("alpha", store.get_project("alpha"), {"email": email}, [{"role": "user", "content": "hi"}], "en",
                  can_apply=can_apply, fill=fill)


BAL = {"balance_grade": "G2.5", "runout_mm": 0.05}


def test_fill_mode_applies_records_provenance_and_undo_restores(env, monkeypatch):
    out = _fill_turn(monkeypatch, [_marker("4_8", BAL), _marker("load_type", "air_propeller", "customer said propeller", "stated")])
    assert out["proposals"] == [] and {p["field_id"] for p in out["applied"]} == {"4_8", "load_type"}
    assert "Filled in the form" in out["reply"]
    w = _wrapper()
    assert w["revision"] == 5 and w["draft"]["fields"]["4_8"]["value"]["balance_grade"] == "G2.5"
    fills = w["draft"]["provenance"]["assistant_fills"]
    assert fills["4_8"]["basis"] == "assumed" and fills["4_8"]["reason"].endswith("(assumed, to confirm)")
    assert fills["load_type"]["basis"] == "stated"
    applied = next(p for p in out["applied"] if p["field_id"] == "4_8")
    assert applied["via"] == "assistant" and applied["previous"]["status"] == "missing"
    c = env["client"]
    shown = c.get("/api/projects/alpha/spec", headers=H).json()["draft"]["provenance"]["assistant_fills"]
    assert shown["4_8"]["basis"] == "assumed" and "value" not in shown["4_8"]
    res = c.post(f"/api/projects/alpha/spec/proposals/{applied['id']}/undo", headers=H)
    assert res.status_code == 200 and "4_8" not in res.json()["draft"]["fields"]
    assert "4_8" not in (_wrapper()["draft"]["provenance"].get("assistant_fills") or {})
    assert P.get("alpha", applied["id"])["status"] == "undone"
    assert c.post(f"/api/projects/alpha/spec/proposals/{applied['id']}/undo", headers=H).status_code == 409
    audit = (store.path_for("alpha") / "audit.jsonl")
    if audit.exists():
        text = audit.read_text(encoding="utf-8")
        assert "spec.proposal.apply" in text and "spec.proposal.undo" in text


def test_undo_restores_a_replaced_value_and_refuses_after_a_later_edit(env, monkeypatch):
    _fill_turn(monkeypatch, [_marker("4_8", {"balance_grade": "G6.3", "runout_mm": 0.1})])
    second = _fill_turn(monkeypatch, [_marker("4_8", BAL)])["applied"][0]
    assert second["previous"]["value"]["balance_grade"] == "G6.3"
    P.undo("alpha", second["id"], OWN)
    assert _wrapper()["draft"]["fields"]["4_8"]["value"]["balance_grade"] == "G6.3"
    third = _fill_turn(monkeypatch, [_marker("4_8", {"balance_grade": "G1", "runout_mm": 0.02})])["applied"][0]

    def edit(w):
        w["draft"]["fields"]["4_8"]["value"] = {"balance_grade": "G2.5", "runout_mm": 0.07}
    store.mutate_json("alpha", "draft.json", edit)
    assert "4_8" not in P.live_fills(_wrapper())              # edited by hand: no longer marked as the assistant's
    with pytest.raises(P.ProposalConflict):
        P.undo("alpha", third["id"], OWN)


def test_a_page_save_keeps_the_stored_fills(env, monkeypatch):
    _fill_turn(monkeypatch, [_marker("4_8", BAL)])
    c = env["client"]
    got = c.get("/api/projects/alpha/spec", headers=H).json()
    saved = c.put("/api/projects/alpha/spec", headers=H, json={"draft": got["draft"], "revision": got["revision"]})
    assert saved.status_code == 200
    assert "4_8" in P.live_fills(_wrapper())


def test_customer_role_and_fill_off_get_cards_only(env, monkeypatch):
    before = _wrapper()["draft"]
    out = _fill_turn(monkeypatch, [_marker("4_8", BAL)], can_apply=False, fill=True)
    assert out["applied"] == [] and len(out["proposals"]) == 1 and out["proposals"][0]["status"] == "open"
    out = _fill_turn(monkeypatch, [_marker("4_5", "6205 deep groove")], can_apply=True, fill=False)
    assert out["applied"] == [] and len(out["proposals"]) == 1
    assert _wrapper()["draft"] == before and _wrapper()["revision"] == 3
    env["as"]("cust@example.test", "user")
    monkeypatch.setenv("DESIGN_AGENT_SPEC_ACCESS", "members")
    monkeypatch.setattr(A, "complete", lambda m, s: ("Hi. " + _marker("4_8", BAL), "x", "y"))
    r = env["client"].post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": [], "fill": True})
    assert r.status_code == 200 and r.json()["applied"] == [] and len(r.json()["proposals"]) == 1
    assert env["client"].post("/api/projects/alpha/spec/proposals/p-x/undo", headers=H).status_code == 403


def test_customer_decisions_are_never_filled_and_stated_only_fields_need_stated(env, monkeypatch):
    """Customer decisions are never proposed or assumed; since 2026-11-11 the assistant writes down what the CUSTOMER SAID
    (basis "stated") so the specification can be finished in the chat.  Commercial fields: never, not even stated."""
    before = _wrapper()["draft"]
    markers = [_marker("6_1", {"prototypes": 3}), _marker("6_2", {"x": 1}), _marker("4_2", {"target_kg": 2}),
               _marker("6_3", {"x": 1}), _marker("quantities", {"x": 1}), _marker("target_price", 5),
               _marker("6_3", {"amount": 9}, basis="stated"), _marker("target_price", 7, basis="stated")]
    out = _fill_turn(monkeypatch, markers)
    assert out["applied"] == [] and out["proposals"] == []
    assert _wrapper()["draft"] == before
    out = _fill_turn(monkeypatch, [_marker("6_5", "mutual NDA", basis="stated")])
    assert [p["field_id"] for p in out["applied"]] == ["6_5"] and out["applied"][0]["basis"] == "stated"
    out = _fill_turn(monkeypatch, [_marker("2_1", {"supply_type": "dc_source", "dc_source_V": 50})])
    assert out["applied"] == [] and [p["field_id"] for p in out["proposals"]] == ["2_1"]
    for fid in ("1_1", "key_points", "2_2"):
        got = A.process_markers("alpha", [{"field": fid, "value": "x", "reason": "r", "basis": "assumed"}], "en", fill_email=OWN)
        assert got["applied"] == []


def test_fill_cap_is_eight_and_proposal_cap_stays_three(env):
    fields = [("1_3", "notes a"), ("1_9", 60), ("2_8", "hall"), ("2_10", "2 m"), ("3_3", "IP54"), ("3_4", "dust"),
              ("4_3", "flange"), ("4_4", "shaft"), ("4_9", "no Al"), ("5_1", "ISO")]
    found = [{"field": f, "value": v, "reason": "r", "basis": "stated"} for f, v in fields]
    got = A.process_markers("alpha", found, "en", fill_email=OWN)
    assert len(got["applied"]) + len(got["open"]) == A.MAX_MARKERS_FILL == 8
    assert len(A.take_proposals("alpha", found, "en")) == A.MAX_MARKERS == 3


def test_prompt_and_context_describe_fill_mode(env):
    assert "FILL MODE ON" in SPEC_PROMPT and "at most 8" in SPEC_PROMPT
    wrapper = P.draft_wrapper("alpha")
    on = A.build_context(store.get_project("alpha"), wrapper, [], lang="en", can_apply=True, fill=True)
    off = A.build_context(store.get_project("alpha"), wrapper, [], lang="en", can_apply=True, fill=False)
    assert "FILL MODE IS ON" in on and "FILL MODE IS OFF" in off


def test_history_is_saved_loaded_cleared_and_private(env, monkeypatch):
    c, as_ = env["client"], env["as"]
    monkeypatch.setattr(A, "complete", lambda m, s: ("Got it. " + _marker("4_8", BAL), "x", "y"))
    r = c.post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": [{"role": "user", "content": "balance?"}], "lang": "en"})
    assert r.status_code == 200 and len(r.json()["applied"]) == 1
    hist = c.get("/api/projects/alpha/spec/assistant/history", headers=H).json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"] and hist[0]["text"] == "balance?"
    assert hist[1]["applied"][0]["field_id"] == "4_8" and hist[1]["applied"][0]["status"] == "applied"
    c.post(f"/api/projects/alpha/spec/proposals/{hist[1]['applied'][0]['id']}/undo", headers=H)
    assert c.get("/api/projects/alpha/spec/assistant/history", headers=H).json()["messages"][1]["applied"][0]["status"] == "undone"
    name = hashlib.sha256(OWN.encode()).hexdigest()[:16]
    assert (store.path_for("alpha") / "spec_assistant" / f"{name}.json").is_file()
    # a kickoff (no typed text) stores only the answer
    c.post("/api/projects/alpha/spec/assistant", headers=H, json={"messages": [], "lang": "en"})
    assert [m["role"] for m in c.get("/api/projects/alpha/spec/assistant/history", headers=H).json()["messages"]] == ["user", "assistant", "assistant"]
    monkeypatch.setenv("DESIGN_AGENT_SPEC_ACCESS", "members")
    as_("cust@example.test", "user")
    assert c.get("/api/projects/alpha/spec/assistant/history", headers=H).json()["messages"] == []
    assert c.get(f"/api/projects/alpha/spec/assistant/history?user={OWN}", headers=H).status_code == 403
    as_("eng@example.test", "engineer")                        # staff read anyone's
    assert len(c.get(f"/api/projects/alpha/spec/assistant/history?user={OWN}", headers=H).json()["messages"]) == 3
    as_(OWN, "engineer")
    assert c.delete("/api/projects/alpha/spec/assistant/history", headers=H).json() == {"messages": []}
    assert c.get("/api/projects/alpha/spec/assistant/history", headers=H).json()["messages"] == []


def test_history_keeps_everything_while_the_model_sees_the_last_turns(env, monkeypatch):
    seen = {}

    def fake(messages, system):
        seen["n"] = len(messages)
        return "ok", "x", "y"
    monkeypatch.setattr(A, "complete", fake)
    HI.append("alpha", OWN, [{"role": "user" if i % 2 == 0 else "assistant", "text": f"m{i}"} for i in range(60)])
    assert len(HI.load("alpha", OWN)) == 60
    sent = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(59)] + [{"role": "user", "content": "last"}]
    A.turn("alpha", store.get_project("alpha"), {"email": OWN}, sent, "en", can_apply=True)
    assert seen["n"] <= A.MAX_TURNS
