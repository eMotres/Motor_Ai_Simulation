"""The PWM drive's DC-mode periodic-orbit solve (simulation/dc_orbit.py).

A salient, linear, three-phase star machine is marched here with the SAME
line-to-line Crank–Nicolson rows the FEM voltage drive solves
(drive.circuit_residual_ll), so the solve can be judged against the EXACT
periodic orbit — which, for a linear machine, is one 2×2 affine solve.  No FEM:
the algebra is the whole of the question (docs/NO_FILTERS_2026-09-24.md item 5).

What the old period-mean anchor did on a short-τ machine — over-correct and open
the window on its own last correction — is exactly what these cases would show
as a DC left in the reported period.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.dc_orbit import (
    DcOrbitSolve, flux_shift_to_state, handover_flux_offset, ll_flux,
    ll_inductance)
from motor_ai_sim.simulation.drive import circuit_residual_ll

_D = np.array([[1.0, -1.0, 0.0], [0.0, 1.0, -1.0]])
_S = np.array([[1.0, -1.0], [1.0, 2.0]])
_T = (2.0 / 3.0) * np.array([[1.0, -0.5, -0.5],
                             [0.0, math.sqrt(3) / 2, -math.sqrt(3) / 2]])
_TI = np.array([[1.0, 0.0], [-0.5, math.sqrt(3) / 2],
                [-0.5, -math.sqrt(3) / 2]])
_E = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, -1.0]])


class Machine:
    """ψ_abc = Q(θ)·(i_A, i_B) + ψ_pm(θ), Ld ≠ Lq, R per phase; θ electrical."""

    def __init__(self, Ld, Lq, R, psi_m=0.01, V=10.0, delta=0.3,
                 ripple=0.0, carriers=6):
        self.Ld, self.Lq, self.R = Ld, Lq, R
        self.psi_m, self.V, self.delta = psi_m, V, delta
        self.ripple, self.carriers = ripple, carriers

    def Q(self, th):
        L0, L2 = 0.5 * (self.Ld + self.Lq), 0.5 * (self.Ld - self.Lq)
        c, s = math.cos(2 * th), math.sin(2 * th)
        Lab = np.array([[L0 + L2 * c, L2 * s], [L2 * s, L0 - L2 * c]])
        return _TI @ Lab @ _T @ _E

    def psi_pm(self, th):
        return _TI @ np.array([self.psi_m * math.cos(th),
                               self.psi_m * math.sin(th)])

    def v(self, a, b, fine):
        """MEAN phase voltage over the step θ ∈ [a, b] — the volt-seconds per
        second the CN row integrates, as the solver's sources return."""
        d = self.delta
        va = self.V / (b - a) * _TI @ np.array(
            [math.sin(b + d) - math.sin(a + d),
             -(math.cos(b + d) - math.cos(a + d))])
        if fine and self.ripple:
            # a zero-mean chopped term, phase-shifted per leg (Σ v·Δt = 0
            # over the period, as for the synchronous PWM); its step mean by
            # 64 sub-intervals
            x = a + (np.arange(64) + 0.5) * (b - a) / 64
            for j, ph in enumerate((0.0, 2.1, 4.2)):
                va[j] += self.ripple * float(np.mean(np.sign(
                    np.sin(self.carriers * x + ph + 0.1))))
        return va

    def step(self, y_prev, u_prev, th, dt, fine, dth):
        """One CN step over θ ∈ [th − dth, th], returning (y_k, u_k, ψ_abc_k)."""
        Q = self.Q(th)
        L = _D @ Q
        rhs = (_D @ self.v(th - dth, th, fine)) * dt + y_prev \
            - _D @ self.psi_pm(th) - 0.5 * self.R * dt * (_S @ u_prev)
        u = np.linalg.solve(L + 0.5 * self.R * dt * _S, rhs)
        psi = Q @ u + self.psi_pm(th)
        return _D @ psi, u, psi


