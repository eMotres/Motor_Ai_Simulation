"""BOM list to and from Excel (owner 2026-11-05): preview writes nothing, apply only the previewed plan, nothing is ever deleted."""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook, load_workbook

from tests.test_production_access import CONFIG, DIE, ENGINEER, OEM_NDA, prod_env  # noqa: F401  (fixture)

BASE = f"/api/production/{DIE}/{CONFIG}"
HEAD = ["Name", "Type", "Qty", "Material", "Spec", "Unit price", "Currency", "Note"]


def _bi(en: str, zh: str = "") -> dict:
    return {"en": en, "zh": zh, "src_lang": "en", "status": {"en": "confirmed", "zh": "confirmed" if zh else "pending"}}


def _xlsx(*rows, title: str = "Sheet1") -> bytes:
    wb = Workbook()
    wb.active.title = title
    for row in rows:
        wb.active.append(list(row))
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _post(c, headers, data: bytes, plan_id: str | None = None, list_name: str = ""):
    url = f"{BASE}/bom/import" + (f"?list={list_name}" if list_name else "")
    return c.post(url, headers=headers, files={"file": ("bom.xlsx", data)}, data={"plan_id": plan_id} if plan_id else {})


def _apply(c, headers, data: bytes, list_name: str = "") -> dict:
    preview = _post(c, headers, data, list_name=list_name)
    assert preview.status_code == 200, preview.text
    done = _post(c, headers, data, preview.json()["plan_id"], list_name)
    assert done.status_code == 200, done.text
    return done.json()


@pytest.fixture(autouse=True)
def no_translation(monkeypatch):
    """A marked translation: no network, and the tests can see which texts went through it."""
    from motor_ai_sim.production import translate as T
    monkeypatch.setattr(T, "translate_text", lambda text, src, tgt: f"{tgt}:{text}")


@pytest.fixture()
def prod(prod_env):
    c, admin, eng = prod_env["client"], prod_env["admin"], prod_env["engineer"]
    r = c.post(f"/api/admin/production/{DIE}/{CONFIG}", headers=admin, json={
        "title": _bi("Excel BOM test"), "order_quantity": 10, "share_costs_with_oem": False,
        "members": [{"email": ENGINEER, "role": "engineer"}, {"email": OEM_NDA, "role": "oem"}]})
    assert r.status_code in (200, 201), r.text
    shaft = c.post(f"{BASE}/parts", headers=eng, json={"name": _bi("Shaft", "主轴"), "kind": "made", "qty_per_motor": 1, "material": "40Cr"}).json()
    screw = c.post(f"{BASE}/parts", headers=eng, json={"name": _bi("Screw M4"), "kind": "purchased", "qty_per_motor": 6}).json()
    c.put(f"{BASE}/costs", headers=eng, json={"rows": [{"part_id": screw["id"], "unit_price": 3.0, "currency": "EUR", "note": "keep"}],
                                              "extras": [{"name": _bi("Packaging"), "amount": 15}], "margin_pct": 12})
    return {**prod_env, "shaft": shaft, "screw": screw}


def _bom(c, headers, list_name: str = "") -> dict:
    url = f"{BASE}/bom" + (f"?list={list_name}" if list_name else "")
    return {r["part_id"]: r for r in c.get(url, headers=headers).json()["rows"]}


def test_preview_writes_nothing(prod):
    c, eng = prod["client"], prod["engineer"]
    before = (_bom(c, eng), c.get(f"{BASE}/parts", headers=eng).json(), c.get(f"{BASE}/documents", headers=eng).json())
    plan = _post(c, eng, _xlsx(HEAD, ["Bearing 608", "purchased", 2, "", "608-2RS", 12.5, "CNY", ""], ["Shaft", "", 3])).json()
    assert plan["summary"] == {"new": 1, "update": 1, "same": 0, "missing": 1, "errors": 0}
    assert (_bom(c, eng), c.get(f"{BASE}/parts", headers=eng).json(), c.get(f"{BASE}/documents", headers=eng).json()) == before
    assert "values" not in plan["rows"][0] and "source" not in plan["rows"][0]


