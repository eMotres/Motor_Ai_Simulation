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


def _current_geom_hash_and_params():
    """Return (hash, params_dict) of the LIVE UI-edited geometry.

    Falls back to (None, None) if the geometry service is unavailable, in which
    case CadQueryMotor() reads config defaults.
    """
    import hashlib, json
    try:
        from motor_ai_sim.services.geometry_service import get_current_geometry
        pd = get_current_geometry().to_dict()
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

    # Atomic write (temp + replace) — write_text truncate-then-write leaves a
    # window where a concurrent reader parses an empty config and clobbers it.
    import os as _os
    _tmp = cfg_path.with_suffix(".yaml.tmp")
    _tmp.write_text(''.join(result), encoding="utf-8")
    _os.replace(_tmp, cfg_path)
    clear_config_cache()
    return {"status": "ok", "updated": updates}


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Physics analytics (analytical, no PINN needed)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/physics")
def get_physics(
    rotor_angle_deg: float = 0.0,
    gamma_deg:       float = 0.0,
    n_points:        int   = 360,
):
    """Return full analytical physics for the current operating point.

    Computes (no PINN required):
      - Winding layout (slot -> phase, direction)
      - Spatial MMF distribution around airgap
      - Air-gap flux density (MMF + PM contribution)
      - Spatial harmonic spectrum (FFT of B)
      - Slot current vector J_z per slot
      - Loss estimates (DC copper + classical eddy)
    """
    import math, numpy as np
    from motor_ai_sim.config import get_config
    from motor_ai_sim.simulation.geometry_2d import (
        MotorDomains2D, params_from_config, build_winding_layout,
    )

    cfg = get_config()
    sim = cfg.get("simulation", {})
    geo = cfg.get("geometry", {})
    wind = cfg.get("winding", {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating point ───────────────────────────────────────────────────────
    I_phase_rms  = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_series     = wind.get("n_series", 2)
    freq         = sim.get("frequency", 921.67)
    rpm          = sim.get("rpm", 3950.0)
    Br           = 1.19  # T from materials
    mu0          = 4e-7 * math.pi

    I_coil_rms  = I_phase_rms / n_parallel
    I_coil_peak = I_coil_rms * math.sqrt(2)
    n_wires     = geo.get("num_wires_per_slot", 14)
    num_slots   = p.num_slots
    num_poles   = p.num_poles
    pole_pairs  = num_poles // 2
    air_gap_m   = (p.r_air_out - p.r_air_in)
    omega_e     = 2 * math.pi * freq

    # Phase currents at this rotor angle (γ=0 → q-axis ⇒ +π/2 shift)
    theta_elec = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + 90.0)
    I = {
        'A':  I_coil_peak * math.cos(theta_elec),
        'B':  I_coil_peak * math.cos(theta_elec - 2*math.pi/3),
        'C':  I_coil_peak * math.cos(theta_elec + 2*math.pi/3),
    }

    # ── Spatial arrays ────────────────────────────────────────────────────────
    theta = np.linspace(0, 2*math.pi, n_points, endpoint=False)
    theta_deg = np.degrees(theta)

    # ── MMF distribution ──────────────────────────────────────────────────────
    # Each slot contributes a rectangular window (width = slot angular width)
    slot_pitch    = 2*math.pi / num_slots
    slot_ang_half = math.asin(p.slot_width_m / (2 * p.r_stator_in))  # half-angle

    MMF = np.zeros(n_points)
    slot_currents = []
    for i, (phase, direction) in enumerate(d.winding_layout):
        I_slot = direction * I[phase] * n_wires  # A-turns
        phi_c  = i * slot_pitch
        # Rectangular window around slot center
        dphi = ((theta - phi_c + math.pi) % (2*math.pi)) - math.pi
        mask = np.abs(dphi) < slot_ang_half
        MMF += I_slot * mask.astype(float)
        slot_currents.append({
            "slot": i,
            "phase": phase,
            "direction": direction,
            "phi_center_deg": math.degrees(phi_c),
            "I_slot_A": float(I_slot),
            "J_z_MA_m2": float(I_slot / (p.slot_width_m * p.slot_height_m)),
        })

    # ── Air-gap B from windings ───────────────────────────────────────────────
    # Linear air-gap model: B_winding = mu0 * MMF / g
    B_winding = mu0 * MMF / air_gap_m

    # ── PM contribution: alternating rectangular poles ────────────────────────
    pole_pitch   = 2*math.pi / num_poles
    mag_arc_half = pole_pitch * p.magnet_fill_fraction / 2
    B_pm = np.zeros(n_points)
    for i, polarity in enumerate(d.magnet_polarity):
        phi_c   = (i + 0.5) * pole_pitch
        # Shift by rotor angle (magnets rotate with rotor)
        phi_c_r = phi_c + math.radians(rotor_angle_deg)
        dphi    = ((theta - phi_c_r + math.pi) % (2*math.pi)) - math.pi
        mask    = np.abs(dphi) < mag_arc_half
        B_pm   += polarity * Br * mask.astype(float)

    B_total = B_winding + B_pm

    # ── Harmonic spectrum (FFT) ───────────────────────────────────────────────
    N = len(B_total)
    fft_B = np.fft.rfft(B_total) / N
    harmonics_amp = (2 * np.abs(fft_B)).tolist()
    harmonics_phase = np.degrees(np.angle(fft_B)).tolist()
    harm_orders = list(range(len(harmonics_amp)))

    # ── Loss estimates (analytical) ───────────────────────────────────────────
    # DC copper
    sigma_cu = 5.8e7  # S/m at 20C
    rho_cu   = 1.72e-8 * (1 + 0.00393 * (120 - 20))  # at 120C
    r_mid    = p.r_stator_in + p.slot_height_m * 0.5
    L_turn   = 2 * (math.pi * r_mid / num_slots + p.stack_length)
    R_coil   = rho_cu * L_turn * n_wires / (p.slot_width_m * geo.get("wire_height", 0.6)*1e-3)
    R_phase  = R_coil  # 2P2S: same as coil
    P_cu_dc  = 3 * R_phase * I_phase_rms**2

    # AC proximity (analytical, thin wire regime)
    B_peak_ag = float(np.max(np.abs(B_total)))
    delta_cu  = math.sqrt(2 / (omega_e * mu0 * sigma_cu))
    d_wire    = geo.get("wire_height", 0.6) * 1e-3
    d_over_delta = d_wire / delta_cu
    # Proximity factor (Dowell, simplified)
    k_prox = (d_over_delta**4) / 48 if d_over_delta < 1 else d_over_delta**2 / 4
    P_cu_prox = k_prox * P_cu_dc

    # Magnet eddy (classical)
    sigma_mag = 6.25e5
    V_mag = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * p.stack_length * p.magnet_fill_fraction
    r_mid_mag = (p.r_rotor_out + p.r_rotor_in) / 2
    d_tang = 2*math.pi * r_mid_mag / num_poles * p.magnet_fill_fraction
    d_rad  = p.r_rotor_out - p.r_rotor_in
    d_eff  = min(d_tang, d_rad)
    # Use actual computed B_pm peak
    B_pm_peak = float(np.max(np.abs(B_pm))) if np.max(np.abs(B_pm)) > 0.01 else 0.8
    P_mag_eddy = sigma_mag * omega_e**2 * B_pm_peak**2 * d_eff**2 * V_mag / 24

    # Bertotti iron estimate (stator, rough)
    kh, kc = 2.5, 0.003
    B_fe_rms = float(np.sqrt(np.mean(B_total**2)))
    vol_stator = math.pi * (p.r_stator_out**2 - p.r_stator_in**2) * p.stack_length
    P_fe_stat  = (kh * freq * B_fe_rms**2 + kc * freq**2 * B_fe_rms**2) * vol_stator * 7650
    P_fe_rotor = P_fe_stat * 0.15  # rough estimate

    # Skin depth
    delta_mag = math.sqrt(2 / (omega_e * mu0 * sigma_mag))

    return {
        # Winding
        "winding_layout": [
            {"slot": i, "phase": ph, "direction": sgn}
            for i, (ph, sgn) in enumerate(d.winding_layout)
        ],
        "slot_currents": slot_currents,

        # Spatial waveforms (360 points)
        "theta_deg": theta_deg.tolist(),
        "MMF":       MMF.tolist(),
        "B_winding": B_winding.tolist(),
        "B_pm":      B_pm.tolist(),
        "B_total":   B_total.tolist(),

        # Harmonics
        "harmonics": [
            {"order": n, "amplitude": harmonics_amp[n], "phase_deg": harmonics_phase[n]}
            for n in range(min(50, len(harmonics_amp)))
        ],

        # ── Losses (physically correct estimates) ────────────────────────────
        # NOTE: linear-B model overestimates Fe & Mag by 5-10x.
        # We use specific-loss [W/kg] data for steel and slot-harmonic B for magnets.
        **_compute_losses(p, geo, wind, sim, MMF, B_pm, freq, omega_e, P_cu_dc, P_cu_prox),

        # ── Torque estimate ───────────────────────────────────────────────────
        "torque": _compute_torque(p, geo, wind, sim, gamma_deg),

        # ── Component masses ──────────────────────────────────────────────────
        "masses": _compute_masses(p, geo),

        # Key scalars
        "scalars": {
            "I_coil_peak_A":    round(I_coil_peak, 2),
            "I_coil_rms_A":     round(I_coil_rms, 2),
            "B_total_peak_T":   round(float(np.max(np.abs(B_total))), 4),
            "B_pm_peak_T":      round(float(np.max(np.abs(B_pm))), 4),
            "B_winding_peak_T": round(float(np.max(np.abs(B_winding))), 4),
            "MMF_peak_At":      round(float(np.max(np.abs(MMF))), 1),
            "delta_cu_mm":      round(delta_cu * 1e3, 4),
            "delta_mag_mm":     round(delta_mag * 1e3, 3),
            "d_wire_over_delta": round(d_wire / delta_cu, 4),
            "air_gap_mm":       round(air_gap_m * 1e3, 3),
            "R_phase_mOhm":     round(R_phase * 1e3, 2),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7.  2-D Field map  —  analytical Green's function approach
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/physics/field2d")
def get_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg:       float = 0.0,
    grid_size:       int   = 150,
):
    """Compute 2-D analytical field map of the motor cross-section.

    Method: superposition of Green's function solutions
      A_z(x,y) = Σ_{slots}   −μ₀/(2π) · I_slot · ln|r − r_slot|
               + Σ_{PM faces} −Br/(2π)  · dl      · ln|r − r_face|

    From A_z:  B_x = ∂A_z/∂y,  B_y = −∂A_z/∂x   (central differences)
               |B| = sqrt(B_x²+B_y²)

    PM equivalent surface currents: radially magnetised magnets have
    bound surface currents K_z = ±Br/μ₀ at their angular faces only
    (the cylindrical faces carry zero K because M·n̂ = 0 there).

    Limitations vs PINN:
      - No nonlinear μ(B) — steel treated as free space with large μ
      - No back-reaction from eddy currents
      - Approximate (linear superposition)
      Accuracy: ~correct topology, amplitudes off by 20-50% in steel regions.
    """
    import math, numpy as np
    from motor_ai_sim.simulation.geometry_2d import (
        MotorDomains2D, params_from_config,
    )
    from motor_ai_sim.config import get_config

    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    mu0         = 4e-7 * math.pi
    pole_pairs  = p.num_poles // 2
    I_phase_rms = sim.get("max_current", 85.0)
    n_parallel  = wind.get("n_parallel", 2)
    n_wires     = geo.get("num_wires_per_slot", 14)
    Br          = 1.19

    I_coil_peak = I_phase_rms / n_parallel * math.sqrt(2)
    # q-axis convention: add +π/2 so γ=0 puts current on q-axis (max torque)
    theta_elec  = math.radians(0 * pole_pairs + gamma_deg + 90.0)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_elec),
        'B': I_coil_peak * math.cos(theta_elec - 2*math.pi/3),
        'C': I_coil_peak * math.cos(theta_elec + 2*math.pi/3),
    }

    # ── Grid (Cartesian, square covering stator OD) ───────────────────────────
    R    = p.r_stator_out * 1.02
    gs   = min(max(grid_size, 60), 250)
    xs   = np.linspace(-R, R, gs)
    ys   = np.linspace(-R, R, gs)
    XX, YY = np.meshgrid(xs, ys)
    # Radial distance for masking
    RR   = np.sqrt(XX**2 + YY**2)

    EPS  = 1e-9   # avoid log(0)
    A_z  = np.zeros((gs, gs))

    # ── 1. Slot line currents (one per slot at centroid) ──────────────────────
    slot_pitch = 2 * math.pi / p.num_slots
    r_slot_mid = p.r_stator_in + p.slot_height_m * 0.5

    for i, (phase, direction) in enumerate(d.winding_layout):
        phi  = i * slot_pitch + math.radians(0)   # stator fixed
        I_sl = direction * I_ph[phase] * n_wires   # A-turns
        x0   = r_slot_mid * math.cos(phi)
        y0   = r_slot_mid * math.sin(phi)
        dist = np.sqrt((XX - x0)**2 + (YY - y0)**2) + EPS
        A_z += -mu0 / (2*math.pi) * I_sl * np.log(dist)

    # ── 2. PM equivalent surface currents — TANGENTIAL magnetization ─────
    # Magnetization M = polarity·Br·φ̂ (tangent to magnet bottom edge).
    # Bound surface current K = M × n̂ lives only on the radial (top/bottom)
    # faces — not the angular ones — so we discretise each magnet's
    # rotor-facing arc and air-gap-facing arc into N_a angular samples.
    #
    #   Top face   (n̂ = +r̂, r = r_rotor_out): K_z = -polarity·Br/μ₀
    #   Bottom face(n̂ = −r̂, r = r_magnet_in): K_z = +polarity·Br/μ₀
    N_a       = 40
    pole_pit  = 2 * math.pi / p.num_poles
    fill_up   = geo.get("magnet_fill_up",   0.44)   # narrow top
    fill_down = geo.get("magnet_fill_down",  0.9)   # wide bottom
    rotor_hh  = geo.get("rotor_house_height", 0.0) * 1e-3   # mm → m
    mag_up_gap = geo.get("magnet_up_gap", 0.0) * 1e-3      # mm → m

    r_top     = p.r_rotor_out - mag_up_gap        # air-gap-facing edge
    r_bot     = p.r_rotor_in + rotor_hh           # rotor-facing edge

    theta_rotor = math.radians(rotor_angle_deg)

    # Pre-compute angular sample offsets on each face
    half_top = fill_up   * pole_pit / 2
    half_bot = fill_down * pole_pit / 2
    da_top   = 2 * half_top / N_a
    da_bot   = 2 * half_bot / N_a
    off_top  = np.linspace(-half_top + da_top/2, half_top - da_top/2, N_a)
    off_bot  = np.linspace(-half_bot + da_bot/2, half_bot - da_bot/2, N_a)

    # Line current per point [A] = K_z [A/m] · arc element [m]
    I_top_unit = -Br / mu0 * r_top * da_top   # times polarity per magnet
    I_bot_unit = +Br / mu0 * r_bot * da_bot

    XX_f = XX.ravel(); YY_f = YY.ravel()

    for i, polarity in enumerate(d.magnet_polarity):
        phi_c = (i + 0.5) * pole_pit + theta_rotor
        a_top = phi_c + off_top
        a_bot = phi_c + off_bot

        xT = r_top * np.cos(a_top); yT = r_top * np.sin(a_top)
        xB = r_bot * np.cos(a_bot); yB = r_bot * np.sin(a_bot)

        xs_src = np.concatenate([xT, xB])
        ys_src = np.concatenate([yT, yB])
        Ks     = np.concatenate([
            np.full(N_a, polarity * I_top_unit),
            np.full(N_a, polarity * I_bot_unit),
        ])

        dx2  = (XX_f[None, :] - xs_src[:, None])**2
        dy2  = (YY_f[None, :] - ys_src[:, None])**2
        dist = np.sqrt(dx2 + dy2) + EPS

        contrib = np.sum(-mu0 / (2*math.pi) * Ks[:, None] * np.log(dist), axis=0)
        A_z    += contrib.reshape(gs, gs)

    # ── 3. Flux density  B = curl(A_z) ────────────────────────────────────────
    dx   = xs[1] - xs[0]
    dy   = ys[1] - ys[0]
    # Central differences  (pad boundary with 0)
    B_y  = -np.gradient(A_z, dx, axis=1)   # B_y = -∂A_z/∂x
    B_x  =  np.gradient(A_z, dy, axis=0)   # B_x = +∂A_z/∂y
    B_mag = np.sqrt(B_x**2 + B_y**2)

    # ── 4. Current density map J_z  (non-zero in slots) ───────────────────────
    J_z_map = np.zeros((gs, gs))
    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    for i, (phase, direction) in enumerate(d.winding_layout):
        phi   = i * slot_pitch
        I_sl  = direction * I_ph[phase] * n_wires
        J_sl  = I_sl / slot_area                   # [A/m²]
        x0    = r_slot_mid * math.cos(phi)
        y0    = r_slot_mid * math.sin(phi)
        # Mark pixels within slot rectangle (rotated)
        cos_p = math.cos(phi); sin_p = math.sin(phi)
        # Local coords: u = tangential, v = radial
        u = (XX - x0) * (-sin_p) + (YY - y0) * cos_p
        v = (XX - x0) *   cos_p  + (YY - y0) * sin_p
        mask = (np.abs(u) < p.slot_width_m/2) & \
               (np.abs(v) < p.slot_height_m/2)
        J_z_map[mask] = J_sl

    # ── 5. Domain map  — REAL GEOMETRY from CadQueryMotor.get_2d_polygons() ──
    # Uses the SAME Shapely polygons as the 3D motor visualisation.
    # Coordinates in get_2d_polygons() are mm; grid XX/YY are metres → scale ×1000.

    domain = np.zeros((gs, gs), dtype=np.int8)

    try:
        from PIL import Image, ImageDraw
        polys = _get_motor_geom(rotor_angle_deg=rotor_angle_deg)

        # ── Rasterise each polygon domain into the domain array ───────────────
        # PIL is much faster than Shapely point-in-polygon for grid classification.
        # Coordinate transform: mm → pixel index
        # grid extent: [-R, R] metres = [-R*1000, R*1000] mm
        R_mm  = R * 1e3                          # half-extent in mm
        scale = gs / (2 * R_mm)                  # pixels per mm

        def mm_to_px(coords_mm):
            """Convert list of (x_mm, y_mm) → list of (px, py) pixel coords."""
            result = []
            for x, y in coords_mm:
                px = int((x + R_mm) * scale)
                py = int((R_mm - y) * scale)   # flip Y (PIL top=0)
                result.append((px, py))
            return result

        def _rasterise_poly(poly, val: int, img_arr: np.ndarray):
            """Draw polygon onto img_arr (gs×gs uint8) with value val."""
            if poly is None or poly.is_empty:
                return
            from shapely.geometry import MultiPolygon as SMPoly2
            geoms = list(poly.geoms) if isinstance(poly, SMPoly2) else [poly]
            for g in geoms:
                if g.is_empty or g.area < 0.01:
                    continue
                ext_px = mm_to_px(g.exterior.coords)
                if len(ext_px) < 3:
                    continue
                img    = Image.new('L', (gs, gs), 0)
                draw   = ImageDraw.Draw(img)
                draw.polygon(ext_px, fill=val)
                # Subtract interior holes
                for hole in g.interiors:
                    h_px = mm_to_px(hole.coords)
                    draw.polygon(h_px, fill=0)
                mask = np.array(img, dtype=np.uint8)
                img_arr[mask == val] = val

        dom_img = np.zeros((gs, gs), dtype=np.int8)

        # Draw in z-order (later overwrites earlier)
        _rasterise_poly(polys['stator'],  1,  dom_img)
        _rasterise_poly(polys['air_gap'], 3,  dom_img)
        for mag_poly, polarity in polys['magnets']:
            _rasterise_poly(mag_poly, 4 if polarity > 0 else 44, dom_img)
        _rasterise_poly(polys['rotor'],   5,  dom_img)
        _rasterise_poly(polys['shaft'],   6,  dom_img)
        for coil_poly in polys['coils']:
            _rasterise_poly(coil_poly, 2, dom_img)

        # Convert 44 → int8: PIL only supports 0-255; map 44 stays 44 (fits in int8)
        # Map: 0=outside, 1=stator, 2=winding, 3=airgap,
        #       4=magN, 44→use -4 for int8, 5=rotor, 6=shaft
        # Keep 44 as-is (int8 range -128..127, 44 fits fine)
        domain = dom_img

        # Update J_z for winding pixels
        slot_area_v = p.slot_width_m * p.slot_height_m * p.fill_factor
        for i, (phase, direction) in enumerate(d.winding_layout):
            phi   = i * slot_pitch
            cos_p = math.cos(phi); sin_p = math.sin(phi)
            cx    = p.r_stator_in * cos_p
            cy    = p.r_stator_in * sin_p
            u     =  (XX - cx) * (-sin_p) + (YY - cy) * cos_p
            v     =  (XX - cx) *   cos_p  + (YY - cy) * sin_p
            w_msk = (v >= 0) & (v <= p.slot_height_m) & (np.abs(u) < p.slot_width_m/2)
            I_sl  = direction * I_ph[phase] * n_wires
            J_z_map[w_msk & (domain == 2)] = I_sl / slot_area_v

        log.info("field2d: real geometry rasterised (%dx%d) OK", gs, gs)

    except Exception as e:
        log.warning("field2d: real geometry failed (%s), using analytical fallback", e)
        # Fallback: simple analytical domain map
        domain[RR <= p.r_stator_out] = 1
        domain[RR < p.r_air_out]     = 3
        t_r = np.clip((RR - p.r_rotor_in) / (p.r_rotor_out - p.r_rotor_in + 1e-9), 0, 1)
        fill_half_r = (fill_up + t_r*(fill_down - fill_up)) * pole_pit / 2
        angle_map = np.arctan2(YY, XX)
        for i, pol in enumerate(d.magnet_polarity):
            phi_c = (i+0.5)*pole_pit + theta_rotor
            dphi  = ((angle_map - phi_c + math.pi) % (2*math.pi)) - math.pi
            in_r  = (RR >= p.r_rotor_in) & (RR <= p.r_rotor_out)
            domain[in_r & (np.abs(dphi) < fill_half_r)] = 4 if pol > 0 else 44
        domain[(domain == 3) & (RR < p.r_rotor_in)]  = 5
        domain[(domain == 5) & (RR < p.r_shaft_in)]   = 6
        # Slot winding — simple rectangular
        for i, (phase, direction) in enumerate(d.winding_layout):
            phi = i*slot_pitch; cp = math.cos(phi); sp = math.sin(phi)
            cx = p.r_stator_in*cp; cy = p.r_stator_in*sp
            u  =  (XX-cx)*(-sp) + (YY-cy)*cp
            v  =  (XX-cx)*cp    + (YY-cy)*sp
            domain[(v>=0)&(v<=p.slot_height_m)&(np.abs(u)<p.slot_width_m/2)] = 2

    # ── Clamp B in steel (saturation ~1.8 T, linear model overestimates) ──────
    B_max_steel = 1.8
    steel_mask  = (domain == 1) | (domain == 5)
    B_mag_clamped        = B_mag.copy()
    B_mag_clamped[steel_mask] = np.minimum(B_mag[steel_mask], B_max_steel)

    # ── Magnet positions & polarity for visualization ────────────────────────────
    magnets = []
    for i, polarity in enumerate(d.magnet_polarity):
        phi_c = (i + 0.5) * (2 * math.pi / p.num_poles) + theta_rotor
        r_mid = (p.r_rotor_in + p.r_rotor_out) / 2
        x_mid = r_mid * math.cos(phi_c)
        y_mid = r_mid * math.sin(phi_c)
        magnets.append({
            "index": i,
            "polarity": int(polarity),  # +1 for N, -1 for S
            "x_m": float(x_mid),
            "y_m": float(y_mid),
            "r_inner_m": float(p.r_rotor_in),
            "r_outer_m": float(p.r_rotor_out),
            "angle_rad": float(phi_c),
        })

    # ── Serialize real CadQuery polygons → vector overlay on field map ───────
    # Coordinates are in mm in get_2d_polygons(), convert to metres to match extent.
    def _poly_to_rings_m(poly):
        """Shapely (Multi)Polygon → list of {exterior, holes} in metres."""
        from shapely.geometry import MultiPolygon as _SMP
        if poly is None or poly.is_empty:
            return []
        geoms = list(poly.geoms) if isinstance(poly, _SMP) else [poly]
        out = []
        for g in geoms:
            if g.is_empty or g.area < 0.01:
                continue
            ext = [[x*1e-3, y*1e-3] for x, y in g.exterior.coords]
            holes = [[[x*1e-3, y*1e-3] for x, y in h.coords] for h in g.interiors]
            out.append({"exterior": ext, "holes": holes})
        return out

    polygons_payload = {"stator": [], "rotor": [], "shaft": [],
                        "coils": [], "magnets": []}
    try:
        polys_real = _get_motor_geom(rotor_angle_deg=rotor_angle_deg)
        polygons_payload["stator"] = _poly_to_rings_m(polys_real.get("stator"))
        polygons_payload["rotor"]  = _poly_to_rings_m(polys_real.get("rotor"))
        polygons_payload["shaft"]  = _poly_to_rings_m(polys_real.get("shaft"))
        for coil_poly in polys_real.get("coils", []):
            for ring in _poly_to_rings_m(coil_poly):
                polygons_payload["coils"].append(ring)
        for mag_poly, polarity in polys_real.get("magnets", []):
            rings = _poly_to_rings_m(mag_poly)
            if rings:
                polygons_payload["magnets"].append({
                    "polarity": int(polarity),
                    "rings": rings,
                })
    except Exception as e:
        log.warning("field2d: failed to serialize polygons (%s)", e)

    # Flatten for JSON  (row-major, left→right, bottom→top)
    def flat(arr):
        return arr.ravel().tolist()

    return {
        "grid_size":    gs,
        "extent":       [-R, R, -R, R],   # [xmin, xmax, ymin, ymax] in metres
        "A_z":          flat(A_z),
        "B_x":          flat(B_x),
        "B_y":          flat(B_y),
        "B_mag":        flat(B_mag_clamped),
        "J_z":          flat(J_z_map),
        "domain":       flat(domain),
        "stats": {
            "A_z_min":    float(A_z.min()),
            "A_z_max":    float(A_z.max()),
            "B_mag_max":  float(B_mag_clamped.max()),
            "B_mag_mean": float(B_mag_clamped[domain > 0].mean()),
            "J_z_max":    float(np.abs(J_z_map).max()),
        },
        "geometry": {
            "r_stator_out": p.r_stator_out,
            "r_stator_in":  p.r_stator_in,
            "r_rotor_out":  p.r_rotor_out,
            "r_rotor_in":   p.r_rotor_in,
            "r_shaft_in":   p.r_shaft_in,
            "r_air_out":    p.r_air_out,
            "r_air_in":     p.r_air_in,
            "num_slots":    p.num_slots,
            "num_poles":    p.num_poles,
        },
        "rotor_angle_deg": rotor_angle_deg,
        "gamma_deg":       gamma_deg,
        "magnets":        magnets,  # 28 magnets with position, polarity, angle
        "polygons":       polygons_payload,  # real CadQuery polygons in metres
        "note": "Analytical Green's function (superposition). Tangential PM magnetization (M⊥bottom edge), alternating per polarity. Steel treated as μ_r=1. PINN solves the exact nonlinear PDE.",
    }


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
    _ghash, _params_dict = _current_geom_hash_and_params()
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
    key = (round(rotor_angle_deg, 3), round(mesh_size_mm, 2),
           round(min_size_mm, 2), round(surface_deviation, 4),
           round(normal_deviation, 1), round(aspect_ratio, 1),
           round(outer_air_factor, 2), round(band_thickness_mm, 2),
           round(gap_layers, 1), int(n_sectors), round(stator_fillet_mm, 2),
           int(bool(pole_copy)), tuple(sorted(_comp_mesh.items())))
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
    polys = _simplify_polys(polys, tol_mm=surface_deviation,
                             stator_fillet_mm=stator_fillet_mm,
                             normal_dev_deg=normal_deviation,
                             band_mode="merged")
    # in_band / out_band now come straight from get_2d_polygons (full inner
    # air disk + outer air annulus), so the old air-gap-splitting motion
    # band is no longer needed for the sliding-band path.

    # Mesh density is driven by the Mesh-tab sliders (mesh_size, min_size,
    # gap_layers, normal_deviation) — the SAME values the transient solver now
    # uses (its old hard clamps were removed), so this is byte-for-byte the mesh
    # that computes T(t)/V(t)/losses.
    try:
        mesh_s, tags_s, classify_s, mesh_r, tags_r, classify_r = \
            _build_sliding_band_meshes(
                polys, rotor_angle_deg=rotor_angle_deg,
                mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                normal_deviation_deg=normal_deviation, aspect_ratio=aspect_ratio,
                outer_air_factor=outer_air_factor,
                band_thickness_mm=band_thickness_mm, gap_layers=gap_layers,
                n_sectors=(1 if _full_ring_view else n_sectors),
                geo_cfg=motor.parameters,
                component_mesh_mm=_comp_mesh,
                full_ring=_full_ring_view,
                pole_copy=bool(pole_copy),
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


@router.get("/physics/fem_field2d")
def get_fem_field2d(
    rotor_angle_deg:     float = 0.0,
    gamma_deg:           float = 0.0,
    mesh_size_mm:        float = 4.0,
    min_size_mm:         float = 0.3,
    outer_air_factor:    float = 1.3,
    motion_band:         bool  = True,    # accepted for URL compat (SB always bands)
    band_thickness_mm:   float = 0.4,
    n_sectors:           int   = 4,
    stator_fillet_mm:    float = 0.0,
    I_phase_rms:         Optional[float] = None,   # None = use config; 0 = zero-current
    component_mesh:      str   = "",      # JSON {comp: size_mm} per-part mesh size
    demag:               bool  = False,   # show the irreversible-demag %-map
    pole_copy:           bool  = False,   # bit-identical pole/slot template-copy mesh
):
    """Field view at ONE rotor angle, computed by the SLIDING-BAND TRANSIENT
    solver (1 step, rotor PHYSICALLY placed at rotor_angle_deg) — the SAME solver
    that produces the transient torque/losses.  There is no separate magnetostatic
    solver any more: the field picture is exactly the per-frame field the transient
    sweeps, so it is guaranteed consistent with the results.  Slower than the old
    static solve (it builds the sliding-band mesh); cached per (angle, γ, I, mesh).
    """
    import numpy as _np
    import time as _time

    _comp_mesh = _parse_component_mesh(component_mesh)
    key = (
        "sbfield", round(rotor_angle_deg * 2) / 2, round(gamma_deg, 1),
        round(mesh_size_mm, 2), round(min_size_mm, 2), round(outer_air_factor, 2),
        int(n_sectors), round(stator_fillet_mm, 2),
        round(I_phase_rms, 2) if I_phase_rms is not None else None,
        int(bool(demag)), int(bool(pole_copy)), tuple(sorted(_comp_mesh.items())),
    )
    if key in _fem_field_cache:
        return _fem_field_cache[key]

    try:
        from motor_ai_sim.simulation.fem_solver_2d import (
            fem_transient_sliding_band, _simplify_polys,
            DOM_MAG_BASE, DOM_COIL_BASE, DOM_MAG_N, DOM_MAG_S, DOM_COIL)
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
        from motor_ai_sim.config import get_config as _gc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FEM solver unavailable: {e}")

    if I_phase_rms is None:
        I_phase_rms = float(_gc().get("simulation", {}).get("max_current", 85.0))

    _t0 = _time.time()
    try:
        # 1 step (rotor pinned at the requested angle) → a true single-angle field
        # from the sliding-band machinery.  demag needs a short sweep for the
        # worst-case knee pre-pass, so use a few steps and snapshot the FIRST one.
        _nsteps = 8 if demag else 1
        d = fem_transient_sliding_band(
            n_steps_per_period=_nsteps, n_periods=1.0,
            gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
            mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
            outer_air_factor=float(outer_air_factor),
            n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
            stator_fillet_mm=float(stator_fillet_mm),
            eddy=False, rotor_eddy=False, demag=bool(demag),
            return_field=True, field_first=True,
            rotor_angle0_deg=float(rotor_angle_deg),
            pole_copy=bool(pole_copy),
            component_mesh_mm=_comp_mesh)
    except Exception as e:
        log.exception("SB field solve failed")
        raise HTTPException(status_code=500, detail=f"FEM solve failed: {e}")
    fld = d.get("field")
    if not fld:
        raise HTTPException(status_code=500, detail="SB field solve returned no snapshot")

    P  = _np.asarray(fld["P_mm"]) * 1e-3
    T  = _np.asarray(fld["T"])
    A  = _np.asarray(fld["A"])
    Bx = _np.asarray(fld["Bx"]); By = _np.asarray(fld["By"])
    Bmag = _np.sqrt(Bx ** 2 + By ** 2)
    Jtri = _np.asarray(fld.get("Jtri_src", _np.zeros(T.shape[1])))
    tags = _np.asarray(fld["tags"]).astype(int)

    # Collapse per-wire / per-magnet tags → renderer palette (rotor at angle).
    motor = CadQueryMotor()
    polys = _simplify_polys(
        motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle_deg)),
        tol_mm=0.005, stator_fillet_mm=float(stator_fillet_mm))
    tags_vis = tags.copy()
    tags_vis[tags >= DOM_COIL_BASE] = DOM_COIL
    for i, (mp, pol) in enumerate(polys.get("magnets", []) or []):
        tags_vis[tags == (DOM_MAG_BASE + i)] = (DOM_MAG_N if pol > 0 else DOM_MAG_S)

    nsec = int(n_sectors) if int(n_sectors) > 1 else 4
    result = {
        "ok": True,
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
        "solve_time_s": round(_time.time() - _t0, 1), "total_time_s": 0.0,
    }
    # Demag %-map + per-magnet knee report (only when demag modelling is on).
    _dc = d.get("demag_coef_per_tri")
    if _dc is not None and len(_dc) == int(T.shape[1]):
        result["demag_coef_per_tri"] = list(_dc)
    if d.get("demag_report"):
        result["demag_report"] = d["demag_report"]

    _fem_field_cache[key] = result
    return result


