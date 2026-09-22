"""Controller routes — ``/api/controller``.

The Controller tab's whole backend:

  ``GET  /devices``            the catalogue rows (the browsable MOSFET table)
  ``GET  /devices/{part}``     one card, whole, with its provenance
  ``POST /devices``            add a card (validated; writes ``config/devices``)
  ``GET  /topologies``         the presets + THIS machine's coils, so the
                               mapping table can be drawn before anything is
                               solved
  ``POST /schematic``          the power schematic for a topology, without
                               solving — what the tab redraws on every change
  ``POST /solve``              losses, junction temperatures, the two
                               efficiencies, the DC-link ripple and one
                               electrical period of the waveform
  ``GET  /last``               what this tab last showed

SOLVER ISOLATION, the rule ``routes/thermal.py`` states and ``routes/bearings``
keeps: **nothing here solves a field.**  It is arithmetic over a device card, a
winding layout read-only and an operating point the caller states (or the duty
record already holds).  It never starts an electromagnetic transient, never
touches the thermal or mechanical caches and never writes a die file.  The one
thing it does write is the duty's ``controller`` block — the same seam every
other kind uses (:func:`duty_results.note_controller`) — and the device card
the "Add device" form posts.

WHERE THE DEFAULTS COME FROM, and it is never ``motor_config.yaml``: the
operating point is the DUTY's (``duty_results`` -> ``coupled`` -> the record's
``inverter`` block and its ``point``), exactly as the project's rule says.  The
response's ``sources`` block names, field by field, whether a number came from
the duty record, from the request, or from a stated default.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query

from motor_ai_sim import duty_results as _DR
from motor_ai_sim import run_history as _RH
from motor_ai_sim.inverter import devices as _dev
from motor_ai_sim.inverter import schematic as _schem
from motor_ai_sim.inverter.losses import ControllerRefusal, solve_controller
from motor_ai_sim.inverter.topology import (TOPOLOGY_PRESETS, TopologyError,
                                            build_topology, coils_from_winding)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/controller", tags=["controller"])

_HISTORY = _RH.history_for("controller.solve")
_RH.register_kind("controller.solve")

#: What ``GET /last`` serves — the tab's own restore, per process.
_LAST: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Refusals, in the project's shape
# ---------------------------------------------------------------------------

def _refuse(message: str, fields: Optional[List[str]] = None,
            code: str = "bad_controller") -> HTTPException:
    return HTTPException(status_code=422, detail={
        "error": code, "message": message, "fields": list(fields or [])})


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------

def _live_machine(body: Dict[str, Any]) -> Dict[str, Any]:
    """Slots, poles, layers and connection — request first, live config second."""
    out: Dict[str, Any] = {}
    src: Dict[str, str] = {}
    try:
        from motor_ai_sim.config import get_config
        cfg = get_config()
        geo = dict(cfg.get("geometry", {}) or {})
        wnd = dict(cfg.get("winding", {}) or {})
    except Exception:                                       # noqa: BLE001
        geo, wnd = {}, {}
    for key, live, default in (("num_slots", geo.get("num_slots"), None),
                               ("num_poles", geo.get("num_poles"), None)):
        if body.get(key) is not None:
            out[key], src[key] = body[key], "the request"
        elif live is not None:
            out[key], src[key] = live, "the loaded machine's geometry"
        else:
            out[key], src[key] = default, "not available"
    if body.get("single_layer") is not None:
        out["single_layer"], src["single_layer"] = bool(body["single_layer"]), "the request"
    else:
        out["single_layer"] = int(wnd.get("layers") or 1) == 1
        src["single_layer"] = "the loaded machine's winding block"
    out["winding_layout"] = body.get("winding_layout")
    if out["winding_layout"]:
        src["winding_layout"] = "the request"
    return {"values": out, "sources": src, "winding": wnd}


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return None if (math.isnan(x) or math.isinf(x)) else x
    except (TypeError, ValueError):
        return None


def _point_label(coupled: Dict[str, Any], kind: str) -> str:
    """What the resolved point is called, for the tab's header line.

    Owner 2026-09-22 (production, CIANO14 50 edited / L15 / rated edited): the
    S1-verified continuous rating REPLACES the coupled record's own ``em`` with
    the S1 machine (``continuous_rating.record_is_s1``) — a controller solved
    from it must say so, never "solved for the duty" as if it were the
    setpoint that was typed on the Simulation tab.
    """
    if kind == "coupled":
        cr = coupled.get("continuous_rating") or {}
        if isinstance(cr, dict) and cr.get("record_is_s1"):
            return "the S1 point"
        if coupled.get("mode") == "limited":
            return "the point at the limit"
        return "the duty's steady point"
    if kind == "pwm":
        return "the PWM run"
    if kind == "standalone":
        return "the standalone electromagnetic solve"
    return "the duty"


def _duty_defaults(die: Optional[str], cfg: Optional[str],
                   duty: Optional[str]) -> Dict[str, Any]:
    """The duty's own operating point and inverter block, or ``{}``.

    NEVER ``motor_config.yaml``: the project's rule is that every run setting
    comes from the duty / Simulation tab.  A delta duty's ``point.I_phase_rms``
    is the LINE current (the records are written that way), so the machine
    phase current is taken from the coupled record's
    ``inverter.I_phase_rms_solved_A`` when it is there and derived otherwise —
    and which of the two it was is reported.

    ``p_ac_W`` — the inverter's AC OUTPUT power, i.e. the motor's electrical
    INPUT power — is never typed by anyone (owner 2026-09-22: «не пойму, куда
    это записывать»).  It is resolved through :func:`report.duty_em_source`
    (the SAME lookup a report page uses: a coupled record whatever state it is
    in — steady, at a limit, or the S1-verified machine a ``solve_to:
    continuous`` run replaced it with — a PWM run, or a plain standalone
    electromagnetic solve) and :func:`report.shaft_view` (the SAME formula
    every table of this document prints: ``P_elec = P_rotor + P_loss_em`` for a
    motor), so a report and this tab can never print two different inputs for
    one duty.  The OLD formula here required the mechanical/bearing block
    (``efficiency_shaft`` + ``P_loss_total_incl_mech_W``) to exist, which is
    absent on a machine with no bearings assigned or a background pass — and
    ``p_ac_W`` does not need a bearing at all, only the rotor power and the
    electromagnetic loss.  The current and the power are read off this SAME
    resolved record — never the coupled record's power beside the Simulation
    tab's setpoint current, which is the mismatch the owner's screenshot showed
    on 2026-09-21.

    ``efficiency_shaft`` (owner 2026-09-22, production, an S1-verified
    continuous run: ``efficiency.shaft`` / ``wall_to_shaft`` came back
    ``None`` although the report prints a shaft efficiency for that exact
    record) is resolved the SAME way, through :func:`report.shaft_view`, on
    whichever ``em`` block ``duty_em_source`` handed back — never the
    persisted top-level ``coupled.efficiency_shaft``, which is only ever
    written by the coupled loop's own iterations and stays ``None`` on a
    record an S1 verification pass, a PWM run or a standalone solve produced
    instead.  With a bearing model (``P_mech_extra_W``, read off the coupled
    record or the em block, whichever carries it) this is the SKF shaft
    efficiency every report table prints; with none assigned it is the
    electromagnetic shaft efficiency ``shaft_view`` itself falls back to —
    never invented here.  Truly unavailable (no rotor power or
    electromagnetic loss at all) leaves the field absent and says why in
    ``efficiency_shaft_note``.
    """
    if not (die and cfg and duty):
        ctx = _DR.active_context()
        if not ctx:
            return {}
        die, cfg, duty = ctx
    die, cfg, duty = str(die), str(cfg), str(duty)
    try:
        node = (_DR.get(die, cfg) or {}).get(duty) or {}
    except Exception:                                       # noqa: BLE001
        node = {}
    if not isinstance(node, dict):
        node = {}

    try:
        from motor_ai_sim.routes import family as _fam
        cfg_doc = _fam.config_doc(die, cfg)
        d_entry = _fam.duty_entry(die, cfg, duty)
    except Exception:                                       # noqa: BLE001
        cfg_doc, d_entry = None, None
    d_entry = d_entry if isinstance(d_entry, dict) else {}

    try:
        from motor_ai_sim import report as _R
        em_src = _R.duty_em_source(die, cfg, duty, cfg_doc, d=d_entry, res=node)
    except Exception:                                       # noqa: BLE001
        em_src = {"em": {}, "kind": "none", "inverter": {}}
    em: Dict[str, Any] = em_src.get("em") or {}
    kind = str(em_src.get("kind") or "none")

    out: Dict[str, Any] = {"die": die, "config": cfg, "duty": duty,
                           "em_source": kind}
    src: Dict[str, str] = {}
    if kind == "none":
        out["_sources"] = src
        return out

    coupled = node.get("coupled") if isinstance(node.get("coupled"), dict) else {}
    thermal = node.get("thermal") if isinstance(node.get("thermal"), dict) else {}
    inv = em_src.get("inverter") or coupled.get("inverter") or thermal.get("inverter") or {}
    point = thermal.get("point") or {}
    where = f"the duty's {kind} record" if kind != "coupled" else "the duty's coupled record"

    sd = str(inv.get("star_delta") or em.get("star_delta") or "star").lower()
    out["star_delta"] = sd
    src["star_delta"] = where

    # THE CURRENT: the SAME record ``em`` (and its ``inverter`` block, when
    # there is one) that ``p_ac_W`` below is read from — never mixed with the
    # Simulation tab's setpoint (``thermal.point``), which is a DIFFERENT
    # record on a run that moved off it (e.g. the S1-verified machine).
    i_solved = (inv.get("I_phase_rms_solved_A") or em.get("I_phase_rms_solved_A")
               or em.get("I1_phase_rms_A") or em.get("I_phase_rms_A"))
    if i_solved is not None:
        out["i_phase_rms_A"] = float(i_solved)
        src["i_phase_rms_A"] = (f"{where} (the current the machine actually "
                                f"drew, from {_point_label(coupled, kind)})")
    elif point.get("I_phase_rms") is not None:
        raw = float(point["I_phase_rms"])
        out["i_phase_rms_A"] = raw / math.sqrt(3.0) if sd == "delta" else raw
        src["i_phase_rms_A"] = (
            "the duty's point (a delta duty's point.I_phase_rms is the LINE "
            "current, so it was divided by sqrt(3))" if sd == "delta"
            else "the duty's point")

    for k_out, k_in, w in (("v_dc_V", "v_dc_V",
                            "the duty's own PWM bus (the real voltage it was "
                            "solved at)"),
                           ("f_carrier_hz", "f_carrier_hz",
                            "the duty's own PWM carrier"),
                           ("modulation_index", "m",
                            "the duty's inverter block (the modulator's own "
                            "ratio; it transfers unchanged to the real "
                            "bridge)")):
        if inv.get(k_in) is not None:
            out[k_out] = float(inv[k_in])
            src[k_out] = w

    rpm = _num(point.get("rpm")) or _num(d_entry.get("rpm")) or _num(em.get("rpm"))
    if rpm is not None:
        out["rpm"] = float(rpm)
        src["rpm"] = "the duty's point"
        try:
            poles = float(_live_machine({})["values"]["num_poles"] or 0)
            if poles:
                out["f_elec_hz"] = float(rpm) / 60.0 * (poles / 2.0)
                src["f_elec_hz"] = "rpm x pole pairs"
        except Exception:                                   # noqa: BLE001
            pass

    # THE FUNDAMENTAL PHASE (COIL) VOLTAGE — the one field a SINE duty (no
    # PWM inverter block at all) needs so its own modulation index and power
    # factor can be derived below (:func:`_build_request`) instead of being
    # required from the web (owner 2026-09-22, production: "Error: send
    # modulation_index ... or power_factor" on a duty solved with sine
    # current).  ``V1_phase_V`` — never ``V_phase_peak_V``/``V_phase_rms_V``,
    # which are the WHOLE waveform's peak/rms, harmonics included — is the
    # harmonic-decomposed fundamental (``simulation/postproc.voltage_harmonics``),
    # and it is already the quantity the bridge must synthesise: in star the
    # phase voltage, in DELTA the coil voltage, which IS the line-to-line
    # voltage the bridge terminals see (the winding IS the line — no separate
    # delta handling needed here, only inside ``pwm.modulation_index`` itself).
    v1 = _num(em.get("V1_phase_V"))
    if v1 is not None and v1 > 0:
        out["v1_phase_peak_V"] = float(v1)
        src["v1_phase_peak_V"] = (
            f"{where} (the fundamental phase/coil voltage the solver "
            "reports, V1_phase_V)")

    # THE POWER: rotor power (from the SAME em block, deriving it off the
    # torque exactly as the report's headline does when the record carries no
    # ``P_mech_W`` of its own) plus the electromagnetic loss —
    # ``report.shaft_view``'s ``P_elec_W``, motor-side.  No bearing block is
    # needed for this number; only ``efficiency_shaft`` (below) is.
    mode = ("generator" if str(d_entry.get("mode")
                              or em.get("op_mode") or "").lower().startswith("gen")
           else "motor")
    t2d = _num(em.get("T_em_avg_Nm"))
    p_mech = _num(em.get("P_mech_W"))
    if p_mech is None and t2d is not None and rpm:
        p_mech = abs(t2d) * 2.0 * math.pi * float(rpm) / 60.0
    em_for_sv = {**em, "P_mech_W": p_mech} if p_mech is not None else em

    # THE BEARING BLOCK, for ``shaft_view`` below — the SAME construction
    # ``report._cpl_shaft_view`` uses for every table of the document, not
    # the persisted top-level ``coupled.efficiency_shaft`` field (owner
    # 2026-09-22, production, CIANO14 50 edited / L15 / rated edited, an
    # S1-verified continuous run): that field is only ever written by the
    # coupled loop's OWN iterations, and stays ``None`` on a record whose
    # answer came from elsewhere afterwards — the S1 verification pass
    # (``routes/coupled.py``, ``continuous_rating['record_is_s1']``) REPLACES
    # ``em`` with the verified machine but never re-derives that top-level
    # field, and a plain PWM or standalone record never had it at all.
    # ``P_mech_extra_W`` is the one number every such pass DOES carry (it is
    # a postprocessing output, not the loop's own bookkeeping) — the coupled
    # record's own first, the em block's (the S1/PWM/standalone machine)
    # second.
    p_extra = _num(coupled.get("P_mech_extra_W"))
    if p_extra is None:
        p_extra = _num(em.get("P_mech_extra_W"))
    brg = ({"has_bearings": True, "P_mech_extra_W": p_extra}
           if p_extra is not None else None)
    sv = _R.shaft_view(em_for_sv, brg, mode)
    if sv.get("P_elec_W") is not None:
        out["p_ac_W"] = float(sv["P_elec_W"])
        src["p_ac_W"] = (f"{where}: report.shaft_view's P_elec_W (rotor power "
                         f"plus the electromagnetic loss) at {_point_label(coupled, kind)} "
                         "— the motor's electrical input power")
    if t2d is not None:
        out["torque_Nm"] = t2d

    # ``efficiency_shaft`` rides ALONGSIDE, for the wall-to-shaft column — but
    # ``p_ac_W`` above never depends on it existing.  ``shaft_view`` itself
    # decides what "the ONE shaft efficiency" means here: with a bearing
    # model, bearings + windage taken off the shaft (SKF model); with none
    # assigned, the electromagnetic shaft efficiency the report prints for
    # this machine instead (never invented, never left silently absent when
    # the report would print something) — see ``shaft_view``'s own
    # docstring.  Only when even that cannot be formed (no rotor power / no
    # electromagnetic loss on this record) does the field stay absent, with
    # a reason.
    if brg is not None and sv.get("eta_shaft") is not None:
        out["efficiency_shaft"] = float(sv["eta_shaft"])
        src["efficiency_shaft"] = (
            f"{where}: report.shaft_view's eta_shaft (bearings + windage "
            f"taken off the shaft, SKF model) at {_point_label(coupled, kind)}")
    elif sv.get("eta_em") is not None:
        out["efficiency_shaft"] = float(sv["eta_em"])
        src["efficiency_shaft"] = (
            f"{where}: report.shaft_view's eta_em — this configuration "
            "names no bearings, so the electromagnetic shaft efficiency is "
            "the one the report prints for this machine")
    else:
        out["efficiency_shaft_note"] = (
            f"{where} carries no rotor power or electromagnetic loss to "
            "balance, so no shaft efficiency could be resolved")

    if out.get("i_phase_rms_A") is not None and out.get("p_ac_W") is not None:
        out["solved_for_line"] = (
            f"solved for {_point_label(coupled, kind)}: "
            f"{out['i_phase_rms_A']:.1f} A rms · "
            f"{out['p_ac_W'] / 1000.0:.2f} kW in")
    out["_sources"] = src
    return out


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@router.get("/devices")
def get_devices(
    topology: Optional[str] = Query(None, description="size the suggestion for this topology"),
    die: Optional[str] = Query(None), config: Optional[str] = Query(None),
    duty: Optional[str] = Query(None),
    i_switch_rms_A: Optional[float] = Query(
        None, description="override the per-switch current the suggestion uses"),
) -> Dict[str, Any]:
    """The catalogue — one row per card in ``config/devices/``.

    With a duty (explicit, or the loaded one) and a topology, every row also
    carries ``suggested_parallel``: how many of THAT part one switch position
    would need on the datasheet's continuous current rating alone
    (``ceil(I_switch_rms / I_DDC@100 degC)``).  It is a first cut for reading
    the table at a glance — the binding limit is the junction temperature, and
    only ``POST /solve`` knows that.
    """
    i_sw = i_switch_rms_A
    basis = "the request" if i_sw is not None else None
    if i_sw is None:
        i_sw, basis = _switch_current_for(topology, die, config, duty)
    return {"dir": str(_dev.devices_dir()),
            "i_switch_rms_A": None if i_sw is None else round(i_sw, 1),
            "i_switch_rms_basis": basis,
            "suggestion_note": (
                "devices per switch on the continuous CURRENT rating alone "
                "(I_DDC at a 100 degC case); the junction temperature usually "
                "asks for more — press Solve"),
            "devices": _dev.list_devices(i_switch_rms_A=i_sw)}


def _switch_current_for(topology: Optional[str], die: Optional[str],
                        config: Optional[str], duty: Optional[str]
                        ) -> Tuple[Optional[float], Optional[str]]:
    """The rms current ONE switch position carries, for this topology and duty.

    A switch conducts the leg current for about half the period, so its own
    full-period rms is ``I_leg / sqrt(2)`` — the same definition the solve
    reports, so the table and the result cannot disagree.
    """
    d = _duty_defaults(die, config, duty)
    i_ph = d.get("i_phase_rms_A")
    if not i_ph:
        return None, None
    sd = str(d.get("star_delta") or "star")
    preset = str(topology or "one_3ph").strip().lower()
    root3 = math.sqrt(3.0)
    if preset == "h_bridge":
        i_leg = float(i_ph)                    # the coil current, both legs
    else:
        i_leg = float(i_ph) * (root3 if sd == "delta" else 1.0)
    where = (f"{d.get('die')} · {d.get('config')} · {d.get('duty')}"
             if d.get("die") else "the loaded duty")
    return i_leg / math.sqrt(2.0), where


@router.get("/devices/{part}")
def get_device_card(part: str) -> Dict[str, Any]:
    try:
        card = _dev.get_device(part)
    except _dev.CardError as exc:
        raise _refuse(str(exc), ["part"], code="unknown_device")
    return {"part": card.part, "card": card.doc, "row": card.row(),
            "provenance": card.provenance(),
            "file": str(card.source_file) if card.source_file else None}


@router.get("/devices/{part}/image")
def get_device_image(part: str):
    """The LOCAL package photo a card points at, if the owner placed one.

    ``image: <file name>`` on the card, the file in ``config/devices/img/``.
    Nothing is ever downloaded, and the name is resolved strictly inside that
    folder — a card is data, and data does not get to name a path.
    """
    from fastapi.responses import FileResponse
    try:
        card = _dev.get_device(part)
    except _dev.CardError as exc:
        raise _refuse(str(exc), ["part"], code="unknown_device")
    name = str(card.doc.get("image") or "").strip()
    if not name:
        raise HTTPException(status_code=404,
                            detail=f"{card.part} has no local image; the tab "
                                   f"draws the generated package outline")
    root = (_dev.devices_dir() / "img").resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404,
                            detail=f"{name} is not a file in {root}")
    return FileResponse(str(path))


@router.post("/devices")
def add_device_card(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Add (or replace) a card.

    The ONLY writer of ``config/devices/``.  It validates first and refuses a
    half-filled card outright — a card that loads and then returns ``None``
    deep inside a loss sum is the silent wrong answer this project does not
    ship.  No datasheet is scraped and none ever will be here: somebody
    transcribes the numbers and says where each block came from.
    """
    if body.get("card_yaml"):
        import yaml as _yaml
        try:
            card = _yaml.safe_load(str(body["card_yaml"]))
        except Exception as exc:                            # noqa: BLE001
            raise _refuse(f"this is not valid YAML: {exc}", ["card_yaml"],
                          code="bad_device_card")
    elif isinstance(body.get("card"), dict):
        card = body["card"]
    else:
        card = body
    bad = _dev.validate_card(card)
    if bad:
        raise _refuse("this card cannot be stored: " + "; ".join(bad),
                      ["card"], code="bad_device_card")
    try:
        path = _dev.write_card(dict(card), overwrite=bool(body.get("overwrite")))
    except _dev.CardError as exc:
        raise _refuse(str(exc), ["card"], code="bad_device_card")
    return {"ok": True, "part": card.get("part"), "file": str(path),
            "devices": _dev.list_devices()}


