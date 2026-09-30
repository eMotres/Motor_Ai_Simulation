"""Time-periodic (TDM) eddy steady state against a long BDF2 march.

A synthetic 1-D analogue of the P2 bordered eddy system: a nonlinear
stiffness K(A)A = K0 A + beta A^3 (the same Kpw/tangent2 contract), one
current-constrained conductor (the constraint row of P2Drive.eddy_solve), one
slow U = 0 ring (the shaft's role), a DC source (the magnet's role) and a
frame-dependent projection (the slip weld's role).  The reference is the
march itself, marched until its period change is at round-off; the TDM orbit
must be its fixed point.
"""
import math

import numpy as np
import pytest
from scipy.sparse import csr_matrix, diags, identity, coo_matrix

pytest.importorskip("pypardiso")

from motor_ai_sim.simulation import time_periodic as tp  # noqa: E402

N = 40
NSTEP = 24


def _model(dc_source: bool, sig_ring: float = 0.5):
    k = 4.0e3
    K0 = diags([-k * np.ones(N - 1), 2 * k * np.ones(N), -k * np.ones(N - 1)],
               [-1, 0, 1], format="csr")
    sig = np.zeros(N)
    sig[5:13] = 0.2          # the constrained conductor (a wire)
    sig[20:31] = sig_ring    # the slow ring (a shaft)
    Msig = diags(sig, format="csr")
    g = Msig @ np.where((np.arange(N) >= 5) & (np.arange(N) < 13), 1.0, 0.0)
    G = csr_matrix(g.reshape(-1, 1))
    S = np.array([g.sum()])
    f = np.zeros(N)
    if dc_source:
        f[33:37] = 40.0
    beta = 5.0e2

    def kfun(A):
        return (K0 + diags(beta * A * A, format="csr")).tocsr(), A.copy()

    def tangent(info):
        return diags(2.0 * beta * info * info, format="csr")
    return dict(K0=K0, Msig=Msig, G=G, S=S, f=f, kfun=kfun, tangent=tangent)


def _pro(k):
    """identity with a weld 16 == 17 on even frames (a moving constraint)."""
    if k % 2:
        return identity(N, format="csr")
    rows = list(range(N)); cols = list(range(N)); cols[17] = 16
    P = coo_matrix((np.ones(N), (rows, cols)), shape=(N, N)).tocsc()
    keep = np.flatnonzero(np.asarray(P.sum(axis=0)).ravel() > 0)
    return P[:, keep].tocsr()


def _free(P):
    # the last dof is the outer Dirichlet ring
    last = np.flatnonzero(np.asarray(P[N - 1].todense()).ravel())
    return np.setdiff1d(np.arange(P.shape[1]), last)


def _cur(k, I0=3.0):
    return np.array([I0 * math.sin(2.0 * math.pi * k / NSTEP)])


def _step(m, P, free, A_start, U_start, I, Ahist, dt, bdf2):
    """One march step: the bordered Newton of P2Drive.eddy_solve, dense."""
    dte = tp.DTE_FACTOR * dt if bdf2 else dt
    Msd = m["Msig"] / dte
    Sdt = m["S"] * dte
    # the start projected on this frame's constraint manifold, as the frame
    # loop does (a welded pair must start equal)
    pd = np.asarray(P.multiply(P).sum(axis=0)).ravel()
    A = P @ (np.asarray(P.T @ A_start).ravel() / np.maximum(pd, 1.0))
    U = U_start.copy()
    Pf = P[:, free].toarray()
    for _ in range(60):
        K, info = m["kfun"](A)
        rhs = m["f"] + Msd @ Ahist
        rf = Pf.T @ (K @ A + Msd @ A - m["G"] @ U - rhs)
        cr = dte * I - m["G"].T @ Ahist
        rc = Sdt * U - m["G"].T @ A - cr
        if max(np.linalg.norm(rf) / max(np.linalg.norm(Pf.T @ rhs), 1e-30),
               np.linalg.norm(rc) / max(np.linalg.norm(cr), np.linalg.norm(m["G"].T @ A),
                                        1e-30)) < 1e-12:
            break
        J = (K + m["tangent"](info) + Msd).toarray()
        Jff = Pf.T @ J @ Pf
        B = Pf.T @ m["G"].toarray()
        Mb = np.block([[Jff, -B], [-B.T, np.diag(Sdt)]])
        x = np.linalg.solve(Mb, -np.concatenate([rf, rc]))
        A = A + Pf @ x[:free.size]
        U = U + x[free.size:]
    return A, U


