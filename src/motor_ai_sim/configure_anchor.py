"""Configure's lumped thermal network ANCHORED to a duty's coupled FEM record (owner 2026-10-19:
one model, anchored to the server's FEM truth).

Where a configuration has no calibrated S1 network for the cooling in use (still air / robotics,
typed air, anything the S1 model was not fitted to), Configure's temperatures used the bare lumped
estimate - on CIANO28 85 L15 rated in still air 202 °C winding where the duty's coupled record says
74 °C.  Here the duty's own stored coupled record (``duty_results``: the coupled loop's thermal
solve at the duty's point) fixes the network's three paths so that, fed the record's losses, it
returns the record's temperatures at the record's ambient:

    G_housing = P_total / (T_housing - T_ambient)      housing to ambient [W/K]
    R_winding = (T_winding - T_housing) / P_cu          winding hot spot over the housing [K/W]
    R_gap     = (T_magnet - T_housing) / P_rotor        rotor over the housing [K/W]

Away from the record's point the page feeds the network its own losses, scaled by the ratio of the
record's losses to its own at the record's point (so the stored point is the record exactly and the
loss model only carries the change), with the housing conductance following the film when the air
moves (``G_housing · h / h_ref``).  ``web/src/lib/thermalAnchor.ts`` is the same arithmetic
(parity: tests/test_configure_anchor.py).  Nothing here writes anything.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _first(*vals: Any) -> Optional[float]:
    for v in vals:
        x = _num(v)
        if x is not None:
            return x
    return None


def _get(d: Any, *path: str) -> Any:
    for k in path:
        if not isinstance(d, Mapping):
            return None
        d = d.get(k)
    return d


def anchor_from_record(duty: str, entry: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The anchor of one duty from its stored results (``duty_results.get(die, cfg)[duty]``), or
    None when the record lacks a steady thermal answer with the temperatures and losses it needs."""
    if not isinstance(entry, Mapping):
        return None
    c = entry.get("coupled") if isinstance(entry.get("coupled"), Mapping) else {}
    t = entry.get("thermal") if isinstance(entry.get("thermal"), Mapping) else {}
    if not c and not t:
        return None
    if c and (c.get("runaway") or c.get("converged") is False):
        return None
    if str(c.get("mode") or "steady") != "steady":
        return None                                   # a point AT a limit is not a steady state to anchor on
    at = _get(c, "time_to_limit", "at_point_c") or {}
    comp = t.get("components") or {}
    cool = t.get("cooling") or {}
    outer = cool.get("outer") or {}
    budget = cool.get("heat_budget") or {}
    em = c.get("em") or {}
    pt = t.get("point") or _get(c, "constants_20c", "point") or {}
    T_w = _first(at.get("winding"), _get(comp, "winding", "max"), c.get("coil_temp_c"))
    T_m = _first(at.get("magnet"), _get(comp, "magnet", "max"), c.get("magnet_temp_max_c"), c.get("magnet_temp_c"))
    T_h = _first(at.get("housing"), _get(cool, "heat_path", "t_body_c"), outer.get("t_wall_c"), _get(comp, "stator", "avg"))
    amb = _first(pt.get("ambient_temp"), outer.get("t_sink_c"), _get(c, "limited", "cooling", "ambient_temp"))
    p_tot = _first(budget.get("losses_W"), em.get("P_loss_total_W"), t.get("P_loss_total_W"))
    p_cu = _first(em.get("P_stranded_W"), t.get("P_cu_W"))
    p_rot = _first(budget.get("rotor_W"), em.get("P_solid_W"),
                   (_num(em.get("P_mag_W")) or 0.0) + (_num(em.get("P_shaft_W")) or 0.0) or None)
    rpm, cur = _first(pt.get("rpm")), _first(pt.get("I_phase_rms"))
    if None in (T_w, T_m, T_h, amb, p_tot, p_cu, rpm, cur) or p_tot <= 0 or p_cu <= 0 or T_h <= amb + 0.1:
        return None
    T_w, T_m = max(T_w, T_h), max(T_m, T_h)
    g_h = p_tot / (T_h - amb)
    r_w = (T_w - T_h) / p_cu
    r_gap = (T_m - T_h) / p_rot if p_rot and p_rot > 0 else 0.0
    return {
        "schema": "configure-thermal-anchor-1",
        "duty": duty,
        "cooling_mode": str(pt.get("cooling_mode") or outer.get("mode") or ""),
        "air_speed_ms": _first(outer.get("air_speed_mps"), pt.get("air_speed_mps")) or 0.0,
        "ambient_C": amb,
        "point": {"rpm": rpm, "I_A": cur},
        "drive": str(c.get("drive") or t.get("drive") or "sine"),
        "record": {"T_winding_C": T_w, "T_magnet_C": T_m, "T_housing_C": T_h,
                   "P_total_W": p_tot, "P_cu_W": p_cu, "P_rotor_W": p_rot or 0.0},
        "G_housing_W_K": g_h, "R_winding_K_W": r_w, "R_gap_K_W": r_gap,
        "computed_at": c.get("computed_at") or t.get("computed_at"),
    }


def anchored_temperatures(a: Mapping[str, Any], p_cu: float, p_rotor: float, p_total: float,
                          h: float, h_ref: float, ambient_c: float,
                          k_cu: float = 1.0, k_rotor: float = 1.0, k_total: float = 1.0,
                          p_extra: float = 0.0) -> Dict[str, float]:
    """The anchored network at one point: the page's losses times the record/page ratios at the record's
    point (``k_*``; 1 = the record's own losses), the housing conductance following the film.
    ``p_extra`` = heat the record's (sine) losses do not contain - the PWM extra loss - added to the
    winding and the total unscaled."""
    g_h = float(a["G_housing_W_K"]) * (max(h, 1e-9) / max(h_ref, 1e-9))
    x_h = (k_total * p_total + p_extra) / max(g_h, 1e-12)
    T_h = ambient_c + x_h
    return {"housing": T_h,
            "winding": T_h + (k_cu * p_cu + p_extra) * float(a["R_winding_K_W"]),
            "magnet": T_h + k_rotor * p_rotor * float(a["R_gap_K_W"])}