# ---------------------------------------------------------------------------
# Topologies
# ---------------------------------------------------------------------------

@router.get("/topologies")
def get_topologies(num_slots: Optional[int] = Query(None),
                   num_poles: Optional[int] = Query(None),
                   single_layer: Optional[bool] = Query(None),
                   ) -> Dict[str, Any]:
    """The presets, and THIS machine's coils as the winding builder names them."""
    mach = _live_machine({"num_slots": num_slots, "num_poles": num_poles,
                          "single_layer": single_layer})
    v = mach["values"]
    coils: List[Dict[str, Any]] = []
    error = None
    if v.get("num_slots") and v.get("num_poles"):
        try:
            coils = [c.as_dict() for c in coils_from_winding(
                int(v["num_slots"]), int(v["num_poles"]),
                single_layer=bool(v["single_layer"]),
                layout_str=v.get("winding_layout"))]
        except TopologyError as exc:
            error = str(exc)
    return {"presets": [{"id": k, **vv} for k, vv in TOPOLOGY_PRESETS.items()],
            "machine": {**v, "sources": mach["sources"]},
            "star_delta": (mach["winding"] or {}).get("star_delta"),
            "coils": coils, "error": error}


# ---------------------------------------------------------------------------
# Settings — the tab's own FORM, persisted with the configuration
# ---------------------------------------------------------------------------

