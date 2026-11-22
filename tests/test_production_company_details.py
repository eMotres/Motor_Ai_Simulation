"""The factory's Company details of a production (owner 2026-11-22): any OEM member of the production fills / edits, staff read and
edit, the customer side never sees it; bank details never reach the audit; explicit copy between productions of one factory."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.development import store as DS
from motor_ai_sim.production import company_routes as CR
from motor_ai_sim.production import store

ADMIN, ENG = "admin@example.com", "engineer@example.com"
OEM1, OEM2, OEM_NDA_LESS = "oem1@example.com", "oem2@example.com", "oem3@example.com"
OTHER_OEM, VIEWER, OWNER = "oem9@example.com", "viewer@example.com", "owner@example.com"
DIE = "CIANO14 40 new"
FORM = {"name_zh": "示例电机有限公司", "name_en": "Example Motors Ltd", "uscc": "91310000ab12cd34ef", "reg_address": "1 Test Road, Shanghai",
        "legal_representative": "Li Test", "contact_person": "Wang Test", "docs_email": "docs@example.test",
        "bank_name": "Test Bank", "bank_account": "6222000011112222", "general_vat_taxpayer": True, "company_seal": False}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth, users as U
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "production").mkdir()
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-company-1234")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    for email, role in ((ENG, "engineer"), (OEM1, "oem"), (OEM2, "oem"), (OEM_NDA_LESS, "oem"), (OTHER_OEM, "oem"),
                        (VIEWER, "user"), (OWNER, "user")):
        U.create_user(email, "password-" + email[:3], role=role, name=email)
    for email in (OEM1, OEM2, OTHER_OEM):
        U.set_nda(email, True, "2026-10-01", by=ADMIN)
    client = TestClient(app)
    head = lambda email: {"Authorization": f"Bearer {U.issue_token(email)}"}          # noqa: E731
    for config, oems in (("L12", (OEM1, OEM2, OEM_NDA_LESS)), ("L20", (OEM1,)), ("L30", (OTHER_OEM,))):
        r = client.post(f"/api/admin/production/{DIE}/{config}", headers=head(ADMIN), json={
            "title": {"en": "P " + config, "zh": "P " + config, "src_lang": "en"}, "order_quantity": 10,
            "members": [{"email": ENG, "role": "engineer"}] + [{"email": e, "role": "oem"} for e in oems]})
        assert r.status_code == 200, r.text
    DS.create_project({"id": "alpha", "title": "Alpha", "customer_org": "Org", "status": "quote", "created_at": "t", "updated_at": "t",
                       "owner_engineer": ENG, "notes": "", "pending_invites": [],
                       "linked_motors": [{"die": DIE, "config": "L12"}],
                       "members": [{"email": OWNER, "role": "owner"}, {"email": VIEWER, "role": "customer"}]})
    return client, head


def _url(config="L12", tail=""):
    return f"/api/production/{DIE}/{config}/company-details{tail}"


def test_any_oem_member_staff_and_admin_read_and_edit(env):
    client, head = env
    assert client.put(_url(), headers=head(OEM1), json={"values": FORM}).status_code == 200
    for who in (OEM1, OEM2, ENG, ADMIN):
        r = client.get(_url(), headers=head(who))
        assert r.status_code == 200 and r.json()["values"]["uscc"] == "91310000AB12CD34EF", who
    assert client.put(_url(), headers=head(OEM2), json={"values": {"contact_wechat": "wx_test"}}).status_code == 200
    assert client.put(_url(), headers=head(ENG), json={"values": {"legal_representative": "Li Other"}}).status_code == 200
    assert client.get(_url(), headers=head(OEM1)).json()["values"]["contact_wechat"] == "wx_test"


def test_customer_side_and_outsiders_get_404_and_agents_403(env):
    client, head = env
    client.put(_url(), headers=head(OEM1), json={"values": FORM})
    for who in (VIEWER, OWNER, OTHER_OEM):                       # the project's viewer / owner (customer side), an OEM of ANOTHER production
        for r in (client.get(_url(), headers=head(who)), client.put(_url(), headers=head(who), json={"values": {"name_en": "X"}}),
                  client.post(_url(tail="/confirm"), headers=head(who)), client.get(_url(tail="/sources"), headers=head(who))):
            assert r.status_code == 404, who
    assert client.get(_url()).status_code == 401
    assert client.get(_url(), headers=head(OEM_NDA_LESS)).status_code == 403            # the NDA gate every factory page has
    from fastapi import HTTPException, Request, Response
    from motor_ai_sim import auth
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""})
    with auth.use_agent_user({"email": ADMIN, "role": "admin"}):
        with pytest.raises(HTTPException) as exc:
            CR.get_company_details(DIE, "L12", req, Response())
    assert exc.value.status_code == 403


def test_validation_names_and_flags(env):
    client, head = env
    assert client.put(_url(), headers=head(OEM1), json={"values": {"contact_person": "x"}}).status_code == 422          # a name is needed
    assert client.put(_url(), headers=head(OEM1), json={"values": {"name_en": "X", "uscc": "123"}}).status_code == 422
    assert client.put(_url(), headers=head(OEM1), json={"values": {"name_en": "X", "docs_email": "nope"}}).status_code == 422
    assert client.put(_url(), headers=head(OEM1), json={"values": {"name_en": "X", "company_seal": "maybe"}}).status_code == 422
    assert client.put(_url(), headers=head(OEM1), json={"values": {"name_zh": "只有中文"}}).status_code == 200


def test_audit_and_logs_never_carry_bank_values(env, caplog):
    client, head = env
    with caplog.at_level("DEBUG"):
        client.put(_url(), headers=head(OEM1), json={"values": FORM})
        client.put(_url(), headers=head(OEM1), json={"values": {"bank_account": "6222999988887777"}})
    text = store.audit_path(DIE, "L12").read_text(encoding="utf-8")
    rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    company = [r for r in rows if r["entity"] == "company"]
    assert [r["action"] for r in company] == ["update", "company.completed", "update"]
    assert company[2]["snapshot"] == {"bank_changed": True, "fields": ["bank_account"]}
    for secret in ("6222000011112222", "6222999988887777", "Test Bank", "docs@example.test"):
        assert secret not in text and secret not in caplog.text
    assert "bank_changed" in text


def test_digest_and_owner_report_lines_carry_no_values(env):
    client, head = env
    client.put(_url(), headers=head(OEM1), json={"values": FORM})
    from motor_ai_sim.digest import collect as C
    from motor_ai_sim.production import access
    rec = access.load_production(DIE, "L12")
    items = C.collect_production(rec, 0.0, 4e9)
    assert not any("Test Bank" in (i.en + i.zh) or "6222" in (i.en + i.zh) or "Example Motors" in (i.en + i.zh) for i in items)


def test_payload_flags_name_for_staff_and_reminder_for_the_factory(env):
    client, head = env
    assert client.get(f"/api/production/{DIE}/L12", headers=head(OEM1)).json()["oem_company_missing"] is True
    staff = client.get(f"/api/production/{DIE}/L12", headers=head(ENG)).json()
    assert "oem_company" not in staff and "oem_company_missing" not in staff
    client.put(_url(), headers=head(OEM1), json={"values": {"name_en": "Example Motors Ltd"}})
    assert client.get(f"/api/production/{DIE}/L12", headers=head(OEM1)).json()["oem_company_missing"] is True      # not complete yet
    client.put(_url(), headers=head(OEM1), json={"values": FORM})
    oem = client.get(f"/api/production/{DIE}/L12", headers=head(OEM1)).json()
    assert "oem_company_missing" not in oem and "oem_company" not in oem                                             # the factory knows its own name
    assert client.get(f"/api/production/{DIE}/L12", headers=head(ENG)).json()["oem_company"] == "Example Motors Ltd"
    assert client.get(f"/api/production/{DIE}/L12", headers=head(ADMIN)).json()["oem_company"] == "Example Motors Ltd"
    viewer = client.get(f"/api/production/{DIE}/L12", headers=head(VIEWER)).json()                                   # derived read-only viewer
    assert "oem_company" not in viewer and "oem_company_missing" not in viewer
    assert "Example Motors" not in json.dumps(viewer) and "Test Bank" not in json.dumps(viewer)


def test_confirm_and_clear_on_edit(env):
    client, head = env
    assert client.post(_url(tail="/confirm"), headers=head(OEM1)).status_code == 422
    client.put(_url(), headers=head(OEM1), json={"values": FORM})
    done = client.post(_url(tail="/confirm"), headers=head(OEM2)).json()
    assert done["confirmed"]["by"] == OEM2
    assert CR.oem_placeholders(DIE, "L12")["oem_confirmed_by"] == OEM2
    assert client.put(_url(), headers=head(OEM1), json={"values": {"contact_wechat": "w"}}).json()["confirmed"] is None


def test_copy_between_productions_of_one_factory_only(env):
    client, head = env
    client.put(_url("L12"), headers=head(OEM1), json={"values": FORM})
    sources = client.get(_url("L20", "/sources"), headers=head(OEM1)).json()["sources"]
    assert [(s["die"], s["config"]) for s in sources] == [(DIE, "L12")]
    assert client.get(_url("L20"), headers=head(OEM1)).json()["values"] == {}
    r = client.post(_url("L20", "/copy-from"), headers=head(OEM1), json={"die": DIE, "config": "L12"})
    assert r.status_code == 200 and r.json()["values"]["name_en"] == "Example Motors Ltd"
    # another factory's production cannot be copied from, and a stranger to L12 sees nothing of it
    client.put(_url("L30"), headers=head(OTHER_OEM), json={"values": {"name_en": "Another Factory"}})
    assert client.post(_url("L20", "/copy-from"), headers=head(OEM1), json={"die": DIE, "config": "L30"}).status_code == 404
    assert client.get(_url("L30", "/sources"), headers=head(OTHER_OEM)).json()["sources"] == []


def test_placeholders_have_every_key_and_yes_no_flags(env):
    client, head = env
    empty = CR.oem_placeholders(DIE, "L20")
    client.put(_url(), headers=head(OEM1), json={"values": FORM})
    ph = CR.oem_placeholders(DIE, "L12")
    assert set(empty) == set(ph) and empty["oem_name_en"] == ""
    assert ph["oem_general_vat_taxpayer"] == "yes" and ph["oem_company_seal"] == "no" and ph["oem_name_zh"] == FORM["name_zh"]
