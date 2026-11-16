"""``/api/designation``: the standard motor designation (``motor_ai_sim.motor_designation``).

* ``GET /motor/{die}/{cfg}``   the designation of a stored motor, computed NOW from its parameters, with every segment's
  value and source, the facts the decoder shows beside it, and the read-only name check.  Same visibility gate as the
  configuration itself (a hidden configuration answers like a missing one).

The public help page "How to read a MOTRES motor name" decodes a typed name in the browser (``web/src/lib/motorDesignation.ts``,
the same grammar as :func:`motor_designation.parse`), so it needs no route and works without sign-in.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, Response

from motor_ai_sim import motor_designation as MD

router = APIRouter(prefix="/api/designation", tags=["designation"])


@router.get("/motor/{die}/{cfg}")
def motor_designation(die: str, cfg: str, response: Response, authorization: Optional[str] = Header(default=None)):
    from motor_ai_sim.routes import family as F
    die, cfg = F._check_name(die, "die"), F._check_name(cfg, "configuration")
    F._require_die_access(die, authorization, cfg)
    F._load_yaml(F._cfg_file(die, cfg), "configuration")          # 404 for a configuration that does not exist
    response.headers["Cache-Control"] = "no-store"                 # recomputed at every read: never a stale name
    out = MD.for_motor(die, cfg)
    return {"die": die, "config": cfg, **out}

