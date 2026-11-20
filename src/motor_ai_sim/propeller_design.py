"""Parametric model of the own propeller, 10 / 11 / 12 / 13 in — stage P1 of docs/PROPELLER_MODULE_PLAN_2026-11-15.md (sections 8-10).

A candidate is a flat dict of DESIGN VARIABLES (``chord_R@0.2`` ... , ``twist_deg@0.2`` ..., ``hub_radius_mm``, ``root_cutout_mm``,
``airfoil_root|mid|tip``, ``blades``, ``diameter_in``).  ``candidate_to_propeller2`` turns it into a valid ``propeller-2`` dictionary
(every candidate passes the real loader ``propeller._build``), with the airfoil AS MANUFACTURED (``propeller_airfoil``).  ``evaluate_candidate``
runs the blade-element model at the rpm where the propeller torque meets the motor's torque limit and returns both objectives:

1. thrust per shaft watt;
2. cooling air: mean axial speed at the disc in the radius band the motor occupies behind the hub (``None`` where the model has no value).

They are two numbers, never one weighted sum.  All the numbers come from ``config/propellers/_designs/own_family.yaml``; the bounds, the
tip-speed limit, the blade options and the manufacturing limits are editable and marked unconfirmed there.  Every result says
"BEMT, generic airfoil polars, not validated" and carries the flags of what the model cannot do at the root.
"""
from __future__ import annotations

import hashlib
import json
import math
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import yaml

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_bemt as pb
from motor_ai_sim import propeller_inflow as pi

DESIGN_PATH = pp.seeded_config_path("propellers", "_designs", "own_family.yaml")
N_STATIONS = 12
BANNER = "BEMT, generic airfoil polars, not validated"
F_UNCONFIRMED = "design_inputs_unconfirmed"
F_ROOT = "root_flow_not_validated"
F_HUB = "hub_shape_not_modelled"
F_AIRFOIL = "airfoil_choice_not_resolved_by_polar"
F_AIRFOIL_DRAG = "airfoil_drag_not_resolved_by_polar"     # camber_model: the zero-lift angle follows the airfoil, drag and stall do not
F_POLAR_MFOIL = "polar_viscous_model_mfoil"              # mfoil_table: viscous section model (free transition), not a measurement
POLAR_CHOICES = ("flatplate_model", "camber_model", "mfoil_table")
_POLAR_FLAG = {"flatplate_model": F_AIRFOIL, "camber_model": F_AIRFOIL_DRAG, "mfoil_table": F_POLAR_MFOIL}
FLIGHT_RPM_TOL = 0.05                                    # flight mode: the torque limit must be reached within +-5 % of the rated rpm
IN = 0.0254


class DesignError(pp.PropellerError, ValueError):
    pass


@dataclass(frozen=True)
class OperatingPoint:
    name: str
    die: str
    config: str
    torque_limit_Nm: float
    rated_rpm_ref: float
    rpm_search_max: float
    motor_radius_mm: float
    source: str
    flight_speed_ms: Optional[float] = None               # forward-flight design speed (owner 2026-11-19); None = no flight point


@dataclass(frozen=True)
class Design:
    raw: Dict[str, Any]
    id: str
    diameters_in: Tuple[int, ...]
    control: Tuple[float, ...]
    operating_points: Dict[str, OperatingPoint]
    manufacturing: af.ManufacturingProfile

    @property
    def blades_options(self) -> List[int]:
        return [int(b) for b in self.raw["blades"]["options"]]

    @property
    def tip_mach_limit(self) -> float:
        return float(self.raw["tip_mach_limit"]["value"])

    def inputs_unconfirmed(self) -> bool:
        r = self.raw
        return not all(bool(r[k].get("confirmed_by")) for k in ("blades", "bounds", "tip_mach_limit"))


def polar_source(d: "Design") -> str:
    """The section polar of the family: ``polars_source`` in the design file, default ``flatplate_model`` (the stored behaviour)."""
    return str(d.raw.get("polars_source") or "flatplate_model")


def with_polar_source(d: "Design", source: Optional[str]) -> "Design":
    """A copy of the design using ``source`` (None = unchanged); refused by name when not offered."""
    if source is None:
        return d
    if source not in POLAR_CHOICES:
        raise DesignError("polar_source must be one of %s, got %r" % (list(POLAR_CHOICES), source))
    return dataclasses.replace(d, raw=dict(d.raw, polars_source=source))


def with_compressibility(d: "Design", model: Optional[str]) -> "Design":
    """A copy of the design whose candidates use a compressibility correction of the section lift (None = unchanged)."""
    if model is None:
        return d
    if model != "prandtl_glauert":
        raise DesignError("compressibility must be 'prandtl_glauert', got %r" % (model,))
    return dataclasses.replace(d, raw=dict(d.raw, polars_compressibility=model))


