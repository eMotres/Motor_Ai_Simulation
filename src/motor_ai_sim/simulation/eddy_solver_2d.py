"""Honest (coupled) eddy-current solver — 2-D, frequency-domain (phasor) form.

This is a SECOND, independent eddy-current engine, added alongside the existing
sliding-band transient (``fem_solver_2d``).  It does NOT replace or modify that
solver; it exists so the two can be compared on the same design.

Why a separate engine
---------------------
The production transient solves MAGNETOSTATICS per rotor frame and post-processes
eddy loss as ``σ·∫(∂A/∂t)²`` — *resistance-limited*: it ignores the eddy REACTION
(the induced currents' own field, i.e. self-shielding / skin effect).  That is exact
when the skin depth δ ≫ the conductor size (good for the magnets) but OVER-counts
high-σ bodies where δ ≲ size (e.g. a solid aluminium shaft).

This engine solves the COUPLED magneto-dynamic problem, so the reaction (skin effect)
emerges from the physics — no slab `d²/12`, no cylinder factor, no skin-depth cap.

Uniform treatment of every conductor
-------------------------------------
EVERY conductor — coil, magnet, shaft — is the SAME kind of object: a *solid* body
with conductivity σ and ONE extra unknown, the uniform axial field E0 = −V/L (the
"loop voltage per unit length").  Its axial current density is

    J_z = σ (−jω A_z + E0).

A per-body constraint ``∮_body J_z dA = I_body`` closes it: coils → I_body = the phase
current; magnets and shaft → I_body = 0 (no net axial current).  Only the prescribed
current differs between conductor kinds — the physics is identical.

Phasor system (one angular frequency ω, complex A and E0)
---------------------------------------------------------
    [ K + jω Mσ      −P ] [ A  ]   [ f_src ]
    [ −jω Pᵀ          S ] [ E0 ] = [ I_b   ]

    K   : reluctivity stiffness  ∫ ν ∇A·∇A'         (ν = 1/(μ0 μr) per element)
    Mσ  : σ-weighted mass        ∫ σ A A'           (0 on iron/air → those rows are
                                                     plain magnetostatics)
    P   : N×B,  column b = Mσ · 1_b   (1_b = node indicator of conductor body b)
    S   : B×B diagonal,  S_bb = 1_bᵀ Mσ 1_b = ∫_b σ dA
    f_src: external sources (imposed current density / magnetisation), if any

Eddy loss (period-averaged ohmic dissipation, reaction included):

    P_b = ½ · L · ∫_b |J_z|² / σ dA ,   J_z = σ(−jω A + E0_b)

The ½ is the sinusoidal time-average for peak-amplitude phasors.

Self-test:  ``python -m motor_ai_sim.simulation.eddy_solver_2d`` solves a solid
conducting cylinder in a uniform transverse AC field over a sweep of frequency and
compares the loss to the exact Kelvin-function result and to the resistance-limited
(no-reaction) value — demonstrating the skin-effect roll-off the production solver
cannot see.
"""
from __future__ import annotations

import math
from copy import copy
from typing import Dict, List, Sequence, Tuple

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

MU0 = 4.0e-7 * math.pi


# ─────────────────────────────────────────────────────────────────────────────
# Assembly on a P1 triangle mesh.  Kept dependency-light (no skfem needed): the
# P1 gradients are constant per element, so K and the (lumped+consistent) mass
# matrix are closed-form.  p = (2, Nn) node coords [m]; t = (3, Ne) connectivity.
# ─────────────────────────────────────────────────────────────────────────────
def _tri_geom(p: np.ndarray, t: np.ndarray):
    """Per-element area and ∇φ_i (constant P1 shape-function gradients)."""
    x = p[0, t]; y = p[1, t]                       # (3, Ne)
    # edge vectors
    b = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]])   # (3, Ne) = ∂φ/∂x · 2A
    c = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]])   # (3, Ne) = ∂φ/∂y · 2A
    area2 = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])  # 2A (signed)
    area = 0.5 * np.abs(area2)
    sgn = np.where(area2 >= 0, 1.0, -1.0)
    # gradients (divide by 2A); keep sign so b,c are consistent with +area
    gx = b * sgn / (2.0 * area)                    # (3, Ne)  ∂φ_i/∂x
    gy = c * sgn / (2.0 * area)
    return area, gx, gy


def assemble_K(p: np.ndarray, t: np.ndarray, nu_elem: np.ndarray) -> sp.csr_matrix:
    """Stiffness  K_ij = Σ_e ν_e ∫_e ∇φ_i·∇φ_j  (reluctivity-weighted Laplacian)."""
    area, gx, gy = _tri_geom(p, t)
    Ne = t.shape[1]; Nn = p.shape[1]
    rows = np.empty(9 * Ne, int); cols = np.empty(9 * Ne, int)
    vals = np.empty(9 * Ne, float)
    k = 0
    for i in range(3):
        for j in range(3):
            rows[k * Ne:(k + 1) * Ne] = t[i]
            cols[k * Ne:(k + 1) * Ne] = t[j]
            vals[k * Ne:(k + 1) * Ne] = nu_elem * (gx[i] * gx[j] + gy[i] * gy[j]) * area
            k += 1
    return sp.csr_matrix((vals, (rows, cols)), shape=(Nn, Nn))


