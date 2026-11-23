"""Mass-production checklist of one production, GENERATED from the production's own data (owner 2026-11-23).

Rule: the production module is the only source.  Nothing here is typed twice: the items come from the assembly steps, the process sections
(requirements and test limits), the parts (material, coating, finish, fasteners, packaging), the open Discussion threads and the
certification evidence not yet received.  What is stored is only the STATUS of each item (``checklist_status.json``: item key ->
``{status, evidence, by, at, revision_hash, revision}``).

Identity: an item key is derived from its source (``step:<id>``, ``section:<id>``, ``part:<id>``, ``thread:<id>``, ``cert:<doc id>``), so a
regenerated list keeps the status of every unchanged item.  ``revision_hash`` is a hash of the item's own text (title and requirement,
English and Chinese); when the source text changed since the status was set, the item is shown as ``to_confirm`` again.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from motor_ai_sim.production import models, process_steps as PS

FILE_NAME = "checklist_status.json"

STATUSES = ("to_do", "to_confirm", "missing_info", "optional", "done")
#: statuses that close an item: everything else is an open item
CLOSED = ("done", "optional")
RESPONSIBLE = ("factory", "supplier", "MOTRES")

#: label, English and Chinese
STATUS_LABELS: Dict[str, tuple] = {
    "to_do": ("To do", "待办"),
    "to_confirm": ("To confirm", "待确认"),
    "missing_info": ("Missing info", "信息缺失"),
    "optional": ("Optional", "可选"),
    "done": ("Done", "已完成"),
}
RESPONSIBLE_LABELS: Dict[str, tuple] = {"factory": ("Factory", "工厂"), "supplier": ("Supplier", "供应商"), "MOTRES": ("MOTRES", "MOTRES")}

#: category key -> (English, Chinese); the order of this table is the order of the list
CATEGORIES: Dict[str, tuple] = {
    "assembly": ("Assembly steps", "装配工序"),
    "checks": ("Checks in the process", "过程检验"),
    "process": ("Process requirements", "工艺要求"),
    "tests": ("Test limits", "测试限值"),
    "parts": ("Parts, materials and finish", "零件、材料与表面处理"),
    "fasteners": ("Fasteners", "紧固件"),
    "packaging": ("Packaging", "包装"),
    "discussion": ("Open discussion items", "未决讨论事项"),
    "certification": ("Certification evidence", "认证证据"),
}

_HOW = {
    "step": ("Operator record and first-article inspection", "操作记录与首件检验"),
    "check": ("Measurement or test record of every unit", "逐台测量或测试记录"),
    "section": ("Process control per the requirement text", "按要求文本进行过程控制"),
    "tests": ("Test report with the measured values", "含实测值的测试报告"),
    "part": ("Incoming inspection against the drawing and the material certificate", "按图纸和材料证明来料检验"),
    "thread": ("Answered and closed in the Discussion", "在讨论区答复并关闭"),
    "cert": ("Evidence document uploaded and marked received", "上传证据文件并标记已收到"),
}

_PACKAGING_RE = re.compile(r"packag|carton|包装|纸箱|foam|泡棉|\bbox\b", re.IGNORECASE)
_FASTENER_RE = re.compile(r"\bscrew|\bbolt|\bnut\b|washer|circlip|retaining ring|fastener|螺钉|螺栓|螺母|垫圈|卡簧|紧固", re.IGNORECASE)


def _bi(value: Any) -> tuple:
    """(en, zh) of a bilingual value; a missing side falls back to the other, so a cell is never empty when one language exists."""
    if isinstance(value, dict):
        en, zh = str(value.get("en") or "").strip(), str(value.get("zh") or "").strip()
    else:
        en = zh = str(value or "").strip()
    return en or zh, zh or en


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _tables_text(section: dict, lang: str) -> str:
    ti = section.get("tables_i18n") or {}
    tables = ti.get(lang) or section.get("tables") or []
    lines: List[str] = []
    for tb in tables:
        if not isinstance(tb, list):
            continue
        for row in tb:
            cells = row if isinstance(row, list) else [row]
            line = " | ".join(str(c) for c in cells if str(c).strip())
            if line:
                lines.append(line)
    return "\n".join(lines)


def _item(key: str, category: str, item: tuple, requirement: tuple, how: str, responsible: str, source: str, *,
          source_kind: str, order: int) -> dict:
    ident = [_norm(item[0]), _norm(item[1]), _norm(requirement[0]), _norm(requirement[1])]
    digest = hashlib.sha256(json.dumps(ident, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {
        "key": key, "category": category, "item": {"en": item[0], "zh": item[1]},
        "requirement": {"en": requirement[0], "zh": requirement[1]},
        "how": {"en": _HOW[how][0], "zh": _HOW[how][1]}, "responsible": responsible, "source": source,
        "source_kind": source_kind, "revision_hash": digest, "_order": order,
    }


def _revision_info(production: dict, revisions: list) -> dict:
    number = str((production or {}).get("current_revision") or "1.0")
    date = None
    for r in revisions:
        if isinstance(r, dict) and str(r.get("number")) == number:
            date = str(r.get("date") or "")[:10] or None
    return {"number": number, "date": date}


def generate(die: str, config: str, *, include_internal: bool = True) -> dict:
    """The current checklist as ``{revision, items}`` (no statuses yet), read from the production's stored files.

    ``include_internal`` False (the factory) leaves out the certification documents marked internal."""
    from motor_ai_sim.production import access, store

    production = access.load_production(die, config) or {}
    parts = [p for p in (store.load_file(die, config, "parts.json").get("parts") or []) if isinstance(p, dict)]
    process = store.load_file(die, config, "process.json").get("sections") or []
    sections = [s for s in process if isinstance(s, dict)]
    asm = store.load_file(die, config, "assembly_process.json")
    steps = [s for s in (PS.view(asm, parts, models.product_type_of(production))["steps"]) if isinstance(s, dict)]
    documents = [d for d in (store.load_file(die, config, "documents.json").get("documents") or []) if isinstance(d, dict)]
    threads = [t for t in (store.load_file(die, config, "threads.json").get("threads") or []) if isinstance(t, dict)]
    revisions = store.load_file(die, config, "revisions.json").get("revisions") or []

    rev = _revision_info(production, revisions)
    items: List[dict] = []
    n = 0

    def add(*a, **k):
        nonlocal n
        n += 1
        items.append(_item(*a, order=n, **k))

    explicit = any(s.get("sections") for s in steps)
    step_of_section: Dict[str, str] = {}
    ordered = sorted(enumerate(steps), key=lambda p: (PS.step_number(p[1], p[0]), p[0]))
    for idx, st in ordered:
        num = PS.step_number(st, idx)
        for s in PS.related_sections(st, sections, explicit):
            step_of_section.setdefault(str(s.get("id")), "%s" % num)
    for idx, st in ordered:
        num = PS.step_number(st, idx)
        is_check = st.get("kind") == "check"
        title = _bi(st.get("title"))
        note = _bi(st.get("note")) if st.get("note") else ("", "")
        resolved = [_bi(p.get("name")) for p in st.get("parts_resolved") or []]
        req_en, req_zh = note
        if resolved:
            req_en = (req_en + "\n" if req_en else "") + "Parts: " + ", ".join(r[0] for r in resolved)
            req_zh = (req_zh + "\n" if req_zh else "") + "零件：" + "、".join(r[1] for r in resolved)
        add("step:%s" % st.get("id"), "checks" if is_check else "assembly",
            ("%d. %s" % (num, title[0]), "%d. %s" % (num, title[1])), (req_en, req_zh), "check" if is_check else "step",
            "factory", "step %s (%d)" % (st.get("id"), num), source_kind="step")

    for s in sections:
        title = _bi(s.get("title"))
        body_en, body_zh = _bi(s.get("body"))
        t_en, t_zh = _tables_text(s, "en"), _tables_text(s, "zh")
        req = ((body_en + ("\n" + t_en if t_en else "")).strip(), (body_zh + ("\n" + t_zh if t_zh else "")).strip())
        tests = s.get("kind") == "tests"
        step_no = step_of_section.get(str(s.get("id")))
        add("section:%s" % s.get("id"), "tests" if tests else "process",
            (PS.strip_number(title[0]), PS.strip_number(title[1])), req, "tests" if tests else "section", "factory",
            "section %s%s" % (s.get("id"), " / step %s" % step_no if step_no else ""), source_kind="section")

    for p in parts:
        if p.get("kind") == "assembly":
            continue
        name = _bi(p.get("name"))
        mat = _bi(p.get("material_i18n") or p.get("material"))
        spec = _bi(p.get("spec_i18n") or p.get("spec"))
        probe = "%s %s" % name
        packaging = bool(_PACKAGING_RE.search(probe))
        fastener = bool(_FASTENER_RE.search(probe)) and not packaging
        if not (mat[0] or spec[0] or packaging or fastener):
            continue
        qty = int(p.get("qty_per_motor") or 1)
        en = ["Material: %s" % mat[0]] if mat[0] else []
        zh = ["材料：%s" % mat[1]] if mat[1] else []
        if spec[0]:
            en.append("Specification / finish: %s" % spec[0])
            zh.append("规格 / 表面处理：%s" % spec[1])
        en.append("Quantity per unit: %d; drawing revision %s" % (qty, p.get("revision") or "1.0"))
        zh.append("每台用量：%d；图纸版本 %s" % (qty, p.get("revision") or "1.0"))
        add("part:%s" % p.get("id"), "packaging" if packaging else "fasteners" if fastener else "parts", name,
            ("\n".join(en), "\n".join(zh)), "part", "factory" if p.get("kind") == "made" else "supplier",
            "part %s" % p.get("id"), source_kind="part")

    for t in threads:
        if t.get("resolved") or str(t.get("status") or "").lower() in ("closed", "resolved", "done"):
            continue
        msgs = [m for m in (t.get("messages") or []) if isinstance(m, dict)]
        first = _bi(msgs[0].get("text")) if msgs else ("", "")
        last = _bi(msgs[-1].get("text")) if msgs else ("", "")
        part = next((p for p in parts if p.get("id") == t.get("part_id")), None)
        title = first if first[0] else ("Discussion %s" % t.get("id"), "讨论 %s" % t.get("id"))
        if part is not None:
            pn = _bi(part.get("name"))
            title = ("%s: %s" % (pn[0], title[0]), "%s：%s" % (pn[1], title[1]))
        add("thread:%s" % t.get("id"), "discussion", (title[0][:200], title[1][:200]), (last[0][:1000], last[1][:1000]), "thread",
            "MOTRES", "thread %s" % t.get("id"), source_kind="thread")

    for d in documents:
        if not models.is_certification(d) or d.get("status") == "superseded" or d.get("archived_at"):
            continue
        if d.get("cert_status") in ("evidence_received", "signed"):
            continue
        if d.get("internal") and not include_internal:
            continue
        grp = _norm(d.get("group") or "")
        name = str(d.get("name") or "")
        label = ("%s: %s" % (grp, name) if grp else name)
        add("cert:%s" % d.get("id"), "certification", (label, label),
            ("Evidence not yet received (status: %s)" % (d.get("cert_status") or "draft"),
             "证据尚未收到（状态：%s）" % (d.get("cert_status") or "draft")), "cert", "MOTRES", "document %s" % d.get("id"),
            source_kind="certification")

    order = {k: i for i, k in enumerate(CATEGORIES)}
    items.sort(key=lambda i: (order[i["category"]], i["_order"]))
    for i, it in enumerate(items, 1):
        it["no"] = i
        it.pop("_order", None)
        it["revision"] = rev["number"]
        it["revision_date"] = rev["date"]
    return {"revision": rev, "items": items}


def merge_status(items: List[dict], stored: Optional[dict], documents: Optional[List[dict]] = None,
                 include_internal: bool = True) -> List[dict]:
    """Add the stored status to each item.  A stored status whose ``revision_hash`` differs from the item's own hash was set for
    another text: the item is ``to_confirm`` again (``changed`` true, ``previous_status`` kept).  No record means ``to_do``."""
    stored = stored if isinstance(stored, dict) else {}
    docs = {str(d.get("id")): d for d in (documents or []) if isinstance(d, dict)}
    out = []
    for it in items:
        rec = stored.get(it["key"])
        rec = rec if isinstance(rec, dict) else None
        row = dict(it)
        if rec is None or rec.get("status") not in STATUSES:
            row.update(status="to_do", changed=False, previous_status=None, evidence=None, by=None, at=None)
        else:
            changed = str(rec.get("revision_hash") or "") != it["revision_hash"]
            row.update(status="to_confirm" if changed else rec["status"], changed=changed,
                       previous_status=rec["status"] if changed else None, by=rec.get("by"), at=rec.get("at"),
                       evidence=_evidence_view(rec.get("evidence"), docs, include_internal))
        row["open"] = row["status"] not in CLOSED
        out.append(row)
    return out


def _evidence_view(ev: Any, docs: Dict[str, dict], include_internal: bool) -> Optional[dict]:
    if not isinstance(ev, dict):
        return None
    doc_id = str(ev.get("doc_id") or "")
    doc = docs.get(doc_id) if doc_id else None
    hidden = bool(doc is not None and doc.get("internal") and not include_internal)
    return {"doc_id": doc_id or None, "note": str(ev.get("note") or ""),
            "name": (str(doc.get("name")) if doc is not None and not hidden else None),
            "file_id": (str(doc.get("file_id")) if doc is not None and not hidden else None), "hidden": hidden}


def stats(items: List[dict]) -> dict:
    by_status = {s: 0 for s in STATUSES}
    by_category: Dict[str, dict] = {k: {"total": 0, "open": 0} for k in CATEGORIES}
    for it in items:
        by_status[it["status"]] += 1
        c = by_category[it["category"]]
        c["total"] += 1
        c["open"] += 1 if it["open"] else 0
    total = len(items)
    return {"total": total, "open": sum(1 for i in items if i["open"]), "by_status": by_status, "by_category": by_category,
            "done_pct": round(100.0 * by_status["done"] / total, 1) if total else 0.0}


def build(die: str, config: str, *, include_internal: bool = True) -> dict:
    """The checklist with statuses, as the routes and the export use it."""
    from motor_ai_sim.production import store

    gen = generate(die, config, include_internal=include_internal)
    stored = (store.load_file(die, config, FILE_NAME) or {}).get("items") or {}
    docs = store.load_file(die, config, "documents.json").get("documents") or []
    items = merge_status(gen["items"], stored, docs, include_internal)
    return {"die": die, "config": config, "revision": gen["revision"], "items": items, "stats": stats(items)}


def validate_update(body: Any) -> dict:
    """``{key, status, evidence}`` from a request body; raises ValueError with a short reason."""
    if not isinstance(body, dict):
        raise ValueError("a JSON object is expected")
    key = str(body.get("key") or "").strip()
    if not key or len(key) > 200:
        raise ValueError("key is required")
    status = str(body.get("status") or "").strip()
    if status not in STATUSES:
        raise ValueError("status must be one of %s" % ", ".join(STATUSES))
    ev = body.get("evidence")
    evidence = None
    if ev is not None:
        if not isinstance(ev, dict):
            raise ValueError("evidence must be an object {doc_id, note}")
        doc_id = str(ev.get("doc_id") or "").strip()[:80]
        note = str(ev.get("note") or "").strip()[:500]
        evidence = {"doc_id": doc_id or None, "note": note} if (doc_id or note) else None
    return {"key": key, "status": status, "evidence": evidence, "has_evidence": "evidence" in body}


def set_status(die: str, config: str, who: str, update: dict, *, include_internal: bool = True) -> dict:
    """Store one status.  ``ValueError('unknown_item')`` when the key is not in the current list, ``('unknown_document')`` when the
    evidence names a document this production does not show to the caller.  Returns ``{item, before}``."""
    from motor_ai_sim.production import store

    current = {it["key"]: it for it in build(die, config, include_internal=include_internal)["items"]}
    item = current.get(update["key"])
    if item is None:
        raise ValueError("unknown_item")
    docs = {str(d.get("id")): d for d in (store.load_file(die, config, "documents.json").get("documents") or []) if isinstance(d, dict)}
    ev = update.get("evidence")
    if ev and ev.get("doc_id"):
        d = docs.get(ev["doc_id"])
        if d is None or (d.get("internal") and not include_internal):
            raise ValueError("unknown_document")
    box: Dict[str, Any] = {}

    def mut(doc):
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
            doc.clear()
            doc["items"] = {}
        before = doc["items"].get(update["key"])
        box["before"] = before
        # keep the old evidence when the request does not mention it and the text is unchanged
        keep = before.get("evidence") if (isinstance(before, dict) and not update["has_evidence"]
                                          and before.get("revision_hash") == item["revision_hash"]) else None
        doc["items"][update["key"]] = {"status": update["status"], "evidence": ev if update["has_evidence"] else keep,
                                       "by": who, "at": models._now(), "revision_hash": item["revision_hash"],
                                       "revision": item["revision"]}
    store.mutate_file(die, config, FILE_NAME, mut)
    before = box.get("before")
    return {"item": next(i for i in build(die, config, include_internal=include_internal)["items"] if i["key"] == update["key"]),
            "before": (before or {}).get("status") if isinstance(before, dict) else None}