def chord_shape_violation(d: "Design", vec: Dict[str, Any], max_step: float = 0.08) -> float:
    """0 for a makeable chord shape, else how far it is from one (opt-in optimiser rule, 2026-11-20): chord/R rises to ONE maximum
    and then falls to the tip (no second bulge), and neighbouring control points differ by at most ``max_step`` (chord/R)."""
    c = [float(vec[_cname("chord_R", r)]) for r in d.control]
    k = int(np.argmax(c))
    v = sum(max(0.0, c[i] - c[i + 1]) for i in range(k)) + sum(max(0.0, c[i + 1] - c[i]) for i in range(k, len(c) - 1))
    v += sum(max(0.0, abs(c[i + 1] - c[i]) - max_step) for i in range(len(c) - 1))
    return v / max_step


def load_design(path: Path = DESIGN_PATH) -> Design:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        raise DesignError("%s: cannot read the design file: %s" % (path, str(exc).splitlines()[0] if str(exc) else type(exc).__name__))
    if not isinstance(raw, dict) or raw.get("schema") != "propeller-design-1":
        raise DesignError("%s: not a propeller-design-1 file" % path)
    ops = {}
    for name, o in (raw.get("operating_points") or {}).items():
        ops[name] = OperatingPoint(name, o["die"], o["config"], float(o["torque_limit_Nm"]), float(o["rated_rpm_ref"]),
                                   float(o["rpm_search_max"]), float(o["motor_radius_mm"]), str(o["source"]),
                                   float(o["flight_speed_ms"]) if o.get("flight_speed_ms") is not None else None)
    return Design(raw, str(raw["id"]), tuple(int(d) for d in raw["diameters_in"]),
                  tuple(float(c) for c in raw["control_r_over_R"]), ops, af.parse_manufacturing(raw.get("manufacturing")))


def _cname(kind: str, r: float) -> str:
    return "%s@%g" % (kind, r)


def variable_names(d: Design) -> List[str]:
    return ([_cname("chord_R", r) for r in d.control] + [_cname("twist_deg", r) for r in d.control]
            + ["hub_radius_mm", "root_cutout_mm", "airfoil_root", "airfoil_mid", "airfoil_tip", "blades", "diameter_in"])


def seed_vector(d: Design, diameter_in: int = 10, blades: int = 2) -> Dict[str, Any]:
    s = d.raw["seed"]
    v: Dict[str, Any] = {}
    for r, c in zip(d.control, s["chord_over_R"]):
        v[_cname("chord_R", r)] = float(c)
        v[_cname("twist_deg", r)] = math.degrees(math.atan((float(s["pitch_ratio"])) / (math.pi * r)))
    v.update(hub_radius_mm=float(s["hub_radius_mm"]), root_cutout_mm=float(s["root_cutout_mm"]),
             airfoil_root=s["airfoil"]["root"], airfoil_mid=s["airfoil"]["mid"], airfoil_tip=s["airfoil"]["tip"],
             blades=int(blades), diameter_in=int(diameter_in))
    return v


