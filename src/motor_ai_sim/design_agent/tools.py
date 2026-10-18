"""The design agent's tools: read-only views over the catalogue, the propeller data and the saved simulation results.

Every executor runs inside ``mcp_tools.acting_as(principal)``, so each permission check answers exactly as the
caller's own web session would (catalogue grants, private configurations, the per-user workspace).  The agent
sees what the CLIENT sees: only configurations with a catalogue card (``card_date``, owner 2026-10-17: uncomputed
configurations are not shown to clients), and only public-datasheet data (``mcp_tools.assert_no_geometry``).
Nothing here solves a field or writes a store; the propeller S1 rating is closed-form arithmetic on the
configuration's passport model, the same call Configure makes.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from motor_ai_sim import mcp_tools as _t
from motor_ai_sim.agent_keys import Principal


class AgentToolError(ValueError):
    """A caller mistake (unknown motor, propeller not allowed ...) - reported to the model, never a crash."""


def _r(v: Any, nd: int = 1) -> Optional[float]:
    return _t._f(v, nd)


def _visible_configs() -> List[Dict[str, Any]]:
    """(die, config) rows of the caller's catalogue that carry a catalogue card."""
    out = []
    for die in _t._visible_tree():
        for c in die.get("configs") or []:
            if c.get("card_date"):
                out.append({"die": die, "config": c})
    return out


def _find(die: str, config: str) -> Dict[str, Any]:
    for row in _visible_configs():
        if row["die"].get("name") == die and row["config"].get("name") == config:
            return row
    raise AgentToolError(f"motor '{die} / {config}' is not in this account's catalogue")


def _has_s1(die: str, config: str) -> bool:
    from motor_ai_sim import propeller_s1 as s1
    try:
        s1.load_model(die, config)
        return True
    except Exception:                                       # noqa: BLE001 - no model = no propeller rating
        return False


# ── list_motors ──────────────────────────────────────────────────────────────

def list_motors(application: str = "any", max_diameter_mm: Optional[float] = None,
                max_mass_kg: Optional[float] = None) -> Dict[str, Any]:
    from motor_ai_sim.cooling_options import _applications_of, cooling_options
    machines = {(m["die"], m["config"]): m for m in _t._machines()}
    rows = []
    for row in _visible_configs():
        d, c = row["die"], row["config"]
        dn, cn = str(d.get("name")), str(c.get("name"))
        m = machines.get((dn, cn)) or {}
        apps = _applications_of(dn, cn)
        if application and application != "any" and apps and application not in apps:
            continue
        od, mass = m.get("outer_diameter_mm"), m.get("mass_kg")
        if max_diameter_mm and od and od > max_diameter_mm:
            continue
        if max_mass_kg and mass and mass > max_mass_kg:
            continue
        co = cooling_options(dn, cn)
        props = co.get("propellers") or []
        rows.append({
            "die": dn, "config": cn, "applications": apps or None,
            "outer_diameter_mm": od, "active_length_mm": m.get("active_length_mm"),
            "mass_kg": mass, "mass_partial": m.get("mass_partial"),
            "slots": m.get("slots"), "poles": m.get("poles"), "magnet": m.get("magnet"),
            "dc_bus_nominal_v": m.get("dc_bus_nominal_v"), "dc_bus_min_v": m.get("dc_bus_min_v"),
            "dc_bus_max_v": m.get("dc_bus_max_v"),
            "default_duty": c.get("default_duty"),
            "duties": [{k: x.get(k) for k in ("duty", "mode", "speed_rpm", "torque_nm", "power_kw", "efficiency_pct")}
                       for x in m.get("duties") or []],
            "propellers": props, "default_propeller": co.get("default_propeller"),
            "propeller_s1_available": bool(props) and _has_s1(dn, cn),
        })
    return {"count": len(rows), "motors": rows,
            "note": "numbers are saved simulation results; for a propeller motor call propeller_s1 with one of its "
                    "propellers for thrust, current and temperatures"}


# ── propellers ───────────────────────────────────────────────────────────────

