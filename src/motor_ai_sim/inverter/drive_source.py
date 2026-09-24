"""WHERE A MACHINE'S PWM DRIVE COMES FROM — the Controller, and only the Controller.

Owner, 2026-09-24 (a screenshot of the Controller tab's greyed "Carrier
20,000 Hz" placeholder): *«Это значение нужно задавать в контроллере; PWM нужно
выкинуть из Electromagnetic.»*  Until then the carrier had two homes — the
Simulation tab's ``sim.fSwitch`` / ``simulation.f_switch`` and the Controller
tab's ``controller.f_carrier_hz`` — and every consumer picked one of them in
its own order.  This module is the ONE resolution every consumer uses:

  the request (an explicit API field — a carrier study, the Controller tab's
    own Solve body)
    > the CONTROLLER settings saved with the configuration
      (``PATCH /api/family/config/{die}/{cfg}/controller``)
    > MIGRATION — the retired Simulation-tab carrier the configuration left
      behind (the duty's stored PWM record, the duty's ``sim.fSwitch``, any
      duty's ``sim.fSwitch``, ``simulation.f_switch``), read until the
      Controller tab is saved once with a carrier of its own
    > ``DEFAULT_CARRIER_HZ`` (only where a carrier is REQUIRED — a bridge
      run or a controller solve; a report's resonance line and the
      mechanical excitation table pass ``default=False`` and draw no line
      rather than a made-up one).

Every answer carries ``origin`` (``request`` | ``controller`` | ``legacy`` |
``default``) and ``source`` (the words a record and a tooltip print), so no
number reaches a record without saying where it came from.

The DC link follows the same shape: request > the Controller's manual
``v_dc_V`` > the configuration's battery (nominal) > the legacy PWM bus of a
duty record (only when the configuration has no battery at all).

Pure over documents (``resolve_carrier(cfg_doc, ...)``) so the report can use
it on the document it already holds; ``*_for(die, cfg, duty)`` wrappers load
the document (the active catalog context when the names are omitted).
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

#: The carrier when nothing — no request, no saved Controller carrier, no
#: legacy Simulation-tab carrier — names one.  A plain, conservative SiC
#: number; no measurement backs it and every answer that uses it says so.
DEFAULT_CARRIER_HZ = 20_000.0

ORIGIN_REQUEST = "request"
ORIGIN_CONTROLLER = "controller"
ORIGIN_LEGACY = "legacy"
ORIGIN_DEFAULT = "default"

SRC_CONTROLLER_CARRIER = "the Controller settings (carrier)"
SRC_DEFAULT_CARRIER = ("the Controller's stated default (%.0f kHz — no carrier "
                       "saved in the Controller and none left by the Simulation "
                       "tab)" % (DEFAULT_CARRIER_HZ / 1000.0))

# Deprecation messages already written in this process — one line per
# distinct message, not one per request (the panel re-runs often).
_WARNED: set = set()


def warn_deprecated(msg: str) -> None:
    """Log a deprecation ONCE per distinct message per process."""
    if msg in _WARNED:
        return
    _WARNED.add(msg)
    log.warning("DEPRECATED: %s", msg)


def _pos(v: Any) -> Optional[float]:
    """A positive finite number, or ``None``."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if (math.isfinite(f) and f > 0.0) else None


