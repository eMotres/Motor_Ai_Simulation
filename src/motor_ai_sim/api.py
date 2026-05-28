"""FastAPI server for motor geometry API.

Usage:
    uvicorn motor_ai_sim.api:app --reload --port 8000
    python -m motor_ai_sim.api
"""

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from motor_ai_sim.config import get_config, get_material_assignments, clear_config_cache
from motor_ai_sim.routes.geometry import router as geometry_router
from motor_ai_sim.routes.pipeline import router as pipeline_router
from motor_ai_sim.services.geometry_service import get_current_geometry, params_to_dict
from motor_ai_sim import materials as mat_lib

app = FastAPI(
    title="Motor Geometry API",
    description="REST API for electric motor geometry parameters",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(geometry_router)
app.include_router(pipeline_router)


@app.get("/")
def root():
    return {
        "name": "Motor Geometry API",
        "version": "0.1.0",
        "endpoints": [
            "/api/geometry",
            "/api/geometry/summary",
            "/api/materials",
            "/api/materials/library",
            "/api/materials/library/{category}",
            "/api/materials/library/{category}/{name}",
            "/api/config",
        ],
    }


@app.get("/api/health")
def health_check():
    return {"status": "healthy"}


_ASSIGNABLE_PARTS = {'stator_core', 'slot', 'rotor_core', 'magnet', 'shaft'}
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "motor_config.yaml"


class MaterialAssignment(BaseModel):
    part: str
    material: str


@app.get("/api/materials")
def get_materials():
    try:
        return get_material_assignments()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/materials")
def update_material(assignment: MaterialAssignment):
    """Assign a library material to a motor part, saved to motor_config.yaml."""
    if assignment.part not in _ASSIGNABLE_PARTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown part '{assignment.part}'. Valid: {sorted(_ASSIGNABLE_PARTS)}"
        )
    try:
        content = _CONFIG_PATH.read_text(encoding="utf-8")
        # Replace the specific key under the materials: block
        pattern = rf'^(\s+{re.escape(assignment.part)}:\s*).*$'
        new_content = re.sub(pattern, rf'\g<1>{assignment.material}', content, flags=re.MULTILINE)
        if new_content == content:
            raise ValueError(f"Key '{assignment.part}' not found in config")
        _CONFIG_PATH.write_text(new_content, encoding="utf-8")
        clear_config_cache()
        return {"status": "ok", "assignments": get_material_assignments(reload=True)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Materials library (EM simulation database) ────────────────────────────

@app.get("/api/materials/library")
def get_materials_library():
    """Return all available materials from the EM simulation library."""
    try:
        result: dict = {}

        # Steels
        result["steel"] = {}
        for name, m in mat_lib.all_steels().items():
            result["steel"][name] = {
                "description": m.description,
                "form": m.form,
                "sigma": m.sigma,
                "density": m.density,
                "stacking_factor": m.stacking_factor,
                "core_loss_model": m.core_loss_model,
                "core_loss_kh": m.core_loss_kh,
                "core_loss_kc": m.core_loss_kc,
                "core_loss_ke": m.core_loss_ke,
                "core_loss_curve_unit": m.core_loss_curve_unit,
                "bh_curve": m.bh_curve,
                "core_loss_curves": m.core_loss_curves,
            }

        # Magnets
        result["magnet"] = {}
        for name, m in mat_lib.all_magnets().items():
            result["magnet"][name] = {
                "description": m.description,
                "Br": m.Br,
                "Hc": m.Hc,
                "mu_rec": m.mu_rec,
                "sigma": m.sigma,
                "density": m.density,
                "energy_product_kj_m3": m.energy_product_kj_m3,
                "bh_curve": m.bh_curve,
            }

        # Conductors
        result["conductor"] = {}
        for name, m in mat_lib.all_conductors().items():
            result["conductor"][name] = {
                "description": m.description,
                "sigma": m.sigma,
                "resistivity": m.resistivity,
                "density": m.density,
                "thermal_conductivity": m.thermal_conductivity,
                "specific_heat": m.specific_heat,
                "thermal_alpha": m.thermal_alpha,
                "wire_width_mm": m.wire_width_mm,
                "wire_height_mm": m.wire_height_mm,
            }

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/materials/library/{category}")
def get_materials_library_category(category: str):
    """Return materials for a specific category: steel | magnet | conductor."""
    try:
        if category == "steel":
            names = mat_lib.list_materials("steel")["steel"]
        elif category == "magnet":
            names = mat_lib.list_materials("magnet")["magnet"]
        elif category == "conductor":
            names = mat_lib.list_materials("conductor")["conductor"]
        else:
            raise HTTPException(status_code=404, detail=f"Unknown category '{category}'")
        return {"category": category, "materials": names}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/materials/library/{category}/{name}")
def get_material_detail(category: str, name: str):
    """Return full detail for a single material."""
    try:
        m = mat_lib.get_material(category, name)  # type: ignore[arg-type]
        import dataclasses
        return dataclasses.asdict(m)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
def get_full_config():
    try:
        config = get_config()
        mesh_cfg = config.get("mesh", {})
        sim_cfg = config.get("simulation", {})
        return {
            "geometry": params_to_dict(get_current_geometry()),
            "materials": get_material_assignments(),
            "mesh": {
                "n_radial": mesh_cfg.get("n_radial", 10),
                "n_angular": mesh_cfg.get("n_angular", 64),
                "n_angular_slots": mesh_cfg.get("n_angular_slots", 8),
            },
            "simulation": {
                "max_current": sim_cfg.get("max_current", 10.0),
                "frequency": sim_cfg.get("frequency", 50.0),
                "rpm": sim_cfg.get("rpm", 2000),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main():
    import uvicorn
    uvicorn.run("motor_ai_sim.api:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