def list_propellers(ids: Optional[List[str]] = None, query: Optional[str] = None) -> Dict[str, Any]:
    from motor_ai_sim import propeller as pp
    want = {str(i) for i in ids or []}
    rows = []
    for p in pp.list_propellers():
        s = pp.summary(p)
        if want and s["id"] not in want:
            continue
        if query and not _t._match(query, s.get("id"), s.get("vendor"), s.get("model"), s.get("series")):
            continue
        rows.append({k: s.get(k) for k in ("id", "vendor", "model", "diameter_in", "pitch_in", "blades", "mass_g",
                                           "rpm_range_tested", "selectable", "power_data", "published_limits")})
    return {"count": len(rows), "propellers": rows}


_ROW_KEYS = ("rpm", "thrust_g", "current_A", "battery_current_A", "battery_power_W", "electrical_power_W",
             "shaft_power_W", "efficiency_pct", "T_winding_C", "T_magnet_C", "ok", "binding")


def _row(r: Dict[str, Any]) -> Dict[str, Any]:
    from motor_ai_sim import propeller as pp
    tn = r.get("thrust_N")
    pb, pe, ps = r.get("battery_power_W"), r.get("electrical_power_W"), r.get("shaft_power_W")
    pin = pb or pe                       # battery power when the drive is modelled, else the motor's input
    return {"rpm": _r(r.get("rpm"), 0), "thrust_g": _r(tn / pp.G0 * 1000.0 if tn is not None else None, 0),
            "current_A": _r(r.get("current_A")), "battery_current_A": _r(r.get("battery_current_A")),
            "battery_power_W": _r(pb, 0), "electrical_power_W": _r(pe, 0), "shaft_power_W": _r(ps, 0),
            "efficiency_pct": _r(ps / pin * 100.0 if (pin and ps) else None),
            "T_winding_C": _r(r.get("T_winding_C"), 0), "T_magnet_C": _r(r.get("T_magnet_C"), 0),
            "ok": bool(r.get("ok")), "binding": r.get("binding")}


def _at_thrust(rows: List[Dict[str, Any]], need_g: float) -> Optional[Dict[str, Any]]:
    """The scanned point at the required thrust (linear between the two bracketing rows)."""
    pts = [r for r in rows if r.get("thrust_g") is not None]
    for a, b in zip(pts, pts[1:]):
        if a["thrust_g"] <= need_g <= b["thrust_g"] and b["thrust_g"] > a["thrust_g"]:
            w = (need_g - a["thrust_g"]) / (b["thrust_g"] - a["thrust_g"])
            out = {}
            for k in _ROW_KEYS:
                va, vb = a.get(k), b.get(k)
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and not isinstance(va, bool):
                    out[k] = round(va + w * (vb - va), 1)
            out["ok"] = bool(a["ok"] and b["ok"])
            out["binding"] = None if out["ok"] else (b.get("binding") or a.get("binding"))
            return out
    return None


