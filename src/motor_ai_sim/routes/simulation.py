"""REST endpoints for the 2-D magnetostatics simulation.

Endpoints
---------
GET  /api/simulation/status          — solver info + current operating point
POST /api/simulation/run             — start a simulation (async)
GET  /api/simulation/result/{job_id} — poll job status / result
GET  /api/simulation/config          — current operating-point config
PATCH /api/simulation/config        — update operating-point config
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections import OrderedDict
from typing import Dict, Optional, Union

from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends, Query
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


def _parse_mat_override(mat: Optional[str]) -> Optional[dict]:
    """Parse a per-request `mat` JSON override -> {'assignment':{region:name},
    'materials':{name:props}}, or None when absent.

    MALFORMED IS A 422, not None.  Returning None meant "no override", i.e. the
    solve silently fell back to the shared config's materials and reported the
    resulting torque/losses as the signed-in user's own — the same class of bug
    as the `geo=` fallback below, one field over.  See routes/_validation.
    """
    from motor_ai_sim.routes._validation import parse_mat_override
    return parse_mat_override(mat)


async def _material_override_dep(mat: Optional[str] = Query(default=None)):
    """Router-level (multi-user, Stage 2b): set THIS request's material override
    from `mat` before the sync handler runs, so build_materials uses the signed-in
    user's own materials. Per-task context → no cross-request leak; absent/malformed
    clears it (None) so the solve falls back to the shared config exactly as before.

    An override that ASSIGNS a name nobody can resolve — not in the library, not
    carried in the override's own `materials` block — is a 400 right here, with
    the name in the message. It used to be a log.warning inside the solve and a
    silent fall-back to the analytic defaults (F6)."""
    from motor_ai_sim.material_context import set_request_materials
    _ov = _parse_mat_override(mat)
    if _ov and _ov.get("assignment"):
        from motor_ai_sim.materials import (validate_assignment as _va,
                                            UnknownMaterialError as _ume)
        try:
            _va(_ov["assignment"], known_extra=set(_ov.get("materials") or ()))
        except _ume as _me:
            raise HTTPException(status_code=400, detail=str(_me))
    set_request_materials(_ov)


router = APIRouter(
    prefix="/api/simulation", tags=["simulation"],
    dependencies=[Depends(_material_override_dep)],
)

# ── In-memory job store (replace with Redis/DB for production) ────────────────
_jobs: Dict[str, Dict] = {}

# ── Module-level geometry cache (built once per server start) ─────────────────
_motor_geom_cache: Dict = {}
_motor_geom_ghash: list = [None]   # hash of the geometry the cache was built for

_VALID_MESH_COMPONENTS = ("stator", "rotor", "magnet", "coil", "shaft",
                          "airgap", "outer")


def _parse_component_mesh(s: str) -> dict:
    """Parse the per-component mesh-size JSON ({comp: size_mm}) coming from the
    UI into a clean {comp: float} dict.  Unknown keys / non-positive sizes are
    dropped so a stray value can never corrupt the gmsh size field.  Returns {}
    for an empty / malformed string (→ global size everywhere)."""
    if not s:
        return {}
    import json
    try:
        raw = json.loads(s)
        if not isinstance(raw, dict):
            return {}
    except Exception:
        return {}
    out = {}
    for k, v in raw.items():
        kk = str(k).lower()
        if kk not in _VALID_MESH_COMPONENTS:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0.0:
            out[kk] = round(fv, 4)
    return out


def _outlines_from_polys(pfo: dict) -> list:
    """Build the renderer's outline payload (domain → polygon loops in METRES)
    from a polys dict.  Shared by the magnetostatic and eddy field endpoints."""
    def _polys_only(gm):
        if gm is None or getattr(gm, "is_empty", True):
            return []
        if gm.geom_type == "Polygon":
            return [gm]
        if hasattr(gm, "geoms"):       # Multi* / GeometryCollection
            out = []
            for sub in gm.geoms:
                out.extend(_polys_only(sub))
            return out
        return []

    def _poly_outlines_m(poly):
        rings = []
        for g in _polys_only(poly):
            if g.is_empty or g.area < 1e-6:
                continue
            rings.append([[x * 1e-3, y * 1e-3] for x, y in g.exterior.coords])
            for h in g.interiors:
                rings.append([[x * 1e-3, y * 1e-3] for x, y in h.coords])
        return rings

    pfo = pfo or {}
    outlines: list = []
    for k, dom in (("stator", 1), ("rotor", 5), ("shaft", 6),
                   ("air_gap", 3), ("airgap_band", 7), ("air_outer", 8)):
        if pfo.get(k) is not None:
            outlines.append({"domain": dom, "loops": _poly_outlines_m(pfo[k])})
    for mag_poly, polarity in pfo.get("magnets", []) or []:
        outlines.append({"domain": 4 if polarity > 0 else 44,
                         "loops": _poly_outlines_m(mag_poly)})
    for coil_poly in pfo.get("coils", []) or []:
        outlines.append({"domain": 2, "loops": _poly_outlines_m(coil_poly)})
    return outlines


def _parse_geo_override(geo: Optional[str]) -> Optional[dict]:
    """Parse a per-request `geo` JSON override into a geometry dict, or None when
    absent.

    A MALFORMED override is a 422 naming `geo` and quoting the parser.  It used
    to return None, which meant every caller fell back to the SHARED GLOBAL
    CONFIG: a client whose override was truncated, double-encoded or built from a
    stale schema got somebody else's design solved, labelled with its own name,
    with a 200 and no way to notice.  See routes/_validation.
    """
    from motor_ai_sim.routes._validation import parse_geo_override
    return parse_geo_override(geo)


def _current_geom_hash_and_params(geo: Optional[str] = None):
    """Return (hash, params_dict) of the geometry for THIS request.

    Base = the LIVE UI-edited geometry (global config). When a per-request ``geo``
    override (a JSON dict of geometry params) is supplied it is overlaid on top —
    step toward stateless, per-user endpoints (docs/MULTI_USER_PLAN.md): a
    signed-in client computes against ITS OWN design without mutating the shared
    config. Absent ``geo`` → just the global config (back-compat); a MALFORMED
    one raises 422 through ``_parse_geo_override`` instead of quietly returning
    the shared machine (that fallback is the bug, not the feature).

    Falls back to (None, None) if the geometry service is unavailable, in which
    case CadQueryMotor() reads config defaults.
    """
    import hashlib, json
    ov = _parse_geo_override(geo)        # 422 on garbage — outside the catch-all
    try:
        from motor_ai_sim.services.geometry_service import get_current_geometry
        pd = get_current_geometry().to_dict()
        if ov:
            pd = {**pd, **ov}
        h = hashlib.md5(json.dumps(pd, sort_keys=True, default=str).encode()).hexdigest()[:12]
        return h, pd
    except Exception:
        return None, None


def _get_motor_geom(rotor_angle_deg: float = 0.0):
    """Build (or return cached) CadQueryMotor 2D polygons.

    Cached per rotor_angle (rounded to 0.5°) AND per geometry hash: the moment
    ANY geometry parameter changes (e.g. rotor_fill_r) the hash changes and the
    whole angle cache is dropped, so the next field/torque render rebuilds with
    the new geometry.  Previously the cache was keyed by angle ONLY and built
    from config defaults, so radius edits were invisible until a full restart.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    ghash, params_dict = _current_geom_hash_and_params()

    if _motor_geom_ghash[0] != ghash:
        _motor_geom_cache.clear()
        _motor_geom_ghash[0] = ghash
        log.info("geometry changed (hash=%s) — cleared field/torque poly cache", ghash)

    key = round(rotor_angle_deg * 2) / 2   # round to 0.5° steps
    if key not in _motor_geom_cache:
        m = CadQueryMotor()
        if params_dict:
            m.set_parameters(params_dict)       # use LIVE params, not stale config
        _motor_geom_cache[key] = m.get_2d_polygons(rotor_angle_deg=key)
        log.info("geometry cache miss — built polys for θ=%.1f° (hash=%s)", key, ghash)

    return _motor_geom_cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class SimRunRequest(BaseModel):
    """Parameters for a single simulation run."""
    max_current:      float = Field(default=10.0,  description="Peak coil current [A]")
    frequency:        float = Field(default=50.0,  description="Electrical frequency [Hz]")
    rpm:              float = Field(default=2000.0, description="Rotor speed [rpm]")
    rotor_angle:      float = Field(default=0.0,   description="Static rotor angle [deg]")
    phase_offset_deg: float = Field(default=0.0,   description="γ — current angle offset vs d-axis [deg]")
    max_steps:        int   = Field(default=10_000, ge=100, le=200_000,
                                    description="PINN training steps")
    device:           str   = Field(default="cpu", description="'cuda' or 'cpu'")


class SimConfigPatch(BaseModel):
    max_current:      Optional[float] = None
    frequency:        Optional[float] = None
    rpm:              Optional[float] = None
    phase_offset_deg: Optional[float] = None
    # ── the rest of the PHYSICS the Simulation tab controls ─────────────────
    # These lived ONLY in the browser's localStorage.  Everything that solves a
    # candidate off-tab — the sweep, the optimizer, the descent — reads the
    # SHARED config, and `_config_fingerprint` (routes/optimization.py) hashes
    # this same block to decide whether a cached eval is still valid.  A switch
    # the browser kept to itself therefore did two silent things at once: swept
    # points solved DIFFERENT physics than the Simulation tab showed, and the
    # eval cache happily served results from before the switch was flipped.
    demag:              Optional[bool]  = None   # per-element irreversible demag (de-rates Br)
    eddy:               Optional[bool]  = None   # coupled sigma*dA/dt solve (solved copper loss)
    rotor_eddy:         Optional[bool]  = None   # field-based magnet/shaft eddy (vs slab estimate)
    torque_filter:      Optional[bool]  = None   # band-limit T(t) to the 6k orders
    drive:              Optional[str]   = None   # "current" | "voltage"
    v_phase_peak:       Optional[float] = None   # voltage drive: phase amplitude [V peak]
    v_delta_deg:        Optional[float] = None   # voltage drive: angle [deg el]
    coil_temp_c:        Optional[float] = None   # copper temperature -> rho_Cu(T)
    steps_per_period:   Optional[int]   = None   # transient frames per electrical period
    end_winding_factor: Optional[float] = None   # k_end (0 = auto from geometry)
    connection:         Optional[str]   = None   # winding: "4S" | "2S-2P" | "4P"
    # D-AXIS REFERENCE, electrical degrees.  A number PINS it and nothing is
    # solved to find it (the 24-frame no-load calibration is skipped entirely);
    # "" or "auto" clears the pin and the measurement runs again.  Both are
    # states the user chose explicitly — what this must never become is a stale
    # angle nobody can see, so it lives in the config the panel shows and is
    # stamped into every result as `daxis_source`.
    daxis_deg:          Optional[Union[float, str]] = None


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
async def simulation_status():
    """Return solver info and the current operating point."""
    try:
        from motor_ai_sim.simulation.solver_2d import SimConfig
        cfg = SimConfig.from_motor_config()
    except Exception as e:
        return {"modulus_available": False, "error": str(e)}

    return {
        # legacy field kept for the frontend response shape; FEM is the solver now
        "modulus_available": False,
        "operating_point": {
            "max_current":      cfg.I_peak,
            "frequency_hz":     cfg.frequency_hz,
            "rpm":              cfg.rpm,
            "Br_magnet_T":      cfg.Br_magnet,
            "phase_offset_deg": cfg.phase_offset_deg,
        },
        "solver": "2-D magnetostatics FEM (scikit-fem, sliding-band transient)",
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
        sim_cfg.I_peak            = req.max_current
        sim_cfg.frequency_hz      = req.frequency
        sim_cfg.rpm               = req.rpm
        sim_cfg.rotor_angle_deg   = req.rotor_angle
        sim_cfg.phase_offset_deg  = req.phase_offset_deg
        sim_cfg.max_steps         = req.max_steps
        sim_cfg.device            = req.device

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

    # The load angle lives under two names — phase_offset_deg (UI, presets) and
    # gamma_deg (solver, optimizer) — and the same for the current. Writing only
    # the one the caller named let them drift: the panel showed γ=0 while the FEM
    # ran at 10, and an optimization inherited a stale current. Mirror them.
    for _a, _b in (("phase_offset_deg", "gamma_deg"), ("max_current", "current_a")):
        if _a in updates and _b not in updates:
            updates[_b] = updates[_a]
        elif _b in updates and _a not in updates:
            updates[_a] = updates[_b]

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
                    # "" / "auto" is how a PINNED value is cleared (the d-axis
                    # goes back to being measured).  yaml's null reads back as
                    # None, which is exactly what the resolvers call "auto".
                    _out = ("null" if (isinstance(val, str)
                                       and val.strip().lower() in ("", "auto"))
                            else str(val))
                    line = m.group(1) + _out + '\n'
                    replaced.add(key)
                    break

        result.append(line)

    missing = set(updates) - replaced
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Keys not found in simulation: block: {missing}"
        )

    # Atomic write (temp + replace) — write_text truncate-then-write leaves a
    # window where a concurrent reader parses an empty config and clobbers it.
    # On Windows os.replace raises PermissionError while ANY other handle holds
    # the target (a concurrent get_config() read, the presets autosave, an
    # editor) — observed as a 500 killing the settings save mid-session.  The
    # lock is transient, so retry briefly before giving up.
    import os as _os
    import time as _time
    _tmp = cfg_path.with_suffix(".yaml.tmp")
    _tmp.write_text(''.join(result), encoding="utf-8")
    for _attempt in range(10):
        try:
            _os.replace(_tmp, cfg_path)
            break
        except PermissionError:
            if _attempt == 9:
                raise HTTPException(
                    status_code=503,
                    detail="config file is locked by another process — retry")
            _time.sleep(0.05 * (_attempt + 1))
    clear_config_cache()
    # A simulation parameter IS the machine's operating physics: anything solved
    # under the previous value is stale the moment it changes.  Drop every
    # simulation-side cache (2-D polys, meshes, field, transient, frame) here
    # rather than trusting each consumer's own key — the optimizer's eval cache
    # is keyed by `_config_fingerprint`, which hashes this block, so it
    # invalidates itself in the same instant.
    try:
        clear_simulation_caches()
    except Exception:  # noqa: BLE001
        log.warning("simulation caches were not flushed after a config patch",
                    exc_info=True)
    return {"status": "ok", "updated": updates}


# ─────────────────────────────────────────────────────────────────────────────
# 7b.  FEM mesh builder — returns triangle mesh for visualisation only
# ─────────────────────────────────────────────────────────────────────────────

_fem_mesh_cache: Dict[tuple, Dict] = {}


@router.get("/mesh/build2d")
async def build_fem_mesh_2d(
    rotor_angle_deg:     float = 0.0,
    mesh_size_mm:        float = 4.0,
    surface_deviation:   float = 0.005,   # Ansys "Surface Deviation" [mm]
    normal_deviation:    float = 6.0,     # Ansys "Normal Deviation" [deg]
    aspect_ratio:        float = 10.0,    # Ansys "Aspect Ratio"
    min_size_mm:         float = 0.3,     # Mesh.MeshSizeMin
    outer_air_factor:    float = 1.0,     # 1.0 = no outer ring; 1.3 = +30% radius
    motion_band:         bool  = False,   # split air gap with thin DOM_BAND ring
    band_thickness_mm:   float = 0.4,
    gap_layers:          float = 3.0,     # element layers across the air gap
    n_sectors:           int   = 1,       # 1 = full motor; 4 = 1/4 symmetry
    stator_fillet_mm:    float = 0.0,     # extra Shapely smoothing; polygons
                                          # now ship with CadQuery-radius fillets
                                          # (stator_fillet_r=2.5, _r1=0.9) baked in,
                                          # so this is OFF by default.
    component_mesh:      str   = "",      # JSON {comp: size_mm} per-part target
                                          # element size: stator/rotor/magnet/
                                          # coil/shaft/outer. "" = global size.
    geo:                 Optional[str] = None,  # per-request geometry override (multi-user)
):
    """Build a 2-D triangle mesh of the motor cross-section and return it as
    JSON-friendly arrays. Parameters mirror Ansys Maxwell's Curved Surface
    Meshing settings.

    Solver-domain extensions (Ansys-style):
      • outer_air_factor: extend air beyond stator OD to apply A_z=0 at a
        far-field boundary instead of directly on the iron.
      • motion_band: thin slip-surface ring inside the air gap (transient
        solver re-meshes only this band as the rotor sweeps).
      • n_sectors: split the motor into n equal wedges (e.g. 4 → 1/4 model).
        Per-sector slot/pole count is num_slots/n and num_poles/n; must
        be integers for the cut to make sense.  Anti-periodic BC on the
        two radial cuts must be enforced by the solver.
    """
    import math as _math
    import numpy as _np

    # Include a hash of the LIVE geometry so editing any geometry parameter
    # (e.g. rotor_fill_r) invalidates the mesh cache and rebuilds — previously
    # the key had only mesh params, so geometry edits never showed up here.
    _ghash, _params_dict = _current_geom_hash_and_params(geo)
    _comp_mesh = _parse_component_mesh(component_mesh)
    key = (
        _ghash,
        round(rotor_angle_deg * 2) / 2,
        round(mesh_size_mm, 2),
        round(surface_deviation, 4),
        round(normal_deviation, 1),
        round(aspect_ratio, 1),
        round(min_size_mm, 2),
        round(outer_air_factor, 2),
        bool(motion_band),
        round(band_thickness_mm, 2),
        round(gap_layers, 2),
        int(n_sectors),
        round(stator_fillet_mm, 2),
        tuple(sorted(_comp_mesh.items())),
    )
    if key in _fem_mesh_cache:
        return _fem_mesh_cache[key]

    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.simulation.fem_solver_2d import (
            _simplify_polys, build_mesh_from_polygons,
            _build_full_disk_from_halves,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FEM solver unavailable: {e}")

    try:
        motor = CadQueryMotor()
        if _params_dict:
            motor.set_parameters(_params_dict)   # LIVE params, not stale config
        # The out_band far-field radius is baked into get_2d_polygons from
        # motor.parameters["outer_air_factor"] — without feeding it here the
        # Mesh-tab "Outer air ring" slider did nothing (same fix as the
        # sliding-band path below).
        try:
            motor.parameters["outer_air_factor"] = float(outer_air_factor)
            motor.parameters["band_thickness_mm"] = float(band_thickness_mm)
        except Exception:
            pass
        polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)
        # Cap the simplify tolerance hard: a large surface_deviation
        # Douglas-Peucker-flattens the rounded rotor-tooth / fillet ARCS into
        # straight chords (the "straight tooth" mismatch vs the Geometry tab).
        # 0.01 mm is visually lossless, so the Mesh always shows the REAL
        # geometry — identical to the Geometry tab and to the solver (which
        # already uses 0.005).
        polys = _simplify_polys(polys, tol_mm=min(float(surface_deviation), 0.01),
                                 stator_fillet_mm=stator_fillet_mm)
        # periodic_coils=False: the disjoint air_background polygon now covers
        # slot air around wires and rotor-pocket air above magnets, so the
        # single gmsh fragment pass produces a clean non-overlapping mesh.
        # No more template overlay → no more "two meshes stacked" artefacts.
        if int(n_sectors) == 1:
            # FULL DISK: OCC can't cleanly mesh the closed 360° (double-meshes
            # the iron) — stitch it from two clean 1/2 sector meshes instead.
            mesh, cell_tags_from_build, classify_fn = _build_full_disk_from_halves(
                polys, rotor_angle_deg, mesh_size_mm, min_size_mm,
                outer_air_factor, motion_band, band_thickness_mm,
                motor.parameters, _comp_mesh,
                normal_deviation_deg=normal_deviation, aspect_ratio=aspect_ratio,
                gap_layers=gap_layers)
        else:
            mesh, cell_tags_from_build, classify_fn = build_mesh_from_polygons(
                polys, rotor_angle_deg, mesh_size_mm,
                min_size_mm=min_size_mm,
                normal_deviation_deg=normal_deviation,
                aspect_ratio=aspect_ratio,
                periodic_coils=False,
                geo_cfg=motor.parameters,
                outer_air_factor=outer_air_factor,
                motion_band=motion_band,
                band_thickness_mm=band_thickness_mm,
                gap_layers=gap_layers,
                n_sectors=n_sectors,
                component_mesh_mm=_comp_mesh,
            )
    except Exception as e:
        log.exception("mesh build failed")
        raise HTTPException(status_code=500, detail=f"mesh build failed: {e}")

    # Use the gmsh physical-group → domain map directly.  The radial-bands
    # `classify_fn` knows nothing about the new air_background polygon and
    # would mis-tag rotor-pocket air / slot air → stator/rotor.  With the
    # disjoint polygon decomposition, build_mesh_from_polygons' own tags
    # are now the authoritative source.
    cell_tags = cell_tags_from_build.astype(_np.int16)

    # Each magnet now has its OWN domain id (DOM_MAG_BASE + i).  Collapse
    # them back to DOM_MAG_N (4) / DOM_MAG_S (44) so the visualiser, which
    # only knows those two ids, still colours them correctly.
    from motor_ai_sim.simulation.fem_solver_2d import (
        DOM_MAG_BASE as _MAG0, DOM_COIL_BASE as _COIL0,
        DOM_MAG_N as _MAGN, DOM_MAG_S as _MAGS, DOM_COIL as _COIL,
    )
    polys_meshed = getattr(classify_fn, "polys", polys)
    polarities = [pol for _mp, pol in polys_meshed.get("magnets", [])]
    # Per-coil ids → DOM_COIL
    mask_coil = cell_tags >= _COIL0
    if _np.any(mask_coil):
        cell_tags[mask_coil] = _COIL
    # Per-magnet ids → DOM_MAG_N / DOM_MAG_S
    mask = (cell_tags >= _MAG0) & (cell_tags < _COIL0)
    if _np.any(mask):
        idx = (cell_tags[mask] - _MAG0).astype(int)
        cell_tags[mask] = _np.array(
            [_MAGN if (j < len(polarities) and polarities[j] > 0) else _MAGS
             for j in idx], dtype=cell_tags.dtype)

    # mesh.p is (2, n_nodes); mesh.t is (3, n_tri)
    vertices = mesh.p.T.tolist()           # n_nodes × 2
    triangles = mesh.t.T.tolist()           # n_tri × 3

    # Cell-centroid radial bounds for context (mm)
    r_nodes = _np.sqrt((mesh.p ** 2).sum(axis=0))
    n_nodes = len(vertices)
    n_tri   = len(triangles)

    domain_counts: Dict[str, int] = {}
    dom_names = {0: "air", 1: "stator", 2: "coil", 3: "airgap",
                 4: "magnet_N", 5: "rotor", 6: "shaft",
                 7: "band", 8: "outer_air",
                 44: "magnet_S"}
    for d, c in zip(*_np.unique(cell_tags, return_counts=True)):
        domain_counts[dom_names.get(int(d), f"d{d}")] = int(c)

    # ── Smooth CadQuery outlines (mm → m) for the overlay layer ────────────
    # Each entry: { "domain": int, "loops": [[[x,y], ...], ...] }
    # The renderer draws each loop as a closed polyline → crisp boundary
    # regardless of mesh density.
    from shapely.geometry import MultiPolygon as _SMP

    def _poly_outlines_m(poly):
        if poly is None or poly.is_empty:
            return []
        # Flatten ANY geometry to its polygon parts.  A large surface_deviation
        # can simplify a thin polygon into a GeometryCollection (polygons +
        # stray LineStrings); the 1-D bits have no `.exterior` and would 500
        # the whole mesh build here in the outline assembly.
        def _polys_only(gm):
            if gm is None or gm.is_empty:
                return []
            if gm.geom_type == "Polygon":
                return [gm]
            if hasattr(gm, "geoms"):       # Multi* / GeometryCollection
                out = []
                for sub in gm.geoms:
                    out.extend(_polys_only(sub))
                return out
            return []
        rings = []
        for g in _polys_only(poly):
            if g.is_empty or g.area < 1e-6:
                continue
            rings.append([[x * 1e-3, y * 1e-3] for x, y in g.exterior.coords])
            for h in g.interiors:
                rings.append([[x * 1e-3, y * 1e-3] for x, y in h.coords])
        return rings

    # Use the post-clip polys attached by build_mesh_from_polygons (when
    # n_sectors > 1 or motion_band / outer_air added new domains).  Falls
    # back to the input polys if the solver didn't expose them.
    polys_for_outlines = getattr(classify_fn, "polys", polys)

    outlines: List[Dict] = []
    if polys_for_outlines.get("stator") is not None:
        outlines.append({"domain": 1, "loops": _poly_outlines_m(polys_for_outlines["stator"])})
    if polys_for_outlines.get("rotor") is not None:
        outlines.append({"domain": 5, "loops": _poly_outlines_m(polys_for_outlines["rotor"])})
    if polys_for_outlines.get("shaft") is not None:
        outlines.append({"domain": 6, "loops": _poly_outlines_m(polys_for_outlines["shaft"])})
    if polys_for_outlines.get("air_gap") is not None:
        outlines.append({"domain": 3, "loops": _poly_outlines_m(polys_for_outlines["air_gap"])})
    if polys_for_outlines.get("airgap_band") is not None:
        outlines.append({"domain": 7, "loops": _poly_outlines_m(polys_for_outlines["airgap_band"])})
    if polys_for_outlines.get("air_outer") is not None:
        outlines.append({"domain": 8, "loops": _poly_outlines_m(polys_for_outlines["air_outer"])})
    for mag_poly, polarity in polys_for_outlines.get("magnets", []):
        outlines.append({
            "domain": 4 if polarity > 0 else 44,
            "loops": _poly_outlines_m(mag_poly),
        })
    for coil_poly in polys_for_outlines.get("coils", []):
        outlines.append({"domain": 2, "loops": _poly_outlines_m(coil_poly)})

    payload = {
        "rotor_angle_deg": rotor_angle_deg,
        "mesh_size_mm":    mesh_size_mm,
        "n_vertices":      n_nodes,
        "n_triangles":     n_tri,
        "vertices":        vertices,           # metres
        "triangles":       triangles,          # 0-indexed node refs
        "domain_per_tri":  cell_tags.tolist(),
        "domain_counts":   domain_counts,
        "outlines":        outlines,           # smooth CadQuery boundaries
        "extent": [
            float(mesh.p[0].min()), float(mesh.p[0].max()),
            float(mesh.p[1].min()), float(mesh.p[1].max()),
        ],
        "note": "Conforming triangle mesh of the real CadQuery cross-section (gmsh OCC).",
    }
    _fem_mesh_cache[key] = payload
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 7b'.  Sliding-band TWO-mesh view  (feature/sliding-band-fem branch)
# ─────────────────────────────────────────────────────────────────────────────

