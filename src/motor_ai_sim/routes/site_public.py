"""The motors emotres.com shows (``motor_ai_sim.site_publication``).

PUBLIC, read-only, no sign-in, cached (answer shape: ``docs/schemas/site_motors.v1.json``, ``docs/SITE_PUBLIC_API.md``):

* ``GET /api/public/site/motors``          the flagged motors
* ``GET /api/public/site/motors/{slug}``   one of them (404 for anything else, flagged or not)

ADMIN (``auth.require_admin``):

* ``GET /api/admin/site-publication``                    every site record (the by-die catalog reads the flags there)
* ``GET /api/admin/site-publication/{die}/{config}``     the record, the saved points to pick from and "what the site will show"
* ``PUT /api/admin/site-publication/{die}/{config}``     set the flag and the site-only fields (audited)
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from motor_ai_sim import auth
from motor_ai_sim import site_publication as SP

public_router = APIRouter(prefix="/api/public/site", tags=["site-public"])
admin_router = APIRouter(prefix="/api/admin/site-publication", tags=["site-publication"])

_DEFAULT_ORIGINS = "https://emotres.com,https://www.emotres.com"


def _origins() -> List[str]:
    return [o.strip() for o in (os.environ.get("SITE_PUBLIC_ORIGINS") or _DEFAULT_ORIGINS).split(",") if o.strip()]


def _answer(request: Request, response: Response, body: Dict[str, Any]) -> Dict[str, Any]:
    origin = request.headers.get("origin", "")
    if origin in _origins():
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Cache-Control"] = "public, max-age=300"
    payload = {k: v for k, v in body.items() if k != "generated_at"}
    response.headers["ETag"] = '"' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24] + '"'
    return body


@public_router.get("/motors")
def list_motors(request: Request, response: Response):
    return _answer(request, response, SP.envelope(SP.published()))


@public_router.get("/motors/{slug}")
def one_motor(slug: str, request: Request, response: Response):
    for m in SP.published():
        if m["slug"] == slug:
            return _answer(request, response, SP.envelope([m]) | {"motor": m})
    raise HTTPException(status_code=404, detail="No such motor.")


def _shared_motors() -> List[Dict[str, str]]:
    """Every configuration of the SHARED catalog (the only motors the site can show)."""
    from pathlib import Path
    from motor_ai_sim.workspace import shared_root
    root = Path(str(shared_root())) / "dies"
    out = []
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "die.yaml").is_file() and not p.name.startswith(".")):
            for f in sorted(d.glob("*.yaml")):
                if f.name != "die.yaml" and not f.name.startswith("."):
                    out.append({"die": d.name, "config": f.stem})
    return out


@admin_router.get("")
def list_records(admin_user: dict = Depends(auth.require_admin)):
    motors = SP.load()["motors"]
    out = {}
    for k in motors:
        die, _s, cfg = k[len("motor:"):].partition("/")
        r = SP.record_of(die, cfg, motors)
        out[f"{die}/{cfg}"] = {"show": r["show"], "availability": r["availability"], "slug": r["slug"]}
    return {"schema_version": SP.SCHEMA_VERSION, "motors": out}


def _require_shared(die: str, config: str) -> None:
    if {"die": die, "config": config} not in _shared_motors():
        raise HTTPException(status_code=404, detail="Not a motor of the shared catalog.")


def _view(die: str, config: str) -> Dict[str, Any]:
    rec = SP.record_of(die, config)
    motor, problems = SP.build_motor(die, config, rec)
    if rec["show"] and motor is None and not problems:
        problems = ["not published"]
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.duty_kind import is_test_duty
    saved = []
    try:
        for name, kinds in ((dr.index(die, shared=True).get(config)) or {}).items():
            saved.append(str(name))
    except Exception:                                       # noqa: BLE001
        saved = []
    return {"die": die, "config": config, "record": rec, "problems": problems,
            "preview": motor if rec["show"] else None, "preview_if_shown": motor, "saved_points": sorted(saved),
            "availability_values": list(SP.AVAILABILITY), "schema_version": SP.SCHEMA_VERSION}


@admin_router.get("/{die}/{config}")
def get_record(die: str, config: str, admin_user: dict = Depends(auth.require_admin)):
    _require_shared(die, config)
    return _view(die, config)


class SiteChange(BaseModel):
    show: Optional[bool] = None
    availability: Optional[str] = None
    lead_time_text: Optional[str] = Field(default=None, max_length=400)
    slug: Optional[str] = Field(default=None, max_length=120)
    price_text: Optional[str] = Field(default=None, max_length=400)
    image_url: Optional[str] = Field(default=None, max_length=500)
    points: Optional[List[str]] = None
    rated_point: Optional[str] = Field(default=None, max_length=120)


@admin_router.put("/{die}/{config}")
def put_record(die: str, config: str, change: SiteChange, admin_user: dict = Depends(auth.require_admin)):
    _require_shared(die, config)
    fields = change.model_dump(exclude_unset=True)
    by = str(admin_user.get("email") or "")
    try:
        SP.set_record(die, config, fields, by=by)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    from motor_ai_sim import admin_audit as _AA
    _AA.record(by, "site.publication", f"{die}/{config}", details={k: v for k, v in fields.items()})
    return _view(die, config)
