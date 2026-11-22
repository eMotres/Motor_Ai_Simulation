"""Company details: the legal / delivery data of a company that signs a contract with us (owner 2026-11-22).

One engine, two forms: the project's customer company (``PROJECT_FORM``, filled by the project owner) and, on the production
side, the factory's company (see ``production/company_routes.py``).  The engine is pure: it validates a patch, merges it into
the stored document, reports which fields really changed and keeps the "confirmed by <email> on <date>" stamp (any later
change clears it).  Storage, access rules and audit live with the routes of each side.

Stored document: ``{"values": {field: value}, "confirmed": {"by", "at"} | None, "completed_at": iso | None,
"updated_at": iso, "updated_by": email}``.  Values are strings (booleans for ``bool`` fields); an empty value is not stored."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_COUNTRY = re.compile(r"[A-Z]{2}")
_PHONE = re.compile(r"[0-9+()\-./ ]{5,40}")
_VAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .\-/]{3,23}")
_EORI = re.compile(r"[A-Z]{2}[A-Z0-9]{1,15}")
_USCC = re.compile(r"[0-9A-Z]{18}")
_TAXCODE = re.compile(r"[0-9A-Z]{15,20}")


class CompanyFormError(ValueError):
    """A patch the form refuses; the message names the field."""


@dataclass(frozen=True)
class Field:
    id: str
    group: str
    kind: str = "text"          # text | email | country | phone | vat | eori | uscc | taxcode | bool | multiline
    max_len: int = 200
    bank: bool = False          # bank details: never in an audit text, a digest or a log; hidden from everyone but the form's readers


@dataclass(frozen=True)
class Form:
    key: str
    fields: tuple
    required: tuple              # every one must be present to SAVE (``any_of`` groups below count as one)
    complete: tuple              # every one must be present for the form to count as completed (confirm + owner report)
    any_of: tuple = ()           # ((field, field), ...): at least one of each group is needed to save

    def by_id(self) -> dict:
        return {f.id: f for f in self.fields}


PROJECT_FORM = Form(
    key="project",
    fields=(
        Field("legal_name", "legal"), Field("legal_form", "legal", max_len=80), Field("reg_no", "legal", max_len=60),
        Field("vat_no", "legal", "vat"), Field("reg_address", "legal", "multiline", 400), Field("country", "legal", "country"),
        Field("signatory_name", "signatory"), Field("signatory_title", "signatory", max_len=120),
        Field("signatory_email", "signatory", "email"), Field("billing_email", "signatory", "email"),
        Field("delivery_address", "delivery", "multiline", 400), Field("delivery_city", "delivery", max_len=120),
        Field("delivery_postal", "delivery", max_len=32), Field("delivery_country", "delivery", "country"),
        Field("carrier_contact_name", "delivery"), Field("carrier_contact_phone", "delivery", "phone"),
        Field("importer_name", "delivery"), Field("eori", "delivery", "eori"), Field("preferred_carrier", "delivery", max_len=80),
    ),
    required=("legal_name", "country", "billing_email"),
    complete=("legal_name", "country", "billing_email", "reg_address", "signatory_name"),
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_doc() -> dict:
    return {"values": {}, "confirmed": None, "completed_at": None, "updated_at": None, "updated_by": None}


def _clean(field: Field, raw: Any) -> Any:
    """The stored form of one value, or None for "empty"; raises ``CompanyFormError`` for a value the field refuses."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if field.kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ("yes", "true", "1"):
            return True
        if str(raw).strip().lower() in ("no", "false", "0"):
            return False
        raise CompanyFormError(f"{field.id}: yes or no")
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise CompanyFormError(f"{field.id}: text expected")
    text = str(raw).strip()
    if field.kind != "multiline" and ("\n" in text or "\r" in text):
        raise CompanyFormError(f"{field.id}: one line expected")
    if len(text) > field.max_len:
        raise CompanyFormError(f"{field.id}: at most {field.max_len} characters")
    if field.kind == "email":
        text = text.lower()
        if not _EMAIL.fullmatch(text):
            raise CompanyFormError(f"{field.id}: an e-mail address is required")
    elif field.kind == "country":
        text = text.upper()
        if not _COUNTRY.fullmatch(text):
            raise CompanyFormError(f"{field.id}: a 2-letter ISO country code (e.g. DE)")
    elif field.kind == "phone" and not _PHONE.fullmatch(text):
        raise CompanyFormError(f"{field.id}: digits, spaces and + ( ) - . / only")
    elif field.kind == "vat" and not _VAT.fullmatch(text):
        raise CompanyFormError(f"{field.id}: 4-24 letters, digits, space . - /")
    elif field.kind == "eori":
        text = text.upper().replace(" ", "")
        if not _EORI.fullmatch(text):
            raise CompanyFormError(f"{field.id}: two letters of the country and up to 15 letters or digits")
    elif field.kind == "uscc":
        text = text.upper().replace(" ", "")
        if not _USCC.fullmatch(text):
            raise CompanyFormError(f"{field.id}: 18 characters (digits and capital letters)")
    elif field.kind == "taxcode":
        text = text.upper().replace(" ", "")
        if not _TAXCODE.fullmatch(text):
            raise CompanyFormError(f"{field.id}: 15-20 characters (digits and capital letters)")
    return text


