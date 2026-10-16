"""Bisected conductors at the sector cut, and the eddy time scheme's order.

Synthetic, seconds, no application import: a P1 annulus in free space with a
travelling current sheet, solved with the PRODUCTION bordered eddy Newton
(p2_drive.P2Drive.eddy_solve) and the production helpers
(cut_bodies.pair_bisected_bodies / merge_pair_constraint,
P2Drive.bdf2_history).  docs/EDDY_TIME_INTEGRATION_2026-09-25.md.

1. A magnet straddling the cut: the half model with the paired row reproduces
   the full-ring solve to round-off; the old "U = 0 on both halves" does not.
2. BDF2 converges as Δt², backward Euler as Δt, to the same periodic loss.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.linalg import spsolve

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from motor_ai_sim.simulation import p2_drive  # noqa: E402
from motor_ai_sim.simulation.cut_bodies import (  # noqa: E402
    merge_pair_constraint, pair_bisected_bodies)

MU0 = 4e-7 * math.pi
R_IN, R_MAG0, R_MAG1, R_SRC, R_OUT = 0.010, 0.014, 0.018, 0.022, 0.024
NR, NTH = 14, 72                      # rings, cells round the FULL ring
SIG = 2.0e6                           # a magnet-class conductor [S/m]
F = 2000.0                            # travelling-field frequency [Hz]
ALPHA = 2 * math.pi / NTH * 4.0       # half-width of a magnet (4 cells)


def _mesh(n_cells, span):
    """Structured P1 annulus sector 0..span with n_cells cells round it.
    span = 2π → full ring (periodic wrap, no duplicate ray)."""
    full = abs(span - 2 * math.pi) < 1e-12
    nj = n_cells if full else n_cells + 1
    rs = np.linspace(R_IN, R_OUT, NR + 1)
    th = np.arange(nj) * (span / n_cells)
    P = np.array([[r * math.cos(a), r * math.sin(a)] for r in rs for a in th]).T
    idx = lambda i, j: i * nj + (j % nj if full else j)       # noqa: E731
    T = []
    for i in range(NR):
        for j in range(n_cells):
            a, b = idx(i, j), idx(i, j + 1)
            c, d = idx(i + 1, j), idx(i + 1, j + 1)
            T += [(a, b, d), (a, d, c)]
    return P, np.array(T).T, rs, th, nj


def _p1(P, T, w):
    x, y = P[0][T], P[1][T]
    ar2 = (x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0])
    area = 0.5 * np.abs(ar2)
    sg = np.sign(ar2)
    b = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]]) * sg / (2 * area)
    c = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]]) * sg / (2 * area)
    I, J, K, M = [], [], [], []
    for i in range(3):
        for j in range(3):
            I.append(T[i]); J.append(T[j])
            K.append((b[i] * b[j] + c[i] * c[j]) * area / MU0)
            M.append(w * area * (2.0 if i == j else 1.0) / 12.0)
    n = P.shape[1]
    I = np.concatenate(I); J = np.concatenate(J)
    return (coo_matrix((np.concatenate(K), (I, J)), shape=(n, n)).tocsr(),
            lambda wt: coo_matrix((np.concatenate(
                [wt * area * (2.0 if i == j else 1.0) / 12.0
                 for i in range(3) for j in range(3)]), (I, J)),
                shape=(n, n)).tocsr(), area)


class _Lin:
    sat = []

    def __init__(self, K):
        self.K = K

    def asmK(self, _nu):
        return self.K

    def solve_ff(self, M, rhs):
        return spsolve(csr_matrix(M).tocsc(), rhs)

    def pad2(self, Pro, free, x):
        z = np.zeros(Pro.shape[1]); z[free] = x
        return np.asarray(Pro @ z).ravel()


class _Model:
    """One model (full ring or half sector) with its bodies and projection."""

    def __init__(self, span, pairing):
        n_cells = NTH if abs(span - 2 * math.pi) < 1e-12 else NTH // 2
        P, T, rs, th, nj = _mesh(n_cells, span)
        self.P, self.T = P, T
        cx, cy = P[0][T].mean(0), P[1][T].mean(0)
        rc = np.hypot(cx, cy); tc = np.mod(np.arctan2(cy, cx), 2 * math.pi)
        ring = (rc > R_MAG0) & (rc < R_MAG1)
        dist = lambda a: np.abs(np.angle(np.exp(1j * (tc - a))))  # noqa: E731
        self.bodies = [np.where(ring & (dist(a) < ALPHA))[0]
                       for a in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)]
        self.bodies = [b for b in self.bodies if b.size]
        K, Mof, area = _p1(P, T, np.zeros(T.shape[1]))
        self.area = area
        n = P.shape[1]
        sig_e = np.zeros(T.shape[1])
        for b in self.bodies:
            sig_e[b] = SIG
        self.Msig = Mof(sig_e)
        self.Mb = []
        for b in self.bodies:
            w = np.zeros(T.shape[1]); w[b] = SIG
            self.Mb.append(Mof(w))
        # source: travelling sheet J0·cos(θ − ωt) in the outer layer; one
        # pole pair, so the field is anti-periodic over π (s = −1)
        self.src_e = np.where(rc > R_SRC)[0]
        self.src_th = np.arctan2(cy, cx)[self.src_e]
        self.T_src = T[:, self.src_e]; self.a_src = area[self.src_e]
        # projection: half model pairs the θ = π ray to −(θ = 0 ray)
        r_all = np.hypot(P[0], P[1])
        outer = np.where(r_all > R_OUT - 1e-12)[0]
        if pairing is None:                 # full ring: identity
            Pro = csr_matrix(np.eye(n))
            keep = np.arange(n)
        else:
            on_pi = np.array([i * nj + (nj - 1) for i in range(NR + 1)])
            on_0 = np.array([i * nj for i in range(NR + 1)])
            keep = np.setdiff1d(np.arange(n), on_pi)
            col = {v: k for k, v in enumerate(keep)}
            rows = list(keep) + list(on_pi)
            cols = [col[v] for v in keep] + [col[v] for v in on_0]
            vals = [1.0] * len(keep) + [-1.0] * len(on_pi)
            Pro = coo_matrix((vals, (rows, cols)), shape=(n, len(keep))).tocsr()
        red_outer = np.unique([np.flatnonzero(Pro[o].toarray().ravel())[0]
                               for o in outer])
        self.Pro = Pro
        self.free = np.setdiff1d(np.arange(Pro.shape[1]), red_outer)
        # constraint rows
        g = [np.asarray(Mb @ np.ones(n)).ravel() for Mb in self.Mb]
        cons = []
        if pairing == "paired":
            pairs, info = pair_bisected_bodies(P, T, self.bodies, 2, 1e-9)
            assert len(pairs) == 1, (pairs, info)
            i0, j0 = pairs[0]
            gm, Sm = merge_pair_constraint(g[i0], g[i0].sum(), g[j0],
                                           g[j0].sum(), -1)
            cons.append((gm, Sm, [(i0, 1.0), (j0, -1.0)]))
            rest = [b for b in range(len(self.bodies)) if b not in (i0, j0)]
        elif pairing == "u0":               # the old rule: halves carry U ≡ 0
            pairs, _ = pair_bisected_bodies(P, T, self.bodies, 2, 1e-9)
            rest = [b for b in range(len(self.bodies))
                    if b not in pairs[0]]
        else:
            rest = list(range(len(self.bodies)))
        for b in rest:
            cons.append((g[b], float(g[b].sum()), [(b, 1.0)]))
        self.cons = cons
        self.G = csr_matrix(np.array([c[0] for c in cons]).T)
        self.S = np.array([c[1] for c in cons])
        self.K = K
        self.n = n

    def source(self, t):
        J = 5e6 * np.cos(self.src_th - 2 * math.pi * F * t)
        f = np.zeros(self.n)
        for k in range(3):
            np.add.at(f, self.T_src[k], J * self.a_src / 3.0)
        return f

    def drive(self, dt):
        ed = [dict(S=float(s), key="mag", Iunit=0.0, phase=None) for s in self.S]
        return p2_drive.P2Drive(
            p2=_Lin(self.K), psi=lambda _a: (0.0, 0.0, 0.0),
            f_mag=np.zeros(self.n), Pa=np.zeros(self.n), Pb=np.zeros(self.n),
            R_phase=1.0, v_phase_peak=1.0, n_dof=self.n, pic_tol=1e-3, dt=dt,
            log=logging.getLogger(__name__), ed_con=ed, G=self.G,
            Msig=self.Msig, Msd=(self.Msig * (1.0 / dt)).tocsr(),
            Sdt=self.S * dt)

    def loss(self, dA, U):
        """σ∫(−∂A/∂t + U)² per metre over all conductors [W/m]."""
        P = float(dA @ (self.Msig @ dA))
        for (g, S, _m), u in zip(self.cons, U):
            P += u * (u * S - 2.0 * float(g @ dA))
        return P

    def march(self, n_steps_pp, n_periods, scheme):
        dt = 1.0 / (F * n_steps_pp)
        drv = self.drive(dt)
        A1 = np.zeros(self.n); A2 = None; U = np.zeros(len(self.cons))
        out = []
        for k in range(1, n_steps_pp * n_periods + 1):
            drv.f_mag = self.source(k * dt)
            if scheme.startswith("bdf2") and A2 is not None:
                dte, Ah = drv.bdf2_history(dt, dt, A1, A2)
            else:
                dte, Ah = None, A1
            ok, A, U, _r, _n = drv.eddy_solve(
                self.Pro, self.free, A1, U, np.zeros(len(self.cons)), Ah,
                1.0, 4, dte=dte)
            assert ok
            if scheme == "bdf2_mid" and A2 is not None:
                # the production midpoint sampling: centred difference, U from
                # the body's own ∫J = 0 row at t_{k−½}
                d = (A - A1) / dt
                Um = np.array([float(c[0] @ d) / c[1] for c in self.cons])
                out.append(self.loss(d, Um))
            else:
                out.append(self.loss((A - Ah) / (dte or dt), U))
            A2, A1 = A1, A
        return A1, np.array(out)


def test_bisected_magnet_paired_row_matches_the_full_ring():
    full = _Model(2 * math.pi, None)
    half = _Model(math.pi, "paired")
    old = _Model(math.pi, "u0")
    Af, Pf = full.march(24, 2, "bdf2")
    Ah, Ph = half.march(24, 2, "bdf2")
    Ao, Po = old.march(24, 2, "bdf2")
    # the half model's nodes are the full ring's first half, same order
    nj_h = NTH // 2 + 1
    map_h = np.array([i * NTH + (j % NTH) for i in range(NR + 1)
                      for j in range(nj_h)])
    scale = float(np.max(np.abs(Af)))
    assert np.max(np.abs(Ah - Af[map_h])) < 1e-9 * scale
    # losses: half model = half the ring, per step, to round-off
    np.testing.assert_allclose(2.0 * Ph, Pf, rtol=1e-8)
    # the old rule (U ≡ 0 on both pieces) is a DIFFERENT machine
    assert abs(2.0 * Po[-24:].mean() / Pf[-24:].mean() - 1.0) > 0.01


def test_pairing_ignores_a_whole_magnet_touching_the_ray():
    # a magnet whose side face lies ON the cut, with no partner: own row
    P, T, *_ = _mesh(NTH // 2, math.pi)
    cx, cy = P[0][T].mean(0), P[1][T].mean(0)
    rc = np.hypot(cx, cy); tc = np.arctan2(cy, cx)
    b = np.where((rc > R_MAG0) & (rc < R_MAG1) & (tc > 0) & (tc < ALPHA))[0]
    pairs, info = pair_bisected_bodies(P, T, [b], 2, 1e-9)
    assert pairs == [] and info["unpaired"] == [0]


@pytest.mark.parametrize("scheme,order", [("be", 1.0), ("bdf2", 2.0),
                                          ("bdf2_mid", 2.0)])
def test_eddy_time_scheme_order(scheme, order):
    """Periodic loss vs steps/period: error ∝ Δt^order against a fine run.
    Measured (σ 2 MS/m, resistance-limited): BE −3.06/−1.42/−0.68 %, BDF2 at
    t_k +4.37/+1.11/+0.28 %, BDF2 sampled at the midpoint −0.71/−0.17/−0.04 %
    at 24/48/96 steps."""
    m = _Model(2 * math.pi, None)
    ref = m.march(384, 6, "bdf2_mid")[1][-384:].mean()
    errs = []
    for n in (24, 48, 96):
        P = m.march(n, 6, scheme)[1][-n:].mean()
        errs.append(abs(P - ref) / ref)
    p1 = math.log2(errs[0] / errs[1]); p2 = math.log2(errs[1] / errs[2])
    assert abs(p1 - order) < 0.35 and abs(p2 - order) < 0.35, (errs, p1, p2)
    if scheme == "bdf2_mid":
        assert errs[0] < 0.02      # the default-class resolution is inside 2 %


def test_variable_step_bdf2_is_exact_on_a_quadratic():
    """Δt_eff/A_hist of p2_drive.bdf2_history reproduce Ȧ(t_k) exactly for
    any quadratic A(t) and any step ratio (the variable-step BDF2 order
    condition), and ω = 1 is the textbook (3A_k − 4A_{k−1} + A_{k−2})/2h."""
    a, b, c = 0.3, -1.7, 2.9
    A = lambda t: a + b * t + c * t * t                     # noqa: E731
    for h_prev, h in ((1e-3, 1e-3), (1e-3, 0.25e-3), (1e-3, 1.8e-3)):
        t0, t1 = 0.0, h_prev
        t2 = t1 + h
        dte, Ah = p2_drive.P2Drive.bdf2_history(h, h_prev, A(t1), A(t0))
        assert abs((A(t2) - Ah) / dte - (b + 2 * c * t2)) < 1e-9
    dte, Ah = p2_drive.P2Drive.bdf2_history(2.0, 2.0, 4.0, 1.0)
    assert abs(dte - 4.0 / 3.0) < 1e-15 and abs(Ah - (16.0 - 1.0) / 3.0) < 1e-15


def test_backward_euler_keeps_the_construction_operators():
    """dte None (SB_EDDY_BE=1) hands eddy_solve the very matrices the caller
    built — bit-for-bit the pre-BDF2 march."""
    m = _Model(2 * math.pi, None)
    drv = m.drive(1e-4)
    Msd, Sdt, dt = drv.eddy_ops(None)
    assert Msd is drv.Msd and Sdt is drv.Sdt and dt == drv.dt
    Msd2, Sdt2, dt2 = drv.eddy_ops(2e-4 / 3.0)
    assert dt2 == 2e-4 / 3.0
    np.testing.assert_allclose(Msd2.toarray(), (m.Msig / dt2).toarray())
    np.testing.assert_allclose(Sdt2, m.S * dt2)
