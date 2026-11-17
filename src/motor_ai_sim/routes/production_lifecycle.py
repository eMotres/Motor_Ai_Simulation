"""``/api/production-lifecycle``: prepared / in production / finished of a production (``motor_ai_sim.production_lifecycle``).

* ``GET  ""``                    admins: the state of every production (the Overview and the catalog read it there too)
* ``GET  /{die}/{config}``       admins and the production's members (the factory sees the state; it cannot set it)
* ``PUT  ""``                    admins: ``{die, config, state, payment?, source?}``; ``in_production`` needs ``payment``
  (the owner's rule: production starts when the customer's payment has arrived - a date or a note, no amount).
  Written to the admin audit.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from motor_ai_sim import production_lifecycle as PL
from motor_ai_sim.production import access as prod_access

router = APIRouter(prefix="/api/production-lifecycle", tags=["production-lifecycle"])


def _require_admin(request: Request) -> dict:
    user = prod_access.require_signed_in(request)
    if not prod_access.is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


@router.get("")
def list_states(request: Request):
    _require_admin(request)
    from motor_ai_sim.production import routes as PR
    states = PL.load()["states"]
    out = {}
    for rec in PR._production_list_for("", True):
        die, config = str(rec.get("die") or ""), str(rec.get("config") or "")
        if die and config:
            out[f"{die}/{config}"] = PL.state_of(die, config, states)
    return {"states": list(PL.STATES), "productions": out}


@router.get("/{die}/{config}")
def get_state(die: str, config: str, request: Request):
    user, _production = prod_access.require_member(request, die, config)       # 404 for anybody else
    st = PL.state_of(die, config)
    if not prod_access.is_admin(user):
        st = {"state": st["state"], "explicit": st["explicit"]}                 # the factory: the state, not who / why
    return {"die": die, "config": config, **st}


class LifecycleChange(BaseModel):
    die: str
    config: str
    state: Literal["prepared", "in_production", "finished"]
    payment: str = Field(default="", max_length=PL.NOTE_MAX)
    source: str = Field(default="", max_length=PL.NOTE_MAX)


@router.put("")
def put_state(change: LifecycleChange, request: Request):
    user = _require_admin(request)
    if prod_access.load_production(change.die, change.config) is None:
        raise HTTPException(status_code=404, detail="Production not found.")
    by = str(user.get("email") or "")
    try:
        rec = PL.set_state(change.die, change.config, change.state, by=by, payment=change.payment, source=change.source)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    from motor_ai_sim import admin_audit as _AA
    _AA.record(by, "production.lifecycle", f"{change.die}/{change.config}",
               details={"state": change.state, "payment": rec.get("payment"), "source": rec.get("source")})
    return {"die": change.die, "config": change.config, **PL.state_of(change.die, change.config)}
