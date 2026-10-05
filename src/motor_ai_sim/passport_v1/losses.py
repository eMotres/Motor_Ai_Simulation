"""The loss trajectory (spec §4) and the analytic mechanical losses (§5.6).

The loss grid is a control TRAJECTORY, not a general loss map [P10]: every
point stores its actual (i_d, i_q, T, magnet state) and the operating angle
it was solved at (MTPA below base speed, fixed-current field weakening above,
at the trajectory bus).  Interpolation [G11]: per loss group, between the two
neighbouring speed points of a current row, P = a·n^k with k fitted on those
two points (a local interpolant, never a law through FW, never extrapolated
past the grid's speed range); linear in I between the current rows; DC copper
is not interpolated (analytic I²·R(T) of the solved winding).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

#: Loss groups interpolated separately (all W).  P_cu_dc is analytic.
GROUPS = ("P_cu_ac_W", "P_fe_stator_W", "P_fe_rotor_W", "P_mag_W", "P_shaft_W",
          "P_sleeve_W")


def _pow_interp(n: float, na: float, Pa: float, nb: float, Pb: float) -> Tuple[float, Optional[float]]:
    """P(n) between (na, Pa) and (nb, Pb) by P = a·n^k (positive-bounded);
    linear when either end is not positive (a noise-level group)."""
    if Pa > 0 and Pb > 0 and na > 0 and nb > 0 and na != nb:
        k = math.log(Pb / Pa) / math.log(nb / na)
        return Pa * (n / na) ** k, k
    t = (n - na) / (nb - na) if nb != na else 0.0
    return max(0.0, Pa + t * (Pb - Pa)), None


def interp_row(n: float, row: Sequence[Mapping[str, Any]], group: str) -> Tuple[Optional[float], Any]:
    """One current row (points sorted by rpm): the group at speed n."""
    pts = sorted([p for p in row if p.get(group) is not None], key=lambda p: p["rpm"])
    if not pts:
        return None, "no points"
    if n < pts[0]["rpm"] - 1e-9 or n > pts[-1]["rpm"] + 1e-9:
        return None, "outside the row's speed range (never extrapolated)"
    for a, b in zip(pts[:-1], pts[1:]):
        if a["rpm"] - 1e-9 <= n <= b["rpm"] + 1e-9:
            v, k = _pow_interp(n, a["rpm"], float(a[group]), b["rpm"], float(b[group]))
            return v, {"k": k, "between_rpm": [a["rpm"], b["rpm"]]}
    if len(pts) == 1 and abs(n - pts[0]["rpm"]) < 1e-9:
        return float(pts[0][group]), {"k": None}
    return None, "no bracketing pair"


def interp_loss(n: float, I: float, rows: Mapping[float, Sequence[Mapping[str, Any]]],
                R_dc_hot: float) -> Dict[str, Any]:
    """Every group at (n, I): power law in n on each bracketing current row,
    linear in I between the rows.  DC copper analytic: 3·I²·R (star phase)."""
    Is = sorted(rows)
    out: Dict[str, Any] = {"n": n, "I": I, "groups": {}, "notes": []}
    if I < Is[0] - 1e-9 or I > Is[-1] + 1e-9:
        out["notes"].append("current outside the grid rows — refused")
        out["P_total_W"] = None
        return out
    lo = max(x for x in Is if x <= I + 1e-9)
    hi = min(x for x in Is if x >= I - 1e-9)
    tot = 3.0 * I * I * R_dc_hot
    out["groups"]["P_cu_dc_W"] = tot
    for g in GROUPS:
        va, ia = interp_row(n, rows[lo], g)
        vb, ib = interp_row(n, rows[hi], g)
        if va is None or vb is None:
            out["groups"][g] = None
            out["notes"].append(f"{g}: {ia if va is None else ib}")
            continue
        v = va if hi == lo else va + (I - lo) / (hi - lo) * (vb - va)
        out["groups"][g] = v
        tot += v
    out["P_total_W"] = (None if any(out["groups"][g] is None for g in GROUPS) else tot)
    return out


def mech_losses(*, rpm: float, bearings: Mapping[str, Any], geometry: Mapping[str, Any],
                temp_c: Optional[float]) -> Dict[str, Any]:
    """Bearings (SKF frictional moment) + windage (Couette gap + two faces),
    analytic (mech_losses.machine_mech_losses, the solver card's own model)."""
    from motor_ai_sim import mech_losses as ML
    a = dict(bearings)
    r = ML.machine_mech_losses(rpm=float(rpm), assignment=a, geometry=dict(geometry),
                               temp_c=temp_c, resolve_machine=False)
    if r is None:
        return {"P_W": None, "note": "no bearings named — mechanical loss UNKNOWN (never 0)"}
    blk = ML.summary_block(r)
    Pb = sum(float(b.get("P_W") or 0.0) for b in blk.get("bearings") or [])
    Pw = float((blk.get("windage") or {}).get("P_W") or 0.0)
    return {"P_W": Pb + Pw, "P_bearings_W": Pb, "P_windage_W": Pw,
            "bearing_temp_c": r.get("bearing_temp_c"),
            "bearing_temp_source": r.get("bearing_temp_source"),
            "speed_limit_rpm": min([float(b.get("limit_rpm")) for b in blk.get("bearings") or []
                                    if b.get("limit_rpm")] or [float("nan")]),
            "summary": blk}


def efficiency_shaft(T_em: float, rpm: float, P_loss_em: float, P_mech: float) -> Dict[str, float]:
    """One efficiency, at the shaft (memory one-efficiency-at-the-shaft; the
    route's convention): P_air = T·w, P_in = P_air + P_loss_em,
    P_shaft = P_air - P_mech, eta = P_shaft / P_in."""
    w = 2.0 * math.pi * float(rpm) / 60.0
    P_air = float(T_em) * w
    P_in = P_air + float(P_loss_em)
    P_sh = P_air - float(P_mech)
    return {"P_airgap_W": P_air, "P_in_W": P_in, "P_shaft_W": P_sh,
            "eta_shaft": (P_sh / P_in) if P_in > 0 else None}
