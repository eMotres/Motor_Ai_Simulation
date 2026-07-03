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
    """
    out: Dict[str, Any] = {"V1_phase_V": 0.0, "THD_pct": 0.0,
                           "THD_LL_pct": 0.0, "V_harm_amp": []}
    va = d.get("V_A") or []
    N = len(va)
    P = max(1, int(round(float(d.get("n_periods", 1.0) or 1.0))))
    hmax = min(int(h_max), N // (2 * P) - 1) if N else 0
    if hmax < 1:
        return out
    phases = [np.nan_to_num(np.asarray(d.get(k), dtype=float),
                            nan=0.0, posinf=0.0, neginf=0.0)
              for k in ("V_A", "V_B", "V_C")
              if isinstance(d.get(k), (list, tuple)) and len(d.get(k)) == N]
    if not phases:
        return out
    n = np.arange(N)
    amps = []
    for h in range(1, hmax + 1):
        w = 2.0 * np.pi * h * P / N
        c, s = np.cos(w * n), np.sin(w * n)
        m = 0.0
        for v in phases:
            m += (2.0 / N) * math.hypot(float(v @ c), float(-(v @ s)))
        amps.append(m / len(phases))
    v1 = amps[0]
    out["V1_phase_V"] = round(v1, 2)
    out["V_harm_amp"] = [round(a, 2) for a in amps]
    if v1 > 1e-9:
        hi = np.asarray(amps[1:], dtype=float)
        orders = np.arange(2, hmax + 1)
        out["THD_pct"] = round(100.0 * float(np.sqrt(np.sum(hi ** 2))) / v1, 2)
        nt = hi[orders % 3 != 0]
        out["THD_LL_pct"] = round(100.0 * float(np.sqrt(np.sum(nt ** 2))) / v1, 2)
    return out
