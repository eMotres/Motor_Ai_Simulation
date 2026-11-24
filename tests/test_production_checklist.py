"""Mass-production checklist (owner 2026-11-23): generated from the production's own data, statuses kept in the system.

Synthetic production only: nothing here reads a real production."""
from __future__ import annotations

import io

from motor_ai_sim.production import checklist, models, store
from tests.test_production_access import CONFIG, DIE, prod_env  # noqa: F401  (fixture)
from tests.test_production_files import _create

BASE = "/api/production/%s/%s/checklist" % (DIE, CONFIG)


def _bi(en, zh=None):
    return {"en": en, "zh": zh if zh is not None else en, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed"}}


def _seed(env, *, hipot="Hipot 1000 V, 1 s, leakage under 1 mA"):
    _create(env)
    store.save_file(DIE, CONFIG, "assembly_process.json", {"steps": [
        {"id": "a1", "n": 1, "title": _bi("Press the coils", "线圈压制"), "kind": "operation", "parts": ["Coil"], "after": [],
         "sections": [], "documents": [], "note": None},
        {"id": "a2", "n": 2, "title": _bi("Hipot test", "耐压测试"), "kind": "check", "parts": [], "after": ["a1"],
         "sections": ["sec_t"], "documents": [], "note": _bi("Every coil.", "每个线圈。")}]})
    store.save_file(DIE, CONFIG, "process.json", {"sections": [
        {"id": "sec_t", "kind": "tests", "title": _bi("Insulation test", "绝缘测试"), "body": _bi(hipot, "耐压 1000 V"),
         "tables": [], "tables_i18n": {"en": [[["Item", "Limit"], ["Leakage", "1 mA"]]], "zh": []}},
        {"id": "sec_g", "kind": "assembly", "title": _bi("1. General notes", "总则"), "body": _bi("Clean room."),
         "tables": [], "tables_i18n": {"en": [], "zh": []}}]})
    store.save_file(DIE, CONFIG, "parts.json", {"parts": [
        models.normalize_part({"id": "p1", "name": _bi("Coil 40"), "kind": "made", "material": "Cu", "spec": "enamel 200 C"}),
        models.normalize_part({"id": "p2", "name": _bi("Socket head screw M3"), "kind": "purchased"}),
        models.normalize_part({"id": "p3", "name": _bi("Carton", "包装纸箱"), "kind": "purchased"}),
        models.normalize_part({"id": "p4", "name": _bi("Plain thing"), "kind": "purchased"}),   # no material: skipped
        models.normalize_part({"id": "sub", "name": _bi("Stator assembly"), "kind": "assembly", "material": "x"})]})
    store.save_file(DIE, CONFIG, "threads.json", {"threads": [
        models.normalize_thread({"id": "th1", "part_id": "p1", "messages": [{"text": _bi("What is the wire gauge?"), "author_email": "a@b.c"}]}),
        {**models.normalize_thread({"id": "th2", "messages": [{"text": _bi("Done thing")}]}), "status": "closed"}]})
    store.save_file(DIE, CONFIG, "documents.json", {"documents": [
        {"id": "d_cert", "name": "DoC.pdf", "file_id": "f" * 16, "section": "certification", "group": "EU", "cert_status": "draft", "internal": False},
        {"id": "d_int", "name": "internal_cert.pdf", "file_id": "g" * 16, "section": "certification", "cert_status": "draft", "internal": True},
        {"id": "d_ok", "name": "received.pdf", "file_id": "h" * 16, "section": "certification", "cert_status": "evidence_received"},
        {"id": "d_plain", "name": "plain.pdf", "file_id": "i" * 16}]})
    store.save_file(DIE, CONFIG, "revisions.json", {"revisions": [{"number": "1.0", "date": "2026-11-10T08:00:00Z"}]})


def _by_key(data):
    return {i["key"]: i for i in data["items"]}


def test_generator_covers_every_source_with_stable_keys(prod_env):
    _seed(prod_env)
    data = checklist.build(DIE, CONFIG)
    items = _by_key(data)
    assert {"step:a1", "step:a2", "section:sec_t", "section:sec_g", "part:p1", "part:p2", "part:p3", "thread:th1", "cert:d_cert", "cert:d_int"} == set(items)
    assert items["step:a1"]["category"] == "assembly" and items["step:a2"]["category"] == "checks"
    assert items["section:sec_t"]["category"] == "tests" and items["section:sec_g"]["category"] == "process"
    assert items["part:p1"]["category"] == "parts" and items["part:p2"]["category"] == "fasteners" and items["part:p3"]["category"] == "packaging"
    assert items["section:sec_t"]["requirement"]["zh"].startswith("耐压 1000 V")
    assert "Leakage | 1 mA" in items["section:sec_t"]["requirement"]["en"]
    assert items["section:sec_g"]["item"]["en"] == "General notes"            # the old numbering is stripped
    assert "step 2" in items["section:sec_t"]["source"] and items["step:a2"]["source"].startswith("step a2")
    assert [i["no"] for i in data["items"]] == list(range(1, len(data["items"]) + 1))
    assert data["revision"] == {"number": "1.0", "date": "2026-11-10"}
    assert all(i["revision"] == "1.0" and i["status"] == "to_do" and i["open"] for i in data["items"])
    assert {i["responsible"] for i in data["items"]} <= {"factory", "supplier", "MOTRES"}
    # a closed thread, a received certification and a plain document are not items
    assert "thread:th2" not in items and "cert:d_ok" not in items and "cert:d_plain" not in items
    # the key is derived from the source: generating twice gives the same keys and hashes
    again = checklist.build(DIE, CONFIG)
    assert [(i["key"], i["revision_hash"]) for i in again["items"]] == [(i["key"], i["revision_hash"]) for i in data["items"]]


def test_factory_does_not_get_internal_certification_items(prod_env):
    _seed(prod_env)
    c, h = prod_env["client"], prod_env["oem_nda"]
    keys = {i["key"] for i in c.get(BASE, headers=h).json()["items"]}
    assert "cert:d_cert" in keys and "cert:d_int" not in keys


def test_status_is_kept_for_unchanged_items_and_resets_when_the_requirement_changes(prod_env):
    _seed(prod_env)
    c, h = prod_env["client"], prod_env["engineer"]
    r = c.put(BASE, headers=h, json={"key": "section:sec_t", "status": "done", "evidence": {"doc_id": "d_cert", "note": "report 1"}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["evidence"]["name"] == "DoC.pdf" and r.json()["by"] == "engineer@example.com"
    c.put(BASE, headers=h, json={"key": "part:p1", "status": "optional"})
    # regenerate after an unrelated change (a new part): the statuses stay
    parts = store.load_file(DIE, CONFIG, "parts.json")["parts"]
    parts.append(models.normalize_part({"id": "p9", "name": _bi("Bearing"), "kind": "purchased", "material": "steel"}))
    store.save_file(DIE, CONFIG, "parts.json", {"parts": parts})
    items = _by_key(c.get(BASE, headers=h).json())
    assert items["section:sec_t"]["status"] == "done" and not items["section:sec_t"]["open"] and not items["section:sec_t"]["changed"]
    assert items["part:p1"]["status"] == "optional" and items["part:p9"]["status"] == "to_do"
    # the requirement text changes: only that item goes back to "to confirm", the old status is kept for the display
    _seed(prod_env, hipot="Hipot 1500 V, 1 s, leakage under 0.5 mA")
    items = _by_key(c.get(BASE, headers=h).json())
    assert items["section:sec_t"]["status"] == "to_confirm" and items["section:sec_t"]["changed"] is True
    assert items["section:sec_t"]["previous_status"] == "done" and items["section:sec_t"]["open"]
    assert items["part:p1"]["status"] == "optional"
    # confirming it again stores the new text's hash
    r = c.put(BASE, headers=h, json={"key": "section:sec_t", "status": "done"})
    assert r.json()["status"] == "done" and r.json()["changed"] is False
    assert r.json()["evidence"] is None                  # the old evidence belonged to the old text: it is not carried over silently
    c.put(BASE, headers=h, json={"key": "section:sec_t", "status": "done", "evidence": {"doc_id": "d_cert"}})
    r = c.put(BASE, headers=h, json={"key": "section:sec_t", "status": "missing_info"})
    assert r.json()["evidence"]["doc_id"] == "d_cert"    # evidence not mentioned and the text unchanged: the link stays


def test_write_access_validation_and_audit(prod_env):
    _seed(prod_env)
    c = prod_env["client"]
    put = lambda who, body: c.put(BASE, headers=prod_env[who], json=body)
    ok = {"key": "step:a1", "status": "to_confirm"}
    assert put("oem_nda", ok).status_code == 200                              # the factory writes
    assert put("engineer", ok).status_code == 200
    assert c.get(BASE, headers=prod_env["non_member"]).status_code == 404
    assert put("non_member", ok).status_code == 404
    assert put("oem_no_nda", ok).status_code in (403, 404)                    # the NDA gate holds
    assert put("engineer", {"key": "step:a1", "status": "bogus"}).status_code == 422
    assert put("engineer", {"key": "step:nope", "status": "done"}).status_code == 404
    assert put("engineer", {"key": "step:a1", "status": "done", "evidence": {"doc_id": "missing"}}).status_code == 422
    # the factory cannot cite our internal document as evidence
    assert put("oem_nda", {"key": "step:a1", "status": "done", "evidence": {"doc_id": "d_int"}}).status_code == 422
    assert put("engineer", {"key": "step:a1", "status": "done", "evidence": {"doc_id": "d_int"}}).status_code == 200
    e = c.get(BASE, headers=prod_env["engineer"]).json()
    assert e["can_edit"] is True and e["stats"]["total"] == len(e["items"])
    assert c.get(BASE, headers=prod_env["oem_nda"]).json()["can_edit"] is True
    log = [l for l in store.audit_path(DIE, CONFIG).read_text(encoding="utf-8").splitlines() if '"checklist"' in l]
    assert len(log) >= 3 and '"who": "oem_nda@example.com"' in log[0] and '"to": "to_confirm"' in log[0]
    # the factory's view of an internal evidence document does not leak its name
    seen = _by_key(c.get(BASE, headers=prod_env["oem_nda"]).json())["step:a1"]["evidence"]
    assert seen["hidden"] is True and seen["name"] is None


def test_xlsx_export_has_three_bilingual_sheets_and_no_import(prod_env):
    from openpyxl import load_workbook
    _seed(prod_env)
    c = prod_env["client"]
    c.put(BASE, headers=prod_env["engineer"], json={"key": "section:sec_t", "status": "done"})
    r = c.get(BASE + ".xlsx", headers=prod_env["engineer"])
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert len(wb.sheetnames) == 3
    main, tracker, legend = wb.worksheets
    head = [str(c_.value) for c_ in main[1]]
    assert head[0].startswith("No") and "Requirement" in head[3] and "验证方式" in head[4] and "Source" in head[9]
    rows = list(main.iter_rows(min_row=2, values_only=True))
    n = len(checklist.build(DIE, CONFIG)["items"])
    assert len(rows) == n
    done = [r_ for r_ in rows if r_[9].startswith("section sec_t")][0]
    assert done[8] == "N" and "Done" in done[7] and "已完成" in done[7]
    assert "耐压 1000 V" in done[3]
    assert len(list(tracker.iter_rows(min_row=2, values_only=True))) == n - 1       # open items only
    flat = [str(v) for row in legend.iter_rows(values_only=True) for v in row if v is not None]
    assert any("Open items" in v for v in flat) and any("工艺要求" in v for v in flat)
    assert c.get(BASE + ".xlsx", headers=prod_env["non_member"]).status_code == 404
    assert c.post(BASE + ".xlsx", headers=prod_env["engineer"], data=b"x").status_code in (404, 405)


def test_xlsx_text_never_becomes_a_formula(prod_env):
    from openpyxl import load_workbook
    _seed(prod_env)
    parts = store.load_file(DIE, CONFIG, "parts.json")["parts"]
    parts.append(models.normalize_part({"id": "px", "name": _bi("=HYPERLINK(\"http://x\",\"y\")"), "kind": "made", "material": "steel"}))
    store.save_file(DIE, CONFIG, "parts.json", {"parts": parts})
    r = prod_env["client"].get(BASE + ".xlsx", headers=prod_env["engineer"])
    ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
    cells = [c_ for row in ws.iter_rows(min_row=2) for c_ in row if isinstance(c_.value, str) and c_.value.startswith("=HYPERLINK")]
    assert cells and all(c_.data_type == "s" for c_ in cells)


def test_merge_status_pure():
    items = [{"key": "k1", "revision_hash": "h1"}, {"key": "k2", "revision_hash": "h2"}, {"key": "k3", "revision_hash": "h3"}]
    stored = {"k1": {"status": "done", "revision_hash": "h1", "by": "x", "at": "t"},
              "k2": {"status": "missing_info", "revision_hash": "OLD"}, "k3": {"status": "weird", "revision_hash": "h3"}}
    out = {i["key"]: i for i in checklist.merge_status(items, stored)}
    assert (out["k1"]["status"], out["k1"]["open"], out["k1"]["changed"]) == ("done", False, False)
    assert (out["k2"]["status"], out["k2"]["previous_status"], out["k2"]["open"]) == ("to_confirm", "missing_info", True)
    assert out["k3"]["status"] == "to_do"                                         # an unknown stored status is ignored
    assert checklist.merge_status(items, None)[0]["status"] == "to_do"
