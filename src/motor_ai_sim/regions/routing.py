"""Where an e-mail must be served, and the 421 ``wrong_region`` answer of the sign-in routes (stage 3).

``serving_region(email)`` is the region of the OTHER server when this server must not serve the account, else ``None``:

* a local record of a non-staff account whose home region is the peer AND whose guard is on (per account after its
  migration, or globally with ``MOTRES_REGION_GUARD``) -> its home region.  An account classified US but not migrated
  keeps being served here (DESIGN section 11);
* no local record, multi-region mode, and the remote directory places the e-mail on the peer -> the peer region.

The sign-in routes call it BEFORE any password check (DESIGN section 5); the answer is::

    421  {"detail": {"code": "wrong_region", "region": "us", "base_url": "https://us.aerostator.com",
                     "message": "...", "signin": true, "reason": "...", "handoff": "<ticket, when the caller proved who it is>"}}
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from motor_ai_sim.regions import config as RC
from motor_ai_sim.regions import store as RS


def serving_region(email: str) -> Optional[str]:
    from motor_ai_sim import users as U
    em = U._norm(email)
    if not em:
        return None
    local = RC.local_region()
    rec = U._load_soft().get(em)
    if isinstance(rec, dict):
        if RS.is_staff(rec):
            return None
        home = RS.effective_region(rec)
        if home != local and (RS.is_guarded(rec) or RC.guard_enabled()):
            return home
        return None
    if not RC.peer_configured():
        return None
    e = (RS.load_remote_directory().get("entries") or {}).get(em)
    r = RC.norm_region((e or {}).get("region")) if isinstance(e, dict) else None
    return r if r is not None and r != local else None


def detail(region: str, *, ticket: str = "", signin: bool = False, reason: str = "") -> Dict[str, Any]:
    from motor_ai_sim.regions import guard as G
    d = dict(G.body_for(region)["detail"])
    if ticket:
        d["handoff"] = ticket
    if signin:
        d["signin"] = True
    if reason:
        d["reason"] = reason
    return d


def headers() -> Dict[str, str]:
    return {"X-Motres-Region": RC.local_region()}


def refuse(region: str, **kw: Any) -> HTTPException:
    """The exception a route raises: 421 with the detail above and the ``X-Motres-Region`` header."""
    from motor_ai_sim.regions import guard as G
    return HTTPException(status_code=G.STATUS, detail=detail(region, **kw), headers=headers())
