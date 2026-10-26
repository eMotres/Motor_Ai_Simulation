"""The "Still to compute" section of the motor report (owner 2026-10-26).

A project variant may be only partly computed: the electromagnetic point of a duty exists, the coupled thermal loop, the mechanics or
the continuous rating does not.  The report is built from what exists and never refuses because something is missing; this module
lists, ONCE, what is missing and for which duty, and what is stored but not current (a rotor-stress answer made by a superseded
solver, or on another geometry) - which is never printed as current.

Pure reading of the stores (``duty_results``); nothing is solved or written.  Returns plain rows so the PDF and the Word builders
print the same list.
"""
from __future__ import annotations

from typing import Any, Dict, List

#: Row texts (English source; the report is an English document).
HEADING = "Still to compute"
INTRO = "Results this report does not contain yet, by duty; a superseded result is never printed as current."
NONE_LINE = "Nothing is pending for this variant."

ITEM_EM = "Electromagnetic run"
ITEM_COUPLED = "Coupled EM-thermal steady state at the duty's own point"
ITEM_S1 = "Continuous (S1) rating"
ITEM_MECH = "Mechanics with magnetic pull"
REASON_MISSING = "not computed yet"
REASON_SUPERSEDED = "the stored result predates the contact-solver repair; to be recomputed"
REASON_GEOMETRY = "the stored result is of another geometry; to be recomputed"
MOTOR_LEVEL = "all duties (the rotor stress is one answer for the motor)"


def still_to_compute(die: str, cfg: str, cfg_doc: Dict[str, Any]) -> List[Dict[str, str]]:
    """``[{"duty", "item", "reason"}, ...]`` - what the variant still needs, in duty order, motor-level items last."""
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.development import motor_view

    try:
        stored = dr.get(die, cfg) or {}
    except Exception:                                       # noqa: BLE001 - a store that cannot be read means "nothing stored"
        stored = {}
    rows: List[Dict[str, str]] = []
    for d in (cfg_doc.get("duties") or []):
        if not isinstance(d, dict) or not d.get("name"):
            continue
        name = str(d["name"])
        kinds = stored.get(name) or {}
        if not (d.get("result") or d.get("summary")):
            rows.append({"duty": name, "item": ITEM_EM, "reason": REASON_MISSING})
        coupled = kinds.get("coupled") if isinstance(kinds.get("coupled"), dict) else None
        if coupled is None or dr.is_limit_search(coupled):
            rows.append({"duty": name, "item": ITEM_COUPLED, "reason": REASON_MISSING})
        has_s1 = any(isinstance(r, dict) and r.get("continuous_rating")
                     for r in (kinds.get("coupled_limit"), coupled, kinds.get("continuous_rating"))) \
            or bool(kinds.get("continuous_rating"))
        if not has_s1:
            rows.append({"duty": name, "item": ITEM_S1, "reason": REASON_MISSING})
    # COUPLED RESULTS MADE BEFORE THE OPERATING-POINT STAMP (owner 2026-10-26): they cannot be tied to their duty and
    # are not used by the report - said ONCE, for every duty that has one
    unstamped = []
    for name, kinds in stored.items():
        for k in ("coupled", "coupled_limit"):
            row = (kinds or {}).get(k)
            if isinstance(row, dict) and row:
                st = motor_view.record_stamp(k, row)
                if not st["rpm"] and not st["current"]:
                    unstamped.append(str(name))
                    break
    if unstamped:
        rows.append({"duty": ", ".join(sorted(unstamped)), "item": ITEM_UNSTAMPED, "reason": REASON_UNSTAMPED})
    rows += _requirement_rows(die, cfg, cfg_doc, stored)
    worst = motor_view.worst_case_rotor_stress(stored)
    if worst is None:
        rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_MISSING})
    else:
        if worst.get("solver_status") == "superseded":
            rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_SUPERSEDED})
        if worst.get("geometry_status") == "differs":
            rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_GEOMETRY})
    return rows


