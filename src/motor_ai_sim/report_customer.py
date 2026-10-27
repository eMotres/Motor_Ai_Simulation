"""The customer-facing blocks of the motor report (owner 2026-10-26, customer review "FIX FIRST").

Pure builders, no renderer: both the PDF (``report``) and the Word file (``report_docx``) print what these return.

* :func:`project_requirements` - the customer's numbers, read from the project specification the configuration belongs
  to (key points, peak duration, battery, maximum ambient, overspeed, loads).  Never a number typed into the report.
* :func:`requirements_rows` - the "Requirements check" table at the top of the report: each requirement, the value
  achieved, the record it comes from, and met / not met / not computed.
* :func:`duty_basis_words` - the ONE declared basis of a duty's numbers on the cover and in the summary.
* :func:`bearing_load_rows` - the "Bearing loads" table: axial and radial load per duty from stated inputs, and the
  ISO 281 L10 life when bearings are assigned.
* :func:`overspeed_cell` - the mechanical overspeed case, or "pending".

Every cell is one line: the fact and its number.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MET, NOT_MET, NOT_COMPUTED = "met", "not met", "not computed"
CUSTOMER_INPUT_NEEDED = "customer input needed"
PENDING = "pending"

#: How close a duty's speed must be to a requirement's to answer it (the #437 per-point tolerance).
RPM_REL_TOL, RPM_ABS_TOL = 0.01, 1.0
#: A torque this close under the target is not rounded into a pass.
TORQUE_TOL_REL = 0.0005

MU0 = 4e-7 * math.pi
G0 = 9.80665

#: The STATED assumptions of the bearing-load table; a configuration overrides them under ``bearing_loads:``.
BEARING_LOAD_DEFAULTS = {
    "load_factor_g": 3.0,            # rotor weight multiplier (manoeuvre / landing)
    "eccentricity_pct_of_gap": 10.0,  # static eccentricity for the unbalanced magnetic pull
    "axial_offset_mm": 0.5,           # rotor-to-stator axial offset tolerance
}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, Mapping):
        v = v.get("maximum", v.get("value"))
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fmt(v: Any, d: int = 1, unit: str = "") -> str:
    from motor_ai_sim.report import _fmt as f
    return f(v, d, unit)


def _g(node: Any, path: str) -> Any:
    cur = node
    for p in path.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(p)
    return cur


def _close(a: Optional[float], b: Optional[float], rel: float = RPM_REL_TOL, floor: float = RPM_ABS_TOL) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= max(floor, rel * max(abs(a), abs(b)))


def _secs(s: Any) -> str:
    v = _num(s)
    if v is None:
        return "—"
    if v < 120:
        return "%s s" % _fmt(v, 0)
    m, sec = divmod(int(round(v)), 60)
    return "%d min %02d s" % (m, sec)


# ---------------------------------------------------------------------------
# the customer's numbers
# ---------------------------------------------------------------------------

#: The configuration key of an EXPLICIT requirements override (offline builds, a variant outside any project).  The
#: report labels it as such; the project specification is the normal source.
REQUIREMENTS_OVERRIDE_KEY = "requirements_override"
OVERRIDE_SOURCE = "configuration override (not the project specification)"


def requirements_from_draft(draft: Optional[Mapping[str, Any]], source: str = "project specification"
                            ) -> Dict[str, Any]:
    """The requirements this report checks, from one specification draft (``{"fields": {...}}``), read the CANONICAL
    way (``development.models``): only FILLED answers (``_active_value``), the key points through their own list,
    the overspeed through ``proof_speed_rpm`` (the larger of the maximum and a stored overspeed) and the thermal
    basis through ``thermal_basis`` (ambient air, coolant inlet or water, by the cooling medium)."""
    from motor_ai_sim.development import models as M
    draft = dict(draft or {})
    fields = draft.get("fields") if isinstance(draft.get("fields"), Mapping) else {}

    def val(fid: str) -> Dict[str, Any]:
        v = M._active_value(fields, fid)
        return dict(v) if isinstance(v, Mapping) else {}

    out: Dict[str, Any] = {"source": source}
    if M.key_points_stored(draft):
        kp = M._active_value(fields, M.KEY_POINTS_FIELD)
        rows = [dict(r) for r in kp if isinstance(r, Mapping)] if isinstance(kp, list) else []
    else:
        rows = M.legacy_key_points({k: v for k, v in fields.items()
                                    if k in ("1_4", "1_5") and M._active_value(fields, k) is not None})
    try:
        cont, peak = M.derive_continuous_peak(rows)
    except Exception:                                           # noqa: BLE001
        cont, peak = None, None
    if not cont and not M.key_points_stored(draft) and val("1_4"):
        cont = val("1_4")                     # a legacy S1 answer the key-point rules cannot use as a row
    if not peak and not M.key_points_stored(draft) and val("1_5"):
        peak = val("1_5")
    if cont:
        out["continuous"] = {"torque_Nm": _num(cont.get("torque_Nm")), "speed_rpm": _num(cont.get("speed_rpm"))}
    if peak:
        dur = peak.get("duration_s")
        if dur in (None, M.CONTINUOUS):
            dur = val("1_5").get("duration_s")
        out["peak"] = {"torque_Nm": _num(peak.get("torque_Nm")), "speed_rpm": _num(peak.get("speed_rpm")),
                       "duration_s": _num(dur)}
    b = val("2_1")
    if b and b.get("supply_type") == "dc_source":       # a DC power source: its one voltage is the supply window
        dc = _num(b.get("dc_source_V"))
        if dc is not None:
            out["battery"] = {"v_min": dc, "v_nom": dc, "v_max": dc, "cells": None, "chemistry": None}
    elif b:
        out["battery"] = {"v_min": _num(b.get("voltage_min_V")), "v_nom": _num(b.get("voltage_nominal_V")),
                          "v_max": _num(b.get("voltage_max_V")), "cells": _num(b.get("series_cells")),
                          "chemistry": b.get("chemistry")}
    tb = M.thermal_basis(draft)
    if _num(tb.get("temperature_C")) is not None:
        out["ambient_max_c"] = _num(tb.get("temperature_C"))
        out["thermal_medium"] = tb.get("medium") or "air"
    ov = M.proof_speed_rpm(draft)
    if ov is not None:
        out["overspeed_rpm"] = float(ov)
    ld = val("4_6")
    if ld:
        out["axial_thrust_N"] = _num(ld.get("axial_thrust_N"))
        out["reverse_thrust_N"] = _num(ld.get("reverse_thrust_N"))
        out["radial_load_N"] = _num(ld.get("radial_load_N"))
    return {k: v for k, v in out.items() if v not in (None, {}, "")}


def _latest_spec_draft(project_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(draft, source words)``: the newest SUBMITTED specification version, else the working draft."""
    from motor_ai_sim.development import store
    idx = store.load_json(project_id, "spec/index.json", {"versions": []}) or {}
    nums = [int(v.get("version", 0)) for v in (idx.get("versions") or []) if isinstance(v, Mapping)]
    for n in sorted(nums, reverse=True):
        ver = store.load_json(project_id, f"spec/v{n}.json", None)
        if isinstance(ver, Mapping) and isinstance(ver.get("draft"), Mapping):
            return dict(ver["draft"]), "project specification v%d" % n
    cur = store.load_json(project_id, "draft.json", {}) or {}
    return ((dict(cur.get("draft")), "project specification (draft, not submitted)")
            if isinstance(cur.get("draft"), Mapping) else (None, ""))


