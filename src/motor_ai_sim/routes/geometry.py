from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from motor_ai_sim.config import get_config, clear_config_cache, DEFAULT_CONFIG_PATH
from motor_ai_sim.geometry.motor_geometry import HAS_MODULUS
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
        params = update_current_geometry(**update.model_dump())

        # Persist changes to YAML so they survive server restarts
        config_path = Path(DEFAULT_CONFIG_PATH)
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        geometry_section = config.setdefault("geometry", {})
        for key, value in update.model_dump().items():
            if value is not None and key in geometry_section:
                geometry_section[key] = value
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        return params_to_dict(params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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

        parameters = [
            {
                "name": name,
                "label": meta.get("label", name.replace("_", " ").title()),
                "unit": meta.get("unit", ""),
                "type": meta.get("type", "float"),
                "min": meta.get("min", 0),
                "max": meta.get("max", 1000),
                "step": meta.get("step", 0.1),
                "group": meta.get("group", "other"),
                "description": meta.get("description", ""),
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


_mesh_cache: dict    = {"hash": None, "data": None}
_mesh2d_cache: dict = {"hash": None, "data": None}

@router.get("/mesh")
def get_geometry_mesh():
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json
        params = get_current_geometry()
        params_dict = params.to_dict()
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh_cache["hash"] == params_hash and _mesh_cache["data"] is not None:
            return _mesh_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        motor.build_all()
        data = motor.get_all_mesh_data()

        _mesh_cache["hash"] = params_hash
        _mesh_cache["data"] = data
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/mesh2d")
def get_geometry_mesh2d():
    """Return flat 2-D cross-section meshes for all motor components (shapely+earcut, no CadQuery)."""
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        import hashlib, json
        params = get_current_geometry()
        params_dict = params.to_dict()
        params_hash = hashlib.md5(json.dumps(params_dict, sort_keys=True).encode()).hexdigest()

        if _mesh2d_cache["hash"] == params_hash and _mesh2d_cache["data"] is not None:
            return _mesh2d_cache["data"]

        motor = CadQueryMotor()
        motor.set_parameters(params_dict)
        data = motor.get_2d_mesh_data()

        _mesh2d_cache["hash"] = params_hash
        _mesh2d_cache["data"] = data
        return data
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{e}\n{traceback.format_exc()}")


@router.get("/pointcloud")
def get_geometry_pointcloud(n_points: int = 20000):
    try:
        params = get_current_geometry(reload=True)

        if HAS_MODULUS:
            from motor_ai_sim.geometry.motor_geometry import MotorGeometry2D
            motor = MotorGeometry2D(params)
            geometries = motor.get_modulus_geometries()

            regions_to_sample = {
                'stator_core': 'steel',
                'rotor_core': 'steel',
                'coils': 'copper',
                'magnets': 'permanent_magnet',
                'shaft': 'steel',
                'air_gap': 'air',
            }
            pointcloud_data = {}
            for region_name, material_type in regions_to_sample.items():
                if region_name not in geometries:
                    continue
                try:
                    samples = geometries[region_name].sample_interior(n_points)
                    if hasattr(samples, 'numpy'):
                        samples = samples.numpy()
                    points = samples.T.tolist() if samples.shape[0] == 3 else samples.tolist()
                    if points and len(points[0]) == 2:
                        points = [[x, y, 0.0] for x, y in points]
                    pointcloud_data[region_name] = {
                        'points': points,
                        'material': material_type,
                        'count': len(points),
                    }
                except Exception as e:
                    pointcloud_data[region_name] = {
                        'points': [], 'material': material_type, 'count': 0, 'error': str(e)
                    }
        else:
            pointcloud_data = generate_synthetic_pointcloud(params, n_points)

        return {'n_points': n_points, 'has_modulus': HAS_MODULUS, 'regions': pointcloud_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
