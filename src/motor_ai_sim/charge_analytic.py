"""Analytic BOOST-CHARGING of a generator, from its passport.

The Python twin of ``web/src/lib/generatorCharge.ts``, restricted to the
machine AS BUILT — the base winding, base stack, base connection — because
that is the machine a datasheet describes.  (The Configure tab's version also
rescales the winding; the two agree exactly at the base knobs, which is what
the validation harness checks.)

Everything is read from the passport's own measurements:

    T(I)          the current sweep          (saturation included)
    Pfe, Pmag     the I x rpm loss grid      (bilinear)
    cuAC          the same grid's copper factor -> proximity watts
    dP_*(rpm)     the PWM block              (only when pwm=True)
    V1            Vemf0 x rpm + the loaded drop, the passport's own law

and then the charge arithmetic is three lines:

    P_charge = |T|*omega - P_loss                     (IDEAL BRIDGE)
    V_bus    = (V_oc + sqrt(V_oc^2 + 4*P*R_pack)) / 2 (exact fixed point)
    I_charge = P_charge / V_bus

with three ways to be infeasible, and the caller is told which: the bus cannot
synthesise the fundamental (modulation), the pack will not take the current
(current), or the bus would rise past the pack's ceiling (pack).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

M_LIMIT = 1.15          # linear modulation with 3rd-harmonic injection

_R_INT_DEFAULT_MOHM: Dict[str, float] = {
    "nmc": 12.0, "nca": 12.0, "lco": 15.0,
    "lifepo4": 8.0, "lfp": 8.0, "lto": 6.0,
}


def _interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Linear interpolation, end-slope extrapolation, floored at 0 — the same
    rule motorScaling.ts uses, so the two sides cannot drift."""
    n = len(xs)
    if n == 0:
        return 0.0
    if n == 1:
        return float(ys[0])
    if x <= xs[0]:
        s = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return max(0.0, ys[0] + s * (x - xs[0]))
    if x >= xs[n - 1]:
        s = (ys[n - 1] - ys[n - 2]) / (xs[n - 1] - xs[n - 2])
        return max(0.0, ys[n - 1] + s * (x - xs[n - 1]))
    i = 1
    while i < n and xs[i] < x:
        i += 1
    t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[i - 1] + t * (ys[i] - ys[i - 1])


def _clamp_interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    if not xs:
        return 0.0
    if len(xs) == 1 or x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    return _interp(xs, ys, x)


