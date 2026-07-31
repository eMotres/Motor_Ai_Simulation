"""Motor catalog — the public 'MOTORS' menu.

A curated table of ready-made motors, organised by stator diameter, plus the
subscription tiers.  Loading a catalog motor applies its underlying preset
(geometry + operating point) via the presets service.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException

from motor_ai_sim.json_store import mutate_json as _mutate_json, read_json as _read_json

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

_CATALOG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "motor_catalog.json"


def _load() -> dict:
    return _read_json(_CATALOG_PATH, {"tiers": [], "diameters_mm": [], "motors": []})


def _mutate(fn: Callable[[dict], None]) -> dict:
    """Apply `fn` to the catalog as it stands ON DISK, then write atomically.

    presets.py writes this same file (every motor save upserts a card), so the
    old load-mutate-save could drop a card that appeared while this request was
    running — thumbnail generation and passport extraction both hold the gap
    open for seconds to minutes.  `mutate_json` keys its lock by path, so both
    modules serialise on the same lock without knowing about each other.
    """
    return _mutate_json(_CATALOG_PATH, fn,
                        default={"tiers": [], "diameters_mm": [], "motors": []})


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
    def _m(d: dict) -> None:
        d["motors"] = [m for m in d.get("motors", []) if m.get("id") != motor_id]
        # prune now-empty diameter buckets so the section header disappears too
        remaining_d = {m.get("diameter_mm") for m in d["motors"]}
        d["diameters_mm"] = [x for x in d.get("diameters_mm", []) if x in remaining_d]
    _mutate(_m)

    preset_id = target.get("preset")
    dropped_preset = False
    if drop_preset and preset_id:
        try:
            from motor_ai_sim.routes.presets import _drop_preset, _load_presets
            if preset_id in _load_presets():
                _drop_preset(preset_id)
                dropped_preset = True
        except Exception:
            pass
    return {"status": "ok", "deleted": motor_id, "dropped_preset": dropped_preset}


# ── Passport: characterise a motor via FEM so the Configurator can scale it ──

@router.post("/{motor_id}/passport")
def generate_motor_passport(motor_id: str, coarse: bool = False):
    """Admin: FEM-characterise a catalog motor and store its passport.

    Applies the motor's preset (so its geometry + operating point become the
    active config), runs the passport extraction (3 base solves + an rpm loss
    sweep), and saves the result on the catalog entry.  FEM-heavy (minutes);
    `coarse=true` uses fewer steps/rpms for a quick smoke test.

    Side effect: switches the active motor to the one being characterised.
    """
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    preset_id = motor.get("preset")
    if not preset_id:
        raise HTTPException(status_code=400, detail=f"motor '{motor_id}' has no preset to characterise")
    try:
        from motor_ai_sim.routes.presets import apply_preset
        apply_preset(preset_id)
        from motor_ai_sim.passport import generate_passport
        kw = dict(base_steps=6, sweep_steps=6, rpms=[2000.0, 4000.0, 6000.0]) if coarse else {}
        result = generate_passport(**kw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"passport generation failed: {e}")

    # Minutes have passed inside generate_passport.  Attach the passport to the
    # card as it exists NOW — writing back the whole document we read before the
    # run would undo every catalog edit made while it was running.
    def _m(d: dict) -> None:
        for m in d.get("motors", []):
            if m.get("id") == motor_id:
                m["passport"] = result
                return
        d.setdefault("motors", []).append({**motor, "passport": result})
    _mutate(_m)
    return {"status": "ok", "motor": motor_id, "coarse": coarse, "passport": result}


@router.get("/{motor_id}/passport")
def get_motor_passport(motor_id: str):
    """Return a motor's stored passport (404 if it hasn't been generated yet)."""
    cat = _load()
    motor = next((m for m in cat.get("motors", []) if m.get("id") == motor_id), None)
    if not motor:
        raise HTTPException(status_code=404, detail=f"motor '{motor_id}' not found")
    passport = motor.get("passport")
    if not passport:
        raise HTTPException(status_code=404,
                            detail=f"motor '{motor_id}' has no passport — generate it first")
    return passport
