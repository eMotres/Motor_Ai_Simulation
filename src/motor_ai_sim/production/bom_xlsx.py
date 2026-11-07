"""BOM list of one production to and from Excel (.xlsx), owner 2026-11-05.

The file is read twice: first for a preview that writes nothing, then, after the editor confirms, for the same plan again.  A plan
that no longer matches what the editor saw is refused (``plan_id``), and a file with any row error applies nothing.

What an import never does: delete.  A part that is not in the file stays in the BOM and is listed as ``missing``; a blank cell keeps
the stored value.  Parts match by the ``Part ID`` column, or else by the exact name (EN or ZH, any case) among the parts of this list.

Prices: the motor list stores the cost of the line PER MOTOR ("Price / motor"), the tooling list the price of ONE piece.  A file's
"Unit price" column is the price of one piece, so on the motor list it is multiplied by the quantity per motor.

Quantities are whole numbers because the BOM stores integers; a fractional or zero quantity is a row error, never rounded.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import zipfile
from multiprocessing.pool import ThreadPool
from typing import Any, Callable, Optional

from motor_ai_sim.production import models

MAX_FILE_BYTES = 10 * 1024 * 1024
#: a spreadsheet is a zip: the unpacked size is limited too, so a small file cannot expand into gigabytes of XML
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
MAX_ROWS = 2000
HEADER_SEARCH_ROWS = 30
TRANSLATE_THREADS = 6

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_HEADER_ALIASES = {
    "part_id": ("partid", "零件id"),
    "name": ("part", "partname", "name", "item", "itemname", "component", "零件", "零件名称", "名称", "物料", "物料名称", "部件",
             "деталь", "название", "наименование"),
    "name_en": ("nameen", "partnameen", "englishname", "nameenglish", "英文名称", "英文名"),
    "name_zh": ("namezh", "namecn", "partnamezh", "name中文", "chinesename", "namechinese", "中文名称", "中文名", "名称中文"),
    "kind": ("type", "kind", "类型", "类别", "тип"),
    "qty": ("qty", "quantity", "permotor", "qtypermotor", "quantitypermotor", "每台", "每台数量", "单台用量", "数量", "用量",
            "количество", "колво", "намотор"),
    "material": ("material", "materialfinish", "材料", "材料表面", "材质", "материал"),
    "spec": ("spec", "specs", "specification", "规格", "规格型号", "спецификация"),
    "price_motor": ("pricemotor", "pricepermotor", "单价每台", "每台价格", "ценанамотор"),
    "price_piece": ("unitprice", "price", "priceeach", "单价", "价格", "цена", "ценазашт"),
    "currency": ("currency", "币种", "货币", "валюта"),
    "note": ("note", "notes", "remark", "remarks", "comment", "备注", "примечание", "комментарий"),
}
_HEADER_FIELD = {alias: field for field, aliases in _HEADER_ALIASES.items() for alias in aliases}
_TOTAL_LABELS = {"total", "subtotal", "grandtotal", "合计", "总计", "小计", "итого", "всего"}

_KIND_WORDS = (
    ("made", ("自制", "made", "изготов", "собствен")),
    ("purchased", ("外购", "采购", "标准件", "purchased", "standard", "покуп", "стандарт")),
    ("consumable", ("辅料", "耗材", "consumable", "расход")),
)

_QTY_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:pcs?|шт\.?|个|件|只|台|套|片)?$", re.IGNORECASE)
_MONEY_NOISE_RE = re.compile(r"[\s ¥￥€$]|CNY|RMB|EUR|USD|元", re.IGNORECASE)


def _norm_header(value: Any) -> str:
    return re.sub(r"[\s_()（）/\\.:：,，\-]+", "", str(value or "")).lower()


def _norm_name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _text(value: Any) -> str:
    """A cell as text: a spec typed as the number 608 stays "608", not "608.0"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return str(value).strip()


