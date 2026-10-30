"""What the configuration's STORED results say, in the shape the comparison needs.  Nothing is solved, nothing written.

Sources, in order of preference:

* each duty's saved ``summary`` in ``<die>/<config>.yaml`` (the electromagnetic source of truth), falling back to the
  duty's ``em_run`` row of ``duty_results`` (a run that was never saved);
* ``duty_results`` ``coupled`` / ``thermal`` rows for temperatures;
* the magnet cards of the duty's magnet grade (``materials_library``: one card per temperature) for Br(T);
* the configuration's ``bearings`` block and geometry for the bearing friction (SKF) and the windage (``bearings``).

``collect`` is the only function that touches files; ``from_docs`` builds the same answer from documents, which is what
the tests drive.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from motor_ai_sim.motor_tests.compare import num

log = logging.getLogger(__name__)

_NOLOAD_NAME = re.compile(r"no[\s_-]?load|open[\s_-]?circuit|free[\s_-]?run|idle|emf", re.I)
_STATOR_ONLY_NAME = re.compile(r"stator[\s_-]?only|no[\s_-]?rotor|without[\s_-]?rotor|rotor[\s_-]?out", re.I)
#: A duty below this terminal current is a no-load point.
NOLOAD_MAX_A = 0.5


def _summary_numbers(duty: Dict[str, Any], stored: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    s = duty.get("summary")
    if isinstance(s, dict) and s:
        return s, "saved"
    run = (stored or {}).get("em_run")
    if isinstance(run, dict) and isinstance(run.get("numbers"), dict) and run["numbers"]:
        return {**run["numbers"], **(run.get("text") or {})}, "em_run"
    return {}, ""


def _components(row: Any) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    for key in ("components",):
        if isinstance(row.get(key), dict):
            return row[key]
    th = row.get("thermal") if isinstance(row.get("thermal"), dict) else None
    if th and isinstance(th.get("components"), dict):
        return th["components"]
    return {}


def _component_temp(comps: Dict[str, Any], needles) -> Optional[float]:
    best = None
    for name, v in comps.items():
        if not any(n in str(name).lower() for n in needles):
            continue
        t = num(v.get("max") if isinstance(v, dict) else v)
        if t is not None and (best is None or t > best):
            best = t
    return best


def _temps(stored: Dict[str, Any]) -> Dict[str, Optional[float]]:
    for kind in ("coupled", "thermal"):
        comps = _components((stored or {}).get(kind))
        if comps:
            return {"winding_c": _component_temp(comps, ("copper", "coil", "wind")),
                    "magnet_c": _component_temp(comps, ("magnet",)), "source": kind}
    return {"winding_c": None, "magnet_c": None, "source": None}


def _magnet_card(cfg_doc: Dict[str, Any], duty: Optional[Dict[str, Any]]) -> Optional[str]:
    for src in ((duty or {}).get("materials"), cfg_doc.get("materials")):
        if isinstance(src, dict) and isinstance(src.get("magnet"), str) and src["magnet"]:
            return src["magnet"]
    return None


def grade_points(card: Optional[str], library: Dict[str, Any]) -> Dict[str, Any]:
    """``{"card", "grade", "temp_c", "points": [(T, Br), ...]}`` for the card's grade (every card of the same grade).

    Since 2026-10-30 a grade is ONE 20 C card and its Br at any temperature is derived (materials.at_temperature): the
    points are that derivation every 10 K from 20 C to the grade's maximum working temperature.  A former
    per-temperature name ('N52UH_150C') stands for its grade card, quoted at that temperature."""
    from motor_ai_sim import materials as _M
    mags = (library or {}).get("magnet") or {}
    _old = _M.legacy_magnet(card) if card and card not in mags else None
    key = _old[0] if _old else card
    raw = mags.get(key) if key else None
    if not isinstance(raw, dict):
        return {"card": card, "grade": None, "temp_c": None, "points": []}
    try:
        m = _M._parse_magnet(str(key), raw)
        t_hi = float(m.max_working_temp_c or 180.0)
        derived = [(float(t), round(float(m.at_temperature(float(t)).Br), 4))
                   for t in range(20, int(t_hi) + 1, 10)] if m.temperature_c is not None else []
    except Exception:   # noqa: BLE001 - a card the model cannot move keeps the per-card points below
        derived = []
    if derived:
        return {"card": card, "grade": str(raw.get("grade") or key),
                "temp_c": _old[1] if _old else num(raw.get("temperature_c")), "points": derived}

    def _grade(name: str, r: Dict[str, Any]) -> str:
        g = r.get("grade")
        if isinstance(g, str) and g:
            return g
        return re.sub(r"_\d+(\.\d+)?C$", "", str(name))
    grade = _grade(card, raw)
    pts = []
    for name, r in mags.items():
        if isinstance(r, dict) and _grade(name, r) == grade and num(r.get("temperature_c")) is not None \
                and num(r.get("Br")) is not None:
            pts.append((float(r["temperature_c"]), float(r["Br"])))
    return {"card": card, "grade": grade, "temp_c": num(raw.get("temperature_c")), "points": sorted(set(pts))}


def factors_from_end3d(end3d: Optional[Dict[str, Any]], source: str = "Stage A") -> Dict[str, Any]:
    """The 3-D end-effect factors a comparison applies, from a passport block (``end3d``).

    ``k_flux`` (voltages / EMF) is the Stage A flux factor, accepted in (0.5, 1.0] exactly as the report does;
    ``k_torque`` (torque, Kt) is ``end3d_factors.torque_factor`` (the measured k_T, else k_flux).  Both None = 2-D."""
    from motor_ai_sim import end3d_factors as E
    e = end3d if isinstance(end3d, dict) else {}
    k = num(e.get("k_flux"))
    if k is None or not (0.5 < k <= 1.0):
        return {"k_flux": None, "k_torque": None, "torque_basis": E.BASIS_2D, "source": None, "inherited": False}
    kt, basis = E.torque_factor(e)
    return {"k_flux": k, "k_torque": kt if kt is not None else k, "torque_basis": basis, "source": source,
            "inherited": bool(e.get("inherited"))}


def end3d_from_summaries(cfg_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``end3d`` block a duty's saved summary carries (the passport that existed when it was solved)."""
    for d in (cfg_doc or {}).get("duties") or []:
        s = d.get("summary") if isinstance(d, dict) else None
        if isinstance(s, dict) and isinstance(s.get("end3d"), dict) and num(s["end3d"].get("k_flux")) is not None:
            return s["end3d"]
    return None


