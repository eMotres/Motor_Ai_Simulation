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

# Field-view solves: dedupe concurrent twins, cap how many run at once, and
# say out loud what is running (see motor_ai_sim/field_jobs.py).
from motor_ai_sim import jobs as _JOBS
from motor_ai_sim import progress as _PROG
from motor_ai_sim import workspace as _WSP
from motor_ai_sim.field_jobs import field_busy, run_field_job

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

# Passport generation solves the LIVE machine's geometry (zero-touch — as an
# override), which made its sweep points indistinguishable from the user's own
# runs: a background loss-grid point (1.5·I0 @ 6000 rpm) persisted as the last
# transient and REPLACED the card in the user's browser (measured live
# 2026-08-25: "мощность упала, момент вырос, я ничего не трогал").  Solves
# made under this flag never persist and never touch the field-snapshot store.
from contextvars import ContextVar as _CtxVar
_BACKGROUND_RUN: "_CtxVar[bool]" = _CtxVar("background_run", default=False)

# ── Per-workspace geometry cache (migration Stage 3) ─────────────────────────
# Was "built once per server start", which on a multi-user box means built once
# for whoever ran first and then handed to everyone: the polygons of ONE
# machine answering every caller's frame request.  One cache per workspace now,
# each keyed by rotor angle and dropped whole when that workspace's geometry
# hash moves — the same invalidation, one machine narrower.
#
# CAP: 512 rotor angles.  A transient asks for one polygon set per frame and
# the longest runs in the repo are 464 steps, so the cap is above a full run
# and far below "unbounded"; the hash check below still empties it on an edit.
_MOTOR_GEOM_CACHE_MAX = 512
_motor_geom_cache = _WSP.ws_map("simulation.motor_geom", _MOTOR_GEOM_CACHE_MAX,
                                lru_on_read=True)
#: hash of the geometry the cache was built for — one slot, per workspace
_motor_geom_ghash = _WSP.ws_list("simulation.motor_geom_ghash",
                                 seed=lambda: [None])

_VALID_MESH_COMPONENTS = ("stator", "rotor", "magnet", "coil", "shaft",
                          "airgap", "outer",
                          # NOT a mm size — the "Wire cell" FACTOR (½h/1h/2h,
                          # h = wire height).  It rides this same block so the
                          # duty save/restore, the per-die settings memory and
                          # every cache key carry it with no extra plumbing.
                          "coil_rel")


def _parse_component_mesh(s: str) -> dict:
    """Parse the per-component mesh-size JSON ({comp: size_mm}) coming from the
    UI into a clean {comp: float} dict.  Unknown keys / non-positive sizes are
    dropped so a stray value can never corrupt the gmsh size field.  Returns {}
    for an empty / malformed string (→ global size everywhere).

    "coil_rel" is the one dimensionless entry: it is snapped to the nearest of
    0.5 / 1 / 2 rather than dropped or rejected, so a hand-written or older
    client cannot fail a whole simulation over a mesh cosmetic (see
    geo_mesh.snap_coil_rel for why snapping, not 400, is the right answer)."""
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
        if kk == "coil_rel":
            from motor_ai_sim.simulation.geo_mesh import snap_coil_rel
            fr = snap_coil_rel(v)
            if fr > 0.0:
                out[kk] = fr
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
    torque_filter:      Optional[bool]  = None   # deprecated, ignored; raw torque only
    drive:              Optional[str]   = None   # "current" | "voltage" | "pwm_voltage"
                                                 #  | "custom_current"
    v_phase_peak:       Optional[float] = None   # voltage drive: phase amplitude [V peak]
    v_delta_deg:        Optional[float] = None   # voltage drive: angle [deg el]
    v_bus:              Optional[float] = None   # pwm_voltage: DC link [V]
    f_switch:           Optional[float] = None   # pwm_voltage: carrier [Hz]
    i_block:            Optional[float] = None   # bldc_current: flat-top block [A]
    # NOT the custom waveform: it is up to 20k samples and belongs to the run
    # that used it, not to the shared operating point every off-tab consumer
    # (sweep / optimizer / descent) reads.
    coil_temp_c:        Optional[float] = None   # copper temperature -> rho_Cu(T)
    steps_per_period:   Optional[int]   = None   # transient frames per electrical period
    end_winding_factor: Optional[float] = None   # k_end (0 = auto from geometry)
    connection:         Optional[str]   = None   # winding: "4S" | "2S-2P" | "4P"
    # TERMINAL connection of the three phases — orthogonal to `connection`,
    # which groups a phase's own coils.  Delta puts the full line voltage on
    # each winding (so sqrt(3) more turns fit the same bus) and closes a loop
    # the winding's zero-sequence triplen EMF can drive current round.
    star_delta:         Optional[str]   = None   # "star" (default) | "delta"
    # How the k wires IN HAND are joined.  "transposed" is the perfectly
    # transposed winding (the assumption this solver made silently until
    # 2026-09-11), "series" the real coil soldered at its two ends, "parallel"
    # the upper bound with every half-turn joined.  Only meaningful with the
    # coupled eddy solve and more than one wire in hand.
    strand_bonding:     Optional[str]   = None   # "transposed" | "series" | "parallel"
    # D-AXIS REFERENCE, electrical degrees.  A number PINS it and nothing is
    # solved to find it (the 24-frame no-load calibration is skipped entirely);
    # "" or "auto" clears the pin and the measurement runs again.  Both are
    # states the user chose explicitly — what this must never become is a stale
    # angle nobody can see, so it lives in the config the panel shows and is
    # stamped into every result as `daxis_source`.
    daxis_deg:          Optional[Union[float, str]] = None
    # Operating mode: "motor" | "generator" — the generator drives the same
    # panel gamma shifted 180 deg el (current opposes the EMF).
    mode:               Optional[str]   = None


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

    # Resolved PER CALL from ``DEFAULT_CONFIG_PATH`` (which honours
    # ``MOTOR_AI_SIM_CONFIG``) — the same fix ``simulation/geometry_2d.py`` got
    # on 2026-09-15.  This was a hardcoded ``Path(__file__)…/config`` and it is a
    # WRITER: a redirected process (a test, a sandboxed in-process run) patched
    # the operating point of the machine the USER has loaded, which is exactly
    # what the redirect exists to make impossible (config.py, 2026-08-06).  With
    # no env var set this is byte-identical to the old constant.
    try:
        from motor_ai_sim.config import config_path as _resolve_cfg_path
        cfg_path = Path(str(_resolve_cfg_path()))
    except Exception:                       # noqa: BLE001 — never fail the PATCH
        cfg_path = Path(__file__).parent.parent.parent.parent / "config" / "motor_config.yaml"
    content = cfg_path.read_text(encoding="utf-8")

    updates = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    # What the block says NOW — so the cache flush below can tell a real
    # change from a re-save of the same values.  User 2026-09-07 ("опять то же
    # самое… сколько можно повторять?"): a panel re-saved unchanged settings
    # right after a run finished, this handler flushed every cache including
    # the fresh field snapshot, and the field view said "not solved".
    try:
        from motor_ai_sim.config import get_config as _get_cfg
        _before_sim = dict((_get_cfg() or {}).get("simulation") or {})
    except Exception:  # noqa: BLE001
        _before_sim = {}

    # The load angle lives under two names — phase_offset_deg (UI, presets) and
    # gamma_deg (solver, optimizer) — and the same for the current. Writing only
    # the one the caller named let them drift: the panel showed γ=0 while the FEM
    # ran at 10, and an optimization inherited a stale current. Mirror them.
    for _a, _b in (("phase_offset_deg", "gamma_deg"), ("max_current", "current_a")):
        if _a in updates and _b not in updates:
            updates[_b] = updates[_a]
        elif _b in updates and _a not in updates:
            updates[_a] = updates[_b]

    # `connection` is read by every solve from the WINDING block
    # (`_effective_winding` → winding.connection / winding.n_parallel), not from
    # the `simulation:` entry, which is a display mirror nothing consumes.
    # Patching only the mirror changed nothing physical: the panel showed the
    # new label while the FEM kept solving the old paths.  Write the winding
    # block too — the label plus the counts parse_connection derives from it
    # ("2S-2P" → n_parallel=2, n_series=2) so the block stays internally
    # consistent.  The mirror is still written for backward compat.  ""/"auto"
    # keeps the winding block untouched (there is no "measured" connection to
    # fall back to); an unreadable label is a 422, never a silent 1-path solve.
    wind_updates: dict = {}
    # star / delta goes to the winding block for the same reason the label
    # does: every solve resolves it from there, and the duty save copies the
    # whole block into the configuration.
    _sd_req = updates.get("star_delta")
    if isinstance(_sd_req, str) and _sd_req.strip():
        wind_updates["star_delta"] = ("delta" if _sd_req.strip().lower()
                                      .startswith("d") else "star")
    _conn_req = updates.get("connection")
    if isinstance(_conn_req, str) and _conn_req.strip().lower() not in ("", "auto"):
        from motor_ai_sim.winding import parse_connection as _parse_conn
        try:
            _n_par, _n_ser = _parse_conn(_conn_req)
        except ValueError as _e:
            raise HTTPException(status_code=422, detail=str(_e))
        wind_updates.update({"connection": _conn_req.strip(),
                             "n_parallel": _n_par, "n_series": _n_ser})

    lines = content.splitlines(keepends=True)
    in_sim = False
    in_wind = False
    result = []
    replaced = set()
    wind_replaced = set()
    appended = set()

    def _yaml_scalar(val) -> str:
        if isinstance(val, str) and val.strip().lower() in ("", "auto"):
            return "null"
        if isinstance(val, bool):
            return "true" if val else "false"
        return str(val)

    def _flush_sim_missing():
        # Model-known keys the yaml has never carried yet are APPENDED at the
        # end of the simulation block instead of refused.  The refusal made the
        # first machine ever to use a new drive parameter unable to save ANY
        # setting: v_bus/f_switch/i_block were in the pydantic model but not in
        # the file, so the whole PATCH 422'd and Run died before starting
        # (measured live 2026-08-31).  The model is the whitelist; a key it
        # accepts is a key the file may gain.
        for _k, _v in updates.items():
            if _k not in replaced and _k not in appended:
                result.append(f"  {_k}: {_yaml_scalar(_v)}\n")
                appended.add(_k)

    def _flush_wind_missing():
        # Keys the winding block did not carry are appended at its end, so an
        # older config without n_series/n_parallel still comes out consistent.
        for _k in ("connection", "n_parallel", "n_series", "star_delta"):
            if _k in wind_updates and _k not in wind_replaced:
                result.append(f"  {_k}: {wind_updates[_k]}\n")
                wind_replaced.add(_k)

    for line in lines:
        if re.match(r'^simulation\s*:', line):
            in_sim = True
        elif in_sim and re.match(r'^\S', line):
            in_sim = False
            _flush_sim_missing()
        if re.match(r'^winding\s*:', line):
            in_wind = True
        elif in_wind and re.match(r'^\S', line):
            in_wind = False
            _flush_wind_missing()

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
        elif in_wind and wind_updates:
            for key, val in wind_updates.items():
                m = re.match(rf'^(\s+{re.escape(key)}\s*:\s*)(.*)$', line)
                if m:
                    line = m.group(1) + str(val) + '\n'
                    wind_replaced.add(key)
                    break

        result.append(line)
    if in_sim:
        _flush_sim_missing()                # simulation: was the file's last block
    if in_wind:
        _flush_wind_missing()               # winding: was the file's last block
    if set(wind_updates) - wind_replaced:
        # No winding: block at all — append one (newline-terminate the last
        # line first, or the header would glue onto it).
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append("winding:\n")
        _flush_wind_missing()

    missing = set(updates) - replaced - appended
    if missing:
        # only possible when the file has NO simulation: block at all — that is
        # a broken config, not a bad request
        raise HTTPException(
            status_code=422,
            detail=f"motor_config.yaml has no simulation: block to carry {sorted(missing)}"
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
    # …but ONLY when a value actually changed.  A panel that re-saves the same
    # numbers (mount adoption, a debounced write of an unchanged slider) must
    # not throw away the field the user just waited ten minutes for.
    def _same(a, b) -> bool:
        try:
            if isinstance(a, (int, float)) and not isinstance(a, bool) \
                    and isinstance(b, (int, float)) and not isinstance(b, bool):
                return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
            if isinstance(a, str) and isinstance(b, str):
                return a.strip() == b.strip()
            if a is None and isinstance(b, str) and b.strip().lower() in ("", "auto"):
                return True
            return a == b
        except Exception:  # noqa: BLE001
            return False
    _changed = sorted(k for k, v in updates.items() if not _same(_before_sim.get(k), v))
    if _changed:
        try:
            clear_simulation_caches(reason="simulation config patched (%s)"
                                    % ", ".join(_changed[:8]))
        except Exception:  # noqa: BLE001
            log.warning("simulation caches were not flushed after a config patch",
                        exc_info=True)
    else:
        log.info("simulation config re-saved with no change (%s) — caches kept",
                 ", ".join(sorted(updates)[:8]))
    return {"status": "ok", "updated": updates, "changed": _changed}


# ─────────────────────────────────────────────────────────────────────────────
# 7b.  FEM mesh builder — returns triangle mesh for visualisation only
# ─────────────────────────────────────────────────────────────────────────────

#: Stage 3: per workspace, bounded.  A mesh payload is one full triangulation;
#: eight of them is a few tens of MB and covers the mesh-size slider's whole
#: range for one machine, which is what this cache is for.
_FEM_MESH_CACHE_MAX = 8
_fem_mesh_cache = _WSP.ws_map("simulation.fem_mesh", _FEM_MESH_CACHE_MAX,
                              lru_on_read=True)


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

#: Stage 3: per workspace, bounded — the two-mesh sliding-band sibling of
#: ``_fem_mesh_cache``, same size class, same cap.
_FEM_MESH_SB_CACHE_MAX = 8
_fem_mesh_sb_cache = _WSP.ws_map("simulation.fem_mesh_sb",
                                 _FEM_MESH_SB_CACHE_MAX, lru_on_read=True)


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

#: Stage 3: per workspace, bounded.  A field entry carries A_z per node and B
#: per element for one frame; eight is the field viewer's working set (the
#: quantity toggles and the ± frame steps around one run) and the Run path
#: empties it wholesale anyway (``_refresh_caches_for_run``).
_FEM_FIELD_CACHE_MAX = 8
_fem_field_cache = _WSP.ws_map("simulation.fem_field", _FEM_FIELD_CACHE_MAX,
                               lru_on_read=True)


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
_TRANSIENT_SNAP_MAX = 3
#: Stage 3: per workspace.  The cap IS ``_TRANSIENT_SNAP_MAX`` — the number the
#: store site already trims to — so the bound and the trim cannot disagree.
#: ``lru_on_read`` stays off: ``next(reversed(...))`` here means "the newest
#: run", and a lookup must not make an older snapshot look newest.
#: WARMED from ``.last_transient_field.pkl`` on first touch, so a workspace that
#: has not been asked for anything since the restart still opens its field view.
_transient_field_snap = _WSP.ws_map(
    "simulation.transient_field_snap", _TRANSIENT_SNAP_MAX,
    warm=lambda _s: _warm_transient_field_snap(_s))


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
        # Per-part accounting states ride this same payload, and an `excluded`
        # part is solved as AIR — a different machine.  Normalising it away
        # (the default `return None` below) would hand a run with the shaft
        # removed the snapshot of the run that still had it, which is exactly
        # the class of silent mix-up `_mat_ov_for_key` exists to prevent.
        from motor_ai_sim.part_states import (normalize_states as _nps,
                                              config_part_states as _cps,
                                              INCLUDED as _INC)
        _cfg_parts = _cps()
        for _k, _v in _nps(mat_ov.get("parts")).items():
            if str(_cfg_parts.get(_k) or _INC) != str(_v):
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


#: Parts whose MATERIAL never enters the electromagnetic solve: the insulation
#: and the wire enamel are insulators the field does not see (their THICKNESS
#: is geometry, which the fingerprint keeps), and the air regions are air by
#: construction.  Swapping the liner card (Nomex → Al2O3, 2026-09-07) changes
#: the thermal answer only, so it must not invalidate the EM snapshot the
#: Thermal tab replays — nor the EM caches.
_EM_INERT_PARTS = ("slot_insulation", "wire_insulation", "air_gap", "in_band", "out_band")


def _em_materials(m):
    """The materials assignment minus the EM-inert parts (see _EM_INERT_PARTS)."""
    if not isinstance(m, dict):
        return m
    return {k: v for k, v in m.items() if k not in _EM_INERT_PARTS}


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
              "m": _em_materials(_cfg.get("materials")), "mag": _cfg.get("magnet"),
              # Per-part accounting state: `excluded` changes the FIELD (the
              # part is solved as air), so two states of the same machine must
              # never share a cache entry.  `reference` changes only the
              # accounting, but it changes the summary, which is cached with
              # the solve — so it belongs in the same hash.
              "parts": _cfg.get("parts")}
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
            # NORMALISED, not verbatim: an override that names exactly the
            # config's own materials and states is the same machine as no
            # override at all, and the two used to hash differently.  Measured
            # 2026-09-04: the field view's after-reload cache probe went out
            # before the page had its `mat=` ready, so it could never find the
            # picture the same page had solved with `mat=` nine minutes earlier.
            # `_mat_ov_for_key` keeps a genuinely different override distinct.
            _d["req_mat"] = _mat_ov_for_key(_grm())
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
            # Resolve the override into the machine it PRODUCES instead of
            # hashing the raw dict: a kernel run sends the on-screen machine
            # as an explicit `geo` payload, and the raw-dict hash gave the
            # SAME motor a different fingerprint than the no-override path —
            # so its Stage-A passport (keyed by the no-override print) was
            # never found (measured live: end3d null on every kernel run).
            # ov==live resolves to glive → identical hash to no-override,
            # keeping every already-issued fingerprint key valid; a genuine
            # candidate eval resolves to a different machine → different hash.
            try:
                from motor_ai_sim.simulation.geometry_2d import (
                    merge_geo_override as _mgo)
                if _d["glive"] is None:
                    raise ValueError("no live geometry to resolve against")
                _merged = _mgo(dict(_d["glive"]), dict(geo_override))
                # Dict equality, NOT serialized-text equality: the client
                # sends 13 where the live object holds 13.0 — Python calls
                # them equal, json.dumps does not, and the hash is of the
                # text (measured live: the same machine fingerprinted
                # aa4dd7… via override vs 147609… without).  When the
                # resolved machine IS the live one, hash the live dict
                # itself so the print is bit-identical to the no-override
                # path.
                if _merged != _d["glive"]:
                    _d["glive"] = _merged
            except Exception:
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


def _effective_bonding(v: Optional[str] = None) -> Optional[str]:
    """"transposed" | "series" | "parallel", or None to let the GEOMETRY decide.

    There is no default here on purpose.  How the wires in hand are joined
    follows from how many there are, and `wire_parallel` lives in the geometry
    — so an unset value is passed through as None and the solver derives it
    (k > 1 ⇒ soldered at the ends).  Naming one here would put the answer in
    two places and let them disagree.

    An explicit value still wins: the transposed and per-turn-parallel bounds
    have to stay askable, they are what make the middle number mean something.
    """
    _v = str(v if v is not None else "").strip().lower()
    if not _v:
        from motor_ai_sim.config import get_config as _gc
        try:
            _v = str(((_gc().get("simulation", {}) or {})
                      .get("strand_bonding") or "")).strip().lower()
        except Exception:
            _v = ""
    if _v.startswith(("ser", "sold")):
        return "series"
    if _v.startswith("par"):
        return "parallel"
    if _v.startswith("tr"):
        return "transposed"
    return None


def _effective_star_delta(v: Optional[str] = None) -> str:
    """"star" | "delta" — the argument if given, else the saved operating point.

    Mirrors `_effective_rpm` / `_effective_winding`: an explicit argument wins,
    the shared config answers otherwise, and anything unreadable is STAR — the
    connection every one of this solver's numbers meant before the choice
    existed, so a config written by an older build keeps its meaning.
    """
    _v = str(v if v is not None else "").strip().lower()
    if not _v:
        from motor_ai_sim.config import get_config as _gc
        try:
            _c = _gc()
            # The WINDING block is the home (it is a property of the winding,
            # saved with the machine like `connection`); the simulation:
            # mirror is read for configs written before it moved.
            _v = str(((_c.get("winding", {}) or {}).get("star_delta")
                      or (_c.get("simulation", {}) or {}).get("star_delta")
                      or "")).strip().lower()
        except Exception:
            _v = ""
    return "delta" if _v.startswith("d") else "star"


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
                           n_parallel=None, connection=None,
                           excitation="", magnet_temp_c=None) -> "OrderedDict":
    """The snapshot key as NAMED fields, in key order.

    Named because the key is the thing that decides "is this the run the user
    just did?", and when it says no the only useful answer is WHICH field
    disagreed — a bare 26-tuple diff is unreadable, and the two bugs found here
    (geo, materials) both hid behind exactly that unreadability.
    """
    _kf = OrderedDict((
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
        # The source's own parameters, as ONE opaque string: the DC bus and the
        # carrier for the PWM inverter, the waveform hash for an imposed
        # current.  Empty on the two sinusoidal drives, so their keys are
        # byte-identical to what they were before this field existed and no
        # stored snapshot is orphaned.  Without it, a 4 kHz and a 48 kHz run
        # would hand each other's field to the J⟳ / Loss views.
        ("excitation", str(excitation or "")),
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
    # MAGNET TEMPERATURE.  The card's Br and its whole demagnetisation curve
    # move with it, so a snapshot solved with a 163 °C magnet is a different
    # field — and a different loss map — from the same request at 150 °C.
    # APPENDED ONLY WHEN ASKED FOR, the same way the battery block is: with no
    # magnet temperature the key is byte-identical to the one this function has
    # always built, so every snapshot already on disk — the one the field views
    # look up after a restart — still matches instead of being orphaned by a
    # field that carries no information for it.
    if magnet_temp_c is not None:
        _kf["magnet_temp_c"] = round(float(magnet_temp_c), 2)
    return _kf


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
        # An EMPTY store is not proof that nothing was solved: a benign cache
        # flush (a settings re-save, a material tooltip PATCH) drops the memory
        # copy while the last run still sits on disk.  Reload it and let the
        # same-machine checks below decide — a snapshot of another motor is
        # still refused exactly as before (user 2026-09-07: "field not solved
        # for this machine" one minute after the run finished).
        if not _transient_field_snap:
            _load_last_transient_field_snapshot()
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
                "demag_report",
                # The end-winding factor this run was BILLED at (2026-09-09).
                # Not a display number: the open-frame thermal model puts the end
                # turns in the airflow and their LENGTH is (k_end − 1)·L/2 per
                # side, so it has to be the run's own k_end — auto-estimated or
                # typed on the Electromagnetic tab — and not a second estimate
                # made in the thermal route.  Absent from every snapshot written
                # before this line, which is why the thermal side falls back to
                # the same geometry estimator the solver uses for 0 = auto.
                "end_winding_factor")},
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
        # PERSIST the newest snapshot beside .last_transient.json, so a backend
        # restart (or a machine reboot — 2026-09-05) does not leave every field
        # view blank until the user re-solves what the run already solved.
        # Pickle: the entry holds numpy arrays; written off-thread, atomically.
        import threading as _thr
        _entry_copy = _transient_field_snap[key]
        def _persist():
            try:
                import pickle as _pk
                p = _transient_field_store_path()
                with open(p + ".tmp", "wb") as fh:
                    _pk.dump({"key": list(key), "entry": _entry_copy}, fh,
                             protocol=_pk.HIGHEST_PROTOCOL)
                _os_t.replace(p + ".tmp", p)
            except Exception as _pe:      # noqa: BLE001
                log.warning("could not persist transient field snapshot: %s", _pe)
        # Stage 3: the path is resolved inside the thread, and a fresh thread
        # has an empty context — carry this call's workspace across.
        _thr.Thread(target=_WSP.bind(_persist), daemon=True).start()
    except Exception as _e:      # never let a viewer convenience break a solve
        log.warning("could not store transient field snapshot: %s", _e)


def _transient_field_store_path() -> str:
    return _transient_store_path().replace(".last_transient.json",
                                           ".last_transient_field.pkl")


def _read_transient_field_blob():
    """``(key, entry)`` off ``.last_transient_field.pkl``, or ``None``.

    Split out of the loader below (Stage 3) so a per-workspace store can be
    WARMED by writing straight into the container it was handed, without going
    back through the module-level proxy that is in the middle of creating it.
    """
    import pickle as _pk
    p = _transient_field_store_path()
    if not _os_t.path.exists(p):
        return None
    with open(p, "rb") as fh:
        blob = _pk.load(fh)
    def _retuple(x):
        return tuple(_retuple(i) for i in x) if isinstance(x, list) else x
    return _retuple(blob["key"]), blob["entry"]


def _warm_transient_field_snap(store) -> None:
    """First touch of a workspace's snapshot store: fill it from that
    workspace's own pickle.  Quiet — the loader below does the logging."""
    try:
        got = _read_transient_field_blob()
    except Exception as _e:      # noqa: BLE001
        log.debug("snapshot warm skipped: %s", _e)
        return
    if got is not None:
        store[got[0]] = got[1]


def _load_last_transient_field_snapshot() -> None:
    """Repopulate the snapshot store from disk at startup (see the persist
    above).  Served only through the same-machine checks every lookup applies,
    so a snapshot of yesterday's motor never paints today's.

    Still called by hand wherever the store was emptied and the view would
    otherwise be blank (``clear_simulation_caches`` is followed by exactly that
    question), so it deliberately has no "already loaded" guard: a re-read after
    a clear is the whole point.
    """
    try:
        got = _read_transient_field_blob()
        if got is None:
            return
        _key, _entry = got
        _transient_field_snap[_key] = _entry
        _transient_field_snap.move_to_end(_key)
        log.info("restored the last run's field snapshot from %s (computed %s)",
                 _transient_field_store_path(),
                 (_entry.get("meta") or {}).get("computed_at"))
    except Exception as _e:      # noqa: BLE001
        log.warning("could not restore the transient field snapshot: %s", _e)


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
        DOM_MAG_N, DOM_MAG_S, DOM_ROTOR, DOM_SHAFT, DOM_SLEEVE, DOM_STATOR)
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
            ("shaft",   t == DOM_SHAFT),
            ("sleeve",  t == DOM_SLEEVE)):
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