def _parse_qty(value: Any) -> tuple[Optional[int], Optional[str]]:
    """(quantity, error code).  Blank is (None, None): the stored quantity stays."""
    text = _text(value)
    if not text:
        return None, None
    m = _QTY_RE.match(text)
    if not m:
        return None, "bad_qty"
    number = float(m.group(1).replace(",", "."))
    if not number.is_integer():
        return None, "qty_not_whole"
    if number <= 0:
        return None, "bad_qty"
    return int(number), None


def _parse_money(value: Any) -> tuple[Optional[float], Optional[str]]:
    if value is None or str(value).strip() == "":
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = _MONEY_NOISE_RE.sub("", str(value))
        if "," in text and "." in text:                     # the last separator is the decimal one
            text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")
        try:
            number = float(text)
        except ValueError:
            return None, "bad_price"
    if number < 0 or number != number or number in (float("inf"), float("-inf")):
        return None, "bad_price"
    return round(number, 6), None


def _parse_kind(value: Any) -> tuple[Optional[str], Optional[str]]:
    text = _text(value).lower()
    if not text:
        return None, None
    for kind, words in _KIND_WORDS:
        if any(w in text for w in words):
            return kind, None
    return None, "bad_kind"


def _empty_parse(code: str) -> dict:
    return {"sheet": "", "ignored_columns": [], "rows": [], "errors": [{"row": 0, "code": code, "value": ""}], "skipped_rows": 0}


def _find_header(rows: list) -> Optional[tuple[int, dict, list]]:
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        columns: dict = {}
        ignored: list = []
        for col, cell in enumerate(row):
            field = _HEADER_FIELD.get(_norm_header(cell))
            if field and field not in columns:
                columns[field] = col
            elif _text(cell):
                ignored.append(_text(cell))
        has_name = any(f in columns for f in ("name", "name_en", "name_zh", "part_id"))
        if has_name and "qty" in columns:
            return i, columns, ignored
    return None


def parse_workbook(data: bytes) -> dict:
    """The rows of the first sheet that has a BOM header.  File-level problems come back as an error with ``row`` 0."""
    if len(data) > MAX_FILE_BYTES:
        return _empty_parse("file_too_large")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if sum(i.file_size for i in zf.infolist()) > MAX_UNZIPPED_BYTES:
                return _empty_parse("file_too_large")
    except zipfile.BadZipFile:
        return _empty_parse("not_xlsx")                     # also the old .xls format, which is not a zip

    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                       # noqa: BLE001 - whatever the zip holds, it is not a workbook we can read
        return _empty_parse("not_xlsx")
    try:
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(row)
                if len(rows) > MAX_ROWS + HEADER_SEARCH_ROWS:
                    return _empty_parse("too_many_rows")
            found = _find_header(rows)
            if found:
                return _parse_rows(ws.title, rows, *found)
    finally:
        wb.close()
    return _empty_parse("no_header")


