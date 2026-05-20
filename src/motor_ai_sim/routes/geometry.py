from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from motor_ai_sim.config import get_config
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
    stator_diameter: Optional[float] = None
    slot_height: Optional[float] = None
    core_thickness: Optional[float] = None
    num_seg: Optional[int] = None
    num_slots_per_segment: Optional[int] = None
    num_poles_per_segment: Optional[int] = None
    stator_width: Optional[float] = None
    air_gap: Optional[float] = None
    tooth_width: Optional[float] = None
    insulation_thickness: Optional[float] = None
    wire_width: Optional[float] = None
    wire_height: Optional[float] = None
    wire_spacing_x: Optional[float] = None
    wire_spacing_y: Optional[float] = None
    num_wires_per_slot: Optional[int] = None
    slot_hs: Optional[float] = None
    magnet_height: Optional[float] = None
    rotor_house_height: Optional[float] = None
    shaft_height: Optional[float] = None
    magnet_fill_down: Optional[float] = None
    magnet_fill_up: Optional[float] = None
    magnet_fill_radius: Optional[float] = None
    magnet_up_gap: Optional[float] = None
    magnet_down_height: Optional[float] = None


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
        params = update_current_geometry(**update.model_dump())
        return params_to_dict(params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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


@router.get("/mesh")
def get_geometry_mesh():
    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        params = get_current_geometry()
        motor = CadQueryMotor()
        motor.set_parameters(params.to_dict())
        motor.build_all()
        return motor.get_all_mesh_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
