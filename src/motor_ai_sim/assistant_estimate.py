"""Operating points the assistant DERIVES from our propeller catalogue (owner 2026-11-11: «оцени его параметры, как у аналогичных
на рынке» - the assistant must estimate from data instead of refusing).

When the user's own words name a propeller (a catalogue id, ``42x16`` / ``42*16``, or a diameter in inches: ``42"``, ``42 inch``,
``42-дюймовый``) and a battery (``12S`` or a voltage), this module finds the catalogue entry and computes, with
``propeller.py`` (the same fitted C_T / C_P model Configure uses):

* the MAX point: the propeller's published recommended maximum rpm (else the top of its test data) - torque, shaft power, thrust;
* the HOVER point: half the max thrust (thrust-to-weight 2:1, the usual multirotor sizing; ASSUMED) - rpm, torque, power;
* the propeller-wash air speed at the motor at hover (momentum theory, ``propeller.cooling_air_speed_ms``).

Everything carries its basis: ``derived`` (from the vendor's table, named with its source URL) or ``assumed`` (a typical value for
this class of motor, to confirm).  It never produces the customer's own constraints (envelope, mass target, quantities, section 6):
those stay questions.  Pure: reads the catalogue, writes nothing.

``spec_values`` turns an estimate into specification answers (field id, value in the schema's shape, reason, basis) - the values a
specification created from the chat starts with (``assistant_handoff``) and the model sees in its context.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

#: Assumptions for the class (owner 2026-11-11: typical market values, marked "assumed, to confirm").
HOVER_THRUST_FRACTION = 0.5          # thrust-to-weight 2:1
PEAK_DURATION_S = 60                 # max thrust held for take-off / manoeuvre
AMBIENT_MAX_C = 40.0
LIPO_CELL_V = (3.0, 3.7, 4.2)        # min / nominal / max per cell
SYSTEM_EFFICIENCY = 0.85             # battery -> shaft (motor + controller), for the battery-current estimate only
SPECIFIC_POWER_KW_KG = (5.0, 8.0)    # propeller-cooled drone outrunners at max (short-time) shaft power: the mass CLASS shown in chat

_INCH = r"(?:\"|''|”|″|\s*-?\s*(?:in\b|inch|inches|дюйм\w*|дуй\w*|英寸|寸))"
_SIZE_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:[.,]\d)?)\s*(?:[x×х*]|by)\s*(\d{1,2}(?:[.,]\d{1,2})?)(?![\d])", re.IGNORECASE)
_DIAM_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:[.,]\d)?)" + _INCH, re.IGNORECASE)
_PROP_WORD_DIAM_RE = re.compile(r"(?:propell?er|prop|пропеллер\w*|винт\w*|螺旋桨)\D{0,12}?(\d{1,2}(?:[.,]\d)?)(?![\d.,])", re.IGNORECASE)
_CELLS_RE = re.compile(r"(?<![\w.])(\d{1,3})\s*[sS](?:\b|(?=[^A-Za-z]))")
_VOLT_RE = re.compile(r"(?<![\w.])(\d{2,4}(?:[.,]\d)?)\s*(?:V|В|伏)(?![A-Za-zА-Яа-я])")
_ID_RE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
_PROP_HINT_RE = re.compile(r"propell?er|\bprop\b|пропеллер|винт|螺旋桨|\bapc\b|t-?motor|\bxoar\b|\bmejzlik\b|\bfalcon\b", re.IGNORECASE)


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def parse_request(texts: Iterable[str]) -> Dict[str, Any]:
    """What the user's messages name: ``{prop_id, diameter_in, pitch_in, cells, voltage_V, prop_named}`` (latest message wins)."""
    out: Dict[str, Any] = {}
    for text in reversed([str(t or "") for t in texts]):
        low = text.lower()
        if "prop_id" not in out:
            m = next((i for i in _ID_RE.findall(low) if i.split("_", 1)[0] in ("tmotor", "motres", "apc", "xoar")), None)
            if m:
                out["prop_id"] = m
        if "diameter_in" not in out:
            m = _SIZE_RE.search(text)
            if m and 5 <= _num(m.group(1)) <= 80:
                out["diameter_in"], out["pitch_in"] = _num(m.group(1)), _num(m.group(2))
            else:
                m = _DIAM_RE.search(text) or _PROP_WORD_DIAM_RE.search(text)
                if m and 5 <= _num(m.group(1)) <= 80:
                    out["diameter_in"] = _num(m.group(1))
        if "cells" not in out:
            m = _CELLS_RE.search(text)
            if m and 1 <= int(m.group(1)) <= 200:
                out["cells"] = int(m.group(1))
        if "voltage_V" not in out:
            m = _VOLT_RE.search(text)
            if m and 3 <= _num(m.group(1)) <= 1500:
                out["voltage_V"] = _num(m.group(1))
        if _PROP_HINT_RE.search(text) or "diameter_in" in out or "prop_id" in out:
            out["prop_named"] = True
    return out