def propeller_s1(die: str, config: str, propeller_id: str, ambient_c: float = 25.0, altitude_m: float = 0.0,
                 required_thrust_g: Optional[float] = None, pack_v_min: Optional[float] = None,
                 pack_v_max: Optional[float] = None) -> Dict[str, Any]:
    from fastapi import HTTPException
    from motor_ai_sim.routes import propellers as _pr
    _find(die, config)
    try:
        out = _pr.get_s1(propeller_id, die=die, config=config, ambient_c=float(ambient_c),
                         altitude_m=float(altitude_m), position=None, factor=None, n=25, drive="sine",
                         esc_i_bus_max_A=None, esc_i_phase_max_A=None, pack_v_min=pack_v_min,
                         pack_v_max=pack_v_max, pack_i_max_A=None, authorization=None)
    except HTTPException as exc:
        raise AgentToolError(str(exc.detail))
    rows = [_row(r) for r in out.get("rows") or []]
    lim = out.get("limit") or None
    limit = None
    if lim:
        limit = {**_row(lim), "ok": True, "binding": lim.get("binding"),
                 "binding_meaning": "the limit reached just above this speed (winding / magnet temperature, "
                                    "voltage, controller current, demagnetisation) or 'speed_range' = no limit "
                                    "inside the scanned range",
                 "flags": lim.get("flags") or [], "calibration_in_range": lim.get("calibration_in_range"),
                 "extrapolated_propeller_data": bool(lim.get("extrapolated"))}
    res = {"die": die, "config": config, "propeller_id": out.get("propeller_id"), "propeller": out.get("propeller"),
           "ambient_c": out.get("ambient_c"), "altitude_m": out.get("altitude_m"),
           "pack": out.get("pack"), "temperature_limits": {k: (out.get("limits") or {}).get(k)
                                                          for k in ("winding_C", "magnet_C")},
           "continuous_s1_limit": limit, "no_continuous_point": out.get("no_continuous_point"),
           "rows": rows,
           "rule": "continuous (S1) operation: cooled only by the propeller's own air; a row is ok when every "
                   "temperature, voltage and current limit holds there"}
    if required_thrust_g:
        res["at_required_thrust"] = _at_thrust(rows, float(required_thrust_g)) or {
            "note": f"{required_thrust_g:g} g is outside the scanned thrust range of this motor + propeller"}
    return res


# ── the recommendation card ──────────────────────────────────────────────────

def present_motor(die: str, config: str, headline: str, propeller_id: Optional[str] = None,
                  duty: Optional[str] = None) -> Dict[str, Any]:
    row = _find(die, config)
    duties = [d.get("name") for d in row["config"].get("duties") or []]
    if duty and duty not in duties:
        raise AgentToolError(f"'{die} / {config}' has no duty '{duty}' (has {duties})")
    if propeller_id:
        from motor_ai_sim.cooling_options import cooling_options
        allowed = cooling_options(die, config).get("propellers") or []
        if propeller_id not in allowed:
            raise AgentToolError(f"{propeller_id} is not a propeller {die} / {config} may use (allowed {allowed})")
    card = {"die": die, "config": config, "duty": duty or row["config"].get("default_duty"),
            "propeller_id": propeller_id or None, "headline": str(headline or "")[:300]}
    return {"shown": True, "card": card}


# ── schemas for the model ────────────────────────────────────────────────────

_NUM = {"type": "number"}

TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "list_motors",
     "description": "The motors in this user's catalogue (only computed ones): diameter, active length, mass, "
                    "slots/poles, magnet, battery voltage range, saved duties and - for propeller motors - the "
                    "propellers each one may drive.  Start here.",
     "input_schema": {"type": "object", "properties": {
         "application": {"type": "string", "enum": ["any", "propeller", "robot", "aviation", "marine"],
                         "description": "filter: propeller (drones, air-cooled by the propeller), robot (joints), "
                                        "aviation / marine (liquid-cooled drives)"},
         "max_diameter_mm": _NUM, "max_mass_kg": _NUM}}},
    {"name": "list_propellers",
     "description": "Propeller catalogue: vendor, model, diameter and pitch in inches, blades, mass, tested rpm "
                    "range.  Pass ids to describe the propellers list_motors named.",
     "input_schema": {"type": "object", "properties": {
         "ids": {"type": "array", "items": {"type": "string"}},
         "query": {"type": "string", "description": "free text, e.g. '15' or 'T-Motor'"}}}},
    {"name": "propeller_s1",
     "description": "Continuous (S1) operation of one propeller motor driving one propeller, cooled only by "
                    "that propeller's air: per-speed rows (thrust in grams, phase and battery current, battery "
                    "power, efficiency, winding and magnet temperature, ok/limit) and the highest continuous "
                    "point with the limit that stops it.  efficiency_pct = shaft power / battery power (or / motor input power when the drive is not modelled).  With required_thrust_g it also interpolates the point "
                    "at that thrust.  This is THE tool for drone / aircraft questions.",
     "input_schema": {"type": "object", "required": ["die", "config", "propeller_id"], "properties": {
         "die": {"type": "string"}, "config": {"type": "string"},
         "propeller_id": {"type": "string", "description": "one of the motor's propellers from list_motors"},
         "ambient_c": {"type": "number", "description": "air temperature, default 25"},
         "altitude_m": {"type": "number", "description": "default 0"},
         "required_thrust_g": {"type": "number", "description": "thrust per motor the user needs, grams"},
         "pack_v_min": {"type": "number", "description": "battery minimum voltage, V (default the motor's)"},
         "pack_v_max": {"type": "number", "description": "battery full voltage, V (default the motor's)"}}}},
    {"name": "check_fit",
     "description": "Motors whose saved duties deliver at least the torque at the speed within the voltage, "
                    "diameter, length and mass limits (robot joints, drives).  Ranked lightest first.",
     "input_schema": {"type": "object", "required": ["torque_nm", "speed_rpm"], "properties": {
         "torque_nm": _NUM, "speed_rpm": _NUM, "voltage_v": _NUM, "max_diameter_mm": _NUM,
         "max_length_mm": _NUM, "max_mass_kg": _NUM}}},
    {"name": "get_machine_performance",
     "description": "One saved duty in full: operating point, losses, temperatures, continuous rating, "
                    "time to the thermal limit, KV, ripple.",
     "input_schema": {"type": "object", "required": ["die", "config", "duty"], "properties": {
         "die": {"type": "string"}, "config": {"type": "string"}, "duty": {"type": "string"}}}},
    {"name": "present_motor",
     "description": "Show the user a recommended motor as a card with an 'Open in Configure' button.  Call it "
                    "once for each motor you finally recommend (at most 3), with a one-line headline in the "
                    "user's language, e.g. '15x8: 1.9 kg thrust continuous, 41 A at 22.2 V'.",
     "input_schema": {"type": "object", "required": ["die", "config", "headline"], "properties": {
         "die": {"type": "string"}, "config": {"type": "string"}, "headline": {"type": "string"},
         "propeller_id": {"type": "string"}, "duty": {"type": "string"}}}},
]


def _compact_fit(res: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("die", "config", "duty", "torque_nm", "speed_rpm", "power_kw", "efficiency_pct", "mass_kg",
            "outer_diameter_mm", "active_length_mm", "unverified", "reasons")
    visible = {(r["die"].get("name"), r["config"].get("name")) for r in _visible_configs()}
    fits = [{k: f.get(k) for k in keep} for f in res.get("fits") or [] if (f["die"], f["config"]) in visible]
    rej = [{k: f.get(k) for k in keep} for f in res.get("rejected") or [] if (f["die"], f["config"]) in visible]
    return {"requirements": res.get("requirements"), "rule": res.get("rule"), "fits": fits[:8],
            "rejected": rej[:5]}


def run_tool(principal: Principal, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one tool call for ``principal``.  Raises ``AgentToolError`` for the model to read."""
    args = dict(args or {})
    try:
        if name == "check_fit":
            res = _t.check_fit(principal, limit=50, **{k: args.get(k) for k in (
                "torque_nm", "speed_rpm", "voltage_v", "max_diameter_mm", "max_length_mm", "max_mass_kg")})
            with _t.acting_as(principal):
                return _compact_fit(res)
        if name == "get_machine_performance":
            with _t.acting_as(principal):
                _find(str(args.get("die")), str(args.get("config")))
            return _t.get_machine_performance(principal, str(args.get("die")), str(args.get("config")),
                                              str(args.get("duty")))
        fn: Optional[Callable[..., Dict[str, Any]]] = {
            "list_motors": list_motors, "list_propellers": list_propellers, "propeller_s1": propeller_s1,
            "present_motor": present_motor}.get(name)
        if fn is None:
            raise AgentToolError(f"unknown tool {name}")
        with _t.acting_as(principal):
            return _t._checked(fn(**args))
    except _t.ToolError as exc:
        raise AgentToolError(str(exc))
    except TypeError as exc:                                 # a wrong / missing argument from the model
        raise AgentToolError(f"bad arguments for {name}: {exc}")
