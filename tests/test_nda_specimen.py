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


def extract_pdf_text(pdf):
    try:
        import fitz
    except ImportError:
        return None
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def cid_pdf_marker(text):
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    return UnicodeCIDFont("STSong-Light").formatForPdf(text).encode("ascii")


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
    assert cur["sha256s"]["en"].encode("ascii") in pdf and cur["sha256s"]["zh-CN"].encode("ascii") in pdf
    import hashlib
    expected_rendered_en = N.render(TEXT_EN, N.template_values({k: "________" for k in N.FIELDS}, "________", "________", VERSION))
    assert hashlib.sha256(expected_rendered_en.encode("utf-8")).hexdigest().encode("ascii") in pdf
    pdf_text = extract_pdf_text(pdf)
    if pdf_text is None:
        zh_marker = cid_pdf_marker("保密协议")
        assert b"English (EN)" in pdf and zh_marker in pdf and pdf.index(b"English (EN)") < pdf.index(zh_marker)
        assert b"/STSong-Light" in pdf
    else:
        assert "Between MOTRES d.o.o." in pdf_text and "保密协议" in pdf_text
        assert pdf_text.index("Between MOTRES d.o.o.") < pdf_text.index("保密协议")
    # Caller's personal data must never appear in the specimen
    assert USER.encode("ascii") not in pdf
    assert b"Erika Mustermann" not in pdf
    # Nothing is written to the signatures store or signed-PDF folder
    assert not (env["legal"] / "nda_signatures.jsonl").exists()
    assert not (env["legal"] / "nda_signed").exists()
    assert N.read_records() == []


def test_specimen_pdf_zh_cn_works_and_writes_nothing(env, monkeypatch):
    real = N.build_pdf
    monkeypatch.setattr(N, "build_pdf", lambda rendered, rec, *, compress=True, specimen=False:
                        real(rendered, rec, compress=False, specimen=specimen))
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
    pdf_text = extract_pdf_text(r.content)
    if pdf_text is None:
        zh_marker = cid_pdf_marker("保密协议")
        assert b"English (EN)" in r.content and zh_marker in r.content and r.content.index(b"English (EN)") < r.content.index(zh_marker)
        assert b"/STSong-Light" in r.content
    else:
        assert pdf_text.index("Between MOTRES d.o.o.") < pdf_text.index("保密协议")
    assert not (env["legal"] / "nda_signatures.jsonl").exists()
    assert not (env["legal"] / "nda_signed").exists()


def test_legacy_single_language_specimen_mapping_fails_closed():
    with pytest.raises(FileNotFoundError, match="Both English and zh-CN"):
        N.build_specimen_pdf({"version": VERSION, "lang": "en", "text": TEXT_EN, "sha256": N.sha256_text(TEXT_EN)})


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


def test_specimen_pdf_and_signing_fail_closed_when_one_language_is_missing(env):
    (env["legal"] / "model_nda" / VERSION / "model_nda_zh-CN.md").unlink()
    assert client.get("/api/nda/current", headers=env["user"]).status_code == 503
    assert client.get("/api/nda/current/pdf?lang=en", headers=env["user"]).status_code == 503
    r = client.post("/api/nda/sign", json={"lang": "en", "version": VERSION}, headers=env["user"])
    assert r.status_code == 503
    assert not (env["legal"] / "nda_signatures.jsonl").exists()
    assert not (env["legal"] / "nda_signed").exists()
