"""Propeller Mission: the best propeller (and motor turns / Kv) for a given flight - the model behind ``/api/propellers/mission``.

WHAT IT DOES
============
A SELECTION over a small, explicit, listed set of propellers (catalogue entries with usable data, and the own family on a grid
of diameter, pitch ratio, chord scale and blade count).  The motor is NOT optimised: it is the passport S1 model of one die /
configuration, re-wound by turns (``rewind``), the battery is fixed, the airframe and the flight are given.  For every candidate:

1. the static curves T(rpm), Q(rpm) of the propeller (own blades: blade-element model with the section-polar tables and the
   Prandtl-Glauert lift correction; catalogue: the published test fit), cut at the tip Mach limit;
2. the motor turns N (Kv) by the inner step ``choose_turns``: the LARGEST N whose peak-thrust point is still reachable at the
   end-of-discharge pack voltage under load, with a voltage headroom (default 15 %).  Torque and rpm do not depend on Kv, Kv
   only trades current against voltage (study 2026-11-26: range changed < 0.5 %), so there is no grid over Kv;
3. forward flight of the multirotor by the momentum / energy method (``rotor_flight``): the same for every candidate, so the
   ranking is the point; the absolute values are estimates (no edgewise blade-element solution, no flight test);
4. motor + controller at every point through the S1 model (current, losses, winding temperature, voltage at the pack minimum),
   battery power with the pack's internal resistance, usable energy -> range / endurance / top speed / hover g/W;
5. hard constraints (a failed row is never ranked above a passing one).

The S1 calls are the ones of ``propeller_s1`` (``_fixed_point`` with the pack minimum, ``thermal_at``); nothing here solves a
field and nothing writes anywhere.  The functions are pure: the model data, the wire stock and the propeller catalogue are read
through their existing loaders only.

Port of the reference study scripts of 2026-11-26 (cruise.py ``flight``, turns.py ``rewind`` / ``max_cont`` / ``peak``, r9.py
Kv calibration), written cleanly.  One difference is deliberate and listed: the controller loss of a PWM drive and the drive's
PWM extra loss are counted when the model carries them (the study counted motor losses only).
"""
from __future__ import annotations

import copy
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_s1 as s1

MODEL_VERSION = "propeller-mission-1"
G0 = 9.80665
KAPPA = 1.15                    # induced-power factor of the momentum / energy method (the study's value)
#: Forward flight: the same propeller at another shaft power turns at rpm ~ P^(1/3) (constant C_T / C_P, P ~ n^3; an estimate).
#: The 2026-11-24/26 study scripts meant this but executed P^(1/6) (sqrt applied twice); corrected here by US · propeller 2026-11-26.
RPM_POWER_EXP = 1.0 / 3.0
NOTE = ("Estimate: forward flight by momentum/energy method, no edgewise BEMT; numbers rank candidates, absolute values are "
        "not measured.")
OBJECTIVES = ("range_km", "endurance_min", "top_speed_ms", "hover_g_per_w")
LOW_STOCK_KG = 1.0
MODULATION = 0.95               # estimate models: share of the pack voltage the line-line peak may use (SVPWM limit is 1.0)

CHEMISTRIES: Dict[str, Dict[str, Any]] = {
    # r_cell_ohm_ah: internal resistance of one series position times the pack capacity [ohm * Ah]; the pack's R is
    # S * r_cell_ohm_ah / capacity_Ah (more parallel cells, less resistance).  Editable in the UI.
    "li-ion-21700": {"label": "Li-ion 21700", "v_nom": 3.6, "v_min": 2.8, "v_max": 4.2, "wh_per_kg": 250.0, "r_cell_ohm_ah": 0.015},
    "lipo": {"label": "LiPo", "v_nom": 3.7, "v_min": 3.5, "v_max": 4.2, "wh_per_kg": 160.0, "r_cell_ohm_ah": 0.004},
    "lihv": {"label": "LiHV", "v_nom": 3.85, "v_min": 3.5, "v_max": 4.35, "wh_per_kg": 170.0, "r_cell_ohm_ah": 0.004},
}


class MissionError(ValueError):
    """A bad input or an unusable model; the API turns it into a 422 with this message."""


class MissionCancelled(Exception):
    """The run was stopped (the queue's cancel).  Deliberately NOT a ``DesignError`` or a ``ValueError``: the optimiser treats those as "a bad
    design, go on", and the per-candidate guard of the run must not swallow a stop."""


def _chk(ms: Mapping[str, Any]) -> None:
    """Raise ``MissionCancelled`` when the run was asked to stop (called inside every loop of any length)."""
    fn = ms.get("cancel")
    if fn is not None and fn():
        raise MissionCancelled("stopped")


# ---------------------------------------------------------------------------
# Inputs: defaults and validation (SI inside; the UI shows km/h and inches)
# ---------------------------------------------------------------------------

DEFAULTS: Dict[str, Any] = {
    "motor": {"card_id": None, "die": None, "config": None, "rotors": 4, "drive": "sine", "board_cap_A": None},
    "kv": {"mode": "free", "turns0": None, "strip_thickness0_mm": None, "strip_width_mm": None, "headroom": 0.15,
           "turns_below": 3, "turns_above": 5, "coil_height_tol": 0.0, "calibrate_bench": True, "allow_unverified": False},
    "battery": {"chemistry": "li-ion-21700", "cells_s": 6, "capacity_ah": 16.0, "v_nom": None, "v_min": None, "v_max": None,
                "wh_per_kg": None, "r_cell_ohm_ah": None, "usable": 0.85, "mass_kg": None, "cont_current_A": None},
    "airframe": {"dry_mass_kg": 3.5, "payload_kg": 1.0, "drag_area_m2": 0.05, "max_diameter_in": 13.0, "min_diameter_in": 10.0,
                 "air_temp_c": 25.0, "altitude_m": 0.0, "avionics_W": 0.0},
    "flight": {"cruise_speed_ms": 70.0 / 3.6, "best_speed": False, "hover_share_pct": 0.0, "tw_cont": 1.5, "tw_peak": 2.0,
               "tip_mach": 0.7, "endurance_mode": "hover", "speed_max_ms": 50.0},
    "candidates": {"catalogue": True, "own": True, "pd_min": 0.25, "pd_max": 0.60, "pd_step": 0.05, "chords": [1.0, 1.15],
                   "blades": [2], "rpm_step": 500.0, "max_candidates": 600},
    "refine": {"enabled": True, "top_n": 1, "pop": 12, "generations": 6, "seed": 1, "free": ["outer", "inner", "airfoil"]},
    "objective": "range_km",
}

# (group, key): (kind, lo, hi); kind "f" float, "i" int, "b" bool, "s" str, "fs" list of floats, "is" list of ints
_SPEC: Dict[Tuple[str, str], Tuple[str, Any, Any]] = {
    ("motor", "card_id"): ("s?", None, None), ("motor", "die"): ("s?", None, None), ("motor", "config"): ("s?", None, None),
    ("refine", "enabled"): ("b", None, None), ("refine", "top_n"): ("i", 1, 3), ("refine", "pop"): ("i", 4, 20),
    ("refine", "generations"): ("i", 1, 10), ("refine", "seed"): ("i", 0, 2 ** 32 - 1), ("refine", "free"): ("ss", None, None),
    ("motor", "rotors"): ("i", 1, 12), ("motor", "board_cap_A"): ("f?", 1.0, 2000.0), ("motor", "drive"): ("s", None, None),
    ("kv", "mode"): ("s", None, None), ("kv", "turns0"): ("i?", 1, 60), ("kv", "strip_thickness0_mm"): ("f?", 0.05, 5.0),
    ("kv", "strip_width_mm"): ("f?", 0.5, 30.0), ("kv", "headroom"): ("f", 0.0, 0.5), ("kv", "turns_below"): ("i", 0, 10),
    ("kv", "turns_above"): ("i", 0, 10), ("kv", "coil_height_tol"): ("f", 0.0, 0.5), ("kv", "calibrate_bench"): ("b", None, None), ("kv", "allow_unverified"): ("b", None, None),
    ("battery", "cont_current_A"): ("f?", 1.0, 5000.0),
    ("battery", "chemistry"): ("s", None, None), ("battery", "cells_s"): ("i", 1, 30), ("battery", "capacity_ah"): ("f", 0.1, 500.0),
    ("battery", "v_nom"): ("f?", 2.0, 5.0), ("battery", "v_min"): ("f?", 2.0, 5.0), ("battery", "v_max"): ("f?", 2.0, 5.0),
    ("battery", "wh_per_kg"): ("f?", 20.0, 600.0), ("battery", "r_cell_ohm_ah"): ("f?", 0.0, 1.0),
    ("battery", "usable"): ("f", 0.3, 1.0), ("battery", "mass_kg"): ("f?", 0.01, 500.0),
    ("airframe", "dry_mass_kg"): ("f", 0.05, 500.0), ("airframe", "payload_kg"): ("f", 0.0, 500.0),
    ("airframe", "drag_area_m2"): ("f", 0.0, 5.0), ("airframe", "max_diameter_in"): ("f", 4.0, 30.0),
    ("airframe", "min_diameter_in"): ("f", 0.0, 30.0), ("airframe", "air_temp_c"): ("f", -40.0, 60.0),
    ("airframe", "altitude_m"): ("f", -500.0, 6000.0), ("airframe", "avionics_W"): ("f", 0.0, 2000.0),
    ("flight", "cruise_speed_ms"): ("f", 0.0, 80.0), ("flight", "best_speed"): ("b", None, None),
    ("flight", "hover_share_pct"): ("f", 0.0, 95.0), ("flight", "tw_cont"): ("f", 1.0, 10.0), ("flight", "tw_peak"): ("f", 1.0, 10.0),
    ("flight", "tip_mach"): ("f", 0.2, 1.0), ("flight", "endurance_mode"): ("s", None, None), ("flight", "speed_max_ms"): ("f", 5.0, 100.0),
    ("candidates", "catalogue"): ("b", None, None), ("candidates", "own"): ("b", None, None),
    ("candidates", "pd_min"): ("f", 0.1, 1.5), ("candidates", "pd_max"): ("f", 0.1, 1.5), ("candidates", "pd_step"): ("f", 0.01, 0.5),
    ("candidates", "chords"): ("fs", 0.5, 1.6), ("candidates", "blades"): ("is", 2, 3), ("candidates", "rpm_step"): ("f", 100.0, 2000.0),
    ("candidates", "max_candidates"): ("i", 1, 2000),
}


def _coerce(group: str, key: str, v: Any) -> Any:
    kind, lo, hi = _SPEC[(group, key)]
    name = "%s.%s" % (group, key)
    if v is None:
        if kind.endswith("?"):
            return None
        raise MissionError("%s is required" % name)
    base = kind.rstrip("?")
    if base == "b":
        if not isinstance(v, bool):
            raise MissionError("%s must be true or false" % name)
        return v
    if base == "s":
        if not isinstance(v, str) or not v or len(v) > 200:
            raise MissionError("%s must be a text" % name)
        return v
    if base == "ss":
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x in ("outer", "inner", "airfoil") for x in v):
            raise MissionError("%s must be a non-empty list of outer / inner / airfoil" % name)
        return list(v)
    if base in ("fs", "is"):
        if not isinstance(v, list) or not v or len(v) > 8:
            raise MissionError("%s must be a list of 1..8 numbers" % name)
        return [_num(name, x, "i" if base == "is" else "f", lo, hi) for x in v]
    return _num(name, v, base, lo, hi)


def _num(name: str, v: Any, base: str, lo: Any, hi: Any) -> Any:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MissionError("%s must be a number" % name)
    if base == "i":
        if float(v) != int(v):
            raise MissionError("%s must be a whole number" % name)
        v = int(v)
    else:
        v = float(v)
    if not (lo <= v <= hi):
        raise MissionError("%s must be between %g and %g" % (name, lo, hi))
    return v