def march(m, y0, u0, nspp, fine, th0=0.0, sol=None):
    """One period; returns boundary state and the per-frame phase currents.
    ``sol`` is fed every frame's incremental inductance, as the solver does."""
    dth = 2 * math.pi / nspp
    dt = dth / (2 * math.pi * 1000.0)            # f_e = 1 kHz
    y, u = y0.copy(), u0.copy()
    cur, psis = [], []
    for j in range(nspp):
        th = th0 + (j + 1) * dth
        y, u, psi = m.step(y, u, th, dt, fine, dth)
        if sol is not None:
            Q = m.Q(th)
            sol.frame(ll_inductance(Q[:, 0], Q[:, 1]), dt)
        cur.append((u[0], u[1], -u[0] - u[1])); psis.append(psi)
    return y, u, np.asarray(cur), psis, dt


def orbit(m, nspp, fine):
    """The EXACT periodic orbit: the period map on u is affine."""
    def F(u0):
        y0 = _D @ (m.Q(0.0) @ u0 + m.psi_pm(0.0))
        return march(m, y0, u0, nspp, fine)[1]
    b = F(np.zeros(2))
    A = np.column_stack([F(np.eye(2)[i]) - b for i in range(2)])
    return np.linalg.solve(np.eye(2) - A, b)


def trap_dc(cur_prev_last, cur):
    x = np.vstack([cur_prev_last[None, :], cur])
    return 0.5 * (x[1:] + x[:-1]).mean(axis=0)


def run(m, schedule, u_start, *, solve=True, predict=False):
    """March `schedule` (list of (nspp, fine)) then one reported fine period.

    Returns the reported period's trapezoidal DC per phase, the start state's
    distance from the exact fine orbit, and the solver.
    """
    sol = DcOrbitSolve(R_phase=m.R)
    u = np.asarray(u_start, float)
    y = _D @ (m.Q(0.0) @ u + m.psi_pm(0.0))
    Q0 = m.Q(0.0)
    sol.frame(ll_inductance(Q0[:, 0], Q0[:, 1]), 1e-6)   # the frame before
    last = np.array([u[0], u[1], -u[0] - u[1]])
    for n, (nspp, fine) in enumerate(schedule):
        sol.period_start(y)
        y, u, cur, _ps, _dt = march(m, y, u, nspp, fine, sol=sol)
        last = cur[-1]
        correct = solve and n < len(schedule) - 1
        w = sol.period_end(y, tag="fine" if fine else "coarse",
                           correct=correct)
        nxt = schedule[n + 1] if n + 1 < len(schedule) else None
        if predict and correct and not fine and nxt is not None and nxt[1]:
            nf = nxt[0]
            hf = 2 * math.pi / nf
            vr = np.array([m.v(j * hf, (j + 1) * hf, True)
                           - m.v(j * hf, (j + 1) * hf, False)
                           for j in range(nf)])
            wh = handover_flux_offset(vr, [1.0 / (1000.0 * nf)] * nf)
            sol.note_handover(wh)
            w = wh if w is None else w + wh
        if w is not None:
            Q = m.Q(0.0)
            dpsi, di = flux_shift_to_state(w, Q[:, 0], Q[:, 1])
            y = y + ll_flux(dpsi)
            u = u + np.array([di['A'], di['B']])
    u_star = orbit(m, schedule[-1][0], True)
    err = float(np.max(np.abs(u - u_star)))
    _y, _u, cur, _ps, _dt = march(m, y, u, schedule[-1][0], True)
    return trap_dc(last, cur), err, sol


def _schedule(n_coarse, n_fine, c=24, f=96):
    return [(c, False)] * n_coarse + [(f, True)] * n_fine


