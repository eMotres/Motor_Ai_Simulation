"""The conversions between a bench measurement and the model value of the SAME quantity at the SAME conditions.

Pure functions, no I/O, no FastAPI: the routes, the report and the tests all call these.  Every function documents the
physics it applies; the UI shows the one-line version of it in a tooltip (``TIPS``).

Conventions
-----------
* Resistance in ohm inside, mOhm on screen; inductance in henry inside where noted, uH on screen.
* ``delta_pct = (model - measured) / measured * 100``: a negative delta = the model reads LOW.
* Connection: ``star`` or ``delta`` (the configuration's winding ``star_delta``).
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

#: Copper: alpha = 0.00393 1/K about 20 C, i.e. R(T) / R(T0) = (234.5 + T) / (234.5 + T0).
CU_T0 = 234.5
#: Annealed copper (IACS) at 20 C, ohm * mm^2 / m.
RHO_CU_20 = 0.017241

#: The one-line explanations the UI puts in a tooltip (EN source; the web carries the zh mirror).
TIPS = {
    "cogging": ("no-load cogging peak-to-peak: simulation = the configuration's stored cogging block (I = 0, eddy and "
                "demagnetisation off, magnets at the temperature the block states); bench = break-away by hand through a "
                "torque meter in both directions, friction taken out"),
    "resistance": ("simulated R_phase at its coil temperature, scaled to the measured temperature by (234.5 + T), x2 line-to-line "
                   "in star (2/3 in delta), plus the stated lead cables; the phase board and the joints are not in the simulation"),
    "ldq_sum": "3-pair mean of L line-line = Ld + Lq (star); simulation = the incremental Ld/Lq of the lowest-current duty",
    "ldq_amp": "3-pair amplitude sqrt(2/3 * sum dev^2) = |Lq - Ld|; the pair order is not known, so the smaller fitted value is compared with the smaller simulated value",
    "l_rotor_out": "stator without rotor: compared with a stored stator-only run when one exists",
    "l_freq": "the simulation is low-frequency incremental; at kHz the eddy currents in iron and magnets lower the bench reading",
    "emf_peak": ("simulated V_LL peak of the no-load duty, x speed ratio, at the test magnet temperature: a stored no-load "
                 "point within 10 K is compared directly; else the EMF-to-Br elasticity fitted from this configuration's own "
                 "no-load points at other temperatures; else Br(test magnet T) / Br(simulation magnet T), labelled "
                 "approximate; x the end-effect correction of the configuration (\"not corrected yet\" when it has none); "
                 "measured = Vpp / 2"),
    "emf_rms": "simulated V_LL rms of the no-load duty, same speed, temperature basis and correction",
    "loss": ("simulation = core (hysteresis ~n, eddy ~n^2, excess ~n^1.5) + solid eddy (~n^2) of the no-load duty, x (Br ratio)^2, "
             "+ bearings (SKF) + windage; the rest of the measured power is unexplained"),
    "fit": "least squares P = a*n + b*n^2 through the origin (n in krpm)",
    "bearings": ("simulation values, not a measurement: SKF friction of the bearings assigned to the configuration, the rotor "
                 "weight as radial load, at the bench speed and temperature; the only measured figure is the total free-run "
                 "loss, in which the bearings are not separated"),
    "kv_kt": ("slope of V_LL peak against speed (least squares through the origin, over the rows named in the group); "
              "Kv = 1000 / slope in rpm per V of line-line peak of the FUNDAMENTAL; Kt = 3 * Ke per A rms of phase current "
              "(Ke = phase rms EMF per rad/s; current on the q axis, sine EMF); the simulated values carry the end-effect correction"),
    "load": ("the duty within 2 % speed and 3 % current; simulated torque x the torque factor (measured k_T, else k_flux); "
             "simulated efficiency is the motor's at the corrected shaft power (no inverter)"),
    "eff_system": ("torque * omega / (V_dc * I_dc) includes the inverter: compared only with the system efficiency of the "
                   "stored duty (inverter x motor), never with the motor's"),
    "weight": "simulated mass of the configuration's parts; the bench weighs what the record names",
}


# ---------------------------------------------------------------------------
# small numerics
# ---------------------------------------------------------------------------

def num(v: Any) -> Optional[float]:
    """A finite float or None (bools, strings that are not numbers, NaN/inf -> None)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def delta_pct(model: Optional[float], measured: Optional[float]) -> Optional[float]:
    m, x = num(model), num(measured)
    if m is None or x is None or x == 0:
        return None
    return (m - x) / x * 100.0


