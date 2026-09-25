"""The EM ↔ thermal ORCHESTRATOR — /api/coupled.

WHY A THIRD ROUTER (user, 2026-09-08: *"не надо всё смешивать, нужен
оркестратор"* and *"чтобы можно было его включать и отключать"*)
=========================================================================
The Electromagnetic solve needs two temperatures it cannot compute — the copper's
(``coil_temp_c``, through ρ_Cu(T)) and the magnet's (``magnet_temp_c``, through
Br(T) and the demagnetisation knee, phase 1).  The Thermal solve computes both,
from a loss map only the Electromagnetic solve can produce.  That is a fixed
point, and there are exactly three ways to close it:

  1. let the Thermal tab start an EM solve — REFUSED on 2026-09-07, and the
     refusal is a standing rule: ``routes.thermal`` answers 422
     ``no_electromagnetic_run`` rather than run one;
  2. let the Electromagnetic tab start a thermal solve — the same mistake with
     the arrows reversed;
  3. put a THIRD thing above both, which owns the loop and calls each solver
     through its own public entry point.

This module is (3).  It imports ``routes.simulation.get_fem_transient`` and
``routes.thermal.solve_thermal_field`` and calls them exactly as their own routes
do; NEITHER solver learns anything about the other, and with the toggle off
nothing in this file is reachable at all — today's behaviour, bit for bit.

WHAT ONE ITERATION IS
---------------------
    EM run at (T_coil, T_magnet, T_bearing)  → the loss map, stored as THE last run
    thermal solve at the same three          → the temperature field it produces
    T_coil'  = winding AVERAGE        → the bulk ρ_Cu(T) the EM solve means
    T_magnet' = magnet  AVERAGE       → the bulk Br(T) the magnet card is moved to
    T_bearing' = the SHAFT at its ends → the grease viscosity the SKF model means
    under-relax:  T ← T + damping·(T' − T)

The winding AVERAGE and not its maximum, because ``coil_temp_c`` scales a bulk
resistivity over the whole winding; the magnet AVERAGE for the same reason, while
the magnet MAXIMUM is carried in the history for the demagnetisation check and is
deliberately NOT fed back — a knee check is about the hottest element, a Br is
about the body.

THE THIRD TEMPERATURE (2026-09-08).  User: *"когда запускается каплинг, должно
решаться всё моделирование, и все потери должны передаваться в электромагнитный
расчёт"*.  The bearings and the rotor windage are ANALYTIC — the SKF frictional
moment and Couette/disc drag (``motor_ai_sim.bearings``) — and they are a
temperature-dependent loss like any other: M_rr goes as ν^0.6, and grease quoted
at 40 °C running at 90 °C is a factor of two on the rolling term.  So each pass
takes the bearing temperature off the PREVIOUS pass's map (the shaft's mean
temperature at the exposed ends, else the shaft average — ``mech_losses.
bearing_temp_from_map``), and that temperature drives BOTH halves of the next
pass: the mechanical loss fields the electromagnetic summary carries, and the
friction heat the thermal solve puts into the shaft.  It is NOT part of the
convergence test — that stays on the winding and the magnet, the two temperatures
that change the field — but it is reported per iteration and at the top, because
a mechanical loss whose temperature nobody can see is a number nobody can argue
with.  It cannot destabilise the loop either: the friction is a source the
winding barely feels, and its own temperature follows the shaft that the loop is
already driving to a fixed point.

THE LAST EM RUN IS THE REPORTED ONE
-----------------------------------
Every iteration BEGINS with the electromagnetic run and the damped update is
applied only when another iteration will consume it, so the pair this route
reports is always the pair the last run — and the thermal map stored beside it —
were actually solved at.  That is the whole reason to be careful here: the panel
writes the reported temperature straight back into the Electromagnetic tab's own
field, and a headline one step ahead of the cards underneath it is a number
nobody can reproduce by pressing Run.  What the loop owes instead is honesty
about the gap: ``residual_coil_K`` / ``residual_magnet_K`` say how far the last
thermal answer sat from the temperature it was solved at, and ``converged`` says
whether that is inside ``tol_K``.

WHAT IS RECORDED
----------------
``coupling`` inside the LAST TRANSIENT'S SUMMARY — the iteration history, the
converged pair and the tolerance — so the Electromagnetic tab's summary can say
"winding 128 °C · magnets 163 °C · 3 it." beside the numbers it belongs to, and
so a run that was NOT coupled carries no such block and reads exactly as it
always did.  The same block, plus the parameters, is the answer of
``GET /api/coupled/last``.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import os
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from motor_ai_sim import coupled_continuous_rating as _ccr
from motor_ai_sim import coupled_duty_cycle as _cdc
from motor_ai_sim import coupled_time_to_limit as _ttl
from motor_ai_sim import run_recording as _rr
from motor_ai_sim import run_history as _RH
from motor_ai_sim import workspace as _WSP
from motor_ai_sim import jobs as _JOBS
from motor_ai_sim.progress import poll as _progress_poll
from motor_ai_sim.progress import route_progress as _route_progress

log = logging.getLogger(__name__)


async def _material_override_dep(mat: Optional[str] = Query(default=None)):
    """``?mat=`` for this request, the SAME contract as the simulation, thermal
    and mechanical routers.

    Both halves of the loop read the material assignment — the EM solve for the
    magnet grade and the steel, the thermal solve for the conductivities — so a
    client working on its own copy of a motor must get its own materials on both
    without either of them touching the shared config.  The POST body may carry
    ``mat`` instead (the kernel's transport, which the dependency never sees);
    the run handler applies that one itself, exactly as ``get_fem_transient``
    does.
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


router = APIRouter(prefix="/api/coupled", tags=["coupled"],
                   dependencies=[Depends(_material_override_dep)])

#: Default convergence band, KELVIN.  Both temperatures must be inside it.  2 K
#: is not a solver tolerance, it is an ENGINEERING one: 2 K of copper is 0.8 % of
#: ρ_Cu and 2 K of magnet is 0.25 % of Br — below the spread between two nominally
#: identical magnets, and far below what the cooling boundary condition is known
#: to.  A tighter band would buy digits, not confidence, at one whole EM
#: transient each.
DEFAULT_TOL_K = 2.0
#: Under-relaxation.  The loop's gain is well below 1 (more copper loss → a hotter
#: winding → more copper loss, but the thermal resistance divides it), so a plain
#: substitution converges — and on the first live run (2026-09-08, Ø200 at
#: 461.7 A) the thermal answer was the SAME 108.4 / 126.3 °C from iteration 2
#: on while a fixed 0.5 crept toward it for five transients (23 min).  So the
#: default is 1.0 (plain substitution) and the loop HALVES its own step the
#: moment a correction grows or flips sign — the two signatures of a gain that
#: is not small.  A caller may still pin `damping` explicitly.
DEFAULT_DAMPING = 1.0
#: The damping the loop falls back to when the iteration oscillates or grows.
DAMPING_ON_OSCILLATION = 0.5

#: How far the bearing seat temperature the last run was billed at may sit
#: from the seat the final map produces, for the loop to count as converged
#: (K).  Looser than `tol_K` on purpose: friction goes as ν^0.6, so 5 K is a
#: few per cent of the bearing watts — while the 79 K a "converged" pass-1 loop
#: used to carry was a third of them (reviewer 2026-09-13).
BEARING_TOL_K = 5.0
DEFAULT_MAX_ITER = 6
#: Past this the loop is not converging on anything (mirrors
#: ``routes.thermal.RUNAWAY_C``, read from there so the two cannot drift).
_RUNAWAY_FALLBACK_C = 400.0


# ---------------------------------------------------------------------------
# Live progress + cancellation
# ---------------------------------------------------------------------------
# ONE tracker for the LOOP.  The EM run and the thermal solve keep their own
# (``simulation._fem_transient_progress`` and ``thermal._progress``), so the two
# existing strips still show what their own solver is doing frame by frame; this
# one answers the question neither of them can — which iteration of how many.
# Migration Stage 4: one tracker per RUN.  The name and every call site are
# unchanged; ``RouteProgress`` resolves to this run's tracker, or to this
# router's per-workspace default when the call is not inside a job.
_progress = _route_progress("coupled")

#: Cooperative cancel, keyed by run-id exactly as the transient's is: the Stop
#: button knows which run it means, and cancelling one loop must never kill the
#: next.  Set by ``POST /cancel``; read between phases and forwarded into the EM
#: solve's own registry so a cancel that arrives mid-transient is honoured by the
#: frame march instead of waiting for it to finish.
#:
#: Migration Stage 4: it is no longer a one-slot dict but the process-wide
#: run-id registry in ``motor_ai_sim.jobs`` — a MAP, so a second account's
#: cancel cannot stop this loop, with the ownership check at the endpoint.

_LOCK = threading.Lock()


@router.get("/progress")
def progress(run_id: str = "") -> Dict[str, Any]:
    """Which iteration of how many, and which half of it is running.

    Same payload as the transient's and the thermal router's progress endpoints
    plus ``kind`` (always ``coupled``), so the frontend's one strip component
    serves this too.

    Deliberately NOT gated (see ``auth._GATED``), like the other two progress
    routes: the SOLVE is gated, its counter is a status read, and a bar that
    401s over a running solve is the one moment the user most needs to see
    something.

    ``?run_id=`` answers THAT loop (Stage 4); with no argument, the caller's
    newest — which is what today's strip polls.
    """
    return _progress_poll("coupled", run_id)


@router.post("/cancel")
def cancel(run_id: str = "") -> Dict[str, Any]:
    """Ask the loop with this run-id to stop.

    ONE registry now, and it is the process-wide one in ``motor_ai_sim.jobs``:
    the loop checks it between phases and the frame march inside the running EM
    solve checks the same map, so a cancel that arrives mid-transient is honoured
    by the march instead of waiting six minutes for it to finish.  (It used to be
    two one-slot dicts that had to be set in step; forgetting either was a Stop
    button that did nothing, or one that killed one EM run and let the loop start
    the next.)

    Migration Stage 4: the caller must OWN the run — or be an admin.  ``403``
    otherwise, because with several accounts a cancel is the one status write
    that can destroy somebody else's afternoon.
    """
    if not run_id:
        return {"cancelled": False, "run_id": ""}
    try:
        out = _JOBS.cancel_run(run_id)
    except _JOBS.NotOwner:
        raise HTTPException(status_code=403,
                            detail="that run belongs to another account")
    return {"cancelled": bool(out.get("cancelled")), "run_id": run_id}


class _LoopCancelled(BaseException):
    """Raised between phases when this loop's run-id was cancelled.

    ``BaseException`` for the same reason ``simulation._RunCancelled`` is: a
    cancellation is a REQUEST, not an error, and the paths it travels through are
    full of ``except Exception`` guards that exist to stop a broken callback from
    killing a solve.
    """


def _check_cancelled(run_id: str) -> None:
    if run_id and _JOBS.is_cancelled(run_id):
        raise _LoopCancelled()


# ---------------------------------------------------------------------------
# The last coupled run, kept across tab switches and backend restarts
# ---------------------------------------------------------------------------
# Same bargain as ``routes.thermal._LAST`` and ``routes.mechanical._LAST``, and
# cheap enough to be JSON: a coupled answer is a handful of scalars and one row
# per iteration — no meshes, no per-node arrays — so it does not need the pickle
# the other two use.

#: Migration Stage 3: per WORKSPACE.  Unlike the other two ``_LAST`` stores
#: this one is FLAT — the coupled answer's own keys, a few dozen of them — so
#: the cap is set far above any answer's key count and exists only so a store
#: that somehow kept growing could not take the server down with it.
_LAST_MAX = 512
_LAST = _WSP.ws_map("coupled.last", _LAST_MAX)
_SLOT_LAST_LOADED = "coupled.last_loaded"
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


def __getattr__(name):
    """``_LAST_LOADED`` survives as a NAME — per workspace, overridable by a
    plain assignment for the tests that already do that."""
    if name == "_LAST_LOADED":
        return bool(_WSP.state().flag(_SLOT_LAST_LOADED, False))
    raise AttributeError(name)


def _last_store_path() -> str:
    try:
        # Stage 1: the caller's WORKSPACE, which with none set is
        # ``Path(DEFAULT_CONFIG_PATH).parent`` — the old expression exactly.
        from motor_ai_sim.workspace import root as _ws_root
        base = str(_ws_root())
    except Exception:  # noqa: BLE001
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    return os.path.abspath(os.path.join(base, ".last_coupled.json"))


def _load_last() -> None:
    if _last_loaded():
        return
    _set_last_loaded(True)
    try:
        with open(_last_store_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            _LAST.update(d)
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — a corrupt store is an empty store
        log.warning("could not restore the last coupled run: %s", exc)


def _remember_last(out: Dict[str, Any], *, alt_carrier: bool = False) -> None:
    """Park THIS loop's verdict as the last coupled run — unless it is an errand.

    A run marked ``record: false`` (``run_recording``) was made at ANOTHER
    duty's operating point while this machine is the one loaded, so it is not
    this machine's coupled answer: no ``_LAST``, no ``config/.last_coupled.json``,
    no per-duty row.
    """
    from motor_ai_sim import run_recording as _rr
    if _rr.suppressed():
        log.info("coupled: run solved for another duty (record: false) — "
                 "not remembered as this machine's last coupled result")
        return
    _set_last_loaded(True)
    _LAST.clear()
    _LAST.update(out)
    try:
        # What goes to disk is the STORE, snapshotted under its own lock
        # (``workspace.BoundedStore.snapshot``), not the caller's ``out``: a
        # second coupled run updating ``_LAST`` while this one serialised it is
        # the same race that lost the mechanical pickle ("dictionary changed
        # size during iteration").  Same content, taken coherently.
        _snap = getattr(_LAST, "snapshot", None)
        payload = _snap() if callable(_snap) else dict(_LAST)
        p = _last_store_path()
        tmp = f"{p}.tmp{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001 — a store is a convenience
        log.warning("could not persist the last coupled run: %s", exc)
    # ── and once more, PER DUTY (2026-09-09) ────────────────────────────────
    # One coupled answer per machine is one report column; the user asked to
    # compare every duty across every simulation.  A compact copy — iterations,
    # convergence, the settled temperatures, the bearing watts and the shaft
    # efficiency, without the per-iteration history — is filed under the duty
    # the catalog context names.
    try:
        from motor_ai_sim import duty_results as _dr
        _dr.note_coupled(out, alt_carrier=bool(alt_carrier))
    except Exception:  # noqa: BLE001 — bookkeeping never fails a solve
        log.debug("coupled: per-duty result not recorded", exc_info=True)


@router.get("/last")
def last(geo: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    """The most recent coupled run — a lookup, never a solve.

    200 with ``has_result: false`` rather than a 404: never having run the loop
    is the NORMAL state of the toggle, and a console full of red 404s on every
    mount is not an error report.  ``stale_geometry`` is ``None`` — UNKNOWN,
    never "fine" — when either fingerprint is missing, the same rule
    ``/api/thermal/last`` follows.
    """
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.routes.thermal import _live_fingerprint

    _load_last()
    live = _live_fingerprint(parse_geo_override(geo))
    if not _LAST:
        return {"has_result": False, "result": None,
                "live_geometry_fingerprint": live}
    fp = _LAST.get("geometry_fingerprint")
    stale = None if (not fp or not live or live == "nofp") else bool(fp != live)
    _snap = getattr(_LAST, "snapshot", None)
    return {"has_result": True,
            # Under the store's lock: a coupled run finishing while this GET
            # copies the store must not turn the read into a 500.
            "result": (_snap() if callable(_snap) else dict(_LAST)),
            "live_geometry_fingerprint": live, "stale_geometry": stale}


# ---------------------------------------------------------------------------
# Reading the request
# ---------------------------------------------------------------------------

def _refuse(message: str, fields: List[str], *, code: str = "coupled_refused"):
    return HTTPException(status_code=422, detail={
        "error": message, "error_code": code,
        "invalid_parameters": [{"field": f} for f in fields]})


def _f(body: Dict[str, Any], key: str, default: float) -> float:
    try:
        v = body.get(key)
        return float(default if v is None else float(v))
    except (TypeError, ValueError):
        return float(default)


def _magnet_reference_temp_c() -> Optional[float]:
    """The assigned magnet card's OWN temperature — the loop's starting guess.

    A card is a measurement at one temperature (phase 1), so "no magnet
    temperature yet" means "the card as quoted", and starting the iteration
    anywhere else would make the first EM run a different machine from the one a
    plain Run produces.  ``None`` when there is no magnet assigned or the card
    carries no reference: the loop then leaves ``magnet_temp_c`` alone and says
    so, rather than inventing a temperature for a magnet nobody named.
    """
    try:
        from motor_ai_sim.materials import get_magnet
        from motor_ai_sim.routes.thermal import _assignments
        name = (_assignments() or {}).get("magnet")
        if not name:
            return None
        t = get_magnet(str(name)).temperature_c
        return None if t is None else float(t)
    except Exception:  # noqa: BLE001 — an unknown card is not a reason to crash
        return None


# ---------------------------------------------------------------------------
# THE DRIVE — a sinusoid, or the inverter (2026-09-14)
# ---------------------------------------------------------------------------
# WHY (PWM study 2026-09-13 §2.9, the study's own most important conclusion):
# the measured carrier cost on the Ø200 L155 peak point is +2 196 W at 24 kHz
# and 96 % of it lands in the STATOR — copper +1 367 W, teeth and yoke +884 W —
# while the magnets, shaft and sleeve move by less than a watt (the 3.2 mm gap,
# the 2.5 mm sleeve and the 5 mm axial magnet slices screen the carrier before
# it reaches them).  But every thermal map, every heat budget, every bearing
# temperature and every warning of every duty in this project was computed on
# SINUSOIDAL losses.  The stator is being fed ~30 % less heat than the inverter
# actually puts into it, and the whole accepted operating point — Br, torque,
# efficiency, the demagnetisation margin — grew out of the winding and magnet
# temperatures that map produced.
#
# So the loop learns a second drive.  ``drive: "pwm"`` runs the SAME
# ``pwm_voltage`` path ``/api/simulation`` uses — star-equivalent circuit on a
# √3-scaled bus for a delta machine, the regular-sampled centre-aligned
# modulator, the settled DC anchor — at the loop's current temperatures, and the
# per-element loss map THAT run produces is what the thermal half solves on.
# ``drive`` absent, or "sine"/"current", is byte-for-byte today's loop.
#
# …and from 2026-09-22 a THIRD: ``drive: "inverter"`` is the CONTROLLER's own
# bridge — the same modulator with the dead time and the device drops of a
# named part, iterated against the junction temperature those losses produce
# (Controller Stage 2, docs/CONTROLLER_MODULE_2026-09-22.md §7).  It is a new
# drive and NOT a change to the second: a stored ``drive: "pwm"`` record keeps
# its answer and a duty re-solved on ``pwm`` gives the same numbers it always
# did.  ``"inverter"`` used to be an ALIAS of ``"pwm"``; it now means the
# Controller, which is the one place a PWM excitation is described from here on
# (owner: *«всё будет задаваться в меню Controller»*).
_DRIVE_ALIASES = {"": "current", "sine": "current", "sinusoid": "current",
                  "current": "current", "pwm": "pwm", "pwm_voltage": "pwm",
                  "ideal_pwm": "pwm",
                  "inverter": "inverter", "controller": "inverter"}

#: The two drives that are a CHOPPED BRIDGE — everything the loop does about a
#: carrier, a DC link, a modulation ceiling, a loss map and a current regulator
#: is asked of this set rather than of the word "pwm".
_BRIDGE_DRIVES = ("pwm", "inverter")

#: Samples of the FEM march per carrier period.  The study's two measured
#: carriers were solved at 280 steps over 14 carriers (20.0) and 600 over 29
#: (20.7); below ~16 the passport's own note applies — "reads LOW on every
#: ripple-driven number" — and the G2-L40 block that was taken at 4.5 is the
#: worked example of what that produces.  20 is the study's resolution, so a
#: coupled PWM run is comparable with the numbers already measured.
PWM_SAMPLES_PER_CARRIER = 20

#: The mixed settle schedule the B5 fix ships with: a coarse prefix, then whole
#: FINE electrical periods with the period-mean DC anchor.  ``fine`` solves the
#: whole prefix at the fine step (slower, same answer within noise).
_PWM_SCHEDULES = {"mixed": "1", "fine": "0"}


def _coupled_drive(body: Dict[str, Any]) -> str:
    """``"current"``, ``"pwm"`` or ``"inverter"`` — validated."""
    raw = str(body.get("drive") or "").strip().lower()
    d = _DRIVE_ALIASES.get(raw)
    if d is None:
        raise _refuse(
            "drive=%r is not something this loop can run: the electromagnetic "
            "half is the sinusoidal CURRENT drive (\"sine\", the default), the "
            "IDEAL two-level inverter (\"pwm\"), or the CONTROLLER's own bridge "
            "(\"inverter\" — the Controller menu's device, dead time and drops). "
            "An imposed sinusoidal voltage has no carrier to measure and is not "
            "offered here." % (body.get("drive"),),
            ["drive"], code="unknown_drive")
    return d


# ---------------------------------------------------------------------------
# WHAT THE LOOP IS ASKED FOR — the steady state, or the limits
# ---------------------------------------------------------------------------
# Owner, 2026-09-18: *«надо сделать выбор — или считать до конца стабилизации
# температуры, или считать до лимитов и находить время работы при заданных
# условиях»*.  So it is a CHOICE and not a rule the backend applies by itself:
#
#   ``steady``      (the default, and byte-identical to every record ever
#                   written) iterate until the winding, the magnets and the
#                   bearing seat stop moving, and report that state — even
#                   when it is past a limit.
#   ``limits``      stop at the FIRST limit any part reaches and report the
#                   machine AT THAT MOMENT, with the time it took to get there
#                   from cold.
#   ``continuous``  (owner 2026-09-21: *«давай сделаем кнопку, или лучше
#                   добавим ещё один элемент в меню»*, on the same selector) —
#                   run the loop exactly as ``limits`` does (same stop rule, no
#                   extra electromagnetic passes beyond what ``limits`` costs),
#                   then, from that converged/limited pass's own loss map and
#                   the cooling THIS duty was just solved with, find the
#                   largest current the machine may hold FOR EVER (S1) —
#                   :func:`coupled_continuous_rating.rate`, the same machinery
#                   ``POST /api/coupled/continuous_rating`` uses, here run for
#                   exactly one condition (the duty's own cooling, no patch).
#
# A default that had switched silently would have re-written the meaning of
# every stored coupled record on the next re-run; this way a duty gets the
# limited or continuous answer only because somebody asked for it, and the
# record says which question it is the answer to (``solve_to``) and which
# answer it turned out to be (``mode``).
SOLVE_TO = ("steady", "limits", "continuous")
_SOLVE_TO_ALIASES = {"": "steady", "steady": "steady", "steady_state": "steady",
                     "stabilise": "steady", "stabilize": "steady",
                     "limits": "limits", "limit": "limits",
                     "time_to_limit": "limits", "limited": "limits",
                     "continuous": "continuous", "continuous_rating": "continuous",
                     "rating": "continuous", "s1": "continuous"}


def _solve_to(body: Dict[str, Any]) -> str:
    """``"steady"``, ``"limits"`` or ``"continuous"`` — validated, and
    ``"steady"`` when absent."""
    raw = str(body.get("solve_to") or "").strip().lower()
    out = _SOLVE_TO_ALIASES.get(raw)
    if out is None:
        raise _refuse(
            "solve_to=%r is not one of the three questions this loop answers: "
            "\"steady\" iterates until the temperatures stop moving and reports "
            "that state, \"limits\" stops at the first limit a part reaches and "
            "reports the machine at that moment with the time it took to get "
            "there, \"continuous\" does the same and adds the largest current "
            "this machine may hold for ever at the duty's own cooling (S1)."
            % (body.get("solve_to"),),
            ["solve_to"], code="unknown_solve_to")
    return out


def _pole_pairs(body: Dict[str, Any]) -> int:
    """The machine's pole pairs, honouring this request's geometry override."""
    from motor_ai_sim.config import get_config
    from motor_ai_sim.routes._validation import parse_geo_override
    g = dict((get_config().get("geometry") or {}))
    try:
        ov = parse_geo_override(body.get("geo")) or {}
        g.update(ov)
    except Exception:  # noqa: BLE001
        pass
    try:
        return max(int(g.get("num_poles") or 0) // 2, 1)
    except (TypeError, ValueError):
        return 1


def _pack_nominal_v() -> Optional[float]:
    """The loaded machine's DC link, nominal.  ``None`` when no pack is named —
    the loop then refuses rather than inventing a bus, because a PWM answer at
    the wrong bus is a different machine's answer (the study measured 0.14 pp
    of efficiency between v_nom and v_max on this very duty)."""
    try:
        from motor_ai_sim.config import get_config
        b = (get_config() or {}).get("battery") or {}
        for k in ("v_nom", "v_oc"):
            v = b.get(k)
            if v is not None and float(v) > 0.0:
                return float(v)
    except Exception:  # noqa: BLE001
        pass
    return None


def _duty_summary() -> Dict[str, Any]:
    """The saved summary of the duty the catalog context names — where the
    fundamental-voltage seed of a PWM run comes from when the caller sends
    none.  ``{}`` when nothing is loaded."""
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        if not ctx:
            return {}
        from motor_ai_sim.routes.family import duty_entry
        e = duty_entry(*ctx) or {}
        s = e.get("summary")
        return dict(s) if isinstance(s, dict) else {}
    except Exception:  # noqa: BLE001 — a catalog read never fails a solve
        log.debug("coupled: could not read the active duty's summary",
                  exc_info=True)
        return {}


def _pwm_steps_per_period(f_carrier_hz: float, f_elec_hz: float) -> int:
    """The study's resolution rule: ``PWM_SAMPLES_PER_CARRIER`` FEM steps per
    carrier period, i.e. ``round(f_c/f_el) · 20`` steps per ELECTRICAL period.

    The carrier is snapped to a whole number per electrical period by the
    source itself (synchronous modulation), so the count is built on the
    snapped carrier and not on the asked-for frequency.
    """
    nc = max(int(round(float(f_carrier_hz) / max(float(f_elec_hz), 1e-9))), 1)
    return int(nc * PWM_SAMPLES_PER_CARRIER)


#: How far UNDER the modulator's own ceiling a regulator step may aim
#: [fraction].  The gain below is measured at ONE reference phase, and the
#: factory's compensation walks that phase a little while it solves; 0.5 % is
#: wider than every phase spread there is above 4 carriers (0.12 % at 7, 0.01 %
#: at 32) and is nothing a converging regulator can feel.
_MODULATION_HEADROOM = 0.005


@functools.lru_cache(maxsize=256)
def _modulator_gain(carriers: int, v_delta_deg: float) -> float:
    """Fundamental the bridge APPLIES per volt of linear-modulation reference,
    at the linear limit m = 1.15 and this many carriers per electrical period.

    THE NUMBER THE REGULATOR WAS MISSING (2026-09-15, CIANO10 200 opt / L180 gen
    'rated 0.5x9 mm').  ``pwm.build_pwm_source`` does not apply the reference it
    is handed: a regular-sampled modulator attenuates and delays the fundamental,
    so the factory SOLVES for the reference whose APPLIED fundamental is the
    requested one and refuses when that solved reference leaves the linear region
    (``simulation/pwm.py``, the compensation loop at the end of
    ``build_pwm_source``).  The ceiling in the coordinate the regulator moves —
    ``v_phase_peak_V``, the fundamental it asks for — is therefore NOT
    0.5·1.15·V_bus: it is what a reference at m = 1.15 actually puts on the
    terminals, which at 14 carriers on a 799.2 V delta link is 750.9 V and not
    795.9 V.  Aiming a pass between the two is a 422 hours into a loop
    ("compensating the modulator's sampled-reference gain for 14 carriers per
    period needs m = 1.161, past the 1.15 linear limit"), and that is exactly how
    that generator lost its last pass and saved 3.4 % off point.

    MEASURED, NOT MODELLED: the same ``PwmVoltageSource.applied_fundamental`` the
    factory iterates against, on a unit bus (the ratio is bus-independent — the
    bus is a multiplicative factor in every pole mean).  It is NOT the textbook
    sinc(π/(2·N_c)) of the sampled hold, which is 0.2 % here; most of the loss is
    the [0, 1] duty clamp biting at m > 1, where sine-triangle without
    zero-sequence injection is already clipping.  Both effects are in the
    measurement and neither is in a formula.

    ``v_delta_deg`` is the reference's phase against the carrier.  The d-axis
    offset the run adds to it is not known here and does not need to be: the
    spread over a whole period is 0.12 % at 7 carriers and less above, which is
    what ``_MODULATION_HEADROOM`` covers.  Clamped to 1.0 so a degenerate pulse
    ratio can never hand back a ceiling ABOVE the linear one — at 3 carriers the
    projection swings 0.81-1.10 with phase, and a source that thin is refused by
    the factory's own "cannot synthesise this fundamental" branch anyway.

    Cached: a few milliseconds each, and a loop asks for one carrier count.
    """
    from motor_ai_sim.simulation.pwm import (MAX_MODULATION_INDEX as _MAX_M,
                                             PwmVoltageSource as _Src)
    src = _Src(pole_pairs=1, daxis_deg=0.0, v_delta_deg=float(v_delta_deg),
               v_bus=2.0, carriers=max(1, int(carriers)), m=_MAX_M)
    applied, _ = src.applied_fundamental()
    # v_bus = 2 V, so the LINEAR reference peak is 0.5·m·v_bus = m.
    return min(1.0, max(1e-3, float(applied) / _MAX_M))


def _inverter_settings(body: Dict[str, Any], *, rpm: float) -> Dict[str, Any]:
    """The inverter this coupled run is fed by, fully resolved — every default
    named, nothing guessed silently.

    ``body["inverter"]`` may carry ``f_carrier_hz``, ``v_dc_V``,
    ``n_steps_per_period``, ``schedule`` ("mixed" | "fine"), and the
    fundamental the bridge must apply (``v_phase_peak_V`` / ``v_delta_deg``).
    Each falls back, in order, to:

      * the carrier — the CONTROLLER's (``_drive_carrier``: the block sent
        with the request, the saved Controller settings, the retired
        Simulation-tab carrier as a migration tier, the Controller's stated
        default); since 2026-09-24 the Controller is the one place a PWM
        drive is defined;
      * the bus — the Controller's manual V_dc, else the machine's battery
        ``v_nom`` (``_drive_v_dc``);
      * the frame count — the study's 20 samples per carrier;
      * the fundamental — the duty's own saved ``V1_seed_peak_V`` /
        ``V1_seed_delta_deg``, which is what a current-drive run of this point
        measured the terminals at.

    THE FUNDAMENTAL IS HELD ACROSS THE ITERATIONS, and that is the physics, not
    a shortcut: a voltage-fed machine is given a fundamental by its inverter and
    answers with whatever current its own resistance and back-EMF allow.  As the
    copper heats the current moves, and the loop reports the solved current per
    iteration rather than pretending the point is pinned.
    """
    raw = body.get("inverter")
    inv = dict(raw) if isinstance(raw, dict) else {}

    def _num(key, fallback, what, fallback_src=None):
        v = inv.get(key)
        if v is not None:
            try:
                f = float(v)
            except (TypeError, ValueError):
                raise _refuse("inverter.%s must be a number; got %r"
                              % (key, v), ["inverter." + key],
                              code="bad_inverter")
            if not (f > 0.0 and math.isfinite(f)):
                raise _refuse("inverter.%s must be positive; got %r"
                              % (key, v), ["inverter." + key],
                              code="bad_inverter")
            return f, "the request"
        if fallback is None:
            raise _refuse(
                "drive='pwm' needs %s and none was given: send "
                "inverter.%s, or %s." % (what, key, _INV_HINTS[key]),
                ["inverter." + key], code="pwm_incomplete_inverter")
        return float(fallback), (fallback_src or _INV_SOURCES[key])

    # THE CARRIER AND THE BUS ARE THE CONTROLLER'S (2026-09-24).  An explicit
    # ``inverter.f_carrier_hz`` / ``inverter.v_dc_V`` (a carrier study, an
    # ``alt_carrier`` record) still wins; otherwise ``_drive_carrier`` /
    # ``_drive_v_dc`` answer from the Controller — never a default the record
    # cannot name.
    _fc = _drive_carrier(body, default=True)
    f_c, f_c_src = _num("f_carrier_hz", _fc["hz"], "a carrier frequency",
                        fallback_src=_fc["source"])
    _vd = _drive_v_dc(body)
    v_dc, v_dc_src = _num("v_dc_V", _vd["V"], "a DC link voltage",
                          fallback_src=_vd["source"])
    f_el = float(rpm) * _pole_pairs(body) / 60.0
    steps, steps_src = _num("n_steps_per_period",
                            _pwm_steps_per_period(f_c, f_el),
                            "a frame count")
    sched = str(inv.get("schedule") or "mixed").strip().lower()
    if sched not in _PWM_SCHEDULES:
        raise _refuse("inverter.schedule must be one of %s; got %r"
                      % (", ".join(sorted(_PWM_SCHEDULES)), inv.get("schedule")),
                      ["inverter.schedule"], code="bad_inverter")
    _sum = _duty_summary()
    v1, v1_src = _num("v_phase_peak_V", _sum.get("V1_seed_peak_V"),
                      "the fundamental voltage the bridge must apply")
    # The load angle may legitimately be zero or negative, so it does not go
    # through `_num` (which demands a positive number).
    dl = inv.get("v_delta_deg")
    dl_src = "the request"
    if dl is None:
        dl, dl_src = _sum.get("V1_seed_delta_deg"), "the duty's saved summary"
    if dl is None:
        raise _refuse(
            "drive='pwm' needs the fundamental's load angle and none was "
            "given: send inverter.v_delta_deg, or run this duty on the "
            "current drive once so its summary carries V1_seed_delta_deg.",
            ["inverter.v_delta_deg"], code="pwm_incomplete_inverter")
    try:
        dl = float(dl)
    except (TypeError, ValueError):
        raise _refuse("inverter.v_delta_deg must be a number; got %r" % (dl,),
                      ["inverter.v_delta_deg"], code="bad_inverter")
    # WHERE this run's answer is filed on the duty.  "main" is the duty's own
    # design carrier (its `sim.fSwitch`) and owns the record; "alt_carrier" is
    # the same point measured at ANOTHER carrier — an extra column for the
    # report, filed beside the main record and never over it.
    rec_as = str(inv.get("record_as") or "main").strip().lower()
    if rec_as not in ("main", "alt_carrier"):
        raise _refuse("inverter.record_as must be 'main' or 'alt_carrier'; "
                      "got %r" % (inv.get("record_as"),),
                      ["inverter.record_as"], code="bad_inverter")
    # THE POINT THIS RUN IS SUPPOSED TO SIT AT.  Optional, and when it is
    # absent the loop applies a fixed fundamental and reports whatever current
    # that produces (which is a legitimate question, just not the report's).
    tgt = inv.get("target_I_phase_rms_A")
    if tgt is not None:
        try:
            tgt = float(tgt)
        except (TypeError, ValueError):
            raise _refuse("inverter.target_I_phase_rms_A must be a number; got "
                          "%r" % (inv.get("target_I_phase_rms_A"),),
                          ["inverter.target_I_phase_rms_A"], code="bad_inverter")
        if not (tgt > 0.0 and math.isfinite(tgt)):
            raise _refuse("inverter.target_I_phase_rms_A must be positive; got "
                          "%r" % (inv.get("target_I_phase_rms_A"),),
                          ["inverter.target_I_phase_rms_A"], code="bad_inverter")
    nc = max(int(round(f_c / max(f_el, 1e-9))), 1)
    # THE CEILING THE BRIDGE CAN ACTUALLY SYNTHESISE, in the same coordinate the
    # regulator moves: the largest ``v_phase_peak_V`` whose modulation index is
    # still inside the linear limit on THIS link.  In delta the drive is asked
    # for the branch (= line) fundamental, so the ceiling carries the √3 the
    # star-equivalent substitution carries (`pwm.modulation_index`).
    #
    # WHY IT IS COMPUTED HERE (2026-09-15, CIANO10 200 opt / L180 gen).  The
    # electromagnetic half refuses m > 1.15 with a 422 in milliseconds — which
    # is right — but the regulator between two passes did not know the number,
    # so a secant step could aim the NEXT pass at a fundamental nobody can
    # build and kill a loop that was hours deep.  That generator's peak duty
    # sits 0.8 % under its own ceiling (789.7 V of 795.9 V on a 799.2 V pack),
    # which is exactly where one honest correction runs out of inverter.
    from motor_ai_sim.simulation.pwm import (
        MAX_MODULATION_INDEX as _MAX_M, is_delta as _is_delta)
    _sd = str(body.get("star_delta") or "").strip().lower()
    if not _sd:
        from motor_ai_sim.routes.simulation import _effective_star_delta as _esd
        _sd = _esd(None)
    v1_max_unc = 0.5 * _MAX_M * v_dc * (math.sqrt(3.0) if _is_delta(_sd) else 1.0)
    # …AND IT IS THE COMPENSATED CEILING (2026-09-15, the same generator, one
    # defect deeper).  The linear limit above is what the BRIDGE can chop; what
    # the regulator asks for is the fundamental the modulator must APPLY, and
    # the factory buys that by raising the reference (`_modulator_gain`).  The
    # first fix clamped to the uncompensated number, so pass 4 was aimed at
    # 761.7 V of a 795.9 V "ceiling" whose real value was 747.2 V — inside the
    # clamp, outside the inverter, and the 422 killed the pass anyway.
    _gain = _modulator_gain(nc, round(float(dl), 3))
    v1_max = v1_max_unc * _gain * (1.0 - _MODULATION_HEADROOM)
    # …AND IT IS ONLY HANDED ON WHEN THE RUN ITSELF FITS UNDER IT.  The
    # connection is resolved here from the body, and a body that does not name
    # one falls back to the shared configuration — which can be a machine away
    # from the one being solved.  A ceiling computed as STAR for a machine the
    # solver runs as DELTA is √3 too low, and clamping a good run down to it
    # would be this function inventing an operating point.  So: when the
    # fundamental this run starts from is already above the ceiling, the ceiling
    # is not trusted and nothing is clamped — the electromagnetic half then
    # refuses that run by name (m > 1.15, in milliseconds, naming the bus and
    # the volts), which is the honest answer to a genuinely impossible inverter.
    v1_cap = {"v_phase_peak_max_V": round(float(v1_max), 4)} \
        if float(v1) <= v1_max else {}
    return {
        **v1_cap,
        # BOTH ceilings, always — including on the run whose seed is already
        # over them and which therefore gets no clamp at all.  A reader looking
        # at that refusal needs to see WHICH ceiling it hit and by how much, and
        # a report comparing two carriers needs the gain that separates them.
        "v_phase_peak_max_uncompensated_V": round(float(v1_max_unc), 4),
        "modulator_gain_factor": round(float(_gain), 6),
        "record_as": rec_as,
        "harm_ref": bool(inv.get("harm_ref", False)),
        "target_I_phase_rms_A": tgt,
        "i_tol_pct": float(inv.get("i_tol_pct") or 1.0),
        # The seed the regulator's clamp is measured against — the fundamental
        # the caller asked for, kept even after the regulator has moved it.
        "v_phase_peak_seed_V": float(v1),
        "f_carrier_hz": f_c, "v_dc_V": v_dc,
        "n_steps_per_period": int(steps), "schedule": sched,
        "v_phase_peak_V": float(v1), "v_delta_deg": float(dl),
        "f_elec_hz": round(f_el, 4),
        "carriers_per_period": nc,
        "samples_per_carrier": round(float(steps) / nc, 2),
        # WHICH TIER the carrier came from (2026-09-24): ``controller`` is the
        # rule; ``legacy`` = migrated from the retired Simulation-tab carrier,
        # ``default`` = nothing named one — both say "save it in Controller".
        "carrier_origin": ("request" if f_c_src == "the request"
                           else _fc["origin"]),
        "sources": {"f_carrier_hz": f_c_src, "v_dc_V": v_dc_src,
                    "n_steps_per_period": steps_src,
                    "v_phase_peak_V": v1_src, "v_delta_deg": dl_src},
    }


#: Where each inverter field comes from when the request does not name it —
#: the words the refusal and the record use, so both say the same thing.
_INV_SOURCES = {
    "f_carrier_hz": "the Controller settings (carrier)",
    "v_dc_V": "the machine's battery v_nom",
    "n_steps_per_period": ("the study's rule: %d FEM steps per carrier period"
                           % PWM_SAMPLES_PER_CARRIER),
    "v_phase_peak_V": "the duty's saved summary (V1_seed_peak_V)",
}
_INV_HINTS = {
    "f_carrier_hz": "set the carrier in the Controller tab and save it",
    "v_dc_V": ("give the machine a battery with a v_nom, or set V_dc in the "
               "Controller tab"),
    "n_steps_per_period": "give the run a speed so the rule can be applied",
    "v_phase_peak_V": ("run this duty on the current drive once so its summary "
                       "carries V1_seed_peak_V"),
}


#: The electromagnetic numbers the coupling block carries at the top, so a
#: record can be compared with another excitation's at the same point without
#: the transient beside it: the torque, the four loss classes the carrier is
#: split into, the total and the efficiency.
_EM_FACE_KEYS = ("T_em_avg_Nm", "T_ripple_pct", "P_stranded_W", "P_core_W",
                 "P_solid_W", "P_mag_W", "P_shaft_W", "P_sleeve_W",
                 "P_loss_total_W", "efficiency", "I1_phase_rms_A",
                 "THD_I_pct", "THD_LL_pct", "V_line_peak_V",
                 "n_steps_per_period")


#: The duty-cycle kinds that are an IMPULSE: the machine does not stay at the
#: point long enough for a steady temperature to exist.  One definition, in
#: ``coupled_duty_cycle`` — the module that solves them.
_IMPULSE_KINDS = _cdc.IMPULSE_KINDS


def _duty_cycle_of(body: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """The duty cycle this run belongs to, and the duty it is named on.

    The body's own ``duty_cycle`` block wins (an API caller saying what the
    point is for); otherwise the block saved on the duty the catalog context
    names, which is the same place ``duty_results.note_coupled`` files this
    run's answer.  ``(None, "")`` whenever there is nothing to read — a machine
    with no catalogued duty is the normal case for a bare Run, and it is not a
    reason to refuse anything.
    """
    blk = body.get("duty_cycle")
    if isinstance(blk, dict) and blk:
        return dict(blk), str(body.get("duty") or "this run")
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        if not ctx:
            return None, ""
        die, cfg, duty = ctx
        from motor_ai_sim.routes.family import duty_entry
        stored = (duty_entry(die, cfg, duty) or {}).get("duty_cycle")
        if isinstance(stored, dict) and stored:
            return dict(stored), duty
    except Exception:  # noqa: BLE001 — a catalog read never fails a solve
        log.debug("coupled: could not read the active duty's cycle",
                  exc_info=True)
    return None, ""


def _cycle_preflight(body: Dict[str, Any]) -> None:
    """An S2/S3 duty is solved as a REGIME — check that it CAN be, before a solve.

    THE REFUSAL THAT WENT AWAY (user, 2026-09-16: *"каплинг на цикле S3 подбирает
    скважность для того чтобы можно было влезть в лимиты"*).  Until today this
    function raised ``impulse_duty_not_steady`` on every impulse duty the loop
    was asked to iterate, and its reasoning was sound: iterating an S3 point to a
    fixed point answers with the temperature it would reach if the pull never
    ended, which on the Ø85 robot joint is hundreds of kelvin above anything the
    cycle sees.  The answer to that is not to refuse the question — it is to ask
    the right one, which is what ``coupled_duty_cycle`` now does inside every
    pass: FIND the duty ratio the machine can hold and feed back the temperatures
    AT it.

    What is still checked here is the SHAPE of the cycle, because everything it
    can be wrong about is knowable without a solve: a kind nobody recognises, a
    cycle time of zero, a segment naming a duty this configuration does not
    have.  Each of those would otherwise surface as a ``DutyCycleError`` on the
    far side of a two-minute transient.  It is the same structural check
    ``routes.family`` runs when a cycle is SAVED (``structure_only=True``), so a
    block the editor accepted can never be refused here.

    …and NOTHING AT ALL while the duty-cycle feature is off (owner 2026-09-17).
    With the flag off this run is a standard coupled loop whatever the duty
    stores, so a structural complaint about a cycle nobody is going to solve
    would refuse a run that was never going to look at the block.
    """
    if not _cdc.enabled():
        return
    blk, name = _duty_cycle_of(body)
    if not _cdc.is_impulse(blk):
        return
    duties, name = _cycle_catalogue(body, name)
    try:
        from motor_ai_sim.thermal_duty_cycle import normalise_spec
        normalise_spec(dict(blk or {}), duties, default_duty=name,
                       structure_only=True)
    except _cdc.DutyCycleError as exc:
        raise _refuse(
            "duty %r carries a cycle the coupled loop cannot solve: %s"
            % (name, exc.message),
            ["duty_cycle"], code=exc.code)


def _cycle_catalogue(body: Dict[str, Any],
                     name: str) -> Tuple[List[Dict[str, Any]], str]:
    """``(the duties a cycle may name, the duty this run IS)``.

    The name is the body's when it states one, else the duty the catalog context
    has open — the loop runs the loaded duty's point, so that is the duty the
    powered segment means.  The list always CONTAINS that duty, placeholder and
    all: an API caller may state a cycle for a machine whose duty is not
    catalogued, and refusing the run's own duty as "not one of this
    configuration's duties" would be the pre-flight failing on the one name it
    cannot be wrong about.  Every OTHER name in the block is still checked.
    """
    duties: List[Dict[str, Any]] = []
    ctx = None
    try:
        from motor_ai_sim.duty_results import active_context
        from motor_ai_sim.routes.family import config_duties
        ctx = active_context()
        if ctx:
            duties = list(config_duties(ctx[0], ctx[1]) or ())
    except Exception:  # noqa: BLE001 — a catalog read never fails a solve
        log.debug("coupled: could not read the duties for the cycle",
                  exc_info=True)
    duty = str(body.get("duty") or (ctx[2] if ctx else "") or name or "this run")
    if not any(str((d or {}).get("name") or "") == duty for d in duties):
        duties = list(duties) + [{"name": duty}]
    return duties, duty


def _rated_state_c(die: str, cfg: str, duties: List[Dict[str, Any]],
                   this_duty: str) -> Tuple[Dict[str, float], str, str]:
    """The node MEANS of the RATED duty's own stored thermal map — the warm
    machine an S2 pull may also be started from.

    ``({}, "", why)`` whenever there is no such map, and that is the common case
    and not a failure: the coupled loop calibrates on the point it is SOLVING,
    so "and how long from rated?" can only be answered when the rated duty has
    been solved on its own.  Naming where the state came from matters here — a
    start state taken from this machine's own peak map would be a warm-up nobody
    ran.
    """
    rated = ""
    for d in (duties or ()):
        nm = str((d or {}).get("name") or "")
        if nm and nm != str(this_duty) and nm.strip().lower().startswith("rated"):
            rated = nm
            break
    if not rated:
        return {}, "", ("this configuration has no rated duty beside %r, so "
                        "there is no warm state to start a pull from"
                        % this_duty)
    try:
        from motor_ai_sim import duty_results as _dr
        from motor_ai_sim.thermal_capacities import NODES as _NODES
        comps = ((_dr.get(die, cfg).get(rated) or {}).get("thermal")
                 or {}).get("components") or {}
        out: Dict[str, float] = {}
        for n in _NODES:
            v = (comps.get(n) or {}).get("avg")
            if v is None:
                return {}, "", (
                    "the rated duty %r has no stored temperature for the %s, so "
                    "there is no warm state to start a pull from" % (rated, n))
            out[n] = float(v)
    except Exception:  # noqa: BLE001 — a store read never fails a solve
        log.debug("coupled: could not read the rated duty's map", exc_info=True)
        return {}, "", "the rated duty's stored map could not be read"
    return out, rated, ("the rated duty %r's own stored thermal map" % rated)


def _cycle_inputs(body: Dict[str, Any],
                  cooling: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Everything the cycle model needs that does NOT change between passes.

    ``None`` means this run is not on an impulse duty — every machine that has
    no cycle, and every cycle that is S1 or an explicit segment list, is the
    settled point the loop has always solved.

    What DOES change between passes is the map and the run the model is fitted
    to, and that is deliberately not in here: it arrives at :func:`_cycle_step`
    each time, which is the whole mechanism.

    THE FEATURE FLAG (owner 2026-09-17: *«давай пока уберём duty cycle из
    Thermal, оставим только стандартный каплинг»*).  With
    ``DUTY_CYCLE_ENABLED`` off — the default — this is ``None`` for EVERY duty,
    including one carrying a stored S2/S3 block: the loop then iterates that
    point to its fixed point exactly as it did before cycles existed, writes no
    ``duty_cycle`` sub-block and files no cycle record.  One gate, here, because
    ``None`` is already the word this loop understands for "no regime".
    """
    if not _cdc.enabled():
        return None
    blk, name = _duty_cycle_of(body)
    if not _cdc.is_impulse(blk):
        return None
    duties, name = _cycle_catalogue(body, name)
    die = cfg = ""
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        if ctx:
            die, cfg = str(ctx[0]), str(ctx[1])
    except Exception:  # noqa: BLE001 — a catalog read never fails a solve
        log.debug("coupled: could not read the catalog context for the cycle",
                  exc_info=True)
    from motor_ai_sim.routes.thermal import _assignments, _dc_geometry
    geom, _ov = _dc_geometry(body.get("geo"))
    lim, lim_src = _cdc.magnet_limit(blk, body.get("magnet_limit_c"))
    rated, rated_duty, rated_why = _rated_state_c(die, cfg, duties, name)
    return {
        "block": dict(blk or {}), "duty": str(name or "this run"),
        "die": die, "config": cfg, "duties": duties,
        "geometry": geom,
        "d_housing_m": float(geom.get("stator_diameter") or 0.0) * 1e-3,
        # The materials the RUN itself used — the shared assignment plus this
        # request's `?mat=` override — because the capacities must belong to the
        # machine that was solved, not to a catalogue row that may name another
        # steel (the stale-assignment trap, 2026-09-12).
        "materials": _assignments(),
        "part_states": None,          # = the states the run's summary carries
        "magnet_limit_c": lim, "magnet_limit_source": lim_src,
        "rated_state_c": rated, "rated_duty": rated_duty,
        "rated_state_source": rated_why,
        "cooling": dict(cooling or {}),
    }


def _cycle_lengths(body: Dict[str, Any]) -> Optional[List[float]]:
    """The periods the ED-vs-cycle-length curve is solved at — ``None`` unless
    the caller names them.

    The same body key ``POST /api/thermal/duty_cycle`` takes, and the same
    meaning, with one difference that is a cost decision and not a whim: there
    the span DEFAULTS to five periods because that request exists to draw it;
    here it defaults to none, because on a machine whose time constant is far
    above its cycle each extra period is minutes of cycle-map iteration inside a
    loop the user is watching.
    """
    raw = body.get("ed_cycle_lengths_s")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    out: List[float] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0.0 and math.isfinite(f):
            out.append(f)
    return out or None


def _cycle_step(inputs: Dict[str, Any], em_summary: Dict[str, Any],
                field: Dict[str, Any], *, with_curve: bool = False,
                cycle_lengths: Optional[List[float]] = None,
                samples: Optional[int] = None) -> Tuple[Any, Dict[str, Any]]:
    """ONE pass's regime: fit the cycle to the map just solved and search it.

    Returns ``(model, regime)``.  The model is kept because the FINAL regime —
    the one with the curves, the ED-vs-period span and the stored record — is
    solved on the last pass's model rather than on a fresh fit nobody iterated.
    """
    from motor_ai_sim.routes.thermal import _dc_side_areas

    side = _dc_side_areas(field, em_summary, inputs["geometry"])
    model = _cdc.build_model(
        block=inputs["block"], duties=inputs["duties"],
        duty_name=inputs["duty"], em_summary=em_summary, thermal_result=field,
        geometry=inputs["geometry"], cooling=inputs["cooling"],
        materials=inputs["materials"], part_states=inputs["part_states"],
        magnet_limit_c=inputs["magnet_limit_c"],
        magnet_limit_source=inputs["magnet_limit_source"],
        side_areas=side, d_housing_m=inputs["d_housing_m"],
        rated_state_c=inputs["rated_state_c"])
    regime = _cdc.solve_regime(
        model, samples_per_segment=int(samples or _cdc.LOOP_SAMPLES),
        with_curve=bool(with_curve), cycle_lengths=cycle_lengths)
    if inputs.get("rated_duty") and regime.get("s2_from_rated_s") is not None:
        regime["s2_from_rated_duty"] = inputs["rated_duty"]
    return model, regime


# ---------------------------------------------------------------------------
# HOW LONG MAY IT RUN — the time to the limit (owner 2026-09-17)
# ---------------------------------------------------------------------------
# *«если где-то выходим за лимиты, нужно посчитать время, за какое мотор
# проработает до этого лимита»*.  The loop answers a question about the STEADY
# state; when that state is past a limit the one thing the answer does not
# contain is how long the machine may actually pull before it gets there.
#
# NOT GATED BY `DUTY_CYCLE_ENABLED`, on purpose: this is not a duty cycle.  It
# reads no cycle block, offers no cycle UI and needs no duty ratio — it only
# reuses `thermal_duty_cycle`'s network, which is the one thing in this project
# that knows both the conductances of THIS machine and its heat capacity.  It
# runs on every coupled loop, flag on or off.


def _ttl_rated_state(die: str, cfg: str, duties: List[Dict[str, Any]],
                     this_duty: str) -> Tuple[Dict[str, float], str]:
    """The WARM start: the rated duty's own converged node temperatures.

    ``({}, why)`` unless that duty has BOTH a stored thermal map (the state) and
    a coupled record (the evidence that the state is a converged one and not a
    single thermal solve at a temperature nobody iterated).  A warm start
    invented out of this point's own map would be a warm-up nobody ran, and a
    map solved at a typed coil temperature is not a machine that has been
    working.
    """
    state, rated, why = _rated_state_c(die, cfg, duties, this_duty)
    if not state:
        return {}, why
    try:
        from motor_ai_sim import duty_results as _dr
        rec = (_dr.get(die, cfg) or {}).get(rated) or {}
    except Exception:  # noqa: BLE001 — a store read never fails a solve
        log.debug("coupled: could not read the rated duty's record",
                  exc_info=True)
        return {}, "the rated duty's stored record could not be read"
    cp = rec.get("coupled")
    if not isinstance(cp, dict) or not cp:
        return {}, ("the rated duty %r has a thermal map but no coupled record, "
                    "so there is no converged warm state to start a pull from"
                    % rated)
    return state, ("the rated duty %r's own coupled run — the node means of the "
                   "map it converged on" % rated)


def _ttl_step(body: Dict[str, Any], cooling: Dict[str, Any],
              em_summary: Dict[str, Any], field: Dict[str, Any], *,
              bearing_temp_c: Optional[float],
              runaway: bool) -> Optional[Dict[str, Any]]:
    """The ``time_to_limit`` block for the point this loop just solved.

    ``None`` whenever the answer cannot be formed — no map, no capacities, no
    limits on this machine.  NEVER an exception out of here: the loop solved the
    machine, and a bookkeeping answer that could not be computed must not turn a
    successful two-minute run into a 500.
    """
    from motor_ai_sim.routes.thermal import (_assignments, _dc_geometry,
                                             _dc_side_areas)
    from motor_ai_sim.thermal_capacities import CapacityError, part_capacities

    if not field or not em_summary:
        return None
    geom, _ov = _dc_geometry(body.get("geo"))
    mats = _assignments()
    try:
        caps = part_capacities(em_summary, mats, None)
    except CapacityError as exc:
        log.debug("coupled: no capacities for the time to the limit (%s)", exc)
        return None

    duties, duty = _cycle_catalogue(body, "")
    die = cfg = ""
    try:
        from motor_ai_sim.duty_results import active_context
        ctx = active_context()
        if ctx:
            die, cfg = str(ctx[0]), str(ctx[1])
    except Exception:  # noqa: BLE001 — a catalog read never fails a solve
        log.debug("coupled: no catalog context for the time to the limit",
                  exc_info=True)
    rated, rated_why = (_ttl_rated_state(die, cfg, duties, duty)
                        if (die and cfg) else
                        ({}, "this run is not on a catalogued machine, so there "
                             "is no rated duty to start a warm pull from"))

    limits = _ttl.part_limits(
        thermal_result=field, em_summary=em_summary,
        magnet_grade=mats.get("magnet"),
        bearing_temp_c=bearing_temp_c,
        magnet_limit_c=(float(body["magnet_limit_c"])
                        if body.get("magnet_limit_c") not in (None, "") else None))
    if not limits:
        return None
    try:
        return _ttl.solve(
            thermal_result=field, em_summary=em_summary, limits=limits,
            caps=caps, geometry=geom, cooling=cooling,
            side_areas=_dc_side_areas(field, em_summary, geom),
            d_housing_m=float(geom.get("stator_diameter") or 0.0) * 1e-3,
            duty=duty, rated_state_c=(rated or None), rated_source=rated_why,
            runaway=bool(runaway))
    except _cdc.DutyCycleError as exc:
        log.info("coupled: the time to the limit was not computed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# THE CATALOGUE CONSTANTS — the same machine at 20 °C
# ---------------------------------------------------------------------------
# Owner, 2026-09-18: *«для каждого отчёта делать прогон на холодную 20 °C, чтобы
# находить все коэффициенты KV, Kt, Km, Km/mass, которые фигурируют во всех
# каталогах моторов и нужны для сравнения»*.
#
# EVERY constant in this project is reported at the duty's own temperatures,
# which is the honest thing to do and the wrong thing to COMPARE with.  A
# catalogue quotes KV, Kt, Km and Km/kg at room temperature — copper at 20 °C,
# magnets at 20 °C — because that is the only state two machines from two
# manufacturers are ever both in.  A Kt measured with the winding at 172 °C and
# the magnets at 120 °C is 10-15 % below the number on the competitor's page,
# and nothing on either page says so.
#
# So the loop ends with ONE more electromagnetic run at 20/20, at the same
# operating point and on the same drive, NOT fed back into anything: it is a
# measurement of the machine, not a state it is in.  It runs as a BACKGROUND
# solve (`simulation._BACKGROUND_RUN`), which is what keeps it from replacing
# the duty's own run on the Electromagnetic tab, its field snapshot and its
# persisted last-transient — the cold pass must leave no trace except its
# numbers.

#: The datasheet temperature.  20 °C and not 25: the magnet cards this project
#: carries are stated at 20 °C (`<grade>_<T>C`), and picking the copper's
#: reference instead would put the two halves of one constant 5 K apart.
COLD_CONSTANTS_C = 20.0

#: What is read off the cold pass, and how each one travels.  ``flux`` means the
#: quantity goes as the flux and therefore carries the 3-D end-effect factor;
#: ``inv_flux`` means it goes as 1/flux (KV is rpm per volt, so a bigger flux is
#: a SMALLER KV); ``plain`` is a number k_3d has nothing to do with.
_COLD_FLUX = ("psi_pm_Wb", "Kt_Nm_per_Arms", "Kt_Nm_per_A_line",
              "Km_Nm_sqrtW", "Km_per_mass_Nm_sqrtW_kg", "T_em_avg_Nm")
_COLD_INV_FLUX = ("KV_noload_rpm_per_V_line", "KV_rpm_per_V_line")
_COLD_PLAIN = ("Ld_mH", "Lq_mH", "Ld_eq_star_mH", "Lq_eq_star_mH",
               "saliency_Lq_over_Ld", "R_phase_ohm", "R_phase_eq_star_ohm",
               "mass_total_kg", "star_delta", "connection",
               "P_cu_dc_W", "P_cu_exact_W", "I_phase_rms_A")


def _cold_constants(em: Dict[str, Any], *, body: Dict[str, Any],
                    rpm: float, drive: str) -> Optional[Dict[str, Any]]:
    """The 20 °C block, off a cold electromagnetic pass — or ``None``.

    BOTH VALUES ARE KEPT for every flux-proportional constant: the 2-D one the
    solver produced and the one corrected by this geometry's 3-D passport, under
    the report's own §4 conventions — Kt × k_3d, KV ÷ k_3d, and in delta the Kt
    that matters is the one per LINE amp because that is what an inverter is
    rated against.  A machine with no passport carries ``k_3d: null`` and no
    corrected column, exactly as §4 prints an em dash there.
    """
    s = dict((em or {}).get("summary") or {})
    if not s:
        return None
    k3 = None
    try:
        k3 = float(((s.get("end3d") or {}).get("k_flux")))
    except (TypeError, ValueError):
        k3 = None
    if k3 is not None and not (k3 > 0.0):
        k3 = None

    def _n(key):
        v = s.get(key)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    # ── THE NO-LOAD PROBE DOES NOT FOLLOW THE RUN ───────────────────────────
    # `KV_noload_rpm_per_V_line` and `psi_pm_Wb` come from
    # `simulation.noload_psi_pm`, which takes NO run temperature: it is a
    # per-geometry probe at the magnet CARD's own temperature, so it reads the
    # same under every duty of a machine and the same at 20 °C as at 150 °C.
    # Measured on the L13 rated duty (2026-09-18): the cold pass moved Kt by
    # 5.3 % and R by 51 % and left KV at 53.26 rpm/V, bit for bit.
    #
    # A "KV at 20 °C" that is really a KV at 120 °C is exactly the wrong number
    # to put in a catalogue line, so it is WALKED — by the one rule this project
    # already states (`report.kv_at_magnet_temp`): KV goes as 1/Br, Br walks
    # linearly on the card's own reversible coefficient.  A card that carries no
    # dBr/dT leaves both quantities at the probe's value and SAYS so, because a
    # KV at a temperature nothing was measured at would be an invention.
    _grade = ((s.get("demag") or {}).get("magnet_name")
              if isinstance(s.get("demag"), Mapping) else None)
    kv_note = "the no-load probe, at the magnet card's own temperature"
    kv_walked = None
    try:
        from motor_ai_sim.report import kv_at_magnet_temp
        kv_walked, _why = kv_at_magnet_temp(
            s.get("KV_noload_rpm_per_V_line"), _grade, COLD_CONSTANTS_C)
        if kv_walked is not None:
            kv_note = "walked to %g °C — %s" % (COLD_CONSTANTS_C, _why)
    except Exception:  # noqa: BLE001 — a note is not worth losing a block over
        log.debug("coupled: the 20 °C KV could not be walked", exc_info=True)
    s = dict(s)
    if kv_walked is not None:
        # ψ_PM is the same probe read the other way round: KV goes as 1/Br and
        # the flux linkage as Br, so one factor moves both, in opposite
        # directions, and the two cannot drift apart.
        _f_br = float(s["KV_noload_rpm_per_V_line"]) / float(kv_walked)
        s["KV_noload_rpm_per_V_line"] = round(float(kv_walked), 6)
        if _n("psi_pm_Wb") is not None:
            s["psi_pm_Wb"] = round(_n("psi_pm_Wb") * _f_br, 8)

    two_d: Dict[str, Any] = {}
    k3d: Dict[str, Any] = {}
    for key in _COLD_FLUX:
        v = _n(key)
        if v is None:
            continue
        two_d[key] = v
        if k3 is not None:
            k3d[key] = round(v * k3, 6)
    for key in _COLD_INV_FLUX:
        v = _n(key)
        if v is None:
            continue
        two_d[key] = v
        if k3 is not None:
            k3d[key] = round(v / k3, 6)
    for key in _COLD_PLAIN:
        v = s.get(key)
        if v is None:
            continue
        two_d[key] = _n(key) if not isinstance(v, str) else v
    delta = str(s.get("star_delta") or "star").lower().startswith("d")
    return {
        "coil_temp_c": COLD_CONSTANTS_C,
        "magnet_temp_c": COLD_CONSTANTS_C,
        # THE POINT THEY WERE MEASURED AT.  Kt and Km are only constants while
        # the iron is not saturating, so the current they were taken at is part
        # of the answer and not a footnote.
        "point": {"rpm": float(rpm),
                  "I_phase_rms": _f(body, "I_phase_rms", 0.0),
                  "gamma_deg": _f(body, "gamma_deg", 0.0),
                  "drive": str(drive),
                  "star_delta": "delta" if delta else "star"},
        "k_3d": k3,
        "two_d": two_d,
        "k3d": k3d,
        # WHERE THE NO-LOAD KV CAME FROM.  It is the one number in this block
        # that is not a direct reading of the cold pass, and a catalogue line
        # must not hide that.
        "kv_note": kv_note,
        "kv_walked": bool(kv_walked is not None),
        "magnet_grade": (str(_grade) if _grade else None),
        # WHICH Kt A CATALOGUE WOULD PRINT: per LINE amp, which in star is the
        # winding's and in delta is the winding's ÷ √3.
        "kt_line_Nm_per_A": ((k3d if k3 is not None else two_d).get(
            "Kt_Nm_per_A_line" if delta else "Kt_Nm_per_Arms")),
        "kv_line_rpm_per_V": ((k3d if k3 is not None else two_d).get(
            "KV_noload_rpm_per_V_line")),
        "km_Nm_sqrtW": ((k3d if k3 is not None else two_d).get("Km_Nm_sqrtW")),
        "km_per_mass_Nm_sqrtW_kg": ((k3d if k3 is not None else two_d).get(
            "Km_per_mass_Nm_sqrtW_kg")),
        "R_phase_20_ohm": two_d.get("R_phase_ohm"),
        "mass_kg": two_d.get("mass_total_kg"),
        "computed_at": em.get("computed_at"),
        "note": (
            "solved with the winding and the magnets at %g °C at this duty's "
            "operating point — the datasheet convention every motor catalogue "
            "uses, for comparison between machines. The hot values elsewhere "
            "in this document are the same constants at this duty's own "
            "temperatures.%s"
            % (COLD_CONSTANTS_C,
               "" if k3 is None else
               " Kt, Km and Km/mass carry k_3d = %.4f; KV is ÷ k_3d." % k3)),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  SINE vs INVERTER, at the SAME point and the SAME temperatures (2026-09-25)
# ─────────────────────────────────────────────────────────────────────────────
# Owner: «нужно давать сравнение, как изменились характеристики мотора с
# контроллером по сравнению с синусоидой, и тоже указывать это в отчёте».
#
# ONE extra electromagnetic pass after the loop has finished, on the IDEAL
# sinusoidal current source, at the operating point and the temperatures of the
# state the record reports (the loop's last pass, the pass at the limit, or the
# S1 pass).  EQUAL TEMPERATURES, deliberately: letting the sine reference find
# its own thermal state would change the copper resistance, the magnet
# remanence and the iron at once, and the difference printed would then be the
# carrier's AND a colder machine's — two causes in one number.  Held at the
# inverter state's own temperatures, the only thing that differs between the
# columns is the drive, which is the question.  (A separately converged sine
# run, where the duty has one, is still the `reference_sine` record — a
# different question: what each supply reaches on its own.)
#
# THE SAME FUNDAMENTAL, not the same setpoint: the sine pass is fed the
# fundamental current PHASOR the inverter run actually produced (I₁, γ₁ from
# `postproc.fundamental_current`), exactly as the transient route's own
# `harm_ref` reference does — so the torque, voltage and loss differences are
# the harmonics' and the devices' and not a regulator's miss.  Resolution-
# matched: the sine pass uses the inverter run's own steps per period.  Run as
# a BACKGROUND solve, so it never becomes the Electromagnetic tab's last run.

#: (key, label, unit, how the difference is stated) — "pct" = relative change
#: of the value, "pp" = the difference itself (for quantities already in %).
SINE_CMP_ROWS: Tuple[Tuple[str, str, str, str], ...] = (
    ("T_em_avg_Nm", "Torque, mean", "N·m", "pct"),
    ("T_ripple_pct", "Torque ripple", "%", "pp"),
    ("V1_LL_V", "Line voltage, fundamental", "V", "pct"),
    ("V_line_peak_V", "Line voltage, peak", "V", "pct"),
    ("I_phase_rms_A", "Phase current, rms", "A", "pct"),
    ("I1_phase_rms_A", "Phase current, fundamental", "A", "pct"),
    ("THD_I_pct", "Current THD", "%", "pp"),
    ("P_cu_dc_W", "Copper, DC", "W", "pct"),
    ("P_cu_ac_W", "Copper, AC (skin + proximity)", "W", "pct"),
    ("P_stranded_W", "Copper, total", "W", "pct"),
    ("P_core_stator_W", "Iron, stator", "W", "pct"),
    ("P_core_rotor_W", "Iron, rotor", "W", "pct"),
    ("P_mag_W", "Magnets, eddy", "W", "pct"),
    ("P_shaft_W", "Shaft", "W", "pct"),
    ("P_sleeve_W", "Sleeve", "W", "pct"),
    ("P_loss_total_W", "Motor loss, total", "W", "pct"),
    ("eta_shaft_pct", "Shaft efficiency", "%", "pp"),
)


def _sine_cmp_values(em: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """One run's numbers in :data:`SINE_CMP_ROWS`' keys — ``None`` where the
    run did not report the quantity (never a zero standing in for it)."""
    from motor_ai_sim.simulation.postproc import fundamental_current

    s = (em or {}).get("summary") or {}

    def _num(v: Any) -> Optional[float]:
        if isinstance(v, (list, tuple)):
            vals = [float(x) for x in v if isinstance(x, (int, float))
                    and math.isfinite(float(x))]
            return (sum(vals) / len(vals)) if vals else None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    out: Dict[str, Optional[float]] = {k: _num(s.get(k)) for k, *_r in
                                       SINE_CMP_ROWS}
    out["I_phase_rms_A"] = _num(em.get("I_phase_rms_solved_A")) \
        or _num(s.get("I_phase_rms_A"))
    try:
        fc = fundamental_current(em or {})
        out["I1_phase_rms_A"] = (float(fc["I1_phase_rms_A"])
                                 if fc.get("I1_phase_rms_A") else None)
    except Exception:                                   # noqa: BLE001
        out["I1_phase_rms_A"] = None
    dc = _num((em or {}).get("P_cu_dc_W"))
    out["P_cu_dc_W"] = dc
    cu = out.get("P_stranded_W")
    out["P_cu_ac_W"] = (None if (dc is None or cu is None)
                        else max(cu - dc, 0.0))
    eta = _num(s.get("efficiency_shaft"))
    if eta is None:
        eta = _num(s.get("efficiency"))
    out["eta_shaft_pct"] = None if eta is None else 100.0 * eta
    return out


def _sine_cmp_rows(sine: Dict[str, Optional[float]],
                   inv: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    """The table rows: both values and the difference, stated per row."""
    rows: List[Dict[str, Any]] = []
    for key, label, unit, kind in SINE_CMP_ROWS:
        a, b = sine.get(key), inv.get(key)
        if a is None and b is None:
            continue
        d: Optional[float] = None
        if a is not None and b is not None:
            if kind == "pp":
                d = b - a
            elif abs(a) > 1e-12:
                d = 100.0 * (b - a) / abs(a)
        rows.append({"key": key, "label": label, "unit": unit,
                     "sine": None if a is None else round(a, 4),
                     "inverter": None if b is None else round(b, 4),
                     "delta": None if d is None else round(d, 3),
                     "delta_kind": kind})
    return rows


def _sine_comparison_step(body: Dict[str, Any], em: Dict[str, Any], *,
                          coil_temp_c: float, magnet_temp_c: Optional[float],
                          bearing_temp_c: Optional[float],
                          inverter: Dict[str, Any],
                          ctl: Optional["_ControllerLoop"],
                          state_phase: str) -> Optional[Dict[str, Any]]:
    """The ``sine_comparison`` block — never raising (a comparison that could
    not be made is absent and says nothing, never a column of invented
    numbers)."""
    from motor_ai_sim import mech_losses as _ml
    from motor_ai_sim.routes.simulation import _BACKGROUND_RUN
    from motor_ai_sim.simulation.postproc import fundamental_current

    try:
        fc = fundamental_current(em or {})
    except Exception:                                   # noqa: BLE001
        fc = {}
    i1 = _f(fc, "I1_phase_rms_A", 0.0)
    g1 = fc.get("gamma1_deg")
    basis_i = "the inverter run's own fundamental current phasor"
    if not (i1 > 0.0) or g1 is None:
        # No waveform to take the phasor from: the solved rms and the duty's
        # own angle, said so.
        i1 = _f(em or {}, "I_phase_rms_solved_A", 0.0) or _f(body, "I_phase_rms",
                                                              0.0)
        g1 = _f(body, "gamma_deg", 0.0)
        basis_i = "the inverter run's rms current at the duty's own angle"
    else:
        g1 = float(g1)
        if str(body.get("mode") or "motor").strip().lower() == "generator":
            # The transient folds the generator's 180° in itself.
            g1 = ((g1 - 180.0 + 180.0) % 360.0) - 180.0
    if not (i1 > 0.0):
        return None
    ref_body = dict(body)
    ref_body.update(I_phase_rms=round(float(i1), 4), gamma_deg=round(float(g1), 3),
                    n_steps_per_period=int(inverter.get("n_steps_per_period")
                                           or body.get("n_steps_per_period")
                                           or 36))
    tok_bg = _BACKGROUND_RUN.set(True)
    tok_brg = _ml.BEARING_TEMP_C.set(None if bearing_temp_c is None
                                     else float(bearing_temp_c))
    try:
        em_sine = _em_run(ref_body, coil_temp_c=float(coil_temp_c),
                          magnet_temp_c=magnet_temp_c, inverter=None)
    except HTTPException as exc:
        log.warning("coupled: the sine reference pass was refused (%s) — the "
                    "record carries no sine comparison", _detail_text(exc))
        return None
    except Exception:                                   # noqa: BLE001
        log.debug("coupled: the sine reference pass failed", exc_info=True)
        return None
    finally:
        _ml.BEARING_TEMP_C.reset(tok_brg)
        _BACKGROUND_RUN.reset(tok_bg)
    v_s, v_i = _sine_cmp_values(em_sine), _sine_cmp_values(em)
    blk: Dict[str, Any] = {
        "state": str(state_phase),
        "basis": {
            "I1_phase_rms_A": round(float(i1), 4),
            "gamma1_deg": round(float(g1), 3),
            "gamma_duty_deg": _f(body, "gamma_deg", 0.0),
            "current_basis": basis_i,
            "coil_temp_c": round(float(coil_temp_c), 2),
            "magnet_temp_c": (None if magnet_temp_c is None
                              else round(float(magnet_temp_c), 2)),
            "bearing_temp_c": (None if bearing_temp_c is None
                               else round(float(bearing_temp_c), 2)),
            "rpm": _f(body, "rpm", 0.0) or None,
            "n_steps_per_period": int(ref_body["n_steps_per_period"]),
            "temperatures": ("equal — the sine pass is solved at the reported "
                             "state's own winding and magnet temperatures, so "
                             "the drive is the only difference"),
        },
        "rows": _sine_cmp_rows(v_s, v_i),
        "caption": ("Ideal sine current vs the controller's waveform at the "
                    "same fundamental current, speed and temperatures."),
    }
    if ctl is not None and ctl.solve:
        L = ctl.solve.get("losses") or {}
        E = ctl.solve.get("efficiency") or {}
        blk["inverter"] = {
            "P_inverter_W": L.get("total_W"),
            "eta_inverter_pct": (None if E.get("inverter") is None
                                 else round(100.0 * float(E["inverter"]), 3)),
            "eta_wall_to_shaft_pct": (
                None if E.get("wall_to_shaft") is None
                else round(100.0 * float(E["wall_to_shaft"]), 3)),
            "t_j_c": round(float(ctl.t_j_c), 2),
        }
    return blk


# ─────────────────────────────────────────────────────────────────────────────
#  DRIVE = INVERTER: the loop on the SINE, then the controller's PWM once
#  (owner 2026-09-25)
# ─────────────────────────────────────────────────────────────────────────────
# «очень долго идёт каплинг с контроллером, нужно сменить алгоритм: каплинг
# делается только с синусоидой, а последний прогон — с PWM из контроллера».
#
# THE ALGORITHM (``inverter_coupling: "final_pass"``, the default):
#   1. the whole EM ↔ thermal (↔ mechanical) loop runs on the ideal SINE
#      current — steady, to the limit, or to the S1 point, as asked;
#   2. ONE electromagnetic pass on the controller's PWM (carrier, dead time,
#      device drops) at the sine state's temperatures and current, the
#      fundamental seeded from the sine run's own terminal voltage, and the
#      devices solved on it (loss, T_j);
#   3. that pass's own loss map → ONE thermal re-solve.  If the winding /
#      magnet / bearing temperatures move by more than the loop's tolerance (or
#      the current landed outside the inverter's band), ONE more PWM pass at the
#      new temperatures (re-aimed), then its thermal map — never more than two
#      PWM passes and never the full loop on PWM;
#   4. the reported machine is the final PWM state; the sine state is the
#      reference column of ``sine_comparison`` (equal temperatures = pass 1,
#      thermally corrected = the final pass).
#
# ``inverter_coupling: "full"`` keeps the old loop (every pass on the PWM) for
# validation.
INVERTER_COUPLING_MODES = ("final_pass", "full")
PWM_FINAL_MAX_PASSES = 2


def _inverter_coupling_mode(body: Dict[str, Any]) -> str:
    raw = str(body.get("inverter_coupling") or "final_pass").strip().lower()
    if raw not in INVERTER_COUPLING_MODES:
        raise _refuse("inverter_coupling must be one of %s; got %r"
                      % (", ".join(INVERTER_COUPLING_MODES),
                         body.get("inverter_coupling")),
                      ["inverter_coupling"], code="bad_inverter_coupling")
    return raw


def _pwm_final_passes(body: Dict[str, Any], *, cooling: Dict[str, Any],
                      rpm: float, inverter: Dict[str, Any],
                      ctl: "_ControllerLoop", sine_em: Dict[str, Any],
                      coil_c: float, magnet_c: Optional[float],
                      bearing_c: Optional[float], i_body: float,
                      i_target: float, tol: float, adjust_temps: bool,
                      max_passes: int = PWM_FINAL_MAX_PASSES,
                      field_params: Optional[Dict[str, Any]] = None,
                      run_id: str = "") -> Dict[str, Any]:
    """Steps 2–3 above.  Returns ``{"passes": [...], "em", "em_first", "field",
    "coil_c", "magnet_c", "bearing_c", "inverter", "v1_pts", "dc_notes",
    "ripple_quotable", "refusal", "refusal_code", "converged"}``; ``em`` is
    ``None`` when not even the first PWM pass could be solved (the caller then
    keeps the sine state and says why).  ``adjust_temps`` False (``limits``):
    ONE pass only — the limit instant's temperatures are the answer by
    construction, and the PWM thermal map is read for the margin instead."""
    from motor_ai_sim import mech_losses as _ml

    s0 = (sine_em or {}).get("summary") or {}
    inv = dict(inverter)
    # THE FUNDAMENTAL THE SINE RUN MEASURED, at this state — the seed a
    # voltage-fed pass needs to land on the sine's current (the duty's saved
    # seed is at another temperature, and at another current on an S1 point).
    if _f(s0, "V1_seed_peak_V", 0.0) > 0.0:
        inv["v_phase_peak_V"] = float(s0["V1_seed_peak_V"])
        inv["v_phase_peak_seed_V"] = float(s0["V1_seed_peak_V"])
        if s0.get("V1_seed_delta_deg") is not None:
            inv["v_delta_deg"] = float(s0["V1_seed_delta_deg"])
        inv.setdefault("sources", {})
        inv["sources"] = dict(inv.get("sources") or {},
                              v_phase_peak_V=("the sine-converged state's own "
                                              "terminal fundamental"))
    inv["target_I_phase_rms_A"] = float(i_target)
    # The body's own current convention for the pass (the setpoint, or the S1
    # current), and the inverter's target in ITS convention (the winding's).
    body_k = dict(body)
    body_k["I_phase_rms"] = float(i_body)
    ctl.reseed(i_target)
    # THE JUNCTION TEMPERATURE, SEEDED FROM THE SINE STATE — no EM run: the
    # controller solved on the sine pass's own current, power and efficiency,
    # at the modulation index its measured fundamental needs on this link.
    # Without it the first (often the only) PWM pass would read R_DS(on) and
    # V_SD at the Controller's START temperature; with it the pass reads them
    # near where they will settle, and the pass's own step is the residual.
    try:
        from motor_ai_sim.simulation.pwm import modulation_index as _mi
        _m0 = _mi(float(inv["v_phase_peak_V"]), float(inv["v_dc_V"]),
                  star_delta=ctl.star_delta)
        _seed = {"summary": dict(s0),
                 "I_phase_rms_solved_A": float(i_target),
                 "pwm": {"modulation_index": float(_m0)}}
        ctl.step(_seed, it=0, phase="sine_seed")
    except Exception:                                       # noqa: BLE001
        log.debug("coupled: no T_j seed from the sine state", exc_info=True)
    out: Dict[str, Any] = {"passes": [], "em": None, "em_first": None,
                           "field": None, "inverter": inv, "v1_pts": [],
                           "dc_notes": [], "ripple_quotable": True,
                           "refusal": None, "refusal_code": None,
                           "converged": False}
    c_in, m_in, b_in = float(coil_c), magnet_c, bearing_c
    n = max(1, int(max_passes))
    for k in range(1, n + 1):
        _check_cancelled(run_id)
        _progress.update(phase="controller PWM pass %d/%d — coil %.1f °C"
                               % (k, n, c_in))
        tok = _ml.BEARING_TEMP_C.set(None if b_in is None else float(b_in))
        st = ctl.state()
        try:
            em_k = _em_run(body_k, coil_temp_c=c_in, magnet_temp_c=m_in,
                           inverter=inv, controller=ctl, fresh=True)
            _ok, _quot, _dc, _band, _dcnote = _pwm_dc_verdict(
                inv, em_k.get("summary") or {})
            if not _ok:
                raise _refuse("the controller PWM pass %d did not settle its DC "
                              "(%s A left, band %.2f A)"
                              % (k, (em_k.get("summary") or {}).get(
                                  "pwm_dc_residual_A"), float(_band or 0.0)),
                              ["drive"], code="pwm_dc_unconverged")
            if not _quot and _dcnote and _dcnote not in out["dc_notes"]:
                out["dc_notes"].append(_dcnote)
            out["ripple_quotable"] = out["ripple_quotable"] and bool(_quot)
            try:
                _map, _src = _pwm_loss_map(body_k, em_k, inv, coil_temp_c=c_in,
                                           magnet_temp_c=m_in, controller=ctl)
            except _NoLossMap:
                em_k = _em_run(body_k, coil_temp_c=c_in, magnet_temp_c=m_in,
                               inverter=inv, controller=ctl, fresh=True)
                try:
                    _map, _src = _pwm_loss_map(body_k, em_k, inv,
                                               coil_temp_c=c_in,
                                               magnet_temp_c=m_in,
                                               controller=ctl)
                except _NoLossMap as _nm:
                    raise _refuse("the controller PWM pass left no loss map "
                                  "(%s)" % _nm.reason, ["drive"],
                                  code="pwm_no_loss_map")
        except HTTPException as exc:
            ctl.restore(st)
            _ml.BEARING_TEMP_C.reset(tok)
            out["refusal"] = ("controller PWM pass %d refused at coil %.1f °C: "
                              "%s" % (k, c_in, _detail_text(exc)))
            out["refusal_code"] = ("pwm_final_first_refused" if k == 1
                                   else "pwm_final_later_refused")
            log.warning("coupled: %s", out["refusal"])
            break
        _ml.BEARING_TEMP_C.reset(tok)
        d_tj = ctl.step(em_k, it=k, phase="pwm_final")
        _progress.update(phase="controller PWM pass %d/%d — thermal with the "
                               "PWM losses" % (k, n))
        field_k = _thermal_solve(body_k, cooling, coil_temp_c=c_in,
                                 magnet_temp_c=m_in, rpm=rpm,
                                 bearing_temp_c=b_in,
                                 n_steps_per_period=inv["n_steps_per_period"],
                                 em_map=_map, em_loss_source=_src,
                                 params_out=field_params)
        c_out = _bulk_temp(_component(field_k, "winding"))
        m_out = _bulk_temp(_component(field_k, "magnet"))
        _hit = _bearing_temp(field_k)
        b_out = None if _hit is None else float(_hit[0])
        s_k = em_k.get("summary") or {}
        i_k = em_k.get("I_phase_rms_solved_A")
        pe = _point_error_pct(inv, i_k)
        row = {"pass": k, "T_coil_in": round(c_in, 2),
               "T_magnet_in": None if m_in is None else round(float(m_in), 2),
               "T_bearing_in": None if b_in is None else round(float(b_in), 2),
               "T_coil_out": None if c_out is None else round(float(c_out), 2),
               "T_magnet_out": None if m_out is None else round(float(m_out), 2),
               "T_bearing_out": None if b_out is None else round(b_out, 2),
               "T_magnet_max": _component(field_k, "magnet").get("max"),
               "v_phase_peak_V": round(float(inv["v_phase_peak_V"]), 4),
               "I_phase_rms_solved_A": i_k,
               "point_error_pct": None if pe is None else round(pe, 3),
               "T_em_Nm": s_k.get("T_em_avg_Nm"),
               "P_loss_W": s_k.get("P_loss_total_W"),
               "T_junction_c": round(float(ctl.t_j_c), 2),
               "d_T_junction_K": None if d_tj is None else round(float(d_tj), 3),
               "P_inverter_W": ((ctl.solve or {}).get("losses") or {}).get(
                   "total_W")}
        out["passes"].append(row)
        out.update(em=em_k, field=field_k, coil_c=c_in, magnet_c=m_in,
                   bearing_c=b_in, inverter=inv)
        if k == 1:
            out["em_first"] = em_k
        d_c = None if c_out is None else float(c_out) - c_in
        d_m = (None if (m_out is None or m_in is None)
               else float(m_out) - float(m_in))
        d_b = (None if (b_out is None or b_in is None)
               else b_out - float(b_in))
        point_off = pe is not None and abs(pe) > _point_tol_pct(inv)
        if adjust_temps:
            settled = ((d_c is None or abs(d_c) < tol)
                       and (d_m is None or abs(d_m) < tol)
                       and (d_b is None or abs(d_b) < BEARING_TOL_K)
                       and not point_off)
        else:
            # `limits`: the instant's temperatures are the answer; only the
            # CURRENT is re-aimed (a voltage-fed pass seeded from the sine's
            # fundamental lands short by the devices' drop and dead time).
            settled = not point_off
        out["converged"] = bool(settled)
        if settled or k >= n or c_out is None:
            break
        # ONE MORE PWM PASS — at the temperatures the PWM losses produced
        # (not on `limits`), re-aimed at the current when it landed outside
        # the band.
        if adjust_temps:
            c_in = float(c_out)
            m_in = m_in if m_out is None else float(m_out)
            b_in = b_in if b_out is None else b_out
        _nx = _regulate_v1(inv, i_k, out["v1_pts"])
        if _nx is not None:
            inv = _nx
    return out


def _pwm_final_block(fin: Dict[str, Any], *, sine_coil_c: float,
                     sine_magnet_c: Optional[float],
                     sine_bearing_c: Optional[float], tol: float,
                     t_wall_s: float) -> Dict[str, Any]:
    """The record's ``pwm_final`` block: what the PWM passes did to the sine
    state, in numbers."""
    p = fin.get("passes") or []
    last = p[-1] if p else {}

    def _d(a, b):
        return (None if (a is None or b is None)
                else round(float(a) - float(b), 2))
    return {
        "algorithm": "sine loop, then the controller's PWM on the converged "
                     "state (inverter_coupling: final_pass)",
        "passes": p,
        "n_pwm_passes": len(p),
        "converged": bool(fin.get("converged")),
        "tol_K": float(tol),
        # How far the PWM losses moved the machine from the sine state — the
        # final PWM pass's own thermal map against the sine-converged temps.
        "dT_vs_sine_K": {
            "winding": _d(last.get("T_coil_out"), sine_coil_c),
            "magnet": _d(last.get("T_magnet_out"), sine_magnet_c),
            "bearing": _d(last.get("T_bearing_out"), sine_bearing_c),
        },
        # …and how far the last PWM pass's own map still sits from the
        # temperatures that pass was solved at.
        "residual_K": {
            "winding": _d(last.get("T_coil_out"), last.get("T_coil_in")),
            "magnet": _d(last.get("T_magnet_out"), last.get("T_magnet_in")),
            "bearing": _d(last.get("T_bearing_out"), last.get("T_bearing_in")),
        },
        "wall_s": round(float(t_wall_s), 1),
        **({"refusal": fin["refusal"], "refusal_code": fin["refusal_code"]}
           if fin.get("refusal") else {}),
    }


def _sine_cmp_final(sine_em: Dict[str, Any], em_first: Dict[str, Any],
                    em_last: Dict[str, Any], *, state_phase: str,
                    sine_temps: Tuple[float, Optional[float]],
                    last_temps: Tuple[float, Optional[float]],
                    ctl: Optional["_ControllerLoop"],
                    body: Dict[str, Any]) -> Dict[str, Any]:
    """``sine_comparison`` for the final-pass algorithm — no extra solve: the
    sine column is the sine-converged state, ``inverter`` the first PWM pass
    (SAME temperatures), ``inverter_corrected`` the final PWM state when a
    second pass moved the temperatures."""
    v_s = _sine_cmp_values(sine_em)
    v_1 = _sine_cmp_values(em_first)
    rows = _sine_cmp_rows(v_s, v_1)
    corrected = em_last is not None and em_last is not em_first
    if corrected:
        v_2 = _sine_cmp_values(em_last)
        by_key = {r["key"]: r for r in _sine_cmp_rows(v_s, v_2)}
        for r in rows:
            r2 = by_key.get(r["key"]) or {}
            r["inverter_corrected"] = r2.get("inverter")
            r["delta_corrected"] = r2.get("delta")
    blk: Dict[str, Any] = {
        "state": str(state_phase),
        "algorithm": "final_pass",
        "basis": {
            "I_setpoint_A": _f(body, "I_phase_rms", 0.0),
            "gamma_duty_deg": _f(body, "gamma_deg", 0.0),
            "coil_temp_c": round(float(sine_temps[0]), 2),
            "magnet_temp_c": (None if sine_temps[1] is None
                              else round(float(sine_temps[1]), 2)),
            "corrected_coil_temp_c": round(float(last_temps[0]), 2),
            "corrected_magnet_temp_c": (None if last_temps[1] is None
                                        else round(float(last_temps[1]), 2)),
            "temperatures": ("'inverter' is the controller's PWM at the sine "
                             "state's own temperatures (equal temperatures, "
                             "the drive the only difference); "
                             "'inverter_corrected' is the PWM state after its "
                             "own losses were fed back"
                             if corrected else
                             "equal — the PWM pass is solved at the sine "
                             "state's own temperatures, and its own losses "
                             "moved them by less than the loop's tolerance"),
        },
        "rows": rows,
        "has_corrected": bool(corrected),
        "caption": ("Ideal sine current (the converged loop) vs the "
                    "controller's PWM at the same current setpoint and "
                    "temperatures%s." % (", then with its own losses fed back"
                                         if corrected else "")),
    }
    if ctl is not None and ctl.solve:
        L = ctl.solve.get("losses") or {}
        E = ctl.solve.get("efficiency") or {}
        blk["inverter"] = {
            "P_inverter_W": L.get("total_W"),
            "eta_inverter_pct": (None if E.get("inverter") is None
                                 else round(100.0 * float(E["inverter"]), 3)),
            "eta_wall_to_shaft_pct": (
                None if E.get("wall_to_shaft") is None
                else round(100.0 * float(E["wall_to_shaft"]), 3)),
            "t_j_c": round(float(ctl.t_j_c), 2),
        }
    return blk


def _cold_constants_step(body: Dict[str, Any], *, rpm: float, drive: str,
                         inverter: Optional[Dict[str, Any]]
                         ) -> Optional[Dict[str, Any]]:
    """Run the cold pass and read the constants off it — never raising.

    As a BACKGROUND solve: no field snapshot, no persisted last transient, no
    run-journal entry, so the duty's own run on the Electromagnetic tab is still
    the run the user pressed.  A refusal at 20 °C is logged and the block is
    simply absent — a machine that could not be solved cold has no catalogue
    constants, and inventing them by extrapolating the hot ones is exactly what
    this pass exists to replace.
    """
    from motor_ai_sim.routes.simulation import _BACKGROUND_RUN

    tok = _BACKGROUND_RUN.set(True)
    try:
        em = _em_run(body, coil_temp_c=COLD_CONSTANTS_C,
                     magnet_temp_c=COLD_CONSTANTS_C, inverter=inverter)
    except HTTPException as exc:
        log.warning("coupled: the 20 °C constants pass was refused (%s) — the "
                    "record carries no catalogue constants",
                    _detail_text(exc))
        return None
    except Exception:  # noqa: BLE001 — never fails a solved run
        log.debug("coupled: the 20 °C constants pass failed", exc_info=True)
        return None
    finally:
        _BACKGROUND_RUN.reset(tok)
    blk = _cold_constants(em, body=body, rpm=rpm, drive=drive)
    if blk is not None:
        blk.update(_cold_ldq0(em, body) or {})
    return blk


def _cold_ldq0(em: Dict[str, Any],
               body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """CATALOGUE Ld / Lq: incremental, at i = 0, at 20 °C — never raising.

    The owner's rule of 2026-09-20: *«Ld/Lq нужно указывать тоже для 20
    градусов и без тока, как для KV»*.  KV is a no-load constant at a stated
    temperature; so are the inductances a control engineer sizes a loop with,
    and a catalogue that quotes one at no load and the other at 600 A is
    comparing two different machines.  The LOADED point's own inductances stay
    where they are — in the run's summary, incremental, at that point.

    One extra cheap no-load solve per duty (the ψ_PM calibration knobs), cached
    on disk per geometry/winding/magnet temperature, so a re-run of the same
    machine pays nothing.
    """
    from motor_ai_sim.routes._validation import parse_geo_override
    from motor_ai_sim.routes.simulation import catalogue_ldq0

    s = dict((em or {}).get("summary") or {})
    # THE D-AXIS STAMP SITS ON THE RESULT, NOT IN ITS SUMMARY.  The transient
    # route returns ``daxis_deg`` / ``daxis_source`` beside ``summary`` (what
    # ``_transient_ledger_row`` reads as ``result.get("daxis_deg")``), and the
    # summary itself carries no d-axis key at all — so a read of the summary
    # alone never probed on a real run and every cold block came back without
    # Ld0/Lq0 (measured 2026-09-20 on the L180 rated duty through
    # ``POST /api/coupled/constants_20c``).  Both places are read; the result's
    # own stamp wins.
    _dax = (em or {}).get("daxis_deg")
    if _dax is None:
        _dax = s.get("daxis_deg")
    if _dax is None:
        return None
    try:
        ov = parse_geo_override(body.get("geo")) or None
    except Exception:   # noqa: BLE001 — the live machine, then
        ov = None
    blk = catalogue_ldq0(ov, daxis_deg=float(_dax),
                         connection=(str(s.get("connection") or "") or None),
                         magnet_temp_c=COLD_CONSTANTS_C)
    if not blk or blk.get("Ld_mH") is None:
        return None
    _ld, _lq = float(blk["Ld_mH"]), float(blk["Lq_mH"])
    return {
        "Ld0_mH": round(_ld, 4),
        "Lq0_mH": round(_lq, 4),
        "Ldq0_mH": (None if blk.get("Ldq_mH") is None
                    else round(float(blk["Ldq_mH"]), 4)),
        "saliency0_Lq_over_Ld": (round(_lq / _ld, 3) if abs(_ld) > 1e-9
                                 else None),
        "ldq0_method": ("frozen-permeability incremental at i=0, %g °C"
                        % COLD_CONSTANTS_C),
        # The probe's own self-checks, kept: `spread_pct` is how much L(θ)
        # moves with rotor position (these are the average of four positions),
        # `reciprocity_pct` the asymmetry of a matrix that must be symmetric.
        "ldq0_spread_pct": blk.get("spread_pct"),
        "ldq0_reciprocity_pct": blk.get("reciprocity_pct"),
    }


def _limited_block(time_to_limit: Optional[Dict[str, Any]],
                   *, cooling: Dict[str, Any], field: Dict[str, Any],
                   passes: int, steady_converged: bool,
                   steady_runaway: bool) -> Optional[Dict[str, Any]]:
    """THE LIMITED STATE this run is about to be re-computed at, or ``None``.

    ``None`` — and the loop's own steady answer stands — on all three of:

      * the point is inside every limit this machine states (there is no first
        crossing, so there is no moment to report);
      * the step response never reaches the limit at all.  The map is then over
        it for a reason the four-node network does not represent, and the
        STEADY state is the answer (the brief's own rule): a time extrapolated
        out of a curve that flattens first would be an invented number;
      * no block could be formed.

    ``steady_state_would_be`` is what the pass this network was fitted to says
    each part reaches — with ``steady_state_converged`` beside it, because in
    this mode the loop STOPS at the first over-limit pass rather than iterating
    towards a state the machine cannot hold, so that number is a reading off
    the last map and not a converged fixed point.

    THE TEMPERATURES THE FINAL ELECTROMAGNETIC PASS IS SOLVED AT (``em_pass_at``,
    owner 2026-09-18 on the live site: *«так и расчёт тогда должен быть при
    катушках в 200 градусов, а не 184»*).  THE RULE: the EM pass of a limited
    state uses, for each part, the temperature the LIMIT is judged on (the
    winding HOT SPOT, the HOTTEST magnet element — never the node mean), with
    the limiting part exactly at its limit.  So when the winding limits, the
    coils are solved AT the class temperature (200 °C) and the magnets at their
    hottest element of that instant (43.8 °C node → 45.2 °C); when the magnet
    limits, the magnets are solved at the card's limit and the coils at their
    hot spot of that instant.  The resistances, the copper loss, the torque, η,
    KV/Kt and the voltages of the record are then those of a winding that IS at
    200 °C — conservative, and consistent with the sentence "then the winding
    reaches 200 °C" — instead of those of the 183.5 °C node mean, which is a
    machine 16 K colder than the one the sentence describes.

    ``temperatures_at_limit`` (the node means) and ``at_limit_c`` (each judged
    quantity) are both kept: the map is translated onto the node means, §8 is
    judged on the quantities, and ``em_pass_at`` says which numbers the
    electromagnetic ones were solved at.
    """
    from motor_ai_sim.thermal_settings import cooling_words

    lim = _ttl.limiting(time_to_limit)
    if not lim:
        return None
    t = dict(time_to_limit or {})
    out: Dict[str, Any] = {
        "part": lim["part"],
        "quantity": lim["quantity"],
        "limit_c": lim["limit_c"],
        "limit_source": lim["limit_source"],
        "t_cold_s": lim["t_cold_s"],
        "t_cold_words": _ttl.fmt_seconds(lim["t_cold_s"]),
        "t_rated_s": lim["t_rated_s"],
        "t_rated_words": (None if lim["t_rated_s"] is None
                          else _ttl.fmt_seconds(lim["t_rated_s"])),
        # THE NODE MEANS at the crossing — what the thermal map is translated
        # onto (the network integrates node means).
        "temperatures_at_limit": dict(lim["temperatures_at_limit"]),
        # …and each part's own JUDGED quantity there (the hot spot, the hottest
        # element, the seat): what §8 judges, what the maps must show, and —
        # since 2026-09-18 — what the final electromagnetic pass is solved at
        # (`em_pass_at`, below), so the record's temperatures are these.
        "at_limit_c": {},
        "steady_state_would_be": dict(t.get("at_point_c") or {}),
        "steady_state_converged": bool(steady_converged),
        "steady_state_runaway": bool(steady_runaway),
        "calibration_passes": int(passes),
        # THE COOLING THIS ANSWER IS CONDITIONAL ON (owner addendum 2026-09-18):
        # the duty's own boundary, the one the loop solved with — never a
        # default.  Named here so the line can say "at this power and cooling"
        # and the tooltip can say WHICH cooling.
        "cooling": dict(cooling or {}),
        "cooling_words": cooling_words(cooling or {}),
    }
    for row in (t.get("parts") or ()):
        if not isinstance(row, dict):
            continue
        st = row.get("state_c")
        part, node = str(row.get("part") or ""), str(row.get("node") or "")
        off = row.get("offset_K")
        if part == lim["part"] and lim["limit_c"] is not None:
            out["at_limit_c"][part] = round(float(lim["limit_c"]), 2)
        elif isinstance(st, Mapping) or node in lim["temperatures_at_limit"]:
            base = lim["temperatures_at_limit"].get(node)
            if base is not None and off is not None:
                out["at_limit_c"][part] = round(float(base) + float(off), 2)
    # A part that is NOT over its limit has no row above (the step response is
    # only integrated for the parts that are over), so its judged quantity at
    # the instant is read the same way the network reads it: the node mean of
    # the crossing plus the map's own max − mean, frozen at the map's shape.
    # Without this the magnets of the L13 peak (43.8 °C, far inside their card)
    # would have no "hottest element" to solve the final pass at.
    for part, node in (("winding", "winding"), ("magnet", "magnet")):
        if part in out["at_limit_c"]:
            continue
        base = lim["temperatures_at_limit"].get(node)
        if base is None:
            continue
        off, _why = _ttl._offset(field or {}, node)
        out["at_limit_c"][part] = round(float(base) + float(off), 2)
    # ── WHAT THE FINAL ELECTROMAGNETIC PASS IS SOLVED AT (owner 2026-09-18) ──
    # The rule in the docstring: each part at the temperature its limit is
    # judged on, the limiting part exactly at its limit.  `at_limit_c` already
    # IS that — the limiting part's entry is its limit by construction, every
    # other part's is its hot spot / hottest element of the instant — so the
    # pass is solved at those two numbers and the record says so by name.
    _coil_at = out["at_limit_c"].get("winding")
    _mag_at = out["at_limit_c"].get("magnet")
    out["em_pass_at"] = {
        "coil_c": (None if _coil_at is None else round(float(_coil_at), 2)),
        "magnet_c": (None if _mag_at is None else round(float(_mag_at), 2)),
        "coil_basis": ("the winding limit" if lim["part"] == "winding"
                       else "the winding hot spot at that instant"),
        "magnet_basis": ("the magnet limit" if lim["part"] == "magnet"
                         else "the hottest magnet element at that instant"),
        "rule": ("the electromagnetic pass of a limited state uses, for each "
                 "part, the temperature the LIMIT is judged on (hot spot / "
                 "hottest element), the limiting part exactly at its limit"),
    }
    # The bearing seat rides the rotor node whether or not it is JUDGED — the
    # summary of the final pass is billed at it, so it is resolved here from the
    # same map the offsets come from.
    _seat = _bearing_temp(field)
    _rotor = _bulk_temp(_component(field, "rotor"))
    _r_lim = lim["temperatures_at_limit"].get("rotor")
    if _seat is not None and _rotor is not None and _r_lim is not None:
        out["bearing_seat_at_limit_c"] = round(
            float(_r_lim) + (float(_seat[0]) - float(_rotor)), 2)
    out["line"] = _ttl.limited_line(time_to_limit)
    _ep = out["em_pass_at"]
    out["note"] = (
        "%s. Every number in this record is the machine at that moment: the "
        "electromagnetic run was made once more with each part at the "
        "temperature its limit is judged on — the winding at %s (%s), the "
        "magnets at %s (%s) — and the thermal map is the last solved map "
        "translated onto the node temperatures of that instant (its shape "
        "frozen, as the time-to-limit model states). Cooling: %s."
        % (out["line"].rstrip("."),
           ("—" if _ep["coil_c"] is None else "%.1f °C" % _ep["coil_c"]),
           _ep["coil_basis"],
           ("—" if _ep["magnet_c"] is None else "%.1f °C" % _ep["magnet_c"]),
           _ep["magnet_basis"],
           out["cooling_words"] or "as solved"))
    return out


def _cycle_refusal(exc: "_cdc.DutyCycleError", duty: str) -> HTTPException:
    """A cycle the loop cannot solve, as this router's 422 — by NAME, with the
    model's own code, so a client switches on the code and a panel prints the
    sentence."""
    return _refuse(
        "the coupled loop could not solve duty %r as a cycle: %s  %s"
        % (duty, getattr(exc, "message", str(exc)), getattr(exc, "remedy", "")),
        ["duty_cycle"], code=getattr(exc, "code", "duty_cycle_refused"))


def _preflight(body: Dict[str, Any], *,
               max_iter: Optional[int] = None) -> None:
    """Everything that would make the loop fail AFTER an electromagnetic run.

    Every check here is one the thermal side already makes — but it makes it on
    the far side of a transient the user has already paid for, and its message
    ("solved at another operating point") does not say which of the two
    parameters was the one that did not line up.  A loop that cannot possibly
    close must refuse before it burns a solve.

    ``max_iter`` is the EFFECTIVE iteration budget (the body's, else the Thermal
    panel's).  It no longer decides whether an impulse duty may run — since
    2026-09-16 it may, and the loop finds its regime — but it is still part of
    this signature because callers pass it and because a future check may need
    to know whether this is a loop or a single pass.
    """
    from motor_ai_sim.routes.simulation import _effective_rpm, _effective_winding

    _cycle_preflight(body)
    drive = _coupled_drive(body)
    if drive == "current" and int(body.get("n_steps_per_period") or 0) <= 1:
        raise _refuse(
            "a coupled run needs a CYCLE: the loss map the thermal solve reads "
            "is a period average, and a single frame has no period. Ask for "
            "n_steps_per_period > 1.", ["n_steps_per_period"])
    if not bool(body.get("eddy", True)) or not bool(body.get("rotor_eddy", True)):
        raise _refuse(
            "the coupled loop needs the conducting solve on BOTH sides — eddy "
            "(copper) and rotor_eddy (magnets, shaft, sleeve). Without them the "
            "rotor is heated by nothing, so the magnet temperature it feeds back "
            "would be the ambient.", ["eddy", "rotor_eddy"])

    # The two fields the thermal loss-map probe resolves from the SHARED CONFIG
    # instead of from the request (`_loss_snapshot_probe` deliberately does not
    # take them).  When the Electromagnetic tab's own value differs, the run this
    # loop makes can never be the run the thermal side looks for — and the 422
    # that follows blames the operating point.  Say which one, before the solve.
    rpm = body.get("rpm")
    if rpm is not None and round(_effective_rpm(rpm), 3) != round(_effective_rpm(None), 3):
        raise _refuse(
            "the coupled loop's thermal half looks its loss map up at the shared "
            "configuration's speed (%.0f rpm) while this run asks for %.0f rpm, "
            "so the two halves would describe different machines. Save the speed "
            "to the machine first." % (_effective_rpm(None), _effective_rpm(rpm)),
            ["rpm"])
    try:
        want = _effective_winding(body.get("n_parallel"), body.get("connection"))
        have = _effective_winding(None, None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if want != have:
        raise _refuse(
            "the coupled loop's thermal half resolves the winding from the shared "
            "configuration (%s) while this run asks for %s — the loss map of one "
            "is not the loss map of the other. Save the connection to the machine "
            "first." % (have[1] or "n_parallel %d" % have[0],
                        want[1] or "n_parallel %d" % want[0]), ["connection"])


# ---------------------------------------------------------------------------
# The two halves
# ---------------------------------------------------------------------------

def _pwm_run_kwargs(inv: Dict[str, Any]) -> Dict[str, Any]:
    """What ``drive: "pwm"`` adds to the electromagnetic run's arguments.

    Nothing here is a second PWM implementation: it is the same argument set
    ``POST /api/kernel/run`` carries for ``drive="pwm_voltage"``, so the run the
    loop makes is byte-for-byte a run the user could make by hand — the
    star-equivalent substitution for a delta machine, the modulation gate, the
    settled DC anchor and the ``pwm`` block in the result all come from the one
    implementation in ``routes.simulation``.

    ``harm_ref`` is OFF by default, and that is a COST decision measured on this
    machine (2026-09-15): the resolution-matched sinusoidal reference is a
    second full transient inside every iteration — 53 minutes of the first
    L155 peak run's 3 hours, paid three times over for a number the loop never
    reads.  The comparison the report prints is against ``reference_sine``, the
    duty's own settled sine answer, so the in-run reference buys nothing here.
    A caller that wants it (a single-pass measurement of what the carrier costs
    at one fixed resolution) asks for ``inverter.harm_ref: true``.
    """
    return dict(
        drive="pwm_voltage",
        v_bus=float(inv["v_dc_V"]),
        f_switch=float(inv["f_carrier_hz"]),
        v_phase_peak=float(inv["v_phase_peak_V"]),
        v_delta_deg=float(inv["v_delta_deg"]),
        n_steps_per_period=int(inv["n_steps_per_period"]),
        harm_ref=bool(inv.get("harm_ref", False)),
    )


def _regulate_v1(inv: Dict[str, Any], i_solved: Optional[float],
                 pts: List[tuple]) -> Optional[Dict[str, Any]]:
    """Move the applied fundamental so the NEXT pass sits at the duty's current.

    WHY (2026-09-15).  A voltage-fed run answers with whatever current its own
    back-EMF and impedance allow, and over a coupled loop that answer MOVES:
    the first L155 peak run went 442.8 → 496.1 → 500.8 A as the magnets settled,
    and reported 280.5 N·m where the duty is 230.  That is an honest answer to
    a question nobody asked — the report's premise is "this duty, on PWM", not
    "this inverter setting, wherever the machine ends up".

    So when ``inverter.target_I_phase_rms_A`` is given, each pass re-aims:

      * pass 1 has ONE point and cannot know dI/dV (V₁ ≈ E + I·Z, and E is most
        of it), so it takes a DAMPED proportional step — right direction, no
        wild swing on a machine whose current is a small difference of two
        large voltages;
      * from pass 2 there are two (V₁, I) points and it is a plain SECANT,
        which is exact for the affine relation the machine actually has.

    Returns the updated inverter block, or ``None`` when there is nothing to
    regulate (no target, or a current the run did not report).  ``v_delta_deg``
    is deliberately NOT moved: the load angle is the duty's, taken from the
    sine seed, and letting the regulator chase two variables at once would make
    the point it lands on unreproducible.
    """
    target = inv.get("target_I_phase_rms_A")
    if not target or i_solved is None:
        return None
    try:
        target = float(target)
        i_now = float(i_solved)
    except (TypeError, ValueError):
        return None
    if not (target > 0.0 and i_now > 0.0 and math.isfinite(i_now)):
        return None
    v_now = float(inv["v_phase_peak_V"])
    pts.append((v_now, i_now))
    if abs(i_now - target) / target <= float(inv.get("i_tol_pct", 1.0)) / 100.0:
        return None                       # already there — do not nudge it off
    # THE SECANT NEEDS TWO DIFFERENT FUNDAMENTALS, not just two passes.  When
    # pass 1 landed inside the band nothing was nudged, so pass 2 ran at the SAME
    # V₁ — and a secant through (V, I₁) and (V, I₂) has zero run, which reduced
    # to `v_new = v_now` and was reported as "next V1 None" (CIANO10 200 opt /
    # L155 'rated 1x9 mm', 2026-09-15: pass 1 at +0.17 %, pass 2 drifting to
    # −3.16 % as the winding heated, and no correction to show for it).  With one
    # usable direction only, fall back to the damped proportional step.
    if (len(pts) >= 2 and abs(pts[-1][1] - pts[-2][1]) > 1e-9
            and abs(pts[-1][0] - pts[-2][0]) > 1e-9):
        (va, ia), (vb, ib) = pts[-2], pts[-1]
        v_new = vb + (target - ib) * (vb - va) / (ib - ia)
    else:
        v_new = v_now * (1.0 + _V1_FIRST_STEP_GAIN * (target / i_now - 1.0))
    # A fundamental outside ±40 % of the seed is not a regulator converging, it
    # is one that has lost the machine — clamped, and the loop says so in the
    # record rather than solving an inverter nobody can build.
    lo, hi = 0.6 * float(inv["v_phase_peak_seed_V"]), 1.4 * float(inv["v_phase_peak_seed_V"])
    # …and the SECOND ceiling, which is the inverter's own: a fundamental past
    # the linear-modulation limit on this DC link is a 422 from the
    # electromagnetic half, i.e. a loop that dies hours in over a step it could
    # have declined.  Aim at the ceiling instead and say so — the point is then
    # simply not reachable on this bus, which the record reports as an off-point
    # run rather than as a crash.  (Absent key = older caller: unchanged.)
    # …and it is the COMPENSATED ceiling (`_modulator_gain`): the largest
    # fundamental the modulator can be made to APPLY, not the largest reference
    # the bridge can chop.  Between the two there is a band that passes this
    # clamp and is then refused by the electromagnetic half.
    v_cap = inv.get("v_phase_peak_max_V")
    capped = False
    if v_cap:
        try:
            if v_new > float(v_cap) > 0.0:
                v_new, capped = float(v_cap), True
        except (TypeError, ValueError):
            pass
    v_new = min(max(v_new, lo), hi)
    if abs(v_new - v_now) < 1e-6:
        return None
    out = dict(inv)
    out["v_phase_peak_V"] = float(v_new)
    # Written on every step, True or False: a stale True carried forward from an
    # earlier pass would report a ceiling the run had since walked away from.
    out["v_phase_peak_at_modulation_ceiling"] = bool(capped)
    return out


def _point_error_pct(inv: Optional[Dict[str, Any]],
                     i_solved: Optional[float]) -> Optional[float]:
    """Signed per-cent miss of a voltage-fed pass against the duty's current.

    ``None`` when there is nothing to judge — a sine loop (no inverter), a run
    with the fundamental pinned instead of a target, or a pass that reported no
    current.  Positive means the machine drew MORE than the duty asks for.  One
    definition, shared by the convergence test and the record, so the loop can
    never stop on a point the record then calls off.
    """
    if not inv:
        return None
    target = inv.get("target_I_phase_rms_A")
    if not target or i_solved is None:
        return None
    try:
        target = float(target)
        i_now = float(i_solved)
    except (TypeError, ValueError):
        return None
    if not (target > 0.0 and math.isfinite(i_now)):
        return None
    return 100.0 * (i_now - target) / target


def _point_tol_pct(inv: Optional[Dict[str, Any]]) -> float:
    """The band ``point_error_pct`` is judged against [%] — the same
    ``inverter.i_tol_pct`` the regulator stops nudging inside."""
    try:
        return float((inv or {}).get("i_tol_pct") or 1.0)
    except (TypeError, ValueError):
        return 1.0


#: THE DC BAND a PWM coupled run may still be BILLED on, as a fraction of the
#: current the run is aimed at (2026-09-15).  Above the solver's own settling
#: tolerance the orbit is not closed and the ripple belongs to the offset rather
#: than to the machine — but the LOSSES do not: a residual of 4.66 A on a 445 A
#: fundamental is 0.01 % of I², i.e. nothing a temperature field can feel.
#: Refusing the whole loop over it cost a night (the L155 peak run died after 87
#: minutes on its first pass), so inside this band the run CONTINUES, the record
#: says ``dc_unconverged: true`` and ``ripple_quotable: false``, and the report
#: prints "not quotable" for the torque and current ripple while the watts and
#: the temperatures stand.  Outside it the refusal is unchanged: a machine whose
#: phase current carries several per cent of DC is not the machine.
#:
#: TWO PER CENT and not one: the measured case was 4.66 A on a 445 A
#: fundamental, which is 1.05 % — a band drawn at 1 % would have failed the
#: same night for the sake of five hundredths of a per cent.  On the quantity
#: that decides the losses it is 0.04 % of I².
PWM_DC_BAND_FRACTION = 0.02
#: …and the floor, for a run with no current to measure the fraction against.
PWM_DC_BAND_FLOOR_A = 0.5


def _pwm_dc_reference_a(inv: Dict[str, Any], summary: Dict[str, Any]) -> float:
    """The current the DC residual is judged AGAINST: what this run is aimed at,
    else what it actually drew."""
    ref = inv.get("target_I_phase_rms_A") or summary.get("I1_phase_rms_A")
    try:
        return abs(float(ref or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _pwm_dc_band_a(inv: Dict[str, Any], summary: Dict[str, Any]) -> float:
    """How much DC this run may carry and still be worth its losses [A]."""
    return max(PWM_DC_BAND_FLOOR_A,
               PWM_DC_BAND_FRACTION * _pwm_dc_reference_a(inv, summary))


def _pwm_dc_verdict(inv: Dict[str, Any], summary: Dict[str, Any]) -> tuple:
    """``(ok, quotable, residual, band, note)`` — what to do about this run's DC.

    ``ok`` False is the refusal (outside the band).  ``quotable`` False means
    the run stands but its RIPPLE does not: a settling offset makes the torque
    ripple and the current ripple report the offset, not the machine (B5 / PWM
    study §1.9 measured 55 % where the settled orbit reads 25 %).
    """
    if not summary.get("pwm_dc_unconverged"):
        return True, True, summary.get("pwm_dc_residual_A"), None, None
    try:
        dc = abs(float(summary.get("pwm_dc_residual_A") or 0.0))
    except (TypeError, ValueError):
        dc = float("inf")
    band = _pwm_dc_band_a(inv, summary)
    ref = _pwm_dc_reference_a(inv, summary)
    note = ("the PWM run did not settle: %s A of DC is left in phase %s of the "
            "reported period (the solver's tolerance is %s A). That is %.2f %% "
            "of the %.1f A this run is billed at — %.3f %% of I², which no "
            "temperature field can feel — so the LOSSES and the temperatures "
            "stand, but the torque ripple and the ripple current belong to the "
            "offset and are NOT quotable."
            % (summary.get("pwm_dc_residual_A"),
               summary.get("pwm_dc_residual_phase"),
               summary.get("pwm_dc_tol_A"),
               100.0 * dc / max(ref, 1e-9), ref,
               100.0 * (dc / max(ref, 1e-9)) ** 2))
    return dc <= band, False, summary.get("pwm_dc_residual_A"), band, note


#: How much of a pure proportional step the FIRST current correction takes.
#: V₁ is dominated by the back-EMF, so I is a small difference of two large
#: numbers and dI/dV is far larger than I/V — a full proportional step would
#: overshoot by a factor of several.  One third is conservative in the direction
#: that cannot diverge; pass 2 replaces it with a real secant.
_V1_FIRST_STEP_GAIN = 0.3


# ---------------------------------------------------------------------------
# STAGE 2 — THE CONTROLLER AS THE DRIVE (2026-09-22)
# ---------------------------------------------------------------------------
# Owner: *«как закончишь лимиты, запускай каплинг — сначала стандартный инвертор
# на L155 motor»*, and, the decision that shapes it: *«как отладим каплинг с
# контроллером, нам не нужен будет PWM в электромагнитном моделировании — всё
# будет задаваться в меню Controller»*.
#
# WHAT THIS ADDS TO ``drive: "pwm"`` is the DEVICE, and with it a second fixed
# point.  The fundamental regulator (``_regulate_v1``) and every ceiling, every
# DC gate and every loss-map path are reused unchanged; what is new is:
#
#   1. the excitation carries the dead-time clamp and the channel/diode drops of
#      a real part (``inverter/coupling.py``), decided by the current the solver
#      has just measured — so the inner controller↔machine loop is closed at the
#      FEM time-step level, not by an outer relaxation;
#   2. the losses those currents cause in the DEVICES set a junction
#      temperature, that temperature moves ``R_DS(on)`` and ``V_SD``, and the
#      next pass is solved with the moved numbers.  ``T_j`` is therefore a
#      residual of this loop exactly as the winding and the magnets are, and it
#      is tested with them.
#
# It costs NO extra electromagnetic run: the controller solve is arithmetic over
# a card and it rides on the pass the loop was going to make anyway.

#: Junction-temperature band the coupled loop closes on [K].  Two kelvin, not
#: the controller module's own 0.1 K: that tolerance is the inner solve's
#: (how exactly ONE controller solve settles against its coldplate), while this
#: one is the OUTER loop's, and a device whose R_DS(on) moves 0.6 % per kelvin
#: does not need the outer loop chased to a tenth.  Two kelvin is ~1.2 % of the
#: channel resistance, which is inside the figure-read tolerance of the curve
#: it comes from.
CONTROLLER_TJ_TOL_K = 2.0

#: The dead time used when neither the request nor the duty's stored controller
#: record names one [µs].  The IMCQ120R004M2H datasheet publishes no
#: RECOMMENDED dead time — it publishes switching TIMES, from which AN2025-10
#: eqs. (22)-(26) build a minimum — so this is Stage 1's design value and it is
#: reported as one.  ``_dead_time_floor_ns`` below states the device's own
#: floor beside it, so the margin is visible rather than asserted.
CONTROLLER_DEAD_TIME_US = 0.5


def _dead_time_floor_ns(card) -> Optional[float]:
    """The device's own dead-time floor [ns], from its switching times.

    AN2025-10 §7: the low side must be fully off before the high side turns on,
    so the window has to cover the worst turn-OFF delay less the best turn-ON
    delay.  The card tabulates ``t_d_off`` and ``t_d_on`` at 25 and 175 °C, and
    the worst pairing of the two is what a design has to clear:

        ``t_dead,min = max(t_d_off) + max(t_f) − min(t_d_on)``

    It is the DEVICE's floor only: the gate driver's own propagation-delay
    mismatch and the layout add to it, and neither is on this card.  ``None``
    when the card does not publish the times.
    """
    try:
        t = ((card.doc.get("switching") or {}).get("times_ns")) or {}
        def _mx(name, fn):
            blk = t.get(name) or {}
            vals = [float(v) for k, v in blk.items()
                    if k.startswith("t_j_") and v is not None]
            return fn(vals) if vals else None
        off, fall, on = _mx("t_d_off", max), _mx("t_f", max), _mx("t_d_on", min)
        if off is None or on is None:
            return None
        return float(off) + float(fall or 0.0) - float(on)
    except Exception:                                       # noqa: BLE001
        return None


def _controller_settings(body: Dict[str, Any], *, rpm: float,
                         inverter: Dict[str, Any]) -> Dict[str, Any]:
    """The CONTROLLER this coupled run is driven by, fully resolved.

    ``body["controller"]`` carries what the Controller tab carries — ``device``,
    ``topology``, ``devices_parallel``, ``dead_time_us``, the gate drive, the
    coldplate — and every field that is absent falls back, in order, to the
    duty's own stored ``controller`` record (the Stage 1 solve the tab left
    behind) and then to a NAMED default.  Where each one came from is reported
    in ``sources`` and travels into the coupled record, because a controller
    whose device nobody can name is not a measurement of anything.

    The carrier, the DC link and the fundamental are NOT here: they are the
    inverter block's, resolved by :func:`_inverter_settings` exactly as the
    ideal-PWM path resolves them, so the two drives cannot disagree about which
    bus this machine runs on — and since 2026-09-24 both read them from the
    Controller too (``_drive_carrier`` / ``_drive_v_dc``).
    """
    from motor_ai_sim.inverter.devices import CardError, get_device
    from motor_ai_sim.inverter.losses import (DEFAULT_TIM_K_W, E_OSS_POLICIES,
                                              SET_SPLITS)

    raw = body.get("controller")
    req = dict(raw) if isinstance(raw, dict) else {}
    stored = _duty_controller_record()
    st_set = dict((stored.get("settings") or {})) if stored else {}
    st_top = dict((stored.get("topology") or {})) if stored else {}
    # THE CONTROLLER SETTINGS SAVED WITH THE CONFIGURATION (2026-09-24 — the
    # Controller is the one place the drive is defined): a request that sends
    # no ``controller`` block (a script, an old session) reads the tab's saved
    # form before the duty's last Stage-1 solve, which may be older than it.
    try:
        from motor_ai_sim.inverter import drive_source as _DS
        saved = _DS.controller_block_for()
    except Exception:                                       # noqa: BLE001
        saved = {}
    src: Dict[str, str] = {}

    def _pick(key, stored_val, default, where_stored, where_default):
        if req.get(key) is not None:
            src[key] = "the request"
            return req[key]
        if saved.get(key) is not None:
            src[key] = "the Controller settings saved with the configuration"
            return saved[key]
        if stored_val is not None:
            src[key] = where_stored
            return stored_val
        src[key] = where_default
        return default

    device = _pick("device", stored.get("device") if stored else None, None,
                   "the duty's stored controller solve", "")
    if not device:
        raise _refuse(
            "drive='inverter' is the CONTROLLER's bridge and it needs a device: "
            "send controller.device (a part in config/devices), or solve this "
            "duty once in the Controller tab so its record carries one.  For an "
            "IDEAL two-level bridge use drive='pwm' — both stay available and "
            "both stay readable.",
            ["controller.device"], code="controller_no_device")
    try:
        card = get_device(str(device))
    except CardError as exc:
        raise _refuse(str(exc), ["controller.device"], code="unknown_device")

    topology = str(_pick("topology", st_top.get("preset"), "one_3ph",
                         "the duty's stored controller solve",
                         "the standard three-phase bridge") or "one_3ph")
    n_par = int(_pick("devices_parallel", st_set.get("devices_parallel"), 1,
                      "the duty's stored controller solve", "one per switch")
                or 1)
    if n_par < 1:
        raise _refuse("controller.devices_parallel must be at least 1",
                      ["controller.devices_parallel"])
    dead_us = float(_pick("dead_time_us", st_set.get("dead_time_us"),
                          CONTROLLER_DEAD_TIME_US,
                          "the duty's stored controller solve",
                          "this module's stated default (the card publishes "
                          "switching times, not a recommended dead time)"))
    if dead_us < 0.0:
        raise _refuse("controller.dead_time_us cannot be negative",
                      ["controller.dead_time_us"])
    v_gs_on = float(_pick("v_gs_on_V", st_set.get("v_gs_on_V"), 18.0,
                          "the duty's stored controller solve",
                          "the card's recommended gate-on voltage"))
    v_gs_off = float(_pick("v_gs_off_V", st_set.get("v_gs_off_V"), 0.0,
                           "the duty's stored controller solve",
                           "a 0 V gate-off drive"))
    r_g = _pick("r_g_ext_ohm", st_set.get("r_g_ext_ohm"), None,
                "the duty's stored controller solve",
                "the datasheet's own R_G,ext")
    policy = str(_pick("e_oss_policy", None, "included_in_eon", "", "the "
                       "module default — the datasheet E_on already contains "
                       "the C_oss discharge") or "included_in_eon")
    if policy not in E_OSS_POLICIES:
        raise _refuse("controller.e_oss_policy must be "
                      + " or ".join(E_OSS_POLICIES),
                      ["controller.e_oss_policy"])
    split = str(_pick("set_split", None, "series_split", "",
                      "the module default") or "series_split")
    if split not in SET_SPLITS:
        raise _refuse("controller.set_split must be " + " or ".join(SET_SPLITS),
                      ["controller.set_split"])
    cooling = req.get("cooling")
    if cooling is None and isinstance(saved.get("cooling"), dict) and saved["cooling"]:
        cooling = dict(saved["cooling"])
        src["cooling"] = "the Controller settings saved with the configuration"
    elif cooling is None and stored:
        cooling = ((stored.get("thermal") or {}).get("coldplate"))
        if isinstance(cooling, dict):
            cooling = {k: v for k, v in cooling.items()
                       if k in ("coolant", "flow_lpm", "t_in_c", "n_channels",
                                "channel_w_mm", "channel_h_mm", "length_mm",
                                "fin_area_factor", "r_override_k_w")}
        src["cooling"] = "the duty's stored controller solve"
    elif cooling is not None:
        src["cooling"] = "the request"
    else:
        src["cooling"] = "the module's default micro-channel coldplate"
    r_tim = float(_pick("r_tim_k_w", st_set.get("r_tim_k_w"), DEFAULT_TIM_K_W,
                        "the duty's stored controller solve",
                        "the module's stated default thermal interface"))

    floor_ns = _dead_time_floor_ns(card)
    notes: List[str] = []
    if floor_ns and dead_us * 1e3 < floor_ns:
        notes.append(
            "the %.3g us dead time is BELOW the %.0f ns this card's own "
            "switching times demand (t_d_off + t_f - t_d_on, AN2025-10 "
            "eqs. 22-26) — before the gate driver's propagation mismatch is "
            "counted at all" % (dead_us, floor_ns))
    return {
        "device": card.part,
        "topology": topology,
        "devices_parallel": n_par,
        "dead_time_us": dead_us,
        "dead_time_floor_ns": (None if floor_ns is None
                               else round(float(floor_ns), 1)),
        "v_gs_on_V": v_gs_on, "v_gs_off_V": v_gs_off,
        "r_g_ext_ohm": (None if r_g is None else float(r_g)),
        "e_oss_policy": policy, "set_split": split,
        "cooling": cooling or {}, "r_tim_k_w": r_tim,
        "h_bridge_modulation": str(req.get("h_bridge_modulation")
                                   or st_top.get("h_bridge_modulation")
                                   or "unipolar"),
        "mapping": req.get("mapping"),
        "devices_parallel_by_bridge": req.get("devices_parallel_by_bridge"),
        # The junction temperature the FIRST pass reads the card at.  A start,
        # not an answer: pass 2 onward uses what the previous pass solved.
        "t_j_start_c": float(req.get("t_j_start_c")
                             or (stored.get("thermal") or {}).get("t_j_max_c")
                             or 120.0) if stored else float(
            req.get("t_j_start_c") or 120.0),
        "notes": notes,
        "sources": src,
    }


def _duty_controller_record() -> Dict[str, Any]:
    """The Stage 1 controller solve stored on the duty the context names.

    ``{}`` when nothing is loaded or the duty has never been through the
    Controller tab — which is not an error: the request may carry everything.
    """
    try:
        from motor_ai_sim.duty_results import active_context, get as _dr_get
        ctx = active_context()
        if not ctx:
            return {}
        die, cfg, duty = ctx
        node = (_dr_get(str(die), str(cfg)) or {}).get(str(duty)) or {}
        blk = node.get("controller")
        return dict(blk) if isinstance(blk, dict) else {}
    except Exception:                                       # noqa: BLE001
        log.debug("coupled: could not read the duty's controller record",
                  exc_info=True)
        return {}


class _ControllerLoop:
    """The controller half of a ``drive: "inverter"`` run, pass by pass.

    It owns exactly two things: the :class:`DeviceDrop` the next
    electromagnetic run is solved with, and the last controller solve those
    currents produced.  Everything else — the carrier, the bus, the
    fundamental, the regulator — belongs to the inverter block beside it.
    """

    def __init__(self, cfg: Dict[str, Any], *, inverter: Dict[str, Any],
                 star_delta: str, rpm: float, pole_pairs: int,
                 i_leg_seed_A: float = 0.0):
        from motor_ai_sim.inverter.coupling import fit_device_drop
        from motor_ai_sim.inverter.devices import get_device

        self.cfg = dict(cfg)
        self.inverter = inverter
        self.star_delta = str(star_delta or "star").lower()
        self.rpm = float(rpm)
        self.pole_pairs = int(pole_pairs)
        self.card = get_device(str(cfg["device"]))
        self.t_j_c = float(cfg.get("t_j_start_c") or 120.0)
        self.solve: Dict[str, Any] = {}
        self.passes: List[Dict[str, Any]] = []
        self.warnings: List[str] = list(cfg.get("notes") or [])
        # The first pass has no solved current yet, so the body-diode fit is
        # placed at the current the duty is AIMED at — the regulator's target
        # where there is one, and otherwise the panel's own terminal current,
        # which on either connection IS the leg current.  A seed of zero would
        # fit the diode over a one-amp span and read it four volts wrong on the
        # first pass; from pass 2 the solved current replaces it either way.
        i_target = float(inverter.get("target_I_phase_rms_A") or 0.0)
        i_leg = (i_target * (math.sqrt(3.0)
                             if self.star_delta == "delta" else 1.0)
                 or abs(float(i_leg_seed_A or 0.0)))
        self.drop = fit_device_drop(
            self.card, t_j_c=self.t_j_c,
            n_parallel=int(cfg["devices_parallel"]),
            i_leg_peak_A=max(i_leg * math.sqrt(2.0), 1.0),
            v_gs_on_V=float(cfg["v_gs_on_V"]),
            v_gs_off_V=float(cfg["v_gs_off_V"]),
            dead_time_s=float(cfg["dead_time_us"]) * 1e-6)

    # ── what the electromagnetic run needs ────────────────────────────────
    def run_kwargs(self) -> Dict[str, Any]:
        """The four physics scalars and the provenance beside them."""
        d = self.drop
        return dict(
            inv_r_ds_ohm=float(d.r_ds_ohm),
            inv_v_sd_v0_V=float(d.v_sd_v0_V),
            inv_v_sd_rd_ohm=float(d.v_sd_rd_ohm),
            inv_dead_time_us=float(d.dead_time_s) * 1e6,
            inv_device=str(d.device),
            inv_devices_parallel=int(d.devices_parallel),
            inv_t_j_c=float(d.t_j_c),
            inv_topology=str(self.cfg.get("topology") or "one_3ph"))

    def snap_excitation(self) -> str:
        """The snapshot key's ``excitation`` field — spelled EXACTLY as
        ``get_fem_transient`` spells it for this drive."""
        d = self.drop
        return ("%g/%g/%g/%g/%g/%g"
                % (float(self.inverter["v_dc_V"]),
                   float(self.inverter["f_carrier_hz"]),
                   float(d.r_ds_ohm), float(d.v_sd_v0_V),
                   float(d.v_sd_rd_ohm), float(d.dead_time_s) * 1e6))

    def reseed(self, i_phase_rms_A: float) -> None:
        """Re-place the body-diode fit at a NEW operating current, before an
        electromagnetic pass solved at that current (2026-09-25).

        The loop's own passes all sit at the duty's current, so the fit the
        last :meth:`step` made is placed where the next pass needs it.  The
        continuous (S1) verification pass is solved at ``I_cont`` instead —
        a different current, often far from the duty's — and a diode line
        fitted over ``0.1·i_peak … i_peak`` of the OLD current would be read
        outside its span.  So the drop is refitted at the new current and the
        junction temperature the controller last converged on; :meth:`step`
        on that pass then re-converges ``T_j`` at the new current itself.
        """
        from motor_ai_sim.inverter.coupling import fit_device_drop

        i_leg = abs(float(i_phase_rms_A or 0.0)) * (
            math.sqrt(3.0) if self.star_delta == "delta" else 1.0)
        if not (i_leg > 0.0):
            return
        self.drop = fit_device_drop(
            self.card, t_j_c=self.t_j_c,
            n_parallel=int(self.cfg["devices_parallel"]),
            i_leg_peak_A=max(i_leg * math.sqrt(2.0), 1.0),
            v_gs_on_V=float(self.cfg["v_gs_on_V"]),
            v_gs_off_V=float(self.cfg["v_gs_off_V"]),
            dead_time_s=float(self.cfg["dead_time_us"]) * 1e-6)

    def state(self) -> tuple:
        """What :meth:`restore` puts back — so a pass that REFUSED leaves the
        controller describing the last pass that solved, never a drop fitted
        for a machine nobody reports."""
        return (self.drop, self.t_j_c, self.solve, len(self.passes))

    def restore(self, st: tuple) -> None:
        self.drop, self.t_j_c, self.solve, n = st
        del self.passes[n:]

    # ── and what comes back ───────────────────────────────────────────────
    def step(self, em: Dict[str, Any], *, it: int,
             phase: str = "loop") -> Optional[float]:
        """Solve the controller on THIS pass's answer; return ``ΔT_j`` [K].

        ``None`` when the pass carried nothing to solve on — the loop then
        treats the junction temperature as un-moved rather than inventing a
        residual.

        ``phase`` names which pass this is — ``"loop"`` (the fixed-point
        iteration), ``"limit"`` (the one pass AT the limit) or
        ``"s1_verify"`` (a continuous-rating verification pass) — so the
        record's ``passes`` table and its ``state`` say which machine the
        devices were solved on (2026-09-25).
        """
        from motor_ai_sim.inverter.coupling import fit_device_drop
        from motor_ai_sim.inverter.losses import ControllerRefusal, solve_controller

        req = self._solve_request(em)
        if req is None:
            return None
        try:
            out = solve_controller(req)
        except ControllerRefusal as exc:
            # A controller that cannot be solved does not kill a solved
            # electromagnetic pass: the machine's own answer stands, the
            # devices are reported as unsolved, and the run says why.
            self.warnings.append(
                "the controller could not be solved on pass %d: %s" % (it, exc))
            log.warning("coupled: controller solve refused on pass %d: %s",
                        it, exc)
            return None
        self.solve = out
        t_new = float((out.get("thermal") or {}).get("t_j_max_c")
                      or self.t_j_c)
        d_tj = t_new - self.t_j_c
        self.t_j_c = t_new
        self.drop = fit_device_drop(
            self.card, t_j_c=t_new,
            n_parallel=int(self.cfg["devices_parallel"]),
            i_leg_peak_A=max(float((out.get("point") or {}).get(
                "i_leg_rms_3ph_A") or 0.0) * math.sqrt(2.0), 1.0),
            v_gs_on_V=float(self.cfg["v_gs_on_V"]),
            v_gs_off_V=float(self.cfg["v_gs_off_V"]),
            dead_time_s=float(self.cfg["dead_time_us"]) * 1e-6)
        self.passes.append({
            "iter": int(it),
            "phase": str(phase),
            # The current the devices were solved at — the pass's own.
            "i_phase_rms_A": (round(float(req["i_phase_rms_A"]), 4)
                              if req.get("i_phase_rms_A") is not None
                              else None),
            "t_j_c": round(t_new, 2),
            "d_t_j_K": round(d_tj, 3),
            "p_inverter_W": (out.get("losses") or {}).get("total_W"),
            "eta_inverter": (out.get("efficiency") or {}).get("inverter"),
            "eta_wall_to_shaft": (out.get("efficiency") or {}).get(
                "wall_to_shaft"),
            "r_ds_on_mohm_device": round(self.drop.r_ds_on_mohm_device, 3),
            "limits_verdict": out.get("limits_verdict"),
        })
        return d_tj

    def _solve_request(self, em: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The controller solve's inputs, from the electromagnetic pass.

        EVERY number is the run's own: the current the machine drew, the power
        it drew it at, the modulation index the modulator reported and the ONE
        shaft efficiency of this pass.  Nothing is read from
        ``motor_config.yaml`` and nothing is re-derived from a nameplate.
        """
        s = em.get("summary") or {}
        i_ph = em.get("I_phase_rms_solved_A") or s.get("I1_phase_rms_A")
        eta = s.get("efficiency_shaft")
        if eta is None:
            # A machine that names no bearings has no shaft efficiency (its
            # mechanical loss is UNKNOWN, not zero) — and the controller was
            # then silently never solved (2026-09-25, Ø40 L12: T_j frozen at
            # the 120 °C start, no device losses in the record).  The
            # electromagnetic efficiency is the machine's one known balance;
            # used, and said so.
            eta = s.get("efficiency")
            if eta is not None and not getattr(self, "_eta_em_noted", False):
                self._eta_em_noted = True
                self.warnings.append(
                    "no bearings on this machine, so the AC power the devices "
                    "are solved at uses the electromagnetic efficiency (the "
                    "mechanical loss is unknown, not zero)")
        p_loss = s.get("P_loss_total_incl_mech_W") or s.get("P_loss_total_W")
        if not i_ph or not p_loss:
            return None
        try:
            i_ph = float(i_ph)
            p_loss = float(p_loss)
        except (TypeError, ValueError):
            return None
        eta_f = None
        p_ac = None
        if eta is not None and 0.0 < float(eta) < 1.0:
            eta_f = float(eta)
            # THE SHAFT EFFICIENCY POINTS THE OTHER WAY ON A GENERATOR, and the
            # quantity this module needs is always the AC side of the bridge.
            # Motor:      eta = P_shaft / P_ac      -> P_shaft = P_loss*eta/(1-eta),
            #                                          P_ac    = P_shaft + P_loss.
            # Generator:  eta = P_ac / P_shaft      -> P_ac    = P_loss*eta/(1-eta).
            # One line apart, and getting it wrong would hand the inverter the
            # MECHANICAL power of a generator and quietly over-read its
            # efficiency.
            _other = p_loss * eta_f / (1.0 - eta_f)
            _gen = str((em.get("summary") or {}).get("op_mode")
                       or "motor").strip().lower().startswith("gen")
            p_ac = _other if _gen else (_other + p_loss)
        if p_ac is None or not (p_ac > 0.0):
            return None
        pwm = em.get("pwm") if isinstance(em.get("pwm"), dict) else {}
        if not pwm:
            exc = em.get("excitation")
            pwm = (exc.get("pwm") or {}) if isinstance(exc, dict) else {}
        m = pwm.get("modulation_index")
        f_el = float(self.rpm) * self.pole_pairs / 60.0
        try:
            from motor_ai_sim.config import get_config
            geo = dict((get_config().get("geometry") or {}))
            wnd = dict((get_config().get("winding") or {}))
        except Exception:                                   # noqa: BLE001
            geo, wnd = {}, {}
        cfg = self.cfg
        req: Dict[str, Any] = {
            "num_slots": geo.get("num_slots"),
            "num_poles": geo.get("num_poles"),
            "single_layer": int(wnd.get("layers") or 1) == 1,
            "star_delta": self.star_delta,
            "device": cfg["device"],
            "devices_parallel": int(cfg["devices_parallel"]),
            "topology": cfg["topology"],
            "set_split": cfg["set_split"],
            "h_bridge_modulation": cfg["h_bridge_modulation"],
            "mapping": cfg.get("mapping"),
            "devices_parallel_by_bridge": cfg.get("devices_parallel_by_bridge"),
            "v_dc_V": float(self.inverter["v_dc_V"]),
            "f_carrier_hz": float(self.inverter["f_carrier_hz"]),
            "dead_time_us": float(cfg["dead_time_us"]),
            "v_gs_on_V": float(cfg["v_gs_on_V"]),
            "v_gs_off_V": float(cfg["v_gs_off_V"]),
            "r_g_ext_ohm": cfg.get("r_g_ext_ohm"),
            "e_oss_policy": cfg["e_oss_policy"],
            "cooling": cfg.get("cooling") or {},
            "r_tim_k_w": float(cfg["r_tim_k_w"]),
            "i_phase_rms_A": i_ph,
            "p_ac_W": p_ac,
            "f_elec_hz": f_el,
            "rpm": self.rpm,
            "efficiency_shaft": eta_f,
            "t_j_start_c": self.t_j_c,
        }
        if not m:
            # NO INVENTED POWER FACTOR.  The bridge's duty cycle cannot be got
            # from the current alone, and a 0.95 stuffed in here would be a
            # number nobody measured deciding the switching loss.  Every run
            # of this drive reports its modulation index; a run that does not
            # is one this module declines to answer for, and says so.
            self.warnings.append(
                "the electromagnetic run reported no modulation index, so the "
                "devices were not solved on this pass — the bridge's duty "
                "cycle cannot be derived from the current alone")
            return None
        req["modulation_index"] = float(m)
        return req

    # ── the record ────────────────────────────────────────────────────────
    def record(self, em: Dict[str, Any]) -> Dict[str, Any]:
        """The ``controller`` block a coupled record carries.

        The Stage 1 solve's own shape, trimmed of the waveform arrays (the
        record is not a place for a thousand samples), plus what only a COUPLED
        run knows: which pass it settled on, how far the junction temperature
        still was, and the excitation the machine was really fed.
        """
        out = dict(self.solve or {})
        # THE WAVEFORM, AS A SUMMARY.  The arrays themselves are a thousand
        # samples per coil and a record is not the place for them (the
        # Controller tab draws them live from its own solve); what a reader of
        # this record needs is the shape's own numbers — the grid it was drawn
        # on, the dead-time error it carries, and one rms per coil, which is
        # the quantity that says whether the six coils see the same volts.
        _wf = (self.solve or {}).get("waveforms")
        out.pop("waveforms", None)
        if isinstance(_wf, dict):
            out["waveform_summary"] = {
                k: _wf.get(k) for k in
                ("f_elec_hz", "f_carrier_eff_hz", "dead_time_us", "samples",
                 "dead_time_error_V") if _wf.get(k) is not None}
            out["waveform_summary"]["v_coil_rms_V"] = {
                str(c): (v or {}).get("v_rms_V")
                for c, v in sorted((_wf.get("coils") or {}).items())}
            out["waveform_summary"]["i_coil_rms_A"] = {
                str(c): (v or {}).get("i_rms_A")
                for c, v in sorted((_wf.get("coils") or {}).items())}
            out["waveform_summary"]["note"] = (
                "the Controller module's own picture of what the bridge "
                "applies, at THIS pass's device temperature; the machine was "
                "marched on the same clamp through the excitation block below")
        nonideal = {}
        pwm = em.get("pwm") if isinstance(em.get("pwm"), dict) else {}
        if not pwm:
            exc = em.get("excitation")
            pwm = (exc.get("pwm") or {}) if isinstance(exc, dict) else {}
        if isinstance(pwm.get("nonideal"), dict):
            nonideal = dict(pwm["nonideal"])
        d = self.drop
        # WHICH MACHINE THESE DEVICE NUMBERS ARE (2026-09-25).  The record's
        # machine may be the loop's last pass, the pass AT the limit, or the
        # continuous-rating (S1) pass — and the controller block must be the
        # devices of THAT pass, at its current, not of an earlier one.  Stated
        # rather than implied, and checked against the run it rides beside.
        last = self.passes[-1] if self.passes else {}
        i_em = em.get("I_phase_rms_solved_A") or (em.get("summary") or {}).get(
            "I1_phase_rms_A")
        state = {"phase": last.get("phase"), "iter": last.get("iter"),
                 "I_phase_rms_A": last.get("i_phase_rms_A")}
        warnings_extra: List[str] = []
        try:
            if (i_em is not None and last.get("i_phase_rms_A") is not None
                    and abs(float(i_em) - float(last["i_phase_rms_A"]))
                    > 1e-3 * max(abs(float(i_em)), 1.0)):
                state["matches_record"] = False
                warnings_extra.append(
                    "the devices were last solved at %.3f A but the machine "
                    "this record reports drew %.3f A — the controller block "
                    "does not describe the reported state"
                    % (float(last["i_phase_rms_A"]), float(i_em)))
            elif i_em is not None and last:
                state["matches_record"] = True
        except (TypeError, ValueError):
            pass
        out.update({
            "source": "controller",
            "stage": 2,
            "coupled": True,
            "state": state,
            "settings_resolved": {
                k: self.cfg.get(k) for k in
                ("device", "topology", "devices_parallel", "dead_time_us",
                 "dead_time_floor_ns", "v_gs_on_V", "v_gs_off_V",
                 "r_g_ext_ohm", "e_oss_policy", "set_split", "r_tim_k_w")},
            "sources": dict(self.cfg.get("sources") or {}),
            "t_j_c": round(float(self.t_j_c), 2),
            "t_j_tol_K": CONTROLLER_TJ_TOL_K,
            "t_j_residual_K": (round(float(self.passes[-1]["d_t_j_K"]), 3)
                               if self.passes else None),
            "passes": list(self.passes),
            "excitation": nonideal,
            "device_drop": d.as_dict(),
            "warnings": list(self.warnings) + warnings_extra
                        + list((self.solve or {}).get("warnings") or []),
            "note": (
                "the machine was solved on the CONTROLLER's own waveform — "
                "%s x%d at %.3g us dead time on a %.1f V link — and these "
                "device losses are the ones that waveform's currents cause; "
                "the junction temperature they set moved R_DS(on) and V_SD for "
                "the next pass, so the two halves are one fixed point"
                % (d.device, d.devices_parallel, d.dead_time_s * 1e6,
                   float(self.inverter["v_dc_V"]))),
            # WHAT IS EXACT AND WHAT IS NOT, on the device side.  The loss model
            # is handed the SOLVED rms — the ripple is in it — so the CONDUCTION
            # term is exact (it depends on <i^2> and on nothing else).  The
            # third-quadrant and switching terms are integrated on a SINUSOID of
            # that rms rather than on the rippled waveform itself: the shape
            # decides which instantaneous current each edge switches, and the
            # ripple's own contribution to that averages toward zero over a
            # period but does not vanish.  Named rather than left implied.
            "model_note": (
                "conduction is billed on the run's own solved rms (the "
                "switching ripple is in it and it is the only thing I^2R "
                "depends on); the dead-time and switching integrals run on a "
                "SINUSOID of that rms, so the instantaneous current at each "
                "commutation is the fundamental's and not the rippled one"),
        })
        return out


def _pwm_snap_excitation(inv: Dict[str, Any]) -> str:
    """The snapshot key's ``excitation`` field for this inverter — spelled
    EXACTLY as ``get_fem_transient`` spells it when it stores the snapshot
    (``"%g/%g" % (v_bus, f_switch)``).  One format string in two places is how
    a loss map goes missing, so this function is the single caller's copy and
    the test pins it against the route's own key."""
    return "%g/%g" % (float(inv["v_dc_V"]), float(inv["f_carrier_hz"]))


class _NoLossMap(Exception):
    """The PWM run left no per-element loss map under its own key.

    A control-flow signal, not an error: the loop answers it by SOLVING again
    with ``fresh=True`` and only refuses if that still leaves nothing.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _em_call(fn, kw: Dict[str, Any]) -> Dict[str, Any]:
    """The transient call, with a CANCEL turned back into a cancel.

    THE STOP BUTTON THAT DID NOT STOP (owner, production 2026-09-25): the frame
    march answers a cancel with ``HTTPException(499)`` — and this loop's
    passes catch ``HTTPException`` to keep a solved state when a LATER pass is
    refused (a magnet off its card, an unsettled DC).  A 499 is not a refusal:
    read as one, the loop kept the previous pass and went on to the pass at
    the limit, the S1 verification, the 20 °C constants, the rotor stress and
    the modal sweep — minutes of solving after the user pressed Stop.  So a
    499 leaves here as :class:`_LoopCancelled`, a ``BaseException`` no
    ``except HTTPException`` / ``except Exception`` on the way can swallow.
    """
    from motor_ai_sim.modules.solvers import _call_filtered
    try:
        return _call_filtered(fn, kw) or {}
    except HTTPException as exc:
        if int(getattr(exc, "status_code", 0) or 0) == 499:
            raise _LoopCancelled() from exc
        raise


def _em_run(body: Dict[str, Any], *, coil_temp_c: float,
            magnet_temp_c: Optional[float],
            inverter: Optional[Dict[str, Any]] = None,
            controller: Optional["_ControllerLoop"] = None,
            fresh: bool = False, ledger: bool = False) -> Dict[str, Any]:
    """ONE Electromagnetic run — the same call the Run button makes.

    ``ledger`` — OFF for every pass except the loop's very FIRST one, and even
    there only when the caller opts in.  Every iteration from the second on
    solves at a temperature the network fed back, which the results ledger
    has never seen under that key — reading it there would be the exact trap
    the module note below `ledger=False, fresh=bool(fresh)` describes (the
    night's peak run died in three seconds on a stored summary with no field
    behind it).  The FIRST pass is different: it is solved at the body's OWN
    coil/magnet temperature, the identical point a plain Run at those same
    panel fields would have made moments before Coupled thermal was switched
    on — and owner, 2026-09-22, the REVERSE of the ledger-miss report: "why
    does it solve the electromagnetic pass again" applies just as much to the
    loop re-solving a point the user already ran.  The caller (the loop's own
    ``it == 1``) is the only one that may ask for it.

    Goes through ``routes.simulation.get_fem_transient`` and nothing else, so the
    result IS the tab's last run: journaled, snapshotted, cached and persisted by
    that function exactly as a hand-pressed Run would be.  A second code path
    that "does the same solve" is a second path that drifts, and the whole point
    of the loop is that the run left behind is one the user can reproduce.

    ``field_snapshot`` is forced ON — it is not an extra solve, it is the frame
    just solved being kept instead of dropped, and it is the ONLY thing that lets
    the thermal half find this run's per-element loss map.

    THE BEARING TEMPERATURE is not a parameter here: the loop below sets
    ``mech_losses.BEARING_TEMP_C`` around this call, and the summary builder
    reads it from there.  Deliberately not threaded through the argument list —
    the road from the loop to the arithmetic that needs it runs through
    ``get_fem_transient`` and ``_build_transient_summary``, thirty parameters
    that have nothing to do with bearings — and a ContextVar is exactly the right
    lifetime: it cannot leak into a parallel request the way a module global
    would, and it reaches the summary whatever this function is.
    """
    from motor_ai_sim.modules.solvers import _call_filtered
    from motor_ai_sim.routes.simulation import get_fem_transient

    kw = dict(body)
    kw.update(coil_temp_c=float(coil_temp_c),
              magnet_temp_c=(None if magnet_temp_c is None
                             else float(magnet_temp_c)),
              # The pre-flight reads an ABSENT eddy / rotor_eddy as ON (the
              # loop cannot run without them); the transient route's own
              # default for an absent key is OFF.  Spell the pre-flight's
              # reading out, or a body that omits the two solves a run the
              # thermal half then refuses as "no run of this machine"
              # (2026-09-08 13:45, an API caller without the web's payload).
              eddy=bool(body.get("eddy", True)),
              rotor_eddy=bool(body.get("rotor_eddy", True)),
              field_snapshot=True,
              # A coupled iteration must SOLVE.  `restore` would hand back the
              # previous run, and `ledger_probe` answers without solving at all —
              # either would make the loop iterate on a temperature nothing was
              # computed at.
              #
              # …AND THE RUN LEDGER IS THE THIRD DOOR, which this list missed
              # until 2026-09-15.  A ledger hit returns a STORED result for the
              # identical key in about three seconds and says so itself: "the
              # field views work off the in-memory snapshot store, which this
              # path deliberately does not touch" (simulation.py, the
              # `_ledger_lookup` branch).  So the loop got a summary with no
              # field behind it, the PWM half found no loss map under its own
              # key, and the night's peak run died in three seconds on its
              # second attempt — the ledger lives on disk, so an API restart
              # does not clear it.  Same trap the Ø200 gen pair hit on
              # 2026-09-13; the cure there was `fresh`, and the cure here is to
              # shut the door: a coupled iteration is never a lookup — EXCEPT
              # the one call that opts in through this function's own
              # ``ledger`` parameter (the loop's first pass only — see the
              # docstring).  A ledger hit there is not "a stored summary with
              # no field behind it": the FIELD SNAPSHOT lives in the in-memory
              # store keyed by the same identity, not on the stored result, so
              # it is still there whenever the hit is served in the same
              # process the Run that made it ran in — exactly the situation a
              # plain Run just before switching Coupled thermal on leaves.
              ledger=bool(ledger), fresh=bool(fresh),
              restore=False, ledger_probe=False)
    if inverter is None:
        # THE LOOP's spelling of the drive is not the RUN's: this route accepts
        # "sine" as a synonym for the sinusoidal current source, and the
        # transient route knows only "current" — forwarding the synonym would
        # 422 with "unknown excitation source 'sine'".
        kw["drive"] = "current"
        return _em_call(get_fem_transient, kw)
    # ── THE INVERTER ────────────────────────────────────────────────────────
    # The settle SCHEDULE is an environment switch of the solver (the B5 fix's
    # own `SB_PWM_COARSE_SETTLE`), so it is set around this one call and put
    # back afterwards — a coupled run must not leave the process configured
    # for the next request.
    kw.update(_pwm_run_kwargs(inverter))
    if controller is not None:
        # STAGE 2.  The SAME arguments the ideal bridge takes, plus the device:
        # four physics scalars (the leg's channel resistance at the junction
        # temperature the controller last converged on, the body-diode fit and
        # the dead time) and the provenance the record prints.  Nothing else
        # changes — the carrier, the link and the fundamental are the inverter
        # block's, so an "inverter" run and a "pwm" run of one duty differ in
        # exactly the thing they are supposed to differ in.
        kw["drive"] = "inverter"
        kw.update(controller.run_kwargs())
    _prev = os.environ.get("SB_PWM_COARSE_SETTLE")
    os.environ["SB_PWM_COARSE_SETTLE"] = _PWM_SCHEDULES[inverter["schedule"]]
    try:
        return _em_call(get_fem_transient, kw)
    finally:
        if _prev is None:
            os.environ.pop("SB_PWM_COARSE_SETTLE", None)
        else:
            os.environ["SB_PWM_COARSE_SETTLE"] = _prev


def _pwm_loss_map(body: Dict[str, Any], em: Dict[str, Any],
                  inv: Dict[str, Any], *, coil_temp_c: float,
                  magnet_temp_c: Optional[float],
                  controller: Optional["_ControllerLoop"] = None
                  ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The PWM run's own per-element loss map, in the payload shape the thermal
    solve reads — and its provenance.

    THE CARRIER'S WATTS ARE IN THE MAP, ELEMENT BY ELEMENT, and that is the
    whole point of doing it this way.  The map is the run's own
    ``loss_density_per_tri``: the copper term is the coupled σ·∂A/∂t solve's
    ∫σE² over the conductors of THIS excitation (so the proximity and skin loss
    of the ripple current is in it, not a factor applied to a DC number), and
    the iron term is computed per element from that element's own B(t) series
    over the solved cycle — the carrier's flux ripple included, because the
    cycle marched is the PWM cycle.  Nothing here is scaled from a total.
    ``P_cu_exact_W`` beside it is the same integral as a scalar, which is what
    the 1 % identity check in the tests compares the map against.

    WHY NOT ``thermal._em_loss_map``: that function looks a run up by a PHYSICS
    identity whose ``drive`` is hard-coded ``current`` and whose operating point
    names a REQUESTED phase current — and on a voltage-fed run the current is
    the answer, not the request.  So the loop hands the map over directly
    (``solve_thermal_field(_em_map=…)``, the seam that already exists), and the
    lookup here is an EXACT-key snapshot probe of the run just made:
    ``latest_run_field=False`` so a near miss can never be served, and the
    run's own ``computed_at`` checked against the payload's, so the map can
    only be this iteration's.
    """
    from motor_ai_sim.routes import simulation as _sim

    g = _f(body, "gamma_deg", 0.0)
    if str(body.get("mode") or "motor").strip().lower() == "generator":
        # The transient folds the generator's 180° el shift in BEFORE it builds
        # its snapshot key (see thermal._solved_gamma_deg); a probe on the panel
        # angle would miss every generator run.
        g += 180.0
    probe = dict(
        gamma_deg=g, I_phase_rms=_f(body, "I_phase_rms", 0.0),
        n_steps_per_period=int(inv["n_steps_per_period"]),
        n_periods=_f(body, "n_periods", 1.0),
        eddy=True, rotor_eddy=True,
        coil_temp_c=float(coil_temp_c), magnet_temp_c=magnet_temp_c,
        mesh_size_mm=_f(body, "mesh_size_mm", 3.0),
        min_size_mm=_f(body, "min_size_mm", 0.3),
        outer_air_factor=_f(body, "outer_air_factor", 1.3),
        n_sectors=int(body.get("n_sectors") or 1),
        stator_fillet_mm=_f(body, "stator_fillet_mm", 0.0),
        gap_layers=_f(body, "gap_layers", 2.0),
        component_mesh=str(body.get("component_mesh") or ""),
        pole_copy=bool(body.get("pole_copy", False)),
        iron_template=bool(body.get("iron_template", True)),
        geo_mesh=bool(body.get("geo_mesh", True)),
        structured_gap=bool(body.get("structured_gap", True)),
        airgap_macro=bool(body.get("airgap_macro", False)),
        demag=bool(body.get("demag", False)),
        rotor_angle_deg=_f(body, "rotor_angle_deg", 0.0),
        geo=body.get("geo"),
        snap_drive=("inverter" if controller is not None else "pwm_voltage"),
        snap_excitation=(controller.snap_excitation() if controller is not None
                         else _pwm_snap_excitation(inv)),
        use_transient_snapshot=True, snapshot_only=True,
        latest_run_field=False)
    out = _sim.get_fem_field2d(**probe) or {}
    n_ld = len(out.get("loss_density_per_tri") or [])
    stamp = em.get("computed_at")
    if not (out.get("ok") and out.get("from_transient")
            and n_ld == int(out.get("n_triangles") or -1)):
        # NOT a refusal yet — the caller answers this by solving again.  The
        # commonest cause is a run that never solved (a ledger hit, a memo) and
        # therefore left no field; the cure is a fresh solve, not a 422.
        raise _NoLossMap(out.get("reason")
                         or "no snapshot matched this run's key")
    if stamp and out.get("transient_computed_at") \
            and str(out["transient_computed_at"]) != str(stamp):
        raise _refuse(
            "the loss map found for this iteration belongs to another run "
            "(%s, not %s) — refusing rather than solving a temperature field "
            "on somebody else's watts."
            % (out.get("transient_computed_at"), stamp),
            ["drive"], code="pwm_loss_map_mismatch")
    return out, {
        "kind": "pwm_run",
        "run_id": stamp, "computed_at": stamp,
        # …and the two fields the THERMAL record reads off its own
        # `loss_source`, so the temperature column can say which excitation it
        # belongs to without having to find the coupled record beside it.
        "drive": ("inverter" if controller is not None else "pwm"),
        "inverter": _inverter_record(em, inv),
        **({"controller": {
            "device": controller.drop.device,
            "devices_parallel": controller.drop.devices_parallel,
            "t_j_c": round(float(controller.t_j_c), 2),
            "dead_time_us": round(controller.drop.dead_time_s * 1e6, 3),
            "topology": controller.cfg.get("topology")}}
           if controller is not None else {}),
        "note": ("per-element loss density of THIS coupled iteration's PWM run "
                 "(%.0f Hz carrier on a %.1f V link, %d steps/period = %.1f per "
                 "carrier): the copper term is the coupled eddy solve's own "
                 "integral over the conductors of the ripple current, and the "
                 "iron term is computed per element from that element's B(t) "
                 "over the solved PWM cycle — nothing is scaled from a total"
                 % (float(inv["f_carrier_hz"]), float(inv["v_dc_V"]),
                    int(inv["n_steps_per_period"]),
                    float(inv["samples_per_carrier"]))),
    }


def _record_inverter(inv: Dict[str, Any],
                     v1_ran: Optional[float]) -> Dict[str, Any]:
    """The inverter as the RECORD must describe it: ``v_phase_peak_V`` is the
    fundamental the last electromagnetic run was actually solved at, and the
    regulator's next aim — computed on every pass, the final one included —
    rides beside it as ``v_phase_peak_next_V`` instead of silently replacing it.

    Without this the live regulator state leaked into the record: a loop that
    stopped off point reported the voltage it was ABOUT to try next next to the
    current it drew at the previous one, which is two passes in one row.
    """
    if v1_ran is None:
        return inv
    out = dict(inv)
    out["v_phase_peak_V"] = float(v1_ran)
    aim = float(inv.get("v_phase_peak_V") or v1_ran)
    if abs(aim - float(v1_ran)) > 1e-9:
        out["v_phase_peak_next_V"] = round(aim, 4)
    return out


def _inverter_record(em: Dict[str, Any], inv: Dict[str, Any],
                     *, ripple_quotable: bool = True) -> Dict[str, Any]:
    """The ``inverter`` block a record carries: what was ASKED of the bridge and
    what the run ANSWERED — the carrier, the link, the modulation index, whether
    the delta machine was solved on its star equivalent, and the three numbers
    that say whether the ripple may be quoted at all (the settled DC residual,
    the torque ripple and the current THD)."""
    s = em.get("summary") or {}
    pwm = em.get("pwm") if isinstance(em.get("pwm"), dict) else {}
    if not pwm:
        exc = em.get("excitation")
        pwm = (exc.get("pwm") or {}) if isinstance(exc, dict) else {}
    dq = s.get("delta_equivalent_star")
    out: Dict[str, Any] = {
        "f_carrier_hz": float(inv["f_carrier_hz"]),
        # Which tier named the carrier (2026-09-24: the Controller is the rule;
        # ``legacy`` / ``default`` mean "save a carrier in the Controller").
        "carrier_origin": inv.get("carrier_origin"),
        "f_carrier_eff_hz": pwm.get("f_switch_eff_Hz"),
        "carriers_per_period": pwm.get("carriers_per_period",
                                       inv.get("carriers_per_period")),
        "steps_per_period": int(inv["n_steps_per_period"]),
        "samples_per_carrier": pwm.get("steps_per_switching_period",
                                       inv.get("samples_per_carrier")),
        "schedule": inv["schedule"],
        "v_dc_V": float(inv["v_dc_V"]),
        "v_phase_peak_V": float(inv["v_phase_peak_V"]),
        # WHAT THE REGULATOR WOULD HAVE AIMED AT NEXT — written on the final pass
        # too, so a run that stopped off point says where it was heading instead
        # of leaving the reader to re-derive it (absent when the last pass landed
        # inside the band, which is what "nothing left to correct" looks like).
        "v_phase_peak_next_V": inv.get("v_phase_peak_next_V"),
        # The largest fundamental this DC link can synthesise inside the linear
        # limit (delta: the branch = line value, √3 above the per-phase one).
        # Printed so "off point" can be read as "out of inverter" where it is.
        "v_phase_peak_max_V": inv.get("v_phase_peak_max_V"),
        # The two halves of that ceiling: what the bridge could chop at m = 1.15,
        # and the factor the modulator's sampled-reference gain costs on top of
        # it (`coupled._modulator_gain`).  Printed so a point that ran out of
        # inverter can be told from one that ran out of iterations.
        "v_phase_peak_max_uncompensated_V": inv.get(
            "v_phase_peak_max_uncompensated_V"),
        "modulator_gain_factor": inv.get("modulator_gain_factor"),
        "at_modulation_ceiling": inv.get(
            "v_phase_peak_at_modulation_ceiling") or None,
        "v_delta_deg": float(inv["v_delta_deg"]),
        "m": pwm.get("modulation_index"),
        # The star-equivalent substitution, NAMED (never hidden — a model bus
        # of 1 299.7 V that nobody can buy must not look like a measurement).
        "equivalent_star": bool(pwm.get("equivalent_star")
                                or isinstance(dq, dict)),
        "v_bus_model_V": pwm.get("v_bus_model_V"),
        "star_delta": s.get("star_delta"),
        "dc_residual_A": s.get("pwm_dc_residual_A"),
        "dc_tol_A": s.get("pwm_dc_tol_A"),
        "dc_unconverged": s.get("pwm_dc_unconverged"),
        "dc_band_A": round(_pwm_dc_band_a(inv, s), 3),
        # MAY THE RIPPLE BE QUOTED?  False when any pass came back with DC left
        # in the phase current: the losses and the temperatures are unaffected
        # (a few amps of DC on a few hundred is nothing on I²), but the torque
        # ripple and the current ripple then describe the offset and not the
        # machine — 55 % where the settled orbit reads 25 % (B5 / study §1.9).
        # ALWAYS written, True included: a report must be able to tell "settled"
        # from "this record predates the flag".
        "ripple_quotable": bool(ripple_quotable),
        "ripple_pct": s.get("T_ripple_pct"),
        "thd_i_pct": s.get("THD_I_pct"),
        "thd_ll_pct": s.get("THD_LL_pct"),
        "I_phase_rms_solved_A": (em.get("I_phase_rms_solved_A")
                                 or s.get("I1_phase_rms_A")),
        "modulator": pwm.get("modulator"),
        "sources": dict(inv.get("sources") or {}),
    }
    # THE POINT: what this run was aimed at and how close the last pass landed.
    # Absent when the caller pinned the fundamental instead — "no target" and
    # "on target" must not read the same.
    tgt = inv.get("target_I_phase_rms_A")
    if tgt:
        out["target_I_phase_rms_A"] = float(tgt)
        out["i_tol_pct"] = _point_tol_pct(inv)
        out["v_phase_peak_seed_V"] = float(inv.get("v_phase_peak_seed_V")
                                           or inv["v_phase_peak_V"])
        # ONE definition of the miss, shared with the convergence test — a record
        # that could call a pass off point while the loop called it converged is
        # exactly the bug this rule exists to stop.
        _err = _point_error_pct(inv, out.get("I_phase_rms_solved_A"))
        if _err is not None:
            out["point_error_pct"] = round(_err, 3)
            out["on_point"] = bool(abs(out["point_error_pct"])
                                   <= out["i_tol_pct"])
    return {k: v for k, v in out.items() if v is not None}


def _thermal_solve(body: Dict[str, Any], cooling: Dict[str, Any], *,
                   coil_temp_c: float, magnet_temp_c: Optional[float],
                   rpm: float,
                   bearing_temp_c: Optional[float] = None,
                   n_steps_per_period: Optional[int] = None,
                   em_map: Optional[Dict[str, Any]] = None,
                   em_loss_source: Optional[Dict[str, Any]] = None,
                   params_out: Optional[Dict[str, Any]] = None
                   ) -> Dict[str, Any]:
    """ONE thermal solve at the same temperatures the EM run was made at.

    ``routes.thermal.solve_thermal_field`` — the function all three thermal
    callers share — with this loop's cooling and the EM identity that selects the
    run just solved.  It is also REMEMBERED as the Thermal tab's last ``field``
    result, so the tab comes back to the converged map instead of the one the
    user solved by hand an hour ago.

    The thermal router's OWN progress tracker is started and finished around it:
    the Thermal tab's strip has a real solve to show, and this loop's tracker
    (the iteration counter) is a separate instrument reporting a separate thing.

    ``bearing_temp_c`` is this pass's bearing temperature — the same number the
    electromagnetic half was billed at — so the friction heat this map carries at
    the shaft ends is the friction of a bearing at the temperature the shaft
    actually reaches.  It rides the argument list here (unlike the EM half, whose
    path goes through a route signature that has no room for it) and is dropped
    from the REMEMBERED params for the same reason ``geo`` is: it is not one of
    the Thermal tab's input fields, and restoring it into them would show the tab
    a control it does not have.
    """
    from motor_ai_sim.routes import thermal as th

    kw = dict(cooling)
    kw.update(
        rpm=float(rpm),                       # the gap Taylor number
        gamma_deg=_f(body, "gamma_deg", 0.0),
        I_phase_rms=_f(body, "I_phase_rms", 0.0),
        n_steps_per_period=int(n_steps_per_period
                               or body.get("n_steps_per_period") or 12),
        n_periods=_f(body, "n_periods", 1.0),
        mesh_size_mm=_f(body, "mesh_size_mm", 3.0),
        min_size_mm=_f(body, "min_size_mm", 0.3),
        outer_air_factor=_f(body, "outer_air_factor", 1.3),
        n_sectors=int(body.get("n_sectors") or 1),
        component_mesh=str(body.get("component_mesh") or ""),
        geo=body.get("geo"),
        coil_temp_c=float(coil_temp_c),
        magnet_temp_c=(None if magnet_temp_c is None else float(magnet_temp_c)),
        # The EM half solved the body's mode (generator = panel γ + 180°); the
        # thermal half must look the run up under the same angle.
        op_mode=body.get("mode"))
    if bearing_temp_c is not None:
        kw["bearing_temp_c"] = float(bearing_temp_c)
    if em_map is not None:
        # THE MAP IS HANDED OVER, not looked up (the PWM drive).  The seam is
        # `solve_thermal_field`'s own `_em_map` / `_em_loss_source`, which exists
        # precisely so a caller that already HAS the electromagnetic answer does
        # not go back through a lookup keyed on a requested phase current.  The
        # conduction solve below it is byte-for-byte the one a sine map gets.
        kw["_em_map"] = em_map
        kw["_em_loss_source"] = dict(em_loss_source or {})

    th._progress.start(total=2, kind="field",
                       phase="loss map — the coupled run's own solve",
                       composition="conduction + post-processing")
    try:
        out = th.solve_thermal_field(progress=th._progress.callback(), **kw)
    finally:
        th._progress.finish()
    try:
        # `geo` is dropped from the remembered params for the same reason the
        # thermal route never stores it: it is the whole machine as a JSON
        # string, the fingerprint beside it already says WHICH machine, and the
        # store is a pickle that sits next to the user's config.
        _p = th._field_params(**{k: v for k, v in kw.items()
                                 if k not in ("geo", "bearing_temp_c",
                                              "_em_map", "_em_loss_source")})
        # …and handed BACK, so a caller that goes on to change the map can
        # re-remember it under the very same params (the limited mode's
        # transient snapshot — see `_run`).  Re-deriving them at the second
        # call site would be two spellings of one identity, which is how a
        # store ends up with two entries for one solve.
        if params_out is not None:
            params_out.clear()
            params_out.update(_p)
        th._remember_last("field", out, _p, out.get("geometry_fingerprint"))
    except Exception:  # noqa: BLE001 — a memory is not worth losing an answer over
        log.exception("could not remember the coupled run's thermal map")
    return out


def _shaft_torque_nm(summary: Dict[str, Any]) -> tuple:
    """The run's shaft torque for the mechanical step, and how it was made.

    ``(torque_or_None, note_or_None)``.  The 2-D mean ``T_em_avg_Nm`` times
    the run's 3-D end-effect factor ``end3d.k_flux`` when the summary carries
    one — the same product the Electromagnetic tab's tiles show — else the 2-D
    mean itself.  A run with no torque at all gives ``None`` (the hook then
    falls back the way the route does).
    """
    t = summary.get("T_em_avg_Nm")
    try:
        t = None if t is None else float(t)
    except (TypeError, ValueError):
        t = None
    if t is None or not math.isfinite(t):
        return None, None
    e3 = summary.get("end3d")
    k = (e3 or {}).get("k_flux") if isinstance(e3, dict) else None
    try:
        k = None if k is None else float(k)
    except (TypeError, ValueError):
        k = None
    if k is not None and math.isfinite(k) and k > 0.0 and abs(k - 1.0) > 1e-9:
        return t * k, ("2-D mean %.4g N·m × 3-D end-effect %.4g" % (t, k))
    return t, None


def _detail_text(exc: HTTPException) -> str:
    """A refusal's message as one line — the route's own words, whatever shape
    it packed them in (a string, or the ``{"error": …}`` dict the validators
    raise)."""
    d = exc.detail
    if isinstance(d, dict):
        return str(d.get("error") or d.get("detail") or json.dumps(d, ensure_ascii=False))
    return str(d)


def _component(field: Dict[str, Any], name: str) -> Dict[str, Any]:
    c = ((field or {}).get("components") or {}).get(name)
    return c if isinstance(c, dict) else {}


def _bearing_temp(field: Dict[str, Any]) -> Optional[tuple]:
    """``(temp_c, where)`` — the bearing seat, off a thermal map this loop made.

    Thin on purpose: the RULE (shaft-ends mean, else shaft average, else nothing)
    lives in ``mech_losses.bearing_temp_from_map`` because the stored-run summary
    applies exactly the same one to the REMEMBERED map.  Two readings of "the
    bearing's temperature" would eventually be two different temperatures.

    ``None`` when the map carries no shaft at all: the next pass then keeps
    whatever it had — the machine's own assignment on pass 1 — instead of the
    loop inventing a seat temperature out of a cross-section that has no seat.
    """
    from motor_ai_sim import mech_losses as ml
    return ml.bearing_temp_from_map(field)


def _bulk_temp(comp: Dict[str, Any]) -> Optional[float]:
    """The temperature a BULK material property belongs at: the average.

    ``max`` is the fallback for a part that meshed to a single element band — no
    interior node, so no meaningful average — and not a preference: feeding the
    hotspot back as the body's temperature would over-state ρ_Cu and under-state
    Br on every machine.
    """
    v = comp.get("avg", comp.get("max"))
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# The mechanical step — the third tab at the same temperatures
# ---------------------------------------------------------------------------

def _mechanical_point(panel: Dict[str, Any], rpm: Optional[float]
                      ) -> tuple:
    """The speed the coupled rotor-stress solve is run at, and a note.

    Returns ``(rpm_or_None, note_or_None)``.  ``rpm`` is the operating speed the
    loop just solved the machine at, and THAT is the speed of the mechanical
    step — in both of the tab's modes (single: the one case is this speed;
    three cases: this is the rated speed and the route's overspeed factor
    multiplies it).  The tab's ``rpm`` / ``rpm1`` boxes are numbers typed for
    whatever machine was loaded when they were typed (2026-09-09 morning: the
    Ø200's 23 000 on a 13 000 rpm motor; user: "обороты должны быть
    правильными — и электромагнитного, и обороты, и температуры"), so they are
    not consulted; when a box disagrees, the note says which speed was solved.

    ``None`` for the speed means "the hook's own precedence" (the panel, then
    ``simulation.rpm``), which only happens when the loop has no speed at all.
    """
    if rpm is None or not math.isfinite(float(rpm)) or float(rpm) <= 0.0:
        return None, None
    rpm = float(rpm)
    single = str(panel.get("cases") or "three").strip().lower() == "single"
    try:
        box = float(str(panel.get("rpm1" if single else "rpm", "")).strip() or "nan")
    except (TypeError, ValueError):
        box = float("nan")
    if math.isfinite(box) and abs(box - rpm) > 0.5:
        return rpm, ("solved at the operating %s rpm of this run (the "
                     "Mechanical tab's %s box says %s rpm)"
                     % (f"{rpm:,.0f}", "proof rpm" if single else "rpm",
                        f"{box:,.0f}"))
    return rpm, None


def _mechanical_step(body: Dict[str, Any], field: Dict[str, Any], *,
                     authorization: Optional[str] = None,
                     rpm: Optional[float] = None,
                     torque_nm: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Rotor stress at the temperatures the converged map gives EACH PART.

    User 2026-09-08: "в механический расчёт тоже нужно делать каплинг, чтобы
    температуры везде были одинаковы".  The Mechanical solver stays its own
    solver — this only hands it the four temperatures (magnet, rotor core, shaft,
    sleeve; the AVERAGE of each part, the number a thermal expansion belongs
    at) through the hook ``routes.mechanical.run_rotor_stress_at``, which does
    what the tab's Solve button does with the user's own saved settings and
    leaves the answer as the tab's last result.

    THE POINT is the run's own (2026-09-09).  ``rpm`` and ``torque_nm`` are the
    speed and the electromagnetic torque the loop just solved — they are passed
    explicitly because the hook would otherwise read the Mechanical tab's
    boxes, and those hold whatever was typed for the machine that was loaded at
    the time (the Ø200's 245.5 N·m would have been applied to a 1 N·m rotor).
    The standing rule: the operating point comes from the Simulation run,
    never from a box on another tab.  ``authorization`` is the caller's, so the
    overspeed factor, the fit, the mesh and the contact set are the USER's
    saved ones — the thermal half already reads its boundary conditions that
    way; without it the anonymous "shared" entry answered.

    Never fails the loop: a mechanical refusal (a sleeveless machine with a
    saved fit, an unmeshable rotor) is RECORDED in the coupling block with its
    message, because the electromagnetic and thermal answers above it are
    already good and losing them to a downstream error would be the worse
    outcome.  OPT-IN: ``body["mechanical"] = true`` runs the step (the
    Electromagnetic tab sends it with the toggle); a body that does not ask
    gets the loop exactly as before — a rotor-stress solve is not something to
    charge every caller of this route for.
    """
    if not body.get("mechanical"):
        return None
    temps: Dict[str, float] = {}
    for part, comp in (("rotor_core", "rotor"), ("magnet", "magnet"),
                       ("shaft", "shaft"), ("sleeve", "sleeve")):
        t = _bulk_temp(_component(field, comp))
        if t is not None:
            temps[part] = round(float(t), 2)
    if not temps:
        return {"ok": False, "temps_c": {}, "error": (
            "the thermal map has no rotor part to take a temperature from")}
    # THE TEMPERATURES GO THROUGH AS THEY ARE — the map's own numbers, per
    # part, which is what the user asked the coupling to fill in ("нужно
    # заполнять всё реальными цифрами").  What they DO is the solver's rule,
    # not this route's (user 2026-09-09: "нам нужно учитывать температуру
    # только как изменение давления на бандаж, если он есть"): the rotor
    # stress solve carries a temperature only as the change of a retaining
    # band's fit pressure, and solves every part as drawn — so on a machine
    # with no band the map changes nothing and the answer says so in its
    # `thermal.notes`.  The block repeats that in one line, because the
    # Electromagnetic tab's coupling line reads this block and not the
    # mechanical result.
    temps_note: Optional[str] = None
    if "sleeve" not in temps:
        temps_note = ("no retaining band: the rotor's temperature is not a "
                      "mechanical load here — it only changes a band's fit "
                      "pressure — so the parts are solved as drawn; the map "
                      "(%s) is carried for the record"
                      % ", ".join(f"{k} {v:.0f} °C" for k, v in temps.items()))
    speed_note: Optional[str] = None
    try:
        from motor_ai_sim.routes import mechanical as _mech
        kw: Dict[str, Any] = {}
        if body.get("geo") is not None:
            kw["geo"] = body.get("geo")
        if authorization is not None:
            kw["authorization"] = authorization
        panel = _mech._mech_panel_settings(authorization) if rpm is not None else {}
        speed, speed_note = _mechanical_point(panel, rpm)
        if speed is not None:
            kw["rpm"] = float(speed)
        if torque_nm is not None and math.isfinite(float(torque_nm)):
            kw["torque_nm"] = float(torque_nm)
        r = _mech.run_rotor_stress_at(temps, **kw) or {}
    except HTTPException as exc:
        return {"ok": False, "temps_c": temps, "error": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001 — recorded, never raised past the loop
        log.exception("coupled: the mechanical step failed")
        return {"ok": False, "temps_c": temps, "error": str(exc)}
    case_name = r.get("primary_case")
    case = ((r.get("cases") or {}).get(case_name) or {}) if case_name else {}
    out: Dict[str, Any] = {
        "ok": True, "temps_c": temps,
        "rpm": r.get("rpm"), "case": case_name,
        "sf_min": case.get("sf_min"), "sf_min_part": case.get("sf_min_part"),
        "sf_min_p05": case.get("sf_min_p05"),
        "rotor_od_growth_um": case.get("rotor_od_growth_um"),
        "od_growth": case.get("od_growth"),
        "air_gap": case.get("air_gap"),
        "interference_effective_mm": r.get("interference_effective_mm"),
        "elapsed_s": r.get("elapsed_s"),
    }
    if torque_nm is not None and math.isfinite(float(torque_nm)):
        out["torque_nm"] = round(float(torque_nm), 3)
    if speed_note:
        out["note"] = speed_note
    if isinstance(r.get("contact_fallback"), dict):
        # The route solved a joint bonded because separation left the part
        # unretained (2026-09-09) — carried into the block so the summary line
        # says it too.
        out["contact_fallback"] = r["contact_fallback"]
    if temps_note:
        out["temps_note"] = temps_note
    return out


def _http_detail_text(detail: Any) -> str:
    """The one line of an HTTPException's detail — the route's ``error`` when
    it sent the structured form, the text otherwise."""
    if isinstance(detail, dict):
        return str(detail.get("error") or detail.get("detail") or detail)
    return str(detail)


def _compact_modes(r: Dict[str, Any]) -> Dict[str, Any]:
    """The modal answer as one line's worth: the first frequency, how close the
    closest mode sits to an excitation line, how many are flagged.  The full
    result (every mode, the shapes) is the Mechanical tab's last modal answer
    and the duty's filed copy — this is what the coupling block quotes."""
    ms = [m for m in (r.get("modes") or []) if isinstance(m, dict)]
    f = [float(m["f_hz"]) for m in ms
         if m.get("f_hz") is not None and math.isfinite(float(m["f_hz"]))]
    seps = []
    for m in ms:
        ne = m.get("nearest") if isinstance(m.get("nearest"), dict) else {}
        if ne.get("margin_pct") is not None:
            try:
                seps.append((abs(float(ne["margin_pct"])), m, ne))
            except (TypeError, ValueError):
                pass
    tight = min(seps, key=lambda t: t[0]) if seps else None
    first = ms[f.index(min(f))] if f else None
    out: Dict[str, Any] = {
        "ok": True, "body": r.get("body"), "support": r.get("support"),
        "n_modes": len(ms), "rpm": r.get("rpm"),
        # WHICH CARRIER the excitation column was built on — the report checks
        # it against the duty's own `sim.fSwitch` (A1, 2026-09-14).
        "f_switch_hz": r.get("f_switch_hz"),
        "f1_hz": (round(min(f), 1) if f else None),
        "f1_order": (first or {}).get("order"),
        "n_flagged": sum(1 for _, _, ne in seps if ne.get("flag")),
        "cached": bool(r.get("cached")), "elapsed_s": r.get("elapsed_s"),
    }
    if tight is not None:
        _, m, ne = tight
        out["tightest"] = {
            "f_hz": round(float(m.get("f_hz")), 1), "order": m.get("order"),
            "excitation": ne.get("name"), "excitation_hz": ne.get("hz"),
            "margin_pct": round(float(ne["margin_pct"]), 1),
            "flag": bool(ne.get("flag"))}
    return out


def _compact_crit(r: Dict[str, Any]) -> Dict[str, Any]:
    """The rotordynamics answer as one line's worth: the first FORWARD critical
    and its margin over rated (relative to the critical, as the report quotes
    it), how many forward criticals lie below rated, the verdict, and the two
    shaft-line assumptions the number depends on most.

    Plus the CAMPBELL SWEEP (2026-09-14), decimated by
    ``duty_results.compact_campbell``: the crossings say where the branches met
    the 1x line, and the report's diagram draws the branches — a block that kept
    only the crossings published empty axes under a caption promising curves."""
    crits = [c for c in (r.get("critical_speeds") or []) if isinstance(c, dict)]
    rated = r.get("rated_rpm")
    try:
        rated = float(rated) if rated is not None else None
    except (TypeError, ValueError):
        rated = None
    fwd = sorted(float(c["rpm"]) for c in crits
                 if c.get("rpm") is not None and str(c.get("whirl")) == "forward")
    first = fwd[0] if fwd else None
    margin = (100.0 * (first - rated) / first) if (first and rated) else None
    inp = r.get("inputs") if isinstance(r.get("inputs"), dict) else {}
    try:
        from motor_ai_sim.duty_results import compact_campbell as _cc
        cam = _cc(r.get("campbell"))
    except Exception:  # noqa: BLE001 — a missing sweep is a missing figure, not an error
        cam = None
    return {
        "ok": True, "rated_rpm": rated,
        "campbell": cam,
        "rpm_plot_max": r.get("rpm_plot_max"),
        "f_switch_hz": r.get("f_switch_hz"),
        "first_forward_rpm": (round(first) if first else None),
        "first_forward_margin_pct": (round(margin, 1) if margin is not None else None),
        "n_forward_below_rated": (sum(1 for v in fwd if v <= rated) if rated else 0),
        "n_criticals": len(crits),
        "verdict": r.get("verdict"),
        "bearing_span_mm": inp.get("bearing_span_mm"),
        "bearing_k_n_per_m": inp.get("bearing_k_n_per_m"),
        "cached": bool(r.get("cached")), "elapsed_s": r.get("elapsed_s"),
    }


def _drive_carrier(body: Dict[str, Any], *, default: bool) -> Dict[str, Any]:
    """THE CARRIER OF THE RUN BEING SOLVED — ``{hz, origin, source}``.

    2026-09-24 (owner: «Это значение нужно задавать в контроллере; PWM нужно
    выкинуть из Electromagnetic»): the CONTROLLER owns it.  In order —

      * ``body.controller.f_carrier_hz`` (the Controller block the Coupled
        panel sends by reference) and the SAVED Controller settings of the
        loaded configuration;
      * ``f_switch_hz`` / ``f_switch`` in the body — the retired Simulation-tab
        field an old browser session or a script may still send: ACCEPTED,
        below the Controller, and logged as deprecated;
      * the migration tier (``inverter.drive_source.legacy_carrier``: the
        duty's saved ``sim.fSwitch``, then ``simulation.f_switch``);
      * the Controller's stated default — only when ``default`` (a bridge
        drive needs a carrier; the excitation table does not, and draws no
        PWM line rather than a made-up one).
    """
    from motor_ai_sim.inverter import drive_source as _DS
    ctl = body.get("controller") if isinstance(body.get("controller"), dict) else None
    ans = _DS.resolve_carrier_for(controller=ctl, default=False)
    if ans["origin"] == _DS.ORIGIN_CONTROLLER:
        return ans
    for k in ("f_switch_hz", "f_switch"):
        if body.get(k) is None:
            continue
        try:
            v = float(body.get(k))
        except (TypeError, ValueError):
            continue
        _DS.warn_deprecated(
            "coupled: '%s' in the request — the carrier is the Controller's "
            "since 2026-09-24 (save it in the Controller tab)" % k)
        if v > 0.0:
            return {"hz": v, "origin": _DS.ORIGIN_REQUEST,
                    "source": "the request's %s (deprecated field — no "
                              "carrier saved in the Controller)" % k}
        # 0 is "this run has no PWM line" — the caller said so explicitly.
        return {"hz": None, "origin": _DS.ORIGIN_REQUEST,
                "source": "the request's %s = 0 (no PWM line)" % k}
    if ans["hz"] is not None or not default:
        return ans
    return _DS.resolve_carrier_for(controller=ctl, default=True)


def _drive_v_dc(body: Dict[str, Any]) -> Dict[str, Any]:
    """The DC link — the Controller's manual V_dc, else the battery
    (``inverter.drive_source.resolve_v_dc``).  ``{"V": None, ...}`` when the
    machine names neither; the caller refuses by name."""
    from motor_ai_sim.inverter import drive_source as _DS
    ctl = body.get("controller") if isinstance(body.get("controller"), dict) else None
    ans = _DS.resolve_v_dc_for(controller=ctl)
    if ans["V"] is None:
        v = _pack_nominal_v()
        if v is not None:
            return {"V": v, "origin": _DS.ORIGIN_CONTROLLER,
                    "source": _INV_SOURCES["v_dc_V"]}
    return ans


def _effective_f_switch(body: Dict[str, Any]) -> Optional[float]:
    """THE CARRIER OF THE RUN BEING SOLVED, for the excitation tables.

    The ring modes and the whirl branches are compared against slot passing and
    against the inverter carrier, and the carrier is a SETTING of the machine's
    CONTROLLER (2026-09-24), not an output of the current-drive solve this loop
    makes.  Resolved once, with the run, by :func:`_drive_carrier` (no default:
    ``None`` = no PWM line is drawn), and passed down explicitly.

    Read HERE rather than inside the mechanical route, so the value that lands
    in the modal record is the one this run was made with.  Before 2026-09-14
    the modal step read the process-global itself, at whatever moment it ran,
    and the Ø85's 48 kHz ended up in the Ø200 L155 rated duty's ring-mode
    table.
    """
    return _drive_carrier(body, default=False)["hz"]


def _modal_steps(body: Dict[str, Any], *, authorization: Optional[str],
                 rpm: Optional[float], run_id: str,
                 f_switch_hz: Optional[float] = None) -> Dict[str, Any]:
    """The two temperature-FREE mechanical answers, at this run's speed.

    User 2026-09-13: "при каплинге чтобы всё решалось — и модальный, и
    частоты, чтобы к отчёту было всё готово".  The ring modes and the shaft's
    critical speeds go through the same hooks the tab's two buttons use
    (``run_modes_at`` / ``run_critical_speeds_at`` — the user's saved body,
    mode count, mesh and shaft line), so the report's mechanical page and the
    per-duty columns are filled by the coupled run itself.  Neither model reads
    a temperature (bonded, unprestressed — their own ``assumptions`` say so),
    which is why they are not gated on the thermal map: they describe the
    machine, not its state.  Each is recorded — never raised — like the stress
    step; a refusal names its reason in the block.
    """
    from motor_ai_sim.routes import mechanical as _mech
    kw: Dict[str, Any] = {}
    if body.get("geo") is not None:
        kw["geo"] = body.get("geo")
    if authorization is not None:
        kw["authorization"] = authorization
    if rpm is not None and math.isfinite(float(rpm)) and float(rpm) > 0.0:
        kw["rpm"] = float(rpm)
    # ALWAYS passed, `None` included: the hooks read it as "this run has no PWM
    # line", not as "go and look at the process-global block" — which is the
    # whole point of resolving it out here (see `_effective_f_switch`).
    kw["f_switch_hz"] = (float(f_switch_hz) if f_switch_hz else 0.0)
    out: Dict[str, Any] = {}
    for key, hook, phase, compact in (
            ("modes", _mech.run_modes_at,
             "mechanical — ring modes at this run's speed", _compact_modes),
            ("critical_speeds", _mech.run_critical_speeds_at,
             "mechanical — critical speeds (rotordynamics)", _compact_crit)):
        _check_cancelled(run_id)
        _progress.update(phase=phase)
        try:
            out[key] = compact(hook(**dict(kw)) or {})
        except _LoopCancelled:
            raise
        except HTTPException as exc:
            out[key] = {"ok": False, "error": _http_detail_text(exc.detail)}
        except Exception as exc:  # noqa: BLE001 — recorded, never raised past the loop
            log.exception("coupled: the %s step failed", key)
            out[key] = {"ok": False, "error": str(exc)}
    return out


# ---------------------------------------------------------------------------
# Writing the answer back onto the run it belongs to
# ---------------------------------------------------------------------------

def _attach_coupling(em_result: Dict[str, Any], block: Dict[str, Any]) -> bool:
    """Put ``coupling`` in the last transient's SUMMARY.

    The summary is where the Electromagnetic tab's cards read from, and this
    block is a statement ABOUT those cards: they were computed at a winding and a
    magnet temperature the machine actually reaches, over N iterations.  A run
    that was not coupled carries no such key and reads exactly as it always did —
    that is the whole "off = today's behaviour" promise, expressed in the payload.

    Both copies are updated and they are different objects: ``em_result`` is the
    response (and the in-memory cache entry), while
    ``simulation._last_transient_ref["result"]`` is the frames-stripped copy that
    the ?restore=true store and the disk file hold.  Refuses when another run has
    landed in between — labelling somebody else's run as coupled would be worse
    than not labelling this one.
    """
    from motor_ai_sim.routes import simulation as sim

    s = dict(em_result.get("summary") or {})
    s["coupling"] = block
    em_result["summary"] = s

    key = sim._last_transient_ref.get("key")
    res = sim._last_transient_ref.get("result")
    if key is None or not isinstance(res, dict):
        # No persisted last run: the EM run was not of the LIVE machine (a
        # per-request geometry override that is not the one on screen), so there
        # is nothing on disk this block could describe.
        return False
    if em_result.get("computed_at") and res.get("computed_at") != em_result.get("computed_at"):
        log.warning("coupled: another transient landed while the loop ran — "
                    "the coupling block was NOT written to the last run")
        return False
    out = dict(res)
    out["summary"] = dict(s)
    sim._save_last_transient(tuple(key), out, journal=False)
    return True


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

#: What the COUPLING block keeps of the regime.  The curves and the per-period
#: span are not here on purpose: they are big, they are drawn from the
#: ``duty_cycle`` record the same run files, and this block travels inside every
#: transient summary the panel holds.
_CYCLE_BLOCK_KEYS = (
    "kind", "duty", "cycle_s", "ed_cycle_s", "ed_requested_pct",
    "ed_allowable_pct", "t_on_allowable_s", "requested_t_on_s",
    "fits_requested", "feasible", "unlimited", "limiting_part", "limits_c",
    "magnet_limit_source", "hot_spot_offset_K", "winding_hot_peak_c",
    "magnet_peak_c", "coil_temp_c", "magnet_temp_c", "s2_time_to_limit_s",
    "s2_limiting_part", "s2_note", "s2_from_rated_s", "s2_from_rated_part",
    "s2_from_rated_duty", "at_allowable", "at_requested", "ed_note", "note")


def _cycle_block_of(regime: Optional[Dict[str, Any]],
                    inputs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The regime as the coupled record carries it — the answer, not the
    workings."""
    out = {k: regime.get(k) for k in _CYCLE_BLOCK_KEYS
           if regime and regime.get(k) is not None}
    # …spelled ALSO the way the stored `duty_cycle` record spells it, so the web
    # reads a coupled regime with the helper it already has
    # (`dutyCycleRegime.regimeLine`) instead of growing a second reader of the
    # same four numbers.
    lims = dict(out.get("limits_c") or {})
    if lims.get("winding") is not None:
        out["winding_limit_c"] = lims["winding"]
    if lims.get("magnet") is not None:
        out["magnet_limit_c"] = lims["magnet"]
    if out.get("limiting_part"):
        out["ed_limiting_part"] = out["limiting_part"]
    if regime:
        out["ed_found"] = bool(regime.get("feasible", True))
    if inputs:
        out.setdefault("duty", inputs.get("duty"))
        if not out.get("s2_from_rated_s") and inputs.get("rated_state_source"):
            # WHY there is no "and from rated" answer — absent with no reason
            # reads as a number somebody forgot to compute.
            out["s2_from_rated_note"] = str(inputs["rated_state_source"])
    return out


# ---------------------------------------------------------------------------
# Persistent history (2026-09-22) — "don't recompute an identical coupled run"
# ---------------------------------------------------------------------------
# Owner, first sentence of the 2026-09-22 ask: *"если я запускаю те же
# параметры каплинга, он не считается, а подгружает уже рассчитанный
# вариант"*.  There is no in-process memo to build on here (unlike the EM
# transient's ``_fem_transient_cache`` or mechanical's ``rsm.cache_get``):
# ``_em_run`` below deliberately forces every INNER electromagnetic pass to
# solve (``fresh=True, ledger=False`` at its own call site) because a coupled
# iteration must see a real field each step — that has nothing to do with the
# OUTER question this section answers, which is "has this /run request, as a
# whole, already been solved before".
_COUPLED_HISTORY = _RH.history_for("coupled.run")
_RH.register_kind("coupled.run")


def _coupled_body_bool(body: Dict[str, Any], key: str, default: bool = False) -> bool:
    v = body.get(key)
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(v)


def _coupled_canonical(body: Dict[str, Any], cooling: Dict[str, Any],
                       solve_to: str, max_iter: int) -> Dict[str, Any]:
    """Every field that can change the coupled answer — the key the brief
    asks for, spelled out field by field:

    * ``cfg`` — ``routes.simulation._config_physics_fingerprint`` (with this
      request's material override folded in): the LIVE geometry object plus
      the raw ``geometry:``/``winding:``/``materials:``/``magnet:`` config
      blocks and the per-part accounting state (``excluded``/``reference``).
      This is what the brief calls "geometry_fingerprint_v2" and "part
      states" — the closest existing machine-identity hash in this codebase
      (``geometry_fingerprint_v2`` itself is never called anywhere in the
      backend outside its own module and tests; every route that needs a
      machine fingerprint, including mechanical.py's reference integration,
      uses this one).
    * ``body`` — the WHOLE request body, minus ``run_id`` (a fresh nonce
      every launch), ``for_duty`` (a log label) and ``record`` (a
      ``record: false`` errand never reaches this cache at all — see
      ``_run``).  ``_run``'s own docstring says what this already is:
      "every parameter the Electromagnetic Run sends" — the operating point
      (I/rpm/gamma), the drive/PWM/inverter block, star/delta, coil/magnet
      temperatures, mesh/steps-per-period/periods/sectors, the
      eddy/rotor_eddy/demag/element-order flags, plus ``max_iter``,
      ``tol_k``, ``damping``, ``thermal_settings`` (when the caller sent its
      own) and ``cold_constants``.  ``mat`` is dropped here — it already
      rode into ``cfg`` above; keeping it twice would just be two names for
      one input.
    * ``cooling`` — the RESOLVED cooling block (``thermal_settings.
      cooling_fields``): mode, both fluids/flows, ambient, frame, mount,
      emissivity, end faces.  Resolved and not raw, because the normal case
      is an ABSENT ``thermal_settings`` in the body, which then means "the
      caller's remembered Thermal-tab settings" — two requests with the same
      (absent) body key can still be different runs if the user's stored
      Thermal tab changed between them, and only the resolved block sees
      that.
    * ``solve_to`` / ``max_iter`` — already inside ``body`` too; pulled out
      explicitly so a future rename/alias inside the body can never quietly
      stop being keyed.

    ``k_3d`` is deliberately NOT an independent input: ``_cold_constants``
    derives it from the geometry alone, so ``cfg`` above already determines
    it bit for bit — hashing it separately could only ever agree with what
    ``cfg`` says, never catch anything ``cfg`` would miss.
    """
    skip_keys = {"run_id", "for_duty", "record", "mat"}
    body_norm = {k: v for k, v in body.items() if k not in skip_keys}
    from motor_ai_sim.routes.simulation import _config_physics_fingerprint
    return {
        "cfg": _config_physics_fingerprint(with_request_materials=True),
        "body": _RH.round_floats(body_norm),
        "cooling": _RH.round_floats(dict(cooling or {})),
        "solve_to": str(solve_to),
        "max_iter": int(max_iter),
    }


def _coupled_history_key(body: Dict[str, Any], cooling: Dict[str, Any],
                         solve_to: str, max_iter: int) -> str:
    return _RH.make_key("coupled.run",
                        _coupled_canonical(body, cooling, solve_to, max_iter))


def _coupled_summary(block: Dict[str, Any], solve_to: str) -> str:
    try:
        bits = [f"{float(block.get('coil_temp_c') or 0.0):.1f}°C coil"]
        mag = block.get("magnet_temp_c")
        if mag is not None:
            bits.append(f"{float(mag):.1f}°C magnet")
        bits.append(str(solve_to))
        if block.get("mode"):
            bits.append(str(block["mode"]))
        if block.get("limited"):
            bits.append("AT THE LIMIT")
        elif not block.get("converged"):
            bits.append("not converged")
        return ", ".join(bits)
    except Exception:                                       # noqa: BLE001
        return "coupled run"


def _load_coupled_history_entry(entry: Dict[str, Any],
                                payload: Dict[str, Any]) -> Dict[str, Any]:
    """``run_history.register_loader`` hook AND the shared tail of a
    ``/run`` history hit: the response is the whole stored ``out`` dict, so
    the S1 auto-set and the "AT THE LIMIT" line (``coupling.warning``,
    ``coupling.limited``) come back exactly as the record has always shown
    them — they are already baked into the payload, nothing here recomputes
    them. What this function still has to REPLAY is the SIDE EFFECT a normal
    run has: ``_remember_last`` (``.last_coupled.json`` + the per-duty
    ``duty_results.note_coupled`` row), so a History-popover load makes the
    Coupled panel and the report agree exactly as a fresh Run would.

    KNOWN GAP (2026-09-22, scoped out for time): the impulse-duty regime
    save (``duty_results.note_duty_cycle``, S2/S3 machines) is NOT replayed
    — it needs ``cycle_doc``/``cycle_in``, which only exist as local state
    inside a live solve. A loaded S2/S3 record still shows correctly on the
    Coupled panel; the duty-cycle editor's regime row keeps whatever the
    LAST real solve filed there until the next real solve.
    """
    out = dict(payload)
    out["served_from_history"] = True
    out["computed_at"] = entry.get("computed_at")
    out["history_key"] = entry.get("key")
    try:
        _remember_last({k: v for k, v in out.items()
                        if k not in ("transient", "thermal", "served_from_history",
                                    "history_key")},
                       alt_carrier=bool(entry.get("alt_carrier")))
    except Exception:                                       # noqa: BLE001
        log.debug("coupled: history load could not update /last", exc_info=True)
    log.info("coupled: %s served from history (computed %s); the duty-cycle "
             "regime row was not re-filed (known gap)",
             entry.get("key"), entry.get("computed_at"))
    return out


_RH.register_loader("coupled.run", _load_coupled_history_entry)


# ── THE SINE STATE, filed under a DRIVE-INDEPENDENT key (2026-09-25) ─────────
# Every loop that runs on the sine current — a plain sine run, or the sine
# phase of a drive=inverter `final_pass` run — files its converged state here,
# keyed on everything EXCEPT the drive.  A later inverter run of the same
# machine, point, cooling and question finds it and goes straight to the PWM
# pass(es).  Same run_history rules as every other kind: exact key, same code
# version, ``fresh`` always solves.
_SINE_STATE_HISTORY = _RH.history_for("coupled.sine_state")

#: Body keys that describe the DRIVE (or a label / a launch), not the sine
#: state: two requests that differ only in these share one sine state.
_SINE_STATE_SKIP = frozenset({
    "run_id", "for_duty", "record", "mat", "fresh", "drive", "inverter",
    "controller", "inverter_coupling", "sine_compare", "cold_constants",
    "mechanical", "harm_ref"})


def _sine_state_canonical(body: Dict[str, Any], cooling: Dict[str, Any],
                          solve_to: str, max_iter: int) -> Dict[str, Any]:
    from motor_ai_sim.routes.simulation import _config_physics_fingerprint
    body_norm = {k: v for k, v in body.items() if k not in _SINE_STATE_SKIP}
    return {
        "cfg": _config_physics_fingerprint(with_request_materials=True),
        "body": _RH.round_floats(body_norm),
        "cooling": _RH.round_floats(dict(cooling or {})),
        "solve_to": str(solve_to),
        "max_iter": int(max_iter),
    }


def _sine_state_key(body: Dict[str, Any], cooling: Dict[str, Any],
                    solve_to: str, max_iter: int) -> str:
    return _RH.make_key("coupled.sine_state",
                        _sine_state_canonical(body, cooling, solve_to, max_iter))


def _sine_state_miss_reason(body: Dict[str, Any], cooling: Dict[str, Any],
                            solve_to: str, max_iter: int) -> Optional[str]:
    """One line: why the newest stored sine state of this machine is not this
    one — the first input that differs — or ``None`` when there is none."""
    want = _sine_state_canonical(body, cooling, solve_to, max_iter)
    for e in _SINE_STATE_HISTORY.list():
        p = e.get("params") or {}
        if p.get("cfg") != want["cfg"]:
            continue
        for k in ("solve_to", "max_iter"):
            if p.get(k) != want[k]:
                return "a sine state of this machine exists but %s differs" % k
        pc, wc = p.get("cooling") or {}, want["cooling"]
        for k in sorted(set(pc) | set(wc)):
            if pc.get(k) != wc.get(k):
                return ("a sine state of this machine exists but cooling.%s "
                        "differs (%r vs %r)" % (k, pc.get(k), wc.get(k)))
        pb, wb = p.get("body") or {}, want["body"]
        for k in sorted(set(pb) | set(wb)):
            if pb.get(k) != wb.get(k):
                return ("a sine state of this machine exists but %s differs "
                        "(%r vs %r)" % (k, pb.get(k), wb.get(k)))
        if e.get("code_version") != _RH.code_version():
            return "a sine state of this machine exists from an older build"
        return None
    return None


def _record_wanted(body: Dict[str, Any]) -> bool:
    """Is this run THIS machine's answer, or an errand for another duty?

    ``record: false`` in the body says errand.  Anything else — the key absent,
    which is every caller that existed before 2026-09-15 — says answer, so the
    default is today's behaviour bit for bit.
    """
    v = body.get("record")
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off")
    return bool(v)


@router.post("/run")
@_JOBS.queued("coupled.run", priority=_JOBS.Priority.DUTY,
              run_id_from=_JOBS.body_run_id("coupled"))
def run(body: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """``POST /api/coupled/run`` — the loop, with one extra body key.

    ``record`` (default ``true``).  ``false`` means THIS RUN IS NOT THIS
    MACHINE'S ANSWER: it is a solve made on behalf of another duty — today's
    only caller is the duty-cycle editor's escape hatch, which runs the
    CALIBRATION duty's point (14.7 A, 1 000 rpm, coil 30 °C) while the editor
    holds a different duty (a 45.96 A peak at 200 °C) — and nothing about it may
    be filed as this machine's last result.  ``for_duty`` names that duty, for
    the log line and nothing else.

    What ``record: false`` suppresses (``run_recording``, the whole list):
    ``duty_fields.save_active`` for every kind, ``duty_results``' per-duty rows
    (every ``note_*``), and the ``_LAST`` / on-disk stores of the thermal,
    mechanical and coupled routes.  What it does NOT touch — deliberately, since
    it is the entire purpose of the run — is the transient run store and the
    FIELD SNAPSHOT store, which is where the duty-cycle route looks the loss map
    up by its exact physics key.

    It is refused with ``max_iter > 1``: an errand is one pass, and a loop that
    iterates this machine's temperatures onto another duty's point is not a
    calibration run, it is a mistake with a longer runtime.

    Everything below is the contract the loop always had.
    """
    tok = None if _record_wanted(body) else _rr.suppress()
    try:
        return _run(body, authorization)
    finally:
        _rr.restore(tok)


def _run(body: Dict[str, Any],
         authorization: Optional[str] = None) -> Dict[str, Any]:
    """Run EM → Thermal → EM … until the winding and the magnet settle.

    BODY: every parameter the Electromagnetic Run sends (the same dict the panel
    posts to ``/api/kernel/run``), plus

      ``max_iter``          how many EM runs at most (default 6, and the Thermal
                            panel's own ``maxIter`` when it has one);
      ``tol_k``             the convergence band in KELVIN, on BOTH temperatures
                            (default 2.0);
      ``damping``           under-relaxation of the update (default 0.5);
      ``thermal_settings``  the cooling boundary conditions, in the Thermal
                            panel's own field names.  ABSENT is the normal case:
                            the caller's remembered Thermal-tab settings are then
                            loaded server-side and mapped, so the loop solves the
                            boundary conditions the user set on that tab rather
                            than a default invented here.

    A sync ``def``, so FastAPI runs it in the threadpool exactly as it runs the
    transient route: the loop is minutes long and must not sit on the event loop
    that serves ``/progress``.
    """
    t0 = time.time()
    run_id = str(body.get("run_id") or "")
    # A cancel that arrived BEFORE this run started is honoured, not discarded:
    # the id is a fresh nonce per Run (the panel's ``runNonce``), so a pending
    # cancel carrying it can only be about this very run — the user pressed Stop
    # while the request was in flight or waiting for a queue slot.  It used to
    # be cleared here, which made that cancel a no-op for the LOOP and left it
    # to the transient's own registry to stop the run one full EM solve later.
    # Migration Stage 4 reaches the same 499 without solving anything.

    from motor_ai_sim import mech_losses as _ml
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes.simulation import _effective_rpm, _parse_mat_override
    from motor_ai_sim.routes.thermal import RUNAWAY_C
    from motor_ai_sim.thermal_settings import (cooling_fields, cooling_issue,
                                               coupled_iteration_settings,
                                               thermal_panel_settings)

    # Per-request materials through the BODY, the kernel's transport: the router
    # dependency only ever sees `?mat=`, and a user working on their own copy of
    # a motor posts it here.  Same parse/validate/set the transient route does.
    if body.get("mat") is not None:
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

    # ── the thermal half's boundary conditions ──────────────────────────────
    # The CALLER's remembered Thermal tab, not the anonymous bucket: the panel
    # store is keyed per user, and a body without `thermal_settings` used to be
    # solved with whatever the shared entry held — still air at h = 7 W/m²K on
    # a 6 kW machine, an 8 500 °C "runaway" on 2026-09-08 13:54.
    stored = thermal_panel_settings(authorization)
    raw = body.get("thermal_settings")
    settings = dict(raw) if isinstance(raw, dict) else dict(stored)

    # From the EFFECTIVE settings, not the stored ones: a caller that sent its
    # own `thermal_settings` sent the Thermal tab's whole block, `maxIter`
    # included, and reading the iteration count from a different copy than the
    # cooling would be two halves of one panel disagreeing.  Resolved BEFORE the
    # pre-flight, which needs to know whether this is a loop or a single pass.
    max_iter = int(body.get("max_iter")
                   or coupled_iteration_settings(settings)["max_iter"]
                   or DEFAULT_MAX_ITER)
    max_iter = max(1, min(40, max_iter))

    # ── an errand is ONE PASS ───────────────────────────────────────────────
    # `record: false` means this run answers for a duty that is not the one
    # loaded.  One EM run plus one thermal solve at a stated point is an honest
    # thing to ask for; iterating that point to a fixed point is a coupled
    # ANSWER, and an answer nobody may file is an answer nobody can read.
    # Refused before anything is solved, with the number the caller sent.
    if _rr.suppressed():
        if max_iter > 1:
            raise _refuse(
                "record: false is a single background pass — one electromagnetic "
                "run and one thermal solve at the point you named (this request "
                "asks for %d iterations). Send max_iter = 1, or drop record to "
                "let the loop file its answer under the loaded duty."
                % max_iter,
                ["record", "max_iter"], code="record_false_needs_single_pass")
        log.info("coupled: record: false — nothing will be filed under the "
                 "loaded duty%s",
                 (" (this run is for %r)" % str(body.get("for_duty")))
                 if body.get("for_duty") else "")

    # WHICH OF THE TWO QUESTIONS (owner 2026-09-18) — resolved here, BEFORE the
    # pre-flight, for the reason everything else in this neighbourhood is: a
    # misspelled mode is a 422 in milliseconds, not after a transient.
    solve_to = _solve_to(body)
    # ``continuous`` STOPS THE LOOP EXACTLY WHERE ``limits`` DOES — the S1
    # search below is not another loop, it is one more thing done with the
    # pass the loop already made, so it must never buy itself extra
    # electromagnetic passes by changing the stop rule.  `solve_to` (what was
    # ASKED, stored on the record) is kept separate from `_loop_solve_to`
    # (which of the two STOP RULES the iteration itself obeys).
    _loop_solve_to = "limits" if solve_to in ("limits", "continuous") else "steady"

    _preflight(body, max_iter=max_iter)

    issue = cooling_issue(settings)
    if issue is not None:
        raise _refuse(
            "the Thermal tab's cooling cannot be solved: %s. Set it on the "
            "Thermal tab (or send thermal_settings) and run again." % issue,
            ["thermal_settings"], code="no_thermal_boundary")
    cooling = cooling_fields(settings)

    # ── persistent history (2026-09-22) ─────────────────────────────────────
    # The earliest point every field the key needs is resolved (solve_to,
    # max_iter, thermal settings validated, cooling resolved) — and BEFORE
    # anything that looks like a solve starts: no ``_LOCK``, no
    # ``_progress.start``, no EM/thermal call.  A hit therefore returns with
    # no progress ring and holds the run lock for nobody.  Never on an
    # errand (``record: false``): that run is deliberately not this
    # machine's answer and must neither be served as one nor stored as one.
    history_fresh = _coupled_body_bool(body, "fresh", False)
    history_key: Optional[str] = None
    if not _rr.suppressed():
        history_key = _coupled_history_key(body, cooling, solve_to, max_iter)
        if not history_fresh:
            history_hit = _COUPLED_HISTORY.get(history_key)
            if history_hit is not None:
                return _load_coupled_history_entry(
                    history_hit["entry"], history_hit["payload"])

    # ── AN IMPULSE DUTY IS SOLVED AS A REGIME (2026-09-16) ──────────────────
    # On an S2/S3 duty the loop no longer iterates towards the temperature this
    # point would reach if it never ended: each pass fits the lumped cycle to
    # the map it just solved, FINDS the duty ratio (or the on-time) the machine
    # can hold inside its limits, and feeds back the temperatures AT that point.
    # `None` = the continuous duty the loop has always solved, bit for bit.
    #
    # NOT on an errand (`record: false`): that run is made at ANOTHER duty's
    # operating point while this one is loaded, so the cycle the context names
    # is not the cycle of the run in hand — and a regime found for the wrong
    # point is worse than no regime at all.
    cycle_in = None if _rr.suppressed() else _cycle_inputs(body, cooling)
    cycle_model: Any = None
    regime: Optional[Dict[str, Any]] = None
    prev_regime: Optional[Dict[str, Any]] = None
    cycle_doc: Optional[Dict[str, Any]] = None
    # HOW LONG THE POINT MAY BE HELD, when it is past a limit (2026-09-17).
    # `None` on every machine that states no limit at all and on every run whose
    # map could not be fitted; a point INSIDE its limits still gets a block, and
    # that block says so — "nothing is over" is an answer a reader may rely on.
    time_to_limit: Optional[Dict[str, Any]] = None
    # …AND WHICH OF THE TWO QUESTIONS THIS RUN IS (owner 2026-09-18).  `steady`
    # is the default and every line below it behaves exactly as it always has;
    # `limits` stops at the first limit a part reaches and re-computes the
    # machine AT that moment.  `limited` is the block that says what that moment
    # is — `None` right through a steady run, and `None` in `limits` mode too
    # whenever there is no first crossing to stop at.
    limited: Optional[Dict[str, Any]] = None
    limited_stop = False
    # …AND THE CATALOGUE CONSTANTS (owner 2026-09-18).  One extra pass at 20/20
    # after the loop has finished, so the report can print the KV / Kt / Km /
    # Km-per-kg a datasheet quotes beside the hot ones this duty actually runs
    # at.  `cold_constants: false` in the body switches it off — an errand or a
    # sweep point does not need it and it is a whole transient.
    constants_20c: Optional[Dict[str, Any]] = None
    # …and NEVER on an ERRAND.  `record: false` is one electromagnetic run and
    # one thermal solve at a point this machine is not loaded at, by contract
    # (the route's own docstring and its `max_iter` refusal); a second transient
    # bolted onto it would break that promise, and the constants would be filed
    # nowhere anyway.
    want_cold = bool(body.get("cold_constants", True)) and not _rr.suppressed()
    # A non-positive band is a loop that can never stop, which is a typo far more
    # often than a request: it falls back to the default rather than running the
    # whole budget on every machine for ever.
    tol = abs(_f(body, "tol_k", DEFAULT_TOL_K)) or DEFAULT_TOL_K
    damping = _f(body, "damping", DEFAULT_DAMPING)
    if not (0.0 < damping <= 1.0):
        raise _refuse("damping must be in (0, 1]; got %r" % (body.get("damping"),),
                      ["damping"])
    runaway_c = float(RUNAWAY_C or _RUNAWAY_FALLBACK_C)

    # ── the starting pair ───────────────────────────────────────────────────
    t_coil = _f(body, "coil_temp_c", 120.0)
    t_mag = body.get("magnet_temp_c")
    t_mag = float(t_mag) if t_mag is not None else _magnet_reference_temp_c()
    rpm_eff = _effective_rpm(body.get("rpm"))
    # ── WHICH DRIVE, and the inverter behind it ─────────────────────────────
    # Resolved here, once, and BEFORE the lock is taken: an incomplete inverter
    # is a 422 in milliseconds, not after a transient.
    drive = _coupled_drive(body)
    inverter = (_inverter_settings(body, rpm=rpm_eff)
                if drive in _BRIDGE_DRIVES else None)
    # …and, on the CONTROLLER's bridge, the device behind it.  Resolved with the
    # inverter block and before the lock, for the same reason: a controller with
    # no device is a 422 in milliseconds, not after a transient.
    ctl: Optional["_ControllerLoop"] = None
    if drive == "inverter":
        _ctl_cfg = _controller_settings(body, rpm=rpm_eff, inverter=inverter)
        _ctl_sd = str(body.get("star_delta") or "").strip().lower()
        if not _ctl_sd:
            from motor_ai_sim.routes.simulation import (
                _effective_star_delta as _esd2)
            _ctl_sd = _esd2(None)
        ctl = _ControllerLoop(_ctl_cfg, inverter=inverter, star_delta=_ctl_sd,
                              rpm=rpm_eff, pole_pairs=_pole_pairs(body),
                              i_leg_seed_A=_f(body, "I_phase_rms", 0.0))
    # …and the carrier, resolved WITH the run and before anything else can move
    # the shared configuration under it (2026-09-14).  It reaches the modal and
    # rotordynamic steps explicitly and is stored in their records.
    # …and on a PWM run it is the carrier THIS run is actually fed by, which is
    # the one the ring modes and the whirl branches must be compared against.
    fsw_eff = (float(inverter["f_carrier_hz"]) if inverter
               else _effective_f_switch(body))
    # ── DRIVE = INVERTER: THE LOOP ON THE SINE, THE PWM ONCE (2026-09-25) ───
    # `final_pass` (the default): the inverter and the controller are put
    # aside here and every pass below — the loop, the pass at the limit, the
    # S1 verification — runs on the ideal sine current; the controller's PWM
    # comes back after it, on the converged state (`_pwm_final_passes`).
    # `full` keeps the old loop, every pass on the PWM, for validation.
    inv_mode = _inverter_coupling_mode(body) if drive == "inverter" else None
    final_pass = inv_mode == "final_pass"
    inv_final: Optional[Dict[str, Any]] = None
    ctl_final: Optional["_ControllerLoop"] = None
    if final_pass:
        inv_final, ctl_final = inverter, ctl
        inverter, ctl = None, None
    # …AND IF THIS VERY SINE STATE WAS ALREADY SOLVED, IT IS NOT SOLVED AGAIN
    # (owner 2026-09-25: «если уже есть каплинг с синусом — просто запускается
    # расчёт с PWM из контроллера»).  Looked up under the drive-independent
    # sine-state key; `fresh` (Recompute) always solves.
    sine_key: Optional[str] = None
    sine_hit: Optional[Dict[str, Any]] = None
    sine_reuse_note: Optional[str] = None
    if (inverter is None and drive in ("current", "sine", "inverter")
            and not _rr.suppressed()):
        try:
            sine_key = _sine_state_key(body, cooling, solve_to, max_iter)
            if final_pass and not history_fresh:
                sine_hit = _SINE_STATE_HISTORY.get(sine_key)
                if sine_hit is None:
                    sine_reuse_note = _sine_state_miss_reason(
                        body, cooling, solve_to, max_iter)
        except Exception:                                   # noqa: BLE001
            log.debug("coupled: sine-state lookup failed", exc_info=True)
            sine_hit = None

    # ONE loop at a time.  Not politeness: the two halves iterate through
    # PROCESS-WIDE state — the last transient, its field snapshot, the thermal
    # last-result store — and two loops interleaving in it would each feed back
    # temperatures produced by the other's run.  Refused rather than queued: a
    # request that waits behind up to six transients is a request the browser
    # gives up on, having said nothing.
    if not _LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=(
            "a coupled run is already in flight on this backend — wait for it or "
            "press Stop"))
    history: List[Dict[str, Any]] = []
    converged = False
    runaway = False
    magnet_note = ""
    em: Dict[str, Any] = {}
    field: Dict[str, Any] = {}
    #: The params the LAST thermal solve was remembered under — kept so the
    #: limited mode can re-remember its rescaled snapshot as the same answer
    #: rather than leave the Thermal tab showing a state the record does not.
    field_params: Dict[str, Any] = {}
    mech_block: Optional[Dict[str, Any]] = None
    # THE BEARING TEMPERATURE, carried from one pass to the next.  `None` on the
    # first pass is not a missing value: it means "resolve it the way any other
    # run does" — the machine's own `bearings.temp_c`, or its last thermal map
    # when the assignment says `temp_source: thermal`.  From pass 2 it is the
    # shaft off the previous map, and it drives BOTH halves.  Fed back
    # UNDAMPED and outside the convergence test: it is not a temperature the
    # field depends on, so it has no gain to oscillate with — it simply follows
    # the shaft the loop is already driving to a fixed point.
    t_brg: Optional[float] = None
    brg_where = ""
    v1_pts: List[tuple] = []         # (v_phase_peak, I_solved) — the regulator's
    # THE OPERATING POINT, on a voltage-fed loop: how far the LAST pass's current
    # sat from the duty's, and whether that is outside `inverter.i_tol_pct`.
    # `None`/False on a sine loop and on a PWM run with no target — there the
    # current is imposed and the point error is 0 by construction, so neither the
    # convergence test nor the warning below can fire.
    point_err: Optional[float] = None
    point_off = False
    v1_ran: Optional[float] = None   # the fundamental the last pass really ran at
    # THE JUNCTION TEMPERATURE's own residual [K].  `None` on every drive but
    # the Controller's, and `None` on a pass whose controller could not be
    # solved — in both cases the convergence test below is the one it always
    # was, which is what "nothing else changed" has to mean.
    d_tj: Optional[float] = None
    ripple_quotable = True           # …until a pass comes back carrying DC
    dc_notes: List[str] = []
    refusal: Optional[str] = None    # a later pass's EM refusal, verbatim
    #: MACHINE-READABLE companion to `refusal`.  A consumer deciding whether a
    #: result may be saved needs to tell "the loop stopped early because a later
    #: pass would not settle" from "the loop stopped early because the machine
    #: could not be solved at all" — and prose is not the place to look.
    refusal_code: Optional[str] = None
    damping_eff = float(damping)     # the step actually taken; halves on oscillation
    prev_d: Optional[tuple] = None   # the previous (Δcoil, Δmagnet) corrections
    em_at = (t_coil, t_mag)          # what the LAST EM run was actually solved at
    try:
        _progress.start(
            total=max_iter * 2, kind="coupled",
            phase="iteration 1/%d — electromagnetic" % max_iter,
            composition="up to %d x (electromagnetic run + thermal solve)" % max_iter)
        # THE SINE STATE, REUSED — the loop below is skipped entirely and the
        # state it would have reached is the stored one (same machine, point,
        # cooling, question and build; only the drive differs).
        sine_reused: Optional[Dict[str, Any]] = None
        if sine_hit is not None:
            _sp = sine_hit.get("payload") or {}
            if isinstance(_sp.get("em"), dict) and isinstance(_sp.get("field"),
                                                              dict):
                sine_reused = {
                    "computed_at": (sine_hit.get("entry") or {}).get(
                        "computed_at"),
                    "key": (sine_hit.get("entry") or {}).get("key"),
                    "source": "run history (coupled.sine_state)"}
                em, field = _sp["em"], _sp["field"]
                t_coil = float(_sp["coil_c"])
                t_mag = None if _sp.get("magnet_c") is None else float(
                    _sp["magnet_c"])
                t_brg = _sp.get("bearing_c")
                em_at = (t_coil, t_mag)
                brg_where = str(_sp.get("brg_where") or "")
                converged = bool(_sp.get("converged"))
                limited = _sp.get("limited")
                time_to_limit = _sp.get("time_to_limit")
                magnet_note = str(_sp.get("magnet_note") or "")
                history.extend([dict(r, sine_reused=True)
                                for r in (_sp.get("history") or [])])
                log.info("coupled: sine state reused from %s (%s) — going "
                         "straight to the controller's PWM",
                         sine_reused["source"], sine_reused["computed_at"])
        for it in (range(1, max_iter + 1) if sine_reused is None else ()):
            _check_cancelled(run_id)
            _progress.update(done=(it - 1) * 2,
                             phase="iteration %d/%d — electromagnetic" % (it, max_iter))
            # THE BEARING TEMPERATURE, for the duration of this pass's EM run:
            # the summary builder reads it off the ContextVar, wherever in the
            # call tree it ends up.  `None` = "resolve it the way any other run
            # does" — the machine's own `bearings.temp_c`, or its last thermal
            # map when the assignment says `temp_source: thermal`.  Reset in a
            # `finally`, so a failed run cannot leave the next REQUEST's summary
            # billed at this loop's temperature.
            _tok = _ml.BEARING_TEMP_C.set(
                None if t_brg is None else float(t_brg))
            try:
                # THE REVERSE of the ledger-miss report (owner, 2026-09-22):
                # the FIRST pass is solved at the body's own coil/magnet
                # temperature — the identical point a plain Run at those same
                # panel fields would make.  If that Run already happened (the
                # ordinary way Coupled thermal gets switched ON: type an
                # operating point, Run it once to see it solve, then turn the
                # loop on), this pass should be served from the results
                # ledger instead of paying for the same transient twice.
                # Every later iteration keeps the door shut (`ledger=False`,
                # the module note above explains why) — only `it == 1` ever
                # asks, and `history_fresh` (this run's own "Recompute") shuts
                # it even there.
                em = _em_run(body, coil_temp_c=t_coil, magnet_temp_c=t_mag,
                             inverter=inverter, controller=ctl,
                             ledger=(it == 1 and not history_fresh))
            except HTTPException as exc:
                if not history:
                    raise               # the first pass: nothing solved to keep
                # A LATER pass refused — the machine at the temperatures the
                # previous map gave it cannot be solved (2026-09-09: the 40 mm's
                # magnets at 243.6 °C, +124 K past the card's linear model).
                # The previous pass's run and map are still a solved state and
                # ARE the answer so far; a 500 here threw both away after two
                # minutes of solving.  Stopped and said, like a runaway.
                refusal = ("electromagnetic run %d refused at coil %.1f °C / "
                           "magnet %s °C: %s — the temperatures above are the "
                           "last pass that solved"
                           % (it, float(t_coil),
                              ("—" if t_mag is None else "%.1f" % float(t_mag)),
                              _detail_text(exc)))
                refusal_code = "last_pass_em_refused"
                log.warning("coupled: %s", refusal)
                break
            finally:
                _ml.BEARING_TEMP_C.reset(_tok)
            em_at = (t_coil, t_mag)

            _check_cancelled(run_id)
            _em_map = None
            _em_src = None
            if inverter is not None:
                # THE DC MUST BE SETTLED, or nothing this run says about ripple
                # means anything (B5 / PWM study §1.9: 43-45 A of spurious DC on
                # a 433 A fundamental reported 55 % torque ripple where the
                # settled orbit reads 25 %).  The LOSSES survive a small DC and
                # the temperatures would be only slightly wrong — but a coupled
                # run's whole product is a state the report quotes, and quoting
                # half of it while suppressing the other half is worse than
                # refusing.  Checked on the FIRST pass before anything is fed
                # back, so the refusal costs one transient and not six.
                _ok, _quot, _dc, _band, _dcnote = _pwm_dc_verdict(
                    inverter, em.get("summary") or {})
                if not _ok:
                    _s = em.get("summary") or {}
                    _dc_msg = (
                        "the PWM electromagnetic run did not settle: %s A of DC "
                        "is left in phase %s of the reported period (tolerance "
                        "%s A), which is more than the %.2f A this run may be "
                        "billed on. At that level the current itself is not the "
                        "machine's — raise the settle budget "
                        "(SB_V_SETTLE_PERIODS) or the frame count."
                        % (_s.get("pwm_dc_residual_A"),
                           _s.get("pwm_dc_residual_phase"),
                           _s.get("pwm_dc_tol_A"), float(_band or 0.0)))
                    if not history:
                        # THE FIRST PASS still refuses, and must: nothing has
                        # been solved to keep, and catching it here is what
                        # makes the refusal cost one transient instead of six.
                        raise _refuse(_dc_msg, ["drive"],
                                      code="pwm_dc_unconverged")
                    # A LATER pass failed the gate — same situation as an EM
                    # refusal thirty lines up, and answered the same way
                    # (2026-09-15: a three-pass Ø200 run lost two good passes
                    # and 93 minutes because this one branch raised where its
                    # neighbour breaks).  The previous pass IS a solved state
                    # that passed this very gate; it is kept, and the run says
                    # loudly that it stopped early and why.  The temperatures
                    # reported are that pass's, exactly as for a refusal.
                    refusal = ("electromagnetic run %d left unsettled DC at "
                               "coil %.1f °C / magnet %s °C: %s — the "
                               "temperatures above are the last pass that "
                               "settled"
                               % (it, float(t_coil),
                                  ("—" if t_mag is None else "%.1f" % float(t_mag)),
                                  _dc_msg))
                    refusal_code = "last_pass_dc_unconverged"
                    log.warning("coupled: %s", refusal)
                    break
                if not _quot and _dcnote and _dcnote not in dc_notes:
                    # Recorded once, on the loop's own block: the run goes on,
                    # and the report is told which half of it it may quote.
                    dc_notes.append(_dcnote)
                    log.warning("coupled: %s", _dcnote)
                ripple_quotable = ripple_quotable and _quot
                try:
                    _em_map, _em_src = _pwm_loss_map(
                        body, em, inverter, coil_temp_c=t_coil,
                        magnet_temp_c=t_mag, controller=ctl)
                except _NoLossMap as _nm:
                    # SOLVE, don't refuse.  A run that was answered from a
                    # store left no field behind it; ONE forced solve is the
                    # honest answer, and a second miss after that is a real
                    # refusal (the machine genuinely produced no per-element
                    # map — a single frame, the conducting solve off).
                    log.warning("coupled: no loss map after the PWM run (%s) — "
                                "re-solving this pass with fresh=True", _nm.reason)
                    _progress.update(
                        phase="iteration %d/%d — electromagnetic (re-solving: "
                              "the run was answered from a store and left no "
                              "field)" % (it, max_iter))
                    _tok2 = _ml.BEARING_TEMP_C.set(
                        None if t_brg is None else float(t_brg))
                    try:
                        em = _em_run(body, coil_temp_c=t_coil,
                                     magnet_temp_c=t_mag, inverter=inverter,
                                     controller=ctl, fresh=True)
                    finally:
                        _ml.BEARING_TEMP_C.reset(_tok2)
                    try:
                        _em_map, _em_src = _pwm_loss_map(
                            body, em, inverter, coil_temp_c=t_coil,
                            magnet_temp_c=t_mag, controller=ctl)
                    except _NoLossMap as _nm2:
                        raise _refuse(
                            "the PWM electromagnetic run left no per-element "
                            "loss map the thermal half could read, and a forced "
                            "fresh solve left none either (%s). A coupled PWM "
                            "run needs field_snapshot on and the conducting "
                            "solve on both sides." % _nm2.reason,
                            ["drive"], code="pwm_no_loss_map")
            _progress.update(done=(it - 1) * 2 + 1,
                             phase="iteration %d/%d — thermal" % (it, max_iter))
            field = _thermal_solve(body, cooling, coil_temp_c=t_coil,
                                   magnet_temp_c=t_mag, rpm=rpm_eff,
                                   bearing_temp_c=t_brg,
                                   n_steps_per_period=(
                                       inverter["n_steps_per_period"]
                                       if inverter else None),
                                   em_map=_em_map, em_loss_source=_em_src,
                                   params_out=field_params)

            w = _component(field, "winding")
            m = _component(field, "magnet")
            t_coil_out = _bulk_temp(w)
            t_mag_out = _bulk_temp(m)
            t_mag_max = m.get("max")
            summary = em.get("summary") or {}
            # ── THE CYCLE, INSIDE THE PASS (2026-09-16) ─────────────────────
            # The steady map above is the machine running this point FOR EVER,
            # which on an impulse duty is not a state it is ever in.  What the
            # next electromagnetic run must be solved at is the temperature the
            # CYCLE reaches — so the model is fitted to that map, the allowable
            # regime is searched, and the pair that travels on is the pair at
            # the found duty ratio.  `_bulk_temp` above still supplies them on
            # every S1 machine, unchanged.
            if cycle_in is not None:
                _progress.update(
                    phase="iteration %d/%d — duty cycle: the allowable regime"
                          % (it, max_iter))
                try:
                    cycle_model, regime = _cycle_step(cycle_in, summary, field)
                except _cdc.DutyCycleError as exc:
                    if not history:
                        raise _cycle_refusal(exc, cycle_in["duty"])
                    # A LATER pass's cycle refused — answered exactly as a later
                    # pass's electromagnetic refusal is: the previous pass is a
                    # solved state and IS the answer so far.
                    refusal = ("the duty cycle could not be solved on pass %d "
                               "(%s) — the regime above is the last pass that "
                               "could" % (it, getattr(exc, "message", str(exc))))
                    refusal_code = "last_pass_cycle_refused"
                    log.warning("coupled: %s", refusal)
                    break
                # The SPATIAL spread of the magnets, carried from the map onto
                # the cycle peak: the node the model integrates is a mean, and
                # the demagnetisation check is about the hottest element.
                _spread = 0.0
                _mavg, _mmax = _bulk_temp(m), m.get("max")
                if _mavg is not None and _mmax is not None:
                    _spread = max(float(_mmax) - float(_mavg), 0.0)
                if regime.get("coil_temp_c") is not None:
                    t_coil_out = float(regime["coil_temp_c"])
                t_mag_out = (None if regime.get("magnet_temp_c") is None
                             else float(regime["magnet_temp_c"]))
                t_mag_max = (None if regime.get("magnet_peak_c") is None
                             else round(float(regime["magnet_peak_c"]) + _spread, 2))
                _progress.update(phase=_cdc.progress_words(
                    it, regime, t_coil, regime.get("coil_temp_c")))
            # THE MECHANICAL HALF of this pass: the temperature the friction was
            # billed at (the machine's own on pass 1, the previous map's shaft
            # after that), and what it cost.  Read off the EM summary rather than
            # recomputed here — the row must be the watts the run carries, or the
            # history and the cards would be two answers to one question.  Absent
            # on a machine with no bearings, and absent means UNKNOWN.
            _brg_used = summary.get("bearing_temp_c")
            if _brg_used is None and t_brg is not None:
                _brg_used = round(float(t_brg), 2)
            history.append({
                "iter": it,
                "T_coil_in": round(float(t_coil), 2),
                "T_magnet_in": (None if t_mag is None else round(float(t_mag), 2)),
                "T_coil_out": (None if t_coil_out is None else round(t_coil_out, 2)),
                "T_magnet_out": (None if t_mag_out is None else round(t_mag_out, 2)),
                "T_magnet_max": (None if t_mag_max is None
                                 else round(float(t_mag_max), 2)),
                "P_loss_W": summary.get("P_loss_total_W"),
                "T_em_Nm": summary.get("T_em_avg_Nm"),
                "bearing_temp_c": _brg_used,
                "bearing_temp_source": summary.get("bearing_temp_source"),
                "P_mech_extra_W": summary.get("P_mech_extra_W"),
            })
            if regime is not None:
                # WHAT THIS PASS FOUND, beside the temperatures it found it at:
                # the two numbers that make the search visible in the history
                # the panel plots and the report quotes.
                history[-1].update({
                    "ed_allowable_pct": regime.get("ed_allowable_pct"),
                    "t_on_allowable_s": regime.get("t_on_allowable_s"),
                    "cycle_limiting_part": regime.get("limiting_part"),
                })
            if inverter is not None:
                # On a VOLTAGE-fed run the current is the answer, not the
                # request: the inverter holds the fundamental and the machine
                # draws what its hot copper and its back-EMF allow.  So the row
                # says what this pass actually drew, and the DC residual beside
                # it says whether that reading is settled.
                _i_solved = em.get("I_phase_rms_solved_A")
                history[-1].update({
                    "v_phase_peak_V": round(float(inverter["v_phase_peak_V"]), 4),
                    "I_phase_rms_solved_A": _i_solved,
                    "T_ripple_pct": summary.get("T_ripple_pct"),
                    "pwm_dc_residual_A": summary.get("pwm_dc_residual_A"),
                })
                v1_ran = float(inverter["v_phase_peak_V"])
                # WHERE THIS PASS LANDED, against the duty's own current.  The
                # convergence test below reads THIS and not the temperatures
                # alone: a voltage-fed loop whose copper has stopped moving is
                # not finished if the machine is drawing 3 % off the point it is
                # billed at.
                point_err = _point_error_pct(inverter, _i_solved)
                point_off = (point_err is not None
                             and abs(point_err) > _point_tol_pct(inverter))
                if point_err is not None:
                    history[-1]["point_error_pct"] = round(point_err, 3)
                # …and RE-AIM at the duty's current for the next pass.  Applied
                # here, between the map and the next electromagnetic run, so it
                # costs nothing: the loop was going to solve again anyway.
                # THE DEVICES, on the current this pass actually drew.  It
                # costs no solve — arithmetic over a card — and the junction
                # temperature it lands on is what the NEXT electromagnetic run
                # reads R_DS(on) and V_SD at, which is the second fixed point
                # this drive closes.
                if ctl is not None:
                    # ONE MORE PHASE ON THE STRIP (Stage 2 web hook, 2026-09-22):
                    # the controller step is arithmetic over a card, not a solve,
                    # so it costs no extra done-count — but it does take a
                    # moment, and the ring said nothing about it before this.
                    _progress.update(
                        phase="iteration %d/%d — controller: device losses / T_j"
                        % (it, max_iter))
                    d_tj = ctl.step(em, it=it)
                    history[-1]["T_junction_c"] = round(float(ctl.t_j_c), 2)
                    if d_tj is not None:
                        history[-1]["d_T_junction_K"] = round(float(d_tj), 3)
                    if ctl.solve:
                        history[-1]["P_inverter_W"] = (
                            ctl.solve.get("losses") or {}).get("total_W")
                        history[-1]["eta_wall_to_shaft"] = (
                            ctl.solve.get("efficiency") or {}).get(
                                "wall_to_shaft")
                _next = _regulate_v1(inverter, _i_solved, v1_pts)
                if _next is not None:
                    log.info("coupled: re-aiming the fundamental %.3f -> %.3f V "
                             "(solved %.2f A, target %.2f A)",
                             float(inverter["v_phase_peak_V"]),
                             float(_next["v_phase_peak_V"]), float(_i_solved),
                             float(inverter["target_I_phase_rms_A"]))
                    history[-1]["v_phase_peak_next_V"] = round(
                        float(_next["v_phase_peak_V"]), 4)
                    inverter = _next
            # THE FEEDBACK: the shaft this map produced is the next pass's
            # bearing seat.  Taken from the map in hand, by the same rule the
            # stored-run summary uses on the REMEMBERED map — one definition.
            _hit = _bearing_temp(field)
            d_brg: Optional[float] = None
            if _hit is not None:
                if _brg_used is not None:
                    d_brg = float(_hit[0]) - float(_brg_used)
                history[-1]["T_bearing_out"] = round(float(_hit[0]), 2)
                t_brg, brg_where = _hit

            if t_coil_out is None:
                # A winding that meshed to nothing has no temperature to feed
                # back, and iterating on the one we started with would be a loop
                # that pretends to converge.  Stop and say so.
                magnet_note = ("the winding has no elements in the thermal mesh, "
                               "so no copper temperature could be fed back")
                break
            if t_mag_out is None and not magnet_note:
                magnet_note = ("the magnets have no elements in the thermal mesh "
                               "(or no magnet is assigned): magnet_temp_c was "
                               "held at its starting value")

            d_coil = float(t_coil_out) - float(t_coil)
            d_mag = (0.0 if (t_mag is None or t_mag_out is None)
                     else float(t_mag_out) - float(t_mag))
            # ── THE LIMITS, WHEN THAT IS THE QUESTION (owner 2026-09-18) ────
            # *«или считать до конца стабилизации температуры, или считать до
            # лимитов»*.  In `limits` mode the loop must NOT keep iterating
            # towards a steady state the machine is never allowed to reach: as
            # soon as a pass's own map puts a part past its limit, the step
            # response of THAT map is integrated and — if it really does cross —
            # the loop stops here and the answer becomes the machine at the
            # crossing.  Four ODE nodes against a two-minute transient.
            #
            # IT STOPS ONLY ON A REAL CROSSING.  A map over a limit whose
            # transient settles UNDER it is over it for a reason these four
            # nodes do not represent, and then the steady state IS the answer —
            # so the loop goes on iterating towards it exactly as in `steady`.
            #
            # NOT on an impulse duty (`regime is not None`): the cycle search is
            # already the "how long may it be pulled" answer for that duty, and
            # two answers to one question is worse than one.  NOT on an errand
            # (`record: false`), which is one pass by contract.
            if (_loop_solve_to == "limits" and regime is None
                    and not _rr.suppressed()):
                _progress.update(
                    phase="iteration %d/%d — how long until the limit"
                          % (it, max_iter))
                _ttl_now = None
                try:
                    _ttl_now = _ttl_step(
                        body, cooling, summary, field,
                        bearing_temp_c=summary.get("bearing_temp_c"),
                        runaway=False)
                except Exception:  # noqa: BLE001 — never fails a solved pass
                    log.debug("coupled: the limit check could not be made",
                              exc_info=True)
                if (_ttl_now and not _ttl_now.get("within_limits", True)
                        and _ttl.limiting(_ttl_now)):
                    time_to_limit = _ttl_now
                    limited_stop = True
                    log.info("coupled: solve_to=limits — pass %d is already "
                             "past a limit, so the loop stops here instead of "
                             "iterating towards a state the machine cannot "
                             "hold; %s", it, _ttl.limited_line(_ttl_now))
                    break
            # THE RUNAWAY GUARD, judged on what the machine actually reaches.
            # On an impulse duty that is the CYCLE's peak — the steady map above
            # it is routinely past this ceiling and says nothing about whether
            # the cycle has an equilibrium (it has one: the search found the
            # ratio at which it does, or said there is none).
            _hot_pair = ((regime.get("winding_hot_peak_c"),
                          regime.get("magnet_peak_c")) if regime is not None
                         else (t_coil_out, t_mag_max))
            hottest = max([x for x in _hot_pair if x is not None], default=0.0)
            if float(hottest) > runaway_c:
                runaway = True
                break                       # no equilibrium to converge TO
            # THE BEARING SEAT is the third temperature this loop closes on
            # (reviewer 2026-09-13: a loop that "converged" on pass 1 had
            # billed its bearings at the assigned 70 °C while its own map put
            # the shaft at 149 °C — 1 478 W of friction where the settled
            # answer is about 1 000 W).  A machine with no bearings has no
            # d_brg and is judged on the copper and the magnets as before.
            # …and on a VOLTAGE-fed loop the OPERATING POINT is the fourth
            # (2026-09-15, CIANO10 200 opt / L155 'rated 1x9 mm'): pass 1 landed
            # at +0.17 %, pass 2 drifted to −3.16 % as the winding heated, and
            # the loop stopped there because the three temperature residuals were
            # inside tol — so the regulator never got the pass it needed and the
            # record said `on_point: false`.  Temperatures settling is necessary,
            # not sufficient: a machine 3 % off the current it is billed at is a
            # different machine.  Sine/current drive is untouched — `point_off`
            # is False there, the current being imposed rather than answered.
            # …and on an IMPULSE duty the REGIME is the fifth (2026-09-16): the
            # pair being compared is the pair at a duty ratio that is itself
            # being searched, so a loop whose copper has stopped moving while
            # its ED is still walking has not converged on anything.  The band
            # is one percentage point (`coupled_duty_cycle.ED_TOL_PCT`), which
            # on a 60 s cycle is 0.6 s of on-time.
            # …and on the CONTROLLER's bridge the JUNCTION TEMPERATURE is the
            # sixth (2026-09-22): the devices' own losses set it, it moves
            # R_DS(on) by ~0.6 %/K and V_SD with it, and those move the waveform
            # the machine is fed — so a loop whose copper has settled while its
            # silicon is still climbing has not closed the excitation.
            if (abs(d_coil) < tol and abs(d_mag) < tol
                    and (d_brg is None or abs(d_brg) < BEARING_TOL_K)
                    and (d_tj is None or abs(d_tj) < CONTROLLER_TJ_TOL_K)
                    and not point_off
                    and (regime is None
                         or _cdc.regime_settled(prev_regime, regime))):
                converged = True
                break
            prev_regime = regime
            if it >= max_iter:
                break                       # out of budget — see the note below
            # (f) THE UPDATE IS APPLIED ONLY WHEN ANOTHER ITERATION WILL USE IT.
            # Every iteration BEGINS with the electromagnetic run, so the pair
            # this loop reports is always the pair the last run — and the last
            # thermal map beside it — was actually solved at.  Moving the report
            # to the damped estimate on the way out would put the headline one
            # step ahead of the cards underneath it, and buying that back would
            # cost a whole extra transient at a temperature no thermal solve has
            # ever answered: a reported temperature with no map, which is worse
            # than an honest residual.  `residual_coil_K` / `residual_magnet_K`
            # say how far the answer still was; `converged` says whether that is
            # inside `tol_K`.
            # ADAPTIVE step: plain substitution until the corrections misbehave.
            # A correction that GROWS or FLIPS SIGN against the previous one is
            # an iteration with gain near or above 1; from then on the step is
            # halved (and stays halved — a loop that oscillated once is not
            # trusted with a full step again).  A caller's explicit `damping`
            # below 1 is honoured as the ceiling either way.
            if prev_d is not None:
                for d_new, d_old in ((d_coil, prev_d[0]), (d_mag, prev_d[1])):
                    if d_old and (d_new * d_old < 0.0 or abs(d_new) > abs(d_old)):
                        damping_eff = min(damping_eff, DAMPING_ON_OSCILLATION)
            prev_d = (d_coil, d_mag)
            history[-1]["damping_used"] = round(float(damping_eff), 3)
            # ROUNDED to the panel's own precision (owner, 2026-09-22: "зачем
            # он ещё пересчитывает... если во время каплинга он уже считал").
            # `adoptConvergedTemperatures` (web/coupledApi.ts) writes this
            # exact 1-decimal number into the Simulation tab's coil/magnet
            # temperature fields, and the tab's next Run sends it straight
            # back — so the EM ledger key (`round(coil_temp_c, 1)` /
            # `round(magnet_temp_c, 1)` in routes/simulation.py) can only
            # equal the key this pass writes under if the pass was SOLVED at
            # the same 1-decimal number the panel will show, not at the raw
            # damped-update float (189.95 rounds to 189.9 in Python's
            # round-half-to-even but to 190.0 in JS's Math.round — a solved
            # pass at the unrounded value made every later key miss on that
            # boundary alone).  0.05 °C of truncation is far inside `tol` and
            # changes no physics; solving AT the number the panel will
            # actually show is what lets that later Run be served from here
            # instead of re-solved from scratch.
            t_coil = round(float(t_coil) + damping_eff * d_coil, 1)
            if t_mag is not None and t_mag_out is not None:
                t_mag = round(float(t_mag) + damping_eff * d_mag, 1)
        # THE BUDGET RAN OUT WITH THE POINT STILL OFF.  The last pass is a solved
        # state and is KEPT — the temperatures, the map and the mechanics all
        # belong to it — but it is not the duty's operating point, and a reader
        # must not have to divide two numbers in the record to find that out.
        # Said as a warning with its own code, like every other "stopped early"
        # here.  Never over a refusal or a runaway: those stopped the loop for a
        # harder reason and own the message.
        # …and NEVER over a deliberate stop at a limit (2026-09-18): that loop
        # did not run out of budget, it was asked to stop, and the pass it makes
        # at the limit re-aims at this duty's current anyway.
        if point_off and refusal is None and not runaway and not limited_stop:
            # OUT OF INVERTER, OR OUT OF ITERATIONS?  Two different answers and
            # only one of them is fixed by raising max_iter.  The last pass ran
            # AT the ceiling — `v1_ran` is the fundamental it was actually solved
            # at — when the regulator had nowhere left to aim: the bridge cannot
            # build the volts this point needs on this link, and the current that
            # pass drew is the most the machine will ever draw here.  Said with
            # its own code, so a consumer can stop offering "more iterations".
            _cap_v = 0.0
            try:
                _cap_v = float((inverter or {}).get("v_phase_peak_max_V") or 0.0)
            except (TypeError, ValueError):
                _cap_v = 0.0
            _i_last = (history[-1].get("I_phase_rms_solved_A")
                       if history else None)
            if (_cap_v > 0.0 and v1_ran is not None
                    and float(v1_ran) >= _cap_v - 1e-6):
                from motor_ai_sim.simulation.pwm import (
                    MAX_MODULATION_INDEX as _MAX_M_MSG)
                refusal_code = "point_limited_by_modulation"
                refusal = (
                    "the point is out of INVERTER, not out of iterations: the "
                    "largest fundamental a %.1f V link can build at %d carriers "
                    "per electrical period is %.2f V peak (m = %.2f once the "
                    "modulator's sampled-reference gain is compensated; %.1f V "
                    "before it), the last pass ran there, and it drew %s A "
                    "against the %.2f A this duty is billed at (%+.2f %%) — "
                    "raise inverter.v_dc_V or the carrier, or bill this duty at "
                    "the current the bridge can reach"
                    % (float((inverter or {}).get("v_dc_V") or 0.0),
                       int((inverter or {}).get("carriers_per_period") or 0),
                       _cap_v, _MAX_M_MSG,
                       float((inverter or {}).get(
                           "v_phase_peak_max_uncompensated_V") or 0.0),
                       ("—" if _i_last is None else "%.2f" % float(_i_last)),
                       float((inverter or {}).get("target_I_phase_rms_A") or 0.0),
                       float(point_err or 0.0)))
                log.warning("coupled: %s", refusal)
            else:
                refusal_code = "point_not_converged"
                refusal = (
                    "the temperatures settled but the operating point did "
                    "not: after %d electromagnetic run(s) the machine draws "
                    "%+.2f %% off the %.2f A this duty is billed at "
                    "(tolerance ±%g %%)%s — raise max_iter, or widen "
                    "inverter.i_tol_pct if that miss is acceptable"
                    % (len(history), float(point_err or 0.0),
                       float((inverter or {}).get(
                           "target_I_phase_rms_A") or 0.0),
                       _point_tol_pct(inverter),
                       ("" if v1_ran is None
                        or abs(float(inverter["v_phase_peak_V"])
                               - v1_ran) < 1e-9
                        else "; the next pass would have been aimed at "
                             "%.3f V" % float(inverter["v_phase_peak_V"]))))
                log.warning("coupled: %s", refusal)
        # ── THE FOUND REGIME, at the resolution the record keeps ────────────
        # The loop searched at the coarse resolution because it was going to
        # search again next pass; what is STORED — the cycle the report draws,
        # the winding-peak-vs-ED curve, the allowable ratio over a span of
        # periods and the two S2 times — is solved once, here, on the model the
        # last pass was fitted with.  A runaway has no regime to report: the
        # cycle peak passed the ceiling, which the loop already says.
        if cycle_model is not None and regime is not None and not runaway:
            _check_cancelled(run_id)
            _progress.update(phase="duty cycle — the allowable regime, at full "
                                   "resolution")
            try:
                regime = _cdc.solve_regime(
                    cycle_model, samples_per_segment=_cdc.FINAL_SAMPLES,
                    with_curve=True, cycle_lengths=_cycle_lengths(body))
                if (cycle_in and cycle_in.get("rated_duty")
                        and regime.get("s2_from_rated_s") is not None):
                    regime["s2_from_rated_duty"] = cycle_in["rated_duty"]
                cycle_doc = _cdc.cycle_record(cycle_model, regime)
            except _cdc.DutyCycleError as exc:
                # The loop's own answer stands: the pass that produced it solved
                # the same search at the coarser resolution and converged on it.
                log.warning("coupled: the final regime could not be re-solved "
                            "at full resolution (%s) — keeping the last pass's",
                            exc)
        # ── HOW LONG MAY IT RUN (owner 2026-09-17) ──────────────────────────
        # The loop has finished; if the point it finished at is past any limit
        # this machine states, the same network the duty cycle uses is fitted to
        # the map that last pass solved and the step response is integrated from
        # cold and from rated.  Done HERE — inside the lock, beside the map it is
        # fitted to — so a second coupled run cannot start between the map and
        # the answer that belongs to it, and so the phase is visible on the bar.
        # It costs four ODE nodes against the two minutes of finite elements
        # above it, and it never fails a run: `_ttl_step` returns None instead.
        if history and field and time_to_limit is None:
            _check_cancelled(run_id)
            _progress.update(phase="how long until the limit — the step "
                                   "response of this point")
            try:
                time_to_limit = _ttl_step(
                    body, cooling, (em.get("summary") or {}), field,
                    bearing_temp_c=((em.get("summary") or {})
                                    .get("bearing_temp_c")),
                    runaway=bool(runaway))
            except Exception:  # noqa: BLE001 — never fails a solved run
                log.debug("coupled: the time to the limit was not computed",
                          exc_info=True)
        if time_to_limit and not time_to_limit.get("within_limits", True):
            log.warning("coupled: %s", time_to_limit.get("note") or "")
        elif time_to_limit:
            log.info("coupled: every part with a stated limit (%s) is "
                     "inside it at this point",
                     ", ".join(time_to_limit.get("judged") or ()) or "none")
        # ── AND THE MACHINE AT THAT MOMENT (owner 2026-09-18) ───────────────
        # *«будем ставить максимальные значения этих лимитов и делать вычисление
        # для них… то есть состояние мотора в работе 24 секунды при заданной
        # мощности»* — and (addendum) at the cooling this duty was solved with.
        #
        # ONE extra electromagnetic pass, AT THE LIMIT, so torque, the four
        # loss classes, R, KV/Kt/Km, the demagnetisation check, the voltages
        # and the ripple are those of the machine at that instant instead of
        # those of a steady state it never reaches.  "At the limit" is literal
        # (owner 2026-09-18, on the live site: *«так и расчёт тогда должен быть
        # при катушках в 200 градусов, а не 184»*): each part is solved at the
        # temperature its limit is judged on — the winding hot spot, the
        # hottest magnet element — and the limiting part exactly AT its limit,
        # never at the node mean the network integrates (`_limited_block`
        # states the rule; `em_pass_at` records the two numbers).  The thermal
        # map is the last solved one TRANSLATED onto the node temperatures of
        # the same instant — the field-shape-frozen assumption the
        # time-to-limit model already states — so its hot spot IS that limit
        # and the pictures and the tables are one state.  The loop is NOT
        # re-entered: one pass, and the temperatures it is made at are the
        # answer by construction.
        if (_loop_solve_to == "limits" and history and field and regime is None
                and not _rr.suppressed() and sine_reused is None):
            limited = _limited_block(
                time_to_limit, cooling=cooling, field=field,
                passes=len(history), steady_converged=bool(converged),
                steady_runaway=bool(runaway))
        if limited:
            from motor_ai_sim.routes import thermal as _th
            _check_cancelled(run_id)
            # THE NODE MEANS of the crossing — what the map is translated onto.
            _t_at = limited["temperatures_at_limit"]
            # …AND THE TEMPERATURES THE PASS IS SOLVED AT: the winding at its
            # limit (or its hot spot, when another part limits), the magnets at
            # their hottest element (or their limit).  Never the node means —
            # a "winding at 200 °C" solved with 183.5 °C copper is a machine
            # 16 K colder than the sentence beside it.
            _ep = limited["em_pass_at"]
            _t_c = float(_ep["coil_c"] if _ep.get("coil_c") is not None
                         else _t_at["winding"])
            _t_m = (_ep.get("magnet_c") if _ep.get("magnet_c") is not None
                    else _t_at.get("magnet"))
            _t_m = None if (t_mag is None or _t_m is None) else float(_t_m)
            # ROUNDED to the panel's own 1-decimal precision — same reason as
            # the damped-update rounding above: this pass becomes the record,
            # and the record's temperatures are what the Simulation tab's
            # coil/magnet fields auto-set to and replay on its next Run.
            # Solving at the panel's own number (not a finer one nobody will
            # ever ask for again) is what lets that Run hit the ledger.
            _t_c = round(_t_c, 1)
            _t_m = None if _t_m is None else round(_t_m, 1)
            _ep["coil_c"], _ep["magnet_c"] = _t_c, _t_m
            _t_b = limited.get("bearing_seat_at_limit_c")
            _progress.update(
                phase="final pass at the limit — the machine after %s"
                      % limited["t_cold_words"])
            log.info("coupled: final pass at the limit — coil %.1f degC (%s), "
                     "magnet %s degC (%s); node means winding %.1f / magnet "
                     "%s; %s", _t_c, _ep["coil_basis"],
                     "n/a" if _t_m is None else "%.1f" % _t_m,
                     _ep["magnet_basis"], float(_t_at["winding"]),
                     "n/a" if _t_at.get("magnet") is None
                     else "%.1f" % float(_t_at["magnet"]), limited["line"])
            _tok = _ml.BEARING_TEMP_C.set(None if _t_b is None else float(_t_b))
            # THE CONTROLLER'S BRIDGE, on this pass too (2026-09-25).  Without
            # `controller=` `_em_run` falls back to the IDEAL two-level bridge,
            # so an inverter duty's "machine at the limit" was solved with no
            # device drops and no dead time while its record still printed the
            # loop's T_j beside it.  The drop is the one the loop converged on.
            try:
                _em_lim = _em_run(body, coil_temp_c=_t_c, magnet_temp_c=_t_m,
                                  inverter=inverter, controller=ctl)
            except HTTPException as exc:
                # The machine could not be solved AT its limit.  The steady
                # answer above is a solved state and stays — said loudly, with
                # its own code, exactly as a late pass's refusal is.
                refusal = ("the final pass at the limit was refused at coil "
                           "%.1f °C / magnet %s °C: %s — the temperatures "
                           "above are the loop's own last pass, not the state "
                           "at the limit"
                           % (_t_c, ("—" if _t_m is None else "%.1f" % _t_m),
                              _detail_text(exc)))
                refusal_code = "limited_pass_refused"
                log.warning("coupled: %s", refusal)
                limited = None
                _em_lim = None
            finally:
                _ml.BEARING_TEMP_C.reset(_tok)
            if limited and _em_lim:
                em = _em_lim
                em_at = (_t_c, _t_m)
                t_coil, t_mag = _t_c, _t_m
                _s_lim = em.get("summary") or {}
                field = _th.rescale_map_to_nodes(
                    field, _t_at,
                    note=("the machine %s: the last solved map translated onto "
                          "the node temperatures of that instant, its shape "
                          "frozen at the solved map's — NOT a transient field "
                          "solve" % limited["line"].split(" — ")[0].lower()))
                if _t_b is not None:
                    t_brg = float(_t_b)
                # …AND THE TAB MUST SHOW THE SAME MACHINE.  Without this the
                # Thermal tab (and `/api/thermal/last`, and the per-duty map the
                # report draws) keeps the map the loop solved on its way here —
                # the L13 peak's 409 °C steady state — beside a record that says
                # the winding is at 200 °C.  Seen live on emotres.com,
                # 2026-09-18.  The snapshot is remembered under the very same
                # params, so it REPLACES that entry instead of growing a second
                # one for the same solve.
                try:
                    if field_params:
                        _th._remember_last("field", field, dict(field_params),
                                           field.get("geometry_fingerprint"))
                except Exception:  # noqa: BLE001 — a memory never fails a solve
                    log.debug("coupled: the snapshot map was not remembered",
                              exc_info=True)
                history.append({
                    "iter": len(history) + 1,
                    # WHAT THIS ROW IS: not another step of the loop but the one
                    # pass made AT the limit, so a chart of the history does not
                    # read it as a residual that jumped.
                    "phase": "limit",
                    "T_coil_in": round(_t_c, 2),
                    "T_magnet_in": (None if _t_m is None else round(_t_m, 2)),
                    "T_coil_out": None, "T_magnet_out": None,
                    "T_magnet_max": limited.get("at_limit_c", {}).get("magnet"),
                    "P_loss_W": _s_lim.get("P_loss_total_W"),
                    "T_em_Nm": _s_lim.get("T_em_avg_Nm"),
                    "bearing_temp_c": _s_lim.get("bearing_temp_c"),
                    "bearing_temp_source": _s_lim.get("bearing_temp_source"),
                    "P_mech_extra_W": _s_lim.get("P_mech_extra_W"),
                })
                if inverter is not None:
                    # THE POINT IS HELD FOR THIS PASS TOO: the same inverter the
                    # regulator had aimed at this duty's current drives it, and
                    # how far the colder machine then landed is RECORDED rather
                    # than regulated away — a second re-aim would be a second
                    # pass, and there is exactly one.
                    _i_lim = em.get("I_phase_rms_solved_A")
                    _pe = _point_error_pct(inverter, _i_lim)
                    history[-1].update({
                        "v_phase_peak_V": round(
                            float(inverter["v_phase_peak_V"]), 4),
                        "I_phase_rms_solved_A": _i_lim,
                        "T_ripple_pct": _s_lim.get("T_ripple_pct"),
                        "pwm_dc_residual_A": _s_lim.get("pwm_dc_residual_A"),
                        **({"point_error_pct": round(_pe, 3)}
                           if _pe is not None else {}),
                    })
                    v1_ran = float(inverter["v_phase_peak_V"])
                    # …AND THE DEVICES AT THIS PASS'S CURRENT: the controller is
                    # re-solved on the limit pass itself (arithmetic over the
                    # card, no extra EM run), so the record's controller block —
                    # losses, T_j, eta_inv — is this machine's, not the
                    # loop's last pass's.  T_j is NOT iterated here: the pass is
                    # one by contract, the fundamental is the loop's, and the
                    # current moves only as far as the colder/hotter copper
                    # moves it; the step it would still take is recorded.
                    if ctl is not None:
                        _d_tj_lim = ctl.step(em, it=len(history), phase="limit")
                        history[-1]["T_junction_c"] = round(float(ctl.t_j_c), 2)
                        if _d_tj_lim is not None:
                            history[-1]["d_T_junction_K"] = round(
                                float(_d_tj_lim), 3)
                            limited["controller_t_j_residual_K"] = round(
                                float(_d_tj_lim), 3)
                        if ctl.solve:
                            history[-1]["P_inverter_W"] = (
                                ctl.solve.get("losses") or {}).get("total_W")
                            history[-1]["eta_wall_to_shaft"] = (
                                ctl.solve.get("efficiency") or {}).get(
                                    "wall_to_shaft")
                    limited["drive_held"] = ("inverter" if ctl is not None
                                             else "pwm")
                    limited["v_phase_peak_V"] = round(
                        float(inverter["v_phase_peak_V"]), 4)
                    limited["I_phase_rms_solved_A"] = _i_lim
                    if _pe is not None:
                        limited["point_error_pct"] = round(_pe, 3)
                        point_err, point_off = _pe, False
                else:
                    limited["drive_held"] = "sine"
                limited["em_run"] = True
        # ── THE CONTINUOUS (S1) RATING, when that is the question ───────────
        # Owner 2026-09-21: a third option on the ``solve_to`` selector, beside
        # ``steady`` and ``limits``.  This is not a second loop: it is one more
        # thing said about the pass the loop already made — the converged
        # steady state, or the machine at the limit found above — using this
        # duty's OWN cooling with no patch, and the already-existing
        # ``coupled_continuous_rating`` machinery
        # (``POST /api/coupled/continuous_rating``) for exactly one condition.
        # `regime is None` for the reason the limit search above states: an
        # impulse duty's S2/S3 search already answers "how long may it be
        # pulled", and a continuous rating beside it would answer a question
        # this duty does not ask.
        continuous_rating: Optional[Dict[str, Any]] = None
        if sine_reused is not None:
            _cr0 = (sine_hit.get("payload") or {}).get("continuous_rating")
            continuous_rating = dict(_cr0) if isinstance(_cr0, dict) else None
        if (solve_to == "continuous" and history and field and regime is None
                and not _rr.suppressed() and sine_reused is None):
            _check_cancelled(run_id)
            try:
                from motor_ai_sim.duty_results import active_context as _dnc
                _dn_ctx = _dnc()
                duty_name = str(_dn_ctx[2]) if _dn_ctx else "this run"
            except Exception:  # noqa: BLE001 — a context read never fails
                duty_name = "this run"
            _progress.update(phase="continuous rating — this duty's own "
                                   "cooling, from the pass just solved")
            try:
                continuous_rating = _continuous_rating_for_loop(
                    body, cooling=cooling, rpm=rpm_eff,
                    coil_temp_c=t_coil, magnet_temp_c=t_mag,
                    duty=duty_name, mode=str(body.get("mode") or "motor"),
                    time_to_limit=time_to_limit)
            except Exception:  # noqa: BLE001 — never fails the loop's own answer
                log.debug("coupled: the continuous rating could not be found",
                          exc_info=True)
            # ── CONFIRM IT WITH A REAL EM PASS (owner 2026-09-21) ────────────
            # *«почему сразу не пересчитывается электромагнитное моделирование
            # для найденного непрерывного режима — токи не совпадают»*.  The
            # network's own answer is an ESTIMATE (a four-node fit); the record
            # — and every tile downstream of `em` / `field` — must be the
            # REAL machine at that current, exactly as `limits` mode makes the
            # record the real machine AT the limit rather than the network's
            # step-response estimate of it.
            if (continuous_rating and continuous_rating.get("ok")
                    and continuous_rating.get("feasible") is not False
                    and continuous_rating.get("I_cont_A_rms") is not None
                    and continuous_rating.get("limiting_part")):
                i_est = float(continuous_rating["I_cont_A_rms"])
                part = str(continuous_rating["limiting_part"])
                lim_c = _ccr._num((continuous_rating.get("limits_c") or {})
                                  .get(part))
                # THE SETPOINT'S OWN STORY, kept — the "runs 41 s" answer is
                # this duty's own question and stays available, printed by the
                # AT THE LIMIT block above; only the record's OWN machine
                # (em / field / temperatures) moves to the S1 point.
                # `False` until a verification pass actually REPLACES em /
                # field / temperatures below with the S1 machine — the flag a
                # reader (the panel, `coupledStateLine`) checks before trusting
                # that the record's own numbers are the rating's rather than
                # the setpoint's.
                continuous_rating["record_is_s1"] = False
                continuous_rating["duty_point"] = {
                    "I_phase_rms_A": _f(body, "I_phase_rms", 0.0),
                    "T_em_Nm": (last_row := (history[-1] if history else {}))
                              .get("T_em_Nm"),
                    "verdict": (limited["line"] if limited
                               else (time_to_limit.get("note")
                                     if time_to_limit else None)),
                }
                continuous_rating["I_estimated_A_rms"] = round(i_est, 3)
                if lim_c is None:
                    continuous_rating["verified"] = False
                    continuous_rating["note"] = (
                        "no card limit for %r, so the estimate could not be "
                        "verified" % part)
                else:
                    # ROUNDED to the panel's own 1-decimal precision — same
                    # reason as the two roundings above: a verified S1 pass
                    # REPLACES the record (below, `record_is_s1 = True`), and
                    # `adoptConvergedTemperatures` will write this exact
                    # number into the Simulation tab.  Guessing at the
                    # network fit's raw float and only rounding for DISPLAY
                    # would solve a pass under a key the panel's own Run can
                    # never reproduce.
                    _cr_temps = continuous_rating.get("temperatures_c") or {}
                    _coil_guess = round(
                        float(_cr_temps.get("winding") or t_coil), 1)
                    _mag_guess = _cr_temps.get("magnet")
                    _mag_guess = (None if _mag_guess is None
                                 else round(float(_mag_guess), 1))
                    try:
                        v = _s1_verify(
                            body, cooling=cooling, rpm=rpm_eff,
                            inverter=inverter, i_estimate=i_est,
                            coil_temp_c_guess=_coil_guess,
                            magnet_temp_c_guess=_mag_guess,
                            limiting_part=part, limit_c=float(lim_c),
                            controller=ctl)
                    except HTTPException as exc:
                        v = None
                        continuous_rating["verified"] = False
                        continuous_rating["note"] = (
                            "the verification pass could not be solved at "
                            "%.1f A (%s) — the current below is the network's "
                            "own estimate, not confirmed"
                            % (i_est, _detail_text(exc)))
                    except Exception:  # noqa: BLE001 — never fails the loop
                        v = None
                        log.debug("coupled: S1 verification failed",
                                  exc_info=True)
                        continuous_rating["verified"] = False
                    if v:
                        continuous_rating["verified"] = bool(v.get("verified"))
                        continuous_rating["verification_passes"] = v.get("passes")
                        continuous_rating["miss_K"] = v.get("miss_K")
                        if v.get("note"):
                            continuous_rating["note"] = v["note"]
                        continuous_rating["I_cont_A_rms"] = v.get(
                            "I_cont_A_rms", i_est)
                        continuous_rating["temperatures_c"] = dict(
                            continuous_rating.get("temperatures_c") or {})
                        if v.get("actual_c") is not None:
                            continuous_rating["temperatures_c"][part] = round(
                                float(v["actual_c"]), 2)
                        # THE REAL FEM TORQUE AND POWER — no longer the linear
                        # estimate: this pass really was solved.
                        from motor_ai_sim.report import shaft_view
                        _s_v = v["em"].get("summary") or {}
                        _t_v, _t_note_v = _shaft_torque_nm(_s_v)
                        _x_v = _ccr._num(_s_v.get("P_mech_extra_W"))
                        _brg_v = ({"has_bearings": True,
                                  "P_mech_extra_W": _x_v}
                                 if _x_v is not None else None)
                        _sv_v = shaft_view(_s_v, _brg_v,
                                           str(body.get("mode") or "motor"))
                        continuous_rating["power"] = {
                            "T_em_Nm": _t_v, "P_mech_W": _sv_v.get("P_mech_W"),
                            "P_rotor_W": _sv_v.get("P_rotor_W"),
                            "P_shaft_W": _sv_v.get("P_shaft_W"),
                            "eta_em": _sv_v.get("eta_em"),
                            "eta_shaft": _sv_v.get("eta_shaft"),
                            "basis": ("real electromagnetic pass at the "
                                     "verified S1 current — report.shaft_view, "
                                     "not the linear estimate"),
                        }
                        # THE RECORD BECOMES THE S1 MACHINE (owner's rule): the
                        # same replacement `limits` mode makes for the setpoint,
                        # made here for the rating instead.
                        em = v["em"]
                        field = v["field"]
                        t_coil = v["coil_temp_c"]
                        t_mag = v["magnet_temp_c"]
                        em_at = (t_coil, t_mag)
                        history.append({
                            "iter": len(history) + 1, "phase": "s1_verify",
                            "T_coil_in": round(float(t_coil), 2),
                            "T_magnet_in": (None if t_mag is None
                                           else round(float(t_mag), 2)),
                            "T_coil_out": None, "T_magnet_out": None,
                            "T_magnet_max": None,
                            "P_loss_W": _s_v.get("P_loss_total_W"),
                            "T_em_Nm": _s_v.get("T_em_avg_Nm"),
                            "bearing_temp_c": _s_v.get("bearing_temp_c"),
                            "bearing_temp_source": _s_v.get(
                                "bearing_temp_source"),
                            "P_mech_extra_W": _s_v.get("P_mech_extra_W"),
                        })
                        if ctl is not None:
                            # The devices were re-solved on THIS pass (see
                            # `_s1_verify`), so the history row and the
                            # rating say which T_j / inverter watts go with
                            # the S1 machine.
                            history[-1].update({
                                "I_phase_rms_solved_A": v.get("I_cont_A_rms"),
                                "T_junction_c": v.get("controller_t_j_c"),
                                "d_T_junction_K": v.get(
                                    "controller_t_j_residual_K"),
                                "P_inverter_W": v.get(
                                    "controller_P_inverter_W"),
                            })
                            continuous_rating["controller"] = {
                                "t_j_c": v.get("controller_t_j_c"),
                                "t_j_residual_K": v.get(
                                    "controller_t_j_residual_K"),
                                "P_inverter_W": v.get(
                                    "controller_P_inverter_W"),
                                "basis": ("the controller re-solved on the S1 "
                                          "verification pass itself, at its "
                                          "own current"),
                            }
                        continuous_rating["record_is_s1"] = True
                        # …AND THE SETPOINT'S OWN "AT THE LIMIT" LINE MUST STOP
                        # CLAIMING TO DESCRIBE THE NUMBERS BELOW IT (owner
                        # 2026-09-21, third round) — those numbers are now the
                        # S1 machine, not the setpoint's crossing.
                        if limited:
                            _i_duty = _ccr._num(
                                (continuous_rating.get("duty_point") or {})
                                .get("I_phase_rms_A"))
                            _new_line = _setpoint_only_limited_line(
                                _i_duty, time_to_limit)
                            if _new_line:
                                limited["line"] = _new_line
                                limited["note"] = (
                                    _new_line + " — this is the SETPOINT's own "
                                    "question; the temperatures, torque and "
                                    "power below are the S1 machine the "
                                    "continuous rating verified, not this "
                                    "crossing.")
            if continuous_rating:
                log.info(
                    "coupled: continuous rating — %s",
                    continuous_rating.get("headline")
                    or (continuous_rating.get("refusal") or {}).get("error")
                    or "")
        # ── THE SINE STATE, FILED FOR REUSE (2026-09-25) ────────────────────
        # Whenever the loop ran on the sine (a sine run, or the sine phase of
        # an inverter `final_pass` run) and reached an answer, the state is
        # filed under the drive-independent key, so the next inverter run of
        # the same machine/point/cooling goes straight to the PWM.
        if (sine_key is not None and sine_reused is None and inverter is None
                and history and em and field and not runaway
                and refusal is None and not _rr.suppressed()):
            try:
                _SINE_STATE_HISTORY.put(
                    sine_key,
                    params=_sine_state_canonical(body, cooling, solve_to,
                                                 max_iter),
                    summary=("sine state: coil %.1f °C, %s" % (
                        float(em_at[0]), solve_to)),
                    payload={"em": em, "field": field,
                             "coil_c": float(em_at[0]),
                             "magnet_c": (None if em_at[1] is None
                                          else float(em_at[1])),
                             "bearing_c": t_brg, "brg_where": brg_where,
                             "converged": bool(converged),
                             "limited": limited, "time_to_limit": time_to_limit,
                             "continuous_rating": continuous_rating,
                             "magnet_note": magnet_note,
                             "history": list(history)})
            except Exception:                               # noqa: BLE001
                log.warning("coupled: the sine state was not filed for reuse",
                            exc_info=True)
        # ── THE CONTROLLER'S PWM, ON THE CONVERGED SINE STATE (2026-09-25) ──
        # See `_pwm_final_passes`.  The sine state becomes the reference
        # column of `sine_comparison`; the reported machine is the PWM one.
        pwm_final_blk: Optional[Dict[str, Any]] = None
        sine_cmp: Optional[Dict[str, Any]] = None
        if (final_pass and inv_final is not None and ctl_final is not None
                and history and em and field and not runaway):
            _t0p = time.time()
            _check_cancelled(run_id)
            _cr_s1 = bool((continuous_rating or {}).get("record_is_s1"))
            _state_phase = ("s1_verify" if _cr_s1
                            else "limit" if (limited and limited.get("em_run"))
                            else "loop")
            sine_em, sine_temps, sine_brg = em, (
                float(em_at[0]),
                None if em_at[1] is None else float(em_at[1])), t_brg
            _i_body_set = _f(body, "I_phase_rms", 0.0)
            _i_tgt_set = (float(inv_final.get("target_I_phase_rms_A") or 0.0)
                          or _i_body_set)
            if _cr_s1:
                _i_s1 = float(continuous_rating["I_cont_A_rms"])
                _ratio = (_i_s1 / _i_body_set) if _i_body_set > 0 else 1.0
                _i_body, _i_tgt = _i_s1, _i_tgt_set * _ratio
            else:
                _i_body, _i_tgt = _i_body_set, _i_tgt_set
            fin = _pwm_final_passes(
                body, cooling=cooling, rpm=rpm_eff, inverter=inv_final,
                ctl=ctl_final, sine_em=sine_em, coil_c=sine_temps[0],
                magnet_c=sine_temps[1], bearing_c=sine_brg, i_body=_i_body,
                i_target=_i_tgt, tol=tol,
                adjust_temps=(_state_phase != "limit"),
                field_params=field_params, run_id=run_id)
            pwm_final_blk = _pwm_final_block(
                fin, sine_coil_c=sine_temps[0], sine_magnet_c=sine_temps[1],
                sine_bearing_c=sine_brg, tol=tol, t_wall_s=time.time() - _t0p)
            pwm_final_blk["state"] = _state_phase
            if _state_phase == "limit":
                # The PWM map is a STEADY map at the limit instant's inputs —
                # read only for the time to the limit (`limited.pwm`); its
                # temperatures are not a state this answer is in.
                pwm_final_blk["dT_vs_sine_K"] = None
                pwm_final_blk["residual_K"] = None
                pwm_final_blk["note"] = (
                    "limit instant: temperatures held at the sine loop's "
                    "crossing; the PWM pass's own map re-reads the time to "
                    "the limit (limited.pwm)")
            if sine_reused is not None:
                pwm_final_blk["sine_state_reused"] = dict(sine_reused)
            elif sine_reuse_note:
                pwm_final_blk["sine_state_not_reused"] = sine_reuse_note
            if fin.get("em") is not None:
                inverter, ctl = fin["inverter"], ctl_final
                em = fin["em"]
                t_coil, t_mag = float(fin["coil_c"]), fin["magnet_c"]
                t_brg = fin["bearing_c"]
                em_at = (t_coil, t_mag)
                v1_ran = float(inverter["v_phase_peak_V"])
                point_err = _point_error_pct(inverter,
                                             em.get("I_phase_rms_solved_A"))
                point_off = False
                ripple_quotable = ripple_quotable and bool(fin["ripple_quotable"])
                for _n in fin.get("dc_notes") or []:
                    if _n not in dc_notes:
                        dc_notes.append(_n)
                _field_pwm = fin["field"]
                if _state_phase == "limit" and limited:
                    # THE LIMIT INSTANT'S TEMPERATURES ARE THE ANSWER; what the
                    # PWM changes is how soon they are reached — the step
                    # response of the PWM pass's own steady map.
                    from motor_ai_sim.routes import thermal as _th
                    try:
                        _ttl_pwm = _ttl_step(
                            body, cooling, (em.get("summary") or {}),
                            _field_pwm,
                            bearing_temp_c=(em.get("summary") or {}).get(
                                "bearing_temp_c"), runaway=False)
                    except Exception:                       # noqa: BLE001
                        _ttl_pwm = None
                    _lim_pwm = _ttl.limiting(_ttl_pwm) if _ttl_pwm else None
                    limited["pwm"] = {
                        "t_cold_s": (_lim_pwm or {}).get("t_cold_s"),
                        "t_cold_words": (None if not _lim_pwm else
                                         _ttl.fmt_seconds(_lim_pwm["t_cold_s"])),
                        "part": (_lim_pwm or {}).get("part"),
                        "sine_t_cold_s": limited.get("t_cold_s"),
                        "note": ("time to the limit on the controller's PWM "
                                 "losses (the sine loop found the limit "
                                 "instant; the PWM pass re-read its step "
                                 "response)")}
                    try:
                        field = _th.rescale_map_to_nodes(
                            _field_pwm, limited["temperatures_at_limit"],
                            note=("the PWM pass's own map translated onto the "
                                  "node temperatures of the limit instant"))
                        if field_params:
                            _th._remember_last("field", field,
                                               dict(field_params),
                                               field.get(
                                                   "geometry_fingerprint"))
                    except Exception:                       # noqa: BLE001
                        field = _field_pwm
                    limited["drive_held"] = "inverter"
                else:
                    field = _field_pwm
                    converged = bool(converged) and bool(fin.get("converged"))
                if _cr_s1:
                    _part = str(continuous_rating.get("limiting_part") or "")
                    _lim_c = _ccr._num((continuous_rating.get("limits_c") or {})
                                       .get(_part))
                    _node = ("winding" if _part == "winding" else
                             "magnet" if _part == "magnet" else _part)
                    _act = _ccr._num(((field or {}).get("components") or {})
                                     .get(_node, {}).get("max"))
                    _amb = _ccr._num((cooling or {}).get("ambient_temp"))
                    _amb = 25.0 if _amb is None else _amb
                    _pwm_cr: Dict[str, Any] = {
                        "limiting_part": _part, "limit_c": _lim_c,
                        "actual_c": _act,
                        "margin_K": (None if (_lim_c is None or _act is None)
                                     else round(_lim_c - _act, 2))}
                    if (_lim_c is not None and _act is not None
                            and _act > _lim_c and _act > _amb):
                        _pwm_cr["I_cont_pwm_estimate_A"] = round(
                            float(continuous_rating["I_cont_A_rms"])
                            * math.sqrt(max(_lim_c - _amb, 1e-6)
                                        / max(_act - _amb, 1e-6)), 3)
                        _pwm_cr["note"] = (
                            "with the controller's PWM losses the %s reaches "
                            "%.1f °C, %.1f K over its limit at the S1 current; "
                            "the first-order PWM-corrected current is an "
                            "estimate, not re-searched"
                            % (_part, _act, _act - _lim_c))
                    continuous_rating["pwm"] = _pwm_cr
                for r in fin["passes"]:
                    history.append({
                        "iter": len(history) + 1, "phase": "pwm_final",
                        "T_coil_in": r["T_coil_in"],
                        "T_magnet_in": r["T_magnet_in"],
                        "T_coil_out": r["T_coil_out"],
                        "T_magnet_out": r["T_magnet_out"],
                        "T_magnet_max": r.get("T_magnet_max"),
                        "bearing_temp_c": r.get("T_bearing_in"),
                        "T_bearing_out": r.get("T_bearing_out"),
                        "P_loss_W": r.get("P_loss_W"),
                        "T_em_Nm": r.get("T_em_Nm"),
                        "v_phase_peak_V": r.get("v_phase_peak_V"),
                        "I_phase_rms_solved_A": r.get("I_phase_rms_solved_A"),
                        "point_error_pct": r.get("point_error_pct"),
                        "T_junction_c": r.get("T_junction_c"),
                        "d_T_junction_K": r.get("d_T_junction_K"),
                        "P_inverter_W": r.get("P_inverter_W"),
                    })
                if _coupled_body_bool(body, "sine_compare", True):
                    # The SAME-temperature inverter column: the first PWM pass
                    # (steady / S1), or the last one at the limit, where every
                    # pass is at the instant's temperatures and only the
                    # current was re-aimed.
                    _em_eq = (fin["em"] if _state_phase == "limit"
                              else fin["em_first"])
                    sine_cmp = _sine_cmp_final(
                        sine_em, _em_eq, fin["em"],
                        state_phase=_state_phase, sine_temps=sine_temps,
                        last_temps=(t_coil, t_mag), ctl=ctl, body=body)
                log.info("coupled: controller PWM on the %s sine state — %d "
                         "pass(es), %.0f s; ΔT vs sine %s",
                         _state_phase, len(fin["passes"]),
                         time.time() - _t0p, pwm_final_blk["dT_vs_sine_K"])
            else:
                # NOT EVEN ONE PWM PASS: the sine state stands and the record
                # says it is a sine record, and why.
                refusal = fin.get("refusal")
                refusal_code = fin.get("refusal_code")
        # ── SINE vs INVERTER, the `full` loop (owner 2026-09-25) ───────────
        # One background pass on the ideal sinusoid, at the reported state's
        # own fundamental current and temperatures — see
        # `_sine_comparison_step`.  Only on the Controller's drive, and only on
        # a run whose answer is filed (not an errand); `sine_compare: false`
        # in the body skips it.
        if (not final_pass and ctl is not None and inverter is not None
                and history and em
                and not _rr.suppressed()
                and _coupled_body_bool(body, "sine_compare", True)):
            _check_cancelled(run_id)
            _progress.update(phase="sine reference — the same point on an "
                                   "ideal sinusoid, same temperatures")
            _state_phase = (
                "s1_verify" if (continuous_rating or {}).get("record_is_s1")
                else "limit" if (limited and limited.get("em_run"))
                else "loop")
            sine_cmp = _sine_comparison_step(
                body, em, coil_temp_c=float(em_at[0]),
                magnet_temp_c=(None if em_at[1] is None else float(em_at[1])),
                bearing_temp_c=t_brg, inverter=inverter, ctl=ctl,
                state_phase=_state_phase)
            if sine_cmp:
                log.info("coupled: sine comparison (%s state) — %s",
                         _state_phase, "; ".join(
                             "%s %s→%s" % (r["key"], r["sine"], r["inverter"])
                             for r in sine_cmp["rows"]
                             if r["key"] in ("T_em_avg_Nm", "P_loss_total_W")))
        # ── AND THE SAME MACHINE AT 20 °C (owner 2026-09-18) ────────────────
        # *«для каждого отчёта делать прогон на холодную 20 °C, чтобы находить
        # все коэффициенты KV, Kt, Km, Km/mass, которые фигурируют во всех
        # каталогах моторов и нужны для сравнения»*.  One background pass, at
        # the end, feeding back into nothing: it is a measurement of the
        # machine, not a state the machine is in.
        if want_cold and history:
            _check_cancelled(run_id)
            _progress.update(phase="catalogue constants — the same machine at "
                                   "%g °C" % COLD_CONSTANTS_C)
            constants_20c = _cold_constants_step(
                body, rpm=rpm_eff,
                # The catalogue constants are a MEASUREMENT of the machine, not
                # of its power stage: a 20 °C pass is run on whichever bridge
                # the loop used, and "inverter" is spelled "pwm" to that step
                # because it takes the ideal modulator's arguments.
                drive=("current" if final_pass
                       else "pwm" if drive == "inverter" else drive),
                # …and on `final_pass` the constants are measured on the
                # sine, like the loop itself: they are the machine's, and a
                # PWM transient at 20 °C would cost what the new algorithm
                # exists to save.
                inverter=(None if final_pass else inverter))
            if constants_20c:
                log.info("coupled: constants at %g degC — KV %s rpm/V, Kt %s "
                         "N·m/A (line), Km %s N·m/sqrt(W), Km/kg %s",
                         COLD_CONSTANTS_C,
                         constants_20c.get("kv_line_rpm_per_V"),
                         constants_20c.get("kt_line_Nm_per_A"),
                         constants_20c.get("km_Nm_sqrtW"),
                         constants_20c.get("km_per_mass_Nm_sqrtW_kg"))
        if time_to_limit is not None:
            # WHICH STATE THE RECORD AROUND IT DESCRIBES.  A reader who has only
            # this block must not have to guess whether the temperatures beside
            # it are a steady state or the instant of the crossing.
            time_to_limit["reported_state"] = "limited" if limited else "steady"
            time_to_limit["solve_to"] = solve_to
        # THE THIRD TAB at the same temperatures (phase 3): the rotor stress is
        # solved once, at the per-part averages of the last thermal map, while
        # this loop still owns the lock — a second coupled run must not start
        # between the map and the mechanics that belong to it.
        if history and field and body.get("mechanical") and runaway:
            # A map that ran away is not a state the machine can be in: a
            # rotor-stress verdict at 658 °C (2026-09-09, the 40 mm at its peak
            # in still air) would read as "the magnets fail" when the answer
            # is "there is no equilibrium".  Recorded as a refusal, like any
            # other mechanical step that cannot be solved honestly.
            mech_block = {"ok": False, "temps_c": {}, "error": (
                "thermal runaway — the map has no equilibrium temperatures to "
                "solve the rotor at")}
        elif history and field and body.get("mechanical"):
            _check_cancelled(run_id)
            _progress.update(
                phase="mechanical — rotor stress at the converged temperatures")
            # At THIS run's point: the speed the loop solved at and the torque
            # the last electromagnetic run produced — not the Mechanical tab's
            # boxes, which hold whatever machine they were typed for.  The
            # torque is the SHAFT torque — the 2-D mean times the machine's
            # 3-D end-effect factor when the run carries one — i.e. the number
            # the Electromagnetic tab shows (user 2026-09-09: "0.59 Nm", not the
            # 2-D 0.623 the loop had used).
            _t_last, _t_note = _shaft_torque_nm(em.get("summary") or {})
            mech_block = _mechanical_step(
                body, field, authorization=authorization, rpm=rpm_eff,
                torque_nm=_t_last)
            if mech_block and _t_note and mech_block.get("ok"):
                mech_block["torque_note"] = _t_note
        # …and the modes and the critical speeds (2026-09-13), so one coupled
        # run leaves every mechanical answer the report prints.  Not gated on
        # the map or on runaway — see `_modal_steps`.
        if history and body.get("mechanical"):
            if mech_block is None:
                mech_block = {"ok": False, "temps_c": {}, "error": (
                    "no thermal map to take the rotor temperatures from — the "
                    "rotor stress was not solved")}
            mech_block.update(_modal_steps(
                body, authorization=authorization, rpm=rpm_eff, run_id=run_id,
                f_switch_hz=fsw_eff))
        # Hand the unspent budget back, exactly as `thermal.solve_coupled` does:
        # a loop that settled on pass 2 of 6 should END its bar at 2, not report
        # itself a third finished for ever.  The budget over-estimates on
        # purpose — a bar that shrinks under the user is better than one that
        # sticks at 100 % while the solver is still working.
        _done = max(len(history), 1) * 2
        _progress.update(done=_done, total=_done)
    except (_LoopCancelled, _JOBS.JobCancelled):
        # …a JobCancelled too: the mechanical / thermal / rating steps check
        # the job's own cancel flag (`jobs.check_cancelled`) inside their
        # loops, and raise the queue's exception rather than this router's.
        _progress.update(phase="cancelled")
        log.info("coupled run %s cancelled", run_id)
        raise HTTPException(status_code=499, detail="coupled run stopped")
    finally:
        # Unconditional: an exception on any path must not leave /progress
        # reporting a live loop, nor the lock held.
        _progress.finish()
        _LOCK.release()

    # One electromagnetic run per iteration, and the history has one row per
    # iteration — so `em_runs` and `iterations` are the same count, reported as
    # two keys because they answer two questions (what it cost, and how far it
    # got) and a reader should not have to know they are the same number.
    # Rows restored from a REUSED sine state were not solved by this run.
    n_em = len([r for r in history if not r.get("sine_reused")])
    last_row = history[-1] if history else {}
    # THE MECHANICAL HALF, at the top: the numbers the LAST run carries, so the
    # block and the cards beside it are one answer.  All four are absent — never
    # zero — on a machine that names no bearings: an unknown mechanical loss
    # printed as 0 W is an efficiency nobody measured (the house rule the
    # /api/bearings/losses route and the summary already follow).
    _last_summary = (em.get("summary") or {}) if isinstance(em, dict) else {}
    _mech_top: Dict[str, Any] = {}
    for _k in ("bearing_temp_c", "bearing_temp_source", "bearing_temp_note",
               "P_bearings_W", "P_windage_W", "P_mech_extra_W",
               "P_loss_total_incl_mech_W", "efficiency_shaft"):
        if _last_summary.get(_k) is not None:
            _mech_top[_k] = _last_summary[_k]
    # The note is written ONLY when the last run really was billed at a fed-back
    # temperature.  A single-pass loop resolves the bearing from the machine, and
    # `brg_where` is already set by then (that pass's own map produced it) — so
    # keying the note off `brg_where` alone would claim a feedback that had not
    # happened yet, on exactly the run where it matters most.
    if _mech_top.get("bearing_temp_source") == "coupled" and brg_where:
        _mech_top["bearing_temp_note"] = (
            "fed back from the previous iteration's thermal map — " + brg_where)
    # …and how far the FINAL map's seat sits from the temperature the last run
    # was billed at — the number a reader checks against the shaft row of the
    # thermal table (reviewer 2026-09-13, item 3).  Written into the run's own
    # summary too: that is the note the report's bearing line prints.
    _fin = last_row.get("T_bearing_out")
    if _fin is not None and _mech_top.get("bearing_temp_c") is not None:
        _mech_top["bearing_temp_note"] = (
            str(_mech_top.get("bearing_temp_note") or "").rstrip(".")
            + "; the final map puts the seat at %.1f °C (residual %.1f K)"
            % (float(_fin), abs(float(_fin) - float(_mech_top["bearing_temp_c"])))
        ).lstrip("; ")
        _last_summary["bearing_temp_note"] = _mech_top["bearing_temp_note"]
    # ── THE CYCLE'S OWN VERDICT ─────────────────────────────────────────────
    # A duty ratio that does not fit is not a failed run: the loop solved the
    # machine and the machine cannot hold what was asked of it, which is the
    # answer the user came for.  It rides as a WARNING with its own code — never
    # over a refusal or a runaway, which stopped the loop for a harder reason and
    # own the message — so the panel's one-line notice can print the sentence.
    if regime is not None and refusal is None and not runaway:
        if not regime.get("feasible", True):
            refusal_code = "duty_cycle_no_allowable_ed"
            refusal = str(regime.get("note") or "")
            log.warning("coupled: %s", refusal)
        elif regime.get("fits_requested") is False:
            refusal_code = "duty_cycle_requested_over_allowable"
            refusal = ("the regime this duty asks for does not fit: %s"
                       % (regime.get("note") or ""))
            log.warning("coupled: %s", refusal)
    block: Dict[str, Any] = {
        # WHICH EXCITATION these temperatures belong to.  Always written — a
        # report that finds no `drive` is reading a record from before the
        # inverter existed, and that is a sinusoid; a record that says "pwm"
        # carries the `inverter` block beside it and nothing has to be inferred.
        "drive": ("inverter" if ctl is not None
                  else "pwm" if inverter is not None else "sine"),
        **({"inverter": _inverter_record(em, _record_inverter(inverter, v1_ran),
                                         ripple_quotable=ripple_quotable)}
           if inverter is not None else {}),
        # ── THE CONTROLLER (Stage 2) ───────────────────────────────────────
        # The devices' own losses, their junction temperature, the datasheet
        # verdict and BOTH efficiencies, from the SAME run the machine's
        # numbers above came from.  The `inverter` block beside it keeps its
        # meaning unchanged — carrier, link, modulation, the settled DC — and
        # this block says which power stage applied them.
        **({"controller": ctl.record(em)} if ctl is not None else {}),
        # …and the same point on an ideal sinusoid, at the same temperatures
        # (owner 2026-09-25) — see `_sine_comparison_step`.
        **({"sine_comparison": sine_cmp} if sine_cmp else {}),
        # HOW the inverter drive was coupled (2026-09-25) and, on the default
        # `final_pass`, what the PWM pass(es) did to the sine state.
        **({"inverter_coupling": inv_mode} if inv_mode else {}),
        **({"pwm_final": pwm_final_blk} if pwm_final_blk else {}),
        # THE LAST RUN's electromagnetic numbers, in the block itself.  Not a
        # duplicate for its own sake: the per-duty record keeps this block and
        # drops the transient beside it, and "sine → PWM at the same point"
        # is a comparison of torque and of the four loss classes — a
        # `reference_sine` that carried temperatures but no watts could not
        # print the one row the whole exercise is about (the carrier's cost).
        "em": {k: _last_summary.get(k) for k in _EM_FACE_KEYS
               if _last_summary.get(k) is not None},
        "coil_temp_c": round(float(em_at[0]), 2),
        "magnet_temp_c": (None if em_at[1] is None else round(float(em_at[1]), 2)),
        # THE REGIME, when this duty has one (2026-09-16).  The temperatures
        # above are the ones the last electromagnetic run was solved at, and on
        # an impulse duty they ARE this block's cycle temperatures — the loop
        # closes on both together.  Absent on every S1 machine, which is what
        # "the loop is unchanged there" looks like in the payload.
        **({"duty_cycle": _cycle_block_of(regime, cycle_in)}
           if regime is not None else {}),
        # HOW LONG THIS POINT MAY BE HELD (owner 2026-09-17).  A temperature past
        # its limit is half an answer: the other half is the TIME, and it rides
        # at the top of the block beside the temperatures it belongs to rather
        # than in the history, because it is the sentence the panel prints and
        # the row the report adds.  Absent on a machine that states no limit and
        # on a run whose map could not be fitted; a point inside every limit
        # carries the block with `within_limits: true` and no time.
        **({"time_to_limit": time_to_limit} if time_to_limit else {}),
        # ── WHICH QUESTION, AND WHICH ANSWER (owner 2026-09-18) ─────────────
        # `solve_to` is what was ASKED (the panel's two-option selector, stored
        # with the duty); `mode` is what this record turned out to BE.  They
        # differ legitimately: a `limits` run of a machine that is inside every
        # limit — or whose transient never reaches one — is a `steady` record,
        # and it says so rather than inventing a moment to report.
        #
        # `converged` is NOT how this is read.  A limited record's temperatures
        # are not a fixed point of the loop and never will be; they are an
        # instant of a step response, and a consumer that tested `converged` to
        # decide whether the numbers may be quoted must read `mode` instead.
        "solve_to": solve_to,
        "mode": ("limited" if limited else "steady"),
        **({"limited": limited} if limited else {}),
        # THE CATALOGUE CONSTANTS (owner 2026-09-18): KV, Kt, Km and Km/kg of
        # this machine at 20 °C, for comparison with any other manufacturer's
        # page.  ABSENT — never null — when the cold pass was switched off or
        # could not be solved; the report then says "not solved" rather than
        # extrapolating the hot numbers, which is the very thing this pass
        # exists to replace.
        **({"constants_20c": constants_20c} if constants_20c else {}),
        # THE CONTINUOUS (S1) RATING (owner 2026-09-21), when ``solve_to`` asked
        # for it — kept whole, exactly as ``/continuous_rating`` returns a row,
        # so the panel and the report read one shape either way.  Absent, never
        # null, when it was not asked for or could not be found.
        **({"continuous_rating": continuous_rating} if continuous_rating
           else {}),
        # `**` rather than fixed keys: a machine with no bearings grows no
        # mechanical keys at all, which is what "absent, not zero" means in a
        # payload.
        **_mech_top,
        "magnet_temp_max_c": next(
            (r["T_magnet_max"] for r in reversed(history)
             if r.get("T_magnet_max") is not None), None),
        "iterations": n_em,
        "em_runs": n_em,
        # …and in a LIMITED record it is false by construction: the reported
        # state is an instant of a transient, not a fixed point the loop settled
        # on, and a `true` here would tell every consumer the opposite.
        "converged": bool(converged) and not limited,
        "runaway": bool(runaway),
        "tol_K": float(tol),
        "damping": float(damping),
        "damping_final": float(damping_eff),
        "max_iter": int(max_iter),
        # The residual the answer is quoted WITH: how far the thermal solve's
        # last answer sat from the temperature the run was solved at.  Inside
        # `tol_K` when converged; it is the number that says how much.
        "residual_coil_K": (
            None if last_row.get("T_coil_out") is None
            else round(abs(float(last_row["T_coil_out"])
                           - float(last_row["T_coil_in"])), 2)),
        "residual_magnet_K": (
            None if (last_row.get("T_magnet_out") is None
                     or last_row.get("T_magnet_in") is None)
            else round(abs(float(last_row["T_magnet_out"])
                           - float(last_row["T_magnet_in"])), 2)),
        # The bearing seat: what the last run billed its friction at against
        # what its own map then said (absent on a machine with no bearings).
        "residual_bearing_K": (
            None if (last_row.get("T_bearing_out") is None
                     or last_row.get("bearing_temp_c") is None)
            else round(abs(float(last_row["T_bearing_out"])
                           - float(last_row["bearing_temp_c"])), 2)),
        "tol_bearing_K": float(BEARING_TOL_K),
        "history": history,
        # The mechanical step's verdict (phase 3), or its refusal, or None when
        # the caller skipped it — the Mechanical tab's last result is the full
        # answer, this is the line the summary quotes.
        "mechanical": mech_block,
        "note": magnet_note or None,
        # A DC residual inside the band is not a failure — it is a statement
        # about which half of the answer may be quoted, and it rides at the top
        # of the block so nobody has to read the history to find it.
        **({"dc_notes": list(dc_notes)} if dc_notes else {}),
        **({"warning_code": ("limited_operation" if limited and not refusal
                             else refusal_code)}
           if (refusal_code or limited) else {}),
        # A LIMITED run is not an unconverged one, and must not be described as
        # one: the loop stopped deliberately, at a moment the user asked for.
        # The sentence is the block's own — one line, the model in the tooltip.
        "warning": (limited["line"] if (limited and not refusal) else
                    refusal if refusal else
                    "thermal runaway: no equilibrium at this operating point — "
                    "more cooling or less current" if runaway else
                    (None if converged else
                     "stopped after %d electromagnetic run(s) without settling "
                     "inside %g K — raise max_iter, or read the residual below "
                     "as the honest uncertainty on these temperatures"
                     % (n_em, tol))),
    }
    written = _attach_coupling(em, block)

    out: Dict[str, Any] = {
        "ok": bool(em),
        "coupling": block,
        # The two halves, as the panels read them.  `transient` is the payload a
        # Run returns — the panel adopts it exactly as it adopts its own — and
        # `thermal` is the converged map, already remembered as the Thermal tab's
        # last result.
        "transient": em,
        "thermal": field,
        "written_to_last_run": bool(written),
        "run_id": run_id,
        "geometry_fingerprint": em.get("geo_fingerprint"),
        "computed_at": em.get("computed_at"),
        "elapsed_s": round(max(time.time() - t0, 1e-3), 2),
    }
    # /last keeps everything but the two heavy payloads: it answers "what did the
    # loop last conclude", and shipping a transient plus a temperature-per-node
    # map to do it is how a lookup becomes a download.
    _remember_last({k: v for k, v in out.items()
                    if k not in ("transient", "thermal")},
                   alt_carrier=bool(inverter
                                    and inverter.get("record_as") == "alt_carrier"))
    # ── and file the WHOLE coupling block in the persistent history ────────
    # ``history_key`` is the one computed before anything was solved — the
    # inputs it was built from (body, cooling, solve_to, max_iter) are fixed
    # for the whole loop, so it is still the right key for what just ran.
    if history_key is not None:
        try:
            _COUPLED_HISTORY.put(
                history_key,
                params=_coupled_canonical(body, cooling, solve_to, max_iter),
                summary=_coupled_summary(block, solve_to),
                payload=out,
                extra={"alt_carrier": bool(inverter and
                                           inverter.get("record_as") == "alt_carrier")})
        except Exception:                                   # noqa: BLE001
            log.warning("coupled: could not file this run in the history",
                       exc_info=True)
    # ── AND THE CYCLE, UNDER THE DUTY IT DESCRIBES ──────────────────────────
    # The same ``duty_cycle`` kind ``POST /api/thermal/duty_cycle`` files, with
    # the same keys, so the report's Allowable-regime line, its rows and its two
    # ED figures print a coupled answer without knowing one exists.  Never on an
    # errand (`record: false` suppresses the whole seam) and never over a
    # runaway — there is no regime then, and `cycle_doc` is None.
    if cycle_doc is not None and not _rr.suppressed():
        try:
            from motor_ai_sim import duty_results as _dr
            _params = {
                "rpm": rpm_eff, "I_phase_rms": _f(body, "I_phase_rms", 0.0),
                "gamma_deg": _f(body, "gamma_deg", 0.0),
                "coil_temp_c": block["coil_temp_c"],
                "magnet_temp_c": block["magnet_temp_c"],
                "calibration_duty": cycle_in["duty"],
                "calibration_source": ("the coupled loop's own converged "
                                       "thermal map of this very point"),
                **{k: v for k, v in cooling.items()},
            }
            _dr.note_duty_cycle(cycle_doc, _params,
                                out.get("geometry_fingerprint"),
                                out.get("computed_at"),
                                die=cycle_in.get("die") or None,
                                cfg=cycle_in.get("config") or None,
                                duty=cycle_in.get("duty") or None)
        except Exception:  # noqa: BLE001 — bookkeeping never fails a solve
            log.debug("coupled: the duty cycle was not filed", exc_info=True)
    log.info("coupled: %d EM run(s), winding %.1f degC, magnets %s, %s%s",
             n_em, block["coil_temp_c"],
             "n/a" if block["magnet_temp_c"] is None
             else "%.1f degC" % block["magnet_temp_c"],
             ("AT THE LIMIT" if limited else
              "converged" if converged else "NOT converged"),
             (" — %s" % limited["line"] if limited else
              "" if not time_to_limit or time_to_limit.get("within_limits", True)
              else " — %s" % _ttl.headline(time_to_limit)))
    if history_key is not None:
        out["history_key"] = history_key
    return out


# ---------------------------------------------------------------------------
# THE CATALOGUE CONSTANTS ON THEIR OWN — for a duty that already converged
# ---------------------------------------------------------------------------

@router.post("/constants_20c")
@_JOBS.queued("coupled.constants_20c", priority=_JOBS.Priority.DUTY,
              run_id_from=_JOBS.body_run_id("coupled"))
def constants_20c(body: Dict[str, Any] = Body(default_factory=dict),
                  authorization: Optional[str] = Header(default=None)
                  ) -> Dict[str, Any]:
    """``POST /api/coupled/constants_20c`` — ONE cold pass, nothing else.

    Owner, 2026-09-18: every report is to carry the KV / Kt / Km / Km-per-kg a
    catalogue quotes, and those are 20 °C numbers.  A duty whose coupled loop
    already converged should not have to pay for that loop again to get them —
    it is one electromagnetic run at 20/20 at the same point, and this is it.

    The body is the same payload ``/run`` takes (the operating point, the mesh,
    the drive and, on a voltage-fed duty, its ``inverter``); ``max_iter``,
    ``thermal_settings`` and ``mechanical`` are ignored, because nothing thermal
    and nothing mechanical happens here.

    What it WRITES, and only this: ``constants_20c`` onto the last coupled
    answer (``/last``) and onto the duty's stored coupled record, both in place.
    The temperatures, the torque, the losses and every other number in those
    records are left exactly as the loop left them — this pass is a measurement
    of the machine, not a new answer about the duty, and a route that quietly
    replaced a converged record with a 20 °C one would be the no-silent-state
    rule broken.  ``record: false`` files nothing, as everywhere else.
    """
    from motor_ai_sim import material_context as _mc
    from motor_ai_sim.routes.simulation import _effective_rpm, _parse_mat_override

    tok_rec = None if _record_wanted(body) else _rr.suppress()
    try:
        if body.get("mat") is not None:
            ov = _parse_mat_override(body.get("mat"))
            if ov and ov.get("assignment"):
                from motor_ai_sim.materials import (UnknownMaterialError,
                                                    validate_assignment)
                try:
                    validate_assignment(ov["assignment"],
                                        known_extra=set(ov.get("materials") or ()))
                except UnknownMaterialError as exc:
                    raise HTTPException(status_code=400, detail=str(exc))
            _mc.set_request_materials(ov)

        drive = _coupled_drive(body)
        rpm_eff = _effective_rpm(body.get("rpm"))
        inverter = (_inverter_settings(body, rpm=rpm_eff)
                    if drive == "pwm" else None)
        # The SAME lock the loop takes: this pass goes through the transient
        # solver and its process-wide caches, and a cold run interleaving with
        # a coupled iteration would have each reading the other's field.
        if not _LOCK.acquire(blocking=False):
            raise HTTPException(status_code=409, detail=(
                "a coupled run is already in flight on this backend — wait for "
                "it or press Stop"))
        t0 = time.time()
        try:
            _progress.start(total=1, kind="coupled",
                            phase="catalogue constants — the same machine at "
                                  "%g °C" % COLD_CONSTANTS_C,
                            composition="one electromagnetic run")
            out = _cold_constants_step(body, rpm=rpm_eff, drive=drive,
                                       inverter=inverter)
            _progress.update(done=1, total=1)
        finally:
            _progress.finish()
            _LOCK.release()
        if not out:
            raise _refuse(
                "the machine could not be solved at %g °C, so it has no "
                "catalogue constants. The electromagnetic run refused — see "
                "the server log for the reason it gave." % COLD_CONSTANTS_C,
                ["cold_constants"], code="cold_constants_unsolved")
        written = _merge_constants_20c(out)
        return {"ok": True, "constants_20c": out,
                "written_to_last": bool(written.get("last")),
                "written_to_duty": bool(written.get("duty")),
                "elapsed_s": round(max(time.time() - t0, 1e-3), 2)}
    finally:
        _rr.restore(tok_rec)


def _merge_constants_20c(block: Dict[str, Any]) -> Dict[str, bool]:
    """Put ``block`` on the last coupled answer and on the duty's record.

    IN PLACE, and touching nothing else.  Both writes are best-effort and
    reported: a machine that has never been through the loop has no record to
    merge into, which is an answer ("run the loop first"), not a failure.
    """
    done = {"last": False, "duty": False}
    if _rr.suppressed():
        return done
    try:
        _load_last()
        cur = dict(_LAST)
        cp = cur.get("coupling")
        if isinstance(cp, dict) and cp:
            cp = dict(cp)
            cp["constants_20c"] = dict(block)
            cur["coupling"] = cp
            _remember_last(cur)
            done["last"] = True
    except Exception:  # noqa: BLE001 — bookkeeping never fails a solve
        log.debug("coupled: the 20 °C constants were not merged into /last",
                  exc_info=True)
    if done["last"]:
        # `_remember_last` already re-filed the whole compacted record under the
        # duty, constants included — no second write, and therefore no way for
        # the two copies to disagree.
        done["duty"] = True
        return done
    # No coupled answer in hand: patch the DUTY's stored record directly, so a
    # duty whose loop ran in another session (or before a restart) still gets
    # its constants.
    try:
        from motor_ai_sim import duty_results as _dr
        ctx = _dr.active_context()
        if ctx:
            entry = (_dr.get(ctx[0], ctx[1]) or {}).get(ctx[2]) or {}
            rec = entry.get("coupled")
            if isinstance(rec, dict) and rec:
                rec = dict(rec)
                rec["constants_20c"] = dict(block)
                done["duty"] = bool(_dr.record(*ctx, "coupled", rec))
    except Exception:  # noqa: BLE001
        log.debug("coupled: the 20 °C constants were not filed under the duty",
                  exc_info=True)
    return done


# ---------------------------------------------------------------------------
# HOW MUCH MAY IT PULL FOR EVER — the continuous rating, per cooling condition
# ---------------------------------------------------------------------------
# Owner, 2026-09-20: *«давай ещё сделаем расчёт continuous power для разных
# условий охлаждения»*.  The loop and `coupled_time_to_limit` both answer for a
# current somebody typed; this answers for the current the machine may HOLD, and
# it answers it once per cooling condition, because that is the number that
# moves by a factor of three between a joint in still air and a jacketed one.
#
# ONE electromagnetic run pays for the whole table.  The physics is in
# `coupled_continuous_rating` (pure); everything here is the bridge to the 2-D
# thermal FEM: which map, solved with which copper, under which cooling.


def _cr_point_kwargs(body: Dict[str, Any]) -> Dict[str, Any]:
    """The operating-point and mesh half of a thermal request, from the body.

    The same fields ``/run`` takes and the same defaults, so a caller hands this
    route its Simulation-tab payload unchanged.  The COOLING half is not here:
    it is what a condition varies.
    """
    from motor_ai_sim.routes.simulation import _effective_rpm

    return dict(
        rpm=_effective_rpm(body.get("rpm")),
        gamma_deg=_f(body, "gamma_deg", 0.0),
        I_phase_rms=_f(body, "I_phase_rms", 0.0),
        n_steps_per_period=int(body.get("n_steps_per_period") or 12),
        n_periods=_f(body, "n_periods", 1.0),
        mesh_size_mm=_f(body, "mesh_size_mm", 3.0),
        min_size_mm=_f(body, "min_size_mm", 0.3),
        outer_air_factor=_f(body, "outer_air_factor", 1.3),
        n_sectors=int(body.get("n_sectors") or 1),
        component_mesh=str(body.get("component_mesh") or ""),
        geo=body.get("geo"),
        coil_temp_c=_f(body, "coil_temp_c", 120.0),
        magnet_temp_c=(None if body.get("magnet_temp_c") in (None, "")
                       else float(body["magnet_temp_c"])),
        op_mode=body.get("mode"),
    )


def _cr_duty_cooling() -> Tuple[Dict[str, Any], str]:
    """``(the cooling the loaded duty's stored thermal map was solved under, why)``.

    The DEFAULTS a condition patches, so "the saved setup but 20 m/s" is one
    key.  ``({}, why)`` when this machine has no stored thermal block — and then
    a condition carries its whole cooling or the request is refused by name,
    rather than silently solved against the panel's still-air defaults.
    """
    try:
        from motor_ai_sim import duty_results as _dr
        ctx = _dr.active_context()
        if not ctx:
            return {}, ("no die / configuration / duty is loaded, so there is "
                        "no saved thermal setup to take the defaults from")
        rec = (_dr.get(ctx[0], ctx[1]) or {}).get(ctx[2]) or {}
        th = rec.get("thermal")
        if not isinstance(th, dict) or not th:
            return {}, ("the duty %r has no stored thermal map, so there is no "
                        "saved cooling to patch" % ctx[2])
        return _ccr.cooling_from_duty_thermal(th), (
            "the cooling the duty %r's own stored thermal map was solved under"
            % ctx[2])
    except Exception as exc:  # noqa: BLE001 — a store read never fails a solve
        log.debug("continuous_rating: no duty defaults", exc_info=True)
        return {}, "the duty's stored thermal setup could not be read (%s)" % exc


def _cr_scaled_map(em: Dict[str, Any], factor: float) -> Dict[str, Any]:
    """The same loss map with ONLY the winding multiplied by ``factor``.

    The current-scaling twin of ``routes.thermal._scaled_copper_map``, and it is
    deliberately ONE factor over the whole copper: ``factor`` already carries
    both ``s**2`` and the copper's own rho ratio, the map does not carry the
    DC/AC split per ELEMENT, and the thermal solve spreads the copper total
    uniformly over the coil domain anyway (``q_cu`` in ``solve_thermal_field``).
    Iron, magnet, shaft and sleeve losses are untouched — they do not move with
    the winding's current at a fixed field to first order, which is the
    approximation ``coupled_continuous_rating`` states out loud.

    A shallow copy: the mesh arrays are shared.
    """
    import numpy as _np

    from motor_ai_sim.routes.thermal import _DOM_COIL_VIS

    f = float(factor)
    out = dict(em)
    tags = _np.asarray(em.get("domain_per_tri") or [], int)
    ld = _np.asarray(em.get("loss_density_per_tri") or [], float)
    if ld.size and tags.size == ld.size:
        ld = ld.copy()
        coil = (tags == _DOM_COIL_VIS)
        if coil.any():
            ld[coil] *= f
        out["loss_density_per_tri"] = ld.tolist()
    for p_key, tot_key in (("P_cu_W", "P_loss_total_W"),
                           ("P_cu_exact_W", "P_loss_total_exact_W")):
        if em.get(p_key) is None:
            continue
        p = float(em[p_key])
        out[p_key] = p * f
        if em.get(tot_key) is not None:
            out[tot_key] = float(em[tot_key]) + p * (f - 1.0)
    for ac_key in ("P_cu_ac_solve_W", "P_cu_ac_exact_W"):
        if em.get(ac_key) is not None:
            out[ac_key] = float(em[ac_key]) * f
    return out


def _cr_solve_map(point: Dict[str, Any], cooling: Dict[str, Any], *,
                  em_map: Optional[Dict[str, Any]] = None,
                  em_source: Optional[Dict[str, Any]] = None,
                  capture: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """ONE 2-D steady thermal map: this point, this cooling, this loss map.

    Nothing is remembered and nothing is filed — unlike the loop's own
    ``_thermal_step``, which re-points the Thermal tab at its result.  A rating
    sweep solves six machines the user did not ask to look at, and leaving the
    last one in the tab would be exactly the silent state change the project
    forbids.
    """
    from motor_ai_sim.routes import thermal as th

    kw = dict(point)
    kw.update(cooling)
    if em_map is not None:
        kw["_em_map"] = em_map
        kw["_em_loss_source"] = dict(em_source or {})
    if capture is not None:
        kw["_em_capture"] = capture
    return th.solve_thermal_field(**kw)


def _cr_conditions(body: Dict[str, Any]) -> List[Any]:
    """The conditions this request asks for, validated by name."""
    raw = body.get("conditions")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise _refuse(
            "continuous_rating needs a conditions list: each entry is a label "
            "plus the cooling that differs from the duty's saved setup, for "
            "example {label: 'forced air 20 m/s', cooling_mode: 'air', "
            "air_speed_mps: 20, bore_mode: 'none'}.",
            ["conditions"], code="no_conditions")
    out: List[Any] = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise _refuse("conditions[%d] is not an object" % i, ["conditions"])
        label = str(c.get("label") or "").strip() or ("condition %d" % (i + 1))
        params = {k: v for k, v in c.items()
                  if k in _ccr.COOLING_KEYS and v is not None}
        unknown = sorted(set(c) - set(_ccr.COOLING_KEYS) - {"label"})
        if unknown:
            raise _refuse(
                "conditions[%d] (%s) names %s, which is not cooling: a "
                "condition may only vary how the machine is COOLED — the "
                "operating point, the mesh and the electromagnetic run are the "
                "body's and are the same for every row."
                % (i, label, ", ".join(repr(u) for u in unknown)),
                ["conditions"], code="condition_not_cooling")
        out.append(_ccr.Condition(label, params))
    return out


def _cr_em_summary() -> Dict[str, Any]:
    """The summary of the electromagnetic run the thermal map was built from.

    ``solve_thermal_field`` does not hand its EM summary back, so it is read
    from the same place every other consumer reads it — the persisted last
    transient.  ``{}`` when there is none, and the caller then refuses by name
    rather than rating a machine whose torque nobody knows.
    """
    try:
        from motor_ai_sim.routes import simulation as _sim
        res = dict(_sim._last_transient_ref.get("result") or {})
        return dict(res.get("summary") or {})
    except Exception:  # noqa: BLE001
        log.debug("continuous_rating: no last transient summary", exc_info=True)
        return {}


def _cr_record(block: Dict[str, Any]) -> bool:
    """File the table under the loaded duty as ``continuous_rating``."""
    try:
        from motor_ai_sim import duty_results as _dr
        ctx = _dr.active_context()
        if not ctx:
            return False
        return bool(_dr.record(*ctx, "continuous_rating", dict(block)))
    except Exception:  # noqa: BLE001 — bookkeeping never fails an answer
        log.debug("continuous_rating: not filed under the duty", exc_info=True)
        return False


def _cooling_label(cooling: Mapping[str, Any]) -> str:
    """One line, for a human, of a cooling block — "forced air 40 m/s + bore
    air 10 m/s, 30 °C".  Display only; the machine-readable answer is the
    ``cooling`` block that already rides beside it in every rating."""
    c = dict(cooling or {})
    mode = str(c.get("cooling_mode") or "").strip().lower()
    parts: List[str] = []
    if mode == "air":
        v = _ccr._num(c.get("air_speed_mps"))
        parts.append("forced air %.0f m/s" % v if v else "still air")
    elif mode == "liquid":
        v = _ccr._num(c.get("flow_lpm"))
        parts.append("liquid jacket, %s%s" % (
            str(c.get("fluid") or "water"),
            "" if v is None else " %.1f L/min" % v))
    elif mode == "manual":
        v = _ccr._num(c.get("h_conv"))
        parts.append("manual film" + ("" if v is None else " %.0f W/m2K" % v))
    elif mode == "robotics":
        v = _ccr._num(c.get("emissivity"))
        parts.append("still air + radiation" + ("" if v is None
                                                 else " eps %.2f" % v))
    else:
        parts.append(mode or "cooling not stated")
    if str(c.get("frame") or "housed").strip().lower() == "open":
        v = _ccr._num(c.get("open_air_speed_mps"))
        parts.append("open frame" + ("" if v is None else " %.0f m/s" % v))
    bore = str(c.get("bore_mode") or "none").strip().lower()
    if bore == "air":
        v = _ccr._num(c.get("bore_air_speed_mps"))
        parts.append("bore air" + ("" if v is None else " %.0f m/s" % v))
    elif bore == "liquid":
        v = _ccr._num(c.get("bore_flow_lpm"))
        parts.append("bore liquid" + ("" if v is None else " %.1f L/min" % v))
    if _ccr._num(c.get("mount_g_w_per_k")):
        parts.append("mount %.1f W/K" % _ccr._num(c.get("mount_g_w_per_k")))
    label = " + ".join(p for p in parts if p)
    amb = _ccr._num(c.get("ambient_temp"))
    if amb is not None:
        label += ", %g °C" % amb
    return label or "cooling not stated"


def _cr_consistency_guard(block: Dict[str, Any],
                          time_to_limit: Optional[Dict[str, Any]]
                          ) -> Dict[str, Any]:
    """A rating that CONTRADICTS this run's own ``time_to_limit`` verdict is
    not trustworthy, whatever its residual says (bug found in production
    2026-09-21: a point 41 s from its winding class came back rated ABOVE its
    own duty current — a point that reaches its limit cannot hold MORE
    current for ever, and a point inside every limit cannot rate BELOW the
    current it is already holding).  Belt and braces over the reference-map
    fix below: the guard catches any OTHER way the two could disagree, not
    only the one that was found.
    """
    if not isinstance(block, dict) or not block.get("ok") \
            or block.get("feasible") is False or block.get("trustworthy") is False:
        return block
    s = _ccr._num(block.get("s"))
    within = (time_to_limit or {}).get("within_limits") \
        if isinstance(time_to_limit, dict) and time_to_limit else None
    if s is None or within is None:
        return block
    contradiction = None
    if within is False and s >= 1.0:
        contradiction = (
            "time_to_limit says this point is OVER a limit (%s), yet the "
            "continuous rating came out at or above the duty's own current "
            "(s=%.3f) — a point that reaches its limit cannot hold MORE "
            "current for ever" % (time_to_limit.get("limiting_part")
                                  or "a part", s))
    elif within is True and s < 1.0:
        contradiction = (
            "time_to_limit says this point is INSIDE every limit, yet the "
            "continuous rating came out below the duty's own current "
            "(s=%.3f) — an inside-limits point should rate at or above its "
            "own current" % s)
    if contradiction is None:
        return block
    out = dict(block)
    out["trustworthy"] = False
    out["notes"] = list(block.get("notes") or []) + [
        "CONTRADICTS THE LOOP'S OWN VERDICT: " + contradiction]
    out["note"] = contradiction
    return out


def _setpoint_only_limited_line(i_duty: Optional[float],
                                time_to_limit: Optional[Dict[str, Any]]) -> str:
    """The AT-THE-LIMIT sentence, re-worded once the record's own numbers have
    moved to the S1 machine (owner 2026-09-21, third round: *«опять токи не
    совпадают»* — the setpoint's own "Runs 45 s …" sentence used to end "the
    numbers below are the machine at that moment", which became FALSE the
    instant those numbers became the S1 pass's.  States the SETPOINT's own
    current up front and drops the now-false tail.  Only ever called after a
    real S1 verification pass replaced the record — `limits` mode, where the
    tail is still true, keeps `coupled_time_to_limit.limited_line` unchanged.
    """
    lim = _ttl.limiting(time_to_limit)
    if not lim:
        return ""
    runs = "%s from cold" % _ttl.fmt_seconds(lim["t_cold_s"])
    if lim["t_rated_s"] is not None:
        runs += " (%s from rated)" % _ttl.fmt_seconds(lim["t_rated_s"])
    i_txt = ("Setpoint %.2f A rms " % i_duty) if i_duty is not None else ""
    return ("%sruns %s at this cooling, then the %s reaches %s"
            % (i_txt, runs, lim["part"],
               "%g °C" % lim["limit_c"] if lim["limit_c"] is not None
               else "its limit"))


def _s1_verify(body: Dict[str, Any], *, cooling: Dict[str, Any], rpm: float,
               inverter: Optional[Dict[str, Any]], i_estimate: float,
               coil_temp_c_guess: float, magnet_temp_c_guess: Optional[float],
               limiting_part: str, limit_c: float, max_passes: int = 2,
               controller: Optional["_ControllerLoop"] = None
               ) -> Dict[str, Any]:
    """CONFIRM the S1 network estimate with a real EM pass, at the current
    the estimate found — never trust the network alone.

    Owner, 2026-09-21 (screenshot: the S1 line said 34.1 A while the tiles
    still showed the 63.64 A setpoint's numbers): *«почему сразу не
    пересчитывается электромагнитное моделирование для найденного
    непрерывного режима — токи не совпадают»*.  One EM pass at ``i_estimate``,
    one real 2-D thermal solve of its own loss map, and the limiting part's
    OWN hot spot / hottest element read straight off that map (never the
    network's node-mean-plus-offset estimate).  Off by more than 3 K in
    either direction: ONE first-order correction —
    ``i_next = i_now · sqrt((limit − ambient) / (actual − ambient))``, the
    same square-law the network search itself bisects on, applied once more
    with the REAL map's reading in place of the estimate — and one more pass.
    Two passes, never more: a machine that still misses after two real solves
    is reported with the miss stated, not chased further.

    Returns ``{"verified", "passes", "miss_K", "I_cont_A_rms", "em", "field",
    "coil_temp_c", "magnet_temp_c"}`` — ``em``/``field`` are the LAST pass
    made, real, for the caller to adopt as the record's own (the "AT THE
    LIMIT" convention, applied to the S1 point instead of the setpoint's).
    Never raises: a pass that cannot be solved stops the loop where it is and
    reports the miss against the last pass that DID solve, or — on the very
    first pass — re-raises so the caller can fall back to the estimate.

    ``controller`` — the drive=inverter loop's :class:`_ControllerLoop`
    (2026-09-25).  Every verification pass is then solved on the
    CONTROLLER's bridge (device drops, dead time), not the ideal one: the
    body-diode fit is re-placed at the pass's own current
    (:meth:`_ControllerLoop.reseed`), the thermal map is that run's OWN
    per-element loss map (``_pwm_loss_map``, as the loop's passes use —
    never a lookup keyed on a sine current), and after the map the
    controller is re-solved on that pass (:meth:`_ControllerLoop.step`), so
    ``T_j``, the device losses and ``eta_inv`` are re-converged AT the S1
    current.  ``T_j`` therefore iterates with the verification passes: pass
    k+1 reads the card at the ``T_j`` pass k's current set, exactly as the
    loop's own passes do; the last pass's remaining step is the record's
    ``t_j_residual_K``.  A pass that refuses puts the controller back to
    the last pass that solved.
    """
    amb = _ccr._num((cooling or {}).get("ambient_temp"))
    if amb is None:
        amb = 25.0
    i_now = float(i_estimate)
    last: Dict[str, Any] = {}
    for k in range(max(int(max_passes), 1)):
        _progress.update(phase="S1 verification %d/%d — EM at %.1f A"
                               % (k + 1, max_passes, i_now))
        body_at = dict(body)
        body_at["I_phase_rms"] = i_now
        inv_at = None
        if inverter is not None:
            # THE FUNDAMENTAL, not the current, is what a PWM run is actually
            # fed — scaled by the same ratio the current moved by, as a
            # STARTING guess for this one pass, exactly as "AT THE LIMIT"
            # drives its own extra pass with the loop's inverter rather than
            # re-regulating it: one pass, and whatever current it actually
            # draws is recorded, not chased to an exact target.
            inv_at = dict(inverter)
            ratio = (i_now / float(inverter.get("target_I_phase_rms_A") or i_now
                                   or 1.0))
            if _ccr._num(inv_at.get("v_phase_peak_V")):
                inv_at["v_phase_peak_V"] = float(inv_at["v_phase_peak_V"]) * ratio
            inv_at["target_I_phase_rms_A"] = i_now
        _ctl_st = controller.state() if controller is not None else None
        try:
            if controller is not None:
                controller.reseed(i_now)
            em_v = _em_run(body_at, coil_temp_c=coil_temp_c_guess,
                           magnet_temp_c=magnet_temp_c_guess, inverter=inv_at,
                           controller=controller)
            _map_v, _src_v = None, None
            if inv_at is not None:
                # THE RUN'S OWN LOSS MAP, handed over exactly as the loop's
                # passes hand theirs: a voltage-fed run's map is not findable
                # by a lookup keyed on a requested sine current.
                try:
                    _map_v, _src_v = _pwm_loss_map(
                        body_at, em_v, inv_at, coil_temp_c=coil_temp_c_guess,
                        magnet_temp_c=magnet_temp_c_guess,
                        controller=controller)
                except _NoLossMap:
                    em_v = _em_run(body_at, coil_temp_c=coil_temp_c_guess,
                                   magnet_temp_c=magnet_temp_c_guess,
                                   inverter=inv_at, controller=controller,
                                   fresh=True)
                    try:
                        _map_v, _src_v = _pwm_loss_map(
                            body_at, em_v, inv_at,
                            coil_temp_c=coil_temp_c_guess,
                            magnet_temp_c=magnet_temp_c_guess,
                            controller=controller)
                    except _NoLossMap as _nm:
                        raise _refuse(
                            "the S1 verification run left no per-element loss "
                            "map (%s)" % _nm.reason, ["drive"],
                            code="pwm_no_loss_map")
            _progress.update(phase="S1 verification %d/%d — thermal"
                                   % (k + 1, max_passes))
            field_v = _thermal_solve(
                body_at, cooling, coil_temp_c=coil_temp_c_guess,
                magnet_temp_c=magnet_temp_c_guess, rpm=rpm,
                n_steps_per_period=(inv_at["n_steps_per_period"]
                                    if inv_at is not None else None),
                em_map=_map_v, em_loss_source=_src_v)
        except HTTPException as exc:
            if controller is not None:
                controller.restore(_ctl_st)
            if not last:
                raise
            last["verified"] = False
            last["note"] = ("pass %d could not be solved (%s) — the last "
                            "verified state stands" % (k + 1, _detail_text(exc)))
            break
        _d_tj_v = None
        if controller is not None:
            # THE DEVICES AT THE S1 CURRENT: re-converged on this pass.
            _d_tj_v = controller.step(em_v, it=k + 1, phase="s1_verify")
        i_solved = _ccr._num((em_v.get("summary") or {}).get("I_phase_rms_A")) \
            or _ccr._num(em_v.get("I_phase_rms_solved_A")) or i_now
        comp = (field_v.get("components") or {})
        node = "winding" if limiting_part == "winding" else (
            "magnet" if limiting_part == "magnet" else limiting_part)
        actual = _ccr._num((comp.get(node) or {}).get("max"))
        miss = None if actual is None else round(actual - float(limit_c), 3)
        last = {"verified": (miss is not None and abs(miss) <= 3.0),
                "passes": k + 1, "miss_K": miss, "I_cont_A_rms": round(i_solved, 3),
                "em": em_v, "field": field_v,
                "coil_temp_c": coil_temp_c_guess,
                "magnet_temp_c": magnet_temp_c_guess,
                "actual_c": actual}
        if controller is not None:
            last["controller_t_j_c"] = round(float(controller.t_j_c), 2)
            last["controller_t_j_residual_K"] = (
                None if _d_tj_v is None else round(float(_d_tj_v), 3))
            last["controller_P_inverter_W"] = (
                (controller.solve or {}).get("losses") or {}).get("total_W")
        if last["verified"] or actual is None or k >= max_passes - 1:
            if actual is None:
                last["note"] = ("the verification map carries no %s reading, "
                                "so the miss could not be judged" % node)
            elif not last["verified"]:
                last["note"] = ("still %.1f K %s its limit after %d "
                                "verification pass(es) — the last verified "
                                "state stands" % (abs(miss), "over" if miss > 0
                                                  else "under", k + 1))
            break
        # ONE first-order correction, from the REAL map's reading.
        over_amb_actual = max(actual - amb, 1e-6)
        over_amb_limit = max(float(limit_c) - amb, 1e-6)
        i_now = max(i_now * math.sqrt(over_amb_limit / over_amb_actual), 0.01)
    return last


def _continuous_rating_for_loop(body: Dict[str, Any], *, cooling: Dict[str, Any],
                                rpm: float, coil_temp_c: float,
                                magnet_temp_c: Optional[float], duty: str,
                                mode: str,
                                time_to_limit: Optional[Dict[str, Any]] = None
                                ) -> Optional[Dict[str, Any]]:
    """The S1 rating for ``solve_to: continuous`` — ONE condition (this duty's
    own cooling, no patch), from the pass the loop already made.

    THE REFERENCE MUST BE A STEADY MAP, never the loop's own ``field``.  Bug
    found in production 2026-09-21: on a ``limits``-mode pass ``field`` is the
    machine translated onto the INSTANT it crosses its limit
    (``routes.thermal.rescale_map_to_nodes``) — the network the S1 search
    fits to that snapshot sits ON the limit at ``s ≈ 1`` BY CONSTRUCTION, so a
    machine 41 s from its winding class came back rated ABOVE its own duty
    current.  The fix is the same one the standalone
    ``POST /api/coupled/continuous_rating`` route already uses: solve a FRESH
    2-D STEADY thermal map — no ``_em_map`` override, ``_cr_solve_map`` looks
    the loop's own just-solved run up by its own IDENTITY (rpm, current,
    gamma, mesh, ``coil_temp_c``/``magnet_temp_c`` — this pass's own, not the
    body's original ones) exactly as this loop's ``_thermal_solve`` does every
    iteration — and CAPTURE the mesh-level loss map from that lookup for the
    rescale passes.

    This also fixes the TypeError the first version hit: the loop's own
    ``em`` (the bare transient result) does not carry the per-element loss
    mesh at all — ``field_snapshot=True`` keeps it in a SEPARATE snapshot
    store keyed by identity, not in the dict the loop passes around — so
    scaling it and handing it back as ``_em_map`` fed ``solve_thermal_field``
    arrays it could not do arithmetic on.  Letting the lookup find its own
    map, as the standalone route does, sidesteps that shape entirely.

    Never raises: a condition that cannot be rated (no run to rate from, no
    capacities, no part limits, a non-monotone map) is reported as
    ``{"ok": False, "refusal": ...}`` rather than failing the loop's own
    answer.
    """
    from motor_ai_sim.routes.thermal import _assignments, _dc_geometry, \
        _dc_side_areas
    from motor_ai_sim.thermal_capacities import CapacityError, part_capacities

    point = dict(_cr_point_kwargs(body))
    point["rpm"] = float(rpm)
    point["coil_temp_c"] = float(coil_temp_c)
    point["magnet_temp_c"] = (None if magnet_temp_c is None
                              else float(magnet_temp_c))

    capture: Dict[str, Any] = {}
    try:
        field0 = _cr_solve_map(point, cooling, capture=capture)
    except HTTPException as exc:
        return {"ok": False, "cooling": dict(cooling or {}),
                "cooling_label": _cooling_label(cooling),
                "refusal": {"error": (exc.detail if isinstance(exc.detail, str)
                                      else str(exc.detail)),
                           "error_code": "no_steady_map"}}
    em0 = capture.get("em")
    em_src0 = dict(capture.get("loss_source") or {})
    # THE SUMMARY comes from the canonical "last run" store — where every
    # other reader of this run's numbers reads them, and the shape
    # `coupled_continuous_rating.reference_point` was written against — never
    # the loop's own local `em["summary"]`, which does not carry the same
    # keys (the None-valued I_phase_rms_A / rpm / coil_temp_c / P_cu_W of the
    # production bug).
    summary = _cr_em_summary()
    if not summary:
        return {"ok": False, "cooling": dict(cooling or {}),
                "cooling_label": _cooling_label(cooling),
                "refusal": {"error": ("this backend holds no electromagnetic "
                                      "run summary, so there is no torque to "
                                      "rate"),
                           "error_code": "no_electromagnetic_run"}}
    try:
        geom, _ov = _dc_geometry(body.get("geo"))
        mats = _assignments()
        d_housing_m = float(geom.get("stator_diameter") or 0.0) * 1e-3
        caps = part_capacities(summary, mats, None)
    except CapacityError as exc:
        return {"ok": False, "cooling": dict(cooling or {}),
                "cooling_label": _cooling_label(cooling),
                "refusal": {"error": str(exc), "error_code": "no_capacities"}}
    except Exception:  # noqa: BLE001 — never fails the loop's own answer
        log.debug("coupled: continuous rating precheck failed", exc_info=True)
        return None
    side0 = _dc_side_areas(field0, summary, geom)

    _pass_n = {"n": 1}

    def _resolve(factor: float) -> Optional[Dict[str, Any]]:
        if em0 is None:
            return None
        _pass_n["n"] += 1
        _s_approx = math.sqrt(max(float(factor), 0.0))
        _progress.update(
            phase="continuous rating %d/%d — thermal at ≈%.2f × I"
                  % (_pass_n["n"], _ccr.MAX_MAP_PASSES, _s_approx))
        return _cr_solve_map(
            point, cooling, em_map=_cr_scaled_map(em0, factor),
            em_source={**em_src0, "note": (
                "the loop's own steady reference map, re-solved with the "
                "copper scaled by %.4f x (s^2 x rho_Cu(T_w)/rho_Cu(coil_ref)) "
                "— no electromagnetic solve" % float(factor))})

    def _refit(m: Mapping[str, Any]):
        return _dc_side_areas(dict(m), summary, geom), None

    _progress.update(phase="continuous rating 1/%d — steady map at the "
                           "pass's own losses" % _ccr.MAX_MAP_PASSES)
    try:
        block = _ccr.rate(
            thermal_result=field0, em_summary=summary, caps=caps,
            geometry=geom, cooling=cooling, side_areas=side0,
            d_housing_m=d_housing_m, duty=duty, mode=mode,
            resolve=_resolve, refit=_refit, magnet_grade=mats.get("magnet"))
    except _cdc.DutyCycleError as exc:
        return {"ok": False, "cooling": dict(cooling or {}),
                "cooling_label": _cooling_label(cooling),
                "refusal": {"error": str(exc),
                            "error_code": getattr(exc, "error_code",
                                                  "duty_cycle")}}
    # A FAILED RE-SOLVE must not leave a network-only pass looking trustworthy
    # (production bug, item 2): `_ccr.rate` itself only NOTES a failed
    # `resolve()` and keeps going on the map(s) it already has — right for an
    # ordinary "no map yet at this factor", wrong here because pass 0's own
    # map is exactly the one the whole bug above was found on.  So: no
    # electromagnetic solve happened here at all (`em0 is None`, `resolve`
    # always returns `None`) is never actually hit — `em0` comes from the SAME
    # lookup as `field0` — but a genuinely failed later pass (an exception
    # `_ccr.rate` caught and turned into a note) still leaves `converged`
    # false; the consistency guard below is what actually catches a bad
    # NUMBER, and a note starting "the loss map could not be re-solved" is
    # kept in `notes` for a reader either way.
    if not block.get("converged") and int(block.get("n_thermal_fem_solves")
                                          or 0) > 1:
        block = dict(block)
        block["trustworthy"] = False
        block["notes"] = list(block.get("notes") or []) + [
            "the fixed-point re-solve did not converge within the map-pass "
            "budget, so this number is not a settled rating"]
    block["ok"] = True
    block["headline"] = _ccr.headline(block)
    block["cooling_label"] = _cooling_label(cooling)
    block = _cr_consistency_guard(block, time_to_limit)
    if block.get("trustworthy") is False:
        block["headline"] = _ccr.headline(block)
    return block


def _cr_one(cond: Any, *, point: Dict[str, Any], defaults: Dict[str, Any],
            geom: Dict[str, Any], mats: Dict[str, Any], d_housing_m: float,
            duty: str, mode: str, limit_kw: Dict[str, Any]) -> Dict[str, Any]:
    """ONE condition, from its cooling patch to its rating block.

    Never raises for a reason that belongs to this row alone: a cooling the
    thermal router refuses (a still bore beside a jacket, a liquid with no flow)
    and a machine with no heat capacities are both REPORTED in the row, because
    one impossible condition must not cost the other five their answer.
    """
    from motor_ai_sim.routes.thermal import _dc_side_areas
    from motor_ai_sim.thermal_capacities import CapacityError, part_capacities

    cooling = cond.merged(defaults)
    capture: Dict[str, Any] = {}
    try:
        field0 = _cr_solve_map(point, cooling, capture=capture)
    except HTTPException as exc:
        return {"label": cond.label, "cooling": cooling, "ok": False,
                "refusal": (exc.detail if isinstance(exc.detail, dict)
                            else {"error": str(exc.detail)})}
    em0 = capture.get("em")
    em_src0 = dict(capture.get("loss_source") or {})
    summary = _cr_em_summary()
    if not summary:
        return {"label": cond.label, "cooling": cooling, "ok": False,
                "refusal": {"error": ("this backend holds no electromagnetic "
                                      "run summary, so there is no torque to "
                                      "rate"),
                            "error_code": "no_electromagnetic_run"}}
    try:
        caps = part_capacities(summary, mats, None)
    except CapacityError as exc:
        return {"label": cond.label, "cooling": cooling, "ok": False,
                "refusal": {"error": str(exc), "error_code": "no_capacities"}}
    side0 = _dc_side_areas(field0, summary, geom)

    def _resolve(factor: float) -> Optional[Dict[str, Any]]:
        if em0 is None:
            return None
        return _cr_solve_map(
            point, cooling, em_map=_cr_scaled_map(em0, factor),
            em_source={**em_src0, "note": (
                "the same cycle-averaged map with the copper scaled by %.4f x "
                "(s^2 x rho_Cu(T_w)/rho_Cu(coil_ref)) — no electromagnetic "
                "solve" % float(factor))})

    def _refit(m: Mapping[str, Any]):
        return _dc_side_areas(dict(m), summary, geom), None

    try:
        block = _ccr.rate(
            thermal_result=field0, em_summary=summary, caps=caps,
            geometry=geom, cooling=cooling, side_areas=side0,
            d_housing_m=d_housing_m, duty=duty, mode=mode,
            resolve=_resolve, refit=_refit,
            magnet_grade=mats.get("magnet"), bearing_temp_c=None, **limit_kw)
    except _cdc.DutyCycleError as exc:
        return {"label": cond.label, "cooling": cooling, "ok": False,
                "refusal": {"error": str(exc),
                            "error_code": getattr(exc, "error_code",
                                                  "duty_cycle")}}
    block["label"] = cond.label
    block["ok"] = True
    block["headline"] = _ccr.headline(block)
    block["loss_source"] = em_src0
    return block


@router.post("/continuous_rating")
@_JOBS.queued("coupled.continuous_rating", priority=_JOBS.Priority.DUTY,
              run_id_from=_JOBS.body_run_id("coupled"))
def continuous_rating(body: Dict[str, Any] = Body(default_factory=dict),
                      authorization: Optional[str] = Header(default=None)
                      ) -> Dict[str, Any]:
    """``POST /api/coupled/continuous_rating`` — the S1 rating, per cooling.

    Body: the operating point and mesh ``/run`` takes (they select the
    ELECTROMAGNETIC run this table stands on — one run, and no electromagnetic
    solve is ever started here), plus

      ``conditions``      a list of ``{label, ...cooling...}`` patches over the
                          loaded duty's own saved thermal setup;
      ``reference``       ``"last_run"`` (the default and the only value): the
                          electromagnetic run this backend already holds for the
                          point in the body;
      ``winding_limit_c`` / ``magnet_limit_c`` / ``bearing_limit_c``
                          override the cards, for a what-if;
      ``record``          ``true`` files the block under the loaded duty as
                          ``continuous_rating``.  The default is FALSE: a
                          what-if sweep over six coolings is not the duty's
                          answer, and nothing is written unless it is asked for.

    Every condition costs two or three 2-D thermal solves and NO electromagnetic
    solve.  The Thermal tab is left exactly where the user had it.
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.routes.simulation import _parse_mat_override
    from motor_ai_sim.routes.thermal import _assignments, _dc_geometry

    ref = body.get("reference")
    ref_sel = "explicit" if isinstance(ref, dict) else str(
        ref or "last_run").strip().lower()
    if ref_sel != "last_run":
        raise _refuse(
            "reference must be 'last_run': this route rates the machine that is "
            "LOADED, from the electromagnetic run this backend already holds for "
            "the point in the body. Naming another die / configuration / duty "
            "would load another machine into your workspace behind a read-only "
            "question — load it yourself and ask again.",
            ["reference"], code="reference_must_be_last_run")

    if body.get("mat") is not None:
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

    conditions = _cr_conditions(body)
    point = _cr_point_kwargs(body)
    defaults, defaults_why = _cr_duty_cooling()
    geom, _ov = _dc_geometry(body.get("geo"))
    mats = _assignments()
    d_housing_m = float(geom.get("stator_diameter") or 0.0) * 1e-3
    duty = _cycle_catalogue(body, "")[1]
    mode = str(body.get("mode") or "motor")
    limit_kw = {k: float(body[k]) for k in ("winding_limit_c", "magnet_limit_c",
                                            "bearing_limit_c")
                if body.get(k) not in (None, "")}

    t0 = time.time()
    rows: List[Dict[str, Any]] = []
    _progress.start(total=len(conditions), kind="coupled",
                    phase="continuous rating — %d cooling condition(s)"
                          % len(conditions),
                    composition="2-3 thermal solves each, no electromagnetic run")
    try:
        for i, cond in enumerate(conditions):
            if not cond.merged(defaults).get("cooling_mode"):
                raise _refuse(
                    "condition %r carries no cooling and there is no saved "
                    "thermal setup to patch: %s. Give the condition its whole "
                    "cooling (cooling_mode, ambient_temp, ...)."
                    % (cond.label, defaults_why),
                    ["conditions"], code="no_cooling")
            _progress.update(done=i, total=len(conditions),
                             phase="continuous rating — %s" % cond.label)
            rows.append(_cr_one(cond, point=point, defaults=defaults,
                                geom=geom, mats=mats, d_housing_m=d_housing_m,
                                duty=duty, mode=mode, limit_kw=limit_kw))
        _progress.update(done=len(conditions), total=len(conditions))
    finally:
        _progress.finish()

    out = {
        "ok": True,
        "conditions": rows,
        "defaults": {"cooling": defaults, "source": defaults_why},
        "duty": duty,
        "elapsed_s": round(max(time.time() - t0, 1e-3), 2),
        "note": ("one electromagnetic run pays for the whole table: every row is "
                 "that run's loss map re-solved under its own cooling with the "
                 "copper scaled to the current the machine can hold"),
    }
    if _record_wanted(body) and body.get("record") is not None:
        out["recorded"] = _cr_record(out)
    return out