@router.get("/physics/fem_eddy_field2d")
def get_fem_eddy_field2d(
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
):
    """Run the time-coupled EDDY-CURRENT solve and return its LAST-frame field —
    A_z, |B|, and the copper eddy current density J = σ(−∂A/∂t + U_c) — in the
    SAME payload shape the magnetostatic field renderer consumes, so the existing
    Az / |B| / J views show the eddy-solve fields.  Slow (~25 s)."""
    import numpy as _np
    import math as _math
    _comp_mesh = _parse_component_mesh(component_mesh)
    key = ("eddyfld", round(gamma_deg, 1), round(I_phase_rms, 1),
           int(n_steps_per_period), round(n_periods, 2), round(mesh_size_mm, 2),
           round(min_size_mm, 2), round(outer_air_factor, 2), int(n_sectors),
           round(coil_temp_c, 1), tuple(sorted(_comp_mesh.items())))
    if key in _fem_field_cache:
        return _fem_field_cache[key]
    try:
        from motor_ai_sim.simulation.fem_solver_2d import (
            fem_transient_sliding_band, _simplify_polys,
            DOM_MAG_BASE, DOM_COIL_BASE, DOM_MAG_N, DOM_MAG_S, DOM_COIL)
        from motor_ai_sim.cadquery_geometry import CadQueryMotor
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"eddy solver unavailable: {e}")
    import time as _time
    _t0 = _time.time()
    try:
        d = fem_transient_sliding_band(
            n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
            gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
            mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
            outer_air_factor=float(outer_air_factor),
            n_sectors=int(n_sectors) if int(n_sectors) > 1 else 4,
            coil_temp_c=float(coil_temp_c), eddy=True, rotor_eddy=True,
            return_field=True,
            component_mesh_mm=_comp_mesh)
    except Exception as e:
        log.exception("eddy field solve failed")
        raise HTTPException(status_code=500, detail=f"eddy solve failed: {e}")
    _solve_s = round(_time.time() - _t0, 1)
    fld = d.get("field")
    if not fld:
        raise HTTPException(status_code=500, detail="eddy solve returned no field snapshot")

    P = _np.asarray(fld["P_mm"]) * 1e-3        # nodes → metres
    T = _np.asarray(fld["T"])                  # (3, ntri)
    A = _np.asarray(fld["A"])
    Bx = _np.asarray(fld["Bx"]); By = _np.asarray(fld["By"])
    Jn = _np.asarray(fld["Jeddy"])             # nodal Cu eddy/total current density
    tags = _np.asarray(fld["tags"]).astype(int)
    Bmag = _np.sqrt(Bx ** 2 + By ** 2)
    Jtri = Jn[T].mean(axis=0)                   # per-element J (nonzero in Cu)
    Ld = _np.asarray(fld.get("loss_dens") or [], float)   # per-element loss density [W/m³]

    # Collapse per-wire / per-magnet tags → renderer palette (rotor at angle 0).
    motor = CadQueryMotor()
    polys = _simplify_polys(motor.get_2d_polygons(rotor_angle_deg=0.0), tol_mm=0.005)
    tags_vis = tags.copy()
    tags_vis[tags >= DOM_COIL_BASE] = DOM_COIL
    for i, (mp, pol) in enumerate(polys.get("magnets", []) or []):
        tags_vis[tags == (DOM_MAG_BASE + i)] = (DOM_MAG_N if pol > 0 else DOM_MAG_S)

    def _mean(kk):
        s = d.get(kk) or [0.0]
        return float(_np.mean(_np.asarray(s, float))) if len(s) else 0.0
    nsec = int(n_sectors) if int(n_sectors) > 1 else 4
    Pcu = float(d.get("P_cu_total_solve_W", 0.0))    # eddy-solve copper (DC+AC)
    Pfe = _mean("P_fe_W"); Pmag = _mean("P_mag_eddy_W")
    Tavg = float(d.get("T_avg_Nm", 0.0)); rpm = float(d.get("rpm", 0.0))
    ploss = Pcu + Pfe + Pmag
    # Energy conservation: P_mech = P_elec_in − P_loss (P_elec_in = ⟨Σ v·i⟩ = 0
    # at no-load → P_mech = −P_loss, not the noisy cogging-mean torque × ω).
    Pelec = float(d.get("P_elec_in_W", 0.0))
    Pmech = Pelec - ploss
    Pairgap = Tavg * 2 * _math.pi * rpm / 60.0
    eff = (Pmech / Pelec) if (Pmech > 0 and Pelec > 1.0) else 0.0
    result = {
        "ok": True, "eddy": True,
        "n_vertices": int(P.shape[1]), "n_triangles": int(T.shape[1]),
        "vertices": P.T.tolist(), "triangles": T.T.tolist(),
        "domain_per_tri": tags_vis.tolist(),
        "A_z_per_node": A.tolist(),
        "Bmag_per_tri": Bmag.tolist(),
        "J_z_per_tri": Jtri.tolist(),           # eddy current density (A/m²) in Cu
        "loss_density_per_tri": Ld.tolist(),    # cycle-avg loss density [W/m³] per element
        "loss_dens_max": float(Ld.max()) if Ld.size else 0.0,
        "extent": [float(P[0].min()), float(P[0].max()),
                   float(P[1].min()), float(P[1].max())],
        "outlines": _outlines_from_polys(polys),
        "A_z_min": float(A.min()), "A_z_max": float(A.max()),
        "B_mag_max": float(Bmag.max()),
        "n_sectors": nsec, "symmetry_mult": nsec,
        "rpm": rpm, "freq_Hz": round(float(d.get("f_elec_Hz", 0.0)), 2),
        "T_em_Nm": round(Tavg, 3),
        "P_cu_W": round(Pcu, 1), "P_fe_W": round(Pfe, 1),
        "P_mag_eddy_W": round(Pmag, 1), "P_loss_total_W": round(ploss, 1),
        "P_mech_W": round(Pmech, 1), "efficiency": round(eff, 4),
        "P_cu_ac_solve_W": round(float(d.get("P_cu_ac_solve_W", 0.0)), 1),
        "V_peak": round(float(d.get("V_peak", 0.0)), 1),
        "solve_time_s": _solve_s,
        "total_time_s": _solve_s,
    }
    _fem_field_cache[key] = result
    return result


