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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from motor_ai_sim.config import get_config
from motor_ai_sim.optimization import run_pareto_search
from motor_ai_sim.optimization import pareto as _pareto

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
_scan_thread: Optional["threading.Thread"] = None   # the live worker (liveness guard)


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
                _restored = json.load(fh)
            # It belongs to whatever run produced it, and to no request made
            # after this process started: mark it so the client can show it as
            # history but never adopt it as the result of a run it just fired.
            if isinstance(_restored, dict):
                _restored["restored_from_disk"] = True
            _scan_state["result"] = _restored
            log.info("restored last scan from %s (run_id=%s)", p,
                     (_restored or {}).get("run_id") if isinstance(_restored, dict) else None)
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


def _effective_demag(demag: Optional[bool]) -> bool:
    """The demag flag an eval will actually run with: the caller's, or the
    active config's when the caller left it unset (which is what refine_proc's
    run_one does)."""
    if demag is not None:
        return bool(demag)
    try:
        from motor_ai_sim.config import get_config
        return bool(get_config().get("simulation", {}).get("demag", False))
    except Exception:  # noqa: BLE001
        return False


def _eval_cache_key(overrides: Dict[str, float], current_a: float, steps: int,
                    coil_temp_c: float, n_periods: float, gamma_deg: float,
                    mesh_size_mm: float, min_size_mm: float, n_sectors: int,
                    pole_copy, torque_filter: bool, cfg_fp: str,
                    gap_layers: float = 3.0, end_winding_factor: float = 0.0,
                    rotor_eddy: bool = False, hi_fidelity: bool = False,
                    structured_gap: bool = False, airgap_macro: bool = False,
                    iron_template: bool = True, geo_mesh: bool = True,
                    element_order: int = 2, demag: Optional[bool] = None) -> str:
    payload = {
        "ov": {k: round(float(v), 6) for k, v in sorted(overrides.items())},
        "I": round(float(current_a), 4), "steps": int(steps),
        "ct": round(float(coil_temp_c), 2), "np": round(float(n_periods), 5),
        "g": round(float(gamma_deg), 4), "ms": round(float(mesh_size_mm), 4),
        "mn": round(float(min_size_mm), 4), "ns": int(n_sectors),
        "pc": pole_copy, "tf": bool(torque_filter), "cfg": cfg_fp,
        "gl": round(float(gap_layers), 2), "ew": round(float(end_winding_factor), 3),
        "re": bool(rotor_eddy), "hf": bool(hi_fidelity),
        "sg": bool(structured_gap), "am": bool(airgap_macro), "it": bool(iron_template),
        "gm": bool(geo_mesh), "eo": int(element_order),
        # Demag de-rates Br, so a demag point and a full-strength point are
        # DIFFERENT physics and must not share a cache entry.  None means the
        # eval will fall back to the config, so the KEY has to resolve the same
        # way — otherwise a demag run is served full-strength cached points.
        "dm": bool(_effective_demag(demag)),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _eval_healthy(out: Dict[str, Any]) -> bool:
    """True only for an eval whose FEM actually produced numbers.  A worker that
    dies mid-solve (or a solver that silently returns an empty field) can emit
    ok=true with NaN torque — caching that poisons every re-run of the sweep
    with instantly-"done" empty points, so gate both store AND load on it.

    Also rejects an eval whose nonlinear solve did NOT converge.  refine_proc
    raises on that case, so a fresh eval can never reach here unhealthy on this
    count; the check exists for cache lines and for any future path that returns
    the stamp instead of raising."""
    try:
        r = out.get("res") if isinstance(out.get("res"), dict) else out
        if r.get("nonlinear_converged") is False:
            return False
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
            # Lines written BEFORE the convergence gate carry no stamp, so they
            # cannot be checked — they are kept (re-running them costs hours of
            # FEM) but counted out loud, because "unverified" is not "converged".
            n_unver = sum(
                1 for v in _EVAL_CACHE.values()
                if not isinstance((v.get("res") if isinstance(v.get("res"), dict)
                                   else v).get("nonlinear_converged"), bool))
            log.info("loaded %d cached FEM evals from %s (skipped %d unhealthy, "
                     "%d predate the convergence gate — unverified, not proven "
                     "converged)", len(_EVAL_CACHE), p, n_bad, n_unver)
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


# ── Measured eval cost ───────────────────────────────────────────────────────
# Project rule: a run says what it COSTS before it starts.  The only honest
# number is one this machine measured, so every subprocess eval is timed and the
# MEDIAN is kept (median, not mean: one 300 s timeout must not triple the
# quote).  Persisted next to the config so the estimate survives a restart, and
# the sample count travels with it — a quote from 3 evals is labelled as such.
_EVAL_SECS: List[float] = []
_eval_secs_lock = threading.Lock()
_EVAL_SECS_MAX = 200          # rolling window
_EVAL_RATE_FLUSH_EVERY = 10   # persist at most every N evals


def _eval_rate_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                        "config", ".eval_rate.json"))


def _load_eval_rate() -> None:
    try:
        p = _eval_rate_path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                blob = json.load(fh)
            secs = [float(x) for x in (blob.get("samples") or []) if float(x) > 0]
            with _eval_secs_lock:
                _EVAL_SECS.extend(secs[-_EVAL_SECS_MAX:])
    except Exception as _e:  # noqa: BLE001
        log.debug("no persisted eval rate: %s", _e)


def _save_eval_rate() -> None:
    try:
        with _eval_secs_lock:
            samples = list(_EVAL_SECS)
        tmp = _eval_rate_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"samples": samples[-_EVAL_SECS_MAX:]}, fh)
        os.replace(tmp, _eval_rate_path())
    except Exception as _e:  # noqa: BLE001
        log.debug("could not persist eval rate: %s", _e)


def _record_eval_seconds(dt: float) -> None:
    """Time ONE subprocess eval.  Best-effort; never breaks an eval."""
    try:
        if not (math.isfinite(dt) and dt > 0):
            return
        with _eval_secs_lock:
            _EVAL_SECS.append(float(dt))
            if len(_EVAL_SECS) > _EVAL_SECS_MAX:
                del _EVAL_SECS[:-_EVAL_SECS_MAX]
            n = len(_EVAL_SECS)
        if n % _EVAL_RATE_FLUSH_EVERY == 0:
            _save_eval_rate()
    except Exception:  # noqa: BLE001
        pass


# Fallback quote when this machine has never measured an eval.  A P2 honest eval
# is ~2× a plain transient at the same frame count; the perf work put a warm
# frame at ~1.2 s, so ~2.4 s/frame is the standing estimate.
_EVAL_S_PER_FRAME_DEFAULT = 2.4


def measured_eval_seconds(steps_per_period: int = 36) -> Dict[str, Any]:
    """Median measured seconds per FEM eval + how many samples back it.

    Returns ``{"s_per_eval", "n_samples", "source"}``.  source='measured' when
    this machine has timed evals, 'estimate' when the frame-count fallback is
    used — the caller SHOWS which, because a quote nobody measured is a guess
    and must not be printed as a measurement."""
    with _eval_secs_lock:
        samples = sorted(_EVAL_SECS)
    if samples:
        mid = len(samples) // 2
        med = (samples[mid] if len(samples) % 2
               else 0.5 * (samples[mid - 1] + samples[mid]))
        return {"s_per_eval": round(float(med), 2), "n_samples": len(samples),
                "source": "measured"}
    return {"s_per_eval": round(_EVAL_S_PER_FRAME_DEFAULT * max(4, int(steps_per_period)), 2),
            "n_samples": 0, "source": "estimate"}


_load_eval_rate()


# ── One eval = one core, for real this time ──────────────────────────────────
# The pool above runs _SCAN_WORKERS eval subprocesses at once "each pinned to a
# single core".  Nothing was actually pinning them: BLAS/LAPACK inside each
# subprocess opened its own thread pool sized to the whole machine, so 10
# concurrent evals asked for ~10x the cores that exist and every one of them ran
# at a fraction of its solo speed.
#
# MEASURED (2026-08-05, 12 physical cores, 10 workers, the CIANO20 150_35 at
# 48 frames): a screening wave of 10 evals had not returned a single result
# after 18 minutes, while one eval running alongside it finished in 5.7 — the
# pool was thrashing, not computing.  Pinning each subprocess to one thread and
# letting the POOL provide the parallelism is what the design already claimed.
#
# It also buys reproducibility: a threaded BLAS reduction sums in whatever order
# the threads finish, so the same solve can differ in the last few ulp run to
# run.  At one thread the order is fixed and an eval is bit-identical to itself —
# which is what lets the screening descent treat its finite differences as exact
# and skip paying for replicate evaluations (docs/SCREENING_DESCENT.md).
_EVAL_ENV = dict(os.environ)
_EVAL_ENV.update({k: "1" for k in (
    "MKL_NUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")})


def _nonphysical_result(res: Any) -> Optional[str]:
    """Reason the metrics of an 'ok' eval are physically impossible, else None.

    Bounds are deliberately loose — orders of magnitude, not engineering
    judgement: this gate must never veto a merely BAD design (the optimizer is
    entitled to explore those), only a broken postprocess.  For scale, the best
    aerospace machines sit near ~50 Nm/kg; the cap leaves two orders of
    headroom before calling a number impossible."""
    if not isinstance(res, dict):
        return "result payload is not a dict"
    checks = (
        ("T_em_Nm",               lambda v: abs(v) < 1e4),
        ("mass_total_kg",         lambda v: 0.0 < v < 1e4),
        ("efficiency",            lambda v: 0.0 < v <= 1.0),
        ("torque_per_mass_Nm_kg", lambda v: abs(v) < 5e3),
        ("T_ripple_pct",          lambda v: 0.0 <= v < 1e4),
        ("P_loss_total_W",        lambda v: 0.0 <= v < 1e7),
    )
    for key, ok_fn in checks:
        v = res.get(key)
        if v is None:
            continue                    # absent metric is the consumer's problem
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "{} is not a number: {!r}".format(key, v)
        if not math.isfinite(f):
            return "{} is not finite: {!r}".format(key, v)
        if not ok_fn(f):
            return "{} = {:g} is outside any physical range".format(key, f)
    return None


def _subprocess_eval(overrides: Dict[str, float], current_a: float, steps: int,
                     coil_temp_c: float, n_periods: float = 1.0,
                     gamma_deg: float = 0.0, mesh_size_mm: float = 4.0,
                     min_size_mm: float = 0.3, n_sectors: int = -1,
                     _log: bool = True, pole_copy=None, torque_filter=False,
                     gap_layers: float = 3.0, end_winding_factor: float = 0.0,
                     rotor_eddy: bool = False, hi_fidelity: bool = False,
                     structured_gap: bool = False, airgap_macro: bool = False,
                     iron_template: bool = True, geo_mesh: bool = True,
                     element_order: int = 2,
                     rpm: Optional[float] = None,
                     n_parallel: Optional[int] = None,
                     connection: Optional[str] = None,
                     demag: Optional[bool] = None) -> Dict[str, Any]:
    """Evaluate ONE (geometry, current, γ) with the real sliding-band transient
    in an isolated subprocess (FEM/LLVM crash → failed design, not a dead API).
    Rebuilds the CadQuery geometry + gmsh mesh for the candidate in-memory.
    ``steps`` frames over ``n_periods`` of the electrical period.  n_sectors=-1
    = full disk (accurate ripple); 4 = ¼ sector (≈3× faster, for quick debug)."""
    import subprocess, sys, json, os as _os
    spec = json.dumps({"overrides": overrides, "current_a": current_a,
                       "steps": int(steps), "coil_temp_c": float(coil_temp_c),
                       "n_periods": float(n_periods), "gamma_deg": float(gamma_deg),
                       "mesh_size_mm": float(mesh_size_mm), "min_size_mm": float(min_size_mm),
                       "n_sectors": int(n_sectors), "pole_copy": pole_copy,
                       "torque_filter": bool(torque_filter),
                       "gap_layers": float(gap_layers),
                       "end_winding_factor": float(end_winding_factor),
                       "rotor_eddy": bool(rotor_eddy),
                       "hi_fidelity": bool(hi_fidelity),
                       "structured_gap": bool(structured_gap),
                       "airgap_macro": bool(airgap_macro),
                       "iron_template": bool(iron_template),
                       "geo_mesh": bool(geo_mesh),
                       "element_order": int(element_order),
                       # DEMAG: omitted (None) = the candidate subprocess falls
                       # back to the active config's simulation.demag, exactly
                       # as before.  Passed, the caller's flag wins.
                       **({} if demag is None else {"demag": bool(demag)}),
                       # SPEED: omitted (None) = the candidate subprocess reads
                       # the active config, exactly as before.  Passed, it pins
                       # the eval's speed so the solver's f_elec cannot drift
                       # from the operating point the caller means (F2).
                       **({} if rpm is None else {"rpm": float(rpm)}),
                       # WINDING: same rule — omitted = the candidate subprocess
                       # reads the active config's connection (F3).
                       **({} if n_parallel is None
                          else {"n_parallel": int(n_parallel)}),
                       **({} if connection is None
                          else {"connection": str(connection)})})
    import time as _t_eval
    _t0_eval = _t_eval.monotonic()
    try:
        # Adaptive hang cap: 4x the MEASURED median eval, floored at 300 s and
        # capped at 30 min.  A fixed 300 s assumed "healthy = 1-2 min", but with
        # 10 concurrent evals the same solve stretches to a 231 s median (CPU
        # contention), and the fixed cap then rejected healthy candidates as
        # hangs — 8 of 12 in one generation, which starved CMA-ES into a false
        # flat-fitness stop.  Pathological meshes no longer need the cap anyway:
        # the mesh triangle budget kills them in seconds.
        try:
            _med = float(measured_eval_seconds().get("s_per_eval") or 0.0)
        except Exception:  # noqa: BLE001
            _med = 0.0
        # …and a floor that KNOWS WHAT THIS EVAL COSTS.  The measured median is
        # empty on the first generation, and the 300 s floor was sized for a
        # magnetostatic 24-step point.  Turning on the switches the Simulation
        # tab uses changes that by an order of magnitude: demag prepends a whole
        # settling period and the coupled eddy solve adds its warm-up, so a
        # 40-step point solves ~123 frames, and ten of those run concurrently on
        # the same cores.  With the flat floor every single point of a sweep came
        # back "timeout" — 10 of 10 — and the chart was empty with nothing saying
        # why.  Estimate the frames this eval will actually solve, price them at
        # the measured per-frame rate (or a conservative 2 s), and let the
        # concurrency stretch that.
        _frames = float(steps) * float(n_periods)
        if demag:
            _frames += float(steps)          # full-period settling pre-pass
        _frames += 43.0 if rotor_eddy else 0.0   # eddy warm-up, measured
        _per_frame = float(_EVAL_S_PER_FRAME_DEFAULT)
        # Contention, not worker count: each eval is pinned to one thread and the
        # POOL provides the parallelism, so ten workers do not make one eval ten
        # times slower — the measured stretch on this machine was ~4x (60 s solo
        # -> 231 s median at 10 concurrent).
        _contention = min(4.0, max(1.0, float(_scan_worker_count()) / 2.5))
        _expect = _frames * _per_frame * _contention
        _cap = min(3600.0, max(300.0, 4.0 * _med, 3.0 * _expect))
        proc = subprocess.run(
            [sys.executable, "-m", "motor_ai_sim.optimization.refine_proc"],
            input=spec, capture_output=True, text=True, timeout=_cap,
            env=_EVAL_ENV)
        _record_eval_seconds(_t_eval.monotonic() - _t0_eval)
        out = proc.stdout or ""
        m = out.rfind("@@RESULT@@")
        if m >= 0:
            _res = json.loads(out[m + len("@@RESULT@@"):])
            if _res.get("ok"):
                # Output sanity gate.  A degenerate-but-buildable candidate can
                # solve and still produce garbage metrics (seen live: a wafer
                # machine reported ok with td = -7.7e9 Nm/kg and efficiency
                # exactly 0.0 — the point then stretched every chart axis).
                # A non-physical RESULT is a rejected eval, said out loud —
                # never data.
                _bad = _nonphysical_result(_res.get("res"))
                if _bad:
                    log.warning("eval REJECTED (non-physical result): %s | "
                                "I=%.3g A gamma=%.3g deg overrides=%s",
                                _bad, current_a, gamma_deg, overrides)
                    _res = {"ok": False,
                            "error": "non-physical result: " + _bad}
            if not _res.get("ok") and "unconverged FEM frames" in str(_res.get("error", "")):
                # Say it out loud and name the frames.  A rejected candidate that
                # vanishes with a generic "eval failed" looks like a mesh problem;
                # this one is the solver telling us the field is not usable.
                log.warning("eval REJECTED (nonlinear solve): %s | I=%.3g A "
                            "gamma=%.3g deg overrides=%s",
                            _res.get("error"), current_a, gamma_deg, overrides)
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
    torque_filter: bool = False            # band-limit ripple — honest default: RAW
    n_sectors: int = 1                     # FEM symmetry — SINGLE SOURCE: Mesh tab (same build as Simulation)
    gap_layers: float = 3.0                # air-gap mesh layers — SINGLE SOURCE: Mesh tab (drives ripple/eddy; match Simulation)
    end_winding_factor: float = 0.0        # end-winding k_end — SINGLE SOURCE: Simulation (drives copper loss / eff; 0 = auto)
    rotor_eddy: bool = False                # field-based magnet/shaft eddy — SINGLE SOURCE: Simulation (drives magnet loss / eff vs slab estimate)
    hi_fidelity: bool = False               # 2× slip nodes + finer mesh + ≥4 gap layers — SINGLE SOURCE: Mesh tab (smoother raw torque, ~3-5× slower)
    structured_gap: bool = False            # belt (mapped concentric-ring) gap mesh — SINGLE SOURCE: Mesh tab "Structured"; honest ripple, ¼-sector == full disk
    airgap_macro: bool = False              # harmonic gap coupling — SINGLE SOURCE: Mesh tab "Harmonic gap"; RAW ripple step-independent (full ring + sectors)
    iron_template: bool = True              # deterministic template iron mesh (fallback: gmsh)
    geo_mesh: bool = True                   # geometry-driven CDT mesh — SINGLE SOURCE: Mesh tab (same build as Simulation)
    element_order: int = 2                  # 2 = P2 — the only basis: energy-consistent mean torque + mesh-convergent ripple.  P1 is deleted; any other value raises.
    demag: bool = False                     # per-element irreversible demagnetisation — SINGLE SOURCE: Simulation (de-rates Br → torque/EMF; doubles the frames per point)
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


def _scan_owns(run_id) -> bool:
    """True while THIS run still owns the shared scan state.

    There is ONE `_scan_state` and it was written by whoever was running.  Stop
    + Run starts run B while run A is still winding down (A only notices the
    cancel between tasks, and with ten concurrent evals that can be minutes), and
    then A's `finally` cleared `running` for B: the panel saw running=False with
    `done` still climbing, stopped waiting, and showed nothing at all while 10 of
    13 points had already been computed.  Progress, points, the result and the
    running flag now belong to a named run, and a run that no longer owns the
    state keeps its hands off it.  Caller holds `_scan_lock`.
    """
    return str(_scan_state.get("run_id") or "") == str(run_id or "")


