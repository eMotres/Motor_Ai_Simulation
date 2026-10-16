"""Wire stock routes — /api/wires.

One GET: the enamelled flat copper wire physically on the shelf
(``config/wire_stock.yaml``), for the winding editors to later restrict wire
sizes to and, meanwhile, for the Materials tab's reference table.

SOLVER ISOLATION: nothing here solves anything or writes anything — it reads a
YAML table through ``motor_ai_sim.wire_stock`` (same loader shape as
``routes/bearings.py`` reads ``config/bearings_library.yaml``) and hands it
back.  Ungated, same as ``GET /api/materials``: it is a reference table, not a
compute endpoint.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wires", tags=["wires"])


@router.get("/stock")
def get_stock() -> Dict[str, Any]:
    """The whole wire-stock table plus the sizes it makes available.

    ``wires`` — every row, sorted by thickness then width then code.
    ``available_sizes`` — sorted unique ``(thickness_mm, width_mm)`` pairs with
    the summed stock_kg across every code stocked at that size.
    """
    from motor_ai_sim import wire_stock as ws
    try:
        wires = ws.list_wires()
        sizes = ws.available_sizes()
        meta = ws.meta()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except ws.WireStockError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        **meta,
        "wires": [w.to_dict() for w in wires],
        "available_sizes": sizes,
    }
