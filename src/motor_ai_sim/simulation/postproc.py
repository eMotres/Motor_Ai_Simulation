"""Post-processing helpers shared by the Simulation route and the optimizer
eval — ONE implementation so derived metrics can never drift between the two
paths (the opt↔sim byte-identity rule).
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np


def voltage_harmonics(d: Dict[str, Any], h_max: int = 25) -> Dict[str, Any]:
    """Harmonic analysis of the phase voltages of a finished transient dict.

    Returns
      V1_phase_V   — fundamental phase-voltage amplitude [V]
      THD_pct      — phase-to-neutral THD, all harmonics 2..h_max
      THD_LL_pct   — line-to-line THD: non-triplen harmonics only.  For a
                     balanced 3-phase set the line-to-line amplitude of
                     harmonic h is 2·sin(h·π/3)·V_h — √3·V_h for non-triplen
                     and 0 for triplen — so the √3 cancels in the ratio and
                     THD_LL is simply RSS(non-triplen V_h) / V1.  This is what
                     a wye-connected FOC drive actually fights.
      V_harm_amp   — per-order amplitude list [V], orders 1..h_max

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
        out["V1_LL_V"] = round(math.sqrt(3.0) * v1, 2)   # exact for the fundamental
    ll = _line_harmonics(d, h_max)
    if ll:
        v1ll = ll[0]
        out["V1_LL_V"] = round(v1ll, 2)
        if v1ll > 1e-9:
            hill = np.asarray(ll[1:], dtype=float)
            out["THD_LL_pct"] = round(
                100.0 * float(np.sqrt(np.sum(hill ** 2))) / v1ll, 2)
    return out


def _line_harmonics(d: Dict[str, Any], h_max: int) -> list:
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
    dd = {"n_periods": d.get("n_periods", 1.0),
          "LL_ab": (va_ - vb_).tolist(), "LL_bc": (vb_ - vc_).tolist(),
          "LL_ca": (vc_ - va_).tolist()}
    return _phase_harmonics(dd, ("LL_ab", "LL_bc", "LL_ca"), h_max)


def _phase_harmonics(d: Dict[str, Any], keys, h_max: int) -> list:
    """Per-order harmonic amplitudes (3-phase magnitude average) of any balanced
    triple of per-frame series in ``d`` — shared by the voltage and current
    analyses.  Harmonic h of the electrical frequency lives in bin h·n_periods;
    non-finite samples are zeroed (dψ/dt edge artifacts in older stored runs)."""
    va = d.get(keys[0]) or []
    N = len(va)
    P = max(1, int(round(float(d.get("n_periods", 1.0) or 1.0))))
    hmax = min(int(h_max), N // (2 * P) - 1) if N else 0
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
        m = 0.0
        for v in phases:
            m += (2.0 / N) * math.hypot(float(v @ c), float(-(v @ s)))
        amps.append(m / len(phases))
    return amps


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
    ia = d.get("I_A") or []
    ang = d.get("rotor_angle_deg") or []
    N = len(ia)
    if N < 8 or len(ang) != N:
        return out
    rpm = float(d.get("rpm", 0.0) or 0.0)
    f_e = float(d.get("f_elec_Hz", 0.0) or 0.0)
    if rpm <= 0.0 or f_e <= 0.0:
        return out
    pp = max(1, int(round(f_e * 60.0 / rpm)))
    from motor_ai_sim.simulation.fem_solver_2d import DAXIS_SHIFT_DEG  # lazy: heavy module, already loaded in-process
    th = np.radians(np.asarray(ang, dtype=float) * pp)
    S = 0.0 + 0.0j
    for key, off in (("I_A", 0.0), ("I_B", -2.0 * np.pi / 3.0),
                     ("I_C", 2.0 * np.pi / 3.0)):
        v = d.get(key)
        if not (isinstance(v, (list, tuple)) and len(v) == N):
            continue
        v = np.nan_to_num(np.asarray(v, dtype=float),
                          nan=0.0, posinf=0.0, neginf=0.0)
        S += (2.0 / N) * complex(v @ np.exp(-1j * (th + off)))
    S /= 3.0
    i1_branch_pk = abs(S)
    npar = max(1, int(d.get("n_parallel", 1) or 1))
    g1 = (math.degrees(np.angle(S)) - DAXIS_SHIFT_DEG) % 360.0
    if g1 > 180.0:
        g1 -= 360.0
    out["I1_phase_rms_A"] = round(i1_branch_pk * npar / math.sqrt(2.0), 3)
    out["gamma1_deg"] = round(g1, 2)
    return out


def complex_fundamental(d: Dict[str, Any], key: str) -> complex:
    """Complex fundamental phasor of a per-frame series in the ROTOR frame
    (projection onto e^{-j·θe}, θe = rotor_deg·pp).  Runs that share the same
    start angle (θ=0, the solver convention) return frame-comparable phasors,
    so e.g. Ê₁ of a no-load run can be subtracted from V̂₁ of a loaded run —
    the basis of the analytic L̂ estimate for ΔP_harm screening."""
    v = d.get(key) or []
    ang = d.get("rotor_angle_deg") or []
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


def current_harmonics(d: Dict[str, Any], h_max: int = 25) -> Dict[str, Any]:
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