def mean(values: Iterable[Any]) -> Optional[float]:
    xs = [v for v in (num(x) for x in values) if v is not None]
    return sum(xs) / len(xs) if xs else None


# ---------------------------------------------------------------------------
# resistance
# ---------------------------------------------------------------------------

def cu_temp_factor(t_to_c: float, t_from_c: float) -> float:
    """R(t_to) / R(t_from) for copper: (234.5 + t_to) / (234.5 + t_from)."""
    return (CU_T0 + float(t_to_c)) / (CU_T0 + float(t_from_c))


def awg_area_mm2(awg: float) -> float:
    """Cross-section of one solid AWG conductor: d = 0.127 mm * 92 ** ((36 - n) / 39)."""
    d = 0.127 * 92.0 ** ((36.0 - float(awg)) / 39.0)
    return math.pi / 4.0 * d * d


def lead_resistance_ohm(length_mm: float, *, awg: Optional[float] = None, area_mm2: Optional[float] = None,
                        temp_c: float = 20.0) -> Optional[float]:
    """One copper lead of ``length_mm`` at ``temp_c``: rho20 * L / A * (234.5 + T) / 254.5.  None without a section."""
    a = num(area_mm2)
    if a is None and num(awg) is not None:
        a = awg_area_mm2(float(awg))
    L = num(length_mm)
    if a is None or a <= 0 or L is None or L <= 0:
        return None
    return RHO_CU_20 * (L / 1000.0) / a * cu_temp_factor(temp_c, 20.0)


def line_line_factor(connection: str) -> float:
    """Terminal-to-terminal over one phase: two windings in series (star) or one in parallel with two (delta)."""
    return 2.0 / 3.0 if str(connection or "star").lower().startswith("d") else 2.0


