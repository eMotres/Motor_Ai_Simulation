"""Applicant verification, end to end (routes + store + service) against a throwaway registry, a throwaway legal directory, a fake
CAD tree and a FAKE network: the gate (no STEP without VERIFIED), the intake on the NDA form, the automatic decision, the token reply
page (corporate e-mail with a one-time code, links, one document), the administrator's queue and actions, the audit trail.

No SMTP (mail is captured), no FreeCAD, no real network, no background threads.
"""
from __future__ import annotations

import io
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth_email as E
from motor_ai_sim import model3d as M
from motor_ai_sim.api import app
from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import service as SV
from motor_ai_sim.verification import store as ST
from tests._verification_fakes import FakeHttp
from tests.test_nda import CFG, DIE, GOOD, MID, STEP, install

ADMIN = "admin@example.com"
CORP = "erika@acme-drones.com"          # an account on the company's own domain
GMAIL = "erika.m@gmail.com"
FLAGGED = "flagged@example.com"          # NDA flag set by an administrator (grandfathered)
NET_OK = {
    "https://api.gleif.org/api/v1/lei-records": {"data": [{"id": "5299000TESTLEI000000", "attributes": {"entity": {
        "legalName": {"name": "Acme Drones Ltd"}, "legalAddress": {"country": "DE", "city": "Berlin"}, "registeredAs": "HRB 12345",
        "jurisdiction": "DE", "status": "ACTIVE", "registeredAt": {"id": "RA000242"}}}}]},
    "https://www.acme-drones.com/": "<html><title>Acme Drones</title><body>Acme Drones Ltd, Berlin. HRB 12345</body></html>",
    "https://www.acme-drones.com/team": "<html><body>Our team: Erika Mustermann, CTO</body></html>",
}
client = TestClient(app, follow_redirects=False)
INTAKE = dict(website="www.acme-drones.com", purpose="evaluating the 40 mm motor for a drone", vat_no="")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    legal = tmp_path / "legal"
    legal.mkdir()
    install(legal, "v1-2026-10")
    cad = tmp_path / "cad" / DIE / CFG
    cad.mkdir(parents=True)
    (cad / (MID + "_full.glb")).write_bytes(b"glb")
    (cad / (MID + "_active.step")).write_bytes(STEP)
    (cad / "manifest.json").write_text(json.dumps({"motor_id": MID, "files": {
        MID + "_full.glb": {"sha256": "0" * 64}, MID + "_active.step": {"sha256": M.sha256_bytes(STEP)}}}))
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal))
    monkeypatch.delenv("MOTRES_VERIFICATION_ROOT", raising=False)
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    E.reset_limits()
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(CORP, "password-corp", role="user", name="Erika Mustermann")
    U.create_user(GMAIL, "password-gmail", role="user", name="Erika M")
    U.create_user(FLAGGED, "password-flag", role="user", name="Flagged")
    for e in (CORP, GMAIL, FLAGGED):
        U.set_motor_grants(e, all_motors=False, dies=[DIE])
    U.set_nda(FLAGGED, True, "2026-10-01", by=ADMIN)
    sent = []
    monkeypatch.setattr(E, "send", lambda to, subject, body, **kw: sent.append(dict(to=to, subject=subject, body=body)) or True)
    monkeypatch.setattr(E, "smtp_configured", lambda: True)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(admin=h(ADMIN), corp=h(CORP), gmail=h(GMAIL), flagged=h(FLAGGED), mail=sent, legal=legal, url=f"/api/model3d/{DIE}/{CFG}",
                audit=tmp_path / "admin_audit.jsonl", log=tmp_path / "cad_downloads.jsonl")


def network(routes):
    NET.set_http(FakeHttp(routes))


def sign(env, who="corp", **over):
    return client.post("/api/nda/sign", json={**GOOD, **INTAKE, "version": "v1-2026-10", **over}, headers=env[who])


def case_of(user):
    return ST.latest_of(user)


def step(env, who):
    return client.get(env["url"] + "/model.step", headers=env[who])


def audit(env):
    p = env["audit"]
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def token_of(case):
    return SV.make_token(case["id"])


# -- the gate -------------------------------------------------------------------------------------------------------------------

