from __future__ import annotations

from fastapi import HTTPException, Request
from motor_ai_sim import auth
from motor_ai_sim.development import store


def is_engineer(user: dict) -> bool:
    return str(user.get("role") or "").lower() in {"admin", "engineer"}


def membership(email: str, project: dict) -> str | None:
    email = email.strip().lower()
    for member in project.get("members", []):
        if isinstance(member, dict) and str(member.get("email") or "").strip().lower() == email:
            return member.get("role")
    return None


def can_view(user: dict, project: dict) -> bool:
    return is_engineer(user) or membership(str(user.get("email") or ""), project) is not None


def can_edit_spec(user: dict, project: dict) -> bool:
    """Owner rule 2026-10-26: the specification is changed ONLY by the project owner (project role "owner") and by
    global admins.  Project engineers, customers and global engineers who are not admins only view it: they test in
    Configure and give recommendations in the project thread."""
    if str(user.get("role") or "").lower() == "admin":
        return True
    return membership(str(user.get("email") or ""), project) == "owner"


def require_signed_in(request: Request) -> dict:
    from motor_ai_sim.production.access import require_signed_in as production_require_signed_in
    return production_require_signed_in(request)


def require_project(request: Request, project_id: str, *, edit: bool = False) -> tuple[dict, dict, str | None]:
    user = require_signed_in(request)
    project = store.get_project(project_id)
    if project is None or not can_view(user, project):
        raise HTTPException(status_code=404, detail="Project not found")
    role = membership(str(user.get("email") or ""), project)
    if edit and not (is_engineer(user) or role in ("owner", "engineer")):
        raise HTTPException(status_code=403, detail="Project engineer access required")
    return user, project, role


def require_spec_editor(request: Request, project_id: str) -> tuple[dict, dict, str | None]:
    user, project, role = require_project(request, project_id)
    if not can_edit_spec(user, project):
        raise HTTPException(status_code=403, detail="Only the project owner or an admin can edit the specification")
    return user, project, role
