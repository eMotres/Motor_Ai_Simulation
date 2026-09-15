"""THE machine's mechanical losses — one implementation, four consumers.

WHY THIS MODULE EXISTS (user, 2026-09-08)
=========================================
*"Когда запускается каплинг, должно решаться всё моделирование, и все потери
должны передаваться в электромагнитный расчёт."*

Until today the bearing friction and the rotor windage lived at the EDGE of the
app: ``GET /api/bearings/losses`` computed them, the Electromagnetic summary
table fetched them from the browser and pasted them into four cells, and the run
that was stored — the thing the datasheet, the report, Compare and the coupled
loop all read — knew nothing about them.  A loss that only exists in a React
component is not part of the model: it cannot heat anything, it cannot be fed
back, and it cannot be reproduced from the stored result.

So the arithmetic ``routes/bearings.py`` used to do inline is HERE, and the
route, the transient summary, the thermal solve and the coupled orchestrator all
call the same function.  One implementation: a bearing pair that costs 63 W on
the Electromagnetic tab costs 63 W in the thermal map and 63 W in the report, or
the difference is a bug in one place instead of a disagreement between four.

WHAT IS AND IS NOT MODELLED is stated once, in ``motor_ai_sim.bearings``: the SKF
frictional-moment model (tables 2 and 3) for the two bearings, analytic
Couette/disc windage for the rotor, and NOTHING for unbalanced magnetic pull,
coupling side loads or a shaft seal.  This module adds no physics — it resolves
WHICH machine, WHICH mass, WHICH temperature, and hands those to that module.

THE HOUSE RULE ON A MACHINE WITH NO BEARINGS: the answer is ``None`` and the
fields are ABSENT, never zero.  A machine nobody has given bearings to has an
UNKNOWN mechanical loss, and printing 0 W would quietly improve its efficiency
by exactly the amount nobody measured.

ISOLATION: nothing here solves a field and nothing here writes.  It reads a die
file, a geometry, a panel store and a stored thermal map, and does arithmetic.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

__all__ = [
    "machine_bearings", "live_geometry", "rotor_mass_kg", "beam_settings",
    "bearing_temp_from_map", "bearing_temp_from_thermal",
    "resolve_bearing_temp", "machine_mech_losses", "summary_block",
    "from_summary", "BEARING_TEMP_C",
]

#: THE COUPLED LOOP'S bearing temperature, for the duration of one iteration.
#:
#: ``routes.coupled`` owns the temperature inside its loop: it reads the shaft
#: off the previous pass's thermal map and every solver in the next pass has to
#: use THAT number, whatever the machine's saved ``temp_source`` says (a machine
#: set to "manual" is still being iterated when the user asked for a coupled
#: run).  A ContextVar rather than a threaded argument because the path between
#: the loop and this resolution goes through ``get_fem_transient`` and
#: ``_build_transient_summary`` — thirty parameters that have nothing to do with
#: bearings — and request-scoped is exactly the lifetime wanted: it cannot leak
#: into a parallel request the way a module global would.
BEARING_TEMP_C: "ContextVar[Optional[float]]" = ContextVar(
    "coupled_bearing_temp_c", default=None)


# ---------------------------------------------------------------------------
# WHICH machine
# ---------------------------------------------------------------------------

def machine_bearings(die: Optional[str] = None, cfg: Optional[str] = None
                     ) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """``(assignment, die, config)`` for the machine named, or for the ACTIVE one.

    ``(None, ...)`` when there is no machine or it carries no bearings — never an
    invented assignment.  Moved here verbatim from ``routes/bearings.py`` so the
    route and the solvers cannot disagree about which die they are reading.
    """
    from fastapi import HTTPException

    from motor_ai_sim.routes.family import (_cfg_file, _check_name, _load_yaml,
                                            _read_ctx)

    if die and cfg:
        d, c = _check_name(die, "die"), _check_name(cfg, "configuration")
        try:
            doc = _load_yaml(_cfg_file(d, c), "configuration")
        except HTTPException:
            return None, d, c
        return (doc.get("bearings") or None), d, c
    ctx = _read_ctx()
    if not ctx:
        return None, None, None
    d, c = str(ctx.get("die") or ""), str(ctx.get("config") or "")
    if not (d and c):
        return None, d or None, c or None
    try:
        doc = _load_yaml(_cfg_file(d, c), "configuration")
    except HTTPException:
        return None, d, c
    return (doc.get("bearings") or None), d, c


def live_geometry(geo_override: Optional[dict] = None) -> Dict[str, Any]:
    """The machine's geometry as a plain dict, the per-request override merged in.

    Read-only: windage needs four numbers off it (rotor OD, sleeve, air gap,
    stack length) and no CAD build at all.  The override matters — a client
    evaluating its own copy of a motor must get ITS clearance in the windage
    number, not the shared configuration's.
    """
    try:
        from motor_ai_sim.config import get_config
        base = dict(get_config().get("geometry", {}) or {})
    except Exception:  # noqa: BLE001 — no geometry is "no windage", not a 500
        return {}
    if not geo_override:
        return base
    try:
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override
        return merge_geo_override(base, geo_override)
    except Exception:  # noqa: BLE001
        return base


def rotor_mass_kg(geo: Dict[str, Any]) -> Optional[float]:
    """Rotating mass = rotor iron + magnets + shaft + sleeve [kg], off the CAD.

    ``None`` when the CAD cannot measure it — the caller then says the radial
    load is unknown rather than billing the bearings for a zero-mass rotor.  NB
    it barely matters: M_rr goes as F_r^0.54 and the seal term does not depend on
    load at all, so tripling the rotating mass moves a sealed bearing's torque by
    under 1 %.
    """
    try:
        from motor_ai_sim.masses import compute_masses
        from motor_ai_sim.simulation.geometry_2d import params_from_config
        m = compute_masses(params_from_config(), geo)
        return float(m.get("rotor", 0.0) + m.get("mag", 0.0)
                     + m.get("shaft", 0.0) + m.get("sleeve", 0.0))
    except Exception as exc:  # noqa: BLE001
        log.debug("mech_losses: rotor mass unavailable (%s)", exc)
        return None


def rotor_mass_from_summary(summary: Dict[str, Any]) -> Optional[float]:
    """The rotating mass off a RUN's own mass rows — what the card was billed at.

    A REFERENCE part (a customer-supplied shaft) carries mass 0 in the card but
    it still spins and still loads the bearings, so its modelled mass is used —
    the same rule ``report._rotating_mass_kg`` and ``datasheet`` follow.
    ``None`` when the summary carries no mass rows at all, so the caller can fall
    back to the CAD instead of billing a zero-mass rotor.
    """
    rows = (summary or {}).get("mass_components") or []
    tot, seen = 0.0, False
    for c in rows:
        if not isinstance(c, dict):
            continue
        if not str(c.get("name") or "").startswith(
                ("Rotor back-iron", "Magnets", "Shaft", "Sleeve")):
            continue
        v = c.get("mass_kg") or c.get("mass_modelled_kg")
        try:
            tot += float(v or 0.0)
            seen = True
        except (TypeError, ValueError):
            pass
    return tot if seen else None


def beam_settings(authorization: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The Mechanical tab's shaft-line block (bearing span, stack offset).

    Decides how the rotor's weight splits between bearing A and B.  Read, never
    written — and a missing store just means "half each".
    """
    try:
        from motor_ai_sim.routes.panel_settings import _load as _panel_load
        from motor_ai_sim.routes.panel_settings import _who as _panel_who
        _mech = _panel_load().get("mechanical") or {}
        _entry = _mech.get(_panel_who(authorization)) or _mech.get("shared") or {}
        b = (_entry.get("settings") or {}).get("beam")
        return b if isinstance(b, dict) else None
    except Exception:  # noqa: BLE001 — the split falls back to half each
        return None