def find_propeller(req: Dict[str, Any]):
    """``(Propeller | None, [other ids of the same size])`` - the catalogue entry the request names."""
    from motor_ai_sim import propeller as PR
    try:
        cat = PR.load_catalog()
    except Exception:                                         # noqa: BLE001 - no catalogue: no estimate
        return None, []
    if req.get("prop_id") and req["prop_id"] in cat:
        p = cat[req["prop_id"]]
        return (p if p.selectable else None), []
    d = req.get("diameter_in")
    if d is None:
        return None, []
    hits = [p for p in cat.values() if p.selectable and abs(p.diameter_nominal_in - d) <= 0.3]
    if req.get("pitch_in") is not None:
        exact = [p for p in hits if p.pitch_in is not None and abs(p.pitch_in - req["pitch_in"]) <= 0.3]
        if not exact:
            return None, [p.id for p in hits]
        hits = exact
    if not hits:
        return None, []
    hits.sort(key=lambda p: (p.power_data != "measured_torque", p.status != "current", p.id))
    return hits[0], [p.id for p in hits[1:]]


def _battery(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if req.get("cells"):
        n = int(req["cells"])
        lo, nom, hi = (round(n * c, 1) for c in LIPO_CELL_V)
        return {"cells": n, "voltage_min_V": lo, "voltage_nominal_V": nom, "voltage_max_V": hi,
                "basis": "derived", "note": f"{n}S, LiPo / Li-ion cells {LIPO_CELL_V[0]}/{LIPO_CELL_V[1]}/{LIPO_CELL_V[2]} V (cell voltages assumed, to confirm)"}
    if req.get("voltage_V"):
        v = float(req["voltage_V"])
        return {"cells": None, "voltage_min_V": None, "voltage_nominal_V": v, "voltage_max_V": None, "basis": "stated",
                "note": f"{v:g} V stated"}
    return None


def _thrust_rpm(PR, prop, thrust_n: float, hi: float) -> float:
    lo = 0.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if PR.thrust_N(prop, mid) < thrust_n:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _src(prop) -> str:
    raw = prop.raw or {}
    tables = (raw.get("performance") or {}).get("tables") or []
    return str(next((t.get("source_url") for t in tables if t.get("source_url")), None) or raw.get("product_url")
               or next(iter(raw.get("source_urls") or []), "") or "")


def estimate(texts: Iterable[str]) -> Optional[Dict[str, Any]]:
    """The derived operating points for the propeller + battery the messages name, or ``None`` (no catalogue propeller named).
    Without a battery the points still come out; the supply then stays a question."""
    req = parse_request(texts)
    prop, others = find_propeller(req)
    if prop is None:
        return {"found": False, "request": req, "same_size": others, "battery": _battery(req)} if req.get("prop_named") else None
    from motor_ai_sim import propeller as PR
    limits = prop.raw.get("published_limits") or {}
    lo, hi = prop.rpm_range
    rpm_max = float(limits.get("recommended_max_rpm") or hi)
    mx = PR.operating_point(prop, rpm_max)
    rpm_h = _thrust_rpm(PR, prop, HOVER_THRUST_FRACTION * mx["thrust_N"], rpm_max)
    hv = PR.operating_point(prop, rpm_h)
    label = f"{prop.vendor.title() if prop.vendor.isupper() else prop.vendor} {prop.model}".strip()
    src = _src(prop)
    bat = _battery(req)
    point = lambda op: {"rpm": round(op["rpm"]), "torque_Nm": round(op["torque_Nm"], 2),  # noqa: E731
                        "shaft_power_W": round(op["shaft_power_W"]), "thrust_N": round(op["thrust_N"], 1),
                        "thrust_kgf": round(op["thrust_N"] / PR.G0, 1), "extrapolated": bool(op["extrapolated"])}
    out = {
        "found": True, "request": req, "propeller_id": prop.id, "propeller": label, "source_url": src,
        "same_size": others, "rpm_tested": [round(lo), round(hi)],
        "max_basis": "published recommended max rpm" if limits.get("recommended_max_rpm") else "top of the published test data",
        "max": point(mx), "hover": point(hv),
        "air_speed_hover_ms": round(PR.cooling_air_speed_ms(prop, rpm_h), 1),
        "battery": bat,
        "assumed": {"hover_thrust_fraction": HOVER_THRUST_FRACTION, "peak_duration_s": PEAK_DURATION_S,
                    "ambient_max_C": AMBIENT_MAX_C, "system_efficiency": SYSTEM_EFFICIENCY},
    }
    if bat and bat.get("voltage_nominal_V"):
        out["battery_current_max_A"] = round(mx["shaft_power_W"] / (SYSTEM_EFFICIENCY * bat["voltage_nominal_V"]))
    kw = mx["shaft_power_W"] / 1000.0
    out["mass_class_kg"] = [round(kw / SPECIFIC_POWER_KW_KG[1], 1), round(kw / SPECIFIC_POWER_KW_KG[0], 1)]
    return out


def _basis_line(est: Dict[str, Any]) -> str:
    return f"derived from the {est['propeller']} data" + (f" ({est['source_url']})" if est.get("source_url") else "")


def render_for_model(est: Optional[Dict[str, Any]]) -> str:
    """The server-computed block the model reads (and quotes) - numbers with their basis."""
    if not est:
        return ""
    if not est.get("found"):
        req = est.get("request") or {}
        size = (f"{req['diameter_in']:g}" + (f"x{req['pitch_in']:g}" if req.get("pitch_in") is not None else "") + " in") \
            if req.get("diameter_in") is not None else "the named part"
        lines = ["PROPELLER ESTIMATE (server): " + size + " is NOT in our propeller catalogue"
                 + (f" with that pitch (same diameter in the catalogue: {', '.join(est['same_size'])})" if est.get("same_size") else "") + "."]
        lines.append("If the web search tool is available, look up the maker's published test table (thrust, torque or shaft power vs rpm) "
                     "and derive the points from it, naming the maker and the URL in the basis (\"derived from <maker> data, <URL>\"). "
                     "If nothing reliable is found, give a typical-class estimate marked \"assumed, to confirm\" and say so.")
        if est.get("battery"):
            lines.append(f"- Battery: {est['battery']['note']}.")
        return "\n".join(lines)
    mx, hv, bat = est["max"], est["hover"], est.get("battery")
    basis = _basis_line(est)
    lines = [
        f"PROPELLER ESTIMATE (computed by the server from our propeller catalogue: {est['propeller']}, id {est['propeller_id']}; "
        f"tested {est['rpm_tested'][0]}-{est['rpm_tested'][1]} rpm). Quote these numbers with their basis; do not recompute them.",
        f"- Max point ({est['max_basis']}): {mx['rpm']} rpm, {mx['torque_Nm']} N·m, {mx['shaft_power_W']} W shaft, thrust "
        f"{mx['thrust_kgf']} kgf ({mx['thrust_N']} N) - {basis}.",
        f"- Hover point at {int(HOVER_THRUST_FRACTION * 100)} % of max thrust: {hv['rpm']} rpm, {hv['torque_Nm']} N·m, "
        f"{hv['shaft_power_W']} W, {hv['thrust_kgf']} kgf - rpm/torque/power {basis}; the {int(HOVER_THRUST_FRACTION * 100)} % "
        "(thrust-to-weight 2:1) is assumed, typical for this class, to confirm.",
        f"- Propeller-wash air speed at the motor at hover: {est['air_speed_hover_ms']} m/s - derived (momentum theory, motor behind the hub).",
        f"- Assumed, typical for this class, to confirm: hover is the continuous point; max thrust held {PEAK_DURATION_S} s; ambient "
        f"{AMBIENT_MAX_C:g} °C max; cooling by the propeller air; open housing (air through the motor).",
    ]
    if bat:
        lines.append(f"- Battery: {bat['note']}: " + ", ".join(f"{k.split('_')[1]} {bat[k]:g} V" for k in
                     ("voltage_min_V", "voltage_nominal_V", "voltage_max_V") if bat.get(k) is not None) + ".")
        if est.get("battery_current_max_A"):
            lines.append(f"- Battery current at the max point about {est['battery_current_max_A']} A (assumed {SYSTEM_EFFICIENCY:.0%} "
                         "battery-to-shaft efficiency, to confirm).")
    else:
        lines.append("- Battery: not named yet - ask for it (cells in series or voltage).")
    lines.append(f"- Mass class (information only, never a specification value): motors of this class weigh about "
                 f"{est['mass_class_kg'][0]}-{est['mass_class_kg'][1]} kg (typical {SPECIFIC_POWER_KW_KG[0]:g}-"
                 f"{SPECIFIC_POWER_KW_KG[1]:g} kW/kg at max shaft power; assumed). The mass target itself is the customer's decision.")
    if est.get("same_size"):
        lines.append(f"- Other catalogue propellers of this size: {', '.join(est['same_size'])}.")
    return "\n".join(lines)


def spec_values(est: Optional[Dict[str, Any]], lang: str = "en") -> List[Dict[str, Any]]:
    """Specification answers from an estimate: ``[{field, value, reason, basis}]``, each in the schema's shape (validated again
    when it is applied).  Only physics and typical-class values - never the envelope, the mass target, quantities or section 6."""
    if not est or not est.get("found"):
        return []
    mx, hv, bat = est["max"], est["hover"], est.get("battery")
    basis = _basis_line(est)
    tag = "（假设，待确认）" if lang == "zh-CN" else "(assumed, to confirm)"
    vol = (bat or {}).get("voltage_min_V") or (bat or {}).get("voltage_nominal_V")
    kp = [
        {"name": "Hover (continuous)", "torque_Nm": hv["torque_Nm"], "speed_rpm": hv["rpm"], "duration_s": "continuous"},
        {"name": "Max thrust (peak)", "torque_Nm": mx["torque_Nm"], "speed_rpm": mx["rpm"], "duration_s": PEAK_DURATION_S},
    ]
    if vol:
        for row in kp:
            row["voltage_V"] = vol
    out = [
        {"field": "key_points", "value": kp, "basis": "derived",
         "reason": f"Torque and rpm {basis}; hover at {int(HOVER_THRUST_FRACTION * 100)} % of max thrust, hover continuous and max "
                   f"thrust {PEAK_DURATION_S} s are typical for this class {tag}"},
        {"field": "1_6", "value": {"maximum_rpm": mx["rpm"]}, "basis": "derived",
         "reason": f"{est['max_basis']} of the propeller, {basis}"},
        {"field": "4_6", "value": {"axial_thrust_N": mx["thrust_N"]}, "basis": "derived",
         "reason": f"thrust at the max point, {basis}"},
        {"field": "1_2", "value": "Motor, one direction", "basis": "assumed", "reason": f"a propeller drive turns one way {tag}"},
        {"field": "3_1", "value": {"cooling_medium": "air", "airflow": {"mode": "Propeller wash", "speed_mps": est["air_speed_hover_ms"]}},
         "basis": "assumed", "reason": f"propeller-air cooling, air speed at hover derived from the thrust (momentum theory) {tag}"},
        {"field": "3_2", "value": {"temperature_max_C": AMBIENT_MAX_C}, "basis": "assumed",
         "reason": f"typical maximum ambient for this class {tag}"},
        {"field": "3_3", "value": "Open (air flows through the motor)", "basis": "assumed",
         "reason": f"propeller-cooled motors of this class run open {tag}"},
    ]
    if bat and bat.get("cells"):
        out.insert(1, {"field": "2_1", "basis": "derived", "reason": bat["note"],
                       "value": {"supply_type": "battery", "chemistry": "LiPo / Li-ion", "series_cells": bat["cells"],
                                 "voltage_min_V": bat["voltage_min_V"], "voltage_nominal_V": bat["voltage_nominal_V"],
                                 "voltage_max_V": bat["voltage_max_V"], "cell_voltage_min_V": LIPO_CELL_V[0],
                                 "cell_voltage_nominal_V": LIPO_CELL_V[1], "cell_voltage_max_V": LIPO_CELL_V[2]}})
    elif bat and bat.get("voltage_nominal_V"):
        out.insert(1, {"field": "2_1", "basis": "stated", "reason": bat["note"],
                       "value": {"supply_type": "battery", "voltage_nominal_V": bat["voltage_nominal_V"]}})
    return out
