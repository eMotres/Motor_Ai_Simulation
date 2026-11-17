"""``/api/catalog/jobs``: running computations per motor and per server, for the admin's Motors catalog (``motor_ai_sim.job_status``).

* ``GET /api/catalog/jobs``           admins only: ``{"servers": [this, peer], "motors", "dies", "unknown", "unnamed"}``
* ``GET /api/peer/v1/jobs/status``   the other server asks for this one's view (HMAC-signed peer route, 404 otherwise)

Read-only; customers and the factory get 403 and see nothing of this anywhere.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from motor_ai_sim import job_status as JS
from motor_ai_sim.regions import peer as P
from motor_ai_sim.routes.regions import require_peer

router = APIRouter(tags=["catalog-jobs"])


def _require_admin(request: Request) -> None:
    from motor_ai_sim.routes.overview import _require_admin as overview_admin     # the Overview's own gate
    overview_admin(request)


@router.get("/api/catalog/jobs")
def catalog_jobs(request: Request) -> Dict[str, Any]:
    _require_admin(request)
    views = [JS.local_view()]
    peer = JS.peer_view()
    if peer is not None:
        views.append(peer)
    try:
        from motor_ai_sim.routes import family as F
        known = {str(e["die"]) for e in F._iter_die_entries()} | {str(e["name"]) for e in F._iter_die_entries()}
    except Exception:                                       # noqa: BLE001
        known = None
    return {"servers": [{k: v for k, v in view.items() if k != "jobs"} | {"jobs": len(view.get("jobs") or [])} for view in views],
            **JS.by_motor(views, known)}


@router.get(P.PREFIX + "/jobs/status")
def peer_jobs_status(_peer: str = Depends(require_peer)) -> Dict[str, Any]:
    return JS.local_view()
