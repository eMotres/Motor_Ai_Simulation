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
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from motor_ai_sim import coupled_duty_cycle as _cdc
from motor_ai_sim import run_recording as _rr
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
_DRIVE_ALIASES = {"": "current", "sine": "current", "sinusoid": "current",
                  "current": "current", "pwm": "pwm", "pwm_voltage": "pwm",
                  "inverter": "pwm"}

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
    """``"current"`` (the sinusoid) or ``"pwm"`` (the inverter) — validated."""
    raw = str(body.get("drive") or "").strip().lower()
    d = _DRIVE_ALIASES.get(raw)
    if d is None:
        raise _refuse(
            "drive=%r is not something this loop can run: the electromagnetic "
            "half is either the sinusoidal CURRENT drive (\"sine\", the default) "
            "or the two-level inverter (\"pwm\"). An imposed sinusoidal voltage "
            "has no carrier to measure and is not offered here." % (body.get("drive"),),
            ["drive"], code="unknown_drive")
    return d


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

      * the carrier — this run's own ``f_switch_hz`` / the shared
        configuration's ``simulation.f_switch`` (``_effective_f_switch``),
        i.e. the DUTY's ``sim.fSwitch``;
      * the bus — the loaded machine's battery ``v_nom``;
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

    def _num(key, fallback, what):
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
        return float(fallback), _INV_SOURCES[key]

    f_c, f_c_src = _num("f_carrier_hz", _effective_f_switch(body),
                        "a carrier frequency")
    v_dc, v_dc_src = _num("v_dc_V", _pack_nominal_v(), "a DC link voltage")
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
        "sources": {"f_carrier_hz": f_c_src, "v_dc_V": v_dc_src,
                    "n_steps_per_period": steps_src,
                    "v_phase_peak_V": v1_src, "v_delta_deg": dl_src},
    }


