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

import json
import logging
import math
import os
import re
import threading
from datetime import datetime
from pathlib import Path
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
# Concurrent FEM subprocesses.  Each refine_proc is one process pinned to a
# single core, so this is effectively "how many cores the optimizer uses".
# Default to (physical cores − 2): use most of the box but leave ~2 cores for
# the uvicorn event loop + the descent daemon thread, so /progress polling and
# the live charts stay responsive.  Override with FEM_SCAN_WORKERS.
def _scan_worker_count() -> int:
    env = os.environ.get("FEM_SCAN_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    logical = os.cpu_count() or 8
    physical = logical // 2 if logical > 4 else logical   # SMT/HT → physical ≈ logical/2
    return max(2, physical - 2)
_SCAN_WORKERS = _scan_worker_count()   # e.g. 10 on a 12-physical-core box


def _subprocess_eval(overrides: Dict[str, float], current_a: float, steps: int,
                     coil_temp_c: float, n_periods: float = 1.0,
                     gamma_deg: float = 0.0, mesh_size_mm: float = 4.0,
                     min_size_mm: float = 0.3, n_sectors: int = -1) -> Dict[str, Any]:
    """Evaluate ONE (geometry, current, γ) with the real sliding-band transient
    in an isolated subprocess (FEM/LLVM crash → failed design, not a dead API).
    Rebuilds the CadQuery geometry + gmsh mesh for the candidate in-memory.
    ``steps`` frames over ``n_periods`` of the electrical period.  n_sectors=-1
    = full disk (accurate ripple); 4 = ¼ sector (≈3× faster, for quick debug)."""
    import subprocess, sys, json
    spec = json.dumps({"overrides": overrides, "current_a": current_a,
                       "steps": int(steps), "coil_temp_c": float(coil_temp_c),
                       "n_periods": float(n_periods), "gamma_deg": float(gamma_deg),
                       "mesh_size_mm": float(mesh_size_mm), "min_size_mm": float(min_size_mm),
                       "n_sectors": int(n_sectors)})
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
    mesh_size_mm: float = 4.0              # mesh resolution (set from Mesh tab) — coarser = faster scan
    min_size_mm: float = 0.3
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
    # Enumerate the EXACT grid (sweep min:step:max × optimize spread), in order,
    # up to the safety cap — no rounding, no random subsampling, so the user
    # gets precisely the points their Sweep Variables define.
    combos: List[Dict[str, float]] = []
    for c in itertools.product(*axes):
        combos.append(dict(c))
        if len(combos) >= max_geom:
            break
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
                 max_geom, seed, run_id, mesh_size_mm=4.0, min_size_mm=0.3) -> None:
    import numpy as np  # noqa: F401
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from motor_ai_sim.optimization.optimizer import _pareto_front
    try:
        geos = _enumerate_geometries(variables, int(max_geom), n_opt=4, seed=seed)
        tasks = []  # (geom_id, op_index, overrides, current, op_gamma)
        for gi, ov in enumerate(geos):
            for oi, op in enumerate(operating_points):
                tasks.append((gi, oi, ov, float(op.get("current_a", 85.0)),
                              float(op.get("gamma_deg", 0.0))))
        with _scan_lock:
            _scan_state.update(total=len(tasks) + 1, done=0)
        points: List[Any] = [None] * len(tasks)

        # Sweep a FULL electrical period: the iron/magnet eddy losses are
        # computed from dB/dt and need the whole period for a correct frequency
        # content (a 1/6-period window inflated them ~2-5× → wrong efficiency).
        # ``steps`` frames/period — 18 gives 3 samples per 6·k ripple cycle.
        _NPER = 1.0

        def _do(i_t):
            i, (gi, oi, ov, I, opg) = i_t
            # γ as a swept variable overrides the operating-point γ; it is NOT a
            # geometry key, so split it out before building the mesh.
            g = float(ov.get("gamma_deg", opg))
            geo_ov = {k: v for k, v in ov.items() if k != "gamma_deg"}
            out = _subprocess_eval(geo_ov, I, steps, coil_temp_c,
                                   n_periods=_NPER, gamma_deg=g,
                                   mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm)
            return i, _point_from_eval(out, ov, I, gi, oi, ripple_max)

        # Manual executor so a Stop can cancel the not-yet-started tasks (the
        # ~5 already-running subprocesses just finish in the background); the
        # partial results computed so far are kept and shown.
        ex = ThreadPoolExecutor(max_workers=_SCAN_WORKERS)
        futs = [ex.submit(_do, it) for it in enumerate(tasks)]
        done = 0
        try:
            for fut in as_completed(futs):
                if _scan_state["cancel"]:
                    break
                i, pt = fut.result()
                points[i] = pt
                done += 1
                with _scan_lock:
                    _scan_state["done"] = done
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
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

        # baseline = current motor at operating point 0 (real FEM) — skipped on
        # a Stop so the partial result shows immediately.
        if _scan_state["cancel"]:
            baseline = {"feasible": False, "fem": True, "overrides": {}}
        else:
            base_out = _subprocess_eval({}, float(operating_points[0].get("current_a", 85.0)),
                                        steps, coil_temp_c, n_periods=_NPER,
                                        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm)
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
        # Whitelist gate: only sweep_whitelist params (plus the load-angle
        # gamma_deg, which is not a geometry knob) may be scanned.  Empty/missing
        # whitelist → allow all (back-compat).
        wl = get_config().get("sweep_whitelist", None)
        allowed = set(wl or [])
        variables = [{"name": v.name, "min": float(v.min), "max": float(v.max),
                      "mode": v.mode, "step": float(v.step)} for v in req.variables
                     if (not allowed) or v.name == "gamma_deg" or v.name in allowed]
        if req.variables and not variables:
            raise HTTPException(status_code=400,
                                detail="no scan variables are in the sweep whitelist")
        ops = [{"gamma_deg": float(o.gamma_deg), "current_a": float(o.current_a),
                "rpm": float(o.rpm)} for o in (req.operating_points or [])] or \
              [{"gamma_deg": 0.0, "current_a": 85.0, "rpm": 3950.0}]
        steps = max(4, min(int(req.steps_per_period), 180))
        max_geom = max(1, min(int(req.max_geometries), 400))
        mesh_size = max(1.0, min(float(req.mesh_size_mm), 12.0))
        min_size  = max(0.1, min(float(req.min_size_mm), 3.0))
        _scan_state.update({"running": True, "done": 0, "total": 0, "result": None,
                            "run_id": req.run_id, "error": None, "cancel": False})
    threading.Thread(target=_scan_worker,
                     args=(variables, ops, steps, float(req.coil_temp_c),
                           float(req.ripple_max_pct), max_geom, int(req.seed), req.run_id,
                           mesh_size, min_size),
                     daemon=True).start()
    return {"started": True, "steps_per_period": steps, "max_geometries": max_geom,
            "mesh_size_mm": mesh_size, "min_size_mm": min_size}


