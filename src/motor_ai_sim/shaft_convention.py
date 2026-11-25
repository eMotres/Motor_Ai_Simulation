"""The shaft-power convention of a run summary (owner decision 2026-11-25).

ONE place that answers three questions for every reader of a transient summary:

* which shaft-side load ``D`` the reported (field) torque does NOT already contain;
* the shaft-side power ``P_mech_W`` and the efficiency built on it;
* whether a stored summary follows this convention or the earlier one.

The convention, marker ``shaft_convention = "fe_on_shaft_k3d_v1"``:

* the core loss is a SHAFT load: a motor delivers ``k*T*w - D`` at the shaft, a
  generator needs ``k*T*w + D`` at the shaft (quoted positive, as input);
* ``k`` is the run's 3-D torque factor (measured k_T, else the Stage A k_flux),
  applied to the FIELD part only, never to ``D``;
* every loss stays 2-D (conservative: copper at a given current is unchanged, and
  iron at the 2-D flux reads slightly high);
* the electrical power closes on the same losses: motor ``P_elec = P_mech + L``,
  generator ``P_elec = P_mech - L``.

``D`` depends on which torque was reported and on what the field solve already
dissipated (the drag of a loss solved IN the field is already in a rotor-side torque;
a stator-side torque is the power that entered the field, so it contains every
in-field loss):

=====================  ===================  =============================================
reported torque        in-field losses      D
=====================  ===================  =============================================
rotor-side (Coulomb,   coupled rotor eddy   Fe
Maxwell)               rotor eddy not       Fe + magnet + shaft + sleeve (reported)
                       coupled
stator-side (flux      none (eddy off)      Fe + magnet + shaft + sleeve (reported)
linkage, terminal      copper AC (eddy on)  Fe + solved copper AC + magnet + shaft +
work)                                       sleeve (reported)
=====================  ===================  =============================================

A summary WITHOUT the marker is the earlier convention: every loss on the electrical
side and ``P_mech_W = T*w`` (2-D).  It is never converted: old and restored runs keep
their numbers.

Pure functions, no I/O.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: The marker value of this convention.
SHAFT_CONVENTION = "fe_on_shaft_k3d_v1"
#: The numeric form a Compare / saved-metrics row carries (numbers-only metric dicts).
SHAFT_CONVENTION_CODE = 1

#: Reported-torque methods that are the force ON THE ROTOR (the drag of every in-field
#: rotor loss is inside them).  Everything else the solver can report is a stator-side
#: mean (flux-linkage space vector, terminal work), i.e. the power that entered the field.
ROTOR_SIDE_METHODS = frozenset({"coulomb_virtual_work", "maxwell_stress"})

#: One sentence that goes with every summary carrying the marker.
BASIS_NOTE = ("Shaft side: core loss counted as a shaft load; the 3-D torque factor applies to the field "
              "torque only; losses stay 2-D (conservative: copper at the given current is unchanged, iron at "
              "the 2-D flux reads slightly high).")
#: The same, for a run with no 3-D passport (an optimiser candidate, a machine never measured).
BASIS_NOTE_2D = ("Shaft side: core loss counted as a shaft load; no 3-D passport for this geometry, so the "
                 "torque is the 2-D field torque; losses are 2-D.")


def _f(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if math.isfinite(x) else 0.0


def has_marker(summary: Optional[Mapping[str, Any]]) -> bool:
    """True when ``summary`` follows this convention (carries the marker)."""
    return isinstance(summary, Mapping) and summary.get("shaft_convention") == SHAFT_CONVENTION


def torque_side(method: Optional[str]) -> str:
    """``"rotor"`` for a Coulomb / Maxwell mean, else ``"stator"``."""
    return "rotor" if str(method or "") in ROTOR_SIDE_METHODS else "stator"


def drag(*, torque_method: Optional[str], eddy_coupled: bool, rotor_eddy_coupled: bool,
         p_fe: float, p_mag: float, p_shaft: float, p_sleeve: float,
         p_cu_ac_solved: float = 0.0) -> Dict[str, Any]:
    """Which losses sit where for ONE run (every loss argument is the REPORTED value).

    Returns ``{"D_W", "terms", "side", "rotor_in_field", "rotor_embedded_W",
    "stator_embedded_W"}``:

    * ``D_W`` = the losses supplied from the SHAFT side: the core loss and every rotor
      loss (magnet, shaft, sleeve), solved in the field or not;
    * ``rotor_embedded_W`` = the rotor eddy loss already inside a ROTOR-side torque
      (solved in the field: its drag is in the force on the rotor);
    * ``stator_embedded_W`` = the solved copper AC already inside a STATOR-side torque
      (that mean is the power entering the field).

    The 3-D factor scales only the converted air-gap power ``G``, rebuilt from the
    reported torque by removing what is embedded (see :func:`balance`), never a loss.
    ``rotor_eddy_coupled`` without ``eddy_coupled`` cannot happen in the solver and is
    treated as not coupled."""
    side = torque_side(torque_method)
    rotor_in_field = bool(eddy_coupled and rotor_eddy_coupled)
    rotor = _f(p_mag) + _f(p_shaft) + _f(p_sleeve)
    terms = ("fe", "mag", "shaft", "sleeve")
    return {
        "D_W": _f(p_fe) + rotor, "terms": terms, "side": side, "rotor_in_field": rotor_in_field,
        "rotor_embedded_W": rotor if (side == "rotor" and rotor_in_field) else 0.0,
        "stator_embedded_W": _f(p_cu_ac_solved) if (side == "stator" and eddy_coupled) else 0.0,
    }


def drag_of_result(res: Mapping[str, Any], *, p_fe: float, p_mag: float, p_shaft: float,
                   p_sleeve: float) -> Dict[str, Any]:
    """:func:`drag` from the fields a transient result dict already carries
    (``torque_method``, ``eddy_coupled``, ``rotor_eddy_solved``, ``P_cu_ac_solve_W``)."""
    eddy = bool(res.get("eddy_coupled"))
    rotor = bool(res.get("rotor_eddy_solved")) if "rotor_eddy_solved" in res else eddy
    return drag(torque_method=res.get("torque_method"), eddy_coupled=eddy, rotor_eddy_coupled=rotor,
                p_fe=p_fe, p_mag=p_mag, p_shaft=p_shaft, p_sleeve=p_sleeve,
                p_cu_ac_solved=_f(res.get("P_cu_ac_solve_W")) if eddy else 0.0)


def balance(*, t_field_nm: float, rpm: float, k: Optional[float], d_w: float,
            p_loss_w: float, rotor_embedded_w: float = 0.0,
            stator_embedded_w: float = 0.0) -> Dict[str, Any]:
    """The shaft-side balance of one run under this convention.

    ``t_field_nm`` is the SIGNED 2-D reported torque (negative = generating); ``s`` = +1
    motor, -1 generator.  The converted air-gap power is rebuilt first, so the 3-D factor
    never multiplies a loss:

    * rotor-side torque with the rotor eddy in the field: ``G = |T|*w + s*R``
      (the drag R is inside the force on the rotor);
    * stator-side torque: ``G = |T|*w - s*Cu_ac`` (the power into the field contains the
      solved copper AC);
    * otherwise ``G = |T|*w``.

    Then motor ``P = k*G - D``, generator ``P = k*G + D`` (input, quoted positive),
    with ``D`` = core loss + every rotor loss, and the electrical power ``E = P + L``
    (motor) / ``P - L`` (generator).  At zero current G = 0, so a generator's input is
    exactly D and its output 0 W (no 3-D factor on losses).  ``op_mode`` comes from the
    sign of the reported torque.  Efficiency is 0 when nothing useful is delivered."""
    w = 2.0 * math.pi * _f(rpm) / 60.0
    kk = float(k) if (k is not None and _f(k) > 0.0) else 1.0
    t = _f(t_field_nm)
    op = "generator" if t < 0.0 else "motor"
    sgn = -1.0 if op == "generator" else 1.0
    p_field = abs(t) * w
    g = p_field + sgn * _f(rotor_embedded_w) - sgn * _f(stator_embedded_w)
    d = max(0.0, _f(d_w))
    loss = max(0.0, _f(p_loss_w))
    if op == "generator":
        p_mech = kk * g + d                       # input at the shaft
        p_elec = p_mech - loss                    # output at the terminals
        eff = (p_elec / p_mech) if (p_mech > 0.0 and p_elec > 0.0) else 0.0
    else:
        p_mech = kk * g - d                       # output at the shaft (negative at no load)
        p_elec = p_mech + loss                    # input at the terminals
        eff = (p_mech / p_elec) if (p_mech > 0.0 and p_elec > 0.0) else 0.0
    return {
        "op_mode": op, "omega": w, "k": (kk if kk != 1.0 or k is not None else None),
        "P_field_W": p_field, "P_gap_W": g, "P_gap_k_W": kk * g, "P_mech_W": p_mech,
        "P_elec_W": p_elec, "efficiency": eff,
        "T_field_k_Nm": kk * abs(t),
        "T_shaft_Nm": (p_mech / w) if w > 0.0 else 0.0,
    }


def balance_of(dr: Mapping[str, Any], *, t_field_nm: float, rpm: float, k: Optional[float],
               p_loss_w: float) -> Dict[str, Any]:
    """:func:`balance` with the losses placed by :func:`drag`."""
    return balance(t_field_nm=t_field_nm, rpm=rpm, k=k, d_w=dr["D_W"], p_loss_w=p_loss_w,
                   rotor_embedded_w=dr["rotor_embedded_W"], stator_embedded_w=dr["stator_embedded_W"])


def per_mass_torque(b: Mapping[str, Any]) -> float:
    """The torque the comparison chart divides by mass (owner Q2, 2026-11-25): the
    SHAFT torque of a motor, the FIELD torque (x k) of a generator."""
    return float(b["T_shaft_Nm"]) if b.get("op_mode") != "generator" else float(b["T_field_k_Nm"])


#: The summary fields a marked summary carries for its convention (copied or stripped together).
CONVENTION_KEYS = ("shaft_convention", "shaft_k3d", "torque_side", "shaft_rotor_in_field",
                   "P_torque_embedded_W", "P_cu_ac_field_W", "P_shaft_drag_W", "P_shaft_drag_terms",
                   "P_field_W", "P_gap_W", "T_field_k_Nm", "T_shaft_Nm", "P_elec_W", "shaft_basis_note")


def drag_of_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    """The loss placement of a MARKED summary, rebuilt from its own loss fields (so a
    report or a loop that replaced the losses gets a D and an embedded part that match
    them)."""
    side = str(summary.get("torque_side") or "rotor")
    rotor_in_field = bool(summary.get("shaft_rotor_in_field"))
    rotor = _f(summary.get("P_mag_W")) + _f(summary.get("P_shaft_W")) + _f(summary.get("P_sleeve_W"))
    return {
        "D_W": _f(summary.get("P_core_W")) + rotor, "terms": ("fe", "mag", "shaft", "sleeve"),
        "side": side, "rotor_in_field": rotor_in_field,
        "rotor_embedded_W": rotor if (side == "rotor" and rotor_in_field) else 0.0,
        "stator_embedded_W": _f(summary.get("P_cu_ac_field_W")) if side == "stator" else 0.0,
    }


def fields(b: Mapping[str, Any], dr: Mapping[str, Any], k: Optional[float]) -> Dict[str, Any]:
    """The convention's own summary fields from one balance (rounded for storage)."""
    return {
        "shaft_convention": SHAFT_CONVENTION,
        "P_field_W": round(b["P_field_W"], 1), "P_gap_W": round(b["P_gap_W"], 1),
        "T_field_k_Nm": round(b["T_field_k_Nm"], 4), "T_shaft_Nm": round(b["T_shaft_Nm"], 4),
        "P_shaft_drag_W": round(dr["D_W"], 3), "P_shaft_drag_terms": list(dr["terms"]),
        "torque_side": dr["side"], "shaft_rotor_in_field": bool(dr["rotor_in_field"]),
        "P_torque_embedded_W": round(dr["rotor_embedded_W"] or dr["stator_embedded_W"], 3),
        "P_cu_ac_field_W": (round(dr["stator_embedded_W"], 3) if dr["side"] == "stator" else None),
        "P_elec_W": round(b["P_elec_W"], 1),
        "shaft_k3d": (round(float(k), 6) if k is not None else None),
        "shaft_basis_note": (BASIS_NOTE if k is not None else BASIS_NOTE_2D),
    }


