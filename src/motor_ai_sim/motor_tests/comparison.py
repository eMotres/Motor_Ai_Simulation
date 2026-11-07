"""One test record + the model source -> the measured | model | delta table (one table per test, grouped).

Shape (what the web and the report read)::

    {"test_id", "kind", "groups": [
        {"key", "label", "params", "tip", "rows": [
            {"key", "label", "params", "unit", "measured", "model", "delta_pct", "tip",
             "status": "ok" | "no_model" | "info", "extra": {...}, "action": {...} | None}]}],
     "fit": {...} | None,
     "series": {"x": "rpm", "quantities": [{"key", "label", "unit", "tip", "points": [
         {"rpm", "current", "measured", "model", "delta_pct"}]}]} | None}     # load records only: every row at once

``key`` / ``params`` are what the web translates (EN + zh); ``label`` is the EN text the report prints.  ``tip`` names a
``compare.TIPS`` entry; ``detail`` (EN) says which duty / temperature the model value came from.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from motor_ai_sim.motor_tests import compare as C
from motor_ai_sim.motor_tests.compare import num


def _row(key: str, label: str, unit: str, measured: Any = None, model: Any = None, *, tip: Optional[str] = None,
         params: Optional[Dict[str, Any]] = None, status: Optional[str] = None, detail: str = "",
         extra: Optional[Dict[str, Any]] = None, action: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    m, x = num(model), num(measured)
    st = status or ("ok" if (m is not None and x is not None) else ("no_model" if x is not None else "info"))
    return {"key": key, "label": label, "params": params or {}, "unit": unit, "measured": x, "model": m,
            "delta_pct": C.delta_pct(m, x), "tip": tip, "detail": detail, "status": st,
            "extra": extra or {}, "action": action}


def _group(key: str, label: str, rows: List[Dict[str, Any]], *, params: Optional[Dict[str, Any]] = None,
           tip: Optional[str] = None, basis: Optional[str] = None, basis_key: Optional[str] = None,
           basis_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``basis`` = the one line (EN) saying what the model values of the group are (temperature, correction);
    ``basis_key`` / ``basis_params`` are what the web translates (``tests.basis.<key>``)."""
    return {"key": key, "label": label, "params": params or {}, "tip": tip, "basis": basis, "basis_key": basis_key,
            "basis_params": basis_params or {}, "rows": rows}


def _k3(model: Dict[str, Any], what: str = "flux") -> tuple:
    """``(k, clause, source)``: the end-effect correction factor a simulation value carries and the words for the detail.

    ``flux`` = voltages / EMF (Stage A k_flux); ``torque`` = torque, Kt and the shaft power (the measured k_T, else
    k_flux).  The configuration has no factor: ``(None, "not corrected yet", None)`` - the uncorrected value stays and
    says so (the one exception to "one simulation value", owner 2026-11-04)."""
    f = model.get("end3d") or {}
    k = f.get("k_flux") if what == "flux" else f.get("k_torque")
    if k is None:
        return None, "not corrected yet", None
    if what == "flux":
        src = f.get("source") or "Stage A"
    elif f.get("torque_basis") == "3-D":
        src = "measured k_T"
    else:
        src = "Stage A flux factor"
    clause = "corrected"
    if f.get("inherited"):
        src += ", earlier geometry"
        clause += ", factor of an earlier geometry"
    return float(k), clause, src


def _scaled(value: Any, k: Optional[float]) -> Optional[float]:
    v = num(value)
    return None if v is None else (v if k is None else v * k)


#: A no-load point is stored with this terminal current: the duty API refuses 0 A, and anything at or below
#: ``model_source.NOLOAD_MAX_A`` is read back as a no-load point (the older bench duties carry 0.1 A the same way).
NOLOAD_CURRENT_A = 0.1


def gamma_for(model: Optional[Dict[str, Any]], current: float) -> tuple:
    """``(gamma_deg, source)``: the current angle a new bench point is solved at.

    In order, from the configuration's own stored operating data (nothing is invented):

    1. a no-load point (<= 0.5 A) has no torque current, so 0 deg;
    2. the stored duty NEAREST in current (log distance; the configuration's ordinary duties first, its test duties only
       when it has no other) - the MTPA angle moves with the current, so the nearest point is the best copy;
    3. the configuration's own ``simulation.gamma_deg`` / default-duty angle;
    4. 0 deg.

    ``source`` is a short EN phrase for the row tooltip."""
    cur = num(current)
    if cur is None or cur <= 0.5:
        return 0.0, "no-load point"
    duties = [d for d in (model or {}).get("duties") or []
              if isinstance(d, dict) and num(d.get("gamma_deg")) is not None
              and (num(d.get("current")) or 0) > 0.5]
    ordinary = [d for d in duties if str(d.get("kind") or "") != "test"]
    best, best_err = None, None
    for d in (ordinary or duties):
        err = abs(math.log(cur / float(d["current"])))
        if best_err is None or err < best_err:
            best, best_err = d, err
    if best is not None:
        return float(best["gamma_deg"]), "duty '%s' (%g A)" % (best.get("name"), float(best["current"]))
    sim = num((model or {}).get("sim_gamma_deg"))
    if sim is not None:
        return float(sim), "the configuration's own angle"
    return 0.0, "default 0 deg"


def action_key(rpm: Any, current: Any) -> tuple:
    """The point a bench row stands for, for de-duplication: rpm rounded to 10, current rounded to 0.5 A."""
    n, i = num(rpm), num(current)
    if n is None or i is None:
        return (None, None)
    return (int(round(n / 10.0)) * 10, round(round(i * 2.0) / 2.0, 1))


