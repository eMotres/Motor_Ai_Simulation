"""Periodic-orbit solve of the circuit's DC mode under an imposed voltage.

This replaces the period-mean DC ANCHOR of the PWM drive (B5 / PWM study
2026-09-13; stopped in docs/NO_FILTERS_2026-09-24.md item 5).  The anchor
subtracted the DC it measured over a settling period from the circuit state at
the period's end, with a gain learned from the previous anchor and clamped to
[0.2, 3].  Its model had no free decay in it: it assumed a DC measured over a
period is still all there at the period's end.  On a long-τ_e machine (L155,
τ_e ≈ 27 periods) that is nearly true and the anchor removed a 43 A DC; on a
short-τ_e machine (Ø40, the 30 mm fixture) most of that DC has already decayed
by itself, the anchor over-corrected, and the reported window opened on its own
last correction with 1.1-1.2 A of DC in it however long the settle was.

WHAT IS SOLVED INSTEAD
======================
Sum the solved Crank–Nicolson line-to-line rows over one whole electrical
period and the flux telescopes:

    y(end) − y(start) = Σ D·v·Δt − R·Σ S·ī·Δt                          (1)

with y = (ψ_A − ψ_B, ψ_B − ψ_C) the line-to-line flux linkage, D the
line-to-line difference, S the same on (i_A, i_B) with i_C = −i_A − i_B.  On
the periodic steady state the left side is zero.  For a symmetric bridge
Σ v·Δt = 0 over a whole period (the sinusoid, the synchronous regular-sampled
PWM), so the orbit has zero net DC; in general it has exactly the DC that (1)
allows, and nothing here assumes which.

The steady state is the fixed point y* of the PERIOD MAP of the boundary flux.
For a magnetostatic field that map is R² → R² exactly: the line-to-line flux IS
the circuit state, the field follows from it.  Around the fixed point, with
x = y − y*,

    x(end of n) = M·x(start of n)        drift δ_n = (M − I)·x(start of n)

and the correction that puts the next period's start state on the orbit is the
Newton step

    w = −x(end of n) = M·(I − M)⁻¹·δ_n                                  (2)

in FLUX.  δ_n is the exact flux change over the period (the converged frame's
flux minus the CN state the period started from, no estimator in between).
That makes the period's WINDOW part of the measurement: it must span exactly
one electrical period, or δ carries the orbit's own flux motion over the
excess.  The solver guarantees it — including across the coarse→fine handover
of the mixed PWM settle, whose first fine step used to be one coarse step long
(fem_solver_2d._build_schedule).

M is the Jacobian of the period map, and it is not guessed: it is the
product of the CN step propagators of the linearised circuit along the period
just marched (the variational equation of shooting methods),

    δy_k = (I + R·h_k·S·L_k⁻¹)⁻¹ · (I − R·h_k·S·L_{k−1}⁻¹) · δy_{k−1},   h_k = Δt_k/2

where L_k = D·[∂ψ/∂i_A, ∂ψ/∂i_B] is the INCREMENTAL line-to-line inductance of
frame k — the columns its own Newton already solved for, at its own
saturation state.  No extra solve.  That is what a DC perturbation meets: in
saturated iron the incremental inductance can be several times below the
apparent one the phasor initialiser reports (measured on the 30 mm fixture: a
τ₀ from the phasor's Ld/Lq predicted a decay of 0.28 per period, the machine's
is 0.035, and a correction built on the former over-shot ten-fold).  With the
coupled eddy solve the columns carry the eddy reaction of ONE step (the
transient inductance), and the eddy history's own memory is not in M; the
error that leaves is on the side of predicting a faster decay, i.e. of
under-correcting, and the next boundary's drift measures what is left.

The frame's columns also move the CN current memory with the flux (it enters
the next step through R·Δt/2 only) — :func:`flux_shift_to_state`.

A period whose M is not contractive (spectral radius ≥ 1) is NOT corrected:
a DC mode that grows is not a circuit with a resistance in it, it is a
Jacobian that went wrong, and the period is recorded as refused.

THE MODULATOR'S TURN-ON (mixed coarse/fine settle)
====================================================
The coarse settle marches the modulator's smooth fundamental and lands its
orbit; the chopped waveform then switches on.  The fine orbit is the coarse one
plus the switching ripple's periodic flux, and at the boundary that ripple flux
is not zero — the turn-on leaves a DC of about half the ripple amplitude.  On a
machine whose carrier ripple is a large part of its current (the 30 mm fixture
at 8 steps/carrier: tens of amperes) the first Newton step is then taken from
far out on a saturating machine and lands short.  The offset is predictable
from the modulator ALONE: with Ψ(t) = ∫(v_pwm − v_fund)·dt from the boundary,
the periodic ripple flux is Ψ(t) + c, and the CN period rows make its
trapezoidal period mean — the ripple current's DC — vanish, so

    c = −⟨Ψ⟩                                                            (3)

exactly for a constant inductance and small R·T/L (:func:`handover_flux_offset`,
on the exact per-step volt-seconds of the ideal bridge).  It is applied at the
coarse→fine boundary as the Newton PREDICTOR; the first fine period's drift
measures what it missed (saturation, saliency, dead time) and the Newton step
at its end takes that out.  It is recorded beside the correction.

No correction is applied at the end of the last settling period.  That period
is the solve's VERIFICATION: it runs free from the last corrected state, its
drift and DC are reported, and the reported window follows it without a state
jump in front of it — which is exactly what the anchor did not have.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

__all__ = ["DcOrbitSolve", "ll_flux", "ll_inductance", "flux_shift_to_state",
           "handover_flux_offset"]

# Line-to-line difference on the three phases, and the same on (i_A, i_B) with
# i_C = −i_A − i_B: (i_A − i_B, i_B − i_C) = S·(i_A, i_B).  The circuit rows of
# drive.circuit_residual_ll / circuit_jacobian_ll, written as matrices.
_D = np.array([[1.0, -1.0, 0.0], [0.0, 1.0, -1.0]])
_S = np.array([[1.0, -1.0], [1.0, 2.0]])


def ll_flux(psi: Dict[str, float] | Sequence[float]) -> np.ndarray:
    """(ψ_A − ψ_B, ψ_B − ψ_C) of a phase-flux dict or (A, B, C) triple."""
    if isinstance(psi, dict):
        a, b, c = float(psi['A']), float(psi['B']), float(psi['C'])
    else:
        a, b, c = (float(x) for x in psi)
    return np.array([a - b, b - c])


def ll_inductance(qa: Sequence[float], qb: Sequence[float]) -> np.ndarray:
    """D·[qa qb]: d(line-to-line flux)/d(i_A, i_B) from the phase columns."""
    Q = np.column_stack([np.asarray(qa, float)[:3], np.asarray(qb, float)[:3]])
    return _D @ Q


def flux_shift_to_state(w_ll: np.ndarray, qa: Sequence[float],
                        qb: Sequence[float]):
    """A line-to-line flux shift as a CONSISTENT (ψ, i) circuit-state shift.

    ``qa``/``qb`` are the frame's ∂ψ/∂i_A, ∂ψ/∂i_B phase columns (i_C =
    −i_A − i_B).  Returns ``(dpsi_phase, di_phase)`` with D·dpsi = w exactly and
    di = L_LL⁻¹·w the current that flux carries at this frame's permeability.
    """
    Q = np.column_stack([np.asarray(qa, float)[:3], np.asarray(qb, float)[:3]])
    u = np.linalg.solve(_D @ Q, np.asarray(w_ll, float))
    dpsi = Q @ u
    return ({'A': float(dpsi[0]), 'B': float(dpsi[1]), 'C': float(dpsi[2])},
            {'A': float(u[0]), 'B': float(u[1]), 'C': float(-u[0] - u[1])})


def handover_flux_offset(v_ripple_phase: np.ndarray,
                         dt_steps: Sequence[float]) -> np.ndarray:
    """(3): −⟨Ψ⟩ over one whole period of steps, line-to-line [Wb].

    ``v_ripple_phase`` is (N, 3): per step, the step-mean chopped pole voltage
    minus the step-mean fundamental [V]; ``dt_steps`` the N step lengths.  Ψ
    starts at 0 on the boundary and is the CN-exact running volt-seconds; its
    mean is the trapezoid over the steps, as the CN rows weight the current.
    """
    v = np.asarray(v_ripple_phase, float)
    dts = np.asarray(dt_steps, float)
    if v.ndim != 2 or v.shape[1] != 3 or v.shape[0] != dts.size or dts.size == 0:
        raise ValueError("handover_flux_offset: need (N, 3) volts and N steps, "
                         "got %r and %r" % (v.shape, dts.shape))
    if not (np.all(np.isfinite(v)) and np.all(np.isfinite(dts))
            and np.all(dts > 0.0)):
        raise ValueError("handover_flux_offset: non-finite volts or a "
                         "non-positive step")
    vll = v @ _D.T                                    # (N, 2)
    psi = np.vstack([np.zeros((1, 2)), np.cumsum(vll * dts[:, None], axis=0)])
    mean = (0.5 * (psi[1:] + psi[:-1]) * dts[:, None]).sum(axis=0) / dts.sum()
    return -mean


def _finite_2x2(L: np.ndarray, what: str) -> np.ndarray:
    L = np.asarray(L, float)
    if L.shape != (2, 2) or not np.all(np.isfinite(L)):
        raise ValueError("DcOrbitSolve: %s is not a finite 2x2 matrix: %r"
                         % (what, L))
    return L


@dataclass
class DcOrbitSolve:
    """Newton shooting on the period map of the line-to-line boundary flux.

    Per solved frame call :meth:`frame` with its incremental inductance and
    step; at the first frame of a whole settling period call
    :meth:`period_start` (before the frame) with the CN flux state the period
    starts from, and at its last frame :meth:`period_end` (after the frame)
    with the converged flux.  ``period_end`` returns the flux correction to
    apply to the CN state before the next period, or None.
    """

    R_phase: float
    # False: measure and report only, never correct (a reference run)
    correcting: bool = True
    records: List[Dict[str, Any]] = field(default_factory=list)
    corrections: int = 0
    refused: int = 0
    _q: Optional[np.ndarray] = field(default=None, init=False, repr=False)
    _M: Optional[np.ndarray] = field(default=None, init=False, repr=False)
    _L_prev: Optional[np.ndarray] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        R = float(self.R_phase)
        if not (np.isfinite(R) and R > 0.0):
            raise ValueError("DcOrbitSolve: R_phase=%r ohm — the DC mode of a "
                             "lossless circuit never decays and has no orbit "
                             "to solve for" % self.R_phase)

    def frame(self, L_ll: np.ndarray, dt_s: float) -> None:
        """Advance the period's Jacobian by one CN step (the variational row)."""
        L = _finite_2x2(L_ll, "the frame's incremental inductance")
        dt = float(dt_s)
        if not (np.isfinite(dt) and dt > 0.0):
            raise ValueError("DcOrbitSolve: step %r s" % dt_s)
        Lp = L if self._L_prev is None else self._L_prev
        if self._M is not None:
            h = 0.5 * float(self.R_phase) * dt
            RSh = h * _S
            A = np.eye(2) + RSh @ np.linalg.inv(L)
            B = np.eye(2) - RSh @ np.linalg.inv(Lp)
            self._M = np.linalg.solve(A, B @ self._M)
        self._L_prev = L

    def period_start(self, q_ll: np.ndarray) -> None:
        q = np.asarray(q_ll, float).reshape(2)
        if not np.all(np.isfinite(q)):
            raise ValueError("DcOrbitSolve: non-finite start flux %r" % (q,))
        self._q = q
        self._M = np.eye(2)

    def period_end(self, p_ll: np.ndarray, *, tag: str, correct: bool,
                   frame: int = -1, dc_phase_A: Optional[Sequence[float]] = None
                   ) -> Optional[np.ndarray]:
        if self._q is None or self._M is None:
            raise RuntimeError("DcOrbitSolve.period_end without period_start")
        p = np.asarray(p_ll, float).reshape(2)
        if not np.all(np.isfinite(p)):
            raise ValueError("DcOrbitSolve: non-finite end flux %r" % (p,))
        delta = p - self._q
        M = self._M
        self._q = None
        self._M = None
        eig = np.linalg.eigvals(M)
        rho = float(np.max(np.abs(eig)))
        w: Optional[np.ndarray] = None
        verdict = ("free (verification)" if self.correcting
                   else "measured only (corrections off)")
        if correct and self.correcting:
            if np.all(np.isfinite(M)) and rho < 1.0:
                # (2): the next start state onto the orbit.
                w = M @ np.linalg.solve(np.eye(2) - M, delta)
                self.corrections += 1
                verdict = "corrected"
            else:
                self.refused += 1
                verdict = ("REFUSED: period Jacobian not contractive "
                           "(spectral radius %.6g)" % rho)
        self.records.append({
            "frame": int(frame), "resolution": str(tag),
            "drift_Wb": [float(delta[0]), float(delta[1])],
            "dc_A": (None if dc_phase_A is None
                     else [float(x) for x in dc_phase_A]),
            "correction_Wb": (None if w is None
                              else [float(w[0]), float(w[1])]),
            "verdict": verdict,
            # eigenvalues of the period Jacobian, as [re, im] pairs: the DC
            # mode's decay per period at this operating point
            "decay_per_period": [[float(np.real(e)), float(np.imag(e))]
                                 for e in eig],
        })
        return w

    def note_handover(self, offset_ll: np.ndarray) -> None:
        """Record the turn-on predictor applied at the last period's end."""
        w = np.asarray(offset_ll, float).reshape(2)
        self.records[-1]["handover_prediction_Wb"] = [float(w[0]),
                                                      float(w[1])]

    def describe(self) -> Dict[str, Any]:
        return {
            "method": ("Newton shooting on the line-to-line DC mode: exact "
                       "whole-period flux drift, period Jacobian from the CN "
                       "variational product of each frame's incremental "
                       "inductance, correction M(I-M)^-1·drift in flux; the "
                       "modulator's turn-on flux predicted from its own "
                       "volt-seconds at the coarse->fine boundary; the "
                       "last settling period runs free and verifies"),
            "correcting": bool(self.correcting),
            "corrections": int(self.corrections),
            "refused": int(self.refused),
            "periods": list(self.records),
        }
