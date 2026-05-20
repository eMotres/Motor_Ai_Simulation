import asyncio
import json
import tempfile
from typing import Dict

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/pipeline")

try:
    from motor_ai_sim.cadquery_geometry import CadQueryCache, CadQueryMotor
    from motor_ai_sim.modulus_bridge import ModulusBridge
    HAS_PIPELINE = True
except ImportError:
    HAS_PIPELINE = False
    print("Warning: Pipeline components not available")


def _require_pipeline():
    if not HAS_PIPELINE:
        raise HTTPException(status_code=503, detail="Pipeline components not available")


def _latest_cache_dir():
    cache = CadQueryCache()
    cache_dir = cache.cache_dir
    if not cache_dir.exists():
        raise HTTPException(status_code=404, detail="No cached geometry found. Run /api/pipeline/generate first.")
    dirs = sorted([d for d in cache_dir.iterdir() if d.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)
    if not dirs:
        raise HTTPException(status_code=404, detail="No cached geometry found. Run /api/pipeline/generate first.")
    return dirs[0]


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
    try:
        CadQueryCache().clear_all()
        return {"status": "success", "message": "Cache cleared successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
def generate_geometry_pipeline(params: Dict = Body(default={})):
    _require_pipeline()
    try:
        motor = CadQueryMotor()
        if params:
            motor.set_parameters(params)

        param_hash = motor.get_parameter_hash()
        cache = CadQueryCache()

        if cache.exists(param_hash):
            stl_dir = cache.get_cache_path(param_hash)
        else:
            stl_files = motor.export_stl(tempfile.mkdtemp())
            stl_dir = cache.save(param_hash, stl_files)

        bridge = ModulusBridge()
        bridge.load_stl_files(str(stl_dir))

        try:
            if bridge.HAS_MODULUS:
                bridge.create_sdf(airtight=True)
        except Exception as e:
            print(f"Warning: Failed to create SDF: {e}")

        try:
            validation = bridge.validate_geometry(n_points=50000)
        except Exception as e:
            validation = {"error": str(e), "status": "validation_failed"}

        return {
            "status": "success",
            "param_hash": param_hash,
            "stl_directory": str(stl_dir),
            "validation": validation,
            "components": list(bridge.mesh_data.keys()),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stl/{component}")
def get_stl_mesh(component: str):
    _require_pipeline()
    try:
        import trimesh
        stl_path = _latest_cache_dir() / f"{component}.stl"
        if not stl_path.exists():
            raise HTTPException(status_code=404, detail=f"Component '{component}' not found in cache.")
        mesh = trimesh.load_mesh(str(stl_path))
        return {
            "component": component,
            "vertices": mesh.vertices.tolist(),
            "faces": mesh.faces.tolist(),
            "vertex_count": len(mesh.vertices),
            "face_count": len(mesh.faces),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stream")
async def stream_pipeline():
    """SSE endpoint that runs the pipeline for the current geometry and streams progress.

    Usage (JavaScript):
        const es = new EventSource('/api/pipeline/stream');
        es.onmessage = e => console.log(JSON.parse(e.data));

    Each event has: { stage, progress, ...optional_payload }
    The final event has stage='complete' with full pipeline result.
    """
    _require_pipeline()

    def _event(stage: str, progress: int, **extra) -> str:
        return f"data: {json.dumps({'stage': stage, 'progress': progress, **extra})}\n\n"

    async def generate():
        loop = asyncio.get_event_loop()

        yield _event("starting", 0)

        motor = CadQueryMotor()
        param_hash = motor.get_parameter_hash()
        cache = CadQueryCache()

        if cache.exists(param_hash):
            stl_dir = cache.get_cache_path(param_hash)
            yield _event("cache_hit", 50)
        else:
            yield _event("building_geometry", 10)
            stl_files = await loop.run_in_executor(
                None, lambda: motor.export_stl(tempfile.mkdtemp())
            )
            stl_dir = cache.save(param_hash, stl_files)
            yield _event("stl_exported", 50)

        yield _event("loading_bridge", 60)
        bridge = ModulusBridge()
        bridge.load_stl_files(str(stl_dir))

        if bridge.HAS_MODULUS:
            yield _event("creating_sdf", 70)
            try:
                await loop.run_in_executor(None, lambda: bridge.create_sdf(airtight=True))
            except Exception as e:
                yield _event("sdf_warning", 80, message=str(e))

        yield _event("validating", 85)
        try:
            validation = await loop.run_in_executor(
                None, lambda: bridge.validate_geometry(n_points=50000)
            )
        except Exception as e:
            validation = {"error": str(e), "status": "validation_failed"}

        yield _event(
            "complete", 100,
            param_hash=param_hash,
            stl_directory=str(stl_dir),
            validation=validation,
            components=list(bridge.mesh_data.keys()),
        )

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/validate")
def validate_ai_geometry(n_points: int = 50000):
    _require_pipeline()
    try:
        latest_cache = _latest_cache_dir()
        bridge = ModulusBridge()
        bridge.load_stl_files(str(latest_cache))
        result = bridge.validate_geometry(n_points=n_points)
        return {"param_hash": latest_cache.name, "validation": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
