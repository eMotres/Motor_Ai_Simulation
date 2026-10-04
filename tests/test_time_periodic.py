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


def _tdm(m, dt, n_frames, wrap, coarse, starts, workers=2, **solver_kw):
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
                            tol=1e-11, workers=workers, **solver_kw)
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


# -- Codex review 2026-09-30: parallel frames, GMRES status, the gate --------
def test_parallel_frames_equal_serial_and_release_every_handle():
    """Per-frame state is private (own factor, own PARDISO handle, Kpw memo is
    content-keyed): the orbit with 3 workers equals the serial one bit for bit,
    run after run, and no handle outlives the solver."""
    m = _model(dc_source=True)
    dt = 1e-4
    starts = [np.zeros(N) for _ in range(NSTEP)]
    ref, st_ref = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), True, starts,
                       workers=1)
    assert st_ref["converged"]
    for _rep in range(3):
        par, st = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), True, starts,
                       workers=3)
        assert st["converged"]
        assert st["newton_iterations"] == st_ref["newton_iterations"]
        assert max(float(np.max(np.abs(a - b))) for a, b in zip(par, ref)) == 0.0
    assert tp.FrameFactor.open_handles() == 0


def test_gmres_reports_convergence_and_the_true_residual():
    rng = np.random.default_rng(1)
    A = np.eye(40) + 0.9 * rng.standard_normal((40, 40)) / math.sqrt(40)
    b = rng.standard_normal(40)
    x, info = tp.gmres_right(lambda v: A @ v, b, rtol=1e-12, restart=5, maxiter=5)
    assert info["converged"] is False
    true = np.linalg.norm(b - A @ x) / np.linalg.norm(b)
    assert info["rel_resid"] == pytest.approx(true, rel=1e-12)
    x, info = tp.gmres_right(lambda v: A @ v, b, rtol=1e-10, restart=40, maxiter=400)
    assert info["converged"] is True
    assert np.linalg.norm(b - A @ x) / np.linalg.norm(b) <= 1e-10


def test_newton_rejects_an_inaccurate_wrap_solve():
    """A wrap GMRES that misses its forcing term by more than
    GMRES_ACCEPT_FACTOR is not taken: the Newton stops unconverged (the
    caller then marches)."""
    m = _model(dc_source=True, sig_ring=20.0)     # slow ring: many iterations
    dt = 1e-4
    starts = [np.zeros(N) for _ in range(NSTEP)]
    _, st = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), False, starts,
                 gmres_max=1, eta=1e-8)
    assert st["converged"] is False
    assert st["stopped_by"] == "gmres_not_converged"
    rec = st["newton"][-1]
    assert rec["gmres_converged"] is False
    assert rec["gmres_rel_resid"] > tp.GMRES_ACCEPT_FACTOR * 1e-8


def _perturbed_gmres(factor):
    """A wrap 'GMRES' that returns the exact solution plus a perturbation whose
    TRUE residual is ``factor`` x the forcing term, flagged unconverged."""
    real = tp.gmres_right

    def fake(mv, g, prec=None, x0=None, rtol=1e-8, restart=40, maxiter=200):
        x, info = real(mv, g, prec=prec, rtol=1e-13, restart=60, maxiter=2000)
        d = np.random.default_rng(0).standard_normal(g.size)
        x = x + factor * rtol * np.linalg.norm(g) / np.linalg.norm(mv(d)) * d
        r = float(np.linalg.norm(g - mv(x)) / np.linalg.norm(g))
        return x, {"iterations": int(info["iterations"]), "restarts": 0, "resid": [],
                   "rel_resid": r, "converged": False}
    return fake


