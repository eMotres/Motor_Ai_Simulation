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


#: Fields of a costs payload / cost summary that carry OUR commercial terms - the margin, the selling price, a markup or a
#: quote - and never only what the motor costs (owner 2026-11-02: the factory sees costs only where the production has
#: ``share_costs_with_oem`` on, and even then never our margin or our selling price).
COMMERCIAL_COST_FIELDS = frozenset({
    "margin_pct", "margin", "price_per_motor", "order_price_total", "factory_price_per_motor",
    "markup", "markup_pct", "price", "selling_price", "sale_price",
    "quote", "quotes", "quote_id", "quote_total", "quoted_price",
})

#: What a caller sees of the production's money: everything, the cost rows and cost totals only, or nothing at all.
COST_VIEW_FULL, COST_VIEW_COST_ONLY, COST_VIEW_NONE = "full", "cost", "none"


def cost_view(user: dict, production: dict) -> str:
    """The ONE rule for production money: admins and the production's owner / engineer see everything; the factory (OEM)
    sees the cost rows and cost totals only, and only with ``share_costs_with_oem`` on; anybody else - a project
    "viewer" (project_link) included - sees no cost at all."""
    if is_admin(user):
        return COST_VIEW_FULL
    role = is_member(str(user.get("email") or ""), production)
    if role in ("owner", "engineer"):
        return COST_VIEW_FULL
    if role == "oem" and bool(production.get("share_costs_with_oem")):
        return COST_VIEW_COST_ONLY
    return COST_VIEW_NONE


def can_view_costs(user: dict, production: dict) -> bool:
    return cost_view(user, production) != COST_VIEW_NONE


def _strip_commercial(value):
    if isinstance(value, dict):
        return {k: _strip_commercial(v) for k, v in value.items() if str(k) not in COMMERCIAL_COST_FIELDS}
    if isinstance(value, list):
        return [_strip_commercial(v) for v in value]
    return value


def costs_for_caller(user: dict, production: dict, payload: dict) -> dict:
    """Filter a response that carries costs (``/bom``, ``/costs``, a cost summary) for the caller: as it is for a full
    view, without any commercial field (recursively) for the factory, and empty for anybody else (fail closed - the
    routes refuse or strip earlier, this is the belt)."""
    level = cost_view(user, production)
    if level == COST_VIEW_FULL:
        return payload
    if level == COST_VIEW_COST_ONLY:
        return _strip_commercial(payload)
    return {}


def can_edit_costs(user: dict, production: dict) -> bool:
    if is_admin(user):
        return True
    role = is_member(user["email"], production)
    return role in ("owner", "engineer")


def can_edit_procurement(email: str, production: dict, is_admin: bool) -> bool:
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


def full_view_denial(request: Request, user: dict, production: dict) -> Optional[dict]:
    """``None`` when ``user`` may open the production's content, else the 403 detail.  OEM members must have a signed NDA."""
    role = is_member(user["email"], production)
    if role == "oem" and not is_admin(user):
        identity = auth.caller_identity(request.headers.get("authorization"))
        rec = _user_rec(identity.get("id") or "")
        if not model3d.can_download(identity, rec):
            detail = model3d.denial_detail(identity, rec)
            return {"code": "nda_required", "message": str(detail)}
    return None


def require_member_with_full_view(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member(request, die, config)
    denial = full_view_denial(request, user, production)
    if denial is not None:
        raise HTTPException(status_code=403, detail=denial, headers={"code": "nda_required"})
    return user, production


def require_reader(request: Request, die: str, config: str) -> tuple[dict, dict, str, Optional[dict]]:
    """A production MEMBER (with the NDA gate for the factory), an admin, or a member of the project that links this
    motor (``project_link``: derived at request time, read-only).

    Returns ``(user, production, role, viewer_ctx)``: ``role`` is the production role (``owner`` / ``engineer`` /
    ``oem``), ``"admin"`` for a global admin who is not a member, or ``"viewer"`` for a project member; ``viewer_ctx`` is
    ``{"project_id", "project_role"}`` for a viewer and ``None`` otherwise.  Anybody else gets 404, exactly like a
    missing production.  Only the read routes a viewer may use call this; every other route keeps ``require_member``."""
    from motor_ai_sim.production import project_link
    user = require_signed_in(request)
    production = load_production(die, config)
    if production is None:
        raise HTTPException(status_code=404, detail="Production not found.")
    email = str(user.get("email") or "")
    role = is_member(email, production)
    if role is not None:
        denial = None if is_admin(user) else full_view_denial(request, user, production)
        if denial is not None:
            raise HTTPException(status_code=403, detail=denial, headers={"code": "nda_required"})
        return user, production, role, None
    if is_admin(user):
        return user, production, "admin", None
    ctx = project_link.viewer_context(email, die, config)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Production not found.")
    return user, production, project_link.VIEWER, ctx


def viewer_download_denial(request: Request) -> Optional[dict]:
    """A project viewer downloads a production FILE only with the signed NDA - the same gate as the factory's."""
    identity = auth.caller_identity(request.headers.get("authorization"))
    rec = _user_rec(identity.get("id") or "")
    if not model3d.can_download(identity, rec):
        return {"code": "nda_required", "message": str(model3d.denial_detail(identity, rec))}
    return None


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


def require_procurement_editor(request: Request, die: str, config: str) -> tuple[dict, dict]:
    user, production = require_member_with_full_view(request, die, config)
    is_adm = is_admin(user)
    if not can_edit_procurement(user["email"], production, is_adm):
        raise HTTPException(status_code=403, detail="Procurement editor access required.")
    return user, production


def require_schedule_editor_or_oem(request: Request, die: str, config: str) -> tuple[dict, dict, bool]:
    """Returns (user, production, is_full_editor).

    Full editors: owner, engineer, admin.
    OEM member has limited schedule editing rights (actuals for Ordering, Assembly, Testing).
    """
    user, production = require_member_with_full_view(request, die, config)
    is_adm = is_admin(user)
    is_ed = can_edit(user["email"], production, is_adm)
    role = is_member(user["email"], production)
    is_oem = (role == "oem")
    if not (is_ed or is_oem):
        raise HTTPException(status_code=403, detail="Schedule editor access required.")
    return user, production, is_ed