@router.get("/scan/progress")
def scan_progress():
    with _scan_lock:
        return dict(_scan_state)


@router.post("/scan/cancel")
def scan_cancel():
    with _scan_lock:
        _scan_state["cancel"] = True
    return {"cancelled": True}


# ─────────────────────────────────────────────────────────────────────────────
# GRADIENT / COORDINATE DESCENT — fixed current+rpm, vary the whitelisted
# geometry knobs.  At each iteration every variable is perturbed ±step (central
# finite difference, evaluated in parallel) to estimate ∂cost/∂var; we then step
# downhill along the normalised gradient with a backtracking line search.
#
#   F    = (efficiency/eff0)^w_eff · (torque_per_mass/td0)^w_td   (both maximised)
#   cost = −F + λ·max(0, ripple − ripple_max)                     (ripple penalty)
#
# So while ripple is above target the penalty dominates (descent drives ripple
# down first); once feasible it rides the constraint boundary while pushing
# efficiency × torque-density up.  Every evaluation is a real sliding-band FEM
# transient in an isolated subprocess.
# ─────────────────────────────────────────────────────────────────────────────
_descent_state: Dict[str, Any] = {
    "running": False, "iter": 0, "max_iters": 0, "n_evals": 0,
    "best": None, "current": None, "history": [], "baseline": None,
    "phase": "", "run_id": "", "error": None, "cancel": False,
}
_descent_lock = threading.Lock()

# ── Persist the last optimization (descent / CMA-ES) to disk ─────────────────
# So the objective-space plot + best design SURVIVE a page reload or a back-end
# restart (the run otherwise lived only in memory).  One entry (the latest),
# written next to the config and reloaded into _descent_state at import — mirrors
# the transient persistence in routes/simulation.py.
import json as _json_o
import os as _os_o


def _descent_store_path() -> str:
    try:
        from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _cp
        _base = _os_o.path.dirname(str(_cp))
    except Exception:
        _base = _os_o.path.join(_os_o.path.dirname(__file__), "..", "..", "..", "config")
    return _os_o.path.abspath(_os_o.path.join(_base, ".last_descent.json"))


def _descent_json_default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    return float(o)


def _save_descent_state() -> None:
    """Best-effort atomic snapshot of the last optimization to disk."""
    try:
        snap = {k: v for k, v in _descent_state.items() if k != "cancel"}
        p = _descent_store_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json_o.dump(snap, fh, default=_descent_json_default)
        _os_o.replace(tmp, p)
    except Exception as _e:   # noqa: BLE001
        log.warning("could not persist descent state: %s", _e)


def _load_descent_state() -> None:
    try:
        p = _descent_store_path()
        if not _os_o.path.exists(p):
            return
        with open(p, encoding="utf-8") as fh:
            blob = _json_o.load(fh)
        if isinstance(blob, dict):
            _descent_state.update(blob)
            _descent_state["running"] = False   # a reloaded run is not in flight
            _descent_state["cancel"] = False
            log.info("restored last optimization from %s", p)
    except Exception as _e:   # noqa: BLE001
        log.warning("could not restore descent state: %s", _e)