def default_inputs() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def validate_inputs(body: Any) -> Dict[str, Any]:
    """The full input set: ``body`` over the defaults, every field checked.  ``MissionError`` names the first bad field.
    A group or key the model does not know is refused (a typo must not silently run on defaults)."""
    if not isinstance(body, Mapping):
        raise MissionError("the request body must be an object")
    cfg = default_inputs()
    for g, val in body.items():
        if g not in cfg:
            raise MissionError("unknown input %r" % (g,))
        if g == "objective":
            if val not in OBJECTIVES:
                raise MissionError("objective must be one of %s" % list(OBJECTIVES))
            cfg[g] = val
            continue
        if not isinstance(val, Mapping):
            raise MissionError("%s must be an object" % g)
        for k in val:
            if k not in cfg[g]:
                raise MissionError("unknown input %s.%s" % (g, k))
    for g in cfg:
        if g == "objective":
            continue
        sub = body.get(g) or {}
        for k in cfg[g]:
            cfg[g][k] = _coerce(g, k, sub[k] if k in sub else cfg[g][k])
    m = cfg["motor"]
    if not m["card_id"] and not (m["die"] and m["config"]):
        raise MissionError("choose a motor: motor.card_id (a catalogue motor) or motor.die and motor.config (a motor with an S1 model)")
    if cfg["kv"]["mode"] not in ("free", "as_built"):
        raise MissionError("kv.mode must be 'free' or 'as_built'")
    if cfg["flight"]["endurance_mode"] not in ("hover", "best_speed"):
        raise MissionError("flight.endurance_mode must be 'hover' or 'best_speed'")
    b = cfg["battery"]
    if b["chemistry"] not in CHEMISTRIES:
        raise MissionError("battery.chemistry must be one of %s" % sorted(CHEMISTRIES))
    for k, dv in CHEMISTRIES[b["chemistry"]].items():
        if k != "label" and b.get(k) is None:
            b[k] = dv
    if not (b["v_min"] < b["v_nom"] < b["v_max"]):
        raise MissionError("battery cell voltages must satisfy v_min < v_nom < v_max")
    a, c, f = cfg["airframe"], cfg["candidates"], cfg["flight"]
    if a["min_diameter_in"] > a["max_diameter_in"]:
        raise MissionError("airframe.min_diameter_in must not exceed airframe.max_diameter_in")
    if c["pd_min"] > c["pd_max"]:
        raise MissionError("candidates.pd_min must not exceed candidates.pd_max")
    if f["tw_peak"] < f["tw_cont"]:
        raise MissionError("flight.tw_peak must not be below flight.tw_cont")
    rf = cfg["refine"]
    if rf["pop"] * (rf["generations"] + 1) > 200:
        raise MissionError("refine: pop x (generations + 1) must not exceed 200")
    if not (c["catalogue"] or c["own"]):
        raise MissionError("candidates: choose the catalogue, the own family or both")
    return cfg


# ---------------------------------------------------------------------------
# Battery and mission numbers
# ---------------------------------------------------------------------------

def air_density(temp_c: float, altitude_m: float) -> float:
    try:
        return pp.air_density(temp_c=temp_c, altitude_m=altitude_m)
    except ValueError as exc:
        raise MissionError(str(exc))


def battery_numbers(bat: Mapping[str, Any]) -> Dict[str, Any]:
    """Pack energy, mass, resistance and the open-circuit voltages at the mean and at the end of discharge (linear OCV between the
    cell's max and min voltage over the state of charge; the mean is the nominal voltage the energy is counted with)."""
    s, ah = int(bat["cells_s"]), float(bat["capacity_ah"])
    e_nom = s * ah * float(bat["v_nom"])
    mass = float(bat["mass_kg"]) if bat.get("mass_kg") else e_nom / float(bat["wh_per_kg"])
    soc_end = 1.0 - float(bat["usable"])
    v_end = max(float(bat["v_min"]), float(bat["v_min"]) + (float(bat["v_max"]) - float(bat["v_min"])) * soc_end)
    return {"S": s, "Ah": ah, "E_nominal_Wh": e_nom, "E_usable_Wh": e_nom * float(bat["usable"]), "mass_kg": mass,
            "R_pack_ohm": s * float(bat["r_cell_ohm_ah"]) / ah, "V_mean_open": s * float(bat["v_nom"]), "V_end_open": s * v_end}


def pack_state(pk: Mapping[str, Any], p_elec_W: float, which: str) -> Dict[str, Any]:
    """The pack delivering ``p_elec_W`` at its terminals: V I = P with V = V_open - I R.  ``which`` = 'mean' | 'end'.
    ``P_draw_W`` is what leaves the cells (the terminal power plus I^2 R)."""
    v0 = float(pk["V_mean_open"] if which == "mean" else pk["V_end_open"])
    r = float(pk["R_pack_ohm"])
    if p_elec_W <= 0:
        return {"ok": True, "I_bus_A": 0.0, "V_V": v0, "P_draw_W": 0.0, "V_open": v0}
    if r <= 0:
        i = p_elec_W / v0
        return {"ok": True, "I_bus_A": i, "V_V": v0, "P_draw_W": p_elec_W, "V_open": v0}
    disc = v0 * v0 - 4.0 * r * p_elec_W
    if disc < 0:
        return {"ok": False, "I_bus_A": None, "V_V": None, "P_draw_W": None, "V_open": v0}
    i = (v0 - math.sqrt(disc)) / (2.0 * r)
    return {"ok": True, "I_bus_A": i, "V_V": v0 - i * r, "P_draw_W": v0 * i, "V_open": v0}


