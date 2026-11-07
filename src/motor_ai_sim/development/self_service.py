"""Self-service projects (owner 2026-10-30): any signed-in account may open its own development project.

A customer who signed up (Google or e-mail, role "user") opens a project, becomes its project OWNER (customer side - the one role
that edits the specification, ``access.can_edit_spec``) and fills the requirements; Motres staff (admins and engineers) already
see and edit every project (``access.can_view`` / ``require_project``), so nothing has to be granted for them.  The platform
agreement gate (428 until accepted) stays in ``routes.create_project``.

Two things live here so the route stays short and the tests can pin them: the project id generated from the title, and the
per-account limit of open projects that stops abuse of the open sign-up.
"""
from __future__ import annotations

import logging
import re
import secrets
import unicodedata

from motor_ai_sim.development import access, project_limits, store

#: Open (not closed) projects one account may own unless an admin set an override (project_limits.py).  Staff are not limited:
#: they create projects for customers.
MAX_OPEN_OWNED_PROJECTS = project_limits.DEFAULT_OPEN_LIMIT
LIMIT_CODE = "project_limit"
_MAX_SLUG = 48
_MAX_TITLE = 120
log = logging.getLogger(__name__)


#: The name of a customer's project until its specification says which motor it is (owner 2026-11-07: we name the
#: projects, not the users, so that the name tells which motor it is).
NEW_PROJECT_TITLE = "New project"


def new_project_seed() -> str:
    """What a customer's project id is made from: nothing of the customer's in the address."""
    return "project " + secrets.token_hex(3)


def _value(fields: dict, key: str):
    field = fields.get(key)
    return field.get("value") if isinstance(field, dict) else None


def _number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:g}" if number > 0 else ""


def spec_title(draft: dict) -> str:
    """"Vehicle traction motor, 100 N·m, 1500 rpm": the application, motor or generator, and the continuous point.
    Empty while the specification names neither the application nor a point."""
    fields = (draft or {}).get("fields") or {}
    application = str(_value(fields, "1_1") or "").strip()
    kind = str(_value(fields, "1_2") or "").strip().lower()
    machine = "generator" if kind.startswith("generator") else ("motor" if kind.startswith("motor") else "")
    if machine and machine in application.lower():
        machine = ""
    point = _value(fields, "1_4")
    if not (isinstance(point, dict) and _number(point.get("torque_Nm"))):
        rows = _value(fields, "key_points")
        point = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    torque, speed = _number(point.get("torque_Nm")), _number(point.get("speed_rpm"))
    parts = [" ".join(x for x in (application, machine) if x)]
    if torque:
        parts.append(f"{torque} N·m")
    if speed:
        parts.append(f"{speed} rpm")
    return ", ".join(x for x in parts if x)[:_MAX_TITLE] if (application or torque) else ""


def refresh_title(project_id: str, draft: dict) -> None:
    """After a save of the specification: the name of a project we name follows it.  Never blocks the save."""
    try:
        project = store.get_project(project_id)
        if not project or not project.get("auto_title"):
            return
        title = spec_title(draft)
        if not title or title == project.get("title"):
            return

        def mutate(current: dict) -> None:
            if current.get("auto_title"):
                current["title"] = title
        store.mutate_project(project_id, mutate)
    except Exception:                                       # noqa: BLE001 - the name must never block a save
        log.warning("project title not refreshed for %s", project_id, exc_info=True)


def slug_from_title(title: str) -> str:
    """Lowercase ASCII slug of a project title (``store.valid_id`` shape); "project" when nothing usable is left (e.g. a Chinese title)."""
    text = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:_MAX_SLUG].strip("-")
    return slug or "project"


def candidate_ids(title: str):
    """``gonzalo-7-inch``, ``gonzalo-7-inch-2``, ``-3`` ... - the caller takes the first that is free."""
    base = slug_from_title(title)
    yield base
    for n in range(2, 1000):
        yield f"{base}-{n}"


def open_owned_count(email: str) -> int:
    """Projects the account owns (project role "owner") that are not closed."""
    email = str(email or "").strip().lower()
    count = 0
    for project_id in store.membership_projects(email):
        project = store.get_project(project_id)
        if (project and access.membership(email, project) == "owner"
                and str(project.get("status") or "") != "closed"):
            count += 1
    return count


def limit_of(user: dict) -> int | None:
    """The open projects this account may own: its admin override, else the default; ``None`` = unlimited (staff, or "unlimited")."""
    if access.is_engineer(user):
        return None
    return project_limits.limit_for(str(user.get("email") or ""))


def limit_reached(user: dict) -> bool:
    """True for a non-staff account that already owns as many open projects as its limit allows."""
    limit = limit_of(user)
    return limit is not None and open_owned_count(str(user.get("email") or "")) >= limit