def _field2d_cache_key(
    *,
    rotor_angle_deg:     float = 0.0,
    gamma_deg:           float = 0.0,
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    outer_air_factor:    float = 1.3,
    n_sectors:           int   = 4,
    stator_fillet_mm:    float = 0.0,
    I_phase_rms:         Optional[float] = None,
    component_mesh:      str   = "",
    demag:               bool  = False,
    pole_copy:           bool  = False,
    iron_template:       bool  = True,
    geo_mesh:            bool  = True,
    structured_gap:      bool  = True,
    airgap_macro:        bool  = False,
    gap_layers:          float = 2.0,
    geo:                 Optional[str] = None,
    n_steps_per_period:  int   = 0,
    n_periods:           float = 1.0,
    eddy:                bool  = False,
    rotor_eddy:          bool  = False,
    coil_temp_c:         float = 120.0,
    magnet_temp_c: Optional[float] = None,
    use_transient_snapshot: bool = True,
    latest_run_field:    bool  = True,
    snap_drive:          str   = "current",
    snap_excitation:     str   = "",
    **_ignored,
) -> tuple:
    """THE cache key of a field-view request — one definition, two readers.

    `get_fem_field2d` (the queue in front) needs it BEFORE the solve body runs,
    to recognise a concurrent twin; `_fem_field2d_impl` needs it to read and
    fill `_fem_field_cache`.  Two copies of a 25-field key would drift on the
    first parameter anyone adds — and a key that drifts is a cache that serves
    one machine's picture for another, which is the bug class this file has
    already paid for twice (see `_geo_ov_for_key`).

    Accepts the whole request kwargs dict (`**_ignored` swallows the parameters
    the key does not depend on, e.g. `snapshot_only`, which selects HOW the
    payload is obtained, not WHAT is computed).
    """
    _comp_mesh = _parse_component_mesh(component_mesh)
    _geo_ov = _parse_geo_override(geo)
    # The same P2 coercion the solve body applies (and logs) — the key must
    # describe what will actually be solved, not what was asked for.
    if airgap_macro or not structured_gap:
        structured_gap = True
        airgap_macro = False
    _cfp_f = _config_physics_fingerprint(with_request_materials=True)
    key = (
        "sbfield", round(rotor_angle_deg * 2) / 2, round(gamma_deg, 1),
        round(mesh_size_mm, 2), round(min_size_mm, 2), round(outer_air_factor, 2),
        int(n_sectors), round(stator_fillet_mm, 2),
        round(I_phase_rms, 2) if I_phase_rms is not None else None,
        int(bool(demag)), int(bool(pole_copy)), int(bool(iron_template)),
        tuple(sorted(_comp_mesh.items())),
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
    # Magnet temperature: appended ONLY when asked for, so a request without one
    # keeps the byte-identical key it has always had.  It must be in the key at
    # all because `**_ignored` would otherwise swallow it and hand a 163 °C view
    # the picture solved with the card's own 150 °C magnet — a weaker field
    # served as if it were the one requested.
    if magnet_temp_c is not None:
        key = key + (round(float(magnet_temp_c), 2),)
    # WHICH EXCITATION's snapshot this request is looking for.  Appended ONLY
    # when it is not the sinusoidal current drive every caller before the PWM
    # coupled loop asked for, so every key already in the cache is byte-identical
    # to the one it has always had.  It must be in the key at all because two
    # requests that differ only here are looking at DIFFERENT runs of the same
    # machine — a 24 kHz PWM map and the sine map at the same point.
    if str(snap_drive or "current") != "current" or str(snap_excitation or ""):
        key = key + (str(snap_drive or "current"), str(snap_excitation or ""))
    return key


def _field2d_job_kind(n_steps_per_period: int, demag: bool, eddy: bool) -> str:
    """Short human label for the busy list ("24-frame eddy map")."""
    n = int(n_steps_per_period or 0)
    if n > 1:
        return "%d-frame %s map" % (n, "eddy" if eddy else "loss")
    return "8-frame demag probe" if demag else "single-frame field"


@router.get("/physics/fem_field2d")
def get_fem_field2d(
    rotor_angle_deg:     float = 0.0,
    gamma_deg:           float = 0.0,
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    outer_air_factor:    float = 1.3,
    motion_band:         bool  = True,
    band_thickness_mm:   float = 0.4,
    n_sectors:           int   = 4,
    stator_fillet_mm:    float = 0.0,
    I_phase_rms:         Optional[float] = None,
    component_mesh:      str   = "",
    demag:               bool  = False,
    pole_copy:           bool  = False,
    iron_template:       bool  = True,
    geo_mesh:            bool  = True,
    structured_gap:      bool  = True,
    airgap_macro:        bool  = False,
    gap_layers:          float = 2.0,
    geo:                 Optional[str] = None,
    n_steps_per_period:  int   = 0,
    n_periods:           float = 1.0,
    eddy:                bool  = False,
    rotor_eddy:          bool  = False,
    coil_temp_c:         float = 120.0,
    magnet_temp_c: Optional[float] = None,
    use_transient_snapshot: bool = True,
    snapshot_only:       bool  = False,
    latest_run_field:    bool  = True,
    # WHICH EXCITATION's snapshot to look for (2026-09-14, the PWM coupled
    # loop).  The snapshot key carries the drive and the source's own parameters
    # (`_field_snap_key_fields`), and this view has always probed for the
    # sinusoidal CURRENT run because that is the only kind anything asked it
    # for.  A PWM coupled iteration needs the loss map of the run it just made,
    # which lives under drive='pwm_voltage' and excitation='<v_bus>/<f_switch>';
    # without these two the exact key can never hit and the only way in would be
    # the RELAXED same-machine fallback, i.e. "some run of this motor" — which
    # is exactly the wrong map to build a temperature field from.
    snap_drive:          str   = "current",
    snap_excitation:     str   = "",
    progress_cb=None,
):
    """The field view — QUEUED.  Parameters are documented on the body below
    (`_fem_field2d_impl`), which is the function this one calls.

    `progress_cb` (2026-09-07) is a pure PASS-THROUGH to the solver's per-frame
    callback — the shared contract in `motor_ai_sim.progress`, i.e.
    `(done, total, phase, composition)`.  It exists for the thermal router,
    whose /field is a full multi-frame EM solve followed by a conduction solve:
    without it the Thermal tab's progress bar had nothing to say for the 90 % of
    the wait that IS this call.  It is deliberately NOT part of `_p` below, so
    it can never reach the cache key — two requests that differ only in who is
    watching are the same solve.

    Everything here is about WHEN the solve runs, never about what it computes:

    * a cache hit answers straight away (as it always did);
    * a `snapshot_only` PROBE goes straight through — it never solves, so it
      must never wait behind something that does (the Loss / J⟳ views probe on
      every open, and a probe that blocked for six minutes would be worse than
      the disease);
    * anything else goes through the field-solve queue: a concurrent request
      with the SAME cache key waits for the one already solving instead of
      starting a second identical solve, and at most `SB_FIELD_MAX_CONCURRENT`
      (default 2) solves run at once, the rest FIFO.

    Measured 2026-09-03: 14 automatic field solves on one machine, two of them
    byte-identical and simultaneous, ~17 cores busy with nothing on screen
    saying so.  The frontend no longer starts these by itself, and this is the
    other half — the server refusing to run the same solve twice.
    """
    _p = dict(
        rotor_angle_deg=rotor_angle_deg, gamma_deg=gamma_deg,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, motion_band=motion_band,
        band_thickness_mm=band_thickness_mm, n_sectors=n_sectors,
        stator_fillet_mm=stator_fillet_mm, I_phase_rms=I_phase_rms,
        component_mesh=component_mesh, demag=demag, pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh,
        structured_gap=structured_gap, airgap_macro=airgap_macro,
        gap_layers=gap_layers, geo=geo,
        n_steps_per_period=n_steps_per_period, n_periods=n_periods,
        eddy=eddy, rotor_eddy=rotor_eddy, coil_temp_c=coil_temp_c,
        magnet_temp_c=magnet_temp_c,
        use_transient_snapshot=use_transient_snapshot,
        snapshot_only=snapshot_only, latest_run_field=latest_run_field,
        snap_drive=snap_drive, snap_excitation=snap_excitation,
    )
    key = _field2d_cache_key(**_p)
    _hit = _fem_field_cache.get(key)
    if _hit is not None:
        return _hit
    if snapshot_only:
        return _fem_field2d_impl(**_p, _key=key, _progress_cb=progress_cb)
    return run_field_job(
        key, _field2d_job_kind(n_steps_per_period, demag, eddy),
        lambda: _fem_field2d_impl(**_p, _key=key, _progress_cb=progress_cb))


def _fem_field2d_impl(
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
    magnet_temp_c: Optional[float] = None,  # MAGNET temperature [°C]; None = the assigned
                                          # card exactly as the library quotes it.  It is in
                                          # the snapshot key too, so a view at one magnet
                                          # temperature is never answered with the field
                                          # solved at another.
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
    snap_drive:          str   = "current",  # WHICH excitation's snapshot to probe for
    snap_excitation:     str   = "",          # …and that source's own key string
                                          # ("<v_bus>/<f_switch>" for PWM).  See the
                                          # note on `get_fem_field2d`.
    *,
    _key:                tuple,           # THE cache key, built by the queue in
                                          # front of this body (see
                                          # `_field2d_cache_key`) so both agree
                                          # on what "the same request" means.
    _progress_cb=None,                    # per-frame progress, passed straight
                                          # to the solver.  Never in the cache
                                          # key: who is watching is not part of
                                          # what is solved.
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
    # THE cache key — fingerprinting what the SOLVE depends on but the URL does
    # not carry (the shared config's geometry / winding / material assignment
    # and this request's per-user material override).  Built by
    # `_field2d_cache_key` and handed in by the queue in front of this body, so
    # "the same request" means one thing to the cache and to the dedupe.
    key = _key
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
    if use_transient_snapshot:
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
            magnet_temp_c=magnet_temp_c,
            drive=str(snap_drive or "current"),
            excitation=str(snap_excitation or ""),
            element_order=2,
            cfg_fingerprint=_config_physics_fingerprint(
                with_request_materials=False),
            geo_ov=_geo_ov, mat_ov=_get_request_materials_safe(),
            rotor_angle0_deg=float(rotor_angle_deg))
        # The exact key only means something for a multi-frame view (the
        # single-angle views pin the rotor; the run's frame is at its end
        # angle) — so only the sweep views look it up.
        if _sweep:
            # AN EMPTY STORE IS NOT PROOF NOTHING WAS SOLVED (2026-09-15).  The
            # snapshot store is in memory and the newest entry is mirrored to
            # disk; after a backend restart the memory copy is empty until
            # something loads it.  `routes.thermal._snapshot_loss_entry` has
            # applied this rule since it was written — this view did not, so a
            # Loss / J⟳ request (and the coupled PWM loop's own exact-key probe)
            # missed a snapshot that was sitting on disk the whole time.
            if not _transient_field_snap:
                _load_last_transient_field_snapshot()
            _snap = _transient_field_snap.get(tuple(_probe_fields.values()))
            if _snap is None:
                _log_snap_key_miss(_probe_fields)
        elif snapshot_only and latest_run_field:
            # A single-angle PROBE — a page reload, a tab switch, a backend
            # restart with the persisted snapshot reloaded.  Nothing is
            # solved behind a probe, so the choice is "the run's final frame,
            # labelled" or a blank view saying "press Re-solve"; the user's
            # verdict on the blank view (2026-09-05: "ну почему ты это до сих
            # пор не исправил?") settles it.  Same-machine check inside.
            _snap, _relaxed_diffs = _latest_run_snapshot(_probe_fields)
            if _snap is not None:
                _relaxed_diffs = list(_relaxed_diffs) + [
                    "single-angle view: showing the run's FINAL frame "
                    "(rotor at the run's end angle, not at %.1f deg)"
                    % float(rotor_angle_deg)]
                log.info("fem_field2d: single-angle probe served the last "
                         "run's final frame (no solve)")
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
                magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
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
                element_order=2,
                progress_cb=_progress_cb)
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
    # J: the requested VIEW picks the quantity — Jeddy for the coupled solve's
    # EDDY density σ(−∂A/∂t + U), Jtri_src for the applied SOURCE density —
    # not merely whether the snapshot happened to come from an eddy run: the
    # solver now writes Jtri_src on every snapshot (eddy or not), so a J⟳
    # request against a non-eddy snapshot still falls back to the source
    # density below rather than reading nothing.  The eddy J is nodal (as it
    # is in the solve) — average it onto elements so both cases hand the
    # renderer the same shape.
    _j_stale = False
    if eddy and fld.get("Jeddy") is not None:
        Jtri = _np.asarray(fld["Jeddy"], float)[T].mean(axis=0)
    elif not eddy and fld.get("Jtri_src") is not None:
        Jtri = _np.asarray(fld["Jtri_src"], float)
    else:
        # A snapshot that predates this fix (an eddy run whose snapshot only
        # ever carried Jeddy) has no source density to show. NaN would say
        # "no data" to the renderer, but this payload leaves the process as
        # JSON through Starlette's JSONResponse (allow_nan=False) — a NaN
        # here is not a blank picture, it is a 500 on every such probe
        # (2026-09-20 review). Zeros are FINITE and ship; `j_view_stale` and
        # the source_label sentence below are what actually tell the user
        # this picture is not data, not the array itself.
        Jtri = _np.zeros(int(T.shape[1]))
        _j_stale = True
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
    if _j_stale:
        # Same header-note mechanism as the relaxed-match sentence above —
        # printed by the SAME renderer, so no web change is needed for it to
        # show up under the field.
        result["source_label"] = (
            str(result["source_label"]) + " — this run predates the "
            "source-J snapshot (Jtri_src); re-run the simulation to get "
            "the \"J\" view for it.")
        result["j_view_stale"] = True
    # A real transient also produces the machine numbers the J⟳ sidebar and the
    # thermal solve read off this payload.  A single frame produces none of them
    # honestly, so they are only added when the frames were actually swept.
    if _sweep:
        def _mean(kk):
            s = d.get(kk) or [0.0]
            return float(_np.mean(_np.asarray(s, float))) if len(s) else 0.0
        # COPPER: the series, always — coupled solve or not (fixed 2026-09-10).
        #
        # `P_cu_total_solve_W` is the coupled solve's own ∫σE² and it is a 2-D
        # integral: it is the loss of the ACTIVE LENGTH and nothing else, because
        # a 2-D slice has no end windings in it.  `P_cu_W` is the series the
        # solver builds for exactly this purpose — the end-winding-corrected DC
        # plus that same coupled AC increment (`fem_solver_2d`, "COPPER SPLIT")
        # — so it is the whole winding.
        #
        # Taking the 2-D integral here dropped k_end from every number this
        # payload feeds, and this payload is what the THERMAL solve reads.  On
        # the live Ø200 that was 2,900.4 W into the thermal model against
        # 3,443.1 W the machine actually makes: 542.7 W — 18.7 % of the copper,
        # 9 % of all losses — never entered the temperature field, and the
        # efficiency computed below was flattered by the same watts.  The user's
        # position on where that heat goes is the physical one: the end turns
        # are cooled through the stator, so they belong in the 2-D thermal.
        #
        # `P_cu_total_solve_W` is still returned, untouched, as the cross-check
        # it was meant to be — two independent routes to the active-length watts.
        Pcu = _mean("P_cu_W")
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
            # UNROUNDED companions (2026-09-07).  The four above are display
            # numbers and 0.1 W is the right resolution to print them at — but
            # the thermal solve builds its copper heat density from `P_cu_W`,
            # i.e. one solver reads another's rounded output.  On a small
            # machine that is not a rounding, it is the whole signal: the 30 mm
            # fixture makes 2.6 W of copper, so an 18 K change of winding
            # temperature (2.6 % of ρ_Cu) moved P_cu by less than half a
            # quantum and the coupled EM↔thermal loop could not see its own
            # feedback at all.  Additive on purpose — every existing reader of
            # the rounded keys keeps the number it has always had.
            "P_cu_exact_W": float(Pcu), "P_fe_exact_W": float(Pfe),
            "P_mag_eddy_exact_W": float(Pmag),
            "P_loss_total_exact_W": float(ploss),
            "P_mech_W": round(Pmech, 1), "efficiency": round(eff, 4),
            "P_cu_ac_solve_W": round(float(d.get("P_cu_ac_solve_W", 0.0)), 1),
            # Un-rounded, for the same reason as the four above — and this one
            # in particular, because the AC (proximity/skin) share is what
            # decides HOW copper loss moves with winding temperature: the DC
            # term goes as ρ(T), the solved eddy term as σ(T) = 1/ρ(T), so the
            # two partly cancel.  A consumer that cannot see the split can only
            # scale the total the wrong way.
            "P_cu_ac_exact_W": float(d.get("P_cu_ac_solve_W", 0.0) or 0.0),
            "V_peak": round(float(d.get("V_peak", 0.0)), 1),
            # The end-winding factor the run was billed at — the copper loss
            # above already contains it.  Carried so a consumer that needs the
            # end turns THEMSELVES (the open-frame thermal model: their length is
            # (k_end − 1)·L/2 per side) uses the run's own number instead of
            # estimating a second one.  0.0 on a snapshot written before it was
            # stored, which the reader treats as "not recorded".
            "end_winding_factor": float(d.get("end_winding_factor", 0.0) or 0.0),
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


# ─────────────────────────────────────────────────────────────────────────────
# 7d. FEM Transient — N steps per electrical period
# ─────────────────────────────────────────────────────────────────────────────

#: Stage 3: per workspace, bounded, WARMED from ``.last_transient.json``.
#: CAP 4: the Run path keeps exactly one entry (``_refresh_caches_for_run``
#: clears and re-seeds it), so four is head-room for the restore plus a couple
#: of re-runs, and a whole transient result is the largest object in here.
_FEM_TRANSIENT_CACHE_MAX = 4
_fem_transient_cache = _WSP.ws_map(
    "simulation.fem_transient", _FEM_TRANSIENT_CACHE_MAX,
    warm=lambda _s: _warm_fem_transient(_s))

# ── A Run ALWAYS solves ──────────────────────────────────────────────────────
# `_fem_transient_cache` used to be consulted by the Run path itself, so a
# second press of Run with the same inputs returned the FIRST run's object —
# same torque, same ripple, same `computed_at` — without touching the solver.
# For an engineer that is not a cache hit, it is a Run that silently did
# nothing (user, 2026-09-04: "у нас с ними постоянно жуткие проблемы, их нужно
# очищать при каждом расчёте и обновлять").  The dict survives only as an
# OPT-IN memo for the iterative loops that re-enter this route many times
# inside ONE request (the bus-coupling fixed point and the charge-max compass
# search: they revisit the same operating point by construction and each
# revisit is a full FEM solve).  Nothing that a human pressed Run for may read
# it — see `_memo_allowed`.
_TRANSIENT_MEMO: "_CtxVar[bool]" = _CtxVar("transient_memo", default=False)


def _memo_allowed() -> bool:
    """May THIS solve be served from `_fem_transient_cache`?

    Only an internal, machine-driven re-entry may: an outer loop that has
    explicitly opted in (`_TRANSIENT_MEMO`), or a background solve
    (`_BACKGROUND_RUN` — a passport sweep point, a search probe), which by
    definition is not a run the user asked for.  Every user-initiated path —
    GET /physics/fem_transient, POST /api/kernel/run, the animation viewer —
    leaves both false and therefore always reaches the solver.
    """
    return bool(_TRANSIENT_MEMO.get() or _BACKGROUND_RUN.get())


# What the last cache refresh / clear was, for GET /api/simulation/caches.
# Stage 3: per workspace — it describes THIS caller's caches, and answering
# "cleared: geometry PUT" about somebody else's edit would be a lie.
_cache_state = _WSP.ws_map(
    "simulation.cache_state", 8,
    seed=lambda: {"last_refreshed_by": None, "last_cleared_reason": None})


def _refresh_caches_for_run(sb_key: tuple, result: Dict,
                            snap_key: Optional[tuple] = None) -> None:
    """REPLACE the physics caches with this run's own state.

    Called from exactly one place — where a finished live-machine run is
    finalised, next to `_save_last_transient` — so no route can forget it.
    After it returns:

      * `_fem_field_cache`   is empty (every field picture in it was drawn for
        an earlier run; the fresh run's own last-frame snapshot serves the
        J⟳ / Loss views without a solve anyway);
      * `_transient_field_snap` holds exactly ONE entry — this run's snapshot
        when the run kept one, otherwise the newest surviving entry, so a
        Run that did not ask for a snapshot (the animation viewer's second
        request) cannot blank the store the previous Run just filled;
      * `_fem_transient_cache` holds exactly this result.

    Coexistence, not staleness, was the bug: the stores are keyed correctly,
    but an older entry sitting beside the new one is a picture of a machine the
    user has moved on from and gets served by any near-miss lookup.
    """
    try:
        _fem_field_cache.clear()
        _keep = snap_key if (snap_key is not None
                             and snap_key in _transient_field_snap) else None
        if _keep is None and _transient_field_snap:
            _keep = next(reversed(_transient_field_snap))
        for _k in [_k for _k in _transient_field_snap if _k != _keep]:
            _transient_field_snap.pop(_k, None)
        _fem_transient_cache.clear()
        _fem_transient_cache[sb_key] = result
        _cache_state["last_refreshed_by"] = result.get("computed_at")
        log.info("physics caches refreshed by run %s: field-cache %d, "
                 "snapshots %d", result.get("computed_at"),
                 len(_fem_field_cache), len(_transient_field_snap))
    except Exception as _e:   # noqa: BLE001 — housekeeping must never kill a run
        log.warning("cache refresh after the run failed: %s", _e)


# The single most-recent transient (key + result), kept so the web UI can RESTORE
# the last simulation on open (?restore=true) — showing it stale-flagged instead
# of recomputing when the requested params don't match.  Updated on every save
# and repopulated from disk at startup.
#: Stage 3: per workspace.  Two named slots, not a cache — the cap is a safety
#: valve.  Seeded so ``_last_transient_ref["key"]`` never raises, and warmed
#: from that workspace's own ``.last_transient.json``.
_last_transient_ref = _WSP.ws_map(
    "simulation.last_transient_ref", 8,
    seed=lambda: {"key": None, "result": None},
    warm=lambda _s: _warm_last_transient_ref(_s))

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
        from motor_ai_sim.workspace import root as _ws_root
        _base = str(_ws_root())
    except Exception:
        _base = _os_t.path.join(_os_t.path.dirname(__file__), "..", "..", "..", "config")
    return _os_t.path.abspath(_os_t.path.join(_base, ".last_transient.json"))

def _json_default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    return float(o)

def _save_last_transient(sb_key: tuple, result: Dict, *,
                         journal: bool = True) -> None:
    """Persist THIS result as "the last simulation" (the ?restore=true store).

    `journal=False` for a result that was LOADED from the results ledger rather
    than solved (2026-09-05): the restore store must follow what is on screen —
    otherwise a reload finds a newer solve on disk than the loaded run and the
    panel silently adopts it (the mount probe's `backNewer` branch), i.e. the
    numbers change under the user without a word.  The run JOURNAL, though, is a
    record of solves the user actually made; a load is not one, and writing it
    there would teach the heuristics that the same design was tried twice.
    """
    try:
        # NEVER persist or re-serve the per-frame field data.  `frames` is the
        # animation payload — one full mesh + A_z per frame — and it belongs to
        # the request that asked for it, once.  Left in, a 464-step run wrote a
        # 790 MB .last_transient.json, the post-processing serialisation choked
        # the event loop for minutes (the whole API read as hung), and every
        # later RESTORE shipped 725 MB of JSON that no browser could parse —
        # the user's card silently fell back to a stale localStorage summary
        # and showed the previous run's numbers (measured live 2026-09-01:
        # "обновил страницу, так 0 и стоит" over a solved P_solid = 5.1 W).
        result = {k: v for k, v in result.items() if k != "frames"}
        tmp = _transient_store_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump({"key": list(sb_key), "result": result}, fh,
                       default=_json_default)
        _os_t.replace(tmp, _transient_store_path())   # atomic
        _last_transient_ref["key"] = tuple(sb_key)
        _last_transient_ref["result"] = result
        if journal:
            _append_run_journal(result)
    except Exception as _e:
        log.warning("could not persist last transient: %s", _e)


def _append_run_journal(result: Dict) -> None:
    """One line per finished LIVE-machine run: logs/run_journal.jsonl.

    The user optimises machines by hand — a geometry edit, a run, a look at
    the numbers, the next edit — and asked (2026-09-04) that this be WATCHED
    and learned from, because the automatic optimizer does not find what the
    hand finds.  The transient store keeps only the last run; the geometry
    audit keeps only the edits.  This journal keeps the pairing: what the
    machine WAS (every geometry key), what it was run AT, what came out.
    Append-only, ~2 kB per run, never read by the app itself — a passive
    record for the orchestrator's summaries (scratchpad/journal_summary.py).
    """
    try:
        import time as _tj
        from pathlib import Path as _P
        from motor_ai_sim.config import get_config as _gc
        from motor_ai_sim.workspace import root as _ws_root
        s = result.get("summary") or {}
        cfg = _gc()
        geo = dict(cfg.get("geometry") or {})
        mats = dict(cfg.get("materials") or {})
        ctx = {}
        try:
            ctx = _json.loads((_ws_root() / ".family_context.json").read_text(encoding="utf-8"))
        except Exception:   # noqa: BLE001
            ctx = {}
        def _num(v):
            try:
                return round(float(v), 6)
            except Exception:   # noqa: BLE001
                return None
        _mkeys = ("T_em_avg_Nm", "P_mech_W", "efficiency", "T_ripple_pct", "P_loss_total_W",
                  "P_stranded_W", "P_core_W", "P_solid_W", "mass_total_kg",
                  "torque_per_mass_Nm_kg", "power_per_mass_W_kg", "J_coil_A_per_mm2",
                  "V_line_peak_V", "V_phase_peak_V", "KV_rpm_per_V_line",
                  "KV_noload_rpm_per_V_line", "Kt_Nm_per_Arms", "Ld_mH", "Lq_mH",
                  "psi_pm_Wb", "B_gap_mean_T",
                  "THD_pct", "THD_I_pct", "slot_fill_pct", "R_phase_ohm",
                  "I_phase_rms_A", "I_phase_rms_solved_A", "rpm", "gamma_deg",
                  "n_steps_per_period", "turns_per_coil", "wire_parallel",
                  "wire_split", "n_parallel")
        line = {
            "ts": _tj.strftime("%Y-%m-%dT%H:%M:%S"),
            "computed_at": result.get("computed_at"),
            "geo_fingerprint": result.get("geo_fingerprint"),
            "context": {k: ctx.get(k) for k in ("die", "config", "duty")},
            "geometry": {k: (_num(v) if isinstance(v, (int, float)) else v)
                         for k, v in geo.items() if not isinstance(v, (dict, list))},
            "materials": {k: mats.get(k) for k in ("magnet", "stator_core", "rotor_core", "sleeve")
                          if mats.get(k)},
            "op": {"drive": result.get("drive"), "mode": s.get("op_mode"),
                   "connection": s.get("connection"), "coil_temp_C": s.get("coil_temp_C"),
                   "daxis_deg": _num(result.get("daxis_deg")),
                   "daxis_source": result.get("daxis_source"),
                   "demag": result.get("demag"), "eddy": result.get("eddy_coupled")},
            "metrics": {k: _num(s.get(k)) for k in _mkeys if s.get(k) is not None},
            "end3d_k": _num((s.get("end3d") or {}).get("k_flux")) if isinstance(s.get("end3d"), dict) else None,
            "n_frames_solved": result.get("n_frames_solved"),
            "wall_s": _num(result.get("wall_s") or result.get("elapsed_s")),
        }
        # The run journal stays PROCESS-global (it sits in the deployment's
        # `logs/`, not in any workspace): it is the optimizer's learning record
        # for this installation, not one user's store.
        from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _DCP
        p = _P(_DCP).parent.parent / "logs" / "run_journal.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(line, ensure_ascii=False, default=_json_default) + "\n")
    except Exception as _e:   # noqa: BLE001 — a journal must never touch a run
        log.debug("run journal skipped: %s", _e)

def _read_last_transient_blob():
    """``(key, result)`` off ``.last_transient.json``, or ``None``.

    Split out for the same reason as ``_read_transient_field_blob``: a
    per-workspace store is warmed by writing into the container directly.
    """
    p = _transient_store_path()
    if not _os_t.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        blob = _json.load(fh)
    def _retuple(x):    # JSON turns tuples into lists — restore for hashing
        return tuple(_retuple(i) for i in x) if isinstance(x, list) else x
    return _retuple(blob["key"]), blob["result"]


def _warm_fem_transient(store) -> None:
    """First touch of a workspace's transient cache: seed it with that
    workspace's own last run."""
    try:
        got = _read_last_transient_blob()
    except Exception as _e:      # noqa: BLE001
        log.debug("transient warm skipped: %s", _e)
        return
    if got is not None:
        store[got[0]] = got[1]


def _warm_last_transient_ref(store) -> None:
    """…and the ``?restore=true`` reference that points at it."""
    try:
        got = _read_last_transient_blob()
    except Exception as _e:      # noqa: BLE001
        log.debug("transient ref warm skipped: %s", _e)
        return
    if got is not None:
        store["key"], store["result"] = got[0], got[1]


def _load_last_transient_into_cache() -> None:
    try:
        got = _read_last_transient_blob()
        if got is None:
            return
        _key, _res = got
        _fem_transient_cache[_key] = _res
        _last_transient_ref["key"] = _key
        _last_transient_ref["result"] = _res
        log.info("restored last transient from %s", _transient_store_path())
    except Exception as _e:
        log.warning("could not restore last transient: %s", _e)


_load_last_transient_into_cache()   # repopulate the cache at import (startup)
_load_last_transient_field_snapshot()   # …and the run's field for the views


# ═════════════════════════════════════════════════════════════════════════════
#  RESULTS LEDGER — "have I already computed exactly this?"
# ═════════════════════════════════════════════════════════════════════════════
# User, 2026-09-05: «Не надо Recent runs — нужно просто сканировать результаты:
# не совпадают ли они с уже проведёнными, хотя бы пока по этим параметрам.»
# He had set 667.4 A peak in the morning, computed, changed the current,
# computed again, came back to 667.4 A with everything else untouched — and had
# to sit through a solve whose answer was already on this disk.
#
# This does NOT reopen the staleness hole the 2026-09-04 rework closed ("a Run
# ALWAYS solves", `_memo_allowed`).  The two rules meet on three conditions,
# all of them required:
#   * EXACT key.  A ledger hit is `_sb_key == stored key` — the very tuple the
#     solve is cached under, geometry+materials fingerprint and all.  Nothing
#     looser: no "close enough", no per-field tolerance.
#   * LABELLED.  The result carries `ledger_hit` / `ledger_computed_at` and the
#     card says "result from 19:28 — identical parameters", so the engineer is
#     never told a solve happened when none did — which was the whole complaint
#     behind the in-memory memo.
#   * ONE CLICK OUT.  `fresh=true` (the card's "Recompute") always solves and
#     replaces the entry.
#
# On disk, not in memory, because "this morning's run" must survive the API
# restart that stands between morning and afternoon.
_LEDGER_DIRNAME = ".run_ledger"
#: Newest N kept per geometry fingerprint.  40 ≈ a full day of hand
#: optimisation at one machine (the journal shows 20-40 runs on a working day),
#: so "the current I had this morning" is always still there while a machine
#: the user left behind two weeks ago cannot hold the disk hostage.
_LEDGER_KEEP_PER_GEO = 40
#: Total cap.  A stripped transient is ~40-200 kB gzipped (the per-step series;
#: frames never enter), so 200 MB is thousands of runs — the cap exists to bound
#: a pathological 464-step PWM series, not to ration ordinary work.
_LEDGER_MAX_BYTES = 200 * 1024 * 1024


def _ledger_dir():
    """`config/.run_ledger/` — beside `.last_transient.json`, so a test sandbox
    (MOTOR_AI_SIM_CONFIG) gets its own ledger for free."""
    from pathlib import Path as _P
    return _P(_transient_store_path()).parent / _LEDGER_DIRNAME


def _ledger_key_hash(sb_key: tuple) -> str:
    """Filename for a key.  md5 of the key's JSON rendering — the same
    `default=str` rendering the key is persisted with, so a value that only
    `str()` can express (a numpy scalar sneaking in) hashes stably instead of
    raising."""
    import hashlib as _hl
    return _hl.md5(_json.dumps(_retuple_to_list(sb_key), sort_keys=False,
                               default=str).encode()).hexdigest()


def _retuple_to_list(x):
    """tuples → lists, recursively (JSON has no tuple)."""
    if isinstance(x, (tuple, list)):
        return [_retuple_to_list(i) for i in x]
    return x


def _retuple_from_list(x):
    """The inverse, for comparing a loaded key against a live one."""
    if isinstance(x, list):
        return tuple(_retuple_from_list(i) for i in x)
    return x


def _ledger_mat_signature() -> str:
    """Which MATERIALS the run was solved with, in words.

    `_config_physics_fingerprint` already folds materials into the key, so this
    changes no decision — it is here so that a human (or a support session)
    reading a ledger file can see *what* the entry describes without replaying
    a 16-character hash.  Same reason the mass rows name the solved steel.
    """
    try:
        from motor_ai_sim.config import get_material_assignments as _gma
        _assign = dict(_gma() or {})
    except Exception:       # noqa: BLE001
        _assign = {}
    try:
        _ov = (_get_request_materials_safe() or {}).get("assignment") or {}
        _assign.update({str(k): str(v) for k, v in _ov.items()})
    except Exception:       # noqa: BLE001
        pass
    return ";".join("%s=%s" % (k, _assign[k]) for k in sorted(_assign))


def _ledger_path_for(sb_key: tuple, geo_fp: Optional[str]):
    """`<geometry fingerprint>-<key hash>.json.gz`.

    The machine's print is in the FILENAME so the ring buffer can group and trim
    by geometry without opening (and gunzipping) every entry it owns — that
    housekeeping runs after every single solve.
    """
    return _ledger_dir() / ("%s-%s.json.gz"
                            % (str(geo_fp or "nogeo"), _ledger_key_hash(sb_key)))


