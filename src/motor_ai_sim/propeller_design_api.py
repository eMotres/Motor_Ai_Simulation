"""Logic behind ``/api/propellers/design/*`` (stage P2 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md): the routes are thin wrappers.

Everything here works on a plain design vector (``propeller_design``).  Drafts are JSON files in the CALLER'S workspace
(``workspace.root() / propeller_designs``), never in the shared catalog.  Nothing here writes anywhere else.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_design as pd
from motor_ai_sim import workspace

DRAFT_SCHEMA = "propeller-draft-1"
DRAFT_DIR = "propeller_designs"
NAME_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,59}$")
MAX_SWEEP_POINTS = 25
MAX_DRAFTS = 200


class ApiProblem(Exception):
    """Carries an HTTP status and a detail the route turns into an HTTPException."""

    def __init__(self, status: int, detail: Any):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def _design() -> pd.Design:
    return pd.load_design()


def _clean_vector(vec: Any) -> Dict[str, Any]:
    if not isinstance(vec, dict):
        raise ApiProblem(422, "vector must be an object of design variables")
    out: Dict[str, Any] = {}
    for k, v in vec.items():
        if not isinstance(k, str):
            raise ApiProblem(422, "variable names must be strings")
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ApiProblem(422, "variable %r must be a number or a string" % k)
        if isinstance(v, float) and not math.isfinite(v):
            raise ApiProblem(422, "variable %r must be finite" % k)
        out[k] = v
    return out


def family(diameter_in: int = 10, blades: int = 2) -> Dict[str, Any]:
    d = _design()
    if diameter_in not in d.diameters_in:
        raise ApiProblem(422, "diameter_in must be one of %s" % list(d.diameters_in))
    if blades not in d.blades_options:
        raise ApiProblem(422, "blades must be one of %s" % d.blades_options)
    seed = pd.seed_vector(d, diameter_in, blades)
    return {
        "id": d.id, "banner": pd.BANNER, "diameters_in": list(d.diameters_in), "blades_options": d.blades_options,
        "scaling_rule": d.raw["scaling_rule"],
        "operating_points": {n: {"die": o.die, "config": o.config, "torque_limit_Nm": o.torque_limit_Nm, "rated_rpm_ref": o.rated_rpm_ref,
                                 "rpm_search_max": o.rpm_search_max, "motor_radius_mm": o.motor_radius_mm, "source": o.source}
                             for n, o in d.operating_points.items()},
        "tip_mach_limit": {"value": d.tip_mach_limit, "confirmed": bool(d.raw["tip_mach_limit"].get("confirmed_by")),
                           "source": d.raw["tip_mach_limit"]["source"]},
        "manufacturing": {"process": d.manufacturing.process, "unset": d.manufacturing.unset(), "unconfirmed": d.manufacturing.unconfirmed(),
                          "limits": {n: {"value": l.value, "unit": l.unit, "source": l.source, "confirmed": l.confirmed}
                                     for n, l in d.manufacturing.limits.items()}},
        "inputs_unconfirmed": d.inputs_unconfirmed(),
        "control_r_over_R": list(d.control), "seed": seed, "variables": pd.variables_table(d, seed),
    }


def check(vec: Any) -> Dict[str, Any]:
    d = _design()
    v = _clean_vector(vec)
    viol = pd.check_candidate(d, v)
    return {"valid": not viol, "violations": viol}


def geometry(vec: Any) -> Dict[str, Any]:
    """The candidate as a propeller-2 dict plus the per-zone manufacturing report (what the page draws)."""
    d = _design()
    v = _clean_vector(vec)
    viol = pd.check_candidate(d, v)
    if viol:
        raise ApiProblem(422, {"message": "invalid candidate", "violations": viol})
    raw = pd.candidate_to_propeller2(d, v)
    zones = []
    R_mm = float(v["diameter_in"]) * 25.4 / 2.0
    for z in ("root", "mid", "tip"):
        a, b = d.raw["zones"][z]
        sts = [s for s in raw["blade"]["stations"] if a <= s["r_mm"] / R_mm < b]
        if not sts:
            continue
        s = sts[len(sts) // 2]
        e = af.generated(v["airfoil_" + z]) if str(v["airfoil_" + z]).startswith("naca4-") else af.get_airfoil(v["airfoil_" + z])
        ms = af.manufactured_section(e.x, e.yu, e.yl, s["chord_mm"], d.manufacturing)
        zones.append({"zone": z, "airfoil": v["airfoil_" + z], "station_r_mm": s["r_mm"], "chord_mm": s["chord_mm"],
                      "t_c_ideal": ms.ideal.t_c, "t_c_manufactured": ms.made.t_c,
                      "te_thickness_mm": ms.made.te_gap_c * s["chord_mm"], "le_radius_mm": ms.made.le_radius_c * s["chord_mm"],
                      "active_limits": list(ms.active), "flags": list(ms.flags)})
    return {"propeller": raw, "zones": zones, "banner": pd.BANNER,
            "outline": [{"r_mm": s["r_mm"], "chord_mm": s["chord_mm"], "twist_deg": s["twist_deg"]} for s in raw["blade"]["stations"]]}


def evaluate(vec: Any, op: str, v_inf: float = 0.0) -> Dict[str, Any]:
    d = _design()
    v = _clean_vector(vec)
    if op not in d.operating_points:
        raise ApiProblem(422, "unknown operating point %r; known: %s" % (op, sorted(d.operating_points)))
    if not (isinstance(v_inf, (int, float)) and math.isfinite(v_inf) and 0.0 <= v_inf <= 60.0):
        raise ApiProblem(422, "v_inf must be a number between 0 and 60 m/s")
    viol = pd.check_candidate(d, v)
    if viol:
        raise ApiProblem(422, {"message": "invalid candidate", "violations": viol})
    try:
        return pd.evaluate_candidate(d, v, op, v_inf=float(v_inf))
    except pd.DesignError as exc:
        raise ApiProblem(422, str(exc))


def sweep(vec: Any, op: str, variable: str, values: Optional[List[Any]] = None, lo: Optional[float] = None,
          hi: Optional[float] = None, n: int = 7) -> Dict[str, Any]:
    """Evaluate the candidate with ONE variable changed: explicit ``values`` or ``n`` points from ``lo`` to ``hi`` (numeric variables)."""
    d = _design()
    v = _clean_vector(vec)
    if variable not in pd.variable_names(d):
        raise ApiProblem(422, "unknown variable %r" % variable)
    if values is None:
        if not isinstance(v.get(variable), (int, float)):
            raise ApiProblem(422, "give explicit values for the non-numeric variable %r" % variable)
        if lo is None or hi is None or not (isinstance(n, int) and 2 <= n <= MAX_SWEEP_POINTS) or not hi > lo:
            raise ApiProblem(422, "give lo < hi and 2 <= n <= %d" % MAX_SWEEP_POINTS)
        values = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
    if not isinstance(values, list) or not (1 <= len(values) <= MAX_SWEEP_POINTS):
        raise ApiProblem(422, "values must be a list of 1 to %d entries" % MAX_SWEEP_POINTS)
    if op not in d.operating_points:
        raise ApiProblem(422, "unknown operating point %r; known: %s" % (op, sorted(d.operating_points)))
    rows = []
    for val in values:
        w = dict(v)
        w[variable] = val
        w = _clean_vector(w)
        viol = pd.check_candidate(d, w)
        if viol:
            rows.append({"value": val, "valid": False, "violations": viol})
            continue
        try:
            r = pd.evaluate_candidate(d, w, op)
        except pd.DesignError as exc:
            rows.append({"value": val, "valid": False, "violations": [{"rule": "evaluation", "name": variable, "value": str(exc), "limit": None}]})
            continue
        rows.append({"value": val, "valid": True, "objectives": r["objectives"], "constraints": r["constraints"], "constraints_met": r["constraints_met"],
                     "rpm": r["operating"]["rpm"], "thrust_N": r["thrust_N"], "torque_Nm": r["torque_Nm"], "shaft_power_W": r["shaft_power_W"],
                     "tip_mach": r["tip_mach"], "flags": r["flags"], "design_id": r["design_id"]})
    return {"variable": variable, "operating_point": op, "rows": rows, "banner": pd.BANNER}


def curve(vec: Any, op: str, n: int = 11, v_inf: float = 0.0) -> Dict[str, Any]:
    """Thrust, torque and power against rpm for the candidate (0.4 to 1.3 x the motor's stored rated rpm), with the motor torque limit."""
    from motor_ai_sim import propeller_inflow as pi
    d = _design()
    v = _clean_vector(vec)
    if op not in d.operating_points:
        raise ApiProblem(422, "unknown operating point %r; known: %s" % (op, sorted(d.operating_points)))
    if not (isinstance(n, int) and not isinstance(n, bool) and 3 <= n <= 21):
        raise ApiProblem(422, "n must be an integer between 3 and 21")
    if not (isinstance(v_inf, (int, float)) and math.isfinite(v_inf) and 0.0 <= v_inf <= 60.0):
        raise ApiProblem(422, "v_inf must be a number between 0 and 60 m/s")
    viol = pd.check_candidate(d, v)
    if viol:
        raise ApiProblem(422, {"message": "invalid candidate", "violations": viol})
    o = d.operating_points[op]
    prop = pd.build_propeller(d, v)
    air = d.raw["air"]
    rows = []
    for i in range(n):
        rpm = o.rated_rpm_ref * (0.4 + 0.9 * i / (n - 1))
        f = pi.get_model("bemt-1").evaluate(pi.InflowRequest(propeller=prop, rpm=rpm, rho=float(air["rho_kg_m3"]), v_inf_ms=float(v_inf),
                                                              r_min_m=0.0, r_max_m=o.motor_radius_mm / 1000.0, air_temp_c=float(air["temp_c"])))
        rows.append({"rpm": rpm, "thrust_N": f.thrust_N, "torque_Nm": f.torque_Nm, "shaft_power_W": f.shaft_power_W,
                     "air_speed_ms": f.air_speed_ms, "tip_mach": pd._tip_mach(prop.blade.tip_radius_m, rpm, float(air["temp_c"]))})
    return {"operating_point": op, "torque_limit_Nm": o.torque_limit_Nm, "rated_rpm_ref": o.rated_rpm_ref, "rows": rows,
            "tip_mach_limit": d.tip_mach_limit, "banner": pd.BANNER}


def optimize(vec: Any, op: str, pop: Any = 12, generations: Any = 6, seed: Any = 1, objectives: Any = None, free: Any = None,
             enforce_tip_mach: Any = True, tip_mach_limit: Any = None,
             structure_material: Any = None, min_safety_factor: Any = None) -> Dict[str, Any]:
    """Two-objective optimisation from the given design (diameter and blade count stay).  Synchronous and capped (pop x (generations + 1) <= 200);
    every evaluation is appended to ``<workspace>/propeller_designs/_runs/<run_id>.jsonl``."""
    from motor_ai_sim import propeller_optimize as po
    d = _design()
    v = _clean_vector(vec)
    if not isinstance(pop, int) or isinstance(pop, bool) or not isinstance(generations, int) or isinstance(generations, bool) \
            or not isinstance(seed, int) or isinstance(seed, bool):
        raise ApiProblem(422, "pop, generations and seed must be integers")
    if objectives is not None and not (isinstance(objectives, list) and all(isinstance(o, str) for o in objectives)):
        raise ApiProblem(422, "objectives must be a list of names")
    if free is not None and not (isinstance(free, list) and all(isinstance(g, str) for g in free)):
        raise ApiProblem(422, "free must be a list of group names")
    if not isinstance(enforce_tip_mach, bool):
        raise ApiProblem(422, "enforce_tip_mach must be true or false")
    if tip_mach_limit is not None and (isinstance(tip_mach_limit, bool) or not isinstance(tip_mach_limit, (int, float))):
        raise ApiProblem(422, "tip_mach_limit must be a number")
    if structure_material is not None and not isinstance(structure_material, str):
        raise ApiProblem(422, "structure_material must be a material name")
    if min_safety_factor is not None and (isinstance(min_safety_factor, bool) or not isinstance(min_safety_factor, (int, float))):
        raise ApiProblem(422, "min_safety_factor must be a number")
    kw: Dict[str, Any] = {"pop": pop, "generations": generations, "seed": seed, "enforce_tip_mach": enforce_tip_mach,
                          "tip_mach_limit": tip_mach_limit, "store_dir": _draft_dir() / "_runs"}
    if objectives is not None:
        kw["objectives"] = objectives
    if free is not None:
        kw["free"] = free
    if structure_material is not None or min_safety_factor is not None:
        kw.update(structure_material=structure_material, min_safety_factor=min_safety_factor)
    try:
        return po.optimize(d, v, op, **kw)
    except po.OptimizeError as exc:
        raise ApiProblem(422, str(exc))
    except pd.DesignError as exc:
        raise ApiProblem(422, str(exc))


# ---------------------------------------------------------------------------
# Drafts: the caller's own workspace
# ---------------------------------------------------------------------------

def _draft_dir() -> Path:
    return Path(workspace.root()) / DRAFT_DIR


def _draft_path(name: str) -> Path:
    if not isinstance(name, str) or not NAME_RX.match(name) or name.endswith("."):
        raise ApiProblem(422, "draft name: 1-60 letters, digits, '.', '_' or '-', starting with a letter or digit")
    return _draft_dir() / (name + ".json")


def list_drafts() -> Dict[str, Any]:
    out = []
    dd = _draft_dir()
    if dd.is_dir():
        for p in sorted(dd.glob("*.json")):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                out.append({"name": p.stem, "saved_at": j.get("saved_at"), "operating_point": j.get("operating_point"),
                            "diameter_in": (j.get("vector") or {}).get("diameter_in"), "blades": (j.get("vector") or {}).get("blades")})
            except (OSError, ValueError):
                continue
    return {"drafts": out}


def get_draft(name: str) -> Dict[str, Any]:
    p = _draft_path(name)
    if not p.is_file():
        raise ApiProblem(404, "no draft named %r" % name)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApiProblem(500, "draft %r is unreadable" % name)


def put_draft(name: str, vec: Any, op: str = "L12") -> Dict[str, Any]:
    """Store a draft.  Incomplete or out-of-bounds vectors ARE stored (a draft is work in progress); the answer lists the violations."""
    d = _design()
    v = _clean_vector(vec)
    if op not in d.operating_points:
        raise ApiProblem(422, "unknown operating point %r; known: %s" % (op, sorted(d.operating_points)))
    p = _draft_path(name)
    existing = list(_draft_dir().glob("*.json")) if _draft_dir().is_dir() else []
    if not p.exists() and len(existing) >= MAX_DRAFTS:
        raise ApiProblem(409, "at most %d drafts; delete one first" % MAX_DRAFTS)
    rec = {"schema": DRAFT_SCHEMA, "design_family": d.id, "name": name, "operating_point": op, "vector": v,
           "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    viol = pd.check_candidate(d, v)
    return {"saved": True, "name": name, "valid": not viol, "violations": viol}


def delete_draft(name: str) -> Dict[str, Any]:
    p = _draft_path(name)
    if not p.is_file():
        raise ApiProblem(404, "no draft named %r" % name)
    p.unlink()
    return {"deleted": True, "name": name}
