"""Production module data-model helpers: build, normalize, migrate and validate the
entities stored under ``<shared>/production/<die>/<config>/``.  Contract:
docs/PRODUCTION_MODULE_DESIGN.md section 4."""
from __future__ import annotations

import datetime
import re
import secrets
from typing import Any, Optional

from motor_ai_sim.production import (
    LANGS, MEMBER_ROLES, EDIT_ROLES, PART_KINDS, PROPOSAL_STATUSES,
    SECTION_KINDS, TEXT_STATUSES,
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def _coerce_bilingual_status(v) -> dict:
    if not isinstance(v, dict):
        return {"en": "machine", "zh": "machine"}
    return {
        "en": str(v.get("en") or "machine") if v.get("en") in TEXT_STATUSES else "machine",
        "zh": str(v.get("zh") or "machine") if v.get("zh") in TEXT_STATUSES else "machine",
    }


def _machine_fill(text: str, src_lang: str) -> dict:
    """Build the Bilingual object with the *other* language marked pending.

    The caller is expected to call ``translate.translate_text`` and overwrite the
    target; until then it is ``pending`` so the UI shows the retry action.
    """
    other = "zh" if src_lang == "en" else "en"
    out: dict = {"en": "", "zh": "", "src_lang": src_lang,
                 "status": {"en": "pending", "zh": "pending"}}
    out[src_lang] = str(text)
    out["status"][src_lang] = "confirmed"
    return out


def make_bilingual(text: str, src_lang: str,
                   translate_fn: Optional[Any] = None) -> dict:
    """Create a bilingual object; machine-translate the other side if a translator
    callable is supplied (or default to translate_text). On failure the other side stays pending."""
    if src_lang not in LANGS:
        src_lang = "en"
    out = _machine_fill(text, src_lang)
    other = "zh" if src_lang == "en" else "en"
    if translate_fn is None:
        try:
            from motor_ai_sim.production import translate as T
            translate_fn = T.translate_text
        except Exception:                               # noqa: BLE001
            translate_fn = None
    if translate_fn is not None and str(text or "").strip():
        try:
            out[other] = str(translate_fn(text, src_lang, other)).strip()
            out["status"][other] = "machine"
        except Exception:                               # noqa: BLE001 - never block save
            pass
    return out


def ensure_translated(bi: dict, translate_fn: Optional[Any] = None) -> dict:
    """If one language is pending and empty, attempt machine translation with translate_text."""
    if not isinstance(bi, dict):
        return bi
    src = bi.get("src_lang") or "en"
    other = "zh" if src == "en" else "en"
    if not bi.get(other) or bi.get("status", {}).get(other) == "pending":
        if bi.get(src):
            try:
                fn = translate_fn
                if fn is None:
                    from motor_ai_sim.production import translate as T
                    fn = T.translate_text
                bi[other] = str(fn(bi[src], src, other)).strip()
                bi.setdefault("status", {})[other] = "machine"
            except Exception:                           # noqa: BLE001 - never block save
                bi.setdefault("status", {})[other] = "pending"
    return bi


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def normalize_i18n_dict(raw: Any, default_zh: str = "") -> dict:
    if isinstance(raw, dict):
        zh = str(raw.get("zh") or "").strip()
        en = str(raw.get("en") or "").strip()
        return {"zh": zh or default_zh, "en": en}
    elif isinstance(raw, str):
        return {"zh": raw.strip() or default_zh, "en": ""}
    return {"zh": default_zh, "en": ""}


def ensure_i18n_translated(d: dict, translate_fn: Optional[Any] = None) -> dict:
    """Ensure {zh, en} dict has both languages filled, auto-translating if needed."""
    if not isinstance(d, dict):
        return {"zh": "", "en": ""}
    zh = str(d.get("zh") or "").strip()
    en = str(d.get("en") or "").strip()
    out = {"zh": zh, "en": en}
    if zh and not en:
        if not _CJK_RE.search(zh):
            out["en"] = zh
        else:
            try:
                fn = translate_fn
                if fn is None:
                    from motor_ai_sim.production import translate as T
                    fn = T.translate_text
                out["en"] = str(fn(zh, "zh", "en")).strip() or zh
            except Exception:
                pass
    elif en and not zh:
        try:
            fn = translate_fn
            if fn is None:
                from motor_ai_sim.production import translate as T
                fn = T.translate_text
            out["zh"] = str(fn(en, "en", "zh")).strip() or en
        except Exception:
            pass
    return out



def is_member(email: str, production: dict) -> Optional[str]:
    """Return caller's role if a member of production, else None."""
    for m in (production or {}).get("members") or []:
        if isinstance(m, dict) and str(m.get("email", "")).strip().lower() == email.strip().lower():
            role = m.get("role")
            if role in MEMBER_ROLES:
                return role
    return None


def normalize_bilingual(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    src_lang = str(raw.get("src_lang") or "en")
    if src_lang not in LANGS:
        src_lang = "en"
    out = {
        "en": str(raw.get("en") or ""),
        "zh": str(raw.get("zh") or ""),
        "src_lang": src_lang,
        "status": _coerce_bilingual_status(raw.get("status")),
    }
    # An empty text in the source language cannot be confirmed.
    for lang in LANGS:
        if not out[lang]:
            out["status"][lang] = "pending"
    return out


def _norm_member_role(role: str) -> str:
    return role if role in MEMBER_ROLES else "oem"


def normalize_production(raw: dict, die: str, config: str) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    members = raw.get("members") or []
    members = [{"email": str(m.get("email", "")).strip().lower(),
                "role": _norm_member_role(str(m.get("role", "oem")))}
               for m in members if isinstance(m, dict) and str(m.get("email", "")).strip()]
    members = _unique_members(members)
    return {
        "die": die,
        "config": config,
        "title": normalize_bilingual(raw.get("title")),
        "order_quantity": max(0, int(raw.get("order_quantity") or 0)),
        "status": raw.get("status") if raw.get("status") in ("active", "paused", "archived") else "active",
        "current_revision": str(raw.get("current_revision") or "1.0"),
        "share_costs_with_oem": bool(raw.get("share_costs_with_oem", False)),
        "members": members,
        "created_at": str(raw.get("created_at") or _now()),
        "updated_at": str(raw.get("updated_at") or _now()),
    }


def _unique_members(members: list) -> list:
    seen: set = set()
    out = []
    for m in members:
        if m["email"] not in seen:
            seen.add(m["email"])
            out.append(m)
    return out


def normalize_members(raw: list) -> list:
    if not isinstance(raw, list):
        return []
    members = [{"email": str(m.get("email", "")).strip().lower(),
                "role": _norm_member_role(str(m.get("role", "oem")))}
               for m in raw if isinstance(m, dict) and str(m.get("email", "")).strip()]
    return _unique_members(members)


def normalize_part(raw: dict, part_id: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    drawing_files = raw.get("drawing_files") or []
    drawing_files = [{
        "file_id": str(df.get("file_id") or ""),
        "kind": "pdf" if str(df.get("kind")) == "pdf" else "png",
        "name": str(df.get("name") or ""),
    } for df in drawing_files if isinstance(df, dict) and df.get("file_id")]
    optional_step = raw.get("optional_step")
    if isinstance(optional_step, dict) and optional_step.get("file_id"):
        optional_step = {"file_id": str(optional_step["file_id"])}
    else:
        optional_step = None

    old_mat = str(raw.get("material") or "").strip()
    old_spec = str(raw.get("spec") or "").strip()
    mat_i18n = normalize_i18n_dict(raw.get("material_i18n"), default_zh=old_mat)
    if not mat_i18n["zh"] and old_mat:
        mat_i18n["zh"] = old_mat
    spec_i18n = normalize_i18n_dict(raw.get("spec_i18n"), default_zh=old_spec)
    if not spec_i18n["zh"] and old_spec:
        spec_i18n["zh"] = old_spec

    return {
        "id": part_id or str(raw.get("id") or _new_id("pt")),
        "name": normalize_bilingual(raw.get("name")),
        "kind": str(raw.get("kind")) if str(raw.get("kind")) in PART_KINDS else "made",
        "qty_per_motor": max(0, int(raw.get("qty_per_motor") or 1)),
        "material": mat_i18n["zh"] or old_mat,
        "spec": spec_i18n["zh"] or old_spec,
        "material_i18n": mat_i18n,
        "spec_i18n": spec_i18n,
        "description": normalize_bilingual(raw.get("description")),
        "drawing_files": drawing_files,
        "optional_step": optional_step,
        "revision": str(raw.get("revision") or "1.0"),
    }



def normalize_bom_rows(raw: list, part_ids: set) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("part_id") or "")
        if pid not in part_ids:
            continue
        out.append({
            "part_id": pid,
            "qty_per_motor": max(0, int(r.get("qty_per_motor") or 1)),
            "order_total": max(0, int(r.get("order_total") or 0)),
        })
    return out


def normalize_cost_row(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    pid = str(raw.get("part_id") or raw.get("bom_row_id") or "").strip()
    price_raw = raw.get("unit_price")
    unit_price: Optional[float] = None
    if price_raw is not None and str(price_raw).strip() != "":
        try:
            unit_price = float(price_raw)
        except (ValueError, TypeError):
            unit_price = None
    currency = str(raw.get("currency") or "CNY").strip().upper() or "CNY"
    note = str(raw.get("note") or "").strip() or None
    return {
        "part_id": pid,
        "unit_price": unit_price,
        "currency": currency,
        "note": note,
    }


def normalize_extra_cost(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    eid = str(raw.get("id") or _new_id("ec"))
    name = normalize_bilingual(raw.get("name"))
    try:
        amount = max(0.0, float(raw.get("amount") or 0.0))
    except (ValueError, TypeError):
        amount = 0.0
    return {
        "id": eid,
        "name": name,
        "amount": amount,
    }


def normalize_costs(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    rows = raw.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    norm_rows = []
    for r in rows:
        if isinstance(r, dict):
            nr = normalize_cost_row(r)
            if nr["part_id"]:
                norm_rows.append(nr)
    extras = raw.get("extras") or []
    if not isinstance(extras, list):
        extras = []
    norm_extras = [normalize_extra_cost(e) for e in extras if isinstance(e, dict)]
    try:
        margin_pct = max(0.0, min(99.99, float(raw.get("margin_pct") or 0.0)))
    except (ValueError, TypeError):
        margin_pct = 0.0
    return {
        "rows": norm_rows,
        "extras": norm_extras,
        "margin_pct": margin_pct,
    }


def compute_cost_summary(bom_rows: list[dict], cost_rows: list[dict], extras: list[dict],
                         margin_pct: float, order_quantity: int) -> dict:
    order_quantity = max(0, int(order_quantity or 0))
    try:
        margin_pct = max(0.0, min(99.99, float(margin_pct or 0.0)))
    except (ValueError, TypeError):
        margin_pct = 0.0

    cost_map = {r["part_id"]: r for r in cost_rows if isinstance(r, dict) and r.get("part_id")}
    missing_prices_count = 0
    parts_subtotal = 0.0

    for br in bom_rows:
        pid = br.get("part_id")
        crow = cost_map.get(pid)
        if crow is None or crow.get("unit_price") is None:
            missing_prices_count += 1
        else:
            parts_subtotal += float(crow["unit_price"])

    extra_subtotal = sum(float(e.get("amount") or 0.0) for e in extras if isinstance(e, dict))
    cost_per_motor = parts_subtotal + extra_subtotal
    price_factor = 1.0 - (margin_pct / 100.0)
    price_per_motor = (cost_per_motor / price_factor) if price_factor > 0 else cost_per_motor

    order_parts_total = parts_subtotal * order_quantity
    order_extra_total = extra_subtotal * order_quantity
    order_cost_total = cost_per_motor * order_quantity
    order_price_total = price_per_motor * order_quantity

    warnings = []
    if missing_prices_count > 0:
        warnings.append(f"{missing_prices_count} BOM item(s) have missing prices; parts subtotal is incomplete.")

    return {
        "parts_subtotal": round(parts_subtotal, 2),
        "extra_subtotal": round(extra_subtotal, 2),
        "cost_per_motor": round(cost_per_motor, 2),
        "margin_pct": round(margin_pct, 2),
        "price_per_motor": round(price_per_motor, 2),
        "order_quantity": order_quantity,
        "order_parts_total": round(order_parts_total, 2),
        "order_extra_total": round(order_extra_total, 2),
        "order_cost_total": round(order_cost_total, 2),
        "order_price_total": round(order_price_total, 2),
        "missing_prices_count": missing_prices_count,
        "warnings": warnings,
    }


def normalize_process_section(raw: dict, section_id: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    kind = str(raw.get("kind") or "assembly")
    if kind not in SECTION_KINDS:
        kind = "assembly"
    tables = raw.get("tables") or []
    if not isinstance(tables, list):
        tables = []
    tables_i18n = raw.get("tables_i18n")
    if not isinstance(tables_i18n, dict):
        tables_i18n = {"zh": tables, "en": tables}
    else:
        tables_i18n = {
            "zh": tables_i18n.get("zh") if isinstance(tables_i18n.get("zh"), list) else tables,
            "en": tables_i18n.get("en") if isinstance(tables_i18n.get("en"), list) else [],
        }
    return {
        "id": section_id or str(raw.get("id") or _new_id("sec")),
        "kind": kind,
        "title": normalize_bilingual(raw.get("title")),
        "body": normalize_bilingual(raw.get("body")),
        "tables": tables,
        "tables_i18n": tables_i18n,
    }


def normalize_document(raw: dict, file_id: str, uploader_email: str, name: str,
                       part_id: Optional[str] = None) -> dict:
    return {
        "id": _new_id("doc"),
        "uploader_email": uploader_email,
        "uploaded_at": _now(),
        "part_id": part_id,
        "file_id": file_id,
        "name": name,
    }


def normalize_message(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    src_lang = str(raw.get("src_lang") or "en")
    if src_lang not in LANGS:
        src_lang = "en"
    attachments = raw.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    attachments = [{"file_id": str(a.get("file_id") or ""),
                    "name": str(a.get("name") or "")}
                   for a in attachments if isinstance(a, dict) and a.get("file_id")]
    return {
        "id": _new_id("msg"),
        "author_email": str(raw.get("author_email") or ""),
        "src_lang": src_lang,
        "text": normalize_bilingual(raw.get("text")),
        "attachments": attachments,
        "created_at": _now(),
    }


def normalize_thread(raw: dict, thread_id: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    messages = raw.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    return {
        "id": thread_id or str(raw.get("id") or _new_id("th")),
        "part_id": str(raw.get("part_id")) if raw.get("part_id") else None,
        "created_at": str(raw.get("created_at") or _now()),
        "messages": [normalize_message(m) for m in messages],
    }


def normalize_proposal(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    status = str(raw.get("status") or "open")
    if status not in PROPOSAL_STATUSES:
        status = "open"
    note = raw.get("decision_note")
    return {
        "id": _new_id("pr"),
        "part_id": str(raw.get("part_id")) if raw.get("part_id") else None,
        "author_email": str(raw.get("author_email") or ""),
        "text": normalize_bilingual(raw.get("text")),
        "status": status,
        "decision_note": normalize_bilingual(note) if isinstance(note, dict) else None,
        "linked_revision": str(raw.get("linked_revision")) if raw.get("linked_revision") else None,
        "created_at": str(raw.get("created_at") or _now()),
        "resolved_at": str(raw.get("resolved_at")) if raw.get("resolved_at") else None,
    }


def normalize_revision(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    changes = raw.get("changes") or []
    if not isinstance(changes, list):
        changes = []
    acks = raw.get("acknowledgements") or []
    if not isinstance(acks, list):
        acks = []
    return {
        "number": str(raw.get("number") or "1.0"),
        "date": str(raw.get("date") or _now()),
        "author_email": str(raw.get("author_email") or ""),
        "summary": normalize_bilingual(raw.get("summary")),
        "changes": [normalize_bilingual(c) for c in changes],
        "acknowledgements": [{
            "email": str(a.get("email") or ""),
            "time": str(a.get("time") or _now()),
        } for a in acks if isinstance(a, dict) and a.get("email")],
    }


def bump_revision(rev: str) -> str:
    """1.0 -> 1.1 -> 1.2 ..."""
    try:
        major, minor = str(rev).split(".")
        return f"{major}.{int(minor) + 1}"
    except Exception:                               # noqa: BLE001
        return "1.0"


def next_revision_number(revisions: list) -> str:
    if not revisions:
        return "1.0"
    nums = [str(r.get("number") or "1.0") for r in revisions]
    latest = max(nums, key=lambda s: [int(x) for x in re.split(r"[._]", s) if x.isdigit()] or [0])
    return bump_revision(latest)


def audit_entry(who: str, action: str, entity: str, entity_id: str,
                snapshot: Optional[dict] = None) -> dict:
    return {
        "time": _now(),
        "who": who,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "snapshot": snapshot or {},
    }


def update_bilingual_text(obj: dict, ref: dict, lang: str, text: Optional[str]) -> bool:
    """Confirm or edit one bilingual field.  ``ref`` is a TranslationRef dict.
    Returns True if something changed.
    """
    if lang not in LANGS:
        return False
    field = str(ref.get("field") or "")
    parts = field.split(".")
    target = obj
    for p in parts[:-1]:
        if p.isdigit():
            target = target[int(p)]
        else:
            target = target[p]
    leaf = parts[-1]
    if leaf.isdigit():
        target = target[int(leaf)]
        leaf = "status" if False else ""
    if text is not None:
        target["status"][lang] = "confirmed"
        target[lang] = str(text)
        return True
    # confirm machine translation as-is
    if target["status"][lang] in ("machine", "pending") and target.get(lang):
        target["status"][lang] = "confirmed"
        return True
    return False
