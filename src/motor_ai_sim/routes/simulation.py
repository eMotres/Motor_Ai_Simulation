"""REST endpoints for the 2-D magnetostatics PINN simulation.

Endpoints
---------
GET  /api/simulation/status          — check Modulus availability
POST /api/simulation/run             — start a simulation (async)
GET  /api/simulation/result/{job_id} — poll job status / result
GET  /api/simulation/config          — current operating-point config
PATCH /api/simulation/config        — update operating-point config
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulation", tags=["simulation"])

# ── In-memory job store (replace with Redis/DB for production) ────────────────
_jobs: Dict[str, Dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class SimRunRequest(BaseModel):
    """Parameters for a single simulation run."""
    max_current: float   = Field(default=10.0, description="Peak phase current [A]")
    frequency:   float   = Field(default=50.0, description="Electrical frequency [Hz]")
    rpm:         float   = Field(default=2000.0, description="Rotor speed [rpm]")
    rotor_angle: float   = Field(default=0.0,  description="Static rotor angle [deg]")
    max_steps:   int     = Field(default=10_000, ge=100, le=200_000,
                                  description="PINN training steps")
    device:      str     = Field(default="cpu", description="'cuda' or 'cpu'")


class SimConfigPatch(BaseModel):
    max_current: Optional[float] = None
    frequency:   Optional[float] = None
    rpm:         Optional[float] = None


class JobStatus(BaseModel):
    job_id:   str
    status:   str    # "queued" | "running" | "done" | "error"
    progress: float  # 0.0 – 1.0
    result:   Optional[Dict] = None
    error:    Optional[str]  = None
    elapsed_s: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status")
def simulation_status():
    """Return Modulus availability and current config."""
    try:
        from motor_ai_sim.simulation.solver_2d import HAS_MODULUS
        from motor_ai_sim.simulation.solver_2d import SimConfig
        cfg = SimConfig.from_motor_config()
    except Exception as e:
        return {"modulus_available": False, "error": str(e)}

    return {
        "modulus_available": HAS_MODULUS,
        "operating_point": {
            "max_current":  cfg.I_peak,
            "frequency_hz": cfg.frequency_hz,
            "rpm":          cfg.rpm,
            "Br_magnet_T":  cfg.Br_magnet,
        },
        "solver": "2D Magnetostatics PINN (Modulus Sym)" if HAS_MODULUS
                  else "Modulus not installed — dry-run mode",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Run (async background task)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=JobStatus, status_code=202)
def run_simulation(req: SimRunRequest, background_tasks: BackgroundTasks):
    """Enqueue a PINN training job and return a job_id to poll."""
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "status":   "queued",
        "progress": 0.0,
        "result":   None,
        "error":    None,
        "start_t":  None,
    }

    background_tasks.add_task(_run_job, job_id, req)

    return JobStatus(
        job_id=job_id,
        status="queued",
        progress=0.0,
    )


def _run_job(job_id: str, req: SimRunRequest) -> None:
    """Background worker: build solver, train, store result."""
    from motor_ai_sim.simulation.solver_2d import (
        MagnetostaticsSolver2D,
        SimConfig,
        MotorDomainParams,
    )
    from motor_ai_sim.simulation.geometry_2d import params_from_config

    job = _jobs[job_id]
    job["status"]  = "running"
    job["start_t"] = time.time()

    try:
        sim_cfg = SimConfig.from_motor_config()
        # Override with request values
        sim_cfg.I_peak       = req.max_current
        sim_cfg.frequency_hz = req.frequency
        sim_cfg.rpm          = req.rpm
        sim_cfg.rotor_angle_deg = req.rotor_angle
        sim_cfg.max_steps    = req.max_steps
        sim_cfg.device       = req.device

        geo_params = params_from_config()
        solver = MagnetostaticsSolver2D(sim_cfg, geo_params)

        job["progress"] = 0.1
        result = solver.run()
        job["progress"] = 1.0
        job["status"]   = "done"
        job["result"]   = result

    except Exception as exc:
        log.exception("Simulation job %s failed", job_id)
        job["status"] = "error"
        job["error"]  = str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Poll result
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/result/{job_id}", response_model=JobStatus)
def get_result(job_id: str):
    """Poll job status / fetch result when done."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    job = _jobs[job_id]
    elapsed = None
    if job["start_t"]:
        elapsed = time.time() - job["start_t"]

    return JobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        result=job["result"],
        error=job["error"],
        elapsed_s=elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Config read / update
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
def get_sim_config():
    """Return current simulation operating-point config."""
    from motor_ai_sim.config import get_config
    cfg = get_config()
    return cfg.get("simulation", {})


@router.patch("/config")
def update_sim_config(patch: SimConfigPatch):
    """Update simulation parameters in motor_config.yaml."""
    import re
    from pathlib import Path
    from motor_ai_sim.config import clear_config_cache

    cfg_path = Path(__file__).parent.parent.parent.parent / "config" / "motor_config.yaml"
    content = cfg_path.read_text(encoding="utf-8")

    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    lines = content.splitlines(keepends=True)
    in_sim = False
    result = []
    replaced = set()

    for line in lines:
        if re.match(r'^simulation\s*:', line):
            in_sim = True
        elif in_sim and re.match(r'^\S', line):
            in_sim = False

        if in_sim:
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
            detail=f"Keys not found in simulation: block: {missing}"
        )

    cfg_path.write_text(''.join(result), encoding="utf-8")
    clear_config_cache()
    return {"status": "ok", "updated": updates}