def _scan_worker(variables, operating_points, steps, coil_temp_c, ripple_max,
                 max_geom, seed, run_id, mesh_size_mm=4.0, min_size_mm=0.3,
                 pole_copy=None, torque_filter=False, n_sectors=1, gap_layers=3.0,
                 end_winding=0.0, rotor_eddy=False, hi_fidelity=False,
                 structured_gap=False, airgap_macro=False, iron_template=True,
                 geo_mesh=True, element_order=2, demag=False) -> None:
    import numpy as np  # noqa: F401
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
    from motor_ai_sim.optimization.optimizer import _pareto_front
    try:
        geos = _enumerate_geometries(variables, int(max_geom), n_opt=4, seed=seed)
        tasks = []  # (geom_id, op_index, overrides, current, op_gamma)
        for gi, ov in enumerate(geos):
            for oi, op in enumerate(operating_points):
                tasks.append((gi, oi, ov, float(op.get("current_a", 85.0)),
                              float(op.get("gamma_deg", 0.0))))
        with _scan_lock:
            if not _scan_owns(run_id):
                return                      # superseded before we even started
            _scan_state.update(total=len(tasks) + 1, done=0)
        points: List[Any] = [None] * len(tasks)

        # Sweep a FULL electrical period: the iron/magnet eddy losses are
        # computed from dB/dt and need the whole period for a correct frequency
        # content (a 1/6-period window inflated them ~2-5× → wrong efficiency).
        # ``steps`` frames/period — 18 gives 3 samples per 6·k ripple cycle.
        _NPER = 1.0
        _cfg_fp = _config_fingerprint()   # constant for this scan → one fingerprint for every point

        def _cache_key(geo_ov, I, g):
            return _eval_cache_key(geo_ov, I, steps, coil_temp_c, _NPER, g,
                                 mesh_size_mm, min_size_mm, n_sectors, pole_copy, torque_filter, _cfg_fp,
                                 gap_layers, end_winding, rotor_eddy, hi_fidelity, structured_gap, airgap_macro,
                                 iron_template=iron_template, geo_mesh=geo_mesh,
                                 element_order=element_order, demag=demag)

        def _mk_point(out, ov, I, gi, oi, g):
            pt = _point_from_eval(out, ov, I, gi, oi, ripple_max)
            pt["gamma_deg"] = g    # stamp γ so the chart can group/connect without the request
            return pt

        def _do(i_t):
            # COMPUTE path — only cache MISSES reach here (hits are pre-filled
            # before the pool starts, see below).  γ as a swept variable
            # overrides the operating-point γ; it is NOT a geometry key.
            i, (gi, oi, ov, I, opg) = i_t
            g = float(ov.get("gamma_deg", opg))
            geo_ov = {k: v for k, v in ov.items() if k != "gamma_deg"}
            out = _subprocess_eval(geo_ov, I, steps, coil_temp_c,
                                   n_periods=_NPER, gamma_deg=g,
                                   mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                                   pole_copy=pole_copy, torque_filter=torque_filter,
                                   n_sectors=n_sectors, gap_layers=gap_layers,
                                   end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                   hi_fidelity=hi_fidelity, structured_gap=structured_gap,
                                   airgap_macro=airgap_macro,
                                   iron_template=iron_template, geo_mesh=geo_mesh,
                                   element_order=element_order, demag=demag)
            if out and out.get("ok"):
                _store_eval(_cache_key(geo_ov, I, g), out)   # cache successful evals only
            return i, _mk_point(out, ov, I, gi, oi, g)

        # PREFILL: instantly plot every point already in the cache (from prior
        # sweeps — survives a backend restart via .scan_cache.jsonl) with NO FEM
        # re-run, then compute ONLY the misses.  This is what makes a re-run's
        # already-computed points appear on the chart immediately (0 s) instead
        # of trickling through the worker, and shrinks the work to the new points.
        misses = []
        for i, t in enumerate(tasks):
            gi, oi, ov, I, opg = t
            g = float(ov.get("gamma_deg", opg))
            geo_ov = {k: v for k, v in ov.items() if k != "gamma_deg"}
            out = _EVAL_CACHE.get(_cache_key(geo_ov, I, g))
            if out is not None:
                points[i] = _mk_point(out, ov, I, gi, oi, g)
            else:
                misses.append((i, t))
        n_cached = sum(1 for p in points if p is not None)
        with _scan_lock:
            if _scan_owns(run_id):
                _scan_state["cached"] = n_cached
                _scan_state["done"] = n_cached
                _scan_state["points"] = [p for p in points if p is not None]   # instant plot

        # Manual executor so a Stop can cancel the not-yet-started tasks (the
        # ~5 already-running subprocesses just finish in the background); the
        # partial results computed so far are kept and shown.
        ex = ThreadPoolExecutor(max_workers=_SCAN_WORKERS)
        futs = [ex.submit(_do, it) for it in misses]
        done = n_cached
        try:
            # Poll with a 1 s timeout instead of blocking on as_completed(): a Stop
            # must take effect within ~1 s EVEN IF the in-flight subprocess evals
            # are slow or hung — otherwise as_completed() blocks waiting for a
            # future that never completes, the cancel is never seen, and
            # _scan_state["running"] stays True (→ "a scan is already running" 409
            # on the next Run).  On cancel we stop harvesting and shut down; the
            # ~few running subprocesses finish/timeout on their own in the
            # background and are ignored.
            pending = set(futs)
            while pending:
                if _scan_state["cancel"]:
                    break
                just_done, pending = wait(pending, timeout=1.0,
                                          return_when=FIRST_COMPLETED)
                for fut in just_done:
                    try:
                        i, pt = fut.result()
                    except Exception:      # a single dead eval must not stall the sweep
                        continue
                    points[i] = pt
                    done += 1
                with _scan_lock:
                    if _scan_owns(run_id):
                        _scan_state["done"] = done
                    # live-stream the points computed so far so the chart fills in
                    # as the sweep runs (not only at the end).
                    if _scan_owns(run_id):
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
                                   gap_layers, end_winding, rotor_eddy, hi_fidelity, structured_gap, airgap_macro,
                                 iron_template=iron_template, geo_mesh=geo_mesh,
                                 element_order=element_order, demag=demag)
            base_out = _EVAL_CACHE.get(_bck)
            if base_out is None:
                base_out = _subprocess_eval({}, _bI, steps, coil_temp_c, n_periods=_NPER,
                                            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                                            pole_copy=pole_copy, torque_filter=torque_filter,
                                            n_sectors=n_sectors, gap_layers=gap_layers,
                                            end_winding_factor=end_winding, rotor_eddy=rotor_eddy,
                                            hi_fidelity=hi_fidelity, structured_gap=structured_gap,
                                       airgap_macro=airgap_macro,
                                       iron_template=iron_template, geo_mesh=geo_mesh,
                                       element_order=element_order, demag=demag)
                if base_out and base_out.get("ok"):
                    _store_eval(_bck, base_out)
            baseline = _point_from_eval(base_out, {}, _bI, -1, 0, ripple_max)
        with _scan_lock:
            if _scan_owns(run_id):
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
            # WHICH RUN THIS IS.  The panel polls one endpoint for "is my run
            # done" and "here is the result", and those were only ever the same
            # thing by luck: after a restart the state holds the RESTORED last
            # scan with running=False, so a poll that arrives before the new run
            # flips the flag reads someone else's result as its own — a sweep of
            # magnet_height/rotor_hole showed a table of slot_height points.
            # Stamped here so the client can refuse a result it did not ask for.
            "run_id": str(run_id or ""),
            # solver params for this scan → lets the cache be re-seeded from this
            # result later with the exact same key inputs (see /scan/seed_cache).
            "scan_params": {"coil_temp_c": float(coil_temp_c), "mesh_size_mm": float(mesh_size_mm),
                            "min_size_mm": float(min_size_mm), "pole_copy": pole_copy,
                            "torque_filter": bool(torque_filter)},
        }
        with _scan_lock:
            if _scan_owns(run_id):
                _scan_state["result"] = result
        _save_last_scan(result)   # persist so it survives reload / restart
    except Exception as e:  # noqa: BLE001
        log.exception("FEM scan failed")
        with _scan_lock:
            if _scan_owns(run_id):
                _scan_state["error"] = str(e)
    finally:
        with _scan_lock:
            # ONLY if we are still the current run — otherwise this clears the
            # flag for the run that superseded us, which is the bug above.
            if _scan_owns(run_id):
                _scan_state["running"] = False


@router.post("/scan")
def scan_designs(req: ScanRequest):
    """Start a background FEM scan — every point is a real transient."""
    global _scan_thread
    # Start guard (done OUTSIDE the param lock so the wait can't deadlock the
    # worker's finally, which needs _scan_lock to clear "running"):
    #  • genuinely running scan, no Stop pending → 409;
    #  • Stop already requested (cancel set) → the worker is winding down; WAIT
    #    up to ~6 s for it to finish, then start fresh — so "Stop then Run" is
    #    NOT a race that 409s (the reported bug);
    #  • dead/stale worker → clear the flag and start.
    import time as _time
    _deadline = _time.time() + 6.0
    while True:
        with _scan_lock:
            _running = _scan_state["running"]
            _alive = _scan_thread is not None and _scan_thread.is_alive()
            _cancelling = bool(_scan_state.get("cancel"))
            if not _running or not _alive:
                _scan_state["running"] = False          # clear stale / dead worker
                break
            if not _cancelling:
                raise HTTPException(status_code=409, detail="a scan is already running")
            # else: a Stop is in progress — fall through and wait for wind-down
        if _time.time() >= _deadline:
            with _scan_lock:
                _scan_state["running"] = False           # took too long → force clear
            break
        _time.sleep(0.15)
    with _scan_lock:
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
        # Torque RIPPLE is a high-harmonic quantity — the 12s14p cogging sits at the
        # 12th electrical harmonic, so < ~48 frames/period UNDERSAMPLES and ALIASES
        # it.  The aliasing is geometry-dependent, so a coarse step count produced
        # SPURIOUS ripple minima (e.g. magnet_fill_up=0.40 read 4.4 % at 24 steps
        # but 5.4 % at 48) → the sweep "found" ripple wins that vanish at Simulation
        # resolution.  For honest-ripple runs (P2 / structured belt) floor the frame
        # count so the sweep's ripple equals the converged value.  Mean torque is
        # step-insensitive, so this only fixes ripple; mesh is already converged at
        # the 1.0 mm floor (1.0 and 0.5 mm agree to 0.03 % at 48 steps).
        if int(getattr(req, "element_order", 2)) == 2 or bool(req.structured_gap):
            steps = max(steps, 48)
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
        # Belt (mapped concentric-ring) gap mesh — single source: the Mesh tab
        # "Structured" toggle.  Honest ripple (¼-sector == full disk), same build
        # the Simulation tab uses.
        structured_gap = bool(req.structured_gap)
        airgap_macro = bool(req.airgap_macro)
        iron_template = bool(getattr(req, 'iron_template', True))
        # Geometry-driven CDT mesh — single source: the Mesh tab toggle.  The
        # Simulation sends it explicitly; without it the sweep fell back to the
        # gmsh moving mesh under airgap_macro (template+macro raises) and its
        # assembly noise read as 58-75% ripple vs the Simulation's honest value.
        geo_mesh = bool(getattr(req, 'geo_mesh', True))
        # P2 elements.  refine_proc.run_one applies the same coercions the
        # Simulation route does (force belt, auto natural-symmetry sector, keep
        # rotor_eddy), so a sweep point matches a Simulation run at the SAME
        # geometry/operating point.  P2 is the only basis, so this is a constant
        # in all but name; kept as a field because the request carries it.
        element_order = int(getattr(req, 'element_order', 2) or 2)
        # Per-element irreversible demagnetisation — single source: the
        # Simulation tab's toggle.  It de-rates Br, i.e. it changes the machine,
        # so a sweep that ignores it ranks full-strength designs against a
        # Simulation the user is reading de-rated.  Costs a whole extra period
        # of frames per point.
        demag = bool(getattr(req, 'demag', False))
        _scan_state.update({"running": True, "done": 0, "total": 0, "result": None,
                            "points": [], "run_id": req.run_id, "error": None, "cancel": False,
                            "cached": 0})
    _scan_thread = threading.Thread(target=_scan_worker,
                     args=(variables, ops, steps, float(req.coil_temp_c),
                           float(req.ripple_max_pct), max_geom, int(req.seed), req.run_id,
                           mesh_size, min_size, req.pole_copy, bool(req.torque_filter),
                           n_sectors, gap_layers, end_winding, rotor_eddy, hi_fidelity,
                           structured_gap, airgap_macro, iron_template, geo_mesh,
                           element_order, demag),
                     daemon=True)
    _scan_thread.start()
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
    # Set the cancel flag (the worker's 1 s poll loop sees it and stops within
    # ~1 s).  Belt-and-suspenders: if the worker thread is already dead but the
    # flag is stale, clear "running" here too so the next Run isn't blocked.
    with _scan_lock:
        _scan_state["cancel"] = True
        if _scan_thread is None or not _scan_thread.is_alive():
            _scan_state["running"] = False
    return {"cancelled": True}


class SeedCacheRequest(BaseModel):
    # Only used for OLD results that predate scan_params storage; new results carry
    # their own params (which take precedence) so the keys match exactly.
    coil_temp_c: float = 120.0
    mesh_size_mm: float = 4.0
    min_size_mm: float = 0.3
    pole_copy: Optional[bool] = None
    torque_filter: bool = False


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
    torque_filter: bool = False           # band-limit ripple — honest default: RAW


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
            # WHICH MACHINE produced this row.  ``overrides`` is a DELTA on the
            # active baseline geometry, so without the baseline's fingerprint a
            # row from a 40 mm motor is indistinguishable from a 30 mm one — and
            # warm-starting a search from another machine's optimum is exactly
            # the stale-machine failure this repo refuses everywhere else.  Same
            # fingerprint the FEM eval cache is keyed by (geometry + winding +
            # materials + magnet + speed; operating point deliberately excluded,
            # it travels next to it as current_a / gamma_deg).
            "cfg_fp": _config_fingerprint(),
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


def _ov_sig(ov: Dict[str, Any]) -> tuple:
    """Identity of a geometry: its override map, γ excluded (γ is the operating
    point, and it travels next to the design, not inside it)."""
    out = []
    for k, v in (ov or {}).items():
        if k == "gamma_deg":
            continue
        f = _pareto._f(v)
        if f is None:
            continue
        out.append((str(k), round(f, 9)))
    return tuple(sorted(out))


def _backfill_point_metrics(points: List[Dict[str, Any]]) -> int:
    """Restore `torque` / `mass` on cloud points published before `_pt` carried
    them (runs started before 2026-08-05 — including the 110-eval run whose card
    then drew ZERO dots, because the chart keys on torque).

    Where from: `config/.opt_dataset.jsonl`, which logs every FEM eval with its
    overrides, current AND torque/mass.  That file is the CROSS-RUN accumulator,
    so it is used here for ONE thing only — filling two missing fields on a point
    that is already in this run's own array.  The match key is exact: same
    override map, same current, and the eff / td / ripple it already carries must
    agree to 1e-9.  No new point is ever added, and a point that does not match
    is left alone.  Returns how many were repaired."""
    need = [p for p in points or []
            if isinstance(p, dict) and p.get("overrides")
            and (p.get("torque") is None or p.get("mass") is None)]
    if not need:
        return 0
    try:
        path = _dataset_path()
        if not _os_o.path.exists(path):
            return 0
        idx: Dict[tuple, List[Dict[str, Any]]] = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = _json_o.loads(line)
                except Exception:      # noqa: BLE001 — a torn last line
                    continue
                cur = _pareto._f(r.get("current_a"))
                if cur is None:
                    continue
                idx.setdefault((_ov_sig(r.get("overrides") or {}), round(cur, 6)),
                               []).append(r)
    except Exception as _e:            # noqa: BLE001
        log.debug("point backfill unavailable: %s", _e)
        return 0
    fixed = 0
    for p in need:
        cur = _pareto._f(p.get("current_a"))
        if cur is None:
            continue
        rows = idx.get((_ov_sig(p.get("overrides") or {}), round(cur, 6))) or []
        hit = None
        for r in rows:
            same = True
            for pk, rk in (("eff", "eff"), ("td", "td"), ("ripple", "ripple")):
                a, b = _pareto._f(p.get(pk)), _pareto._f(r.get(rk))
                if a is None or b is None or abs(a - b) > 1e-9:
                    same = False
                    break
            if same:
                hit = r
                break
        if hit is None:
            continue
        if p.get("torque") is None and _pareto._f(hit.get("torque")) is not None:
            p["torque"] = _pareto._f(hit.get("torque"))
        if p.get("mass") is None and _pareto._f(hit.get("mass")) is not None:
            p["mass"] = _pareto._f(hit.get("mass"))
        fixed += 1
    if fixed:
        log.info("restored torque/mass on %d cloud point(s) from the eval log", fixed)
    return fixed


def _load_descent_state() -> None:
    try:
        p = _descent_store_path()
        if not _os_o.path.exists(p):
            return
        with open(p, encoding="utf-8") as fh:
            blob = _json_o.load(fh)
        if isinstance(blob, dict):
            _backfill_point_metrics(blob.get("points") or [])
            _descent_state.update(blob)
            _descent_state["running"] = False   # a reloaded run is not in flight
            _descent_state["cancel"] = False
            log.info("restored last optimization from %s", p)
    except Exception as _e:   # noqa: BLE001
        log.warning("could not restore descent state: %s", _e)


_load_descent_state()   # repopulate at import (startup)

# mtime of the checkpoint the in-memory state was last taken from
_descent_disk_mtime = [0.0]
# True while the in-memory `running` flag was ADOPTED from the checkpoint file
# (a run hosted outside this process) rather than owned by a worker thread in
# this process.  An adopted flag must expire with the file — see
# _refresh_descent_state_from_disk.
_descent_external = [False]


