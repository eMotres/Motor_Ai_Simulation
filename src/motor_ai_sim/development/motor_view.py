"""Read-only view of what is STORED for a motor: one display system for every entry (owner 2026-10-26).

A motor opened from the Motors tab and a project motor opened from Development are the same motor and show the
same view.  When the motor has no full passport, Configure opens in a POINTS-LIMITED mode that shows only what the
stores keep per computed duty point:

* electromagnetic numbers (the duty's saved block, the kernel run's own numbers),
* thermal results (the settled temperatures, the S1 limit, the S2 times) - ``duty_results``,
* mechanical results (safety factor per part, the retention verdict, the largest displacement, the proof speed) -
  ``duty_results``,
* the field maps (|B|, loss density, demag, temperature, stress) - ``duty_fields``.

Everything here is a READ: nothing is solved, nothing is written, a missing kind is reported as missing (never as
zero), and every block names where it came from (``provenance``: when it was computed, which solver commit and app
version made it, the mesh).  Access is the caller's - the routes that serve these functions apply the ordinary motor
gate (and, for a project motor, the project membership gate) before anything is read.

Pure functions over the stores; no FastAPI imports, so the tests drive them directly.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

#: Field kinds the viewer can draw (``duty_fields.KINDS`` minus ``modes``, which has its own gallery elsewhere).
FIELD_KINDS = ("em", "thermal", "rotor_stress")

#: Views each field kind offers, in menu order, when the arrays for them are stored.
EM_VIEWS = (("Bmag", "b_mag_per_tri"), ("Loss", "loss_dens_per_tri"), ("Demag", "demag_coef_per_tri"),
            ("Az", "a_z_per_node"))
THERMAL_VIEWS = (("temp", "temperature_per_node"), ("flux", "heat_flux_per_tri"))
MECH_VIEWS = (("disp", "u_mag_per_node"), ("vm", "vm_per_tri"), ("s1", "s_p1_per_tri"), ("sf", "sf_per_tri"))


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def plain(value: Any, depth: int = 3) -> Any:
    """A JSON-safe copy keeping only finite numbers, booleans, short strings, small lists and nested dicts.

    The stored rows are allow-listed when they are written, and this is the second belt: nothing that is not plain
    data (and nothing deeper than ``depth``) leaves the server through the viewer.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        return value[:300]
    if depth <= 0:
        return None
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:80]:
            p = plain(v, depth - 1)
            if p is not None:
                out[str(k)[:60]] = p
        return out
    if isinstance(value, (list, tuple)):
        items = [plain(v, depth - 1) for v in list(value)[:60]]
        return [v for v in items if v is not None]
    return None


def _pick(src: Any, keys) -> Dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    return {k: plain(src[k]) for k in keys if src.get(k) is not None and plain(src[k]) is not None}


