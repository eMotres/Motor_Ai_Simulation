"""Proposals of the specification assistant (owner 2026-10-27): a value the assistant suggests for ONE specification
field, kept apart from the draft until the project owner (or an admin) presses Apply.

A proposal is ``{id, field_id, value, reason, recalc, by, created_at, status, applied_by, applied_revision}`` in
``<project>/spec_proposals.json``.  The assistant never writes the draft: ``create`` only validates the value through
the specification's own normaliser (the same one ``PUT /spec`` uses) and stores it; ``apply`` is the only writer, goes
through the same normalise + revision check as ``PUT /spec`` and refuses a stale revision.

``recalc`` tells the owner, BEFORE the change is applied, which recalculations it triggers (owner rule 2026-10-26: a
spec change shows what changed and what it recalculates).  It is derived from the field: operating points, voltage,
cooling, ambient, limits -> coupled thermal + mechanics of the key points; documentation-only fields -> none.
"""
from __future__ import annotations

import copy
import json
import secrets
from typing import Any

from motor_ai_sim.development import models, store

STATUSES = ("open", "applied", "dismissed")
MAX_REASON = 300
MAX_OPEN = 40

#: Customer decisions: the assistant asks, it never proposes (owner 2026-10-27).  Whole sections 6 (project and
#: commercial) and production, the mass target and the target budget.
CUSTOMER_SECTIONS = frozenset({"6", "production", "mission_profile"})
CUSTOMER_FIELDS = frozenset({"4_2", "6_3"})
#: Version-2 fields the specification derives from the key operating points (S1 and peak point).
DERIVED_V2 = frozenset({"1_4", "1_5"})

#: Fields whose change re-runs the coupled thermal + mechanics check of the key points.
_THERMAL_MECHANICS = frozenset({
    "key_points", "1_2", "1_4", "1_5", "1_6", "1_8",
    "2_1", "2_2", "2_3", "2_4", "2_5", "2_6", "2_7", "2_9", "2_11",
    "3_1", "3_2", "3_3", "3_5", "3_6",
    "4_1", "load_type", "load_source", "load_inertia_kg_m2", "startup_mode", "load_characteristic",
})
#: Fields that only move the mechanical check (rotor, bearings, shaft loads, balance).
_MECHANICS_ONLY = frozenset({"4_5", "4_6", "4_7", "4_8"})
#: Documentation / targets / interfaces: nothing is recalculated.
_NO_RECALC = frozenset({
    "1_1", "1_3", "1_7", "1_9", "1_10", "additional_requirements", "2_8", "2_10", "2_12", "3_4",
    "4_3", "4_4", "4_9", "5_1", "5_2", "5_3", "5_4",
})

RECALC_TEXT = {
    "thermal_mechanics": {"en": "coupled thermal + mechanics of the key points",
                          "zh-CN": "关键工作点的热-机械耦合重算"},
    "mechanics": {"en": "mechanics of the key points", "zh-CN": "关键工作点的机械校核"},
    "none": {"en": "none (documentation only)", "zh-CN": "无（仅文档）"},
}


class ProposalError(ValueError):
    """The proposal cannot be stored / applied; the message is meant for the model or the user."""


class ProposalConflict(ProposalError):
    """The draft changed since the caller looked (HTTP 409)."""

    def __init__(self, message: str, revision: int):
        super().__init__(message)
        self.revision = revision


# ── schema helpers ───────────────────────────────────────────────────────────

def _empty_wrapper() -> dict:
    return {"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {},
                      "provenance": {}}, "revision": 0, "version": None}


def draft_wrapper(project_id: str) -> dict:
    return store.load_json(project_id, "draft.json", _empty_wrapper())


def schema_version_of(wrapper: dict) -> int:
    draft = wrapper.get("draft") or {}
    try:
        return int(wrapper.get("schema_version", draft.get("schema_version", 1)))
    except (TypeError, ValueError):
        return 1


def field_info(field_id: str, version: int) -> tuple[dict, dict]:
    """(section, field) of the schema in force, or ``ProposalError``."""
    known = models._field_map(version)
    if field_id not in known:
        raise ProposalError(f"unknown field {field_id}")
    return known[field_id]


def refusal(field_id: str, section_id: str) -> str | None:
    """Why the assistant must not propose this field, or ``None``."""
    if field_id in CUSTOMER_FIELDS or section_id in CUSTOMER_SECTIONS:
        return f"{field_id} is a customer decision - ask the customer, do not propose a value"
    return None


