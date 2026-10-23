"""Regression coverage for production PDF review findings R1 and R2."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from motor_ai_sim.production import store
from tests.test_production_access import ENGINEER, OEM_NDA, prod_env


DIE = "Synthetic PDF Review"
CONFIG = "Route Review"


def _create(env):
    body = {
        "title": {"en": "PDF review", "zh": "PDF review", "src_lang": "en"},
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}],
    }
    response = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG),
                                  json=body, headers=env["admin"])
    assert response.status_code in (200, 201), response.text
    store.mutate_file(DIE, CONFIG, "production.json", lambda p: p.update(current_revision="4.7"))


def _stub_pdf(monkeypatch, captured=None):
    from motor_ai_sim.production import process_pdf as PDF

    def render(_html, path, *, step_images=None, **_kwargs):
        if captured is not None:
            captured.update(step_images or {})
        Path(path).write_bytes(b"%PDF-1.4 synthetic process PDF")
    monkeypatch.setattr(PDF, "render_process_pdf", render)


def test_process_pdf_uses_current_step_document_selection(prod_env, monkeypatch):
    _create(prod_env)
    captured = {}
    _stub_pdf(monkeypatch, captured)
    current_part, duplicate_part, old_part, current_explicit, unselected, current_photo, old_photo, cross_photo = (
        "currentpartfile123", "duplicatepartfile1", "oldpartfile123456", "explicitfile123456",
        "unselectedfile12", "currentphoto12345", "oldphoto123456789", "crossphoto1234567")
    for file_id in (current_part, duplicate_part, old_part, current_explicit, unselected,
                    current_photo, old_photo, cross_photo):
        store.save_uploaded_file(DIE, CONFIG, file_id, b"image bytes")
    store.save_file(DIE, CONFIG, "parts.json", {"parts": [{
        "id": "part-1", "name": {"en": "Bracket", "zh": "支架"}, "drawing_files": [
            {"file_id": current_part, "kind": "png", "name": "bracket.png"},
            # Duplicate-name document and cross-step photo aliases must stay excluded by their rows.
            {"file_id": duplicate_part, "kind": "png", "name": "bracket.png"},
            # This alias must not reintroduce an uploaded image whose document row is superseded.
            {"file_id": old_part, "kind": "png", "name": "bracket.png"},
            {"file_id": cross_photo, "kind": "png", "name": "cross-step.png"},
        ],
    }]})
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [
        {"id": "current-part", "file_id": current_part, "name": "bracket.png", "part_id": "part-1"},
        {"id": "duplicate-current-part", "file_id": duplicate_part, "name": "bracket.png", "part_id": "part-1"},
        {"id": "old-part", "file_id": old_part, "name": "[superseded] bracket.png", "part_id": "part-1"},
        {"id": "explicit-current", "file_id": current_explicit, "name": "inspection.png"},
        {"id": "unselected", "file_id": unselected, "name": "other.png"},
        {"id": "current-photo", "file_id": current_photo, "name": "current-photo.png", "step_id": "s1"},
        {"id": "old-photo", "file_id": old_photo, "name": "[superseded] old-photo.png", "step_id": "s1"},
        {"id": "cross-step-photo", "file_id": cross_photo, "name": "cross-step.png", "step_id": "s2"},
    ]})
    store.save_file(DIE, CONFIG, "assembly_process.json", {"steps": [{
        "id": "s1", "n": 1, "title": {"en": "Assemble", "zh": "Assemble"},
        "parts": ["part-1"], "documents": ["explicit-current", "cross-step-photo"], "sections": [],
    }]})

    response = prod_env["client"].get(
        "/api/production/%s/%s/process_document?download=true" % (DIE, CONFIG),
        headers=prod_env["engineer"])
    assert response.status_code == 200, response.text
    assert {item["file_id"] for item in captured[1]} == {current_part, current_explicit, current_photo}


def test_direct_process_pdf_download_is_logged_and_audited(prod_env, monkeypatch):
    from motor_ai_sim import model3d as M

    _create(prod_env)
    _stub_pdf(monkeypatch)
    log_path = prod_env["root"].parent / "downloads.jsonl"
    monkeypatch.setattr(M, "download_log_path", lambda: log_path)
    response = prod_env["client"].get(
        "/api/production/%s/%s/process_document?download=true" % (DIE, CONFIG),
        headers=prod_env["engineer"])
    assert response.status_code == 200, response.text
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "production_process_pdf"
    assert row["user"] == ENGINEER and row["revision"] == "4.7"
    assert row["file_count"] == 1 and row["bytes"] == len(response.content)
    assert row["sha256"] == M.sha256_bytes(response.content)
    assert "nda_basis" in row and "ip" in row and row["email"] == ENGINEER
    audits = [json.loads(line) for line in store.audit_path(DIE, CONFIG).read_text(encoding="utf-8").splitlines()]
    records = [entry for entry in audits if entry.get("entity") == "process_document_pdf"]
    assert len(records) == 1
    assert records[0]["snapshot"] == {"revision": "4.7", "lang": "en", "file_count": 1,
                                      "bytes": len(response.content)}


@pytest.mark.parametrize("fail_audit", [False, True])
def test_process_pdf_log_or_audit_failure_returns_503_and_removes_temp_pdf(prod_env, monkeypatch, tmp_path,
                                                                            fail_audit):
    from motor_ai_sim import model3d as M
    from motor_ai_sim.production import routes

    _create(prod_env)
    _stub_pdf(monkeypatch)
    monkeypatch.setattr(M, "download_log_path", lambda: tmp_path / "downloads.jsonl")
    original_mkstemp = routes.tempfile.mkstemp
    monkeypatch.setattr(routes.tempfile, "mkstemp", lambda **kwargs: original_mkstemp(dir=tmp_path, **kwargs))
    if fail_audit:
        monkeypatch.setattr(routes, "_audit", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")))
    else:
        monkeypatch.setattr(M, "log_download", lambda _record: (_ for _ in ()).throw(OSError("read only")))
    response = prod_env["client"].get(
        "/api/production/%s/%s/process_document?download=true" % (DIE, CONFIG),
        headers=prod_env["engineer"])
    assert response.status_code == 503
    assert "Download log unavailable" in response.json()["detail"]
    assert not list(tmp_path.glob("production-process-*.pdf"))
