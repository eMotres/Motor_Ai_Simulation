"""Unsigned NDA specimen PDF for legal review before signing (routes/nda.py, nda.py).

Covers:
- 200 for a signed-in user, PDF body (%PDF..%%EOF), Content-Disposition and Cache-Control headers, blank fields, specimen header,
  version and SHA-256 of the served text;
- 401 for an anonymous caller;
- 503 when no agreement text is installed;
- zh-CN works (Chinese CID font path);
- nothing is written to the signatures store or disk.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim import nda as N
from motor_ai_sim.api import app

USER = "buyer@example.com"
VERSION = "v1-2026-10"

TEXT_EN = """# Mutual NDA

Between MOTRES d.o.o. and {{company}} (reg. {{reg_no}}, {{country}}, {{address}}).

Signed by {{signatory}}, {{title}}, {{email}} on {{date}}. Version {{version}}.
"""
TEXT_ZH = """# 保密协议

{{company}}（注册号 {{reg_no}}，{{country}}，{{address}}）与 MOTRES d.o.o. 之间，签署人 {{signatory}}，{{title}}，{{email}}，{{date}}，版本 {{version}}。
"""

client = TestClient(app)


def install(legal, version=VERSION):
    d = legal / "model_nda" / version
    d.mkdir(parents=True, exist_ok=True)
    (d / "model_nda_en.md").write_text(TEXT_EN, encoding="utf-8")
    (d / "model_nda_zh-CN.md").write_text(TEXT_ZH, encoding="utf-8")
    (legal / "model_nda" / "current.txt").write_text(f"{version}\n", encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    legal = tmp_path / "legal"
    legal.mkdir()
    install(legal)
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(legal))
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(tmp_path / "admin_audit.jsonl"))
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "AUTH_ENFORCE", True)
    U.create_user(USER, "password-buyer", role="user", name="Erika Mustermann")
    return dict(
        user={"Authorization": f"Bearer {U.issue_token(USER)}"},
        legal=legal,
        tmp_path=tmp_path,
    )


def test_specimen_pdf_for_signed_in_user_has_headers_blanks_hash_and_writes_nothing(env, monkeypatch):
    real = N.build_pdf
    monkeypatch.setattr(
        N,
        "build_pdf",
        lambda rendered, rec, *, compress=True, specimen=False: real(rendered, rec, compress=False, specimen=specimen),
    )
    cur = client.get("/api/nda/current?lang=en", headers=env["user"]).json()
    r = client.get("/api/nda/current/pdf?lang=en", headers=env["user"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert (
        r.headers["content-disposition"]
        == f'attachment; filename="AeroStator_Core_Model_NDA_{VERSION}_en_SPECIMEN.pdf"'
    )
    pdf = r.content
    assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF")
    assert b"SPECIMEN - NOT SIGNED - for review only" in pdf
    assert b"SPECIMEN - NOT SIGNED" in pdf
    assert b"________" in pdf
    assert VERSION.encode("ascii") in pdf
    assert cur["sha256"].encode("ascii") in pdf
    # Caller's personal data must never appear in the specimen
    assert USER.encode("ascii") not in pdf
    assert b"Erika Mustermann" not in pdf
    # Nothing is written to the signatures store or signed-PDF folder
    assert not (env["legal"] / "nda_signatures.jsonl").exists()
    assert not (env["legal"] / "nda_signed").exists()
    assert N.read_records() == []


def test_specimen_pdf_zh_cn_works_and_writes_nothing(env):
    cur = client.get("/api/nda/current?lang=zh-CN", headers=env["user"]).json()
    assert cur["lang"] == "zh-CN" and cur["sha256"] == N.sha256_text(TEXT_ZH)
    r = client.get("/api/nda/current/pdf?lang=zh-CN", headers=env["user"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    assert (
        r.headers["content-disposition"]
        == f'attachment; filename="AeroStator_Core_Model_NDA_{VERSION}_zh-CN_SPECIMEN.pdf"'
    )
    assert r.content.startswith(b"%PDF") and r.content.rstrip().endswith(b"%%EOF")
    assert len(r.content) > 1500
    assert not (env["legal"] / "nda_signatures.jsonl").exists()
    assert not (env["legal"] / "nda_signed").exists()


def test_specimen_pdf_refuses_anonymous_caller(env):
    r = client.get("/api/nda/current/pdf")
    assert r.status_code == 401
    assert not (env["legal"] / "nda_signatures.jsonl").exists()


def test_specimen_pdf_returns_503_when_no_text_is_installed(env, monkeypatch):
    empty_legal = env["tmp_path"] / "empty_legal"
    empty_repo = env["tmp_path"] / "empty_repo"
    empty_legal.mkdir()
    empty_repo.mkdir()
    monkeypatch.setenv("MOTRES_LEGAL_ROOT", str(empty_legal))
    monkeypatch.setattr(N, "repo_legal_root", lambda: empty_repo)
    assert client.get("/api/nda/current", headers=env["user"]).status_code == 503
    r = client.get("/api/nda/current/pdf?lang=en", headers=env["user"])
    assert r.status_code == 503
    assert not (empty_legal / "nda_signatures.jsonl").exists()