# ── the exact identity the solve rests on ───────────────────────────────────
def test_the_period_drift_is_exactly_the_CN_rows_summed():
    """y(end) − y(start) = Σ D·v·Δt − R·Σ S·ī·Δt, to round-off, off the orbit."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=0.05, ripple=3.0)
    u0 = np.array([30.0, -5.0])
    y0 = _D @ (m.Q(0.0) @ u0 + m.psi_pm(0.0))
    y1, _u, cur, _ps, dt = march(m, y0, u0, 96, True)
    ib = np.vstack([np.r_[u0, -u0.sum()][None, :], cur])
    ibar = 0.5 * (ib[1:] + ib[:-1])
    h = 2 * math.pi / 96
    vsum = sum(_D @ m.v(j * h, (j + 1) * h, True) for j in range(96)) * dt
    rsum = m.R * dt * sum(_S @ ibar[j, :2] for j in range(96))
    np.testing.assert_allclose(y1 - y0, vsum - rsum, rtol=0, atol=1e-15)
    # …and the applied volt-seconds of the symmetric bridge are zero, so the
    # orbit has zero net DC.
    np.testing.assert_allclose(vsum, 0.0, atol=1e-12)


def test_the_synthetic_step_is_the_solvers_circuit_row():
    m = Machine(Ld=60e-6, Lq=40e-6, R=0.05, ripple=3.0)
    u0 = np.array([12.0, 4.0]); th = 0.4; dt = 1e-5
    y0 = _D @ (m.Q(0.0) @ u0 + m.psi_pm(0.0))
    psi0 = m.Q(0.0) @ u0 + m.psi_pm(0.0)
    _y, u, psi = m.step(y0, u0, th, dt, True, 0.05)
    v = m.v(th - 0.05, th, True)
    r = circuit_residual_ll(psi, u[0], u[1],
                            {'A': u0[0], 'B': u0[1], 'C': -u0.sum()},
                            dict(zip('ABC', psi0)), dict(zip('ABC', v)), dt,
                            m.R)
    np.testing.assert_allclose(r, 0.0, atol=1e-9)


# ── the solve against the exact orbit ───────────────────────────────────────
@pytest.mark.parametrize("R,label", [(0.40, "short tau (0.3 periods)"),
                                     (0.12, "tau ~1 period"),
                                     (0.004, "long tau (25 periods)")])
def test_the_window_opens_on_the_orbit(R, label):
    """Start 40 A off the orbit; the window's DC is the orbit's (zero)."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=R, ripple=0.0)
    u_off = orbit(m, 96, True) + np.array([40.0, -25.0])
    dc, err, sol = run(m, [(96, True)] * 4, u_off)
    # a linear machine's period map is affine and M is its exact Jacobian:
    # one Newton step lands on the orbit to round-off
    assert np.max(np.abs(dc)) < 1e-9, (label, dc)
    assert err < 1e-9, (label, err)
    assert sol.corrections == 3 and sol.refused == 0


def test_the_period_jacobian_is_the_period_maps_own():
    """M from the variational product of the frames' incremental inductances
    IS the Jacobian of the period map — checked against finite differences of
    the marched map itself, saliency and all."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=0.03, ripple=4.0)
    nspp = 96

    def F(y0):
        # consistent start state for the boundary flux y0 at θ = 0
        L0 = _D @ m.Q(0.0)
        u0 = np.linalg.solve(L0, y0 - _D @ m.psi_pm(0.0))
        return march(m, y0, u0, nspp, True)[0]

    y0 = _D @ (m.Q(0.0) @ np.array([30.0, -10.0]) + m.psi_pm(0.0))
    h = 1e-7
    J = np.column_stack([(F(y0 + h * e) - F(y0 - h * e)) / (2 * h)
                         for e in np.eye(2)])
    sol = DcOrbitSolve(R_phase=m.R)
    Q0 = m.Q(0.0)
    sol.frame(ll_inductance(Q0[:, 0], Q0[:, 1]), 1e-6)
    sol.period_start(y0)
    u0 = np.linalg.solve(_D @ Q0, y0 - _D @ m.psi_pm(0.0))
    march(m, y0, u0, nspp, True, sol=sol)
    np.testing.assert_allclose(sol._M, J, rtol=1e-6, atol=1e-9)


@pytest.mark.parametrize("R", [0.40, 0.12, 0.004])
def test_the_coarse_to_fine_handover_kick_is_solved(R):
    """Coarse sinusoid settle, then the chopped modulator switches on: its
    turn-on DC is taken out by the first fine period's Newton step with the
    M the coarse periods learned, and the free last period verifies."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=R, ripple=4.0)
    u_off = orbit(m, 24, False) + np.array([40.0, -25.0])
    dc, err, sol = run(m, _schedule(4, 2), u_off)
    # the fine orbit's own DC is zero (Σ v·Δt = 0): what is left is round-off
    assert np.max(np.abs(dc)) < 1e-9, dc
    assert err < 1e-9, err
    # no correction after the verification period
    assert sol.records[-1]["correction_Wb"] is None
    assert sol.records[-1]["verdict"] == "free (verification)"


@pytest.mark.parametrize("Ld,Lq", [(50e-6, 50e-6), (60e-6, 40e-6)])
@pytest.mark.parametrize("R,rel", [(0.0005, 1e-3), (0.002, 3e-3),
                                   (0.03, 5e-2)])
