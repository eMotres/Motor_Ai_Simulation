"""Owner decisions 2026-10-31 (D1, D4, D7, D10) on top of the account acceptance of ``legal_texts`` and the agreement of
``platform_agreement``:

* the capacity choice (agreement 2.3) and the optional earlier-data box (7.9) in every acceptance path, 422 ``capacity_required`` /
  ``business_name_required``;
* ``training_eligibility``: the window ``[eligible_from, eligible_until)`` of every branch (company, account agreement, Terms box by
  capacity, historical opt-in, PRIVATE switch, no-training hold, MOTRES's own);
* the allow-list ``dataset_exclusions()["training"]`` (owner D10);
* the ``TERMS_REQUIRED_FROM`` deadline (HTTP 428 ``terms_required``, MCP ``simulate``);
* the account-acceptance record view / PDF without empty company rows (gate R16).

Everything runs on tmp_path; no mail, no solver.
"""
from __future__ import annotations

import datetime
import json
import types

import pytest
from fastapi import HTTPException

from motor_ai_sim import legal_texts as LT
from motor_ai_sim import platform_agreement as PA
from tests.test_legal_texts import (ADMIN, ANNA, BOB, ENG, LONE, OWN, TICKED, _agreement_record, _company, _terms_line, client, env,  # noqa: F401
                                    lines, signup)

FAR = "2099-01-01T00:00:00Z"                     # a line time later than every account creation