# ---------------------------------------------------------------------------
# WHICH temperature the grease is at
# ---------------------------------------------------------------------------
#: How far the shaft's mean temperature is above the bearing's, when the bearing
#: temperature is taken from a thermal map.  ZERO, deliberately: the bearing sits
#: on the shaft it is pressed onto, and inventing a drop across the seat would be
#: a second unmeasured model on top of the one the map already is.  The named
#: constant exists so a future measurement has one place to land.
_SHAFT_TO_BEARING_DROP_K = 0.0


def bearing_temp_from_map(result: Optional[Dict[str, Any]]
                          ) -> Optional[Tuple[float, str]]:
    """``(temp_c, where)`` off ONE thermal result dict, or ``None``.

    WHICH temperature, in the order a bearing engineer would ask for it:

      1. ``cooling.shaft_ends.t_shaft_mean_c`` — the mean temperature of the
         shaft elements the exposed-stub conductance acts on, i.e. the metal the
         inner ring is pressed onto, measured by the solve.  Available only when
         the shaft-ends heat path was ON, which is also the only case in which
         the friction heat was modelled, so the two agree by construction;
      2. the shaft COMPONENT's average, when the path was off.  The shaft is
         then adiabatic along the axis and its average is the best statement the
         cross-section can make about the seat;
      3. ``None`` — a map whose model carries no shaft at all (the part is
         excluded).  The caller must fall back to the assignment rather than
         invent a seat temperature.

    Split out of ``bearing_temp_from_thermal`` so the coupled orchestrator, which
    HAS the previous pass's map in hand, reads it by exactly the same rule as the
    summary reading the remembered one — one definition of "the bearing's
    temperature", not two that drift.
    """
    res = result if isinstance(result, dict) else None
    if res is None:
        return None
    se = ((res.get("cooling") or {}).get("shaft_ends") or {})
    t = se.get("t_shaft_mean_c")
    where = "the shaft's mean temperature at the exposed ends"
    if t is None:
        comp = (res.get("components") or {}).get("shaft") or {}
        t = comp.get("avg")
        where = "the shaft's average temperature in the cross-section"
    if t is None:
        return None
    try:
        t = float(t)
    except (TypeError, ValueError):
        return None
    # A map that ran away is not a measurement of the seat.  The last thermal
    # map "of this machine" can be one solved at the wrong operating point —
    # the G2-L40 at the Ø200's 687 A on 2026-09-08 23:26 left a 46 742 °C
    # shaft in the store, and the next loop's first pass billed its bearings at
    # that temperature (grease viscosity → 0).  No bearing seat lives outside
    # this band; outside it the caller falls back to the assignment.
    if not (BEARING_SEAT_MIN_C <= t <= BEARING_SEAT_MAX_C):
        return None
    return t - _SHAFT_TO_BEARING_DROP_K, where