_fem_mesh_sb_cache: Dict[tuple, Dict] = {}


@router.get("/mesh/build2d_sliding_band")
async def build_fem_mesh_2d_sliding_band(
    rotor_angle_deg:   float = 0.0,
    mesh_size_mm:      float = 4.0,
    min_size_mm:       float = 0.3,
    surface_deviation: float = 0.005,   # Ansys "Surface Deviation" [mm]
    normal_deviation:  float = 6.0,     # Ansys "Normal Deviation" [deg]
    aspect_ratio:      float = 10.0,    # Ansys "Aspect Ratio"
    outer_air_factor:  float = 1.3,
    band_thickness_mm: float = 0.4,
    gap_layers:        float = 3.0,     # element layers across the air gap
    n_sectors:         int   = 4,
    stator_fillet_mm:  float = 0.0,     # extra Shapely fillet smoothing
    component_mesh:    str   = "",      # JSON {comp: size_mm} per-part mesh size
    pole_copy:         bool  = False,   # bit-identical pole/slot template-copy mesh
    iron_template:     bool  = True,    # deterministic template iron (fallback: gmsh)
    geo_mesh:          bool  = False,   # geometry-driven CDT mesh (real fillets; full-ring only)
    hi_fidelity:       bool  = False,   # match the SOLVER's hi-fi mesh: feature÷8 + gap≥4
    structured_gap:    bool  = False,   # ANSYS-style concentric-ring gap (experimental toggle)
    geo:               Optional[str] = None,  # per-request geometry override (multi-user)
):
    """Build TWO independent meshes (stator + rotor) and stitch them into
    one renderer-friendly payload for the Mesh tab.  Lets the user
    visually verify that the rotor mesh REALLY rotates as a rigid body
    by sweeping `rotor_angle_deg` — the stator mesh and the band stay
    put; the rotor + magnets sweep through the wedge.

    Each half is meshed with the existing build_mesh_from_polygons; the
    rotor mesh's points are then transformed via _rotate_mesh_points.
    Returns the same JSON shape as /mesh/build2d so the existing Mesh
    viewer can render it without any frontend changes — only difference
    is the cell-tag colouring naturally splits into 'stator part' and
    'rotor part' because they came from independent gmsh runs.
    """
    import math as _math
    import numpy as _np

    _comp_mesh = _parse_component_mesh(component_mesh)
    _gh, _pd = _current_geom_hash_and_params(geo)   # geometry (live + optional override)
    key = (round(rotor_angle_deg, 3), round(mesh_size_mm, 2),
           round(min_size_mm, 2), round(surface_deviation, 4),
           round(normal_deviation, 1), round(aspect_ratio, 1),
           round(outer_air_factor, 2), round(band_thickness_mm, 2),
           round(gap_layers, 1), int(n_sectors), round(stator_fillet_mm, 2),
           int(bool(pole_copy)), int(bool(iron_template)), int(bool(hi_fidelity)), int(bool(structured_gap)),
           int(bool(geo_mesh)),
           tuple(sorted(_comp_mesh.items())), _gh)
    if key in _fem_mesh_sb_cache:
        return _fem_mesh_sb_cache[key]

    try:
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.simulation.fem_solver_2d import (
            _simplify_polys, _add_motion_band,
            _build_sliding_band_meshes, _find_ring_nodes,
            DOM_MAG_BASE, DOM_COIL_BASE,
            DOM_MAG_N, DOM_MAG_S, DOM_COIL,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sliding-band unavailable: {e}")

    motor = CadQueryMotor()
    # Per-request geometry override (multi-user): apply ONLY when a `geo` override
    # is present, so the existing (no-geo) path keeps reading config defaults
    # exactly as before — zero behaviour change without geo.
    if geo and _pd:
        motor.set_parameters(_pd)
    # outer_air_factor controls out_band's far-field radius, which is baked
    # into get_2d_polygons — feed it through the geometry parameters so the
    # Mesh-tab "Outer air ring" slider actually moves the boundary.
    try:
        motor.parameters["outer_air_factor"] = float(outer_air_factor)
    except Exception:
        pass
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    # Symmetry switch honours the label: "Full" (n_sectors<=1) draws the WHOLE
    # motor (stitched 2×180° halves); ½ and ¼ draw the symmetry sector.  Use the
    # MERGED band (single shared ring at mid) so the air gap is continuous — the
    # moving band's mid±δ split has no triangles there and would show as a black
    # strip in the display.
    _full_ring_view = int(n_sectors) <= 1
    # geo mesh now builds the 1/N wedge directly, so the Mesh tab shows the real
    # sector (no full-ring force).
    # Slip-ring density: SAME adaptive formula as the transient solver, so the
    # Mesh tab shows the mesh the solver actually uses (it used to fall back to
    # the global default ring — a DIFFERENT, coarser grid than any solve).
    try:
        _pp_v = max(1, int(round(float(_pd.get("num_poles", 20)))) // 2)
    except Exception:
        _pp_v = 10
    _slip_base_v = int(round(1008.0 * (max(1.0, float(gap_layers)) + 2.0) / 3.0))
    _n_slip_v = _pp_v * 24 * max(5, _math.ceil(_slip_base_v / (24 * _pp_v)))
    polys = _simplify_polys(polys, tol_mm=surface_deviation,
                             stator_fillet_mm=stator_fillet_mm,
                             normal_dev_deg=normal_deviation,
                             band_mode="merged", n_slip=_n_slip_v,
                             gap_layers=gap_layers, structured_gap=structured_gap)
    # in_band / out_band now come straight from get_2d_polygons (full inner
    # air disk + outer air annulus), so the old air-gap-splitting motion
    # band is no longer needed for the sliding-band path.

    # Mesh density is driven by the Mesh-tab sliders (mesh_size, min_size,
    # gap_layers, normal_deviation) — the SAME values the transient solver now
    # uses (its old hard clamps were removed), so this is byte-for-byte the mesh
    # that computes T(t)/V(t)/losses.
    # Match the SOLVER's auto-refine EXACTLY (fem_transient_sliding_band): refine the
    # target element to smallest-feature/8 under hi-fi (/4 otherwise), and force >=4
    # air-gap layers under hi-fi.  Without this the Mesh viewer drew a COARSER mesh than
    # the transient actually solved (especially with hi-fidelity ON) — the "why is the
    # real mesh different?" gap.  With it, the viewer is byte-for-byte the solved mesh.
    _eff_mesh = float(mesh_size_mm); _eff_gap = float(gap_layers); _feat_floor = None
    try:
        _feat = min(float(motor.parameters.get("slot_width", 1e9) or 1e9),
                    float(motor.parameters.get("tooth_width", 1e9) or 1e9))
        if 0.0 < _feat < 1e8:
            # feature/2 (÷4 hi-fi) is the quality floor: the coarsest the iron is
            # allowed to be = 2 elements across the smallest tooth/slot.  Per Vadim
            # 2026-07-02 the previous feature/4 was too fine on big motors (450 mm:
            # feature/4 = 3.9 mm forced a 96k-tri mesh); feature/2 ≈ 8 mm there lets
            # it mesh coarse + fast, and the slider still refines down.  Report it so
            # the Mesh tab bounds the slider to it (the actually-meshed size).
            _feat_floor = max(float(min_size_mm), _feat / (4.0 if hi_fidelity else 2.0))
            _eff_mesh = min(_eff_mesh, _feat_floor)
    except Exception:
        pass
    if hi_fidelity:
        _eff_gap = max(_eff_gap, 4.0)
    try:
        mesh_s, tags_s, classify_s, mesh_r, tags_r, classify_r = \
            _build_sliding_band_meshes(
                polys, rotor_angle_deg=rotor_angle_deg,
                mesh_size_mm=_eff_mesh, min_size_mm=min_size_mm,
                normal_deviation_deg=normal_deviation, aspect_ratio=aspect_ratio,
                outer_air_factor=outer_air_factor,
                band_thickness_mm=band_thickness_mm, gap_layers=_eff_gap,
                n_sectors=(1 if _full_ring_view else n_sectors),
                geo_cfg=motor.parameters,
                component_mesh_mm=_comp_mesh,
                full_ring=_full_ring_view,
                pole_copy=bool(pole_copy),
                iron_template=bool(iron_template),
                geo_mesh=bool(geo_mesh),
            )
    except Exception as e:
        log.exception("sliding-band mesh build failed")
        raise HTTPException(status_code=500, detail=f"sliding-band mesh failed: {e}")

    # Concatenate into one renderer payload — rotor triangles get their
    # node indices offset by n_stator_nodes, and we re-map per-cell domain
    # ids to the visualisation palette (DOM_MAG_N / S, DOM_COIL).
    n_s_nodes = mesh_s.p.shape[1]
    verts = _np.hstack([mesh_s.p, mesh_r.p]).T.tolist()
    tris  = _np.hstack([mesh_s.t, mesh_r.t + n_s_nodes]).T.tolist()
    tags  = _np.concatenate([tags_s, tags_r]).astype(_np.int16)

    polys_s_meshed = getattr(classify_s, "polys", {})
    polys_r_meshed = getattr(classify_r, "polys", {})
    polarities = ([pol for _mp, pol in polys_s_meshed.get("magnets", [])]
                  + [pol for _mp, pol in polys_r_meshed.get("magnets", [])])
    # Magnet tags → N/S, coil tags → DOM_COIL
    mask_coil = tags >= DOM_COIL_BASE
    if _np.any(mask_coil):
        tags[mask_coil] = DOM_COIL
    mask = (tags >= DOM_MAG_BASE) & (tags < DOM_COIL_BASE)
    if _np.any(mask):
        idx = (tags[mask] - DOM_MAG_BASE).astype(int)
        tags[mask] = _np.array(
            [DOM_MAG_N if (j < len(polarities) and polarities[j] > 0)
                       else DOM_MAG_S for j in idx],
            dtype=tags.dtype)

    # Slip-surface (mid_r) interface nodes — used by the renderer to draw
    # the sliding surface as a distinctive ring overlay, and by the solver
    # for the master-slave coupling.  mid_r is the air-gap midline where
    # in_band (rotor side) meets out_band (stator side).
    r_slip = float(polys.get("mid_r_mm", 56.55)) * 1e-3
    iface_s = _find_ring_nodes(mesh_s, r_slip, tol_m=2e-4)
    iface_r = _find_ring_nodes(mesh_r, r_slip, tol_m=2e-4) + n_s_nodes

    domain_counts: Dict[str, int] = {}
    dom_names = {0: "air", 1: "stator", 2: "coil", 3: "airgap",
                 4: "magnet_N", 5: "rotor", 6: "shaft",
                 7: "band", 8: "outer_air", 44: "magnet_S"}
    for d, c in zip(*_np.unique(tags, return_counts=True)):
        domain_counts[dom_names.get(int(d), f"d{d}")] = int(c)

    payload = {
        "rotor_angle_deg":   rotor_angle_deg,
        "n_stator_nodes":    int(n_s_nodes),
        "n_rotor_nodes":     int(mesh_r.p.shape[1]),
        "n_stator_tris":     int(mesh_s.t.shape[1]),
        "n_rotor_tris":      int(mesh_r.t.shape[1]),
        "n_vertices":        len(verts),
        "n_triangles":       len(tris),
        # Honest mesh sizing: what the slider requested vs what was actually
        # meshed (iron is capped to the feature/4 quality floor).
        "mesh_size_mm":            float(mesh_size_mm),
        "effective_mesh_size_mm":  round(float(_eff_mesh), 3),
        "feature_floor_mm":        (round(float(_feat_floor), 3)
                                    if _feat_floor is not None else None),
        "vertices":          verts,
        "triangles":         tris,
        "domain_per_tri":    tags.tolist(),
        "domain_counts":     domain_counts,
        "band_iface_stator": iface_s.tolist(),
        "band_iface_rotor":  iface_r.tolist(),
        "r_band_in_m":       r_slip,
        "r_band_out_m":      r_slip,
        "r_slip_m":          r_slip,
        "extent": [
            min(float(mesh_s.p[0].min()), float(mesh_r.p[0].min())),
            max(float(mesh_s.p[0].max()), float(mesh_r.p[0].max())),
            min(float(mesh_s.p[1].min()), float(mesh_r.p[1].min())),
            max(float(mesh_s.p[1].max()), float(mesh_r.p[1].max())),
        ],
        "note": ("Sliding-band TWO-mesh view (feature/sliding-band-fem).  "
                  "Rotor mesh node coordinates are obtained by rigidly "
                  "rotating the rotor_angle=0 mesh by rotor_angle_deg — "
                  "topology unchanged."),
    }
    _fem_mesh_sb_cache[key] = payload
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 7c. Real FEM solve (scikit-fem) — returns A_z + mesh + torque + losses
# ─────────────────────────────────────────────────────────────────────────────

_fem_field_cache: Dict[tuple, Dict] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Field snapshot produced BY the main transient (Re-run Simulation)
# ─────────────────────────────────────────────────────────────────────────────
# The sliding-band transient already computes, for its LAST frame, exactly the
# payload the field views draw: mesh + A + B + domain tags + the coupled eddy
# Jeddy (when the eddy solve is on) + the cycle-averaged loss density.  It used
# to throw that away, so selecting J⟳ / Loss ran a SECOND full transient and the
# user waited ~25 s for a field the run had already produced.
#
# get_fem_transient now asks for it (return_field=True — a snapshot of the frame
# it just solved, NOT an extra solve) and parks it here; get_fem_field2d serves
# the multi-frame (J⟳ / Loss) views straight out of it when the key matches.
# The key is the physics the snapshot depends on, so a different operating point
# / mesh / geometry can never be served from here — it misses and re-solves.
#
# Memory-only and deliberately small (the last few runs): each entry holds full
# per-node / per-element arrays.  A back-end restart empties it, so the first
# J⟳ view after a restart computes on demand and SAYS so.
_transient_field_snap: "OrderedDict[tuple, Dict]" = OrderedDict()
_TRANSIENT_SNAP_MAX = 3


def _geo_ov_for_key(geo_ov) -> object:
    """Normalise a geo override for the snapshot key.

    The Field view always sends the FULL geometry as a `geo=` override (the
    live store state), while Re-run Simulation sends none and solves the saved
    config — the SAME machine, expressed two ways.  Compared verbatim they can
    never match, so the run's snapshot was unreachable from the view that
    exists to display it (measured: every probe MISSed on geo alone).  An
    override that agrees with the current config geometry — value-for-value
    within float round-trip noise, extra derived keys ignored — is therefore
    keyed as None, i.e. "the config machine".  A override that actually
    DIFFERS keeps its tuple and misses, as it must.

    The reference is `get_current_geometry().to_dict()` — the LIVE parameter
    set, with every derived radius/pitch recomputed — and NOT the raw
    `geometry:` block of motor_config.yaml.  That block keeps whatever derived
    values were last written to it, and they go stale the moment a base
    parameter changes: this machine's YAML still carried stator_outer_radius
    20 mm (a 40 mm design) beside stator_diameter 30, while the frontend sends
    the recomputed 15.  Compared against the YAML the override then "differed"
    on five derived keys and every probe missed — the SAME machine, one side
    reading a stale copy.  `_current_geom_hash_and_params` already overlays the
    override onto exactly this live dict before building the motor, so this is
    also the set the solve is actually made of.
    """
    if not geo_ov:
        return None
    try:
        try:
            from motor_ai_sim.services.geometry_service import (
                get_current_geometry as _gcg)
            _cfg_geo = dict(_gcg().to_dict() or {})
        except Exception:
            from motor_ai_sim.config import get_config as _gc
            _cfg_geo = dict((_gc().get("geometry", {})) or {})
        for _k, _v in geo_ov.items():
            if _k not in _cfg_geo:
                continue          # key the geometry model does not carry
            try:
                # Float round-trip noise: the frontend sends JSON doubles for
                # values the backend computed, so an exact compare fails on the
                # last bit of e.g. 8.899999999999999.
                if abs(float(_v) - float(_cfg_geo[_k])) > 1e-6:
                    return tuple(sorted(geo_ov.items()))
            except (TypeError, ValueError):
                if _v != _cfg_geo[_k]:
                    return tuple(sorted(geo_ov.items()))
        return None               # override == the live machine
    except Exception:
        return tuple(sorted(geo_ov.items()))


def _get_request_materials_safe():
    """This request's `mat=` override, or None — never raising."""
    try:
        from motor_ai_sim.material_context import get_request_materials
        return get_request_materials()
    except Exception:
        return None


def _mat_ov_tuple(mat_ov) -> tuple:
    """Stable, hashable rendering of a material override that genuinely differs."""
    import json as _jm
    return ("mat", _jm.dumps(mat_ov, sort_keys=True, default=str))


def _mat_ov_for_key(mat_ov) -> object:
    """Normalise a per-request material override for the snapshot key.

    Exactly the `geo=` disease, one field over: the Field view goes through the
    fetch interceptor, which appends `mat=<assignment+props>` to every
    /api/simulation/physics request, while Re-run Simulation posts to
    /api/kernel/run — a URL the interceptor does not match — so the RUN solves
    with no override at all and reads the shared config.  The SAME machine
    therefore produced two fingerprints and the view could never find the run's
    snapshot (measured: MISS on cfg_fingerprint alone, every time, for a signed-in
    user with any assignment at all — which is everyone).

    An override is keyed as None ("the config machine") when it changes nothing
    the solver would do: every assigned part names the material the config
    already assigns, AND every shipped prop-set resolves to the same material
    object `materials.get_material` builds from the library.  An override that
    genuinely names a different magnet, or ships custom props, keeps its own key
    and misses — as it must.
    """
    if not mat_ov:
        return None
    try:
        import dataclasses as _dc
        from motor_ai_sim.config import get_material_assignments as _gma
        from motor_ai_sim import materials as _ml
        _cfg_assign = _gma() or {}
        _assign = mat_ov.get("assignment") or {}
        for _k, _v in _assign.items():
            if str(_cfg_assign.get(_k) or "") != str(_v or ""):
                return _mat_ov_tuple(mat_ov)
        # Props only matter for a material that is actually ASSIGNED — the
        # frontend ships the whole "non-builtin in use" set, and an unused entry
        # never reaches the solve.
        _used = {str(_v) for _v in {**_cfg_assign, **_assign}.values() if _v}
        for _name, _pr in (mat_ov.get("materials") or {}).items():
            if _name not in _used:
                continue
            _cat = (_pr or {}).get("category")
            try:
                _a = _ml.material_from_dict(_cat, _name, _pr)
                _b = _ml.get_material(_cat, _name)
            except Exception:
                return _mat_ov_tuple(mat_ov)
            if _b is None or _dc.asdict(_a) != _dc.asdict(_b):
                return _mat_ov_tuple(mat_ov)
        return None            # override == the config machine
    except Exception:
        return _mat_ov_tuple(mat_ov)


def _config_physics_fingerprint(*, with_request_materials: bool) -> str:
    """md5 of the shared config the SOLVE depends on but the URL doesn't carry.

    ONE definition for both routes (it was copy-pasted twice, and the copies were
    already the place the two keys could drift).  `with_request_materials=True`
    folds in this request's `mat=` override verbatim — right for a per-request
    CACHE key, wrong for the cross-request snapshot key, where the same machine
    arrives spelled two ways (see `_mat_ov_for_key`).
    """
    try:
        import hashlib as _hl, json as _jl
        from motor_ai_sim.config import get_config as _gc
        from motor_ai_sim.material_context import get_request_materials as _grm
        _cfg = _gc() or {}
        _d = {"g": _cfg.get("geometry"), "w": _cfg.get("winding"),
              "m": _cfg.get("materials"), "mag": _cfg.get("magnet")}
        # The LIVE geometry object as well as the raw `geometry:` block.  They
        # are not the same thing: the YAML keeps whatever derived radii/pitches
        # were last written to it and only num_poles/num_slots/angle_* get
        # recomputed on save, so the block can sit internally inconsistent
        # (measured: stator_outer_radius 20 beside stator_diameter 30, while the
        # live object said 15) — and it is the LIVE object that every builder on
        # this path actually reads (`_current_geom_hash_and_params`,
        # `_geo_ov_for_key`, CadQueryMotor).  A key that fingerprints the raw
        # block alone is tracking a document, not the machine.
        try:
            from motor_ai_sim.services.geometry_service import (
                get_current_geometry as _gcg)
            _d["glive"] = _gcg().to_dict()
        except Exception:
            _d["glive"] = None
        if with_request_materials:
            _d["req_mat"] = _grm()
        return _hl.md5(_jl.dumps(_d, sort_keys=True,
                                 default=str).encode()).hexdigest()[:16]
    except Exception:
        return "nofp"


def _geometry_fingerprint(geo_override: Optional[dict] = None) -> str:
    """md5 of the MACHINE alone — no operating point, no materials.

    `_config_physics_fingerprint` mixes geometry, winding, materials and the
    request's material override into one hash, which is right for a cache key
    (any of them changes the answer) and useless for the question a restored
    result has to answer: "is this still the same MOTOR?".  A user who switched
    presets and one who nudged the coil temperature both get "key differs", and
    the UI could only say "stale" without saying stale *how* — so the geometry
    case, the one that silently shows the previous machine's torque, looked
    exactly like a harmless input tweak.

    Hashes the live geometry object (what every builder actually reads) plus the
    raw config block, and folds in a per-request `geo=` override, so a candidate
    eval is never mistaken for the motor on screen.
    """
    try:
        import hashlib as _hl, json as _jl
        from motor_ai_sim.config import get_config as _gc
        _d = {"g": (_gc() or {}).get("geometry")}
        try:
            from motor_ai_sim.services.geometry_service import (
                get_current_geometry as _gcg)
            _d["glive"] = _gcg().to_dict()
        except Exception:
            _d["glive"] = None
        if geo_override:
            _d["ov"] = dict(sorted(geo_override.items()))
        return _hl.md5(_jl.dumps(_d, sort_keys=True,
                                 default=str).encode()).hexdigest()[:16]
    except Exception:
        return "nofp"


def _effective_rpm(rpm=None) -> float:
    """The speed a solve will ACTUALLY run at: the explicit argument when the
    caller passed one, else the shared config's ``simulation.rpm``.

    Both physics keys resolve it through here rather than storing the raw
    argument, for two reasons: an explicit 13000 and a config-default 13000 are
    the same solve and must share a cache entry, and — the older hole — the
    config's speed was in NEITHER key, so editing rpm and pressing Run replayed
    a cached result computed at the previous speed (torque unmoved, but P_fe,
    V_peak and eta all belong to a different operating point)."""
    if rpm is not None:
        return float(rpm)
    try:
        from motor_ai_sim.config import get_config as _gc
        return float((_gc().get("simulation") or {}).get("rpm", 3950.0) or 3950.0)
    except Exception:
        return 3950.0


def _effective_winding(n_parallel=None, connection=None) -> tuple:
    """(n_parallel, connection) a solve will ACTUALLY use — explicit arguments
    first, then the connection label, then the shared config.

    Same reasoning as ``_effective_rpm``: an explicit ``2S-2P`` and a config that
    already says ``2S-2P`` are the same solve and must share a cache entry.
    Raises ValueError on an unreadable connection label (the route turns that
    into a 400) — never a silent fallback to one parallel path."""
    from motor_ai_sim.winding import parse_connection as _pc
    _cfgw = {}
    try:
        from motor_ai_sim.config import get_config as _gc
        _cfgw = dict((_gc().get("winding") or {}))
    except Exception:
        pass
    conn = connection if connection is not None else _cfgw.get("connection")
    npar = n_parallel
    if npar is None and connection is not None:
        npar = _pc(connection)[0]
    elif connection is not None:
        _pc(connection)                       # validate even when npar is explicit
    if npar is None:
        npar = _cfgw.get("n_parallel", 1)
    return int(max(1, int(npar or 1))), (str(conn) if conn else "")


def _effective_daxis(daxis_deg=None):
    """The d-axis reference a solve will ACTUALLY use, or None for "measure it".

    Same rule as the rest of the operating point (standing rule, 2026-08-14):
    the value comes from the Simulation tab.  An explicit argument wins; then
    simulation.daxis_deg; a blank/absent setting means the calibration runs.
    A stored value that is not a finite number is a REFUSAL, not a fallback —
    γ measured from a garbage zero is the failure this whole path exists to
    prevent."""
    if daxis_deg is not None:
        _v = float(daxis_deg)
        if not math.isfinite(_v):
            raise HTTPException(status_code=422, detail=(
                "daxis_deg must be a finite angle in degrees; got %r" % (daxis_deg,)))
        return _v % 360.0
    try:
        from motor_ai_sim.config import get_config as _gc
        _raw = (_gc().get("simulation") or {}).get("daxis_deg", None)
    except Exception:
        _raw = None
    if _raw is None or (isinstance(_raw, str) and not _raw.strip()):
        return None                      # blank = measure it
    try:
        _v = float(_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=(
            "the Simulation tab's d-axis (simulation.daxis_deg = %r) is not a "
            "number — clear it to measure the reference, or set a real angle; "
            "the solver will not guess one." % (_raw,)))
    if not math.isfinite(_v):
        raise HTTPException(status_code=422, detail=(
            "the Simulation tab's d-axis is not finite (%r)" % (_raw,)))
    return _v % 360.0


def _field_snap_key_fields(*, gamma_deg, I_phase_rms, mesh_size_mm, min_size_mm,
                           outer_air_factor, n_sectors, stator_fillet_mm,
                           gap_layers, coil_temp_c, comp_mesh, pole_copy,
                           iron_template, geo_mesh, structured_gap, airgap_macro,
                           n_steps_per_period, n_periods, eddy, rotor_eddy,
                           demag, drive, element_order, cfg_fingerprint, geo_ov,
                           mat_ov, rotor_angle0_deg=0.0, rpm=None,
                           n_parallel=None, connection=None) -> "OrderedDict":
    """The snapshot key as NAMED fields, in key order.

    Named because the key is the thing that decides "is this the run the user
    just did?", and when it says no the only useful answer is WHICH field
    disagreed — a bare 26-tuple diff is unreadable, and the two bugs found here
    (geo, materials) both hid behind exactly that unreadability.
    """
    return OrderedDict((
        ("kind", "tfield"),
        ("gamma_deg", round(float(gamma_deg), 1)),
        ("I_phase_rms", round(float(I_phase_rms), 2)),
        ("mesh_size_mm", round(float(mesh_size_mm), 2)),
        ("min_size_mm", round(float(min_size_mm), 2)),
        ("outer_air_factor", round(float(outer_air_factor), 2)),
        ("n_sectors", int(n_sectors) if int(n_sectors) > 1 else -1),
        ("stator_fillet_mm", round(float(stator_fillet_mm), 2)),
        ("gap_layers", round(float(gap_layers), 1)),
        ("coil_temp_c", round(float(coil_temp_c), 1)),
        # Per-part element sizes are keyed VERBATIM, and that is now correct:
        # every entry the parser accepts changes the mesh.  It did not use to —
        # on the geometry-driven path only "outer"/"air" reached the mesher, so a
        # view sending {"magnet": 0.15} MISSED the snapshot of a run that solved
        # the bit-identical mesh without it.  That was fixed where it broke (the
        # mesher now applies stator/rotor/magnet/coil as CDT region sizes and
        # routes shaft/airgap to the gmsh mesher, which applies them), NOT by
        # normalising entries out of this key: a no-op normalisation here can
        # only be a GUESS about what the mesher will do with a value, and a wrong
        # guess serves the picture of a DIFFERENT mesh — the one failure mode
        # this key exists to prevent.  Compare _geo_ov_for_key / _mat_ov_for_key,
        # which normalise only against a reference they can read exactly.
        ("comp_mesh", tuple(sorted((comp_mesh or {}).items()))),
        ("pole_copy", int(bool(pole_copy))),
        ("iron_template", int(bool(iron_template))),
        ("geo_mesh", int(bool(geo_mesh))),
        ("structured_gap", int(bool(structured_gap))),
        ("airgap_macro", int(bool(airgap_macro))),
        ("n_steps_per_period", int(n_steps_per_period)),
        ("n_periods", round(float(n_periods), 2)),
        ("eddy", int(bool(eddy))),
        ("rotor_eddy", int(bool(rotor_eddy))),
        ("demag", int(bool(demag))),
        ("drive", str(drive or "current")),
        ("element_order", int(element_order)),
        ("cfg_fingerprint", str(cfg_fingerprint)),
        ("geo_ov", _geo_ov_for_key(geo_ov)),
        ("mat_ov", _mat_ov_for_key(mat_ov)),
        # The rotor's CAD start angle.  The transient always meshes at 0; a field
        # view asking for a physically rotated rotor is a different mesh and must
        # never be answered from the run's snapshot.
        ("rotor_angle0_deg", round(float(rotor_angle0_deg), 3)),
        # Speed.  The magnetostatic field is speed-independent, but everything
        # the snapshot CARRIES beside it is not: the loss-density map, the eddy
        # J and the back-EMF all scale with f_elec = rpm*pp/60.
        ("rpm", round(_effective_rpm(rpm), 3)),
        # Winding: n_parallel divides the coil current, so it changes the FIELD;
        # the connection label changes the d-axis calibration key.  Resolved, so
        # explicit and config-default spellings of the same machine collide.
        ("winding", _effective_winding(n_parallel, connection)),
    ))


def _field_snap_key(**kw) -> tuple:
    """THE key both routes build — the transient when it stores its snapshot and
    the field view when it looks one up.  One function so the two can never drift
    into "almost the same key" (which would silently serve the wrong picture, or
    silently never hit).

    Everything the SOLVE depends on is in here, including the shared-config
    fingerprint and the normalised per-request geometry / material overrides.
    `n_sectors` is normalised to the effective wedge (>1 = sector, -1 = full ring)
    because the two routes reach it by different routes (auto-symmetry vs GCD
    snapping).
    """
    return tuple(_field_snap_key_fields(**kw).values())


def _log_snap_key_miss(probe: "OrderedDict") -> None:
    """Say WHICH field made the probe miss, against every stored run.

    A miss is a user-visible failure ("No matching simulation run" after the run
    they just did), and it has now been caused twice by one field out of 27
    carrying the same machine in a different spelling.  Logging the diff is how
    the next one is found in a minute instead of a day.
    """
    try:
        _names = list(probe.keys())
        _pv = list(probe.values())
        if not _transient_field_snap:
            log.info("field snapshot MISS: the store is EMPTY (no run has "
                     "stored a snapshot since the backend started)")
            return
        for _i, _stored in enumerate(_transient_field_snap.keys()):
            _d = ["%s: run=%r view=%r" % (_n, _s, _p)
                  for _n, _s, _p in zip(_names, _stored, _pv) if _s != _p]
            log.info("field snapshot MISS vs stored run #%d — %d differing "
                     "field(s): %s", _i, len(_d),
                     "; ".join(_d) or "(none — length/shape mismatch)")
    except Exception as _e:
        log.warning("snapshot-key diff logging failed: %s", _e)


# Fields the relaxed lookup will NEVER cross, because the result would not be a
# near-miss but a picture of a different object.  Geometry is the hard one: the
# outline, the mesh and every element index change, so a snapshot of another
# cross-section is simply the wrong motor on screen.
#
# `mat_ov` is deliberately NOT in here.  A different magnet is the SAME mesh with
# different physics: the picture is of the user's motor, the levels belong to
# another material, and that is a difference a label can carry honestly (it is
# reported in `transient_param_diffs` like any other).  Refusing it would send the
# view back to the analytic fallback, which is the failure this whole mechanism
# exists to remove.  cfg_fingerprint covers the CONFIG-side material assignment
# together with the geometry, so a config material swap is still a hard mismatch —
# only a per-request `mat=` override is soft.
_SNAP_MACHINE_FIELDS = ("geo_ov", "element_order")


def _snap_geometry_fields(kf: dict) -> tuple:
    """The part of cfg_fingerprint's job that must stay hard: same machine."""
    return (kf.get("cfg_fingerprint"), kf.get("geo_ov"))


def _abbrev(v):
    """A key field short enough to print in a label — the material override is a
    whole assignment dict and would otherwise be a paragraph."""
    _t = repr(v)
    return _t if len(_t) <= 60 else _t[:57] + "…"


def _latest_run_snapshot(probe: "OrderedDict"):
    """The most recent stored snapshot OF THE SAME MACHINE, plus what differs.

    The exact key has ~27 fields and both sides have to spell all of them the
    same way.  That has now failed three times in a row for the user — geometry,
    materials, and whatever the next one would have been — and each time the
    symptom was the same: "No matching simulation run" moments after a run that
    matched, and a silent fall back to a single-frame analytic map.  The key is
    still the primary lookup and still exact.  This is the honest fallback: same
    machine, possibly a different operating point or mesh, and the caller is
    handed the LIST of differences so the label can print them.

    Returns ``(entry, diffs)`` or ``(None, [])``.
    """
    try:
        _names = list(probe.keys())
        _pv = list(probe.values())
        for _key in reversed(list(_transient_field_snap.keys())):
            _entry = _transient_field_snap[_key]
            _kf = (_entry.get("meta") or {}).get("key_fields")
            if not _kf:
                continue
            if any(_kf.get(_f) != probe.get(_f) for _f in _SNAP_MACHINE_FIELDS):
                continue                      # a different motor — never serve it
            if _snap_geometry_fields(_kf) != _snap_geometry_fields(probe):
                # cfg_fingerprint carries the geometry (raw + live) AND the
                # config-side material assignment.  Either differing means the
                # run was of a different machine: fall through to a solve rather
                # than show one motor's field on another motor's outline.
                continue
            _diffs = ["%s: run=%r view=%r"
                      % (_n, _abbrev(_kf.get(_n)), _abbrev(_p))
                      for _n, _p in zip(_names, _pv)
                      if _n not in _SNAP_MACHINE_FIELDS and _kf.get(_n) != _p]
            return _entry, _diffs
    except Exception as _e:
        log.warning("relaxed snapshot lookup failed: %s", _e)
    return None, []


def _store_transient_field_snapshot(key: tuple, field: Dict, sbres: Dict,
                                    *, eddy: bool, n_steps_per_period: int,
                                    n_periods: float, solve_time_s: float,
                                    key_fields: "Optional[OrderedDict]" = None
                                    ) -> None:
    """Park the transient's last-frame field + the handful of machine scalars the
    field-view sidebar reads.  Only the scalars get copied — the full transient
    result stays where it is (the transient cache), so this store holds one
    field snapshot, not a second copy of the run."""
    try:
        _transient_field_snap[key] = {
            "field": field,
            # EXACTLY the keys the field-view payload builder reads off the
            # solver result `d`.  Copied (not referenced) so the snapshot cannot
            # be mutated from under the view by a later trim of the transient.
            "scalars": {k: sbres.get(k) for k in (
                "P_cu_total_solve_W", "P_cu_W", "P_fe_W", "P_mag_eddy_W",
                "T_avg_Nm", "rpm", "P_elec_in_W", "f_elec_Hz",
                "P_cu_ac_solve_W", "V_peak", "demag_coef_per_tri",
                "demag_report")},
            "meta": {
                "eddy": bool(eddy),
                "n_steps_per_period": int(n_steps_per_period),
                "n_periods": float(n_periods),
                "computed_at": sbres.get("computed_at"),
                "solve_time_s": round(float(solve_time_s), 1),
                # The run's OWN key, field by field.  Kept so a view that could
                # not match the key exactly can still ask for "the last run's
                # field, whatever it was solved at" and be TOLD what differs
                # (see `latest_run_field`).  Without this the only honest answer
                # to a near-miss was to throw the run away and re-solve.
                "key_fields": (dict(key_fields) if key_fields else None),
            },
        }
        _transient_field_snap.move_to_end(key)
        while len(_transient_field_snap) > _TRANSIENT_SNAP_MAX:
            _transient_field_snap.popitem(last=False)
    except Exception as _e:      # never let a viewer convenience break a solve
        log.warning("could not store transient field snapshot: %s", _e)


class _HaveSolverLossMap(Exception):
    """Skip the analytic single-frame loss estimate — the transient supplied the
    real map.  A sentinel rather than an `if` because the analytic block is one
    long guarded stretch, and an early exit is the honest way to say "this
    fallback does not apply" without duplicating its except-clause."""


def _unmodelled_loss_classes(tags, loss_dens, declared=()) -> list:
    """Material classes the loss map contains NO value for → drawn blank.

    The renderer has exactly two things it can do with a zero: paint it (band 0
    of the colour scale — which on a loss map means "measured, and it is zero
    here") or leave it blank ("no model produced a number for this material").
    Those are different statements and only this list can tell them apart, so it
    is derived from the map that is actually being served rather than trusted
    from upstream: a class every one of whose elements is 0 was written by
    nobody, whichever code path built the array.

    AIR is always in the result, and not by the all-zero rule alone: there IS no
    air-loss model to run.  σ=0 kills eddy current, air has no hysteresis loop,
    and windage is not part of a 2-D magnetic solve — so a coloured air gap is
    always a lie, and this is the flag that stops it being drawn.

    ``declared`` is the solver's own list (``loss_dens_unmodelled``), unioned in:
    it knows things the array cannot show, e.g. that a magnet term came out
    non-zero but from a model this run had no business running.

    ``loss_dens`` must be a float ndarray: any loss found in an air element is
    zeroed IN PLACE (and logged) before the map is served.
    """
    import numpy as _np
    from motor_ai_sim.simulation.sb_domains import (
        DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_COIL, DOM_COIL_BASE, DOM_MAG_BASE,
        DOM_MAG_N, DOM_MAG_S, DOM_ROTOR, DOM_SHAFT, DOM_STATOR)
    t = _np.asarray(tags, int)
    d = _np.asarray(loss_dens, float)
    out = [str(x) for x in (declared or [])]
    if t.size != d.size:                       # nothing trustworthy to say
        return out if "air" in out else out + ["air"]
    _mag = ((t >= DOM_MAG_BASE) & (t < DOM_COIL_BASE)) | (t == DOM_MAG_N) \
        | (t == DOM_MAG_S)
    for name, mask in (
            ("iron",    (t == DOM_STATOR) | (t == DOM_ROTOR)),
            ("magnets", _mag),
            ("copper",  (t >= DOM_COIL_BASE) | (t == DOM_COIL)),
            ("shaft",   t == DOM_SHAFT)):
        if name in out:
            continue
        if mask.any() and not _np.any(d[mask] != 0.0):
            out.append(name)
    if "air" not in out:
        out.append("air")
    # The invariant the picture rests on: air carries no loss.  A non-zero here
    # is an element-index bug upstream, and it is exactly the bug that paints a
    # smooth gradient across the air gap — so it is zeroed and said out loud.
    _air = (t == DOM_AIR) | (t == DOM_AIRGAP) | (t == DOM_BAND)
    _bad = int(_np.count_nonzero(d[_air] != 0.0)) if _air.any() else 0
    if _bad:
        log.error("loss map: %d AIR element(s) carried loss (max %.4g W/m³) — "
                  "zeroed before serving; this is an element-index bug, not "
                  "physics", _bad, float(_np.max(_np.abs(d[_air]))))
        d[_air] = 0.0
    return out


@router.get("/physics/fem_field2d")
def get_fem_field2d(
    rotor_angle_deg:     float = 0.0,
    gamma_deg:           float = 0.0,
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    outer_air_factor:    float = 1.3,
    motion_band:         bool  = True,    # accepted for URL compat (SB always bands)
    band_thickness_mm:   float = 0.4,
    n_sectors:           int   = 4,       # snapped to a valid divisor of GCD(slots, poles)
    stator_fillet_mm:    float = 0.0,
    I_phase_rms:         Optional[float] = None,   # None = use config; 0 = zero-current
    component_mesh:      str   = "",      # JSON {comp: size_mm} per-part mesh size
    demag:               bool  = False,   # show the irreversible-demag %-map
    pole_copy:           bool  = False,   # bit-identical pole/slot template-copy mesh
    iron_template:       bool  = True,    # deterministic template iron (fallback: gmsh)
    geo_mesh:            bool  = True,    # geometry-driven CDT mesh (Mesh-tab toggle)
    structured_gap:      bool  = True,    # ANSYS-style ring gap (merged band)
    airgap_macro:        bool  = False,   # harmonic gap coupling (moving band)
    gap_layers:          float = 2.0,     # radial gap rings (K of the macro ladder)
    geo:                 Optional[str] = None,  # per-request geometry override (multi-user)
    # ── Multi-frame modes (J⟳ / Loss map / thermal source) ───────────────────
    # These used to be a SECOND endpoint (/physics/fem_eddy_field2d) with its own
    # mesh-flag defaults, so the J view solved a DIFFERENT motor than the A_z
    # view beside it — free gmsh gap, no template iron, no geo-mesh — and drew a
    # visibly different outline.  Two endpoints meshing "the same" geometry from
    # two sets of defaults is a chimera by construction; there is one now, so a
    # picture can only disagree with its neighbour if the physics does.
    n_steps_per_period:  int   = 0,       # 0 = single-angle field (the A_z/|B|/J view);
                                          #   >1 = run that many frames and snapshot the
                                          #   LAST — needed for anything with a B(t)
                                          #   history behind it (loss map, eddy J)
    n_periods:           float = 1.0,
    eddy:                bool  = False,   # coupled σ·∂A/∂t solve → the real eddy J⟳
    rotor_eddy:          bool  = False,   # magnet/shaft eddy losses in the loss map
    coil_temp_c:         float = 120.0,
    use_transient_snapshot: bool = True,  # serve the multi-frame views from the LAST
                                          # simulation run's own field snapshot when its
                                          # key matches (see _field_snap_key).  Set false
                                          # to force a fresh solve.
    snapshot_only:       bool  = False,   # PROBE: return the snapshot if one matches,
                                          # otherwise answer {"ok": false, "no_snapshot":
                                          # true} WITHOUT solving.  The Loss view uses it
                                          # to prefer the run's real cycle-averaged map and
                                          # fall back to its own single-frame estimate,
                                          # instead of silently starting a ~25 s solve.
    latest_run_field:    bool  = True,    # RELAXED fallback: when the exact key misses,
                                          # serve the most recent snapshot OF THE SAME
                                          # MACHINE and report every field that differs in
                                          # `source_label`.  ON by default — three separate
                                          # one-field spelling mismatches have sent this
                                          # view to a single-frame analytic map whose
                                          # magnet term is ZERO, and the user hit it on
                                          # every click.  A labelled real field beats an
                                          # unlabelled fake one.  The exact key is still
                                          # tried FIRST and still wins when it hits, and
                                          # this NEVER crosses a machine boundary
                                          # (geometry / materials / element order) — a
                                          # different motor falls through to a solve.
                                          # Pass false to demand an exact match.
):
    """Field view computed by the SLIDING-BAND TRANSIENT solver (P2) — the SAME
    solver that produces the transient torque/losses, so the field picture is
    exactly the per-frame field the transient sweeps and is guaranteed consistent
    with the results.  Cached per (angle, γ, I, mesh, frames, eddy).

    Default (``n_steps_per_period=0``): ONE step with the rotor physically placed
    at ``rotor_angle_deg`` — a true single-angle field.
    ``n_steps_per_period>1``: a real transient whose LAST frame is snapshotted;
    that is what the loss-density map (needs B(t)) and the coupled eddy J need.
    """
    import numpy as _np
    import time as _time

    _comp_mesh = _parse_component_mesh(component_mesh)
    _geo_ov = _parse_geo_override(geo)   # per-request geometry override (multi-user)
    # P2 self-consistency, the SAME two coercions get_fem_transient applies (and
    # for the same reason): the solver refuses the moving / harmonic-macro band
    # on P2 and needs the merged structured belt. This is a substitution, so it
    # is LOGGED — but the alternative is worse than logging it: the field view
    # would 500 with "Harmonic gap" on while the Simulation tab beside it
    # quietly solved the merged belt, i.e. the picture and the numbers would
    # come from different models with nothing on screen saying so.
    if airgap_macro or not structured_gap:
        log.info("fem_field2d: forcing structured_gap=True, airgap_macro=False "
                 "(P2 runs the merged belt; the transient route coerces the "
                 "same way, so the picture matches the numbers)")
        structured_gap = True
        airgap_macro = False
    # Fingerprint what the SOLVE depends on but the URL doesn't carry: the shared
    # config (geometry / winding / material assignment) and this request's per-user
    # material override.  Without it the field view kept serving a picture built
    # from a different design — a changed magnet or a config-side geometry edit
    # left the cached image in place.
    _cfp_f = _config_physics_fingerprint(with_request_materials=True)
    key = (
        "sbfield", round(rotor_angle_deg * 2) / 2, round(gamma_deg, 1),
        round(mesh_size_mm, 2), round(min_size_mm, 2), round(outer_air_factor, 2),
        int(n_sectors), round(stator_fillet_mm, 2),
        round(I_phase_rms, 2) if I_phase_rms is not None else None,
        int(bool(demag)), int(bool(pole_copy)), int(bool(iron_template)), tuple(sorted(_comp_mesh.items())),
        int(bool(geo_mesh)), int(bool(structured_gap)), int(bool(airgap_macro)),
        round(float(gap_layers), 1), _cfp_f,
        int(n_steps_per_period), round(float(n_periods), 2),
        int(bool(eddy)), int(bool(rotor_eddy)), round(float(coil_temp_c), 1),
        # A payload built from the simulation run's snapshot and one solved here
        # are labelled differently, so they are different cache entries — asking
        # for a fresh solve must not replay a snapshot-sourced picture.
        int(bool(use_transient_snapshot)),
        # Same reason, one step further: a caller that DEMANDS an exact key match
        # and one that accepts the last run of this machine can get different
        # payloads with different labels, so they cannot share an entry.  Caught
        # by its own acceptance test — the exact-only probe was being handed the
        # relaxed payload the previous call had just cached.
        int(bool(latest_run_field)),
    )
    if _geo_ov:   # distinct cache entry per overridden geometry (no-geo key unchanged)
        key = key + (tuple(sorted(_geo_ov.items())),)
    if key in _fem_field_cache:
        return _fem_field_cache[key]

    try:
        from motor_ai_sim.simulation.fem_solver_2d import (
            fem_transient_sliding_band, _simplify_polys,
            DOM_MAG_BASE, DOM_COIL_BASE, DOM_MAG_N, DOM_MAG_S, DOM_COIL,
            DOM_STATOR, DOM_ROTOR)
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.config import get_config as _gc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FEM solver unavailable: {e}")

    if I_phase_rms is None:
        I_phase_rms = float(_gc().get("simulation", {}).get("max_current", 85.0))


    # Effective (override-aware) motor: the SAME geometry the solver will build.
    # Used to (a) snap n_sectors to a VALID divisor of GCD(slots, poles) — the
    # old blind default 4 built a broken wedge on e.g. 12s14p (GCD 2) and the
    # field view showed a corrupt picture; (b) draw palette/outlines from the
    # requested geometry instead of the global config.
    motor = CadQueryMotor()
    if _geo_ov:
        motor.set_parameters(_geo_ov)
    _mp = motor.parameters
    _slots = int(_mp.get("num_slots") or 0)
    _poles = int(_mp.get("num_poles") or 0)
    _gcd = math.gcd(_slots, _poles) if (_slots and _poles) else 1
    _ns_req = int(n_sectors)
    _ns_eff = -1                                     # full disk
    if _ns_req > 1:
        _valid = [dv for dv in range(2, _gcd + 1) if _gcd % dv == 0]
        _ns_eff = max([dv for dv in _valid if dv <= _ns_req], default=-1)
        if _ns_eff != _ns_req:
            log.info("fem_field2d: n_sectors=%d invalid for %ds%dp (GCD %d) — using %s",
                     _ns_req, _slots, _poles, _gcd,
                     "full disk" if _ns_eff < 0 else f"1/{_ns_eff}")
    else:
        # P2 AUTO-SYMMETRY — the SAME rule get_fem_transient applies when the
        # caller leaves n_sectors at "full" (see its element_order==2 block):
        # solve the machine's natural anti-periodic wedge num_seg =
        # gcd(slots, poles).  This is not a speed default: the transient beside
        # this view already does it, so a full-disk field view was solving a
        # DIFFERENT model than the numbers next to it — and could never be
        # served from the run's own snapshot.  The wedge is tiled back to the
        # full ring for display (symmetry_mult / anti_periodic below).
        try:
            from motor_ai_sim.config import get_config as _gc_sym
            _g_sym = dict((_gc_sym().get("geometry", {})) or {})
            if _geo_ov:
                _g_sym = {**_g_sym, **_geo_ov}
            _sym = int(_g_sym.get("num_seg")
                       or math.gcd(int(_g_sym.get("num_slots", 1)),
                                   int(_g_sym.get("num_poles", 1))) or 1)
        except Exception:
            _sym = _gcd
        if _sym >= 2:
            _ns_eff = _sym
            log.info("fem_field2d: n_sectors<=1 → P2 auto-symmetry 1/%d "
                     "(same wedge the transient solves)", _sym)

    # 1 step (rotor pinned at the requested angle) → a true single-angle field
    # from the sliding-band machinery.  demag needs a short sweep for the
    # worst-case knee pre-pass, so use a few steps and snapshot the FIRST one.
    # A caller that asked for a real transient (loss map, eddy J) gets its
    # frame count and the LAST frame — the rotor has to have MOVED for a
    # B(t)-derived quantity to mean anything.
    _sweep = int(n_steps_per_period) > 1
    _nsteps = int(n_steps_per_period) if _sweep else (8 if demag else 1)

    # ── Serve the multi-frame views from the LAST SIMULATION RUN ──────────────
    # A J⟳ / Loss request IS a sliding-band transient — the same one the
    # Simulation tab just ran.  If that run's snapshot matches this request's
    # physics key exactly, use it: the field is already computed, and re-solving
    # it made the user wait ~25 s for a picture the machine already had.
    # A single-angle view (_sweep False) is NOT the same physics (rotor pinned,
    # no B(t) history) and never comes from here.
    _snap = None
    _relaxed_diffs: list = []
    if _sweep and use_transient_snapshot:
        _probe_fields = _field_snap_key_fields(
            gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor, n_sectors=_ns_eff,
            stator_fillet_mm=stator_fillet_mm, gap_layers=gap_layers,
            coil_temp_c=coil_temp_c, comp_mesh=_comp_mesh,
            pole_copy=pole_copy, iron_template=iron_template, geo_mesh=geo_mesh,
            structured_gap=structured_gap, airgap_macro=airgap_macro,
            n_steps_per_period=_nsteps, n_periods=float(n_periods),
            eddy=eddy, rotor_eddy=rotor_eddy, demag=demag,
            drive="current", element_order=2,
            cfg_fingerprint=_config_physics_fingerprint(
                with_request_materials=False),
            geo_ov=_geo_ov, mat_ov=_get_request_materials_safe(),
            rotor_angle0_deg=float(rotor_angle_deg))
        _snap = _transient_field_snap.get(tuple(_probe_fields.values()))
        if _snap is None:
            _log_snap_key_miss(_probe_fields)
        # RELAXED fallback, only when the caller asked for it: the last run of
        # the SAME machine, with every difference reported.  This exists because
        # the exact key has 27 fields that two independent request paths must
        # spell identically, and the alternative to a near-miss was a silent
        # fall back to a single-frame analytic map whose magnet term is zero.
        if _snap is None and latest_run_field:
            _snap, _relaxed_diffs = _latest_run_snapshot(_probe_fields)
            if _snap is not None:
                log.info("fem_field2d: exact key missed — serving the last run "
                         "of the SAME machine (%d differing field(s): %s)",
                         len(_relaxed_diffs), "; ".join(_relaxed_diffs) or "none")

    _t0 = _time.time()
    if _snap is not None:
        d = dict(_snap["scalars"])
        fld = _snap["field"]
        _src = ("transient-snapshot" if not _relaxed_diffs
                else "transient-snapshot (relaxed match)")
        _meta = _snap["meta"]
        _kf_run = (_meta.get("key_fields") or {})
        _snap_desc = {
            "I": ("%.4g A" % _kf_run["I_phase_rms"]
                  if _kf_run.get("I_phase_rms") is not None else "? A"),
            "gamma": ("gamma %.4g deg" % _kf_run["gamma_deg"]
                      if _kf_run.get("gamma_deg") is not None else "gamma ?"),
        }
        log.info("fem_field2d: served from the last simulation run's field "
                 "snapshot (%d steps/period, eddy=%s, computed %s) — no solve",
                 _meta.get("n_steps_per_period"), _meta.get("eddy"),
                 _meta.get("computed_at"))
    elif snapshot_only:
        # Probe with nothing to serve: say so, do NOT start a solve behind it.
        return {
            "ok": False, "no_snapshot": True,
            "reason": ("no field snapshot from a simulation run matches this "
                       "operating point / mesh / frame count"
                       + ("" if _sweep else " (single-angle views are never "
                                            "served from a transient snapshot)")),
        }
    else:
        _src = "on-demand solve"
        _meta = {}
        try:
            d = fem_transient_sliding_band(
                n_steps_per_period=_nsteps,
                n_periods=float(n_periods) if _sweep else 1.0,
                gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
                mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
                outer_air_factor=float(outer_air_factor),
                n_sectors=_ns_eff,
                stator_fillet_mm=float(stator_fillet_mm),
                coil_temp_c=float(coil_temp_c),
                eddy=bool(eddy), rotor_eddy=bool(rotor_eddy), demag=bool(demag),
                return_field=True, field_first=not _sweep,
                rotor_angle0_deg=float(rotor_angle_deg),
                pole_copy=bool(pole_copy),
                iron_template=bool(iron_template),
                geo_mesh=bool(geo_mesh),
                structured_gap=bool(structured_gap),
                airgap_macro=bool(airgap_macro),
                gap_layers=float(gap_layers),
                component_mesh_mm=_comp_mesh,
                geo_override=_geo_ov,
                element_order=2)
        except Exception as e:
            log.exception("SB field solve failed")
            raise HTTPException(status_code=500, detail=f"FEM solve failed: {e}")
        fld = d.get("field")
        if not fld:
            raise HTTPException(status_code=500,
                                detail="SB field solve returned no snapshot")

    P  = _np.asarray(fld["P_mm"]) * 1e-3
    T  = _np.asarray(fld["T"])
    A  = _np.asarray(fld["A"])
    Bx = _np.asarray(fld["Bx"]); By = _np.asarray(fld["By"])
    Bmag = _np.sqrt(Bx ** 2 + By ** 2)
    # J: the coupled solve's EDDY density σ(−∂A/∂t + U) when it ran, else the
    # applied SOURCE density.  The eddy J is nodal (as it is in the solve) —
    # average it onto elements so both cases hand the renderer the same shape.
    if eddy and fld.get("Jeddy") is not None:
        Jtri = _np.asarray(fld["Jeddy"], float)[T].mean(axis=0)
    else:
        Jtri = _np.asarray(fld.get("Jtri_src", _np.zeros(T.shape[1])))
    tags = _np.asarray(fld["tags"]).astype(int)

    # Collapse per-wire / per-magnet tags → renderer palette (rotor at angle).
    # `motor` already carries the geo override — palette/outlines match the
    # requested geometry, not the global config.
    polys = _simplify_polys(
        motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle_deg)),
        tol_mm=0.005, stator_fillet_mm=float(stator_fillet_mm))
    tags_vis = tags.copy()
    tags_vis[tags >= DOM_COIL_BASE] = DOM_COIL
    for i, (mp, pol) in enumerate(polys.get("magnets", []) or []):
        tags_vis[tags == (DOM_MAG_BASE + i)] = (DOM_MAG_N if pol > 0 else DOM_MAG_S)

    # ── Loss-density map [W/m³] ──────────────────────────────────────────────
    # A real transient (n_steps_per_period>1) carries a B(t) history, so the
    # solver's OWN map — the one normalised to the reported component watts —
    # comes back in the snapshot and is used as-is.  Anything else is a single
    # magnetostatic frame with no history, and falls back to the analytic
    # estimate below.
    _loss_dens = _np.zeros(int(T.shape[1]))
    _ld_solver = _np.asarray(fld.get("loss_dens") or [], float)
    # WHAT the picture is, in the picture's own words — the solver writes it
    # component by component (which came from the coupled σE² solve, which from
    # a normalised model), and the view prints it verbatim.  A map whose magnet
    # term is a smeared model and one whose magnet term is the solved eddy
    # current look different and ARE different; the label is how the user can
    # tell without reading the backend log.
    _loss_label = ""
    # Material classes NO loss model produced a value for.  The view leaves them
    # BLANK: on a loss map the bottom of the colour scale is what air looks like,
    # so painting an unmodelled magnet there says "no loss in the magnets", which
    # is not what "we did not model it" means.
    _loss_unmodelled: list = []
    if _ld_solver.size == int(T.shape[1]):
        _loss_dens = _ld_solver
        _loss_label = str(fld.get("loss_dens_label") or
                          "cycle-averaged loss density from the transient")
        _loss_unmodelled = [str(x) for x in (fld.get("loss_dens_unmodelled")
                                             or [])]
    else:
        _loss_label = ("single-frame analytic estimate — Bertotti(|B|) iron, "
                       "slab-eddy magnets, ρ·J² copper (no B(t) history)")
    # ── Single-frame ANALYTIC loss-density map [W/m³] ────────────────────────
    # The Loss view is ONE magnetostatic frame (like |B|): estimate the local loss
    # density from THIS frame's B / J — no multi-frame transient.  Frames are run
    # ONLY for the field animation.
    #   iron   : Bertotti   p = kh·f·B² + kc·f²·B² + ke·f^1.5·B^1.5
    #   magnet : slab eddy   p = σ·(d·ω·B)² / 24     (d = magnet tangential width)
    #   copper : resistive   p = ρ_Cu(T)·J²
    # Approximate (local instantaneous |B| as the amplitude proxy) but instant and
    # shows WHERE losses concentrate, matching the |B|/J views' 1-frame speed.
    try:
        if _ld_solver.size == int(T.shape[1]):
            raise _HaveSolverLossMap
        from motor_ai_sim import materials as _ml2
        from motor_ai_sim.config import (get_config as _gcfg2,
                                          get_material_assignments as _gma2)
        _cfg2 = _gcfg2() or {}
        _sim2 = _cfg2.get("simulation") or {}
        _rpm = float(_sim2.get("rpm", 0.0) or 0.0)
        _ctemp = float(_sim2.get("coil_temp_c", 120.0) or 120.0)
        _felec = _rpm * (max(int(_poles), 2) // 2) / 60.0
        _wel = 2.0 * math.pi * _felec
        _asg = _gma2() or {}
        if _felec > 0:
            for _dom, _mkey in ((DOM_STATOR, "stator_core"), (DOM_ROTOR, "rotor_core")):
                _msk = (tags == _dom)
                if not _msk.any():
                    continue
                try:
                    _kh, _kc, _ke = _ml2.effective_bertotti(
                        _ml2.get_material("steel", _asg.get(_mkey)))
                except Exception:
                    continue
                _b = Bmag[_msk]
                _loss_dens[_msk] = (_kh * _felec * _b ** 2 + _kc * _felec ** 2 * _b ** 2
                                    + _ke * _felec ** 1.5 * _np.power(_b, 1.5))
            _mmsk = (tags >= DOM_MAG_BASE) & (tags < DOM_COIL_BASE)
            if _mmsk.any():
                try:
                    _sig = float(getattr(_ml2.get_material("magnet", _asg.get("magnet")),
                                         "sigma", 0.0) or 0.0)
                except Exception:
                    _sig = 0.0
                _rout = float(_mp.get("rotor_outer_radius", 0.0) or 0.0) * 1e-3
                _dtan = (2.0 * math.pi * _rout / max(int(_poles), 2)
                         * float(_mp.get("magnet_fill_up", 0.5) or 0.5))
                if _sig > 0 and _dtan > 0:
                    _b = Bmag[_mmsk]
                    _loss_dens[_mmsk] = _sig * (_dtan * _wel * _b) ** 2 / 24.0
        _cmsk = (tags >= DOM_COIL_BASE)
        if _cmsk.any():
            _rho_cu = 1.724e-8 * (1.0 + 0.00393 * (_ctemp - 20.0))
            _loss_dens[_cmsk] = _rho_cu * (Jtri[_cmsk] ** 2)
    except _HaveSolverLossMap:
        pass                       # the transient's own map is already in place
    except Exception as _le:
        log.warning("field-view loss density failed: %s", _le)
        _loss_dens = _np.zeros(int(T.shape[1]))
        _loss_label = "loss density unavailable (%s)" % _le

    # ── which material classes this map does NOT contain ─────────────────────
    # Derived from the map itself (a class with no non-zero element anywhere is
    # a class no model wrote into), UNIONED with whatever the solver declared.
    # Both loss paths run through here — the transient's own map and the
    # single-frame analytic estimate, which has never had a shaft or an air
    # term either — so the view gets the same guarantee from both: a class in
    # this list is drawn BLANK, and a zero inside a class NOT in this list is a
    # real, modelled zero.  Air is in it by construction: σ=0, no hysteresis,
    # windage is not a magnetic solve, so there is nothing to draw in the gap.
    _loss_dens = _np.asarray(_loss_dens, float)      # zeroed in place below
    _loss_unmodelled = _unmodelled_loss_classes(tags, _loss_dens,
                                                _loss_unmodelled)

    nsec = _ns_eff if _ns_eff > 1 else 1      # ACTUAL model symmetry (full = 1)
    result = {
        "ok": True,
        "loss_density_per_tri": _loss_dens.tolist(),
        "loss_dens_max": float(_loss_dens.max()) if _loss_dens.size else 0.0,
        "loss_density_label": _loss_label,
        "loss_density_unmodelled": _loss_unmodelled,
        "n_vertices": int(P.shape[1]), "n_triangles": int(T.shape[1]),
        "vertices": P.T.tolist(), "triangles": T.T.tolist(),
        "domain_per_tri": tags_vis.tolist(),
        "A_z_per_node": A.tolist(),
        "Bmag_per_tri": Bmag.tolist(),
        "J_z_per_tri": Jtri.tolist(),
        "extent": [float(P[0].min()), float(P[0].max()),
                   float(P[1].min()), float(P[1].max())],
        "outlines": _outlines_from_polys(polys),
        "A_z_min": float(A.min()), "A_z_max": float(A.max()),
        "B_mag_max": float(Bmag.max()),
        "n_sectors": nsec, "symmetry_mult": nsec,
        "poles_per_sector": (int(_poles // nsec) if nsec > 1 and _poles else 0),
        # Anti-periodic radial-cut BC (A_z flips sign between adjacent sectors)
        # ⇔ an ODD number of poles per sector.  The full-ring display tiling needs
        # this to place each rotated copy with the right sign.
        "anti_periodic": bool(nsec > 1 and _poles and (_poles // nsec) % 2 == 1),
        "solve_time_s": round(_time.time() - _t0, 1), "total_time_s": 0.0,
        # WHERE this picture came from.  A view that re-solves and a view that
        # replays the simulation run's own last frame are not the same claim,
        # so the payload says which one it is and the header prints it.
        "source": _src,
        "from_transient": bool(_snap is not None),
        "from_transient_relaxed": bool(_relaxed_diffs),
        "transient_param_diffs": list(_relaxed_diffs),
        "source_label": (
            (f"from last simulation run — its own last frame "
             f"({_meta.get('n_steps_per_period')} steps/period"
             + (", coupled eddy solve" if _meta.get("eddy") else "")
             + (f", solved in {_meta.get('solve_time_s')} s at "
                f"{_meta.get('computed_at')}" if _meta.get("computed_at") else "")
             + ")"
             # A relaxed match is the same MACHINE but not the same request, so
             # the label states the run's OWN operating point and then every
             # field that differs.  Naming both is the whole licence for serving
             # it: the user has to be able to see, without opening a log, that
             # this picture is of their motor but not of the panel's numbers.
             + ((" — solved at %s, %s"
                 % (_snap_desc.get("I"), _snap_desc.get("gamma"))
                 + "; DIFFERS from this view: " + "; ".join(_relaxed_diffs))
                if _relaxed_diffs else ""))
            if _snap is not None else
            (f"computed on demand — a {_nsteps}-step transient run for this view"
             if _sweep else "computed on demand — single-angle field")),
        # The run this snapshot came from (absent on the on-demand path).
        "transient_steps_per_period": _meta.get("n_steps_per_period"),
        "transient_computed_at": _meta.get("computed_at"),
    }
    # A real transient also produces the machine numbers the J⟳ sidebar and the
    # thermal solve read off this payload.  A single frame produces none of them
    # honestly, so they are only added when the frames were actually swept.
    if _sweep:
        def _mean(kk):
            s = d.get(kk) or [0.0]
            return float(_np.mean(_np.asarray(s, float))) if len(s) else 0.0
        # Copper: the coupled solve reports the SOLVED total; without it the
        # transient's own DC+AC series is the honest number.
        Pcu = (float(d.get("P_cu_total_solve_W", 0.0)) if eddy else _mean("P_cu_W"))
        Pfe = _mean("P_fe_W"); Pmag = _mean("P_mag_eddy_W")
        Tavg = float(d.get("T_avg_Nm", 0.0)); rpm = float(d.get("rpm", 0.0))
        ploss = Pcu + Pfe + Pmag
        # Same convention as the Simulation summary (2026-08-04): shaft out over
        # (shaft out + every reported loss).  The solved-terminal P_elec_in
        # lacks the post-processed losses (analytic iron, end-winding copper),
        # so dividing by it read optimistic.  At no-load T·ω is cogging noise
        # and Pmech<=0 → eff reports 0, same as before.
        Pelec = float(d.get("P_elec_in_W", 0.0))
        Pmech = Tavg * 2.0 * _np.pi * rpm / 60.0
        eff = (Pmech / (Pmech + ploss)) if (Pmech > 0 and (Pmech + ploss) > 1.0) else 0.0
        result.update({
            "eddy": bool(eddy),
            "rpm": rpm, "freq_Hz": round(float(d.get("f_elec_Hz", 0.0)), 2),
            "T_em_Nm": round(Tavg, 3),
            "P_cu_W": round(Pcu, 1), "P_fe_W": round(Pfe, 1),
            "P_mag_eddy_W": round(Pmag, 1), "P_loss_total_W": round(ploss, 1),
            "P_mech_W": round(Pmech, 1), "efficiency": round(eff, 4),
            "P_cu_ac_solve_W": round(float(d.get("P_cu_ac_solve_W", 0.0)), 1),
            "V_peak": round(float(d.get("V_peak", 0.0)), 1),
        })
    # Demag %-map + per-magnet knee report (only when demag modelling is on).
    _dc = d.get("demag_coef_per_tri")
    if _dc is not None and len(_dc) == int(T.shape[1]):
        result["demag_coef_per_tri"] = list(_dc)
    if d.get("demag_report"):
        result["demag_report"] = d["demag_report"]

    result["total_time_s"] = result["solve_time_s"]
    _fem_field_cache[key] = result
    return result


_thermal_field_cache: Dict[tuple, Dict] = {}


def _thermal_k(category: str, name, default: float) -> float:
    """Thermal conductivity [W/m·K] of a named material, or a category default."""
    if not name:
        return default
    try:
        from motor_ai_sim.materials import get_material
        v = getattr(get_material(category, name), "thermal_conductivity", None)
        return float(v) if v else default
    except Exception:
        return default


def _thermal_k_any(name, default: float) -> float:
    """Thermal conductivity of a material whose category is unknown (e.g. shaft)."""
    if not name:
        return default
    from motor_ai_sim.materials import get_material
    for cat in ("conductor", "steel", "magnet"):
        try:
            v = getattr(get_material(cat, name), "thermal_conductivity", None)
            if v:
                return float(v)
        except Exception:
            pass
    return default


# ── Cooling-system models — the whole outer stator surface (housing) is cooled ──
# Fluid properties (rho kg/m3, cp J/kgK, k W/mK, nu m2/s, Pr) come from the
# materials library (config/materials_library.yaml → `coolant:`), so every fluid
# used in the model is also a catalogued material.  The table below is only a
# safety fallback if the library is missing an entry.
_COOLANT_FALLBACK = {
    "water":            (1000.0, 4186.0, 0.60, 1.0e-6, 7.0),
    "water_glycol_50":  (1070.0, 3300.0, 0.40, 3.0e-6, 25.0),
    "oil":              (860.0,  2000.0, 0.14, 4.0e-5, 280.0),
    "ethylene_glycol":  (1110.0, 2400.0, 0.25, 1.5e-5, 150.0),
    "air":              (1.16,   1007.0, 0.0263, 1.56e-5, 0.707),   # ~300 K
}


def _coolant_props(name: str):
    """(rho, cp, k, nu, Pr) for a coolant — read FROM the materials library so the
    catalogue is the single source of truth; falls back to the built-in table so
    the thermal model never breaks on a missing/renamed entry."""
    try:
        from motor_ai_sim import materials as _mat
        return _mat.get_coolant(name).props_tuple
    except Exception:
        return _COOLANT_FALLBACK.get(name, _COOLANT_FALLBACK["water"])


def _cooling_bc(*, mode: str, t_ambient_c: float, air_speed_mps: float,
                fluid: str, fluid_temp_in_c: float, flow_lpm: float,
                p_loss_w: float, r_housing_m: float, length_m: float,
                h_manual: float, fluid_temp_out_c: float = 0.0):
    """Convert a cooling-system spec into the housing Robin BC (h, T_sink) plus
    reportable extras.  Cooling acts on the full outer stator surface (area A).

      • manual : use the supplied h_manual + ambient (legacy behaviour).
      • air    : forced convection over the housing as a cylinder in cross-flow
                 (Churchill-Bernstein) from the air speed, natural-convection floor.
                 'Heat we can blow off' = h·A·ΔT (= the losses at equilibrium).
      • liquid : coolant carries the losses → T_out = T_in + P/(ṁ·cp); the BC sink
                 is the MEAN coolant temp; h is a flow-scaled water-jacket estimate.
    """
    import math
    A = 2.0 * math.pi * max(r_housing_m, 1e-4) * max(length_m, 1e-3)   # housing area [m²]

    if mode == "liquid":
        rho, cp, kf, nu, Pr = _coolant_props(fluid)
        # INVERTED liquid model: the user sets the inlet AND target outlet temps.
        # The flow rate needed to carry the losses with that ΔT is DERIVED from the
        # energy balance (ṁ = P/(cp·ΔT)), and the housing (outer contour) is HELD at
        # the OUTLET coolant temp — the hottest the jacket reaches, a conservative
        # reference.  Bigger chosen ΔT → less flow required.
        t_in  = float(fluid_temp_in_c)
        t_out = max(float(fluid_temp_out_c), t_in + 0.1)         # outlet must exceed inlet
        dT    = t_out - t_in
        m_dot = (p_loss_w / (cp * dT)) if dT > 1e-6 else 0.0     # required mass flow [kg/s]
        flow_lpm_req = (m_dot / max(rho, 1e-6)) * 60000.0        # → L/min  (DERIVED, reported)
        t_sink = t_out                                           # outer contour = outlet temp
        h = max(2.0e4, p_loss_w / (A * 0.05))                    # pin housing to t_out (film drop ≤0.05°C)
        extras = {
            "mode": "liquid", "fluid": fluid, "flow_lpm": round(flow_lpm_req, 2),
            "fluid_temp_in_c": round(t_in, 1), "fluid_temp_out_c": round(t_out, 1),
            "fluid_dT_c": round(dT, 1), "m_dot_kg_s": round(m_dot, 4),
            "housing_area_m2": round(A, 4), "heat_removed_W": round(p_loss_w, 1),
            "flow_auto": True,
        }
        return h, t_sink, extras

    if mode == "air":
        rho, cp, ka, nu, Pr = _coolant_props("air")
        D = 2.0 * max(r_housing_m, 1e-4)
        v = max(air_speed_mps, 0.0)
        Re = v * D / nu if v > 0.0 else 0.0
        h_forced = 0.0
        if Re > 1.0:
            Nu = (0.3 + (0.62 * Re ** 0.5 * Pr ** (1 / 3))
                  / (1 + (0.4 / Pr) ** (2 / 3)) ** 0.25
                  * (1 + (Re / 282000.0) ** (5 / 8)) ** (4 / 5))
            h_forced = Nu * ka / D
        h_nat = 7.0                       # still-air natural-convection floor
        h = max(h_forced, h_nat)
        extras = {
            "mode": "air", "air_temp_c": round(t_ambient_c, 1),
            "air_speed_mps": round(v, 2), "Re": round(Re, 0), "h_conv": round(h, 1),
            "regime": "forced" if h_forced > h_nat else "natural",
            "housing_area_m2": round(A, 4), "heat_removed_W": round(p_loss_w, 1),
            "cooling_capacity_W_per_K": round(h * A, 2),
        }
        return h, t_ambient_c, extras

    # manual (legacy): caller-supplied h + ambient
    return h_manual, t_ambient_c, {"mode": "manual", "h_conv": round(h_manual, 1),
                                   "housing_area_m2": round(A, 4)}


@router.get("/physics/thermal_field2d")
def get_thermal_field2d(
    ambient_temp:       float = 25.0,    # coolant / ambient [°C]
    h_conv:             float = 50.0,    # housing convection coeff [W/m²·K] (manual mode)
    slot_k:             float = 0.0,     # slot transverse k [W/m·K]; 0 = auto from winding (Cu fill + enamel)
    gap_k:              float = 0.0,     # air-gap k [W/m·K]; 0 = auto (Taylor-enhanced from rpm)
    rpm:                float = 0.0,     # rotor speed [rpm] (from Simulation) → gap Taylor number
    gamma_deg:          float = 0.0,
    I_phase_rms:        float = 120.0,
    n_steps_per_period: int   = 12,
    n_periods:          float = 2.0,
    mesh_size_mm:       float = 3.0,
    min_size_mm:        float = 0.3,
    outer_air_factor:   float = 1.3,
    n_sectors:          int   = 4,
    coil_temp_c:        float = 120.0,
    component_mesh:     str   = "",
    geo:                Optional[str] = None,
    cooling_mode:       str   = "manual",   # "manual" | "air" | "liquid"
    air_speed_mps:      float = 0.0,        # air: airflow speed over the housing
    fluid:              str   = "water",    # liquid: coolant name (materials lib `coolant:`)
    fluid_temp_in_c:    float = 25.0,       # liquid: inlet temperature
    fluid_temp_out_c:   float = 0.0,        # liquid: target OUTLET temp (housing held here; flow derived)
    flow_lpm:           float = 0.0,        # liquid: volumetric flow [L/min] (legacy; now derived from ΔT)
):
    """Steady-state 2-D thermal map. Runs the EM eddy solve for the loss field
    (cached), then solves −∇·(k∇T)=q on the same mesh with convection at the
    housing.  Returns the temperature field, heat flux, and per-component T_max."""
    import numpy as _np
    _geo_ov = _parse_geo_override(geo)

    # Auto-refine the COIL mesh for the thermal solve.  A slot meshed ~1 element
    # across thermally shorts the windings to the iron (every coil node sits on
    # the slot wall, shared with k≈25 steel) → no interior node can heat up and the
    # winding hotspot collapses, regardless of the (correct) homogenised slot_k.
    # Target ~4 elements across the slot width so the winding gradient resolves.
    try:
        from motor_ai_sim.config import get_config as _get_cfg
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _merge_geo
        # merge_geo_override, not a dict-update: slot_width is DERIVED and the
        # override carries primaries only, so the update sized the thermal coil
        # mesh from whatever design the shared config held.
        _g0 = _merge_geo(dict(_get_cfg().get("geometry", {})), _geo_ov)
        _slot_w = float(_g0.get("slot_width", 3.0) or 3.0)
        _cm0 = _parse_component_mesh(component_mesh)
        if "coil" not in _cm0:
            import json as _json
            _cm0["coil"] = round(max(0.4, min(_slot_w / 4.0, float(mesh_size_mm))), 3)
            component_mesh = _json.dumps(_cm0)
    except Exception:
        pass

    key = ("thermal", round(ambient_temp, 1), round(h_conv, 1), round(slot_k, 3),
           round(gap_k, 3), round(rpm, 1), round(gamma_deg, 1), round(I_phase_rms, 1),
           int(n_steps_per_period), round(n_periods, 2), round(mesh_size_mm, 2),
           round(min_size_mm, 2), round(outer_air_factor, 2), int(n_sectors),
           round(coil_temp_c, 1), component_mesh,
           cooling_mode, round(air_speed_mps, 2), fluid,
           round(fluid_temp_in_c, 1), round(fluid_temp_out_c, 1), round(flow_lpm, 2))
    if _geo_ov:
        key = key + (tuple(sorted(_geo_ov.items())),)
    if key in _thermal_field_cache:
        return _thermal_field_cache[key]

    # 1. EM losses on the mesh (reuses the field cache → cheap on repeat).
    # Same endpoint as every other field view, so the thermal map is solved on
    # the SAME mesh the user is looking at — it used to come from the separate
    # eddy endpoint, whose mesh defaults differed.
    em = get_fem_field2d(
        gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
        n_steps_per_period=n_steps_per_period, n_periods=n_periods,
        eddy=True, rotor_eddy=True,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, n_sectors=n_sectors,
        coil_temp_c=coil_temp_c, component_mesh=component_mesh, geo=geo)
    verts = _np.asarray(em["vertices"], float)         # (n,2) metres
    tris = _np.asarray(em["triangles"], int)            # (m,3)
    tags = _np.asarray(em["domain_per_tri"], int)       # collapsed palette tags
    loss_dens = _np.asarray(em.get("loss_density_per_tri") or [], float)
    if loss_dens.size != tris.shape[0]:
        loss_dens = _np.zeros(tris.shape[0])
    Pcu = float(em.get("P_cu_W", 0.0))

    # 2. geometry: housing radius + copper volume (for the copper heat density)
    from motor_ai_sim.config import get_config
    cfg = get_config()
    # merge_geo_override, not a dict-update: the winding thermal model below
    # reads the DERIVED slot_width (copper fill, bulk transverse k), which the
    # override does not carry — a plain update left the config's value in place.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _merge_geo
    g = _merge_geo(dict(cfg.get("geometry", {})), _geo_ov)
    R_house = float(g.get("stator_diameter", 200.0)) / 2.0 * 1e-3
    stator_inner_m = R_house - float(g.get("core_thickness", 5.0)) * 1e-3 \
        - float(g.get("slot_height", 18.0)) * 1e-3
    rotor_outer_m = stator_inner_m - float(g.get("air_gap", 0.6)) * 1e-3

    # Air-gap effective conductivity — ROTATION-ENHANCED (Taylor–Couette).  At rest
    # the gap is still-air conduction (~0.03 W/m·K); as the rotor spins, Taylor
    # vortices stir the gap air and raise the effective cross-gap k.  We size it from
    # the gap Taylor number with the Becker–Kaye Nusselt correlation, using the rotor
    # speed from the Simulation tab.  Bigger radius / wider gap / higher rpm → more
    # enhancement.  Pass gap_k>0 to override with a fixed value.
    if float(gap_k) > 0.0:
        gap_k_eff = float(gap_k); gap_Ta = None; gap_Nu = 1.0
    else:
        _r_m   = max((rotor_outer_m + stator_inner_m) / 2.0, 1e-4)   # mean gap radius [m]
        _delta = max(stator_inner_m - rotor_outer_m, 1e-5)           # radial gap thickness [m]
        _omega = abs(float(rpm)) * 2.0 * _np.pi / 60.0               # rotor angular speed [rad/s]
        _k_air  = 0.030                                              # air k at ~75 °C gap temp [W/m·K]
        _nu_air = 2.0e-5                                             # air kinematic viscosity ~75 °C [m²/s]
        gap_Ta = (_omega ** 2) * _r_m * (_delta ** 3) / (_nu_air ** 2)
        if gap_Ta < 1700.0:
            gap_Nu = 1.0                                             # sub-critical → pure conduction
        elif gap_Ta <= 1.0e4:
            gap_Nu = 0.128 * gap_Ta ** 0.367                         # Becker–Kaye, transitional
        else:
            gap_Nu = 0.409 * gap_Ta ** 0.241                         # Becker–Kaye, turbulent
        gap_k_eff = max(gap_Nu * _k_air, _k_air)                     # ≥ still-air conduction

    L = float(g.get("motor_length", 30.0)) * 1e-3
    num_slots = int(g.get("num_slots") or round(float(g.get("num_seg", 1)) * float(g.get("num_slots_per_segment", 6))))
    V_cu = (num_slots * float(g.get("num_wires_per_slot", 12))
            * float(g.get("wire_width", 5.0)) * 1e-3
            * float(g.get("wire_height", 0.8)) * 1e-3 * L)
    q_cu = (Pcu / V_cu) if V_cu > 1e-12 else 0.0

    # 3. materials → conductivities
    mats = cfg.get("materials", {})
    k_steel = _thermal_k("steel", mats.get("stator_core"), 25.0)
    k_mag = _thermal_k("magnet", mats.get("magnet"), 8.0)
    k_shaft = _thermal_k_any(mats.get("shaft"), 150.0)

    # Slot insulation as a SERIES thermal resistance on the copper→iron path.
    # The liner (insulation_thickness, Nomex/ceramic) and wire enamel are sub-mesh
    # thin (0.05–0.15 mm < 0.3 mm mesh), so we LUMP them into the coil-region
    # effective conductivity rather than meshing thin strips:
    #     k_eff = h_slot / (h_slot/k_winding + t_liner/k_liner)   (series, ≤ k_winding)
    # k_winding = the slot_k param (Cu + enamel + air, transverse).  Effect:
    # Nomex (k≈0.14) → strong barrier → HOTTER windings;  AlN ceramic (k≈170) →
    # negligible barrier → k_eff≈k_winding (cooler).  The real liner trade-off.
    k_liner = _thermal_k("insulator", mats.get("slot_insulation"), 0.14)
    t_liner = float(g.get("insulation_thickness", 0.2))    # mm  (liner thickness)
    h_slot  = float(g.get("slot_height", 14.0))            # mm  (winding radial extent)
    # Winding bulk transverse k FROM THE ACTUAL WIRE STACK — a volume-weighted
    # SERIES ("layered") mean, not a hardcoded guess and not a copper-inclusion
    # (Maxwell) estimate, which the high copper fraction inflates to ~0.4.  Heat
    # leaving the slot crosses, IN SERIES, the stacked conductors (k≈400 → negligible
    # R) and the inter-wire gaps; those gaps are air-dominated (thin enamel build /
    # imperfect impregnation), and air (k≈0.026) sets the resistance.  For this
    # 8-wire winding the series mean is ≈0.18 W/m·K — the realistic transverse value.
    # A high constant slot_k thermally SHORTS the windings to the iron → no hotspot
    # (the old bug).  Pass slot_k>0 to override with a manual value.
    k_cu_w   = _thermal_k_any(mats.get("winding") or mats.get("conductor"), 400.0)
    k_enamel = _thermal_k("insulator", mats.get("wire_insulation"), 0.12)
    k_gap    = 0.026                                       # still air in the inter-wire gaps
    _sw = float(g.get("slot_width", 3.0)); _nw = float(g.get("num_wires_per_slot", 8))
    _ww = float(g.get("wire_width", 2.5)); _wh = float(g.get("wire_height", 0.5))
    _sy = float(g.get("wire_spacing_y", 0.1))              # mm, inter-wire gap (air-filled)
    f_cu = min(max((_nw * _ww * _wh) / max(_sw * h_slot, 1e-6), 0.0), 0.92)   # copper fill (reported)
    _d_cu  = _nw * _wh                                      # total copper thickness across the stack
    _d_gap = max(_nw - 1.0, 0.0) * _sy                      # total inter-wire gap thickness
    _R_ser = _d_cu / max(k_cu_w, 1e-6) + _d_gap / max(k_gap, 1e-6)   # series resistance (copper + gaps)
    slot_k_auto = (_d_cu + _d_gap) / max(_R_ser, 1e-9)      # winding bulk transverse k (≈0.18)
    slot_k_used = float(slot_k) if float(slot_k) > 0.0 else slot_k_auto   # >0 = manual override
    slot_k_eff = h_slot / (
        h_slot / max(slot_k_used, 1e-6) + t_liner / max(float(k_liner), 1e-6))   # + liner in series

    # 4. per-element k + q from the collapsed domain tags
    (DOM_AIR, DOM_STATOR, DOM_COIL, DOM_AIRGAP, DOM_MAG_N, DOM_ROTOR,
     DOM_SHAFT, DOM_BAND, DOM_OUTER, DOM_MAG_S) = 0, 1, 2, 3, 4, 5, 6, 7, 8, 44
    k_elem = _np.full(tris.shape[0], gap_k_eff)         # default = air / gap (Taylor-enhanced)
    q_elem = loss_dens.copy()
    is_steel = (tags == DOM_STATOR) | (tags == DOM_ROTOR)
    is_mag = (tags == DOM_MAG_N) | (tags == DOM_MAG_S)
    is_coil = (tags == DOM_COIL)
    k_elem[is_steel] = k_steel
    k_elem[is_mag] = k_mag
    k_elem[tags == DOM_SHAFT] = k_shaft
    k_elem[is_coil] = slot_k_eff                        # winding + slot-liner series resistance
    q_elem[is_coil] = q_cu                              # copper loss density (overwrites eddy part)

    # 5. cooling system → housing Robin BC (h, sink temp).  The whole outer stator
    # surface is cooled.  Air: h from air speed (cylinder cross-flow); liquid: outlet
    # temp from the energy balance + mean-coolant sink.  manual: caller's h + ambient.
    P_loss_total = float(em.get("P_loss_total_W") or 0.0)
    h_eff, t_sink, cooling = _cooling_bc(
        mode=cooling_mode, t_ambient_c=ambient_temp, air_speed_mps=air_speed_mps,
        fluid=fluid, fluid_temp_in_c=fluid_temp_in_c, fluid_temp_out_c=fluid_temp_out_c,
        flow_lpm=flow_lpm,
        p_loss_w=P_loss_total, r_housing_m=R_house, length_m=L, h_manual=h_conv)

    # 6. steady thermal solve — drop ALL air (outer + gap + slip band); the rotor
    # is reconnected to the stator by an explicit gap conductance bridge.
    from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal
    th = solve_steady_thermal(
        verts.T, tris.T, tags, k_elem, q_elem,
        drop_tags=[DOM_OUTER, DOM_AIRGAP, DOM_BAND, DOM_AIR],
        r_housing_m=R_house, rotor_outer_m=rotor_outer_m, stator_inner_m=stator_inner_m,
        gap_k=float(gap_k_eff), h_conv=float(h_eff), t_ambient=float(t_sink))

    # 6. per-component temperatures
    Tn = _np.asarray(th["T_node"]); ts = _np.asarray(th["triangles"], int)
    tg = _np.asarray(th["cell_tags"], int)

    def _comp(mask):
        if not mask.any():
            return None
        nodes = _np.unique(ts[mask])
        return {"max": round(float(Tn[nodes].max()), 1), "avg": round(float(Tn[nodes].mean()), 1)}

    result = {
        "ok": True,
        "n_vertices": len(th["vertices"]), "n_triangles": len(th["triangles"]),
        "vertices": th["vertices"], "triangles": th["triangles"],
        "domain_per_tri": th["cell_tags"],
        "temperature_per_node": th["T_node"],            # °C
        "heat_flux_per_tri": th["flux_elem"],            # W/m² (vector)
        "flux_mag_per_tri": th["flux_mag_elem"],
        "T_min": round(float(th["T_min"]), 1), "T_max": round(float(th["T_max"]), 1),
        "n_bridge_links": th.get("n_bridge_links"), "n_nonfinite": th.get("n_nonfinite"),
        "n_housing_facets": th.get("n_housing_facets"),
        "ambient_temp": float(ambient_temp), "h_conv": round(float(h_eff), 1),
        "t_sink_c": round(float(t_sink), 1), "cooling": cooling,
        "slot_k": round(float(slot_k_used), 3),            # winding bulk transverse k (auto unless slot_k>0 override)
        "slot_k_auto": round(float(slot_k_auto), 3),       # series-stack value (Cu + air gaps)
        "slot_fill": round(float(f_cu), 3),                # copper fill fraction in the slot
        "k_enamel": round(float(k_enamel), 3),             # wire-insulation (enamel) conductivity
        "gap_k": round(float(gap_k_eff), 3),               # air-gap k used (Taylor-enhanced unless gap_k>0 override)
        "gap_k_taylor": round(float(gap_k_eff), 3),        # rotation-enhanced gap conductivity
        "gap_Ta": (round(float(gap_Ta), 0) if gap_Ta is not None else None),  # gap Taylor number
        "gap_Nu": round(float(gap_Nu), 2),                 # Nusselt enhancement (k_eff/k_air)
        "rpm": float(rpm),
        "slot_k_eff": round(float(slot_k_eff), 3),         # winding + liner series k
        "k_liner": round(float(k_liner), 3),               # slot-liner material conductivity
        "liner_material": mats.get("slot_insulation"),
        "k_steel": round(k_steel, 1), "k_magnet": round(k_mag, 1), "k_shaft": round(k_shaft, 1),
        "components": {
            "winding": _comp(tg == DOM_COIL),
            "magnet": _comp((tg == DOM_MAG_N) | (tg == DOM_MAG_S)),
            "stator": _comp(tg == DOM_STATOR),
            "rotor": _comp(tg == DOM_ROTOR),
        },
        "P_cu_W": round(Pcu, 1), "P_fe_W": em.get("P_fe_W"),
        "P_mag_eddy_W": em.get("P_mag_eddy_W"), "P_loss_total_W": em.get("P_loss_total_W"),
        "outlines": em.get("outlines"), "extent": em.get("extent"),
    }
    _thermal_field_cache[key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 7d. FEM Transient — N steps per electrical period
# ─────────────────────────────────────────────────────────────────────────────

_fem_transient_cache: Dict[tuple, Dict] = {}
# The single most-recent transient (key + result), kept so the web UI can RESTORE
# the last simulation on open (?restore=true) — showing it stale-flagged instead
# of recomputing when the requested params don't match.  Updated on every save
# and repopulated from disk at startup.
_last_transient_ref: Dict = {"key": None, "result": None}

# ── Persist the last sliding-band transient to disk ──────────────────────────
# So a re-run with the SAME params after a back-end restart is instant instead
# of recomputing the whole FEM solve.  We keep ONE entry (the latest), keyed by
# its param tuple, written next to the config and reloaded into the cache on
# import.  (The web UI also caches the last run in localStorage for display;
# this covers the "same params, fresh process" path.)
import json as _json
import os as _os_t

def _transient_store_path() -> str:
    try:
        from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _cp
        _base = _os_t.path.dirname(str(_cp))
    except Exception:
        _base = _os_t.path.join(_os_t.path.dirname(__file__), "..", "..", "..", "config")
    return _os_t.path.abspath(_os_t.path.join(_base, ".last_transient.json"))

def _json_default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    return float(o)

def _save_last_transient(sb_key: tuple, result: Dict) -> None:
    try:
        tmp = _transient_store_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump({"key": list(sb_key), "result": result}, fh,
                       default=_json_default)
        _os_t.replace(tmp, _transient_store_path())   # atomic
        _last_transient_ref["key"] = tuple(sb_key)
        _last_transient_ref["result"] = result
    except Exception as _e:
        log.warning("could not persist last transient: %s", _e)

def _load_last_transient_into_cache() -> None:
    try:
        p = _transient_store_path()
        if not _os_t.path.exists(p):
            return
        with open(p, encoding="utf-8") as fh:
            blob = _json.load(fh)
        def _retuple(x):    # JSON turns tuples into lists — restore for hashing
            return tuple(_retuple(i) for i in x) if isinstance(x, list) else x
        _key = _retuple(blob["key"])
        _fem_transient_cache[_key] = blob["result"]
        _last_transient_ref["key"] = _key
        _last_transient_ref["result"] = blob["result"]
        log.info("restored last transient from %s", p)
    except Exception as _e:
        log.warning("could not restore last transient: %s", _e)


_load_last_transient_into_cache()   # repopulate the cache at import (startup)


# Serializes transient computes so concurrent identical requests (the
# animation viewer + transient charts both fire on Simulation-tab mount)
# don't each spawn a worker pool and storm the CPU.
import threading as _threading
_fem_transient_lock = _threading.Lock()


def clear_simulation_caches() -> None:
    """Drop every cached 2-D polygon / mesh / field / transient result.

    Called whenever the motor GEOMETRY changes (PUT /api/geometry) so the
    Mesh tab and the Simulation field/transient re-derive everything from
    the new cross-section instead of serving stale, old-geometry results.

    This is a belt, not the braces, and the docstring used to say otherwise
    ("keys on these caches intentionally omit the geometry parameters … so they
    must be flushed explicitly").  They do not omit it any more: the field and
    transient keys both carry `_config_physics_fingerprint`, which hashes the
    config geometry AND the live geometry object, and the field key appends any
    per-request `geo=` override.  So a geometry change invalidates them even
    when it arrives by a route that never calls this function — an optimizer
    subprocess writing the YAML, a text editor, `git checkout`.  Verified on the
    live stack: after a PUT the no-`geo=` field view returns a different outline
    and element count.  Flushing here is still worth doing (it frees the memory
    and makes the first request after a save cheap to reason about).
    """
    for _c in (_motor_geom_cache, _fem_mesh_cache, _fem_mesh_sb_cache,
               _fem_field_cache, _fem_transient_cache, _transient_field_snap):
        try:
            _c.clear()
        except Exception:
            pass

# Cooperative cancel keyed by run-id.  The "Stop" button POSTs the id of
# the run it wants stopped; the parallel solve loop (and any duplicate
# request waiting on the lock) checks whether ITS run-id was cancelled and
# bails.  Keying by id means cancelling one run never aborts the next one.
_fem_transient_cancelled_run: Dict[str, Optional[str]] = {"id": None}

# Shared progress state for the currently-running transient.  Polled by
# the frontend via /physics/fem_transient/progress so the user sees
# "Frame X / N — Ys elapsed — ETA Zs" instead of a spinning "Running…".
_fem_transient_progress: Dict[str, Dict] = {
    "current": {
        "running":   False,
        "step":      0,
        "total":     0,
        "elapsed_s": 0.0,
        "eta_s":     0.0,
        "ts_start":  0.0,
        "phase":     "idle",
    }
}


@router.get("/physics/fem_transient/progress")
async def get_fem_transient_progress():
    """Lightweight progress endpoint — frontend polls this every ~500 ms
    while a transient solve is in flight.  Returns step counter, elapsed
    wall-time and an ETA estimated from the average seconds-per-step so
    far."""
    p = _fem_transient_progress.get("current", {})
    if p.get("running") and p.get("ts_start", 0) > 0:
        import time as _t
        elapsed  = _t.time() - p["ts_start"]
        raw_step = int(p.get("step", 0))
        total    = max(1, int(p.get("total", 0)))
        # Before the first frame completes (step 0 — the one-time mesh build
        # + first solve) there is NO per-frame timing sample yet, so any ETA
        # is a wild single-sample extrapolation that climbs with elapsed.
        # Report ETA-unknown (0) instead of a misleading runaway estimate.
        if raw_step <= 0:
            return {
                **p,
                "elapsed_s":  round(elapsed, 1),
                "eta_s":      0.0,
                "per_step_s": 0.0,
                "frac":       0.0,
            }
        per_step = elapsed / raw_step
        eta = per_step * max(0, total - raw_step)
        return {
            **p,
            "elapsed_s": round(elapsed, 1),
            "eta_s":     round(eta, 1),
            "per_step_s": round(per_step, 2),
            "frac":      round(raw_step / total, 3),
        }
    return p


@router.post("/physics/fem_transient/cancel")
async def cancel_fem_transient(run_id: str = ""):
    """Request the transient with this run_id to stop.  The solve loop
    checks after each completed frame, tears the worker pool down
    (cancelling pending frames) and raises 499.  A duplicate request for
    the same run_id waiting on the lock bails immediately."""
    # Cancel a SPECIFIC run only.  A new Run uses a fresh run_id (the
    # incrementing runNonce) so it never matches a previously-cancelled
    # id — no risk of a stale cancel killing the next solve.
    if run_id:
        _fem_transient_cancelled_run["id"] = run_id
    return {"cancelled": bool(run_id), "run_id": run_id}


@router.get("/physics/fem_transient")
def get_fem_transient(
    n_steps_per_period:  int   = 60,   # FEM solves per electrical period
    n_periods:           float = 1.0,  # how many electrical periods to sim
    gamma_deg:           float = 0.0,
    I_phase_rms:         float = 85.0,
    rpm:       Optional[float] = None,    # ← MECHANICAL SPEED [rpm].  Omitted (the UI's
                                          #   case) = the shared config's simulation.rpm,
                                          #   so nothing that never sent it changes.  Sent
                                          #   explicitly (a preset/catalog/candidate eval),
                                          #   the solve's f_elec, iron loss, magnet/shaft
                                          #   eddy and back-EMF all follow THIS number
                                          #   instead of whatever the shared config holds
                                          #   (docs/SOLVER_TRIALS_2026-07-30.md F2).
    n_parallel:  Optional[int] = None,    # ← WINDING PARALLEL PATHS.  Omitted = the shared
                                          #   config's winding.n_parallel.  The FEM only ever
                                          #   sees I_coil = I_phase / n_parallel, so a stored
                                          #   machine evaluated without its own value is driven
                                          #   at n_parallel x its intended coil MMF (F3).
    daxis_deg:   Optional[float] = None,  # ← D-AXIS REFERENCE, GIVEN.  Omitted = the
                                          #   Simulation tab's simulation.daxis_deg if it
                                          #   holds one, else MEASURED (a 24-frame no-load
                                          #   solve, ~39 s, cached per geometry).  Given, it
                                          #   is used as is and nothing is solved for it.
    connection:  Optional[str] = None,    # ← WINDING CONNECTION label ("4S" / "2S-2P" / "4P").
                                          #   Supplies n_parallel when that is absent and enters
                                          #   the d-axis topology key.  Unreadable label -> 400.
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    outer_air_factor:    float = 1.3,
    motion_band:         bool  = True,
    band_thickness_mm:   float = 0.4,
    gap_layers:          float = 3.0,     # ← element layers across the air gap (Mesh slider)
    n_sectors:           int   = 4,
    stator_fillet_mm:    float = 0.0,
    include_frames:      bool  = False,   # ← if true, accumulate per-step field
    n_frames:            int   = 12,      # ← #frames sampled for the animation
    run_id:              str   = "",      # ← Stop-button cancellation token
    fresh:               bool  = False,   # ← "Start fresh" recomputes instead of serving the cache
    sliding_band:        bool  = True,    # ← accepted for URL compat and IGNORED: there is
                                          #   only the mesh-once sliding band now.  The
                                          #   remesh-per-frame alternative it used to select
                                          #   solved each frame on the legacy static P1 solver
                                          #   at a hard-coded 108° d-axis, so a caller that
                                          #   omitted this flag silently got a different
                                          #   operating point than one that sent it.
    coil_temp_c:         float = 120.0,   # ← copper temperature → ρ_Cu(T)
    end_winding_factor:  float = 0.0,     # ← 0 = auto-estimate from geometry
    component_mesh:      str   = "",      # ← JSON {comp: size_mm} per-part mesh size
    eddy:                bool  = False,   # ← coupled σ·∂A/∂t eddy-current solve (P2, 11e1469):
                                          #   the currents in copper/magnets/shaft become part of
                                          #   the Newton system instead of a post-process, so the
                                          #   run reports the SOLVED copper loss and its field
                                          #   snapshot carries the real eddy J⟳.  Costs solve time;
                                          #   OFF unless the caller asks (the Simulation tab has a
                                          #   checkbox — never enabled behind the user's back).
    field_snapshot:      bool  = False,   # ← keep the LAST frame's field (mesh+A+B+tags+Jeddy+
                                          #   loss_dens) for the field views.  NOT an extra solve:
                                          #   it is the frame just solved, kept instead of dropped.
                                          #   It is stripped from the HTTP payload (megabytes) and
                                          #   parked server-side in _transient_field_snap.
                                          #   OPT-IN (the Simulation tab asks; a sweep / optimizer
                                          #   point does not) — it does add the cycle-averaged
                                          #   loss-density map to the post-processing, and a batch
                                          #   of candidate evals would only evict each other's
                                          #   snapshots without anyone ever looking at them.
    rotor_eddy:          bool  = True,    # ← field-based magnet/shaft eddy losses
    demag:               bool  = False,   # ← per-element irreversible demagnetisation (de-rates Br → torque)
    torque_filter:       bool  = False,   # ← band-limit T(t) to physical 6·k orders (off = raw; honest default)
    pole_copy:           bool  = False,   # ← bit-identical pole/slot template-copy mesh
    iron_template:       bool  = True,    # ← deterministic template iron (fallback: gmsh)
    geo_mesh:            bool  = True,    # ← geometry-driven CDT mesh (real fillets, cell-tiled iron;
                                          #   full ring + sectors) — matches the Mesh-tab default, so
                                          #   callers that omit it get the SAME build as Simulation
    hi_fidelity:         bool  = False,   # ← 2× slip nodes + finer mesh → smoother raw torque (slower)
    structured_gap:      bool  = False,   # ← ANSYS-style concentric-ring air-gap mesh (experimental)
    airgap_macro:        bool  = False,   # ← harmonic air-gap macroelement (honest RAW ripple; full ring + sectors)
    element_order:       int   = 2,       # ← 2 = P2 quadratic, the ONLY basis.  B is linear per
                                          #   element → smooth Arkkio torque, an energy-consistent
                                          #   mean AND a mesh-convergent ripple (noise floor →0 with
                                          #   mesh refinement).  Requires the structured belt, forced
                                          #   on below.  Irreversible demag, the coupled σ∂A/∂t eddy
                                          #   solve, the voltage drive and eddy+voltage TOGETHER all
                                          #   run on it (a1aedad / 11e1469 / 4e316b9); the only thing
                                          #   still raising NotImplementedError is the moving /
                                          #   harmonic-macro air-gap band.  Anything but 2 raises.
    restore:             bool  = False,   # ← on open: return the LAST saved transient (stale if params differ) instead of recomputing
    geo:                 Optional[str] = None,  # ← per-request geometry override (multi-user); absent = global config
    drive:               str   = "current",  # ← "current" (imposed sinusoidal I) | "voltage"
                                             #   (imposed sinusoidal V — the currents are the
                                             #   machine's own response, incl. back-EMF-harmonic
                                             #   parasitics; the FOC-drive verification mode)
    v_phase_peak:        float = 0.0,     # ← voltage drive: phase-voltage amplitude [V, peak]
    v_delta_deg:         float = 0.0,     # ← voltage drive: voltage angle [°el] in the γ frame
    harm_ref:            bool  = True,    # ← voltage drive: ALSO run a current-drive reference at
                                          #   the extracted fundamental (I₁, γ₁) → ΔP_harm = the
                                          #   watt cost of the parasitic harmonic currents
):
    """Transient FEM analysis — runs N solves per electrical period and
    returns time-resolved T(t), losses(t) and V_phase(t).

    Phase voltage is computed as V = R·I + dψ/dt, where ψ is the flux
    linkage through each phase's coils (numerical integration of A_z
    over the coil triangles, weighted by winding direction).  The
    derivative dψ/dt uses central finite differences in time.

    When include_frames=True, additionally returns ``frames`` — a list of
    n_frames complete FEM payloads (mesh + A_z + |B| + demag at each rotor
    position).  Used by the field-animation viewer in the web UI to scrub
    through one electrical period.
    """
    import numpy as _np

    # Winding connection: validate BEFORE anything else touches it.  An
    # unreadable label is a request error (400 naming the label and the forms
    # that parse), never a silent fall-back to one parallel path — that
    # fall-back is a factor-n_parallel error in the coil MMF (F3).
    if connection is not None:
        from motor_ai_sim.winding import parse_connection as _pc_route
        try:
            _pc_route(connection)
        except ValueError as _ce:
            raise HTTPException(status_code=400, detail=str(_ce))
    if n_parallel is not None and int(n_parallel) < 1:
        raise HTTPException(status_code=400,
                            detail=f"n_parallel must be >= 1; got {n_parallel!r}")

    # Mesh once, rotate the rotor by re-pairing the slip ring → smooth T(t),
    # clean V(t), and one mesh for every frame including the animation's.
    _comp_mesh = _parse_component_mesh(component_mesh)
    _geo_ov = _parse_geo_override(geo)   # per-request geometry override (multi-user)
    # P2 needs the merged structured belt.  rotor_eddy (post-processed
    # magnet/shaft/iron losses) IS supported — keep it.  Coerce only the
    # still-unsupported options (harmonic macro) so the mode is self-consistent.
    #
    # HISTORY: this block used to coerce demag=False and drive="current"
    # too.  Both became stale silently — P2 demag landed in a1aedad and the
    # P2 voltage drive in 4e316b9 — and the leftover coercion turned the
    # user's Demagnetisation checkbox into a no-op on P2: the run came back
    # at no-demag speed with a uniform (single-colour) Br map and nothing
    # on screen saying why.  A route-side coercion is a silent substitution;
    # if an option is genuinely unsupported the solver must raise, not have
    # the route quietly answer a different question.
    if int(element_order) == 2:
        structured_gap = True
        airgap_macro = False
        # SPEED: P2 is a high-fidelity RIPPLE mode, and its anti-periodic
        # sector solve gives the SAME torque/ripple/losses as the full motor
        # (validated to 0.3%) at ~S× lower cost (S× fewer DOFs → the direct
        # factorization, which dominates P2 wall-time, shrinks steeply).  So
        # if the caller left n_sectors at the full motor (≤1), auto-use the
        # machine's natural rotational symmetry gcd(slots, poles) = num_seg.
        if int(n_sectors) <= 1:
            try:
                from math import gcd as _gcd
                from motor_ai_sim.config import get_config as _gc2
                _g = dict((_gc2().get("geometry", {})) or {})
                if _geo_ov:
                    _g = {**_g, **_geo_ov}
                _sym = int(_g.get("num_seg")
                           or _gcd(int(_g.get("num_slots", 1)),
                                   int(_g.get("num_poles", 1))) or 1)
                if _sym >= 2:
                    n_sectors = _sym
            except Exception:
                pass
    # Config fingerprint of the base physics a solve depends on but that is
    # NOT in the request signature (num_wires_per_slot, the whole geometry,
    # winding connection, materials/magnet/steel).  WITHOUT this, editing a
    # geometry field (e.g. wires 9→7) and re-running returned the STALE
    # cached result — the reported "it didn't change immediately" bug — because
    # the operating-point key was unchanged.  A per-request geo override is
    # still appended below (it changes the effective geometry on top of this).
    # A signed-in user's material choice lives ONLY in this request's `mat=`
    # override (per-user, never written to the shared config).  Leaving it out
    # of the CACHE key meant "assign a different magnet, press Run" replayed the
    # previous material's cached solve — same torque, same losses, no hint
    # anything was stale.
    _cfp = _config_physics_fingerprint(with_request_materials=True)
    _sb_key = ("sb", int(n_steps_per_period), round(n_periods, 2),
               round(gamma_deg, 1), round(I_phase_rms, 1),
               round(mesh_size_mm, 2), round(min_size_mm, 2),
               round(outer_air_factor, 2), int(n_sectors),
               round(stator_fillet_mm, 2),
               round(coil_temp_c, 1), round(end_winding_factor, 3),
               int(bool(rotor_eddy)), round(gap_layers, 1),
               int(bool(demag)), int(bool(torque_filter)),
               int(bool(pole_copy)), int(bool(iron_template)), int(bool(hi_fidelity)), int(bool(structured_gap)),
               int(bool(airgap_macro)), int(bool(geo_mesh)),
               tuple(sorted(_comp_mesh.items())),
               str(drive or "current"), round(float(v_phase_peak), 2),
               round(float(v_delta_deg), 1), int(bool(harm_ref)),
               int(element_order), _cfp,
               # Speed: resolved (explicit argument or the config's), so an
               # rpm change invalidates the entry — the config's speed used to
               # be in NO key at all.
               round(_effective_rpm(rpm), 3),
               _effective_winding(n_parallel, connection),
               # The d-axis reference: a run pinned to a given angle and a run
               # that measured its own are different operating points whenever
               # the two numbers differ, so they may not share a cache entry.
               _effective_daxis(daxis_deg),
               int(n_frames) if include_frames else 0,
               # The coupled eddy solve is DIFFERENT physics (solved copper loss,
               # reaction currents in the magnets/shaft) — it must not share a
               # cache entry with the magnetostatic run.  field_snapshot is NOT
               # in the key: it changes nothing about the numbers, only whether
               # the last frame's field is kept for the viewer.
               int(bool(eddy)))
    if _geo_ov:   # distinct cache entry per overridden geometry (no-geo key unchanged)
        _sb_key = _sb_key + (tuple(sorted(_geo_ov.items())),)
    # Key of the field snapshot this run's last frame belongs under — built ONCE
    # here and used both to store it after the solve and to tell a cache hit
    # whether that snapshot is still in memory.
    _fsnap_fields = _field_snap_key_fields(
        gamma_deg=gamma_deg, I_phase_rms=I_phase_rms, rpm=rpm,
        n_parallel=n_parallel, connection=connection,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, n_sectors=n_sectors,
        stator_fillet_mm=stator_fillet_mm, gap_layers=gap_layers,
        coil_temp_c=coil_temp_c, comp_mesh=_comp_mesh,
        pole_copy=pole_copy, iron_template=iron_template, geo_mesh=geo_mesh,
        structured_gap=structured_gap, airgap_macro=airgap_macro,
        n_steps_per_period=n_steps_per_period, n_periods=n_periods,
        eddy=eddy, rotor_eddy=rotor_eddy, demag=demag,
        drive=str(drive or "current"), element_order=element_order,
        # The snapshot key must be spelling-independent: this route is reached
        # through POST /api/kernel/run (no `mat=`, no `geo=` — the interceptor
        # does not match that URL) while the field view that looks the snapshot
        # up goes through /api/simulation/physics WITH both.  Same machine, two
        # spellings; the fingerprint therefore carries the shared config only and
        # the two overrides are normalised against it (`_geo_ov_for_key`,
        # `_mat_ov_for_key`) instead of being hashed verbatim.
        cfg_fingerprint=_config_physics_fingerprint(
            with_request_materials=False),
        geo_ov=_geo_ov,
        mat_ov=_get_request_materials_safe())
    _fsnap_key = tuple(_fsnap_fields.values())

    def _with_live_snapshot_flag(res: Dict) -> Dict:
        """A cached result was solved in some earlier request — possibly in an
        earlier PROCESS (the last transient is restored from disk at startup).
        Its stored field_snapshot flag says what that run did, not what is in
        memory now, and the J⟳ / Loss views act on this flag.  Report the LIVE
        answer instead of a remembered one."""
        _have = _fsnap_key in _transient_field_snap
        if res.get("field_snapshot") == _have:
            return res
        out = dict(res)
        out["field_snapshot"] = _have
        out["field_snapshot_eddy"] = bool(eddy) and _have
        return out

    if not fresh and _sb_key in _fem_transient_cache:
        return _with_live_snapshot_flag(_fem_transient_cache[_sb_key])
    # RESTORE path (page open / tab switch): NEVER recompute.  Hand back the
    # last saved transient — flagged stale if its params differ from those
    # requested — or signal that nothing has ever been computed so the UI can
    # show "press Run" instead of spinning up a solve.
    if restore:
        _ref_res = _last_transient_ref.get("result")
        if _ref_res is not None:
            _out = dict(_ref_res)
            _out["restored"] = True
            _out["stale"] = (_last_transient_ref.get("key") != _sb_key)
            # WHY it is stale, not just THAT it is.  The restored run carries the
            # fingerprint of the machine it was solved on; compare it with the
            # machine loaded right now.  A geometry mismatch is the dangerous
            # case (the numbers describe a motor that is no longer on screen) and
            # the UI escalates it to a red banner — an operating-point tweak only
            # earns the amber "press Run" hint.  A run saved before this stamp
            # existed reports `None`: unknown, never a silent "fine".
            _live_geo_fp = _geometry_fingerprint(_geo_ov)
            _saved_geo_fp = _out.get("geo_fingerprint")
            _out["geo_fingerprint_live"] = _live_geo_fp
            _out["stale_geometry"] = (
                None if not _saved_geo_fp else bool(_saved_geo_fp != _live_geo_fp))
            _out["stale_reason"] = (
                "geometry" if _out["stale_geometry"]
                else ("inputs" if _out["stale"] else None))
            if _out["stale_geometry"]:
                log.warning(
                    "restore: the saved transient was solved on a DIFFERENT "
                    "machine (geo %s -> %s) — returning it flagged stale, not "
                    "as the current result", _saved_geo_fp, _live_geo_fp)
            # The persisted result remembers that ITS run kept a field snapshot;
            # that snapshot lives in memory only, and a stale restore is not even
            # the same run.  Report what is actually available now.
            _out["field_snapshot"] = bool(
                not _out["stale"] and _fsnap_key in _transient_field_snap)
            _out["field_snapshot_eddy"] = bool(_out["field_snapshot"] and eddy)
            return _out
        return {"restored": False, "stale": False}

    # ── GEOMETRY GATE — refuse to solve a cross-section that cannot exist ────
    # geometry_constraints.clamp guards ONE scalar knob at a time; it cannot see
    # where the finished regions land.  Here the real 2-D polygons (the same
    # ones build_mesh_from_polygons consumes) are checked for overlapping
    # domains, parts escaping their host and collapsed regions.  If any of that
    # is true, the mesher will still mesh it and the FEM will still return
    # torque, losses and efficiency — for a machine nobody could build.  That is
    # the silent-wrong-answer class this gate exists to close.
    #
    # Placed AFTER the cache / restore returns so a warm hit and a page-open
    # restore cost nothing, and BEFORE the lock so nothing is serialised behind
    # a solve that is not going to happen.  It lives route-side on purpose:
    # fem_solver_2d stays untouched, and every caller that reaches a solve does
    # so through this function (the Simulation tab, the kernel module
    # solver.em_transient, the optimizer's refine_proc, torque_sweep, passport).
    #
    # WARNINGS DO NOT BLOCK — only errors do.  The Mesh and Geometry previews
    # deliberately still render an invalid machine, because that picture is how
    # the user fixes it.
    try:
        from motor_ai_sim.geometry_validation import validate_geometry as _vgeo
        from motor_ai_sim.config import get_config as _gc_gate
        _gate_geo = dict((_gc_gate().get("geometry", {})) or {})
        if _geo_ov:
            _gate_geo = {**_gate_geo, **_geo_ov}
        _gate = _vgeo(_gate_geo)
    except HTTPException:
        raise
    except Exception as _ge:
        # The validator itself failing must not take the solver down with it.
        log.warning("geometry validation unavailable — solving unguarded: %s", _ge)
        _gate = None
    if _gate is not None and not _gate.ok:
        log.error("refusing to solve an invalid cross-section:\n%s", _gate.summary())
        raise HTTPException(status_code=422, detail={
            "error": _gate.summary(),
            "geometry_validation": _gate.to_dict(),
        })

    # Serialise concurrent identical solves.  A duplicate request (shares the
    # run_id) or a React dev double-invoke would otherwise run a SECOND full
    # sliding-band solve in parallel AND clobber the shared progress global
    # (the "Solving frame 0 / N" flash mid-run).  Acquire BEFORE touching
    # progress; a twin waiting on the lock wakes straight into the
    # freshly-populated cache instead of re-solving.
    _fem_transient_lock.acquire()
    try:
        # Re-check under the lock: a twin that finished while we waited has
        # already populated the cache → return its result, don't re-solve.
        if not fresh and _sb_key in _fem_transient_cache:
            return _with_live_snapshot_flag(_fem_transient_cache[_sb_key])
        # Sliding band for EVERY symmetry (mesh once, slide the rotor).
        # Full (n_sectors=1) has no clean 360° slip mesh here, so it computes
        # the symmetry-EXACT sector — which equals the full motor exactly
        # (×N), verified within 0.7 % of the literal full disk.  The remesh-
        # per-frame quasi-static alternative that used to sit beside this is
        # gone: ~10× slower AND it solved on the legacy static P1 solver.
        from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
        import time as _t
        # Per-frame progress so the web UI's "Solving frame X of N" + ETA
        # advance during a sliding-band run.  The remesh path used to drive
        # this global; now that the field animation is opt-in, the solve
        # itself must report.  The callback fires at the TOP of each frame
        # with the solver's true (snapped) frame count.
        _est_total = max(1, int(round(float(n_steps_per_period) * float(n_periods))))
        if demag:
            _est_total *= 2     # demag adds a pre-pass sweep over the period
        _fem_transient_progress["current"] = {
            "running": True, "step": 0, "total": _est_total,
            "elapsed_s": 0.0, "eta_s": 0.0, "ts_start": _t.time(),
            "phase": ("fem-solve (sliding-band, demag)" if demag
                      else "fem-solve (sliding-band)"),
        }
        def _sb_progress(_done, _total, _phase=None):
            _cur = _fem_transient_progress["current"]
            _cur["step"] = int(_done)
            _cur["total"] = int(_total)
            # The solver names the stage it is in (the eddy warm-up counts its
            # own frames against its own, moving, total).  None = back to the
            # reported window, so the label returns to the solve's own.
            if _phase:
                _cur["phase"] = str(_phase)
            elif _cur.get("phase", "").startswith("eddy warm-up"):
                _cur["phase"] = ("fem-solve (sliding-band, coupled eddy)"
                                 if eddy else "fem-solve (sliding-band)")
        try:
            # ONE canonical solve — shared with the optimizer (refine_proc) and
            # the solver.em_transient module via em_transient_eval, so they can
            # never diverge.  'Full' (n_sectors<=1) solves the full ring (matches
            # the Mesh tab); 1/4 1/2 solve the sector (1/4 is the UI default).
            _sbres = em_transient_eval(
                n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
                gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
                rpm=(None if rpm is None else float(rpm)),
                n_parallel=(None if n_parallel is None else int(n_parallel)),
                connection=(None if connection is None else str(connection)),
                daxis_deg=_effective_daxis(daxis_deg),
                mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
                outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
                n_sectors=int(n_sectors), stator_fillet_mm=float(stator_fillet_mm),
                coil_temp_c=float(coil_temp_c), end_winding_factor=float(end_winding_factor),
                rotor_eddy=bool(rotor_eddy), demag=bool(demag),
                torque_filter=bool(torque_filter), pole_copy=bool(pole_copy),
                iron_template=bool(iron_template), geo_mesh=bool(geo_mesh),
                component_mesh_mm=_comp_mesh, geo_override=_geo_ov,
                progress_cb=_sb_progress, hi_fidelity=bool(hi_fidelity),
                structured_gap=bool(structured_gap),
                airgap_macro=bool(airgap_macro),
                drive=str(drive or "current"),
                v_phase_peak=float(v_phase_peak),
                v_delta_deg=float(v_delta_deg),
                element_order=int(element_order),
                return_frames=int(n_frames) if include_frames else 0,
                # Coupled σ·∂A/∂t solve — only when the caller asked for it.
                eddy=bool(eddy),
                # Keep the last frame's field for the J⟳ / Loss views.  Free:
                # the frame is already solved; this stops it being discarded.
                return_field=bool(field_snapshot))
            # ── ΔP_harm (voltage drive): current-drive REFERENCE at the
            # extracted fundamental (I₁, γ₁), so the comparison runs at a
            # MATCHED fundamental current — the loss difference is then
            # purely the parasitic harmonic currents' watt cost.
            if str(drive or "").lower().startswith("v") and harm_ref:
                try:
                    from motor_ai_sim.simulation.postproc import fundamental_current
                    _fc = fundamental_current(_sbres)
                    if _fc["I1_phase_rms_A"] > 1e-3:
                        _fem_transient_progress["current"]["phase"] = \
                            "fem-solve (harm-ref, sinusoidal current)"
                        _ref = em_transient_eval(
                            n_steps_per_period=int(n_steps_per_period),
                            n_periods=float(n_periods),
                            gamma_deg=float(_fc["gamma1_deg"]),
                            I_phase_rms=float(_fc["I1_phase_rms_A"]),
                            # Same machine at the same SPEED — the reference's
                            # only allowed difference from the voltage run is
                            # the drive.
                            rpm=(None if rpm is None else float(rpm)),
                            n_parallel=(None if n_parallel is None
                                        else int(n_parallel)),
                            connection=(None if connection is None
                                        else str(connection)),
                            mesh_size_mm=float(mesh_size_mm),
                            min_size_mm=float(min_size_mm),
                            outer_air_factor=float(outer_air_factor),
                            gap_layers=float(gap_layers),
                            n_sectors=int(n_sectors),
                            stator_fillet_mm=float(stator_fillet_mm),
                            coil_temp_c=float(coil_temp_c),
                            end_winding_factor=float(end_winding_factor),
                            # eddy is unsupported (and force-dropped) in the
                            # voltage solve — the reference must match its
                            # physics or ΔP_harm absorbs the whole P_mag.
                            rotor_eddy=False, demag=bool(demag),
                            torque_filter=bool(torque_filter),
                            pole_copy=bool(pole_copy),
                            iron_template=bool(iron_template),
                            # The reference must be the SAME machine, meshed
                            # and discretised the same way — its only allowed
                            # difference from the voltage run is the drive.
                            # geo_mesh and element_order were both omitted
                            # here, so the reference silently fell back to the
                            # gmsh mesh and to P1: ΔP_harm was then a
                            # cross-mesh, cross-element-order difference with
                            # the harmonic cost buried in it.
                            geo_mesh=bool(geo_mesh),
                            element_order=int(element_order),
                            component_mesh_mm=_comp_mesh, geo_override=_geo_ov,
                            hi_fidelity=bool(hi_fidelity),
                            structured_gap=bool(structured_gap),
                            airgap_macro=bool(airgap_macro))
                        import numpy as _np_hr
                        _pl_v = float(_np_hr.mean(_sbres.get(
                            "P_loss_total_W") or [0.0]))
                        _pl_r = float(_np_hr.mean(_ref.get(
                            "P_loss_total_W") or [0.0]))
                        _sbres["harm_ref"] = {
                            "I1_phase_rms_A": round(_fc["I1_phase_rms_A"], 2),
                            "gamma1_deg": round(_fc["gamma1_deg"], 1),
                            "P_loss_ref_W": round(_pl_r, 1),
                            "P_loss_v_W": round(_pl_v, 1),
                            "T_ref_Nm": round(float(_ref.get("T_avg_Nm", 0.0)), 3),
                        }
                        _sbres["dP_harm_W"] = round(_pl_v - _pl_r, 1)
                except Exception:
                    log.exception("harm_ref reference run failed (non-fatal)")
        finally:
            # NOT done yet — what follows (animation keyframes, CAD masses, the
            # summary block, the disk save) is seconds to tens of seconds on a
            # big machine, and the panel is still waiting on this response the
            # whole time.  Reporting "finished" here made a run that was still
            # working look like a run that had finished and changed nothing:
            # progress at 40/40, the button still on Stop, the old numbers still
            # on the cards.  Stay running, say what is happening; the flag is
            # cleared in the route's own finally, so no error path can hang it.
            _fem_transient_progress["current"]["phase"] = \
                "post-processing (frames, masses, summary)"
        # ── Animation keyframes → the viewer's payload shape ──────────────────
        # The solver hands back ONE mesh topology plus, per keyframe, the
        # rotor-rotated node coordinates and that frame's field.  This replaces
        # the remesh-per-frame path, which built a full gmsh mesh per keyframe
        # (~11 s of ~15 s each) across a 24-process pool AND solved it on the
        # legacy static P1 solver — at the hard-coded 108 deg d-axis, i.e. ~48
        # deg off the q-axis for this 12s14p machine, so the animation showed a
        # DIFFERENT operating point than the charts beside it.
        _fr_raw = _sbres.pop("frames", None) or []
        _fr_mesh = _sbres.pop("frames_mesh", None)
        if include_frames and _fr_raw and _fr_mesh:
            try:
                from motor_ai_sim.simulation.fem_solver_2d import (
                    _simplify_polys as _sp_a, DOM_MAG_BASE as _DMB_a,
                    DOM_COIL_BASE as _DCB_a, DOM_MAG_N as _DMN_a,
                    DOM_MAG_S as _DMS_a, DOM_COIL as _DC_a)
                from motor_ai_sim.cadquery_geometry import CadQueryMotor as _CQM_a
                _mot_a = _CQM_a()
                if _geo_ov:
                    _mot_a.set_parameters(_geo_ov)
                _T_a = _np.asarray(_fr_mesh["T"])
                _tags_a = _np.asarray(_fr_mesh["tags"]).astype(int)
                _tri_a = _T_a.T.tolist()
                # n_sectors already carries the P2 auto-symmetry sector chosen
                # above, so this is the wedge the solve actually ran on.
                _nsec_a = int(n_sectors) if int(n_sectors) > 1 else 1
                _T_ser = _sbres.get("T_em_Nm") or []
                _frames_out = []
                for _fi, _f in enumerate(_fr_raw):
                    _ang_a = float(_f["rotor_angle_deg"])
                    _P_a = _np.asarray(_f["P_mm"]) * 1e-3         # mm → m
                    _A_a = _np.asarray(_f["A"], float)
                    _Bm_a = _np.hypot(_np.asarray(_f["Bx"], float),
                                      _np.asarray(_f["By"], float))
                    # Palette tags are rotor-angle-independent (the domains do
                    # not change), so collapse them once against frame 0's polys.
                    _polys_a = _sp_a(_mot_a.get_2d_polygons(rotor_angle_deg=_ang_a),
                                     tol_mm=0.005)
                    if _fi == 0:
                        _tv_a = _tags_a.copy()
                        _tv_a[_tags_a >= _DCB_a] = _DC_a
                        for _mi, (_mp_a, _pol_a) in enumerate(
                                _polys_a.get("magnets", []) or []):
                            _tv_a[_tags_a == (_DMB_a + _mi)] = (
                                _DMN_a if _pol_a > 0 else _DMS_a)
                        _tv_list = _tv_a.tolist()
                    _fr_out = {
                        "step_idx": int(_f["step_idx"]),
                        "time_s": float(_f["time_s"]),
                        "rotor_angle_deg": _ang_a,
                        "T_em_Nm": float(_T_ser[int(_f["step_idx"])])
                                   if int(_f["step_idx"]) < len(_T_ser) else 0.0,
                        "vertices": _P_a.T.tolist(),
                        "triangles": _tri_a,
                        "domain_per_tri": _tv_list,
                        "A_z_per_node": _A_a.tolist(),
                        "Bmag_per_tri": _Bm_a.tolist(),
                        "J_z_per_tri": [],
                        "demag_coef_per_tri": [],
                        "extent": [float(_P_a[0].min()), float(_P_a[0].max()),
                                   float(_P_a[1].min()), float(_P_a[1].max())],
                        "n_vertices": int(_P_a.shape[1]),
                        "n_triangles": int(_T_a.shape[1]),
                        "A_z_min": float(_A_a.min()), "A_z_max": float(_A_a.max()),
                        "B_mag_max": float(_Bm_a.max()),
                    }
                    if _fi == 0:
                        _fr_out["outlines"] = _outlines_from_polys(_polys_a)
                        _fr_out["symmetry_mult"] = _nsec_a
                        _fr_out["n_sectors"] = _nsec_a
                        # Anti-periodic radial cut (A_z flips sign between
                        # adjacent sectors) ⇔ an ODD pole count per sector.
                        # The client tiles the wedge to the full ring and needs
                        # this to place each copy with the right sign.
                        _pol_n = int(_mot_a.parameters.get("num_poles") or 0)
                        _fr_out["anti_periodic"] = bool(
                            _nsec_a > 1 and _pol_n and (_pol_n // _nsec_a) % 2 == 1)
                    else:
                        # Rotor-attached outlines only; the client reuses the
                        # static stator/coil outlines from frame 0.
                        _fr_out["outlines_rotor"] = _outlines_from_polys(
                            {"rotor": _polys_a.get("rotor"),
                             "magnets": _polys_a.get("magnets", [])})
                    _frames_out.append(_fr_out)
                _sbres["frames"] = _frames_out
                _sbres["n_frames_returned"] = len(_frames_out)
            except Exception:
                log.exception("animation keyframe payload build failed")
        # ── Summary block (masses, loss split, KV, efficiency, specific
        # torque/power) so the Simulation values table renders.  Built by the
        # Stamp WHEN this run was solved — the UI shows it in the header so
        # "is this the fresh result?" is answerable at a glance (a stale
        # background tab was indistinguishable from a failed update).
        from datetime import datetime as _dtm
        _sbres["computed_at"] = _dtm.now().isoformat(timespec="seconds")
        # …and stamp WHICH MACHINE produced it.  This rides with the result into
        # the cache AND onto disk (.last_transient.json), so a restore after a
        # preset switch / a YAML edit / a back-end restart can be compared
        # against the live machine instead of being served as if it were current.
        _sbres["geo_fingerprint"] = _geometry_fingerprint(_geo_ov)
        # ── Park the last frame's field for the J⟳ / Loss views ───────────────
        # It leaves the HTTP payload here (megabytes of per-node arrays the
        # charts never read, and _save_last_transient would write them to disk)
        # and goes into the server-side snapshot store instead, keyed by the
        # physics that produced it.  Selecting J⟳ / Loss for THIS operating
        # point then renders the run's own field instead of re-solving it.
        # A per-candidate eval (optimizer / kernel run with a geo override) is
        # NOT the motor on screen — same rule as _save_last_transient below.
        # Storing those would evict the user's own run from a 3-deep store while
        # nobody ever looks at them.
        _fld_snap = _sbres.pop("field", None)
        if _geo_ov:
            _fld_snap = None
        if _fld_snap is not None:
            _store_transient_field_snapshot(
                _fsnap_key, key_fields=_fsnap_fields,
                field=_fld_snap, sbres=_sbres, eddy=bool(eddy),
                n_steps_per_period=int(n_steps_per_period),
                n_periods=float(n_periods),
                solve_time_s=(_t.time()
                              - _fem_transient_progress["current"].get("ts_start",
                                                                       _t.time())))
            log.info("transient: kept last-frame field snapshot for the field "
                     "views (eddy=%s, %d steps/period) — J⟳/Loss will render "
                     "without re-solving", bool(eddy), int(n_steps_per_period))
        # Say it in the payload too: the Simulation tab can tell the user the
        # J⟳ / Loss views are ready, and WHY they are (or are not).
        _sbres["field_snapshot"] = bool(_fld_snap is not None)
        _sbres["field_snapshot_eddy"] = bool(eddy) if _fld_snap is not None else False
        # SHARED helper (_build_transient_summary) so the direct route, the
        # legacy remesh path AND the kernel/solver.em_transient path all use the
        # SAME formula and every run returns a populated `summary`.
        try:
            _sbres["summary"] = _build_transient_summary(
                _sbres, I_phase_rms=I_phase_rms, gamma_deg=gamma_deg,
                coil_temp_c=coil_temp_c, geo_override=_geo_ov)
        except Exception as _se:
            # A summary-build failure MUST be visible (not a silently frozen
            # card set): log it AND attach an error marker so the frontend can
            # surface it instead of keeping stale numbers.
            log.exception("SB summary build failed")
            _sbres["summary_error"] = f"{type(_se).__name__}: {_se}"
        _fem_transient_cache[_sb_key] = _sbres
        # Persist for "restore last simulation" on reload — but ONLY the user's
        # MAIN motor. Per-candidate evals (optimizer / kernel runs with a geo
        # override) must never clobber the saved simulation, and parallel
        # optimizer subprocesses must not race on the shared store file.
        if not _geo_ov:
            _save_last_transient(_sb_key, _sbres)   # survive a back-end restart
        return _sbres
    except HTTPException:
        raise
    except Exception as _e:
        log.exception("sliding-band transient failed")
        raise HTTPException(status_code=500,
                            detail=f"sliding-band transient failed: {_e}")
    finally:
        # The run is over HERE — after the post-processing, not after the last
        # frame (see the phase note above).  Unconditional: an exception on any
        # path must not leave the progress endpoint reporting a live solve.
        _fem_transient_progress["current"]["running"] = False
        _fem_transient_lock.release()


# ─────────────────────────────────────────────────────────────────────────────
# 7b.  Analytic ΔP_harm screening (CIANO spec) — no FEM beyond two cached runs
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/physics/harm_screening")
def get_harm_screening(
    n_steps_per_period:  int   = 48,
    gamma_deg:           float = 0.0,
    I_phase_rms:         float = 85.0,
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    gap_layers:          float = 3.0,
    n_sectors:           int   = 4,
    coil_temp_c:         float = 120.0,
    end_winding_factor:  float = 0.0,
    rotor_eddy:          bool  = True,
    torque_filter:       bool  = False,
    pole_copy:           bool  = False,
    iron_template:       bool  = True,
    hi_fidelity:         bool  = False,
    structured_gap:      bool  = False,
    component_mesh:      str   = "",
    geo:                 Optional[str] = None,
):
    """Cheap analytic estimate of the harmonic-current losses a sinusoidal
    VOLTAGE supply (FOC) would add on this design — the CIANO screening step.

    Two current-drive runs (both cache-reusable): the LOADED point and NO-LOAD
    (I=0 → the pure back-EMF spectrum E_n + cogging).  Then per non-triplen
    harmonic the current a stiff sinusoidal supply forces through the winding
    is I_n ≈ E_n/(n·ω·L̂), with L̂ measured from the loaded fundamental phasors
    L̂ = |V̂₁ − R·Î₁ − Ê₁|/(ω·|Î₁|).  Copper cost scales as Σ(I_n/I₁)²·K_ac(n)
    on the run's own measured copper loss (K_ac(n) = 1 + (K_ac1−1)·n², skin/
    proximity); iron cost as Σ(E_n/E₁)²·n^0.4 on the measured iron loss.
    Analytic → instant on cache hits; the honest number is the voltage-drive
    transient (drive="voltage"), this ranks candidates without paying for it.
    """
    common = dict(
        n_steps_per_period=n_steps_per_period, n_periods=1.0,
        gamma_deg=gamma_deg, mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        gap_layers=gap_layers, n_sectors=n_sectors, coil_temp_c=coil_temp_c,
        end_winding_factor=end_winding_factor, rotor_eddy=rotor_eddy,
        torque_filter=torque_filter, pole_copy=pole_copy,
        iron_template=iron_template,
        hi_fidelity=hi_fidelity, structured_gap=structured_gap,
        component_mesh=component_mesh, geo=geo, sliding_band=True,
    )
    loaded = get_fem_transient(I_phase_rms=I_phase_rms, **common)
    noload = get_fem_transient(I_phase_rms=0.0, **common)

    import math
    import numpy as _np
    from motor_ai_sim.simulation.postproc import (voltage_harmonics,
                                                  complex_fundamental)
    R = float(loaded.get("R_phase_ohm", 0.0) or 0.0)
    w = 2.0 * math.pi * float(loaded.get("f_elec_Hz", 0.0) or 1.0)
    V1 = complex_fundamental(loaded, "V_A")
    I1 = complex_fundamental(loaded, "I_A")
    E1 = complex_fundamental(noload, "V_A")
    if abs(I1) < 1e-6 or w <= 0.0:
        raise HTTPException(status_code=422,
                            detail="loaded run has no fundamental current")
    L_hat = abs(V1 - R * I1 - E1) / (w * abs(I1))
    _vh = voltage_harmonics(noload)
    amps = _vh.get("V_harm_amp") or []          # E_n, orders 1..h_max
    E1a = amps[0] if amps else 0.0
    # Non-triplen harmonic currents under a stiff sinusoidal supply.
    harm = []
    _sum_fe = 0.0
    for n_ord in range(2, len(amps) + 1):
        if n_ord % 3 == 0:
            continue                             # triplen: floating wye blocks it
        En = amps[n_ord - 1]
        In = En / (n_ord * w * L_hat) if L_hat > 1e-12 else 0.0
        _sum_fe += (En / E1a) ** 2 * n_ord ** 0.4 if E1a > 1e-9 else 0.0
        if In > 1e-3:
            harm.append({"n": n_ord, "E_n_V": round(En, 3),
                         "I_n_A": round(In, 3)})
    # Copper: the run's own measured copper loss scaled by the harmonic-current
    # ratio with AC (skin/proximity) growth K_ac(n)=1+(K_ac1−1)n².
    _pcu = float(_np.mean(loaded.get("P_cu_W") or [0.0]))
    _pcu_dc = float(loaded.get("P_cu_dc_W", 0.0) or 0.0)
    _kac1 = (_pcu / _pcu_dc) if _pcu_dc > 1e-9 else 1.0
    _sum_cu = 0.0
    for h in harm:
        _kacn = 1.0 + (_kac1 - 1.0) * h["n"] ** 2
        _sum_cu += (h["I_n_A"] / abs(I1)) ** 2 * (_kacn / max(_kac1, 1.0))
    dP_cu = _pcu * _sum_cu
    _pfe = float(_np.mean(loaded.get("P_fe_W") or [0.0]))
    dP_fe = _pfe * _sum_fe
    _Tnl = _np.asarray(noload.get("T_em_Nm") or [0.0], dtype=float)
    _thd_pred = (100.0 * math.sqrt(sum((h["I_n_A"] / abs(I1)) ** 2
                                       for h in harm)))
    return {
        "L_hat_H": round(L_hat, 8),
        "R_phase_ohm": R,
        "E1_V": round(E1a, 2),
        "E_THD_LL_pct": _vh.get("THD_LL_pct", 0.0),
        "I1_A": round(abs(I1), 3),               # branch amplitude, as I_A series
        "harmonic_currents": harm,
        "THD_I_pred_pct": round(_thd_pred, 2),
        "dP_cu_harm_W": round(dP_cu, 1),
        "dP_fe_harm_W": round(dP_fe, 1),
        "dP_harm_W": round(dP_cu + dP_fe, 1),
        "cogging_pkpk_Nm": round(float(_Tnl.max() - _Tnl.min()), 3),
        "T_avg_Nm": round(float(loaded.get("T_avg_Nm", 0.0)), 3),
        "P_cu_W": round(_pcu, 1), "P_fe_W": round(_pfe, 1),
        "K_ac1": round(_kac1, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Component mass calculator
# ─────────────────────────────────────────────────────────────────────────────
def _compute_masses(p, geo_cfg: dict, k_end: float = 0.0) -> dict:
    """Calculate mass of each motor component.

    ``k_end`` (0 = auto): the end-winding factor to bill the copper at.  Pass the
    SAME value the loss/R path used (the solver returns it as ``end_winding_factor``)
    so the copper MASS and the copper LOSS / phase R agree — whether k_end is the
    geometric auto value or a user pin.  0 → ``compute_masses`` derives it from the
    geometry (matching the solver's own auto path).

    Every density and lamination fill factor comes from the material ASSIGNED to
    that part (config `materials:` / the per-request override) — the same records
    the field solve reads — and every cross-section is measured on the CAD
    polygons the mesher receives.  Nothing here is hard-coded.
    """
    # SINGLE SOURCE: motor_ai_sim.masses.compute_masses — identical mass to the sweep
    # and all three optimizers (CAD sections × stack × k_f × assigned density).
    from motor_ai_sim.masses import compute_masses
    m = compute_masses(p, geo_cfg, k_end=k_end)
    m_total = m["total"]
    _mat = m["MAT"]
    _rho = m["RHO"]
    _lam = ", lamination k_f={:.3f}"
    return {
        "components": [
            {"name": f"Stator core ({_mat['stator_core'] or 'steel'})", "material": "electrical steel",
             "density_kg_m3": _rho["steel"],
             "volume_cm3": round(m["V_stator"]*1e6, 1), "mass_kg": round(m["stator"], 3),
             "note": f"CAD section {m['A_stator']*1e6:.0f} mm² × stack"
                     + _lam.format(m["k_f_stator"])},
            {"name": "Copper windings (Cu)",    "material": _mat["slot"] or "copper",
             "density_kg_m3": _rho["cu"],
             "volume_cm3": round(m["V_cu"]*1e6, 1), "mass_kg": round(m["cu"], 3),
             "note": f"measured copper section {m['A_cu']*1e6:.0f} mm² × stack × k_end {m['k_end']:.3f}"},
            {"name": f"Magnets ({_mat['magnet'] or 'PM'})",  "material": "NdFeB",
             "density_kg_m3": _rho["mag"],
             "volume_cm3": round(m["V_mag"]*1e6, 1), "mass_kg": round(m["mag"], 3),
             "note": f"CAD magnet polygons {m['A_mag']*1e6:.0f} mm² × stack"},
            {"name": f"Rotor back-iron ({_mat['rotor_core'] or 'steel'})", "material": "electrical steel",
             "density_kg_m3": _rho["steel_rotor"],
             "volume_cm3": round(m["V_rotor"]*1e6, 1), "mass_kg": round(m["rotor"], 3),
             "note": f"CAD section {m['A_rotor']*1e6:.0f} mm² × stack"
                     + _lam.format(m["k_f_rotor"])},
            {"name": f"Shaft ({_mat['shaft'] or 'shaft'})",  "material": "shaft",
             "density_kg_m3": _rho["al"],
             "volume_cm3": round(m["V_shaft"]*1e6, 1), "mass_kg": round(m["shaft"], 3),
             "note": f"hollow shaft tube {m['A_shaft']*1e6:.0f} mm² × stack — NOT in active mass"},
        ],
        # ACTIVE = the EM-active mass (iron + copper + magnets), the basis ANSYS
        # quotes; TOTAL = active + shaft, the historical divisor of torque-per-mass
        # (unchanged, so stored Compare points keep their meaning).
        "mass_active_kg": round(m["active"], 3),
        "mass_total_kg": round(m_total, 3),
        "total_active_kg": round(m_total, 3),   # legacy name = TOTAL, kept for old readers
        "area_source": m["area_source"],
        "note": "Active section only (no housing/frame/bearings). Typical frame adds 30-50% mass.",
        "estimated_total_with_frame_kg": round(m_total * 1.4, 2),
    }


def _build_transient_summary(
    sbres: dict,
    *,
    I_phase_rms: float,
    gamma_deg: float,
    coil_temp_c: float,
    geo_override: Optional[dict] = None,
) -> dict:
    """Build the Simulation summary block (masses, loss split, KV, efficiency,
    specific torque/power) from a finished transient result dict.

    THE single source for the summary formula — called by BOTH the sliding-band
    path AND the legacy remesh path in get_fem_transient, AND by the kernel's
    solver.em_transient module (via get_fem_transient), so the summary can never
    drift between the direct route and the kernel and every card is populated on
    every run.  Operates purely on the result dict's keys (T_avg_Nm, P_cu_W,
    P_fe_W, P_mag_eddy_W, P_shaft_eddy_W, V_peak, P_elec_in_W, rpm, …), defaulting
    any key a given path did not emit — so it is robust to either producer.

    ``geo_override`` (the per-request geometry, if any) is threaded into the mass
    calc so an applied design's torque/mass density reflects THAT geometry, not
    the globally-saved one.
    """
    import math as _math
    import numpy as _np
    from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
    from motor_ai_sim.simulation.postproc import voltage_harmonics as _vharm
    from motor_ai_sim.config import get_config as _gc

    # Masses for the CURRENT operating geometry (respect a per-request override so
    # an applied design's specific torque/power is billed against its own mass).
    _p = _pfc(geo_override=geo_override)
    # merge_geo_override, not a dict-update: the update kept the CONFIG's counts
    # and derived fields (slot_width, radii, pitches) next to the override's
    # primaries, so an applied design was billed against a chimera's mass.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _merge_geo
    _geo_cfg = _merge_geo(dict(_gc().get("geometry", {})), geo_override)
    _masses = _compute_masses(_p, _geo_cfg,
                              k_end=float(sbres.get("end_winding_factor", 0.0) or 0.0))
    # TOTAL (active + shaft) stays the divisor of torque/power/loss density — the
    # basis every stored Compare point and optimizer objective was built on.
    # ACTIVE (iron + copper + magnets, no shaft) is reported alongside: it is the
    # number an ANSYS "active mass" expression quotes, and the tile the user
    # cross-checks against it.
    _m_tot = float(_masses["mass_total_kg"])
    _m_active = float(_masses["mass_active_kg"])

    _rpm = float(sbres.get("rpm", 3950.0))
    _Tavg = float(sbres.get("T_avg_Nm", 0.0))
    # The card's mechanical power IS its torque tile times speed — the two
    # numbers must agree by inspection (T·ω).  The solver's energy-balanced
    # P_mech_avg_W stays available below as a diagnostic; it differed by ~1 %
    # and made the card contradict itself (user-caught, 2026-08-04).
    _Pmech = _Tavg * 2 * _math.pi * _rpm / 60.0
    _Pmech_balance = float(sbres.get("P_mech_avg_W", _Pmech))

    # Period-MEAN of each instantaneous loss series — NOT [0].  The iron/magnet
    # series ripple as the teeth pass; frame 0 sits near a peak, so [0] would
    # overstate the reported average loss.  Copper is DC (flat) so its mean == [0].
    def _mean(_k):
        _s = sbres.get(_k) or [0.0]
        return float(_np.mean(_np.asarray(_s, float))) if len(_s) else 0.0
    _Pcu = _mean("P_cu_W")
    _Pfe = _mean("P_fe_W")
    _Pmag = _mean("P_mag_eddy_W")
    _Pshaft = _mean("P_shaft_eddy_W")   # solid-shaft eddy (bulk conductor)

    # Voltage figures from the ACTUAL waveforms.  The old shortcuts
    # (rms = pk/√2, line = √3·phase) hold only for a pure balanced sinusoid:
    # on a concentrated winding the PHASE peak is inflated by the (large)
    # triplen harmonics, which CANCEL in the line-to-line difference — the
    # √3 shortcut then overstates the line peak by tens of percent (measured
    # 121.6 V card vs 95 V real V_A−V_B on the 24s28p), which corrupts
    # battery/inverter sizing.  Fall back to the shortcuts only when the
    # per-frame series are missing (old stored runs).
    import numpy as _np_v
    _Vpk = float(sbres.get("V_peak", 0.0))
    _Vrms = _Vpk / _math.sqrt(2)
    _Vlpk = _Vpk * _math.sqrt(3)
    _Vlrms = _Vlpk / _math.sqrt(2)
    _vs = [sbres.get(k) for k in ("V_A", "V_B", "V_C")]
    if all(isinstance(v, (list, tuple)) and len(v) == len(_vs[0]) and len(v) >= 4
           for v in _vs):
        _va, _vb, _vc = (_np_v.nan_to_num(_np_v.asarray(v, float),
                                          nan=0.0, posinf=0.0, neginf=0.0)
                         for v in _vs)
        _Vpk = float(max(_np_v.max(_np_v.abs(p)) for p in (_va, _vb, _vc)))
        _Vrms = float(_np_v.mean([_np_v.sqrt(_np_v.mean(p ** 2))
                                  for p in (_va, _vb, _vc)]))
        _lls = (_va - _vb, _vb - _vc, _vc - _va)
        _Vlpk = float(max(_np_v.max(_np_v.abs(p)) for p in _lls))
        _Vlrms = float(_np_v.mean([_np_v.sqrt(_np_v.mean(p ** 2))
                                   for p in _lls]))

    # Total INCLUDES shaft eddy so the breakdown sums to the same loss the solver's
    # energy-balanced P_mech subtracts (else the card's Mech-power ≠ Σ losses).
    _ploss = _Pcu + _Pfe + _Pmag + _Pshaft
    # Efficiency = shaft out / (shaft out + EVERY loss the card reports).  The
    # solved-circuit P_elec_in_W is NOT the denominator: the 2-D circuit only
    # carries the losses that live in the field solve (active-length copper +
    # solved eddy — measured 295 W of the card's 500 W on the 150 mm), while
    # the analytic iron loss and the end-winding copper are post-processed and
    # never flow through its terminals.  Dividing by it showed 97.81 % where
    # the card's own numbers said 96.3 % (user-caught, 2026-08-04).  The solved
    # terminal power stays below as an energy-balance diagnostic.
    _Pelec_solved = float(sbres.get("P_elec_in_W", 0.0) or 0.0)
    _Pelec = _Pmech + _ploss
    _eff = (_Pmech / _Pelec) if (_Pmech > 0 and _Pelec > 1.0) else 0.0

    # Voltage waveform quality (CIANO THD spec): V₁ + phase/line-to-line THD +
    # torque constant — first-class metrics on EVERY run so the optimizer can
    # hold THD_LL like it holds ripple.
    _vh = _vharm(sbres)
    # Current waveform quality: ≈0 in current drive (imposed sinusoids); in
    # VOLTAGE drive this is the real parasitic harmonic-current content the
    # distorted back-EMF forces through the winding.
    from motor_ai_sim.simulation.postproc import current_harmonics as _iharm
    _ih = _iharm(sbres)
    # Voltage drive: the requested I_phase_rms is 0 — use the run's own
    # fundamental current for Kt so the card stays meaningful in both modes.
    _kt_I = float(I_phase_rms or 0.0)
    if _kt_I <= 1e-9 and str(sbres.get("drive", "")).startswith("v"):
        try:
            from motor_ai_sim.simulation.postproc import fundamental_current
            _kt_I = float(fundamental_current(sbres)["I1_phase_rms_A"])
        except Exception:
            _kt_I = 0.0
    _kt = round(_Tavg / _kt_I, 4) if _kt_I > 1e-9 else 0.0

    # Coil current density J = conductor current / bare-copper cross-section.
    # The conductor carries the phase current split over the a_parallel paths
    # (turns are in series within a path); its cross-section is one strand
    # (wire_width × wire_height).  This is the standard machine J [A/mm²] — the
    # thermal-loading figure of merit ("Irms / phase conductor section").
    # n_parallel comes from THE RUN, not from the shared config: a solve driven
    # by an explicit connection label (a catalog eval, an optimizer candidate,
    # or the Simulation panel in the window between the click and the config
    # sync) uses paths the config has not been told about, and dividing by the
    # config's value quoted a J_coil the machine never saw.
    _wind = _gc().get("winding", {})
    _npar = max(1, int(sbres.get("n_parallel") or _wind.get("n_parallel", 1) or 1))
    # …and the label those paths belong to, so the card can name the winding it
    # is reporting (empty when the run carried no consistent label).
    _conn = str(sbres.get("connection") or "")
    _a_cond_mm2 = float(_geo_cfg.get("wire_width", 0.0)) * float(_geo_cfg.get("wire_height", 0.0))
    _j_coil = (float(I_phase_rms) / _npar / _a_cond_mm2) if _a_cond_mm2 > 1e-9 else 0.0

    # ── R and L-dq of this operating point ───────────────────────────────────
    # R_phase comes out of the solve and ALREADY includes the end-winding
    # (copper_loss_W: ρ_Cu(T)·J²·V_cu·k_end → R = P/(3I²)).  Line-to-line is
    # quoted for the isolated-neutral star this machine is driven as (the
    # voltage circuit is line-to-line for exactly that reason): R_LL = 2·R_ph.
    _R_ph = float(sbres.get("R_phase_ohm", 0.0) or 0.0)
    # Ld/Lq: ψd = ψ_PM + Ld·id and ψq = Lq·iq, with ψ_PM measured at I=0 (one
    # cached no-load solve per geometry).  Refused rather than guessed when the
    # frame cannot be trusted: the dq torque identity must reproduce the energy
    # torque, and ψq at no load must be small next to ψ_PM.
    _Ld_mH = _Lq_mH = _psi_pm = None
    _dq_note = ""
    try:
        _psid = sbres.get("psi_d_Wb"); _psiq = sbres.get("psi_q_Wb")
        _idm = sbres.get("i_d_A"); _iqm = sbres.get("i_q_A")
        _chk = sbres.get("dq_torque_check_pct")
        if None in (_psid, _psiq, _idm, _iqm):
            _dq_note = "run predates the dq stamp — re-run to compute Ld/Lq"
        elif _chk is None or _chk > 5.0:
            _dq_note = ("dq frame failed its torque self-check (%.1f%% vs the "
                        "energy torque) — Ld/Lq withheld" % (_chk or -1))
        else:
            from motor_ai_sim.simulation.fem_solver_2d import noload_psi_pm
            _geo_sum = dict(_geo_cfg)
            _pp_sum = int(_geo_sum.get("num_poles", 0)) // 2
            _pm, _q0 = noload_psi_pm(
                _geo_sum, dict(_gc().get("winding", {}) or {}), _pp_sum,
                0, float(sbres.get("daxis_deg", 0.0)),
                geo_override=geo_override,
                # ψ_PM at the RUN's own connection (n_series scales it).
                connection=(str(sbres.get("connection")) or None))
            if abs(_pm) > 1e-9 and abs(_q0) > 0.05 * abs(_pm):
                _dq_note = ("no-load ψq is %.1f%% of ψ_PM — frame suspect, "
                            "Ld/Lq withheld" % (100 * abs(_q0 / _pm)))
            else:
                _psi_pm = float(_pm)
                if abs(float(_iqm)) > 1e-3:
                    _Lq_mH = 1e3 * float(_psiq) / float(_iqm)
                if abs(float(_idm)) > 1e-3:
                    _Ld_mH = 1e3 * (float(_psid) - _psi_pm) / float(_idm)
                else:
                    _dq_note = ("i_d ≈ 0 at γ = %.1f° — Ld needs d-axis "
                                "current; set γ ≠ 0 and re-run" % float(gamma_deg))
    except Exception as _el:   # noqa: BLE001
        _dq_note = f"{type(_el).__name__}: {_el}"

    return {
        "rpm": _rpm,
        "I_phase_rms_A": round(float(I_phase_rms), 2),
        "gamma_deg": round(float(gamma_deg), 2),
        # The winding these numbers belong to.  Torque, voltage and R_phase all
        # move with the connection, so a card that does not name it cannot be
        # checked against the selector — which is exactly how "I changed the
        # connection and nothing moved" became unanswerable.
        "connection": _conn,
        "n_parallel": _npar,
        # Terminal parameters, persisted with every run (they ride the summary
        # into sim.lastSummary, .last_transient.json, Compare and the motor
        # autosave — one write path, no separate store to rot).
        "R_phase_ohm": (round(_R_ph, 6) if _R_ph else None),
        "R_line_line_ohm": (round(2.0 * _R_ph, 6) if _R_ph else None),
        "Ld_mH": (None if _Ld_mH is None else round(_Ld_mH, 4)),
        "Lq_mH": (None if _Lq_mH is None else round(_Lq_mH, 4)),
        "psi_pm_Wb": (None if _psi_pm is None else round(_psi_pm, 6)),
        "saliency_Lq_over_Ld": (round(_Lq_mH / _Ld_mH, 3)
                                if _Ld_mH not in (None, 0) and _Lq_mH is not None
                                else None),
        "dq_note": _dq_note,
        "T_em_avg_Nm": round(_Tavg, 3),
        "T_ripple_pct": round(float(sbres.get("T_ripple_pct", 0.0)), 1),
        "T_ripple_raw_pct": round(float(sbres.get("T_ripple_raw_pct", 0.0)), 1),
        "T_ripple_filt_pct": round(float(sbres.get("T_ripple_filt_pct",
                                                   sbres.get("T_ripple_pct", 0.0))), 1),
        # Mesh-noise floor: RMS of the forbidden (non-6·k) torque orders, % of
        # mean torque — how much numerical noise the 6·k gate removed.
        "T_noise_floor_pct": round(float(sbres.get("T_noise_floor_pct", 0.0)), 2),
        "P_mech_W": round(_Pmech, 1),
        "V_phase_peak_V": round(_Vpk, 1),
        "V_phase_rms_V": round(_Vrms, 1),
        "V_line_peak_V": round(_Vlpk, 1),
        "V_line_rms_V": round(_Vlrms, 1),
        # KV = rpm / V_peak — the user's (and their Ansys table's) convention:
        # max(rpm)/max(voltage), i.e. the PEAK of the waveform shown right next
        # to this tile, not the fundamental rms (which read ~sqrt(2) higher and
        # contradicted a by-hand rpm/V_line_peak check, 2026-08-04).  NB this is
        # the LOADED voltage: at field-weakening γ it differs from the no-load
        # back-EMF KV; a true no-load KV needs an I=0 run (harm_screening E1).
        "KV_rpm_per_V_phase": (round(_rpm / _Vpk, 2) if _Vpk > 1 else 0.0),
        "KV_rpm_per_V_line": (round(_rpm / _Vlpk, 2) if _Vlpk > 1 else 0.0),
        "V1_LL_V":        _vh.get("V1_LL_V", 0.0),
        "V1_phase_V":     _vh["V1_phase_V"],
        "THD_pct":        _vh["THD_pct"],
        "THD_LL_pct":     _vh["THD_LL_pct"],
        "I1_A":           _ih["I1_A"],
        "THD_I_pct":      _ih["THD_I_pct"],
        "Kt_Nm_per_Arms": _kt,
        "J_coil_A_per_mm2": round(_j_coil, 1),   # I_rms/parallel over one strand's copper section
        "P_loss_total_W": round(_ploss, 1),
        "P_core_W":     round(_Pfe, 1),            # laminated iron
        # Which Bertotti TERM the core loss is, stator vs rotor.  "The core loss
        # reads low" is only answerable by the split, and the card had no way to
        # show it.  {stator|rotor: hysteresis_W, eddy_W, excess_W, k_f}.
        "P_core_terms": sbres.get("P_fe_terms") or {},
        "P_stranded_W": round(_Pcu, 1),            # copper
        # ── copper-AC honesty: wire_split is NOT in the solved value ─────
        # ``wire_split`` subdivides the bar into insulated, transposed strips.
        # It is an ELECTRICAL subdivision with no CAD geometry behind it, so
        # the mesher gets the whole bar and the coupled σ·∂A/∂t solve reports
        # the AC loss of a SOLID drawn conductor — up to wire_split² too much on
        # the width-direction term.  (The modelled proximity path does divide by
        # it, simulation/losses.copper_ac_dims.)  Flagged rather than silently
        # corrected: correcting it needs the strips in the mesh.
        "wire_split": int(float(_geo_cfg.get("wire_split", 1) or 1)),
        "cu_ac_solved_ignores_wire_split":
            bool(sbres.get("eddy_coupled")
                 and float(_geo_cfg.get("wire_split", 1) or 1) > 1),
        "P_solid_W":    round(_Pmag + _Pshaft, 1), # magnet + shaft eddy
        # AXIAL magnet segmentation (`magnet_lamination`): the factor the 2-D
        # magnet eddy loss was multiplied by, and the loop width it was derived
        # from.  {} on a run that predates it; factor 1.0 on a solid magnet.
        "magnet_segmentation": sbres.get("magnet_segmentation") or {},
        # How many discarded frames at θ<0 the coupled eddy solve needed before
        # the σ·∂A/∂t start-up transient was quiet enough to start reporting.
        # It belongs beside P_solid_W because that is the number an un-settled
        # window corrupts first (measured 262 W against a settled 68 W on the
        # 150 mm), so the card that shows it can say what it cost to be honest.
        "eddy_warmup_frames": int(sbres.get("eddy_warmup_frames") or 0),
        "efficiency":   round(_eff, 4),
        # Energy-balance diagnostics: the solved terminal power and the solver's
        # balanced mech power.  P_elec_in_solved − P_mech_balance = the losses
        # that live INSIDE the field solve; the gap to P_loss_total_W is the
        # post-processed remainder (iron + end-winding copper).
        "P_elec_in_solved_W": round(_Pelec_solved, 1),
        "P_mech_balance_W":   round(_Pmech_balance, 1),
        # ── nonlinear-solve honesty ──────────────────────────────────────
        # Every frame of the reported window has to have met its solver's
        # convergence test; if one did not, these numbers are an average over
        # a field that was never converged, and the card says so instead of
        # leaving it in a log line nobody reads.
        "nonlinear_converged": bool(sbres.get("picard_converged", True)),
        "nonlinear_resid_max": float(sbres.get("picard_resid_max", 0.0) or 0.0),
        "nonlinear_tol": float(sbres.get("picard_tol", 0.0) or 0.0),
        "nonlinear_unconverged_frames":
            list(sbres.get("picard_unconverged_frames") or []),
        # ── time-resolution honesty ──────────────────────────────────────
        # The solver snaps steps/period onto the slip-node divisor grid.  When
        # it does, the run is NOT at the requested resolution, and the card has
        # to say so rather than present the snapped number as the asked-for one.
        "steps_snapped": bool(sbres.get("steps_snapped", False)),
        "n_steps_per_period": int(sbres.get("n_steps_per_period", 0) or 0),
        "n_steps_per_period_requested":
            int(sbres.get("n_steps_per_period_requested",
                          sbres.get("n_steps_per_period", 0)) or 0),
        "slip_nodes_per_period": int(sbres.get("slip_nodes_per_period", 0) or 0),
        "coil_temp_C":  round(float(sbres.get("coil_temp_C", coil_temp_c)), 1),
        "end_winding_factor": round(float(sbres.get("end_winding_factor", 0.0)), 2),
        "mass_total_kg": round(_m_tot, 3),
        "mass_active_kg": round(_m_active, 3),
        "mass_area_source": _masses.get("area_source", ""),
        "mass_components": _masses["components"],
        "torque_per_mass_Nm_kg": round(_Tavg / max(_m_tot, 1e-6), 3),
        "power_per_mass_W_kg":   round(_Pmech / max(_m_tot, 1e-6), 1),
        "loss_density_W_kg":     round(_ploss / max(_m_tot, 1e-6), 1),
    }
