"""One test record + the model source -> the measured | model | delta table (one table per test, grouped).

Shape (what the web and the report read)::

    {"test_id", "kind", "groups": [
        {"key", "label", "params", "tip", "rows": [
            {"key", "label", "params", "unit", "measured", "model", "delta_pct", "tip",
             "status": "ok" | "no_model" | "info", "extra": {...}, "action": {...} | None}]}],
     "fit": {...} | None}

``key`` / ``params`` are what the web translates (EN + zh); ``label`` is the EN text the report prints.  ``tip`` names a
``compare.TIPS`` entry; ``detail`` (EN) says which duty / temperature the model value came from.
"""
from __future__ import annotations

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
           tip: Optional[str] = None) -> Dict[str, Any]:
    return {"key": key, "label": label, "params": params or {}, "tip": tip, "rows": rows}


def _create_duty(die: str, config: str, *, rpm: float, current: float, mode: str = "motor",
                 name: Optional[str] = None) -> Dict[str, Any]:
    """The body of ``POST /api/family/duty`` that adds the missing operating point (no solve; the engineer runs it)."""
    nm = name or (f"test {int(round(rpm))} rpm" + (" no-load" if current <= 0.5 else f" {current:g} A"))
    return {"kind": "create_duty", "body": {"die": die, "config": config,
                                            "duty": {"name": nm[:60], "mode": mode, "rpm": round(float(rpm), 1),
                                                     "current_arms": round(float(current), 2)}}}


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------

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
        rows.append(_row("r_winding", "winding only (model)", "mOhm", None, res["winding_ohm"] * 1000,
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
                  params={"temp_c": t}, tip="resistance")


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
                  tip="l_freq" if freq_flag["high_freq"] else "ldq_sum")


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
        mkg = model.get("mass_kg")
        rows.append(_row("weight", "Weight: %s" % p.get("what"), "g", p.get("mass_g"),
                         None if mkg is None else mkg * 1000.0, tip="weight", params={"what": p.get("what")}))
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