def test_signing_the_nda_alone_does_not_open_the_step_or_the_tools(env):
    r = sign(env)
    assert r.status_code == 200 and r.json()["can_download"] is False
    assert step(env, "corp").status_code == 403 and "verification" in step(env, "corp").json()["detail"]
    models = client.get("/api/model3d/models", headers=env["corp"]).json()
    assert models["can_download"] is False and models["access"]["state"] in ("in_progress", "need_info") and models["access"]["nda_signed"] is True
    assert client.get(env["url"] + "/parts", headers=env["corp"]).status_code == 403
    assert client.get(env["url"] + "/bom.csv", headers=env["corp"]).status_code == 403
    assert client.get(env["url"] + "/model.glb", headers=env["corp"]).status_code == 403              # owner 2026-10-15: the FULL model goes with the gate
    assert client.get(env["url"] + "/model.glb", headers=env["corp"]).json()["detail"]["code"] == "nda_required"
    assert M.read_log(10) == []


def test_admins_and_admin_flagged_accounts_are_not_gated(env):
    assert step(env, "admin").status_code == 200
    assert step(env, "flagged").status_code == 200
    assert client.get("/api/model3d/models", headers=env["flagged"]).json()["access"]["state"] == "ok"


def test_no_nda_at_all_says_so(env):
    m = client.get("/api/model3d/models", headers=env["gmail"]).json()
    assert m["can_download"] is False and m["access"]["state"] == "nda"
    assert "NDA" in step(env, "gmail").json()["detail"]


def test_an_unreadable_store_closes_the_gate(env, monkeypatch):
    sign(env)
    case = case_of(CORP)
    client.post(f"/api/admin/verification/{case['id']}/approve", json={"reason": "known customer"}, headers=env["admin"])
    assert step(env, "corp").status_code == 200
    monkeypatch.setattr(ST, "cases_of", lambda u: (_ for _ in ()).throw(OSError("disk")))
    assert step(env, "corp").status_code == 403


# -- the intake on the NDA form -----------------------------------------------------------------------------------------------------

def test_the_nda_form_collects_website_purpose_and_work_email(env):
    r = sign(env, work_email="erika@acme-drones.com", vat_no="DE 811 115 368")
    assert r.status_code == 200
    a = case_of(CORP)["application"]
    assert a["legal_name"] == "Acme Drones Ltd" and a["applicant_name"] == "Erika Mustermann" and a["title"] == "CTO"
    assert a["website"] == "https://www.acme-drones.com" and a["purpose"].startswith("evaluating") and a["vat_no"] == "DE 811 115 368"
    assert a["work_email"] == CORP and a["reg_no"] == "HRB 12345" and a["country"] == "DE"
    c = case_of(CORP)
    assert c["nda"]["version"] == "v1-2026-10" and c["nda"]["id"] == r.json()["record_id"] and c["nda"]["text_sha256"]


def test_the_work_email_defaults_to_the_account_email_and_bad_values_are_refused(env):
    sign(env, website="", purpose="")
    assert case_of(CORP)["application"]["work_email"] == CORP
    bad = sign(env, who="gmail", website="http://10.0.0.1/", work_email="nope")
    assert bad.status_code == 422 and set(bad.json()["detail"]["errors"]) == {"website", "work_email"}
    assert ST.cases_of(GMAIL) == []


def test_missing_website_is_a_question_not_a_refusal(env):
    sign(env, website="", purpose="")
    c = case_of(CORP)
    assert c["status"] == ENG.MORE_INFORMATION_REQUIRED and ENG.REQ_WEBSITE in [r["code"] for r in c["requests"]]
    mails = [m for m in env["mail"] if m["to"] == CORP]
    assert mails and "one more thing" in mails[-1]["subject"] and "/api/verification/reply/" in mails[-1]["body"]
    assert "passport" in mails[-1]["body"].lower()


# -- the automatic decision ------------------------------------------------------------------------------------------------------------

def test_a_registry_confirmed_company_with_a_company_email_and_a_listing_is_verified_automatically(env):
    network(NET_OK)
    r = sign(env)
    c = case_of(CORP)
    assert c["status"] == ENG.VERIFIED and c["access"]["granted"] and c["status_actor"] == "system"
    assert r.json()["access"]["state"] == "ok"
    d = c["decision"]
    assert d["company"]["by"] == ["gleif"] and len(d["signals"]) == 2 and all(s["strength"] == "strong" for s in d["signals"])
    assert step(env, "corp").status_code == 200
    assert any("access is open" in m["subject"] for m in env["mail"])


