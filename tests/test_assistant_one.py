"""The ONE assistant (owner 2026-11-11): estimates from the propeller catalogue, the help chat creating the specification itself,
the hand-off through sign-up, the spec mode in the same chat (fill, not-known-yet, submit on confirmation) and the web lookup.

The owner's failed conversation of 2026-11-11 («Задай мне мотор под 42 дюймовый пропеллер, напряжение батареи 12s» -> "numbers without
a calculation would be invention") is the first case.  The propeller card is the T-Motor HEP-S 42*16 entry of the server catalogue
(published vendor rows, copied here); the provider is a fake - nothing leaves the test."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import (account_lifecycle, assistant_archive as AR, assistant_estimate as E, assistant_handoff as HO, auth,
                          platform_agreement, propeller as PR, users)
from motor_ai_sim.development import routes as DR, spec_assistant as A, spec_assistant_history as HI, spec_proposals as P, store
from motor_ai_sim.routes import support

H = {"Authorization": "Bearer t"}
CUST = "cust@example.test"
REQUEST = "Задай мне мотор под 42 дуйиовый пропеллер напряжение батареи 12s"

HEP_S_42 = {
    "schema": "propeller-1", "id": "tmotor_hep_s_42x16", "vendor": "T-MOTOR", "model": "HEP-S 42*16", "status": "current",
    "product_url": "https://store.tmotor.com/product/hep-s-42-12-carbon-fiber-uav-propeller.html",
    "geometry": {"diameter_in": 42.0, "diameter_mm": 1066.8, "disc_diameter_mm": 1072.4, "pitch_in": 16.0},
    "published_limits": {"recommended_max_rpm": 3640, "limit_rpm": 4050},
    "performance": {"data_quality": "torque_measured", "test_density_kg_m3": 1.1924, "tables": [{
        "id": "lab", "source_url": "https://store.tmotor.com/product/hep-s-42-12-carbon-fiber-uav-propeller.html",
        "torque_resolution_Nm": 0.01, "rows": [
            {"rpm": r, "thrust_g": t, "torque_Nm": q} for r, t, q in (
                (1670, 10463, 5.14), (1840, 12796, 6.23), (2010, 15370, 7.45), (2180, 18184, 8.80), (2350, 21239, 10.27),
                (2520, 24535, 11.87), (2690, 28072, 13.59), (2860, 31850, 15.45), (3030, 35868, 17.43), (3200, 40128, 19.53),
                (3370, 44628, 21.77), (3540, 49368, 24.13), (3710, 54350, 26.62), (3880, 59572, 29.23), (4050, 65036, 31.97))]}]},
}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    d = tmp_path / "props" / "tmotor"
    d.mkdir(parents=True)
    (d / "hep_s_42x16.yaml").write_text(json.dumps(HEP_S_42), encoding="utf-8")       # JSON is YAML
    monkeypatch.setattr(PR, "_DIR", tmp_path / "props")
    PR._cache.clear()
    yield
    PR._cache.clear()


class FakeClient:
    """Anthropic's client shape: records each call, answers with the next scripted list of blocks."""

    def __init__(self, *scripts):
        self.scripts, self.calls = list(scripts), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        blocks = self.scripts.pop(0) if self.scripts else [NS(type="text", text="ok")]
        blocks = [NS(type="text", text=b) if isinstance(b, str) else b for b in blocks]
        return NS(content=blocks, stop_reason="end_turn", usage=NS(input_tokens=1, output_tokens=1))


@pytest.fixture
def env(tmp_path, monkeypatch, catalog):
    ident = tmp_path / "identity"
    ident.mkdir()
    monkeypatch.setattr(users, "_USERS_FILE", str(ident / "users.json"))
    monkeypatch.setattr(account_lifecycle, "identity_dir", lambda: ident)
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.delenv("SUPPORT_WEB_SEARCH_DAY_MAX", raising=False)
    A.reset_limits()
    current = {"email": CUST, "role": "user"}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(current) if token else None)
    monkeypatch.setattr(auth, "caller_identity", lambda authorization=None: {"id": current["email"], "role": current["role"]}
                        if authorization else {"id": "", "role": "anon"})
    monkeypatch.setattr(users, "get_user", lambda email: {"email_verified": True, "role": "user"})
    monkeypatch.setattr(support, "_load_overrides", lambda: {})
    monkeypatch.setattr(support.support_catalog, "published_motors_block", lambda *a, **k: "")
    monkeypatch.setattr(DR, "_notify_staff", lambda *a, **k: None)
    monkeypatch.setattr(support, "_effective", lambda: {"provider": "anthropic", "anthropic": {"key": "k", "model": "claude-haiku-5-5"},
                                                        "gemini": {"key": "", "model": ""}})
    support.reset_limits()
    app = FastAPI()
    app.include_router(support.router)
    app.include_router(DR.router)
    box = {}

    def use(client):
        monkeypatch.setattr(support, "_anthropic_client", lambda key: client)
        box["client"] = client
        return client
    return {"client": TestClient(app), "use": use, "as": lambda email, role: current.update(email=email, role=role)}


