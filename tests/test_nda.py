"""Online NDA click-through (routes/nda.py, nda.py): validation, the append-only record, the user flag, the STEP unlock, the
placeholder guard, revoke, the signed PDF, versioning, the admin overview and the mail copy.

Runs against a throwaway registry, a throwaway legal directory and a fake CAD tree; no FreeCAD, no SMTP.
"""
from __future__ import annotations

import datetime
import json
import time

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import model3d as M
from motor_ai_sim import nda as N
from motor_ai_sim.api import app

ADMIN = "admin@example.com"
USER = "buyer@example.com"
FREE = "someone@gmail.com"
DIE, CFG, MID = "CIANO14 40 new", "L12", "CIANO14_40_new_L12"

TEXT_EN = ("# Mutual NDA\n\nBetween MOTRES d.o.o. and {{company}} (reg. {{reg_no}}, {{country}}, {{address}}).\n\n"
           "Signed by {{signatory}}, {{title}}, {{email}} on {{date}}. Version {{version}}.\n")
TEXT_ZH = "# 保密协议\n\n{{company}}（注册号 {{reg_no}}）与 MOTRES d.o.o. 之间，签署人 {{signatory}}，{{title}}，{{date}}，版本 {{version}}。\n"
STEP = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('x'),'2;1');\nFILE_NAME('a','b',('c'),('d'),'e','f','g');\n"
        "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n#1=CARTESIAN_POINT('',(0.,0.,0.));\nENDSEC;\nEND-ISO-10303-21;\n").encode()

client = TestClient(app)

GOOD = dict(lang="en", company="Acme Drones Ltd", reg_no="HRB 12345", country="DE", address="Hauptstr. 1, 10115 Berlin",
            signatory="Erika Mustermann", title="CTO", accept=True, typed_name="erika  mustermann")


def install(legal, version, current=True):
    d = legal / "model_nda" / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "model_nda_en.md").write_text(TEXT_EN, encoding="utf-8")
    (d / "model_nda_zh-CN.md").write_text(TEXT_ZH, encoding="utf-8")
    if current:
        (legal / "model_nda" / "current.txt").write_text(version + "\n", encoding="utf-8")


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
    monkeypatch.setenv("MOTRES_CAD_ROOT", str(tmp_path / "cad"))
    monkeypatch.setenv("CAD_DOWNLOAD_LOG", str(tmp_path / "cad_downloads.jsonl"))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    U.create_user(USER, "password-buyer", role="user", name="Erika")
    U.create_user(FREE, "password-free", role="user", name="Free")
    for e in (USER, FREE):
        U.set_motor_grants(e, all_motors=False, dies=[DIE])
    h = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}
    return dict(admin=h(ADMIN), user=h(USER), free=h(FREE), legal=legal, url=f"/api/model3d/{DIE}/{CFG}")


def sign(env, who="user", **over):
    return client.post("/api/nda/sign", json={**GOOD, "version": "v1-2026-10", **over}, headers=env[who])


def records(env):
    p = env["legal"] / "nda_signatures.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# -- the text and its hash ----------------------------------------------------

def test_current_text_hash_matches_what_is_served(env):
    r = client.get("/api/nda/current?lang=en", headers=env["user"]).json()
    assert r["version"] == "v1-2026-10" and r["lang"] == "en" and r["text"] == TEXT_EN and r["signable"] is True
    assert r["sha256"] == N.sha256_text(r["text"]) and "DE" in r["countries"] and "CN" in r["countries"]
    zh = client.get("/api/nda/current?lang=zh-CN", headers=env["user"]).json()
    assert zh["lang"] == "zh-CN" and zh["sha256"] == N.sha256_text(TEXT_ZH)
    assert client.get("/api/nda/current?lang=fr", headers=env["user"]).json()["lang"] == "en"
    assert client.get("/api/nda/current").status_code in (200, 401)


def test_anonymous_is_refused(env, monkeypatch):
    from motor_ai_sim import auth
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    assert client.get("/api/nda/current").status_code == 401
    assert client.post("/api/nda/sign", json=GOOD).status_code == 401


# -- validation ---------------------------------------------------------------

def test_every_field_is_required_and_the_errors_name_them(env):
    r = client.post("/api/nda/sign", json={"version": "v1-2026-10", "lang": "en"}, headers=env["user"])
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert set(errs) == {"company", "reg_no", "country", "address", "signatory", "title", "accept", "typed_name"}
    assert records(env) == []


def test_typed_name_must_match_country_must_be_listed_and_box_ticked(env):
    errs = sign(env, typed_name="Somebody Else").json()["detail"]["errors"]
    assert errs == {"typed_name": "must match the signatory name"}
    assert "country" in sign(env, country="ZZ").json()["detail"]["errors"]
    assert "country" in sign(env, country="").json()["detail"]["errors"]
    assert "accept" in sign(env, accept=False).json()["detail"]["errors"]
    assert "accept" in sign(env, accept="yes").json()["detail"]["errors"]
    assert sign(env, version="v0-old").status_code == 409
    assert records(env) == []