ITEM_UNSTAMPED = "Coupled results made before the operating-point stamp"
REASON_UNSTAMPED = "not used in this report (no stated point); to be re-run"
ITEM_OVERSPEED = "Rotor stress at the {rpm} rpm overspeed"
ITEM_S1_LIMIT = "Continuous (S1) rating at the {lim} °C winding limit"
ITEM_S2_LIMIT = "Peak held {dur} s at the {lim} °C winding limit (S2)"
REASON_OTHER_LIMIT = "the stored result is at {got} °C"


def _num(v: Any):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _requirement_rows(die: str, cfg: str, cfg_doc: Dict[str, Any], stored: Dict[str, Any]) -> List[Dict[str, str]]:
    """What the CUSTOMER's requirements still need (owner 2026-10-26): the overspeed case, and the S1 / S2 answers
    at the project's own winding limit when the stored ones were judged at another one."""
    rows: List[Dict[str, str]] = []
    try:
        from motor_ai_sim import report_customer as rc
        from motor_ai_sim import winding_limit as wl
        req = rc.project_requirements(die, cfg, cfg_doc)
        lim = wl.config_winding_limit_c(cfg_doc)
    except Exception:                                       # noqa: BLE001
        return rows
    ov = _num(req.get("overspeed_rpm"))
    if ov is not None:
        done = any(isinstance((k or {}).get("rotor_stress"), dict)
                   and (_num(k["rotor_stress"].get("rpm")) or 0.0) >= ov - 0.5 for k in stored.values())
        if not done:
            rows.append({"duty": MOTOR_LEVEL, "item": ITEM_OVERSPEED.format(rpm=f"{ov:,.0f}"),
                         "reason": REASON_MISSING})
    if lim is None:
        return rows
    for name, kinds in stored.items():
        for k in ("coupled_limit", "coupled"):
            blk = ((kinds or {}).get(k) or {}).get("continuous_rating") if isinstance((kinds or {}).get(k), dict) else None
            got = _num(((blk or {}).get("limits_c") or {}).get("winding")) if isinstance(blk, dict) else None
            if got is not None and abs(got - lim) > 0.5:
                rows.append({"duty": str(name), "item": ITEM_S1_LIMIT.format(lim=f"{lim:g}"),
                             "reason": REASON_OTHER_LIMIT.format(got=f"{got:g}")})
                break
    pk = req.get("peak") if isinstance(req.get("peak"), dict) else {}
    dur = _num(pk.get("duration_s"))
    if dur is not None:
        cols = [{"duty": n, "d": d, "em": (d.get("summary") or {}), "res": stored.get(str(d.get("name"))) or {}}
                for n, d in ((str(x.get("name")), x) for x in (cfg_doc.get("duties") or []) if isinstance(x, dict))]
        c = rc.match_duty(cols, _num(pk.get("speed_rpm")), _num(pk.get("torque_Nm")))
        if c is not None:
            t, got, _src = rc._s2_time(c)
            if t is None or got is None or abs(got - lim) > 0.5:
                rows.append({"duty": str(c["duty"]), "item": ITEM_S2_LIMIT.format(dur=f"{dur:g}", lim=f"{lim:g}"),
                             "reason": (REASON_OTHER_LIMIT.format(got=f"{got:g}") if got is not None
                                        else REASON_MISSING)})
    return rows


def as_lines(rows: List[Dict[str, str]]) -> List[str]:
    """One printable line per row: ``<duty>: <item> - <reason>``."""
    return [f"{r['duty']}: {r['item']} - {r['reason']}" for r in rows]


RECOMMENDED_LINE = "Recommended variant of project {project}"


def recommended_banner(die: str, cfg: str) -> str:
    """The title-page line of the variant the staff marked as the project's recommended one; '' for any other variant
    (no automatic pick: an unmarked project has no recommended variant on its reports)."""
    try:
        from motor_ai_sim.development import motors, store
        state = motors.report_link_state(die, cfg)
        if not state or not isinstance((state["link"] or {}).get("recommended"), dict):
            return ""
        project = store.get_project(state["project_id"]) or {}
        return RECOMMENDED_LINE.format(project=str(project.get("title") or state["project_id"]))
    except Exception:                                       # noqa: BLE001
        return ""