def _ans(hz: Optional[float], origin: Optional[str],
         source: Optional[str]) -> Dict[str, Any]:
    return {"hz": hz, "origin": origin, "source": source}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def controller_block(cfg_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The configuration's saved Controller settings, or ``{}``."""
    blk = (cfg_doc or {}).get("controller")
    return dict(blk) if isinstance(blk, dict) else {}


def _duty_doc(cfg_doc: Optional[Dict[str, Any]],
              duty: Optional[str]) -> Dict[str, Any]:
    if not duty:
        return {}
    for d in ((cfg_doc or {}).get("duties") or []):
        if isinstance(d, dict) and str(d.get("name") or "") == str(duty):
            return d
    return {}


def _duty_sim_carrier(d: Dict[str, Any]) -> Optional[float]:
    """A duty's saved Simulation-tab carrier (``mesh['sim.fSwitch']``; some
    older saves carried the same flat map as ``settings``)."""
    for blk in ("mesh", "settings"):
        m = d.get(blk)
        if isinstance(m, dict):
            v = _pos(m.get("sim.fSwitch"))
            if v is not None:
                return v
    return None


def legacy_carrier(cfg_doc: Optional[Dict[str, Any]],
                   duty: Optional[str] = None, *,
                   legacy_record: Optional[Tuple[Any, str]] = None,
                   live_sim: Optional[Dict[str, Any]] = None
                   ) -> Tuple[Optional[float], Optional[str]]:
    """The carrier the RETIRED Simulation-tab PWM controls left behind —
    ``(hz, words)`` or ``(None, None)``.

    In order: the duty's own stored PWM record (``legacy_record``, handed in
    by a caller that holds it — what that record was ACTUALLY solved at), the
    duty's saved ``sim.fSwitch``, the first other duty of the configuration
    that saved one, the configuration's ``simulation.f_switch``, and the
    process-global ``simulation`` block (``live_sim`` — a caller passes it
    only when it names THIS machine; it is the value that once leaked a
    Ø85's 48 kHz into a Ø200 report).
    """
    if legacy_record is not None:
        v = _pos(legacy_record[0])
        if v is not None:
            return v, ("migrated from the duty's stored PWM record (%s)"
                       % legacy_record[1])
    if duty:
        v = _duty_sim_carrier(_duty_doc(cfg_doc, duty))
        if v is not None:
            return v, ("migrated from the retired Simulation-tab PWM carrier "
                       "of duty '%s' (sim.fSwitch)" % duty)
    for d in ((cfg_doc or {}).get("duties") or []):
        if not isinstance(d, dict):
            continue
        v = _duty_sim_carrier(d)
        if v is not None:
            return v, ("migrated from the retired Simulation-tab PWM carrier "
                       "of duty '%s' (sim.fSwitch)" % (d.get("name") or "?"))
    v = _pos(((cfg_doc or {}).get("simulation") or {}).get("f_switch"))
    if v is not None:
        return v, ("migrated from the configuration's retired "
                   "simulation.f_switch")
    v = _pos((live_sim or {}).get("f_switch"))
    if v is not None:
        return v, ("migrated from the loaded machine's retired "
                   "simulation.f_switch")
    return None, None


# ---------------------------------------------------------------------------
# The carrier
# ---------------------------------------------------------------------------

def resolve_carrier(cfg_doc: Optional[Dict[str, Any]], *,
                    duty: Optional[str] = None,
                    request: Any = None,
                    request_words: str = "the request",
                    controller: Optional[Dict[str, Any]] = None,
                    legacy_record: Optional[Tuple[Any, str]] = None,
                    live_sim: Optional[Dict[str, Any]] = None,
                    default: bool = True) -> Dict[str, Any]:
    """``{hz, origin, source}`` — see the module doc for the order.

    ``controller`` is a Controller block handed in BY REFERENCE (the coupled
    loop's ``body.controller``) and outranks the saved one only because it is
    the same block, newer by at most one auto-save.
    """
    v = _pos(request)
    if v is not None:
        return _ans(v, ORIGIN_REQUEST, request_words)
    for blk, words in ((controller, "the Controller settings sent with the "
                                    "request (carrier)"),
                       (controller_block(cfg_doc), SRC_CONTROLLER_CARRIER)):
        v = _pos((blk or {}).get("f_carrier_hz"))
        if v is not None:
            return _ans(v, ORIGIN_CONTROLLER, words)
    v, words = legacy_carrier(cfg_doc, duty, legacy_record=legacy_record,
                              live_sim=live_sim)
    if v is not None:
        return _ans(v, ORIGIN_LEGACY, words)
    if default:
        return _ans(DEFAULT_CARRIER_HZ, ORIGIN_DEFAULT, SRC_DEFAULT_CARRIER)
    return _ans(None, None, None)


def _context(die: Optional[str], cfg: Optional[str],
             duty: Optional[str]) -> Tuple[Optional[str], Optional[str],
                                           Optional[str], bool]:
    """``(die, cfg, duty, is_active)`` — the names given, else the active
    catalog context; ``is_active`` says the machine named IS the one the
    server has loaded (so its process-global block may be read)."""
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
    except Exception:                                       # noqa: BLE001
        ctx = None
    if not (die and cfg):
        if not ctx:
            return None, None, None, True
        return ctx[0], ctx[1], (duty or ctx[2]), True
    active = bool(ctx and str(ctx[0]) == str(die) and str(ctx[1]) == str(cfg))
    if active and not duty:
        duty = ctx[2]
    return str(die), str(cfg), duty, active


def load_config_doc(die: Optional[str], cfg: Optional[str]) -> Dict[str, Any]:
    if not (die and cfg):
        return {}
    try:
        from motor_ai_sim.routes import family as _fam
        doc = _fam.config_doc(str(die), str(cfg))
    except Exception:                                       # noqa: BLE001
        return {}
    return doc if isinstance(doc, dict) else {}


def _live_sim() -> Dict[str, Any]:
    try:
        from motor_ai_sim.config import get_config
        return dict((get_config() or {}).get("simulation") or {})
    except Exception:                                       # noqa: BLE001
        return {}


def resolve_carrier_for(die: Optional[str] = None, cfg: Optional[str] = None,
                        duty: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
    """:func:`resolve_carrier` on a configuration named by die/cfg (default:
    the active catalog context).  The process-global ``simulation`` block is
    consulted as the LAST legacy tier only when the machine named is the one
    loaded (or nothing is catalogued at all — a client's ▶-copy)."""
    d, c, du, active = _context(die, cfg, duty)
    doc = load_config_doc(d, c)
    if active and "live_sim" not in kw:
        kw["live_sim"] = _live_sim()
    ans = resolve_carrier(doc, duty=du, **kw)
    ans["context"] = {"die": d, "config": c, "duty": du}
    return ans


def controller_block_for(die: Optional[str] = None,
                         cfg: Optional[str] = None) -> Dict[str, Any]:
    d, c, _, _ = _context(die, cfg, None)
    return controller_block(load_config_doc(d, c))


# ---------------------------------------------------------------------------
# The DC link
# ---------------------------------------------------------------------------

def battery_v_dc(cfg_doc: Optional[Dict[str, Any]]
                 ) -> Tuple[Optional[float], Optional[str]]:
    """The configuration's battery pack, nominal — ``(V, words)``."""
    batt = (cfg_doc or {}).get("battery")
    if not isinstance(batt, dict) or not batt:
        return None, None
    v_nom = _pos(batt.get("v_nom"))
    if v_nom is not None:
        return v_nom, "the configuration's battery block (pack nominal)"
    v_min, v_max = _pos(batt.get("v_min")), _pos(batt.get("v_max"))
    if v_min is not None and v_max is not None:
        return ((v_min + v_max) / 2.0,
                "the configuration's battery block (no nominal cell voltage "
                "saved — midpoint of the pack's min/max)")
    if v_min is not None:
        return v_min, ("the configuration's battery block (no nominal or max "
                       "saved — the pack's minimum)")
    return None, None


def resolve_v_dc(cfg_doc: Optional[Dict[str, Any]], *,
                 request: Any = None,
                 request_words: str = "the request",
                 controller: Optional[Dict[str, Any]] = None,
                 legacy_record: Optional[Tuple[Any, str]] = None,
                 live_battery_v: Optional[float] = None
                 ) -> Dict[str, Any]:
    """``{V, origin, source}`` — request > the Controller's manual V_dc >
    the configuration's battery (nominal) > the loaded machine's battery
    (``live_battery_v``, a machine with no catalog document) > the legacy
    PWM bus a duty record was solved at (``legacy_record``) > ``None``."""
    v = _pos(request)
    if v is not None:
        return {"V": v, "origin": ORIGIN_REQUEST, "source": request_words}
    for blk, words in ((controller, "the Controller settings sent with the "
                                    "request (manual V_dc)"),
                       (controller_block(cfg_doc),
                        "the Controller settings (manual V_dc)")):
        v = _pos((blk or {}).get("v_dc_V"))
        if v is not None:
            return {"V": v, "origin": ORIGIN_CONTROLLER, "source": words}
    v, words = battery_v_dc(cfg_doc)
    if v is not None:
        return {"V": v, "origin": ORIGIN_CONTROLLER,
                "source": words + " — the Controller's V_dc source"}
    v = _pos(live_battery_v)
    if v is not None:
        return {"V": v, "origin": ORIGIN_CONTROLLER,
                "source": "the machine's battery v_nom — the Controller's "
                          "V_dc source"}
    if legacy_record is not None:
        v = _pos(legacy_record[0])
        if v is not None:
            return {"V": v, "origin": ORIGIN_LEGACY,
                    "source": "migrated from %s (no battery on this "
                              "configuration)" % legacy_record[1]}
    return {"V": None, "origin": None, "source": None}


def _live_battery_v() -> Optional[float]:
    try:
        from motor_ai_sim.config import get_config
        b = (get_config() or {}).get("battery") or {}
        for k in ("v_nom", "v_oc"):
            v = _pos(b.get(k))
            if v is not None:
                return v
    except Exception:                                       # noqa: BLE001
        pass
    return None


def resolve_v_dc_for(die: Optional[str] = None, cfg: Optional[str] = None,
                     **kw: Any) -> Dict[str, Any]:
    d, c, _, active = _context(die, cfg, None)
    doc = load_config_doc(d, c)
    if active and "live_battery_v" not in kw:
        kw["live_battery_v"] = _live_battery_v()
    return resolve_v_dc(doc, **kw)


# ---------------------------------------------------------------------------
# The retired Simulation-tab drive on an external request
# ---------------------------------------------------------------------------

def pwm_request_fields(payload: Dict[str, Any], *,
                       where: str) -> Dict[str, Any]:
    """An EXTERNAL electromagnetic request on the retired ``pwm_voltage``
    drive (an old browser session, a script): its ``v_bus`` / ``f_switch``
    are ACCEPTED but the Controller's own values win where the Controller has
    saved one, and a deprecation is logged.  Returns the payload, updated,
    with ``_drive_sources`` naming what was used.  Anything else — another
    drive, a restore, a ledger probe — passes through untouched.
    """
    if str(payload.get("drive") or "").strip().lower() != "pwm_voltage":
        return payload
    if payload.get("restore") or payload.get("ledger_probe"):
        return payload
    out = dict(payload)
    sent_fs, sent_vb = _pos(out.get("f_switch")), _pos(out.get("v_bus"))
    if sent_fs is not None or sent_vb is not None:
        warn_deprecated(
            "%s: drive='pwm_voltage' with v_bus/f_switch in the request — the "
            "PWM drive is defined in the Controller tab since 2026-09-24; the "
            "Controller's saved carrier / V_dc are used where set" % where)
    blk = controller_block_for()
    ctl_fs, ctl_vb = _pos(blk.get("f_carrier_hz")), _pos(blk.get("v_dc_V"))
    src: Dict[str, str] = {}
    if ctl_fs is not None:
        out["f_switch"] = ctl_fs
        src["f_switch"] = SRC_CONTROLLER_CARRIER
    elif sent_fs is not None:
        src["f_switch"] = "the request (deprecated field — no carrier saved " \
                          "in the Controller)"
    if ctl_vb is not None:
        out["v_bus"] = ctl_vb
        src["v_bus"] = "the Controller settings (manual V_dc)"
    elif sent_vb is not None:
        src["v_bus"] = "the request (deprecated field)"
    out["_drive_sources"] = src
    return out
