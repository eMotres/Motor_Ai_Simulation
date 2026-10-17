"""Access control for the production module: membership, NDA gate and caller
identity.  Contract: docs/PRODUCTION_MODULE_DESIGN.md section 2."""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request

from motor_ai_sim import auth, model3d, users
from motor_ai_sim.production import models, store


def _identity_from_request(request: Request) -> dict:
    return auth.caller_identity(request.headers.get("authorization"))


def _user_rec(email: str) -> Optional[dict]:
    return users.get_user(email)


def load_production(die: str, config: str) -> Optional[dict]:
    if not store.production_exists(die, config):
        return None
    return store.load_file(die, config, "production.json")


def is_admin(user: dict) -> bool:
    return bool(user.get("role") == "admin")


_is_admin = is_admin


def is_member(email: str, production: dict) -> Optional[str]:
    return models.is_member(email, production)


def can_edit(email: str, production: dict, is_admin: bool) -> bool:
    if is_admin:
        return True
    role = is_member(email, production)
    return role in ("owner", "engineer")


def can_propose(email: str, production: dict, is_admin: bool) -> bool:
    if is_admin:
        return True
    role = is_member(email, production)
    return role in ("owner", "engineer", "oem")


def require_signed_in(request: Request) -> dict:
    user = auth.resolve_user(request.headers.get("authorization"))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return user


def require_member(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user = require_signed_in(request)
    production = load_production(die, config)
    if production is None:
        raise HTTPException(status_code=404, detail="Production not found.")
    admin_flag = bool(user.get("role") == "admin")
    if not admin_flag and is_member(user["email"], production) is None:
        raise HTTPException(status_code=404, detail="Production not found.")
    return user, production


def require_member_with_full_view(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member(request, die, config)
    role = is_member(user["email"], production)
    is_adm = is_admin(user)
    # OEM members must have a signed NDA to open a production
    if role == "oem" and not is_adm:
        identity = auth.caller_identity(request.headers.get("authorization"))
        rec = _user_rec(identity.get("id") or "")
        if not model3d.can_download(identity, rec):
            detail = model3d.denial_detail(identity, rec)
            raise HTTPException(
                status_code=403,
                detail={"code": "nda_required", "message": str(detail)},
                headers={"code": "nda_required"},
            )
    return user, production


def require_editor(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member(request, die, config)
    is_admin = bool(user.get("role") == "admin")
    if not can_edit(user["email"], production, is_admin):
        raise HTTPException(status_code=403, detail="Editor access required.")
    return user, production


def require_editor_with_full_view(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member_with_full_view(request, die, config)
    is_admin = bool(user.get("role") == "admin")
    if not can_edit(user["email"], production, is_admin):
        raise HTTPException(status_code=403, detail="Editor access required.")
    return user, production


def require_proposer_or_editor(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member_with_full_view(request, die, config)
    is_admin = bool(user.get("role") == "admin")
    if not (can_edit(user["email"], production, is_admin) or can_propose(user["email"], production, is_admin)):
        raise HTTPException(status_code=403, detail="Member access required.")
    return user, production
