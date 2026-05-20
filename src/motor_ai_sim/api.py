"""FastAPI server for motor geometry API.

Usage:
    uvicorn motor_ai_sim.api:app --reload --port 8000
    python -m motor_ai_sim.api
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from motor_ai_sim.config import get_config, get_material_assignments
from motor_ai_sim.routes.geometry import router as geometry_router
from motor_ai_sim.routes.pipeline import router as pipeline_router
from motor_ai_sim.services.geometry_service import get_current_geometry, params_to_dict

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
            "/api/config",
        ],
    }


@app.get("/api/health")
def health_check():
    return {"status": "healthy"}


@app.get("/api/materials")
def get_materials():
    try:
        return get_material_assignments()
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
