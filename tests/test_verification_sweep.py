"""Tests for applicant verification follow-up sweep:
- Reminder on Day 3 once (silent applicant)
- Escalation on Day 7 to MANUAL_REVIEW (applicant_silent)
- Idempotence of sweep()
- Daily digest to support@ (at most once per calendar day, only when cases wait)
- Resend code on reply page (2-minute cooldown guard, daily limit)
- Badge count for admin (MANUAL_REVIEW + PENDING > 1h) and GET /api/admin/verification/waiting
- CLI: python -m motor_ai_sim.verification.sweep [--dry-run]
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import auth_email as E
from motor_ai_sim.api import app
from motor_ai_sim.verification import config as C
from motor_ai_sim.verification import engine as ENG
from motor_ai_sim.verification import net as NET
from motor_ai_sim.verification import service as SV
from motor_ai_sim.verification import store as ST
from motor_ai_sim.verification import texts as TX
from motor_ai_sim.verification.sweep import main as sweep_main
from tests._verification_fakes import FakeHttp
from tests.test_nda import CFG, DIE, GOOD, MID, STEP, install

ADMIN = "admin@example.com"
CORP = "erika@acme-drones.com"
GMAIL = "erika.m@gmail.com"
FLAGGED = "flagged@example.com"

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
    from motor_ai_sim import auth, model3d as M, users as U
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
    SV.BACKGROUND = False
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(admin=h(ADMIN), corp=h(CORP), gmail=h(GMAIL), flagged=h(FLAGGED), mail=sent, legal=legal, url=f"/api/model3d/{DIE}/{CFG}",
                audit=tmp_path / "admin_audit.jsonl", log=tmp_path / "cad_downloads.jsonl")


def network(routes):
    NET.set_http(FakeHttp(routes))


def sign(env, who="gmail", **over):
    return client.post("/api/nda/sign", json={**GOOD, **INTAKE, "version": "v1-2026-10", **over}, headers=env[who])


# -- Task 1: Reminder and escalation for silent applicants -------------------------------------------------------------

def test_reminder_on_day_3_once(env):
    network(NET_OK)
    r = sign(env, who="gmail")
    assert r.status_code == 200
    c = ST.cases_of(GMAIL)[0]
    assert c["status"] == ENG.MORE_INFORMATION_REQUIRED
    t0 = c["created"]

    env["mail"].clear()

    # Day 2: no reminder yet
    res = SV.sweep(now=t0 + 2.0 * 86400)
    assert res["reminded"] == 0
    assert len(env["mail"]) == 0

    # Day 3.1: reminder sent once
    res = SV.sweep(now=t0 + 3.1 * 86400)
    assert res["reminded"] == 1
    reminders = [m for m in env["mail"] if m["to"] == GMAIL and "reminder" in m["subject"].lower()]
    assert len(reminders) == 1
    m = reminders[0]
    assert SV.reply_url(c["id"]) in m["body"]
    assert "support@aerostator.com" in m["body"]

    # Verify event recorded
    c_updated = ST.get(c["id"])
    assert c_updated.get("reminder_sent")
    assert any(e["type"] == "reminder_sent" for e in ST.events_of(c["id"]))

    # Calling sweep again on day 3.2: not sent again (idempotent, never twice)
    env["mail"].clear()
    res2 = SV.sweep(now=t0 + 3.2 * 86400)
    assert res2["reminded"] == 0
    assert len(env["mail"]) == 0


def test_escalation_on_day_7(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    t0 = c["created"]

    # At day 6.9: not escalated
    res = SV.sweep(now=t0 + 6.9 * 86400)
    assert res["escalated"] == 0
    assert ST.get(c["id"])["status"] == ENG.MORE_INFORMATION_REQUIRED

    # At day 7.1: escalated to MANUAL_REVIEW
    env["mail"].clear()
    res = SV.sweep(now=t0 + 7.1 * 86400)
    assert res["escalated"] == 1

    c_esc = ST.get(c["id"])
    assert c_esc["status"] == ENG.MANUAL_REVIEW
    assert any(r.get("code") == "applicant_silent" for r in c_esc.get("reasons", []))

    # Applicant receives review mail
    app_mails = [m for m in env["mail"] if m["to"] == GMAIL]
    assert any("reviewed" in m["subject"].lower() for m in app_mails)

    # Admin receives notification mail
    admin_mails = [m for m in env["mail"] if m["to"] == ADMIN]
    assert any("[verification]" in m["subject"] and "manual review" in m["subject"] for m in admin_mails)


def test_applicant_activity_resets_silence_countdown(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    t0 = c["created"]

    # Day 2: applicant uploads document
    SV.add_document(c["id"], "licence.pdf", b"%PDF-valid-data", now=t0 + 2.0 * 86400)

    # Day 4 (2 days after document): silence is 2 days, no reminder
    res = SV.sweep(now=t0 + 4.0 * 86400)
    assert res["reminded"] == 0

    # Day 5.1 (3.1 days after document): reminder sent
    res = SV.sweep(now=t0 + 5.1 * 86400)
    assert res["reminded"] == 1


def test_sweep_is_idempotent(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    t0 = c["created"]

    # Run sweep at day 8
    res1 = SV.sweep(now=t0 + 8.0 * 86400)
    assert res1["escalated"] == 1

    # Second run immediately after
    res2 = SV.sweep(now=t0 + 8.0 * 86400)
    assert res2["escalated"] == 0
    assert res2["reminded"] == 0


# -- Task 2: Badge count and waiting decisions ------------------------------------------------------------------------

def test_badge_count_and_waiting_endpoint(env):
    network(NET_OK)
    # Initially no waiting cases
    r = client.get("/api/admin/verification/waiting", headers=env["admin"])
    assert r.status_code == 200
    assert r.json() == {"count": 0}

    # Case in MORE_INFORMATION_REQUIRED is not waiting for admin
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    assert SV.count_waiting() == 0

    # Move case to MANUAL_REVIEW
    ST.append(c["id"], "status_changed", {"status": ENG.MANUAL_REVIEW, "reason": "test"}, actor="system")
    assert SV.count_waiting() == 1
    r = client.get("/api/admin/verification/waiting", headers=env["admin"])
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # PENDING case older than 1 hour is also counted
    cid = ST.new_case_id()
    t_now = time.time()
    ST.append(cid, "case_created", {"user": "pending@example.com", "application": {"legal_name": "Pending Inc"}, "nda": {}}, now=t_now - 3650)
    assert SV.count_waiting(now=t_now) == 2


# -- Task 3: Daily digest to support@ ----------------------------------------------------------------------------------

def test_daily_digest_only_when_something_waits_and_at_most_once_per_day(env):
    network(NET_OK)
    t = 1800000000.0  # arbitrary fixed epoch timestamp

    # Nothing waiting: no digest sent
    env["mail"].clear()
    res = SV.sweep(now=t)
    assert res["digest"] == 0
    assert not any("digest" in m["subject"].lower() for m in env["mail"])

    # Create a waiting case (MANUAL_REVIEW)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    ST.append(c["id"], "status_changed", {"status": ENG.MANUAL_REVIEW, "reason": "testing review"}, actor="system", now=t)

    # First run today: digest sent
    env["mail"].clear()
    res = SV.sweep(now=t)
    assert res["digest"] == 1
    digests = [m for m in env["mail"] if "[verification]" in m["subject"] and "digest" in m["subject"].lower()]
    assert len(digests) == 1
    d = digests[0]
    assert d["to"] == ADMIN
    assert "Acme Drones Ltd" in d["body"]
    assert "/?admin=verification" in d["body"]
    assert "MANUAL_REVIEW" in d["body"]

    # Running sweep again on the same day: no duplicate digest
    env["mail"].clear()
    res = SV.sweep(now=t + 3600)
    assert res["digest"] == 0
    assert len(env["mail"]) == 0

    # Next calendar day: digest sent again
    env["mail"].clear()
    res = SV.sweep(now=t + 86400 + 100)
    assert res["digest"] == 1
    assert any("digest" in m["subject"].lower() for m in env["mail"])


# -- Task 4: Reply page resend, 2-minute guard, daily limit & contact link --------------------------------------------

def test_resend_button_cooldown_and_daily_limit(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    tok = SV.make_token(c["id"])

    t0 = time.time()
    # First code sent via start_email
    ok, code = SV.start_email(c["id"], "work@acme-drones.com", now=t0)
    assert ok is True and code == "sent"

    # Resend attempt at 60s (within 2-minute cooldown): rejected with cooldown
    ok, code = SV.resend_email(c["id"], "work@acme-drones.com", now=t0 + 60)
    assert ok is False and code == "cooldown"

    # HTTP POST at current time (within 2-minute cooldown from t0): shows cooldown message
    r = client.post(f"/api/verification/reply/{tok}", data={"action": "resend", "email": "work@acme-drones.com"})
    assert r.status_code == 200
    assert "2 minutes" in r.text.lower() or "2 分钟" in r.text

    # Resend at 125s (after 2 minutes): allowed
    ok, code = SV.resend_email(c["id"], "work@acme-drones.com", now=t0 + 125)
    assert ok is True and code == "sent"

    # Exhaust remaining per-day limit (total 6 allowed)
    for i in range(4):
        ok, code = SV.resend_email(c["id"], "work@acme-drones.com", now=t0 + 300 + i * 130)
        assert ok is True

    # 7th send is blocked by per-day limit
    ok, code = SV.resend_email(c["id"], "work@acme-drones.com", now=t0 + 2000)
    assert ok is False and code == "limit"


def test_reply_page_contains_no_code_block_and_contact(env):
    network(NET_OK)
    sign(env, who="gmail")
    c = ST.cases_of(GMAIL)[0]
    tok = SV.make_token(c["id"])

    # Send one code so pending_email is set
    SV.start_email(c["id"], "team@acme-drones.com")

    # English page
    r = client.get(f"/api/verification/reply/{tok}?lang=en")
    assert r.status_code == 200
    assert "Did not get the code?" in r.text
    assert "spam" in r.text.lower()
    assert 'action" value="resend"' in r.text
    assert "Send a new code" in r.text
    assert "mailto:support@aerostator.com" in r.text

    # Chinese page
    r_zh = client.get(f"/api/verification/reply/{tok}?lang=zh-CN")
    assert r_zh.status_code == 200
    assert "没收到验证码？" in r_zh.text
    assert "垃圾邮件" in r_zh.text
    assert "重新发送验证码" in r_zh.text
    assert "mailto:support@aerostator.com" in r_zh.text


def test_cli_sweep_dry_run(env, capsys):
    network(NET_OK)
    sign(env, who="gmail")
    # Dry run CLI should succeed with code 0
    code = sweep_main(["--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    assert "verification sweep:" in out