def test_bounded_inexact_newton_accepts_a_step_within_the_factor(monkeypatch):
    """Second Codex review, finding 8: a wrap solve that misses its forcing
    term eta but whose TRUE residual is within GMRES_ACCEPT_FACTOR * eta is a
    valid inexact-Newton step (forcing < 1): it is taken, recorded as such
    (true residual recomputed by the solver itself), and the Newton still
    converges to the march's fixed point — the accuracy of the orbit does not
    depend on the GMRES."""
    m = _model(dc_source=True)
    dt = 1e-4
    ref = _march(m, dt, periods=150)[-NSTEP:]
    monkeypatch.setattr(tp, "gmres_right", _perturbed_gmres(5.0))
    starts = [np.zeros(N) for _ in range(NSTEP)]
    orbit, st = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), False, starts,
                     eta=1e-3)
    assert st["converged"] and st["stopped_by"] == "state_residual"
    steps = [r for r in st["newton"] if "gmres_iterations" in r]
    assert steps
    for r in steps:
        assert r["gmres_converged"] is False and r["gmres_accepted_inexact"] is True
        assert 1e-3 < r["gmres_rel_resid"] <= tp.GMRES_ACCEPT_FACTOR * 1e-3
        # recomputed by the solver, independent of what GMRES said
        assert r["gmres_rel_resid"] == pytest.approx(r["gmres_reported_rel_resid"],
                                                     rel=1e-6)
    err = max(np.linalg.norm(orbit[j] - ref[j]) for j in range(NSTEP))
    assert err < 1e-7 * max(np.linalg.norm(a) for a in ref)


def test_bounded_inexact_newton_rejects_a_step_beyond_the_factor(monkeypatch):
    m = _model(dc_source=True)
    dt = 1e-4
    monkeypatch.setattr(tp, "gmres_right", _perturbed_gmres(20.0))
    starts = [np.zeros(N) for _ in range(NSTEP)]
    _, st = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), False, starts, eta=1e-3)
    assert st["converged"] is False and st["stopped_by"] == "gmres_not_converged"
    r = st["newton"][-1]
    assert r["gmres_accepted_inexact"] is False
    assert r["gmres_rel_resid"] > tp.GMRES_ACCEPT_FACTOR * 1e-3


# -- second Codex review (2026-10-03): closure, map checks, demag, shortcut ---
def test_state_closure_per_group_and_level():
    M = {"cu": diags(np.r_[np.ones(3), np.zeros(3)], format="csr"),
         "shaft": diags(np.r_[np.zeros(3), np.ones(3)], format="csr")}
    ref = np.r_[np.ones(3), 0.01 * np.ones(3)]
    got = ref.copy()
    got[4] += 0.01 * 1e-4          # the small shaft group moved 1e-4/sqrt(3) of itself
    ok, info = tp.state_closure([("frame 11", got, ref), ("frame 10", ref, ref)], M,
                                1e-4)
    assert ok and info["levels"]["frame 10"]["shaft"] == 0.0
    ok, info = tp.state_closure([("frame 11", got, ref)], M, 1e-4 / 2)
    assert not ok and info["worst_at"] == ("frame 11", "shaft")
    bad = ref.copy(); bad[0] = np.nan
    assert not tp.state_closure([("x", bad, ref)], M, 1.0)[0]   # non-finite fails


def _sector_maps(n=12):
    """A toy 'period map' on n dofs: dofs 0..5 stator (fixed), 6..11 rotor
    (a cyclic shift by 2 with the anti-periodic BC sign on the fold)."""
    rot = np.arange(6, 12)
    perm = np.roll(np.arange(6), 2)
    sgn = np.where(np.arange(6) < 2, -1.0, 1.0)

    def back(v):
        o = np.array(v, float, copy=True)
        o[rot] = sgn * np.asarray(v, float)[rot[perm]]
        return o

    inv = np.argsort(perm)

    def fwd(v):
        o = np.array(v, float, copy=True)
        o[rot[perm]] = np.asarray(v, float)[rot] * sgn
        return o
    return back, fwd, rot, perm, sgn, inv


