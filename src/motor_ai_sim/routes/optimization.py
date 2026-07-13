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

import hashlib
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
    "run_id": "", "error": None, "cancel": False, "cached": 0,
}
_scan_lock = threading.Lock()


def _scan_store_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", ".last_scan.json"))


def _save_last_scan(result: Dict[str, Any]) -> None:
    """Persist the last completed sweep so its chart survives a reload / restart."""
    try:
        tmp = _scan_store_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(result, fh, default=float)
        os.replace(tmp, _scan_store_path())
    except Exception as _e:  # noqa: BLE001
        log.warning("could not persist last scan: %s", _e)


def _load_last_scan() -> None:
    try:
        p = _scan_store_path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                _scan_state["result"] = json.load(fh)
            log.info("restored last scan from %s", p)
    except Exception as _e:  # noqa: BLE001
        log.warning("could not restore last scan: %s", _e)


_load_last_scan()   # repopulate the last sweep at startup (survives backend restart)


# ── Persistent FEM eval cache ────────────────────────────────────────────────
# A sweep point is a deterministic function of its inputs, so once computed it
# never needs recomputing.  We key each result by (eval inputs + a fingerprint
# of the physics config refine_proc reads) and persist it, so re-running a sweep
# after widening one variable's range REUSES the points already computed and only
# evaluates the genuinely new ones.  The config fingerprint deliberately EXCLUDES
# the operating-point fields (max_current / phase_offset) — the scan passes
# current & γ explicitly, so applying a design must NOT invalidate the cache.
_EVAL_CACHE: Dict[str, Dict[str, Any]] = {}
_eval_cache_lock = threading.Lock()


def _eval_cache_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", ".scan_cache.jsonl"))


def _config_fingerprint() -> str:
    """Hash the physics config a FEM eval depends on (geometry baseline, winding,
    materials, magnet, rpm) so a Geometry / Materials / speed edit invalidates the
    cache — but an operating-point change (current/γ, passed per-eval) does not."""
    try:
        cfg = get_config()
        sim = {k: v for k, v in (cfg.get("simulation") or {}).items()
               if k not in ("max_current", "phase_offset_deg", "current_a", "gamma_deg", "gamma")}
        phys = {"geometry": cfg.get("geometry"), "winding": cfg.get("winding"),
                "materials": cfg.get("materials"), "magnet": cfg.get("magnet"),
                "rotor": cfg.get("rotor"), "stator": cfg.get("stator"), "sim": sim}
        return hashlib.md5(json.dumps(phys, sort_keys=True, default=str).encode()).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "nofp"


def _eval_cache_key(overrides: Dict[str, float], current_a: float, steps: int,
                    coil_temp_c: float, n_periods: float, gamma_deg: float,
                    mesh_size_mm: float, min_size_mm: float, n_sectors: int,
                    pole_copy, torque_filter: bool, cfg_fp: str,
                    gap_layers: float = 3.0, end_winding_factor: float = 0.0,
                    rotor_eddy: bool = False, hi_fidelity: bool = False) -> str:
    payload = {
        "ov": {k: round(float(v), 6) for k, v in sorted(overrides.items())},
        "I": round(float(current_a), 4), "steps": int(steps),
        "ct": round(float(coil_temp_c), 2), "np": round(float(n_periods), 5),
        "g": round(float(gamma_deg), 4), "ms": round(float(mesh_size_mm), 4),
        "mn": round(float(min_size_mm), 4), "ns": int(n_sectors),
        "pc": pole_copy, "tf": bool(torque_filter), "cfg": cfg_fp,
        "gl": round(float(gap_layers), 2), "ew": round(float(end_winding_factor), 3),
        "re": bool(rotor_eddy), "hf": bool(hi_fidelity),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _eval_healthy(out: Dict[str, Any]) -> bool:
    """True only for an eval whose FEM actually produced numbers.  A worker that
    dies mid-solve (or a solver that silently returns an empty field) can emit
    ok=true with NaN torque — caching that poisons every re-run of the sweep
    with instantly-"done" empty points, so gate both store AND load on it."""
    try:
        r = out.get("res") if isinstance(out.get("res"), dict) else out
        t = r.get("T_em_Nm")
        return isinstance(t, (int, float)) and math.isfinite(float(t))
    except Exception:  # noqa: BLE001
        return False


def _load_eval_cache() -> None:
    try:
        p = _eval_cache_path()
        if os.path.exists(p):
            n_bad = 0
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if not _eval_healthy(rec["v"]):
                            n_bad += 1
                            continue
                        _EVAL_CACHE[rec["k"]] = rec["v"]   # later lines win (re-computed)
                    except Exception:  # noqa: BLE001
                        pass
            log.info("loaded %d cached FEM evals from %s (skipped %d unhealthy)",
                     len(_EVAL_CACHE), p, n_bad)
    except Exception as _e:  # noqa: BLE001
        log.warning("could not load eval cache: %s", _e)


_RES_KEYS = ("T_em_Nm", "efficiency", "torque_per_mass_Nm_kg", "T_ripple_pct",
             "P_loss_total_W", "P_cu_W", "P_cu_dc_W", "P_cu_ac_W", "P_fe_W",
             "P_mag_W", "P_shaft_W", "mass_total_kg", "V_peak",
             "V1_phase_V", "THD_pct", "THD_LL_pct", "Kt_Nm_per_Arms")


def _store_eval(key: str, res: Dict[str, Any]) -> None:
    if not _eval_healthy(res):   # never cache a NaN/empty eval (see _eval_healthy)
        return
    with _eval_cache_lock:
        if key in _EVAL_CACHE:   # same inputs already cached → no duplicate file write
            return
        _EVAL_CACHE[key] = res
        try:
            with open(_eval_cache_path(), "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"k": key, "v": res}, default=float) + "\n")
        except Exception as _e:  # noqa: BLE001
            log.warning("could not persist eval: %s", _e)


_load_eval_cache()   # warm the cache from disk so it survives a backend restart
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
                     min_size_mm: float = 0.3, n_sectors: int = -1,
                     _log: bool = True, pole_copy=None, torque_filter=True,
                     gap_layers: float = 3.0, end_winding_factor: float = 0.0,
                     rotor_eddy: bool = False, hi_fidelity: bool = False) -> Dict[str, Any]:
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
                       "n_sectors": int(n_sectors), "pole_copy": pole_copy,
                       "torque_filter": bool(torque_filter),
                       "gap_layers": float(gap_layers),
                       "end_winding_factor": float(end_winding_factor),
                       "rotor_eddy": bool(rotor_eddy),
                       "hi_fidelity": bool(hi_fidelity)})
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "motor_ai_sim.optimization.refine_proc"],
            input=spec, capture_output=True, text=True, timeout=900)
        out = proc.stdout or ""
        m = out.rfind("@@RESULT@@")
        if m >= 0:
            _res = json.loads(out[m + len("@@RESULT@@"):])
            if _log:
                _log_eval(overrides, current_a, gamma_deg, _res)   # accumulate surrogate dataset
            return _res
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
        return _json_sane(dict(_refine_state))


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
    pole_copy: Optional[bool] = None       # mesh mode (Mesh tab "Periodic")
    torque_filter: bool = True             # band-limit ripple — Simulation's toggle
    n_sectors: int = 1                     # FEM symmetry — SINGLE SOURCE: Mesh tab (same build as Simulation)
    gap_layers: float = 3.0                # air-gap mesh layers — SINGLE SOURCE: Mesh tab (drives ripple/eddy; match Simulation)
    end_winding_factor: float = 0.0        # end-winding k_end — SINGLE SOURCE: Simulation (drives copper loss / eff; 0 = auto)
    rotor_eddy: bool = False                # field-based magnet/shaft eddy — SINGLE SOURCE: Simulation (drives magnet loss / eff vs slab estimate)
    hi_fidelity: bool = False               # 2× slip nodes + finer mesh + ≥4 gap layers — SINGLE SOURCE: Mesh tab (smoother raw torque, ~3-5× slower)
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
                 max_geom, seed, run_id, mesh_size_mm=4.0, min_size_mm=0.3,
                 pole_copy=None, torque_filter=True, n_sectors=1, gap_layers=3.0,
                 end_winding=0.0, rotor_eddy=False, hi_fidelity=False) -> None:
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
        _cfg_fp = _config_fingerprint()   # constant for this scan → one fingerprint for every point

        def _do(i_t):
            i, (gi, oi, ov, I, opg) = i_t
            # γ as a swept variable overrides the operating-point γ; it is NOT a
            # geometry key, so split it out before building the mesh.
            g = float(ov.get("gamma_deg", opg))
            geo_ov = {k: v for k, v in ov.items() if k != "gamma_deg"}
            # Reuse a previously-computed identical point (skip the ~1-min FEM) — this
            # is what lets a re-run after widening a range only evaluate the new points.
            ck = _eval_cache_key(geo_ov, I, steps, coil_temp_c, _NPER, g,
                                 mesh_size_mm, min_size_mm, n_sectors, pole_copy, torque_filter, _cfg_fp,
                                 gap_layers, end_winding, rotor_eddy, hi_fidelity)
            out = _EVAL_CACHE.get(ck)
            if out is not None:
                with _scan_lock:
                    _scan_state["cached"] = _scan_state.get("cached", 0) + 1
            else:
                out = _subprocess_eval(geo_ov, I, steps, coil_temp_c,
                                       n_periods=_NPER, gamma_deg=g,
                                       mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                                       pole_copy=pole_copy, torque_filter=torque_filter,
                                       n_sectors=n_sectors, gap_layers=gap_layers,
                                       end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                       hi_fidelity=hi_fidelity)
                if out and out.get("ok"):
                    _store_eval(ck, out)   # cache successful evals only (skip transient crashes)
            pt = _point_from_eval(out, ov, I, gi, oi, ripple_max)
            pt["gamma_deg"] = g    # stamp γ so the chart can group/connect without the request
            return i, pt

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
                    # live-stream the points computed so far so the chart fills in
                    # as the sweep runs (not only at the end).
                    _scan_state["points"] = [p for p in points if p is not None]
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
            _bI = float(operating_points[0].get("current_a", 85.0))
            _bck = _eval_cache_key({}, _bI, steps, coil_temp_c, _NPER, 0.0,
                                   mesh_size_mm, min_size_mm, n_sectors, pole_copy, torque_filter, _cfg_fp,
                                   gap_layers, end_winding, rotor_eddy, hi_fidelity)
            base_out = _EVAL_CACHE.get(_bck)
            if base_out is None:
                base_out = _subprocess_eval({}, _bI, steps, coil_temp_c, n_periods=_NPER,
                                            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                                            pole_copy=pole_copy, torque_filter=torque_filter,
                                            n_sectors=n_sectors, gap_layers=gap_layers,
                                            end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                            hi_fidelity=hi_fidelity)
                if base_out and base_out.get("ok"):
                    _store_eval(_bck, base_out)
            baseline = _point_from_eval(base_out, {}, _bI, -1, 0, ripple_max)
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
            # solver params for this scan → lets the cache be re-seeded from this
            # result later with the exact same key inputs (see /scan/seed_cache).
            "scan_params": {"coil_temp_c": float(coil_temp_c), "mesh_size_mm": float(mesh_size_mm),
                            "min_size_mm": float(min_size_mm), "pole_copy": pole_copy,
                            "torque_filter": bool(torque_filter)},
        }
        with _scan_lock:
            _scan_state["result"] = result
        _save_last_scan(result)   # persist so it survives reload / restart
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
        # FEM symmetry — single source: the Mesh tab (same build the Simulation uses),
        # so the sweep's ripple matches the Simulation for an identical geometry.
        # "Full"(1) MUST mean the full ring (-1), exactly like the Simulation transient
        # route (simulation.py: n_sectors ≤1 → -1).  Passing raw 1 made the solver build
        # an invalid NS=4 wedge (90°) — broken for any motor whose pole count is not a
        # multiple of 4 (e.g. 14 poles → 3.5/sector) → spurious tooth-width torque slope
        # + scattered ripple.  This was the sweep-vs-Simulation mismatch vs ANSYS.
        n_sectors = -1 if int(req.n_sectors) <= 1 else int(req.n_sectors)
        # Air-gap mesh layers — single source: the Mesh tab.  gap_layers drives the
        # air-gap field resolution → torque ripple + magnet eddy; the Simulation uses
        # mesh.gapLayers, so the sweep must too or its numbers won't reproduce in Sim.
        gap_layers = max(1.0, min(float(req.gap_layers), 8.0))
        # End-winding k_end — single source: the Simulation tab.  Drives copper loss
        # → efficiency; the Simulation sends its value, so the sweep must too.
        end_winding = max(0.0, float(req.end_winding_factor))
        # Field-based rotor eddy — single source: the Simulation (fieldLosses toggle).
        # Slab vs field magnet-eddy differs ~3×, so the sweep must use the same model.
        rotor_eddy = bool(req.rotor_eddy)
        # Hi-fidelity torque — single source: the Mesh tab toggle.  2× slip-ring nodes +
        # finer angular mesh + ≥4 gap layers → less per-mesh mean-torque scatter (smoother
        # tooth-width trend), at ~3-5× the runtime.  Same bundle the Simulation tab uses.
        hi_fidelity = bool(req.hi_fidelity)
        _scan_state.update({"running": True, "done": 0, "total": 0, "result": None,
                            "points": [], "run_id": req.run_id, "error": None, "cancel": False,
                            "cached": 0})
    threading.Thread(target=_scan_worker,
                     args=(variables, ops, steps, float(req.coil_temp_c),
                           float(req.ripple_max_pct), max_geom, int(req.seed), req.run_id,
                           mesh_size, min_size, req.pole_copy, bool(req.torque_filter),
                           n_sectors, gap_layers, end_winding, rotor_eddy, hi_fidelity),
                     daemon=True).start()
    return {"started": True, "steps_per_period": steps, "max_geometries": max_geom,
            "mesh_size_mm": mesh_size, "min_size_mm": min_size}


