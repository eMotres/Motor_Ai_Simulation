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


def _duty_defaults(die: Optional[str], cfg: Optional[str],
                   duty: Optional[str]) -> Dict[str, Any]:
    """The duty's own operating point and inverter block, or ``{}``.

    NEVER ``motor_config.yaml``: the project's rule is that every run setting
    comes from the duty / Simulation tab.  A delta duty's ``point.I_phase_rms``
    is the LINE current (the records are written that way), so the machine
    phase current is taken from the coupled record's
    ``inverter.I_phase_rms_solved_A`` when it is there and derived otherwise —
    and which of the two it was is reported.
    """
    if not (die and cfg and duty):
        ctx = _DR.active_context()
        if not ctx:
            return {}
        die, cfg, duty = ctx
    try:
        node = (_DR.get(str(die), str(cfg)) or {}).get(str(duty)) or {}
    except Exception:                                       # noqa: BLE001
        return {}
    if not isinstance(node, dict) or not node:
        return {}
    coupled = node.get("coupled") if isinstance(node.get("coupled"), dict) else {}
    thermal = node.get("thermal") if isinstance(node.get("thermal"), dict) else {}
    inv = (coupled.get("inverter") or thermal.get("inverter") or {})
    point = thermal.get("point") or {}
    em = coupled.get("em") or {}
    out: Dict[str, Any] = {"die": die, "config": cfg, "duty": duty}
    src: Dict[str, str] = {}

    sd = str(inv.get("star_delta") or "star").lower()
    out["star_delta"] = sd
    src["star_delta"] = "the duty's coupled record"

    i_solved = inv.get("I_phase_rms_solved_A")
    if i_solved is not None:
        out["i_phase_rms_A"] = float(i_solved)
        src["i_phase_rms_A"] = ("the duty's coupled record "
                                "(inverter.I_phase_rms_solved_A — the current "
                                "the machine actually drew)")
    elif point.get("I_phase_rms") is not None:
        raw = float(point["I_phase_rms"])
        out["i_phase_rms_A"] = raw / math.sqrt(3.0) if sd == "delta" else raw
        src["i_phase_rms_A"] = (
            "the duty's point (a delta duty's point.I_phase_rms is the LINE "
            "current, so it was divided by sqrt(3))" if sd == "delta"
            else "the duty's point")

    for k_out, k_in, where in (("v_dc_V", "v_dc_V", "the duty's inverter block"),
                               ("f_carrier_hz", "f_carrier_hz",
                                "the duty's inverter block"),
                               ("modulation_index", "m",
                                "the duty's inverter block (the modulator's own "
                                "ratio; it transfers unchanged to the real "
                                "bridge)")):
        if inv.get(k_in) is not None:
            out[k_out] = float(inv[k_in])
            src[k_out] = where

    rpm = point.get("rpm")
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

    eta = coupled.get("efficiency_shaft")
    p_loss = coupled.get("P_loss_total_incl_mech_W")
    if eta is not None:
        out["efficiency_shaft"] = float(eta)
        src["efficiency_shaft"] = ("the duty's coupled record — the ONE shaft "
                                   "efficiency, not recomputed here")
        if p_loss is not None and 0.0 < float(eta) < 1.0:
            p_shaft = float(p_loss) * float(eta) / (1.0 - float(eta))
            out["p_shaft_W"] = p_shaft
            out["p_ac_W"] = p_shaft + float(p_loss)
            src["p_ac_W"] = ("the duty's coupled record: P_shaft / eta_shaft "
                             "(shaft power plus every motor loss)")
    if em.get("T_em_avg_Nm") is not None:
        out["torque_Nm"] = float(em["T_em_avg_Nm"])
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
    """The solver's request, with a ``sources`` map for every resolved field."""
    mach = _live_machine(body)
    req: Dict[str, Any] = dict(mach["values"])
    sources: Dict[str, str] = dict(mach["sources"])

    duty = _duty_defaults(body.get("die"), body.get("config"), body.get("duty"))
    duty_src = duty.pop("_sources", {}) if duty else {}

    for key in ("star_delta", "i_phase_rms_A", "p_ac_W", "f_elec_hz",
                "f_carrier_hz", "v_dc_V", "modulation_index",
                "efficiency_shaft", "rpm"):
        if body.get(key) is not None:
            req[key], sources[key] = body[key], "the request"
        elif duty.get(key) is not None:
            req[key], sources[key] = duty[key], duty_src.get(key, "the duty record")
    if body.get("power_factor") is not None:
        req["power_factor"] = body["power_factor"]
        sources["power_factor"] = "the request"
        req.pop("modulation_index", None)

    defaults = {"device": (body.get("device") or _default_device()),
                "devices_parallel": body.get("devices_parallel") or 1,
                "topology": body.get("topology") or "one_3ph",
                "set_split": body.get("set_split") or "series_split",
                "h_bridge_modulation": body.get("h_bridge_modulation") or "unipolar",
                "dead_time_us": body.get("dead_time_us"),
                "v_gs_on_V": body.get("v_gs_on_V"),
                "v_gs_off_V": body.get("v_gs_off_V"),
                "r_g_ext_ohm": body.get("r_g_ext_ohm"),
                "r_tim_k_w": body.get("r_tim_k_w"),
                "r_spread_k_w": body.get("r_spread_k_w"),
                "e_oss_policy": body.get("e_oss_policy") or "included_in_eon",
                "samples_per_carrier": body.get("samples_per_carrier"),
                "mapping": body.get("mapping"),
                "devices_parallel_by_bridge":
                    dict(body.get("devices_parallel_by_bridge") or {}) or None,
                "cooling": dict(body.get("cooling") or {})}
    for k, v in defaults.items():
        if v is not None:
            req[k] = v
            sources.setdefault(k, "the request" if body.get(k) is not None
                               else "this module's stated default")
    if duty.get("die"):
        req["_context"] = {"die": duty["die"], "config": duty["config"],
                           "duty": duty["duty"]}
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