def test_period_map_checks_pass_an_exact_map_and_catch_each_defect():
    back, fwd, rot, perm, sgn, _ = _sector_maps()
    n = 12
    I = identity(n, format="csr")
    # operators invariant under the map: stator block anything, rotor block a
    # circulant compatible with the signed shift (diagonal here)
    K = diags(np.r_[np.arange(1.0, 7.0), 2.0 * np.ones(6)], format="csr")
    Ms = diags(np.r_[np.zeros(6), np.ones(6)], format="csr")
    # bodies: one stator coil (dofs 0-1), three rotor bodies of two dofs each
    # that the shift-by-two permutes (with the BC sign on the folded one)
    cols = [np.r_[1, 1, np.zeros(10)]]
    for b in range(3):
        c = np.zeros(n); c[6 + 2 * b: 8 + 2 * b] = 1.0; cols.append(c)
    G = csr_matrix(np.stack(cols, axis=1))
    S = np.array([2.0, 2.0, 2.0, 2.0])
    f = np.zeros(n)            # a source the map leaves invariant
    kw = dict(P_from=I, P_to=I, P_start=I, P_fwd=I, K=K, Msig=Ms, GT=G.T.tocsr(),
              S_raw=S, f_mag=f)
    ok, rec = tp.period_map_checks(wrap=back, wrapf=fwd, **kw)
    assert ok, rec
    assert rec["dev"]["inverse"] == 0.0 and rec["dev"]["bodies"] < 1e-14
    # an inverse that is not exact
    ok, rec = tp.period_map_checks(wrap=back, wrapf=lambda v: fwd(v) * (1 + 1e-6), **kw)
    assert not ok and rec["failed"] == ["inverse"]
    # a wrong BC sign on one rotor dof: the operators and the bodies see it
    def back_bad(v):
        o = back(v); o[rot[3]] *= -1.0; return o

    def fwd_bad(v):
        w = np.array(v, float, copy=True); w[rot[3]] *= -1.0; return fwd(w)
    Kc = K + csr_matrix((np.ones(2), ([rot[3], rot[4]], [rot[4], rot[3]])), shape=(n, n))
    ok, rec = tp.period_map_checks(wrap=back_bad, wrapf=fwd_bad,
                                   **dict(kw, K=Kc))
    assert not ok and "bodies" in rec["failed"]
    # a magnet source the map does not leave invariant
    f2 = np.zeros(n); f2[rot[0]] = 1.0
    ok, rec = tp.period_map_checks(wrap=back, wrapf=fwd, **dict(kw, f_mag=f2))
    assert not ok and rec["failed"] == ["source"]
    # a constrained space the map does not preserve (a weld 0 == 6 at the
    # start frame, no weld at the end frame)
    rows = list(range(n)); cs = list(range(n)); cs[6] = 0
    from scipy.sparse import coo_matrix
    Pw = coo_matrix((np.ones(n), (rows, cs)), shape=(n, n)).tocsc()
    Pw = Pw[:, np.flatnonzero(np.asarray(Pw.sum(axis=0)).ravel() > 0)].tocsr()
    ok, rec = tp.period_map_checks(wrap=back, wrapf=fwd, **dict(kw, P_to=Pw))
    assert not ok and "constrained" in rec["failed"]


def test_demag_settled_is_the_mean_and_the_element_is_a_warning():
    """Owner 2026-10-04: the per-magnet area mean decides (with the drift and
    the image history, in the solver); one element still moving > 1 % of Br0
    is a WARNING, recorded, not a verdict."""
    assert tp.demag_settled({"per_magnet_mean_max": 5e-4, "element_max": 5e-3})
    assert tp.demag_settled({"per_magnet_mean_max": 5e-4, "element_max": 0.02})
    assert not tp.demag_settled({"per_magnet_mean_max": 2e-3, "element_max": 1e-3})
    assert tp.demag_element_warning({"element_max": 5e-3}) is None
    w = tp.demag_element_warning({"element_max": 0.0108})
    assert w and "0.0108" in w and "warning" in w


def test_the_shortcut_window_is_exactly_one_sixth():
    assert tp.demag_window_frames(36) == 6          # the old ceil(0.1666667*36) = 7
    assert tp.demag_window_frames(36, "1/6") == 6
    assert tp.demag_window_frames(36, 1 / 6) == 6
    assert tp.demag_window_frames(40) == 7          # rounded up where it does not divide
    assert tp.demag_window_frames(12) == 2
    assert tp.demag_window_frames(6) == 2           # at least two frames