def _refresh_descent_state_from_disk() -> None:
    """Adopt a checkpoint written by a run hosted OUTSIDE this process.

    The optimizer is a plain function, so a long run can be driven from its own
    process to survive a web-server restart — and `_cmaes_worker` checkpoints to
    this same file every generation.  Without picking that up, the progress
    endpoint would keep serving the snapshot taken at import and the UI would look
    frozen for the whole run.  The file's `running` flag is only believed while the
    file is still being touched: a stale one means that process is gone.
    """
    import time as _t
    try:
        p = _descent_store_path()
        m = _os_o.path.getmtime(p)
    except Exception:      # noqa: BLE001 — no checkpoint yet
        return
    fresh = (_t.time() - m) < 300.0
    if m <= _descent_disk_mtime[0]:
        # No new checkpoint since the last look.  An ADOPTED `running` must
        # expire with the file: the hosting process checkpoints every
        # generation, so a silent file means that process is gone.  Without
        # this, a crashed run's last checkpoint (written with running=True)
        # latched the flag forever — the refresh that would correct it only
        # ran while running was False.  Seen live: a backend restart mid-run
        # reported "running, iter 9/9" for a day.
        if _descent_external[0] and not fresh and _descent_state.get("running"):
            _descent_state["running"] = False
            _descent_external[0] = False
            if not _descent_state.get("error"):
                _descent_state["error"] = (
                    "the optimizer process stopped without finishing — its "
                    "checkpoint went silent. The numbers shown are the last "
                    "generation it completed, not a final result.")
            log.warning("adopted optimizer run declared dead: checkpoint %s "
                        "silent for %.0f s", p, _t.time() - m)
        return
    _descent_disk_mtime[0] = m
    try:
        with open(p, encoding="utf-8") as fh:
            blob = _json_o.load(fh)
    except Exception as _e:   # noqa: BLE001 — mid-write; the next poll retries
        log.debug("descent checkpoint unreadable: %s", _e)
        return
    if not isinstance(blob, dict):
        return
    _backfill_point_metrics(blob.get("points") or [])
    _descent_state.update(blob)
    _descent_state["running"] = bool(blob.get("running")) and fresh
    _descent_external[0] = bool(_descent_state["running"])
    _descent_state["cancel"] = False


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
    # Belt (mapped concentric-ring) gap mesh — SINGLE SOURCE: the Mesh tab
    # "Structured" toggle.  Honest ripple (¼-sector == full disk), same build
    # as the Simulation tab.
    structured_gap: bool = False
    # Harmonic gap coupling — SINGLE SOURCE: Mesh tab "Harmonic gap".  RAW
    # ripple becomes step-count independent; full ring AND sector models.
    airgap_macro: bool = False
    iron_template: bool = True
    # Geometry-driven CDT mesh — SINGLE SOURCE: Mesh tab (same build as Simulation).
    geo_mesh: bool = True
    # P2 quadratic elements — the only basis.  Each candidate is SCORED on the
    # honest ripple, so a ripple-gated optimization targets the real ripple and
    # not the retired P1 basis's mesh staircase.  run_one applies the P2 belt +
    # natural-symmetry-sector coercions per eval.
    element_order: int = 2
    # Pole/slot mesh mode from the UI (Mesh tab "Periodic (identical poles)").
    # None = solver env default; the optimizer must mesh the SAME way Simulation does.
    pole_copy: Optional[bool] = None
    # Band-limit T(t) to the physical 6·k orders — from Simulation's torque-filter toggle.
    torque_filter: bool = False
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
    structured_gap: bool = False   # Mesh tab "Structured" toggle (belt gap mesh)
    airgap_macro: bool = False     # Mesh tab "Harmonic gap" (step-independent RAW ripple; full ring + sectors)
    iron_template: bool = True     # deterministic template iron mesh (fallback: gmsh)
    geo_mesh: bool = True          # geometry-driven CDT mesh (Mesh tab; same as Simulation)
    pole_copy: Optional[bool] = None
    torque_filter: bool = False
    rotor_eddy: bool = True
    end_winding_factor: float = 0.0
    n_sectors: int = -1
    element_order: int = 2         # P2 by default — the baseline line must use the same basis as the descent


def _msum(m: Dict[str, Any]) -> Dict[str, Any]:
    """Compact metric snapshot for history / progress."""
    return {
        "T_em_Nm":         m.get("T_em_Nm"),
        "efficiency":      m.get("efficiency"),
        "torque_per_mass": m.get("torque_per_mass_Nm_kg"),
        "T_ripple_pct":    m.get("T_ripple_pct"),
        "mass_total_kg":   m.get("mass_total_kg"),
        "P_loss_total_W":  m.get("P_loss_total_W"),
        # The loss BREAKDOWN, thermal loading and densities the eval already
        # computes.  Dropping them here used to leave every optimizer design with
        # blank Fe/Cu/magnet/J columns in Compare, so an optimum could not be read
        # against a Simulation run of the same geometry.
        "P_core_W":        m.get("P_core_W"),        # laminated iron
        "P_stranded_W":    m.get("P_stranded_W"),    # copper
        "P_solid_W":       m.get("P_solid_W"),       # magnet + shaft eddy
        "P_mech_W":        m.get("P_mech_W"),
        "J_coil_A_per_mm2": m.get("J_coil_A_per_mm2"),
        "KV_rpm_per_V_line": m.get("KV_rpm_per_V_line"),
        "power_per_mass_W_kg": m.get("power_per_mass_W_kg"),
        "loss_density_W_kg":   m.get("loss_density_W_kg"),
        "V_peak":          m.get("V_peak"),
        "V_line_peak_V":   m.get("V_line_peak_V"),   # DC-bus sizing number
        "I_phase_rms_A":   m.get("I_phase_rms_A"),
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
            # Shaft torque — the number the user judges a design by.  Without it
            # the auto card's torque×ripple cloud could only plot the per-generation
            # BEST history (6 rows), not the run's 50+ measured designs.
            "torque": r.get("T_em_Nm"),
            # Mass travels with the point too: torque and torque density differ
            # ONLY by it, so with all three present the Pareto report can state
            # both axes for the same design (and a point that somehow lost one
            # of them can still be reconstructed instead of dropped).
            "mass": r.get("mass_total_kg"),
            "thd": r.get("THD_LL_pct"),      # line-to-line voltage THD (FOC quality)
            "overrides": {k: v for k, v in ov.items() if k != "gamma_deg"},
            "current_a": out.get("current_a") or r.get("current_a"),
            "gamma_deg": ov.get("gamma_deg")}


# ─────────────────────────────────────────────────────────────────────────────
# OBJECTIVE-SPACE CLOUD — publication policy.
#
# INVARIANT (Vadim, 2026-08-05: «надо выводить все точки я потом могу отфильтровать
# их по пульсации там же есть ползунок»): EVERY eval that produced metrics is
# published to `points`.  Nothing is filtered server-side — not by ripple, not by
# the objective, not by "is it the incumbent".  The chart's ripple slider does the
# trimming, visually, where the user can move it.  A run that shows the user 51 of
# its 52 measured designs makes them conclude it found nothing.
#
# The only things NOT in the cloud are things that have no metrics to plot:
#   • candidates the in-process geometry pre-fence rejected (no FEM ran),
#   • evals that failed in the FEM (no `res`),
#   • the MTPA γ-sweep, which is a DIFFERENT operating point — see _descent_cost's
#     note: a design measured at another current/γ is another problem, and mixing
#     it into this run's cloud is exactly the misreading that started this fix.
#
# BOUND: _POINTS_CAP points, ~200 bytes each → ≲1 MB of state, which is also what
# /descent/progress serialises on every poll.  Above the cap the OLDEST ordinary
# point is dropped; the two baseline anchors (A and the current-bumped B that
# define the perpendicular reference line) are never evicted, or the chart loses
# the line it measures everything against.  At ~10 min/eval, 4000 points is ~28
# CPU-days of evaluation — no real run reaches it.
_POINTS_CAP = 4000
_PT_ANCHOR_KINDS = ("baseline", "baseline_bump")


def _pub_pt(all_pts: List[Dict[str, Any]], out: Dict[str, Any],
            kind: str, cap: int = _POINTS_CAP) -> Optional[Dict[str, Any]]:
    """Publish ONE evaluated design into the run's objective-space cloud.

    The single place a point may enter `points`, so the invariant above is
    checkable in one function instead of at nine call sites.  Returns the point
    (or None when the eval carries no metrics)."""
    p = _pt(out, kind)
    if p is None:
        return None
    all_pts.append(p)
    while len(all_pts) > max(2, int(cap)):
        for i, q in enumerate(all_pts):
            if q.get("kind") not in _PT_ANCHOR_KINDS:
                all_pts.pop(i)
                break
        else:                       # nothing but anchors — cannot happen, but
            all_pts.pop(0)          # never loop forever if it does
    return p


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
                      lo=-50.0, hi=0.0, step=5.0, element_order=2):
    """Find the load angle γ that MAXIMISES torque (MTPA) for ONE geometry at a
    reference current — a coarse PARALLEL sweep + parabolic refine.  Run once
    before the geometry search so the whole optimization uses the best phase."""
    from concurrent.futures import ThreadPoolExecutor
    cand = [round(lo + i * step, 1) for i in range(int(round((hi - lo) / step)) + 1)]

    def _one(gc):
        o = _subprocess_eval(geom, ref_I, steps, coil_temp, n_periods=1.0,
                             gamma_deg=float(gc), mesh_size_mm=mesh_size,
                             min_size_mm=min_size, n_sectors=n_sectors,
                             element_order=element_order)
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
                  torque_filter=False, end_winding=0.0, rotor_eddy=True,
                  gap_layers=2.0, objective="baseline_line",
                  current_bump_pct=10.0, structured_gap=False, airgap_macro=False,
                  iron_template=True, geo_mesh=True, element_order=2) -> None:
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
                                 gap_layers=gap_layers, structured_gap=structured_gap,
                                 airgap_macro=airgap_macro,
                                 iron_template=iron_template, geo_mesh=geo_mesh,
                                 element_order=element_order)
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
            _gm = _mtpa_gamma_sweep(x, I, steps, coil_temp, mesh_size, min_size, n_sectors,
                                    element_order=element_order)
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
        all_pts: List[Dict[str, Any]] = []   # every eval with metrics → a cloud point
        _pub_pt(all_pts, b, "baseline")
        # ── Baseline (current-only) line: a 2nd FEM sim of THIS geometry at
        #    I·(1+bump) gives point B; A–B sets the perpendicular-distance weights. ──
        if str(objective) == "baseline_line":
            try:
                bb = _eval_at(x, I * (1.0 + max(0.0, float(current_bump_pct)) / 100.0))
                n_evals += 1
                if bb.get("ok"):
                    base["_bline"] = _make_bline(base, bb["res"], current_bump_pct)
                    # Point B is a fully paid-for eval with metrics; it belongs in
                    # the cloud like any other (it used to be the ONE design every
                    # run measured and never showed).
                    _pub_pt(all_pts, bb, "baseline_bump")
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
        with _descent_lock:
            _descent_external[0] = False   # this process owns the flag now
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
                    _pub_pt(all_pts, out, "grad")         # publish per-eval (real-time chart)
                    with _descent_lock:
                        _descent_state["n_evals"] = n_evals
                        _descent_state["points"] = list(all_pts)
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
                _pub_pt(all_pts, out, "line")
                with _descent_lock:
                    _descent_state["n_evals"] = n_evals
                    _descent_state["points"] = list(all_pts)
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
                  torque_filter=False, end_winding=0.0, rotor_eddy=True,
                  gap_layers=2.0, objective="baseline_line",
                  current_bump_pct=10.0, structured_gap=False, airgap_macro=False,
                  iron_template=True, geo_mesh=True, element_order=2) -> None:
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
                                 gap_layers=gap_layers, structured_gap=structured_gap,
                                 airgap_macro=airgap_macro,
                                 iron_template=iron_template, geo_mesh=geo_mesh,
                                 element_order=element_order)
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
                    _gm = _mtpa_gamma_sweep(to_geom(x0n), I, steps, coil_temp, mesh_size, min_size, n_sectors,
                                            element_order=element_order)
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
                _bb_pub: Optional[Dict[str, Any]] = None
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
                            _bb_pub = bb          # published below, next to point A
                            with _descent_lock:
                                _descent_state["baseline_line"] = dict(base["_bline"])
                    except Exception:   # noqa: BLE001
                        pass
                cost0, F0 = _descent_cost(base, base, ripple_max, w_eff, w_td, lam, v_peak_limit)
                best = {"x": to_geom(x0n), "metrics": base, "cost": cost0, "F": F0}
                history.append(_hrow(0, base, cost0, F0, best["x"]))
                _pub_pt(all_pts, b, "baseline")
                if _bb_pub is not None:
                    _pub_pt(all_pts, _bb_pub, "baseline_bump")

            with _descent_lock:
                _descent_external[0] = False   # this process owns the flag now
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
            #
            # 3 is right for an unconstrained search but impatient for a
            # CONSTRAINED one: with a ripple gate the penalty landscape is rugged
            # and CMA-ES can spend several generations finding a direction that
            # improves the objective WITHOUT breaching the gate.  A ripple<=5%
            # run stopped at generation 3 having sampled 36 points in 15
            # dimensions and reported "no improvement" — far too thin to believe.
            # Raise it with SB_DESCENT_PATIENCE for constrained runs.
            _PATIENCE = max(1, int(os.environ.get("SB_DESCENT_PATIENCE", "3") or 3))
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
                            _pub_pt(all_pts, out, "cmaes")
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
        _descent_external[0] = False   # this process owns the flag now
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
                                   "structured_gap": bool(req.structured_gap),
                                   "airgap_macro": bool(req.airgap_macro),
                                   "iron_template": bool(getattr(req, "iron_template", True)),
                                   "geo_mesh": bool(getattr(req, "geo_mesh", True)),
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
              max(0.0, min(float(req.current_bump_pct), 100.0)),
              bool(req.structured_gap), bool(req.airgap_macro)),
        kwargs={"iron_template": bool(getattr(req, "iron_template", True)),
                "geo_mesh": bool(getattr(req, "geo_mesh", True)),
                "element_order": int(getattr(req, "element_order", 2) or 2)},
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
                                rotor_eddy=bool(req.rotor_eddy), gap_layers=gap,
                                structured_gap=bool(req.structured_gap),
                                airgap_macro=bool(req.airgap_macro),
                                iron_template=bool(getattr(req, "iron_template", True)),
                                geo_mesh=bool(getattr(req, "geo_mesh", True)),
                                element_order=int(getattr(req, "element_order", 2) or 2))

    a = _ev(I)
    if not a.get("ok"):
        raise HTTPException(status_code=500, detail=f"baseline A eval failed: {a.get('error')}")
    b = _ev(I * (1.0 + bump / 100.0))
    if not b.get("ok"):
        raise HTTPException(status_code=500, detail=f"baseline B eval failed: {b.get('error')}")
    a["res"]["current_a"] = I
    b["res"]["current_a"] = I * (1.0 + bump / 100.0)
    return {"baseline_line": _make_bline(a["res"], b["res"], bump)}


def _with_pareto(st: Dict[str, Any]) -> Dict[str, Any]:
    """Annotate a state SNAPSHOT with Pareto-dominance flags + a summary.

    Computed at READ time, from the run's OWN points array, so it also answers
    for runs that finished before this code existed (and cannot drift out of
    sync with the cloud the same payload carries).  See
    motor_ai_sim.optimization.pareto for the predicate, the tolerance and the
    operating-point rule that keeps a candidate from another current/γ out of
    both sets."""
    try:
        pts = st.get("points")
        if not isinstance(pts, list) or not pts:
            return st
        rep = _pareto.report(pts, st.get("baseline") or {}, _pareto.resolve_op(st))
        st["points"] = rep["points"]
        st["pareto"] = rep["summary"]
    except Exception as _e:      # noqa: BLE001 — reporting must never break progress
        log.warning("pareto report failed: %s", _e)
    return st


@router.get("/descent/progress")
def descent_progress():
    with _descent_lock:
        # Nothing in flight HERE does not mean nothing is in flight: a run may be
        # hosted in its own process.  Pick up its checkpoint so the UI tracks it.
        # An externally ADOPTED running flag must keep being re-checked too —
        # that is how it expires when the hosting process dies.
        if not _descent_state.get("running") or _descent_external[0]:
            _refresh_descent_state_from_disk()
        return _json_sane(_with_pareto(dict(_descent_state)))


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


# ═════════════════════════════════════════════════════════════════════════════
# ONE-CLICK AUTO OPTIMIZATION — the user types ONE number (max torque ripple %)
# and presses Run.  Everything else is assembled HERE, from this project's
# standing conventions, so there is no way to launch a run that quietly
# disagrees with them:
#
#   OPERATING POINT  the Simulation tab's current settings (current / rpm / γ /
#                    coil temp), read from the persisted simulation config —
#                    never a raw default.  The user checks the point in
#                    Simulation first; the optimizer must optimise THAT machine
#                    at THAT point.
#   OBJECTIVE        objective="baseline_line" — the signed perpendicular
#                    distance above the current-only baseline line.  Standing
#                    rule: no other single-metric objective is offered here.
#   CONSTRAINT       ripple ≤ the user's number, through the existing penalty
#                    machinery (λ starts at 2 and the continuation ramp
#                    escalates it while the incumbent still breaches the gate).
#   VARIABLES        config sweep_whitelist — the curated set (incl. rotor_hole).
#   EVALS            refine_proc: P2, geometry validation, unconverged rejects,
#                    d-axis per cross-section.  The one honest eval path.
#
# SEARCH RANGE.  The whitelist says WHICH knobs move, not how far.  The window
# is NOT clamped to the schema min/max and not to any UI range: a candidate that
# the geometry validator accepts is a legitimate candidate no matter how far it
# wandered.  The only fence below is physical positivity; everything else is
# decided by validate_geometry (cheap, pre-mesh, inside refine_proc) and by the
# objective itself.  Because there is no box, the step size cannot come from a
# range — it comes from the MACHINE's scale (see _auto_sigma), so a parameter
# sitting at 0.0 (a fillet, a small hole) is still genuinely explorable instead
# of being frozen by a percentage of zero.
# ═════════════════════════════════════════════════════════════════════════════

# Ripple-penalty weight this route starts at.  Deliberately soft: the
# continuation ramp in _ripple_ramp_step doubles it (up to 16×) whenever the
# round's best still breaches the gate, so the search begins free to explore and
# is progressively forced under the limit.
_AUTO_RIPPLE_LAMBDA = 2.0
_AUTO_DEFAULT_BUDGET = 120        # FEM evals, hard cap (the quote is a promise)
_AUTO_BUDGET_PER_VAR = 24         # …and at least this many per search dimension
_AUTO_BUDGET_MAX = 2000
_AUTO_CURRENT_BUMP_PCT = 10.0     # 2nd baseline sim at I·1.10 → the baseline line

# The two search algorithms this route can drive.  'cmaes' is the original
# one-click behaviour; 'screen' is the engineer's method (see _screen_worker).
_AUTO_MODES = ("cmaes", "screen")
_AUTO_DEFAULT_MODE = "cmaes"

