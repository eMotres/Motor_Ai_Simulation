"""The controller's PWM final pass of a coupled ``drive: "inverter"`` run —
what it may hand to the thermal solve, which pass the record is judged on, and
which way the bridge's power flows.  Pure functions: no solve, no I/O.

WHY (CR-HF-75 G1-L170 generator, 4000 rpm, IMCQ120R007M2H x2 on 750 V /
20 kHz, two server runs 2026-10-29).  Three defects, one per section below:

1. ROTOR LOSSES FROM AN OPEN WINDOW.  The PWM pass is voltage-fed, so it is
   marched (TDM does not take a circuit state) with no demag pre-pass, and on
   that march the rotor iron's commensurate window did not close
   (``P_core_terms.rotor.window_closed: false``).  Its raw one-period DFT bills
   the end-to-start step of every rotor element as broadband loss — 639 W of
   rotor iron against 120 W from the same window detrended (663.7 / 127.8 W at
   254 A) — and the thermal step put the magnets at 238.6 / 293 °C against
   121 / 168 °C on the sine passes.  ``rotor_iron_selection`` decides which
   candidate the thermal solve may see; the raw open window is never one.
2. LIMITS JUDGED ON THE WRONG PASS.  ``time_to_limit`` was formed on the last
   SINE pass and kept after the PWM pass moved the machine: magnets at 240 °C
   (limit 180) still read "may be held for ever".  ``judge_final_state`` puts
   the verdict on the pass the record reports last.
3. A GENERATOR SOLVED AS A MOTOR.  The controller request never carried a
   direction, so a generating point (``P_elec_in_W < 0``) was billed in motor
   direction: ``power_direction: motor``, ``dc_link.power_flow: link ->
   machine``.  ``bridge_power_direction`` reads it off the machine's own
   terminal power.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

#: Which rotor-iron number the PWM pass hands to the thermal solve, best first.
#: ``pwm_closed_window`` — the PWM pass's own commensurate (closed) window, the
#: exact answer; ``sine_state_closed_window`` — the converged sine state's
#: closed window at the same operating point (settled, demag pre-passed, but
#: without the carrier's rotor share); ``pwm_detrended_open_window`` — the PWM
#: pass's own open window with the legacy linear ramp removed (the carrier is
#: in it, the ramp removal is a filter).  When the PWM window is open the LARGER
#: of the last two is used: for a temperature the conservative of two settled
#: readings, never the raw open window.
ROTOR_IRON_CANDIDATES = ("pwm_closed_window", "sine_state_closed_window",
                         "pwm_detrended_open_window")

#: Summary keys that are a sum containing the rotor iron loss, moved by the
#: same watts when the selected candidate replaces the raw one.
_SUMMARY_LOSS_KEYS = ("P_core_W", "P_loss_total_W", "P_loss_rotor_W",
                      "P_loss_total_incl_mech_W", "P_elec_in_W")
#: …and the same in the field2d loss-map payload the thermal solve reads.
_MAP_LOSS_KEYS = ("P_fe_W", "P_fe_exact_W", "P_loss_total_W",
                  "P_loss_total_exact_W")


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rotor_terms(summary: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    terms = (summary or {}).get("P_core_terms") or {}
    rot = terms.get("rotor") if isinstance(terms, Mapping) else None
    return dict(rot) if isinstance(rot, Mapping) else {}


def _flag(summary: Optional[Mapping[str, Any]], key: str) -> Optional[bool]:
    v = (summary or {}).get(key)
    return None if v is None else bool(v)


# ---------------------------------------------------------------------------
# 1. The rotor iron the thermal solve may see
# ---------------------------------------------------------------------------

def rotor_iron_selection(pwm_summary: Optional[Mapping[str, Any]],
                         sine_summary: Optional[Mapping[str, Any]]
                         ) -> Dict[str, Any]:
    """Which rotor-iron loss the PWM pass reports and hands to the thermal solve.

    Returns ``{"candidate", "raw_W", "selected_W", "factor", "window_closed",
    "settled", "refuse", "reason", ...}``.  ``factor`` is ``selected / raw`` —
    what the rotor-iron elements of the loss map and the summary's sums are
    moved by.  ``refuse`` is True when the window is open and no settled
    candidate exists: the caller then refuses the pass rather than solve a
    temperature field on the open window's watts.
    """
    rot = _rotor_terms(pwm_summary)
    raw = _num((pwm_summary or {}).get("P_core_rotor_W"))
    out: Dict[str, Any] = {
        "raw_W": raw, "selected_W": raw, "factor": 1.0, "candidate": None,
        "window_closed": (None if "window_closed" not in rot
                          else bool(rot["window_closed"])),
        "window_method": rot.get("window_method"),
        "surface_selected_candidate": rot.get("surface_selected_candidate"),
        "settled": None, "refuse": False,
        # The rest of the rotor's settling, stated beside the iron: the PWM
        # pass is a voltage-fed march, so the demag ratchet runs on its
        # settling prefix only and the record says whether that settled.
        "demag_settled": _flag(pwm_summary, "demag_settled"),
        "eddy_settled": _flag(pwm_summary, "eddy_settled"),
        "sine_demag_settled": _flag(sine_summary, "demag_settled"),
    }
    if not rot or raw is None:
        out["reason"] = ("the PWM pass reports no per-half core-loss split "
                         "(P_core_terms.rotor / P_core_rotor_W), so its rotor "
                         "window cannot be checked; the map is used as solved")
        return out
    if raw <= 0.0:
        out.update(candidate="no_rotor_iron_loss", settled=True,
                   reason="the PWM pass bills no rotor iron loss")
        return out
    if out["window_closed"]:
        out.update(candidate="pwm_closed_window", settled=True,
                   reason=("the PWM pass's own rotor window is closed (%s)"
                           % (rot.get("window_method") or "commensurate")))
        return out

    options = []
    s_rot = _rotor_terms(sine_summary)
    sine_W = _num((sine_summary or {}).get("P_core_rotor_W"))
    if s_rot.get("window_closed") and sine_W is not None and sine_W >= 0.0:
        options.append((sine_W, "sine_state_closed_window"))
    out["sine_closed_window_W"] = (sine_W if s_rot.get("window_closed")
                                   else None)
    det = _num(rot.get("surface_detrended_candidate_W"))
    raw_c = _num(rot.get("surface_raw_window_candidate_W"))
    det_W = (raw * det / raw_c) if (det is not None and det >= 0.0
                                    and raw_c is not None and raw_c > 0.0) \
        else None
    out["pwm_detrended_W"] = None if det_W is None else round(det_W, 3)
    if det_W is not None:
        options.append((det_W, "pwm_detrended_open_window"))
    if not options:
        out.update(settled=False, refuse=True, reason=(
            "the PWM pass's rotor window is OPEN (%s) and there is no settled "
            "candidate to replace it — neither the sine state's closed window "
            "nor a detrended PWM reading; the open window's raw %.1f W is not "
            "fed to the thermal solve" % (rot.get("window_method") or "open",
                                          raw)))
        return out
    # max() keeps the first of equals: the sine state wins a tie.
    sel_W, cand = max(options, key=lambda o: o[0])
    out.update(candidate=cand, selected_W=round(sel_W, 3),
               factor=sel_W / raw, settled=True, reason=(
                   "the PWM pass's rotor window is OPEN (%s): its raw %.1f W "
                   "bills the window's end-to-start step as loss, so the larger "
                   "of the settled candidates is used — %s"
                   % (rot.get("window_method") or "open", raw, "; ".join(
                       "%s %.1f W%s" % (c, w, " (selected)" if c == cand
                                        else "")
                       for w, c in options))))
    return out


def apply_to_summary(summary: Mapping[str, Any],
                     sel: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of the PWM pass's summary carrying the SELECTED rotor iron — the
    sums it is part of and the efficiencies built on them moved with it — and
    the raw open-window value beside it under its own name."""
    out = dict(summary or {})
    out["P_core_rotor_selection"] = dict(sel)
    raw, new = _num(sel.get("raw_W")), _num(sel.get("selected_W"))
    if raw is None or new is None or abs(new - raw) < 1e-9:
        return out
    delta = new - raw
    out["P_core_rotor_open_window_raw_W"] = raw
    out["P_core_rotor_W"] = round(new, 1)
    for k in _SUMMARY_LOSS_KEYS:
        v = _num(out.get(k))
        if v is not None:
            out[k] = round(v + delta, 1)
    # The efficiencies, by the summary's own definitions (routes/simulation.py
    # `_build_transient_summary` / `_mech_summary`): P_mech_W is a magnitude in
    # both directions, the sign of the shaft power names the mode.
    p_mech = _num(out.get("P_mech_W"))
    p_loss = _num(out.get("P_loss_total_W"))
    gen = str(out.get("op_mode") or "").lower().startswith("gen")
    if out.get("efficiency") is not None and p_mech is not None \
            and p_loss is not None and p_mech > 0.0:
        if gen:
            eff = ((p_mech - p_loss) / p_mech) if p_mech > max(p_loss, 1.0) \
                else 0.0
        else:
            eff = (p_mech / (p_mech + p_loss)) if (p_mech + p_loss) > 1.0 \
                else 0.0
        out["efficiency"] = round(eff, 4)
        extra = _num(out.get("P_mech_extra_W"))
        if out.get("efficiency_shaft") is not None and extra is not None:
            x = extra / p_mech
            out["efficiency_shaft"] = round(
                eff / (1.0 + x) if gen else eff * max(0.0, 1.0 - x), 6)
    return out