def _line(env, email, **extra):
    """A Terms line of the current version at an explicit time (``at`` ISO)."""
    at = extra.pop("at")
    ts = datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    with open(env["tmp"] / "terms_acceptances.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "t20990101-" + "2" * 12, "user": email, "terms_version": "v0.4", "ts": ts, "iso": at, **extra}) + chr(10))


# -- capacity and the earlier-data box -------------------------------------------------------------------------------

def test_signup_needs_a_capacity_and_a_business_name_for_a_business(env):
    base = {"accept_terms": True, "terms_version": LT.TERMS_VERSION, "country": "DE"}
    for extra, code in (({}, "capacity_required"), ({"capacity": ""}, "capacity_required"), ({"capacity": "owner"}, "capacity_required"),
                        ({"capacity": "business"}, "business_name_required"),
                        ({"capacity": "business", "business_name": "   "}, "business_name_required")):
        r = signup({**base, **extra})
        assert r.status_code == 422 and r.json()["code"] == code, (extra, r.json())
    assert env["U"].get_user("new1@example.com") is None and lines(env) == []
    for i, cap in enumerate(("business", "sole_trader", "other")):
        r = signup({**base, "capacity": cap, "business_name": "Motor GmbH", "accept_label": "  I accept   the terms "},
                   email=f"cap{i}@example.com", ip=f"203.0.113.7{i}")
        assert r.status_code == 202, r.text
    by = {r["user"]: r for r in lines(env)}
    assert [by[f"cap{i}@example.com"]["capacity"] for i in range(3)] == ["business", "sole_trader", "other"]
    assert by["cap0@example.com"]["labels"] == {"accept": "I accept the terms"} and by["cap0@example.com"]["historical_opt_in"] is False


def test_a_new_account_never_carries_the_earlier_data_election(env):
    r = signup({**TICKED, "historical_opt_in": True})                        # no such field at sign-up; an old client may still send it
    assert r.status_code == 202
    assert lines(env)[0]["historical_opt_in"] is False


def test_existing_account_dialog_records_capacity_election_and_labels(env):
    h = env["h"](ANNA)
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    c = TestClient(app)
    bad = c.post("/api/legal/accept", json={**TICKED, "capacity": ""}, headers=h)
    assert bad.status_code == 422 and bad.json()["code"] == "capacity_required" and lines(env) == []
    bad = c.post("/api/legal/accept", json={**TICKED, "capacity": "business"}, headers=h)
    assert bad.status_code == 422 and bad.json()["code"] == "business_name_required" and lines(env) == []
    ok = c.post("/api/legal/accept", json={**TICKED, "capacity": "business", "business_name": "Acme", "historical_opt_in": True,
                                           "accept_label": "A", "election_label": "Also use the engineering data my account entered before today"},
                headers=h)
    assert ok.status_code == 200, ok.text
    (rec,) = lines(env)
    assert (rec["source"], rec["capacity"], rec["business_name"], rec["historical_opt_in"]) == ("existing", "business", "Acme", True)
    assert rec["labels"] == {"accept": "A", "election": "Also use the engineering data my account entered before today"}


def test_a_current_line_without_a_capacity_does_not_count_as_accepted(env):
    _terms_line(env, ANNA, "v0.4")
    assert LT.accepted_version(ANNA) is False
    assert env["U"] and client.get("/api/legal/status", headers=env["h"](ANNA)).json()["required"] is True
    _terms_line(env, ANNA, "v0.4", capacity="other")
    assert LT.accepted_version(ANNA) is True


# -- training_eligibility: every branch -------------------------------------------------------------------------------

def test_terms_box_by_capacity(env):
    _terms_line(env, ANNA, "v0.4", capacity="business")
    _terms_line(env, BOB, "v0.4", capacity="sole_trader")
    _terms_line(env, LONE, "v0.4", capacity="other")
    a, b = PA.training_eligibility(ANNA), PA.training_eligibility(BOB)
    assert (a["mode"], a["capacity"], a["scope"], a["historical_opt_in"]) == ("FREE", "business", "account", False)
    assert b["capacity"] == "sole_trader" and "eligible_until" not in a
    assert a["eligible_from"].endswith("Z") and a["eligible_from"] < b["eligible_from"]
    assert PA.training_eligibility(LONE) is None and PA.training_allowed(LONE) is False          # other: fail-closed
    assert PA.training_eligibility("user:" + ANNA) == a and PA.training_allowed(ANNA) is True
    assert PA.training_eligibility("") is None and PA.training_eligibility("company:nope") is None
    _terms_line(env, "late@example.com", "v0.3", capacity="business")                            # before the FREE data use
    assert PA.training_eligibility("late@example.com") is None


def test_the_newest_terms_line_decides_and_the_run_sets_the_start(env):
    _terms_line(env, ANNA, "v0.4", capacity="business")
    first = PA.training_eligibility(ANNA)["eligible_from"]
    _terms_line(env, ANNA, "v0.4", capacity="sole_trader")
    assert PA.training_eligibility(ANNA)["eligible_from"] == first                                # one unbroken run
    _terms_line(env, ANNA, "v0.4", capacity="other")
    assert PA.training_eligibility(ANNA) is None                                                  # the person changed capacity: out
    _terms_line(env, ANNA, "v0.4", capacity="business")
    assert PA.training_eligibility(ANNA)["eligible_from"] > first                                 # a new run starts at the new line


def test_historical_opt_in_reaches_back_to_the_account_creation(env):
    created = PA._created_iso(ANNA)
    assert created and created < FAR
    _line(env, ANNA, at=FAR, capacity="business", historical_opt_in=True)
    _line(env, BOB, at=FAR, capacity="business", historical_opt_in=False)
    a, b = PA.training_eligibility(ANNA), PA.training_eligibility(BOB)
    assert a["eligible_from"] == created and a["historical_opt_in"] is True
    assert b["eligible_from"] == FAR and b["historical_opt_in"] is False
    # an account whose creation time is unknown gets no extension (fail-closed)
    env["mp"].setattr(PA, "_created_iso", lambda e: "")
    assert PA.training_eligibility(ANNA)["eligible_from"] == FAR


def test_account_agreement_free_then_private_closes_the_window_at_the_switch(env):
    _agreement_record(env, "user:" + LONE, "FREE", LONE)
    free_at = PA.training_eligibility(LONE)["eligible_from"]
    assert PA.training_eligibility(LONE)["capacity"] == "business" and PA.training_allowed(LONE) is True
    _agreement_record(env, "user:" + LONE, "PRIVATE", LONE)
    w = PA.training_eligibility(LONE)
    assert w["eligible_from"] == free_at and w["eligible_until"] > free_at                         # data of the FREE time stay eligible
    assert PA.training_allowed(LONE) is False                                                     # nothing new from now on
    _agreement_record(env, "user:" + LONE, "FREE", LONE)                                          # back to FREE: the newest FREE run
    assert "eligible_until" not in PA.training_eligibility(LONE) and PA.training_allowed(LONE) is True
    _agreement_record(env, "user:" + BOB, "PRIVATE", BOB)                                          # never FREE
    assert PA.training_eligibility(BOB) is None


def test_private_after_a_terms_box_keeps_the_earlier_window(env):
    _terms_line(env, ANNA, "v0.4", capacity="business")
    start = PA.training_eligibility(ANNA)["eligible_from"]
    _agreement_record(env, "user:" + ANNA, "PRIVATE", ANNA)
    w = PA.training_eligibility(ANNA)
    assert w["eligible_from"] == start and w["eligible_until"] > start and w["scope"] == "account"
    assert PA.training_allowed(ANNA) is False


def test_company_free_then_private_closes_at_the_switch_and_caps_the_accounts_before_it(env):
    comp = _company(env, "Acme Drones Ltd", "HRB 12345", [ANNA, BOB])
    _terms_line(env, ANNA, "v0.4", capacity="business")
    _agreement_record(env, "company:" + comp["id"], "FREE", ANNA)
    c = PA.training_eligibility(comp["id"])
    assert (c["scope"], c["capacity"], c["mode"]) == ("company", "company", "FREE") and "eligible_until" not in c
    assert PA.training_eligibility(BOB)["capacity"] == "company"                                   # covered by the company acceptance
    _agreement_record(env, "company:" + comp["id"], "PRIVATE", ANNA)
    c2 = PA.training_eligibility(comp["id"])
    assert c2["eligible_from"] == c["eligible_from"] and c2["eligible_until"] > c2["eligible_from"]
    assert PA.training_eligibility(BOB)["eligible_until"] == c2["eligible_until"]
    assert PA.training_allowed(BOB) is False and PA.training_allowed(comp["id"]) is False


def test_company_private_from_the_start_leaves_only_the_accounts_earlier_data(env):
    comp = _company(env, "Bolt Motors", "HRB 777", [ANNA, BOB])
    _terms_line(env, ANNA, "v0.4", capacity="business")
    _agreement_record(env, "company:" + comp["id"], "PRIVATE", ANNA)
    w = PA.training_eligibility(ANNA)
    assert w["scope"] == "account" and w["eligible_until"] > w["eligible_from"]                    # data before the company's switch
    assert PA.training_eligibility(BOB) is None and PA.training_eligibility(comp["id"]) is None   # BOB never accepted FREE


def test_a_hold_closes_the_window_at_the_hold_time_and_a_release_reopens_it(env):
    comp = _company(env, "Acme Drones Ltd", "HRB 12345", [ANNA, BOB])
    _terms_line(env, BOB, "v0.4", capacity="sole_trader")
    _agreement_record(env, "company:" + comp["id"], "FREE", ANNA)
    PA.record_hold("user:" + BOB, kind="hold", actor="admin@example.com", note="mail of 2026-10-31")
    hold = PA.active_holds()["user:" + BOB]
    w = PA.training_eligibility(BOB)
    assert w["eligible_until"] == hold["iso"] and w["eligible_until"] > w["eligible_from"]
    assert "eligible_until" not in PA.training_eligibility(ANNA) and PA.training_allowed(BOB) is False
    PA.record_hold("company:" + comp["id"], kind="hold", actor="admin@example.com")
    assert PA.training_eligibility(ANNA)["eligible_until"] and PA.training_eligibility(comp["id"])["eligible_until"]
    PA.record_hold("user:" + BOB, kind="release", actor="admin@example.com")
    PA.record_hold("company:" + comp["id"], kind="release", actor="admin@example.com")
    released = [json.loads(l) for l in PA.holds_path().read_text(encoding="utf-8").splitlines()][-2]["iso"]
    assert PA.active_holds() == {} and "eligible_until" not in PA.training_eligibility(BOB)
    assert PA.training_eligibility(BOB)["eligible_from"] == released                              # never the interval the hold covered
    # append-only: four lines on file, nothing rewritten
    assert [json.loads(l)["kind"] for l in PA.holds_path().read_text(encoding="utf-8").splitlines()] == ["hold", "hold", "release", "release"]
    # a hold on somebody who was never FREE changes nothing: still not eligible
    PA.record_hold("user:" + LONE, kind="hold", actor="a")
    assert PA.training_eligibility(LONE) is None
    with pytest.raises(ValueError):
        PA.hold_key(email="not-an-address")


def test_motres_own_accounts_keep_their_behaviour(env):
    w = PA.training_eligibility(OWN)
    assert (w["scope"], w["capacity"], w["eligible_from"]) == ("own", "own", PA.EPOCH) and "eligible_until" not in w
    assert PA.training_allowed(OWN) is True and PA.training_allowed("user:" + OWN) is True
    assert PA.training_allowed(ADMIN) is False


# -- the hold endpoints --------------------------------------------------------------------------------------------------

def test_admin_marks_a_private_request_received_and_releases_it(env):
    admin, user = env["h"](ADMIN), env["h"](ANNA)
    _terms_line(env, BOB, "v0.4", capacity="business")
    assert client.post("/api/admin/agreement/holds", json={"email": BOB}).status_code in (401, 403)
    assert client.post("/api/admin/agreement/holds", json={"email": BOB}, headers=user).status_code in (401, 403)
    assert client.post("/api/admin/agreement/holds", json={}, headers=admin).status_code == 422
    assert client.post("/api/admin/agreement/holds", json={"email": BOB, "action": "pause"}, headers=admin).status_code == 422
    assert client.post("/api/admin/agreement/holds", json={"company_id": "c-missing"}, headers=admin).status_code == 404
    (env["tmp"] / "legal").mkdir(exist_ok=True)
    r = client.post("/api/admin/agreement/holds", json={"email": BOB.upper(), "note": "PRIVATE request by mail", "reference": "mail-17"}, headers=admin)
    assert r.status_code == 200 and r.json()["record"]["key"] == "user:" + BOB and r.json()["active"] == ["user:" + BOB]
    assert PA.training_eligibility(BOB)["eligible_until"] == r.json()["record"]["iso"]
    g = client.get("/api/admin/agreement/holds", headers=admin).json()
    assert [h["key"] for h in g["active"]] == ["user:" + BOB] and g["history"][0]["actor"] == ADMIN
    r = client.post("/api/admin/agreement/holds", json={"email": BOB, "action": "release"}, headers=admin)
    assert r.status_code == 200 and r.json()["active"] == [] and "eligible_until" not in PA.training_eligibility(BOB)
    assert PA.training_eligibility(BOB)["eligible_from"] == r.json()["record"]["iso"]


# -- the allow-list (owner D10) ----------------------------------------------------------------------------------------

@pytest.fixture()
def world(env):
    from motor_ai_sim.development import store
    from motor_ai_sim.directory import service as D
    env["mp"].setenv("MOTRES_PROJECTS_ROOT", str(env["tmp"] / "projects"))
    env["mp"].setenv("MOTRES_PRODUCTION_ROOT", str(env["tmp"] / "production"))
    acme = _company(env, "Acme Drones Ltd", "HRB 1", [ANNA, BOB])
    freeco = D.create_company(dict(legal_name="Free Co", reg_no="HRB 2", country="DE", address="Street 2"))
    p = D.create_person(dict(name="fred", emails=["fred@free-co.example"]))
    D.create_affiliation(freeco["id"], p["id"], status="confirmed")
    env["U"].create_user("fred@free-co.example", "password-fred", role="user", name="fred")

    def project(pid, members):
        store.create_project({"id": pid, "title": pid, "status": "spec", "members": [{"email": e, "role": r} for e, r in members],
                              "linked_motors": [], "motor_history": []})
    project("acme-1", [(ANNA, "owner"), (BOB, "customer")])
    project("acme-by-staff", [(ENG, "owner"), (BOB, "customer")])
    project("mixed", [("fred@free-co.example", "owner"), (BOB, "customer")])
    project("motres-only", [(ENG, "owner"), (OWN, "engineer")])
    return dict(env, acme=acme["id"], freeco=freeco["id"], project=project)


def test_allowlist_is_empty_before_anybody_accepts(world):
    t = PA.dataset_exclusions()["training"]
    assert t["version"] == 1 and t["policy"] == "allow-list" and t["generated"].endswith("Z")
    assert (t["accounts"], t["companies"]) == ({}, {})
    assert t["projects"] == {"motres-only": {"eligible_from": PA.EPOCH}}                       # MOTRES's own project; the clients' are absent
    assert t["own"] == {"emails": [OWN], "domains": ["aerostator.com", "emotres.com", "motresres.com"], "company_ids": []}
    assert t["workspaces"] == {PA_ws(OWN): OWN}                                                  # MOTRES's own workspace only


def PA_ws(email):
    from motor_ai_sim.workspace import workspace_id
    return workspace_id(email)


def test_allowlist_shape_for_a_free_company_and_a_sole_trader(world):
    _agreement_record(world, "company:" + world["acme"], "FREE", ANNA)
    _terms_line(world, LONE, "v0.4", capacity="sole_trader")
    _line(world, "fred@free-co.example", at=FAR, capacity="other")                                # other: fail-closed
    out = PA.dataset_exclusions()
    t = out["training"]
    assert set(t) == {"version", "policy", "generated", "accounts", "companies", "workspaces", "projects", "own"}
    comp = t["companies"][world["acme"]]
    assert comp["mode"] == "FREE" and "eligible_until" not in comp
    assert set(t["accounts"]) == {ANNA, BOB, LONE}
    assert t["accounts"][ANNA] == {"eligible_from": comp["eligible_from"], "mode": "FREE", "historical_opt_in": False, "capacity": "company"}
    assert t["accounts"][LONE]["capacity"] == "sole_trader" and "fred@free-co.example" not in t["accounts"]
    assert t["workspaces"] == {PA_ws(e): e for e in (ANNA, BOB, LONE, OWN)}
    # a project: the owner's window; staff-owned: the customer side; a non-eligible owner (Free Co never accepted FREE) keeps it out
    assert t["projects"] == {"acme-1": {"eligible_from": comp["eligible_from"]},
                             "acme-by-staff": {"eligible_from": comp["eligible_from"]},
                             "motres-only": {"eligible_from": PA.EPOCH}}
    assert out["companies"] == out["emails"] == []                                               # the deny-list keys are untouched


def test_allowlist_private_switch_closes_accounts_companies_and_projects(world):
    _agreement_record(world, "company:" + world["acme"], "FREE", ANNA)
    start = PA.dataset_exclusions()["training"]["companies"][world["acme"]]["eligible_from"]
    _agreement_record(world, "company:" + world["acme"], "PRIVATE", ANNA)
    out = PA.dataset_exclusions()
    t = out["training"]
    end = t["companies"][world["acme"]]["eligible_until"]
    assert t["companies"][world["acme"]]["eligible_from"] == start and end > start
    assert t["accounts"][ANNA]["eligible_until"] == end and t["accounts"][BOB]["eligible_until"] == end
    assert t["projects"]["acme-1"] == {"eligible_from": start, "eligible_until": end}
    assert t["projects"]["acme-by-staff"]["eligible_until"] == end
    assert out["companies"] == [world["acme"]] and out["emails"] == sorted([ANNA, BOB])        # the deny-list still names the PRIVATE company
    # a company that was PRIVATE from the start has no window at all
    _agreement_record(world, "company:" + world["freeco"], "PRIVATE", "fred@free-co.example")
    assert world["freeco"] not in PA.dataset_exclusions()["training"]["companies"]


def test_allowlist_hold_closes_at_the_hold_and_a_never_free_private_member_blocks_the_project(world):
    _agreement_record(world, "company:" + world["acme"], "FREE", ANNA)
    start = PA.dataset_exclusions()["training"]["companies"][world["acme"]]["eligible_from"]
    PA.record_hold("user:" + BOB, kind="hold", actor="admin@example.com")
    hold_at = PA.active_holds()["user:" + BOB]["iso"]
    t = PA.dataset_exclusions()["training"]
    assert t["accounts"][BOB]["eligible_until"] == hold_at and "eligible_until" not in t["accounts"][ANNA]
    assert "eligible_until" not in t["companies"][world["acme"]]
    assert t["projects"]["acme-1"] == {"eligible_from": start, "eligible_until": hold_at}        # the member's window closes the project's
    # the intersection of two owners: latest start, earliest end
    world["project"]("two-owners", [(ANNA, "owner"), (LONE, "owner")])
    _agreement_record(world, "user:" + LONE, "FREE", LONE)
    lone_start = PA.training_eligibility(LONE)["eligible_from"]
    _agreement_record(world, "user:" + LONE, "PRIVATE", LONE)
    lone_end = PA.training_eligibility(LONE)["eligible_until"]
    p = PA.dataset_exclusions()["training"]["projects"]["two-owners"]
    assert p == {"eligible_from": max(start, lone_start), "eligible_until": lone_end}
    # the FREE owner of "mixed" with BOB (held, so his window ends at the hold) as a member: the project closes at the hold too
    _agreement_record(world, "company:" + world["freeco"], "FREE", "fred@free-co.example")
    mixed = PA.dataset_exclusions()["training"]["projects"]["mixed"]
    assert mixed["eligible_until"] == hold_at


def test_allowlist_never_free_private_member_blocks_a_free_owners_project(world):
    _agreement_record(world, "company:" + world["freeco"], "FREE", "fred@free-co.example")
    assert "mixed" in PA.dataset_exclusions()["training"]["projects"]                              # BOB accepted nothing: does not block
    _agreement_record(world, "user:" + BOB, "PRIVATE", BOB)
    _agreement_record(world, "company:" + world["acme"], "PRIVATE", ANNA)
    t = PA.dataset_exclusions()["training"]
    assert "mixed" not in t["projects"] and BOB not in t["accounts"]


def test_cli_and_admin_route_carry_the_training_key(world):
    _agreement_record(world, "company:" + world["acme"], "FREE", ANNA)
    r = client.get("/api/admin/dataset/exclusions", headers=world["h"](ADMIN))
    assert r.status_code == 200 and world["acme"] in r.json()["training"]["companies"]
    assert client.get("/api/admin/dataset/exclusions", headers=world["h"](ANNA)).status_code in (401, 403)


# -- the deadline (owner D7) ---------------------------------------------------------------------------------------------

def test_no_gate_without_the_deadline_or_before_it(env):
    assert LT.required_from() is None and LT.gate_detail(ANNA) is None
    env["mp"].setenv("TERMS_REQUIRED_FROM", "2099-01-01")
    assert LT.required_from() == datetime.date(2099, 1, 1) and LT.gate_active() is False and LT.gate_detail(ANNA) is None
    env["mp"].setenv("TERMS_REQUIRED_FROM", "not a date")
    assert LT.required_from() is None and LT.gate_active() is False
    st = client.get("/api/legal/status", headers=env["h"](ANNA)).json()
    assert st["required_from"] is None
    env["mp"].setenv("TERMS_REQUIRED_FROM", "2099-01-01")
    assert client.get("/api/legal/status", headers=env["h"](ANNA)).json()["required_from"] == "2099-01-01"


def test_from_the_deadline_a_compute_job_needs_the_current_terms(env):
    from motor_ai_sim import jobs as J
    from motor_ai_sim import workspace as WSP

    @J.queued("test.terms_gate")
    def start():
        return {"ran": True}

    def call(email, role="user"):
        with WSP.use_caller({"id": email, "role": role}):
            return start()

    env["mp"].setenv("TERMS_REQUIRED_FROM", "2020-01-01")
    with pytest.raises(HTTPException) as e:
        call(ANNA)
    assert e.value.status_code == 428 and e.value.detail["code"] == "terms_required"
    assert e.value.detail["version"] == LT.TERMS_VERSION and e.value.detail["from"] == "2020-01-01"
    # staff and MOTRES's own accounts are never gated; so is an anonymous / process identity and the global switch
    for who, role in ((ENG, "engineer"), (ADMIN, "admin"), (OWN, "user")):
        assert call(who, role)["ran"] is True
    assert call("process")["ran"] is True
    # an account that accepted the current version passes - and after the acceptance the same call repeats
    h = env["h"](ANNA)
    assert client.post("/api/legal/accept", json={**TICKED, "capacity": "business", "business_name": "Acme"}, headers=h).status_code == 200
    assert call(ANNA)["ran"] is True
    env["mp"].setenv("TERMS_ACCEPTANCE_DISABLED", "1")
    assert call(BOB)["ran"] is True


def test_the_gate_applies_to_the_real_workspace_routes_and_nested_runs_are_not_gated_twice(env):
    env["mp"].setenv("TERMS_REQUIRED_FROM", "2020-01-01")
    env["mp"].setenv("WORKSPACES_ROOT", str(env["tmp"] / "workspaces"))          # the server mode: every request resolves its caller
    r = client.post("/api/simulation/cogging/run", json={}, headers=env["h"](BOB))
    assert r.status_code == 428 and r.json()["detail"]["code"] == "terms_required"


def test_mcp_simulate_is_refused_with_a_terms_required_tool_error(env):
    from motor_ai_sim import agent_designs as AD
    env["mp"].setenv("TERMS_REQUIRED_FROM", "2020-01-01")
    with pytest.raises(AD.DesignError) as e:
        AD.simulate(types.SimpleNamespace(email=BOB, client_name="t", credential_id="c", kind="key"), "d-x", "em")
    assert str(e.value).startswith("terms_required:") and LT.TERMS_VERSION in str(e.value) and "2020-01-01" in str(e.value)
    # staff are not gated: the call gets past the gate (and fails later on the unknown draft, not on the terms)
    with pytest.raises(Exception) as e2:
        AD.simulate(types.SimpleNamespace(email=ENG, client_name="t", credential_id="c", kind="key"), "d-x", "em")
    assert not str(e2.value).startswith("terms_required")


# -- the record as shown (R16) ----------------------------------------------------------------------------------------------

def test_record_view_never_shows_empty_company_rows(env):
    assert signup({**TICKED, "capacity": "sole_trader", "country": "DE"}).status_code == 202
    rec = lines(env)[0]
    rows = dict(LT.record_view(rec))
    assert rows["Capacity"].startswith("Itself, as a registered sole trader") and rows["Country of establishment"] == "DE"
    assert rows["Forum (agreement section 13)"] == "Variant A" and "Business name" not in rows          # no name: no empty row
    for banned in ("Company", "Registration no.", "Title", "Mode", "Signatory", "Address"):
        assert banned not in rows
    assert all(v.strip() for v in rows.values())
    rec2 = dict(rec, capacity="business", business_name="Shenzhen Drive Co.", country="CN", forum_variant="B", historical_opt_in=True)
    rows2 = dict(LT.record_view(rec2))
    assert rows2["Business name"] == "Shenzhen Drive Co." and rows2["Forum (agreement section 13)"].startswith("Variant B")
    assert rows2["Earlier data (section 7.9)"].startswith("Included")


def test_own_acceptance_pdf_and_rows_are_served_and_the_admin_can_fetch_any(env):
    h = env["h"](ANNA)
    assert client.get("/api/legal/mine/pdf", headers=h).status_code == 404
    assert client.post("/api/legal/accept", json={**TICKED, "capacity": "business", "business_name": "Acme", "lang": "zh-CN"}, headers=h).status_code == 200
    mine = client.get("/api/legal/mine", headers=h).json()["acceptances"]
    labels = [r["label"] for r in mine[0]["rows"]]
    assert "Capacity" in labels and "Business name" in labels and "Company" not in labels and "Registration no." not in labels
    pdf = client.get("/api/legal/mine/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "no-store" in pdf.headers["cache-control"]
    rid = lines(env)[0]["id"]
    assert client.get(f"/api/admin/legal/{rid}/pdf", headers=h).status_code in (401, 403)
    a = client.get(f"/api/admin/legal/{rid}/pdf", headers=env["h"](ADMIN))
    assert a.status_code == 200 and a.content.startswith(b"%PDF")
    assert client.get("/api/admin/legal/t20000101-000000000000/pdf", headers=env["h"](ADMIN)).status_code == 404
    assert client.get("/api/legal/mine", headers=env["h"](BOB)).json() == {"acceptances": []}