def _march(m, dt, periods):
    A1 = np.zeros(N); A2 = None; U = np.zeros(1)
    out = []
    for k in range(periods * NSTEP):
        P = _pro(k % NSTEP); fr = _free(P)
        if A2 is None:
            Ah = A1; bdf2 = False
        else:
            Ah = tp.C_M1 * A1 + tp.C_M2 * A2; bdf2 = True
        A, U = _step(m, P, fr, A1, U, _cur(k % NSTEP), Ah, dt, bdf2)
        A2, A1 = A1, A
        out.append(A)
    return out


def _tdm(m, dt, n_frames, wrap, coarse, starts):
    cond = np.flatnonzero(m["Msig"].diagonal() > 0)
    Msd = tp.bdf2_msd(m["Msig"], dt)
    frames = []
    for j in range(n_frames):
        P = _pro(j); fr = _free(P)
        frames.append(tp.TdmFrame(j, P, fr, _cur(j), Msd=Msd, G=m["G"], cond=cond,
                                  factor=tp.FrameFactor()))
    spec = None
    if coarse:
        ring = np.arange(20, 31)
        perm = np.arange(ring.size); sign = np.ones(ring.size)
        spec = {"ring": ring, "Msig": m["Msig"], "image": (perm, sign, 1, 1.0),
                "period_s": n_frames * dt}
    s = tp.TimePeriodicEddy(kfun=m["kfun"], tangent=m["tangent"], f_mag=m["f"],
                            G=m["G"], Msig=m["Msig"], S_raw=m["S"], dt=dt,
                            frames=frames, wrap_back=wrap, cond=cond, coarse=spec,
                            tol=1e-11, workers=2)
    try:
        st = s.solve(starts, [np.zeros(1)] * n_frames)
        return [fr.A.copy() for fr in s.frames], st
    finally:
        s.close()


@pytest.mark.parametrize("coarse", [False, True])
def test_periodic_orbit_is_the_fixed_point_of_the_march(coarse):
    m = _model(dc_source=True)
    dt = 1e-4
    ref = _march(m, dt, periods=150)
    last = ref[-NSTEP:]
    chg = max(np.linalg.norm(last[j] - ref[-2 * NSTEP + j]) for j in range(NSTEP))
    assert chg < 1e-9 * max(np.linalg.norm(a) for a in last)   # the march settled
    starts = [np.zeros(N) for _ in range(NSTEP)]
    orbit, st = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), coarse, starts)
    assert st["converged"]
    err = max(np.linalg.norm(orbit[j] - last[j]) for j in range(NSTEP))
    assert err < 1e-7 * max(np.linalg.norm(a) for a in last)
    assert st["newton_iterations"] <= 12


def test_coarse_correction_cuts_the_krylov_count():
    m = _model(dc_source=True, sig_ring=20.0)   # tau ~ 200 periods
    dt = 1e-4
    starts = [np.zeros(N) for _ in range(NSTEP)]
    _, st0 = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), False, starts)
    _, st1 = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), True, starts)
    assert st1["converged"] and st0["converged"]
    assert st1["gmres_iterations"] < st0["gmres_iterations"]


def test_half_period_antiperiodic_orbit():
    """No DC source: the orbit is anti-periodic, A(t + T/2) = -A(t); half the
    frames with the wrap A_{-1} = -A_{N/2-1} give the same orbit."""
    m = _model(dc_source=False)
    dt = 1e-4
    ref = _march(m, dt, periods=150)
    last = ref[-NSTEP:]
    half = NSTEP // 2
    starts = [np.zeros(N) for _ in range(half)]
    orbit, st = _tdm(m, dt, half, lambda v: -np.array(v, float), False, starts)
    assert st["converged"]
    err = max(np.linalg.norm(orbit[j] - last[j]) for j in range(half))
    assert err < 1e-7 * max(np.linalg.norm(a) for a in last)
    # and the second half is the negated first
    err2 = max(np.linalg.norm(-orbit[j] - last[half + j]) for j in range(half))
    assert err2 < 1e-7 * max(np.linalg.norm(a) for a in last)


