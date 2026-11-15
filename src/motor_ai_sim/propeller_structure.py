"""Structural root check of the own propeller — plan section 9.5 ("Structural hook") of docs/PROPELLER_MODULE_PLAN_2026-11-15.md.

A beam model of ONE blade: centrifugal tension at the root from the mass distribution, bending at the root from the blade-element loads
(thrust = flapwise, torque force = edgewise) resolved on the section's principal axes through the twist, combined stress at the root
section, safety factor against the material allowables, ``passes = factor >= 3`` (owner rule: mechanics are always checked, SF 3).

What this is NOT: no FEM, no torsion in the stress, no centrifugal bending from a centroid line that is not straight, no stress
concentration at the hub fillet, no fatigue, no moulded-fibre orientation.  Stresses are taken at the bounding-box extremes of the
section and the two bending directions are added in absolute value, so the number errs on the safe side.  The material table is
PLACEHOLDER data and the result says so; the check is a screen for the optimiser's hard constraint, never a release.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af
from motor_ai_sim import propeller_bemt as pb

SAFETY_FACTOR = 3.0                       # owner rule: mechanics always checked, SF 3
OVERSPEED_FACTOR = 1.2                    # assumption: runaway / overspeed rpm = 1.2 x the design rpm when the caller gives none
PLACEHOLDER = "placeholder - supplier data sheet / factory question"
BASIS = "beam model, placeholder material, root flow not validated"
THIN_ELLIPSE_CENTROID = 0.40              # thin-section fallback: centroid at 40 % chord from the leading edge

# Direction-dependent allowables: the value stored is the one used (the weakest direction that the check can load); the note says what
# was assumed.  Moulded filled nylon is anisotropic along the flow; the fibre orientation in the root is a factory/material question.
MATERIALS: Dict[str, Dict[str, Any]] = {
    "pa6_gf30": {
        "name": "glass-filled nylon (PA6 GF30, injection moulded)", "density_kg_m3": 1360.0,
        "tensile_allowable_Pa": 40.0e6, "bending_allowable_Pa": 55.0e6,
        "direction_note": "allowables are across-flow (weak) values after a placeholder derating for moisture, temperature and weld lines; "
                          "along-flow values are higher",
        "source": PLACEHOLDER},
    "pa6_cf30": {
        "name": "carbon-fibre-filled nylon (PA6 CF30, injection moulded)", "density_kg_m3": 1250.0,
        "tensile_allowable_Pa": 60.0e6, "bending_allowable_Pa": 80.0e6,
        "direction_note": "allowables are across-flow (weak) values after a placeholder derating; along-flow values are higher",
        "source": PLACEHOLDER},
    "cfrp_laminate": {
        "name": "CFRP laminate (compression-moulded prepreg)", "density_kg_m3": 1550.0,
        "tensile_allowable_Pa": 300.0e6, "bending_allowable_Pa": 350.0e6,
        "direction_note": "allowables are along-fibre values of a placeholder quasi-isotropic layup after derating; "
                          "transverse and interlaminar allowables are far lower and are NOT checked here",
        "source": PLACEHOLDER},
}


class StructureError(pp.PropellerError, ValueError):
    pass


def get_material(material: Any) -> Dict[str, Any]:
    """A material id from the table, or a dict with the same keys (the caller's own data)."""
    if isinstance(material, str):
        if material not in MATERIALS:
            raise StructureError("unknown material %r; known: %s" % (material, sorted(MATERIALS)))
        return MATERIALS[material]
    if isinstance(material, dict):
        for k in ("density_kg_m3", "tensile_allowable_Pa", "bending_allowable_Pa", "source"):
            if k not in material:
                raise StructureError("material dict is missing %s" % k)
        for k in ("density_kg_m3", "tensile_allowable_Pa", "bending_allowable_Pa"):
            v = material[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (math.isfinite(v) and v > 0):
                raise StructureError("material %s must be a positive number" % k)
        return material
    raise StructureError("material must be an id or a dict")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _outline_props(x: np.ndarray, yu: np.ndarray, yl: np.ndarray, t_scale: float) -> Dict[str, float]:
    """Unit-chord area, centroid and second moments of the closed outline (shoelace), y scaled by ``t_scale``."""
    px = np.concatenate([x, x[::-1][1:]])
    py = np.concatenate([yu, yl[::-1][1:]]) * t_scale
    x0, y0 = px, py
    x1, y1 = np.roll(px, -1), np.roll(py, -1)
    cr = x0 * y1 - x1 * y0
    a2 = float(cr.sum())
    sgn = 1.0 if a2 >= 0 else -1.0
    A = abs(a2) / 2.0
    if A <= 0:
        raise StructureError("airfoil outline has no area")
    cx = float(((x0 + x1) * cr).sum() / (3.0 * a2))
    cy = float(((y0 + y1) * cr).sum() / (3.0 * a2))
    Ixx = float(((y0 ** 2 + y0 * y1 + y1 ** 2) * cr).sum() / 12.0) * sgn - A * cy ** 2     # about the chord-parallel centroid axis
    Iyy = float(((x0 ** 2 + x0 * x1 + x1 ** 2) * cr).sum() / 12.0) * sgn - A * cx ** 2     # about the normal centroid axis
    return {"area": A, "xc": cx, "yc": cy, "i_flap": abs(Ixx), "i_edge": abs(Iyy),
            "n_max": float(np.max(np.abs(py - cy))), "c_max": float(np.max(np.abs(px - cx)))}


def _thin_ellipse(t_c: float) -> Dict[str, float]:
    """Documented fallback when no coordinates exist: an ellipse of unit chord and thickness ``t_c`` (axes c/2 and t/2)."""
    a, b = 0.5, 0.5 * t_c
    return {"area": math.pi * a * b, "xc": THIN_ELLIPSE_CENTROID, "yc": 0.0, "i_flap": math.pi * a * b ** 3 / 4.0,
            "i_edge": math.pi * a ** 3 * b / 4.0, "n_max": b, "c_max": max(THIN_ELLIPSE_CENTROID, 1.0 - THIN_ELLIPSE_CENTROID)}


def blade_sections(propeller2: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per station of a ``propeller-2`` dict: area, centroid and second moments.

    The airfoil coordinates come from ``propeller_airfoil`` (formula ids ``naca4-XXXX`` or the library); the y values are scaled to the
    station's ``thickness_rel`` (the airfoil as manufactured).  When the coordinates are not available, a thin ellipse of the station's
    thickness ratio is used and ``method`` says ``thin_ellipse_approximation``.
    Keys: r_m, chord_m, twist_deg, thickness_rel, area_m2, centroid_from_le_m, i_flap_m4 (thickness direction, about the chord-parallel
    centroid axis), i_edge_m4 (chordwise direction), n_max_m, c_max_m (extreme fibre distances), method, airfoil."""
    blade = (propeller2 or {}).get("blade")
    if not isinstance(blade, dict) or not blade.get("stations"):
        raise StructureError("propeller-2 has no blade.stations")
    out: List[Dict[str, Any]] = []
    for s in blade["stations"]:
        chord = float(s["chord_mm"]) / 1000.0
        r = float(s["r_mm"]) / 1000.0
        aid = s.get("airfoil")
        entry = None
        try:
            entry = af.generated(aid) if str(aid).startswith("naca4-") else af.get_airfoil(aid)
        except pp.PropellerError:
            entry = None
        tc_s = s.get("thickness_rel")
        if entry is not None:
            base_tc = af.section_props(entry.x, entry.yu, entry.yl).t_c
            scale = (float(tc_s) / base_tc) if tc_s and base_tc > 0 else 1.0
            u = _outline_props(entry.x, entry.yu, entry.yl, scale)
            method = "airfoil_outline:%s" % aid
            tc = base_tc * scale
        elif tc_s:
            u = _thin_ellipse(float(tc_s))
            method = "thin_ellipse_approximation"
            tc = float(tc_s)
        else:
            raise StructureError("station r=%g mm: no airfoil coordinates and no thickness_rel" % (r * 1000.0))
        out.append({"r_m": r, "chord_m": chord, "twist_deg": float(s["twist_deg"]), "thickness_rel": tc, "airfoil": aid,
                    "area_m2": u["area"] * chord ** 2, "centroid_from_le_m": u["xc"] * chord,
                    "i_flap_m4": u["i_flap"] * chord ** 4, "i_edge_m4": u["i_edge"] * chord ** 4,
                    "n_max_m": u["n_max"] * chord, "c_max_m": u["c_max"] * chord, "method": method})
    return out


# ---------------------------------------------------------------------------
# Loads
# ---------------------------------------------------------------------------

def bemt_blade_loads(propeller2: Dict[str, Any], rpm: float, rho: float = pp.RHO_ISA, v_inf: float = 0.0, temp_c: float = 15.0) -> Dict[str, Any]:
    """Per-BLADE spanwise loads from the blade-element model: ``r_m``, ``dT_dr_N_per_m``, ``dQ_dr_Nm_per_m`` (the BEMT arrays are for
    all blades together, divided here by the blade count)."""
    prop = pp._build(propeller2, "structure:" + str(propeller2.get("id")))
    res = pb.solve(prop.blade, prop.blades, rpm, rho, v_inf, polar=pb.polar_for(prop), temp_c=temp_c)
    B = float(prop.blades)
    return {"r_m": res.r_m.tolist(), "dT_dr_N_per_m": (res.dT_dr / B).tolist(), "dQ_dr_Nm_per_m": (res.dQ_dr / B).tolist(),
            "rpm": float(rpm), "flags": list(res.flags)}


# ---------------------------------------------------------------------------
# Beam model
# ---------------------------------------------------------------------------

def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    return float(np.sum(0.5 * (y[1:] + y[:-1]) * np.diff(x))) if len(x) > 1 else 0.0


def root_stress_from_sections(sections: Sequence[Dict[str, Any]], rpm: float, material: Any, loads: Dict[str, Any], *,
                              rpm_design: Optional[float] = None, rpm_max_assumed: bool = False, n_int: int = 400) -> Dict[str, Any]:
    """The beam model on already-computed sections (see ``blade_sections``).  ``rpm`` is the rpm of the check (the maximum rpm)."""
    mat = get_material(material)
    if not (isinstance(rpm, (int, float)) and rpm > 0 and math.isfinite(rpm)):
        raise StructureError("rpm must be positive")
    if len(sections) < 2:
        raise StructureError("need at least two sections")
    sec = sorted(sections, key=lambda s: s["r_m"])
    r_s = np.array([s["r_m"] for s in sec])
    root = sec[0]
    r0, R = float(r_s[0]), float(r_s[-1])
    if not R > r0:
        raise StructureError("section radii must increase")
    om = rpm * 2.0 * math.pi / 60.0
    rr = np.linspace(r0, R, n_int)
    area = np.interp(rr, r_s, [s["area_m2"] for s in sec])
    rho = float(mat["density_kg_m3"])
    n_cf = rho * om ** 2 * _trapz(area * rr, rr)                       # centrifugal tension at the root, N
    mass = rho * _trapz(area, rr)

    lr = np.asarray(loads["r_m"], dtype=float)
    dT = np.asarray(loads["dT_dr_N_per_m"], dtype=float)
    dQ = np.asarray(loads["dQ_dr_Nm_per_m"], dtype=float)
    if not (len(lr) == len(dT) == len(dQ) and len(lr) >= 2):
        raise StructureError("loads: r_m, dT_dr_N_per_m, dQ_dr_Nm_per_m must be arrays of the same length >= 2")
    m = lr >= r0 - 1e-12
    lr, dT, dQ = lr[m], dT[m], dQ[m]
    if len(lr) < 2:
        raise StructureError("loads: fewer than two load points outboard of the root section")
    Ft = dQ / lr                                                         # tangential force per unit span, N/m
    arm = lr - r0
    thrust = _trapz(dT, lr)
    m_flap = _trapz(dT * arm, lr)                                        # moment of the axial load about the root, Nm
    m_edge = _trapz(Ft * arm, lr)                                        # moment of the tangential load about the root, Nm
    th = math.radians(root["twist_deg"])
    # principal axes are the chord line and its normal; the twist turns them against the plane of rotation.  The two terms are added in
    # absolute value (worst combination of signs): safe side.
    m_c = abs(m_flap * math.cos(th)) + abs(m_edge * math.sin(th))       # about the chord-parallel axis, uses i_flap and n_max
    m_n = abs(m_edge * math.cos(th)) + abs(m_flap * math.sin(th))       # about the normal axis, uses i_edge and c_max
    A0 = root["area_m2"]
    s_axial = n_cf / A0
    s_bend = m_c * root["n_max_m"] / root["i_flap_m4"] + m_n * root["c_max_m"] / root["i_edge_m4"]
    s_comb = s_axial + s_bend
    util = s_axial / mat["tensile_allowable_Pa"] + s_bend / mat["bending_allowable_Pa"]      # linear interaction
    factor = (1.0 / util) if util > 0 else math.inf
    allow_eq = (s_comb * factor) if util > 0 else None
    methods = sorted({s["method"] for s in sec})
    return {
        "rpm_check": float(rpm), "rpm_design": rpm_design, "rpm_max_assumed": bool(rpm_max_assumed),
        "overspeed_assumption": ("rpm_max = %g x the design rpm (assumption, not a confirmed runaway speed)" % OVERSPEED_FACTOR) if rpm_max_assumed else None,
        "stress_Pa": {"centrifugal_axial": s_axial, "bending": s_bend, "combined": s_comb},
        "allowable_Pa": {"tensile": mat["tensile_allowable_Pa"], "bending": mat["bending_allowable_Pa"], "combined_equivalent": allow_eq},
        "utilisation": util, "safety_factor": factor, "required_factor": SAFETY_FACTOR, "passes": bool(factor >= SAFETY_FACTOR),
        "forces": {"centrifugal_tension_N": n_cf, "blade_mass_kg": mass, "thrust_per_blade_N": thrust,
                   "root_moment_flap_Nm": m_flap, "root_moment_edge_Nm": m_edge},
        "section_used": {k: root[k] for k in ("r_m", "chord_m", "twist_deg", "thickness_rel", "area_m2", "centroid_from_le_m",
                                              "i_flap_m4", "i_edge_m4", "n_max_m", "c_max_m", "method")},
        "section_methods": methods,
        "material": {"name": mat.get("name"), "density_kg_m3": rho, "direction_note": mat.get("direction_note"), "source": mat["source"]},
        "validated": False, "basis": BASIS,
    }


def root_stress(propeller2: Dict[str, Any], rpm: float, material: Any, loads: Optional[Dict[str, Any]] = None, *,
                rpm_max: Optional[float] = None, rho_air: float = pp.RHO_ISA, v_inf: float = 0.0) -> Dict[str, Any]:
    """Root check of one blade of a ``propeller-2`` dict.

    ``rpm`` is the design rpm; the check runs at ``rpm_max`` (the runaway / overspeed rpm from the caller; default
    ``OVERSPEED_FACTOR`` x ``rpm``, labelled as an assumption).  ``loads`` = per-blade spanwise loads (``bemt_blade_loads`` layout); when
    absent they are computed with the blade-element model AT ``rpm_max`` (static thrust unless ``v_inf`` is given), which is the
    conservative direction.  The result carries ``passes`` (safety factor >= 3), the stresses and ``validated: false``."""
    if not (isinstance(rpm, (int, float)) and rpm > 0 and math.isfinite(rpm)):
        raise StructureError("rpm must be positive")
    assumed = rpm_max is None
    rmax = OVERSPEED_FACTOR * float(rpm) if assumed else float(rpm_max)
    if rmax < rpm:
        raise StructureError("rpm_max is below the design rpm")
    sections = blade_sections(propeller2)
    if loads is None:
        loads = bemt_blade_loads(propeller2, rmax, rho_air, v_inf)
    out = root_stress_from_sections(sections, rmax, material, loads, rpm_design=float(rpm), rpm_max_assumed=assumed)
    if loads.get("flags"):
        out["load_flags"] = list(loads["flags"])
    return out
