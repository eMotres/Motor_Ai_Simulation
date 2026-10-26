"""AeroStator Platform Services and Data Agreement click-through (platform_agreement.py, routes/platform_agreement.py).

Runs against throwaway users, a throwaway legal directory, a throwaway company directory and a throwaway projects store.  No
SMTP (nothing is mailed), no solver.  All names are made up.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import nda as N
from motor_ai_sim import platform_agreement as PA
from motor_ai_sim.api import app
from motor_ai_sim.development import store

ADMIN, ENG = "admin@example.com", "eng@example.com"
CUST1, CUST2, LONE, OWN = "ann@acme-drones.example", "bob@acme-drones.example", "solo@solo-works.example", "vadim@motresres.com"
V1, V2 = "v1-2026-11", "v2-2027-01"

TEXT_EN = ("# Platform Agreement\n\nBetween MOTRES d.o.o. and {{company}} (reg. {{reg_no}}, {{country}}, {{address}}).\n\n"
           "Mode {{mode}}. Signed by {{signatory}}, {{title}}, {{email}} on {{date}}. Version {{version}}. Unresolved {{later_fill}}.\n")
TEXT_ZH = "# 平台协议\n\n{{company}}（注册号 {{reg_no}}）与 MOTRES d.o.o. 之间，模式 {{mode}}，签署人 {{signatory}}，{{title}}，版本 {{version}}。\n"
GOOD = dict(mode="FREE", signer="Ann Example", title="CTO", business=True, authority=True, lang="en")

client = TestClient(app)


def install(legal, version, current=True, en=TEXT_EN, zh=TEXT_ZH):
    d = legal / "platform_agreement" / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "platform_agreement_en.md").write_text(en, encoding="utf-8")
    (d / "platform_agreement_zh-CN.md").write_text(zh, encoding="utf-8")
    if current:
        (legal / "platform_agreement" / "current.txt").write_text(version + "\n", encoding="utf-8")


def make_company(name, reg_no, country, address, emails):
    from motor_ai_sim.directory import service as D
    comp = D.create_company(dict(legal_name=name, reg_no=reg_no, country=country, address=address))
    for e in emails:
        person = D.create_person(dict(name=e.split("@")[0], emails=[e]))
        D.create_affiliation(comp["id"], person["id"], status="confirmed")
    return comp


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    legal = tmp_path / "legal"
    legal.mkdir()
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal))
    monkeypatch.setenv("MOTRES_DIRECTORY_ROOT", str(tmp_path / "directory"))
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("PLATFORM_AGREEMENT_DISABLED", raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    PA._CACHE.update(sig=None, rows=[])
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(ENG, "password-eng", role="engineer", name="Eng")
    for e in (CUST1, CUST2, LONE, OWN):
        U.create_user(e, "password-" + e.split("@")[0], role="user", name=e.split("@")[0])
    comp = make_company("Acme Drones Ltd", "HRB 12345", "DE", "Hauptstr. 1, Berlin", [CUST1, CUST2])
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(legal=legal, company=comp, admin=h(ADMIN), eng=h(ENG), c1=h(CUST1), c2=h(CUST2), lone=h(LONE), own=h(OWN), tmp=tmp_path)


def accept(env, who="c1", **over):
    body = {**GOOD, "version": V1, **over}
    return client.post("/api/agreement/accept", json=body, headers=env[who])


def records(env):
    p = env["legal"] / "platform_agreement_acceptances.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def start_project(env, project_id="alpha", owner=CUST1):
    """A project whose owner is a customer account, with a specification that can be submitted."""
    from tests.test_development_core import _all_unknown_draft, make_project
    store.create_project(make_project(project_id, members=[{"email": owner, "role": "owner"}]))
    store.save_json(project_id, "draft.json", {"draft": _all_unknown_draft(), "schema_version": 2, "revision": 0, "version": None})


def submit(env, who="c1", project_id="alpha"):
    return client.post(f"/api/projects/{project_id}/spec/submit", headers=env[who], json={"revision": 0})


# -- the shipped draft is a placeholder: nothing is enforced -----------------------------------------------------------

def test_shipped_draft_is_a_placeholder_and_nothing_is_enforced(env):
    cur = PA.current("en")
    assert cur["version"].startswith("v0-") and cur["placeholder"] is True and cur["installed"] is False
    assert cur["text"].startswith("# DRAFT") and "NOT FOR SIGNATURE" in cur["text"]
    assert "NOT FOR SIGNATURE" in PA.current("zh-CN")["texts"]["zh-CN"] and "{{mode}}" in cur["texts"]["en"]
    assert PA.enforced() is False
    # a user is told nothing; an admin learns the approved text is not installed
    st = client.get("/api/agreement/status", headers=env["c1"]).json()
    assert st == {"enforced": False, "required": False, "accepted": False, "exempt": None}
    adm = client.get("/api/agreement/status", headers=env["admin"]).json()
    assert adm["enforced"] is False and adm["admin"]["installed"] is False and adm["admin"]["placeholder"] is True
    assert client.get("/api/agreement/text", headers=env["c1"]).status_code == 404
    assert client.get("/api/agreement/text", headers=env["admin"]).json()["placeholder"] is True      # admins may read the draft
    assert accept(env).status_code == 409                                                             # cannot accept a draft
    # the gate is off: a customer submits freely, no 428
    start_project(env)
    assert submit(env).status_code == 200
    assert records(env) == []


def test_draft_text_has_no_customer_names_and_keeps_its_placeholders(env):
    for lang in ("en", "zh-CN"):
        text = PA.current(lang)["text"]
        assert "{{company}}" in text and "{{mode}}" in text and "{{signatory}}" in text
    en = PA.current("en")["text"]
    assert "7255691000" in en and "FREE" in en and "PRIVATE" in en        # MOTRES's own company data, both modes


def test_a_v0_name_or_a_marked_text_is_never_enforced(env):
    install(env["legal"], "v0-test")
    assert PA.enforced() is False
    install(env["legal"], "v3-final", en=TEXT_EN + "\nNOT FOR SIGNATURE\n")
    assert PA.enforced() is False
    install(env["legal"], "v3-final")
    assert PA.enforced() is True
    install(env["legal"], "v3-final", zh="# 草案\n\n非签署版本\n")
    assert PA.enforced() is False
    install(env["legal"], "v3-final")
    PA.os.environ["PLATFORM_AGREEMENT_DISABLED"] = "1"
    try:
        assert PA.enforced() is False
    finally:
        del PA.os.environ["PLATFORM_AGREEMENT_DISABLED"]


# -- the installed version: 428, accept, pass ---------------------------------------------------------------------------

def test_text_is_served_in_both_languages_with_matching_hashes(env):
    install(env["legal"], V1)
    r = client.get("/api/agreement/text?lang=zh-CN", headers=env["c1"]).json()
    assert r["version"] == V1 and r["lang"] == "zh-CN" and r["installed"] is True and r["modes"] == ["FREE", "PRIVATE"]
    assert r["texts"] == {"en": TEXT_EN, "zh-CN": TEXT_ZH} and r["text"] == TEXT_ZH
    assert r["sha256"] == N.sha256_text(TEXT_ZH) and r["sha256s"]["en"] == N.sha256_text(TEXT_EN)
    assert client.get("/api/agreement/text?lang=fr", headers=env["c1"]).json()["lang"] == "en"


def test_428_then_accept_then_pass(env):
    install(env["legal"], V1)
    start_project(env)
    gated = submit(env)
    assert gated.status_code == 428
    assert gated.json()["detail"]["code"] == "agreement_required" and gated.json()["detail"]["version"] == V1
    st = client.get("/api/agreement/status", headers=env["c1"]).json()
    assert st["enforced"] and st["required"] and not st["accepted"] and st["scope"] == "company"
    assert st["company"] == {"company": "Acme Drones Ltd", "reg_no": "HRB 12345", "country": "DE", "address": "Hauptstr. 1, Berlin",
                             "known": True, "complete": True}
    r = accept(env)
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "FREE" and r.json()["private_pending_payment"] is False and r.json()["status"]["accepted"] is True
    assert submit(env).status_code == 200                                    # the same action now passes
    assert client.post("/api/agreement/accept", json={**GOOD, "version": V1}, headers=env["c1"]).status_code == 409   # not twice


def test_record_is_append_only_with_hash_ip_time_signer_company_and_a_pdf(env):
    install(env["legal"], V1)
    r = accept(env, mode="PRIVATE", signer="  Ann   Example ", title="CTO").json()
    (rec,) = records(env)
    assert rec["id"] == r["record_id"] and rec["mode"] == "PRIVATE" and rec["version"] == V1 and rec["lang"] == "en"
    assert rec["signatory"] == "Ann Example" and rec["title"] == "CTO" and rec["company"] == "Acme Drones Ltd" and rec["user"] == CUST1
    assert rec["company_id"] == env["company"]["id"] and rec["key"] == "company:" + env["company"]["id"] and rec["scope"] == "company"
    assert rec["business"] is True and rec["authority"] is True
    assert rec["ai_training_allowed"] is False and rec["private_pending_payment"] is True
    assert rec["iso"].endswith("Z") and rec["ip"] and rec["ts"] > 0
    # the hash is of the rendered text (client data, mode, signer, date filled; an unknown placeholder stays visible)
    values = N.template_values({k: rec[k] for k in ("company", "reg_no", "country", "address", "signatory", "title")}, CUST1, rec["iso"][:10], V1) | {"mode": "PRIVATE"}
    shown = N.render(TEXT_EN, values)
    assert rec["text_sha256"] == N.sha256_text(shown) and "{{later_fill}}" in shown and "Mode PRIVATE" in shown
    assert rec["text_sha256s"]["zh-CN"] == N.sha256_text(N.render(TEXT_ZH, values))
    assert rec["template_sha256"] == N.sha256_text(TEXT_EN)
    pdf = PA.pdf_path(rec["id"])
    assert pdf and pdf.read_bytes().startswith(b"%PDF")
    # append-only: a second company's acceptance only adds a line; the first line is byte-identical
    first = (env["legal"] / "platform_agreement_acceptances.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert accept(env, who="lone").status_code in (200, 422)
    lines = (env["legal"] / "platform_agreement_acceptances.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines[0] == first
    # the owner can fetch his copy, an admin any
    assert client.get("/api/agreement/mine/pdf", headers=env["c2"]).content.startswith(b"%PDF")
    assert client.get(f"/api/admin/agreement/{rec['id']}/pdf", headers=env["admin"]).content.startswith(b"%PDF")
    assert client.get(f"/api/admin/agreement/{rec['id']}/pdf", headers=env["c1"]).status_code == 403
    assert client.get("/api/admin/agreement/a20200101-000000000000/pdf", headers=env["admin"]).status_code == 404
    audit = (env["tmp"] / "admin_audit.jsonl").read_text(encoding="utf-8")
    assert "agreement.accept" in audit and "unknown:agreement" not in audit


def test_validation_names_each_problem(env):
    install(env["legal"], V1)
    r = client.post("/api/agreement/accept", json={"version": V1, "mode": "gold", "signer": " ", "title": "", "business": False,
                                                   "authority": "yes"}, headers=env["c1"])
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert set(errs) == {"mode", "signer", "title", "business", "authority"}
    assert records(env) == []
    # lower-case mode is taken, the wrong version is a 409
    assert accept(env, mode="private").json()["mode"] == "PRIVATE"
    assert accept(env, who="lone", version="v0-old").status_code == 409


# -- company coverage ---------------------------------------------------------------------------------------------------

def test_one_acceptance_covers_the_whole_company_but_not_another(env):
    install(env["legal"], V1)
    start_project(env, "a1", CUST1)
    start_project(env, "a2", CUST2)
    assert submit(env, "c1", "a1").status_code == 428 and submit(env, "c2", "a2").status_code == 428
    assert accept(env, "c1").status_code == 200
    assert submit(env, "c2", "a2").status_code == 200                       # a colleague of the same company is covered
    assert client.get("/api/agreement/status", headers=env["c2"]).json()["acceptance"]["signatory"] == "Ann Example"
    start_project(env, "b1", LONE)
    assert submit(env, "lone", "b1").status_code == 428                      # another company is not
    assert accept(env, "c2").status_code == 409                              # already accepted for the company


def test_an_account_without_a_company_accepts_for_itself(env):
    install(env["legal"], V1)
    st = client.get("/api/agreement/status", headers=env["lone"]).json()
    assert st["scope"] == "account" and st["required"] and st["company"]["known"] is False and st["company"]["complete"] is False
    r = accept(env, "lone")                                                  # company data is unknown -> asked for
    assert r.status_code == 422 and set(r.json()["detail"]["errors"]) == {"company", "reg_no", "country", "address"}
    ok = accept(env, "lone", company="Solo Works GmbH", reg_no="HRB 9", country="at", address="Wien 1")
    assert ok.status_code == 200
    (rec,) = records(env)
    assert rec["key"] == "user:" + LONE and rec["scope"] == "account" and rec["country"] == "AT" and rec["company_id"] == ""
    start_project(env, "b1", LONE)
    assert submit(env, "lone", "b1").status_code == 200


def test_company_facts_come_from_the_directory_the_body_cannot_override_them(env):
    install(env["legal"], V1)
    accept(env, "c1", company="Evil Corp", reg_no="X", country="US", address="Nowhere")
    (rec,) = records(env)
    assert rec["company"] == "Acme Drones Ltd" and rec["country"] == "DE" and rec["reg_no"] == "HRB 12345"


def test_a_proposed_affiliation_is_not_a_company(env):
    from motor_ai_sim.directory import service as D
    install(env["legal"], V1)
    person = D.create_person(dict(name="Newbie", emails=[LONE]))
    D.create_affiliation(env["company"]["id"], person["id"], status="proposed")
    assert PA.company_of(LONE) is None and PA.scope_of(LONE)["scope"] == "account"


# -- versions -----------------------------------------------------------------------------------------------------------

def test_a_new_version_requires_a_new_acceptance_the_old_one_stays_valid_for_its_version(env):
    install(env["legal"], V1)
    start_project(env, "a1", CUST1)
    assert accept(env, "c1", mode="PRIVATE").status_code == 200
    assert submit(env, "c1", "a1").status_code == 200
    install(env["legal"], V2)                                                # the owner installs a newer approved text
    start_project(env, "a2", CUST1)
    assert submit(env, "c1", "a2").status_code == 428
    assert client.post("/api/agreement/accept", json={**GOOD, "version": V1}, headers=env["c1"]).status_code == 409      # stale version
    st = client.get("/api/agreement/status", headers=env["c1"]).json()
    assert st["required"] and st["version"] == V2
    assert st["acceptance"]["version"] == V1 and st["acceptance"]["mode"] == "PRIVATE"      # the old acceptance is still on record
    assert PA.training_allowed(CUST1) is False                                              # and still decides the mode meanwhile
    assert accept(env, "c1", version=V2, mode="FREE").status_code == 200
    assert submit(env, "c1", "a2").status_code == 200
    assert [r["version"] for r in records(env)] == [V1, V2]
    assert PA.training_allowed(CUST1) is True                                               # the new acceptance replaces it


# -- training_allowed ---------------------------------------------------------------------------------------------------

def test_training_allowed_follows_the_mode(env):
    install(env["legal"], V1)
    assert PA.training_allowed(CUST1) is False and PA.training_allowed(LONE) is False       # no acceptance: fail-closed
    assert PA.training_allowed("") is False and PA.training_allowed("nobody@x.example") is False
    accept(env, "c1", mode="FREE")
    cid = env["company"]["id"]
    assert PA.training_allowed(CUST1) and PA.training_allowed(CUST2)                         # by e-mail, whole company
    assert PA.training_allowed(cid) and PA.training_allowed("company:" + cid)              # by company id / key
    assert PA.training_allowed(LONE) is False
    assert records(env)[0]["ai_training_allowed"] is True
    make_company("Priv Motors Oy", "FI 1", "FI", "Espoo", ["pia@priv-motors.example"])
    from motor_ai_sim import users as U
    U.create_user("pia@priv-motors.example", "password-pia", role="user", name="Pia")
    h = {"Authorization": f"Bearer {U.issue_token('pia@priv-motors.example')}"}
    assert client.post("/api/agreement/accept", json={**GOOD, "version": V1, "mode": "PRIVATE"}, headers=h).status_code == 200
    assert PA.training_allowed("pia@priv-motors.example") is False
    # MOTRES's own data is its own
    assert PA.training_allowed(OWN) is True and PA.training_allowed(ADMIN) is False          # an arbitrary admin account is not MOTRES's domain


# -- exemptions ---------------------------------------------------------------------------------------------------------

def test_staff_and_motres_are_never_gated_and_reads_are_never_gated(env):
    install(env["legal"], V1)
    for who in ("admin", "eng", "own"):
        st = client.get("/api/agreement/status", headers=env[who]).json()
        assert st["required"] is False and st["exempt"] in ("staff", "motres")
        assert accept(env, who).status_code == 409
    assert PA.required_detail(ADMIN, "admin") is None and PA.required_detail(ENG, "engineer") is None and PA.required_detail(OWN, "user") is None
    # an existing project is read freely by a customer who has not accepted
    start_project(env)
    assert client.get("/api/projects/alpha", headers=env["c1"]).status_code == 200
    assert client.get("/api/projects", headers=env["c1"]).status_code == 200
    assert client.get("/api/projects/alpha/spec", headers=env["c1"]).status_code == 200
    # a staff engineer creates a project for a customer without any acceptance
    created = client.post("/api/projects", headers=env["eng"], json={"id": "gamma", "title": "G", "customer_org": "Acme"})
    assert created.status_code == 200


def test_the_motres_company_in_the_directory_is_exempt(env):
    from motor_ai_sim import users as U
    install(env["legal"], V1)
    make_company("MOTRES d.o.o.", "7255691000", "SI", "Ljubljana", ["tech@partner-mail.example"])
    U.create_user("tech@partner-mail.example", "password-tech", role="user", name="Tech")
    assert PA.scope_of("tech@partner-mail.example")["exempt"] == "motres"
    assert PA.required_detail("tech@partner-mail.example", "user") is None
    assert PA.training_allowed("tech@partner-mail.example") is True


# -- the other starting points ------------------------------------------------------------------------------------------

def test_start_design_is_gated_for_a_company_that_has_not_accepted(env):
    from motor_ai_sim import agent_designs as AD
    from motor_ai_sim.agent_keys import Principal
    install(env["legal"], V1)
    p = Principal(email=CUST1, credential_id="k", kind="api_key", scopes=("designs:write",), client_name="t")
    with pytest.raises(AD.DesignError, match="agreement_required"):
        AD.start_design(p, {"torque_nm": 5})
    accept(env, "c1")
    try:
        AD.start_design(p, {"torque_nm": 5})          # may fail for other reasons (no catalogue in the test), never for the agreement
    except Exception as e:                           # noqa: BLE001
        assert "agreement_required" not in str(e)
    staff = Principal(email=ENG, credential_id="k", kind="api_key", scopes=("designs:write",), client_name="t")
    try:
        AD.start_design(staff, {"torque_nm": 5})
    except Exception as e:                           # noqa: BLE001
        assert "agreement_required" not in str(e)


# -- admin --------------------------------------------------------------------------------------------------------------

def test_admin_lists_acceptances_and_the_company_row_carries_the_agreement(env):
    install(env["legal"], V1)
    accept(env, "c1", mode="PRIVATE")
    r = client.get("/api/admin/agreement/acceptances", headers=env["admin"]).json()
    assert r["enforced"] is True and r["current_version"] == V1 and len(r["acceptances"]) == 1
    row = r["acceptances"][0]
    assert row["mode"] == "PRIVATE" and row["private_pending_payment"] is True and row["company"] == "Acme Drones Ltd"
    assert "user_agent" not in row
    assert client.get("/api/admin/agreement/acceptances", headers=env["c1"]).status_code == 403
    from motor_ai_sim.directory import service as D
    comp = D.get_company(env["company"]["id"])
    assert comp["agreement"]["mode"] == "PRIVATE" and comp["agreement"]["version"] == V1
    other = make_company("Other Ltd", "R2", "DE", "Köln", [])
    assert D.get_company(other["id"])["agreement"] is None