def test_gmres_right_solves_a_nonsymmetric_system():
    rng = np.random.default_rng(0)
    A = np.eye(30) + 0.3 * rng.standard_normal((30, 30)) / math.sqrt(30)
    b = rng.standard_normal(30)
    x, info = tp.gmres_right(lambda v: A @ v, b, rtol=1e-12, restart=10, maxiter=300)
    assert np.linalg.norm(A @ x - b) <= 1e-10 * np.linalg.norm(b)
    D = np.diag(1.0 / np.diag(A))
    x2, _ = tp.gmres_right(lambda v: A @ v, b, prec=lambda v: D @ v, rtol=1e-12)
    assert np.allclose(x, x2, atol=1e-9)


# -- demag helpers: pole image maps of the magnets, the owner's shortcut -----
def _ring_of_magnets(n_mag=5, n_el=6, r=0.05):
    """n_mag magnets as rings of n_el 'elements' (centroids) in a sector of
    n_mag poles (anti-periodic, NS = 2 on a 2*n_mag-pole machine)."""
    pitch = math.pi / n_mag                 # sector = pi, n_mag poles
    cen = []
    mags = []
    for m in range(n_mag):
        idx = []
        for e in range(n_el):
            a = (m + 0.15 + 0.7 * e / (n_el - 1)) * pitch
            rr = r + 0.001 * (e % 3)
            cen.append((rr * math.cos(a), rr * math.sin(a)))
            idx.append(len(cen) - 1)
        mags.append({"tag": 100 + m, "idx": np.array(idx)})
    cen = np.array(cen).T
    return mags, cen, np.full(cen.shape[1], 1e-6), pitch


def test_shortcut_maps_the_reference_magnet_to_every_pole():
    mags, cen, ar, pitch = _ring_of_magnets()
    maps, info = tp.magnet_image_maps(mags, cen, ar, 2, -1, pitch, len(mags))
    assert maps is not None, info
    br = np.ones(cen.shape[1])
    ref = 2
    br[mags[ref]["idx"]] = np.linspace(0.8, 0.95, len(mags[ref]["idx"]))
    new, mi = tp.map_br_from_reference(mags, ref, br, maps)
    assert mi["complete"]
    for d in mags:                     # every magnet carries the same map,
        assert np.allclose(new[d["idx"]], br[mags[ref]["idx"]])   # element by element


def test_image_min_is_the_elementwise_minimum_over_poles():
    mags, cen, ar, pitch = _ring_of_magnets()
    maps, _ = tp.magnet_image_maps(mags, cen, ar, 2, -1, pitch, len(mags))
    rng = np.random.default_rng(3)
    br = 1.0 - 0.1 * rng.random(cen.shape[1])
    out = tp.image_min_br(mags, br, maps)
    stack = np.array([br[d["idx"]] for d in mags])
    for d in mags:
        assert np.allclose(out[d["idx"]], stack.min(axis=0))


def test_analytic_dnu_dB2_matches_the_curve_off_the_knots():
    from motor_ai_sim.simulation.field_ops import MU0, _mu_r_from_bh_vec
    curve = [(0.0, 0.0), (80.0, 0.5), (150.0, 1.0), (400.0, 1.4), (2000.0, 1.7),
             (12000.0, 2.0), (60000.0, 2.2)]
    rng = np.random.default_rng(7)
    B = rng.uniform(0.05, 2.6, 400)
    knots = np.array([p[1] for p in curve])
    B = B[np.min(np.abs(B[:, None] - knots[None, :]), axis=1) > 2e-3]

    def nu(b):
        return 1.0 / (MU0 * np.maximum(_mu_r_from_bh_vec(curve, b), 1.0))
    h = 1e-6
    fd = (nu(np.sqrt(B * B + h)) - nu(np.sqrt(B * B - h))) / (2 * h)
    an = tp.dnu_dB2(curve, B, MU0)
    assert np.allclose(an, fd, rtol=1e-4, atol=1e-6 * np.max(np.abs(fd)))