def _ledger_lookup(sb_key: tuple) -> Optional[Dict]:
    """The stored result for EXACTLY this key, or None.

    The key is re-checked against the file's own copy after loading: a hash
    collision, or a key whose JSON rendering changed under a code edit, must
    read as "no result", never as somebody else's numbers.
    """
    import gzip as _gz
    try:
        _d = _ledger_dir()
        if not _d.exists():
            return None
        # By hash, whatever machine prefix it was written under — the key check
        # below is the authority, the prefix is only there for the trim.
        for p in sorted(_d.glob("*-%s.json.gz" % _ledger_key_hash(sb_key))):
            with _gz.open(p, "rt", encoding="utf-8") as fh:
                blob = _json.load(fh)
            if _retuple_from_list(blob.get("key")) == tuple(sb_key):
                return blob
            log.warning("run ledger: %s holds a DIFFERENT key than the one "
                        "asked for — ignoring it (never serving a near miss)",
                        p.name)
        return None
    except Exception as _e:     # noqa: BLE001 — a broken ledger must never
        log.warning("run ledger read failed (%s) — solving instead", _e)
        return None


def _ledger_write(sb_key: tuple, result: Dict, key_fields: "OrderedDict") -> None:
    """Record a finished LIVE-machine run so an identical request can load it.

    Called from the ONE place a run is finalised (next to `_save_last_transient`
    and `_refresh_caches_for_run`), so no route can forget it and a candidate
    eval / background probe — which is not the motor on screen — can never
    write one.

    `frames` is stripped exactly as the persist path strips it: the per-frame
    animation payload belongs to the request that asked for it (a 464-step run
    once wrote 790 MB), and a ledger hit is served to the charts, which never
    read frames.
    """
    import gzip as _gz
    try:
        _d = _ledger_dir()
        _d.mkdir(parents=True, exist_ok=True)
        stripped = {k: v for k, v in result.items() if k != "frames"}
        blob = {
            "key": _retuple_to_list(sb_key),
            # NAMED, because "why did it not match?" is the only question this
            # file ever gets asked, and a bare 40-tuple diff is unreadable —
            # the same lesson as `_field_snap_key_fields`.
            "key_fields": {k: _retuple_to_list(v)
                           for k, v in key_fields.items()},
            "computed_at": result.get("computed_at"),
            "geo_fingerprint": result.get("geo_fingerprint"),
            "mat_signature": _ledger_mat_signature(),
            "result": stripped,
        }
        p = _ledger_path_for(sb_key, result.get("geo_fingerprint"))
        tmp = p.with_suffix(".tmp")
        with _gz.open(tmp, "wt", encoding="utf-8") as fh:
            _json.dump(blob, fh, default=_json_default)
        _os_t.replace(tmp, p)       # atomic: a half-written entry is never read
        _ledger_trim()
    except Exception as _e:     # noqa: BLE001 — bookkeeping never kills a run
        log.warning("could not record the run in the ledger: %s", _e)


def _ledger_entries() -> list:
    """Every entry as {file, key_fields, computed_at, geo_fingerprint,
    mat_signature, bytes}, newest first.  Metadata only — the results
    themselves are megabytes and nobody wants them on a status line."""
    import gzip as _gz
    out = []
    try:
        _d = _ledger_dir()
        if not _d.exists():
            return out
        for p in _d.glob("*.json.gz"):
            try:
                with _gz.open(p, "rt", encoding="utf-8") as fh:
                    blob = _json.load(fh)
                out.append({
                    "file": p.name,
                    "key_fields": blob.get("key_fields"),
                    "computed_at": blob.get("computed_at"),
                    "geo_fingerprint": blob.get("geo_fingerprint"),
                    "mat_signature": blob.get("mat_signature"),
                    "bytes": p.stat().st_size,
                    "mtime": p.stat().st_mtime,
                })
            except Exception:   # noqa: BLE001 — one unreadable file is not a 500
                continue
    except Exception as _e:     # noqa: BLE001
        log.warning("run ledger listing failed: %s", _e)
    out.sort(key=lambda e: (e.get("computed_at") or "", e.get("mtime") or 0),
             reverse=True)
    return out


def _ledger_trim() -> None:
    """Ring buffer: keep the newest `_LEDGER_KEEP_PER_GEO` per geometry, and
    keep the whole store under `_LEDGER_MAX_BYTES`.

    Per GEOMETRY, not globally: the point of the ledger is that today's machine
    keeps today's operating points, and a global ring would let one afternoon of
    PWM sweeps on machine B evict every point of machine A the user is about to
    go back to.
    """
    try:
        _d = _ledger_dir()
        if not _d.exists():
            return
        # Filenames and stat() only — this runs after EVERY solve, and opening
        # (gunzipping, parsing) a few hundred stored results to decide which
        # ones to keep is work the user would pay for on every run.
        files = []
        for p in _d.glob("*-*.json.gz"):
            try:
                st = p.stat()
            except Exception:   # noqa: BLE001
                continue
            files.append((p, p.name.split("-", 1)[0], st.st_mtime, st.st_size))
        files.sort(key=lambda f: f[2], reverse=True)     # newest first
        _seen: Dict[str, int] = {}
        doomed, kept = [], []
        for f in files:
            _seen[f[1]] = _seen.get(f[1], 0) + 1
            (doomed if _seen[f[1]] > _LEDGER_KEEP_PER_GEO else kept).append(f)
        _total = sum(f[3] for f in kept)
        while kept and _total > _LEDGER_MAX_BYTES:
            _old = kept.pop()                  # oldest of what survived
            _total -= _old[3]
            doomed.append(_old)
        for f in doomed:
            try:
                f[0].unlink()
            except Exception:   # noqa: BLE001
                pass
        if doomed:
            log.info("run ledger: trimmed %d entr%s", len(doomed),
                     "y" if len(doomed) == 1 else "ies")
    except Exception as _e:     # noqa: BLE001
        log.warning("run ledger trim failed: %s", _e)


def _ledger_clear() -> int:
    """Delete every entry; returns how many went."""
    n = 0
    try:
        _d = _ledger_dir()
        if not _d.exists():
            return 0
        for p in _d.glob("*.json.gz"):
            try:
                p.unlink()
                n += 1
            except Exception:   # noqa: BLE001
                pass
    except Exception as _e:     # noqa: BLE001
        log.warning("run ledger clear failed: %s", _e)
    return n


# Serializes transient computes so concurrent identical requests (the
# animation viewer + transient charts both fire on Simulation-tab mount)
# don't each spawn a worker pool and storm the CPU.
import threading as _threading
_fem_transient_lock = _threading.Lock()


# ═════════════════════════════════════════════════════════════════════════════
#  GENERATOR → BATTERY: the DC side of the same bridge
# ═════════════════════════════════════════════════════════════════════════════
_BATTERY_FIELDS = ("v_oc", "v_nom", "v_min", "v_max", "cells", "n_parallel",
                   "r_int_mohm", "capacity_ah", "i_charge_max_a", "chemistry")


def _parse_battery_payload(raw: Optional[str]) -> Optional[dict]:
    """Parse the `battery` request payload into a plain dict, or None.

    MALFORMED IS A 422, not None — the same rule `geo=` and `mat=` learned the
    hard way: returning None on a bad payload meant the run silently fell back
    to "no battery" and the card quietly lost its charging block while every
    other number stayed plausible.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        import json as _js
        try:
            data = _js.loads(raw)
        except Exception as _be:
            raise HTTPException(status_code=422, detail=(
                "`battery` is not valid JSON (%s).  Expected an object like "
                '{"v_oc": 44.4, "cells": 12, "r_int_mohm": 12, '
                '"capacity_ah": 10}.' % (_be,)))
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail=(
            "`battery` must be a JSON object; got %s." % type(data).__name__))
    out: dict = {}
    for k, v in data.items():
        if k not in _BATTERY_FIELDS:
            continue                      # tolerate the yaml's extra keys
        if k == "chemistry":
            out[k] = (str(v).strip() or None) if v is not None else None
            continue
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=(
                "battery.%s must be a number; got %r." % (k, v)))
        if not (fv == fv and abs(fv) != float("inf")):
            raise HTTPException(status_code=422, detail=(
                "battery.%s is %r — NaN/inf is not a battery." % (k, v)))
        if k in ("cells", "n_parallel"):
            if fv < 1:
                raise HTTPException(status_code=422, detail=(
                    "battery.%s must be >= 1; got %r." % (k, v)))
            out[k] = int(round(fv))
        else:
            if fv < 0:
                raise HTTPException(status_code=422, detail=(
                    "battery.%s must be >= 0; got %r." % (k, v)))
            out[k] = fv
    return out or None


def _battery_charge_block(sbres: dict, *, op_mode: str, p_mech_w: float,
                          p_loss_w: float) -> Optional[dict]:
    """The CHARGING card: what actually reaches the pack, and the two
    independent ways of measuring it side by side.

    Present only when the run had a battery AND drove the machine from an
    imposed voltage (a current-drive run has no bridge and no bus), so no
    existing case grows a key.

    TWO NUMBERS FOR THE SAME WATTS, and the gap between them is the point:

    * ``P_charge_circuit_W`` — V_bus·⟨i_dc⟩ off the modulator's own switch
      states.  Exact for the circuit that was solved, and by the pole-voltage
      identity it equals that circuit's terminal power.
    * ``P_charge_W`` — the ENERGY BALANCE: mechanical power in, minus every
      loss the card reports.  This is the honest headline, because the 2-D
      circuit does NOT carry the iron loss or the end-winding copper (both are
      post-processed and never flow through its terminals — the same argument
      the efficiency formula above is built on).

    So the circuit number sits HIGH of the balance number by roughly
    (P_fe + end-winding copper), and ``balance_gap_W`` says by how much.  A gap
    that is not close to those watts means something else is wrong, which is
    exactly what a self-check is for.

    IDEAL BRIDGE.  Dead time, device conduction/switching loss and DC-link
    ripple are all absent; every one of them subtracts, so a real charger
    delivers less than either number here, never more.
    """
    bi = sbres.get("battery_input")
    if not isinstance(bi, dict):
        return None
    drive = str(sbres.get("drive") or "")
    if drive not in ("voltage", "pwm_voltage"):
        return None
    v_oc = float(bi.get("v_oc_V") or 0.0)
    r_pack = float(bi.get("R_pack_ohm") or 0.0)
    cap = float(bi.get("capacity_ah") or 0.0)
    i_max = bi.get("i_charge_max_A")
    pwm = sbres.get("pwm") or {}
    dcl = (pwm.get("dc_link") or {}) if isinstance(pwm, dict) else {}
    v_bus = float(pwm.get("v_bus_V") or 0.0) if isinstance(pwm, dict) else 0.0
    if v_bus <= 0.0:
        v_bus = float(sbres.get("v_bus_applied_V") or 0.0) or v_oc

    # Mechanical power INTO the machine (generator) — the card quotes torque
    # and speed as magnitudes, so take the magnitude and let op_mode say which
    # way it flows.  A motoring run gets a charging block too, reporting a
    # NEGATIVE charge power: that is the honest reading of "this operating
    # point empties the pack", and hiding it would be the substitution.
    p_mech_in = abs(float(p_mech_w)) if op_mode == "generator" else -abs(float(p_mech_w))
    p_balance = p_mech_in - float(p_loss_w)

    out: dict = {
        "V_oc_V": round(v_oc, 3),
        "R_pack_ohm": round(r_pack, 6),
        "V_bus_V": round(v_bus, 3),
        "V_bus_rise_V": round(v_bus - v_oc, 3),
        "capacity_ah": (round(cap, 3) if cap > 0 else None),
        "i_charge_max_A": i_max,
        "cells_series": bi.get("cells_series"),
        "cells_parallel": bi.get("cells_parallel"),
        "chemistry": bi.get("chemistry"),
        "r_int_mohm_per_cell": bi.get("r_int_mohm_per_cell"),
        # WHICH of these numbers nobody measured.  Rides to the card so a
        # placeholder is never printed as a measurement.
        "placeholders": bi.get("sources") or {},
        "P_charge_W": round(p_balance, 1),
        "P_mech_in_W": round(p_mech_in, 1),
        "P_loss_machine_W": round(float(p_loss_w), 1),
        "bridge_model": ("ideal two-level bridge — no dead time, no device "
                         "conduction or switching loss, no DC-link ripple; a "
                         "real charger delivers less, never more"),
    }
    # ── the switched (circuit) measurement, when there were switches ──────
    if dcl.get("I_dc_mean_A") is not None and v_bus > 0.0:
        i_dc = float(dcl["I_dc_mean_A"])          # + = out of the pack
        out["I_dc_mean_A"] = round(i_dc, 4)
        out["I_dc_rms_A"] = dcl.get("I_dc_rms_A")
        out["I_dc_ripple_pp_A"] = dcl.get("I_dc_ripple_pp_A")
        out["I_charge_circuit_A"] = round(-i_dc, 4)
        out["P_charge_circuit_W"] = round(-i_dc * v_bus, 1)
        out["balance_gap_W"] = round(out["P_charge_circuit_W"] - p_balance, 1)
        _den = max(abs(out["P_charge_circuit_W"]), abs(p_balance), 1e-9)
        out["balance_gap_pct"] = round(100.0 * out["balance_gap_W"] / _den, 2)
        out["balance_gap_note"] = (
            "the switched number is the 2-D circuit's own terminal power, so "
            "the gap is the loss terms that never pass through those terminals "
            "— the post-processed iron loss, the solved magnet/shaft eddy "
            "(both paid out of the shaft, not the bus) — plus the difference "
            "between T·omega and the solver's energy-balanced P_mech.  "
            "MEASURED on the 85 mm 13 mm-stack at its peak duty (4 kHz, 7.1 "
            "steps/carrier): 25.3 W of 418 W = 5.7 %, against P_core 3.0 W + "
            "P_solid 14.0 W and a 17.1 W T·omega-vs-balance difference.  A gap "
            "much larger than those terms is the model telling you something "
            "else is wrong.")
        out["method"] = "switch states (Σ s_phase·i_phase) + energy balance"
    else:
        out["method"] = (
            "energy balance only — a sinusoidal voltage drive has no switch "
            "states, so there is no DC-link waveform to integrate; the charge "
            "power assumes a LOSSLESS converter")
    # Headline current: from the balance, because that is the honest power.
    i_charge = (p_balance / v_bus) if v_bus > 1e-9 else 0.0
    out["I_charge_A"] = round(i_charge, 3)
    if cap > 1e-9:
        out["C_rate"] = round(abs(i_charge) / cap, 3)
    if i_max not in (None, 0):
        out["i_charge_headroom_A"] = round(float(i_max) - i_charge, 3)
        out["over_i_charge_max"] = bool(i_charge > float(i_max))
    # η_charge — the chain the user asked for: shaft in → pack in.  Defined
    # only when the shaft is actually driving the machine; a machine that is
    # NOT generating has no charge efficiency, and 0/negative is the answer.
    if p_mech_in > 1.0:
        out["eta_charge"] = round(max(0.0, p_balance) / p_mech_in, 4)
        out["charging"] = bool(p_balance > 0.0)
        if p_balance <= 0.0:
            out["verdict"] = (
                "NOT charging at this point: the machine's own losses (%.0f W) "
                "exceed the shaft power (%.0f W), so the bridge draws from the "
                "pack instead of filling it"
                % (float(p_loss_w), p_mech_in))
    else:
        out["eta_charge"] = None
        out["charging"] = False
        out["verdict"] = ("not generating — the shaft is taking power, not "
                          "giving it; this point discharges the pack")
    # Pack-side ohmic loss: NOT a machine loss, kept out of the motor's
    # efficiency and reported on its own so the two are never conflated.
    out["P_pack_r_loss_W"] = round(i_charge * i_charge * r_pack, 2)
    return out


def _charge_read(res: dict) -> dict:
    """The charging block of a finished run, or {}."""
    s = res.get("summary")
    return (s.get("battery_charge") or {}) if isinstance(s, dict) else {}


def _charge_attach(res: dict, key: str, block) -> dict:
    """Copy-on-write the extra block onto the charging card.

    A COPY, always: the result being decorated is the object sitting in
    ``_fem_transient_cache``, and mutating it in place would write this
    request's outer-loop trace into every later cache hit for the same physics.
    """
    out = dict(res)
    s = dict(out.get("summary") or {})
    bc = dict(s.get("battery_charge") or {})
    bc[key] = block
    s["battery_charge"] = bc
    out["summary"] = s
    return out


def _charge_outer_loops(route_kwargs: dict, *, batt, mode_eff: str,
                        bus_couple: bool, charge_max: bool, bus_iters: int,
                        bus_tol_pct: float, charge_max_steps: int,
                        charge_max_evals: int) -> dict:
    """The two loops whose body is one whole transient.

    ``_charge_outer_loops`` sits ABOVE the solve lock and the cache on purpose:
    every iteration re-enters :func:`get_fem_transient` as an ordinary request,
    so it takes the same validation, the same cache entry and the same summary
    a hand-typed run would — and a repeated iterate costs nothing.  Nothing here
    knows any physics; it only chooses the next request.
    """
    def _solve(**over) -> dict:
        kw = dict(route_kwargs)
        kw.update(over)
        kw["bus_couple"] = False
        kw["charge_max"] = False
        # READER (indirect) of `_fem_transient_cache`: this is the one caller
        # that genuinely needs the memo.  The bus fixed point and the compass
        # search revisit the SAME (V_bus, V1, delta) point by construction, and
        # each revisit would otherwise be a full transient.  Opting in here (and
        # nowhere else) keeps the loops cheap while the user's Run still solves.
        _tok = _TRANSIENT_MEMO.set(True)
        try:
            return get_fem_transient(**kw)
        finally:
            _TRANSIENT_MEMO.reset(_tok)

    def _probe(**over) -> dict:
        """A solve the USER did not ask for — a coarse search point at an
        operating point nobody chose.

        Runs under ``_BACKGROUND_RUN``, which is the standing rule for exactly
        this: a background solve must never persist as the user's last
        transient nor claim the field-snapshot store, even though it solves the
        very same machine (incident 2026-08-25 — a loss-grid point replaced the
        card mid-session).  It also drops the animation frames and the harmonic
        reference run, neither of which anything in a search ever reads and
        both of which cost real time and memory per eval.

        The BUS-COUPLING iterations deliberately do NOT go through here: they
        are the same operating point at successive bus voltages, the last of
        them IS the answer, and letting them persist in order leaves the live
        state exactly where a hand-run sequence would have left it.
        """
        tok = _BACKGROUND_RUN.set(True)
        try:
            return _solve(include_frames=False, field_snapshot=False,
                          harm_ref=False, **over)
        finally:
            _BACKGROUND_RUN.reset(tok)

    _tol = max(1e-4, float(bus_tol_pct)) / 100.0

    # ── LOOP 1: the bus the battery actually presents ────────────────────
    # V_bus = V_oc + I_charge·R_pack.  Charging RAISES the terminal it charges
    # into, which lowers the modulation index at a fixed applied fundamental,
    # which changes the current, which changes the terminal — so it is a fixed
    # point, not a formula.  Each pass is a full transient; on a 12S pack with
    # ~10 mΩ of internal resistance it converges in two or three.
    def _coupled(**over):
        if not bus_couple:
            return _solve(**over), None
        v_bus = float(batt.v_oc)
        trace = []
        res = None
        conv = False
        for it in range(max(1, int(bus_iters))):
            res = _solve(v_bus=round(v_bus, 4), **over)
            bc = _charge_read(res)
            i_ch = float(bc.get("I_charge_A") or 0.0)
            v_next = batt.bus_under_charge(i_ch)
            trace.append({"iteration": it + 1, "V_bus_V": round(v_bus, 4),
                          "I_charge_A": round(i_ch, 4),
                          "V_bus_next_V": round(v_next, 4),
                          "delta_V": round(v_next - v_bus, 5)})
            if abs(v_next - v_bus) <= _tol * max(abs(v_bus), 1e-9):
                conv = True
                break
            v_bus = v_next
        block = {
            "enabled": True,
            "converged": conv,
            "iterations": len(trace),
            "max_iterations": int(bus_iters),
            "tol_pct": float(bus_tol_pct),
            "V_oc_V": round(float(batt.v_oc), 4),
            "R_pack_ohm": round(batt.r_pack_ohm, 6),
            "trace": trace,
            "note": ("fixed point V_bus = V_oc + I_charge·R_pack around the "
                     "whole transient; each iteration is a full solve.  NOT "
                     "converged means the reported bus is the last iterate, "
                     "not the answer — the trace says by how much it was still "
                     "moving."),
        }
        return res, block

    # ── LOOP 2: "вся мощность в зарядку" ─────────────────────────────────
    if not charge_max:
        res, bus_block = _coupled()
        return _charge_attach(res, "bus_coupling", bus_block) if bus_block else res

    v1_0 = float(route_kwargs.get("v_phase_peak") or 0.0)
    dl_0 = float(route_kwargs.get("v_delta_deg") or 0.0)
    if not (v1_0 > 0.0):
        raise HTTPException(status_code=422, detail=(
            "charge_max is a LOCAL search and needs a starting point: send "
            "v_phase_peak / v_delta_deg from a sine-current generator run's "
            "V1_seed (Simulation → run the duty on the current drive, then "
            "'apply').  Searching from nothing would report whichever local "
            "maximum the first guess happened to sit in."))
    i_lim = float(route_kwargs.get("I_phase_rms") or 0.0)
    i_ch_max = float(batt.i_charge_max_a or 0.0)

    # Coarse resolution for the search passes.  4 steps per carrier is the
    # solver's own floor for a PWM run; below it the exact-volt-second means
    # average the pulses away and the ripple (hence the copper loss, hence the
    # charge power) reads low.  The winner is re-solved at the requested
    # resolution before anything is reported.
    _steps_fine = int(route_kwargs.get("n_steps_per_period") or 40)
    _steps_coarse = int(charge_max_steps or 0)
    if _steps_coarse <= 0:
        try:
            _rpm_e = _effective_rpm(route_kwargs.get("rpm"))
            _g = _parse_geo_override(route_kwargs.get("geo")) or {}
            from motor_ai_sim.config import get_config as _gc_s
            _pp = int((_g.get("num_poles")
                       or (_gc_s().get("geometry") or {}).get("num_poles") or 2)) // 2
            _fe = float(_rpm_e) * _pp / 60.0
            _nc = max(1, int(round(float(route_kwargs.get("f_switch") or 0.0)
                                   / max(_fe, 1e-9))))
            _steps_coarse = max(24, 4 * _nc)
        except Exception:                       # noqa: BLE001
            _steps_coarse = max(24, _steps_fine // 4)
    _steps_coarse = min(_steps_coarse, _steps_fine)

    log.info("charge_max: seed V1=%.3f V pk, delta=%.2f deg; coarse %d "
             "steps/period, <=%d evals; limits I_phase<=%.3g A, "
             "I_charge<=%.3g A", v1_0, dl_0, _steps_coarse, charge_max_evals,
             i_lim or float("inf"), i_ch_max or float("inf"))

    evals: list = []
    cache: dict = {}

    def _score(v1: float, dl: float):
        """(objective, record).  Feasible → the charge power itself; infeasible
        → a large negative ordered by HOW infeasible, so the search can walk
        back into the feasible set instead of falling off a cliff."""
        k = (round(v1, 4), round(dl, 3))
        if k in cache:
            return cache[k]
        try:
            r = _probe(v_phase_peak=k[0], v_delta_deg=k[1],
                       n_steps_per_period=_steps_coarse, v_bus=round(
                           float(batt.v_oc), 4) if bus_couple else
                       route_kwargs.get("v_bus"))
        except HTTPException as _he:
            # An impossible inverter (m > 1.15) is a real boundary of the
            # search space, not an error — score it as maximally infeasible and
            # keep going.
            rec = {"V1_peak_V": k[0], "delta_deg": k[1], "feasible": False,
                   "refused": str(getattr(_he, "detail", _he))[:200]}
            evals.append(rec)
            cache[k] = (-1e18, rec)
            return cache[k]
        bc = _charge_read(r)
        p = float(bc.get("P_charge_W") or 0.0)
        i_ph = r.get("I_phase_rms_solved_A")
        i_ph = float(i_ph) if i_ph is not None else 0.0
        i_ch = float(bc.get("I_charge_A") or 0.0)
        viol = 0.0
        if i_lim > 1e-9 and i_ph > i_lim:
            viol += i_ph / i_lim - 1.0
        if i_ch_max > 1e-9 and i_ch > i_ch_max:
            viol += i_ch / i_ch_max - 1.0
        rec = {"V1_peak_V": k[0], "delta_deg": k[1],
               "P_charge_W": round(p, 1),
               "I_phase_rms_A": round(i_ph, 3),
               "I_charge_A": round(i_ch, 3),
               "feasible": viol <= 0.0,
               "violation": round(viol, 5)}
        evals.append(rec)
        cache[k] = ((p if viol <= 0.0 else -1e12 * (1.0 + viol)), rec)
        return cache[k]

    # Compass (pattern) search on (V1, delta).  Derivative-free, two variables,
    # a predictable eval count and no step it cannot explain — the objective is
    # a FEM solve, so a method that needs gradients or many probes per move is
    # the wrong shape here.
    best_v1, best_dl = v1_0, dl_0
    h_v, h_d = max(0.05 * v1_0, 0.05), 10.0
    best_f, best_rec = _score(best_v1, best_dl)
    while len(evals) < max(2, int(charge_max_evals)):
        moved = False
        for dv, dd in ((h_v, 0.0), (-h_v, 0.0), (0.0, h_d), (0.0, -h_d)):
            if len(evals) >= max(2, int(charge_max_evals)):
                break
            cand_v = max(1e-3, best_v1 + dv)
            cand_d = best_dl + dd
            f, rec = _score(cand_v, cand_d)
            if f > best_f:
                best_f, best_rec = f, rec
                best_v1, best_dl = cand_v, cand_d
                moved = True
                break
        if not moved:
            if h_v <= 0.005 * max(v1_0, 1e-9) and h_d <= 0.5:
                break                       # step is below what the seed knows
            h_v *= 0.5
            h_d *= 0.5

    # ── FINE CONFIRM ─────────────────────────────────────────────────────
    # The winner re-solved at the REQUESTED resolution (and, when asked, with
    # the battery-coupled bus).  A search result quoted at its own coarse
    # resolution is a number the run never actually produced.
    res, bus_block = _coupled(v_phase_peak=round(best_v1, 4),
                              v_delta_deg=round(best_dl, 3))
    if bus_block:
        res = _charge_attach(res, "bus_coupling", bus_block)
    fine = _charge_read(res)
    search = {
        "objective": "maximum charge power P_charge_W at this rpm",
        "seed": {"V1_peak_V": round(v1_0, 4), "delta_deg": round(dl_0, 3)},
        "best": {"V1_peak_V": round(best_v1, 4), "delta_deg": round(best_dl, 3)},
        "constraints": {
            "I_phase_rms_max_A": (round(i_lim, 3) if i_lim > 0 else None),
            "i_charge_max_A": (round(i_ch_max, 3) if i_ch_max > 0 else None),
        },
        "coarse_steps_per_period": int(_steps_coarse),
        "fine_steps_per_period": int(_steps_fine),
        "n_coarse_solves": len(evals),
        "evals": evals,
        "P_charge_coarse_W": (best_rec or {}).get("P_charge_W"),
        "P_charge_fine_W": fine.get("P_charge_W"),
        "coarse_to_fine_shift_W": (
            None if (fine.get("P_charge_W") is None
                     or (best_rec or {}).get("P_charge_W") is None)
            else round(float(fine["P_charge_W"])
                       - float(best_rec["P_charge_W"]), 1)),
        "method": ("compass search on (V₁, δ) with a shrinking step, seeded "
                   "from the V₁ of a sine-current run at this operating point; "
                   "infeasible points (over the current limit, or past the "
                   "modulation limit) are scored by how infeasible so the "
                   "search walks back rather than falling off"),
        "caveat": ("a LOCAL maximum from a seeded local search — it is the "
                   "best point found near the seed, not a proof that no better "
                   "one exists elsewhere"),
    }
    return _charge_attach(res, "charge_search", search)


def clear_simulation_caches(reason: str = "") -> None:
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

    It is ALSO the answer to "this is a different machine now" for the two
    changes that are not geometry but change the physics just as completely:
    a MATERIAL assignment (PATCH /api/materials) and a PART STATE
    (PATCH /api/parts — a part solved as air weighs nothing and carries no
    flux).  Both used to leave the field snapshot and the transient sitting
    there for the relaxed lookup to serve.

    Idempotent and cheap: six `dict.clear()` calls plus the two sibling
    routers', safe to call on every write, safe to call twice.  `reason` is
    remembered for GET /api/simulation/caches so a surprising empty store can be
    explained.
    """
    for _c in (_motor_geom_cache, _fem_mesh_cache, _fem_mesh_sb_cache,
               _fem_field_cache, _fem_transient_cache, _transient_field_snap):
        try:
            _c.clear()
        except Exception:
            pass
    # The rotor STRUCTURAL result is keyed on the same cross-section and the
    # same material assignment, so it goes stale for exactly the reasons these
    # do — a sleeve sized on the previous machine is a wrong sleeve.  Lazy
    # import: routes.mechanical imports from here.
    try:
        from motor_ai_sim.routes.mechanical import clear_mechanical_caches
        clear_mechanical_caches(reason)
    except Exception:
        pass
    # The TEMPERATURE map is keyed on the same cross-section and the same
    # material assignment (the conductivities come straight off it), so it goes
    # stale for exactly the same reasons — a map drawn on the previous
    # cross-section is a wrong temperature.  It used to be `_thermal_field_cache`
    # in this module; since 2026-09-07 it lives in routes.thermal, cleared here
    # by the same call.  Lazy import: routes.thermal imports from here.
    try:
        from motor_ai_sim.routes.thermal import clear_thermal_caches
        clear_thermal_caches(reason)
    except Exception:
        pass
    _cache_state["last_cleared_reason"] = reason or "unspecified"
    _cache_state["last_refreshed_by"] = None


def _cache_stats() -> Dict:
    """What is actually IN the physics caches right now.

    Counts only — an entry holds whole per-node arrays and nobody wants them on
    a status line.  The snapshot store additionally names each entry's
    `computed_at`, because "which run is the J⟳ view about to draw?" is the one
    question this store gets asked.
    """
    _snaps = []
    try:
        for _e in _transient_field_snap.values():
            _snaps.append((_e.get("meta") or {}).get("computed_at"))
    except Exception:       # noqa: BLE001
        pass
    _opt = None
    try:    # lazy: the optimizer module pulls in the whole scan machinery
        from motor_ai_sim.routes.optimization import _EVAL_CACHE as _ec
        _opt = len(_ec)
    except Exception:       # noqa: BLE001 — unavailable is null, never a 500
        _opt = None
    return {
        "field_cache": len(_fem_field_cache),
        "transient_cache": len(_fem_transient_cache),
        "snapshots": len(_transient_field_snap),
        "snapshot_computed_at": _snaps,
        "optimization_eval_cache": _opt,
        "last_refreshed_by": _cache_state.get("last_refreshed_by"),
        "last_cleared_reason": _cache_state.get("last_cleared_reason"),
    }