def test_the_shortcut_is_never_taken_from_the_environment():
    assert tp.resolve_tdm_demag(None, {}) == ("full", None)
    mode, note = tp.resolve_tdm_demag(None, {"SB_TDM_DEMAG": "shortcut"})
    assert mode == "full" and "ignored" in note
    assert tp.resolve_tdm_demag("shortcut", {}) == ("shortcut", None)
    assert tp.resolve_tdm_demag("full", {"SB_TDM_DEMAG": "shortcut"}) == ("full", None)
    with pytest.raises(ValueError):
        tp.resolve_tdm_demag("fast", {})


# -- third / fourth Codex reviews (2026-10-04): the orbit-error ESTIMATE -------
def _solver_open(m, dt, n_frames, coarse, tol):
    cond = np.flatnonzero(m["Msig"].diagonal() > 0)
    Msd = tp.bdf2_msd(m["Msig"], dt)
    frames = [tp.TdmFrame(j, _pro(j), _free(_pro(j)), _cur(j), Msd=Msd, G=m["G"],
                          cond=cond, factor=tp.FrameFactor()) for j in range(n_frames)]
    spec = None
    if coarse:
        ring = np.arange(20, 31)
        spec = {"ring": ring, "Msig": m["Msig"],
                "image": (np.arange(ring.size), np.ones(ring.size), 1, 1.0),
                "period_s": n_frames * dt}
    return tp.TimePeriodicEddy(kfun=m["kfun"], tangent=m["tangent"], f_mag=m["f"],
                               G=m["G"], Msig=m["Msig"], S_raw=m["S"], dt=dt,
                               frames=frames, wrap_back=lambda v: np.array(v, float),
                               cond=cond, coarse=spec, tol=tol, workers=1), cond


def _march_one_period(m, dt, As, Us):
    """The test's own march (dense bordered Newton) from the orbit's start."""
    a1, a2 = As[-1], As[-2]
    out = []
    U = Us[0].copy()
    for k in range(NSTEP):
        P = _pro(k)
        Ah = tp.C_M1 * a1 + tp.C_M2 * a2
        A, U = _step(m, P, _free(P), As[k], U, _cur(k), Ah, dt, True)
        out.append(A)
        a2, a1 = a1, A
    return out


def test_the_orbit_error_estimate_sees_the_slow_mode():
    """A loosely converged orbit on a slow ring (tau ~ 200 periods): the
    one-period closure defect d is far SMALLER than the true orbit error
    (the slow mode barely moves in one period) — exactly the reviewer's case.
    The estimate recovers it: rho (Arnoldi, converged) is the slow
    eigenvalue of the dense period map, e = (I - T)^-1 d matches the true
    error, and |d| / (1 - rho) covers it here (an estimate, not a proof)."""
    m = _model(dc_source=True, sig_ring=20.0)
    dt = 1e-4
    starts = [np.zeros(N) for _ in range(NSTEP)]
    ref, st_ref = _tdm(m, dt, NSTEP, lambda v: np.array(v, float), True, starts)
    assert st_ref["converged"]
    s, cond = _solver_open(m, dt, NSTEP, True, tol=1e-3)
    try:
        assert s.solve(starts, [np.zeros(1)] * NSTEP)["converged"]
        As = [fr.A.copy() for fr in s.frames]
        Us = [fr.U.copy() for fr in s.frames]
        mar = _march_one_period(m, dt, As, Us)
        d = np.concatenate([(mar[-2] - As[-2])[cond], (mar[-1] - As[-1])[cond]])
        e_true = np.concatenate([(ref[-2] - As[-2])[cond], (ref[-1] - As[-1])[cond]])
        s.refresh_jacobian(As, Us)
        ctr = s.contraction(d, m=15)
        nw = 2 * cond.size
        T = np.column_stack([s.period_map(np.eye(nw)[:, i]) for i in range(nw)])
        lam = float(np.max(np.abs(np.linalg.eigvals(T))))
        e, info = s.orbit_error(d)
    finally:
        s.close()
    assert info["converged"]
    assert lam > 0.9                                   # a slow mode is there
    assert ctr["rho"] == pytest.approx(lam, rel=1e-3) and ctr["ritz_converged"]
    nd, ne = np.linalg.norm(d), np.linalg.norm(e_true)
    assert ne > 3.0 * nd                               # the defect hides it
    # e recovers it to first order (the loose orbit carries second-order terms:
    # measured 5.3 % here)
    assert np.linalg.norm(e - e_true) <= 0.1 * ne
    assert nd / (1.0 - ctr["rho"]) >= 0.9 * ne         # covered here
    assert tp.FrameFactor.open_handles() == 0


