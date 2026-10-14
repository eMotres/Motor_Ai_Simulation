# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""System solve for the shaft chain  motor -> [transmission] -> load.

Two solves, both pure functions of the IRs (no state, nothing written):

  * ``solve_operating_point`` - where the motor's torque capability meets the load
    seen through the transmission (full throttle).  The motor side is a
    ``MotorPerformanceIR``.
  * ``solve_s1`` - the propeller-loaded continuous (S1) rating of
    ``motor_ai_sim.propeller_s1`` with a transmission between motor and propeller.
    It REUSES the existing S1 point solver (``propeller_s1.solve_point``) through its
    ``load`` hook, so the motor thermal / loss / voltage physics is the existing one;
    this module only changes the load the motor sees.  With no transmission and a
    propulsor sampled from a catalogue propeller it reproduces ``s1_rating``.

Directions: v0.1 is motoring (power flows motor -> load).  The chain, from the
motor shaft::

    n_out = n_in / ratio        T_in solves  T_in*w_in - loss(n_in, T_in) = T_out*w_out
    T_out, P_out, thrust  from the load at n_out (rho, airspeed)

A point outside any declared validity range is a loud ``limited_by`` / ``binding``
(``transmission_validity`` / ``load_validity``), never a silent clamp.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from ... import propeller as pp
from ... import propeller_s1 as s1
from ...contracts import (MotorPerformanceIR, OutOfValidity, PropulsorIR, ShaftLoadIR,
                          TransmissionIR)

SCHEMA = "drivetrain_result-1"
_W = math.pi / 30.0                      # rpm -> rad/s


# ---------------------------------------------------------------------------
# the chain at one motor speed
# ---------------------------------------------------------------------------

def chain_at(n_in_rpm: float, transmission: Optional[TransmissionIR], load: ShaftLoadIR | PropulsorIR,
             *, rho: float = pp.RHO_ISA, airspeed_ms: float = 0.0) -> Dict[str, Any]:
    """Load, transmission and motor-shaft demand at motor speed ``n_in_rpm``.
    Raises ``OutOfValidity`` (transmission or load map)."""
    ratio = transmission.ratio if transmission is not None else 1.0
    n_out = n_in_rpm / ratio
    try:
        lp = load.evaluate(n_out, rho=rho, airspeed_ms=airspeed_ms)
    except OutOfValidity as exc:
        raise OutOfValidity(f"load_validity: {exc}") from exc
    if transmission is None:
        t_in, split = lp.torque_Nm, {}
    else:
        try:
            t_in = transmission.input_torque_for_output(n_in_rpm, lp.torque_Nm)
            split = transmission.loss_split_W(n_in_rpm, t_in)
        except OutOfValidity as exc:
            raise OutOfValidity(f"transmission_validity: {exc}") from exc
    p_in = t_in * n_in_rpm * _W
    loss = sum(split.values())
    return {"n_in_rpm": n_in_rpm, "n_out_rpm": n_out, "load": lp, "torque_in_Nm": t_in,
            "torque_out_Nm": lp.torque_Nm, "power_in_W": p_in, "power_out_W": lp.shaft_power_W,
            "loss_W": loss, "loss_split_W": split,
            "efficiency": (lp.shaft_power_W / p_in) if p_in > 0 else None}