_load_descent_state()   # repopulate at import (startup)


class DescentRequest(BaseModel):
    variables: List[OptVariable] = Field(default_factory=list)   # names (+optional bounds/step)
    operating_point: OptOperating = Field(default_factory=OptOperating)
    ripple_max_pct: float = 5.0
    w_eff: float = 1.0                # weight on efficiency ratio
    w_td: float = 1.0                 # weight on torque-density ratio
    penalty_lambda: float = 1.0       # ripple-violation penalty
    max_iters: int = 8
    steps_per_period: int = 24        # FEM frames/period (higher → cleaner ripple gradient)
    coil_temp_c: float = 120.0
    mesh_size_mm: float = 4.0
    min_size_mm: float = 0.3
    # 'cmaes' = Covariance-Matrix-Adaptation ES (derivative-free, noise-robust,
    # default); 'gradient' = the original finite-difference gradient descent.
    algorithm: str = "cmaes"
    # FEM symmetry for the evaluation: -1 = full disk (accurate ripple, default);
    # 4 = ¼ sector (~3× faster, for quick algorithm debugging).
    n_sectors: int = -1
    # Rated-duty constraints (off = 0 / 1e9): target_torque_nm makes each geometry
    # be evaluated at the current that delivers this shaft torque (not a fixed
    # current); v_peak_limit caps the peak phase voltage (inverter DC-bus ×
    # modulation factor) so the bus can actually drive the design.
    target_torque_nm: float = 0.0
    v_peak_limit: float = 1e9
    # Optimize the load angle γ (MTPA) for the starting geometry BEFORE the search.
    optimize_gamma: bool = True
    run_id: str = ""


def _msum(m: Dict[str, Any]) -> Dict[str, Any]:
    """Compact metric snapshot for history / progress."""
    return {
        "T_em_Nm":         m.get("T_em_Nm"),
        "efficiency":      m.get("efficiency"),
        "torque_per_mass": m.get("torque_per_mass_Nm_kg"),
        "T_ripple_pct":    m.get("T_ripple_pct"),
        "mass_total_kg":   m.get("mass_total_kg"),
        "P_loss_total_W":  m.get("P_loss_total_W"),
        "V_peak":          m.get("V_peak"),
    }


def _pt(out: Dict[str, Any], kind: str):
    """One evaluated design as a point in objective space (torque-density vs
    efficiency), for the 2-D projection.  None if the eval failed."""
    if not out or not out.get("ok"):
        return None
    r = out["res"]
    return {"td": r.get("torque_per_mass_Nm_kg"), "eff": r.get("efficiency"),
            "ripple": r.get("T_ripple_pct"), "kind": kind}


def _descent_cost(m: Dict[str, Any], base: Dict[str, Any],
                  ripple_max: float, w_eff: float, w_td: float, lam: float,
                  v_peak_limit: float = 1e9):
    """Scalar cost (lower = better) + the raw figure-of-merit F.

    Penalises BOTH a ripple violation AND an over-voltage — V_peak above the
    inverter's usable phase-voltage limit (DC-bus × modulation factor).  A design
    the bus physically can't drive at the operating point is repelled just like an
    over-ripple one, so the optimizer stays inside the voltage budget."""
    eff  = max(float(m.get("efficiency", 0.0) or 0.0), 1e-6)
    td   = max(float(m.get("torque_per_mass_Nm_kg", 0.0) or 0.0), 1e-6)
    rip  = float(m.get("T_ripple_pct", 1e9) or 1e9)
    vpk  = float(m.get("V_peak", 0.0) or 0.0)
    eff0 = max(float(base.get("efficiency", 1.0) or 1.0), 1e-6)
    td0  = max(float(base.get("torque_per_mass_Nm_kg", 1.0) or 1.0), 1e-6)
    F    = ((eff / eff0) ** w_eff) * ((td / td0) ** w_td)
    pen  = lam * max(0.0, rip - ripple_max) + lam * max(0.0, vpk - v_peak_limit)
    return (-F + pen), F