def test_estimate_check_against_the_owner_terms():
    base = {"dT_rel": 1e-4, "dripple_pp": 0.01, "dP_cond_W": 1.0, "dP_fe_W": 0.5,
            "ripple_pct": 5.0}
    lim = tp.owner_limits(5.0)
    assert lim == pytest.approx({"T_rel": 1e-3, "ripple_pp": 0.05,
                                 "P_total_rel": 5e-3, "safety": 0.1})
    assert tp.owner_limits(20.0)["ripple_pp"] == pytest.approx(0.2)
    ok, rec = tp.estimate_check(base, P_total_W=1000.0)
    assert ok and rec["estimate_P_total_W"] == pytest.approx(1.5)
    # the reported window's own deviation is ADDED
    win = {"w": {"T_mean_rel": 9.5e-4, "ripple_pp": 0.0, "P": {}}}
    assert not tp.estimate_check(base, P_total_W=1000.0, report_windows=win)[0]
    # the iron loss enters DIRECTLY (no 2 rB P_fe surrogate any more)
    for bad in ({"dT_rel": 2e-3}, {"dripple_pp": 0.06}, {"dP_fe_W": 4.5},
                {"dP_cond_W": float("inf")}):
        assert not tp.estimate_check(dict(base, **bad), P_total_W=1000.0)[0], bad
    ok, rec = tp.estimate_check({"rho_eff": 1.0}, P_total_W=1.0)
    assert not ok and "error" in rec


class _LinearMap(tp.TimePeriodicEddy):
    """Only what `contraction` needs: a period map T given as a dense matrix."""

    def __init__(self, T):  # noqa: D401 — no solver state
        self._T = np.asarray(T, float)

    def period_map(self, w):
        return self._T @ w


def _hard_map(seed=4):
    """A NON-NORMAL map with a NEAR-UNIT real mode (0.995), a COMPLEX PAIR of
    modulus 0.97 and a fast bulk, under a random ill-conditioned similarity."""
    rng = np.random.default_rng(seed)
    n = 30
    D = np.zeros((n, n))
    D[0, 0] = 0.995
    th = 0.4
    D[1:3, 1:3] = 0.97 * np.array([[np.cos(th), -np.sin(th)],
                                   [np.sin(th), np.cos(th)]])
    D[3:, 3:] = np.diag(rng.uniform(-0.3, 0.3, n - 3))
    D[3, 4] = 2.0                            # a Jordan-like non-normal coupling
    S = rng.standard_normal((n, n)) + 3.0 * np.eye(n)
    return S @ D @ np.linalg.inv(S)


def test_arnoldi_rho_on_a_near_unit_non_normal_complex_map():
    """The fourth review's adversarial cases for the estimate's rho: a
    near-unit mode, a complex pair, non-normality.  With enough steps the top
    Ritz value converges to the spectral radius and says so; with too few it
    says it has NOT converged (which sends the solver to the empirical
    safeguard: more closure periods)."""
    T = _hard_map()
    lam = float(np.max(np.abs(np.linalg.eigvals(T))))
    assert lam == pytest.approx(0.995, rel=1e-9)
    v0 = np.random.default_rng(1).standard_normal(T.shape[0])
    full = _LinearMap(T).contraction(v0, m=29)
    assert full["ritz_converged"] and full["rho"] == pytest.approx(lam, rel=1e-6)
    short = _LinearMap(T).contraction(v0, m=3)
    assert short["ritz_converged"] is False
    assert len(short["ritz_history"]) == 3