def variables_table(d: Design, vec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rows for the GUI: name, value, lo, hi, unit, group (inner / outer / hub / airfoil / topology), basis, confirmed."""
    b = d.raw["bounds"]
    inner_max = float(d.raw["inner_group_max_r_over_R"])
    conf = bool(b.get("confirmed_by"))
    rows: List[Dict[str, Any]] = []
    for r in d.control:
        grp = "inner" if r <= inner_max else "outer"
        rows.append(dict(name=_cname("chord_R", r), value=vec[_cname("chord_R", r)], lo=b["chord_over_R"][0], hi=b["chord_over_R"][1],
                         unit="c/R", group=grp, basis=b["basis"], confirmed=conf))
        rows.append(dict(name=_cname("twist_deg", r), value=vec[_cname("twist_deg", r)], lo=b["twist_deg"][0], hi=b["twist_deg"][1],
                         unit="deg", group=grp, basis=b["basis"], confirmed=conf))
    for n in ("hub_radius_mm", "root_cutout_mm"):
        rows.append(dict(name=n, value=vec[n], lo=b[n][0], hi=b[n][1], unit="mm", group="inner", basis=b["basis"], confirmed=conf))
    for z in ("root", "mid", "tip"):
        rows.append(dict(name="airfoil_" + z, value=vec["airfoil_" + z], options=list(d.raw["airfoil_options"][z]), unit="-",
                         group="airfoil", basis="generic generated sections", confirmed=False))
    rows.append(dict(name="blades", value=vec["blades"], options=d.blades_options, unit="-", group="topology",
                     basis="owner 2026-11-15: compare 2 and 3", confirmed=bool(d.raw["blades"].get("confirmed_by"))))
    rows.append(dict(name="diameter_in", value=vec["diameter_in"], options=list(d.diameters_in), unit="in", group="topology",
                     basis="family 10 / 11 / 12 / 13 in", confirmed=True))
    return rows


# ---------------------------------------------------------------------------
# Candidate -> propeller-2
# ---------------------------------------------------------------------------

def _zone_of(d: Design, r_over_R: float) -> str:
    for z, (a, b) in d.raw["zones"].items():
        if a <= r_over_R < b:
            return z
    return "tip"


def _stations(d: Design, vec: Dict[str, Any]):
    R_mm = float(vec["diameter_in"]) * 25.4 / 2.0
    rc = max(float(vec["root_cutout_mm"]), float(vec["hub_radius_mm"]))
    from scipy.interpolate import PchipInterpolator
    x = np.array(d.control)
    chord = PchipInterpolator(x, [vec[_cname("chord_R", r)] for r in d.control])
    twist = PchipInterpolator(x, [vec[_cname("twist_deg", r)] for r in d.control])
    r_mm = np.linspace(rc, R_mm, N_STATIONS)
    fr = np.clip(r_mm / R_mm, x[0], x[-1])                    # held constant inside the first control point
    return R_mm, r_mm, chord(fr) * R_mm, twist(fr), r_mm / R_mm


def candidate_to_propeller2(d: Design, vec: Dict[str, Any]) -> Dict[str, Any]:
    """The candidate as a ``propeller-2`` dictionary, airfoil as manufactured.  Raises ``DesignError`` for a vector that cannot be built."""
    for n in variable_names(d):
        if n not in vec:
            raise DesignError("missing design variable %s" % n)
    if int(vec["diameter_in"]) not in d.diameters_in:
        raise DesignError("diameter_in must be one of %s" % list(d.diameters_in))
    R_mm, r_mm, chord_mm, twist_deg, rR = _stations(d, vec)
    st = []
    for i in range(N_STATIONS):
        zone = _zone_of(d, float(rR[i]))
        aid = vec["airfoil_" + zone]
        try:
            e = af.generated(aid) if str(aid).startswith("naca4-") else af.get_airfoil(aid)
        except pp.PropellerDataError as exc:
            raise DesignError(str(exc))
        ms = af.manufactured_section(e.x, e.yu, e.yl, float(chord_mm[i]), d.manufacturing)
        st.append({"r_mm": round(float(r_mm[i]), 4), "chord_mm": round(float(chord_mm[i]), 4), "twist_deg": round(float(twist_deg[i]), 4),
                   "airfoil": aid, "thickness_rel": math.ceil(float(ms.made.t_c) * 1e6) / 1e6})        # rounded UP: never below a minimum
    D_mm = 2.0 * R_mm
    ident = {k: vec[k] for k in sorted(vec)}
    h = hashlib.sha1(json.dumps(ident, sort_keys=True, default=str).encode()).hexdigest()[:8]
    man = d.raw.get("manufacturing") or {}
    blade = {"geometry_source": "inferred", "source_ref": "parametric family %s (propeller_design.py)" % d.id, "estimated": True,
             "method": "parametric blade from design variables; generic generated airfoils; not a measured geometry",
             "hub_radius_mm": float(vec["hub_radius_mm"]), "root_cutout_mm": max(float(vec["root_cutout_mm"]), float(vec["hub_radius_mm"])),
             "interpolation": "pchip", "pitch_axis": 0.25, "stations": st, "polars": dict({"source": polar_source(d)}, **({"compressibility": d.raw["polars_compressibility"]}
                                                                                         if d.raw.get("polars_compressibility") else {})),
             "manufacturing": man}
    return {"schema": "propeller-2", "id": "own_%din_%db_%s" % (int(vec["diameter_in"]), int(vec["blades"]), h),
            "vendor": "MOTRES", "model": "%d in %d-blade design %s" % (int(vec["diameter_in"]), int(vec["blades"]), h), "status": "design",
            "geometry": {"diameter_in": float(vec["diameter_in"]), "diameter_mm": D_mm, "blades": int(vec["blades"])},
            "performance": {"data_quality": "geometry_only"}, "blade": blade}


def build_propeller(d: Design, vec: Dict[str, Any]) -> pp.Propeller:
    raw = candidate_to_propeller2(d, vec)
    try:
        return pp._build(raw, "design:" + raw["id"])
    except pp.PropellerDataError as exc:
        raise DesignError("candidate is not a valid propeller-2: %s" % exc)


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def check_candidate(d: Design, vec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Violations of the candidate's own rules (bounds, ordering, manufacturing, loader).  Empty = valid.  Each: ``{rule, name, value, limit}``."""
    out: List[Dict[str, Any]] = []
    b = d.raw["bounds"]

    def viol(rule, name, value, limit):
        out.append({"rule": rule, "name": name, "value": value, "limit": limit})

    for n in variable_names(d):
        if n not in vec:
            viol("missing", n, None, None)
    if out:
        return out
    if int(vec["diameter_in"]) not in d.diameters_in:
        viol("diameter_not_in_family", "diameter_in", vec["diameter_in"], list(d.diameters_in))
    if int(vec["blades"]) not in d.blades_options:
        viol("blades_not_offered", "blades", vec["blades"], d.blades_options)
    for r in d.control:
        for kind, key in (("chord_R", "chord_over_R"), ("twist_deg", "twist_deg")):
            n = _cname(kind, r)
            lo, hi = b[key]
            if not (isinstance(vec[n], (int, float)) and math.isfinite(vec[n]) and lo <= vec[n] <= hi):
                viol("out_of_bounds", n, vec[n], [lo, hi])
    for n in ("hub_radius_mm", "root_cutout_mm"):
        lo, hi = b[n]
        if not (isinstance(vec[n], (int, float)) and math.isfinite(vec[n]) and lo <= vec[n] <= hi):
            viol("out_of_bounds", n, vec[n], [lo, hi])
    tw = [vec[_cname("twist_deg", r)] for r in d.control]
    if any(tw[i + 1] > tw[i] + 1e-9 for i in range(len(tw) - 1)):
        viol("twist_must_not_increase_outward", "twist_deg", tw, None)
    if isinstance(vec["root_cutout_mm"], (int, float)) and isinstance(vec["hub_radius_mm"], (int, float)):
        R_mm = float(vec["diameter_in"]) * 25.4 / 2.0
        if vec["root_cutout_mm"] < vec["hub_radius_mm"] - 1e-9:
            viol("root_cutout_inside_hub", "root_cutout_mm", vec["root_cutout_mm"], vec["hub_radius_mm"])
        if vec["root_cutout_mm"] > float(d.raw["inner_group_max_r_over_R"]) * R_mm:
            viol("root_cutout_beyond_inner_zone", "root_cutout_mm", vec["root_cutout_mm"], float(d.raw["inner_group_max_r_over_R"]) * R_mm)
    for z in ("root", "mid", "tip"):
        if vec["airfoil_" + z] not in d.raw["airfoil_options"][z]:
            viol("airfoil_not_offered", "airfoil_" + z, vec["airfoil_" + z], d.raw["airfoil_options"][z])
    if out:
        return out
    try:
        prop = build_propeller(d, vec)
    except DesignError as exc:
        return [{"rule": "invalid_propeller2", "name": None, "value": str(exc), "limit": None}]
    st = prop.blade.stations
    for v in af.check_blade([s.twist_deg for s in st], [s.chord_m * 1000.0 for s in st], [s.thickness_rel or 0.0 for s in st], d.manufacturing):
        out.append({"rule": "manufacturing:" + v["limit"], "name": v["limit"], "value": v["value"], "limit": v["limit_value"]})
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _tip_mach(R_m: float, rpm: float, temp_c: float) -> float:
    return rpm * 2.0 * math.pi / 60.0 * R_m / pb.sound_speed(temp_c)


def evaluate_candidate(d: Design, vec: Dict[str, Any], op_name: str, *, v_inf: float = 0.0, max_iter: int = 8,
                       rtol: float = 2e-3, flight: bool = False) -> Dict[str, Any]:
    """Blade-element evaluation at the rpm where the propeller torque meets the motor's torque limit (search up to ``rpm_search_max``).

    Torque scales with rpm squared to first order, so the search is a few secant-like steps on ``n <- n sqrt(T_lim / T)``; if the cap is
    hit the result says ``rpm_capped``.  Raises ``DesignError`` for an invalid candidate (use ``check_candidate`` first for the reasons).

    ``flight=True`` (owner 2026-11-19: design for forward flight): the axial speed is the operating point's ``flight_speed_ms`` and the
    candidate must take the motor's rated torque AT its rated rpm (constraint ``rpm_matches_rated``, +-``FLIGHT_RPM_TOL``), i.e. the
    rated power; the objective ``flight_thrust_N`` is the thrust there.  Without it nothing changes."""
    if op_name not in d.operating_points:
        raise DesignError("unknown operating point %r; known: %s" % (op_name, sorted(d.operating_points)))
    op = d.operating_points[op_name]
    if flight:
        if op.flight_speed_ms is None:
            raise DesignError("operating point %r has no flight_speed_ms" % op_name)
        v_inf = op.flight_speed_ms
    bad = check_candidate(d, vec)
    if bad:
        raise DesignError("invalid candidate: %s" % "; ".join("%s %s" % (b["rule"], b["name"]) for b in bad))
    prop = build_propeller(d, vec)
    air = d.raw["air"]
    rho, temp = float(air["rho_kg_m3"]), float(air["temp_c"])
    flags = set()
    rpm = op.rated_rpm_ref
    res = None
    for _ in range(max_iter):
        res = pi.get_model("bemt-1").evaluate(pi.InflowRequest(
            propeller=prop, rpm=rpm, rho=rho, v_inf_ms=v_inf, r_min_m=0.0, r_max_m=op.motor_radius_mm / 1000.0, air_temp_c=temp))
        if res.torque_Nm <= 0:
            raise DesignError("the propeller absorbs no positive torque at %.0f rpm" % rpm)
        new = rpm * math.sqrt(op.torque_limit_Nm / res.torque_Nm)
        capped = new > op.rpm_search_max
        new = min(new, op.rpm_search_max)
        if abs(new - rpm) <= rtol * rpm:
            rpm = new
            break
        rpm = new
    res = pi.get_model("bemt-1").evaluate(pi.InflowRequest(
        propeller=prop, rpm=rpm, rho=rho, v_inf_ms=v_inf, r_min_m=0.0, r_max_m=op.motor_radius_mm / 1000.0, air_temp_c=temp))
    converged = abs(res.torque_Nm - op.torque_limit_Nm) <= 0.01 * op.torque_limit_Nm
    if capped and res.torque_Nm < op.torque_limit_Nm:
        flags.add("rpm_capped")                       # the torque limit is not reached below the search cap: slack, not a violation
    elif not converged:
        flags.add("rpm_search_not_converged")
    R_m = prop.blade.tip_radius_m
    mach = _tip_mach(R_m, rpm, temp)
    flags |= set(res.flags) | {F_ROOT, F_HUB, _POLAR_FLAG.get(polar_source(d), F_AIRFOIL_DRAG)}
    if d.inputs_unconfirmed():
        flags.add(F_UNCONFIRMED)
    flags |= set(d.manufacturing.flags())
    thrust_per_w = res.thrust_N / res.shaft_power_W if res.shaft_power_W > 0 else None
    constraints = {
        "torque_within_limit": res.torque_Nm <= op.torque_limit_Nm * 1.01,
        "tip_mach_within_limit": mach <= d.tip_mach_limit,
        "bemt_converged": not any(f.startswith("unconverged_elements") for f in flags),
    }
    rpm_error = rpm / op.rated_rpm_ref - 1.0
    if flight:
        constraints["rpm_matches_rated"] = abs(rpm_error) <= FLIGHT_RPM_TOL
    ident = candidate_to_propeller2(d, vec)["id"]
    rec = {
        "model_id": pb.MODEL_BEMT, "design_family": d.id, "design_id": ident, "operating_point": op.name,
        "operating": {"rpm": rpm, "rho_kg_m3": rho, "v_inf_ms": v_inf, "air_temp_c": temp, "torque_limit_Nm": op.torque_limit_Nm,
                      "rated_rpm_ref": op.rated_rpm_ref, "motor_radius_mm": op.motor_radius_mm},
        "thrust_N": res.thrust_N, "torque_Nm": res.torque_Nm, "shaft_power_W": res.shaft_power_W, "tip_mach": mach,
        "objectives": {"thrust_per_shaft_W": thrust_per_w, "cooling_air_axial_ms": res.air_speed_ms},
        "constraints": constraints, "constraints_met": all(constraints.values()),
        "profile": {"r_m": [b.r_min_m for b in res.bands], "axial_total_ms": [b.axial_ms for b in res.bands],
                    "swirl_ms": [b.swirl_ms for b in res.bands]},
        "flags": sorted(flags), "estimated": True, "validated_against": None, "banner": BANNER,
    }
    if flight:                                           # flight mode only: the stored static records stay as they were
        rec["objectives"]["flight_thrust_N"] = res.thrust_N
        rec["flight"] = {"v_ms": v_inf, "rpm_error_vs_rated": rpm_error,
                         "propulsive_efficiency": res.thrust_N * v_inf / res.shaft_power_W if res.shaft_power_W > 0 else None}
    return rec