def test_apply_adds_new_parts_and_keeps_what_the_file_does_not_name(prod):
    c, eng = prod["client"], prod["engineer"]
    out = _apply(c, eng, _xlsx(HEAD, ["Bearing 608", "外购", 2, "Steel", "608-2RS", 12.5, "CNY", "SKF"], ["主轴", "", 3]))
    assert out["applied"] is True
    bom = _bom(c, eng)
    bearing_id = next(r["part_id"] for r in out["rows"] if r["action"] == "new")
    assert bom[bearing_id]["qty_per_motor"] == 2 and bom[bearing_id]["order_total"] == 20
    assert bom[prod["shaft"]["id"]]["qty_per_motor"] == 3 and bom[prod["shaft"]["id"]]["order_total"] == 30
    assert bom[prod["screw"]["id"]]["qty_per_motor"] == 6                       # not in the file: untouched, listed as missing
    assert [m["part_id"] for m in out["missing"]] == [prod["screw"]["id"]]

    parts = {p["id"]: p for p in c.get(f"{BASE}/parts", headers=eng).json()["parts"]}
    assert parts[bearing_id]["kind"] == "purchased"
    assert parts[bearing_id]["material_i18n"] == {"en": "Steel", "zh": "zh:Steel"}
    assert parts[bearing_id]["name"]["status"]["zh"] == "pending"               # part names are not machine-translated
    assert parts[prod["shaft"]["id"]]["qty_per_motor"] == 3
    assert parts[prod["shaft"]["id"]]["material"] == "40Cr"                     # blank cell keeps the value

    costs = c.get(f"{BASE}/costs", headers=eng).json()
    by_id = {r["part_id"]: r for r in costs["rows"]}
    assert by_id[bearing_id]["unit_price"] == 25.0 and by_id[bearing_id]["note"] == "SKF"    # 12.5 per piece x 2 per motor
    assert by_id[prod["screw"]["id"]]["unit_price"] == 3.0 and by_id[prod["screw"]["id"]]["currency"] == "EUR"
    assert costs["margin_pct"] == 12 and [e["amount"] for e in costs["extras"]] == [15]


def test_price_per_motor_column_is_kept_as_it_is_and_tooling_is_per_piece(prod):
    c, eng = prod["client"], prod["engineer"]
    _apply(c, eng, _xlsx(["Name", "Per motor", "Price / motor"], ["Screw M4", 6, 18.0]))
    assert next(r for r in c.get(f"{BASE}/costs", headers=eng).json()["rows"] if r["part_id"] == prod["screw"]["id"])["unit_price"] == 18.0

    out = _apply(c, eng, _xlsx(["Name", "Quantity", "Unit price"], ["Press plate", 2, 50.0]), "tooling")
    pid = out["rows"][0]["part_id"]
    assert pid in _bom(c, eng, "tooling") and pid not in _bom(c, eng)
    tool = next(r for r in c.get(f"{BASE}/costs?list=tooling", headers=eng).json()["rows"] if r["part_id"] == pid)
    assert tool["unit_price"] == 50.0


def test_a_part_of_the_other_list_is_not_matched_by_name(prod):
    c, eng = prod["client"], prod["engineer"]
    out = _apply(c, eng, _xlsx(["Name", "Qty"], ["Shaft", 1]), "tooling")
    assert out["summary"]["new"] == 1
    assert out["rows"][0]["part_id"] != prod["shaft"]["id"]