def _mtpa_gamma_sweep(geom, ref_I, steps, coil_temp, mesh_size, min_size, n_sectors,
                      lo=-50.0, hi=0.0, step=5.0):
    """Find the load angle γ that MAXIMISES torque (MTPA) for ONE geometry at a
    reference current — a coarse PARALLEL sweep + parabolic refine.  Run once
    before the geometry search so the whole optimization uses the best phase."""
    from concurrent.futures import ThreadPoolExecutor
    cand = [round(lo + i * step, 1) for i in range(int(round((hi - lo) / step)) + 1)]

    def _one(gc):
        o = _subprocess_eval(geom, ref_I, steps, coil_temp, n_periods=1.0,
                             gamma_deg=float(gc), mesh_size_mm=mesh_size,
                             min_size_mm=min_size, n_sectors=n_sectors)
        return (float(gc), float(o["res"].get("T_em_Nm", 0.0) or 0.0)) if o.get("ok") else None

    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
        pts = [p for p in ex.map(_one, cand) if p]
    if not pts:
        return None
    pts.sort(key=lambda p: p[0])
    bi = max(range(len(pts)), key=lambda i: pts[i][1])
    gb = pts[bi][0]
    if 0 < bi < len(pts) - 1:                       # parabolic refine around the peak
        (_, y0), (_, y1), (_, y2) = pts[bi - 1], pts[bi], pts[bi + 1]
        denom = (y0 - 2.0 * y1 + y2)
        if abs(denom) > 1e-9:
            h = pts[bi][0] - pts[bi - 1][0]
            gb = pts[bi][0] + 0.5 * h * (y0 - y2) / denom
    return round(min(hi, max(lo, float(gb))), 1)