_daxis_sweep_cache: Dict[tuple, Dict] = {}


@router.get("/physics/daxis_sweep")
def get_daxis_sweep(
    lo:           float = -30.0,
    hi:           float = 30.0,
    step:         float = 2.0,
    I_phase_rms:  float = 120.0,
    rotor_angle_deg: float = 0.0,
    mesh_size_mm: float = 4.0,
    n_sectors:    int   = 4,
):
    """Sweep the load angle γ over [lo, hi] (step°) and return the magnetostatic
    torque at each — a 1-parameter optimisation of the d-axis angle.  Returns the
    full list of (angle, torque) plus the optimum (max-torque angle)."""
    import numpy as _np
    _ghash, _ = _current_geom_hash_and_params()
    key = ("daxis", _ghash, round(lo, 1), round(hi, 1), round(step, 2),
           round(I_phase_rms, 1), round(rotor_angle_deg, 1),
           round(mesh_size_mm, 2), int(n_sectors))
    if key in _daxis_sweep_cache:
        return _daxis_sweep_cache[key]
    try:
        from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FEM solver unavailable: {e}")

    angles = [round(float(a), 2) for a in
              _np.arange(float(lo), float(hi) + 1e-6, float(step))]
    pts: List[Dict] = []
    for g in angles:
        try:
            r = fem_solve_for_sim(
                rotor_angle_deg=float(rotor_angle_deg), gamma_deg=float(g),
                mesh_size_mm=float(mesh_size_mm), n_sectors=int(n_sectors),
                I_phase_rms=float(I_phase_rms))
            pts.append({"angle": g, "torque": round(float(r["T_em_Nm"]), 3)})
        except Exception as e:
            log.warning("daxis_sweep gamma=%s failed: %s", g, e)
            pts.append({"angle": g, "torque": None})

    valid = [p for p in pts if p["torque"] is not None]
    best = max(valid, key=lambda p: p["torque"]) if valid else {"angle": None, "torque": None}
    out = {
        "points": pts,
        "optimal_angle": best["angle"],
        "optimal_torque": best["torque"],
        "I_phase_rms": I_phase_rms, "lo": lo, "hi": hi, "step": step,
    }
    _daxis_sweep_cache[key] = out
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 7d. FEM Transient — N steps per electrical period
# ─────────────────────────────────────────────────────────────────────────────

_fem_transient_cache: Dict[tuple, Dict] = {}

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
        _fem_transient_cache[_retuple(blob["key"])] = blob["result"]
        log.info("restored last transient from %s", p)
    except Exception as _e:
        log.warning("could not restore last transient: %s", _e)


_load_last_transient_into_cache()   # repopulate the cache at import (startup)