def is_complete(form: Form, values: dict) -> bool:
    return all(values.get(k) not in (None, "") for k in form.complete)


def apply_patch(form: Form, doc: dict, patch: Any, actor: str, *, now: Optional[str] = None) -> tuple:
    """``(new document, changes)``; ``changes`` is ``{field: (old, new)}`` (empty = nothing changed, the document is returned
    untouched).  Unknown fields, bad values and a patch that would leave a required field empty are refused."""
    if not isinstance(patch, dict):
        raise CompanyFormError("an object {field: value} is required")
    fields = form.by_id()
    unknown = sorted(str(k) for k in patch if str(k) not in fields)
    if unknown:
        raise CompanyFormError("unknown fields: " + ", ".join(unknown))
    old = dict((doc or {}).get("values") or {})
    values = dict(old)
    for key, raw in patch.items():
        cleaned = _clean(fields[str(key)], raw)
        if cleaned is None:
            values.pop(str(key), None)
        else:
            values[str(key)] = cleaned
    for key in form.required:
        if values.get(key) in (None, ""):
            raise CompanyFormError(f"{key}: required")
    for group in form.any_of:
        if not any(values.get(k) not in (None, "") for k in group):
            raise CompanyFormError(" or ".join(group) + ": at least one is required")
    changes = {k: (old.get(k), values.get(k)) for k in sorted(set(old) | set(values)) if old.get(k) != values.get(k)}
    if not changes:
        return doc, {}
    stamp = now or _now()
    new = {**empty_doc(), **(doc or {}), "values": values, "confirmed": None, "updated_at": stamp, "updated_by": actor.lower()}
    if new.get("completed_at") is None and is_complete(form, values):
        new["completed_at"] = stamp
    return new, changes


def confirm(form: Form, doc: dict, actor: str, *, now: Optional[str] = None) -> dict:
    if not is_complete(form, (doc or {}).get("values") or {}):
        raise CompanyFormError("fill the required details first: " + ", ".join(
            k for k in form.complete if ((doc or {}).get("values") or {}).get(k) in (None, "")))
    return {**doc, "confirmed": {"by": actor.lower(), "at": now or _now()}}


def audit_changes(form: Form, changes: dict) -> dict:
    """What the audit may say: values for ordinary fields, only that they changed for bank fields."""
    by_id = form.by_id()
    out: dict = {"fields": sorted(changes)}
    plain = {k: {"old": o, "new": n} for k, (o, n) in changes.items() if not by_id[k].bank}
    if plain:
        out["changes"] = plain
    if any(by_id[k].bank for k in changes):
        out["bank_changed"] = True
    return out


def public_view(form: Form, doc: Optional[dict], *, include_bank: bool = True) -> dict:
    doc = doc or empty_doc()
    by_id = form.by_id()
    values = {k: v for k, v in (doc.get("values") or {}).items() if k in by_id and (include_bank or not by_id[k].bank)}
    return {"values": values, "confirmed": doc.get("confirmed"), "completed": doc.get("completed_at") is not None,
            "completed_at": doc.get("completed_at"), "updated_at": doc.get("updated_at"), "updated_by": doc.get("updated_by"),
            "ready_to_confirm": is_complete(form, doc.get("values") or {})}