# -- signing: record, flag, unlock, PDF ----------------------------------------

def test_signing_writes_the_record_unlocks_the_step_and_stores_the_pdf(env):
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403
    r = sign(env)
    assert r.status_code == 200 and r.json()["can_download"] is True
    (rec,) = records(env)
    assert rec["id"] == r.json()["record_id"] and rec["user"] == USER and rec["email"] == USER
    assert rec["company"] == "Acme Drones Ltd" and rec["country"] == "DE" and rec["signatory"] == "Erika Mustermann"
    assert rec["version"] == "v1-2026-10" and rec["lang"] == "en" and rec["iso"].endswith("Z") and rec["ip"] and "user_agent" in rec
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    expect = N.render(TEXT_EN, N.template_values(dict(company="Acme Drones Ltd", reg_no="HRB 12345", country="DE",
                                                      address="Hauptstr. 1, 10115 Berlin", signatory="Erika Mustermann", title="CTO"),
                                                 USER, today, "v1-2026-10"))
    assert rec["text_sha256"] == N.sha256_text(expect) and rec["template_sha256"] == N.sha256_text(TEXT_EN)
    assert "{{" not in expect
    pdf = (env["legal"] / "nda_signed" / (rec["id"] + ".pdf")).read_bytes()
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF")
    # the user flag
    from motor_ai_sim import users as U
    nda = U.get_nda(USER)
    assert nda["signed"] and nda["by"] == "online" and nda["version"] == "v1-2026-10" and nda["company"] == "Acme Drones Ltd"
    assert nda["record_id"] == rec["id"] and nda["date"] == today
    # the download is open at once, stamped and logged as usual
    s = client.get(env["url"] + "/model.step", headers=env["user"])
    assert s.status_code == 200 and b"Licensed to Erika" in s.content
    assert client.get("/api/model3d/models", headers=env["user"]).json()["can_download"] is True
    # the user fetches the signed PDF, a second signing of the same version is refused
    p = client.get("/api/nda/mine/pdf", headers=env["user"])
    assert p.status_code == 200 and p.content == pdf and p.headers["content-type"] == "application/pdf"
    mine = client.get("/api/nda/mine", headers=env["user"]).json()
    assert mine["signed"] and mine["source"] == "online" and mine["has_pdf"] and mine["nda"]["record_id"] == rec["id"]
    assert sign(env).status_code == 409 and len(records(env)) == 1


def test_pdf_carries_the_text_and_the_acceptance_block():
    rec = dict(id="n20261013-aabbccddeeff", signatory="Erika Mustermann", title="CTO", company="Acme Drones Ltd", reg_no="HRB 1",
               country="DE", address="Berlin", email="e@x.de", iso="2026-10-13T10:00:00Z", ip="203.0.113.7", version="v1",
               text_sha256="ab" * 32)
    pdf = N.build_pdf("# Title\n\nHello Acme Drones Ltd world.\n", rec, compress=False)
    for needle in (b"Hello Acme Drones Ltd world.", b"Erika Mustermann", b"203.0.113.7", b"n20261013-aabbccddeeff", b"ab" * 32, b"v1"):
        assert needle in pdf, needle
    zh = N.build_pdf(TEXT_ZH, dict(rec, company="阿克米公司"))
    assert zh.startswith(b"%PDF") and len(zh) > 2000


def test_mail_copy_is_skipped_quietly_without_smtp_and_sent_with_it(env, monkeypatch, caplog):
    from motor_ai_sim import auth_email as AE
    monkeypatch.setattr(AE, "smtp_configured", lambda: False)
    with caplog.at_level("INFO"):
        assert sign(env).status_code == 200
    assert any("mail not configured" in m for m in caplog.messages)
    sent = []
    monkeypatch.setattr(AE, "smtp_configured", lambda: True)
    monkeypatch.setattr(AE, "deliver", lambda msg: sent.append(msg))
    monkeypatch.setenv("NDA_NOTIFY_EMAIL", "legal@example.com")
    assert sign(env, who="free").status_code == 200
    for _ in range(40):
        if len(sent) >= 2:
            break
        time.sleep(0.05)
    assert sorted(m["To"] for m in sent) == ["legal@example.com", FREE]
    assert all(any(p.get_content_type() == "application/pdf" for p in m.iter_attachments()) for m in sent)


# -- the placeholder guard ------------------------------------------------------

