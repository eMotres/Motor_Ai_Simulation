"""Owner-queue items the server itself creates (owner 2026-11-25: on a submit the admin gets a card and confirms whether an
agent starts a simulation or an optimisation of that project).

Every NEW specification version (customer or staff; an "unchanged" submit creates no version and no item) puts one ``decision``
in the owner queue (simulate on existing sizes / optimise geometry / not now), with the project set so the project priority
orders it.  The app starts nothing on the answer: the coordinator reads it and starts the session.  The items belong to the
internal token ``owner_queue.SPEC_SUBMIT_TOKEN``, a name no real token can have (token names are ``[A-Za-z0-9_.-]``), so no agent
token lists or withdraws them.

:func:`spec_submitted` never raises: a queue failure is logged and the submit goes on.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

ASKED_BY = "app"
#: The owner's choice on a submit (owner 2026-11-25).
START_OPTIONS = ("Simulate on existing sizes", "Optimise geometry", "Not now")
START_SUFFIX = ". Start?"
MAX_CHANGED = 15


def _one_line(s: Any, limit: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def completeness_line(c: dict) -> str:
    """``Completeness: 41 of 45 required answered, 4 not known yet; simulation ready`` from ``models.completeness`` (pure)."""
    line = f"Completeness: {int(c.get('answered', 0))} of {int(c.get('total_required', 0))} required answered"
    if c.get("unknown"):
        line += f", {int(c['unknown'])} not known yet"
    missing = c.get("simulation_missing") or []
    if c.get("simulation_ready", not missing):
        return line + "; simulation ready"
    return line + "; simulation needs " + ", ".join(_one_line(m.get("label") or m.get("field"), 60) for m in missing[:5]) + \
        (f", +{len(missing) - 5}" if len(missing) > 5 else "")


def od_mode(draft: dict) -> str | None:
    """The diameter mode of the specification, ``existing`` | ``custom`` (the size envelope ``4_1``), else None (pure).
    Until the form stores it there is none, and the card recommends nothing."""
    v = (((draft or {}).get("fields") or {}).get("4_1") or {}).get("value")
    m = v.get("od_mode") if isinstance(v, dict) else None
    return m if m in ("existing", "custom") else None


def changed_fields(old: dict | None, new: dict) -> list[str]:
    """The labels of the specification fields whose answer differs between two versions, in form order (pure).  Labels only,
    never values."""
    if not isinstance(old, dict):
        return []
    from motor_ai_sim.development import models
    out = []
    for section in models.schema(int(new.get("schema_version", 2) or 2))["sections"]:
        if section["id"] == "mission_profile":
            mp = ("mission_profile", "mission_profile_status")
            if [old.get(k) for k in mp] != [new.get(k) for k in mp]:
                out.append(_one_line(_label(section) or "Mission profile", 60))
            continue
        area = "production" if section["id"] == "production" else "fields"
        a, b = old.get(area) or {}, new.get(area) or {}
        for field in section["fields"]:
            if a.get(field["id"]) != b.get(field["id"]):
                out.append(_one_line(_label(field) or field["id"], 60))
    return out


def _label(d: dict) -> str:
    lab = d.get("short_label") or d.get("label") or d.get("title")
    return str(lab.get("en") or "") if isinstance(lab, dict) else str(lab or "")


def spec_item(user: dict, project_id: str, version: dict, project: dict, *, completeness: dict, app_url: str, staff: bool,
              changed: list[str] | None = None) -> dict:
    """The owner-queue decision for one submitted version (pure): start a simulation, an optimisation, or nothing yet.
    The app starts nothing on the answer; the coordinator reads it and starts the session."""
    from motor_ai_sim import owner_queue as OQ
    title = _one_line(project.get("title") or project_id, 200)
    who = _one_line(user.get("email") or version.get("created_by") or "?", 200)
    lines = [f"Submitted by: {who} ({'staff' if staff else 'customer'})",
             f"Company: {_one_line(project.get('customer_org') or '-', 200)}",
             completeness_line(completeness)]
    if changed is not None:
        more = f", +{len(changed) - MAX_CHANGED}" if len(changed) > MAX_CHANGED else ""
        listed = ", ".join(changed[:MAX_CHANGED]) + more if changed else "nothing in the form fields"
        lines.append(f"Changed since v{int(version.get('version') or 1) - 1}: {listed}")
    lines.append(f"{app_url}/projects/{project_id}/specification")
    rec = START_OPTIONS[0] if od_mode(version.get("draft") or {}) == "existing" else None
    question = _one_line(f"Specification v{version.get('version')} submitted: {title}", OQ.MAX_QUESTION - len(START_SUFFIX))
    return {"kind": "decision", "asked_by": ASKED_BY, "project_id": project_id, "question": question + START_SUFFIX,
            "options": [{"label": o, "recommended": o == rec} for o in START_OPTIONS],
            "detail": "\n".join(lines)[:OQ.MAX_DETAIL]}


def spec_submitted(user: dict, project_id: str, version: dict, project: dict) -> None:
    """Create the owner-queue decision for a new specification version.  Never raises."""
    try:
        from motor_ai_sim import auth_email as E, owner_queue as OQ
        from motor_ai_sim.development import access, models, store
        draft = version.get("draft") or {}
        n = int(version.get("version") or 1)
        changed = None
        if n > 1:
            prev = store.load_json(project_id, f"spec/v{n - 1}.json", None)
            changed = changed_fields((prev or {}).get("draft"), draft) if isinstance(prev, dict) else None
        body = spec_item(user, project_id, version, project or {}, completeness=models.completeness(draft),
                         app_url=E.app_url(), staff=access.is_engineer(user), changed=changed)
        OQ.create_item(OQ.SPEC_SUBMIT_TOKEN, body)
    except Exception:                                       # noqa: BLE001 - never block the submit
        log.warning("owner-queue item for the specification submit of %s failed", project_id, exc_info=True)
