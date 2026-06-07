"""Motor catalog — the public 'MOTORS' menu.

A curated table of ready-made motors, organised by stator diameter, plus the
subscription tiers.  Loading a catalog motor applies its underlying preset
(geometry + operating point) via the presets service.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "motor_catalog.json"


def _load() -> dict:
    if not _CATALOG_PATH.exists():
        return {"tiers": [], "diameters_mm": [], "motors": []}
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


@router.get("")
def get_catalog():
    """Return the full catalog: tiers, the diameter buckets, and every motor."""
    cat = _load()
    # group motor ids by diameter for convenience
    by_d: dict = {d: [] for d in cat.get("diameters_mm", [])}
    for m in cat.get("motors", []):
        by_d.setdefault(m.get("diameter_mm"), []).append(m["id"])
    cat["by_diameter"] = by_d
    return cat


@router.post("/{motor_id}/load")
def load_motor(motor_id: str):
    """Load a catalog motor into the app by applying its underlying preset."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    preset_id = motor.get("preset")
    if not preset_id:
        raise HTTPException(status_code=400, detail=f"motor '{motor_id}' has no preset to load")
    from motor_ai_sim.routes.presets import apply_preset
    result = apply_preset(preset_id)
    result["motor"] = motor_id
    return result