def test_a_gmail_account_gets_a_specific_question_and_no_access(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = case_of(GMAIL)
    assert c["status"] == ENG.MORE_INFORMATION_REQUIRED
    assert ENG.REQ_CORPORATE_EMAIL in [r["code"] for r in c["requests"]]
    assert step(env, "gmail").status_code == 403
    body = [m for m in env["mail"] if m["to"] == GMAIL][-1]["body"]
    assert "corporate e-mail address on acme-drones.com" in body


def test_a_conflict_goes_to_manual_review_and_the_admin_is_told(env):
    network({**NET_OK, "https://api.gleif.org/api/v1/lei-records": {"data": [{"id": "X", "attributes": {"entity": {
        "legalName": {"name": "Acme Drones Ltd"}, "legalAddress": {"country": "FR"}, "jurisdiction": "FR", "status": "ACTIVE"}}}]}})
    sign(env)
    c = case_of(CORP)
    assert c["status"] == ENG.MANUAL_REVIEW and c["reasons"][0]["code"] == "conflict" and not c["access"]["granted"]
    assert any(m["to"] == ADMIN and "manual review" in m["subject"] for m in env["mail"])
    assert any(m["to"] == CORP and "reviewed" in m["subject"] for m in env["mail"])


def test_offline_registries_never_pass_a_case(env):
    sign(env)                                           # the autouse fixture's offline network: every lookup is "unknown"
    c = case_of(CORP)
    assert c["status"] != ENG.VERIFIED and not c["access"]["granted"]
    assert not any(e["result"] == "pass" and e["kind"] == "company" for e in c["evidence"])


def test_a_crashing_source_sends_the_case_to_a_person(env):
    from motor_ai_sim.verification import sources as S

    class Boom(S.VerificationSource):
        name = "boom"

        def run(self, c, x):
            raise RuntimeError("bug")

    S.register(Boom())
    try:
        network(NET_OK)
        sign(env)
    finally:
        S.unregister("boom")
    c = case_of(CORP)
    assert c["status"] == ENG.MANUAL_REVIEW and c["reasons"][0]["code"] == "exception"


# -- the reply page ---------------------------------------------------------------------------------------------------------------------

def test_the_reply_page_works_without_javascript_and_shows_what_is_asked(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = case_of(GMAIL)
    r = client.get(f"/api/verification/reply/{token_of(c)}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "<script" not in r.text.lower() and "corporate e-mail address on acme-drones.com" in r.text
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    zh = client.get(f"/api/verification/reply/{token_of(c)}?lang=zh").text
    assert "公司邮箱" in zh and 'lang="zh-CN"' in zh
    assert GMAIL not in client.get(f"/api/verification/reply/{token_of(c)}").text.replace("Acme", "")      # the address is not echoed in the page


def test_the_reply_page_is_reachable_without_a_session_on_an_enforcing_server_but_nothing_else_is(env, monkeypatch):
    from motor_ai_sim import auth
    sign(env)
    c = case_of(CORP)
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    assert client.get(f"/api/verification/reply/{token_of(c)}").status_code == 200
    assert client.post(f"/api/verification/reply/{token_of(c)}", data={"action": "reply", "note": "hello"}).status_code == 200
    assert client.get("/api/verification/mine").status_code == 401
    assert client.get("/api/admin/verification").status_code == 401
    assert client.get(f"/api/admin/verification/{c['id']}/documents/dxx").status_code == 401


def test_a_bad_or_expired_token_is_a_404_page_and_is_rate_limited(env):
    assert client.get("/api/verification/reply/garbage").status_code == 404
    sign(env)
    c = case_of(CORP)
    expired = SV.make_token(c["id"], ttl=-10)
    assert client.get(f"/api/verification/reply/{expired}").status_code == 404
    tampered = token_of(c)[:-2] + "xx"
    assert client.get(f"/api/verification/reply/{tampered}").status_code == 404
    for _ in range(40):
        client.get("/api/verification/reply/garbage")
    assert client.get("/api/verification/reply/garbage").status_code == 429


def _code_from_mail(env, to):
    m = [x for x in env["mail"] if x["to"] == to and "confirmation code" in x["subject"]][-1]
    return re.search(r"code is (\d{6})", m["body"]).group(1)


def test_linked_work_email_is_requested_and_otp_is_sent_to_that_applicant_address(env):
    applicant = "erika@motresres.com"
    site = "https://emotres.com"
    routes = {
        "https://api.gleif.org/api/v1/lei-records": {"data": [{"id": "5299000TESTLEI000000", "attributes": {"entity": {
            "legalName": {"name": "Acme Drones Ltd"}, "legalAddress": {"country": "DE", "city": "Berlin"},
            "registeredAs": "HRB 12345", "jurisdiction": "DE", "status": "ACTIVE", "registeredAt": {"id": "RA000242"}}}}]},
        site + "/": "<html><body>Acme Drones Ltd, HRB 12345. Contact: owner@motresres.com.</body></html>",
    }
    network(routes)
    NET.set_mx(lambda domain: [(10, "aspmx.l.google.com")] if domain in ("motresres.com", "emotres.com") else None)
    sign(env, who="corp", website=site, work_email=applicant)
    c = case_of(CORP)
    req = next(r for r in c["requests"] if r["code"] == "corporate_email")
    assert req["params"]["address"] == applicant and req["params"]["linked"] is True
    initial_decision = [e for e in ST.events_of(c["id"]) if e["type"] == "check_run"][-1]["data"]["decision"]
    assert not any(s["family"] == "email" for s in initial_decision["signals"])
    tok = token_of(c)
    page = client.get(f"/api/verification/reply/{tok}").text
    assert applicant in page and f'value="{applicant}"' in page
    assert "linked to the company website domain emotres.com" in page

    r = client.post(f"/api/verification/reply/{tok}", data={"action": "add_email", "email": applicant})
    assert f"We sent a code to {applicant}" in r.text
    assert any(mail["to"] == applicant and "confirmation code" in mail["subject"] for mail in env["mail"])
    assert not any(mail["to"] == "owner@emotres.com" and "confirmation code" in mail["subject"] for mail in env["mail"])
    code = _code_from_mail(env, applicant)
    ok = client.post(f"/api/verification/reply/{tok}", data={"action": "confirm_email", "email": applicant, "code": code})
    assert "is confirmed" in ok.text
    c = case_of(CORP)
    linked = next(e for e in c["evidence"] if e["check"] == "domain.match_website" and e["value"] == applicant)
    assert linked["result"] == "pass" and linked["strength"] == "none"
    assert linked["source_url"] == site + "/"
    assert linked["detail"]["how"] == "matching_mx_plus_website_mention"
    assert any(p["type"] == "visible_domain_text" and p["matched"] == "motresres.com" for p in linked["detail"]["evidence"]["proofs"])
    decision = [e for e in ST.events_of(c["id"]) if e["type"] == "check_run"][-1]["data"]["decision"]
    assert [s["family"] for s in decision["signals"]].count("email") == 1
    assert not any(r["code"] == ENG.REQ_DOMAIN_MISMATCH for r in decision["requests"])
    assert c["status"] == ENG.MORE_INFORMATION_REQUIRED and step(env, "corp").status_code == 403


def test_corporate_email_with_a_one_time_code_then_a_link_verifies_the_applicant(env):
    network(NET_OK)
    sign(env, who="gmail", signatory="Erika Mustermann", typed_name="Erika Mustermann")
    c = case_of(GMAIL)
    tok = token_of(c)
    url = f"/api/verification/reply/{tok}"
    # a free mailbox is refused, a corporate address gets a code
    assert "free or disposable" in client.post(url, data={"action": "add_email", "email": "x@gmail.com"}).text
    r = client.post(url, data={"action": "add_email", "email": "erika@acme-drones.com"})
    assert r.status_code == 200 and "We sent a code to erika@acme-drones.com" in r.text and 'name="code"' in r.text
    code = _code_from_mail(env, "erika@acme-drones.com")
    assert "The code is not right" in client.post(url, data={"action": "confirm_email", "email": "erika@acme-drones.com", "code": "000000" if code != "000000" else "111111"}).text
    ok = client.post(url, data={"action": "confirm_email", "email": "erika@acme-drones.com", "code": code})
    assert "is confirmed" in ok.text
    c = case_of(GMAIL)
    assert [v["email"] for v in c["emails"]["verified"]] == ["erika@acme-drones.com"]
    # the OTP mail body is not stored in the case file
    assert all(code not in (x.get("body") or "") for x in c["correspondence"])
    assert not any(code in (e["data"].get("subject") or "") + (e["data"].get("body") or "") for e in ST.events_of(c["id"]) if e["type"] == "mail_out")
    # one strong signal (the company e-mail) + the applicant listed on the site = two strong signals -> verified without any link
    assert c["status"] == ENG.VERIFIED and step(env, "gmail").status_code == 200


def test_wrong_codes_lock_and_expired_codes_are_refused(env, monkeypatch):
    sign(env, who="gmail")
    c = case_of(GMAIL)
    url = f"/api/verification/reply/{token_of(c)}"
    client.post(url, data={"action": "add_email", "email": "erika@acme-drones.com"})
    good = _code_from_mail(env, "erika@acme-drones.com")
    bad = "999999" if good != "999999" else "888888"
    for _ in range(5):
        client.post(url, data={"action": "confirm_email", "email": "erika@acme-drones.com", "code": bad})
    assert "Too many wrong codes" in client.post(url, data={"action": "confirm_email", "email": "erika@acme-drones.com", "code": good}).text
    client.post(url, data={"action": "add_email", "email": "other@acme-drones.com"})
    code2 = _code_from_mail(env, "other@acme-drones.com")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert "expired" in client.post(url, data={"action": "confirm_email", "email": "other@acme-drones.com", "code": code2}).text


def test_replying_with_links_and_a_note_reruns_the_checks(env):
    network({k: v for k, v in NET_OK.items() if not k.endswith("/team")})          # the website does not list the applicant
    sign(env, who="gmail")
    c = case_of(GMAIL)
    before = c["rounds"]
    url = f"/api/verification/reply/{token_of(c)}"
    r = client.post(url, data={"action": "reply", "urls": "https://www.linkedin.com/in/erika-mustermann-1a2b\nnot a url", "note": "I lead the drive team.",
                                "website": "https://www.acme-drones.com", "reg_no": "", "vat_no": ""})
    assert "received your answer" in r.text
    c = case_of(GMAIL)
    assert c["rounds"] == before + 1 and c["urls"] == ["https://www.linkedin.com/in/erika-mustermann-1a2b"] and c["notes"][0]["note"] == "I lead the drive team."
    assert c["open_replies"] == 1 and any(x["dir"] == "in" for x in c["correspondence"])
    assert any(e["check"] == "person.linkedin" and e["strength"] == "supporting" for e in c["evidence"])
    assert c["status"] != ENG.VERIFIED                 # LinkedIn is never enough alone
    assert "fill in at least one" in client.post(url, data={"action": "reply", "urls": "", "note": "", "website": "https://www.acme-drones.com"}).text


def test_three_unproductive_replies_end_with_a_person(env):
    sign(env, who="gmail")
    c = case_of(GMAIL)
    url = f"/api/verification/reply/{token_of(c)}"
    for i in range(3):
        client.post(url, data={"action": "reply", "note": "hello %d" % i})
    c = case_of(GMAIL)
    assert c["status"] == ENG.MANUAL_REVIEW and c["reasons"][0]["code"] == "repeated_failure"


def test_one_document_is_stored_privately_and_never_served_inline(env):
    sign(env, who="gmail")
    c = case_of(GMAIL)
    url = f"/api/verification/reply/{token_of(c)}/document"
    pdf = b"%PDF-1.4\n%fake\n"
    r = client.post(url, files={"file": ("licence.pdf", io.BytesIO(pdf), "application/pdf")})
    assert r.status_code == 200 and "document is stored" in r.text
    c = case_of(GMAIL)
    (doc,) = c["documents"]
    assert doc["ext"] == "pdf" and doc["bytes"] == len(pdf) and len(doc["sha256"]) == 64
    stored = ST.documents_dir() / c["id"] / f"{doc['id']}.pdf"
    assert stored.read_bytes() == pdf
    # refused: wrong type, a renamed executable, too large, empty
    for name, data in (("run.exe", b"MZ\x90"), ("fake.pdf", b"MZ\x90\x00"), ("x.png", b"%PDF-1.4"), ("e.pdf", b"")):
        bad = client.post(url, files={"file": (name, io.BytesIO(data), "application/octet-stream")})
        assert "Only PDF" in bad.text or "Choose a file" in bad.text, name
    big = client.post(url, files={"file": ("big.pdf", io.BytesIO(b"%PDF" + b"0" * (5 * 1024 * 1024)), "application/pdf")})
    assert "too large" in big.text
    assert len(case_of(GMAIL)["documents"]) == 1
    # the applicant's token does not download it; an administrator gets an attachment, audited
    assert client.get(f"/api/admin/verification/{c['id']}/documents/{doc['id']}", headers=env["gmail"]).status_code == 403
    d = client.get(f"/api/admin/verification/{c['id']}/documents/{doc['id']}", headers=env["admin"])
    assert d.status_code == 200 and d.content == pdf and d.headers["content-disposition"].startswith("attachment")
    assert d.headers["x-content-type-options"] == "nosniff"
    assert any(a["action"] == "verification.document" for a in audit(env))


def test_a_denied_case_cannot_be_changed_from_the_reply_page(env):
    sign(env, who="gmail")
    c = case_of(GMAIL)
    client.post(f"/api/admin/verification/{c['id']}/deny", json={"reason": "not our customer"}, headers=env["admin"])
    url = f"/api/verification/reply/{token_of(c)}"
    client.post(url, data={"action": "reply", "note": "please"})
    client.post(url + "/document", files={"file": ("a.pdf", io.BytesIO(b"%PDF-1"), "application/pdf")})
    c = case_of(GMAIL)
    assert c["status"] == ENG.DENIED and c["notes"] == [] and c["documents"] == []


# -- the signed-in status ---------------------------------------------------------------------------------------------------------------

def test_mine_gives_the_status_the_link_and_lets_the_account_complete_its_details(env):
    sign(env, who="gmail", website="", purpose="")
    m = client.get("/api/verification/mine", headers=env["gmail"]).json()
    assert m["state"] == "need_info" and m["reply_url"].startswith("http") and "/api/verification/reply/" in m["reply_url"]
    assert ENG.REQ_WEBSITE in m["requests"] and m["details"]["website"] == ""
    r = client.post("/api/verification/mine/details", json={"website": "acme-drones.com", "purpose": "testing", "vat_no": ""}, headers=env["gmail"])
    assert r.status_code == 200
    assert case_of(GMAIL)["application"]["website"] == "https://acme-drones.com"
    assert client.post("/api/verification/mine/details", json={"website": "nope"}, headers=env["gmail"]).status_code == 422
    assert client.get("/api/verification/mine", headers=env["flagged"]).json()["state"] == "ok"
    assert client.get("/api/verification/mine").status_code in (200, 401)


def test_an_account_that_signed_before_this_system_gets_a_case_on_its_first_look_without_a_mail(env):
    from motor_ai_sim import nda as N
    from motor_ai_sim import users as U
    sign(env, who="corp")
    # forget the case, as if the NDA had been signed before the verification existed
    import shutil
    shutil.rmtree(ST.root())
    env["mail"].clear()
    assert ST.cases_of(CORP) == []
    assert step(env, "corp").status_code == 403                   # closed, not open, while there is no case
    m = client.get("/api/verification/mine", headers=env["corp"]).json()
    assert m["state"] in ("need_info", "in_progress") and ST.latest_of(CORP)["nda"]["id"] == U.get_nda(CORP)["record_id"]
    assert env["mail"] == []                                       # silent: no mail from a GET
    assert N.get_record(U.get_nda(CORP)["record_id"])["company"] == "Acme Drones Ltd"


# -- the administrator ---------------------------------------------------------------------------------------------------------------------

def test_admin_queue_case_detail_and_every_action_is_audited(env):
    network(NET_OK)
    sign(env, who="gmail")
    sign(env, who="corp")
    q = client.get("/api/admin/verification", headers=env["admin"]).json()
    assert {r["user"] for r in q["cases"]} == {GMAIL, CORP} and q["counts"]["VERIFIED"] == 1
    only = client.get("/api/admin/verification?status=MORE_INFORMATION_REQUIRED", headers=env["admin"]).json()["cases"]
    assert [r["user"] for r in only] == [GMAIL]
    assert client.get("/api/admin/verification?status=WHATEVER", headers=env["admin"]).status_code == 422
    assert client.get("/api/admin/verification", headers=env["gmail"]).status_code == 403
    cid = case_of(GMAIL)["id"]
    detail = client.get(f"/api/admin/verification/{cid}", headers=env["admin"]).json()
    assert detail["case"]["application"]["legal_name"] == "Acme Drones Ltd" and detail["nda"]["version"] == "v1-2026-10"
    types = [e["type"] for e in detail["events"]]
    assert types[0] == "case_created" and "check_run" in types and "status_changed" in types and "mail_out" in types
    run = next(e for e in detail["events"] if e["type"] == "check_run")
    assert all({"source", "check", "result", "at", "summary"} <= set(x) for x in run["data"]["evidence"])
    assert any(x["source"] == "gleif" and x["source_url"] and x["raw"] for x in run["data"]["evidence"])

    # request info -> a templated mail with the admin's words
    r = client.post(f"/api/admin/verification/{cid}/request-info", json={"text": "Please send the name of your purchasing contact."}, headers=env["admin"])
    assert r.status_code == 200 and r.json()["case"]["status"] == ENG.MORE_INFORMATION_REQUIRED
    assert "purchasing contact" in [m for m in env["mail"] if m["to"] == GMAIL][-1]["body"]
    assert client.post(f"/api/admin/verification/{cid}/request-info", json={"text": " "}, headers=env["admin"]).status_code == 422
    # re-run
    assert client.post(f"/api/admin/verification/{cid}/rerun", headers=env["admin"]).status_code == 200
    # approve needs a reason, opens the STEP, mails the applicant
    assert client.post(f"/api/admin/verification/{cid}/approve", json={"reason": ""}, headers=env["admin"]).status_code == 422
    assert step(env, "gmail").status_code == 403
    ap = client.post(f"/api/admin/verification/{cid}/approve", json={"reason": "met at the fair"}, headers=env["admin"])
    assert ap.json()["case"]["status"] == ENG.VERIFIED and ap.json()["case"]["status_actor"] == "admin:" + ADMIN
    assert step(env, "gmail").status_code == 200
    # a later automatic re-run never takes an administrator's approval away; a denial does
    SV.run_checks(cid, trigger="reply")
    assert case_of(GMAIL)["status"] == ENG.VERIFIED
    dn = client.post(f"/api/admin/verification/{cid}/deny", json={"reason": "sanctions list"}, headers=env["admin"])
    assert dn.json()["case"]["status"] == ENG.DENIED and not dn.json()["case"]["access"]["granted"]
    assert step(env, "gmail").status_code == 403
    SV.run_checks(cid, trigger="reply")
    assert case_of(GMAIL)["status"] == ENG.DENIED

    acts = [(a["action"], a["target"]) for a in audit(env) if a["action"].startswith("verification.")]
    for want in ("verification.read", "verification.request", "verification.rerun", "verification.approve", "verification.deny"):
        assert (want, cid) in acts, want
    ap_row = next(a for a in audit(env) if a["action"] == "verification.approve")
    assert ap_row["actor"] == ADMIN and ap_row["subject"] == GMAIL and ap_row["details"]["reason"] == "met at the fair"
    ev = ST.events_of(cid)
    assert [e["actor"] for e in ev if e["type"] == "admin_action"] == ["admin:" + ADMIN] * 4
    assert any(e["type"] == "access_granted" for e in ev) and any(e["type"] == "access_revoked" for e in ev)


# -- the audit trail -----------------------------------------------------------------------------------------------------------------------

def test_the_audit_trail_is_complete_append_only_and_rebuildable(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = case_of(GMAIL)
    url = f"/api/verification/reply/{token_of(c)}"
    client.post(url, data={"action": "add_email", "email": "erika@acme-drones.com"})
    client.post(url, data={"action": "confirm_email", "email": "erika@acme-drones.com", "code": _code_from_mail(env, "erika@acme-drones.com")})
    client.post(url, data={"action": "reply", "urls": "https://www.linkedin.com/in/erika-mustermann", "note": "hi"})
    client.post(url + "/document", files={"file": ("a.pdf", io.BytesIO(b"%PDF-1"), "application/pdf")})
    assert step(env, "gmail").status_code == 200
    ev = ST.events_of(c["id"])
    types = {e["type"] for e in ev}
    assert {"case_created", "check_run", "status_changed", "mail_out", "reply_in", "email_otp_sent", "email_verified", "document_uploaded",
            "access_granted", "download"} <= types
    created = ev[0]
    assert created["type"] == "case_created" and created["actor"] == "system" and created["iso"].endswith("Z") and created["ts"] > 0
    assert created["data"]["nda"]["version"] == "v1-2026-10" and created["data"]["nda"]["id"] and created["data"]["application"]["legal_name"]
    st = next(e for e in ev if e["type"] == "status_changed")
    assert st["data"]["reason"] and st["data"]["reasons"]
    dl = next(e for e in ev if e["type"] == "download")
    logged = M.read_log(5, user=GMAIL)[0]
    assert dl["data"]["download_id"] == logged["download_id"] and dl["data"]["sha256"] == logged["sha256"]
    assert logged["nda_basis"]["verification"]["case"] == c["id"] and logged["nda_basis"]["verification"]["status"] == ENG.VERIFIED
    # the in/out correspondence is on the case
    dirs = {x["dir"] for x in case_of(GMAIL)["correspondence"]}
    assert dirs == {"in", "out"}
    # append-only: the file only grows, and the state file is a rebuildable cache of it
    size = ST.events_path().stat().st_size
    SV.run_checks(c["id"], trigger="reply")
    assert ST.events_path().stat().st_size > size
    saved = json.loads((ST.cases_dir() / (c["id"] + ".json")).read_text(encoding="utf-8"))
    (ST.cases_dir() / (c["id"] + ".json")).unlink()
    assert ST.rebuild(c["id"]) == saved                  # the state is exactly the reduction of the events
    assert json.loads((ST.cases_dir() / (c["id"] + ".json")).read_text(encoding="utf-8"))["id"] == c["id"]
    # the case timeline in the admin view carries the file's lines
    assert len(client.get(f"/api/admin/verification/{c['id']}", headers=env["admin"]).json()["events"]) >= len(ev)


def test_the_reducer_is_pure_and_the_store_refuses_unknown_events(env):
    ev = {"ts": 1.0, "iso": "1970-01-01T00:00:01Z", "case": "v20261015-aaaaaaaaaaaa", "type": "case_created", "actor": "system",
          "data": {"user": "a@b.com", "application": {"legal_name": "X"}}}
    s1 = ST.apply(None, ev)
    s2 = ST.apply(None, ev)
    assert s1 == s2 and s1["user"] == "a@b.com" and s1["events"] == 1
    with pytest.raises(ValueError):
        ST.append("v20261015-aaaaaaaaaaaa", "made_up", {})
    with pytest.raises(ValueError):
        ST.append("not-a-case", "download", {})


def test_the_store_lives_beside_the_nda_records_and_nothing_is_ever_deleted(env):
    sign(env)
    assert ST.root() == env["legal"] / "verification"
    assert (ST.root() / "events.jsonl").is_file() and (ST.root() / "index.json").is_file()
    src = (__import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim" / "verification" / "store.py").read_text(encoding="utf-8")
    assert "unlink" not in src and "os.remove" not in src and "rmtree" not in src


def test_one_form_keeps_what_was_typed_and_stores_the_details_with_send_me_a_code(env):
    """Owner 2026-10-17: a free address + links / registration / VAT, then "Send me a code" -> the page came back empty and nothing was
    stored.  Now the details are stored with either button and a refused address is shown again with the error."""
    sign(env, who="gmail")
    c = case_of(GMAIL)
    before = c["rounds"]
    url = f"/api/verification/reply/{token_of(c)}"
    r = client.post(url, data={"action": "add_email", "email": "x@gmail.com", "urls": "https://www.linkedin.com/in/erika-mustermann-1a2b",
                                "website": "https://www.acme-drones.com", "reg_no": "7255691000", "vat_no": "SI22399933", "note": ""})
    assert "free or disposable" in r.text and "received your answer" in r.text
    assert 'value="x@gmail.com"' in r.text and 'value="7255691000"' in r.text and 'value="SI22399933"' in r.text
    c = case_of(GMAIL)
    assert c["rounds"] == before + 1 and c["urls"] == ["https://www.linkedin.com/in/erika-mustermann-1a2b"]
    assert c["application"]["reg_no"] == "7255691000" and c["application"]["vat_no"] == "SI22399933"
    # nothing else typed: the refused address and the error come back, nothing is stored
    r = client.post(url, data={"action": "add_email", "email": "y@gmail.com"})
    assert "free or disposable" in r.text and 'value="y@gmail.com"' in r.text and "received your answer" not in r.text
    assert case_of(GMAIL)["rounds"] == before + 1
    # one form, one button: details + a corporate address in one submit store the details and send the code
    r = client.post(url, data={"action": "send", "email": "erika@acme-drones.com", "note": "I lead the drive team."})
    assert "We sent a code to erika@acme-drones.com" in r.text and "received your answer" in r.text and 'name="code"' in r.text
    assert case_of(GMAIL)["notes"][-1]["note"] == "I lead the drive team."
    # pressing Send again while the code waits does not send another one
    sends = len([x for x in case_of(GMAIL)["correspondence"] if x.get("dir") == "out"])
    client.post(url, data={"action": "send", "email": "erika@acme-drones.com"})
    assert len([x for x in case_of(GMAIL)["correspondence"] if x.get("dir") == "out"]) == sends
    page = client.get(url).text
    assert page.count('name="action" value="send"') == 1 and "add_email" not in page and page.count("<button") == 4   # send, confirm, resend, upload


def test_send_with_nothing_new_asks_for_the_email_and_no_repeat_mail(env):
    """Owner 2026-10-17: details saved, then Send with an empty e-mail -> a red error and the same "one more thing" mail again, a loop.
    Now the page says the details are saved and the company e-mail is the next step; a page answer sends no new request mail."""
    network({k: v for k, v in NET_OK.items() if not k.endswith("/team")})
    sign(env, who="gmail")
    c = case_of(GMAIL)
    url = f"/api/verification/reply/{token_of(c)}"
    outs = lambda: len([x for x in case_of(GMAIL)["correspondence"] if x.get("dir") == "out"])     # noqa: E731
    n0 = outs()
    r = client.post(url, data={"action": "send", "urls": "https://www.linkedin.com/in/erika-mustermann-1a2b", "vat_no": "DE123456789"})
    assert "received your answer" in r.text
    new = [x for x in case_of(GMAIL)["correspondence"] if x.get("dir") == "out"][n0:]
    assert not [x for x in new if "one more thing" in (x.get("subject") or "")], [x.get("subject") for x in new]
    r = client.post(url, data={"action": "send", "email": ""})
    assert case_of(GMAIL)["status"] == ENG.MORE_INFORMATION_REQUIRED
    assert "Your details are saved" in r.text and 'class="err"' not in r.text
    assert "Already received" in client.get(url).text and "links: 1" in client.get(url).text