def _config_doc_for(die: Optional[str], cfg: Optional[str]) -> Dict[str, Any]:
    """The configuration's yaml document, or ``{}`` — die/cfg resolved from
    the active context when not given, the one convention every resolver in
    this module (``_duty_defaults``, this one, the battery lookup below)
    shares, so they can never disagree about WHICH machine is loaded."""
    d, cf = die, cfg
    if not (d and cf):
        ctx = _DR.active_context()
        if not ctx:
            return {}
        d, cf = ctx[0], ctx[1]
    try:
        from motor_ai_sim.routes import family as _fam
        doc = _fam.config_doc(str(d), str(cf))
    except Exception:                                       # noqa: BLE001
        return {}
    return doc if isinstance(doc, dict) else {}


def _controller_settings_for(die: Optional[str], cfg: Optional[str]) -> Dict[str, Any]:
    """The Controller tab's own settings, as they were last saved WITH the
    active configuration (owner 2026-09-22: "при сохранении мотора текущий
    контроллер тоже должен сохраняться со всеми настройками") — written by
    ``PATCH /api/family/config/{die}/{cfg}/controller``, one physical
    controller box per configuration, the same footing as ``battery``.

    ``{}`` — never a 404, never a validation error — when nothing has ever
    been saved: the tab reads that as "use its own defaults", exactly the
    rule ``missing block = the tab's defaults, no error`` the owner stated.
    Never a solve RESULT: that is ``duty_results``'s own ``controller`` kind
    (the losses/thermal/limits table ``POST /solve`` writes) and lives beside
    this, never inside it.
    """
    block = _config_doc_for(die, cfg).get("controller")
    return dict(block) if isinstance(block, dict) else {}


