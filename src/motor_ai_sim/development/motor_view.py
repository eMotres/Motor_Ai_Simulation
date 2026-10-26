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
from typing import Any, Dict, List, Optional

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
    # the magnetic pull the worst case carried (2026-10-26): its source and size
    pull = rs.get("pull") if isinstance(rs.get("pull"), dict) else {}
    return {
        **_pick(rs, ("case", "sf_min", "sf_min_part", "sf_min_p05", "overspeed_factor",
                     "max_displacement_um", "rotor_od_growth_um", "interference_mm")),
        # the speed the stress solve ran at: the proof speed when the case is the proof case
        "proof_rpm": rpm,
        # the load case, from the record's own fields: speed, torque and which loads were applied
        "worst_case": {k: v for k, v in {"rpm": rpm, "torque_nm": _num(point.get("torque_nm")),
                                         "loads": point.get("loads"), "case": rs.get("case"),
                                         "pull_source": pull.get("source"),
                                         "pull_mpa": _num(pull.get("pressure_peak_mpa")),
                                         "pull_per_pole_n": _num(pull.get("radial_force_per_pole_n")),
                                         "pull_label": pull.get("label"),
                                         "pull_estimate": pull.get("estimate"),
                                         # single snapshot (not a worst case)
                                         # or the frames' envelope (F1)
                                         "pull_load_basis": pull.get("load_basis")}.items() if v is not None},
        "retention": _pick(rs.get("retention"), ("status", "verdict", "message")),
        "load_case": rs.get("load_case"),
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


def mechanical_block(stored: Dict[str, Any], worst: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Mechanical results shown at one duty: the motor's worst case (see :func:`worst_case_rotor_stress`) plus the
    ring modes and critical speeds filed under THIS duty - or ``None``."""
    modes = stored.get("modes") if isinstance(stored.get("modes"), dict) else None
    crit = stored.get("critical_speeds") if isinstance(stored.get("critical_speeds"), dict) else None
    if not (worst or modes or crit):
        return None
    out: Dict[str, Any] = {}
    if worst:
        out["rotor_stress"] = worst
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
    worst = worst_case_rotor_stress(stored_by_duty)
    duties = []
    for row in result_rows:
        name = str(row.get("duty") or "")
        stored = (stored_by_duty or {}).get(name) or {}
        fields = fields_block((fields_by_duty or {}).get(name))
        thermal = thermal_block(stored)
        mechanical = mechanical_block(stored, worst)
        em_run = em_run_block(stored)
        # the worst case is the motor's, shown everywhere: it alone does not make a duty a stored POINT
        has_anything = bool(row.get("has_result") or thermal or (stored.get("rotor_stress") or stored.get("modes")
                                                                   or stored.get("critical_speeds")) or em_run or fields)
        duties.append({
            "duty": name,
            "has_result": has_anything,
            "operating_point": row.get("operating_point") or {},
            "electromagnetic": row.get("electromagnetic") or {},
            "comparison": row.get("comparison") or {},
            "em_run": em_run,
            "thermal": thermal,
            "mechanical": mechanical,
            "fields": fields,
            "kinds": dr.kinds_present(stored),
        })
    has_points = any(d["has_result"] for d in duties)
    return {
        "die": die, "config": config,
        "configure_available": bool(configure_duties),
        "configure_duties": sorted(configure_duties),
        # the one decision the UI needs: a motor with stored points and no full passport opens points-limited
        "mode": "full" if configure_duties else ("points" if has_points else "empty"),
        "mechanical_worst_case": worst,
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
        "outlines": [],
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
    segs = {}
    for name in list(d.keys()):
        if isinstance(name, str) and name.startswith("contact_seg::"):
            arr = np.asarray(d[name], dtype=float).reshape(-1, 4)
            segs[name.split("::", 1)[1]] = _list(arr, 4)
    return {
        "vertices": _list(verts, 5), "triangles": tris.tolist(), "domain_per_tri": dom,
        "part_names": meta.get("part_names") or {}, "outlines": [], "extent": ext,
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
