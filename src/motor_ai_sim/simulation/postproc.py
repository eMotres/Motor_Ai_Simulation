"""Post-processing helpers shared by the Simulation route and the optimizer
eval — ONE implementation so derived metrics can never drift between the two
paths (the opt↔sim byte-identity rule).
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np


def voltage_harmonics(d: Dict[str, Any],
                      h_max: Optional[int] = None) -> Dict[str, Any]:
    """Harmonic analysis of the phase voltages of a finished transient dict.

    Returns
      V1_phase_V   — fundamental phase-voltage amplitude [V]
      THD_pct      — phase-to-neutral THD, EVERY harmonic the window resolves
                     (2 .. Nyquist).  No order cap (owner 2026-09-24: no
                     truncation may shape a reported value; this used to stop
                     at the 25th).  ``h_max`` is kept only for a caller that
                     asks for a named band explicitly.
      THD_LL_pct   — line-to-line THD: non-triplen harmonics only.  For a
                     balanced 3-phase set the line-to-line amplitude of
                     harmonic h is 2·sin(h·π/3)·V_h — √3·V_h for non-triplen
                     and 0 for triplen — so the √3 cancels in the ratio and
                     THD_LL is simply RSS(non-triplen V_h) / V1.  This is what
                     a wye-connected FOC drive actually fights.
      V_harm_amp   — per-order amplitude list [V], orders 1..Nyquist

    Harmonic h of the ELECTRICAL frequency lives in DFT bin h·P where
    P = round(n_periods) (the stored window may span several periods).
    Magnitudes are averaged over the 3 phases — identical for a balanced
    machine, so averaging only suppresses numerical asymmetry.  Non-finite
    samples (dψ/dt edge artifacts in older stored runs) are zeroed rather
    than poisoning the sums.

    THD_LL comes from the ACTUAL line-to-line waveforms (V_A−V_B, V_B−V_C,
    V_C−V_A): triplens cancel there physically, and — unlike the non-triplen
    approximation from the phase spectrum — real phase UNBALANCE (e.g. the
    sector-mesh seam bias) shows up honestly.  Falls back to the non-triplen
    phase approximation when a phase series is missing.
    """
    out: Dict[str, Any] = {"V1_phase_V": 0.0, "THD_pct": 0.0,
                           "THD_LL_pct": 0.0, "V1_LL_V": 0.0, "V_harm_amp": []}
    amps = _phase_harmonics(d, ("V_A", "V_B", "V_C"), h_max)
    if not amps:
        return out
    v1 = amps[0]
    out["V1_phase_V"] = round(v1, 2)
    out["V_harm_amp"] = [round(a, 2) for a in amps]
    if v1 > 1e-9:
        hi = np.asarray(amps[1:], dtype=float)
        orders = np.arange(2, len(amps) + 1)
        out["THD_pct"] = round(100.0 * float(np.sqrt(np.sum(hi ** 2))) / v1, 2)
        nt = hi[orders % 3 != 0]
        out["THD_LL_pct"] = round(100.0 * float(np.sqrt(np.sum(nt ** 2))) / v1, 2)
        out["V1_LL_V"] = round(
            (1.0 if str(d.get("star_delta") or "star").lower().startswith("d")
             else math.sqrt(3.0)) * v1, 2)   # exact for the fundamental
    ll = _line_harmonics(d, h_max)
    if ll:
        v1ll = ll[0]
        out["V1_LL_V"] = round(v1ll, 2)
        if v1ll > 1e-9:
            hill = np.asarray(ll[1:], dtype=float)
            out["THD_LL_pct"] = round(
                100.0 * float(np.sqrt(np.sum(hill ** 2))) / v1ll, 2)
    return out


def _line_harmonics(d: Dict[str, Any], h_max: Optional[int]) -> list:
    """Per-order amplitudes of the ACTUAL line-to-line voltages (3-pair
    magnitude average) — None-safe wrapper building A−B/B−C/C−A on the fly."""
    keys = ("V_A", "V_B", "V_C")
    va = d.get(keys[0]) or []
    N = len(va)
    if N < 4:
        return []
    vs = []
    for k in keys:
        v = d.get(k)
        if not (isinstance(v, (list, tuple)) and len(v) == N):
            return []
        vs.append(np.nan_to_num(np.asarray(v, dtype=float),
                                nan=0.0, posinf=0.0, neginf=0.0))
    va_, vb_, vc_ = vs
    # LINE per the terminal connection.  Star: the difference of two windings.
    # Delta: the winding IS the line, minus its zero-sequence part — the
    # triplen EMF drives the circulating current round the closed loop and the
    # three terminal voltages, summing to zero identically, cannot carry it.
    if str(d.get("star_delta") or "star").lower().startswith("d"):
        v0_ = (va_ + vb_ + vc_) / 3.0
        va_, vb_, vc_ = va_ - v0_, vb_ - v0_, vc_ - v0_
        dd = {"n_periods": d.get("n_periods", 1.0),
              "V_A": va_.tolist(), "V_B": vb_.tolist(), "V_C": vc_.tolist()}
        return _phase_harmonics(dd, ("V_A", "V_B", "V_C"), h_max)
    dd = {"n_periods": d.get("n_periods", 1.0),
          "LL_ab": (va_ - vb_).tolist(), "LL_bc": (vb_ - vc_).tolist(),
          "LL_ca": (vc_ - va_).tolist()}
    return _phase_harmonics(dd, ("LL_ab", "LL_bc", "LL_ca"), h_max)


def _phase_harmonics(d: Dict[str, Any], keys, h_max: Optional[int]) -> list:
    """Per-order harmonic amplitudes (3-phase magnitude average) of any balanced
    triple of per-frame series in ``d`` — shared by the voltage and current
    analyses.  Harmonic h of the electrical frequency lives in bin h·n_periods;
    non-finite samples are zeroed (dψ/dt edge artifacts in older stored runs).

    Every order up to Nyquist (h·P ≤ N/2) unless ``h_max`` names a band.
    Peak amplitude 2|X|/N, except the Nyquist bin of an even window — a
    single real cosine — at |X|/N."""
    va = d.get(keys[0]) or []
    N = len(va)
    P = max(1, int(round(float(d.get("n_periods", 1.0) or 1.0))))
    hmax = (N // 2) // P if N else 0
    if h_max is not None:
        hmax = min(int(h_max), hmax)
    if hmax < 1:
        return []
    phases = [np.nan_to_num(np.asarray(d.get(k), dtype=float),
                            nan=0.0, posinf=0.0, neginf=0.0)
              for k in keys
              if isinstance(d.get(k), (list, tuple)) and len(d.get(k)) == N]
    if not phases:
        return []
    n = np.arange(N)
    amps = []
    for h in range(1, hmax + 1):
        w = 2.0 * np.pi * h * P / N
        c, s = np.cos(w * n), np.sin(w * n)
        scale = (1.0 / N) if 2 * h * P == N else (2.0 / N)
        m = 0.0
        for v in phases:
            m += scale * math.hypot(float(v @ c), float(-(v @ s)))
        amps.append(m / len(phases))
    return amps


def _angles_for(d: Dict[str, Any], key: str) -> list:
    """Rotor angles the samples of series ``key`` belong to.

    The winding VOLTAGE is a per-STEP quantity (the Crank–Nicolson row over
    (t_{k-1}, t_k], see fem_solver_2d._step_voltage_series) and sits at the
    step MIDPOINT, which the solver ships as ``V_rotor_angle_deg``; currents,
    flux linkages and torque sit on the frames.  Older results have no
    midpoint angles and fall back to the frame angles."""
    if str(key).startswith("V_"):
        va = d.get("V_rotor_angle_deg")
        if isinstance(va, (list, tuple)) and len(va) == len(d.get(key) or []):
            return list(va)
    return list(d.get("rotor_angle_deg") or [])


def _drive_frame_phasor(d: Dict[str, Any], keys) -> "complex":
    """Positive-sequence fundamental phasor of an abc series, in the frame the
    EXCITATION SOURCES are written in.

    Both sources in ``simulation/drive.py`` write the same thing:

        x_A(θ) = X̂·cos(θ_mech·p + angle + daxis) ,  B/C shifted ∓120°

    so projecting the three phases onto e^{-j(θ_mech·p + shift)} and averaging
    gives a phasor whose magnitude is X̂ and whose argument is
    ``angle + daxis``.  Subtracting the run's own d-axis therefore recovers the
    ARGUMENT THE SOURCE TAKES — γ for a current, δ for a voltage — which is the
    whole point: it is what makes the two directions round-trip.

    THE D-AXIS IS THE RUN'S OWN.  This used to read the module constant
    ``DAXIS_SHIFT_DEG`` (108°), which the solver itself documents as "wrong for
    EVERY topology": the real offset is auto-calibrated per machine and is 60°
    on both 12s/14p and 24s/28p and 120° on 24s/20p.  Every angle this function
    produced was therefore ~48° out on the two commonest topologies — including
    the γ₁ that the ΔP_harm reference run is solved at, so that comparison was
    run at a load angle nobody asked for.  The constant survives only as the
    fallback for a result dict too old to carry ``daxis_deg``.
    """
    ang = _angles_for(d, keys[0])
    N = len(ang)
    if N < 8:
        return 0j
    rpm = float(d.get("rpm", 0.0) or 0.0)
    f_e = float(d.get("f_elec_Hz", 0.0) or 0.0)
    if rpm <= 0.0 or f_e <= 0.0:
        return 0j
    pp = max(1, int(round(f_e * 60.0 / rpm)))
    th = np.radians(np.asarray(ang, dtype=float) * pp)
    S = 0.0 + 0.0j
    used = 0
    for key, off in zip(keys, (0.0, -2.0 * np.pi / 3.0, 2.0 * np.pi / 3.0)):
        v = d.get(key)
        if not (isinstance(v, (list, tuple)) and len(v) == N):
            continue
        v = np.nan_to_num(np.asarray(v, dtype=float),
                          nan=0.0, posinf=0.0, neginf=0.0)
        S += (2.0 / N) * complex(v @ np.exp(-1j * (th + off)))
        used += 1
    return S / max(used, 1)


def _daxis_of(d: Dict[str, Any]) -> float:
    from motor_ai_sim.simulation.fem_solver_2d import DAXIS_SHIFT_DEG
    v = d.get("daxis_deg")
    try:
        return float(v) if v is not None else float(DAXIS_SHIFT_DEG)
    except (TypeError, ValueError):
        return float(DAXIS_SHIFT_DEG)


def _wrap180(a: float) -> float:
    a = a % 360.0
    return a - 360.0 if a > 180.0 else a


def fundamental_voltage(d: Dict[str, Any]) -> Dict[str, Any]:
    """The SEED for an imposed-voltage run, extracted from a finished one.

    Returns ``(V1_seed_peak_V, V1_seed_delta_deg)`` — the fundamental phasor of
    the SOLVED terminal phase voltage V = R·i + dψ/dt, expressed in exactly the
    ``(v_phase_peak, v_delta_deg)`` coordinates that ``drive="voltage"`` and
    ``drive="pwm_voltage"`` consume.  Feeding the two numbers back verbatim
    re-runs the same operating point with the voltage as the input instead of
    the answer.

    This is the mirror of :func:`fundamental_current`, which goes the other way
    (a voltage run's fundamental current, for the ΔP_harm reference).  The two
    share one frame helper so they cannot drift apart — the round trip is only
    a round trip if both directions agree about where zero is.

    IT IS A SEED, NOT AN IDENTITY.  The machine is nonlinear: driving this
    voltage lands on a slightly different current than the run it came from,
    because Ld/Lq at the new operating point are not the Ld/Lq that produced
    it, and near-zero R makes the current solution stiff in V.  MEASURED on
    ciano14_40_new at its rated point (40.659 A rms, γ = 10°, 13 000 rpm, 240
    steps, eddy on) — current-drive run → seed → sinusoid voltage run:

        T_avg   0.59010 → 0.58906 N·m   −0.18 %
        I₁      40.659  → 40.594  A rms −0.16 %
        γ₁      10.00   → 9.89    °el   −0.11° absolute
        P_cu    62.4    → 62.3    W     −0.16 %

    The extraction itself is idempotent: the voltage run reports back
    (10.1126 V, 18.870°) against the (10.1122 V, 18.867°) it was handed.
    """
    out = {"V1_seed_peak_V": 0.0, "V1_seed_delta_deg": 0.0}
    S = _drive_frame_phasor(d, ("V_A", "V_B", "V_C"))
    if S == 0j:
        return out
    out["V1_seed_peak_V"] = round(abs(S), 4)
    out["V1_seed_delta_deg"] = round(
        _wrap180(math.degrees(np.angle(S)) - _daxis_of(d)), 3)
    return out


def fundamental_current(d: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the fundamental current PHASOR of a finished transient dict in
    the solver's own (I_phase_rms, γ) coordinates — i.e. the current-drive
    settings that would reproduce this run's fundamental current exactly.

    Used by the voltage-drive ΔP_harm reference: comparing a voltage-drive run
    against a current-drive run AT THE SAME FUNDAMENTAL isolates the watt cost
    of the parasitic harmonic currents (near zero R the current solution is
    ill-conditioned in V, so matching V instead would compare different
    operating points).

    Solver conventions this inverts (see _currents in fem_solver_2d):
      i_A(k) = Î·cos(θe(k)),  θe = rotor_deg·pp + γ + DAXIS_SHIFT_DEG [°]
      Î (stored I_A series) is the BRANCH amplitude = I_phase_rms·√2/n_parallel.
    """
    out = {"I1_phase_rms_A": 0.0, "gamma1_deg": 0.0}
    S = _drive_frame_phasor(d, ("I_A", "I_B", "I_C"))
    if S == 0j:
        return out
    npar = max(1, int(d.get("n_parallel", 1) or 1))
    out["I1_phase_rms_A"] = round(abs(S) * npar / math.sqrt(2.0), 3)
    out["gamma1_deg"] = round(
        _wrap180(math.degrees(np.angle(S)) - _daxis_of(d)), 2)
    return out


def complex_fundamental(d: Dict[str, Any], key: str) -> complex:
    """Complex fundamental phasor of a per-frame series in the ROTOR frame
    (projection onto e^{-j·θe}, θe = rotor_deg·pp).  Runs that share the same
    start angle (θ=0, the solver convention) return frame-comparable phasors,
    so e.g. Ê₁ of a no-load run can be subtracted from V̂₁ of a loaded run —
    the basis of the analytic L̂ estimate for ΔP_harm screening."""
    v = d.get(key) or []
    ang = _angles_for(d, key)
    N = len(v)
    if N < 4 or len(ang) != N:
        return 0j
    rpm = float(d.get("rpm", 0.0) or 0.0)
    f_e = float(d.get("f_elec_Hz", 0.0) or 0.0)
    if rpm <= 0.0 or f_e <= 0.0:
        return 0j
    pp = max(1, int(round(f_e * 60.0 / rpm)))
    th = np.radians(np.asarray(ang, dtype=float) * pp)
    vv = np.nan_to_num(np.asarray(v, dtype=float),
                       nan=0.0, posinf=0.0, neginf=0.0)
    return (2.0 / N) * complex(vv @ np.exp(-1j * th))


def current_harmonics(d: Dict[str, Any],
                      h_max: Optional[int] = None) -> Dict[str, Any]:
    """Harmonic analysis of the phase CURRENTS of a finished transient dict.

    In current drive the currents are imposed sinusoids (THD_I ≈ 0 confirms a
    clean drive); in VOLTAGE drive they are the machine's own response, so
    THD_I is the real parasitic harmonic-current content a distorted back-EMF
    forces through the winding (the CIANO spec's Step-3 quantity).
    """
    out: Dict[str, Any] = {"I1_A": 0.0, "THD_I_pct": 0.0, "I_harm_amp": []}
    amps = _phase_harmonics(d, ("I_A", "I_B", "I_C"), h_max)
    if not amps:
        return out
    i1 = amps[0]
    out["I1_A"] = round(i1, 3)
    out["I_harm_amp"] = [round(a, 3) for a in amps]
    if i1 > 1e-9:
        hi = np.asarray(amps[1:], dtype=float)
        out["THD_I_pct"] = round(100.0 * float(np.sqrt(np.sum(hi ** 2))) / i1, 2)
    return out
