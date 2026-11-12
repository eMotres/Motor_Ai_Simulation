"""Who may see and change the tests of one configuration.

Production side (``/api/motor-tests/{die}/{config}``), the production module's own membership (and its NDA gate for a
factory member):

* global admin                      view, add, edit, delete, settings (also when the configuration has no production)
* production ``owner`` / ``engineer`` view, add, edit, delete, settings
* production ``oem`` (the factory)  view and ADD (they measure); no edit, no delete
* anybody else                      404 (indistinguishable from a missing production)

Project side (``/api/projects/{id}/motors/tests``), the project motor gate of the Development module: project owners and
engineers and global engineers / admins always; a ``customer`` member only when the configuration's
``tests_visible_to_customer`` flag is on (default off, owner 2026-10-26: «for now, engineers and the owner only»).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException, Request

from motor_ai_sim.production import access as PA
from motor_ai_sim.production import models as PM


def resolve(request: Request, die: str, config: str) -> Dict[str, Any]:
    """Adds two flags (owner 2026-11-12) to the access context: ``internal`` (our own side: a global admin or engineer,
    or a production ``owner`` / ``engineer`` member) sees ``note_internal`` and the comparison with the simulation; the
    factory (``oem``) keeps the comparison but never the internal note; a project viewer sees neither."""
    ctx = _resolve(request, die, config)
    internal = (bool(ctx.get("admin")) or is_global_staff(ctx["user"])
                or (ctx.get("viewer") is None and ctx.get("role") in ("owner", "engineer")))
    ctx["internal"] = internal
    ctx["model_view"] = internal or (ctx.get("viewer") is None and ctx.get("role") == "oem")
    return ctx


def is_global_staff(user: Dict[str, Any]) -> bool:
    from motor_ai_sim.development import access as DA
    return DA.is_engineer(user)


def _resolve(request: Request, die: str, config: str) -> Dict[str, Any]:
    user = PA.require_signed_in(request)
    if PA.is_admin(user):
        production = PA.load_production(die, config)
        role = PM.is_member(str(user.get("email") or ""), production) if production else None
        return {"user": user, "admin": True, "role": role, "production": production,
                "can_view": True, "can_add": True, "can_edit": True}
    # members (an OEM member must have the signed NDA, exactly as for the production itself), or a member of the project
    # that links this motor (production.project_link): read-only, and a project customer only behind the flag
    user, production, role, viewer = PA.require_reader(request, die, config)
    if viewer is not None:
        from motor_ai_sim.motor_tests import store
        from motor_ai_sim.production import project_link
        try:
            doc = store.load(die, config) if store.exists(die, config) else {}
        except ValueError:
            doc = {}
        if not project_link.viewer_sees_tests(viewer, doc):
            raise HTTPException(status_code=404, detail="No tests")
        return {"user": user, "admin": False, "role": role, "production": production, "viewer": viewer,
                "can_view": True, "can_add": False, "can_edit": False}
    edit = role in ("owner", "engineer")
    return {"user": user, "admin": False, "role": role, "production": production,
            "can_view": True, "can_add": edit or role == "oem", "can_edit": edit}


def require_add(ctx: Dict[str, Any]) -> None:
    if not ctx.get("can_add"):
        raise HTTPException(status_code=403, detail="Only production members may add tests.")


def require_edit(ctx: Dict[str, Any]) -> None:
    if not ctx.get("can_edit"):
        raise HTTPException(status_code=403, detail="Only engineers and admins may change or delete tests.")


def email(user: Dict[str, Any]) -> str:
    return str(user.get("email") or "").strip().lower()