def _parse_rows(sheet: str, rows: list, header_index: int, columns: dict, ignored: list) -> dict:
    out: dict = {"sheet": sheet, "ignored_columns": ignored, "rows": [], "errors": [], "skipped_rows": 0}

    def cell(row: tuple, field: str) -> Any:
        col = columns.get(field)
        return row[col] if col is not None and col < len(row) else None

    for offset, row in enumerate(rows[header_index + 1:]):
        number = header_index + 2 + offset                  # the row number Excel shows
        if not any(_text(c) for c in row):
            continue
        name, name_en, name_zh = _text(cell(row, "name")), _text(cell(row, "name_en")), _text(cell(row, "name_zh"))
        part_id = _text(cell(row, "part_id"))
        label = name or name_en or name_zh
        if _norm_name(label) in _TOTAL_LABELS:
            out["skipped_rows"] += 1
            continue
        if not label and not part_id:
            out["errors"].append({"row": number, "code": "missing_name", "value": ""})
            continue
        qty, qty_error = _parse_qty(cell(row, "qty"))
        kind, kind_error = _parse_kind(cell(row, "kind"))
        price_motor, motor_error = _parse_money(cell(row, "price_motor"))
        price_piece, piece_error = _parse_money(cell(row, "price_piece"))
        currency_text = _text(cell(row, "currency"))
        currency, currency_error = None, None
        if currency_text:
            try:
                currency = models.normalize_currency(currency_text, strict=True)
            except ValueError:
                currency_error = "bad_currency"
        row_errors = [
            (qty_error, cell(row, "qty")), (kind_error, cell(row, "kind")), (motor_error, cell(row, "price_motor")),
            (piece_error, cell(row, "price_piece")), (currency_error, currency_text),
        ]
        bad = [{"row": number, "code": code, "value": _text(value)} for code, value in row_errors if code]
        if bad:
            out["errors"].extend(bad)
            continue
        out["rows"].append({
            "row": number, "part_id": part_id, "name": name, "name_en": name_en, "name_zh": name_zh, "kind": kind, "qty": qty,
            "material": _text(cell(row, "material")), "spec": _text(cell(row, "spec")), "price_motor": price_motor,
            "price_piece": price_piece, "currency": currency, "note": _text(cell(row, "note")),
        })
    return out


def _part_label(part: dict) -> str:
    name = part.get("name") or {}
    return str(name.get("en") or name.get("zh") or part.get("id") or "")


def _row_part_ids(rows: list) -> set:
    return {str(r.get("part_id")) for r in rows if isinstance(r, dict)}


def build_plan(parsed: dict, parts: list, bom_rows: list, other_list_rows: list, cost_rows: list, list_name: str) -> dict:
    """What the file would change.  Writes nothing; the same inputs always give the same ``plan_id``."""
    errors = list(parsed["errors"])
    in_list = _row_part_ids(bom_rows)
    elsewhere = _row_part_ids(other_list_rows)
    candidates = {str(p.get("id")): p for p in parts if isinstance(p, dict) and p.get("kind") != "assembly"
                  and (str(p.get("id")) in in_list or str(p.get("id")) not in elsewhere)}
    by_name: dict = {}
    for pid, part in candidates.items():
        name = part.get("name") or {}
        for text in {_norm_name(name.get("en")), _norm_name(name.get("zh"))} - {""}:
            by_name.setdefault(text, []).append(pid)
    bom_by_id = {str(r.get("part_id")): r for r in bom_rows if isinstance(r, dict)}
    costs_by_id = {str(r.get("part_id")): r for r in cost_rows if isinstance(r, dict)}

    plan_rows: list = []
    seen_parts: set = set()
    seen_new: set = set()
    for row in parsed["rows"]:
        names = [t for t in (row["name"], row["name_en"], row["name_zh"]) if t]
        label = names[0] if names else row["part_id"]
        part: Optional[dict] = None
        if row["part_id"]:
            part = candidates.get(row["part_id"])
            if part is None:
                errors.append({"row": row["row"], "code": "unknown_part_id", "value": row["part_id"]})
                continue
        else:
            matches = {pid for t in names for pid in by_name.get(_norm_name(t), [])}
            if len(matches) > 1:
                errors.append({"row": row["row"], "code": "ambiguous_name", "value": label})
                continue
            if matches:
                part = candidates[next(iter(matches))]
        if part is None:
            new_key = _norm_name(label)
            if new_key in seen_new:
                errors.append({"row": row["row"], "code": "duplicate_part", "value": label})
                continue
            seen_new.add(new_key)
            if row["qty"] is None:
                errors.append({"row": row["row"], "code": "missing_qty", "value": label})
                continue
            plan_rows.append(_new_row(row, label, list_name))
            continue
        pid = str(part["id"])
        if pid in seen_parts:
            errors.append({"row": row["row"], "code": "duplicate_part", "value": label})
            continue
        seen_parts.add(pid)
        plan_rows.append(_existing_row(row, part, bom_by_id.get(pid), costs_by_id.get(pid), list_name))

    missing = [{"part_id": pid, "name": _part_label(candidates[pid])} for pid in bom_by_id
               if pid in candidates and pid not in seen_parts]
    plan = {
        "sheet": parsed["sheet"], "ignored_columns": parsed["ignored_columns"], "skipped_rows": parsed["skipped_rows"],
        "rows": plan_rows, "errors": sorted(errors, key=lambda e: e["row"]), "missing": missing,
        "summary": {
            "new": sum(1 for r in plan_rows if r["action"] == "new"),
            "update": sum(1 for r in plan_rows if r["action"] == "update"),
            "same": sum(1 for r in plan_rows if r["action"] == "same"),
            "missing": len(missing), "errors": len(errors),
        },
    }
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    return plan