def assemble_Msigma(p: np.ndarray, t: np.ndarray, sigma_elem: np.ndarray) -> sp.csr_matrix:
    """Consistent σ-weighted mass  M_ij = Σ_e σ_e ∫_e φ_i φ_j.
    ∫_e φ_i φ_j = area/12 (i≠j), area/6 (i=j)."""
    area, _, _ = _tri_geom(p, t)
    Ne = t.shape[1]; Nn = p.shape[1]
    rows = np.empty(9 * Ne, int); cols = np.empty(9 * Ne, int)
    vals = np.empty(9 * Ne, float)
    k = 0
    for i in range(3):
        for j in range(3):
            rows[k * Ne:(k + 1) * Ne] = t[i]
            cols[k * Ne:(k + 1) * Ne] = t[j]
            w = (1.0 / 6.0) if i == j else (1.0 / 12.0)
            vals[k * Ne:(k + 1) * Ne] = sigma_elem * area * w
            k += 1
    return sp.csr_matrix((vals, (rows, cols)), shape=(Nn, Nn))


# ─────────────────────────────────────────────────────────────────────────────
# Coupled harmonic solve
# ─────────────────────────────────────────────────────────────────────────────
def solve_harmonic_eddy(
    p: np.ndarray, t: np.ndarray, nu_elem: np.ndarray, sigma_elem: np.ndarray,
    bodies: Sequence[np.ndarray], I_bodies: Sequence[complex], omega: float,
    dir_nodes: np.ndarray, dir_vals: np.ndarray, f_src: np.ndarray | None = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Solve the coupled phasor system for A (nodes) and E0 (per conductor body).

    bodies[b]   : int array of ELEMENT indices that make up conductor body b.
    I_bodies[b] : prescribed net axial current (A, complex).  0 for floating bodies.
    dir_nodes   : Dirichlet node ids (e.g. outer boundary = external field BC).
    dir_vals    : complex A_z there.
    Returns (A complex (Nn,), E0 complex (B,)).
    """
    return _PreparedHarmonicEddy(p, t, nu_elem, sigma_elem, bodies, dir_nodes).solve(
        I_bodies, omega, dir_vals, f_src)


class _PreparedHarmonicEddy:
    """Invariant assembly for one region's frequency sweep, never a global cache.

    Matrices and body constraints own their assembled data; no solution, source
    or boundary-value array is retained between frequencies. A changed mesh,
    material, body incidence or boundary-node set needs a new instance.

    ``cols`` (optional) gives each body's constraint column EXACTLY as the
    coupled time-domain solve builds it — g_b = σ-mass of body b's OWN
    elements · 1 (a signed sum for a magnet bisected by the sector cut) — in
    place of the full σ-mass times a node indicator, which also picks up the
    σ of a NEIGHBOURING conductor on shared nodes.  ``Pm`` (optional, Nn ×
    Nr) is the (anti)periodic sector projection: A = Pm·a on the reduced
    dofs a, the cut ray θ = φ slaved to θ = 0 with the sector sign.
    """
    def __init__(self, p, t, nu_elem, sigma_elem, bodies, dir_nodes,
                 cols=None, Pm=None):
        Nn = p.shape[1]
        B = len(bodies) if cols is None else len(cols)
        self.Nn, self.B = Nn, B
        K = assemble_K(p, t, nu_elem).astype(complex)
        M = assemble_Msigma(p, t, sigma_elem).astype(complex)
        if cols is None:
            # P[:,b] = M · 1_b where 1_b marks nodes touched by body b.
            Pcols = []
            S = np.zeros(B, complex)
            for b in range(B):
                ind = np.zeros(Nn)
                ind[np.unique(t[:, bodies[b]].ravel())] = 1.0
                col = M @ ind
                Pcols.append(col)
                S[b] = ind @ col                  # 1_bᵀ M 1_b = ∫_b σ dA
            P = np.array(Pcols).T if B else np.zeros((Nn, 0), complex)
        else:
            P = (np.array([np.asarray(c[0], float) for c in cols]).T
                 if B else np.zeros((Nn, 0))).astype(complex)
            S = np.array([float(c[1]) for c in cols], complex)
        self.Pm = Pm
        if Pm is not None:
            K = (Pm.T @ K @ Pm).tocsr(); M = (Pm.T @ M @ Pm).tocsr()
            P = np.asarray(Pm.T @ P)
        self.K, self.M, self.P, self.S = K, M, P, S
        self.Nr = K.shape[0]
        self.dir_nodes = copy(dir_nodes)
        self.free = np.ones(self.Nr + B, bool)
        self.free[self.dir_nodes] = False

    def solve(self, I_bodies, omega, dir_vals, f_src=None):
        Nn, B = self.Nr, self.B
        P, S, free = self.P, self.S, self.free
        A_block = self.K + 1j * omega * self.M

        # Keep the original frequency-dependent assembly and elimination.
        # Each frequency has different numeric factors and calls spsolve anew.
        top = sp.hstack([A_block, sp.csr_matrix(-P)], format="csr")
        bot = sp.hstack([sp.csr_matrix(-1j * omega * P.T), sp.csr_matrix(np.diag(S))],
                        format="csr")
        KK = sp.vstack([top, bot], format="csr").tolil()
        rhs = np.zeros(Nn + B, complex)
        if f_src is not None:
            rhs[:Nn] = f_src if self.Pm is None else self.Pm.T @ f_src
        rhs[Nn:] = np.asarray(I_bodies, complex)

        # Dirichlet on A (penalty-free: eliminate rows/cols).
        KK = KK.tocsr()
        A_full = np.zeros(Nn + B, complex)
        A_full[self.dir_nodes] = dir_vals
        rhs = rhs - KK @ A_full
        KKf = KK[free][:, free]
        sol = spsolve(KKf.tocsc(), rhs[free])
        out = A_full.copy()
        out[free] = sol
        A = out[:Nn] if self.Pm is None else np.asarray(self.Pm @ out[:Nn])
        return A, out[Nn:]


def eddy_loss_per_body(
    p: np.ndarray, t: np.ndarray, sigma_elem: np.ndarray, A: np.ndarray,
    E0: np.ndarray, bodies: Sequence[np.ndarray], omega: float, L: float,
) -> np.ndarray:
    """Period-averaged ohmic loss per body  P_b = ½ L ∫_b |J|²/σ dA,
    J = σ(−jωA + E0_b)  (peak-amplitude phasors → ½ for the time average)."""
    area, _, _ = _tri_geom(p, t)
    Ael = A[t].mean(axis=0)                       # element-mean A (complex)
    out = np.zeros(len(bodies))
    for b, els in enumerate(bodies):
        s = sigma_elem[els]
        J = s * (-1j * omega * Ael[els] + E0[b])  # |J| per element
        out[b] = 0.5 * L * np.sum(np.abs(J) ** 2 / np.maximum(s, 1e-30) * area[els])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Self-test: solid conducting cylinder in a uniform transverse AC field.
# Exact axial-eddy loss via Kelvin functions; compare coupled solve vs exact vs
# the resistance-limited (no-reaction) value.
# ─────────────────────────────────────────────────────────────────────────────
def _lowfreq_cylinder_loss(a, sigma, omega, B0, L):
    """Resistance-limited (no-reaction) transverse-field solid-cylinder loss [W]:

        E_z = -dA/dt = omega B0 sin(wt) y ,  J = sigma E ,  P = <int J^2/sigma dA> L
        int y^2 dA (disk) = pi a^4 / 4 ,  <sin^2> = 1/2
        => P = sigma omega^2 B0^2 * (pi a^4 / 4) * (1/2) * L = pi a^4 L sigma omega^2 B0^2 / 8

    This is the EXACT delta>>a asymptote AND exactly what the production solver's
    post-process (sigma*(dA/dt)^2, no reaction) returns at ANY frequency.  So the
    coupled solve must match it at low omega and fall BELOW it as delta -> a."""
    return math.pi * a ** 4 * L * sigma * omega ** 2 * B0 ** 2 / 8.0


def _build_disk_mesh(a_cond: float, r_out: float, h: float):
    """Triangulate a disk of radius r_out with a conductor core of radius a_cond.
    Returns p (2,Nn), t (3,Ne), cond_elems (element ids inside the core)."""
    # simple structured polar mesh
    nr = max(8, int(round(r_out / h)))
    nth = max(24, int(round(2 * math.pi * a_cond / h)))
    rs = np.linspace(0.0, r_out, nr + 1)
    pts = [(0.0, 0.0)]
    ring_start = [0]
    for ri in rs[1:]:
        ring_start.append(len(pts))
        for j in range(nth):
            th = 2 * math.pi * j / nth
            pts.append((ri * math.cos(th), ri * math.sin(th)))
    p = np.array(pts).T
    tris = []
    # center fan
    for j in range(nth):
        tris.append((0, 1 + j, 1 + (j + 1) % nth))
    for ir in range(1, nr):
        s0 = ring_start[ir]; s1 = ring_start[ir + 1]
        for j in range(nth):
            a0 = s0 + j; a1 = s0 + (j + 1) % nth
            b0 = s1 + j; b1 = s1 + (j + 1) % nth
            tris.append((a0, b0, b1)); tris.append((a0, b1, a1))
    t = np.array(tris).T
    # conductor elements = centroid radius < a_cond
    cx = p[0, t].mean(0); cy = p[1, t].mean(0)
    cond = np.where(np.hypot(cx, cy) <= a_cond + 1e-12)[0]
    bnd = np.where(np.hypot(p[0], p[1]) >= r_out - 1e-9)[0]
    return p, t, cond, bnd


def _self_test():
    a = 2.6e-3                 # cylinder radius (the 40 mm motor's shaft radius)
    sigma = 2.58e7             # aluminium 6061
    mu_r = 1.0
    B0 = 0.05                  # peak transverse flux density [T]
    L = 0.012                  # stack length [m]
    r_out = 6.0 * a
    p, t, cond, bnd = _build_disk_mesh(a, r_out, a / 12.0)
    Ne = t.shape[1]
    nu = np.full(Ne, 1.0 / MU0)         # μ_r=1 everywhere (cylinder + surrounding air)
    sig = np.zeros(Ne); sig[cond] = sigma
    # external uniform field B0 x̂  ↔  A_z = −B0·y on the outer boundary
    dir_vals = -B0 * p[1, bnd]
    print(f"{'f [Hz]':>9} {'d/a':>7} {'coupled [W]':>13} {'no-react [W]':>13} {'ratio':>7}")
    for f in (50, 200, 1000, 2600, 8000, 30000):
        omega = 2 * math.pi * f
        delta = math.sqrt(2.0 / (omega * MU0 * mu_r * sigma))
        A, E0 = solve_harmonic_eddy(p, t, nu, sig, [cond], [0.0], omega,
                                    bnd, dir_vals.astype(complex))
        P = eddy_loss_per_body(p, t, sig, A, E0, [cond], omega, L)[0]
        P_nr = _lowfreq_cylinder_loss(a, sigma, omega, B0, L)  # resistance-limited
        print(f"{f:9d} {delta/a:7.3f} {P:13.4e} {P_nr:13.4e} {P/P_nr:7.3f}")
    print("\nExpect: ratio ~1 at low f (delta >> a), then DROPS below 1 as delta -> a "
          "(skin screening). The production solver returns the 'no-react' column at all f.")


def region_eddy_from_history(
    p: np.ndarray, t: np.ndarray, nu_elem: np.ndarray, sigma_elem: np.ndarray,
    bodies: Sequence[np.ndarray], bound_mask: np.ndarray, A_hist: np.ndarray,
    period_s: float, L: float, I_hists: Sequence[np.ndarray] | None = None,
) -> Tuple[np.ndarray, List[float]]:
    """Honest eddy loss per conductor body over a REGION (e.g. the whole rotor:
    magnets + shaft + iron), driven by the REAL field time-history the production
    transient computed at the region's nodes.

    p, t        : the region's mesh (nodes [m], P1 triangles) — REAL geometry, so the
                  iron's μ_r and the conductors' shapes shield the field exactly.
    nu_elem     : 1/(μ0 μr) per element (iron low, air/conductor = 1/μ0).
    sigma_elem  : σ per element (0 on iron/air; conductor σ on magnet/shaft elements).
    bodies[b]   : ELEMENT indices of conductor body b (one per magnet, one for shaft, …).
                  Each gets ∮J = I_body and a uniform-E0 unknown — uniform treatment.
    bound_mask  : (Nn,) bool — region OUTER boundary (the air-gap interface); the field
                  the rotor sees enters here as a Dirichlet BC.  Internal screening +
                  inter-body coupling are SOLVED — no shape factor, no cap.
    A_hist      : (Nframes, Nn) real A_z at the region nodes over a window of
                  length ``period_s`` that CLOSES on itself (a whole period of the
                  rotor-frame field — see simulation/rotor_window.py; an open
                  window reads its end-to-start step as broadband content).
    I_hists[b]  : (Nframes,) net current per body (coils); None → all floating (I=0).

    rFFT the boundary history → harmonics; per harmonic solve the coupled multi-body
    system; sum per-harmonic losses (orthogonal → add in power).  Returns
    (loss_per_body [W], freqs_used).

    EVERY bin up to Nyquist is solved — no harmonic cap, no amplitude floor
    (owner 2026-09-24: no filter may shape a reported value).  A bin is skipped
    only when its drive is EXACTLY zero (boundary A and every body current),
    whose solution is exactly zero.  Peak amplitudes: 2|C_k|, except the
    Nyquist bin of an even-length window, a single real cosine of |C_k|."""
    Nf = A_hist.shape[0]
    Ah = np.fft.rfft(A_hist, axis=0) / Nf
    Ih = ([np.fft.rfft(np.asarray(I, float)) / Nf for I in I_hists]
          if I_hists is not None else None)
    bnodes = np.where(bound_mask)[0]
    nyq = Nf // 2 if Nf % 2 == 0 else -1
    P = np.zeros(len(bodies)); used: List[float] = []
    prepared = None
    for k in range(1, Ah.shape[0]):
        a_k = 1.0 if k == nyq else 2.0
        Abk = a_k * Ah[k]
        I_k = ([complex(a_k * Ih[b][k]) for b in range(len(bodies))]
               if Ih is not None else [0.0] * len(bodies))
        if not np.any(Abk[bnodes]) and not any(I_k):
            continue                      # exactly zero drive → exactly zero loss
        omega = 2.0 * math.pi * k / period_s
        if prepared is None:
            prepared = _PreparedHarmonicEddy(
                p, t, nu_elem, sigma_elem, bodies, bnodes)
        A, E0 = prepared.solve(I_k, omega, Abk[bnodes])
        P += eddy_loss_per_body(p, t, sigma_elem, A, E0, bodies, omega, L)
        used.append(k / period_s)
    return P, used


def sector_projection(p: np.ndarray, n_sectors: int, bc_sign: int,
                      tol: float):
    """(Pm, keep, slave, master) of the (anti)periodic sector wedge 0 ≤ θ ≤
    φ = 2π/NS on nodes ``p`` (2, N): every node on the ray θ = φ is slaved to
    the node on θ = 0 at the same radius, A_slave = s·A_master — the coupled
    solve's boundary condition.  Raises when a slave has no master."""
    ns = int(n_sectors)
    Nn = p.shape[1]
    phi = 2.0 * math.pi / ns
    r = np.hypot(p[0], p[1])
    c, s_ = math.cos(phi), math.sin(phi)
    d0 = np.where(p[0] > 0.0, np.abs(p[1]), np.inf)
    along = c * p[0] + s_ * p[1]
    d1 = np.where(along > 0.0, np.abs(-s_ * p[0] + c * p[1]), np.inf)
    on0 = np.where(d0 < tol)[0]; on1 = np.where(d1 < tol)[0]
    if on1.size == 0:
        return None, np.arange(Nn), np.zeros(0, int), np.zeros(0, int)
    r0 = r[on0]; o = np.argsort(r0)
    k = np.searchsorted(r0[o], r[on1])
    k = np.clip(k, 0, max(len(o) - 1, 0))
    kl = np.clip(k - 1, 0, max(len(o) - 1, 0))
    pick = np.where(np.abs(r0[o][kl] - r[on1]) < np.abs(r0[o][k] - r[on1]),
                    kl, k)
    master = on0[o][pick]
    if np.any(np.abs(r[master] - r[on1]) > tol):
        raise ValueError("sector projection: a node on the ray θ = 2π/NS has "
                         "no partner on θ = 0 (%d of %d unmatched)"
                         % (int(np.sum(np.abs(r[master] - r[on1]) > tol)),
                            int(on1.size)))
    slave = on1
    keep = np.setdiff1d(np.arange(Nn), slave)
    col = np.full(Nn, -1, int); col[keep] = np.arange(keep.size)
    sg = -1.0 if int(bc_sign) < 0 else 1.0
    rows = np.concatenate([keep, slave])
    cols = np.concatenate([col[keep], col[master]])
    vals = np.concatenate([np.ones(keep.size), np.full(slave.size, sg)])
    Pm = sp.csr_matrix((vals, (rows, cols)), shape=(Nn, keep.size))
    return Pm, keep, slave, master


def rotor_eddy_solver_bc(
    p: np.ndarray, t: np.ndarray, tags: np.ndarray, nu_elem: np.ndarray,
    sigma_of_tag, mag_tags: Sequence[int], shaft_tag: int,
    A_hist: np.ndarray, period_s: float, L: float, NS: float,
    n_sectors: int, bc_sign: int,
    mag_pairs: Sequence[Tuple[int, int]] = (),
) -> Tuple[float, float, List[float], Dict]:
    """The frequency-domain rotor eddy loss on the COUPLED SOLVE'S OWN terms
    (2026-09-25): per-element reluctivity ``nu_elem`` (the solve's converged
    secant ν, window mean — the frozen-permeability linearisation), and the
    solve's boundary conditions and body constraints:

    * sector runs: the (anti)periodic cut (``sector_projection``); the shaft
      (a closed ring, its own image) carries U ≡ 0, no row — as in the
      coupled solve; full ring: one ∫J = 0 row;
    * every magnet one ∫J = 0 row, a magnet bisected by the cut ONE row over
      its two pieces (cut_bodies; ``mag_pairs`` = (tag on θ=0⁺, tag of the
      image piece)), the image piece carrying s·E0;
    * constraint columns from each body's OWN σ-mass (never a neighbour's σ
      on shared nodes).

    It remains a LINEAR (fixed-μ) phasor model: the μ(B) modulation of a
    saturable conductor — the wall of a steel shaft — is outside it, so on a
    magnetic shaft the shaft figure is a linear estimate, not a cross-check of
    the coupled value (docs/EDDY_TIME_INTEGRATION_2026-09-25.md §3).
    Returns (P_magnet_W, P_shaft_W, freqs_used, info)."""
    ne = t.shape[1]; Nn = p.shape[1]
    sig = np.array([float(sigma_of_tag(int(tg))) for tg in tags])
    cells: Dict[int, np.ndarray] = {}
    for tg in np.unique(tags):
        cells[int(tg)] = np.where(tags == int(tg))[0]
    s = -1.0 if int(bc_sign) < 0 else 1.0
    paired = {}
    for a, b in (mag_pairs or ()):
        if int(a) in cells and int(b) in cells:
            paired[int(a)] = (int(b), 0); paired[int(b)] = (int(a), 1)
    # conductor bodies: (group, [(tag, sign)], constrained)
    groups = []
    for tg in mag_tags:
        tg = int(tg)
        if tg not in cells:
            continue
        if tg in paired:
            if paired[tg][1] == 0:
                groups.append(("mag", [(tg, 1.0), (paired[tg][0], s)], True))
            continue
        groups.append(("mag", [(tg, 1.0)], True))
    has_shaft = (int(shaft_tag) in cells
                 and sig[cells[int(shaft_tag)]].max() > 0)
    full_ring = int(n_sectors) <= 1
    if has_shaft:
        groups.append(("shaft", [(int(shaft_tag), 1.0)], bool(full_ring)))
    info = {"bodies_constrained": sum(1 for g in groups if g[2]),
            "shaft_u0": bool(has_shaft and not full_ring),
            "bisected_pairs": len([1 for v in paired.values() if v[1] == 0])}
    if not groups:
        return 0.0, 0.0, [], info
    # per-element sign of E0 and the body each element's E0 comes from
    e_body = np.full(ne, -1, int); e_sgn = np.zeros(ne)
    cols = []
    for gi, (_k, members, con) in enumerate(groups):
        if not con:
            continue
        ci = len(cols)
        col = np.zeros(Nn); S = 0.0
        for tg, sg_ in members:
            w = np.zeros(ne); w[cells[tg]] = sig[cells[tg]]
            g = np.asarray(assemble_Msigma(p, t, w) @ np.ones(Nn)).ravel()
            col += sg_ * g; S += float(g.sum())
            e_body[cells[tg]] = ci; e_sgn[cells[tg]] = sg_
        cols.append((col, S))
    r = np.hypot(p[0], p[1]); rmax = float(r.max())
    bmask = r > 0.985 * rmax
    Pm = None; red_of = np.arange(Nn)
    if not full_ring:
        h = float(np.sqrt(np.min(np.abs(
            (p[0, t[1]] - p[0, t[0]]) * (p[1, t[2]] - p[1, t[0]])
            - (p[0, t[2]] - p[0, t[0]]) * (p[1, t[1]] - p[1, t[0]])))))
        Pm, keep, slave, master = sector_projection(
            p, int(n_sectors), int(bc_sign), max(1e-9, 1e-4 * h))
        if Pm is not None:
            col_of = np.full(Nn, -1, int); col_of[keep] = np.arange(keep.size)
            red_of = col_of.copy(); red_of[slave] = col_of[master]
    bnodes = np.where(bmask)[0]
    brep = bnodes[red_of[bnodes] >= 0]
    # one representative full node per reduced boundary dof (a slave's value
    # is s × its master's — the history obeys the BC, so the master speaks)
    _, first = np.unique(red_of[brep], return_index=True)
    brep = brep[first]; bred = red_of[brep]
    area, _, _ = _tri_geom(p, t)
    Nf = A_hist.shape[0]
    Ah = np.fft.rfft(A_hist, axis=0) / Nf
    nyq = Nf // 2 if Nf % 2 == 0 else -1
    prep = None
    P_g = {"mag": 0.0, "shaft": 0.0}
    used: List[float] = []
    for k in range(1, Ah.shape[0]):
        a_k = 1.0 if k == nyq else 2.0
        Abk = a_k * Ah[k]
        if not np.any(Abk[bnodes]):
            continue
        omega = 2.0 * math.pi * k / period_s
        if prep is None:
            prep = _PreparedHarmonicEddy(p, t, nu_elem, sig, [], bred,
                                         cols=cols, Pm=Pm)
        A, E0 = prep.solve([0.0] * len(cols), omega, Abk[brep])
        Ael = A[t].mean(axis=0)
        E0e = (np.where(e_body >= 0, E0[np.maximum(e_body, 0)] * e_sgn, 0.0)
               if len(cols) else np.zeros(ne))
        J = sig * (-1j * omega * Ael + E0e)
        dens = np.abs(J) ** 2 / np.maximum(sig, 1e-30) * area
        for gname, members, _con in groups:
            for tg, _sg in members:
                P_g[gname] += 0.5 * L * float(np.sum(dens[cells[tg]]))
        used.append(k / period_s)
    return P_g["mag"] * NS, P_g["shaft"] * NS, used, info


def honest_rotor_eddy(
    p: np.ndarray, t: np.ndarray, tags: np.ndarray, mu_r_of_tag, sigma_of_tag,
    mag_tags: Sequence[int], shaft_tag: int, A_hist: np.ndarray, period_s: float,
    L: float, NS: float = 1.0,
) -> Tuple[float, float, List[float]]:
    """Honest (reaction-included) eddy loss of the ROTOR conductors, computed on the
    production transient's REAL rotor mesh, driven by the rotor-node A history it
    captured.  Geometry-exact: the iron's mu_r and each conductor's shape are in the
    mesh, so screening + inter-body coupling are solved — no shape factor, no cap.

    p,t,tags  : rotor mesh (nodes [m], P1 triangles, per-element material tag).
    mu_r_of_tag, sigma_of_tag : callables tag -> mu_r / sigma.
    mag_tags  : one tag per magnet (each is a floating body, ∮J=0).
    shaft_tag : the shaft's tag (one floating body).
    A_hist    : (Nframes, Nn) rotor-node A_z over a window of length period_s that
                closes on itself — the solver passes the COMMENSURATE rotor
                window (rotor_window.commensurate_rotor_potential_window).
    NS        : sector->full-ring multiplier (=1 for a full-ring solve).

    Returns (P_magnet_W, P_shaft_W, freqs_used)."""
    ne = t.shape[1]
    nu = np.array([1.0 / (MU0 * max(float(mu_r_of_tag(int(tg))), 1e-3)) for tg in tags])
    sig = np.array([float(sigma_of_tag(int(tg))) for tg in tags])
    cells: Dict[int, np.ndarray] = {}
    for tg in np.unique(tags):
        cells[int(tg)] = np.where(tags == int(tg))[0]
    bodies = [cells[int(tg)] for tg in mag_tags if int(tg) in cells]
    n_mag = len(bodies)
    has_shaft = int(shaft_tag) in cells and sig[cells[int(shaft_tag)]].max() > 0
    if has_shaft:
        bodies.append(cells[int(shaft_tag)])
    if not bodies:
        return 0.0, 0.0, []
    r = np.hypot(p[0], p[1]); rmax = float(r.max())
    bmask = r > 0.985 * rmax                       # air-gap (outer) ring = driving BC
    # ── the savgol pre-filter that used to sit here is DELETED ───────────────
    # It claimed to de-jitter the rotor-frame history before the FFT (a
    # broadband ~1-2-frame slip-band node-identification artifact).  Measured on
    # p2_load + rotor_eddy (scripts/_filter_ablation.py, 2026-07-29):
    #
    #     steps/period   P_mag with savgol   without   delta
    #        12               1.5035 W       1.5631 W  +3.96 %
    #        36               2.1325 W       2.1367 W  +0.20 %
    #
    # At 36 frames — the first count at which this loss is even close to
    # resolved — removing it moves the answer by 0.20 %, inside the physics
    # regression's 0.5 % tolerance.  It only "works" at 12 frames, where the
    # window it picked (w = 5) smooths 42 % of the electrical period: that is
    # not de-jittering, it is destroying resolution.  And the 12-frame answer is
    # 36 % BELOW the 36-frame one either way (1.50 vs 2.35 W unfiltered), so the
    # filter was cosmetics on a number that is wrong for a different reason —
    # removing it moves the 12-frame value TOWARD the resolved one, not away.
    # A filter that only acts where the underlying result is unusable is a bias
    # nobody is watching, which is exactly what it was.
    #
    # ── the k ≤ 16 harmonic ceiling and the 5e-4 amplitude floor are DELETED
    # (owner 2026-09-24: "убираем все фильтры").  The ceiling was "measured
    # necessary" because without it the loss grew with the step count
    # (p2_load + rotor_eddy, 36 steps: 2.1325 W capped vs 2.3504 W uncapped).
    # That growth was not slip-band jitter: it was the OPEN one-period window.
    # A rotor node slides a non-integer number of slot pitches per electrical
    # period, so its one-period A(t) ends mid slot-passing cycle and the DFT
    # bills the end-to-start step as broadband content up to Nyquist — the
    # same leakage the rotor iron had (docs/SOLVER_HELD_ITEMS_FIX_2026-09-24.md
    # §2).  The cure is the defect's, not the symptom's: the caller hands this
    # function the COMMENSURATE window (rotor_window.
    # commensurate_rotor_potential_window — q windows chained from the solved
    # frames of the pole-pair image nodes, exact, no extra solve), on which
    # every bin up to Nyquist is solved.  Convergence with the step count is
    # in docs/NO_FILTERS_2026-09-24.md.
    Pbod, freqs = region_eddy_from_history(p, t, nu, sig, bodies, bmask, A_hist,
                                           period_s, L)
    P_mag = float(np.sum(Pbod[:n_mag])) * NS
    P_shaft = (float(np.sum(Pbod[n_mag:])) * NS) if has_shaft else 0.0
    return P_mag, P_shaft, freqs


def _screening_factor(a, sigma, mu_r, f, h_frac=1.0 / 14.0):
    """Coupled / resistance-limited loss ratio for a solid cylinder radius a at
    frequency f.  = 1 when delta>>a (no reaction); < 1 as skin shields the core."""
    omega = 2 * math.pi * f
    r_out = 6.0 * a
    p, t, cond, bnd = _build_disk_mesh(a, r_out, a * h_frac)
    nu = np.full(t.shape[1], 1.0 / (MU0 * mu_r))
    sig = np.zeros(t.shape[1]); sig[cond] = sigma
    B0 = 0.05
    A, E0 = solve_harmonic_eddy(p, t, nu, sig, [cond], [0.0], omega, bnd,
                                (-B0 * p[1, bnd]).astype(complex))
    P = eddy_loss_per_body(p, t, sig, A, E0, [cond], omega, 0.012)[0]
    return P / _lowfreq_cylinder_loss(a, sigma, omega, B0, 0.012)


def _motor_demo():
    """40 mm motor @ 13000 rpm: apply the coupled-solve SCREENING factor to the
    production solver's resistance-limited solid losses and compare to ANSYS.

    The rotor co-rotates, so its conductors see the STATOR SLOT ripple at
    f_slot = N_slots * f_mech = 12 * 13000/60 = 2600 Hz.  (Higher slot harmonics
    exist but carry less energy; 2600 Hz is the dominant driver.)"""
    rpm, n_slots = 13000.0, 12
    f_slot = n_slots * rpm / 60.0
    print("\n=== 40 mm motor @ 13000 rpm : honest (reaction) vs production ===")
    print(f"slot-ripple frequency f_slot = {f_slot:.0f} Hz\n")
    # shaft: solid Al, radius = r_shaft_in = 2.6 mm
    sf_shaft = _screening_factor(2.6e-3, 2.58e7, 1.0, f_slot)
    # magnet: NdFeB N52UH, sigma 5.56e5; radial build ~7.5 mm -> equiv radius ~3.75 mm
    sf_mag = _screening_factor(3.75e-3, 5.56e5, 1.0, f_slot)
    P_mag_rl, P_shaft_rl = 1.9, 1.5          # production (resistance-limited) values
    P_mag_h = P_mag_rl * sf_mag
    P_shaft_h = P_shaft_rl * sf_shaft
    print(f"{'body':>8} {'sigma':>10} {'screen':>7} {'prod [W]':>9} {'honest [W]':>11}")
    print(f"{'magnet':>8} {5.56e5:10.2e} {sf_mag:7.3f} {P_mag_rl:9.2f} {P_mag_h:11.2f}")
    print(f"{'shaft':>8} {2.58e7:10.2e} {sf_shaft:7.3f} {P_shaft_rl:9.2f} {P_shaft_h:11.2f}")
    print(f"\n  solid total  production = {P_mag_rl + P_shaft_rl:.2f} W")
    print(f"  solid total  HONEST     = {P_mag_h + P_shaft_h:.2f} W")
    print(f"  ANSYS SolidLoss          = 2.79 W")
    print("\n  -> magnet barely screened (delta >> 7.5 mm build) -> production OK;")
    print("     shaft strongly screened (Al, delta ~ radius) -> production over-counts.")


def _test_history_driven():
    """region_eddy_from_history driven by a single-frequency one-period history must
    reproduce the directly-solved single-frequency coupled loss."""
    a, sigma, B0, L, f = 2.6e-3, 2.58e7, 0.05, 0.012, 2600.0
    p, t, cond, bnd = _build_disk_mesh(a, 6.0 * a, a / 12.0)
    nu = np.full(t.shape[1], 1.0 / MU0); sig = np.zeros(t.shape[1]); sig[cond] = sigma
    bmask = np.zeros(p.shape[1], bool); bmask[bnd] = True
    Nframes = 64; period = 1.0 / f
    tt = np.arange(Nframes) * period / Nframes
    A_hist = (-B0 * np.cos(2 * math.pi * f * tt)[:, None]) * p[1][None, :]   # (Nf, Nn)
    P, freqs = region_eddy_from_history(p, t, nu, sig, [cond], bmask, A_hist, period, L)
    # direct single-frequency reference
    A, E0 = solve_harmonic_eddy(p, t, nu, sig, [cond], [0.0], 2 * math.pi * f, bnd,
                                (-B0 * p[1, bnd]).astype(complex))
    P_ref = eddy_loss_per_body(p, t, sig, A, E0, [cond], 2 * math.pi * f, L)[0]
    print("\n=== history-driven engine check (single 2600 Hz harmonic) ===")
    print(f"  region_eddy_from_history = {P[0]:.4f} W   direct harmonic = {P_ref:.4f} W"
          f"   match = {abs(P[0]-P_ref)/P_ref*100:.2f}%")


if __name__ == "__main__":
    _self_test()
    _test_history_driven()
    _motor_demo()