def test_group_joule_splits_the_frame_loss_by_group():
    m = _model(dc_source=True)
    rng = np.random.default_rng(2)
    A, a1, a2 = (rng.standard_normal(N) for _ in range(3))
    U = np.array([0.7])
    dte = tp.DTE_FACTOR * 1e-4
    sig = m["Msig"].diagonal()
    wire = np.zeros(N); wire[5:13] = sig[5:13]
    ring = np.zeros(N); ring[20:31] = sig[20:31]
    Mg = {"cu": diags(wire, format="csr"), "shaft": diags(ring, format="csr")}
    out = tp.group_joule(A, U, a1, a2, dte=dte, Mg=Mg, GT=m["G"].T.tocsr(),
                         S_raw=m["S"], body_group=["cu"])
    dA = (A - (tp.C_M1 * a1 + tp.C_M2 * a2)) / dte
    whole = float(dA @ (m["Msig"] @ dA)) + float(
        U[0] * (U[0] * m["S"][0] - 2.0 * float(np.asarray(m["G"].T @ dA).ravel()[0])))
    assert out["cu"] + out["shaft"] == pytest.approx(whole, rel=1e-12)
    assert out["shaft"] == pytest.approx(float(dA @ (Mg["shaft"] @ dA)), rel=1e-12)


def test_window_gate_in_the_owners_terms():
    ref = {"T_mean": 10.0, "ripple_pct": 5.0, "P": {"cu": 100.0, "shaft": 1.0,
                                                   "mag": 0.001}}
    ok, info = tp.window_gate(ref, {"T_mean": 10.009, "ripple_pct": 5.04,
                                    "P": {"cu": 100.4, "shaft": 1.004, "mag": 0.0015}})
    assert ok, info           # mag moved 50 % but is below 1e-4 of the total
    for bad in ({"T_mean": 10.02}, {"ripple_pct": 5.06},
                {"P": {"cu": 100.0, "shaft": 1.02, "mag": 0.001}}):
        got = {"T_mean": 10.0, "ripple_pct": 5.0, "P": dict(ref["P"])}
        got.update(bad)
        assert not tp.window_gate(ref, got)[0], bad


def test_br_change_and_the_period_relabelling():
    mags = [{"idx": np.array([0, 1])}, {"idx": np.array([2, 3])}]
    areas = np.array([1.0, 3.0, 1.0, 1.0])
    b0 = np.ones(4)
    b1 = np.array([0.9, 1.0, 1.0, 0.98])
    c = tp.br_change(mags, b0, b1, areas)
    assert c["per_magnet_mean_max"] == pytest.approx(0.1 / 4)   # magnet 0
    assert c["element_max"] == pytest.approx(0.1)
    assert c["area_mean"] == pytest.approx(0.12 / 6)
    # relabel: element elems[i] takes the Br of elems[image[i]]
    br = np.array([5.0, 0.1, 0.2, 0.3, 0.4])
    out = tp.relabel_br(br, np.array([1, 2, 3, 4]), np.array([2, 3, 0, 1]))
    assert out.tolist() == [5.0, 0.3, 0.4, 0.1, 0.2]


def test_frame_factor_falls_back_to_lu_and_releases_its_handle():
    """A matrix Cholesky must decline (indefinite, or unsymmetric) is factorised
    by LU, loudly (lu_used), and solves correctly; close() releases it."""
    rng = np.random.default_rng(3)
    for M in (diags([1.0, -2.0, 3.0, 4.0], format="csr"),                 # indefinite
              csr_matrix(np.eye(4) + 0.3 * rng.standard_normal((4, 4)))):  # unsymmetric
        f = tp.FrameFactor()
        f.factor(M)
        assert f.lu_used
        b = rng.standard_normal(4)
        x = np.asarray(f.solve(b)).ravel()
        assert np.allclose(M @ x, b, atol=1e-10)
        f.close()
    spd = diags([2.0, 3.0, 4.0, 5.0], format="csr")
    f = tp.FrameFactor()
    f.factor(spd)
    assert not f.lu_used
    f.close()
    assert tp.FrameFactor.open_handles() == 0
