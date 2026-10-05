"""Physical limits Configure's sliders obey, resolved on the server.

Owner rules, 2026-10-05.  Most limits are arithmetic the web does itself (the
slot fit, the wire minimum); two need data that lives with the MACHINE and
therefore comes from here, through ``GET /api/catalog/{motor_id}/configure_context``:

* the **stack-length maximum**, set BY HAND per motor by an admin and stored on
  the catalogue card (``configure_limits.L_max_mm``);
* the **phase-current maximum**: what the machine's own inverter device allows,
  from the Controller settings saved with its configuration — the device's
  continuous drain-current rating at the junction limit times the devices in
  parallel, converted to a phase current;
* the pack and the modulation index the speed limit is read against.

Nothing here solves a field and nothing is written except the one hand-set
field.  The current limit uses the SAME card method the Controller tab's
"suggested parallel" and its datasheet-limit table use
(:meth:`inverter.devices.DeviceCard.i_d_rating`), so the two cannot disagree.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

#: The case temperature the continuous rating is read at.  100 degC is the
#: Controller catalogue's own basis (``DeviceCard.row()['i_d_100c_A']``, the
#: number ``suggested_parallel`` divides by): at that case temperature the
#: datasheet rating is the current that brings the junction to T_j,max.
RATING_T_CASE_C = 100.0

#: The modulation index the speed limit assumes when the machine's Controller
#: settings do not state one (owner 2026-10-05).
DEFAULT_MODULATION_INDEX = 0.89

#: Upper bound on a hand-set stack length [mm] — a typo guard, not a physics limit.
L_MAX_SANITY_MM = 2000.0


def _pos(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if (math.isfinite(x) and x > 0.0) else None


def current_limit(ctrl: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The inverter-side phase-current ceiling of a machine.

    ``{set: False, reason}`` when the machine has no Controller device saved
    (Configure then keeps the passport's rule and says "no controller set").
    Otherwise ``i_phase_rms_max_A`` = sqrt(2) x I_D(rating) x devices in
    parallel: one switch carries its leg's current for half the period, so its
    full-period rms is I_leg / sqrt(2) — the very definition the Controller
    solve judges against the card's continuous rating.
    """
    from motor_ai_sim.inverter import devices as _dev

    c = ctrl if isinstance(ctrl, dict) else {}
    part = str(c.get("device") or "").strip()
    if not part:
        return {"set": False, "reason": "no controller device is saved with this machine"}
    try:
        card = _dev.get_device(part)
    except Exception as exc:                                   # noqa: BLE001
        return {"set": False, "device": part,
                "reason": f"the saved controller device cannot be read: {exc}"}
    rating = card.i_d_rating(RATING_T_CASE_C)
    i_d = _pos(rating.get("i_a"))
    if i_d is None:
        return {"set": False, "device": card.part,
                "reason": f"{card.part} publishes no continuous current rating"}
    n_par = int(_pos(c.get("devices_parallel")) or 1)
    return {
        "set": True, "device": card.part, "devices_parallel": n_par,
        "i_d_rating_A": round(i_d, 2), "t_case_c": RATING_T_CASE_C,
        "t_j_max_c": card.t_j_max_c, "basis": rating.get("basis"),
        "i_phase_rms_max_A": round(math.sqrt(2.0) * i_d * n_par, 1),
    }


def modulation(ctrl: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    c = ctrl if isinstance(ctrl, dict) else {}
    m = _pos(c.get("modulation_index"))
    if m is not None:
        return {"m": m, "source": "controller"}
    return {"m": DEFAULT_MODULATION_INDEX, "source": "default"}


def pack(batt: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The machine's battery pack totals, or ``None`` when it names none."""
    b = batt if isinstance(batt, dict) else {}
    v_max, v_nom, v_min = _pos(b.get("v_max")), _pos(b.get("v_nom")), _pos(b.get("v_min"))
    if v_max is None and v_nom is None:
        return None
    return {"v_max": v_max, "v_nom": v_nom, "v_min": v_min}


def hand_limits(card: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The hand-set limits stored on a catalogue card (``L_max_mm`` or ``None``)."""
    blk = (card or {}).get("configure_limits")
    blk = blk if isinstance(blk, dict) else {}
    return {"L_max_mm": _pos(blk.get("L_max_mm")),
            "set_by": blk.get("set_by"), "set_at": blk.get("set_at")}


def validate_l_max(value: Any) -> Optional[float]:
    """``None`` clears the limit; a number must be positive and sane."""
    if value is None:
        return None
    v = _pos(value)
    if v is None or v > L_MAX_SANITY_MM:
        raise ValueError(
            f"L_max_mm must be a positive length up to {L_MAX_SANITY_MM:g} mm "
            f"(or null to fall back to the default rule); got {value!r}")
    return v


def context(card: Optional[Dict[str, Any]], family_doc: Optional[Dict[str, Any]]
            ) -> Dict[str, Any]:
    """Everything Configure reads about one machine's physical limits."""
    fam = family_doc if isinstance(family_doc, dict) else {}
    ctrl = fam.get("controller") if isinstance(fam.get("controller"), dict) else {}
    return {
        "limits": hand_limits(card),
        "current": current_limit(ctrl),
        "modulation": modulation(ctrl),
        "battery": pack(fam.get("battery")),
        "has_family_doc": bool(fam),
    }
