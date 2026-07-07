import os
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from motor_ai_sim.config import get_config, clear_config_cache, DEFAULT_CONFIG_PATH
from motor_ai_sim.services.geometry_service import (
    generate_synthetic_pointcloud,
    get_current_geometry,
    params_to_dict,
    reset_geometry,
    update_current_geometry,
)

router = APIRouter(prefix="/api/geometry")


class GeometryUpdateModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AddParameterRequest(BaseModel):
    name: str
    label: str
    unit: str = ""
    type: str = "float"
    group: str = "custom"
    min: float = 0.0
    max: float = 1000.0
    step: float = 0.1
    default_value: float = 0.0
    description: str = ""


@router.get("")
def get_geometry():
    try:
        return params_to_dict(get_current_geometry(reload=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("")
def update_geometry(update: GeometryUpdateModel):
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryCache
        CadQueryCache().clear_all()
        _mesh_cache["hash"] = None
        # Geometry changed → flush every simulation-side cache (2-D polys,
        # build2d / sliding-band meshes, field, transient, frame) so the
        # Mesh tab and Simulation re-solve on the NEW cross-section.
        try:
            from motor_ai_sim.routes.simulation import clear_simulation_caches
            clear_simulation_caches()
        except Exception:
            pass
        params = update_current_geometry(**update.model_dump())

        # Persist changes to YAML so they survive server restarts
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        geometry_section = config.setdefault("geometry", {})
        for key, value in update.model_dump().items():
            if value is not None and key in geometry_section:
                geometry_section[key] = value
        # Recompute the DERIVED raw fields that shadow the formulas.  The YAML
        # stores both num_seg/num_*_per_segment AND flattened num_poles/
        # num_slots/angle_* — the solver path reads the flattened ones, so a
        # segment-count edit that skips this left e.g. num_poles at the OLD
        # value: a 20-pole rotor driven at the 28-pole frequency (T garbage)
        # with the 28-pole winding layout.
        try:
            _ns = float(geometry_section.get("num_seg", 0) or 0)
            _pps = float(geometry_section.get("num_poles_per_segment", 0) or 0)
            _sps = float(geometry_section.get("num_slots_per_segment", 0) or 0)
            if _ns > 0 and _pps > 0 and "num_poles" in geometry_section:
                geometry_section["num_poles"] = int(round(_ns * _pps))
                if "angle_pole" in geometry_section:
                    geometry_section["angle_pole"] = 360.0 / (_ns * _pps)
            if _ns > 0 and _sps > 0 and "num_slots" in geometry_section:
                geometry_section["num_slots"] = int(round(_ns * _sps))
                if "angle_slot" in geometry_section:
                    geometry_section["angle_slot"] = 360.0 / (_ns * _sps)
        except Exception:
            pass
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # Flush the config cache so get_config()-based consumers (the analytical
        # torque_sweep, params_from_config, winding calc, …) read the NEW
        # geometry. Without this they keep returning the stale cross-section,
        # which silently invalidates geometry optimization.
        try:
            from motor_ai_sim.config import clear_config_cache
            clear_config_cache()
        except Exception:
            pass

        # ── Enforce geometry feasibility constraints ─────────────────────────
        # Clamp any knob that now violates a physical constraint (e.g. a winding
        # that no longer fits the slot) and persist the clamped value, so the
        # simulation geometry is ALWAYS valid.  Report what was clamped.
        applied: list = []
        try:
            from motor_ai_sim.config import get_config, clear_config_cache
            from motor_ai_sim.geometry_constraints import clamp as _clamp_geo
            full_geo = dict(get_config().get("geometry", {}))
            _clamped, applied = _clamp_geo(full_geo)
            if applied:
                fixes = {a["target"]: _clamped[a["target"]] for a in applied}
                params = update_current_geometry(**fixes)
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                gs = config.setdefault("geometry", {})
                for k, v in fixes.items():
                    gs[k] = v
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
                clear_config_cache()
        except Exception:
            pass

        result = params_to_dict(params)
        if applied:
            result["constraints_applied"] = [
                {"target": a["target"], "clamped_to": a["clamped_to"],
                 "bound": a["bound"], "label": a["label"]} for a in applied]
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/constraints")
def get_geometry_constraints():
    """Dynamic feasibility bounds (e.g. max wire_height) for the CURRENT geometry
    so the UI can show the effective limit next to a field."""
    try:
        from motor_ai_sim.config import get_config
        from motor_ai_sim.geometry_constraints import bounds, evaluate
        geo = dict(get_config().get("geometry", {}))
        return {"bounds": bounds(geo), "checks": evaluate(geo)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parameter")
def add_parameter(req: AddParameterRequest):
    """Add a new parameter to motor_config.yaml and reload the schema."""
    try:
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        name = req.name.strip().lower().replace(" ", "_").replace("-", "_")

        # Add to geometry section with default value
        value = int(req.default_value) if req.type == "int" else float(req.default_value)
        config.setdefault("geometry", {})[name] = value

        # Add to geometry_schema section
        config.setdefault("geometry_schema", {})[name] = {
            "label": req.label,
            "unit": req.unit,
            "type": req.type,
            "min": int(req.min) if req.type == "int" else float(req.min),
            "max": int(req.max) if req.type == "int" else float(req.max),
            "step": int(req.step) if req.type == "int" else float(req.step),
            "group": req.group,
            "description": req.description,
        }

        # If group is new, add it to parameter_groups
        groups = config.setdefault("parameter_groups", {})
        if req.group not in groups:
            max_order = max((g.get("order", 0) for g in groups.values()), default=0)
            groups[req.group] = {
                "label": req.group.replace("_", " ").title(),
                "order": max_order + 1,
            }

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # Reload everything
        clear_config_cache()
        from motor_ai_sim.services.geometry_service import _current_geometry
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None

        return {"success": True, "name": name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/parameter/{name}")
def delete_parameter(name: str):
    """Remove a parameter from motor_config.yaml."""
    try:
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        removed = False
        if name in config.get("geometry", {}):
            del config["geometry"][name]
            removed = True
        if name in config.get("geometry_schema", {}):
            del config["geometry_schema"][name]
            removed = True

        if not removed:
            raise HTTPException(status_code=404, detail=f"Parameter '{name}' not found")

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        clear_config_cache()
        import motor_ai_sim.services.geometry_service as gs
        gs._current_geometry = None

        return {"success": True, "name": name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset")
def reset_geometry_endpoint():
    try:
        return params_to_dict(reset_geometry())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
def get_geometry_summary():
    params = get_current_geometry()
    return {
        "stator_outer_radius": params.stator_outer_radius,
        "stator_inner_radius": params.stator_inner_radius,
        "rotor_outer_radius": params.rotor_outer_radius,
        "rotor_inner_radius": params.rotor_inner_radius,
        "air_gap": params.air_gap,
        "num_slots": params.num_slots,
        "num_poles": params.num_poles,
        "shaft_radius": params.shaft_radius,
    }


@router.get("/schema")
def get_geometry_schema():
    try:
        config = get_config(reload=True)
        schema = config.get("geometry_schema", {})
        groups = config.get("parameter_groups", {})
        # Only parameters in the whitelist may be used as Sweep/Optimize
        # variables.  Empty/missing list → all parameters allowed (back-compat).
        whitelist = config.get("sweep_whitelist", None)
        allow_all = not whitelist
        whitelist_set = set(whitelist or [])
        # GEO_UNBOUNDED=1 lifts every parameter's min/max cap (debug/exploration
        # across motor scales 40 mm ↔ 450 mm) so the field clamp never blocks a
        # value.  The yaml bounds are preserved; this only overrides them at serve
        # time while the flag is on — unset the env var to restore the caps.
        _unbounded = os.environ.get("GEO_UNBOUNDED", "0") == "1"

        parameters = [
            {
                "name": name,
                "label": meta.get("label", name.replace("_", " ").title()),
                "unit": meta.get("unit", ""),
                "type": meta.get("type", "float"),
                "min": 0 if _unbounded else meta.get("min", 0),
                "max": 1_000_000 if _unbounded else meta.get("max", 1000),
                "step": meta.get("step", 0.1),
                "group": meta.get("group", "other"),
                "description": meta.get("description", ""),
                "optimizable": bool(allow_all or name in whitelist_set),
                "hidden": bool(meta.get("hidden", False)),
            }
            for name, meta in schema.items()
        ]

        group_list = sorted(
            [
                {"id": gid, "label": gmeta.get("label", gid.title()), "order": gmeta.get("order", 99)}
                for gid, gmeta in groups.items()
            ],
            key=lambda g: g["order"],
        )

        return {"parameters": parameters, "groups": group_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_mesh_cache: dict           = {"hash": None, "data": None, "build_time_s": None}
_mesh2d_cache: dict         = {"hash": None, "data": None, "build_time_s": None}
_mesh_extruded_cache: dict  = {"hash": None, "data": None, "build_time_s": None}

def _resolve_geo_dict(geo: Optional[str]) -> dict:
    """Geometry for a mesh request: the global config, optionally overlaid with a
    per-request ``geo`` override (a JSON dict of geometry params).

    Step 1 of the multi-user migration (docs/MULTI_USER_PLAN.md): a signed-in
    client can compute the mesh for ITS OWN design by passing ``geo``, without
    mutating the shared global config.  Absent or malformed ``geo`` falls back to
    the global config, so existing callers are unaffected.
    """
    import json
    params_dict = get_current_geometry().to_dict()
    if geo:
        try:
            override = json.loads(geo)
            if isinstance(override, dict) and override:
                params_dict = {**params_dict, **override}
        except Exception:
            pass  # malformed → use the global config (back-compat)
    return params_dict


@router.get("/mesh")
def get_geometry_mesh(geo: Optional[str] = None):
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh_cache["hash"] == params_hash and _mesh_cache["data"] is not None:
            return _mesh_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        t0 = time.perf_counter()
        motor.build_all()
        data = motor.get_all_mesh_data()

        # rotor_fill_r: the 3D SOLID rotor cannot take a BRep fillet at the full
        # radius near the thin bridges (it falls back to a smaller r).  The 2D
        # physics geometry IS filleted at the requested radius (per-edge clamp),
        # and get_extruded_mesh_data() extrudes that filleted 2D rotor to 3D for
        # free — no OCC fillet.  Swap the viewer's rotor_core to that mesh so the
        # 3D view shows the SAME rounding as the physics.  The extruded mesh is
        # z∈[-w,0]; the solids are z∈[0,w], so shift z by +w to align.  Guarded:
        # any failure keeps the (smaller-radius but smooth) solid rotor.
        try:
            _rfr = float(params_dict.get('rotor_fill_r', 0.0) or 0.0)
        except Exception:
            _rfr = 0.0
        if _rfr > 1e-4:
            try:
                _w = float(params_dict.get('motor_length') or 45.0)
                _rc = motor.get_extruded_mesh_data().get('rotor_core')
                if _rc and _rc.get('vertices'):
                    _v = _rc['vertices']
                    if _v and isinstance(_v[0], (list, tuple)):
                        _nv = [[float(x), float(y), float(z) + _w] for (x, y, z) in _v]
                    else:                       # flat [x,y,z,x,y,z,...]
                        _nv = [float(c) for c in _v]
                        for _i in range(2, len(_nv), 3):
                            _nv[_i] += _w
                    _rc2 = dict(_rc); _rc2['vertices'] = _nv
                    data['rotor_core'] = _rc2
            except Exception as _e:
                print(f"rotor_core extruded-fillet override skipped: {_e}")

        build_time = time.perf_counter() - t0

        _mesh_cache["hash"] = params_hash
        _mesh_cache["data"] = data
        _mesh_cache["build_time_s"] = round(build_time, 3)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mesh2d")
def get_geometry_mesh2d(geo: Optional[str] = None):
    """Return flat 2-D cross-section meshes for all motor components (shapely+earcut, no CadQuery)."""
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh2d_cache["hash"] == params_hash and _mesh2d_cache["data"] is not None:
            return _mesh2d_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        t0 = time.perf_counter()
        data = motor.get_2d_mesh_data()
        build_time = time.perf_counter() - t0

        _mesh2d_cache["hash"] = params_hash
        _mesh2d_cache["data"] = data
        _mesh2d_cache["build_time_s"] = round(build_time, 3)
        return data
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@router.get("/mesh_extruded")
def get_geometry_mesh_extruded(depth: Optional[float] = None, geo: Optional[str] = None):
    """
    Return 3-D meshes built by extruding 2-D Shapely cross-sections (no CadQuery).

    Query param ``depth`` overrides the motor_length config parameter.
    Response includes ``_timing`` metadata with build times for both this
    endpoint and the cached CadQuery 3D build (if available).
    """
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json, time
        params_dict = _resolve_geo_dict(geo)
        cache_key = hashlib.md5(
            json.dumps({**params_dict, "_depth": depth}, sort_keys=True).encode()
        ).hexdigest()

        if _mesh_extruded_cache["hash"] == cache_key and _mesh_extruded_cache["data"] is not None:
            return _mesh_extruded_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)

        t0 = time.perf_counter()
        data = motor.get_extruded_mesh_data(depth=depth)
        build_time = round(time.perf_counter() - t0, 3)

        # attach timing metadata (not a mesh component — prefixed with _)
        data["_timing"] = {
            "extruded_s":  build_time,
            "cadquery_3d_s": _mesh_cache.get("build_time_s"),   # None if never built
            "mesh2d_s":    _mesh2d_cache.get("build_time_s"),
        }

        _mesh_extruded_cache["hash"] = cache_key
        _mesh_extruded_cache["data"] = data
        _mesh_extruded_cache["build_time_s"] = build_time
        return data
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@router.get("/pointcloud")
def get_geometry_pointcloud(n_points: int = 20000):
    try:
        params = get_current_geometry(reload=True)

        pointcloud_data = generate_synthetic_pointcloud(params, n_points)

        return {'n_points': n_points, 'has_modulus': False, 'regions': pointcloud_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