@pytest.mark.parametrize("row, code", [
    (["Bearing", "", 0], "bad_qty"),
    (["Bearing", "", -2], "bad_qty"),
    (["Bearing", "", "many"], "bad_qty"),
    (["Bearing", "", 1.5], "qty_not_whole"),
    (["Bearing", "", ""], "missing_qty"),
    (["Bearing", "unknown word", 1], "bad_kind"),
    (["Bearing", "", 1, "", "", "free", ""], "bad_price"),
    (["Bearing", "", 1, "", "", -1, ""], "bad_price"),
    (["Bearing", "", 1, "", "", 1, "GBP"], "bad_currency"),
    (["", "", 1], "missing_name"),
])
def test_a_row_error_blocks_the_whole_file(prod, row, code):
    c, eng = prod["client"], prod["engineer"]
    data = _xlsx(HEAD, ["Shaft", "", 9], row)
    plan = _post(c, eng, data).json()
    assert [e["code"] for e in plan["errors"]] == [code]
    assert plan["errors"][0]["row"] == 3
    assert _post(c, eng, data, plan["plan_id"]).status_code == 422
    assert _bom(c, eng)[prod["shaft"]["id"]]["qty_per_motor"] == 1


def test_duplicate_and_unknown_id_are_errors(prod):
    c, eng = prod["client"], prod["engineer"]
    plan = _post(c, eng, _xlsx(["Part ID", "Name", "Qty"], ["", "Shaft", 1], ["", "主轴", 2], ["", "New", 1], ["", "new", 2], ["pt_nope", "", 1])).json()
    assert sorted(e["code"] for e in plan["errors"]) == ["duplicate_part", "duplicate_part", "unknown_part_id"]


def test_ambiguous_name_is_refused_not_guessed(prod):
    c, eng = prod["client"], prod["engineer"]
    c.post(f"{BASE}/parts", headers=eng, json={"name": _bi("Shaft"), "kind": "made", "qty_per_motor": 1})
    plan = _post(c, eng, _xlsx(["Name", "Qty"], ["Shaft", 2])).json()
    assert [e["code"] for e in plan["errors"]] == ["ambiguous_name"]


@pytest.mark.parametrize("data, code", [
    (b"this is not a workbook", "not_xlsx"),
    (_xlsx(["Just", "some"], ["other", "table"]), "no_header"),
])
def test_file_level_errors(prod, data, code):
    plan = _post(prod["client"], prod["engineer"], data).json()
    assert [e["code"] for e in plan["errors"]] == [code]
    assert plan["errors"][0]["row"] == 0


def test_chinese_factory_header_and_junk_above_it(prod):
    c, eng = prod["client"], prod["engineer"]
    plan = _post(c, eng, _xlsx(
        ["物料清单 G2"], [],
        ["序号", "零件", "类型", "每台", "订单合计（10 台）", "材料 / 表面", "规格", "图纸 / 要求", "备注"],
        [1, "主轴", "自制", 1, 10, "40Cr", "", "", ""],
        [2, "轴承", "外购", "2个", 20, "", "608", "", "SKF"],
        ["", "合计", "", "", 30, "", "", "", ""])).json()
    assert plan["errors"] == []
    assert plan["summary"]["new"] == 1 and plan["summary"]["same"] == 1 and plan["skipped_rows"] == 1
    assert set(plan["ignored_columns"]) == {"序号", "订单合计（10 台）", "图纸 / 要求"}


def test_stale_preview_is_refused(prod):
    c, eng = prod["client"], prod["engineer"]
    data = _xlsx(["Name", "Qty"], ["Shaft", 5])
    plan_id = _post(c, eng, data).json()["plan_id"]
    rows = list(_bom(c, eng).values())
    c.put(f"{BASE}/bom", headers=eng, json={"rows": [{**r, "qty_per_motor": 7 if r["part_id"] == prod["shaft"]["id"] else r["qty_per_motor"]} for r in rows]})
    assert _post(c, eng, data, plan_id).status_code == 409
    assert _bom(c, eng)[prod["shaft"]["id"]]["qty_per_motor"] == 7


