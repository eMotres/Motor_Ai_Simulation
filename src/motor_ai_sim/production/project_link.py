"""A production seen through the development project that links its motor (owner 2026-11-02).

"The motor's production is linked to the project, and a project can have several motors (M1, G1, G2)."  There is no
manual production <-> project link: a production ``<die>/<config>`` belongs to the project whose ``linked_motors`` hold
that same die/config (the motor index of the Development module allows one project per motor).

A project MEMBER (project role ``owner`` / ``engineer`` / ``customer``) gets a derived, read-only production role
``"viewer"``.  It is derived at request time from the project as it is NOW - no grant is written - so unlinking the
motor or removing the member removes it at once.  What a viewer may open is decided by the routes:

* the production header (title, revisions, status), never its member list or the cost-sharing flag;
* documents that are not ``internal`` (and only their files: no part drawing, BOM attachment or factory-thread file);
* threads marked ``customer_visible`` (an editor's choice; unmarked = factory-only, the default);
* tests and their comparison - project owners/engineers always, a project customer only behind the configuration's
  ``tests_visible_to_customer`` flag (motor_tests.access);
* never parts, BOM, tooling, costs, procurement, schedule, process, proposals or the audit.

The factory (production role ``oem``) is unaffected: a production member keeps its own production role, and nothing
here opens the project to a production member.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

#: the derived production role of a project member
VIEWER = "viewer"
#: project roles that may see the tests of a linked motor without the configuration's customer flag
PROJECT_STAFF_ROLES = ("owner", "engineer")


def _norm(name: Any) -> str:
    from motor_ai_sim.production.store import _safe_slug
    return _safe_slug(str(name or "")).casefold()


def _same_motor(a_die: Any, a_cfg: Any, b_die: Any, b_cfg: Any) -> bool:
    """Same motor, compared the way the production store names its folders (spaces / slashes / case do not matter)."""
    return bool(str(a_die or "").strip() and str(a_cfg or "").strip()) and \
        _norm(a_die) == _norm(b_die) and _norm(a_cfg) == _norm(b_cfg)


def is_linked(project: Optional[dict], die: str, config: str) -> bool:
    return any(isinstance(m, dict) and _same_motor(m.get("die"), m.get("config"), die, config)
               for m in (project or {}).get("linked_motors") or [])


def linking_project(die: str, config: str) -> Optional[dict]:
    """The project whose linked motors hold ``die/config`` now, or ``None``.

    The motor index names the project (one project per motor); the project itself must still list the link, so a stale
    index row never grants anything.  When the index has no row under this spelling, the projects are scanned."""
    from motor_ai_sim.development import store as DS
    try:
        index = (DS.load_index().get("motors") or {})
    except Exception:                                       # noqa: BLE001 - no development store: nothing is linked
        return None
    candidates = []
    for key, row in index.items():
        if not isinstance(row, dict):
            continue
        # the index key is "<die>\0<config>" (casefolded); older rows carry no die / config fields
        k_die, _, k_cfg = str(key).partition("\0")
        if _same_motor(row.get("die") or k_die, row.get("config") or k_cfg, die, config):
            candidates.append(str(row.get("project_id") or ""))
    for pid in candidates:
        project = DS.get_project(pid) if pid else None
        if project is not None and is_linked(project, die, config):
            return project
    if candidates:
        return None
    try:
        projects = DS.list_projects()
    except Exception:                                       # noqa: BLE001
        return None
    return next((p for p in projects if is_linked(p, die, config)), None)


def project_role(email: str, project: Optional[dict]) -> Optional[str]:
    if not project or not str(email or "").strip():
        return None
    from motor_ai_sim.development import access as DA
    role = DA.membership(str(email), project)
    from motor_ai_sim.development import MEMBER_ROLES
    return role if role in MEMBER_ROLES else None


def viewer_context(email: str, die: str, config: str) -> Optional[Dict[str, Any]]:
    """``{"project_id", "project_role"}`` when ``email`` is a member of the project that links ``die/config``, else None."""
    project = linking_project(die, config)
    role = project_role(email, project)
    if role is None:
        return None
    return {"project_id": str(project.get("id") or ""), "project_role": role}


def viewer_sees_tests(ctx: Optional[Dict[str, Any]], tests_doc: Optional[dict]) -> bool:
    """A viewer sees the tests: project staff always, a project customer only behind ``tests_visible_to_customer``."""
    if not ctx:
        return False
    if ctx.get("project_role") in PROJECT_STAFF_ROLES:
        return True
    return bool(((tests_doc or {}).get("settings") or {}).get("tests_visible_to_customer"))


def customer_visible_thread(thread: Any) -> bool:
    return isinstance(thread, dict) and bool(thread.get("customer_visible"))


def visible_documents(documents: list) -> list:
    """Documents a viewer may see: every document that is not internal."""
    return [d for d in documents or [] if isinstance(d, dict) and not d.get("internal")]


def viewer_file_ids(die: str, config: str) -> set:
    """The ONLY files a viewer may download: those of visible documents and the attachments of customer-visible threads."""
    from motor_ai_sim.production import store
    docs = (store.load_file(die, config, "documents.json") or {}).get("documents", [])
    ids = {d.get("file_id") for d in visible_documents(docs) if d.get("file_id")}
    for t in (store.load_file(die, config, "threads.json") or {}).get("threads", []):
        if customer_visible_thread(t):
            for m in t.get("messages") or []:
                for a in (m.get("attachments") if isinstance(m, dict) else None) or []:
                    if isinstance(a, dict) and a.get("file_id"):
                        ids.add(a["file_id"])
    return ids


def viewer_header(production: dict) -> dict:
    """The production record as a viewer receives it: no member list (the factory's accounts) and no cost flag."""
    out = {k: v for k, v in production.items() if k not in ("members", "share_costs_with_oem")}
    out["members"] = []
    return out
