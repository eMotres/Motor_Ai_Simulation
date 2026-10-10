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
    out: Dict[str, Any] = {"v_max": v_max, "v_nom": v_nom, "v_min": v_min}
    # what the Battery panel seeds itself from: series cells, chemistry, per-cell voltages
    cells = _pos(b.get("cells"))
    out["cells"] = int(round(cells)) if cells is not None else None
    out["chemistry"] = (str(b.get("chemistry")).strip() or None) if b.get("chemistry") else None
    for k in ("v_cell_min", "v_cell_nom", "v_cell_max"):
        out[k] = _pos(b.get(k))
    return out


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


# ---------------------------------------------------------------------------
# Presets — the real configurations of the machine's DIE
# ---------------------------------------------------------------------------

#: The duty a preset's operating point is taken from, when the configuration has one
#: of this name; otherwise its first duty.  (A choice of WHICH saved duty, not a number.)
PREFERRED_DUTY = "rated"


def _num(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def preset_of(doc: Dict[str, Any], die_geometry: Optional[Dict[str, Any]] = None,
              variants: Optional[list] = None) -> Dict[str, Any]:
    """One configuration of a die as a Configure PRESET: every knob the tuner has, read from
    the configuration's own document (never typed here), plus its saved pack and the
    drive variants computed for it.

    A key the configuration does not override falls back to the DIE's geometry; a value
    neither states is ``None`` and the web leaves that knob where it is.
    """
    base = dict(die_geometry or {})
    geo = {**base, **(doc.get("geometry_overrides") or {})}
    wnd = doc.get("winding") if isinstance(doc.get("winding"), dict) else {}
    duties = [d for d in (doc.get("duties") or []) if isinstance(d, dict)]
    duty = next((d for d in duties if d.get("name") == PREFERRED_DUTY), duties[0] if duties else None)
    n_par = _num(wnd.get("n_parallel"))
    split = _num(geo.get("wire_split"))
    vs = [v for v in (variants or []) if isinstance(v, dict) and v.get("id")]
    ctrl = doc.get("controller") if isinstance(doc.get("controller"), dict) else {}
    return {
        "config": str(doc.get("name") or ""),
        "die": str(doc.get("die") or ""),
        "knobs": {
            "L_mm": _num(geo.get("motor_length")),
            "N": _num(geo.get("num_wires_per_slot")),
            "wireH_mm": _num(geo.get("wire_height")),
            "split": split if split is not None else 1.0,
            "nP": n_par if n_par is not None else 1.0,
            "I_A": _num((duty or {}).get("current_arms")),
            "rpm": _num((duty or {}).get("rpm")),
        },
        "duty": (duty or {}).get("name"),
        # Every named duty is available to Propeller-mode defaults. The legacy
        # preset knobs above deliberately remain the rated (or first) duty so
        # Manual mode and existing preset behavior do not change.
        "duty_points": [
            {"name": str(d.get("name") or ""),
             "current_A": _num(d.get("current_arms")),
             "rpm": _num(d.get("rpm"))}
            for d in duties if d.get("name")
        ],
        "battery": pack(doc.get("battery")),
        "device": (str(ctrl.get("device")).strip() or None) if ctrl.get("device") else None,
        "pwm_variants": vs,
        # the drive a preset opens on: its first computed variant, else Sine
        "drive_variant": vs[0]["id"] if vs else None,
    }


def presets_for_die(docs: list, die_geometry: Optional[Dict[str, Any]] = None,
                    variants_for: Optional[Any] = None) -> list:
    """The presets of a die — ONE PER CONFIGURATION, in the order the die lists them (by
    name).  No names are known here: a die with one configuration has one preset, one with
    five has five, and a configuration added tomorrow is a preset tomorrow.

    ``variants_for(die, config)`` -> the drive variants computed for that configuration.
    """
    out = []
    for d in sorted((x for x in docs if isinstance(x, dict) and x.get("name")),
                    key=lambda x: str(x["name"]).casefold()):
        vs = variants_for(str(d.get("die") or ""), str(d["name"])) if variants_for else None
        out.append(preset_of(d, die_geometry, vs))
    return out


#: Winding limit when the machine names none: insulation class H (owner 2026-10-05, "180 degC
#: class H default"); an assumption, said so in ``thermal_limits.winding_basis``.
WINDING_LIMIT_C = 180.0


def thermal_limits(fam: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The temperatures Configure judges the winding and the magnet against.

    Winding: class H 180 degC (a stated default).  Magnet: the ``max_working_temp_c`` of the
    magnet card the machine is assigned (``materials.magnet``); ``None`` when the machine names
    no magnet or the card carries no limit - Configure then falls back to its own default and
    says so."""
    fam = fam if isinstance(fam, dict) else {}
    mats = fam.get("materials") if isinstance(fam.get("materials"), dict) else {}
    name = mats.get("magnet")
    mag: Optional[float] = None
    if name:
        try:
            from motor_ai_sim.materials import get_material
            mag = _pos(getattr(get_material("magnet", str(name)), "max_working_temp_c", None))
        except Exception:                                   # noqa: BLE001
            mag = None
    return {"winding_C": WINDING_LIMIT_C, "winding_basis": "class H default",
            "magnet_C": mag, "magnet_card": str(name) if name else None}


def cooling(fam: Optional[Dict[str, Any]], card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What cooling this machine may offer (``config/cooling_options.yaml``) plus its identity
    ``die`` / ``config`` - the web needs both to know whether the propeller IS the cooling."""
    from motor_ai_sim.cooling_options import cooling_options
    fam = fam if isinstance(fam, dict) else {}
    die = str(fam.get("die") or (card or {}).get("name") or "").strip()
    cfg = str(fam.get("name") or "").strip() or None
    return cooling_options(die, cfg)


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
        "thermal_limits": thermal_limits(fam),
        "cooling": cooling(fam, card),
    }
