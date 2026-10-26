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
INTRO = "Computed results that are not in this report yet, by duty. Stale results are not printed as current."
NONE_LINE = "Every result this report is made of is computed."

ITEM_EM = "Electromagnetic run"
ITEM_COUPLED = "Coupled thermal loop at the duty's own point"
ITEM_S1 = "Continuous (S1) rating"
ITEM_MECH = "Mechanics with magnetic pull"
REASON_MISSING = "to be computed"
REASON_SUPERSEDED = "the stored result was made by a superseded solver (before the contact repair); to be computed again"
REASON_GEOMETRY = "the stored result is of another geometry; to be computed again"
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
    worst = motor_view.worst_case_rotor_stress(stored)
    if worst is None:
        rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_MISSING})
    else:
        if worst.get("solver_status") == "superseded":
            rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_SUPERSEDED})
        if worst.get("geometry_status") == "differs":
            rows.append({"duty": MOTOR_LEVEL, "item": ITEM_MECH, "reason": REASON_GEOMETRY})
    return rows


def as_lines(rows: List[Dict[str, str]]) -> List[str]:
    """One printable line per row: ``<duty>: <item> - <reason>``."""
    return [f"{r['duty']}: {r['item']} - {r['reason']}" for r in rows]


RECOMMENDED_LINE = "Recommended variant of project {project} (marked by our engineers)"


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