# Per-FRAME cache keyed by (frame_param_key, k).  Lets a transient that
# was Stopped mid-way be resumed: a re-run with the same params reuses
# every frame already solved and only computes the missing ones.  The
# "Start fresh" path clears the entries for that param set first.
_fem_frame_cache: Dict[tuple, Dict] = {}

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
    Keys on these caches intentionally omit the geometry parameters (the
    geometry is treated as a global), so they must be flushed explicitly.
    """
    for _c in (_motor_geom_cache, _fem_mesh_cache, _fem_mesh_sb_cache,
               _fem_field_cache, _fem_transient_cache, _fem_frame_cache):
        try:
            _c.clear()
        except Exception:
            pass

# Scalar keys a non-keyframe step needs — returning only these from a
# worker process slashes the pickle payload (no mesh / A_z arrays).
_TRANSIENT_SCALAR_KEYS = (
    "T_em_Nm", "P_cu_W", "P_fe_W", "P_mag_eddy_W",
    "psi_A_Wb", "psi_B_Wb", "psi_C_Wb", "symmetry_mult", "n_sectors",
)


def _transient_frame_worker(task):
    """Picklable worker: solve ONE rotor angle in a SEPARATE process.

    Per-frame gmsh meshing dominates the cost (~11 s of ~15 s) and the
    in-process _GMSH_LOCK serialises gmsh calls within a process, so the
    only way to use the box's many cores is to fan the frames out across
    processes.  Each worker has its own gmsh state — no shared lock.

    task = (k, rot_deg, solve_kwargs, want_full)
    Returns (k, result_dict_or_None).  Non-keyframes are slimmed to the
    scalar series keys to keep the pickled payload tiny.
    """
    k, rot_deg, kw, want_full = task
    try:
        import logging as _lg
        _lg.disable(_lg.WARNING)
    except Exception:
        pass
    from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim
    # A magnet edge landing exactly on the sector cut makes gmsh reject the
    # mesh; a sub-FEM-resolution angular jitter dodges it without changing
    # the physics.
    for jitter in (0.0, +0.05, -0.05, +0.13, -0.13, +0.21, -0.21):
        try:
            r = fem_solve_for_sim(rotor_angle_deg=rot_deg + jitter, **kw)
            if want_full:
                return (k, r)
            return (k, {key: r.get(key) for key in _TRANSIENT_SCALAR_KEYS})
        except Exception:
            continue
    return (k, None)


# ── Persistent, pre-warmed worker pool ──────────────────────────────────
# On Windows every ProcessPoolExecutor worker is spawned (not forked) and
# pays the full ~5 s cold-import of gmsh + scikit-fem + motor_ai_sim before
# it can solve a single frame.  Re-creating the pool per request therefore
# burned ~5-10 s of pure startup on EVERY transient run.  Keep ONE pool
# alive for the process lifetime and warm each worker once via initializer,
# so the import cost is paid a single time and amortised across all runs.
import concurrent.futures as _cf  # noqa: E402  (kept local to this section)
import os as _os  # noqa: E402

_fem_pool: "Optional[_cf.ProcessPoolExecutor]" = None
_fem_pool_lock = _threading.Lock()

# numpy/OpenBLAS, gmsh/OpenMP and friends each default to spawning ONE thread
# PER LOGICAL CORE.  With a process pool that is catastrophic: N worker
# processes × 24 BLAS threads each = hundreds of threads fighting over the
# cores, so adding workers barely helped (and an unpinned 24-worker pool
# crashed outright).  Pin every numeric backend to a single thread so the
# process pool — not the libraries — owns the parallelism: N processes → N
# cores, clean linear scaling.
_THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)


def _pin_single_thread_env() -> None:
    """Force every numeric backend to 1 thread in THIS process's env.  Set
    in the parent right before the pool is created so spawned workers inherit
    it before they import numpy/gmsh (OpenBLAS reads the var at import time).
    The parent's own already-loaded BLAS is unaffected."""
    for k in _THREAD_ENV_KEYS:
        _os.environ.setdefault(k, "1")


