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

# ── FEM background state ──────────────────────────────────────────────────────
_refine_state: Dict[str, Any] = {
    "running": False, "done": 0, "total": 0, "results": [],
    "run_id": "", "error": None, "cancel": False,
}
_refine_lock = threading.Lock()

_scan_state: Dict[str, Any] = {
    "running": False, "done": 0, "total": 0, "result": None,
    "run_id": "", "error": None, "cancel": False,
}
_scan_lock = threading.Lock()
_SCAN_WORKERS = 5            # concurrent FEM subprocesses


def _subprocess_eval(overrides: Dict[str, float], current_a: float, steps: int,
                     coil_temp_c: float, n_periods: float = 1.0) -> Dict[str, Any]:
    """Evaluate ONE (geometry, current) with the real sliding-band transient in
    an isolated subprocess (FEM/LLVM crash → failed design, not a dead API).
    Rebuilds the CadQuery geometry + gmsh mesh for the candidate in-memory.
    ``steps`` frames over ``n_periods`` of the electrical period."""
    import subprocess, sys, json
    spec = json.dumps({"overrides": overrides, "current_a": current_a,
                       "steps": int(steps), "coil_temp_c": float(coil_temp_c),
                       "n_periods": float(n_periods)})
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "motor_ai_sim.optimization.refine_proc"],
            input=spec, capture_output=True, text=True, timeout=900)
        out = proc.stdout or ""
        m = out.rfind("@@RESULT@@")
        if m >= 0:
            return json.loads(out[m + len("@@RESULT@@"):])
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["subprocess crashed"]
        return {"ok": False, "error": tail[0][:160]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


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
    for i, dz in enumerate(designs):
        if _refine_state["cancel"]:
            break
        ov = {k: float(v) for k, v in (dz.get("overrides") or {}).items()}
        I = float(dz.get("current_a", 85.0))
        payload = _subprocess_eval(ov, I, steps, coil_temp_c)
        if payload.get("ok"):
            res = {**payload["res"], "overrides": ov, "current_a": I,
                   "fem": True, "feasible": True, "eligible": True}
        else:
            res = {"overrides": ov, "current_a": I, "fem": True,
                   "feasible": False, "error": payload.get("error", "eval failed")}
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


# ─────────────────────────────────────────────────────────────────────────────
# FEM SCAN — every Pareto point is a REAL sliding-band transient (geometry +
# mesh rebuilt per candidate), at a low step count by default (fast); the front
# can then be refined at a higher step count.  Geometries are enumerated from
# the variables (sweep → grid, optimize → linspace), capped, and each geometry
# is evaluated at both operating currents in parallel isolated subprocesses.
# ─────────────────────────────────────────────────────────────────────────────
class ScanRequest(BaseModel):
    variables: List[OptVariable] = Field(default_factory=list)
    operating_points: List[OptOperating] = Field(default_factory=lambda: [OptOperating()])
    steps_per_period: int = 6              # FEM frames per electrical period
    ripple_max_pct: float = 100.0
    max_geometries: int = 24               # cap on enumerated geometries
    coil_temp_c: float = 120.0
    seed: int = 12345
    run_id: str = ""


def _enumerate_geometries(variables: List[Dict[str, Any]], max_geom: int,
                          n_opt: int, seed: int) -> List[Dict[str, float]]:
    """Build the candidate geometry list: sweep vars take their grid values
    (min:step:max), optimize vars take n_opt evenly-spaced values; the Cartesian
    product is capped (deterministic subsample) at max_geom."""
    import numpy as np, itertools
    gvars = [v for v in variables if v.get("name") not in ("current_a", "rpm")]
    if not gvars:
        return [{}]
    axes = []
    for v in gvars:
        lo, hi = float(v["min"]), float(v["max"])
        if v.get("mode") == "sweep" and float(v.get("step", 0)) > 0 and hi > lo:
            st = float(v["step"])
            vals = list(np.arange(lo, hi + st * 0.5, st))
        elif hi > lo:
            vals = list(np.linspace(lo, hi, max(2, int(n_opt))))
        else:
            vals = [lo]
        axes.append([(v["name"], round(float(x), 4)) for x in vals])
    total = 1
    for a in axes:
        total *= len(a)
    rng = np.random.default_rng(int(seed))
    if total <= max_geom:
        return [dict(c) for c in itertools.product(*axes)]
    # Too many combinations to materialize — sample max_geom distinct ones.
    seen, combos, attempts = set(), [], 0
    while len(combos) < max_geom and attempts < max_geom * 60:
        attempts += 1
        pick = tuple(a[int(rng.integers(len(a)))] for a in axes)
        key = tuple(pick)
        if key in seen:
            continue
        seen.add(key)
        combos.append(dict(pick))
    return combos


def _point_from_eval(out: Dict[str, Any], ov: Dict[str, float], I: float,
                     gi: int, oi: int, ripple_max: float) -> Dict[str, Any]:
    if out.get("ok"):
        r = out["res"]
        return {**r, "overrides": ov, "current_a": I, "geom_id": gi,
                "op_index": oi, "fem": True, "feasible": True,
                "eligible": bool(r.get("T_ripple_pct", 1e9) <= ripple_max)}
    return {"overrides": ov, "current_a": I, "geom_id": gi, "op_index": oi,
            "fem": True, "feasible": False, "eligible": False,
            "error": out.get("error", "eval failed")}


def _scan_worker(variables, operating_points, steps, coil_temp_c, ripple_max,
                 max_geom, seed, run_id) -> None:
    import numpy as np  # noqa: F401
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from motor_ai_sim.optimization.optimizer import _pareto_front
    try:
        geos = _enumerate_geometries(variables, int(max_geom), n_opt=4, seed=seed)
        tasks = []  # (geom_id, op_index, overrides, current)
        for gi, ov in enumerate(geos):
            for oi, op in enumerate(operating_points):
                tasks.append((gi, oi, ov, float(op.get("current_a", 85.0))))
        with _scan_lock:
            _scan_state.update(total=len(tasks) + 1, done=0)
        points: List[Any] = [None] * len(tasks)

        # Sweep only 1/6 of the electrical period — one cycle of the dominant
        # 6·k torque ripple — so ``steps`` (~6) frames capture the ripple
        # amplitude in 6× fewer FEM solves than a full period.
        _NPER = 1.0 / 6.0

        def _do(i_t):
            i, (gi, oi, ov, I) = i_t
            out = _subprocess_eval(ov, I, steps, coil_temp_c, n_periods=_NPER)
            return i, _point_from_eval(out, ov, I, gi, oi, ripple_max)

        with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
            futs = [ex.submit(_do, it) for it in enumerate(tasks)]
            done = 0
            for fut in as_completed(futs):
                if _scan_state["cancel"]:
                    break
                i, pt = fut.result()
                points[i] = pt
                done += 1
                with _scan_lock:
                    _scan_state["done"] = done
        points = [p for p in points if p is not None]

        # segments: the two operating points of one geometry
        by_geom: Dict[int, List[int]] = {}
        for idx, p in enumerate(points):
            by_geom.setdefault(p["geom_id"], []).append(idx)
        segments = [v for v in by_geom.values() if len(v) == 2]

        elig = [(idx, p) for idx, p in enumerate(points) if p.get("eligible")]
        # Fall back to all FEASIBLE points if the ripple gate excluded everything
        # (FEM ripple at a low step count is coarse) — the front always shows.
        pool = elig if elig else [(idx, p) for idx, p in enumerate(points) if p.get("feasible")]
        obj = [(p["torque_per_mass_Nm_kg"], p["efficiency"]) for _, p in pool]
        front_local = _pareto_front(obj) if obj else []
        pareto_indices = sorted([pool[i][0] for i in front_local],
                                key=lambda i: points[i]["torque_per_mass_Nm_kg"])

        # baseline = current motor at operating point 0 (real FEM)
        base_out = _subprocess_eval({}, float(operating_points[0].get("current_a", 85.0)),
                                    steps, coil_temp_c, n_periods=_NPER)
        baseline = _point_from_eval(base_out, {}, float(operating_points[0].get("current_a", 85.0)),
                                    -1, 0, ripple_max)
        with _scan_lock:
            _scan_state["done"] = len(tasks) + 1

        n_built = sum(1 for p in points if p.get("feasible"))
        result = {
            "points": points, "segments": segments, "pareto_indices": pareto_indices,
            "baseline": baseline, "n_total_points": len(points),
            "n_built": n_built, "n_failed": len(points) - n_built,
            "n_eligible_points": len(elig), "n_geometries": len(geos),
            "variables": [{"name": v["name"], "min": float(v["min"]), "max": float(v["max"])}
                          for v in variables if v.get("name") not in ("current_a", "rpm")],
            "operating_points": operating_points, "ripple_max_pct": float(ripple_max),
            "objective": "pareto_torque_density_vs_efficiency_FEM",
            "steps_per_period": int(steps), "fem": True,
        }
        with _scan_lock:
            _scan_state["result"] = result
    except Exception as e:  # noqa: BLE001
        log.exception("FEM scan failed")
        with _scan_lock:
            _scan_state["error"] = str(e)
    finally:
        with _scan_lock:
            _scan_state["running"] = False


@router.post("/scan")
def scan_designs(req: ScanRequest):
    """Start a background FEM scan — every point is a real transient."""
    with _scan_lock:
        if _scan_state["running"]:
            raise HTTPException(status_code=409, detail="a scan is already running")
        variables = [{"name": v.name, "min": float(v.min), "max": float(v.max),
                      "mode": v.mode, "step": float(v.step)} for v in req.variables]
        ops = [{"gamma_deg": float(o.gamma_deg), "current_a": float(o.current_a),
                "rpm": float(o.rpm)} for o in (req.operating_points or [])] or \
              [{"gamma_deg": 0.0, "current_a": 85.0, "rpm": 3950.0}]
        steps = max(4, min(int(req.steps_per_period), 180))
        max_geom = max(1, min(int(req.max_geometries), 80))
        _scan_state.update({"running": True, "done": 0, "total": 0, "result": None,
                            "run_id": req.run_id, "error": None, "cancel": False})
    threading.Thread(target=_scan_worker,
                     args=(variables, ops, steps, float(req.coil_temp_c),
                           float(req.ripple_max_pct), max_geom, int(req.seed), req.run_id),
                     daemon=True).start()
    return {"started": True, "steps_per_period": steps, "max_geometries": max_geom}


@router.get("/scan/progress")
def scan_progress():
    with _scan_lock:
        return dict(_scan_state)


@router.post("/scan/cancel")
def scan_cancel():
    with _scan_lock:
        _scan_state["cancel"] = True
    return {"cancelled": True}