@router.get("/caches")
def get_cache_stats():
    """Physics-cache state: how many entries each store holds and which run
    last refreshed them.  Read-only; gated to the owner tier by the same
    `_GATED` table that gates the solve endpoints (motor_ai_sim/auth.py)."""
    return _cache_stats()


@router.post("/caches/clear")
def clear_cache_endpoint():
    """Empty the field / transient / snapshot stores by hand.

    MEMORY ONLY.  The disk stores — `.last_transient.json` (restore), the
    optimizer's `.scan_cache.jsonl`, `.daxis_cache.json` — are deliberately
    untouched: they are records of work done, not stale physics, and throwing
    them away costs hours of solving.
    """
    clear_simulation_caches(reason="manual clear (POST /caches/clear)")
    return _cache_stats()

# Cooperative cancel keyed by run-id.  The "Stop" button POSTs the id of
# the run it wants stopped; the parallel solve loop (and any duplicate
# request waiting on the lock) checks whether ITS run-id was cancelled and
# bails.  Keying by id means cancelling one run never aborts the next one.
# Migration Stage 4: a MAP keyed by run id, in ``motor_ai_sim.jobs``, not the
# one-slot dict this used to be.  One slot meant the LAST cancel won: with two
# accounts solving, B pressing Stop cleared the id A's march was checking, and
# A's Stop then did nothing.  ``jobs.cancel_run`` also carries the ownership
# check — the caller must own the run, or be an admin.
def _cancel_transient(run_id: str) -> None:
    """Mark a transient cancelled WITHOUT an ownership check.

    For the internal callers only (the coupled loop forwarding its own cancel
    into the EM run it is driving).  The HTTP endpoints go through
    ``jobs.cancel_run``, which refuses a run the caller does not own.
    """
    if run_id:
        _JOBS.cancel_run(run_id, requester="", is_admin=True)


_JOBS.register_cancel_hook("transient", _cancel_transient)


class _RunCancelled(BaseException):
    # BaseException on purpose: the march wraps every progress_cb call in
    # `except Exception: pass` (a broken callback must not kill a solve), and
    # a cancel raised from that callback was swallowed right there (measured:
    # the test run completed 360/360 with the cancel id set from frame 8).
    # Like KeyboardInterrupt, a cancellation is a REQUEST, not an error.
    """Raised out of the per-frame progress callback when the Stop button's
    run-id matches this solve.  The registry used to be WRITE-ONLY — the
    endpoint recorded the id and no code ever read it, so Stop was a no-op
    and a 5984-frame PWM run could only be killed with the process
    (measured live 2026-08-31)."""

# Shared progress state for the currently-running transient.  Polled by
# the frontend via /physics/fem_transient/progress so the user sees
# "Frame X / N — Ys elapsed — ETA Zs" instead of a spinning "Running…".
# Migration Stage 4: ONE OF THESE PER RUN, not one for the server.  The NAME,
# the ``["current"]`` key and all twelve write sites below are unchanged —
# ``TransientProgressMap`` hands back the dict belonging to the run this call is
# inside, and the router's per-workspace default dict (which is exactly what
# this global used to be) when there is none.  So a single-user server with
# nothing queued behaves bit for bit as it did, and a second account's Run no
# longer zeroes the bar the first one is watching.
_fem_transient_progress = _PROG.TransientProgressMap("transient")


@router.get("/physics/fem_transient/progress")
async def get_fem_transient_progress(run_id: str = ""):
    """Lightweight progress endpoint — frontend polls this every ~500 ms
    while a transient solve is in flight.  Returns step counter, elapsed
    wall-time and an ETA estimated from the average seconds-per-step so far.

    ``?run_id=`` (Stage 4) answers THAT run.  With no argument it answers the
    caller's newest transient — which on a single-user server is the same dict
    it always was, so today's strip keeps working with no change.  While a run
    is WAITING for a queue slot the payload also carries ``queued`` and
    ``position``; it carries neither at any other time.
    """
    p, _rid = _PROG.transient_dict("transient", run_id)
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
            return _PROG._with_queue({
                **p,
                "elapsed_s":  round(elapsed, 1),
                "eta_s":      0.0,
                "per_step_s": 0.0,
                "frac":       0.0,
                "field_busy": field_busy(),
            }, _rid)
        per_step = elapsed / raw_step
        eta = per_step * max(0, total - raw_step)
        return _PROG._with_queue({
            **p,
            "elapsed_s": round(elapsed, 1),
            "eta_s":     round(eta, 1),
            "per_step_s": round(per_step, 2),
            "frac":      round(raw_step / total, 3),
            "field_busy": field_busy(),
        }, _rid)
    # Field solves ride along on the poll the panel already makes.  They are
    # NOT part of this transient's progress — they are the OTHER thing the
    # server may be busy with, and the strip could not see them at all (the
    # 2026-09-03 incident: ~17 cores of field solves, "nothing running" on
    # screen).  Same object as GET /physics/field_busy.
    return _PROG._with_queue({**p, "field_busy": field_busy()}, _rid)


@router.get("/physics/field_busy")
async def get_field_busy():
    """What the FIELD-view solver is doing: `{solving, queued, limit, items}`.

    `items` is one entry per solve in flight or waiting —
    `{key_short, kind, state, since_s, waiters}` — so the panel can say "server
    busy: 2 field solves (1 queued)" and name them in a tooltip.  Cheap enough
    to poll; the same object is attached to the transient-progress response so
    a panel that already polls that gets it for free.
    """
    return field_busy()


@router.post("/physics/fem_transient/cancel")
async def cancel_fem_transient(run_id: str = ""):
    """Request the transient with this run_id to stop.  The solve loop
    checks after each completed frame, tears the worker pool down
    (cancelling pending frames) and raises 499.  A duplicate request for
    the same run_id waiting on the lock bails immediately."""
    # Cancel a SPECIFIC run only.  A new Run uses a fresh run_id (the
    # incrementing runNonce) so it never matches a previously-cancelled
    # id — no risk of a stale cancel killing the next solve.
    #
    # Migration Stage 4: the caller must OWN the run, or be an admin (403
    # otherwise).  With one account that is a tautology; with several it is the
    # difference between a Stop button and a denial of service.
    if not run_id:
        return {"cancelled": False, "run_id": ""}
    try:
        out = _JOBS.cancel_run(run_id)
    except _JOBS.NotOwner:
        raise HTTPException(status_code=403,
                            detail="that run belongs to another account")
    return {"cancelled": bool(out.get("cancelled")), "run_id": run_id}


def _mark_equivalent_star(sbres: Dict, *, v_bus_real: float,
                          v_bus_model: float, drive: str) -> None:
    """NAME the delta-on-the-star-circuit substitution in the result.

    A delta machine under an imposed-voltage source is solved on the star
    circuit at √3 × the real DC link (see the star-equivalent block in
    ``get_fem_transient``).  Everything the SOLVER reports is already in the
    right terms — the winding is the winding, and the terminal mapping is the
    connection's own — but the inverter block describes the MODEL bridge, whose
    bus nobody can buy.  So the block says which bus is which, and the two
    quantities that live on the bus are put back on the real one:

    * ``v_bus_V`` becomes the REAL link (``v_bus_model_V`` keeps the model's),
      so every consumer that multiplies bus × DC current — the charging card
      above all — still gets the terminal power the circuit solved;
    * ``dc_link`` is scaled by √3.  EXACT for the mean (V·⟨i_dc⟩ is that same
      terminal power on either side of the change of variable); the rms and the
      peak-to-peak are the MODEL bridge's ripple carried across, which is what
      the PWM study quoted, and the note says so rather than letting them pass
      as the real bridge's own.

    The pole / line edge waveforms are left alone and are labelled as the model
    bridge's: they are drawn on the model bus, and the star-delta rotation means
    they are not the real bridge's waveform in time either (the harmonic
    MAGNITUDES are — see ``pwm.star_equivalent_bus``).  Everything the run
    concludes about losses rides on those magnitudes; nothing rides on a peak.

    IDEMPOTENT: a result already marked is left exactly as it is, so no path can
    scale the DC-link series by √3 twice.
    """
    if isinstance(sbres.get("delta_equivalent_star"), dict):
        return
    _k = math.sqrt(3.0)
    _mapping = ("  Terminals mapped back by the connection: I_line = "
                "√3·I_branch, V_LL = V_branch less its zero sequence, plus the "
                "delta's own circulating triplen loss.")
    if float(v_bus_model) > 0.0:
        note = (
            "delta solved on the star equivalent: the isolated-neutral star "
            "circuit on a model bus of √3·V_dc = %.1f V, whose branch voltage "
            "has the SAME HARMONIC MAGNITUDES as the real bridge's line-to-line "
            "voltage — so the fundamental, the ripple rms and every loss are "
            "the real machine's (exact per order when the carrier count divides "
            "by 3, aggregate ripple within 0.4 %% otherwise; PWM study "
            "2026-09-13 §0.3).  The per-harmonic PHASES carry the star-delta "
            "rotation, so the solved voltage is not the bridge's waveform in "
            "time: V_line_peak_solved_V is the model branch's peak, up to "
            "⅔·√3·V_dc = 1.155·V_dc = %.0f V, where a two-level bridge's line "
            "voltage peaks at V_dc = %.1f V.  Quote the rms, not the peak."
            % (v_bus_model, 2.0 / 3.0 * v_bus_model, v_bus_real)) + _mapping
    else:
        # The SINUSOIDAL voltage drive has no bridge and no bus: a balanced
        # sinusoid carries no common mode, so the star model's branch voltage IS
        # v_phase_peak, which is the delta winding's own (= line) voltage.  The
        # equivalence is exact with nothing to scale.
        note = ("delta solved on the isolated-neutral star circuit: with a "
                "balanced sinusoid there is no common mode, so the model's "
                "branch voltage IS the requested v_phase_peak — the delta "
                "winding's own voltage, which is the line voltage.  Exact, "
                "nothing scaled." + _mapping)
    sbres["delta_equivalent_star"] = {
        "applied": True,
        "drive": str(drive),
        "star_delta": "delta",
        "circuit": "isolated-neutral star (drive.circuit_residual_ll)",
        "v_bus_real_V": round(float(v_bus_real), 3),
        "v_bus_model_V": round(float(v_bus_model), 3),
        "bus_scale": round(_k, 6),
        "note": note,
    }
    pwm = sbres.get("pwm")
    if not isinstance(pwm, dict):
        return
    pwm["equivalent_star"] = True
    pwm["v_bus_real_V"] = round(float(v_bus_real), 3)
    pwm["v_bus_model_V"] = round(float(v_bus_model), 3)
    pwm["v_bus_V"] = float(v_bus_real)
    pwm["equivalent_star_note"] = note
    dcl = pwm.get("dc_link")
    if isinstance(dcl, dict) and dcl.get("I_dc_mean_A") is not None:
        dcl["I_dc_model_mean_A"] = dcl["I_dc_mean_A"]
        for _k2 in ("I_dc_mean_A", "I_dc_rms_A", "I_dc_ripple_pp_A"):
            if dcl.get(_k2) is not None:
                dcl[_k2] = round(float(dcl[_k2]) * _k, 4)
        if isinstance(dcl.get("I_dc_A"), list):
            dcl["I_dc_A"] = [round(float(v) * _k, 4) for v in dcl["I_dc_A"]]
        dcl["bus_scale"] = round(_k, 6)
        dcl["note"] = (str(dcl.get("note") or "") + "  DELTA via the star "
                       "equivalent: these are the model bridge's currents ×√3 "
                       "onto the real %.1f V link — the MEAN is exact (V·⟨i_dc⟩ "
                       "is the same terminal power on either bus), the rms and "
                       "the peak-to-peak carry the model bridge's ripple."
                       % float(v_bus_real))


