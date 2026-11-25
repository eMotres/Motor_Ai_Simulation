"""One set of operating points for the motor card and the project specification (owner 2026-11-25, PR 1).

The customer's REQUIREMENTS live in the specification (``key_points``: ``row_id``, torque, speed, duration, voltage); the
RESULTS live in the catalogue duties.  Nothing here copies a number from one side to the other: a project motor carries
``point_links`` (which duty answers which requirement) and this module compares the two sides, live, side by side.

STATES of one requirement against one linked motor (the first that holds, in this order):

* ``no_point``  - no link, or the linked duty is gone / the motor is unavailable / the duty has no result yet;
* ``stale``     - the stored result is of an older build, a record that feeds it was set aside (other point / geometry / solver),
                  or the requirement changed after the link was confirmed (``requirement_changed``);
* ``differs``   - solved and current, but a check fails or cannot be made (see ``checks``);
* ``matched``   - solved, current, every check passes.

THE MATCH RULE (owner 2026-11-25): torque NOT BELOW the required (the solved, 3-D corrected torque, absolute value), speed within
1 % of the required, supply voltage NOT ABOVE the limit (the solved DC voltage the point needs), and the thermal time holds for
the required duration (the point is inside every limit, or the time to the limit is at least the duration).  A requirement value
that is not stated is not checked (``n/a``); a check that CAN be made but whose result is not stored is ``unknown`` and keeps the
point from being ``matched`` (never a silent pass).

NOTHING is written from here except the links (``point_links`` on the project's ``linked_motors`` row).  Requirements are changed
only by ``can_edit_spec`` (the specification routes); links are made by engineers.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

STATES = ("no_point", "stale", "differs", "matched")
SPEED_REL_TOL = 0.01          # owner 2026-11-25: speed within 1 %
SPEED_ABS_TOL = 1.0           # rpm: a requirement of a few rpm is not judged to the unit
_EPS = 1e-9


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def spec_points(draft: Any) -> List[Dict[str, Any]]:
    """The stored key points of a draft as plain requirement rows (empty when none is stored or the answer is not filled)."""
    fields = (draft or {}).get("fields") if isinstance(draft, dict) else None
    answer = (fields or {}).get("key_points") if isinstance(fields, dict) else None
    value = answer.get("value") if isinstance(answer, dict) and answer.get("status") == "filled" else None
    out: List[Dict[str, Any]] = []
    for p in value if isinstance(value, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("row_id"), str) or not p["row_id"]:
            continue
        out.append({"row_id": p["row_id"], "name": str(p.get("name") or ""), "torque_Nm": _num(p.get("torque_Nm")),
                    "speed_rpm": _num(p.get("speed_rpm")), "duration_s": _num(p.get("duration_s")),
                    "voltage_V": _num(p.get("voltage_V"))})
    return out


def point_digest(point: Dict[str, Any]) -> str:
    """A short digest of the four requirement numbers: a link remembers it, a later edit of a number makes the link ``stale``."""
    raw = json.dumps([point.get(k) for k in ("torque_Nm", "speed_rpm", "duration_s", "voltage_V")], separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def resolve_duty(link: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The result row a link points at: by the immutable ``duty_id`` first, by the stored name only while the link (or the duty)
    has no id yet.  ``None`` = the duty was deleted, or renamed before it had an id."""
    rows = list(rows)
    did = link.get("duty_id")
    if did:
        hit = next((r for r in rows if r.get("duty_id") == did), None)
        if hit is not None:
            return hit
    name = link.get("duty_name")
    if name:
        return next((r for r in rows if r.get("duty") == name and (not did or not r.get("duty_id"))), None)
    return None


