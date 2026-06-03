"""FastAPI server for motor geometry API.

Usage:
    uvicorn motor_ai_sim.api:app --reload --port 8000
    python -m motor_ai_sim.api
"""

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from pydantic import BaseModel

from motor_ai_sim.config import get_config, get_material_assignments, clear_config_cache
from motor_ai_sim.routes.geometry import router as geometry_router
from motor_ai_sim.routes.pipeline import router as pipeline_router
from motor_ai_sim.routes.simulation import router as simulation_router
from motor_ai_sim.routes.optimization import router as optimization_router
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
app.include_router(simulation_router)
app.include_router(optimization_router)


@app.on_event("startup")
def _warm_fem_pool_on_startup() -> None:
    """Spin up the persistent FEM worker pool in the background at server
    start so its ~5 s-per-worker cold-import (gmsh + scikit-fem) is paid
    while the server boots, not on the user's first 'Run Simulation'."""
    import threading

    def _bg():
        try:
            from motor_ai_sim.routes.simulation import warm_fem_pool
            warm_fem_pool()
        except Exception:
            pass

    threading.Thread(target=_bg, daemon=True).start()


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
            "/api/simulation/status",
            "/api/simulation/run",
            "/api/simulation/result/{job_id}",
            "/api/simulation/config",
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


def _set_material_in_yaml(content: str, part: str, material: str):
    """Replace `part: <value>` only inside the `materials:` top-level block.
    Returns (new_content, replaced_bool).
    """
    lines = content.splitlines(keepends=True)
    in_materials = False
    replaced = False
    result = []
    for line in lines:
        if re.match(r'^materials\s*:', line):
            in_materials = True
        elif in_materials and re.match(r'^\S', line):
            in_materials = False  # exited the block

        if in_materials and not replaced:
            m = re.match(rf'^(\s+{re.escape(part)}\s*:\s*)(.*)$', line)
            if m:
                line = m.group(1) + material + '\n'
                replaced = True

        result.append(line)
    return ''.join(result), replaced


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
        new_content, replaced = _set_material_in_yaml(content, assignment.part, assignment.material)
        if not replaced:
            raise ValueError(f"Key '{assignment.part}' not found under materials: in config")
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
        # Geometry-derived end-winding factor k_end = (π·tooth_w/2 + L)/L,
        # recomputed from the CURRENT geometry so the UI cell stays in sync.
        try:
            from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
            from motor_ai_sim.simulation.fem_solver_2d import end_winding_factor_geom as _ewf
            _kend = round(float(_ewf(_pfc(), config.get("geometry", {}))), 3)
        except Exception:
            _kend = 0.0
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
            "end_winding_factor": _kend,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Winding connection ────────────────────────────────────────────────────────

_VALID_CONNECTIONS = {"4S", "2P2S", "4P"}

class WindingConfigPatch(BaseModel):
    connection:       Optional[str] = None  # "4S" | "2P2S" | "4P"
    n_coils_per_phase: Optional[int] = None


@app.get("/api/winding/config")
def get_winding_config():
    """Return current winding connection config."""
    cfg = get_config()
    w = cfg.get("winding", {})
    n_parallel = w.get("n_parallel", 1)
    n_series   = w.get("n_series", 4)
    n_coils    = w.get("n_coils_per_phase", 4)
    # Derived: I_coil = I_phase / n_parallel
    sim        = cfg.get("simulation", {})
    I_phase    = sim.get("max_current", 85.0)
    geo        = cfg.get("geometry", {})
    n_wires    = geo.get("num_wires_per_slot", 14)
    I_coil     = I_phase / n_parallel
    amp_turns  = n_wires * I_coil
    return {
        "connection":         w.get("connection", "2P2S"),
        "n_coils_per_phase":  n_coils,
        "n_parallel":         n_parallel,
        "n_series":           n_series,
        "I_phase_Arms":       I_phase,
        "I_coil_Arms":        round(I_coil, 2),
        "amp_turns_per_slot": round(amp_turns, 1),
    }


