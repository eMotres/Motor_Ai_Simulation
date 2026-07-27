"""Physics primitives on a SOLVED field — torque, B recovery, material curves.

The layer between the mesher and the transient. Every function here is pure:
arrays and material curves in, numbers out. No mesh building, no solver state,
no config — which is exactly what makes them testable on their own, and why they
came out of ``fem_solver_2d`` first.

What lives here:
  * torque      — Maxwell stress on a gap contour, Arkkio on the gap annulus
                  (P1 and P2), and the 6*k band-limit used to separate the
                  physical ripple from the sliding band's numerical hash
  * fields      — per-element B from the nodal potential, element areas,
                  area-weighted nodal smoothing of the demagnetising field
  * materials   — mu_r from a B-H curve (scalar and vectorised), B at a given H,
                  the magnet demag-curve payload the UI plots
  * windings    — end-winding factor from the geometry, copper loss with the
                  temperature-corrected resistivity

Re-exported from ``fem_solver_2d`` so existing imports keep working.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from motor_ai_sim.simulation.sb_domains import DOM_MAG_BASE

# Vacuum permeability and the copper constants the winding helpers need.
MU0 = 4e-7 * math.pi
RHO_CU_20 = 1.724e-8          # copper resistivity at 20 C [ohm*m]
ALPHA_CU = 0.00393            # copper temperature coefficient [1/K]

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
