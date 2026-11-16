"""Active / Parked / Archived of projects and productions, for staff (admins and engineers) only.

``GET /api/display-state`` answers the shown state of every project and production (explicit or read off the existing
status, see ``motor_ai_sim.display_state``); ``PUT /api/display-state`` sets or clears one.  A customer or factory caller
gets 403 and nothing in their own views depends on this: their lists are filtered exactly as before.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from motor_ai_sim import display_state as DS
from motor_ai_sim.development import access as dev_access
from motor_ai_sim.production import access as prod_access

router = APIRouter(prefix="/api/display-state", tags=["display-state"])


def _require_staff(request: Request) -> dict:
    user = prod_access.require_signed_in(request)
    if not dev_access.is_engineer(user):
        raise HTTPException(status_code=403, detail="Staff access required.")
    return user


def _productions() -> list[dict]:
    from motor_ai_sim.production import routes as PR
    return PR._production_list_for("", True)


@router.get("")
def list_display_states(request: Request):
    _require_staff(request)
    from motor_ai_sim.development import store as project_store
    states = DS.load()["states"]
    projects = {}
    for p in project_store.list_projects():
        pid = str(p.get("id") or "")
        if pid:
            projects[pid] = DS.effective("project", DS.object_key("project", project_id=pid), p.get("status"), states)
    productions = {}
    for rec in _productions():
        die, config = str(rec.get("die") or ""), str(rec.get("config") or "")
        if die and config:
            productions[f"{die}/{config}"] = DS.effective(
                "production", DS.object_key("production", die=die, config=config), rec.get("status"), states)
    return {"states": list(DS.STATES), "projects": projects, "productions": productions}


class DisplayStateChange(BaseModel):
    kind: Literal["project", "production"]
    project_id: str = ""
    die: str = ""
    config: str = ""
    state: Optional[Literal["active", "parked", "archived"]] = None   # None = clear: back to the default of the status
    reason: str = Field(default="", max_length=DS.REASON_MAX)


@router.put("")
def set_display_state(change: DisplayStateChange, request: Request):
    user = _require_staff(request)
    if change.kind == "project":
        from motor_ai_sim.development import store as project_store
        project = project_store.get_project(change.project_id) if project_store.valid_id(change.project_id) else None
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        key, legacy = DS.object_key("project", project_id=change.project_id), project.get("status")
    else:
        production = prod_access.load_production(change.die, change.config) if change.die and change.config else None
        if production is None:
            raise HTTPException(status_code=404, detail="Production not found.")
        key, legacy = DS.object_key("production", die=change.die, config=change.config), production.get("status")
    by = str(user.get("email") or user.get("uid") or "")
    DS.set_state(key, change.state, by=by, reason=change.reason)
    return {"key": key, **DS.effective(change.kind, key, legacy)}