def _row_numbers(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    comp = row.get("comparison") if isinstance(row.get("comparison"), dict) else {}
    op = row.get("operating_point") if isinstance(row.get("operating_point"), dict) else {}
    torque = _num(comp.get("torque_nm"))
    return {"torque_Nm": abs(torque) if torque is not None else None, "speed_rpm": _num(op.get("rpm")),
            "voltage_V": _num(comp.get("v_dc_required_v"))}


def thermal_time(row: Dict[str, Any]) -> Dict[str, Any]:
    """What the row says about holding the point: ``{"state": "within" | "limited" | "unknown", "time_s"}``.

    ``within`` = the coupled record exists and the point is inside every limit (it holds indefinitely); ``limited`` = it
    exceeds a limit and ``time_s`` is the time it runs (the rated start, else the cold start); ``unknown`` = nothing stored."""
    hold = row.get("thermal_hold") if isinstance(row.get("thermal_hold"), dict) else {}
    state = hold.get("state") if hold.get("state") in ("within", "limited") else "unknown"
    return {"state": state, "time_s": _num(hold.get("time_s")) if state == "limited" else None}


def checks(point: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The four checks of one requirement against one solved row: ``{name: {status, required, result, delta}}``.
    ``status`` is ``ok`` | ``fail`` | ``unknown`` (stated, cannot be judged) | ``n/a`` (not stated)."""
    have = _row_numbers(row)
    out: Dict[str, Dict[str, Any]] = {}

    def make(name: str, required: Optional[float], result: Optional[float], passed) -> None:
        if required is None:
            out[name] = {"status": "n/a", "required": None, "result": result, "delta": None}
        elif result is None:
            out[name] = {"status": "unknown", "required": required, "result": None, "delta": None}
        else:
            out[name] = {"status": "ok" if passed(required, result) else "fail", "required": required, "result": result,
                         "delta": result - required}

    make("torque", point.get("torque_Nm"), have["torque_Nm"], lambda r, x: x >= r - _EPS * max(1.0, abs(r)))
    make("speed", point.get("speed_rpm"), have["speed_rpm"],
         lambda r, x: abs(x - r) <= max(SPEED_REL_TOL * abs(r), SPEED_ABS_TOL) + _EPS)
    make("voltage", point.get("voltage_V"), have["voltage_V"], lambda r, x: x <= r + _EPS * max(1.0, abs(r)))
    hold = thermal_time(row)
    need = point.get("duration_s")
    if need is None:
        out["thermal"] = {"status": "n/a", "required": None, "result": None, "delta": None}
    elif hold["state"] == "unknown":
        out["thermal"] = {"status": "unknown", "required": need, "result": None, "delta": None}
    elif hold["state"] == "within":
        out["thermal"] = {"status": "ok", "required": need, "result": None, "delta": None, "holds": "indefinitely"}
    else:
        t = hold["time_s"]
        out["thermal"] = ({"status": "unknown", "required": need, "result": None, "delta": None} if t is None else
                          {"status": "ok" if t >= need else "fail", "required": need, "result": t, "delta": t - need})
    return out


def stale_reasons(point: Dict[str, Any], link: Dict[str, Any], row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    if row.get("build_stale") is True:
        reasons.append("build_changed")
    for rec in row.get("hidden_records") or []:
        if isinstance(rec, dict) and rec.get("reason"):
            reasons.append("record_" + str(rec["reason"]))
    ack = link.get("ack_spec_digest")
    if ack and ack != point_digest(point):
        reasons.append("requirement_changed")
    return reasons


def state_of(point: Dict[str, Any], link: Optional[Dict[str, Any]], rows: List[Dict[str, Any]], available: bool = True) -> Dict[str, Any]:
    """The comparison of ONE requirement with one motor: state, reasons, the linked duty, every check."""
    base: Dict[str, Any] = {"spec_row_id": point["row_id"], "requirement": {k: point.get(k) for k in
                            ("name", "torque_Nm", "speed_rpm", "duration_s", "voltage_V")}, "link": None, "duty": None,
                            "duty_id": None, "checks": {}, "reasons": []}
    if not link:
        return {**base, "state": "no_point", "reasons": ["not_linked"]}
    base["link"] = {k: link.get(k) for k in ("duty_id", "duty_name", "mode", "linked_by", "linked_at")}
    if not available:
        return {**base, "state": "no_point", "reasons": ["motor_unavailable"]}
    row = resolve_duty(link, rows)
    if row is None:
        return {**base, "state": "no_point", "reasons": ["duty_gone"]}
    base["duty"], base["duty_id"] = row.get("duty"), row.get("duty_id")
    if not row.get("has_result"):
        return {**base, "state": "no_point", "reasons": ["not_computed"]}
    base["checks"] = checks(point, row)
    reasons = stale_reasons(point, link, row)
    if reasons:
        return {**base, "state": "stale", "reasons": reasons}
    bad = [name for name, c in base["checks"].items() if c["status"] in ("fail", "unknown")]
    if bad:
        return {**base, "state": "differs", "reasons": [f"{n}_{base['checks'][n]['status']}" for n in bad]}
    return {**base, "state": "matched", "reasons": []}


def suggestions(points: List[Dict[str, Any]], rows: List[Dict[str, Any]], links: List[Dict[str, Any]]) -> Dict[str, str]:
    """``{spec_row_id: duty name}`` for every UNLINKED requirement that has exactly ONE candidate duty, where the duty is
    claimed by no other requirement either way (unambiguous both ways).  A candidate has a result, a speed within the 1 %
    band and a solved torque not below the required.  Everything else stays ``no_point`` for an engineer to confirm."""
    linked = {l.get("spec_row_id") for l in links}
    cands: Dict[str, List[str]] = {}
    for p in points:
        if p["row_id"] in linked or p.get("speed_rpm") is None or p.get("torque_Nm") is None:
            continue
        for row in rows:
            if not row.get("has_result"):
                continue
            c = checks(p, row)
            if c["speed"]["status"] == "ok" and c["torque"]["status"] == "ok":
                cands.setdefault(p["row_id"], []).append(str(row.get("duty")))
    taken: Dict[str, List[str]] = {}
    for rid, names in cands.items():
        for n in names:
            taken.setdefault(n, []).append(rid)
    return {rid: names[0] for rid, names in cands.items() if len(names) == 1 and len(taken[names[0]]) == 1}


def comparisons(points: List[Dict[str, Any]], links: List[Dict[str, Any]], rows: List[Dict[str, Any]],
                available: bool = True) -> List[Dict[str, Any]]:
    """One entry per requirement (every spec row is always present), with ``suggested`` (a duty name) where an engineer could
    confirm an unambiguous match.  Links of a requirement that no longer exists are not listed (they stay stored)."""
    by_row = {l.get("spec_row_id"): l for l in links if isinstance(l, dict)}
    hints = suggestions(points, rows, links) if available else {}
    out = []
    for p in points:
        item = state_of(p, by_row.get(p["row_id"]), rows, available)
        if item["state"] == "no_point" and p["row_id"] in hints:
            item["suggested"] = hints[p["row_id"]]
        out.append(item)
    return out
