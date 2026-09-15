"""Thermal (steady-state temperature) routes — /api/thermal.

Split out of ``routes.simulation`` on 2026-09-07.  The steady thermal map used to
live at ``GET /api/simulation/physics/thermal_field2d``, one route among sixty in
a 6 700-line module, with no last-result store, no mesh preview and no timing —
so the Thermal view could not do what the Mechanical tab already does: come back
to what it was last showing, draw the cross-section before anything is solved,
and say how long the answer took.

This module is modelled 1:1 on ``routes.mechanical``, deliberately, so the two
engineering tabs behave identically:

  * ``?geo=`` is a per-request geometry override and ``?mat=`` a per-request
    material override applied by a router dependency — neither ever writes the
    shared config;
  * every answer carries ``elapsed_s`` / ``solve_time_s`` / ``cached`` and the
    geometry fingerprint it was solved for;
  * ``GET /last`` is a lookup that answers 200 with ``has_result: false`` rather
    than 404, and flags staleness against the LIVE fingerprint;
  * ``GET /mesh`` meshes without solving, so an unsolved tab draws the machine;
  * ``clear_thermal_caches`` is called from
    ``routes.simulation.clear_simulation_caches`` — a geometry PUT / material
    PATCH / Run drops the cached temperature maps, because a stale cross-section
    is a wrong temperature.

THE TWO SOLVERS ARE SEPARATE (user, 2026-09-07: *"нужно как-то разделить
тепловые расчёты и электромагнитные; если вдруг тепловому расчёту нужно
электромагнитное моделирование, пусть оно делается во вкладке Simulation"* — the
tab now called Electromagnetic).  THIS ROUTER NEVER STARTS AN ELECTROMAGNETIC
SOLVE: not a 36-frame transient, not a single-frame magnetostatic estimate, not
a d-axis calibration, not a verification pass.  The cycle-averaged loss map is
an ELECTROMAGNETIC RESULT and it comes from an Electromagnetic run the user
made (stored by ``routes.simulation._store_transient_field_snapshot``), matched
on physics identity, and from this router's own memory of the maps it has been
handed (``_LOSS_MAPS``).  When neither has one, the answer is a 422 naming the
operating point to run — never a quietly-started six-minute solve inside a
temperature request.  The conduction solve is still
``simulation.thermal_solver_2d.solve_steady_thermal``.

THE COOLING MODEL (rewritten 2026-09-07, same day, second pass)
===============================================================
The machine has TWO cooled surfaces, not one:

  * the OUTER stator surface — ``cooling_mode`` = manual | air | liquid | none;
  * the ROTOR BORE — ``bore_mode`` = none | air | liquid.  The user's point:
    *"Ротор придётся охлаждать в основном через вал"*.  In a 2-D cross-section
    the rotor's only other way out is the air gap, whose effective conductivity
    is a few hundredths of a W/m·K even with the Taylor vortices working, so a
    rotor with no bore cooling is thermally not cooled at all — and until this
    change there was no way to model the shaft path at all.

Three model changes came with it, each of which was a wrong number before:

  1. the LIQUID model is no longer inverted.  ``flow_lpm`` and
     ``fluid_temp_in_c`` are the inputs; the outlet is the RESULT of
     T_out = T_in + P/(ṁ·cp), which makes the boundary condition depend on the
     answer — so the conduction solve (seconds) is iterated up to four times
     around a loss solve (minutes) that runs exactly ONCE;
  2. the AIR GAP is derived from the true MECHANICAL clearance (stator bore
     minus the rotor OD *including the retaining sleeve*) with air properties at
     a stated gap temperature.  ``gap_k`` stopped being an input;
  3. the retaining SLEEVE is a domain of its own with an anisotropic (r, θ)
     conductivity tensor — *"у него теплопроводность очень плохая в радиальном
     направлении"* — instead of silently inheriting the air-gap value.

And the answer now closes: every surface reports its facet-integrated watts, the
gap bridge reports what crosses it, and ``cooling.heat_budget`` states the
residual.  A temperature map that cannot say where its heat went is an
assertion, not a result.
"""
from __future__ import annotations

import logging
import math
import json
import os
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from motor_ai_sim import workspace as _WSP
from motor_ai_sim import jobs as _JOBS
from motor_ai_sim.progress import poll as _progress_poll
from motor_ai_sim.progress import route_progress as _route_progress

log = logging.getLogger(__name__)


async def _material_override_dep(mat: Optional[str] = Query(default=None)):
    """Apply this request's ``?mat=`` override, same contract as the simulation
    and mechanical routers: a malformed payload is a 422 from the shared parser,
    an assignment naming a material that does not exist is a 400.

    It matters more here than anywhere else that this is a REQUEST-scoped
    override: the conductivities below (steel, magnet, shaft, liner, enamel) are
    read from the material assignment, so a client computing a temperature map of
    its own copy of a motor must get its own materials without touching the
    shared config.
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes._validation import parse_mat_override

    ov = parse_mat_override(mat)
    if ov and ov.get("assignment"):
        from motor_ai_sim.materials import (UnknownMaterialError,
                                            validate_assignment)
        try:
            validate_assignment(ov["assignment"],
                                known_extra=set(ov.get("materials") or ()))
        except UnknownMaterialError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    set_request_materials(ov)


router = APIRouter(prefix="/api/thermal", tags=["thermal"],
                   dependencies=[Depends(_material_override_dep)])


# ---------------------------------------------------------------------------
# Live progress
# ---------------------------------------------------------------------------
# Added 2026-09-07 alongside the mechanical one, and for the same reason: a
# /field request is a full multi-frame EM solve plus a conduction solve, and
# /coupled is up to twelve of them — minutes during which the tab said nothing
# at all.  One tracker per router, polled by `GET /api/thermal/progress`, in the
# transient strip's exact shape (see `motor_ai_sim.progress`).
#
# Migration Stage 4: one tracker per RUN, not one per router.  The NAME is
# unchanged and so is every call site — `RouteProgress` resolves to the tracker
# of the run this call is inside, and to this router's per-workspace default
# tracker (which is what this module global used to be) when there is none.
_progress = _route_progress("thermal")


@router.get("/progress")
def progress(run_id: str = ""):
    """What this router is solving right now — polled at ~500 ms by the panel.

    Same payload as the Simulation transient's progress endpoint plus ``kind``
    (``field`` | ``coupled`` | ``mesh``), so one strip component in the frontend
    serves all three tabs.  On a coupled run the phase carries both counters —
    "iteration 2/6 — EM losses, frame 17/48" — because on a twelve-pass request
    the inner frame count alone is not enough to tell a slow run from a stuck
    one.

    Deliberately NOT gated (see ``auth._GATED``), mirroring the transient's own
    progress route: the SOLVE is gated, its counter is a status read.

    ``?run_id=`` answers THAT run (Stage 4).  With no argument it answers the
    caller's newest run in this router — which on a single-user server is the
    same object it always was, so today's strip keeps working unchanged.
    """
    return _progress_poll("thermal", run_id)


# ---------------------------------------------------------------------------
# Request resolution — the same three questions every physics router asks
# ---------------------------------------------------------------------------

def _live_polys(geo: Optional[str]):
    """The cross-section for THIS request, with ``?geo=`` applied.

    Read-only, and it goes through the SAME resolution the mesh route uses
    (``merge_geo_override`` on the live geometry, then ``set_parameters``) — a
    plain dict update would put one machine's primaries under another's DERIVED
    radii, and the thermal model reads derived values (``slot_width``,
    ``stator_inner_radius``) directly.  A malformed ``geo`` is a 422 from the
    shared parser, never a silent fallback to the shared config.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.services.geometry_service import get_current_geometry
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override

    ov = parse_geo_override(geo)
    params = merge_geo_override(get_current_geometry().to_dict(), ov)
    motor = CadQueryMotor()
    motor.set_parameters(params)
    return motor.get_2d_polygons(0.0), motor, ov


def _assignments() -> Dict[str, str]:
    """part -> material name: the config's ``materials:`` block, then this
    request's override.  Same precedence the solver uses — and the reason it is
    in the cache key: two requests that differ only in the magnet grade are two
    different conductivities and therefore two different temperature maps."""
    out: Dict[str, str] = {}
    try:
        from motor_ai_sim.config import get_material_assignments
        out.update({k: v for k, v in (get_material_assignments() or {}).items() if v})
    except Exception:  # noqa: BLE001 - no config is not a reason to have no answer
        pass
    try:
        from motor_ai_sim.material_context import get_request_materials
        ov = (get_request_materials() or {}).get("assignment") or {}
        out.update({k: v for k, v in ov.items() if v})
    except Exception:  # noqa: BLE001
        pass
    return out


def _override_props() -> Dict[str, dict]:
    """The request's inline material CARDS (``?mat=``'s ``materials`` block).

    Only the names go into the cache key (as in ``routes.mechanical``): a card
    that redefines k for this request must not be served a map solved with the
    catalogued one."""
    try:
        from motor_ai_sim.material_context import get_request_materials
        return dict((get_request_materials() or {}).get("materials") or {})
    except Exception:  # noqa: BLE001
        return {}


def _elapsed(t0: float) -> float:
    """Seconds this request spent working, as the panel's timer reports them.

    The client can time its own fetch, but that number includes the network and
    the JSON — and a thermal payload carries a temperature per node and a flux
    vector per triangle, which are seconds of their own on a big machine — so the
    honest figure is measured here.  Floored at 1 ms so "how long did it take" is
    never answered with a zero that reads as "it did not run".
    """
    return round(max(time.time() - t0, 1e-3), 3)


def _live_fingerprint(geo_ov) -> Optional[str]:
    """The backend's fingerprint of the machine THIS request means.

    Shared with ``routes.simulation`` on purpose: /last compares a stored result
    against it, and a staleness badge computed from two different fingerprint
    functions would be worse than none.
    """
    try:
        from motor_ai_sim.routes.simulation import _geometry_fingerprint
        return _geometry_fingerprint(geo_ov)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Material conductivities
# ---------------------------------------------------------------------------
# Moved verbatim from routes.simulation (2026-09-07) — the numbers and the
# fallbacks are unchanged, only their address is.

def _thermal_k(category: str, name, default: float) -> float:
    """Thermal conductivity [W/m·K] of a named material, or a category default."""
    if not name:
        return default
    try:
        from motor_ai_sim.materials import get_material
        v = getattr(get_material(category, name), "thermal_conductivity", None)
        return float(v) if v else default
    except Exception:
        return default


def _thermal_k_any(name, default: float) -> float:
    """Thermal conductivity of a material whose category is unknown (e.g. shaft)."""
    if not name:
        return default
    from motor_ai_sim.materials import get_material
    for cat in ("conductor", "steel", "magnet"):
        try:
            v = getattr(get_material(cat, name), "thermal_conductivity", None)
            if v:
                return float(v)
        except Exception:
            pass
    return default


# ── Cooling-system models — the whole outer stator surface (housing) is cooled ──
# Fluid properties (rho kg/m3, cp J/kgK, k W/mK, nu m2/s, Pr) come from the
# materials library (config/materials_library.yaml → `coolant:`), so every fluid
# used in the model is also a catalogued material.  The table below is only a
# safety fallback if the library is missing an entry.
_COOLANT_FALLBACK = {
    "water":            (1000.0, 4186.0, 0.60, 1.0e-6, 7.0),
    "water_glycol_50":  (1070.0, 3300.0, 0.40, 3.0e-6, 25.0),
    "oil":              (860.0,  2000.0, 0.14, 4.0e-5, 280.0),
    "ethylene_glycol":  (1110.0, 2400.0, 0.25, 1.5e-5, 150.0),
    "air":              (1.16,   1007.0, 0.0263, 1.56e-5, 0.707),   # ~300 K
}


def _coolant_props(name: str):
    """(rho, cp, k, nu, Pr) for a coolant — read FROM the materials library so the
    catalogue is the single source of truth; falls back to the built-in table so
    the thermal model never breaks on a missing/renamed entry."""
    try:
        from motor_ai_sim import materials as _mat
        return _mat.get_coolant(name).props_tuple
    except Exception:
        return _COOLANT_FALLBACK.get(name, _COOLANT_FALLBACK["water"])


def _known_coolant(name: str) -> bool:
    """Is this fluid one the catalogue (or the fallback table) actually knows?

    ``_coolant_props`` falls back to WATER for anything it cannot resolve, which
    is the right behaviour for a solve that is already running and the wrong
    answer to give a client: a typo'd fluid would come back as a perfectly
    plausible water-cooled map.  The route asks this first and 422s by name.
    """
    if not name:
        return False
    try:
        from motor_ai_sim import materials as _mat
        _mat.get_coolant(str(name))
        return True
    except Exception:  # noqa: BLE001
        return str(name) in _COOLANT_FALLBACK


def _fluid_props(name: str):
    """A ``cooling_models.FluidProps`` for a catalogued coolant."""
    from motor_ai_sim.simulation.cooling_models import FluidProps
    return FluidProps(*_coolant_props(name))


def _cooling_bc(*, mode: str, t_ambient_c: float, air_speed_mps: float,
                fluid: str, fluid_temp_in_c: float, flow_lpm: float,
                p_loss_w: float, r_housing_m: float, length_m: float,
                h_manual: float, emissivity: float = 0.9,
                t_wall_c: Optional[float] = None, **_ignored):
    """The OUTER (housing) surface's Robin BC for a given heat load.

    Returns ``(h, T_sink, report)`` where ``report`` is the documented surface
    dict (mode / h_conv / t_sink_c / t_in_c / t_out_c / flow_lpm / air_speed_mps /
    re / nu / area_m2 / heat_removed_W / note).

      • ``none``   — adiabatic housing.  Legal only when the BORE is cooled or
                     the machine conducts into a mount; the validator refuses
                     "nothing is cooled" before we get here.
      • ``manual`` — the caller's own h at ambient (the legacy contract).
      • ``air``    — cylinder in cross-flow, Churchill–Bernstein, with a
                     natural-convection floor.  The sink stays at ambient: the
                     air outside the machine is an unbounded reservoir.
      • ``liquid`` — jacket channel.  ``flow_lpm`` and ``fluid_temp_in_c`` are
                     the INPUTS and the outlet is the RESULT
                     (T_out = T_in + P/(ṁ·cp)); the sink is the mean film
                     temperature.  Because P is not known until the conduction
                     solve has run, ``p_loss_w`` is this pass's ESTIMATE of the
                     heat this surface removes and the caller iterates.
      • ``robotics`` — (2026-09-14) the machine a robot joint actually is: no
                     fan, no jacket, no slipstream, bolted to an arm in a room.
                     ``cooling_models.outer_still`` — Churchill–Chu natural
                     convection PLUS linearised radiation at the caller's
                     ``emissivity`` — and the two are comparable on a small
                     housing (h_conv ≈ 6, h_rad ≈ 8.3 at ΔT 60 K on Ø85).
                     THE ROBIN COEFFICIENT OF THIS MODE IS ``h_total``: the
                     reported ``h_conv`` keeps its plain meaning (the convective
                     half) so ``emissivity = 0`` means what it says, and a
                     caller that applied ``h_conv`` here would have switched more
                     than half the cooling off.  ``h`` depends on the WALL
                     temperature, which no other mode's does, so ``t_wall_c`` is
                     this pass's estimate of it and the caller iterates on the
                     surface's own measured ``t_mean_c``.

    Replaces the pre-2026-09-07 inverted model, in which the engineer typed the
    outlet temperature and the model answered with the flow rate.  Nobody sizes a
    machine that way round: the pump is a given, the outlet is what the machine
    does to the coolant.
    """
    from motor_ai_sim.simulation import cooling_models as cm

    area = 2.0 * math.pi * max(float(r_housing_m), 1e-4) * max(float(length_m), 1e-3)

    if mode == "robotics":
        rep = cm.outer_still(
            t_wall_c=float(t_ambient_c if t_wall_c is None else t_wall_c),
            t_ambient_c=float(t_ambient_c),
            d_housing_m=2.0 * max(float(r_housing_m), 1e-4),
            emissivity=float(emissivity), area_m2=area,
            heat_w=float(p_loss_w))
        # The USER-FACING name of this mode is what the request asked for: the
        # correlation is called "still" inside `cooling_models` (it is one, and
        # the bore uses the same family), but the Thermal tab offers ONE mode
        # called `robotics` = still-air housing + open bore + end faces + mount,
        # and a payload whose `mode` does not read back as the mode that was
        # asked for is a payload nobody can reproduce a request from.
        rep["mode"] = "robotics"
        rep["model"] = ("still air + radiation; the Robin coefficient is "
                        "h_total = h_conv + h_rad")
        return float(rep["h_total"]), float(rep["t_sink_c"]), rep
    if mode == "none":
        rep = cm.surface_off("the housing")
        return 0.0, float(t_ambient_c), rep
    if mode == "liquid":
        rep = cm.outer_liquid(props=_fluid_props(fluid), fluid=fluid,
                              t_in_c=float(fluid_temp_in_c),
                              flow_lpm=float(flow_lpm),
                              r_housing_m=float(r_housing_m),
                              length_m=float(length_m),
                              heat_w=float(p_loss_w), area_m2=area)
        # USER RULE (2026-09-07): "температура внешней поверхности статора
        # равна температуре выходной воды".  The jacket film is reported for
        # information (`h_jacket`, Re), but the boundary condition is the
        # housing PINNED at the coolant OUTLET — the hottest the jacket gets and
        # the conservative reading of a well-designed jacket (turbulent h is
        # 10⁴ W/m²·K, i.e. a few kelvin of film drop on 6 kW, which the pin
        # rounds to zero).  Realised as a Robin film large enough to be a
        # Dirichlet condition without needing a second BC type in the solver.
        rep["h_jacket"] = rep.get("h_conv")
        rep["h_conv"] = max(float(rep.get("h_conv") or 0.0), 1.0e5)
        rep["t_sink_c"] = float(rep.get("t_out_c", rep.get("t_sink_c", fluid_temp_in_c)))
        rep["model"] = "housing pinned at the coolant outlet temperature"
        rep["note"] = ("housing surface held at the coolant OUTLET temperature "
                       "(user rule); jacket film h_jacket = %.0f W/m²·K, Re %.0f — "
                       "reported, not applied; outlet from ṁ·cp"
                       % (float(rep.get("h_jacket") or 0.0), float(rep.get("re") or 0.0)))
    elif mode == "air":
        rep = cm.outer_air(air_speed_mps=float(air_speed_mps),
                           t_ambient_c=float(t_ambient_c),
                           d_housing_m=2.0 * max(float(r_housing_m), 1e-4),
                           area_m2=area, heat_w=float(p_loss_w))
    else:
        rep = cm.outer_manual(h_conv=float(h_manual),
                              t_ambient_c=float(t_ambient_c),
                              area_m2=area, heat_w=float(p_loss_w))
    return float(rep["h_conv"]), float(rep["t_sink_c"]), rep


def _bore_bc(*, mode: str, t_ambient_c: float, air_speed_mps: float,
             fluid: str, fluid_temp_in_c: float, flow_lpm: float,
             r_bore_m: float, rpm: float, length_m: float, p_bore_w: float,
             emissivity: float = 0.9, t_wall_c: Optional[float] = None):
    """The BORE (rotor inner diameter) Robin BC — the rotor's real heat path.

    User, 2026-09-07: *"Ротор придётся охлаждать в основном через вал"*.  In a
    2-D cross-section the rotor's only other route is the air gap, whose
    effective conductivity is tens of milliwatts per metre-kelvin even when the
    Taylor vortices are working — so a rotor that is not cooled through its bore
    is, thermally, not cooled at all.

    Both fluids use the same internal pipe-flow ladder (laminar 3.66 / Gnielinski
    / Dittus–Boelter); the difference is the properties and, for air, a swirl
    term from the rotating wall.  Both streams HEAT UP along the bore, so the
    sink is the mean of inlet and outlet and the caller iterates.

    ``still`` (2026-09-14) is the OPEN, UNVENTILATED bore of a robot joint — a
    hole with a cable in it, open to the room at both ends and with nothing
    pumped through it (user decision: the bore is open, not sealed).  Natural
    convection floored at conduction across the hole plus radiation out of the
    two ends (``cooling_models.bore_still``); like the still-air housing its
    coefficient depends on the WALL temperature, so ``t_wall_c`` is this pass's
    estimate and the Robin coefficient is ``h_total``, not ``h_conv``.  Nothing
    flows, so there is no outlet to iterate — only the wall.

    Returns ``(h, T_sink, report)`` exactly like ``_cooling_bc``.
    """
    from motor_ai_sim.simulation import cooling_models as cm

    area = 2.0 * math.pi * max(float(r_bore_m), 0.0) * max(float(length_m), 1e-3)
    if mode == "none" or float(r_bore_m) <= 0.0:
        rep = cm.surface_off("the rotor bore")
        rep["r_bore_mm"] = round(float(r_bore_m) * 1e3, 3)
        return 0.0, float(t_ambient_c), rep
    if mode == "still":
        rep = cm.bore_still(
            t_wall_c=float(t_ambient_c if t_wall_c is None else t_wall_c),
            t_ambient_c=float(t_ambient_c), r_bore_m=float(r_bore_m),
            emissivity=float(emissivity), area_m2=area,
            heat_w=float(p_bore_w))
        rep["model"] = ("unventilated bore; the Robin coefficient is "
                        "h_total = h_conv + h_rad")
        rep["r_bore_mm"] = round(float(r_bore_m) * 1e3, 3)
        return float(rep["h_total"]), float(rep["t_sink_c"]), rep
    if mode == "liquid":
        rep = cm.bore_liquid(rpm=float(rpm), props=_fluid_props(fluid), fluid=fluid,
                             t_in_c=float(fluid_temp_in_c),
                             flow_lpm=float(flow_lpm),
                             r_bore_m=float(r_bore_m),
                             heat_w=float(p_bore_w), area_m2=area)
    else:
        rep = cm.bore_air(air_speed_mps=float(air_speed_mps),
                          t_ambient_c=float(t_ambient_c),
                          r_bore_m=float(r_bore_m), rpm=float(rpm),
                          heat_w=float(p_bore_w), area_m2=area)
    rep["r_bore_mm"] = round(float(r_bore_m) * 1e3, 3)
    return float(rep["h_conv"]), float(rep["t_sink_c"]), rep


def _sleeve_k(name: Optional[str]):
    """(k_radial, k_fibre, source) [W/m·K] for the assigned retaining-sleeve
    material.

    A hoop-wound UD CFRP sleeve is the most anisotropic body in the machine —
    the user's words: *"у него теплопроводность очень плохая в радиальном
    направлении"* — and the two numbers are NOT interchangeable.  The radial
    (through-thickness) value is matrix- and contact-limited and is the one that
    stands between the rotor and the air gap; the fibre-direction value is an
    order of magnitude higher and only smears heat AROUND the rotor.

    The materials library already carries both on the sleeve cards
    (``thermal_conductivity`` = through-thickness, ``thermal_conductivity_axial``
    = along the fibres), so the library wins whenever it has them.  The
    documented CFRP-UD-60 % fallbacks below are used — and REPORTED as
    ``source: "default"`` — only for a sleeve material that carries neither, so a
    catalogue gap never silently becomes an isotropic sleeve.
    """
    from motor_ai_sim.simulation.cooling_models import (
        SLEEVE_K_FIBRE_DEFAULT, SLEEVE_K_RADIAL_DEFAULT)

    kr = kf = None
    if name:
        from motor_ai_sim.materials import get_material
        for cat in ("insulator", "conductor", "steel", "magnet"):
            try:
                m = get_material(cat, str(name))
            except Exception:  # noqa: BLE001
                continue
            kr = getattr(m, "thermal_conductivity", None)
            kf = getattr(m, "thermal_conductivity_axial", None)
            break
    if kr:
        # A card with a through-thickness value but no in-plane one is still a
        # library reading: fall back to the DEFAULT ratio rather than making the
        # sleeve isotropic, which is the error this whole block exists to avoid.
        return (float(kr),
                float(kf) if kf else float(kr) * (SLEEVE_K_FIBRE_DEFAULT
                                                  / SLEEVE_K_RADIAL_DEFAULT),
                "library")
    return SLEEVE_K_RADIAL_DEFAULT, SLEEVE_K_FIBRE_DEFAULT, "default"


# ---------------------------------------------------------------------------
# Input validation — loud, engineer-readable, never "solve something else"
# ---------------------------------------------------------------------------

#: ``robotics`` (2026-09-14) is ONE mode, not a field: the user's decision is
#: that a robot joint is chosen by name and everything that makes it follows —
#: a still-air housing with its emissivity, an OPEN bore in still air, the
#: exposed AXIAL end faces of the coils / cores / magnets, and a bolted MOUNT
#: conductance.  Scattering those as four independent switches would let a
#: half-configured machine look like a converged answer.
COOLING_MODES = ("manual", "air", "liquid", "none", "robotics")
#: ``still`` is the unventilated bore that comes with ``robotics`` (and only
#: with it — it is evaluated with that mode's ``emissivity`` input, which no
#: other mode sends; see ``_validate_field_params``).
BORE_MODES = ("none", "air", "liquid", "still")
#: The AXIAL end faces: ``still`` = the coils / cores / magnets hand heat to the
#: room off both ends of the machine (``cooling_models.end_face_still``),
#: ``none`` = both ends are buried (a joint sandwiched between a gearbox and the
#: arm casting).  Robotics only, and ``still`` there by default — the user's
#: decision, from the Fusion model: the 24 coils stand proud of the core on both
#: sides and the core's own end faces are largely uncovered.
END_FACE_MODES = ("still", "none")
#: How the machine is BUILT, which decides whether the end windings and the slot
#: channels are in the airflow at all (user 2026-09-09, on the 40 mm "CIANO14 40
#: new": *"нет корпуса"* — the tooth blocks with their coils are held between two
#: end plates by standoff pins and the end turns sit in the propeller wash).
#: ``housed`` is the model this router has always solved and stays the default,
#: bit for bit; ``open`` adds the two paths — see ``solve_thermal_field``.
FRAME_MODES = ("housed", "open")


def _norm_end_faces(v) -> str:
    """``'still'`` | ``'none'``, lower-cased — never guessed (an unknown value is
    refused by name in ``_validate_field_params``)."""
    return str(v or "still").strip().lower()


def _norm_frame(frame) -> str:
    """``'housed'`` | ``'open'``, lower-cased.  Never guesses: an unknown value
    is refused by name in ``_validate_field_params``, and this only normalises
    the spelling once so the cache key, the sinks and the payload cannot end up
    keyed on ``'Open'`` and solved as ``'housed'``."""
    return str(frame or "housed").strip().lower()


def _bad(field: str, value, kind: str, message: str, error: Optional[str] = None,
         error_code: Optional[str] = None, invalid: Optional[List[dict]] = None):
    """A 422 in this project's shape: an ``error`` line for the toast and an
    ``invalid_parameters`` list naming the field, so the fix is one edit and not
    a guess.  Client-facing rule: never solve a machine nobody built.

    ``invalid`` overrides that list (pass ``[]`` for a refusal that is not about
    one bad field — see ``_no_electromagnetic_run``: nothing the user typed is
    wrong, the run they have to make first simply does not exist yet), and
    ``error_code`` adds a stable machine-readable tag beside the English so a
    panel can offer "run it on the Electromagnetic tab" as a BUTTON instead of
    asking the user to parse a sentence.
    """
    detail: Dict[str, Any] = {
        "error": error or message,
        "invalid_parameters": ([{"field": field, "value": value,
                                 "kind": kind, "message": message}]
                               if invalid is None else list(invalid))}
    if error_code:
        detail["error_code"] = error_code
    return HTTPException(status_code=422, detail=detail)


def _validate_field_params(*, cooling_mode: str, ambient_temp: float,
                           h_conv: float, air_speed_mps: float, fluid: str,
                           fluid_temp_in_c: float, flow_lpm: float,
                           bore_mode: str = "none",
                           bore_air_speed_mps: float = 0.0,
                           bore_fluid: str = "water",
                           bore_fluid_temp_in_c: float = 25.0,
                           bore_flow_lpm: float = 0.0,
                           shaft_ext_length_mm: float = 0.0,
                           shaft_ext_diameter_mm: float = 0.0,
                           shaft_ext_sides: int = 2,
                           frame: str = "housed",
                           open_air_speed_mps: float = 0.0,
                           emissivity: float = 0.9,
                           mount_g_w_per_k: float = 0.0,
                           mount_temp_c: Optional[float] = None,
                           end_faces: str = "still",
                           end_face_sides: int = 2,
                           slot_k: float = 0.0, rpm: float = 0.0,
                           I_phase_rms: float = 0.0, coil_temp_c: float = 25.0,
                           n_steps_per_period: int = 1, n_periods: float = 1.0,
                           mesh_size_mm: float = 1.0, min_size_mm: float = 1.0,
                           outer_air_factor: float = 1.0):
    """Refuse the impossible before spending a minute of FEM on it.

    Returns ``(cooling_mode, bore_mode)`` normalised to lower case.

    Every check here is one an engineer can act on from the message alone.  The
    cooling block is the one worth spelling out:

      * a LIQUID surface without a flow rate is the single easiest way to get a
        plausible-looking wrong answer out of the new model — ṁ = ρ·Q, so Q = 0
        is a coolant that never leaves, whose outlet temperature is infinite.
        Refused by name on both surfaces;
      * an unknown fluid is NAMED rather than quietly solved as water
        (``_coolant_props`` falls back to water, which is right for a solve that
        is already running and wrong as an answer);
      * "nothing is cooled" is refused outright.  With every boundary adiabatic
        the steady problem has no solution at all — the machine heats up for
        ever — and answering it with a number would be answering a different
        question.

    The SHAFT-ENDS block (2026-09-07) is the third path, and it is validated
    here rather than clamped for the same reason: a negative exposed length is
    not a shorter stub, and there is no third end on a shaft.  Zero length is
    legal and means the path is off — the rotor's end faces and the end windings
    are inside the closed housing and have nowhere else to send their heat.
    """
    mode = str(cooling_mode or "manual").strip().lower()
    bore = str(bore_mode or "none").strip().lower()
    if mode not in COOLING_MODES:
        raise _bad("cooling_mode", cooling_mode, "bad_value",
                   "cooling_mode must be 'manual' (your own h), 'air' (housing "
                   "in cross-flow), 'liquid' (jacket at a given inlet "
                   "temperature and flow), 'robotics' (a joint in still air: "
                   "natural convection + radiation on the housing, an open bore, "
                   "the axial end faces and a bolted mount) or 'none' (uncooled "
                   "housing — only with bore_mode or a mount conductance set)",
                   error=f"unknown cooling mode {cooling_mode!r}")
    if bore not in BORE_MODES:
        raise _bad("bore_mode", bore_mode, "bad_value",
                   "bore_mode cools the rotor's inner diameter (the hollow "
                   "shaft) and must be 'none', 'still' (an open, unventilated "
                   "bore — cooling_mode=robotics only), 'air' (blown axially "
                   "through the bore) or 'liquid' (coolant through the shaft)",
                   error=f"unknown bore cooling mode {bore_mode!r}")
    if bore == "still" and mode != "robotics":
        # The still bore is evaluated with `emissivity`, which is a parameter of
        # the robotics mode and of no other (the panel does not send it
        # otherwise — see thermal_settings.cooling_fields), so a still bore
        # beside a jacket would be solved with a radiation term nobody typed.
        raise _bad("bore_mode", bore_mode, "bad_value",
                   "bore_mode='still' is the OPEN, unventilated bore of the "
                   "robotics mode: it radiates out of the two ends at the "
                   "machine's emissivity, and that input only exists when "
                   "cooling_mode='robotics'.  Use cooling_mode='robotics', or "
                   "bore_mode 'none' / 'air' / 'liquid'.",
                   error="bore_mode='still' outside the robotics mode")
    _mount_g = float(mount_g_w_per_k or 0.0)
    if mode == "none" and bore == "none" and not _mount_g > 0.0:
        # WIDENED 2026-09-14: a machine bolted to a cold arm IS cooled, even with
        # every film switched off — the mount is a conductance to a held
        # temperature and the steady problem has a solution.  What is refused is
        # the machine with no door at all.
        raise _bad("cooling_mode", cooling_mode, "bad_value",
                   "no cooled surface and no mount conductance: with "
                   "cooling_mode='none' and bore_mode='none' every boundary is "
                   "adiabatic, so there is no steady temperature to solve for — "
                   "the machine heats up without limit.  Cool the housing, cool "
                   "the bore, or give it a mount conductance "
                   "(mount_g_w_per_k > 0).",
                   error="no cooled surface and no mount conductance")

    for name, val in (("ambient_temp", ambient_temp),
                      ("fluid_temp_in_c", fluid_temp_in_c),
                      ("bore_fluid_temp_in_c", bore_fluid_temp_in_c),
                      ("coil_temp_c", coil_temp_c)):
        if not math.isfinite(float(val)):
            raise _bad(name, val, "bad_value", f"{name} must be a finite °C value")
    if float(ambient_temp) <= -273.15:
        raise _bad("ambient_temp", ambient_temp, "bad_value",
                   "ambient_temp is in °C and must be above absolute zero")
    if float(coil_temp_c) <= -273.15:
        raise _bad("coil_temp_c", coil_temp_c, "bad_value",
                   "coil_temp_c is the winding temperature the EM losses are "
                   "evaluated at, in °C, and must be above absolute zero")

    if mode == "manual" and float(h_conv) <= 0.0:
        raise _bad("h_conv", h_conv, "bad_value",
                   "cooling_mode=manual holds the housing at ambient through "
                   "h·(T−T∞); pass h_conv > 0 W/m²·K, or use cooling_mode=air "
                   "to have it computed from an air speed")
    if mode == "air" and float(air_speed_mps) < 0.0:
        raise _bad("air_speed_mps", air_speed_mps, "bad_value",
                   "air_speed_mps is the airflow over the housing and cannot be "
                   "negative; 0 = still air (natural convection floor)")
    if mode == "liquid":
        if not _known_coolant(fluid):
            raise _bad("fluid", fluid, "unknown_material",
                       "no coolant named %r in the materials library; known "
                       "fallbacks: %s" % (fluid, ", ".join(sorted(_COOLANT_FALLBACK))),
                       error=f"unknown coolant {fluid!r}")
        if not math.isfinite(float(flow_lpm)) or float(flow_lpm) <= 0.0:
            raise _bad("flow_lpm", flow_lpm, "bad_value",
                       "cooling_mode=liquid needs the jacket flow in L/min: the "
                       "outlet temperature is a RESULT (T_out = T_in + P/(ṁ·cp)) "
                       "and ṁ = ρ·Q, so a zero flow is a coolant that never "
                       "leaves the machine",
                       error="liquid cooling without a flow rate")
    if bore == "air" and float(bore_air_speed_mps) < 0.0:
        raise _bad("bore_air_speed_mps", bore_air_speed_mps, "bad_value",
                   "bore_air_speed_mps is the axial air speed through the rotor "
                   "bore and cannot be negative")
    if bore == "liquid":
        if not _known_coolant(bore_fluid):
            raise _bad("bore_fluid", bore_fluid, "unknown_material",
                       "no coolant named %r in the materials library; known "
                       "fallbacks: %s" % (bore_fluid,
                                          ", ".join(sorted(_COOLANT_FALLBACK))),
                       error=f"unknown bore coolant {bore_fluid!r}")
        if not math.isfinite(float(bore_flow_lpm)) or float(bore_flow_lpm) <= 0.0:
            raise _bad("bore_flow_lpm", bore_flow_lpm, "bad_value",
                       "bore_mode=liquid needs the shaft flow in L/min: the "
                       "outlet temperature is a RESULT and ṁ = ρ·Q, so a zero "
                       "flow removes no heat at all",
                       error="bore liquid cooling without a flow rate")

    # The SHAFT ENDS — the one axial path the user asked for (2026-09-07):
    # "торцы и лобовые части — только для вала, всё остальное вращается внутри
    # мотора".  Length 0 turns it off, so only a NEGATIVE or non-finite length is
    # a refusal; a negative stub is not a shorter one.
    if not math.isfinite(float(shaft_ext_length_mm)) or float(shaft_ext_length_mm) < 0.0:
        raise _bad("shaft_ext_length_mm", shaft_ext_length_mm, "bad_value",
                   "shaft_ext_length_mm is how much shaft sticks out of the "
                   "housing on EACH side, in mm; 0 turns the path off and "
                   "negative is not a length",
                   error="negative exposed shaft length")
    if not math.isfinite(float(shaft_ext_diameter_mm)) or float(shaft_ext_diameter_mm) < 0.0:
        raise _bad("shaft_ext_diameter_mm", shaft_ext_diameter_mm, "bad_value",
                   "shaft_ext_diameter_mm is the OUTSIDE diameter of the "
                   "exposed shaft in mm; 0 = derive it from the geometry (the "
                   "shaft tube's OD) and negative is not a diameter")
    if int(shaft_ext_sides) not in (1, 2):
        raise _bad("shaft_ext_sides", shaft_ext_sides, "bad_value",
                   "shaft_ext_sides is how many ends of the shaft come out of "
                   "the housing: 2 for a through-shaft, 1 when the non-drive "
                   "end is capped",
                   error=f"shaft_ext_sides must be 1 or 2, got {shaft_ext_sides!r}")

    # THE FRAME (2026-09-09).  ``housed`` is the machine every answer this
    # router has ever given describes; ``open`` is the CIANO14 40 — no housing,
    # tooth blocks between two end plates, end turns and slot channels in the
    # propeller wash.  Refused by name rather than defaulted, because the two
    # answers differ by a heat path worth tens of kelvin and a typo'd frame that
    # quietly solved the housed machine would look like a result.
    if _norm_frame(frame) not in FRAME_MODES:
        raise _bad("frame", frame, "bad_value",
                   "frame says how the machine is BUILT: 'housed' (the default "
                   "— the end windings and the slot air are inside a closed "
                   "housing and have nowhere else to send their heat) or 'open' "
                   "(no housing: the end turns and the axial channels between "
                   "neighbouring coils are in the airflow)",
                   error=f"unknown frame {frame!r}")
    if not math.isfinite(float(open_air_speed_mps)) or float(open_air_speed_mps) < 0.0:
        raise _bad("open_air_speed_mps", open_air_speed_mps, "bad_value",
                   "open_air_speed_mps is the air speed over the end windings "
                   "and through the slot channels of an OPEN machine, in m/s; "
                   "0 = take the housing's own air speed when the outer surface "
                   "is in air, else still air (the natural-convection floor).  "
                   "Negative is not a speed.",
                   error="negative open-frame air speed")

    # ── THE ROBOTICS MODE's own three inputs (2026-09-14) ───────────────────
    # ε = 0 is LEGAL and meaningful — a bare polished-aluminium housing radiates
    # nothing worth counting, and on this machine that removes more than half the
    # air-side cooling — so only a NEGATIVE or a greater-than-black emissivity is
    # a refusal.  Nothing here is clamped: an ε of 1.5 is a typo, and a typo that
    # solves is a result nobody can reproduce.
    if not math.isfinite(float(emissivity)) or not (0.0 <= float(emissivity) <= 1.0):
        raise _bad("emissivity", emissivity, "bad_value",
                   "emissivity is the housing's total hemispherical emissivity "
                   "and lies between 0 and 1: ~0.9 for anodised, painted or "
                   "oxidised surfaces, ~0.05 for bare polished aluminium.  0 "
                   "switches the radiation term off exactly.",
                   error="emissivity must be between 0 and 1")
    if not math.isfinite(_mount_g) or _mount_g < 0.0:
        raise _bad("mount_g_w_per_k", mount_g_w_per_k, "bad_value",
                   "mount_g_w_per_k is the bolted flange's contact conductance "
                   "into the arm, in W/K, and cannot be negative; 0 turns the "
                   "path off and models a machine bolted to NOTHING (for scale "
                   "on an Ø85 joint: 0.5 W/K a dry interface through a few M4 "
                   "bolts, 2 W/K a machined face with compound, 10 W/K a housing "
                   "that is part of the arm casting)",
                   error="negative mount conductance")
    if mount_temp_c is not None:
        if not math.isfinite(float(mount_temp_c)):
            raise _bad("mount_temp_c", mount_temp_c, "bad_value",
                       "mount_temp_c is the temperature the mount is HELD at, "
                       "in °C; leave it blank to use the ambient")
        if float(mount_temp_c) <= -273.15:
            raise _bad("mount_temp_c", mount_temp_c, "bad_value",
                       "mount_temp_c is in °C and must be above absolute zero")
    if _norm_end_faces(end_faces) not in END_FACE_MODES:
        raise _bad("end_faces", end_faces, "bad_value",
                   "end_faces says whether the machine's AXIAL faces are exposed: "
                   "'still' (the default — the end turns stand proud of the core "
                   "and the core / magnet end faces are uncovered, so each hands "
                   "heat to the room by natural convection + radiation) or 'none' "
                   "(both ends buried against a gearbox and the arm)",
                   error=f"unknown end_faces mode {end_faces!r}")
    if int(end_face_sides) not in (1, 2):
        raise _bad("end_face_sides", end_face_sides, "bad_value",
                   "end_face_sides is how many ends of the machine are exposed: "
                   "2 for a joint open at both ends, 1 when one end is against "
                   "the gearbox",
                   error=f"end_face_sides must be 1 or 2, got {end_face_sides!r}")

    if float(slot_k) < 0.0:
        raise _bad("slot_k", slot_k, "bad_value",
                   "slot_k is the winding bulk transverse conductivity in "
                   "W/m·K; 0 = derive it from the wire stack, negative is not a "
                   "conductivity")
    if not math.isfinite(float(rpm)):
        raise _bad("rpm", rpm, "bad_value",
                   "rpm sets the air-gap Taylor number; pass a finite speed")
    if not math.isfinite(float(I_phase_rms)) or float(I_phase_rms) < 0.0:
        raise _bad("I_phase_rms", I_phase_rms, "bad_value",
                   "I_phase_rms is the phase current the losses are solved at, "
                   "in A rms, and cannot be negative")

    if int(n_steps_per_period) < 1:
        raise _bad("n_steps_per_period", n_steps_per_period, "bad_value",
                   "n_steps_per_period selects WHICH Electromagnetic run's "
                   "cycle-averaged loss map this temperature is solved from, and "
                   "a run has at least one frame per electrical period")
    if not math.isfinite(float(n_periods)) or float(n_periods) <= 0.0:
        raise _bad("n_periods", n_periods, "bad_value",
                   "n_periods must be > 0 — a zero-length transient has no "
                   "losses to conduct")
    if not math.isfinite(float(mesh_size_mm)) or float(mesh_size_mm) <= 0.0:
        raise _bad("mesh_size_mm", mesh_size_mm, "bad_value",
                   "mesh_size_mm is the target element size in mm and must be "
                   "positive")
    if not math.isfinite(float(min_size_mm)) or float(min_size_mm) <= 0.0:
        raise _bad("min_size_mm", min_size_mm, "bad_value",
                   "min_size_mm is the smallest element the mesher may make, in "
                   "mm, and must be positive")
    if float(min_size_mm) > float(mesh_size_mm):
        raise _bad("min_size_mm", min_size_mm, "bad_value",
                   "min_size_mm (%.3f) is larger than mesh_size_mm (%.3f): the "
                   "floor cannot be coarser than the target"
                   % (float(min_size_mm), float(mesh_size_mm)))
    if float(outer_air_factor) < 1.0:
        raise _bad("outer_air_factor", outer_air_factor, "bad_value",
                   "outer_air_factor scales the far-field air ring around the "
                   "stator and must be ≥ 1.0")
    return mode, bore


# ---------------------------------------------------------------------------
# Mesh sizing shared by the solve and the mesh preview
# ---------------------------------------------------------------------------

def _auto_coil_mesh(component_mesh: str, mesh_size_mm: float, geo_ov) -> str:
    """Auto-refine the COIL mesh for the thermal solve.

    A slot meshed ~1 element across thermally shorts the windings to the iron
    (every coil node sits on the slot wall, shared with k≈25 steel) → no interior
    node can heat up and the winding hotspot collapses, regardless of the
    (correct) homogenised slot_k.  Target ~4 elements across the slot width so
    the winding gradient resolves.

    Shared by /field and /mesh on purpose: the preview must be the mesh the solve
    will colour in, and this refinement is part of it.
    """
    from motor_ai_sim.routes.simulation import _parse_component_mesh
    try:
        from motor_ai_sim.config import get_config as _get_cfg
        from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _merge_geo
        # merge_geo_override, not a dict-update: slot_width is DERIVED and the
        # override carries primaries only, so an update would size the thermal
        # coil mesh from whatever design the shared config held.
        _g0 = _merge_geo(dict(_get_cfg().get("geometry", {})), geo_ov)
        _slot_w = float(_g0.get("slot_width", 3.0) or 3.0)
        _cm0 = _parse_component_mesh(component_mesh)
        if "coil" not in _cm0:
            import json as _json
            _cm0["coil"] = round(max(0.4, min(_slot_w / 4.0, float(mesh_size_mm))), 3)
            return _json.dumps(_cm0)
    except Exception:  # noqa: BLE001 — a mesh cosmetic never fails a solve
        pass
    return component_mesh


def _snap_n_sectors(n_sectors: int, geo_ov) -> int:
    """The sector count the EM solve will ACTUALLY use, for the mesh preview.

    Copies ``routes.simulation._fem_field2d_impl``'s rule, and must: an
    n_sectors that is not a divisor of GCD(slots, poles) builds a broken wedge,
    and ``n_sectors<=1`` means "P2 auto-symmetry" (the machine's natural
    anti-periodic wedge), not "full disk".  Returns -1 for a full disk, which is
    what the mesh builder reads as a full ring.
    """
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    try:
        motor = CadQueryMotor()
        if geo_ov:
            motor.set_parameters(geo_ov)
        mp = motor.parameters
        slots = int(mp.get("num_slots") or 0)
        poles = int(mp.get("num_poles") or 0)
    except Exception:  # noqa: BLE001
        slots = poles = 0
    gcd = math.gcd(slots, poles) if (slots and poles) else 1
    req = int(n_sectors)
    if req > 1:
        valid = [dv for dv in range(2, gcd + 1) if gcd % dv == 0]
        return max([dv for dv in valid if dv <= req], default=-1)
    try:
        from motor_ai_sim.config import get_config as _gc
        g = dict((_gc().get("geometry", {})) or {})
        if geo_ov:
            g = {**g, **geo_ov}
        sym = int(g.get("num_seg")
                  or math.gcd(int(g.get("num_slots", 1)),
                              int(g.get("num_poles", 1))) or 1)
    except Exception:  # noqa: BLE001
        sym = gcd
    return sym if sym >= 2 else -1


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------
# Bounded, unlike the plain dict this replaces: a thermal payload carries a
# temperature per node and a flux vector per triangle, so an unbounded store of
# them is a slow memory leak on a machine that is being swept.
#
# `clear_thermal_caches` is called from routes.simulation.clear_simulation_caches,
# so a geometry PUT / material PATCH / Run drops these too: the answer describes a
# cross-section and its material k's, and a stale cross-section is a wrong
# temperature.

# Migration Stage 3: one set of these PER WORKSPACE, not per process.  The caps
# below are per workspace and unchanged from the single-user ones — the store
# they name is now the caller's, and `workspace.MAX_WORKSPACES` bounds how many
# of them a server may hold at once.  `lru_on_read=True`: these are pure
# hit/miss caches, nothing reads their order back, so recency is the right
# thing to evict by.
_FIELD_CACHE_MAX = 8
_COUPLED_CACHE_MAX = 4
_MESH_CACHE_MAX = 4
_FIELD_CACHE = _WSP.ws_map("thermal.field_cache", _FIELD_CACHE_MAX,
                           lru_on_read=True)
_COUPLED_CACHE = _WSP.ws_map("thermal.coupled_cache", _COUPLED_CACHE_MAX,
                             lru_on_read=True)
_MESH_CACHE = _WSP.ws_map("thermal.mesh_cache", _MESH_CACHE_MAX,
                          lru_on_read=True)


def _cache_put(store: "OrderedDict", key: tuple, value, cap: int) -> None:
    if len(store) >= cap:
        store.popitem(last=False)
    store[key] = value


def clear_thermal_caches(reason: str = "") -> int:
    """Drop every cached temperature map, coupled run and thermal mesh.

    Called from ``routes.simulation.clear_simulation_caches`` beside
    ``clear_mechanical_caches``, for the same reason: the map is keyed on a
    cross-section and a material assignment, and both change under this route's
    feet when the shared config is written.

    ``_LAST`` is deliberately NOT cleared — it is not a cache, it is what the
    Thermal tab SHOWS when you come back to it.  Throwing it away on a geometry
    edit would replace the badge with a blank page, which is the bug, not the
    fix; every /last response carries the fingerprint it was solved for so the
    staleness is stated rather than hidden.
    """
    n = len(_FIELD_CACHE) + len(_COUPLED_CACHE) + len(_MESH_CACHE)
    _FIELD_CACHE.clear()
    _COUPLED_CACHE.clear()
    _MESH_CACHE.clear()
    if n:
        log.info("thermal caches cleared (%s): %d entries", reason or "?", n)
    return n


def _field_cache_key(geo_ov, assign, *, ambient_temp, h_conv, slot_k,
                     rpm, gamma_deg, I_phase_rms, n_steps_per_period, n_periods,
                     mesh_size_mm, min_size_mm, outer_air_factor, n_sectors,
                     coil_temp_c, component_mesh, cooling_mode, air_speed_mps,
                     fluid, fluid_temp_in_c, flow_lpm,
                     bore_mode="none", bore_air_speed_mps=0.0,
                     bore_fluid="water", bore_fluid_temp_in_c=25.0,
                     bore_flow_lpm=0.0, shaft_ext_length_mm=0.0,
                     shaft_ext_diameter_mm=0.0, shaft_ext_sides=2,
                     frame="housed", open_air_speed_mps=0.0,
                     emissivity=0.9, mount_g_w_per_k=0.0, mount_temp_c=None,
                     end_faces="still", end_face_sides=2) -> tuple:
    """What makes two thermal requests the SAME request.

    Three groups, and all three are load-bearing:

      * the machine — the geometry fingerprint, the material assignment, the
        names of any inline material cards, plus ``_config_physics_fingerprint``
        (the winding and the parts the URL does not spell out).  Without it,
        editing a geometry parameter and reopening the Thermal view with the same
        knobs replayed the OLD machine's temperature map until a restart;
      * the physics — γ, I, rpm, coil temperature, frames;
      * the COOLING — BOTH surfaces: mode, fluid, inlet temperature, flow, air
        speed, on the housing and on the rotor bore.  A water-jacketed machine
        and the same machine in still air are two answers, not two views of one,
        and since 2026-09-07 so are the same machine with and without air blown
        through its shaft.  Every new field is in here; a bore parameter that
        did not reach this tuple would serve a bore-cooled request the uncooled
        map it computed a minute earlier.

    ``gap_k`` is deliberately ABSENT: the air-gap conductivity is no longer an
    input, it is derived from the clearance, the speed and the gap temperature,
    all of which are already keyed.

    ``frame`` / ``open_air_speed_mps`` (2026-09-09) are APPENDED, and only when
    the frame is ``open`` — the same rule ``magnet_temp_c`` follows below and the
    same one ``thermal_settings`` states for the request itself ("a parameter the
    chosen mode does not use is not sent").  A housed request must produce the
    byte-identical tuple this function has always built, or the first request
    after this change would miss every map already in the cache; an open one
    must not share an entry with it, because it is solved with two extra heat
    paths.

    The ROBOTICS block (2026-09-14) follows exactly the same rule, in two
    independent pieces because the two are independent inputs:

      * ``emissivity`` / ``end_faces`` / ``end_face_sides`` ride along only when
        ``cooling_mode == "robotics"`` — nothing else reads them, and a polished
        housing is a genuinely different machine from a painted one;
      * ``mount_g_w_per_k`` / ``mount_temp_c`` ride along whenever the mount
        conductance is non-zero, IN ANY MODE: a jacketed machine is bolted to
        something too, and the mount is the path that decides this machine's
        temperature.  G = 0 is the machine bolted to nothing, i.e. the tuple
        every entry already in the cache was stored under.
    """
    from motor_ai_sim.routes.simulation import (_config_physics_fingerprint,
                                                _geometry_fingerprint)
    # The VALUES the solve will read off the library for the assigned cards, not
    # only their names: the user edits the catalogue (Al2O3 30 → 24 W/m·K,
    # 2026-09-07) and the materials module follows the file's mtime at once —
    # a key made of names alone would serve the map solved with the old number
    # for as long as the process lives.
    def _k_of(part, cat, default):
        try:
            name = assign.get(part)
            v = _thermal_k(cat, name, default) if cat else _thermal_k_any(name, default)
            return round(float(v), 4)
        except Exception:  # noqa: BLE001
            return None
    _mat_k = (
        _k_of("slot_insulation", "insulator", 0.14), _k_of("wire_insulation", "insulator", 0.12),
        _k_of("stator_core", "steel", 25.0), _k_of("rotor_core", "steel", 25.0),
        _k_of("magnet", "magnet", 8.0), _k_of("shaft", None, 150.0),
        _k_of("sleeve", None, 0.7), _k_of("slot", None, 385.0),
    )
    _base = (
        _geometry_fingerprint(geo_ov),
        _config_physics_fingerprint(with_request_materials=True),
        tuple(sorted((str(k), str(v)) for k, v in assign.items())),
        tuple(sorted(_override_props().keys())),
        _mat_k,
        round(float(ambient_temp), 1), round(float(h_conv), 1),
        round(float(slot_k), 3),
        round(float(rpm), 1), round(float(gamma_deg), 1),
        round(float(I_phase_rms), 1),
        int(n_steps_per_period), round(float(n_periods), 2),
        round(float(mesh_size_mm), 2), round(float(min_size_mm), 2),
        round(float(outer_air_factor), 2), int(n_sectors),
        round(float(coil_temp_c), 1), str(component_mesh),
        str(cooling_mode), round(float(air_speed_mps), 2), str(fluid),
        round(float(fluid_temp_in_c), 1), round(float(flow_lpm), 3),
        str(bore_mode), round(float(bore_air_speed_mps), 2), str(bore_fluid),
        round(float(bore_fluid_temp_in_c), 1), round(float(bore_flow_lpm), 3),
        # …and the THIRD path: the shaft sticking out of the housing.  Same rule
        # as the bore fields above — a machine with 100 mm of shaft in the room
        # and the same machine with none are two answers, not two views of one.
        round(float(shaft_ext_length_mm), 2),
        round(float(shaft_ext_diameter_mm), 2), int(shaft_ext_sides),
    )
    # …and the FRAME, appended only when it is not the housed machine — see the
    # docstring.  ``open`` alone would be enough to separate the two answers, but
    # the speed decides both new conductances, so it rides with it.
    if _norm_frame(frame) == "open":
        _base = _base + ("frame", "open", round(float(open_air_speed_mps), 2))
    if str(cooling_mode or "").strip().lower() == "robotics":
        _base = _base + ("robotics", round(float(emissivity), 4),
                         _norm_end_faces(end_faces), int(end_face_sides))
    _g = float(mount_g_w_per_k or 0.0)
    if _g > 0.0:
        _base = _base + ("mount", round(_g, 5),
                         (None if mount_temp_c is None
                          else round(float(mount_temp_c), 2)))
    return _base


# ---------------------------------------------------------------------------
# The LAST result, kept across tab switches and backend restarts
# ---------------------------------------------------------------------------
# Same store the Mechanical tab has (routes.mechanical._LAST), for the same
# reason: re-entering a tab must restore the last picture and its INPUT fields,
# not a blank page — and a thermal map costs a full EM transient, so re-solving
# it on every tab switch is the most expensive possible way to redraw something
# the process already has.
#
# One in-memory entry per kind, persisted beside the config as a pickle, written
# off-thread and atomically, loaded LAZILY at the first /last request so
# importing this module never touches the disk.

_LAST_KINDS = ("field", "coupled")
#: …plus the DUTY CYCLE (2026-09-14), which is persisted and restored with them
#: but is NOT served by ``GET /last``: it has its own
#: ``GET /duty_cycle/last``, because it is not a temperature map the Thermal tab
#: draws — it is a transient answer about a whole cycle, and a panel asking "is
#: there a map to come back to?" must not be told yes because a cycle was solved.
_PERSIST_KINDS = _LAST_KINDS + ("duty_cycle",)
_HEAVY_KEYS = ("temperature_per_node", "heat_flux_per_tri", "flux_mag_per_tri",
               "grad_T_mag_per_tri")
#: Migration Stage 3: per WORKSPACE.  Three kinds are persisted, so the cap is
#: a safety valve an order of magnitude above what the store can hold.
_LAST_MAX = 16
_LAST = _WSP.ws_map("thermal.last", _LAST_MAX)
#: "Have I read my pickle yet" — per workspace, because the pickle is too:
#: a process-wide flag would mean the SECOND account to ask never restores its
#: own ``.last_thermal.pkl`` and opens the Thermal tab blank.  Kept readable
#: and writable as a module name (``__getattr__`` below); a test that assigns
#: it wins for the rest of the process, which is what those tests want — in the
#: PROCESS workspace only (``workspace.module_override_applies``), because a
#: module attribute cannot be un-created and an unscoped override would turn
#: the flag back into the process-wide one this stage removed.
_SLOT_LAST_LOADED = "thermal.last_loaded"
_UNSET = object()


def _last_loaded() -> bool:
    ov = globals().get("_LAST_LOADED", _UNSET)
    if ov is not _UNSET and _WSP.module_override_applies():
        return bool(ov)
    return bool(_WSP.state().flag(_SLOT_LAST_LOADED, False))


def _set_last_loaded(value: bool) -> None:
    if "_LAST_LOADED" in globals() and _WSP.module_override_applies():
        globals()["_LAST_LOADED"] = value
    else:
        _WSP.state().set_flag(_SLOT_LAST_LOADED, value)


def _last_store_path() -> str:
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        base = str(_ws_root())
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    return os.path.abspath(os.path.join(base, ".last_thermal.pkl"))


def _persist_last() -> None:
    """Write the whole store out, off-thread and atomically."""
    import threading

    # Under the store's OWN lock — see ``workspace.BoundedStore.snapshot`` and
    # the note in ``routes.mechanical._persist_last``: iterating the live store
    # while another solve writes it raised "dictionary changed size during
    # iteration" and the pickle was silently skipped.  A plain dict (the tests'
    # monkeypatch) has no ``snapshot``; ``dict()`` is the same shallow copy.
    _snap = getattr(_LAST, "snapshot", None)
    snapshot = _snap() if callable(_snap) else dict(_LAST)

    def _write():
        try:
            import pickle as pk
            p = _last_store_path()
            with open(p + ".tmp", "wb") as fh:
                pk.dump(snapshot, fh, protocol=pk.HIGHEST_PROTOCOL)
            os.replace(p + ".tmp", p)
        except Exception as exc:  # noqa: BLE001 - a viewer convenience never breaks a solve
            log.warning("could not persist the last thermal result: %s", exc)

    # Stage 3: `_last_store_path()` is resolved INSIDE the thread, and a fresh
    # thread has an empty context — it would write the caller's answer into the
    # process workspace.  `workspace.bind` carries this call's workspace across.
    threading.Thread(target=_WSP.bind(_write), daemon=True).start()


def _load_last() -> None:
    if _last_loaded():
        return
    _set_last_loaded(True)
    try:
        import pickle as pk
        p = _last_store_path()
        if not os.path.exists(p):
            return
        with open(p, "rb") as fh:
            blob = pk.load(fh)
        if isinstance(blob, dict):
            for k in _PERSIST_KINDS:
                e = blob.get(k)
                if isinstance(e, dict) and e.get("result") is not None:
                    _LAST.setdefault(k, e)
            log.info("restored the last thermal result(s) from %s: %s",
                     p, ", ".join(sorted(_LAST)) or "none")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not restore the last thermal result: %s", exc)


def _remember_last(kind: str, result: Dict[str, Any], params: Dict[str, Any],
                   fp: Optional[str], *, duty: Optional[str] = None,
                   die: Optional[str] = None, cfg: Optional[str] = None) -> None:
    """Park THIS answer as "what the Thermal tab was last showing".

    ``die`` / ``cfg`` / ``duty`` are the duty-cycle route's: it is TOLD which
    duty it solved (its request may name one) instead of having to hope the
    editor still has that one open, and the per-duty copy below is filed under
    that name.  Every other caller leaves them ``None`` and the active catalog
    context decides, exactly as before.

    A run marked ``record: false`` (``run_recording`` — the duty-cycle editor's
    calibration run, made at ANOTHER duty's point while this machine is loaded)
    is not "what the Thermal tab was last showing" and never becomes it: no
    ``_LAST``, no ``config/.last_thermal.pkl``, no per-duty row, no per-duty
    map.  Its map is still returned to the caller, which is all it was for.
    """
    import datetime as _dt
    from motor_ai_sim import run_recording as _rr
    if _rr.suppressed():
        log.info("thermal: %s solved for another duty (record: false) — "
                 "not remembered as this machine's last result", kind)
        return
    try:
        _load_last()          # never let a lazy load overwrite what we just stored
        _LAST[kind] = {
            # A PRIVATE shallow copy — same reason as
            # ``routes.mechanical._remember_last``: ``_persist_last`` pickles
            # this entry on a background thread, and a caller that goes on
            # editing the dict it just handed over (the cache-hit branches do)
            # resizes it mid-pickle: "dictionary changed size during iteration",
            # caught, logged, and the pickle silently not written.
            "result": dict(result) if isinstance(result, dict) else result,
            # The request that produced it, so re-entering the tab restores the
            # INPUT fields too and a Solve press reproduces the picture.
            "params": dict(params),
            "geometry_fingerprint": fp,
            "computed_at": _dt.datetime.now(_dt.timezone.utc)
                              .isoformat(timespec="seconds"),
        }
        _persist_last()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not remember the last thermal %s: %s", kind, exc)
    # ── and once more, PER DUTY (2026-09-09) ────────────────────────────────
    # The store above is one entry per MACHINE: solving a second duty overwrites
    # the first, so a report of a configuration with several duties could only
    # ever show one thermal column.  The user asked for a comparison across
    # every simulation — *"если в конфигурации несколько режимов, их нужно
    # сравнивать в таблицах по всем моделированиям"* — so a COMPACT copy (no
    # per-node field) is filed under the duty the catalog context names.
    # Guarded twice over: the write itself never raises, and a failure here must
    # not turn a finished solve into an error.
    #
    # The DUTY CYCLE (2026-09-14) files its own kind and NOTHING else: it is not
    # a temperature map, so putting it through `note_thermal` would overwrite
    # this duty's thermal row with a compact_thermal() of a payload that has no
    # components and no heat budget — a report column silently emptied by a
    # different solve, which is the exact failure class `duty_results` exists to
    # prevent.  It has no per-duty FIELD either: there is no mesh in it.
    try:
        from motor_ai_sim import duty_results as _dr
        _at = (_LAST.get(kind) or {}).get("computed_at")
        if kind == "duty_cycle":
            _dr.note_duty_cycle(result, dict(params), fp, _at,
                                die=die, cfg=cfg, duty=duty)
            return
        _dr.note_thermal(result, dict(params), fp, _at)
    except Exception:  # noqa: BLE001 — bookkeeping never fails a solve
        log.debug("thermal: per-duty result not recorded", exc_info=True)
        if kind == "duty_cycle":
            return
    # ── …and the FIELD itself, per duty (2026-09-09) ────────────────────────
    # The compact row above is a table cell; the report also draws each duty's
    # own temperature MAP side by side (user: *"давай сделаем сохранение всех
    # полей моделирования, как электромагнитных, так и тепловых и
    # механических"*), and the map's arrays are in the pickle one machine at a
    # time.  ``duty_fields`` keeps the mesh + T-per-node + flux of THIS duty
    # (~0.31 MB compressed on the 200 mm machine) beside its stored runs.
    # BOTH kinds write it: a coupled run's converged map is this duty's
    # temperature field just as much as a single conduction solve's, and the
    # newest one wins — a duty that was only ever run through the loop must not
    # be reported as having no map.  A payload with no mesh in it (the light
    # cached shapes) packs to nothing and is skipped by the store itself.
    try:
        from motor_ai_sim import duty_fields as _df
        _df.save_active("thermal", result, geometry_fingerprint=fp,
                        computed_at=(_LAST.get(kind) or {}).get("computed_at"))
    except Exception:  # noqa: BLE001 — a stored map never fails a solve
        log.debug("thermal: per-duty field not stored", exc_info=True)


def _strip_heavy(res: Dict[str, Any]) -> Dict[str, Any]:
    """The same answer without the per-node / per-triangle arrays.

    ``/last?field=false`` is how the panel asks "is there anything to come back
    to?" on mount.  On a 200 mm machine the arrays are tens of MB of JSON, and
    shipping them to answer a yes/no question is the reason that question used to
    be worth avoiding.
    """
    if not isinstance(res, dict):
        return res
    out = {k: v for k, v in res.items() if k not in _HEAVY_KEYS}
    inner = out.get("field")
    if isinstance(inner, dict):
        out["field"] = {k: v for k, v in inner.items() if k not in _HEAVY_KEYS}
    return out


# ---------------------------------------------------------------------------
# The cycle-averaged loss map: TAKEN, never computed
# ---------------------------------------------------------------------------
# User, 2026-09-07: *"а зачем считается каждый шаг? нам нужны средние потери
# мотора за весь цикл"*, and the same day: *"нужно как-то разделить тепловые
# расчёты и электромагнитные; если вдруг тепловому расчёту нужно
# электромагнитное моделирование, пусть оно делается во вкладке Simulation"*.
#
# The thermal solve does not want a movie, it wants ONE number per element: the
# cycle-averaged loss density.  It used to buy that number with its OWN 36-frame
# eddy transient every time the operating point moved — one to six minutes,
# started from inside a temperature request, with `/coupled` running it again on
# every pass of the winding-temperature loop.  That is not this tab's physics:
# the loss map is what the Electromagnetic tab computes, and a second, silent
# copy of that solve living behind the Thermal tab is exactly the entanglement
# the user asked to be rid of (it also poisoned the EM warm-seed lineage once —
# see the ContextVar in `solve_thermal_field`).
#
# So there are TWO sources and no third:
#
#   (a) the Electromagnetic RUN the user made — matched on physics identity,
#       replayed on its own mesh;
#   (b') this router's own memory of a map it was handed (`_LOSS_MAPS`), which
#       is what keeps a temperature answerable after the user has moved the
#       Electromagnetic tab to the next operating point.
#
# When neither has one, the request is REFUSED with a 422 that names the run to
# make.  A refusal costs the user one click; a hidden solve costs six minutes,
# a poisoned seed cache, and a number nobody can attribute.
#
# The matching rule is deliberately the project's OWN definition of "the same
# solve": `routes.simulation._field_snap_key_fields`, the key the transient
# stores its snapshot under and the Loss/J⟳ views look it up with.  Writing a
# second, looser rule here is the failure mode this codebase has already paid
# for twice (see `_geo_ov_for_key`): a key that drifts serves one machine's
# numbers for another.  Nothing is approximated on a near miss — a snapshot that
# does not match, or that carries only totals and no per-element map, is a
# refusal, not an estimate.

# Copper resistivity vs temperature — ρ(T) = ρ₂₀·(1 + α(T − 20 °C)), the SAME
# model the loss solver uses (`simulation.losses`, `field_ops.ALPHA_CU`), so the
# analytic scaling in `solve_coupled` below reproduces what a re-solve would
# have computed instead of approximating it with a second law.
try:                                                       # one source of truth
    from motor_ai_sim.simulation.field_ops import ALPHA_CU as ALPHA_CU_PER_K
except Exception:                                          # noqa: BLE001
    ALPHA_CU_PER_K = 0.00393
T_CU_REF_C = 20.0          # the temperature ρ₂₀ is quoted at, not a coil temp

# The collapsed display tag of the winding in an EM field payload
# (`PART_NAMES` below).  Named because the copper scaler selects on it.
_DOM_COIL_VIS = 2


def _cu_rho_ratio(t_c: float, t_ref_c: float) -> float:
    """ρ_Cu(T) / ρ_Cu(T_ref) — the factor copper loss scales by.

    NOT ``1 + α(T − T_ref)``: both temperatures are referred to the 20 °C
    resistivity the material card quotes, so the ratio is
    ``(1 + α(T − 20)) / (1 + α(T_ref − 20))``.  The difference is not academic —
    from 120 °C to 180 °C the linearised form reads 1.236 against the true
    1.169, i.e. a 5.7 % error on the copper loss, which is most of what the
    coupled loop is trying to resolve.
    """
    num = 1.0 + float(ALPHA_CU_PER_K) * (float(t_c) - T_CU_REF_C)
    den = 1.0 + float(ALPHA_CU_PER_K) * (float(t_ref_c) - T_CU_REF_C)
    return float(num) / float(den if abs(den) > 1e-12 else 1e-12)


def _effective_op_mode(op_mode) -> str:
    """``'motor'`` | ``'generator'`` — the argument, else the shared config's
    ``simulation.mode``, else motor: the SAME resolution order the Electromagnetic
    tab's run applies, so a thermal request that names no mode reads the one
    that tab last saved.  (Resolution only — nothing here starts a run.)"""
    raw = op_mode
    if raw is None:
        try:
            from motor_ai_sim.config import get_config as _gc
            raw = (_gc().get("simulation") or {}).get("mode")
        except Exception:  # noqa: BLE001
            raw = None
    m = str(raw or "motor").strip().lower()
    return m if m in ("motor", "generator") else "motor"


def _solved_gamma_deg(gamma_deg, op_mode) -> float:
    """The load angle a run is KEYED on.  Generator mode drives the panel's γ
    shifted by 180° el (the current vector opposes the EMF), and the transient
    folds that shift in BEFORE it builds its key — so a generator run at panel
    γ = −15° is stored under 165°.  Keying the thermal probe on the panel value
    made every generator run invisible to the Thermal tab and to the coupled
    loop ("no Electromagnetic run of this machine at … γ = −15°", 2026-09-08)."""
    g = float(gamma_deg)
    return g + 180.0 if _effective_op_mode(op_mode) == "generator" else g


def _loss_snapshot_probe(*, gamma_deg, I_phase_rms, mesh_size_mm, min_size_mm,
                         outer_air_factor, n_sectors, coil_temp_c,
                         component_mesh, n_steps_per_period, n_periods, geo_ov,
                         magnet_temp_c=None, op_mode=None):
    """This thermal request, spelled as a Simulation-run snapshot key.

    Every constant in here is one the thermal route hard-codes when it calls
    ``get_fem_field2d`` (``eddy``/``rotor_eddy`` on, no demag, current drive,
    P2 elements, the rotor at its CAD zero) or leaves at that function's own
    default (``stator_fillet_mm`` 0, ``gap_layers`` 2, the four mesh-strategy
    flags).  They are written out rather than defaulted so that a change to
    either side is a visible conflict here instead of a silent permanent miss.

    ``rpm`` is deliberately NOT the thermal request's ``rpm`` parameter: that one
    sets the air-gap Taylor number, while the loss map is solved at
    ``simulation.rpm`` from the shared config — which is what
    ``_field_snap_key_fields`` resolves for both sides through
    ``_effective_rpm(None)``.  Keying the wrong one of the two would compare a
    gap velocity against an electrical frequency.
    """
    from motor_ai_sim.routes.simulation import (_config_physics_fingerprint,
                                                _field_snap_key_fields,
                                                _get_request_materials_safe,
                                                _parse_component_mesh)

    # The same two normalisations `_fem_field2d_impl` applies before it probes:
    # n_sectors snapped to a valid divisor of GCD(slots, poles) (or the P2
    # auto-symmetry wedge), and a single-frame request pinned to one step.
    ns_eff = _snap_n_sectors(n_sectors, geo_ov)
    nsteps = int(n_steps_per_period) if int(n_steps_per_period) > 1 else 1
    return _field_snap_key_fields(
        gamma_deg=_solved_gamma_deg(gamma_deg, op_mode), I_phase_rms=I_phase_rms,
        mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
        outer_air_factor=outer_air_factor, n_sectors=ns_eff,
        stator_fillet_mm=0.0, gap_layers=2.0, coil_temp_c=coil_temp_c,
        comp_mesh=_parse_component_mesh(component_mesh),
        pole_copy=False, iron_template=True, geo_mesh=True,
        structured_gap=True, airgap_macro=False,
        n_steps_per_period=nsteps, n_periods=float(n_periods),
        eddy=True, rotor_eddy=True, demag=False,
        drive="current", element_order=2,
        cfg_fingerprint=_config_physics_fingerprint(with_request_materials=False),
        geo_ov=geo_ov, mat_ov=_get_request_materials_safe(),
        magnet_temp_c=magnet_temp_c,
        rotor_angle0_deg=0.0)


#: The fields that make two loss maps THE SAME PHYSICS: the machine (geometry
#: + materials, with any per-request override), the operating point, the copper
#: temperature the losses were evaluated at, and the cycle discretisation.
#: Everything else in a run's 30-field key — mesh size, sector count, gap
#: layers, the mesh-strategy flags, demag on/off — changes how the SAME map was
#: computed, not what it is: the map is carried on the run's own mesh, and a
#: demag run is the same losses with a more honest magnet.  Keying on those
#: (the first cut of this mechanism did) meant a Thermal solve never matched a
#: Simulation run, because the user runs Simulation with demag and one gap
#: layer while this route solves with neither ("опять расчёт на каждого
#: фрейма", 2026-09-07).
#:
#: ``magnet_temp_c`` (2026-09-08) joins them because it is the one field of the
#: three temperatures a run carries that changes the MAP rather than how it was
#: computed: a hotter magnet has a lower Br and a knee closer to its operating
#: point, so the air-gap field, the iron loss and the magnet eddy loss are all
#: different numbers.  A thermal solve that reused the map solved at the card's
#: own temperature would be feeding its own converged magnet temperature back
#: into losses that never saw it — the exact loop phase 2 is being built to
#: close.  It is ``None`` on every run solved so far and on every request that
#: does not ask for one, so no existing identity moves.
_PHYSICS_ID_FIELDS = ("cfg_fingerprint", "geo_ov", "mat_ov", "gamma_deg",
                      "I_phase_rms", "rpm", "coil_temp_c", "magnet_temp_c",
                      "n_steps_per_period",
                      "n_periods", "drive", "excitation", "winding",
                      "rotor_angle0_deg")


def _freeze(v):
    if isinstance(v, dict):
        return tuple(sorted((str(k), _freeze(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_freeze(x) for x in v)
    return v


def _em_material_names(mat_ov) -> tuple:
    """The EM-relevant part → material pairs of a per-request override, or of
    the shared config when the request carries none.

    A run's key stores the override as the RAW string (``("mat", json)``), and
    ``_mat_ov_for_key`` folds an override that equals the config to ``None`` —
    two spellings of one machine.  Both are read here into one canonical form,
    and the EM-inert parts (liner, enamel, air — ``simulation._EM_INERT_PARTS``)
    are left out: the user swapped the liner to Al2O3 (2026-09-07) and that is a
    thermal change, not a reason to demand a new electromagnetic run.
    """
    import json as _j
    from motor_ai_sim.routes.simulation import _EM_INERT_PARTS
    assign = None
    try:
        if isinstance(mat_ov, (tuple, list)) and len(mat_ov) == 2 and mat_ov[0] == "mat":
            assign = (_j.loads(mat_ov[1]) or {}).get("assignment")
        elif isinstance(mat_ov, dict):
            assign = mat_ov.get("assignment")
    except Exception:  # noqa: BLE001
        assign = None
    if not isinstance(assign, dict):
        try:
            from motor_ai_sim.config import get_material_assignments as _gma
            assign = _gma() or {}
        except Exception:  # noqa: BLE001
            assign = {}
    # Renamed library keys (materials.MATERIAL_ALIASES) are ONE material: a run
    # keyed under the old spelling still describes the machine drawn with the
    # new one.
    from motor_ai_sim.materials import canonical_material_name as _canon
    return tuple(sorted((str(k), _canon(str(v))) for k, v in assign.items()
                        if v and k not in _EM_INERT_PARTS))


def _physics_identity(fields) -> tuple:
    """The physics half of a run key / probe (see ``_PHYSICS_ID_FIELDS``).

    ``mat_ov`` is compared through ``_em_material_names`` — the EM parts only,
    in one canonical spelling — not as the raw override string."""
    out = []
    for k in _PHYSICS_ID_FIELDS:
        v = fields.get(k)
        out.append(_em_material_names(v) if k == "mat_ov" else _freeze(v))
    return tuple(out)


def _run_key_fields(key, entry):
    """The named key fields of a stored run: from its meta when the store wrote
    them, else re-zipped from the key tuple (a pickle from before the names
    were stored)."""
    kf = ((entry.get("meta") or {}).get("key_fields")) if isinstance(entry, dict) else None
    if isinstance(kf, dict) and kf:
        return dict(kf)
    try:
        from motor_ai_sim.routes import simulation as _sim
        names = list(_sim._field_snap_key_fields(
            gamma_deg=0.0, I_phase_rms=0.0, mesh_size_mm=1.0, min_size_mm=0.1,
            outer_air_factor=1.0, n_sectors=1, stator_fillet_mm=0.0, gap_layers=1.0,
            coil_temp_c=0.0, comp_mesh={}, pole_copy=False, iron_template=True,
            geo_mesh=True, structured_gap=True, airgap_macro=False,
            n_steps_per_period=1, n_periods=1.0, eddy=True, rotor_eddy=True,
            demag=False, drive="current", element_order=2, cfg_fingerprint="",
            geo_ov=None, mat_ov=None).keys())
        if isinstance(key, tuple) and len(key) == len(names):
            return dict(zip(names, key))
    except Exception:  # noqa: BLE001
        pass
    return None


def _snapshot_loss_entry(probe):
    """The stored Simulation run this request may reuse — or WHY it may not.

    Returns ``(entry, fields, reason)``: ``entry``/``fields`` are set together or
    not at all.  The reason is carried into ``loss_source.note`` rather than
    logged and forgotten, because "why am I waiting six minutes again" is the
    question this whole mechanism exists to answer.

    A run is accepted when its PHYSICS identity equals this request's
    (``_PHYSICS_ID_FIELDS``) and it was a coupled-eddy run (``eddy`` and
    ``rotor_eddy`` on — without them the magnet / shaft / sleeve losses are
    missing from the map, and a rotor heated by nothing is not the machine).
    Its mesh, sector count, gap layers, mesh flags and demag setting are ITS
    business: the map is replayed on the run's own mesh.  The newest matching
    run wins.  The snapshot must carry the per-element ``loss_dens`` array —
    a run that stored only component totals cannot be spread back over the
    elements without inventing a distribution, so it is not a match either.
    """
    import numpy as _np

    from motor_ai_sim.routes import simulation as _sim

    try:
        store = _sim._transient_field_snap
        if not store:
            # An empty store is not proof nothing was solved: a benign cache
            # flush drops the memory copy while the last run still sits on disk.
            _sim._load_last_transient_field_snapshot()
            store = _sim._transient_field_snap
        items = list(store.items())
    except Exception as exc:  # noqa: BLE001 - a lookup failure is a REASON, not a crash
        return None, None, "the Electromagnetic run store could not be read (%s)" % exc
    want = _physics_identity(probe)
    best = None
    best_fields = None
    best_stamp = ""
    n_same_machine = 0
    for key, entry in items:
        fields = _run_key_fields(key, entry)
        if not fields or str(fields.get("kind", "tfield")) != "tfield":
            continue
        if fields.get("cfg_fingerprint") == probe.get("cfg_fingerprint"):
            n_same_machine += 1
        if not (int(fields.get("eddy") or 0) and int(fields.get("rotor_eddy") or 0)):
            continue
        if _physics_identity(fields) != want:
            continue
        stamp = str(((entry.get("meta") or {}).get("computed_at")) or "")
        if best is None or stamp >= best_stamp:
            best, best_fields, best_stamp = entry, fields, stamp
    if best is None:
        if n_same_machine:
            return None, None, ("the Electromagnetic run(s) of this machine were "
                                "solved at another operating point, coil temperature "
                                "or frame count")
        return None, None, ("no Electromagnetic run of this machine (geometry + "
                            "materials) is stored")
    fld = best.get("field") or {}
    try:
        n_tri = int(_np.asarray(fld.get("T")).shape[1])
        n_ld = int(_np.asarray(fld.get("loss_dens") or []).size)
    except Exception:  # noqa: BLE001
        return None, None, "the matching Simulation run's snapshot is unreadable"
    if n_ld != n_tri or n_tri <= 0:
        return None, None, ("the matching Electromagnetic run carries no per-element "
                            "loss map (component totals only), and spreading component "
                            "watts back over the mesh would need a distribution nobody "
                            "measured")
    return best, best_fields, ""


# ── the thermal route's OWN memory of the maps it had to solve ────────────────
# WHY (user 2026-09-07: "надо просто запоминать карту потерь и не гонять каждый
# раз электромагнитный решатель"): when no Simulation run matches, the map this
# route solves lived only in the field route's in-process cache — gone at the
# next API restart, and keyed on the mesh flags so a Solve with other cooling
# but the same physics could still miss.  This store is keyed on the PHYSICS
# identity alone, kept small (the last few maps, slimmed to what the conduction
# solve reads), and mirrored to a dot-file beside the config so it survives a
# restart.  It is thermal-owned: nothing else reads or writes it (solver
# isolation, same day).
_LOSS_MAPS_CAP = 4
#: Stage 3: per WORKSPACE, and the mirror file is already per workspace.  The
#: store trims itself to `_LOSS_MAPS_CAP` at the write site; the bound below is
#: the same number, so the two can never disagree.
_LOSS_MAPS = _WSP.ws_map("thermal.loss_maps", _LOSS_MAPS_CAP)
_SLOT_LOSS_MAPS_LOADED = "thermal.loss_maps_loaded"


def _loss_maps_loaded() -> bool:
    ov = globals().get("_LOSS_MAPS_LOADED", _UNSET)
    if ov is not _UNSET and _WSP.module_override_applies():
        return bool(ov)
    return bool(_WSP.state().flag(_SLOT_LOSS_MAPS_LOADED, False))


def _set_loss_maps_loaded(value: bool) -> None:
    if "_LOSS_MAPS_LOADED" in globals() and _WSP.module_override_applies():
        globals()["_LOSS_MAPS_LOADED"] = value
    else:
        _WSP.state().set_flag(_SLOT_LOSS_MAPS_LOADED, value)


def __getattr__(name):
    """The two restore flags survive as NAMES, per workspace.

    Six test modules do ``monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True)``
    to keep a solve away from the disk store; ``raising=True`` needs the name to
    resolve, and the accessors above honour a real module attribute once one
    exists, so the patch takes effect and ``undo`` leaves the module behaving
    exactly as it did before this stage.
    """
    if name == "_LAST_LOADED":
        return bool(_WSP.state().flag(_SLOT_LAST_LOADED, False))
    if name == "_LOSS_MAPS_LOADED":
        return bool(_WSP.state().flag(_SLOT_LOSS_MAPS_LOADED, False))
    raise AttributeError(name)
_LOSS_MAP_KEEP_ARRAYS = ("vertices", "triangles", "domain_per_tri",
                         "loss_density_per_tri", "outlines", "extent")


def _symmetrise_rotor_losses_per_pole(loss_dens, verts, tris, tags,
                                      rotor_outer_m: float, n_sectors: int,
                                      poles_per_sector: int):
    """Bring every rotor-side domain's per-pole watts to their mean.

    2026-09-09 — see the call site in ``solve_thermal_field`` for the finding.
    Elements with centroid radius < ``rotor_outer_m`` are binned into
    ``poles_per_sector`` angular pitches of the solved sector (measured from the
    sector's own first angle, about its mean direction, so a sector straddling
    −180° bins correctly); for each domain tag the watts of each pole are
    scaled to the mean over the poles that carry any.  The within-pole shape
    and the total are unchanged.  Returns ``(loss_dens, report)`` where the
    report is ``{tag: {"min": P_min/mean, "max": P_max/mean, "poles": n}}`` —
    empty when nothing was done (fewer than two poles, no rotor-side loss).
    """
    import numpy as _np

    ld = _np.asarray(loss_dens, float)
    report: Dict[str, Any] = {}
    if poles_per_sector < 2 or ld.size == 0 or ld.size != tris.shape[0]:
        return ld, report
    ld = ld.copy()
    p = verts[tris]                                          # (m, 3, 2)
    cen = p.mean(axis=1)
    area = 0.5 * _np.abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
                         - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    rc = _np.hypot(cen[:, 0], cen[:, 1])
    rotor_side = rc < float(rotor_outer_m)
    if not rotor_side.any():
        return ld, report
    th = _np.arctan2(cen[:, 1], cen[:, 0])
    c = _np.exp(1j * th[rotor_side]).sum()
    mean_dir = float(_np.angle(c)) if abs(c) > 1e-9 else 0.0
    rel = _np.degrees(_np.angle(_np.exp(1j * (th - mean_dir))))
    # The pole bins start at the sector's CUT, which the rotor-side VERTICES
    # sit on exactly; the first centroid is a fraction of a degree in, and
    # starting there shifts every bin edge by that fraction.
    vth = _np.arctan2(verts[:, 1], verts[:, 0])
    vrel = _np.degrees(_np.angle(_np.exp(1j * (vth - mean_dir))))
    rel = rel - float(vrel[_np.unique(tris[rotor_side])].min())
    pitch = 360.0 / max(n_sectors, 1) / poles_per_sector
    pole = _np.floor(rel / pitch).astype(int)
    for tag in _np.unique(tags[rotor_side]):
        m_tag = rotor_side & (tags == tag)
        if not (ld[m_tag] > 0).any():
            continue
        P = _np.array([float((ld[m_tag & (pole == k)] * area[m_tag & (pole == k)]).sum())
                       for k in range(poles_per_sector)])
        live = P > 0
        if live.sum() < 2:
            continue
        mean = float(P[live].mean())
        for k in range(poles_per_sector):
            if P[k] > 0:
                ld[m_tag & (pole == k)] *= mean / P[k]
        report[str(int(tag))] = {"min": round(float(P[live].min() / mean), 4),
                                 "max": round(float(P[live].max() / mean), 4),
                                 "poles": int(live.sum())}
    return ld, report


def _loss_maps_path() -> str:
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        base = str(_ws_root())
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    return os.path.abspath(os.path.join(base, ".thermal_loss_maps.pkl"))


def _slim_em(em):
    """The field payload reduced to what ``solve_thermal_field`` reads: the mesh,
    the per-element loss map and every scalar (watts, labels, symmetry).  The
    A / B / J maps are the Simulation tab's pictures, not this route's."""
    out = {}
    for k, v in em.items():
        if k in _LOSS_MAP_KEEP_ARRAYS:
            out[k] = v
        elif isinstance(v, (list, tuple, dict)):
            if len(v) <= 64:
                out[k] = v
        else:
            out[k] = v
    return out


def _loss_maps_load() -> None:
    if _loss_maps_loaded():
        return
    _set_loss_maps_loaded(True)
    try:
        import pickle as pk
        with open(_loss_maps_path(), "rb") as fh:
            d = pk.load(fh)
        if isinstance(d, dict):
            for k, v in list(d.items())[-_LOSS_MAPS_CAP:]:
                _LOSS_MAPS[k] = v
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("thermal: could not read the stored loss maps: %s", exc)


def _loss_maps_persist() -> None:
    import threading

    snapshot = dict(_LOSS_MAPS)

    def _write():
        try:
            import pickle as pk
            p = _loss_maps_path()
            with open(p + ".tmp", "wb") as fh:
                pk.dump(snapshot, fh, protocol=pk.HIGHEST_PROTOCOL)
            os.replace(p + ".tmp", p)
        except Exception as exc:  # noqa: BLE001
            log.warning("thermal: could not persist the loss maps: %s", exc)

    # Stage 3: the path is resolved inside the thread — carry the workspace.
    threading.Thread(target=_WSP.bind(_write), daemon=True).start()


def _loss_maps_get(identity):
    _loss_maps_load()
    e = _LOSS_MAPS.get(identity)
    if e is not None:
        _LOSS_MAPS.move_to_end(identity)
    return e


def _loss_maps_put(identity, em, note: str, run_id: Optional[str] = None) -> None:
    """Remember one map.  ``run_id`` is the ELECTROMAGNETIC run it came from —
    carried separately from the prose so the payload can name it as an id and
    a panel can link to it (entries pickled before this field existed simply
    have no id, and fall back to the timestamp)."""
    _loss_maps_load()
    _LOSS_MAPS[identity] = {"em": _slim_em(em), "note": note, "run_id": run_id,
                            "computed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _LOSS_MAPS.move_to_end(identity)
    while len(_LOSS_MAPS) > _LOSS_MAPS_CAP:
        _LOSS_MAPS.popitem(last=False)
    _loss_maps_persist()


def clear_thermal_loss_maps() -> int:
    """Forget the stored maps (memory AND disk) — for tests, and for anyone who
    wants the Electromagnetic run consulted again regardless (the identity
    carries the geometry and material fingerprints, so a changed machine never
    gets an old map anyway)."""
    n = len(_LOSS_MAPS)
    _LOSS_MAPS.clear()
    try:
        os.remove(_loss_maps_path())
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001
        pass
    return n


#: The machine-readable tag on the ONE refusal this router owns.  A panel keys
#: its "run it on the Electromagnetic tab" button on this, never on the English
#: sentence beside it: the sentence is written for a human and will be reworded,
#: the code is a contract.
NO_EM_RUN_CODE = "no_electromagnetic_run"


def _operating_point_words(*, I_phase_rms, gamma_deg, rpm, coil_temp_c,
                           n_steps_per_period=None) -> str:
    """The operating point spelled the way the Electromagnetic tab spells it.

    The whole value of the refusal below is that the user can READ it and go make
    exactly that run, so every number the physics identity matches on is named:
    the current, the current angle, the speed the LOSSES were solved at, the
    copper temperature and the frame count.

    ``rpm`` is the run's speed (``simulation.rpm`` from the shared config, as
    ``_field_snap_key_fields`` resolves it) — never this request's ``rpm``
    parameter, which sets the air-gap Taylor number.  Naming the wrong one of the
    two would send the user to run the wrong operating point, which is the exact
    failure this message exists to prevent.
    """
    def _n(x) -> str:
        try:
            v = float(x)
        except (TypeError, ValueError):
            return "?"
        return "%d" % round(v) if abs(v - round(v)) < 5e-4 else "%.1f" % v

    _rpm = ("?" if rpm is None
            else format(int(round(float(rpm))), ",d").replace(",", " "))
    out = ("I = %s A, γ = %s°, %s rpm, coil %s °C"
           % (_n(I_phase_rms), _n(gamma_deg), _rpm, _n(coil_temp_c)))
    if n_steps_per_period:
        _st = int(n_steps_per_period)
        out += ", %d step%s/period" % (_st, "" if _st == 1 else "s")
    return out


def _no_electromagnetic_run(*, words: str, why: str = "",
                            single_frame: bool = False) -> HTTPException:
    """The 422 that replaced the hidden six-minute solve.

    User, 2026-09-07: *"нужно как-то разделить тепловые расчёты и
    электромагнитные; если вдруг тепловому расчёту нужно электромагнитное
    моделирование, пусть оно делается во вкладке Simulation"*.  So when no
    Electromagnetic run and no remembered map matches, this router does not
    quietly become an electromagnetic solver for the next six minutes — it says
    which run is missing, in the words of the tab that makes it.

    ``invalid_parameters`` is EMPTY on purpose: nothing the user typed is wrong.
    The machine is fine, the operating point is fine, the run simply has not been
    made yet — and a field-level "your I_phase_rms is invalid" would send an
    engineer editing a number that is exactly right.
    """
    _tail = ("the thermal solve never computes electromagnetic losses itself "
             "(they are that tab's result)")
    if single_frame:
        msg = ("a single frame (n_steps_per_period = 1) has no cycle to average: "
               "the thermal solve needs the cycle-averaged loss map of an "
               "Electromagnetic run of this machine at %s — run it on the "
               "Electromagnetic tab with at least 2 steps per period; %s"
               % (words, _tail))
    else:
        msg = ("no Electromagnetic run of this machine at %s — run it on the "
               "Electromagnetic tab first; %s" % (words, _tail))
    if why:
        msg += ".  What was found instead: %s" % why
    return _bad("n_steps_per_period" if single_frame else "I_phase_rms", None,
                NO_EM_RUN_CODE, msg, error=msg, error_code=NO_EM_RUN_CODE,
                invalid=[])


def _remember_loss_map(identity, em, note: str,
                       run_id: Optional[str] = None) -> None:
    """Keep this map under its PHYSICS identity, so the answer survives the
    user's next Electromagnetic run.

    The run store holds the LAST run per key and the user moves on — solves the
    next current, the next angle — and the moment they do, a Thermal tab that
    only knew how to read the run store would start refusing an operating point
    it answered five minutes ago.  This is the router's own memory of a map it
    was handed: keyed on the physics alone (not on the mesh flags), capped at a
    few entries, mirrored to a dot-file so an API restart does not forget.

    Never a solve, never an approximation — a copy of a map that an
    Electromagnetic run really produced.
    """
    try:
        prev = _loss_maps_get(identity)
        if prev is not None and str(prev.get("note") or "") == note:
            return                    # already remembered — don't rewrite the file
        _loss_maps_put(identity, em, note, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 - remembering never fails an answer
        log.warning("thermal: could not remember the loss map: %s", exc)


def _em_loss_map(*, gamma_deg, I_phase_rms, n_steps_per_period, n_periods,
                 mesh_size_mm, min_size_mm, outer_air_factor, n_sectors,
                 coil_temp_c, component_mesh, geo, geo_ov, phase_cb=None,
                 magnet_temp_c=None, op_mode=None):
    """The cycle-averaged loss map for THIS operating point, and where it came
    from.  THIS FUNCTION NEVER SOLVES.

    Returns ``(em, loss_source)`` where ``em`` is the field-view payload the
    thermal solve reads (vertices / triangles / ``domain_per_tri`` /
    ``loss_density_per_tri`` / the component watts / ``symmetry_mult``) and
    ``loss_source`` is ``{kind, run_id, computed_at, note}`` with ``kind`` one of

      ``simulation_run`` — the Electromagnetic run the user made;
      ``thermal_store``  — the same map, remembered here from an earlier request.

    and NOTHING else.  There used to be a third and a fourth (``field_cache``,
    ``solved``) and they were the same thing wearing two hats: a full multi-frame
    eddy transient started from inside a temperature request.  It is gone (user,
    2026-09-07): the loss map is an ELECTROMAGNETIC result, it is computed on the
    Electromagnetic tab, and when there is none this raises a 422 that says so.

    The run path goes through ``get_fem_field2d(snapshot_only=True)`` rather
    than unpacking the snapshot here: that function already turns a stored
    snapshot into the payload shape (collapsing the per-wire / per-magnet tags
    to the display palette, rebuilding the outlines at the requested geometry),
    and a second copy of that translation in this module would be a second way
    for the two to disagree about what element 4 711 is.  ``snapshot_only``
    also guarantees the call CANNOT start a solve — it answers
    ``{ok: False, no_snapshot: True}`` instead — which is what makes it the only
    door out of this module into the electromagnetic side.

    Raises ``HTTPException(422, error_code="no_electromagnetic_run")`` when
    neither source has the map.
    """
    from motor_ai_sim.routes import simulation as _sim

    # The probe is built FIRST, even on the paths that end in a refusal: it
    # carries the rpm the Electromagnetic run is keyed on, and a refusal that
    # named this request's Taylor-number rpm instead would send the user to solve
    # the wrong point.
    probe = None
    _probe_err = ""
    try:
        probe = _loss_snapshot_probe(
            gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor, n_sectors=n_sectors,
            coil_temp_c=coil_temp_c, component_mesh=component_mesh,
            n_steps_per_period=n_steps_per_period, n_periods=n_periods,
            geo_ov=geo_ov, magnet_temp_c=magnet_temp_c, op_mode=op_mode)
    except Exception as exc:  # noqa: BLE001
        _probe_err = "the run key of this request could not be built (%s)" % exc

    _nsteps = int(n_steps_per_period)
    _words = _operating_point_words(
        I_phase_rms=I_phase_rms, gamma_deg=gamma_deg,
        rpm=(probe or {}).get("rpm"), coil_temp_c=coil_temp_c,
        n_steps_per_period=None if _nsteps <= 1 else _nsteps)

    # A single frame has no cycle to average and an Electromagnetic run's map
    # always is one, so there is nothing that could ever match: refuse by name
    # rather than serve a period-average labelled as an instant.
    if _nsteps <= 1:
        raise _no_electromagnetic_run(words=_words, single_frame=True)
    if probe is None:
        raise _no_electromagnetic_run(words=_words, why=_probe_err)
    _identity = _physics_identity(probe)

    # ── (a) the Electromagnetic tab's own run ───────────────────────────────
    try:
        entry, run_fields, why = _snapshot_loss_entry(probe)
    except Exception as exc:  # noqa: BLE001
        entry, run_fields, why = None, None, "the run lookup failed (%s)" % exc
    if entry is not None:
        run_id = (entry.get("meta") or {}).get("computed_at")
        if callable(phase_cb):
            phase_cb("loss map — Electromagnetic run %s" % (run_id or "(unnamed)"))
        # Replay the run on ITS OWN mesh and flags — the physics matched, the
        # discretisation is the run's, and the map lives on its elements.
        #
        # `latest_run_field=False`, and it is the load-bearing flag.  The field
        # VIEW may honestly show a near miss — the run of the same machine at
        # another current — because it prints the differences beside the picture
        # (`transient_param_diffs`).  A temperature map has no such label: it is
        # a single number per node, and one built from another operating point's
        # losses is simply wrong by however much the two differ.  Measured while
        # writing this: without the flag, a thermal request at 61 A was served
        # the 60 A run's loss map by the relaxed fallback, silently.
        _rf = run_fields or {}
        try:
            _cm = json.dumps(dict(_rf.get("comp_mesh") or ()))
        except Exception:  # noqa: BLE001
            _cm = component_mesh
        _run_kw = dict(
            # The replay looks the snapshot up under the SOLVED angle, exactly
            # as the probe did — a generator run lives under panel γ + 180°.
            gamma_deg=_solved_gamma_deg(gamma_deg, op_mode), I_phase_rms=I_phase_rms,
            n_steps_per_period=n_steps_per_period, n_periods=n_periods,
            eddy=True, rotor_eddy=True, coil_temp_c=coil_temp_c, geo=geo,
            mesh_size_mm=float(_rf.get("mesh_size_mm", mesh_size_mm)),
            min_size_mm=float(_rf.get("min_size_mm", min_size_mm)),
            outer_air_factor=float(_rf.get("outer_air_factor", outer_air_factor)),
            n_sectors=int(_rf.get("n_sectors", n_sectors)),
            stator_fillet_mm=float(_rf.get("stator_fillet_mm", 0.0)),
            gap_layers=float(_rf.get("gap_layers", 2.0)),
            component_mesh=_cm,
            pole_copy=bool(_rf.get("pole_copy", False)),
            iron_template=bool(_rf.get("iron_template", True)),
            geo_mesh=bool(_rf.get("geo_mesh", True)),
            structured_gap=bool(_rf.get("structured_gap", True)),
            airgap_macro=bool(_rf.get("airgap_macro", False)),
            demag=bool(_rf.get("demag", False)),
            rotor_angle_deg=float(_rf.get("rotor_angle0_deg", 0.0)),
            # The magnet temperature is THIS request's, not the run's: the two
            # were just proved equal by the physics identity (`magnet_temp_c` is
            # one of `_PHYSICS_ID_FIELDS`), and the replay's own snapshot probe
            # keys on it — omitting it would look up the card-temperature
            # snapshot of a run stored at 163 °C and miss its own match.
            magnet_temp_c=magnet_temp_c,
            latest_run_field=False)
        em = _sim.get_fem_field2d(**_run_kw, use_transient_snapshot=True,
                                  snapshot_only=True)
        _n_ld = len(em.get("loss_density_per_tri") or []) if em else 0
        if em and em.get("ok") and em.get("from_transient") \
                and _n_ld == int(em.get("n_triangles") or -1):
            log.info("thermal: cycle-averaged loss map taken from the "
                     "Electromagnetic run %s — no EM solve", run_id)
            # Remember it HERE, not only in the run store: the user's next
            # Electromagnetic run at another point replaces that entry, and a
            # temperature the tab answered five minutes ago must not become a
            # refusal because of it.
            _remember_loss_map(
                _identity, em,
                "from the Electromagnetic run %s" % (run_id or "an earlier session"),
                run_id=run_id)
            return em, {
                "kind": "simulation_run",
                "run_id": run_id,
                "computed_at": run_id,
                "note": ("cycle-averaged loss density taken from the "
                         "Electromagnetic run of %s — the same machine, "
                         "operating point, coil temperature and frame "
                         "count; replayed on that run's own mesh "
                         "(%s mm, %s sector(s), demag %s) — no "
                         "electromagnetic solve ran for this temperature "
                         "map" % (run_id or "an earlier session",
                                  _rf.get("mesh_size_mm"), _rf.get("n_sectors"),
                                  "on" if _rf.get("demag") else "off")),
            }
        why = ("the matching Electromagnetic run could not be replayed into a "
               "loss map (%s)" % (em.get("reason") if em else "no payload"))

    # ── (b') this router's OWN remembered map for the same physics ──────────
    _stored = _loss_maps_get(_identity)
    if _stored and isinstance(_stored.get("em"), dict):
        _when = _stored.get("computed_at")
        if callable(phase_cb):
            phase_cb("loss map — remembered")
        log.info("thermal: cycle-averaged loss map taken from the thermal store "
                 "(%s) — no EM solve", _when)
        return dict(_stored["em"]), {
            "kind": "thermal_store",
            "run_id": _stored.get("run_id") or _when,
            "computed_at": _when,
            "note": ("cycle-averaged loss density remembered by the Thermal tab "
                     "on %s (%s), for the same machine, operating point, coil "
                     "temperature and frame count — no electromagnetic solve ran"
                     % (_when, _stored.get("note") or "provenance not recorded")),
        }

    # Neither source has it.  This is where a six-minute solve used to start.
    raise _no_electromagnetic_run(words=_words, why=why)


def _scaled_copper_map(em: Dict[str, Any], *, t_ref_c: float, t_c: float):
    """The same loss map with ONLY the winding moved to a new copper temperature.

    Returns ``(em_scaled, report)``.  Copper is the one loss in the machine that
    depends on the winding temperature, so the coupled loop does not need a new
    electromagnetic solve to move it — but it does need to move it the right
    way, and that is not one multiplication:

      * the DC (resistive) share goes as ρ_Cu(T): hotter copper, more loss at
        the same current;
      * the SOLVED AC share — the proximity and skin loss the coupled eddy solve
        produced — goes as σ_Cu(T) = 1/ρ_Cu(T): hotter copper carries LESS eddy
        current in the same field.  Every conductor in this machine is far
        thinner than the copper skin depth at its electrical frequency, which is
        the regime where P_eddy ∝ σ, so the inverse is the honest law and not a
        first-order excuse for one.

    They partly cancel, and the cancellation is large.  Measured on the 30 mm
    fixture while writing this: scaling the WHOLE copper by ρ(T)/ρ(T₀) made the
    coupled loop 2.7× too sensitive and it settled 1.4 K below the loop that
    re-solved the electromagnetics on every pass.  With the split it lands on
    it.  The effective factor is therefore
    ``(P_dc·r + P_ac/r) / (P_dc + P_ac)``, not ``r``.

    Iron, magnet, shaft and sleeve losses are HELD: they are set by B(t), the
    electrical frequency and their own conductivities, none of which the WINDING
    temperature touches at fixed phase current.  (Their own temperature
    dependence — σ(T) of the magnets — is a different coupling that neither this
    scaling nor the electromagnetic re-solve models, so moving them here would
    invent physics the thing being approximated does not contain.)

    A shallow copy: the mesh arrays are shared, only the loss entries are
    replaced, so a twelve-pass loop does not copy the machine twelve times.
    """
    import numpy as _np

    r = _cu_rho_ratio(t_c, t_ref_c)
    p_cu = float(em.get("P_cu_exact_W", em.get("P_cu_W")) or 0.0)
    p_ac = float(em.get("P_cu_ac_exact_W", em.get("P_cu_ac_solve_W")) or 0.0)
    # A stored AC share larger than the total is a payload we do not understand;
    # clamp rather than produce a negative DC term (which would scale backwards).
    p_ac = min(max(p_ac, 0.0), max(p_cu, 0.0))
    p_dc = max(p_cu - p_ac, 0.0)
    p_cu_new = p_dc * r + (p_ac / r if r > 1e-9 else p_ac)
    eff = (p_cu_new / p_cu) if abs(p_cu) > 1e-12 else r

    out = dict(em)
    tags = _np.asarray(em.get("domain_per_tri") or [], int)
    ld = _np.asarray(em.get("loss_density_per_tri") or [], float)
    if ld.size and tags.size == ld.size:
        ld = ld.copy()
        _coil = (tags == _DOM_COIL_VIS)
        if _coil.any():
            # One factor over the whole winding: the map does not carry the
            # DC/AC split per ELEMENT, and inventing a distribution for it would
            # be exactly the guesswork this module refuses elsewhere.  The
            # thermal solve spreads the copper total uniformly over the coil
            # domain anyway (`q_cu` in `solve_thermal_field`), so the effective
            # factor keeps the picture consistent with the number beside it.
            ld[_coil] *= eff
        out["loss_density_per_tri"] = ld.tolist()
    # Both spellings of every copper total move together — the rounded display
    # ones and the exact ones the thermal solve actually reads (see `Pcu` in
    # `solve_thermal_field`); leaving either behind would make the payload
    # disagree with the map beside it.
    for _p_key, _tot_key in (("P_cu_W", "P_loss_total_W"),
                             ("P_cu_exact_W", "P_loss_total_exact_W")):
        if em.get(_p_key) is None:
            continue
        _p = float(em[_p_key])
        out[_p_key] = _p * eff
        if em.get(_tot_key) is not None:
            out[_tot_key] = float(em[_tot_key]) + _p * (eff - 1.0)
    for _ac_key in ("P_cu_ac_solve_W", "P_cu_ac_exact_W"):
        if em.get(_ac_key) is not None and r > 1e-9:
            out[_ac_key] = float(em[_ac_key]) / r
    return out, {"rho_ratio": float(r), "effective": float(eff),
                 "p_cu_dc_ref_W": float(p_dc), "p_cu_ac_ref_W": float(p_ac)}


# ---------------------------------------------------------------------------
# The thermal domain vocabulary
# ---------------------------------------------------------------------------
# The EM mesh knows four kinds of air and calls them all air: DOM_AIR (0) is the
# slot air AROUND the wire stacks, the liner, the enamel AND the rotor pocket
# air AND the bore; DOM_AIRGAP (3) is the rotor-side half of the gap; DOM_OUTER
# (8) is the far field AND — measured on the 200 mm machine, 2026-09-07 — the
# STATOR-side half of the gap; DOM_BAND (7) is the slip band.  Magnetically that
# is one material.  Thermally it is five different ones, and until this change
# the thermal solve dropped every one of them: the user was looking at a map
# with the slot white around the wires and the air gap white around the rotor,
# and asked for the obvious — *"надо рисовать изоляцию и покрытие провода, а то
# пустое место, и воздух тоже показывать — он же входит в расчёт"*.
#
# So the air is KEPT and NAMED, by geometry (see `_retag_thermal_domains`), in
# a tag range of its own.  61… is chosen to clear everything the EM palette
# allocates: 0-8 the fixed domains, 9/10 the iron-template liner/enamel,
# 11 the sleeve, 44 the S magnet, 100+ per-magnet and 200+ per-coil.
DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER = 0, 3, 7, 8
DOM_TPL_LINER, DOM_TPL_ENAMEL = 9, 10      # simulation/iron_template.py's own
#: The shaft, at module scope because the shaft-ends heat path (2026-09-07) has
#: to name the domain it is attached to from OUTSIDE ``solve_thermal_field``
#: (the tags list of a volume sink, and the tests that pin it).  Same value the
#: function's own local unpack of the collapsed palette carries.
DOM_SHAFT = 6

DOM_SLOT_LINER  = 61      # ground-wall liner between the winding and the tooth
DOM_WIRE_ENAMEL = 62      # the magnet wire's own film, between the conductors
DOM_SLOT_FILL   = 63      # impregnation / air filling the rest of the slot
DOM_GAP_AIR     = 64      # the mechanical clearance the rotor turns in
DOM_POCKET_AIR  = 65      # air inside the rotor (magnet pockets, flux barriers)
DOM_BORE_AIR    = 66      # air inside the bore — DROPPED: it is the coolant side
DOM_AIR_OTHER   = 67      # air the classifier could not place (should be 0)
DOM_OUTER_CUT   = 68      # air in the stator's OUTER cuts / vents — DROPPED: it is
                          # open to the outside, i.e. the coolant side of the
                          # housing boundary condition (user 2026-09-07: "в этих
                          # вырезах не нужно ничего рисовать, там охлаждающая
                          # жидкость или воздух")

#: tag -> display name, for BOTH the solver's own bookkeeping and the payload's
#: ``part_names``.  One table, so a triangle the solve calls "insulation" is the
#: row the Part tree calls "insulation".
THERMAL_EXTRA_DOMAINS = {
    DOM_SLOT_LINER:  "insulation",
    DOM_WIRE_ENAMEL: "wire enamel",
    DOM_SLOT_FILL:   "wire coating",
    DOM_GAP_AIR:     "air gap",
    DOM_POCKET_AIR:  "pocket air",
    DOM_BORE_AIR:    "bore air",
    DOM_AIR_OTHER:   "unclassified air",
    DOM_OUTER_CUT:   "outer cut (coolant)",
}

# What is NOT solid and NOT part of the conduction problem:
#   * DOM_OUTER — the far-field ring outside the housing.  Convection acts at
#     the housing surface, so the mesh boundary has to BE that surface.
#   * DOM_BAND — the sliding band, a numerical artefact of the motion model.
#   * DOM_BORE_AIR — the air inside the bore.  It is the COOLANT side of the
#     bore boundary condition; keeping it would bury the bore surface inside
#     the mesh and there would be nothing left to apply h to.
#   * DOM_AIR_OTHER — air the classifier could not place.  Dropped rather than
#     guessed at, and counted in the payload so a non-zero count is visible.
# DOM_AIR / DOM_AIRGAP are NOT in the list: after `_retag_thermal_domains` no
# element carries them any more, and if the re-tag could not run (no shapely,
# no polygons) they are dropped by the fallback below so the old behaviour is
# what a failure degrades to.
DROP_TAGS = [DOM_OUTER, DOM_BAND, DOM_BORE_AIR, DOM_AIR_OTHER, DOM_OUTER_CUT,
             DOM_AIR, DOM_AIRGAP]
# DOM_SLEEVE (11) is deliberately NOT in that list: the carbon retaining ring is
# a SOLID with a very poor radial conductivity, and dropping it (or leaving it at
# the default air value, which is what happened until 2026-09-07) deletes the
# rotor's series resistance to the gap — the single thing a sleeve does
# thermally.

# The collapsed display palette the EM mesh carries (routes.simulation's
# `dom_names`) plus this module's own air vocabulary, so a client can label a
# triangle without a second lookup table.
PART_NAMES = {0: "air", 1: "stator", 2: "coil", 3: "airgap", 4: "magnet_N",
              5: "rotor", 6: "shaft", 7: "band", 8: "outer_air",
              9: "insulation", 10: "wire enamel", 11: "sleeve",
              44: "magnet_S", **THERMAL_EXTRA_DOMAINS}

#: Everything that is AIR in this vocabulary.  Used by the open-frame channel
#: measurement below: an air-to-air interface is not a duct wall, so the slot
#: channel's wetted perimeter must not count the slot opening where it meets the
#: gap air.
_AIR_TAGS = (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER, DOM_GAP_AIR,
             DOM_POCKET_AIR, DOM_BORE_AIR, DOM_AIR_OTHER, DOM_OUTER_CUT,
             DOM_SLOT_FILL)


def _end_winding_factor(em, geo_ov) -> "tuple[float, str]":
    """``(k_end, where it came from)`` — the end-winding factor the LOSSES of
    this map were billed at.

    NEVER a constant, and never a second estimate of its own when the run
    recorded one.  ``P_cu_exact_W`` already contains ``k_end`` (see
    ``field_ops.copper_loss_W``: P = ρ·J²·V_active·k_end), so the open-frame
    model — which needs the end turns' LENGTH, ``(k_end − 1)·L_stack/2`` per
    side — has to use the SAME number or the copper it puts in the wash is not
    the copper the loss was computed for.  Two sources, in order:

      1. the Electromagnetic run's own payload (``end_winding_factor``, carried
         through the transient snapshot since 2026-09-09).  This is the honest
         one: it is the value the Simulation tab sent, auto or typed;
      2. ``masses.end_winding_factor`` on this request's geometry — the very
         function the solver calls when the tab sends ``0 = auto``.  It is the
         answer for every run stored before (1) existed, and it is right
         whenever the tab was on auto, which is the normal case.

    Returns ``(1.0, "unknown")`` if neither can be had, which the caller reads as
    "no end turns" — a machine with k_end = 1 has none, so the path switches
    itself off and says so rather than inventing a length.
    """
    try:
        k = float((em or {}).get("end_winding_factor") or 0.0)
        if k > 1.0:
            return k, "the Electromagnetic run"
    except (TypeError, ValueError):  # a payload that carries something else
        pass
    try:
        from motor_ai_sim.masses import end_winding_factor as _ewf
        from motor_ai_sim.simulation.geometry_2d import (
            merge_geo_override as _mgo, params_from_config as _pfc)
        from motor_ai_sim.config import get_config as _gc
        _g = _mgo(dict((_gc().get("geometry") or {})), geo_ov)
        k = float(_ewf(_pfc(geo_override=geo_ov), _g))
        if k > 1.0:
            return k, ("the geometry (k_end = (π·(tooth_w + wire column)/2 + "
                       "L)/L, the same estimator the solver uses for auto)")
    except Exception as exc:  # noqa: BLE001 — a missing k_end is not a 500
        log.warning("thermal: the end-winding factor could not be derived (%s)",
                    exc)
    return 1.0, "unknown"


def _domain_wetted_perimeter(verts, tris, tags, mask) -> "tuple[float, float]":
    """``(wetted perimeter [m], cross-section area [m²])`` of a masked domain.

    The perimeter is the length of the masked elements' boundary edges that face
    a SOLID neighbour — copper, enamel, liner, tooth iron.  Three kinds of edge
    are deliberately NOT counted:

      * interior edges (both sides in the mask) — not a wall;
      * edges with no neighbour at all — the mesh's own outer boundary and the
        symmetry-wedge cut.  A cut plane is where the machine continues, not
        where the duct has a wall, and counting it would inflate the perimeter by
        the wedge's two radial cuts every time the answer is scaled by ``sym``;
      * edges facing another AIR domain (``_AIR_TAGS``) — the slot opening onto
        the air gap is where the channel is OPEN, not where it is wetted.

    Measured on the mesh rather than derived from geometry fields because the
    free area of a slot after the wires are in it is not a number anybody types:
    it is what is left over, and the mesh already knows exactly what that is.

    Returns the WEDGE's own numbers; the caller multiplies by the model's
    symmetry to get the machine's, the same way every other flow here is scaled.
    """
    import numpy as _np

    tris = _np.asarray(tris, int)
    verts = _np.asarray(verts, float)
    tags = _np.asarray(tags, int)
    mask = _np.asarray(mask, bool)
    m = int(tris.shape[0])
    if m == 0 or not mask.any():
        return 0.0, 0.0

    # Cross-section: the plain triangle areas of the masked elements.
    v = verts[tris[mask]]                                   # (k, 3, 2)
    a_cs = float(_np.abs(
        (v[:, 1, 0] - v[:, 0, 0]) * (v[:, 2, 1] - v[:, 0, 1])
        - (v[:, 2, 0] - v[:, 0, 0]) * (v[:, 1, 1] - v[:, 0, 1])).sum()) * 0.5

    ends = _np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    owner = _np.tile(_np.arange(m), 3)
    key = _np.sort(ends, axis=1)
    _, inv = _np.unique(key, axis=0, return_inverse=True)
    inv = _np.asarray(inv).ravel()
    n_edges = int(inv.max()) + 1
    # Per unique edge: how many half-edges it has (1 = mesh boundary, 2 =
    # interior), how many of them are in the mask, and — for the interior ones —
    # the OTHER element, recovered as (sum of owners) − (this owner).
    cnt = _np.bincount(inv, minlength=n_edges)
    n_in = _np.bincount(inv, weights=mask[owner].astype(float), minlength=n_edges)
    sum_own = _np.bincount(inv, weights=owner.astype(float), minlength=n_edges)
    other = (sum_own[inv] - owner).astype(int)
    sel = mask[owner] & (n_in[inv] == 1.0) & (cnt[inv] == 2)
    if sel.any():
        sel = sel.copy()
        sel[sel] = ~_np.isin(tags[other[sel]], _np.asarray(_AIR_TAGS, int))
    if not sel.any():
        return 0.0, a_cs
    a = ends[sel, 0]
    b = ends[sel, 1]
    per = float(_np.hypot(verts[b, 0] - verts[a, 0],
                          verts[b, 1] - verts[a, 1]).sum())
    return per, a_cs


# One `get_2d_polygons()` per geometry, not one per solve: the liner and the
# enamel are hundreds of small rings and building them is tens of milliseconds
# that the coupled loop would otherwise pay twelve times over.  Keyed on the
# geometry fingerprint, which is what `clear_thermal_caches` already invalidates
# everything else on.
_POLY_CACHE_MAX = 4
_POLY_CACHE = _WSP.ws_map("thermal.poly_cache", _POLY_CACHE_MAX,
                          lru_on_read=True)


def _thermal_polys(geo: Optional[str], fp: Optional[str]):
    """``get_2d_polygons()`` for this request, memoised on the fingerprint."""
    key = str(fp or "")
    if key and key != "nofp":
        hit = _POLY_CACHE.get(key)
        if hit is not None:
            return hit
    polys, _motor, _ov = _live_polys(geo)
    if key and key != "nofp":
        _cache_put(_POLY_CACHE, key, polys, _POLY_CACHE_MAX)
    return polys


def _mesh_radii(geo_ov) -> Dict[str, float]:
    """The four radii the air classifier separates its annuli by, in METRES.

    Exactly the arithmetic ``solve_thermal_field`` does inline (housing = stator
    OD/2, bore = housing − yoke − slot, rotor iron OD = bore − air_gap, true
    rotor OD = that + the sleeve).  Pulled out so the ``/mesh`` preview cannot
    classify the same cross-section differently from the solve — a preview whose
    liner is somewhere else than the solved one is worse than no preview.
    ``merge_geo_override``, not a dict update: ``slot_height`` and friends are
    primaries but the caller's override carries only what it changed.
    """
    from motor_ai_sim.config import get_config
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override

    g = merge_geo_override(dict((get_config().get("geometry", {})) or {}), geo_ov)
    r_house = float(g.get("stator_diameter", 200.0)) / 2.0 * 1e-3
    stator_inner = (r_house - float(g.get("core_thickness", 5.0)) * 1e-3
                    - float(g.get("slot_height", 18.0)) * 1e-3)
    rotor_iron = stator_inner - float(g.get("air_gap", 0.6)) * 1e-3
    sleeve_t = max(float(g.get("sleeve_thickness", 0.0) or 0.0), 0.0) * 1e-3
    return {"r_housing_m": r_house, "stator_inner_m": stator_inner,
            "rotor_iron_outer_m": rotor_iron,
            "rotor_outer_m": rotor_iron + sleeve_t}


def _retag_thermal_domains(verts, tris, tags, polys, *, r_housing_m,
                           stator_inner_m, rotor_outer_m, r_inner_solid_m):
    """Give every AIR element the name of the thing it actually is.

    Returns ``(tags, report)``.  ``tags`` is a copy with every element that came
    in as DOM_AIR / DOM_AIRGAP / DOM_BAND, and every DOM_OUTER element that lies
    INSIDE the stator bore, replaced by one of the ``THERMAL_EXTRA_DOMAINS``
    tags.  ``report`` carries the per-tag element counts and the note printed in
    the payload.

    WHY IT IS DONE BY GEOMETRY AND NOT BY TAG.  The mesh's own air tags do not
    separate these five materials — measured on the live 200 mm machine
    (2026-09-07, mesh 3.0/0.3 mm, half wedge):

        DOM_AIR   4121 elements: 140 in the bore, 255 in the rotor pockets,
                  1356 inside a slot-liner ring, 1254 inside a wire-enamel ring,
                  1116 elsewhere in the slot;
        DOM_AIRGAP 2880 elements — ONLY the rotor-side half of the clearance
                  (63.1 → 63.4 mm), i.e. up to the slip radius;
        DOM_OUTER 4020 elements — 2880 of which are the STATOR-side half of the
                  same clearance (63.4 → 63.7 mm) and only 1140 the far field.

    That last line is the one that would have been a silent wrong answer: a
    classifier that trusted DOM_OUTER to mean "outside the machine" would have
    thrown away half the air gap and left the rotor bridged across a gap it was
    already conducting through.

    The two INSULATIONS are found by point-in-polygon against
    ``get_2d_polygons()``'s own ``slot_insulation`` / ``wire_insulation`` rings
    — they are 0.05-0.25 mm features that no radius can separate from the slot
    air beside them, and they are the polygons the 3-D tree, the mass table and
    the cost model already use, so the thermal map cannot disagree with them
    about where the liner is.  Everything else is separated by RADIUS, which for
    concentric annuli is exact and costs nothing:

        r < r_inner_solid   → bore air (dropped: the bore is a cooled SURFACE)
        r < rotor_outer     → pocket air
        r < stator_inner    → gap air        (+ every DOM_AIRGAP element)
        r <= r_housing      → wire coating
        otherwise           → unclassified   (counted, dropped)

    Air OPEN TO THE OUTSIDE is not part of the machine.  A stator with cuts or
    vents in its outer surface (the Ø200's `cut_width` grooves) has air in them
    that the radius rule above would call wire coating — but that air is the
    coolant flowing past the housing, and meshing it buries the cut walls inside
    the conduction problem instead of putting the convection film on them
    (user 2026-09-07: "в этих вырезах не нужно ничего рисовать, там находится
    охлаждающая жидкость или воздух, нам важны только граничные условия на
    внешнем контуре статора").  So the air is walked by CONNECTIVITY: every
    air element that shares an edge-path with the far field (DOM_OUTER beyond the
    housing radius) without crossing a solid is `outer cut (coolant)` — counted,
    dropped, and its walls become part of the outer surface the film acts on.
    Slot air cannot reach the far field (iron and liner around it, the bore in
    front), so the walk never takes a slot; the rotor's pocket air and the gap
    are closed too.
    """
    import numpy as _np

    tags = _np.asarray(tags, int).copy()
    air = _np.isin(tags, (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_TPL_LINER,
                          DOM_TPL_ENAMEL))
    # DOM_OUTER counts as air only INSIDE the bore — see the docstring.
    cx = verts[tris, 0].mean(axis=1)
    cy = verts[tris, 1].mean(axis=1)
    rc = _np.hypot(cx, cy)
    air |= (tags == DOM_OUTER) & (rc < float(stator_inner_m))
    if not air.any():
        return tags, {"counts": {}, "n_air": 0,
                      "note": "this mesh carries no air elements at all"}

    idx = _np.where(air)[0]
    r = rc[idx]
    new = _np.full(idx.size, DOM_AIR_OTHER, int)
    # Radial classification first, so the polygon tests below only have to
    # OVERRIDE inside the slot — a liner ring is by construction in the slot.
    new[r <= float(r_housing_m)] = DOM_SLOT_FILL
    new[r < float(stator_inner_m)] = DOM_GAP_AIR
    new[r < float(rotor_outer_m)] = DOM_POCKET_AIR
    new[r < float(r_inner_solid_m)] = DOM_BORE_AIR
    # An element that was tagged DOM_AIRGAP is gap air whatever its radius says:
    # the mesher put it in the clearance and the clearance is what it is.
    new[tags[idx] == DOM_AIRGAP] = DOM_GAP_AIR

    n_liner = n_enamel = 0
    poly_ok = True
    poly_why = ""
    try:
        from shapely import contains_xy
        from shapely.ops import unary_union

        mmx = cx[idx] * 1e3          # the polygons are in MILLIMETRES
        mmy = cy[idx] * 1e3
        in_slot = new == DOM_SLOT_FILL
        for key, tag in (("slot_insulation", DOM_SLOT_LINER),
                         ("wire_insulation", DOM_WIRE_ENAMEL)):
            rings = polys.get(key) or []
            if not rings:
                continue
            geom = unary_union(list(rings))
            if geom.is_empty:
                continue
            hit = _np.zeros(idx.size, bool)
            hit[in_slot] = contains_xy(geom, mmx[in_slot], mmy[in_slot])
            new[hit] = tag
            if tag == DOM_SLOT_LINER:
                n_liner = int(hit.sum())
            else:
                n_enamel = int(hit.sum())
    except Exception as exc:  # noqa: BLE001
        # A missing shapely is not a reason to answer with a wrong machine, but
        # it is also not a reason to refuse one: the liner and the enamel stay
        # inside the wire coating, which is what they were before this change, and
        # the payload says so instead of pretending they were drawn.
        poly_ok = False
        poly_why = f"{type(exc).__name__}: {exc}"
        log.warning("thermal: insulation polygons unavailable (%s)", poly_why)

    # ── air open to the outside = coolant, not a domain (see the docstring) ──
    n_cut = 0
    try:
        outer_far = (tags == DOM_OUTER) & (rc > float(r_housing_m) * 1.0005)
        cand = _np.zeros(tags.size, bool)
        cand[idx] = new == DOM_SLOT_FILL          # only the air the radius rule
        walk = cand | outer_far                   # could not tell from slot air
        if cand.any() and outer_far.any():
            wi = _np.where(walk)[0]
            t_w = tris[wi]
            e = _np.concatenate([t_w[:, [0, 1]], t_w[:, [1, 2]], t_w[:, [2, 0]]])
            e.sort(axis=1)
            owner = _np.concatenate([wi, wi, wi])
            order = _np.lexsort((e[:, 1], e[:, 0]))
            e, owner = e[order], owner[order]
            same = (e[1:] == e[:-1]).all(axis=1)   # an edge shared by two walked elements
            a_ = owner[:-1][same]; b_ = owner[1:][same]
            from scipy.sparse import coo_matrix as _coo
            from scipy.sparse.csgraph import connected_components as _cc
            n = tags.size
            g = _coo((_np.ones(a_.size), (a_, b_)), shape=(n, n))
            _, lab = _cc(g + g.T, directed=False)
            outside = _np.zeros(n, bool)
            outside[_np.isin(lab, _np.unique(lab[outer_far]))] = True
            cut = cand & outside
            n_cut = int(cut.sum())
            if n_cut:
                pos = {int(k): i for i, k in enumerate(idx)}
                for k in _np.where(cut)[0]:
                    new[pos[int(k)]] = DOM_OUTER_CUT
    except Exception as exc:  # noqa: BLE001 - the walk is a refinement, never a refusal
        log.warning("thermal: outer-cut walk failed (%s) — cut air kept as wire coating", exc)

    # The iron-template mesh path tags the liner and the enamel itself (9 / 10).
    # Where it did, that is a direct reading and it wins over the polygon test.
    tpl_l = idx[tags[idx] == DOM_TPL_LINER]
    tpl_e = idx[tags[idx] == DOM_TPL_ENAMEL]
    tags[idx] = new
    if tpl_l.size:
        tags[tpl_l] = DOM_SLOT_LINER; n_liner = max(n_liner, int(tpl_l.size))
    if tpl_e.size:
        tags[tpl_e] = DOM_WIRE_ENAMEL; n_enamel = max(n_enamel, int(tpl_e.size))

    counts = {THERMAL_EXTRA_DOMAINS[t]: int((tags == t).sum())
              for t in sorted(THERMAL_EXTRA_DOMAINS)}
    report = {
        "counts": counts,
        "n_air": int(idx.size),
        "n_liner": n_liner,
        "n_enamel": n_enamel,
        "n_outer_cut": n_cut,
        "polygons": bool(poly_ok),
        "note": ("the air is kept and named by geometry: the liner and the "
                 "enamel by point-in-polygon against the CAD rings, the gap, "
                 "pocket, bore and slot air by radius.  Only the far field, "
                 "the slip band and the bore air are dropped."
                 if poly_ok else
                 "the insulation polygons could not be built (%s) — the liner "
                 "and the enamel are inside 'wire coating' in this answer"
                 % poly_why),
    }
    if counts.get("unclassified air"):
        report["note"] += (" %d air element(s) could not be placed and were "
                           "dropped." % counts["unclassified air"])
    if n_cut:
        report["note"] += (" %d air element(s) in the stator's outer cuts are open "
                           "to the outside: they are the coolant, dropped, and the "
                           "cut walls carry the housing film." % n_cut)
    return tags, report


# ---------------------------------------------------------------------------
# The MECHANICAL losses, as heat this cross-section can carry
# ---------------------------------------------------------------------------

def _resolve_mech_losses(geo_ov: Optional[dict], *, rpm: float,
                         bearing_temp_c: Optional[float] = None
                         ) -> Dict[str, Any]:
    """What the bearings and the air cost this machine at this speed, in watts.

    ONE implementation for the whole app (``motor_ai_sim.mech_losses``): the same
    function answers ``GET /api/bearings/losses``, fills the Electromagnetic
    run's summary and feeds this solve, so a pair that costs 63 W on one tab
    costs 63 W on all of them.  Nothing here solves and nothing here writes.

    THE MACHINE HAS TO BE THIS MACHINE.  The bearings live in the ACTIVE
    configuration's die file, and a request carrying a geometry override that is
    not the machine on screen is a CANDIDATE — attributing the active machine's
    bearing pair to it would heat somebody else's rotor with this one's friction.
    Same resolved-fingerprint test ``routes.simulation`` uses for the field
    snapshot and the persisted last run.

    Returns a plain dict, always — a machine with no bearings still HAS windage
    (it is a property of the rotor, not of the shaft line), and a machine with
    neither gets ``has_any: False`` and no injection at all.  Never raises: a
    bearing card that cannot be read must not take a temperature map down.
    """
    out: Dict[str, Any] = {
        "has_any": False, "has_bearings": False,
        "P_bearings_W": 0.0, "P_windage_gap_W": 0.0, "P_windage_faces_W": 0.0,
        "per_end_W": {}, "mech": None, "windage": None,
        "bearing_temp_c": None, "bearing_temp_source": None, "note": "",
    }
    try:
        if not (rpm and float(rpm) > 0.0):
            out["note"] = "at rest: no friction and no windage"
            return out
        if geo_ov and _live_fingerprint(geo_ov) != _live_fingerprint(None):
            out["note"] = ("this request carries a candidate geometry, not the "
                           "machine on screen — its bearings are unknown, so no "
                           "mechanical heat is attributed to it")
            return out
        from motor_ai_sim import bearings as brg
        from motor_ai_sim import mech_losses as ml

        geo = ml.live_geometry(geo_ov)
        mech = ml.machine_mech_losses(
            rpm=float(rpm), geo_override=geo_ov, geometry=geo,
            geometry_fingerprint=_live_fingerprint(geo_ov),
            temp_c=bearing_temp_c)
        if mech:
            out["mech"] = mech
            out["has_bearings"] = True
            out["bearing_temp_c"] = mech.get("bearing_temp_c")
            out["bearing_temp_source"] = mech.get("bearing_temp_source")
            out["P_bearings_W"] = float(mech.get("P_bearings_W") or 0.0)
            out["per_end_W"] = {
                str(b.get("end")): float(b.get("P_W") or 0.0)
                for b in (mech.get("bearings") or [])}
            w = mech.get("windage")
        else:
            # Windage needs no bearings.  Reported and deposited anyway: a
            # machine whose shaft line nobody has chosen still shears the air in
            # its gap, and that is heat the map should carry.
            t_w = (float(bearing_temp_c) if bearing_temp_c is not None else 40.0)
            w = brg.windage_from_geometry(geo, float(rpm), temp_c=t_w)
            out["note"] = ("no bearings assigned to this machine — the friction "
                           "is UNKNOWN, deliberately not zero; the windage below "
                           "needs no bearings and is modelled")
        if w:
            out["windage"] = w
            out["P_windage_gap_W"] = float(w.get("P_gap_W") or 0.0)
            out["P_windage_faces_W"] = float(w.get("P_faces_W") or 0.0)
        out["has_any"] = bool(out["P_bearings_W"] > 0.0
                              or out["P_windage_gap_W"] > 0.0
                              or out["P_windage_faces_W"] > 0.0)
        return out
    except Exception as exc:  # noqa: BLE001 — never sink a solve over a catalogue
        log.warning("thermal: the mechanical losses could not be resolved (%s)",
                    exc)
        out["note"] = "the mechanical losses could not be resolved: %s" % exc
        return out


# ---------------------------------------------------------------------------
# The solve — shared by the route and by modules.solvers
# ---------------------------------------------------------------------------

def solve_thermal_field(
    ambient_temp:       float = 25.0,
    h_conv:             float = 50.0,
    slot_k:             float = 0.0,
    gap_k:              float = 0.0,
    rpm:                float = 0.0,
    gamma_deg:          float = 0.0,
    I_phase_rms:        float = 120.0,
    n_steps_per_period: int   = 12,
    n_periods:          float = 2.0,
    mesh_size_mm:       float = 3.0,
    min_size_mm:        float = 0.3,
    outer_air_factor:   float = 1.3,
    n_sectors:          int   = 4,
    coil_temp_c:        float = 120.0,
    component_mesh:     str   = "",
    geo:                Optional[str] = None,
    cooling_mode:       str   = "manual",
    air_speed_mps:      float = 0.0,
    fluid:              str   = "water",
    fluid_temp_in_c:    float = 25.0,
    fluid_temp_out_c:   float = 0.0,
    flow_lpm:           float = 0.0,
    bore_mode:          str   = "none",
    bore_air_speed_mps: float = 0.0,
    bore_fluid:         str   = "water",
    bore_fluid_temp_in_c: float = 25.0,
    bore_flow_lpm:      float = 0.0,
    shaft_ext_length_mm: float = 0.0,
    shaft_ext_diameter_mm: float = 0.0,
    shaft_ext_sides:    int   = 2,
    # How the machine is BUILT — "housed" (today's model, bit-identical) or
    # "open" (no housing: the end turns and the axial slot channels are in the
    # airflow).  See THE OPEN FRAME in the docstring.
    frame:              str   = "housed",
    open_air_speed_mps: float = 0.0,
    # ── THE ROBOT JOINT (2026-09-14) — see THE ROBOTICS MODE in the docstring ──
    # `emissivity` is read by cooling_mode="robotics" (housing, bore and end
    # faces share it); the MOUNT is read in every mode, because a machine is
    # bolted to something whatever is blowing on it.
    emissivity:         float = 0.9,
    mount_g_w_per_k:    float = 0.0,
    mount_temp_c:       Optional[float] = None,
    end_faces:          str   = "still",
    end_face_sides:     int   = 2,
    magnet_temp_c:      Optional[float] = None,
    bearing_temp_c:     Optional[float] = None,
    # "motor" | "generator" | None (= the shared config's simulation.mode):
    # selects the angle the Electromagnetic run is looked up under — see
    # _solved_gamma_deg.  Never changes the thermal physics itself.
    op_mode:            Optional[str] = None,
    progress=None,
    _em_map:            Optional[Dict[str, Any]] = None,
    _em_loss_source:    Optional[Dict[str, Any]] = None,
    _em_capture:        Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Steady-state 2-D thermal map — the function, not the route.

    Obtains the cycle-averaged loss field from the ELECTROMAGNETIC run that
    solved this operating point, or from this router's memory of one (see
    ``_em_loss_map``; the answer says which under ``loss_source``, and a 422 says
    so when neither has it — this function never starts an electromagnetic
    solve), then solves
    −∇·(K∇T)=q on the same mesh with a Robin condition on every cooled surface:
    the housing and, since 2026-09-07, the rotor bore.  Returns the temperature
    field, heat flux, per-component T_max and the machine's HEAT BUDGET — how
    many watts left through the housing, how many through the bore, and how many
    crossed the air gap on the way.

    It is a plain function so that THREE callers share one implementation and can
    never drift apart: ``GET /api/thermal/field``, the coupled EM↔thermal fixed
    point below, and ``modules.solvers.ThermalSolver`` (capability
    ``solver.thermal``).  The route is a thin Query-annotated wrapper; everything
    that decides a number is here.  ``fluid_temp_out_c`` and ``gap_k`` are still
    accepted so the old capability payloads keep working, and both are IGNORED —
    the outlet temperature is now a result and the gap conductivity is derived.

    ``magnet_temp_c`` (2026-09-08, phase 2) SELECTS the Electromagnetic run the
    same way the operating point does — it does not correct anything here.  A
    magnet at 163 °C has a lower Br and a knee nearer its working point, so the
    air-gap field, the iron loss and the magnet eddy loss are all different
    numbers; ``_PHYSICS_ID_FIELDS`` already carries the field, and this is the
    parameter that lets a caller ASK for the run solved at that temperature.
    ``None`` — every caller before the coupled orchestrator — is the card as the
    library quotes it, i.e. exactly the run this route has always looked for.

    ``bearing_temp_c`` (2026-09-08) is the temperature the MECHANICAL losses are
    evaluated at, and it is an input here for one reason: the coupled loop reads
    the shaft off the previous pass's map and drives the next pass with it, so
    the friction heat this solve carries is the friction of a bearing at the
    temperature this machine actually reaches.  ``None`` = the machine's own
    ``bearings.temp_c`` (or its last thermal map when the assignment says
    ``temp_source: thermal``) — see ``mech_losses.resolve_bearing_temp``.

    THE MECHANICAL LOSSES ARE A HEAT SOURCE (user, 2026-09-08: *"все потери
    должны передаваться"*).  Two of them, and they enter in the two different
    places they are actually made:

      * BEARING FRICTION goes in at the SHAFT, and only when the shaft-ends heat
        path is on.  The bearings are not in this cross-section — they sit
        outside the housing on the exposed stubs — so the shaft-ends conductance
        is the only door the 2-D model has, and with the shaft closed the watts
        are REPORTED in the budget as not modelled rather than silently dropped.
        All of it is put on the shaft, which is an UPPER BOUND and says so: a
        real bearing sends part of its heat out through the outer ring and the
        end cap, a path this cross-section does not carry either.
      * WINDAGE's GAP-SHEAR half is a volumetric source in the air-gap domain —
        that is literally where the shearing happens, and the gap air has been a
        meshed domain since 2026-09-07, so it needs no lumping.  The END-FACE
        half is not in this cross-section (it is made on the rotor's end faces,
        out along the axis) and is reported as not modelled.

    Both are ANALYTIC (``motor_ai_sim.bearings``) and both are attributed in
    ``cooling.mech_losses`` and in the heat budget, which still closes: the
    injected watts are part of ``losses_W`` and leave through the same surfaces
    as everything else.

    THE OPEN FRAME (``frame='open'``, 2026-09-09) — the 40 mm "CIANO14 40 new"
    has no housing at all: the stator tooth blocks with their coils are held
    between two end plates by standoff pins, and the coil END WINDINGS and the
    axial CHANNELS between neighbouring coils sit directly in the propeller wash
    (the user quotes 10-12 m/s).  ``housed`` — the default, and every answer this
    router gave before today — keeps the 2026-09-07 model exactly: the end turns
    and the slot air are inside a closed box, whatever they hand to that air
    comes straight back through the housing, and the whole copper loss (which
    ALREADY includes the end-winding factor, ``P_cu_exact_W``) is deposited in
    the in-slot copper.  ``open`` adds two lumped conductances to ambient, both
    as ``volume_sinks`` because neither surface exists in this cross-section:

      * ``end_windings`` on the WINDING domain — the end-turn bundles in cross
        flow (``cooling_models.end_windings_path``).  Their length per side is
        ``(k_end − 1)·L_stack/2`` with the SAME ``k_end`` the electromagnetic run
        was billed at, read off the run's own payload and falling back to the
        estimator the solver itself uses for ``end_winding_factor = 0``
        (``masses.end_winding_factor``).  On this machine k_end is 1.759 (L12) /
        1.456 (L20), i.e. the end turns are 76 % / 46 % of the copper LENGTH —
        leaving them uncooled is not conservative, it is most of the winding
        missing from the model.  IT IS LUMPED, and says so: the end turns are
        not a node of their own, so their loss stays where it has always been
        (in the in-slot copper, which is what ``P_cu_exact_W`` already bills)
        and the conductance acts between that copper's mean temperature and
        ambient.  A real end turn sits a few kelvin ABOVE the slot copper it
        feeds, so this reads the end-turn film slightly optimistically and the
        slot copper slightly pessimistically — a 2-D cross-section has no third
        node to put between them;
      * ``slot_channels`` on the slot-air domain — the duct between two
        neighbouring coils, its wetted perimeter and free cross-section MEASURED
        on the mesh (``_domain_wetted_perimeter``) and a pipe-flow film on the
        resulting hydraulic diameter (``cooling_models.slot_channels_path``).

    The outer film is unchanged and still applies: on an open machine that
    surface is the backs of the tooth blocks, in the same air.  Both new paths
    are reported in ``cooling.end_windings`` / ``cooling.slot_channels`` beside
    ``shaft_ends``, both get a line in the heat budget, and the budget still
    closes on them.

    THE ROBOTICS MODE (``cooling_mode='robotics'``, 2026-09-14) — a robot joint
    has no fan, no jacket and no slipstream: it is bolted to an arm and it sits
    in a room.  The nearest thing before today was ``air`` at v = 0, i.e. the
    flat 7 W/m²·K natural-convection floor, one number for every machine, every
    ΔT and every finish.  It is ONE mode and not four fields (user decision,
    2026-09-14), because a half-configured still-air machine looks exactly like a
    converged answer, and choosing it turns on all four of these at once:

      * the HOUSING FILM — ``cooling_models.outer_still``: Churchill–Chu natural
        convection PLUS linearised radiation at ``emissivity`` (default 0.9).  On
        the Ø85 joint at ΔT 60 K those are h_conv ≈ 6 and h_rad ≈ 8.3, so more
        than half of what leaves the housing leaves it as light and the finish is
        a design input.  THE ROBIN COEFFICIENT IS ``h_total``; the reported
        ``h_conv`` is the convective half, so ``emissivity = 0`` removes exactly
        the radiation and nothing else;
      * the BORE, OPEN (user decision: the bore is open, not sealed) —
        ``bore_mode='still'``, ``cooling_models.bore_still``: the same pair down
        an unventilated hole, with Nu floored at conduction across it.
        ``bore_mode='none'`` is still legal and means a bore packed with cable;
      * the AXIAL END FACES (``end_faces='still'``) — on this machine the coils
        stand PROUD of the core on both sides and the core's own end faces are
        largely uncovered, which breaks the 2026-09-07 "everything but the shaft
        turns inside the housing" ruling for this build.  Four lumped
        conductances, ``G = h_total(ΔT)·A·n_faces`` from
        ``cooling_models.end_face_still``, on the winding / stator / rotor /
        magnet tags: the end-turn area is ``cooling_models.end_winding_area`` on
        this run's own ``k_end`` (the SAME derivation the open frame's forced-air
        path uses, so the two cannot disagree about how much copper is out
        there), and the core / magnet faces are each part's own SECTION AREA
        measured on this mesh × ``end_face_sides``;
      * the MOUNT (``mount_g_w_per_k``, at ``mount_temp_c`` — blank = ambient) —
        the bolted flange, which on this machine is not one of five paths but THE
        path: the housing hands the room ~3 W of the 64 W the joint makes at its
        rated point and the bolts take the rest.  A contact conductance cannot be
        predicted from a cross-section (it depends on the bolt pattern, the
        contact area and the interface), so it is an INPUT with a loud note, and
        it enters as a ``volume_sink`` on the stator elements — a 2-D section has
        no face out along the axis for a flange to sit on.  It is read in EVERY
        cooling mode: a jacketed machine is bolted to something too.

    EVERY ONE OF THOSE COEFFICIENTS DEPENDS ON THE WALL TEMPERATURE, which no
    film in this router did before — h ∝ ΔT^(1/4) on the convective half and the
    radiation re-linearises about the wall — so the conduction solve is iterated
    around them exactly as it is around a coolant outlet: seed the wall, solve,
    read each surface's and each sink's OWN area-mean temperature back
    (``thermal_solver_2d``'s ``t_mean_c``), re-evaluate, repeat until every wall
    moves by less than half a kelvin.  The dependence is weak (h_total goes as
    roughly ΔT^0.15 once radiation is in it), so this lands in two or three
    passes; the seed is ``T_∞ + P/(7·A_housing)``, the flat floor the mode
    replaces, which is deliberately the answer this mode would have given before.

    ``progress`` (2026-09-07) is the shared live-progress callback (see
    ``motor_ai_sim.progress``), reporting THIS pass's own budget: TWO steps — the
    conduction solve and the post-processing — with the loss-map lookup and the
    material tables named as phases but not counted (they are milliseconds
    between two things that are not).  There are no EM frames in the bar any
    more, because there is no EM solve in this function any more.  The
    coolant-outlet iteration re-uses the SAME conduction step and only renames
    the phase ("conduction pass 2/4 — outlet 47.3 °C"), so a run that needed
    four passes does not make the bar longer than the one that needed one.
    Local, not global, on purpose: ``solve_coupled`` runs up to twelve of these
    and offsets the numbers itself, so this function never has to know it is in a
    loop.

    THE THREE UNDERSCORE PARAMETERS (2026-09-07) are the coupled loop's, and are
    not part of the route's contract:

      * ``_em_map`` — an already-obtained loss map to use INSTEAD of looking one
        up.  ``solve_coupled`` passes the map it fetched once, with
        only the copper rescaled to the pass's winding temperature.  A request
        carrying one neither reads nor writes ``_FIELD_CACHE``: the cache key
        cannot see the difference between a map that was solved at this coil
        temperature and one that was scaled to it, and storing the second under
        the first's key would quietly hand a later ``/field`` an approximation
        it never asked for;
      * ``_em_loss_source`` — that map's provenance, reported verbatim;
      * ``_em_capture`` — a dict this call fills with ``{"em", "loss_source"}``
        so the caller can reuse the map it just paid for.  An out-parameter
        rather than a payload field because the map is the whole mesh and has no
        business being serialised into a JSON response.
    """
    import numpy as _np

    from motor_ai_sim.routes.simulation import _parse_geo_override

    t0 = time.time()
    _geo_ov = _parse_geo_override(geo)
    cooling_mode, bore_mode = _validate_field_params(
        cooling_mode=cooling_mode, ambient_temp=ambient_temp, h_conv=h_conv,
        air_speed_mps=air_speed_mps, fluid=fluid,
        fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
        bore_mode=bore_mode, bore_air_speed_mps=bore_air_speed_mps,
        bore_fluid=bore_fluid, bore_fluid_temp_in_c=bore_fluid_temp_in_c,
        bore_flow_lpm=bore_flow_lpm,
        shaft_ext_length_mm=shaft_ext_length_mm,
        shaft_ext_diameter_mm=shaft_ext_diameter_mm,
        shaft_ext_sides=shaft_ext_sides,
        frame=frame, open_air_speed_mps=open_air_speed_mps,
        emissivity=emissivity, mount_g_w_per_k=mount_g_w_per_k,
        mount_temp_c=mount_temp_c, end_faces=end_faces,
        end_face_sides=end_face_sides,
        slot_k=slot_k, rpm=rpm, I_phase_rms=I_phase_rms,
        coil_temp_c=coil_temp_c, n_steps_per_period=n_steps_per_period,
        n_periods=n_periods, mesh_size_mm=mesh_size_mm,
        min_size_mm=min_size_mm, outer_air_factor=outer_air_factor)
    frame = _norm_frame(frame)
    end_faces = _norm_end_faces(end_faces)
    # The mount's temperature is the AMBIENT when the field is blank: a machine
    # bolted to an arm in the same room is the answer nobody has to type.
    mount_g_w_per_k = float(mount_g_w_per_k or 0.0)
    t_mount_c = float(ambient_temp if mount_temp_c is None else mount_temp_c)

    # Two parameters survive only so an old client is not broken by a 422 for
    # sending them; both are IGNORED and both say so in the payload.  Silence
    # would be worse than either behaviour: a caller still sending
    # fluid_temp_out_c believes it is choosing the outlet temperature, and it is
    # now the answer.
    _deprecated_notes: List[str] = []
    if float(fluid_temp_out_c or 0.0) > 0.0:
        _deprecated_notes.append(
            "fluid_temp_out_c is ignored: the coolant outlet temperature is now "
            "a RESULT of the solve (T_out = T_in + P/(ṁ·cp)); pass flow_lpm "
            "instead of a target outlet")
    if float(gap_k or 0.0) > 0.0:
        _deprecated_notes.append(
            "gap_k is ignored: the air-gap conductivity is derived per machine "
            "from the mechanical clearance, the rotor speed and the gap "
            "temperature (see cooling.gap)")

    component_mesh = _auto_coil_mesh(component_mesh, mesh_size_mm, _geo_ov)

    assign = _assignments()
    key = _field_cache_key(
        _geo_ov, assign, ambient_temp=ambient_temp, h_conv=h_conv,
        slot_k=slot_k, rpm=rpm, gamma_deg=gamma_deg,
        I_phase_rms=I_phase_rms, n_steps_per_period=n_steps_per_period,
        n_periods=n_periods, mesh_size_mm=mesh_size_mm,
        min_size_mm=min_size_mm, outer_air_factor=outer_air_factor,
        n_sectors=n_sectors, coil_temp_c=coil_temp_c,
        component_mesh=component_mesh, cooling_mode=cooling_mode,
        air_speed_mps=air_speed_mps, fluid=fluid,
        fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
        bore_mode=bore_mode, bore_air_speed_mps=bore_air_speed_mps,
        bore_fluid=bore_fluid, bore_fluid_temp_in_c=bore_fluid_temp_in_c,
        bore_flow_lpm=bore_flow_lpm,
        shaft_ext_length_mm=shaft_ext_length_mm,
        shaft_ext_diameter_mm=shaft_ext_diameter_mm,
        shaft_ext_sides=shaft_ext_sides,
        frame=frame, open_air_speed_mps=open_air_speed_mps,
        emissivity=emissivity, mount_g_w_per_k=mount_g_w_per_k,
        mount_temp_c=mount_temp_c, end_faces=end_faces,
        end_face_sides=end_face_sides)
    # THE MAGNET TEMPERATURE, appended only when the request carries one — the
    # same rule `_field_snap_key_fields` follows, and for the same reason: with
    # no magnet temperature the key must stay byte-identical to the one every
    # entry in this cache was stored under, or the first request after this
    # change would miss a map it already has.  It cannot be left OUT, though: two
    # requests differing only in the magnet's temperature are answered from two
    # different loss maps, and sharing a cache entry would serve one of them the
    # other's temperature field.
    if magnet_temp_c is not None:
        key = key + ("magnet_temp_c", round(float(magnet_temp_c), 2))
    # ── THE MECHANICAL LOSSES, resolved BEFORE the cache is consulted ────────
    # They are a heat SOURCE in this solve (see the docstring), so two requests
    # that differ in them are two answers — and neither the bearing ASSIGNMENT
    # nor its temperature is in `_field_cache_key`, so without this a pair
    # changed in the Mechanical tab would be served the map solved with the old
    # one.  Resolving them here costs a die read and a cached CAD mass; the solve
    # they key costs seconds to minutes.
    _mech = _resolve_mech_losses(_geo_ov, rpm=rpm, bearing_temp_c=bearing_temp_c)
    if _mech.get("has_any"):
        key = key + ("mech", round(float(_mech["P_bearings_W"]), 3),
                     round(float(_mech["P_windage_gap_W"]), 4))
    # An INJECTED map is not cacheable under this key — see the docstring — and
    # a caller that asked to CAPTURE the map must not be handed a cached answer
    # instead: `solve_coupled` needs the loss field itself, not just the
    # temperature, and a cache hit that skipped the lookup would leave it with
    # nothing to scale.  It would then fall back to solving the map on every
    # pass — silently, and only on the machines whose first pass happened to be
    # warm, which is the worst possible way for this to regress.
    hit = (None if (_em_map is not None or _em_capture is not None)
           else _FIELD_CACHE.get(key))
    if hit is not None:
        out = dict(hit)
        out["cached"] = True
        # `solve_time_s` stays the seconds the SOLVE cost when it ran — a cache
        # hit did not make the FEM faster — and `elapsed_s` is what this request
        # actually took, which is the honest pair to print beside "cached".
        out["elapsed_s"] = _elapsed(t0)
        # The deprecation notes belong to the REQUEST, not to the answer: a
        # caller that still sends fluid_temp_out_c must be told it was ignored
        # even when the map it gets back was computed a minute ago for somebody
        # who did not send it.  (Which is exactly why those two fields are not in
        # the cache key: ignored inputs must not split the cache.)
        if isinstance(out.get("cooling"), dict):
            out["cooling"] = {**out["cooling"],
                              "deprecated": list(_deprecated_notes)}
        # The loss provenance stays the one the ANSWER was built from — but say
        # that THIS request did not even get as far as looking for it.
        if isinstance(out.get("loss_source"), dict):
            out["loss_source"] = {**out["loss_source"], "thermal_cache_hit": True}
        return out

    # ── the step budget of this pass ────────────────────────────────────────
    # TWO steps, and no EM frames (2026-09-07).  The bar used to open at
    # `frames + 2` because the first thing a /field request did was run its own
    # multi-frame eddy transient; that solve is gone — the loss map is taken from
    # an Electromagnetic run — so what is left to wait for is the conduction
    # solve and the post-processing.  The coolant-outlet iteration re-uses the
    # SAME conduction step and only renames the phase ("conduction pass 2/4"), so
    # a run that needed four passes does not make the bar longer than one that
    # needed one.
    # A study blackboard can carry a key called "progress" (modules.solvers
    # forwards whatever the payload holds that this signature accepts), and a
    # string is not a callback.  Ignore anything that cannot be called rather
    # than failing a solve over a reporting detail.
    if not callable(progress):
        progress = None
    _TOTAL = 2
    # `_comp_str`, not `_comp`: there is a `def _comp(mask)` further down this
    # function (the per-component temperature helper) and it SHADOWED the
    # string, so the last two progress reports published a function repr as the
    # bar's composition.  Caught by tests/test_progress_routes.py.
    _comp_str = "conduction + post-processing"

    def _report(done, total=None, phase=None):
        if progress is None:
            return
        progress(int(done), _TOTAL, phase, _comp_str)

    _report(0, phase="loss map — looking for the Electromagnetic run")

    def _phase(msg):
        _report(0, phase=msg)

    # 1. The CYCLE-AVERAGED loss map on the mesh — taken from the Electromagnetic
    # run that solved this operating point, or from this router's memory of one.
    # NEVER computed here (see `_em_loss_map`): a missing run is a 422, not a
    # solve started behind a temperature request.
    # ISOLATION from the electromagnetic solver's cross-run state (user
    # 2026-09-07: "надо полностью разделить решатели ... чтобы они никак не
    # пересекались").  Nothing under this call solves any more, but the
    # ContextVar stays: the snapshot replay goes through the same field-view
    # machinery, and the one time this path published its state a full-ring eddy
    # field went into config/.warm_cache.npz, the next 1/2-sector sweep read it
    # as a usable seed, and 10 of 10 points died at the seeded hang cap.  A
    # request-scoped switch costs nothing and closes that door for good; the
    # process-wide env var would also switch the seed off under a transient
    # another request is running.
    if _em_map is not None:
        em = _em_map
        loss_source = dict(_em_loss_source or {"kind": "provided", "note": ""})
        _phase("loss map — %s" % (loss_source.get("note") or "supplied map"))
    else:
        from motor_ai_sim.simulation.fem_solver_2d import _NO_WARM_CACHE_CTX
        _nwc_token = _NO_WARM_CACHE_CTX.set(True)
        try:
            em, loss_source = _em_loss_map(
                gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
                n_steps_per_period=n_steps_per_period, n_periods=n_periods,
                mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
                outer_air_factor=outer_air_factor, n_sectors=n_sectors,
                coil_temp_c=coil_temp_c, component_mesh=component_mesh,
                geo=geo, geo_ov=_geo_ov, phase_cb=_phase,
                magnet_temp_c=magnet_temp_c, op_mode=op_mode)
        finally:
            _NO_WARM_CACHE_CTX.reset(_nwc_token)
    if isinstance(_em_capture, dict):
        _em_capture["em"] = em
        _em_capture["loss_source"] = dict(loss_source)
    _report(0, phase="materials (conductivities, slot stack)")
    verts = _np.asarray(em["vertices"], float)         # (n,2) metres
    tris = _np.asarray(em["triangles"], int)            # (m,3)
    tags = _np.asarray(em["domain_per_tri"], int)       # collapsed palette tags
    loss_dens = _np.asarray(em.get("loss_density_per_tri") or [], float)
    if loss_dens.size != tris.shape[0]:
        loss_dens = _np.zeros(tris.shape[0])
    # `P_cu_exact_W`, not `P_cu_W`: the latter is a DISPLAY number rounded to
    # 0.1 W, and this is a solver reading another solver.  On the 30 mm fixture
    # the whole winding makes 2.6 W, so an 18 K swing of coil temperature moved
    # the rounded value by nothing at all and the coupled loop's own feedback
    # vanished into the quantum.  The rounded key is the fallback for any
    # payload that predates the exact one.
    Pcu = float(em.get("P_cu_exact_W", em.get("P_cu_W", 0.0)) or 0.0)
    # THE END TURNS ARE ALREADY IN IT.  `field_ops.copper_loss_W` returns
    # rho·J²·V_active·k_end, so every copper number that reaches here — the
    # per-frame series and the coupled solve's total alike — is billed for the
    # end windings, and this plane deposits all of it into the slot.  Adding
    # k_end here again double-counts them; it was tried on 2026-09-10 and the
    # copper-scaling identities caught it.


    # 2. geometry: housing radius + copper volume (for the copper heat density)
    from motor_ai_sim.config import get_config
    cfg = get_config()
    # merge_geo_override, not a dict-update: the winding thermal model below
    # reads the DERIVED slot_width (copper fill, bulk transverse k), which the
    # override does not carry — a plain update left the config's value in place.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _merge_geo
    g = _merge_geo(dict(cfg.get("geometry", {})), _geo_ov)
    R_house = float(g.get("stator_diameter", 200.0)) / 2.0 * 1e-3
    stator_inner_m = R_house - float(g.get("core_thickness", 5.0)) * 1e-3 \
        - float(g.get("slot_height", 18.0)) * 1e-3
    # The rotor's IRON outside diameter, and — separately — the rotor's TRUE
    # outside diameter.  With a retaining sleeve fitted they are not the same
    # radius, and everything downstream cares which one it is given: the sleeve
    # occupies [iron OD, iron OD + t] and the MECHANICAL clearance the rotor
    # turns in is what is left of air_gap.  `air_gap` in this project's geometry
    # is measured to the IRON (see geometry_validation.sleeve_gap_error: a
    # sleeve as thick as the gap rubs on the stator bore), so reading δ as
    # `air_gap` over-states the clearance by the whole sleeve thickness — and
    # the Taylor number goes as δ³.
    rotor_iron_outer_m = stator_inner_m - float(g.get("air_gap", 0.6)) * 1e-3
    sleeve_t_m = max(float(g.get("sleeve_thickness", 0.0) or 0.0), 0.0) * 1e-3
    rotor_outer_m = rotor_iron_outer_m + sleeve_t_m        # true rotor OD

    # ── the rotor's loss map, made periodic per pole (2026-09-09) ───────────
    # The Electromagnetic sector solve hands over a per-element eddy loss that
    # is NOT the same from pole to pole: on the G2-L40 quarter the seven magnets
    # carried 4.14 / 4.30 / 3.85 / 3.98 / 4.08 / 4.05 / 3.48 W — ±5 %, and the
    # pole at the 90° cut 15 % short — and the temperature map showed exactly
    # that pole 2 K cooler (user: "опять та же картина с пятнами").  A balanced
    # machine heats every pole alike; the spread is the transient's numerics
    # (eddy start-up, the sliding band at the sector edge), not a hotter magnet.
    # So each rotor-side domain's per-pole watts are brought to their mean —
    # the within-pole distribution and the TOTAL are kept — and the spread that
    # was removed is reported beside the map.
    _n_sect = max(int(em.get("n_sectors") or em.get("symmetry_mult") or 1), 1)
    _pps = int(em.get("poles_per_sector") or 0)
    if _pps <= 0:
        _np_total = int(g.get("num_poles") or round(
            float(g.get("num_seg", 1)) * float(g.get("num_poles_per_segment", 0) or 0)))
        _pps = (_np_total // _n_sect) if _np_total else 0
    loss_dens, _per_pole = _symmetrise_rotor_losses_per_pole(
        loss_dens, verts, tris, tags, rotor_outer_m, _n_sect, _pps)
    if _per_pole:
        loss_source["per_pole"] = _per_pole

    L = float(g.get("motor_length", 30.0)) * 1e-3
    num_slots = int(g.get("num_slots") or round(float(g.get("num_seg", 1)) * float(g.get("num_slots_per_segment", 6))))
    # CONDUCTORS per slot, not wire rows: with wire_split = N each row is N
    # strips of wire_width and the slot holds N times the copper.  Counting rows
    # here made q_cu = Pcu/V_cu N times too big while the MESH carried all N
    # strips, so the map deposited N·Pcu into the winding (measured 2×
    # on a wire_split = 2 fixture, 2026-09-08).
    from motor_ai_sim.winding import conductors_per_slot as _cond_slot
    try:
        _n_cond = float(_cond_slot(g) or g.get("num_wires_per_slot", 12))
    except (ValueError, TypeError):      # unusable knob → the rows alone
        _n_cond = float(g.get("num_wires_per_slot", 12))
    V_cu = (num_slots * _n_cond
            * float(g.get("wire_width", 5.0)) * 1e-3
            * float(g.get("wire_height", 0.8)) * 1e-3 * L)
    q_cu = (Pcu / V_cu) if V_cu > 1e-12 else 0.0

    # 3. materials → conductivities
    mats = cfg.get("materials", {})
    k_steel = _thermal_k("steel", mats.get("stator_core"), 25.0)
    k_mag = _thermal_k("magnet", mats.get("magnet"), 8.0)
    k_shaft = _thermal_k_any(mats.get("shaft"), 150.0)

    # Slot insulation as a SERIES thermal resistance on the copper→iron path.
    # The liner (insulation_thickness, Nomex/ceramic) and wire enamel are sub-mesh
    # thin (0.05–0.15 mm < 0.3 mm mesh), so we LUMP them into the coil-region
    # effective conductivity rather than meshing thin strips:
    #     k_eff = h_slot / (h_slot/k_winding + t_liner/k_liner)   (series, ≤ k_winding)
    # k_winding = the slot_k param (Cu + enamel + air, transverse).  Effect:
    # Nomex (k≈0.14) → strong barrier → HOTTER windings;  AlN ceramic (k≈170) →
    # negligible barrier → k_eff≈k_winding (cooler).  The real liner trade-off.
    k_liner = _thermal_k("insulator", mats.get("slot_insulation"), 0.14)
    t_liner = float(g.get("insulation_thickness", 0.2))    # mm  (liner thickness)
    h_slot  = float(g.get("slot_height", 14.0))            # mm  (winding radial extent)
    # Winding bulk transverse k FROM THE ACTUAL WIRE STACK — a volume-weighted
    # SERIES ("layered") mean, not a hardcoded guess and not a copper-inclusion
    # (Maxwell) estimate, which the high copper fraction inflates to ~0.4.  Heat
    # leaving the slot crosses, IN SERIES, the stacked conductors (k≈400 → negligible
    # R) and the inter-wire gaps; those gaps are air-dominated (thin enamel build /
    # imperfect impregnation), and air (k≈0.026) sets the resistance.  For this
    # 8-wire winding the series mean is ≈0.18 W/m·K — the realistic transverse value.
    # A high constant slot_k thermally SHORTS the windings to the iron → no hotspot
    # (the old bug).  Pass slot_k>0 to override with a manual value.
    k_cu_w   = _thermal_k_any(mats.get("winding") or mats.get("conductor"), 400.0)
    k_enamel = _thermal_k("insulator", mats.get("wire_insulation"), 0.12)
    k_gap    = 0.026                                       # still air in the inter-wire gaps
    _sw = float(g.get("slot_width", 3.0)); _nw = float(g.get("num_wires_per_slot", 8))
    _ww = float(g.get("wire_width", 2.5)); _wh = float(g.get("wire_height", 0.5))
    _sy = float(g.get("wire_spacing_y", 0.1))              # mm, inter-wire gap (air-filled)
    # The FILL counts every strip (`_n_cond`) against the slot the split widened
    # (`slot_width` carries the wire column — see geometry/motor_geometry).  The
    # SERIES stack below is radial and the split is tangential, so `_nw` rows is
    # the right count there and only there.
    f_cu = min(max((_n_cond * _ww * _wh) / max(_sw * h_slot, 1e-6), 0.0), 0.92)  # copper fill (reported)
    _d_cu  = _nw * _wh                                      # total copper thickness across the stack
    _d_gap = max(_nw - 1.0, 0.0) * _sy                      # total inter-wire gap thickness
    _R_ser = _d_cu / max(k_cu_w, 1e-6) + _d_gap / max(k_gap, 1e-6)   # series resistance (copper + gaps)
    slot_k_auto = (_d_cu + _d_gap) / max(_R_ser, 1e-9)      # winding bulk transverse k (≈0.18)
    slot_k_used = float(slot_k) if float(slot_k) > 0.0 else slot_k_auto   # >0 = manual override
    slot_k_eff = h_slot / (
        h_slot / max(slot_k_used, 1e-6) + t_liner / max(float(k_liner), 1e-6))   # + liner in series

    # WIRE COATING — whatever occupies the slot that is neither copper, nor the
    # liner, nor the wire film: the impregnating varnish/resin in a potted
    # machine, plain air in an unpotted one.  The materials library has no card
    # for it (2026-09-07: `insulator:` carries polyimide, Nomex, Al2O3, AlN and
    # the sleeve laminates, none of them an impregnant), so an assignment is
    # honoured when one exists and otherwise a DOCUMENTED default is used and
    # REPORTED as such — 0.25 W/m·K, the usual figure for a filled epoxy
    # varnish, which sits an order of magnitude above still air (0.026) and an
    # order below the liner's ceramic options.  The payload names the source, so
    # an engineer who pots with something else can see that the number was ours
    # and not theirs.
    SLOT_FILL_K_DEFAULT = 0.25
    _fill_name = (assign.get("slot_fill") or mats.get("slot_fill")
                  or assign.get("impregnation") or mats.get("impregnation"))
    if _fill_name:
        k_fill = _thermal_k("insulator", _fill_name, SLOT_FILL_K_DEFAULT)
        fill_src = "library"
    else:
        k_fill = SLOT_FILL_K_DEFAULT
        fill_src = "default"

    # 4. per-element k + q from the collapsed domain tags
    (DOM_AIR, DOM_STATOR, DOM_COIL, DOM_AIRGAP, DOM_MAG_N, DOM_ROTOR,
     DOM_SHAFT, DOM_BAND, DOM_OUTER, DOM_SLEEVE, DOM_MAG_S) = \
        0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 44
    tags = tags.copy()                     # we may RE-TAG the sleeve annulus
    q_elem = loss_dens.copy()
    is_steel = (tags == DOM_STATOR) | (tags == DOM_ROTOR)
    is_mag = (tags == DOM_MAG_N) | (tags == DOM_MAG_S)
    is_coil = (tags == DOM_COIL)

    # ── the retaining sleeve, as its OWN anisotropic domain ──────────────────
    # User 2026-09-07: "у него теплопроводность очень плохая в радиальном
    # направлении".  The EM mesh already builds the ring as DOM_SLEEVE (it has
    # its own eddy loss), but the thermal solve used to hand it the DEFAULT
    # element conductivity — the air-gap value — because nothing assigned it one.
    # A sleeve modelled as air is a sleeve that is not there; a sleeve modelled
    # as an isotropic average is a rotor that cools through the fibres, which is
    # a path the heat cannot take.  So: keep the ring's elements as a domain of
    # their own and give them a TENSOR — radial (through-thickness) low, hoop
    # (along the fibres) high, both from the assigned material's card.
    sleeve_info = None
    sleeve_lumped = False
    is_sleeve = (tags == DOM_SLEEVE)
    if sleeve_t_m > 0.0:
        if not is_sleeve.any():
            # No DOM_SLEEVE elements: this mesh path put the ring in with the
            # gap air.  Reclaim it by RADIUS — the annulus between the iron OD
            # and the true rotor OD — and re-tag, so the drop below keeps it.
            _cx = verts[tris, 0].mean(axis=1)
            _cy = verts[tris, 1].mean(axis=1)
            _rc = _np.hypot(_cx, _cy)
            _tol = 0.05 * sleeve_t_m
            is_sleeve = ((_rc >= rotor_iron_outer_m - _tol)
                         & (_rc <= rotor_outer_m + _tol)
                         & _np.isin(tags, [DOM_AIR, DOM_AIRGAP, DOM_BAND]))
            if is_sleeve.any():
                tags[is_sleeve] = DOM_SLEEVE
        # `assign`, not `mats`: the request's ?mat= override must decide the
        # sleeve's conductivity too, or a client evaluating M55J against T800 on
        # its own copy of the machine would get the config's sleeve twice.
        _sleeve_name = assign.get("sleeve") or mats.get("sleeve")
        k_sl_r, k_sl_f, k_src = _sleeve_k(_sleeve_name)
        sleeve_lumped = not bool(is_sleeve.any())
        sleeve_info = {
            "present": True,
            "k_radial": round(float(k_sl_r), 3),
            "k_fibre": round(float(k_sl_f), 3),
            # `k` is the RADIAL value under its old single-conductivity name.
            # The Thermal tab prints "sleeve in the gap path — t mm · k …" and
            # the sentence it belongs to is about heat crossing the ring on its
            # way to the gap, so the through-thickness number is the honest one
            # to put there; the fibre value would flatter the design by ~10x.
            "k": round(float(k_sl_r), 3),
            "thickness_mm": round(sleeve_t_m * 1e3, 3),
            "material": _sleeve_name,
            "source": k_src,
            "n_elements": int(is_sleeve.sum()),
            "model": ("lumped radial resistance in series with the gap bridge"
                      if sleeve_lumped else
                      "meshed domain with an anisotropic (r, θ) conductivity "
                      "tensor"),
            "note": ("radial (through-thickness) conductivity is the one that "
                     "matters: it is the rotor's series resistance to the gap. "
                     + ("Read from the materials library."
                        if k_src == "library" else
                        "The assigned material carries no thermal card — "
                        "documented CFRP UD-60 %% defaults used.")),
        }

    # ── 4b. the AIR, kept and named ──────────────────────────────────────────
    # User 2026-09-07: *"надо рисовать изоляцию и покрытие провода, а то пустое
    # место, и воздух тоже показывать — он же входит в расчёт, и в дереве
    # отображать их тоже нужно"*.  Everything the mesh calls air is re-tagged by
    # geometry into the five materials it actually is (see
    # `_retag_thermal_domains`), so the insulation, the wire enamel, the slot
    # fill, the air gap and the rotor's pocket air are SOLVED domains with their
    # own conductivities and rows of their own in the Part tree — instead of the
    # white space the map used to show around the wires and around the rotor.
    #
    # The innermost SOLID radius is measured before the re-tag and handed to it:
    # it is what separates the rotor's pocket air (a domain, kept) from the bore
    # air (the coolant side of the bore boundary condition, dropped).
    _solid = ~_np.isin(tags, (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER,
                              DOM_TPL_LINER, DOM_TPL_ENAMEL))
    if _solid.any():
        _sn = _np.unique(tris[_solid])
        r_inner_solid_m = float(_np.hypot(verts[_sn, 0], verts[_sn, 1]).min())
    else:
        r_inner_solid_m = 0.0
    try:
        _polys = _thermal_polys(geo, _live_fingerprint(_geo_ov))
    except Exception as exc:  # noqa: BLE001 — a missing outline is not an answer
        log.warning("thermal: could not build the cross-section polygons (%s)", exc)
        _polys = {}
    tags, air_report = _retag_thermal_domains(
        verts, tris, tags, _polys, r_housing_m=R_house,
        stator_inner_m=stator_inner_m, rotor_outer_m=rotor_outer_m,
        r_inner_solid_m=r_inner_solid_m)
    is_liner = (tags == DOM_SLOT_LINER)
    is_enamel = (tags == DOM_WIRE_ENAMEL)
    is_fill = (tags == DOM_SLOT_FILL)
    is_gap_air = (tags == DOM_GAP_AIR)
    is_pocket = (tags == DOM_POCKET_AIR)
    # The slip radius the sliding band was built on — where the rotor half and
    # the stator half of the gap mesh meet with duplicate nodes.  From the CAD
    # (`mid_r_mm`), falling back to the mesh's own report and then to the middle
    # of the clearance, so a payload that carries neither still ties something
    # sensible rather than silently not tying at all.
    slip_r_m = 0.0
    try:
        slip_r_m = float(_polys.get("mid_r_mm") or 0.0) * 1e-3
    except Exception:  # noqa: BLE001
        slip_r_m = 0.0
    if slip_r_m <= 0.0:
        slip_r_m = float(em.get("r_slip_m") or 0.0)
    if not (rotor_outer_m < slip_r_m < stator_inner_m):
        slip_r_m = 0.5 * (rotor_outer_m + stator_inner_m)

    # 5. cooling: the OUTER (housing) surface and the BORE (rotor ID) surface.
    # The bore radius is the innermost SOLID node of the sub-mesh the conduction
    # solve will build — the shaft bore when the shaft is a tube, the rotor inner
    # radius when the shaft is excluded from the model.  Measured here rather
    # than read off a parameter because neither of those is a geometry field.
    _keep_mask = ~_np.isin(tags, _np.asarray(DROP_TAGS, int))
    if _keep_mask.any():
        _rn = _np.hypot(verts[:, 0], verts[:, 1])[_np.unique(tris[_keep_mask])]
        r_bore_m = float(_rn.min())
    else:
        r_bore_m = 0.0
    if r_bore_m < max(1e-5, 0.02 * R_house):
        r_bore_m = 0.0                     # solid to the axis: there is no bore
    if bore_mode != "none" and r_bore_m <= 0.0:
        raise _bad("bore_mode", bore_mode, "bad_value",
                   "this machine has no rotor bore to cool: the cross-section "
                   "is solid to the axis (a solid shaft, or a shaft excluded "
                   "from the model with no rotor hole behind it).  Give the "
                   "shaft a bore, or use bore_mode=none.",
                   error="no bore surface on this cross-section")

    # Same reason as `Pcu` above: this drives the coolant's energy balance.
    P_loss_total = float(em.get("P_loss_total_exact_W")
                         or em.get("P_loss_total_W") or 0.0)
    # The mesh is a SYMMETRY WEDGE: its facet integrals are the wedge's watts,
    # while a pump's L/min and a fan's m/s are the whole machine's.  Everything
    # reported below is scaled up by the model's own symmetry multiplier so the
    # energy balance on the coolant stream is done in machine watts.
    sym = max(int(em.get("symmetry_mult") or 1), 1)

    # ── the SHAFT ENDS: the rotor's third heat path ───────────────────────────
    # User 2026-09-07: *"торцы и лобовые части — только для вала, всё остальное
    # вращается внутри мотора"*.  The rotor's end faces and the end windings are
    # inside a CLOSED housing, spinning in their own air — whatever they hand to
    # that air comes straight back through the housing, so there is no extra
    # path there and modelling one would flatter every design.  The SHAFT is the
    # exception: it comes out through the bearings and the exposed stubs sit in
    # the room's air, turning.  That is real, it is separate, and until now the
    # 2-D cross-section had no way to carry it.
    #
    # It enters as a LUMPED VOLUME SINK on the shaft elements (see
    # `thermal_solver_2d.solve_steady_thermal`'s `volume_sinks`) rather than as
    # a boundary film: the surface it acts on is not in this cross-section at
    # all — it is out along the axis — so there are no facets to put an h on.
    # The conductance is a fin, not a wetted area: on a 20 mm steel shaft in
    # still air m ≈ 12 /m, so 100 mm of stub is mL ≈ 1.2 and only ~70 % as good
    # as its own surface suggests.
    shaft_ends: Optional[Dict[str, Any]] = None
    _sinks: List[Dict[str, Any]] = []
    _shaft_len_m = max(float(shaft_ext_length_mm), 0.0) * 1e-3
    _shaft_mask = (tags == DOM_SHAFT)
    _shaft_path: Dict[str, Any] = {}
    _shaft_d_out = 0.0
    _shaft_d_src = ""
    if _shaft_len_m > 0.0:
        if not _shaft_mask.any():
            # NAMED, never silently dropped: a client that asked for the shaft
            # path and got a map without it would read the temperatures as if
            # the stubs were cooling a machine that has no shaft in its model.
            raise _bad("shaft_ext_length_mm", shaft_ext_length_mm, "bad_value",
                       "this cross-section has no shaft to conduct along: the "
                       "mesh carries no shaft elements (the shaft part is "
                       "excluded from the model, or this geometry has none).  "
                       "Include the shaft, or set shaft_ext_length_mm = 0.",
                       error="no shaft elements in this cross-section")
        # The exposed stub's OD.  Given wins; otherwise the geometry's own
        # `rotor_inner_radius` (the shaft tube's outer edge — the CAD builds the
        # shaft as the ring shaft_inner_radius → rotor_inner_radius), and if the
        # dict does not carry that derived field, MEASURED off the shaft
        # elements themselves.  Measuring last rather than first because a
        # symmetry wedge's outermost shaft node is still the true radius but a
        # coarse mesh rounds it, and the CAD number is exact.
        _shaft_d_out = max(float(shaft_ext_diameter_mm), 0.0) * 1e-3
        _shaft_d_src = "given"
        if _shaft_d_out <= 0.0:
            _rir = float(g.get("rotor_inner_radius") or 0.0) * 1e-3
            if _rir > 0.0:
                _shaft_d_out, _shaft_d_src = 2.0 * _rir, "geometry"
            else:
                _sn = _np.unique(tris[_shaft_mask])
                _shaft_d_out = 2.0 * float(
                    _np.hypot(verts[_sn, 0], verts[_sn, 1]).max())
                _shaft_d_src = "measured on the shaft elements"
        # The bore is the mesh's own innermost solid radius — the same number the
        # bore film is applied at, so the two cannot disagree about how much
        # steel there is to conduct along.  It removes area from the fin's
        # CROSS-SECTION and nothing from its wetted perimeter: air inside a bore
        # is either still (closed shaft) or already counted as the `bore`
        # surface, and claiming it twice would be the same watts on two paths.
        from motor_ai_sim.simulation import cooling_models as _cm
        _shaft_path = _cm.shaft_ends_path(
            rpm=rpm, t_ambient_c=ambient_temp, d_out_m=_shaft_d_out,
            d_in_m=2.0 * max(r_bore_m, 0.0), k_shaft=k_shaft,
            length_each_side_m=_shaft_len_m, n_sides=int(shaft_ext_sides))
        _sinks = [{"name": "shaft_ends", "tags": [DOM_SHAFT],
                   "G_W_per_K": float(_shaft_path["G_W_per_K"]),
                   "t_sink_c": float(ambient_temp),
                   # G is the WHOLE machine's (a real shaft, a real stub); the
                   # mesh may be a 1/sym wedge carrying 1/sym of the shaft.
                   "symmetry_mult": sym}]

    # ── THE OPEN FRAME: end windings and slot channels in the wash ───────────
    # User 2026-09-09, on the 40 mm CIANO14: the machine has NO HOUSING — the
    # tooth blocks with their coils hang between two end plates on standoff
    # pins, and the end turns plus the axial channels between neighbouring coils
    # are in the propeller wash at 10-12 m/s.  Everything above assumes the
    # opposite (a closed box, 2026-09-07), which is why this is a MODE and not a
    # correction: on a housed motor the end turns really do have nowhere to send
    # their heat, and adding a path there would flatter every housed design.
    #
    # Both are `volume_sinks`, for the same reason the shaft ends are: the
    # surfaces are not in this cross-section.  The end turns are out along the
    # axis; the slot channel's walls are INSIDE the mesh, not on its boundary, so
    # a Robin surface would have to be applied to an interior facet set — and a
    # film on the wrong side of the liner is not the same machine.
    end_windings: Optional[Dict[str, Any]] = None
    slot_channels: Optional[Dict[str, Any]] = None
    _ew_path: Dict[str, Any] = {}
    _ch_path: Dict[str, Any] = {}
    _open_notes: List[str] = []
    _k_end, _k_end_src = 1.0, "not needed"
    _n_coils_note = ""
    # The speed: what was asked for, else the housing's own air when the outer
    # surface is in air (it is the SAME wash — the tooth backs and the end turns
    # are millimetres apart on this machine, and making the user type one number
    # twice is how the two end up disagreeing), else still air.
    _open_v = max(float(open_air_speed_mps), 0.0)
    _open_v_src = "given"
    if _open_v <= 0.0 and cooling_mode == "air":
        _open_v = max(float(air_speed_mps), 0.0)
        _open_v_src = "the housing air speed (open_air_speed_mps = 0)"
    elif _open_v <= 0.0:
        _open_v_src = "still air (open_air_speed_mps = 0, outer surface not in air)"
    if frame == "open":
        if not is_coil.any():
            # NAMED, exactly like the shaft path: a client that asked for an
            # open frame and got a map without the end turns would read the
            # winding temperature as if the wash were cooling copper that is
            # not in the model.
            raise _bad("frame", frame, "bad_value",
                       "frame=open puts the END WINDINGS in the airflow, and "
                       "this cross-section carries no winding elements (the "
                       "windings part is excluded from the model, or this mesh "
                       "resolved none).  Include the windings, or use "
                       "frame=housed.",
                       error="no winding elements in this cross-section")
        _k_end, _k_end_src = _end_winding_factor(em, _geo_ov)
        # ONE coil per tooth — these are fractional-slot concentrated windings,
        # the same assumption `masses.end_winding_factor` makes when it models
        # the end turn as a half-loop over ONE tooth — so the machine has as many
        # coils as it has slots.  `num_slots`, and NOT the config's
        # `winding.n_coils_per_phase × 3` (which is the same 12 on the live
        # machine): the slot count follows this request's `?geo=` override and
        # the winding block does not, so a client evaluating a 24-slot variant of
        # its own would otherwise get the shared config's 12 coils.
        _n_coils = num_slots
        _n_coils_note = "one coil per tooth (concentrated winding): n = num_slots"
        # The bundle: `num_wires_per_slot` ROWS deep (each row a wire plus the
        # air/varnish gap above it) by the whole wire COLUMN wide.  The column,
        # not one strip: with wire_split = N the end turn has to get around all
        # N strips and the insulation between them — the same `winding_footprint_mm`
        # `masses.end_winding_factor` builds the loop radius on.
        try:
            from motor_ai_sim.winding import winding_footprint_mm as _fp
            _bar_w_mm = float(_fp(g) or 0.0)
        except (ValueError, TypeError):
            _bar_w_mm = 0.0
        if _bar_w_mm <= 0.0:
            _bar_w_mm = float(g.get("wire_width", 0.0) or 0.0)
        _bar_t_mm = float(g.get("num_wires_per_slot", 0) or 0) * (
            float(g.get("wire_height", 0.0) or 0.0)
            + float(g.get("wire_spacing_y", 0.0) or 0.0))
        # ℓ_end per side, from the run's own k_end.  k_end = 1 means the model
        # has no end turns at all, so there is nothing to cool.
        _ell_m = max(float(_k_end) - 1.0, 0.0) * L / 2.0
        from motor_ai_sim.simulation import cooling_models as _cm2
        _ew_path = _cm2.end_windings_path(
            air_speed_mps=_open_v, t_ambient_c=ambient_temp,
            n_coils=int(_n_coils), bar_thickness_m=_bar_t_mm * 1e-3,
            bar_width_m=_bar_w_mm * 1e-3, end_turn_length_m=_ell_m, n_sides=2)
        if float(_ew_path["G_W_per_K"]) > 0.0:
            _sinks.append({"name": "end_windings", "tags": [DOM_COIL],
                           "G_W_per_K": float(_ew_path["G_W_per_K"]),
                           "t_sink_c": float(ambient_temp),
                           # A whole-machine conductance (all the coils, both
                           # ends) on a possibly 1/sym wedge — same bookkeeping
                           # the shaft ends use.
                           "symmetry_mult": sym})
        else:
            _open_notes.append(
                "the end windings carry no conductance: k_end = %.3f (%s), so "
                "this model has no end turns to put in the airflow"
                % (_k_end, _k_end_src))

        # THE SLOT CHANNELS, measured on the mesh rather than derived: the free
        # area of a slot once the wires are in it is what is left over, and the
        # mesh already knows exactly what that is.
        _per_w, _acs_w = _domain_wetted_perimeter(verts, tris, tags, is_fill)
        _ch_path = _cm2.slot_channels_path(
            air_speed_mps=_open_v, t_ambient_c=ambient_temp,
            wetted_perimeter_m=_per_w * sym, cross_section_m2=_acs_w * sym,
            length_m=L, n_channels=int(num_slots))
        if float(_ch_path["G_W_per_K"]) > 0.0:
            _sinks.append({"name": "slot_channels", "tags": [DOM_SLOT_FILL],
                           "G_W_per_K": float(_ch_path["G_W_per_K"]),
                           "t_sink_c": float(ambient_temp),
                           "symmetry_mult": sym})
        else:
            _open_notes.append(
                "the slot channels are NOT modelled: this mesh resolves no slot "
                "air between the conductors (%d elements), so there is no duct "
                "to ventilate.  Refine the coil mesh — the channel is what is "
                "left of the slot once the wires are in it."
                % int(is_fill.sum()))

    # ── THE MOUNT: the flange the joint is bolted to (2026-09-14) ────────────
    # User, 2026-09-14: a robot joint in still air.  Add up what the housing can
    # hand the room on the Ø85 machine — ~3 W of the 64 W it makes at the rated
    # point — and the answer is that THE AIR IS NOT THE COOLING SYSTEM: the heat
    # leaves through the bolts.  That path is a contact conductance, not a film;
    # nothing in this model can predict it (bolt pattern, contact area, dry pad
    # or grease, the flatness of two machined faces), so it is an INPUT in W/K to
    # a HELD temperature, and the model's job is to be honest about which of the
    # two numbers the machine's temperature hangs on.
    #
    # It enters as a `volume_sink` on the STATOR elements for the same reason the
    # shaft stubs do: a 2-D cross-section has no face out along the axis for a
    # flange to sit on.  The identity `mount_W == G·(t_housing_mean_c − t_mount)`
    # is then checkable from the payload rather than trusted.
    from motor_ai_sim.simulation import cooling_models as _cm3
    _mount_path = _cm3.mount_path(g_w_per_k=mount_g_w_per_k, t_mount_c=t_mount_c)
    _mount_on = bool(float(_mount_path["G_W_per_K"]) > 0.0)
    if _mount_on:
        if not (tags == DOM_STATOR).any():
            raise _bad("mount_g_w_per_k", mount_g_w_per_k, "bad_value",
                       "this cross-section has no stator elements for the mount "
                       "to conduct into (the stator part is excluded from the "
                       "model, or this mesh resolved none).  Include the stator, "
                       "or set mount_g_w_per_k = 0.",
                       error="no stator elements in this cross-section")
        _sinks.append({"name": "mount", "tags": [DOM_STATOR],
                       "G_W_per_K": float(_mount_path["G_W_per_K"]),
                       "t_sink_c": float(t_mount_c),
                       # A WHOLE-MACHINE conductance (one real flange, real
                       # bolts) on a possibly 1/sym wedge — the same bookkeeping
                       # the shaft ends and the open frame's two paths use.
                       "symmetry_mult": sym})

    # ── THE AXIAL END FACES (2026-09-14) ─────────────────────────────────────
    # User, 2026-09-14, with the Fusion model in front of him: the 24 coils stand
    # PROUD of the core on both sides — hairpin-like, fully exposed — and the
    # stator / rotor end faces are uncovered too.  That is the 2026-09-07 ruling
    # this file's shaft section is built on (*"торцы и лобовые части — только для
    # вала"*) turned round for THIS build, which is why it rides with the
    # robotics mode and is off everywhere else: on a housed machine the end turns
    # really do have nowhere to send their heat, and adding a path there would
    # flatter every design that has a lid.
    #
    # Four lumped conductances, one per node, all `volume_sinks` for the same
    # reason as everything else axial here.  The AREAS are measured, not typed:
    # the end turns from `end_winding_area` on this run's own k_end (the same
    # derivation the open frame uses), the cores and the magnets from THIS MESH's
    # own per-tag section area × the machine's symmetry × the number of exposed
    # ends.  Both films are re-evaluated against the wall temperature in the pass
    # loop below, which is what `_wall_iterating` is for.
    def _tag_area_m2(mask) -> float:
        """The masked elements' AREA in this (possibly wedge) mesh [m²]."""
        if not mask.any():
            return 0.0
        _v = verts[tris[mask]]                      # (k, 3, 2)
        _a = 0.5 * _np.abs(
            (_v[:, 1, 0] - _v[:, 0, 0]) * (_v[:, 2, 1] - _v[:, 0, 1])
            - (_v[:, 2, 0] - _v[:, 0, 0]) * (_v[:, 1, 1] - _v[:, 0, 1]))
        return float(_a.sum())

    _ef_on = bool(cooling_mode == "robotics" and end_faces == "still")
    _ef_n = max(1, min(int(end_face_sides), 2))
    _ef_specs: List[Dict[str, Any]] = []
    _ef_notes: List[str] = []
    if _ef_on:
        _ef_k_end, _ef_k_end_src = _end_winding_factor(em, _geo_ov)
        try:
            from motor_ai_sim.winding import winding_footprint_mm as _fp2
            _ef_bar_w_mm = float(_fp2(g) or 0.0)
        except (ValueError, TypeError):
            _ef_bar_w_mm = 0.0
        if _ef_bar_w_mm <= 0.0:
            _ef_bar_w_mm = float(g.get("wire_width", 0.0) or 0.0)
        _ef_bar_t_mm = float(g.get("num_wires_per_slot", 0) or 0) * (
            float(g.get("wire_height", 0.0) or 0.0)
            + float(g.get("wire_spacing_y", 0.0) or 0.0))
        _ef_ell_m = max(float(_ef_k_end) - 1.0, 0.0) * L / 2.0
        _ew_geom = _cm3.end_winding_area(
            n_coils=int(num_slots), bar_thickness_m=_ef_bar_t_mm * 1e-3,
            bar_width_m=_ef_bar_w_mm * 1e-3, end_turn_length_m=_ef_ell_m,
            n_sides=_ef_n)
        # PER ONE FACE, because `end_face_still` multiplies by `n_faces` itself.
        _a_ew_face = float(_ew_geom["area_per_side_m2"])
        _d_ew = float(_ew_geom["d_equiv_m"]) or math.sqrt(max(_a_ew_face, 1e-12))

        def _spec(name: str, label: str, tags_list, mask, area_face: float,
                  char_len: float) -> None:
            _ef_specs.append({
                "name": name, "label": label, "tags": list(tags_list),
                "area_face_m2": float(area_face),
                "char_len_m": float(char_len),
                "has_elements": bool(mask.any())})

        _a_stator_face = _tag_area_m2(tags == DOM_STATOR) * sym
        _a_rotor_face = _tag_area_m2(tags == DOM_ROTOR) * sym
        _a_magnet_face = _tag_area_m2(is_mag) * sym
        _spec("end_face_winding", "end windings", [DOM_COIL], is_coil,
              _a_ew_face, _d_ew)
        _spec("end_face_stator", "stator core end face", [DOM_STATOR],
              (tags == DOM_STATOR), _a_stator_face,
              math.sqrt(max(_a_stator_face, 1e-12)))
        _spec("end_face_rotor", "rotor core end face", [DOM_ROTOR],
              (tags == DOM_ROTOR), _a_rotor_face,
              math.sqrt(max(_a_rotor_face, 1e-12)))
        _spec("end_face_magnet", "magnet end face", [DOM_MAG_N, DOM_MAG_S],
              is_mag, _a_magnet_face, math.sqrt(max(_a_magnet_face, 1e-12)))
        if not is_coil.any():
            # NAMED, exactly like the open frame's refusal: the end turns are
            # most of the exposed area on this machine, and a client that asked
            # for them and got a map without them would read the winding
            # temperature as if the room were cooling copper that is not there.
            raise _bad("end_faces", end_faces, "bad_value",
                       "end_faces='still' puts the END WINDINGS in the room's "
                       "air, and this cross-section carries no winding elements "
                       "(the windings part is excluded from the model, or this "
                       "mesh resolved none).  Include the windings, or use "
                       "end_faces='none'.",
                       error="no winding elements in this cross-section")
        if _ef_ell_m <= 0.0:
            _ef_notes.append(
                "the end windings carry no area: k_end = %.3f (%s), so this "
                "model has no end turns standing out of the core"
                % (_ef_k_end, _ef_k_end_src))
        # The SHAFT's own end face is deliberately not one of these: when it
        # sticks out of the machine it is the `shaft_ends` fin path (which is a
        # fin, not a wetted face), and counting it here as well would be the same
        # watts claimed twice.  Under-reading the rotor's axial path is the safe
        # direction, the same rule every judgement call in `cooling_models` takes.
        _ef_notes.append(
            "the shaft's own end face is not counted here — when it comes out of "
            "the housing it is the shaft_ends fin path, and claiming it on both "
            "would be the same watts twice")

    # ── THE MECHANICAL HEAT (2026-09-08) ─────────────────────────────────────
    # User: *"все потери должны передаваться в электромагнитный расчёт"* — and
    # into this one.  The two analytic terms enter where they are MADE:
    #
    #   * bearing friction on the SHAFT, and only when the shaft-ends path is on.
    #     The bearings are outside this cross-section (they sit on the exposed
    #     stubs, which is why they were never in the 2-D model at all), and the
    #     shaft-ends conductance is the only door there is.  With the path off
    #     the watts are REPORTED as not modelled — never silently dropped, which
    #     is the whole complaint that started this;
    #   * windage's GAP-SHEAR half as a volumetric source in the air-gap domain,
    #     which is literally where the shearing happens.  The END-FACE half is
    #     out along the axis and is reported as not modelled.
    #
    # Both are converted from MACHINE watts to a wedge-honest density the same
    # way `volume_sinks` converts its conductance: q = P / (A_tags · L · sym), so
    # ∫q dV over this (possibly 1/sym) mesh × sym is exactly P again and the
    # budget closes in machine watts like every other flow here.
    def _q_density(mask, p_machine_w: float) -> float:
        """[W/m³] on ``mask`` that integrates to ``p_machine_w`` machine watts."""
        if not mask.any() or not (p_machine_w > 0.0):
            return 0.0
        _v = verts[tris[mask]]                      # (k, 3, 2)
        _a = 0.5 * _np.abs(
            (_v[:, 1, 0] - _v[:, 0, 0]) * (_v[:, 2, 1] - _v[:, 0, 1])
            - (_v[:, 2, 0] - _v[:, 0, 0]) * (_v[:, 1, 1] - _v[:, 0, 1]))
        a_tot = float(_a.sum())
        if not (a_tot > 1e-15 and L > 1e-12):
            return 0.0
        return float(p_machine_w) / (a_tot * L * float(sym))

    _p_brg_in = float(_mech.get("P_bearings_W") or 0.0)
    _brg_modelled = bool(_shaft_len_m > 0.0 and _shaft_mask.any()
                         and _p_brg_in > 0.0)
    if _brg_modelled:
        # ALL of it on the shaft: an UPPER BOUND, and stated as one.  A real
        # bearing sends part of its heat out through the outer ring into the end
        # cap — a path this cross-section does not carry either, so splitting it
        # would mean inventing a fraction to hide watts behind.
        q_elem[_shaft_mask] += _q_density(_shaft_mask, _p_brg_in)
    _p_brg_dropped = 0.0 if _brg_modelled else _p_brg_in
    _p_wind_gap = float(_mech.get("P_windage_gap_W") or 0.0)
    _wind_modelled = bool(is_gap_air.any() and _p_wind_gap > 0.0)
    if _wind_modelled:
        q_elem[is_gap_air] += _q_density(is_gap_air, _p_wind_gap)
    _p_wind_dropped = 0.0 if _wind_modelled else _p_wind_gap
    # The END-FACE half is made out along the axis, on surfaces this
    # cross-section does not have.  Never modelled here, always reported.
    _p_wind_faces = float(_mech.get("P_windage_faces_W") or 0.0)

    # WHAT WAS DONE WITH EACH MECHANICAL WATT — the block the panel, the report
    # and the coupled loop read.  Every term appears exactly once, either as
    # injected heat or as a named omission with its watts on it; "not modelled"
    # with a number beside it is a statement an engineer can act on, and a
    # silently dropped watt is not.
    _mech_notes: List[str] = []
    if _p_brg_in > 0.0 and not _brg_modelled:
        _mech_notes.append(
            "bearing friction %.1f W is NOT modelled in this cross-section: the "
            "bearings sit on the shaft stubs outside the housing and the "
            "shaft-ends heat path is closed (shaft_ext_length_mm = 0%s).  Open "
            "it on the Thermal tab and the friction enters at the shaft seats."
            % (_p_brg_in,
               "" if _shaft_mask.any() else ", and this model carries no shaft"))
    elif _brg_modelled:
        _mech_notes.append(
            "bearing friction %.1f W enters at the shaft, spread over the shaft "
            "elements the exposed-stub conductance acts on.  ALL of it on the "
            "shaft is an UPPER BOUND: a real bearing sends part of its heat out "
            "through the outer ring into the end cap, a path this cross-section "
            "does not carry either." % _p_brg_in)
    if _p_wind_gap > 0.0 and not _wind_modelled:
        _mech_notes.append(
            "windage gap shear %.2f W is NOT modelled: this mesh has no air-gap "
            "elements to deposit it in" % _p_wind_gap)
    elif _wind_modelled:
        _mech_notes.append(
            "windage gap shear %.2f W enters as a volumetric source in the "
            "air-gap air — where the shearing happens" % _p_wind_gap)
    if _p_wind_faces > 0.0:
        _mech_notes.append(
            "windage on the rotor END FACES %.2f W is not modelled in this "
            "cross-section (it is made out along the axis)" % _p_wind_faces)
    if _mech.get("note"):
        _mech_notes.append(str(_mech["note"]))
    mech_losses = {
        "has_bearings": bool(_mech.get("has_bearings")),
        "bearing_temp_c": _mech.get("bearing_temp_c"),
        "bearing_temp_source": _mech.get("bearing_temp_source"),
        "P_bearings_W": round(_p_brg_in, 3),
        "P_bearings_into_shaft_W": round(_p_brg_in if _brg_modelled else 0.0, 3),
        "P_bearings_not_modelled_W": round(_p_brg_dropped, 3),
        "bearings_modelled": bool(_brg_modelled),
        "per_end_W": {k: round(float(v), 3)
                      for k, v in (_mech.get("per_end_W") or {}).items()},
        "P_windage_gap_W": round(_p_wind_gap, 4),
        "P_windage_gap_into_air_W": round(
            _p_wind_gap if _wind_modelled else 0.0, 4),
        "P_windage_faces_not_modelled_W": round(_p_wind_faces, 4),
        "windage_gap_modelled": bool(_wind_modelled),
        "P_mech_total_W": round(_p_brg_in + _p_wind_gap + _p_wind_faces, 3),
        "P_mech_into_map_W": round(
            (_p_brg_in if _brg_modelled else 0.0)
            + (_p_wind_gap if _wind_modelled else 0.0), 3),
        "shaft_ends_open": bool(_shaft_len_m > 0.0),
        "model": ((_mech.get("mech") or {}).get("model")
                  or "SKF frictional-moment model + analytic windage — ANALYTIC, "
                     "not FEM"),
        "notes": _mech_notes,
    }
    # The watts the conduction solve is actually carrying on top of the EM map.
    _p_mech_in = float(mech_losses["P_mech_into_map_W"])

    # Which surfaces have an OUTLET temperature, i.e. carry a bounded stream that
    # heats up?  Those are the ones the conduction solve has to be iterated
    # around: their sink depends on the heat they remove, which is not known
    # until the solve has run.  The housing in cross-flow does NOT — outside air
    # is an unbounded reservoir — so an air-cooled machine still solves once.
    _outlet_iterating = ([n for n, m in (("outer", cooling_mode),
                                         ("bore", bore_mode))
                          if (n == "outer" and m == "liquid")
                          or (n == "bore" and m in ("air", "liquid"))])
    # …and, since 2026-09-14, the surfaces whose COEFFICIENT depends on the wall
    # temperature rather than on the heat they carry: still air goes as ΔT^(1/4)
    # and the linearised radiation re-linearises about the wall, so h is not
    # known until the conduction solve has run either.  Same loop, a second
    # convergence test — and the end-face sinks ride with them, because their
    # whole conductance is that same film times an area.
    _wall_iterating = ([n for n, m in (("outer", cooling_mode),
                                       ("bore", bore_mode))
                        if (n == "outer" and m == "robotics")
                        or (n == "bore" and m == "still")])
    _iterating = _outlet_iterating + _wall_iterating
    if _ef_specs and not _wall_iterating:
        # Can't happen today (the end faces only come with the robotics housing)
        # but the loop must be driven by whatever moves, not by a mode name.
        _wall_iterating = ["end_faces"]
        _iterating = _iterating + _wall_iterating
    # The SEED is every watt the map carries, mechanical heat included: a coolant
    # outlet estimated from the electromagnetic loss alone would start the outlet
    # iteration below the temperature the stream actually reaches.  It is only a
    # seed — the passes below replace it with what each surface measured — but a
    # seed that ignores a source is a pass wasted.
    _p_seed = P_loss_total + _p_mech_in
    _share = (_p_seed / len(_outlet_iterating)) if _outlet_iterating else 0.0
    p_outer_est = _share if "outer" in _outlet_iterating else _p_seed
    p_bore_est = _share if "bore" in _outlet_iterating else 0.0
    # THE WALL SEED is the answer this mode replaces: the machine's own losses
    # leaving through everything the mode exposes — the housing cylinder and the
    # axial faces — at the flat NATURAL_CONVECTION_H floor, T_wall = T_∞ +
    # P/(h·A).  Deliberately that number and not a guess: it is what the `air`
    # mode at v = 0 would have said about this machine, so the first pass starts
    # from the old model and the iteration walks it to the new one.  It is only a
    # seed — the answer is a fixed point and does not depend on it — so it is
    # capped at 200 K over ambient rather than made cleverer: a machine whose
    # only real door is a mount would otherwise seed the correlation with a wall
    # nobody's machine reaches.
    _area_seed = (2.0 * math.pi * max(R_house, 1e-4) * max(L, 1e-3)
                  + sum(float(s["area_face_m2"]) * _ef_n for s in _ef_specs))
    from motor_ai_sim.simulation.cooling_models import (
        NATURAL_CONVECTION_H as _NAT_H)
    _t_wall_seed = float(ambient_temp) + min(
        _p_seed / max(_NAT_H * _area_seed, 1e-9), 200.0)
    _t_wall: Dict[str, float] = {"outer": _t_wall_seed, "bore": _t_wall_seed}
    for _s in _ef_specs:
        _t_wall[_s["name"]] = _t_wall_seed

    # 6. steady thermal solve — drop ALL air (outer + gap + slip band); the rotor
    # is reconnected to the stator by an explicit gap conductance bridge.
    #
    # Up to FOUR passes when a coolant outlet is in play (see `_iterating`): the
    # sink is the mean film temperature (T_in + T_out)/2 and T_out = T_in +
    # P/(ṁ·cp), where P is the heat that surface actually removes — a number only
    # the conduction solve can produce.  The loss map above is NOT re-fetched:
    # the losses do not depend on the coolant's outlet temperature at fixed
    # copper temperature.
    from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal
    MAX_PASSES = 4
    OUTLET_TOL_C = 0.5
    # The passes the bar will NAME (it does not lengthen for them — they share
    # one step): a machine with no coolant outlet solves exactly once.
    _n_passes = MAX_PASSES if _iterating else 1
    _report(0, phase="conduction pass 1/%d" % _n_passes)
    _t_solve = time.time()
    th: Dict[str, Any] = {}
    cooling = bore_cooling = None
    gap = None
    passes = 0
    _ef_reports: Dict[str, Dict[str, Any]] = {}
    for _p in range(MAX_PASSES):
        passes = _p + 1
        h_eff, t_sink, cooling = _cooling_bc(
            mode=cooling_mode, t_ambient_c=ambient_temp,
            air_speed_mps=air_speed_mps, fluid=fluid,
            fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
            p_loss_w=p_outer_est, r_housing_m=R_house, length_m=L,
            h_manual=h_conv, emissivity=emissivity,
            t_wall_c=_t_wall["outer"])
        h_bore, t_bore_sink, bore_cooling = _bore_bc(
            mode=bore_mode, t_ambient_c=ambient_temp,
            air_speed_mps=bore_air_speed_mps, fluid=bore_fluid,
            fluid_temp_in_c=bore_fluid_temp_in_c, flow_lpm=bore_flow_lpm,
            r_bore_m=r_bore_m, rpm=rpm, length_m=L, p_bore_w=p_bore_est,
            emissivity=emissivity, t_wall_c=_t_wall["bore"])

        # THE END FACES, at this pass's wall temperatures.  Rebuilt every pass
        # rather than once: G = h_total(ΔT)·A·n_faces, and h_total is the whole
        # of what moves between passes.
        _ef_sinks: List[Dict[str, Any]] = []
        for _s in _ef_specs:
            _rep = _cm3.end_face_still(
                t_wall_c=_t_wall[_s["name"]], t_ambient_c=ambient_temp,
                area_m2=_s["area_face_m2"], char_len_m=_s["char_len_m"],
                emissivity=emissivity, orientation="vertical",
                n_faces=_ef_n, name=_s["label"])
            _ef_reports[_s["name"]] = _rep
            if float(_rep["G_W_per_K"]) > 0.0 and _s["has_elements"]:
                _ef_sinks.append({"name": _s["name"], "tags": list(_s["tags"]),
                                  "G_W_per_K": float(_rep["G_W_per_K"]),
                                  "t_sink_c": float(ambient_temp),
                                  # whole-machine G on a 1/sym wedge, as always
                                  "symmetry_mult": sym})
        _sinks_pass = list(_sinks) + _ef_sinks

        if gap is None:
            # The air gap, once.  Properties at a STATED gap temperature: the
            # mean of the coolant sink and the copper temperature the losses
            # were evaluated at.  It is an estimate — but a reproducible one that
            # /coupled drives to self-consistency, because coil_temp_c is
            # exactly what that loop solves for.  Held fixed across the outlet
            # passes: the sink moves by a few kelvin and k_eff by well under a
            # percent, so re-deriving it would only make the loop non-monotone.
            from motor_ai_sim.simulation.cooling_models import taylor_couette_gap
            gap = taylor_couette_gap(
                rpm=rpm, r_rotor_m=rotor_outer_m, r_bore_m=stator_inner_m,
                t_gap_c=0.5 * (float(t_sink) + float(coil_temp_c)))
            gap_k_eff = float(gap["k_eff"])
            # The rotor's POCKET air is still air, but it is not the gap: it is
            # enclosed and turns with the rotor, so there is no Taylor–Couette
            # enhancement to apply to it — plain conduction at the same stated
            # gap temperature.  `gap["k_air"]` is exactly that number, already
            # evaluated there, so the two cannot drift apart.
            k_pocket = float(gap.get("k_air") or 0.026)

            # per-element conductivity, now that the gap value exists
            k_elem = _np.full(tris.shape[0], gap_k_eff)   # default = gap air
            k_rad_elem = k_elem.copy()
            k_elem[is_steel] = k_steel; k_rad_elem[is_steel] = k_steel
            k_elem[is_mag] = k_mag; k_rad_elem[is_mag] = k_mag
            k_elem[tags == DOM_SHAFT] = k_shaft
            k_rad_elem[tags == DOM_SHAFT] = k_shaft
            # THE WINDING, and whether the liner is still lumped into it.
            # `slot_k_eff` is the bulk transverse winding k with the liner's
            # resistance folded in as a series film — right when the liner is
            # sub-mesh and invisible, DOUBLE-COUNTED the moment the liner is a
            # meshed domain of its own with its own k.  So: liner elements
            # present → the coil carries the bare winding value and the liner
            # carries the liner; none present → the old lumped value, unchanged.
            winding_lumps_liner = not bool(is_liner.any())
            # Are the coil elements INDIVIDUAL CONDUCTORS (enamel / wire coating
            # meshed between them) or one homogenised winding block?  A bulk
            # transverse k (~0.13 W/m·K) is the equivalent of copper + enamel +
            # varnish averaged over a block; giving it to the copper strips of a
            # wire-resolved mesh — 9 mm wide, 0.5 mm thick, with the enamel and
            # the fill already meshed around them — stacks the insulation twice
            # and turns the winding into a heater in a thermos (measured live
            # 2026-09-07: 860 °C copper under a 64 °C water jacket, user: "какая-
            # то хрень").  Wires carry the conductor's own k from the library.
            winding_is_wires = bool(is_enamel.any() or is_fill.any())
            if winding_is_wires:
                k_coil = _thermal_k_any(mats.get("slot"), 385.0)
            else:
                k_coil = slot_k_eff if winding_lumps_liner else slot_k_used
            k_elem[is_coil] = k_coil
            k_rad_elem[is_coil] = k_coil
            q_elem[is_coil] = q_cu             # copper loss density (overwrites eddy)

            # THE NAMED AIR.  Each of these carries its own conductivity and no
            # heat source: none of them is lossy (σ = 0 in the insulations,
            # windage is not a magnetic solve), so whatever the EM map wrote
            # there is zeroed rather than conducted as if it were real.
            k_elem[is_liner] = k_liner; k_rad_elem[is_liner] = k_liner
            k_elem[is_enamel] = k_enamel; k_rad_elem[is_enamel] = k_enamel
            k_elem[is_fill] = k_fill; k_rad_elem[is_fill] = k_fill
            k_elem[is_gap_air] = gap_k_eff; k_rad_elem[is_gap_air] = gap_k_eff
            k_elem[is_pocket] = k_pocket; k_rad_elem[is_pocket] = k_pocket
            q_elem[is_liner | is_enamel | is_fill | is_pocket] = 0.0
            gap_k_bridge = gap_k_eff
            if sleeve_info is not None:
                if not sleeve_lumped:
                    k_elem[is_sleeve] = k_sl_f       # hoop = along the fibres
                    k_rad_elem[is_sleeve] = k_sl_r   # radial = through-thickness
                else:
                    # No elements to give the tensor to (a mesh path that never
                    # built the ring).  Put the sleeve's radial resistance in
                    # SERIES with the gap bridge instead — same physics, one
                    # dimension coarser — by conducting through an equivalent
                    # gap conductivity.  Stated in the payload as `model`.
                    _r_sl = 0.5 * (rotor_iron_outer_m + rotor_outer_m)
                    _delta = max(stator_inner_m - rotor_outer_m, 1e-6)
                    _g_gap = gap_k_eff * 2.0 * _np.pi * _r_sl / _delta
                    _g_sl = k_sl_r * 2.0 * _np.pi * _r_sl / max(sleeve_t_m, 1e-6)
                    _g_ser = 1.0 / (1.0 / _g_gap + 1.0 / _g_sl)
                    gap_k_bridge = _g_ser * _delta / (2.0 * _np.pi * _r_sl)

        if passes > 1:
            if _outlet_iterating:
                _phase = ("conduction pass %d/%d — outlet %.1f °C"
                          % (passes, _n_passes,
                             float((bore_cooling if "bore" in _outlet_iterating
                                    else cooling).get("t_out_c") or 0.0)))
            else:
                # A still-air machine has no outlet to report; what moves between
                # its passes is the WALL the film is evaluated at.
                _phase = ("conduction pass %d/%d — housing wall %.1f °C"
                          % (passes, _n_passes, float(_t_wall["outer"])))
            _report(0, phase=_phase)
        try:
            th = solve_steady_thermal(
                verts.T, tris.T, tags, k_elem, q_elem,
                drop_tags=DROP_TAGS,
                r_housing_m=R_house, rotor_outer_m=rotor_outer_m,
                stator_inner_m=stator_inner_m,
                gap_k=float(gap_k_bridge), length_m=L,
                k_radial_elem=k_rad_elem,
                # The gap air is a meshed domain now, so the rotor is not an
                # island: what is left at the slip radius is the sliding band's
                # non-conforming node pair, tied explicitly so the heat crossing
                # the gap can be MEASURED there.
                slip_r_m=float(slip_r_m), coil_mask=is_coil,
                # Still passed: a mesh that resolves no slot air at all (a
                # coarse preview, a machine whose liner is thinner than the
                # element floor) still leaves the coils as islands, and they are
                # bridged through the LINER, not through gap air.  With the slot
                # meshed there are no such islands and this does nothing.
                slot_ins_k=float(k_liner), slot_ins_d_m=float(t_liner) * 1e-3,
                surfaces=[{"name": "outer", "h": float(h_eff),
                           "t_sink": float(t_sink)},
                          {"name": "bore", "h": float(h_bore),
                           "t_sink": float(t_bore_sink)}],
                # The exposed shaft ends, the mount and the axial end faces — all
                # lumped conductances on the elements they act on, not facet
                # films (every one of those surfaces is out along the axis, so
                # this cross-section has no facets for them).
                volume_sinks=_sinks_pass)
        except ValueError as exc:
            # A cross-section the conduction solve cannot stand up (no solid
            # elements left, a disconnected island with no housing) is NAMED,
            # not defaulted — the client-facing rule this project runs on.
            raise HTTPException(status_code=422,
                                detail={"error": str(exc),
                                        "invalid_parameters": []})
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("steady thermal solve failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")

        _by_name = {s["name"]: s for s in (th.get("surfaces") or [])}
        p_outer_new = float(_by_name.get("outer", {}).get("heat_removed_W", 0.0)) * sym
        p_bore_new = float(_by_name.get("bore", {}).get("heat_removed_W", 0.0)) * sym
        if not _iterating:
            break
        # Convergence on the OUTLET TEMPERATURE, not on the heat: 0.5 K is the
        # resolution anybody reads a coolant temperature at, and chasing the
        # watts to the same relative tolerance would cost passes that change no
        # number in the answer.  ΔT_out = ΔP/(ṁ·cp) exactly, so the next pass's
        # outlet shift is known without building the boundary condition again.
        def _dt_out(rep, p_old, p_new):
            mc = float(rep.get("m_dot_kg_s") or 0.0) * _fluid_props(
                rep.get("fluid") or "air").cp
            return abs(p_new - p_old) / mc if mc > 1e-9 else 0.0

        _d = 0.0
        if "outer" in _outlet_iterating:
            _d = max(_d, _dt_out(cooling, p_outer_est, p_outer_new))
        if "bore" in _outlet_iterating:
            _d = max(_d, _dt_out(bore_cooling, p_bore_est, p_bore_new))
        p_outer_est, p_bore_est = p_outer_new, p_bore_new
        # …and on the WALL TEMPERATURE for every film whose coefficient depends
        # on it, to the SAME half a kelvin and for the same reason: h_total moves
        # as roughly ΔT^0.15 there, so half a kelvin of wall is a coefficient
        # nobody can read the difference of.  Each wall is that surface's or that
        # sink's OWN area-mean temperature, measured on the facets / elements the
        # film acts on — never the global maximum, which on a machine whose heat
        # leaves through its mount is tens of kelvin too hot and therefore too
        # cooled.
        if _wall_iterating:
            _sk_by_name = {s.get("name"): s for s in (th.get("sinks") or [])}

            def _wall_move(key: str, t_new) -> float:
                if t_new is None:
                    return 0.0
                _prev = _t_wall.get(key)
                _t_wall[key] = float(t_new)
                return abs(float(t_new) - float(_prev)) if _prev is not None else 0.0

            if "outer" in _wall_iterating:
                _d = max(_d, _wall_move(
                    "outer", _by_name.get("outer", {}).get("t_mean_c")))
            if "bore" in _wall_iterating:
                _d = max(_d, _wall_move(
                    "bore", _by_name.get("bore", {}).get("t_mean_c")))
            for _s in _ef_specs:
                _d = max(_d, _wall_move(
                    _s["name"],
                    (_sk_by_name.get(_s["name"]) or {}).get("t_mean_c")))
        if _d < OUTLET_TOL_C:
            break

    # Re-state the surfaces with the heat they ACTUALLY removed and the areas the
    # solver measured, so what the payload reports is the converged boundary
    # condition rather than the last guess that produced it.  Rebuilding the BC
    # (no solve — these are closed-form) keeps the reported triple
    # (T_in, T_out, T_sink) self-consistent with `heat_removed_W`; it can differ
    # from the sink the last conduction pass actually used by at most the
    # convergence tolerance, which is why that tolerance is stated.
    _by_name = {s["name"]: s for s in (th.get("surfaces") or [])}
    _outer_w = float(_by_name.get("outer", {}).get("heat_removed_W", 0.0)) * sym
    _bore_w = float(_by_name.get("bore", {}).get("heat_removed_W", 0.0)) * sym
    if _iterating:
        _, _, cooling = _cooling_bc(
            mode=cooling_mode, t_ambient_c=ambient_temp,
            air_speed_mps=air_speed_mps, fluid=fluid,
            fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
            p_loss_w=_outer_w, r_housing_m=R_house, length_m=L, h_manual=h_conv,
            emissivity=emissivity, t_wall_c=_t_wall["outer"])
        _, _, bore_cooling = _bore_bc(
            mode=bore_mode, t_ambient_c=ambient_temp,
            air_speed_mps=bore_air_speed_mps, fluid=bore_fluid,
            fluid_temp_in_c=bore_fluid_temp_in_c, flow_lpm=bore_flow_lpm,
            r_bore_m=r_bore_m, rpm=rpm, length_m=L, p_bore_w=_bore_w,
            emissivity=emissivity, t_wall_c=_t_wall["bore"])
        # The end-face films, likewise re-evaluated at the wall the solve came
        # back with, so `cooling.end_faces` reports the converged conductance and
        # not the last guess that produced it.  The WATTS below are still the
        # solver's own, never this closed form's.
        for _s in _ef_specs:
            _ef_reports[_s["name"]] = _cm3.end_face_still(
                t_wall_c=_t_wall[_s["name"]], t_ambient_c=ambient_temp,
                area_m2=_s["area_face_m2"], char_len_m=_s["char_len_m"],
                emissivity=emissivity, orientation="vertical",
                n_faces=_ef_n, name=_s["label"])
    _gap_w = float(th.get("gap_heat_W", 0.0)) * sym
    _bridge_w = float(th.get("slot_bridge_W", 0.0)) * sym
    _coil_w = float(th.get("coil_boundary_W", 0.0)) * sym
    # `slot_liner_W` keeps its NAME and changes its measurement, because the
    # thing it measured no longer exists: with the liner and the wire coating
    # meshed there are no coil islands and no lumped liner link, so the number
    # is now the net conductive heat leaving the winding elements — the same
    # question ("does the copper's heat get out through its insulation") asked
    # of the real elements.  On a mesh too coarse to resolve any slot air the
    # islands come back and the bridge answers it, which is why both are here.
    _slot_w = _coil_w if abs(_coil_w) > 0.0 else _bridge_w
    _gen_w = float(th.get("q_generated_W", 0.0)) * sym
    cooling["heat_removed_W"] = round(_outer_w, 2)
    cooling["area_m2"] = round(float(_by_name.get("outer", {})
                                     .get("area_m2", 0.0)) * sym, 6)
    bore_cooling["heat_removed_W"] = round(_bore_w, 2)
    bore_cooling["area_m2"] = round(float(_by_name.get("bore", {})
                                          .get("area_m2", 0.0)) * sym, 6)
    bore_cooling["r_bore_mm"] = round(r_bore_m * 1e3, 3)

    # THE SHAFT ENDS, as the solve measured them.  `heat_removed_W` comes back
    # from the solver in the same (wedge) watts every other flow does, so it is
    # scaled by `sym` here like the rest — and the identity that makes it
    # checkable is `shaft_ends_W == G·(t_shaft_mean_c − ambient)`, both of which
    # are in the payload.
    _sink_by_name = {s.get("name"): s for s in (th.get("sinks") or [])}
    _se = _sink_by_name.get("shaft_ends")
    _shaft_w = float((_se or {}).get("heat_removed_W") or 0.0) * sym
    _per_side = dict(_shaft_path.get("per_side") or {})
    if _shaft_len_m > 0.0:
        shaft_ends = {
            "mode": "rotating shaft in air",
            "length_each_side_mm": round(_shaft_len_m * 1e3, 2),
            "diameter_mm": round(_shaft_d_out * 1e3, 3),
            "diameter_source": _shaft_d_src,
            "bore_diameter_mm": round(2.0 * max(r_bore_m, 0.0) * 1e3, 3),
            "sides": int(_shaft_path.get("n_sides") or 0),
            "h_conv": round(float(_shaft_path.get("h") or 0.0), 2),
            "re_omega": float(_shaft_path.get("re_omega") or 0.0),
            "nu": round(float(_shaft_path.get("nu") or 0.0), 2),
            "regime": _shaft_path.get("regime"),
            "k_shaft": round(float(k_shaft), 1),
            "G_W_per_K": round(float(_shaft_path.get("G_W_per_K") or 0.0), 5),
            "t_sink_c": round(float(ambient_temp), 2),
            "t_shaft_mean_c": (None if (_se or {}).get("t_mean_c") is None
                               else round(float(_se["t_mean_c"]), 2)),
            "heat_removed_W": round(_shaft_w, 3),
            "fin_efficiency": round(float(_per_side.get("efficiency") or 0.0), 4),
            "mL": round(float(_per_side.get("mL") or 0.0), 4),
            "n_elements": int((_se or {}).get("n_elements") or 0),
            "note": str(_shaft_path.get("note") or ""),
        }
    else:
        # "there is nothing sticking out" is a statement about the machine, not
        # a missing key — same rule the bore's `mode: none` follows.
        shaft_ends = {
            "mode": "off", "length_each_side_mm": 0.0, "diameter_mm": 0.0,
            "sides": int(shaft_ext_sides), "h_conv": 0.0, "re_omega": 0.0,
            "G_W_per_K": 0.0, "t_sink_c": round(float(ambient_temp), 2),
            "t_shaft_mean_c": None, "heat_removed_W": 0.0,
            "fin_efficiency": 0.0,
            "note": ("no shaft length outside the housing: the rotor's end "
                     "faces and the end windings turn inside the closed "
                     "housing and have nowhere else to send their heat, so the "
                     "rotor's only ways out are the air gap and the bore"),
        }

    # ── THE OPEN FRAME's two paths, as the solve measured them ───────────────
    # Same shape and same bookkeeping as `shaft_ends` above: `heat_removed_W`
    # comes back in wedge watts and is scaled by `sym`, and the identity
    # `end_windings_W == G·(t_winding_mean_c − ambient)` is checkable from the
    # payload because both numbers are in it.
    _ew_sink = _sink_by_name.get("end_windings")
    _ch_sink = _sink_by_name.get("slot_channels")
    _ew_w = float((_ew_sink or {}).get("heat_removed_W") or 0.0) * sym
    _ch_w = float((_ch_sink or {}).get("heat_removed_W") or 0.0) * sym
    if frame == "open":
        end_windings = {
            "mode": "end turns in the airflow",
            "air_speed_mps": round(float(_open_v), 2),
            "air_speed_source": _open_v_src,
            "k_end": round(float(_k_end), 4),
            "k_end_source": _k_end_src,
            "n_coils": int(_ew_path.get("n_coils") or 0),
            "n_coils_source": _n_coils_note,
            "n_sides": int(_ew_path.get("n_sides") or 0),
            "end_turn_length_mm": _ew_path.get("end_turn_length_mm"),
            "bundle_thickness_mm": _ew_path.get("bar_thickness_mm"),
            "bundle_width_mm": _ew_path.get("bar_width_mm"),
            "perimeter_mm": round(float(_ew_path.get("perimeter_m") or 0.0) * 1e3, 3),
            "d_equiv_mm": round(float(_ew_path.get("d_equiv_m") or 0.0) * 1e3, 3),
            "area_m2": round(float(_ew_path.get("area_m2") or 0.0), 8),
            "h_conv": round(float(_ew_path.get("h") or 0.0), 2),
            "re": float(_ew_path.get("re") or 0.0),
            "nu": round(float(_ew_path.get("nu") or 0.0), 2),
            "regime": _ew_path.get("regime"),
            "fin_efficiency": 1.0,
            "G_W_per_K": round(float(_ew_path.get("G_W_per_K") or 0.0), 5),
            "t_sink_c": round(float(ambient_temp), 2),
            "t_winding_mean_c": (None if (_ew_sink or {}).get("t_mean_c") is None
                                 else round(float(_ew_sink["t_mean_c"]), 2)),
            "heat_removed_W": round(_ew_w, 3),
            "n_elements": int((_ew_sink or {}).get("n_elements") or 0),
            "note": str(_ew_path.get("note") or ""),
        }
        slot_channels = {
            "mode": "ventilated slot channels",
            "air_speed_mps": round(float(_open_v), 2),
            "air_speed_source": _open_v_src,
            "n_channels": int(_ch_path.get("n_channels") or 0),
            "wetted_perimeter_mm": round(
                float(_ch_path.get("wetted_perimeter_m") or 0.0) * 1e3, 3),
            "wetted_perimeter_per_slot_mm": (
                round(float(_ch_path.get("wetted_perimeter_m") or 0.0) * 1e3
                      / max(int(num_slots), 1), 3)),
            "cross_section_mm2": round(
                float(_ch_path.get("cross_section_m2") or 0.0) * 1e6, 4),
            "hydraulic_diameter_mm": round(
                float(_ch_path.get("hydraulic_diameter_m") or 0.0) * 1e3, 4),
            "area_m2": round(float(_ch_path.get("area_m2") or 0.0), 8),
            "h_conv": round(float(_ch_path.get("h") or 0.0), 2),
            "re": float(_ch_path.get("re") or 0.0),
            "nu": round(float(_ch_path.get("nu") or 0.0), 3),
            "regime": _ch_path.get("regime"),
            "G_W_per_K": round(float(_ch_path.get("G_W_per_K") or 0.0), 5),
            "t_sink_c": round(float(ambient_temp), 2),
            "t_air_mean_c": (None if (_ch_sink or {}).get("t_mean_c") is None
                             else round(float(_ch_sink["t_mean_c"]), 2)),
            "heat_removed_W": round(_ch_w, 3),
            "n_elements": int((_ch_sink or {}).get("n_elements") or 0),
            "note": str(_ch_path.get("note") or ""),
        }
        if _open_notes:
            end_windings["notes"] = list(_open_notes)
            slot_channels["notes"] = list(_open_notes)
    else:
        # "this machine has a housing" is a STATEMENT, not a missing key — the
        # same rule the bore's `mode: none` and the shaft's `mode: off` follow.
        _housed_note = (
            "housed machine: the end windings and the slot air are inside a "
            "closed housing, turning in their own air, so whatever they hand to "
            "it comes straight back through the housing — there is no extra "
            "path and modelling one would flatter the design.  Set frame=open "
            "for a machine with no housing (tooth blocks between end plates, "
            "end turns in the airflow).")
        end_windings = {"mode": "housed", "G_W_per_K": 0.0,
                        "heat_removed_W": 0.0, "h_conv": 0.0,
                        "t_sink_c": round(float(ambient_temp), 2),
                        "note": _housed_note}
        slot_channels = {"mode": "housed", "G_W_per_K": 0.0,
                         "heat_removed_W": 0.0, "h_conv": 0.0,
                         "t_sink_c": round(float(ambient_temp), 2),
                         "note": _housed_note}

    # ── THE MOUNT, as the solve measured it (2026-09-14) ────────────────────
    # Same shape and the same bookkeeping as `shaft_ends`: `heat_removed_W` comes
    # back in wedge watts and is scaled by `sym`, and the identity
    # `mount_W == G·(t_housing_mean_c − t_mount)` is checkable from the payload
    # because both numbers are in it.
    _mt_sink = _sink_by_name.get("mount")
    _mount_w = float((_mt_sink or {}).get("heat_removed_W") or 0.0) * sym
    mount = {
        "mode": str(_mount_path.get("mode") or "off"),
        "G_W_per_K": round(float(_mount_path.get("G_W_per_K") or 0.0), 5),
        "t_sink_c": round(float(t_mount_c), 2),
        "t_sink_source": ("ambient (mount_temp_c not given)"
                          if mount_temp_c is None else "given"),
        "t_housing_mean_c": (None if (_mt_sink or {}).get("t_mean_c") is None
                             else round(float(_mt_sink["t_mean_c"]), 2)),
        "heat_removed_W": round(_mount_w, 3),
        "n_elements": int((_mt_sink or {}).get("n_elements") or 0),
        "attached_to": "stator",
        "note": str(_mount_path.get("note") or ""),
    }

    # ── THE AXIAL END FACES, as the solve measured them (2026-09-14) ────────
    # One block per node, always present and always the same shape: `mode: off`
    # on a machine whose ends are not exposed is an ANSWER, the same rule the
    # bore's `mode: none` and the shaft's `mode: off` follow.  `heat_removed_W`
    # is the SOLVER's (wedge watts × sym), never the closed form's, so
    # `G·(t_mean_c − t_sink_c)` stays a check and not a restatement.
    _EF_NODES = (("winding", "end_face_winding"), ("stator", "end_face_stator"),
                 ("rotor", "end_face_rotor"), ("magnet", "end_face_magnet"))
    end_face_block: Dict[str, Any] = {}
    _ef_w_total = 0.0
    _ef_w_by_node: Dict[str, float] = {}
    for _node, _key in _EF_NODES:
        _rep = _ef_reports.get(_key)
        _sk = _sink_by_name.get(_key)
        _w = float((_sk or {}).get("heat_removed_W") or 0.0) * sym
        _ef_w_total += _w
        _ef_w_by_node[_node] = _w
        if _rep is None:
            end_face_block[_node] = {
                "mode": "off", "area_m2": 0.0, "area_per_face_m2": 0.0,
                "h_conv": 0.0, "h_rad": 0.0, "h_total": 0.0,
                "n_faces": 0, "G_W_per_K": 0.0,
                "t_sink_c": round(float(ambient_temp), 2),
                "t_mean_c": None, "heat_removed_W": 0.0,
                "emissivity": round(float(emissivity), 3),
                "note": ("the axial end faces are not modelled on this machine: "
                         "they are part of cooling_mode='robotics' (the end "
                         "turns stand proud of the core and the core / magnet "
                         "end faces are uncovered), and on a housed machine "
                         "whatever they hand to the air inside the housing comes "
                         "straight back through it")}
            continue
        end_face_block[_node] = {
            # "still" only when a sink was actually built for it: a part this
            # cross-section does not carry (a rotor whose core is excluded from
            # the model, a machine with no magnets meshed) has an AREA of zero
            # and no elements to spread a conductance over, and saying "still"
            # there would report a path nothing is on.
            "mode": (str(_rep.get("mode") or "off") if _sk is not None
                     else "not modelled — this cross-section carries no "
                          "elements of this part"),
            "area_m2": round(float(_rep.get("area_total_m2") or 0.0), 8),
            "area_per_face_m2": round(float(_rep.get("area_m2") or 0.0), 8),
            "char_len_mm": round(float(_rep.get("char_len_m") or 0.0) * 1e3, 3),
            "orientation": _rep.get("orientation"),
            "h_conv": round(float(_rep.get("h_conv") or 0.0), 3),
            "h_rad": round(float(_rep.get("h_rad") or 0.0), 3),
            "h_total": round(float(_rep.get("h_total") or 0.0), 3),
            "ra": float(_rep.get("ra") or 0.0),
            "nu": round(float(_rep.get("nu") or 0.0), 3),
            "regime": _rep.get("regime"),
            "n_faces": int(_rep.get("n_faces") or 0),
            "emissivity": round(float(_rep.get("emissivity") or 0.0), 3),
            "G_W_per_K": round(float(_rep.get("G_W_per_K") or 0.0), 5),
            "t_sink_c": round(float(ambient_temp), 2),
            "t_wall_c": round(float(_rep.get("t_wall_c") or 0.0), 2),
            "t_mean_c": (None if (_sk or {}).get("t_mean_c") is None
                         else round(float(_sk["t_mean_c"]), 2)),
            "heat_removed_W": round(_w, 3),
            "n_elements": int((_sk or {}).get("n_elements") or 0),
            "area_source": ("end_winding_area on this run's own k_end "
                            "(ℓ_end = (k_end − 1)·L/2, shielded perimeter 2t + w)"
                            if _node == "winding" else
                            "this mesh's own section area for the part × "
                            "symmetry"),
            "note": str(_rep.get("note") or ""),
        }
    end_faces_block = {
        "mode": ("still" if _ef_specs else "off"),
        "sides": (_ef_n if _ef_specs else 0),
        "emissivity": round(float(emissivity), 3),
        "heat_removed_W": round(_ef_w_total, 3),
        "G_W_per_K": round(sum(float(end_face_block[n].get("G_W_per_K") or 0.0)
                               for n, _ in _EF_NODES), 5),
        **{n: end_face_block[n] for n, _ in _EF_NODES},
    }
    if _ef_notes:
        end_faces_block["notes"] = list(_ef_notes)
    if _ef_specs:
        end_faces_block["k_end"] = round(float(_ef_k_end), 4)
        end_faces_block["k_end_source"] = _ef_k_end_src

    _resid = (_gen_w - _outer_w - _bore_w - _shaft_w - _ew_w - _ch_w
              - _mount_w - _ef_w_total)

    # ── WHERE THE ROTOR'S HEAT GOES: out through the gap, or in through the
    #    shaft (2026-09-10) ───────────────────────────────────────────────────
    #
    # User: *"в термоанализе ещё нужно считать два числа: сколько тепла от
    # ротора уходит через внешний диаметр, а сколько через внутренний"*, and
    # then plainly: *"то есть через зазор и через вал"*.
    #
    # Every watt made inside the slip radius has exactly three ways out of this
    # cross-section — across the gap into the stator, off the bore surface into
    # whatever turns in it, and axially down the exposed shaft stubs — so the
    # three of them must add back up to what the rotor makes.  The numbers were
    # all in the budget already, one per line, with nothing saying they belong
    # to one balance or what share each carries; that share is the design
    # question (does the bore air earn its impeller, or is the band handing
    # everything to the stator anyway?).
    #
    # `closure_W` is the check, not a decoration: it is the same slip-tie and
    # shaft-sink identity the note below describes, stated as a number.
    _rotor_w = float(th.get("q_rotor_W", 0.0)) * sym

    def _share(w: float) -> Optional[float]:
        return (round(100.0 * w / _rotor_w, 1) if abs(_rotor_w) > 1e-9
                else None)

    # TWO NUMBERS, both of them 2-D (user 2026-09-10: "делай только в двумерном
    # варианте пока").  The gap and the bore are surface integrals on the SAME
    # solved cross-section — one is the outer diameter, the other the inner —
    # so they are directly comparable and the pair is the answer.  The shaft
    # stubs are an AXIAL path bolted onto a plane model, a lumped conductance
    # out of the page rather than a facet of this section, so it is reported
    # beside them and is not folded into either: adding it to the bore would
    # inflate "through the shaft" with a term the 2-D solve never drew.
    rotor_heat_split = {
        "dimensionality": "2D",
        "rotor_W": round(_rotor_w, 3),
        # OUT, through the outer diameter: across the air gap into the stator.
        "gap_W": round(_gap_w, 3),
        "gap_pct": _share(_gap_w),
        # IN, through the inner diameter: the bore surface.
        "bore_W": round(_bore_w, 3),
        "bore_pct": _share(_bore_w),
        # …and the out-of-plane term, named rather than mixed in.  0 on a
        # machine with nothing sticking out of the housing, and 0 there is a
        # statement: there are no stubs to lose heat from.
        "axial_shaft_ends_W": round(_shaft_w, 3),
        "axial_shaft_ends_pct": _share(_shaft_w),
        # …and the rotor's OTHER out-of-plane path (2026-09-14): the rotor core's
        # and the magnets' own end faces, when the machine's ends are exposed.
        # 0 on every housed machine, and 0 there is a statement.
        "axial_end_faces_W": round(_ef_w_by_node.get("rotor", 0.0)
                                   + _ef_w_by_node.get("magnet", 0.0), 3),
        "axial_end_faces_pct": _share(_ef_w_by_node.get("rotor", 0.0)
                                      + _ef_w_by_node.get("magnet", 0.0)),
        # The identity still runs over all of them — a closure that ignored an
        # axial term would not close on a machine that has one.
        "closure_W": round(_rotor_w - _gap_w - _bore_w - _shaft_w
                           - _ef_w_by_node.get("rotor", 0.0)
                           - _ef_w_by_node.get("magnet", 0.0), 3),
        "note": ("what the rotor makes and where it goes, on the 2-D "
                 "cross-section: across the AIR GAP into the stator (the outer "
                 "diameter) and off the BORE surface into whatever turns in it "
                 "(the inner diameter).  Both are surface integrals on the same "
                 "solved section, so the two are directly comparable.  "
                 "axial_shaft_ends_W is a lumped OUT-OF-PLANE path down the "
                 "exposed shaft stubs — reported beside them, never folded into "
                 "either.  closure_W is what all three leave over against the "
                 "rotor's own generation: the slip-tie and shaft-sink identity, "
                 "as a number."),
    }

    # ── …AND WHERE THE STATOR'S HEAT GOES (2026-09-14) ──────────────────────
    # The mirror of `rotor_heat_split`, and it exists because the robotics mode
    # made the question a design decision rather than a detail: on a joint in
    # still air the housing hands the room ~3 W of 64 and the BOLTS take the
    # rest, so "how much leaves through the mount" is the number the flange is
    # designed from.  Everything the stator side makes (∫q dV outside the slip
    # radius) plus everything the rotor hands it across the gap has to leave
    # through the housing film, the mount, the stator-side end faces (the end
    # turns and the core's own end annulus) or — on an open frame — the end
    # windings and the slot channels, and `closure_W` is that identity as a
    # number.
    _stator_gen_w = _gen_w - _rotor_w
    _stator_in_w = _stator_gen_w + _gap_w
    _ef_stator_side_w = (_ef_w_by_node.get("winding", 0.0)
                         + _ef_w_by_node.get("stator", 0.0))

    def _share_s(w: float) -> Optional[float]:
        return (round(100.0 * w / _stator_in_w, 1) if abs(_stator_in_w) > 1e-9
                else None)

    stator_heat_split = {
        "dimensionality": "2D",
        # what the stator side MAKES (copper + stator iron + whatever mechanical
        # heat landed there), and what the rotor hands it across the gap
        "stator_W": round(_stator_gen_w, 3),
        "gap_in_W": round(_gap_w, 3),
        "total_in_W": round(_stator_in_w, 3),
        # …and the doors out
        "housing_W": round(_outer_w, 3),
        "housing_pct": _share_s(_outer_w),
        "mount_W": round(_mount_w, 3),
        "mount_pct": _share_s(_mount_w),
        "end_faces_W": round(_ef_stator_side_w, 3),
        "end_faces_pct": _share_s(_ef_stator_side_w),
        "end_windings_W": round(_ew_w, 3),
        "slot_channels_W": round(_ch_w, 3),
        "closure_W": round(_stator_in_w - _outer_w - _mount_w
                           - _ef_stator_side_w - _ew_w - _ch_w, 3),
        "note": ("what the stator side makes and where it goes: ∫q dV outside "
                 "the slip radius plus what crosses the AIR GAP from the rotor, "
                 "against the housing film, the bolted MOUNT (a lumped "
                 "out-of-plane conductance, not a facet of this section), the "
                 "stator-side axial END FACES (the end turns and the core's own "
                 "end annulus) and the open frame's two paths.  On a machine in "
                 "still air the mount is usually the answer and the housing a "
                 "few per cent of it.  closure_W is the identity, as a number."),
    }

    heat_budget = {
        "rotor_heat_split": rotor_heat_split,
        "stator_heat_split": stator_heat_split,
        "losses_W": round(_gen_w, 2),
        "housing_W": round(_outer_w, 2),
        # WHICH HALF of the housing's watts left as light (2026-09-14).  Only the
        # still-air film has two mechanisms; every other mode reports its whole
        # heat as convection and 0 W of radiation, which is what those models
        # say (`rotating_cylinder_h` states the same omission).  The split is
        # exact, not apportioned: both films act on the same area and the same
        # ΔT, so it is the ratio of the two coefficients.
        "housing_convection_W": round(float(
            cooling.get("convection_W", _outer_w) or 0.0), 3),
        "housing_radiation_W": round(float(
            cooling.get("radiation_W", 0.0) or 0.0), 3),
        "bore_W": round(_bore_w, 2),
        "gap_W": round(_gap_w, 2),
        # THE MOUNT (2026-09-14): the bolted flange, a lumped conductance to a
        # held temperature.  0 on a machine bolted to nothing — which is what
        # every answer before today assumed, and said nothing about.
        "mount_W": round(_mount_w, 3),
        # THE AXIAL END FACES (2026-09-14): the end turns, the core end faces and
        # the magnet ends of a machine whose ends are open, all four summed.  0
        # on a housed machine.
        "end_faces_W": round(_ef_w_total, 3),
        # The exposed shaft stubs — a rotor outflow like the bore, but along the
        # axis rather than across a facet of this cross-section.
        "shaft_ends_W": round(_shaft_w, 3),
        # ── THE OPEN FRAME's two paths (2026-09-09) ─────────────────────────
        # Both are 0 on a housed machine, and 0 there is a STATEMENT: the end
        # turns and the slot air are inside the housing.  On an open one they
        # are real outflows and they are part of the residual below, exactly
        # like the housing, the bore and the shaft ends — every watt that leaves
        # the model is on one of these lines.
        "end_windings_W": round(_ew_w, 3),
        "slot_channels_W": round(_ch_w, 3),
        "frame": frame,
        # ∫q dV inside the slip radius — what the ROTOR makes.  In steady state
        # it has to leave across the gap, through the bore or along the exposed
        # shaft ends and nowhere else, so
        # `gap_W + bore_W + shaft_ends_W == rotor_W` is the check on the slip
        # tie (and, since 2026-09-07, on the shaft sink).
        "rotor_W": round(float(th.get("q_rotor_W", 0.0)) * sym, 3),
        # coil → slot through the winding's own boundary (stator-internal; in
        # steady state it must come back equal to the copper loss)
        "slot_liner_W": round(_slot_w, 2),
        "coil_W": round(_coil_w, 2),
        "slot_bridge_W": round(_bridge_w, 2),
        "n_slip_ties": int(th.get("n_slip_ties", 0)),
        "n_islands_rotor": int(th.get("n_islands_rotor", 0)),
        "n_islands_stator": int(th.get("n_islands_stator", 0)),
        "residual_W": round(_resid, 3),
        "residual_pct": round(100.0 * abs(_resid) / max(abs(_gen_w), 1e-9), 3),
        "em_loss_total_W": round(P_loss_total, 2),
        # ── THE MECHANICAL WATTS, on their own budget lines (2026-09-08) ────
        # `losses_W` above already CONTAINS the two injected terms (they are
        # part of ∫q dV like every other source), so these say WHICH part of it
        # is mechanical — and, just as importantly, which mechanical watts this
        # cross-section could not take.  A term that is not modelled is named
        # with its number rather than dropped: an engineer can then decide to
        # open the shaft-ends path, and nobody reads a closed budget as a
        # complete one.
        "bearing_friction_W": round(
            _p_brg_in if _brg_modelled else 0.0, 3),
        "bearing_friction_not_modelled_W": round(_p_brg_dropped, 3),
        "windage_W": round(_p_wind_gap if _wind_modelled else 0.0, 4),
        "windage_not_modelled_W": round(_p_wind_dropped + _p_wind_faces, 4),
        "mech_loss_total_W": round(_p_brg_in + _p_wind_gap + _p_wind_faces, 3),
        "mech_loss_in_map_W": round(_p_mech_in, 3),
        "symmetry_mult": sym,
        "passes": passes,
        "note": ("losses_W is ∫q dV over the SOLVED sub-mesh (× the model's "
                 "symmetry), housing_W and bore_W are ∫h(T−T_sink)dA on their "
                 "surfaces (housing_convection_W + housing_radiation_W = "
                 "housing_W, and the radiation half is non-zero only in the "
                 "still-air mode), shaft_ends_W is G·(T_shaft − T_ambient) down "
                 "the exposed shaft stubs, mount_W is G·(T_stator − T_mount) "
                 "into the bolted flange and end_faces_W the same product on the "
                 "four AXIAL faces (both 0 unless they were asked for), "
                 "end_windings_W and slot_channels_W are "
                 "the same product on an OPEN frame's end turns and slot ducts "
                 "(both 0 on a housed machine), gap_W is what crosses the "
                 "slip-line tie "
                 "rotor→stator through the meshed gap air, and slot_liner_W is "
                 "∮q·n over the winding's own boundary — the last two are "
                 "INTERNAL, so neither is part of the residual.  The residual "
                 "is the conduction solve's own closure error.  losses_W = "
                 "em_loss_total_W + mech_loss_in_map_W up to the loss map's own "
                 "wedge/mesh integration; bearing friction at the shaft ends and "
                 "gap windage are inside it, and the two *_not_modelled_W lines "
                 "are mechanical watts this cross-section has no place for."),
    }
    if gap is not None:
        gap["k_bridge"] = round(float(gap_k_bridge), 4)

    # 7. per-component temperatures
    _report(1, phase="post-processing (components, flux)")
    Tn = _np.asarray(th["T_node"]); ts = _np.asarray(th["triangles"], int)
    tg = _np.asarray(th["cell_tags"], int)

    def _comp(mask):
        if not mask.any():
            return None
        nodes = _np.unique(ts[mask])
        return {"max": round(float(Tn[nodes].max()), 1), "avg": round(float(Tn[nodes].mean()), 1)}

    result = {
        "ok": True,
        "n_vertices": len(th["vertices"]), "n_triangles": len(th["triangles"]),
        "vertices": th["vertices"], "triangles": th["triangles"],
        "domain_per_tri": th["cell_tags"],
        "temperature_per_node": th["T_node"],            # °C
        "heat_flux_per_tri": th["flux_elem"],            # W/m² (vector)
        "flux_mag_per_tri": th["flux_mag_elem"],
        "grad_T_mag_per_tri": th.get("grad_mag_elem"),  # K/m — the bottleneck map
        "T_min": round(float(th["T_min"]), 1), "T_max": round(float(th["T_max"]), 1),
        "n_bridge_links": th.get("n_bridge_links"), "n_nonfinite": th.get("n_nonfinite"),
        "n_housing_facets": th.get("n_housing_facets"),
        "n_bore_facets": th.get("n_bore_facets"),
        # the symmetry wedge's cut facets, adiabatic by construction (2026-09-09)
        "n_cut_facets": th.get("n_cut_facets"),
        # How many sectors the solved wedge is of the machine, so the map's
        # tiler (`tileFullRing`) can draw the whole motor (user 2026-09-09:
        # "сделай тепловые поля на весь мотор, а не только на 1/4") — the
        # mesh preview always carried it, the field did not.
        "n_sectors": int(sym), "symmetry_mult": int(sym),
        "ambient_temp": float(ambient_temp), "h_conv": round(float(h_eff), 1),
        "t_sink_c": round(float(t_sink), 1),
        # THE cooling block: one shape per surface, the derived gap, the sleeve
        # and the watts that left through each path.  `cooling.outer` is the
        # surface the legacy top-level `h_conv` / `t_sink_c` describe.
        "cooling": {
            "outer": cooling,
            "inner": bore_cooling,
            # The rotor's THIRD path (2026-09-07): the shaft sticking out of the
            # housing on either side.  Not a `surface` — it leaves along the
            # axis, which this cross-section does not have — so it is reported
            # beside the two films rather than as one of them.
            "shaft_ends": shaft_ends,
            # THE BOLTED MOUNT (2026-09-14): on a machine in still air this is
            # not one path of five, it is THE path — and it is an INPUT, so the
            # block says what it was given, what it removed and what the housing
            # sat at while it did.  `mode: off` is an answer: this machine was
            # modelled as bolted to nothing.
            "mount": mount,
            # THE AXIAL END FACES (2026-09-14): the end turns, the two core end
            # faces and the magnet ends, each with its own film, area and watts.
            "end_faces": end_faces_block,
            # THE OPEN FRAME (2026-09-09): the two paths a machine with no
            # housing has and a housed one does not.  Reported beside
            # `shaft_ends` and for the same reason — they leave along the axis /
            # through a duct inside the mesh, so neither is a `surface`.  Both
            # carry `mode: 'housed'` on a housed machine rather than being
            # absent: "there is a housing" is an answer.
            "frame": frame,
            "end_windings": end_windings,
            "slot_channels": slot_channels,
            # The MECHANICAL heat (2026-09-08): what the bearings and the air
            # cost this machine at this speed, at which bearing temperature and
            # from where, and what this cross-section did with each watt.
            "mech_losses": mech_losses,
            "gap": gap,
            "sleeve": sleeve_info,
            "heat_budget": heat_budget,
            "passes": passes,
            "deprecated": _deprecated_notes,
        },
        "slot_k": round(float(slot_k_used), 3),            # winding bulk transverse k (auto unless slot_k>0 override)
        "slot_k_auto": round(float(slot_k_auto), 3),       # series-stack value (Cu + air gaps)
        "slot_fill": round(float(f_cu), 3),                # copper fill fraction in the slot
        "k_enamel": round(float(k_enamel), 3),             # wire-insulation (enamel) conductivity
        # Legacy top-level gap keys — kept so a client written against the old
        # payload keeps drawing; `cooling.gap` is where the full story is.
        "gap_k": gap["k_eff"], "gap_k_taylor": gap["k_eff"],
        "gap_Ta": gap["Ta"], "gap_Nu": gap["Nu"],
        "rpm": float(rpm),
        "slot_k_eff": round(float(slot_k_eff), 3),         # winding + liner series k
        "k_liner": round(float(k_liner), 3),               # slot-liner material conductivity
        "liner_material": mats.get("slot_insulation"),
        "k_steel": round(k_steel, 1), "k_magnet": round(k_mag, 1), "k_shaft": round(k_shaft, 1),
        # The parts, by the SAME tag table the Part tree colours from — so a row
        # the tree shows always has a temperature beside it and vice versa.  The
        # five new entries are the ones the user asked to see (2026-09-07): they
        # are solved domains, not decoration, and their own max/avg is the only
        # way to tell a liner that is doing its job from one that is not.
        "components": {
            "winding": _comp(tg == DOM_COIL),
            "magnet": _comp((tg == DOM_MAG_N) | (tg == DOM_MAG_S)),
            "stator": _comp(tg == DOM_STATOR),
            "rotor": _comp(tg == DOM_ROTOR),
            "shaft": _comp(tg == DOM_SHAFT),
            "sleeve": _comp(tg == DOM_SLEEVE),
            "liner": _comp(tg == DOM_SLOT_LINER),
            "enamel": _comp(tg == DOM_WIRE_ENAMEL),
            "slot_fill": _comp(tg == DOM_SLOT_FILL),
            "gap_air": _comp(tg == DOM_GAP_AIR),
            "pocket_air": _comp(tg == DOM_POCKET_AIR),
        },
        # tag -> name for every triangle in the payload, so the client labels a
        # domain without a second lookup table of its own (it used to fall back
        # to the EM vocabulary, in which all five of these are "air").
        "part_names": {str(k): v for k, v in PART_NAMES.items()},
        # WHICH k every non-metal domain was solved with, and where it came
        # from.  One short line in the panel, this dict behind it: an engineer
        # arguing with a winding temperature has to be able to see whether the
        # liner number is their datasheet or our default.
        "materials_used": {
            "liner": {"k": round(float(k_liner), 3),
                      "material": mats.get("slot_insulation"),
                      "source": ("library" if mats.get("slot_insulation")
                                 else "default"),
                      "n_elements": int((tg == DOM_SLOT_LINER).sum())},
            "enamel": {"k": round(float(k_enamel), 3),
                       "material": mats.get("wire_insulation"),
                       "source": ("library" if mats.get("wire_insulation")
                                  else "default"),
                       "n_elements": int((tg == DOM_WIRE_ENAMEL).sum())},
            "slot_fill": {"k": round(float(k_fill), 3),
                          "material": _fill_name,
                          "source": fill_src,
                          "n_elements": int((tg == DOM_SLOT_FILL).sum()),
                          "note": ("impregnation / air filling the rest of the "
                                   "slot; no impregnant card in the materials "
                                   "library, so 0.25 W/m·K (filled epoxy "
                                   "varnish) is used and reported as a default"
                                   if fill_src == "default" else "")},
            "gap_air": {"k_eff": round(float(gap_k_eff), 4),
                        "k_air": gap.get("k_air") if gap else None,
                        "source": "taylor_couette",
                        "n_elements": int((tg == DOM_GAP_AIR).sum())},
            "pocket_air": {"k": round(float(k_pocket), 4),
                           "source": "air at the stated gap temperature",
                           "n_elements": int((tg == DOM_POCKET_AIR).sum())},
            "winding": {"k": round(float(k_coil), 3),
                        "material": (mats.get("slot") if winding_is_wires else None),
                        "model": ("individual conductors: the copper's own k, "
                                  "with the enamel and the wire coating as meshed "
                                  "domains around each wire" if winding_is_wires
                                  else "bulk transverse winding k with the liner "
                                  "lumped in as a series film (no liner "
                                  "elements in this mesh)" if winding_lumps_liner
                                  else "bulk transverse winding k; the liner is "
                                       "a meshed domain and is NOT lumped in "
                                       "again"),
                        "n_elements": int((tg == DOM_COIL).sum())},
        },
        # What the re-tagger did to the air: counts per new domain and why.
        "air_domains": air_report,
        "P_cu_W": round(Pcu, 1), "P_fe_W": em.get("P_fe_W"),
        # The un-rounded copper the map was actually built from — the coupled
        # loop's feedback runs through it and 0.1 W is not a resolution a fixed
        # point can converge on (see `Pcu` above).
        "P_cu_exact_W": float(Pcu),
        "P_mag_eddy_W": em.get("P_mag_eddy_W"), "P_loss_total_W": em.get("P_loss_total_W"),
        # WHERE the cycle-averaged loss map came from.  A map replayed from the
        # Simulation run the user already did and a map solved for this request
        # are the same physics but not the same claim, and the panel prints the
        # difference instead of the user wondering why one map took six minutes
        # and the next took two seconds.
        "loss_source": loss_source,
        "loss_density_label": em.get("loss_density_label"),
        "outlines": em.get("outlines"), "extent": em.get("extent"),
    }
    # The timing pair every route in this router reports: `solve_time_s` is what
    # the conduction solve cost once the losses were in hand, `elapsed_s` is the
    # whole request (EM included), which is the number the user waited.
    result["solve_time_s"] = round(time.time() - _t_solve, 2)
    result["elapsed_s"] = _elapsed(t0)
    result["cached"] = False
    result["geometry_fingerprint"] = _live_fingerprint(_geo_ov)
    if _em_map is None:
        # An injected (copper-scaled) map is an approximation of the map a solve
        # at this coil temperature would produce, and the cache key cannot tell
        # the two apart — so it is never stored under one.  See the docstring.
        _cache_put(_FIELD_CACHE, key, result, _FIELD_CACHE_MAX)
    _report(2, phase="post-processing (components, flux)")
    return result


# ---------------------------------------------------------------------------
# The coupled EM <-> thermal fixed point
# ---------------------------------------------------------------------------

RUNAWAY_C = 400.0   # past any feasible motor → there is no stable equilibrium

# The winding temperature this loop is solved TO.  0.5 K, not the old 2 K:
# before 2026-09-07 every pass cost a full electromagnetic transient, so a
# tighter tolerance bought a fraction of a kelvin for another minute of FEM.
# Now no pass solves anything electromagnetic at all — every one of them is a
# conduction solve, seconds apiece — so the tolerance is set by what the answer
# means rather than by what it costs.  0.5 K is also the resolution the coolant
# loop inside `solve_thermal_field` already converges to, so tightening past it
# would be chasing the other loop's noise.
COUPLED_TOL_C = 0.5


def solve_coupled(
    *,
    max_iter: int = 12,
    tol_c: float = COUPLED_TOL_C,
    relax: float = 0.6,
    progress=None,
    **field_kwargs: Any,
) -> Dict[str, Any]:
    """EM ↔ thermal fixed point: solve for the winding temperature, don't assume it.

    A single thermal map is computed at an ASSUMED copper temperature, and copper
    resistivity rises ~0.39 %/K — so a map solved at 120 °C on a winding that
    actually sits at 180 °C under-reports the copper loss by ~20 %, and the
    hotspot with it.  This iterates the obvious loop until it stops moving:

        losses at T  →  thermal solve  →  winding temperature  →  back into the
        losses as the new copper temperature

    THE LOSS MAP IS OBTAINED ONCE, AND NEVER SOLVED HERE (2026-09-07).  It used
    to be re-solved on every pass — twelve full eddy transients for one answer —
    and that was never what the feedback needed.  Between two passes the only
    thing that changed was the winding temperature, and the only loss it moves is
    the copper's, through ρ_Cu(T) and nothing else: iron hysteresis and eddy loss
    are set by B(t) and the frequency, magnet and sleeve eddy loss by their own
    conductivities and the same B(t), and at fixed phase current none of those
    see the winding at all.  So the map is taken ONCE from the Electromagnetic
    run (or this router's memory of one — see ``_em_loss_map``, which raises a
    422 when there is neither), and each pass rescales ONLY the coil-domain loss
    density by ρ_Cu(T)/ρ_Cu(T₀).  Every pass is therefore a conduction solve,
    which is seconds, which is why ``tol_c`` is 0.5 K.

    Under-relaxed (``relax``) for stability, stopped when |ΔT| < ``tol_c`` or
    after ``max_iter`` passes, and flagged ``runaway`` when the winding walks past
    ``RUNAWAY_C`` — at that point there is no equilibrium to converge TO, and
    saying "not converged" without saying why would send the user to a bigger
    iteration count instead of to more cooling.

    ``verify_em`` is GONE (2026-09-07).  It re-solved the electromagnetic map
    once at the converged temperature to audit the analytic copper scaling, and
    an electromagnetic solve is exactly what this router may no longer start —
    the audit belongs on the Electromagnetic tab, where the user runs the point
    at the converged coil temperature and the Thermal tab then matches it.  The
    ``/coupled`` route still ACCEPTS the parameter and says it is ignored, so an
    old client is not broken by a 422 over a flag that now does nothing.

    Lives here rather than in ``modules.solvers`` so the capability
    ``solver.em_thermal`` and ``GET /api/thermal/coupled`` run the SAME loop; the
    module now calls this function.

    ``progress`` (2026-09-07) is the shared live-progress callback (see
    ``motor_ai_sim.progress``).  The budget is ``max_iter x 2`` — every pass is a
    conduction solve plus its post-processing, and there are no EM frames in it
    any more — revised DOWN the moment the loop converges: a run that settled on
    pass 3 of 6 should end its bar there, not report itself half-finished for
    ever.  It over-estimates on purpose, because a bar that shrinks under the
    user is worse than one that finishes early.
    """
    if not callable(progress):          # see solve_thermal_field above
        progress = None
    t0 = time.time()
    n_iter = max(1, int(max_iter))
    # The starting guess: whatever coil temperature was asked for, else ambient.
    T = float(field_kwargs.get("coil_temp_c",
                               field_kwargs.get("ambient_temp", 25.0)) or 25.0)

    # Two steps per pass, the same budget the single-pass function reports; the
    # callback still carries the pass's own total, so a change there needs no
    # edit here.
    _pass = {"n": 2, "i": 0}

    def _pass_progress(done, total=None, phase=None, composition=None):
        if progress is None:
            return
        if total:
            _pass["n"] = max(int(total), 1)
        base = _pass["i"] * _pass["n"]
        progress(base + int(done), n_iter * _pass["n"],
                 f"iteration {_pass['i'] + 1}/{n_iter} — {phase}"
                 if phase else None,
                 f"1 loss map + up to {n_iter} x (conduction + post-processing)")

    hist: List[float] = []
    th: Dict[str, Any] = {}
    converged = False
    # The map, obtained once, and the temperature it belongs to.
    em0: Optional[Dict[str, Any]] = None
    src0: Dict[str, Any] = {}
    t_ref_c = T
    p_cu_ref = 0.0
    scale: Dict[str, float] = {"rho_ratio": 1.0, "effective": 1.0,
                               "p_cu_dc_ref_W": 0.0, "p_cu_ac_ref_W": 0.0}
    t_scaled_c = T          # the temperature the LAST map served was scaled to
    for _it in range(n_iter):
        _pass["i"] = _it
        if em0 is None:
            _cap: Dict[str, Any] = {}
            th = solve_thermal_field(**{**field_kwargs, "coil_temp_c": T},
                                     progress=_pass_progress,
                                     _em_capture=_cap) or {}
            em0 = _cap.get("em")
            src0 = dict(_cap.get("loss_source") or {})
            t_ref_c = T
            p_cu_ref = float(th.get("P_cu_exact_W",
                                    th.get("P_cu_W")) or 0.0)

        else:
            t_scaled_c = T
            em_s, scale = _scaled_copper_map(em0, t_ref_c=t_ref_c, t_c=T)
            th = solve_thermal_field(
                **{**field_kwargs, "coil_temp_c": T},
                progress=_pass_progress, _em_map=em_s,
                _em_loss_source={
                    **src0,
                    "scaled_copper": {
                        "alpha_per_k": float(ALPHA_CU_PER_K),
                        "t_ref_c": round(float(t_ref_c), 1),
                        "t_coil_c": round(float(T), 1),
                        **{k: round(float(v), 5) for k, v in scale.items()}},
                    "note": ("the loss map of %s with only the copper moved to "
                             "%.1f °C: its DC share by rho_Cu(T)/rho_Cu(%.1f °C)"
                             " = %.4f and its solved AC share by the inverse, "
                             "net %.4f — iron, magnet, shaft and sleeve losses "
                             "are held, they do not depend on the winding "
                             "temperature"
                             % (src0.get("run_id") or src0.get("kind")
                                or "this operating point",
                                float(T), float(t_ref_c),
                                scale["rho_ratio"], scale["effective"])),
                }) or {}
        winding = (th.get("components") or {}).get("winding") or {}
        # avg, not max: the loss model is a BULK resistivity over the whole
        # winding, so the temperature that belongs in it is the winding's mean.
        # `max` is the fallback for a machine whose slot meshed to a single
        # element band (no interior node → no meaningful average).
        T_cu = winding.get("avg", winding.get("max"))
        if T_cu is None:
            break
        T_cu = float(T_cu)
        hist.append(round(T_cu, 1))
        if T_cu > RUNAWAY_C:
            T = T_cu
            break                                   # diverging — thermal runaway
        if abs(T_cu - T) < float(tol_c):
            T = T_cu
            converged = True
            break
        T = float(relax) * T_cu + (1.0 - float(relax)) * T   # fed BACK to the losses

    # (The optional `verify_em` audit lived here until 2026-09-07.  It re-solved
    # the electromagnetic map at the converged temperature to measure the
    # analytic copper scaling against a solve — a good measurement, made the
    # wrong way round now: this router may not start an electromagnetic solve at
    # all.  To audit the scaling, run the point on the Electromagnetic tab at the
    # converged coil temperature; this tab will then match THAT run and report
    # `loss_source.kind == "simulation_run"` with the solved copper in it.)

    # The passes that were never run are not work anybody is waiting for: hand
    # them back so a loop that converged on pass 3 of 6 ends its bar at 3, not
    # at half.
    if progress is not None:
        _done_passes = max(len(hist), 1)
        progress(_done_passes * _pass["n"], _done_passes * _pass["n"],
                 (f"converged in {_done_passes} iteration"
                  f"{'s' if _done_passes > 1 else ''}" if converged else
                  f"stopped after {_done_passes} iteration"
                  f"{'s' if _done_passes > 1 else ''}"), None)

    runaway = (not converged) and bool(hist) and hist[-1] > RUNAWAY_C
    out: Dict[str, Any] = {
        "ok": bool(th),
        "coil_temp_history_C": hist,
        "coil_temp_converged_C": round(T, 1),
        "converged": bool(converged),
        "runaway": bool(runaway),
        "iterations": len(hist),
        "max_iter": n_iter,
        "tol_C": float(tol_c),
        "relax": float(relax),
        "field": th,
        "T_max": th.get("T_max"), "T_min": th.get("T_min"),
        "components": th.get("components"),
        "P_cu_W": th.get("P_cu_W"), "P_fe_W": th.get("P_fe_W"),
        "P_loss_total_W": th.get("P_loss_total_W"),
        "cooling": th.get("cooling"),
        # WHERE the one loss map came from, and HOW the copper was moved off it.
        "loss_source": th.get("loss_source"),
        "copper_scaling": {
            "alpha_per_k": float(ALPHA_CU_PER_K),
            "t_ref_c": round(float(t_ref_c), 1),
            "t_ref_material_c": float(T_CU_REF_C),
            "formula": ("r = (1 + alpha*(T - 20 C)) / (1 + alpha*(T_ref - 20 C));"
                        "  P_cu(T) = P_cu_dc(T_ref)*r + P_cu_ac(T_ref)/r"),
            "p_cu_ref_W": round(float(p_cu_ref), 2),
            "p_cu_final_W": round(float(th.get("P_cu_exact_W",
                                               th.get("P_cu_W")) or 0.0), 2),
            # The temperature the FINAL map was scaled to.  It is the
            # under-relaxed value the last pass was solved at, not
            # `coil_temp_converged_C` (which is the winding temperature that
            # pass came back with) — a converged loop puts them within `tol_C`
            # of each other, and saying which is which is how the ratio below
            # can be checked by hand.
            "t_scaled_c": round(float(t_scaled_c), 2),
            "p_cu_dc_ref_W": round(float(scale["p_cu_dc_ref_W"]), 3),
            "p_cu_ac_ref_W": round(float(scale["p_cu_ac_ref_W"]), 3),
            "rho_ratio": round(float(scale["rho_ratio"]), 5),
            "ratio": round(float(scale["effective"]), 5),
            # ZERO, and it is the headline of the 2026-09-07 split: not one
            # electromagnetic solve ran for this temperature.  The map came from
            # the Electromagnetic tab (see `loss_source`) and the copper was
            # moved analytically.
            "em_solves": 0,
            "note": ("only the winding is moved between passes, and its two "
                     "shares move opposite ways: the DC loss with rho_Cu(T), "
                     "the solved AC (proximity/skin) loss with sigma_Cu(T) = "
                     "1/rho_Cu(T).  Iron, magnet, shaft and sleeve losses are "
                     "held — they are set by B(t), the electrical frequency and "
                     "their own conductivities, none of which the winding "
                     "temperature changes at fixed phase current"),
        },
        "warning": ("thermal runaway: no stable equilibrium at this operating "
                    "point — increase the cooling or reduce the current"
                    if runaway else None),
        # The honest solve time is the WHOLE loop, not the last pass's —
        # reporting the last one would make a six-pass run look like one pass.
        "solve_time_s": round(time.time() - t0, 2),
        "cached": False,
    }
    out["elapsed_s"] = _elapsed(t0)
    out["geometry_fingerprint"] = th.get("geometry_fingerprint")
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/field")
@_JOBS.queued("thermal.field", priority=_JOBS.Priority.FIELD)
def field(
    cooling_mode:       str = Query(default="manual",
                                    description="OUTER stator surface: manual "
                                                "(your own h) | air (housing in "
                                                "cross-flow) | liquid (jacket at "
                                                "a given inlet temperature and "
                                                "flow) | robotics (a joint in "
                                                "still air: natural convection + "
                                                "radiation, open bore, axial end "
                                                "faces, bolted mount) | none"),
    ambient_temp:       float = Query(default=25.0, description="coolant / ambient [°C]"),
    h_conv:             float = Query(default=50.0,
                                      description="housing convection coefficient "
                                                  "[W/m²·K], cooling_mode=manual only"),
    slot_k:             float = Query(default=0.0, ge=0.0,
                                      description="slot transverse k [W/m·K]; "
                                                  "0 = auto from the wire stack"),
    gap_k:              float = Query(default=0.0, ge=0.0,
                                      description="DEPRECATED and ignored: the "
                                                  "air-gap k is derived from the "
                                                  "mechanical clearance, the "
                                                  "speed and the gap temperature"),
    rpm:                float = Query(default=0.0,
                                      description="rotor speed [rpm] — sets the "
                                                  "gap Taylor number"),
    gamma_deg:          float = Query(default=0.0),
    I_phase_rms:        float = Query(default=120.0, ge=0.0),
    n_steps_per_period: int = Query(default=12, ge=1, le=360),
    n_periods:          float = Query(default=2.0, gt=0.0, le=20.0),
    mesh_size_mm:       float = Query(default=3.0, gt=0.0, le=50.0),
    min_size_mm:        float = Query(default=0.3, gt=0.0, le=50.0),
    outer_air_factor:   float = Query(default=1.3, ge=1.0, le=10.0),
    n_sectors:          int = Query(default=4, ge=-1, le=64),
    coil_temp_c:        float = Query(default=120.0,
                                      description="winding temperature the EM "
                                                  "losses are evaluated at [°C]"),
    component_mesh:     str = Query(default="",
                                    description='JSON {component: size_mm}; the '
                                                'coil entry is auto-refined when '
                                                'absent'),
    geo:                Optional[str] = Query(default=None),
    air_speed_mps:      float = Query(default=0.0, ge=0.0,
                                      description="cooling_mode=air: airflow over "
                                                  "the housing"),
    fluid:              str = Query(default="water",
                                    description="cooling_mode=liquid: coolant name "
                                                "from the materials library"),
    fluid_temp_in_c:    float = Query(default=25.0, description="liquid: inlet temperature [°C]"),
    fluid_temp_out_c:   float = Query(default=0.0,
                                      description="DEPRECATED and ignored: the "
                                                  "outlet temperature is a RESULT"),
    flow_lpm:           float = Query(default=0.0, ge=0.0,
                                      description="cooling_mode=liquid: jacket "
                                                  "flow [L/min], REQUIRED > 0"),
    bore_mode:          str = Query(default="none",
                                    description="ROTOR BORE (hollow shaft): none "
                                                "| still (open, unventilated — "
                                                "cooling_mode=robotics only) "
                                                "| air (blown axially through the "
                                                "bore) | liquid (coolant through "
                                                "the shaft)"),
    bore_air_speed_mps: float = Query(default=0.0, ge=0.0,
                                      description="bore_mode=air: axial air speed"),
    bore_fluid:         str = Query(default="water",
                                    description="bore_mode=liquid: coolant name"),
    bore_fluid_temp_in_c: float = Query(default=25.0,
                                        description="bore liquid: inlet temperature [°C]"),
    bore_flow_lpm:      float = Query(default=0.0, ge=0.0,
                                      description="bore_mode=liquid: shaft flow "
                                                  "[L/min], REQUIRED > 0"),
    shaft_ext_length_mm: float = Query(default=0.0, ge=0.0,
                                       description="EXPOSED SHAFT: how much "
                                                   "shaft sticks out of the "
                                                   "housing on EACH side [mm]; "
                                                   "0 = the path is off"),
    shaft_ext_diameter_mm: float = Query(default=0.0, ge=0.0,
                                         description="OD of the exposed shaft "
                                                     "[mm]; 0 = derive it from "
                                                     "the geometry"),
    shaft_ext_sides:    int = Query(default=2, ge=1, le=2,
                                    description="how many shaft ends come out "
                                                "of the housing (2 = through "
                                                "shaft, 1 = one end capped)"),
    frame:              str = Query(default="housed",
                                    description="how the machine is BUILT: "
                                                "'housed' (default — the end "
                                                "windings and the slot air are "
                                                "inside a closed housing) or "
                                                "'open' (no housing: the end "
                                                "turns and the axial channels "
                                                "between neighbouring coils are "
                                                "in the airflow)"),
    open_air_speed_mps: float = Query(default=0.0, ge=0.0,
                                      description="frame=open: air speed over "
                                                  "the end windings and through "
                                                  "the slot channels [m/s]; 0 = "
                                                  "take the housing's own air "
                                                  "speed when the outer surface "
                                                  "is in air, else still air"),
    emissivity:         float = Query(default=0.9, ge=0.0, le=1.0,
                                      description="cooling_mode=robotics: the "
                                                  "housing's total hemispherical "
                                                  "emissivity (0.9 painted / "
                                                  "anodised / oxidised, ~0.05 "
                                                  "bare polished aluminium).  On "
                                                  "a small machine in still air "
                                                  "radiation carries MORE than "
                                                  "the convection; 0 removes it "
                                                  "exactly"),
    mount_g_w_per_k:    float = Query(default=0.0, ge=0.0,
                                      description="THE MOUNT: contact conductance "
                                                  "from the housing into the arm "
                                                  "it is bolted to [W/K]; 0 = "
                                                  "bolted to nothing.  On an Ø85 "
                                                  "joint 0.5 is a dry interface "
                                                  "through a few M4 bolts, 2 a "
                                                  "machined face with compound, "
                                                  "10 a housing that is part of "
                                                  "the arm casting"),
    mount_temp_c: Optional[float] = Query(default=None,
                                          description="the temperature the mount "
                                                      "is HELD at [°C]; omitted = "
                                                      "the ambient.  The mount is "
                                                      "an infinite sink — its "
                                                      "temperature does not rise "
                                                      "with the machine's heat"),
    end_faces:          str = Query(default="still",
                                    description="cooling_mode=robotics: 'still' "
                                                "(the end turns, the core end "
                                                "faces and the magnet ends are "
                                                "exposed to the room on both "
                                                "sides) or 'none' (both ends "
                                                "buried)"),
    end_face_sides:     int = Query(default=2, ge=1, le=2,
                                    description="how many ends of the machine are "
                                                "exposed (2 = open both sides, "
                                                "1 = one end against a gearbox)"),
    magnet_temp_c: Optional[float] = Query(default=None,
                                           description="MAGNET temperature the "
                                                       "Electromagnetic run was "
                                                       "solved at [°C]; omitted "
                                                       "= the magnet card as the "
                                                       "library quotes it.  It "
                                                       "SELECTS the run, it does "
                                                       "not correct anything "
                                                       "here"),
    mode:               Optional[str] = Query(default=None,
                                              description="motor | generator — the "
                                                          "operating mode the "
                                                          "Electromagnetic run was "
                                                          "made in; omitted = the "
                                                          "shared config's "
                                                          "simulation.mode.  SELECTS "
                                                          "the run (generator keys on "
                                                          "γ + 180°), changes no "
                                                          "physics here"),
):
    """Steady-state 2-D temperature map of the machine.

    Moved here from ``/api/simulation/physics/thermal_field2d`` (2026-09-07); the
    payload is the same one the Temp view has always drawn, plus the four fields
    every route in this router carries — ``elapsed_s``, ``solve_time_s``,
    ``cached`` and ``geometry_fingerprint`` — so the panel can print how long it
    took and whether the picture still describes the live machine.

    The answer is remembered as the tab's last ``field`` result, so re-entering
    the Thermal tab restores it instead of looking the map up again.

    THE LOSSES ARE NOT COMPUTED HERE (2026-09-07).  ``gamma_deg``,
    ``I_phase_rms``, ``coil_temp_c``, ``n_steps_per_period`` and ``n_periods``
    SELECT the Electromagnetic run whose cycle-averaged loss map this temperature
    is solved from; when no run (and no remembered map) matches, the answer is a
    422 with ``error_code: "no_electromagnetic_run"`` naming the point to run —
    never a solve started behind this request.
    """
    # Two steps: the conduction solve and the post-processing.  No EM frames —
    # there is no EM solve in this route.
    _progress.start(
        total=2, phase="loss map — looking for the Electromagnetic run",
        kind="field", composition="conduction + post-processing")
    # The frame's two parameters are RESTORED into the tab's fields only when
    # they were part of the request — the same rule the cache key follows
    # (`_field_cache_key`) and the same one `thermal_settings` states: a
    # parameter the chosen mode does not use is not sent, and a `/last` entry
    # from before this field existed must read identically to a housed one.
    _open_kw = ({"frame": "open",
                 "open_air_speed_mps": float(open_air_speed_mps)}
                if _norm_frame(frame) == "open" else {})
    # …and the ROBOTICS block (2026-09-14) by the same rule, in the same two
    # independent pieces the cache key splits it into: the still-air inputs ride
    # with the mode that reads them, the MOUNT rides with a conductance that is
    # not zero (it is read in every mode — a jacketed machine is bolted to
    # something too), and `mount_temp_c` only when it was actually typed, because
    # blank means "the ambient" and restoring a number into a blank field is how
    # a default becomes an assumption nobody made.
    _robot_kw = ({"emissivity": float(emissivity),
                  "end_faces": _norm_end_faces(end_faces),
                  "end_face_sides": int(end_face_sides)}
                 if str(cooling_mode or "").strip().lower() == "robotics" else {})
    _mount_kw: Dict[str, Any] = {}
    if float(mount_g_w_per_k or 0.0) > 0.0:
        _mount_kw["mount_g_w_per_k"] = float(mount_g_w_per_k)
        if mount_temp_c is not None:
            _mount_kw["mount_temp_c"] = float(mount_temp_c)
    try:
        out = solve_thermal_field(
            ambient_temp=ambient_temp, h_conv=h_conv, slot_k=slot_k, gap_k=gap_k,
            rpm=rpm, gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
            n_steps_per_period=n_steps_per_period, n_periods=n_periods,
            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor, n_sectors=n_sectors,
            coil_temp_c=coil_temp_c, component_mesh=component_mesh, geo=geo,
            cooling_mode=cooling_mode, air_speed_mps=air_speed_mps, fluid=fluid,
            fluid_temp_in_c=fluid_temp_in_c, fluid_temp_out_c=fluid_temp_out_c,
            flow_lpm=flow_lpm, bore_mode=bore_mode,
            bore_air_speed_mps=bore_air_speed_mps, bore_fluid=bore_fluid,
            bore_fluid_temp_in_c=bore_fluid_temp_in_c,
            bore_flow_lpm=bore_flow_lpm,
            shaft_ext_length_mm=shaft_ext_length_mm,
            shaft_ext_diameter_mm=shaft_ext_diameter_mm,
            shaft_ext_sides=shaft_ext_sides, magnet_temp_c=magnet_temp_c,
            frame=frame, open_air_speed_mps=open_air_speed_mps,
            **_robot_kw, **_mount_kw,
            op_mode=mode,
            progress=_progress.callback())
        _remember_last("field", out, _field_params(
            cooling_mode=cooling_mode, ambient_temp=ambient_temp, h_conv=h_conv,
            slot_k=slot_k, rpm=rpm, gamma_deg=gamma_deg,
            I_phase_rms=I_phase_rms, n_steps_per_period=n_steps_per_period,
            n_periods=n_periods, mesh_size_mm=mesh_size_mm,
            min_size_mm=min_size_mm, outer_air_factor=outer_air_factor,
            n_sectors=n_sectors, coil_temp_c=coil_temp_c,
            component_mesh=component_mesh, air_speed_mps=air_speed_mps,
            fluid=fluid, fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
            bore_mode=bore_mode, bore_air_speed_mps=bore_air_speed_mps,
            bore_fluid=bore_fluid,
            bore_fluid_temp_in_c=bore_fluid_temp_in_c,
            bore_flow_lpm=bore_flow_lpm,
            shaft_ext_length_mm=shaft_ext_length_mm,
            shaft_ext_diameter_mm=shaft_ext_diameter_mm,
            shaft_ext_sides=shaft_ext_sides,
            **_open_kw, **_robot_kw, **_mount_kw,
            # Restored into the tab's input fields like every other parameter —
            # and it is `None` on every request that did not ask for one, so a
            # stored entry from before this field existed reads the same.
            magnet_temp_c=magnet_temp_c),
            out.get("geometry_fingerprint"))
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()


def _field_params(**kw) -> Dict[str, Any]:
    """The request, as the panel needs it back.

    ``/last`` hands these to the tab so re-entering it restores the INPUT fields
    as well as the picture — an answer whose cooling mode is not on screen beside
    it is a number nobody can reproduce.
    """
    return dict(kw)


def _note_verify_em_ignored(out: Dict[str, Any], verify_em: bool) -> Dict[str, Any]:
    """Tell a caller that still sends ``verify_em`` that it did nothing.

    Same rule as ``fluid_temp_out_c`` and ``gap_k`` in ``solve_thermal_field``:
    an input that is accepted for compatibility is accepted SILENTLY only over
    this project's dead body.  A client that asked for the electromagnetic audit
    believes the copper scaling was measured against a solve, and since
    2026-09-07 it was not — this router runs no electromagnetic solve at all.

    The note is attached to THIS request's answer (cached or fresh), never to the
    cached payload: the flag is not in the cache key, and a note that leaked into
    the stored result would then be served to somebody who never sent it.
    """
    if not verify_em:
        return out
    cooling = dict(out.get("cooling") or {})
    cooling["deprecated"] = list(cooling.get("deprecated") or ()) + [
        "verify_em is ignored: it re-solved the electromagnetic map at the "
        "converged winding temperature, and the thermal solve no longer runs "
        "any electromagnetic solve — the loss map comes from an Electromagnetic "
        "run (see loss_source).  To audit the analytic copper scaling, run that "
        "point on the Electromagnetic tab at the converged coil temperature and "
        "solve here again."]
    return {**out, "cooling": cooling}


@router.get("/coupled")
@_JOBS.queued("thermal.coupled", priority=_JOBS.Priority.DUTY)
def coupled(
    max_iter:           int = Query(default=12, ge=1, le=40,
                                    description="winding-temperature passes.  The "
                                                "loss map is fetched ONCE; each "
                                                "pass is a conduction solve with "
                                                "the copper rescaled to its "
                                                "temperature"),
    verify_em:          bool = Query(default=False,
                                     description="DEPRECATED and ignored: it "
                                                 "re-solved the electromagnetic "
                                                 "map at the converged "
                                                 "temperature, and this route no "
                                                 "longer runs any electromagnetic "
                                                 "solve"),
    cooling_mode:       str = Query(default="manual"),
    ambient_temp:       float = Query(default=25.0),
    h_conv:             float = Query(default=50.0),
    slot_k:             float = Query(default=0.0, ge=0.0),
    gap_k:              float = Query(default=0.0, ge=0.0),
    rpm:                float = Query(default=0.0),
    gamma_deg:          float = Query(default=0.0),
    I_phase_rms:        float = Query(default=120.0, ge=0.0),
    n_steps_per_period: int = Query(default=12, ge=1, le=360),
    n_periods:          float = Query(default=2.0, gt=0.0, le=20.0),
    mesh_size_mm:       float = Query(default=3.0, gt=0.0, le=50.0),
    min_size_mm:        float = Query(default=0.3, gt=0.0, le=50.0),
    outer_air_factor:   float = Query(default=1.3, ge=1.0, le=10.0),
    n_sectors:          int = Query(default=4, ge=-1, le=64),
    coil_temp_c:        float = Query(default=120.0,
                                      description="the STARTING guess only — the "
                                                  "converged value is the answer"),
    component_mesh:     str = Query(default=""),
    geo:                Optional[str] = Query(default=None),
    air_speed_mps:      float = Query(default=0.0, ge=0.0),
    fluid:              str = Query(default="water"),
    fluid_temp_in_c:    float = Query(default=25.0),
    fluid_temp_out_c:   float = Query(default=0.0,
                                      description="DEPRECATED and ignored"),
    flow_lpm:           float = Query(default=0.0, ge=0.0),
    bore_mode:          str = Query(default="none"),
    bore_air_speed_mps: float = Query(default=0.0, ge=0.0),
    bore_fluid:         str = Query(default="water"),
    bore_fluid_temp_in_c: float = Query(default=25.0),
    bore_flow_lpm:      float = Query(default=0.0, ge=0.0),
    shaft_ext_length_mm: float = Query(default=0.0, ge=0.0),
    shaft_ext_diameter_mm: float = Query(default=0.0, ge=0.0),
    shaft_ext_sides:    int = Query(default=2, ge=1, le=2),
    # Same two as /field (2026-09-09).  Accepted HERE as well because the panel
    # sends one request shape to both buttons: a `frame` this route ignored
    # would silently answer the coupled question about a housed machine while
    # the field map beside it described an open one.
    frame:              str = Query(default="housed"),
    open_air_speed_mps: float = Query(default=0.0, ge=0.0),
    # …and the robotics block (2026-09-14), accepted here for exactly the same
    # reason: a mount conductance this route ignored would answer the coupled
    # question about a machine bolted to nothing while the field map beside it
    # described one bolted to an arm.
    emissivity:         float = Query(default=0.9, ge=0.0, le=1.0),
    mount_g_w_per_k:    float = Query(default=0.0, ge=0.0),
    mount_temp_c: Optional[float] = Query(default=None),
    end_faces:          str = Query(default="still"),
    end_face_sides:     int = Query(default=2, ge=1, le=2),
):
    """The self-consistent operating point: solve for the winding temperature.

    ``/field`` answers "how hot does it get IF the copper is at 120 °C".  This
    answers "how hot does it get", by feeding the computed winding temperature
    back into the losses until it stops moving.  Same params as ``/field`` plus
    ``max_iter``; ``coil_temp_c`` becomes the starting guess rather than an
    assumption, and ``runaway`` says when there is no equilibrium to find.

    Since 2026-09-07 it costs ONE loss map, not ``max_iter`` of them, and it does
    not compute that map either: it comes from the Electromagnetic run of this
    operating point (``loss_source``), and only the copper is rescaled between
    passes (``copper_scaling``, ``em_solves: 0``).  With no matching run the
    answer is the same 422 ``/field`` gives.  Still cached on the same key
    ``/field`` uses plus the iteration settings, and remembered as the tab's last
    ``coupled`` result.

    ``verify_em`` is accepted and IGNORED (it re-solved the map at the converged
    temperature); the response says so under ``cooling.deprecated`` rather than
    refusing an old client's request over a flag that now does nothing.
    """
    # One loss map (fetched, not solved), then max_iter conduction passes of two
    # steps each.  Revised DOWN by `solve_coupled` the moment the loop converges
    # — most do, in three.
    _progress.start(
        total=int(max_iter) * 2,
        phase=(f"iteration 1/{int(max_iter)} — loss map — looking for the "
               "Electromagnetic run"),
        kind="coupled",
        composition=(f"1 loss map + up to {int(max_iter)} x (conduction + "
                     "post-processing)"))
    try:
        t0 = time.time()
        from motor_ai_sim.routes.simulation import _parse_geo_override

        geo_ov = _parse_geo_override(geo)
        mode, bmode = _validate_field_params(
            cooling_mode=cooling_mode, ambient_temp=ambient_temp, h_conv=h_conv,
            air_speed_mps=air_speed_mps, fluid=fluid,
            fluid_temp_in_c=fluid_temp_in_c, flow_lpm=flow_lpm,
            bore_mode=bore_mode, bore_air_speed_mps=bore_air_speed_mps,
            bore_fluid=bore_fluid, bore_fluid_temp_in_c=bore_fluid_temp_in_c,
            bore_flow_lpm=bore_flow_lpm,
            shaft_ext_length_mm=shaft_ext_length_mm,
            shaft_ext_diameter_mm=shaft_ext_diameter_mm,
            shaft_ext_sides=shaft_ext_sides,
            frame=frame, open_air_speed_mps=open_air_speed_mps,
            emissivity=emissivity, mount_g_w_per_k=mount_g_w_per_k,
            mount_temp_c=mount_temp_c, end_faces=end_faces,
            end_face_sides=end_face_sides,
            slot_k=slot_k, rpm=rpm, I_phase_rms=I_phase_rms,
            coil_temp_c=coil_temp_c, n_steps_per_period=n_steps_per_period,
            n_periods=n_periods, mesh_size_mm=mesh_size_mm,
            min_size_mm=min_size_mm, outer_air_factor=outer_air_factor)

        _cm = _auto_coil_mesh(component_mesh, mesh_size_mm, geo_ov)
        # The frame rides `_cool_kw` — which is the cache key AND the remembered
        # params AND the solve's kwargs — only when it is `open`, for the third
        # time and the same reason: a housed request must key, restore and solve
        # byte-identically to every answer given before the frame existed.
        _open_kw = ({"frame": "open",
                     "open_air_speed_mps": float(open_air_speed_mps)}
                    if _norm_frame(frame) == "open" else {})
        _robot_kw = ({"emissivity": float(emissivity),
                      "end_faces": _norm_end_faces(end_faces),
                      "end_face_sides": int(end_face_sides)}
                     if mode == "robotics" else {})
        if float(mount_g_w_per_k or 0.0) > 0.0:
            _robot_kw["mount_g_w_per_k"] = float(mount_g_w_per_k)
            if mount_temp_c is not None:
                _robot_kw["mount_temp_c"] = float(mount_temp_c)
        _cool_kw = dict(cooling_mode=mode, air_speed_mps=air_speed_mps,
                        fluid=fluid, fluid_temp_in_c=fluid_temp_in_c,
                        flow_lpm=flow_lpm, bore_mode=bmode,
                        bore_air_speed_mps=bore_air_speed_mps,
                        bore_fluid=bore_fluid,
                        bore_fluid_temp_in_c=bore_fluid_temp_in_c,
                        bore_flow_lpm=bore_flow_lpm,
                        shaft_ext_length_mm=shaft_ext_length_mm,
                        shaft_ext_diameter_mm=shaft_ext_diameter_mm,
                        shaft_ext_sides=shaft_ext_sides,
                        **_open_kw, **_robot_kw)
        key = _field_cache_key(
            geo_ov, _assignments(), ambient_temp=ambient_temp, h_conv=h_conv,
            slot_k=slot_k, rpm=rpm, gamma_deg=gamma_deg,
            I_phase_rms=I_phase_rms, n_steps_per_period=n_steps_per_period,
            n_periods=n_periods, mesh_size_mm=mesh_size_mm,
            min_size_mm=min_size_mm, outer_air_factor=outer_air_factor,
            n_sectors=n_sectors, coil_temp_c=coil_temp_c, component_mesh=_cm,
            # `verify_em` is NOT in the key any more: an ignored input must not
            # split the cache (same rule as `fluid_temp_out_c` / `gap_k` in
            # `solve_thermal_field`), and the note that says it was ignored is
            # attached to THIS request's answer below, cached or not.
            **_cool_kw) + ("coupled", int(max_iter))

        _params = _field_params(
            max_iter=max_iter, verify_em=bool(verify_em),
            ambient_temp=ambient_temp,
            h_conv=h_conv, slot_k=slot_k, rpm=rpm,
            gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
            n_steps_per_period=n_steps_per_period, n_periods=n_periods,
            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor, n_sectors=n_sectors,
            coil_temp_c=coil_temp_c, component_mesh=component_mesh,
            **_cool_kw)

        hit = _COUPLED_CACHE.get(key)
        if hit is not None:
            out = dict(hit)
            out["cached"] = True
            out["elapsed_s"] = _elapsed(t0)
            _remember_last("coupled", hit, _params, hit.get("geometry_fingerprint"))
            return _note_verify_em_ignored(out, verify_em)

        out = solve_coupled(
            max_iter=max_iter,
            ambient_temp=ambient_temp, h_conv=h_conv, slot_k=slot_k,
            rpm=rpm, gamma_deg=gamma_deg, I_phase_rms=I_phase_rms,
            n_steps_per_period=n_steps_per_period, n_periods=n_periods,
            mesh_size_mm=mesh_size_mm, min_size_mm=min_size_mm,
            outer_air_factor=outer_air_factor, n_sectors=n_sectors,
            coil_temp_c=coil_temp_c, component_mesh=component_mesh, geo=geo,
            progress=_progress.callback(), **_cool_kw)
        _cache_put(_COUPLED_CACHE, key, out, _COUPLED_CACHE_MAX)
        _remember_last("coupled", out, _params, out.get("geometry_fingerprint"))
        return _note_verify_em_ignored(out, verify_em)
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()


@router.get("/last")
def last(
    field: bool = Query(default=True,
                        description="include the heavy per-node / per-triangle "
                                    "arrays"),
    geo: Optional[str] = Query(default=None),
):
    """The most recent thermal map and coupled run.

    Each comes back with the geometry fingerprint it was SOLVED for and the live
    fingerprint now, so the panel can say "this result is for a previous
    geometry" instead of quietly drawing yesterday's machine.  ``stale_geometry``
    is ``None`` — UNKNOWN, never "fine" — when either fingerprint is missing: a
    staleness check that cannot prove a mismatch must not claim one.

    200 with ``has_result: false`` rather than a 404: "nothing solved yet" is the
    NORMAL first state of the tab (it then draws the bare mesh), and a console
    full of red 404s on every fresh mount is not an error report.
    """
    from motor_ai_sim.routes._validation import parse_geo_override

    _load_last()
    geo_ov = parse_geo_override(geo)
    live = _live_fingerprint(geo_ov)

    def _entry(kind: str) -> Optional[Dict[str, Any]]:
        e = _LAST.get(kind)
        if not e or e.get("result") is None:
            return None
        res = e["result"]
        if not field:
            res = _strip_heavy(res)
        fp = e.get("geometry_fingerprint")
        stale = (None if (not fp or not live or live == "nofp")
                 else bool(fp != live))
        return {"result": res, "params": e.get("params") or {},
                "geometry_fingerprint": fp, "computed_at": e.get("computed_at"),
                "stale_geometry": stale}

    out: Dict[str, Any] = {"live_geometry_fingerprint": live}
    for kind in _LAST_KINDS:
        out[kind] = _entry(kind)
    out["has_result"] = any(out[k] is not None for k in _LAST_KINDS)
    return out


# ---------------------------------------------------------------------------
# THE HEAT PATHS — GET /api/thermal/heat_paths/last
# ---------------------------------------------------------------------------
# User, 2026-09-15: *"лучше нарисовать 3D модель с катушками (end windings) и на
# ней прямо показывать, куда и сколько тепла может отводиться"*.
#
# Every watt is already in `cooling`; what this adds is WHERE each one leaves,
# on a machine drawn out of cylinders and annuli, so the answer "89 % through
# four bolts" is a picture rather than a row of a table.  The model is built by
# `motor_ai_sim.thermal_heat_paths`, which computes no physics of its own and
# knows nothing about any particular machine — the same call serves the Ø85
# joint and the Ø200 jacketed motor.
#
# The front end derives the SAME model from the payload it already has (see
# web/src/components/thermal/heatPaths.ts), so the view works before this
# process is next restarted; this route is the server-side reading of it, for a
# client that would rather not re-implement the placement rules.

@router.get("/heat_paths/last")
def heat_paths_last(
    kind: str = Query(default="field",
                      description="which stored result to model: 'field' or "
                                  "'coupled'"),
    geo: Optional[str] = Query(default=None),
):
    """Where the heat left the machine, as a model something can draw.

    200 with ``has_result: false`` rather than a 404, for the same reason
    ``GET /last`` does it: "nothing solved yet" is the normal first state of the
    tab, and a console full of red is not an error report.
    """
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.thermal_heat_paths import (
        HEAT_PATH_SCHEMA_VERSION, heat_path_model)

    _load_last()
    _kind = str(kind or "field")
    if _kind not in _LAST_KINDS:
        raise _bad("kind", kind, "bad_value",
                   "kind names WHICH stored result to model — 'field' (the "
                   "steady map) or 'coupled' (the EM↔thermal fixed point).")

    geo_ov = parse_geo_override(geo)
    live = _live_fingerprint(geo_ov)
    e = _LAST.get(_kind) or {}
    res = e.get("result")
    if not res:
        return {"has_result": False, "kind": _kind,
                "schema_version": HEAT_PATH_SCHEMA_VERSION,
                "live_geometry_fingerprint": live, "model": None}

    # The geometry the model is DRAWN from is the live one, overrides included —
    # the same merge every other route in this file makes.  When the result is
    # stale against it the answer says so and still draws: a stale picture that
    # is labelled stale is more use than no picture, and the panel already has
    # the banner for it.
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override as _mgo
    from motor_ai_sim.config import get_config as _gc
    try:
        _geo = _mgo(dict((_gc().get("geometry") or {})), geo_ov)
    except Exception as exc:                       # noqa: BLE001
        log.warning("thermal/heat_paths: no live geometry (%s)", exc)
        _geo = {}

    fp = e.get("geometry_fingerprint")
    stale = (None if (not fp or not live or live == "nofp")
             else bool(fp != live))
    return {
        "has_result": True,
        "kind": _kind,
        "schema_version": HEAT_PATH_SCHEMA_VERSION,
        "computed_at": e.get("computed_at"),
        "geometry_fingerprint": fp,
        "live_geometry_fingerprint": live,
        "stale_geometry": stale,
        "model": heat_path_model(res, _geo, e.get("params") or {}),
    }


# ---------------------------------------------------------------------------
# THE DUTY CYCLE — POST /api/thermal/duty_cycle
# ---------------------------------------------------------------------------
# A robot joint is not an S1 machine.  Every answer above this line is a STEADY
# one: "how hot does it get if this point never ends".  The question the user
# actually has about the Ø85 joint is "how long may it pull 46 A, and at what
# duty may it repeat that for ever" — which needs heat CAPACITY and TIME, and
# neither is in a steady map.
#
# THIS ROUTE SOLVES NOTHING ELECTROMAGNETIC AND AT MOST ONE CONDUCTION PASS.
# The physics is ``thermal_duty_cycle`` (pure, no FastAPI), fitted to ONE
# converged 2-D map of the CALIBRATION duty:
#
#   1. resolve (die, configuration, duty) — the body's, else the active catalog
#      context — and the cycle block: the body's, else the one saved on the duty;
#   2. resolve the calibration duty (the block's, else the configuration's rated
#      one) and get its steady map in the CURRENT cooling boundary conditions:
#      the remembered one when it is this duty's at these BCs, otherwise one
#      `solve_thermal_field` call at that duty's own saved point.  The loss map
#      that solve reads is the duty's stored Electromagnetic RUN, found by the
#      same physics-identity lookup /field and /coupled use — with no such run
#      the answer is the same 422 they give, `no_electromagnetic_run`;
#   3. fit the network to that map, take the capacities off the duty's own mass
#      rows, read every named duty's losses off its saved summary, and integrate;
#   4. file the record per duty (`duty_results`, kind `duty_cycle`) and remember
#      it for `GET /duty_cycle/last`.  NOTHING ELSE IS PERSISTED: this route does
#      not touch the catalogue yaml, the per-duty thermal row or the per-duty
#      field store.
#
# Every refusal is BY NAME: the `DutyCycleError` codes map 1:1 onto the 422's
# `error_code`, and each one says what to change.

#: The cooling parameters that make two steady maps the SAME boundary condition.
#: A remembered map is reused only when its stored request matches this request's
#: `cooling_fields()` on EVERY one of them — in both directions, so a map solved
#: with a 2 W/K mount cannot be served to a request that named no mount.
_DC_COOLING_KEYS = (
    "cooling_mode", "ambient_temp", "h_conv", "air_speed_mps", "fluid",
    "fluid_temp_in_c", "flow_lpm", "bore_mode", "bore_air_speed_mps",
    "bore_fluid", "bore_fluid_temp_in_c", "bore_flow_lpm",
    "shaft_ext_length_mm", "shaft_ext_sides", "frame", "open_air_speed_mps",
    "emissivity", "end_faces", "end_face_sides", "mount_g_w_per_k",
    "mount_temp_c")

#: …and the operating point that selects the Electromagnetic run behind it.
_DC_POINT_KEYS = ("rpm", "I_phase_rms", "gamma_deg", "coil_temp_c")

#: How many samples of each series survive into the stored record (B.3).
DUTY_CYCLE_MAX_SAMPLES = 400
#: The progress bar's honest composition: one calibration solve (two steps of
#: its own) and one cycle.
_DC_PROGRESS_TOTAL = 3


def _dc_refuse(code: str, message: str, remedy: str = "",
               fields: Optional[List[str]] = None) -> HTTPException:
    """A duty-cycle refusal in this router's 422 shape, by NAME.

    ``code`` is the stable tag (the ``DutyCycleError.code`` where one exists) and
    the English sentence is the whole story plus what to do about it — a panel
    shows the sentence, a client switches on the code, and neither has to parse
    the other.
    """
    text = message + (("  " + remedy) if remedy else "")
    flds = list(fields or ["duty_cycle"])
    return _bad(flds[0], None, "bad_value", text, error=message,
                error_code=code,
                invalid=[{"field": f, "kind": "bad_value", "message": text}
                         for f in flds])


def _dc_from_error(exc, fields: Optional[List[str]] = None) -> HTTPException:
    """One ``DutyCycleError`` → the 422 that names it."""
    return _dc_refuse(getattr(exc, "code", "duty_cycle_refused"),
                      getattr(exc, "message", str(exc)),
                      getattr(exc, "remedy", ""), fields)


def _dc_num(body: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = body.get(key)
        return float(default if v is None or v == "" else float(v))
    except (TypeError, ValueError):
        return float(default)


def _dc_close(a: Any, b: Any) -> bool:
    """Two stored request values, compared the way a cache key would."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def _dc_geometry(geo: Optional[str]):
    """``(the merged geometry parameters, the parsed ?geo override)``.

    The SAME resolution ``_live_polys`` uses — ``merge_geo_override`` on the live
    geometry — because the stack length and the housing diameter this model needs
    are read off it, and a plain dict update would put one machine's primaries
    under another's derived radii.
    """
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.services.geometry_service import get_current_geometry
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override

    ov = parse_geo_override(geo)
    return merge_geo_override(get_current_geometry().to_dict(), ov), ov


def _dc_rated_duty(duties: List[Dict[str, Any]],
                   fallback: Optional[str]) -> Optional[str]:
    """WHICH duty the network is calibrated on when the block does not say.

    The duty whose NAME says rated (the project's own rule — ``report._rated_duty``
    picks the report's pictures the same way), then the duty the request is
    about, then the first one there is.  The calibration point decides every
    conductance in the network, so the choice is reported in the record and never
    left implicit.
    """
    names = [str(d.get("name") or "") for d in duties if d.get("name")]
    for n in names:
        if n.strip().lower().startswith("rated"):
            return n
    if fallback and fallback in names:
        return fallback
    return names[0] if names else None


def _dc_map_matches(entry: Any, *, point: Dict[str, Any],
                    cooling: Dict[str, Any], fp: Optional[str]) -> bool:
    """Is this remembered answer the calibration duty's map at these BCs?

    Deliberately strict in BOTH directions (see ``_DC_COOLING_KEYS``): a map
    that merely looks similar is a network fitted to the wrong machine, and the
    cost of being wrong here is every number below it.
    """
    if not isinstance(entry, dict):
        return False
    res = entry.get("result")
    if not isinstance(res, dict):
        return False
    if not res.get("components"):
        return False
    if not ((res.get("cooling") or {}).get("heat_budget")):
        return False
    stored_fp = entry.get("geometry_fingerprint")
    if fp and stored_fp and str(stored_fp) != str(fp):
        return False
    p = entry.get("params") or {}
    for k in _DC_POINT_KEYS:
        if not _dc_close(p.get(k), point.get(k)):
            return False
    for k in _DC_COOLING_KEYS:
        if not _dc_close(p.get(k), cooling.get(k)):
            return False
    return True


def _dc_side_areas(steady: Dict[str, Any], summary: Dict[str, Any],
                   geom: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The AXIAL end-face areas the network re-evaluates its films on.

    The calibration map's OWN measured areas win (``cooling.end_faces.<node>``):
    they are this mesh's section areas times the machine's symmetry and the
    number of exposed ends, i.e. exactly the faces the steady solve put its
    sinks on, so the transient network and the map it is fitted to cannot
    disagree about how much copper is out there.  Only when the payload carries
    no end-face block at all (an older map) are they derived from the summary
    through ``thermal_duty_cycle.end_face_areas``.

    ``None`` when this machine has no exposed ends — a housed machine, or
    ``end_faces='none'`` — and ``None`` is an answer: the four axial paths are
    then absent from the network exactly as they are absent from the map.
    """
    from motor_ai_sim.thermal_duty_cycle import DutyCycleError, end_face_areas

    ef = ((steady.get("cooling") or {}).get("end_faces") or {})
    if str(ef.get("mode") or "off") == "still":
        out: Dict[str, Any] = {"char_len_m": {}, "basis": {
            "source": "the calibration map's own measured end-face areas "
                      "(this mesh's section area x symmetry x exposed ends)",
            "sides": ef.get("sides"), "k_end": ef.get("k_end")}}
        for node, key in (("winding", "winding_ends"), ("stator", "stator_ends"),
                          ("rotor", "rotor_ends"), ("magnet", "magnet_ends")):
            blk = ef.get(node) or {}
            out[key] = float(blk.get("area_m2") or 0.0)
            out["char_len_m"][key] = float(blk.get("char_len_mm") or 0.0) * 1e-3
        if any(float(out[k] or 0.0) > 0.0 for k in
               ("winding_ends", "stator_ends", "rotor_ends", "magnet_ends")):
            return out
    if ef and str(ef.get("mode") or "off") != "still":
        return None
    try:
        n_coils = int(float(geom.get("num_seg") or 0)
                      * float(geom.get("num_slots_per_segment") or 0)) or None
        return end_face_areas(summary,
                              stack_m=float(geom.get("motor_length") or 0.0) * 1e-3,
                              geometry=geom, n_coils=n_coils)
    except DutyCycleError:
        return None


def _dc_cycle_block(trace, *, decimated: Dict[str, Any],
                    converged: Optional[bool], n_cycles: int,
                    residual_k: Optional[float], note: str,
                    hot_offset_k: float) -> Dict[str, Any]:
    """One integrated cycle, as the stored record holds it (B.3)."""
    from motor_ai_sim.thermal_capacities import NODES

    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    out: Dict[str, Any] = {
        "converged": converged,
        "n_cycles": int(n_cycles),
        "residual_K": (None if residual_k is None else round(float(residual_k), 4)),
        "peak_c": {n: round(max(trace.T_c[n]), 2) for n in NODES},
        "min_c": {n: round(min(trace.T_c[n]), 2) for n in NODES},
        "mean_c": {n: round(_mean(trace.T_c[n]), 2) for n in NODES},
        "winding_hot_peak_c": round(max(trace.hot_spot_c), 2),
        "winding_hot_mean_c": round(_mean(trace.hot_spot_c), 2),
        "hot_spot_offset_K": round(float(hot_offset_k), 2),
        "closure_pct": round(trace.closure_pct, 4),
        "energy_in_J": round(trace.energy_in_J, 3),
        "energy_out_J": round(trace.energy_out_J, 3),
        "stored_J": round(trace.stored_J, 3),
        "segments": [[round(a, 4), round(b, 4), nm]
                     for a, b, nm in trace.segments],
        "note": note,
    }
    out.update(decimated)
    return out


@router.post("/duty_cycle")
@_JOBS.queued("thermal.duty_cycle", priority=_JOBS.Priority.DUTY,
              run_id_from=_JOBS.body_run_id("duty"))
def duty_cycle(body: Dict[str, Any] = Body(default_factory=dict),
               authorization: Optional[str] = Header(default=None)
               ) -> Dict[str, Any]:
    """Integrate a duty's CYCLE on a network fitted to one steady map.

    BODY (everything optional)::

        die, config, duty      the catalogued point; omitted = the active
                               family context (config/.family_context.json)
        duty_cycle             the cycle block {kind, ed_pct, cycle_s, …};
                               omitted = the one saved on that duty
        thermal_settings       the Thermal panel's own field names (coolMode,
                               ambientT, emissivity, mountG, mountT, …);
                               omitted = the caller's remembered panel settings.
                               Mapped by `thermal_settings.cooling_fields`,
                               exactly as POST /api/coupled/run maps them
        geo, mat               per-request geometry / material overrides
        n_steps_per_period,    which Electromagnetic RUN the calibration map is
        n_periods, mesh_size_mm,   solved from, and on what mesh — the same
        min_size_mm, n_sectors,    selectors /field takes
        outer_air_factor, component_mesh, magnet_temp_c, mode
        magnet_limit_c         a magnet temperature to judge the cycle against
                               (the cards carry no maximum, so there is no
                               default and the magnets are simply not judged)
        samples_per_segment, ed_curve_step_pct, ed_search
                               the integration's own resolution
        ed_cycle_lengths_s     the periods the ED-vs-cycle-length curve is
                               solved at; omitted (or null) = the module's own
                               [10, 30, 60, 120, 300] s, and an explicit [] is
                               "no curve, just this period"

    THE TOOL FINDS THE REGIME (user 2026-09-15).  ``duty_cycle.ed_pct`` is
    OPTIONAL on an S3: state none and the allowable duty ratio is FOUND, the
    cycle that comes back is the one AT that ratio (``spec.ed_given: false``,
    ``limits.ed_found: true``), and ``limits`` also carries the same answer over
    a span of cycle lengths (``ed_vs_cycle``), every node's temperature at that
    point (``at_allowable``) and the length of one pull from three start states
    (``s2_time_to_limit_s`` from the start temperature, ``s2_from_rated_s`` from
    the calibration map, ``s2_from_cycle_mean_s`` out of the settled cycle).  A
    block that STATES an ed_pct is graded exactly as before, with the allowable
    ratio reported beside it.

    ANSWER: the record of B.3 — ``spec``, ``network`` (conductances, capacities
    and the calibration point they were fitted at), ``cycle`` (the periodic state
    and its decimated series), ``split`` (where the heat went, time-averaged),
    ``limits`` (the S2 times, the allowable ED and the regime it describes) and
    ``point`` —
    plus this router's four: ``elapsed_s``, ``solve_time_s``, ``cached`` (the
    calibration map was reused rather than re-solved) and
    ``geometry_fingerprint``.

    REFUSALS, all 422 with an ``error_code`` and an English sentence:
    ``duty_cycle_no_context``, ``duty_cycle_unknown_duty``,
    ``duty_cycle_missing``, ``no_thermal_boundary``, ``no_electromagnetic_run``,
    ``no_steady_thermal_map``, ``duty_cycle_locked_rotor``,
    ``duty_cycle_mixed_speed``, ``duty_cycle_no_periodic_state``,
    ``duty_cycle_no_capacity``, ``duty_cycle_no_allowable_ed`` (the search found
    no feasible duty ratio at all) and the plain validation names a malformed
    block earns (``duty_cycle_bad_kind``, ``duty_cycle_bad_ed``, …).
    """
    from motor_ai_sim.thermal_capacities import (CapacityError, NODES,
                                                 capacities_j_per_k,
                                                 cp_sources, part_capacities,
                                                 total_capacity_j_per_k)
    from motor_ai_sim.thermal_duty_cycle import (ED_CYCLE_LENGTHS_S,
                                                 DutyCycleError, allowable_ed,
                                                 average_split, decimate,
                                                 default_limits, ed_vs_cycle,
                                                 integrate_profile, map_state_c,
                                                 network_from_steady,
                                                 normalise_spec,
                                                 periodic_steady_state,
                                                 steady_state, time_to_limit)
    from motor_ai_sim.thermal_settings import (cooling_fields, cooling_issue,
                                               thermal_panel_settings)

    t0 = time.time()
    body = dict(body or {})

    # Per-request materials through the BODY, the same transport POST
    # /api/coupled/run uses: the router dependency only ever sees `?mat=`, and a
    # client working on its own copy of a motor posts it.
    if body.get("mat") is not None:
        from motor_ai_sim.material_context import set_request_materials
        from motor_ai_sim.routes.simulation import _parse_mat_override
        ov = _parse_mat_override(body.get("mat"))
        if ov and ov.get("assignment"):
            from motor_ai_sim.materials import (UnknownMaterialError,
                                                validate_assignment)
            try:
                validate_assignment(ov["assignment"],
                                    known_extra=set(ov.get("materials") or ()))
            except UnknownMaterialError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        set_request_materials(ov)

    # ── 1. WHICH MACHINE, WHICH DUTY ────────────────────────────────────────
    from motor_ai_sim.duty_results import active_context
    from motor_ai_sim.routes.family import config_doc, config_duties

    die = str(body.get("die") or "").strip()
    cfg = str(body.get("config") or body.get("configuration") or "").strip()
    duty_name = str(body.get("duty") or "").strip()
    if not (die and cfg and duty_name):
        ctx = active_context()
        if ctx is None:
            raise _dc_refuse(
                "duty_cycle_no_context",
                "this request names no die / configuration / duty and no "
                "machine is loaded in the catalogue, so there is no duty cycle "
                "to solve.",
                "Open a duty in the catalogue, or pass die, config and duty in "
                "the body.", ["duty"])
        die = die or ctx[0]
        cfg = cfg or ctx[1]
        duty_name = duty_name or ctx[2]

    duties = config_duties(die, cfg)
    known = {str(d.get("name") or ""): d for d in duties}
    if duty_name not in known:
        raise _dc_refuse(
            "duty_cycle_unknown_duty",
            "duty %r is not one of the duties of %s / %s (%s)."
            % (duty_name, die, cfg, ", ".join(sorted(known)) or "none saved"),
            "Name one of the duties above, or save this one first.", ["duty"])

    blk = body.get("duty_cycle")
    if not isinstance(blk, dict) or not blk:
        blk = (known[duty_name] or {}).get("duty_cycle")
    if not isinstance(blk, dict) or not blk:
        raise _dc_refuse(
            "duty_cycle_missing",
            "duty %r carries no duty_cycle block and none was sent, so there is "
            "no cycle to integrate." % duty_name,
            "Save a cycle on the duty (S1 continuous, S2 one pull of t_on_s, S3 "
            "ED % of cycle_s, or an explicit segments list), or send one in "
            "duty_cycle.", ["duty_cycle"])
    blk = dict(blk)

    # ── 2. THE BOUNDARY CONDITIONS ──────────────────────────────────────────
    raw = body.get("thermal_settings")
    settings = dict(raw) if isinstance(raw, dict) else dict(
        thermal_panel_settings(authorization))
    issue = cooling_issue(settings)
    if issue is not None:
        raise _dc_refuse(
            "no_thermal_boundary",
            "the Thermal tab's cooling cannot be solved: %s." % issue,
            "Set it on the Thermal tab (or send thermal_settings) and run "
            "again.", ["thermal_settings"])
    cooling = cooling_fields(settings)

    # ── 3. THE CALIBRATION DUTY, AND ITS STEADY MAP ─────────────────────────
    calib = blk.get("calibration_duty") or _dc_rated_duty(duties, duty_name)
    calib_source = ("the cycle's own calibration_duty"
                    if blk.get("calibration_duty") else
                    "the configuration's rated duty (no calibration_duty on the "
                    "cycle)")
    if not calib or str(calib) not in known:
        raise _dc_refuse(
            "duty_cycle_unknown_duty",
            "the calibration duty %r is not one of the duties of %s / %s (%s)."
            % (calib, die, cfg, ", ".join(sorted(known)) or "none saved"),
            "Point calibration_duty at a duty that has been run.",
            ["duty_cycle"])
    calib = str(calib)
    cal_entry = known[calib] or {}
    cal_summary = dict(cal_entry.get("summary") or {})
    if not cal_summary:
        raise _dc_refuse(
            NO_EM_RUN_CODE,
            "the calibration duty %r has no solved summary, so the network has "
            "nothing to be fitted to." % calib,
            "Run that operating point on the Electromagnetic tab and save the "
            "duty again.", ["duty_cycle"])

    geom, geo_ov = _dc_geometry(body.get("geo"))
    fp = _live_fingerprint(geo_ov)

    def _pt(key: str, *alts, default=0.0) -> float:
        for src, k in ((cal_entry, key), *((cal_summary, a) for a in alts)):
            v = (src or {}).get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    break
        return float(default)

    point = {
        "rpm": _pt("rpm", "rpm"),
        "I_phase_rms": _pt("current_arms", "I_phase_rms_A"),
        "gamma_deg": _pt("gamma_deg", "gamma_deg"),
        "coil_temp_c": float(cal_summary.get("coil_temp_C") or 120.0),
    }
    em_sel = {
        "n_steps_per_period": int(_dc_num(body, "n_steps_per_period", 12)),
        "n_periods": _dc_num(body, "n_periods", 1.0),
        "mesh_size_mm": _dc_num(body, "mesh_size_mm", 3.0),
        "min_size_mm": _dc_num(body, "min_size_mm", 0.3),
        "outer_air_factor": _dc_num(body, "outer_air_factor", 1.3),
        "n_sectors": int(_dc_num(body, "n_sectors", 4)),
        "component_mesh": str(body.get("component_mesh") or ""),
    }
    magnet_temp_c = body.get("magnet_temp_c")
    magnet_temp_c = None if magnet_temp_c is None else float(magnet_temp_c)

    _progress.start(
        total=_DC_PROGRESS_TOTAL,
        phase="calibration map — %s" % calib, kind="duty_cycle",
        composition="1 calibration thermal map + 1 duty cycle")

    def _cal_progress(done=None, total=None, phase=None, composition=None):
        """The calibration solve's own two steps, inside this route's three."""
        _progress.update(done=(None if done is None else int(done)),
                         total=_DC_PROGRESS_TOTAL, phase=phase)

    try:
        _load_last()
        steady: Optional[Dict[str, Any]] = None
        cached = False
        for _k in _LAST_KINDS:                   # "field", then "coupled"
            e = _LAST.get(_k)
            if _dc_map_matches(e, point=point, cooling=cooling, fp=fp):
                steady = dict(e["result"])
                cached = True
                break
        if steady is None:
            # ONE conduction solve, at the calibration duty's own saved point,
            # under this request's boundary conditions.  It does not solve an
            # electromagnetic anything: the loss map is the duty's stored RUN,
            # and with no run the 422 below is that lookup's own, by name.
            steady = solve_thermal_field(
                geo=body.get("geo"), op_mode=(body.get("mode")
                                              or cal_entry.get("mode")
                                              or cal_summary.get("op_mode")),
                magnet_temp_c=magnet_temp_c,
                progress=_cal_progress, **point, **em_sel, **cooling)
        _progress.update(done=2, phase="duty cycle — integrating")
        t_cycle = time.time()

        # ── 4. THE NETWORK, THE CAPACITIES AND THE PROFILE ──────────────────
        materials = dict((config_doc(die, cfg) or {}).get("materials") or {})
        materials.update({k: v for k, v in
                          (cal_entry.get("materials") or {}).items() if v})
        part_states = (cal_summary.get("part_states")
                       or (config_doc(die, cfg) or {}).get("parts") or None)
        try:
            caps = part_capacities(cal_summary, materials, part_states)
        except CapacityError as exc:
            raise _dc_refuse(
                "duty_cycle_no_capacity", str(exc),
                "A duty cycle is an answer about TIME, and time needs the "
                "machine's heat capacity.", ["duty"])

        side_areas = _dc_side_areas(steady, cal_summary, geom)
        d_housing_m = float(geom.get("stator_diameter") or 0.0) * 1e-3
        try:
            net = network_from_steady(
                steady,
                mount_g_w_per_k=float(cooling.get("mount_g_w_per_k") or 0.0),
                mount_temp_c=cooling.get("mount_temp_c"),
                # `or` would read a 0 °C room as "not given", and a joint in a
                # cold store is a machine somebody has.
                t_ambient_c=float(cooling["ambient_temp"]
                                  if cooling.get("ambient_temp") is not None
                                  else (steady.get("ambient_temp")
                                        if steady.get("ambient_temp") is not None
                                        else 25.0)),
                side_areas=side_areas, d_housing_m=d_housing_m,
                emissivity=cooling.get("emissivity"),
                # The GEOMETRY is what a link the map cannot fit falls back on
                # (2026-09-15): the air gap and the magnet root have closed-form
                # conductances, and using them instead of merging the nodes is
                # worth 9 s of S2 on the winding and 23 K on the magnets.  With
                # no geometry the merge is all there is, and the network says so.
                geometry=geom,
                calibration_duty=calib)
            profile = normalise_spec(
                blk, duties, thermal_by_duty={calib: steady},
                default_duty=calib)
        except DutyCycleError as exc:
            raise _dc_from_error(exc)

        samples = max(8, min(int(_dc_num(body, "samples_per_segment", 60)), 400))
        limits_c = default_limits(body.get("magnet_limit_c"))
        ed_search = bool(body.get("ed_search", True))
        # An explicit EMPTY list is an answer ("no curve, just the one period"),
        # so it is honoured rather than falling back to the default span.
        _lengths = body.get("ed_cycle_lengths_s")
        cycle_lengths = (list(_lengths) if isinstance(_lengths, (list, tuple))
                         else list(ED_CYCLE_LENGTHS_S))

        # ── 5a. THE REGIME THE MACHINE CAN HOLD ─────────────────────────────
        # THE TOOL FINDS THE REGIME (user 2026-09-15): «мы сами находим это
        # время / S3 ED, при котором всё нормально».  So the allowable duty
        # ratio is solved FIRST and the cycle that is then integrated and drawn
        # is the one at THAT ED — unless the request stated an ED of its own, in
        # which case it is graded as before and the found one is reported
        # beside it.
        ed: Optional[Dict[str, Any]] = None
        ed_by_cycle: List[Dict[str, Any]] = []
        try:
            if profile.kind == "S3":
                if not ed_search and not profile.ed_given:
                    raise _dc_refuse(
                        "duty_cycle_bad_ed",
                        "this cycle states no duty ratio and the ED search was "
                        "switched off (ed_search: false), so there is no ED to "
                        "integrate and none to find.",
                        "Give ed_pct, or leave the search on and the allowable "
                        "ED is found for you.", ["duty_cycle"])
                if ed_search:
                    _progress.update(done=2,
                                     phase="duty cycle — finding the allowable "
                                           "duty ratio")
                    ed = allowable_ed(
                        profile, net, caps, limits=limits_c,
                        curve_step_pct=_dc_num(body, "ed_curve_step_pct", 5.0),
                        samples_per_segment=max(8, min(samples, 40)))
                    # …and the same answer over a SPAN of periods: an ED means
                    # nothing without the cycle length it is a ratio of.
                    ed_by_cycle = ed_vs_cycle(
                        profile, net, caps, limits=limits_c,
                        cycle_lengths=cycle_lengths,
                        samples_per_segment=max(8, min(samples, 24)))
                if not profile.ed_given:
                    found = (ed or {}).get("ed_allowable_pct")
                    if not found:
                        raise _dc_refuse(
                            "duty_cycle_no_allowable_ed",
                            "no duty ratio is allowable at this operating "
                            "point: even the shortest pull this search tries "
                            "puts the %s over its limit."
                            % ((ed or {}).get("limiting_part") or "winding"),
                            "Lower the current, lengthen the cycle, raise the "
                            "mount conductance or cool the machine harder.",
                            ["duty_cycle"])
                    # The cycle that is integrated, drawn and stored is the
                    # FOUND one.  `ed_given` rides along as False, so nothing
                    # downstream can read it as a ratio the user chose.
                    profile = profile.with_ed(float(found))
        except DutyCycleError as exc:
            raise _dc_from_error(exc)

        # ── 5b. THE CYCLE ───────────────────────────────────────────────────
        try:
            _progress.update(done=2, phase="duty cycle — integrating")
            if profile.kind == "S2":
                # An S2 pull runs ONCE: there is no periodic state to reach, and
                # answering with one would be answering a different question.
                trace = integrate_profile(profile, net, caps, n_cycles=1,
                                          samples_per_segment=samples)
                cycle = _dc_cycle_block(
                    trace, decimated=decimate(trace, DUTY_CYCLE_MAX_SAMPLES),
                    converged=None, n_cycles=1, residual_k=None,
                    hot_offset_k=net.hot_spot_offset_k,
                    note=("S2 is one pull from the start temperature and is "
                          "never repeated, so there is no periodic state — the "
                          "series is that single pull."))
            elif profile.kind == "S1":
                # CONTINUOUS duty: the answer is where it SETTLES, and the
                # series is the machine sitting there.  Marched to the settled
                # state rather than iterated as a one-second "cycle" — the cycle
                # map of a segment far shorter than the time constant would take
                # hundreds of iterations to say what one march says once.
                settled = steady_state(profile.segments[0], net, caps)
                trace = integrate_profile(profile, net, caps, settled,
                                          n_cycles=1,
                                          samples_per_segment=samples)
                cycle = _dc_cycle_block(
                    trace, decimated=decimate(trace, DUTY_CYCLE_MAX_SAMPLES),
                    converged=True, n_cycles=1, residual_k=None,
                    hot_offset_k=net.hot_spot_offset_k,
                    note=("S1 is continuous duty: the machine was marched to "
                          "its SETTLED temperature and the series is one "
                          "segment there, so peak = mean by construction."))
                cycle["start_state_c"] = {n: round(float(settled[n]), 3)
                                          for n in NODES}
            else:
                rec = periodic_steady_state(profile, net, caps,
                                            samples_per_segment=samples)
                trace = rec.pop("trace")
                cycle = _dc_cycle_block(
                    trace, decimated=decimate(trace, DUTY_CYCLE_MAX_SAMPLES),
                    converged=bool(rec["converged"]),
                    n_cycles=int(rec["n_cycles"]),
                    residual_k=rec["residual_K"],
                    hot_offset_k=net.hot_spot_offset_k,
                    note=("the cycle map's fixed point: the series is one REAL "
                          "cycle integrated from the converged start state, "
                          "never an extrapolated one."))
                cycle["start_state_c"] = rec["start_state_c"]

            split = average_split(trace, net)

            # ── 6. THE LIMITS ───────────────────────────────────────────────
            limits: Dict[str, Any] = {
                "winding_limit_c": limits_c["winding"],
                "winding_limit_note": (
                    "the project's insulation class, judged on the HOT SPOT — "
                    "the winding mean plus the calibration map's own max - mean "
                    "offset of %.1f K, held constant (stated approximation)"
                    % net.hot_spot_offset_k),
                "magnet_limit_c": limits_c.get("magnet"),
                "magnet_limit_note": (
                    "" if limits_c.get("magnet") is not None else
                    "the magnet cards carry no maximum operating temperature, "
                    "so the magnets are NOT judged here; pass magnet_limit_c to "
                    "judge them."),
                "limits_c": {k: float(v) for k, v in limits_c.items()},
            }
            s2 = time_to_limit(profile, net, caps, limits=limits_c)
            limits.update({
                "s2_time_to_limit_s": s2["s2_time_to_limit_s"],
                "s2_limiting_part": s2["s2_limiting_part"],
                "s2_horizon_s": s2["t_horizon_s"],
                "s2_end_state_c": s2["end_state_c"],
                "s2_winding_hot_end_c": s2["winding_hot_end_c"],
                "s2_note": s2["note"],
            })

            # THE SAME PULL FROM A WARM MACHINE.  "How long may it pull" has as
            # many answers as the machine has start states, and the one an
            # engineer is sold on (from cold) is the longest of them.  Two more,
            # both free — neither costs a FEM pass, only an integration on the
            # network that is already fitted:
            #   * from RATED — the calibration duty's own settled map, i.e. the
            #     joint that has been holding its rated point all morning;
            #   * from the CYCLE MEAN — the settled S3 above, i.e. one extra
            #     pull out of the regime the machine already lives in.
            cal_state = map_state_c(steady)
            if cal_state:
                s2r = time_to_limit(profile, net, caps, limits=limits_c,
                                    T0=cal_state)
                limits.update({
                    "s2_from_rated_s": s2r["s2_time_to_limit_s"],
                    "s2_from_rated_part": s2r["s2_limiting_part"],
                    "s2_from_rated_start_c": {k: round(float(v), 2)
                                              for k, v in cal_state.items()},
                    "s2_from_rated_note": (
                        "the same pull, started from the calibration duty %r's "
                        "own steady map (winding %.0f °C) instead of from the "
                        "start temperature — what the machine has left when it "
                        "has already been working.  %s"
                        % (calib, cal_state.get("winding", 0.0), s2r["note"])),
                })
            else:
                limits.update({
                    "s2_from_rated_s": None, "s2_from_rated_part": None,
                    "s2_from_rated_start_c": None,
                    "s2_from_rated_note": (
                        "the calibration map carries no node mean temperatures, "
                        "so there is no rated state to start a pull from."),
                })
            if profile.kind == "S3":
                mean_state = {n: float(v) for n, v
                              in (cycle.get("mean_c") or {}).items()
                              if v is not None}
                s2m = time_to_limit(profile, net, caps, limits=limits_c,
                                    T0=mean_state)
                limits.update({
                    "s2_from_cycle_mean_s": s2m["s2_time_to_limit_s"],
                    "s2_from_cycle_mean_part": s2m["s2_limiting_part"],
                    "s2_from_cycle_mean_start_c": {
                        k: round(float(v), 2) for k, v in mean_state.items()},
                    "s2_from_cycle_mean_note": (
                        "the same pull, started from the MEAN state of the "
                        "settled cycle below — one extra pull out of the regime "
                        "the machine already lives in.  %s" % s2m["note"]),
                })
            else:
                limits.update({
                    "s2_from_cycle_mean_s": None,
                    "s2_from_cycle_mean_part": None,
                    "s2_from_cycle_mean_start_c": None,
                    "s2_from_cycle_mean_note": (
                        "there is no settled cycle to start from: this is an %s "
                        "point, not an intermittent one." % profile.kind),
                })

            # THE FOUND REGIME.  `ed` was solved in 5a, before the cycle, and
            # when the request stated no ED the cycle above IS the one at
            # `ed_allowable_pct` — `ed_found` says which of the two happened.
            if ed is not None:
                limits.update({
                    "ed_allowable_pct": ed["ed_allowable_pct"],
                    "ed_requested_pct": ed["ed_requested_pct"],
                    "ed_limiting_part": ed["limiting_part"],
                    "ed_curve": ed["ed_curve"],
                    "ed_note": ed["note"],
                    "at_allowable": ed.get("at_allowable"),
                    "limiting_part": ed.get("limiting_part"),
                    "ed_vs_cycle": ed_by_cycle,
                    "ed_cycle_s": round(float(profile.cycle_s), 4),
                    "ed_found": not profile.ed_given,
                    "ed_found_note": (
                        "the cycle integrated, drawn and stored below is the "
                        "one at the ALLOWABLE duty ratio — this request asked "
                        "for none, so the tool found it."
                        if not profile.ed_given else
                        "the cycle below is the one that was ASKED for; the "
                        "allowable ratio beside it is what this machine would "
                        "hold at the same cycle length."),
                })
            else:
                limits.update({
                    "ed_allowable_pct": None,
                    "ed_requested_pct": (profile.ed_pct if profile.ed_given
                                         else None),
                    "ed_limiting_part": None, "ed_curve": [],
                    "at_allowable": None, "limiting_part": s2["s2_limiting_part"],
                    "ed_vs_cycle": [], "ed_cycle_s": None, "ed_found": False,
                    "ed_found_note": "",
                    "ed_note": ("an allowable duty ratio only means something "
                                "for an S3 (intermittent) cycle; this one is %s."
                                % profile.kind
                                if profile.kind != "S3" else
                                "the ED search was switched off for this "
                                "request (ed_search: false)."),
                })
        except DutyCycleError as exc:
            raise _dc_from_error(exc)

        # ── 7. THE RECORD ───────────────────────────────────────────────────
        import datetime as _dt
        network = net.as_record()
        network.update({
            "C_J_per_K": capacities_j_per_k(caps),
            "C_total_J_per_K": round(total_capacity_j_per_k(caps), 3),
            "cp_sources": cp_sources(caps),
            "capacity_parts": {n: list(caps[n]["parts"]) for n in NODES},
            "calibration_duty": calib,
            "active_nodes": list(net.active_nodes),
            "side_area_basis": (side_areas or {}).get("basis"),
        })
        record: Dict[str, Any] = {
            "kind": "duty_cycle",
            "computed_at": _dt.datetime.now(_dt.timezone.utc)
                              .isoformat(timespec="seconds"),
            "die": die, "configuration": cfg, "duty": duty_name,
            "spec": {
                "kind": profile.kind,
                "cycle_s": round(profile.cycle_s, 4),
                "duration_s": round(profile.duration_s, 4),
                # the ED that was INTEGRATED — the found one when the request
                # stated none, and `ed_given: false` is what says so
                "ed_pct": profile.ed_pct,
                "ed_given": profile.ed_given,
                "t_on_s": profile.t_on_s,
                "rest_duty": profile.rest_duty,
                "calibration_duty": calib,
                "calibration_source": calib_source,
                "t_start_c": profile.t_start_c,
                "n_cycles_max": profile.n_cycles_max,
                "note": profile.note,
                "segments": [
                    {"duty": s.name, "t_s": round(s.t_s, 4), "rpm": s.rpm,
                     "coil_ref_c": s.coil_ref_c,
                     "losses_W": {n: round(float(s.losses.get(n, 0.0)), 4)
                                  for n in NODES},
                     "total_W": round(s.total_W, 4), "note": s.note}
                    for s in profile.segments],
            },
            "network": network,
            "cycle": cycle,
            "split": split,
            "limits": limits,
            "point": {
                **{k: round(float(v), 4) for k, v in point.items()},
                **{k: v for k, v in cooling.items()},
                "magnet_temp_c": magnet_temp_c,
                "calibration_duty": calib,
                "calibration_source": calib_source,
                **{k: em_sel[k] for k in ("n_steps_per_period", "n_periods",
                                          "mesh_size_mm", "min_size_mm",
                                          "n_sectors")},
            },
            "calibration_map": {
                "loss_source": steady.get("loss_source"),
                "T_max": steady.get("T_max"),
                "P_loss_total_W": steady.get("P_loss_total_W"),
                "components": {n: (steady.get("components") or {}).get(n)
                               for n in NODES},
                "heat_budget": ((steady.get("cooling") or {})
                                .get("heat_budget") or {}),
                "reused": cached,
            },
            "elapsed_s": _elapsed(t0),
            "solve_time_s": round(time.time() - t_cycle, 3),
            "cached": cached,
            "geometry_fingerprint": fp,
        }
        _remember_last("duty_cycle", record,
                       {"die": die, "config": cfg, "duty": duty_name,
                        "duty_cycle": blk, "thermal_settings": settings,
                        **point, **cooling, **em_sel},
                       fp, die=die, cfg=cfg, duty=duty_name)
        return record
    finally:
        _progress.finish()


@router.get("/duty_cycle/last")
def duty_cycle_last(geo: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """The most recent duty-cycle answer, with its staleness flagged.

    200 with ``has_result: false`` rather than a 404, the same contract
    ``/last`` keeps: a tab that has never solved a cycle is the NORMAL first
    state, and a console full of red 404s on every mount is not an error report.
    """
    from motor_ai_sim.routes._validation import parse_geo_override

    _load_last()
    live = _live_fingerprint(parse_geo_override(geo))
    e = _LAST.get("duty_cycle")
    out: Dict[str, Any] = {"live_geometry_fingerprint": live,
                           "has_result": False, "duty_cycle": None}
    if not e or e.get("result") is None:
        return out
    fp = e.get("geometry_fingerprint")
    out["has_result"] = True
    out["duty_cycle"] = {
        "result": e["result"], "params": e.get("params") or {},
        "geometry_fingerprint": fp, "computed_at": e.get("computed_at"),
        "stale_geometry": (None if (not fp or not live or live == "nofp")
                           else bool(fp != live)),
    }
    return out


def _run_coro(coro):
    """Drive an ``async def`` from a sync route body.

    ``routes.simulation.build_fem_mesh_2d_sliding_band`` is the mesher this route
    must reuse (reimplementing it would be a second mesh that only LOOKS like the
    solved one), and it is declared async although it never awaits.  This route
    stays sync so FastAPI runs it in the threadpool rather than blocking the
    event loop with a gmsh build.
    """
    import asyncio
    import contextvars as _cv
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures as _cf
    # Carry this request's context (the warm-seed switch above, the material
    # override) into the helper thread — a bare submit would start it empty.
    _ctx = _cv.copy_context()
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:      # pragma: no cover
        return ex.submit(_ctx.run, asyncio.run, coro).result()


@router.get("/mesh")
def mesh(
    mesh_size_mm:     float = Query(default=3.0, gt=0.0, le=50.0),
    min_size_mm:      float = Query(default=0.3, gt=0.0, le=50.0),
    outer_air_factor: float = Query(default=1.3, ge=1.0, le=10.0),
    n_sectors:        int = Query(default=4, ge=-1, le=64),
    component_mesh:   str = Query(default=""),
    geo:              Optional[str] = Query(default=None),
):
    """The SOLID sub-mesh the thermal solve runs on — no solve, no fields.

    What the tab draws before anything has been computed.  It is the EM mesh,
    from the same builder and the same mesh parameters ``/field`` feeds the field
    route (including the coil auto-refinement, which is part of the thermal mesh
    and not a cosmetic), with the air / gap / band / outer-ring domains dropped
    exactly as ``solve_steady_thermal(drop_tags=...)`` drops them and the
    coincident sliding-band nodes welded the same way.

    That last part is what makes it worth a route rather than a client-side
    filter: the thermal mesh is NOT the EM mesh minus some triangles, it is that
    minus the air with the non-conforming gap interface welded back together —
    the step that reconnects the rotor to the cooled housing.  A picture that
    skipped it would show a rotor floating in a hole.
    """
    import numpy as np

    from motor_ai_sim.routes.simulation import (_parse_geo_override,
                                                _outlines_from_polys,
                                                build_fem_mesh_2d_sliding_band)

    # One step: the gmsh build.  A cache hit starts AND finishes the bar (the
    # finally below), so a poll landing just after an instant answer sees
    # running=False rather than the stale True of whatever ran before it.
    _progress.start(total=1, phase="mesh build", kind="mesh",
                    composition="one gmsh mesh build")
    try:
        t0 = time.time()
        geo_ov = _parse_geo_override(geo)
        if float(min_size_mm) > float(mesh_size_mm):
            raise _bad("min_size_mm", min_size_mm, "bad_value",
                       "min_size_mm (%.3f) is larger than mesh_size_mm (%.3f): the "
                       "floor cannot be coarser than the target"
                       % (float(min_size_mm), float(mesh_size_mm)))

        cm = _auto_coil_mesh(component_mesh, mesh_size_mm, geo_ov)
        ns_eff = _snap_n_sectors(n_sectors, geo_ov)
        fp = _live_fingerprint(geo_ov)
        key = (fp, round(float(mesh_size_mm), 4), round(float(min_size_mm), 4),
               round(float(outer_air_factor), 3), int(ns_eff), str(cm))
        hit = _MESH_CACHE.get(key)
        if hit is not None:
            out = dict(hit)
            out["cached"] = True
            # `mesh_s` stays the seconds the mesh actually COST when it was built —
            # a cache hit did not make gmsh faster.
            out["elapsed_s"] = _elapsed(t0)
            return out

        _t_mesh = time.time()
        try:
            em = _run_coro(build_fem_mesh_2d_sliding_band(
                rotor_angle_deg=0.0, mesh_size_mm=float(mesh_size_mm),
                min_size_mm=float(min_size_mm),
                outer_air_factor=float(outer_air_factor),
                # The remaining flags are the ones the FIELD solve uses (see
                # `_fem_field2d_impl` / `fem_transient_sliding_band`): the merged
                # structured belt, template iron and the geometry-driven mesh.  They
                # are not exposed as query params here for exactly that reason —
                # a preview built with different flags would be a different mesh.
                band_thickness_mm=0.4, gap_layers=2.0, n_sectors=int(ns_eff),
                stator_fillet_mm=0.0, component_mesh=cm,
                surface_deviation=0.005, normal_deviation=8.0, aspect_ratio=10.0,
                pole_copy=False, iron_template=True, geo_mesh=True,
                hi_fidelity=False, structured_gap=True, geo=geo))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("thermal mesh build failed")
            raise HTTPException(status_code=500,
                                detail=f"{type(exc).__name__}: {exc}")
        mesh_s = round(time.time() - _t_mesh, 3)

        verts = np.asarray(em["vertices"], float)
        tris = np.asarray(em["triangles"], int)
        tags = np.asarray(em["domain_per_tri"], int)

        # ── the SAME re-tag /field does ─────────────────────────────────────
        # The preview must be the mesh the solve will colour in, and since
        # 2026-09-07 that includes the named air: a cross-section drawn with the
        # slot white and the gap white would still be the picture the user
        # complained about, and pressing Solve would change the geometry under
        # them rather than only filling it with temperatures.
        try:
            polys = _thermal_polys(geo, fp)
        except Exception as exc:  # noqa: BLE001
            log.warning("thermal mesh: cross-section polygons unavailable (%s)", exc)
            polys = {}
        _g = _mesh_radii(geo_ov)
        _solid = ~np.isin(tags, (DOM_AIR, DOM_AIRGAP, DOM_BAND, DOM_OUTER,
                                 DOM_TPL_LINER, DOM_TPL_ENAMEL))
        if _solid.any():
            _sn = np.unique(tris[_solid])
            _r_in = float(np.hypot(verts[_sn, 0], verts[_sn, 1]).min())
        else:
            _r_in = 0.0
        tags, air_report = _retag_thermal_domains(
            verts, tris, tags, polys, r_housing_m=_g["r_housing_m"],
            stator_inner_m=_g["stator_inner_m"],
            rotor_outer_m=_g["rotor_outer_m"], r_inner_solid_m=_r_in)

        # ── the SAME drop + weld solve_steady_thermal does ───────────────────────
        keep = ~np.isin(tags, np.asarray(DROP_TAGS, int))
        t_keep = tris[keep]
        tags_keep = tags[keep]
        if t_keep.size == 0:
            raise HTTPException(
                status_code=422,
                detail={"error": "no solid elements left after dropping the air "
                                 "domains — nothing to conduct heat through",
                        "invalid_parameters": []})
        used = np.unique(t_keep)
        remap = -np.ones(verts.shape[0], int)
        remap[used] = np.arange(used.size)
        p_sub = verts[used]
        t_sub = remap[t_keep]
        # WELD coincident nodes: the sliding band is non-conforming (rotor-side and
        # stator-side duplicates at the gap interface), and without the weld the
        # rotor is a disconnected island — which is exactly what the solve fixes and
        # therefore what the preview must show.
        keyc = np.round(p_sub * 1e6).astype(np.int64)
        _, first, inv = np.unique(keyc, axis=0, return_index=True, return_inverse=True)
        p_weld = p_sub[first]
        t_weld = inv[t_sub]
        nondegen = ((t_weld[:, 0] != t_weld[:, 1]) & (t_weld[:, 1] != t_weld[:, 2])
                    & (t_weld[:, 0] != t_weld[:, 2]))
        t_weld = t_weld[nondegen]
        tags_keep = tags_keep[nondegen]

        try:
            polys, _motor, _ov = _live_polys(geo)
            outlines = _outlines_from_polys(polys)
        except Exception:  # noqa: BLE001 - an outline is a decoration, not the answer
            outlines = []

        out = {
            "vertices": p_weld.tolist(),
            "triangles": t_weld.tolist(),
            "domain_per_tri": tags_keep.astype(int).tolist(),
            "part_names": {str(k): v for k, v in PART_NAMES.items()},
            "air_domains": air_report,
            "outlines": outlines,
            "extent": [float(p_weld[:, 0].min()), float(p_weld[:, 0].max()),
                       float(p_weld[:, 1].min()), float(p_weld[:, 1].max())],
            "n_vertices": int(p_weld.shape[0]),
            "n_triangles": int(t_weld.shape[0]),
            "mesh_size_mm": float(mesh_size_mm),
            "n_sectors": int(ns_eff),
            "mesh_s": mesh_s,
            "cached": False,
            "geo_fingerprint": fp,
        }
        out["elapsed_s"] = _elapsed(t0)
        _cache_put(_MESH_CACHE, key, out, _MESH_CACHE_MAX)
        return out
    finally:
        # Unconditional: an exception on any path must not leave
        # the progress endpoint reporting a live solve.
        _progress.finish()