def resistance_compare(pairs_ohm: Sequence[Any], temp_c: float, *, r_phase_ohm: float, coil_temp_c: float,
                       connection: str = "star", leads: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Measured line-to-line resistance (mean of the pairs) against the model at the measured temperature.

    model = k_LL * R_phase(coil_temp) * (234.5 + T) / (234.5 + coil_temp) + 2 * R_lead(T)
    (k_LL = 2 in star, 2/3 in delta; a line-to-line reading runs through two of the leads).
    """
    measured = mean(pairs_ohm)
    winding = line_line_factor(connection) * float(r_phase_ohm) * cu_temp_factor(temp_c, coil_temp_c)
    lead = None
    if leads:
        one = lead_resistance_ohm(num(leads.get("length_mm")) or 0.0, awg=num(leads.get("awg")),
                                  area_mm2=num(leads.get("area_mm2")), temp_c=temp_c)
        lead = None if one is None else 2.0 * one
    model = winding + (lead or 0.0)
    xs = [v for v in (num(x) for x in pairs_ohm) if v is not None]
    spread = (100.0 * (max(xs) - min(xs)) / measured) if (xs and measured) else None
    return {"measured_ohm": measured, "winding_ohm": winding, "leads_ohm": lead, "model_ohm": model,
            "delta_pct": delta_pct(model, measured), "spread_pct": spread, "temp_c": float(temp_c),
            "remaining_ohm": (None if measured is None else measured - model),
            "not_modelled": ["phase board", "joints / crimps"]}


# ---------------------------------------------------------------------------
# inductance
# ---------------------------------------------------------------------------

def ldq_fit_from_pairs(l_pairs: Sequence[Any]) -> Optional[Dict[str, float]]:
    """Ld, Lq from the three line-to-line readings of a star machine.

    L_LL(theta) = (Ld + Lq) - (Lq - Ld) * cos(2 theta + phi): the three pairs sit 120 deg apart in 2 theta, so
    mean = Ld + Lq and amplitude = sqrt(2/3 * sum (L_i - mean)^2) = |Lq - Ld|.  The smaller fitted value is called
    Ld (the order of the pairs on the rotor is not known).
    """
    xs = [v for v in (num(x) for x in l_pairs) if v is not None]
    if len(xs) != 3:
        return None
    m = sum(xs) / 3.0
    amp = math.sqrt(2.0 / 3.0 * sum((x - m) ** 2 for x in xs))
    return {"sum": m, "amp": amp, "ld": (m - amp) / 2.0, "lq": (m + amp) / 2.0}


def model_ldq_line(ld: float, lq: float, connection: str = "star") -> Tuple[float, float]:
    """The (Ld, Lq) a line-to-line bench reading sees: the winding's own in star, the star equivalent (/3) in delta."""
    k = 1.0 / 3.0 if str(connection or "star").lower().startswith("d") else 1.0
    return float(ld) * k, float(lq) * k


# ---------------------------------------------------------------------------
# magnets
# ---------------------------------------------------------------------------

def br_at(points: Sequence[Tuple[float, float]], temp_c: float) -> Optional[float]:
    """Br at ``temp_c`` from the grade's cards ``[(T, Br), ...]``: linear between cards, the end slope beyond them.

    One card only: that Br (no temperature information) - the caller then says the scaling is 1.
    """
    pts = sorted((float(t), float(b)) for t, b in points if num(t) is not None and num(b) is not None)
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    t = float(temp_c)
    if t <= pts[0][0]:
        (t0, b0), (t1, b1) = pts[0], pts[1]
    elif t >= pts[-1][0]:
        (t0, b0), (t1, b1) = pts[-2], pts[-1]
    else:
        for (t0, b0), (t1, b1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                break
    if t1 == t0:
        return b0
    return b0 + (b1 - b0) * (t - t0) / (t1 - t0)


def br_ratio(points: Sequence[Tuple[float, float]], test_temp_c: Optional[float], model_temp_c: Optional[float]) -> float:
    """Br(test) / Br(model); 1.0 when either temperature or the cards are missing."""
    if num(test_temp_c) is None or num(model_temp_c) is None:
        return 1.0
    a, b = br_at(points, float(test_temp_c)), br_at(points, float(model_temp_c))
    if not a or not b:
        return 1.0
    return a / b


#: A stored no-load point within this many kelvin of the bench magnet temperature is compared DIRECTLY (speed scaling only,
#: no temperature scaling).
EMF_DIRECT_TEMP_K = 10.0
#: Stored no-load points closer than this (K) are one temperature (averaged in the fit).
EMF_SAME_TEMP_K = 0.5
#: The stored no-load points must span at least this many kelvin for their EMF-to-Br elasticity to be fitted.
EMF_FIT_MIN_SPAN_K = 10.0
#: A fitted elasticity outside this range is not physical (EMF falls as Br rises, or rises faster than Br with linear
#: iron at most 1): it is refused and the row falls back to the Br scaling, labelled approximate.
EMF_ELASTICITY_RANGE = (0.0, 1.2)

#: How the simulated EMF of a free-run row was brought to the bench magnet temperature (``emf_temperature_basis``).
TEMP_STORED_POINT = "stored_point"        # (a) a stored no-load point within EMF_DIRECT_TEMP_K, compared directly
TEMP_FITTED = "fitted"                    # (b) the elasticity fitted from this configuration's own no-load points
TEMP_BR_SCALED = "br_scaled"              # (c) EMF taken proportional to Br: temperature-scaled (approximate)
TEMP_NO_BENCH_T = "no_bench_temp"         # the bench row has no magnet temperature: nothing to bring it to


def _emf_value(r: Dict[str, Any], key: str) -> Optional[float]:
    v, n = num(r.get(key)), num(r.get("rpm"))
    return v / n if (v and n) else None


def _emf_per_rpm_by_temp(refs: Sequence[Dict[str, Any]]) -> Tuple[List[Tuple[float, float]], str]:
    """``([(magnet temperature, V_LL per rpm), ...], key)`` of the stored no-load points, sorted by temperature, points within
    ``EMF_SAME_TEMP_K`` averaged.  The fundamental when every point stores one, else the waveform peak: one curve never
    mixes the two."""
    good = [r for r in refs if num(r.get("rpm")) and num(r.get("magnet_temp_c")) is not None]
    key = "v_ll_fund_peak" if good and all(num(r.get("v_ll_fund_peak")) for r in good) else "v_ll_peak"
    pts = sorted((float(r["magnet_temp_c"]), _emf_value(r, key)) for r in good if _emf_value(r, key))
    groups: List[List[Tuple[float, float]]] = []
    for p in pts:
        if groups and p[0] - groups[-1][0][0] <= EMF_SAME_TEMP_K:
            groups[-1].append(p)
        else:
            groups.append([p])
    return [(sum(t for t, _ in g) / len(g), sum(e for _, e in g) / len(g)) for g in groups], key


def fit_emf_elasticity(refs: Sequence[Dict[str, Any]], br_points: Sequence[Tuple[float, float]]) -> Optional[Dict[str, Any]]:
    """The elasticity d ln(EMF) / d ln(Br) of THIS configuration, least squares over its own stored no-load points at
    different magnet temperatures (Br(T) from the grade's cards).  ``None`` when there are fewer than two temperatures, they
    span less than ``EMF_FIT_MIN_SPAN_K``, or the cards give no Br at them.  ``{"elasticity", "n", "span_k", "key",
    "accepted"}``; ``accepted`` False = outside ``EMF_ELASTICITY_RANGE`` (not applied).  Nothing is assumed: a saturated
    rotor moves its EMF much less than Br, and how much is the machine's own number."""
    table, key = _emf_per_rpm_by_temp(refs)
    if len(table) < 2 or table[-1][0] - table[0][0] < EMF_FIT_MIN_SPAN_K:
        return None
    xs, ys = [], []
    for t, e in table:
        b = br_at(br_points, t)
        if not b or b <= 0 or e <= 0:
            return None
        xs.append(math.log(b))
        ys.append(math.log(e))
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    el = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    lo, hi = EMF_ELASTICITY_RANGE
    return {"elasticity": el, "n": len(table), "span_k": table[-1][0] - table[0][0], "key": key,
            "accepted": lo <= el <= hi}


def emf_temperature_basis(rpm: float, refs: Sequence[Dict[str, Any]], br_points: Sequence[Tuple[float, float]],
                          test_temp_c: Optional[float]) -> Optional[Dict[str, Any]]:
    """Which stored no-load point a bench free-run row at ``rpm`` and ``test_temp_c`` is compared with, and by what factor
    its EMF is moved to the bench magnet temperature.  In order of preference:

    (a) ``stored_point``: a stored no-load point within ``EMF_DIRECT_TEMP_K`` of the bench temperature (the nearest in
        temperature, then in speed) - compared directly, factor 1 (speed scaling only);
    (b) ``fitted``: else, with two or more stored points of this configuration at different temperatures, the point nearest
        in speed x (Br(test) / Br(point)) ** elasticity, the elasticity fitted from those points (``fit_emf_elasticity``);
    (c) ``br_scaled``: else the point nearest in speed x Br(test) / Br(point) - EMF taken proportional to Br, which a
        saturated rotor does not follow: temperature-scaled (approximate), and labelled so.

    A row without a bench temperature is ``no_bench_temp`` (the point nearest in speed, factor 1).  ``None`` = no stored
    no-load point.  Returns ``{"ref", "factor", "basis", "model_temp_c", "br_ratio", "elasticity", "fit"}``."""
    near_speed = noload_reference(rpm, refs)
    if near_speed is None:
        return None
    t = num(test_temp_c)
    base = {"elasticity": None, "fit": None}
    if t is None:
        return {**base, "ref": near_speed, "factor": 1.0, "basis": TEMP_NO_BENCH_T,
                "model_temp_c": num(near_speed.get("magnet_temp_c")), "br_ratio": 1.0}
    n = float(rpm)
    timed = [r for r in refs if num(r.get("rpm")) and num(r.get("magnet_temp_c")) is not None]
    close = [r for r in timed if abs(float(r["magnet_temp_c"]) - t) <= EMF_DIRECT_TEMP_K]
    if close:
        best_dt = min(abs(float(r["magnet_temp_c"]) - t) for r in close)
        tied = [r for r in close if abs(float(r["magnet_temp_c"]) - t) <= best_dt + EMF_SAME_TEMP_K]
        ref = min(tied, key=lambda r: (abs(math.log(n / float(r["rpm"]))), abs(float(r["magnet_temp_c"]) - t)))
        return {**base, "ref": ref, "factor": 1.0, "basis": TEMP_STORED_POINT,
                "model_temp_c": float(ref["magnet_temp_c"]), "br_ratio": 1.0}
    ratio = br_ratio(br_points, t, near_speed.get("magnet_temp_c"))
    fit = fit_emf_elasticity(timed, br_points)
    if fit and fit["accepted"] and num(near_speed.get("magnet_temp_c")) is not None:
        return {"ref": near_speed, "factor": ratio ** fit["elasticity"], "basis": TEMP_FITTED, "model_temp_c": t,
                "br_ratio": ratio, "elasticity": fit["elasticity"], "fit": fit}
    return {"ref": near_speed, "factor": ratio, "basis": TEMP_BR_SCALED, "model_temp_c": t, "br_ratio": ratio,
            "elasticity": None, "fit": fit}


def emf_model_at(rpm: float, ref: Dict[str, Any], ratio: float = 1.0) -> Dict[str, Optional[float]]:
    """V_LL waveform peak, rms and FUNDAMENTAL peak at ``rpm`` from a no-load reference point: EMF is proportional to speed;
    ``ratio`` is the temperature factor (``emf_temperature_basis``).  ``fund`` is None for an older stored run that carries
    no fundamental (the waveform peak then stands in for it, flagged by the caller)."""
    r0 = num(ref.get("rpm"))
    if not r0:
        return {"peak": None, "rms": None, "fund": None}
    k = float(rpm) / r0 * float(ratio)
    pk, rms, fund = num(ref.get("v_ll_peak")), num(ref.get("v_ll_rms")), num(ref.get("v_ll_fund_peak"))
    return {"peak": None if pk is None else pk * k, "rms": None if rms is None else rms * k,
            "fund": None if fund is None else fund * k}


# ---------------------------------------------------------------------------
# EMF slope, Kv, Ke, Kt
# ---------------------------------------------------------------------------

#: A bench row whose V/n differs from the median of the OTHER rows by more than this (percent) is flagged (shown, never
#: dropped); at least ``SLOPE_FLAG_MIN_ROWS`` rows are needed for the others to mean anything.
SLOPE_FLAG_PCT = 3.0
SLOPE_FLAG_MIN_ROWS = 4


def summary_magnet_temp_c(s: Optional[Dict[str, Any]]) -> Optional[float]:
    """The magnet temperature a stored EM run was SOLVED at, from its summary, or None.

    ``magnet_temp_used_c`` is the run's own stamp (``routes.simulation._magnet_temperature_stamp``: the typed or coupled
    temperature, else its card's); ``magnet_temp_c`` / ``magnet_temp_C`` are the older spellings.  The first one present
    wins; 0 C is a temperature, not "missing"."""
    s = s if isinstance(s, dict) else {}
    for key in ("magnet_temp_used_c", "magnet_temp_c", "magnet_temp_C"):
        v = num(s.get(key))
        if v is not None:
            return v
    return None


def slope_through_origin(points: Sequence[Tuple[Any, Any]]) -> Optional[Dict[str, float]]:
    """Least squares V = s * n through the origin (EMF is zero at standstill): ``{"slope", "n", "rmse_pct"}`` with the
    slope in V per rpm.  At least two points at a positive speed; None otherwise (one point is a ratio, not a slope)."""
    pts = [(float(n), float(v)) for n, v in points if num(n) is not None and num(v) is not None and float(n) > 0]
    if len(pts) < 2:
        return None
    sxx = sum(n * n for n, _ in pts)
    s = sum(n * v for n, v in pts) / sxx
    if s <= 0:
        return None
    rmse = math.sqrt(sum((v - s * n) ** 2 for n, v in pts) / len(pts))
    mean_v = sum(v for _, v in pts) / len(pts)
    return {"slope": s, "n": float(len(pts)), "rmse_pct": 100.0 * rmse / mean_v if mean_v else float("nan")}


def kv_ke_kt(slope_peak_v_per_krpm: float) -> Dict[str, float]:
    """The speed / torque constants from the slope of the line-line PEAK of the FUNDAMENTAL EMF against speed.

    * ``kv`` = 1000 / slope (rpm per V of line-line peak of the fundamental);
    * ``ke`` = phase rms EMF per mechanical rad/s = slope_peak / 1000 * (30 / pi) / sqrt(3) / sqrt(2)  [V s/rad];
    * ``kt`` = :func:`motor_ai_sim.kv_kt.kt_from_kv` (= 3 * ke), N m per A rms of phase current, sine current on the q axis.
    """
    from motor_ai_sim import kv_kt as K
    sp = float(slope_peak_v_per_krpm)
    kv = 1000.0 / sp
    ke = sp / 1000.0 * (30.0 / math.pi) / math.sqrt(3.0) / math.sqrt(2.0)
    return {"slope_peak": sp, "kv": kv, "ke": ke, "kt": K.kt_from_kv(kv)}


def flag_slope_outliers(points: Sequence[Tuple[Any, Any]], *, pct: float = SLOPE_FLAG_PCT,
                        min_rows: int = SLOPE_FLAG_MIN_ROWS) -> List[Dict[str, Any]]:
    """For each ``(rpm, V)`` row: ``{"rpm", "dev_pct", "flagged"}``, ``dev_pct`` = how far its V/n sits from the median V/n of
    the OTHER rows (percent, negative = below).  Rows are only flagged - never removed here; with fewer than
    ``min_rows`` usable rows nothing is flagged (``dev_pct`` None)."""
    pts = [(i, float(n), float(v)) for i, (n, v) in enumerate(points)
           if num(n) is not None and num(v) is not None and float(n) > 0]
    out: List[Dict[str, Any]] = [{"rpm": num(n), "dev_pct": None, "flagged": False} for n, _ in points]
    if len(pts) < min_rows:
        return out
    for i, n, v in pts:
        others = sorted(vv / nn for j, nn, vv in pts if j != i)
        k = len(others)
        med = others[k // 2] if k % 2 else 0.5 * (others[k // 2 - 1] + others[k // 2])
        if med > 0:
            dev = 100.0 * ((v / n) / med - 1.0)
            out[i] = {"rpm": n, "dev_pct": dev, "flagged": abs(dev) > pct}
    return out


# ---------------------------------------------------------------------------
# no-load loss
# ---------------------------------------------------------------------------

def noload_em_loss_at(rpm: float, ref: Dict[str, Any], ratio: float = 1.0) -> Optional[Dict[str, float]]:
    """Electromagnetic no-load loss at ``rpm`` scaled from a no-load reference point at ``ref['rpm']``.

    core  = hysteresis * r + eddy * r^2 + excess * r^1.5     (r = n / n0; P_core * r^1.5 when the split is not stored)
    solid = P_solid * r^2                                     (resistance-limited eddy in magnets, shaft, sleeve)
    both  x ratio^2                                           (loss ~ B^2, B ~ Br)
    """
    r0 = num(ref.get("rpm"))
    if not r0:
        return None
    r = float(rpm) / r0
    hy, ed, ex = num(ref.get("hyst")), num(ref.get("eddy")), num(ref.get("excess"))
    if hy is not None or ed is not None or ex is not None:
        core = (hy or 0.0) * r + (ed or 0.0) * r * r + (ex or 0.0) * r ** 1.5
    else:
        pc = num(ref.get("p_core"))
        if pc is None:
            return None
        core = pc * r ** 1.5
    solid = (num(ref.get("p_solid")) or 0.0) * r * r
    b2 = float(ratio) ** 2
    return {"core": core * b2, "solid": solid * b2, "em": (core + solid) * b2}


def fit_an_bn2(points: Sequence[Tuple[float, float]]) -> Optional[Dict[str, float]]:
    """Least squares P = a*n + b*n^2 through the origin, n in krpm (a in W/krpm, b in W/krpm^2)."""
    pts = [(float(n) / 1000.0, float(p)) for n, p in points if num(n) is not None and num(p) is not None and float(n) > 0]
    if len(pts) < 2:
        return None
    s2 = sum(n ** 2 for n, _ in pts)
    s3 = sum(n ** 3 for n, _ in pts)
    s4 = sum(n ** 4 for n, _ in pts)
    t1 = sum(n * p for n, p in pts)
    t2 = sum(n * n * p for n, p in pts)
    det = s2 * s4 - s3 * s3
    if abs(det) < 1e-12:
        return None
    a = (t1 * s4 - t2 * s3) / det
    b = (s2 * t2 - s3 * t1) / det
    rmse = math.sqrt(sum((p - a * n - b * n * n) ** 2 for n, p in pts) / len(pts))
    return {"a": a, "b": b, "rmse": rmse, "n": float(len(pts))}


# ---------------------------------------------------------------------------
# loads
# ---------------------------------------------------------------------------

def match_duty(rpm: Optional[float], current: Optional[float], duties: Sequence[Dict[str, Any]], *,
               rpm_tol: float = 0.02, cur_tol: float = 0.03) -> Optional[Dict[str, Any]]:
    """The duty at the nearest operating point within ``rpm_tol`` speed and ``cur_tol`` current, or None."""
    n, i = num(rpm), num(current)
    if n is None or i is None or n <= 0:
        return None
    best, best_err = None, None
    for d in duties:
        dn, di = num(d.get("rpm")), num(d.get("current"))
        if not dn or di is None or di <= 0:
            continue
        en, ei = abs(n - dn) / dn, abs(i - di) / di
        if en > rpm_tol or ei > cur_tol:
            continue
        err = en / rpm_tol + ei / cur_tol
        if best_err is None or err < best_err:
            best, best_err = d, err
    return best


def noload_reference(rpm: float, refs: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The no-load reference nearest in speed (log distance), or None."""
    n = num(rpm)
    good = [r for r in refs if num(r.get("rpm"))]
    if n is None or n <= 0 or not good:
        return None
    return min(good, key=lambda r: abs(math.log(n / float(r["rpm"]))))


def mech_loss_at(fn: Optional[Callable[[float, Optional[float]], Optional[Dict[str, Any]]]], rpm: float,
                 temp_c: Optional[float]) -> Optional[Dict[str, Any]]:
    """Call the injected bearings + windage function; a failure is "not available", never zero."""
    if fn is None:
        return None
    try:
        return fn(float(rpm), temp_c)
    except Exception:                                       # noqa: BLE001
        return None
