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
    callable is supplied.  On failure the other side stays ``pending``."""
    if src_lang not in LANGS:
        src_lang = "en"
    out = _machine_fill(text, src_lang)
    other = "zh" if src_lang == "en" else "en"
    if translate_fn is not None and str(text or "").strip():
        try:
            out[other] = str(translate_fn(text, src_lang, other)).strip()
            out["status"][other] = "machine"
        except Exception:                               # noqa: BLE001 - never block save
            pass
    return out


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
    return {
        "id": part_id or str(raw.get("id") or _new_id("pt")),
        "name": normalize_bilingual(raw.get("name")),
        "kind": str(raw.get("kind")) if str(raw.get("kind")) in PART_KINDS else "made",
        "qty_per_motor": max(0, int(raw.get("qty_per_motor") or 1)),
        "material": str(raw.get("material") or ""),
        "spec": str(raw.get("spec") or ""),
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


def normalize_process_section(raw: dict, section_id: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    kind = str(raw.get("kind") or "assembly")
    if kind not in SECTION_KINDS:
        kind = "assembly"
    tables = raw.get("tables") or []
    if not isinstance(tables, list):
        tables = []
    return {
        "id": section_id or str(raw.get("id") or _new_id("sec")),
        "kind": kind,
        "title": normalize_bilingual(raw.get("title")),
        "body": normalize_bilingual(raw.get("body")),
        "tables": tables,
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
