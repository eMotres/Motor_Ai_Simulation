"""Read-only view of a motor's stored results (Motors tab / Configure; owner 2026-10-26).

``GET /api/motor-view/{die}/{cfg}``                    what is stored per duty + the full / points-limited decision
``GET /api/motor-view/{die}/{cfg}/{duty}/field/{kind}`` one stored field map, shaped like the tab payload it replaces

The same data a project motor shows (``/api/projects/{id}/motors/view``), reached through the ordinary motor gate
(grants / visibility - ``family._require_die_access`` and the app-wide configuration path gate), so loading a motor
from the Motors tab and from a project gives ONE view.  Nothing is solved and nothing is written here.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import APIRouter, Header, HTTPException, Response

router = APIRouter(prefix="/api/motor-view", tags=["motor-view"])


def _motor(die: str, cfg: str, authorization):
    from motor_ai_sim.routes import family
    die, cfg = family._check_name(die, "die"), family._check_name(cfg, "configuration")
    family._require_die_access(die, authorization, cfg)
    path = Path(str(family._cfg_file(die, cfg)))
    if not path.is_file():
        raise HTTPException(404, detail=f"configuration not found: {cfg}.yaml")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return die, cfg, path, doc if isinstance(doc, dict) else {}


@router.get("/{die}/{cfg}")
def motor_view(die: str, cfg: str, response: Response, authorization: str = Header(default=None)):
    from motor_ai_sim.development import motors
    response.headers["Cache-Control"] = "no-store"
    die, cfg, path, doc = _motor(die, cfg, authorization)
    return motors.motor_view_for(die, cfg, path, doc, authorization)


@router.get("/{die}/{cfg}/{duty}/field/{kind}")
def motor_field(die: str, cfg: str, duty: str, kind: str, response: Response,
                authorization: str = Header(default=None)):
    from motor_ai_sim.development import motor_view
    response.headers["Cache-Control"] = "no-store"
    die, cfg, _path, doc = _motor(die, cfg, authorization)
    # an unknown kind name is "not computed" (404), never a 400: nothing is stored under it
    if duty not in {str(d.get("name")) for d in doc.get("duties", []) if isinstance(d, dict)}:
        raise HTTPException(404, detail="Duty not found")
    payload = motor_view.field_payload(die, cfg, duty, kind)
    if payload is None:
        raise HTTPException(404, detail="Not computed")
    return payload