def project_requirements(die: str, cfg: str, cfg_doc: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """The customer's requirements for ``die/cfg``: the latest specification of the project the variant is linked
    to.  A configuration may carry an EXPLICIT ``requirements_override:`` block instead (offline builds, a variant
    outside any project); it is used only when present and is labelled as an override everywhere it shows.
    ``{}`` when neither exists.  Never raises."""
    own = (cfg_doc or {}).get(REQUIREMENTS_OVERRIDE_KEY) if isinstance(cfg_doc, Mapping) else None
    if isinstance(own, Mapping) and own:
        out = {k: v for k, v in dict(own).items()}
        out["source"] = OVERRIDE_SOURCE
        return out
    try:
        from motor_ai_sim.development.motors import report_link_state
        st = report_link_state(die, cfg)
        if not st:
            return {}
        draft, words = _latest_spec_draft(str(st["project_id"]))
        return requirements_from_draft(draft, words) if draft else {}
    except Exception:                                           # noqa: BLE001 - a report never fails over the spec
        return {}


def requirements_source_line(req: Mapping[str, Any]) -> str:
    """The one line under the requirements table: where the requirements came from."""
    src = str((req or {}).get("source") or "").strip()
    return ("Requirements from: %s." % src) if src else ""


def customer_battery(batt: Optional[Mapping[str, Any]], req: Mapping[str, Any]) -> Dict[str, Any]:
    """The pack the report judges against: the CUSTOMER's stated voltages when the specification gives them (M06),
    else the configuration's own pack.  ``source`` says which."""
    b = dict(batt or {})
    rb = req.get("battery") if isinstance(req.get("battery"), Mapping) else {}
    if rb and _num(rb.get("v_min")) is not None:
        for k in ("v_min", "v_nom", "v_max"):
            if _num(rb.get(k)) is not None:
                b[k] = _num(rb.get(k))
        if _num(rb.get("cells")) is not None:
            b["cells"] = int(_num(rb.get("cells")))
        if rb.get("chemistry"):
            b["chemistry"] = rb.get("chemistry")
        b["source"] = "customer"
    return b


# ---------------------------------------------------------------------------
# which duty answers a requirement, and on what basis
# ---------------------------------------------------------------------------

def _em(col: Mapping[str, Any]) -> Dict[str, Any]:
    em = col.get("em")
    return em if isinstance(em, Mapping) else {}


def _rpm(col: Mapping[str, Any]) -> Optional[float]:
    return _num(_em(col).get("rpm")) or _num((col.get("d") or {}).get("rpm"))


def _torque(col: Mapping[str, Any]) -> Optional[float]:
    """The duty's torque on the SAME basis as the report's main torque tables: the end-effect corrected
    ``end3d.T_corrected_Nm`` when the run has one, else the 2-D average."""
    t = _num(_g(_em(col), "end3d.T_corrected_Nm"))
    if t is None:
        t = _num(_em(col).get("T_em_avg_Nm"))
    return abs(t) if t is not None else None


def _torque_basis(col: Mapping[str, Any]) -> str:
    return ("3-D corrected" if _num(_g(_em(col), "end3d.T_corrected_Nm")) is not None else "2-D")


def match_duty(cols: Sequence[Mapping[str, Any]], rpm: Optional[float], torque: Optional[float]
               ) -> Optional[Mapping[str, Any]]:
    """The duty solved at ``rpm`` whose torque is nearest ``torque``; ``None`` when no duty runs at that speed."""
    at = [c for c in cols if _close(_rpm(c), rpm) and _torque(c) is not None]
    if not at:
        return None
    if torque is None:
        return at[0]
    return min(at, key=lambda c: abs((_torque(c) or 0.0) - torque))


def duty_basis_words(col: Mapping[str, Any], magnet_card_c: Optional[float] = None) -> str:
    """'EM run · winding 150 °C · magnets 150 °C (card)' - the ONE basis of this duty's numbers."""
    em = _em(col)
    kind = str(col.get("em_source") or "standalone")
    if not em:
        return "not solved"
    if kind == "pwm":
        lead = "PWM run"
    elif kind == "coupled":
        lead = "coupled loop"
    else:
        lead = "EM run"
    w = _num(em.get("coil_temp_C"))
    m = _num(em.get("magnet_temp_C"))
    bits = [lead]
    if w is not None:
        bits.append("winding %s" % _fmt(w, 0, "°C"))
    if m is not None:
        bits.append("magnets %s" % _fmt(m, 0, "°C"))
    elif magnet_card_c is not None:
        bits.append("magnets %s (card)" % _fmt(magnet_card_c, 0, "°C"))
    return " · ".join(bits)


def _pct_short(got: float, want: float) -> str:
    return "%s %%" % _fmt((got - want) / want * 100.0, 1) if want else ""


def _s1_block(col: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    res = col.get("res") or {}
    for k in ("coupled_limit", "coupled", "continuous_rating"):
        rec = res.get(k)
        if not isinstance(rec, Mapping):
            continue
        blk = rec.get("continuous_rating") if k != "continuous_rating" else rec
        if isinstance(blk, Mapping) and blk:
            return dict(blk)
    return None


def _s1_torque(blk: Mapping[str, Any], rpm: Optional[float] = None) -> Tuple[Optional[float], str]:
    """``(torque, "shaft" | "EM")`` of a verified S1 rating: the SHAFT torque (shaft power over the speed) when the
    block states a shaft power, else the electromagnetic torque, named as such."""
    if blk.get("verified") is False or blk.get("trustworthy") is False or blk.get("feasible") is False:
        return None, ""
    pw = blk.get("power") or {}
    n = _num((blk.get("reference") or {}).get("rpm")) or _num(rpm)
    ps = _num(pw.get("P_shaft_W"))
    if ps is not None and n:
        return abs(ps) / (2.0 * math.pi * n / 60.0), "shaft"
    t = _num(pw.get("T_em_Nm"))
    return (abs(t), "EM") if t is not None else (None, "")


def _s2_time(col: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float], str]:
    """(seconds to the limit from cold, the winding limit it was judged at, source words) of a duty."""
    res = col.get("res") or {}
    dc = res.get("duty_cycle") if isinstance(res.get("duty_cycle"), Mapping) else None
    if dc:
        lim = dc.get("limits") if isinstance(dc.get("limits"), Mapping) else {}
        t = _num(lim.get("s2_time_to_limit_s"))
        if t is not None:
            # the limit the S2 pull was judged at: the peak limit when the record states it (owner 2026-10-27)
            return (t, _num(lim.get("winding_peak_limit_c")) or _num(lim.get("winding_limit_c")),
                    "S2 time-to-limit, from cold")
    for k in ("coupled", "coupled_limit"):
        rec = res.get(k)
        blk = rec.get("time_to_limit") if isinstance(rec, Mapping) else None
        if isinstance(blk, Mapping) and blk:
            t = _num(((blk.get("starts") or {}).get("cold") or {}).get("time_to_limit_s"))
            lim = _num((blk.get("limits_c") or {}).get("winding"))
            if t is None:
                lb = rec.get("limited") if isinstance(rec.get("limited"), Mapping) else {}
                t = _num(lb.get("t_cold_s"))
            if t is not None:
                return t, lim, "coupled loop time-to-limit, from cold" + _ttl_preliminary(blk)
        lb = rec.get("limited") if isinstance(rec, Mapping) and isinstance(rec.get("limited"), Mapping) else None
        if lb and _num(lb.get("t_cold_s")) is not None:
            lim = _num((lb.get("at_limit_c") or {}).get("winding")) or _num(rec.get("coil_temp_c"))
            return _num(lb.get("t_cold_s")), lim, "coupled loop stopped at the limit, from cold"
    return None, None, ""


#: What a requirement cell answered by a time the report itself calls a preliminary estimate carries.
PRELIMINARY = "preliminary"


def _ttl_preliminary(blk: Mapping[str, Any]) -> str:
    """``"; preliminary estimate (network residual 51.3 % of losses)"`` when the lumped network the time is fitted to
    is out of balance past the report's own threshold, else ``""`` (audit 2026-10-27, claim 3: the requirements table
    quoted the 47 s as a firm "not met" while section 3 and section 8 call the same time a preliminary estimate)."""
    from motor_ai_sim.report import TTL_RESIDUAL_LIMIT_PCT, ttl_residual_words
    net = blk.get("network") if isinstance(blk.get("network"), Mapping) else {}
    pct = _num(net.get("worst_residual_pct_of_losses"))
    if pct is None or pct <= TTL_RESIDUAL_LIMIT_PCT:
        return ""
    return ttl_residual_words(dict(blk))


def duty_s2_seconds(col: Mapping[str, Any]) -> Optional[float]:
    """The run time the duty itself is defined with (its own S2 block), else its stored cycle's."""
    d = col.get("d") if isinstance(col.get("d"), Mapping) else {}
    for blk in (d.get("duty_cycle"), d.get("cycle")):
        if isinstance(blk, Mapping) and str(blk.get("kind") or "").upper() == "S2":
            t = _num(blk.get("t_on_s"))
            if t is not None:
                return t
    dc = (col.get("res") or {}).get("duty_cycle")
    spec = dc.get("spec") if isinstance(dc, Mapping) and isinstance(dc.get("spec"), Mapping) else {}
    if str((dc or {}).get("kind") or spec.get("kind") or "").upper() in ("S2", ""):
        return _num(spec.get("t_on_s")) or _num(spec.get("cycle_s"))
    return None


def _same_limit(a: Optional[float], b: Optional[float]) -> bool:
    return a is not None and b is not None and abs(a - b) <= 0.5


# ---------------------------------------------------------------------------
# the requirements check
# ---------------------------------------------------------------------------

REQUIREMENTS_HEAD = ["Requirement", "Achieved", "Source", "Status"]


def requirements_rows(req: Mapping[str, Any], cols: Sequence[Mapping[str, Any]], *,
                      winding_limit_c: Optional[float] = None,
                      batt: Optional[Mapping[str, Any]] = None,
                      winding_peak_limit_c: Optional[float] = None) -> List[List[str]]:
    """The "Requirements check" table, header included: one row per customer requirement.  Empty when the project
    states no requirement.  The continuous rows judge the winding at ``winding_limit_c``; the "peak held" row (an S2
    time-to-limit) at ``winding_peak_limit_c`` (owner 2026-10-27; omitted = the continuous limit)."""
    req = dict(req or {})
    rows: List[List[str]] = []
    wl = _num(winding_limit_c)
    wl_txt = _fmt(wl, 0, "°C") if wl is not None else "the winding limit"
    wp = _num(winding_peak_limit_c)
    if wp is None:
        wp = wl
    wp_txt = _fmt(wp, 0, "°C") if wp is not None else "the winding limit"

    # ── continuous torque at its speed ──────────────────────────────────
    cont = req.get("continuous") if isinstance(req.get("continuous"), Mapping) else None
    if cont and _num(cont.get("torque_Nm")) is not None:
        T, n = _num(cont.get("torque_Nm")), _num(cont.get("speed_rpm"))
        label = "Continuous %s @ %s" % (_fmt(T, 0, "N·m"), _fmt(n, 0, "rpm"))
        c = match_duty(cols, n, T)
        if c is None:
            rows.append([label + ", torque", "no duty at this speed", "—", NOT_COMPUTED])
        else:
            t = _torque(c)
            ok = t >= T * (1.0 - TORQUE_TOL_REL)
            rows.append([label + ", torque", "%s (%s)" % (_fmt(t, 1, "N·m"), c.get("duty")),
                         "EM run %s, winding %s" % (_torque_basis(c),
                                                    _fmt(_num(_em(c).get("coil_temp_C")), 0, "°C")),
                         MET if ok else "%s (%s)" % (NOT_MET, _pct_short(t, T))])
        # …and HELD continuously: the S1 search at the project's winding limit
        blk = _s1_block(c) if c is not None else None
        s1_label = "%s held continuously, winding ≤ %s" % (_fmt(T, 0, "N·m"), wl_txt)
        if not blk:
            rows.append([s1_label, "—", "S1 search", NOT_COMPUTED])
        else:
            lim = _num((blk.get("limits_c") or {}).get("winding"))
            ts1, kind = _s1_torque(blk, n)
            i_s1 = _num(blk.get("I_cont_A_rms"))
            ach = ("%s %s at %s" % (_fmt(ts1, 1, "N·m"), kind, _fmt(i_s1, 1, "A")) if ts1 is not None
                   else ("%s, torque not verified" % _fmt(i_s1, 1, "A") if i_s1 is not None else "no rating"))
            src = "S1 search, winding %s" % _fmt(lim, 0, "°C")
            if blk.get("cooling_label"):
                from motor_ai_sim.report import short_cell
                src += ", " + (short_cell(blk.get("cooling_label"), 40) or "")
            if wl is not None and not _same_limit(lim, wl):
                st = "%s at %s" % (NOT_COMPUTED, wl_txt)
            elif ts1 is None:
                st = NOT_COMPUTED
            else:
                st = MET if ts1 >= T * (1.0 - TORQUE_TOL_REL) else "%s (%s)" % (NOT_MET, _pct_short(ts1, T))
            rows.append([s1_label, ach, src, st])

    # ── peak torque, and how long ───────────────────────────────────────
    pk = req.get("peak") if isinstance(req.get("peak"), Mapping) else None
    if pk and _num(pk.get("torque_Nm")) is not None:
        T, n, dur = _num(pk.get("torque_Nm")), _num(pk.get("speed_rpm")), _num(pk.get("duration_s"))
        label = "Peak %s @ %s" % (_fmt(T, 0, "N·m"), _fmt(n, 0, "rpm") if n else "—")
        c = match_duty(cols, n, T) if n is not None else (
            max(cols, key=lambda x: _torque(x) or 0.0) if cols else None)
        if c is None:
            rows.append([label, "no duty at this speed", "—", NOT_COMPUTED])
        else:
            t = _torque(c)
            ok = t is not None and t >= T * (1.0 - TORQUE_TOL_REL)
            rows.append([label, "%s (%s)" % (_fmt(t, 1, "N·m"), c.get("duty")),
                         "EM run %s, winding %s" % (_torque_basis(c),
                                                    _fmt(_num(_em(c).get("coil_temp_C")), 0, "°C")),
                         MET if ok else "%s (%s)" % (NOT_MET, _pct_short(t or 0.0, T))])
        if dur is not None:
            d_label = "Peak held %s, winding ≤ %s" % (_secs(dur), wp_txt)
            if c is None:
                rows.append([d_label, "—", "S2 time-to-limit", NOT_COMPUTED])
            else:
                t_s, lim, src = _s2_time(c)
                own = duty_s2_seconds(c)
                if t_s is None:
                    ach = ("duty defined for %s" % _secs(own)) if own is not None else "—"
                    rows.append([d_label, ach, "S2 time-to-limit", NOT_COMPUTED])
                else:
                    ach = "%s to %s" % (_secs(t_s), _fmt(lim, 0, "°C"))
                    if wp is not None and not _same_limit(lim, wp):
                        st = "%s at %s" % (NOT_COMPUTED, wp_txt)
                    else:
                        st = MET if t_s >= dur else NOT_MET
                        if "preliminary estimate" in src:
                            st = "%s (%s)" % (st, PRELIMINARY)
                    rows.append([d_label, ach, src, st])

    # ── the battery ─────────────────────────────────────────────────────
    rb = req.get("battery") if isinstance(req.get("battery"), Mapping) else None
    if rb and _num(rb.get("v_min")) is not None:
        vmin = _num(rb.get("v_min"))
        lvls = " / ".join(_fmt(_num(rb.get(k)), 0) for k in ("v_min", "v_nom", "v_max")
                          if _num(rb.get(k)) is not None)
        label = "Battery %s V; voltage at %s V minimum" % (lvls, _fmt(vmin, 0))
        peaks = []
        for c in cols:
            em = _em(c)
            v = _num(_g(em, "end3d.V_line_peak_corrected_V")) or _num(em.get("V_line_peak_V"))
            if v is not None:
                peaks.append((v, c.get("duty")))
        if not peaks:
            rows.append([label, "—", "EM run", NOT_COMPUTED])
        else:
            v, dn = max(peaks, key=lambda x: x[0])
            rows.append([label, "line peak %s (%s)" % (_fmt(v, 1, "V"), dn), "EM run, sinusoidal current",
                         MET if v < vmin else NOT_MET])

    # ── the ambient ─────────────────────────────────────────────────────
    amb = _num(req.get("ambient_max_c"))
    if amb is not None:
        from motor_ai_sim.report import ambient_c_of
        ambs = [a for a in (ambient_c_of((c.get("res") or {}).get("thermal")) for c in cols) if a is not None]
        label = "%s %s" % ({"liquid": "Coolant inlet", "submerged": "Water temperature"}.get(
            str(req.get("thermal_medium") or "air"), "Maximum ambient"), _fmt(amb, 0, "°C"))
        if not ambs:
            rows.append([label, "—", "thermal map", NOT_COMPUTED])
        else:
            lo = min(ambs)
            rows.append([label, "solved at %s" % _fmt(lo, 0, "°C"), "thermal map",
                         MET if lo >= amb - 0.5 else NOT_MET])

    # ── the overspeed ───────────────────────────────────────────────────
    ov = _num(req.get("overspeed_rpm"))
    if ov is not None:
        cell, sf = overspeed_cell(cols, ov)
        from motor_ai_sim.report import SF_ACCEPT
        rows.append(["Mechanics at %s overspeed" % _fmt(ov, 0, "rpm"), cell, "rotor stress",
                     NOT_COMPUTED if sf is None else (MET if sf >= SF_ACCEPT else NOT_MET)])

    if not rows:
        return []
    return [list(REQUIREMENTS_HEAD)] + rows


def overspeed_cell(cols: Sequence[Mapping[str, Any]], overspeed_rpm: Optional[float]
                   ) -> Tuple[str, Optional[float]]:
    """``("SF 3.4 at 1,200 rpm", 3.4)`` from a rotor-stress record solved at the overspeed, else ``("pending", None)``."""
    ov = _num(overspeed_rpm)
    if ov is None:
        return PENDING, None
    for c in cols:
        rs = (c.get("res") or {}).get("rotor_stress")
        if not isinstance(rs, Mapping):
            continue
        rpm = _num(rs.get("rpm")) or _num((rs.get("point") or {}).get("rpm"))
        if rpm is not None and rpm >= ov - 0.5:
            sf = _num(rs.get("sf_min")) or _num(rs.get("worst_sf"))
            if sf is not None:
                return "SF %s at %s" % (_fmt(sf, 2), _fmt(rpm, 0, "rpm")), sf
    return "%s (%s)" % (PENDING, _fmt(ov, 0, "rpm")), None


# ---------------------------------------------------------------------------
# bearing loads (OWNER_EDITS 5)
# ---------------------------------------------------------------------------

BEARING_LOADS_HEAD = ["Duty", "Speed", "Axial", "Radial", "L10h"]


def _gap_geometry(geo: Mapping[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(air-gap radius [m], magnetic gap [m], stack [m])."""
    ri = _num(geo.get("stator_inner_radius"))
    ro = _num(geo.get("rotor_outer_radius"))
    g = _num(geo.get("air_gap"))
    L = _num(geo.get("motor_length"))
    if ri is None and _num(geo.get("stator_diameter")) is not None and ro is not None and g is not None:
        ri = ro + g
    if g is None and ri is not None and ro is not None:
        g = ri - ro
    r = (0.5 * (ri + ro)) if (ri is not None and ro is not None) else (ri or ro)
    return (r / 1000.0 if r else None, g / 1000.0 if g else None, L / 1000.0 if L else None)


def unbalanced_magnetic_pull_n(b_gap_t: Any, r_gap_m: Any, stack_m: Any, ecc_ratio: Any) -> Optional[float]:
    """Linear UMP of a rotor offset by ``ecc_ratio`` of the gap: F = π R L B² ε / μ0 (Maxwell pressure, small ε)."""
    b, r, L, e = _num(b_gap_t), _num(r_gap_m), _num(stack_m), _num(ecc_ratio)
    if None in (b, r, L, e) or b <= 0 or r <= 0 or L <= 0 or e < 0:
        return None
    return math.pi * r * L * b * b * e / MU0


def magnetic_axial_force_n(b_gap_t: Any, r_gap_m: Any, gap_m: Any, offset_m: Any) -> Optional[float]:
    """Axial centring force of a rotor offset ``offset_m`` from the stator: the gap's magnetic energy per unit axial
    length, (B²/2μ0)·2πR·g, reached once the offset is about one gap and taken linear below it (an estimate)."""
    b, r, g, z = _num(b_gap_t), _num(r_gap_m), _num(gap_m), _num(offset_m)
    if None in (b, r, g, z) or b <= 0 or r <= 0 or g <= 0 or z < 0:
        return None
    return (b * b / (2.0 * MU0)) * 2.0 * math.pi * r * g * min(1.0, z / g)


def bearing_load_settings(cfg_doc: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    out = dict(BEARING_LOAD_DEFAULTS)
    own = (cfg_doc or {}).get("bearing_loads") if isinstance(cfg_doc, Mapping) else None
    for k in out:
        v = _num((own or {}).get(k)) if isinstance(own, Mapping) else None
        if v is not None and v >= 0:
            out[k] = v
    return out


def _rotating_kg(em: Mapping[str, Any]) -> Optional[float]:
    v = _num(em.get("rotating_mass_kg"))
    if v is not None:
        return v
    tot = 0.0
    hit = False
    for row in em.get("mass_components") or ():
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").lower()
        if any(w in name for w in ("rotor", "magnet", "shaft", "sleeve")):
            m = _num(row.get("mass_kg"))
            if m is not None:
                tot += m
                hit = True
    return tot if hit else None


def bearing_load_rows(req: Mapping[str, Any], cols: Sequence[Mapping[str, Any]], *,
                      geo: Mapping[str, Any], brg_assign: Optional[Mapping[str, Any]] = None,
                      cfg_doc: Optional[Mapping[str, Any]] = None) -> Tuple[List[List[str]], List[str]]:
    """``(rows, notes)``: the per-duty bearing loads, header included, and the one-line notes stating each input."""
    s = bearing_load_settings(cfg_doc)
    r_gap, gap, stack = _gap_geometry(geo or {})
    thrust = _num((req or {}).get("axial_thrust_N"))
    radial_cust = _num((req or {}).get("radial_load_N"))
    card = None
    try:
        from motor_ai_sim import bearings as _b
        if _b.has_bearings(dict(brg_assign or {})):
            name = _b.resolve_assignment(dict(brg_assign or {}))["A"].get("card")
            card = _b.get_bearing(str(name)) if name else None
    except Exception:                                           # noqa: BLE001
        card = None
    rows: List[List[str]] = [list(BEARING_LOADS_HEAD)]
    for c in cols:
        em = _em(c)
        rpm = _rpm(c)
        b = _num(em.get("B_gap_mean_T"))
        f_ax_mag = magnetic_axial_force_n(b, r_gap, gap, s["axial_offset_mm"] / 1000.0)
        ump = unbalanced_magnetic_pull_n(b, r_gap, stack, s["eccentricity_pct_of_gap"] / 100.0)
        m = _rotating_kg(em)
        w = (m * G0 * s["load_factor_g"]) if m is not None else None
        if thrust is None:
            ax = "%s + %s magnetic" % (CUSTOMER_INPUT_NEEDED, _fmt(f_ax_mag, 0, "N"))
            ax_n = None
        else:
            ax_n = thrust + (f_ax_mag or 0.0)
            ax = "%s (thrust %s + magnetic %s)" % (_fmt(ax_n, 0, "N"), _fmt(thrust, 0), _fmt(f_ax_mag, 0))
        rad_n = sum(x for x in (w, ump, radial_cust) if x is not None) if (w is not None or ump is not None) else None
        rad = "%s (weight %s + UMP %s%s)" % (_fmt(rad_n, 0, "N"), _fmt(w, 0), _fmt(ump, 0),
                                             (" + customer %s" % _fmt(radial_cust, 0)) if radial_cust else "")
        life = "no bearings assigned"
        if card is not None and _num(getattr(card, "C_kn", None)) and _num(getattr(card, "C0_kn", None)):
            if ax_n is None:
                life = CUSTOMER_INPUT_NEEDED
            elif rpm:
                from motor_ai_sim.bearing_fit import compute_l10_bearing_life
                l10 = compute_l10_bearing_life(card.C_kn, card.C0_kn, rpm, thrust_load_n=ax_n,
                                               radial_load_n=0.5 * (rad_n or 0.0))
                life = "%s h (%s)" % (_fmt(l10["single_bearing"]["l10h_hours"], 0), card.name)
        rows.append([str(c.get("duty") or ""), _fmt(rpm, 0, "rpm"), ax, rad, life])
    notes = [
        "Axial: propeller thrust %s (tilt-rotor: direction changes with the flight phase) + magnetic centring force "
        "at %s axial offset — an estimate." % ("from the customer specification" if thrust is not None
                                               else "— customer input needed", _fmt(s["axial_offset_mm"], 2, "mm")),
        "Radial: rotating mass × %s g + unbalanced magnetic pull at %s %% of the %s gap eccentricity — an estimate "
        "from the mean air-gap |B|%s."
        % (_fmt(s["load_factor_g"], 1), _fmt(s["eccentricity_pct_of_gap"], 0),
           _fmt((gap or 0) * 1000.0, 2, "mm"), "" if radial_cust is None else " + customer radial load"),
        "L10h: ISO 281, one bearing taking the whole thrust.",
    ]
    return rows, notes


# ---------------------------------------------------------------------------
# the appendix: short record ids (owner 2026-10-26, H07 / L01)
# ---------------------------------------------------------------------------

RECORD_IDS_HEADING = "Record ids"
RECORD_IDS_HEAD = ["Duty", "EM run", "Thermal", "Coupled loop", "Rotor stress"]
RECORD_IDS_NOTE = ("Record id = the first 8 characters of the record's mesh-independent geometry print "
                   "(* = mesh-dependent print); equal ids = one geometry.")


def _short_id(rec: Any) -> str:
    if not isinstance(rec, Mapping):
        return "—"
    v2 = rec.get("geometry_fingerprint_v2")
    if v2:
        return str(v2)[:8]
    v1 = rec.get("geometry_fingerprint") or rec.get("geo_fingerprint")
    return (str(v1)[:8] + "*") if v1 else "—"


def record_id_rows(cols: Sequence[Mapping[str, Any]]) -> List[List[str]]:
    """The appendix table: one short id per duty and record kind, header included; empty without duties."""
    if not cols:
        return []
    rows: List[List[str]] = [list(RECORD_IDS_HEAD)]
    for c in cols:
        res = c.get("res") or {}
        em_id = (str(c.get("fp2"))[:8] if c.get("fp2") else
                 (str(c.get("fp"))[:8] + "*" if c.get("fp") else "—"))
        rows.append([str(c.get("duty") or ""), em_id, _short_id(res.get("thermal")),
                     _short_id(res.get("coupled")), _short_id(res.get("rotor_stress"))])
    return rows