def _chat(env, text, *, headers=H, place=None, history=None, fill=None):
    msgs = (history or []) + [{"role": "user", "content": text}]
    body = {"messages": msgs, "lang": "en", "sid": "sessionid123456"}
    if place:
        body["place"] = place
    if fill is not None:
        body["fill"] = fill
    return env["client"].post("/api/support/chat", headers=headers, json=body)


# -- 1. estimate from the propeller catalogue ---------------------------------

def test_the_42_inch_12s_request_yields_points_derived_from_the_prop_table(catalog):
    est = E.estimate([REQUEST])
    assert est["found"] and est["propeller_id"] == "tmotor_hep_s_42x16" and est["request"]["cells"] == 12
    mx, hv = est["max"], est["hover"]
    assert mx["rpm"] == 3640                                             # the published recommended max rpm
    rows = HEP_S_42["performance"]["tables"][0]["rows"]
    assert rows[11]["torque_Nm"] < mx["torque_Nm"] < rows[12]["torque_Nm"]  # between the 3540 and 3710 rpm rows of the table
    assert 49.4 < mx["thrust_kgf"] < 54.4 and 9000 < mx["shaft_power_W"] < 10500
    assert abs(hv["thrust_N"] - 0.5 * mx["thrust_N"]) < 1.0 and 2400 < hv["rpm"] < 2800
    assert est["battery"]["voltage_min_V"] == 36.0 and est["battery"]["voltage_max_V"] == 50.4
    block = E.render_for_model(est)
    assert "derived from the T-Motor HEP-S 42*16 data" in block and "store.tmotor.com" in block and "assumed" in block
    values = {v["field"]: v for v in E.spec_values(est)}
    assert values["key_points"]["basis"] == "derived" and values["key_points"]["value"][0]["duration_s"] == "continuous"
    assert values["1_6"]["value"] == {"maximum_rpm": 3640} and values["2_1"]["value"]["series_cells"] == 12
    assert values["3_2"]["basis"] == "assumed" and values["3_2"]["value"] == {"temperature_max_C": 40.0}
    # never the customer's own constraints
    assert not set(values) & {"4_1", "4_2", "6_1", "6_2", "6_4", "6_5", "quantities", "6_3", "target_price"}


def test_sizes_and_batteries_are_read_from_plain_words(catalog):
    assert E.parse_request(["42x16 prop on 12S"])["pitch_in"] == 16.0
    assert E.parse_request(['a 42" propeller, 44.4 V'])["voltage_V"] == 44.4
    assert E.estimate(["robot joint, 48 V, 30 N·m"]) is None                     # no propeller named: no estimate
    miss = E.estimate(["APC 22x10E at 6S"])
    assert miss["found"] is False and "NOT in our propeller catalogue" in E.render_for_model(miss)


# -- 2. the help chat creates the specification itself ------------------------