def test_the_turn_on_predictor_is_the_ripples_own_flux(Ld, Lq, R, rel):
    """Coarse orbit exactly landed, then the chopped waveform switches on: the
    drift of the first chopped period is the turn-on kick.  The predictor —
    the modulator's volt-seconds alone — removes it to O(R·T/L) (R·T/L =
    0.01, 0.04, 0.6 here), saliency or not; the Newton step at the period's
    end takes the rest (the window tests above)."""
    m = Machine(Ld=Ld, Lq=Lq, R=R, ripple=4.0)
    u0 = orbit(m, 24, False)
    sched = [(24, False), (96, True), (96, True)]
    _dc, _e, s0 = run(m, sched, u0, predict=False)
    _dc, _e, s1 = run(m, sched, u0, predict=True)
    kick = np.max(np.abs(s0.records[1]["drift_Wb"]))
    left = np.max(np.abs(s1.records[1]["drift_Wb"]))
    assert kick > 0
    assert left < rel * kick, (left, kick)
    assert "handover_prediction_Wb" in s1.records[0]


def test_the_predictor_refuses_a_malformed_period():
    with pytest.raises(ValueError):
        handover_flux_offset(np.zeros((4, 2)), [1e-5] * 4)
    with pytest.raises(ValueError):
        handover_flux_offset(np.zeros((4, 3)), [1e-5, 1e-5, 0.0, 1e-5])


def test_without_the_solve_a_long_tau_machine_keeps_its_dc():
    """The reason a solve exists at all: 6 free periods shed nothing at τ ≈ 25."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=0.004, ripple=4.0)
    u_off = orbit(m, 24, False) + np.array([40.0, -25.0])
    dc_free, _e, _s = run(m, _schedule(4, 2), u_off, solve=False)
    dc, _e, _s = run(m, _schedule(4, 2), u_off, solve=True)
    assert np.max(np.abs(dc_free)) > 20.0
    assert np.max(np.abs(dc)) < 1e-9


def test_on_a_short_tau_machine_the_solve_is_no_worse_than_waiting():
    """The anchor's failure: on τ < 1 period it LEFT a DC that free settling
    does not.  The solve must land at least as close to the orbit as the free
    march of the same length."""
    m = Machine(Ld=60e-6, Lq=40e-6, R=0.12, ripple=4.0)
    u_off = orbit(m, 24, False) + np.array([5.0, -3.0])
    dc_free, e_free, _ = run(m, _schedule(1, 2), u_off, solve=False)
    dc, e, _ = run(m, _schedule(1, 2), u_off, solve=True)
    assert np.max(np.abs(dc)) <= np.max(np.abs(dc_free)) + 1e-9
    assert e <= e_free + 1e-9


# ── loud refusals ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [0.0, -0.1, float("nan"), float("inf")])
def test_a_lossless_or_nonsense_resistance_is_refused(bad):
    with pytest.raises(ValueError):
        DcOrbitSolve(R_phase=bad)


@pytest.mark.parametrize("L", [np.eye(3), np.array([[np.nan, 0], [0, 1.0]])])
def test_a_frame_inductance_that_is_not_a_2x2_is_refused(L):
    with pytest.raises(ValueError):
        DcOrbitSolve(R_phase=0.1).frame(L, 1e-5)


def test_a_period_whose_jacobian_grows_is_not_corrected():
    """A negative incremental inductance makes the CN propagator expand: that
    is not a circuit, and the period is recorded as refused — no correction."""
    sol = DcOrbitSolve(R_phase=0.1)
    L = -1e-6 * np.eye(2)
    sol.frame(L, 1e-5)
    sol.period_start(np.zeros(2))
    for _ in range(10):
        sol.frame(L, 1e-5)
    assert sol.period_end(np.ones(2), tag="f", correct=True) is None
    assert sol.refused == 1 and sol.corrections == 0
    assert sol.records[-1]["verdict"].startswith("REFUSED")


def test_period_end_without_a_start_is_a_bug_not_a_zero():
    sol = DcOrbitSolve(R_phase=0.1)
    with pytest.raises(RuntimeError):
        sol.period_end(np.zeros(2), tag="f", correct=True)
