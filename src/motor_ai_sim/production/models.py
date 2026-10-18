"""Production module data-model helpers: build, normalize, migrate and validate the
entities stored under ``<shared>/production/<die>/<config>/``.  Contract:
docs/PRODUCTION_MODULE_DESIGN.md section 4."""
from __future__ import annotations

import datetime
import re
import secrets
from typing import Any, Optional

from fastapi import HTTPException

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


PRODUCT_TYPES = ("motor", "electronics")
ROOT_NAMES = {"motor": {"en": "Motor", "zh": "电机整机"}, "electronics": {"en": "Assembly", "zh": "整机"}}


def product_type_of(production: Optional[dict]) -> str:
    t = (production or {}).get("product_type")
    return t if t in PRODUCT_TYPES else "motor"


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
        # what is produced (owner 2026-10-18: the 800 V controller is produced here too): a motor or electronics
        "product_type": raw.get("product_type") if raw.get("product_type") in PRODUCT_TYPES else "motor",
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

    pid = part_id or str(raw.get("id") or _new_id("pt"))
    parent_id_raw = raw.get("parent_id")
    parent_id = str(parent_id_raw).strip() if parent_id_raw is not None and str(parent_id_raw).strip() else None
    if parent_id == pid:
        parent_id = None

    return {
        "id": pid,
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
        "parent_id": parent_id,
    }