def _create_duty(die: str, config: str, *, rpm: float, current: float, mode: str = "motor",
                 name: Optional[str] = None, model: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The action that adds the missing operating point and solves it (``POST /api/motor-tests/{die}/{config}/compute``,
    admin only).  ``body`` is the duty to create (``POST /api/family/duty``'s shape); ``gamma_deg`` comes from the
    configuration's own stored duties (:func:`gamma_for`), ``gamma_source`` says where from."""
    cur = float(current)
    nm = name or (f"test {int(round(rpm))} rpm" + (" no-load" if cur <= 0.5 else f" {cur:g} A"))
    gamma, src = gamma_for(model, cur)
    return {"kind": "create_duty", "gamma_source": src,
            "body": {"die": die, "config": config,
                     # a bench point, not a product duty: never a preset or a public card row
                     "duty": {"name": nm[:60], "mode": mode, "rpm": round(float(rpm), 1),
                              "current_arms": round(max(cur, NOLOAD_CURRENT_A) if cur <= 0.5 else cur, 2),
                              "gamma_deg": round(gamma, 2), "kind": "test"}}}


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------

def motor_mass(doc: Dict[str, Any], model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The mass power and torque per mass are taken on (owner 2026-11-07): the whole motor as weighed on the bench, newest
    first; else the whole motor from CAD; else the active parts only, and the basis says which."""
    tests = sorted(doc.get("tests") or [], key=lambda t: str(t.get("date") or ""), reverse=True)
    for rec in tests:
        if str((rec.get("conditions") or {}).get("rotor") or "") == "out":
            continue
        for row in rec.get("weight") or []:
            kg = num(row.get("mass_g"))
            if kg and "motor" in str(row.get("what") or "").lower():
                what = str(row.get("what") or "").lower()
                return {"kg": kg / 1000.0, "basis": "stated" if "not weighed" in what else "weighed"}
    mass = model.get("mass") or {}
    if num(mass.get("full_kg")) and not mass.get("partial"):
        return {"kg": float(mass["full_kg"]), "basis": "cad"}
    if num(mass.get("active_kg")):
        return {"kg": float(mass["active_kg"]), "basis": "active"}
    return None


def _weight_row(p: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    """The bench weight against the model's WHOLE-motor mass (owner 2026-10-27).

    The bench weighs the whole motor; ``summary.mass_total_kg`` is the ACTIVE parts only (CIANO20 200_50: 9,400 g on the
    bench against 7,334 g, -22 % in red).  The model is ``motor_mass.mass_summary``: its full mass when it is complete
    (the CAD bill of materials, or the configuration's stated CAD mass), compared with a delta; when it is partial the
    active mass is shown as "active parts only" with the reason, and NO delta - two different things are not compared."""
    ms = model.get("mass") if isinstance(model.get("mass"), dict) else None
    what = p.get("what")
    if ms is not None and not ms.get("partial") and num(ms.get("full_kg")) is not None:
        src = str(ms.get("source") or "")
        words = {"cad_stated": "Simulation: Fusion CAD mass" + (" (%s)" % ms["cad_source"] if ms.get("cad_source") else ""),
                 "cad_bom": "Simulation: full motor, CAD bill of materials"}.get(src, "Simulation: full motor mass")
        return _row("weight", "Weight: %s" % what, "g", p.get("mass_g"), 1000.0 * float(ms["full_kg"]), tip="weight",
                    params={"what": what, "model_scope": "full"}, detail=words,
                    extra={"mass_source": src, "mass_scope": "full"})
    akg = num((ms or {}).get("active_kg"))
    if akg is None:
        akg = num(model.get("mass_kg"))
    if ms is not None or akg is not None:
        why = str((ms or {}).get("partial_reason") or "no CAD mass for this configuration")
        r = _row("weight", "Weight: %s" % what, "g", p.get("mass_g"), None if akg is None else 1000.0 * akg,
                 tip="weight", params={"what": what, "model_scope": "active"}, status="info",
                 detail="Simulation: active parts only - %s; no delta, the bench weighs the whole motor" % why,
                 extra={"mass_source": str((ms or {}).get("source") or "active_only"), "mass_scope": "active"})
        r["delta_pct"] = None
        return r
    return _row("weight", "Weight: %s" % what, "g", p.get("mass_g"), None, tip="weight", params={"what": what})


def _resistance(rec: Dict[str, Any], model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    r = rec.get("resistance")
    if not isinstance(r, dict) or not r.get("pairs"):
        return None
    cond = rec.get("conditions") or {}
    t = num(r.get("temp_c"))
    if t is None:
        t = num(cond.get("winding_temp_c"))
    if t is None:
        t = num(cond.get("ambient_c"))
    pairs = [p for p in r["pairs"] if num(p.get("value_mohm")) is not None]
    rows: List[Dict[str, Any]] = []
    ref = model.get("r_ref")
    leads = rec.get("leads")
    meas_ohm = [float(p["value_mohm"]) / 1000.0 for p in pairs]
    if ref and t is not None:
        res = C.resistance_compare(meas_ohm, t, r_phase_ohm=ref["r_phase_ohm"], coil_temp_c=ref["coil_temp_c"],
                                   connection=ref.get("connection") or model.get("connection") or "star", leads=leads)
        detail = (f"duty '{ref['duty']}': R_phase {ref['r_phase_ohm'] * 1000:.3f} mOhm at {ref['coil_temp_c']:g} C -> "
                  f"{t:g} C")
        rows.append(_row("r_ll_mean", "R line-line, mean of %d" % len(pairs), "mOhm", res["measured_ohm"] * 1000,
                         res["model_ohm"] * 1000, tip="resistance", params={"n": len(pairs)}, detail=detail,
                         extra={"winding_mohm": res["winding_ohm"] * 1000,
                                "leads_mohm": None if res["leads_ohm"] is None else res["leads_ohm"] * 1000,
                                "remaining_mohm": None if res["remaining_ohm"] is None else res["remaining_ohm"] * 1000,
                                "temp_c": t, "not_modelled": res["not_modelled"]}))
        rows.append(_row("r_winding", "winding only (simulation)", "mOhm", None, res["winding_ohm"] * 1000,
                         tip="resistance", status="info"))
        if res["leads_ohm"] is not None:
            rows.append(_row("r_leads", "leads (2 in series)", "mOhm", None, res["leads_ohm"] * 1000,
                             tip="resistance", status="info"))
    else:
        rows.append(_row("r_ll_mean", "R line-line, mean of %d" % len(pairs), "mOhm", C.mean(meas_ohm) * 1000,
                         None, tip="resistance", params={"n": len(pairs)},
                         detail="no stored R_phase" if not ref else "no measurement temperature"))
    for p in pairs:
        rows.append(_row("r_pair", "R %s" % p.get("pair", "?"), "mOhm", p["value_mohm"], None,
                         params={"pair": p.get("pair", "?")}, status="info"))
    return _group("resistance", "Resistance at %s C" % ("?" if t is None else f"{t:g}"), rows,
                  params={"temp_c": t}, tip="resistance",
                  basis=("R_phase at %g C scaled to %s C" % (ref["coil_temp_c"], f"{t:g}") if (ref and t is not None)
                         else None),
                  basis_key="resistance" if (ref and t is not None) else None,
                  basis_params=({"from": f"{ref['coil_temp_c']:g}", "to": f"{t:g}"} if (ref and t is not None) else None))


def _inductance(rec: Dict[str, Any], model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ind = rec.get("inductance")
    if not isinstance(ind, dict) or not ind.get("pairs"):
        return None
    cond = rec.get("conditions") or {}
    rotor_out = cond.get("rotor") == "out"
    pairs = [p for p in ind["pairs"] if num(p.get("value_uh")) is not None]
    vals = [float(p["value_uh"]) for p in pairs]
    freq = num(ind.get("freq_hz"))
    rows: List[Dict[str, Any]] = []
    freq_flag = {"freq_hz": freq, "high_freq": bool(freq and freq > 100.0)}
    if rotor_out:
        ref = model.get("l_stator_only")
        meas = C.mean(vals)
        if ref:
            ld, lq = C.model_ldq_line(ref["ld_mh"], ref["lq_mh"], ref.get("connection") or "star")
            rows.append(_row("l_mean_rotor_out", "L line-line, mean (no rotor)", "uH", meas, (ld + lq) * 1000,
                             tip="l_rotor_out", detail=f"duty '{ref['duty']}'", extra=freq_flag))
        else:
            rows.append(_row("l_mean_rotor_out", "L line-line, mean (no rotor)", "uH", meas, None,
                             tip="l_rotor_out", detail="no stator-only run stored", extra=freq_flag))
    else:
        fit = C.ldq_fit_from_pairs(vals)
        ref = model.get("l_ref")
        if fit:
            if ref:
                ld, lq = C.model_ldq_line(ref["ld_mh"], ref["lq_mh"], ref.get("connection") or "star")
                lo, hi = sorted((ld * 1000.0, lq * 1000.0))
                det = (f"duty '{ref['duty']}'" + (f" at {ref['current']:g} A" if ref.get("current") is not None else "")
                       + f", {ref.get('method') or 'stored'} Ld/Lq")
            else:
                lo = hi = None
                det = "no stored Ld/Lq"
            rows.append(_row("l_sum", "Ld + Lq (3-pair mean)", "uH", fit["sum"], None if lo is None else lo + hi,
                             tip="ldq_sum", detail=det, extra=freq_flag))
            rows.append(_row("l_amp", "|Lq - Ld| (3-pair amplitude)", "uH", fit["amp"], None if lo is None else hi - lo,
                             tip="ldq_amp", detail=det, extra=freq_flag))
            rows.append(_row("l_d", "Ld (fitted)", "uH", fit["ld"], lo, tip="ldq_amp", detail=det, extra=freq_flag))
            rows.append(_row("l_q", "Lq (fitted)", "uH", fit["lq"], hi, tip="ldq_amp", detail=det, extra=freq_flag))
        else:
            rows.append(_row("l_mean", "L line-line, mean", "uH", C.mean(vals), None, tip="ldq_sum",
                             detail="three pairs are needed for the Ld/Lq fit", extra=freq_flag))
    for p in pairs:
        rows.append(_row("l_pair", "L %s" % p.get("pair", "?"), "uH", p["value_uh"], None,
                         params={"pair": p.get("pair", "?")}, status="info"))
    return _group("inductance", "Inductance" + (f" at {freq:g} Hz" if freq else "") + (", no rotor" if rotor_out else ""),
                  rows, params={"freq_hz": freq, "rotor": "out" if rotor_out else "in"},
                  tip="l_freq" if freq_flag["high_freq"] else "ldq_sum",
                  basis="no end-winding inductance", basis_key="inductance")


def _cogging(rec: Dict[str, Any], model: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """No-load cogging peak-to-peak: the bench value (or a pending entry) against the configuration's stored cogging
    block (``cogging.py``).  Only on a record that names the cogging check - a stator-only record has no rotor."""
    cg = rec.get("cogging")
    if not isinstance(cg, dict):
        return None
    ref = model.get("cogging") or {}
    m = num(ref.get("pkpk_nm"))
    det = ""
    if ref:
        det = "configuration cogging block: magnets %s C, cycle order %s, dominant order %s" % (
            "?" if ref.get("magnet_temp_c") is None else f"{ref['magnet_temp_c']:g}", ref.get("fundamental_order"),
            ref.get("dominant_order"))
    rows = [_row("cogging_pkpk", "Cogging torque, peak-to-peak", "N*m", cg.get("pkpk_nm"), m, tip="cogging",
                 detail=det or "no cogging stored on the configuration",
                 status=("info" if cg.get("pkpk_nm") is None else None),
                 extra={"pending": bool(cg.get("pending")), "bench_magnet_temp_c": cg.get("magnet_temp_c"),
                        "model_magnet_temp_c": ref.get("magnet_temp_c")})]
    return _group("cogging", "No-load cogging", rows, tip="cogging",
                  basis="I = 0, eddy and demagnetisation off, no skew", basis_key="cogging")


def _info_groups(rec: Dict[str, Any], model: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    lcr = rec.get("lcr") or []
    if lcr:
        rows = [_row("lcr", "%s %s %s" % (p.get("pair") or "", p.get("quantity") or "", p.get("mode") or ""),
                     str(p.get("unit") or ""), p.get("value"), None, status="info",
                     params={"pair": p.get("pair") or "", "quantity": p.get("quantity") or "", "mode": p.get("mode") or "",
                             "freq_hz": p.get("freq_hz"), "level_v": p.get("level_v")}) for p in lcr]
        out.append(_group("lcr", "LCR", rows))
    rows = []
    for p in rec.get("insulation") or []:
        rows.append(_row("insulation", "Insulation at %g V" % p["voltage_v"], "MOhm", p.get("resistance_mohm"), None,
                         status="info", params={"voltage_v": p["voltage_v"], "more_than": bool(p.get("more_than"))}))
    for p in rec.get("hipot") or []:
        rows.append(_row("hipot", ("Hipot %g V %s" % (p["voltage_v"], str(p.get("supply") or "").upper())).strip(), "mA",
                         p.get("leakage_ma"), None, status="info",
                         params={"voltage_v": p["voltage_v"], "time_s": p.get("time_s"), "supply": p.get("supply")}))
    for p in rec.get("surge") or []:
        rows.append(_row("surge", "Surge %g V: %s" % (p["voltage_v"], p.get("result")), "", None, None, status="info",
                         params={"voltage_v": p["voltage_v"], "result": p.get("result")}))
    if rows:
        out.append(_group("insulation", "Insulation", rows))
    rows = []
    for p in rec.get("weight") or []:
        rows.append(_weight_row(p, model))
    for p in rec.get("dimensions") or []:
        rows.append(_row("dimension", "%s" % p.get("what"), "mm", p.get("value_mm"), p.get("nominal_mm"),
                         params={"what": p.get("what")}, detail="nominal = the drawing value entered with the record"))
    if rows:
        out.append(_group("mass", "Weight and dimensions", rows))
    return out


# ---------------------------------------------------------------------------
# free-run
# ---------------------------------------------------------------------------

def _row_magnet_temp(row: Dict[str, Any], rec: Dict[str, Any], rows: List[Dict[str, Any]]) -> tuple:
    """The magnet temperature a free-run row is scaled to, and where it came from."""
    if num(row.get("magnet_temp_c")) is not None:
        return float(row["magnet_temp_c"]), "row"
    cond = rec.get("conditions") or {}
    if num(cond.get("magnet_temp_c")) is not None:
        return float(cond["magnet_temp_c"]), "record"
    pts = sorted((float(r["rpm"]), float(r["temp_c"])) for r in rows
                 if num(r.get("temp_c")) is not None and num(r.get("rpm")) is not None)
    n = num(row.get("rpm"))
    if pts and n is not None:
        if n <= pts[0][0]:
            return pts[0][1], "measured T"
        if n >= pts[-1][0]:
            return pts[-1][1], "measured T"
        for (n0, t0), (n1, t1) in zip(pts, pts[1:]):
            if n0 <= n <= n1:
                return (t0 + (t1 - t0) * (n - n0) / (n1 - n0) if n1 > n0 else t0), "measured T"
    if num(cond.get("ambient_c")) is not None:
        return float(cond["ambient_c"]), "ambient"
    return None, "unknown"


def _fmt_rpms(rpms: List[float]) -> str:
    return ", ".join("%d" % round(x) for x in rpms)


_BENCH_SOURCE_TEXT = {
    "fundamental": "bench voltage: fundamental, line-line peak",
    "rms": "bench voltage: rms reading x sqrt(2)",
    "pp": "bench voltage: half the peak-to-peak (includes the harmonics, approximate)",
}

#: The bench voltage the Kv / Kt slope is fitted to, in order of preference: the fundamental as read (peak), the rms reading
#: times sqrt(2) (a sine's peak), half the peak-to-peak of the waveform (includes the harmonics, so approximate).
BENCH_SOURCES = ("fundamental", "rms", "pp")


def _bench_voltage(r: Dict[str, Any], source: str) -> Optional[float]:
    if source == "fundamental":
        return r.get("fund")
    if source == "rms":
        return None if r.get("rms") is None else float(r["rms"]) * math.sqrt(2.0)
    return r.get("pk")


def _pick_bench_source(rows: List[Dict[str, Any]]) -> Optional[str]:
    """The first source in :data:`BENCH_SOURCES` that at least two rows carry (a slope needs two); one source per record, so
    the fit never mixes a fundamental with a waveform peak."""
    for src in BENCH_SOURCES:
        if sum(1 for r in rows if _bench_voltage(r, src) is not None) >= 2:
            return src
    return None


def _slope_fit(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bench and simulated line-line EMF slopes (V per 1000 rpm, least squares through the origin) over ``rows``.

    The bench slope is on the voltage ``r["v"]`` the caller chose (:func:`_pick_bench_source`): the peak of the fundamental
    (or its stand-ins); the simulated slope on ``m_pk``.  No rms constant is ever shown: one Kv, per V of line-line peak of
    the fundamental."""
    def sl(pts):
        f = C.slope_through_origin(pts)
        return None if f is None else {**f, "krpm": 1000.0 * f["slope"]}
    return {"b_pk": sl([(r["rpm"], r["v"]) for r in rows if r.get("v") is not None]),
            "m_pk": sl([(r["rpm"], r["m_pk"]) for r in rows if r["m_pk"] is not None])}


def _kv_rows(fit: Dict[str, Any], rows: List[Dict[str, Any]], model: Dict[str, Any],
             excluded: List[float]) -> List[Dict[str, Any]]:
    """The Kv / Kt rows of one slope fit: bench | simulation (corrected), with the difference from the bench.

    Correction: the flux factor multiplies the EMF slope (so Kv, a reciprocal, divides by it); Kt carries the torque factor
    (measured k_T, else the flux factor), exactly as the load rows do."""
    b_pk, m_pk = fit["b_pk"], fit["m_pk"]
    if b_pk is None:
        return []
    b = C.kv_ke_kt(b_pk["krpm"])
    md = None if m_pk is None else C.kv_ke_kt(m_pk["krpm"])
    f3 = model.get("end3d") or {}
    k_flux, k_t = f3.get("k_flux"), f3.get("k_torque")
    k_t = k_t if k_t is not None else k_flux
    flux_clause = _k3(model, "flux")[1]
    torque_clause = _k3(model, "torque")[1]
    used = [r["rpm"] for r in rows]
    prov = {"k_flux": k_flux, "k_torque": k_t, "torque_basis": f3.get("torque_basis"), "source": f3.get("source"),
            "inherited": bool(f3.get("inherited")), "inherited_note": f3.get("inherited_note"), "date": f3.get("date"),
            "current": f3.get("current", True), "stale": bool(f3.get("stale"))}
    base = {"n_rows": len(rows), "rpms": used, "excluded_rpm": excluded, "scatter_pct": b_pk["rmse_pct"],
            "bench_slope_v_per_krpm": b["slope_peak"], "bench_ke": b["ke"], "end3d": prov,
            "model_ke": None if md is None else md["ke"]}
    det_b = "bench: %d rows (%s rpm), line through the origin, scatter %.1f %%" % (
        len(rows), _fmt_rpms(used) if len(used) <= 8 else "%d-%d" % (round(min(used)), round(max(used))),
        b_pk["rmse_pct"])
    src = rows[0].get("src") if rows else None
    det_b += "; " + _BENCH_SOURCE_TEXT.get(src, "bench voltage: line-line peak")
    m_src = next((r.get("m_src") for r in rows if r.get("m_src")), None)
    if m_src == "waveform peak":
        det_b += "; simulation from the waveform peak (no fundamental stored)"
    if excluded:
        det_b += "; left out: %s rpm" % _fmt_rpms(excluded)
    out: List[Dict[str, Any]] = []

    def add(key: str, label: str, unit: str, bench_v: float, model_name: str, factor: Optional[float],
            divide: bool, clause: str, more: Optional[Dict[str, Any]] = None) -> None:
        m2 = None if md is None else md[model_name]
        corrected = factor is not None and m2 is not None
        mv = m2 if not corrected else (m2 / float(factor) if divide else m2 * float(factor))
        x = {**base, **(more or {}), "corrected": factor is not None}
        out.append(_row(key, label, unit, bench_v, mv, tip="kv_kt", detail=det_b + "; simulation, " + clause, extra=x,
                        status=None if m2 is not None else "no_model"))

    add("kv_slope", "EMF slope, V_LL peak per 1000 rpm", "V/krpm", b["slope_peak"], "slope_peak", k_flux, False, flux_clause)
    add("kv", "Kv (rpm per V line-line peak)", "rpm/V", b["kv"], "kv", k_flux, True, flux_clause)
    delta = str(model.get("connection") or "star").lower().startswith("d")
    add("kt", "Kt per A rms phase current", "N·m/A", b["kt"], "kt", k_t, False, torque_clause,
        {"connection": "delta" if delta else "star", "winding_a_factor": math.sqrt(3.0) if delta else 1.0})
    return out


def _kv_kt_groups(slope_in: List[Dict[str, Any]], emf_rows: List[Dict[str, Any]], model: Dict[str, Any],
                  mag_p: str) -> List[Dict[str, Any]]:
    """Kv and Kt from the SLOPE of the line-line EMF against speed - never from one point (owner 2026-11-04).

    Group 1 uses every row that carries a voltage.  A row whose V/n sits more than ``compare.SLOPE_FLAG_PCT`` from the
    median of the others is FLAGGED on its EMF row (never dropped); group 2, shown only then, repeats the fit without the
    flagged rows, so both slopes are always reported and the exclusion is visible.  The bench constants need no model;
    the model columns come from the same rows (each scaled to its own magnet temperature)."""
    source = _pick_bench_source(slope_in)
    if source is None:
        return []
    for r in slope_in:
        r["v"] = _bench_voltage(r, source)
        r["src"] = source
    rows = [r for r in slope_in if r["v"] is not None]
    pk_rows = rows
    flags = C.flag_slope_outliers([(r["rpm"], r["v"]) for r in pk_rows])
    flag_of = {id(r): f for r, f in zip(pk_rows, flags)}
    flagged = [r for r in pk_rows if flag_of[id(r)]["flagged"]]
    # which rows entered the slope, and how far each sits from the others, on the EMF rows themselves
    for r in rows:
        f = flag_of.get(id(r))
        row = emf_rows[r["i"]]
        row["extra"].update({"in_slope": True, "slope_dev_pct": None if f is None else f["dev_pct"],
                             "slope_flagged": bool(f and f["flagged"])})
        if f and f["flagged"]:
            row["detail"] = (row["detail"] + "; " if row["detail"] else "") + \
                "V/n %+.1f %% from the median of the other rows (flagged; still in the Kv / Kt fit)" % f["dev_pct"]
    k_flux, k_clause, k_src = _k3(model, "flux")
    kp = {"mag": mag_p, "k": None if k_flux is None else "%.3f" % k_flux, "src": k_src or ""}
    out: List[Dict[str, Any]] = []
    rows_all = _kv_rows(_slope_fit(rows), rows, model, excluded=[])
    if not rows_all:
        return []
    out.append(_group("kv_kt", "Kv / Kt from the EMF slope, all %d rows" % len(rows), rows_all, params={"n": len(rows)},
                      tip="kv_kt", basis="all %d rows, slope through the origin, magnets at %s C, %s" % (
                          len(rows), mag_p, k_clause),
                      basis_key="kv_3d" if k_flux is not None else "kv_2d", basis_params={"n": len(rows), **kp}))
    keep = [r for r in rows if r not in flagged]
    if flagged and len(keep) >= 2:
        ex = [r["rpm"] for r in flagged]
        rows_ex = _kv_rows(_slope_fit(keep), keep, model, excluded=ex)
        if rows_ex:
            out.append(_group("kv_kt_ex", "Kv / Kt without %s rpm (flagged)" % _fmt_rpms(ex), rows_ex,
                              params={"rpm": _fmt_rpms(ex)}, tip="kv_kt",
                              basis="flagged rows left out, %d rows, magnets at %s C, %s" % (len(keep), mag_p, k_clause),
                              basis_key="kv_ex_3d" if k_flux is not None else "kv_ex_2d",
                              basis_params={"n": len(keep), "rpm": _fmt_rpms(ex), **kp}))
    return out


def _bearing_groups(rows_in: List[Dict[str, Any]], rec: Dict[str, Any], model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The Bearings tab of a free-run test: ONE group, the MODEL's total loss of all bearings at each bench speed.

    Values come from ``bearings.machine_bearing_losses`` (SKF friction, rotor weight as radial load) through the injected
    ``mech`` function; the group is labelled from the cards ("Bearings, 2 x CARD (simulation)" when every end uses the same
    card, else the card names joined).  It is a model value, never a measured bearing loss (the measured total stays in
    the Free-run tab).  A configuration without bearings gets the empty state - no number is made up."""
    rows = [r for r in rows_in if num(r.get("rpm"))]
    if not rows:
        return []
    if not (model.get("has_bearings") and model.get("mech")):
        return [_group("bearings_none", "Bearings", [
            _row("no_bearings", "no bearings assigned to this configuration", "", None, None, status="no_model")],
            tip="bearings")]
    amb = num((rec.get("conditions") or {}).get("ambient_c"))
    cards: Dict[str, str] = {}
    sums: List[Dict[str, Any]] = []
    for r in rows:
        n = float(r["rpm"])
        t = num(r.get("temp_c")) if num(r.get("temp_c")) is not None else amb
        m = C.mech_loss_at(model.get("mech"), n, t) or {}
        ends = m.get("ends") or []
        for e in ends:
            cards[e["end"]] = e["card"]
        if ends:
            det = "simulation, bearing temperature %s" % ("?" if t is None else "%g C" % t)
            sums.append(_row("bearing_loss", "%d rpm" % round(n), "W", None, m.get("bearings_w"), tip="bearings",
                             params={"rpm": round(n)}, status="info", detail=det))
    if not sums:
        return []
    names = [cards[e] for e in sorted(cards)]
    cards_txt = "%d x %s" % (len(names), names[0]) if len(set(names)) == 1 else " + ".join(names)
    return [_group("bearing_sum", "Bearings, %s (simulation)" % cards_txt, sums, params={"cards": cards_txt}, tip="bearings",
                   basis="simulation: SKF friction, rotor weight as radial load; not measured", basis_key="bearings_model")]


def _free_run(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    rows_in = [r for r in rec.get("rows") or [] if num(r.get("rpm"))]
    rows_in.sort(key=lambda r: float(r["rpm"]))
    refs = model.get("noload") or []
    mag = model.get("magnet") or {}
    pts = mag.get("points") or []
    emf_rows, rms_rows, loss_rows = [], [], []
    fit_meas, fit_model = [], []
    k_flux, k_clause, k_src = _k3(model, "flux")
    temps_used: List[float] = []
    slope_in: List[Dict[str, Any]] = []      # one entry per bench row: what the Kv / Kt slope is fitted to
    for r in rows_in:
        n = float(r["rpm"])
        ref = C.noload_reference(n, refs)
        t_mag, t_src = _row_magnet_temp(r, rec, rows_in)
        params = {"rpm": round(n)}
        lbl = "%d rpm" % round(n)
        vpk_meas = None if num(r.get("v_ll_pp_v")) is None else float(r["v_ll_pp_v"]) / 2.0
        slope_in.append({"i": len(emf_rows), "rpm": n, "pk": vpk_meas, "rms": num(r.get("v_ll_rms_v")),
                         "fund": num(r.get("v_ll_fund_pk_v")), "m_pk": None, "m_src": None})
        if ref is None:
            emf_rows.append(_row("emf_peak", lbl, "V", vpk_meas, None, tip="emf_peak", params=params))
            rms_rows.append(_row("emf_rms", lbl, "V", r.get("v_ll_rms_v"), None, tip="emf_rms", params=params))
            if num(r.get("shaft_power_w")) is not None:
                loss_rows.append(_row("loss", lbl, "W", r.get("shaft_power_w"), None, tip="loss", params=params))
            continue
        ratio = C.br_ratio(pts, t_mag, ref.get("magnet_temp_c"))
        emf = C.emf_model_at(n, ref, ratio)
        if t_mag is not None:
            temps_used.append(float(t_mag))
        det = (f"no-load duty '{ref['duty']}' at {ref['rpm']:g} rpm, magnet {ref.get('magnet_temp_c') if ref.get('magnet_temp_c') is not None else '?'} C"
               f" -> {('?' if t_mag is None else f'{t_mag:.0f}')} C ({t_src}), Br ratio {ratio:.3f}")
        extra = {"magnet_temp_c": t_mag, "magnet_temp_source": t_src, "br_ratio": ratio, "ref_duty": ref["duty"],
                 "ref_rpm": ref["rpm"]}
        # the EMF carries the end-effect flux factor (corrected value only)
        emf_det = det + ", " + k_clause
        # the simulated slope is formed on the FUNDAMENTAL; an older stored no-load run without one falls back to its waveform
        # peak and says so
        slope_in[-1].update({"m_pk": emf["fund"] if emf["fund"] is not None else emf["peak"],
                             "m_src": "fundamental" if emf["fund"] is not None else "waveform peak"})
        emf_x = {**extra, "corrected": k_flux is not None}
        rms_x = dict(emf_x)
        emf_rows.append(_row("emf_peak", lbl, "V", vpk_meas, _scaled(emf["peak"], k_flux), tip="emf_peak", params=params,
                             detail=emf_det, extra=emf_x))
        rms_rows.append(_row("emf_rms", lbl, "V", r.get("v_ll_rms_v"), _scaled(emf["rms"], k_flux), tip="emf_rms",
                             params=params, detail=emf_det, extra=rms_x))
        em = C.noload_em_loss_at(n, ref, ratio)
        mech = C.mech_loss_at(model.get("mech"), n, num(r.get("temp_c")) if num(r.get("temp_c")) is not None
                              else num((rec.get("conditions") or {}).get("ambient_c")))
        total = None
        if em is not None:
            total = em["em"] + (mech or {}).get("bearings_w", 0.0) + (mech or {}).get("windage_w", 0.0)
        meas_w = num(r.get("shaft_power_w"))
        lx = {"em_w": None if em is None else em["em"], "core_w": None if em is None else em["core"],
              "solid_w": None if em is None else em["solid"],
              "bearings_w": (mech or {}).get("bearings_w"), "windage_w": (mech or {}).get("windage_w"),
              "unexplained_w": None if (meas_w is None or total is None) else meas_w - total,
              "has_bearings": bool(model.get("has_bearings")), **extra}
        if meas_w is not None or total is not None:
            loss_rows.append(_row("loss", lbl, "W", meas_w, total, tip="loss", params=params, detail=det, extra=lx))
        if meas_w is not None:
            fit_meas.append((n, meas_w))
            if total is not None:
                fit_model.append((n, total))
    if temps_used:
        lo, hi = min(temps_used), max(temps_used)
        mag = "%.0f" % lo if round(lo) == round(hi) else "%.0f-%.0f" % (lo, hi)
        mag_txt, mag_p = "magnets at %s C" % mag, mag
    else:
        mag_txt, mag_p = "magnets at the simulation temperature", None
    basis_emf = mag_txt + ", " + k_clause
    emf_key = "emf_3d" if k_flux is not None else "emf_2d"
    emf_bp = {"mag": mag_p or "?", "k": None if k_flux is None else "%.3f" % k_flux, "src": k_src or ""}
    groups = []
    # a voltage group only when the test measured that voltage (owner 2026-10-30: a free-run logged as torque /
    # speed / power only must not show empty EMF tables)
    if any(r.get("measured") is not None for r in emf_rows):
        groups.append(_group("emf_peak", "EMF line-line peak (Vpp / 2)", emf_rows, tip="emf_peak", basis=basis_emf,
                             basis_key=emf_key, basis_params=emf_bp))
    if any(r.get("measured") is not None for r in rms_rows):
        groups.append(_group("emf_rms", "EMF line-line rms", rms_rows, tip="emf_rms", basis=basis_emf,
                             basis_key=emf_key, basis_params=emf_bp))
    groups.extend(_kv_kt_groups(slope_in, emf_rows, model, mag_p or "?"))
    if loss_rows:
        groups.append(_group("loss", "No-load loss (shaft power)", loss_rows, tip="loss",
                             basis=mag_txt, basis_key="loss",
                             basis_params={"mag": mag_p or "?"}))
    groups.extend(_bearing_groups(rows_in, rec, model))
    fm, fd = C.fit_an_bn2(fit_meas), C.fit_an_bn2(fit_model)
    if fm or fd:
        groups.append(_group("fit", "Loss fit P = a*n + b*n^2 (n in krpm)", [
            _row("fit_a", "a", "W/krpm", None if fm is None else fm["a"], None if fd is None else fd["a"], tip="fit"),
            _row("fit_b", "b", "W/krpm^2", None if fm is None else fm["b"], None if fd is None else fd["b"], tip="fit"),
        ], tip="fit", basis="least squares through the origin, n in krpm",
        basis_key="fit"))
    action = None
    if not refs and rows_in:
        top = max(float(r["rpm"]) for r in rows_in)
        action = _create_duty(die, config, rpm=top, current=0.0, name=f"no-load {int(round(top))} rpm", model=model)
        groups.insert(0, _group("no_model", "No simulation point", [
            _row("no_noload", "no no-load duty in this configuration", "", None, None, status="no_model", action=action)]))
    return {"groups": groups, "fit": {"measured": fm, "model": fd}}


# ---------------------------------------------------------------------------
# loads
# ---------------------------------------------------------------------------

def _duty_eff_pct(inp: Optional[Dict[str, Any]], k: float) -> Optional[float]:
    """Shaft efficiency (%) of one stored duty on the CORRECTED rotor power ``|P_mech| * k``; the losses stay as stored.

    The same arithmetic as ``web/src/lib/dutyEfficiency.ts`` (motor: shaft out over electrical in; generator: electrical
    out over shaft in; bearings and windage move the shaft power when the duty stores them).  ``inp`` = the duty's
    ``eff_inputs`` (``p_mech_w``, ``p_loss_total_w``, ``p_bearings_w``, ``p_windage_w``, ``op_mode``).  A duty that lacks
    the rotor power or the total loss gives None - never the uncorrected value."""
    if not isinstance(inp, dict):
        return None
    pm_raw, loss = num(inp.get("p_mech_w")), num(inp.get("p_loss_total_w"))
    if pm_raw is None or loss is None or loss < 0:
        return None
    pm = abs(pm_raw) * float(k)
    if pm <= 0:
        return None
    brg, wind = num(inp.get("p_bearings_w")), num(inp.get("p_windage_w"))
    gen = inp.get("op_mode") == "generator"
    p_elec = max(0.0, pm - loss) if gen else pm + loss
    if brg is None and wind is None:
        return 100.0 * (p_elec / pm if gen else pm / p_elec)
    extra = (brg or 0.0) + (wind or 0.0)
    p_shaft = pm + extra if gen else max(0.0, pm - extra)
    if gen:
        return 100.0 * (p_elec / p_shaft if p_shaft > 0 else 0.0)
    return 100.0 * (p_shaft / p_elec if p_elec > 0 else 0.0)


def _duty_effs(d: Dict[str, Any], k: Optional[float]) -> tuple:
    """``(motor efficiency %, system efficiency %)`` of a stored duty as the Tests tab shows them.

    With a correction factor both are recomputed on the corrected shaft power (system = the stored inverter efficiency
    x the corrected motor efficiency); a duty that lacks what is needed gives None for that value.  Without a factor the
    stored, uncorrected values are returned (the row says "not corrected yet")."""
    if k is None:
        return num(d.get("eff_pct")), num(d.get("eff_system_pct"))
    eff = _duty_eff_pct(d.get("eff_inputs"), k)
    inv = num(d.get("eta_inverter_pct"))
    return eff, (None if (eff is None or inv is None) else eff * inv / 100.0)


def _eff_system_row(measured: float, model: Optional[float], det: str) -> Dict[str, Any]:
    """Between the DC link and the shaft (inverter x machine): against the stored system efficiency, else information, no delta."""
    if num(model) is None:
        return _row("eff_system", "System efficiency", "%", measured, None, tip="eff_system",
                    status="info", detail=(det + ", " if det else "") + "no system efficiency from the stored duty")
    return _row("eff_system", "System efficiency", "%", measured, model, tip="eff_system",
                detail=det + ", inverter x motor")


# the quantities a load sweep can be plotted against rpm: (key, EN label, unit, tip); the web translates
# ``tests.series.q.<key>`` and reads the tooltip from ``tests.tip.<tip>``
_SERIES_QUANTITIES = (
    ("torque", "Torque", "N·m", "load"),
    ("eff", "Efficiency", "%", "load"),
    ("eff_system", "System efficiency", "%", "eff_system"),
    ("i_dc", "DC current", "A", "load"),
    ("p_dc", "DC power", "W", "load"),
    ("p_shaft", "Shaft power", "W", "load"),
    ("t_winding", "Winding temperature", "C", "load"),
    ("t_magnet", "Magnet temperature", "C", "load"),
    ("t_housing", "Housing temperature", "C", "load"),
)


def _load_series(points: List[Dict[str, Any]], plot_rows: Optional[List[Dict[str, Any]]] = None,
                 curves: Optional[List[Dict[str, Any]]] = None, note: str = "") -> Optional[Dict[str, Any]]:
    """All rows of a load record at once, for the chart beside the per-point tables (read-only, additive).

    ``points`` = one dict per bench row: ``{"rpm", "current", "<quantity key>": (measured, model)}``.  A quantity that no
    row has (neither measured nor modelled) is dropped; inside a quantity a row with neither value is skipped, a
    missing value stays ``None`` (the chart never draws it as 0).  Rows are sorted by rpm.

    ``plot_rows`` (additive key ``points``) = the raw bench numbers of every row (rpm, phase current, torque, DC link,
    temperatures) and, only where a stored duty matched, the model's own values (``model``): the web groups them by speed
    and plots torque / loss / efficiency / DC current against torque or current.  Nothing is derived or invented here."""
    quantities = []
    for key, label, unit, tip in _SERIES_QUANTITIES:
        pts = []
        for p in points:
            meas, mod = p.get(key, (None, None))
            meas, mod = num(meas), num(mod)
            if meas is None and mod is None:
                continue
            pts.append({"rpm": p["rpm"], "current": p.get("current"), "measured": meas, "model": mod,
                        "delta_pct": C.delta_pct(mod, meas)})
        if not pts:
            continue
        pts.sort(key=lambda q: q["rpm"])
        quantities.append({"key": key, "label": label, "unit": unit, "tip": tip, "points": pts})
    if not quantities and not curves:
        return None
    out = {"x": "rpm", "quantities": quantities, "points": list(plot_rows or [])}
    if curves:
        # the record's own simulation curves (never stored duties), passed through as stored
        out["simulation_curves"] = curves
        if note:
            out["simulation_note"] = note
    return out


def _load(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    groups = []
    series_pts: List[Dict[str, Any]] = []
    plot_rows: List[Dict[str, Any]] = []
    duties = model.get("duties") or []
    for r in rec.get("rows") or []:
        n, i = num(r.get("rpm")), num(r.get("i_phase_a"))
        params = {"rpm": None if n is None else round(n), "current": i}
        label = "%s rpm" % ("?" if n is None else f"{n:.0f}") + ("" if i is None else f" · {i:g} A")
        t_min = num(r.get("time_min"))                      # minutes from the start of a logged run
        if t_min is not None:
            label = f"{t_min:g} min · {label}"
        d = C.match_duty(n, i, duties)
        # eff_pct = the motor's efficiency as the bench gives it: compared with the model's motor efficiency at the
        # shaft.  An efficiency formed from the DC link (torque * omega / (V_dc * I_dc)) includes the inverter: it is
        # the SYSTEM efficiency, compared only with the model's stored system (wall-to-shaft) efficiency, else shown
        # as information without a delta - never against the motor's.
        eff_meas = num(r.get("eff_pct"))
        eff_sys = None
        if num(r.get("torque_nm")) is not None and n and num(r.get("v_dc_v")) and num(r.get("i_dc_a")):
            # the smaller power over the larger: shaft over DC when motoring, DC over shaft when generating
            p_in = abs(float(r["v_dc_v"]) * float(r["i_dc_a"]))
            if p_in > 0:
                p_mech = abs(float(r["torque_nm"])) * n * 3.141592653589793 / 30.0
                eff_sys = 100.0 * min(p_in, p_mech) / max(p_in, p_mech)
        omega = None if not n else n * 3.141592653589793 / 30.0
        t_meas = num(r.get("torque_nm"))
        v_dc, i_dc = num(r.get("v_dc_v")), num(r.get("i_dc_a"))
        sp: Dict[str, Any] = {
            "rpm": n, "current": i,
            "torque": (t_meas, None), "eff": (eff_meas, None), "eff_system": (eff_sys, None),
            "i_dc": (i_dc, None),
            "p_dc": (None if (v_dc is None or i_dc is None) else v_dc * i_dc, None),
            "p_shaft": (None if (t_meas is None or omega is None) else t_meas * omega, None),
            "t_winding": (r.get("temp_winding_c"), None), "t_magnet": (r.get("temp_magnet_c"), None),
            "t_housing": (r.get("temp_housing_c"), None)}
        if n is not None:
            series_pts.append(sp)
            plot_rows.append({"rpm": n, "current": i, "torque": t_meas, "v_dc": v_dc, "i_dc": i_dc, "time_min": t_min,
                              "t_winding": num(r.get("temp_winding_c")), "t_magnet": num(r.get("temp_magnet_c")),
                              "t_housing": num(r.get("temp_housing_c")), "model": {}})
        if d is None:
            act = (_create_duty(die, config, rpm=n, current=i, model=model) if (n and i) else None)
            rows = [_row("torque", "Torque", "N·m", r.get("torque_nm"), None, tip="load", status="no_model", action=act)]
            if eff_meas is not None:
                rows.append(_row("eff", "Efficiency", "%", eff_meas, None, tip="load", status="no_model"))
            if eff_sys is not None:
                rows.append(_eff_system_row(eff_sys, None, ""))
            if num(r.get("j_a_mm2")) is not None:
                rows.append(_row("j_coil", "Current density", "A/mm²", r.get("j_a_mm2"), None, tip="load", status="no_model"))
            groups.append(_group("load_point", label, rows, params=params, tip="load"))
            continue
        det = f"duty '{d['name']}' ({d['rpm']:g} rpm, {d['current']:g} A)"
        kt, kt_clause, kt_src = _k3(model, "torque")
        rows = [_row("torque", "Torque" + (" (shaft)" if d.get("torque_kind") == "shaft" else " (EM)"), "N·m",
                     r.get("torque_nm"), _scaled(d.get("torque_nm"), kt), tip="load", detail=det + ", " + kt_clause,
                     extra={"corrected": kt is not None}),
                ]
        d_eff, d_eff_sys = _duty_effs(d, kt)
        if eff_meas is not None:
            rows.append(_row("eff", "Efficiency", "%", eff_meas, d_eff, tip="load", detail=det + ", " + kt_clause,
                             extra={"corrected": kt is not None}))
        if eff_sys is not None:
            rows.append(_eff_system_row(eff_sys, d_eff_sys, det + ", " + kt_clause))
        if num(r.get("temp_winding_c")) is not None:
            rows.append(_row("t_winding", "Winding temperature", "C", r.get("temp_winding_c"), d.get("winding_c"),
                             tip="load", detail=det + (f", {d['temps_source']} steady state" if d.get("temps_source") else ""),
                             extra={"duration_s": r.get("duration_s")}))
        if num(r.get("temp_magnet_c")) is not None:
            rows.append(_row("t_magnet", "Magnet temperature", "C", r.get("temp_magnet_c"), d.get("magnet_c"),
                             tip="load", detail=det, extra={"duration_s": r.get("duration_s")}))
        if num(r.get("j_a_mm2")) is not None:
            rows.append(_row("j_coil", "Current density", "A/mm²", r.get("j_a_mm2"), None, tip="load", status="no_model"))
        groups.append(_group("load_point", label, rows, params={**params, "duty": d["name"]}, tip="load",
                             basis="torque and efficiency on the corrected shaft power" if kt is not None
                             else "not corrected yet",
                             basis_key="load_3d" if kt is not None else "load_2d",
                             basis_params={"k": None if kt is None else "%.3f" % kt, "src": kt_src or ""}))
        # the model side of the same row, for the chart (the very numbers of the table rows above)
        t_mod = _scaled(d.get("torque_nm"), kt)
        sp["torque"] = (t_meas, t_mod)
        sp["eff"] = (eff_meas, d_eff)
        sp["eff_system"] = (eff_sys, d_eff_sys)
        sp["p_shaft"] = (sp["p_shaft"][0], None if (t_mod is None or omega is None) else t_mod * omega)
        sp["t_winding"] = (r.get("temp_winding_c"), d.get("winding_c"))
        sp["t_magnet"] = (r.get("temp_magnet_c"), d.get("magnet_c"))
        if n is not None:
            plot_rows[-1]["model"] = {k: num(v) for k, v in (
                ("torque", t_mod), ("eff_system", d_eff_sys),
                ("t_winding", d.get("winding_c")), ("t_magnet", d.get("magnet_c"))) if num(v) is not None}
    return {"groups": groups, "fit": None, "series": _load_series(series_pts, plot_rows, rec.get("simulation_curves"),
                                           str(rec.get("simulation_note") or ""))}


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def compare_record(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    kind = rec.get("kind")
    if kind == "parameters":
        groups = [g for g in (_resistance(rec, model), _inductance(rec, model), _cogging(rec, model)) if g] + _info_groups(rec, model)
        out = {"groups": groups, "fit": None}
    elif kind == "free_run":
        out = _free_run(rec, model, die, config)
    else:
        out = _load(rec, model, die, config)
    return {"test_id": rec.get("id"), "kind": kind, "model_available": bool(model.get("available")), **out}


def compare_all(doc: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    die, config = str(doc.get("die") or ""), str(doc.get("config") or "")
    return {str(r.get("id")): compare_record(r, model, die, config) for r in doc.get("tests") or [] if isinstance(r, dict)}

