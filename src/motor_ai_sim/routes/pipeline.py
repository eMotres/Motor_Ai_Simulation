from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/pipeline")

# The geometry pipeline was the NVIDIA Modulus SDF/point-cloud bridge
# (motor_ai_sim.modulus_bridge.ModulusBridge).  That path has been removed, so
# the pipeline is permanently unavailable; the endpoints below preserve their
# previous behavior of reporting unavailability / returning HTTP 503.
HAS_PIPELINE = False  # NVIDIA Modulus path removed


def _require_pipeline():
    if not HAS_PIPELINE:
        raise HTTPException(status_code=503, detail="Pipeline components not available")


@router.get("/status")
def get_pipeline_status():
    return {
        "fusion360_available": HAS_PIPELINE,
        "modulus_bridge_available": HAS_PIPELINE,
        "cache_enabled": True,
    }


@router.post("/clear-cache")
def clear_pipeline_cache():
    _require_pipeline()


@router.post("/generate")
def generate_geometry_pipeline(params: dict = Body(default={})):
    _require_pipeline()


@router.get("/stl/{component}")
def get_stl_mesh(component: str):
    _require_pipeline()


@router.get("/stream")
async def stream_pipeline():
    _require_pipeline()


@router.get("/validate")
def validate_ai_geometry(n_points: int = 50000):
    _require_pipeline()