def _fem_pool_workers() -> int:
    """Number of worker processes.

    The box reports 24 *logical* CPUs but only 12 *physical* cores (Hyper-
    Threading).  FEM meshing is CPU/cache/memory-bandwidth bound, so the
    hyper-threads add no real throughput — measured scaling peaks at the
    physical-core count and *regresses* past it (18-22 workers were slower
    than 12, and 24 unpinned crashed).  Default to the physical-core count
    (≈ logical/2).  Override with the FEM_POOL_WORKERS env var to experiment.
    """
    env = _os.environ.get("FEM_POOL_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    logical = _os.cpu_count() or 8
    # Assume SMT/Hyper-Threading on an even, >4-core box → physical ≈ logical/2.
    physical = logical // 2 if logical > 4 else logical
    return max(2, physical)


def _fem_worker_init():
    """Runs ONCE per worker when the pool starts a process.  Pins numeric
    threads (belt-and-suspenders alongside the inherited env) and forces the
    heavy imports up-front so the first real frame each worker handles no
    longer pays the ~5 s cold-import."""
    try:
        import os as _o
        for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                   "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
            _o.environ.setdefault(_k, "1")
    except Exception:
        pass
    try:
        import logging as _lg
        _lg.disable(_lg.WARNING)
    except Exception:
        pass
    try:
        # Touch the solver module so gmsh / skfem / numpy are imported and
        # cached inside this worker before any frame arrives.
        from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim  # noqa: F401
    except Exception:
        pass


def get_fem_pool() -> "_cf.ProcessPoolExecutor":
    """Lazily create (and reuse) the shared warm worker pool.  Rebuilds it
    if a previous pool was broken (e.g. a worker crashed)."""
    global _fem_pool
    with _fem_pool_lock:
        if _fem_pool is None:
            # Pin threads in the parent env BEFORE spawning so every worker
            # inherits single-thread numeric backends from process start.
            _pin_single_thread_env()
            _fem_pool = _cf.ProcessPoolExecutor(
                max_workers=_fem_pool_workers(),
                initializer=_fem_worker_init,
            )
        return _fem_pool


def _reset_fem_pool() -> None:
    """Drop the shared pool (next call to get_fem_pool rebuilds it).  Used
    after a BrokenProcessPool so one crashed worker doesn't wedge every
    subsequent transient run.

    HARD-KILLS the old workers.  On Windows the spawned workers routinely
    SURVIVE shutdown(wait=False), so every OOM-driven reset used to leak a
    whole pool's worth of processes — that is how 60+ zombie pythons piled up
    and starved the box into still more OOM crashes (the "process pool
    terminated abruptly" the user hit).  Grab the worker handles first, then
    terminate any that linger so a reset can never accumulate workers."""
    global _fem_pool
    with _fem_pool_lock:
        _p, _fem_pool = _fem_pool, None
    if _p is None:
        return
    _procs = list(getattr(_p, "_processes", {}).values())
    try:
        _p.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for _w in _procs:
        try:
            if _w.is_alive():
                _w.terminate()
        except Exception:
            pass


import atexit as _atexit


@_atexit.register
def _shutdown_fem_pool_atexit() -> None:
    """Tear the worker pool down on a graceful interpreter exit so workers
    don't outlive the server (best-effort; a SIGKILL still orphans them)."""
    try:
        _reset_fem_pool()
    except Exception:
        pass


def warm_fem_pool() -> None:
    """Kick the pool into existence and run a no-op on every worker so the
    cold-import is paid in the background at server startup, not on the
    user's first Run.  Safe to call repeatedly."""
    try:
        pool = get_fem_pool()
        for _ in range(_fem_pool_workers() * 2):
            pool.submit(_fem_worker_warmup)
    except Exception:
        pass


def _fem_worker_warmup(_=None):
    """Trivial task whose only purpose is to occupy a worker so its
    initializer (heavy imports) runs."""
    return True


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
    fresh:               bool  = False,   # ← "Start fresh" wipes the frame cache
    sliding_band:        bool  = False,   # ← mesh-once sliding band (smooth T, clean V)
    coil_temp_c:         float = 120.0,   # ← copper temperature → ρ_Cu(T)
    end_winding_factor:  float = 0.0,     # ← 0 = auto-estimate from geometry
    component_mesh:      str   = "",      # ← JSON {comp: size_mm} per-part mesh size
    rotor_eddy:          bool  = True,    # ← field-based magnet/shaft eddy losses
    demag:               bool  = False,   # ← per-element irreversible demagnetisation (de-rates Br → torque)
    torque_filter:       bool  = True,    # ← band-limit T(t) to physical 6·k orders (off = raw)
    pole_copy:           bool  = False,   # ← bit-identical pole/slot template-copy mesh
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
    import math as _math

    # ── Sliding-band path: mesh once + rotate rotor → smooth T(t), clean V(t).
    # Bypasses the parallel remesh-per-frame machinery entirely.
    if sliding_band:
        _comp_mesh = _parse_component_mesh(component_mesh)
        _sb_key = ("sb", int(n_steps_per_period), round(n_periods, 2),
                   round(gamma_deg, 1), round(I_phase_rms, 1),
                   round(mesh_size_mm, 2), round(min_size_mm, 2),
                   round(outer_air_factor, 2), int(n_sectors),
                   round(stator_fillet_mm, 2),
                   round(coil_temp_c, 1), round(end_winding_factor, 3),
                   int(bool(rotor_eddy)), round(gap_layers, 1),
                   int(bool(demag)), int(bool(torque_filter)),
                   int(bool(pole_copy)),
                   tuple(sorted(_comp_mesh.items())))
        if not fresh and _sb_key in _fem_transient_cache:
            return _fem_transient_cache[_sb_key]
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
                return _fem_transient_cache[_sb_key]
            # FAST sliding-band for EVERY symmetry (mesh once, slide the rotor).
            # Full (n_sectors=1) has no clean 360° slip mesh here, so it computes
            # the symmetry-EXACT sector (n=4) — which equals the full motor exactly
            # (×4), verified within 0.7% of the literal full disk.  Quasi-static
            # (genuine literal full disk) exists in fem_quasistatic_transient but is
            # ~10× slower (re-meshes per frame) — not used by default.
            from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
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
            def _sb_progress(_done, _total):
                _cur = _fem_transient_progress["current"]
                _cur["step"] = int(_done)
                _cur["total"] = int(_total)
            try:
                _sbres = fem_transient_sliding_band(
                    n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
                    gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
                    mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
                    outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
                    # 'Full' (n_sectors<=1) solves the full ring so it matches
                    # the full-motor mesh shown in the Mesh tab; ¼/½ solve the
                    # sector.  ¼ remains the default (set in the UI).
                    n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
                    stator_fillet_mm=float(stator_fillet_mm),
                    coil_temp_c=float(coil_temp_c),
                    end_winding_factor=float(end_winding_factor),
                    rotor_eddy=bool(rotor_eddy),
                    demag=bool(demag),
                    torque_filter=bool(torque_filter),
                    pole_copy=bool(pole_copy),
                    component_mesh_mm=_comp_mesh,
                    progress_cb=_sb_progress)
            finally:
                _fem_transient_progress["current"]["running"] = False
            # ── Summary block (masses, loss split, KV, efficiency, specific
            # torque/power) so the Simulation values table renders — same shape
            # as the remesh path produces.
            try:
                from motor_ai_sim.simulation.geometry_2d import params_from_config as _pfc
                from motor_ai_sim.config import get_config as _gc
                _p = _pfc(); _geo_cfg = _gc().get("geometry", {})
                _masses = _compute_masses(_p, _geo_cfg)
                _m_tot = float(_masses["total_active_kg"])
                _rpm = float(_sbres.get("rpm", 3950.0))
                _Tavg = float(_sbres.get("T_avg_Nm", 0.0))
                _Pmech = float(_sbres.get("P_mech_avg_W",
                                          _Tavg * 2 * _math.pi * _rpm / 60))
                # Period-MEAN of each instantaneous loss series — NOT [0].
                # The iron/magnet series ripple as the teeth pass (mag eddy
                # swings ~88%); frame 0 sits near the peak, so picking [0]
                # would overstate the reported average loss.  Copper is DC
                # (flat) so its mean == [0] anyway.
                def _mean(_k):
                    _s = _sbres.get(_k) or [0.0]
                    return float(_np.mean(_np.asarray(_s, float))) if len(_s) else 0.0
                _Pcu = _mean("P_cu_W")
                _Pfe = _mean("P_fe_W")
                _Pmag = _mean("P_mag_eddy_W")
                _Pshaft = _mean("P_shaft_eddy_W")   # solid-shaft eddy (bulk conductor)
                _Vpk = float(_sbres.get("V_peak", 0.0))
                _Vrms = _Vpk / _math.sqrt(2)
                _Vlpk = _Vpk * _math.sqrt(3); _Vlrms = _Vlpk / _math.sqrt(2)
                # Total INCLUDES shaft eddy so the breakdown sums to the same loss
                # the solver's energy-balanced P_mech subtracts (else the card's
                # Mech-power ≠ Σ(displayed losses) by the hidden shaft term).
                _ploss = _Pcu + _Pfe + _Pmag + _Pshaft
                # Energy conservation: P_mech = P_elec_in − P_loss (the solver now
                # computes it this way, so at no-load P_mech = −P_loss exactly).
                # Efficiency = shaft out / electrical in; 0 when not motoring.
                _Pelec = float(_sbres.get("P_elec_in_W", _Pmech + _ploss))
                _eff = (_Pmech / _Pelec) if (_Pmech > 0 and _Pelec > 1.0) else 0.0
                _sbres["summary"] = {
                    "rpm": _rpm,
                    "I_phase_rms_A": round(float(I_phase_rms), 2),
                    "gamma_deg": round(float(gamma_deg), 2),
                    "T_em_avg_Nm": round(_Tavg, 3),
                    "T_ripple_pct": round(float(_sbres.get("T_ripple_pct", 0.0)), 1),
                    "T_ripple_raw_pct": round(float(_sbres.get("T_ripple_raw_pct", 0.0)), 1),
                    "T_ripple_filt_pct": round(float(_sbres.get("T_ripple_filt_pct", 0.0)), 1),
                    "P_mech_W": round(_Pmech, 1),
                    "V_phase_peak_V": round(_Vpk, 1),
                    "V_phase_rms_V": round(_Vrms, 1),
                    "V_line_peak_V": round(_Vlpk, 1),
                    "V_line_rms_V": round(_Vlrms, 1),
                    "KV_rpm_per_V_phase": round(_rpm / _Vrms, 2) if _Vrms > 1 else 0.0,
                    "KV_rpm_per_V_line":  round(_rpm / _Vlrms, 2) if _Vlrms > 1 else 0.0,
                    "P_loss_total_W": round(_ploss, 1),
                    "P_core_W":     round(_Pfe, 1),            # laminated iron
                    "P_stranded_W": round(_Pcu, 1),            # copper
                    "P_solid_W":    round(_Pmag + _Pshaft, 1), # magnet + shaft eddy
                    "efficiency":   round(_eff, 4),
                    "coil_temp_C":  round(float(_sbres.get("coil_temp_C", coil_temp_c)), 1),
                    "end_winding_factor": round(float(_sbres.get("end_winding_factor", 0.0)), 2),
                    "mass_total_kg": round(_m_tot, 3),
                    "mass_components": _masses["components"],
                    "torque_per_mass_Nm_kg": round(_Tavg / max(_m_tot, 1e-6), 3),
                    "power_per_mass_W_kg":   round(_Pmech / max(_m_tot, 1e-6), 1),
                    "loss_density_W_kg":     round(_ploss / max(_m_tot, 1e-6), 1),
                }
            except Exception as _se:
                log.warning("SB summary build failed: %s", _se)
            _fem_transient_cache[_sb_key] = _sbres
            _save_last_transient(_sb_key, _sbres)   # survive a back-end restart
            return _sbres
        except HTTPException:
            raise
        except Exception as _e:
            log.exception("sliding-band transient failed")
            raise HTTPException(status_code=500,
                                detail=f"sliding-band transient failed: {_e}")
        finally:
            _fem_transient_lock.release()

    key = (
        int(n_steps_per_period), round(n_periods, 2),
        round(gamma_deg, 1), round(I_phase_rms, 1),
        round(mesh_size_mm, 2), round(min_size_mm, 2),
        round(outer_air_factor, 2), bool(motion_band),
        round(band_thickness_mm, 2), int(n_sectors),
        round(stator_fillet_mm, 2),
        bool(include_frames), int(n_frames), bool(torque_filter),
    )

    # Per-frame cache key (omits include_frames/n_frames — a single frame's
    # physics is identical whichever caller asked for it).  Used to resume
    # a Stopped run.
    _frame_pkey = (
        int(n_steps_per_period), round(n_periods, 2),
        round(gamma_deg, 1), round(I_phase_rms, 1),
        round(mesh_size_mm, 2), round(min_size_mm, 2),
        round(outer_air_factor, 2), bool(motion_band),
        round(band_thickness_mm, 2), int(n_sectors),
        round(stator_fillet_mm, 2),
    )
    if fresh:
        # "Start fresh" → drop the cached full run AND every cached frame
        # for this param set, so the whole period is recomputed.
        _fem_transient_cache.pop(key, None)
        for _ck in [c for c in _fem_frame_cache if c[0] == _frame_pkey]:
            _fem_frame_cache.pop(_ck, None)

    if key in _fem_transient_cache:
        return _fem_transient_cache[key]

    try:
        from motor_ai_sim.simulation.fem_solver_2d import fem_solve_for_sim
        from motor_ai_sim.simulation.geometry_2d import params_from_config
        from motor_ai_sim.config import get_config
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"FEM unavailable: {e}")

    p_sim = params_from_config()
    cfg   = get_config()
    rpm   = cfg.get("simulation", {}).get("rpm", 3950)
    wind  = cfg.get("winding", {})
    R_phase = float(wind.get("phase_resistance_ohm", 0.018))
    pole_pairs = p_sim.num_poles // 2
    f_elec  = rpm / 60 * pole_pairs            # Hz
    T_elec  = 1.0 / max(f_elec, 1e-9)
    n_total = max(2, int(round(n_steps_per_period * n_periods)))
    dt      = T_elec / n_steps_per_period
    omega_m = 2 * _math.pi * rpm / 60          # mech angular vel
    rad_per_step = omega_m * dt                # mech rad per step

    # Time series
    t_series      = _np.linspace(0.0, dt * (n_total - 1), n_total)
    rotor_deg     = _np.degrees(rad_per_step) * _np.arange(n_total)
    # Optional: wrap rotor angle to electrical period
    rotor_deg = rotor_deg % (360.0 / pole_pairs)

    T_em_series   : List[float] = []
    P_cu_series   : List[float] = []
    P_fe_series   : List[float] = []
    P_eddy_series : List[float] = []
    psi_A         : List[float] = []
    psi_B         : List[float] = []
    psi_C         : List[float] = []
    I_A_series    : List[float] = []
    I_B_series    : List[float] = []
    I_C_series    : List[float] = []

    # When the user requested an animation, we keep N keyframes spread
    # evenly across the simulated window — index of each frame in the
    # full step sequence.  The first call also captures the full FEM
    # payload (mesh, A_z, |B|, demag) so the field-animation viewer can
    # scrub through the rotor positions client-side.
    frames: List[Dict] = []
    if include_frames and n_frames > 0:
        n_frames = max(2, min(int(n_frames), n_total))
        frame_idxs = set(int(round(i * (n_total - 1) / (n_frames - 1)))
                          for i in range(n_frames))
    else:
        frame_idxs = set()

    # Shapely → outline list helper (same as /fem_field2d).  Only needed
    # for the FIRST animation frame; rotor outlines reference each frame's
    # mesh fill so we don't need a per-frame outline transform.
    def _polys_to_outlines(pfo):
        from shapely.geometry import MultiPolygon as _SMP
        def _conv(poly):
            if poly is None or poly.is_empty: return []
            geoms = list(poly.geoms) if isinstance(poly, _SMP) else [poly]
            rings = []
            for g in geoms:
                if g.is_empty or g.area < 1e-6: continue
                rings.append([[x * 1e-3, y * 1e-3] for x, y in g.exterior.coords])
                for h in g.interiors:
                    rings.append([[x * 1e-3, y * 1e-3] for x, y in h.coords])
            return rings
        out = []
        for k, dom in (("stator", 1), ("rotor", 5), ("shaft", 6),
                        ("air_gap", 3), ("airgap_band", 7), ("air_outer", 8)):
            if pfo.get(k) is not None:
                out.append({"domain": dom, "loops": _conv(pfo[k])})
        for mag_poly, polarity in pfo.get("magnets", []) or []:
            out.append({"domain": 4 if polarity > 0 else 44,
                        "loops": _conv(mag_poly)})
        for coil_poly in pfo.get("coils", []) or []:
            out.append({"domain": 2, "loops": _conv(coil_poly)})
        return out

    import time as _time
    import os as _os
    import concurrent.futures as _cf

    # Serialize transient computes: the animation viewer and the transient
    # charts both hit this endpoint on mount with identical params, so
    # without a guard they would each spawn a worker pool and double the
    # CPU storm.  Acquire BEFORE touching the shared progress state so the
    # waiting caller doesn't clobber the running one's progress; once it
    # wakes it falls straight through to the freshly-populated cache.
    def _cancelled() -> bool:
        return bool(run_id) and _fem_transient_cancelled_run.get("id") == run_id

    _fem_transient_lock.acquire()
    if key in _fem_transient_cache:
        _fem_transient_lock.release()
        return _fem_transient_cache[key]
    # A duplicate request (animation + transient share a run_id) that was
    # waiting on the lock while its twin got cancelled must NOT start a
    # fresh solve — bail immediately.
    if _cancelled():
        _fem_transient_lock.release()
        raise HTTPException(status_code=499, detail="FEM transient cancelled by user")

    _t_loop_start = _time.time()
    _fem_transient_progress["current"] = {
        "running":   True,
        "step":      0,
        "total":     n_total,
        "elapsed_s": 0.0,
        "eta_s":     0.0,
        "ts_start":  _t_loop_start,
        "phase":     "fem-solve",
    }

    # ── Parallel frame solve across processes ───────────────────────────
    # Each rotor angle is independent and per-frame gmsh meshing dominates
    # (~11 s of ~15 s); fan the frames out across processes so the box's
    # many cores actually get used (the in-process _GMSH_LOCK can't).
    _solve_kw = dict(
        gamma_deg=gamma_deg, mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, motion_band=motion_band,
        band_thickness_mm=band_thickness_mm, n_sectors=int(n_sectors),
        stator_fillet_mm=stator_fillet_mm, I_phase_rms=I_phase_rms,
    )
    results: List[Optional[Dict]] = [None] * n_total
    # Resume support: fill in any frames already cached from a prior
    # (Stopped) run, and only submit the missing ones.
    _todo = []
    for k in range(n_total):
        cached = _fem_frame_cache.get((_frame_pkey, k))
        if cached is not None:
            results[k] = cached
        else:
            _todo.append((k, float(rotor_deg[k]), _solve_kw, (k in frame_idxs)))
    _n_cached = n_total - len(_todo)
    # Cap workers WELL below the core count.  Each worker is a full gmsh
    # process that pegs a core; using all cores starved the uvicorn event
    # loop so /progress polling timed out and the browser showed
    # "Failed to fetch" / a frozen UI.  ~1/4 of the cores keeps the box
    # responsive even if the animation + transient solves overlap.
    _was_cancelled = False
    try:
        _done = _n_cached
        _fem_transient_progress["current"]["step"] = _done
        if _todo:
            # Reuse the PERSISTENT warm pool (workers already imported gmsh +
            # skfem once via the initializer) instead of paying ~5 s of
            # cold-import per worker on every run.
            try:
                _ex = get_fem_pool()
                _fut_to_task = {
                    _ex.submit(_transient_frame_worker, t): t for t in _todo
                }
            except _cf.process.BrokenProcessPool:
                # A worker died — rebuild the pool and retry once.
                _reset_fem_pool()
                _ex = get_fem_pool()
                _fut_to_task = {
                    _ex.submit(_transient_frame_worker, t): t for t in _todo
                }
            _futs = list(_fut_to_task.keys())
            try:
                for _fut in _cf.as_completed(_futs):
                    _kk, _rr = _fut.result()
                    results[_kk] = _rr
                    # Stash the completed frame so a later resume reuses it.
                    if _rr is not None:
                        _fem_frame_cache[(_frame_pkey, _kk)] = _rr
                    _done += 1
                    _el = _time.time() - _t_loop_start
                    _solved = max(1, _done - _n_cached)
                    _fem_transient_progress["current"]["step"] = _done
                    _fem_transient_progress["current"]["elapsed_s"] = _el
                    _fem_transient_progress["current"]["per_step_s"] = _el / _solved
                    _fem_transient_progress["current"]["eta_s"] = \
                        (_el / _solved) * (n_total - _done)
                    # Cooperative cancel: the Stop button flagged THIS run.
                    # The pool is SHARED across runs, so don't shut it down —
                    # just cancel the queued (not-yet-started) frames and stop
                    # collecting.  Already-running workers finish their current
                    # frame and return to the pool idle for the next run.
                    if _cancelled():
                        _was_cancelled = True
                        for _pf in _futs:
                            _pf.cancel()
                        break
            except _cf.process.BrokenProcessPool as _bpp:
                _reset_fem_pool()
                raise _bpp
    except Exception as e:
        log.exception("parallel FEM transient failed")
        _fem_transient_lock.release()
        raise HTTPException(status_code=500, detail=f"FEM transient failed: {e}")

    if _was_cancelled:
        _fem_transient_progress["current"] = {
            **_fem_transient_progress["current"],
            "running": False, "phase": "cancelled",
        }
        _fem_transient_lock.release()
        raise HTTPException(status_code=499, detail="FEM transient cancelled by user")

    # Backfill any frame whose every jittered solve failed with the nearest
    # successful neighbour so the series stays continuous.
    _first_good = next((r for r in results if r is not None), None)
    if _first_good is None:
        _fem_transient_lock.release()
        raise HTTPException(status_code=500,
                            detail="FEM transient: every frame failed to mesh/solve")
    _lastg = _first_good
    for _i in range(n_total):
        if results[_i] is None:
            results[_i] = _lastg
        else:
            _lastg = results[_i]

    try:
        for k in range(n_total):
            rot_deg = float(rotor_deg[k])
            r = results[k]
            T_em_series.append(float(r.get("T_em_Nm", 0.0)))
            P_cu_series.append(float(r.get("P_cu_W", 0.0)))
            P_fe_series.append(float(r.get("P_fe_W", 0.0)))
            P_eddy_series.append(float(r.get("P_mag_eddy_W", 0.0)))
            # ── Capture full field for the animation keyframes ───────────
            # Guard against a backfilled (slim, scalar-only) result landing
            # on a keyframe index — skip the frame rather than KeyError.
            if k in frame_idxs and r.get("vertices") is not None:
                # Strip the bulky polys_for_outlines field on all but the
                # FIRST frame: stator outlines are identical across the
                # period and the rotor outline rotates with rotor_angle_deg,
                # which the viewer applies as a transform.
                frame = {
                    "step_idx":         k,
                    "time_s":           float(t_series[k]),
                    "rotor_angle_deg":  rot_deg,
                    "T_em_Nm":          float(r.get("T_em_Nm", 0.0)),
                    "vertices":         r["vertices"],
                    "triangles":        r["triangles"],
                    "domain_per_tri":   r["domain_per_tri"],
                    "A_z_per_node":     r["A_z_per_node"],
                    "Bmag_per_tri":     r["Bmag_per_tri"],
                    "J_z_per_tri":      r.get("J_z_per_tri", []),
                    "demag_coef_per_tri": r.get("demag_coef_per_tri", []),
                    "extent":           r["extent"],
                    "n_vertices":       r["n_vertices"],
                    "n_triangles":      r["n_triangles"],
                    "A_z_min":          float(min(r["A_z_per_node"]) if r["A_z_per_node"] else 0),
                    "A_z_max":          float(max(r["A_z_per_node"]) if r["A_z_per_node"] else 0),
                    "B_mag_max":        float(max(r["Bmag_per_tri"]) if r["Bmag_per_tri"] else 0),
                }
                # Outlines per frame — splits into "static" (stator/coils
                # baked once on frame[0]) and "rotor-attached" (magnets +
                # rotor body, sent every frame at their actual rotated
                # positions so the overlay aligns with the mesh fill).
                pfo = r.get("polys_for_outlines") or {}
                if not frames:
                    # Full first-frame outlines + sym multiplier
                    frame["outlines"]       = _polys_to_outlines(pfo)
                    frame["symmetry_mult"]  = r.get("symmetry_mult", 1)
                    frame["n_sectors"]      = r.get("n_sectors", 1)
                else:
                    # Only rotor-attached outlines on later frames — the
                    # static stator / coil outlines are reused from
                    # frame[0] in the client.
                    rotor_pfo = {
                        "rotor":   pfo.get("rotor"),
                        "magnets": pfo.get("magnets", []),
                    }
                    frame["outlines_rotor"] = _polys_to_outlines(rotor_pfo)
                frames.append(frame)
            # Reconstruct currents at this time-step (same convention as solver)
            theta_e = (rot_deg * pole_pairs + gamma_deg + 270.0) * _math.pi/180
            I_peak = I_phase_rms / float(wind.get("n_parallel", 2)) * _math.sqrt(2)
            iA = I_peak * _math.cos(theta_e)
            iB = I_peak * _math.cos(theta_e - 2*_math.pi/3)
            iC = I_peak * _math.cos(theta_e + 2*_math.pi/3)
            I_A_series.append(iA); I_B_series.append(iB); I_C_series.append(iC)
            # Use the most asymmetric ψ from FEM as the canonical phase
            # waveform.  Sector-mode (1/4 motor + anti-periodic BC) makes
            # the per-phase slot distribution unbalanced — phase A has
            # 2 slots with the SAME sign while B/C have 2 slots with
            # opposite signs.  This produces an asymmetric set of FEM
            # ψ values that don't translate to a balanced 3-phase
            # back-EMF.
            #
            # Workaround: take the largest-amplitude phase from FEM as
            # the canonical ψ(t) shape; ψ for the other two phases is
            # generated by ±120° elec phase-shifting in time.
            psi_A.append(float(r.get("psi_A_Wb", 0.0)))
            psi_B.append(float(r.get("psi_B_Wb", 0.0)))
            psi_C.append(float(r.get("psi_C_Wb", 0.0)))
        # placeholder — will be overwritten below after the loop
    except Exception as e:
        log.exception("FEM transient failed")
        _fem_transient_lock.release()
        raise HTTPException(status_code=500, detail=f"FEM transient failed: {e}")

    # ── Balanced 3-phase ψ via ±120° elec time shift ───────────────────
    # With the corrected half-pitch slot_idx mapping the FEM-derived ψ for
    # each phase should be intrinsically balanced (same amplitude, 120°
    # apart).  In 1/4-sector mode some asymmetry can remain because the
    # six-slot sector doesn't divide cleanly across 3 phases, so we still
    # PICK the largest-amplitude phase as the canonical reference and
    # generate the other two by time-shifting.  We also DC-balance so the
    # back-EMF (= dψ/dt) integrates around zero.
    def _ptp(arr): a = _np.asarray(arr); return float(a.max() - a.min())
    psi_arr = [_np.asarray(psi_A), _np.asarray(psi_B), _np.asarray(psi_C)]
    canon_idx = max(range(3), key=lambda i: _ptp(psi_arr[i]))
    psi_canon = psi_arr[canon_idx]
    # Remove DC offset so the back-EMF integrates around zero.
    psi_canon = psi_canon - float(_np.mean(psi_canon))
    n_per = max(1, int(n_steps_per_period))
    shift_B = -n_per // 3                  # -120° elec
    shift_C = +n_per // 3                  # +120° elec
    psi_A_arr = _np.roll(psi_canon,           0)
    psi_B_arr = _np.roll(psi_canon, shift_B)
    psi_C_arr = _np.roll(psi_canon, shift_C)
    psi_A = psi_A_arr.tolist()
    psi_B = psi_B_arr.tolist()
    psi_C = psi_C_arr.tolist()

    # ── V_phase(t) = R·I + dψ/dt (central differences) ───────────────────
    def _ddt(arr: List[float]) -> List[float]:
        a = _np.asarray(arr)
        d = _np.zeros_like(a)
        d[1:-1] = (a[2:] - a[:-2]) / (2 * dt)
        d[0]    = (a[1]  - a[0])   / dt
        d[-1]   = (a[-1] - a[-2])  / dt
        return d.tolist()

    dpsiA_dt = _ddt(psi_A)
    dpsiB_dt = _ddt(psi_B)
    dpsiC_dt = _ddt(psi_C)
    V_A = [R_phase * iA + e for iA, e in zip(I_A_series, dpsiA_dt)]
    V_B = [R_phase * iB + e for iB, e in zip(I_B_series, dpsiB_dt)]
    V_C = [R_phase * iC + e for iC, e in zip(I_C_series, dpsiC_dt)]

    # ── Honest magnet eddy loss via J = -σ · ∂A_z/∂t ──────────────────────
    # Each frame's mean A_z over a magnet's cells is the rotor-frame A_z of
    # that magnet at time t_k (because the FEM rebuilds the mesh with the
    # magnet at its rotated lab position, so the same physical magnet's
    # cells appear at each frame's rotated coordinates).  We central-
    # difference per magnet, square, multiply by σ × magnet-volume, then
    # sum across magnets × n_sectors to get the full-motor P_mag_eddy(t).
    # (Magnet eddy comes per-frame from the solver — slot-ripple slab
    # model on local B with η = 0.03, calibrated for FSCW SPMSM.  See
    # fem_solver_2d.fem_solve_for_sim for the physics + sliding-band
    # limitation that prevents a fully ab-initio calc in the current
    # rebuild-per-frame pipeline.)

    P_total_series = [c + f + e for c, f, e
                       in zip(P_cu_series, P_fe_series, P_eddy_series)]

    # ── Band-limit the torque to the physical 6·k electrical orders ───────────
    # The remesh-per-frame pipeline meshes every rotor angle INDEPENDENTLY, so
    # frame-to-frame discretisation differences inject broadband torque ripple at
    # orders a balanced 3-phase drive cannot produce.  Reconstruct T(t) from its
    # 6·k content (mean preserved) — same denoising as the sliding-band path.
    from motor_ai_sim.simulation.fem_solver_2d import band_limit_torque as _blt
    if torque_filter:
        T_em_series, _Trip_phys, _Trip_raw = _blt(
            T_em_series, n_steps_per_period, n_periods)
    else:
        _arrT = _np.asarray(T_em_series, float)
        _aT = float(_arrT.mean()) if _arrT.size else 0.0
        _Trip_phys = _Trip_raw = (
            100.0 * (float(_arrT.max()) - float(_arrT.min())) / abs(_aT)
            if _arrT.size and abs(_aT) > 1e-9 else 0.0)

    # ── Energy-conserving shaft power (same convention as the sliding-band path)
    # P_elec_in = ⟨Σ v·i⟩ (0 at no-load), and P_mech = P_elec_in − P_loss so that
    # at no-load the shaft power equals −P_loss (drive overcomes the losses) —
    # NOT the numerically-noisy cogging-mean torque × ω.
    _omega_m = 2 * _math.pi * rpm / 60
    _Pelec_in = (float(_np.mean(
        _np.asarray(V_A) * _np.asarray(I_A_series)
        + _np.asarray(V_B) * _np.asarray(I_B_series)
        + _np.asarray(V_C) * _np.asarray(I_C_series))) if I_A_series else 0.0)
    _Ploss_avg = float(_np.mean(P_total_series)) if P_total_series else 0.0
    _Pmech_shaft = _Pelec_in - _Ploss_avg
    _Pairgap = float(_np.mean(T_em_series)) * _omega_m

    payload = {
        "n_steps":            n_total,
        "n_steps_per_period": int(n_steps_per_period),
        "n_periods":          float(n_periods),
        "dt_s":               dt,
        "T_period_s":         T_elec,
        "f_elec_Hz":          f_elec,
        "rpm":                rpm,
        "rotor_angle_deg":    rotor_deg.tolist(),
        "time_s":             t_series.tolist(),
        "T_em_Nm":            T_em_series,
        "T_avg_Nm":           float(_np.mean(T_em_series)),
        "T_ripple_pct":       round(_Trip_phys, 2),
        "T_ripple_raw_pct":   round(_Trip_raw, 2),
        "P_cu_W":             P_cu_series,
        "P_fe_W":             P_fe_series,
        "P_mag_eddy_W":       P_eddy_series,
        "P_loss_total_W":     P_total_series,
        "P_mech_avg_W":       _Pmech_shaft,        # energy-conserving shaft power
        "P_elec_in_W":        _Pelec_in,           # ⟨Σ v·i⟩ (0 at no-load)
        "P_airgap_W":         _Pairgap,            # electromagnetic T_avg·ω
        "I_A":                I_A_series,
        "I_B":                I_B_series,
        "I_C":                I_C_series,
        "V_A":                V_A,
        "V_B":                V_B,
        "V_C":                V_C,
        "V_peak":             float(max(max(map(abs, V_A)),
                                         max(map(abs, V_B)),
                                         max(map(abs, V_C)))),
        "psi_A_Wb":           psi_A,
        "psi_B_Wb":           psi_B,
        "psi_C_Wb":           psi_C,
        "R_phase_ohm":        R_phase,
    }

    # ── Summary block: masses, loss breakdown, KV, specific torque/power ──
    geo_cfg = cfg.get("geometry", {})
    masses  = _compute_masses(p_sim, geo_cfg)
    m_total = masses["total_active_kg"]

    P_cu_avg  = float(_np.mean(P_cu_series))
    P_fe_avg  = float(_np.mean(P_fe_series))
    P_mag_avg = float(_np.mean(P_eddy_series))
    T_avg     = float(_np.mean(T_em_series))
    P_mech    = _Pmech_shaft          # energy-conserving (P_elec_in − P_loss)

    V_peak = float(max(max(map(abs, V_A)),
                         max(map(abs, V_B)),
                         max(map(abs, V_C))))
    V_phase_rms_estim = V_peak / _math.sqrt(2)
    V_line_peak       = V_peak * _math.sqrt(3)
    V_line_rms        = V_line_peak / _math.sqrt(2)
    # KV constant — motor velocity constant in rpm/V.  Use V_phase_RMS as
    # the standard convention (matches ω·ψ_pm/√2 at no-load).  Guard
    # against zero voltage (e.g. open-circuit run with I=0).
    KV_rpm_per_V_phase = (rpm / V_phase_rms_estim) if V_phase_rms_estim > 1.0 else 0.0
    KV_rpm_per_V_line  = (rpm / V_line_rms)        if V_line_rms       > 1.0 else 0.0

    P_loss_avg = P_cu_avg + P_fe_avg + P_mag_avg
    # Efficiency = shaft out / electrical in (motoring only); 0 at no-load where
    # P_elec_in = 0 and the shaft merely absorbs the losses (P_mech = −P_loss).
    eff_avg    = (P_mech / _Pelec_in) if (P_mech > 0 and _Pelec_in > 1.0) else 0.0

    payload["summary"] = {
        # Operating point
        "rpm":              rpm,
        "I_phase_rms_A":    round(float(I_phase_rms), 2),
        "gamma_deg":        round(float(gamma_deg), 2),
        # Mechanics
        "T_em_avg_Nm":      round(T_avg, 3),
        "T_ripple_pct":     round(_Trip_phys, 1),
        "T_ripple_raw_pct": round(_Trip_raw, 1),
        "P_mech_W":         round(P_mech, 1),
        # Voltage / current
        "V_phase_peak_V":   round(V_peak, 1),
        "V_phase_rms_V":    round(V_phase_rms_estim, 1),
        "V_line_peak_V":    round(V_line_peak, 1),
        "V_line_rms_V":     round(V_line_rms, 1),
        "KV_rpm_per_V_phase": round(KV_rpm_per_V_phase, 2),
        "KV_rpm_per_V_line":  round(KV_rpm_per_V_line, 2),
        # Losses split by physical loss family
        # (core = laminated iron, stranded = wound copper, solid = bulk
        # conductors like magnets and shaft eddies)
        "P_loss_total_W":     round(P_loss_avg, 1),
        "P_core_W":           round(P_fe_avg, 1),       # lamination
        "P_stranded_W":       round(P_cu_avg, 1),       # copper
        "P_solid_W":          round(P_mag_avg, 1),      # magnets + shaft
        "efficiency":         round(eff_avg, 4),
        # Mass + specific performance
        "mass_total_kg":      round(m_total, 3),
        "mass_components":    masses["components"],
        "torque_per_mass_Nm_kg":  round(T_avg / max(m_total, 1e-6), 3),
        "power_per_mass_W_kg":    round(P_mech / max(m_total, 1e-6), 1),
        "loss_density_W_kg":      round(P_loss_avg / max(m_total, 1e-6), 1),
    }

    if include_frames:
        payload["frames"] = frames
        payload["n_frames_returned"] = len(frames)
    _fem_transient_cache[key] = payload
    # Mark the run finished so the progress endpoint stops reporting
    # "running".  Keep the step count so the final state is visible
    # for one more poll on the frontend.
    _fem_transient_progress["current"] = {
        **_fem_transient_progress["current"],
        "running": False,
        "phase":   "done",
        "step":    n_total,
        "total":   n_total,
        "elapsed_s": round(_time.time() - _t_loop_start, 1),
        "eta_s":   0.0,
    }
    _fem_transient_lock.release()
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Torque sweep  —  Maxwell stress tensor on air-gap circle
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/physics/torque_sweep")
def get_torque_sweep(
    gamma_deg:  float = 0.0,   # 0° = pure q-axis (max torque)
    n_rotor:    int   = 60,   # rotor positions per electrical period
    n_ag:       int   = 720,  # points on air-gap integration circle
):
    """Compute T(θ) over one electrical period using Maxwell stress tensor.

    T = (L/μ₀) · r_ag² · ∮ B_r(φ) · B_φ(φ) dφ

    A_z is computed analytically via Green's function superposition
    (same as field2d endpoint).  B_r and B_φ are derived from the
    polar gradient of A_z on the mid-air-gap circle.

    This gives:
      - Average torque  T_avg  (electromagnetic)
      - Cogging ripple  ΔT     (from slot/pole interaction)
      - T(θ) waveform for plotting

    Note: analytic fields ignore steel saturation → torque magnitudes
    are approximate.  PINN gives exact T from nonlinear A_z.
    """
    import math, numpy as np
    from motor_ai_sim.simulation.geometry_2d import (
        MotorDomains2D, params_from_config,
    )
    from motor_ai_sim.config import get_config

    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    mu0         = 4e-7 * math.pi
    pole_pairs  = p.num_poles // 2
    I_phase_rms = sim.get("max_current", 85.0)
    n_parallel  = wind.get("n_parallel", 2)
    n_wires     = geo.get("num_wires_per_slot", 14)
    Br          = 1.19

    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    slot_pitch   = 2 * math.pi / p.num_slots
    r_slot_mid   = p.r_stator_in + p.slot_height_m * 0.5
    r_ag         = (p.r_air_out + p.r_air_in) / 2   # mid air-gap
    dr           = (p.r_air_out - p.r_air_in) * 0.4  # small radial step

    # PM discretisation (tangential magnetization — sources on top/bottom radial faces)
    N_a        = 40
    pole_pitch = 2 * math.pi / p.num_poles
    fill_up_g  = geo.get("magnet_fill_up",   0.44)
    fill_down_g = geo.get("magnet_fill_down", 0.9)
    rotor_hh_m  = geo.get("rotor_house_height", 0.0) * 1e-3
    mag_up_gap_m = geo.get("magnet_up_gap", 0.0) * 1e-3
    r_top_pm    = p.r_rotor_out - mag_up_gap_m
    r_bot_pm    = p.r_rotor_in  + rotor_hh_m
    half_top_pm = fill_up_g   * pole_pitch / 2
    half_bot_pm = fill_down_g * pole_pitch / 2
    da_top_pm   = 2 * half_top_pm / N_a
    da_bot_pm   = 2 * half_bot_pm / N_a
    off_top_pm  = np.linspace(-half_top_pm + da_top_pm/2, half_top_pm - da_top_pm/2, N_a)
    off_bot_pm  = np.linspace(-half_bot_pm + da_bot_pm/2, half_bot_pm - da_bot_pm/2, N_a)
    I_top_unit_pm = -Br / mu0 * r_top_pm * da_top_pm    # × polarity per magnet
    I_bot_unit_pm = +Br / mu0 * r_bot_pm * da_bot_pm

    # Air-gap circle sample points
    phi_ag   = np.linspace(0, 2*math.pi, n_ag, endpoint=False)
    xc       = r_ag * np.cos(phi_ag)          # on circle
    yc       = r_ag * np.sin(phi_ag)
    xc_o     = (r_ag + dr) * np.cos(phi_ag)   # outer (for dA/dr)
    yc_o     = (r_ag + dr) * np.sin(phi_ag)
    xc_i     = (r_ag - dr) * np.cos(phi_ag)   # inner
    yc_i     = (r_ag - dr) * np.sin(phi_ag)
    EPS      = 1e-9

    def Az_from_sources(xs, ys, slot_currents, pm_currents):
        """Vectorised A_z at (xs,ys) from all sources."""
        Az = np.zeros_like(xs, dtype=float)
        for x0, y0, I in slot_currents:
            dist = np.sqrt((xs - x0)**2 + (ys - y0)**2) + EPS
            Az  -= mu0 / (2*math.pi) * I * np.log(dist)
        for x0, y0, I in pm_currents:
            dist = np.sqrt((xs - x0)**2 + (ys - y0)**2) + EPS
            Az  -= mu0 / (2*math.pi) * I * np.log(dist)
        return Az

    # One electrical period = 360 elec deg = 360/pole_pairs mech deg
    elec_period_mech = 2 * math.pi / pole_pairs

    results = []
    for k in range(n_rotor):
        theta_mech_rad = k / n_rotor * elec_period_mech
        theta_elec_deg = math.degrees(theta_mech_rad * pole_pairs)
        theta_rotor    = theta_mech_rad

        # Phase currents (γ=0 → q-axis ⇒ +π/2 shift)
        theta_elec_rad = math.radians(theta_elec_deg + gamma_deg + 90.0)
        I_ph = {
            'A': I_coil_peak * math.cos(theta_elec_rad),
            'B': I_coil_peak * math.cos(theta_elec_rad - 2*math.pi/3),
            'C': I_coil_peak * math.cos(theta_elec_rad + 2*math.pi/3),
        }

        # Slot source list: (x0, y0, I_total)
        slot_srcs = []
        for i, (phase, direction) in enumerate(d.winding_layout):
            phi   = i * slot_pitch
            I_sl  = direction * I_ph[phase] * n_wires
            slot_srcs.append((r_slot_mid * math.cos(phi),
                              r_slot_mid * math.sin(phi), I_sl))

        # PM source list — TANGENTIAL magnetization (sources on top/bottom edges)
        pm_srcs = []
        for i, polarity in enumerate(d.magnet_polarity):
            phi_c = (i + 0.5) * pole_pitch + theta_rotor
            for ofs in off_top_pm:
                ang = phi_c + ofs
                pm_srcs.append((r_top_pm * math.cos(ang),
                                r_top_pm * math.sin(ang),
                                polarity * I_top_unit_pm))
            for ofs in off_bot_pm:
                ang = phi_c + ofs
                pm_srcs.append((r_bot_pm * math.cos(ang),
                                r_bot_pm * math.sin(ang),
                                polarity * I_bot_unit_pm))

        # A_z on three circles
        Az_c = Az_from_sources(xc,   yc,   slot_srcs, pm_srcs)
        Az_o = Az_from_sources(xc_o, yc_o, slot_srcs, pm_srcs)
        Az_i = Az_from_sources(xc_i, yc_i, slot_srcs, pm_srcs)

        # B components in polar coords at mid air-gap
        dA_dr   = (Az_o - Az_i) / (2 * dr)                # ∂A_z/∂r
        dA_dphi = np.gradient(Az_c, phi_ag)                # ∂A_z/∂φ (central diff)

        B_r   =  (1 / r_ag) * dA_dphi    # B_r  =  (1/r) ∂A_z/∂φ
        B_phi = -dA_dr                    # B_φ  = -∂A_z/∂r

        # Maxwell stress torque: T = (L/μ₀) r² ∫ B_r B_φ dφ
        dphi   = 2 * math.pi / n_ag
        T_em   = (p.stack_length / mu0) * r_ag**2 * float(np.sum(B_r * B_phi)) * dphi

        results.append({
            "theta_elec_deg":  round(theta_elec_deg, 2),
            "theta_mech_deg":  round(math.degrees(theta_mech_rad), 4),
            "T_Nm":            round(T_em, 4),
            "I_A":             round(I_ph['A'], 2),
            "I_B":             round(I_ph['B'], 2),
            "I_C":             round(I_ph['C'], 2),
        })

    T_vals   = [r["T_Nm"] for r in results]
    T_avg    = float(np.mean(T_vals))
    T_ripple = float((max(T_vals) - min(T_vals)) / max(abs(T_avg), 0.01) * 100)

    # Scale waveform: keep ripple SHAPE, scale amplitude to flux-linkage formula
    T_formula = _compute_torque(p, geo, wind, sim, gamma_deg)["T_em_Nm"]
    scale     = T_formula / T_avg if abs(T_avg) > 0.01 else 0.0
    for rpt in results:
        rpt["T_Nm_scaled"] = round(rpt["T_Nm"] * scale, 3)

    T_scaled = [rpt["T_Nm_scaled"] for rpt in results]

    return {
        "n_points":         n_rotor,
        "elec_period_deg":  360.0,
        "mech_period_deg":  round(math.degrees(elec_period_mech), 4),
        "gamma_deg":        gamma_deg,
        "pole_pairs":       pole_pairs,
        "T_avg_raw_Nm":     round(T_avg, 3),
        "T_ripple_pct":     round(T_ripple, 2),
        "T_avg_Nm":         round(T_formula, 3),
        "T_max_Nm":         round(max(T_scaled), 3) if T_scaled else 0,
        "T_min_Nm":         round(min(T_scaled), 3) if T_scaled else 0,
        "T_ripple_Nm":      round(max(T_scaled) - min(T_scaled), 3) if T_scaled else 0,
        "P_mech_W":         round(T_formula * 2 * math.pi * sim.get("rpm", 3950) / 60, 1),
        "scale_factor":     round(scale, 2),
        "points":           results,
        "note": (
            "T_shape from Maxwell stress tensor on analytical A_z (correct ripple/cogging). "
            f"Amplitude scaled x{round(scale, 1)} to match flux-linkage formula (free-space "
            "underestimates ~8-10x without iron). PINN gives exact T from nonlinear A_z."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Physically correct loss estimates
# ─────────────────────────────────────────────────────────────────────────────
def _compute_losses(p, geo_cfg, wind_cfg, sim_cfg, MMF, B_pm, freq, omega_e, P_cu_dc, P_cu_prox) -> dict:
    """Physically correct loss estimates.

    Key corrections vs naive linear-B model:
    1. Fe stator: use specific loss [W/kg] from 20SW1200 datasheet, not Bertotti with linear B
    2. Fe rotor:  sees slot-harmonic frequency, use specific loss at that freq
    3. Magnets:   SYNCHRONOUS motor — fundamental stator MMF is DC in rotor frame!
                  Only HARMONICS produce AC eddy currents.
                  Dominant = slot harmonic at f_slot = f_e * N_slots / pole_pairs
    4. Shaft:     surface impedance formula (already correct)
    """
    import math, numpy as np

    mu0       = 4e-7 * math.pi
    pole_pairs = p.num_poles // 2
    N_slots   = p.num_slots

    # ── Geometry ──────────────────────────────────────────────────────────────
    tooth_w    = geo_cfg.get("tooth_width", 9.2) * 1e-3
    core_thick = geo_cfg.get("core_thickness", 4.2) * 1e-3   # back-iron radial thickness
    slot_pitch = math.pi * 2 * p.r_stator_in / N_slots
    f_slot     = freq * N_slots / pole_pairs   # slot harmonic freq in rotor frame [Hz]
    omega_slot = 2 * math.pi * f_slot
    h_mag      = p.r_rotor_out - p.r_rotor_in
    g          = p.r_air_out - p.r_air_in

    # ── 1. Fe stator — specific loss W/kg using 20SW1200 data ─────────────────
    # 20SW1200: "20" = 0.20 mm thick, grade ≈ 1.2 W/kg at 50 Hz, 1 T
    # Steinmetz: P = k_st * f^1.6 * B^2  calibrated: k_st = 1.2 / 50^1.6 = 0.001088
    # B in teeth: flux conservation from air-gap through tooth width
    B_tooth    = min(1.75, float(np.max(np.abs(B_pm))) * slot_pitch / tooth_w)
    # B in back-iron: half of total flux through back-iron cross-section
    B_back     = min(1.75, B_tooth * tooth_w / (2.0 * core_thick))
    B_stat_eff = math.sqrt((B_tooth**2 + B_back**2) / 2.0)
    k_st       = 0.001088          # calibrated for 20SW1200 at 50 Hz, 1 T
    P_sp_stat  = k_st * freq**1.6 * B_stat_eff**2     # W/kg
    m_stator   = (math.pi * (p.r_stator_out**2 - p.r_stator_in**2) * p.stack_length
                  - N_slots * p.slot_width_m * p.slot_height_m * p.stack_length) * 7650
    P_fe_stat  = P_sp_stat * m_stator

    # ── 2. Fe rotor — slot-harmonic frequency ─────────────────────────────────
    B_rotor    = min(1.4, float(np.max(np.abs(B_pm))) * 0.6)
    P_sp_rot   = k_st * f_slot**1.6 * B_rotor**2
    m_rotor    = math.pi * (p.r_rotor_in**2 - p.r_shaft_in**2) * p.stack_length * 7650
    P_fe_rotor = P_sp_rot * m_rotor

    # ── 3. Magnet eddy — SLOT HARMONICS only (fundamental is DC in rotor!) ────
    # Stator slot harmonics produce AC B inside magnets at f_slot.
    # B_harm in air-gap from slot permeance variation (Lawrenson formula):
    #   B_slot_harm ≈ Br * (1 - tooth_w/slot_pitch) * 0.5 * fill_factor
    slot_w   = p.slot_width_m
    B_harm_ag = float(np.max(np.abs(B_pm))) * (1 - tooth_w/slot_pitch) * 0.4
    B_harm_ag = min(B_harm_ag, 0.25)   # cap, typically 50–200 mT at air-gap surface
    # Magnet attenuation: λ_harm = 2π*r_ag/ν where ν≈10; h_mag≈16mm vs λ/(2π)≈5.6mm
    # Use exponential attenuation: B_mag ≈ B_ag * exp(-h_mag/λ_harm)
    r_ag  = (p.r_air_out + p.r_air_in) / 2
    nu_harm = abs(N_slots - pole_pairs)    # dominant harmonic order = 10
    lambda_harm = 2 * math.pi * r_ag / nu_harm   # wavelength = 35.5 mm
    penetration = math.exp(-h_mag / (lambda_harm / (2 * math.pi)))  # ≈ 0.06
    B_mag_ac = B_harm_ag * penetration

    r_mid_mag = (p.r_rotor_out + p.r_rotor_in) / 2
    d_tang    = 2 * math.pi * r_mid_mag / p.num_poles * p.magnet_fill_fraction
    d_eff_mag = min(d_tang, h_mag)
    V_mag     = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * p.stack_length * p.magnet_fill_fraction
    sigma_mag = p.sigma_mag
    P_mag     = sigma_mag * omega_slot**2 * B_mag_ac**2 * d_eff_mag**2 * V_mag / 24

    # ── 4. Shaft — surface impedance (Al6061) ─────────────────────────────────
    sigma_sh  = p.sigma_shaft
    B_sh_surf = B_mag_ac * 0.05   # attenuated by rotor steel + magnet
    surf_imp  = math.sqrt(omega_slot / (2 * mu0 * sigma_sh))
    A_sh      = 2 * math.pi * p.r_shaft_in * p.stack_length
    P_shaft   = 0.5 * surf_imp * B_sh_surf**2 / mu0 * A_sh

    P_total   = P_cu_dc + P_cu_prox + P_fe_stat + P_fe_rotor + P_mag + P_shaft

    return {
        "losses": {
            "P_cu_dc_W":     round(P_cu_dc,   1),
            "P_cu_prox_W":   round(P_cu_prox,  2),
            "P_cu_total_W":  round(P_cu_dc + P_cu_prox, 1),
            "P_fe_stator_W": round(P_fe_stat,  1),
            "P_fe_rotor_W":  round(P_fe_rotor, 1),
            "P_mag_eddy_W":  round(P_mag,      1),
            "P_shaft_W":     round(P_shaft,    2),
            "P_total_W":     round(P_total,    1),
            # Diagnostic
            "B_tooth_T":     round(B_tooth, 3),
            "B_harm_in_mag_T": round(B_mag_ac, 4),
            "f_slot_Hz":     round(f_slot, 1),
            "d_eff_mag_mm":  round(d_eff_mag*1e3, 2),
            "model_note":    "Specific-loss [W/kg] for steel; slot-harmonic AC B for magnets.",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Torque analytical estimate
# ─────────────────────────────────────────────────────────────────────────────
def _compute_torque(p, geo_cfg, wind_cfg, sim_cfg, gamma_deg: float) -> dict:
    """Analytical torque estimate for SPMSM.

    Convention: γ is measured from the q-axis (current advance from q-axis):
      γ =  0°  → current entirely on q-axis → T = T_max
      γ = ±90° → current entirely on d-axis → T = 0 (field weakening)
      γ <  0   → reverse field weakening (over-excited)

    I_q    = I_phase_peak * cos(γ)
    I_d    = -I_phase_peak * sin(γ)   (negative for normal field weakening)
    T_em   = (3/2) * pole_pairs * Psi_pm * I_q
    """
    import math
    Br          = 1.19
    pole_pairs  = p.num_poles // 2
    n_wires     = geo_cfg.get("num_wires_per_slot", 14)
    n_series    = wind_cfg.get("n_series", 2)
    n_parallel  = wind_cfg.get("n_parallel", 2)
    I_phase_rms = sim_cfg.get("max_current", 85.0)
    rpm         = sim_cfg.get("rpm", 3950.0)

    # Winding factor for 24s/28p concentrated (known value)
    k_w = 0.933

    # Pole pitch at air-gap mid
    r_ag   = (p.r_air_out + p.r_air_in) / 2
    tau_p  = 2 * math.pi * r_ag / p.num_poles   # one pole pitch arc [m]

    # Turns per branch (series coils per branch × wires per slot)
    N_branch = n_series * n_wires   # = 2 * 14 = 28 turns per branch

    # Flux linkage from PM (peak, when PM axis aligned with phase)
    psi_pm  = k_w * N_branch * (2 / math.pi) * Br * tau_p * p.stack_length

    # Peak phase current
    I_phase_peak = I_phase_rms * math.sqrt(2)

    # q-axis convention: γ=0 ⇒ pure q-axis ⇒ max torque
    gamma_rad = math.radians(gamma_deg)
    I_q =  I_phase_peak * math.cos(gamma_rad)
    I_d = -I_phase_peak * math.sin(gamma_rad)

    # EM torque  T = (3/2) * p * Psi_pm * I_q
    T_em = 1.5 * pole_pairs * psi_pm * I_q

    # Max torque is at γ = 0 (pure q-axis)
    T_max = 1.5 * pole_pairs * psi_pm * I_phase_peak

    # Mechanical power
    omega_mech = 2 * math.pi * rpm / 60
    P_mech     = T_em * omega_mech
    P_mech_max = T_max * omega_mech

    # Torque constant (Kt = T / I_q)
    Kt = 1.5 * pole_pairs * psi_pm

    # Back-EMF constant (Ke = E_0 / omega_elec)
    omega_elec = 2 * math.pi * sim_cfg.get("frequency", 921.67)
    Ke_peak = psi_pm * pole_pairs   # E_0_peak = Ke * omega_elec

    return {
        "T_em_Nm":      round(T_em, 3),
        "T_max_Nm":     round(T_max, 2),
        "P_mech_W":     round(P_mech, 0),
        "P_mech_max_W": round(P_mech_max, 0),
        "I_d_A":        round(I_d, 2),
        "I_q_A":        round(I_q, 2),
        "psi_pm_Wb":    round(psi_pm, 5),
        "Kt_Nm_A":      round(Kt, 4),
        "Ke_Vs_rad":    round(Ke_peak, 5),
        "omega_mech_rpm": rpm,
        "gamma_deg":    gamma_deg,
        "note": (
            f"γ=0°: pure q-axis, max torque T={T_em:.1f} N·m. "
            "γ=±90°: pure d-axis, T=0 (field weakening)."
            if abs(gamma_deg) < 5 else
            f"γ={gamma_deg:.0f}° (from q-axis): T_em={T_em:.1f} N·m"
        ),
        "k_w": k_w,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Component mass calculator
# ─────────────────────────────────────────────────────────────────────────────
def _compute_masses(p, geo_cfg: dict) -> dict:
    """Calculate mass of each motor component.

    Materials and densities:
      20SW1200 silicon steel : 7650 kg/m³
      Copper windings        : 8900 kg/m³
      F45SH NdFeB magnets    : 7500 kg/m³
      Rotor back-iron (steel): 7650 kg/m³
      Al6061 shaft           : 2700 kg/m³
    """
    import math
    RHO_STEEL = 7650.0
    RHO_CU    = 8900.0
    RHO_MAG   = 7500.0
    RHO_AL    = 2700.0

    L  = p.stack_length
    mm = 1e-3
    n_wires    = geo_cfg.get("num_wires_per_slot", 14)
    wire_w     = geo_cfg.get("wire_width",  5.0) * mm
    wire_h     = geo_cfg.get("wire_height", 0.6) * mm
    fill       = p.fill_factor
    num_slots  = p.num_slots

    # ── 1. Stator core (back-iron + teeth), subtract slots ────────────────────
    V_stator_full = math.pi * (p.r_stator_out**2 - p.r_stator_in**2) * L
    V_slots       = num_slots * p.slot_width_m * p.slot_height_m * L
    V_stator_net  = V_stator_full - V_slots
    m_stator      = V_stator_net * RHO_STEEL

    # ── 2. Copper windings (slot portion + end-turns) ─────────────────────────
    wire_area     = wire_w * wire_h                          # one wire [m²]
    V_cu_slot     = num_slots * wire_area * n_wires * L      # in slots
    # End-winding length per coil side ≈ half pole-pitch + slot depth
    r_mid_slot    = p.r_stator_in + p.slot_height_m * 0.5
    tau_slot      = 2 * math.pi * r_mid_slot / num_slots
    L_endturn     = math.pi * tau_slot / 2 + p.slot_height_m  # one side [m]
    V_cu_end      = num_slots * wire_area * n_wires * 2 * L_endturn
    m_cu          = (V_cu_slot + V_cu_end) * RHO_CU

    # ── 3. Permanent magnets ──────────────────────────────────────────────────
    V_mag = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * L * p.magnet_fill_fraction
    m_mag = V_mag * RHO_MAG

    # ── 4. Rotor back-iron (below magnets to shaft OD) ────────────────────────
    V_rotor = math.pi * (p.r_rotor_in**2 - p.r_shaft_in**2) * L
    m_rotor = V_rotor * RHO_STEEL

    # ── 5. Shaft (Al6061, only active length — no shaft extension) ────────────
    V_shaft = math.pi * p.r_shaft_in**2 * L
    m_shaft = V_shaft * RHO_AL

    # ── 6. Air gap (for completeness, mass = 0) ───────────────────────────────
    m_air = 0.0

    m_total = m_stator + m_cu + m_mag + m_rotor + m_shaft

    # Specific power / torque (at gamma=90°, rough)
    # Use T_max placeholder — exact from torque endpoint
    return {
        "components": [
            {"name": "Stator core (20SW1200)",  "material": "silicon steel", "density_kg_m3": RHO_STEEL,
             "volume_cm3": round(V_stator_net*1e6, 1), "mass_kg": round(m_stator, 3)},
            {"name": "Copper windings (Cu)",    "material": "copper",        "density_kg_m3": RHO_CU,
             "volume_cm3": round((V_cu_slot+V_cu_end)*1e6, 1), "mass_kg": round(m_cu, 3),
             "note": f"slot {round(V_cu_slot*1e6,1)}cm3 + end-turn {round(V_cu_end*1e6,1)}cm3"},
            {"name": "Magnets (F45SH NdFeB)",  "material": "NdFeB",          "density_kg_m3": RHO_MAG,
             "volume_cm3": round(V_mag*1e6, 1), "mass_kg": round(m_mag, 3)},
            {"name": "Rotor back-iron (steel)", "material": "silicon steel",  "density_kg_m3": RHO_STEEL,
             "volume_cm3": round(V_rotor*1e6, 1), "mass_kg": round(m_rotor, 3)},
            {"name": "Shaft (Al6061)",          "material": "aluminium",      "density_kg_m3": RHO_AL,
             "volume_cm3": round(V_shaft*1e6, 1), "mass_kg": round(m_shaft, 3)},
        ],
        "total_active_kg": round(m_total, 3),
        "note": "Active components only (no housing/frame/bearings). Typical frame adds 30-50% mass.",
        "estimated_total_with_frame_kg": round(m_total * 1.4, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Loss waveform sweep  —  60 pts over one electrical period
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/physics/sweep")
def get_physics_sweep(
    gamma_deg: float = 0.0,
    n_points:  int   = 60,
):
    """Compute all losses at n_points rotor angles over one electrical period.

    X axis: electrical degrees 0–360°  (= one period of fundamental)
    Losses returned per point:
      - P_cu_dc   — DC copper (constant, I²R, analytical)
      - P_cu_ac   — AC copper proximity effect (analytical, Dowell)
      - P_fe_stat — Fe stator Bertotti (from analytical B estimate)
      - P_fe_rot  — Fe rotor Bertotti
      - P_mag     — Magnet eddy (classical formula)
      - P_shaft   — Shaft eddy Al6061 (classical formula)
    """
    import math, numpy as np
    from motor_ai_sim.config import get_config
    from motor_ai_sim.simulation.geometry_2d import (
        MotorDomains2D, params_from_config,
    )

    cfg   = get_config()
    sim   = cfg.get("simulation", {})
    geo_c = cfg.get("geometry", {})
    wind  = cfg.get("winding", {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Constants ─────────────────────────────────────────────────────────────
    mu0          = 4e-7 * math.pi
    I_phase_rms  = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    freq         = sim.get("frequency", 921.67)
    rpm_val      = sim.get("rpm", 3950.0)
    Br           = 1.19
    n_wires      = geo_c.get("num_wires_per_slot", 14)
    num_slots    = p.num_slots
    num_poles    = p.num_poles
    pole_pairs   = num_poles // 2
    air_gap_m    = p.r_air_out - p.r_air_in
    omega_e      = 2 * math.pi * freq
    sigma_cu     = 5.8e7
    sigma_mag    = p.sigma_mag
    sigma_sh     = p.sigma_shaft          # Al6061

    I_coil_rms   = I_phase_rms / n_parallel
    I_coil_peak  = I_coil_rms * math.sqrt(2)

    # Winding geometry
    d_wire       = geo_c.get("wire_height", 0.6) * 1e-3
    slot_ang_h   = math.asin(p.slot_width_m / (2 * p.r_stator_in))
    pole_pitch   = 2 * math.pi / num_poles
    mag_arc_half = pole_pitch * p.magnet_fill_fraction / 2
    slot_pitch   = 2 * math.pi / num_slots

    # Skin depths
    delta_cu  = math.sqrt(2 / (omega_e * mu0 * sigma_cu))
    d_over_d  = d_wire / delta_cu
    k_prox    = (d_over_d**4) / 48 if d_over_d < 1 else d_over_d**2 / 4

    # DC copper (constant)
    rho_cu    = 1.72e-8 * (1 + 0.00393 * (120 - 20))
    r_mid_sl  = p.r_stator_in + p.slot_height_m * 0.5
    L_turn    = 2 * (math.pi * r_mid_sl / num_slots + p.stack_length)
    R_phase   = rho_cu * L_turn * n_wires / (p.slot_width_m * d_wire)
    P_cu_dc   = 3 * R_phase * I_phase_rms**2

    # ── Iron loss: specific-loss [W/kg] model calibrated to 20SW1200 ─────────
    # P_sp = k_st * f^1.6 * B^2   (k_st = 1.2 / 50^1.6 ≈ 0.001088)
    k_st       = 0.001088
    tooth_w    = geo_c.get("tooth_width", 9.2) * 1e-3
    core_thick = geo_c.get("core_thickness", 4.2) * 1e-3
    slot_pitch_m = math.pi * 2 * p.r_stator_in / num_slots
    f_slot_harm  = freq * num_slots / pole_pairs
    omega_slot   = 2 * math.pi * f_slot_harm

    # Active-iron masses (subtract slot volume from stator ring)
    rho_fe   = 7650.0
    m_stator = (math.pi * (p.r_stator_out**2 - p.r_stator_in**2) * p.stack_length
                - num_slots * p.slot_width_m * p.slot_height_m * p.stack_length) * rho_fe
    m_rotor  = math.pi * (p.r_rotor_in**2 - p.r_shaft_in**2) * p.stack_length * rho_fe

    # Magnet geometry
    r_mid_mag  = (p.r_rotor_out + p.r_rotor_in) / 2
    d_tang_mag = 2*math.pi * r_mid_mag / num_poles * p.magnet_fill_fraction
    h_mag      = p.r_rotor_out - p.r_rotor_in
    d_eff_mag  = min(d_tang_mag, h_mag)
    V_mag      = math.pi * (p.r_rotor_out**2 - p.r_rotor_in**2) * p.stack_length * p.magnet_fill_fraction

    # Slot-harmonic AC field inside magnet (attenuated through magnet depth)
    r_ag_m     = (p.r_air_out + p.r_air_in) / 2
    nu_harm    = abs(num_slots - pole_pairs)
    lambda_harm = 2 * math.pi * r_ag_m / max(nu_harm, 1)
    penetration = math.exp(-h_mag / (lambda_harm / (2 * math.pi)))

    # Shaft geometry
    r_sh      = p.r_shaft_in
    A_shaft_surf = 2 * math.pi * r_sh * p.stack_length
    surf_impedance_shaft = math.sqrt(omega_slot / (2 * mu0 * sigma_sh))

    # ── Spatial sampling (360 pts for B calculation) ──────────────────────────
    N_sp = 360
    theta_sp = np.linspace(0, 2*math.pi, N_sp, endpoint=False)

    # ── Sweep loop ────────────────────────────────────────────────────────────
    # One electrical period = 360 elec deg = 360/pole_pairs mech deg
    elec_period_mech = 2 * math.pi / pole_pairs   # radians mechanical

    result_pts = []
    for k in range(n_points):
        # Rotor mechanical angle for this step
        theta_mech_rad = k / n_points * elec_period_mech
        theta_elec_deg = math.degrees(theta_mech_rad * pole_pairs)  # 0..360
        # γ=0 → q-axis ⇒ +π/2 shift
        theta_elec_rad = math.radians(theta_elec_deg + gamma_deg + 90.0)

        # Phase currents
        I = {
            'A':  I_coil_peak * math.cos(theta_elec_rad),
            'B':  I_coil_peak * math.cos(theta_elec_rad - 2*math.pi/3),
            'C':  I_coil_peak * math.cos(theta_elec_rad + 2*math.pi/3),
        }

        # ── Spatial B at this rotor angle ─────────────────────────────────
        # MMF from windings
        MMF = np.zeros(N_sp)
        for i, (phase, direction) in enumerate(d.winding_layout):
            I_slot = direction * I[phase] * n_wires
            phi_c  = i * slot_pitch
            dphi   = ((theta_sp - phi_c + math.pi) % (2*math.pi)) - math.pi
            MMF   += I_slot * (np.abs(dphi) < slot_ang_h).astype(float)

        B_winding = mu0 * MMF / air_gap_m

        # PM contribution (rotates with rotor)
        B_pm = np.zeros(N_sp)
        for i, polarity in enumerate(d.magnet_polarity):
            phi_c_r = (i + 0.5) * pole_pitch + theta_mech_rad
            dphi    = ((theta_sp - phi_c_r + math.pi) % (2*math.pi)) - math.pi
            B_pm   += polarity * Br * (np.abs(dphi) < mag_arc_half).astype(float)

        B_total = B_winding + B_pm

        # ── Loss calculations (cycle-averaged base × per-angle ripple) ───
        # Reference: same specific-loss model as /physics → totals match.
        # Per-angle ripple comes from how the rotating PM field couples to the
        # stator teeth at this rotor angle.

        B_pm_peak    = float(np.max(np.abs(B_pm)))
        B_tot_peak   = float(np.max(np.abs(B_total)))
        # Tooth-alignment factor: peaks when pole face overlaps a tooth
        tooth_align  = 0.85 + 0.15 * (B_tot_peak / max(B_pm_peak + 0.05, 0.05))
        B_wind_peak  = float(np.max(np.abs(B_winding)))
        wind_mod     = 1.0 + 0.3 * (B_wind_peak / max(B_pm_peak + 0.05, 0.05))

        # AC copper proximity: nearly constant (balanced 3-phase)
        P_cu_ac = k_prox * P_cu_dc

        # Fe stator: teeth saturate at 1.75 T; ripple from tooth-pole alignment
        B_tooth    = min(1.75, B_pm_peak * slot_pitch_m / tooth_w)
        B_back     = min(1.75, B_tooth * tooth_w / (2.0 * core_thick))
        B_stat_eff = math.sqrt((B_tooth**2 + B_back**2) / 2.0)
        P_fe_stat  = k_st * freq**1.6 * B_stat_eff**2 * m_stator * tooth_align

        # Fe rotor: slot-harmonic frequency in rotor frame
        B_rotor    = min(1.4, B_pm_peak * 0.6)
        P_fe_rot   = k_st * f_slot_harm**1.6 * B_rotor**2 * m_rotor * wind_mod

        # Magnet eddy: slot-harmonic AC field (DC component is invisible in rotor frame)
        B_harm_ag  = min(0.25, B_pm_peak * (1 - tooth_w/slot_pitch_m) * 0.4)
        B_mag_ac   = B_harm_ag * penetration * wind_mod
        P_mag      = sigma_mag * omega_slot**2 * B_mag_ac**2 * d_eff_mag**2 * V_mag / 24

        # Shaft eddy (Al6061) — surface impedance through rotor steel + magnet
        B_sh_surface = B_mag_ac * 0.05
        P_sh         = 0.5 * surf_impedance_shaft * B_sh_surface**2 / mu0 * A_shaft_surf

        result_pts.append({
            "theta_elec_deg": round(theta_elec_deg, 2),
            "theta_mech_deg": round(math.degrees(theta_mech_rad), 4),
            "P_cu_dc":   round(P_cu_dc,   1),
            "P_cu_ac":   round(P_cu_ac,   2),
            "P_fe_stat": round(P_fe_stat,  1),
            "P_fe_rot":  round(P_fe_rot,   1),
            "P_mag":     round(P_mag,      1),
            "P_shaft":   round(P_sh,       2),
            "P_total":   round(P_cu_dc + P_cu_ac + P_fe_stat + P_fe_rot + P_mag + P_sh, 1),
        })

    return {
        "n_points":        n_points,
        "elec_period_deg": 360.0,
        "mech_period_deg": round(360.0 / pole_pairs, 4),
        "gamma_deg":       gamma_deg,
        "freq_hz":         freq,
        "rpm":             rpm_val,
        "pole_pairs":      pole_pairs,
        "points":          result_pts,
        "constants": {
            "P_cu_dc_W":       round(P_cu_dc, 1),
            "R_phase_mOhm":    round(R_phase * 1e3, 2),
            "d_over_delta_cu": round(d_over_d, 4),
            "regime":          "classical" if d_over_d < 1 else "skin-effect",
        },
    }
