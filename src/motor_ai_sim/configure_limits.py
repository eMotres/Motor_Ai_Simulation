"""Physical limits Configure's sliders obey, resolved on the server.

Owner rules, 2026-10-05.  Most limits are arithmetic the web does itself (the
slot fit, the wire minimum); two need data that lives with the MACHINE and
therefore comes from here, through ``GET /api/catalog/{motor_id}/configure_context``:

* the **stack-length maximum**, set BY HAND per motor by an admin and stored on
  the catalogue card (``configure_limits.L_max_mm``);
* the **phase-current maximum** (owner 2026-10-20): the binding REAL limit, not the
  bare drain-current rating of the MOSFET (``current_limit`` below is only the
  device's datasheet figure and is kept as information).  The server states the
  limits it knows - the demagnetisation limit of the passport and the
  controller BOARD's CONTINUOUS current (:func:`current_caps`).  The web caps the
  slider at the lowest MOTOR limit (peak duty, demag, passport grid top; never
  under a duty the loaded configuration saved) and only MARKS the board's
  continuous current on the slider - it is a warning, never a cap
  (``web/src/lib/configuratorLimits.ts``);
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
    """The DATASHEET figure of the machine's inverter device, as a phase current.

    INFORMATION ONLY (owner 2026-10-20): it is the transistor's own drain-current
    rating, not what the controller BOARD can carry and not what the motor can take,
    so Configure no longer sets its current slider from it - the slider reads
    :func:`current_caps` and the motor's own limits instead.

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


#: The board cooling class the Configure current limit reads: the passport's rated
#: airflow (the basis of its "Controller continuous" tile).
BOARD_COOLING_CLASS = "favourable"