def scale_map_rotor_iron(em_map: Mapping[str, Any], sel: Mapping[str, Any], *,
                         rotor_tag: int) -> Dict[str, Any]:
    """A shallow copy of the loss map with the rotor-iron elements carrying the
    selected watts — ONE factor over the rotor core (the per-element shape of
    the solved window kept), the map's totals moved by the same watts.  The
    cached map itself is never touched."""
    out = dict(em_map or {})
    f = _num(sel.get("factor"))
    if f is None or abs(f - 1.0) < 1e-12:
        return out
    tags = list(out.get("domain_per_tri") or [])
    ld = list(out.get("loss_density_per_tri") or [])
    if ld and len(tags) == len(ld):
        out["loss_density_per_tri"] = [
            (float(d) * f if int(t) == int(rotor_tag) else d)
            for d, t in zip(ld, tags)]
    raw, new = _num(sel.get("raw_W")), _num(sel.get("selected_W"))
    if raw is not None and new is not None:
        for k in _MAP_LOSS_KEYS:
            v = _num(out.get(k))
            if v is not None:
                out[k] = v + (new - raw)
    out["rotor_iron_selection"] = {
        "candidate": sel.get("candidate"), "factor": round(f, 6),
        "raw_W": raw, "selected_W": new}
    return out