# Sigma (initial CMA-ES step) per variable.  Two floors, because one is not
# enough:
#   • a fraction of the variable's OWN value — the right scale for a knob that
#     is already large;
#   • a fraction of the MOTOR's size (or an absolute, for dimensionless knobs) —
#     because 25 % of a fillet sitting at 0.0 mm is 0.0, and a variable whose
#     step is zero never moves again.  The machine's own diameter is the honest
#     yardstick for "how far may a millimetre-scale knob jump".
_AUTO_SIGMA_SELF_FRAC = 0.25
_AUTO_SIGMA_SIZE_FRAC = 0.015     # of stator_diameter, for unit=mm variables
# Absolute floors, set by the user (2026-08-01): a step below 0.1 mm on a
# length, or below 0.01 on a dimensionless knob, is manufacturing noise — the
# search should never open that small.
_AUTO_SIGMA_MM_FLOOR = 0.1        # absolute [mm], for unit=mm variables
_AUTO_SIGMA_DIMLESS_FLOOR = 0.01  # absolute, for dimensionless variables
# Reject-pressure adaptation of the RUNNING spread (es.sigma, the scalar
# multiplier on the per-variable steps above — not those steps themselves).
_AUTO_SIGMA_SHRINK = 0.7          # ×per generation that draws >50 % unbuildable
_AUTO_SIGMA_SHRINK_FLOOR = 0.2    # never below this fraction of the initial


def _auto_sigma(x: float, unit: str, is_int: bool, stator_diameter: float) -> float:
    """Initial CMA-ES step for ONE variable — see the block comment above."""
    self_term = _AUTO_SIGMA_SELF_FRAC * abs(float(x))
    if is_int:
        # An integer knob (turns/slot) whose step rounds below 1 cannot change.
        return max(self_term, 1.0)
    if str(unit).strip().lower() == "mm":
        return max(self_term,
                   _AUTO_SIGMA_SIZE_FRAC * max(1.0, float(stator_diameter)),
                   _AUTO_SIGMA_MM_FLOOR)
    return max(self_term, _AUTO_SIGMA_DIMLESS_FLOOR)


def _auto_classify_error(err: Any) -> str:
    """Which fence stopped this candidate.  Counted per run and reported: a run
    that fences 90 % of what it samples is not converging, it is thrashing, and
    that has to be VISIBLE rather than hidden behind a flat 'eval failed'."""
    e = str(err or "").lower()
    if "geometry violation" in e or "not buildable" in e or "infeasible winding" in e:
        return "geometry"
    if "unconverged" in e:
        return "unconverged"
    # Before the generic "timeout": a mesh-budget reject IS the pre-empted
    # timeout (geo_mesh.MeshBudgetExceeded, message "mesh budget: ...") — a
    # candidate whose cross-section is buildable but meshes pathologically,
    # rejected in seconds instead of burning the full subprocess cap.
    if "mesh budget" in e:
        return "mesh"
    if "timeout" in e:
        return "timeout"
    return "other"


_AUTO_FEM_FENCES = ("ok", "geometry", "unconverged", "mesh", "timeout", "other")


def _auto_reject_block(counts: Dict[str, int]) -> Dict[str, Any]:
    """The per-fence reject accounting the status endpoint and the UI chips
    render.  Module-level so the accounting contract is testable without
    running a CMA generation.

    ``evaluated`` / ``rejected`` count FEM EVALS ONLY — the budget the user was
    quoted.  The two in-process pre-fence counters are reported alongside and
    deliberately NOT folded into that total: a candidate rejected before the
    subprocess cost nothing, and adding it to "evaluated" would make the run
    look like it burned a budget it never spent."""
    tried = sum(int(counts.get(k, 0) or 0) for k in _AUTO_FEM_FENCES)
    ok = int(counts.get("ok", 0) or 0)
    rejected = tried - ok
    return {"evaluated": tried, "ok": ok, "rejected": rejected,
            "rejected_geometry": int(counts.get("geometry", 0) or 0),
            "rejected_unconverged": int(counts.get("unconverged", 0) or 0),
            "rejected_mesh": int(counts.get("mesh", 0) or 0),
            "rejected_timeout": int(counts.get("timeout", 0) or 0),
            "rejected_other": int(counts.get("other", 0) or 0),
            # Pre-fence (no FEM eval spent): candidates whose cross-section was
            # rejected in-process and REPLACED by a fresh draw from the same CMA
            # distribution, and those that stayed invalid after the retry cap and
            # took the graded fence instead.
            "resampled_geometry": int(counts.get("resampled", 0) or 0),
            "prefenced_geometry": int(counts.get("prefenced", 0) or 0),
            "reject_pct": (round(100.0 * rejected / tried, 1) if tried else 0.0)}


# ── In-process geometry pre-fence ────────────────────────────────────────────
# Measured on a real 18-variable run: ~45 % of the FEM budget was spent on
# candidates that died in the eval subprocess on GEOMETRY validation — 4 minutes
# of process startup, CAD build, mesh and solve to learn something a millisecond
# of polygon arithmetic already knew.  With a 120-eval budget that left ~56
# informative points for an 18-dimensional covariance, which CMA-ES cannot
# learn from.
#
# So run the SAME gates here, before the subprocess is spawned.  "The same" is
# literal: this imports the identical functions refine_proc.run_one calls, in
# the identical order, on the identical dict (active config geometry + the
# candidate's overrides) — geometry_constraints.clamp, refine_proc._coil_fit,
# geometry_validation.validate_geometry.  Nothing is re-derived, so the verdicts
# cannot drift apart when one of them changes.
#
# HONESTY: a false "valid" costs one FEM eval and is fine.  A false "invalid"
# silently deletes a reachable design from the search and is NOT.  So anything
# this screen cannot decide — a mesh that cascades, a nonlinear solve that will
# not converge, an exception raised while building the polygons — is deferred to
# the subprocess by returning None (valid).
_AUTO_RESAMPLE_TRIES = 100        # replacement draws per invalid slot, then fence


def _auto_prefence(overrides: Dict[str, float]) -> Optional[str]:
    """None if this candidate passes every geometry gate the eval subprocess
    applies before the FEM; otherwise the message the subprocess would raise."""
    try:
        cfg = get_config()
        geo = {**dict(cfg.get("geometry", {})), **dict(overrides)}
        # 1:1 with refine_proc.run_one — clamp first (the eval scores the CLAMPED
        # cross-section, so the fence must judge the clamped one too).
        from motor_ai_sim.geometry_constraints import clamp as _clamp_geo
        geo, _applied = _clamp_geo(geo)
        from motor_ai_sim.optimization.refine_proc import _coil_fit
        n_fit, n_req = _coil_fit(geo)
        if n_fit < n_req:
            return ("infeasible winding: {} turns cannot fit the slot even "
                    "clamped".format(n_req))
        from motor_ai_sim.geometry_validation import validate_geometry as _vgeo
        res = _vgeo(geo)
        if not res.ok:
            return res.summary()
        return None
    except Exception as _e:   # noqa: BLE001
        # Cannot decide → not a reject.  Let the subprocess spend the eval and
        # give the real verdict (see the HONESTY note above).
        log.debug("auto pre-fence could not screen a candidate (%s) — deferring "
                  "to the eval subprocess", _e)
        return None


# ── Warm start from the accumulated eval cache ───────────────────────────────
_AUTO_WARM_MIN_POINTS = 3         # fewer than this is not evidence, it is noise
_AUTO_WARM_I_RTOL = 0.01          # operating current must match within 1 %
_AUTO_WARM_GAMMA_TOL = 0.5        # load angle within half a degree


def _auto_warm_start(recs, specs, cfg_fp: str, current_a: float, gamma_deg: float,
                     ripple_max: float, base: Dict[str, Any],
                     accept=None, min_n: int = _AUTO_WARM_MIN_POINTS):
    """Pick the CMA start mean from evals this project has ALREADY paid for.

    ``recs`` are ``_log_eval`` rows (config/.opt_dataset.jsonl).  A row is
    eligible only if it carries THIS machine's config fingerprint and was solved
    at THIS operating point — a row from another cross-section, or the same
    cross-section at another current, is a different problem and seeding from it
    would start the search at a point that was never good HERE.  Rows written
    before the fingerprint stamp existed carry none and are skipped rather than
    assumed (same rule the eval cache applies to its pre-convergence lines).

    Eligible rows are ranked by the run's OWN objective — the signed
    perpendicular distance above the freshly measured baseline line — so the seed
    is the best point by the metric the run will be judged on, not by a proxy.

    Returns ``{"x": [...], "n": n_eligible, "F": F_best, "overrides": {...}}``
    or None (too few points / none acceptable), in which case the caller starts
    from the current design exactly as before."""
    names = [v["name"] for v in specs]
    x_cur = [float(v["x0"]) for v in specs]
    lows = [float(v.get("lo", 0.0) or 0.0) for v in specs]
    scored = []
    for r in recs or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("cfg_fp") or "") != str(cfg_fp or ""):
            continue                              # other machine, or unstamped
        ov = r.get("overrides")
        if not isinstance(ov, dict):
            continue
        try:
            ia = float(r.get("current_a"))
            ga = float(r.get("gamma_deg", 0.0) or 0.0)
            rip = float(r.get("ripple"))
            td = float(r.get("td"))
            eff = float(r.get("eff"))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (ia, ga, rip, td, eff)):
            continue
        if abs(ia - float(current_a)) > _AUTO_WARM_I_RTOL * max(1e-9, abs(float(current_a))):
            continue
        if abs(ga - float(gamma_deg)) > _AUTO_WARM_GAMMA_TOL:
            continue
        if rip > float(ripple_max):
            continue                              # infeasible: over the gate
        cost, F = _descent_cost(
            {"torque_per_mass_Nm_kg": td, "efficiency": eff, "T_ripple_pct": rip,
             "V_peak": r.get("v_peak") or 0.0},
            base, float(ripple_max), 1.0, 1.0, 1.0, 1e9)
        scored.append((cost, F, ov))
    if len(scored) < int(min_n):
        return None
    scored.sort(key=lambda t: t[0])
    for cost, F, ov in scored:
        x = [max(lows[i], float(ov.get(nm, x_cur[i])))
             for i, nm in enumerate(names)]
        if accept is not None and not accept({nm: x[i] for i, nm in enumerate(names)}):
            # The best cached point's variables, merged onto TODAY's design for
            # the variables it does not carry, need not be a buildable machine.
            # Fall through to the next-best rather than seeding an invalid mean.
            continue
        return {"x": x, "n": len(scored), "F": float(F),
                "overrides": {nm: x[i] for i, nm in enumerate(names)}}
    return None


def _js_number(v: float) -> str:
    """Format a float the way JavaScript's template literal does, so a signature
    computed here compares `===` against the frontend's geoSignature()."""
    f = float(v)
    if f == int(f) and abs(f) < 1e21:
        return str(int(f))
    return repr(f)