def _json_sane(obj):
    """Replace NaN/Inf floats with None recursively — FastAPI's strict JSON
    encoder 500s on them ('Out of range float values are not JSON compliant'),
    which blinded the whole Sweep progress panel when ONE eval produced a NaN."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_sane(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_sane(v) for v in obj]
    return obj


@router.get("/scan/progress")
def scan_progress():
    with _scan_lock:
        return _json_sane(dict(_scan_state))


@router.post("/scan/cancel")
def scan_cancel():
    with _scan_lock:
        _scan_state["cancel"] = True
    return {"cancelled": True}


class SeedCacheRequest(BaseModel):
    # Only used for OLD results that predate scan_params storage; new results carry
    # their own params (which take precedence) so the keys match exactly.
    coil_temp_c: float = 120.0
    mesh_size_mm: float = 4.0
    min_size_mm: float = 0.3
    pole_copy: Optional[bool] = None
    torque_filter: bool = True


@router.post("/scan/seed_cache")
def seed_cache(req: SeedCacheRequest):
    """Seed the eval cache from the LAST completed sweep so a re-run (e.g. after
    widening a variable's range) reuses those points instead of recomputing them.
    Each key is rebuilt exactly as the scan worker would — the scan's own solver
    params (stored on the result; request used only as a fallback for old results)
    plus the CURRENT config fingerprint."""
    with _scan_lock:
        result = _scan_state.get("result")
    if not result or not isinstance(result.get("points"), list) or not result["points"]:
        return {"seeded": 0, "error": "no completed sweep to seed from"}
    steps = int(result.get("steps_per_period", 60))
    sp = result.get("scan_params") or {}
    coil = float(sp.get("coil_temp_c", req.coil_temp_c))
    mesh = float(sp.get("mesh_size_mm", req.mesh_size_mm))
    mn = float(sp.get("min_size_mm", req.min_size_mm))
    pc = sp.get("pole_copy", req.pole_copy)
    tf = bool(sp.get("torque_filter", req.torque_filter))
    fp = _config_fingerprint()
    seeded = 0
    for p in result["points"]:
        if not p.get("feasible") or p.get("current_a") is None or p.get("gamma_deg") is None:
            continue
        geo_ov = {k: v for k, v in (p.get("overrides") or {}).items() if k != "gamma_deg"}
        res = {k: p[k] for k in _RES_KEYS if k in p}
        key = _eval_cache_key(geo_ov, float(p["current_a"]), steps, coil, 1.0,
                              float(p["gamma_deg"]), mesh, mn, -1, pc, tf, fp)
        before = len(_EVAL_CACHE)
        _store_eval(key, {"ok": True, "res": res})
        seeded += int(len(_EVAL_CACHE) > before)
    return {"seeded": seeded, "cache_size": len(_EVAL_CACHE), "steps": steps,
            "params": {"coil": coil, "mesh": mesh, "min": mn, "pole_copy": pc, "torque_filter": tf}}


@router.post("/scan/clear_cache")
def clear_cache():
    """Wipe the persistent FEM eval cache (memory + .scan_cache.jsonl) so the NEXT
    sweep recomputes EVERY point from scratch.  The cache normally makes re-running
    the same grid instant (deterministic inputs → same outputs); this is the explicit
    "give me fresh numbers" escape hatch for when the inputs are identical but the
    solver code changed (the key can't see code edits).  Wired to the sweep panel's
    "Clear result" button, so Clear → Run always recomputes."""
    with _eval_cache_lock:
        n = len(_EVAL_CACHE)
        _EVAL_CACHE.clear()
        removed = False
        try:
            p = _eval_cache_path()
            if os.path.exists(p):
                os.remove(p); removed = True
        except Exception as _e:   # noqa: BLE001
            log.warning("could not delete eval cache file: %s", _e)
    log.info("eval cache cleared: %d entries dropped (file removed=%s)", n, removed)
    return {"cleared": n, "file_removed": removed, "cache_size": len(_EVAL_CACHE)}


# ─────────────────────────────────────────────────────────────────────────────
# DOE SCREENING — Latin-Hypercube sample the design box at a FIXED current,
# FEM-evaluate each, and compute UNBIASED global variable importance (which knobs
# move ripple / torque / efficiency).  Wraps motor_ai_sim.optimization.doe (was
# CLI-only) in a background job so the UI can launch it + show the importance.
# ─────────────────────────────────────────────────────────────────────────────
_doe_lock = threading.Lock()
_doe_state: Dict[str, Any] = {"running": False, "done": 0, "total": 0,
                              "importance": None, "error": None, "n_ok": 0}


class DoeRequest(BaseModel):
    n: int = 60                  # LHS sample count
    current_a: float = 150.0     # fixed current (torque varies → modelable)
    band: float = 0.3            # ± fraction of baseline per variable
    steps_per_period: int = 18
    n_sectors: int = -1          # -1 = full disk (accurate ripple)
    pole_copy: Optional[bool] = None      # mesh mode (Mesh tab "Periodic")
    torque_filter: bool = True            # band-limit ripple — Simulation's toggle


@router.post("/doe/start")
def doe_start(req: DoeRequest):
    with _doe_lock:
        if _doe_state["running"]:
            raise HTTPException(status_code=409, detail="a DOE is already running")
        _doe_state.update({"running": True, "done": 0, "total": int(req.n),
                           "importance": None, "error": None, "n_ok": 0})

    def _worker():
        import re as _re
        try:
            from motor_ai_sim.optimization import doe as _doe, surrogate as _S

            def _log(msg):   # parse "  DOE 5/60  (ok 4)" → live progress
                m = _re.search(r"DOE (\d+)/(\d+)\s+\(ok (\d+)\)", str(msg))
                if m:
                    with _doe_lock:
                        _doe_state["done"] = int(m.group(1))
                        _doe_state["total"] = int(m.group(2))
                        _doe_state["n_ok"] = int(m.group(3))

            recs = _doe.run_doe(n=int(req.n), current_a=float(req.current_a),
                                band=float(req.band), n_sectors=int(req.n_sectors),
                                steps=int(req.steps_per_period),
                                pole_copy=req.pole_copy, torque_filter=bool(req.torque_filter),
                                log=_log)
            vi = _S.variable_importance(recs, min_samples=15, filter_op=False)
            with _doe_lock:
                _doe_state["importance"] = vi
                _doe_state["n_ok"] = len(recs)
                _doe_state["running"] = False
        except Exception as e:  # noqa: BLE001
            with _doe_lock:
                _doe_state["error"] = str(e)
                _doe_state["running"] = False

    threading.Thread(target=_worker, daemon=True).start()
    return {"started": True, "n": int(req.n)}


@router.get("/doe/progress")
def doe_progress():
    with _doe_lock:
        return dict(_doe_state)


# ─────────────────────────────────────────────────────────────────────────────
# GRADIENT / COORDINATE DESCENT — fixed current+rpm, vary the whitelisted
# geometry knobs.  At each iteration every variable is perturbed ±step (central
# finite difference, evaluated in parallel) to estimate ∂cost/∂var; we then step
# downhill along the normalised gradient with a backtracking line search.
#
#   F    = (efficiency/eff0)^w_eff · (torque_per_mass/td0)^w_td   (both maximised)
#   cost = −F + λ·max(0, V_peak − V_limit)                        (voltage penalty)
#
# Ripple is NOT in the objective — the optimizer purely maximises efficiency ×
# torque-density (2 criteria); ripple is filtered post-hoc on the results chart
# (the on-chart "ripple ≤ X%" slider).  The only feasibility penalty left is the
# inverter voltage budget — a design the DC bus physically can't drive at the
# operating point is repelled.  Every evaluation is a real sliding-band FEM
# transient in an isolated subprocess.
# ─────────────────────────────────────────────────────────────────────────────
_descent_state: Dict[str, Any] = {
    "running": False, "iter": 0, "max_iters": 0, "n_evals": 0,
    "best": None, "current": None, "history": [], "baseline": None,
    "baseline_line": None,
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


import threading as _threading_o
_dataset_lock = _threading_o.Lock()


def _dataset_path() -> str:
    """JSONL of every FEM eval (geometry overrides + metrics) — the training data
    for the surrogate / variable-importance model.  Accumulates across ALL runs so
    future optimizations can be warm-started / Bayesian-guided from it."""
    return _descent_store_path().replace(".last_descent.json", ".opt_dataset.jsonl")


def _log_eval(overrides, current_a, gamma_deg, result) -> None:
    """Append one FEM evaluation to the optimization dataset.  Thread-safe,
    best-effort (never breaks an eval).  Called for every _subprocess_eval."""
    try:
        if not (result and result.get("ok")):
            return
        r = result.get("res") or {}
        rec = {
            "overrides": overrides, "current_a": current_a, "gamma_deg": gamma_deg,
            "ripple": r.get("T_ripple_pct"), "torque": r.get("T_em_Nm"),
            "eff": r.get("efficiency"), "td": r.get("torque_per_mass_Nm_kg"),
            "mass": r.get("mass_total_kg"), "v_peak": r.get("V_peak"),
            "p_loss": r.get("P_loss_total_W"),
            # Waveform quality (CIANO FOC spec) — lets the surrogate screen THD.
            "thd_ll": r.get("THD_LL_pct"), "kt": r.get("Kt_Nm_per_Arms"),
        }
        import json as _json_o
        with _dataset_lock:
            with open(_dataset_path(), "a", encoding="utf-8") as f:
                f.write(_json_o.dumps(rec, default=float) + "\n")
    except Exception:
        pass


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
    # Air-gap mesh layers — SINGLE SOURCE: the Mesh tab. The dominant driver of the
    # Arkkio torque + ripple; MUST match Simulation or a selected design won't
    # reproduce in the Simulation tab.
    gap_layers: float = 2.0
    # Pole/slot mesh mode from the UI (Mesh tab "Periodic (identical poles)").
    # None = solver env default; the optimizer must mesh the SAME way Simulation does.
    pole_copy: Optional[bool] = None
    # Band-limit T(t) to the physical 6·k orders — from Simulation's torque-filter toggle.
    torque_filter: bool = True
    # Loss model — SINGLE SOURCE: Simulation, so the optimizer's efficiency matches
    # the Simulation tab exactly. Without these the eval drops the field-based magnet/
    # shaft eddy loss and the end-winding copper → η reads several points too high.
    rotor_eddy: bool = True             # field-based magnet/shaft eddy (vs slab estimate)
    end_winding_factor: float = 0.0     # k_end end-winding copper (0 = solver auto)
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
    # Surrogate (Bayesian) warm-start: seed the search from the best geometry the
    # RandomForest surrogate predicts over ALL accumulated evals (config/.opt_dataset.jsonl).
    # Falls back to the current geometry if there isn't enough data yet (<20 evals).
    surrogate_seed: bool = False
    # Box-walking: when a variable ends pinned at a window edge, re-centre the
    # ±deviation window on the optimum and re-run, server-side, until everything
    # settles inside / hits a physical limit / max_rounds.  Fully unattended.
    # Default ON (per Vadim 2026-07-02): a variable pinned at its window edge
    # auto-extends the window in that direction and keeps optimizing — the run
    # never stalls just because the user's initial range was too narrow.
    auto_expand: bool = True
    max_rounds: int = 5
    # >0 → the cost actively penalises T_ripple_pct above ripple_max_pct
    # (λ_r·overshoot/100).  0 (default) keeps the old behaviour: ripple is only
    # a post-hoc chart gate, never felt by the optimizer.
    ripple_penalty_lambda: float = 0.0
    # >0 → the cost penalises THD_LL_pct (line-to-line voltage THD, non-triplen
    # harmonics — what a wye-connected FOC drive fights) above thd_max_pct, same
    # λ·overshoot/100 scale as the ripple penalty.  CIANO spec: THD_LL < 5 % for
    # the FOC variant.  0 (default) = THD is reported but not constrained.
    thd_penalty_lambda: float = 0.0
    thd_max_pct: float = 5.0
    boundary_margin: float = 0.05
    run_id: str = ""
    # ── Objective mode ──────────────────────────────────────────────────────
    #  'baseline_line' (default): maximise the signed PERPENDICULAR DISTANCE above
    #    the "current-only" baseline line.  Two FEM sims of the START geometry — at
    #    current I and I·(1+current_bump_pct/100) — give points A and B in
    #    (T/mass, efficiency) space; the line A–B is the trade-off you get by just
    #    cranking current.  Its slope sets the eff / (T/mass) weights AUTOMATICALLY
    #    (no manual guessing): w on T/mass = ΔEff (efficiency lost to +current),
    #    w on efficiency = ΔT/mass (torque-density gained from +current).  A design
    #    ABOVE the line beats the current-only trade-off; farther above = better.
    #  'product': the legacy (efficiency/eff0)^w_eff · (T/mass/td0)^w_td.
    objective: str = "baseline_line"
    current_bump_pct: float = 10.0    # 2nd baseline current = I·(1 + pct/100)


class BaselineRequest(BaseModel):
    """Compute ONLY the current-only baseline line (2 sims of the current geometry
    at I and I·(1+bump)) — so the chart can draw it up-front, before running a full
    optimization.  Same eval params as a descent so the line is consistent."""
    operating_point: OptOperating = Field(default_factory=OptOperating)
    current_bump_pct: float = 10.0
    steps_per_period: int = 24
    coil_temp_c: float = 120.0
    mesh_size_mm: float = 4.0
    min_size_mm: float = 0.3
    gap_layers: float = 2.0
    pole_copy: Optional[bool] = None
    torque_filter: bool = True
    rotor_eddy: bool = True
    end_winding_factor: float = 0.0
    n_sectors: int = -1


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
        "THD_LL_pct":      m.get("THD_LL_pct"),      # line-to-line voltage THD (FOC)
        "Kt_Nm_per_Arms":  m.get("Kt_Nm_per_Arms"),
        "current_a":       m.get("current_a"),   # current the design was solved at
                                                 # (auto-adjusted to hit the target torque)
    }


def _pt(out: Dict[str, Any], kind: str):
    """One evaluated design as a point in objective space (torque-density vs
    efficiency), for the 2-D projection.  None if the eval failed."""
    if not out or not out.get("ok"):
        return None
    r = out["res"]
    # Attach the design (geometry overrides + solved current) so the chart can
    # apply a USER-PICKED point — click a point on the scatter → Apply that exact
    # geometry, not just the optimiser's auto-best.
    ov = out.get("overrides") or r.get("overrides") or {}
    return {"td": r.get("torque_per_mass_Nm_kg"), "eff": r.get("efficiency"),
            "ripple": r.get("T_ripple_pct"), "kind": kind,
            "thd": r.get("THD_LL_pct"),      # line-to-line voltage THD (FOC quality)
            "overrides": {k: v for k, v in ov.items() if k != "gamma_deg"},
            "current_a": out.get("current_a") or r.get("current_a"),
            "gamma_deg": ov.get("gamma_deg")}


def _make_bline(base_m: Dict[str, Any], bump_m: Dict[str, Any],
                bump_pct: float) -> Dict[str, Any]:
    """Build the 'current-only' baseline line from points A (start geometry at
    current I) and B (same geometry at I·(1+bump_pct/100)) in (T/mass, efficiency)
    space.  The line's slope sets the objective weights automatically:
      w on T/mass = Eff_A − Eff_B   (efficiency lost to the +current)
      w on eff    = T/mass_B − T/mass_A   (torque-density gained from +current)
    The signed perpendicular distance of any point P above this line is then
    [w_td·(td_P − td_A) + w_eff·(eff_P − eff_A)] / hypot(w_td, w_eff)."""
    td_a  = float(base_m.get("torque_per_mass_Nm_kg", 0.0) or 0.0)
    eff_a = float(base_m.get("efficiency", 0.0) or 0.0)
    td_b  = float(bump_m.get("torque_per_mass_Nm_kg", 0.0) or 0.0)
    eff_b = float(bump_m.get("efficiency", 0.0) or 0.0)
    w_td  = eff_a - eff_b          # efficiency given up per +current
    w_eff = td_b - td_a            # torque-density gained per +current
    norm  = ((w_td * w_td + w_eff * w_eff) ** 0.5) or 1.0
    return {"td_a": td_a, "eff_a": eff_a, "td_b": td_b, "eff_b": eff_b,
            "w_td": w_td, "w_eff": w_eff, "norm": norm,
            "bump_pct": float(bump_pct),
            "current_a": float(base_m.get("current_a", 0.0) or 0.0),
            "current_b": float(bump_m.get("current_a", 0.0) or 0.0)}


_RIPPLE_PEN_LAM = {"v": 0.0, "v0": 0.0}
# "v"  = the LIVE ripple-penalty weight _descent_cost reads (escalated across
#        box-walk rounds by the continuation ramp below).
# "v0" = the run's INITIAL weight from DescentRequest.ripple_penalty_lambda; the
#        ramp is disabled when v0 == 0 (ripple then stays a chart-only gate).
# One descent runs at a time, so a module global is safe.
_RIPPLE_RAMP      = 2.0    # ×λ per box-walk round while ripple is still over the gate
_RIPPLE_RAMP_CAP  = 16.0   # λ never exceeds v0·cap (bounded escalation)
_RIPPLE_OVER_TOL  = 0.3    # %-points slack before a design counts as "over the gate"
_THD_PEN = {"lam": 0.0, "max": 5.0}   # >0 → _descent_cost penalises THD_LL_pct over
                                      # thd_max_pct (same per-run-global pattern)


def _ripple_ramp_step(best_metrics: Dict[str, Any], ripple_max: float,
                      rnd: int) -> Optional[Dict[str, Any]]:
    """Augmented-Lagrangian-style penalty CONTINUATION for ripple.

    If the round's best design still breaches the ripple gate and a penalty is
    active (v0>0) but not yet at the cap, GROW the live weight so the next round
    feels stronger pressure — the optimizer starts soft (free to explore torque/
    efficiency) and is progressively forced under the ripple limit.  Returns an
    event dict (for the range_events info feed) when it ramped, else None.

    The user's ripple LIMIT + one starting λ is all that's needed: the algorithm
    escalates on its own until the constraint holds or the cap is hit."""
    v0 = float(_RIPPLE_PEN_LAM.get("v0", 0.0) or 0.0)
    if v0 <= 0.0:
        return None
    rip = float(best_metrics.get("T_ripple_pct", 0.0) or 0.0)
    if rip <= float(ripple_max) + _RIPPLE_OVER_TOL:
        return None                                   # already under the gate
    cur = float(_RIPPLE_PEN_LAM.get("v", v0) or v0)
    cap = v0 * _RIPPLE_RAMP_CAP
    if cur >= cap - 1e-9:
        return None                                   # escalation exhausted
    new = min(cur * _RIPPLE_RAMP, cap)
    _RIPPLE_PEN_LAM["v"] = new
    log.info("descent ripple RAMP: ripple %.2f%% > gate %.2f%% -> lambda %.3g -> %.3g",
             rip, float(ripple_max), cur, new)
    return {"iter": int(rnd), "name": "ripple_penalty", "side": "ramp",
            "from": round(cur, 4), "to": round(new, 4),
            "ripple": round(rip, 2), "gate": round(float(ripple_max), 2)}


def _descent_cost(m: Dict[str, Any], base: Dict[str, Any],
                  ripple_max: float, w_eff: float, w_td: float, lam: float,
                  v_peak_limit: float = 1e9):
    """Scalar cost (lower = better) + the raw figure-of-merit F.

    Two objective modes.  By DEFAULT ripple is not penalised (trimmed post-hoc on
    the chart) — but when the request sets ripple_penalty_lambda > 0 the cost adds
        λ_r · max(0, T_ripple_pct − ripple_max) / 100
    so the optimizer actively holds ripple under the gate (per Vadim: "ripple < 4 %
    при максимальном КПД и плотности момента" — the chart-trim alone let CMA drift
    to 7.6 % because the objective never felt the constraint).  The other
    feasibility penalty is over-voltage: V_peak above the inverter's usable
    phase-voltage limit, so a design the bus can't drive is repelled.

    • baseline-line (when base carries '_bline'): F = signed perpendicular distance
      of (T/mass, efficiency) ABOVE the current-only baseline line.  Weights come
      from the line itself (see _make_bline), so eff vs T/mass is balanced
      automatically.  F > 0 ⇒ the geometry beats just cranking current.
    • product (legacy fallback): F = (efficiency/eff0)^w_eff · (T/mass/td0)^w_td."""
    vpk  = float(m.get("V_peak", 0.0) or 0.0)
    pen  = lam * max(0.0, vpk - v_peak_limit)
    _rp = float(_RIPPLE_PEN_LAM.get("v", 0.0) or 0.0)
    if _rp > 0.0:
        _rip = float(m.get("T_ripple_pct", 0.0) or 0.0)
        # /100: 1 % overshoot → 0.01·λ_r, same order as typical F gains (~0.01)
        pen += _rp * max(0.0, _rip - float(ripple_max)) / 100.0
    _tl = float(_THD_PEN.get("lam", 0.0) or 0.0)
    if _tl > 0.0:
        _thd = float(m.get("THD_LL_pct", 0.0) or 0.0)
        pen += _tl * max(0.0, _thd - float(_THD_PEN.get("max", 5.0))) / 100.0
    bl = base.get("_bline") if isinstance(base, dict) else None
    if bl:
        td  = float(m.get("torque_per_mass_Nm_kg", 0.0) or 0.0)
        eff = float(m.get("efficiency", 0.0) or 0.0)
        score = bl["w_td"] * (td - bl["td_a"]) + bl["w_eff"] * (eff - bl["eff_a"])
        F = score / (bl.get("norm", 1.0) or 1.0)     # true perpendicular distance
        return (-F + pen), F
    eff  = max(float(m.get("efficiency", 0.0) or 0.0), 1e-6)
    td   = max(float(m.get("torque_per_mass_Nm_kg", 0.0) or 0.0), 1e-6)
    eff0 = max(float(base.get("efficiency", 1.0) or 1.0), 1e-6)
    td0  = max(float(base.get("torque_per_mass_Nm_kg", 1.0) or 1.0), 1e-6)
    F    = ((eff / eff0) ** w_eff) * ((td / td0) ** w_td)
    return (-F + pen), F


def _boundary_flags(specs, best_x, margin=0.05):
    """Variables whose optimum landed within `margin` of a window edge → the true
    optimum is probably OUTSIDE the ±deviation window.  at_hard_limit = the window
    is already at the schema's physical min/max (a real limit, not a too-narrow
    window).  Drives the box-walking stop condition (server-side)."""
    out = []
    for v in specs:
        nm = v["name"]; lo = float(v["lo"]); hi = float(v["hi"])
        x = best_x.get(nm)
        if x is None:
            continue
        x = float(x); w = hi - lo
        if w <= 0:
            continue
        m = margin * w
        pinned = "high" if x >= hi - m else ("low" if x <= lo + m else None)
        if not pinned:
            continue
        hlo = v.get("hard_lo"); hhi = v.get("hard_hi")
        at_hard = ((pinned == "high" and hhi is not None and hi >= float(hhi) - 1e-9) or
                   (pinned == "low"  and hlo is not None and lo <= float(hlo) + 1e-9))
        out.append({"name": nm, "value": x, "min": lo, "max": hi,
                    "pinned": pinned, "at_hard_limit": bool(at_hard)})
    return out


def _recenter_specs(specs, best_x):
    """Slide each variable's window to be centred on the optimum, keeping its width
    (±deviation) constant, clamped to the schema's physical min/max.  This is the
    box-walking step: the window walks toward the optimum without growing."""
    out = []
    for v in specs:
        nm = v["name"]; lo = float(v["lo"]); hi = float(v["hi"])
        nv = dict(v); x = best_x.get(nm); d = (hi - lo) / 2.0
        if x is not None and d > 0:
            x = float(x); nlo, nhi = x - d, x + d
            hlo = v.get("hard_lo"); hhi = v.get("hard_hi")
            if hlo is not None:
                nlo = max(float(hlo), nlo)
            if hhi is not None:
                nhi = min(float(hhi), nhi)
            if nhi > nlo:
                nv["lo"] = nlo; nv["hi"] = nhi
        out.append(nv)
    return out


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
                    optimize_gamma=True, auto_expand=False, max_rounds=1,
                    boundary_margin=0.05, surrogate_seed=False, pole_copy=None,
                  torque_filter=True, end_winding=0.0, rotor_eddy=True,
                  gap_layers=2.0, objective="baseline_line",
                  current_bump_pct=10.0) -> None:
    # NOTE: server-side box-walking (auto_expand) is implemented for CMA-ES only;
    # the gradient path runs a single round, then the UI flags boundary variables
    # for a manual one-click continue.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        cfg = get_config()
        geo0 = dict(cfg.get("geometry", {}))
        I   = float(op.get("current_a", 85.0))
        g   = float(op.get("gamma_deg", 0.0))
        _spec_by = {v["name"]: v for v in var_specs}
        for v in var_specs:                      # remember the ORIGINAL window —
            v["lo0"] = float(v["lo"])            # auto-expand growth caps + events
            v["hi0"] = float(v["hi"])
            v["span0"] = max(float(v["hi"]) - float(v["lo"]), 1e-12)

        def _fit(name, val):
            """Clamp into bounds; round integers; snap mm vars to the 0.1 mm grid."""
            v = _spec_by[name]
            val = min(v["hi"], max(v["lo"], val))
            if v.get("is_int"):
                return round(val)
            q = float(v.get("quant", 0.0) or 0.0)
            return (round(val / q) * q) if q > 0 else val

        # Start at the current config value for each variable, clamped to bounds.
        x = {}
        for v in var_specs:
            x[v["name"]] = _fit(v["name"], float(geo0.get(v["name"], v["lo"])))

        _torque_tol = 0.02       # probe within ±2 % of target → accept, no 2nd solve
        warm = [I]               # warm-start probe current (last geometry's rated current)

        def _eval_at(d, cur):
            o = _subprocess_eval(d, cur, steps, coil_temp, n_periods=1.0,
                                 gamma_deg=g, mesh_size_mm=mesh_size,
                                 min_size_mm=min_size, n_sectors=n_sectors,
                                 pole_copy=pole_copy, torque_filter=torque_filter,
                                 end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                 gap_layers=gap_layers)
            if o.get("ok") and isinstance(o.get("res"), dict):
                o["res"]["current_a"] = float(cur)   # record solved current in best
            if isinstance(o, dict):
                o["overrides"] = dict(d)             # stamp design → chart click-to-apply
            return o

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
        # ── Baseline (current-only) line: a 2nd FEM sim of THIS geometry at
        #    I·(1+bump) gives point B; A–B sets the perpendicular-distance weights. ──
        if str(objective) == "baseline_line":
            try:
                bb = _eval_at(x, I * (1.0 + max(0.0, float(current_bump_pct)) / 100.0))
                n_evals += 1
                if bb.get("ok"):
                    base["_bline"] = _make_bline(base, bb["res"], current_bump_pct)
                    with _descent_lock:
                        _descent_state["baseline_line"] = dict(base["_bline"])
            except Exception:   # noqa: BLE001
                pass
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

        def _expand_pinned(it_no: int) -> bool:
            """AUTO-EXPAND (per Vadim 2026-07-02): when the accepted iterate sits
            pinned at a window edge, GROW the window in that direction and keep
            optimizing — the user's range is a starting guess, not a wall.
            Growth per event = max(half the ORIGINAL span, 3 steps); the schema's
            physical min/max is never crossed; each side may grow at most 4× the
            original span per run (runaway guard).  Every extension is logged and
            published to the progress feed (range_events) + refreshes the live
            variables list, so the UI shows exactly what moved and where."""
            if not auto_expand:
                return False
            grew = False
            for v in var_specs:
                nm = v["name"]; lo = float(v["lo"]); hi = float(v["hi"])
                xv = best["x"].get(nm)
                if xv is None or hi <= lo:
                    continue
                xv = float(xv)
                m = max(boundary_margin * (hi - lo), float(v["step"]) * 1.001)
                inc = max(0.5 * v["span0"], 3.0 * float(v["step"]))
                ev = None
                if xv >= hi - m:                          # pinned at the TOP edge
                    new_hi = min(hi + inc, v["hi0"] + 4.0 * v["span0"])
                    if v.get("hard_hi") is not None:
                        new_hi = min(new_hi, float(v["hard_hi"]))
                    if new_hi > hi + 1e-12:
                        v["hi"] = new_hi
                        ev = {"side": "high", "from": round(hi, 6), "to": round(new_hi, 6)}
                elif xv <= lo + m:                        # pinned at the BOTTOM edge
                    new_lo = max(lo - inc, v["lo0"] - 4.0 * v["span0"])
                    if v.get("hard_lo") is not None:
                        new_lo = max(new_lo, float(v["hard_lo"]))
                    if new_lo < lo - 1e-12:
                        v["lo"] = new_lo
                        ev = {"side": "low", "from": round(lo, 6), "to": round(new_lo, 6)}
                if ev:
                    grew = True
                    ev.update(name=nm, value=round(xv, 6), iter=int(it_no))
                    log.info("descent AUTO-EXPAND: %s pinned %s at %.6g -> window %s "
                             "edge %.6g -> %.6g", nm, ev["side"], xv, ev["side"],
                             ev["from"], ev["to"])
                    with _descent_lock:
                        _descent_state.setdefault("range_events", []).append(dict(ev))
                        _descent_state["variables"] = [
                            {"name": s["name"], "lo": s["lo"], "hi": s["hi"],
                             "step": s["step"]} for s in var_specs]
            if grew:
                with _descent_lock:
                    _descent_state["boundary"] = _boundary_flags(
                        var_specs, best["x"], boundary_margin)
            return grew

        def _ramp_ripple(it_no: int) -> bool:
            """Gradient-path twin of the CMA continuation: when the search would
            otherwise converge but the incumbent still breaches the ripple gate,
            escalate λ and keep going (re-score the incumbent under the new λ)."""
            ev = _ripple_ramp_step(best["metrics"], ripple_max, it_no)
            if ev is None:
                return False
            best["cost"], best["F"] = _descent_cost(
                best["metrics"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
            with _descent_lock:
                _descent_state.setdefault("range_events", []).append(dict(ev))
                _descent_state["best"] = _best_state()
            return True

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
                    p = _pt(out, "grad")                  # publish per-eval (real-time chart)
                    if p:
                        all_pts.append(p)
                    with _descent_lock:
                        _descent_state["n_evals"] = n_evals
                        _descent_state["points"] = list(all_pts[-1200:])
                        _descent_state["best"] = _best_state()
            with _descent_lock:
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
                # Flat gradient: pinned variables produce a zero component (both
                # probes clamp onto the same edge point) — if that's the cause,
                # auto-expand the window and keep going; else truly converged.
                if _expand_pinned(it) or _ramp_ripple(it):
                    lr = max(lr, 3.0)
                    continue
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
            # AUTO-EXPAND check EVERY iteration (improved or not): the iterate
            # may sit pinned at a window edge — the common case is pinned from
            # the very BASELINE (config value clamped into a too-narrow window),
            # where nothing ever "improves" because every move clamps back onto
            # the same edge point.  Widen right away so the NEXT gradient can
            # cross; only declare convergence when the step has collapsed AND
            # nothing was pinned/extendable.
            if _expand_pinned(it):
                lr = max(lr, 3.0)      # fresh territory → restore the step size
            elif (not improved) and lr < 0.05:
                if _ramp_ripple(it):   # converged geometry-wise but ripple over gate
                    lr = max(lr, 3.0)  #   → tighten the penalty and keep going
                else:
                    break              # step too small, nothing pinned → converged

        with _descent_lock:
            # Final boundary flags (the UI banner reads these for the gradient
            # path too — previously only CMA-ES published them).
            _descent_state["boundary"] = _boundary_flags(var_specs, best_seen["x"],
                                                         boundary_margin)
            _descent_state.update(
                result={"best": {"metrics": _msum(best_seen["metrics"]),
                                 "cost": round(best_seen["cost"], 5),
                                 "F": round(best_seen["F"], 5),
                                 "overrides": {k: round(float(val), 4) for k, val in best_seen["x"].items()}},
                        "baseline": _msum(base), "history": list(history),
                        "n_evals": n_evals,
                        "operating_point": op, "ripple_max_pct": ripple_max,
                        "weights": {"w_eff": w_eff, "w_td": w_td, "lambda": lam},
                        "baseline_line": base.get("_bline")},
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


def _surrogate_seed_overrides(var_specs, ripple_max, min_n=20):
    """Bayesian warm-start: the geometry the RandomForest surrogate predicts best
    over ALL accumulated evals (config/.opt_dataset.jsonl).  Returns {var: value}
    clamped to each variable's current window, or None if there isn't enough data."""
    try:
        from motor_ai_sim.optimization import surrogate as _surr
        recs = _surr.filter_operating(_surr.load_dataset(_dataset_path()))
        if len(recs) < int(min_n):
            return None
        bounds = {v["name"]: (float(v["lo"]), float(v["hi"])) for v in var_specs}
        sg = _surr.suggest(recs, bounds=bounds, n=1, ripple_max=float(ripple_max))
        if not sg.get("ok") or not sg.get("suggestions"):
            return None
        return sg["suggestions"][0].get("overrides") or None
    except Exception as _e:   # noqa: BLE001
        log.warning("surrogate seed unavailable: %s", _e)
        return None


def _cmaes_worker(var_specs, op, ripple_max, w_eff, w_td, lam,
                  steps, coil_temp, mesh_size, min_size, max_iters, run_id,
                  n_sectors=-1, v_peak_limit=1e9, target_torque=0.0,
                  optimize_gamma=True, auto_expand=False, max_rounds=1,
                  boundary_margin=0.05, surrogate_seed=False, pole_copy=None,
                  torque_filter=True, end_winding=0.0, rotor_eddy=True,
                  gap_layers=2.0, objective="baseline_line",
                  current_bump_pct=10.0) -> None:
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
        geo0 = dict(cfg.get("geometry", {}))   # round-0 start; re-centred to best each round
        I = float(op.get("current_a", 85.0)); g = float(op.get("gamma_deg", 0.0))
        _torque_tol = 0.02       # probe within ±2 % of target → accept, no 2nd solve
        warm = [I]               # warm-start probe current (shared starting guess)

        def _eval_at(dd, cur):
            o = _subprocess_eval(dd, cur, steps, coil_temp, n_periods=1.0,
                                 gamma_deg=g, mesh_size_mm=mesh_size,
                                 min_size_mm=min_size, n_sectors=n_sectors,
                                 pole_copy=pole_copy, torque_filter=torque_filter,
                                 end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                 gap_layers=gap_layers)
            # Stamp the SOLVED current onto the result so the best records the
            # operating point it was found at (target-torque solves for it) →
            # saving the design can persist current+γ for a reproducible sim.
            if o.get("ok") and isinstance(o.get("res"), dict):
                o["res"]["current_a"] = float(cur)
            if isinstance(o, dict):
                o["overrides"] = dict(dd)            # stamp design → chart click-to-apply
            return o

        def evalx(d):
            # Target-torque with warm-start + ±2 % band: probe at the warm-start
            # current (snapshot once — parallel evals share warm[]); a miss triggers a
            # rescale (T≈linear in I) + 2nd solve.
            if target_torque and target_torque > 0:
                probe = warm[0]
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

        # ── Cross-round state (box-walking: re-centre the window + re-run) ───────
        best = None              # global best across all rounds
        base = None              # round-0 baseline = fixed F reference
        all_pts = []; history = []
        n_evals = 0; it_global = 0
        cur_specs = [dict(v) for v in var_specs]
        # Remember each variable's ORIGINAL window so the box-walk can GROW a
        # pinned side (bounded to 4× the original span), like the gradient path.
        for v in cur_specs:
            v["lo0"] = float(v["lo"]); v["hi0"] = float(v["hi"])
            v["span0"] = max(float(v["hi"]) - float(v["lo"]), 1e-9)
        rounds = max(1, int(max_rounds)) if auto_expand else 1
        boundary = []

        def _bstate():
            return {"metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                    "F": round(best["F"], 5), "x": dict(best["x"])}
        def _hrow(itg, m, c, F, xd):
            return {"iter": itg, **_msum(m), "cost": round(c, 5), "F": round(F, 5),
                    "x": {k: round(float(v), 4) for k, v in xd.items()}}

        for rnd in range(rounds):
            names = [v["name"] for v in cur_specs]
            lo = np.array([float(v["lo"]) for v in cur_specs])
            hi = np.array([float(v["hi"]) for v in cur_specs])
            span = np.maximum(hi - lo, 1e-9)
            is_int = [bool(v.get("is_int")) for v in cur_specs]
            quant = [float(v.get("quant", 0.0) or 0.0) for v in cur_specs]

            def to_geom(xn, names=names, lo=lo, span=span, is_int=is_int, quant=quant):
                phys = lo + np.clip(np.asarray(xn, float), 0.0, 1.0) * span
                def _q(i):
                    if is_int[i]:
                        return float(round(phys[i]))
                    if quant[i] > 0:                      # mm var → snap to the 0.1 mm grid
                        return float(round(phys[i] / quant[i]) * quant[i])
                    return float(phys[i])
                return {nm: _q(i) for i, nm in enumerate(names)}

            x0n = np.clip((np.array([float(geo0.get(nm, lo[i])) for i, nm in enumerate(names)]) - lo) / span, 0.0, 1.0)

            # Bayesian warm-start (surrogate seed): start round 0 from the geometry the
            # surrogate predicts best over all accumulated evals — a learned-good region
            # instead of the current design.  Silent fall-back to current geom if <20 evals.
            if surrogate_seed and rnd == 0:
                _seed_ov = _surrogate_seed_overrides(cur_specs, ripple_max)
                if _seed_ov:
                    x0n = np.clip((np.array([float(_seed_ov.get(nm, geo0.get(nm, lo[i])))
                                             for i, nm in enumerate(names)]) - lo) / span, 0.0, 1.0)
                    with _descent_lock:
                        _descent_state["seeded_from_surrogate"] = True

            if rnd == 0:
                # MTPA: optimize the load angle γ for the starting geometry FIRST.
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
                b = evalx(to_geom(x0n)); n_evals += 1
                if not b.get("ok"):
                    with _descent_lock:
                        _descent_state.update(error=f"baseline eval failed: {b.get('error')}", running=False)
                    return
                base = b["res"]
                # ── Baseline (current-only) line: 2nd FEM sim of the START geometry
                #    at I·(1+bump) → point B; A–B fixes the perpendicular-distance
                #    weights for the whole search (kept across box-walking rounds). ──
                if str(objective) == "baseline_line":
                    try:
                        bb = _eval_at(to_geom(x0n),
                                      I * (1.0 + max(0.0, float(current_bump_pct)) / 100.0))
                        n_evals += 1
                        if bb.get("ok"):
                            base["_bline"] = _make_bline(base, bb["res"], current_bump_pct)
                            with _descent_lock:
                                _descent_state["baseline_line"] = dict(base["_bline"])
                    except Exception:   # noqa: BLE001
                        pass
                cost0, F0 = _descent_cost(base, base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                best = {"x": to_geom(x0n), "metrics": base, "cost": cost0, "F": F0}
                history.append(_hrow(0, base, cost0, F0, best["x"]))
                _bp = _pt(b, "baseline")
                if _bp:
                    all_pts.append(_bp)

            with _descent_lock:
                _descent_state.update(running=True, iter=0, max_iters=max_iters, phase="optimizing",
                                      walk_round=rnd + 1, walk_rounds=rounds, n_evals=n_evals,
                                      baseline=_msum(base), best=_bstate(), current=_msum(best["metrics"]),
                                      history=list(history), error=None, grad={}, points=list(all_pts), boundary=[],
                                      variables=[{"name": v["name"], "lo": v["lo"], "hi": v["hi"],
                                                  "step": v["step"]} for v in cur_specs])

            es = cma.CMAEvolutionStrategy(list(x0n), 0.25,
                                          {"bounds": [0.0, 1.0], "maxiter": int(max_iters),
                                           "verbose": -9, "seed": 12345})
            it_round = 0
            cancelled = False
            # Early stopping: end the round once the best cost stops improving for
            # _PATIENCE generations.  CMA-ES (especially surrogate-seeded) often
            # converges in 1-2 iters, so running the full max_iters just burns FEM
            # solves on no improvement.  max_iters stays the hard cap.
            _PATIENCE = 3
            _stale = 0
            _prev_best = best["cost"]
            while not es.stop():
                with _descent_lock:
                    if _descent_state["cancel"]:
                        cancelled = True
                        break
                sols = es.ask()
                cost_by_i = {}
                with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as ex:
                    futs = {ex.submit(evalx, to_geom(s)): i for i, s in enumerate(sols)}
                    for fut in as_completed(futs):
                        i = futs[fut]
                        out = fut.result()
                        n_evals += 1
                        if out and out.get("ok"):
                            c, Fv = _descent_cost(out["res"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                            cost_by_i[i] = c
                            p = _pt(out, "cmaes")
                            if p:
                                all_pts.append(p)
                            if c < best["cost"] - 1e-9:
                                best = {"x": to_geom(sols[i]), "metrics": out["res"], "cost": c, "F": Fv}
                        else:
                            cost_by_i[i] = 1e6        # failed eval → repelled
                        # Publish per-EVAL so the objective-space chart fills in real time.
                        with _descent_lock:
                            _descent_state["n_evals"] = n_evals
                            _descent_state["points"] = list(all_pts)
                            _descent_state["best"] = _bstate()
                            _descent_state["current"] = _msum(best["metrics"])
                costs = [cost_by_i.get(i, 1e6) for i in range(len(sols))]
                es.tell(sols, costs)
                it_round += 1; it_global += 1
                history.append(_hrow(it_global, best["metrics"], best["cost"], best["F"], best["x"]))
                with _descent_lock:
                    _descent_state.update(iter=it_round, history=list(history))
                _save_descent_state()   # checkpoint each generation
                # Early stop: no meaningful improvement for _PATIENCE generations.
                _tol = max(1e-4, 1e-3 * abs(_prev_best))
                if best["cost"] < _prev_best - _tol:
                    _prev_best = best["cost"]; _stale = 0
                else:
                    _stale += 1
                    if _stale >= _PATIENCE:
                        log.info("descent: early stop at iter %d/%d (no improvement for %d gens)",
                                 it_round, max_iters, _PATIENCE)
                        with _descent_lock:
                            _descent_state["converged"] = True
                        break

            # Box-walking: did any variable end pinned at its window edge?
            boundary = _boundary_flags(cur_specs, best["x"], boundary_margin)
            with _descent_lock:
                _descent_state["boundary"] = boundary
            soft = [f for f in boundary if not f["at_hard_limit"]]

            # Ripple-penalty CONTINUATION: if the round's best still breaches the
            # ripple gate, escalate λ so the NEXT round feels stronger pressure —
            # and keep walking even if every variable already settled (the
            # constraint, not the window, decides when the search is done).
            ramp_ev = _ripple_ramp_step(best["metrics"], ripple_max, rnd + 1)
            if ramp_ev is not None:
                # Re-score the incumbent under the new λ so cross-round "< best"
                # comparisons stay consistent with the escalated cost.
                best["cost"], best["F"] = _descent_cost(
                    best["metrics"], base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                with _descent_lock:
                    _descent_state.setdefault("range_events", []).append(dict(ramp_ev))
                    _descent_state["best"] = _bstate()

            # Stop only when nothing is pinned AND ripple is under the gate
            # (or the user disabled auto-continue / cancelled).
            if cancelled or not auto_expand or (not soft and ramp_ev is None):
                break

            # Re-centre the window on the optimum, then GROW any soft-pinned side
            # so the next round explores fresh territory instead of only sliding
            # (bounded to 4× the ORIGINAL span + the schema's physical limit).
            geo0 = dict(best["x"])
            _pin = {f["name"]: f["pinned"] for f in soft}
            _old_win = {v["name"]: (float(v["lo"]), float(v["hi"])) for v in cur_specs}
            cur_specs = _recenter_specs(cur_specs, best["x"])
            for v in cur_specs:
                side = _pin.get(v["name"])
                if not side:
                    continue
                inc = max(0.5 * v["span0"], 3.0 * float(v["step"]))
                if side == "high":
                    nhi = min(float(v["hi"]) + inc, v["hi0"] + 4.0 * v["span0"])
                    if v.get("hard_hi") is not None:
                        nhi = min(nhi, float(v["hard_hi"]))
                    v["hi"] = max(float(v["hi"]), nhi)
                else:                                  # pinned "low"
                    nlo = max(float(v["lo"]) - inc, v["lo0"] - 4.0 * v["span0"])
                    if v.get("hard_lo") is not None:
                        nlo = max(nlo, float(v["hard_lo"]))
                    v["lo"] = min(float(v["lo"]), nlo)
            # Publish moved/grown windows to the info feed (range_events) + refresh
            # the live variables list, so the UI shows exactly what moved.
            with _descent_lock:
                _descent_state["variables"] = [
                    {"name": v["name"], "lo": v["lo"], "hi": v["hi"], "step": v["step"]}
                    for v in cur_specs]
                for v in cur_specs:
                    o = _old_win.get(v["name"])
                    if o and (abs(o[0] - float(v["lo"])) > 1e-12
                              or abs(o[1] - float(v["hi"])) > 1e-12):
                        _grew = v["name"] in _pin
                        _descent_state.setdefault("range_events", []).append({
                            "iter": int(rnd + 1), "name": v["name"],
                            "side": "grow" if _grew else "walk",
                            "from": round(o[0], 6), "to": round(float(v["lo"]), 6),
                            "from_hi": round(o[1], 6), "to_hi": round(float(v["hi"]), 6),
                            "value": round(float(best["x"].get(v["name"], 0.0)), 6)})
                        log.info("CMA box-%s: %s window [%.6g, %.6g] -> [%.6g, %.6g]",
                                 "grow" if _grew else "walk", v["name"],
                                 o[0], o[1], float(v["lo"]), float(v["hi"]))

        with _descent_lock:
            _descent_state["result"] = {
                "best": {"x": best["x"],
                         "overrides": {k: round(float(v), 4) for k, v in best["x"].items()},
                         "metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                         "F": round(best["F"], 5)},
                "baseline": _msum(base), "history": list(history), "n_evals": n_evals,
                "operating_point": op, "ripple_max_pct": ripple_max,
                "weights": {"w_eff": w_eff, "w_td": w_td, "lambda": lam},
                "baseline_line": base.get("_bline"),
                "boundary": boundary, "walk_rounds": rounds,
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
        # Manufacturable grid: snap mm-dimensioned variables to 0.1 mm during the
        # search, so the optimizer only ever evaluates (and returns) round values.
        quant = 0.1 if str(meta.get("unit", "")).strip().lower() == "mm" else 0.0
        var_specs.append({"name": v.name, "lo": lo, "hi": hi,
                          "step": step, "is_int": is_int,
                          "hard_lo": s_lo, "hard_hi": s_hi, "quant": quant})

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
    # Ripple penalty (per-run; one descent at a time → module global is safe).
    # v0 is the INITIAL weight; the box-walk ramp escalates "v" from it.
    _rp0 = max(0.0, float(req.ripple_penalty_lambda or 0.0))
    _RIPPLE_PEN_LAM["v"] = _rp0
    _RIPPLE_PEN_LAM["v0"] = _rp0
    # THD penalty (CIANO FOC spec: clean line-to-line back-EMF; same pattern).
    _THD_PEN["lam"] = max(0.0, float(req.thd_penalty_lambda or 0.0))
    _THD_PEN["max"] = max(0.0, float(req.thd_max_pct or 5.0))
    # Box-walking (server-side auto-expand): re-centre the window + re-run until settled.
    auto_expand     = bool(req.auto_expand)
    max_rounds      = max(1, min(int(req.max_rounds), 20))
    boundary_margin = min(0.3, max(0.01, float(req.boundary_margin)))

    with _descent_lock:
        _descent_state.update({"running": True, "iter": 0, "max_iters": max_iters,
                               "n_evals": 0, "best": None, "current": None,
                               "history": [], "baseline": None, "baseline_line": None,
                               "result": None, "phase": "starting",
                               "points": [], "grad": {}, "mtpa_gamma_deg": None, "variables": [],
                               "boundary": [], "walk_round": 1, "converged": False,
                               "range_events": [],   # auto-expansions of pinned variables (info feed)
                               "seeded_from_surrogate": False,
                               "walk_rounds": (max_rounds if auto_expand else 1),
                               # Eval parameters this run used — pinned to the result so applying
                               # a point can RESTORE them into the Simulation tab (else re-running
                               # the Sim at toggled settings won't reproduce the picked design).
                               "eval_params": {
                                   "steps_per_period": steps, "n_sectors": n_sectors,
                                   "gap_layers": float(req.gap_layers), "coil_temp_c": float(req.coil_temp_c),
                                   "pole_copy": req.pole_copy, "torque_filter": bool(req.torque_filter),
                                   "rotor_eddy": bool(req.rotor_eddy),
                                   "end_winding_factor": float(req.end_winding_factor),
                                   "mesh_size_mm": mesh_size, "min_size_mm": min_size},
                               "run_id": req.run_id, "error": None, "cancel": False})
    threading.Thread(
        target=worker,
        args=(var_specs, op, float(req.ripple_max_pct), float(req.w_eff),
              float(req.w_td), float(req.penalty_lambda), steps,
              float(req.coil_temp_c), mesh_size, min_size, max_iters, req.run_id,
              n_sectors, v_peak_limit, target_torque, bool(req.optimize_gamma),
              auto_expand, max_rounds, boundary_margin, bool(req.surrogate_seed),
              req.pole_copy, bool(req.torque_filter),
              float(req.end_winding_factor), bool(req.rotor_eddy),
              max(1.0, min(float(req.gap_layers), 8.0)),
              str(req.objective or "baseline_line"),
              max(0.0, min(float(req.current_bump_pct), 100.0))),
        daemon=True).start()
    return {"started": True, "algorithm": algo, "n_sectors": n_sectors,
            "target_torque_nm": target_torque, "v_peak_limit": v_peak_limit,
            "n_variables": len(var_specs), "max_iters": max_iters, "auto_expand": auto_expand,
            "max_rounds": (max_rounds if auto_expand else 1),
            "variables": [s["name"] for s in var_specs], "steps_per_period": steps}


@router.post("/descent/baseline")
def descent_baseline(req: BaselineRequest):
    """Two FEM sims of the CURRENT geometry — at I and I·(1+bump) — defining the
    'current-only' trade-off line A–B, so the objective-space chart can draw it
    BEFORE a full optimization.  Runs synchronously (2 evals); returns the line +
    auto-weights via _make_bline (same as a descent computes internally)."""
    I    = float(req.operating_point.current_a)
    g    = float(req.operating_point.gamma_deg)
    bump = max(0.0, min(float(req.current_bump_pct), 100.0))
    steps     = max(8, min(int(req.steps_per_period), 180))
    mesh_size = max(1.0, min(float(req.mesh_size_mm), 12.0))
    min_size  = max(0.1, min(float(req.min_size_mm), 3.0))
    gap       = max(1.0, min(float(req.gap_layers), 8.0))
    n_sectors = int(req.n_sectors)

    def _ev(cur: float):
        return _subprocess_eval({}, cur, steps, float(req.coil_temp_c), n_periods=1.0,
                                gamma_deg=g, mesh_size_mm=mesh_size, min_size_mm=min_size,
                                n_sectors=n_sectors, pole_copy=req.pole_copy,
                                torque_filter=bool(req.torque_filter),
                                end_winding_factor=float(req.end_winding_factor),
                                rotor_eddy=bool(req.rotor_eddy), gap_layers=gap)

    a = _ev(I)
    if not a.get("ok"):
        raise HTTPException(status_code=500, detail=f"baseline A eval failed: {a.get('error')}")
    b = _ev(I * (1.0 + bump / 100.0))
    if not b.get("ok"):
        raise HTTPException(status_code=500, detail=f"baseline B eval failed: {b.get('error')}")
    a["res"]["current_a"] = I
    b["res"]["current_a"] = I * (1.0 + bump / 100.0)
    return {"baseline_line": _make_bline(a["res"], b["res"], bump)}


@router.get("/descent/progress")
def descent_progress():
    with _descent_lock:
        return _json_sane(dict(_descent_state))


@router.post("/descent/cancel")
def descent_cancel():
    with _descent_lock:
        _descent_state["cancel"] = True
    return {"cancelled": True}


@router.get("/surrogate")
def surrogate_report(suggest: int = 5, ripple_max: float = 5.0):
    """Surrogate variable-importance (which geometry vars drive ripple vs torque vs
    efficiency) + a few Bayesian-style next-design suggestions, learned from the
    accumulated eval dataset (config/.opt_dataset.jsonl)."""
    try:
        from motor_ai_sim.optimization import surrogate as _surr
        recs = _surr.load_dataset(_dataset_path())
        return {"n_total": len(recs),
                "importance": _surr.variable_importance(recs),
                "suggest": _surr.suggest(recs, n=int(suggest), ripple_max=float(ripple_max))}
    except Exception as e:   # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"surrogate report failed: {e}")


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