def _unit_price(row: dict, qty: int, list_name: str) -> Optional[float]:
    """The stored price: per motor on the motor list (a "Price / motor" column as it is, a "Unit price" times the quantity), per piece on tooling."""
    if list_name:
        return row["price_piece"] if row["price_piece"] is not None else row["price_motor"]
    if row["price_motor"] is not None:
        return row["price_motor"]
    return None if row["price_piece"] is None else round(row["price_piece"] * qty, 6)


def _new_row(row: dict, label: str, list_name: str) -> dict:
    qty = int(row["qty"])
    values = {
        "qty": qty, "kind": row["kind"] or "made", "material": row["material"], "spec": row["spec"],
        "unit_price": _unit_price(row, qty, list_name),
        "currency": row["currency"] or models.DEFAULT_CURRENCY, "note": row["note"],
    }
    changes = [{"field": f, "old": None, "new": values[f]} for f in ("qty", "kind", "material", "spec", "unit_price", "note")
               if values[f] not in (None, "")]
    if values["unit_price"] is not None:
        changes.append({"field": "currency", "old": None, "new": values["currency"]})
    return {"row": row["row"], "action": "new", "part_id": None, "name": label, "changes": changes, "add_row": True,
            "source": {k: row[k] for k in ("name", "name_en", "name_zh")}, "values": values}


def _existing_row(row: dict, part: dict, bom_row: Optional[dict], cost: Optional[dict], list_name: str) -> dict:
    stored_qty = int((bom_row or {}).get("qty_per_motor") or part.get("qty_per_motor") or 1)
    qty = row["qty"] if row["qty"] is not None else stored_qty
    changes: list = []
    values: dict = {}

    def change(field: str, old: Any, new: Any) -> None:
        changes.append({"field": field, "old": old, "new": new})
        values[field] = new

    if qty != stored_qty:
        change("qty", stored_qty, qty)
    if row["kind"] and row["kind"] != part.get("kind"):
        change("kind", part.get("kind"), row["kind"])

    name = part.get("name") or {}
    known = {_norm_name(name.get("en")), _norm_name(name.get("zh"))}
    for lang, text in (("en", row["name_en"]), ("zh", row["name_zh"])):
        if text and _norm_name(text) != _norm_name(name.get(lang)):
            change("name_" + lang, name.get(lang) or None, text)
    if row["name"] and _norm_name(row["name"]) not in known:        # one name column, matched by id: the language shows by its characters
        lang = "zh" if models._CJK_RE.search(row["name"]) else "en"
        if "name_" + lang not in values:
            change("name_" + lang, name.get(lang) or None, row["name"])

    for field, i18n_key in (("material", "material_i18n"), ("spec", "spec_i18n")):
        text = row[field]
        i18n = part.get(i18n_key) or {}
        if text and text not in (i18n.get("en"), i18n.get("zh"), part.get(field)):
            change(field, i18n.get("en") or i18n.get("zh") or part.get(field) or None, text)

    stored_price = (cost or {}).get("unit_price")
    price = _unit_price(row, qty, list_name)
    if price is not None and (stored_price is None or abs(float(stored_price) - price) > 1e-9):
        change("unit_price", stored_price, price)
    if row["currency"] and row["currency"] != (cost or {}).get("currency"):
        change("currency", (cost or {}).get("currency"), row["currency"])
    if row["note"] and row["note"] != (cost or {}).get("note"):
        change("note", (cost or {}).get("note"), row["note"])

    add_row = bom_row is None
    if add_row:
        values["qty"] = qty
    return {"row": row["row"], "action": "update" if changes or add_row else "same", "part_id": str(part["id"]),
            "name": _part_label(part), "changes": changes, "add_row": add_row, "values": values}


