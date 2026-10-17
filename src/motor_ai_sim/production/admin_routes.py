"""Admin routes for the production module: ``/api/admin/production/*``.

Contract: docs/PRODUCTION_MODULE_DESIGN.md section 5.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from motor_ai_sim import auth, users
from motor_ai_sim.production import models, store
from motor_ai_sim.production.access import _is_admin, load_production

router = APIRouter(prefix="/api/admin/production", tags=["admin-production"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _production_list_admin() -> list[dict]:
    root = store.production_root()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            rec = store.load_file(d.name, c.name, "production.json")
            if not isinstance(rec, dict) or not rec.get("die"):
                continue
            rec = dict(rec)
            rec["members_without_nda"] = _members_without_nda(rec)
            out.append(rec)
    return out


def _members_without_nda(production: dict) -> list[str]:
    from motor_ai_sim import model3d as M3
    out = []
    for m in production.get("members") or []:
        if not isinstance(m, dict):
            continue
        email = m.get("email", "")
        rec = users.get_user(email)
        identity = {"id": email, "is_admin": False, "role": m.get("role", "oem")}
        if not M3.can_download(identity, rec):
            out.append(email)
    return out


@router.get("")
def admin_list(admin_user: dict = Depends(auth.require_admin)):
    return {"productions": _production_list_admin()}


@router.post("/{die}/{config}")
async def admin_create(request: Request, die: str, config: str, admin_user: dict = Depends(auth.require_admin)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON body")
    title = body.get("title")
    if not isinstance(title, dict):
        raise HTTPException(status_code=400, detail="title required")
    order_quantity = int(body.get("order_quantity") or 0)
    members = models.normalize_members(body.get("members"))
    production = models.normalize_production({
        "title": title,
        "order_quantity": order_quantity,
        "members": members,
    }, die, config)
    store.save_file(die, config, "production.json", production)
    store.save_file(die, config, "parts.json", {"parts": []})
    store.save_file(die, config, "bom.json", {"rows": []})
    store.save_file(die, config, "process.json", {"sections": []})
    store.save_file(die, config, "documents.json", {"documents": []})
    store.save_file(die, config, "threads.json", {"threads": []})
    store.save_file(die, config, "proposals.json", {"proposals": []})
    store.save_file(die, config, "revisions.json", {"revisions": []})
    store.append_audit(die, config, models.audit_entry(_email(admin_user), "create", "production", f"{die}/{config}", production))
    return production


@router.delete("/{die}/{config}")
def admin_delete(die: str, config: str, admin_user: dict = Depends(auth.require_admin)):
    if load_production(die, config) is None:
        raise HTTPException(status_code=404, detail="Production not found")
    import shutil
    p = store.path_for(die, config)
    shutil.rmtree(p, ignore_errors=True)
    return {}


@router.put("/{die}/{config}/members")
async def admin_set_members(request: Request, die: str, config: str, admin_user: dict = Depends(auth.require_admin)):
    if load_production(die, config) is None:
        raise HTTPException(status_code=404, detail="Production not found")
    body = await request.json()
    members = models.normalize_members(body.get("members") if isinstance(body, dict) else [])
    store.mutate_file(die, config, "production.json", lambda d: d.update({"members": members, "updated_at": models._now()}))
    store.append_audit(die, config, models.audit_entry(_email(admin_user), "update", "members", f"{die}/{config}", {"members": members}))
    return {}


@router.post("/{die}/{config}/members")
async def admin_add_member(request: Request, die: str, config: str, admin_user: dict = Depends(auth.require_admin)):
    if load_production(die, config) is None:
        raise HTTPException(status_code=404, detail="Production not found")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON body")
    email = str(body.get("email") or "").strip().lower()
    role = str(body.get("role") or "oem")
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    if role not in models.MEMBER_ROLES:
        raise HTTPException(status_code=400, detail="invalid role")

    def mut(d):
        members = list(d.get("members") or [])
        members = [m for m in members if m.get("email") != email]
        members.append({"email": email, "role": role})
        d["members"] = models._unique_members(members)
        d["updated_at"] = models._now()

    store.mutate_file(die, config, "production.json", mut)
    store.append_audit(die, config, models.audit_entry(_email(admin_user), "update", "members", email, {"role": role}))
    return {}


@router.delete("/{die}/{config}/members/{email}")
def admin_remove_member(die: str, config: str, email: str, admin_user: dict = Depends(auth.require_admin)):
    if load_production(die, config) is None:
        raise HTTPException(status_code=404, detail="Production not found")
    email = email.strip().lower()

    def mut(d):
        d["members"] = [m for m in d.get("members", []) if m.get("email") != email]
        d["updated_at"] = models._now()

    store.mutate_file(die, config, "production.json", mut)
    store.append_audit(die, config, models.audit_entry(_email(admin_user), "update", "members", email, {"removed": True}))
    return {}
