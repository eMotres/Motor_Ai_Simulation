"""Existing sizes a customer may pick in the specification (owner 2026-11-25, 4_1 ``od_mode`` = "existing").

When the diameter is not decisive for a customer the calculation can start from an existing lamination, which is ready much sooner
than a full geometry optimisation for a custom diameter.  The specification form lists the sizes that exist: stator outer diameter,
slots / poles and the available stack lengths, sorted by closeness to the preferred diameter, and the customer ticks the sizes he
accepts (``models.od_choice`` reads them back for the computing session).

PUBLIC DATA ONLY.  The list is built the way the landing page's public motors are (``public_motors``): the tree a freshly registered
account without grants may open (the public dies and configurations and the shared baseline die; archived and retired ones are not in
it), only configurations with a catalogue card, read key by key through the allow-list ``SIZE_KEYS``.  No die or configuration name,
no project, owner, customer, geometry or result is in an answer, and the answer is the same for every caller.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim.development import access, store

log = logging.getLogger(__name__)

#: The ONLY keys of one size row.
SIZE_KEYS = ("od_mm", "slots", "poles", "stack_mm")

#: Mounted by ``productions.router`` (prefix ``/api/projects/{project_id}``): the same project-member gate as the specification.
router = APIRouter(tags=["development-spec-sizes"])


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) and x > 0 else None


def _int(v: Any) -> Optional[int]:
    x = _num(v)
    return int(round(x)) if x is not None else None


def _round(x: float) -> float:
    return round(x, 1)


def build_sizes(dies: Iterable[Dict[str, Any]], *, archived=None) -> List[Dict[str, Any]]:
    """One row per (outer diameter, slots, poles) of a tree already filtered to the public view (pure).

    A configuration without a catalogue card (no datasheet yet) or archived is not counted; a die left with no configuration is not
    listed.  ``stack_mm`` is the sorted list of the stack lengths of the counted configurations."""
    rows: Dict[tuple, Dict[str, Any]] = {}
    for d in dies:
        if d.get("layer") not in (None, "shared"):     # the caller's own workspace copies and the community layer are not published
            continue
        f = d.get("fields") if isinstance(d.get("fields"), dict) else {}
        od = _num(d.get("stator_diameter", f.get("stator_diameter")))
        slots = _int(d.get("slots", f.get("slots")))
        poles = _int(d.get("poles", f.get("poles")))
        if od is None or slots is None or poles is None:
            continue
        for c in d.get("configs") or []:
            if not isinstance(c, dict) or not c.get("card_date"):
                continue
            if archived is not None and archived(str(d.get("name") or ""), str(c.get("name") or "")):
                continue
            row = rows.setdefault((_round(od), slots, poles),
                                  {"od_mm": _round(od), "slots": slots, "poles": poles, "stack_mm": []})
            stack = _num(c.get("stack_mm"))
            if stack is not None and _round(stack) not in row["stack_mm"]:
                row["stack_mm"].append(_round(stack))
    out = list(rows.values())
    for r in out:
        r["stack_mm"].sort()
    return sorted(out, key=lambda r: (r["od_mm"], r["poles"], r["slots"]))


def assert_allowlisted(rows: List[Dict[str, Any]]) -> None:
    for r in rows:
        if tuple(r) != SIZE_KEYS:
            raise AssertionError(f"size row outside the allow-list: {sorted(r)}")


def select(rows: List[Dict[str, Any]], *, preferred_min: Optional[float] = None, preferred_max: Optional[float] = None,
           max_od: Optional[float] = None) -> List[Dict[str, Any]]:
    """The rows within the hard maximum diameter (when given), sorted by distance to the preferred diameter or range (0 inside
    the range); without a preference they stay smallest first.  Pure; the input is not changed."""
    keep = [dict(r, stack_mm=list(r["stack_mm"])) for r in rows if max_od is None or r["od_mm"] <= max_od]
    lo = preferred_min if preferred_min is not None else preferred_max
    hi = preferred_max if preferred_max is not None else preferred_min
    if lo is None or hi is None:
        return keep
    lo, hi = min(lo, hi), max(lo, hi)
    mid = (lo + hi) / 2
    return sorted(keep, key=lambda r: (0.0 if lo <= r["od_mm"] <= hi else min(abs(r["od_mm"] - lo), abs(r["od_mm"] - hi)),
                                       abs(r["od_mm"] - mid), r["poles"], r["slots"]))


# -- live data (the public view, as public_motors.public_motors) --------------------------------------------------

# No row cache of our own (Astra #893): a cached list kept archived and later-private sizes for the whole TTL.  Every request builds
# the list from ``family.tree_for_access``, which follows grants, visibility and archive state of the moment.

def public_sizes() -> List[Dict[str, Any]]:
    """Every existing public size, built fresh from the public view on each call."""
    from contextlib import nullcontext
    from fastapi import Response
    from motor_ai_sim import config_archive as CA
    from motor_ai_sim import public_motors as PM
    from motor_ai_sim import support_catalog as SC
    from motor_ai_sim import workspace as WS
    from motor_ai_sim.routes import family as fam
    acc = SC._visitor_access()
    neutral = SC._neutral_workspace()
    with (WS.use_workspace(neutral) if neutral is not None else nullcontext()):
        dies = list((fam.tree_for_access(Response(), acc, with_mass=False) or {}).get("dies") or [])
        rows = build_sizes(dies, archived=CA.is_archived)
    assert_allowlisted(rows)
    PM.assert_clean(rows)
    return rows


def _opt(request: Request, name: str) -> Optional[float]:
    raw = request.query_params.get(name)
    try:
        x = float(raw) if raw not in (None, "") else None
    except ValueError:
        return None
    return x if x is not None and x == x and abs(x) != float("inf") and x > 0 else None


@router.get("/spec/existing-sizes")
def existing_sizes(request: Request, project_id: str):
    """The public existing sizes for this project's form, closest to ``preferred_min_mm`` .. ``preferred_max_mm`` first, none above
    ``max_mm``.  Project members only (like the specification itself); the answer is the same public list for every member."""
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    access.require_project(request, project_id)
    try:
        rows = public_sizes()
    except Exception as exc:                                    # noqa: BLE001 - the form still works without the list
        log.warning("existing sizes unavailable (%s: %s)", type(exc).__name__, exc)
        return {"sizes": [], "available": False}
    return {"sizes": select(rows, preferred_min=_opt(request, "preferred_min_mm"), preferred_max=_opt(request, "preferred_max_mm"),
                            max_od=_opt(request, "max_mm")), "available": True}
