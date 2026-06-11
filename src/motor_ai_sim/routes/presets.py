"""Motor 'garage' — a library of ready-made motor designs.

Each preset bundles a full geometry parameter set plus the operating point
(current / rpm / load-angle gamma).  Applying a preset writes those values into
config/motor_config.yaml and flushes the geometry + simulation caches, so the
whole app (Geometry, Mesh, Simulation) switches to that motor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/presets", tags=["presets"])

_ROOT = Path(__file__).parent.parent.parent.parent
_PRESETS_PATH = _ROOT / "config" / "motor_presets.json"
_CONFIG_PATH = _ROOT / "config" / "motor_config.yaml"


def _load_presets() -> dict:
    if not _PRESETS_PATH.exists():
        return {}
    try:
        return json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_presets(d: dict) -> None:
    _PRESETS_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")


def _summary(p: dict) -> dict:
    return {
        "id": p.get("id"),
        "name": p.get("name", p.get("id")),
        "description": p.get("description", ""),
        "metrics": p.get("metrics", {}),
        "order": p.get("order", 999),
    }


class SavePresetRequest(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    metrics: Optional[dict] = None


@router.get("")
def list_presets():
    """List all saved motor presets (sorted by order)."""
    presets = _load_presets()
    items = [_summary(p) for p in presets.values()]
    items.sort(key=lambda x: (x["order"], x["name"]))
    return {"presets": items}


@router.get("/{preset_id}")
def get_preset(preset_id: str):
    p = _load_presets().get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")
    return p


@router.post("/{preset_id}/apply")
def apply_preset(preset_id: str):
    """Apply a preset: write its geometry + operating point into config.yaml and
    flush all caches so the app switches to that motor."""
    p = _load_presets().get(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")

    geometry = p.get("geometry", {}) or {}
    simulation = p.get("simulation", {}) or {}

    try:
        config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        geo_sec = config.setdefault("geometry", {})
        for k, v in geometry.items():
            geo_sec[k] = v
        sim_sec = config.setdefault("simulation", {})
        for k, v in simulation.items():
            sim_sec[k] = v
        # Synchronous machine: keep frequency LOCKED to rpm (f = rpm·pp/60).
        # Presets store rpm but not frequency; leaving a stale frequency from
        # the previous motor scaled the solver's loss derivatives wrong.
        try:
            _pp = int(round(float(geo_sec.get("num_seg", 0)))) \
                * int(round(float(geo_sec.get("num_poles_per_segment", 0)))) // 2
            if _pp > 0 and sim_sec.get("rpm"):
                sim_sec["frequency"] = round(float(sim_sec["rpm"]) * _pp / 60.0, 2)
        except Exception:
            pass
        _CONFIG_PATH.write_text(
            yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to write config: {e}")

    # Flush caches + reset in-memory geometry so every tab re-solves on the new motor.
    try:
        from motor_ai_sim.config import clear_config_cache
        clear_config_cache()
    except Exception:
        pass
    try:
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None
        gs.invalidate_mesh_cache()
    except Exception:
        pass
    try:
        from motor_ai_sim.routes.geometry import _mesh_cache, _mesh_extruded_cache
        _mesh_cache["hash"] = None
        _mesh_extruded_cache["hash"] = None
    except Exception:
        pass
    try:
        from motor_ai_sim.routes.simulation import clear_simulation_caches
        clear_simulation_caches()
    except Exception:
        pass
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryCache
        CadQueryCache().clear_all()
    except Exception:
        pass

    return {"status": "ok", "applied": preset_id,
            "geometry": geometry, "simulation": simulation}


@router.post("")
def save_current_as_preset(req: SavePresetRequest):
    """Snapshot the CURRENT config geometry + operating point as a new preset."""
    from motor_ai_sim.config import get_config
    cfg = get_config()
    geo = dict(cfg.get("geometry", {}))
    sim = cfg.get("simulation", {})
    # keep only plain numeric geometry keys (drop nested schema/groups)
    geometry = {k: v for k, v in geo.items() if isinstance(v, (int, float))}
    simulation = {k: sim.get(k) for k in ("max_current", "rpm", "phase_offset_deg")
                  if sim.get(k) is not None}

    presets = _load_presets()
    pid = req.id or req.name.lower().replace(" ", "_").replace("/", "_")
    order = 1 + max([p.get("order", 0) for p in presets.values()] or [0])
    presets[pid] = {
        "id": pid, "name": req.name, "description": req.description,
        "order": order, "metrics": req.metrics or {},
        "geometry": geometry, "simulation": simulation,
    }
    _save_presets(presets)
    return _summary(presets[pid])


@router.delete("/{preset_id}")
def delete_preset(preset_id: str):
    presets = _load_presets()
    if preset_id not in presets:
        raise HTTPException(status_code=404, detail=f"preset '{preset_id}' not found")
    del presets[preset_id]
    _save_presets(presets)
    return {"status": "ok", "deleted": preset_id}