#: The band a bearing seat temperature read off a thermal map must lie in to be
#: believed (°C).  −60 °C is colder than any cold-start spec the catalogue
#: quotes; 350 °C is past the point where grease, seals and ring hardness are
#: gone — a map beyond it describes a failure, not an operating point.
BEARING_SEAT_MIN_C = -60.0
BEARING_SEAT_MAX_C = 350.0


def bearing_temp_from_thermal(geometry_fingerprint: Optional[str] = None
                              ) -> Optional[Tuple[float, str]]:
    """``(temp_c, where)`` off the last thermal map OF THIS MACHINE, or ``None``.

    The map is only usable when it describes the same machine, so the geometry
    fingerprint has to match: a temperature borrowed from another motor is worse
    than the assignment's own number, because it looks computed.

    ``bearing_temp_from_map`` decides WHICH temperature; this function decides
    WHICH MAP.  Reads ``routes.thermal``'s remembered result and NOTHING else: no
    solve is started, no store is written.  That is the rule this whole coupling
    is built on — the Electromagnetic side may not run a thermal solve.
    """
    try:
        from motor_ai_sim.routes import thermal as th
        th._load_last()
        entry = th._LAST.get("field")
        if not isinstance(entry, dict):
            return None
        res = entry.get("result")
        if not isinstance(res, dict):
            return None
        fp = entry.get("geometry_fingerprint") or res.get("geometry_fingerprint")
        if geometry_fingerprint and fp and str(fp) != str(geometry_fingerprint):
            return None
        if geometry_fingerprint and not fp:
            return None          # UNKNOWN machine is not "this machine"
        return bearing_temp_from_map(res)
    except Exception as exc:  # noqa: BLE001 — a memory is not worth a 500
        log.debug("mech_losses: no thermal bearing temperature (%s)", exc)
        return None


def resolve_bearing_temp(assignment: Optional[Dict[str, Any]],
                         *, geometry_fingerprint: Optional[str] = None,
                         override_c: Optional[float] = None
                         ) -> Tuple[float, str, str]:
    """``(temp_c, source, note)`` — the temperature the grease is interpolated at.

    Three sources, in the order a reader would expect:

      * ``override_c`` — the coupled loop's own number for THIS iteration, taken
        off the previous pass's thermal map.  ``source = "coupled"``;
      * the last thermal map of this machine, when the assignment says
        ``temp_source: "thermal"``.  ``source = "thermal"``;
      * the assignment's own ``temp_c``.  ``source = "assigned"``.

    Never a silent default: the fallback when NOTHING says is 70 °C with
    ``source = "default"`` and a note saying so, because M_rr goes as ν^0.6 and a
    grease quoted at 40 °C running at 90 °C is a factor of two on the rolling
    term — a number that important may not be invented quietly.
    """
    from motor_ai_sim import bearings as brg

    res = brg.resolve_assignment(assignment)
    if override_c is None:
        override_c = BEARING_TEMP_C.get()
    if override_c is not None:
        return (float(override_c), "coupled",
                "from the previous coupled iteration's thermal map")
    if str(res.get("temp_source") or "") == "thermal":
        hit = bearing_temp_from_thermal(geometry_fingerprint)
        if hit is not None:
            return hit[0], "thermal", "from the last thermal map — " + hit[1]
    t = res.get("temp_c")
    if t is not None:
        return (float(t), "assigned",
                "the bearing temperature saved on this machine")
    return (70.0, "default",
            "no bearing temperature on this machine and no thermal map to take "
            "one from — 70 °C assumed, and the rolling term goes as ν^0.6")