DEFAULT_SUBASSEMBLIES = [
    {
        "id": "sub_stator",
        "name": {
            "en": "Stator assembly",
            "zh": "定子总成",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "kind": "assembly",
        "qty_per_motor": 1,
        "material": "",
        "spec": "",
        "material_i18n": {"zh": "", "en": ""},
        "spec_i18n": {"zh": "", "en": ""},
        "description": {
            "en": "Stator core, coils, insulation, rings and phase board",
            "zh": "定子铁芯、线圈、绝缘、固定环与相板",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "drawing_files": [],
        "optional_step": None,
        "revision": "1.0",
        "parent_id": "motor",
    },
    {
        "id": "sub_rotor",
        "name": {
            "en": "Rotor assembly",
            "zh": "转子总成",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "kind": "assembly",
        "qty_per_motor": 1,
        "material": "",
        "spec": "",
        "material_i18n": {"zh": "", "en": ""},
        "spec_i18n": {"zh": "", "en": ""},
        "description": {
            "en": "Rotor core, magnets and shaft",
            "zh": "转子铁芯、永磁体与电机轴",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "drawing_files": [],
        "optional_step": None,
        "revision": "1.0",
        "parent_id": "motor",
    },
    {
        "id": "sub_housing",
        "name": {
            "en": "Housing",
            "zh": "机壳与结构件",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "kind": "assembly",
        "qty_per_motor": 1,
        "material": "",
        "spec": "",
        "material_i18n": {"zh": "", "en": ""},
        "spec_i18n": {"zh": "", "en": ""},
        "description": {
            "en": "Holders, bearings, screws and propeller fix",
            "zh": "端盖支架、轴承、螺钉与螺旋桨固定片",
            "src_lang": "en",
            "status": {"en": "confirmed", "zh": "confirmed"},
        },
        "drawing_files": [],
        "optional_step": None,
        "revision": "1.0",
        "parent_id": "motor",
    },
]


def match_default_assembly_parent(part_name_en: str, part_name_zh: str = "") -> str:
    """Deterministic canonical mapping for motor parts:
    - Stator assembly: Stator core, Coil, Slot insulation, Coil-fixing ring, Phase board, Lead wire, Varnish / epoxy
    - Rotor assembly: Rotor core, Magnet, Shaft
    - Housing: Front holder, Back holder, Bearing, Socket head screw, Prop_fix
    """
    en = (part_name_en or "").strip().lower()
    zh = (part_name_zh or "").strip()

    # Check housing matches first (so "Bearing / 轴承" is not captured by "轴")
    if any(k in en for k in ("holder", "bearing", "screw", "bolt", "prop_fix", "prop fix", "housing")) or \
       any(k in zh for k in ("支架", "端盖", "轴承", "螺钉", "螺栓", "固定片", "机壳")):
        return "sub_housing"

    # Check rotor matches
    if any(k in en for k in ("rotor core", "magnet", "shaft")) or \
       any(k in zh for k in ("转子", "磁钢", "磁体")) or \
       ("轴" in zh and "轴承" not in zh):
        if "stator" not in en and "定子" not in zh:
            return "sub_rotor"

    # Check stator matches
    if any(k in en for k in ("stator core", "coil", "insulation", "phase board", "lead wire", "varnish", "epoxy")) or \
       any(k in zh for k in ("定子", "线圈", "绝缘", "相板", "引线", "浸漆", "环氧")):
        return "sub_stator"

    return "sub_housing"


def ensure_default_assembly(parts: list[dict], die: str = "", config: str = "") -> list[dict]:
    """Ensure parts list contains the default assembly hierarchy if none was defined yet.
    If sub-assemblies (kind='assembly') or parent_ids are already present, preserve them.
    """
    if not parts:
        return []

    has_assemblies = any(isinstance(p, dict) and p.get("kind") == "assembly" for p in parts)
    has_parent_ids = any(isinstance(p, dict) and bool(p.get("parent_id")) for p in parts)
    if has_assemblies or has_parent_ids:
        return parts

    sub_map = {sub["id"]: dict(sub) for sub in DEFAULT_SUBASSEMBLIES}

    updated_parts = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        up = dict(p)
        name_en = (up.get("name") or {}).get("en") or ""
        name_zh = (up.get("name") or {}).get("zh") or ""
        parent = match_default_assembly_parent(name_en, name_zh)
        up["parent_id"] = parent
        updated_parts.append(up)

    return list(sub_map.values()) + updated_parts


def build_assembly_tree(parts: list[dict], product_type: str = "motor") -> dict:
    """Build hierarchical tree:
    Root ('motor') -> Sub-assemblies -> Leaf parts.  The motor's default sub-assemblies (stator / rotor / housing) are
    added only for a motor; an electronics production brings its own."""
    structured_parts = ensure_default_assembly(parts) if product_type == "motor" else parts
    by_parent: dict[str, list[dict]] = {}
    for p in structured_parts:
        if not isinstance(p, dict):
            continue
        pid = p.get("parent_id") or "motor"
        by_parent.setdefault(pid, []).append(p)

    sub_assemblies = []
    for sub in by_parent.get("motor", []):
        children = by_parent.get(sub["id"], [])
        sub_assemblies.append({
            "node": sub,
            "children": children,
        })

    return {
        "id": "motor",
        "name": dict(ROOT_NAMES.get(product_type, ROOT_NAMES["motor"])),
        "sub_assemblies": sub_assemblies,
    }


def compute_assembly_costs(parts: list[dict], cost_rows: list[dict] | dict) -> dict[str, float]:
    """Compute roll-up cost per part/sub-assembly.
    For leaf parts, cost is unit_price.
    For a sub-assembly, cost is sum of children's unit_price.
    """
    if isinstance(cost_rows, dict):
        cost_rows = cost_rows.get("rows", [])
    cost_map: dict[str, float] = {}
    for cr in cost_rows or []:
        if isinstance(cr, dict) and cr.get("part_id") and cr.get("unit_price") is not None:
            try:
                cost_map[cr["part_id"]] = float(cr["unit_price"])
            except (ValueError, TypeError):
                pass

    children_map: dict[str, list[dict]] = {}
    for p in parts:
        if isinstance(p, dict):
            pid = p.get("parent_id")
            if pid:
                children_map.setdefault(pid, []).append(p)

    rollup: dict[str, float] = {}
    for p in parts:
        if isinstance(p, dict):
            part_id = p["id"]
            if p.get("kind") != "assembly":
                if part_id in cost_map:
                    rollup[part_id] = cost_map[part_id]

    # Iterative roll-up for assembly nodes
    for _ in range(5):
        changed = False
        for p in parts:
            if isinstance(p, dict) and p.get("kind") == "assembly":
                part_id = p["id"]
                children = children_map.get(part_id, [])
                total = sum(rollup.get(c["id"], 0.0) for c in children)
                if round(rollup.get(part_id, 0.0), 2) != round(total, 2):
                    rollup[part_id] = round(total, 2)
                    changed = True
        if not changed:
            break

    motor_children = [p for p in parts if isinstance(p, dict) and (p.get("parent_id") == "motor" or p.get("kind") == "assembly")]
    rollup["motor"] = round(sum(rollup.get(c["id"], 0.0) for c in motor_children), 2)

    return rollup



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
    cur = str(raw.get("currency") or "CNY").strip().upper()
    note = raw.get("note")
    return {
        "id": eid,
        "name": name,
        "amount": amount,
        # a work line has the same columns as a BOM line (owner 2026-10-18): its own currency and note
        "currency": cur if cur in ("CNY", "USD", "EUR") else "CNY",
        "note": str(note).strip()[:300] if note not in (None, "") else None,
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
                         margin_pct: float, order_quantity: int, parts: Optional[list[dict]] = None) -> dict:
    order_quantity = max(0, int(order_quantity or 0))
    try:
        margin_pct = max(0.0, min(99.99, float(margin_pct or 0.0)))
    except (ValueError, TypeError):
        margin_pct = 0.0

    cost_map = {r["part_id"]: r for r in cost_rows if isinstance(r, dict) and r.get("part_id")}
    assembly_part_ids = set()
    if parts:
        assembly_part_ids = {p["id"] for p in parts if isinstance(p, dict) and p.get("kind") == "assembly"}

    missing_prices_count = 0
    parts_subtotal = 0.0

    for br in bom_rows:
        pid = br.get("part_id")
        if pid in assembly_part_ids or br.get("kind") == "assembly":
            continue
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
        # internal = our own working document: never listed for, nor downloadable by, the factory (role oem)
        "internal": bool((raw or {}).get("internal", False)),
        # a picture posted to one assembly step (owner 2026-10-18: images in every production step)
        "step_id": str((raw or {}).get("step_id"))[:40] if (raw or {}).get("step_id") else None,
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


# ── procurement & project schedule ──────────────────────────────────────────

def validate_iso_date(val: Any, field_name: str = "date") -> Optional[str]:
    """Validate ISO date YYYY-MM-DD or return None if empty. Reject invalid format or calendar date with 422."""
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date format for {field_name}: '{s}'. Expected YYYY-MM-DD.",
        )
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid calendar date for {field_name}: '{s}'.",
        )
    return s


def normalize_receipt(raw: dict, ordered_on: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    date_raw = str(raw.get("date") or "").strip()
    timestamp_raw = raw.get("timestamp")
    timestamp_val: Optional[str] = None

    if "T" in date_raw or " " in date_raw:
        iso_str = date_raw.replace(" ", "T")
        date_part = iso_str.split("T")[0]
        date_val = validate_iso_date(date_part, "receipt date")
        timestamp_val = iso_str
    else:
        date_val = validate_iso_date(date_raw, "receipt date")
        if timestamp_raw is not None and str(timestamp_raw).strip():
            timestamp_val = str(timestamp_raw).strip()

    if not date_val:
        raise HTTPException(status_code=422, detail="Receipt date is required.")
    if ordered_on and date_val < ordered_on:
        raise HTTPException(
            status_code=422,
            detail=f"Receipt date {date_val} cannot be before ordered date {ordered_on}.",
        )
    try:
        qty = float(raw.get("qty", 0.0))
        accepted = float(raw.get("accepted", 0.0))
        rejected = float(raw.get("rejected", 0.0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Receipt quantities must be numbers.")
    if qty < 0 or accepted < 0 or rejected < 0:
        raise HTTPException(status_code=422, detail="Receipt quantities must be non-negative.")
    if abs((accepted + rejected) - qty) > 1e-4:
        raise HTTPException(
            status_code=422,
            detail=f"Receipt inspection mismatch: accepted ({accepted}) + rejected ({rejected}) != qty ({qty}).",
        )
    return {
        "date": date_val,
        "timestamp": timestamp_val,
        "qty": qty,
        "accepted": accepted,
        "rejected": rejected,
        "inspector": str(raw.get("inspector") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
    }


def normalize_procurement_line(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    part_id = str(raw.get("part_id") or "").strip()
    supplier = str(raw.get("supplier") or "").strip()
    po_number = str(raw.get("po_number") or "").strip()
    try:
        lead_time_days = max(0, int(raw.get("lead_time_days") or 0))
    except (ValueError, TypeError):
        lead_time_days = 0

    requested_on = validate_iso_date(raw.get("requested_on"), "requested_on")
    confirmed_on = validate_iso_date(raw.get("confirmed_on"), "confirmed_on")
    ordered_on = validate_iso_date(raw.get("ordered_on"), "ordered_on")
    due_on = validate_iso_date(raw.get("due_on"), "due_on")
    need_by = validate_iso_date(raw.get("need_by") or due_on, "need_by")

    # qty_ordered
    qty_ordered_raw = raw.get("qty_ordered")
    qty_ordered: Optional[float] = None
    if qty_ordered_raw is not None and str(qty_ordered_raw).strip() != "":
        try:
            qty_ordered = max(0.0, float(qty_ordered_raw))
        except (ValueError, TypeError):
            qty_ordered = None

    # receipts
    receipts_raw = raw.get("receipts") or []
    if not isinstance(receipts_raw, list):
        receipts_raw = []
    receipts = [normalize_receipt(r, ordered_on) for r in receipts_raw if isinstance(r, dict)]

    # direct received_on (optional legacy/simple)
    received_on = validate_iso_date(raw.get("received_on"), "received_on")
    if ordered_on and received_on and received_on < ordered_on:
        raise HTTPException(status_code=422, detail=f"received_on {received_on} cannot be before ordered_on {ordered_on}.")

    qty_received: Optional[float] = None
    if raw.get("qty_received") is not None and not receipts:
        try:
            qty_received = max(0.0, float(raw["qty_received"]))
        except (ValueError, TypeError):
            qty_received = None

    note = str(raw.get("note") or "").strip() or None
    updated_by = str(raw.get("updated_by") or "").strip()
    updated_at = str(raw.get("updated_at") or _now())

    return {
        "part_id": part_id,
        "supplier": supplier,
        "po_number": po_number,
        "lead_time_days": lead_time_days,
        "requested_on": requested_on,
        "confirmed_on": confirmed_on,
        "ordered_on": ordered_on,
        "due_on": due_on,
        "need_by": need_by,
        "qty_ordered": qty_ordered,
        "qty_received": qty_received,
        "received_on": received_on,
        "receipts": receipts,
        "note": note,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }


def enrich_procurement_row(
    row: dict,
    bom_row: Optional[dict] = None,
    part: Optional[dict] = None,
    order_quantity: int = 1,
    default_need_by: Optional[str] = None,
    today: Optional[str] = None,
) -> dict:
    if today:
        today_iso = str(today).strip()
    else:
        today_iso = datetime.date.today().isoformat()

    part_id = str(row.get("part_id") or (bom_row or {}).get("part_id") or "").strip()
    supplier = str(row.get("supplier") or "").strip()
    po_number = str(row.get("po_number") or "").strip()
    lead_time_days = max(0, int(row.get("lead_time_days") or 0))

    requested_on = row.get("requested_on") or None
    confirmed_on = row.get("confirmed_on") or None
    ordered_on = row.get("ordered_on") or None

    need_by = row.get("need_by") or row.get("due_on") or default_need_by or None
    due_on = row.get("due_on") or need_by

    qty_per_motor = max(0, int((bom_row or {}).get("qty_per_motor") or (part or {}).get("qty_per_motor") or 1))
    order_quantity = max(0, int(order_quantity or 0))

    if row.get("qty_ordered") is not None and float(row["qty_ordered"]) > 0:
        qty_needed = float(row["qty_ordered"])
    else:
        qty_needed = float(qty_per_motor * order_quantity)

    receipts = list(row.get("receipts") or [])
    accepted_sum = sum(float(r.get("accepted", 0.0)) for r in receipts)
    rejected_sum = sum(float(r.get("rejected", 0.0)) for r in receipts)

    if not receipts and row.get("qty_received") is not None:
        received_qty = float(row["qty_received"])
    else:
        received_qty = accepted_sum
    rejected_qty = rejected_sum

    # order_by = need_by - lead_time_days
    order_by: Optional[str] = None
    if need_by:
        try:
            nb_date = datetime.date.fromisoformat(need_by)
            order_by = (nb_date - datetime.timedelta(days=lead_time_days)).isoformat()
        except ValueError:
            order_by = None

    # expected_arrival = confirmed_on or requested_on or (ordered_on + lead_time_days) or due_on
    expected_arrival: Optional[str] = None
    if confirmed_on:
        expected_arrival = confirmed_on
    elif requested_on:
        expected_arrival = requested_on
    elif ordered_on and lead_time_days >= 0:
        try:
            od_date = datetime.date.fromisoformat(ordered_on)
            expected_arrival = (od_date + datetime.timedelta(days=lead_time_days)).isoformat()
        except ValueError:
            expected_arrival = None
    elif due_on or need_by:
        expected_arrival = due_on or need_by

    # received_on: date/timestamp of the receipt that completed the quantity
    received_on: Optional[str] = None
    if receipts:
        running = 0.0
        sorted_receipts = sorted(receipts, key=lambda r: str(r.get("timestamp") or r.get("date") or ""))
        for r in sorted_receipts:
            running += float(r.get("accepted", 0.0))
            if running >= qty_needed and qty_needed > 0 and received_on is None:
                received_on = str(r.get("timestamp") or r.get("date"))
    elif row.get("received_on") and (received_qty >= qty_needed or qty_needed == 0):
        received_on = str(row["received_on"])

    # Computed Status Priority:
    # 1. received: accepted >= needed
    # 2. partial: some accepted
    # 3. late: expected arrival < today, not received
    # 4. at_risk: expected arrival > need_by
    # 5. confirmed: supplier date set
    # 6. ordered
    # 7. to_order: not ordered and today >= order_by
    # 8. not_ordered
    is_fully_received = (qty_needed > 0 and received_qty >= qty_needed) or (qty_needed == 0 and received_qty > 0)
    is_partially_received = (0 < received_qty < qty_needed)

    is_late = bool(expected_arrival and expected_arrival < today_iso and not is_fully_received)
    is_at_risk = bool(expected_arrival and need_by and expected_arrival > need_by and not is_fully_received)
    is_to_order = bool(not ordered_on and order_by and today_iso >= order_by)

    if is_fully_received:
        status = "received"
    elif is_partially_received:
        status = "partial"
    elif is_late:
        status = "late"
    elif is_at_risk:
        status = "at_risk"
    elif bool(confirmed_on):
        status = "confirmed"
    elif bool(ordered_on):
        status = "ordered"
    elif is_to_order:
        status = "to_order"
    else:
        status = "not_ordered"

    return {
        "part_id": part_id,
        "supplier": supplier,
        "po_number": po_number,
        "lead_time_days": lead_time_days,
        "requested_on": requested_on,
        "confirmed_on": confirmed_on,
        "ordered_on": ordered_on,
        "due_on": due_on,
        "need_by": need_by,
        "order_by": order_by,
        "expected_arrival": expected_arrival,
        "qty_ordered": row.get("qty_ordered"),
        "qty_needed": qty_needed,
        "qty_per_motor": qty_per_motor,
        "received_qty": received_qty,
        "rejected_qty": rejected_qty,
        "received_on": received_on,
        "receipts": receipts,
        "status": status,
        "is_late": is_late,
        "is_at_risk": is_at_risk,
        "is_to_order": is_to_order,
        "has_quality_issue": (rejected_qty > 0),
        "note": row.get("note"),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
        "part_name": part.get("name") if part else None,
        "part_kind": part.get("kind") if part else None,
        "material": part.get("material") if part else None,
        "spec": part.get("spec") if part else None,
        "drawing_files": part.get("drawing_files") if part else [],
    }


def compute_readiness_summary(rows: list[dict], assembly_planned_start: Optional[str] = None) -> dict:
    parts_total_count = len(rows)
    parts_received_count = sum(1 for r in rows if r.get("status") == "received")
    received_pct = round((parts_received_count / parts_total_count * 100), 1) if parts_total_count else 0.0

    qty_needed_total = sum(float(r.get("qty_needed", 0.0)) for r in rows)
    qty_received_total = sum(float(r.get("received_qty", 0.0)) for r in rows)
    qty_received_pct = round((qty_received_total / qty_needed_total * 100), 1) if qty_needed_total else 0.0

    late_count = sum(1 for r in rows if r.get("status") == "late" or r.get("is_late"))
    at_risk_count = sum(1 for r in rows if r.get("status") == "at_risk" or r.get("is_at_risk"))
    to_order_count = sum(1 for r in rows if r.get("status") == "to_order" or r.get("is_to_order"))
    quality_issue_count = sum(1 for r in rows if r.get("has_quality_issue"))

    ready_for_assembly = bool(parts_total_count > 0 and parts_received_count == parts_total_count)

    unreceived = [r for r in rows if r.get("status") != "received"]
    expected_dates = [r.get("expected_arrival") for r in unreceived if r.get("expected_arrival")]
    latest_expected_arrival = max(expected_dates) if expected_dates else None

    assembly_warnings = []
    if assembly_planned_start:
        for r in unreceived:
            exp = r.get("expected_arrival")
            if exp and exp > assembly_planned_start:
                pid = r.get("part_id", "")
                pname = (r.get("part_name", {}) or {}).get("en") or pid
                assembly_warnings.append({
                    "part_id": pid,
                    "expected_arrival": exp,
                    "assembly_planned_start": assembly_planned_start,
                    "message": f"Assembly at risk: {pname} expected {exp}, need by {assembly_planned_start}",
                })

    if ready_for_assembly:
        status_message = "Ready for assembly"
    elif late_count > 0:
        status_message = "Late"
    elif latest_expected_arrival:
        status_message = f"Expected {latest_expected_arrival}"
    else:
        status_message = "In progress"

    return {
        "parts_received_count": parts_received_count,
        "parts_total_count": parts_total_count,
        "received_pct": received_pct,
        "qty_received_total": round(qty_received_total, 2),
        "qty_needed_total": round(qty_needed_total, 2),
        "qty_received_pct": qty_received_pct,
        "late_count": late_count,
        "at_risk_count": at_risk_count,
        "to_order_count": to_order_count,
        "quality_issue_count": quality_issue_count,
        "ready_for_assembly": ready_for_assembly,
        "latest_expected_arrival": latest_expected_arrival,
        "status_message": status_message,
        "assembly_warnings": assembly_warnings,
    }


DEFAULT_SCHEDULE_PHASES = [
    {
        "id": "requirements",
        "key": "requirements",
        "title": {"en": "Requirements", "zh": "需求定义"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": [],
        "percent_complete": 0,
        "owner_role": "us",
        "note": "",
    },
    {
        "id": "design",
        "key": "design",
        "title": {"en": "Design", "zh": "工程设计"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": ["requirements"],
        "percent_complete": 0,
        "owner_role": "us",
        "note": "",
    },
    {
        "id": "ordering",
        "key": "ordering",
        "title": {"en": "Ordering", "zh": "物料采购"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": ["design"],
        "percent_complete": 0,
        "owner_role": "factory",
        "note": "",
    },
    {
        "id": "assembly",
        "key": "assembly",
        "title": {"en": "Assembly", "zh": "样机装配"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": ["ordering"],
        "percent_complete": 0,
        "owner_role": "factory",
        "note": "",
    },
    {
        "id": "testing",
        "key": "testing",
        "title": {"en": "Testing", "zh": "性能测试"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": ["assembly"],
        "percent_complete": 0,
        "owner_role": "us",
        "note": "",
    },
    {
        "id": "delivery",
        "key": "delivery",
        "title": {"en": "Delivery", "zh": "首批交付"},
        "planned_start": "",
        "planned_end": "",
        "actual_start": "",
        "actual_end": "",
        "status": "not_started",
        "depends_on": ["testing"],
        "percent_complete": 0,
        "owner_role": "factory",
        "note": "",
    },
]

DEFAULT_MILESTONES = [
    {"id": "ms_design_freeze", "title": {"en": "Design freeze", "zh": "设计冻结"}, "date": "", "done": False, "rule": "design_freeze"},
    {"id": "ms_all_pos_placed", "title": {"en": "All POs placed", "zh": "采购订单已全下"}, "date": "", "done": False, "rule": "all_pos_placed"},
    {"id": "ms_all_parts_received", "title": {"en": "All parts received", "zh": "全部物料已到齐"}, "date": "", "done": False, "rule": "all_parts_received"},
    {"id": "ms_first_article_approved", "title": {"en": "First article approved", "zh": "首件样品确认"}, "date": "", "done": False, "rule": "first_article_approved"},
    {"id": "ms_tests_passed", "title": {"en": "Tests passed", "zh": "测试通过"}, "date": "", "done": False, "rule": "tests_passed"},
    {"id": "ms_shipped", "title": {"en": "Shipped", "zh": "样机已发货"}, "date": "", "done": False, "rule": "shipped"},
]


def normalize_schedule_phase(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    pid = str(raw.get("id") or raw.get("key") or _new_id("ph")).strip()
    key = str(raw.get("key") or pid).strip()
    title = raw.get("title")
    if isinstance(title, dict):
        title = {"en": str(title.get("en") or "").strip(), "zh": str(title.get("zh") or "").strip()}
    else:
        title = {"en": str(title or ""), "zh": str(title or "")}

    planned_start = validate_iso_date(raw.get("planned_start"), f"phase {pid} planned_start") or ""
    planned_end = validate_iso_date(raw.get("planned_end"), f"phase {pid} planned_end") or ""
    actual_start = validate_iso_date(raw.get("actual_start"), f"phase {pid} actual_start") or ""
    actual_end = validate_iso_date(raw.get("actual_end"), f"phase {pid} actual_end") or ""

    status = str(raw.get("status") or "not_started").strip()
    if status not in ("not_started", "in_progress", "done", "blocked"):
        status = "not_started"

    depends_on = raw.get("depends_on") or []
    if isinstance(depends_on, list):
        depends_on = [str(d).strip() for d in depends_on if str(d).strip()]
    else:
        depends_on = []

    try:
        percent_complete = max(0, min(100, int(raw.get("percent_complete") or 0)))
    except (ValueError, TypeError):
        percent_complete = 0

    owner_role = str(raw.get("owner_role") or "us").strip()
    if owner_role not in ("us", "factory"):
        owner_role = "us"

    note = str(raw.get("note") or "").strip()

    return {
        "id": pid,
        "key": key,
        "title": title,
        "planned_start": planned_start,
        "planned_end": planned_end,
        "actual_start": actual_start,
        "actual_end": actual_end,
        "status": status,
        "depends_on": depends_on,
        "percent_complete": percent_complete,
        "owner_role": owner_role,
        "note": note,
    }


def normalize_milestone(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    mid = str(raw.get("id") or _new_id("ms")).strip()
    title = raw.get("title")
    if isinstance(title, dict):
        title = {"en": str(title.get("en") or "").strip(), "zh": str(title.get("zh") or "").strip()}
    else:
        title = {"en": str(title or ""), "zh": str(title or "")}
    date_val = validate_iso_date(raw.get("date"), f"milestone {mid} date") or ""
    done = bool(raw.get("done", False))
    rule = str(raw.get("rule") or "").strip() or None
    return {
        "id": mid,
        "title": title,
        "date": date_val,
        "done": done,
        "rule": rule,
    }


def detect_cycle(phases: list[dict]) -> None:
    """Detect cycles in depends_on DAG. Reject with 422 if cycle found."""
    adj: dict[str, list[str]] = {p["id"]: list(p.get("depends_on", [])) for p in phases}
    visited: dict[str, int] = {}  # 0: unvisited, 1: visiting (in stack), 2: visited

    def dfs(node: str, stack: list[str]) -> None:
        visited[node] = 1
        for neighbor in adj.get(node, []):
            if neighbor in adj:
                if visited.get(neighbor) == 1:
                    cycle_path = " -> ".join(stack + [neighbor])
                    raise HTTPException(
                        status_code=422,
                        detail=f"Dependency cycle detected in schedule: {cycle_path}.",
                    )
                if visited.get(neighbor, 0) == 0:
                    dfs(neighbor, stack + [neighbor])
        visited[node] = 2

    for node in adj:
        if visited.get(node, 0) == 0:
            dfs(node, [node])


def compute_critical_path_and_slack(phases: list[dict]) -> list[dict]:
    """Compute early start, early finish, late start, late finish, slack, and critical path."""
    if not phases:
        return []
    detect_cycle(phases)

    durations: dict[str, int] = {}
    phase_map: dict[str, dict] = {}
    for p in phases:
        pid = p["id"]
        phase_map[pid] = dict(p)
        start = p.get("planned_start")
        end = p.get("planned_end")
        if start and end:
            try:
                ds = datetime.date.fromisoformat(start)
                de = datetime.date.fromisoformat(end)
                durations[pid] = max(1, (de - ds).days + 1)
            except ValueError:
                durations[pid] = 1
        else:
            durations[pid] = 1

    # Topological sort
    in_degree = {p["id"]: 0 for p in phases}
    children: dict[str, list[str]] = {p["id"]: [] for p in phases}
    for p in phases:
        for dep in p.get("depends_on", []):
            if dep in phase_map:
                in_degree[p["id"]] += 1
                children[dep].append(p["id"])

    queue = [pid for pid, deg in in_degree.items() if deg == 0]
    topo_order = []
    while queue:
        curr = queue.pop(0)
        topo_order.append(curr)
        for child in children.get(curr, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Forward pass: early start (ES) and early finish (EF)
    es: dict[str, int] = {p["id"]: 0 for p in phases}
    ef: dict[str, int] = {}
    for pid in topo_order:
        deps = [d for d in phase_map[pid].get("depends_on", []) if d in ef]
        es[pid] = max([ef[d] for d in deps], default=0)
        ef[pid] = es[pid] + durations[pid]

    max_project_duration = max(ef.values(), default=0)

    # Backward pass: late finish (LF) and late start (LS)
    lf: dict[str, int] = {p["id"]: max_project_duration for p in phases}
    ls: dict[str, int] = {}
    for pid in reversed(topo_order):
        succs = [c for c in children.get(pid, []) if c in ls]
        if succs:
            lf[pid] = min(ls[c] for c in succs)
        else:
            lf[pid] = max_project_duration
        ls[pid] = lf[pid] - durations[pid]

    # Slack and critical path
    out = []
    for p in phases:
        pid = p["id"]
        slack = max(0, lf.get(pid, 0) - ef.get(pid, 0))
        is_crit = (slack == 0)
        enriched = dict(phase_map[pid])
        enriched["duration_days"] = durations[pid]
        enriched["early_start_day"] = es.get(pid, 0)
        enriched["early_finish_day"] = ef.get(pid, 0)
        enriched["slack_days"] = slack
        enriched["is_critical"] = is_crit
        out.append(enriched)
    return out


def derive_ordering_phase(
    phases: list[dict], procurement_rows: list[dict], ready_for_assembly: bool
) -> list[dict]:
    """Derive Ordering phase actual dates and status from procurement rows if not explicitly set."""
    ordered_dates = [r.get("ordered_on") for r in procurement_rows if r.get("ordered_on")]
    received_dates = [r.get("received_on") for r in procurement_rows if r.get("received_on")]

    for p in phases:
        if p.get("key") == "ordering" or p.get("id") == "ordering":
            if not p.get("actual_start") and ordered_dates:
                p["actual_start"] = min(ordered_dates)
            if not p.get("actual_end") and ready_for_assembly and received_dates:
                p["actual_end"] = max(received_dates)
            if p.get("status") in ("not_started", ""):
                if ready_for_assembly:
                    p["status"] = "done"
                    p["percent_complete"] = 100
                elif ordered_dates:
                    p["status"] = "in_progress"
    return phases


def update_auto_milestones(
    milestones: list[dict],
    phases: list[dict],
    procurement_rows: list[dict],
    ready_for_assembly: bool,
) -> list[dict]:
    phase_status_map = {p.get("key") or p.get("id"): p.get("status") for p in phases}
    for m in milestones:
        rule = m.get("rule")
        done = bool(m.get("done", False))
        if rule == "all_pos_placed":
            if procurement_rows and all(bool(r.get("ordered_on")) for r in procurement_rows):
                done = True
        elif rule == "all_parts_received":
            if ready_for_assembly:
                done = True
        elif rule == "design_freeze":
            if phase_status_map.get("design") == "done":
                done = True
        elif rule == "tests_passed":
            if phase_status_map.get("testing") == "done":
                done = True
        elif rule == "shipped":
            if phase_status_map.get("delivery") == "done":
                done = True
        m["done"] = done
    return milestones
