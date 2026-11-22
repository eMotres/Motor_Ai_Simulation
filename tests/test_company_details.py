"""The project's Company details form (owner 2026-11-22): who reads / edits, validation, the audit, the confirm stamp, prefill,
explicit copy between projects, the placeholders and the shipping default.  Temporary project store; neutral made-up data."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient

from motor_ai_sim import auth
from motor_ai_sim import company_form as CF
from motor_ai_sim.development import company_routes as CR
from motor_ai_sim.development import cost_estimate as CE
from motor_ai_sim.development import store

USERS = {
    "admin": {"email": "admin@example.test", "role": "admin"},
    "owner": {"email": "customer@example.test", "role": "user"},
    "owner2": {"email": "second@example.test", "role": "user"},
    "member-eng": {"email": "eng@example.test", "role": "user"},          # project role "engineer", not MOTRES staff
    "viewer": {"email": "viewer@example.test", "role": "user"},           # project role "customer"
    "staff": {"email": "staff@example.test", "role": "engineer"},         # global engineer, member of nothing
    "other": {"email": "other@example.test", "role": "user"},             # owner of beta only
    "stranger": {"email": "stranger@example.test", "role": "user"},
}
FORM = {"legal_name": "Example Drives GmbH", "country": "de", "billing_email": "Billing@Example.test",
        "reg_address": "1 Test Street, Berlin", "signatory_name": "A. Person", "delivery_city": "Hamburg"}


def _h(who):
    return {"Authorization": "Bearer " + who} if who else {}


def _user(authorization):
    return USERS.get(str(authorization or "").split(" ", 1)[-1].strip())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(tmp_path / "legal"))
    monkeypatch.setattr(auth, "resolve_user", lambda authorization, **_k: _user(authorization))
    base = {"customer_org": "Org", "status": "quote", "created_at": "t", "updated_at": "t", "owner_engineer": "eng@example.test",
            "linked_motors": [], "notes": "", "pending_invites": []}
    store.create_project({**base, "id": "alpha", "title": "Alpha",
                          "members": [{"email": "customer@example.test", "role": "owner"}, {"email": "second@example.test", "role": "owner"},
                                      {"email": "eng@example.test", "role": "engineer"}, {"email": "viewer@example.test", "role": "customer"}]})
    store.create_project({**base, "id": "beta", "title": "Beta", "members": [{"email": "customer@example.test", "role": "owner"}]})
    store.create_project({**base, "id": "gamma", "title": "Gamma", "members": [{"email": "other@example.test", "role": "owner"}]})
    app = FastAPI()
    app.include_router(CR.router)
    return TestClient(app)


def _put(client, who, values, pid="alpha"):
    return client.put(f"/api/projects/{pid}/company-details", headers=_h(who), json={"values": values})


def test_owner_second_owner_staff_and_admin_read_and_edit(client):
    for who in ("owner", "owner2", "staff", "admin"):
        assert client.get("/api/projects/alpha/company-details", headers=_h(who)).status_code == 200, who
    r = _put(client, "owner2", FORM)
    assert r.status_code == 200
    got = client.get("/api/projects/alpha/company-details", headers=_h("owner")).json()
    assert got["values"]["country"] == "DE" and got["values"]["billing_email"] == "billing@example.test"
    assert _put(client, "staff", {"legal_form": "GmbH"}).status_code == 200
    assert _put(client, "admin", {"vat_no": "DE 123456789"}).status_code == 200
    assert client.get("/api/projects/alpha/company-details", headers=_h("owner")).json()["values"]["legal_form"] == "GmbH"


def test_other_members_and_outsiders_get_the_unknown_project_answer(client):
    _put(client, "owner", FORM)
    unknown = client.get("/api/projects/nope/company-details", headers=_h("admin"))
    assert unknown.status_code == 404
    for who in ("member-eng", "viewer", "other", "stranger"):
        for call in (lambda: client.get("/api/projects/alpha/company-details", headers=_h(who)),
                     lambda: _put(client, who, {"legal_form": "X"}),
                     lambda: client.post("/api/projects/alpha/company-details/confirm", headers=_h(who)),
                     lambda: client.get("/api/projects/alpha/company-details/sources", headers=_h(who)),
                     lambda: client.post("/api/projects/alpha/company-details/copy-from", headers=_h(who), json={"project_id": "beta"})):
            r = call()
            assert r.status_code == 404 and r.json()["detail"] == "Company details not found", who
            assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/projects/alpha/company-details").status_code == 401


def test_an_mcp_agent_is_refused_even_for_an_owner_or_admin(client):
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    for who in ("admin", "owner"):
        with auth.use_agent_user(USERS[who]):
            with pytest.raises(HTTPException) as exc:
                CR.get_company_details("alpha", request, Response())
        assert exc.value.status_code == 403
    from motor_ai_sim import mcp_tools as T
    for key in ("company_details", "company_placeholders"):
        assert T.commercial_key(key)


def test_validation_and_required_fields(client):
    assert _put(client, "owner", {"legal_name": "X"}).status_code == 422                   # country and billing e-mail missing
    assert _put(client, "owner", {**FORM, "country": "Germany"}).status_code == 422
    assert _put(client, "owner", {**FORM, "billing_email": "not-an-email"}).status_code == 422
    assert _put(client, "owner", {**FORM, "eori": "12"}).status_code == 422
    assert _put(client, "owner", {**FORM, "unknown_field": "x"}).status_code == 422
    assert _put(client, "owner", {**FORM, "carrier_contact_phone": "call me"}).status_code == 422
    assert _put(client, "owner", {**FORM, "eori": "de 1234567", "carrier_contact_phone": "+49 40 123"}).status_code == 200
    assert client.get("/api/projects/alpha/company-details", headers=_h("owner")).json()["values"]["eori"] == "DE1234567"
    assert _put(client, "owner", {"legal_name": None}).status_code == 422                  # a required field cannot be emptied


def test_changes_are_audited_and_completion_once(client):
    _put(client, "owner", FORM)
    _put(client, "owner", {"legal_form": "GmbH"})
    _put(client, "owner", {"legal_form": "GmbH"})                                          # no change: no entry
    rows = [json.loads(l) for l in (store.path_for("alpha") / "audit.jsonl").read_text().splitlines() if l.strip()]
    company = [r for r in rows if r["entity"] == "company"]
    assert [r["action"] for r in company] == ["company.update", "company.completed", "company.update"]
    assert company[0]["actor_email"] == "customer@example.test"
    assert company[2]["details"]["changes"]["legal_form"] == {"old": None, "new": "GmbH"}


def test_confirm_stamp_set_by_owner_and_cleared_by_any_edit(client):
    r = client.post("/api/projects/alpha/company-details/confirm", headers=_h("owner"))
    assert r.status_code == 422                                                            # nothing filled yet
    _put(client, "owner", FORM)
    assert client.post("/api/projects/alpha/company-details/confirm", headers=_h("staff")).status_code == 403
    done = client.post("/api/projects/alpha/company-details/confirm", headers=_h("owner2")).json()
    assert done["confirmed"]["by"] == "second@example.test" and len(done["confirmed"]["at"]) >= 10
    assert CR.company_placeholders(store.get_project("alpha"))["company_confirmed_by"] == "second@example.test"
    after = _put(client, "staff", {"preferred_carrier": "Any"}).json()
    assert after["confirmed"] is None
    assert client.post("/api/projects/alpha/company-details/confirm", headers=_h("admin")).status_code == 200


def test_prefill_from_the_nda_record_for_the_owner_only(client, tmp_path):
    legal = tmp_path / "legal"
    legal.mkdir()
    (legal / "nda_signatures.jsonl").write_text(json.dumps({
        "id": "n1", "email": "customer@example.test", "company": "Example Drives GmbH", "reg_no": "HRB 1", "country": "DE",
        "address": "1 Test Street", "signatory": "A. Person", "title": "CEO"}) + "\n")
    got = client.get("/api/projects/alpha/company-details", headers=_h("owner")).json()
    assert got["values"] == {} and got["prefill"]["legal_name"] == "Example Drives GmbH"
    assert got["prefill"]["signatory_title"] == "CEO" and got["prefill"]["signatory_email"] == "customer@example.test"
    assert "prefill" not in client.get("/api/projects/alpha/company-details", headers=_h("staff")).json()
    _put(client, "owner", FORM)
    assert "prefill" not in client.get("/api/projects/alpha/company-details", headers=_h("owner")).json()
    assert client.get("/api/projects/beta/company-details", headers=_h("owner")).json()["prefill"]["reg_no"] == "HRB 1"


def test_copy_from_another_project_is_explicit_and_access_checked(client):
    _put(client, "owner", FORM)
    sources = client.get("/api/projects/beta/company-details/sources", headers=_h("owner")).json()["sources"]
    assert [s["project_id"] for s in sources] == ["alpha"]
    assert client.get("/api/projects/beta/company-details", headers=_h("owner")).json()["values"] == {}
    r = client.post("/api/projects/beta/company-details/copy-from", headers=_h("owner"), json={"project_id": "alpha"})
    assert r.status_code == 200 and r.json()["values"]["legal_name"] == "Example Drives GmbH" and r.json()["confirmed"] is None
    # a project the caller has no access to is the unknown-project answer, and its data never moves
    _put(client, "other", {**FORM, "legal_name": "Another Company"}, pid="gamma")
    r = client.post("/api/projects/beta/company-details/copy-from", headers=_h("owner"), json={"project_id": "gamma"})
    assert r.status_code == 404
    assert client.get("/api/projects/beta/company-details", headers=_h("owner")).json()["values"]["legal_name"] == "Example Drives GmbH"
    audit = [json.loads(l) for l in (store.path_for("beta") / "audit.jsonl").read_text().splitlines() if l.strip()]
    assert any(r["details"].get("copied_from") == "alpha" for r in audit if r["entity"] == "company")


def test_placeholders_and_shipping_destination(client):
    _put(client, "owner", FORM)
    ph = CR.company_placeholders(store.get_project("alpha"))
    assert ph["company_legal_name"] == "Example Drives GmbH" and ph["company_delivery_address"] == "1 Test Street, Berlin"
    assert ph["company_delivery_country"] == "DE" and ph["company_confirmed_on"] == ""
    assert set(CR.company_placeholders(store.get_project("beta"))) == set(ph)             # every key, even when empty
    assert CR.shipping_destination("alpha") == {"country": "DE", "city": "Hamburg"}
    assert CR.shipping_destination("beta") is None
    _put(client, "owner", {"delivery_country": "fr", "delivery_city": "Lyon"})
    assert CR.shipping_destination("alpha") == {"country": "FR", "city": "Lyon"}


def test_new_cost_estimate_ships_to_the_delivery_address_but_a_stored_one_is_untouched(client):
    schema = CE.load_schema()
    assert CE.default_estimate(schema, "alpha")["settings"]["ship_to"] is None
    _put(client, "owner", FORM)
    assert CE.default_estimate(schema, "alpha")["settings"]["ship_to"] == {"country": "DE", "city": "Hamburg"}
    assert CE.default_estimate(schema, "beta")["settings"]["ship_to"] is None
    stored = CE.default_estimate(schema, "beta")
    again = CE.from_stored(stored, schema, "beta")
    assert again["settings"]["ship_to"] is None


def test_bank_fields_stay_out_of_the_audit_text():
    form = CF.Form("t", (CF.Field("name", "g"), CF.Field("acct", "g", bank=True)), ("name",), ("name",))
    doc, changes = CF.apply_patch(form, CF.empty_doc(), {"name": "N", "acct": "123456"}, "a@example.test")
    audit = CF.audit_changes(form, changes)
    assert audit["bank_changed"] is True and "123456" not in json.dumps(audit)
    assert "acct" not in CF.public_view(form, doc, include_bank=False)["values"]
