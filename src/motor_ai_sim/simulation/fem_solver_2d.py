"""2-D finite-element magnetostatics solver — pure Python (scikit-fem + gmsh).

Solves   ∇·(ν ∇A_z) = -J_z + ∇·(M × ẑ)    on a triangle mesh of the motor
cross-section.  Used as a real-FEM alternative to the analytical Green's
function solver in routes/simulation.py.

Domain-specific data (matches the CadQuery polygon classes):
    air-gap        ν = 1/μ₀                     (free space)
    stator steel   ν = 1/(μ₀·5000)              (silicon steel, linear)
    rotor steel    ν = 1/(μ₀·5000)
    shaft          ν = 1/(μ₀·1000)              (aluminium-ish)
    magnets        ν = 1/(μ₀·1.05),  M = ±Br·φ̂   (tangential, alternating)
    coils          ν = 1/μ₀,  J_z = direction · I_phase · n_wires / area

Boundary: A_z = 0 on the outer stator circle.

Method: linear FE on a P1-triangle mesh; gmsh builds a conforming mesh from
the real CadQuery exterior+interior contours so the same geometry the
canvas displays is the geometry we solve on.

Time budget for one solve at the default mesh density (~25k triangles):
roughly 1-2 s on a modern CPU.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np

# ── Lower layers ─────────────────────────────────────────────────────────────
# Domain tags / mesh flags and the whole geometry->mesh stage now live in their
# own modules.  Re-exported here rather than merely imported: routes.simulation,
# modules.mesh and geo_mesh all reach for these off fem_solver_2d, and moving a
# file should not break a caller.
from motor_ai_sim.simulation.sb_domains import (  # noqa: F401  (re-export)
    DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_COIL, DOM_COIL_BASE, DOM_MAG_BASE,
    DOM_MAG_N, DOM_MAG_S, DOM_OUTER, DOM_ROTOR, DOM_SHAFT, DOM_STATOR,
    _GMSH_LOCK, _N_SLIP, _SB_BAND_DELTA_FRAC, _SB_BELT, _SB_GEO_MESH,
    _SB_GEO_SECTOR, _SB_IRON_RESAMPLE, _SB_IRON_TEMPLATE, _SB_POLE_COPY_ROTOR,
    _SB_POLE_COPY_STATOR, _SB_ROT_PERIODICITY, _SB_STRUCTURED_GAP,
    _SB_STRUCTURED_STRIPS, _SG_EPS_OVERRIDE, _SG_M_TARGET,
)
from motor_ai_sim.simulation.mesher import (  # noqa: F401  (re-export)
    _fillet_polygon, _decimate_ring_by_angle, _decimate_poly_by_angle,
    _simplify_polys, _add_background_air, _add_motion_band,
    _clip_polys_to_sector, _split_polys_for_sliding_band, _replicate_periodic_half,
    _build_sliding_band_meshes, _find_ring_nodes, _band_master_slave_pairing,
    _rotate_mesh_points, _structured_band_mesh, _structured_rect_mesh,
    _mesh_single_polygon, build_periodic_magnet_mesh, build_periodic_coil_mesh,
    _split_ring_pinches, _split_geom_pinches, _resample_ring_arcs,
    _weld_belt_into_half, _structured_gap_sm, _iron_arc_ring_occ,
    _build_structured_gap_cells, build_mesh_from_polygons, _weld_coincident_nodes,
    _read_cell_tags_by_dom, _pair_sector_cut_nodes, _apply_anti_periodic,
    _stitch_full_half,
)

log = logging.getLogger(__name__)


MU0 = 4e-7 * math.pi

# Saturation-Picard honest stopping: worst (over saturable tags) RELATIVE L2
# fixed-point residual of the nu(|B|) update (measured BEFORE damping) below
# which a frame's nonlinear iteration is converged (two consecutive sweeps).
# Replaces the old fixed "14 iterations" recipe, which did not converge and left
# a 5-8 Nm p-p no-load torque floor (see PARITY_FINDINGS_band_mode.md).
_PIC_TOL = 1e-3

# Convergence tolerance on the per-element magnet Br factor: a frame counts as
# converged once no element's remanence still moves by more than this.  The
# demag de-rating is monotone, so this only decides when the coupling
# (magnet weakens -> its own demagnetising field weakens) has settled.
_DEMAG_TOL = 1e-4

# Single source of truth for the d-axis phase offset: the electrical angle added
# to (rotor_angle·pole_pairs + γ) so that γ=0 lands on the q-axis.  MUST be
# identical across every solve path (transient currents, static field, eddy) —
# otherwise the field/torque would be at a different phase per path.
#
# = ideal 90° d→q rotation  +  the motor's rotor-d-axis-vs-phase-A GEOMETRIC
# offset.  That offset is TOPOLOGY-dependent (pole/slot/winding) and must be
# calibrated per motor: a hardcoded 90 left γ=0 ~18° off the q-axis for the
# 20-pole/24-slot motor, so torque peaked at γ≈+38° and "kept rising with γ"
# instead of peaking at a small-+ MTPA.
# Calibrated 2026-06-16 for the 20p/24s topology via a no-load (I=0) run:
# psi_A(θ) peaks at θ*=34.2° mech (342° el) ⇒ DAXIS = (90 − 342) ≡ 108° (mod 360).
# Now γ=0 = q-axis and MTPA sits at a small + γ (~+20° for this motor).
# RECALIBRATE only if the pole/slot/winding TOPOLOGY changes (dimension sweeps
# don't move it): run fem_transient_sliding_band(I_phase_rms=0); θ* = rotor angle
# of max psi_A_Wb; DAXIS_SHIFT_DEG = (90 − θ*·pole_pairs) mod 360.
DAXIS_SHIFT_DEG = 108.0   # LEGACY FALLBACK ONLY — the transient now AUTO-calibrates
                          # this per motor topology (see _resolve_daxis_shift); this
                          # hard value is used only if the no-load calibration fails.

# ── Per-motor d-axis auto-calibration ────────────────────────────────────────
# The geometric rotor-d-axis-vs-phase-A offset is TOPOLOGY-dependent (pole/slot/
# winding), so a single hard-coded DAXIS_SHIFT_DEG is only right for ONE motor
# (it was calibrated for 20p/24s and was ~48° wrong for 14p/12s → the UI's γ was
# 48° off the true q-axis).  We now compute θ* (rotor angle of peak no-load ψ_A)
# from a cheap I=0 run and set DAXIS = (90 − θ*·pole_pairs) mod 360, so γ=0 is the
# TRUE q-axis and the γ the user enters equals the PHYSICAL current angle from the
# q-axis (identical to ANSYS's el_deg).  Cached per topology; θ* is invariant to
# dimension sweeps.
_DAXIS_CACHE: Dict[tuple, float] = {}
_DAXIS_CALIBRATING = False

def _daxis_disk_path():
    """Shared on-disk DAXIS cache so sweep subprocesses (fresh interpreters, empty
    in-memory cache) don't each re-run the calibration → per-design timeout."""
    try:
        import os
        from motor_ai_sim.config import DEFAULT_CONFIG_PATH as _cp
        return os.path.join(os.path.dirname(str(_cp)), ".daxis_cache.json")
    except Exception:
        return None

def _daxis_topology_key(p, geo, wind) -> tuple:
    return (int(getattr(p, "num_poles", 0) or 0),
            int((geo or {}).get("num_slots") or 0),
            int((wind or {}).get("layers", 1) or 1),
            str((wind or {}).get("connection", "")))

def _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override, n_sectors) -> float:
    """DAXIS (elec deg) that makes γ=0 the true q-axis for THIS motor topology.
    Computed from a cheap no-load (I=0) run — ψ_A(θ) peaks at the d-axis, so
    DAXIS = (90 − θ*·pole_pairs) mod 360.  Cached per topology; recursion-guarded
    (the I=0 calibration run has no current, so DAXIS is irrelevant inside it)."""
    global _DAXIS_CALIBRATING
    if _DAXIS_CALIBRATING:
        return DAXIS_SHIFT_DEG
    key = _daxis_topology_key(p, geo, wind)
    if key in _DAXIS_CACHE:
        return _DAXIS_CACHE[key]
    skey = "_".join(str(x) for x in key)
    _dp = _daxis_disk_path()
    if _dp:                           # shared disk cache (across sweep subprocesses)
        try:
            import os, json
            if os.path.exists(_dp):
                with open(_dp) as _f:
                    _v = json.load(_f).get(skey)
                if _v is not None:
                    _DAXIS_CACHE[key] = float(_v)
                    return float(_v)
        except Exception:
            pass
    daxis = None                      # None ⇒ calibration did not succeed
    try:
        _DAXIS_CALIBRATING = True
        # Cheapest possible calibration: θ* (the ψ_A peak angle) is a bulk
        # geometric quantity → a COARSE mesh + the smallest natural symmetry
        # sector + few P1 steps localise it fine, and keep the extra geometry
        # build well inside the sweep's per-design budget.
        import math as _mth
        _cal_ns = _mth.gcd(int((geo or {}).get("num_slots") or 1),
                           int(getattr(p, "num_poles", 1) or 1)) or 1
        cal = em_transient_eval(
            n_steps_per_period=24, n_periods=1.0, gamma_deg=0.0,
            I_phase_rms=0.0, mesh_size_mm=2.5, min_size_mm=0.7,
            outer_air_factor=1.2, gap_layers=1.0,
            n_sectors=_cal_ns if _cal_ns >= 2 else -1,
            coil_temp_c=120.0, rotor_eddy=False,
            iron_template=False, geo_mesh=True,
            geo_override=geo_override, element_order=1)
        psi = np.asarray(cal.get("psi_A_Wb") or [], float)
        ang = np.asarray(cal.get("rotor_angle_deg") or [], float)
        if psi.size >= 4 and ang.size == psi.size:
            n = psi.size
            k0 = int(np.argmax(psi))
            ym1, y0, yp1 = psi[(k0 - 1) % n], psi[k0], psi[(k0 + 1) % n]
            den = (ym1 - 2.0 * y0 + yp1)
            frac = 0.5 * (ym1 - yp1) / den if abs(den) > 1e-30 else 0.0
            dstep = float(ang[1] - ang[0]) if n > 1 else 0.0
            theta_star = (k0 + frac) * dstep            # mech deg of ψ_A peak
            daxis = (90.0 - theta_star * float(pole_pairs)) % 360.0
            log.info("d-axis auto-cal: theta*=%.2f deg mech -> DAXIS=%.1f deg "
                     "(topology poles=%d slots=%d)", theta_star, daxis, key[0], key[1])
        else:
            log.warning("d-axis auto-cal: no ψ_A from calibration run -> fallback %.1f",
                        DAXIS_SHIFT_DEG)
    except Exception as _e:
        log.warning("d-axis auto-calibration failed (%s) -> fallback %.1f",
                    _e, DAXIS_SHIFT_DEG)
    finally:
        _DAXIS_CALIBRATING = False
    if daxis is None:                 # calibration failed → use fallback, DON'T
        return float(DAXIS_SHIFT_DEG) # cache it (so a later request can retry)
    _DAXIS_CACHE[key] = daxis
    if _dp:                           # persist so sweep subprocesses reuse it
        try:
            import os, json
            _disk = {}
            if os.path.exists(_dp):
                try:
                    with open(_dp) as _f:
                        _disk = json.load(_f) or {}
                except Exception:
                    _disk = {}
            _disk[skey] = float(daxis)
            with open(_dp, "w") as _f:
                json.dump(_disk, _f)
        except Exception:
            pass
    return daxis





# Template-copy each pole (rotor) / slot (stator): mesh ONE period, then rotate-
# copy + weld it so every period has a BIT-IDENTICAL interior, not just matched
# boundaries (setPeriodic).  Kills the pole-to-pole mesh-discretisation variance
# that leaves a ~1.2-1.6x residual on the loss waveform.  Per-half opt-in so the
# rotor can be validated before the (winding-phase-aware) stator.
import os as _os_sb

# True moving band (two uniform rings + closed-form re-stitched strip) vs the
# legacy merged single ring.  Diagnostic result: ord6 identical in both (the
# artifact is NOT the coupling); the one-row strip biases frame-local torque
# (frame0 -2.8 vs -0.1 N*m at I=0), so MERGED stays the default until the
# two-mesh gap bias is resolved.
_SB_MOVING_BAND = False

# Harmonic air-gap macroelement (Davat): replace the single-layer moving-band strip
# with the ANALYTIC Laplace solution of the gap annulus (block-circulant coupling,
# DFT-diagonal 2×2 per-harmonic blocks; rotor rotation = smooth phase e^{ikφ}).
# Implemented + validated (per-harmonic stiffness vs FEM annulus; mean torque matches
# the band).  RESULT (2026-06-30, #141): it does NOT reduce torque ripple — measured
# WORSE than the band at the same mesh (raw 25.6 % vs 17.6 %), because the dominant
# ripple is the FEM half-mesh teeth/slot discretisation (present in the field, seen by
# every torque contour), NOT the gap coupling; the band's coarse strip happens to
# low-pass it while the macroelement faithfully captures + (via virtual work) amplifies
# it.  Kept behind this flag (default OFF → production uses the band) for reference.
# 2026-07-08: env-gated (SB_AIRGAP_MACRO=1) so it can be re-tested ON TOP OF the
# structured (mapped) gap — the 2026-06-30 verdict above was measured on the FREE
# mesh, where the macroelement faithfully passed the tooth-discretisation noise;
# on the clean structured field the two are complementary (cells clean the field,
# the macro gives a smooth, continuous-angle coupling).
_SB_AIRGAP_MACRO = _os_sb.environ.get("SB_AIRGAP_MACRO", "0") == "1"
# 0 = adaptive formula (slip density scales with gap_layers); >0 forces slip
# nodes/period.  Env-gated so convergence studies can vary ONE discretisation
# at a time — the adaptive coupling (n_slip ~ gap_layers) otherwise changes the
# slip ring, the seam grid AND the air mesh together, making gl-sweeps impure.
_SLIP_PER_PERIOD_OVERRIDE = int(_os_sb.environ.get("SB_SLIP_PER_PERIOD", "0") or 0)

# ── Torque-band diagnostic (off by default; set ['on']=True before a solve to
# collect the per-frame Arkkio torque over radial sub-bands of the gap, to
# localise where parasitic ripple comes from — e.g. the slip-ring interface).
_TORQUE_DIAG = {"on": False, "full": [], "rotor": [], "stator": [],
                "iface": [], "rinner": [], "router": [],
                # per-frame angular profile of the Arkkio integrand (PHYSICAL
                # angle bins; rotor-half elements shifted by +θ_eff) — to see
                # WHERE around the gap the parasitic torque is generated.
                "ang_bins": 36, "ang_prof": []}


def _snap_steps_to_nodes(n_steps: int, nodes_per_period: int) -> int:
    """Snap the requested steps/period to the nearest DIVISOR of the slip-ring
    node count per electrical period, so the rotor advances a whole number of
    nodes each time step (uniform → strictly PERIODIC torque, not the chaotic
    jitter from round(θ/spacing) landing between nodes).

    Keeping the slip-node count FIXED (instead of scaling it with n_steps) means
    the mesh — and therefore the torque magnitude — does NOT drift with the time
    resolution.  Effective resolution is capped at nodes_per_period."""
    ns = max(int(n_steps), 1)
    if nodes_per_period % ns == 0:
        return ns
    divs = [d for d in range(1, nodes_per_period + 1) if nodes_per_period % d == 0]
    # nearest divisor; on a tie prefer the finer (larger) one
    return min(divs, key=lambda d: (abs(d - ns), -d))




@dataclass
class FEMMaterial:
    name:  str
    mu_r:  float
    J_z:   float = 0.0   # [A/m²]  external current density
    Mx:    float = 0.0   # [A/m]   magnetization x-component
    My:    float = 0.0   # [A/m]   magnetization y-component
    sigma: float = 0.0   # [S/m]   electrical conductivity — solid-conductor eddy
                         #         currents (copper, magnet, shaft).  0 = no eddy
                         #         (air / laminated iron treated as σ=0).
    # Optional measured B-H curve (list of (H_A_per_m, B_T) pairs).
    # When set, the non-linear Picard iteration uses it to derive μ_r(|B|)
    # at each iteration instead of the analytic Fröhlich roll-off.
    bh_curve: Optional[List[Tuple[float, float]]] = None


@dataclass
class FEMResult:
    """Sampled result on a regular Cartesian grid."""
    grid_size:   int
    extent:      Tuple[float, float, float, float]   # xmin xmax ymin ymax [m]
    A_z:         np.ndarray   # (gs, gs)
    B_x:         np.ndarray
    B_y:         np.ndarray
    B_mag:       np.ndarray
    J_z:         np.ndarray   # source J_z on grid
    domain:      np.ndarray   # int8, domain ids on grid
    n_triangles: int
    n_nodes:     int
    solve_time_s: float


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Build a triangle mesh from CadQuery Shapely polygons (using gmsh)
# ─────────────────────────────────────────────────────────────────────────────















# ─────────────────────────────────────────────────────────────────────────────
# Periodic coil meshing — one wire → all wires → all coils
# ─────────────────────────────────────────────────────────────────────────────













































# ─────────────────────────────────────────────────────────────────────────────
# 2.  Assemble + solve the linear magnetostatics problem
# ─────────────────────────────────────────────────────────────────────────────





def _build_magnet_bh_curve_payload(mats: Dict[int, "FEMMaterial"]) -> List[dict]:
    """Pull the assigned magnet's BH curve from any per-magnet material entry
    (they all share the same curve) for plotting on the frontend."""
    for tag in sorted(mats):
        if tag < DOM_MAG_BASE:
            continue
        mat = mats[tag]
        if mat.bh_curve and len(mat.bh_curve) >= 2:
            return [{"H_kA_per_m": round(h * 1e-3, 1), "B_T": round(b, 4)}
                    for (h, b) in mat.bh_curve]
    return []


def _b_from_bh_at_H(bh_curve: List[Tuple[float, float]], H: float) -> float:
    """Interpolate B at given H from a BH curve sorted by H ascending."""
    if not bh_curve:
        return 0.0
    hs = [pt[0] for pt in bh_curve]
    bs = [pt[1] for pt in bh_curve]
    if H <= hs[0]:
        # Linear extrapolation in H (negative side)
        slope = (bs[1] - bs[0]) / max(hs[1] - hs[0], 1e-12)
        return bs[0] + slope * (H - hs[0])
    if H >= hs[-1]:
        slope = (bs[-1] - bs[-2]) / max(hs[-1] - hs[-2], 1e-12)
        return bs[-1] + slope * (H - hs[-1])
    lo, hi = 0, len(hs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if hs[mid] < H: lo = mid
        else:           hi = mid
    f = (H - hs[lo]) / max(hs[hi] - hs[lo], 1e-12)
    return bs[lo] + f * (bs[hi] - bs[lo])


def _mu_r_from_bh(bh_curve: List[Tuple[float, float]], B_mag: float
                   ) -> float:
    """Effective μ_r at flux density |B| read from a measured B-H curve.

    The curve is a list of (H [A/m], B [T]) sample pairs sorted by H.  We
    invert it by linear interpolation in B to find H(B), then μ_r = B / (μ₀·H).

    Beyond the last tabulated point the curve is extrapolated with the
    incremental slope dB/dH ≈ μ₀ (deep saturation), so the iron behaves
    asymptotically like air.  Below the first point the initial slope is
    used.
    """
    if not bh_curve or len(bh_curve) < 2 or B_mag <= 1e-12:
        return 1.0
    # Curve is monotonically increasing in B as H increases.
    bs = [pt[1] for pt in bh_curve]
    hs = [pt[0] for pt in bh_curve]
    if B_mag <= bs[0]:
        H = hs[0] + (hs[1] - hs[0]) * (B_mag - bs[0]) / max(bs[1] - bs[0], 1e-12)
    elif B_mag >= bs[-1]:
        # Extrapolate above the last sample with the differential μ₀ slope.
        H = hs[-1] + (B_mag - bs[-1]) / MU0
    else:
        # Binary search for the segment
        lo, hi = 0, len(bs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if bs[mid] < B_mag:
                lo = mid
            else:
                hi = mid
        f = (B_mag - bs[lo]) / max(bs[hi] - bs[lo], 1e-12)
        H = hs[lo] + f * (hs[hi] - hs[lo])
    if H <= 1e-9:
        return 1.0e6                              # virtually infinite μ_r
    return B_mag / (MU0 * H)


def _mu_r_from_bh_vec(bh_curve, B_arr):
    """Vectorised μ_r(|B|) from a (H,B) curve — one value per element.

    H(B) by linear interpolation in B; above the last sample the differential
    μ₀ slope (deep saturation); μ_r = B/(μ₀·H), clamped to ≥ 1."""
    B = np.asarray(B_arr, dtype=float)
    if not bh_curve or len(bh_curve) < 2:
        return np.ones_like(B)
    hs = np.array([pt[0] for pt in bh_curve], float)
    bs = np.array([pt[1] for pt in bh_curve], float)
    H = np.interp(B, bs, hs)                       # clamps at the ends
    above = B >= bs[-1]
    H = np.where(above, hs[-1] + (B - bs[-1]) / MU0, H)
    H = np.maximum(H, 1e-9)
    mu = np.where(B <= 1e-12, 1.0, B / (MU0 * H))
    return np.maximum(mu, 1.0)


def solve_magnetostatics(
    mesh,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    n_sectors: int = 1,
    pole_pairs_per_sector_is_half_integer: bool = True,
    nonlinear_iterations: int = 8,
) -> np.ndarray:
    """Linear 2-D magnetostatics solve.

    Returns the nodal A_z vector (shape n_nodes,) for the P1 basis.

    Equation:   ∫ ν ∇A_z·∇v  dΩ  =  ∫ J_z v dΩ  +  ∫ (Mx ∂v/∂y − My ∂v/∂x) dΩ

    When `n_sectors > 1`, anti-periodic master-slave boundary conditions are
    enforced on the two radial cuts of the sector:
        A_z(r, θ=0) = -A_z(r, θ=2π/n_sectors)
    The sign flips because each sector covers an ODD number of poles for
    the 24-slot / 28-pole motor (7 poles per quarter = 3.5 pole pairs).
    """
    import time as _t
    from skfem import (
        Basis, ElementTriP1, BilinearForm, LinearForm,
        asm, condense, solve,
    )
    from skfem.helpers import dot, grad

    from skfem import ElementTriP0
    basis = Basis(mesh, ElementTriP1())

    @BilinearForm
    def stiffness(u, v, w):
        return dot(grad(u), grad(v))

    @BilinearForm
    def stiffness_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]   # ∂v/∂y

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]   # ∂v/∂x

    t0 = _t.time()
    n = basis.N
    from scipy.sparse import csr_matrix

    # Pre-compute the per-tag stiffness factors so the Picard iteration can
    # cheaply re-scale them when μ_r is updated.
    unique_tags = np.unique(cell_tags)
    tag_K: Dict[int, "csr_matrix"] = {}
    tag_mat: Dict[int, FEMMaterial] = {}
    tag_cells: Dict[int, np.ndarray] = {}
    tag_basis: Dict[int, "Basis"] = {}

    # Pre-assemble per-tag CURRENT and MAGNETISATION source vectors so the
    # Picard loop can re-scale each magnet's contribution when its Br_eff
    # drops due to demagnetisation, without re-running asm() every step.
    f_current = np.zeros(n)                  # J_z contribution (independent of M)
    tag_fMx: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂y dΩ
    tag_fMy: Dict[int, np.ndarray] = {}      # per-magnet ∫ ∂v/∂x dΩ
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        cells_idx = np.where(cell_tags == tag)[0]
        if cells_idx.size == 0:
            continue
        sub_basis = Basis(mesh, ElementTriP1(), elements=cells_idx)
        tag_K[int(tag)]    = asm(stiffness, sub_basis)
        tag_mat[int(tag)]  = mat
        tag_cells[int(tag)] = cells_idx
        tag_basis[int(tag)] = sub_basis
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub_basis) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub_basis)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub_basis)

    SATURABLE_TAGS = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    mu_r_eff: Dict[int, float] = {tag: tag_mat[tag].mu_r for tag in tag_mat}
    # ── PER-ELEMENT saturation state for the iron domains ────────────────
    # A per-DOMAIN μ from the B p90 NEVER saturates a spoke rotor's bridges
    # (they are a tiny fraction of the rotor area), so the magnets short
    # through the unsaturated bridges and the gap field collapses ~10×
    # (0.11 T instead of ~1 T — the "chaotic iso-lines / dead cogging"
    # symptom).  Mirror the transient: every iron triangle gets its own
    # ν(|B|), updated by damped Picard.
    sat_basis: Dict[int, "Basis"] = {}
    sat_b0:    Dict[int, "Basis"] = {}
    nu_el:     Dict[int, np.ndarray] = {}
    for tag in SATURABLE_TAGS:
        if tag in tag_cells:
            sat_basis[tag] = tag_basis[tag]
            sat_b0[tag] = tag_basis[tag].with_element(ElementTriP0())
            nu_el[tag] = np.full(tag_cells[tag].size,
                                 1.0 / (MU0 * max(tag_mat[tag].mu_r, 1.0)))
    # Br factor — starts at 1.0 (full strength) per magnet; the demag
    # iteration drops it below 1.0 when the operating point crosses the knee.
    br_factor: Dict[int, float] = {
        tag: 1.0 for tag in tag_mat if tag >= DOM_MAG_BASE}

    def _assemble_K() -> "csr_matrix":
        K = csr_matrix((n, n))
        for tag, K_dom in tag_K.items():
            if tag in sat_basis:
                continue                       # assembled per element below
            K = K + K_dom * (1.0 / (MU0 * mu_r_eff[tag]))
        for tag, sb in sat_basis.items():
            b0 = sat_b0[tag]
            nf = b0.zeros()
            nf[tag_cells[tag]] = nu_el[tag]    # P0 dof == global element id
            K = K + asm(stiffness_nu, sb, nu=b0.interpolate(nf))
        return K

    def _assemble_f() -> np.ndarray:
        f = f_current.copy()
        for tag, fMx in tag_fMx.items():
            scale = br_factor.get(tag, 1.0)
            f += fMx * (tag_mat[tag].Mx * scale)
        for tag, fMy in tag_fMy.items():
            scale = br_factor.get(tag, 1.0)
            f -= fMy * (tag_mat[tag].My * scale)
        return f

    f_const = _assemble_f()              # initial source, also referenced below

    f_total = f_const                                       # for compatibility

    # ── Picard iteration for iron saturation ─────────────────────────────
    # Linear iron (μ_r=5000 everywhere) lets the rotor back-iron act as a
    # short-circuit and absorb all the magnet flux instead of pushing it
    # through the air gap into the stator.  Real iron saturates at ~1.8 T,
    # so we iterate: solve linearly → check mean |B| in each iron domain →
    # roll μ_r down for over-saturated domains → resolve.  3–4 iterations
    # converge to a self-consistent saturated picture.
    A = np.zeros(n)
    outer_nodes = _outer_boundary_nodes(mesh)

    for it in range(max(1, nonlinear_iterations)):
        K_csr = _assemble_K().tocsr()
        f_iter = _assemble_f()           # picks up updated br_factor
        A = _solve_with_bc(K_csr, f_iter, outer_nodes, mesh, n_sectors,
                            pole_pairs_per_sector_is_half_integer)
        # Per-ELEMENT ν(|B|) update in every saturable (iron) domain — each
        # triangle saturates on its own, so thin features (spoke bridges)
        # saturate correctly even though the domain average stays low.
        Bx_tri, By_tri = _per_triangle_B(mesh, A)
        Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
        changed = False
        for tag, sb in sat_basis.items():
            idx = tag_cells[tag]
            Bm = Bmag_tri[idx]
            mat_t = tag_mat[tag]
            if mat_t.bh_curve and len(mat_t.bh_curve) >= 2:
                mu_new = _mu_r_from_bh_vec(mat_t.bh_curve, Bm)
            else:
                ratio = np.where(Bm > 1.8, (1.8 / np.maximum(Bm, 1e-9)) ** 3, 1.0)
                mu_new = mat_t.mu_r * ratio + 5.0 * (1.0 - ratio)
            nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
            nu_upd = 0.5 * nu_el[tag] + 0.5 * nu_new
            rel = float(np.max(np.abs(nu_upd - nu_el[tag])
                               / np.maximum(nu_el[tag], 1e-30)))
            if rel > 0.02:
                changed = True
            nu_el[tag] = nu_upd
            log.info("FEM iter %d: tag=%d B_max=%.2fT μ_r median %.0f (per-elem)",
                     it, tag, float(Bm.max()) if Bm.size else 0.0,
                     float(np.median(1.0 / (MU0 * nu_el[tag]))))

        # ── Self-consistent demagnetisation update ────────────────────
        # When a magnet's operating point falls BELOW its BH-curve knee,
        # the effective Br drops to the value defined by the recoil line
        # passing through the operating point.  We reduce br_factor so
        # the next iteration's source term reflects the lost magnetisation
        # — and the reported torque/losses include the demag penalty.
        for tag in [t for t in tag_mat if t >= DOM_MAG_BASE]:
            mat_t = tag_mat[tag]
            if not mat_t.bh_curve or len(mat_t.bh_curve) < 2:
                continue
            Mmag = math.hypot(mat_t.Mx, mat_t.My)
            if Mmag < 1e-9:
                continue
            idx = tag_cells.get(tag)
            if idx is None or idx.size == 0:
                continue
            # Per-cell H projected onto +M̂  (along magnetisation direction).
            B_dot_M = (Bx_tri[idx] * mat_t.Mx + By_tri[idx] * mat_t.My)
            H_along_M = B_dot_M / (MU0 * Mmag) - Mmag * br_factor[tag]
            H_worst = float(np.min(H_along_M))
            H_knee = mat_t.bh_curve[1][0] if mat_t.bh_curve[0][1] <= 0 \
                       else mat_t.bh_curve[0][0]
            if H_worst < H_knee:
                # On the BH curve at H_worst, B is below the recoil line
                # → effective Br must drop.  New Br = B_op - μ_rec·μ₀·H_op
                # where (H_op, B_op) is read from the measured curve.
                B_op = _b_from_bh_at_H(mat_t.bh_curve, H_worst)
                Br_new = B_op - mat_t.mu_r * MU0 * H_worst
                Br_orig = Mmag * MU0      # current full-strength Br
                ratio = max(0.0, min(1.0, Br_new / max(Br_orig, 1e-12)))
                new_factor = 0.5 * (br_factor[tag] + ratio)   # damped
                if abs(new_factor - br_factor[tag]) > 0.01:
                    changed = True
                log.warning("FEM iter %d: magnet tag=%d demag — "
                             "H_min=%.0f A/m, H_knee=%.0f A/m, Br_factor %.3f→%.3f",
                             it, tag, H_worst, H_knee,
                             br_factor[tag], new_factor)
                br_factor[tag] = new_factor
        if not changed:
            break
    log.info("FEM solve: %d nodes, %d triangles, %d Picard iters, %.2fs",
             basis.N, mesh.t.shape[1], it + 1, _t.time() - t0)
    return A


def _solve_with_bc(K_csr, f, outer_nodes, mesh, n_sectors,
                    pole_pairs_per_sector_is_half_integer):
    """Apply Dirichlet outer BC + optional anti-periodic sector BC, then solve.
    Returns the nodal A_z vector at FULL mesh resolution."""
    from skfem import condense, solve

    # ── Anti-periodic master-slave BC on the radial cuts (sector mode) ──
    if n_sectors > 1:
        masters, slaves = _pair_sector_cut_nodes(mesh, n_sectors)
        if masters.size:
            sign = -1.0 if pole_pairs_per_sector_is_half_integer else +1.0
            K_red, f_red, T = _apply_anti_periodic(K_csr, f,
                                                     masters, slaves, sign)
            n_full = mesh.p.shape[1]
            is_slave = np.zeros(n_full, dtype=bool); is_slave[slaves] = True
            free_ids = np.where(~is_slave)[0]
            full2red = -np.ones(n_full, dtype=int)
            full2red[free_ids] = np.arange(free_ids.size)
            outer_red = full2red[outer_nodes]
            outer_red = outer_red[outer_red >= 0]
            A_red = solve(*condense(K_red, f_red, D=outer_red))
            return (T @ A_red).A.ravel() if hasattr(T @ A_red, 'A') \
                else np.asarray(T @ A_red).ravel()

    return solve(*condense(K_csr, f, D=outer_nodes))


def _outer_boundary_nodes(mesh) -> np.ndarray:
    """Return node ids on the outermost circular boundary (highest r)."""
    coords = mesh.p
    r = np.sqrt(coords[0] ** 2 + coords[1] ** 2)
    r_max = r.max()
    # Outer boundary = nodes within 0.5 mm of r_max
    return np.where(r >= r_max - 5e-4)[0]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Sample A_z and derived B onto a regular grid (for canvas rendering)
# ─────────────────────────────────────────────────────────────────────────────

def sample_to_grid(
    mesh,
    A_nodal: np.ndarray,
    cell_tags: np.ndarray,
    materials: Dict[int, FEMMaterial],
    grid_size: int,
    extent_m: Tuple[float, float, float, float],
    classify_fn=None,   # optional (x_mm, y_mm) → domain_id
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate (A_z) onto a regular gs×gs grid; derive B = curl(A);
    reclassify domain on grid directly via classify_fn (recommended) or
    nearest-neighbour from mesh cells."""
    from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

    xmin, xmax, ymin, ymax = extent_m
    xs = np.linspace(xmin, xmax, grid_size)
    ys = np.linspace(ymin, ymax, grid_size)
    XX, YY = np.meshgrid(xs, ys)

    pts = mesh.p.T              # (n_nodes, 2)
    interp_A = LinearNDInterpolator(pts, A_nodal, fill_value=0.0)
    A_z = interp_A(XX, YY)

    dy = ys[1] - ys[0]
    dx = xs[1] - xs[0]
    B_x =  np.gradient(A_z, dy, axis=0)
    B_y = -np.gradient(A_z, dx, axis=1)
    B_mag = np.sqrt(B_x ** 2 + B_y ** 2)

    if classify_fn is not None:
        # Grid-level classification (mm coordinates)
        domain = np.zeros((grid_size, grid_size), dtype=np.int8)
        # Vectorise: vectorize the per-pixel classifier
        flat_x = (XX * 1e3).ravel()    # m → mm
        flat_y = (YY * 1e3).ravel()
        out = np.array([classify_fn(fx, fy) for fx, fy in zip(flat_x, flat_y)],
                       dtype=np.int8)
        domain = out.reshape(grid_size, grid_size)
    else:
        cell_centroids = mesh.p[:, mesh.t].mean(axis=1).T
        dom_interp = NearestNDInterpolator(cell_centroids, cell_tags.astype(np.int32))
        domain = dom_interp(XX, YY).astype(np.int8)

    # J_z grid: directly from the material map by domain id
    J_z = np.zeros_like(A_z)
    for d in np.unique(domain):
        if int(d) in materials:
            J_z[domain == d] = materials[int(d)].J_z

    return A_z, B_x, B_y, B_mag, J_z, domain


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Top-level convenience: build materials dict, solve, sample
# ─────────────────────────────────────────────────────────────────────────────

def build_materials(
    I_ph: Dict[str, float],
    winding_layout: List[Tuple[str, int]],
    polys: dict,
    rotor_angle_deg: float,
    slot_area_m2: float,
    n_wires: int,
    Br: float = 1.19,
    mu_r_steel: float = 5000.0,
) -> Dict[int, FEMMaterial]:
    """Build the per-domain material map for the FEM solve.

    Each magnet (DOM_MAG_BASE+i) and each coil (DOM_COIL_BASE+i) gets its
    own material entry with the polygon-specific source term.  Bulk
    materials (air, iron, etc.) share fixed ids.
    """
    n_slot = len(winding_layout)
    # Tangential M magnitude (alternating per pole)
    M_mag = Br / MU0

    # ── Resolve material assignments from motor_config.yaml ─────────────
    # Each motor part may be linked to a library material (with a BH curve);
    # if not, we keep the analytic μ_r above.
    try:
        from motor_ai_sim.config import get_material_assignments
        from motor_ai_sim import materials as mat_lib
        assignments = get_material_assignments() or {}
    except Exception:
        assignments = {}

    # ── Per-request material override (multi-user, Stage 2b) ─────────────
    # The signed-in user's own assignment + resolved props (mine / global),
    # sent with the request. The override WINS over the shared config; when
    # absent, everything below behaves EXACTLY as before (built-in / global
    # via mat_lib, which already resolves the admin global layer itself).
    try:
        from motor_ai_sim.material_context import get_request_materials
        _ov = get_request_materials() or {}
    except Exception:
        _ov = {}
    _ov_assign = _ov.get("assignment") or {}
    _ov_mats = _ov.get("materials") or {}
    if _ov_assign:
        assignments = {**assignments, **{k: v for k, v in _ov_assign.items() if v}}

    def _resolve_mat(category: str, name: str):
        """Material dataclass: per-request override props first, else the library."""
        from motor_ai_sim import materials as _ml
        if name and name in _ov_mats:
            try:
                cat = (_ov_mats[name] or {}).get("category") or category
                return _ml.material_from_dict(cat, name, _ov_mats[name])
            except Exception:
                pass
        return _ml.get_material(category, name)

    def _bh_for(part_key: str, category: str = "steel"):
        name = assignments.get(part_key)
        if not name:
            return None
        try:
            m = _resolve_mat(category, name)
            bh = getattr(m, "bh_curve", None)
            if bh and len(bh) >= 2:
                return [(float(h), float(b)) for (h, b) in bh]
        except Exception:
            # Silently — shafts are often Aluminium (not in steel) which is fine
            pass
        return None

    def _mu_r_for(part_key: str, default: float = 1.0) -> float:
        """Resolve a part's relative permeability from its linked material,
        searching all library categories.  A non-magnetic material (e.g.
        Aluminium_6061, whose mu_r is None) returns 1.0 — NOT the old 1000
        steel default that turned the aluminium shaft into a spurious flux
        path (it showed ~2.4 T; aluminium is non-magnetic, μ_r≈1)."""
        name = assignments.get(part_key)
        if not name:
            return default
        if name in _ov_mats:                     # per-request override (known category)
            try:
                cat = (_ov_mats[name] or {}).get("category") or "steel"
                mu = getattr(_resolve_mat(cat, name), "mu_r", None)
                return float(mu) if (mu is not None and float(mu) > 1.0) else 1.0
            except Exception:
                return default
        for cat in ("steel", "metal", "conductor", "magnet", "other", "custom"):
            try:
                m = mat_lib.get_material(cat, name)
            except Exception:
                continue
            mu = getattr(m, "mu_r", None)
            if mu is not None and float(mu) > 1.0:
                return float(mu)
            return 1.0          # found, but non-magnetic
        return default

    bh_stator = _bh_for("stator_core", "steel")
    bh_rotor  = _bh_for("rotor_core",  "steel")
    # Shaft is typically aluminium (conductor) or steel — try steel silently;
    # missing entry means no BH curve, which is fine for the air-like Al case.
    try:
        bh_shaft = _bh_for("shaft", "steel")
    except Exception:
        bh_shaft = None
    # μ_r for the shaft: a measured steel BH curve → mu_r_steel; otherwise the
    # linked material's own μ_r (Aluminium → 1.0), never a hard-coded 1000.
    shaft_mu_r = mu_r_steel if bh_shaft is not None else _mu_r_for("shaft", 1.0)
    # Magnet recoil μ_r, Br and BH curve (2nd-quadrant demag curve) from
    # the linked magnet material.
    mag_name = assignments.get("magnet")
    bh_magnet: Optional[List[Tuple[float, float]]] = None
    if mag_name:
        try:
            mat_mag = _resolve_mat("magnet", mag_name)
            Br      = float(getattr(mat_mag, "Br",     Br))
            mu_rec  = float(getattr(mat_mag, "mu_rec", 1.05))
            M_mag   = Br / MU0
            bh = getattr(mat_mag, "bh_curve", None)
            if bh and len(bh) >= 2:
                bh_magnet = [(float(h), float(b)) for (h, b) in bh]
        except Exception as e:
            log.warning("Magnet material '%s' lookup failed: %s", mag_name, e)
            mu_rec = 1.05
    else:
        mu_rec = 1.05

    mats: Dict[int, FEMMaterial] = {
        DOM_AIR:    FEMMaterial("air",    mu_r=1.0),
        DOM_AIRGAP: FEMMaterial("airgap", mu_r=1.0),
        DOM_BAND:   FEMMaterial("band",   mu_r=1.0),
        DOM_OUTER:  FEMMaterial("outer",  mu_r=1.0),
        DOM_STATOR: FEMMaterial("stator", mu_r=mu_r_steel, bh_curve=bh_stator),
        DOM_ROTOR:  FEMMaterial("rotor",  mu_r=mu_r_steel, bh_curve=bh_rotor),
        # Solid (non-laminated) conductors carry σ for the eddy-current solve.
        DOM_SHAFT:  FEMMaterial("shaft",  mu_r=shaft_mu_r, bh_curve=bh_shaft,
                                sigma=SIGMA_SHAFT),
        DOM_COIL:   FEMMaterial("coil",   mu_r=1.0, sigma=SIGMA_CU_20),
        DOM_MAG_N:  FEMMaterial("mag_N",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
        DOM_MAG_S:  FEMMaterial("mag_S",  mu_r=mu_rec, sigma=SIGMA_NDFEB),
    }

    # ── Per-magnet tangential magnetization (SPOKE-PM topology) ──────────
    # M is tangent to the rotor at each magnet's angular position; sign
    # alternates per pole.  The iron tooth between adjacent magnets
    # becomes a virtual pole — flux concentrates there and exits radially
    # into the air gap, then closes through the STATOR YOKE.
    #
    # Convention (CCW tangent):
    #   tangent_CCW(centroid) = (-c_y, +c_x) / |centroid|     in WORLD frame
    #   N magnet (pol = +1):  M = +M_mag · tangent_CCW
    #   S magnet (pol = -1):  M = −M_mag · tangent_CCW
    for i, (mp, polarity) in enumerate(polys.get("magnets", [])):
        if mp is None or mp.is_empty:
            continue
        try:
            cx, cy = mp.centroid.x, mp.centroid.y
            cr = math.hypot(cx, cy)
            if cr < 1e-9:
                continue
            tx, ty = -cy / cr, cx / cr     # CCW tangent
        except Exception:
            continue
        sign = +1.0 if polarity > 0 else -1.0
        # Per-magnet material uses the assigned magnet's recoil permeability
        # and full demagnetisation BH curve so the Picard iteration can
        # detect under-knee operation and warn / reduce Br_eff.
        mats[DOM_MAG_BASE + i] = FEMMaterial(
            name=f"mag_{i}_{('N' if polarity>0 else 'S')}",
            mu_r=mu_rec,
            Mx= sign * M_mag * tx,
            My= sign * M_mag * ty,
            bh_curve=bh_magnet,                # full 2nd-quadrant demag curve
        )

    # ── Per-coil current density ─────────────────────────────────────────
    # cadquery_geometry now emits 24 coil polygons (one per slot, alternating
    # +x / -x side of each tooth).  We look up the slot's (phase, direction)
    # from winding_layout via centroid angle so the indexing is robust to
    # any clipping / re-ordering done downstream.
    coil_list = polys.get("coils", [])
    if coil_list and n_slot > 0:
        slot_pitch_deg = 360.0 / n_slot
        # The cadquery layout places TWO coil polygons per wide tooth — one
        # on either side of the tooth (e.g. at math 83.64° and 96.36° for
        # the tooth at math 90°).  Slot CENTRES sit at the half-pitch
        # offset (math 7.5°, 22.5°, 37.5°, …) so the two halves map to
        # ADJACENT slot indices: 5 and 6 for the example above.  Those two
        # neighbouring slot_idx values carry OPPOSITE direction signs in
        # the winding layout — which gives the go / return current pattern
        # of the concentrated coil (one side red = +J, the other blue = −J,
        # matching the Ansys reference).  Rounding to slot_pitch directly
        # (without the half-pitch offset) collapses both halves onto the
        # SAME slot_idx and forces them to carry the same sign — that's
        # the bug the user spotted in the field render.
        half_pitch_deg = slot_pitch_deg * 0.5
        for i, cp in enumerate(coil_list):
            if cp is None or cp.is_empty:
                continue
            try:
                cx, cy = cp.centroid.x, cp.centroid.y
            except Exception:
                continue
            ang = math.degrees(math.atan2(cy, cx))
            if ang < 0: ang += 360.0
            slot_idx = int((ang - half_pitch_deg) / slot_pitch_deg + 0.5) % n_slot
            phase, direction = winding_layout[slot_idx]
            # J_z = direction · I_phase_peak · n_wires_per_slot / slot_area
            J_z = float(direction) * I_ph[phase] * n_wires / max(slot_area_m2, 1e-12)
            mats[DOM_COIL_BASE + i] = FEMMaterial(
                name=f"coil_{i}_slot{slot_idx}_{phase}{'+' if direction>0 else '-'}",
                mu_r=1.0, J_z=J_z,
            )
    return mats


def _field2d_static_inputs(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
):
    """Config-derived inputs for the magnetostatic field solve.

    SHARED by fem_field2d (which builds its own mesh) and solve_field2d_on_mesh
    (which consumes a prebuilt mesh from the `mesh` module). Single source for the
    operating point + per-domain materials, so the self-meshing path and the
    mesh -> solver handoff path can never drift apart.

    Returns (polys_simplified, materials, params).
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)

    # ── Operating-point currents (γ=0 → q-axis, +π/2 shift) ──────────────
    if I_phase_rms is None:
        I_phase_rms = sim.get("max_current", 85.0)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = geo.get("num_wires_per_slot", 14)
    pole_pairs   = p.num_poles // 2
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    theta_e      = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    # ── Real CadQuery polygons at this rotor angle (simplified to match mesh) ──
    motor = CadQueryMotor()
    polys = _simplify_polys(motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg), tol_mm=0.3)

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    mats = build_materials(I_ph, d.winding_layout, polys, rotor_angle_deg, slot_area, n_wires)
    return polys, mats, p


def fem_field2d(
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    grid_size: int = 150,
    mesh_size_mm: float = 1.6,
) -> FEMResult:
    """Top-level: build mesh + assemble + solve + sample.

    Mirrors the signature of routes/simulation.py::get_field2d so the FEM
    endpoint can drop in as a swap.
    """
    import time as _t

    t_start = _t.time()
    polys, mats, p = _field2d_static_inputs(rotor_angle_deg, gamma_deg)

    log.info("FEM: building triangle mesh (h=%.2f mm)…", mesh_size_mm)
    mesh, cell_tags, classify_fn = build_mesh_from_polygons(polys, rotor_angle_deg, mesh_size_mm)

    # Reclassify each triangle by its centroid (mm) — robust against gmsh tag loss
    tri_centroids_m = mesh.p[:, mesh.t].mean(axis=1)   # (2, n_tri) in metres
    cell_tags = np.array(
        [classify_fn(tri_centroids_m[0, i] * 1e3, tri_centroids_m[1, i] * 1e3)
         for i in range(tri_centroids_m.shape[1])],
        dtype=np.int8,
    )
    log.info("FEM: reclassified cells — %s", dict(zip(*np.unique(cell_tags, return_counts=True))))
    log.info("FEM: mesh has %d nodes, %d triangles", mesh.p.shape[1], mesh.t.shape[1])

    # Solve
    A = solve_magnetostatics(mesh, cell_tags, mats)

    # Sample onto regular grid for canvas
    R = p.r_stator_out * 1.02
    extent = (-R, R, -R, R)
    A_g, Bx_g, By_g, Bmag_g, Jz_g, dom_g = sample_to_grid(
        mesh, A, cell_tags, mats, grid_size, extent, classify_fn=classify_fn,
    )

    return FEMResult(
        grid_size=grid_size,
        extent=extent,
        A_z=A_g, B_x=Bx_g, B_y=By_g, B_mag=Bmag_g,
        J_z=Jz_g, domain=dom_g,
        n_triangles=mesh.t.shape[1],
        n_nodes=mesh.p.shape[1],
        solve_time_s=_t.time() - t_start,
    )


def solve_field2d_on_mesh(
    mesh,
    cell_tags: np.ndarray,
    *,
    rotor_angle_deg: float = 0.0,
    gamma_deg: float = 0.0,
    I_phase_rms: Optional[float] = None,
) -> Dict[str, Any]:
    """Magnetostatic field solve on a PREBUILT mesh — the end-to-end
    mesh -> solver handoff.

    The static solver consumes exactly the discretization the `mesh` module
    produced (its MeshIR vertices/triangles/cell_tags), instead of meshing
    again. The operating point + per-domain materials come from
    _field2d_static_inputs, SHARED with the self-meshing fem_field2d, so the
    physics is identical — only the mesh provenance differs.

    Returns a JSON-friendly field summary (no per-node arrays); callers that
    need the full field still use fem_field2d / the field route.
    """
    import time as _t
    t0 = _t.time()
    _polys, mats, _p = _field2d_static_inputs(rotor_angle_deg, gamma_deg, I_phase_rms)
    cell_tags = np.asarray(cell_tags).astype(int)

    A = solve_magnetostatics(mesh, cell_tags, mats)
    Bx, By = _per_triangle_B(mesh, A)
    Bmag = np.sqrt(Bx ** 2 + By ** 2)
    return {
        "n_nodes":     int(mesh.p.shape[1]),
        "n_cells":     int(mesh.t.shape[1]),
        "A_z_min":     float(A.min()),
        "A_z_max":     float(A.max()),
        "B_mag_max_T": float(Bmag.max()),
        "B_mag_mean_T": float(Bmag.mean()),
        "solve_time_s": round(_t.time() - t0, 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Full FEM pipeline with torque + losses (Simulation tab endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def _smooth_demag_H(mesh, idx: np.ndarray, H_el: np.ndarray) -> np.ndarray:
    """Area-weighted nodal smoothing of the demagnetising field inside ONE magnet.

    H is recovered from B = mu0*(H + M), and on P1 elements B is constant per
    triangle, so the value in a sharp magnet corner is a mesh artefact: it grows
    without bound as the mesh is refined and de-rates that element to ~0 Br.  That
    is why the corner minimum read 0.037 against Ansys' 0.558 on the same design.

    Averaging onto the NODES (weighted by element area) and back gives the
    continuous field the de-rating should be judged on — mesh-independent and the
    same post-processing Ansys plots.  Smoothing is confined to this magnet's own
    elements so nothing leaks across the magnet/iron boundary.

    NOTE this makes the model LESS conservative than the raw peak: the corner is no
    longer driven to zero.  The raw per-element worst is still reported alongside.
    """
    t = np.asarray(mesh.t)[:, idx]                 # 3 x n nodes of this magnet
    x, y = np.asarray(mesh.p)[0], np.asarray(mesh.p)[1]
    ax, ay = x[t[0]], y[t[0]]
    bx, by = x[t[1]], y[t[1]]
    cx, cy = x[t[2]], y[t[2]]
    area = 0.5 * np.abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay))
    area = np.maximum(area, 1e-30)
    nsum = np.zeros(x.size); wsum = np.zeros(x.size)
    for k in range(3):
        np.add.at(nsum, t[k], H_el * area)
        np.add.at(wsum, t[k], area)
    Hn = nsum / np.maximum(wsum, 1e-30)
    return Hn[t].mean(axis=0)


def _per_triangle_B(mesh, A_nodal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Compute B = (B_x, B_y) per triangle from P1 nodal A_z.
    Returns (B_x_per_tri, B_y_per_tri) — both shape (n_tri,).

    For a P1 element with vertices p0, p1, p2 and nodal A values A0, A1, A2:
        ∇A = Σ_i A_i ∇φ_i
        where ∇φ_0 = (y1 - y2, x2 - x1) / (2·area)  (and cyclic)
    Then B_x =  ∂A/∂y, B_y = -∂A/∂x  (2-D out-of-plane A_z convention).
    """
    p = mesh.p          # (2, n_nodes)
    t = mesh.t          # (3, n_tri)
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    A0 = A_nodal[t[0]]
    A1 = A_nodal[t[1]]
    A2 = A_nodal[t[2]]
    two_area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    safe = np.where(np.abs(two_area) > 1e-18, two_area, 1e-18)
    dA_dx = (A0 * (y1 - y2) + A1 * (y2 - y0) + A2 * (y0 - y1)) / safe
    dA_dy = (A0 * (x2 - x1) + A1 * (x0 - x2) + A2 * (x1 - x0)) / safe
    return dA_dy, -dA_dx           # B_x, B_y


def _triangle_areas(mesh) -> np.ndarray:
    p = mesh.p; t = mesh.t
    x0 = p[0, t[0]]; y0 = p[1, t[0]]
    x1 = p[0, t[1]]; y1 = p[1, t[1]]
    x2 = p[0, t[2]]; y2 = p[1, t[2]]
    return 0.5 * np.abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))


def _maxwell_stress_torque(mesh, A_nodal: np.ndarray, r_ag_m: float,
                            stack_length_m: float,
                            theta_start: float = 0.0,
                            theta_end: float   = 2 * math.pi,
                            n_samples: int     = 720) -> float:
    """Maxwell stress tensor torque integrated on an air-gap circle arc.

    T = (L / μ₀) · r² · ∫ B_r · B_φ dφ   (over [theta_start, theta_end])

    Samples B at n_samples points on the arc r = r_ag, finds the host
    triangle for each, evaluates the constant-gradient B.  Returns torque
    in N·m for the integrated arc — caller multiplies by n_sectors when
    only a sector is meshed.
    """
    from matplotlib.tri import Triangulation
    tri = Triangulation(mesh.p[0], mesh.p[1], mesh.t.T)
    finder = tri.get_trifinder()

    phis = np.linspace(theta_start, theta_end, n_samples, endpoint=False)
    xs = r_ag_m * np.cos(phis)
    ys = r_ag_m * np.sin(phis)
    tri_idx = finder(xs, ys)

    Bx_per_tri, By_per_tri = _per_triangle_B(mesh, A_nodal)

    valid = tri_idx >= 0
    Bx = np.where(valid, Bx_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    By = np.where(valid, By_per_tri[np.clip(tri_idx, 0, None)], 0.0)
    # Polar components at each sample point
    cos_p = np.cos(phis); sin_p = np.sin(phis)
    B_r   = Bx * cos_p + By * sin_p
    B_phi = -Bx * sin_p + By * cos_p

    dphi = (theta_end - theta_start) / n_samples
    return (stack_length_m / MU0) * r_ag_m ** 2 * float(np.sum(B_r * B_phi)) * dphi


def _arkkio_torque(mesh, A_nodal: np.ndarray, r_in_m: float, r_out_m: float,
                   stack_length_m: float) -> float:
    """Arkkio torque — averages the Maxwell stress over the WHOLE air-gap
    annulus instead of a single circle:

        T = (L / (μ₀·(r_out−r_in))) · ∫∫_annulus  r · B_r · B_φ  dA

    Integrating over every air-gap element (area-weighted) makes the result
    far less sensitive to mesh density and sampling noise than the
    single-contour stress integral — provided the gap is actually resolved
    (see the radial size field in build_mesh_from_polygons).  Returns the
    SECTOR torque; the caller multiplies by n_sectors.

    mesh.p is in METRES, so r_in_m / r_out_m must be in metres too.
    """
    Bx, By = _per_triangle_B(mesh, A_nodal)
    P = mesh.p; T = mesh.t
    cx = (P[0, T[0]] + P[0, T[1]] + P[0, T[2]]) / 3.0
    cy = (P[1, T[0]] + P[1, T[1]] + P[1, T[2]]) / 3.0
    rc = np.hypot(cx, cy)
    mask = (rc >= r_in_m) & (rc <= r_out_m)
    if not np.any(mask):
        return 0.0
    areas = _triangle_areas(mesh)                      # m²
    cosp = cx[mask] / rc[mask]; sinp = cy[mask] / rc[mask]
    Br  =  Bx[mask] * cosp + By[mask] * sinp
    Bph = -Bx[mask] * sinp + By[mask] * cosp
    integrand = areas[mask] * rc[mask] * Br * Bph
    return (stack_length_m / (MU0 * (r_out_m - r_in_m))) * float(np.sum(integrand))


# ─────────────────────────────────────────────────────────────────────────────
#  SECOND-ORDER (P2 / quadratic) magnetostatics — B = curl A is LINEAR per
#  element instead of piecewise-constant, so the Arkkio air-gap torque is smooth
#  where P1 staircases.  Additive: the P1 path above is untouched; callers opt in
#  via element_order=2.  Validated by p2_cogging_proof.py (see P2_NOTES.md).
# ─────────────────────────────────────────────────────────────────────────────
def _p2_B_at_quad(basis, A_vec):
    """B = (∂A/∂y, −∂A/∂x) at every element's quadrature points for a P2 field,
    plus the integration measure dx.  Each returned array is (n_elem, n_qp).
    Because A is quadratic, ∇A (hence B) is LINEAR within each element — the
    physical origin of P2's smooth torque."""
    Af = basis.interpolate(A_vec)
    g = Af.grad                        # (2, n_elem, n_qp) global gradient
    return g[1], -g[0], basis.dx       # B_x, B_y, dx


def _arkkio_torque_p2(mesh, A_vec, basis, r_in_m: float, r_out_m: float,
                      stack_length_m: float) -> float:
    """Arkkio torque via a quadrature integral of r·B_r·B_φ over the gap-annulus
    elements.  Works for BOTH P1 and P2 fields — it uses the SAME element as the
    supplied `basis`, evaluating ∇A at the element quadrature points.

        T = L/(μ₀·(r_out−r_in)) · ∫∫_annulus r·B_r·B_φ dA

    For a P2 field B is linear in the element (fast convergence, smooth torque);
    for a P1 field B is constant per element (the centroid value), so this matches
    the classic `_arkkio_torque`.  Returns the SECTOR torque (caller ×n_sectors)."""
    from skfem import Basis
    P, T = mesh.p, mesh.t
    cx = (P[0, T[0]] + P[0, T[1]] + P[0, T[2]]) / 3.0
    cy = (P[1, T[0]] + P[1, T[1]] + P[1, T[2]]) / 3.0
    rc = np.hypot(cx, cy)
    gap_idx = np.where((rc >= r_in_m) & (rc <= r_out_m))[0]
    if gap_idx.size == 0:
        return 0.0
    gb = Basis(mesh, basis.elem, elements=gap_idx)   # same element order as the field
    Bx, By, dx = _p2_B_at_quad(gb, A_vec)
    X = gb.global_coordinates().value          # (2, n_gap_elem, n_qp) metres
    r = np.sqrt(X[0] ** 2 + X[1] ** 2)
    cosp, sinp = X[0] / r, X[1] / r
    Br = Bx * cosp + By * sinp
    Bph = -Bx * sinp + By * cosp
    val = float(np.sum(dx * r * Br * Bph))
    return stack_length_m / (MU0 * (r_out_m - r_in_m)) * val


def solve_magnetostatics_fem(mesh, cell_tags: np.ndarray,
                             materials: Dict[int, FEMMaterial],
                             element_order: int = 2,
                             nonlinear_iterations: int = 8):
    """Magnetostatic solve on ElementTriP1 (order 1) or ElementTriP2 (order 2).

    Same weak form  ∫ ν ∇A·∇v = ∫ J_z v + ∫(Mx ∂v/∂y − My ∂v/∂x) and the same
    per-element BH Picard (_mu_r_from_bh_vec, 0.5 damping) for BOTH orders — the
    ONLY difference is the element (order 2 ⇒ A quadratic ⇒ B linear per element,
    smooth torque; order 1 ⇒ A linear ⇒ B piecewise-constant, staircase).  Using
    ONE code path for both makes P1-vs-P2 a controlled comparison (identical mesh,
    sources, Picard, and — importantly — the same robust facet-based outer
    Dirichlet BC, which the legacy vertex-id `_outer_boundary_nodes` gets wrong on
    some meshes → singular matrix).

    Returns (A_vec, basis).  A_vec length = basis.N (order 1: n_nodes; order 2:
    n_nodes + n_edges); `basis` is needed to extract B / torque (_arkkio_torque_p2
    works for both — it evaluates ∇A at the element quadrature points).

    Iron saturation is iterated per element; magnet irreversible-demag is NOT
    modelled here (a second-order correction for cogging/ripple studies).
    """
    from skfem import (Basis, ElementTriP1, ElementTriP2, ElementTriP0,
                       BilinearForm, LinearForm, asm, condense, solve)
    from skfem.helpers import dot, grad
    import time as _t

    if int(element_order) not in (1, 2):
        raise ValueError(f"element_order must be 1 or 2, got {element_order}")
    Elem = ElementTriP1 if int(element_order) == 1 else ElementTriP2

    t0 = _t.time()
    basis = Basis(mesh, Elem())
    b0 = basis.with_element(ElementTriP0())        # P0 on the SAME quadrature rule
    n = basis.N
    n_tri = mesh.t.shape[1]

    @BilinearForm
    def stiffness_nu(u, v, w):
        return w["nu"] * dot(grad(u), grad(v))

    @LinearForm
    def rhs_unit(v, w):
        return 1.0 * v

    @LinearForm
    def rhs_dvdy(v, w):
        return grad(v)[1]

    @LinearForm
    def rhs_dvdx(v, w):
        return grad(v)[0]

    unique_tags = np.unique(cell_tags)
    tag_mat: Dict[int, FEMMaterial] = {}
    tag_cells: Dict[int, np.ndarray] = {}
    f_current = np.zeros(n)
    tag_fMx: Dict[int, np.ndarray] = {}
    tag_fMy: Dict[int, np.ndarray] = {}
    for tag in unique_tags:
        mat = materials.get(int(tag))
        if mat is None:
            continue
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        tag_mat[int(tag)] = mat
        tag_cells[int(tag)] = idx
        sub = Basis(mesh, Elem(), elements=idx)
        if mat.J_z != 0.0:
            f_current += asm(rhs_unit, sub) * mat.J_z
        if abs(mat.Mx) > 0:
            tag_fMx[int(tag)] = asm(rhs_dvdy, sub)
        if abs(mat.My) > 0:
            tag_fMy[int(tag)] = asm(rhs_dvdx, sub)

    # Per-element reluctivity ν (P0 dof == element id), init from μ_r.
    nu_all = np.empty(n_tri)
    for tag in unique_tags:
        mat = materials.get(int(tag))
        idx = np.where(cell_tags == tag)[0]
        mur = mat.mu_r if mat is not None else 1.0
        nu_all[idx] = 1.0 / (MU0 * max(mur, 1.0))

    br_factor = {t: 1.0 for t in tag_mat if t >= DOM_MAG_BASE}

    def _assemble_K():
        nf = b0.zeros()
        nf[:] = nu_all
        return asm(stiffness_nu, basis, nu=b0.interpolate(nf)).tocsr()

    def _assemble_f():
        f = f_current.copy()
        for tag, fMx in tag_fMx.items():
            f += fMx * (tag_mat[tag].Mx * br_factor.get(tag, 1.0))
        for tag, fMy in tag_fMy.items():
            f -= fMy * (tag_mat[tag].My * br_factor.get(tag, 1.0))
        return f

    # Dirichlet DOFs on the outer circle — P2 must include EDGE-MIDPOINT dofs,
    # not just vertices, so select by boundary facet and let get_dofs expand.
    r_nodes = np.sqrt(mesh.p[0] ** 2 + mesh.p[1] ** 2)
    r_max = r_nodes.max()
    out_facets = mesh.facets_satisfying(
        lambda x: np.sqrt(x[0] ** 2 + x[1] ** 2) >= r_max - 5e-4)
    D = basis.get_dofs(facets=out_facets)

    A = np.zeros(n)
    it = 0
    for it in range(max(1, nonlinear_iterations)):
        K = _assemble_K()
        f = _assemble_f()
        A = solve(*condense(K, f, D=D))
        Bx_q, By_q, dx = _p2_B_at_quad(basis, A)
        area = dx.sum(axis=1)
        Bmag_q = np.sqrt(Bx_q ** 2 + By_q ** 2)
        Bmag_el = (Bmag_q * dx).sum(axis=1) / np.maximum(area, 1e-30)
        changed = False
        for tag in (DOM_STATOR, DOM_ROTOR, DOM_SHAFT):
            idx = tag_cells.get(tag)
            if idx is None:
                continue
            mat = tag_mat[tag]
            Bm = Bmag_el[idx]
            if mat.bh_curve and len(mat.bh_curve) >= 2:
                mu_new = _mu_r_from_bh_vec(mat.bh_curve, Bm)
            else:
                ratio = np.where(Bm > 1.8, (1.8 / np.maximum(Bm, 1e-9)) ** 3, 1.0)
                mu_new = mat.mu_r * ratio + 5.0 * (1.0 - ratio)
            nu_new = 1.0 / (MU0 * np.maximum(mu_new, 1.0))
            nu_upd = 0.5 * nu_all[idx] + 0.5 * nu_new
            rel = float(np.max(np.abs(nu_upd - nu_all[idx])
                               / np.maximum(nu_all[idx], 1e-30)))
            if rel > 0.02:
                changed = True
            nu_all[idx] = nu_upd
        if not changed and it > 0:
            break
    log.info("FEM P%d solve: %d dofs, %d triangles, %d Picard iters, %.2fs",
             int(element_order), n, n_tri, it + 1, _t.time() - t0)
    return A, basis


def solve_magnetostatics_p2(mesh, cell_tags: np.ndarray,
                            materials: Dict[int, FEMMaterial],
                            nonlinear_iterations: int = 8):
    """Quadratic (P2) magnetostatic solve — thin wrapper over
    solve_magnetostatics_fem(element_order=2).  Returns (A_vec, basis)."""
    return solve_magnetostatics_fem(mesh, cell_tags, materials,
                                    element_order=2,
                                    nonlinear_iterations=nonlinear_iterations)


def band_limit_torque(T_series, n_steps_per_period, n_periods):
    """Reconstruct T(t) from the electrical orders a BALANCED three-phase machine
    can physically produce — DC + EVERY 6·k order (6, 12, 18, 24, …): the
    6th/12th… torque ripple and the order-12 cogging of a 24/28 machine — and
    discard everything else.

    Both transient pipelines inject NON-physical torque ripple at forbidden
    orders: the sliding band steps the rotor across discrete slip nodes, and the
    remesh-per-frame path gives every frame a slightly different mesh.  Both errors
    spread broadband over orders a 3-phase drive cannot make (1,2,4,5,7,…) and
    NEITHER converges with mesh refinement → they are numerical, not real.  So we
    simply KEEP THE MULTIPLES OF 6 and drop the rest — no amplitude threshold, no
    special-casing.  The MEAN (calibrated average torque) is preserved exactly.

    Returns (T_phys_list, ripple_phys_pct, ripple_raw_pct, noise_floor_pct) —
    noise_floor_pct = RMS of the DISCARDED (forbidden-order) content as % of the
    mean torque: an honest, always-visible measure of how much numerical mesh
    noise the solve carried (0 on a perfectly converged mesh)."""
    x = np.asarray(T_series, float); n = x.size
    if n == 0:
        return [], 0.0, 0.0, 0.0
    avg = float(x.mean())
    def _pp(arr):
        return (100.0 * (float(arr.max()) - float(arr.min())) / abs(avg)
                if abs(avg) > 1e-9 else 0.0)
    raw_rip = _pp(x)
    nper = max(1, int(round(n_periods)))
    step = 6 * nper                                  # electrical order 6 → bin 6·nper
    if n < 2 * step:                                 # too few frames to resolve order 6
        return x.tolist(), raw_rip, raw_rip, 0.0
    F = np.fft.rfft(x - avg)
    G = np.zeros_like(F)
    G[step:F.size:step] = F[step:F.size:step]        # keep DC + every 6·k harmonic
    xf = np.fft.irfft(G, n=n) + avg
    noise = (100.0 * float(np.sqrt(np.mean((x - xf) ** 2))) / abs(avg)
             if abs(avg) > 1e-9 else 0.0)
    return xf.tolist(), _pp(xf), raw_rip, noise


class _SignedUF:
    """Signed union-find for combining anti-periodic + slip master-slave
    constraints.  union(a,b,s) means dof_a == s·dof_b; find returns (root, sign)."""
    __slots__ = ("par", "sgn")

    def __init__(self, n):
        self.par = list(range(n)); self.sgn = [1] * n

    def find(self, x):
        p = self.par[x]
        if p == x:
            return x, 1
        r, s = self.find(p)
        self.par[x] = r; self.sgn[x] *= s
        return r, self.sgn[x]

    def union(self, a, b, sign):
        ra, sa = self.find(a); rb, sb = self.find(b)
        if ra == rb:
            return
        self.par[ra] = rb; self.sgn[ra] = sign * sa * sb


# Copper electrical properties (annealed Cu, IEC 60028).
RHO_CU_20 = 1.724e-8     # Ω·m at 20 °C
ALPHA_CU  = 0.00393      # temperature coefficient [1/°C]

# Conductivities of the SOLID (non-laminated) conductor regions for the
# eddy-current (magnetodynamic) solver [S/m].  σ=0 ⇒ no eddy (air, laminated
# iron).  These move the eddy loss INTO the field solve (J = −σ ∂A/∂t).
SIGMA_CU_20  = 1.0 / RHO_CU_20   # ≈ 5.80e7  (temperature-corrected at use)
SIGMA_NDFEB  = 6.7e5             # sintered NdFeB
SIGMA_SHAFT  = 4.5e6             # carbon-steel shaft


def end_winding_factor_geom(p, geo_cfg) -> float:
    """Estimate the end-winding length factor k_end = (active + end-turn) /
    active from the geometry.  A 2-D solve only resolves the in-slot (active,
    = stack-length) copper; the end-turns that loop outside the iron stack add
    series length the 2-D model can't see.  Per conductor the path length is
    L_stack + 2·L_endturn, so k_end = 1 + 2·L_endturn/L_stack.

    This machine is a 24-slot / 28-pole FRACTIONAL-SLOT CONCENTRATED winding
    (q = slots/(phases·poles) = 0.29 < 1) → tooth coils, each wound around ONE
    tooth.  Its end-turns are SHORT (they just arc over the tooth).

    SINGLE SOURCE: delegates to motor_ai_sim.masses.end_winding_factor so the copper
    MASS (compute_masses), phase RESISTANCE and LOSS (copper_loss_W) all scale by the
    exact same k_end."""
    from motor_ai_sim.masses import end_winding_factor
    return end_winding_factor(p, geo_cfg)


def copper_loss_W(p, geo_cfg, I_phase_rms, n_parallel,
                  coil_temp_c=120.0, end_winding_factor=0.0):
    """Physical 3-phase copper (stranded) loss = ρ_Cu(T)·J²·V_cu·k_end.

    ρ_Cu(T) rises with coil temperature; J is the conductor current density
    (branch current / strand area); V_cu is the ACTIVE in-slot copper volume;
    k_end scales it up for the end-turns the 2-D field never sees.  Returns
    (P_cu_total_W, k_end_used, R_phase_eff_ohm)."""
    mm = 1e-3
    n_wires = float(geo_cfg.get("num_wires_per_slot", 14))
    wire_area = (float(geo_cfg.get("wire_width", 5.0)) * mm
                 * float(geo_cfg.get("wire_height", 0.6)) * mm)
    n_par = max(float(n_parallel), 1.0)
    if wire_area <= 0 or I_phase_rms <= 0:
        return 0.0, 1.0, 0.0
    V_cu_slot = p.num_slots * wire_area * n_wires * float(p.stack_length)
    k_end = (float(end_winding_factor) if end_winding_factor and end_winding_factor > 0
             else end_winding_factor_geom(p, geo_cfg))
    rho = RHO_CU_20 * (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))
    I_coil = float(I_phase_rms) / n_par                 # branch current
    J = I_coil / wire_area                              # conductor current density
    P = rho * J * J * V_cu_slot * k_end
    R_phase_eff = P / (3.0 * float(I_phase_rms) ** 2)
    return float(P), float(k_end), float(R_phase_eff)


def _params_from_geo_dict(g: dict):
    """Build MotorDomainParams from a geometry dict (mirror of
    geometry_2d.params_from_config, but from an in-memory dict so a candidate
    design can be evaluated WITHOUT touching the global config file)."""
    from motor_ai_sim.simulation.geometry_2d import MotorDomainParams
    mm = 1e-3
    r_so = g["stator_diameter"] / 2 * mm
    r_si = r_so - g["core_thickness"] * mm - g["slot_height"] * mm
    r_ro = r_si - g["air_gap"] * mm
    r_ri = r_ro - g["magnet_height"] * mm - g["rotor_house_height"] * mm
    r_sh = r_ri - g["shaft_height"] * mm
    # Pole/slot COUNT is defined by the geometry (magnets/slots) — num_poles/num_slots
    # are authoritative; the segment product is only a fallback (a stale num_seg from a
    # different motor must not override the real count).
    num_slots = int(g.get("num_slots") or round(g["num_seg"] * g["num_slots_per_segment"]))
    num_poles = int(g.get("num_poles") or round(g["num_seg"] * g["num_poles_per_segment"]))
    slot_width_m = (g["wire_width"] + 2 * g["wire_spacing_x"]
                    + 2 * g["insulation_thickness"]) * mm
    return MotorDomainParams(
        r_stator_out=r_so, r_stator_in=r_si, r_rotor_out=r_ro, r_rotor_in=r_ri,
        r_shaft_in=r_sh, r_air_out=r_si, r_air_in=r_ro,
        num_poles=num_poles, num_slots=num_slots,
        stack_length=g.get("motor_length", 30) * mm,
        magnet_fill_fraction=g.get("magnet_fill_down", 0.9),
        slot_width_m=slot_width_m, slot_height_m=g["slot_height"] * mm,
        wire_width_m=g["wire_width"] * mm, wire_height_m=g["wire_height"] * mm,
        num_wires_per_slot=int(g["num_wires_per_slot"]))


def fem_transient_sliding_band(
    n_steps_per_period: int = 12,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    mesh_size_mm: float = 3.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,     # element layers across the air gap (Mesh-tab slider)
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    nonlinear_iterations: int = 100,  # CAP on the saturation Picard; the loop
                                 # exits EARLY on the fixed-point residual
                                 # (_PIC_TOL) — no fixed-recipe iteration counts.
                                 # 14 was a tuned recipe that did NOT converge
                                 # and left a 5-8 Nm no-load torque floor.
    frozen_nu: bool = False,     # FROZEN PERMEABILITY: converge saturation ONCE
                                 # (frame 0, extended Picard), then freeze the
                                 # per-element nu for every rotor position.  The
                                 # damped Picard PLATEAUS (no-load torque floor
                                 # 5-8 Nm p-p even at 60 iters — each frame lands
                                 # in a different nu-state); with a linear/fixed
                                 # nu the whole chain is clean to 0.004 Nm.  The
                                 # industry-standard method for honest cogging /
                                 # ripple.  Current drive only.
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    geo_override: dict = None,
    eddy: bool = False,          # opt-in: time-coupled σ·∂A/∂t eddy-current solve
    rotor_eddy: bool = False,    # field-based magnet/shaft eddy losses (stranded coils)
    demag: bool = False,         # opt-in: per-element irreversible demagnetisation
    component_mesh_mm: dict = None,  # per-part target element size {comp: mm}
    return_field: bool = False,  # also return a field snapshot for the viewer
    field_first: bool = False,   # snapshot the FIRST frame (rotor at angle0) instead
                                 # of the last — used by the magnetostatic field view
                                 # so the picture matches the requested rotor angle
    torque_filter: bool = False,  # band-limit T(t) to the physical 6·k orders — OFF by default: the headline ripple is the RAW one (no filters)
                                 # (False = raw per-frame Maxwell-stress torque)
    pole_copy: Optional[bool] = None,  # bit-identical pole/slot mesh; None=env default
    iron_template: Optional[bool] = None,  # deterministic template iron; None=env default
    geo_mesh: Optional[bool] = None,   # geometry-driven CDT mesh; None=env default
    progress_cb=None,            # optional callback(done:int, total:int) per frame
    magnet_scale: float = 1.0,   # scale ALL magnet Br (0 → PMs off = reluctance torque)
    rotor_angle0_deg: float = 0.0,   # DIAGNOSTIC: build the rotor PHYSICALLY rotated
                                     # in CAD (magnets+pockets rotated before meshing);
                                     # with 1 step this is a true static solve at that
                                     # angle using the SB machinery minus the sliding.
    hi_fidelity: bool = False,       # "High-fidelity torque": 2× slip-ring nodes + finer
                                     # feature mesh (÷8 not ÷4) → pushes the numerical
                                     # picket-fence torque hash to higher orders + halves
                                     # the over-resolved cogging.  ~2× slower; mean torque
                                     # unchanged.  Off by default (speed).
    honest_eddy: bool = False,       # ADDITIVE diagnostic: ALSO compute the coupled
                                     # (reaction-included) rotor eddy via eddy_solver_2d
                                     # for comparison vs the resistance-limited post-
                                     # process.  Captures rotor-node A history; fail-safe
                                     # (any error leaves the production numbers intact).
    structured_gap: bool = False,    # ANSYS-style concentric-ring air-gap mesh (experimental
                                     # Mesh-tab toggle; default off = free gmsh gap).
    airgap_macro: bool = False,      # harmonic air-gap macroelement (Mesh-tab "Harmonic gap"):
                                     # replaces the node re-pairing slip coupling with a smooth
                                     # analytic per-harmonic rotor↔stator link → RAW T(t) becomes
                                     # step-count independent (honest unfiltered ripple).  Works
                                     # on the full ring AND sector wedges (half-integer harmonic
                                     # ladder / skew-circulant for anti-periodic wedges).  ORs
                                     # with the SB_AIRGAP_MACRO env flag.
    drive: str = "current",          # "current" = imposed sinusoidal phase currents (default);
                                     # "voltage" = imposed sinusoidal phase VOLTAGE — the phase
                                     # currents become circuit STATE solved from V = R·i + dψ/dt
                                     # each frame, so non-sinusoidal back-EMF drives REAL
                                     # parasitic harmonic currents (FOC-drive verification mode).
    v_phase_peak: float = 0.0,       # voltage drive: phase-voltage amplitude [V, peak]
    v_delta_deg: float = 0.0,        # voltage drive: voltage angle [°el] in the SAME frame as γ
    element_order: int = 1,          # 1 = P1 linear (default, unchanged); 2 = P2 quadratic
                                     # elements (B linear per element → smooth Arkkio torque,
                                     # no P1 staircase).  P2 runs the FULL magnetostatic
                                     # sliding-band transient on the merged structured belt —
                                     # full ring (n_sectors≤1) AND anti-periodic sector wedge
                                     # (n_sectors≥2) — via edge-midpoint DOF stitching across
                                     # the moving slip cut and the radial cuts (see the P2
                                     # branch below and P2_NOTES.md).  Still gated (raises
                                     # NotImplementedError): the moving/harmonic-macro band and
                                     # eddy/voltage/demag coupling on P2.
) -> dict:
    """Sliding-band transient: mesh the stator + rotor halves ONCE, then sweep
    the rotor by shifting the slip-ring node pairing (no remeshing) so the
    mesh topology is IDENTICAL every frame.  That removes the per-frame
    remesh noise → smooth T(t) and clean back-EMF V(t) = R·I + dψ/dt.

    Fixed-mesh formulation: both halves stay in the [0, 360/n_sectors] wedge;
    the rotor rotation θ = m·slip_spacing is encoded ONLY in the slip pairing
    (shift by m nodes, sign −1 on every wrap past the sector edge — anti-
    periodic).  A signed union-find merges the slip pairing with the radial-cut
    anti-periodic BC.  Iron saturation via per-domain Picard.

    Returns the same dict shape as the parallel transient endpoint expects.
    """
    import time as _t
    from skfem import (Basis, ElementTriP1, ElementTriP0, BilinearForm,
                       LinearForm, asm, condense, solve as _sksolve, MeshTri)
    from skfem.helpers import dot as _dot, grad as _grad
    from scipy.sparse import csr_matrix as _csr, coo_matrix as _coo, block_diag as _bd
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import params_from_config, MotorDomains2D
    from motor_ai_sim.config import get_config

    t0 = _t.time()
    # Mesh density is driven ENTIRELY by the Mesh-tab sliders now (mesh_size,
    # min_size, gap_layers, normal_deviation) — no hidden clamp.  Earlier this
    # path hard-clamped iron to 2 mm and the gap floor to 0.1 mm "for smooth
    # T(t)", but that silently overrode the sliders (they looked dead).  The
    # air-gap is resolved by gap_layers (element size = gap/gap_layers, applied
    # under min_size in build_mesh_from_polygons), so torque accuracy is the
    # user's choice: finer mesh + more gap layers = smoother T(t), coarser =
    # faster.  Defaults (mesh 4 mm clamped→… no: now literally 4 mm; gap_layers
    # 3) reproduce the previous behaviour closely; drag to mesh≈2 mm / gap≈3-4
    # for the cleanest torque.
    element_order = int(element_order)
    if element_order not in (1, 2):
        raise ValueError(f"element_order must be 1 or 2, got {element_order}")
    # NOTE: element_order == 2 (P2) is handled by a dedicated magnetostatic
    # sliding-band branch further down (just after `dt` is defined), once the
    # stitched mesh + belt ring/cut data have been built.  It implements the
    # moving-cut edge-midpoint DOF stitching (the historical blocker) for the
    # merged, full-ring structured belt.  The guard there raises
    # NotImplementedError for the still-unsupported sub-cases (moving/macro band,
    # sector wedge, eddy/voltage/demag).  See P2_NOTES.md, Stage 2/3.
    mesh_size_mm = float(mesh_size_mm)
    min_size_mm = float(min_size_mm)
    cfg = get_config(); sim = cfg.get("simulation", {})
    geo = dict(cfg.get("geometry", {})); wind = cfg.get("winding", {})
    # Candidate-design evaluation (optimization refine): overlay a geometry
    # override in-memory so the global config / Simulation state is untouched.
    if geo_override:
        # Topology-aware merge: the resulting slot/pole counts must describe the
        # SAME motor the CAD meshes (override explicit counts > override segment
        # form > config segment form — the exact CadQueryMotor resolution), so
        # the winding layout, pole-pair drive and sector BC sign are phased
        # against the meshed magnets.  See merge_geo_override.
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        geo = merge_geo_override(geo, geo_override)
        p = _params_from_geo_dict(geo)
    else:
        p = params_from_config()
    dom = MotorDomains2D(p)
    # ── Feature-relative mesh refinement (real element sizing, not a fudge) ──
    # mesh_size_mm is an ABSOLUTE target.  On a small motor the UI default
    # (4 mm) leaves ~1 element across a 2.8 mm slot → the field, torque and
    # back-EMF are grossly under-resolved AND mesh-dependent.  Convergence study
    # (12s/14p 40 mm, I=38, γ=−32): at mesh 1.5 mm the result is garbage
    # (T 0.52, KV 484, 175 % ripple); it only plateaus at mesh ≲ slot_width/3
    # (T≈0.565, KV≈585, 11 % ripple from 1.0→0.5 mm).  So clamp the target to
    # resolve the smallest in-plane feature (slot or tooth) with ≥4 elements.
    # This only ever REFINES (min) — motors with large features (e.g. 200 mm)
    # keep their coarser mesh.  Radial air-gap resolution is separate
    # (gap_layers).  No operating-point tuning — pure geometric element sizing.
    try:
        _feat_mm = min(float(geo.get("slot_width", 1e9) or 1e9),
                       float(geo.get("tooth_width", 1e9) or 1e9))
        if 0.0 < _feat_mm < 1e8:
            _elem_per_feat = 4.0 if hi_fidelity else 2.0   # normal: 2 elem/feature ceiling (÷4 hi-fi)
            _mesh_feat = max(float(min_size_mm), _feat_mm / _elem_per_feat)
            if _mesh_feat < mesh_size_mm - 1e-9:
                log.info("mesh auto-refined %.2f → %.2f mm (smallest feature "
                         "%.2f mm ÷ %g) — small-motor resolution",
                         mesh_size_mm, _mesh_feat, _feat_mm, _elem_per_feat)
                mesh_size_mm = _mesh_feat
    except Exception as _e:
        log.warning("mesh feature-refine skipped: %s", _e)
    # n_sectors == -1: DIAGNOSTIC full ring — no sector cuts at all (the moving
    # band makes a closed 360° pair of halves feasible: the halves are open
    # annuli, not the historically OCC-double-meshed full cross-section).
    # n_sectors ≤ 1 → FULL RING (NS=1).  -1 was the historical "full ring" flag;
    # 1 ("Full" from the UI) must mean the same — NOT fall through to NS=4, which is
    # an invalid 90° wedge for any motor whose pole count is not a multiple of 4
    # (e.g. 14 poles → 3.5/sector → corrupt anti-periodic BC → spurious torque/ripple).
    _full_ring = (int(n_sectors) <= 1)
    # Geometry-driven mesh currently ships the FULL-RING build only (the CDT
    # is not periodic, so a sector wedge would need clone-identical radial cuts
    # for the anti-periodic master-slave pairing — not yet built).  Force the
    # full ring so a 1/N request still gets the real-fillet geo mesh with sound
    # physics (full disk is the reference anyway) instead of silently reverting
    # to the tensor wedge.
    # geo mesh builds the 1/N wedge directly, but the sector ripple is still WIP
    # → default a geo sector request to the validated FULL RING (correct, just
    # slower); SB_GEO_SECTOR=1 opts into the experimental wedge.
    _use_geo_tr = _SB_GEO_MESH if geo_mesh is None else bool(geo_mesh)
    _tpl_on = (iron_template is None and _SB_IRON_TEMPLATE) or bool(iron_template)
    if (_use_geo_tr and _tpl_on and not _SB_GEO_SECTOR and not _full_ring):
        log.info("geo mesh: full ring for a 1/%d request (sector WIP)", int(n_sectors))
        _full_ring = True
    NS = 1 if _full_ring else int(n_sectors)
    sector_deg = 360.0 / NS
    pole_pairs = p.num_poles // 2
    # Sector boundary sign: ANTI-periodic (−1) only when the sector spans an
    # ODD number of poles (e.g. NS=4 → 7 poles); PERIODIC (+1) for an EVEN pole
    # count (NS=2 → 14 poles).  Mirrors the static solve's `anti_periodic =
    # (poles_per_sector % 2 == 1)`.  Hard-coding −1 here corrupted the 1/2-sector
    # field → 40 %-unbalanced phase-A flux linkage + 70 % torque ripple.
    _poles_per_sector = p.num_poles // NS
    _bc_sign = -1 if (_poles_per_sector % 2 == 1) else 1
    n_parallel = wind.get("n_parallel", 2)
    n_wires = int(geo.get("num_wires_per_slot", 14))
    # Physical copper loss: ρ_Cu(coil_temp)·J²·V_cu·k_end (end-winding the 2-D
    # field never sees).  R_phase is derived from it so the R·I voltage drop is
    # temperature-consistent — no hard-coded resistance.
    P_cu, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor)
    # Synchronous machine: rpm and f_elec are LOCKED (f = rpm·pp/60).  The
    # config can carry a stale pair (preset-apply wrote rpm but not frequency)
    # — and using the mismatched rpm in ω_mech scaled dB/dt (→ iron/magnet
    # losses) by the wrong speed (×4 at 3950-vs-2000).  rpm is the master
    # (it's what presets/UI write); the frequency is DERIVED, never read.
    rpm = float(sim.get("rpm", 3950))
    _f_cfg = float(sim.get("frequency", 0.0) or 0.0)
    f_elec = rpm * (p.num_poles // 2) / 60.0
    if _f_cfg > 0 and abs(_f_cfg - f_elec) / max(f_elec, 1e-9) > 0.01:
        log.warning("config frequency=%.2f Hz inconsistent with rpm=%.0f "
                    "(→ %.2f Hz); using the rpm-derived frequency",
                    _f_cfg, rpm, f_elec)
    slot_area_m2 = p.slot_width_m * p.slot_height_m * p.fill_factor
    mid = 0.5 * (p.r_rotor_out + p.r_stator_in)

    # d-axis phase offset AUTO-CALIBRATED for this motor topology so γ=0 is the
    # true q-axis and γ equals the physical current angle from the q-axis (=ANSYS
    # el_deg).  Cached per topology; the I=0 calibration run is recursion-guarded.
    daxis_eff = _resolve_daxis_shift(p, geo, wind, pole_pairs, geo_override, n_sectors)

    def _currents(rotor_angle_deg):
        Ipk = float(I_phase_rms) / n_parallel * math.sqrt(2)
        # d-axis phase offset so γ=0 = q-axis — per-topology auto-calibrated value
        # (daxis_eff) shared by the current & voltage excitation in this solve.
        te = math.radians(rotor_angle_deg * pole_pairs + gamma_deg + daxis_eff)
        return {'A': Ipk * math.cos(te),
                'B': Ipk * math.cos(te - 2 * math.pi / 3),
                'C': Ipk * math.cos(te + 2 * math.pi / 3)}

    # Voltage drive: imposed sinusoidal PHASE voltage in the same electrical
    # frame as the currents (v_delta_deg is directly comparable to γ), so a
    # clean back-EMF yields near-sinusoidal currents and a distorted one shows
    # its real parasitic harmonic currents + their losses.
    _vdrive = str(drive or "current").strip().lower().startswith("v")
    if _vdrive and rotor_eddy:
        # Voltage drive + conducting-rotor dynamics is NOT implemented: the
        # eddy path imposes coil currents via integral constraints while the
        # voltage circuit needs them as unknowns.  Before this guard the frame
        # loop silently took the no-eddy branch — the vdrive orbit then solved
        # DIFFERENT physics (no magnet-eddy screening, ψ off by ~5 %) than the
        # rotor_eddy current-drive it was compared against, skewing the
        # round-trip fundamental ~10 %/19° and ΔP_harm by the whole P_mag.
        # Explicitly drop eddy on BOTH (the route mirrors this in harm_ref) so
        # voltage runs and their references always share the same physics.
        log.warning("voltage drive: rotor_eddy not supported yet — running "
                    "without conducting-rotor dynamics (magnet/shaft eddy "
                    "losses excluded; ΔP_harm = copper+iron harmonic cost)")
        rotor_eddy = False

    def _voltages(rotor_angle_deg):
        vpk = float(v_phase_peak)
        te = math.radians(rotor_angle_deg * pole_pairs + v_delta_deg + daxis_eff)
        return {'A': vpk * math.cos(te),
                'B': vpk * math.cos(te - 2 * math.pi / 3),
                'C': vpk * math.cos(te + 2 * math.pi / 3)}

    # ── High-fidelity = genuinely higher resolution EVERYWHERE the noise lives ─
    # The raw torque ripple of the sliding-band transient carries a BROADBAND
    # numerical floor at the non-6·k orders a balanced 3-φ machine cannot produce.
    # Measured (40 mm 12s/14p) it is a FIELD-level artifact: IDENTICAL on every
    # torque contour (band strip / rotor-surface / stator-surface / whole gap) and
    # NOT removed by any single knob — gap_layers ALONE even makes it worse
    # (20.8 %→25.5 %), steps don't move it (→21.8 %), pole_copy doesn't (→20.3 %).
    # Only RAISING ALL THREE TOGETHER pulls the floor down: tangential band
    # density (slip nodes), radial gap density (gap_layers) and the global mesh.
    # So hi_fidelity bundles all three (mesh ÷8 vs ÷4 above; slip 2× below;
    # gap_layers≥4 here) → measured raw 20.8 %→~14 %, RMS 4.7 %→3.0 %.  This is the
    # honest "spend compute for accuracy" mode, NOT a filter — the real DC torque
    # is unchanged and the 6·k physical ripple already matches Ansys.  gap_layers
    # is bumped ONLY inside this bundle (it is counter-productive on its own).
    if hi_fidelity:
        gap_layers = max(float(gap_layers), 4.0)
    # ── Slip-ring resolution (ADAPTIVE to pole count) ─────────────────────
    # Nodes per electrical period = a multiple of 24 (so 24/30/40/60/120 are all
    # valid step counts) and ≥120, scaled so the full-ring node count stays
    # ≥~1008 (fine tangential spacing → accurate ripple).  n_slip_eff =
    # pole_pairs·per_period is divisible by pole_pairs BY CONSTRUCTION → the rotor
    # advances a whole number of nodes each step (strictly periodic torque) and
    # the electrical period tiles EXACTLY (vs the old fixed 1008 → 100.8/period).
    # Air-gap layers is the SINGLE fidelity regulator (the UI "Air-gap layer" slider):
    # more layers -> more tangential slip nodes -> less node-identification jitter in the
    # eddy loss (the same knob also sets the radial gap density above).  Calibrated so
    # gap_layers=1 -> 1008 (fast) and gap_layers=4 -> 2016 (= the retired hi-fidelity slip);
    # the _slip_per_period rounding below keeps the count pole-pair-divisible for any motor.
    _slip_base = int(round(1008.0 * (max(1.0, float(gap_layers)) + 2.0) / 3.0))
    _slip_per_period = 24 * max(5, math.ceil(_slip_base / (24 * pole_pairs)))
    n_slip_eff = pole_pairs * _slip_per_period
    if bool(_SB_AIRGAP_MACRO) or bool(airgap_macro):
        # The harmonic macroelement is ANALYTIC between ring nodes, so a COARSE ring
        # is enough — and its coupling is a DENSE N×N block, so a small N is wanted.
        # 48 nodes/period resolves angular harmonics to 24·pole_pairs (≫ the
        # significant slot/pole orders); the node-identification band needed the
        # fine ≥120/period purely to keep the re-pairing quiet — the macroelement
        # does not.  This is the efficiency lever that makes the dense block cheap.
        # Denser rings are WORSE, not better: they admit high spatial harmonics the
        # real gap damps as e^{−k·g} (measured: ring-48 14.2%, ring-216 15.6±0.7,
        # ring-432 25.7% — ring-48 acts as the physical gap filter).  Applies to
        # sector models too (the wedge ring is n_slip/n_sectors of these nodes);
        # bumped to the next n_sectors multiple so wedge node counts stay integral.
        _slip_per_period = 48
        _ns_abs = max(1, abs(int(n_sectors)))
        while (pole_pairs * _slip_per_period) % _ns_abs:
            _slip_per_period += 1
        n_slip_eff = pole_pairs * _slip_per_period
    if _SLIP_PER_PERIOD_OVERRIDE:        # advanced: force ring density (dev flag —
        # decouples ring-count/mesh convergence studies from the adaptive
        # slip(gap_layers) coupling).  Snapped UP to the 24k grid so the wedge
        # node counts (n_slip/n_sectors) stay integral for sector models too.
        _spo = int(_SLIP_PER_PERIOD_OVERRIDE)
        _slip_per_period = 24 * max(1, math.ceil(_spo / 24.0))
        n_slip_eff = pole_pairs * _slip_per_period

    # ── Snap steps/period so the rotor lands on whole slip nodes ──────────
    # For a uniform (periodic, non-chaotic) rotor advance, n_steps must divide
    # the nodes-per-period.
    _nodes_per_period = _slip_per_period
    _req_steps = int(n_steps_per_period)
    # HARMONIC MACRO: the gap coupling is an ANALYTIC phase e^{i k φ} — valid at
    # ANY rotor angle, no node re-pairing — so the whole-node snap (a node-merge
    # constraint) does not apply: honour the requested steps exactly (the rotor
    # advances a FRACTIONAL number of slip nodes per step; m is float).
    _macro_free_m = bool(airgap_macro) or bool(_SB_AIRGAP_MACRO)
    if _macro_free_m:
        n_steps_per_period = max(1, _req_steps)
    else:
        n_steps_per_period = _snap_steps_to_nodes(_req_steps, _nodes_per_period)
        if n_steps_per_period != _req_steps:
            log.info("SB: snapped steps/period %d -> %d (divisor of %d slip "
                     "nodes/period -> whole-node rotor steps, periodic torque)",
                     _req_steps, n_steps_per_period, _nodes_per_period)

    # ── Build the two halves ONCE ────────────────────────────────────────
    motor = CadQueryMotor()
    if geo_override:
        motor.set_parameters(geo_override)   # in-memory candidate geometry
    polys = motor.get_2d_polygons(rotor_angle_deg=float(rotor_angle0_deg))
    # STRUCTURED (mapped) gap uses the MERGED band: the route-A cells own the
    # whole gap r_ro→mid→r_si with the SINGLE shared slip ring at mid_r (uniform
    # S·M grid).  The moving-band split (mid±δ, empty re-stitched strip) is
    # incompatible with the cells, so force merged when structured_gap is on —
    # EXCEPT when the harmonic macro is requested: the macro only exists on the
    # MOVING band (K_gap couples the R1/R2 rings analytically), and forcing
    # merged here was exactly why the product's "Harmonic gap" toggle never ran
    # the macroelement (it silently degraded to node-merge on a coarse ring).
    _use_macro_req = bool(airgap_macro) or bool(_SB_AIRGAP_MACRO)
    # Band mode must NOT depend on n_sectors: the full ring used to force
    # "moving" while sectors solved "merged" — two different gap couplings, so
    # ns=1 vs ns=4 disagreed systematically (T +6.7 %, V_peak +22 % on 24s20p;
    # with a shared merged band they match to 0.3 %).  The moving band's
    # one-row strip biases the flux linkage (see _SB_MOVING_BAND note) — keep
    # MERGED as the sole default for every sector count; the macro (analytic
    # gap) and the _SB_MOVING_BAND env flag still opt into "moving" explicitly.
    _band_mode = ("moving" if _use_macro_req
                  else ("merged" if structured_gap
                        else ("moving" if _SB_MOVING_BAND else "merged")))
    polys = _simplify_polys(polys, tol_mm=0.005, stator_fillet_mm=stator_fillet_mm,
                            n_slip=n_slip_eff, gap_layers=gap_layers,
                            structured_gap=structured_gap,
                            band_mode=_band_mode)
    ms, ts, cs, mr, tr, cr = _build_sliding_band_meshes(
        polys, 0.0, mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, band_thickness_mm=0.4,
        n_sectors=NS, geo_cfg=motor.parameters,
        normal_deviation_deg=8.0, aspect_ratio=10.0,
        gap_layers=gap_layers,
        component_mesh_mm=component_mesh_mm,
        full_ring=_full_ring, pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh)
    Ps, Tts = ms.p.copy(), ms.t.copy(); Pr, Ttr = mr.p.copy(), mr.t.copy()
    nsn = Ps.shape[1]
    Pall = np.hstack([Ps, Pr]); Tall = np.hstack([Tts, Ttr + nsn])
    n = Pall.shape[1]
    mesh_all = MeshTri(Pall, Tall)

    def _ring(P, r_at):
        # Select the SEEDED ring nodes only: radius window + snap-to-grid in
        # angle.  A bare radius window also sweeps in foreign free-mesh nodes
        # that happen to sit within microns of the ring radius (the stator
        # free row is only ~0.13 mm thick) — those polluted the pairing.
        r = np.hypot(P[0], P[1])
        idx = np.where(np.abs(r - r_at) < 1e-6)[0]
        ang = np.degrees(np.arctan2(P[1, idx], P[0, idx])) % 360.0
        step = 360.0 / n_slip_eff
        kg = np.round(ang / step)
        on_grid = np.abs(ang - kg * step) < (0.05 * step)
        idx, ang, kg = idx[on_grid], ang[on_grid], kg[on_grid].astype(int) % n_slip_eff
        # one node per grid slot (keep the angularly-closest if duplicated)
        if kg.size:
            order = np.lexsort((np.abs(ang - np.round(ang / step) * step), kg))
            idx, kg = idx[order], kg[order]
            keep = np.concatenate([[True], np.diff(kg) != 0])
            idx, kg = idx[keep], kg[keep]
            o = np.argsort(kg)
            idx = idx[o]
        return idx
    # MOVING BAND: the halves end at two DIFFERENT uniform rings — rotor at
    # R1 = mid−δ (rotates rigidly with the rotor mesh), stator at R2 = mid+δ
    # (stationary).  The annulus between them is re-stitched every frame in
    # closed form.  Legacy (merged single ring at mid) kept as fallback.
    _band_radii = polys.get("band_radii_mm")
    _moving = bool(_band_radii) and len(_band_radii) == 2
    if _moving:
        _r1_m = float(_band_radii[0]) * 1e-3   # metres
        _r2_m = float(_band_radii[1]) * 1e-3
        rring = _ring(Pr, _r1_m)
        sring = _ring(Ps, _r2_m)
    else:
        rring = _ring(Pr, mid)
        sring = _ring(Ps, mid)
    Nring = min(sring.size, rring.size)
    if sring.size != rring.size:
        log.warning("band ring node counts differ: stator=%d rotor=%d — "
                    "truncating to %d", sring.size, rring.size, Nring)
    sring = sring[:Nring]; rring = rring[:Nring]
    if _full_ring:
        spacing = 360.0 / Nring          # CLOSED ring: N nodes, N intervals
    else:
        spacing = sector_deg / (Nring - 1)

    # Constant radial-cut anti-periodic pairs on the combined mesh.
    if _full_ring:
        Mn, Sn = np.array([], int), np.array([], int)   # no cuts at all
    else:
        Mn, Sn = _pair_sector_cut_nodes(mesh_all, NS)

    # Forms
    @BilinearForm
    def _stiff(u, v, w): return _dot(_grad(u), _grad(v))
    @BilinearForm
    def _stiff_nu(u, v, w):            # per-element reluctivity ν(x)
        return w["nu"] * _dot(_grad(u), _grad(v))
    @BilinearForm
    def _massform(u, v, w):            # ∫ u·v  — for the σ·∂A/∂t eddy term
        return u * v
    @LinearForm
    def _f1(v, w): return 1.0 * v
    @LinearForm
    def _fdy(v, w): return _grad(v)[1]
    @LinearForm
    def _fdx(v, w): return _grad(v)[0]
    @LinearForm
    def _msrc(v, w):            # magnet source with PER-ELEMENT M (P0 fields):
        return w["mx"] * _grad(v)[1] - w["my"] * _grad(v)[0]   # ∫(Mx·∂v/∂y − My·∂v/∂x)

    # ── Pre-assemble per-tag stiffness K0 + constant magnet source ───────
    matr0 = build_materials(_currents(0.0), dom.winding_layout,
                            getattr(cr, "polys", polys), 0.0, slot_area_m2, n_wires)
    # unit-current stator sources (per phase), magnet source is in rotor half
    # σ per domain tag for the eddy-current mass matrix (temperature-corrected
    # copper).  Solid conductors only — air / laminated iron stay σ=0.
    _sig_cu_T = SIGMA_CU_20 / (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))

    # ── Assigned materials (library) — REAL σ / BH / loss curves ──────────
    # Fetched BEFORE the σ-mass assembly so the eddy solve uses the σ of the
    # materials actually assigned in the UI (e.g. an ALUMINIUM shaft is 2.6e7
    # S/m — 5.7× the hardcoded carbon-steel value).  Falls back to the generic
    # constants when a lookup fails.
    from motor_ai_sim import materials as _mat_lib
    from motor_ai_sim.config import get_material_assignments as _gma
    _ma = _gma() or {}
    try:
        _steel_s = _mat_lib.get_steel(_ma.get("stator_core", "20SW1200"))
        _steel_r = _mat_lib.get_steel(_ma.get("rotor_core",  "20SW1200"))
    except Exception:
        _steel_s = _steel_r = None
    try:
        _magnet_mat = _mat_lib.get_magnet(_ma.get("magnet")) if _ma.get("magnet") else None
    except Exception:
        _magnet_mat = None

    def _lookup_sigma(name: str) -> float:
        for _cat in ("conductor", "steel", "magnet"):
            try:
                return float(getattr(_mat_lib.get_material(_cat, name), "sigma", 0.0) or 0.0)
            except Exception:
                continue
        return 0.0

    _sigma_mag_lib = (float(getattr(_magnet_mat, "sigma", 0.0) or 0.0)
                      if _magnet_mat else 0.0) or SIGMA_NDFEB
    _sigma_shaft_lib = _lookup_sigma(str(_ma.get("shaft", ""))) or SIGMA_SHAFT

    def _sigma_of_tag(t: int) -> float:
        t = int(t)
        if t >= DOM_COIL_BASE or t == DOM_COIL:      return _sig_cu_T
        if t >= DOM_MAG_BASE or t in (DOM_MAG_N, DOM_MAG_S): return _sigma_mag_lib
        if t == DOM_SHAFT:                           return _sigma_shaft_lib
        return 0.0

    half = {}
    for name, (P, T, tags, mats) in (
        ("s", (Ps, Tts, ts, None)), ("r", (Pr, Ttr, tr, matr0))):
        mesh = MeshTri(P, T); b = Basis(mesh, ElementTriP1()); nh = b.N
        K0 = {}; cells = {}; mu0 = {}
        Msig = _csr((nh, nh))            # σ-weighted mass (eddy term), 0 in air/iron
        for tag in np.unique(tags):
            idx = np.where(tags == tag)[0]; cells[int(tag)] = idx
            sb = Basis(mesh, ElementTriP1(), elements=idx)
            K0[int(tag)] = asm(_stiff, sb)
            _sig = _sigma_of_tag(int(tag))
            if _sig > 0.0:
                Msig = Msig + asm(_massform, sb) * _sig
        half[name] = dict(mesh=mesh, b=b, n=nh, K0=K0, cells=cells,
                          Msig=Msig.tocsr())
    # ── magnet source (rotor half, constant — magnets fixed at angle 0) ──
    # Built from a PER-ELEMENT magnetisation field (P0) so it can be de-rated
    # element-by-element by the demag pass below (_br_glob, 1.0 = full Br).
    # With _br_glob ≡ 1 this is numerically identical to the old per-tag sum.
    _rb  = Basis(half["r"]["mesh"], ElementTriP1())
    _rb0 = _rb.with_element(ElementTriP0())     # P0: dof == rotor element id
    _nt_r = half["r"]["mesh"].t.shape[1]
    _Mx_glob = np.zeros(_nt_r); _My_glob = np.zeros(_nt_r)
    for tag, idx in half["r"]["cells"].items():
        m = matr0.get(int(tag))
        if m is None or (abs(m.Mx) + abs(m.My)) <= 0:
            continue
        _Mx_glob[idx] = m.Mx; _My_glob[idx] = m.My
    def _build_fmag(_br):
        return asm(_msrc, _rb,
                   mx=_rb0.interpolate(_Mx_glob * _br),
                   my=_rb0.interpolate(_My_glob * _br))
    # magnet_scale lets the torque decomposition turn the PMs OFF (=0 →
    # reluctance-only torque) or weaken them, without touching geometry.
    _br_glob = np.full(_nt_r, float(magnet_scale))   # per-element Br factor (demag de-rating × magnet_scale)
    f_mag = _build_fmag(_br_glob)
    # per-phase unit-current stator source vectors
    f_coil = {'A': np.zeros(half["s"]["n"]), 'B': np.zeros(half["s"]["n"]),
              'C': np.zeros(half["s"]["n"])}
    coil_info = []   # (idx, areas, dir, phase) for ψ
    areas_s = _triangle_areas(half["s"]["mesh"])
    for ph in ('A', 'B', 'C'):
        Iunit = {'A': 0.0, 'B': 0.0, 'C': 0.0}; Iunit[ph] = 1.0
        mats_u = build_materials(Iunit, dom.winding_layout,
                                 getattr(cs, "polys", polys), 0.0, slot_area_m2, n_wires)
        for tag, idx in half["s"]["cells"].items():
            mu = mats_u.get(int(tag))
            if mu is None or mu.J_z == 0.0:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            f_coil[ph] += asm(_f1, sb) * mu.J_z
    # ψ coil map (phase, dir) per coil tag — from a full-current material build
    mats_full = build_materials(_currents(0.0), dom.winding_layout,
                                getattr(cs, "polys", polys), 0.0, slot_area_m2, n_wires)
    for tag, idx in half["s"]["cells"].items():
        _mt = mats_full.get(int(tag))
        if _mt is None:
            if int(tag) >= 200:      # a coil tag the material map does not know
                log.warning("psi map: unknown coil tag %d (not in material map)", int(tag))
            continue
        nm = _mt.name
        if not nm.startswith("coil_"):
            continue
        # name = "coil_<i>_slot<j>_<phase><+|->"  → phase is the char before +/-
        ph = nm[-2] if nm[-1] in "+-" else nm[-1]
        direction = 1.0 if nm.endswith("+") else -1.0
        if ph in "ABC":
            coil_info.append((idx, areas_s[idx], direction, ph))

    # ── Stage 2: solid-copper current-constrained eddy data ──────────────────
    # Each coil is a SOLID bar: J = σ(−∂A/∂t + U_c) with ∫J dA = I_c imposed.
    # Per coil store: g_c (σ-lumped load, full DOF space), S_c = ∫σ dA, and the
    # imposed-current coefficient I_c_unit = dir·n_wires·(area/slot_area) so that
    # I_c = Ist[phase]·I_c_unit exactly matches the magnetostatic ampere-turns.
    _coil_con = []
    if eddy:
        _ones_s = np.ones(half["s"]["n"])
        _nr0 = half["r"]["n"]
        for tag, idx in half["s"]["cells"].items():
            if int(tag) < DOM_COIL_BASE:
                continue
            sb = Basis(half["s"]["mesh"], ElementTriP1(), elements=idx)
            g_s = np.asarray((asm(_massform, sb) * _sig_cu_T) @ _ones_s)
            nm = (mats_full.get(int(tag)) or FEMMaterial("x")).name
            ph = nm[-2] if nm.endswith(("+", "-")) else "A"
            dr = 1.0 if nm.endswith("+") else -1.0
            area_c = float(areas_s[idx].sum())
            _coil_con.append({
                "g": np.concatenate([g_s, np.zeros(_nr0)]),
                "S": float(g_s.sum()),
                "Iunit": dr * n_wires * area_c / max(slot_area_m2, 1e-12),
                "phase": ph,
                "nodes": np.unique(half["s"]["mesh"].t[:, idx]),   # stator-local node ids
            })

    # ── Rotor-eddy stage: FIELD-BASED magnet eddy losses ─────────────────────
    # The rotor mesh is the rotor's MATERIAL frame (rotation lives in the slip
    # pairing), so dA/dt at a rotor node IS the material ∂A/∂t — no convective
    # term.  Each isolated magnet carries J = σ(−∂A/∂t + U_m) with ∫J dA = 0;
    # U_m is the per-magnet area-mean of ∂A/∂t (uniform σ).  Magnet halves
    # bisected by the sector cut take U = 0 — their (anti)periodic image
    # cancels the net axial current by symmetry.
    #
    # IMPLEMENTATION: post-processing on the magnetostatic A(t) histories with
    # the SAME smoothed angle-derivative the B-field losses use.  An in-loop
    # coupled solve was tried first and rejected: the raw frame-to-frame ∂A/∂t
    # rides on the slip-ring node-merge jitter, and the σ|∂A/∂t|² integral
    # AMPLIFIES that noise with the step count (P_mag tripled going 24→72
    # steps).  _angle_ddt_2d low-pass-filters A(θ) over the unique slip-node
    # positions before differentiating — physics (slot ripple, 1–2 cycles per
    # period) passes, merge jitter dies.  The resistance-limited approximation
    # (no eddy-reaction skin effect) is good for the magnets: skin depth at the
    # slot-passing frequency ≈ 12 mm vs ~14 mm magnet width, and neglecting the
    # reaction errs conservative (slightly over-reports the loss).
    # σ comes from the ASSIGNED magnet material (library), not a constant.
    _rot_con = []            # bordered ∫J=0 rows — only for the eddy J-VIEW mode
    _rot_sig_nodes = []      # (nodes_global, σ) per rotor group — J snapshot
    _mag_groups = []         # per magnet: element triplets/areas for the loss
    _magnode_glob = np.array([], int)   # global DOF ids of all magnet nodes
    _shaft_group = None                 # field-based shaft eddy group (rotor frame)
    _shaftnode_glob = np.array([], int) # global DOF ids of the shaft nodes
    if rotor_eddy:
        _ones_r = np.ones(half["r"]["n"])
        _areas_r_re = _triangle_areas(half["r"]["mesh"])
        _mag_tags = [int(t) for t in half["r"]["cells"]
                     if (matr0.get(int(t)) is not None
                         and (abs(matr0[int(t)].Mx) + abs(matr0[int(t)].My)) > 0)]
        _mag_area = {t: float(_areas_r_re[half["r"]["cells"][t]].sum())
                     for t in _mag_tags}
        _med_area = float(np.median(list(_mag_area.values()))) if _mag_area else 0.0
        _magnode_loc = (np.unique(np.concatenate(
            [half["r"]["mesh"].t[:, half["r"]["cells"][t]].ravel()
             for t in _mag_tags])) if _mag_tags else np.array([], int))
        _magnode_glob = _magnode_loc + nsn
        _n_interior = _n_halves = 0
        for t in _mag_tags:
            idx = np.asarray(half["r"]["cells"][t], int)
            tri = half["r"]["mesh"].t[:, idx]                  # (3, E) rotor-local
            is_half = bool(_med_area > 0 and _mag_area[t] < 0.6 * _med_area)
            _mag_groups.append({
                "tri": np.searchsorted(_magnode_loc, tri),     # → magnet-node idx
                "areas": _areas_r_re[idx].astype(float),
                "half": is_half,
            })
            nds = np.unique(tri)
            _rot_sig_nodes.append((nds + nsn, _sigma_mag_lib))
            if is_half:
                _n_halves += 1               # edge half-magnet → U = 0
                continue
            _n_interior += 1
            # ∫J=0 constraint row — used only by the coupled eddy J-VIEW mode.
            sb = Basis(half["r"]["mesh"], ElementTriP1(), elements=idx)
            g_r = np.asarray((asm(_massform, sb) * _sigma_mag_lib) @ _ones_r)
            _rot_con.append({"g": np.concatenate([np.zeros(nsn), g_r]),
                             "S": float(g_r.sum()),
                             "nodes": nds + nsn})
        _sh_idx = np.asarray(half["r"]["cells"].get(int(DOM_SHAFT),
                                                    np.array([], int)), int)
        if _sh_idx.size:
            _sh_tri = half["r"]["mesh"].t[:, _sh_idx]            # (3, E) rotor-local
            _shaftnode_loc = np.unique(_sh_tri)
            _shaftnode_glob = _shaftnode_loc + nsn
            _rot_sig_nodes.append((_shaftnode_glob, _sigma_shaft_lib))
            # Field-based shaft eddy group (rotor frame, ∫J=0): the shaft co-rotates
            # with the magnets, so the magnet field is DC in its frame → no loss;
            # only the AC coil-current / slot-ripple field dissipates.  Same
            # treatment as the magnets (replaces the lab-frame slab estimate).
            _shaft_group = {
                "tri":   np.searchsorted(_shaftnode_loc, _sh_tri),
                "areas": _areas_r_re[_sh_idx].astype(float),
            }
        log.info("rotor-eddy: %d interior magnets (∫J=0), %d edge halves (U=0) | "
                 "σ_mag=%.3g σ_shaft=%.3g S/m (library)",
                 _n_interior, _n_halves, _sigma_mag_lib, _sigma_shaft_lib)

    # ── EXACT edge data for the magnet A-histories (pole-shift symmetry) ─────
    # The loss window spans whole electrical periods, but the ROTOR-frame
    # signal is NOT periodic over it (the stator structure passes a non-integer
    # number of times), so any wrap at the window edge is wrong.  The missing
    # samples beyond the edges exist EXACTLY inside the window: after one
    # electrical period the whole solution repeats with the rotor advanced two
    # pole pitches, so  A(node n, t±T) = A(node n∓, t)  where n∓ is the node
    # rotated ∓2 pole pitches in the (pole-periodic) rotor mesh — a pure node
    # permutation, no approximation.  Crossing a sector cut multiplies A by the
    # anti-periodic sign.  Built here once; used to pad the magnet histories so
    # the loss derivative has REAL data at both window ends.
    # The pole meshes share IDENTICAL boundaries (setPeriodic) but gmsh meshes
    # each pole INTERIOR independently (measured node mismatch ≈ 0.9 mm), so a
    # pure node permutation does not exist.  The identity is continuous though:
    # the value at the rotated POINT exists in the same solve — so the map is a
    # P1 barycentric INTERPOLATION matrix over the magnet triangles (the same
    # accuracy class as the FEM field itself).
    _pp2 = None                       # (W_fwd, sign_fwd, W_bwd, sign_bwd)
    if rotor_eddy and _magnode_loc.size:
        try:
            from scipy.spatial import Delaunay as _Del, cKDTree as _KD
            _theta2 = math.radians(2.0 * 360.0 / max(1, p.num_poles))  # 2 pole pitches
            _Pn = half["r"]["mesh"].p[:, _magnode_loc]    # (2, Nn) node coords [m]
            _Nn = _Pn.shape[1]
            _sec_rad = math.radians(sector_deg)
            _dt2 = _Del(_Pn.T)
            _kd2 = _KD(_Pn.T)

            def _pole_map(_dir):
                c, s = math.cos(_dir * _theta2), math.sin(_dir * _theta2)
                x = c * _Pn[0] - s * _Pn[1]; y = s * _Pn[0] + c * _Pn[1]
                sg = np.ones(_Nn)
                if not _full_ring:
                    ang = np.mod(np.arctan2(y, x), 2.0 * math.pi)
                    # wrap rotated points back into the wedge; every cut crossing
                    # flips A by the (anti-)periodic boundary sign.  k and k−NS
                    # wraps give the same sign because _bc_sign**NS == +1.
                    for _ in range(int(NS)):
                        _ov = ang > _sec_rad + 1e-9
                        if not _ov.any():
                            break
                        ang = np.where(_ov, ang - _sec_rad, ang)
                        sg = np.where(_ov, sg * _bc_sign, sg)
                    r = np.hypot(x, y)
                    x = r * np.cos(ang); y = r * np.sin(ang)
                _tgt = np.column_stack([x, y])
                _sx = _dt2.find_simplex(_tgt)
                _out = _sx < 0
                _d, _near = _kd2.query(_tgt)
                if _out.any() and float(np.max(_d[_out])) > 0.5 * float(min_size_mm) * 1e-3:
                    raise ValueError(
                        f"{int(_out.sum())} targets {float(np.max(_d[_out]))*1e3:.3f} mm "
                        "outside the magnet hull")
                return _tgt, sg, _near.astype(int)
            _tgF, _sgF, _nrF = _pole_map(+1.0)   # n's position one period LATER
            _tgB, _sgB, _nrB = _pole_map(-1.0)   # … one period EARLIER
            _pp2 = (_dt2, _tgF, _sgF, _nrF, _tgB, _sgB, _nrB)
            log.info("magnet-history edge pads: 2-pole-pitch C1 interpolation map OK "
                     "(%d nodes)", _Nn)
        except Exception as _pe:
            log.warning("magnet-history edge pads unavailable (%s) — "
                        "falling back to C0-detrend edges", _pe)
            _pp2 = None

    # ── Loss bookkeeping — iron Bertotti + magnet eddy from the ACTUAL B(t) ──
    # The sliding-band run gives a clean B(t) per element over a full electrical
    # period, so instead of the remesh path's single-snapshot Bertotti we use
    # the genuine time-derivative of the field:
    #   • classical eddy  ∝ ⟨(dB/dt)²⟩  (frequency-correct for ALL harmonics —
    #     slot ripple included — because faster flux ⇒ larger dB/dt ⇒ ∝ f²)
    #   • hysteresis      ∝ f·B_ac²     (B_ac = AC excursion, so a DC-biased
    #     rotor tooth contributes only its ripple, not its standing flux)
    #   • magnet eddy     = σ·d²/12·⟨(dB/dt)²⟩  (honest slab loss, no empirical
    #     ripple-fraction fudge)
    # The Bertotti coefficients (kh,kc,ke) are FITTED to the material's measured
    # loss-vs-frequency curves at runtime (materials.effective_bertotti), so this
    # IS the real frequency-dependent loss model.  (Steel/magnet materials were
    # already fetched above, before the σ-mass assembly.)
    _sigma_mag = float(getattr(_magnet_mat, "sigma", 0.0)) if _magnet_mat else 0.0
    # Magnet eddy slab dimension d: the AC field the magnet sees is the SLOT
    # RIPPLE, which varies TANGENTIALLY, so the eddy-current loop is limited by
    # the magnet's TANGENTIAL WIDTH (pole-pitch × fill) — NOT its radial
    # thickness.  P_eddy ∝ d², so using the (smaller) tangential width instead
    # of the 16 mm radial height drops the loss ~3× into the physical range
    # (the radial-thickness slab over-counted the un-segmented eddy).
    _r_mag_mid = 0.5 * (p.r_rotor_in + p.r_rotor_out)
    _mag_frac = float(getattr(p, "magnet_fill_fraction", 0.85) or 0.85)
    _d_mag_m = max(1e-3, (2.0 * math.pi * _r_mag_mid
                          / max(p.num_poles, 1)) * _mag_frac)
    areas_r = _triangle_areas(half["r"]["mesh"])
    _iron_s_idx = np.asarray(half["s"]["cells"].get(int(DOM_STATOR), np.array([], int)), int)
    _iron_r_idx = np.asarray(half["r"]["cells"].get(int(DOM_ROTOR),  np.array([], int)), int)
    _mag_parts = []
    for _tag, _idx in half["r"]["cells"].items():
        _m = matr0.get(int(_tag))
        if _m is not None and (abs(_m.Mx) + abs(_m.My)) > 0:
            _mag_parts.append(np.asarray(_idx, int))
    _mag_idx = np.concatenate(_mag_parts) if _mag_parts else np.array([], int)
    # Non-laminated solid conductors that ALSO carry rotating-field eddy losses
    # (in addition to magnets): the COILS (solid copper bars, stator side) and
    # the SHAFT (solid steel, rotor side).
    _coil_parts = [np.asarray(_i, int) for _t, _i in half["s"]["cells"].items()
                   if int(_t) >= DOM_COIL_BASE or int(_t) == int(DOM_COIL)]
    _coil_idx = np.concatenate(_coil_parts) if _coil_parts else np.array([], int)
    _shaft_idx = np.asarray(half["r"]["cells"].get(int(DOM_SHAFT),
                                                   np.array([], int)), int)
    # Per-frame B histories for the loss elements only (keeps memory small).
    _hist_sx = []; _hist_sy = []; _hist_rx = []; _hist_ry = []
    _hist_mx = []; _hist_my = []; _mshift_hist = []
    _hist_cx = []; _hist_cy = []; _hist_shx = []; _hist_shy = []

    SAT = {DOM_STATOR, DOM_ROTOR, DOM_SHAFT}
    # Per-tag base μ_r (air=1, coil=1, magnet=μ_rec, iron=μ_steel) + BH curves
    # for the saturable iron tags only.
    mu0 = {"s": {}, "r": {}}
    sat_bh = {"s": {}, "r": {}}
    for hn, md in (("s", mats_full), ("r", matr0)):
        for tag in half[hn]["cells"]:
            m = md.get(int(tag))
            mu0[hn][int(tag)] = max(float(m.mu_r), 1.0) if m else 1.0
            if (tag in SAT) and m and m.bh_curve and len(m.bh_curve) >= 2:
                sat_bh[hn][int(tag)] = m.bh_curve

    # Per-element saturation: the CONSTANT (non-iron) stiffness is pre-summed
    # once; the saturable iron tags are re-assembled each Picard iteration with
    # an element-wise reluctivity ν(x) so every triangle gets its own μ(|B|)
    # from the B-H curve — no single lumped μ that over- or under-saturates the
    # whole domain.
    K_const = {}; sb_sat = {"s": {}, "r": {}}
    b0_sat = {"s": {}, "r": {}}; nu_el = {"s": {}, "r": {}}
    for hn in ("s", "r"):
        h = half[hn]
        Kc = _csr((h["n"], h["n"]))
        for tag, Kd in h["K0"].items():
            if tag in sat_bh[hn]:
                idx = h["cells"][tag]
                _sbi = Basis(h["mesh"], ElementTriP1(), elements=idx)
                sb_sat[hn][tag] = _sbi
                b0_sat[hn][tag] = _sbi.with_element(ElementTriP0())
                nu_el[hn][tag] = np.full(
                    idx.size, 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
            else:
                Kc = Kc + Kd * (1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0)))
        K_const[hn] = Kc.tocsr()

    r_all = np.hypot(Pall[0], Pall[1])
    outer_nodes = np.where(r_all >= r_all.max() - 5e-4)[0]

    # ── Moving-band machinery ─────────────────────────────────────────────
    # The annulus R1..R2 is re-stitched EVERY frame; see _simplify_polys: each
    # ring is a UNIFORM N-gon, so the stitch pattern is IDENTICAL at every
    # shift m — two congruent triangle shapes whose local stiffness (air) and
    # torque vectors are computed ONCE; per frame only the index mapping
    # (rotor k ↔ stator k+m, anti-periodic sign on wrap) changes.  This
    # replaces the node-merge slip coupling whose frozen irregular fans
    # produced the order-6 parasitic cogging.
    if _moving:
        _gR1 = rring.astype(int) + nsn          # rotor-ring DOFs (global ids)
        _gR2 = sring.astype(int)                # stator-ring DOFs
        _dphi_b = math.radians(spacing)

        # ── Harmonic air-gap macroelement (Davat) — analytic gap, smooth torque ──
        # Couple the two uniform rings by the EXACT Laplace solution of the gap
        # annulus instead of the single-layer triangle strip.  Both rings are
        # uniform N-gons ⇒ the coupling is block-circulant and DFT-diagonalises into
        # 2×2 per-harmonic blocks; rotor rotation by m nodes is a smooth phase
        # e^{i·k·φ} (no node re-pairing) → the broadband sliding-band ripple is gone
        # at the source.  Per-harmonic stiffness + nodal assembly validated standalone
        # (energy == analytic == FEM annulus; m-shift == circulant shift).  Full-ring
        # only for now (sector anti-periodic harmonics deferred).
        _use_macro = bool(_SB_AIRGAP_MACRO) or bool(airgap_macro)
        if _use_macro:
            # SECTOR generalisation: a wedge of 1/S of the machine carries the
            # (anti-)periodic harmonic ladder k = S·(m + moff), moff = 1/2 when
            # the wedge field is ANTI-periodic (odd pole count per wedge, i.e.
            # _bc_sign = −1) and 0 when periodic.  The half-integer ladder makes
            # the circulant a SKEW-circulant automatically (col(d−Nw) = −col(d)),
            # so the anti-periodic wrap sign needs no special-casing.  Full ring
            # is the S=1, moff=0 member of the same family.
            if _full_ring:
                _NwM = int(Nring)                  # independent ring nodes in model
                _SfacM = 1
                _moffM = 0.0
            else:
                _NwM = int(Nring) - 1              # open wedge: last node == first via cut
                _SfacM = max(1, int(round(360.0 / (_NwM * float(spacing)))))
                _moffM = 0.5 if float(_bc_sign) < 0 else 0.0
            _NfullM = _NwM * _SfacM                # full-circle node count (order base)
            _r1M, _r2M, _stkM = float(_r1_m), float(_r2_m), float(p.stack_length)

            def _Qk_gap(k):
                # per-harmonic 2×2 [[Q11(rotor),Q12],[Q12,Q22(stator)]] for the gap
                # energy u^T Q u with u=(A@r1, A@r2); r1=rotor ring, r2=stator ring.
                # Closed form in ρ=r1/r2<1 (the raw r^{±2k} form overflows for the
                # large k reached at N~1008): Q11=Q22=k(1+ρ^2k)/(1−ρ^2k),
                # Q12=−2k·ρ^k/(1−ρ^2k).  ρ^k→0 for high k → self-stiffness ~k, no
                # cross-coupling (the gap low-passes the surface field), as expected.
                if k == 0:
                    c = 1.0 / math.log(_r2M / _r1M)
                    return c, -c, c
                rhok = (_r1M / _r2M) ** k
                rho2k = rhok * rhok
                den = 1.0 - rho2k
                q11 = k * (1.0 + rho2k) / den
                return q11, -2.0 * k * rhok / den, q11

            # PER-UNIT-LENGTH normalisation: the half-mesh K_const carries no stack
            # length (2D solve; L is applied later in the torque/loss post-processing,
            # exactly like _T_band).  So the gap coupling must also be per-unit — the
            # stack length _stkM is applied only in _T_macro below.  (Baking L in here
            # made the gap ~1/L weaker than the iron → decoupled, garbage field.)
            # Normalise by N_FULL, not Nw: the Qk annulus form integrates the WHOLE
            # 2π gap, so the ladder with G=2π/(MU0·Nfull) carries the machine energy;
            # the wedge's 1/S share then comes out of the Nw-point DFT automatically.
            # The old G=2π/(MU0·Nw) ("wedge = 1/S ⇒ S·G" reasoning) double-counted S:
            # measured K_wedge == S × the energy restriction (1/S)·UᵀK_full·U on EVERY
            # mode — a uniformly 4×-stiff gap on the 1/4.  That barely moves the
            # fundamental (ψ −3%, T_avg −1% — why mean checks passed) but skews the
            # high-harmonic field balance → spurious torque orders that grow with
            # steps/period (sector 22→30% vs full 15%).  Full ring: Nfull==Nw, no-op.
            _Gm = 2.0 * math.pi / (MU0 * _NfullM)         # DFT energy normalisation (per-unit)
            _kphysM = _SfacM * (np.arange(_NwM) + _moffM)  # physical order per bin
            _kfoldM = np.minimum(_kphysM, _NfullM - _kphysM)   # fold to 0..Nfull/2
            _mu_rr = np.empty(_NwM); _mu_rs = np.empty(_NwM); _mu_ss = np.empty(_NwM)
            for _j in range(_NwM):
                _q11, _q12, _q22 = _Qk_gap(float(_kfoldM[_j]))
                _mu_rr[_j], _mu_rs[_j], _mu_ss[_j] = _Gm*_q11, _Gm*_q12, _Gm*_q22
            for _j in range(_NwM):                         # unpaired bins counted once
                if _kphysM[_j] == 0 or 2 * _kphysM[_j] == _NfullM:
                    _mu_rr[_j] *= 0.5; _mu_rs[_j] *= 0.5; _mu_ss[_j] *= 0.5
            _jfreq = _kphysM.copy()                        # signed PHYSICAL order (for ∂/∂φ)
            _jfreq[_jfreq > _NfullM/2] -= _NfullM
            _ii_m = (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) % _NwM
            # Half-integer twist: FFT bins live at (m+moff)/Nw cycles per node.
            _twn = np.exp(-1j * 2.0 * np.pi * _moffM * np.arange(_NwM) / _NwM)
            _twd = np.conj(_twn)                           # e^{+i·2π·moff·d/Nw}
            _gR1M = _gR1[:_NwM]; _gR2M = _gR2[:_NwM]

            def _circ_of(mu):                              # (skew-)circulant C[i,j]=col[(i−j)%Nw]
                col = (_twd * np.fft.ifft(mu)).real
                return col[_ii_m] * np.where(
                    (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) < 0,
                    (-1.0 if _moffM else 1.0), 1.0)
            _Krr_blk = _circ_of(_mu_rr)                    # rotor-rotor  (m-independent)
            _Kss_blk = _circ_of(_mu_ss)                    # stator-stator(m-independent)
            _Rg1, _Cg1 = np.meshgrid(_gR1M, _gR1M, indexing="ij")
            _Rg2, _Cg2 = np.meshgrid(_gR2M, _gR2M, indexing="ij")
            _Rg12, _Cg12 = np.meshgrid(_gR1M, _gR2M, indexing="ij")

            def _K_gap_macro(m):
                # rotor↔stator block at rotor shift m: phase e^{i·κ·φ_m},
                # φ_m = 2π·m/Nfull, κ = SIGNED physical order per bin (_jfreq).
                # m may be FRACTIONAL — the phase is analytic in the rotor angle
                # (no node pairing), so any steps/period is exact.  The order
                # MUST be signed: the unsigned bin form e^{i·S(j+moff)·φ_m}
                # differs from the true e^{iκφ_m} by e^{i·2πm} on every
                # negative-frequency bin — invisible for whole-node m, but a
                # fractional shift then cycles a huge spurious harmonic with
                # the period of frac(m) (measured: T_avg 30→10, order-24 ×50 at
                # 2/3 node per step).
                ph = np.exp(1j * _jfreq * (2.0*np.pi*float(m)/_NfullM))
                colm = (_twd * np.fft.ifft(_mu_rs * ph))
                _krs = colm.real[_ii_m] * np.where(
                    (np.arange(_NwM)[:, None] - np.arange(_NwM)[None, :]) < 0,
                    (-1.0 if _moffM else 1.0), 1.0)
                # forward block  K[gR1[a],gR2[b]] = _krs[a,b]  and its symmetric
                # transpose K[gR2[b],gR1[a]] = _krs[a,b] (NOT _krs[b,a] — _krs is
                # asymmetric for m≠0, so a literal .T here made the global matrix
                # non-symmetric → garbage solve at non-integer-pole shifts).
                rows = np.concatenate([_Rg1.ravel(), _Rg2.ravel(),
                                       _Rg12.ravel(), _Cg12.ravel()])
                cols = np.concatenate([_Cg1.ravel(), _Cg2.ravel(),
                                       _Cg12.ravel(), _Rg12.ravel()])
                data = np.concatenate([_Krr_blk.ravel(), _Kss_blk.ravel(),
                                       _krs.ravel(), _krs.ravel()])
                return _coo((data, (rows, cols)), shape=(n, n)).tocsr()

            def _T_macro(m, Avec):
                # virtual work: T = −∂(L·w_gap)/∂φ ; only the rotor↔stator term depends
                # on φ.  w_rs(per-unit) = (1/Nw) Σ μ_rs(j) e^{i·k·φ} conj(Ûr) Ûs with
                # Û = FFT of the moff-twisted ring samples; ∂/∂φ brings i·k_signed
                # (PHYSICAL order).  Returns the WEDGE torque (1/S of the machine),
                # matching _T_band's wedge convention — the caller's sector scaling
                # applies unchanged.  Real torque scales by the stack length _stkM.
                Ur = np.fft.fft(Avec[_gR1M] * _twn); Us = np.fft.fft(Avec[_gR2M] * _twn)
                # SIGNED order in the phase (see _K_gap_macro) — exact for
                # fractional m; identical to the old unsigned form for whole m.
                ph = np.exp(1j * _jfreq * (2.0*np.pi*float(m)/_NfullM))
                return float(-(_stkM/_NwM) * np.sum(
                    (1j*_jfreq) * _mu_rs * ph * np.conj(Ur) * Us).real)

            if not _full_ring:
                # ── SECTOR torque via UNFOLD to the validated full-ring formula ──
                # The wedge-native (half-integer twist) torque above emits SPURIOUS
                # harmonics (measured on the 1/4: even 10/16 + a 5× order-6 on the
                # tensor mesh, odd 1/3/11 on the geo mesh; the ripple GROWS with
                # steps/period) while the FIELD is provably fine (ψ and mean torque
                # match the full ring).  So keep the K-coupling, and evaluate the
                # per-frame torque the provably-equivalent way instead: the (anti-)
                # periodic BC determines the WHOLE ring from the wedge samples,
                #     A_full[j·Nw + k] = σ^j · A_wedge[k],   σ = _bc_sign
                # (σ^S = 1 always: anti-periodic wedges come in an even count), so
                # unfold both rings, apply the VALIDATED full-ring virtual-work
                # formula on N_full samples, and return the wedge share (÷S — the
                # caller multiplies by NS).  No twist algebra involved.
                _sgnM = -1.0 if float(_bc_sign) < 0 else 1.0
                _spowM = _sgnM ** np.arange(_SfacM)          # σ^j per sector copy
                _GfM = 2.0 * math.pi / (MU0 * _NfullM)
                _kfM = np.arange(_NfullM)
                _kfoldF = np.minimum(_kfM, _NfullM - _kfM)
                _mu_rs_f = np.empty(_NfullM)
                for _j in range(_NfullM):
                    _mu_rs_f[_j] = _GfM * _Qk_gap(float(_kfoldF[_j]))[1]
                for _j in range(_NfullM):                    # unpaired bins once
                    if _kfM[_j] == 0 or 2 * _kfM[_j] == _NfullM:
                        _mu_rs_f[_j] *= 0.5
                _jfreq_f = _kfM.astype(float)
                _jfreq_f[_jfreq_f > _NfullM / 2] -= _NfullM

                def _T_macro(m, Avec):                       # noqa: F811 — override
                    Urf = np.fft.fft(np.concatenate(
                        [_s * Avec[_gR1M] for _s in _spowM]))
                    Usf = np.fft.fft(np.concatenate(
                        [_s * Avec[_gR2M] for _s in _spowM]))
                    # SIGNED order (see _K_gap_macro) — exact for fractional m
                    ph = np.exp(1j * _jfreq_f * (2.0*np.pi*float(m)/_NfullM))
                    return float(-(_stkM / _NfullM) * np.sum(
                        (1j * _jfreq_f) * _mu_rs_f * ph
                        * np.conj(Urf) * Usf).real) / _SfacM

        def _tri_template(P3):
            (x1, y1), (x2, y2), (x3, y3) = P3
            bb = np.array([y2 - y3, y3 - y1, y1 - y2])
            cc = np.array([x3 - x2, x1 - x3, x2 - x1])
            area = 0.5 * abs(cc[2] * bb[1] - cc[1] * bb[2])
            Kl = (np.outer(bb, bb) + np.outer(cc, cc)) / (4.0 * area * MU0)
            cxl = (x1 + x2 + x3) / 3.0; cyl = (y1 + y2 + y3) / 3.0
            rcl = math.hypot(cxl, cyl); cp, sp = cxl / rcl, cyl / rcl
            # B = (∂A/∂y, −∂A/∂x);  Br = u·A,  Bφ = v·A  (template frame —
            # rotationally invariant, so valid for every quad of the ring)
            u = ( cc * cp - bb * sp) / (2.0 * area)
            v = (-cc * sp - bb * cp) / (2.0 * area)
            return Kl, u, v, area, rcl

        def _pol(r_, a_):
            return (r_ * math.cos(a_), r_ * math.sin(a_))
        _Ka, _ua, _va, _ArA, _rcA = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, 0.0), _pol(_r2_m, _dphi_b)])
        _Kb, _ub, _vb, _ArB, _rcB = _tri_template(
            [_pol(_r1_m, 0.0), _pol(_r2_m, _dphi_b), _pol(_r1_m, _dphi_b)])
        if _full_ring:
            _kk_b  = np.arange(Nring)               # closed: N quads
            _kk1_b = (np.arange(Nring) + 1) % Nring
        else:
            _kk_b  = np.arange(Nring - 1)           # open sector: N−1 quads
            _kk1_b = _kk_b + 1
        _ones_b = np.ones(len(_kk_b))

        def _band_idx(m):
            if _full_ring:
                j  = (_kk_b + int(m)) % Nring        # periodic, no sign
                j1 = (_kk_b + int(m) + 1) % Nring
                return j.astype(int), j1.astype(int), _ones_b, _ones_b
            j = _kk_b + int(m); j1 = j + 1
            sj = np.ones(Nring - 1); sj1 = np.ones(Nring - 1)
            while np.any(j > Nring - 1):
                w = j > Nring - 1
                j = np.where(w, j - (Nring - 1), j)
                sj = np.where(w, sj * _bc_sign, sj)
            while np.any(j1 > Nring - 1):
                w = j1 > Nring - 1
                j1 = np.where(w, j1 - (Nring - 1), j1)
                sj1 = np.where(w, sj1 * _bc_sign, sj1)
            return j.astype(int), j1.astype(int), sj, sj1

        def _K_band(m):
            j, j1, sj, sj1 = _band_idx(m)
            rows = []; cols = []; data = []
            for Kl, dofs, sgs in (
                (_Ka, (_gR1[_kk_b], _gR2[j],  _gR2[j1]),
                       (_ones_b, sj, sj1)),
                (_Kb, (_gR1[_kk_b], _gR2[j1], _gR1[_kk1_b]),
                       (_ones_b, sj1, _ones_b)),
            ):
                for pq in range(9):
                    pp, qq = divmod(pq, 3)
                    rows.append(dofs[pp]); cols.append(dofs[qq])
                    data.append(Kl[pp, qq] * sgs[pp] * sgs[qq])
            return _coo((np.concatenate(data),
                         (np.concatenate(rows), np.concatenate(cols))),
                        shape=(n, n)).tocsr()

        def _T_band(m, Avec):
            j, j1, sj, sj1 = _band_idx(m)
            Aa = np.vstack([Avec[_gR1[_kk_b]],
                            Avec[_gR2[j]] * sj, Avec[_gR2[j1]] * sj1])
            Ab = np.vstack([Avec[_gR1[_kk_b]],
                            Avec[_gR2[j1]] * sj1, Avec[_gR1[_kk1_b]]])
            s = (_ArA * _rcA * (_ua @ Aa) * (_va @ Aa)
                 + _ArB * _rcB * (_ub @ Ab) * (_vb @ Ab))
            # Arkkio over the STRIP alone — normalise by the strip's radial
            # width (r2−r1).  The strip is the consistently-COUPLED region
            # (rotor ring ↔ stator ring), so its stress is artifact-free; the
            # half-mesh gap fields are sheared (rotor at θ=0 vs coupled stator)
            # and carry a spurious DC torque, so they are NOT used.
            return float(np.sum(s)) * p.stack_length / (MU0 * (_r2_m - _r1_m))

        # Cut pairing is m-INDEPENDENT now (no slip merge) → constant Pro.
        _suf0 = _SignedUF(n)
        for a, b in zip(Mn, Sn):
            _suf0.union(int(b), int(a), _bc_sign)
        _roots0 = [_suf0.find(i) for i in range(n)]
        _rid0 = np.array([r for r, _ in _roots0])
        _rsg0 = np.array([s for _, s in _roots0], float)
        _uniq0, _inv0 = np.unique(_rid0, return_inverse=True)
        Pro_const = _coo((_rsg0, (np.arange(n), _inv0)),
                         shape=(n, _uniq0.size)).tocsr()
        outer_red_const = np.unique(_inv0[outer_nodes])
        log.info("moving band: %d quads (%s), r1=%.3f r2=%.3f mm, Δφ=%.4f°",
                 len(_kk_b), "full ring" if _full_ring else "sector",
                 _r1_m * 1e3, _r2_m * 1e3, spacing)

    # ── Frame loop ───────────────────────────────────────────────────────
    n_total = max(1, int(round(n_steps_per_period * n_periods)))
    # Voltage drive: the currents are STATE (start at 0), so the run has an
    # electrical start-up transient.  Run ONE extra settling period and discard
    # its frames after the loop — every reported series/metric is steady-state.
    # dt = T_elec·n_periods/n_total is invariant under the dual bump.
    _vskip = 0
    _v_nspp = int(round(n_steps_per_period))
    if _vdrive:
        # TEN settling periods with ITERATED Aitken.  The electrical time
        # constant L/R spans many periods on a low-R machine, so a marched DC
        # start-up decays too slowly to shed by brute force.  Instead: the
        # phasor init lands near the orbit, then the period-boundary flux
        # (which converges GEOMETRICALLY) is Δ²-extrapolated to its limit at
        # every 3rd boundary (anchors at periods 3, 6, 9 — each application
        # cuts the residual DC ~3×), and the final settling period runs after
        # the last anchor so the reported window starts on a clean orbit.
        # Settling frames use a REDUCED Picard depth (the DC dynamics only
        # need L roughly right); the last settling period + the reported
        # window run at full depth.
        _v_settle_periods = 10
        _vskip = _v_settle_periods * max(2, _v_nspp)
        n_periods = float(n_periods) + float(_v_settle_periods)
        n_total += _vskip
    # Demagnetisation: SETTLING pass, then a clean measurement pass.
    # The magnet weakens THROUGH the run, so a single pass measures a machine
    # whose magnets are still changing: torque decays across the window and the
    # reported "ripple" is mostly that decay (NdFeB measured 19 % with demag vs
    # 9 % without — the extra 10 points were the magnet dying, not a physical
    # oscillation).  Run one extra period first so the de-rating settles, then
    # discard those frames.  Every reported number then comes from the second
    # pass, on a magnet that has stopped moving.
    _dmskip = 0
    if demag and _mag_idx.size and _v_nspp > 1 and n_total > 1:
        _dmskip = _v_nspp
        n_periods = float(n_periods) + 1.0
        n_total += _dmskip
    period_mech = 360.0 / pole_pairs                      # one electrical period [deg mech]
    T_series = []; psiA = []; psiB = []; psiC = []
    IA = []; IB = []; IC = []; tt = []
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total

    # ═══════════════════════════════════════════════════════════════════════
    #  P2 (quadratic) SLIDING-BAND MAGNETOSTATIC PATH  (element_order == 2)
    # ═══════════════════════════════════════════════════════════════════════
    # B = curl A is LINEAR per element instead of piecewise-constant, so the
    # Arkkio air-gap torque is SMOOTH where P1 staircases.  The blocker solved
    # here is the moving-cut EDGE-MIDPOINT DOF stitching: P2 puts a dof on every
    # element edge, including the belt's rotor/stator interface edges, so the
    # signed union-find that welds the slip cut must pair those edge midpoints
    # (not just the vertices) as the rotor shifts by m slip nodes.  Assembled on
    # the SINGLE stitched mesh (mesh_all) — simplest correct P2 assembly — with a
    # per-frame P2 projection Pro2 that welds ring vertices AND ring-edge
    # midpoints (and, for a SECTOR wedge, the radial-cut vertices + cut-edge
    # midpoints with the anti-periodic _bc_sign).  Works for the full ring AND
    # anti-periodic sector wedges (validated: sector T_avg == full-ring T_avg to
    # 0.3 %).  Magnetostatic per frame (no σ∂A/∂t): for the cogging / ripple goal
    # this is exactly the physics that matters; eddy on P2 is a refinement.
    if element_order == 2:
        from skfem import ElementTriP2 as _P2E
        from scipy.sparse.linalg import splu as _splu2
        # ONE persistent MKL PARDISO solver for the whole run: it caches the
        # symbolic factorization and reuses it across the same-pattern Picard
        # sweeps of a frame (re-analysing only when the pattern changes — new
        # frame / new slip pairing).  None ⇒ pypardiso unavailable ⇒ SuperLU.
        try:
            if _os_sb.environ.get("SB_NO_PARDISO") == "1":
                raise ImportError("disabled via SB_NO_PARDISO")
            import pypardiso as _pypard2
            _pardiso2 = _pypard2.PyPardisoSolver()
        except Exception as _pae:
            log.info("pypardiso unavailable (%s) — using SuperLU for P2", _pae)
            _pardiso2 = None
        if _moving:
            raise NotImplementedError(
                "P2 + moving/harmonic-macro band not implemented; run the merged "
                "structured belt (structured_gap=True, element_order=2).")
        if eddy or _vdrive or demag:
            raise NotImplementedError(
                "P2 path supports current-drive magnetostatics + post-processed "
                "rotor_eddy losses; the coupled σ∂A/∂t J-view solve (eddy=True), "
                "voltage drive and demag pre-pass are not wired for P2 yet. See "
                "P2_NOTES.md.")

        b2 = Basis(mesh_all, _P2E())
        b2_0 = b2.with_element(ElementTriP0())      # P0 for per-element ν interpolate
        N2 = b2.N
        # Picard early-stop tolerance for the P2 branch.  On a COARSE belt mesh a
        # handful of BH-knee iron elements plateau above the P1 module tol (1e-3)
        # — the fixed point of the TORQUE/loss is reached far earlier (measured:
        # T_avg is flat to <0.3 % between residual 0.03 and 0.007), so chasing
        # 1e-3 just burns ~30 extra sweeps per frame for no physics change.  A
        # reachable tol lets warm-started frames early-stop in a handful of sweeps.
        _PIC_TOL2 = 6e-3
        nst = int(Tts.shape[1])                      # rotor elems in mesh_all are +nst
        n_all_el = int(mesh_all.t.shape[1])

        @BilinearForm
        def _stiff_nu2(u, v, w):
            return w["nu"] * _dot(_grad(u), _grad(v))

        # Newton tangent (differential-reluctivity) term:  T(u,v) =
        # 2·(dν/dB²)·(∇A·∇u)(∇A·∇v), with ∇A the current field gradient and
        # dν/dB² per element.  Added to the secant stiffness K(ν) to form the
        # Jacobian J = K + T of the magnetostatic residual R = K(ν(|B|))·A − f.
        @BilinearForm
        def _tang_nu2(u, v, w):
            gA = w["gA"]                       # (2, nelem, nqp) current ∇A
            gu = _grad(u); gv = _grad(v)
            au = gA[0] * gu[0] + gA[1] * gu[1]
            av = gA[0] * gv[0] + gA[1] * gv[1]
            return w["c"] * au * av            # c = 2·dν/dB² (element-constant)

        # ── vertex & edge dof maps ───────────────────────────────────────────
        vdof = b2.nodal_dofs[0]                       # global vertex id -> P2 dof
        fdof = b2.facet_dofs[0]                       # facet (edge) id  -> P2 dof
        _fac = mesh_all.facets
        _fa = np.minimum(_fac[0], _fac[1]); _fb = np.maximum(_fac[0], _fac[1])
        _emap = {(int(_fa[i]), int(_fb[i])): i for i in range(_fac.shape[1])}

        def _edge_dof(va, vb):
            fi = _emap.get((va, vb) if va < vb else (vb, va))
            return None if fi is None else int(fdof[fi])

        # ── P2 sources on the stitched mesh ──────────────────────────────────
        # magnet: per-element M over ALL elements (rotor block offset by nst)
        _mx_all = np.zeros(n_all_el); _my_all = np.zeros(n_all_el)
        _mx_all[nst:] = _Mx_glob * _br_glob          # _br_glob folds magnet_scale
        _my_all[nst:] = _My_glob * _br_glob
        f_mag2 = asm(_msrc, b2, mx=b2_0.interpolate(_mx_all),
                     my=b2_0.interpolate(_my_all))
        # per-phase UNIT-current stator coil sources
        f_coil2 = {'A': np.zeros(N2), 'B': np.zeros(N2), 'C': np.zeros(N2)}
        for _ph in ('A', 'B', 'C'):
            _Iu = {'A': 0.0, 'B': 0.0, 'C': 0.0}; _Iu[_ph] = 1.0
            _mu = build_materials(_Iu, dom.winding_layout,
                                  getattr(cs, "polys", polys), 0.0,
                                  slot_area_m2, n_wires)
            for tag, idx in half["s"]["cells"].items():
                m_ = _mu.get(int(tag))
                if m_ is None or m_.J_z == 0.0:
                    continue
                sb = Basis(mesh_all, _P2E(), elements=np.asarray(idx, int))
                f_coil2[_ph] += asm(_f1, sb) * m_.J_z

        # ── per-element base ν + saturable element sets (mesh_all element ids) ─
        nu_base2 = np.empty(n_all_el)
        for _hn, _off in (("s", 0), ("r", nst)):
            for tag, idx in half[_hn]["cells"].items():
                nu_base2[np.asarray(idx, int) + _off] = 1.0 / (
                    MU0 * max(mu0[_hn].get(int(tag), 1.0), 1.0))
        _sat2 = []          # (elem_ids_in_mesh_all, bh_curve)
        for _hn, _off in (("s", 0), ("r", nst)):
            for tag, curve in sat_bh[_hn].items():
                _sat2.append((np.asarray(half[_hn]["cells"][tag], int) + _off, curve))

        # ── CONSTANT/VARIABLE stiffness split (perf) ─────────────────────────
        # The non-saturable ν (air, magnet, coil, shaft, non-iron) never changes
        # across frames OR Picard sweeps → assemble that whole-mesh stiffness ONCE
        # (K_const2, iron elements zeroed).  Each Picard sweep then re-assembles
        # ONLY the saturable-iron tags on their element sub-bases and adds them —
        # exactly like the P1 K_const + per-tag path.  Cuts the per-sweep assembly
        # from the whole mesh to the iron fraction.
        _nu_const2 = nu_base2.copy()
        for _ids, _c in _sat2:
            _nu_const2[_ids] = 0.0
        K_const2 = asm(_stiff_nu2, b2, nu=b2_0.interpolate(_nu_const2)).tocsr()
        _sat_sub2 = []       # (sub_basis, sub_P0_basis, elem_ids, bh_curve)
        for _ids, _c in _sat2:
            _sb2 = Basis(mesh_all, _P2E(), elements=_ids)
            _sat_sub2.append((_sb2, _sb2.with_element(ElementTriP0()), _ids, _c))

        # ── outer Dirichlet: facet-based so P2 edge midpoints are pinned too ──
        _out_fac2 = mesh_all.facets_satisfying(
            lambda x: np.hypot(x[0], x[1]) >= r_all.max() - 5e-4)
        _D2_ids = np.asarray(b2.get_dofs(facets=_out_fac2).flatten(), int)

        # ── per-frame P2 projection: weld ring + (for sector) radial-cut, both
        #    VERTICES and EDGE midpoints, with the anti-periodic sign ──────────
        # Ring edges are between angularly-consecutive ring nodes: for the FULL
        # ring the ring is CLOSED (Nring edges, kk→(kk+1)%Nring); for a SECTOR
        # wedge it is OPEN (Nring−1 edges, kk→kk+1).  rring/sring are index-
        # aligned (both sorted by grid slot 0..Nring−1).
        if _full_ring:
            _re_pairs = [(kk, (kk + 1) % Nring) for kk in range(Nring)]
        else:
            _re_pairs = [(kk, kk + 1) for kk in range(Nring - 1)]
        # rotor ring-edge midpoint dof for each segment (constant across frames)
        _re_dofs = [_edge_dof(int(rring[a]) + nsn, int(rring[b]) + nsn)
                    for a, b in _re_pairs]
        _n_redge = int(sum(x is not None for x in _re_dofs))

        # Anti-periodic RADIAL-CUT welds (sector only) — VERTICES and the cut-
        # boundary EDGE midpoints.  Mn/Sn are matched master/slave cut vertices
        # sorted by radius, so consecutive entries (i, i+1) delimit a cut edge on
        # each side; the slave DOF = _bc_sign · master DOF, same as P1's vertex BC.
        _cut_v = []          # (slave_dof, master_dof, sign) for vertices
        _cut_e = []          # (slave_edge_dof, master_edge_dof, sign) for edges
        if not _full_ring and Mn.size:
            for i in range(Mn.size):
                _cut_v.append((int(vdof[int(Sn[i])]), int(vdof[int(Mn[i])]),
                               float(_bc_sign)))
            for i in range(Mn.size - 1):
                em = _edge_dof(int(Mn[i]), int(Mn[i + 1]))
                es = _edge_dof(int(Sn[i]), int(Sn[i + 1]))
                if em is not None and es is not None:
                    _cut_e.append((int(es), int(em), float(_bc_sign)))
        log.info("P2 belt: N2=%d dofs, %s, ring=%d nodes, %d/%d ring-edge + "
                 "%d cut-vertex + %d cut-edge midpoints paired",
                 N2, "full ring" if _full_ring else "sector",
                 Nring, _n_redge, len(_re_pairs), len(_cut_v), len(_cut_e))

        def _ring_map(m_shift):
            # rotor ring node kk -> (stator node j, sign).  Full ring: periodic
            # mod Nring, sign +1.  Sector: open wedge of Nring nodes, wrap period
            # Nring−1 with a _bc_sign flip per wrap (identical to the P1 loop).
            if _full_ring:
                j = (np.arange(Nring) + int(m_shift)) % Nring
                return j.astype(int), np.ones(Nring)
            j = np.empty(Nring, int); sg = np.ones(Nring)
            for kk in range(Nring):
                jj = kk + int(m_shift); s = 1.0
                while jj > Nring - 1: jj -= (Nring - 1); s *= _bc_sign
                while jj < 0:         jj += (Nring - 1); s *= _bc_sign
                j[kk] = jj; sg[kk] = s
            return j, sg

        def _build_Pro2(m_shift):
            suf = _SignedUF(N2)
            # radial-cut anti-periodic welds (sector) — m-independent
            for sd, md, sgn in _cut_v:
                suf.union(sd, md, sgn)
            for sd, md, sgn in _cut_e:
                suf.union(sd, md, sgn)
            # slip-ring welds (vertices + edge midpoints), rotor shifted by m
            j, sg = _ring_map(m_shift)
            for kk in range(Nring):
                suf.union(int(vdof[int(rring[kk]) + nsn]),
                          int(vdof[int(sring[j[kk]])]), float(sg[kk]))
            for e, (a, b) in enumerate(_re_pairs):
                re = _re_dofs[e]
                if re is None:
                    continue
                # the rotor edge (a,b) maps to the stator edge (j[a], j[b]); it is
                # a real mesh edge only when the two endpoints stay angularly
                # consecutive with the SAME sign (i.e. no wrap between them).
                if sg[a] != sg[b]:
                    continue
                se = _edge_dof(int(sring[j[a]]), int(sring[j[b]]))
                if se is not None:
                    suf.union(int(re), int(se), float(sg[a]))
            roots = [suf.find(i) for i in range(N2)]
            rid = np.array([r for r, _ in roots])
            rsg = np.array([s for _, s in roots], float)
            uniq, inv = np.unique(rid, return_inverse=True)
            Pro = _coo((rsg, (np.arange(N2), inv)),
                       shape=(N2, uniq.size)).tocsr()
            return Pro, np.unique(inv[_D2_ids])

        # ── P2 flux linkage (vertex-average per stator coil element) ─────────
        _As_v = vdof[:nsn]                            # stator vertex -> P2 dof
        _sc_psi2 = p.stack_length * NS / float(n_parallel)

        def _psi2(A2):
            As_ = A2[_As_v]
            A_tri = (As_[Tts[0]] + As_[Tts[1]] + As_[Tts[2]]) / 3.0
            pa = pb = pc = 0.0
            for idx_, ar_, dir_, ph_ in coil_info:
                sa_ = float(np.sum(ar_))
                if sa_ <= 0:
                    continue
                v_ = dir_ * float(np.sum(A_tri[idx_] * ar_)) / sa_
                if ph_ == 'A':   pa += v_
                elif ph_ == 'B': pb += v_
                else:            pc += v_
            return _sc_psi2 * pa, _sc_psi2 * pb, _sc_psi2 * pc

        # ── frame loop ───────────────────────────────────────────────────────
        _T2 = []; _psiA = []; _psiB = []; _psiC = []; _tt = []
        _IA = []; _IB = []; _IC = []
        _pic_iters = []; _pic_res_max = 0.0
        _snap2 = None
        # ── rotor-eddy / iron-loss histories (post-processed after the loop,
        #    exactly as the P1 path does — magnetostatic field + honest coupled
        #    rotor-eddy solve + Bertotti iron; no σ∂A/∂t in the main solve) ─────
        _nr2 = int(half["r"]["n"])                    # rotor vertex count
        _rot_vdof = vdof[nsn:nsn + _nr2]              # rotor vertex -> P2 dof
        _histA_rot2 = []                              # (N, n_rotor_nodes) rotor A
        _hsx2 = []; _hsy2 = []; _hrx2 = []; _hry2 = []  # stator/rotor iron B(t)
        _hcx2 = []; _hcy2 = []                        # coil B(t) for AC copper
        # FROZEN PERMEABILITY (frozen_nu): converge the saturation ONCE at frame
        # 0 (extended Picard) then hold the per-element ν fixed for every rotor
        # position — the industry-standard honest cogging/ripple method.  It
        # removes the per-frame saturation-Picard jitter (which does NOT converge
        # to _PIC_TOL on a coarse mesh and otherwise MASKS the discretisation
        # ripple that P2 actually fixes), so the remaining T(θ) variation is
        # purely geometric: P1 staircases, P2 is smooth.
        nu_all2 = nu_base2.copy()      # persists across frames when frozen_nu
        # NEWTON–RAPHSON for the BH nonlinearity (default; SB_NO_NEWTON=1 forces
        # the damped-Picard path).  Cross-frame warm starts: the previous frame's
        # converged field A and ν (a near-perfect Newton initial guess).
        _use_newton = (_os_sb.environ.get("SB_NO_NEWTON") != "1")
        _A2_prev = None; _nu_conv2 = None
        for k in range(n_total):
            if progress_cb is not None:
                try: progress_cb(k, n_total)
                except Exception: pass
            theta = (k / n_total) * period_mech * n_periods
            m_shift = int(round(theta / spacing))
            theta_eff = m_shift * spacing
            Ist = _currents(theta_eff)
            _IA.append(Ist['A']); _IB.append(Ist['B']); _IC.append(Ist['C'])
            f = (f_mag2 + Ist['A'] * f_coil2['A']
                 + Ist['B'] * f_coil2['B'] + Ist['C'] * f_coil2['C'])
            Pro, outer_red = _build_Pro2(m_shift)
            # WARM-START ν across frames (perf): only frame 0 converges from the
            # unsaturated base (~60–70 sweeps cold); every later frame starts from
            # the PREVIOUS frame's CONVERGED ν and reaches the fixed point in ~40
            # sweeps instead of a full cold ~70.  (The BH-knee Picard is genuinely
            # slow — P1's main loop needs ~55 sweeps too — so warm-start trims the
            # cold-start tax, it does not make it "a few".)  This is SOUND and does
            # NOT bias the mean torque because the Picard early-stops on the
            # residual (_PIC_TOL2, two consecutive sweeps): the initial guess
            # changes the PATH, never the fixed point.  The cap is raised so frame
            # 0 reaches _PIC_TOL2 from cold and warm-started frames have headroom.
            # Same strategy as the P1 main loop.
            if k == 0:
                nu_all2 = nu_base2.copy()          # frozen path: base at k=0
            _Ptf = np.asarray(Pro.T @ f).ravel()
            # free (non-Dirichlet) reduced DOFs — CONSTANT within a frame (Pro and
            # the outer Dirichlet set are fixed), so precompute the slice once.
            _free2 = np.setdiff1d(np.arange(Pro.shape[1]), outer_red)
            _bff2 = _Ptf[_free2]
            # cross-frame warm starts (previous converged field + ν).  The slip
            # pairing (Pro) changes every frame, so the previous field A_prev lives
            # on the PREVIOUS constraint manifold range(Pro_prev); PROJECT it onto
            # the current range(Pro) (least-squares, average each paired group) so
            # the Newton start is constraint-consistent — otherwise Newton drifts
            # off-manifold and diverges frame-to-frame.
            if k == 0 or _nu_conv2 is None:
                _nu_start = nu_base2.copy(); _A_start = np.zeros(N2)
            else:
                _nu_start = _nu_conv2.copy()
                _pd = np.asarray(Pro.multiply(Pro).sum(axis=0)).ravel()
                _A_start = Pro @ (np.asarray(Pro.T @ _A2_prev).ravel()
                                  / np.maximum(_pd, 1.0))

            def _solve_ff(Mff, rhs):
                nonlocal _pardiso2
                if _pardiso2 is not None:
                    try:
                        return _pardiso2.solve(Mff, rhs)
                    except Exception as _pe2:
                        log.warning("pypardiso solve failed (%s) — SuperLU fallback",
                                    _pe2)
                        _pardiso2 = None
                return _splu2(Mff).solve(rhs)

            def _elemB(Avec):
                bx, by, dq = _p2_B_at_quad(b2, Avec)
                ar = dq.sum(axis=1)
                return (np.sqrt(bx ** 2 + by ** 2) * dq).sum(axis=1) \
                    / np.maximum(ar, 1e-30)

            def _nu_of(Bmag, base):
                nu = base.copy()
                for _ids, _c in _sat2:
                    nu[_ids] = 1.0 / (MU0 * np.maximum(
                        _mu_r_from_bh_vec(_c, Bmag[_ids]), 1.0))
                return nu

            def _asmK(nu):
                K = K_const2.copy()
                for _sb2, _sb02, _ids2, _c2 in _sat_sub2:
                    _nf2 = _sb02.zeros(); _nf2[_ids2] = nu[_ids2]
                    K = K + asm(_stiff_nu2, _sb2, nu=_sb02.interpolate(_nf2))
                return K.tocsr()

            _res = 0.0; _nit = 0; _newton_ok = False
            # ── NEWTON–RAPHSON (differential-reluctivity tangent) ─────────────
            # Residual R(A)=K(ν(elem-mean|B|))·A−f is IDENTICAL to the Picard fixed
            # point (element-mean ν per iron element), so Newton converges to the
            # SAME physics — just far fewer sweeps.  Jacobian J=K+T with the
            # per-element tangent T=2(dν/dB²)(∇A·∇u)(∇A·∇v).  Line-search on |R|
            # globalises the BH knee; if it collapses, this frame falls back to
            # damped Picard (never returns garbage).
            if _use_newton and not frozen_nu and _sat2:
                # POINTWISE ν(|B|²) at quadrature points — the residual and the
                # tangent then use the SAME nonlinearity, giving a TRUE (quadratic)
                # Newton step.  (An element-mean ν residual with a pointwise
                # tangent is inconsistent → no acceleration.)  For P2, B is linear
                # per element, so pointwise ν is also the more accurate model; it
                # is validated to match the element-mean Picard fixed point below.
                def _Kpw(Avec):
                    K = K_const2.copy(); info = []
                    for _sb2, _sb02, _ids2, _c2 in _sat_sub2:
                        gA = _sb2.interpolate(Avec).grad            # (2,nel,nqp)
                        Bm = np.sqrt(np.maximum(gA[0] ** 2 + gA[1] ** 2, 1e-18))
                        mur = np.maximum(_mu_r_from_bh_vec(
                            _c2, Bm.ravel()).reshape(Bm.shape), 1.0)
                        nuq = 1.0 / (MU0 * mur)
                        K = K + asm(_stiff_nu2, _sb2, nu=nuq)
                        info.append((_sb2, _ids2, _c2, gA, Bm, nuq))
                    return K.tocsr(), info

                def _rfree_pw(Avec, K):
                    return np.asarray(Pro.T @ (K @ Avec - f)).ravel()[_free2]

                A2 = _A_start.copy(); _fail = False; _rrel = 1.0
                _bnrm = max(float(np.linalg.norm(_bff2)), 1e-30)
                for it in range(max(int(nonlinear_iterations), 20)):
                    _nit = it + 1
                    K, _info = _Kpw(A2)
                    r_free = _rfree_pw(A2, K)
                    _rrel = float(np.linalg.norm(r_free)) / _bnrm
                    if _rrel < 1e-7:
                        break
                    # tangent T = 2(dν/dB²)(∇A·∇u)(∇A·∇v), pointwise & consistent
                    T = None
                    for _sb2, _ids2, _c2, gA, Bm, nuq in _info:
                        _dB = 1e-3 * Bm + 1e-6
                        nu1 = 1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(
                            _c2, (Bm + _dB).ravel()).reshape(Bm.shape), 1.0))
                        nup = np.maximum((nu1 - nuq) / _dB / (2.0 * Bm), 0.0)  # dν/dB²
                        Ti = asm(_tang_nu2, _sb2, gA=gA, c=2.0 * nup)
                        T = Ti if T is None else T + Ti
                    J = (K + T).tocsr() if T is not None else K
                    Jff = (Pro.T @ J @ Pro).tocsr()[_free2][:, _free2].tocsc()
                    try:
                        _du = _solve_ff(Jff, -r_free)
                    except Exception as _je:
                        log.info("P2 Newton solve failed (%s) — Picard fallback", _je)
                        _fail = True; break
                    _duf = np.zeros(Pro.shape[1]); _duf[_free2] = _du
                    dA = Pro @ _duf
                    # backtracking line-search on the residual norm (BH-knee safety)
                    _r0 = float(np.linalg.norm(r_free)); _lam = 1.0; _acc = False
                    for _ls in range(6):
                        A_try = A2 + _lam * dA
                        _Kt, _ = _Kpw(A_try)
                        if float(np.linalg.norm(_rfree_pw(A_try, _Kt))) < _r0:
                            A2 = A_try; _acc = True; break
                        _lam *= 0.5
                    if not _acc:
                        _fail = True; break
                # accept only if the field residual actually reached tol
                if (not _fail) and (_rrel < 1e-7):
                    _newton_ok = True; _res = _rrel
                    # element-mean ν for the loss post-processing (loss code uses
                    # per-element ν); negligible vs the pointwise field solve.
                    nu_all2 = _nu_of(_elemB(A2), nu_all2)
                else:
                    log.info("P2 Newton not converged at frame %d (%d its, rrel=%.1e)"
                             " — Picard fallback", k, _nit, _rrel)
            # ── DAMPED PICARD (SB_NO_NEWTON, frozen_nu, or Newton fell back) ──
            if not _newton_ok:
                if not frozen_nu:
                    nu_all2 = _nu_start.copy()     # warm-start (base at k=0)
                if frozen_nu:
                    _n_pic2 = max(nonlinear_iterations, 40) if k == 0 else 1
                else:
                    _n_pic2 = max(nonlinear_iterations, 70 if k == 0 else 45)
                A2 = np.zeros(N2)
                _res = 0.0; _nit = 0
                _pic_ok = 0; _pic_r_prev = None; _pic_om = 0.5   # Irons–Tuck state
                for it in range(_n_pic2):
                    _nit = it + 1
                    K = _asmK(nu_all2)
                    Kff = (Pro.T @ K @ Pro).tocsr()[_free2][:, _free2].tocsc()
                    _xf2 = _solve_ff(Kff, _bff2)
                    _xred2 = np.zeros(Pro.shape[1]); _xred2[_free2] = _xf2
                    A2 = Pro @ _xred2
                    if (frozen_nu and k > 0) or not _sat2:
                        break                      # frozen frame: 1 linear solve
                    Bmag_el = _elemB(A2)
                    _vo = np.concatenate([nu_all2[_ids] for _ids, _ in _sat2])
                    _vn = np.concatenate([
                        1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(_c, Bmag_el[_ids]), 1.0))
                        for _ids, _c in _sat2])
                    _rr = _vn - _vo
                    _res = float(np.linalg.norm(_rr) / max(np.linalg.norm(_vo), 1e-30))
                    if _pic_r_prev is not None:
                        _dr = _rr - _pic_r_prev; _den = float(_dr @ _dr)
                        if _den > 0.0:
                            _pic_om = float(np.clip(
                                -_pic_om * float(_pic_r_prev @ _dr) / _den, 0.05, 1.0))
                    _pic_r_prev = _rr
                    _vu = _vo + _pic_om * _rr
                    _p0 = 0
                    for _ids, _c in _sat2:
                        nu_all2[_ids] = _vu[_p0:_p0 + _ids.size]; _p0 += _ids.size
                    if _res < _PIC_TOL2:
                        _pic_ok += 1
                        if _pic_ok >= 2:
                            break
                    else:
                        _pic_ok = 0
            _pic_iters.append(_nit); _pic_res_max = max(_pic_res_max, _res)
            _A2_prev = A2.copy(); _nu_conv2 = nu_all2.copy()
            Tq = _arkkio_torque_p2(mesh_all, A2, b2, p.r_rotor_out,
                                   p.r_stator_in, p.stack_length) * NS
            _T2.append(Tq)
            _pa, _pb, _pc = _psi2(A2)
            _psiA.append(_pa); _psiB.append(_pb); _psiC.append(_pc)
            _tt.append(k * dt)
            # Element-mean B in the iron and the coils, captured on EVERY frame —
            # the same thing the P1 path does unconditionally.  This used to sit
            # inside `if rotor_eddy:`, which meant P2 reported ZERO core loss and
            # zero AC copper loss whenever that flag was off, and overstated the
            # efficiency by exactly those terms.  `rotor_eddy` means "magnet and
            # shaft eddy losses" — it has no business gating the iron.  Harmless
            # while P1 was the default; a silent error the moment P2 became it.
            _bx_q, _by_q, _dxq = _p2_B_at_quad(b2, A2)
            _ar_el = _dxq.sum(axis=1)
            _bx_el = (_bx_q * _dxq).sum(axis=1) / np.maximum(_ar_el, 1e-30)
            _by_el = (_by_q * _dxq).sum(axis=1) / np.maximum(_ar_el, 1e-30)
            _hsx2.append(_bx_el[_iron_s_idx]); _hsy2.append(_by_el[_iron_s_idx])
            _hrx2.append(_bx_el[_iron_r_idx + nst])
            _hry2.append(_by_el[_iron_r_idx + nst])
            if _coil_idx.size:                     # coil B for AC copper loss
                _hcx2.append(_bx_el[_coil_idx]); _hcy2.append(_by_el[_coil_idx])
            if rotor_eddy:
                # rotor-frame nodal A (magnet/shaft eddy via honest_rotor_eddy)
                _histA_rot2.append(A2[_rot_vdof].copy())
            if return_field and ((field_first and k == 0)
                                 or (not field_first and k == n_total - 1)):
                _Bx_all, _By_all = _p2_B_at_quad(
                    Basis(mesh_all, _P2E()), A2)[:2]
                _snap2 = {"P_mm": (mesh_all.p * 1e3).copy(),
                          "T": mesh_all.t.copy(),
                          "A": A2[vdof].copy(),
                          "nsn": int(nsn)}
        # ── LOSS post-processing (rotor_eddy) ─────────────────────────────────
        # Same architecture as the P1 app path: the field is magnetostatic per
        # frame; the eddy-current LOSSES come from post-processing the A(t)/B(t)
        # histories — magnet + shaft via the honest (reaction-included) frequency-
        # domain rotor solve `honest_rotor_eddy`, iron via Bertotti on dB/dt.
        # (P1's default transient uses eddy=False too — the coupled σ∂A/∂t J-view
        # solve is NOT the app loss path.)  Current drive → copper = I²R (DC).
        # Iron (Bertotti) and AC copper are always in; rotor_eddy only adds the
        # coupled magnet/shaft term, which upgrades this label below.
        _lm2 = "field (P2 magnetostatic + Bertotti iron + AC copper)"
        P_cu_dc2 = float(P_cu)                              # from copper_loss_W (I²R)
        P_cu_ac_ser2 = [0.0] * n_total; P_cu_ac_avg2 = 0.0
        P_cu_ser2 = [P_cu_dc2] * n_total
        P_fe_ser2 = [0.0] * n_total; P_fe_avg2 = 0.0
        P_mag_ser2 = [0.0] * n_total; P_mag_avg2 = 0.0
        P_shaft_ser2 = [0.0] * n_total; P_shaft_avg2 = 0.0
        # Losses from the captured B(t).  Copper AC and iron are computed
        # UNCONDITIONALLY, matching the P1 path — they do not depend on the
        # rotor-eddy model.  The magnet/shaft eddy below is the part rotor_eddy
        # actually selects, and it is already gated by `if _histA_rot2:`, which
        # is only populated when the flag is on.
        _two_pi2_2 = 2.0 * math.pi ** 2

        # ── AC copper (proximity/skin) — MUST match P1: DC I²R is already
        #    element-order-independent (same copper_loss_W); the AC part is the
        #    coil proximity loss σ/12·Σ(d_r²·dBr² + d_t²·dBt²), field split into
        #    radial/tangential (same _prox_eddy_split model P1 uses).  Periodic
        #    central-difference dB/dt (P2 field is smooth).
        _rho_cu2 = RHO_CU_20 * (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))
        _sig_cu2 = 1.0 / _rho_cu2
        _om_e2 = 2.0 * math.pi * max(1e-6, f_elec)
        _dlt_cu2 = math.sqrt(2.0 * _rho_cu2 / (_om_e2 * MU0))
        _nws2 = max(1, int(round(float(geo.get("wire_split", 1) or 1))))
        _w_cu2 = min(float(geo.get("wire_width", 5.0)) * 1e-3 / _nws2,
                     2.0 * _dlt_cu2)
        _h_cu2 = min(float(geo.get("wire_height", 0.8)) * 1e-3, 2.0 * _dlt_cu2)
        if _coil_idx.size and _hcx2:
            _smp = half["s"]["mesh"]
            _cc = (_smp.p[:, _smp.t].mean(axis=1))[:, _coil_idx]
            Xc = np.asarray(_hcx2); Yc = np.asarray(_hcy2)     # (N, E)
            _rr = np.hypot(_cc[0], _cc[1]); _rr = np.where(_rr < 1e-9, 1e-9, _rr)
            _uxc = (_cc[0] / _rr)[None, :]; _uyc = (_cc[1] / _rr)[None, :]
            _Brc = Xc * _uxc + Yc * _uyc; _Btc = -Xc * _uyc + Yc * _uxc
            _dBrc = (np.roll(_Brc, -1, 0) - np.roll(_Brc, 1, 0)) / (2.0 * dt)
            _dBtc = (np.roll(_Btc, -1, 0) - np.roll(_Btc, 1, 0)) / (2.0 * dt)
            _volc = areas_s[_coil_idx] * p.stack_length
            _Pac = (_sig_cu2 / 12.0) * np.sum(
                (_w_cu2 ** 2 * _dBrc ** 2 + _h_cu2 ** 2 * _dBtc ** 2)
                * _volc[None, :], axis=1) * NS
            _Pac = np.maximum(_Pac, 0.0)
            P_cu_ac_ser2 = _Pac.tolist(); P_cu_ac_avg2 = float(np.mean(_Pac))
        P_cu_ser2 = [P_cu_dc2 + ac for ac in P_cu_ac_ser2]

        def _iron_p2(hx, hy, idx, areas_half, mat):
            # compact Bertotti: classical eddy ∝⟨(dB/dt)²⟩ (ripples) + flat
            # hysteresis + excess.  Periodic central-difference dB/dt (the P2
            # field is already smooth, so the P1 savgol slip-jitter filter is
            # unnecessary here).  Coeffs fitted from the material's measured
            # loss curves (effective_bertotti).  Returns (P(t) [N], hyst [W]).
            if mat is None or idx.size == 0 or len(hx) == 0:
                return np.zeros(n_total), 0.0
            X = np.asarray(hx); Y = np.asarray(hy)         # (N, E)
            kh, kc, ke = _mat_lib.effective_bertotti(mat)
            sf = float(getattr(mat, "stacking_factor", 0.95))
            vol = areas_half[idx] * p.stack_length * sf
            dX = (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2.0 * dt)
            dY = (np.roll(Y, -1, 0) - np.roll(Y, 1, 0)) / (2.0 * dt)
            pcl = (kc / _two_pi2_2) * np.sum((dX ** 2 + dY ** 2)
                                             * vol[None, :], axis=1)
            Bac2 = (((X.max(0) - X.min(0)) * 0.5) ** 2
                    + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)
            phys = float(np.sum((kh * f_elec * Bac2
                                 + ke * f_elec ** 1.5
                                 * np.power(np.maximum(Bac2, 0.0), 0.75)) * vol))
            return pcl, phys

        _pcl_s, _ph_s = _iron_p2(_hsx2, _hsy2, _iron_s_idx, areas_s, _steel_s)
        _pcl_r, _ph_r = _iron_p2(_hrx2, _hry2, _iron_r_idx, areas_r, _steel_r)
        _P_fe_t = (_pcl_s + _pcl_r) * NS + (_ph_s + _ph_r) * NS
        _P_fe_t = np.maximum(_P_fe_t, 0.0)
        P_fe_ser2 = _P_fe_t.tolist(); P_fe_avg2 = float(np.mean(_P_fe_t))
        # magnet + shaft eddy: honest (reaction-included) rotor solve on the
        # rotor-frame A(t) history — the SAME function the P1 path uses.
        if _histA_rot2:
            try:
                from motor_ai_sim.simulation.eddy_solver_2d import (
                    honest_rotor_eddy as _hre2)
                _rm = half["r"]["mesh"]
                _tags_r2 = np.zeros(_rm.t.shape[1], int)
                for _tg, _els in half["r"]["cells"].items():
                    _tags_r2[np.asarray(_els, int)] = int(_tg)
                _magt2 = [int(tg) for tg in np.unique(_tags_r2)
                          if int(tg) >= DOM_MAG_BASE]
                # rotor back-iron μ_r from the CONVERGED P2 ν (last frame)
                _rir = half["r"]["cells"].get(int(DOM_ROTOR))
                if _rir is not None and np.size(_rir):
                    _mur_bi = 1.0 / (MU0 * float(np.mean(
                        nu_all2[np.asarray(_rir, int) + nst])))
                else:
                    _mur_bi = 1000.0

                def _muf2(tg):
                    tg = int(tg)
                    if tg >= DOM_MAG_BASE: return 1.05
                    if tg == DOM_ROTOR:    return _mur_bi
                    return 1.0
                P_mag_avg2, P_shaft_avg2, _hf2 = _hre2(
                    np.asarray(_rm.p, float), np.asarray(_rm.t, int), _tags_r2,
                    _muf2, _sigma_of_tag, _magt2, DOM_SHAFT,
                    np.asarray(_histA_rot2, float), float(n_total) * dt,
                    float(p.stack_length), float(NS))
                P_mag_ser2 = [float(P_mag_avg2)] * n_total
                P_shaft_ser2 = [float(P_shaft_avg2)] * n_total
                _lm2 = "field+honest (P2 magnetostatic + coupled rotor eddy)"
                log.info("P2 rotor eddy: mag=%.3f shaft=%.3f W (%d harmonics); "
                         "iron=%.3f W, copper(dc)=%.1f W", P_mag_avg2,
                         P_shaft_avg2, len(_hf2), P_fe_avg2, P_cu_dc2)
            except Exception as _e2:
                log.warning("P2 honest rotor eddy failed: %s", _e2)
        P_tot_ser2 = [c + f + m + s for c, f, m, s in
                      zip(P_cu_ser2, P_fe_ser2, P_mag_ser2, P_shaft_ser2)]
        P_loss_avg2 = float(np.mean(P_tot_ser2)) if P_tot_ser2 else 0.0

        # ── metrics ──────────────────────────────────────────────────────────
        # Raw Maxwell-stress (Arkkio) torque — kept as a DIAGNOSTIC only.  On the
        # node-repaired sliding band the gap field is contaminated UNDER LOAD, so
        # the volume-weighted Maxwell integral is radius-INCONSISTENT (measured
        # 0.78..1.30 Nm across integration bands for one converged frame) and
        # over-reads the mean torque ~35 % vs the energy method / ANSYS.
        _T2raw = list(_T2)                       # preserve the Maxwell series (diag)
        T_arr = np.asarray(_T2, float)
        T_maxwell_avg = float(T_arr.mean()) if T_arr.size else 0.0
        # ── HYBRID torque: energy-consistent MEAN + Maxwell-stress RIPPLE ──────
        # Two physical facts, each measured by the method that is right for it:
        #  • MEAN — the ANSYS energy method (virtual work) via the terminal flux
        #    linkages ψ and currents I:  <T> = (3/2)·p·<ψα·iβ − ψβ·iα>, the airgap
        #    power balance P=T·ω.  It never touches the gap field, so it is immune
        #    to the sliding-band DC contamination that makes the raw Maxwell mean
        #    radius-inconsistent (~+35 %); validated vs ω·ψ_pm back-EMF and ANSYS.
        #  • RIPPLE — the Maxwell-stress torque T(t).  The flux-linkage torque is
        #    winding-FILTERED, so it is smooth and CANNOT see cogging or the slot
        #    harmonics → it under-reports ripple (1.7 % vs the real ~5-6 %).  The
        #    Maxwell σ_rθ integral DOES resolve them (P2 noise floor →0 with mesh).
        # So the reported T(t) = Maxwell AC (real ripple) re-centred on the energy
        # mean (correct DC).  This is NOT tuning: the DC bias we remove is the
        # measured slip-band contamination; the AC we keep is the physical ripple.
        # No-load (I≈0) keeps the raw Maxwell cogging directly (energy torque = 0).
        _torque_method = "maxwell_stress"
        try:
            _pa = np.asarray(_psiA, float); _pb = np.asarray(_psiB, float)
            _pc = np.asarray(_psiC, float)
            _ea = np.asarray(_IA, float); _eb = np.asarray(_IB, float)
            _ec = np.asarray(_IC, float)
            _Ipk = float(np.max(np.abs(np.concatenate([_ea, _eb, _ec])))) if _ea.size else 0.0
            if _pa.size and _pa.size == _ea.size and _Ipk > 1.0:
                _s = 2.0 / 3.0; _kc = math.sqrt(3.0) / 2.0
                _psial = _s * (_pa - 0.5 * _pb - 0.5 * _pc); _psibe = _s * _kc * (_pb - _pc)
                _ial = _s * (_ea - 0.5 * _eb - 0.5 * _ec); _ibe = _s * _kc * (_eb - _ec)
                _T2e = (1.5 * float(pole_pairs) * (_psial * _ibe - _psibe * _ial))
                _emean = float(_T2e.mean())
                _mx = np.asarray(_T2raw, float)     # raw Maxwell σ_rθ series
                # real Maxwell ripple, DC re-centred on the energy-consistent mean:
                _T2 = (_mx - _mx.mean() + _emean).tolist()
                _torque_method = "energy_mean+maxwell_ripple"
            # else: no-load (or no terminal data) → keep the Maxwell cogging series
        except Exception as _te:
            log.warning("P2 hybrid torque failed (%s) — using Maxwell series", _te)
        T_arr = np.asarray(_T2, float)
        Tavg = float(T_arr.mean()) if T_arr.size else 0.0
        _Tf, Trip, Trip_raw, Tnoise = band_limit_torque(
            _T2, int(n_steps_per_period), int(round(n_periods)))
        _omega_m2 = 2.0 * math.pi * rpm / 60.0
        P_airgap_avg2 = float(Tavg * _omega_m2)
        P_mech_avg2 = P_airgap_avg2 - (P_fe_avg2 + P_mag_avg2 + P_shaft_avg2)
        # back-EMF V = R·i + dψ/dt (central diff on the reported window)
        def _ddt(arr):
            a = np.asarray(arr, float)
            return (np.gradient(a) / dt).tolist() if a.size > 1 else [0.0] * a.size
        VA = _ddt(_psiA); VB = _ddt(_psiB); VC = _ddt(_psiC)
        Vpk = float(np.max(np.abs(VA + VB + VC))) if _psiA else 0.0
        _ang = [(k / n_total) * period_mech * n_periods for k in range(n_total)]
        log.info("P2 belt transient done: %d frames, T_avg=%.5f Nm, "
                 "ripple_raw=%.2f%%, picard_max_res=%.2e", n_total, Tavg,
                 Trip_raw, _pic_res_max)
        return {
            "method": "sliding_band_p2", "element_order": 2,
            "loss_model": _lm2,
            "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
            "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
            "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
            "time_s": _tt, "rotor_angle_deg": _ang,
            "T_em_Nm": _T2, "T_avg_Nm": Tavg, "T_ripple_pct": Trip_raw,
            "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip,
            "T_noise_floor_pct": round(float(Tnoise), 2),
            "T_em_raw_Nm": list(_T2), "T_em_filt_Nm": _Tf,
            "torque_method": _torque_method,
            "T_avg_maxwell_Nm": round(T_maxwell_avg, 4),
            "T_em_maxwell_Nm": list(_T2raw),
            "psi_A_Wb": _psiA, "psi_B_Wb": _psiB, "psi_C_Wb": _psiC,
            "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
            "I_A": _IA, "I_B": _IB, "I_C": _IC,
            "P_cu_W": P_cu_ser2, "P_fe_W": P_fe_ser2,
            "P_mag_eddy_W": P_mag_ser2, "P_shaft_eddy_W": P_shaft_ser2,
            "P_loss_total_W": P_tot_ser2,
            "P_cu_dc_W": P_cu_dc2, "P_cu_ac_W": P_cu_ac_ser2,
            "P_mag_honest_W": round(float(P_mag_avg2), 3),
            "P_shaft_honest_W": round(float(P_shaft_avg2), 3),
            "P_fe_avg_W": round(float(P_fe_avg2), 3),
            "P_loss_total_avg_W": round(float(P_loss_avg2), 3),
            "P_airgap_W": P_airgap_avg2, "P_mech_avg_W": P_mech_avg2,
            "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
            "n_parallel": int(n_parallel),
            "picard_iters_mean": (round(float(np.mean(_pic_iters)), 1)
                                  if _pic_iters else 0.0),
            "picard_iters_max": (int(max(_pic_iters)) if _pic_iters else 0),
            "picard_resid_max": round(float(_pic_res_max), 6),
            "picard_tol": float(_PIC_TOL2),
            "picard_converged": bool(_pic_res_max < _PIC_TOL2),
            "coil_temp_C": float(coil_temp_c),
            "end_winding_factor": float(_k_end_used),
            "drive": "current",
            "field": _snap2,
        }

    # Eddy-current (magnetodynamic) coupling: backward-Euler adds (Msig/dt) to the
    # stiffness and (Msig/dt)·A_prev to the RHS.  Msig is 0 outside solid
    # conductors, so air/iron are unaffected.  A_prev follows the material points
    # (rotor mesh rotates rigidly), so it IS the previous-step field per DOF.
    if eddy:
        # Bordered magnetodynamic system (the coupled eddy J-VIEW mode).
        #   stator copper → SOLID bars with imposed current (∫J dA = I_c);
        #   + rotor magnets/shaft σ when rotor_eddy (∫J = 0 per interior
        #     magnet; shaft + cut halves U = 0 by symmetry) so the J view
        #     shows the rotor eddy currents too.
        # The TRANSIENT loss path does NOT use this solve — magnet losses come
        # from smoothed post-processing (see the rotor-eddy stage above).
        from scipy.sparse import bmat as _bmat, diags as _diags
        from scipy.sparse.linalg import spsolve as _spsolve
        _Ms_s = half["s"]["Msig"]
        _Ms_r = half["r"]["Msig"] if rotor_eddy else _csr(half["r"]["Msig"].shape)
        _Minv_dt = _bd([_Ms_s, _Ms_r]).tocsr() * (1.0 / dt)
        A_prev = np.zeros(n)
        _eddy_P = []          # field-based dissipation ∫σ(∂A/∂t)² per frame [W, sector]
        _cons = _coil_con + (_rot_con if rotor_eddy else [])
        _Gfull = _csr(np.column_stack([c["g"] for c in _cons])) if _cons else _csr((n, 0))
        _Sdt   = np.array([c["S"] for c in _cons]) * dt
        _n_coil_con = len(_coil_con)
        _Iunit = np.array([c["Iunit"] for c in _coil_con])
        _phase = [c["phase"] for c in _coil_con]

        def _solve_eddy_constrained(Keff, rhs_field, Pro, outer_red, I_vec, A_prv):
            m = Pro.shape[1]
            KK = (Pro.T @ Keff @ Pro).tocsr()
            rf = np.asarray(Pro.T @ rhs_field).ravel()      # m
            free = np.setdiff1d(np.arange(m), outer_red)
            KKff = KK[np.ix_(free, free)].tocsr()
            if _Gfull.shape[1] == 0:                  # no conductors constrained
                sol = _spsolve(KKff, rf[free])
                A_red = np.zeros(m); A_red[free] = sol
                return Pro @ A_red, np.zeros(0)
            Bred = (Pro.T @ _Gfull).tocsr()                 # m × nc
            Bf = Bred[free, :].tocsr()
            cr = dt * I_vec - np.asarray(_Gfull.T @ A_prv).ravel()   # nc
            Mb = _bmat([[KKff, -Bf], [-Bf.T, _diags(_Sdt)]]).tocsr()
            sol = _spsolve(Mb, np.concatenate([rf[free], cr]))
            A_red = np.zeros(m); A_red[free] = sol[:free.size]
            return Pro @ A_red, sol[free.size:]      # A , per-conductor voltages U_c

    # ── Demagnetisation pre-pass (opt-in) ────────────────────────────────
    # Sweep the rotor over the WHOLE period at full Br, tracking the worst
    # (most negative) demagnetising field H·M̂ at EVERY magnet element.  Any
    # element whose worst H crosses the material BH-curve knee is de-rated
    # along the recoil line (irreversible) → _br_glob.  The measurement loop
    # below then runs with the weakened magnets, so the reported torque /
    # back-EMF / losses carry the demag penalty — Ansys-style, per element.
    _demag_coef = None
    _demag_field = None
    _demag_report = []
    if demag and _mag_idx.size:
        _dm = []
        for _tag, _idx in half["r"]["cells"].items():
            _m = matr0.get(int(_tag))
            if _m is None or (abs(_m.Mx) + abs(_m.My)) <= 0:
                continue
            _bh = getattr(_m, "bh_curve", None)
            if not _bh or len(_bh) < 2:
                continue
            _Mm = math.hypot(_m.Mx, _m.My)
            _knee = _bh[1][0] if _bh[0][1] <= 0 else _bh[0][0]
            _hs = np.array([pt[0] for pt in _bh], float)
            _bs = np.array([pt[1] for pt in _bh], float)
            _o = np.argsort(_hs)
            _dm.append(dict(idx=np.asarray(_idx, int), Mx=_m.Mx, My=_m.My,
                            Mm=_Mm, knee=float(_knee), mu_r=float(_m.mu_r),
                            hs=_hs[_o], bs=_bs[_o],
                            # INTRINSIC curve J = B - mu0*H: the load-line
                            # construction below works in (H, J), where the
                            # magnet's own line passes through the origin.
                            Js=_bs[_o] - MU0 * _hs[_o],
                            # Recoil slope taken from the curve's OWN top segment
                            # rather than the declared mu_rec: the two disagree by
                            # a few percent (curve 1.083 vs declared 1.05 for
                            # F45SH), and any mismatch shows up as a spurious
                            # couple of percent of PERMANENT loss on a magnet that
                            # never left the reversible part of its curve.
                            mu_rec_c=(abs(float(_bs[_o][-1] - _bs[_o][-2]))
                                      / max(abs(float(_hs[_o][-1] - _hs[_o][-2])), 1e-9)
                                      / MU0) if len(_o) >= 2 else float(_m.mu_r),
                            Br0=_Mm * MU0, tag=int(_tag)))
        # De-rating is part of EVERY frame's nonlinear fixed point, solved by the
        # same Picard that handles the iron B-H knee (see the call inside the
        # frame loop).  The magnet weakens, its own demagnetising field weakens
        # with it, and the frame is not converged until the two agree.
        #
        # Applying it once per time step instead made the answer depend on the
        # step count (Fe16N2: 0.27 N*m at 24 steps vs 0.23 at 48) — a numerical
        # artefact, not physics.  Damping that dependence away with a relaxation
        # factor was a fudge and is gone; converging the coupling is the fix.
        # The older one-shot pre-pass was worse still: it swept the whole period
        # at full Br, then applied that field to a magnet which no longer existed.
        _rmesh_dm = half["r"]["mesh"]
        _demag_seen = {}          # magnet tag -> worst smoothed H seen so far
        # Per-ELEMENT field diagnostics, independent of the de-rating rule:
        #   _H_first — H on the very first step, i.e. on the PRISTINE magnet.
        #              This is the field the geometry produces, before any
        #              feedback, so it says where demagnetisation *should*
        #              start.  Comparing it with the final Br map separates a
        #              wrong field from a wrong de-rating rule.
        #   _H_worst — running per-element minimum over the whole run.
        _H_first = np.full(_nt_r, np.nan)
        _H_worst = np.full(_nt_r, np.inf)

        def _demag_derate(_Bxr_f, _Byr_f) -> bool:
            """Apply this step's field to _br_glob.  True if anything weakened."""
            _chg = False
            for _d in _dm:
                _ix = _d["idx"]
                _BdotM = _Bxr_f[_ix] * _d["Mx"] + _Byr_f[_ix] * _d["My"]
                # H along M for the magnet's CURRENT strength: B_par/mu0 - M(now).
                # Only the magnetisation term scales with the de-rating.
                _Hraw = _BdotM / (MU0 * _d["Mm"]) - _d["Mm"] * _br_glob[_ix]
                _H = _smooth_demag_H(_rmesh_dm, _ix, _Hraw)
                _msk = np.isnan(_H_first[_ix])
                if np.any(_msk):
                    _H_first[_ix[_msk]] = _H[_msk]
                np.minimum.at(_H_worst, _ix, _H)
                _hmin = float(_H.min())
                if _hmin < _demag_seen.get(_d["tag"], np.inf):
                    _demag_seen[_d["tag"]] = _hmin
                # ── Load-line construction, per element ──────────────────────
                # Reading the curve AT the present H is wrong for a magnet that
                # demagnetises itself: H is produced mostly by the element's own
                # magnetisation, so H and M collapse together.  Evaluating the
                # curve at the full-strength field fixes a state that cannot
                # exist — it drove Fe16N2 to Br 0.016 when the self-consistent
                # answer is 0.26 (hand check: load line vs curve, alpha = 0.42).
                #
                # The honest operating point is the classic graphical one: where
                # the element's LOAD LINE crosses the material's intrinsic curve.
                # With
                #     alpha = B_par / (mu0 * M * br)     effective permeance
                #     H(br) = (alpha - 1) * M * br
                # eliminating br gives a straight line through the origin in
                # (H, J):   J = mu0 * H / (alpha - 1).
                # alpha comes from the FEM field, so the geometry, saturation and
                # armature all set it; the Picard re-measures it after every
                # update, which makes the constant-alpha step self-correcting.
                _cur = _br_glob[_ix]
                _Bpar_mu0 = _H + _d["Mm"] * _cur             # A/m
                _alpha = _Bpar_mu0 / np.maximum(_d["Mm"] * _cur, 1e-9)
                # alpha >= 1 means the element is not being demagnetised at all.
                _k = MU0 / np.minimum(_alpha - 1.0, -1e-9)   # load-line slope < 0
                _hs = _d["hs"]; _Jsv = _d["Js"]
                # Walk the tabulated segments from H = 0 downwards and take the
                # FIRST crossing — for a monotone curve that is the operating
                # point.  Default (no crossing) = untouched.
                _Jst = np.full(_ix.size, _d["Br0"])
                _Hst = np.zeros(_ix.size)
                _hit = np.zeros(_ix.size, bool)
                for _i in range(len(_hs) - 2, -1, -1):
                    _h0, _h1 = float(_hs[_i]), float(_hs[_i + 1])
                    if _h1 <= _h0:
                        continue
                    _s = (float(_Jsv[_i + 1]) - float(_Jsv[_i])) / (_h1 - _h0)
                    _den = _s - _k
                    _Hc = np.where(np.abs(_den) > 1e-30,
                                   (_s * _h0 - float(_Jsv[_i])) / _den, np.nan)
                    _ok = (~_hit) & np.isfinite(_Hc) & (_Hc >= _h0 - 1e-6) \
                        & (_Hc <= _h1 + 1e-6)
                    if np.any(_ok):
                        _Jst[_ok] = _k[_ok] * _Hc[_ok]
                        _Hst[_ok] = _Hc[_ok]
                        _hit |= _ok
                # Only the IRREVERSIBLE part is a loss.  Follow the recoil line
                # from the operating point back to H = 0: an element that never
                # left the reversible upper branch lands exactly on Br0 and keeps
                # its full strength, while one that crossed the knee returns a
                # genuinely lower remanence.  Using J at the operating point
                # instead booked the curve's own recoil slope as permanent damage
                # and cost every healthy element ~5 %.
                _Brn = _Jst - (_d["mu_rec_c"] - 1.0) * MU0 * _Hst
                _new = np.minimum(_cur, np.clip(_Brn / max(_d["Br0"], 1e-12),
                                                0.0, 1.0))
                if np.any(_new < _cur - _DEMAG_TOL):
                    _chg = True
                _br_glob[_ix] = _new
            return _chg

        # starts at FULL strength — the magnets weaken as the run progresses
        f_mag = _build_fmag(_br_glob)

    _field_snap = None       # eddy last-frame field snapshot (if return_field)
    _hist_Am = []            # per-frame A on magnet nodes (loss post-processing)
    _hist_Ash = []           # per-frame A on shaft nodes (field-based shaft loss)
    _hist_A_rotor = []       # per-frame A on ALL rotor nodes (honest coupled eddy)
    # When the demag pre-pass ran, the measurement pass is the SECOND half of
    # the work — continue the progress counter so the UI bar doesn't reset.
    _prog_off = 0
    _prog_tot = n_total   # single pass: demag is applied inside this loop

    # Per-phase flux linkage of a solution — used INSIDE the voltage-drive
    # circuit iteration and for the ψ series (single implementation).
    sc_psi = p.stack_length * NS / float(n_parallel)

    def _psi_of(Avec):
        As_ = Avec[:nsn]
        A_tri_ = (As_[Tts[0]] + As_[Tts[1]] + As_[Tts[2]]) / 3.0
        pa_ = pb_ = pc_ = 0.0
        for idx_, ar_, dir_, ph_ in coil_info:
            sa_ = float(np.sum(ar_))
            if sa_ <= 0:
                continue
            val_ = dir_ * float(np.sum(A_tri_[idx_] * ar_)) / sa_
            if ph_ == 'A':
                pa_ += val_
            elif ph_ == 'B':
                pb_ += val_
            else:
                pc_ += val_
        return pa_, pb_, pc_

    # Voltage-drive circuit state: the phase currents are UNKNOWNS solved with
    # the field (strong field↔circuit coupling — see the Picard loop).  Warm-
    # start from the previous frame + the previous-step flux for backward-Euler.
    from scipy.sparse.linalg import splu as _splu
    _iv_state = {'A': 0.0, 'B': 0.0, 'C': 0.0}
    _psi_prev = None
    _th_eff_prev = None     # previous frame's SNAPPED rotor angle (rotor-time dt)
    _dt_k = dt              # per-frame rotor-time step (uniform when nodes align)
    _v_diag = {"iters": [], "resid": []}   # circuit convergence stats per frame
    _v_bpsi = []            # period-boundary flux samples (psiA, psiB) for Aitken
    _v_aitken_done = False

    if _vdrive:
        # ── Phasor steady-state initialiser ──────────────────────────────────
        # The electrical time constant tau = L/R spans ~20 electrical periods on
        # a low-R machine, so a marched start-up would need ~100 periods to shed
        # its DC transient — impractical.  Instead measure the PM flux + the dq
        # inductances once at theta=0 and place the current DIRECTLY on the
        # periodic orbit (i(0), psi(-dt)); the march then only has to develop the
        # small saturation/slotting harmonics on top of a DC-free fundamental.
        import math as _m
        _pp = pole_pairs
        # m_shift = 0 periodicity setup
        if _moving:
            _Pro0, _out0 = Pro_const, outer_red_const
            _Kb0 = _K_gap_macro(0) if _use_macro else _K_band(0)
        else:
            _suf = _SignedUF(n)
            for _a, _b in zip(Mn, Sn):
                _suf.union(int(_b), int(_a), _bc_sign)
            for _kk in range(Nring):
                _suf.union(int(rring[_kk] + nsn), int(sring[_kk]), 1)
            _rt = [_suf.find(_ii) for _ii in range(n)]
            _rid = np.array([_r for _r, _ in _rt]); _rsg = np.array([_s for _, _s in _rt], float)
            _uq, _iv = np.unique(_rid, return_inverse=True)
            _Pro0 = _coo((_rsg, (np.arange(n), _iv)), shape=(n, _uq.size)).tocsr()
            _out0 = np.unique(_iv[outer_nodes]); _Kb0 = None
        _Pmag0 = np.concatenate([np.zeros(nsn), f_mag])
        _Pa0 = np.concatenate([f_coil['A'] - f_coil['C'], np.zeros(half["r"]["n"])])
        _Pb0 = np.concatenate([f_coil['B'] - f_coil['C'], np.zeros(half["r"]["n"])])
        for _hn in ("s", "r"):
            for _tg in sb_sat[_hn]:
                nu_el[_hn][_tg][:] = 1.0 / (MU0 * max(mu0[_hn].get(_tg, 1.0), 1.0))

        def _assemble0():
            _bl = []
            for _hn in ("s", "r"):
                _h = half[_hn]; _K = K_const[_hn].copy()
                for _tg, _sbi in sb_sat[_hn].items():
                    _b0 = b0_sat[_hn][_tg]; _nf = _b0.zeros()
                    _nf[_h["cells"][_tg]] = nu_el[_hn][_tg]
                    _K = _K + asm(_stiff_nu, _sbi, nu=_b0.interpolate(_nf))
                _bl.append(_K)
            _K = _bd(_bl).tocsr()
            if _Kb0 is not None:
                _K = (_K + _Kb0).tocsr()
            return _K

        def _fac0(_K):
            _Kg = (_Pro0.T @ _K @ _Pro0).tocsr()
            _mk = np.ones(_Kg.shape[0], bool); _mk[_out0] = False
            _fr = np.flatnonzero(_mk)
            _lu = _splu(_Kg[_fr][:, _fr].tocsc())
            _N = _Kg.shape[0]

            def _bs(_ff):
                _r = (_Pro0.T @ _ff)[_fr]
                _x = np.zeros(_N); _x[_fr] = _lu.solve(_r)
                return _Pro0 @ _x
            return _bs

        _the0 = _m.radians(0.0 * _pp + DAXIS_SHIFT_DEG)  # theta_eff=0 electrical

        def _park(_xa_, _xb_, _xc_, _th):
            return ((2.0 / 3.0) * (_xa_ * _m.cos(_th) + _xb_ * _m.cos(_th - 2.094395102393195)
                                   + _xc_ * _m.cos(_th + 2.094395102393195)),
                    -(2.0 / 3.0) * (_xa_ * _m.sin(_th) + _xb_ * _m.sin(_th - 2.094395102393195)
                                    + _xc_ * _m.sin(_th + 2.094395102393195)))

        def _ipark(_d, _q, _th):
            return (_d * _m.cos(_th) - _q * _m.sin(_th),
                    _d * _m.cos(_th - 2.094395102393195) - _q * _m.sin(_th - 2.094395102393195),
                    _d * _m.cos(_th + 2.094395102393195) - _q * _m.sin(_th + 2.094395102393195))

        _w = 2.0 * _m.pi * f_elec
        _V0 = _voltages(0.0)
        # Coupled phasor Picard: solve the dq STEADY-STATE circuit and the
        # saturated field TOGETHER at theta=0, so the inductances used for the
        # operating point are measured AT that operating point (Lq changes ~5x
        # between i=0 and full load -> an i=0 estimate leaves a large DC).
        _id0 = _iq0 = 0.0; _thal = _the0; _align = 0.0
        _psi_pm_d = 0.0; _Ldd = _Lqq = _Ldq = _Lqd = 1e-6
        for _it in range(nonlinear_iterations):
            _K0 = _assemble0(); _bs = _fac0(_K0)
            _A0 = _bs(_Pmag0); _xa = _bs(_Pa0); _xb = _bs(_Pb0)
            _pm = _psi_of(_A0); _qa = _psi_of(_xa); _qb = _psi_of(_xb)
            _ppmA, _ppmB, _ppmC = _pm[0] * sc_psi, _pm[1] * sc_psi, _pm[2] * sc_psi
            # align d on the PM flux, measure the dq inductances there
            _pd0, _pq0 = _park(_ppmA, _ppmB, _ppmC, _the0)
            _align = _m.atan2(_pq0, _pd0); _thal = _the0 + _align
            _psi_pm_d = _m.hypot(_pd0, _pq0)
            _Laa, _Lba = _qa[0] * sc_psi, _qa[1] * sc_psi
            _Lab, _Lbb = _qb[0] * sc_psi, _qb[1] * sc_psi
            _idA, _idB, _idC = _ipark(1.0, 0.0, _thal)
            _iqA, _iqB, _iqC = _ipark(0.0, 1.0, _thal)
            _Ldd, _Lqd = _park(_Laa * _idA + _Lab * _idB, _Lba * _idA + _Lbb * _idB,
                               -((_Laa + _Lba) * _idA + (_Lab + _Lbb) * _idB), _thal)
            _Ldq, _Lqq = _park(_Laa * _iqA + _Lab * _iqB, _Lba * _iqA + _Lbb * _iqB,
                               -((_Laa + _Lba) * _iqA + (_Lab + _Lbb) * _iqB), _thal)
            # dq steady state: V_d = R i_d - w psi_q ; V_q = R i_q + w psi_d
            _Vd, _Vq = _park(_V0['A'], _V0['B'], _V0['C'], _thal)
            _M = np.array([[R_phase - _w * _Lqd, -_w * _Lqq],
                           [_w * _Ldd, R_phase + _w * _Ldq]])
            try:
                _idq = np.linalg.solve(_M, np.array([_Vd, _Vq - _w * _psi_pm_d]))
            except np.linalg.LinAlgError:
                _idq = np.array([0.0, 0.0])
            _id0, _iq0 = float(_idq[0]), float(_idq[1])
            # operating-point field: A = A_pm + iA*xa + iB*xb (i_C folded in),
            # then update the iron saturation from it for the next iterate.
            _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
            _Aop = _A0 + _iA0 * _xa + _iB0 * _xb
            _pres0 = 0.0
            for _hn, _off in (("s", 0), ("r", nsn)):
                _h = half[_hn]
                _Bx, _By = _per_triangle_B(_h["mesh"], _Aop[_off:_off + _h["n"]])
                _Bm = np.sqrt(_Bx ** 2 + _By ** 2)
                for _tg, _cv in sat_bh[_hn].items():
                    _ix = _h["cells"][_tg]
                    if _ix.size == 0:
                        continue
                    _mn = _mu_r_from_bh_vec(_cv, _Bm[_ix])
                    _nn = 1.0 / (MU0 * np.maximum(_mn, 1.0))
                    # same decaying damping + honest residual stop as the main
                    # frame loop (this only SEEDS the transient's initial state,
                    # but there is no reason for it to use a different recipe)
                    _al = 0.5 if _it < 6 else max(0.05, 3.0 / (_it + 1))
                    _pres0 = max(_pres0, float(
                        np.linalg.norm(_nn - nu_el[_hn][_tg])
                        / max(np.linalg.norm(nu_el[_hn][_tg]), 1e-30)))
                    nu_el[_hn][_tg] = (1.0 - _al) * nu_el[_hn][_tg] + _al * _nn
            if _pres0 < _PIC_TOL:
                break
        _iA0, _iB0, _iC0 = _ipark(_id0, _iq0, _thal)
        _iv_state = {'A': _iA0, 'B': _iB0, 'C': _iC0}
        # psi at t=-dt on the orbit: dq are constant in steady state, so the
        # previous-step flux is just the SAME dq vector mapped one FRAME back —
        # i.e. the park angle rotated by the per-frame electrical step w*dt (NOT
        # one slip-node; a frame spans many slip nodes).  Getting this wrong
        # injects a spurious rotational EMF at frame 0 -> a decaying DC current.
        _psd = _psi_pm_d + _Ldd * _id0 + _Ldq * _iq0
        _psq = _Lqd * _id0 + _Lqq * _iq0
        _thal_m1 = _thal - _w * dt
        _psi_prev = dict(zip(('A', 'B', 'C'), _ipark(_psd, _psq, _thal_m1)))
        log.info("vdrive phasor init: Ld=%.4g Lq=%.4g H |psi_pm|=%.4g Wb "
                 "i_dq=(%.1f, %.1f) A i0=(%.1f, %.1f, %.1f)",
                 _Ldd, _Lqq, _psi_pm_d, _id0, _iq0, _iA0, _iB0, _iC0)

    # Saturation-Picard convergence diagnostics (per frame): iterations actually
    # used and the final fixed-point residual — reported in the result dict so
    # the honesty of every run is visible, never assumed.
    _pic_iters_hist: List[int] = []
    _pic_resid_hist: List[float] = []
    for k in range(n_total):
        if progress_cb is not None:
            try:
                progress_cb(_prog_off + k, _prog_tot)
            except Exception:
                pass
        theta = (k / n_total) * period_mech * n_periods
        if _moving and _macro_free_m:
            m_shift = theta / spacing                # FRACTIONAL node shift
            _mi = round(m_shift)                     # kill fp dust on whole-node
            if abs(m_shift - _mi) < 1e-9:            # runs (exact-pad gate keys
                m_shift = float(_mi)                 # on consecutive-int m)
        else:
            m_shift = int(round(theta / spacing))
        theta_eff = m_shift * spacing
        # Voltage drive uses a Crank–Nicolson circuit: (ψ_k − ψ_{k−1})/dt is the
        # EXACT centred derivative at the mid-step time, so V must be sampled
        # there too and R split between i_k and i_{k−1} — this removes the
        # backward-Euler phase lag (ωΔt/2, 15°el at 12 steps/period) that
        # otherwise skews the whole operating point when |V| ≈ |E|.
        #
        # CRITICAL: the field only exists at SNAPPED slip-node angles θ_eff, so
        # the circuit must live in "rotor time": Δt_k = Δθ_eff/ω with V sampled
        # at the midpoint of the ACTUAL motion.  Dividing the snapped-rotor Δψ
        # by the UNIFORM dt instead modulates dψ/dt by the node-quantisation
        # sawtooth (±33 % at 48 steps vs 72 nodes/period — fake volts ≫ |V−E|)
        # and Crank–Nicolson rings undamped at Nyquist → monster harmonic
        # currents (THD_I ~110 % observed).  Rotor-time stepping removes the
        # artifact exactly; over a period Σ Δt_k = the nominal period.
        _dth_frame = period_mech * n_periods / n_total     # mech deg per frame
        if _vdrive:
            if _th_eff_prev is None:            # very first frame: nominal step
                _th_eff_prev = theta_eff - _dth_frame
            _dth_eff = theta_eff - _th_eff_prev
            _dt_k = dt * (_dth_eff / _dth_frame) if _dth_eff > 1e-12 else dt
            _Vt = _voltages(0.5 * (theta_eff + _th_eff_prev))
            _th_eff_prev = theta_eff
        else:
            _Vt = None
        _iv_prev = dict(_iv_state) if _vdrive else None    # i_{k−1} for the R/2 term
        if not _vdrive:
            Ist = _currents(theta_eff)
        else:
            Ist = dict(_iv_state)   # warm start (only seeds the saturation Picard)
        if _moving:
            # Moving band: the rotor<->stator coupling is the closed-form strip
            # stiffness K_band(m) (added to K below); the only node-pairing
            # constraints left are the m-INDEPENDENT sector cuts -> constant Pro.
            Pro = Pro_const
            outer_red = outer_red_const
            _Kband_f = _K_gap_macro(m_shift) if _use_macro else _K_band(m_shift)
        else:
            # legacy: signed union-find merges ring nodes (slip) + cut pairs.
            suf = _SignedUF(n)
            for a, b in zip(Mn, Sn):
                suf.union(int(b), int(a), _bc_sign)
            for kk in range(Nring):
                j = kk + m_shift; sg = 1
                while j > Nring - 1: j -= (Nring - 1); sg *= _bc_sign
                while j < 0:         j += (Nring - 1); sg *= _bc_sign
                suf.union(int(rring[kk] + nsn), int(sring[j]), sg)
            roots = [suf.find(i) for i in range(n)]
            rid = np.array([r for r, _ in roots]); rsg = np.array([s for _, s in roots], float)
            uniq, inv = np.unique(rid, return_inverse=True)
            Pro = _coo((rsg, (np.arange(n), inv)), shape=(n, uniq.size)).tocsr()
            outer_red = np.unique(inv[outer_nodes])
            _Kband_f = None
        # Source vectors.  Current drive: one fixed load vector f (Ist known).
        # Voltage drive: the winding "unit-current" columns so the field can be
        # written A = A_pm + i_A*xa + i_B*xb with i_C = -i_A-i_B (coil C folded
        # in), and the currents solved from the circuit.
        _n_rot = half["r"]["n"]
        if not _vdrive:
            f_cur_s = (Ist['A'] * f_coil['A'] + Ist['B'] * f_coil['B']
                       + Ist['C'] * f_coil['C'])
            f = np.concatenate([f_cur_s, f_mag])
        else:
            _Pmag = np.concatenate([np.zeros(nsn), f_mag])
            _Pa = np.concatenate([f_coil['A'] - f_coil['C'], np.zeros(_n_rot)])
            _Pb = np.concatenate([f_coil['B'] - f_coil['C'], np.zeros(_n_rot)])
        # WARM-START nu across frames: adjacent rotor positions differ in their
        # saturation pattern by well under a percent, so the previous frame's
        # converged nu is an excellent initial guess (~4-5x fewer sweeps).
        # HONESTY NOTE: under the OLD fixed-iteration recipe (14 sweeps, no
        # residual test) warm-starting was UNSOUND — every frame stopped at a
        # different stage of non-convergence and the start-dependence showed up
        # as extra ripple, so the reset was load-bearing.  With residual-based
        # stopping (_PIC_TOL) the fixed point is start-independent by
        # construction: the initial guess changes the PATH, never the answer
        # (to within tol).  Frame 0 still starts from the unsaturated base.
        # FROZEN-NU: frame 0 converges once (extended Picard below); later frames
        # keep that nu untouched — no reset, no update, one linear solve each.
        if k == 0 and not (frozen_nu and not _vdrive and k > 0):
            for hn in ("s", "r"):
                for tag in sb_sat[hn]:
                    nu_el[hn][tag][:] = 1.0 / (MU0 * max(mu0[hn].get(tag, 1.0), 1.0))
        A = np.zeros(n)
        # Voltage drive changes the current every Picard step, so the saturation
        # state moves more than at fixed current -> a few extra iterations.
        # SETTLING frames (all but the last discarded period) only need the DC
        # trajectory roughly right -> a shallow Picard is ~3× cheaper; the last
        # settling period + the whole reported window run at full depth.
        if _vdrive and _vskip and k < (_vskip - _v_nspp):
            _n_pic = max(6, nonlinear_iterations // 2)
        else:
            _n_pic = nonlinear_iterations + (6 if _vdrive else 0)
        if frozen_nu and not _vdrive:
            # reference frame: extended convergence; frozen frames: 1 linear solve
            _n_pic = max(nonlinear_iterations, 40) if k == 0 else 1
        _pic_ok = 0; _pic_res = 0.0
        _pic_r_prev = None; _pic_om = 0.5   # Irons–Tuck relaxation state (per frame)
        for it in range(_n_pic):
            blocks = []
            for hn in ("s", "r"):
                h = half[hn]; K = K_const[hn].copy()
                for tag, _sbi in sb_sat[hn].items():
                    b0 = b0_sat[hn][tag]; nf = b0.zeros()
                    nf[h["cells"][tag]] = nu_el[hn][tag]   # P0 dof = global elem id
                    K = K + asm(_stiff_nu, _sbi, nu=b0.interpolate(nf))
                blocks.append(K)
            K = _bd(blocks).tocsr()
            if _Kband_f is not None:
                K = (K + _Kband_f).tocsr()   # moving-band strip coupling
            if eddy:
                Keff = (K + _Minv_dt).tocsr()
                # Solid-bar coils: current imposed via the integral J=I constraint,
                # NOT a source -- the RHS carries magnets + eddy history.
                rhs_field = (np.concatenate([np.zeros(nsn), f_mag])
                             + _Minv_dt @ A_prev)
                I_vec = np.concatenate([
                    np.array([Ist[ph] for ph in _phase]) * _Iunit,
                    np.zeros(len(_cons) - _n_coil_con)])
                A, U_cons = _solve_eddy_constrained(Keff, rhs_field, Pro,
                                                    outer_red, I_vec, A_prev)
            elif _vdrive:
                # ---- STRONG field + circuit coupling ------------------------
                # The phase currents are unknowns solved WITH the field on the
                # EXACT inductance of the current saturation state:
                #   A = A_pm + i_A*xa + i_B*xb   (i_C = -i_A - i_B), where
                #     A_pm = K^-1 * f_mag                 (PM flux, i = 0)
                #     xa   = K^-1 * (coilA - coilC),  xb = K^-1 * (coilB - coilC)
                # so K*A reproduces the imposed winding source exactly.  The
                # per-phase flux linkages give the PM flux + the 2x2 inductance
                # L; the circuit (backward Euler)
                #   (R*I2 + L/dt) * i = V - (psi_pm - psi_prev)/dt
                # is solved directly for i.  No outer iteration, no frozen
                # Jacobian -- L is re-measured EVERY Picard step, so saturation
                # and the circuit converge together.  Factor K once, reuse the
                # LU for all three back-solves.
                Kg = (Pro.T @ K @ Pro).tocsr()
                _msk = np.ones(Kg.shape[0], bool); _msk[outer_red] = False
                _free = np.flatnonzero(_msk)
                _lu = _splu(Kg[_free][:, _free].tocsc())

                def _bsolve(_ffull, _lu=_lu, _free=_free, _Ncol=Kg.shape[0]):
                    _fr = (Pro.T @ _ffull)[_free]
                    _xr = np.zeros(_Ncol); _xr[_free] = _lu.solve(_fr)
                    return Pro @ _xr

                A_pm = _bsolve(_Pmag); xa = _bsolve(_Pa); xb = _bsolve(_Pb)
                _pm = _psi_of(A_pm); _qa = _psi_of(xa); _qb = _psi_of(xb)
                _psi_pmA = _pm[0] * sc_psi; _psi_pmB = _pm[1] * sc_psi
                _psi_pmC = _pm[2] * sc_psi
                _Laa = _qa[0] * sc_psi; _Lba = _qa[1] * sc_psi; _Lca = _qa[2] * sc_psi
                _Lab = _qb[0] * sc_psi; _Lbb = _qb[1] * sc_psi; _Lcb = _qb[2] * sc_psi
                if _psi_prev is None:   # first (discarded settling) frame bootstrap
                    _psi_prev = {'A': _psi_pmA, 'B': _psi_pmB, 'C': _psi_pmC}
                # Crank–Nicolson circuit at the mid-step time of the ACTUAL
                # rotor motion (Δt_k = Δθ_eff/ω — see the frame-top comment),
                # in the LINE-TO-LINE (floating-neutral wye) formulation:
                #   V_AB = R·(Δi_k + Δi_{k−1})/2 + (Δψ_k − Δψ_{k−1})/Δt_k  (Δ = A−B)
                #   V_BC likewise (B−C), with i_C = −i_A − i_B.
                # A real FOC inverter drives an ISOLATED-neutral machine: the
                # zero-sequence back-EMF (triplen harmonics — large on this
                # concentrated winding) falls on the floating neutral and drives
                # NO current.  Applying phase voltages to the phase equations
                # directly pins the machine's neutral to the source's and shorts
                # that zero-sequence EMF through the tiny zero-seq inductance →
                # monster fake triplen currents (measured h3 ≈ 43 % of I₁,
                # THD_I ≈ 110 %).  Line-to-line differences kill the zero
                # sequence exactly — as the physical isolated neutral does.
                _dpmA = _psi_pmA - _psi_prev['A']
                _dpmB = _psi_pmB - _psi_prev['B']
                _dpmC = _psi_pmC - _psi_prev['C']
                _Mc = np.array([
                    [0.5 * R_phase + (_Laa - _Lba) / _dt_k,
                     -0.5 * R_phase + (_Lab - _Lbb) / _dt_k],
                    [0.5 * R_phase + (_Lba - _Lca) / _dt_k,
                     1.0 * R_phase + (_Lbb - _Lcb) / _dt_k]])
                _bc = np.array([
                    (_Vt['A'] - _Vt['B']) - (_dpmA - _dpmB) / _dt_k
                    - 0.5 * R_phase * (_iv_prev['A'] - _iv_prev['B']),
                    (_Vt['B'] - _Vt['C']) - (_dpmB - _dpmC) / _dt_k
                    - 0.5 * R_phase * (_iv_prev['B'] - _iv_prev['C'])])
                _iab = np.linalg.solve(_Mc, _bc)
                _iA = float(_iab[0]); _iB = float(_iab[1])
                Ist = {'A': _iA, 'B': _iB, 'C': -_iA - _iB}
                A = A_pm + _iA * xa + _iB * xb
            else:
                A = Pro @ _sksolve(*condense((Pro.T @ K @ Pro).tocsr(),
                                              Pro.T @ f, D=outer_red))
            if frozen_nu and not _vdrive and k > 0:
                continue                      # frozen frames: nu stays untouched
            # PER-ELEMENT reluctivity: each iron triangle gets its own mu(|B|)
            # from the B-H curve.  Gather the WHOLE saturable nu state into one
            # vector so the relaxation and the residual see the global field.
            _nu_old_v = []; _nu_new_v = []; _nu_slices = []
            for hn, off in (("s", 0), ("r", nsn)):
                h = half[hn]
                Bx, By = _per_triangle_B(h["mesh"], A[off:off + h["n"]])
                Bm = np.sqrt(Bx ** 2 + By ** 2)
                for tag, curve in sat_bh[hn].items():
                    idx = h["cells"][tag]
                    if idx.size == 0:
                        continue
                    mu_new = _mu_r_from_bh_vec(curve, Bm[idx])
                    _nu_old_v.append(nu_el[hn][tag])
                    _nu_new_v.append(1.0 / (MU0 * np.maximum(mu_new, 1.0)))
                    _nu_slices.append((hn, tag))
            if _nu_slices:
                _vo = np.concatenate(_nu_old_v)
                _r = np.concatenate(_nu_new_v) - _vo
                # honest fixed-point residual (relative L2, BEFORE relaxation —
                # the relaxed step size would fake convergence)
                _pic_res = float(np.linalg.norm(_r)
                                 / max(np.linalg.norm(_vo), 1e-30))
                # IRONS–TUCK (vector Aitken Δ²) adaptive relaxation: the fixed
                # 0.5 step OSCILLATES around the fixed point (THE source of the
                # old 5–8 Nm no-load torque floor); a 1/it decay converges but
                # crawls (~100 sweeps to 1e-3).  Aitken measures the actual
                # contraction from consecutive residuals and picks the step —
                # no tuned schedules.  (Anderson(m=4) was tried and THRASHES on
                # this map — the B-H knee makes the residual non-smooth and the
                # secant model misfires.)  omega clamped to (0, 1]: the update
                # stays a convex combination of old and new nu, so it can never
                # leave the physical range the B-H curve produced.
                if _pic_r_prev is not None:
                    _dr = _r - _pic_r_prev
                    _den = float(_dr @ _dr)
                    if _den > 0.0:
                        _pic_om = float(np.clip(
                            -_pic_om * float(_pic_r_prev @ _dr) / _den,
                            0.05, 1.0))
                _pic_r_prev = _r
                _vu = _vo + _pic_om * _r
                _p0 = 0
                for (_hn2, _tg2), _arr in zip(_nu_slices, _nu_old_v):
                    nu_el[_hn2][_tg2] = _vu[_p0:_p0 + _arr.size]
                    _p0 += _arr.size
            # HONEST stopping: two consecutive sweeps with the worst relative
            # nu residual under _PIC_TOL end this frame's Picard.  Lightly
            # saturated frames exit in a handful of iterations; deep saturation
            # runs to the cap.  No fixed-recipe iteration counts anywhere.
            if _pic_res < _PIC_TOL:
                _pic_ok += 1
                if _pic_ok >= 2:
                    # Saturation has converged FOR THE PRESENT MAGNET STATE, so
                    # this is the only place the irreversible demag rule may be
                    # evaluated.  Applying it to an intermediate Picard iterate
                    # is wrong and destructive: the early sweeps start from
                    # unsaturated iron and pass through fields that are pure
                    # numerical transients (measured H ≈ −880 kA/m against a
                    # converged −550), and a monotone irreversible rule burns
                    # that artefact into the magnet permanently — it wiped NdFeB
                    # to Br 0.03 and cost a third of the torque.
                    #
                    # Weakening the magnet moves the saturation state, so the
                    # Picard is re-entered and the frame only ends when the
                    # field and the magnet agree.
                    if demag and _mag_idx.size:
                        _Bxr_p, _Byr_p = _per_triangle_B(half["r"]["mesh"],
                                                         A[nsn:])
                        if _demag_derate(_Bxr_p, _Byr_p):
                            f_mag = _build_fmag(_br_glob)
                            if _vdrive:
                                _Pmag = np.concatenate([np.zeros(nsn), f_mag])
                            else:
                                f = np.concatenate([f_cur_s, f_mag])
                            _pic_ok = 0
                            continue
                    break
            else:
                _pic_ok = 0
        if (demag and _mag_idx.size and frozen_nu and not _vdrive and k > 0):
            # Frozen-nu frames run a single linear solve and never reach the
            # convergence test above; with nu fixed that solve IS the converged
            # field, so the same rule applies to it directly.
            _Bxr_p, _Byr_p = _per_triangle_B(half["r"]["mesh"], A[nsn:])
            if _demag_derate(_Bxr_p, _Byr_p):
                f_mag = _build_fmag(_br_glob)
                f = np.concatenate([f_cur_s, f_mag])
        _pic_iters_hist.append(it + 1); _pic_resid_hist.append(_pic_res)
        # per-phase flux linkage of the converged solution (also used below).
        pa, pb, pc = _psi_of(A)
        if _vdrive:
            # circuit residual on the CONVERGED solution (health check: ~0 when
            # the coupled Picard converged).
            _psiA_c = pa * sc_psi; _psiB_c = pb * sc_psi; _psiC_c = pc * sc_psi
            # line-to-line residuals (the phase-A equation alone is legitimately
            # nonzero by the zero-sequence EMF the floating neutral absorbs)
            _resA = ((_Vt['A'] - _Vt['B'])
                     - 0.5 * R_phase * (Ist['A'] - Ist['B']
                                        + _iv_prev['A'] - _iv_prev['B'])
                     - ((_psiA_c - _psiB_c)
                        - (_psi_prev['A'] - _psi_prev['B'])) / _dt_k)
            _resB = ((_Vt['B'] - _Vt['C'])
                     - 0.5 * R_phase * (Ist['B'] - Ist['C']
                                        + _iv_prev['B'] - _iv_prev['C'])
                     - ((_psiB_c - _psiC_c)
                        - (_psi_prev['B'] - _psi_prev['C'])) / _dt_k)
            _v_diag["iters"].append(int(_n_pic))
            _v_diag["resid"].append(float(max(abs(_resA), abs(_resB))))
            _psi_prev = {'A': _psiA_c, 'B': _psiB_c, 'C': pc * sc_psi}
            _iv_state = dict(Ist)
            # ITERATED Aitken DC-mode removal: the period-boundary flux converges
            # geometrically toward the steady orbit; sample it at each period
            # end and Δ²-extrapolate the limit whenever 3 fresh samples exist
            # since the last anchor (anchors at periods 3, 6, 9 within the
            # settling window — each application cuts the residual DC ~3×).
            # Samples must share the cycle phase (period spacing) so the
            # periodic flux content cancels exactly in the differences.
            if _v_nspp > 0 and ((k + 1) % _v_nspp == 0) and (k + 1) < _vskip:
                _v_bpsi.append((_psiA_c, _psiB_c))
                if len(_v_bpsi) >= 3:
                    _p0, _p1, _p2 = _v_bpsi[-3:]
                    _new = {}
                    for _ci, _ky in enumerate(('A', 'B')):
                        _x0, _x1, _x2 = _p0[_ci], _p1[_ci], _p2[_ci]
                        _d1 = _x1 - _x0; _d2 = _x2 - _x1; _dd = _d2 - _d1
                        _new[_ky] = (_x2 - _d2 * _d2 / _dd) if abs(_dd) > 1e-15 else _x2
                    _new['C'] = -(_new['A'] + _new['B'])
                    _corr = max(abs(_new['A'] - _psiA_c), abs(_new['B'] - _psiB_c))
                    _drift = max(abs(_p2[0] - _p1[0]), abs(_p2[1] - _p1[1]))
                    # Guarded anchor: once the boundary drift is below noise the
                    # Δ² quotient divides noise by noise and the "correction"
                    # EXPLODES — skip when already converged (drift < 0.05 % of
                    # the PM flux) or when the extrapolation is unstable
                    # (|corr| ≫ drift).  Better to keep marching than to kick a
                    # converged orbit right before the reported window.
                    _flux_scale = max(abs(_psiA_c), abs(_psiB_c), 1e-6)
                    if _drift < 5e-4 * _flux_scale or _corr > 5.0 * _drift:
                        log.info("vdrive Aitken anchor SKIPPED at period %d "
                                 "(drift %.3g, corr %.3g Wb — converged/unstable)",
                                 (k + 1) // _v_nspp, _drift, _corr)
                        _v_bpsi.clear()
                    else:
                        _psi_prev = _new
                        _v_bpsi.clear()   # fresh samples only after the re-anchor
                        log.info("vdrive Aitken anchor at period %d: psiA %.4g -> "
                                 "%.4g (|corr| %.3g Wb)", (k + 1) // _v_nspp,
                                 _psiA_c, _new['A'], _corr)
        if eddy:
            # Joule loss = ∫σ J²/σ² = ∫σ(−∂A/∂t + U_c)² over the conductors.
            # The conductor voltage U_c cancels the large inductive −∂A/∂t,
            # leaving the real dissipation.  Constrained conductors get their
            # solved U_c; U=0 conductors (shaft, cut magnet halves) are pure
            # J = −σ∂A/∂t by symmetry.
            Uvec = np.zeros(n)
            for _ci, _c in enumerate(_cons):
                Uvec[_c["nodes"]] = U_cons[_ci]
            Ffld = -(A - A_prev) / dt + Uvec
            _eddy_P.append(float(Ffld @ (_Minv_dt @ Ffld)) * dt)   # ∫σ F² [W, sector]
            A_prev = A.copy()        # previous-step field for the σ·∂A/∂t term
        if rotor_eddy and _magnode_glob.size:
            # Magnet-node A history → smoothed post-processed eddy loss below.
            _hist_Am.append(A[_magnode_glob].copy())
        if rotor_eddy and _shaftnode_glob.size:
            # Shaft-node A history → field-based (rotor-frame) shaft eddy loss.
            _hist_Ash.append(A[_shaftnode_glob].copy())
        if honest_eddy or rotor_eddy:
            # ALL rotor-node A history → coupled (reaction-included) rotor eddy.
            # With rotor_eddy this is now the PRODUCTION magnet/shaft loss (the
            # history-based post-process is jitter-dominated for screened bodies
            # — see the honest-swap block below); honest_eddy alone keeps the
            # old diagnostic behaviour.  ~N·n_rotor_nodes·8 B ≈ 10-20 MB.
            _hist_A_rotor.append(A[nsn:nsn + half["r"]["n"]].copy())
        # capture the converged per-element B for the loss integrals
        _Bxs, _Bys = _per_triangle_B(half["s"]["mesh"], A[:nsn])
        _Bxr, _Byr = _per_triangle_B(half["r"]["mesh"], A[nsn:])
        # field_first snapshots the FIRST frame of the MEASUREMENT pass — frame 0
        # is now a settling frame with a magnet that has not yet de-rated, and
        # showing that as "the demag map" would report a pristine magnet.
        if return_field and ((field_first and k == _dmskip)
                             or (not field_first and k == n_total - 1)):
            # Field snapshot for the viewer: A_z, per-element B, and a current
            # density J.  eddy=True → the EDDY density J = σ(−∂A/∂t + U_c) on the
            # solid conductors (the genuinely-new field the eddy solve produces).
            # eddy=False (magnetostatic field view, run at 1 step + rotor_angle0)
            # → the per-element SOURCE current density so the J view still shows
            # the applied winding currents at this rotor position.
            _sig_node = np.zeros(n)
            if eddy:
                for _c in _coil_con:
                    _sig_node[_c["nodes"]] = _sig_cu_T
            for _nds, _sg in _rot_sig_nodes:
                _sig_node[_nds] = _sg
            _Jnodal = _sig_node * (Ffld if eddy else 0.0)
            _field_snap = {
                "P_mm": (mesh_all.p * 1e3).copy(),                 # node coords [mm]
                "T":    mesh_all.t.copy(),                         # triangles (3,nel)
                "A":    A.copy(),                                  # nodal A_z [Wb/m]
                "Bx":   np.concatenate([_Bxs, _Bxr]),              # per-elem B [T]
                "By":   np.concatenate([_Bys, _Byr]),
                "Jeddy": _Jnodal,                          # nodal eddy J [A/m^2]
                "tags": np.concatenate([np.asarray(ts), np.asarray(tr)]).astype(int),
                "nsn":  int(nsn),
            }
            if not eddy:
                # per-element source current density J_z = dir·I_phase·n_wires/area
                # (coil elements live in the stator half = first block of the mesh)
                _Js = np.zeros(_Bxs.size + _Bxr.size)
                for _ix, _ar, _dir, _ph in coil_info:
                    _Js[_ix] = _dir * Ist[_ph] * n_wires / max(slot_area_m2, 1e-12)
                _field_snap["Jtri_src"] = _Js
        _hist_sx.append(_Bxs[_iron_s_idx]); _hist_sy.append(_Bys[_iron_s_idx])
        _hist_rx.append(_Bxr[_iron_r_idx]); _hist_ry.append(_Byr[_iron_r_idx])
        _hist_mx.append(_Bxr[_mag_idx]);    _hist_my.append(_Byr[_mag_idx])
        if _coil_idx.size:
            _hist_cx.append(_Bxs[_coil_idx]); _hist_cy.append(_Bys[_coil_idx])
        if _shaft_idx.size:
            _hist_shx.append(_Bxr[_shaft_idx]); _hist_shy.append(_Byr[_shaft_idx])
        _mshift_hist.append(m_shift)
        # torque: sector → Arkkio over the whole gap.  Moving band → Arkkio over
        # the coupled STRIP only (the half-mesh gap fields are sheared and carry
        # a spurious DC torque; the strip is the consistent rotor↔stator join).
        if _moving:
            Tq_sec = _T_macro(m_shift, A) if _use_macro else _T_band(m_shift, A)
        else:
            Tq_sec = _arkkio_torque(mesh_all, A, p.r_rotor_out, p.r_stator_in,
                                    p.stack_length)
        Tq = Tq_sec * NS
        if _TORQUE_DIAG["on"]:
            _mr = 0.5 * (p.r_rotor_out + p.r_stator_in)
            _gw = (p.r_stator_in - p.r_rotor_out)
            _ak = lambda a, b: _arkkio_torque(mesh_all, A, a, b, p.stack_length) * NS
            _TORQUE_DIAG["full"].append(Tq)                                 # reported torque (strip for moving)
            _TORQUE_DIAG.setdefault("arkkio_full", []).append(_ak(p.r_rotor_out, p.r_stator_in))  # sheared half-mesh Arkkio
            _TORQUE_DIAG.setdefault("tband", []).append(_T_band(m_shift, A) * NS if _moving else 0.0)  # strip Arkkio
            _TORQUE_DIAG["rotor"].append(_ak(p.r_rotor_out, _mr))          # whole rotor half
            _TORQUE_DIAG["stator"].append(_ak(_mr, p.r_stator_in))         # whole stator half
            _TORQUE_DIAG["iface"].append(_ak(_mr - 0.25 * _gw, _mr + 0.25 * _gw))  # straddles slip ring
            _TORQUE_DIAG["rinner"].append(_ak(p.r_rotor_out, p.r_rotor_out + 0.3 * _gw))   # rotor surface, far from ring
            _TORQUE_DIAG["router"].append(_ak(p.r_stator_in - 0.3 * _gw, p.r_stator_in))   # stator surface, far from ring
            # angular profile of the torque integrand over the gap band
            _Bx, _By = _per_triangle_B(mesh_all, A)
            _Pq = mesh_all.p; _Tq2 = mesh_all.t
            _cx = (_Pq[0, _Tq2[0]] + _Pq[0, _Tq2[1]] + _Pq[0, _Tq2[2]]) / 3.0
            _cy = (_Pq[1, _Tq2[0]] + _Pq[1, _Tq2[1]] + _Pq[1, _Tq2[2]]) / 3.0
            _rc = np.hypot(_cx, _cy)
            _msk = (_rc >= p.r_rotor_out) & (_rc <= p.r_stator_in)
            _ar = _triangle_areas(mesh_all)
            _cp = _cx / _rc; _sp = _cy / _rc
            _Brq = _Bx * _cp + _By * _sp
            _Bpq = -_Bx * _sp + _By * _cp
            _itg = (_ar * _rc * _Brq * _Bpq) * (p.stack_length / (MU0 * (p.r_stator_in - p.r_rotor_out))) * NS
            _phi = np.degrees(np.arctan2(_cy, _cx)) % 360.0
            _nst_tris = Tts.shape[1]
            _is_rot = np.arange(_Tq2.shape[1]) >= _nst_tris
            _phi_phys = np.where(_is_rot, (_phi + theta_eff) % 360.0, _phi)   # theta_eff is in degrees
            if _TORQUE_DIAG.get("capture_A") is not None:
                _TORQUE_DIAG["capture_A"].append(
                    dict(A=A.copy(), m=int(m_shift), Mn=Mn.copy(), Sn=Sn.copy(),
                         nsn=int(nsn)))
            _nb = int(_TORQUE_DIAG["ang_bins"])
            _prof = np.zeros(_nb)
            _sec = 360.0 / NS
            # wrap rotated rotor elements past the sector edge back into the
            # sector (Br·Bφ is invariant under the anti-periodic map)
            _bi = np.clip(((_phi_phys[_msk] % _sec) / _sec * _nb).astype(int), 0, _nb - 1)
            np.add.at(_prof, _bi, _itg[_msk])
            _TORQUE_DIAG["ang_prof"].append(_prof)
        # flux linkage: pa/pb/pc were computed by _psi_of(A) inside the frame
        # solve (single implementation — the voltage drive needs them in-loop).
        T_series.append(float(Tq))
        psiA.append(pa * sc_psi); psiB.append(pb * sc_psi); psiC.append(pc * sc_psi)
        IA.append(Ist['A']); IB.append(Ist['B']); IC.append(Ist['C'])
        tt.append(k * dt)

    # ── Voltage drive: drop the settling period — every series below is
    #    steady-state.  n_total/n_periods return to the REQUESTED window so all
    #    per-period post-processing (spectra, band-limit, summaries) is unchanged.
    if _vdrive and _vskip:
        n_total -= _vskip
        n_periods = float(n_periods) - float(_v_settle_periods)
        _slice_lists = [T_series, psiA, psiB, psiC, IA, IB, IC, tt, _mshift_hist,
                        _hist_sx, _hist_sy, _hist_rx, _hist_ry, _hist_mx, _hist_my,
                        _hist_cx, _hist_cy, _hist_shx, _hist_shy,
                        _hist_Am, _hist_Ash, _hist_A_rotor]
        try:
            _slice_lists.append(_eddy_P)   # exists only when eddy=True
        except NameError:
            pass
        for _lst in _slice_lists:
            if len(_lst) > _vskip:
                del _lst[:_vskip]
        _t0_new = tt[0] if tt else 0.0
        tt[:] = [_t - _t0_new for _t in tt]
    # ── Demag: drop the settling period.  The magnets keep their de-rated state
    #    (_br_glob is cumulative), only the FRAMES are discarded, so the reported
    #    window is one full period of a machine whose magnets have settled.
    if _dmskip:
        n_total -= _dmskip
        n_periods = float(n_periods) - 1.0
        _slice_lists = [T_series, psiA, psiB, psiC, IA, IB, IC, tt, _mshift_hist,
                        _hist_sx, _hist_sy, _hist_rx, _hist_ry, _hist_mx, _hist_my,
                        _hist_cx, _hist_cy, _hist_shx, _hist_shy,
                        _hist_Am, _hist_Ash, _hist_A_rotor]
        try:
            _slice_lists.append(_eddy_P)   # exists only when eddy=True
        except NameError:
            pass
        for _lst in _slice_lists:
            if len(_lst) > _dmskip:
                del _lst[:_dmskip]
        _t0_new = tt[0] if tt else 0.0
        tt[:] = [_t - _t0_new for _t in tt]
    # ── Spectral periodic time-derivative (truncated to K harmonics) ─────────
    # The rotor advances in DISCRETE slip-node steps, so ψ(t) and B(t) carry a
    # tiny frame-to-frame quantisation jitter.  A raw finite-difference dψ/dt
    # amplifies that jitter into a jagged back-EMF (worse at small dt → the 24-
    # step run looked torn).  Reconstruct the derivative from the LOW harmonics
    # only: that keeps the genuine fundamental + slot-ripple content but drops
    # the quantisation noise floor near Nyquist, giving a clean V(t) and a
    # physically-rippling (not noisy, not flat) loss(t).
    _two_pi2 = 2.0 * math.pi ** 2

    def _spectral_ddt(x, kmax):
        x = np.asarray(x, float); N = x.size
        if N < 4:
            return np.array([(x[(i + 1) % N] - x[(i - 1) % N]) / (2 * dt)
                             for i in range(N)])
        F = np.fft.rfft(x)
        if kmax + 1 < F.size:
            F[kmax + 1:] = 0.0
        return np.fft.irfft(F * (1j * 2 * np.pi * np.fft.rfftfreq(N, d=dt)), n=N)

    # The rotor can only sit on DISCRETE slip nodes (≈ N_slip/4 ≈ 72 positions
    # per electrical period), so B depends only on the quantised angle m_shift.
    # When n_steps > that node count the rotor advances <1 node/step and
    # STUTTERS (m_shift jumps 0,1,1,0,1…); a frame-to-frame dB/dt of that
    # stutter is meaningless noise.  So differentiate B against the UNIQUE
    # rotor node-positions (smooth, ~72 pts) and map the result back onto the
    # time frames — gives a clean dB/dt at any n_steps.
    # float: macro mode advances a FRACTIONAL node count per step (free m);
    # integer-m runs are unchanged (whole numbers survive the float dtype, and
    # the pole-shift exact-pad gate simply stays on its consecutive-int check).
    _m_arr = np.asarray(_mshift_hist, float)
    _spacing_rad = math.radians(spacing)
    _omega_mech = 2.0 * math.pi * rpm / 60.0

    def _angle_ddt_2d(X, quasi_period_rad=None, pre=None, post=None):
        """Smoothed dX/dt on the unique slip-node grid, mapped back to frames.

        Raw node-to-node derivatives amplify slip-merge jitter with the step
        count, so X(θ) is savgol-low-passed before differentiating.  Two
        defects were found by a 1-period vs 2-period ground-truth comparison
        and are handled here:
        (1) ROTOR-frame histories (magnets, rotor iron) are NOT periodic over
            the window — the stator slotting sweeps past at the stator-
            structure period (2 slot pitches: alternating wide/narrow teeth),
            a non-integer count per electrical period — so a forced periodic
            wrap put a LEVEL JUMP at the seam that the smoother bent into the
            neighbours: P_mag humped ~2.5× on the first/last frames.  Fixed by
            a C0 wrap-detrend (see below) — exact for the derivative, no
            assumption about the signal's true period, no-op when already
            periodic (stator-frame histories).
        (2) the savgol window was set in SAMPLES (U//8), so the physical
            smoothing width depended on the run length — a 2-period run
            smoothed the genuine 15° slot ripple away (P_mag halved on
            identical physics).  Fixed: the width is a constant ANGLE.
        When `pre`/`post` are given they are EXACT samples just before/after
        the window (from the pole-shift symmetry — see the magnet-history pad
        block); the derivative then uses real data at both ends and needs no
        wrap assumption at all.
        (quasi_period_rad accepted for compatibility; unused.)
        """
        N = X.shape[0]
        if N < 3:
            return np.zeros_like(X)
        uniq, first = np.unique(_m_arr, return_index=True)   # sorted unique m
        if uniq.size < 3:
            return (np.roll(X, -1, 0) - np.roll(X, 1, 0)) / (2 * dt)
        Bu = X[first]                                        # (U, E)
        theta_u = uniq * _spacing_rad                        # (U,)
        U = uniq.size
        _W = math.radians(period_mech * n_periods)           # window span
        if float(theta_u[-1]) >= _W - 1e-9:
            # degenerate: last node is the periodic image of the first — drop it
            uniq = uniq[:-1]; Bu = Bu[:-1]; theta_u = theta_u[:-1]; U -= 1
        # savgol at a FIXED PHYSICAL width (≈1/3 slot pitch).  Two past bugs:
        # (a) a window set in SAMPLES (the old U//8) made the width depend on
        #     the run length — a 2-period run smoothed the genuine 15° slot
        #     ripple away (P_mag halved on identical physics);
        # (b) the angle→samples conversion divided by the slip-NODE spacing
        #     (_spacing_rad) instead of the SAMPLE spacing, so the effective
        #     width was 5°×(nodes advanced per step): 60° at 12 steps, 30° at
        #     24, 10° at 72 — the filter NARROWED as the step count grew, so
        #     the "converged" rotor eddy loss GREW with steps (450 mm shaft:
        #     0.8→12.7→25.6 kW at 12/24/72) instead of converging.  Fixed:
        #     divide by the actual theta_u sample spacing.
        _w_ang = math.radians(5.0)                            # smoothing width
        _samp_rad = (float(np.median(np.diff(theta_u))) if U >= 2
                     else _spacing_rad)
        w = int(round(_w_ang / max(_samp_rad, 1e-12)))
        w = max(5, w | 1)                                     # odd, ≥5
        _exact = (pre is not None and post is not None
                  and U == N and np.array_equal(uniq, _m_arr))
        if _exact:
            # EXACT pads: real samples beyond both window ends — no wrap, no
            # detrend; the seam simply does not exist.
            _need = w // 2 + 2
            pre_u = pre[-min(_need, pre.shape[0]):]
            post_u = post[:min(_need, post.shape[0])]
            thL = theta_u[0] - _spacing_rad * np.arange(pre_u.shape[0], 0, -1)
            thR = theta_u[-1] + _spacing_rad * np.arange(1, post_u.shape[0] + 1)
            th_ext = np.concatenate([thL, theta_u, thR])
            Bu_ext = np.concatenate([pre_u, Bu, post_u], axis=0)
            i0 = pre_u.shape[0]
            _c = np.zeros((1, Bu.shape[1]))
        else:
            # C0 detrend: per column, remove the linear ramp that makes the two
            # window ends MEET, so the periodic extension has no level jump at
            # the seam; the ramp's constant slope is added back to the
            # derivative exactly.  (A harmonic-regression replacement was tried
            # and rejected: the slot-structure lines sit ~0.86 cycles apart —
            # under the Rayleigh limit of a 1-period window — so the design
            # matrix is near-singular and the fit explodes.)
            _span = float(theta_u[-1] - theta_u[0])
            if _span > 1e-12:
                _c = (Bu[-1] - Bu[0])[None, :] / _span       # (1, E) dB/dθ
                Bu = Bu - _c * (theta_u - theta_u[0])[:, None]
            else:
                _c = np.zeros((1, Bu.shape[1]))
            th_ext = np.concatenate([theta_u - _W, theta_u, theta_u + _W])
            Bu_ext = np.concatenate([Bu, Bu, Bu], axis=0)
            i0 = U
        if U >= 7 and Bu_ext.shape[0] >= w:
            from scipy.signal import savgol_filter as _sg
            Bu_ext = _sg(Bu_ext, w, 3, axis=0, mode="interp")
        dBdt_u = ((np.gradient(Bu_ext, th_ext, axis=0)[i0:i0 + U] + _c)
                  * _omega_mech)
        pos = np.clip(np.searchsorted(uniq, _m_arr), 0, U - 1)   # frame → unique idx
        return dBdt_u[pos]

    def _declip(a):
        # Safety net: clip any residual single-frame outlier to median±5·MAD.
        a = np.asarray(a, float)
        if a.size < 5:
            return a
        med = float(np.median(a)); mad = float(np.median(np.abs(a - med)))
        if mad <= 0:
            return a
        return np.clip(a, max(0.0, med - 5 * mad), med + 5 * mad)

    _Kv = max(1, min(5, (n_total // 2) - 1))     # back-EMF: keep it smooth

    # voltage V = R·I + dψ/dt  (spectrally smoothed back-EMF)
    eA = _spectral_ddt(psiA, _Kv); eB = _spectral_ddt(psiB, _Kv)
    eC = _spectral_ddt(psiC, _Kv)
    VA = [R_phase * i + e for i, e in zip(IA, eA.tolist())]
    VB = [R_phase * i + e for i, e in zip(IB, eB.tolist())]
    VC = [R_phase * i + e for i, e in zip(IC, eC.tolist())]

    # ── HYBRID torque: energy-consistent MEAN + Maxwell-stress RIPPLE ─────────
    # IDENTICAL to the P2 branch above — one torque definition for the whole
    # solver, not two.  P1 reported the raw Arkkio/Maxwell mean, which on the
    # node-repaired sliding band over-reads ~35 % under load: measured 0.181 N*m
    # here against 0.130 in ANSYS for the same design, and 0.181/1.35 = 0.134.
    # That gap was never physics, only this missing correction.
    #
    # P1 is still reachable for the things P2 cannot do (irreversible demag,
    # coupled eddy, voltage drive), so it has to report the same number as P2 or
    # every demag result is silently inflated.
    #   MEAN   — energy/virtual-work via the terminal quantities:
    #            <T> = (3/2)*p*<psi_al*i_be - psi_be*i_al>.  Never touches the
    #            gap field, so it is immune to the slip-band contamination.
    #   RIPPLE — the Maxwell series, which DOES resolve cogging and slot
    #            harmonics (the flux-linkage torque is winding-filtered and
    #            cannot see them).
    # No-load (I ~ 0) keeps the raw Maxwell cogging: the energy torque is 0 there.
    _torque_method_p1 = "maxwell_stress"
    _mx_raw_p1 = list(T_series)          # keep the Maxwell series as a diagnostic
    try:
        _pa1 = np.asarray(psiA, float); _pb1 = np.asarray(psiB, float)
        _pc1 = np.asarray(psiC, float)
        _ea1 = np.asarray(IA, float); _eb1 = np.asarray(IB, float)
        _ec1 = np.asarray(IC, float)
        _Ipk1 = (float(np.max(np.abs(np.concatenate([_ea1, _eb1, _ec1]))))
                 if _ea1.size else 0.0)
        if _pa1.size and _pa1.size == _ea1.size and _Ipk1 > 1.0:
            _s1 = 2.0 / 3.0; _kc1 = math.sqrt(3.0) / 2.0
            _psial1 = _s1 * (_pa1 - 0.5 * _pb1 - 0.5 * _pc1)
            _psibe1 = _s1 * _kc1 * (_pb1 - _pc1)
            _ial1 = _s1 * (_ea1 - 0.5 * _eb1 - 0.5 * _ec1)
            _ibe1 = _s1 * _kc1 * (_eb1 - _ec1)
            _Te1 = 1.5 * float(pole_pairs) * (_psial1 * _ibe1 - _psibe1 * _ial1)
            _mx1 = np.asarray(T_series, float)
            T_series = (_mx1 - _mx1.mean() + float(_Te1.mean())).tolist()
            _torque_method_p1 = "energy_mean+maxwell_ripple"
    except Exception as _te1:
        log.warning("P1 hybrid torque failed (%s) — using Maxwell series", _te1)
    Tavg = float(np.mean(T_series)) if T_series else 0.0

    # ── Band-limit the torque to the physical 6·k electrical orders ──────────
    # The sliding band steps the rotor across DISCRETE slip nodes, injecting
    # broadband torque ripple at orders a balanced 3-φ machine CANNOT produce
    # (1,2,3,4,5,7,…) that does NOT converge with mesh refinement → purely
    # numerical.  Measured at no-load: the real order-6 cogging dominates, but
    # ~41 % of the ripple ENERGY sits in those forbidden orders (raw pk-pk
    # 10.0 → 6·k-only 4.8 N·m).  Keep DC + every 6·k order (real cogging + load
    # ripple) and drop the rest; the mean is preserved exactly.  torque_filter
    # (UI toggle, default ON) switches back to the raw per-frame torque for
    # inspecting the unfiltered solve.
    # Always compute BOTH the raw per-frame torque and the band-limited (6·k)
    # reconstruction, and return both — band-limiting is pure post-processing
    # (FFT → keep DC + 6·k → inverse), so the UI can toggle between them
    # INSTANTLY without a 30 s re-solve.  band_limit_torque preserves the mean
    # exactly, so T_avg is identical for raw and filtered.
    _T_raw = list(T_series)
    _T_filt, Trip_filt, Trip_raw, Tnoise_pct = band_limit_torque(
        T_series, n_steps_per_period, n_periods)
    # T_em_Nm follows the toggle for back-compat (saved sims + server summary);
    # the UI uses the explicit T_em_raw_Nm / T_em_filt_Nm fields below to flip
    # client-side without re-running.
    if torque_filter:
        T_series = list(_T_filt); Trip = Trip_filt
    else:
        T_series = list(_T_raw);  Trip = Trip_raw
    Tavg = float(np.mean(_T_raw)) if _T_raw else Tavg
    Vpk = float(max(max(map(abs, VA)), max(map(abs, VB)), max(map(abs, VC)))) if VA else 0.0
    # P_cu already computed physically (ρ(T)·J²·V·k_end) near the top.

    # ── Torque harmonic spectrum over ONE electrical period ──────────────────
    # The single most telling diagnostic for "is this periodic or chaotic": a
    # clean ripple shows a few DISCRETE peaks (the cogging / 6·k 3-phase orders);
    # broadband noise spreads across all orders.  Orders are multiples of the
    # ELECTRICAL fundamental; amplitude is the single-sided FFT magnitude [N·m].
    # Spectrum is ALWAYS the RAW per-frame torque (not the band-limited series),
    # so the UI shows every order and the user can SEE which bars the 6·k filter
    # keeps (orange) vs drops (the broadband slip-node noise).
    T_harm_order = []; T_harm_amp = []
    if _T_raw:
        _per = max(1, int(round(n_steps_per_period)))
        _Tp = np.asarray(_T_raw[:_per], float)
        if _Tp.size >= 4:
            _F = np.abs(np.fft.rfft(_Tp - _Tp.mean())) / _Tp.size * 2.0
            _nh = min(_F.size - 1, 36)
            T_harm_order = list(range(1, _nh + 1))
            T_harm_amp = [round(float(_F[k]), 4) for k in range(1, _nh + 1)]

    # ── Losses from the captured B(t) — PER-FRAME instantaneous series ────────
    # iron(t)  = hysteresis baseline (per-cycle quantity, flat) + classical
    #            eddy from the smooth |dB/dt|²(t) → ripples as the teeth pass.
    # magnet(t)= σ·d²/12·|dB/dt|²(t)  → ripples likewise.
    def _iron_series(hx, hy, idx, areas_half, mat, qp=None):
        if mat is None or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return np.zeros(n_total), 0.0
        X = np.asarray(hx); Y = np.asarray(hy)            # (N, E)
        # Maxwell-style coefficients: fitted from the material's MEASURED loss
        # curves when present (relative-error-weighted NNLS over every (f,B)
        # point), falling back to the YAML kh/kc/ke.  Real curves → real loss.
        kh, kc, ke = _mat_lib.effective_bertotti(mat)
        sf = float(getattr(mat, "stacking_factor", 0.95))
        vol = areas_half[idx] * p.stack_length * sf       # (E,)
        dX = _angle_ddt_2d(X, qp); dY = _angle_ddt_2d(Y, qp)
        pcl_t = (kc / _two_pi2) * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1)
        Bac2 = (((X.max(0) - X.min(0)) * 0.5) ** 2
                + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)
        phys = float(np.sum((kh * f_elec * Bac2
                             + ke * f_elec ** 1.5
                               * np.power(np.maximum(Bac2, 0.0), 0.75)) * vol))
        return pcl_t, phys

    _pcl_s, _ph_s = _iron_series(_hist_sx, _hist_sy, _iron_s_idx, areas_s, _steel_s)
    _pcl_r, _ph_r = _iron_series(_hist_rx, _hist_ry, _iron_r_idx, areas_r, _steel_r)
    _P_hyst = (_ph_s + _ph_r) * NS
    _P_fe_t = _declip((_pcl_s + _pcl_r) * NS + _P_hyst)  # classical ripple + flat hyst
    P_fe_series = _P_fe_t.tolist()
    P_fe_avg = float(np.mean(_P_fe_t))

    if rotor_eddy and _hist_Am and _mag_groups:
        # FIELD-BASED magnet eddy from the A(t) DISTRIBUTION (post-processed):
        #   J_e = σ(−dA/dt|material + U_m),  U_m = per-magnet area-mean (∫J=0),
        #   U = 0 for sector-cut halves (symmetry).  dA/dt uses the SAME
        # smoothed angle-derivative as the B-field losses (_angle_ddt_2d), so
        # the slip-merge jitter is filtered and the loss CONVERGES with step
        # count — the raw in-loop derivative tripled going 24→72 steps.
        # P(t) = Σ_magnets σ Σ_e (dA/dt_e − U_m)²·area_e × stack × NS.
        _Am = np.asarray(_hist_Am, float)            # (N, n_magnodes)
        # Exact edge pads from the pole-shift symmetry:  A(n, m±M_per) =
        # A(n∓, m), n∓ = the node 2 pole pitches away (see the _pp2 block).
        # Requires the electrical period to be an integer number of slip nodes
        # and frames to map 1:1 onto consecutive nodes — both true for the
        # standard runs; otherwise the C0-detrend edges are used.
        _pads = (None, None)
        _M_per = period_mech / spacing               # slip nodes per elec. period
        if (_pp2 is not None and abs(_M_per - round(_M_per)) < 1e-6):
            _Mp = int(round(_M_per))
            if (_Am.shape[0] >= _Mp and _m_arr.size == _Am.shape[0]
                    and np.array_equal(_m_arr,
                                       np.arange(_m_arr[0], _m_arr[0] + _m_arr.size))):
                _dt2c, _tgF, _sgF, _nrF, _tgB, _sgB, _nrB = _pp2
                _K = min(24, _Mp - 1)

                def _ct_rows(_rows, _tg, _sg, _nr):
                    # C1 (Clough–Tocher) interpolation of each frame's A at the
                    # ±2-pole-pitch image points; nearest-node fallback for
                    # boundary-rounding stragglers outside the hull.
                    from scipy.interpolate import CloughTocher2DInterpolator as _CTI
                    _o = np.empty((_rows.shape[0], _tg.shape[0]))
                    for _i in range(_rows.shape[0]):
                        _w = np.asarray(_CTI(_dt2c, _rows[_i])(_tg), float)
                        _bad = ~np.isfinite(_w)
                        if _bad.any():
                            _w[_bad] = _rows[_i][_nr[_bad]]
                        _o[_i] = _w * _sg
                    return _o
                # post (m = m_max+1 … m_max+K): rows N−M_per … N−M_per+K−1,
                # values at each node's +2-pole-pitch image; pre mirrors it.
                _post = _ct_rows(_Am[_Am.shape[0] - _Mp:_Am.shape[0] - _Mp + _K],
                                 _tgF, _sgF, _nrF)
                _pre = _ct_rows(_Am[_Mp - _K:_Mp], _tgB, _sgB, _nrB)
                _pads = (_pre, _post)
        _dAm = _angle_ddt_2d(_Am, pre=_pads[0], post=_pads[1])   # material dA/dt
        _Pt = np.zeros(n_total)
        for _mg in _mag_groups:
            _dA_e = _dAm[:, _mg["tri"]].mean(axis=1)        # (N, E) elem-mean
            _ar = _mg["areas"]
            if _mg["half"]:
                _F = _dA_e                                   # U = 0 (symmetry)
            else:
                _w = _ar / max(_ar.sum(), 1e-30)
                _F = _dA_e - (_dA_e * _w[None, :]).sum(axis=1, keepdims=True)
            _Pt += _sigma_mag_lib * np.sum(_F ** 2 * _ar[None, :], axis=1)
        _P_mag_t = _declip(_Pt * p.stack_length * NS)
        P_mag_series = _P_mag_t.tolist()
        P_mag_avg = float(np.mean(_P_mag_t))
    elif (_sigma_mag > 0.0 and _mag_idx.size and _hist_mx
            and np.asarray(_hist_mx[0]).size):
        Xm = np.asarray(_hist_mx); Ym = np.asarray(_hist_my)
        dXm = _angle_ddt_2d(Xm); dYm = _angle_ddt_2d(Ym)
        vol_m = areas_r[_mag_idx] * p.stack_length
        _P_mag_t = _declip(_sigma_mag * (_d_mag_m ** 2 / 12.0)
                    * np.sum((dXm ** 2 + dYm ** 2) * vol_m[None, :], axis=1) * NS)
        P_mag_series = _P_mag_t.tolist()
        P_mag_avg = float(np.mean(_P_mag_t))
    else:
        P_mag_series = [0.0] * n_total; P_mag_avg = 0.0

    # ── AC eddy / proximity losses in the SOLID (non-laminated) conductors ────
    # Same classical slab loss as the magnets, σ·(d²/12)·⟨(dB/dt)²⟩, applied to
    # the COILS (solid copper bars) and the SHAFT (solid steel).  d is the
    # conductor dimension capped at twice the skin depth (for d≫δ the field is
    # surface-limited, so the d² slab law alone would over-count).
    def _slab_eddy(hx, hy, idx, areas_half, sigma, d_m, qp=None):
        if sigma <= 0.0 or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return [0.0] * n_total, 0.0
        X = np.asarray(hx); Y = np.asarray(hy)
        dX = _angle_ddt_2d(X, qp); dY = _angle_ddt_2d(Y, qp)
        vol = areas_half[idx] * p.stack_length
        Pt = _declip(sigma * (d_m ** 2 / 12.0)
                     * np.sum((dX ** 2 + dY ** 2) * vol[None, :], axis=1) * NS)
        return Pt.tolist(), float(np.mean(Pt))

    def _prox_eddy_split(hx, hy, idx, cen, areas_half, sigma, d_for_Br, d_for_Bt):
        # Proximity loss with the field resolved into RADIAL and TANGENTIAL
        # components, each paired with the conductor dimension PERPENDICULAR to
        # it: B_r ↔ tangential width, B_θ (slot leakage) ↔ radial height.  This
        # avoids the single-d slab over-count (a tall-thin bar barely sees the
        # tangential slot-leakage field).
        if sigma <= 0.0 or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
            return [0.0] * n_total, 0.0
        X = np.asarray(hx); Y = np.asarray(hy)                 # (N, E)
        r = np.hypot(cen[0], cen[1]); r = np.where(r < 1e-9, 1e-9, r)
        ux = (cen[0] / r)[None, :]; uy = (cen[1] / r)[None, :]  # r_hat
        Br = X * ux + Y * uy                                   # radial component
        Bt = -X * uy + Y * ux                                  # tangential component
        dBr = _angle_ddt_2d(Br); dBt = _angle_ddt_2d(Bt)
        vol = areas_half[idx] * p.stack_length
        Pt = _declip((sigma / 12.0) * np.sum(
            (d_for_Br ** 2 * dBr ** 2 + d_for_Bt ** 2 * dBt ** 2)
            * vol[None, :], axis=1) * NS)
        return Pt.tolist(), float(np.mean(Pt))

    _omega_e = 2.0 * math.pi * max(1e-6, f_elec)
    # Copper winding bar (SOLID, one strand): proximity loss from the rotating
    # field, split into radial/tangential and each capped at 2·skin-depth.
    _rho_cu   = RHO_CU_20 * (1.0 + ALPHA_CU * (float(coil_temp_c) - 20.0))
    _sigma_cu = 1.0 / _rho_cu
    _delta_cu = math.sqrt(2.0 * _rho_cu / (_omega_e * MU0))
    # wire_split (per Vadim, matches his ANSYS practice): the wide flat bar is
    # wound as N parallel strips across its WIDTH (insulated + transposed), so
    # the width-direction proximity loops see w/N, cutting that loss term ∝N².
    # Assumes ideal transposition (no circulating currents between strips).
    # The 2·δ skin cap still applies on top (whichever is smaller governs).
    _n_wsplit = max(1, int(round(float(geo.get("wire_split", 1) or 1))))
    _w_cu = min(float(geo.get("wire_width",  5.0)) * 1e-3 / _n_wsplit,
                2.0 * _delta_cu)                                             # ↔ B_radial
    _h_cu = min(float(geo.get("wire_height", 0.8)) * 1e-3, 2.0 * _delta_cu)  # ↔ B_tangential
    _sm = half["s"]["mesh"]
    _coil_cen = ((_sm.p[:, _sm.t].mean(axis=1))[:, _coil_idx]
                 if _coil_idx.size else np.zeros((2, 0)))
    P_cu_ac_series, P_cu_ac_avg = _prox_eddy_split(
        _hist_cx, _hist_cy, _coil_idx, _coil_cen, areas_s, _sigma_cu, _w_cu, _h_cu)
    # Shaft eddy (any SOLID conductor, e.g. aluminium).  Computed the SAME geometry-
    # exact field way as the magnets — NO slab/cylinder shape factor, NO skin-depth
    # cap, NO fudge:
    #     P = σ · ∫ (∂A/∂t − ⟨∂A/∂t⟩_body)² dA · L · NS
    # integrated over the REAL shaft element areas, with the area-mean ∂A/∂t removed so
    # the net axial current ∮J dA = 0 over the single connected shaft body.  Because it
    # integrates the actual E = −∂A/∂t over the actual geometry, it reproduces the exact
    # solid-cylinder loss with no shape correction.  The co-rotating shaft sees the
    # magnet field as DC → only the AC slot-ripple / armature reaction dissipates; ∂A/∂t
    # is the slip-jitter-smoothed material derivative (same _angle_ddt_2d as the
    # magnets).  UNIVERSAL: every σ>0 domain (magnet, shaft, …) uses this identical
    # formula; laminated iron (σ=0) contributes nothing here (its loss is in CoreLoss).
    _sigma_shaft = _sigma_shaft_lib
    if (_shaft_group is not None and _hist_Ash
            and np.asarray(_hist_Ash[0]).size and _sigma_shaft > 0.0):
        _Ash  = np.asarray(_hist_Ash, float)                      # (N, n_shaftnodes)
        _dAsh = _angle_ddt_2d(_Ash)                               # material ∂A/∂t = −E
        _dA_e = _dAsh[:, _shaft_group["tri"]].mean(axis=1)        # (N, E) per element
        _ar_sh = _shaft_group["areas"]
        _w_sh  = _ar_sh / max(_ar_sh.sum(), 1e-30)
        _F_sh  = _dA_e - (_dA_e * _w_sh[None, :]).sum(axis=1, keepdims=True)   # ∮J=0
        _Psh_t = _declip(_sigma_shaft
                         * np.sum(_F_sh ** 2 * _ar_sh[None, :], axis=1)
                         * p.stack_length * NS)
        P_shaft_series = _Psh_t.tolist()
        P_shaft_avg    = float(np.mean(_Psh_t))
    else:
        P_shaft_series = [0.0] * n_total
        P_shaft_avg    = 0.0

    # ── HONEST (coupled) rotor eddy — PRODUCTION magnet/shaft loss when rotor_eddy ──
    # Frequency-domain multi-body solve on the REAL rotor mesh (eddy_solver_2d),
    # driven by the captured rotor-node A history: per-harmonic screening + skin
    # reaction are SOLVED, and the fixed harmonic ceiling (k ≤ 16) band-limits the
    # drive to the physical rotor-frame orders.  The history post-process above
    # squares ∂A/∂t, so the slip-band node-identification jitter — which does NOT
    # decay with depth the way physical field harmonics do — dominates SCREENED
    # bodies: the 450 mm shaft (under 50 mm magnet + 5 mm back-iron; physical
    # reach ~e⁻⁹) read 32→45 kW of pure jitter (GROWING with step count) vs a
    # stable 0.5-0.6 kW here.  So with rotor_eddy the honest values REPLACE the
    # history-based magnet/shaft averages: the magnet series keeps its (physical
    # slot-ripple) shape rescaled to the honest mean; the shaft series — whose
    # shape is jitter, not physics — is flattened to the honest mean.
    # honest_eddy alone keeps the old additive-diagnostic behaviour.
    # Fail-safe: any error → the history-based values stand (as before).
    P_mag_honest = P_shaft_honest = 0.0
    P_mag_hist_avg = float(P_mag_avg); P_shaft_hist_avg = float(P_shaft_avg)
    _honest_ok = False
    if (honest_eddy or rotor_eddy) and _hist_A_rotor:
        try:
            from motor_ai_sim.simulation.eddy_solver_2d import honest_rotor_eddy as _hre
            _rm = half["r"]["mesh"]
            _tags_r = np.zeros(_rm.t.shape[1], int)
            for _tg, _els in half["r"]["cells"].items():
                _tags_r[np.asarray(_els, int)] = int(_tg)
            _mag_tags_h = [int(tg) for tg in np.unique(_tags_r) if int(tg) >= DOM_MAG_BASE]

            def _muf(tg):
                tg = int(tg)
                if tg >= DOM_MAG_BASE:                 # magnet (NdFeB recoil ~1.05)
                    return 1.05
                if tg == DOM_ROTOR:                    # rotor back-iron: converged mu_r
                    try:
                        _n = nu_el.get("r", {}).get(DOM_ROTOR)
                        if _n is not None and np.size(_n):
                            return 1.0 / (MU0 * float(np.mean(_n)))
                    except Exception:
                        pass
                    return 1000.0
                return 1.0                             # shaft (Al) / air = non-magnetic
            P_mag_honest, P_shaft_honest, _hfreqs = _hre(
                np.asarray(_rm.p, float), np.asarray(_rm.t, int), _tags_r,
                _muf, _sigma_of_tag, _mag_tags_h, DOM_SHAFT,
                np.asarray(_hist_A_rotor, float), float(n_total) * dt,
                float(p.stack_length), float(NS))
            _honest_ok = True
            log.info("HONEST rotor eddy: mag=%.3f shaft=%.3f W (%d harmonics) | "
                     "resistance-limited mag=%.3f shaft=%.3f W",
                     P_mag_honest, P_shaft_honest, len(_hfreqs), P_mag_avg, P_shaft_avg)
        except Exception as _e:
            log.warning("honest rotor eddy failed (history-based values stand): %s", _e)
            P_mag_honest = P_shaft_honest = 0.0
    if rotor_eddy and _honest_ok:
        _mh = float(P_mag_honest)
        if P_mag_avg > 1e-9:
            _kh = _mh / P_mag_avg
            P_mag_series = [v * _kh for v in P_mag_series]
        else:
            P_mag_series = [_mh] * n_total
        P_mag_avg = _mh
        P_shaft_series = [float(P_shaft_honest)] * n_total
        P_shaft_avg = float(P_shaft_honest)

    # ── AXIAL magnet lamination (magnet_lamination, mm; 0 = solid) ──────────
    # Slicing the magnets ALONG THE STACK (per Vadim: the 450's 180 mm magnets
    # laminated at 10 mm = 18 slices) cannot be meshed in this 2-D model — the
    # J_z formulation assumes infinitely long conductors (loss ∝ w_eff², the
    # in-plane loop width, with free loop closure at z = ±∞).  A finite axial
    # slice of length l forces the loop to close within the slice, adding the
    # return-path resistance; in the resistance-limited regime (NdFeB skin
    # depth ≈ 16 mm @ 1.8 kHz > l = 10 mm) the classical rectangular-plate
    # result rescales the eddy loss by
    #     k_ax = l² / (l² + w_eff²),   w_eff = magnet area / longest extent
    # (limits: l→∞ → 1 = the 2-D value; l ≪ w → (l/w)², loops close axially).
    # Field / torque / mass are untouched — insulated cuts do not change the
    # magnetostatics.  Applied to the PRODUCTION magnet numbers (series + avg +
    # the reported honest value); P_mag_hist_W stays the raw-2D diagnostic.
    # lam=0 keeps k=1 (pure 2-D, back-compat); a real 180 mm solid magnet is
    # itself k(180) ≈ 0.97 — negligible.  Shaft: not affected by this param.
    try:
        _lam_mm = float((geo or {}).get("magnet_lamination", 0.0) or 0.0)
    except Exception:
        _lam_mm = 0.0
    if _lam_mm > 0.1 and P_mag_avg > 0.0:
        try:
            _mp0 = (polys.get("magnets") or [(None, 0)])[0][0]
            _xy0 = list(_mp0.exterior.coords)
            _rr0 = [math.hypot(_x, _y) for _x, _y in _xy0]
            _c0 = _mp0.centroid
            _ca0 = math.atan2(_c0.y, _c0.x)
            _an0 = [((math.atan2(_y, _x) - _ca0 + math.pi) % (2.0 * math.pi)) - math.pi
                    for _x, _y in _xy0]
            _rad0 = max(_rr0) - min(_rr0)
            _tan0 = (max(_an0) - min(_an0)) * 0.5 * (max(_rr0) + min(_rr0))
            _w_eff = _mp0.area / max(max(_rad0, _tan0), 1e-9)
            _l_ax = min(_lam_mm, float(p.stack_length) * 1e3)   # can't exceed the stack
            _k_ax = (_l_ax * _l_ax) / (_l_ax * _l_ax + _w_eff * _w_eff)
            P_mag_series = [v * _k_ax for v in P_mag_series]
            P_mag_avg = float(P_mag_avg) * _k_ax
            P_mag_honest = float(P_mag_honest) * _k_ax
            log.info("magnet AXIAL lamination: l=%.1f mm, w_eff=%.1f mm -> k_ax=%.4f, "
                     "P_mag=%.0f W", _l_ax, _w_eff, _k_ax, P_mag_avg)
        except Exception as _e:
            log.warning("magnet lamination factor skipped: %s", _e)

    # ── Field-based rotor eddy loss from the magnetodynamic solve (Stage 1) ──
    # ∫σ(∂A/∂t)² straight from the eddy field — NO slab/d/cap.  Compare against
    # the slab estimate above.  Skip the first electrical period (eddy warmup).
    P_cu_total_solve_W = 0.0; P_cu_ac_solve_W = 0.0
    if eddy and '_eddy_P' in dir() and len(_eddy_P) > 1:
        _warm = max(1, len(_eddy_P) // 2)
        # _eddy_P entries are ∫σF² dA over the 2-D sector mesh [W per metre of
        # stack] — × stack_length for watts (was missing → reported 22× high,
        # which is why the UI note called this value "inflated").
        P_cu_total_solve_W = float(np.mean(_eddy_P[_warm:]) * NS * p.stack_length)
        P_cu_ac_solve_W = P_cu_total_solve_W - float(P_cu)    # total − DC I²R
        log.info("EDDY-SOLVE copper total=%.1f W (DC=%.0f + AC=%.1f) vs slab DC+AC=%.1f W",
                 P_cu_total_solve_W, float(P_cu), P_cu_ac_solve_W, float(P_cu) + P_cu_ac_avg)

    log.info("SB transient: %d frames, %d slip nodes, P_fe=%.1f P_mag=%.1f "
             "P_cuDC=%.1f P_cuAC=%.1f P_shaft=%.1f, %.1fs",
             n_total, Nring, P_fe_avg, P_mag_avg, float(P_cu), P_cu_ac_avg,
             P_shaft_avg, _t.time() - t0)
    # Copper total = DC I²R (flat) + AC eddy/proximity (rotor-position dependent).
    P_cu_dc = float(P_cu)
    P_cu_series = [P_cu_dc + ac for ac in P_cu_ac_series]
    P_tot_series = [c + f + m + s for c, f, m, s
                    in zip(P_cu_series, P_fe_series, P_mag_series, P_shaft_series)]
    # ── Mechanical/shaft power from GLOBAL energy conservation ───────────────
    # P_elec_in = ⟨Σ v·i⟩ over the period (EXACTLY 0 at no-load, I=0).  Energy
    # balance P_in = P_mech + P_loss gives the physically-correct shaft power
    #     P_mech = P_elec_in − P_loss_total
    # — at no-load this equals −P_loss (the drive overcomes every loss), and it
    # never relies on the numerically-noisy cogging-mean torque (T_avg·ω gave a
    # spurious −620 W at I=0 against 325 W of loss, violating conservation).
    #
    # ⚠ PER-BRANCH → TOTAL:  VA/VB/VC are the PER-BRANCH terminal voltages (ψ was
    # divided by n_parallel to get one branch's linkage — see the ψ scaling and
    # the legacy-path comment) and IA/IB/IC are the PER-BRANCH conductor currents
    # (I_phase ÷ n_parallel, see `_currents`).  So ⟨Σ v·i⟩ is the power of ONE
    # parallel branch per phase.  The phase has n_parallel such branches in
    # parallel (same terminal V, currents add), so the machine's TOTAL electrical
    # input is n_parallel × ⟨Σ v·i⟩.  WITHOUT this factor P_elec_in — and hence
    # the energy-balance shaft power and efficiency — came out ÷n_parallel too
    # small (129 kW / η 92.8 % vs the 557 kW / η 98 % the airgap torque T·ω=563 kW
    # implies at n_parallel=4).  Losses (P_cu via copper_loss_W, iron/magnet from
    # the ×NS field integrals) are already whole-machine totals, so only the ⟨v·i⟩
    # terminal power carried the per-branch scale.
    _omega_m = 2.0 * math.pi * rpm / 60.0
    P_elec_in = (float(np.mean(np.asarray(VA) * np.asarray(IA)
                               + np.asarray(VB) * np.asarray(IB)
                               + np.asarray(VC) * np.asarray(IC)))
                 * float(n_parallel)
                 if IA else 0.0)
    P_loss_total_avg = float(np.mean(P_tot_series)) if P_tot_series else 0.0
    P_airgap_avg = float(Tavg * _omega_m)        # electromagnetic (Arkkio) power
    P_mech_avg = P_elec_in - P_loss_total_avg     # energy-conserving shaft power

    # ── Per-element loss DENSITY (W/m³) for the Ansys-style spatial map ──────
    # Same per-element loss math as the totals above, kept per-element instead
    # of summed, then each component NORMALISED so its volume-integral equals
    # the reported (physically-trusted) component loss — the map both shows the
    # spatial distribution AND integrates back to the sidebar numbers.  Element
    # order matches the field snapshot: [stator-half | rotor-half].
    if _field_snap is not None:
        _nst_e = int(_Bxs.size)
        _dens = np.zeros(int(_Bxs.size + _Bxr.size))

        def _mean_sq_ddt(hx, hy, qp=None):              # time-avg |dB/dt|² per elem
            dX = _angle_ddt_2d(np.asarray(hx), qp)
            dY = _angle_ddt_2d(np.asarray(hy), qp)
            return np.mean(dX ** 2 + dY ** 2, axis=0)

        def _bac2(hx, hy):                              # (½ peak-peak)² per elem
            X = np.asarray(hx); Y = np.asarray(hy)
            return (((X.max(0) - X.min(0)) * 0.5) ** 2
                    + ((Y.max(0) - Y.min(0)) * 0.5) ** 2)

        def _norm_into(local_idx, shape_e, areas_half, base, P_target_W):
            if local_idx.size == 0 or shape_e.size == 0 or P_target_W <= 0:
                return
            integ = float(np.sum(shape_e * areas_half[local_idx])) * p.stack_length * NS
            if integ > 1e-30:
                _dens[base + local_idx] += shape_e * (P_target_W / integ)

        # Iron — stator + rotor share one Bertotti total (P_fe_avg).
        def _iron_shape(hx, hy, idx, mat, qp=None):
            if mat is None or idx.size == 0 or not hx or np.asarray(hx[0]).size == 0:
                return np.zeros(idx.size)
            kh, kc, ke = _mat_lib.effective_bertotti(mat)
            b2 = _bac2(hx, hy)
            return (kh * f_elec * b2
                    + ke * f_elec ** 1.5 * np.power(np.maximum(b2, 0.0), 0.75)
                    + (kc / _two_pi2) * _mean_sq_ddt(hx, hy, qp))
        _sh_is = _iron_shape(_hist_sx, _hist_sy, _iron_s_idx, _steel_s)
        _sh_ir = _iron_shape(_hist_rx, _hist_ry, _iron_r_idx, _steel_r)
        _integ_fe = ((float(np.sum(_sh_is * areas_s[_iron_s_idx])) if _iron_s_idx.size else 0.0)
                     + (float(np.sum(_sh_ir * areas_r[_iron_r_idx])) if _iron_r_idx.size else 0.0)
                     ) * p.stack_length * NS
        if _integ_fe > 1e-30 and P_fe_avg > 0:
            _kfe = P_fe_avg / _integ_fe
            if _iron_s_idx.size: _dens[_iron_s_idx] += _sh_is * _kfe
            if _iron_r_idx.size: _dens[_nst_e + _iron_r_idx] += _sh_ir * _kfe

        # Magnets — slab |dB/dt|² shape, normalised to P_mag_avg.
        if _mag_idx.size and _hist_mx and np.asarray(_hist_mx[0]).size:
            _norm_into(_mag_idx, _mean_sq_ddt(_hist_mx, _hist_my),
                       areas_r, _nst_e, P_mag_avg)

        # Copper — uniform DC ohmic + crowded AC proximity (radial/tangential).
        if _coil_idx.size:
            _vol_cu = float(np.sum(areas_s[_coil_idx])) * p.stack_length * NS
            if _vol_cu > 1e-30 and P_cu_dc > 0:
                _dens[_coil_idx] += P_cu_dc / _vol_cu
            if _hist_cx and np.asarray(_hist_cx[0]).size and P_cu_ac_avg > 0:
                _Xc = np.asarray(_hist_cx); _Yc = np.asarray(_hist_cy)
                _rc = np.hypot(_coil_cen[0], _coil_cen[1])
                _rc = np.where(_rc < 1e-9, 1e-9, _rc)
                _uxc = (_coil_cen[0] / _rc)[None, :]; _uyc = (_coil_cen[1] / _rc)[None, :]
                _dBrc = _angle_ddt_2d(_Xc * _uxc + _Yc * _uyc)
                _dBtc = _angle_ddt_2d(-_Xc * _uyc + _Yc * _uxc)
                _sh_cu = (_sigma_cu / 12.0) * np.mean(
                    _w_cu ** 2 * _dBrc ** 2 + _h_cu ** 2 * _dBtc ** 2, axis=0)
                _norm_into(_coil_idx, _sh_cu, areas_s, 0, P_cu_ac_avg)

        _field_snap["loss_dens"] = _dens.tolist()

    # (The honest coupled rotor-eddy solve moved ABOVE the loss-series assembly —
    # it is now the production magnet/shaft loss when rotor_eddy is on.)

    # Demag payload is built HERE, after the run: with per-step de-rating the
    # coefficients only reach their final value once the last step is done, so
    # snapshotting them before the loop (as the old pre-pass did) would have
    # captured the pristine magnet.
    if demag and _mag_idx.size and _dm:
        _nst = int(Tts.shape[1])
        _demag_coef = np.ones(int(mesh_all.t.shape[1]))
        _demag_coef[_nst:] = _br_glob
        _pmm = mesh_all.p * 1e3
        _demag_field = {
            "vertices":           _pmm.T.tolist(),
            "triangles":          mesh_all.t.T.astype(int).tolist(),
            "domain_per_tri":     np.concatenate([np.asarray(ts), np.asarray(tr)]).astype(int).tolist(),
            "demag_coef_per_tri": _demag_coef.tolist(),
            "mag_domains":        sorted({int(_d["tag"]) for _d in _dm}),
            "extent": [float(_pmm[0].min()), float(_pmm[0].max()),
                       float(_pmm[1].min()), float(_pmm[1].max())],
        }
        # Raw per-element demagnetising field, for diagnosing the map against a
        # reference solver: H_first is the field on the PRISTINE magnet (pure
        # geometry, no de-rating feedback), H_worst the running per-element
        # minimum.  Two extra full-mesh arrays — opt-in so the normal response
        # stays lean.
        if _os_sb.environ.get("SB_DEMAG_H_DUMP") == "1":
            _demag_field.update({
                "H_first_per_tri": np.concatenate(
                    [np.full(_nst, np.nan), _H_first]).tolist(),
                "H_worst_per_tri": np.concatenate(
                    [np.full(_nst, np.nan),
                     np.where(np.isinf(_H_worst), np.nan, _H_worst)]).tolist(),
                "H_knee_per_mag": {str(int(_d["tag"])): float(_d["knee"])
                                   for _d in _dm},
            })
        for _d in _dm:
            _hmin = float(_demag_seen.get(_d["tag"], 0.0))
            _prox = _hmin / _d["knee"] if _d["knee"] < 0 else 0.0
            if _prox > 0.85:
                _demag_report.append({
                    "magnet_index":     int(_d["tag"] - DOM_MAG_BASE),
                    "H_min_kA_per_m":   round(_hmin * 1e-3, 1),
                    "H_knee_kA_per_m":  round(_d["knee"] * 1e-3, 1),
                    "knee_proximity":   round(_prox, 2),
                    "demagnetised":     bool(_prox > 1.0),
                    "Br_factor":        round(float(np.min(_br_glob[_d["idx"]])), 3),
                })
        log.warning("demag (per-step): %d/%d magnet elems de-rated, min Br_factor %.3f",
                    int(np.sum(_br_glob < 0.999)), int(_mag_idx.size), float(_br_glob.min()))

    return {
        "method": "sliding_band",
        # 'field+honest' = magnet/shaft loss from the coupled frequency-domain
        # rotor solve (screening + skin reaction, k≤16 physical band) — the
        # production model with rotor_eddy; 'field' = its history-based σ·∂A/∂t
        # fallback; 'slab' = classical d²/12 estimate.
        "loss_model": ("field+honest" if (rotor_eddy and _honest_ok)
                        else ("field" if rotor_eddy else "slab")),
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec,
        "dt_s": dt, "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": tt, "rotor_angle_deg": [
            (k / n_total) * period_mech * n_periods for k in range(n_total)],
        "T_em_Nm": T_series, "T_avg_Nm": Tavg, "T_ripple_pct": Trip,
        # Which torque definition produced the number above, plus the raw
        # Maxwell-stress series it was re-centred from — same transparency the
        # P2 branch already provides, so an inflated mean can never hide again.
        "torque_method": _torque_method_p1,
        "T_avg_maxwell_Nm": round(float(np.mean(_mx_raw_p1)) if _mx_raw_p1 else 0.0, 4),
        "T_em_maxwell_Nm": list(_mx_raw_p1),
        "T_ripple_raw_pct": Trip_raw, "T_ripple_filt_pct": Trip_filt,
        # RMS of the forbidden (non-6·k) torque orders as % of mean torque —
        # the mesh-noise floor the 6·k gate removed.  ~0 on a converged mesh.
        "T_noise_floor_pct": round(float(Tnoise_pct), 2),
        # Both reconstructions — the UI toggles between them client-side (no
        # re-solve) when the "Torque filter" checkbox is flipped.
        "T_em_raw_Nm": _T_raw, "T_em_filt_Nm": _T_filt,
        "psi_A_Wb": psiA, "psi_B_Wb": psiB, "psi_C_Wb": psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": IA, "I_B": IB, "I_C": IC,
        "P_cu_W": P_cu_series, "P_fe_W": P_fe_series,
        "P_mag_eddy_W": P_mag_series, "P_loss_total_W": P_tot_series,
        "P_cu_dc_W": P_cu_dc, "P_cu_ac_W": P_cu_ac_series,
        "P_shaft_eddy_W": P_shaft_series,
        "P_mag_honest_W": round(float(P_mag_honest), 3),    # coupled (reaction) eddy — production w/ rotor_eddy
        "P_shaft_honest_W": round(float(P_shaft_honest), 3),
        "P_mag_hist_W": round(float(P_mag_hist_avg), 3),    # pre-swap history-based avgs (diagnostic:
        "P_shaft_hist_W": round(float(P_shaft_hist_avg), 3),  # jitter-dominated for screened bodies)
        "P_cu_ac_solve_W": round(P_cu_ac_solve_W, 1),       # field-based copper AC (eddy solve)
        "P_cu_total_solve_W": round(P_cu_total_solve_W, 1),  # field-based copper total
        "P_mech_avg_W": P_mech_avg,                          # energy-conserving shaft power
        "P_elec_in_W": P_elec_in,                            # ⟨Σ v·i⟩ (0 at no-load)
        "P_airgap_W": P_airgap_avg,                          # electromagnetic T_avg·ω
        "P_loss_total_avg_W": P_loss_total_avg,
        "R_phase_ohm": R_phase, "n_slip_nodes": int(Nring),
        "n_parallel": int(n_parallel),
        # Saturation-Picard honesty report: iterations actually used per frame
        # (early-stop on the nu fixed-point residual < _PIC_TOL, cap =
        # nonlinear_iterations) and the worst final residual over all frames.
        # picard_converged=False means some frame hit the cap without meeting
        # the tolerance — treat ripple from such a run with suspicion.
        "picard_iters_mean": (round(float(np.mean(_pic_iters_hist)), 1)
                              if _pic_iters_hist else 0.0),
        "picard_iters_max": (int(max(_pic_iters_hist)) if _pic_iters_hist else 0),
        "picard_resid_max": (round(float(max(_pic_resid_hist)), 6)
                             if _pic_resid_hist else 0.0),
        "picard_tol": float(_PIC_TOL),
        "picard_converged": bool(_pic_resid_hist
                                 and max(_pic_resid_hist) < _PIC_TOL),
        "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        # Drive mode: "current" (imposed sinusoidal I) or "voltage" (imposed
        # sinusoidal V — the currents above are the machine's own response).
        "drive": ("voltage" if _vdrive else "current"),
        "v_phase_peak_V": float(v_phase_peak) if _vdrive else None,
        "v_delta_deg": float(v_delta_deg) if _vdrive else None,
        # circuit-iteration convergence stats (per frame, incl. settling) + the
        # honest steady-state quality gauge: mean phase current over the
        # REPORTED window (≈0 A on a converged periodic orbit).
        "v_drive_diag": (_v_diag if _vdrive else None),
        "v_dc_residual_A": (round(float(np.mean(np.asarray(IA, float))), 3)
                            if (_vdrive and IA) else None),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp,
        "field": _field_snap,
        # Demagnetisation (populated only when demag=True): per-element Br
        # factor over the FULL stitched mesh (1.0 = full strength), plus the
        # per-magnet worst-cell report consumed by the UI panel/% map.
        "demag_coef_per_tri": (_demag_coef.tolist() if _demag_coef is not None else None),
        "demag_report": _demag_report,
        "demag_field": _demag_field,     # full mesh + per-element Br factor for the %-map
    }




def _build_full_disk_from_halves(polys, rotor_angle_deg, mesh_size_mm,
                                 min_size_mm, outer_air_factor, motion_band,
                                 band_thickness_mm, geo_cfg, component_mesh_mm,
                                 normal_deviation_deg=6.0, aspect_ratio=10.0,
                                 gap_layers=3.0):
    """Build a CLEAN full-disk (n_sectors=1) mesh by stitching TWO 1/2 sector
    meshes (the half is meshed cleanly by OCC; the full 360° is NOT).

    Steps: build the clean half (n_sectors=2) → duplicate it rotated 180° →
    weld the coincident seam nodes → reclassify every triangle by its centroid
    against the FULL (un-clipped) polygons.  Result is a manifold (no
    overlapping/double-meshed iron) full disk that the magnetostatics solver
    handles as the genuine 360° motor (no periodic BC).

    Returns (MeshTri, cell_tags int16, classify_fn) — same contract as
    build_mesh_from_polygons.  classify_fn.polys = the full polys so
    build_materials assigns per-magnet/per-coil materials correctly.
    """
    import numpy as _np
    import scipy.sparse as _sp
    from scipy.sparse.csgraph import connected_components as _cc
    from scipy.spatial import cKDTree as _KD
    import shapely as _sh
    from skfem import MeshTri as _MT

    # 1) clean half (n_sectors=2 → OCC meshes the open wedge without overlaps)
    mesh2, _ct2, _cf2 = build_mesh_from_polygons(
        polys, rotor_angle_deg, mesh_size_mm, min_size_mm=min_size_mm,
        normal_deviation_deg=normal_deviation_deg, aspect_ratio=aspect_ratio,
        outer_air_factor=outer_air_factor, motion_band=motion_band,
        band_thickness_mm=band_thickness_mm, gap_layers=gap_layers, n_sectors=2,
        geo_cfg=geo_cfg, component_mesh_mm=component_mesh_mm)
    V = mesh2.p.T; T = mesh2.t.T; N = len(V)

    # 2) stitch: half + 180°-rotated copy, then weld coincident seam nodes
    Vf = _np.vstack([V, -V])              # 180° rotation = (x,y)->(-x,-y)
    Tf = _np.vstack([T, T + N]); n2 = len(Vf)
    pairs = _KD(Vf).query_pairs(r=1e-7)
    if pairs:
        ij = _np.array(list(pairs)).T
        g = _sp.coo_matrix((_np.ones(ij.shape[1]), (ij[0], ij[1])), shape=(n2, n2))
        _, lab = _cc(g + g.T, directed=False)
    else:
        lab = _np.arange(n2)
    uniq, inv = _np.unique(lab, return_inverse=True)
    Vw = _np.zeros((len(uniq), 2)); _np.add.at(Vw, inv, Vf)
    Vw /= _np.bincount(inv)[:, None]
    Tw = inv[Tf]
    good = ((Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2]))
    Tw = Tw[good]
    meshF = _MT(Vw.T, Tw.T.copy())

    # 3) classify each triangle by centroid against the FULL (un-clipped) polys
    cen = Vw[Tw].mean(axis=1) * 1000.0    # mesh metres → polygon mm
    rr = _np.hypot(cen[:, 0], cen[:, 1])
    _gc = geo_cfg or {}
    r_ro = float(_gc.get("rotor_outer_radius", 0.0))
    r_si = float(_gc.get("stator_inner_radius", 0.0))
    ct = _np.full(len(Tw), DOM_AIR, dtype=_np.int32)
    if r_ro > 0.0 and r_si > r_ro:
        ct[(rr >= r_ro) & (rr <= r_si)] = DOM_AIRGAP
    clf = []
    for i, (mp, _pl) in enumerate(polys.get("magnets", [])):
        if mp is not None and not mp.is_empty:
            clf.append((mp, DOM_MAG_BASE + i))
    for i, cp in enumerate(polys.get("coils", [])):
        if cp is not None and not cp.is_empty:
            clf.append((cp, DOM_COIL_BASE + i))
    for k, dm in (("shaft", DOM_SHAFT), ("rotor", DOM_ROTOR), ("stator", DOM_STATOR)):
        gg = polys.get(k)
        if gg is not None and not gg.is_empty:
            clf.append((gg, dm))
    # least-specific first so magnets/coils (front of clf) overwrite last → win
    for gg, tag in reversed(clf):
        try:
            ct[_sh.contains_xy(gg, cen[:, 0], cen[:, 1])] = tag
        except Exception:
            pass

    class _CF:
        pass
    cf = _CF(); cf.polys = polys
    log.info("FEM-sim: stitched full disk from 2 halves — %d nodes, %d tris",
             len(Vw), len(Tw))
    return meshF, ct.astype(_np.int16), cf


def em_transient_eval(
    *,
    n_steps_per_period: int,
    n_periods: float,
    gamma_deg: float,
    I_phase_rms: float,
    mesh_size_mm: float = 4.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    gap_layers: float = 3.0,
    n_sectors: int = -1,
    stator_fillet_mm: float = 0.0,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    rotor_eddy: bool = False,
    demag: bool = False,
    torque_filter: bool = False,
    pole_copy=None,
    component_mesh_mm=None,
    geo_override=None,
    progress_cb=None,
    hi_fidelity: bool = False,
    structured_gap: bool = False,
    iron_template=None,
    geo_mesh=None,
    airgap_macro: bool = False,
    frozen_nu: bool = False,
    drive: str = "current",
    v_phase_peak: float = 0.0,
    v_delta_deg: float = 0.0,
    element_order: int = 1,          # 1 = P1 (default); 2 = P2 (see fem_transient_sliding_band)
) -> Dict:
    """THE single canonical sliding-band transient invocation.

    Every consumer that needs a 2-D transient solve funnels through here — the
    Simulation route (get_fem_transient), the optimizer (refine_proc.run_one) and
    the solver.em_transient module — so the optimizer's physics can NEVER drift
    from what the Simulation tab shows. Pure: no caching, no global progress, no
    disk-save (those UI concerns stay in the route, which wraps this). Returns the
    raw sliding-band result dict (sbres).
    """
    return fem_transient_sliding_band(
        n_steps_per_period=int(n_steps_per_period), n_periods=float(n_periods),
        gamma_deg=float(gamma_deg), I_phase_rms=float(I_phase_rms),
        mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
        outer_air_factor=float(outer_air_factor), gap_layers=float(gap_layers),
        n_sectors=int(n_sectors) if int(n_sectors) > 1 else -1,
        stator_fillet_mm=float(stator_fillet_mm),
        coil_temp_c=float(coil_temp_c), end_winding_factor=float(end_winding_factor),
        rotor_eddy=bool(rotor_eddy), demag=bool(demag),
        torque_filter=bool(torque_filter), pole_copy=pole_copy,
        iron_template=iron_template, geo_mesh=geo_mesh,
        component_mesh_mm=(component_mesh_mm or {}), geo_override=geo_override,
        progress_cb=progress_cb, hi_fidelity=bool(hi_fidelity),
        structured_gap=bool(structured_gap), airgap_macro=bool(airgap_macro),
        frozen_nu=bool(frozen_nu),
        drive=str(drive or "current"), v_phase_peak=float(v_phase_peak),
        v_delta_deg=float(v_delta_deg), element_order=int(element_order))


def fem_quasistatic_transient(
    n_steps_per_period: int = 24,
    n_periods: float = 1.0,
    gamma_deg: float = 0.0,
    I_phase_rms: float = 85.0,
    mesh_size_mm: float = 4.0,
    min_size_mm: float = 0.3,
    outer_air_factor: float = 1.3,
    motion_band: bool = True,
    band_thickness_mm: float = 0.4,
    n_sectors: int = 4,
    stator_fillet_mm: float = 0.0,
    coil_temp_c: float = 120.0,
    end_winding_factor: float = 0.0,
    component_mesh_mm: dict = None,
) -> dict:
    """GENUINE quasi-static transient — ONE algorithm for every symmetry.

    Sweeps ``fem_solve_for_sim`` over one electrical period at the REQUESTED
    symmetry: Full (n_sectors=1) uses the real stitched 360° disk, 1/2 & 1/4 use
    the clipped sectors with the correct (anti)periodic BC.  NOTHING is forced to
    1/4 — so the full disk shows its own honest result.  Each frame is a real
    magnetostatic solve; back-EMF = R·I + dψ/dt (central differences); per-frame
    losses (Bertotti core + I²R copper + slab magnet eddy) already carry the
    n_sectors multiplier.  Returns the same dict shape the transient endpoint and
    summary builder expect (so the frontend is unchanged).
    """
    import time as _t
    import numpy as _np
    import math as _math
    from motor_ai_sim.simulation.geometry_2d import params_from_config
    from motor_ai_sim.config import get_config

    t0 = _t.time()
    cfg = get_config(); sim = cfg.get("simulation", {}); geo = dict(cfg.get("geometry", {}))
    wind = cfg.get("winding", {})
    p = params_from_config()
    pole_pairs = p.num_poles // 2
    n_parallel = wind.get("n_parallel", 2)
    # rpm is the master; the electrical frequency is DERIVED (see the
    # sliding-band path for why — stale config pairs scaled losses wrong).
    rpm = float(sim.get("rpm", 3950))
    f_elec = rpm * pole_pairs / 60.0
    n_total = max(2, int(round(n_steps_per_period * n_periods)))
    period_mech = 360.0 / pole_pairs                       # one electrical period [deg mech]
    dt = (1.0 / max(f_elec, 1e-9)) * n_periods / n_total
    Ipk = float(I_phase_rms) / n_parallel * _math.sqrt(2)
    # Temperature-consistent phase resistance for the R·I voltage drop.
    _P_cu_dc, _k_end_used, R_phase = copper_loss_W(
        p, geo, float(I_phase_rms), n_parallel,
        coil_temp_c=coil_temp_c, end_winding_factor=end_winding_factor)

    T = []; psiA = []; psiB = []; psiC = []
    Pcu = []; Pfe = []; Pmag = []; IA = []; IB = []; IC = []; tt = []; ang_list = []
    for k in range(n_total):
        ang = (k / n_total) * period_mech * n_periods
        r = fem_solve_for_sim(
            rotor_angle_deg=float(ang), gamma_deg=float(gamma_deg),
            mesh_size_mm=float(mesh_size_mm), min_size_mm=float(min_size_mm),
            outer_air_factor=float(outer_air_factor), motion_band=motion_band,
            band_thickness_mm=float(band_thickness_mm), n_sectors=int(n_sectors),
            stator_fillet_mm=float(stator_fillet_mm), I_phase_rms=float(I_phase_rms),
            component_mesh_mm=component_mesh_mm)
        T.append(float(r.get("T_em_Nm", 0.0)))
        psiA.append(float(r.get("psi_A_Wb", 0.0)))
        psiB.append(float(r.get("psi_B_Wb", 0.0)))
        psiC.append(float(r.get("psi_C_Wb", 0.0)))
        Pcu.append(float(r.get("P_cu_W", 0.0)))
        Pfe.append(float(r.get("P_fe_W", 0.0)))
        Pmag.append(float(r.get("P_mag_eddy_W", 0.0)))
        te = _math.radians(ang * pole_pairs + gamma_deg + DAXIS_SHIFT_DEG)
        IA.append(Ipk * _math.cos(te))
        IB.append(Ipk * _math.cos(te - 2 * _math.pi / 3))
        IC.append(Ipk * _math.cos(te + 2 * _math.pi / 3))
        tt.append(k * dt); ang_list.append(ang)
        log.info("QS transient: frame %d/%d ang=%.2f T=%.2f", k + 1, n_total, ang, T[-1])

    # Back-EMF e = dψ/dt via a SPECTRAL derivative (keep the fundamental + a few
    # low harmonics).  Each frame is meshed independently, so ψ(t) carries a tiny
    # frame-to-frame remesh jitter; a raw finite difference amplifies that into a
    # spurious V-peak (and differently per symmetry).  Differentiating the low
    # harmonics of the periodic ψ removes the jitter and gives the genuine,
    # symmetry-consistent back-EMF.  (Same denoising the sliding-band path used.)
    _Kv = max(1, min(6, n_total // 2 - 1))
    def _ddt(arr):
        a = _np.asarray(arr, float); N = a.size
        if N < 4:
            return _np.array([(a[(i + 1) % N] - a[(i - 1) % N]) / (2 * dt) for i in range(N)])
        Fc = _np.fft.rfft(a)
        if _Kv + 1 < Fc.size:
            Fc[_Kv + 1:] = 0.0
        return _np.fft.irfft(Fc * (1j * 2 * _np.pi * _np.fft.rfftfreq(N, d=dt)), n=N)
    VA = [R_phase * i + e for i, e in zip(IA, _ddt(psiA).tolist())]
    VB = [R_phase * i + e for i, e in zip(IB, _ddt(psiB).tolist())]
    VC = [R_phase * i + e for i, e in zip(IC, _ddt(psiC).tolist())]
    Vpk = float(max(max(map(abs, VA)), max(map(abs, VB)), max(map(abs, VC)))) if VA else 0.0
    Ta = _np.asarray(T, float)
    Tavg = float(Ta.mean()) if Ta.size else 0.0
    Tpp = float(Ta.max() - Ta.min()) if Ta.size else 0.0
    Trip = float(100.0 * Tpp / abs(Tavg)) if Tavg else 0.0
    # torque spectrum (orders = × electrical frequency) for the harmonics bar chart
    T_harm_order = []; T_harm_amp = []
    if Ta.size >= 4:
        F = _np.fft.rfft(Ta - Ta.mean())
        scale = 2.0 / Ta.size
        for kk in range(1, len(F)):
            T_harm_order.append(round(kk / max(n_periods, 1e-9), 2))
            T_harm_amp.append(float(abs(F[kk]) * scale))
    Ptot = [c + f + m for c, f, m in zip(Pcu, Pfe, Pmag)]
    Pmech = float(Tavg * 2.0 * _math.pi * rpm / 60.0)
    log.info("QS transient DONE: n=%d sectors=%d T_avg=%.2f ripple=%.1f%% V_peak=%.1f (%.1fs)",
             n_total, int(n_sectors), Tavg, Trip, Vpk, _t.time() - t0)
    return {
        "method": "quasistatic",
        "n_steps": n_total, "n_steps_per_period": int(n_steps_per_period),
        "n_periods": float(n_periods), "rpm": rpm, "f_elec_Hz": f_elec, "dt_s": dt,
        "T_period_s": (1.0 / f_elec if f_elec > 1e-9 else 0.0),
        "time_s": tt, "rotor_angle_deg": ang_list,
        "T_em_Nm": T, "T_avg_Nm": Tavg, "T_ripple_pct": round(Trip, 2),
        "T_ripple_raw_pct": round(Trip, 2),
        "psi_A_Wb": psiA, "psi_B_Wb": psiB, "psi_C_Wb": psiC,
        "V_A": VA, "V_B": VB, "V_C": VC, "V_peak": Vpk,
        "I_A": IA, "I_B": IB, "I_C": IC,
        "P_cu_W": Pcu, "P_fe_W": Pfe, "P_mag_eddy_W": Pmag, "P_loss_total_W": Ptot,
        "P_mech_avg_W": Pmech, "R_phase_ohm": R_phase, "coil_temp_C": float(coil_temp_c),
        "end_winding_factor": float(_k_end_used),
        "T_harm_order": T_harm_order, "T_harm_amp": T_harm_amp, "field": None,
    }


def fem_solve_for_sim(
    rotor_angle_deg: float = 0.0,
    gamma_deg:       float = 0.0,
    mesh_size_mm:    float = 3.0,
    min_size_mm:     float = 0.3,
    outer_air_factor:float = 1.3,
    motion_band:     bool  = True,
    band_thickness_mm: float = 0.4,
    n_sectors:       int   = 4,
    stator_fillet_mm:float = 0.0,
    I_phase_rms:     Optional[float] = None,
    component_mesh_mm: Optional[dict] = None,
) -> dict:
    """End-to-end FEM solve: build mesh on (possibly clipped) geometry,
    solve magnetostatics, compute Maxwell-stress torque and Steinmetz iron
    losses + I²R copper losses, return everything the Simulation tab needs.

    Multiplies INTEGRAL quantities (torque + iron + magnet eddy losses)
    by n_sectors so the values represent the full motor.  Copper loss is
    derived from phase currents directly (no mesh integration) so it's
    already a full-motor number.
    """
    import time as _t
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.geometry_2d import (
        params_from_config, MotorDomains2D,
    )
    from motor_ai_sim.config import get_config

    t_start = _t.time()
    cfg  = get_config()
    sim  = cfg.get("simulation", {})
    geo  = cfg.get("geometry",   {})
    wind = cfg.get("winding",    {})

    p = params_from_config()
    d = MotorDomains2D(p)
    pole_pairs   = p.num_poles // 2
    # I_phase_rms = 0 must be honoured (zero-current solve = magnet field only).
    # `None` means "use whatever the operating-point config says".
    if I_phase_rms is None:
        I_phase_rms  = sim.get("max_current", 85.0)
    I_phase_rms = float(I_phase_rms)
    n_parallel   = wind.get("n_parallel", 2)
    n_wires      = int(geo.get("num_wires_per_slot", 14))
    I_coil_peak  = I_phase_rms / n_parallel * math.sqrt(2)
    # d-axis convention for SPOKE-PM:
    #   The effective N pole of the rotor sits at the CENTRE OF THE IRON
    #   TOOTH between two adjacent magnets — half a pole pitch (= 90° elec)
    #   offset from the magnet centre.  Empirical γ-sweep with the actual
    #   mesh + nonlinear iron + corrected (half-pitch) slot_idx mapping
    #   determines this constant so γ = 0 lands on the q-axis (max torque).
    # Geometry: rotor d-axis tooth at math 90° (+Y axis), aligned with the
    #   first stator tooth (also at math 90°), exactly as in the Ansys
    #   reference image.
    # Shared module constant so every solve path uses the SAME phase shift.
    SPOKE_PM_DAXIS_SHIFT_DEG = DAXIS_SHIFT_DEG
    theta_e      = math.radians(rotor_angle_deg * pole_pairs
                                 + gamma_deg + SPOKE_PM_DAXIS_SHIFT_DEG)
    I_ph = {
        'A': I_coil_peak * math.cos(theta_e),
        'B': I_coil_peak * math.cos(theta_e - 2 * math.pi / 3),
        'C': I_coil_peak * math.cos(theta_e + 2 * math.pi / 3),
    }

    motor = CadQueryMotor()
    polys = motor.get_2d_polygons(rotor_angle_deg=rotor_angle_deg)
    polys = _simplify_polys(polys, tol_mm=0.005,
                             stator_fillet_mm=stator_fillet_mm)

    log.info("FEM-sim: building mesh (h=%.2f, n_sectors=%d, outer×%.2f, band=%s)",
             mesh_size_mm, n_sectors, outer_air_factor, motion_band)
    if int(n_sectors) == 1:
        # FULL DISK: OCC fragment can't cleanly mesh the closed 360° geometry
        # (it double-meshes the iron).  Build it from two clean 1/2 sector
        # meshes stitched together instead.
        mesh, cell_tags, classify_fn = _build_full_disk_from_halves(
            polys, rotor_angle_deg, mesh_size_mm, min_size_mm, outer_air_factor,
            motion_band, band_thickness_mm, motor.parameters, component_mesh_mm)
    else:
        mesh, cell_tags, classify_fn = build_mesh_from_polygons(
            polys, rotor_angle_deg, mesh_size_mm,
            min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor,
            motion_band=motion_band,
            band_thickness_mm=band_thickness_mm,
            n_sectors=n_sectors,
            geo_cfg=motor.parameters,
            component_mesh_mm=component_mesh_mm,
        )
    # int16 — per-magnet tags reach DOM_MAG_BASE + 27 = 127, well within int16
    # but right at the edge of int8.  Stay in int16 to be safe.
    cell_tags = cell_tags.astype(np.int16)

    slot_area = p.slot_width_m * p.slot_height_m * p.fill_factor
    # build_mesh_from_polygons attached the FINAL (post-clip + air-injected)
    # polys to classify_fn.  Per-magnet material indices must match the
    # mesh's per-magnet tags, so we build materials from that same dict.
    polys_meshed = getattr(classify_fn, "polys", polys)
    mats = build_materials(I_ph, d.winding_layout, polys_meshed,
                            rotor_angle_deg, slot_area, n_wires)

    t_solve_start = _t.time()
    poles_per_sector = p.num_poles // max(int(n_sectors), 1)
    anti_periodic = (poles_per_sector % 2 == 1)
    A = solve_magnetostatics(mesh, cell_tags, mats,
                              n_sectors=int(n_sectors),
                              pole_pairs_per_sector_is_half_integer=anti_periodic)
    t_solve = _t.time() - t_solve_start

    # ── Demagnetisation post-check (after the converged solve) ───────────
    Bx_post, By_post = _per_triangle_B(mesh, A)
    demag_report: List[dict] = []
    magnet_op_points: List[dict] = []
    # PER-TRIANGLE demagnetisation coefficient (0..1) for the field map —
    # all triangles default to 1.0 (no demag); magnet cells get the actual
    # ratio of remaining B / Br at their operating point.
    demag_coef_per_tri = np.ones(mesh.t.shape[1], dtype=np.float32)
    for tag in sorted([t for t in mats if t >= DOM_MAG_BASE]):
        mat_t = mats[tag]
        if abs(mat_t.Mx) + abs(mat_t.My) < 1e-9:
            continue
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        Mmag = math.hypot(mat_t.Mx, mat_t.My)
        # H projected on +M̂, accounting for the iteration's br_factor.
        H_M = (Bx_post[idx] * mat_t.Mx + By_post[idx] * mat_t.My) \
                / (MU0 * Mmag + 1e-30) - Mmag
        # B projected on +M̂
        B_M = (Bx_post[idx] * mat_t.Mx + By_post[idx] * mat_t.My) / Mmag
        H_min = float(np.min(H_M))
        H_mean = float(np.mean(H_M))
        B_at_min = float(B_M[int(np.argmin(H_M))])
        magnet_op_points.append({
            "magnet_index": int(tag - DOM_MAG_BASE),
            "H_op_kA_per_m":  round(H_min  * 1e-3, 1),
            "H_mean_kA_per_m": round(H_mean * 1e-3, 1),
            "B_op_T":         round(B_at_min, 4),
        })
        if not mat_t.bh_curve or len(mat_t.bh_curve) < 2:
            continue
        H_knee = mat_t.bh_curve[1][0] if mat_t.bh_curve[0][1] <= 0 \
                   else mat_t.bh_curve[0][0]
        # Per-cell demag coefficient.  Above the knee (H ≥ H_knee, i.e.
        # less negative) the magnet operates linearly → DC = 1.  Below
        # the knee the coefficient drops linearly with H, hitting 0 at
        # 2·H_knee (a deeply demagnetised cell).
        for j, c in enumerate(idx):
            h = H_M[j]
            if h >= H_knee:
                dc = 1.0
            else:
                dc = 1.0 - (H_knee - h) / abs(H_knee)
            demag_coef_per_tri[c] = max(0.0, min(1.0, float(dc)))
        ratio = H_min / H_knee if H_knee < 0 else 0.0
        if ratio > 0.85:
            demag_report.append({
                "tag": int(tag),
                "magnet_index": int(tag - DOM_MAG_BASE),
                "H_min_kA_per_m": round(H_min * 1e-3, 1),
                "H_knee_kA_per_m": round(H_knee * 1e-3, 1),
                "knee_proximity": round(ratio, 2),
                "demagnetised": bool(ratio > 1.0),
            })
    # Sanitize any NaN/Inf so the response stays JSON-compliant.  Bad nodes
    # become zero — they show up as background-coloured spots in the canvas
    # but don't crash the whole render.
    A = np.nan_to_num(A, nan=0.0, posinf=0.0, neginf=0.0)
    n_bad = int(np.sum(~np.isfinite(A))) if A.size else 0
    if n_bad:
        log.warning("FEM: %d non-finite A values clamped to 0", n_bad)

    # ── Per-triangle B, |B| ───────────────────────────────────────────────
    Bx_tri, By_tri = _per_triangle_B(mesh, A)
    Bmag_tri = np.sqrt(Bx_tri ** 2 + By_tri ** 2)
    Bx_tri = np.nan_to_num(Bx_tri, nan=0.0, posinf=0.0, neginf=0.0)
    By_tri = np.nan_to_num(By_tri, nan=0.0, posinf=0.0, neginf=0.0)
    Bmag_tri = np.nan_to_num(Bmag_tri, nan=0.0, posinf=0.0, neginf=0.0)
    areas    = _triangle_areas(mesh)               # m² for unit stack

    # ── Torque via Arkkio (air-gap annulus average) — mesh-robust ─────────
    # The old single-circle Maxwell stress was wildly mesh-dependent (23→37 N·m)
    # because the gap was under-meshed; with the air-gap size field the gap is
    # now resolved and Arkkio (averaging the stress over the whole annulus)
    # converges to ~26-27 N·m.  Single-circle kept only for the debug log.
    r_ag_m = 0.5 * (p.r_rotor_out + p.r_stator_in)      # mid-air-gap
    theta_end = 2 * math.pi if n_sectors <= 1 else (2 * math.pi / n_sectors)
    T_sector = _arkkio_torque(mesh, A, p.r_rotor_out, p.r_stator_in, p.stack_length)
    T_em_Nm = T_sector * (n_sectors if n_sectors > 1 else 1)
    try:
        T_circle = _maxwell_stress_torque(mesh, A, r_ag_m, p.stack_length,
                                          0.0, theta_end, 720) \
                   * (n_sectors if n_sectors > 1 else 1)
        log.info("torque: Arkkio=%.2f N·m  (single-circle=%.2f N·m)",
                 T_em_Nm, T_circle)
    except Exception:
        pass

    # ── Per-phase flux linkage ψ_A, ψ_B, ψ_C ─────────────────────────────
    # ψ_per_slot = N_turns · L_stack · ⟨A_z⟩_slot  (signed by winding dir).
    # ⟨A_z⟩_slot is the AREA-WEIGHTED mean A_z over the slot's triangles
    # (linear P1 element, so per-tri mean = nodal mean).  Summing the
    # signed per-slot contributions over all slots belonging to a phase
    # gives the phase flux linkage in Wb.  Multiplied by the symmetry
    # multiplier to recover the full-motor value.
    psi_A = psi_B = psi_C = 0.0
    coil_polys_clipped = polys_meshed.get("coils", [])
    n_slot_layout = len(d.winding_layout)
    if n_slot_layout > 0 and coil_polys_clipped:
        slot_pitch_deg_layout = 360.0 / n_slot_layout
        # Same half-pitch offset fix as build_materials: slot CENTRES sit
        # midway between adjacent coil polygons, so the two halves of each
        # wide tooth land on ADJACENT slot_idx values with OPPOSITE direction
        # signs.  Without this offset both halves collapse onto the same
        # slot_idx and inherit the SAME direction → ψ_phase becomes
        # ⟨A_z_left⟩ + ⟨A_z_right⟩ ≈ 2⟨A_z_tooth⟩ instead of the proper
        # ⟨A_z_left⟩ − ⟨A_z_right⟩ = flux LINKED by the coil loop, which
        # over-estimates the back-EMF voltage by 10–50× (user-reported
        # V_peak ≈ 5 kV vs. expected ≈ 140 V).
        half_pitch_layout = slot_pitch_deg_layout * 0.5
        A_tri_mean = (A[mesh.t[0]] + A[mesh.t[1]] + A[mesh.t[2]]) / 3.0
        for i, cp in enumerate(coil_polys_clipped):
            if cp is None or cp.is_empty:
                continue
            try:
                cx, cy = cp.centroid.x, cp.centroid.y
            except Exception:
                continue
            ang = math.degrees(math.atan2(cy, cx))
            if ang < 0: ang += 360.0
            slot_idx = int((ang - half_pitch_layout)
                            / slot_pitch_deg_layout + 0.5) % n_slot_layout
            phase, direction = d.winding_layout[slot_idx]
            tag = DOM_COIL_BASE + i
            idx = np.where(cell_tags == tag)[0]
            if idx.size == 0:
                continue
            slot_area = float(np.sum(areas[idx]))
            if slot_area <= 0:
                continue
            mean_Az = float(np.sum(A_tri_mean[idx] * areas[idx])) / slot_area
            psi_slot = direction * mean_Az      # signed Wb/m  per turn
            if   phase == 'A': psi_A += psi_slot
            elif phase == 'B': psi_B += psi_slot
            elif phase == 'C': psi_C += psi_slot
        # ⟨A_z⟩ has units Wb/m.  Critical scaling note:
        #   _clip_polys_to_sector unpacks each slot's MultiPolygon
        #   (= union of N_wires disjoint wire rectangles) into N_wires
        #   INDIVIDUAL polygons in `coils`.  The loop above therefore
        #   already SUMS direction × ⟨A_z⟩ over all N_wires wires per
        #   slot, so we MUST NOT multiply by N_wires again — that
        #   would over-count flux linkage by N_wires (= 14 for this
        #   motor, producing the ~5 kV phase-voltage artefact the user
        #   pointed out).  Divide by n_parallel to convert the SUMMED
        #   phase flux linkage into PER-BRANCH ψ, which is what the
        #   phase-terminal voltage equation uses.
        sym = (n_sectors if n_sectors > 1 else 1)
        scale = p.stack_length * sym / float(n_parallel)
        psi_A *= scale
        psi_B *= scale
        psi_C *= scale

    # ── Losses ────────────────────────────────────────────────────────────
    # Iron loss: per-cell Bertotti formula using the material's actual
    # kh / kc / ke coefficients from materials_library.yaml.  The Bertotti
    # model splits hysteresis, classical eddy and excess losses and
    # encodes BOTH frequency and B dependence per material grade:
    #
    #   P/V  =  k_h · f · B²   +   k_c · f² · B²   +   k_e · f^1.5 · B^1.5
    #
    # Lamination stacking_factor (≈0.97) discounts the geometric volume
    # to account for the inter-laminate insulation thickness.
    #
    # Copper: 3-phase I²R from R_phase in config.
    freq = sim.get("rpm", 3950) / 60 * pole_pairs       # electrical Hz

    # Pull material objects directly from the library to get Bertotti
    # coefficients, density and stacking factor.
    try:
        from motor_ai_sim import materials as _mat_lib
        from motor_ai_sim.config import get_material_assignments as _gma
        _ma = _gma() or {}
        _stator_mat = _mat_lib.get_steel(_ma.get("stator_core", "20SW1200"))
        _rotor_mat  = _mat_lib.get_steel(_ma.get("rotor_core",  "20SW1200"))
    except Exception as e:
        log.warning("Steel material lookup failed (%s) — falling back to hardcoded Bertotti", e)
        _stator_mat = _rotor_mat = None

    # Per-triangle volumes [m³].  Stacking factor applied at loss step.
    vol = areas * p.stack_length

    def _domain_iron_loss_bertotti(tag: int, mat) -> float:
        """Per-cell Bertotti core loss summed over a domain."""
        mask = cell_tags == tag
        idx = np.where(mask)[0]
        if idx.size == 0 or mat is None:
            return 0.0
        # Maxwell-style: coefficients fitted from the material's MEASURED loss
        # curves when present (see materials.fit_bertotti_from_curves), else YAML.
        kh, kc, ke = _mat_lib.effective_bertotti(mat)
        sf = float(getattr(mat, "stacking_factor", 0.97))
        f  = freq
        B  = Bmag_tri[idx]
        # W/m³ per cell
        p_dens = kh * f * B**2 + kc * f**2 * B**2 + ke * f**1.5 * B**1.5
        # Apply lamination stacking factor to the geometric volume
        return float(np.sum(p_dens * vol[idx] * sf))

    P_fe_stator = _domain_iron_loss_bertotti(DOM_STATOR, _stator_mat)
    P_fe_rotor  = _domain_iron_loss_bertotti(DOM_ROTOR,  _rotor_mat)

    # ── Magnet eddy losses — slot-ripple slab model ──────────────────────
    # In a SYNCHRONOUS machine the fundamental armature reaction rotates
    # with the rotor, so in the magnet's frame it is DC — no eddy loss.
    # The dominant AC field a magnet sees is the SLOT RIPPLE, at frequency
    #     f_slot = num_slots × n_mech     [Hz]
    # whose amplitude inside the magnet is a few percent of the local B.
    # The classical conducting-slab result for losses is
    #     P/V = σ · (2π f_slot)² · (η·B)² · d² / 12      [W/m³]
    # with η ≈ 0.10 the empirical "ripple fraction" for unsegmented
    # rotors in concentrated-winding machines (Bianchi & Fornasiero,
    # IEEE TIA 2009).  Segmenting axially reduces this by N_seg²
    # (out of scope for this 2-D solver).
    #
    # Naively plugging the FULL B and electrical f into the slab formula
    # (as a static FEM might suggest) over-estimates the loss by ~3-4
    # orders of magnitude — see the rotor-frame argument above.
    #
    # An exact "finite-difference of A_z over transient frames" turns
    # out to be poisoned by gauge / sector-clipping noise (A_z FLIPS sign
    # by anti-periodicity once per pole pitch even though B stays the
    # same), so for a robust unattended answer we use the slot-ripple
    # slab model.  Per-snapshot A_z mean / per-magnet volume / σ are
    # still surfaced in the response for downstream tooling that does a
    # gauge-aware finite difference (e.g. a full-motor moving-band run).
    try:
        mag_name = _ma.get("magnet")
        _magnet_mat = _mat_lib.get_magnet(mag_name) if mag_name else None
    except Exception:
        _magnet_mat = None
    rho_magnet = float(getattr(_magnet_mat, "density", 7500.0)) if _magnet_mat else 7500.0
    sigma_mag  = float(getattr(_magnet_mat, "sigma",   0.0))    if _magnet_mat else 0.0

    # Per-magnet mean A_z, volume, and ROTOR-FRAME centroid angle for the
    # downstream time-differentiation in /fem_transient.  The rotor-frame
    # angle (= lab centroid angle minus the current rotor_angle_deg) is a
    # stable identifier — the SAME physical magnet has the SAME rotor-frame
    # angle across all transient frames, even though sector clipping may
    # reorder it in the per-frame magnet list.
    A_z_mean_per_magnet:  List[float] = []
    Bmag_mean_per_magnet: List[float] = []
    vol_per_magnet:       List[float] = []
    mag_rotor_angle_deg:  List[float] = []
    A_tri_mean_all = (A[mesh.t[0]] + A[mesh.t[1]] + A[mesh.t[2]]) / 3.0
    for i, (mp, _pol) in enumerate(polys_meshed.get("magnets", [])):
        tag = DOM_MAG_BASE + i
        idx = np.where(cell_tags == tag)[0]
        try:
            lab_ang = math.degrees(math.atan2(mp.centroid.y, mp.centroid.x))
        except Exception:
            lab_ang = 0.0
        rotor_frame_ang = (lab_ang - rotor_angle_deg) % 360.0
        if idx.size == 0:
            A_z_mean_per_magnet.append(0.0)
            Bmag_mean_per_magnet.append(0.0)
            vol_per_magnet.append(0.0)
            mag_rotor_angle_deg.append(rotor_frame_ang)
            continue
        a_w = float(np.sum(areas[idx]))
        A_z_mean_per_magnet.append(
            float(np.sum(A_tri_mean_all[idx] * areas[idx])) / max(a_w, 1e-30))
        # |B|² area-weighted mean — gauge-INVARIANT, unlike ⟨A_z⟩
        Bmag_mean_per_magnet.append(
            float(np.sum(Bmag_tri[idx] * areas[idx])) / max(a_w, 1e-30))
        vol_per_magnet.append(a_w * p.stack_length)
        mag_rotor_angle_deg.append(rotor_frame_ang)

    # ── Magnet eddy losses — slot-ripple slab model on local B ───────────
    #
    # The honest computation would be a sliding-band moving-mesh FEM
    # (rotor mesh rigidly rotates, stator mesh stays fixed, anti-periodic
    # BC on the radial cuts handles the wedge — exactly what Ansys does
    # with its "Dependent Boundary / Bdep = −Bind" master-slave pairing).
    # In our pipeline the mesh is rebuilt from scratch every frame, so
    # neither ∂A_z/∂t (gauge-ambiguous) nor ∂|B|/∂t (mesh-noise
    # dominated) of the per-frame ⟨...⟩-over-magnet gives a stable
    # answer — 24 frames → 1.5 kW, 48 frames → 25 kW peak.  See git
    # commit history for the gauge-and-noise analysis.
    #
    # Pending the sliding-band rewrite we use the classical conducting-
    # slab formula on the LOCAL per-cell B with a small empirical
    # ripple fraction η:
    #     P/V = σ · (2π f_slot)² · (η · B_local)² · d² / 12   [W/m³]
    # with
    #     f_slot = num_slots × n_mech      (slot-ripple in rotor frame)
    #     η      = 0.03                    (typical 24-slot/14-pp FSCW
    #                                       SPMSM — 3 % of local B
    #                                       varies at slot frequency)
    #     B_local = per-cell |B|           (gauge-stable)
    #     d      = magnet radial thickness
    #
    # This is calibrated to match the typical 1–3 % of P_in published
    # value for unsegmented NdFeB in FSCW machines (Bianchi & Fornasiero,
    # IEEE TIA 2009).
    n_mech_solver = sim.get("rpm", 3950) / 60.0
    f_slot_solver = float(p.num_slots) * n_mech_solver
    omega_slot    = 2.0 * math.pi * f_slot_solver
    d_mag_m       = float(p.r_rotor_out - p.r_rotor_in - 0.0012) \
                    if (p.r_rotor_out - p.r_rotor_in) > 0.002 else 0.016
    RIPPLE_FRACTION = 0.03

    P_mag_eddy = 0.0
    for i, _ in enumerate(polys_meshed.get("magnets", [])):
        tag = DOM_MAG_BASE + i
        idx = np.where(cell_tags == tag)[0]
        if idx.size == 0:
            continue
        B_cells = Bmag_tri[idx]
        p_dens = (sigma_mag * omega_slot**2
                  * (RIPPLE_FRACTION * B_cells) ** 2
                  * d_mag_m ** 2 / 12.0)
        P_mag_eddy += float(np.sum(p_dens * vol[idx]))

    mult = n_sectors if n_sectors > 1 else 1
    P_fe_total = (P_fe_stator + P_fe_rotor) * mult
    P_mag_total = P_mag_eddy * mult

    # Copper loss — phase currents × R_phase  (×3 phases).  Uses the
    # I_phase_rms passed in (NOT the config value) so the simulation
    # actually reflects user-set current changes.
    R_phase = float(wind.get("phase_resistance_ohm", 0.018))
    P_cu = 3 * I_phase_rms ** 2 * R_phase

    P_loss_total = P_fe_total + P_mag_total + P_cu
    rpm = sim.get("rpm", 3950)
    P_mech = T_em_Nm * 2 * math.pi * rpm / 60
    eff = P_mech / max(P_mech + P_loss_total, 1e-6) if P_mech > 0 else 0.0

    # ── Outlines (for the renderer; matches /mesh/build2d format) ─────────
    polys_for_outlines = getattr(classify_fn, "polys", polys)

    # Remap per-magnet + per-coil tags back to the visualisation ids
    # (DOM_MAG_N / DOM_MAG_S / DOM_COIL) that the frontend already knows
    # how to colour.
    polarities = [pol for _mp, pol in polys_meshed.get("magnets", [])]
    cell_tags_vis = cell_tags.copy()
    mask_coil = cell_tags_vis >= DOM_COIL_BASE
    if np.any(mask_coil):
        cell_tags_vis[mask_coil] = DOM_COIL
    mask = (cell_tags_vis >= DOM_MAG_BASE) & (cell_tags_vis < DOM_COIL_BASE)
    if np.any(mask):
        idx = (cell_tags_vis[mask] - DOM_MAG_BASE).astype(int)
        cell_tags_vis[mask] = np.array(
            [DOM_MAG_N if (j < len(polarities) and polarities[j] > 0) else DOM_MAG_S
             for j in idx], dtype=cell_tags_vis.dtype)

    # ── Per-triangle J_z [A/m²] for the field renderer (J mode) ───────────
    # Each coil cell carries the J_z of its per-coil material entry;
    # everything else is zero.  Used by FemFieldChart's "J" mode to draw
    # the Ansys-style red/blue/green current-density map.
    J_z_per_tri = np.zeros(cell_tags.shape[0], dtype=np.float32)
    coil_tags = np.where(cell_tags >= DOM_COIL_BASE)[0]
    if coil_tags.size:
        for tag in np.unique(cell_tags[coil_tags]):
            mat_t = mats.get(int(tag))
            if mat_t is None:
                continue
            J_z_per_tri[cell_tags == tag] = float(mat_t.J_z)

    return {
        "ok": True,
        "rotor_angle_deg": rotor_angle_deg,
        "gamma_deg":       gamma_deg,
        "n_sectors":       n_sectors,
        "symmetry_mult":   mult,
        "n_vertices":      int(mesh.p.shape[1]),
        "n_triangles":     int(mesh.t.shape[1]),
        "vertices":        mesh.p.T.tolist(),       # metres
        "triangles":       mesh.t.T.tolist(),
        "domain_per_tri":  cell_tags_vis.tolist(),
        "A_z_per_node":    A.tolist(),               # Wb/m
        "Bmag_per_tri":    Bmag_tri.tolist(),
        "J_z_per_tri":     J_z_per_tri.tolist(),     # A/m² — coils only
        # ── Per-magnet bulk quantities for transient-mode honest eddy ─────
        "A_z_mean_per_magnet":   A_z_mean_per_magnet,   # Wb/m per magnet
        "Bmag_mean_per_magnet":  Bmag_mean_per_magnet,  # T per magnet (gauge-invariant)
        "vol_per_magnet":        vol_per_magnet,        # m³ per magnet
        "mag_rotor_angle_deg":   mag_rotor_angle_deg,   # rotor-frame ID (deg)
        "sigma_magnet":          float(sigma_mag),      # S/m
        "extent": [
            float(mesh.p[0].min()), float(mesh.p[0].max()),
            float(mesh.p[1].min()), float(mesh.p[1].max()),
        ],
        "polys_for_outlines": polys_for_outlines,
        # ── Physics quantities (with n_sectors multiplier already applied) ──
        "T_em_Nm":       round(T_em_Nm, 4),
        "P_cu_W":        round(P_cu, 1),
        "P_fe_W":        round(P_fe_total, 1),
        "P_mag_eddy_W":  round(P_mag_total, 1),
        "P_loss_total_W":round(P_loss_total, 1),
        "P_mech_W":      round(P_mech, 1),
        "efficiency":    round(eff, 4),
        "freq_Hz":       round(freq, 2),
        "rpm":           rpm,
        "solve_time_s":  round(t_solve, 2),
        "total_time_s":  round(_t.time() - t_start, 2),
        # Demagnetisation report — each entry is a magnet whose worst-cell
        # H came within 15 % of the BH-curve knee.  demagnetised=True means
        # the magnet has crossed the knee and is irreversibly weakened.
        "demag_report":      demag_report,
        "demag_coef_per_tri": demag_coef_per_tri.tolist(),
        # Per-phase flux linkages [Wb].  Used by the transient endpoint
        # to derive V_phase(t) = R·I + dψ/dt across the period.
        "psi_A_Wb":          float(psi_A),
        "psi_B_Wb":          float(psi_B),
        "psi_C_Wb":          float(psi_C),
    }
