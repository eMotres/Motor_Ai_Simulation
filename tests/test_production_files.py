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
    # the account is named (owner 2026-10-20: the 3-D downloads list showed "(unknown)" for every production file)
    assert rows[-1]["user"] == ENGINEER.lower() and rows[-1]["email"] == ENGINEER.lower(), rows[-1]


def test_old_rows_without_the_account_name_it_from_their_nda_record(tmp_path, monkeypatch):
    from motor_ai_sim import model3d as M, nda as N
    log = tmp_path / "downloads.jsonl"
    monkeypatch.setattr(M, "download_log_path", lambda: log)
    monkeypatch.setattr(N, "get_record", lambda rid: {"id": rid, "email": "buyer@example.com"} if rid == "n1" else None)
    old = {"download_id": "d1", "kind": "production_file", "user": None, "email": "", "name": "unknown",
           "nda_basis": {"type": "online", "record_id": "n1"}}
    log.write_text(json.dumps(old) + chr(10), encoding="utf-8")
    row = M.read_log()[0]
    assert row["email"] == "buyer@example.com" and row["account_from"] == "nda_record"
    assert M.read_log(user="buyer@")[0]["download_id"] == "d1"
    assert json.loads(log.read_text(encoding="utf-8"))["email"] == ""      # the log itself is not rewritten


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


def test_oem_cannot_rewrite_design_text_via_translation_confirm(prod_env):
    _create(prod_env)
    c = prod_env["client"]
    part = {"name": {"en": "Shaft", "zh": "", "src_lang": "en", "status": {"en": "confirmed", "zh": "pending"}},
            "kind": "made", "qty_per_motor": 1}
    r = c.post("/api/production/%s/%s/parts" % (DIE, CONFIG), json=part, headers=prod_env["engineer"])
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    body = {"entity": "part", "entity_id": pid, "field": "name", "lang": "en", "text": "HACKED"}
    r = c.post("/api/production/%s/%s/translate/confirm" % (DIE, CONFIG), json=body, headers=prod_env["oem_nda"])
    assert r.status_code in (403, 404), r.text
    names = [p["name"]["en"] for p in c.get("/api/production/%s/%s/parts" % (DIE, CONFIG), headers=prod_env["admin"]).json()["parts"]]
    assert "HACKED" not in names


def test_slug_cannot_escape_the_production_root(tmp_path, monkeypatch):
    import pytest
    from motor_ai_sim.production import store
    monkeypatch.setenv("MOTRES_PRODUCTION_ROOT", str(tmp_path / "production"))
    for die, cfg in (("..", ".."), ("....", "x"), ("../..", "L12"), (".", ".")):
        p = store.path_for(die, cfg)
        assert (tmp_path / "production").resolve() in p.parents, (die, cfg, p)
    assert store._safe_slug("CIANO14 40 new") == "CIANO14_40_new"


def test_factory_posts_a_picture_to_a_step(prod_env):
    """Owner 2026-10-18: pictures in every production step - any member posts, the document carries the step id."""
    _create(prod_env)
    c = prod_env["client"]
    url = "/api/production/%s/%s/documents" % (DIE, CONFIG)
    r = c.post(url, headers=prod_env["oem_nda"], data={"step_id": "s5"},
               files={"file": ("rotor_film.jpg", b"\xff\xd8\xff jpeg", "image/jpeg")})
    assert r.status_code == 200, r.text
    assert r.json()["step_id"] == "s5" and r.json()["part_id"] is None
    assert c.post(url, headers=prod_env["oem_nda"], data={"step_id": "../x"},
                  files={"file": ("a.jpg", b"x", "image/jpeg")}).status_code == 422