def test_placeholder_text_blocks_signing(env, monkeypatch, tmp_path):
    empty = tmp_path / "legal2"
    empty.mkdir()
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(empty))                    # no current.txt: the shipped placeholder is current
    cur = client.get("/api/nda/current", headers=env["user"]).json()
    assert cur["version"].startswith("v0-placeholder") and cur["signable"] is False and "NOT FOR SIGNATURE" in cur["text"]
    r = client.post("/api/nda/sign", json={**GOOD, "version": cur["version"]}, headers=env["user"])
    assert r.status_code == 409 and "draft" in r.json()["detail"].lower()
    assert not (empty / "nda_signatures.jsonl").exists()
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403
    assert client.get("/api/nda/mine", headers=env["user"]).json()["signable"] is False


def test_missing_legal_directory_refuses_instead_of_creating_it(env, monkeypatch, tmp_path):
    ghost = tmp_path / "no-such-legal"
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(ghost))
    assert client.get("/api/nda/current", headers=env["user"]).json()["signable"] is False
    assert not ghost.exists()


# -- revoke and versions -------------------------------------------------------

def test_revoke_keeps_the_record_and_closes_the_download(env):
    rid = sign(env).json()["record_id"]
    off = client.patch(f"/api/auth/users/{USER}", json={"nda_signed": False}, headers=env["admin"])
    nda = off.json()["user"]["nda"]
    assert nda["signed"] is False and nda["record_id"] == rid and nda["version"] == "v1-2026-10" and nda["revoked"]
    assert [r["id"] for r in records(env)] == [rid]                         # the record line is never touched
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 403
    assert client.get(env["url"] + "/model.glb", headers=env["user"]).status_code == 200
    assert client.get(f"/api/nda/{rid}/pdf", headers=env["admin"]).status_code == 200        # admin can still open it
    assert sign(env).status_code == 200 and len(records(env)) == 2          # signing again is allowed after a revoke


def test_admin_date_edit_keeps_the_online_source(env):
    rid = sign(env).json()["record_id"]
    r = client.patch(f"/api/auth/users/{USER}", json={"nda_signed": True, "nda_date": "2026-11-01"}, headers=env["admin"])
    nda = r.json()["user"]["nda"]
    assert nda["by"] == "online" and nda["record_id"] == rid and nda["date"] == "2026-11-01"


def test_a_newer_version_does_not_invalidate_an_older_signature(env):
    sign(env)
    install(env["legal"], "v2-2027-01")
    cur = client.get("/api/nda/current", headers=env["user"]).json()
    assert cur["version"] == "v2-2027-01"
    assert client.get(env["url"] + "/model.step", headers=env["user"]).status_code == 200      # still signed (v1)
    assert client.get("/api/nda/mine", headers=env["user"]).json()["nda"]["version"] == "v1-2026-10"
    assert sign(env).status_code == 409                                                        # v1 text is no longer current
    r = client.post("/api/nda/sign", json={**GOOD, "version": "v2-2027-01"}, headers=env["user"])
    assert r.status_code == 200 and [x["version"] for x in records(env)] == ["v1-2026-10", "v2-2027-01"]


def test_admin_toggle_still_works_without_a_record(env):
    u = f"/api/auth/users/{FREE}"
    on = client.patch(u, json={"nda_signed": True, "nda_date": "2026-10-13"}, headers=env["admin"]).json()["user"]["nda"]
    assert on == {"signed": True, "date": "2026-10-13", "by": ADMIN}
    assert client.get(env["url"] + "/model.step", headers=env["free"]).status_code == 200
    off = client.patch(u, json={"nda_signed": False}, headers=env["admin"]).json()["user"]["nda"]
    assert off == {"signed": False, "date": "", "by": ""}


# -- admin overview -------------------------------------------------------------

def test_admin_overview_flags_free_mail_and_is_admin_only(env):
    sign(env)
    sign(env, who="free")
    rows = client.get("/api/nda/signatures", headers=env["admin"]).json()["signatures"]
    by = {r["email"]: r for r in rows}
    assert by[USER]["free_mail"] is False and by[FREE]["free_mail"] is True
    assert by[USER]["company"] == "Acme Drones Ltd" and by[USER]["country"] == "DE" and "user_agent" not in by[USER]
    assert client.get("/api/nda/signatures", headers=env["user"]).status_code == 403
    rid = by[USER]["id"]
    assert client.get(f"/api/nda/{rid}/pdf", headers=env["user"]).status_code == 403
    assert client.get("/api/nda/n00000000-000000000000/pdf", headers=env["admin"]).status_code == 404
    assert client.get("/api/nda/..%2Fx/pdf", headers=env["admin"]).status_code in (400, 404)


def test_free_mail_detection():
    for e in ("a@gmail.com", "a@QQ.com", "a@163.com", "a@yahoo.co.uk", "a@outlook.de", "a@proton.me"):
        assert N.free_mail(e), e
    for e in ("a@acme-drones.com", "a@motresres.com", "a@university.edu"):
        assert not N.free_mail(e), e