def _parse_connection(conn: str):
    """Parse '2P2S' → (n_parallel=2, n_series=2)."""
    import re as _re
    m = _re.match(r'^(\d+)P(\d+)S$', conn)
    if m:
        return int(m.group(1)), int(m.group(2))
    if conn == "4S":
        return 1, 4
    if conn == "4P":
        return 4, 1
    raise ValueError(f"Unknown connection '{conn}'")


@app.patch("/api/winding/config")
def update_winding_config(patch: WindingConfigPatch):
    """Update winding connection in motor_config.yaml."""
    updates: dict = {}
    if patch.connection is not None:
        if patch.connection not in _VALID_CONNECTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid connection '{patch.connection}'. Valid: {sorted(_VALID_CONNECTIONS)}"
            )
        n_par, n_ser = _parse_connection(patch.connection)
        updates["connection"] = f'"{patch.connection}"'
        updates["n_parallel"]  = str(n_par)
        updates["n_series"]    = str(n_ser)
    if patch.n_coils_per_phase is not None:
        updates["n_coils_per_phase"] = str(patch.n_coils_per_phase)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    content = _CONFIG_PATH.read_text(encoding="utf-8")
    lines   = content.splitlines(keepends=True)
    in_winding = False
    result     = []
    replaced   = set()

    for line in lines:
        if re.match(r'^winding\s*:', line):
            in_winding = True
        elif in_winding and re.match(r'^\S', line):
            in_winding = False

        if in_winding:
            for key, val in updates.items():
                m = re.match(rf'^(\s+{re.escape(key)}\s*:\s*)(.*)$', line)
                if m:
                    line = m.group(1) + val + '\n'
                    replaced.add(key)
                    break

        result.append(line)

    missing = set(updates) - replaced
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Keys not found in winding: block: {missing}"
        )

    _CONFIG_PATH.write_text(''.join(result), encoding="utf-8")
    clear_config_cache()
    return {"status": "ok", "updated": {k: v.strip('"') for k, v in updates.items()}}


# ── Mesh ──────────────────────────────────────────────────────────────────────

class MeshConfigPatch(BaseModel):
    n_radial:       Optional[int]   = None
    n_angular:      Optional[int]   = None
    n_angular_slots: Optional[int]  = None


@app.get("/api/mesh/config")
def get_mesh_config():
    """Return current mesh collocation-point config."""
    cfg = get_config()
    m = cfg.get("mesh", {})
    return {
        "n_radial":        m.get("n_radial", 10),
        "n_angular":       m.get("n_angular", 64),
        "n_angular_slots": m.get("n_angular_slots", 8),
    }


@app.patch("/api/mesh/config")
def update_mesh_config(patch: MeshConfigPatch):
    """Update mesh parameters in motor_config.yaml."""
    import re
    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    content = _CONFIG_PATH.read_text(encoding="utf-8")
    lines   = content.splitlines(keepends=True)
    in_mesh = False
    result  = []
    replaced = set()

    for line in lines:
        if re.match(r'^mesh\s*:', line):
            in_mesh = True
        elif in_mesh and re.match(r'^\S', line):
            in_mesh = False

        if in_mesh:
            for key, val in updates.items():
                m = re.match(rf'^(\s+{re.escape(key)}\s*:\s*)(.*)$', line)
                if m:
                    line = m.group(1) + str(val) + '\n'
                    replaced.add(key)
                    break

        result.append(line)

    missing = set(updates) - replaced
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Keys not found in mesh: block: {missing}"
        )

    _CONFIG_PATH.write_text(''.join(result), encoding="utf-8")
    clear_config_cache()
    return {"status": "ok", "updated": updates}


def main():
    import uvicorn
    uvicorn.run("motor_ai_sim.api:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