@router.get("/physics/fem_transient")
# Migration Stage 4: the ADMISSION point for the heaviest thing this server
# does.  One line, and it serves both modes (blocking by default; 202 with
# QUEUE_ASYNC=1) because the decorator has the whole body as a callable.
#
# ``skip`` keeps the two CHEAP paths out of the queue, and that is not an
# optimisation: ``ledger_probe`` and ``restore`` both answer off the disk in
# milliseconds, and the panel asks them WHILE a solve of its own is running —
# queued behind it, with one job per user, they would block on the very run
# they are asking about.
@_JOBS.queued("transient", priority=_JOBS.Priority.INTERACTIVE,
              skip=lambda kw: bool(kw.get("ledger_probe") or kw.get("restore")),
              body_keys=("current_a", "rpm", "steps", "n_periods", "gamma_deg",
                         "drive", "n_sectors", "demag", "rotor_eddy"))
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
    mode:        Optional[str] = None,    # ← "motor" (default) | "generator".  Omitted =
                                          #   the Simulation tab's simulation.mode.  A
                                          #   generator run drives the SAME gamma the panel
                                          #   shows, shifted 180 deg el — the current vector
                                          #   opposes the EMF, torque brakes, mechanical
                                          #   power flows in.  Unknown value -> 422.
    daxis_deg:   Optional[float] = None,  # ← D-AXIS REFERENCE, GIVEN.  Omitted = the
                                          #   Simulation tab's simulation.daxis_deg if it
                                          #   holds one, else MEASURED (a 24-frame no-load
                                          #   solve, ~39 s, cached per geometry).  Given, it
                                          #   is used as is and nothing is solved for it.
    strand_bonding: Optional[str] = None, # ← how the wires IN HAND are joined:
                                          #   "transposed" (default) | "series" (the
                                          #   real soldered-ends coil) | "parallel"
                                          #   (upper bound).  Eddy runs only — without
                                          #   the coupled solve there are no strand
                                          #   currents to redistribute.
    star_delta:  Optional[str] = None,    # ← TERMINAL connection: "star" (default)
                                          #   or "delta".  Changes no ampere-turn and
                                          #   therefore no torque: what it changes is the
                                          #   terminal mapping (delta trades sqrt(3) of
                                          #   current for sqrt(3) of voltage) and the
                                          #   zero-sequence loop, which in delta carries a
                                          #   real circulating current the star cannot.
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
    ledger:              bool  = True,    # ← may a STORED result with exactly this key answer the
                                          #   request instead of solving?  (user, 2026-09-05:
                                          #   "просто сканировать результаты: не совпадают ли они с
                                          #   уже проведёнными").  ON for the charts, which read the
                                          #   summary and the series; the field-animation viewer
                                          #   sends false because it needs the per-frame fields,
                                          #   which the ledger deliberately never stores.
    ledger_probe:        bool  = False,   # ← ask only: return {match, computed_at} for this exact
                                          #   request and solve NOTHING.  Exists so
                                          #   GET .../fem_transient/ledger_match can reuse THIS
                                          #   function's key construction instead of rebuilding it
                                          #   (a second copy of a 40-field key is a second copy that
                                          #   drifts, and a drifted probe would promise a stored
                                          #   result that the Run then does not find).
    sliding_band:        bool  = True,    # ← accepted for URL compat and IGNORED: there is
                                          #   only the mesh-once sliding band now.  The
                                          #   remesh-per-frame alternative it used to select
                                          #   solved each frame on the legacy static P1 solver
                                          #   at a hard-coded 108° d-axis, so a caller that
                                          #   omitted this flag silently got a different
                                          #   operating point than one that sent it.
    coil_temp_c:         float = 120.0,   # ← copper temperature → ρ_Cu(T)
    magnet_temp_c: Optional[float] = None,  # ← MAGNET temperature [°C].  None (every
                                          #   caller today) = the assigned magnet card is
                                          #   used exactly as the library quotes it, so
                                          #   every key, fingerprint and number below is
                                          #   byte-identical to what it has always been.
                                          #   A number corrects the card to it — Br, the
                                          #   coercivity and the whole demag curve, knee
                                          #   included (materials.at_temperature).  This
                                          #   is what phase 2's EM↔thermal loop will
                                          #   iterate on; today it is the manual knob.
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
    torque_filter:       bool  = False,   # deprecated, ignored; raw torque only
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
    drive:               str   = "current",  # ← EXCITATION SOURCE:
                                             #   "current"        imposed sinusoidal phase current
                                             #   "voltage"        imposed sinusoidal phase voltage —
                                             #                    the currents are the machine's own
                                             #                    response, incl. back-EMF-harmonic
                                             #                    parasitics (FOC verification mode)
                                             #   "pwm_voltage"    the same circuit driven by an ideal
                                             #                    two-level inverter's chopped pole
                                             #                    voltages → the real switching-
                                             #                    frequency current ripple and what
                                             #                    it costs in torque ripple / copper
                                             #                    / core loss (simulation/pwm.py)
                                             #   "custom_current" imposed ARBITRARY periodic phase
                                             #                    current from a sampled waveform
    v_phase_peak:        float = 0.0,     # ← voltage drive: phase-voltage amplitude [V, peak].
                                          #   Under pwm_voltage this is the FUNDAMENTAL the inverter
                                          #   must APPLY (m = 2·v_phase_peak/v_bus); the modulator's
                                          #   sampled-reference delay/gain is compensated for it.
    v_delta_deg:         float = 0.0,     # ← voltage drive: voltage angle [°el] in the γ frame
    v_bus:               float = 0.0,     # ← pwm_voltage: DC link voltage [V] (pole = ±v_bus/2).
                                          #   Deliberately a PLAIN NUMBER, not a path into the
                                          #   family config: the solver must not know where a
                                          #   machine's battery is stored.  The UI prefills it from
                                          #   the loaded machine's battery v_nom when there is one.
    f_switch:            float = 0.0,     # ← pwm_voltage: carrier frequency [Hz].  SNAPPED to a
                                          #   whole number of carriers per electrical period
                                          #   (synchronous PWM — the reported period must repeat);
                                          #   the effective value comes back in the result.
    waveform:            Optional[str] = None,  # ← custom_current: JSON array of [θ_e_deg, i_A]
                                          #   samples of the phase-A TERMINAL current over ONE
                                          #   electrical period (B/C = the same shape shifted
                                          #   ∓120°el), linearly interpolated, ≤20k points
    i_block:             float = 0.0,     # ← bldc_current: FLAT-TOP terminal current of the 120°
                                          #   block [A].  Not an rms: a 120° block of amplitude I
                                          #   has rms I·√(2/3), so the copper-loss-matched
                                          #   equivalent of a sinusoidal I_rms is I_rms·√(3/2).
    harm_ref:            bool  = True,    # ← voltage drive: ALSO run a current-drive reference at
                                          #   the extracted fundamental (I₁, γ₁) → ΔP_harm = the
                                          #   watt cost of the parasitic harmonic currents
    battery:      Optional[str] = None,   # ← THE PACK ON THE DC LINK, as a plain JSON payload:
                                          #   {v_oc, cells, n_parallel, r_int_mohm, capacity_ah,
                                          #   i_charge_max_a, chemistry}.  A PAYLOAD, never a path
                                          #   into the family config — same rule as v_bus: the
                                          #   solver must not know where a machine's battery is
                                          #   stored.  Present ⇒ the summary carries the charging
                                          #   block (I_charge, P_charge, C-rate, η_charge).
    bus_couple:          bool  = False,   # ← BATTERY-COUPLED BUS.  Off (the default, and every run
                                          #   ever made before this) = a stiff ideal supply at
                                          #   v_bus.  On = fixed point V_bus ← V_oc + I_charge·R_pack
                                          #   around the whole solve, because charging RAISES the
                                          #   terminal it is charging into and that moves the
                                          #   modulation index.  Needs `battery`; PWM/voltage only.
    bus_iters:           int   = 4,       # ← cap on those outer iterations (each is a full transient)
    bus_tol_pct:         float = 0.1,     # ← convergence: |ΔV_bus| below this % of V_bus
    charge_max:          bool  = False,   # ← "ВСЯ МОЩНОСТЬ В ЗАРЯДКУ": search (V₁, δ) for the
                                          #   maximum charge power at THIS rpm, subject to
                                          #   |I_phase| ≤ I_phase_rms and I_charge ≤ i_charge_max.
                                          #   Seeded from v_phase_peak/v_delta_deg (the two-pass
                                          #   V₁ seed), coarse search then one fine confirm.
    charge_max_steps:    int   = 0,       # ← steps/period for the SEARCH passes (0 = auto: enough
                                          #   for 4 per carrier).  The confirm runs at the
                                          #   requested n_steps_per_period.
    charge_max_evals:    int   = 10,      # ← cap on coarse solves in the search
    mat:            Optional[str] = None, # ← per-request material override (multi-user).  The GET
                                          #   route gets this via the router dependency (?mat=);
                                          #   POST /api/kernel/run maps its payload onto THIS
                                          #   signature, which the dependency never sees — without
                                          #   the parameter a user-copied motor solved with the
                                          #   SHARED config's materials and reported the numbers
                                          #   as the user's own.
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

    # EVERY argument, exactly as handed in, captured BEFORE anything below
    # mutates one (gamma_deg picks up the generator's 180°, n_sectors picks up
    # the symmetry).  The battery-coupled bus and the max-charge search are
    # OUTER loops over this same route — each iteration is a whole transient —
    # and they re-enter it by name so every inner solve gets the identical
    # validation, cache key and post-processing a hand-made request would.
    # First statement of the body on purpose: a capture taken any later would
    # forward a half-resolved request.
    _route_kwargs = dict(locals())
    _route_kwargs.pop("_np", None)

    # Per-request materials via the KERNEL path: same parse/validate/set as
    # the router dependency does for ?mat= — per-task context, so the kernel
    # call (which runs inside this request's task) resolves the caller's own
    # materials and nothing leaks across requests.
    if mat is not None:
        from motor_ai_sim.material_context import set_request_materials
        _mov = _parse_mat_override(mat)
        if _mov and _mov.get("assignment"):
            from motor_ai_sim.materials import (validate_assignment as _va2,
                                                UnknownMaterialError as _ume2)
            try:
                _va2(_mov["assignment"],
                     known_extra=set(_mov.get("materials") or ()))
            except _ume2 as _me2:
                raise HTTPException(status_code=400, detail=str(_me2))
        set_request_materials(_mov)

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
    # Resolve the operating mode (argument > shared config > motor) and fold it
    # into the load angle EARLY, so every consumer downstream — the cache key,
    # the solver, the dq stamp — sees the one effective gamma this run solves.
    _mode_raw = mode
    if _mode_raw is None:
        try:
            from motor_ai_sim.config import get_config as _gc_m
            _mode_raw = ((_gc_m().get("simulation") or {}).get("mode"))
        except Exception:
            _mode_raw = None
    _mode_eff = str(_mode_raw or "motor").strip().lower()
    if _mode_eff not in ("motor", "generator"):
        raise HTTPException(status_code=422, detail=(
            "simulation mode must be 'motor' or 'generator'; got %r" % (_mode_raw,)))
    _gamma_panel = float(gamma_deg)          # what the panel shows and compares
    if _mode_eff == "generator":
        gamma_deg = float(gamma_deg) + 180.0

    if n_parallel is not None and int(n_parallel) < 1:
        raise HTTPException(status_code=400,
                            detail=f"n_parallel must be >= 1; got {n_parallel!r}")

    # ── EXCITATION SOURCE — validate BEFORE anything is meshed or solved ──
    # Everything checkable without the machine's d-axis is checked here so an
    # impossible inverter (m > 1.15) or an unusable waveform comes back as a 422
    # in milliseconds, naming the number to change, instead of after a mesh
    # build.  What needs the solved frame — the carrier snap, the modulator
    # delay/gain compensation — is checked in the solver and surfaces through
    # the ExcitationError handler around the solve below.
    from motor_ai_sim.simulation.pwm import (
        ExcitationError as _ExcErr, parse_waveform as _parse_wf,
        MAX_MODULATION_INDEX as _MAX_M,
        star_equivalent_bus as _sq_bus, modulation_index as _mod_idx)
    _drive = str(drive or "current").strip().lower()
    _DRIVES = ("current", "voltage", "pwm_voltage", "custom_current",
               "bldc_current")
    # ── DELTA ON AN IMPOSED-VOLTAGE SOURCE — the EXACT star equivalent ────
    # The voltage circuit (drive.circuit_residual_ll) is the isolated-neutral
    # STAR one: it integrates DIFFERENCES of the applied phase voltages, so a
    # branch of that model sees pole-minus-common-mode.  A DELTA branch sees the
    # inverter's LINE-TO-LINE waveform.  This route used to REFUSE the pair
    # outright — which left every delta machine (L155, L180, the whole Ø200
    # line) with no PWM answer at all.
    #
    # It is not a missing circuit, it is a change of variable: for a balanced
    # set of three legs the two waveforms are the SAME function when the model's
    # bus is √3 × the real DC link and the branch is asked for V₁ of the delta
    # winding (= the line voltage the panel/user gives).  The fundamental matches
    # always; the ripple matches exactly at a carrier count divisible by 3 and
    # within 0.4 % rms otherwise — MEASURED, not argued (user 2026-09-14 / PWM
    # study §0.3, tests/test_pwm_delta_star_equivalent.py).  The
    # terminals are then mapped back by the connection's own rules, which the
    # solver already owns (I_line = √3·I_branch, V_LL = V_branch minus its zero
    # sequence, the circulating triplen loss) — the same block the current-drive
    # delta path goes through, so the summary keys keep their meaning.
    #
    # The substitution is NAMED in the result (`delta_equivalent_star`,
    # `pwm.equivalent_star`, `pwm.v_bus_real_V` / `v_bus_model_V`), never hidden:
    # a model bus of 1299.7 V that nobody can buy must not look like a number
    # somebody measured.
    _sd_eff = _effective_star_delta(star_delta)
    _eq_star = (_drive in ("voltage", "pwm_voltage") and _sd_eff == "delta")
    # The bus the SOURCE chops.  Star: v_bus, byte for byte.  The REQUEST keeps
    # the real link everywhere else (cache key, snapshot key, the reported
    # v_bus_real_V) — what is scaled is only what the star circuit is handed.
    _v_bus_model = _sq_bus(float(v_bus), _sd_eff) if _eq_star else float(v_bus)
    if _drive not in _DRIVES:
        raise HTTPException(status_code=422, detail=(
            "unknown excitation source %r — expected one of %s"
            % (drive, ", ".join(_DRIVES))))
    _wf_pts = None
    if _drive == "custom_current":
        try:
            _wf_pts = _parse_wf(waveform)
        except _ExcErr as _we:
            raise HTTPException(status_code=422, detail=str(_we))
    elif waveform:
        raise HTTPException(status_code=422, detail=(
            "a waveform was sent with drive=%r; the sampled waveform is only "
            "used by drive='custom_current'.  Either switch the source or drop "
            "the waveform — silently ignoring it would hide a wrong request."
            % (drive,)))
    if _drive == "pwm_voltage":
        if not (float(v_bus) > 0.0):
            raise HTTPException(status_code=422, detail=(
                "drive='pwm_voltage' needs a DC bus voltage; got v_bus=%r.  "
                "Use the machine's battery v_nom, or type one." % (v_bus,)))
        if not (float(f_switch) > 0.0):
            raise HTTPException(status_code=422, detail=(
                "drive='pwm_voltage' needs a switching frequency; got "
                "f_switch=%r Hz." % (f_switch,)))
        if not (float(v_phase_peak) > 0.0):
            raise HTTPException(status_code=422, detail=(
                "drive='pwm_voltage' needs the fundamental phase-voltage "
                "amplitude v_phase_peak [V peak]; got %r.  Run a current-drive "
                "simulation first and use its V₁." % (v_phase_peak,)))
        # ── MODULATION INDEX, on the PER-PHASE fundamental ────────────────
        # m = 2·V_phase/V_dc, and the modulator's V_phase is a POLE voltage
        # referred to the DC mid-point — the star-equivalent per-phase quantity.
        # In DELTA the drive is asked for the BRANCH voltage, which IS the line
        # voltage, so feeding it here straight was a gate √3 too large: it
        # refused this machine's peak duty at m = 1.53 where the real bridge
        # sits at 0.88 (user 2026-09-14 / PWM study §0.2).  `modulation_index`
        # divides by √3 in delta — equivalently, it is the branch V₁ against the
        # √3-scaled model bus, which is exactly what the source will chop.  The
        # 1.15 ceiling (sine-triangle WITH zero-sequence injection) is unchanged.
        _m_req = _mod_idx(float(v_phase_peak), float(v_bus),
                          star_delta=_sd_eff)
        if _m_req > _MAX_M:
            raise HTTPException(status_code=422, detail=(
                "modulation index m = 2·V_phase/V_bus = %.3f exceeds the "
                "%.2f linear-modulation limit (%s on the REAL %.1f V DC link)."
                "  Overmodulation (pulse dropping / six-step) is out of scope: "
                "raise V_bus above %.0f V, or lower %s below %.1f V."
                % (_m_req, _MAX_M,
                   ("%.1f V peak per phase — the %.1f V delta branch (= line) "
                    "fundamental over √3" % (float(v_phase_peak) / math.sqrt(3.0),
                                             float(v_phase_peak)))
                   if _eq_star else ("%.1f V peak" % float(v_phase_peak)),
                   float(v_bus),
                   math.ceil(float(v_bus) * _m_req / _MAX_M),
                   ("the branch V₁" if _eq_star else "V_phase_peak"),
                   0.5 * _MAX_M * _v_bus_model)))
    elif float(v_bus) or float(f_switch):
        raise HTTPException(status_code=422, detail=(
            "v_bus / f_switch were sent with drive=%r; they only mean "
            "something for drive='pwm_voltage'." % (drive,)))
    if _drive == "bldc_current":
        if not (float(i_block) > 0.0):
            raise HTTPException(status_code=422, detail=(
                "drive='bldc_current' needs the flat-top block amplitude "
                "i_block [A]; got %r.  It is NOT an rms — a 120° block of "
                "amplitude I carries I·√(2/3) rms, so the copper-matched "
                "equivalent of a sinusoidal %.4g A rms run is %.4g A."
                % (i_block, float(I_phase_rms),
                   float(I_phase_rms) * math.sqrt(1.5))))
    elif float(i_block):
        raise HTTPException(status_code=422, detail=(
            "i_block was sent with drive=%r; it only means something for "
            "drive='bldc_current'." % (drive,)))
    # Content hash of the imposed waveform for the transient cache key — the
    # samples themselves are physics, and a 20k-point list is not a dict key.
    _wf_key = ""
    if _wf_pts is not None:
        import hashlib as _hl_wf
        _wf_key = _hl_wf.sha1(
            (";".join("%.6g,%.6g" % (a, b) for a, b in _wf_pts))
            .encode("utf-8")).hexdigest()[:16]

    # ── THE PACK ON THE DC LINK ──────────────────────────────────────────
    # Parsed here, before anything is meshed, so a malformed battery is a 422
    # in milliseconds naming the field — and so `_batt` is available both to
    # the outer loops below and to the summary stash further down.
    _batt_raw = _parse_battery_payload(battery)
    _batt = None
    if _batt_raw is not None:
        from motor_ai_sim.simulation.battery import pack_from_config as _pfc_b
        _batt = _pfc_b(_batt_raw, v_oc=_batt_raw.get("v_oc"))
        if _batt is None:
            raise HTTPException(status_code=422, detail=(
                "the battery payload carries no usable open-circuit voltage: "
                "send v_oc, or v_nom, or both v_min and v_max.  Got %r"
                % (_batt_raw,)))

    # ── OUTER LOOPS: battery-coupled bus, and the max-charge search ──────
    # Both are loops whose body is ONE transient, so they run HERE — above the
    # solve lock (a nested acquire would deadlock) and above the cache, so each
    # inner solve is an ordinary fully-cached request that a user could have
    # typed by hand.  Everything they need is `_route_kwargs`.
    if bus_couple or charge_max:
        # A ledger PROBE must never start one of these loops: each iteration is
        # a whole transient, and the key the loop finally solves at is not the
        # key of the request that asked.  "No stored match" is the honest answer.
        if ledger_probe:
            return {"match": False, "computed_at": None,
                    "reason": "bus_couple / charge_max searches are not "
                              "recorded under the requested key"}
        if _drive not in ("pwm_voltage", "voltage"):
            raise HTTPException(status_code=422, detail=(
                "bus_couple / charge_max describe a machine feeding a battery "
                "THROUGH the bridge; they only mean something on an imposed-"
                "voltage source (drive='pwm_voltage' or 'voltage'), not on "
                "drive=%r." % (drive,)))
        if _batt is None:
            raise HTTPException(status_code=422, detail=(
                "bus_couple / charge_max need the pack: send `battery` "
                "(v_oc/v_nom, cells, r_int_mohm, capacity_ah).  Without an "
                "internal resistance there is nothing to iterate and without a "
                "capacity there is no C-rate — guessing either would be a "
                "number nobody measured presented as one somebody did."))
        return _charge_outer_loops(
            _route_kwargs, batt=_batt, mode_eff=_mode_eff,
            bus_couple=bool(bus_couple), charge_max=bool(charge_max),
            bus_iters=int(bus_iters), bus_tol_pct=float(bus_tol_pct),
            charge_max_steps=int(charge_max_steps),
            charge_max_evals=int(charge_max_evals))

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
    # NAMED FIELDS, in key order.  The tuple below is byte-identical to the one
    # this route has always built (same values, same order); the names exist
    # because the key now also lands on disk in the results ledger, where the
    # only question ever asked of it is "which field disagreed?" — the same
    # lesson `_field_snap_key_fields` learned twice.
    _sb_key_fields = OrderedDict((
        ("kind", "sb"),
        ("n_steps_per_period", int(n_steps_per_period)),
        ("n_periods", round(n_periods, 2)),
        ("gamma_deg", round(gamma_deg, 1)),
        ("I_phase_rms", round(I_phase_rms, 1)),
        ("mesh_size_mm", round(mesh_size_mm, 2)),
        ("min_size_mm", round(min_size_mm, 2)),
        ("outer_air_factor", round(outer_air_factor, 2)),
        ("n_sectors", int(n_sectors)),
        ("stator_fillet_mm", round(stator_fillet_mm, 2)),
        ("coil_temp_c", round(coil_temp_c, 1)),
        ("end_winding_factor", round(end_winding_factor, 3)),
        ("rotor_eddy", int(bool(rotor_eddy))),
        ("gap_layers", round(gap_layers, 1)),
        ("demag", int(bool(demag))),
        ("torque_filter", int(bool(torque_filter))),
        ("pole_copy", int(bool(pole_copy))),
        ("iron_template", int(bool(iron_template))),
        ("hi_fidelity", int(bool(hi_fidelity))),
        ("structured_gap", int(bool(structured_gap))),
        ("airgap_macro", int(bool(airgap_macro))),
        ("geo_mesh", int(bool(geo_mesh))),
        ("comp_mesh", tuple(sorted(_comp_mesh.items()))),
        ("drive", _drive),
        ("v_phase_peak", round(float(v_phase_peak), 2)),
        ("v_delta_deg", round(float(v_delta_deg), 1)),
        ("harm_ref", int(bool(harm_ref))),
        # PWM inverter: the bus and the carrier are PHYSICS.  Two runs
        # that differ only in f_switch are two different machines-under-
        # drive and must never share an entry — without these in the key,
        # changing the switching frequency and pressing Run replayed the
        # previous solve, which is precisely the failure the whole study
        # would be built on.  The waveform is keyed by content hash for
        # the same reason (and by hash, not by value, because 20k samples
        # have no business sitting in a dict key).
        ("v_bus", round(float(v_bus), 3)),
        ("f_switch", round(float(f_switch), 3)),
        ("waveform", _wf_key),
        ("i_block", round(float(i_block), 3)),
        ("element_order", int(element_order)),
        ("cfg_fingerprint", _cfp),
        # Speed: resolved (explicit argument or the config's), so an
        # rpm change invalidates the entry — the config's speed used to
        # be in NO key at all.
        ("rpm", round(_effective_rpm(rpm), 3)),
        ("winding", _effective_winding(n_parallel, connection)),
        # Star and delta are two different machines at the terminals, and in
        # delta the copper total carries a circulating loss star does not have.
        # Sharing one cache entry would replay the other connection's numbers.
        ("star_delta", _effective_star_delta(star_delta)),
        # Transposed / soldered / per-turn-parallel are three different
        # windings and they differ by kilowatts of copper — never one entry.
        ("strand_bonding", _effective_bonding(strand_bonding) or "auto"),
        # The d-axis reference: a run pinned to a given angle and a run
        # that measured its own are different operating points whenever
        # the two numbers differ, so they may not share a cache entry.
        ("daxis_deg", _effective_daxis(daxis_deg)),
        ("n_frames", int(n_frames) if include_frames else 0),
        # The coupled eddy solve is DIFFERENT physics (solved copper loss,
        # reaction currents in the magnets/shaft) — it must not share a
        # cache entry with the magnetostatic run.  field_snapshot is NOT
        # in the key: it changes nothing about the numbers, only whether
        # the last frame's field is kept for the viewer.
        ("eddy", int(bool(eddy))),
    ))
    # The pack does not change the FIELD, but it changes the summary's charging
    # block (R_pack sets the bus rise, the capacity sets the C-rate), and the
    # summary rides the cache entry.  Without this, editing r_int and pressing
    # Run replayed the previous pack's charging card — the same staleness the
    # material override was added to the key to kill.  Absent battery ⇒ the key
    # is byte-identical to what it has always been.
    # MAGNET TEMPERATURE — appended only when asked for, so a run with no magnet
    # temperature keeps the byte-identical cache key it has always had (same
    # rule as the battery block below).  When it IS asked for, it is physics:
    # the magnet's Br and its demag knee both move, so this run must never be
    # answered from the entry solved at the card's own temperature.
    if magnet_temp_c is not None:
        _sb_key_fields["magnet_temp_c"] = round(float(magnet_temp_c), 2)
    if _batt is not None:
        _sb_key_fields["battery"] = ("batt", round(_batt.v_oc, 4),
                                     round(_batt.r_pack_ohm, 7),
                                     round(_batt.capacity_pack_ah, 4),
                                     round(float(_batt.i_charge_max_a), 4))
    if _geo_ov:   # distinct cache entry per overridden geometry (no-geo key unchanged)
        _sb_key_fields["geo_ov"] = tuple(sorted(_geo_ov.items()))
    _sb_key = tuple(_sb_key_fields.values())
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
        drive=_drive, element_order=element_order,
        magnet_temp_c=magnet_temp_c,
        excitation=("%g/%g" % (float(v_bus), float(f_switch))
                    if _drive == "pwm_voltage"
                    else (_wf_key if _drive == "custom_current"
                          else ("%g" % float(i_block)
                                if _drive == "bldc_current" else ""))),
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
        res = _refresh_summary_shape(res)
        if res.get("field_snapshot") == _have:
            return res
        out = dict(res)
        out["field_snapshot"] = _have
        out["field_snapshot_eddy"] = bool(eddy) and _have
        return out

    # READER #1 of `_fem_transient_cache` — the Run path.  Gated on
    # `_memo_allowed()`: a user-initiated Run (GET here, POST /api/kernel/run,
    # the animation viewer) never reads it and therefore always solves.  Only an
    # internal re-entry — the bus-coupling / charge-max outer loops, a background
    # passport probe — may be served the memo, because those revisit the very
    # same operating point dozens of times inside one request.
    if not fresh and _memo_allowed() and _sb_key in _fem_transient_cache:
        return _with_live_snapshot_flag(_fem_transient_cache[_sb_key])
    # RESTORE path (page open / tab switch): NEVER recompute.  Hand back the
    # last saved transient — flagged stale if its params differ from those
    # requested — or signal that nothing has ever been computed so the UI can
    # show "press Run" instead of spinning up a solve.
    if restore:
        _ref_res = _last_transient_ref.get("result")
        if _ref_res is not None:
            _out = dict(_refresh_summary_shape(_ref_res))
            # belt AND braces with _save_last_transient's strip: a restore must
            # never ship per-frame fields — a 725 MB response is one no browser
            # survives, and the panel then silently shows a stale summary
            _out.pop("frames", None)
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
                # WHICH keys moved — the two prints alone cost an hour of
                # guessing on 2026-09-13 (a verdict flipped for ~5 min after a
                # restart and froze a dimmed dashboard on the user's screen).
                # The run kept the client machine it was solved with
                # (`_summary_args.geo_override`); hold it against the client
                # machine of THIS request and against the live service object.
                try:
                    _ran_ov = ((_out.get("_summary_args") or {}).get("geo_override")
                               or {})
                    _now_ov = dict(_geo_ov or {})
                    from motor_ai_sim.services.geometry_service import (
                        get_current_geometry as _gcg_dbg)
                    _live_d = _gcg_dbg().to_dict()
                    def _kdiff(a, b):
                        return sorted(k for k in set(a) | set(b)
                                      if a.get(k) != b.get(k))[:12]
                    log.warning(
                        "restore stale-geometry detail: override present=%s "
                        "(%d keys; run had %d) | run-vs-request override diff=%s "
                        "| run-override-vs-live diff=%s | request-override-vs-live "
                        "diff=%s", bool(_geo_ov), len(_now_ov), len(_ran_ov),
                        [(k, _ran_ov.get(k), _now_ov.get(k))
                         for k in _kdiff(_ran_ov, _now_ov)] if _ran_ov and _now_ov
                        else "n/a",
                        [(k, _ran_ov.get(k), _live_d.get(k))
                         for k in _kdiff(_ran_ov, _live_d)] if _ran_ov else "n/a",
                        [(k, _now_ov.get(k), _live_d.get(k))
                         for k in _kdiff(_now_ov, _live_d)] if _now_ov else "n/a")
                except Exception:      # noqa: BLE001 — a diagnostic must never break a restore
                    log.debug("restore stale-geometry detail unavailable", exc_info=True)
            # The persisted result remembers that ITS run kept a field snapshot;
            # that snapshot lives in memory only, and a stale restore is not even
            # the same run.  Report what is actually available now.
            _out["field_snapshot"] = bool(
                not _out["stale"] and _fsnap_key in _transient_field_snap)
            _out["field_snapshot_eddy"] = bool(_out["field_snapshot"] and eddy)
            return _out
        return {"restored": False, "stale": False}

    # ── RESULTS LEDGER — "this exact run already exists on disk" ─────────────
    # User, 2026-09-05: he set 667.4 A peak in the morning, ran, changed the
    # current, ran, came back to 667.4 A with nothing else touched — and had to
    # re-solve a run whose answer was already stored.  «Не надо Recent runs —
    # нужно просто сканировать результаты: не совпадают ли они с уже
    # проведёнными».
    #
    # This is NOT the in-memory memo coming back (`_memo_allowed` — a Run still
    # never reads that).  The difference is what makes it honest:
    #   * the match is the EXACT `_sb_key`, geometry+material fingerprint and
    #     all, read back from the file and re-compared field by field;
    #   * the answer is LABELLED `ledger_hit` and the card says so;
    #   * `fresh=true` — the card's "Recompute" — always solves.
    # Only a user Run may be served: `_memo_allowed()` is true exactly for the
    # internal re-entries (bus-coupling / charge-max loops) and background
    # probes, which have their own memo and their own rules.
    if ledger and not fresh and not _memo_allowed():
        _led = _ledger_lookup(_sb_key)
        if ledger_probe:
            return {"match": _led is not None,
                    "computed_at": (_led or {}).get("computed_at")}
        if _led is not None:
            _out = dict(_refresh_summary_shape(_led.get("result") or {}))
            _out.pop("frames", None)     # never stored; never implied
            _out["ledger_hit"] = True
            _out["ledger_computed_at"] = _led.get("computed_at")
            # ALIAS (2026-09-22): `computed_at` is already in `_out` (copied
            # straight off the stored `result`, which carries its own); what
            # is missing is the one flag every other panel's history hit
            # carries (mechanical.py's _ROTOR_STRESS_HISTORY, coupled.py's
            # _COUPLED_HISTORY) so the web notice ("Loaded from history —
            # computed … · Recompute") is ONE piece of UI code across all
            # four panels rather than one per backend mechanism. This ledger
            # already IS this route's persistent layer — see the module note
            # above `_ledger_dir()`: built well before `motor_ai_sim.
            # run_history` existed, exact-key, `fresh`-gated, survives a
            # restart. It is not re-implemented on top of
            # run_history.RunHistory; only its response vocabulary is aligned.
            _out["served_from_history"] = True
            # NOT `restored`: that word means "the last transient, shown while
            # you decide whether to run", and the UI escalates a stale restore
            # to a red banner.  This is a full match on today's inputs.
            _out["restored"] = False
            _out["stale"] = False
            # The field views work off the in-memory snapshot store, which this
            # path deliberately does not touch (a load is not a solve, and
            # wiping the store would cost the user the pictures of the run that
            # IS in it).  Report what is actually there for this key.
            _out["field_snapshot"] = _fsnap_key in _transient_field_snap
            _out["field_snapshot_eddy"] = bool(eddy) and _out["field_snapshot"]
            # The RESTORE store follows the screen.  Without this, a reload
            # finds a NEWER solve on disk (the run the user made in between)
            # than the one they just loaded, and the panel's mount probe adopts
            # it whole — the numbers would change under the user with no word
            # said, which is the exact failure this whole area keeps fighting.
            # The run JOURNAL is skipped — see `_save_last_transient`.
            _save_last_transient(_sb_key, _out, journal=False)
            log.info("run ledger HIT (%s) — identical parameters, loaded "
                     "instead of solved", _led.get("computed_at"))
            return _out
    elif ledger_probe:
        # A probe on a request that would never consult the ledger anyway says
        # WHY, instead of implying "nothing stored".
        return {"match": False, "computed_at": None,
                "reason": ("fresh=true forces a solve" if fresh else
                           "ledger=false — this caller needs a fresh solve"
                           if not ledger else
                           "internal re-entry — the ledger is for user runs")}

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
        # READER #2 — the same memo, re-checked under the lock for a twin that
        # finished while we waited.  Same gate: a user Run solves even if an
        # identical one just finished (that is the whole point of pressing Run);
        # the concurrent-duplicate storm it used to absorb is handled by the lock
        # itself and, for the field views, by field_jobs' dedupe queue.
        if not fresh and _memo_allowed() and _sb_key in _fem_transient_cache:
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
            # demag adds a pre-pass sweep over the period — on BOTH paths since
            # 2026-09-05: magnetostatic runs prepend it as `_dmskip` frames,
            # coupled-eddy runs splice it at θ<0 right after the eddy handoff
            # (the Br ratchet may not see the σ·∂A/∂t start-up transient — the
            # user's "second run always differs from the first").  The eddy
            # warm-up frames on top of it are a moving target by construction,
            # so the solver's own progress callback corrects this estimate at
            # the first frame; ×2 is the right pre-run guess for both.
            _est_total *= 2
        _fem_transient_progress["current"] = {
            "running": True, "step": 0, "total": _est_total,
            "elapsed_s": 0.0, "eta_s": 0.0, "ts_start": _t.time(),
            "phase": ("fem-solve (sliding-band, demag)" if demag
                      else "fem-solve (sliding-band)"),
            "composition": "",
        }
        def _sb_progress(_done, _total, _phase=None, _composition=None):
            # Cooperative cancel: this callback is the one hook that fires at
            # the top of EVERY frame (settling, warm-up and demag pre-pass
            # included), so it is where the Stop button takes effect.
            if run_id and _JOBS.is_cancelled(run_id):
                raise _RunCancelled(run_id)
            _cur = _fem_transient_progress["current"]
            _cur["step"] = int(_done)
            _cur["total"] = int(_total)
            # HOW that total is made up, in the SOLVER's own words.  The strip
            # used to derive this client-side from "total = 3 x steps/period",
            # which the PWM mixed-resolution schedule (coarse sinusoid settle +
            # fine pre-roll + fine reported window) makes false — and only the
            # code that built the schedule can describe it.
            if _composition:
                _cur["composition"] = str(_composition)
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
            # THE CURRENT SETPOINT IS THE TERMINAL (line) CURRENT — the three
            # leads between the machine and the inverter, in both connections
            # (user 2026-09-12: "эти параметры задаются для 3 проводов, которые
            # идут с мотора на инвертор").  The solver drives the WINDING, and
            # in delta a winding carries the line current over sqrt(3): that
            # division happens HERE, once, so the field sees the ampere-turns
            # the inverter actually produces.  Star: winding = line, no-op.
            _I_wind = float(I_phase_rms) / (
                math.sqrt(3.0) if _effective_star_delta(star_delta) == "delta"
                else 1.0)
            _sbres = em_transient_eval(
                n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
                gamma_deg=float(gamma_deg), I_phase_rms=_I_wind,
                rpm=(None if rpm is None else float(rpm)),
                n_parallel=(None if n_parallel is None else int(n_parallel)),
                connection=(None if connection is None else str(connection)),
                star_delta=_effective_star_delta(star_delta),
                strand_bonding=_effective_bonding(strand_bonding),
                daxis_deg=_effective_daxis(daxis_deg),
                mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
                outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
                n_sectors=int(n_sectors), stator_fillet_mm=float(stator_fillet_mm),
                coil_temp_c=float(coil_temp_c),
                magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
                end_winding_factor=float(end_winding_factor),
                rotor_eddy=bool(rotor_eddy), demag=bool(demag),
                torque_filter=bool(torque_filter), pole_copy=bool(pole_copy),
                iron_template=bool(iron_template), geo_mesh=bool(geo_mesh),
                component_mesh_mm=_comp_mesh, geo_override=_geo_ov,
                progress_cb=_sb_progress, hi_fidelity=bool(hi_fidelity),
                structured_gap=bool(structured_gap),
                airgap_macro=bool(airgap_macro),
                drive=_drive,
                v_phase_peak=float(v_phase_peak),
                v_delta_deg=float(v_delta_deg),
                # THE MODEL BUS, not the request's.  Star: identical.  Delta:
                # √3 × the real DC link, which is what makes the star circuit's
                # branch voltage the real bridge's LINE voltage harmonic for
                # harmonic (see the star-equivalent block above).
                v_bus=float(_v_bus_model), f_switch=float(f_switch),
                waveform=_wf_pts, i_block=float(i_block),
                element_order=int(element_order),
                return_frames=int(n_frames) if include_frames else 0,
                # THE HONEST INDUCTANCES OF THIS POINT: frozen permeability on
                # the frames this run already solves, so the card and the
                # report can stop quoting a chord (ψd − ψ_PM)/i_d that the
                # loaded iron's ψ_PM sag dominates (client review, 2026-09-20).
                # Three extra back-solves on four frames' own matrices.
                inc_ldq=True,
                # Coupled σ·∂A/∂t solve — only when the caller asked for it.
                eddy=bool(eddy),
                # Keep the last frame's field for the J⟳ / Loss views.  Free:
                # the frame is already solved; this stops it being discarded.
                return_field=bool(field_snapshot))
            # ── ΔP_harm (voltage drive): current-drive REFERENCE at the
            # extracted fundamental (I₁, γ₁), so the comparison runs at a
            # MATCHED fundamental current — the loss difference is then
            # purely the parasitic harmonic currents' watt cost.
            # Also for the PWM source, where the reference is even more to the
            # point: ΔP_harm is then exactly the watt cost of the SWITCHING
            # ripple, measured against an ideal sinusoid at the same fundamental
            # current the inverter actually produced.
            if _drive in ("voltage", "pwm_voltage") and harm_ref:
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
                            # Same d-axis reference as the main run.  γ₁ is
                            # measured against that axis, so with a pinned
                            # d-axis an unpinned reference re-measured its own
                            # zero and solved a SHIFTED current angle —
                            # ΔP_harm/T_ref then compared mismatched operating
                            # points.
                            daxis_deg=_effective_daxis(daxis_deg),
                            mesh_size_mm=float(mesh_size_mm),
                            min_size_mm=float(min_size_mm),
                            outer_air_factor=float(outer_air_factor),
                            gap_layers=float(gap_layers),
                            n_sectors=int(n_sectors),
                            stator_fillet_mm=float(stator_fillet_mm),
                            coil_temp_c=float(coil_temp_c),
                            # Same magnet, at the same temperature: the
                            # reference's only allowed difference is the drive.
                            magnet_temp_c=(None if magnet_temp_c is None
                                           else float(magnet_temp_c)),
                            end_winding_factor=float(end_winding_factor),
                            # ROTOR eddy: the reference must carry whatever the
                            # voltage run carried.  It used to be force-dropped
                            # on BOTH, because the solver dropped it on every
                            # imposed-voltage run; now that the conducting rotor
                            # is solved inside the (A, U, i) Newton, the
                            # reference runs it too — otherwise ΔP_harm would
                            # EXCLUDE the magnet-loss harmonic cost, which under
                            # PWM is exactly the term the comparison is for
                            # (HF air-gap harmonics raise the magnet loss above
                            # the sinusoid's).  The two flip together, including
                            # when SB_VDRIVE_ROTOR_EDDY=0 puts the drop back:
                            # the solver reports what it actually solved in
                            # rotor_eddy_solved, so the reference mirrors the
                            # RUN, not the request.
                            #
                            # The COUPLED eddy solve is a different matter and
                            # this line used to drop it as well, on a comment
                            # ("eddy is unsupported in the voltage solve") that
                            # stopped being true when the bordered (A, U, i)
                            # Newton landed: an eddy voltage run was compared
                            # against a MAGNETOSTATIC current reference, so
                            # ΔP_harm silently contained the entire solved AC
                            # copper loss instead of the harmonic cost alone.
                            # The reference's only allowed difference from the
                            # run it is measuring is the DRIVE.
                            eddy=bool(eddy),
                            rotor_eddy=bool(_sbres.get("rotor_eddy_solved")),
                            demag=bool(demag),
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
            # The substitution is stated in the payload, beside the numbers it
            # produced — never left for a reader to infer from a bus voltage
            # that is √3 too big (user 2026-09-14 / PWM study §2.2 B1).
            if _eq_star:
                _mark_equivalent_star(_sbres, v_bus_real=float(v_bus),
                                      v_bus_model=float(_v_bus_model),
                                      drive=_drive)
        except _RunCancelled:
            _fem_transient_progress["current"] = {
                "running": False, "step": 0, "total": 0, "elapsed_s": 0.0,
                "eta_s": 0.0, "ts_start": 0.0, "phase": "cancelled"}
            log.info("fem_transient run %s cancelled by the Stop button", run_id)
            raise HTTPException(status_code=499, detail="simulation stopped")
        except _ExcErr as _ee:
            # An excitation the caller described but that cannot be built — a
            # carrier too slow to synthesise the requested fundamental, a
            # delay/gain compensation that would need overmodulation.  That is a
            # REQUEST error, not a 500: it comes back as a 422 naming the number
            # to change, exactly like the pre-solve checks at the top of this
            # route.  (Those catch everything checkable without the machine's
            # d-axis; these need the solved frame.)
            raise HTTPException(status_code=422, detail=str(_ee))
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
        # "Is this the motor on screen?" is a question about the MACHINE, not
        # about how the request spelled it: every UI run arrives through the
        # kernel WITH an explicit geo payload of the on-screen machine, and
        # the raw `if _geo_ov` test read that as a candidate eval — so user
        # runs lost their field snapshot AND (below) were never persisted as
        # the last transient (measured live: a restart restored a run from
        # hours earlier).  The resolved fingerprint answers it properly.
        # …and a BACKGROUND solve is never "the motor on screen" even when it
        # solves the very same geometry (passport sweeps — see _BACKGROUND_RUN).
        _is_live_machine = ((not _geo_ov or
                             _sbres.get("geo_fingerprint")
                             == _geometry_fingerprint(None))
                            and not _BACKGROUND_RUN.get())
        if not _is_live_machine:
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
            # ── …and once more, PER DUTY (2026-09-09) ────────────────────────
            # The store above holds a handful of snapshots for ONE machine and
            # the next run evicts them, so a report of a configuration with
            # several duties could draw one duty's |B| and loss maps and had to
            # say so in the caption.  The user asked for all of the solved
            # fields to be kept — *"давай сделаем сохранение всех полей
            # моделирования, как электромагнитных, так и тепловых и
            # механических"* — so this run's mesh, |B|, loss density and demag
            # coefficient are filed under the duty the catalog context names
            # (~0.31 MB compressed on the 200 mm machine).  Only here: this is
            # the one place a live-machine run's field is persisted at all, and
            # a candidate eval / background solve never gets this far.
            try:
                from motor_ai_sim import duty_fields as _df
                _df.save_active(
                    "em", _transient_field_snap.get(_fsnap_key),
                    geometry_fingerprint=_sbres.get("geo_fingerprint"),
                    computed_at=_sbres.get("computed_at"))
            except Exception:      # noqa: BLE001 — never fails a run
                log.debug("transient: per-duty field not stored", exc_info=True)
        # Bench Ld/Lq ride with every live-machine run (user: "во время
        # расчёта посчитай индуктивность" — no separate button).  Once per
        # machine+connection: a cache hit costs a file read, a miss costs
        # ~45 s of three quick solves appended to this run.  Candidate evals
        # skip it — an optimizer must not pay 45 s per candidate.
        if _is_live_machine:
            try:
                from motor_ai_sim.config import get_config as _gcb
                _bconn = str(_sbres.get("connection")
                             or (_gcb().get("winding") or {}).get("connection")
                             or "")
                if _bench_read(_sbres.get("geo_fingerprint"), _bconn) is None:
                    log.info("bench Ld/Lq: not cached for this machine — "
                             "measuring (3 quick solves)…")
                    _bench_compute(_geo_ov, _bconn)
            except Exception:
                log.exception("bench Ld/Lq probe failed — the summary will "
                              "carry none (run itself unaffected)")
        # Say it in the payload too: the Simulation tab can tell the user the
        # J⟳ / Loss views are ready, and WHY they are (or are not).
        _sbres["field_snapshot"] = bool(_fld_snap is not None)
        _sbres["field_snapshot_eddy"] = bool(eddy) if _fld_snap is not None else False
        # SHARED helper (_build_transient_summary) so the direct route, the
        # legacy remesh path AND the kernel/solver.em_transient path all use the
        # SAME formula and every run returns a populated `summary`.
        # The pack rides WITH the result, so a cache hit / a restore / a
        # summary-shape rebuild all reconstruct the charging card from the same
        # numbers the run was solved against instead of from whatever the panel
        # holds now.  Absent battery ⇒ the key is absent and every existing
        # consumer sees the payload it has always seen.
        if _batt is not None:
            _sbres["battery_input"] = _batt.as_dict()
        try:
            # The summary carries the PANEL's gamma (16, not 196): the card's
            # staleness check compares against the panel input, and the mode
            # chip says which way the 180 went; the solver result keeps the
            # effective angle in gamma_effective_deg.
            _sbres["summary"] = _build_transient_summary(
                _sbres, I_phase_rms=_I_wind, gamma_deg=_gamma_panel,
                coil_temp_c=coil_temp_c, geo_override=_geo_ov,
                mode_requested=_mode_eff)
            # The exact build args, stashed WITH the cached result: a cache hit
            # re-serves the summary as stored, so a summary-shape change (a new
            # derived quantity like Km or the rotor inertia) never reached any
            # cached point — the card just silently lacked the new cells.  With
            # the args recorded, the hit path can rebuild the summary with the
            # CURRENT code (see _refresh_summary_shape).
            _sbres["_summary_args"] = {
                "I_phase_rms": _I_wind,
                "gamma_deg": float(_gamma_panel),
                "coil_temp_c": float(coil_temp_c),
                "geo_override": _geo_ov,
                "mode_requested": _mode_eff}
        except Exception as _se:
            # A summary-build failure MUST be visible (not a silently frozen
            # card set): log it AND attach an error marker so the frontend can
            # surface it instead of keeping stale numbers.
            log.exception("SB summary build failed")
            _sbres["summary_error"] = f"{type(_se).__name__}: {_se}"
        # WRITER of `_fem_transient_cache` (kept: `_refresh_caches_for_run`
        # reduces the dict to this one entry a line below, and the opt-in memo
        # readers above need it there; a candidate eval that never refreshes
        # still gets its own memo entry for the optimizer's inner loops).
        _fem_transient_cache[_sb_key] = _sbres
        # Persist for "restore last simulation" on reload — but ONLY the user's
        # MAIN motor. Per-candidate evals (optimizer / kernel runs with a geo
        # override) must never clobber the saved simulation, and parallel
        # optimizer subprocesses must not race on the shared store file.
        if _is_live_machine:
            # THE one place the physics caches are refreshed.  Same condition as
            # the persist below on purpose: a candidate eval / a background
            # passport point is not the motor on screen, and letting it wipe the
            # user's field snapshot mid-optimization is the eviction bug this
            # store was already guarded against.
            _refresh_caches_for_run(
                _sb_key, _sbres,
                snap_key=(_fsnap_key if _fld_snap is not None else None))
            _save_last_transient(_sb_key, _sbres)   # survive a back-end restart
            # …and record it in the results ledger, so coming BACK to this
            # operating point later today loads it instead of re-solving (user,
            # 2026-09-05).  Same `_is_live_machine` gate as the two above, for
            # the same reason: a candidate eval / a background passport point is
            # not the motor on screen and must not fill the user's ledger.
            # `fresh=true` lands here too — that is the "Recompute" replacing
            # the stored entry with the run the user just forced.
            _ledger_write(_sb_key, _sbres, _sb_key_fields)
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
# 7a-ter.  The results ledger — endpoints
# ─────────────────────────────────────────────────────────────────────────────

