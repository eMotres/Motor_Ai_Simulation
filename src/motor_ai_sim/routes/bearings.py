"""Bearing routes — /api/bearings.

Two GETs and nothing else:

  * ``/library``  — the whole catalogue (``config/bearings_library.yaml``), so
    the Mechanical tab's pickers and the datasheet name real parts;
  * ``/losses``   — the mechanical loss of ONE machine at one speed: two
    bearings by the SKF frictional-moment model plus rotor windage.

SOLVER ISOLATION (the rule ``routes/thermal.py`` states at length, and this
router keeps): **nothing here solves a field, and nothing here writes.**  It is
arithmetic over a catalogue, a geometry read-only, and an operating point the
caller states.  It never starts an electromagnetic transient, never touches the
thermal or mechanical caches, and never writes a die file — the ONLY writer of a
machine's ``bearings`` block is
``routes/family.py::set_bearings`` (PATCH), pressed by the user.

WHY A ROUTE *AS WELL AS* A FIELD ON THE SUMMARY: the bearings belong to the
MACHINE and the losses to the OPERATING POINT, so recomputing them from a card at
whatever rpm the summary is showing is milliseconds — and it means an assignment
changed in the Mechanical tab moves the Electromagnetic tab's loss picture on the
next render instead of after a six-minute re-run.  That is what this route is
for: PREVIEWING a pair, and answering for a speed nothing has been solved at.

Since 2026-09-08 it is no longer the ONLY place those watts exist.  User: *"все
потери должны передаваться в электромагнитный расчёт"* — every stored run of a
machine with bearings carries them in its own summary (``routes.simulation``),
the thermal solve takes the friction as a heat source, and the coupled loop
iterates the bearing temperature with the rest.  All four go through ONE
implementation, ``motor_ai_sim.mech_losses``, which is where the helpers this
module used to own now live.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from motor_ai_sim import mech_losses as ml

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bearings", tags=["bearings"])


# ---------------------------------------------------------------------------
# Resolving WHICH machine — the shared implementation, under this module's own
# names so nothing that imported them (tests included) had to move.
# ---------------------------------------------------------------------------

def _machine_bearings(die: Optional[str],
                      cfg: Optional[str]) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """``(assignment, die, config)`` — see ``mech_losses.machine_bearings``."""
    return ml.machine_bearings(die, cfg)


def _live_geometry() -> Dict[str, Any]:
    """The loaded machine's geometry — see ``mech_losses.live_geometry``."""
    return ml.live_geometry()


def _rotor_mass_kg(geo: Dict[str, Any]) -> Optional[float]:
    """Rotating mass [kg] — see ``mech_losses.rotor_mass_kg``."""
    return ml.rotor_mass_kg(geo)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/library")
def get_library() -> Dict[str, Any]:
    """Every card and lubricant in ``config/bearings_library.yaml``.

    The loader follows the file's mtime (``bearings._load``), so a card
    corrected on disk is served on the next request — no restart, exactly like
    the materials library.
    """
    from motor_ai_sim import bearings as brg
    try:
        return brg.library()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/losses")
def get_losses(
    rpm: float = Query(..., description="shaft speed [rpm]"),
    temp_c: float = Query(70.0, description="bearing temperature [degC]"),
    rotor_mass_kg: Optional[float] = Query(
        None, description="rotating mass [kg]; measured from the CAD when omitted"),
    preload_n: Optional[float] = Query(
        None, description="axial preload per bearing [N]; the machine's own when omitted"),
    lubrication: Optional[str] = Query(
        None, description="'grease' | 'oil_air'; the machine's own when omitted"),
    # The Mechanical tab must be able to TRY a bearing before committing it to
    # the machine — otherwise the only way to see what a card costs is to save
    # it onto the die first, which is the wrong order for a design decision.
    # These override the assignment for this request only; nothing is written.
    card_a: Optional[str] = Query(None, description="preview: override bearing A"),
    card_b: Optional[str] = Query(None, description="preview: override bearing B"),
    die: Optional[str] = Query(None),
    cfg: Optional[str] = Query(None),
    windage_temp_c: Optional[float] = Query(
        None, description="air temperature for windage [degC]; the bearing temperature when omitted"),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """The mechanical loss of this machine at this speed.

    ``{"has_bearings": false, "note": ...}`` when the machine has no bearings
    assigned — a machine nobody has given bearings to has an UNKNOWN mechanical
    loss, and answering 0 W would quietly improve its efficiency.
    """
    from motor_ai_sim import bearings as brg

    if not (rpm > 0):
        raise HTTPException(status_code=422, detail={
            "error": f"rpm must be positive; got {rpm}",
            "invalid_parameters": ["rpm"]})
    if lubrication is not None and lubrication not in ("grease", "oil_air"):
        raise HTTPException(status_code=422, detail={
            "error": f"lubrication must be 'grease' or 'oil_air'; got '{lubrication}'",
            "invalid_parameters": ["lubrication"]})

    assignment, d, c = _machine_bearings(die, cfg)
    geo = _live_geometry()
    preview = False
    if card_a or card_b:
        base = dict(assignment or {})
        if card_a:
            base["A"] = {"card": card_a.strip()}
        if card_b:
            base["B"] = {"card": card_b.strip()}
        assignment, preview = base, True
    if not brg.has_bearings(assignment):
        # Windage does NOT need bearings — it is a property of the rotor, and a
        # machine without bearings still has it.  Report it, so the answer is
        # "the part we can know" rather than nothing.
        w = brg.windage_from_geometry(
            geo, rpm, temp_c=float(windage_temp_c if windage_temp_c is not None else temp_c))
        return {
            "has_bearings": False,
            "die": d, "config": c, "rpm": float(rpm),
            "windage": w,
            "P_windage_W": (float(w["P_W"]) if w else None),
            "note": ("this machine has no bearings assigned — set them in "
                     "Mechanical -> Shaft & bearings"),
        }

    a = dict(assignment or {})
    if lubrication is not None:
        a["lubrication"] = lubrication
    mass = rotor_mass_kg
    mass_source = "given by the caller"
    if mass is None:
        mass = _rotor_mass_kg(geo)
        mass_source = ("rotor iron + magnets + shaft + sleeve, measured on the "
                       "CAD polygons")
        if mass is None:
            mass, mass_source = 0.0, ("UNKNOWN — the CAD could not be measured, "
                                      "so the radial load is preload only")

    # The bearing SPAN and the stack offset are the Mechanical tab's shaft-line
    # fields, and they decide how the rotor's weight splits between A and B.
    # Read, never written — and a missing store just means "half each".
    beam = ml.beam_settings(authorization)

    try:
        out = brg.machine_bearing_losses(
            a, rpm=float(rpm), temp_c=float(temp_c), rotor_mass_kg=float(mass),
            geometry=geo, preload_n=preload_n, beam=beam,
            windage_temp_c=windage_temp_c)
    except brg.UnknownBearingError as exc:
        raise HTTPException(status_code=422, detail={
            "error": str(exc), "invalid_parameters": ["bearings"]})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": str(exc)})
    out["die"], out["config"] = d, c
    out["rotor_mass_source"] = mass_source
    if preview:
        # Say so: these watts belong to a pair the user is TRYING, not to the
        # pair the machine is built with.  A preview mistaken for the saved
        # answer is exactly how a datasheet ends up quoting a bearing nobody
        # ordered.
        out["preview"] = True
        out.setdefault("notes", []).append(
            "PREVIEW: computed for the bearing(s) named in the request, not for "
            "the pair saved on this machine")
    return out
