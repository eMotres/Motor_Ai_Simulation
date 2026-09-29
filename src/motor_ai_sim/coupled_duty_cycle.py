"""THE CYCLE INSIDE THE COUPLED LOOP — an S2/S3 duty solved for its REGIME.

WHY THIS MODULE EXISTS (user, 2026-09-16: *"каплинг на цикле S3 подбирает
скважность для того чтобы можно было влезть в лимиты"*)
=========================================================================
Until today the coupled EM↔thermal loop REFUSED an impulse duty, and the refusal
was right about the physics and wrong about the question.  The loop's method is
to iterate the electromagnetic run and the thermal solve until the winding and
the magnet stop moving; on a duty that runs 25 % of a 60 s cycle that fixed point
is the temperature the machine would reach if the pull never ended — several
hundred kelvin above anything the cycle sees.  So the answer used to be "ask the
Thermal tab", and the user's answer to that is: no, the loop itself must FIND the
duty ratio at which the machine stays inside its limits, and report the
temperatures THERE.

WHAT ONE ITERATION BECOMES
--------------------------
    EM run at (T_coil, T_magnet, T_bearing)   → the loss map, as always
    thermal solve of that map                 → the STEADY field, as always
    ── and then, on an impulse duty ──
    fit the lumped cycle network to that very map     (`thermal_duty_cycle`)
    search the duty ratio ED whose PERIODIC peak respects every limit
    T_coil'   = the winding node's peak over the cycle AT THAT ED
    T_magnet' = the magnet  node's peak over the same cycle

so the loop closes on the ED and on the temperatures at that ED together: a
hotter winding makes more copper loss, which lowers the allowable ED, which
lowers the temperature the next electromagnetic run is solved at.  Converged
means BOTH — the temperatures inside the loop's own tolerance and the found ED
stable to :data:`ED_TOL_PCT` of a point between passes.

WHY THE PEAK AND NOT THE MEAN.  The steady loop feeds back the winding AVERAGE
because ``coil_temp_c`` scales a bulk resistivity; that rule is about SPACE and
is unchanged here — what travels back is the lumped winding NODE, which is a
spatial mean, while the hot spot (the node plus the calibration map's own
max − mean offset) is carried beside it for the limit and never fed back.  In
TIME it is the peak, because the on-time is when the machine is doing the work
the run describes: an electromagnetic run billed at the cycle MEAN would
under-state ρ_Cu over the pull and over-state the torque the machine still has
at the end of it.

WHAT IS FOUND, PER KIND
-----------------------
  * **S3** — the allowable ED at the cycle's own period, the temperatures there,
    the limiting part, and whether the ED the request asked for fits under it;
  * **S2** — the allowable ON-TIME from the start temperature (and from the
    rated state when there is one), and the temperatures at the end of it.

NEVER A FAKE CONVERGENCE.  When even the shortest pull the search tries puts a
part over its limit there is no feasible ED at all: the model says so
(``feasible: false``), the single-pulse time from cold is reported instead — the
same number the duty-cycle tool prints — and the loop carries that as its
warning rather than a settled-looking pair of temperatures.

ONE MODEL, ONE ANSWER.  Every number here comes out of ``thermal_duty_cycle``:
the same network fit, the same bisection, the same limits, the same integrator
``POST /api/thermal/duty_cycle`` uses.  The only difference is the calibration
map — the tool fits a STORED map of a stored point, the loop fits the map it
just solved — and that is the whole point of doing it inside the loop.

Pure: no FastAPI, no route, no I/O.  Everything expensive here is an ODE on four
nodes; the costly half of a coupled pass is still the finite-element run above
it.  Every refusal is a :class:`thermal_duty_cycle.DutyCycleError` carrying the
``error_code`` the route puts in its 422.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field as _field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import thermal_duty_cycle as tdc
from motor_ai_sim.thermal_capacities import NODES
from motor_ai_sim.thermal_duty_cycle import DutyCycleError

#: The duty-cycle kinds that are an IMPULSE — the machine does not sit at the
#: point long enough for a steady temperature to be the answer.
IMPULSE_KINDS: Tuple[str, ...] = ("S2", "S3")

#: The env var that switches everything above BACK ON, and the values that count
#: as "on".  Off is the default (owner, 2026-09-17: *«давай пока уберём duty
#: cycle из Thermal, оставим только стандартный каплинг»*).
DUTY_CYCLE_ENV = "DUTY_CYCLE_ENABLED"
_ON = ("1", "true", "yes", "on")


def enabled() -> bool:
    """Is the coupled loop's duty-cycle search switched on?

    OFF BY DEFAULT since 2026-09-17.  With the feature off the coupled loop runs
    the STANDARD loop for every duty: a stored S2/S3 block is read as the
    continuous point it was read as before cycles existed — the temperatures
    iterate to their fixed point, no ED is searched, and no ``duty_cycle``
    sub-block is written into the answer or filed as a record.
    ``DUTY_CYCLE_ENABLED=1`` restores the behaviour of 2026-09-16 exactly;
    nothing in this module was removed.

    WHAT THE FLAG DOES NOT TOUCH, on purpose: ``POST /api/thermal/duty_cycle``
    (the standalone tool is how a cycle is still asked about), the heat paths and
    the ``robotics`` cooling mode (a different feature that merely arrived in the
    same week), and every duty-cycle record already filed — the report prints its
    cycle section when a RECORD exists, so stored answers stay readable.

    Read from the environment on every call rather than at import: a flag that is
    frozen into a module at import time cannot be flipped in a test, and cannot
    be flipped by an operator without a restart of something that is not the
    process they just edited the env of.
    """
    return str(os.environ.get(DUTY_CYCLE_ENV, "")).strip().lower() in _ON


#: How far the found duty ratio may move between two passes and still count as
#: settled [percentage POINTS].  One point of ED on a 60 s cycle is 0.6 s of
#: on-time — below the resolution of anything downstream (the report prints ED
#: to a tenth) and far inside what the lumped model itself claims.
ED_TOL_PCT = 1.0

#: …and the same tolerance on an S2's allowable on-time, as a FRACTION of it: a
#: pull whose length moves by under a per cent between passes has settled.
T_ON_TOL_FRACTION = 0.01

#: Integration resolution INSIDE the loop (per segment) and for the final
#: record.  The search runs a dozen periodic solves per pass, so the loop takes
#: the coarser one; the record — which is what the report draws — takes the
#: finer.  Both are far above the ~8 samples the model needs to resolve a
#: segment, and the gap between them on the L13 is under 0.2 K.
LOOP_SAMPLES = 24
FINAL_SAMPLES = 40

#: Bisection steps of the ED search inside a pass.  12 is 0.02 % on a 0-100 %
#: bracket — two orders finer than :data:`ED_TOL_PCT`, so the loop's own
#: convergence can never be limited by the search's.
LOOP_ED_ITERS = 12

#: The cycle lengths the ED-vs-period curve is drawn at, in the final record.
ED_CYCLE_LENGTHS_S = tdc.ED_CYCLE_LENGTHS_S


# ---------------------------------------------------------------------------
# The model — everything one pass needs, fitted to that pass's own map
# ---------------------------------------------------------------------------

@dataclass
class CycleModel:
    """The lumped cycle of ONE coupled pass: the network fitted to that pass's
    thermal map, the capacities of the machine that made it, and the profile the
    search varies.

    ``profile`` is built at the REQUESTED split — the ED the duty states, or the
    model's own seed when it states none — and it is never the answer; the
    answer is the profile at the ED :func:`solve_regime` finds.
    """
    kind: str
    duty: str
    network: tdc.Network
    caps: Dict[str, Any]
    profile: tdc.Profile
    limits: Dict[str, float]
    cycle_s: float
    requested_ed_pct: Optional[float] = None
    requested_t_on_s: Optional[float] = None
    magnet_limit_source: str = ""
    rated_state_c: Dict[str, float] = _field(default_factory=dict)
    side_area_basis: Optional[Dict[str, Any]] = None
    calibration_note: str = ""

    @property
    def hot_spot_offset_k(self) -> float:
        return float(self.network.hot_spot_offset_k)


def _kind_of(block: Optional[Mapping[str, Any]]) -> str:
    return str((block or {}).get("kind") or "S1").strip().upper()


def is_impulse(block: Optional[Mapping[str, Any]]) -> bool:
    """Is this cycle block one the loop must solve as a REGIME rather than as a
    settled point?  An absent block is the continuous duty every machine was
    taken to be before cycles existed, and that is not a refusal of anything."""
    return bool(block) and _kind_of(block) in IMPULSE_KINDS


def magnet_limit(block: Optional[Mapping[str, Any]],
                 body_value: Any = None) -> Tuple[Optional[float], str]:
    """``(limit °C, where it came from)`` for the magnets — or ``(None, …)``.

    The request wins, then the cycle block's own ``magnet_limit_c``.  With
    neither, the magnets are NOT judged — exactly as ``POST /api/thermal/
    duty_cycle`` does not judge them without one.  Deliberately NOT read off the
    magnet card here, although the cards do carry ``max_working_temp_c``: the
    coupled loop and the duty-cycle tool must answer the same question about the
    same machine, and a limit one of them applies silently is two different
    answers with one name.
    """
    for v, where in ((body_value, "the request (magnet_limit_c)"),
                     ((block or {}).get("magnet_limit_c"),
                      "the duty's cycle block (magnet_limit_c)")):
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f, where
    return None, ("no magnet limit was given, so the magnets are not judged "
                  "(pass magnet_limit_c to judge them)")


def build_model(*, block: Mapping[str, Any],
                duties: Sequence[Mapping[str, Any]],
                duty_name: str, em_summary: Mapping[str, Any],
                thermal_result: Mapping[str, Any],
                geometry: Optional[Mapping[str, Any]] = None,
                cooling: Optional[Mapping[str, Any]] = None,
                materials: Optional[Mapping[str, Any]] = None,
                part_states: Optional[Mapping[str, Any]] = None,
                magnet_limit_c: Optional[float] = None,
                magnet_limit_source: str = "",
                side_areas: Optional[Mapping[str, Any]] = None,
                d_housing_m: Optional[float] = None,
                magnet_k_w_per_mk: Optional[float] = None,
                rated_state_c: Optional[Mapping[str, float]] = None,
                ) -> CycleModel:
    """Fit this pass's cycle model to the map this pass just solved.

    ``em_summary`` and ``thermal_result`` are the coupled iteration's OWN
    answers — not a duty's stored ones.  That is the whole difference from
    ``POST /api/thermal/duty_cycle``, which calibrates on a stored map of a
    stored point: here the calibration map IS the map of the point being
    iterated, at the temperature this pass solved it at, so the conductances,
    the watts and the hot-spot offset all move with the loop.

    Refuses by NAME (:class:`DutyCycleError`) — a cycle that quietly became a
    different cycle is the class of bug this project does not ship.
    """
    from motor_ai_sim.thermal_capacities import CapacityError, part_capacities

    blk = dict(block or {})
    kind = _kind_of(blk)
    if kind not in IMPULSE_KINDS:
        raise DutyCycleError(
            "duty_cycle_not_impulse",
            "this cycle is %s, which the coupled loop solves as a settled point "
            "and not as a regime." % kind,
            "Only S2 and S3 duties have a regime to find.")

    # ── the profile, with THIS pass's losses standing in for the duty's ──────
    # `normalise_spec` reads every segment's watts off the named duty's saved
    # summary; the on-segment of this cycle is the run the loop just made, so
    # the loaded duty's entry is replaced by one carrying that run — and the map
    # beside it, which is what splits the solid loss and puts the bearing
    # friction where the steady solve put it.
    entries: List[Dict[str, Any]] = []
    seen = False
    for d in (duties or ()):
        e = dict(d or {})
        if str(e.get("name") or "") == str(duty_name):
            e["summary"] = dict(em_summary or {})
            e["rpm"] = float((em_summary or {}).get("rpm") or e.get("rpm") or 0.0)
            seen = True
        entries.append(e)
    if not seen:
        entries.append({"name": str(duty_name),
                        "summary": dict(em_summary or {}),
                        "rpm": float((em_summary or {}).get("rpm") or 0.0)})

    on_duty = str(blk.get("duty") or duty_name)
    if on_duty != str(duty_name):
        raise DutyCycleError(
            "duty_cycle_point_mismatch",
            "this cycle's powered segment names duty %r while the coupled loop "
            "is solving %r, so the run in hand is not the run the cycle is made "
            "of." % (on_duty, duty_name),
            "Run the coupled loop on %r, or drop the cycle's own `duty` so it "
            "means the duty it is saved on." % (on_duty,))
    # The cycle is calibrated on THIS duty whatever the block says: the map in
    # hand is this point's, and fitting it under another duty's name would file
    # a network against a machine state nobody solved.
    spec = dict(blk, calibration_duty=str(duty_name))
    calib_note = ""
    if blk.get("calibration_duty") and str(blk["calibration_duty"]) != str(duty_name):
        calib_note = ("the cycle names %r as its calibration duty; inside the "
                      "coupled loop the network is fitted to the map this run "
                      "just solved, which is %r's own"
                      % (str(blk["calibration_duty"]), str(duty_name)))

    profile = tdc.normalise_spec(
        spec, entries,
        thermal_by_duty={str(duty_name): dict(thermal_result or {})},
        default_duty=str(duty_name))

    # ── the capacities of the machine that made this run ────────────────────
    try:
        caps = part_capacities(em_summary, materials, part_states)
    except CapacityError as exc:
        raise DutyCycleError(
            "duty_cycle_no_capacity", str(exc),
            "A duty cycle is an answer about TIME, and time needs the machine's "
            "heat capacity.")

    cool = dict(cooling or {})
    t_amb = cool.get("ambient_temp")
    if t_amb is None:
        t_amb = (thermal_result or {}).get("ambient_temp")
    net = tdc.network_from_steady(
        thermal_result,
        mount_g_w_per_k=float(cool.get("mount_g_w_per_k") or 0.0),
        mount_temp_c=cool.get("mount_temp_c"),
        t_ambient_c=(None if t_amb is None else float(t_amb)),
        side_areas=side_areas,
        d_housing_m=(None if d_housing_m is None else float(d_housing_m)),
        emissivity=cool.get("emissivity"),
        geometry=geometry,
        magnet_k_w_per_mk=magnet_k_w_per_mk,
        calibration_duty=str(duty_name))

    return CycleModel(
        kind=kind, duty=str(duty_name), network=net, caps=caps, profile=profile,
        limits=tdc.default_limits(magnet_limit_c),
        cycle_s=float(profile.cycle_s),
        requested_ed_pct=(float(profile.ed_pct)
                          if (kind == "S3" and profile.ed_given
                              and profile.ed_pct is not None) else None),
        requested_t_on_s=(float(profile.t_on_s)
                          if (kind == "S2" and profile.t_on_s) else None),
        magnet_limit_source=str(magnet_limit_source),
        rated_state_c={str(k): float(v)
                       for k, v in (rated_state_c or {}).items()},
        side_area_basis=((side_areas or {}).get("basis")
                         if isinstance(side_areas, Mapping) else None),
        calibration_note=calib_note)


# ---------------------------------------------------------------------------
# The search — what the loop asks of the model once per pass
# ---------------------------------------------------------------------------

def _peak_pair(at: Optional[Mapping[str, Any]]) -> Tuple[Optional[float],
                                                         Optional[float]]:
    """``(winding node peak, magnet node peak)`` out of an ``at_allowable``
    block — the two temperatures the electromagnetic half is re-solved at."""
    if not at:
        return None, None
    peak = dict((at.get("peak_c") or {}))

    def _n(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    return _n(peak.get("winding")), _n(peak.get("magnet"))


def _state_at(model: CycleModel, t_on_s: float,
              T0: Optional[Mapping[str, float]] = None,
              *, samples: int = LOOP_SAMPLES) -> Dict[str, Any]:
    """Integrate ONE pull of ``t_on_s`` and report where it ended.

    The S2 answer's own temperatures: a pull that is allowed to last 26.6 s ends
    at whatever the network says after 26.6 s, and that end state — not a
    settled one, which this point never reaches — is what the next
    electromagnetic run is solved at.
    """
    seg = replace(model.profile.segments[0], t_s=float(t_on_s))
    prof = replace(model.profile, segments=(seg,), cycle_s=float(t_on_s),
                   t_on_s=float(t_on_s))
    tr = tdc.integrate_profile(prof, model.network, model.caps, T0, n_cycles=1,
                               samples_per_segment=max(8, int(samples)))
    end = tr.final_state()
    return {
        "winding_hot_peak_c": round(max(tr.hot_spot_c), 2),
        "winding_hot_mean_c": round(sum(tr.hot_spot_c) / len(tr.hot_spot_c), 2),
        "magnet_peak_c": round(max(tr.T_c["magnet"]), 2),
        "peak_c": {n: round(max(tr.T_c[n]), 2) for n in NODES},
        "mean_c": {n: round(sum(tr.T_c[n]) / len(tr.T_c[n]), 2) for n in NODES},
        "end_c": {n: round(float(end[n]), 2) for n in NODES},
        "_trace": tr,
    }


#: The first horizon an S2 question is asked over [s], and the floor under it.
#: TWO STAGES, and the reason is measured (L13 peak, 2026-09-16): the model's own
#: horizon is ten time constants — 5 264 s on this joint — and a peak point held
#: for 5 264 s is a machine climbing past 700 °C, which is where the radiation
#: term makes the integration expensive (332 s of wall clock for an answer that
#: happens at 19.8 s).  So the question is asked first over a horizon a pull
#: could plausibly last; only when NOTHING is reached in it does the full
#: horizon run, and a machine that reaches no limit is a settling one, which is
#: cheap to integrate.  The answer is identical either way — the hit is the hit.
_S2_FIRST_HORIZON_FACTOR = 10.0
_S2_FIRST_HORIZON_FLOOR_S = 120.0


def _time_to_limit(model: CycleModel,
                   T0: Optional[Mapping[str, float]] = None) -> Dict[str, Any]:
    """:func:`thermal_duty_cycle.time_to_limit`, asked twice at most."""
    short = max(_S2_FIRST_HORIZON_FLOOR_S,
                _S2_FIRST_HORIZON_FACTOR * float(model.profile.segments[0].t_s))
    rec = tdc.time_to_limit(model.profile, model.network, model.caps,
                            limits=model.limits, T0=T0, t_max_s=short)
    if rec.get("s2_time_to_limit_s") is not None:
        return rec
    return tdc.time_to_limit(model.profile, model.network, model.caps,
                             limits=model.limits, T0=T0)


def _s2_times(model: CycleModel) -> Dict[str, Any]:
    """The S2 questions, on the network in hand: how long may it pull from the
    start temperature, and how long from the rated state when there is one."""
    out: Dict[str, Any] = {}
    cold = _time_to_limit(model)
    out.update({
        "s2_time_to_limit_s": cold["s2_time_to_limit_s"],
        "s2_limiting_part": cold["s2_limiting_part"],
        "s2_horizon_s": cold["t_horizon_s"],
        "s2_winding_hot_end_c": cold["winding_hot_end_c"],
        "s2_end_state_c": cold["end_state_c"],
        "s2_note": cold["note"],
    })
    if model.rated_state_c:
        warm = _time_to_limit(model, T0=model.rated_state_c)
        out.update({
            "s2_from_rated_s": warm["s2_time_to_limit_s"],
            "s2_from_rated_part": warm["s2_limiting_part"],
            "s2_from_rated_start_c": {k: round(float(v), 2)
                                      for k, v in model.rated_state_c.items()},
            "s2_from_rated_note": (
                "the same pull, started from the settled state of the point "
                "this loop is solving instead of from the start temperature — "
                "what the machine has left when it has already been working.  "
                "%s" % warm["note"]),
        })
    return out


def solve_regime(model: CycleModel, *, samples_per_segment: int = LOOP_SAMPLES,
                 with_curve: bool = False,
                 cycle_lengths: Optional[Sequence[float]] = None,
                 iters: int = LOOP_ED_ITERS) -> Dict[str, Any]:
    """THE ANSWER of one pass: the regime this machine can hold at this point.

    Returns a block with, always, ``kind``, ``feasible``, ``fits_requested``,
    ``limiting_part``, the pair the loop feeds back (``coil_temp_c`` /
    ``magnet_temp_c``) and a one-sentence ``note``; plus, per kind, the
    allowable ED (S3) or the allowable on-time (S2) and the temperatures there.

    ``with_curve`` adds what only the FINAL pass needs and no intermediate one
    reads: the winding-peak-vs-ED curve, the S2 times, and the cycle at the
    requested ED when one was asked for.  Inside the loop it is off — twenty
    extra periodic solves per pass buy nothing the feedback uses.

    ``cycle_lengths`` adds the allowable ED over a SPAN of periods, and it is
    off unless a caller names the periods: it is the one expensive thing in this
    module (see the note at the call site).
    """
    if model.kind == "S3":
        return _regime_s3(model, samples_per_segment=samples_per_segment,
                          with_curve=with_curve, cycle_lengths=cycle_lengths,
                          iters=iters)
    return _regime_s2(model, samples_per_segment=samples_per_segment,
                      with_curve=with_curve)


def _regime_s3(model: CycleModel, *, samples_per_segment: int,
               with_curve: bool, cycle_lengths: Optional[Sequence[float]],
               iters: int) -> Dict[str, Any]:
    # WARM-STARTED (2026-09-16).  The search runs inside every pass of a loop
    # whose other half is a finite-element transient, so the path each cycle map
    # takes to its fixed point is worth money: seeding it from the neighbouring
    # ED's converged start state is the same answer at a fraction of the cycles.
    ed = tdc.allowable_ed(model.profile, model.network, model.caps,
                          limits=model.limits, iters=iters,
                          samples_per_segment=max(8, int(samples_per_segment)),
                          with_curve=bool(with_curve), warm_start=True)
    allowable = ed.get("ed_allowable_pct")
    asked = model.requested_ed_pct
    at = ed.get("at_allowable")
    feasible = bool(allowable) and float(allowable) > 0.0
    out: Dict[str, Any] = {
        "kind": "S3",
        "duty": model.duty,
        "cycle_s": round(float(model.cycle_s), 4),
        "ed_cycle_s": round(float(model.cycle_s), 4),
        "ed_requested_pct": asked,
        "ed_allowable_pct": allowable,
        "t_on_allowable_s": (round(float(model.cycle_s) * float(allowable)
                                   / 100.0, 3) if feasible else None),
        "limiting_part": ed.get("limiting_part"),
        "limits_c": {k: float(v) for k, v in model.limits.items()},
        "magnet_limit_source": model.magnet_limit_source,
        "hot_spot_offset_K": round(model.hot_spot_offset_k, 2),
        "feasible": feasible,
        "at_allowable": at,
        "ed_note": ed.get("note"),
        "ed_curve": ed.get("ed_curve") or [],
    }
    if feasible:
        t_coil, t_mag = _peak_pair(at)
        out["fits_requested"] = (None if asked is None
                                 else bool(float(asked) <= float(allowable)
                                           + 1e-9))
        out["note"] = _s3_note(model, allowable, asked, ed.get("limiting_part"),
                               at)
    else:
        # EVEN ED → 0 IS OVER THE LIMIT.  There is no intermittent regime at
        # this point; what the machine HAS is one pull, and the honest pair to
        # carry on with is where that pull ends (the same number the duty-cycle
        # tool prints as "S2 from cold").
        cold = _time_to_limit(model)
        t_hit = cold.get("s2_time_to_limit_s")
        out["fits_requested"] = (None if asked is None else False)
        out["s2_time_to_limit_s"] = t_hit
        out["s2_limiting_part"] = cold.get("s2_limiting_part")
        out["s2_note"] = cold.get("note")
        if t_hit:
            st = _state_at(model, float(t_hit), samples=samples_per_segment)
            st.pop("_trace", None)
            out["at_allowable"] = st
            t_coil, t_mag = _peak_pair(st)
        else:
            t_coil = t_mag = None
        out["note"] = (
            "no duty ratio is allowable at this operating point: even the "
            "shortest pull the search tries puts the %s over %s. One pull from "
            "%s lasts %s."
            % (ed.get("limiting_part") or "winding",
               _limit_words(model, ed.get("limiting_part")),
               _start_words(model),
               ("%.1f s" % float(t_hit)) if t_hit else
               "longer than the horizon this model integrates"))
    out["coil_temp_c"] = (None if t_coil is None else round(float(t_coil), 2))
    out["magnet_temp_c"] = (None if t_mag is None else round(float(t_mag), 2))
    at_now = out.get("at_allowable") or {}
    out["winding_hot_peak_c"] = at_now.get("winding_hot_peak_c")
    out["magnet_peak_c"] = (at_now.get("magnet_peak_c")
                            or (at_now.get("peak_c") or {}).get("magnet"))
    if with_curve:
        out.update(_s2_times(model))
        if cycle_lengths:
            # OPT-IN, AND OFF BY DEFAULT (2026-09-16).  The allowable ED at one
            # period costs ~5 s on the Ø85 joint; the same answer over the five
            # standard periods costs MINUTES, because a period far below the
            # machine's time constant needs a hundred cycle-map iterations to
            # settle and the search runs a dozen of them.  The Thermal tab's own
            # duty-cycle run is where that curve belongs — it is asked for once,
            # deliberately — while the coupled loop must not add a quarter of an
            # hour to a run the user is watching.  `ed_cycle_lengths_s` in the
            # body turns it on.
            try:
                out["ed_vs_cycle"] = tdc.ed_vs_cycle(
                    model.profile, model.network, model.caps,
                    limits=model.limits, cycle_lengths=list(cycle_lengths),
                    samples_per_segment=max(8, min(int(samples_per_segment), 24)),
                    warm_start=True)
            except DutyCycleError:
                out["ed_vs_cycle"] = []
        if asked is not None and feasible:
            # WHAT THE ASKED-FOR RATIO ACTUALLY DOES.  The loop converges on the
            # allowable one; the request is a question, and the record answers
            # it with the cycle the user would get rather than making anybody
            # interpolate the curve.
            try:
                rec = tdc.periodic_steady_state(
                    model.profile.with_ed(float(asked)), model.network,
                    model.caps,
                    samples_per_segment=max(8, int(samples_per_segment)))
                rec.pop("trace", None)
                out["at_requested"] = {
                    "winding_hot_peak_c": rec.get("winding_hot_peak_c"),
                    "winding_hot_mean_c": rec.get("winding_hot_mean_c"),
                    "magnet_peak_c": (rec.get("peak_c") or {}).get("magnet"),
                    "peak_c": rec.get("peak_c"), "mean_c": rec.get("mean_c")}
            except DutyCycleError as exc:
                out["at_requested"] = {"note": exc.message}
    return out


def _regime_s2(model: CycleModel, *, samples_per_segment: int,
               with_curve: bool) -> Dict[str, Any]:
    times = _s2_times(model)
    t_allow = times.get("s2_time_to_limit_s")
    asked = model.requested_t_on_s
    feasible = bool(t_allow) and float(t_allow) > 0.0
    out: Dict[str, Any] = {
        "kind": "S2",
        "duty": model.duty,
        "cycle_s": round(float(model.cycle_s), 4),
        "requested_t_on_s": asked,
        "t_on_allowable_s": t_allow,
        "limiting_part": times.get("s2_limiting_part"),
        "limits_c": {k: float(v) for k, v in model.limits.items()},
        "magnet_limit_source": model.magnet_limit_source,
        "hot_spot_offset_K": round(model.hot_spot_offset_k, 2),
        # An S2 that never reaches a limit is not infeasible — it is a point the
        # machine may hold for ever, which the model says by finding no hit.
        "feasible": True,
        "unlimited": not feasible,
        **times,
    }
    # The pull the temperatures are taken at: the ALLOWABLE one when there is a
    # limit to reach, otherwise the one that was asked for (a point with no
    # limit has no "allowable" length to report, and inventing the horizon's
    # would bill the run at a temperature nobody asked about).
    t_at = float(t_allow) if feasible else float(asked or model.profile.segments[0].t_s)
    st = _state_at(model, t_at, samples=samples_per_segment)
    st.pop("_trace", None)
    out["at_allowable"] = st
    t_coil, t_mag = _peak_pair(st)
    out["coil_temp_c"] = (None if t_coil is None else round(float(t_coil), 2))
    out["magnet_temp_c"] = (None if t_mag is None else round(float(t_mag), 2))
    out["winding_hot_peak_c"] = st.get("winding_hot_peak_c")
    out["magnet_peak_c"] = st.get("magnet_peak_c")
    out["fits_requested"] = (None if asked is None else
                             (True if not feasible
                              else bool(float(asked) <= float(t_allow) + 1e-9)))
    if not feasible:
        out["note"] = ("no part reaches its limit at this point: the machine "
                       "settles below every one of them, so the pull may last "
                       "as long as the application wants it to (this point is "
                       "S1).")
    else:
        out["note"] = (
            "one pull from %s lasts %.1f s before the %s reaches %s%s."
            % (_start_words(model), float(t_allow),
               times.get("s2_limiting_part") or "winding",
               _limit_words(model, times.get("s2_limiting_part")),
               ("" if asked is None else
                "; the %g s asked for %s under it"
                % (float(asked),
                   "fits" if float(asked) <= float(t_allow) else "does NOT fit"))))
    return out


def _limit_words(model: CycleModel, part: Optional[str]) -> str:
    try:
        return "%.0f °C" % float(model.limits[str(part)])
    except (KeyError, TypeError, ValueError):
        return "its limit"


def _start_words(model: CycleModel) -> str:
    t0 = model.profile.t_start_c
    if t0 is None:
        return "%.0f °C ambient" % float(model.network.t_ambient_c)
    return "%.0f °C" % float(t0)


def _s3_note(model: CycleModel, allowable: float, asked: Optional[float],
             part: Optional[str], at: Optional[Mapping[str, Any]]) -> str:
    """One sentence: what the machine may run, how hot it is there, and whether
    the ratio the request asked about fits under it."""
    hot = (at or {}).get("winding_hot_peak_c")
    mag = ((at or {}).get("magnet_peak_c")
           or ((at or {}).get("peak_c") or {}).get("magnet"))
    bits = ["%.1f %% of a %g s cycle (%.1f s on) is allowable, limited by the "
            "%s at %s" % (float(allowable), float(model.cycle_s),
                          float(model.cycle_s) * float(allowable) / 100.0,
                          part or "winding", _limit_words(model, part))]
    if hot is not None:
        bits.append("the winding hot spot peaks at %.0f °C there" % float(hot))
    if mag is not None:
        bits.append("the magnets at %.0f °C" % float(mag))
    if asked is not None:
        bits.append("the %g %% asked for %s under it"
                    % (float(asked),
                       "fits" if float(asked) <= float(allowable)
                       else "does NOT fit"))
    return "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# Is it settled?
# ---------------------------------------------------------------------------

def regime_settled(previous: Optional[Mapping[str, Any]],
                   current: Optional[Mapping[str, Any]]) -> bool:
    """Has the found regime stopped moving between two passes?

    The loop's temperature test is necessary and NOT sufficient on an impulse
    duty: the pair being compared is the pair at a duty ratio that is itself
    being searched, so a loop whose copper has settled while its ED is still
    walking has not converged on anything.  ``True`` before there is anything to
    compare would be a one-pass loop calling itself settled, so the first pass
    is never settled.
    """
    if not previous or not current:
        return False
    if str(current.get("kind")) != str(previous.get("kind")):
        return False
    if current.get("kind") == "S3":
        a, b = previous.get("ed_allowable_pct"), current.get("ed_allowable_pct")
        if a is None or b is None:
            return a is None and b is None
        return abs(float(b) - float(a)) <= ED_TOL_PCT
    a, b = previous.get("t_on_allowable_s"), current.get("t_on_allowable_s")
    if a is None or b is None:
        return a is None and b is None
    return abs(float(b) - float(a)) <= max(T_ON_TOL_FRACTION * abs(float(a)),
                                           1e-3)


def progress_words(pass_no: int, regime: Mapping[str, Any],
                   coil_from: Optional[float],
                   coil_to: Optional[float]) -> str:
    """The loop's progress line, one clause: what the search found this pass and
    where it is taking the winding next."""
    if str(regime.get("kind")) == "S3":
        ed = regime.get("ed_allowable_pct")
        head = ("ED %s allowable"
                % ("—" if ed is None else "%.1f %%" % float(ed)))
    else:
        t = regime.get("t_on_allowable_s")
        head = ("on-time %s allowable"
                % ("unlimited" if t is None else "%.1f s" % float(t)))
    if coil_from is None or coil_to is None:
        return "pass %d: %s" % (int(pass_no), head)
    return ("pass %d: %s, coil %.0f → %.0f °C"
            % (int(pass_no), head, float(coil_from), float(coil_to)))


# ---------------------------------------------------------------------------
# The stored record — the same shape POST /api/thermal/duty_cycle writes
# ---------------------------------------------------------------------------

def cycle_record(model: CycleModel, regime: Mapping[str, Any], *,
                 samples_per_segment: int = FINAL_SAMPLES,
                 max_samples: int = 400) -> Dict[str, Any]:
    """The full duty-cycle record of the FOUND regime, in the shape the report
    already prints.

    One more integration — the cycle at the allowable ED (or the pull of the
    allowable on-time), kept with its series — because the report's figures are
    a real cycle and never an extrapolated one.  Every key here is one
    ``duty_results.compact_duty_cycle`` already whitelists and the report
    already reads; nothing new is invented, which is what lets a coupled answer
    print through the existing section with no new prose.
    """
    prof = _profile_of_answer(model, regime)
    cycle, split = _integrate_answer(model, prof, regime,
                                     samples_per_segment=samples_per_segment,
                                     max_samples=max_samples)
    limits = _limits_block(model, regime)
    network = model.network.as_record()
    from motor_ai_sim.thermal_capacities import (capacities_j_per_k, cp_sources,
                                                 total_capacity_j_per_k)
    network.update({
        "C_J_per_K": capacities_j_per_k(model.caps),
        "C_total_J_per_K": round(total_capacity_j_per_k(model.caps), 3),
        "cp_sources": cp_sources(model.caps),
        "capacity_parts": {n: list(model.caps[n]["parts"]) for n in NODES},
        "calibration_duty": model.duty,
        "active_nodes": list(model.network.active_nodes),
        "side_area_basis": model.side_area_basis,
    })
    return {
        "kind": "duty_cycle",
        "duty": model.duty,
        "spec": {
            "kind": prof.kind,
            "cycle_s": round(prof.cycle_s, 4),
            "duration_s": round(prof.duration_s, 4),
            "ed_pct": prof.ed_pct,
            # The cycle stored, drawn and printed is the one at the ALLOWABLE
            # ratio — the loop FOUND it, so `ed_given` is false and the report's
            # own gate reads it as a found regime rather than a graded one.
            "ed_given": False,
            "found": True,
            "t_on_s": prof.t_on_s,
            "rest_duty": prof.rest_duty,
            "calibration_duty": model.duty,
            "calibration_source": (
                "the coupled loop's own last thermal map — the map of this very "
                "point, at the temperature the loop settled it at"),
            "t_start_c": prof.t_start_c,
            "n_cycles_max": prof.n_cycles_max,
            "note": _spec_note(model, regime),
            "segments": [
                {"duty": s.name, "t_s": round(s.t_s, 4), "rpm": s.rpm,
                 "coil_ref_c": s.coil_ref_c,
                 "losses_W": {n: round(float(s.losses.get(n, 0.0)), 4)
                              for n in NODES},
                 "total_W": round(s.total_W, 4), "note": s.note}
                for s in prof.segments],
        },
        "network": network,
        "cycle": cycle,
        "split": split,
        "limits": limits,
    }


def _profile_of_answer(model: CycleModel,
                       regime: Mapping[str, Any]) -> tdc.Profile:
    """The cycle the answer describes: the S3 at the allowable ED, or the S2
    pull of the allowable length."""
    if model.kind == "S3":
        ed = regime.get("ed_allowable_pct")
        if ed and float(ed) > 0.0:
            return model.profile.with_ed(float(ed))
        return model.profile
    t = regime.get("t_on_allowable_s") or model.requested_t_on_s \
        or model.profile.segments[0].t_s
    seg = replace(model.profile.segments[0], t_s=float(t))
    return replace(model.profile, segments=(seg,), cycle_s=float(t),
                   t_on_s=float(t))


def _integrate_answer(model: CycleModel, prof: tdc.Profile,
                      regime: Mapping[str, Any], *, samples_per_segment: int,
                      max_samples: int) -> Tuple[Dict[str, Any],
                                                 Dict[str, Any]]:
    """The series the report draws, plus where the heat went."""
    if prof.kind == "S3" and regime.get("feasible"):
        rec = tdc.periodic_steady_state(
            prof, model.network, model.caps,
            samples_per_segment=max(8, int(samples_per_segment)))
        trace = rec.pop("trace")
        cycle = _cycle_block(
            trace, model, converged=bool(rec["converged"]),
            n_cycles=int(rec["n_cycles"]), residual_k=rec["residual_K"],
            note=("the cycle map's fixed point at the ALLOWABLE duty ratio: one "
                  "real cycle integrated from the converged start state."),
            max_samples=max_samples)
        cycle["start_state_c"] = rec["start_state_c"]
    else:
        trace = tdc.integrate_profile(
            prof, model.network, model.caps, n_cycles=1,
            samples_per_segment=max(8, int(samples_per_segment)))
        cycle = _cycle_block(
            trace, model, converged=None, n_cycles=1, residual_k=None,
            note=("one pull from the start temperature, never repeated — there "
                  "is no periodic state, so the series is that single pull."),
            max_samples=max_samples)
    return cycle, tdc.average_split(trace, model.network)


def _cycle_block(trace, model: CycleModel, *, converged: Optional[bool],
                 n_cycles: int, residual_k: Optional[float], note: str,
                 max_samples: int) -> Dict[str, Any]:
    def _mean(xs):
        return sum(xs) / len(xs) if xs else None

    out: Dict[str, Any] = {
        "converged": converged,
        "n_cycles": int(n_cycles),
        "residual_K": (None if residual_k is None
                       else round(float(residual_k), 4)),
        "peak_c": {n: round(max(trace.T_c[n]), 2) for n in NODES},
        "min_c": {n: round(min(trace.T_c[n]), 2) for n in NODES},
        "mean_c": {n: round(_mean(trace.T_c[n]), 2) for n in NODES},
        "winding_hot_peak_c": round(max(trace.hot_spot_c), 2),
        "winding_hot_mean_c": round(_mean(trace.hot_spot_c), 2),
        "hot_spot_offset_K": round(model.hot_spot_offset_k, 2),
        "closure_pct": round(trace.closure_pct, 4),
        "energy_in_J": round(trace.energy_in_J, 3),
        "energy_out_J": round(trace.energy_out_J, 3),
        "stored_J": round(trace.stored_J, 3),
        "segments": [[round(a, 4), round(b, 4), nm]
                     for a, b, nm in trace.segments],
        "note": note,
    }
    out.update(tdc.decimate(trace, int(max_samples)))
    return out


def _spec_note(model: CycleModel, regime: Mapping[str, Any]) -> str:
    bits = [str(model.profile.note or "").strip()]
    if model.kind == "S3":
        bits.append("the split above is the ALLOWABLE one, found by the coupled "
                    "loop rather than stated")
    if model.calibration_note:
        bits.append(model.calibration_note)
    return "; ".join(b for b in bits if b)


def _limits_block(model: CycleModel,
                  regime: Mapping[str, Any]) -> Dict[str, Any]:
    """The ``limits`` block of the stored record — the same keys the duty-cycle
    route writes, so the report's rows, its regime line and its two ED figures
    print a coupled answer without knowing where it came from."""
    lims = {k: float(v) for k, v in model.limits.items()}
    out: Dict[str, Any] = {
        "winding_limit_c": lims.get("winding"),
        "winding_limit_note": (
            "the project's insulation class, judged on the HOT SPOT — the "
            "winding mean plus the calibration map's own max − mean offset of "
            "%.1f K, held constant (stated approximation)"
            % model.hot_spot_offset_k),
        "magnet_limit_c": lims.get("magnet"),
        "magnet_limit_note": ("" if lims.get("magnet") is not None
                              else model.magnet_limit_source),
        "limits_c": lims,
        "limiting_part": regime.get("limiting_part"),
        "s2_time_to_limit_s": regime.get("s2_time_to_limit_s"),
        "s2_limiting_part": regime.get("s2_limiting_part"),
        "s2_horizon_s": regime.get("s2_horizon_s"),
        "s2_end_state_c": regime.get("s2_end_state_c"),
        "s2_winding_hot_end_c": regime.get("s2_winding_hot_end_c"),
        "s2_note": regime.get("s2_note"),
        "s2_from_rated_s": regime.get("s2_from_rated_s"),
        "s2_from_rated_part": regime.get("s2_from_rated_part"),
        "s2_from_rated_start_c": regime.get("s2_from_rated_start_c"),
        "s2_from_rated_note": regime.get("s2_from_rated_note"),
        # An S3 solved inside the loop has no "cycle mean to pull out of" that
        # is not the cycle itself, so the three keys are written ABSENT rather
        # than zero — the report drops the row, which is the honest display of
        # a number nobody computed.
        "at_allowable": regime.get("at_allowable"),
        "at_requested": regime.get("at_requested"),
    }
    if model.kind == "S3":
        out.update({
            "ed_allowable_pct": regime.get("ed_allowable_pct"),
            "ed_requested_pct": regime.get("ed_requested_pct"),
            "ed_limiting_part": regime.get("limiting_part"),
            "ed_cycle_s": regime.get("ed_cycle_s"),
            "ed_curve": regime.get("ed_curve") or [],
            "ed_vs_cycle": regime.get("ed_vs_cycle") or [],
            "ed_note": regime.get("ed_note"),
            "ed_found": True,
            "ed_found_note": (
                "the coupled loop found this duty ratio: the cycle stored and "
                "drawn is the one at the allowable ED, and the electromagnetic "
                "run beside it was solved at the temperatures it reaches there."),
        })
    else:
        out.update({
            "ed_allowable_pct": None, "ed_requested_pct": None,
            "ed_limiting_part": None, "ed_curve": [], "ed_vs_cycle": [],
            "ed_cycle_s": None, "ed_found": False, "ed_found_note": "",
            "ed_note": ("an allowable duty ratio only means something for an S3 "
                        "(intermittent) cycle; this one is S2."),
            "t_on_allowable_s": regime.get("t_on_allowable_s"),
            "t_on_requested_s": regime.get("requested_t_on_s"),
        })
    return {k: v for k, v in out.items() if v is not None or k in (
        "magnet_limit_c", "ed_allowable_pct", "ed_requested_pct",
        "s2_time_to_limit_s", "at_allowable")}