def pack_from_spec(b: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A pack as the DC link sees it — mirrors simulation/battery.py's
    ``pack_from_config`` (including which defaults are placeholders)."""
    if not b:
        return None
    ph: Dict[str, str] = {}
    chem = b.get("chemistry")
    ns = max(1, int(b.get("cells") or 1))
    npar = max(1, int(b.get("n_parallel") or 1))
    if b.get("n_parallel") is None:
        ph["n_parallel"] = "assumed 1 string"
    r_int = b.get("r_int_mohm")
    if not r_int:
        r_int = _R_INT_DEFAULT_MOHM.get(str(chem or "").strip().lower(), 12.0)
        ph["r_int_mohm"] = "placeholder for %s — not measured" % (chem or "unknown",)
    cap = b.get("capacity_ah")
    if not cap:
        cap = 10.0
        ph["capacity_ah"] = "placeholder — not derivable from a voltage spec"
    i_max = b.get("i_charge_max_a")
    if not i_max:
        i_max = float(cap) * npar
        ph["i_charge_max_a"] = "placeholder — 1 C of the (placeholder) capacity"
    v_oc = b.get("v_nom")
    if not v_oc:
        lo, hi = b.get("v_min"), b.get("v_max")
        if lo and hi:
            v_oc = 0.5 * (float(lo) + float(hi))
            ph["v_oc"] = "midpoint of v_min/v_max — the pack carries no nominal"
        else:
            return None
    else:
        ph["v_oc"] = "pack nominal (no state-of-charge model)"
    return {
        "v_oc_V": float(v_oc), "cells": ns, "n_parallel": npar,
        "r_int_mohm": float(r_int),
        "R_pack_ohm": ns * float(r_int) * 1e-3 / npar,
        "capacity_ah": float(cap) * npar,
        "i_charge_max_A": float(i_max),
        "v_max_V": float(b.get("v_max") or 0.0),
        "chemistry": chem, "placeholders": ph,
    }


def _k_end(pp: Dict[str, Any]) -> float:
    """3-D end-effect factor at the passport's own stack length (1.0 when the
    machine has no Stage-A measurement — the honest 2-D answer)."""
    e3 = pp.get("end3d") or {}
    m = e3.get("k_flux_vs_L") or {}
    if m:
        pts = sorted((float(a), float(b)) for a, b in m.items())
        L0 = float(pp.get("L0_mm") or 0.0)
        if pts:
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            return _clamp_interp(xs, ys, L0)
    return float(e3.get("k_flux") or 1.0)


def machine_point(pp: Dict[str, Any], I: float, rpm: float, *,
                  pwm: bool = False,
                  f_sw_Hz: Optional[float] = None) -> Dict[str, float]:
    """Torque, losses and terminal fundamental of the BASE machine at (I, rpm).

    The base-winding restriction of motorScaling.scaleMotor(): fN = fL = fH =
    fConn = 1, so every factor there collapses and what is left is the
    passport's own measured curves.
    """
    cur = pp.get("current") or {}
    lg = pp.get("loss_grid") or {}
    I0 = float(pp.get("I0_A") or 0.0)
    rpm0 = float(pp.get("rpm0") or 0.0)
    R0 = float(pp.get("R0_ohm") or 0.0)
    k3 = _k_end(pp)

    if cur.get("I_A") and len(cur["I_A"]) >= 3:
        T = _interp([float(x) for x in cur["I_A"]],
                    [abs(float(x)) for x in cur["T_Nm"]], I) * k3
    else:
        T = abs(float(pp.get("T0_Nm") or 0.0)) * (I / I0 if I0 else 1.0) * k3

    def grid(rows: List[List[float]]) -> float:
        Is = [float(x) for x in lg["I_A"]]
        rs = [float(x) for x in lg["rpm"]]
        by_i = [_interp(rs, [float(v) for v in rows[r]], rpm)
                for r in range(len(Is))]
        return _interp(Is, by_i, I)

    if lg.get("I_A") and lg.get("rpm"):
        P_fe = grid(lg["Pfe_W"])
        P_mag = grid(lg["Pmag_W"])
        prox = 0.0
        if lg.get("cuAC"):
            rows = []
            for r, Ir in enumerate(lg["I_A"]):
                dc = 3.0 * float(Ir) ** 2 * R0
                rows.append([max(0.0, dc * (float(a) - 1.0))
                             for a in lg["cuAC"][r]])
            prox = max(0.0, grid(rows))
    else:
        f = (rpm / rpm0) if rpm0 else 1.0
        P_fe = float(pp.get("Pfe0_W") or 0.0) * f ** 1.5
        P_mag = float(pp.get("Pmag0_W") or 0.0) * f * f
        prox = 0.0
    P_cu = 3.0 * I * I * R0 + prox

    d_mag = d_fe = d_cu = 0.0
    if pwm:
        d = pwm_deltas(pp, rpm, I, f_sw_Hz=f_sw_Hz)
        d_mag, d_fe, d_cu = d["dP_mag_W"], d["dP_fe_W"], d["dP_cu_ac_W"]

    # terminal fundamental — the passport's own voltage law at base winding
    fRpm = (rpm / rpm0) if rpm0 else 1.0
    fI = (I / I0) if I0 else 1.0
    Vemf = float(pp.get("Vemf0_peak_V") or 0.0) * fRpm * k3
    vl0 = float(pp.get("Vload0_peak_V") or 0.0)
    ve0 = float(pp.get("Vemf0_peak_V") or 0.0)
    drop0 = (vl0 - ve0) if vl0 > ve0 else 0.0
    Vdrop = drop0 * fI * fRpm if drop0 > 0 else R0 * I * math.sqrt(2.0)
    return {
        "T_Nm": T,
        "P_mech_W": T * 2.0 * math.pi * rpm / 60.0,
        "P_cu_W": P_cu + d_cu, "P_fe_W": P_fe + d_fe, "P_mag_W": P_mag + d_mag,
        "P_loss_W": P_cu + P_fe + P_mag + d_cu + d_fe + d_mag,
        "V1_peak_V": Vemf + Vdrop,
        "dP_mag_W": d_mag, "dP_fe_W": d_fe, "dP_cu_ac_W": d_cu,
    }


def pwm_deltas(pp: Dict[str, Any], rpm: float, I: float, *,
               f_sw_Hz: Optional[float] = None) -> Dict[str, float]:
    """The measured carrier deltas at the BASE winding — the Python twin of
    motorScaling.ts pwmDeltas() with every winding factor equal to 1, so the
    only law left is the ripple current's ∝ 1/f_sw."""
    b = pp.get("pwm") or {}
    pts = b.get("points") or []
    if not pts:
        return {"dP_mag_W": 0.0, "dP_fe_W": 0.0, "dP_cu_ac_W": 0.0,
                "I_dc_ripple_pp_A": 0.0, "ripple_pct": 0.0,
                "I_ripple_A": 0.0, "extrapolated": False}
    f_ref = float(b.get("f_sw_ref_Hz") or (b.get("f_sw_Hz") or [0])[0])
    ref = [q for q in pts if abs(float(q["f_sw_Hz"]) - f_ref) < 1e-6] or pts
    f_sw = float(f_sw_Hz or f_ref)
    fit = b.get("fit") or {}
    env = b.get("envelope") or {}
    rip_ref = float((fit.get("ref") or {}).get("I_ripple_A")
                    or env.get("I_ripple_max_A") or 0.0)
    rr_raw = f_ref / max(1e-9, f_sw)
    raw = rip_ref * rr_raw
    hi = float(env.get("I_ripple_max_A") or rip_ref) * 1.5
    lo = float(env.get("I_ripple_min_A") or rip_ref) / 1.5
    used = min(hi, max(lo, raw)) if rip_ref > 0 else raw
    rr = (used / rip_ref) if rip_ref > 0 else rr_raw
    extrap = bool(
        f_sw < float(env.get("f_sw_min_Hz") or f_ref) * 0.999
        or f_sw > float(env.get("f_sw_max_Hz") or f_ref) * 1.001
        or rpm < float(env.get("rpm_min") or 0.0) * 0.999
        or rpm > float(env.get("rpm_max") or 1e18) * 1.001
        or (rip_ref > 0 and (raw > hi or raw < lo)))

    def field(key: str) -> float:
        Is = sorted({float(q["I_A"]) for q in ref})
        by_i = []
        for Iv in Is:
            row = sorted((q for q in ref if abs(float(q["I_A"]) - Iv) < 1e-9),
                         key=lambda q: float(q["rpm"]))
            by_i.append(_clamp_interp([float(q["rpm"]) for q in row],
                                      [float(q.get(key) or 0.0) for q in row],
                                      rpm))
        return _clamp_interp(Is, by_i, I)

    def rip_delta() -> float:
        Is = sorted({float(q["I_A"]) for q in ref})
        by_i = []
        for Iv in Is:
            row = sorted((q for q in ref if abs(float(q["I_A"]) - Iv) < 1e-9),
                         key=lambda q: float(q["rpm"]))
            by_i.append(_clamp_interp(
                [float(q["rpm"]) for q in row],
                [float(q.get("ripple_pwm_pct") or 0.0)
                 - float(q.get("ripple_sine_pct") or 0.0) for q in row], rpm))
        return _clamp_interp(Is, by_i, I)

    def p(n: float) -> float:
        return math.pow(max(1e-9, rr), float(n))

    return {
        "dP_mag_W": field("dP_mag_W") * p(fit.get("n_mag", 2.0)),
        "dP_fe_W": field("dP_fe_W") * p(fit.get("n_fe", 2.0)),
        "dP_cu_ac_W": field("dP_cu_ac_W") * p(fit.get("n_cu", 2.0)),
        "I_dc_ripple_pp_A": (field("I_dc_ripple_pp_A")
                             * p(fit.get("n_dc_ripple", 1.0))),
        "ripple_pct": (float(pp.get("ripple0_pct") or 0.0)
                       + rip_delta() * p(fit.get("n_ripple", 1.0))),
        "I_ripple_A": used,
        "extrapolated": extrap,
    }