# -- conditional blocks: the Chinese / Hong Kong variant and the Slovenian one -------

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "nda"


def install_draft(env, monkeypatch):
    """The drafted agreement (two {{#if country_is_cn_hk}} / {{#unless ...}} blocks) as the current version."""
    d = env["legal"] / "model_nda" / "v3-draft-2026-10-13"
    d.mkdir(parents=True)
    for lang in ("en", "zh-CN"):
        (d / f"model_nda_{lang}.md").write_text((FIXTURES / f"model_nda_{lang}.md").read_text(encoding="utf-8"), encoding="utf-8")
    (env["legal"] / "model_nda" / "current.txt").write_text("v3-draft-2026-10-13\n", encoding="utf-8")
    real = N.build_pdf
    monkeypatch.setattr(N, "build_pdf", lambda r, rec, compress=True: real(r, rec, compress=False))   # literal text in the PDF
    return "v3-draft-2026-10-13"


def test_render_blocks_both_variants_and_no_template_syntax_left():
    t = "A {{#if country_is_cn_hk}}CN{{/if}}{{#unless country_is_cn_hk}}SI{{/unless}} B {{company}}"
    for country, want in (("CN", "A CN B X"), ("HK", "A CN B X"), ("hk", "A CN B X"), ("SI", "A SI B X"), ("DE", "A SI B X"), ("", "A SI B X")):
        v = N.template_values(dict(country=country, company="X"), "e@x", "2026-10-13", "v")
        assert N.render(t, v).strip() == want, country
    assert N.render("x {{#if nope}}a\n\n\n\nb{{/if}}\n\n\n\ny {{unknown}}", {}) == "x \n\ny {{unknown}}\n"


@pytest.mark.parametrize("country,has,hasnot", [("CN", "arbitration", "Convention"), ("HK", "arbitration", "Convention"),
                                                ("SI", "Convention", "arbitration")])
def test_signing_stores_the_hash_of_exactly_the_rendered_variant_and_the_pdf_has_that_text(env, monkeypatch, country, has, hasnot):
    ver = install_draft(env, monkeypatch)
    for lang in ("en", "zh-CN"):
        r = client.post("/api/nda/sign", json={**GOOD, "version": ver, "country": country, "lang": lang}, headers=env["user"])
        assert r.status_code == 200, r.text
        rec = records(env)[-1]
        served = client.get(f"/api/nda/current?lang={lang}", headers=env["user"]).json()
        assert "{{#" in served["text"] and rec["template_sha256"] == served["sha256"]            # the client renders the blocks itself
        rendered = N.render(served["text"], N.template_values(
            {k: GOOD[k] for k in ("company", "reg_no", "address", "signatory", "title")} | {"country": country}, USER, rec["iso"][:10], ver))
        assert "{{" not in rendered and rec["text_sha256"] == N.sha256_text(rendered)
        if lang == "en":
            assert has in rendered and hasnot not in rendered
            pdf = (env["legal"] / "nda_signed" / rec["pdf"]).read_bytes()
            assert has.encode() in pdf and hasnot.encode() not in pdf and rec["id"].encode() in pdf
        # revoke, so the next language can sign again
        client.patch(f"/api/auth/users/{USER}", json={"nda_signed": False}, headers=env["admin"])


def test_the_shipped_placeholder_stays_a_draft_and_has_no_blocks():
    for lang in ("en", "zh-CN"):
        t = (N.repo_legal_root() / "model_nda" / "v0-placeholder" / f"model_nda_{lang}.md").read_text(encoding="utf-8")
        assert "NOT FOR SIGNATURE" in t


def test_python_render_hashes_match_the_values_locked_in_the_web_test():
    """The same four hashes are asserted by web/src/lib/__tests__/nda.test.mjs for the TypeScript renderer."""
    f = dict(company="Acme Drones Ltd", reg_no="HRB 1", country="CN", address="Berlin", signatory="Erika Mustermann", title="CTO")
    want = {("en", "CN"): "fd174cb610c0f1fb2dc51e78eacfbb507ca626b581026c969ab8b7bcfd4b43c5",
            ("en", "SI"): "63cd700989536b990490cbdf5cbd915955fd3fd854f0f236ada4b4c965b66975",
            ("zh-CN", "CN"): "e6340750fc9010b87d184cb27ae9bb6f6affa0eb33e6d77f70c36fd1ac71a44c",
            ("zh-CN", "SI"): "4c9dd2e214542aa4c6df1fe4d75618a76c78c95e16077e4961733a61572cd182"}
    for (lang, country), h in want.items():
        text = (FIXTURES / f"model_nda_{lang}.md").read_text(encoding="utf-8")
        assert N.sha256_text(N.render(text, N.template_values(dict(f, country=country), "e@x.de", "2026-10-13", "v3"))) == h, (lang, country)
