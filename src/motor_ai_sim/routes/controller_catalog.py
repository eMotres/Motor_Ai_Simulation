"""Complete-controller catalogue - ``GET /api/controllers/catalog`` (list) and ``/{id}`` (one entry, whole).

Read-only reference data (``config/controllers/catalog.yaml``, see :mod:`motor_ai_sim.inverter.controller_catalog`).
It solves nothing and is served by the API process (``/api/controllers`` is not the compute worker's
``/api/controller``).
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from motor_ai_sim.inverter import controller_catalog as _cat

router = APIRouter(prefix="/api/controllers", tags=["controllers"])


def _loaded() -> Dict[str, Any]:
    try:
        return _cat.load_catalog()
    except _cat.CatalogError as exc:
        raise HTTPException(status_code=500, detail=f"controller catalogue unreadable: {exc}")


@router.get("/catalog")
def get_catalog() -> Dict[str, Any]:
    doc = _loaded()
    return {"catalog_version": doc.get("catalog_version"), "updated": doc.get("updated"),
            "controllers": _cat.list_controllers()}


@router.get("/catalog/{controller_id}")
def get_catalog_entry(controller_id: str) -> Dict[str, Any]:
    _loaded()
    entry = _cat.get_controller(controller_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown controller")
    return {"summary": _cat.summary(entry), "entry": entry}


@router.get("/{controller_id}")
def get_controller(controller_id: str) -> Dict[str, Any]:
    """Same as ``/catalog/{id}``."""
    return get_catalog_entry(controller_id)
