"""Design-optimization API — Pareto search over geometry + operating point.

POST /api/optimization/run runs a fast analytical Latin-Hypercube + Pareto
search and returns the scatter of evaluated designs, the non-dominated front
(torque density vs efficiency) and the baseline point.

This endpoint is READ-ONLY w.r.t. global state: it reads the current config as
the baseline but evaluates every candidate in-memory (motor_ai_sim.optimization
deep-copies the geometry per candidate).  It never writes motor_config.yaml and
never touches the simulation caches — so running an optimization cannot disturb
the Simulation tab.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from motor_ai_sim.config import get_config
from motor_ai_sim.optimization import run_pareto_search

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/optimization", tags=["optimization"])

# ── FEM-refine background state ───────────────────────────────────────────────
_refine_state: Dict[str, Any] = {
    "running": False, "done": 0, "total": 0, "results": [],
    "run_id": "", "error": None, "cancel": False,
}
_refine_lock = threading.Lock()


class OptVariable(BaseModel):
    name: str                       # geometry key, or 'gamma_deg' / 'current_a'
    min: float
    max: float
    mode: str = "optimize"          # 'optimize' = continuous, 'sweep' = grid
    step: float = 0.0               # grid step (used only for 'sweep')


class OptOperating(BaseModel):
    gamma_deg: float = 0.0
    current_a: float = 85.0
    rpm: float = 3950.0


class OptRequest(BaseModel):
    variables: List[OptVariable] = Field(default_factory=list)
    # One point per (geometry, operating-point); two points → a Pareto segment.
    operating_points: List[OptOperating] = Field(default_factory=lambda: [OptOperating()])
    ripple_max_pct: float = 100.0          # cogging-ripple selection gate [%]
    n_samples: int = 600                   # number of GEOMETRY samples
    coil_temp_c: float = 120.0
    seed: int = 12345


@router.post("/run")
def run_optimization(req: OptRequest):
    """Run the Pareto design search and return points + segments + front."""
    try:
        cfg = get_config()
        geo = dict(cfg.get("geometry", {}))
        wind = dict(cfg.get("winding", {}))
        sim = dict(cfg.get("simulation", {}))
        variables = [{"name": v.name, "min": float(v.min), "max": float(v.max),
                      "mode": v.mode, "step": float(v.step)}
                     for v in req.variables]
        ops = [{"gamma_deg": float(o.gamma_deg), "current_a": float(o.current_a),
                "rpm": float(o.rpm)} for o in (req.operating_points or [])]
        if not ops:
            ops = [{"gamma_deg": 0.0, "current_a": float(sim.get("max_current", 85.0)),
                    "rpm": float(sim.get("rpm", 3950.0))}]
        n = max(20, min(int(req.n_samples), 5000))
        res = run_pareto_search(
            geo, wind, sim, variables, ops,
            n_samples=n, ripple_max_pct=float(req.ripple_max_pct),
            coil_temp_c=float(req.coil_temp_c), seed=int(req.seed))
        return res
    except Exception as e:  # noqa: BLE001
        log.exception("optimization run failed")
        raise HTTPException(status_code=500, detail=f"optimization failed: {e}")


@router.get("/variables")
def list_optimizable_variables():
    """Geometry params (from the config schema) that can be optimized, plus the
    two operating-point variables, with sensible default ranges (schema
    min/max).  The frontend uses this to offer a variable picker."""
    cfg = get_config()
    schema = cfg.get("geometry_schema", {})
    geo = cfg.get("geometry", {})
    out = []
    # numeric geometry params (skip integer topology that breaks slot/pole count)
    skip = {"num_seg", "num_slots_per_segment", "num_poles_per_segment",
            "num_wires_per_slot"}
    for name, meta in schema.items():
        if name in skip or name not in geo:
            continue
        try:
            cur = float(geo[name])
        except (TypeError, ValueError):
            continue
        lo = float(meta.get("min", cur * 0.5))
        hi = float(meta.get("max", cur * 1.5))
        out.append({"name": name, "label": meta.get("label", name),
                    "unit": meta.get("unit", ""), "group": meta.get("group", ""),
                    "current": cur, "min": lo, "max": hi})
    # operating-point variables
    out.append({"name": "gamma_deg", "label": "Load angle γ", "unit": "°",
                "group": "operating", "current": float(cfg.get("simulation", {}).get("phase_offset_deg", 0.0)),
                "min": -30.0, "max": 45.0})
    out.append({"name": "current_a", "label": "Phase current", "unit": "Arms",
                "group": "operating", "current": float(cfg.get("simulation", {}).get("max_current", 85.0)),
                "min": 20.0, "max": 120.0})
    return {"variables": out}


# ─────────────────────────────────────────────────────────────────────────────
# FEM refinement — re-evaluate selected designs with the real sliding-band
# transient (period-averaged T, losses, efficiency, HONEST ripple).  Each design
# is evaluated in-memory via geo_override, so the global config / Simulation
# state is never touched.  Runs in a background thread; the UI polls /progress.
# ─────────────────────────────────────────────────────────────────────────────
class RefineDesign(BaseModel):
    overrides: Dict[str, float] = Field(default_factory=dict)  # geometry keys
    current_a: float = 85.0
    rpm: float = 3950.0


class RefineRequest(BaseModel):
    designs: List[RefineDesign] = Field(default_factory=list)
    steps_per_period: int = 40
    coil_temp_c: float = 120.0
    run_id: str = ""


def _refine_worker(designs: List[Dict[str, Any]], steps: int, coil_temp_c: float,
                   run_id: str) -> None:
    """Evaluate each design in an ISOLATED subprocess (the FEM stack can crash
    the LLVM JIT; a subprocess crash yields a failed design, not a dead API)."""
    import subprocess, sys, json
    for i, dz in enumerate(designs):
        if _refine_state["cancel"]:
            break
        ov = {k: float(v) for k, v in (dz.get("overrides") or {}).items()}
        I = float(dz.get("current_a", 85.0))
        spec = json.dumps({"overrides": ov, "current_a": I, "steps": int(steps),
                           "coil_temp_c": float(coil_temp_c)})
        res: Dict[str, Any]
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "motor_ai_sim.optimization.refine_proc"],
                input=spec, capture_output=True, text=True, timeout=900)
            out = proc.stdout or ""
            marker = out.rfind("@@RESULT@@")
            if marker >= 0:
                payload = json.loads(out[marker + len("@@RESULT@@"):])
                if payload.get("ok"):
                    res = {**payload["res"], "overrides": ov, "current_a": I,
                           "fem": True, "feasible": True, "eligible": True}
                else:
                    res = {"overrides": ov, "current_a": I, "fem": True,
                           "feasible": False, "error": payload.get("error", "eval failed")}
            else:
                # no result marker → the subprocess crashed (e.g. LLVM JIT)
                tail = (proc.stderr or "").strip().splitlines()[-1:] or ["subprocess crashed"]
                res = {"overrides": ov, "current_a": I, "fem": True,
                       "feasible": False, "error": tail[0][:160]}
        except subprocess.TimeoutExpired:
            res = {"overrides": ov, "current_a": I, "fem": True,
                   "feasible": False, "error": "timeout"}
        except Exception as e:  # noqa: BLE001
            log.exception("refine subprocess failed")
            res = {"overrides": ov, "current_a": I, "fem": True,
                   "feasible": False, "error": str(e)}
        with _refine_lock:
            _refine_state["results"].append(res)
            _refine_state["done"] = i + 1
    with _refine_lock:
        _refine_state["running"] = False


@router.post("/refine")
def refine_designs(req: RefineRequest):
    """Start a background FEM-transient refinement of up to 60 designs."""
    with _refine_lock:
        if _refine_state["running"]:
            raise HTTPException(status_code=409, detail="a refinement is already running")
        designs = [d.model_dump() if hasattr(d, "model_dump") else d.dict()
                   for d in (req.designs or [])][:60]
        if not designs:
            raise HTTPException(status_code=400, detail="no designs to refine")
        _refine_state.update({"running": True, "done": 0, "total": len(designs),
                              "results": [], "run_id": req.run_id, "error": None,
                              "cancel": False})
    steps = max(8, min(int(req.steps_per_period), 180))
    t = threading.Thread(target=_refine_worker,
                         args=(designs, steps, float(req.coil_temp_c), req.run_id),
                         daemon=True)
    t.start()
    return {"started": True, "total": len(designs), "steps_per_period": steps}


@router.get("/refine/progress")
def refine_progress():
    with _refine_lock:
        return dict(_refine_state)


@router.post("/refine/cancel")
def refine_cancel():
    with _refine_lock:
        _refine_state["cancel"] = True
    return {"cancelled": True}