def test_the_help_chat_creates_the_project_and_fills_derived_values(env):
    client = env["use"](FakeClient([
        "From the T-Motor HEP-S 42*16 data at 12S: hover 2601 rpm / 13.1 N·m, max 3640 rpm / 26.2 N·m. "
        'I will ask for the envelope next.\n[[SPEC_PROPOSAL: field="1_1"; value="Drone propulsion"; reason="customer: heavy-lift drone"; '
        'basis="stated"]]\n[[SPEC_PROPOSAL: field="4_2"; value={"complete_kg": 1.5}; reason="typical"; basis="assumed"]]\n[[CREATE_SPEC]]']))
    r = _chat(env, REQUEST + ". It is for a heavy-lift drone; please fill the specification.")
    assert r.status_code == 200
    out = r.json()
    assert "[[" not in out["reply"] and "I created the project" in out["reply"] and out["openSpec"] is True
    pid = out["project"]["id"]
    # the catalogue block reached the model, and the web tool was offered to the signed-in caller
    system = client.calls[0]["system"]
    assert "PROPELLER ESTIMATE" in system and "tmotor_hep_s_42x16" in system and "CREATE_SPEC" in system
    assert client.calls[0]["tools"][0]["type"] == "web_search_20250305"            # Haiku 5.5: the basic tool
    w = P.draft_wrapper(pid)
    fields, fills = w["draft"]["fields"], w["draft"]["provenance"]["assistant_fills"]
    assert fields["1_6"]["value"]["maximum_rpm"] == 3640 and fills["key_points"]["basis"] == "derived"
    assert "HEP-S 42*16" in fills["key_points"]["reason"] and fields["2_1"]["value"]["series_cells"] == 12
    assert fields["1_1"]["value"] == "Drone propulsion" and fills["1_1"]["basis"] == "stated"
    assert fills["3_2"]["basis"] == "assumed" and "(assumed, to confirm)" in fills["3_2"]["reason"]
    assert "4_2" not in fields and "4_1" not in fields                                          # customer-only stay open
    assert not set(fills) & {"4_1", "4_2", "6_1", "6_2", "6_4", "6_5", "quantities"}            # (6_1 holds only its form default)
    from motor_ai_sim.development import models
    assert models.completeness(w["draft"])["simulation_ready"] is True
    hist = HI.load(pid, CUST)
    assert hist[0]["role"] == "user" and REQUEST in hist[0]["text"] and hist[-1]["applied"]       # the chat goes on in the project
    assert list((account_lifecycle.identity_dir() / "assistant_handoff").glob("*.json")) == []       # a used hand-off is gone


def test_agreement_first_then_the_kept_handoff_creates_the_project(env, monkeypatch):
    env["use"](FakeClient(["Values below.\n[[CREATE_SPEC]]"]))
    state = {"accepted": False}

    def gate(user):
        if not state["accepted"]:
            raise HTTPException(status_code=428, detail={"code": "agreement_required", "version": "v"})
    monkeypatch.setattr(platform_agreement, "require_accepted", gate)
    out = _chat(env, "42 inch prop, 12S - make me the specification").json()
    assert out["handoff"]["status"] == "agreement_required" and "project" not in out
    token = out["handoff"]["token"]
    assert env["client"].post(f"/api/support/handoff/{token}/create", headers=H).status_code == 428   # the dialog, then the retry
    state["accepted"] = True
    made = env["client"].post(f"/api/support/handoff/{token}/create", headers=H).json()
    assert made["openSpec"] and {p["field_id"] for p in made["applied"]} >= {"key_points", "1_6", "2_1", "3_1", "3_2", "3_3"}
    assert env["client"].post(f"/api/support/handoff/{token}/create", headers=H).status_code == 404   # used once


def test_a_visitor_gets_a_sign_up_link_that_keeps_the_values(env):
    client = env["use"](FakeClient(["After a free sign-up the specification opens with these values.\n[[CREATE_SPEC]]"]))
    out = _chat(env, "42 inch propeller, 12S, fill the specification please", headers={}).json()
    hand = out["handoff"]
    assert hand["status"] == "sign_up" and hand["path"].startswith("/projects?new=spec&handoff=") and hand["values"] >= 6
    assert "tools" not in client.calls[0]                                             # no web search for visitors
    from motor_ai_sim import return_path
    assert return_path.safe_app_path(hand["path"]) == hand["path"]                    # the link survives the sign-up
    rec = json.loads((account_lifecycle.identity_dir() / "assistant_handoff" / f"{hand['token']}.json").read_text(encoding="utf-8"))
    assert rec["account"] == "" and set(rec) == {"token", "account", "created", "expires", "lang", "values", "conversation"}
    made = env["client"].post(f"/api/support/handoff/{hand['token']}/create", headers=H).json()      # after sign-up
    assert made["project"]["id"] and len(made["applied"]) >= 6


def test_a_handoff_expires_and_belongs_to_its_account(env):
    token = HO.put([], [], account="other@example.test")
    assert env["client"].post(f"/api/support/handoff/{token}/create", headers=H).status_code == 404
    old = HO.put([], [], account=CUST, now=0.0)
    assert env["client"].post(f"/api/support/handoff/{old}/create", headers=H).status_code == 404


# -- 3. the same chat inside the Specification ---------------------------------