def mission_numbers(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    a, f, b = cfg["airframe"], cfg["flight"], cfg["battery"]
    pk = battery_numbers(b)
    mass = float(a["dry_mass_kg"]) + float(a["payload_kg"]) + pk["mass_kg"]
    rho = air_density(a["air_temp_c"], a["altitude_m"])
    from motor_ai_sim import propeller_bemt as pb
    return {"pack": pk, "mass_kg": mass, "W_N": mass * G0, "rho": rho, "a_snd": pb.sound_speed(a["air_temp_c"]),
            "n_rot": int(cfg["motor"]["rotors"]), "f_m2": float(a["drag_area_m2"]), "avionics_W": float(a["avionics_W"]),
            "ambient_c": float(a["air_temp_c"]), "tip_mach": float(f["tip_mach"]), "headroom": float(cfg["kv"]["headroom"]),
            "allow_unverified": bool(cfg["kv"]["allow_unverified"]), "batt_cont_A": cfg["battery"].get("cont_current_A"), "cancel": None}


# ---------------------------------------------------------------------------
# Propeller curves
# ---------------------------------------------------------------------------

Row = Tuple[float, float, float]            # rpm, thrust N, torque N m


def at_thrust(rows: Sequence[Row], thrust_N: float) -> Optional[Tuple[float, float]]:
    """(rpm, torque) at ``thrust_N`` by interpolation (T ~ rpm^2 and Q ~ rpm^2 in between); None outside the curve."""
    for a, b in zip(rows[:-1], rows[1:]):
        if a[1] <= thrust_N <= b[1] and b[1] > a[1]:
            x = (math.sqrt(thrust_N) - math.sqrt(a[1])) / (math.sqrt(b[1]) - math.sqrt(a[1]))
            rpm = a[0] + x * (b[0] - a[0])
            qa, qb = a[2] / a[0] ** 2, b[2] / b[0] ** 2
            return rpm, (qa + x * (qb - qa)) * rpm * rpm
    return None


def at_rpm(rows: Sequence[Row], rpm: float) -> Optional[Tuple[float, float]]:
    """(thrust, torque) at ``rpm`` by linear interpolation; None outside the curve."""
    for a, b in zip(rows[:-1], rows[1:]):
        if a[0] <= rpm <= b[0] and b[0] > a[0]:
            x = (rpm - a[0]) / (b[0] - a[0])
            return a[1] + x * (b[1] - a[1]), a[2] + x * (b[2] - a[2])
    return None


def tip_mach_at(D_m: float, rpm: float, v_ms: float, a_snd: float) -> float:
    return math.hypot(math.pi * D_m * rpm / 60.0, v_ms) / a_snd


def cut_at_mach(rows: Sequence[Row], D_m: float, a_snd: float, limit: float) -> List[Row]:
    """The curve rows whose static tip Mach number is within ``limit``."""
    return [r for r in rows if tip_mach_at(D_m, r[0], 0.0, a_snd) <= limit + 1e-12]


def rotor_flight(rows: Sequence[Row], D_m: float, W_N: float, n_rot: int, f_m2: float, V: float, rho: float) -> Dict[str, Any]:
    """One rotor of a multirotor in steady level flight at ``V``: tilt a = atan(drag / weight), T = sqrt(W^2 + drag^2) / n,
    Glauert induced velocity, P = kappa T v_i + P0 (1 + 4.65 mu^2) + T V sin a with the profile power P0 taken from the
    propeller's own static curve.  At V = 0 the power is the static curve's.  ``error`` when the thrust is outside the curve."""
    drag = 0.5 * rho * V * V * f_m2
    a = math.atan2(drag, W_N)
    T = math.hypot(W_N, drag) / n_rot
    h = at_thrust(rows, T)
    if h is None:
        return {"V": V, "error": "thrust %.1f N is outside the propeller curve" % T, "T_N": T}
    rpm_h, q_h = h
    om_h = 2.0 * math.pi * rpm_h / 60.0
    p_h = q_h * om_h
    area = math.pi * D_m * D_m / 4.0
    v_h = math.sqrt(T / (2.0 * rho * area))
    if V <= 0:
        return {"V": 0.0, "tilt_deg": 0.0, "T_N": T, "rpm": rpm_h, "q_Nm": q_h, "P_shaft_W": p_h, "mu": 0.0, "v_h": v_h,
                "FM": T * v_h / p_h if p_h > 0 else None}
    p0 = max(p_h - KAPPA * T * v_h, 0.05 * p_h)
    vi = v_h
    for _ in range(200):
        vi = 0.5 * vi + 0.5 * v_h * v_h / math.sqrt((V * math.cos(a)) ** 2 + (V * math.sin(a) + vi) ** 2)
    mu = V * math.cos(a) / (om_h * D_m / 2.0)
    p = KAPPA * T * vi + p0 * (1.0 + 4.65 * mu * mu) + T * V * math.sin(a)
    rpm = rpm_h * (p / p_h) ** RPM_POWER_EXP
    q = p / (2.0 * math.pi * rpm / 60.0)
    return {"V": V, "tilt_deg": math.degrees(a), "T_N": T, "rpm": rpm, "q_Nm": q, "P_shaft_W": p, "mu": mu, "v_h": v_h,
            "FM": T * v_h / p_h if p_h > 0 else None}


# ---------------------------------------------------------------------------
# Motor: the S1 model re-wound by turns
# ---------------------------------------------------------------------------

def _fit_row(currents: Sequence[float], values: Sequence[Optional[float]]) -> Optional[Callable[[float], float]]:
    """Least squares y = a I + b I^2 through the non-null cells of one speed row; None with fewer than two."""
    pts = [(i, v) for i, v in zip(currents, values) if v is not None]
    if len(pts) < 2:
        return None
    s11 = sum(i * i for i, _ in pts)
    s12 = sum(i ** 3 for i, _ in pts)
    s22 = sum(i ** 4 for i, _ in pts)
    b1 = sum(i * v for i, v in pts)
    b2 = sum(i * i * v for i, v in pts)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-300:
        return None
    a = (b1 * s22 - b2 * s12) / det
    b = (s11 * b2 - s12 * b1) / det
    return lambda x: max(a * x + b * x * x, 0.0)


def rewind(raw: Mapping[str, Any], n0: int, t0_mm: float, n: int, t_mm: float) -> Tuple[Dict[str, Any], float, float]:
    """The raw S1 model dict re-wound from ``n0`` turns of ``t0_mm`` strip to ``n`` turns of ``t_mm`` strip at the same ampere-turns.
    k = n / n0; currents / k; flux linkage x k; R x k^2 x f with f = n0 t0 / (n t) (copper cross-section now / new); the iron and
    the AC copper loss per cell unchanged (valid while the coil is not taller than today's); the PWM extra loss / k^2; the controller
    loss of each drive re-fitted as a I + b I^2 at the real current (not at k = 1); the demagnetisation and the passport current
    limit / k.  Returns ``(model dict, k, f)``; ``rewind(raw, n0, t0, n0, t0)`` returns the same data."""
    k = float(n) / float(n0)
    f = (float(n0) * float(t0_mm)) / (float(n) * float(t_mm))
    m = copy.deepcopy(dict(raw))
    pm = m.get("psi_map")
    if pm:
        pm["I_A"] = [i / k for i in pm["I_A"]]
        pm["psi_d_Wb"] = [[x * k for x in r] for r in pm["psi_d_Wb"]]
        pm["psi_q_Wb"] = [[x * k for x in r] for r in pm["psi_q_Wb"]]
        pm["psi_pm_Wb"] = pm["psi_pm_Wb"] * k
    g = m["grid"]
    old_i = list(g["I_A"])
    g["I_A"] = [i / k for i in old_i]
    if g.get("I_floor_A"):
        g["I_floor_A"] = [None if x is None else x / k for x in g["I_floor_A"]]
    m["R_phase_ohm"] = m["R_phase_ohm"] * k * k * f
    for d in (m.get("drives") or {}).values():
        di = list(d["I_A"])
        d["I_A"] = [i / k for i in di]
        if d.get("pwm_extra_W"):
            d["pwm_extra_W"] = [[None if x is None else x / (k * k) for x in r] for r in d["pwm_extra_W"]]
        if d.get("controller_loss_W") and k != 1.0:
            new = []
            for r in d["controller_loss_W"]:
                fn = _fit_row(di, r)
                new.append([None if (fn is None or x is None) else fn(i) for x, i in zip(r, d["I_A"])])
            d["controller_loss_W"] = new
    if m.get("demag"):
        m["demag"]["I_limit_A"] = m["demag"]["I_limit_A"] / k
    lim = m.setdefault("limits", {})
    if lim.get("I_max_A") is not None:
        lim["I_max_A"] = lim["I_max_A"] / k
    return m, k, f


def kv_of(raw: Mapping[str, Any]) -> Optional[float]:
    """The model's own Kv [rpm per V, line-line peak, at 20 C]: from the PM flux when the model has a psi map, else from the torque
    constant of the grid (T / I at the first finite cell; Kv = 60 / (2 pi) / (sqrt(3) * T / I / (1.5 sqrt(2))) )."""
    pm = raw.get("psi_map")
    if pm:
        p, c = float(pm["pole_pairs"]), float(raw.get("flux_temp_coeff_per_K") or 0.0)
        psi20 = float(pm["psi_pm_Wb"]) * (1.0 + c * (20.0 - float(raw["magnet_ref_temp_C"])))
        return 60.0 / (2.0 * math.pi * p * math.sqrt(3.0) * psi20) if psi20 > 0 else None
    g = raw["grid"]
    for row in g["T_Nm"]:
        for i, t in zip(g["I_A"], row):
            if t is not None and i and float(i) > 0 and float(t) > 0:
                kt = float(t) / float(i)
                return 60.0 / (2.0 * math.pi) / (math.sqrt(3.0) * kt / (1.5 * math.sqrt(2.0)))
    return None


def calibrate_kv(raw: Dict[str, Any], kv_target: float) -> Dict[str, float]:
    """Shift the PM flux of a raw model (psi map and torque tables, in place) so its Kv at 20 C equals ``kv_target`` (the bench value),
    the passport's own rule: psi_d + dpsi, T + 1.5 p dpsi i_q.  Needs a psi map; returns the scale and the two Kv values."""
    pm = raw.get("psi_map")
    if not pm:
        raise MissionError("the model has no psi map: its Kv cannot be calibrated to a bench value")
    p, t_ref = float(pm["pole_pairs"]), float(raw["magnet_ref_temp_C"])
    c = float(raw.get("flux_temp_coeff_per_K") or 0.0)
    psi_hot = float(pm["psi_pm_Wb"])
    kv_model = 60.0 / (2.0 * math.pi * p * math.sqrt(3.0) * psi_hot * (1.0 + c * (20.0 - t_ref)))
    psi20 = 60.0 / (2.0 * math.pi * p * math.sqrt(3.0) * float(kv_target))
    psi_new = psi20 / (1.0 + c * (20.0 - t_ref))
    dpsi = psi_new - psi_hot
    for i, cur in enumerate(pm["I_A"]):
        for j, gam in enumerate(pm["gamma_deg"]):
            iq = math.sqrt(2.0) * cur * math.cos(math.radians(gam))
            pm["psi_d_Wb"][i][j] = pm["psi_d_Wb"][i][j] + dpsi
            if pm.get("T_Nm") and pm["T_Nm"][i][j] is not None:
                pm["T_Nm"][i][j] = pm["T_Nm"][i][j] + 1.5 * p * dpsi * iq
    pm["psi_pm_Wb"] = psi_new
    g = raw["grid"]
    if g.get("gamma_deg"):
        for a, row in enumerate(g["T_Nm"]):
            for b, t in enumerate(row):
                gg = g["gamma_deg"][a][b]
                if t is not None and gg is not None:
                    row[b] = t + 1.5 * p * dpsi * math.sqrt(2.0) * g["I_A"][b] * math.cos(math.radians(gg))
    return {"kv_model_20C": kv_model, "kv_target": float(kv_target), "flux_scale": psi_new / psi_hot}


def _load_point_v(m: Mapping[str, Any], drive: Mapping[str, Any], rpm: float, q_load: float, h: float, amb: float,
                  v_min: Optional[float]) -> Optional[Dict[str, Any]]:
    """``propeller_s1.load_point`` with the voltage re-check at ``v_min`` inside the fixed point (deeper field weakening raises the
    current, so the losses and the temperatures): the same calls as ``load_point`` with the pack."""
    w = 2.0 * math.pi * rpm / 60.0
    q_em = float(q_load) + (s1._mech_W(m, rpm) / w if w > 0 else 0.0)
    pack = {"v_min": v_min}
    cal = s1.calibration_of(m)
    fp = s1._fixed_point(m, rpm, q_em, h, amb, cal, dict(drive), pack, 1.0, s1.FIXED_POINT_MAX_IT)
    if fp["state"] == "nonconverged":
        fp2 = s1._fixed_point(m, rpm, q_em, h, amb, cal, dict(drive), pack, 0.2, 3 * s1.FIXED_POINT_MAX_IT)
        fp = fp2 if fp2["state"] != "nonconverged" else fp
    vc = fp.get("voltage") or {}
    if fp["state"] != "converged":
        return {"state": fp["state"], "status": fp.get("status"), "voltage": vc}
    t = s1.thermal_at(m, rpm, fp["I"], h, amb, drive or None)
    if not t or t.get("state") != "converged":
        return {"state": (t or {}).get("state") or "loss_data", "voltage": vc}
    return {**t, "state": "converged", "current_A": fp["I"], "I_grid_A": fp.get("I_grid"), "voltage": vc}


class MotorCtx:
    """One winding of the motor: the re-wound validated S1 model with its drive, limits and housing diameter."""

    def __init__(self, raw: Mapping[str, Any], *, turns: int, strip_mm: float, k: float, f: float, drive_id: str,
                 board_cap_A: Optional[float], kv_shown: Optional[float]):
        self.m = s1.validate_model(raw)
        #: an ESTIMATE model (built from a catalogue card) has no psi map: the voltage is checked by ``simple_voltage_ok``
        self.estimate = bool(raw.get("estimate"))
        self.turns, self.strip_mm, self.k, self.f = turns, strip_mm, k, f
        self.drive = s1.resolve_drive(self.m, drive_id)
        self.lim = s1.limits_of(self.m)
        self.od_m = float(self.m["thermal_geom"]["statorOD_mm"]) / 1000.0
        cap = board_cap_A if board_cap_A is not None else self.drive.get("I_cont_A")
        self.board_cap_A = float(cap) if cap else None
        self.kv = kv_shown
        self.pwm = self.drive if self.drive.get("kind") == "pwm" else {}

    def simple_voltage_ok(self, rpm: float, i_rms: float, v_dc: float, r_ohm: Optional[float] = None) -> bool:
        """Estimate models only: line-line peak voltage needed = rpm / Kv + sqrt(3) sqrt(2) R I (inductance and field weakening
        ignored) against the modulation limit ``MODULATION`` x the pack voltage.  ``r_ohm`` = the HOT phase resistance at the
        point's winding temperature (default: the model's reference value)."""
        if not self.kv:
            return True
        r = float(r_ohm) if r_ohm is not None else float(self.m["R_phase_ohm"])
        need = rpm / float(self.kv) + math.sqrt(6.0) * r * i_rms
        return need <= MODULATION * v_dc

    def point(self, rpm: float, q_Nm: float, air_ms: float, ambient_c: float, v_min: Optional[float]) -> Dict[str, Any]:
        """The motor + controller at one point: ``ok`` False with ``state`` when no steady state exists (voltage, current cap,
        loss data); else current, losses, temperatures, electrical input power and the constraint check ``continuous``.
        ``verification`` says how the voltage was checked: ``verified`` (the passport's psi map), ``estimate`` (the simple rule of an
        estimate model) or ``unverified`` (the model has no map or no pack voltage was given: nothing is asserted)."""
        h = s1.housing_film(max(air_ms, 1.0), ambient_c, self.od_m)
        p = _load_point_v(self.m, self.pwm, rpm, q_Nm, h, ambient_c, v_min)
        if not p or p.get("state") != "converged":
            return {"ok": False, "state": (p or {}).get("state") or "none", "status": (p or {}).get("status")}
        w = 2.0 * math.pi * rpm / 60.0
        shaft = q_Nm * w
        motor_loss = float(p["losses_W"]["motor_total"])
        vc = p.get("voltage") or {}
        i = float(p["current_A"])
        ctl = s1.controller_loss(self.m, rpm, i, self.drive if self.drive.get("kind") == "pwm" else None)
        p_in = shaft + motor_loss
        p_elec = p_in + (ctl or 0.0)
        reasons: List[str] = []
        if p["T_winding_C"] > self.lim["winding_C"]:
            reasons.append("winding_temperature")
        if p["T_magnet_C"] > self.lim["magnet_C"]:
            reasons.append("magnet_temperature")
        if i > self.lim["I_max_A"] + 1e-9:
            reasons.append("model_current")
        if self.board_cap_A is not None and i > self.board_cap_A + 1e-9:
            reasons.append("board_current")
        dl, _ = s1.demag_limit_at(self.m, float(p["T_magnet_C"]))
        if dl is not None and i > dl + 1e-9:
            reasons.append("demagnetisation")
        status = vc.get("status")
        if self.estimate:
            if v_min:
                r_hot = s1.resistance(self.m, float(p["T_winding_C"]))
                status = "ok" if self.simple_voltage_ok(rpm, i, float(v_min), r_hot) else "infeasible"
            else:
                status = "unverified"
            verification = "estimate" if status == "ok" else ("unverified" if status == "unverified" else "estimate")
        else:
            verification = "unverified" if status in (None, "unverified") else "verified"
        if status == "infeasible":
            return {"ok": False, "state": "voltage_infeasible", "status": status}
        return {"ok": True, "state": "converged", "rpm": rpm, "torque_Nm": q_Nm, "current_A": i, "T_winding_C": float(p["T_winding_C"]),
                "T_magnet_C": float(p["T_magnet_C"]), "shaft_W": shaft, "P_motor_in_W": p_in, "P_elec_W": p_elec,
                "eta_motor": shaft / p_in if p_in > 0 else None, "controller_loss_W": ctl, "voltage_status": status, "verification": verification,
                "continuous": not reasons, "reasons": reasons, "demag_known": dl is not None}


# ---------------------------------------------------------------------------
# Motors of the catalogue: the full S1 model, or an ESTIMATE built from the card
# ---------------------------------------------------------------------------

KIND_S1 = "s1"
KIND_ESTIMATE = "estimate"
KIND_NONE = "none"
ESTIMATE_LABEL = "estimate, not an S1 model"


def s1_name_index() -> Dict[str, Tuple[str, str]]:
    """Casefolded ``"<die> <config>"`` -> (die, config) of every S1 model file (``propeller_s1.model_dirs``)."""
    out: Dict[str, Tuple[str, str]] = {}
    for root in s1.model_dirs():
        try:
            dies = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in dies:
            for f in sorted(d.glob("*.json")):
                out["%s %s" % (d.name.casefold(), f.stem.casefold())] = (d.name, f.stem)
    return out


def _fnum(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def card_values(card: Mapping[str, Any]) -> Dict[str, Any]:
    """What the catalogue card and its stored passport say, as plain numbers (None where missing): Kv from the no-load EMF
    (phase peak at the base rpm, Kv line-line peak = rpm0 / (sqrt(3) EMF)), phase R and its temperature, pole pairs, mass, rated and
    peak current, iron and magnet loss at the base speed, winding rows, strip."""
    sp = card.get("passport") or {}
    inner = sp.get("passport") or {}
    fit, geo = sp.get("fit") or {}, sp.get("geo") or {}
    emf, rpm0 = _fnum(inner.get("Vemf0_peak_V")), _fnum(inner.get("rpm0"))
    cur = inner.get("current") or {}
    ia = [x for x in (_fnum(v) for v in (cur.get("I_A") or [])) if x is not None]
    poles = _fnum(sp.get("poles")) or _fnum(geo.get("numPoles")) or _fnum(card.get("poles"))
    par, split, omit = _fnum(inner.get("wire_parallel0")) or 1.0, _fnum(inner.get("wire_split0")) or 1.0, _fnum(inner.get("wire_omit0")) or 0.0
    return {"kv": (rpm0 / (math.sqrt(3.0) * emf)) if (emf and rpm0 and emf > 0) else None, "rpm0": rpm0,
            "R_ohm": _fnum(inner.get("R0_ohm")), "R_temp_C": _fnum(inner.get("R0_temp_C")) or 120.0,
            "pole_pairs": (poles / 2.0) if poles else None, "mass_kg": _fnum(inner.get("mass0_kg")) or _fnum(card.get("mass_kg")),
            "I0_A": _fnum(inner.get("I0_A")), "I_peak_A": max(ia) if ia else None, "T0_Nm": _fnum(inner.get("T0_Nm")),
            "Pfe0_W": _fnum(inner.get("Pfe0_W")), "Pmag0_W": _fnum(inner.get("Pmag0_W")), "turns": _fnum(inner.get("N0")),
            "series_turns": ((_fnum(inner.get("N0")) or 0.0) / par * split - omit) or None,
            "strip_thickness_mm": _fnum(inner.get("wireH0_mm")), "strip_width_mm": _fnum(fit.get("wireWidth_mm")),
            "length_mm": _fnum(inner.get("L0_mm")) or _fnum(card.get("length_mm")), "diameter_mm": _fnum(card.get("diameter_mm")),
            "current_table": ({"I_A": [float(x) for x in cur.get("I_A")], "T_Nm": [float(x) for x in cur.get("T_Nm")]}
                              if cur.get("I_A") and cur.get("T_Nm") and len(cur["I_A"]) == len(cur["T_Nm"]) else None),
            "fit": fit, "geo": geo}


def estimate_problem(card: Mapping[str, Any]) -> Optional[str]:
    """None when an estimate model can be built from the card, else the reason in one line (the picker greys the motor)."""
    v = card_values(card)
    if not card.get("passport"):
        return "the card has no stored passport"
    if v["kv"] is None:
        return "no Kv (no-load EMF) in the stored passport"
    if v["R_ohm"] is None or v["R_ohm"] <= 0:
        return "no phase resistance in the stored passport"
    if v["pole_pairs"] is None:
        return "no pole count"
    if not (v["I_peak_A"] or v["I0_A"]):
        return "no rated or peak current"
    if not v["turns"]:
        return "no winding turns (N0) in the stored passport: the motor cannot be re-wound"
    return None


def estimate_model(card: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A simple S1-schema model from the card (kind ``estimate``): torque from the stored current sweep (else Kv: T = Kt I), phase R at its
    temperature (copper loss I^2 R with the S1 copper law), iron loss from the base point scaled ~ rpm^1.5 (omitted and flagged when none
    is stored), magnet eddy loss ~ rpm^2, no AC copper loss, thermal geometry from the card's passport geometry (generic when absent),
    voltage by the simple rule of ``MotorCtx.simple_voltage_ok``.  Returns ``(raw model dict, info)``; the model carries ``estimate`` and
    ``kv_bench_rpm_per_V`` (= the card's Kv) and ``winding`` for the turns re-winding.  ``MissionError`` with the reason when not possible."""
    why = estimate_problem(card)
    if why:
        raise MissionError(why)
    v = card_values(card)
    flags = ["estimate"]
    kv = float(v["kv"])
    kt = 60.0 / (2.0 * math.pi) / (math.sqrt(3.0) / (1.5 * math.sqrt(2.0))) / kv        # N m per A rms, the inverse of kv_of
    i_pk = float(v["I_peak_A"] or 1.5 * v["I0_A"])
    tab = v["current_table"]
    if tab and len(tab["I_A"]) >= 3 and all(b > a for a, b in zip(tab["I_A"], tab["I_A"][1:])) and tab["I_A"][0] > 0:
        cur, tq = tab["I_A"], tab["T_Nm"]
        flags.append("torque_from_stored_current_sweep")
    else:
        cur = [i_pk * x / 8.0 for x in range(1, 9)]
        tq = [kt * i for i in cur]
        flags.append("torque_linear_from_Kv")
    rpm_ax = [500.0 * (60.0 ** (j / 11.0)) for j in range(12)]               # 500 .. 30000 rpm
    rpm0 = float(v["rpm0"] or 3000.0)
    loss: Dict[str, Any] = {}
    zero = ["fe_rotor", "cu_ac"]
    if v["Pfe0_W"] is not None and v["Pfe0_W"] >= 0:
        loss["fe_stator"] = [[float(v["Pfe0_W"]) * (r / rpm0) ** 1.5 for _ in cur] for r in rpm_ax]
        flags.append("iron_loss_scaled_rpm_1.5")
    else:
        zero.append("fe_stator")
        flags.append("iron_loss_omitted")
    if v["Pmag0_W"]:
        loss["rotor_eddy"] = [[float(v["Pmag0_W"]) * (r / rpm0) ** 2 for _ in cur] for r in rpm_ax]
    else:
        zero.append("rotor_eddy")
    fit, geo = v["fit"], v["geo"]
    L = float(v["length_mm"] or 20.0)
    od = (2.0 * float(geo["statorOR_mm"])) if _fnum(geo.get("statorOR_mm")) else float(v["diameter_mm"] or 40.0)
    if _fnum(geo.get("statorOR_mm")) and _fnum(fit.get("slotHeight_mm")) and _fnum(geo.get("statorIR_mm")) and _fnum(geo.get("rotorOR_mm")):
        sh = float(fit["slotHeight_mm"])
        tg = {"statorOD_mm": od, "stackLength_mm": L, "numSlots": float(geo.get("numSlots") or 12), "slotHeight_mm": sh,
              "slotWidth_mm": float(fit.get("slotWidth_mm") or 3.0), "insulation_mm": float(fit.get("insulation_mm") or 0.2),
              "coreThickness_mm": max(0.5, float(geo["statorOR_mm"]) - float(geo["statorIR_mm"]) - sh),
              "airGap_mm": max(0.2, abs(float(geo["statorIR_mm"]) - float(geo["rotorOR_mm"]))), "magnetOD_mm": 2.0 * float(geo["rotorOR_mm"])}
    else:
        tg = {"statorOD_mm": od, "stackLength_mm": L, "numSlots": 12.0, "slotHeight_mm": 0.15 * od, "slotWidth_mm": 0.08 * od,
              "insulation_mm": 0.2, "coreThickness_mm": 0.06 * od, "airGap_mm": 0.5, "magnetOD_mm": 0.55 * od}
        flags.append("thermal_geometry_generic")
    raw = {"schema": s1.SCHEMA, "die": str(card.get("name") or "card"), "config": "estimate",
           "R_phase_ohm": float(v["R_ohm"]), "R_temp_C": float(v["R_temp_C"]), "magnet_ref_temp_C": 20.0, "flux_temp_coeff_per_K": 0.0,
           "torque_factor": 1.0,
           "grid": {"rpm": rpm_ax, "I_A": cur, "T_Nm": [list(tq) for _ in rpm_ax], "loss_W": loss},
           "loss_groups_zero": zero,
           "limits": {"I_max_A": i_pk, "rpm_max": rpm_ax[-1], "magnet_C": _magnet_limit(card), "I_max_basis": "peak current of the stored passport"},
           "thermal_geom": tg, "estimate": {"label": ESTIMATE_LABEL, "flags": flags, "card_id": card.get("id")},
           "kv_bench_rpm_per_V": kv}
    if v["turns"] and v["strip_thickness_mm"] and v["strip_width_mm"]:
        raw["winding"] = {"turns": int(round(float(v["turns"]))), "strip_thickness_mm": float(v["strip_thickness_mm"]),
                          "strip_width_mm": float(v["strip_width_mm"])}
    else:
        flags.append("winding_strip_unknown")
    info = {"kind": KIND_ESTIMATE, "label": ESTIMATE_LABEL, "flags": flags, "kv_rpm_per_V": kv, "R_phase_ohm": float(v["R_ohm"]),
            "pole_pairs": v["pole_pairs"], "mass_kg": v["mass_kg"], "I_peak_A": i_pk, "I_rated_A": v["I0_A"]}
    return raw, info


def _magnet_limit(card: Mapping[str, Any]) -> float:
    import re
    mt = re.search(r"(\d{2,3})\s*C\b", str(card.get("magnet") or ""))
    return float(mt.group(1)) if mt else 120.0


def card_entry(card: Mapping[str, Any], s1_index: Mapping[str, Tuple[str, str]]) -> Dict[str, Any]:
    """One row of the picker: the card, its model kind (``s1`` | ``estimate`` | ``none`` with the reason) and the numbers it shows."""
    name = str(card.get("name") or "")
    hit = s1_index.get(name.casefold())
    v = card_values(card)
    base = {"id": card.get("id"), "name": name, "diameter_mm": card.get("diameter_mm"), "die": None, "config": None, "kind": KIND_NONE,
            "reason": None, "kv_rpm_per_V": v["kv"], "R_phase_ohm": v["R_ohm"], "pole_pairs": v["pole_pairs"], "mass_kg": v["mass_kg"],
            "I_peak_A": v["I_peak_A"]}
    if hit:
        return {**base, "die": hit[0], "config": hit[1], "kind": KIND_S1}
    why = estimate_problem(card)
    if why:
        return {**base, "reason": why}
    return {**base, "kind": KIND_ESTIMATE, "label": ESTIMATE_LABEL}


# ---------------------------------------------------------------------------
# Windings: turns, strips from the stock, Kv
# ---------------------------------------------------------------------------

def strips_for(stock: Sequence[Mapping[str, Any]], width_mm: float, n: int, n0: int, t0_mm: float, tol: float) -> List[Dict[str, Any]]:
    """The stocked strips of the same width with ``n * t <= n0 * t0 * (1 + tol)`` (the coil is not taller than today's), thickest
    first.  ``stock`` rows: ``{thickness_mm, width_mm, stock_kg, check}``; a row with less than ``LOW_STOCK_KG`` or an unconfirmed
    label (``check``) is kept and marked."""
    out = []
    for r in stock:
        if abs(float(r["width_mm"]) - width_mm) > 0.05:
            continue
        t = float(r["thickness_mm"])
        if n * t <= n0 * t0_mm * (1.0 + tol) + 1e-9:
            out.append({"thickness_mm": t, "width_mm": float(r["width_mm"]), "stock_kg": float(r["stock_kg"]), "check": bool(r.get("check")),
                        "low_stock": float(r["stock_kg"]) < LOW_STOCK_KG})
    out.sort(key=lambda s: -s["thickness_mm"])
    return out


def turn_options(stock: Sequence[Mapping[str, Any]], width_mm: float, n0: int, t0_mm: float, below: int, above: int,
                 tol: float) -> List[Dict[str, Any]]:
    """Ascending list of ``{turns, strip}`` for N in [n0 - below, n0 + above]: the thickest fitting stock strip (the winding as built
    for N = n0 always stays, taken as built even when its strip is not in the stock table)."""
    opts: List[Dict[str, Any]] = []
    for n in range(max(1, n0 - below), n0 + above + 1):
        fit = strips_for(stock, width_mm, n, n0, t0_mm, tol)
        if fit:
            opts.append({"turns": n, "strip": fit[0], "as_built": n == n0 and abs(fit[0]["thickness_mm"] - t0_mm) < 1e-9})
        elif n == n0:
            opts.append({"turns": n, "strip": {"thickness_mm": t0_mm, "width_mm": width_mm, "stock_kg": None, "check": False,
                                              "low_stock": False}, "as_built": True})
    return opts


def choose_turns(options: Sequence[Mapping[str, Any]], reachable: Callable[[int], bool]) -> Tuple[Optional[Dict[str, Any]], bool]:
    """The inner Kv step: the LARGEST N whose peak point ``reachable(N)`` (voltage headroom held).  Larger N = lower Kv = lower
    current, and every constraint that depends on the current only improves with N, so the first hit from the top is the choice.
    With none reachable the smallest N (highest Kv) is returned and the flag is False (the row then fails the headroom rule)."""
    if not options:
        return None, False
    for o in sorted(options, key=lambda x: -x["turns"]):
        if reachable(int(o["turns"])):
            return dict(o), True
    return dict(min(options, key=lambda x: x["turns"])), False


class MotorBank:
    """The windings of one motor: options by turns, the context of each (built once, kept)."""

    def __init__(self, raw: Mapping[str, Any], cfg: Mapping[str, Any], stock: Sequence[Mapping[str, Any]]):
        self.raw = raw
        kv = cfg["kv"]
        wind = raw.get("winding") or {}
        n0 = kv["turns0"] or wind.get("turns")
        t0 = kv["strip_thickness0_mm"] or wind.get("strip_thickness_mm")
        w0 = kv["strip_width_mm"] or wind.get("strip_width_mm")
        if kv["mode"] == "free" and not (n0 and t0 and w0):
            raise MissionError("the model carries no winding data (turns, strip thickness and width): enter kv.turns0, "
                               "kv.strip_thickness0_mm and kv.strip_width_mm, or use kv.mode 'as_built'")
        # unknown winding (as-built mode only): identity scaling inside, null in every result (review #922 E)
        self.winding_known = bool(n0 and t0 and w0)
        self.n0 = int(n0 or 1)
        self.t0 = float(t0 or 1.0)
        self.w0 = float(w0 or 0.0)
        self.kv_bench = raw.get("kv_bench_rpm_per_V")
        self.calibrate = bool(kv["calibrate_bench"]) and self.kv_bench is not None and bool(raw.get("psi_map"))
        self.drive_id = cfg["motor"]["drive"]
        self.board_cap = cfg["motor"]["board_cap_A"]
        if kv["mode"] == "free":
            self.options = turn_options(stock, self.w0, self.n0, self.t0, int(kv["turns_below"]), int(kv["turns_above"]), float(kv["coil_height_tol"]))
        else:
            self.options = [{"turns": self.n0, "strip": {"thickness_mm": self.t0, "width_mm": self.w0, "stock_kg": None, "check": False,
                                                         "low_stock": False}, "as_built": True}]
        self._ctx: Dict[int, MotorCtx] = {}

    def shown_turns(self, turns: Any) -> Optional[int]:
        return int(turns) if self.winding_known and turns is not None else None

    def shown_strip(self, strip: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        if strip is None:
            return None
        return dict(strip) if self.winding_known else {**strip, "thickness_mm": None, "width_mm": None, "unknown": True}

    def ctx(self, turns: int) -> MotorCtx:
        if turns not in self._ctx:
            opt = next(o for o in self.options if o["turns"] == turns)
            t = float(opt["strip"]["thickness_mm"])
            r, k, f = rewind(self.raw, self.n0, self.t0, turns, t)
            kv_shown = None
            if self.kv_bench is not None:
                kv_shown = float(self.kv_bench) * self.n0 / turns
                if self.calibrate:
                    calibrate_kv(r, kv_shown)
            else:
                kv_shown = kv_of(r)
            self._ctx[turns] = MotorCtx(r, turns=turns, strip_mm=t, k=k, f=f, drive_id=self.drive_id, board_cap_A=self.board_cap, kv_shown=kv_shown)
        return self._ctx[turns]


# ---------------------------------------------------------------------------
# Candidate evaluation
# ---------------------------------------------------------------------------

def _fail(out: List[Dict[str, Any]], rule: str, detail: str) -> None:
    out.append({"rule": rule, "detail": detail})


def solve_pack_point(ctx: MotorCtx, ms: Mapping[str, Any], rpm: float, q_Nm: float, air_ms: float, which: str, headroom: float = 0.0
                     ) -> Dict[str, Any]:
    """One motor point together with the pack that feeds ALL rotors, iterated to convergence: the motor is solved at the voltage the pack
    gives under the load the motor itself (with its losses, at that voltage) puts on it.  ``which`` = 'mean' | 'end' of discharge;
    ``headroom`` lowers the voltage the motor must hold.  Impossible or non-converged points are rejected (``ok`` False with a reason),
    never approximated with an assumed efficiency."""
    n, pk = ms["n_rot"], ms["pack"]
    v = pack_state(pk, 0.0, which)["V_V"]
    last = None
    for it in range(1, 41):
        _chk(ms)
        mp = ctx.point(rpm, q_Nm, air_ms, ms["ambient_c"], v * (1.0 - headroom))
        if not mp["ok"]:
            return {"ok": False, "reason": "motor_" + str(mp.get("state")), "detail": "motor: %s %s" % (mp.get("state"), mp.get("status") or ""), "iterations": it}
        st = pack_state(pk, n * mp["P_elec_W"] + ms["avionics_W"], which)
        if not st["ok"]:
            return {"ok": False, "reason": "pack_cannot_deliver", "detail": "the pack cannot supply this power at %s of discharge" % which, "iterations": it}
        last = (mp, st)
        if abs(st["V_V"] - v) <= 1e-4 * max(v, 1.0):
            bus = ctx.drive.get("bus_v")                         # the LOADED bus voltage against the controller's window (review #922 B)
            if bus and not (bus["min"] * 0.999 <= st["V_V"] <= bus["max"] * 1.001):
                return {"ok": False, "reason": "drive_bus_window",
                        "detail": "loaded pack voltage %.2f V at %s of discharge is outside the controller's bus range %.2f-%.2f V"
                                  % (st["V_V"], which, bus["min"], bus["max"]), "iterations": it, "last_V": st["V_V"]}
            return {"ok": True, "mp": mp, "pack": st, "V_used": v, "iterations": it}
        v = 0.5 * v + 0.5 * st["V_V"]
    return {"ok": False, "reason": "nonconverged", "detail": "motor and pack voltage did not converge in 40 iterations", "iterations": 40,
            "last_V": last[1]["V_V"] if last else None}


def _pt_summary(ms: Mapping[str, Any], D_m: float, fl: Mapping[str, Any], mp: Mapping[str, Any], pack: Mapping[str, Any]) -> Dict[str, Any]:
    return {"V_ms": fl["V"], "thrust_N": fl["T_N"], "tilt_deg": fl.get("tilt_deg"), "rpm": fl["rpm"], "torque_Nm": fl["q_Nm"],
            "shaft_W": mp.get("shaft_W"), "current_A": mp.get("current_A"), "pack_power_W": pack.get("P_draw_W"),
            "pack_current_A": pack.get("I_bus_A"), "pack_V": pack.get("V_V"), "motor_eta": mp.get("eta_motor"),
            "T_winding_C": mp.get("T_winding_C"), "T_magnet_C": mp.get("T_magnet_C"), "voltage_status": mp.get("voltage_status"),
            "verification": mp.get("verification"), "tip_mach": tip_mach_at(D_m, fl["rpm"], fl["V"], ms["a_snd"]), "mu": fl.get("mu")}


def flight_point(ctx: MotorCtx, rows: Sequence[Row], D_m: float, ms: Mapping[str, Any], V: float, which: str = "mean",
                 headroom: float = 0.0) -> Dict[str, Any]:
    """The whole aircraft at speed V (V = 0: hover): rotor flight, motor + controller of one rotor, pack of all rotors, solved together
    (``solve_pack_point``).  ``which`` = 'mean' | 'end' selects the pack voltage; ``headroom`` lowers the voltage the motor must hold.
    ``reasons`` lists every constraint the point breaks (motor limits, tip Mach, voltage headroom, battery current, unverified voltage)."""
    fl = rotor_flight(rows, D_m, ms["W_N"], ms["n_rot"], ms["f_m2"], V, ms["rho"])
    if "error" in fl:
        return {"ok": False, "reason": "thrust_outside_curve", "detail": fl["error"], "V_ms": V}
    sp = solve_pack_point(ctx, ms, fl["rpm"], fl["q_Nm"], V + fl["v_h"], which, headroom)
    if not sp["ok"]:
        return {"ok": False, "reason": sp["reason"], "detail": sp["detail"], "V_ms": V, "flight": fl}
    mp, st = sp["mp"], sp["pack"]
    summ = _pt_summary(ms, D_m, fl, mp, st)
    summ["P_elec_W"] = ms["n_rot"] * mp["P_elec_W"] + ms["avionics_W"]
    summ["pack_iterations"] = sp["iterations"]
    reasons = list(mp["reasons"])
    if summ["tip_mach"] > ms["tip_mach"] + 1e-9:
        reasons.append("tip_mach")
    if headroom > 0 and mp.get("voltage_status") == "deeper_fw":
        reasons.append("headroom")
    if ms.get("batt_cont_A") and st["I_bus_A"] is not None and st["I_bus_A"] > float(ms["batt_cont_A"]) + 1e-9:
        reasons.append("battery_current")
    if mp.get("verification") == "unverified" and not ms["allow_unverified"]:
        reasons.append("voltage_unverified")
    return {"ok": True, "V_ms": V, "point": summ, "pack": st, "reasons": reasons, "pass": not reasons, "verification": mp.get("verification"),
            "demag_known": mp.get("demag_known")}


def _limit_row(rows: Sequence[Row], test: Callable[[Row], Optional[Dict[str, Any]]]) -> Tuple[Optional[Row], Optional[Dict[str, Any]]]:
    """The highest curve row for which ``test`` returns a dict (None = fails), assuming the test holds at low rpm and stops holding
    above some rpm (loads rise with rpm): bisection over the row index, a few motor evaluations instead of one per row."""
    if not rows:
        return None, None
    res0 = test(rows[0])
    if res0 is None:
        return None, None
    best: Tuple[Optional[Row], Optional[Dict[str, Any]]] = (rows[0], res0)
    lo, hi = 0, len(rows) - 1
    resh = test(rows[hi])
    if resh is not None:
        return rows[hi], resh
    while hi - lo > 1:
        mid = (lo + hi) // 2
        r = test(rows[mid])
        if r is not None:
            lo, best = mid, (rows[mid], r)
        else:
            hi = mid
    return best


def thrust_limits(ctx: MotorCtx, rows: Sequence[Row], ms: Mapping[str, Any]) -> Dict[str, Any]:
    """Highest thrust per rotor the motor holds continuously (temperatures, current limits, board cap, voltage at the end of
    discharge, the pack solved with the motor) and the highest it reaches at all (voltage and the model's current limit only), at
    5 m/s air over the housing."""
    cache: Dict[float, Optional[Dict[str, Any]]] = {}
    verifs: set = set()

    def cached(row: Row) -> Optional[Dict[str, Any]]:
        if row[0] not in cache:
            _chk(ms)
            sp = solve_pack_point(ctx, ms, row[0], row[2], 5.0, "end", 0.0)
            cache[row[0]] = sp["mp"] if sp["ok"] else None
            if sp["ok"]:
                verifs.add(sp["mp"].get("verification"))
        return cache[row[0]]

    cont_row, cont_mp = _limit_row(rows, lambda r: (cached(r) if (cached(r) or {}).get("continuous") else None))
    peak_row, peak_mp = _limit_row(rows, lambda r: (cached(r) if (cached(r) or {}).get("current_A", 1e99) <= ctx.lim["I_max_A"] + 1e-9 else None))
    out: Dict[str, Any] = {"cont": None, "peak": None, "verification": sorted(v for v in verifs if v)}
    if cont_row:
        out["cont"] = {"thrust_N": cont_row[1], "rpm": cont_row[0], "torque_Nm": cont_row[2], "current_A": cont_mp["current_A"],
                       "T_winding_C": cont_mp["T_winding_C"]}
    if peak_row:
        out["peak"] = {"thrust_N": peak_row[1], "rpm": peak_row[0], "torque_Nm": peak_row[2], "current_A": peak_mp["current_A"]}
    return out


def speed_scan(ctx: MotorCtx, rows: Sequence[Row], D_m: float, ms: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """Level flight over speeds (2 m/s steps from 0 until the first failure after a pass, then 4 bisections): the top speed (thrust /
    tilt, motor continuous, board cap and voltage headroom all hold at the END-of-discharge voltage), and the speeds of best range and
    best endurance among the passing ones (energy at the mean voltage)."""
    h = float(cfg["kv"]["headroom"])
    vmax = float(cfg["flight"]["speed_max_ms"])
    pts: List[Dict[str, Any]] = []
    passed_any = False
    v = 0.0
    last_pass, first_fail = None, None
    while v <= vmax + 1e-9:
        _chk(ms)
        fp = flight_point(ctx, rows, D_m, ms, v, "end", h)
        pts.append(fp)
        if fp["ok"] and fp["pass"]:
            passed_any, last_pass = True, v
        elif passed_any:
            first_fail = v
            break
        v += 2.0
    top = last_pass
    if last_pass is not None and first_fail is not None:
        lo, hi = last_pass, first_fail
        for _ in range(4):
            _chk(ms)
            mid = 0.5 * (lo + hi)
            fp = flight_point(ctx, rows, D_m, ms, mid, "end", h)
            if fp["ok"] and fp["pass"]:
                lo = mid
            else:
                hi = mid
        top = lo
    best_range, best_end = None, None
    e_wh = ms["pack"]["E_usable_Wh"]
    share = float(cfg["flight"]["hover_share_pct"]) / 100.0
    p_hover = None
    means: Dict[float, Dict[str, Any]] = {}
    for fp in pts:
        if fp["ok"] and fp["pass"]:
            _chk(ms)
            means[fp["V_ms"]] = flight_point(ctx, rows, D_m, ms, fp["V_ms"], "mean")
    if 0.0 in means and means[0.0]["ok"]:
        p_hover = means[0.0]["pack"]["P_draw_W"]
    for vv, mean in means.items():
        if not mean["ok"]:
            continue
        p = mean["pack"]["P_draw_W"]
        t_h = e_wh / p
        if best_end is None or t_h > best_end["time_h"]:
            best_end = {"V_ms": vv, "time_h": t_h, "endurance_min": t_h * 60.0}
        if vv > 0:
            p_mix = share * p_hover + (1.0 - share) * p if (share > 0 and p_hover) else p
            rng = vv * (1.0 - share) * (e_wh / p_mix) * 3.6
            if best_range is None or rng > best_range["range_km"]:
                best_range = {"V_ms": vv, "range_km": rng}
    return {"top_speed_ms": top, "best_range": best_range, "best_endurance": best_end,
            "points": [{"V_ms": q["V_ms"], "ok": q["ok"], "pass": bool(q.get("pass")), "reason": q.get("reason")} for q in pts]}


def peak_headroom(ctx: MotorCtx, rows: Sequence[Row], ms: Mapping[str, Any], cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """The Kv check: the peak-thrust point (T/W peak; the top of the curve when the curve does not reach it) must be reachable at the
    END-of-discharge pack voltage under load, lowered by the headroom, with the motor and the pack solved together.  ``verification``:
    an ``unverified`` voltage check never counts as headroom held (unless the run allows it explicitly)."""
    req = float(cfg["flight"]["tw_peak"]) * ms["W_N"] / ms["n_rot"]
    pk = at_thrust(rows, req)
    reaches = pk is not None
    if pk is None:
        pk = (rows[-1][0], rows[-1][2])
    rpm, q = pk
    sp = solve_pack_point(ctx, ms, rpm, q, 5.0, "end", float(cfg["kv"]["headroom"]))
    info = {"rpm": rpm, "torque_Nm": q, "curve_reaches": reaches, "thrust_required_N": req, "verification": None, "status": None}
    if not sp["ok"]:
        return {**info, "held": False, "reason": sp["reason"]}
    mp = sp["mp"]
    info.update(verification=mp.get("verification"), status=mp.get("voltage_status"), current_A=mp["current_A"])
    if mp.get("voltage_status") == "deeper_fw" or mp["current_A"] > ctx.lim["I_max_A"] + 1e-9:
        return {**info, "held": False, "reason": "headroom"}
    if mp.get("verification") == "unverified" and not ms["allow_unverified"]:
        return {**info, "held": False, "reason": "voltage_unverified"}
    return {**info, "held": True, "reason": None}


def choose_for_candidate(bank: MotorBank, rows: Sequence[Row], D_m: float, ms: Mapping[str, Any], cfg: Mapping[str, Any]
                         ) -> Tuple[Dict[str, Any], bool, Dict[str, Any]]:
    """The inner Kv step for one propeller (``choose_turns`` with ``peak_headroom``).  Returns (chosen option, headroom held, peak check)."""
    def reachable(n: int) -> bool:
        _chk(ms)
        return bool(peak_headroom(bank.ctx(n), rows, ms, cfg)["held"])

    opt, ok = choose_turns(bank.options, reachable)
    info = peak_headroom(bank.ctx(int(opt["turns"])), rows, ms, cfg) if opt else {}
    return opt, ok, info


def evaluate_candidate(cand: Mapping[str, Any], bank: MotorBank, ms: Mapping[str, Any], cfg: Mapping[str, Any],
                       turns_override: Optional[int] = None) -> Dict[str, Any]:
    """Everything for one candidate (``cand``: id, name, source, D_in, pitch_ratio, blades, ..., ``rows`` = the static curve).  The
    result is JSON-ready; ``passed`` False with ``failures`` when a hard constraint fails (the numbers are still given).  The required
    hover and cruise points are checked at the mean AND at the end-of-discharge voltage (with the headroom); ``checks`` says what could
    and could not be verified."""
    _chk(ms)
    D_m = float(cand["D_in"]) * 0.0254
    rows = cut_at_mach(cand["rows"], D_m, ms["a_snd"], ms["tip_mach"])
    base = {k: cand.get(k) for k in ("id", "name", "source", "prop_id", "D_in", "pitch_ratio", "pitch_in", "chord_scale", "blades", "flags", "vector")}
    base["curve"] = {"source": cand.get("curve_source"), "points": len(cand["rows"]), "points_within_mach": len(rows),
                     "rpm_min": cand["rows"][0][0] if cand["rows"] else None, "rpm_max": cand["rows"][-1][0] if cand["rows"] else None}
    failures: List[Dict[str, Any]] = []
    if len(rows) < 3:
        _fail(failures, "no_curve", "fewer than three curve points within the tip Mach limit")
        return {**base, "passed": False, "failures": failures, "objectives": {}, "objective_value": None, "checks": {}}
    if turns_override is not None:
        opt = next((o for o in bank.options if o["turns"] == turns_override), None)
        if opt is None:
            return {**base, "passed": False, "failures": [{"rule": "no_turns", "detail": "no such winding"}], "objectives": {}, "objective_value": None, "checks": {}}
        held, pk_info = True, {}
    else:
        opt, held, pk_info = choose_for_candidate(bank, rows, D_m, ms, cfg)
    if opt is None:
        _fail(failures, "no_winding", "no winding option")
        return {**base, "passed": False, "failures": failures, "objectives": {}, "objective_value": None, "checks": {}}
    ctx = bank.ctx(int(opt["turns"]))
    if turns_override is not None:
        pk_info = peak_headroom(ctx, rows, ms, cfg)
        held = bool(pk_info["held"])
    if not held:
        if pk_info.get("reason") == "drive_bus_window":
            _fail(failures, "drive_bus_window", "peak point: the loaded pack voltage at the end of discharge is outside the controller's bus range")
        elif pk_info.get("reason") == "voltage_unverified":
            _fail(failures, "voltage_unverified", "the voltage check of this motor model is unverified: headroom cannot be asserted")
        else:
            _fail(failures, "headroom", "the peak point is not reachable with %.0f %% voltage headroom at the end-of-discharge voltage"
                  % (100.0 * cfg["kv"]["headroom"]))

    h = float(cfg["kv"]["headroom"])
    hov = flight_point(ctx, rows, D_m, ms, 0.0, "mean")
    cru_v = float(cfg["flight"]["cruise_speed_ms"])
    cru = flight_point(ctx, rows, D_m, ms, cru_v, "mean") if not cfg["flight"]["best_speed"] else None
    hov_end = flight_point(ctx, rows, D_m, ms, 0.0, "end", h)
    cru_end = flight_point(ctx, rows, D_m, ms, cru_v, "end", h) if not cfg["flight"]["best_speed"] else None
    lim = thrust_limits(ctx, rows, ms)
    W4 = ms["W_N"] / ms["n_rot"]
    tw_cont = lim["cont"]["thrust_N"] / W4 if lim["cont"] else 0.0
    tw_peak = lim["peak"]["thrust_N"] / W4 if lim["peak"] else 0.0
    if tw_cont < float(cfg["flight"]["tw_cont"]):
        _fail(failures, "tw_cont", "T/W continuous %.2f is below %.2f" % (tw_cont, cfg["flight"]["tw_cont"]))
    if tw_peak < float(cfg["flight"]["tw_peak"]):
        _fail(failures, "tw_peak", "T/W peak %.2f is below %.2f" % (tw_peak, cfg["flight"]["tw_peak"]))
    unverified_seen = False
    for name, fp in (("hover", hov), ("cruise", cru), ("hover_end", hov_end), ("cruise_end", cru_end)):
        if fp is None:
            continue
        if not fp["ok"]:
            _fail(failures, name + "_drive_bus_window" if fp.get("reason") == "drive_bus_window" else name,
                  fp.get("detail") or fp.get("reason") or "no point")
        else:
            for r in fp["reasons"]:
                if r == "voltage_unverified":
                    unverified_seen = True
                    continue
                _fail(failures, name + "_" + r, "%s: %s" % (name, r))
    if unverified_seen and not any(f["rule"] == "voltage_unverified" for f in failures):
        _fail(failures, "voltage_unverified", "the voltage check of this motor model is unverified: the required points cannot be asserted")

    # what could and could not be checked (shown beside the result)
    verifs = {fp.get("verification") for fp in (hov, cru, hov_end, cru_end) if fp and fp.get("ok")} | set(lim.get("verification") or []) | {pk_info.get("verification")}
    verifs.discard(None)
    level = "unverified" if "unverified" in verifs else ("estimate" if "estimate" in verifs else ("verified" if verifs else "unknown"))
    pack_win = {"v_min": ms["pack"]["V_end_open"], "v_max": ms["pack"]["S"] * float(cfg["battery"]["v_max"])}
    fits = s1.pack_fits_drive(ctx.drive, pack_win)
    if fits is False:
        _fail(failures, "drive_bus_window", "the pack voltage window %.1f-%.1f V is outside the controller's bus range" % (pack_win["v_min"], pack_win["v_max"]))
    demag = {bool(fp.get("demag_known")) for fp in (hov, cru, hov_end, cru_end) if fp and fp.get("ok")}
    loaded_bus_fail = any(fp is not None and not fp.get("ok") and fp.get("reason") == "drive_bus_window" for fp in (hov, cru, hov_end, cru_end)) \
        or pk_info.get("reason") == "drive_bus_window"
    bus_state = "unknown" if fits is None else ("ok" if fits and not loaded_bus_fail else "fail")
    checks = {"voltage": level, "bus_window": bus_state,
              "board_current": "set" if ctx.board_cap_A is not None else "unknown", "battery_current": "set" if ms.get("batt_cont_A") else "unknown",
              "demagnetisation": "known" if (demag and all(demag)) else "unknown"}

    scan = speed_scan(ctx, rows, D_m, ms, cfg)
    e_wh = ms["pack"]["E_usable_Wh"]
    share = float(cfg["flight"]["hover_share_pct"]) / 100.0
    obj: Dict[str, Optional[float]] = {"range_km": None, "endurance_min": None, "top_speed_ms": scan["top_speed_ms"], "hover_g_per_w": None}
    if hov["ok"]:
        p_h = hov["pack"]["P_draw_W"]
        obj["hover_g_per_w"] = ms["mass_kg"] * 1000.0 / p_h
        obj["endurance_min"] = e_wh / p_h * 60.0
        if cfg["flight"]["endurance_mode"] == "best_speed" and scan["best_endurance"]:
            obj["endurance_min"] = scan["best_endurance"]["endurance_min"]
    if cfg["flight"]["best_speed"]:
        if scan["best_range"]:
            obj["range_km"] = scan["best_range"]["range_km"]
        else:
            _fail(failures, "no_speed", "no speed passes all constraints")
    elif cru is not None and cru["ok"] and hov["ok"]:
        p_c = cru["pack"]["P_draw_W"]
        p_mix = share * hov["pack"]["P_draw_W"] + (1.0 - share) * p_c
        obj["range_km"] = cru_v * (1.0 - share) * (e_wh / p_mix) * 3.6
    elif cru is not None and cru["ok"]:
        obj["range_km"] = cru_v * (e_wh / cru["pack"]["P_draw_W"]) * 3.6
    value = obj.get(cfg["objective"])
    if value is None and cfg["objective"] == "top_speed_ms":
        _fail(failures, "no_speed", "no speed passes all constraints")
    strip = opt["strip"]
    points = {"hover": hov.get("point") if hov["ok"] else None, "cruise": cru["point"] if (cru and cru["ok"]) else None,
              "hover_end": hov_end.get("point") if hov_end["ok"] else None, "cruise_end": cru_end["point"] if (cru_end and cru_end["ok"]) else None}
    return {**base, "turns": bank.shown_turns(opt["turns"]), "strip": bank.shown_strip(strip), "as_built": bool(opt.get("as_built")),
            "winding_known": bank.winding_known, "k": ctx.k, "copper_f": ctx.f,
            "kv_rpm_per_V": ctx.kv, "R_phase_ohm": ctx.m["R_phase_ohm"], "board_cap_A": ctx.board_cap_A,
            "objectives": obj, "objective_value": value, "tw_cont": tw_cont, "tw_peak": tw_peak, "limits": lim, "peak_point": pk_info,
            "points": points, "best_speed": {"range": scan["best_range"], "endurance": scan["best_endurance"]}, "checks": checks,
            "failures": failures, "passed": not failures and value is not None}


def rank(results: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Passing rows first by ``key`` (higher is better), then failing rows by the same key; rows without a value last.  Stable."""
    def k(r: Mapping[str, Any]) -> Tuple[int, float]:
        v = (r.get("objectives") or {}).get(key)
        return (0 if r.get("passed") else 1, -(v if v is not None else -1e18))
    return sorted(results, key=k)


# ---------------------------------------------------------------------------
# Candidates: catalogue and the own family
# ---------------------------------------------------------------------------

CATALOGUE_QUALITY = ("torque_measured", "thrust_measured_power_estimated")


def catalogue_candidates(cfg: Mapping[str, Any], rho: float) -> List[Dict[str, Any]]:
    """Catalogue propellers with usable thrust AND power data inside the diameter window: their static curves from the published fit
    over the tested rpm range (forward flight by the same energy method on the static curve)."""
    a = cfg["airframe"]
    out = []
    for p in pp.list_propellers():
        if not p.selectable or p.data_quality not in CATALOGUE_QUALITY or not p.rpm_range:
            continue
        d_in = p.diameter_m / 0.0254
        if not (float(a["min_diameter_in"]) - 1e-9 <= d_in <= float(a["max_diameter_in"]) + 1e-9):
            continue
        lo, hi = p.rpm_range
        rows: List[Row] = []
        for i in range(25):
            rpm = lo + (hi - lo) * i / 24.0
            t, q = pp.thrust_N(p, rpm, rho), pp.torque_Nm(p, rpm, rho)
            if t > 0 and q > 0:
                rows.append((rpm, t, q))
        out.append({"id": "cat:" + p.id, "name": "%s %s" % (p.vendor, p.model), "source": "catalogue", "prop_id": p.id, "D_in": d_in,
                    "pitch_in": p.pitch_in, "pitch_ratio": (p.pitch_in / d_in) if p.pitch_in else None, "chord_scale": None,
                    "blades": p.blades, "flags": ["static_data_only", "power_estimated" if p.power_data == "estimated" else "power_measured"],
                    "rows": rows})
    return out


def pitch_ratios(c: Mapping[str, Any]) -> List[float]:
    out, x, n = [], float(c["pd_min"]), 0
    while x <= float(c["pd_max"]) + 1e-9 and n < 200:
        out.append(round(x, 4))
        x += float(c["pd_step"])
        n += 1
    return out


def helix_vector(base: Mapping[str, Any], D_in: float, pitch_ratio: float, chord_scale: float, blades: int) -> Dict[str, Any]:
    """The constant-pitch helix on the design's base blade: every twist control point set to atan(P/D / (pi r)), the chords scaled."""
    v = dict(base)
    v["diameter_in"] = float(D_in) if not float(D_in).is_integer() else int(D_in)
    v["blades"] = int(blades)
    for k in list(v):
        if k.startswith("twist_deg@"):
            r = float(k.split("@")[1])
            v[k] = math.degrees(math.atan(pitch_ratio / (math.pi * r)))
        if k.startswith("chord_R@"):
            v[k] = base[k] * chord_scale
    return v


def own_family_specs(cfg: Mapping[str, Any], design: Any) -> List[Dict[str, Any]]:
    """The explicit grid of own propellers (no curves yet): diameter in the family's half inches within the window, P/D, chord scale,
    blades.  ``vector`` is the design vector."""
    from motor_ai_sim import propeller_design as pd
    a, c = cfg["airframe"], cfg["candidates"]
    out = []
    for D in pd.family_diameters(design):
        if not (float(a["min_diameter_in"]) - 1e-9 <= float(D) <= float(a["max_diameter_in"]) + 1e-9):
            continue
        for B in c["blades"]:
            if int(B) not in design.blades_options:
                continue
            base = pd.seed_vector(design, D, int(B))
            for pr in pitch_ratios(c):
                for cs in c["chords"]:
                    vec = helix_vector(base, float(D), pr, float(cs), int(B))
                    out.append({"id": "own:D%g-PD%.2f-C%.2f-B%d" % (D, pr, cs, B), "name": "own D%g P/D %.2f chord x%.2f B%d" % (D, pr, cs, B),
                                "source": "own", "prop_id": None, "D_in": float(D), "pitch_in": pr * float(D), "pitch_ratio": pr,
                                "chord_scale": float(cs), "blades": int(B), "vector": vec})
    return out


def mission_design() -> Any:
    """The own-family design with the mfoil section tables and the Prandtl-Glauert correction (the study's choice)."""
    from motor_ai_sim import propeller_design as pd
    return pd.with_compressibility(pd.with_polar_source(pd.load_design(), "mfoil_table"), "prandtl_glauert")


def own_curve(spec: Mapping[str, Any], design: Any, ms: Mapping[str, Any], rpm_step: float) -> Tuple[List[Row], List[str]]:
    """Static T(rpm), Q(rpm) of one own propeller by the blade-element model, from 2000 rpm up to the tip Mach limit.  Raises
    ``MissionError`` for an invalid design (the reasons) so the caller lists the row as failed."""
    from motor_ai_sim import propeller_bemt as pb
    from motor_ai_sim import propeller_design as pd
    vec = spec["vector"]
    bad = pd.check_candidate(design, vec)
    if bad:
        raise MissionError("; ".join("%s %s" % (b["rule"], b["name"]) for b in bad))
    prop = pd.build_propeller(design, vec)
    pol = pb.polar_for(prop)
    D_m = float(spec["D_in"]) * 0.0254
    rpm_cap = ms["tip_mach"] * ms["a_snd"] / (math.pi * D_m) * 60.0
    rows: List[Row] = []
    flags: set = set()
    rpm = 2000.0
    while rpm <= rpm_cap + 1e-9:
        _chk(ms)
        try:
            r = pb.solve(prop.blade, int(vec["blades"]), float(rpm), ms["rho"], 0.0, polar=pol, temp_c=ms["ambient_c"])
        except Exception:                                          # noqa: BLE001  (a speed the section model cannot solve is skipped)
            r = None
        if r is not None and r.thrust_N > 0 and r.torque_Nm > 0:
            rows.append((float(rpm), float(r.thrust_N), float(r.torque_Nm)))
            flags |= set(getattr(r, "flags", ()) or ())
        rpm += rpm_step
    return rows, sorted(flags)


# ---------------------------------------------------------------------------
# Stage 2: refine the best own propellers with the existing optimiser
# ---------------------------------------------------------------------------

CURVE_SOURCE_OWN = "blade-element model bemt-1, static, mfoil section tables + Prandtl-Glauert lift correction"
CURVE_SOURCE_CATALOGUE = "catalogue fit of the published static test points (thrust, torque) over the tested rpm range"


def _compact(res: Mapping[str, Any]) -> Dict[str, Any]:
    keep = ("turns", "strip", "kv_rpm_per_V", "objectives", "objective_value", "tw_cont", "tw_peak", "checks", "failures", "passed", "points", "curve")
    return {k: res.get(k) for k in keep}


def optimizer_evaluate(bank: "MotorBank", ms: Mapping[str, Any], cfg: Mapping[str, Any],
                       on_eval: Optional[Callable[[], None]] = None) -> Callable[[Any, Dict[str, Any], str], Dict[str, Any]]:
    """The ``evaluate`` callback of ``propeller_optimize.optimize`` for the opt-in ``mission_objective``: curve of the candidate by the
    blade-element model, then the mission with the chosen motor, battery and flight (Kv inner step inside).  The record carries the
    objective and one boolean per failed mission rule (every failed rule counts 1 in the optimiser's violation).  A stop raises
    ``MissionCancelled`` (not a ``DesignError``), which leaves the optimiser at once."""
    from motor_ai_sim import propeller_design as pd
    from motor_ai_sim import propeller_optimize as po

    def evaluate(design: Any, vec: Dict[str, Any], op: str) -> Dict[str, Any]:
        _chk(ms)
        if on_eval:
            on_eval()
        spec = {"vector": vec, "D_in": float(vec["diameter_in"])}
        try:
            rows, flags = own_curve(spec, design, ms, float(cfg["candidates"]["rpm_step"]))
        except MissionError as exc:
            raise pd.DesignError(str(exc))
        cand = {"id": "opt", "name": "opt", "source": "own", "D_in": spec["D_in"], "blades": int(vec["blades"]), "flags": flags, "rows": rows,
                "curve_source": CURVE_SOURCE_OWN}
        res = evaluate_candidate(cand, bank, ms, cfg)
        value = res.get("objective_value")
        return {"design_id": "mission", "objectives": {po.MISSION_OBJECTIVE: value}, "tip_mach": 0.0, "flags": flags,
                "constraints": {f["rule"]: False for f in res.get("failures", [])}, "operating": {"rpm": (res.get("points", {}).get("cruise") or {}).get("rpm")},
                "mission": _compact(res)}
    return evaluate


def refine_candidate(seed_spec: Mapping[str, Any], seed_result: Mapping[str, Any], bank: "MotorBank", ms: Mapping[str, Any], cfg: Mapping[str, Any],
                     design: Any, on_eval: Optional[Callable[[], None]] = None, store_dir: Optional[Any] = None) -> Dict[str, Any]:
    """NSGA-II (the existing ``propeller_optimize.optimize``) over the design variables from the stage-1 vector with the single
    objective ``mission_objective``; diameter and blade count stay.  EVERY evaluation (failures included) is stored in ``store_dir``
    (``<run id>.jsonl`` + ``.run.json`` of the optimiser's own record format) and its run id is returned.  Returns the stage-1 seed,
    the best feasible refined design with its full mission numbers, the gain in percent and the counts."""
    from motor_ai_sim import propeller_optimize as po
    rf = cfg["refine"]
    op = sorted(design.operating_points)[0]
    ev = optimizer_evaluate(bank, ms, cfg, on_eval)
    out = po.optimize(design, dict(seed_spec["vector"]), op, objectives=[po.MISSION_OBJECTIVE], pop=int(rf["pop"]), generations=int(rf["generations"]),
                      seed=int(rf["seed"]), free=list(rf["free"]), enforce_tip_mach=False, evaluate=ev, store_dir=store_dir)
    feas = [e for e in out["evaluations"] if e["constraints_met"] and e["objectives"].get(po.MISSION_OBJECTIVE) is not None]
    seed_value = seed_result.get("objective_value")
    best = max(feas, key=lambda e: e["objectives"][po.MISSION_OBJECTIVE]) if feas else None
    gain = None
    if best is not None and seed_value:
        gain = 100.0 * (best["objectives"][po.MISSION_OBJECTIVE] - seed_value) / abs(seed_value)
    final = None
    _chk(ms)                                                     # a stop before the final recomputation ends the stage here
    if best is not None:
        spec = {"vector": best["vector"], "D_in": float(best["vector"]["diameter_in"])}
        rows, flags = own_curve(spec, design, ms, float(cfg["candidates"]["rpm_step"]))
        final = evaluate_candidate({"id": "refined", "name": "refined " + str(seed_result.get("name")), "source": "own", "D_in": spec["D_in"],
                                    "blades": int(best["vector"]["blades"]), "pitch_ratio": seed_result.get("pitch_ratio"), "flags": flags, "rows": rows,
                                    "vector": best["vector"], "curve_source": CURVE_SOURCE_OWN}, bank, ms, cfg)
    return {"from_id": seed_result.get("id"), "from_name": seed_result.get("name"), "seed_vector": dict(seed_spec["vector"]), "seed_value": seed_value,
            "objective": cfg["objective"], "best_vector": best["vector"] if best else None, "best_value": best["objectives"][po.MISSION_OBJECTIVE] if best else None,
            "gain_pct": gain, "result": final, "n_evaluations": len(out["evaluations"]), "n_feasible": len(feas),
            "optimizer_run_id": out.get("run_id"), "optimizer_store": out.get("stored"),
            "settings": {k: rf[k] for k in ("pop", "generations", "seed", "free")}, "optimizer": "propeller_optimize.optimize (NSGA-II), objective mission_objective"}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def stock_rows() -> List[Dict[str, Any]]:
    """The wire stock table (``wire_stock``): thickness, width, kg and the unconfirmed-label flag."""
    from motor_ai_sim import wire_stock
    return [{"thickness_mm": w.thickness_mm, "width_mm": w.width_mm, "stock_kg": w.stock_kg, "check": w.check} for w in wire_stock.list_wires()]


def count_candidates(cfg: Mapping[str, Any]) -> int:
    """How many candidates the inputs ask for (the catalogue entries in the window plus the own grid)."""
    n = 0
    if cfg["candidates"]["own"]:
        design = mission_design()
        n += len(own_family_specs(cfg, design))
    if cfg["candidates"]["catalogue"]:
        n += len(catalogue_candidates(cfg, 1.225))
    return n


_PROV_CACHE: Dict[str, str] = {}


def _sha256_files(paths: Sequence[Any]) -> str:
    import hashlib
    h = hashlib.sha256()
    for f in sorted(paths, key=lambda x: str(x)):
        try:
            h.update(str(getattr(f, "name", f)).encode())
            h.update(open(f, "rb").read())
        except OSError:
            h.update(b"?")
    return h.hexdigest()


def _code_commit() -> str:
    if "commit" not in _PROV_CACHE:
        import os
        import subprocess
        from pathlib import Path
        out = os.environ.get("MOTRES_COMMIT", "")
        if not out:
            try:
                out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(Path(__file__).resolve().parent), capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:                                    # noqa: BLE001
                out = ""
        _PROV_CACHE["commit"] = out or "unknown"
    return _PROV_CACHE["commit"]


def provenance(stock: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the numbers were computed with: the code commit, the hashes of the design file, the section polar tables and the wire stock
    used, and the solver identities.  (The motor model's hash is added by the caller that read the file.)"""
    import hashlib
    import json
    from motor_ai_sim import propeller_bemt as pb
    from motor_ai_sim import propeller_design as pd
    if "polars" not in _PROV_CACHE:
        _PROV_CACHE["polars"] = _sha256_files(sorted(pb.MFOIL_POLAR_DIR.glob("*.csv")))
        _PROV_CACHE["design"] = _sha256_files([pd.DESIGN_PATH])
    return {"code_commit": _code_commit(), "model_version": MODEL_VERSION, "design_file_sha256": _PROV_CACHE["design"],
            "polar_tables_sha256": _PROV_CACHE["polars"],
            "wire_stock_sha256": hashlib.sha256(json.dumps(list(stock), sort_keys=True, default=str).encode()).hexdigest(),
            "solvers": {"propeller_bemt": getattr(pb, "MODEL_BEMT", "bemt-1"), "s1_schema": s1.SCHEMA}}


def run_selection(cfg: Mapping[str, Any], raw_model: Mapping[str, Any], *, stock: Optional[Sequence[Mapping[str, Any]]] = None,
                  progress: Optional[Callable[[int, int, str], None]] = None, should_stop: Optional[Callable[[], bool]] = None,
                  store_dir: Optional[Any] = None) -> Dict[str, Any]:
    """The whole selection.  ``raw_model`` is the raw S1 model dict of the chosen motor; ``progress(done, total, line)`` and
    ``should_stop()`` are optional.  A stop (``should_stop`` true, checked inside every loop) ends the run with the partial results and
    ``stopped`` True (``stopped_stage``); a stop that arrives after the last step also reports ``stopped`` (the results are then complete).
    ``store_dir``: where the optimiser stores every stage-2 evaluation.  Returns ``{inputs, mission, results (ranked), alternatives,
    refined, counts, provenance, note, ...}``."""
    ms = mission_numbers(cfg)
    ms["cancel"] = should_stop
    stock_used = list(stock) if stock is not None else stock_rows()
    bank = MotorBank(raw_model, cfg, stock_used)
    design = mission_design() if cfg["candidates"]["own"] else None
    specs: List[Dict[str, Any]] = own_family_specs(cfg, design) if design is not None else []
    cats = catalogue_candidates(cfg, ms["rho"]) if cfg["candidates"]["catalogue"] else []
    total = len(specs) + len(cats)
    if total > int(cfg["candidates"]["max_candidates"]):
        raise MissionError("the inputs ask for %d candidates; the limit is %d (narrow the diameter window or the grid)"
                           % (total, cfg["candidates"]["max_candidates"]))
    if total == 0:
        raise MissionError("no candidate propeller in the diameter window")
    results: List[Dict[str, Any]] = []
    cand_by_id: Dict[str, Dict[str, Any]] = {}
    alternatives: Dict[str, List[Dict[str, Any]]] = {}
    refined: List[Dict[str, Any]] = []
    done = 0
    stage = "stage1"
    stopped = False
    keys = ("id", "name", "source", "prop_id", "D_in", "pitch_ratio", "pitch_in", "chord_scale", "blades", "vector")
    try:
        for c in cats + specs:
            _chk(ms)
            c["curve_source"] = CURVE_SOURCE_CATALOGUE if c["source"] == "catalogue" else CURVE_SOURCE_OWN
            if "rows" not in c:
                try:
                    c["rows"], c["flags"] = own_curve(c, design, ms, float(cfg["candidates"]["rpm_step"]))
                except MissionError as exc:
                    results.append({k: c.get(k) for k in keys} | {"passed": False, "objectives": {}, "objective_value": None,
                                                                  "failures": [{"rule": "invalid_design", "detail": str(exc)}], "checks": {}})
                    done += 1
                    if progress:
                        progress(done, total, c["name"])
                    continue
            try:
                res = evaluate_candidate(c, bank, ms, cfg)
            except MissionCancelled:
                raise
            except Exception as exc:                            # noqa: BLE001  (one bad candidate must not end the run)
                res = {k: c.get(k) for k in keys} | {"passed": False, "objectives": {}, "objective_value": None, "checks": {},
                                                      "failures": [{"rule": "evaluation_error", "detail": "%s: %s" % (type(exc).__name__, exc)}]}
            cand_by_id[c["id"]] = c
            results.append(res)
            done += 1
            if progress:
                progress(done, total, c["name"])
        stage = "alternatives"
        ranked = rank(results, cfg["objective"])
        opts = [o["turns"] for o in bank.options]
        for r in ranked[:3]:
            if not r.get("turns") or r["id"] not in cand_by_id or r["turns"] not in opts:
                continue
            i = opts.index(r["turns"])
            alts = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(opts):
                    _chk(ms)
                    a = evaluate_candidate(cand_by_id[r["id"]], bank, ms, cfg, turns_override=opts[j])
                    alts.append({"turns": opts[j], "strip": a.get("strip"), "kv_rpm_per_V": a.get("kv_rpm_per_V"), "objectives": a.get("objectives"),
                                 "tw_cont": a.get("tw_cont"), "tw_peak": a.get("tw_peak"), "passed": a.get("passed"), "failures": a.get("failures")})
            alternatives[r["id"]] = alts
        rf = cfg["refine"]
        if rf["enabled"] and design is not None:
            stage = "stage2"
            seeds = [r for r in ranked if r.get("source") == "own" and r.get("passed") and r["id"] in cand_by_id][: int(rf["top_n"])]
            total += len(seeds) * int(rf["pop"]) * (int(rf["generations"]) + 1)

            def tick() -> None:
                nonlocal done
                done += 1
                if progress:
                    progress(done, total, "refining")
            for k, r in enumerate(seeds):
                sd = None
                if store_dir is not None:
                    from pathlib import Path
                    sd = Path(store_dir) / ("seed%d" % (k + 1))
                refined.append(refine_candidate(cand_by_id[r["id"]], r, bank, ms, cfg, design, tick, sd))
        stage = "done"
    except MissionCancelled:
        stopped = True
    ranked = rank(results, cfg["objective"])
    prov = provenance(stock_used)
    if not stopped and should_stop is not None and should_stop():
        stopped = True                                           # a stop that came too late to cut anything: complete results, terminal state stopped
    return {"version": MODEL_VERSION, "note": NOTE, "inputs": cfg, "refined": refined,
            "mission": {"mass_kg": ms["mass_kg"], "weight_N": ms["W_N"], "rho_kg_m3": ms["rho"], "pack": ms["pack"], "rotors": ms["n_rot"]},
            "winding": {"known": bank.winding_known, "turns0": bank.shown_turns(bank.n0),
                        "strip_thickness0_mm": bank.t0 if bank.winding_known else None, "strip_width_mm": bank.w0 if bank.winding_known else None,
                        "kv_bench_rpm_per_V": bank.kv_bench,
                        "options": [{"turns": bank.shown_turns(o["turns"]), "strip": bank.shown_strip(o["strip"])} for o in bank.options]},
            "results": ranked, "alternatives": alternatives, "counts": {"total": total, "done": done, "passed": sum(1 for r in results if r.get("passed"))},
            "stopped": stopped, "stopped_stage": stage if stopped else None, "complete": stage == "done", "provenance": prov}