def rederive(out: Dict[str, Any]) -> bool:
    """Recompute, IN PLACE, the shaft-side quantities of a MARKED summary whose torque,
    speed or losses were replaced (report overlays, the PWM / S1 loops).  Returns False
    and leaves ``out`` alone when it carries no marker.  ``T_em_avg_Nm`` is the 2-D
    reported torque (quoted positive), ``op_mode`` its sign."""
    if not has_marker(out):
        return False
    t = abs(_f(out.get("T_em_avg_Nm")))
    if str(out.get("op_mode") or "") == "generator":
        t = -t
    dr = drag_of_summary(out)
    k = out.get("shaft_k3d")
    b = balance_of(dr, t_field_nm=t, rpm=_f(out.get("rpm")), k=k, p_loss_w=_f(out.get("P_loss_total_W")))
    out.update(fields(b, dr, k if k is not None else None))
    out["P_mech_W"] = round(b["P_mech_W"], 1)
    out["efficiency"] = round(b["efficiency"], 4)
    return True


def strip(summary: Dict[str, Any]) -> None:
    """Remove every convention field from ``summary`` IN PLACE (an overlay of an
    unmarked run onto a marked one leaves an unmarked, earlier-convention summary)."""
    for key in CONVENTION_KEYS:
        summary.pop(key, None)


def overlay(base: Dict[str, Any], over: Mapping[str, Any]) -> None:
    """Carry the convention of ``over`` onto ``base`` ATOMICALLY: all its fields when it
    is marked, none (and the marker removed) when it is not."""
    strip(base)
    if has_marker(over):
        for key in CONVENTION_KEYS:
            if key in over:
                base[key] = over[key]


def comparable(rows: Sequence[Optional[Mapping[str, Any]]]) -> bool:
    """Saved rows are ranked together only when they share ONE shaft convention
    (a row without the numeric ``shaft_conv`` is the earlier convention)."""
    codes = {int(_f((r or {}).get("shaft_conv"))) for r in rows if r is not None}
    return len(codes) <= 1