# ---------------------------------------------------------------------------
# 2. The verdict on the pass the record reports last
# ---------------------------------------------------------------------------

def _verdict(blk: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if not blk:
        return None
    return {k: blk.get(k) for k in ("within_limits", "limiting_part",
                                    "over_parts", "over_by_K", "at_point_c",
                                    "limits_c", "time_to_limit_s", "note")
            if blk.get(k) is not None}


def _direct_over(limits_c: Mapping[str, Any],
                 components: Mapping[str, Any]) -> Dict[str, float]:
    """Parts whose hottest element on the map is over its limit — the fallback
    when the PWM map could not be fitted for a time to the limit."""
    over: Dict[str, float] = {}
    for part, lim in (limits_c or {}).items():
        c = (components or {}).get(part)
        t = _num((c or {}).get("max")) if isinstance(c, Mapping) else None
        lv = _num(lim)
        if t is not None and lv is not None and t > lv:
            over[str(part)] = round(t - lv, 2)
    return over


def judge_final_state(final_ttl: Optional[Mapping[str, Any]],
                      sine_ttl: Optional[Mapping[str, Any]], *,
                      final_pass: str,
                      final_field: Optional[Mapping[str, Any]] = None
                      ) -> Optional[Dict[str, Any]]:
    """The ``time_to_limit`` block of the record, judged on ``final_pass``.

    ``final_ttl`` is the block formed on the final pass's own map and summary;
    the sine state's verdict rides beside it as ``sine_state``.  When the final
    map could not be judged, the sine block is NOT re-used as the verdict: its
    limits are read against the final map's hottest elements, and any part
    over its limit makes ``within_limits`` False with the parts named.
    """
    if final_ttl:
        out = dict(final_ttl)
        out["judged_on"] = final_pass
        sv = _verdict(sine_ttl)
        if sv is not None:
            out["sine_state"] = sv
            if sv.get("within_limits", True) and not out.get(
                    "within_limits", True):
                out["note"] = (
                    "%s — on the %s pass; the sine state was inside every limit"
                    % (str(out.get("note") or "over a limit"), final_pass))
        return out
    if not sine_ttl:
        return None
    out = dict(sine_ttl)
    out["judged_on"] = "sine_state"
    over = _direct_over(sine_ttl.get("limits_c") or {},
                        (final_field or {}).get("components") or {})
    out["final_pass_check"] = {
        "pass": final_pass, "over_by_K": over,
        "note": ("the %s pass's map could not be fitted for a time to the "
                 "limit; its hottest elements were read against the sine "
                 "state's limits" % final_pass)}
    if over:
        out.update(within_limits=False, over_parts=sorted(over),
                   over_by_K=over, limiting_part=max(over, key=over.get),
                   time_to_limit_s=None, note=(
                       "on the %s pass %s over the limit (%s); no time to the "
                       "limit could be formed on that map"
                       % (final_pass, ", ".join(sorted(over)),
                          ", ".join("%s +%.1f K" % (p, k)
                                    for p, k in sorted(over.items())))))
    return out


def over_limit_parts(ttl: Optional[Mapping[str, Any]]) -> Iterable[str]:
    if not ttl or ttl.get("within_limits", True):
        return []
    return list(ttl.get("over_parts") or ([ttl["limiting_part"]]
                                          if ttl.get("limiting_part") else []))


# ---------------------------------------------------------------------------
# 3. Which way the bridge's power flows
# ---------------------------------------------------------------------------

def bridge_power_direction(summary: Optional[Mapping[str, Any]],
                           body_mode: Optional[str] = None
                           ) -> Tuple[str, str]:
    """``(direction, source)`` — "generator" when the machine delivers power to
    the bridge, "motor" when it draws it.

    The machine's own terminal power decides (consumer arrows: ``P_elec_in_W``
    < 0 is power OUT of the machine, into the link); then the summary's
    ``op_mode`` (the sign of its shaft power); the duty's ``mode`` only when the
    pass carries neither.  A generator duty whose point actually motors is a
    motor to the bridge, and the other way round.
    """
    s = summary or {}
    for key in ("P_elec_in_W", "P_elec_in_solved_W"):
        p = _num(s.get(key))
        if p is not None and p != 0.0:
            return (("generator" if p < 0.0 else "motor"),
                    "the machine's terminal power %s = %.4g W (%s)"
                    % (key, p, "machine -> link" if p < 0.0
                       else "link -> machine"))
    op = str(s.get("op_mode") or "").strip().lower()
    if op:
        return (("generator" if op.startswith("gen") else "motor"),
                "the pass's op_mode (the sign of its shaft power)")
    m = str(body_mode or "").strip().lower()
    if m:
        return (("generator" if m.startswith("gen") else "motor"),
                "the duty's mode (the pass carries no power flow)")
    return "motor", "no power flow on the pass and no duty mode: motor"