def translate_texts(plan: dict, translate_fn: Optional[Callable] = None) -> dict:
    """The material and spec texts of the plan in both languages, each distinct text once and in parallel: a BOM repeats its materials."""
    texts = sorted({str(r["values"][f]) for r in plan["rows"] for f in ("material", "spec") if r["values"].get(f)})

    def both(text: str) -> tuple[str, dict]:
        side = {"zh": text, "en": ""} if models._CJK_RE.search(text) else {"zh": "", "en": text}
        out = models.ensure_i18n_translated(side, translate_fn)
        out["zh"] = out["zh"] or out["en"]                  # a failed translation still shows the text in the other language
        return text, out

    if not texts:
        return {}
    with ThreadPool(min(TRANSLATE_THREADS, len(texts))) as pool:
        return dict(pool.map(both, texts))


def _new_name(source: dict, die: str, config: str) -> dict:
    """A new part's name.  Names are not machine-translated: the other language stays pending, as for every part."""
    en, zh = source["name_en"], source["name_zh"]
    unmapped = False
    if not en and not zh:
        from motor_ai_sim.production.import_package import resolve_part_name
        en, zh, unmapped = resolve_part_name(source["name"], die=die, config=config)
    name = {"en": en, "zh": zh, "src_lang": "en" if en else "zh",
            "status": {"en": "confirmed" if en else "pending", "zh": "confirmed" if zh else "pending"}}
    if unmapped:
        name["unmapped"] = True
    return name


def apply_to_parts(parts_doc: dict, plan: dict, texts: dict, die: str, config: str) -> list:
    """Create and change the parts of the plan; new rows get their ``part_id`` here.  Returns [(action, part_id, snapshot)] for the audit."""
    parts = parts_doc.setdefault("parts", [])
    by_id = {p.get("id"): p for p in parts if isinstance(p, dict)}
    audit: list = []
    for row in plan["rows"]:
        values = row["values"]
        if row["action"] == "new":
            part = models.normalize_part({
                "name": _new_name(row["source"], die, config), "kind": values["kind"], "qty_per_motor": values["qty"],
                "material_i18n": texts.get(values["material"], {"zh": "", "en": ""}),
                "spec_i18n": texts.get(values["spec"], {"zh": "", "en": ""}),
                "description": {"en": "", "zh": "", "src_lang": "en"},
            })
            part["material"] = part["material_i18n"]["zh"]
            part["spec"] = part["spec_i18n"]["zh"]
            parts.append(part)
            row["part_id"] = part["id"]
            audit.append(("create", part["id"], {"part": part, "source": "excel"}))
            continue
        part = by_id.get(row["part_id"])
        if part is None or not (set(values) & {"qty", "kind", "name_en", "name_zh", "material", "spec"}):
            continue
        before = {k: part.get(k) for k in ("qty_per_motor", "kind", "name", "material_i18n", "spec_i18n")}
        if "qty" in values:
            part["qty_per_motor"] = values["qty"]
        if "kind" in values:
            part["kind"] = values["kind"]
        for lang in ("en", "zh"):
            if "name_" + lang in values:
                name = dict(part.get("name") or {})
                name[lang] = values["name_" + lang]
                name["status"] = {**(name.get("status") or {}), lang: "confirmed"}
                part["name"] = name
        for field, i18n_key in (("material", "material_i18n"), ("spec", "spec_i18n")):
            if field in values:
                part[i18n_key] = dict(texts.get(values[field]) or {"zh": values[field], "en": ""})
                part[field] = part[i18n_key]["zh"]
        audit.append(("update", row["part_id"], {"before": before, "patch": values, "source": "excel"}))
    return audit


