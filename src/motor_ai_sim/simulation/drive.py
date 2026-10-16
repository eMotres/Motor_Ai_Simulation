"""The terminal side of the sliding-band transient: excitation, dq transforms
and the line-to-line phase circuit.

None of this touches the mesh or the field — it is algebra on three phase
quantities — yet it lived as eight closures inside ``fem_transient_sliding_band``
and was written TWICE, once per element order, because the P2 branch returns
before the P1 code is reached.  Two copies of a circuit is how the two orders
came to disagree: the Park constant, the floating-neutral formulation and the
Aitken anchor all had to be fixed in two places, and more than once only one of
them got the fix.

Nothing here is a new model.  Every expression is the one the solver already
ran, moved verbatim so that both orders provably share it and so the algebra can
be exercised without a 20-minute FEM solve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# 2*pi/3 as a literal, exactly as the two Park copies carried it.  Writing it
# out (rather than computing it) is deliberate: the transforms are compared
# against each other bit-for-bit, and a recomputed constant is a way for the two
# element orders to drift apart in the last ulp.
_TWO_THIRDS_PI = 2.094395102393195


@dataclass(frozen=True)
class Excitation:
    """Imposed three-phase excitation as a function of the ROTOR angle.

    Both drives live in the same electrical frame — ``v_delta_deg`` is directly
    comparable to ``gamma_deg`` — because both add the SAME per-topology
    auto-calibrated d-axis offset (``daxis_deg``), so gamma=0 is the true q-axis
    on either drive.
    """

    pole_pairs: int
    daxis_deg: float
    i_peak: float
    gamma_deg: float
    v_peak: float = 0.0
    v_delta_deg: float = 0.0

    def currents(self, rotor_angle_deg: float) -> Dict[str, float]:
        Ipk = self.i_peak
        # d-axis phase offset so γ=0 = q-axis — per-topology auto-calibrated value
        # (daxis_eff) shared by the current & voltage excitation in this solve.
        te = math.radians(rotor_angle_deg * self.pole_pairs + self.gamma_deg
                          + self.daxis_deg)
        return {'A': Ipk * math.cos(te),
                'B': Ipk * math.cos(te - 2 * math.pi / 3),
                'C': Ipk * math.cos(te + 2 * math.pi / 3)}

    def voltages(self, rotor_angle_deg: float) -> Dict[str, float]:
        vpk = self.v_peak
        te = math.radians(rotor_angle_deg * self.pole_pairs + self.v_delta_deg
                          + self.daxis_deg)
        return {'A': vpk * math.cos(te),
                'B': vpk * math.cos(te - 2 * math.pi / 3),
                'C': vpk * math.cos(te + 2 * math.pi / 3)}


def park(xa: float, xb: float, xc: float, th: float) -> Tuple[float, float]:
    """abc -> dq, amplitude-invariant (2/3) convention."""
    return ((2.0 / 3.0) * (xa * math.cos(th)
                           + xb * math.cos(th - _TWO_THIRDS_PI)
                           + xc * math.cos(th + _TWO_THIRDS_PI)),
            -(2.0 / 3.0) * (xa * math.sin(th)
                            + xb * math.sin(th - _TWO_THIRDS_PI)
                            + xc * math.sin(th + _TWO_THIRDS_PI)))


def inverse_park(d: float, q: float, th: float) -> Tuple[float, float, float]:
    """dq -> abc, the inverse of :func:`park`."""
    return (d * math.cos(th) - q * math.sin(th),
            d * math.cos(th - _TWO_THIRDS_PI) - q * math.sin(th - _TWO_THIRDS_PI),
            d * math.cos(th + _TWO_THIRDS_PI) - q * math.sin(th + _TWO_THIRDS_PI))


def circuit_residual_ll(psi: Sequence[float], iA: float, iB: float,
                        iv_prev: Dict[str, float], psi_prev: Dict[str, float],
                        Vt: Dict[str, float], dtk: float,
                        r_phase: float) -> np.ndarray:
    """Line-to-line Crank–Nicolson circuit residual [V_AB, V_BC].

    LINE-TO-LINE, not per phase, and that is physics rather than taste: a real
    FOC inverter drives an ISOLATED-neutral machine, so the zero-sequence
    back-EMF (triplen harmonics, large on this concentrated winding) falls on the
    floating neutral and drives NO current.  Phase-voltage equations pin the
    machine neutral to the source's and short that EMF through the tiny
    zero-sequence inductance — measured h3 ~43 % of I1, THD_I ~110 %.  The
    differences kill the zero sequence exactly, as the physical neutral does.
    """
    iC = -iA - iB
    return np.array([
        (Vt['A'] - Vt['B'])
        - 0.5 * r_phase * (iA - iB + iv_prev['A'] - iv_prev['B'])
        - ((psi[0] - psi[1]) - (psi_prev['A'] - psi_prev['B'])) / dtk,
        (Vt['B'] - Vt['C'])
        - 0.5 * r_phase * (iB - iC + iv_prev['B'] - iv_prev['C'])
        - ((psi[1] - psi[2]) - (psi_prev['B'] - psi_prev['C'])) / dtk])


def circuit_jacobian_ll(qa: Sequence[float], qb: Sequence[float], dtk: float,
                        r_phase: float) -> np.ndarray:
    """d[r_AB, r_BC]/d[i_A, i_B] from the dpsi/di columns qa, qb.
    i_C = -i_A - i_B, so d(i_B-i_C)/di_A = 1 and d/di_B = 2."""
    return np.array([
        [0.5 * r_phase + (qa[0] - qa[1]) / dtk,
         -0.5 * r_phase + (qb[0] - qb[1]) / dtk],
        [0.5 * r_phase + (qa[1] - qa[2]) / dtk,
         1.0 * r_phase + (qb[1] - qb[2]) / dtk]])


def aitken_flux_anchor(samples: List[Tuple[float, float]]
                       ) -> Tuple[Optional[Dict[str, float]], float, float]:
    """Delta^2-extrapolate the period-boundary flux to its steady-state limit.

    Takes the (psiA, psiB) samples collected at period ends and returns
    ``(new_psi_prev, corr, drift)``; ``new_psi_prev`` is None when the anchor
    must be SKIPPED.

    Guarded anchor: once the boundary drift is below noise the Δ² quotient
    divides noise by noise and the "correction" EXPLODES — skip when already
    converged (drift < 0.05 % of the PM flux) or when the extrapolation is
    unstable (|corr| ≫ drift).  Better to keep marching than to kick a converged
    orbit right before the reported window.

    MEASURED (scripts/_filter_ablation.py, 2026-07-29, the p2_voltage recipe:
    7.0 V pk, +10°, 12 steps/period, 30 mm 12s14p):

        variant                          I_A_peak    T_avg     T_ripple   i_dc
        baseline (guards on)             71.756 A    0.24005   1.2404 %   0 A
        guards REMOVED (always anchor)   +1.03 %     +0.38 %   +275.36 %  0.424 A
        anchor DISABLED (never anchor)    identical to baseline, every digit

    Two findings, both worth keeping in view:

    * The guards are LOAD-BEARING.  Force the extrapolation through on every
      period boundary and the reported ripple nearly quadruples and the settled
      orbit picks up a 0.42 A DC current — i.e. the Δ² correction, applied when
      it should not be, is exactly the "kick a converged orbit" failure the
      thresholds exist to prevent.  They stay.
    * On THIS machine the anchor never actually fires: guards-on and
      anchor-disabled agree to every printed digit, so every attempt is being
      skipped.  The anchor is a settling accelerator that is currently a no-op
      here — kept because it is a capability for machines that settle slowly,
      not because it is doing anything for this one.  So that this no longer
      needs an ablation to discover, the solver counts the skips and reports
      them (``v_anchor_attempts`` / ``v_anchor_applied`` in the transient dict).
    """
    q0, q1, q2 = samples[-3:]
    new: Dict[str, float] = {}
    for ci, ky in enumerate(('A', 'B')):
        x0, x1, x2 = q0[ci], q1[ci], q2[ci]
        d1 = x1 - x0; d2 = x2 - x1; dd = d2 - d1
        new[ky] = (x2 - d2 * d2 / dd) if abs(dd) > 1e-15 else x2
    new['C'] = -(new['A'] + new['B'])
    corr = max(abs(new['A'] - q2[0]), abs(new['B'] - q2[1]))
    drift = max(abs(q2[0] - q1[0]), abs(q2[1] - q1[1]))
    flux_scale = max(abs(q2[0]), abs(q2[1]), 1e-6)
    if drift < 5e-4 * flux_scale or corr > 5.0 * drift:
        return None, corr, drift
    return new, corr, drift