def recalc_for(field_id: str, section_id: str) -> dict:
    """Which recalculations a change of this field triggers."""
    if field_id in _THERMAL_MECHANICS:
        kind = "thermal_mechanics"
    elif field_id in _MECHANICS_ONLY:
        kind = "mechanics"
    elif field_id in _NO_RECALC or section_id in CUSTOMER_SECTIONS or section_id == "5":
        kind = "none"
    else:                                    # a field the table does not know: sections 1-4 are physics - be cautious
        kind = "thermal_mechanics" if section_id in ("1", "2", "3", "4") else "none"
    return {"needed": kind != "none", "kind": kind, "text": dict(RECALC_TEXT[kind])}


def _answer_area(draft: dict, section_id: str) -> dict:
    return draft.setdefault("production" if section_id == "production" else "fields", {})


def current_answer(wrapper: dict, field_id: str, version: int) -> dict:
    """The stored answer of a field (``{value, status}``), ``{value: None, status: missing}`` when absent."""
    section, _ = field_info(field_id, version)
    draft = wrapper.get("draft") or {}
    area = draft.get("production" if section["id"] == "production" else "fields") or {}
    ans = area.get(field_id)
    return copy.deepcopy(ans) if isinstance(ans, dict) else {"value": None, "status": "missing"}


def coerce_value(field: dict, value: Any) -> Any:
    """Be forgiving about what a text model writes: a number or an object delivered as a string."""
    typ = field.get("type", "string")
    if isinstance(value, str) and (typ in ("number", "object", "array", "boolean") or field.get("properties")):
        text = value.strip()
        try:
            return json.loads(text)
        except ValueError:
            if typ == "number":
                try:
                    return float(text.replace(",", "."))
                except ValueError:
                    return value
    return value


def validate(wrapper: dict, field_id: str, value: Any) -> dict:
    """The normalised ``{value, status: "filled"}`` of a single field, validated by the specification's own rules
    (same ``normalize_draft`` as ``PUT /spec``), or ``ProposalError``."""
    version = schema_version_of(wrapper)
    section, field = field_info(field_id, version)
    why = refusal(field_id, section["id"])
    if why:
        raise ProposalError(why)
    if field_id == "mission_profile":
        raise ProposalError("mission_profile cannot be proposed")
    if version >= 2 and field_id in DERIVED_V2:
        raise ProposalError(f"{field_id} is derived from the key operating points - propose key_points instead")
    if isinstance(value, str) and not value.strip():
        raise ProposalError(f"{field_id}: empty value")
    value = coerce_value(field, value)
    trial = copy.deepcopy(wrapper.get("draft") or {})
    trial["schema_version"] = version
    _answer_area(trial, section["id"])[field_id] = {"value": value, "status": "filled"}
    try:
        norm = models.normalize_draft(trial, schema_version=version)
    except (ValueError, TypeError) as exc:
        raise ProposalError(str(exc))
    area = norm.get("production" if section["id"] == "production" else "fields") or {}
    ans = area.get(field_id)
    if not isinstance(ans, dict) or ans.get("status") != "filled" or not models._value_present(ans.get("value")):
        raise ProposalError(f"{field_id}: the value is empty")
    return {"value": ans["value"], "status": "filled"}


# ── the store ────────────────────────────────────────────────────────────────

_FILE = "spec_proposals.json"


def _doc() -> dict:
    return {"proposals": []}


def list_proposals(project_id: str, status: str | None = None) -> list[dict]:
    doc = store.load_json(project_id, _FILE, _doc())
    rows = [p for p in doc.get("proposals", []) if isinstance(p, dict)]
    if status:
        rows = [p for p in rows if p.get("status") == status]
    return sorted(rows, key=lambda p: p.get("created_at", ""))


def get(project_id: str, proposal_id: str) -> dict | None:
    return next((p for p in list_proposals(project_id) if p.get("id") == proposal_id), None)


def with_current(project_id: str, proposals: list[dict]) -> list[dict]:
    """The proposals with the field's CURRENT answer beside them (what would change), read now."""
    wrapper = draft_wrapper(project_id)
    version = schema_version_of(wrapper)
    out = []
    for p in proposals:
        row = copy.deepcopy(p)
        try:
            row["current"] = current_answer(wrapper, p["field_id"], version)
        except ProposalError:
            row["current"] = {"value": None, "status": "missing"}
        out.append(row)
    return out


