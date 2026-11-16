"""``/api/designation``: the standard motor designation (``motor_ai_sim.motor_designation``).

* ``GET /motor/{die}/{cfg}``   the designation of a stored motor, computed NOW from its parameters, with every segment's
  value and source, the facts the decoder shows beside it, and the read-only name check.  Same visibility gate as the
  configuration itself (a hidden configuration answers like a missing one).  The name check (``checks``) is our
  housekeeping: only staff (admin / engineer) get it.  A customer or the factory never gets an INCOMPLETE designation
  (one with "?"): ``designation`` is null for them and no segment is sent; staff see it, marked incomplete.
  ``other_names``: the stored results of this motor that were computed for another designation (each result keeps the
  name it was computed for, #724), for staff and members alike, since they are the caller's own results.  Only beside a
  complete designation: against a "?" every stamp would read as different.

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
    ident = F._auth.caller_identity(authorization)
    staff = bool(ident.get("is_admin")) or str(ident.get("role") or "").lower() in {"admin", "engineer"}
    if not staff:
        out.pop("checks", None)
        if not out["complete"]:
            return {"die": die, "config": cfg, "designation": None, "complete": False}
    out["other_names"] = _other_names(die, cfg, out) if out["complete"] else []
    return {"die": die, "config": cfg, **out}


def _other_names(die: str, cfg: str, out: dict) -> list:
    """Results computed for another name; ``[]`` when the store cannot be read (the name itself still answers)."""
    try:
        from motor_ai_sim import duty_results
        return MD.result_marks(out.get("designation"), duty_results.index(die, shared=True).get(cfg) or {})
    except Exception:                                       # noqa: BLE001
        return []