def _checks(motor: Optional[MotorPerformanceIR], tr: Optional[TransmissionIR],
            load: ShaftLoadIR | PropulsorIR, c: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Ratings and interface checks at a chain point.  ``ok`` None = cannot be checked
    (a number the contract allows to be absent) - reported, never passed silently."""
    out: List[Dict[str, Any]] = []

    def add(name: str, value: Optional[float], limit: Optional[float], unit: str) -> None:
        ok = None if (value is None or limit is None) else bool(value <= limit * (1 + 1e-9))
        out.append({"name": name, "ok": ok, "value": value, "limit": limit, "unit": unit})

    if motor is not None:
        add("motor_speed", c["n_in_rpm"], motor.limits.speed_max_rpm, "rpm")
    if tr is not None:
        lim = tr.limits
        add("transmission_torque_in", c["torque_in_Nm"], lim.torque_in_max_Nm, "N*m")
        add("transmission_torque_out", c["torque_out_Nm"], lim.torque_out_max_Nm, "N*m")
        add("transmission_speed_in", c["n_in_rpm"], lim.speed_in_max_rpm, "rpm")
        add("transmission_speed_out", c["n_out_rpm"], lim.speed_out_max_rpm, "rpm")
        add("transmission_heat", c["loss_W"], lim.loss_continuous_max_W, "W")

    def iface(name: str, a: Any, b: Any) -> None:
        if a is None or b is None:
            out.append({"name": name, "ok": None, "value": None, "limit": None, "unit": "mm"})
        else:
            out.append({"name": name, "ok": abs(a.diameter_mm - b.diameter_mm) <= 0.05,
                        "value": a.diameter_mm, "limit": b.diameter_mm, "unit": "mm"})

    if tr is not None:
        iface("interface_motor_to_transmission", motor.shaft if motor is not None else None, tr.input_shaft)
        iface("interface_transmission_to_load", tr.output_shaft, load.shaft_interface)
    else:
        iface("interface_motor_to_load", motor.shaft if motor is not None else None, load.shaft_interface)
    return out


def _motor_eta(motor: MotorPerformanceIR, rpm: float, torque_Nm: float) -> Optional[float]:
    try:
        return motor.efficiency_at(rpm, torque_Nm) if rpm > 0 else None
    except OutOfValidity:
        return None                                      # outside the efficiency map: not claimed


# ---------------------------------------------------------------------------
# operating point (torque equilibrium)
# ---------------------------------------------------------------------------

def solve_operating_point(motor: MotorPerformanceIR, transmission: Optional[TransmissionIR],
                          load: ShaftLoadIR | PropulsorIR, *, rho: float = pp.RHO_ISA,
                          airspeed_ms: float = 0.0, n_grid: int = 2000) -> Dict[str, Any]:
    """The highest motor speed at which the motor's peak torque still covers the load
    seen through the transmission (full throttle).  ``limited_by``:
    ``torque_equilibrium`` (a genuine crossing), ``motor_speed_limit`` (the motor out-
    torques the load up to the top of its envelope), ``no_motor_torque``,
    ``transmission_validity`` / ``load_validity`` (a declared range ends first)."""
    top = motor.speed_rpm[-1]
    top = min(top, motor.limits.speed_max_rpm)
    if transmission is not None:
        top = min(top, transmission.speed_in_rpm[-1])
    fail: Dict[str, str] = {}

    def margin(n: float) -> float:
        try:
            c = chain_at(n, transmission, load, rho=rho, airspeed_ms=airspeed_ms)
        except OutOfValidity as exc:
            fail["why"] = str(exc)
            return -1.0
        return motor.torque_peak_at(n) - c["torque_in_Nm"]

    if max(motor.torque_peak_Nm) <= 0.0:
        n_star, limited = 0.0, "no_motor_torque"
    else:
        n_star, limited, prev = top, "motor_speed_limit", 0.0
        for k in range(1, n_grid + 1):
            n = top * k / n_grid
            fail.clear()
            if margin(n) < 0.0:
                a, b = prev, n
                for _ in range(60):
                    mid = 0.5 * (a + b)
                    fail.clear()
                    if margin(mid) >= 0.0:
                        a = mid
                    else:
                        b = mid
                n_star = 0.5 * (a + b)
                fail.clear()
                margin(b)                                   # record why b failed (validity or torque)
                limited = (fail["why"].split(":")[0] if "why" in fail else "torque_equilibrium")
                break
            prev = n
    c = chain_at(n_star, transmission, load, rho=rho, airspeed_ms=airspeed_ms)
    checks = _checks(motor, transmission, load, c)
    lp = c["load"]
    masses = [motor.mass_kg, transmission.mass_kg if transmission else 0.0, load.mass_kg]
    j_load = load.inertia_kg_m2
    ratio = transmission.ratio if transmission else 1.0
    j_refl = None
    if motor.rotor_inertia_kg_m2 is not None and j_load is not None:
        j_refl = (motor.rotor_inertia_kg_m2 + (transmission.inertia_in_kg_m2 if transmission else 0.0)
                  + ((transmission.inertia_out_kg_m2 if transmission else 0.0) + j_load) / ratio ** 2)
    return {
        "schema": SCHEMA, "kind": "operating_point",
        "motor_id": motor.id, "transmission_id": transmission.id if transmission else None, "load_id": load.id,
        "environment": {"rho_kg_m3": rho, "airspeed_ms": airspeed_ms},
        "limited_by": limited, "limited_by_detail": fail.get("why"),
        "point": {"motor_rpm": c["n_in_rpm"], "output_rpm": c["n_out_rpm"],
                  "motor_torque_Nm": c["torque_in_Nm"], "motor_torque_peak_Nm": motor.torque_peak_at(c["n_in_rpm"]),
                  "load_torque_Nm": c["torque_out_Nm"], "motor_shaft_power_W": c["power_in_W"],
                  "load_shaft_power_W": c["power_out_W"], "transmission_loss_W": c["loss_W"],
                  "transmission_loss_split_W": c["loss_split_W"], "transmission_efficiency": c["efficiency"],
                  "motor_efficiency": _motor_eta(motor, c["n_in_rpm"], c["torque_in_Nm"]),
                  "load_output": {"name": lp.output_name, "value": lp.output, "unit": lp.output_unit},
                  "extrapolated": lp.extrapolated, "power_estimated": lp.power_estimated},
        "checks": checks,
        "ok": all(k["ok"] is not False for k in checks),
        "unverified": [k["name"] for k in checks if k["ok"] is None],
        "mass_kg_total": (sum(masses) if all(x is not None for x in masses) else None),
        "inertia_reflected_to_motor_kg_m2": j_refl,
        "assumptions": ["motoring only (power flows motor -> load)",
                        "motor torque capability = the IR's peak envelope (voltage limit not applied here)",
                        "transmission loss maps interpolated bilinearly; no extrapolation"]
                       + lp.notes,
    }


# ---------------------------------------------------------------------------
# S1 (continuous rating) through a transmission
# ---------------------------------------------------------------------------

def make_load_hook(transmission: Optional[TransmissionIR], load: ShaftLoadIR | PropulsorIR, *,
                   airspeed_ms: float = 0.0, cooling_air_ms: float = 0.0) -> Callable[..., Dict[str, Any]]:
    """The ``load`` hook for ``propeller_s1.solve_point``: the operating_point-shaped
    dict of the load as the MOTOR shaft sees it (torque after the transmission loss)."""
    def hook(rpm: float, rho: float, position: Optional[str], factor: Optional[float]) -> Dict[str, Any]:
        c = chain_at(rpm, transmission, load, rho=rho, airspeed_ms=airspeed_ms)
        lp = c["load"]
        f, label = pp.position_factor(position, factor)
        air = f * lp.wake_speed_ms if lp.wake_speed_ms is not None else float(cooling_air_ms)
        return {"rpm": float(rpm), "rho_kg_m3": rho, "thrust_N": lp.output if lp.output_name == "thrust" else 0.0,
                "torque_Nm": c["torque_in_Nm"], "shaft_power_W": c["power_in_W"],
                "wake_velocity_ms": lp.wake_speed_ms or 0.0, "air_speed_ms": air,
                "slipstream_position": label, "slipstream_factor": f,
                "extrapolated": lp.extrapolated, "power_estimated": lp.power_estimated}
    return hook


def solve_s1(model: Mapping[str, Any], transmission: Optional[TransmissionIR], load: ShaftLoadIR | PropulsorIR,
             *, ambient_c: float = 25.0, altitude_m: float = 0.0, airspeed_ms: float = 0.0,
             cooling_air_ms: float = 0.0, position: Optional[str] = None, factor: Optional[float] = None,
             n_points: int = 41, rpm_min: Optional[float] = None, rpm_max: Optional[float] = None,
             tol_rpm: float = 1.0, winding_limit_c: Optional[float] = None, magnet_limit_c: Optional[float] = None,
             drive: Optional[str] = None, board_cooling: Optional[str] = None,
             pack_v_min: Optional[float] = None, pack_v_max: Optional[float] = None,
             pack_i_max_A: Optional[float] = None) -> Dict[str, Any]:
    """Continuous (S1) limit of the chain.  ``model`` = a validated passport S1 model
    (``propeller_s1.load_model``).  Rows are over MOTOR speed.  A row is ok only if the
    motor holds (``propeller_s1.solve_point``) AND the transmission holds its ratings
    (torque, speed, heat).  The limit is the upper edge of the highest passing band.
    The transmission's heat is NOT fed into the motor's thermal network (separate body)."""
    drv = s1.resolve_drive(model, drive, board_cooling)
    pk = s1.resolve_pack(model, pack_v_min, pack_v_max, pack_i_max_A)
    lims = s1.limits_of(model, winding_limit_c, magnet_limit_c)
    rho = pp.air_density(temp_c=ambient_c, altitude_m=altitude_m)
    hook = make_load_hook(transmission, load, airspeed_ms=airspeed_ms, cooling_air_ms=cooling_air_ms)
    hi = float(rpm_max) if rpm_max else lims["rpm_max"]
    if transmission is not None:
        hi = min(hi, transmission.speed_in_rpm[-1])
    if not rpm_max and isinstance(load, PropulsorIR):
        hi = min(hi, 1.5 * load.speed_rpm[-1] * (transmission.ratio if transmission else 1.0))
    lo = float(rpm_min) if rpm_min else 0.1 * hi
    if not (0 < lo < hi):
        raise s1.S1Error("rpm range must satisfy 0 < rpm_min < rpm_max")
    n = max(5, min(int(n_points), 201))
    cache: Dict[float, Dict[str, Any]] = {}

    def f(r: float) -> Dict[str, Any]:
        k = round(float(r), 6)
        if k in cache:
            return cache[k]
        try:
            row = s1.solve_point(model, None, r, ambient_c=ambient_c, rho=rho, lims=lims, position=position,
                                 factor=factor, drive=drv, pack=pk, load=hook)
            c = chain_at(r, transmission, load, rho=rho, airspeed_ms=airspeed_ms)
            ch = _checks(None, transmission, load, c)
            row["output_rpm"] = c["n_out_rpm"]
            row["transmission"] = {"loss_W": c["loss_W"], "loss_split_W": c["loss_split_W"],
                                   "torque_in_Nm": c["torque_in_Nm"], "torque_out_Nm": c["torque_out_Nm"],
                                   "efficiency": c["efficiency"], "checks": ch}
            bad = [x["name"] for x in ch if x["ok"] is False and not x["name"].startswith("interface")]
            if bad:
                row["violations"] = list(row["violations"]) + bad
                if row["ok"]:
                    row["ok"], row["binding"] = False, bad[0]
        except OutOfValidity as exc:
            why = str(exc).split(":")[0]
            row = {"rpm": float(r), "ok": False, "binding": why, "violations": [why], "detail": str(exc),
                   "current_A": None, "converged": None}
        cache[k] = row
        return row

    rows = [f(lo + (hi - lo) * i / (n - 1)) for i in range(n)]
    for _ in range(2):                                   # refine every ok / not-ok edge by bisection
        allr = sorted(cache.values(), key=lambda r: r["rpm"])
        for a, b in zip(allr, allr[1:]):
            if b["rpm"] - a["rpm"] > tol_rpm and a["ok"] != b["ok"]:
                s1._bisect_edge(f, a["rpm"], b["rpm"], tol_rpm)
    allr = sorted(cache.values(), key=lambda r: r["rpm"])
    bands = s1._bands(allr)
    first_fail = next((i for i, r in enumerate(allr) if not r["ok"]), None)
    monotone = first_fail is None or not any(r["ok"] for r in allr[first_fail:])
    limit: Optional[Dict[str, Any]] = None
    if bands:
        top_ok = max(i for i, r in enumerate(allr) if r["ok"])
        best = allr[top_ok]
        nxt = allr[top_ok + 1] if top_ok < len(allr) - 1 else None
        limit = {"motor_rpm": best["rpm"], "output_rpm": best["output_rpm"],
                 "thrust_N": best["thrust_N"], "motor_shaft_power_W": best["shaft_power_W"],
                 "electrical_power_W": best["electrical_power_W"], "current_A": best["current_A"],
                 "T_winding_C": best["T_winding_C"], "T_magnet_C": best["T_magnet_C"],
                 "transmission_loss_W": best["transmission"]["loss_W"],
                 "transmission_efficiency": best["transmission"]["efficiency"],
                 "binding": nxt["binding"] if nxt else "speed_range",
                 "violations_above": (nxt or {}).get("violations") or [],
                 "lower_bound": nxt is None, "extrapolated": best["extrapolated"],
                 "power_estimated": best["power_estimated"]}
    return {
        "schema": SCHEMA, "kind": "s1", "transmission_id": transmission.id if transmission else None,
        "load_id": load.id, "die": model.get("die"), "config": model.get("config"),
        "environment": {"ambient_c": ambient_c, "altitude_m": altitude_m, "rho_kg_m3": rho, "airspeed_ms": airspeed_ms},
        "rows": rows, "refined": [{"rpm": r["rpm"], "ok": r["ok"], "binding": r["binding"]} for r in allr
                                  if not any(r is x for x in rows)],
        "bands": [list(b) for b in bands], "monotone": monotone, "limit": limit,
        "no_continuous_point": limit is None,
        "first_failure": None if first_fail is None else allr[first_fail]["binding"],
        "assumptions": ["motor physics, losses, voltage and thermal network: propeller_s1.solve_point (unchanged)",
                        "load as seen at the motor shaft: transmission loss maps over the INPUT side",
                        "transmission heat is checked against its own rating, not added to the motor thermal network",
                        "motoring only"],
    }