def _descent_worker(var_specs, op, ripple_max, w_eff, w_td, lam,
                    steps, coil_temp, mesh_size, min_size, max_iters, run_id,
                    n_sectors=-1, v_peak_limit=1e9, target_torque=0.0,
                    optimize_gamma=True) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        cfg = get_config()
        geo0 = dict(cfg.get("geometry", {}))
        I   = float(op.get("current_a", 85.0))
        g   = float(op.get("gamma_deg", 0.0))
        _spec_by = {v["name"]: v for v in var_specs}

        def _fit(name, val):
            """Clamp into bounds; round integer-typed variables."""
            v = _spec_by[name]
            val = min(v["hi"], max(v["lo"], val))
            return round(val) if v.get("is_int") else val

        # Start at the current config value for each variable, clamped to bounds.
        x = {}
        for v in var_specs:
            x[v["name"]] = _fit(v["name"], float(geo0.get(v["name"], v["lo"])))

        _torque_tol = 0.02       # probe within ±2 % of target → accept, no 2nd solve
        warm = [I]               # warm-start probe current (last geometry's rated current)

        def _eval_at(d, cur):
            return _subprocess_eval(d, cur, steps, coil_temp, n_periods=1.0,
                                    gamma_deg=g, mesh_size_mm=mesh_size,
                                    min_size_mm=min_size, n_sectors=n_sectors)

        def evalx(xx):
            # Target-torque: probe at the WARM-START current (the last geometry's
            # rated current).  Since CMA-ES candidates are similar, that probe almost
            # always lands inside the ±2 % torque band → ONE solve.  Only on a miss
            # do we rescale (T≈linear in I) and solve again, then warm-start the next.
            if target_torque and target_torque > 0:
                probe = warm[0]                      # snapshot ONCE: parallel evals share warm[],
                o1 = _eval_at(xx, probe)             # so T1 and I2 MUST use the same current, else
                if not o1.get("ok"):                 # the rescale lands at the wrong torque (race).
                    return o1
                T1 = float(o1["res"].get("T_em_Nm", 0.0) or 0.0)
                if T1 <= 1e-6:
                    return o1
                if abs(T1 - target_torque) <= _torque_tol * target_torque:
                    warm[0] = probe                  # this current works → next warm-start
                    return o1                        # already in band → skip 2nd solve
                I2 = min(400.0, max(2.0, probe * target_torque / T1))
                warm[0] = I2                          # warm-start the next probe
                return _eval_at(xx, I2)
            return _eval_at(xx, I)

        # MTPA: find the best load angle γ for the starting geometry FIRST, then
        # run the whole geometry search at that phase.
        if optimize_gamma:
            with _descent_lock:
                _descent_state["phase"] = "mtpa"
            _save_descent_state()
            _gm = _mtpa_gamma_sweep(x, I, steps, coil_temp, mesh_size, min_size, n_sectors)
            if _gm is not None:
                g = _gm
                with _descent_lock:
                    _descent_state["mtpa_gamma_deg"] = g

        with _descent_lock:
            _descent_state["phase"] = "baseline"
        _save_descent_state()
        n_evals = 0
        b = evalx(x); n_evals += 1
        if not b.get("ok"):
            with _descent_lock:
                _descent_state.update(error=f"baseline eval failed: {b.get('error')}")
            return
        base = b["res"]
        cost0, F0 = _descent_cost(base, base, ripple_max, w_eff, w_td, lam, v_peak_limit)
        best = {"x": dict(x), "metrics": base, "cost": cost0, "F": F0}   # descent iterate
        # best_seen = the GLOBALLY lowest-cost design over ALL evaluations (not just
        # accepted iterates) — this is what the ⭐/Apply report, so a great gradient
        # probe is never thrown away.
        best_seen = {"x": dict(x), "metrics": base, "cost": cost0, "F": F0}

        def _consider(xx, out):
            if out and out.get("ok"):
                c, Fv = _descent_cost(out["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                if c < best_seen["cost"] - 1e-9:
                    best_seen.update(x=dict(xx), metrics=out["res"], cost=c, F=Fv)

        def _best_state():
            return {"metrics": _msum(best_seen["metrics"]), "cost": round(best_seen["cost"], 5),
                    "F": round(best_seen["F"], 5), "x": dict(best_seen["x"])}

        history = [{"iter": 0, **_msum(base), "cost": round(cost0, 5), "F": round(F0, 5),
                    "x": {k: round(float(v), 4) for k, v in x.items()}}]
        all_pts = [p for p in [_pt(b, "baseline")] if p]   # every eval → objective-space point
        with _descent_lock:
            _descent_state.update(running=True, iter=0, max_iters=max_iters, phase="optimizing",
                                  n_evals=n_evals, baseline=_msum(base),
                                  best=_best_state(),
                                  current=_msum(base), history=list(history), error=None,
                                  grad={}, points=list(all_pts),
                                  variables=[{"name": v["name"], "lo": v["lo"],
                                              "hi": v["hi"], "step": v["step"]}
                                             for v in var_specs])

        lr = 3.0                       # step multiplier (in units of each var's step)
        for it in range(1, int(max_iters) + 1):
            with _descent_lock:
                if _descent_state["cancel"]:
                    break

            # ── central finite-difference gradient (parallel) ───────────────
            grad: Dict[str, float] = {}
            futs = {}
            with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
                for v in var_specs:
                    for sign in (+1, -1):
                        xx = dict(best["x"])
                        xx[v["name"]] = _fit(v["name"], xx[v["name"]] + sign * v["step"])
                        futs[ex.submit(evalx, xx)] = (v["name"], sign, xx)
                outs: Dict[Any, Any] = {}
                for fut in as_completed(futs):
                    nm, sg, pxx = futs[fut]
                    out = fut.result()
                    outs[(nm, sg)] = out
                    n_evals += 1
                    _consider(pxx, out)                   # track global best
            for o in outs.values():                       # log gradient probes
                p = _pt(o, "grad")
                if p:
                    all_pts.append(p)
            with _descent_lock:
                _descent_state["n_evals"] = n_evals
                _descent_state["points"] = list(all_pts[-1200:])
                _descent_state["best"] = _best_state()
                if _descent_state["cancel"]:
                    break

            for v in var_specs:
                op_p = outs.get((v["name"], +1)); op_m = outs.get((v["name"], -1))
                if op_p and op_p.get("ok") and op_m and op_m.get("ok"):
                    c_p, _ = _descent_cost(op_p["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                    c_m, _ = _descent_cost(op_m["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                    grad[v["name"]] = (c_p - c_m) / 2.0
                else:
                    grad[v["name"]] = 0.0

            with _descent_lock:
                _descent_state["grad"] = {k: round(float(gv), 6) for k, gv in grad.items()}

            gmax = max((abs(gv) for gv in grad.values()), default=0.0)
            if gmax < 1e-9:
                break                  # flat → converged

            # ── backtracking line search downhill along the unit gradient ───
            improved = False
            for trial in range(6):
                step_mult = lr * (0.5 ** trial)
                xx = dict(best["x"])
                for v in var_specs:
                    d = -grad[v["name"]] / gmax           # normalised component
                    xx[v["name"]] = _fit(v["name"],
                                         best["x"][v["name"]] + d * step_mult * v["step"])
                out = evalx(xx); n_evals += 1
                _consider(xx, out)                        # track global best
                _lp = _pt(out, "line")
                if _lp:
                    all_pts.append(_lp)
                with _descent_lock:
                    _descent_state["n_evals"] = n_evals
                    _descent_state["points"] = list(all_pts[-1200:])
                    _descent_state["best"] = _best_state()
                if out.get("ok"):
                    c_new, F_new = _descent_cost(out["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                    if c_new < best["cost"] - 1e-6:
                        best = {"x": xx, "metrics": out["res"], "cost": c_new, "F": F_new}
                        history.append({"iter": it, **_msum(out["res"]),
                                        "cost": round(c_new, 5), "F": round(F_new, 5),
                                        "x": {k: round(float(v), 4) for k, v in xx.items()}})
                        improved = True
                        with _descent_lock:
                            _descent_state.update(
                                iter=it, n_evals=n_evals, history=list(history),
                                current=_msum(out["res"]), best=_best_state())
                        break
            if not improved:
                lr *= 0.5
                with _descent_lock:
                    _descent_state["iter"] = it
                if lr < 0.05:
                    break              # step too small → converged

        with _descent_lock:
            _descent_state.update(
                result={"best": {"metrics": _msum(best_seen["metrics"]),
                                 "cost": round(best_seen["cost"], 5),
                                 "F": round(best_seen["F"], 5),
                                 "overrides": {k: round(float(val), 4) for k, val in best_seen["x"].items()}},
                        "baseline": _msum(base), "history": list(history),
                        "n_evals": n_evals,
                        "operating_point": op, "ripple_max_pct": ripple_max,
                        "weights": {"w_eff": w_eff, "w_td": w_td, "lambda": lam}},
                best=_best_state())
    except Exception as e:  # noqa: BLE001
        log.exception("descent failed")
        with _descent_lock:
            _descent_state["error"] = str(e)
    finally:
        with _descent_lock:
            _descent_state["running"] = False
            _descent_state["phase"] = "done"
        _save_descent_state()   # persist so the optimization survives a reload/restart


def _cmaes_worker(var_specs, op, ripple_max, w_eff, w_td, lam,
                  steps, coil_temp, mesh_size, min_size, max_iters, run_id,
                  n_sectors=-1, v_peak_limit=1e9, target_torque=0.0,
                  optimize_gamma=True) -> None:
    """Covariance-Matrix-Adaptation Evolution Strategy — derivative-free,
    noise-robust geometry search.  Same penalised cost as the gradient descent
    (−(eff/eff0)^w_eff·(td/td0)^w_td + λ·max(0, ripple−ripple_max)), evaluated on
    a POPULATION per generation (parallel FEM).  Variables are normalised to
    [0,1] so their very different physical scales don't bias the search.  Writes
    the SAME _descent_state the UI already renders (baseline/best/history/points)
    so the existing charts work unchanged."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import numpy as np
    try:
        import cma
    except Exception as e:  # noqa: BLE001
        with _descent_lock:
            _descent_state.update(error=f"cma package not installed: {e}", running=False)
        return
    try:
        cfg = get_config()
        geo0 = dict(cfg.get("geometry", {}))
        I = float(op.get("current_a", 85.0)); g = float(op.get("gamma_deg", 0.0))
        names = [v["name"] for v in var_specs]
        lo = np.array([float(v["lo"]) for v in var_specs])
        hi = np.array([float(v["hi"]) for v in var_specs])
        span = np.maximum(hi - lo, 1e-9)
        is_int = [bool(v.get("is_int")) for v in var_specs]

        def to_geom(xn):
            phys = lo + np.clip(np.asarray(xn, float), 0.0, 1.0) * span
            return {nm: (float(round(phys[i])) if is_int[i] else float(phys[i]))
                    for i, nm in enumerate(names)}

        _torque_tol = 0.02       # probe within ±2 % of target → accept, no 2nd solve
        warm = [I]               # warm-start probe current (last geometry's rated current)

        def _eval_at(dd, cur):
            return _subprocess_eval(dd, cur, steps, coil_temp, n_periods=1.0,
                                    gamma_deg=g, mesh_size_mm=mesh_size,
                                    min_size_mm=min_size, n_sectors=n_sectors)

        def evalx(d):
            # Target-torque with warm-start + ±2 % band (see _descent_worker): the
            # probe at the last geometry's rated current usually lands in band → one
            # solve; only a miss triggers a rescale + 2nd solve.
            if target_torque and target_torque > 0:
                probe = warm[0]                      # snapshot ONCE (parallel evals share warm[])
                o1 = _eval_at(d, probe)
                if not o1.get("ok"):
                    return o1
                T1 = float(o1["res"].get("T_em_Nm", 0.0) or 0.0)
                if T1 <= 1e-6:
                    return o1
                if abs(T1 - target_torque) <= _torque_tol * target_torque:
                    warm[0] = probe
                    return o1
                I2 = min(400.0, max(2.0, probe * target_torque / T1))
                warm[0] = I2
                return _eval_at(d, I2)
            return _eval_at(d, I)

        x0n = np.clip((np.array([float(geo0.get(nm, lo[i])) for i, nm in enumerate(names)]) - lo) / span, 0.0, 1.0)
        # MTPA: optimize the load angle γ for the starting geometry FIRST, then run
        # the whole geometry search at that phase.
        if optimize_gamma:
            with _descent_lock:
                _descent_state["phase"] = "mtpa"
            _save_descent_state()
            _gm = _mtpa_gamma_sweep(to_geom(x0n), I, steps, coil_temp, mesh_size, min_size, n_sectors)
            if _gm is not None:
                g = _gm
                with _descent_lock:
                    _descent_state["mtpa_gamma_deg"] = g
        with _descent_lock:
            _descent_state["phase"] = "baseline"
        _save_descent_state()
        b = evalx(to_geom(x0n)); n_evals = 1
        if not b.get("ok"):
            with _descent_lock:
                _descent_state.update(error=f"baseline eval failed: {b.get('error')}", running=False)
            return
        base = b["res"]
        cost0, F0 = _descent_cost(base, base, ripple_max, w_eff, w_td, lam, v_peak_limit)
        best = {"x": to_geom(x0n), "metrics": base, "cost": cost0, "F": F0}

        def _bstate():
            return {"metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                    "F": round(best["F"], 5), "x": dict(best["x"])}
        def _hrow(it, m, c, F, xd):
            return {"iter": it, **_msum(m), "cost": round(c, 5), "F": round(F, 5),
                    "x": {k: round(float(v), 4) for k, v in xd.items()}}

        history = [_hrow(0, base, cost0, F0, best["x"])]
        all_pts = [p for p in [_pt(b, "baseline")] if p]
        with _descent_lock:
            _descent_state.update(running=True, iter=0, max_iters=max_iters, phase="optimizing", n_evals=n_evals,
                                  baseline=_msum(base), best=_bstate(), current=_msum(base),
                                  history=list(history), error=None, grad={}, points=list(all_pts),
                                  variables=[{"name": v["name"], "lo": v["lo"], "hi": v["hi"],
                                              "step": v["step"]} for v in var_specs])

        es = cma.CMAEvolutionStrategy(list(x0n), 0.25,
                                      {"bounds": [0.0, 1.0], "maxiter": int(max_iters),
                                       "verbose": -9, "seed": 12345})
        it = 0
        while not es.stop():
            with _descent_lock:
                if _descent_state["cancel"]:
                    break
            sols = es.ask()
            outs = [None] * len(sols)
            with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
                futs = {ex.submit(evalx, to_geom(s)): i for i, s in enumerate(sols)}
                for fut in as_completed(futs):
                    outs[futs[fut]] = fut.result()
            n_evals += len(sols)
            costs = []
            for i, out in enumerate(outs):
                if out and out.get("ok"):
                    c, Fv = _descent_cost(out["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                    p = _pt(out, "cmaes")
                    if p:
                        all_pts.append(p)
                    if c < best["cost"] - 1e-9:
                        best = {"x": to_geom(sols[i]), "metrics": out["res"], "cost": c, "F": Fv}
                else:
                    c = 1e6                       # failed eval → repelled
                costs.append(c)
            es.tell(sols, costs)
            it += 1
            history.append(_hrow(it, best["metrics"], best["cost"], best["F"], best["x"]))
            with _descent_lock:
                _descent_state.update(iter=it, n_evals=n_evals, best=_bstate(),
                                      current=_msum(best["metrics"]), history=list(history),
                                      points=list(all_pts))
            _save_descent_state()   # checkpoint each generation (survives a mid-run restart)
        with _descent_lock:
            _descent_state["result"] = {
                "best": {"x": best["x"],
                         "overrides": {k: round(float(v), 4) for k, v in best["x"].items()},
                         "metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                         "F": round(best["F"], 5)},
                "baseline": _msum(base), "history": list(history), "n_evals": n_evals,
                "operating_point": op, "ripple_max_pct": ripple_max,
                "weights": {"w_eff": w_eff, "w_td": w_td, "lambda": lam},
                "algorithm": "cmaes"}
    except Exception as e:  # noqa: BLE001
        log.exception("CMA-ES failed")
        with _descent_lock:
            _descent_state["error"] = str(e)
    finally:
        with _descent_lock:
            _descent_state["running"] = False
            _descent_state["phase"] = "done"
        _save_descent_state()   # persist so the optimization survives a reload/restart


@router.post("/descent/start")
def descent_start(req: DescentRequest):
    """Start a background geometry optimization (fixed current+rpm+γ).
    algorithm='cmaes' (default) → CMA-ES; 'gradient' → finite-diff descent."""
    with _descent_lock:
        if _descent_state["running"]:
            raise HTTPException(status_code=409, detail="a descent is already running")

    cfg = get_config()
    schema = cfg.get("geometry_schema", {})
    wl = cfg.get("sweep_whitelist", None)
    allowed = set(wl or [])

    # Build per-variable bounds + perturbation step.  Use the request bounds when
    # a real range is given, else fall back to the schema min/max; step from the
    # request, else schema step, else 5 % of the range.
    var_specs: List[Dict[str, Any]] = []
    for v in req.variables:
        if allowed and v.name not in allowed:
            continue
        meta = schema.get(v.name, {})
        s_lo = float(meta.get("min", v.min))
        s_hi = float(meta.get("max", v.max))
        lo, hi = (float(v.min), float(v.max)) if float(v.max) > float(v.min) else (s_lo, s_hi)
        # Never let the soft search window exceed the schema's physical min/max:
        # the hard limit is the wall a re-centered (box-walking) ±deviation window
        # stops at, so a boundary-active variable can't walk into non-physical
        # geometry.  A variable that ends pinned AND its window == schema bound is a
        # genuine physical limit (the UI marks it red, no further continue).
        lo, hi = max(lo, s_lo), min(hi, s_hi)
        if hi <= lo:
            continue
        step = float(v.step) if float(v.step) > 0 else float(meta.get("step", 0) or 0)
        if step <= 0:
            step = max(1e-4, 0.05 * (hi - lo))
        is_int = str(meta.get("type", "float")) == "int"
        if is_int:
            step = max(1.0, round(step))
        var_specs.append({"name": v.name, "lo": lo, "hi": hi,
                          "step": step, "is_int": is_int})

    if not var_specs:
        raise HTTPException(status_code=400,
                            detail="no whitelisted variables with a usable range to descend")

    op = {"gamma_deg": float(req.operating_point.gamma_deg),
          "current_a": float(req.operating_point.current_a),
          "rpm": float(req.operating_point.rpm)}
    steps     = max(8, min(int(req.steps_per_period), 180))
    mesh_size = max(1.0, min(float(req.mesh_size_mm), 12.0))
    min_size  = max(0.1, min(float(req.min_size_mm), 3.0))
    max_iters = max(1, min(int(req.max_iters), 40))
    n_sectors = int(req.n_sectors)
    algo      = (req.algorithm or "cmaes").strip().lower()
    worker    = _cmaes_worker if algo == "cmaes" else _descent_worker
    # Rated-duty constraints (off when 0 / huge): target torque + peak-voltage cap.
    v_peak_limit  = float(req.v_peak_limit) if float(req.v_peak_limit) > 0 else 1e9
    target_torque = max(0.0, float(req.target_torque_nm))

    with _descent_lock:
        _descent_state.update({"running": True, "iter": 0, "max_iters": max_iters,
                               "n_evals": 0, "best": None, "current": None,
                               "history": [], "baseline": None, "result": None, "phase": "starting",
                               "run_id": req.run_id, "error": None, "cancel": False})
    threading.Thread(
        target=worker,
        args=(var_specs, op, float(req.ripple_max_pct), float(req.w_eff),
              float(req.w_td), float(req.penalty_lambda), steps,
              float(req.coil_temp_c), mesh_size, min_size, max_iters, req.run_id,
              n_sectors, v_peak_limit, target_torque, bool(req.optimize_gamma)),
        daemon=True).start()
    return {"started": True, "algorithm": algo, "n_sectors": n_sectors,
            "target_torque_nm": target_torque, "v_peak_limit": v_peak_limit,
            "n_variables": len(var_specs), "max_iters": max_iters,
            "variables": [s["name"] for s in var_specs], "steps_per_period": steps}


@router.get("/descent/progress")
def descent_progress():
    with _descent_lock:
        return dict(_descent_state)


@router.post("/descent/cancel")
def descent_cancel():
    with _descent_lock:
        _descent_state["cancel"] = True
    return {"cancelled": True}


# ─────────────────────────────────────────────────────────────────────────────
# SAVED RESULTS — persist a scan result to disk so it survives restarts; list,
# load and delete them.  Stored as plain JSON files under optimization_saves/.
# ─────────────────────────────────────────────────────────────────────────────
_SAVE_DIR = Path(__file__).resolve().parents[3] / "optimization_saves"
_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


class SaveRequest(BaseModel):
    name: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)   # variables/operating/steps


def _safe(sid: str) -> Path:
    if not _ID_RE.match(sid or ""):
        raise HTTPException(status_code=400, detail="bad id")
    return _SAVE_DIR / f"{sid}.json"


@router.post("/saved")
def save_result(req: SaveRequest):
    """Persist a scan result to disk; returns its id."""
    if not req.result:
        raise HTTPException(status_code=400, detail="no result to save")
    _SAVE_DIR.mkdir(exist_ok=True)
    ts = datetime.now()
    sid = ts.strftime("%Y%m%d_%H%M%S_%f")
    name = (req.name or "").strip() or f"scan {ts.strftime('%Y-%m-%d %H:%M')}"
    entry = {"id": sid, "name": name, "created_at": ts.isoformat(timespec="seconds"),
             "config": req.config, "result": req.result}
    _safe(sid).write_text(json.dumps(entry), encoding="utf-8")
    return {"id": sid, "name": name, "created_at": entry["created_at"]}


@router.get("/saved")
def list_saved():
    """List saved results (metadata only), newest first."""
    _SAVE_DIR.mkdir(exist_ok=True)
    out = []
    for f in sorted(_SAVE_DIR.glob("*.json"), reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            r = d.get("result", {})
            cfg = d.get("config", {})
            out.append({
                "id": d["id"], "name": d.get("name", d["id"]),
                "created_at": d.get("created_at", ""),
                "n_geometries": r.get("n_geometries"),
                "n_points": r.get("n_total_points"),
                "n_built": r.get("n_built"),
                "n_front": len(r.get("pareto_indices", [])),
                "steps_per_period": cfg.get("steps_per_period") or r.get("steps_per_period"),
                "variables": [v.get("name") for v in (r.get("variables") or [])],
            })
        except Exception:  # noqa: BLE001
            continue
    return {"saved": out}


@router.get("/saved/{sid}")
def get_saved(sid: str):
    """Load a saved result (full data)."""
    f = _safe(sid)
    if not f.exists():
        raise HTTPException(status_code=404, detail="not found")
    return json.loads(f.read_text(encoding="utf-8"))


@router.delete("/saved/{sid}")
def delete_saved(sid: str):
    f = _safe(sid)
    if f.exists():
        f.unlink()
    return {"deleted": sid}