def get_fem_transient_ledger_match(**kwargs):
    """Is there a STORED result for exactly this request?  → {match, computed_at}

    Cheap: it builds the key, hashes it and stats one file — no mesh, no solve.
    (The one thing it can be slow for is a machine whose d-axis has never been
    measured; the key holds `None` there — "measure it" — so even that costs
    nothing here, the measurement happens inside a real solve.)

    The signature is COPIED from `get_fem_transient` below, deliberately: this
    endpoint must build the byte-identical key from the byte-identical query
    parsing, or it would promise a stored result the Run then fails to find —
    a lie in exactly the direction the user does not tolerate.  `restore` and
    `ledger_probe` are removed because this route pins them.
    """
    kwargs.pop("restore", None)
    kwargs.pop("ledger_probe", None)
    return get_fem_transient(restore=False, ledger_probe=True, **kwargs)


import inspect as _inspect_lm      # noqa: E402 — used immediately below
_lm_sig = _inspect_lm.signature(get_fem_transient)
get_fem_transient_ledger_match.__signature__ = _lm_sig.replace(
    parameters=[_p for _n, _p in _lm_sig.parameters.items()
                if _n not in ("restore", "ledger_probe")])
router.add_api_route("/physics/fem_transient/ledger_match",
                     get_fem_transient_ledger_match, methods=["GET"])


@router.get("/ledger")
def get_run_ledger():
    """What the results ledger holds for the LIVE machine's config directory.

    Metadata only — key fields, when it was computed, how big it is.  Read-only
    and owner-gated by the same `_GATED` table as GET /api/simulation/caches:
    it describes the SHARED machine's stored work.
    """
    _es = _ledger_entries()
    return {
        "dir": str(_ledger_dir()),
        "count": len(_es),
        "bytes": sum(int(e.get("bytes") or 0) for e in _es),
        "keep_per_geometry": _LEDGER_KEEP_PER_GEO,
        "max_bytes": _LEDGER_MAX_BYTES,
        "entries": _es,
    }


@router.delete("/ledger")
def delete_run_ledger():
    """Throw the whole ledger away.

    Unlike the physics caches this is a record of WORK DONE, not stale physics,
    so nothing clears it automatically — a stored entry can only be served on an
    exact key match, and a key that no longer describes the machine simply never
    matches again.  This exists for the case the user wants the disk back.
    """
    n = _ledger_clear()
    log.info("run ledger cleared by request: %d entries deleted", n)
    return {"deleted": n}


# ─────────────────────────────────────────────────────────────────────────────
# 7a-bis.  Bench-style Ld/Lq probe — the LCR-meter measurement, simulated
# ─────────────────────────────────────────────────────────────────────────────

#: One probe current for everyone: the cache key includes it, and a per-user
#: knob would just fragment the cache for a value nobody tunes on a bench.
_BENCH_I_PROBE_ARMS = 2.0


def _bench_cache_path():
    from motor_ai_sim.workspace import root as _ws_root
    return _ws_root() / ".bench_ldq.json"


def _bench_key(fp: str, conn: str, i_probe: float = _BENCH_I_PROBE_ARMS) -> str:
    return "%s_C%s_I%.2f" % (fp, conn or "cfg", float(i_probe))


def _bench_read(fp: Optional[str], conn: Optional[str],
                i_probe: float = _BENCH_I_PROBE_ARMS) -> Optional[dict]:
    """Cache-only lookup — the summary builder calls this on EVERY build
    (including restore rebuilds), so it must never start a solve."""
    if not fp:
        return None
    try:
        if not conn:
            from motor_ai_sim.config import get_config as _gc0
            conn = (_gc0().get("winding") or {}).get("connection") or ""
        import json as _j
        return (_j.loads(_bench_cache_path().read_text(encoding="utf-8"))
                .get(_bench_key(fp, str(conn or ""), i_probe))) or None
    except Exception:
        return None


