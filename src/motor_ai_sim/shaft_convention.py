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
         p_cu_ac_solved: float = 0.0) -> Tuple[float, Tuple[str, ...], str]:
    """``(D_W, terms, side)``: the shaft-side load the reported torque does not contain.

    ``eddy_coupled``: the copper (and, with ``rotor_eddy_coupled``, the rotor bodies)
    were solved with sigma*dA/dt IN the field.  ``rotor_eddy_coupled`` without
    ``eddy_coupled`` cannot happen in the solver and is treated as not coupled.
    Every loss argument is the REPORTED value (the segmented magnet loss)."""
    side = torque_side(torque_method)
    rotor_in_field = bool(eddy_coupled and rotor_eddy_coupled)
    terms = ["fe"]
    d = _f(p_fe)
    if side == "rotor":
        if not rotor_in_field:
            terms += ["mag", "shaft", "sleeve"]
            d += _f(p_mag) + _f(p_shaft) + _f(p_sleeve)
    else:
        if eddy_coupled:
            terms.append("cu_ac")
            d += _f(p_cu_ac_solved)
        terms += ["mag", "shaft", "sleeve"]
        d += _f(p_mag) + _f(p_shaft) + _f(p_sleeve)
    return d, tuple(terms), side


def drag_of_result(res: Mapping[str, Any], *, p_fe: float, p_mag: float, p_shaft: float,
                   p_sleeve: float) -> Tuple[float, Tuple[str, ...], str]:
    """:func:`drag` from the fields a transient result dict already carries
    (``torque_method``, ``eddy_coupled``, ``rotor_eddy_solved``, ``P_cu_ac_solve_W``)."""
    eddy = bool(res.get("eddy_coupled"))
    rotor = bool(res.get("rotor_eddy_solved")) if "rotor_eddy_solved" in res else eddy
    return drag(torque_method=res.get("torque_method"), eddy_coupled=eddy, rotor_eddy_coupled=rotor,
                p_fe=p_fe, p_mag=p_mag, p_shaft=p_shaft, p_sleeve=p_sleeve,
                p_cu_ac_solved=_f(res.get("P_cu_ac_solve_W")) if eddy else 0.0)


def balance(*, t_field_nm: float, rpm: float, k: Optional[float], d_w: float,
            p_loss_w: float) -> Dict[str, Any]:
    """The shaft-side balance of one run under this convention.

    ``t_field_nm`` is the SIGNED 2-D field torque (negative = generating).  Returns
    ``op_mode`` (from the field-power sign, never from the shaft side), the field power
    (2-D and x k), ``P_mech_W`` (shaft side, generator quoted positive as input), the
    electrical power, the efficiency (0 when the shaft side is not a delivered output,
    e.g. a motor at no load whose shaft must SUPPLY the core loss) and the shaft torque."""
    w = 2.0 * math.pi * _f(rpm) / 60.0
    kk = float(k) if (k is not None and _f(k) > 0.0) else 1.0
    t = _f(t_field_nm)
    p_field = abs(t) * w
    p_field_k = kk * p_field
    d = max(0.0, _f(d_w))
    loss = max(0.0, _f(p_loss_w))
    if t > 0.0:
        op = "motor"
    elif t < 0.0:
        op = "generator"
    else:
        op = "motor"
    if op == "generator":
        p_mech = p_field_k + d                    # input at the shaft
        p_elec = p_mech - loss                    # output at the terminals
        eff = (p_elec / p_mech) if (p_mech > max(loss, 1.0) and p_elec > 0.0) else 0.0
    else:
        p_mech = p_field_k - d                    # output at the shaft (negative at no load)
        p_elec = p_mech + loss                    # input at the terminals
        eff = (p_mech / p_elec) if (p_mech > 0.0 and p_elec > 1.0) else 0.0
    return {
        "op_mode": op, "omega": w, "k": (kk if kk != 1.0 or k is not None else None),
        "P_field_W": p_field, "P_field_k_W": p_field_k, "P_mech_W": p_mech,
        "P_elec_W": p_elec, "efficiency": eff,
        "T_field_k_Nm": kk * abs(t),
        "T_shaft_Nm": (p_mech / w) if w > 0.0 else 0.0,
    }


def per_mass_torque(b: Mapping[str, Any]) -> float:
    """The torque the comparison chart divides by mass (owner Q2, 2026-11-25): the
    SHAFT torque of a motor, the FIELD torque (x k) of a generator."""
    return float(b["T_shaft_Nm"]) if b.get("op_mode") != "generator" else float(b["T_field_k_Nm"])


#: Summary loss key of each drag term (the reported, 2-D watts).
DRAG_TERM_KEYS = {"fe": "P_core_W", "mag": "P_mag_W", "shaft": "P_shaft_W", "sleeve": "P_sleeve_W",
                  "cu_ac": "P_cu_ac_field_W"}


def drag_of_summary(summary: Mapping[str, Any]) -> float:
    """``D`` rebuilt from a marked summary's own loss fields and its stored terms (used
    when a report overlays another run's losses on the summary)."""
    terms = summary.get("P_shaft_drag_terms")
    if not isinstance(terms, (list, tuple)):
        return _f(summary.get("P_shaft_drag_W"))
    return sum(_f(summary.get(DRAG_TERM_KEYS.get(str(t), ""))) for t in terms)


def rederive(out: Dict[str, Any]) -> bool:
    """Recompute, IN PLACE, the shaft-side quantities of a MARKED summary whose torque,
    speed or losses were replaced (report overlays).  Returns False and leaves ``out``
    alone when it carries no marker.  ``T_em_avg_Nm`` is the 2-D field torque (quoted
    positive), ``op_mode`` its sign."""
    if not has_marker(out):
        return False
    t = abs(_f(out.get("T_em_avg_Nm")))
    if str(out.get("op_mode") or "") == "generator":
        t = -t
    d = drag_of_summary(out)
    b = balance(t_field_nm=t, rpm=_f(out.get("rpm")), k=out.get("shaft_k3d"), d_w=d,
                p_loss_w=_f(out.get("P_loss_total_W")))
    out.update({"P_mech_W": b["P_mech_W"], "P_field_W": b["P_field_W"], "T_field_k_Nm": b["T_field_k_Nm"],
                "T_shaft_Nm": b["T_shaft_Nm"], "P_shaft_drag_W": d, "P_elec_W": b["P_elec_W"]})
    return True


def comparable(rows: Sequence[Optional[Mapping[str, Any]]]) -> bool:
    """Saved rows are ranked together only when they share ONE shaft convention
    (a row without the numeric ``shaft_conv`` is the earlier convention)."""
    codes = {int(_f((r or {}).get("shaft_conv"))) for r in rows if r is not None}
    return len(codes) <= 1