def apply_to_bom(bom_doc: dict, plan: dict, order_qty: int) -> None:
    rows = bom_doc.setdefault("rows", [])
    by_id = {str(r.get("part_id")): r for r in rows if isinstance(r, dict)}
    for row in plan["rows"]:
        values = row["values"]
        if row["add_row"] and row["part_id"] not in by_id:
            rows.append({"part_id": row["part_id"], "qty_per_motor": values["qty"], "order_total": values["qty"] * order_qty})
        elif "qty" in values and row["part_id"] in by_id:
            by_id[row["part_id"]].update(qty_per_motor=values["qty"], order_total=values["qty"] * order_qty)


def apply_to_costs(costs_doc: dict, plan: dict) -> None:
    """Only the lines the file names; the other lines, the work lines and the margin stay as they are."""
    rows = costs_doc.setdefault("rows", [])
    by_id = {str(r.get("part_id")): r for r in rows if isinstance(r, dict)}
    for row in plan["rows"]:
        values = row["values"]
        if not any(values.get(f) not in (None, "") for f in ("unit_price", "currency", "note")):
            continue
        line = by_id.get(row["part_id"])
        if line is None:
            line = {"part_id": row["part_id"], "unit_price": None, "currency": models.DEFAULT_CURRENCY, "note": None}
            rows.append(line)
            by_id[row["part_id"]] = line
        if values.get("unit_price") is not None:
            line["unit_price"] = values["unit_price"]
        if values.get("currency"):
            line["currency"] = values["currency"]
        if values.get("note"):
            line["note"] = values["note"]


def changes_costs(plan: dict) -> bool:
    return any(values.get(f) not in (None, "") for values in (r["values"] for r in plan["rows"]) for f in ("unit_price", "note"))


def public_plan(plan: dict) -> dict:
    """The plan as the page shows it: what will change, without the internal apply fields."""
    rows = [{k: r[k] for k in ("row", "action", "part_id", "name", "changes")} for r in plan["rows"]]
    return {**plan, "rows": rows}


def build_workbook(parts: list, bom_rows: list, cost_rows: list, list_name: str) -> bytes:
    """The list as a file that imports back unchanged; with no rows yet it is the empty template."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Tooling" if list_name else "BOM"
    price_header = "Unit price" if list_name else "Price / motor"
    qty_header = "Quantity" if list_name else "Per motor"
    ws.append(["Part ID", "Name (EN)", "Name (ZH)", "Type", qty_header, "Material", "Specification", price_header, "Currency", "Note"])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    by_id = {str(p.get("id")): p for p in parts if isinstance(p, dict)}
    costs = {str(c.get("part_id")): c for c in cost_rows if isinstance(c, dict)}
    for row in bom_rows:
        pid = str(row.get("part_id"))
        part = by_id.get(pid)
        if part is None:
            continue
        name = part.get("name") or {}
        material = part.get("material_i18n") or {}
        spec = part.get("spec_i18n") or {}
        cost = costs.get(pid) or {}
        ws.append([
            pid, name.get("en") or None, name.get("zh") or None, part.get("kind"), int(row.get("qty_per_motor") or 1),
            material.get("en") or material.get("zh") or part.get("material") or None,
            spec.get("en") or spec.get("zh") or part.get("spec") or None,
            cost.get("unit_price"), cost.get("currency") if cost.get("unit_price") is not None else None, cost.get("note") or None,
        ])
    for column, width in zip("ABCDEFGHIJ", (20, 32, 24, 12, 10, 28, 28, 12, 9, 28)):
        ws.column_dimensions[column].width = width
    ws.freeze_panes = "A2"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
