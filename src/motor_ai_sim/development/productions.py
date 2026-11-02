"""The productions of a project's linked motors (owner 2026-11-02).

``GET /api/projects/{project_id}/productions``: one entry per linked motor that has a Production record (a project can
link several motors - M1, G1, G2 - so several entries).  There is no manual production <-> project link; the link IS the
project's ``linked_motors`` (``production.project_link``).

Each entry tells what the CALLER would see when it opens that production in the Production module, with the same rules
the production routes apply:

* a production member (owner / engineer / oem) and a global admin: their own production view;
* any other project member (project ``owner`` / ``engineer`` / ``customer``): the derived read-only ``viewer`` - visible
  (not internal) documents, customer-visible threads, the tests (a project customer only behind the configuration's
  ``tests_visible_to_customer`` flag);
* nothing about costs, the member list, parts or factory-only threads ever leaves here.

Global engineers who are not project members see the project (``development.access``) but get no derived production
access: the entry then names the production with ``access: "none"`` and no counts.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response

from motor_ai_sim.development import access

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects/{project_id}", tags=["development-productions"])

#: readiness numbers a project member may see (procurement rows themselves stay in Production: suppliers, dates, notes)
_READINESS_KEYS = ("parts_total_count", "parts_received_count", "received_pct", "late_count", "ready_for_assembly")


def _readiness(die: str, config: str, production: dict) -> Optional[Dict[str, Any]]:
    try:
        from motor_ai_sim.production import routes as PR
        _rows, summary = PR._get_procurement_data(die, config, production)
    except Exception:                                       # noqa: BLE001 - a summary never breaks the project page
        log.debug("project productions: no readiness for %s/%s", die, config, exc_info=True)
        return None
    if not isinstance(summary, dict) or not summary.get("parts_total_count"):
        return None
    return {k: summary.get(k) for k in _READINESS_KEYS}


def _tests(die: str, config: str) -> Dict[str, Any]:
    from motor_ai_sim.motor_tests import store as MTS
    try:
        doc = MTS.load(die, config) if MTS.exists(die, config) else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def _entry(user: dict, project: dict, link: dict) -> Optional[Dict[str, Any]]:
    from motor_ai_sim.production import access as PA
    from motor_ai_sim.production import models as PM
    from motor_ai_sim.production import project_link
    from motor_ai_sim.production import store as PS
    die, config = str(link.get("die") or ""), str(link.get("config") or "")
    try:
        production = PA.load_production(die, config) if die and config else None
    except ValueError:
        production = None
    if not isinstance(production, dict) or not production.get("die"):
        return None
    p_die, p_cfg = str(production.get("die")), str(production.get("config") or config)
    email = str(user.get("email") or "")
    admin = PA.is_admin(user)
    prod_role = PM.is_member(email, production)
    proj_role = project_link.project_role(email, project)
    if prod_role is not None:
        role = prod_role
    elif admin:
        role = "admin"
    elif proj_role is not None:
        role = project_link.VIEWER
    else:
        role = None
    title = production.get("title") if isinstance(production.get("title"), dict) else {}
    out: Dict[str, Any] = {
        "die": p_die, "config": p_cfg, "linked_die": die, "linked_config": config,
        "status_in_project": str(link.get("status") or "current"),
        "title": {"en": str(title.get("en") or ""), "zh": str(title.get("zh") or "")},
        "revision": str(production.get("current_revision") or ""),
        "status": str(production.get("status") or "active"),
        "access": "none" if role is None else ("project" if role == project_link.VIEWER else "member"),
        "role": role,
    }
    try:
        from motor_ai_sim import motor_revisions
        info = motor_revisions.label_for(p_die, p_cfg)
        if info:
            out["product_revision"] = info
    except Exception:                                       # noqa: BLE001
        pass
    if role is None:
        return out
    docs = (PS.load_file(p_die, p_cfg, "documents.json") or {}).get("documents", [])
    sees_internal = role in ("admin", "owner", "engineer")
    out["documents"] = len([d for d in docs if isinstance(d, dict)]) if sees_internal \
        else len(project_link.visible_documents(docs))
    threads = [t for t in (PS.load_file(p_die, p_cfg, "threads.json") or {}).get("threads", []) if isinstance(t, dict)]
    if role == project_link.VIEWER:
        threads = [t for t in threads if project_link.customer_visible_thread(t)]
    out["discussions"] = len(threads)
    tests_doc = _tests(p_die, p_cfg)
    viewer_ctx = {"project_id": str(project.get("id") or ""), "project_role": proj_role}
    if role != project_link.VIEWER or project_link.viewer_sees_tests(viewer_ctx, tests_doc):
        tests = [t for t in tests_doc.get("tests") or [] if isinstance(t, dict)]
        by_kind: Dict[str, int] = {}
        for t in tests:
            k = str(t.get("kind") or "")
            by_kind[k] = by_kind.get(k, 0) + 1
        out["tests"] = {"count": len(tests), "by_kind": by_kind}
    readiness = _readiness(p_die, p_cfg, production)
    if readiness is not None:
        out["readiness"] = readiness
    return out


@router.get("/productions")
def project_productions(project_id: str, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    user, project, _role = access.require_project(request, project_id)
    out = []
    seen = set()
    for link in project.get("linked_motors") or []:
        if not isinstance(link, dict):
            continue
        try:
            entry = _entry(user, project, link)
        except Exception:                                   # noqa: BLE001 - one broken production never hides the rest
            log.warning("project %s: production of %s/%s unreadable", project_id, link.get("die"), link.get("config"),
                        exc_info=True)
            continue
        if entry is None or (entry["die"], entry["config"]) in seen:
            continue
        seen.add((entry["die"], entry["config"]))
        out.append(entry)
    return {"productions": out}