def resolve_end3d(die_doc: Dict[str, Any], cfg_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The Stage A passport of THIS configuration's geometry - the resolver every stored duty already uses when it is
    served (``routes.family`` apply: the die geometry + the configuration's overrides -> ``_geometry_fingerprint`` ->
    ``routes.simulation._end3d_lookup``, which also adds the measured k_T).  None when there is no passport."""
    from motor_ai_sim.routes import family
    from motor_ai_sim.routes.simulation import _end3d_lookup, _geometry_fingerprint
    geo = dict((die_doc or {}).get("geometry") or {})
    geo.update({k: v for k, v in ((cfg_doc or {}).get("geometry_overrides") or {}).items() if v is not None})
    for k, v in family._ABSENT_MEANS.items():
        if geo.get(k) is None:
            geo[k] = v
    from motor_ai_sim.geometry.motor_geometry import fresh_derived_on_write, refresh_derived_geometry
    if fresh_derived_on_write():
        geo = refresh_derived_geometry(geo)
    return _end3d_lookup(_geometry_fingerprint(geo), geo)


def from_docs(die_doc: Dict[str, Any], cfg_doc: Dict[str, Any], stored_by_duty: Dict[str, Any],
              library: Dict[str, Any], *, mech: Optional[Callable] = None,
              end3d: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The model answer from the documents (pure apart from the injected ``mech`` function).

    ``end3d`` = the configuration's current Stage A passport (``resolve_end3d``); without it the passport a duty's saved
    summary carries is used; without either the model is 2-D (``factors.k_flux`` None)."""
    cfg_doc = cfg_doc if isinstance(cfg_doc, dict) else {}
    if isinstance(end3d, dict):
        factors = factors_from_end3d(end3d, "Stage A")
    else:
        factors = factors_from_end3d(end3d_from_summaries(cfg_doc), "Stage A, stored with the duty")
    winding = cfg_doc.get("winding") if isinstance(cfg_doc.get("winding"), dict) else {}
    connection = str(winding.get("star_delta") or "star").lower()
    duties: List[Dict[str, Any]] = []
    noload: List[Dict[str, Any]] = []
    r_ref = l_ref = l_stator = None
    mass = None
    magnet = None
    for d in cfg_doc.get("duties") or []:
        if not isinstance(d, dict) or not d.get("name"):
            continue
        name = str(d["name"])
        stored = (stored_by_duty or {}).get(name) or {}
        s, src = _summary_numbers(d, stored)
        rpm = num(s.get("rpm")) or num(d.get("rpm"))
        cur = num(s.get("I_phase_rms_A"))
        if cur is None:
            cur = num(d.get("current_arms"))
        conn = str(s.get("star_delta") or s.get("connection") or connection).lower()
        card = _magnet_card(cfg_doc, d)
        mag = grade_points(card, library)
        mag_t = num(s.get("magnet_temp_c")) or num(s.get("magnet_temp_C")) or mag.get("temp_c")
        if magnet is None and card:
            magnet = mag
        temps = _temps(stored)
        eff = num(s.get("efficiency_shaft")) or num(s.get("efficiency"))
        # DC link in, shaft out: the controller record's wall-to-shaft efficiency (inverter x motor), when stored
        ctrl = (stored or {}).get("controller")
        eff_sys = num(((ctrl or {}).get("efficiency") or {}).get("wall_to_shaft")) if isinstance(ctrl, dict) else None
        p_shaft = num(s.get("P_shaft_net_W"))
        torque = (p_shaft / (rpm * 3.141592653589793 / 30.0)) if (p_shaft is not None and rpm) else num(s.get("T_em_avg_Nm"))
        row = {"name": name, "mode": str(d.get("mode") or "motor"), "rpm": rpm, "current": cur, "source": src,
               "connection": conn, "torque_nm": torque, "torque_kind": "shaft" if p_shaft is not None else "em",
               "eff_pct": None if eff is None else 100.0 * eff,
               "eff_system_pct": None if eff_sys is None else 100.0 * eff_sys, "winding_c": temps["winding_c"],
               "magnet_c": temps["magnet_c"], "temps_source": temps["source"], "magnet_temp_c": mag_t}
        duties.append(row)
        if not s:
            continue
        if mass is None:
            mass = num(s.get("mass_total_kg"))
        r_ph, t_coil = num(s.get("R_phase_ohm")), num(s.get("coil_temp_C"))
        if r_ph and t_coil is not None and r_ref is None:
            r_ref = {"duty": name, "r_phase_ohm": r_ph, "coil_temp_c": t_coil, "connection": conn}
        ld = num(s.get("Ld_inc_mH")) or num(s.get("Ld_mH"))
        lq = num(s.get("Lq_inc_mH")) or num(s.get("Lq_mH"))
        if ld and lq:
            cand = {"duty": name, "ld_mh": ld, "lq_mh": lq, "current": cur, "connection": conn,
                    "method": s.get("ldq_method") or ("differential" if s.get("Ld_inc_mH") else "stored")}
            if _STATOR_ONLY_NAME.search(name) or s.get("stator_only"):
                l_stator = l_stator or cand
            elif l_ref is None or (cur is not None and (l_ref.get("current") is None or cur < l_ref["current"])):
                l_ref = cand
        is_noload = (cur is not None and cur <= NOLOAD_MAX_A) or bool(_NOLOAD_NAME.search(name))
        vpk = num(s.get("V_line_peak_V"))
        if is_noload and rpm and vpk:
            terms = s.get("P_core_terms") if isinstance(s.get("P_core_terms"), dict) else {}
            hy = ed = ex = None
            if terms:
                hy = sum(num((terms.get(p) or {}).get("hysteresis_W")) or 0.0 for p in ("stator", "rotor"))
                ed = sum(num((terms.get(p) or {}).get("eddy_W")) or 0.0 for p in ("stator", "rotor"))
                ex = sum(num((terms.get(p) or {}).get("excess_W")) or 0.0 for p in ("stator", "rotor"))
            p_solid = num(s.get("P_solid_W"))
            if p_solid is None:
                p_solid = (num(s.get("P_mag_W")) or 0.0) + (num(s.get("P_shaft_W")) or 0.0) + (num(s.get("P_sleeve_W")) or 0.0)
            noload.append({"duty": name, "rpm": rpm, "v_ll_peak": vpk, "v_ll_rms": num(s.get("V_line_rms_V")),
                           "hyst": hy, "eddy": ed, "excess": ex, "p_core": num(s.get("P_core_W")),
                           "p_solid": p_solid, "magnet_temp_c": mag_t})
    return {"connection": connection, "duties": duties, "noload": noload, "r_ref": r_ref, "l_ref": l_ref,
            "l_stator_only": l_stator, "magnet": magnet or {"card": None, "grade": None, "temp_c": None, "points": []},
            "mass_kg": mass, "mech": mech, "end3d": factors, "has_bearings": bool(mech and getattr(mech, "has_bearings", False)),
            "available": bool(duties)}


def mech_function(cfg_doc: Dict[str, Any], geometry: Dict[str, Any], rotor_mass_kg: Optional[float]) -> Optional[Callable]:
    """``f(rpm, temp_c) -> {"bearings_w", "windage_w"}``: bearings (SKF) only when the configuration names them,
    windage whenever the geometry carries the gap.  None when neither is available."""
    from motor_ai_sim import bearings as B
    assignment = cfg_doc.get("bearings") if isinstance(cfg_doc.get("bearings"), dict) else None
    has_b = bool(assignment) and B.has_bearings(assignment)
    if not has_b and not geometry:
        return None

    def f(rpm: float, temp_c: Optional[float]) -> Optional[Dict[str, float]]:
        t = 25.0 if temp_c is None else float(temp_c)
        out: Dict[str, float] = {}
        if has_b:
            res = B.machine_bearing_losses(assignment, rpm=rpm, temp_c=t, rotor_mass_kg=float(rotor_mass_kg or 0.0),
                                           geometry=geometry or None, windage_temp_c=t)
            out["bearings_w"] = float(res.get("P_bearings_W") or 0.0)
            if res.get("windage"):
                out["windage_w"] = float(res.get("P_windage_W") or 0.0)
        if "windage_w" not in out and geometry:
            w = B.windage_from_geometry(geometry, rpm, temp_c=t)
            if w:
                out["windage_w"] = float(w["P_W"])
        return out or None
    f.has_bearings = has_b                                   # type: ignore[attr-defined]
    return f


def collect(die: str, config: str) -> Dict[str, Any]:
    """Read the stores for ``die/config`` and return :func:`from_docs`' answer (``available: False`` when unreadable)."""
    try:
        from motor_ai_sim.routes import family
        from motor_ai_sim import duty_results, materials
        cfg_doc = family._load_yaml(family._cfg_file(die, config), "configuration")
        try:
            die_doc = family._load_yaml(family._die_file(die), "die")
        except Exception:                                   # noqa: BLE001
            die_doc = {}
        stored = duty_results.get(die, config) or {}
        library = materials._load() or {}
    except Exception as e:                                  # noqa: BLE001
        log.info("motor tests: no model for %s/%s (%s)", die, config, e)
        return {"available": False, "duties": [], "noload": [], "r_ref": None, "l_ref": None, "l_stator_only": None,
                "magnet": {"points": []}, "mass_kg": None, "mech": None, "connection": "star", "has_bearings": False,
                "end3d": factors_from_end3d(None)}
    geometry: Dict[str, Any] = {}
    try:
        from motor_ai_sim.passport_v1.snapshot import resolve_geometry
        geometry = resolve_geometry(die_doc, cfg_doc) if die_doc.get("geometry") else {}
    except Exception:                                       # noqa: BLE001
        geometry = {}
    rotor_mass = None
    try:
        from motor_ai_sim.mech_losses import rotor_mass_from_summary
        for d in cfg_doc.get("duties") or []:
            if isinstance(d, dict) and isinstance(d.get("summary"), dict):
                rotor_mass = rotor_mass_from_summary(d["summary"])
                if rotor_mass:
                    break
    except Exception:                                       # noqa: BLE001
        rotor_mass = None
    mech = None
    try:
        mech = mech_function(cfg_doc, geometry, rotor_mass)
    except Exception:                                       # noqa: BLE001
        mech = None
    e3 = None
    try:
        e3 = resolve_end3d(die_doc, cfg_doc)
    except Exception:                                       # noqa: BLE001 - no passport is the normal state
        log.debug("motor tests: no 3-D passport for %s/%s", die, config, exc_info=True)
    out = from_docs(die_doc, cfg_doc, stored, library, mech=mech, end3d=e3)
    out["mass"] = whole_motor_mass(die, config)
    return out


def whole_motor_mass(die: str, config: str) -> Optional[Dict[str, Any]]:
    """``motor_mass.mass_summary`` of the configuration (full mass, partial flag and why, source) for the Tests weight row;
    ``None`` when it cannot be read.  A stated CAD mass names its source (``cad_source``)."""
    try:
        from motor_ai_sim.modules import motor_mass as _mm
        ms = _mm.mass_summary(die, config)
        if ms is None:
            return None
        if ms.get("source") == "cad_stated":
            cad = _mm.config_cad_mass(_mm.find_die_dir(die), config) or {}
            ms = dict(ms, cad_source=cad.get("source"), cad_date=cad.get("date"))
        return ms
    except Exception:                                       # noqa: BLE001 - a mass hiccup never fails the table
        log.debug("motor tests: no full mass for %s/%s", die, config, exc_info=True)
        return None