def test_export_imports_back_unchanged(prod):
    c, eng = prod["client"], prod["engineer"]
    r = c.get(f"{BASE}/bom.xlsx", headers=eng)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(r.content)).active
    assert [ws.cell(1, i).value for i in (1, 5, 8)] == ["Part ID", "Per motor", "Price / motor"]
    plan = _post(c, eng, r.content).json()
    assert plan["errors"] == [] and plan["summary"]["same"] == 2 and plan["summary"]["new"] == plan["summary"]["update"] == 0

    documents = c.get(f"{BASE}/documents", headers=eng).json()["documents"]
    assert _post(c, eng, r.content, plan["plan_id"]).status_code == 200
    assert c.get(f"{BASE}/documents", headers=eng).json()["documents"] == documents   # nothing to do: nothing stored


def test_an_edit_by_id_renames_and_changes_material(prod):
    c, eng = prod["client"], prod["engineer"]
    _apply(c, eng, _xlsx(["Part ID", "Name (EN)", "Name (ZH)", "Qty", "Material"], [prod["screw"]["id"], "Screw M5", "螺钉", 4, "不锈钢"]))
    part = next(p for p in c.get(f"{BASE}/parts", headers=eng).json()["parts"] if p["id"] == prod["screw"]["id"])
    assert part["name"]["en"] == "Screw M5" and part["name"]["zh"] == "螺钉" and part["name"]["status"]["zh"] == "confirmed"
    assert part["material_i18n"] == {"zh": "不锈钢", "en": "en:不锈钢"} and part["material"] == "不锈钢"


def test_uploaded_file_is_kept_internal_and_the_factory_never_sees_it(prod):
    c, eng, oem = prod["client"], prod["engineer"], prod["oem_nda"]
    data = _xlsx(HEAD, ["Bearing", "purchased", 2, "", "", 12.5, "CNY", ""])
    out = _apply(c, eng, data)
    doc = next(d for d in c.get(f"{BASE}/documents", headers=eng).json()["documents"] if d["id"] == out["document_id"])
    assert doc["internal"] is True and doc["name"] == "bom.xlsx"
    assert c.get(f"{BASE}/files/{doc['file_id']}", headers=eng).content == data
    assert out["document_id"] not in {d["id"] for d in c.get(f"{BASE}/documents", headers=oem).json()["documents"]}
    assert c.get(f"{BASE}/files/{doc['file_id']}", headers=oem).status_code in (403, 404)


def test_only_editors_import_and_export(prod):
    c = prod["client"]
    data = _xlsx(["Name", "Qty"], ["Shaft", 5])
    assert _post(c, prod["oem_nda"], data).status_code == 403
    assert c.get(f"{BASE}/bom.xlsx", headers=prod["oem_nda"]).status_code == 403
    assert _post(c, prod["non_member"], data).status_code == 404
    assert c.get(f"{BASE}/bom.xlsx", headers=prod["non_member"]).status_code == 404
    assert _post(c, prod["admin"], data).status_code == 200


def test_audit_records_before_and_after_and_keeps_prices_out_of_the_bom_entry(prod):
    c, eng = prod["client"], prod["engineer"]
    _apply(c, eng, _xlsx(HEAD, ["Shaft", "", 4, "", "", 100.0, "CNY", ""]))
    lines = next(prod["root"].rglob("audit*")).read_text(encoding="utf-8")
    import json
    events = [json.loads(line) for line in lines.splitlines()]
    bom_event = [e for e in events if e["entity"] == "bom"][-1]
    assert bom_event["snapshot"]["source"] == "excel"
    assert [r["qty_per_motor"] for r in bom_event["snapshot"]["before"] if r["part_id"] == prod["shaft"]["id"]] == [1]
    assert "unit_price" not in json.dumps(bom_event["snapshot"])
    assert [e for e in events if e["entity"] == "costs"][-1]["snapshot"]["source"] == "excel"