def _board_entry(v: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The board's continuous phase current of one computed drive variant, or ``None``."""
    lim = v.get("i_board_limit_A") if isinstance(v.get("i_board_limit_A"), dict) else {}
    a = _pos(lim.get(BOARD_COOLING_CLASS))
    if a is None:
        return None
    return {"A": round(a, 2), "device": str(v.get("device") or "") or None,
            "n_parallel": int(_pos(v.get("n_parallel")) or 1), "variant": str(v.get("id") or "") or None,
            "cooling": BOARD_COOLING_CLASS, "source": "passport drive variant"}


def board_cap(fam: Optional[Dict[str, Any]], ctrl: Optional[Dict[str, Any]],
              variants: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """The controller BOARD's continuous phase current for the machine's own Controller
    device (``None`` = the board model states none).

    Read from the board model the passport carries (stage 3: ``i_board_limit_A`` of the
    computed drive variant that uses the machine's device and number of devices in
    parallel; else the S1 model's sine controller block) - never from the bare
    drain-current rating of the transistor.
    """
    fam = fam if isinstance(fam, dict) else {}
    c = ctrl if isinstance(ctrl, dict) else {}
    part = str(c.get("device") or "").strip().upper()
    if not part:
        return None
    n_par = int(_pos(c.get("devices_parallel")) or 1)
    vs = variants
    if vs is None:
        try:
            from motor_ai_sim import passport_store as _ps
            vs = _ps.variants_for(f"{fam.get('die') or ''} {fam.get('name') or ''}".strip(), None)
        except Exception:                                   # noqa: BLE001
            vs = None
    cands = []
    for v in vs or []:
        if not isinstance(v, dict) or str(v.get("device") or "").strip().upper() != part:
            continue
        e = _board_entry(v)
        if e is not None:
            cands.append(e)
    exact = [e for e in cands if e["n_parallel"] == n_par]
    if exact:
        # several carriers of one build: the board limit does not depend on the carrier
        # in the model's calibration; the lowest is the safe one to state
        return min(exact, key=lambda e: e["A"])
    try:
        from motor_ai_sim import propeller_s1 as _s1
        m = _s1.load_model(str(fam.get("die") or ""), str(fam.get("name") or ""))
        sc = m.get("sine_controller") or {}
        a = _pos((sc.get("i_board_limit_A") or {}).get(BOARD_COOLING_CLASS))
        if a is not None:
            return {"A": round(a, 2), "device": part, "n_parallel": n_par, "variant": None,
                    "cooling": BOARD_COOLING_CLASS, "source": "S1 model sine controller"}
    except Exception:                                       # noqa: BLE001
        pass
    return None


def demag_cap(fam: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The passport's demagnetisation current limit (<= 1 % rated-torque drop, phase rms)
    of this configuration, or ``None`` when its model carries none."""
    fam = fam if isinstance(fam, dict) else {}
    try:
        from motor_ai_sim import propeller_s1 as _s1
        m = _s1.load_model(str(fam.get("die") or ""), str(fam.get("name") or ""))
    except Exception:                                       # noqa: BLE001
        return None
    dm = m.get("demag") if isinstance(m.get("demag"), dict) else {}
    a = _pos(dm.get("I_limit_A"))
    if a is None:
        return None
    return {"A": round(a, 2), "ref_magnet_C": _pos(dm.get("ref_magnet_C")),
            "source": "passport demagnetisation probe"}


def current_caps(fam: Optional[Dict[str, Any]], ctrl: Optional[Dict[str, Any]],
                 variants: Optional[list] = None) -> Dict[str, Any]:
    """The current limits this machine's own records state: ``{demag, board}``, each
    ``None`` when unknown.  Both are values AT THE SAVED BUILD; the web scales the
    demag one with ampere-turns when the user changes turns or connection."""
    return {"demag": demag_cap(fam), "board": board_cap(fam, ctrl, variants)}


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


from motor_ai_sim.applications import config_applications, default_application  # noqa: E402


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
    try:
        phase_count = int(wnd.get("phases") or 3)
    except (TypeError, ValueError):
        raise ValueError(f"configuration {doc.get('name')!r} has an invalid winding phase count")
    if phase_count not in (3, 6):
        raise ValueError(f"configuration {doc.get('name')!r} uses unsupported {phase_count}-phase winding")
    phase_model_supported = (phase_count == 3 or
                             (n_par is not None and n_par.is_integer() and int(n_par) % 2 == 0))
    current_arms = _num((duty or {}).get("current_arms"))
    return {
        "config": str(doc.get("name") or ""),
        "die": str(doc.get("die") or ""),
        "phase_count": phase_count,
        "phase_sets": 2 if phase_count == 6 else 1,
        "phase_model_supported": phase_model_supported,
        "phase_model_status": "ready" if phase_model_supported else "unsupported_parallel_path",
        "current_basis": "equivalent_three_phase_total" if phase_count == 6 else "phase_current",
        "current_per_inverter_A": current_arms / 2.0 if phase_count == 6 and current_arms is not None else current_arms,
        "winding": {k: wnd.get(k) for k in ("phases", "connection", "set1_paths", "set_neutrals")
                    if k in wnd},
        "knobs": {
            "L_mm": _num(geo.get("motor_length")),
            "N": _num(geo.get("num_wires_per_slot")),
            "wireH_mm": _num(geo.get("wire_height")),
            "split": split if split is not None else 1.0,
            "nP": n_par if n_par is not None else 1.0,
            "I_A": current_arms,
            "rpm": _num((duty or {}).get("rpm")),
        },
        "duty": (duty or {}).get("name"),
        # Every named duty is available to Propeller-mode defaults. The legacy
        # preset knobs above deliberately remain the rated (or first) duty so
        # Manual mode and existing preset behavior do not change.
        "duty_points": [
            {"name": str(d.get("name") or ""),
             "current_A": _num(d.get("current_arms")),
             "rpm": _num(d.get("rpm")),
             # the rest of the saved point, for Configure's configuration x mode
             # buttons (owner 2026-10-13)
             "gamma_deg": _num(d.get("gamma_deg")),
             "mode": str(d.get("mode") or "motor")}
            for d in duties if d.get("name")
        ],
        "battery": pack(doc.get("battery")),
        "device": (str(ctrl.get("device")).strip() or None) if ctrl.get("device") else None,
        "pwm_variants": vs,
        # the configuration's own application list (a die can mix applications)
        "applications": config_applications(doc)[0],
        "application": default_application(config_applications(doc)[0]),
        # the drive a preset opens on: ALWAYS Sine (owner 2026-10-20: the rated point is a sine rating, so on the
        # first load the speed marker, the rated duty and the S1 line are one point).  The PWM button then
        # picks the first computed variant (the default build, two devices per switch), named here.
        "drive_variant": None,
        "drive_variant_suggested": vs[0]["id"] if vs else None,
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


#: Winding limit fallback when the single source cannot be imported: the project's
#: ONE insulation class, N (200 degC) — mirrors ``report.PROJECT_INSULATION_C``
#: (owner 2026-10-12: the winding limit is 200 degC class N; the earlier "class H
#: 180 degC default" of 2026-10-05 is stale and made the Configure tiles print
#: "> 180").  Normally read from ``coupled_time_to_limit.winding_limit()``.
WINDING_LIMIT_C = 200.0


def thermal_limits(fam: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The temperatures Configure judges the winding, the magnet and the housing against.

    Winding: the project's class N 200 degC, read from the ONE place that states it
    (``coupled_time_to_limit.winding_limit``).  Housing: the SAME limit — the housing
    can never be hotter than the winding it carries, and drone housings run far
    hotter than the robotics touch limit (review 2026-10-12).
    Magnet: the ``max_working_temp_c`` of the magnet card the machine is assigned
    (``materials.magnet``); ``None`` when the machine names no magnet or the card carries
    no limit - Configure then falls back to its own default and says so."""
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
    try:
        from motor_ai_sim.coupled_time_to_limit import winding_limit
        w_c, w_basis = winding_limit()
    except Exception:                                   # noqa: BLE001
        w_c, w_basis = WINDING_LIMIT_C, "project insulation class N (200 °C)"
    try:
        from motor_ai_sim.applications import (config_applications,
                                               default_application)
        app = default_application(config_applications(fam)[0])
    except Exception:                                   # noqa: BLE001
        app = None
    r_c, r_basis = rated_winding(app)
    out = {"winding_C": float(w_c), "winding_basis": w_basis,
           # THE RATED POINT (owner 2026-10-19): the coils' mean at the rated
           # winding temperature of this configuration's application (180 °C
           # aviation / propeller, 70 °C robots) — Configure's rated cap aims
           # here; the red line stays on ``winding_C`` (the insulation class).
           "rated_winding_C": r_c, "rated_winding_basis": r_basis,
           "magnet_C": mag, "magnet_card": str(name) if name else None}
    r_amb = rated_ambient(app)
    if r_amb is not None:
        # …and the air the rated point is stated at (robots: 30 °C)
        out["rated_ambient_C"] = r_amb
    return out


#: Rated winding fallback when the single source cannot be imported — mirrors
#: ``report.PROJECT_RATED_WINDING_C`` (owner 2026-10-19: rated = coils at 180 °C).
RATED_WINDING_C = 180.0


def rated_winding(application: Optional[str] = None) -> tuple:
    """``(rated winding °C, basis)`` for ``application`` from
    ``coupled_time_to_limit.rated_winding_limit`` — the ONE place the S1
    rating, ``solve_to: continuous``, the duty class and Configure all read."""
    try:
        from motor_ai_sim.coupled_time_to_limit import rated_winding_limit
        c, basis = rated_winding_limit(application)
        return float(c), basis
    except Exception:                                   # noqa: BLE001
        return RATED_WINDING_C, "project rated winding temperature (180 °C)"


def rated_ambient(application: Optional[str] = None) -> Optional[float]:
    """The ambient the rated point is stated at for ``application`` (robots:
    30 °C), ``None`` = the duty's own (``coupled_time_to_limit.rated_ambient``)."""
    try:
        from motor_ai_sim.coupled_time_to_limit import rated_ambient as _ra
        return _ra(application)
    except Exception:                                   # noqa: BLE001
        return None



def cooling(fam: Optional[Dict[str, Any]], card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What cooling this machine may offer (``config/cooling_options.yaml``) plus its identity
    ``die`` / ``config`` - the web needs both to know whether the propeller IS the cooling."""
    from motor_ai_sim.cooling_options import cooling_options
    fam = fam if isinstance(fam, dict) else {}
    die = str(fam.get("die") or (card or {}).get("name") or "").strip()
    cfg = str(fam.get("name") or "").strip() or None
    from motor_ai_sim.applications import config_applications
    return cooling_options(die, cfg, applications=config_applications(fam)[0])


def context(card: Optional[Dict[str, Any]], family_doc: Optional[Dict[str, Any]]
            ) -> Dict[str, Any]:
    """Everything Configure reads about one machine's physical limits."""
    fam = family_doc if isinstance(family_doc, dict) else {}
    ctrl = fam.get("controller") if isinstance(fam.get("controller"), dict) else {}
    from motor_ai_sim.applications import config_applications, default_application
    apps, apps_err = config_applications(fam)
    return {
        # what the machine is for (owner 2026-10-13); the first entry is the default
        "applications": apps,
        "application": default_application(apps),
        "applications_error": apps_err,
        "limits": hand_limits(card),
        # the datasheet figure of the device is information only; the slider's
        # maximum comes from the real limits in ``caps`` (see module docstring)
        "current": {**current_limit(ctrl), "caps": current_caps(fam, ctrl)},
        "modulation": modulation(ctrl),
        "battery": pack(fam.get("battery")),
        "has_family_doc": bool(fam),
        "thermal_limits": thermal_limits(fam),
        "cooling": cooling(fam, card),
    }