def max_current(pp: Dict[str, Any]) -> float:
    """Largest phase current the BASE machine may be swept to — the twin of
    motorScaling.maxCurrent() at base knobs.

    Two bounds: the conductor (the base operating point with 25 % headroom, the
    same convention the tuner's slider uses) and the passport's own MEASURED
    demagnetisation knee, where Br retention crosses 99.5 %.  The current sweep
    deliberately runs PAST that knee — that is how it locates it — so a
    max-charge search without this bound would happily win at a current that
    permanently weakens the magnets.
    """
    I0 = float(pp.get("I0_A") or 0.0)
    wire = I0 * 1.25
    cur = pp.get("current") or {}
    xs = [float(x) for x in (cur.get("I_A") or [])]
    keep = [float(x) for x in (cur.get("demag_keep_pct") or [])]
    if len(xs) >= 2 and len(keep) == len(xs):
        i_dm = xs[-1]                 # never crossed inside the data
        for i in range(1, len(xs)):
            if keep[i] < 99.5 <= keep[i - 1]:
                t = (99.5 - keep[i - 1]) / (keep[i] - keep[i - 1])
                i_dm = xs[i - 1] + t * (xs[i] - xs[i - 1])
                break
        return min(wire, i_dm)
    return wire


def charge_at(pp: Dict[str, Any], batt: Optional[Dict[str, Any]],
              I: float, rpm: float, *, pwm: bool = False,
              f_sw_Hz: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Charge state at one point, or None when there is nothing to charge."""
    pack = pack_from_spec(batt)
    if not pack:
        return None
    m = machine_point(pp, I, rpm, pwm=pwm, f_sw_Hz=f_sw_Hz)
    P = m["P_mech_W"] - m["P_loss_W"]
    R = pack["R_pack_ohm"]
    voc = pack["v_oc_V"]
    disc = voc * voc + 4.0 * P * R
    v_bus = 0.5 * (voc + math.sqrt(disc)) if disc > 0 else voc
    I_ch = (P / v_bus) if v_bus > 1e-9 else 0.0
    mi = (2.0 * m["V1_peak_V"] / v_bus) if v_bus > 1e-9 else float("inf")
    limited = "none"
    if not (mi <= M_LIMIT):
        limited = "modulation"
    elif pack["i_charge_max_A"] > 0 and I_ch > pack["i_charge_max_A"]:
        limited = "current"
    elif pack["v_max_V"] > 0 and v_bus > pack["v_max_V"]:
        limited = "pack"
    return {
        "I_A": I, "rpm": rpm,
        "T_Nm": m["T_Nm"], "P_mech_W": m["P_mech_W"],
        "P_loss_W": m["P_loss_W"], "P_cu_W": m["P_cu_W"],
        "P_fe_W": m["P_fe_W"], "P_mag_W": m["P_mag_W"],
        "P_charge_W": P, "I_charge_A": I_ch,
        "V_oc_V": voc, "V_bus_V": v_bus, "V_rise_V": v_bus - voc,
        "C_rate": (abs(I_ch) / pack["capacity_ah"]
                   if pack["capacity_ah"] > 1e-9 else None),
        "eta_charge": (max(0.0, P) / m["P_mech_W"]
                       if m["P_mech_W"] > 1.0 else None),
        "R_pack_ohm": R, "P_pack_r_loss_W": I_ch * I_ch * R,
        "V1_peak_V": m["V1_peak_V"], "modulation_index": mi,
        "V1_max_peak_V": 0.5 * M_LIMIT * v_bus,
        "limited_by": limited, "charging": bool(P > 0.0),
        "pwm": bool(pwm), "pack": pack,
        "dP_mag_W": m["dP_mag_W"], "dP_fe_W": m["dP_fe_W"],
        "dP_cu_ac_W": m["dP_cu_ac_W"],
    }


def max_charge(pp: Dict[str, Any], batt: Optional[Dict[str, Any]], rpm: float,
               *, pwm: bool = False, f_sw_Hz: Optional[float] = None,
               steps: int = 40) -> Optional[Dict[str, Any]]:
    """The most charge power any current in the passport's own MEASURED sweep
    range reaches at this speed, with every limit held.  Outside that range the
    torque is an extrapolation, and a max-charge answer on an extrapolated
    torque is a guess with a number attached."""
    cur = pp.get("current") or {}
    Is = [float(x) for x in (cur.get("I_A") or [])]
    if len(Is) >= 2:
        lo, hi = min(Is), max(Is)
    else:
        I0 = float(pp.get("I0_A") or 0.0)
        lo, hi = 0.25 * I0, 1.5 * I0
    cap = max_current(pp)
    lo, hi = min(lo, cap), min(hi, cap)
    best = None
    blocker = "none"
    n = max(4, int(steps))
    for i in range(n + 1):
        I = lo + (hi - lo) * i / n
        if I <= 0:
            continue
        c = charge_at(pp, batt, I, rpm, pwm=pwm, f_sw_Hz=f_sw_Hz)
        if c is None:
            return None
        if c["limited_by"] != "none":
            if blocker == "none":
                blocker = c["limited_by"]
            continue
        if not c["charging"]:
            continue
        if best is None or c["P_charge_W"] > best["P_charge_W"]:
            best = c
    if best is not None:
        best = dict(best, blocked_beyond=blocker)
    return best


def charge_map(pp: Dict[str, Any], batt: Optional[Dict[str, Any]], *,
               pwm: bool = False, f_sw_Hz: Optional[float] = None,
               n: int = 13) -> List[Dict[str, Any]]:
    """P_charge(rpm) at the max-charge current across the passport's own
    measured speed range — the boost-mode interpolation, as datasheet rows."""
    lg = pp.get("loss_grid") or {}
    rs = [float(x) for x in (lg.get("rpm")
                             or (pp.get("speed") or {}).get("rpm")
                             or [pp.get("rpm0") or 0.0])]
    lo, hi = min(rs), max(rs)
    out: List[Dict[str, Any]] = []
    m = max(2, int(n))
    for i in range(m):
        rpm = lo + (hi - lo) * i / (m - 1)
        b = max_charge(pp, batt, rpm, pwm=pwm, f_sw_Hz=f_sw_Hz, steps=24)
        if b is not None:
            out.append({"rpm": rpm, "I_A": b["I_A"],
                        "P_charge_W": b["P_charge_W"],
                        "I_charge_A": b["I_charge_A"],
                        "V_bus_V": b["V_bus_V"], "C_rate": b["C_rate"],
                        "eta_charge": b["eta_charge"],
                        "limited_by": b.get("blocked_beyond", "none")})
        else:
            c = charge_at(pp, batt, float(pp.get("I0_A") or 0.0), rpm,
                          pwm=pwm, f_sw_Hz=f_sw_Hz)
            out.append({"rpm": rpm, "I_A": None,
                        "P_charge_W": min(0.0, (c or {}).get("P_charge_W", 0.0)),
                        "I_charge_A": (c or {}).get("I_charge_A", 0.0),
                        "V_bus_V": (c or {}).get("V_bus_V", 0.0),
                        "C_rate": (c or {}).get("C_rate"),
                        "eta_charge": (c or {}).get("eta_charge"),
                        "limited_by": (c or {}).get("limited_by", "none")})
    return out