#: Where each inverter field comes from when the request does not name it —
#: the words the refusal and the record use, so both say the same thing.
_INV_SOURCES = {
    "f_carrier_hz": "the duty's sim.fSwitch (the shared configuration)",
    "v_dc_V": "the machine's battery v_nom",
    "n_steps_per_period": ("the study's rule: %d FEM steps per carrier period"
                           % PWM_SAMPLES_PER_CARRIER),
    "v_phase_peak_V": "the duty's saved summary (V1_seed_peak_V)",
}
_INV_HINTS = {
    "f_carrier_hz": ("set simulation.f_switch on the machine (the duty's "
                     "sim.fSwitch)"),
    "v_dc_V": "give the machine a battery with a v_nom",
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
    """
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
    """
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


def _em_run(body: Dict[str, Any], *, coil_temp_c: float,
            magnet_temp_c: Optional[float],
            inverter: Optional[Dict[str, Any]] = None,
            fresh: bool = False) -> Dict[str, Any]:
    """ONE Electromagnetic run — the same call the Run button makes.

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
              # shut the door: a coupled iteration is never a lookup.
              ledger=False, fresh=bool(fresh),
              restore=False, ledger_probe=False)
    if inverter is None:
        # THE LOOP's spelling of the drive is not the RUN's: this route accepts
        # "sine" as a synonym for the sinusoidal current source, and the
        # transient route knows only "current" — forwarding the synonym would
        # 422 with "unknown excitation source 'sine'".
        kw["drive"] = "current"
        return _call_filtered(get_fem_transient, kw) or {}
    # ── THE INVERTER ────────────────────────────────────────────────────────
    # The settle SCHEDULE is an environment switch of the solver (the B5 fix's
    # own `SB_PWM_COARSE_SETTLE`), so it is set around this one call and put
    # back afterwards — a coupled run must not leave the process configured
    # for the next request.
    kw.update(_pwm_run_kwargs(inverter))
    _prev = os.environ.get("SB_PWM_COARSE_SETTLE")
    os.environ["SB_PWM_COARSE_SETTLE"] = _PWM_SCHEDULES[inverter["schedule"]]
    try:
        return _call_filtered(get_fem_transient, kw) or {}
    finally:
        if _prev is None:
            os.environ.pop("SB_PWM_COARSE_SETTLE", None)
        else:
            os.environ["SB_PWM_COARSE_SETTLE"] = _prev


def _pwm_loss_map(body: Dict[str, Any], em: Dict[str, Any],
                  inv: Dict[str, Any], *, coil_temp_c: float,
                  magnet_temp_c: Optional[float]) -> Tuple[Dict[str, Any],
                                                           Dict[str, Any]]:
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
        snap_drive="pwm_voltage",
        snap_excitation=_pwm_snap_excitation(inv),
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
        "drive": "pwm",
        "inverter": _inverter_record(em, inv),
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
                   em_loss_source: Optional[Dict[str, Any]] = None
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
        th._remember_last(
            "field", out,
            th._field_params(**{k: v for k, v in kw.items()
                                if k not in ("geo", "bearing_temp_c",
                                             "_em_map", "_em_loss_source")}),
            out.get("geometry_fingerprint"))
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


def _effective_f_switch(body: Dict[str, Any]) -> Optional[float]:
    """THE CARRIER OF THE RUN BEING SOLVED, for the excitation tables.

    The ring modes and the whirl branches are compared against slot passing and
    against the inverter carrier, and the carrier is a SETTING of the duty, not
    an output of the current-drive solve this loop makes.  Resolved once, with
    the run, and passed down explicitly:

      * ``f_switch_hz`` / ``f_switch`` in the body — what the caller is solving
        this duty at (the catalogue's ``sim.fSwitch``, which ``routes/family``
        exposes as ``f_switch_hz``);
      * failing that, the shared configuration's ``simulation.f_switch`` — the
        block the ▶ route PATCHes from the duty before the electromagnetic run,
        i.e. this run's own carrier.

    Read HERE rather than inside the mechanical route, so the value that lands
    in the modal record is the one this run was made with.  Before 2026-09-14
    the modal step read the process-global itself, at whatever moment it ran,
    and the Ø85's 48 kHz ended up in the Ø200 L155 rated duty's ring-mode
    table.  ``None`` = no PWM line is drawn.
    """
    for k in ("f_switch_hz", "f_switch"):
        if body.get(k) is None:
            continue
        try:
            v = float(body.get(k))
        except (TypeError, ValueError):
            continue
        return v if v > 0.0 else None
    try:
        from motor_ai_sim.config import get_config
        v = ((get_config() or {}).get("simulation") or {}).get("f_switch")
        return float(v) if v and float(v) > 0.0 else None
    except Exception:  # noqa: BLE001
        return None


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

    _preflight(body, max_iter=max_iter)

    issue = cooling_issue(settings)
    if issue is not None:
        raise _refuse(
            "the Thermal tab's cooling cannot be solved: %s. Set it on the "
            "Thermal tab (or send thermal_settings) and run again." % issue,
            ["thermal_settings"], code="no_thermal_boundary")
    cooling = cooling_fields(settings)
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
                if drive == "pwm" else None)
    # …and the carrier, resolved WITH the run and before anything else can move
    # the shared configuration under it (2026-09-14).  It reaches the modal and
    # rotordynamic steps explicitly and is stored in their records.
    # …and on a PWM run it is the carrier THIS run is actually fed by, which is
    # the one the ring modes and the whirl branches must be compared against.
    fsw_eff = (float(inverter["f_carrier_hz"]) if inverter
               else _effective_f_switch(body))

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
        for it in range(1, max_iter + 1):
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
                em = _em_run(body, coil_temp_c=t_coil, magnet_temp_c=t_mag,
                             inverter=inverter)
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
                        magnet_temp_c=t_mag)
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
                                     fresh=True)
                    finally:
                        _ml.BEARING_TEMP_C.reset(_tok2)
                    try:
                        _em_map, _em_src = _pwm_loss_map(
                            body, em, inverter, coil_temp_c=t_coil,
                            magnet_temp_c=t_mag)
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
                                   em_map=_em_map, em_loss_source=_em_src)

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
            if (abs(d_coil) < tol and abs(d_mag) < tol
                    and (d_brg is None or abs(d_brg) < BEARING_TOL_K)
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
            t_coil = float(t_coil) + damping_eff * d_coil
            if t_mag is not None and t_mag_out is not None:
                t_mag = float(t_mag) + damping_eff * d_mag
        # THE BUDGET RAN OUT WITH THE POINT STILL OFF.  The last pass is a solved
        # state and is KEPT — the temperatures, the map and the mechanics all
        # belong to it — but it is not the duty's operating point, and a reader
        # must not have to divide two numbers in the record to find that out.
        # Said as a warning with its own code, like every other "stopped early"
        # here.  Never over a refusal or a runaway: those stopped the loop for a
        # harder reason and own the message.
        if point_off and refusal is None and not runaway:
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
    except _LoopCancelled:
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
    n_em = len(history)
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
        "drive": ("pwm" if inverter is not None else "sine"),
        **({"inverter": _inverter_record(em, _record_inverter(inverter, v1_ran),
                                         ripple_quotable=ripple_quotable)}
           if inverter is not None else {}),
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
        # `**` rather than fixed keys: a machine with no bearings grows no
        # mechanical keys at all, which is what "absent, not zero" means in a
        # payload.
        **_mech_top,
        "magnet_temp_max_c": next(
            (r["T_magnet_max"] for r in reversed(history)
             if r.get("T_magnet_max") is not None), None),
        "iterations": n_em,
        "em_runs": n_em,
        "converged": bool(converged),
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
        **({"warning_code": refusal_code} if refusal_code else {}),
        "warning": (refusal if refusal else
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
    log.info("coupled: %d EM run(s), winding %.1f degC, magnets %s, %s",
             n_em, block["coil_temp_c"],
             "n/a" if block["magnet_temp_c"] is None
             else "%.1f degC" % block["magnet_temp_c"],
             "converged" if converged else "NOT converged")
    return out
