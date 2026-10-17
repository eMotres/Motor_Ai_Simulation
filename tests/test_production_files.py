"""Production files: every member (OEM included) may upload; downloads go into the traced model3d download log
with the original file name; ids that are not server-generated are refused (owner 2026-10-17)."""
from __future__ import annotations

import json

from tests.test_production_access import ADMIN, CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)


def _create(env):
    payload = {"title": {"en": "P", "zh": "P", "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}},
               "order_quantity": 100,
               "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]}
    r = env["client"].post("/api/admin/production/%s/%s" % (DIE, CONFIG), json=payload, headers=env["admin"])
    assert r.status_code in (200, 201), r.text


def test_oem_uploads_and_download_is_traced(prod_env, tmp_path, monkeypatch):
    from motor_ai_sim import model3d as M
    log = tmp_path / "downloads.jsonl"
    monkeypatch.setattr(M, "download_log_path", lambda: log)
    _create(prod_env)
    c = prod_env["client"]
    r = c.post("/api/production/%s/%s/documents" % (DIE, CONFIG), headers=prod_env["oem_nda"],
               files={"file": ("test_report_汇总.pdf", b"%PDF-1.4 bench data", "application/pdf")})
    assert r.status_code == 200, r.text
    fid = r.json()["file_id"]
    d = c.get("/api/production/%s/%s/files/%s" % (DIE, CONFIG, fid), headers=prod_env["engineer"])
    assert d.status_code == 200 and d.content == b"%PDF-1.4 bench data"
    assert "filename*=UTF-8''test_report_" in d.headers["content-disposition"]
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows and rows[-1]["kind"] == "production_file" and rows[-1]["file"] == "test_report_汇总.pdf"
    assert rows[-1]["sha256"] and rows[-1]["die"] == DIE and rows[-1]["config"] == CONFIG


def test_oem_still_cannot_edit_design_data(prod_env):
    _create(prod_env)
    r = prod_env["client"].put("/api/production/%s/%s/bom" % (DIE, CONFIG), json={"rows": []}, headers=prod_env["oem_nda"])
    assert r.status_code in (403, 404, 405), r.text


def test_bad_file_ids_are_refused(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    for bad in ("...", "abc", "x" * 80, "....%2F....%2Fproduction.json"):
        r = c.get("/api/production/%s/%s/files/%s" % (DIE, CONFIG, bad), headers=prod_env["admin"])
        assert r.status_code in (400, 404), (bad, r.status_code)