def _geo_signature(geo: Dict[str, Any]) -> str:
    """The frontend's geoSignature(), server-side: numeric fields only, sorted by
    key, ``name:value`` joined by '|'.  This is the machine stamp the stale-result
    guards compare — a stored point whose stamp does not match the loaded machine
    is a row pairing one motor's geometry with another motor's numbers."""
    parts = []
    for k in sorted(geo.keys()):
        v = geo[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        parts.append("{}:{}".format(k, _js_number(v)))
    return "|".join(parts)


def _auto_population(n_vars: int) -> int:
    """CMA-ES default population λ = 4 + ⌊3·ln N⌋ — the same rule the library
    uses, restated here so the cost quote can be computed before the run."""
    return int(4 + math.floor(3.0 * math.log(max(2, int(n_vars)))))


# ═════════════════════════════════════════════════════════════════════════════
# SCREENING DESCENT — the engineer's method, mechanised
#
# WHY THIS EXISTS (measured, not hypothetical).  On the CIANO20 150_35 the
# one-click CMA-ES run spent 434 evals (359 informative) and its best candidate
# inside the ripple gate scored F = −0.0173 on the run's own perpendicular
# baseline metric — i.e. it never beat the design it started from.  The USER then
# beat it BY HAND at the same fixed operating point, reaching F = +0.00221 while
# touching at most four parameters at a time.  A human beat 359 machine evals, so
# the defect is in the SEARCH, not the physics.
#
# The user's method, in their words:
#   «сначала сделал бы первоначальные отклонения по всем переменным в районе
#    0.2 mm или 0.02 для безразмерных и понял бы какая куда отклоняет систему,
#    а потом уже использовал самые влиятельные и доводку делал оставшимися»
#
#   1. SCREEN  — perturb EVERY variable by ±δ (δ = 0.2 mm for lengths, 0.02 for
#      dimensionless, 1 for integers) and measure which way each one moves the
#      objective.  2N evals, all independent, all parallel.
#   2. DESCEND — move along the steepest-descent direction restricted to the few
#      most influential variables, with a line search over step multipliers.
#   3. POLISH  — once those stop paying, cycle the remaining variables in small
#      groups (the user works in groups of ≤4) with the same fine δ.
#
# WHY IT BEATS CMA-ES HERE.  CMA-ES must estimate an N×N covariance — 171 free
# parameters at N=18 — before its distribution carries any shape, and the eval
# budget that costs is not available at ~10 min per FEM eval.  Finite differences
# buy the gradient outright for 2N evals and spend everything after that moving.
# The trade is real and stated in docs/SCREENING_DESCENT.md: this method finds the
# nearest local optimum and cannot jump basins, so CMA-ES remains the right tool
# for a genuinely unexplored design.
# ═════════════════════════════════════════════════════════════════════════════

# The screening deviations the user named.  Not tuned — quoted.
_SCREEN_DELTA_MM = 0.2          # length variables [mm]
_SCREEN_DELTA_DIMLESS = 0.02    # dimensionless variables (fill fractions, …)
_SCREEN_DELTA_INT = 1.0         # integer knobs (turns/slot) — 1 is their quantum
# Step multipliers evaluated IN PARALLEL along the descent direction.  They are
# independent points, so a line search costs one wave, not four.
_SCREEN_ALPHAS = (0.5, 1.0, 2.0, 4.0)
# One variable IS a legitimate direction: when the screening says a single knob
# dominates the next one by two orders of magnitude, moving anything else with it
# only adds noise.  The cap at six is what keeps this a screening rather than
# gradient descent on all N.
_SCREEN_TOPK_MIN = 1
_SCREEN_TOPK_MAX = 6
_SCREEN_GAP_MIN = 1.5           # below this ratio there is no cliff to cut at
_SCREEN_GROUP = 4               # polish group size — the user's own working set
_SCREEN_SHRINK = 0.5            # δ ×= this when the whole line search fails
# …and never below half, i.e. 0.1 mm on a length and 0.01 on a dimensionless
# knob — the user's own floors (2026-08-01: a step below those is manufacturing
# noise).  It is also the mm quantisation grid: a 0.05 mm perturbation rounds
# straight back onto the design it started from, so it measures nothing at all.
_SCREEN_MIN_SHRINK = 0.5
_SCREEN_TOL = 1e-9              # cost improvement below this is not an improvement


def _screen_delta(unit: str, is_int: bool, scale: float = 1.0) -> float:
    """The screening deviation for ONE variable, at the user's own scale.

    ``scale`` < 1 is the shrink applied after a failed line search.  Integers do
    not shrink: their quantum is 1, and a step that rounds to zero is not a
    perturbation, it is a wasted eval."""
    if is_int:
        return _SCREEN_DELTA_INT
    base = (_SCREEN_DELTA_MM if str(unit).strip().lower() == "mm"
            else _SCREEN_DELTA_DIMLESS)
    return base * float(scale)


def _screen_rows(names, deltas, c0, c_plus, c_minus,
                 F_plus=None, F_minus=None) -> List[Dict[str, Any]]:
    """Turn one screening pass into the ranked sensitivity table.

    ``c_plus`` / ``c_minus`` map variable name → COST at x+δ / x−δ, with a
    missing key meaning that side was not measurable (unbuildable cross-section,
    failed solve, or a variable whose −δ would go negative).  Cost, not F: the
    ripple penalty is part of what the search descends, so it must be part of
    what the screening measures.

    Per variable this reports
      slope      dcost/dx        — central when both sides exist, one-sided else
      slope_F    dF/dx           — the same derivative on the RAW objective,
                 without the ripple penalty, so the two can be compared
      influence  |Δcost| over ONE δ, the ranking key.  Scaled by δ, so a
                 millimetre knob and a dimensionless one are comparable: it is
                 the gradient in units of "one screening deviation".
      direction  −1 / +1 — which way to MOVE this variable to lower the cost
      jitter     |c₊ + c₋ − 2c₀| / 2 — how much the two one-sided slopes
                 disagree.  Second-order curvature plus mesh discretisation
                 noise; the median over all variables is the noise floor below
                 which an "influence" is not evidence of anything.
    Sorted by influence, descending.  Pure arithmetic — no FEM, so the ranking
    math is testable on a function whose gradient is known."""
    rows = []
    for i, nm in enumerate(names):
        d = float(deltas[i])
        cp = c_plus.get(nm)
        cm = c_minus.get(nm)
        row = {"name": nm, "delta": d, "c_plus": cp, "c_minus": cm,
               "one_sided": False, "measured": True}
        fp = (F_plus or {}).get(nm)
        fm = (F_minus or {}).get(nm)
        if fp is not None:
            row["F_plus"] = fp
        if fm is not None:
            row["F_minus"] = fm
        # dF/dx alongside dcost/dx.  They differ by the ripple penalty, and the
        # difference is the whole story on a constrained design: a variable that
        # buys F while breaking the gate has slope_F and slope pointing OPPOSITE
        # ways, and reporting only one of them hides that.
        if fp is not None and fm is not None:
            row["slope_F"] = (fp - fm) / (2.0 * d)
        elif fp is not None or fm is not None:
            row["slope_F"] = None      # one-sided: needs F at the centre, unowned here
        if cp is not None and cm is not None:
            row["slope"] = (cp - cm) / (2.0 * d)
            row["influence"] = abs(cp - cm) / 2.0
            row["jitter"] = abs(cp + cm - 2.0 * c0) / 2.0
            row["direction"] = -1.0 if (cp - cm) > 0 else 1.0
        elif cp is not None:
            row["slope"] = (cp - c0) / d
            row["influence"] = abs(cp - c0)
            row["jitter"] = None
            row["direction"] = -1.0 if (cp - c0) > 0 else 1.0
            row["one_sided"] = True
        elif cm is not None:
            row["slope"] = (c0 - cm) / d
            row["influence"] = abs(c0 - cm)
            row["jitter"] = None
            row["direction"] = -1.0 if (c0 - cm) > 0 else 1.0
            row["one_sided"] = True
        else:
            # Neither side evaluated.  NOT "inert" — unmeasured.  Saying those
            # two are the same thing is how a variable silently leaves a search.
            row.update(slope=0.0, influence=0.0, jitter=None, direction=0.0,
                       measured=False)
        rows.append(row)
    rows.sort(key=lambda r: (-float(r["influence"]), r["name"]))
    return rows


def _screen_noise_floor(rows) -> float:
    """Below this |Δcost| a screening result is not evidence.

    Evals here are deterministic (same inputs → same mesh → same numbers), so
    there is no statistical noise to average away and replicating the unperturbed
    point measures nothing.  What DOES limit the gradient is that the mesh is
    rebuilt per candidate: the objective is piecewise-smooth with small jumps at
    the remeshing seams.  The two one-sided slopes disagreeing by ``jitter`` is
    exactly that effect (plus real curvature), so the MEDIAN jitter over all
    two-sided variables is an honest floor — an influence smaller than the
    typical disagreement between the two ways of measuring it is not a signal."""
    j = sorted(float(r["jitter"]) for r in rows
               if r.get("jitter") is not None and math.isfinite(float(r["jitter"])))
    if not j:
        return 0.0
    mid = len(j) // 2
    return j[mid] if len(j) % 2 else 0.5 * (j[mid - 1] + j[mid])


def _screen_pick_k(rows, noise: float, k_min: int = _SCREEN_TOPK_MIN,
                   k_max: int = _SCREEN_TOPK_MAX) -> Dict[str, Any]:
    """Choose how many variables the descent moves — BY THE GAP, not by a
    constant.  Among the variables whose influence clears the noise floor, cut at
    the largest ratio drop between consecutive influences: that is where the
    "influential" set visibly ends.  Returns the chosen names, k, and the gap
    that justified it, so the choice is reportable rather than assumed."""
    live = [r for r in rows if r.get("measured")
            and float(r["influence"]) > float(noise)]
    if not live:
        return {"names": [], "k": 0, "gap": 0.0, "n_above_noise": 0,
                "why": "no variable's influence clears the noise floor"}
    hi = min(int(k_max), len(live))
    lo = min(int(k_min), hi)
    if hi <= lo or len(live) <= lo:
        return {"names": [r["name"] for r in live[:hi]], "k": hi, "gap": 0.0,
                "n_above_noise": len(live),
                "why": "only {} variable(s) clear the noise floor".format(hi)}
    # Only INTERIOR cuts are gaps.  "k = everything above the noise floor" has no
    # variable after it to fall off to, and scoring that as an infinite drop is
    # how a screening quietly turns back into gradient descent on all N.
    best_k, best_gap = lo, 0.0
    for k in range(lo, min(hi, len(live) - 1) + 1):
        gap = float(live[k - 1]["influence"]) / max(float(live[k]["influence"]), 1e-30)
        if gap > best_gap:
            best_gap, best_k = gap, k
    if best_gap < _SCREEN_GAP_MIN and len(live) <= hi:
        # No cliff anywhere: the influences form a smooth ramp, so there is no
        # honest place to cut and every variable above the floor is influential.
        return {"names": [r["name"] for r in live], "k": len(live),
                "gap": round(float(best_gap), 3), "n_above_noise": len(live),
                "why": ("no gap above {:g}x anywhere — all {} variables above the "
                        "noise floor are comparably influential"
                        .format(_SCREEN_GAP_MIN, len(live)))}
    return {"names": [r["name"] for r in live[:best_k]], "k": best_k,
            "gap": round(float(best_gap), 3), "n_above_noise": len(live),
            "why": ("influence drops {:.2f}x after #{} — the influential set ends "
                    "there".format(best_gap, best_k))}


def _screen_step(rows, active) -> Dict[str, float]:
    """Steepest descent restricted to ``active``, in units of the screening δ.

    In δ-scaled coordinates u_i = x_i/δ_i the gradient component is
    slope_i·δ_i = ±influence_i, so steepest descent is du_i ∝ −sign(slope_i)·
    influence_i.  Normalised so the MOST influential variable moves exactly one
    δ (the user's own step), which makes the line-search multiplier read directly
    as "how many screening deviations".  Returns Δx per variable in PHYSICAL
    units."""
    sel = [r for r in rows if r["name"] in set(active) and r.get("measured")]
    if not sel:
        return {}
    top = max(float(r["influence"]) for r in sel) or 1.0
    return {r["name"]: float(r["direction"]) * float(r["delta"])
            * (float(r["influence"]) / top) for r in sel}


def _screen_groups(names, size: int = _SCREEN_GROUP) -> List[List[str]]:
    """Split the polish variables into the user's working groups of ≤ size,
    keeping the screening order so the most promising leftovers go first."""
    n = max(1, int(size))
    return [list(names[i:i + n]) for i in range(0, len(names), n)]


def _auto_assemble(max_ripple_pct: float, budget_evals: int = 0,
                   mode: str = _AUTO_DEFAULT_MODE) -> Dict[str, Any]:
    """Build the FULL optimization request from the standing conventions.

    Raises HTTPException(422) with an engineer-readable message when the input
    or the project state cannot produce a runnable optimization — external
    clients are coming, so an impossible machine is refused loudly rather than
    solved quietly."""
    try:
        r = float(max_ripple_pct)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=(
            "max_ripple_pct must be a number in percent (e.g. 5 for 5 %); "
            "got {!r}".format(max_ripple_pct)))
    if not math.isfinite(r):
        raise HTTPException(status_code=422, detail=(
            "max_ripple_pct must be a finite number in percent; "
            "got {!r}".format(max_ripple_pct)))
    if r <= 0.0:
        raise HTTPException(status_code=422, detail=(
            "max_ripple_pct must be > 0 %; got {:g} %. A zero or negative ripple "
            "limit has no feasible design — every real machine has some torque "
            "ripple.".format(r)))
    if r > 100.0:
        raise HTTPException(status_code=422, detail=(
            "max_ripple_pct must be <= 100 %; got {:g} %. Ripple is peak-to-peak "
            "torque as a percentage of the mean, so a limit above 100 % is not a "
            "constraint at all.".format(r)))
    md = str(mode or _AUTO_DEFAULT_MODE).strip().lower()
    if md not in _AUTO_MODES:
        raise HTTPException(status_code=422, detail=(
            "mode must be one of {}; got {!r}. 'cmaes' is the global population "
            "search; 'screen' is the finite-difference screening descent "
            "(perturb every variable, descend the influential ones, polish with "
            "the rest).".format(", ".join(repr(m) for m in _AUTO_MODES), mode)))

    cfg = get_config()
    geo = dict(cfg.get("geometry", {}))
    sim = dict(cfg.get("simulation", {}))
    mesh = dict(cfg.get("mesh", {}))
    schema = dict(cfg.get("geometry_schema", {}))
    wl = cfg.get("sweep_whitelist") or []
    if not wl:
        raise HTTPException(status_code=422, detail=(
            "config sweep_whitelist is empty — there is nothing to optimize. "
            "Add the geometry parameters the optimizer may vary."))

    # ── OPERATING POINT: the Simulation tab's settings, not raw defaults ──────
    # max_current / phase_offset_deg are what the Simulation panel PATCHes; the
    # mirrored current_a / gamma_deg are accepted as a fallback for older configs.
    def _sim_num(*keys):
        for k in keys:
            v = sim.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and math.isfinite(float(v)):
                return float(v)
        return None

    I = _sim_num("max_current", "current_a")
    rpm = _sim_num("rpm")
    gamma = _sim_num("phase_offset_deg", "gamma_deg")
    coil_temp = _sim_num("coil_temp_c")
    steps = _sim_num("steps_per_period")
    if I is None or I <= 0.0:
        raise HTTPException(status_code=422, detail=(
            "the Simulation tab has no usable phase current (simulation."
            "max_current) — set the operating point in Simulation first; the "
            "optimizer never invents one."))
    if rpm is None or rpm <= 0.0:
        raise HTTPException(status_code=422, detail=(
            "the Simulation tab has no usable speed (simulation.rpm) — set the "
            "operating point in Simulation first."))
    if gamma is None:
        gamma = 0.0
    if coil_temp is None or coil_temp <= -273.0:
        coil_temp = 120.0
    steps_pp = int(steps) if (steps and steps >= 8) else 36
    steps_pp = max(8, min(180, steps_pp))
    # RIPPLE IS THE CONSTRAINT HERE, so it may not be aliased.  The 12s14p
    # cogging sits at the 12th electrical harmonic; below ~48 frames/period the
    # sampling folds it, geometry-dependently, and the optimizer "finds" ripple
    # minima that do not survive a converged re-solve (the same reasoning that
    # floors the sweep route).  A run whose whole job is holding ripple under a
    # number cannot be allowed to measure that number wrong — so floor the frame
    # count and SAY that we did.  Applying the result pins 48 back into the
    # Simulation tab (eval_params), so the re-solve reproduces what was reported.
    steps_requested = steps_pp
    steps_pp = max(steps_pp, 48)

    # ── EVAL PARAMS: the Mesh tab's persisted settings + the P2 honest path ───
    n_sectors = int(mesh.get("n_sectors", 1) or 1)
    n_sectors = -1 if n_sectors <= 1 else n_sectors
    ev = {
        "steps_per_period": steps_pp,
        # What the Simulation tab asked for, when the ripple floor raised it —
        # so the card can explain the difference instead of silently disagreeing.
        "steps_per_period_requested": steps_requested,
        "coil_temp_c": float(coil_temp),
        "mesh_size_mm": max(1.0, min(float(mesh.get("mesh_size_mm", 4.0) or 4.0), 12.0)),
        "min_size_mm": max(0.1, min(float(mesh.get("min_size_mm", 0.3) or 0.3), 3.0)),
        "gap_layers": max(1.0, min(float(mesh.get("gap_layers", 2.0) or 2.0), 8.0)),
        "n_sectors": n_sectors,
        # P2 is the only basis; refine_proc coerces the belt gap + natural
        # symmetry sector per eval, exactly as the Simulation route does.
        "element_order": 2,
        "structured_gap": True,
        "airgap_macro": False,
        "iron_template": True,
        "geo_mesh": True,
        # Loss model: the same one the Simulation tab runs, so the optimizer's
        # efficiency is the efficiency the user will re-measure.
        "rotor_eddy": True,
        "end_winding_factor": 0.0,   # 0 = per-candidate auto k_end (refine_proc)
        "torque_filter": False,      # honest RAW ripple
        "pole_copy": None,
    }

    # ── VARIABLES: the whitelist, at the machine's own scale, UNBOXED ─────────
    stator_d = float(geo.get("stator_diameter", 0.0) or 0.0)
    if stator_d <= 0.0:
        stator_d = 2.0 * float(geo.get("stator_outer_radius", 20.0) or 20.0)
    variables = []
    for name in wl:
        cur = geo.get(name)
        if isinstance(cur, bool) or not isinstance(cur, (int, float)):
            continue
        meta = schema.get(name, {}) or {}
        unit = str(meta.get("unit", "") or "")
        is_int = str(meta.get("type", "float")) == "int"
        x0 = float(cur)
        sigma = _auto_sigma(x0, unit, is_int, stator_d)
        delta = _screen_delta(unit, is_int)
        # The ONLY bound is physical positivity.  A dimension may go to zero
        # (that is a real design — no fillet, no hole); it may not go negative,
        # because a negative length is not a machine.  Integers additionally
        # floor at 1: zero turns is not a winding.
        lo = 1.0 if is_int else 0.0
        variables.append({
            "name": name, "x0": x0, "sigma": round(float(sigma), 4),
            # The SCREENING deviation (mode='screen'): the user's own numbers —
            # 0.2 mm on a length, 0.02 on a dimensionless knob, 1 on an integer.
            "delta": round(float(delta), 4),
            "lo": lo, "hi": None,             # explicitly UNBOUNDED above
            "unit": unit, "is_int": is_int,
            # Manufacturable grid, not a bound: mm knobs land on 0.1 mm.
            "quant": 0.1 if unit.strip().lower() == "mm" else 0.0,
        })
    if not variables:
        raise HTTPException(status_code=422, detail=(
            "none of the sweep_whitelist parameters exist as numbers in the "
            "current geometry — the whitelist and the loaded motor disagree."))

    n_vars = len(variables)
    rate = measured_eval_seconds(steps_pp)
    _asked = int(budget_evals or 0)

    if md == "screen":
        # ── SCREENING DESCENT ────────────────────────────────────────────────
        # The budget default is the SAME rule CMA-ES gets, so the two modes are
        # compared over the same money.  The difference is where it goes:
        #   2      baseline pair (A at I, B at I·1.1) — the reference line
        #   2N     the opening screen, every variable ±δ
        #   then   descent steps (one line-search wave + a re-screen of the
        #          active set) until the influential set stops paying, then
        #          polish rounds over the rest in groups of ≤4.
        # The run STOPS EARLY when a full round measures no improvement; the
        # budget is a cap, not a target, and the log says which of the two ended
        # the run.
        n_screen = 2 * n_vars
        budget = (max(_AUTO_DEFAULT_BUDGET, _AUTO_BUDGET_PER_VAR * n_vars)
                  if _asked <= 0 else _asked)
        budget = max(2 + n_screen + len(_SCREEN_ALPHAS),
                     min(int(budget), _AUTO_BUDGET_MAX))
        workers = max(1, min(_SCAN_WORKERS, n_screen))
        # Wall-clock: the opening screen is fully parallel; after it, a "round"
        # is one line-search wave (≤ workers, so one wave) plus a re-screen of
        # the active set (k≈4 → 8 evals → one wave).  So ≈ 2 waves per
        # (len(alphas)+2k) evals — worked with k = the max, i.e. pessimistic.
        _k = _SCREEN_TOPK_MAX
        _per_round = len(_SCREEN_ALPHAS) + 2 * _k
        _rounds = max(1.0, (budget - 2 - n_screen) / float(_per_round))
        est_wall = rate["s_per_eval"] * (
            2.0                                              # baseline pair
            + math.ceil(n_screen / float(workers))           # opening screen
            + 2.0 * _rounds)                                 # descent + polish
        return {
            "objective": "baseline_line",      # STANDING RULE — not configurable
            "mode": "screen",
            "ripple_max_pct": r,
            "ripple_penalty_lambda": _AUTO_RIPPLE_LAMBDA,
            "current_bump_pct": _AUTO_CURRENT_BUMP_PCT,
            "operating_point": {"current_a": I, "rpm": rpm, "gamma_deg": gamma},
            "eval": ev,
            "variables": variables,
            "stator_diameter": stator_d,
            "budget_evals": budget,
            # Kept so every existing consumer of the plan (UI chips, progress,
            # /auto/status) reads a number instead of undefined.  For a screening
            # run "population" is the opening screen's wave and "generations" the
            # cap on descent+polish rounds it could still afford.
            "population": n_screen,
            "generations": max(1, int(round(_rounds))),
            "screen": {
                "delta": {v["name"]: v["delta"] for v in variables},
                "n_screen_evals": n_screen,
                "alphas": list(_SCREEN_ALPHAS),
                "top_k_min": _SCREEN_TOPK_MIN, "top_k_max": _SCREEN_TOPK_MAX,
                "group_size": _SCREEN_GROUP,
                "shrink": _SCREEN_SHRINK, "min_shrink": _SCREEN_MIN_SHRINK,
            },
            "cost": {
                "s_per_eval": rate["s_per_eval"],
                "s_per_eval_source": rate["source"],
                "n_samples": rate["n_samples"],
                "n_evals_max": budget,
                "parallel_workers": workers,
                "est_wall_seconds": int(round(est_wall)),
                "est_cpu_seconds": int(round(budget * rate["s_per_eval"])),
            },
        }

    pop = _auto_population(n_vars)
    if _asked > 0:
        # An explicit budget is a HARD cap the user typed — floor to whole
        # generations, never round past the number they were quoted.
        budget = max(pop + 2, min(_asked, _AUTO_BUDGET_MAX))
        # 2 evals go to the baseline pair (A at I, B at I·1.1) that defines the line.
        generations = max(1, int((budget - 2) // pop))
    else:
        # The DEFAULT scales with the dimension.  A flat 120 evals is a fine
        # budget for 5 variables and a fiction for 18: CMA-ES has to estimate an
        # N×N covariance, and the literature's rule of thumb is O(N²)/O(10·N)
        # evaluations before the distribution carries any shape at all.  At 18
        # variables the flat budget delivered ~56 informative points after the
        # geometry fence — fewer than the 171 free parameters of the covariance —
        # so the run could not beat its own baseline no matter how it was tuned.
        budget = max(_AUTO_DEFAULT_BUDGET, _AUTO_BUDGET_PER_VAR * n_vars)
        budget = max(pop + 2, min(budget, _AUTO_BUDGET_MAX))
        # Round UP to a whole number of generations: a half-finished generation
        # tells CMA-ES nothing (the distribution updates once per generation), so
        # the quote pays for the last one instead of abandoning it.
        generations = max(1, int(math.ceil((budget - 2) / float(pop))))
        budget = 2 + generations * pop

    workers = max(1, min(_SCAN_WORKERS, pop))
    waves = int(math.ceil(pop / float(workers)))
    est_wall = 2.0 * rate["s_per_eval"] + generations * waves * rate["s_per_eval"]

    return {
        "objective": "baseline_line",          # STANDING RULE — not configurable
        "mode": "cmaes",
        "ripple_max_pct": r,
        "ripple_penalty_lambda": _AUTO_RIPPLE_LAMBDA,
        "current_bump_pct": _AUTO_CURRENT_BUMP_PCT,
        "operating_point": {"current_a": I, "rpm": rpm, "gamma_deg": gamma},
        "eval": ev,
        "variables": variables,
        "stator_diameter": stator_d,
        "budget_evals": budget,
        "population": pop,
        "generations": generations,
        "cost": {
            "s_per_eval": rate["s_per_eval"],
            "s_per_eval_source": rate["source"],
            "n_samples": rate["n_samples"],
            "n_evals_max": budget,
            "parallel_workers": workers,
            "est_wall_seconds": int(round(est_wall)),
            "est_cpu_seconds": int(round(budget * rate["s_per_eval"])),
        },
    }


def _auto_worker(plan: Dict[str, Any], run_id: str, bucket: str,
                 point_name: str) -> None:
    """CMA-ES over the whitelist in PHYSICAL units with a per-variable sigma.

    Differs from _cmaes_worker in exactly one way that matters: the search is
    NOT normalised into a [0,1] box.  x lives in millimetres and fractions, the
    initial step comes from the machine's scale (_auto_sigma) and CMA's own
    covariance adapts it outward as it learns — so an optimum outside whatever
    range a human would have typed is reachable.  The fence is the geometry
    validator — run IN-PROCESS first (_auto_prefence, milliseconds) so an
    unbuildable candidate is replaced by a fresh draw from the same distribution
    instead of spending four minutes of FEM to be told it is unbuildable, and
    again inside refine_proc for everything the cheap screen cannot decide.
    Every candidate either fence rejects is counted."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        import cma
    except Exception as e:  # noqa: BLE001
        with _descent_lock:
            _descent_state.update(error="cma package not installed: {}".format(e),
                                  running=False, phase="done")
        return

    specs = plan["variables"]
    names = [v["name"] for v in specs]
    x0 = [float(v["x0"]) for v in specs]
    sigmas = [float(v["sigma"]) for v in specs]
    lows = [float(v["lo"]) for v in specs]
    ev = plan["eval"]
    op = plan["operating_point"]
    I = float(op["current_a"]); rpm = float(op["rpm"]); g = float(op["gamma_deg"])
    ripple_max = float(plan["ripple_max_pct"])
    budget = int(plan["budget_evals"])
    pop = int(plan["population"])

    counts = {"ok": 0, "geometry": 0, "unconverged": 0, "mesh": 0,
              "timeout": 0, "other": 0,
              # in-process pre-fence (no FEM eval spent) — see _auto_reject_block
              "resampled": 0, "prefenced": 0}

    def _fit(i, val):
        v = specs[i]
        x = max(float(v["lo"]), float(val))
        if v["is_int"]:
            return float(max(1, int(round(x))))
        q = float(v.get("quant") or 0.0)
        return round(x / q) * q if q > 0 else float(x)

    def to_geom(xv):
        return {names[i]: _fit(i, float(xv[i])) for i in range(len(names))}

    def _eval_at(d, cur):
        o = _subprocess_eval(
            d, cur, int(ev["steps_per_period"]), float(ev["coil_temp_c"]),
            n_periods=1.0, gamma_deg=g,
            mesh_size_mm=float(ev["mesh_size_mm"]), min_size_mm=float(ev["min_size_mm"]),
            n_sectors=int(ev["n_sectors"]), pole_copy=ev["pole_copy"],
            torque_filter=bool(ev["torque_filter"]), gap_layers=float(ev["gap_layers"]),
            end_winding_factor=float(ev["end_winding_factor"]),
            rotor_eddy=bool(ev["rotor_eddy"]), structured_gap=bool(ev["structured_gap"]),
            airgap_macro=bool(ev["airgap_macro"]), iron_template=bool(ev["iron_template"]),
            geo_mesh=bool(ev["geo_mesh"]), element_order=int(ev["element_order"]),
            rpm=rpm)
        if o.get("ok") and isinstance(o.get("res"), dict):
            o["res"]["current_a"] = float(cur)
        if isinstance(o, dict):
            o["overrides"] = dict(d)
        if o.get("ok"):
            counts["ok"] += 1
        else:
            counts[_auto_classify_error(o.get("error"))] += 1
        return o

    def _reject_block():
        return _auto_reject_block(counts)

    try:
        # The sigma vector is the run's most consequential hidden choice — print
        # it so a run can be audited from the log alone.
        log.info("AUTO optimization | ripple <= %.2f%% | %d vars | pop %d | budget %d evals\n"
                 "  operating point: %.4g A - %.4g rpm - gamma %.4g deg - coil %.4g C - %d steps/T\n"
                 "  sigma (initial CMA step, physical units): %s",
                 ripple_max, len(names), pop, budget, I, rpm, g,
                 float(ev["coil_temp_c"]), int(ev["steps_per_period"]),
                 ", ".join("{}={:g}".format(n, s) for n, s in zip(names, sigmas)))

        _RIPPLE_PEN_LAM["v"] = _AUTO_RIPPLE_LAMBDA
        _RIPPLE_PEN_LAM["v0"] = _AUTO_RIPPLE_LAMBDA
        _THD_PEN["lam"] = 0.0

        n_evals = 0
        all_pts = []
        history = []

        with _descent_lock:
            _descent_state["phase"] = "baseline"
        _save_descent_state()

        b = _eval_at(to_geom(x0), I); n_evals += 1
        if not b.get("ok"):
            with _descent_lock:
                _descent_state.update(
                    error=("baseline eval failed — the CURRENT design does not "
                           "evaluate at this operating point: {}".format(b.get("error"))),
                    running=False, phase="done")
            _save_descent_state()
            return
        base = b["res"]
        _pub_pt(all_pts, b, "baseline")
        bump = float(plan["current_bump_pct"])
        bb = _eval_at(to_geom(x0), I * (1.0 + bump / 100.0))
        n_evals += 1
        if bb.get("ok"):
            base["_bline"] = _make_bline(base, bb["res"], bump)
            _pub_pt(all_pts, bb, "baseline_bump")   # point B is a measured design too
            with _descent_lock:
                _descent_state["baseline_line"] = dict(base["_bline"])
        else:
            # Without point B there is no line, hence no perpendicular metric —
            # and the standing rule forbids silently falling back to another
            # objective.  Stop and say why.
            with _descent_lock:
                _descent_state.update(
                    error=("baseline LINE failed — the second reference sim at "
                           "I x {:.2f} did not evaluate ({}), so the perpendicular-"
                           "distance objective has no reference to measure against."
                           .format(1.0 + bump / 100.0, bb.get("error"))),
                    running=False, phase="done")
            _save_descent_state()
            return

        cost0, F0 = _descent_cost(base, base, ripple_max, 1.0, 1.0, 1.0, 1e9)
        best = {"x": to_geom(x0), "metrics": base, "cost": cost0, "F": F0}
        history.append({"iter": 0, **_msum(base), "cost": round(cost0, 5),
                        "F": round(F0, 5),
                        "x": {k: round(float(v), 4) for k, v in best["x"].items()}})

        def _bstate():
            return {"metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                    "F": round(best["F"], 5), "x": dict(best["x"])}

        with _descent_lock:
            _descent_state.update(
                running=True, iter=0, phase="optimizing", n_evals=n_evals,
                baseline=_msum(base), best=_bstate(), current=_msum(base),
                history=list(history), points=list(all_pts), grad={}, error=None,
                variables=[{"name": v["name"], "lo": v["lo"], "hi": v["hi"],
                            "step": v["sigma"]} for v in specs])
            _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                          rejects=_reject_block())

        # ── WARM START ───────────────────────────────────────────────────────
        # Every FEM eval this project ever ran is on disk.  If some of them were
        # run on THIS machine at THIS operating point, starting the search at the
        # best of them instead of at the current design hands CMA-ES a head start
        # that costs nothing.  The seed only moves the START MEAN — every number
        # this run reports is still measured fresh, so a stale or lucky cached
        # point cannot leak into the result.
        x_start = list(x0)
        try:
            from motor_ai_sim.optimization import surrogate as _surr
            _recs = _surr.load_dataset(_dataset_path())
        except Exception as _e:   # noqa: BLE001
            log.warning("auto: eval cache unreadable (%s) — no warm start", _e)
            _recs = []
        _seed = _auto_warm_start(_recs, specs, _config_fingerprint(), I, g,
                                 ripple_max, base,
                                 accept=lambda d: _auto_prefence(d) is None)
        if _seed:
            x_start = list(_seed["x"])
            log.info("AUTO: seeded from %d cached evals (this machine, this "
                     "operating point) | seed F=%+.5g vs baseline 0 | %s",
                     _seed["n"], _seed["F"],
                     ", ".join("{}={:g}".format(k, v)
                               for k, v in _seed["overrides"].items()))
            with _descent_lock:
                _descent_state["seeded_from_surrogate"] = True
                _descent_state["auto"] = dict(
                    _descent_state.get("auto") or {},
                    seeded_from_cache={"n_points": _seed["n"],
                                       "F": round(float(_seed["F"]), 6)})
        else:
            log.info("AUTO: no compatible cached evals (need >=%d for this "
                     "machine fingerprint at %.4g A / gamma %.4g deg) — starting "
                     "from the current design", _AUTO_WARM_MIN_POINTS, I, g)

        es = cma.CMAEvolutionStrategy(
            list(x_start), 1.0,
            {"CMA_stds": list(sigmas), "bounds": [list(lows), None],
             "popsize": pop, "maxiter": int(plan["generations"]),
             "verbose": -9, "seed": 12345})
        _sigma_init = float(es.sigma)          # 1.0 — the per-variable scale
        _sigma_floor = _AUTO_SIGMA_SHRINK_FLOOR * _sigma_init   # lives in CMA_stds

        # Graded fence.  A constant 1e6 made an ALL-fenced generation perfectly
        # flat, and CMA-ES read the flat fitness as convergence (tolfun) — a 10 %
        # run died after generation 1 "successfully".  The penalty grows with the
        # sigma-normalised distance from the known-good baseline (the CURRENT
        # design — the one point proven buildable), so a fenced generation still
        # slopes back toward feasibility.
        def _fence_cost(sol):
            _d = math.sqrt(sum(
                ((float(sol[k]) - float(x0[k])) / max(float(sigmas[k]), 1e-9)) ** 2
                for k in range(len(x0))))
            return 1e6 * (1.0 + _d)

        it = 0
        while not es.stop():
            with _descent_lock:
                if _descent_state["cancel"]:
                    break
            if n_evals >= budget:
                log.info("AUTO: eval budget reached (%d/%d) — stopping as quoted",
                         n_evals, budget)
                break
            sols = es.ask()

            # ── PRE-FENCE + RESAMPLING ───────────────────────────────────────
            # Screen every candidate in-process (milliseconds) with the exact
            # gates the eval subprocess applies, and replace each unbuildable one
            # with a fresh draw from the SAME CMA distribution.  es.ask(1) is
            # pycma's documented way to top up a population mid-generation (see
            # ask_geno's docstring: "X = es.ask(); X.append(es.ask(1)[0]); ...;
            # es.tell(X, ...)"), and because the replacement came from ask() it
            # lands in es.sent_solutions, so tell() takes its genotype from there
            # and does NOT treat it as a foreign point to be repaired.
            cost_by_i = {}
            gen_invalid = 0            # candidates whose FIRST draw was invalid
            gen_resampled = 0          # …of those, how many found a replacement
            for i in range(len(sols)):
                why = _auto_prefence(to_geom(sols[i]))
                if why is None:
                    continue
                gen_invalid += 1
                for _try in range(_AUTO_RESAMPLE_TRIES):
                    cand = es.ask(1)[0]
                    if _auto_prefence(to_geom(cand)) is None:
                        sols[i] = cand
                        gen_resampled += 1
                        break
                else:
                    # The distribution's whole neighbourhood is unbuildable here.
                    # Keep the original point and give it the graded fence — the
                    # same treatment a subprocess-rejected candidate gets, minus
                    # the four minutes.  It is NOT dropped: CMA still needs a
                    # ranked value for every slot it asked for.
                    cost_by_i[i] = _fence_cost(sols[i])
                    log.info("AUTO: slot %d stayed unbuildable after %d "
                             "resamples — graded fence, no FEM eval spent (%s)",
                             i, _AUTO_RESAMPLE_TRIES, str(why)[:120])
            counts["resampled"] += gen_resampled
            counts["prefenced"] += (gen_invalid - gen_resampled)

            with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
                futs = {pool.submit(_eval_at, to_geom(s), I): i
                        for i, s in enumerate(sols) if i not in cost_by_i}
                for fut in as_completed(futs):
                    i = futs[fut]
                    out = fut.result()
                    n_evals += 1
                    if out and out.get("ok"):
                        c, Fv = _descent_cost(out["res"], base, ripple_max,
                                              1.0, 1.0, 1.0, 1e9)
                        cost_by_i[i] = c
                        _pub_pt(all_pts, out, "cmaes")
                        if c < best["cost"] - 1e-9:
                            best = {"x": to_geom(sols[i]), "metrics": out["res"],
                                    "cost": c, "F": Fv}
                    else:
                        # Geometry-valid in-process, still died in the FEM
                        # (cascading mesh, unconverged field, crash) — the
                        # graded fence, exactly as before.
                        cost_by_i[i] = _fence_cost(sols[i])
                    with _descent_lock:
                        _descent_state["n_evals"] = n_evals
                        _descent_state["points"] = list(all_pts)
                        _descent_state["best"] = _bstate()
                        _descent_state["current"] = _msum(best["metrics"])
                        _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                                      rejects=_reject_block())
            es.tell(sols, [cost_by_i.get(i, 1e6) for i in range(len(sols))])
            it += 1
            history.append({"iter": it, **_msum(best["metrics"]),
                            "cost": round(best["cost"], 5), "F": round(best["F"], 5),
                            "x": {k: round(float(v), 4) for k, v in best["x"].items()}})
            with _descent_lock:
                _descent_state.update(iter=it, history=list(history))
            _save_descent_state()
            rj = _reject_block()
            log.info("AUTO gen %d/%d | evals %d/%d | best F=%.5g ripple=%.3g%% "
                     "T=%.4g Nm | fenced %d/%d (%.0f%%: geom %d, unconv %d, "
                     "mesh %d, timeout %d) | pre-fenced %d/%d this gen "
                     "(%d resampled, %d graded)",
                     it, plan["generations"], n_evals, budget, best["F"],
                     float(best["metrics"].get("T_ripple_pct") or 0.0),
                     float(best["metrics"].get("T_em_Nm") or 0.0),
                     rj["rejected"], rj["evaluated"], rj["reject_pct"],
                     rj["rejected_geometry"], rj["rejected_unconverged"],
                     rj["rejected_mesh"], rj["rejected_timeout"],
                     gen_invalid, len(sols), gen_resampled,
                     gen_invalid - gen_resampled)

            # ── SIGMA SELF-ADAPTATION ON REJECT PRESSURE ─────────────────────
            # More than half the generation drawn unbuildable means the sampling
            # cloud straddles the feasible region's wall: most of its volume is
            # outside the machine.  CMA's own step-size control cannot see this
            # (the fenced points look merely bad, not impossible), so pull the
            # spread in explicitly.  Floored at a fifth of the initial step so a
            # run can never collapse to a point — and note this is the RUNNING
            # spread, not the per-variable initial steps in CMA_stds, which are
            # the user's standing sigma floors and stay untouched.
            if gen_invalid > 0.5 * max(1, len(sols)):
                _before = float(es.sigma)
                es.sigma = max(_before * _AUTO_SIGMA_SHRINK, _sigma_floor)
                if es.sigma < _before - 1e-12:
                    log.info("AUTO: %d/%d candidates unbuildable this generation "
                             "— shrinking the sampling spread %.4g -> %.4g "
                             "(floor %.4g)", gen_invalid, len(sols), _before,
                             float(es.sigma), _sigma_floor)
                    with _descent_lock:
                        _descent_state.setdefault("range_events", []).append({
                            "iter": it, "event": "sigma_shrink",
                            "why": ("{}/{} candidates were geometry-rejected "
                                    "before the FEM".format(gen_invalid, len(sols))),
                            "sigma_before": round(_before, 6),
                            "sigma_after": round(float(es.sigma), 6)})
            # Penalty continuation: still over the gate → make the next
            # generation feel it harder (λ ×2, capped at 16× the start).
            ramp = _ripple_ramp_step(best["metrics"], ripple_max, it)
            if ramp is not None:
                best["cost"], best["F"] = _descent_cost(
                    best["metrics"], base, ripple_max, 1.0, 1.0, 1.0, 1e9)
                with _descent_lock:
                    _descent_state.setdefault("range_events", []).append(dict(ramp))
                    _descent_state["best"] = _bstate()

        rj = _reject_block()
        result = {
            "best": {"x": best["x"],
                     "overrides": {k: round(float(v), 4) for k, v in best["x"].items()},
                     "metrics": _msum(best["metrics"]), "cost": round(best["cost"], 5),
                     "F": round(best["F"], 5)},
            "baseline": _msum(base), "history": list(history), "n_evals": n_evals,
            "operating_point": op, "ripple_max_pct": ripple_max,
            "baseline_line": base.get("_bline"),
            "algorithm": "cmaes_auto", "auto": True, "rejects": rj,
            "sigma": dict(zip(names, sigmas)),
        }
        with _descent_lock:
            _descent_state["result"] = result
            _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                          rejects=rj, n_evals=n_evals)
        log.info("AUTO done | %d evals, %d fenced (%.0f%%) | best ripple %.3g%% "
                 "(gate %.3g%%) T=%.4g Nm eff=%.4g",
                 n_evals, rj["rejected"], rj["reject_pct"],
                 float(best["metrics"].get("T_ripple_pct") or 0.0), ripple_max,
                 float(best["metrics"].get("T_em_Nm") or 0.0),
                 float(best["metrics"].get("efficiency") or 0.0))

        # ── Persist the result as a Compare point (with provenance) ───────────
        try:
            pt = _auto_compare_point(bucket, point_name, plan, result)
            with _descent_lock:
                _descent_state["auto"] = dict(
                    _descent_state.get("auto") or {},
                    compare_point={"id": pt.get("id"), "name": pt.get("name")})
        except Exception as _e:   # noqa: BLE001 — a failed save must not lose the run
            log.warning("auto: could not save the compare point: %s", _e)
            with _descent_lock:
                _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                              compare_point_error=str(_e))
    except Exception as e:  # noqa: BLE001
        log.exception("auto optimization failed")
        with _descent_lock:
            _descent_state["error"] = str(e)
    finally:
        with _descent_lock:
            _descent_state["running"] = False
            _descent_state["phase"] = "done"
        _save_descent_state()
        _save_eval_rate()


def _screen_worker(plan: Dict[str, Any], run_id: str, bucket: str,
                   point_name: str) -> None:
    """Screening descent — the user's method, mechanised.  See the block comment
    above _screen_delta for why it exists and what it trades away.

    Phases, in order, all on the SAME fixed operating point and the SAME honest
    eval path the CMA route uses:

      baseline   A at I and B at I·1.1 → the perpendicular reference line.
      screening  every variable ±δ (2N evals, one parallel wave).  Produces the
                 ranked sensitivity table: slope, influence, sign, and the noise
                 floor below which an influence is not evidence.
      descent    steepest descent restricted to the top-k influential variables
                 (k chosen by the gap in the table), line-searched over
                 α ∈ {0.5, 1, 2, 4} × δ — four independent points, one wave.
                 After each accepted step the ACTIVE set is re-screened (the
                 gradient rotates as the design moves); when the active set
                 stalls, everything is re-screened once before giving up on it.
      polish     the remaining variables in groups of ≤ 4, same δ, same line
                 search.  A polish round that improves something sends the run
                 back to descent; a round that improves nothing ends it.

    Costs are recomputed from stored METRICS on every comparison rather than
    cached as numbers, so the ripple-penalty continuation ramp re-scores the
    whole run consistently instead of leaving old costs measured at an old λ."""
    from concurrent.futures import ThreadPoolExecutor

    specs = plan["variables"]
    names = [v["name"] for v in specs]
    spec_by = {v["name"]: v for v in specs}
    x0 = {v["name"]: float(v["x0"]) for v in specs}
    base_delta = {v["name"]: float(v["delta"]) for v in specs}
    ev = plan["eval"]
    op = plan["operating_point"]
    I = float(op["current_a"]); rpm = float(op["rpm"]); g = float(op["gamma_deg"])
    ripple_max = float(plan["ripple_max_pct"])
    budget = int(plan["budget_evals"])

    counts = {"ok": 0, "geometry": 0, "unconverged": 0, "mesh": 0,
              "timeout": 0, "other": 0, "resampled": 0, "prefenced": 0}
    state = {"n_evals": 0, "n_cache_pre": 0, "n_cache_self": 0}
    memo: Dict[Any, Optional[Dict[str, Any]]] = {}   # point → metrics (None = rejected)
    own_keys = set()          # persistent-cache keys THIS run wrote
    all_pts: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = []
    trajectory: List[Dict[str, Any]] = []

    def _fit(nm: str, val: float) -> float:
        v = spec_by[nm]
        x = max(float(v["lo"]), float(val))
        if v["is_int"]:
            return float(max(1, int(round(x))))
        q = float(v.get("quant") or 0.0)
        return round(x / q) * q if q > 0 else float(x)

    def to_geom(d: Dict[str, float]) -> Dict[str, float]:
        return {nm: _fit(nm, d[nm]) for nm in names}

    def _pkey(d: Dict[str, float]):
        return tuple(round(float(d[nm]), 6) for nm in names)

    def _cache_key(d: Dict[str, float], cur: float) -> str:
        return _eval_cache_key(
            d, cur, int(ev["steps_per_period"]), float(ev["coil_temp_c"]), 1.0, g,
            float(ev["mesh_size_mm"]), float(ev["min_size_mm"]),
            int(ev["n_sectors"]), ev["pole_copy"], bool(ev["torque_filter"]),
            _config_fingerprint(), float(ev["gap_layers"]),
            float(ev["end_winding_factor"]), bool(ev["rotor_eddy"]), False,
            bool(ev["structured_gap"]), bool(ev["airgap_macro"]),
            bool(ev["iron_template"]), bool(ev["geo_mesh"]),
            int(ev["element_order"]))

    def _eval_at(d: Dict[str, float], cur: float) -> Dict[str, Any]:
        """One FEM eval, through the persistent cache.  A cache HIT is not
        counted as an eval — it cost nothing, and folding it into the budget
        would make the run look like it spent money it did not."""
        ck = _cache_key(d, cur)
        hit = _EVAL_CACHE.get(ck)
        if hit is not None and _eval_healthy(hit):
            out = dict(hit)
            out["overrides"] = dict(d)
            counts["ok"] += 1
            if ck in own_keys:
                state["n_cache_self"] += 1
            else:
                state["n_cache_pre"] += 1
            return out
        o = _subprocess_eval(
            d, cur, int(ev["steps_per_period"]), float(ev["coil_temp_c"]),
            n_periods=1.0, gamma_deg=g,
            mesh_size_mm=float(ev["mesh_size_mm"]), min_size_mm=float(ev["min_size_mm"]),
            n_sectors=int(ev["n_sectors"]), pole_copy=ev["pole_copy"],
            torque_filter=bool(ev["torque_filter"]), gap_layers=float(ev["gap_layers"]),
            end_winding_factor=float(ev["end_winding_factor"]),
            rotor_eddy=bool(ev["rotor_eddy"]), structured_gap=bool(ev["structured_gap"]),
            airgap_macro=bool(ev["airgap_macro"]), iron_template=bool(ev["iron_template"]),
            geo_mesh=bool(ev["geo_mesh"]), element_order=int(ev["element_order"]),
            rpm=rpm)
        state["n_evals"] += 1
        if o.get("ok") and isinstance(o.get("res"), dict):
            o["res"]["current_a"] = float(cur)
            _store_eval(ck, {"ok": True, "res": o["res"]})
            own_keys.add(ck)
            counts["ok"] += 1
        else:
            counts[_auto_classify_error(o.get("error"))] += 1
        if isinstance(o, dict):
            o["overrides"] = dict(d)
        return o

    def _reject_block():
        blk = _auto_reject_block(counts)
        blk["cache_hits"] = state["n_cache_pre"] + state["n_cache_self"]
        blk["cache_hits_pre_run"] = state["n_cache_pre"]
        blk["fem_evals"] = state["n_evals"]
        return blk

    # ── batch evaluation: memo → geometry pre-fence → parallel FEM ────────────
    def _eval_batch(designs: List[Dict[str, float]]) -> None:
        """Evaluate a wave of design points into ``memo``.  Points already in
        memo cost nothing; points the in-process geometry screen rejects cost
        nothing either and are memoised as None (rejected), so the same
        unbuildable cross-section is never screened twice."""
        todo: Dict[Any, Dict[str, float]] = {}
        for d in designs:
            k = _pkey(d)
            if k in memo or k in todo:
                continue
            why = _auto_prefence(d)
            if why is not None:
                counts["prefenced"] += 1
                memo[k] = None
                log.info("SCREEN: candidate rejected before the FEM (%s)",
                         str(why)[:140])
                continue
            todo[k] = d
        if not todo:
            return
        # The budget is a PROMISE, not a target — enforce it inside the wave, not
        # only between waves, or a 36-eval screening pass overruns a quote the
        # user was given.  A trimmed screening pass simply leaves those variables
        # one-sided or unmeasured, and the table SAYS so.
        room = int(budget) - state["n_evals"]
        if room < len(todo):
            dropped = list(todo)[max(0, room):]
            for k in dropped:
                todo.pop(k, None)
            log.info("SCREEN: eval budget nearly spent (%d/%d) — %d candidate(s) "
                     "in this wave dropped, unmeasured rather than assumed",
                     state["n_evals"], budget, len(dropped))
        if not todo:
            return
        from concurrent.futures import as_completed
        with ThreadPoolExecutor(max_workers=max(1, _SCAN_WORKERS)) as pool:
            futs = {pool.submit(_eval_at, dict(d), I): k for k, d in todo.items()}
            for fut in as_completed(futs):
                k = futs[fut]
                out = fut.result()
                if out and out.get("ok"):
                    memo[k] = out["res"]
                    _pub_pt(all_pts, out, "screen")
                    # A screening perturbation is a real, fully-paid-for design.
                    # If one of them is the best thing this run has seen, it IS
                    # the best thing this run has seen — the incumbent is not
                    # allowed to be worse than a point already on the table just
                    # because no line search happened to land on it.
                    _keep_if_best(todo[k], out["res"])
                else:
                    memo[k] = None
                with _descent_lock:
                    _descent_state["n_evals"] = state["n_evals"]
                    _descent_state["points"] = list(all_pts)
                    _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                                  rejects=_reject_block())

    def _spent() -> int:
        return state["n_evals"]

    try:
        log.info("SCREENING DESCENT | ripple <= %.2f%% | %d vars | budget %d evals\n"
                 "  operating point: %.4g A - %.4g rpm - gamma %.4g deg - coil %.4g C - %d steps/T\n"
                 "  screening deviations: %s",
                 ripple_max, len(names), budget, I, rpm, g,
                 float(ev["coil_temp_c"]), int(ev["steps_per_period"]),
                 ", ".join("{}=±{:g}".format(n, base_delta[n]) for n in names))

        _RIPPLE_PEN_LAM["v"] = _AUTO_RIPPLE_LAMBDA
        _RIPPLE_PEN_LAM["v0"] = _AUTO_RIPPLE_LAMBDA
        _THD_PEN["lam"] = 0.0

        with _descent_lock:
            _descent_state["phase"] = "baseline"
        _save_descent_state()

        x_cur = to_geom(x0)
        b = _eval_at(x_cur, I)
        if not b.get("ok"):
            with _descent_lock:
                _descent_state.update(
                    error=("baseline eval failed — the CURRENT design does not "
                           "evaluate at this operating point: {}".format(b.get("error"))),
                    running=False, phase="done")
            _save_descent_state()
            return
        base = b["res"]
        memo[_pkey(x_cur)] = base
        _pub_pt(all_pts, b, "baseline")
        bump = float(plan["current_bump_pct"])
        bb = _eval_at(x_cur, I * (1.0 + bump / 100.0))
        if not bb.get("ok"):
            with _descent_lock:
                _descent_state.update(
                    error=("baseline LINE failed — the second reference sim at "
                           "I x {:.2f} did not evaluate ({}), so the perpendicular-"
                           "distance objective has no reference to measure against."
                           .format(1.0 + bump / 100.0, bb.get("error"))),
                    running=False, phase="done")
            _save_descent_state()
            return
        base["_bline"] = _make_bline(base, bb["res"], bump)
        _pub_pt(all_pts, bb, "baseline_bump")   # point B is a measured design too
        with _descent_lock:
            _descent_state["baseline_line"] = dict(base["_bline"])

        def _score(m: Optional[Dict[str, Any]]):
            """(cost, F) for one evaluated point — computed NOW, at the current
            ripple λ, so a penalty ramp re-scores every comparison at once."""
            if m is None:
                return None, None
            return _descent_cost(m, base, ripple_max, 1.0, 1.0, 1.0, 1e9)

        def _cost_of(d: Dict[str, float]):
            c, _F = _score(memo.get(_pkey(d)))
            return c

        cost0, F0 = _score(base)
        best = {"x": dict(x_cur), "metrics": base, "cost": cost0, "F": F0}
        cur = {"x": dict(x_cur), "metrics": base, "cost": cost0, "F": F0}

        def _bstate():
            return {"metrics": _msum(best["metrics"]), "cost": round(best["cost"], 6),
                    "F": round(best["F"], 6), "x": dict(best["x"])}

        def _keep_if_best(d: Dict[str, float], m: Dict[str, Any]) -> None:
            """Record any evaluated design that beats the incumbent.  Called for
            every point the run pays for, screening perturbations included — a
            run must never report a worse design than one it already measured."""
            c, F = _score(m)
            if c is not None and c < best["cost"] - _SCREEN_TOL:
                best.update(x=dict(d), metrics=m, cost=c, F=F)
                with _descent_lock:
                    _descent_state["best"] = _bstate()

        def _log_step(phase: str, note: str) -> None:
            history.append({"iter": len(history), "phase": phase,
                            **_msum(cur["metrics"]),
                            "cost": round(cur["cost"], 6), "F": round(cur["F"], 6),
                            "x": {k: round(float(v), 4) for k, v in cur["x"].items()}})
            trajectory.append({"step": len(trajectory), "phase": phase, "note": note,
                               "F": round(float(cur["F"]), 6),
                               "cost": round(float(cur["cost"]), 6),
                               "fem_evals": state["n_evals"],
                               "cache_hits": state["n_cache_pre"] + state["n_cache_self"],
                               "td": cur["metrics"].get("torque_per_mass_Nm_kg"),
                               "eff": cur["metrics"].get("efficiency"),
                               "ripple": cur["metrics"].get("T_ripple_pct"),
                               "x": {k: round(float(v), 4) for k, v in cur["x"].items()}})
            with _descent_lock:
                _descent_state.update(iter=len(history), history=list(history),
                                      best=_bstate(), current=_msum(cur["metrics"]),
                                      n_evals=state["n_evals"])
                _descent_state["auto"] = dict(
                    _descent_state.get("auto") or {},
                    rejects=_reject_block(), trajectory=list(trajectory))
            _save_descent_state()
            log.info("SCREEN %s | F=%+.6g cost=%.6g | td=%.4g eff=%.5g ripple=%.3g%% "
                     "| %d FEM evals (%d cache hits) | %s",
                     phase, cur["F"], cur["cost"],
                     float(cur["metrics"].get("torque_per_mass_Nm_kg") or 0.0),
                     float(cur["metrics"].get("efficiency") or 0.0),
                     float(cur["metrics"].get("T_ripple_pct") or 0.0),
                     state["n_evals"],
                     state["n_cache_pre"] + state["n_cache_self"], note)

        with _descent_lock:
            _descent_state.update(
                running=True, iter=0, phase="screening", n_evals=state["n_evals"],
                baseline=_msum(base), best=_bstate(), current=_msum(base),
                points=list(all_pts), grad={}, error=None,
                variables=[{"name": v["name"], "lo": v["lo"], "hi": v["hi"],
                            "step": v["delta"]} for v in specs])
        _log_step("baseline", "start design, {:.4g} A".format(I))

        def _cancelled() -> bool:
            with _descent_lock:
                return bool(_descent_state["cancel"])

        # ── SCREEN ───────────────────────────────────────────────────────────
        def screen(subset: List[str], scale: float) -> Dict[str, Any]:
            """Perturb each variable in ``subset`` by ±δ·scale around the CURRENT
            design and return the ranked sensitivity table."""
            deltas = {nm: _screen_delta(spec_by[nm]["unit"], spec_by[nm]["is_int"],
                                        scale) for nm in subset}
            wave, side = [], {}
            for nm in subset:
                for sgn, tag in ((+1.0, "plus"), (-1.0, "minus")):
                    d = dict(cur["x"])
                    d[nm] = d[nm] + sgn * deltas[nm]
                    if d[nm] < float(spec_by[nm]["lo"]) - 1e-12:
                        continue          # −δ would leave the physical half-line
                    d = to_geom(d)
                    if _pkey(d) == _pkey(cur["x"]):
                        continue          # quantised back onto the current point
                    side[(nm, tag)] = d
                    wave.append(d)
            with _descent_lock:
                _descent_state["phase"] = "screening"
            _eval_batch(wave)
            c_plus, c_minus, F_plus, F_minus = {}, {}, {}, {}
            for (nm, tag), d in side.items():
                c, F = _score(memo.get(_pkey(d)))
                if c is None:
                    continue
                (c_plus if tag == "plus" else c_minus)[nm] = c
                (F_plus if tag == "plus" else F_minus)[nm] = F
            rows = _screen_rows(subset, [deltas[nm] for nm in subset], cur["cost"],
                                c_plus, c_minus, F_plus, F_minus)
            noise = _screen_noise_floor(rows)
            tab = {"rows": rows, "noise_floor": noise, "scale": scale,
                   "n_vars": len(subset), "at_F": cur["F"]}
            log.info("SCREEN table (delta x%.3g, noise floor %.3g):\n%s", scale, noise,
                     "\n".join(
                         "  {:<22} influence {:>10.3g}  slope {:>11.4g}  move {:+.0f}"
                         "  jitter {:>9}  {}".format(
                             r["name"], r["influence"], r["slope"], r["direction"],
                             ("{:.3g}".format(r["jitter"]) if r["jitter"] is not None
                              else "—"),
                             ("UNMEASURED" if not r["measured"] else
                              "one-sided" if r["one_sided"] else
                              "inert" if r["influence"] <= noise else ""))
                         for r in rows))
            with _descent_lock:
                _descent_state["auto"] = dict(
                    _descent_state.get("auto") or {},
                    sensitivity={"noise_floor": noise, "scale": scale,
                                 "rows": [{k: r.get(k) for k in
                                           ("name", "influence", "slope", "slope_F",
                                            "direction", "jitter", "one_sided",
                                            "measured")}
                                          for r in rows]})
            return tab

        # ── LINE SEARCH ──────────────────────────────────────────────────────
        def line_search(step: Dict[str, float], phase: str, label: str) -> bool:
            """Try α·step for every α in _SCREEN_ALPHAS — independent points, one
            parallel wave — and ACCEPT the best if it lowers the cost.  Returns
            whether the design moved."""
            if not step:
                return False
            cand = {}
            for a in _SCREEN_ALPHAS:
                d = dict(cur["x"])
                for nm, s in step.items():
                    d[nm] = d[nm] + a * s
                d = to_geom(d)
                if _pkey(d) != _pkey(cur["x"]):
                    cand[a] = d
            if not cand:
                log.info("SCREEN: %s — every step quantised back onto the current "
                         "design (0.1 mm manufacturing grid); nothing to try", label)
                return False
            with _descent_lock:
                _descent_state["phase"] = phase
            _eval_batch(list(cand.values()))
            scored = []
            for a, d in cand.items():
                c, F = _score(memo.get(_pkey(d)))
                if c is not None:
                    scored.append((c, a, d, F))
            if not scored:
                log.info("SCREEN: %s — no candidate on the line evaluated", label)
                return False
            scored.sort(key=lambda t: t[0])
            c, a, d, F = scored[0]
            if c >= cur["cost"] - _SCREEN_TOL:
                log.info("SCREEN: %s — best on the line (alpha %.2g) costs %.6g, "
                         "not better than %.6g; rejected", label, a, c, cur["cost"])
                return False
            moves = ", ".join("{} {:+.3g}".format(nm, d[nm] - cur["x"][nm])
                              for nm in sorted(step)
                              if abs(d[nm] - cur["x"][nm]) > 1e-9)
            cur.update(x=dict(d), metrics=memo[_pkey(d)], cost=c, F=F)
            if c < best["cost"] - _SCREEN_TOL:
                best.update(x=dict(d), metrics=memo[_pkey(d)], cost=c, F=F)
            _log_step(phase, "{} | alpha {:g} | {}".format(label, a, moves or "—"))
            # Penalty continuation — the same machinery the CMA route uses.
            ramp = _ripple_ramp_step(cur["metrics"], ripple_max, len(history))
            if ramp is not None:
                cur["cost"], cur["F"] = _score(cur["metrics"])
                best["cost"], best["F"] = _score(best["metrics"])
                with _descent_lock:
                    _descent_state.setdefault("range_events", []).append(dict(ramp))
                    _descent_state["best"] = _bstate()
            return True

        # ── the run ──────────────────────────────────────────────────────────
        scale = 1.0
        active: List[str] = []
        table = screen(list(names), scale)
        _log_step("screening", "opening screen over all {} variables, {} FEM evals"
                  .format(len(names), state["n_evals"]))
        stop_reason = "budget"

        while True:
            if _cancelled():
                stop_reason = "cancelled"
                break
            if _spent() >= budget:
                stop_reason = "budget"
                break

            # DESCENT on the influential set ─────────────────────────────────
            pick = _screen_pick_k(table["rows"], table["noise_floor"])
            active = list(pick["names"])
            log.info("SCREEN: descending on %d of %d variables — %s (%s)",
                     pick["k"], len(names), ", ".join(active) or "none", pick["why"])
            with _descent_lock:
                _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                              active_set=list(active),
                                              active_why=pick["why"])
            while active and _spent() < budget and not _cancelled():
                if not line_search(_screen_step(table["rows"], active), "descent",
                                   "descent on " + "+".join(active)):
                    break
                # The gradient ROTATES as the design moves — re-screen the active
                # set (2k evals) before taking another step along a stale one.
                if _spent() >= budget:
                    break
                table = screen(active, scale)

            # The active set stalled.  Before abandoning it, re-screen EVERYTHING
            # once: the influential set at the new point may not be the old one.
            if _spent() < budget and not _cancelled():
                table = screen(list(names), scale)
                pick2 = _screen_pick_k(table["rows"], table["noise_floor"])
                if set(pick2["names"]) != set(active) and pick2["names"]:
                    log.info("SCREEN: full re-screen moved the influential set to "
                             "%s — descending again", ", ".join(pick2["names"]))
                    if line_search(_screen_step(table["rows"], pick2["names"]),
                                   "descent", "descent on " + "+".join(pick2["names"])):
                        active = list(pick2["names"])
                        continue

            # POLISH the rest, in the user's groups of ≤4 ─────────────────────
            rest = [r["name"] for r in table["rows"]
                    if r["name"] not in set(active) and r.get("measured")]
            polished = False
            with _descent_lock:
                _descent_state["phase"] = "polish"
            for grp in _screen_groups(rest, _SCREEN_GROUP):
                if _spent() >= budget or _cancelled():
                    break
                gt = screen(grp, scale)
                if line_search(_screen_step(gt["rows"], grp), "polish",
                               "polish " + "+".join(grp)):
                    polished = True
            if polished:
                # Polish paid — the influential set may have shifted, so go back
                # and descend from the new point rather than declaring victory.
                if _spent() < budget and not _cancelled():
                    table = screen(list(names), scale)
                continue

            # Neither the influential set nor the polish groups paid at this δ.
            # (There is no point lapping again at the same deviation: the descent
            # inner loop already ran to exhaustion, the full re-screen already
            # retried, and every point is memoised — a repeat lap would measure
            # the identical numbers.)  So halve δ once, down to the floor, and
            # try the whole cycle again — the user's «доводка», finer.
            if scale * _SCREEN_SHRINK >= _SCREEN_MIN_SHRINK - 1e-12 \
                    and _spent() < budget and not _cancelled():
                scale *= _SCREEN_SHRINK
                log.info("SCREEN: no improvement at this deviation — shrinking "
                         "delta to x%.3g of the screening value", scale)
                with _descent_lock:
                    _descent_state.setdefault("range_events", []).append({
                        "iter": len(history), "name": "screen_delta", "side": "shrink",
                        "to": round(scale, 4),
                        "why": "a full descent+polish round measured no improvement"})
                table = screen(list(names), scale)
                continue
            stop_reason = "converged"
            break

        if _cancelled():
            stop_reason = "cancelled"
        log.info("SCREEN: stopping — %s", stop_reason)

        rj = _reject_block()
        result = {
            "best": {"x": best["x"],
                     "overrides": {k: round(float(v), 4) for k, v in best["x"].items()},
                     "metrics": _msum(best["metrics"]), "cost": round(best["cost"], 6),
                     "F": round(best["F"], 6)},
            "baseline": _msum(base), "history": list(history),
            "n_evals": state["n_evals"], "cache_hits": rj["cache_hits"],
            "operating_point": op, "ripple_max_pct": ripple_max,
            "baseline_line": base.get("_bline"),
            "algorithm": "screening_descent", "auto": True, "mode": "screen",
            "rejects": rj, "stop_reason": stop_reason,
            "trajectory": list(trajectory),
            "sensitivity": {"noise_floor": table["noise_floor"],
                            "scale": table["scale"], "rows": table["rows"]},
            "active_set": list(active),
            "delta": dict(base_delta),
        }
        with _descent_lock:
            _descent_state["result"] = result
            _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                          rejects=rj, n_evals=state["n_evals"],
                                          stop_reason=stop_reason)
        log.info("SCREEN done | %d FEM evals + %d cache hits | %d fenced | best "
                 "F=%+.6g ripple %.3g%% (gate %.3g%%) td=%.4g eff=%.5g",
                 state["n_evals"], rj["cache_hits"], rj["rejected"], best["F"],
                 float(best["metrics"].get("T_ripple_pct") or 0.0), ripple_max,
                 float(best["metrics"].get("torque_per_mass_Nm_kg") or 0.0),
                 float(best["metrics"].get("efficiency") or 0.0))

        try:
            pt = _auto_compare_point(bucket, point_name, plan, result)
            with _descent_lock:
                _descent_state["auto"] = dict(
                    _descent_state.get("auto") or {},
                    compare_point={"id": pt.get("id"), "name": pt.get("name")})
        except Exception as _e:   # noqa: BLE001 — a failed save must not lose the run
            log.warning("screen: could not save the compare point: %s", _e)
            with _descent_lock:
                _descent_state["auto"] = dict(_descent_state.get("auto") or {},
                                              compare_point_error=str(_e))
    except Exception as e:  # noqa: BLE001
        log.exception("screening descent failed")
        with _descent_lock:
            _descent_state["error"] = str(e)
    finally:
        with _descent_lock:
            _descent_state["running"] = False
            _descent_state["phase"] = "done"
        _save_descent_state()
        _save_eval_rate()


def _auto_result_metrics(m: Dict[str, Any], rpm: float, geo_sig: str) -> Dict[str, Any]:
    """Optimizer metrics re-keyed into the Compare tab's result vocabulary (the
    one PhysicsDashboard writes), so an auto point renders in the same columns
    as a hand-saved simulation instead of showing blanks."""
    out = {
        "T_em_avg_Nm": m.get("T_em_Nm"),
        "efficiency": m.get("efficiency"),
        "torque_per_mass_Nm_kg": m.get("torque_per_mass"),
        "T_ripple_pct": m.get("T_ripple_pct"),
        "mass_total_kg": m.get("mass_total_kg"),
        "P_loss_total_W": m.get("P_loss_total_W"),
        "P_core_W": m.get("P_core_W"),
        "P_stranded_W": m.get("P_stranded_W"),
        "P_solid_W": m.get("P_solid_W"),
        "P_mech_W": m.get("P_mech_W"),
        "J_coil_A_per_mm2": m.get("J_coil_A_per_mm2"),
        "KV_rpm_per_V_line": m.get("KV_rpm_per_V_line"),
        "power_per_mass_W_kg": m.get("power_per_mass_W_kg"),
        "loss_density_W_kg": m.get("loss_density_W_kg"),
        "V_phase_peak_V": m.get("V_peak"),
        "V_line_peak_V": m.get("V_line_peak_V"),
        "I_phase_rms_A": m.get("I_phase_rms_A") or m.get("current_a"),
        "THD_LL_pct": m.get("THD_LL_pct"),
        "Kt_Nm_per_Arms": m.get("Kt_Nm_per_Arms"),
        "rpm": rpm,
        # The machine stamp travels WITH the numbers (stale-machine doctrine):
        # these results were solved on THIS cross-section and no other.
        "_geoSig": geo_sig,
    }
    return {k: v for k, v in out.items() if v is not None}


def _auto_compare_point(bucket: str, name: str, plan: Dict[str, Any],
                        result: Dict[str, Any]) -> Dict[str, Any]:
    """File the optimized design in the Compare library: full geometry, the
    metrics it was scored on, and the provenance that says where it came from.

    Stamped with the geometry signature of the OPTIMIZED cross-section — both
    `geo_sig` and `geo_sig_solved`, because for this row they are by construction
    the same machine (the numbers came from evaluating exactly this geometry)."""
    from motor_ai_sim.routes.saved_sims import append_sim

    cfg = get_config()
    geo = {k: v for k, v in (cfg.get("geometry") or {}).items()
           if isinstance(v, (int, float)) and not isinstance(v, bool)}
    geo.update({k: float(v) for k, v in (result["best"]["overrides"] or {}).items()})
    sig = _geo_signature(geo)
    op = plan["operating_point"]
    ev = plan["eval"]
    m = result["best"]["metrics"]
    b = result.get("baseline") or {}

    params = {}
    for part, mat in (cfg.get("materials") or {}).items():
        if isinstance(mat, str) and mat:
            params["mat_{}".format(part)] = mat
    params.update({
        "geo_sig": sig, "geo_sig_solved": sig,
        "I_phase_rms": m.get("current_a", op["current_a"]),
        "gamma_deg": op["gamma_deg"], "rpm": op["rpm"],
        "coil_temp_c": ev["coil_temp_c"],
        "end_winding_factor": ev["end_winding_factor"],
        "connection": (cfg.get("simulation") or {}).get("connection"),
        "steps_per_period": ev["steps_per_period"],
        "n_sectors": ev["n_sectors"], "mesh_size_mm": ev["mesh_size_mm"],
        "min_size_mm": ev["min_size_mm"],
        # ── provenance: what produced this row ──────────────────────────────
        "src": "auto_optimizer",
        "src_mode": plan.get("mode", _AUTO_DEFAULT_MODE),
        "src_algorithm": result.get("algorithm"),
        "src_objective": plan["objective"],
        "src_ripple_max_pct": plan["ripple_max_pct"],
        "src_ripple_lambda": plan["ripple_penalty_lambda"],
        "src_n_evals": result.get("n_evals"),
        "src_rejected": (result.get("rejects") or {}).get("rejected"),
        # The objective's own verdict on this row, stored WITH it: F is the
        # signed perpendicular distance above the current-only baseline line, so
        # F < 0 means this design does not beat simply raising the current.  A
        # stored point that outlives the session must carry that judgement, or
        # it will later be read as "the optimizer's answer" with no sign attached.
        "src_F_above_baseline": result.get("best", {}).get("F"),
        "src_beats_current_only": bool((result.get("best", {}).get("F") or 0) > 0),
        "src_variables": ",".join(v["name"] for v in plan["variables"]),
        "src_baseline_T_em_Nm": b.get("T_em_Nm"),
        "src_baseline_ripple_pct": b.get("T_ripple_pct"),
        "src_element_order": ev["element_order"],
        "src_created": datetime.now().isoformat(timespec="seconds"),
    })
    params.update(geo)
    return append_sim(bucket, name, params,
                      _auto_result_metrics(m, float(op["rpm"]), sig))


class AutoOptRequest(BaseModel):
    """The whole user-facing surface of a one-click optimization: ONE number —
    plus WHICH SEARCH runs it, because the two available searches answer
    different questions and only the engineer knows which one is being asked."""
    max_ripple_pct: float
    # 'cmaes'  — global population search; explores, can change basin, needs
    #            O(N²) evals before its covariance means anything.
    # 'screen' — the engineer's method: screen every variable by ±δ, descend the
    #            influential ones, polish with the rest.  Local, but it starts
    #            paying after 2N evals.  See docs/SCREENING_DESCENT.md.
    mode: str = _AUTO_DEFAULT_MODE
    # Everything below is an escape hatch, not a knob the simple card shows.
    budget_evals: int = 0        # 0 = the standing default
    point_name: str = ""         # override the auto_ripple<NN>_<date> name
    run_id: str = ""


def _auto_point_name(req: "AutoOptRequest") -> str:
    if (req.point_name or "").strip():
        return req.point_name.strip()[:80]
    # The mode is part of the NAME, not just the provenance: two rows in Compare
    # that differ only in which search produced them must be tellable apart at a
    # glance, or a comparison of the two algorithms cannot be read off the table.
    md = str(getattr(req, "mode", _AUTO_DEFAULT_MODE) or _AUTO_DEFAULT_MODE).lower()
    return "auto{}_ripple{:02d}_{}".format(
        "" if md == "cmaes" else "_" + md,
        int(round(float(req.max_ripple_pct))), datetime.now().strftime("%Y%m%d"))


@router.post("/auto/plan")
def auto_plan(req: AutoOptRequest):
    """Assemble the run WITHOUT launching it and quote what it will cost.

    Project rule: a run says what it costs before it starts.  This is the
    pre-flight the Run button shows — the assembled operating point, objective,
    variables with their initial steps, and n_evals x measured s/eval."""
    plan = _auto_assemble(req.max_ripple_pct, req.budget_evals, req.mode)
    return {"plan": plan, "point_name": _auto_point_name(req)}


@router.post("/auto")
def auto_start(req: AutoOptRequest, request: Request):
    """One-click optimization: the user gives the max torque ripple and the
    search mode, this route assembles the rest from the project's standing
    conventions and launches it.  Progress on the existing optimizer channel
    (GET /api/optimization/descent/progress) for BOTH modes, so every chart, the
    Apply path and the eval-param restore keep working unchanged."""
    with _descent_lock:
        if _descent_state["running"]:
            raise HTTPException(status_code=409,
                                detail="an optimization is already running")

    plan = _auto_assemble(req.max_ripple_pct, req.budget_evals, req.mode)
    name = _auto_point_name(req)
    try:
        from motor_ai_sim.routes.saved_sims import bucket_for
        bucket = bucket_for(request)
    except Exception:   # noqa: BLE001
        bucket = "local"

    with _descent_lock:
        _descent_external[0] = False   # this process owns the flag now
        _descent_state.update({
            "running": True, "iter": 0, "max_iters": int(plan["generations"]),
            "n_evals": 0, "best": None, "current": None, "history": [],
            "baseline": None, "baseline_line": None, "result": None,
            "phase": "starting", "points": [], "grad": {}, "mtpa_gamma_deg": None,
            "variables": [], "boundary": [], "walk_round": 1, "walk_rounds": 1,
            "converged": False, "range_events": [], "seeded_from_surrogate": False,
            # Pinned so applying the result RESTORES the eval settings into the
            # Simulation tab — else re-running the Sim would not reproduce it.
            "eval_params": {
                "steps_per_period": plan["eval"]["steps_per_period"],
                "n_sectors": plan["eval"]["n_sectors"],
                "gap_layers": plan["eval"]["gap_layers"],
                "coil_temp_c": plan["eval"]["coil_temp_c"],
                "pole_copy": plan["eval"]["pole_copy"],
                "torque_filter": plan["eval"]["torque_filter"],
                "rotor_eddy": plan["eval"]["rotor_eddy"],
                "end_winding_factor": plan["eval"]["end_winding_factor"],
                "structured_gap": plan["eval"]["structured_gap"],
                "airgap_macro": plan["eval"]["airgap_macro"],
                "iron_template": plan["eval"]["iron_template"],
                "geo_mesh": plan["eval"]["geo_mesh"],
                "mesh_size_mm": plan["eval"]["mesh_size_mm"],
                "min_size_mm": plan["eval"]["min_size_mm"]},
            "auto": {"max_ripple_pct": plan["ripple_max_pct"],
                     "objective": plan["objective"],
                     "mode": plan["mode"],
                     "budget_evals": plan["budget_evals"],
                     "population": plan["population"],
                     "generations": plan["generations"],
                     "operating_point": plan["operating_point"],
                     "cost": plan["cost"],
                     "point_name": name,
                     "sigma": {v["name"]: v["sigma"] for v in plan["variables"]},
                     "delta": {v["name"]: v["delta"] for v in plan["variables"]},
                     "rejects": None},
            "run_id": req.run_id, "error": None, "cancel": False})

    worker = _screen_worker if plan["mode"] == "screen" else _auto_worker
    threading.Thread(target=worker, args=(plan, req.run_id, bucket, name),
                     daemon=True).start()
    return {"started": True, "run_id": req.run_id, "plan": plan, "point_name": name}


@router.get("/auto/status")
def auto_status():
    """The auto run's state.

    An auto run reports on the SHARED optimizer channel (/descent/progress) so
    that every existing chart, the Apply path and the eval-param restore keep
    working unchanged — but /auto/plan and /auto exist, so /auto/status is the
    name anyone probing this API will reach for first, and a bare 404 there
    reads as "the run vanished".  Same state, plus the one judgement a caller
    should not have to derive: `above_baseline_line`.

    F is the signed perpendicular distance above the current-only baseline line.
    F < 0 means the winner does NOT beat simply raising the current — the
    constraint cost more than the objective bought.  That is a legitimate answer
    to "hold ripple under X", and it must be stated, not left as a number the
    reader is expected to interpret."""
    with _descent_lock:
        if not _descent_state.get("running") or _descent_external[0]:
            _refresh_descent_state_from_disk()
        st = _json_sane(_with_pareto(dict(_descent_state)))
    auto = st.get("auto") or {}
    if not auto.get("objective"):
        raise HTTPException(status_code=404, detail=(
            "no auto-optimization has been run on this server yet — POST "
            "/api/optimization/auto to start one, or GET "
            "/api/optimization/descent/progress for a manual optimizer run"))
    F = ((st.get("best") or {}).get("F")
         if st.get("best") else ((st.get("result") or {}).get("best") or {}).get("F"))
    pareto = st.get("pareto") or None
    verdict = None
    if isinstance(F, (int, float)):
        verdict = (
            "beats the current-only trade-off (above the baseline line)" if F > 0 else
            "does NOT beat simply raising the current: the design sits BELOW the "
            "current-only baseline line, so the ripple gate cost more than the "
            "objective bought")
    # F answers ONE question — "did we beat raising the current". It is, on this
    # machine, ~pure efficiency (1 pp of η = 4.97 Nm/kg), so an F ≤ 0 run can
    # still hold designs that are better on torque AND ripple AND efficiency.
    # That second answer travels next to the first instead of being derived by
    # whoever reads it.
    if pareto and pareto.get("n_dominating"):
        n = int(pareto["n_dominating"])
        verdict = ((verdict + " — but ") if verdict else "") + (
            f"{n} of this run's own candidates beat the starting design on all "
            f"three axes (torque, ripple, efficiency) at the same operating point")
    return {
        "running": st.get("running"), "phase": st.get("phase"),
        "iter": st.get("iter"), "generations": auto.get("generations"),
        "n_evals": st.get("n_evals"), "budget_evals": auto.get("budget_evals"),
        "max_ripple_pct": auto.get("max_ripple_pct"),
        "objective": auto.get("objective"),
        "mode": auto.get("mode") or _AUTO_DEFAULT_MODE,
        # Screening-mode extras — absent for a CMA run, and absent is the honest
        # answer there (a CMA run never measured a sensitivity table).
        "sensitivity": auto.get("sensitivity"),
        "active_set": auto.get("active_set"),
        "trajectory": auto.get("trajectory"),
        "stop_reason": auto.get("stop_reason"),
        "operating_point": auto.get("operating_point"),
        "cost": auto.get("cost"), "rejects": auto.get("rejects"),
        "point_name": auto.get("point_name"),
        "compare_point": auto.get("compare_point"),
        "baseline": st.get("baseline"),
        "best": st.get("best"), "F": F,
        "above_baseline_line": (None if F is None else bool(F > 0)),
        # Pareto-dominance over the run's own cloud, at the run's own operating
        # point (counts only — the flagged points themselves ride on
        # /descent/progress, where the cloud already lives).
        "pareto": pareto,
        "verdict": verdict,
        "error": st.get("error"),
        "progress_channel": "/api/optimization/descent/progress",
    }


class AutoPointRequest(BaseModel):
    name: str = ""


@router.post("/auto/compare_point")
def auto_save_compare_point(req: AutoPointRequest, request: Request):
    """Re-file the last auto result as a Compare point (the panel's explicit
    'Save as Compare point' button).  Same builder the run uses on completion,
    so a manual save and an automatic one are the same row."""
    with _descent_lock:
        result = dict(_descent_state.get("result") or {})
        auto = dict(_descent_state.get("auto") or {})
    if not result or not result.get("auto"):
        raise HTTPException(status_code=404,
                            detail="no finished auto-optimization to save")
    plan = _auto_assemble(float(auto.get("max_ripple_pct", 5.0)),
                          int(auto.get("budget_evals", 0) or 0),
                          str(auto.get("mode") or _AUTO_DEFAULT_MODE))
    plan["operating_point"] = auto.get("operating_point") or plan["operating_point"]
    try:
        from motor_ai_sim.routes.saved_sims import bucket_for
        bucket = bucket_for(request)
    except Exception:   # noqa: BLE001
        bucket = "local"
    pt = _auto_compare_point(bucket, (req.name or auto.get("point_name") or "auto"),
                             plan, result)
    return {"saved": True, "id": pt.get("id"), "name": pt.get("name")}