def _battery_v_dc(die: Optional[str], cfg: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """The configuration's own battery pack, nominal — ``(v_dc, basis)``, or
    ``(None, None)`` when the configuration names no battery at all.

    Owner 2026-09-22 (production, "Error: v_dc_V is required"): «почему это
    всё не берётся из мотора или из батарейки?» — this is the "или из
    батарейки" half.  ``v_nom`` (``set_battery``'s own pack total, ``n_cells
    x v_cell_nom``) is preferred; a pack that never named a nominal CELL
    voltage falls back to the midpoint of its min/max, then to its minimum —
    every step says which it used, because a nominal and a floor read the
    same on the tab otherwise.
    """
    batt = _config_doc_for(die, cfg).get("battery")
    if not isinstance(batt, dict) or not batt:
        return None, None
    v_nom = _num(batt.get("v_nom"))
    if v_nom is not None:
        return v_nom, "the configuration's battery block (pack nominal)"
    v_min, v_max = _num(batt.get("v_min")), _num(batt.get("v_max"))
    if v_min is not None and v_max is not None:
        return ((v_min + v_max) / 2.0,
                "the configuration's battery block (no nominal cell voltage "
                "saved — midpoint of the pack's min/max)")
    if v_min is not None:
        return v_min, ("the configuration's battery block (no nominal or max "
                       "saved — the pack's minimum)")
    return None, None


@router.get("/settings")
def get_controller_settings(die: Optional[str] = Query(None),
                            config: Optional[str] = Query(None)
                            ) -> Dict[str, Any]:
    """The Controller tab's own settings — see :func:`_controller_settings_for`."""
    return _controller_settings_for(die, config)


@router.get("/point")
def get_resolved_point(die: Optional[str] = Query(None), config: Optional[str] = Query(None),
                       duty: Optional[str] = Query(None)) -> Dict[str, Any]:
    """The point ``POST /solve`` WOULD use right now — before anything is
    solved, and without a device/topology chosen yet.

    Owner 2026-09-22: the tab must say where it is about to get every number
    from, not only after a failed Solve.  Built on the exact same
    :func:`_build_request` a real solve runs, so the preview line can never
    name a different source than the answer that follows it.
    """
    req, sources = _build_request({"die": die, "config": config, "duty": duty})
    ctx = req.get("_context") or {}
    i_ph, p_ac = req.get("i_phase_rms_A"), req.get("p_ac_W")
    v_dc, f_sw, sd = req.get("v_dc_V"), req.get("f_carrier_hz"), req.get("star_delta")
    m, pf = req.get("modulation_index"), req.get("power_factor")
    line = None
    if i_ph is not None and p_ac is not None:
        if m is not None and pf is not None:
            mod_txt = (f"m {float(m):.2f} · cos φ {float(pf):.2f} "
                       f"({sources.get('modulation_index', 'unknown source')})")
        elif m is not None:
            mod_txt = f"m {float(m):.2f} ({sources.get('modulation_index', 'unknown source')})"
        elif pf is not None:
            mod_txt = f"cos φ {float(pf):.2f} ({sources.get('power_factor', 'unknown source')})"
        else:
            mod_txt = "no modulation index known"
        parts = [str(ctx.get("duty") or "the loaded duty"),
                 f"{float(i_ph):.1f} A rms",
                 (f"{float(v_dc):.1f} V ({sources.get('v_dc_V', 'unknown source')})"
                  if v_dc is not None else "no DC bus voltage known"),
                 str(sd or "star"),
                 (f"{float(f_sw) / 1000.0:.0f} kHz ({sources.get('f_carrier_hz', 'unknown source')})"
                  if f_sw is not None else "no carrier known"),
                 mod_txt]
        line = "solving for: " + " · ".join(parts)
    return {"die": ctx.get("die"), "config": ctx.get("config"), "duty": ctx.get("duty"),
            "i_phase_rms_A": i_ph, "p_ac_W": p_ac, "v_dc_V": v_dc,
            "f_carrier_hz": f_sw, "star_delta": sd, "modulation_index": m,
            "power_factor": pf, "sources": sources, "line": line}


@router.post("/schematic")
def post_schematic(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """The power schematic for a topology, without solving anything.

    The CONNECTION is resolved exactly as ``/solve`` resolves it — the duty's
    coupled record first, the request over it — because a picture that draws a
    star point on a machine the solve treats as a delta is a picture that lies.
    """
    mach = _live_machine(body)["values"]
    if not (mach.get("num_slots") and mach.get("num_poles")):
        raise _refuse("no machine: send num_slots and num_poles, or load one",
                      ["num_slots", "num_poles"], code="no_machine")
    sd = body.get("star_delta")
    if not sd:
        sd = (_duty_defaults(body.get("die"), body.get("config"),
                             body.get("duty")) or {}).get("star_delta")
    try:
        coils = coils_from_winding(int(mach["num_slots"]), int(mach["num_poles"]),
                                   single_layer=bool(mach["single_layer"]),
                                   layout_str=mach.get("winding_layout"))
        topo = build_topology(
            preset=str(body.get("topology") or "one_3ph"),
            coils=coils,
            star_delta=str(sd or "star"),
            device=body.get("device"),
            devices_parallel=int(body.get("devices_parallel") or 1),
            v_dc_V=body.get("v_dc_V"),
            h_bridge_modulation=str(body.get("h_bridge_modulation") or "unipolar"),
            mapping=body.get("mapping"),
            devices_parallel_by_bridge=body.get("devices_parallel_by_bridge"))
    except TopologyError as exc:
        raise _refuse(str(exc), ["topology", "mapping"], code="bad_topology")
    return {"topology": topo.as_dict(),
            "svg": _schem.schematic_svg(topo, v_dc_V=body.get("v_dc_V"),
                                        device=body.get("device"))}


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------

#: The PWM carrier when nothing else names one at all — the duty's own PWM
#: setup and the saved controller settings both come first (see
#: ``_build_request``); this only fires when NEITHER exists, so a fresh
#: configuration with no PWM history and no saved controller can still be
#: solved.  A plain, conservative SiC number — no measurement backs it, and
#: the response's ``sources`` names it as this module's own default, never
#: as anything read off the machine.
DEFAULT_CARRIER_HZ = 20_000.0

#: Everything that changes the answer, rounded, in one place — see
#: ``run_history``'s contract on why the rounding lives in the key-builder.
_KEY_FIELDS = ("num_slots", "num_poles", "single_layer", "winding_layout",
               "star_delta", "device", "devices_parallel", "v_dc_V",
               "i_phase_rms_A", "p_ac_W", "f_elec_hz", "f_carrier_hz",
               "modulation_index", "power_factor", "efficiency_shaft",
               "topology", "set_split", "h_bridge_modulation",
               "dead_time_us", "v_gs_on_V", "v_gs_off_V", "r_g_ext_ohm",
               "r_tim_k_w", "r_spread_k_w", "e_oss_policy",
               "samples_per_carrier")


def _history_key(req: Dict[str, Any]) -> str:
    p: Dict[str, Any] = {}
    for k in _KEY_FIELDS:
        p[k] = _RH.round_floats(req.get(k), 6) if req.get(k) is not None else None
    p["cooling"] = _RH.round_floats(dict(req.get("cooling") or {}), 6)
    p["par_by_bridge"] = sorted(
        f"{k}={v}" for k, v in (req.get("devices_parallel_by_bridge") or {}).items())
    p["mapping"] = sorted(
        [f"{r.get('coil')}>{r.get('bridge')}/{r.get('leg')}"
         for r in (req.get("mapping") or []) if isinstance(r, dict)])
    return _RH.make_key("controller.solve", p)


def _summary(req: Dict[str, Any], out: Dict[str, Any]) -> str:
    t = out.get("thermal") or {}
    e = out.get("efficiency") or {}
    return (f"{out.get('device')} x{req.get('devices_parallel')} · "
            f"{(out.get('topology') or {}).get('preset_label')} · "
            f"{(out.get('losses') or {}).get('total_W')} W · "
            f"T_j {t.get('t_j_max_c')} degC · "
            f"eta_inv {e.get('inverter')}")


def _build_request(body: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """The solver's request, with a ``sources`` map for every resolved field.

    AUDIT, owner 2026-09-22 (production, "Error: v_dc_V is required" —
    «почему это всё не берётся из мотора или из батарейки? проверь всё»):
    nothing here may be REQUIRED from the web except the controller's own
    choices — topology/mapping, device, N in parallel, R_g, dead time,
    cooling.  Every machine/point value is resolved server-side and named in
    ``sources``, in this priority, highest first:

      the request (whatever is on screen right now)
        > the saved controller settings (``PATCH .../controller`` — a
          deliberate choice, e.g. a manual V_dc override)
        > the duty's own PWM record (what it was ACTUALLY solved at)
        > the configuration's battery pack, nominal (V_dc only)
        > this module's own stated default (carrier only — see
          ``DEFAULT_CARRIER_HZ``)

    V_dc and the carrier are handled OUTSIDE the generic loop below because
    their priority order differs from every other field's (and from each
    other's — the duty's PWM record outranks the saved settings for the
    carrier, the settings outrank the duty for V_dc: a saved manual bus is a
    deliberate override, a saved manual carrier is a fallback for a duty that
    has never run PWM at all).
    """
    mach = _live_machine(body)
    req: Dict[str, Any] = dict(mach["values"])
    sources: Dict[str, str] = dict(mach["sources"])

    duty = _duty_defaults(body.get("die"), body.get("config"), body.get("duty"))
    duty_src = duty.pop("_sources", {}) if duty else {}
    ctrl = _controller_settings_for(body.get("die"), body.get("config"))
    ctrl_cooling = ctrl.get("cooling") if isinstance(ctrl.get("cooling"), dict) else {}

    for key in ("star_delta", "i_phase_rms_A", "p_ac_W", "f_elec_hz",
                "modulation_index", "efficiency_shaft", "rpm"):
        if body.get(key) is not None:
            req[key], sources[key] = body[key], "the request"
        elif duty.get(key) is not None:
            req[key], sources[key] = duty[key], duty_src.get(key, "the duty record")
    # WHY, when it stays absent: the one case ``_duty_defaults`` could not
    # form even the electromagnetic shaft efficiency (no bearings AND no
    # rotor power/loss to balance) — never left for the panel to guess at.
    if req.get("efficiency_shaft") is None and duty.get("efficiency_shaft_note"):
        req["_efficiency_shaft_note"] = duty["efficiency_shaft_note"]
    if body.get("power_factor") is not None:
        req["power_factor"] = body["power_factor"]
        sources["power_factor"] = "the request"
        req.pop("modulation_index", None)

    # ── V_DC: request > saved manual override > the duty's own PWM bus (the
    # REAL voltage it was solved at) > the configuration's battery, nominal.
    if body.get("v_dc_V") is not None:
        req["v_dc_V"], sources["v_dc_V"] = body["v_dc_V"], "the request"
    elif ctrl.get("v_dc_V") is not None:
        req["v_dc_V"] = ctrl["v_dc_V"]
        sources["v_dc_V"] = "the saved controller settings (manual V_dc)"
    elif duty.get("v_dc_V") is not None:
        req["v_dc_V"] = duty["v_dc_V"]
        sources["v_dc_V"] = duty_src.get("v_dc_V", "the duty's own PWM bus")
    else:
        v_batt, batt_basis = _battery_v_dc(body.get("die"), body.get("config"))
        if v_batt is not None:
            req["v_dc_V"], sources["v_dc_V"] = v_batt, batt_basis

    # ── CARRIER: request > the duty's own PWM carrier (what it was actually
    # solved at) > a saved controller default > this module's plain default —
    # always SOMETHING, so a solve never dies on a raw "is required".
    if body.get("f_carrier_hz") is not None:
        req["f_carrier_hz"], sources["f_carrier_hz"] = body["f_carrier_hz"], "the request"
    elif duty.get("f_carrier_hz") is not None:
        req["f_carrier_hz"] = duty["f_carrier_hz"]
        sources["f_carrier_hz"] = duty_src.get("f_carrier_hz", "the duty's own PWM carrier")
    elif ctrl.get("f_carrier_hz") is not None:
        req["f_carrier_hz"] = ctrl["f_carrier_hz"]
        sources["f_carrier_hz"] = "the saved controller settings"
    else:
        req["f_carrier_hz"] = DEFAULT_CARRIER_HZ
        sources["f_carrier_hz"] = ("this module's stated default (%.0f kHz — no "
                                   "PWM history and no saved carrier)"
                                   % (DEFAULT_CARRIER_HZ / 1000.0))

    # ── MODULATION INDEX / POWER FACTOR (owner 2026-09-22, production,
    # Controller -> Solve on a duty solved with plain sine current: "Error:
    # send modulation_index ... or power_factor — the bridge's duty cycle
    # cannot be guessed from the current alone").  Same class of defect as
    # v_dc_V/p_ac_W above — resolved here, never required from the web:
    #
    #   the request (the generic loop above; a manual ``power_factor`` there
    #   already popped ``modulation_index``)
    #     > a saved manual override (a deliberate choice, same footing as the
    #       saved manual V_dc)
    #     > the duty's own PWM record (the generic loop above, from
    #       ``inv.get("m")`` — what the bridge was ACTUALLY told to do)
    #     > derived from the EM record's fundamental voltage/current and the
    #       V_dc just resolved above — the ONLY way a duty solved on plain
    #       sine current (no PWM at all) gets a modulation index without
    #       anyone typing physics.
    #
    # Derivation: ``pwm.modulation_index()`` — the SAME m the project's own
    # ideal-PWM path (``routes/coupled.py::_inverter_settings``) computes for
    # a delta machine's star-equivalent substitution — against ``v1``, the
    # solved fundamental (``V1_phase_V`` above; in delta this IS the coil/line
    # voltage, so no extra star/delta handling belongs here, only inside that
    # function).  Power factor is the displacement one, off the SAME
    # fundamentals: P_elec (the same ``p_ac_W`` every other quantity in this
    # module reads, never a different power) over the fundamental apparent
    # power 3*V1_rms*I1_rms — algebraically the identical ratio
    # ``solve_controller`` recomputes internally once it has ``m``, so the
    # two never disagree.  A current-drive sine run imposes an exactly
    # sinusoidal current, so its rms current IS its fundamental — the
    # already-resolved ``i_phase_rms_A`` needs no separate "I1" lookup.
    if req.get("modulation_index") is None and req.get("power_factor") is None:
        if ctrl.get("modulation_index") is not None:
            req["modulation_index"] = ctrl["modulation_index"]
            sources["modulation_index"] = (
                "the saved controller settings (manual modulation index)")
        elif ctrl.get("power_factor") is not None:
            req["power_factor"] = ctrl["power_factor"]
            sources["power_factor"] = (
                "the saved controller settings (manual power factor)")
        else:
            v1 = duty.get("v1_phase_peak_V")
            i1 = req.get("i_phase_rms_A")
            p_ac = req.get("p_ac_W")
            v_dc = req.get("v_dc_V")
            if v1 is not None and i1 and p_ac is not None and v_dc:
                from motor_ai_sim.simulation.pwm import \
                    modulation_index as _modulation_index
                sd = str(req.get("star_delta") or "star")
                m = _modulation_index(float(v1), float(v_dc), star_delta=sd)
                v1_rms = float(v1) / math.sqrt(2.0)
                s3 = 3.0 * v1_rms * float(i1)
                pf = (float(p_ac) / s3) if s3 > 0 else None
                v1_where = duty_src.get(
                    "v1_phase_peak_V",
                    "the duty's electromagnetic record's fundamental voltage")
                req["modulation_index"] = round(m, 4)
                sources["modulation_index"] = (
                    f"{v1_where} — m = 2*V1/(V_dc*(sqrt3 if delta)), the "
                    "duty was solved on sine current, not PWM")
                if pf is not None:
                    req["power_factor"] = round(max(0.0, min(1.0, pf)), 4)
                    sources["power_factor"] = (
                        f"{v1_where}: cos phi = P_elec / (3 * V1_rms * "
                        "I1_rms)")

    defaults = {"device": (body.get("device") or ctrl.get("device") or _default_device()),
                "devices_parallel": (body.get("devices_parallel")
                                     or ctrl.get("devices_parallel") or 1),
                "topology": (body.get("topology") or ctrl.get("topology")
                            or "one_3ph"),
                "set_split": (body.get("set_split") or ctrl.get("set_split")
                             or "series_split"),
                "h_bridge_modulation": (body.get("h_bridge_modulation")
                                        or ctrl.get("h_bridge_modulation")
                                        or "unipolar"),
                "dead_time_us": (body.get("dead_time_us")
                                 if body.get("dead_time_us") is not None
                                 else ctrl.get("dead_time_us")),
                "v_gs_on_V": body.get("v_gs_on_V"),
                "v_gs_off_V": (body.get("v_gs_off_V")
                               if body.get("v_gs_off_V") is not None
                               else ctrl.get("v_gs_off_V")),
                "r_g_ext_ohm": (body.get("r_g_ext_ohm")
                                if body.get("r_g_ext_ohm") is not None
                                else ctrl.get("r_g_ext_ohm")),
                "r_tim_k_w": (body.get("r_tim_k_w")
                              if body.get("r_tim_k_w") is not None
                              else ctrl_cooling.get("r_tim_k_w")),
                "r_spread_k_w": body.get("r_spread_k_w"),
                "e_oss_policy": body.get("e_oss_policy") or "included_in_eon",
                "samples_per_carrier": body.get("samples_per_carrier"),
                "mapping": body.get("mapping") or (ctrl.get("mapping") or None),
                "devices_parallel_by_bridge":
                    dict(body.get("devices_parallel_by_bridge")
                         or ctrl.get("devices_parallel_by_bridge") or {}) or None,
                "cooling": {**ctrl_cooling, **dict(body.get("cooling") or {})}}
    for k, v in defaults.items():
        if v is not None:
            req[k] = v
            sources.setdefault(k, "the request" if body.get(k) is not None
                               else "the saved controller settings"
                               if (k in ctrl or (k == "r_tim_k_w" and "r_tim_k_w" in ctrl_cooling))
                               else "this module's stated default")
    if duty.get("die"):
        req["_context"] = {"die": duty["die"], "config": duty["config"],
                           "duty": duty["duty"]}
    # WHICH POINT this solve is for, and whether the duty has an answer at all
    # — the route uses the second to decide between solving and refusing with
    # the plain-English sentence (never the raw "p_ac_W is required").
    req["_solved_for"] = duty.get("solved_for_line")
    req["_em_source"] = duty.get("em_source")
    return req, sources


def _default_device() -> str:
    rows = [r for r in _dev.list_devices() if not r.get("error")]
    return str(rows[0]["part"]) if rows else ""


@router.post("/solve")
def post_solve(body: Dict[str, Any] = Body(default={}),
               fresh: bool = Query(False, description="ignore the stored answer"),
               ) -> Dict[str, Any]:
    """Solve this controller on this duty's point."""
    t0 = time.time()
    req, sources = _build_request(body or {})
    ctx = req.pop("_context", None)
    solved_for = req.pop("_solved_for", None)
    em_source = req.pop("_em_source", None)
    # AUDIT, owner 2026-09-22 ("Error: v_dc_V is required" — «проверь всё»):
    # every value that is genuinely missing gets ONE plain sentence here,
    # never the physics module's raw "<field> is required" — the panel would
    # otherwise show that as its status line with no way to know what to do.
    if req.get("num_slots") is None or req.get("num_poles") is None:
        raise _refuse(
            "no machine is loaded — open a motor before solving the controller",
            ["num_slots", "num_poles"], code="no_machine")
    # ``p_ac_W`` is never typed by anyone: the route resolves it, together
    # with the phase current, from the duty's own electromagnetic record.
    if req.get("p_ac_W") is None or req.get("i_phase_rms_A") is None:
        raise _refuse(
            "run the Simulation/coupled solve for this duty first — the "
            "controller needs its electrical input power and phase current",
            ["p_ac_W", "i_phase_rms_A"], code="no_duty_record")
    if req.get("f_elec_hz") is None:
        raise _refuse(
            "the duty's speed (rpm) could not be resolved — set it on the "
            "Simulation tab and save the duty, or run the coupled solve",
            ["f_elec_hz"], code="no_duty_record")
    # V_dc: the configuration's battery, the duty's own PWM bus and a saved
    # manual override are all tried in _build_request — this is what is left
    # when a configuration names NONE of the three («почему это всё не
    # берётся из мотора или из батарейки?» — because there was nothing to
    # take it from).
    if req.get("v_dc_V") is None:
        raise _refuse(
            "no DC bus voltage is known for this motor — add a battery to "
            "the configuration, or set V_dc in the controller settings",
            ["v_dc_V"], code="no_v_dc_source")
    # Modulation index / power factor: resolved in _build_request from the
    # duty's own PWM record, a saved manual override, or (for a duty solved
    # on plain sine current) the EM record's fundamental voltage — see the
    # block above the "defaults" dict there.  What is left here is a record
    # so old it carries none of those (no V1_phase_V at all): never the
    # physics module's raw "send modulation_index ... or power_factor",
    # which names an internal field, but one sentence saying what to do.
    if req.get("modulation_index") is None and req.get("power_factor") is None:
        raise _refuse(
            "this duty's electromagnetic record has no voltage saved (an "
            "old run, from before the fundamental voltage was recorded) — "
            "re-run the Simulation for this duty to get a modulation index",
            ["modulation_index", "power_factor"], code="no_modulation_source")
    key = _history_key(req)

    if not fresh:
        hit = _HISTORY.get(key)
        if hit is not None:
            out = dict(hit["payload"])
            out.update({"cached": True, "served_from_history": True,
                        "computed_at": hit["entry"].get("computed_at"),
                        "history_key": key})
            _LAST.clear(); _LAST.update(out)
            return out

    try:
        out = solve_controller(req)
    except ControllerRefusal as exc:
        raise _refuse(str(exc), exc.fields, code=exc.code)
    except Exception as exc:                                # noqa: BLE001
        log.exception("controller solve failed")
        raise HTTPException(status_code=500, detail=str(exc))

    out["sources"] = sources
    out["context"] = ctx
    # WHICH POINT this answer is for — printed as the tab's header line so a
    # run that used the S1-verified machine (or one at a limit) never reads
    # like a plain solve of the setpoint that was typed on the Simulation tab.
    out["solved_for"] = solved_for
    out["em_source"] = em_source
    out["elapsed_s"] = round(time.time() - t0, 3)
    out["history_key"] = key
    out["cached"] = False
    try:
        topo_obj = _rebuild_topology(req)
        out["schematic_svg"] = _schem.schematic_svg(
            topo_obj, v_dc_V=req.get("v_dc_V"), device=out.get("device"))
    except Exception as exc:                                # noqa: BLE001
        log.warning("controller: schematic not drawn (%s)", exc)
        out["schematic_svg"] = None

    _HISTORY.put(key, params={k: req.get(k) for k in _KEY_FIELDS},
                 summary=_summary(req, out), payload=out,
                 extra={"context": ctx})
    if ctx:
        _DR.note_controller(out, ctx["die"], ctx["config"], ctx["duty"])
    _LAST.clear(); _LAST.update(out)
    return out


def _rebuild_topology(req: Dict[str, Any]):
    coils = coils_from_winding(int(req["num_slots"]), int(req["num_poles"]),
                               single_layer=bool(req.get("single_layer", True)),
                               layout_str=req.get("winding_layout"))
    return build_topology(preset=str(req.get("topology") or "one_3ph"),
                          coils=coils,
                          star_delta=str(req.get("star_delta") or "star"),
                          device=req.get("device"),
                          devices_parallel=int(req.get("devices_parallel") or 1),
                          v_dc_V=req.get("v_dc_V"),
                          h_bridge_modulation=str(
                              req.get("h_bridge_modulation") or "unipolar"),
                          mapping=req.get("mapping"),
                          devices_parallel_by_bridge=req.get(
                              "devices_parallel_by_bridge"))


@router.get("/last")
def get_last() -> Dict[str, Any]:
    """What this tab last showed — ``{}`` when nothing has been solved."""
    return dict(_LAST)


@router.get("/history")
def get_history(limit: int = Query(10, ge=1, le=50)) -> Dict[str, Any]:
    return {"kind": "controller.solve",
            "entries": _HISTORY.list(limit=limit)}


def _load_history_entry(entry: Dict[str, Any],
                        payload: Dict[str, Any]) -> Dict[str, Any]:
    """A History-popover click behaves exactly like the route's own hit."""
    out = dict(payload)
    out.update({"cached": True, "served_from_history": True,
                "computed_at": entry.get("computed_at"),
                "history_key": entry.get("key")})
    _LAST.clear(); _LAST.update(out)
    return out


_RH.register_loader("controller.solve", _load_history_entry)
