"""GET /api/system/graph - how the simulations are chained (View only, stage 1).

Returns ``{version, view, title, nodes, edges, loops, notes, status_context}``; the schema is
in ``docs/SYSTEM_GRAPH.md``.  ``?view=modules`` (default) is every registered module with the
data links its manifests declare; ``?view=propeller_s1`` is the chain ``propeller_s1.py``
runs.  ``?die=&config=&duty=`` adds the last-run status per node from records that already
exist (the caller's jobs, the run history); a node without a record is ``unknown``.  The
route never starts a computation.

Engineer and admin only (``auth._GATED``: the Simulation menus' audience).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from motor_ai_sim import system_graph as _SG

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/graph")
def get_graph(view: str = Query("modules", description="modules | propeller_s1"),
              die: Optional[str] = Query(None), config: Optional[str] = Query(None),
              duty: Optional[str] = Query(None),
              authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if view not in _SG.VIEWS:
        raise HTTPException(status_code=400, detail={
            "error": f"unknown view {view!r}", "views": list(_SG.VIEWS)})
    # The status layer names a machine: a PRIVATE configuration the caller has no
    # grant for is reported like one that does not exist.
    from motor_ai_sim import motor_access as _ma
    visible = not (die and config and not _ma.may_see_config(
        _ma.catalog_access(authorization), die, config))
    try:
        return _SG.build(view, die=die, config=config, duty=duty, visible=visible)
    except LookupError as exc:                      # the code a node describes has moved: say so, loudly
        raise HTTPException(status_code=500, detail={"error": f"system graph out of date: {exc}"})