def _free_run(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    rows_in = [r for r in rec.get("rows") or [] if num(r.get("rpm"))]
    rows_in.sort(key=lambda r: float(r["rpm"]))
    refs = model.get("noload") or []
    mag = model.get("magnet") or {}
    pts = mag.get("points") or []
    emf_rows, rms_rows, loss_rows = [], [], []
    fit_meas, fit_model = [], []
    for r in rows_in:
        n = float(r["rpm"])
        ref = C.noload_reference(n, refs)
        t_mag, t_src = _row_magnet_temp(r, rec, rows_in)
        params = {"rpm": round(n)}
        lbl = "%d rpm" % round(n)
        vpk_meas = None if num(r.get("v_ll_pp_v")) is None else float(r["v_ll_pp_v"]) / 2.0
        if ref is None:
            emf_rows.append(_row("emf_peak", lbl, "V", vpk_meas, None, tip="emf_peak", params=params))
            rms_rows.append(_row("emf_rms", lbl, "V", r.get("v_ll_rms_v"), None, tip="emf_rms", params=params))
            if num(r.get("shaft_power_w")) is not None:
                loss_rows.append(_row("loss", lbl, "W", r.get("shaft_power_w"), None, tip="loss", params=params))
            continue
        ratio = C.br_ratio(pts, t_mag, ref.get("magnet_temp_c"))
        emf = C.emf_model_at(n, ref, ratio)
        det = (f"no-load duty '{ref['duty']}' at {ref['rpm']:g} rpm, magnet {ref.get('magnet_temp_c') if ref.get('magnet_temp_c') is not None else '?'} C"
               f" -> {('?' if t_mag is None else f'{t_mag:.0f}')} C ({t_src}), Br ratio {ratio:.3f}")
        extra = {"magnet_temp_c": t_mag, "magnet_temp_source": t_src, "br_ratio": ratio, "ref_duty": ref["duty"],
                 "ref_rpm": ref["rpm"]}
        emf_rows.append(_row("emf_peak", lbl, "V", vpk_meas, emf["peak"], tip="emf_peak", params=params, detail=det,
                             extra=extra))
        rms_rows.append(_row("emf_rms", lbl, "V", r.get("v_ll_rms_v"), emf["rms"], tip="emf_rms", params=params,
                             detail=det, extra=extra))
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
    groups = [_group("emf_peak", "EMF line-line peak (Vpp / 2)", emf_rows, tip="emf_peak"),
              _group("emf_rms", "EMF line-line rms", rms_rows, tip="emf_rms")]
    if loss_rows:
        groups.append(_group("loss", "No-load loss (shaft power)", loss_rows, tip="loss"))
    fm, fd = C.fit_an_bn2(fit_meas), C.fit_an_bn2(fit_model)
    if fm or fd:
        groups.append(_group("fit", "Loss fit P = a*n + b*n^2 (n in krpm)", [
            _row("fit_a", "a", "W/krpm", None if fm is None else fm["a"], None if fd is None else fd["a"], tip="fit"),
            _row("fit_b", "b", "W/krpm^2", None if fm is None else fm["b"], None if fd is None else fd["b"], tip="fit"),
        ], tip="fit"))
    action = None
    if not refs and rows_in:
        top = max(float(r["rpm"]) for r in rows_in)
        action = _create_duty(die, config, rpm=top, current=0.0, name=f"no-load {int(round(top))} rpm")
        groups.insert(0, _group("no_model", "No model point", [
            _row("no_noload", "no no-load duty in this configuration", "", None, None, status="no_model", action=action)]))
    return {"groups": groups, "fit": {"measured": fm, "model": fd}}


# ---------------------------------------------------------------------------
# loads
# ---------------------------------------------------------------------------

def _eff_system_row(measured: float, model: Optional[float], det: str) -> Dict[str, Any]:
    """DC link in, shaft out (inverter x motor): against the stored system efficiency, else information, no delta."""
    if num(model) is None:
        return _row("eff_system", "System efficiency (DC in, shaft out)", "%", measured, None, tip="eff_system",
                    status="info", detail=(det + ", " if det else "") + "no stored system efficiency")
    return _row("eff_system", "System efficiency (DC in, shaft out)", "%", measured, model, tip="eff_system",
                detail=det + ", controller wall-to-shaft")


def _load(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    groups = []
    duties = model.get("duties") or []
    for r in rec.get("rows") or []:
        n, i = num(r.get("rpm")), num(r.get("i_phase_a"))
        params = {"rpm": None if n is None else round(n), "current": i}
        label = "%s rpm" % ("?" if n is None else f"{n:.0f}") + ("" if i is None else f" · {i:g} A")
        d = C.match_duty(n, i, duties)
        # eff_pct = the motor's efficiency as the bench gives it: compared with the model's motor efficiency at the
        # shaft.  An efficiency formed from the DC link (torque * omega / (V_dc * I_dc)) includes the inverter: it is
        # the SYSTEM efficiency, compared only with the model's stored system (wall-to-shaft) efficiency, else shown
        # as information without a delta - never against the motor's.
        eff_meas = num(r.get("eff_pct"))
        eff_sys = None
        if num(r.get("torque_nm")) is not None and n and num(r.get("v_dc_v")) and num(r.get("i_dc_a")):
            p_in = float(r["v_dc_v"]) * float(r["i_dc_a"])
            if p_in > 0:
                eff_sys = 100.0 * float(r["torque_nm"]) * n * 3.141592653589793 / 30.0 / p_in
        if d is None:
            act = (_create_duty(die, config, rpm=n, current=i) if (n and i) else None)
            rows = [_row("torque", "Torque", "N·m", r.get("torque_nm"), None, tip="load", status="no_model", action=act)]
            if eff_meas is not None:
                rows.append(_row("eff", "Efficiency", "%", eff_meas, None, tip="load", status="no_model"))
            if eff_sys is not None:
                rows.append(_eff_system_row(eff_sys, None, ""))
            groups.append(_group("load_point", label, rows, params=params, tip="load"))
            continue
        det = f"duty '{d['name']}' ({d['rpm']:g} rpm, {d['current']:g} A)"
        rows = [_row("torque", "Torque" + (" (shaft)" if d.get("torque_kind") == "shaft" else " (EM)"), "N·m",
                     r.get("torque_nm"), d.get("torque_nm"), tip="load", detail=det),
                ]
        if eff_meas is not None:
            rows.append(_row("eff", "Efficiency", "%", eff_meas, d.get("eff_pct"), tip="load", detail=det))
        if eff_sys is not None:
            rows.append(_eff_system_row(eff_sys, d.get("eff_system_pct"), det))
        if num(r.get("temp_winding_c")) is not None:
            rows.append(_row("t_winding", "Winding temperature", "C", r.get("temp_winding_c"), d.get("winding_c"),
                             tip="load", detail=det + (f", {d['temps_source']} steady state" if d.get("temps_source") else ""),
                             extra={"duration_s": r.get("duration_s")}))
        if num(r.get("temp_magnet_c")) is not None:
            rows.append(_row("t_magnet", "Magnet temperature", "C", r.get("temp_magnet_c"), d.get("magnet_c"),
                             tip="load", detail=det, extra={"duration_s": r.get("duration_s")}))
        groups.append(_group("load_point", label, rows, params={**params, "duty": d["name"]}, tip="load"))
    return {"groups": groups, "fit": None}


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def compare_record(rec: Dict[str, Any], model: Dict[str, Any], die: str, config: str) -> Dict[str, Any]:
    kind = rec.get("kind")
    if kind == "parameters":
        groups = [g for g in (_resistance(rec, model), _inductance(rec, model)) if g] + _info_groups(rec, model)
        out = {"groups": groups, "fit": None}
    elif kind == "free_run":
        out = _free_run(rec, model, die, config)
    else:
        out = _load(rec, model, die, config)
    return {"test_id": rec.get("id"), "kind": kind, "model_available": bool(model.get("available")), **out}


def compare_all(doc: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    die, config = str(doc.get("die") or ""), str(doc.get("config") or "")
    return {str(r.get("id")): compare_record(r, model, die, config) for r in doc.get("tests") or [] if isinstance(r, dict)}