def provenance(row: Any, field_meta: Any = None) -> Dict[str, Any]:
    """Where a stored block came from: when, which solver, which mesh.  Missing parts stay absent (never guessed)."""
    row = row if isinstance(row, dict) else {}
    fm = field_meta if isinstance(field_meta, dict) else {}
    solver = row.get("solver") if isinstance(row.get("solver"), dict) else (
        fm.get("solver") if isinstance(fm.get("solver"), dict) else {})
    mesh = row.get("mesh") if isinstance(row.get("mesh"), dict) else {}
    point = row.get("point") if isinstance(row.get("point"), dict) else {}
    out = {
        "computed_at": row.get("computed_at") or fm.get("computed_at"),
        "recorded_at": row.get("recorded_at") or fm.get("saved_at"),
        "code_version": solver.get("code_version"),
        "app_version": solver.get("version"),
        "image": solver.get("image"),
        "geometry_fingerprint": row.get("geometry_fingerprint") or fm.get("geometry_fingerprint"),
        "mesh_size_mm": _num(mesh.get("mesh_size_mm")) if _num(mesh.get("mesh_size_mm")) is not None
        else _num(point.get("mesh_size_mm")),
        "n_triangles": mesh.get("n_triangles") if isinstance(mesh.get("n_triangles"), (int, float))
        else fm.get("n_triangles"),
        "element_order": mesh.get("element_order") if mesh.get("element_order") is not None else point.get("order"),
    }
    return {k: plain(v) for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# thermal / mechanical blocks (from duty_results rows)
# ---------------------------------------------------------------------------

#: The solver change after which a stored rotor-stress answer is trusted: #422 (commit 5249508a) repaired the stalled
#: unilateral contact, so an answer computed before it was made by a superseded solver.
CONTACT_REPAIR_COMMIT = "5249508a"
CONTACT_REPAIR_AT = "2026-10-25T21:50:12+00:00"


def _when(row: Any):
    """When a stored row was computed (aware datetime; a naive stamp is read as UTC), or ``None``."""
    from datetime import datetime, timezone
    if not isinstance(row, dict):
        return None
    for key in ("computed_at", "recorded_at"):
        raw = row.get(key)
        if isinstance(raw, str) and raw:
            try:
                d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def solver_status(row: Any) -> str:
    """``current`` | ``superseded`` | ``unknown``: was this row made after the contact repair (#422)?

    A row carrying a solver stamp was written by a build that has the repair (the stamps came later). An unstamped row
    is judged by its date; with no date it is ``unknown`` - never presented as current.
    """
    from datetime import datetime
    if isinstance(row, dict) and isinstance(row.get("solver"), dict) and row["solver"].get("code_version"):
        return "current"
    when = _when(row)
    if when is None:
        return "unknown"
    return "superseded" if when < datetime.fromisoformat(CONTACT_REPAIR_AT) else "current"


def _is_limit_search(cp: Dict[str, Any]) -> bool:
    """A coupled row that answers 'where does this part reach its limit' (S1 search), not 'what does this duty settle at'."""
    from motor_ai_sim.duty_results import is_limit_search
    return is_limit_search(cp)


def _newest(*rows: Any) -> Optional[Dict[str, Any]]:
    """The newest of the given rows (None entries skipped); an undated row loses to a dated one."""
    from datetime import datetime, timezone
    floor = datetime.min.replace(tzinfo=timezone.utc)
    best = None
    for r in rows:
        if isinstance(r, dict) and r and (best is None or (_when(r) or floor) > (_when(best) or floor)):
            best = r
    return best


def _coupled_em(cp: Dict[str, Any]) -> Dict[str, Any]:
    """The electromagnetic numbers the COUPLED LOOP itself ended on (its own torque and losses at its own temperatures) - kept
    apart from the EM run's, which was made at a fixed winding temperature."""
    from motor_ai_sim.development import motors
    em = motors._metric_projection(cp.get("em") if isinstance(cp.get("em"), dict) else {})
    out = {}
    for name, keys in (("torque_nm", ("t_em_avg_nm", "t_em_nm", "t_avg_nm", "torque_avg_nm", "torque_nm")),
                       ("p_loss_total_w", ("p_loss_total_w", "loss_w")),
                       ("efficiency_pct", ("efficiency_pct", "efficiency_percent", "eta_pct"))):
        for k in keys:
            if _num(em.get(k)) is not None:
                out[name] = _num(em[k])
                break
    return out


# ---------------------------------------------------------------------------
# which stored records belong to a point (owner 2026-10-26)
# ---------------------------------------------------------------------------
#
# "I chose a concrete configuration; if it has no calculation, show nothing - only real results that belong to each point."
# A record filed under a duty is shown for that duty only when (1) it is the duty's OWN record (same die / configuration /
# duty - never a configuration-level "worst case" over other duties), (2) the operating point it was solved AT is the duty's
# (speed and phase current within a small tolerance), (3) it is not superseded by a solver repair or made for another machine
# print, and (4) its numbers are physically possible.  Anything else is left out of the view - not dimmed, not flagged - and
# only named in the duty's ``hidden_records`` list (for diagnostics, never drawn).

#: Speed and current tolerances of "the record was solved at the point's operating point".
RPM_REL_TOL, RPM_ABS_TOL = 0.01, 1.0
CURRENT_REL_TOL, CURRENT_ABS_TOL = 0.03, 0.5

#: Kinds whose operating point is checked.  The others (``em`` pointer, ``modes``, ``pwm``, ``controller``) are kept as filed.
_POINT_KINDS = ("thermal", "coupled", "coupled_limit", "rotor_stress", "duty_cycle", "em_run", "continuous_rating",
                "critical_speeds")
#: A limit search solves its own current (the S1 current IS its answer), so only its speed is compared.
_SPEED_ONLY_KINDS = ("coupled_limit", "rotor_stress", "continuous_rating", "critical_speeds")

#: The temperatures a real result can show; a stored value outside it is a broken record, never shown.
T_FLOOR_C, T_CEIL_C = -80.0, 1500.0


def _nums(*values: Any) -> List[float]:
    return [float(n) for n in (_num(x) for x in values) if n is not None]


def record_stamp(kind: str, row: Any) -> Dict[str, List[float]]:
    """The speeds and phase currents a stored row says it was solved at (``{"rpm": [...], "current": [...]}``)."""
    row = row if isinstance(row, dict) else {}
    pt = row.get("point") if isinstance(row.get("point"), dict) else {}
    nums = row.get("numbers") if isinstance(row.get("numbers"), dict) else {}
    em = row.get("em") if isinstance(row.get("em"), dict) else {}
    cr = row.get("continuous_rating") if isinstance(row.get("continuous_rating"), dict) else {}
    rpm = _nums(pt.get("rpm"), row.get("rpm"), nums.get("rpm"), row.get("rated_rpm"))
    if kind in ("coupled", "coupled_limit"):
        rpm += _nums(cr.get("rpm"))
    cur = _nums(pt.get("I_phase_rms"), pt.get("current_arms"), nums.get("I_phase_rms_A"), nums.get("I1_phase_rms_A"),
                em.get("I1_phase_rms_A"))
    return {"rpm": rpm, "current": cur}


def _close(a: float, b: float, rel: float, floor: float) -> bool:
    return abs(a - b) <= max(floor, rel * max(abs(a), abs(b)))


def operating_of(row: Any) -> Dict[str, Optional[float]]:
    """The operating point of a duty row (``motors._results``): speed and phase current as the duty states them."""
    op = row.get("operating_point") if isinstance(row, dict) and isinstance(row.get("operating_point"), dict) else {}
    return {"rpm": _num(op.get("rpm")), "current": _num(op.get("current_arms"))}


def belongs_to_point(kind: str, row: Any, op: Dict[str, Optional[float]], *, limit_search: bool = False) -> bool:
    """Was this stored row solved at the duty's operating point?

    Every identifying stamp the row carries must agree with the point; a row with no stamp at all cannot be tied to the
    point and does NOT belong (it is not guessed).  A point that states neither speed nor current has nothing to compare:
    everything filed under it stays.
    """
    rpm, cur = (op or {}).get("rpm"), (op or {}).get("current")
    if rpm is None and cur is None:
        return True
    stamp = record_stamp(kind, row)
    checks: List[bool] = []
    if rpm is not None and stamp["rpm"]:
        checks.append(any(_close(rpm, x, RPM_REL_TOL, RPM_ABS_TOL) for x in stamp["rpm"]))
    if cur is not None and stamp["current"] and kind not in _SPEED_ONLY_KINDS and not limit_search:
        checks.append(any(_close(cur, x, CURRENT_REL_TOL, CURRENT_ABS_TOL) for x in stamp["current"]))
    return bool(checks) and all(checks)


def impossible_temperature(row: Any) -> Optional[str]:
    """The offending value when a stored thermal row carries a temperature outside any physical window, else ``None``.

    Seen 2026-10-26: a ``limited`` map (the last solved map TRANSLATED part by part onto the node temperatures of the
    limit instant, ``routes.thermal.rescale_map_to_nodes``) stored ``T_min = -147.4 degC``: a level shift cannot keep a
    cold part above ambient when the shift is as large as the runaway steady state is hot.
    """
    if not isinstance(row, dict):
        return None
    values = _nums(row.get("T_min"), row.get("T_max"), row.get("coil_temp_c"), row.get("magnet_temp_c"),
                   row.get("magnet_temp_max_c"), row.get("bearing_temp_c"))
    comps = row.get("components") if isinstance(row.get("components"), dict) else {}
    for c in comps.values():
        if isinstance(c, dict):
            values += _nums(c.get("min"), c.get("avg"), c.get("max"))
    bad = [v for v in values if v < T_FLOOR_C or v > T_CEIL_C]
    return f"{(min(bad) if min(bad) < T_FLOOR_C else max(bad)):.1f}" if bad else None


def _geometry_status(rs: Dict[str, Any], duty_rows: Dict[str, Any]) -> str:
    """``match`` | ``differs`` | ``unknown``: the stress record's mesh-independent machine print against the newest
    electromagnetic / coupled / thermal record of the SAME duty (the v1 fingerprint moves with the mesh, so it is not compared)."""
    ref = None
    for kind in ("em_run", "coupled", "thermal"):
        row = duty_rows.get(kind)
        fp = row.get("geometry_fingerprint_v2") if isinstance(row, dict) else None
        w = _when(row)
        if fp and w and (ref is None or w > ref[0]):
            ref = (w, fp)
    mine = rs.get("geometry_fingerprint_v2")
    return "unknown" if not (mine and ref) else ("match" if mine == ref[1] else "differs")


def own_records(stored_duty: Any, op: Dict[str, Optional[float]]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """The records of ONE duty that really belong to it: ``(kept, hidden)``.

    ``kept`` is the duty's stored dict without the rows left out; ``hidden`` names each row left out and why
    (``other_point`` | ``superseded`` | ``other_geometry`` | ``impossible_temperature``).
    """
    stored_duty = stored_duty if isinstance(stored_duty, dict) else {}
    kept: Dict[str, Any] = {}
    hidden: List[Dict[str, str]] = []
    for kind, row in stored_duty.items():
        if not isinstance(row, dict):
            kept[kind] = row
            continue
        if kind in _POINT_KINDS and not belongs_to_point(kind, row, op, limit_search=_is_limit_search(row)):
            hidden.append({"kind": str(kind), "reason": "other_point"})
            continue
        if kind in ("thermal", "coupled", "coupled_limit"):
            bad = impossible_temperature(row)
            if bad is not None:
                hidden.append({"kind": str(kind), "reason": "impossible_temperature", "value": bad})
                continue
        kept[kind] = row
    rs = kept.get("rotor_stress")
    if isinstance(rs, dict) and rs:
        if solver_status(rs) == "superseded":
            hidden.append({"kind": "rotor_stress", "reason": "superseded"})
            del kept["rotor_stress"]
        elif _geometry_status(rs, kept) == "differs":
            hidden.append({"kind": "rotor_stress", "reason": "other_geometry"})
            del kept["rotor_stress"]
    return kept, hidden


def own_fields(rows: Any, op: Dict[str, Optional[float]], kept: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """The stored field maps of one duty that belong to it.

    A map belongs when its own stamped speed is the point's (a map with no stamp cannot be tied to the point and is left out),
    except the stress map, which also belongs when the duty's own stress RECORD was kept (the record and the map are one run).
    A temperature map with a physically impossible range is never offered.
    """
    out: List[Dict[str, Any]] = []
    hidden: List[Dict[str, str]] = []
    for r in rows or []:
        kind = r.get("kind")
        pt = r.get("point") if isinstance(r.get("point"), dict) else {}
        ok = belongs_to_point("em_run" if kind == "em" else kind, {"point": pt}, op) or (
            kind == "rotor_stress" and isinstance(kept.get("rotor_stress"), dict) and bool(kept.get("rotor_stress")))
        if not ok:
            hidden.append({"kind": f"field:{kind}", "reason": "other_point"})
            continue
        if kind == "thermal":
            bad = impossible_temperature({"T_min": pt.get("T_min"), "T_max": pt.get("T_max")})
            if bad is not None:
                hidden.append({"kind": "field:thermal", "reason": "impossible_temperature", "value": bad})
                continue
        out.append(r)
    return out, hidden


def thermal_block(stored: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Thermal results of one duty - or ``None`` when nothing is stored.

    SELECTION RULES (owner review 2026-10-26):
      * ``duty_temps`` - the duty's own temperatures - come from the converged coupled loop (``coupled``) at the duty's
        point; only when the duty has NO coupled row does the plain temperature map (``thermal``, steady state) stand in.
        Each says which run kind it is and when it was computed.
      * a coupled row that is a limit search (S1 / ``solve_to`` limits or continuous, mode limited) is NEVER the duty's
        temperature: it is returned as ``limit_search`` on its own line.
      * S1 (continuous limit) and S2 (time to the limit, duty cycle) are their own lines.
    """
    th = stored.get("thermal") if isinstance(stored.get("thermal"), dict) else None
    cp = stored.get("coupled") if isinstance(stored.get("coupled"), dict) else None
    cl = stored.get("coupled_limit") if isinstance(stored.get("coupled_limit"), dict) else None
    dc = stored.get("duty_cycle") if isinstance(stored.get("duty_cycle"), dict) else None
    cr = stored.get("continuous_rating") if isinstance(stored.get("continuous_rating"), dict) else None
    if not (th or cp or cl or dc or cr):
        return None
    # the limit search: its own kind (since 2026-10-26) or, in an old store, a `coupled` row that IS a limit search
    limit_row = _newest(cl, cp if (cp and _is_limit_search(cp)) else None)
    out: Dict[str, Any] = {}
    duty_temps: Optional[Dict[str, Any]] = None
    if cp and not _is_limit_search(cp):
        duty_temps = {"kind": "coupled", "winding_c": _num(cp.get("coil_temp_c")), "magnet_c": _num(cp.get("magnet_temp_c")),
                      "magnet_max_c": _num(cp.get("magnet_temp_max_c")), "bearing_c": _num(cp.get("bearing_temp_c")),
                      "converged": cp.get("converged") if isinstance(cp.get("converged"), bool) else None,
                      "drive": cp.get("drive"), "provenance": provenance(cp)}
        em = _coupled_em(cp)
        if em:
            duty_temps["em"] = em
        # the housing of the same loop's map, when that map states it
    elif cp is None and th and str(th.get("state") or "steady").lower() == "steady":
        comps = th.get("components") if isinstance(th.get("components"), dict) else {}
        duty_temps = {"kind": "thermal", "winding_c": _num((comps.get("winding") or {}).get("max")),
                      "magnet_c": _num((comps.get("magnet") or {}).get("max")), "drive": th.get("drive"),
                      "provenance": provenance(th)}
    if duty_temps is not None:
        if th and str(th.get("state") or "steady").lower() == "steady":
            cool = th.get("cooling") if isinstance(th.get("cooling"), dict) else {}
            for src in ((cool.get("mount") or {}).get("t_housing_mean_c"), (cool.get("heat_path") or {}).get("t_body_c")):
                if _num(src) is not None:
                    duty_temps["housing_c"] = _num(src)
                    duty_temps["housing_from"] = {"kind": "thermal", "computed_at": th.get("computed_at")}
                    break
        out["duty_temps"] = {k: v for k, v in duty_temps.items() if v is not None}
    if limit_row:
        out["limit_search"] = {"kind": "coupled_limit" if limit_row is cl else "coupled", "solve_to": limit_row.get("solve_to"),
                               "mode": limit_row.get("mode"),
                               "winding_c": _num(limit_row.get("coil_temp_c")), "magnet_c": _num(limit_row.get("magnet_temp_c")),
                               "provenance": provenance(limit_row)}
        out["limit_search"] = {k: v for k, v in out["limit_search"].items() if v is not None}
    # S1: the largest current that may run for ever at this duty's cooling (owner 2026-09-21).
    with_s1 = [r for r in (limit_row, cp) if isinstance(r, dict) and isinstance(r.get("continuous_rating"), dict) and r["continuous_rating"]]
    src1 = _newest(*with_s1)
    s1 = src1["continuous_rating"] if src1 else cr
    if isinstance(s1, dict) and s1:
        out["s1"] = {**_pick(s1, ("ok", "feasible", "I_cont_A_rms", "limiting_part", "limits_c", "temperatures_c",
                                  "record_is_s1", "verified", "note")),
                     "provenance": provenance(src1 or cr)}
    # S2: how long the point may run when it is over a limit (owner 2026-09-17), and the duty-cycle spec it came with.
    with_s2 = [r for r in (limit_row, cp) if isinstance(r, dict) and isinstance(r.get("time_to_limit"), dict) and r["time_to_limit"]]
    src2 = _newest(*with_s2)
    if src2:
        out["s2"] = {**plain(src2["time_to_limit"], 3), "provenance": provenance(src2)}
    if cp and isinstance(cp.get("duty_cycle"), dict) and cp["duty_cycle"]:
        out["regime"] = plain(cp["duty_cycle"], 2)
    if dc:
        out["cycle"] = {"spec": _pick(dc.get("spec"), ("kind", "cycle_s", "ed_pct", "t_on_s", "rest_duty")),
                        "limits": plain(dc.get("limits"), 2),
                        "peak_c": plain(((dc.get("cycle") or {}).get("peak_c")), 2),
                        "provenance": provenance(dc)}
    if not out:
        return None
    out["provenance"] = provenance(cp or th or limit_row or dc or cr)
    return out


def _rotor_stress_block(rs: Dict[str, Any]) -> Dict[str, Any]:
    parts = {}
    for name, p in (rs.get("parts") or {}).items():
        if isinstance(p, dict):
            parts[str(name)] = _pick(p, ("material", "safety_factor", "von_mises_max_mpa",
                                         "governing_stress_mpa", "strength_mpa", "strength_kind"))
    point = rs.get("point") if isinstance(rs.get("point"), dict) else {}
    rpm = _num(rs.get("rpm")) if _num(rs.get("rpm")) is not None else _num(point.get("rpm"))
    return {
        **_pick(rs, ("case", "sf_min", "sf_min_part", "sf_min_p05", "overspeed_factor",
                     "max_displacement_um", "rotor_od_growth_um", "interference_mm")),
        # the speed the stress solve ran at: the proof speed when the case is the proof case
        "proof_rpm": rpm,
        # the speed the solve was ASKED at (the proof case may run above it by the overspeed factor)
        "request_rpm": _num(point.get("rpm")),
        # the load case, from the record's own fields: speed, torque and which loads were applied
        "worst_case": {k: v for k, v in {"rpm": rpm, "torque_nm": _num(point.get("torque_nm")),
                                         "loads": point.get("loads"), "case": rs.get("case")}.items() if v is not None},
        "retention": _pick(rs.get("retention"), ("status", "verdict", "message")),
        "magnet_retention": _pick(rs.get("magnet_retention"), ("verdict", "share", "seated")),
        "air_gap": _pick(rs.get("air_gap"), ("clearance_um", "remaining_um", "closed_pct")),
        "limit_speed_rpm": _num((rs.get("limit_speed") or {}).get("rpm_sf1")),
        "parts": parts,
        "provenance": provenance(rs),
    }


def worst_case_rotor_stress(stored_by_duty: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The motor's ONE mechanical answer: the newest ``rotor_stress`` record of the die/configuration.

    The stress solve is a per-motor worst case (proof speed + peak torque + magnetic pull), not a per-duty result, so the
    newest record - whichever duty it happened to be filed under - is shown on every point. It is flagged, never hidden:

      * ``solver`` - ``superseded`` when it was made before the contact repair (#422), ``unknown`` with no date;
      * ``geometry`` - ``differs`` when its mesh-independent machine print (``geometry_fingerprint_v2``) differs from the
        newest electromagnetic / thermal record of the motor, ``match`` when equal, ``unknown`` when either has no print
        (the v1 fingerprint is a cache key that moves with the mesh, so it is not compared);
      * ``stale`` is true for ``superseded`` or ``differs`` - such a record is not the current answer.
    """
    rows = []
    for duty, kinds in (stored_by_duty or {}).items():
        rs = (kinds or {}).get("rotor_stress")
        if isinstance(rs, dict) and rs:
            rows.append((_when(rs), str(duty), rs))
    if not rows:
        return None
    from datetime import datetime, timezone
    floor = datetime.min.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: r[0] or floor, reverse=True)
    when, filed_under, rs = rows[0]
    ref = None
    for kinds in (stored_by_duty or {}).values():
        for kind in ("em_run", "coupled", "thermal"):
            row = (kinds or {}).get(kind)
            fp = row.get("geometry_fingerprint_v2") if isinstance(row, dict) else None
            w = _when(row)
            if fp and w and (ref is None or w > ref[0]):
                ref = (w, fp)
    mine = rs.get("geometry_fingerprint_v2")
    geometry = "unknown" if not (mine and ref) else ("match" if mine == ref[1] else "differs")
    solver = solver_status(rs)
    block = _rotor_stress_block(rs)
    block.update({"filed_under": filed_under, "solver_status": solver, "geometry_status": geometry,
                  "stale": solver == "superseded" or geometry == "differs",
                  "records": len(rows),
                  "others": [{"duty": d, "computed_at": (r.get("computed_at") or r.get("recorded_at")),
                              "sf_min": _num(r.get("sf_min"))} for _w, d, r in rows[1:6]]})
    return block


def duty_rotor_stress(kept: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The rotor-stress block of ONE duty's own (already selected, see :func:`own_records`) record, with the record's own load
    case (``worst_case``: its speed and torque), or ``None``.  Never another duty's record."""
    rs = kept.get("rotor_stress")
    if not (isinstance(rs, dict) and rs):
        return None
    block = _rotor_stress_block(rs)
    block.update({"solver_status": solver_status(rs), "geometry_status": _geometry_status(rs, kept), "stale": False})
    return block


def mechanical_block(stored: Dict[str, Any], rotor_stress: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Mechanical results of one duty: its OWN stress record (see :func:`duty_rotor_stress`) plus the ring modes and critical
    speeds filed under it - or ``None`` when it has none."""
    modes = stored.get("modes") if isinstance(stored.get("modes"), dict) else None
    crit = stored.get("critical_speeds") if isinstance(stored.get("critical_speeds"), dict) else None
    if not (rotor_stress or modes or crit):
        return None
    out: Dict[str, Any] = {}
    if rotor_stress:
        out["rotor_stress"] = rotor_stress
    if modes:
        out["modes"] = {"frequencies_hz": plain(modes.get("frequencies_hz"), 2),
                        "f_switch_hz": _num(modes.get("f_switch_hz")), "provenance": provenance(modes)}
    if crit:
        out["critical_speeds"] = {"verdict": crit.get("verdict"), "rated_rpm": _num(crit.get("rated_rpm")),
                                  "critical_speeds": plain(crit.get("critical_speeds"), 3),
                                  "provenance": provenance(crit)}
    return out


def em_run_block(stored: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The electromagnetic kernel run's own numbers, with provenance (the saved block is in ``electromagnetic``)."""
    row = stored.get("em_run") if isinstance(stored.get("em_run"), dict) else None
    if not row:
        return None
    nums = row.get("numbers") if isinstance(row.get("numbers"), dict) else {}
    return {"numbers": {k: v for k, v in nums.items() if _num(v) is not None},
            "provenance": provenance(row)}


def fields_block(rows: Any) -> Dict[str, Any]:
    """Which field kinds are stored for one duty, each with its provenance."""
    out: Dict[str, Any] = {}
    for r in rows or []:
        kind = r.get("kind")
        if kind in FIELD_KINDS:
            out[kind] = {"provenance": provenance({}, {**r, "saved_at": r.get("saved_at")}),
                         "point": plain(r.get("point"), 1) or {}}
    return out


# ---------------------------------------------------------------------------
# the whole view
# ---------------------------------------------------------------------------

def build_view(die: str, config: str, result_rows: List[Dict[str, Any]], stored_by_duty: Dict[str, Any],
               fields_by_duty: Dict[str, Any], configure_duties: set) -> Dict[str, Any]:
    """The view of one motor: the points-limited decision plus, per duty, everything that is stored.

    ``result_rows`` are ``motors._results`` rows (operating point, electromagnetic numbers, comparison);
    ``stored_by_duty`` is ``duty_results.get``; ``fields_by_duty`` is ``duty_fields.have``;
    ``configure_duties`` the duties Configure can open through a full passport.
    """
    from motor_ai_sim import duty_results as dr
    duties = []
    for row in result_rows:
        name = str(row.get("duty") or "")
        # ONLY the records that belong to THIS point (same duty, solved at its speed and current, current solver and
        # machine, physical numbers): the rest is not shown at all (owner 2026-10-26)
        kept, hidden = own_records((stored_by_duty or {}).get(name) or {}, operating_of(row))
        field_rows, hidden_fields = own_fields((fields_by_duty or {}).get(name), operating_of(row), kept)
        fields = fields_block(field_rows)
        thermal = thermal_block(kept)
        mechanical = mechanical_block(kept, duty_rotor_stress(kept))
        em_run = em_run_block(kept)
        # the duty's own saved electromagnetic result (yaml) or any of its own records makes it a stored point
        has_anything = bool(row.get("electromagnetic") or row.get("comparison") or thermal or mechanical or em_run or fields)
        em_nums = row.get("electromagnetic") if isinstance(row.get("electromagnetic"), dict) else {}
        has_em_torque = any(_num(em_nums.get(k)) is not None for k in ("t_em_avg_nm", "t_em_nm", "t_avg_nm", "torque_avg_nm", "torque_nm"))
        duties.append({
            "duty": name,
            # WHERE the electromagnetic tiles come from: the EM run (made at a fixed winding temperature, magnets at their
            # card temperature) - never silently the coupled loop, whose own numbers are in the thermal block
            "sources": {"em": {"kind": "em_run" if has_em_torque else "coupled",
                               "coil_temp_c": _num(em_nums.get("coil_temp_c")), "magnet_temp_c": _num(em_nums.get("magnet_temp_c"))}},
            "has_result": has_anything,
            "operating_point": row.get("operating_point") or {},
            "electromagnetic": row.get("electromagnetic") or {},
            "comparison": row.get("comparison") or {},
            "em_run": em_run,
            "thermal": thermal,
            "mechanical": mechanical,
            "fields": fields,
            "kinds": dr.kinds_present(kept),
            # records filed under this duty that were left out, and why (diagnostics; the UI never draws them)
            "hidden_records": hidden + hidden_fields,
        })
    has_points = any(d["has_result"] for d in duties)
    return {
        "die": die, "config": config,
        "configure_available": bool(configure_duties),
        "configure_duties": sorted(configure_duties),
        # the one decision the UI needs: a motor with stored points and no full passport opens points-limited
        "mode": "full" if configure_duties else ("points" if has_points else "empty"),
        "duties": duties,
        "solver_now": dr.solver_stamp(),
    }


# ---------------------------------------------------------------------------
# field payloads
# ---------------------------------------------------------------------------

def _xy_mm(v: Any):
    """(n, 2) float64 array in millimetres from any store's convention ((2, n) or (n, 2); metres or mm)."""
    import numpy as np
    a = np.asarray(v, dtype=float)
    if a.ndim != 2:
        return None
    if a.shape[1] != 2 and a.shape[0] == 2:
        a = a.T
    if a.shape[1] != 2 or a.shape[0] < 3:
        return None
    if float(np.abs(a).max()) < 1.0:     # a motor is tens of millimetres across; the same numbers in metres are < 1
        a = a * 1e3
    return a


def _tris(v: Any):
    import numpy as np
    a = np.asarray(v)
    if a.ndim != 2:
        return None
    if a.shape[1] != 3 and a.shape[0] == 3:
        a = a.T
    if a.shape[1] != 3 or a.shape[0] < 1:
        return None
    return a.astype(int)


def _sig(a, digits: int = 5):
    """Round to ``digits`` significant figures (keeps a JSON payload small without flattening small values)."""
    import numpy as np
    x = np.asarray(a, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mag = 10.0 ** (np.floor(np.log10(np.abs(x))) - (digits - 1))
        out = np.where(x == 0, 0.0, np.round(x / mag) * mag)
    return np.where(np.isfinite(out), out, 0.0)


def _list(a, nd: Optional[int] = None):
    import numpy as np
    x = np.asarray(a, dtype=float)
    return (np.round(x, nd) if nd is not None else x).tolist()


def _meta_point(meta: Dict[str, Any]) -> Dict[str, Any]:
    return plain(meta.get("point"), 1) or {}


def _em_payload(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import numpy as np
    verts = _xy_mm(d.get("vertices"))
    tris = _tris(d.get("triangles"))
    if verts is None or tris is None:
        return None
    meta = d.get("meta") or {}
    n_tri = tris.shape[0]
    tags = d.get("tags")
    tags = np.asarray(tags, dtype=int).tolist() if tags is not None and len(tags) == n_tri else [0] * n_tri
    vm = verts / 1000.0
    out: Dict[str, Any] = {
        "vertices": _list(vm, 7), "triangles": tris.tolist(), "domain_per_tri": tags,
        "n_vertices": int(vm.shape[0]), "n_triangles": int(n_tri),
        "extent": [float(vm[:, 0].min()), float(vm[:, 0].max()), float(vm[:, 1].min()), float(vm[:, 1].max())],
        "outlines": [], "n_sectors": 1, "symmetry_mult": 1,
    }
    views = []
    b = d.get("b_mag_per_tri")
    if b is not None and len(b) == n_tri:
        out["Bmag_per_tri"] = _list(_sig(b))
        out["B_mag_max"] = float(np.max(b))
        views.append("Bmag")
    ld = d.get("loss_dens_per_tri")
    if ld is not None and len(ld) == n_tri:
        out["loss_density_per_tri"] = _list(_sig(ld))
        out["loss_dens_max"] = float(np.max(ld))
        if meta.get("loss_dens_label"):
            out["loss_density_label"] = str(meta["loss_dens_label"])
        out["eddy"] = bool(meta.get("eddy"))
        views.append("Loss")
    dc = d.get("demag_coef_per_tri")
    if dc is not None and len(dc) == n_tri:
        out["demag_coef_per_tri"] = _list(dc, 5)
        views.append("Demag")
    az = d.get("a_z_per_node")
    if az is not None and len(az) == vm.shape[0]:
        out["A_z_per_node"] = _list(_sig(az))
        out["A_z_min"] = float(np.min(az))
        out["A_z_max"] = float(np.max(az))
        views.append("Az")
    if "Bmag_per_tri" not in out:
        out["Bmag_per_tri"] = [0.0] * n_tri
    if "A_z_per_node" not in out:
        out["A_z_per_node"] = [0.0] * int(vm.shape[0])
        out["A_z_min"] = 0.0
        out["A_z_max"] = 0.0
    out["views"] = views
    return out


def _thermal_outlines(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stored thermal outlines regrouped as the tab's ``[{domain, loops}]`` ([] when the record has none)."""
    import numpy as np
    from motor_ai_sim.duty_fields import unpack_rings
    rings = unpack_rings(d)
    if not rings:
        return []
    doms = d.get("outline_domain")
    doms = np.asarray(doms, dtype=int).tolist() if doms is not None else [0] * len(rings)
    by: Dict[int, List[Any]] = {}
    for dom, ring in zip(doms, rings):
        by.setdefault(int(dom), []).append(ring)
    return [{"domain": k, "loops": v} for k, v in by.items()]


def _thermal_payload(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import numpy as np
    p = np.asarray(d.get("vertices"), dtype=float)
    tris = _tris(d.get("triangles"))
    t = d.get("temperature_per_node")
    if p.ndim != 2 or p.shape[1] != 2 or tris is None or t is None or len(t) != p.shape[0]:
        return None
    meta = d.get("meta") or {}
    n_tri = tris.shape[0]
    dom = d.get("domain_per_tri")
    dom = np.asarray(dom, dtype=int).tolist() if dom is not None and len(dom) == n_tri else [0] * n_tri
    out: Dict[str, Any] = {
        "vertices": _list(p, 7), "triangles": tris.tolist(), "domain_per_tri": dom,
        "n_vertices": int(p.shape[0]), "n_triangles": int(n_tri),
        "temperature_per_node": _list(t, 3),
        "T_min": float(np.min(t)), "T_max": float(np.max(t)),
        "part_names": meta.get("part_names") or {},
        "outlines": _thermal_outlines(d),
    }
    views = ["temp"]
    hf = d.get("heat_flux_per_tri")
    if hf is not None:
        hf = np.asarray(hf, dtype=float)
        if hf.ndim == 2 and hf.shape[0] == n_tri and hf.shape[1] == 2:
            out["heat_flux_per_tri"] = _list(_sig(hf))
            out["flux_mag_per_tri"] = _list(_sig(np.hypot(hf[:, 0], hf[:, 1])))
            views.append("flux")
    out["views"] = views
    return out


def domain_boundary_loops(vertices_mm: Any, triangles: Any, domain: Any, *,
                          rel: float = 2e-4, ndigits: int = 4) -> List[List[List[float]]]:
    """The part outlines of a mesh as its DOMAIN-BOUNDARY EDGES: ``[[[x0, y0], [x1, y1]], ...]`` (mm), one two-point loop
    per edge (the viewers close every loop onto itself, so an unchained edge draws exactly that edge).

    Used for a stored map whose record carries no outlines of its own (records written before 2026-10-26); new records
    store the outlines the live Mechanical tab drew and the viewer uses those.  An edge is an outline when the triangles on
    its two sides belong to different parts, or when it is the outer border.  The vertices are WELDED by coordinate first
    (bucket = ``rel`` x the largest coordinate): the mechanical mesh is SPLIT (interface nodes duplicated, one set per
    part) and a replicated sector answer is the sector copied with its own nodes, so by index a seam between two copies
    would read as a border - the zigzag of radial lines from the rim to the hub (owner screen 2026-10-26).
    """
    import numpy as np
    v = np.asarray(vertices_mm, dtype=float)
    t = np.asarray(triangles, dtype=np.int64)
    d = np.asarray(domain, dtype=np.int64)
    if v.size == 0 or t.size == 0:
        return []
    q = float(np.abs(v).max()) * rel or 1.0
    keys = np.round(v / q).astype(np.int64)
    _, first, ids = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    ids = np.asarray(ids).reshape(-1)
    xy = v[first]
    w = ids[t]
    e = np.concatenate([w[:, [0, 1]], w[:, [1, 2]], w[:, [2, 0]]])
    dom = np.concatenate([d, d, d])
    keep = e[:, 0] != e[:, 1]
    e, dom = e[keep], dom[keep]
    lo, hi = np.minimum(e[:, 0], e[:, 1]), np.maximum(e[:, 0], e[:, 1])
    key = lo * (int(ids.max()) + 1) + hi
    order = np.argsort(key, kind="stable")
    key, lo, hi, dom = key[order], lo[order], hi[order], dom[order]
    first_of = np.concatenate([[True], key[1:] != key[:-1]])
    starts = np.nonzero(first_of)[0]
    counts = np.diff(np.concatenate([starts, [key.size]]))
    d0 = dom[starts]
    d1 = np.where(counts > 1, dom[np.minimum(starts + 1, key.size - 1)], d0)
    draw = (counts == 1) | ((counts == 2) & (d0 != d1)) | (counts > 2)
    a, b = lo[starts][draw], hi[starts][draw]
    return [[[round(float(xy[i, 0]), ndigits), round(float(xy[i, 1]), ndigits)],
             [round(float(xy[j, 0]), ndigits), round(float(xy[j, 1]), ndigits)]] for i, j in zip(a, b)]


def _mech_payload(d: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    import numpy as np
    verts = _xy_mm(d.get("vertices"))
    tris = _tris(d.get("triangles"))
    vm = d.get("vm_per_tri")
    if verts is None or tris is None or vm is None:
        return None
    meta = d.get("meta") or {}
    n_tri = tris.shape[0]
    dom = d.get("domain_per_tri")
    dom = np.asarray(dom, dtype=int).tolist() if dom is not None and len(dom) == n_tri else [0] * n_tri
    case = str(meta.get("case") or "primary")
    case_field: Dict[str, Any] = {"vm_per_tri": _list(_sig(vm))}
    views = ["vm"]
    for key, view in (("s_p1_per_tri", "s1"), ("sf_per_tri", "sf")):
        a = d.get(key)
        if a is not None and len(a) == n_tri:
            case_field[key] = _list(_sig(a))
            views.append(view)
    u = d.get("u_per_node")
    um = d.get("u_mag_per_node")
    if u is not None and len(u) == verts.shape[0]:
        case_field["u_per_node"] = _list(_sig(u))
    if um is not None and len(um) == verts.shape[0]:
        case_field["u_mag_per_node"] = _list(_sig(um))
        views.insert(0, "disp")
    else:
        case_field["u_mag_per_node"] = [0.0] * int(verts.shape[0])
        case_field["u_per_node"] = [[0.0, 0.0]] * int(verts.shape[0])
    ext = float(np.abs(verts).max())
    from motor_ai_sim.duty_fields import unpack_rings
    # the outlines the live Mechanical tab drew (records since 2026-10-26); an older record gets the welded domain boundaries
    rings = unpack_rings(d) or domain_boundary_loops(verts, tris, dom)
    segs = {}
    for name in list(d.keys()):
        if isinstance(name, str) and name.startswith("contact_seg::"):
            arr = np.asarray(d[name], dtype=float).reshape(-1, 4)
            segs[name.split("::", 1)[1]] = _list(arr, 4)
    return {
        "vertices": _list(verts, 5), "triangles": tris.tolist(), "domain_per_tri": dom,
        "part_names": meta.get("part_names") or {}, "outlines": rings, "extent": ext,
        "n_sectors": int(meta.get("n_sectors") or 1), "symmetry_mult": int(meta.get("n_sectors") or 1),
        "contact_segments_per_pair": segs,
        "cases": {case: case_field}, "case": case, "views": views,
    }


_PAYLOADS = {"em": _em_payload, "thermal": _thermal_payload, "rotor_stress": _mech_payload}


_KIND_ALIASES = {"em": "em", "electromagnetic": "em", "magnetic": "em",
                 "thermal": "thermal", "temperature": "thermal",
                 "rotor_stress": "rotor_stress", "mech": "rotor_stress", "mechanical": "rotor_stress", "stress": "rotor_stress"}


def field_kind(name: Any) -> Optional[str]:
    """The stored field kind a request name means (``mech`` -> ``rotor_stress``), or ``None`` for a name nothing is stored under."""
    return _KIND_ALIASES.get(str(name or "").strip().lower())


def field_payload(die: str, config: str, duty: str, kind: str) -> Optional[Dict[str, Any]]:
    """The stored field of one duty, shaped like the payload its tab's adapter draws - or ``None`` (not computed).

    ``{"kind", "payload", "provenance", "point"}``; the arrays are rounded (5 significant figures) so a map is
    a few hundred kB of JSON, not megabytes.
    """
    from motor_ai_sim import duty_fields as df
    kind = field_kind(kind)
    if kind is None:
        return None
    d = df.load(die, config, duty, kind)
    if not d:
        return None
    payload = _PAYLOADS[kind](d)
    if payload is None:
        return None
    meta = d.get("meta") or {}
    rows = (df.have(die, config) or {}).get(duty) or []
    row = next((r for r in rows if r.get("kind") == kind), {})
    return {"kind": kind, "duty": duty, "payload": payload,
            "provenance": provenance({}, {**meta, "n_triangles": row.get("n_triangles"),
                                           "saved_at": meta.get("saved_at")}),
            "point": _meta_point(meta)}
