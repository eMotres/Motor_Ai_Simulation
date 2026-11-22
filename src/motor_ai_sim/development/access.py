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


def sees_prices(user: dict | None, project: dict) -> bool:
    """Who may read the project's prices (owner 2026-11-22: "the owner must be able to regulate who sees the price"): a global
    admin, every project OWNER (always, not switchable) and a member whose entry carries ``sees_prices: true``.  The quote, the
    documents and the messages marked price-restricted all ask this one question.  Never a global engineer who is no member."""
    if not user:
        return False
    if str(user.get("role") or "").lower() == "admin":
        return True
    email = str(user.get("email") or "").strip().lower()
    for member in project.get("members", []):
        if isinstance(member, dict) and str(member.get("email") or "").strip().lower() == email:
            return str(member.get("role") or "").lower() == "owner" or member.get("sees_prices") is True
    return False


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
    return project_for(require_signed_in(request), project_id, edit=edit)


def project_for(user: dict, project_id: str, *, edit: bool = False) -> tuple[dict, dict, str | None]:
    """``require_project`` for a caller that is already resolved (the web session or an MCP agent acting for its owner):
    404 unless ``user`` may view the project, 403 for ``edit`` without project-engineer rights."""
    project = store.get_project(project_id) if store.valid_id(project_id) else None
    if project is None or not can_view(user, project):
        raise HTTPException(status_code=404, detail="Project not found")
    role = membership(str(user.get("email") or ""), project)
    if edit and not (is_engineer(user) or role in ("owner", "engineer")):
        raise HTTPException(status_code=403, detail="Project engineer access required")
    return user, project, role


def require_spec_editor(request: Request, project_id: str) -> tuple[dict, dict, str | None]:
    return spec_editor_for(require_signed_in(request), project_id)


def spec_editor_for(user: dict, project_id: str) -> tuple[dict, dict, str | None]:
    """``require_spec_editor`` for an already resolved caller: 404 / 403 unless ``can_edit_spec``."""
    user, project, role = project_for(user, project_id)
    if not can_edit_spec(user, project):
        raise HTTPException(status_code=403, detail="Only the project owner or an admin can edit the specification")
    return user, project, role