# ---------------------------------------------------------------------------
# THE answer
# ---------------------------------------------------------------------------

def machine_mech_losses(*, rpm: float,
                        assignment: Optional[Dict[str, Any]] = None,
                        die: Optional[str] = None,
                        config: Optional[str] = None,
                        geo_override: Optional[dict] = None,
                        geometry: Optional[Dict[str, Any]] = None,
                        geometry_fingerprint: Optional[str] = None,
                        rotor_mass_kg_: Optional[float] = None,
                        temp_c: Optional[float] = None,
                        preload_n: Optional[float] = None,
                        lubrication: Optional[str] = None,
                        windage_temp_c: Optional[float] = None,
                        authorization: Optional[str] = None,
                        resolve_machine: bool = True) -> Optional[Dict[str, Any]]:
    """The whole mechanical loss of ONE machine at ONE speed, or ``None``.

    ``None`` means "this machine names no bearings" — the caller must then omit
    its mechanical fields rather than write zeros (see the module docstring).
    Windage alone, which needs no bearings, is still available through
    ``bearings.windage_from_geometry`` for the callers that want to say what the
    air costs on a machine whose bearings nobody has chosen.

    Everything the answer rides on is in the answer: the temperature and where it
    came from, the rotating mass and how it was measured, the cards, the SKF
    intermediates.  A friction number whose viscosity is hidden cannot be argued
    with.
    """
    from motor_ai_sim import bearings as brg

    if not (rpm and float(rpm) > 0):
        return None
    a = assignment
    d, c = die, config
    if a is None and resolve_machine:
        a, d, c = machine_bearings(die, config)
    if not brg.has_bearings(a):
        return None

    block = dict(a or {})
    if lubrication is not None:
        block["lubrication"] = lubrication

    t_c, t_src, t_note = resolve_bearing_temp(
        a, geometry_fingerprint=geometry_fingerprint, override_c=temp_c)

    geo = geometry if geometry is not None else live_geometry(geo_override)
    mass = rotor_mass_kg_
    mass_source = "given by the caller"
    if mass is None:
        mass = rotor_mass_kg(geo)
        mass_source = ("rotor iron + magnets + shaft + sleeve, measured on the "
                       "CAD polygons")
        if mass is None:
            mass, mass_source = 0.0, ("UNKNOWN — the CAD could not be measured, "
                                      "so the radial load is preload only")

    try:
        out = brg.machine_bearing_losses(
            block, rpm=float(rpm), temp_c=float(t_c),
            rotor_mass_kg=float(mass), geometry=geo, preload_n=preload_n,
            beam=beam_settings(authorization), windage_temp_c=windage_temp_c)
    except brg.UnknownBearingError:
        raise
    except ValueError:
        raise
    out["die"], out["config"] = d, c
    out["rotor_mass_source"] = mass_source
    out["bearing_temp_c"] = round(float(t_c), 2)
    out["bearing_temp_source"] = t_src
    out["bearing_temp_note"] = t_note
    return out


