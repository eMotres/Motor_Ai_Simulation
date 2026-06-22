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


def _save(cat: dict) -> None:
    _CATALOG_PATH.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")


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


@router.delete("/{motor_id}")
def delete_motor(motor_id: str, drop_preset: bool = True):
    """Remove a motor from the catalog (admin).  Drops the card and, by default,
    its underlying preset too.  No-op-safe: 404 only if the id isn't present."""
    cat = _load()
    motors = cat.get("motors", [])
    target = next((m for m in motors if m.get("id") == motor_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    cat["motors"] = [m for m in motors if m.get("id") != motor_id]
    # prune now-empty diameter buckets so the section header disappears too
    remaining_d = {m.get("diameter_mm") for m in cat["motors"]}
    cat["diameters_mm"] = [d for d in cat.get("diameters_mm", []) if d in remaining_d]
    _save(cat)

    preset_id = target.get("preset")
    dropped_preset = False
    if drop_preset and preset_id:
        try:
            from motor_ai_sim.routes.presets import _load_presets, _save_presets
            presets = _load_presets()
            if preset_id in presets:
                del presets[preset_id]
                _save_presets(presets)
                dropped_preset = True
        except Exception:
            pass
    return {"status": "ok", "deleted": motor_id, "dropped_preset": dropped_preset}