def create(project_id: str, field_id: str, value: Any, reason: str, *, by: str = "assistant") -> dict:
    """Validate and store a proposal.  A newer proposal for the same field replaces the open one (dismissed as
    ``superseded``).  Raises ``ProposalError`` for an invalid one - nothing is stored then."""
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise ProposalError("a one-line reason is required")
    reason = reason[:MAX_REASON]
    wrapper = draft_wrapper(project_id)
    version = schema_version_of(wrapper)
    section, _ = field_info(field_id, version)
    answer = validate(wrapper, field_id, value)
    proposal = {"id": "p-" + secrets.token_hex(5), "field_id": field_id, "value": answer["value"], "reason": reason,
                "recalc": recalc_for(field_id, section["id"]), "by": by, "created_at": models.now(),
                "status": "open", "applied_by": None, "applied_revision": None}

    def mutate(doc: dict) -> None:
        rows = doc.setdefault("proposals", [])
        for p in rows:
            if p.get("field_id") == field_id and p.get("status") == "open":
                p.update(status="dismissed", dismissed_by="superseded", dismissed_at=models.now())
        if sum(1 for p in rows if p.get("status") == "open") >= MAX_OPEN:
            raise ProposalError("too many open proposals - apply or dismiss some first")
        rows.append(proposal)

    store.mutate_json(project_id, _FILE, mutate, default=_doc())
    store.append_audit(project_id, by, "spec.proposal.create", "spec_proposal", proposal["id"],
                       {"field": field_id})
    return proposal


def dismiss(project_id: str, proposal_id: str, actor_email: str) -> dict:
    result: dict = {}

    def mutate(doc: dict) -> None:
        for p in doc.get("proposals", []):
            if p.get("id") == proposal_id:
                if p.get("status") != "open":
                    result["error"] = f"proposal is already {p.get('status')}"
                    return
                p.update(status="dismissed", dismissed_by=actor_email.lower(), dismissed_at=models.now())
                result["proposal"] = copy.deepcopy(p)
                return
        result["missing"] = True

    store.mutate_json(project_id, _FILE, mutate, default=_doc())
    if result.get("missing"):
        raise LookupError("proposal not found")
    if result.get("error"):
        raise ProposalConflict(result["error"], -1)
    store.append_audit(project_id, actor_email, "spec.proposal.dismiss", "spec_proposal", proposal_id,
                       {"field": result["proposal"]["field_id"]})
    return result["proposal"]


def apply(project_id: str, proposal_id: str, actor_email: str, expected_revision: int) -> dict:
    """Write the proposal's value into the draft through the SAME normalise + revision rules as ``PUT /spec`` and mark
    it applied.  Raises ``LookupError`` (no such proposal), ``ProposalConflict`` (stale revision, already closed) or
    ``ProposalError`` (the value no longer validates against the current draft).  Returns ``{proposal, revision,
    draft, completeness}``."""
    out: dict = {}

    def mutate_proposals(doc: dict) -> None:
        prop = next((p for p in doc.get("proposals", []) if p.get("id") == proposal_id), None)
        if prop is None:
            out["missing"] = True
            return
        if prop.get("status") != "open":
            out["closed"] = prop.get("status")
            return
        field_id, value = prop["field_id"], prop["value"]

        def mutate_draft(current: dict) -> None:
            revision = int(current.get("revision", 0))
            if revision != expected_revision:
                out["conflict"] = revision
                return
            version = schema_version_of(current)
            try:
                section, _ = field_info(field_id, version)
                why = refusal(field_id, section["id"])
                if why:
                    raise ProposalError(why)
                answer = validate(current, field_id, value)
                trial = copy.deepcopy(current.get("draft") or {})
                trial["schema_version"] = version
                _answer_area(trial, section["id"])[field_id] = answer
                draft = models.normalize_draft(trial, schema_version=version)
            except (ValueError, TypeError) as exc:       # ProposalError is a ValueError
                out["invalid"] = str(exc)
                return
            draft["provenance"] = copy.deepcopy((current.get("draft") or {}).get("provenance", {}))
            draft["schema_version"] = version
            current.update({"draft": draft, "schema_version": version, "revision": revision + 1,
                            "updated_at": models.now()})
            out["saved"] = copy.deepcopy(current)

        store.mutate_json(project_id, "draft.json", mutate_draft, default=_empty_wrapper())
        if out.get("saved") is not None:
            prop.update(status="applied", applied_by=actor_email.lower(), applied_at=models.now(),
                        applied_revision=out["saved"]["revision"])
            out["proposal"] = copy.deepcopy(prop)

    store.mutate_json(project_id, _FILE, mutate_proposals, default=_doc())
    if out.get("missing"):
        raise LookupError("proposal not found")
    if out.get("closed"):
        raise ProposalConflict(f"proposal is already {out['closed']}", -1)
    if out.get("conflict") is not None:
        raise ProposalConflict("Draft changed; reload before applying", out["conflict"])
    if out.get("invalid"):
        raise ProposalError(out["invalid"])
    saved = out["saved"]
    store.append_audit(project_id, actor_email, "spec.proposal.apply", "spec_proposal", proposal_id,
                       {"field": out["proposal"]["field_id"], "revision": saved["revision"]})
    return {"proposal": out["proposal"], "revision": saved["revision"], "draft": saved["draft"],
            "completeness": models.completeness(saved["draft"])}