def summary_block(mech: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The COMPACT form the transient summary carries, from a full answer.

    The full answer is a page of SKF intermediates per bearing; the summary is
    read by the card, the datasheet, the report and Compare, and it is stored
    with every run for ever.  So this is the short form — the watts, the
    temperature and its provenance, the cards, the model — plus just enough of
    the split (rolling/sliding/seal per end, gap/faces for the windage) that the
    card's tooltip can say WHY the number is what it is without a second fetch.

    ``{}`` for ``None``, so a caller can splat it into the summary dict and a
    machine without bearings simply grows no keys.
    """
    if not mech or not mech.get("has_bearings"):
        return {}
    w = mech.get("windage") or {}
    ends = []
    for b in mech.get("bearings") or []:
        sp = b.get("speed") or {}
        ends.append({
            "end": b.get("end"), "bearing": b.get("bearing"),
            "P_W": round(float(b.get("P_W") or 0.0), 3),
            "M_total_Nm": round(float(b.get("M_total_Nm") or 0.0), 5),
            "M_rr_Nm": round(float(b.get("M_rr_Nm") or 0.0), 5),
            "M_sl_Nm": round(float(b.get("M_sl_Nm") or 0.0), 5),
            "M_seal_Nm": round(float(b.get("M_seal_Nm") or 0.0), 5),
            "nu_mm2_s": round(float(b.get("nu_mm2_s") or 0.0), 3),
            "F_r_N": round(float(b.get("F_r_N") or 0.0), 2),
            # THE SPEED VERDICT and the two numbers behind it.  Carried in full
            # because "this pair is over its grease limit at 23 000 rpm" is a
            # safety statement, and a report that can only print the verdict but
            # not the limit it was measured against is not checkable.
            "speed_ok": sp.get("ok"),
            "speed_verdict": sp.get("verdict"),
            "n_dm": sp.get("n_dm"),
            "limit_rpm": sp.get("limit_rpm"),
        })
    return {
        "rpm": round(float(mech.get("rpm") or 0.0), 1),
        "bearings": ends,
        "cards": [e["bearing"] for e in ends if e.get("bearing")],
        "lubrication": mech.get("lubrication"),
        "preload_n": mech.get("preload_n"),
        "rotor_mass_kg": mech.get("rotor_mass_kg"),
        "rotor_mass_source": mech.get("rotor_mass_source"),
        "F_r_total_N": mech.get("F_r_total_N"),
        "M_bearings_Nm": round(float(mech.get("M_bearings_Nm") or 0.0), 5),
        "windage": ({} if not w else {
            "P_W": round(float(w.get("P_W") or 0.0), 4),
            "P_gap_W": round(float(w.get("P_gap_W") or 0.0), 4),
            "P_faces_W": round(float(w.get("P_faces_W") or 0.0), 4),
            "M_total_Nm": round(float(w.get("M_total_Nm") or 0.0), 6),
            "gap_regime": w.get("gap_regime"),
            "face_regime": w.get("face_regime"),
            "delta_mm": w.get("delta_mm"),
        }),
        "die": mech.get("die"), "config": mech.get("config"),
        "model": mech.get("model"),
        "notes": mech.get("notes") or [],
    }


def from_summary(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A stored RUN's mechanical block, back in ``machine_bearing_losses`` shape.

    The datasheet, the report and Compare were all written against the shape the
    SKF roll-up returns, and they must now prefer the numbers the run was STORED
    with over a fresh recomputation — same physics, but the stored one is the one
    the efficiency on the same page was derived from, and it carries the bearing
    temperature the coupled loop actually converged on rather than whatever the
    assignment says today.

    ``None`` when the summary predates this (2026-09-08) or belongs to a machine
    with no bearings; the caller then falls back to computing it, and says so.
    """
    s = summary if isinstance(summary, dict) else None
    if not s:
        return None
    blk = s.get("mech_losses")
    if not isinstance(blk, dict) or not blk.get("bearings"):
        return None
    ends = []
    for b in blk.get("bearings") or []:
        ends.append({**b, "speed": {"ok": b.get("speed_ok"),
                                    "verdict": b.get("speed_verdict"),
                                    "n_dm": b.get("n_dm"),
                                    "limit_rpm": b.get("limit_rpm")}})
    return {
        "has_bearings": True,
        "from_stored_run": True,
        "rpm": blk.get("rpm"),
        "temp_c": s.get("bearing_temp_c"),
        "bearing_temp_c": s.get("bearing_temp_c"),
        "bearing_temp_source": s.get("bearing_temp_source"),
        "bearing_temp_note": s.get("bearing_temp_note"),
        "lubrication": blk.get("lubrication"),
        "preload_n": blk.get("preload_n"),
        "rotor_mass_kg": blk.get("rotor_mass_kg"),
        "rotor_mass_source": blk.get("rotor_mass_source"),
        "F_r_total_N": blk.get("F_r_total_N"),
        "bearings": ends,
        "windage": (blk.get("windage") or None),
        "P_bearings_W": s.get("P_bearings_W"),
        "P_windage_W": s.get("P_windage_W"),
        "P_mech_extra_W": s.get("P_mech_extra_W"),
        "M_bearings_Nm": blk.get("M_bearings_Nm"),
        "die": blk.get("die"), "config": blk.get("config"),
        "model": blk.get("model"),
        "notes": list(blk.get("notes") or ()),
    }