def _project_with_values(env):
    env["use"](FakeClient(["Done.\n[[CREATE_SPEC]]"]))
    return _chat(env, REQUEST + ", fill the specification").json()["project"]["id"]


def test_spec_mode_marks_not_known_yet_and_submits_only_on_confirmation(env):
    pid = _project_with_values(env)
    place = {"project_id": pid, "view": "specification"}
    nky = " ".join(f'[[SPEC_PROPOSAL: field="{f}"; value=null; status="not_known_yet"; reason="customer: not known yet"; basis="stated"]]'
                    for f in ("2_4", "4_1", "4_2", "6_2", "mission_profile"))
    client = env["use"](FakeClient(
        ['Noted. [[SPEC_PROPOSAL: field="1_1"; value="Drone propulsion"; reason="customer said"; basis="stated"]] ' + nky],
        ["It can be submitted now. Submit?"],
        ["Submitting.\n[[SUBMIT_SPEC]]"]))
    out = _chat(env, "heavy-lift drone; the rest I do not know yet", place=place).json()
    assert out["mode"] == "spec" and {p["field_id"] for p in out["applied"]} == {"1_1", "2_4", "4_1", "4_2", "6_2", "mission_profile"}
    assert "SPECIFICATION CONTEXT" in client.calls[0]["system"] and "In a project's Specification" in client.calls[0]["system"]
    w = P.draft_wrapper(pid)
    assert w["draft"]["fields"]["4_2"]["status"] == "not_known_yet" and w["draft"]["mission_profile_status"] == "not_known_yet"
    out = _chat(env, "ok", place=place).json()
    assert "submitted" not in out                                                      # the model only asked
    out = _chat(env, "yes, submit", place=place).json()
    assert out["submitted"]["version"] == 1 and "sent to MOTRES engineering" in out["reply"]
    assert store.load_json(pid, "spec/v1.json", None)["version"] == 1


def test_submit_without_the_minimum_is_refused_with_the_missing_fields(env):
    pid = _project_with_values(env)
    env["use"](FakeClient(["Submitting.\n[[SUBMIT_SPEC]]"]))
    out = _chat(env, "submit", place={"project_id": pid}).json()
    assert "submitted" not in out and "Not submitted yet - still missing" in out["reply"]


# -- 4. the web lookup (a propeller not in the catalogue) ----------------------

def test_a_prop_not_in_the_catalogue_is_looked_up_on_the_web_and_filled_with_its_url(env):
    url = "https://www.apcprop.com/files/PER3_22x10E.dat"
    blocks = [NS(type="server_tool_use", name="web_search", input={"query": "APC 22x10E performance data"}),
              NS(type="web_search_tool_result", content=[NS(url=url, title="APC 22x10E performance")]),
              NS(type="text", text="From APC's published table (" + url + "): max 6000 rpm, 3.1 N·m.\n"
                 '[[SPEC_PROPOSAL: field="key_points"; value=[{"name":"Max","torque_Nm":3.1,"speed_rpm":6000,"duration_s":60}]; '
                 f'reason="derived from APC data, {url}"; basis="derived"]]\n'
                 '[[SPEC_PROPOSAL: field="1_6"; value={"maximum_rpm":6000}; reason="derived from APC data, ' + url + '"; basis="derived"]]\n'
                 "[[CREATE_SPEC]]")]
    client = env["use"](FakeClient(blocks))
    out = _chat(env, "APC 22x10E at 6S - make me a specification").json()
    assert client.calls[0]["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    assert "NOT in our propeller catalogue" in client.calls[0]["system"]
    assert out["searches"] == ["APC 22x10E performance data"] and out["sources"][0]["url"] == url
    pid = out["project"]["id"]
    fills = P.draft_wrapper(pid)["draft"]["provenance"]["assistant_fills"]
    assert fills["key_points"]["basis"] == "derived" and fills["key_points"]["sources"] == [url]
    rec = [r for r in AR.iter_records(context="help") if r.get("searches")][0]
    assert rec["sources"][0]["url"] == url and rec["time"]
    assert support.web_search_budget(CUST) == 3                                        # one search of 20 used
    support.charge_web_search(CUST, 20)
    assert support.web_search_budget(CUST) == 0                                        # the daily cap


def test_the_dynamic_web_tool_is_used_on_models_that_have_it():
    assert support.web_search_tool("claude-sonnet-5-5", 2)["type"] == "web_search_20260209"
    assert support.web_search_tool("claude-haiku-5-5", 2)["type"] == "web_search_20250305"