def _bench_compute(geo_ov: Optional[dict], conn: str,
                   i_probe: float = _BENCH_I_PROBE_ARMS) -> dict:
    """The three-solve bench measurement (q-probe, d-probe, cached ψ_PM).
    Raises on any frame/convergence doubt; writes the cache on success."""
    import json as _j, math as _m, time as _t2
    from motor_ai_sim.config import get_config as _gc
    from motor_ai_sim.simulation.fem_solver_2d import (em_transient_eval,
                                                       noload_psi_pm)
    _fp = _geometry_fingerprint(geo_ov)
    # The MACHINE BEING PROBED, not whatever is loaded: with an override in
    # hand the pole count, symmetry and mesh scale must come from IT, or a
    # catalog motor is probed with the live machine's sector count and mesh
    # (zero-touch passport generation hits exactly this path).
    _geo_cfg = dict(geo_ov) if geo_ov else dict(_gc().get("geometry") or {})
    _wind = dict(_gc().get("winding") or {})
    _pp = int(_geo_cfg.get("num_poles", 0)) // 2
    if _pp <= 0:
        raise HTTPException(422, detail="machine has no poles configured")
    # Same cheap-calibration knobs as the ψ_PM solve (noload_psi_pm), so all
    # three solves share one mesh and one d-axis frame.
    _cal_ns = _m.gcd(int(_geo_cfg.get("num_slots") or 1),
                     int(_geo_cfg.get("num_poles") or 1)) or 1
    _cal_D = float(_geo_cfg.get("stator_diameter") or 0.0) or 40.0
    _cal_mesh = round(min(2.0, max(0.5, 1.4 * _cal_D / 150.0)), 2)
    _knobs = dict(n_steps_per_period=6, n_periods=1.0,
                  mesh_size_mm=_cal_mesh, min_size_mm=0.3,
                  outer_air_factor=1.2, gap_layers=2.0,
                  n_sectors=_cal_ns if _cal_ns >= 2 else -1,
                  coil_temp_c=120.0, rotor_eddy=False, iron_template=True,
                  structured_gap=True, geo_mesh=True, element_order=2,
                  geo_override=geo_ov,
                  **({} if not conn else {"connection": conn}))
    _t0 = _t2.time()
    # q-probe (γ = 0 → current on q).  daxis auto-calibrates (cached per
    # machine) and its angle is reused verbatim by the other two solves.
    _rq = em_transient_eval(gamma_deg=0.0, I_phase_rms=float(i_probe),
                            daxis_deg=None, **_knobs)
    _dax = float(_rq.get("daxis_deg") or 0.0)
    # d-probe (γ = 90 → current on d).
    _rd = em_transient_eval(gamma_deg=90.0, I_phase_rms=float(i_probe),
                            daxis_deg=_dax, **_knobs)
    _pm, _q0 = noload_psi_pm(_geo_cfg, _wind, _pp, 0, _dax,
                             geo_override=geo_ov,
                             connection=(conn or None))
    if abs(_pm) > 1e-9 and abs(_q0) > 0.05 * abs(_pm):
        raise HTTPException(500, detail=(
            "no-load ψq is %.1f%% of ψ_PM — the dq frame is suspect, refusing "
            "to report bench inductances off it" % (100 * abs(_q0 / _pm))))
    _ipk = float(i_probe) * _m.sqrt(2.0)
    _iq = float(_rq.get("i_q_A") or 0.0)
    _idp = float(_rd.get("i_d_A") or 0.0)
    if abs(_iq) < 0.5 * _ipk or abs(_idp) < 0.5 * _ipk:
        raise HTTPException(500, detail=(
            "probe current did not land on its axis (i_q %.2f A, i_d %.2f A "
            "for a %.2f A-peak probe) — frame error, not a measurement"
            % (_iq, _idp, _ipk)))
    _Lq = 1e3 * (float(_rq.get("psi_q_Wb") or 0.0) - float(_q0)) / _iq
    _Ld = 1e3 * (float(_rd.get("psi_d_Wb") or 0.0) - float(_pm)) / _idp
    out = {
        "Ld_mH": round(_Ld, 4), "Lq_mH": round(_Lq, 4),
        "Lq_over_Ld": (round(_Lq / _Ld, 3) if abs(_Ld) > 1e-9 else None),
        "psi_pm_mWb": round(1e3 * float(_pm), 3),
        "I_probe_arms": float(i_probe),
        "daxis_deg": round(_dax, 3),
        "connection": conn or None,
        "geo_fingerprint": _fp,
        "method": ("small-signal probe at the I=0 iron state — bench/LCR "
                   "equivalent; loaded (chord) values differ under "
                   "saturation"),
        "solve_time_s": round(_t2.time() - _t0, 1),
        "cached": False,
    }
    try:
        _cpath = _bench_cache_path()
        _all = {}
        try:
            _all = _j.loads(_cpath.read_text(encoding="utf-8"))
        except Exception:
            pass
        _all[_bench_key(_fp, conn, i_probe)] = {
            k: v for k, v in out.items() if k != "cached"}
        _cpath.write_text(_j.dumps(_all, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    except Exception:
        log.exception("bench_ldq: cache write failed (result still returned)")
    return out


def catalogue_ldq0(geo_ov: Optional[dict], *, daxis_deg: float,
                   connection: Optional[str] = None,
                   magnet_temp_c: float = 20.0) -> Optional[dict]:
    """CATALOGUE Ld/Lq — incremental, no load, 20 °C — for THIS machine.

    The config plumbing around ``fem_solver_2d.noload_incremental_ldq``, in one
    place: which geometry (the override when a catalog motor is being probed,
    the live machine otherwise), which winding, how many pole pairs.  The same
    shape ``_bench_compute`` has, and for the same reason — a probe that reads
    the live config when it was handed another machine measures the wrong one.

    Never raises: a machine whose no-load probe will not converge simply has no
    catalogue inductances, and inventing them from a loaded chord is what this
    whole change exists to stop.
    """
    from motor_ai_sim.config import get_config as _gc
    from motor_ai_sim.simulation.fem_solver_2d import noload_incremental_ldq
    try:
        _geo_cfg = dict(geo_ov) if geo_ov else dict(_gc().get("geometry") or {})
        _wind = dict(_gc().get("winding") or {})
        _pp = int(_geo_cfg.get("num_poles", 0)) // 2
        if _pp <= 0:
            return None
        return noload_incremental_ldq(
            _geo_cfg, _wind, _pp, float(daxis_deg),
            geo_override=geo_ov, connection=(connection or None),
            magnet_temp_c=float(magnet_temp_c),
            coil_temp_c=float(magnet_temp_c))
    except Exception:   # noqa: BLE001 — a catalogue row is never worth a 500
        log.warning("catalogue Ld0/Lq0 probe failed", exc_info=True)
        return None


@router.get("/pwm_waveform")
def get_pwm_waveform(
    v_bus:        float = Query(..., description="DC link voltage [V]"),
    f_switch:     float = Query(..., description="carrier frequency [Hz]"),
    I_phase_rms:  float = Query(..., description="terminal phase current setpoint [A rms]"),
    gamma_deg:    float = 0.0,      # current angle, q-axis = 0 (panel convention)
    rpm:          Optional[float] = None,   # None = the shared config's speed
    R_phase_ohm:  Optional[float] = None,   # None = bench/last-run value
    Ld_mH:        Optional[float] = None,   # None = bench measurement
    Lq_mH:        Optional[float] = None,
    psi_pm_mWb:   Optional[float] = None,
    emf_waveform: Optional[str] = None,     # [[theta_e_deg, e_A_V], ...] at THIS rpm
    n_samples:    int  = 0,                 # 0 = 32 per carrier (>= 360)
    connection:   Optional[str] = None,
    geo:          Optional[str] = None,
    poles:        Optional[int] = None,     # None = the loaded machine's pole count
):
    """Synthesise the phase current a PWM inverter forces through this machine —
    pure Python, about a second, no FEM.

    This is the "PWM calculator": it integrates the synchronous-frame circuit
    equations with the inverter's instantaneous (chopped) phase voltages, and
    returns the resulting phase-A current sampled over one electrical period,
    in exactly the shape ``drive="custom_current"`` takes.  So the cheap route —
    synthesise the ripple in a constant-L model, impose it on the FEM — is
    available WITHOUT an external tool, which is the route the published
    PWM-excitation studies use and the one this solver has to be comparable
    with.

    R, Ld, Lq and ψ_PM default to the machine's own measured values (the same
    small-signal bench probe ``/physics/bench_ldq`` serves), so the calculator
    describes THIS motor rather than a textbook one.  It is still a CONSTANT-L
    model: for the honest answer — saturation, the real back-EMF shape, the eddy
    reaction, all in the loop — run ``drive="pwm_voltage"`` instead.

    See ``simulation/pwm.synthesize_pwm_current`` for the model, the regulator
    and what each returned number means.
    """
    from motor_ai_sim.config import get_config as _gc_p
    from motor_ai_sim.simulation.pwm import (
        synthesize_pwm_current as _synth, ExcitationError as _ExcErr2,
        parse_waveform as _pwf)
    _geo_ov = _parse_geo_override(geo)
    _cfg = _gc_p()
    _geo = {**dict(_cfg.get("geometry") or {}), **(_geo_ov or {})}
    _poles = int(poles or _geo.get("num_poles") or 0)
    if _poles < 2:
        raise HTTPException(422, detail=(
            "cannot tell the pole count of the loaded machine (num_poles=%r) "
            "— pass ?poles=" % (_geo.get("num_poles"),)))
    _rpm = _effective_rpm(rpm)
    # Ld/Lq/ψ_PM: the machine's own measurement unless the caller pins them.
    # Missing ones are FILLED from the bench probe rather than guessed — a
    # synthesis run on default inductances would produce a plausible-looking
    # ripple belonging to no motor at all.
    _need_bench = (Ld_mH is None or Lq_mH is None or psi_pm_mWb is None)
    _bench = None
    if _need_bench:
        _conn = str(connection or (_cfg.get("winding", {}) or {})
                    .get("connection") or "")
        _bench = (_bench_read(_geometry_fingerprint(_geo_ov), _conn)
                  or _bench_compute(_geo_ov, _conn))
    _ld = float(Ld_mH if Ld_mH is not None else _bench["Ld_mH"]) * 1e-3
    _lq = float(Lq_mH if Lq_mH is not None else _bench["Lq_mH"]) * 1e-3
    _pm = float(psi_pm_mWb if psi_pm_mWb is not None
                else _bench["psi_pm_mWb"]) * 1e-3
    # R_phase: the caller's, else the last transient's solved value, else the
    # cheap geometric one.  Never zero silently — R sets the ripple's damping.
    _r = R_phase_ohm
    if _r is None:
        _last = (_last_transient_ref.get("result") or {})
        _r = _last.get("R_phase_ohm")
    if _r is None:
        raise HTTPException(422, detail=(
            "no phase resistance available: pass ?R_phase_ohm=, or run one "
            "transient first (its solved R_phase is then reused)."))
    try:
        _emf = _pwf(emf_waveform, what="emf_waveform") if emf_waveform else None
        out = _synth(
            r_phase=float(_r), ld_h=_ld, lq_h=_lq, psi_pm_wb=_pm,
            pole_pairs=_poles // 2, rpm=float(_rpm), v_bus=float(v_bus),
            f_switch_hz=float(f_switch), i_phase_rms=float(I_phase_rms),
            gamma_deg=float(gamma_deg), emf_waveform=_emf,
            n_samples=int(n_samples))
    except _ExcErr2 as _pe:
        raise HTTPException(422, detail=str(_pe))
    out["machine"] = {
        "rpm": float(_rpm), "poles": int(_poles),
        "R_phase_ohm": round(float(_r), 6),
        "Ld_mH": round(_ld * 1e3, 4), "Lq_mH": round(_lq * 1e3, 4),
        "psi_pm_mWb": round(_pm * 1e3, 4),
        "source": ("bench probe" if _need_bench else "caller"),
    }
    return out


@router.get("/physics/bench_ldq")
def get_bench_ldq(
    connection: Optional[str] = None,
    geo:        Optional[str] = None,
    fresh:      bool = False,
):
    """Small-signal Ld/Lq the way they are measured on the bench (see
    `_bench_compute`).  The Simulation card gets these automatically with
    every run on the live machine; this endpoint serves API users and lets
    `fresh=true` force a re-measure."""
    from motor_ai_sim.config import get_config as _gc
    _geo_ov = _parse_geo_override(geo)
    _conn = str(connection or (_gc().get("winding", {}) or {})
                .get("connection") or "")
    if not fresh:
        _hit = _bench_read(_geometry_fingerprint(_geo_ov), _conn)
        if _hit:
            return {**_hit, "cached": True}
    return _bench_compute(_geo_ov, _conn)


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
def _sleeve_hoop(geo_cfg: dict, rpm: float) -> Optional[dict]:
    """Hoop stress in the retaining sleeve at this run's speed, or None.

    THIN-RING, OWN MASS ONLY:  sigma_hoop = rho * omega^2 * r_mean^2.

    What it deliberately does NOT include is the magnet pressure the sleeve is
    fitted to retain — the whole reason a retaining ring exists.  On a surface-PM
    rotor that term dominates, and computing it needs the magnet mass the ring
    carries, the interference fit and the temperature; none of that is modelled
    here.  So this number is a LOWER BOUND on the real hoop stress, it is
    labelled as one, and it GATES NOTHING: a run is never refused on it.  What
    it is good for is the obvious answer — a ring already past its own strength
    carrying nothing but itself is finished before the magnets are added.

    Returns {sigma_hoop_MPa, strength_MPa, utilisation_pct, r_mean_mm, rpm,
    material, density} or None when there is no sleeve / no speed / no strength
    figure on the material.
    """
    try:
        t_mm = float(geo_cfg.get("sleeve_thickness", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if t_mm <= 0.0:
        return None
    try:
        from motor_ai_sim.masses import part_material
        from motor_ai_sim.geometry.motor_geometry import derived_geometry
        r_ro = derived_geometry(dict(geo_cfg)).get("rotor_outer_radius")
        if not r_ro or float(r_ro) <= 0.0:
            return None
        rho, _kf, name = part_material("sleeve")
        r_mean_m = (float(r_ro) + 0.5 * t_mm) * 1e-3
        omega = 2.0 * math.pi * float(rpm) / 60.0
        sigma_pa = float(rho) * omega * omega * r_mean_m * r_mean_m
        strength = None
        for _cat in ("insulator", "conductor", "steel"):
            try:
                from motor_ai_sim.materials import get_material as _gm
                strength = getattr(_gm(_cat, name), "tensile_strength_mpa", None)
                break
            except Exception:      # noqa: BLE001 — wrong category, keep looking
                continue
        out = {
            "sigma_hoop_MPa": round(sigma_pa * 1e-6, 3),
            "r_mean_mm": round(r_mean_m * 1e3, 3),
            "rpm": round(float(rpm), 1),
            "material": name or "",
            "density_kg_m3": round(float(rho), 1),
            "thickness_mm": round(t_mm, 4),
        }
        if strength:
            out["strength_MPa"] = float(strength)
            out["utilisation_pct"] = round(
                100.0 * sigma_pa * 1e-6 / float(strength), 2)
        return out
    except Exception as _e:      # noqa: BLE001 — a stress figure must never
        log.warning("sleeve hoop stress unavailable: %s", _e)
        return None               # sink a summary


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
    from motor_ai_sim.masses import compute_masses, rotor_inertia_kg_m2
    m = compute_masses(p, geo_cfg, k_end=k_end)
    m_total = m["total"]
    _MASS_KEY = {"stator_core": "stator", "rotor_core": "rotor",
                 "magnet": "mag", "slot": "cu", "shaft": "shaft",
                 "sleeve": "sleeve"}
    _mat = m["MAT"]
    _rho = m["RHO"]
    _lam = ", lamination k_f={:.3f}"
    try:
        _J = rotor_inertia_kg_m2(p, geo_cfg)
        _rotor_J = {"J_kg_m2": float(_J["total"]),
                    "J_kg_cm2": round(float(_J["total"]) * 1e4, 3),
                    "rotor_iron": float(_J["rotor_iron"]),
                    "magnet": float(_J["magnet"]), "shaft": float(_J["shaft"]),
                    "sleeve": float(_J.get("sleeve", 0.0) or 0.0),
                    # A reference/excluded part contributes 0 above; what it
                    # WOULD have contributed is kept so the drop is explainable.
                    "J_modelled": _J.get("J_modelled"),
                    "part_states": _J.get("states") or {},
                    "source": _J["source"]}
    except Exception as _je:   # noqa: BLE001 — inertia must never sink the summary
        log.warning("rotor inertia computation failed: %s", _je)
        _rotor_J = None
    # Per-part accounting state (included / reference / excluded).  A REFERENCE
    # part keeps its row — the card and the datasheet must SAY that a shaft is
    # customer-supplied rather than let it vanish and leave the reader to
    # wonder — carrying mass 0 (it is not ours) plus the mass it was modelled
    # at.  An EXCLUDED part is not there at all, so its row is dropped.
    _state = m.get("STATE") or {}
    _phys = m.get("PHYS") or {}
    from motor_ai_sim.part_states import state_note as _snote, REFERENCE as _REF

    def _row(part: str, name: str, material: str, rho: float, vol_m3: float,
             mass_kg: float, note: str) -> Optional[dict]:
        st = _state.get(part)
        if st and st != _REF:                       # excluded → no row at all
            return None
        d = {"name": name, "material": material, "density_kg_m3": rho,
             "volume_cm3": round(vol_m3 * 1e6, 1),
             "mass_kg": round(mass_kg, 3), "note": note}
        if st == _REF:
            d["state"] = _REF
            d["counted"] = False
            d["mass_modelled_kg"] = round(float(_phys.get(_MASS_KEY[part], 0.0)), 3)
            d["name"] = f"{name} — customer-supplied"
            d["note"] = (_snote(part, _mat.get(part) or material, st)
                         + f" ({d['mass_modelled_kg']:.3f} kg modelled); " + note)
        return d

    return {
        "components": [c for c in (
            _row("stator_core", f"Stator core ({_mat['stator_core'] or 'steel'})",
                 "electrical steel", _rho["steel"], m["V_stator"], m["stator"],
                 f"CAD section {m['A_stator']*1e6:.0f} mm² × stack"
                 + _lam.format(m["k_f_stator"])),
            _row("slot", "Copper windings (Cu)", _mat["slot"] or "copper",
                 _rho["cu"], m["V_cu"], m["cu"],
                 f"measured copper section {m['A_cu']*1e6:.0f} mm² × stack × k_end {m['k_end']:.3f}"),
            _row("magnet", f"Magnets ({_mat['magnet'] or 'PM'})", "NdFeB",
                 _rho["mag"], m["V_mag"], m["mag"],
                 f"CAD magnet polygons {m['A_mag']*1e6:.0f} mm² × stack"),
            _row("rotor_core", f"Rotor back-iron ({_mat['rotor_core'] or 'steel'})",
                 "electrical steel", _rho["steel_rotor"], m["V_rotor"], m["rotor"],
                 f"CAD section {m['A_rotor']*1e6:.0f} mm² × stack"
                 + _lam.format(m["k_f_rotor"])),
            _row("shaft", f"Shaft ({_mat['shaft'] or 'shaft'})", "shaft",
                 _rho["al"], m["V_shaft"], m["shaft"],
                 f"hollow shaft tube {m['A_shaft']*1e6:.0f} mm² × stack — NOT in active mass"),
            # The retaining sleeve exists only when it has a section: a row of
            # 0.000 kg on the 99 % of machines that carry no ring would be
            # noise, and worse, it would imply a part that is not there.
            (_row("sleeve", f"Sleeve ({_mat.get('sleeve') or 'CFRP'})",
                  "carbon fibre", _rho.get("sleeve", 0.0), m.get("V_sleeve", 0.0),
                  m.get("sleeve", 0.0),
                  f"retaining ring {m.get('A_sleeve', 0.0)*1e6:.1f} mm² × stack "
                  f"— NOT in active mass")
             if float(m.get("A_sleeve", 0.0) or 0.0) > 0.0 else None),
        ) if c is not None],
        # The accounting states this mass was computed under, so a restored
        # summary can still say WHY its total is what it is.
        "part_states": {k: v for k, v in _state.items()},
        # ACTIVE = the EM-active mass (iron + copper + magnets), the basis ANSYS
        # quotes; TOTAL = active + shaft, the historical divisor of torque-per-mass
        # (unchanged, so stored Compare points keep their meaning).
        "mass_active_kg": round(m["active"], 3),
        "mass_total_kg": round(m_total, 3),
        "total_active_kg": round(m_total, 3),   # legacy name = TOTAL, kept for old readers
        "area_source": m["area_source"],
        # Rotor moment of inertia about the shaft axis — ∬r²dA over the SAME
        # CAD polygons the mass uses (rotor iron billed at its lamination k_f,
        # magnets and shaft solid), × stack × density.  SI in the payload;
        # the UI shows kg·cm² (the unit drive-sizing sheets speak).
        "rotor_inertia": _rotor_J,
        "note": "Active section only (no housing/frame/bearings). Typical frame adds 30-50% mass.",
        "estimated_total_with_frame_kg": round(m_total * 1.4, 2),
    }


# Bump when the summary gains a derived quantity: cached/restored results carry
# their summary AS STORED, so without this stamp a new cell (Km, rotor inertia)
# never appeared on any cached point — the card silently lacked it until the
# user happened to re-solve.
# v3 (2026-08-23): + demag coefficient (volume-weighted Br kept / loss %).
# v4 (2026-08-23): demag coefficient is ENERGY-based (BH ∝ Br², user's spec)
#                  + effective grade.
# v5 (2026-08-23): + saturation torque droop (vs the unsaturated 1.5·p·ψ_PM·iq).
# v6 (2026-08-24): + 3D end-effect correction (Stage A k_flux per machine).
# v7 (2026-08-24): + bench Ld/Lq (small-signal probe at I≈0, cached per
#                  machine+connection — computed automatically on live-machine
#                  runs, cache-read here).
# v8 (2026-08-24): + no-load KV from ψ_PM (KV_noload_rpm_per_V_line) — zero
#                  extra solves, the loaded run's cached I=0 probe.
# v9 (2026-08-24): chord Ld withheld when cross-saturation dominates it
#                  (checked against the run's own saturation droop) — restored
#                  summaries rebuild so contaminated chord values drop out.
# v10 (2026-08-26): + phase copper cross-section (A_phase_mm2).
# v11 (2026-08-30): + wire coating factor, MEASURED on the CAD polygons (copper
#                  over the winding window the teeth leave) — the number that
#                  says whether this winding can actually be wound.
# v12 (2026-09-01): + per-part accounting states (included / reference /
#                  excluded).  A stored summary from before this stamp has no
#                  `part_states` and its mass rows carry no `state`, so a
#                  frameless build's "customer-supplied shaft" label would
#                  never appear on a restored card without the rebuild.
# v13 (2026-09-01): saturation reference includes the BENCH reluctance term
#                  (1.5·p·(Ld−Lq)·i_d·i_q) and droop_pct is floored at 0, so
#                  the Saturation koef can never read above 100 % — a stored
#                  summary from v12 could show 101.6 % at γ ≠ 0 on a salient
#                  rotor (PM-only reference), which the rebuild corrects.
# v14 (2026-09-01): + battery_charge + I_phase_rms_solved_A.
# v15 (2026-09-04): the HEAT SPLIT — P_loss_stator_W / P_loss_rotor_W and the
#                  core loss per half (P_core_stator_W / P_core_rotor_W).  A
#                  stored summary from v14 has neither, and the two cells are
#                  what a cooling design is sized on, so it is worth a rebuild.
# v16 (2026-09-08): the MECHANICAL half of the loss picture, server-side —
#                  P_bearings_W / P_windage_W / P_mech_extra_W, the bearing
#                  temperature and where it came from, P_loss_total_incl_mech_W,
#                  P_shaft_net_W and efficiency_shaft.  Until now those four
#                  cells were computed in the BROWSER from /api/bearings/losses
#                  and the stored run knew nothing about them, so the datasheet,
#                  the report, Compare and the coupled loop all read a machine
#                  whose largest sub-3000-rpm loss was missing.  Worth a rebuild:
#                  the numbers are arithmetic over the run's own rpm and mass
#                  rows plus the machine's bearing cards, so a stored result can
#                  be given them honestly without re-solving anything.
_SUMMARY_SHAPE_V = 18   # v18: incremental (frozen-permeability) Ld/Lq, the
                        #      chord under its own name, ψ_PM sag (2026-09-20)
# NOT bumped for the eddy-settle verdict (2026-09-07): a solver payload from
# before that date cannot say whether its warm-up settled, so rebuilding an old
# summary would stamp a default "settled: true" into it — a claim nobody
# measured.  Absent stays absent, and the card stays silent about those runs;
# every result solved since carries the verdict from its own solve.


def _end3d_lookup(geo_fp: Optional[str], geo: Optional[dict] = None,
                  exact: bool = False) -> Optional[dict]:
    """The Stage A end-effect passport for THIS machine, or None.

    config/end_effect_passports.json is keyed by the same `_geometry_fingerprint`
    the stale-machine banner uses, so a passport can never be served for a
    different motor than the one on screen.  A 2-D transient overestimates the
    flux of a short stack (the field spills axially past the laminations); the
    3-D static Stage A measures the factor once per machine — 0.952 on the
    40 mm, 0.927 on the 85 mm (stack 13 mm on Ø85 spills hard)."""
    if not geo_fp:
        return None
    try:
        import json as _json
        from motor_ai_sim.workspace import root as _ws_root
        _p = _ws_root() / "end_effect_passports.json"
        store = _json.loads(_p.read_text(encoding="utf-8")) or {}
        rec = store.get(str(geo_fp))
        if isinstance(rec, dict):
            return dict(rec)
        if exact:
            return None
        # No passport for THIS geometry.  User 2026-09-03: the coefficient must
        # not vanish the moment a slot or a magnet is edited — keep the last
        # one measured on the SAME machine (slots/poles/OD) and say it needs a
        # Stage A re-run.  The catalog's passport builder asks for exact=True
        # so an inherited value never suppresses a real measurement there.
        desc = _machine_desc(geo)
        if not desc:
            return None
        cands = [(k, v) for k, v in store.items()
                 if isinstance(v, dict) and v.get("machine") == desc
                 and v.get("k_flux") is not None]
        if not cands:
            return None
        _when = lambda v: str(v.get("adopted_utc") or v.get("generated_utc") or "")  # noqa: E731
        k, v = max(cands, key=lambda kv: _when(kv[1]))
        return {**v, "inherited": True, "inherited_from": k,
                "inherited_note": (
                    f"coefficient of an EARLIER geometry of this machine "
                    f"({desc}, passport {_when(v)[:10] or 'undated'}) — the "
                    f"current geometry has no Stage A passport yet; recompute "
                    f"to confirm or replace it")}
    except Exception:   # noqa: BLE001 — no passport is a normal state
        return None


def _machine_desc(geo: Optional[dict] = None) -> Optional[str]:
    """`24s/28p OD 85` — the machine in the words the passports are stamped
    with (static3d.viewer.machine_summary → routes/catalog), from the live
    config geometry with an optional per-request override on top."""
    try:
        from motor_ai_sim.config import get_config as _gc
        g = dict((_gc().get("geometry") or {}))
        if geo:
            g.update({kk: vv for kk, vv in dict(geo).items() if vv is not None})
        ns = int(g.get("num_seg") or 0)
        sl = int(g.get("num_slots_per_segment") or 0)
        pl = int(g.get("num_poles_per_segment") or 0)
        od = float(g.get("stator_diameter") or 0)
        if not (ns and sl and pl and od):
            return None
        return f"{ns * sl}s/{ns * pl}p OD {od:g}"
    except Exception:   # noqa: BLE001
        return None


def _demag_with_grade(dsum):
    """Attach the nominal/effective GRADE to the demag aggregate.

    The number in a NdFeB grade name (N52, F45SH) is (BH)max in MGOe, and the
    kept-energy fraction is exactly (BH)'/(BH) — so nominal × kept is the grade
    the magnet has effectively become.  Parsed from the ASSIGNED magnet name;
    a name without digits just skips the grade line."""
    if not isinstance(dsum, dict):
        return dsum
    out = dict(dsum)
    try:
        import re as _re
        from motor_ai_sim.config import get_config as _gc
        name = str((_gc().get("materials") or {}).get("magnet") or "")
        m = _re.search(r"(\d+)", name)
        if m and out.get("bh_kept_vol_pct") is not None:
            g0 = float(m.group(1))
            out["grade_nominal"] = g0
            out["grade_effective"] = round(
                g0 * float(out["bh_kept_vol_pct"]) / 100.0, 1)
            out["magnet_name"] = name
    except Exception:   # noqa: BLE001 — the grade line is garnish, never fatal
        pass
    # The same integral in JOULES: E = (BH)max·V of the whole magnet system,
    # lost fraction = 1 − ⟨k²⟩.  (BH)max = Br²/(4·μ0·μ_rec) of the ASSIGNED
    # grade at its record temperature; V from the mass model's own CAD magnet
    # section — the integral the % was taken over, so the two cannot disagree.
    try:
        import math as _m2
        from motor_ai_sim.config import get_config as _gc2
        from motor_ai_sim.materials import get_material as _gm
        from motor_ai_sim.optimization.design_eval import build_params as _bp
        from motor_ai_sim.masses import cad_areas_m2 as _ca
        name = str((_gc2().get("materials") or {}).get("magnet") or "")
        mat = _gm("magnet", name)
        geo = dict(_gc2().get("geometry") or {})
        A = _ca(geo)
        if mat is not None and A and out.get("bh_kept_vol_pct") is not None:
            V = float(A["magnet"]) * float(_bp(geo).stack_length)
            bh_max = float(mat.Br) ** 2 / (4.0 * 4e-7 * _m2.pi
                                           * float(getattr(mat, "mu_rec", 1.05)))
            e_tot = bh_max * V
            out["energy_total_J"] = round(e_tot, 2)
            out["energy_lost_J"] = round(
                e_tot * (1.0 - float(out["bh_kept_vol_pct"]) / 100.0), 3)
    except Exception:   # noqa: BLE001 — garnish, never fatal
        pass
    return out


def _refresh_summary_shape(res: dict) -> dict:
    """Rebuild a cached result's summary when its SHAPE predates the code.

    Only possible for results that recorded their summary build args (stashed
    since the same commit that introduced the stamp); older entries are served
    as-is and refresh naturally on the next real solve.  Purely derived-number
    work — the solver payload is untouched, so this is milliseconds (the mass
    sections and the inertia polar moments are memoised by geometry)."""
    try:
        s = res.get("summary") or {}
        _cur = int(s.get("summary_shape_v", 1) or 1) >= _SUMMARY_SHAPE_V
        # Same shape is NOT always fresh: a Stage-A passport can be measured
        # AFTER the run was solved — the stored summary then carries
        # end3d=null at the current version, and without this clause the new
        # correction would never reach any existing result.
        _passport_arrived = (s.get("end3d") is None
                             and _end3d_lookup(res.get("geo_fingerprint"))
                             is not None)
        if _cur and not _passport_arrived:
            return res
        args = res.get("_summary_args")
        if not isinstance(args, dict):
            return res                    # pre-stash entry — cannot rebuild honestly
        out = dict(res)
        out["summary"] = _build_transient_summary(
            out, I_phase_rms=float(args["I_phase_rms"]),
            gamma_deg=float(args["gamma_deg"]),
            coil_temp_c=float(args["coil_temp_c"]),
            geo_override=args.get("geo_override"),
            mode_requested=args.get("mode_requested"))
        return out
    except Exception as _e:   # noqa: BLE001 — a refresh failure must not eat the hit
        log.warning("summary shape refresh failed: %s", _e)
        return res


def _round_w(value: float, nd: int) -> float:
    """Round a watt figure for STORAGE without ever turning it into a zero.

    The house rule this module keeps (see ``mech_losses``) is that a mechanical
    loss nobody measured is ABSENT, never 0 W — an efficiency nobody measured is
    the one thing a stored run may not claim.  Rounding breaks that rule from the
    other end: on 2026-09-15 the 30 mm fixture at 1000 rpm computed a 618/8-2Z
    pair at 2.9 mW — an honest number, the whole loss of a shielded miniature
    bearing at that speed — and ``round(x, 2)`` stored it as ``0.0``.  Every
    consumer then read "this machine's bearings cost nothing", which is a claim
    the solver never made; the same run at 12 000 rpm stored 0.12 W and read
    correctly, so the bug only ever showed on the small, slow machines where a
    milliwatt IS the answer.

    So: the fixed precision when it keeps the number, three significant figures
    when it would erase it.  Nothing a reader sees moves — a 84.0 W pair still
    rounds to 84.0 — and a machine whose friction is genuinely zero (no speed, no
    bearings) is unaffected, because those paths return ``{}`` long before here.
    """
    v = float(value or 0.0)
    r = round(v, nd)
    if r == 0.0 and v != 0.0:
        return float(f"{v:.3g}")
    return r


def _mech_loss_fields(sbres: dict, *, geo_override: Optional[dict],
                      rpm: float, p_mech_w: float, p_loss_w: float,
                      efficiency: float, op_mode: str,
                      mass_components: Optional[list] = None) -> dict:
    """The MECHANICAL half of this run's loss picture, or ``{}``.

    User, 2026-09-08: *"все потери должны передаваться в электромагнитный
    расчёт"*.  Until today the bearing friction and the rotor windage were
    computed in the BROWSER, from ``/api/bearings/losses``, and pasted into four
    cells of the summary table; the run that was stored — the thing the
    datasheet, the report, Compare and the coupled loop read — carried none of
    them.  On the measured 150 mm free run those two were 43–170 W out of
    83–319 W, the largest single term below 3000 rpm, so every stored efficiency
    was optimistic by an amount nobody could see in the payload.

    Now they ride WITH the run.  The physics is unchanged and still ANALYTIC (the
    SKF frictional-moment model plus Couette/disc windage — see
    ``motor_ai_sim.bearings``); what changed is that one implementation
    (``mech_losses``) answers for the route, this summary, the thermal solve and
    the orchestrator instead of four callers each doing their own arithmetic.

    THREE GATES, and all three are the point:

      * a machine that names NO BEARINGS gets ``{}`` — the fields are ABSENT, not
        zero.  An unknown mechanical loss printed as 0 W is an efficiency nobody
        measured (the house rule the ``/losses`` route already follows);
      * a BACKGROUND solve (``_BACKGROUND_RUN`` — a passport sweep point, a
        search probe) gets ``{}``: an optimizer candidate is not a machine
        anybody has chosen bearings for, and paying a die read plus a CAD mass
        measurement per candidate is exactly the cost those gates exist to avoid;
      * a CANDIDATE geometry (a per-request override that is not the machine on
        screen) gets ``{}`` for the same reason — its fingerprint says it is not
        the motor whose die file the bearings are written in.

    ``efficiency_shaft`` is DERIVED from ``efficiency`` rather than recomputed
    from a loss sum, so the two can never disagree — identical arithmetic to
    ``datasheet``, ``report`` and the summary table's own cell:

        motor      eta_shaft = eta · (1 − P_extra/P_mech)
        generator  eta_shaft = eta / (1 + P_extra/P_mech)

    which is ``P_shaft_net / (P_shaft_net + every loss)`` in both directions —
    the mechanical losses sit BETWEEN the rotor and the coupling, so motoring
    they come off the output and generating they go onto the input.
    """
    try:
        if _BACKGROUND_RUN.get():
            return {}
        # "Is this the motor on screen?" — the same resolved-fingerprint test
        # `get_fem_transient` uses for the field snapshot and the persisted last
        # run, so the three can never disagree about which runs are the user's.
        if geo_override:
            fp = sbres.get("geo_fingerprint") or _geometry_fingerprint(geo_override)
            if fp != _geometry_fingerprint(None):
                return {}
        from motor_ai_sim import mech_losses as _ml

        assignment, _die, _cfg = _ml.machine_bearings()
        from motor_ai_sim import bearings as _brg
        if not _brg.has_bearings(assignment):
            return {}
        # The ROTATING mass THIS summary is being billed at — the very mass rows
        # the card shows, not a fresh CAD measurement, so the bearing load and
        # the torque density beside it come from the same kilograms.  `None`
        # falls through to the CAD inside `machine_mech_losses`.
        mass = _ml.rotor_mass_from_summary(
            {"mass_components": list(mass_components or ())})
        mech = _ml.machine_mech_losses(
            rpm=float(rpm), assignment=assignment, die=_die, config=_cfg,
            geo_override=geo_override, rotor_mass_kg_=mass,
            geometry_fingerprint=sbres.get("geo_fingerprint"),
            resolve_machine=False)
    except Exception as exc:  # noqa: BLE001 — a bearing card must not sink a run
        log.warning("summary: the mechanical loss block could not be built (%s)",
                    exc)
        return {}
    if not mech:
        return {}

    p_brg = float(mech.get("P_bearings_W") or 0.0)
    p_wind = float(mech.get("P_windage_W") or 0.0)
    p_extra = float(mech.get("P_mech_extra_W") or 0.0)
    p_mech = abs(float(p_mech_w or 0.0))
    gen = str(op_mode) == "generator"
    x = (p_extra / p_mech) if p_mech > 0.0 else None
    if x is None:
        eta_shaft = None
        p_shaft_net = None
    elif gen:
        # Quoted POSITIVE like every other generator number on this card: the
        # shaft has to SUPPLY the rotor's mechanical power plus the friction that
        # never reaches it, so the coupling sees more than the rotor does.
        p_shaft_net = p_mech + p_extra
        eta_shaft = float(efficiency) / (1.0 + x)
    else:
        p_shaft_net = p_mech - p_extra
        eta_shaft = float(efficiency) * max(0.0, 1.0 - x)
    return {
        "P_bearings_W": _round_w(p_brg, 2),
        "P_windage_W": _round_w(p_wind, 3),
        "P_mech_extra_W": _round_w(p_extra, 2),
        "bearing_temp_c": mech.get("bearing_temp_c"),
        "bearing_temp_source": mech.get("bearing_temp_source"),
        "bearing_temp_note": mech.get("bearing_temp_note"),
        # What a calorimeter around the ASSEMBLED machine would read: the solved
        # electromagnetic loss plus the two analytic mechanical terms.
        "P_loss_total_incl_mech_W": round(float(p_loss_w) + p_extra, 1),
        "P_shaft_net_W": (None if p_shaft_net is None
                          else round(float(p_shaft_net), 1)),
        "P_shaft_convention": ("rotor power PLUS the mechanical losses — what the "
                               "coupling must supply" if gen else
                               "rotor power MINUS the mechanical losses — what "
                               "leaves at the coupling"),
        # SIX decimals, not the four `efficiency` is stored at: eta_shaft is
        # DERIVED from eta by the factor (1 - x), and quantising it onto eta's
        # own grid makes a machine whose friction is small but real report the
        # same shaft efficiency as its electromagnetic one — the rounding saying
        # "no mechanical loss" about a machine that has one.  1e-4 % is below
        # every display precision in the product, so nothing a reader sees moves.
        "efficiency_shaft": (None if eta_shaft is None else round(eta_shaft, 6)),
        "mech_losses": _ml.summary_block(mech),
    }


def _build_transient_summary(
    sbres: dict,
    *,
    I_phase_rms: float,
    gamma_deg: float,
    coil_temp_c: float,
    geo_override: Optional[dict] = None,
    mode_requested: Optional[str] = None,   # the mode the REQUEST asked for
                                            # (motor/generator) — op_mode below
                                            # is derived from the power sign
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
    # Carbon-fibre retaining sleeve — 0.0 on every machine without one, and on
    # a stored result that predates the feature (`_mean` of a missing key).
    _Psleeve = _mean("P_sleeve_eddy_W")
    # DC (I²R) copper alone for the motor constant below: Km's definition uses
    # the OHMIC loss — the price of torque — not the frequency-dependent AC
    # add-on, so Km stays a property of the winding, not of the speed.
    _Pcu_dc = float(sbres.get("P_cu_dc_W", 0.0) or 0.0) or _Pcu

    # ── HEAT TO REMOVE, PER SIDE (user 2026-09-04) ───────────────────────
    # Two numbers a cooling engineer can act on: what the STATOR has to shed
    # and what the ROTOR has to shed.  Nothing is invented and nothing is
    # dropped — every watt in `_ploss` below is attributed to the body it is
    # dissipated in:
    #   stator = stator iron + ALL copper (I²R incl. end-winding + AC/prox)
    #   rotor  = rotor iron + magnet eddy + shaft eddy + sleeve eddy
    # The iron split is the solver's OWN per-half Bertotti/surface breakdown
    # (`P_fe_terms`), renormalised onto the reported core loss so the two
    # halves add up to the Core tile exactly (the terms are rounded to 3
    # decimals and the reported series is clipped at 0).
    _fe_terms = sbres.get("P_fe_terms") or {}

    def _half_fe(_h: str) -> float:
        _t = _fe_terms.get(_h) or {}
        return (float(_t.get("hysteresis_W", 0.0) or 0.0)
                + float(_t.get("eddy_W", 0.0) or 0.0)
                + float(_t.get("excess_W", 0.0) or 0.0))
    _fe_halves = _half_fe("stator") + _half_fe("rotor")
    # A stored run from before the per-half breakdown existed cannot be split
    # honestly.  The whole core loss then goes to the STATOR — where all but a
    # few percent of it physically is on a surface-PM machine — and the flag
    # says the split was assumed, so the card and the datasheet can say so
    # instead of quoting a number nobody measured.
    _fe_split_known = bool(_fe_terms) and _fe_halves > 0.0
    _Pfe_s = (_Pfe * _half_fe("stator") / _fe_halves) if _fe_split_known else _Pfe
    # stator = stator iron + ALL copper.  The rotor side is (_Pfe − _Pfe_s) +
    # _Pmag + _Pshaft + _Psleeve, but it is REPORTED as the complement of this
    # one against the reported total, so the two cells can never round apart
    # from the Total-loss tile they are billed against.
    _P_stator = _Pcu + _Pfe_s

    # Voltage figures from the ACTUAL waveforms.  The old shortcuts
    # (rms = pk/√2, line = √3·phase) hold only for a pure balanced sinusoid:
    # on a concentrated winding the PHASE peak is inflated by the (large)
    # triplen harmonics, which CANCEL in the line-to-line difference — the
    # √3 shortcut then overstates the line peak by tens of percent (measured
    # 121.6 V card vs 95 V real V_A−V_B on the 24s28p), which corrupts
    # battery/inverter sizing.  Fall back to the shortcuts only when the
    # per-frame series are missing (old stored runs).
    import numpy as _np_v
    # TERMINAL CONNECTION of this run — every line / terminal quantity in
    # this summary is mapped by it, once, here (see the STAR / DELTA block in
    # fem_solver_2d for the physics).  The solver's own numbers are WINDING
    # quantities in both connections.
    _is_delta = str(sbres.get("star_delta") or "star").lower().startswith("d")
    _sq3 = _math.sqrt(3.0)
    _Vpk = float(sbres.get("V_peak", 0.0))
    _Vrms = _Vpk / _math.sqrt(2)
    _Vlpk = _Vpk * (1.0 if _is_delta else _sq3)
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
        # LINE-TO-LINE, per the terminal connection.  Star: the difference of
        # two phase voltages.  Delta: the winding IS the line, minus its
        # zero-sequence part — the triplen EMF drives the circulating current
        # round the closed loop and never reaches the terminals (user
        # 2026-09-12: "в дельте линейные должны быть равны фазным").  Both
        # are what the DC bus has to cover; sqrt(3)x a phase peak is neither.
        if _is_delta:
            _v0 = (_va + _vb + _vc) / 3.0
            _lls = (_va - _v0, _vb - _v0, _vc - _v0)
        else:
            _lls = (_va - _vb, _vb - _vc, _vc - _va)
        _Vlpk = float(max(_np_v.max(_np_v.abs(p)) for p in _lls))
        _Vlrms = float(_np_v.mean([_np_v.sqrt(_np_v.mean(p ** 2))
                                   for p in _lls]))

    # Total INCLUDES shaft eddy so the breakdown sums to the same loss the solver's
    # energy-balanced P_mech subtracts (else the card's Mech-power ≠ Σ losses).
    _ploss = _Pcu + _Pfe + _Pmag + _Pshaft + _Psleeve
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
    # WHICH WAY THE POWER FLOWS decides the efficiency formula — the sign of
    # the shaft power, not a UI flag, so a hand-typed generator angle gets the
    # generator arithmetic too.  Motor: eta = P_shaft_out / (P_shaft + losses).
    # Generator: mechanical power comes IN (P_mech < 0), the electrical output
    # is what remains after the losses, eta = (P_in - losses) / P_in.
    if _Pmech > 0:
        _eff = (_Pmech / _Pelec) if _Pelec > 1.0 else 0.0
        _op_mode = "motor"
    elif _Pmech < 0:
        _P_in = -_Pmech
        _eff = ((_P_in - _ploss) / _P_in) if _P_in > max(_ploss, 1.0) else 0.0
        _op_mode = "generator"
    else:
        _eff = 0.0
        _op_mode = "motor"
    # GENERATOR NUMBERS ARE QUOTED POSITIVE (user rule, 2026-08-19): the sign
    # only says which way the shaft turns against the current, and a negative
    # T_avg would poison every quantity built on it — torque density, Kt,
    # power/mass, and the optimizer's perpendicular metric all assume a
    # magnitude.  The GENERATOR chip on the card carries the direction; the
    # raw T(t) series in the result keeps its true (negative) sign for anyone
    # reading the waveform.
    if _op_mode == "generator":
        _Tavg = abs(_Tavg)
        _Pmech = abs(_Pmech)

    # Voltage waveform quality (CIANO THD spec): V₁ + phase/line-to-line THD +
    # torque constant — first-class metrics on EVERY run so the optimizer can
    # hold THD_LL like it holds ripple.
    _vh = _vharm(sbres)
    # Current waveform quality: ≈0 in current drive (imposed sinusoids); in
    # VOLTAGE drive this is the real parasitic harmonic-current content the
    # distorted back-EMF forces through the winding.
    from motor_ai_sim.simulation.postproc import current_harmonics as _iharm
    _ih = _iharm(sbres)
    # The mirror of harm_ref's voltage→current extraction: current→voltage, so
    # a run done on the current drive hands the imposed-voltage sources the
    # fundamental that reproduces it.  See postproc.fundamental_voltage.
    from motor_ai_sim.simulation.postproc import (
        fundamental_voltage as _vseed_fn)
    _vseed = _vseed_fn(sbres)
    # Voltage drive: the requested I_phase_rms is 0 — use the run's own
    # fundamental current for Kt so the card stays meaningful in both modes.
    # (Both imposed-VOLTAGE sources — the sinusoid and the PWM inverter — reach
    # here with I_phase_rms = 0; custom_current carries its own waveform, whose
    # fundamental is also not I_phase_rms, so it takes the same route.)
    _kt_I = float(I_phase_rms or 0.0)
    if _kt_I <= 1e-9 and str(sbres.get("drive", "")) in (
            "voltage", "pwm_voltage", "custom_current", "bldc_current"):
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
    # STRANDS IN HAND multiply the paths for every current-splitting purpose:
    # k wires in hand means each physical wire carries I_coil/k, so J and the
    # phase's conductor section are built on n_parallel x wire_parallel
    # (winding.n_parallel_effective).  `wire_split` is NOT in it: a row's strips
    # are series TURNS, each carrying the whole branch current.  The run reports
    # its own (it may have solved a per-request geometry override the shared
    # config knows nothing about); the config is the fallback.
    try:
        from motor_ai_sim.winding import (wire_parallel_from_geo as _wp_geo,
                                          wire_split_from_geo as _ws_geo,
                                          n_parallel_effective as _npe,
                                          turns_per_coil as _tpc)
        _wpar = int(sbres.get("wire_parallel") or _wp_geo(_geo_cfg))
        _turns = int(sbres.get("turns_per_coil") or _tpc(_geo_cfg))
        _wsplit = int(sbres.get("wire_split") or _ws_geo(_geo_cfg))
        _npar_eff_cfg = int(_npe(_npar, _geo_cfg))
    except Exception:       # noqa: BLE001 — a summary is never worth a 500
        _wpar, _turns = 1, int(float(_geo_cfg.get("num_wires_per_slot", 0) or 0))
        _wsplit, _npar_eff_cfg = 1, _npar
    _npar_eff = max(1, int(sbres.get("n_parallel_eff") or _npar_eff_cfg))
    # …and the label those paths belong to, so the card can name the winding it
    # is reporting (empty when the run carried no consistent label).
    _conn = str(sbres.get("connection") or "")
    # The conductor J is measured on is the DRAWN one — one STRIP, which is
    # wire_width × wire_height whether or not there is a split (wire_split lays
    # N of them side by side, it does not subdivide the width).  `_npar_eff`
    # below does NOT carry the split: the strips are series turns and each one
    # carries the branch current whole, so J is the same as the unsplit row's.
    try:
        from motor_ai_sim.winding import strip_width_mm as _strip_w
        _a_cond_mm2 = _strip_w(_geo_cfg) * float(_geo_cfg.get("wire_height", 0.0))
    except Exception:       # noqa: BLE001 — a summary is never worth a 500
        _a_cond_mm2 = (float(_geo_cfg.get("wire_width", 0.0))
                       * float(_geo_cfg.get("wire_height", 0.0)))
    # _kt_I, not the raw requested I_phase_rms: in voltage drive the request
    # carries I=0 while real current flows, and dividing the request quoted
    # J = 0 A/mm² on the card next to a non-zero copper loss.  _kt_I is the
    # run's measured fundamental in that mode and the requested current
    # otherwise — the same effective current Kt is built on.
    _j_coil = (_kt_I / _npar_eff / _a_cond_mm2) if _a_cond_mm2 > 1e-9 else 0.0
    # Wire coating, measured on the same polygons the mesher sees (cached per
    # geometry, so this costs nothing on a repeat run).  Never fatal: a machine
    # whose CAD will not build still gets its summary, just without the cell.
    try:
        from motor_ai_sim.masses import slot_fill_from_cad
        _slot_fill = slot_fill_from_cad(dict(_geo_cfg))
    except Exception as _sfe:   # noqa: BLE001
        log.warning("wire coating measurement failed: %s", _sfe)
        _slot_fill = None

    # ── R and L-dq of this operating point ───────────────────────────────────
    # R_phase comes out of the solve and ALREADY includes the end-winding
    # (copper_loss_W: ρ_Cu(T)·J²·V_cu·k_end → R = P/(3I²)).  Line-to-line is
    # quoted for the isolated-neutral star this machine is driven as (the
    # voltage circuit is line-to-line for exactly that reason): R_LL = 2·R_ph.
    _R_ph = float(sbres.get("R_phase_ohm", 0.0) or 0.0)
    # CHORD Ld/Lq: ψd = ψ_PM + Ld·id and ψq = Lq·iq, with ψ_PM measured at I=0
    # (one cached no-load solve per geometry).  Refused rather than guessed when
    # the frame cannot be trusted: the dq torque identity must reproduce the
    # energy torque, and ψq at no load must be small next to ψ_PM.
    #
    # THESE ARE NO LONGER THE REPORTED Ld/Lq (client review 2026-09-20: *"这个
    # 电机 Ld > Lq? 好像和一般的电机不太一样"*).  The chord divides by a ψ_PM
    # measured at NO LOAD while the loaded iron's magnet flux has sagged ~9 %,
    # and on a machine driven at a small γ that sag IS the numerator — which is
    # how a spoke-PM machine whose Lq is the larger inductance shipped a report
    # saying Ld 0.0976 > Lq 0.0693.  They are kept, under their own names, as
    # the number the sag can be read off; the reported Ld/Lq are the
    # frozen-permeability incremental values below.
    _Ld_chord = _Lq_chord = _psi_pm = None
    _sat_droop = None          # set below only when ψ_PM and i_q both resolved
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
                    _Lq_chord = 1e3 * float(_psiq) / float(_iqm)
                # Ld divides (ψd − ψ_PM_noload) by i_d — but under load the
                # iron's cross-saturation shifts ψd by ~1-2 % of ψ_PM even at
                # i_d = 0, and that shift lands in the numerator.  A small
                # i_d cannot outweigh it (measured live: γ = 2° on a machine
                # with Lq/Ld ~ 1 read Ld = 0.169 mH vs Lq 0.040 — the whole
                # excess was Δψ_saturation/i_d).  Report Ld only when i_d
                # carries at least 10 % of the current (γ ≳ 6°); below that
                # the number would be cross-saturation, not inductance.
                _i_pk = (float(_idm) ** 2 + float(_iqm) ** 2) ** 0.5
                if abs(float(_idm)) >= max(1e-3, 0.10 * _i_pk):
                    _Ld_chord = 1e3 * (float(_psid) - _psi_pm) / float(_idm)
                else:
                    _dq_note = (
                        "i_d is only %.1f%% of the current at γ = %.1f° — "
                        "(ψd − ψ_PM)/i_d would be dominated by cross-"
                        "saturation of the loaded iron, not by inductance; "
                        "set γ ≥ ~6° and re-run to measure Ld honestly"
                        % (100.0 * abs(float(_idm)) / max(_i_pk, 1e-9),
                           float(gamma_deg)))
    except Exception as _el:   # noqa: BLE001
        _dq_note = f"{type(_el).__name__}: {_el}"

    # ── Saturation torque droop (needs the ψ_PM the block above resolved) ────
    try:
        _iq_s = sbres.get("i_q_A")
        _id_s = sbres.get("i_d_A")
        if _psi_pm is not None and _iq_s is not None and abs(float(_iq_s)) > 1e-3:
            _pp_s = int(dict(_geo_cfg).get("num_poles", 0)) // 2
            _T_lin = 1.5 * _pp_s * _psi_pm * float(_iq_s)
            # The linear REFERENCE includes the reluctance term off the BENCH
            # (small-signal, I≈0, unsaturated) Ld/Lq whenever the machine has
            # them: at γ ≠ 0 on a salient rotor the measured torque carries
            # 1.5·p·(Ld−Lq)·i_d·i_q, and a PM-only reference put that term in
            # the numerator alone — the koef read 101.6 % on the 85 mm at
            # γ = 2° (user 2026-09-01: "не может быть больше 100%").  With the
            # bench reluctance inside, the ratio measures IRON SATURATION
            # alone; a saturated machine sits below its unsaturated linear
            # twin by construction, and the clamp below covers the fallback
            # when no bench Ld/Lq exist for this machine yet.
            _T_rel = None
            try:
                _b = (_bench_read(sbres.get("geo_fingerprint"),
                                  sbres.get("connection")) or {})
                if _b.get("Ld_mH") and _b.get("Lq_mH") and _id_s is not None:
                    _T_rel = (1.5 * _pp_s
                              * (float(_b["Ld_mH"]) - float(_b["Lq_mH"])) * 1e-3
                              * float(_id_s) * float(_iq_s))
                    _T_lin += _T_rel
            except Exception:   # noqa: BLE001 — no bench, PM-only reference
                _T_rel = None
            if abs(_T_lin) > 1e-6 and _Tavg > 0:
                _sat_droop = {
                    "T_linear_Nm": round(abs(_T_lin), 3),
                    "droop_pct": round(max(0.0, 100.0 * (1.0 - _Tavg / abs(_T_lin))), 2),
                }
                if _T_rel is not None:
                    _sat_droop["T_reluctance_Nm"] = round(_T_rel, 4)
    except Exception:   # noqa: BLE001 — a droop failure must not sink the summary
        _sat_droop = None

    # ── THE REPORTED Ld/Lq: frozen-permeability INCREMENTAL at this point ────
    # The solver measured them on the frames it already solved: the per-element
    # ν of the converged loaded field is held fixed and a unit d- and q-axis
    # current solved on that operator, so Ld = ∂ψd/∂i_d and Lq = ∂ψq/∂i_q are
    # flux-per-amp at THIS iron state and nothing else (fem_solver_2d.
    # frozen_permeability_ldq).  The magnets' own flux in that same loaded iron
    # comes out of the same solve, which turns the ψ_PM sag — the term that
    # contaminated the chord — into a number of its own instead of a suspicion.
    _Ld_mH = _Lq_mH = _Ldq_mH = _psi_sag_pct = None
    _ldq_method = None
    _inc_blk = sbres.get("inc_ldq")
    try:
        if isinstance(_inc_blk, dict) and _inc_blk.get("Ld_mH") is not None:
            _Ld_mH = float(_inc_blk["Ld_mH"])
            _Lq_mH = float(_inc_blk["Lq_mH"])
            _Ldq_mH = (None if _inc_blk.get("Ldq_mH") is None
                       else float(_inc_blk["Ldq_mH"]))
            _ldq_method = "frozen-permeability incremental at the point"
            _pmf = _inc_blk.get("psi_d_pm_frozen_Wb")
            if _psi_pm and _pmf is not None:
                _psi_sag_pct = 100.0 * (1.0 - float(_pmf) / float(_psi_pm))
        else:
            _dq_note = (_dq_note or
                        "this run predates the incremental Ld/Lq measurement — "
                        "re-run to measure them by frozen permeability")
    except Exception:   # noqa: BLE001 — never sink a summary over a diagnostic
        _Ld_mH = _Lq_mH = _Ldq_mH = None

    # ── What the CHORD says, and why it is not the answer ────────────────────
    # With ν frozen the flux linkage splits EXACTLY, ψd = ψ*_PM + Ld·i_d +
    # Ldq·i_q, so the chord and the incremental value differ by an identity:
    #
    #     (ψd − ψ_PM)/i_d  =  Ld  +  [(ψ*_PM − ψ_PM) + Ldq·i_q] / i_d
    #
    # — the magnet flux the LOAD moved, plus the q-axis cross term, divided by
    # a current that may be small.  Both are iron state, neither is
    # flux-per-amp, and at γ = 8°, 89 A they took a 0.041 mH machine to a
    # 0.116 mH reading (measured live, 2026-09-13).  The note quantifies them
    # instead of inferring them from the torque droop, as it used to.
    try:
        if (_Ld_chord is not None and _psi_pm and _psi_sag_pct is not None
                and _Ldq_mH is not None):
            _d_pm = abs(_psi_sag_pct) / 100.0 * abs(float(_psi_pm))
            _x_wb = abs(1e-3 * _Ldq_mH * float(sbres.get("i_q_A") or 0.0))
            _numer = abs(float(sbres.get("psi_d_Wb")) - float(_psi_pm))
            if _numer > 1e-12 and (_d_pm + _x_wb) > 0.4 * _numer:
                _dq_note = (
                    "chord (ψd − ψ_PM)/i_d = %.4f mH — not an inductance here: "
                    "~%.0f%% of that numerator is the magnet flux the load "
                    "moved (%+.1f%% of ψ_PM) plus the q-axis cross term; Ld/Lq "
                    "above are the frozen-permeability incremental values"
                    % (_Ld_chord,
                       min(100.0, 100.0 * (_d_pm + _x_wb) / _numer),
                       -_psi_sag_pct))
    except Exception:   # noqa: BLE001
        pass

    return {
        "rpm": _rpm,
        # I_phase_rms_A is the TERMINAL current — the setpoint the panel holds,
        # the three leads to the inverter — in BOTH connections.  It has to be:
        # every staleness check in the app compares this field with the panel
        # field, and the catalog rows, Compare and the datasheet all read it as
        # "the current this point was set to".  (The builder's `I_phase_rms`
        # argument is the WINDING current the field was driven with; in delta
        # they differ by sqrt(3), and a summary carrying the winding value here
        # left the card permanently "stale" — user 2026-09-12: "экран так и
        # остаётся замыленным".)
        "I_phase_rms_A": round(float(I_phase_rms) * (_sq3 if _is_delta else 1.0), 2),
        "I_terminal_rms_A": round(float(I_phase_rms) * (_sq3 if _is_delta else 1.0), 2),
        # WINDING current — what the field saw, what J coil and Kt per winding
        # amp are billed on.  Equals the terminal current in star.
        "I_winding_rms_A": round(float(I_phase_rms), 2),
        "gamma_deg": round(float(gamma_deg), 2),
        # The winding these numbers belong to.  Torque, voltage and R_phase all
        # move with the connection, so a card that does not name it cannot be
        # checked against the selector — which is exactly how "I changed the
        # connection and nothing moved" became unanswerable.
        "connection": _conn,
        "n_parallel": _npar,
        # STRANDS IN HAND, STRIPS PER ROW, and what they make the coil.  The
        # slot holds num_wires_per_slot wire ROWS of wire_split strips each, a
        # row's strips being consecutive SERIES turns; turns_per_coil =
        # (rows / wire_parallel) × wire_split is what the EMF, Kt and R were
        # solved on, and n_parallel_eff (paths × wire_parallel — the split
        # divides no current) is the divider both the coil current and the flux
        # linkage carry.  Both are on the card because the split moves KV,
        # R_phase and the voltage as well as the slot width, and the numbers are
        # where the user checks it landed.
        "wire_parallel": int(_wpar),
        "turns_per_coil": int(_turns),
        "n_parallel_eff": int(_npar_eff),
        "op_mode": _op_mode,
        # op_mode above is DERIVED from the power-flow sign — honest physics,
        # but useless for "is this run the panel's point?" checks: near a
        # zero-crossing (or an unconventional γ) a generator-mode request can
        # measure motoring power.  This is the mode the request ASKED for.
        "op_mode_requested": mode_requested,
        # WHICH EXCITATION SOURCE produced these numbers.  The summary is what
        # outlives the run (sim.lastSummary, .last_transient.json, Compare, the
        # motor autosave), and the V₁ seed below only means "the voltage that
        # reproduces this point" when the point was solved with the CURRENT as
        # the input — on a voltage/PWM run it is just the voltage that was
        # applied.  Nothing downstream can tell those apart without this field.
        "drive": str(sbres.get("drive") or "current"),
        # Terminal parameters, persisted with every run (they ride the summary
        # into sim.lastSummary, .last_transient.json, Compare and the motor
        # autosave — one write path, no separate store to rot).
        "R_phase_ohm": (round(_R_ph, 6) if _R_ph else None),
        # Terminal-to-terminal: two windings in series (star) or one winding
        # in parallel with the other two in series (delta) — what an ohmmeter
        # across two leads reads.
        "R_line_line_ohm": (round((2.0 / 3.0 if _is_delta else 2.0) * _R_ph, 6)
                            if _R_ph else None),
        # STAR-EQUIVALENT per-phase values: the R and L a datasheet quotes
        # "per phase" for a delta machine are those of the equivalent star,
        # one third of the winding's own.  Identical to the winding in star.
        "R_phase_eq_star_ohm": (round(_R_ph / (3.0 if _is_delta else 1.0), 6)
                                if _R_ph else None),
        "Ld_eq_star_mH": (None if _Ld_mH is None
                          else round(_Ld_mH / (3.0 if _is_delta else 1.0), 4)),
        "Lq_eq_star_mH": (None if _Lq_mH is None
                          else round(_Lq_mH / (3.0 if _is_delta else 1.0), 4)),
        # Ld_mH / Lq_mH ARE the incremental (frozen-permeability) values since
        # 2026-09-20 — every consumer that reads them (the card, §4, the
        # datasheet, the PWM calculator) gets flux-per-amp at this point.
        "Ld_mH": (None if _Ld_mH is None else round(_Ld_mH, 4)),
        "Lq_mH": (None if _Lq_mH is None else round(_Lq_mH, 4)),
        "Ld_inc_mH": (None if _Ld_mH is None else round(_Ld_mH, 4)),
        "Lq_inc_mH": (None if _Lq_mH is None else round(_Lq_mH, 4)),
        # The CROSS term of the same 2×2 matrix.  Symmetric by reciprocity of a
        # linear magnetic circuit, which the solver checks rather than assumes.
        "Ldq_inc_mH": (None if _Ldq_mH is None else round(_Ldq_mH, 4)),
        "ldq_method": _ldq_method,
        # The chord, under its own name: (ψd − ψ_PM_noload)/i_d and ψq/i_q.
        # NOT an inductance under saturation — see `dq_note`.
        "Ld_chord_mH": (None if _Ld_chord is None else round(_Ld_chord, 4)),
        "Lq_chord_mH": (None if _Lq_chord is None else round(_Lq_chord, 4)),
        # WHAT THE LOAD DID TO THE MAGNET FLUX, measured rather than inferred
        # from the torque droop: 100·(1 − ψ*_PM/ψ_PM), with ψ*_PM the magnets'
        # own flux solved on the LOADED field's frozen permeability and ψ_PM
        # the cached no-load probe.  POSITIVE is a sag (the armature reaction
        # saturated the flux path); NEGATIVE means the load saturated a
        # LEAKAGE path instead and more magnet flux reached the gap, which is
        # ordinary on a flux-concentrating rotor.  These are exactly the two
        # numbers the chord Ld differences, so this IS the chord's error term
        # — including the part of it that is the calibration mesh the no-load
        # probe runs on, which is why it belongs in a note and not in a row.
        "psi_pm_sag_pct": (None if _psi_sag_pct is None
                           else round(_psi_sag_pct, 2)),
        "psi_pm_frozen_Wb": (
            None if not isinstance(_inc_blk, dict)
            or _inc_blk.get("psi_d_pm_frozen_Wb") is None
            else round(float(_inc_blk["psi_d_pm_frozen_Wb"]), 6)),
        # …and its Q COMPONENT, which is zero at no load and is NOT zero under
        # cross-saturation: the loaded iron tilts the magnets' own flux off the
        # d-axis (−7.5 mWb on the L180 rated duty).  That term sits in ψq, so
        # it is the reason the chord ψq/i_q is not Lq either.
        "psi_pm_q_frozen_Wb": (
            None if not isinstance(_inc_blk, dict)
            or _inc_blk.get("psi_q_pm_frozen_Wb") is None
            else round(float(_inc_blk["psi_q_pm_frozen_Wb"]), 6)),
        **({"inc_ldq": _inc_blk} if isinstance(_inc_blk, dict) and _inc_blk
           else {}),
        "psi_pm_Wb": (None if _psi_pm is None else round(_psi_pm, 6)),
        "saliency_Lq_over_Ld": (round(_Lq_mH / _Ld_mH, 3)
                                if _Ld_mH not in (None, 0) and _Lq_mH is not None
                                else None),
        "dq_note": _dq_note,
        "T_em_avg_Nm": round(_Tavg, 3),
        "T_ripple_pct": round(abs(float(sbres.get("T_ripple_pct", 0.0))), 1),
        "T_ripple_raw_pct": round(abs(float(sbres.get("T_ripple_raw_pct", 0.0))), 1),
        "T_ripple_filt_pct": round(abs(float(sbres.get("T_ripple_raw_pct",
                                                   sbres.get("T_ripple_pct", 0.0)))), 1),
        # Deprecated compatibility fields: raw ripple, no noise estimate.
        "T_noise_floor_pct": None, "torque_filter_applied": False,
        "P_mech_W": round(_Pmech, 1),
        "V_phase_peak_V": round(_Vpk, 1),
        "V_phase_rms_V": round(_Vrms, 1),
        "V_line_peak_V": round(_Vlpk, 1),
        "V_line_rms_V": round(_Vlrms, 1),
        # TERMINAL CONNECTION.  The line numbers above are the star mapping
        # (sqrt(3)x phase); these are the solver's own, taken off the solved
        # waveforms for the connection actually selected — in delta the winding
        # IS the line, and neither connection puts the zero-sequence triplen on
        # the terminals, so this is the number the DC bus has to cover.
        "star_delta": str(sbres.get("star_delta") or "star"),
        # …and, on a voltage/PWM delta run, WHICH CIRCUIT solved it: the star
        # equivalent on a √3-scaled bus (user 2026-09-14 / PWM study B1).  Keyed
        # only when it applies, so a star run and a current-drive delta run keep
        # the summary they have always had, byte for byte.
        **({"delta_equivalent_star": sbres["delta_equivalent_star"]}
           if isinstance(sbres.get("delta_equivalent_star"), dict) else {}),
        "strand_bonding": str(sbres.get("strand_bonding") or "transposed"),
        "V_line_peak_solved_V": (
            None if sbres.get("V_line_peak_solved_V") is None
            else round(float(sbres["V_line_peak_solved_V"]), 1)),
        "V_line_rms_solved_V": (
            None if sbres.get("V_line_rms_solved_V") is None
            else round(float(sbres["V_line_rms_solved_V"]), 1)),
        "I_line_rms_A": (None if sbres.get("I_line_rms_A") is None
                         else round(float(sbres["I_line_rms_A"]), 1)),
        "L0_mH": sbres.get("L0_mH"),
        "P_cu_circulating_W": round(float(sbres.get("P_cu_circulating_W")
                                          or 0.0), 1),
        "circulating_harmonics": list(sbres.get("circulating_harmonics") or []),
        # KV = rpm / V_peak — the user's (and their Ansys table's) convention:
        # max(rpm)/max(voltage), i.e. the PEAK of the waveform shown right next
        # to this tile, not the fundamental rms (which read ~sqrt(2) higher and
        # contradicted a by-hand rpm/V_line_peak check, 2026-08-04).  NB this is
        # the LOADED voltage: at field-weakening γ it differs from the no-load
        # back-EMF KV; a true no-load KV needs an I=0 run (harm_screening E1).
        "KV_rpm_per_V_phase": (round(_rpm / _Vpk, 2) if _Vpk > 1 else 0.0),
        "KV_rpm_per_V_line": (round(_rpm / _Vlpk, 2) if _Vlpk > 1 else 0.0),
        # NO-LOAD KV from the LOADED run, zero extra solves (user's ask): the
        # back-EMF fundamental is ω_e·ψ_PM with the ψ_PM this summary already
        # measured (cached I=0 probe), so E_line_peak = √3·ω_e·ψ_PM and
        # KV_nl = rpm/E_line_peak — what a spun-by-hand bench reads, free of
        # the IR/IL drop and the load's saturation that sit inside the loaded
        # KV above.  Fundamental only: back-EMF harmonics are excluded.
        "KV_noload_rpm_per_V_line": (lambda _fe: (
            round(_rpm / ((1.0 if _is_delta else _sq3)
                          * 2.0 * math.pi * _fe * _psi_pm), 2)
            if _psi_pm and _fe and _fe > 0 and _rpm > 0 else None))(
                float(sbres.get("f_elec_Hz", 0.0) or 0.0)),
        "V1_LL_V":        _vh.get("V1_LL_V", 0.0),
        "V1_phase_V":     _vh["V1_phase_V"],
        "THD_pct":        _vh["THD_pct"],
        "THD_LL_pct":     _vh["THD_LL_pct"],
        "I1_A":           _ih["I1_A"],
        "THD_I_pct":      _ih["THD_I_pct"],
        # ── SEED FOR AN IMPOSED-VOLTAGE RUN ──────────────────────────────
        # The fundamental of the SOLVED terminal voltage, in exactly the
        # (v_phase_peak, v_delta_deg) coordinates drive="voltage" and
        # drive="pwm_voltage" take.  This is the two-pass workflow the user
        # works in: fix the operating point on the CURRENT drive (where the
        # torque is the thing you dial in), read these two numbers, then run
        # the inverter at the fundamental that reproduces that current — a PWM
        # run driven at a guessed V is a run at a load angle nobody chose.
        # Reported on every drive: on a voltage/PWM run it comes back as the
        # amplitude and angle that were applied, which is a free self-check.
        **_vseed,
        "Kt_Nm_per_Arms": _kt,          # per WINDING (phase) amp, both connections
        # Per LINE amp — what the inverter's current rating is set against.
        # Same number in star; √3 smaller in delta.
        "Kt_Nm_per_A_line": round(_kt / (_sq3 if _is_delta else 1.0), 4),
        "J_coil_A_per_mm2": round(_j_coil, 1),   # I_rms/parallel over one strand's copper section
        # Copper cross-section the PHASE current flows through: one strand ×
        # the parallel paths.  J_phase = I_phase / A_phase equals J_coil by
        # construction, so the two cells cross-check each other.
        "A_phase_mm2": round(_a_cond_mm2 * max(1.0, float(_npar_eff)), 3),
        # Wire coating: measured conductor area over the winding window the teeth
        # leave — geometry, not a wound-in assumption.  None when the CAD cannot
        # build the cross-section.
        **((lambda _sf: ({} if not _sf else {
            "slot_fill_pct": round(100.0 * _sf["fill"], 1),
            "A_slot_mm2": round(_sf["A_slot_mm2"], 1),
            "A_copper_slotted_mm2": round(_sf["A_cu_mm2"], 1),
        }))(_slot_fill)),
        "P_loss_total_W": round(_ploss, 1),
        # Mean |B| over the AIR-GAP clearance, averaged over the period — the
        # number a machine is sized on before anything else, and the one the
        # summary never carried (user 2026-09-10: "для электромагнитного
        # анализа надо ещё рассчитывать среднее поле в зазоре и писать это
        # число в таблицу").  Area-weighted over the elements between the
        # outermost rotating metal and the stator bore, so it is a mean of the
        # field and not of the mesh.  Absent (None) on a result solved before
        # the feature, which is what keeps a stale card from printing a zero.
        **({"B_gap_mean_T": round(_mean("B_gap_mean_T"), 4)}
           if sbres.get("B_gap_mean_T") else {}),
        "P_core_W":     round(_Pfe, 1),            # laminated iron
        # Which Bertotti TERM the core loss is, stator vs rotor.  "The core loss
        # reads low" is only answerable by the split, and the card had no way to
        # show it.  {stator|rotor: hysteresis_W, eddy_W, excess_W, k_f}.
        "P_core_terms": sbres.get("P_fe_terms") or {},
        # The core loss, per half — the two numbers the heat split below is
        # built on, reported in their own right because "which side is the iron
        # loss in" is a cooling question the Core tile could not answer.
        "P_core_stator_W": round(_Pfe_s, 1),
        "P_core_rotor_W":  round(_Pfe - _Pfe_s, 1),
        # ── HEAT TO REMOVE, PER SIDE ─────────────────────────────────────
        # stator = stator iron + all copper; rotor = rotor iron + magnet +
        # shaft + sleeve eddy.  They sum to P_loss_total_W BY CONSTRUCTION
        # (the rotor cell is the complement of the stator cell at the same
        # 0.1 W resolution), so the card can never show a split that leaks
        # watts.  False on `P_loss_split_measured` = this run carries no
        # per-half iron breakdown and the whole core loss was put on the
        # stator; the tooltips say so.
        "P_loss_stator_W": round(_P_stator, 1),
        "P_loss_rotor_W":  round(round(_ploss, 1) - round(_P_stator, 1), 1),
        "P_loss_split_measured": bool(_fe_split_known),
        "P_stranded_W": round(_Pcu, 1),            # copper
        # ── copper AC and the split ──────────────────────────────────────
        # ``wire_split`` = N lays N strips of wire_width side by side per wire
        # row, 2·wire_spacing_x apart.  Since 2026-09-08 those strips are REAL
        # GEOMETRY: the CAD draws them, the mesher gets them as N separate
        # conductors and the coupled σ·∂A/∂t solve imposes each one's own net
        # current — so the solved copper AC is the honest loss of the narrow
        # strips, not of the wide bar they replaced.
        #
        # The flag stays in the payload (the card reads it, and every stored
        # summary carries it) but it is now always False: there is nothing left
        # for the solve to ignore.  It used to fire whenever the coupled solve
        # ran on a split machine, back when the split was electrical-only —
        # strips of wire_width/N with no CAD behind them, assumed ideally
        # transposed — and the mesher was handed the whole bar.  That reading is
        # retired.
        "wire_split": int(_wsplit),
        "cu_ac_solved_ignores_wire_split": False,
        "P_solid_W":    round(_Pmag + _Pshaft + _Psleeve, 1),  # magnet + shaft + sleeve eddy
        # The two big solid conductors on their own (user 2026-09-07: "добавь
        # потери в валу"): the shaft eddy loss is the rotor's largest single
        # heat source on a sleeved machine, and a card that only showed their
        # sum could not say so.
        "P_mag_W":      round(_Pmag, 1),
        "P_shaft_W":    round(_Pshaft, 1),
        # Broken out because a CFRP ring is milliwatts next to the magnets: at
        # 0.1 W resolution it would vanish into P_solid_W and the card would say
        # nothing about a part the user can see in the 3-D view.  Absent (None)
        # when the machine has no sleeve, so no card grows an empty row.
        "P_sleeve_W":   (round(_Psleeve, 4) if _Psleeve else None),
        # "P_solid_W above is a zero that means NOT SOLVED, not no loss."
        # Imposed-voltage runs used to be exactly that: the solver force-dropped
        # the conducting rotor on every one of them, and the missing watts
        # flattered the efficiency (measured live 2026-08-31, a PWM run read a
        # HIGHER efficiency than its sinusoid reference purely because ~4 W of
        # magnet loss fell off the books).  They are solved now — the (A, U, i)
        # bordered Newton carries the magnet/shaft σ·∂A/∂t under the imposed
        # voltage — so the flag follows what the solver ACTUALLY SOLVED
        # (rotor_eddy_solved) instead of the drive name.  It therefore still
        # fires for a caller who asked for rotor_eddy=False, and for the
        # SB_VDRIVE_ROTOR_EDDY=0 escape hatch.  The SummaryTable keys off its
        # presence and needs no change when it is absent.
        # A stale/restored result predates rotor_eddy_solved; the drive test is
        # kept in front so such a payload reads exactly as it did before rather
        # than claiming a solved run was never solved.
        "solid_loss_not_solved": bool(
            str(sbres.get("drive") or "current") in ("voltage", "pwm_voltage")
            and not sbres.get("rotor_eddy_solved")),
        # AXIAL magnet segmentation (`magnet_lamination`): the factor the 2-D
        # magnet eddy loss was multiplied by, and the loop width it was derived
        # from.  {} on a run that predates it; factor 1.0 on a solid magnet.
        "magnet_segmentation": sbres.get("magnet_segmentation") or {},
        # How many discarded frames at θ<0 the coupled eddy solve needed before
        # the σ·∂A/∂t start-up transient was quiet enough to start reporting.
        # It belongs beside P_solid_W because that is the number an un-settled
        # window corrupts first (measured 262 W against a settled 68 W on the
        # 150 mm), so the card that shows it can say what it cost to be honest.
        # Since 2026-09-05 this count also carries the demag PRE-PASS (one more
        # discarded electrical period on an eddy+demag run, where the Br ratchet
        # is finally allowed to look at a settled field); `demag_prepass_frames`
        # says how many of the frames were that, so the tooltip can name it.
        "eddy_warmup_frames": int(sbres.get("eddy_warmup_frames") or 0),
        "demag_prepass_frames": int(sbres.get("demag_prepass_frames") or 0),
        # …and whether that warm-up actually WORKED (user, 2026-09-07).  The
        # frame count alone was read as proof of a settled window; it is not —
        # the march can end at its one-shot cap with the transient still
        # running, which is what the 90-point sweep of that day did on all 90
        # points (57 frames each) while its shaft eddy loss swung 504 → 3558 →
        # 6652 W between neighbouring air gaps.  When eddy_settled is False the
        # Solid tile beside this says so, because that tile is the number the
        # unsettled window corrupts first.
        # None, not True, when the solve did not say: a payload from before
        # 2026-09-07 (or a restored old result) has no verdict to report, and
        # inventing "settled" for it would be the very claim this change ends.
        # The card treats null exactly as it treated the missing key.
        "eddy_settled": (None if sbres.get("eddy_settled") is None
                         else bool(sbres["eddy_settled"])),
        "eddy_capped": (None if sbres.get("eddy_capped") is None
                        else bool(sbres["eddy_capped"])),
        "eddy_settle_residual": sbres.get("eddy_settle_residual"),
        "eddy_settle_tol": sbres.get("eddy_settle_tol"),
        # Did this run CONTINUE a previous one's state instead of solving it?
        # Both are False on every interactive Run by construction — the flag
        # that allows it (SB_SEED_FROM_PREVIOUS) is set only in the optimizer's
        # eval environment (user 2026-09-06, "каждый следующий расчёт берётся
        # из предыдущего", which is a rule about sweeps).  Carried anyway, so a
        # saved simulation states it rather than leaving it to be assumed.
        "warm_seeded": bool(sbres.get("warm_seeded", False)),
        "demag_seeded": bool(sbres.get("demag_seeded", False)),
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
        # ── imposed-voltage / PWM settling honesty (B5, PWM study 2026-09-13)
        # The DC left in the phase currents of the REPORTED electrical period,
        # worst phase.  It is 0 A on a converged orbit, and when it is not, it
        # IS the reported torque ripple and current ripple: the L155 peak duty
        # came back with 55 % ripple against the machine's own 0.9 % while
        # carrying 43 A of DC on a 433 A fundamental.  None on an imposed-
        # current run (no circuit state to settle) and on any payload from
        # before this existed.
        "pwm_dc_residual_A": sbres.get("v_dc_residual_A"),
        "pwm_dc_residual_phase": sbres.get("v_dc_residual_phase"),
        "pwm_dc_tol_A": sbres.get("v_dc_residual_tol_A"),
        "pwm_dc_unconverged": sbres.get("v_dc_unconverged"),
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
        # Per-part accounting (included / reference / excluded).  Empty on a
        # default machine, so a card built before this feature reads the same.
        "part_states": _masses.get("part_states") or {},
        # Rotor moment of inertia about the shaft axis (∬r²dA on the mass's own
        # CAD polygons; rotor iron at its lamination k_f) — drive sizing needs
        # it next to the torque, not in a separate tool.
        "rotor_inertia": _masses.get("rotor_inertia"),
        # Retaining-sleeve burst check — None on a machine without a sleeve, so
        # no existing card grows a row.  Reported, never gating: see
        # _sleeve_hoop for what it leaves out (the magnet pressure).
        "sleeve_hoop": _sleeve_hoop(_geo_cfg, _rpm),
        # Demagnetisation coefficient — present only when the run modelled
        # irreversible demag.  Headline = bh_loss_pct, the volume-weighted
        # ENERGY-product deficit ((BH)max ∝ Br², user's criterion): it speaks
        # grade language — kept-energy × the nominal grade number (N52 = 52
        # MGOe) is the grade the magnet has effectively become.  The Br-based
        # flux/torque bound rides along for the drive view.
        "demag": _demag_with_grade(sbres.get("demag_summary")),
        # 3D end-effect correction — Stage A k_flux for THIS machine (by
        # geometry fingerprint), with the 2D numbers it corrects: a 13 mm stack
        # on Ø85 spills 7.3 % of its flux past the laminations, and the 2D
        # solve cannot see it.
        "end3d": (lambda _r: (None if not _r else {
            **_r,
            "T_corrected_Nm": round(_Tavg * float(_r["k_flux"]), 3),
            "V_line_peak_corrected_V": (
                round(_Vlpk * float(_r["k_flux"]), 2) if _Vlpk else None),
        }))(_end3d_lookup(sbres.get("geo_fingerprint"))),
        # Bench Ld/Lq — the LCR-meter measurement, simulated: small-signal
        # values at the I≈0 iron state, per machine + connection.  Cache READ
        # only (this builder also runs on restore rebuilds); the live-machine
        # solve path fills the cache right before building this summary.
        "bench_ldq": _bench_read(sbres.get("geo_fingerprint"),
                                 sbres.get("connection")),
        # Torque lost to SATURATION — the demag cell's sibling (user's point:
        # torque falls to saturation too, and the two must not be conflated).
        # Zero extra solves: the unsaturated PM torque is 1.5·p·ψ_PM·i_q with
        # the ALREADY-measured no-load ψ_PM (cached per geometry) and the run's
        # own dq current; the actual torque sits below it by exactly the
        # saturation sag of ψd (the reluctance term can push it the other way —
        # a negative droop is real, not an error).  Present only when the dq
        # frame passed its self-check (ψ_PM/iq exist).
        "saturation": _sat_droop,
        # Motor constant Km = T / √P_cu_DC [N·m/√W] — torque per square root of
        # the ohmic loss paid for it.  Operating-point-independent as long as
        # the iron is not saturating (T ∝ I, P ∝ I²), which is why robotics
        # sizes actuators on it; Km/m (the specific motor constant) is the
        # figure of merit that survives scaling.  Computed at the SOLVED coil
        # temperature — R grows ~39 %/100 °C, so a datasheet Km at 25 °C reads
        # higher than the same machine hot.  No-load (P_cu→0) has no Km: null.
        "Km_Nm_sqrtW": (round(_Tavg / _math.sqrt(_Pcu_dc), 4)
                        if _Pcu_dc > 1e-9 and _Tavg > 0 else None),
        "Km_per_mass_Nm_sqrtW_kg": (
            round(_Tavg / _math.sqrt(_Pcu_dc) / max(_m_tot, 1e-6), 4)
            if _Pcu_dc > 1e-9 and _Tavg > 0 else None),
        # Shape version — bumped when a NEW derived quantity is added to this
        # summary, so a cache/restore hit knows its stored summary is missing
        # cells and rebuilds it with the current code (_refresh_summary_shape).
        # v2: rotor_inertia + Km/Km-per-mass (2026-08-22).
        "summary_shape_v": _SUMMARY_SHAPE_V,
        "torque_per_mass_Nm_kg": round(_Tavg / max(_m_tot, 1e-6), 3),
        "power_per_mass_W_kg":   round(_Pmech / max(_m_tot, 1e-6), 1),
        "loss_density_W_kg":     round(_ploss / max(_m_tot, 1e-6), 1),
        # rms terminal phase current where the current was the ANSWER (imposed
        # voltage / PWM / imposed waveform).  ABSENT on a current-drive run,
        # where the request already says it — a key that appears only where it
        # means something cannot contradict I_phase_rms_A.
        **({} if sbres.get("I_phase_rms_solved_A") is None else {
            "I_phase_rms_solved_A": round(
                float(sbres["I_phase_rms_solved_A"]), 3)}),
        # ── GENERATOR → BATTERY ──────────────────────────────────────────
        # Presence-gated twice over: only a run that carried a `battery`
        # payload AND drove the machine from an imposed voltage gets this key,
        # so every stored case, every regression pin and every current-drive
        # run reads exactly as it did before.
        **((lambda _b: ({} if not _b else {"battery_charge": _b}))(
            _battery_charge_block(sbres, op_mode=_op_mode, p_mech_w=_Pmech,
                                  p_loss_w=_ploss))),
        # ── THE MECHANICAL HALF (2026-09-08) ─────────────────────────────
        # Bearings and windage, on the RUN instead of in the browser.  Absent
        # — not zero — on a machine that names no bearings, on a background
        # solve and on a candidate geometry; see `_mech_loss_fields`.
        **_mech_loss_fields(
            sbres, geo_override=geo_override, rpm=_rpm, p_mech_w=_Pmech,
            p_loss_w=_ploss, efficiency=_eff, op_mode=_op_mode,
            mass_components=_masses.get("components")),
    }
