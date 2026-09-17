"""HOW LONG MAY IT RUN? — the coupled loop's answer when a part is over its limit.

WHY THIS MODULE EXISTS (owner, 2026-09-17: *«для каплинга: если где-то выходим за
лимиты, нужно посчитать время, за какое мотор проработает до этого лимита»*)
=============================================================================
The coupled EM↔thermal loop answers a question about the STEADY state: held at
this operating point for ever, the winding settles at 214 °C.  When that number
is past the insulation class the loop says so and stops, and the reader is left
with the one thing the answer does not contain — *how long* the machine may
actually pull before it gets there.  On a robot joint, a test bench or a launch
transient that is the whole question: 214 °C in four minutes is a usable pulse,
214 °C in four seconds is not a machine.

So whenever the loop finishes at a point where at least one part is over its
limit, the SAME lumped network the duty cycle uses is fitted to the map that
pass just solved, the machine is switched on at that point, and the step
response is integrated until each over-limit part first reaches its limit.

    T(0) = the start state      →   Ṫ = (P(T) − ΣG·ΔT) / C   →   first crossing

TWO STARTS, because they answer two different questions:

  * **from COLD** — every node at the ambient / coolant inlet.  The machine was
    switched on just now: this is the pulse a cold motor has in it, and it is
    always available because the ambient is an input of the solve;
  * **from RATED** — the node temperatures the RATED duty's own coupled run
    settled at.  The machine has already been working: this is what is left when
    the pull comes on top of a warm machine, and it is always the shorter of the
    two.  Omitted, with the reason said out loud, when this configuration has no
    rated duty with a coupled record — a warm start invented out of this point's
    own map would be a warm-up nobody ran.

WHAT IS JUDGED, AND ON WHICH QUANTITY.  Each part rides ONE network node plus a
CONSTANT offset fitted to the calibration map, so the quantity that is integrated
is the quantity the coupled record reports and the report's §8 rows judge:

  ==============  =========  =========================================  =========
  part            node       quantity                                   limit
  ==============  =========  =========================================  =========
  winding         winding    hot spot = node + (map max − map mean)      the class
  magnet          magnet     hottest element = node + (max − mean)       the card
  bearing         rotor      the seat = node + (map seat − rotor mean)   the grease
  ==============  =========  =========================================  =========

The offsets are the same trick the duty cycle already plays on the winding hot
spot, and they carry the same stated approximation: the SHAPE of each part's
temperature field is frozen at the calibration map's, and only its level moves.

NEVER AN INVENTED NUMBER.  A step response whose asymptote sits BELOW the limit
never reaches it, and that happens for a real reason: the map is over the limit
because of something the four-node network does not represent (a coupling the
loop closed on, a heat path fitted at one temperature, a runaway map with no
equilibrium to fit).  The answer is then ``reaches: false`` with the asymptote
printed beside it — not a time extrapolated out of a curve that flattens first.

THE FEATURE FLAG.  Nothing here is gated by ``DUTY_CYCLE_ENABLED``: this is not
a duty cycle, it asks for no cycle block and offers no cycle UI.  It merely reuses
``thermal_duty_cycle``'s network — the only thing in this project that knows both
the conductances of THIS machine and its heat capacity — and it runs on every
coupled loop, flag on or off.

Pure: no FastAPI, no route, no I/O.  Every refusal is a
:class:`thermal_duty_cycle.DutyCycleError` carrying the ``error_code`` a caller
would report; the coupled route catches them all and files no block rather than
failing a solve that succeeded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from motor_ai_sim import thermal_duty_cycle as tdc
from motor_ai_sim.thermal_capacities import NODES
from motor_ai_sim.thermal_duty_cycle import DutyCycleError

#: The part names this module judges, in the order a reader meets them.
PARTS: Tuple[str, ...] = ("winding", "magnet", "bearing")

#: Which network NODE each part's transient rides.
PART_NODE: Dict[str, str] = {"winding": "winding", "magnet": "magnet",
                             "bearing": "rotor"}

#: What each part's judged quantity IS, for the notes.
PART_QUANTITY: Dict[str, str] = {
    "winding": "the winding hot spot",
    "magnet": "the hottest magnet element",
    "bearing": "the bearing seat",
}

#: A part is "over its limit" only past this much — a tenth of a kelvin over a
#: 200 °C class is the rounding of the number that produced it, not an answer,
#: and reporting "reaches 200 °C after 4 h" for it would be noise.
OVER_TOL_K = 0.5

#: The first horizon the step response is integrated over [s], and the floor
#: under it.  TWO STAGES, for the reason ``coupled_duty_cycle`` documents: the
#: model's own horizon is ten time constants, which on a small joint is over an
#: hour of a machine climbing past 700 °C, and radiation makes that expensive to
#: integrate for an answer that happens in the first minute.  Only when NOTHING
#: is reached in the short horizon does the full one run — and a machine that
#: reaches no limit is a settling one, which is cheap.
FIRST_HORIZON_S = 900.0

#: Ten time constants is where "for ever" is FIRST called — but only first.
#: ``_time_constant`` is a deliberately crude ΣC/ΣG (its own docstring says so),
#: and on a machine whose magnets hang off a physical gap conductance the slowest
#: mode is minutes longer than it: the L13 rated point reads τ = 74 s and has not
#: settled at 900 s, but has at 3 000 s.  So the horizon ESCALATES until one of
#: the two real answers arrives — every target reached, or nothing moving any
#: more — rather than trusting an estimate to be a horizon.
FULL_HORIZON_FACTOR = 10.0

#: …and stops there.  Six hours of holding an operating point is past any
#: question this feature is asked, and a machine that has neither reached a limit
#: nor settled in six hours is reported as exactly that rather than as a number.
HORIZON_CAP_S = 6.0 * 3600.0


# ---------------------------------------------------------------------------
# One thing that may be over a limit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PartLimit:
    """One part, its limit, and how its quantity is read off the network.

    ``at_point_c`` is what the coupled loop's own map says this quantity is —
    the number the §8 row prints — so "over" here means exactly what "over"
    means everywhere else in the document.
    """
    part: str
    node: str
    limit_c: float
    offset_k: float
    at_point_c: float
    source: str = ""

    @property
    def over_by_k(self) -> float:
        return float(self.at_point_c) - float(self.limit_c)

    @property
    def over(self) -> bool:
        return self.over_by_k > OVER_TOL_K

    def as_target(self) -> tdc.Target:
        return (self.part, self.node, float(self.limit_c), float(self.offset_k))


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _comp(thermal_result: Mapping[str, Any], name: str,
          which: str) -> Optional[float]:
    blk = ((thermal_result or {}).get("components") or {}).get(name)
    return _num(blk.get(which)) if isinstance(blk, Mapping) else None


def _offset(thermal_result: Mapping[str, Any], node: str) -> Tuple[float, str]:
    """``(max − mean, how it reads)`` for one part off the calibration map."""
    hi = _comp(thermal_result, node, "max")
    mean = _comp(thermal_result, node, "avg")
    if hi is None or mean is None:
        return 0.0, ("the map carries no max/mean pair for the %s, so its "
                     "node temperature is judged directly" % node)
    return (float(hi) - float(mean),
            "%.2f K above the %s node's mean, frozen at this map's own shape"
            % (float(hi) - float(mean), node))


# ---------------------------------------------------------------------------
# The limits — read off the machine's own cards, by the report's own rules
# ---------------------------------------------------------------------------

def winding_limit() -> Tuple[float, str]:
    """The insulation class, from the ONE place the project states it."""
    try:
        from motor_ai_sim.report import PROJECT_INSULATION_C as lim
    except Exception:                                        # noqa: BLE001
        lim = tdc.PROJECT_INSULATION_C
    return float(lim), ("the project's insulation class (%g °C, class N per "
                        "IEC 60085) — ASSUMED, no card carries a rating"
                        % float(lim))


def magnet_card_limit(grade: Any) -> Tuple[Optional[float], str]:
    """The assigned magnet's maximum working temperature, and where it is from.

    ``report._magnet_limit`` is the one rule — the card's ``max_working_temp_c``
    first, the coercivity class read off the grade name second — so the time this
    module reports and the row §8 prints are judged against ONE number.  A grade
    nothing can be read off is ``(None, why)`` and the magnets are simply not
    judged, exactly as the report does not judge them.
    """
    name = str(grade or "").strip()
    if not name:
        return None, "no magnet is assigned, so the magnets are not judged"
    try:
        from motor_ai_sim.report import _magnet_limit
        lim, why = _magnet_limit(name)
        if lim is not None:
            return float(lim), str(why)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from motor_ai_sim.materials import get_material
        v = _num(getattr(get_material("magnet", name), "max_working_temp_c",
                         None))
        if v:
            return float(v), "the %s card's own maximum working temperature" % name
    except Exception:                                        # noqa: BLE001
        pass
    return None, ("nothing on this machine states a maximum working temperature "
                  "for %r, so the magnets are not judged" % name)


def bearing_lubricant_limit(summary: Optional[Mapping[str, Any]]
                            ) -> Tuple[Optional[float], str]:
    """The top of the grease's stated temperature range, off the RUN's own
    bearings — ``(None, why)`` on a machine that names none.

    The same two hops the report makes (``mech_losses.from_summary`` →
    ``report.bearing_lubricant``), so the limit is the lubricant card the bearing
    table prints and never a second opinion about the same grease.
    """
    try:
        from motor_ai_sim import mech_losses as ml
        from motor_ai_sim.report import bearing_lubricant
        brg = ml.from_summary(dict(summary or {}))
        if not brg:
            return None, ("this run carries no bearings, so there is no "
                          "lubricant limit to judge the seat against")
        lube = bearing_lubricant(brg)
        if not lube or lube.get("limit_c") is None:
            return None, ("the bearings name no lubricant with a stated "
                          "temperature range, so the seat is not judged")
        return float(lube["limit_c"]), (
            "the top of the %s range (%s)" % (lube.get("name") or "lubricant",
                                              lube.get("source") or ""))
    except Exception:                                        # noqa: BLE001
        return None, "the lubricant card could not be read"


def part_limits(*, thermal_result: Mapping[str, Any],
                em_summary: Optional[Mapping[str, Any]] = None,
                magnet_grade: Any = None,
                bearing_temp_c: Optional[float] = None,
                winding_limit_c: Optional[float] = None,
                magnet_limit_c: Optional[float] = None,
                bearing_limit_c: Optional[float] = None,
                ) -> List[PartLimit]:
    """Every part that HAS a limit on this machine, with the offset it rides.

    A part with no limit is simply absent — "nothing on this machine states a
    maximum working temperature for the magnets" is an answer, and a magnet
    judged against an invented 180 °C would be worse than one not judged.
    """
    out: List[PartLimit] = []

    w_lim, w_src = ((float(winding_limit_c), "given by the caller")
                    if winding_limit_c is not None else winding_limit())
    w_mean = _comp(thermal_result, "winding", "avg")
    if w_mean is not None:
        off, off_note = _offset(thermal_result, "winding")
        out.append(PartLimit("winding", "winding", w_lim, off, w_mean + off,
                             "%s; the hot spot is %s" % (w_src, off_note)))

    m_lim, m_src = ((float(magnet_limit_c), "given by the caller")
                    if magnet_limit_c is not None
                    else magnet_card_limit(magnet_grade))
    m_mean = _comp(thermal_result, "magnet", "avg")
    if m_lim is not None and m_mean is not None:
        off, off_note = _offset(thermal_result, "magnet")
        out.append(PartLimit("magnet", "magnet", float(m_lim), off,
                             m_mean + off,
                             "%s; the hottest element is %s" % (m_src, off_note)))

    b_lim, b_src = ((float(bearing_limit_c), "given by the caller")
                    if bearing_limit_c is not None
                    else bearing_lubricant_limit(em_summary))
    seat = _num(bearing_temp_c)
    r_mean = _comp(thermal_result, "rotor", "avg")
    if b_lim is not None and seat is not None and r_mean is not None:
        out.append(PartLimit(
            "bearing", "rotor", float(b_lim), seat - r_mean, seat,
            "%s; the seat is carried as a constant %.2f K from the rotor node, "
            "fitted to this map" % (b_src, seat - r_mean)))
    return out


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------

def fmt_seconds(s: Optional[float]) -> str:
    """A duration a human reads at a glance: ``0.8 s``, ``48 s``, ``2 m 40 s``.

    The same rule the web's ``fmtSecs`` applies, spelled here so the report, the
    log line and the panel cannot disagree about what 160.2 seconds is called.
    """
    v = _num(s)
    if v is None or v < 0.0:
        return "—"
    if v < 10.0:
        return "%.1f s" % v
    if v < 60.0:
        return "%d s" % round(v)
    m = int(v // 60.0)
    return "%d m %02d s" % (m, int(round(v - m * 60.0)))


def _start_words(start: str) -> str:
    return "cold" if start == "cold" else "rated"


def headline(block: Optional[Mapping[str, Any]]) -> str:
    """The ONE sentence — the loop logs it and the panel prints it.

    ``""`` when there is nothing to say (no block, or every part inside its
    limit): a machine that is not over anything grows no line.
    """
    b = dict(block or {})
    if not b or b.get("within_limits", True):
        return ""
    part = str(b.get("limiting_part") or "")
    lim = _num((b.get("limits_c") or {}).get(part))
    over = _num((b.get("over_by_K") or {}).get(part))
    times: List[str] = []
    for start in ("cold", "rated"):
        blk = (b.get("starts") or {}).get(start)
        if not blk:
            continue
        t = _num(blk.get("time_to_limit_s"))
        if t is not None:
            times.append("%s from %s" % (fmt_seconds(t), _start_words(start)))
    head = ("over the %s limit by %s K" % (part, "%.0f" % over)
            if (part and over is not None) else "over a limit")
    if not times:
        return ("%s — the step response of this network never reaches %s, so no "
                "time is quoted" % (head, "%g °C" % lim if lim else "it"))
    return ("%s — reaches %s after %s"
            % (head, "%g °C" % lim if lim is not None else "the limit",
               ", ".join(times)))


def panel_line(block: Optional[Mapping[str, Any]]) -> str:
    """The panel's own wording: what it RUNS, not what it is over by."""
    b = dict(block or {})
    if not b or b.get("within_limits", True):
        return ""
    part = str(b.get("limiting_part") or "")
    lim = _num((b.get("limits_c") or {}).get(part))
    times: List[str] = []
    for start in ("cold", "rated"):
        blk = (b.get("starts") or {}).get(start)
        t = _num((blk or {}).get("time_to_limit_s"))
        if t is not None:
            times.append("%s from %s" % (fmt_seconds(t), _start_words(start)))
    if not times:
        return ("No time to the %s limit: the step response settles below %s"
                % (part or "limit",
                   "%g °C" % lim if lim is not None else "it"))
    return ("Runs %s, then %s reaches %s"
            % (", ".join(times), part or "a part",
               "%g °C" % lim if lim is not None else "its limit"))


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------

def _segment(em_summary: Mapping[str, Any], thermal_result: Mapping[str, Any],
             duty: str) -> tdc.Segment:
    """This operating point as ONE powered segment — the step that is applied.

    The watts are split over the four nodes by the same rule the duty cycle uses
    (``thermal_duty_cycle.losses_by_node``), with the converged map beside the
    run so the solid loss is split and the bearing friction enters where the
    steady solve put it.
    """
    loss = tdc.losses_by_node(em_summary, thermal_result)
    rpm = float(loss.get("rpm") or 0.0)
    return tdc.Segment(str(duty or "this point"), t_s=1.0,
                       losses=tdc.node_watts(loss),
                       coil_ref_c=float(loss["coil_ref_c"]), rpm=rpm,
                       note="; ".join(loss["notes"]))


def _fit_residual(segment: tdc.Segment, network: tdc.Network,
                  caps: Mapping[str, Any],
                  thermal_result: Mapping[str, Any]) -> Dict[str, Any]:
    """HOW WELL the network reproduces the map it was fitted to.

    At the map's own temperatures a perfect network is in balance: every node's
    net watts are zero.  The calibrated links are exact by construction, so what
    a residual measures is one of two things, and the note names both because
    they are read differently:

      * the cost of the links the map could NOT fit (the physical gap and
        magnet-root conductances) and of the end-face films a 2-D cross-section
        has nothing to fit them to — a few per cent, and the honest uncertainty
        on the time;
      * a loop that has NOT converged.  The watts are billed at ``coil_ref_c``,
        the temperature the electromagnetic run was solved at; when the map came
        back far from it (L13 peak, one pass: solved at 200 °C, map at 479 °C)
        ρ_Cu(T) re-scales the copper by a factor and the imbalance is most of the
        losses.  That is not the network being wrong — it is the loop not being
        finished, and the residual is the cheapest place it shows.

    It costs one evaluation of the right-hand side.
    """
    state = {}
    for n in NODES:
        v = _comp(thermal_result, n, "avg")
        if v is None:
            return {"available": False,
                    "note": "the map carries no mean temperature for the %s, so "
                            "the fit cannot be checked" % n}
        state[network.rep(n)] = float(v)
    try:
        C = tdc.merged_capacities(caps, network)
        d, P, _F = tdc._derivatives(segment, state, network, C)
    except DutyCycleError as exc:
        return {"available": False, "note": str(exc)}
    resid_w = {r: float(d[r]) * float(C[r]) for r in d}
    total = sum(abs(float(P.get(n, 0.0))) for n in NODES) or 1.0
    worst = max(abs(v) for v in resid_w.values()) if resid_w else 0.0
    # HOW FAR the map came back from the temperature its watts were billed at.
    # A converged loop puts these within its own tolerance and the copper
    # feedback is then a factor of ~1; a big gap here explains a big residual.
    t_w = float(state[network.rep("winding")])
    drift_k = t_w - float(segment.coil_ref_c)
    return {
        "available": True,
        "residual_W": {r: round(v, 4) for r, v in resid_w.items()},
        "worst_residual_W": round(worst, 4),
        "worst_residual_pct_of_losses": round(100.0 * worst / total, 3),
        "link_kinds": network.link_kinds(),
        "hot_spot_offset_K": round(float(network.hot_spot_offset_k), 2),
        "coil_ref_c": round(float(segment.coil_ref_c), 2),
        "map_winding_mean_c": round(t_w, 2),
        "coil_ref_drift_K": round(drift_k, 2),
        "note": ("at the map's own temperatures the network is out of balance by "
                 "at most %.2f W (%.2f %% of the %.1f W this point makes); the "
                 "calibrated links are exact by construction, so this is the cost "
                 "of the links the map could not fit and of the end-face films%s"
                 % (worst, 100.0 * worst / total, total,
                    ("" if abs(drift_k) <= 5.0 else
                     " — and mostly of an UNFINISHED loop: the watts are billed "
                     "at %.1f °C while the map came back at %.1f °C, so "
                     "ρ_Cu(T) re-scales the copper by %.2f×"
                     % (float(segment.coil_ref_c), t_w,
                        tdc.cu_rho_ratio(t_w)
                        / tdc.cu_rho_ratio(float(segment.coil_ref_c)))))),
    }


def _one_start(segment: tdc.Segment, network: tdc.Network,
               caps: Mapping[str, Any], over: Sequence[PartLimit],
               T0: Optional[Mapping[str, float]]) -> Dict[str, Any]:
    """Integrate ONE step response and read the crossings off it.

    Asked over a SHORT horizon first — a part that is reached is reached at the
    same instant whatever the horizon, and the expensive integration of a machine
    climbing past 700 °C is paid for only by one that reaches nothing.  The
    horizon then escalates until every target has been reached or nothing is
    moving any more, because ΣC/ΣG is an estimate and not an answer.
    """
    targets = [p.as_target() for p in over]
    C = tdc.merged_capacities(caps, network)
    horizons: List[float] = [FIRST_HORIZON_S]
    nxt = FULL_HORIZON_FACTOR * tdc._time_constant(network, C)
    while horizons[-1] < HORIZON_CAP_S:
        nxt = max(nxt, horizons[-1] * 4.0)
        horizons.append(min(nxt, HORIZON_CAP_S))
    rec: Dict[str, Any] = {}
    for t_max in horizons:
        rec = tdc.time_to_limits(segment, network, caps, targets, T0=T0,
                                 t_max_s=t_max)
        if all(t.get("reaches") for t in rec["targets"].values()):
            return rec
        if rec.get("settled"):
            return rec
    return rec


def _parts_block(rec: Mapping[str, Any], over: Sequence[PartLimit]
                 ) -> Tuple[List[Dict[str, Any]], Optional[float], Optional[str]]:
    """The per-part list of one start, plus its headline pair."""
    rows: List[Dict[str, Any]] = []
    best: Optional[Tuple[float, str]] = None
    for p in over:
        t = dict((rec.get("targets") or {}).get(p.part) or {})
        row: Dict[str, Any] = {
            "part": p.part,
            "quantity": PART_QUANTITY.get(p.part, p.part),
            "node": p.node,
            "limit_c": round(float(p.limit_c), 2),
            "limit_source": p.source,
            "at_point_c": round(float(p.at_point_c), 2),
            "over_by_K": round(p.over_by_k, 2),
            "offset_K": round(float(p.offset_k), 2),
            "reaches": bool(t.get("reaches")),
            "time_to_limit_s": _num(t.get("time_s")),
        }
        if row["reaches"]:
            row["note"] = ("%s reaches %g °C after %s"
                           % (row["quantity"], p.limit_c,
                              fmt_seconds(row["time_to_limit_s"])))
            v = _num(row["time_to_limit_s"])
            if v is not None and (best is None or v < best[0]):
                best = (v, p.part)
        else:
            asym = _num(t.get("asymptote_c"))
            row["asymptote_c"] = asym
            row["note"] = (
                "%s never reaches %g °C in this model: the step response "
                "%s, so the map is over the limit for a reason these four nodes "
                "do not represent and NO time is quoted"
                % (row["quantity"], p.limit_c,
                   ("settles at %.1f °C" % asym) if asym is not None
                   else ("is still at %.1f °C after %s and had not settled"
                         % (float(t.get("end_c") or 0.0),
                            fmt_seconds(rec.get("t_horizon_s"))))))
        rows.append(row)
    return rows, (best[0] if best else None), (best[1] if best else None)


def solve(*, thermal_result: Mapping[str, Any],
          em_summary: Mapping[str, Any],
          limits: Sequence[PartLimit],
          caps: Mapping[str, Any],
          geometry: Optional[Mapping[str, Any]] = None,
          cooling: Optional[Mapping[str, Any]] = None,
          side_areas: Optional[Mapping[str, Any]] = None,
          d_housing_m: Optional[float] = None,
          magnet_k_w_per_mk: Optional[float] = None,
          duty: str = "",
          rated_state_c: Optional[Mapping[str, float]] = None,
          rated_source: str = "",
          runaway: bool = False,
          ) -> Dict[str, Any]:
    """THE BLOCK the coupled record carries — or the "nothing to say" version.

    ``limits`` is what :func:`part_limits` found on this machine; ``caps`` is
    ``thermal_capacities.part_capacities`` of the run that made this map.  Every
    other argument is the same one ``coupled_duty_cycle.build_model`` takes and
    means the same thing.

    With every part inside its limit the answer is ``{"within_limits": true,
    "time_to_limit_s": null}`` and NOTHING is integrated: a machine that is not
    over anything has no time to a limit, and reporting the time to a limit it
    respects would invite the reader to plan around a number that is not a
    constraint.
    """
    judged = list(limits or ())
    over = [p for p in judged if p.over]
    limits_c = {p.part: round(float(p.limit_c), 2) for p in judged}
    at_point = {p.part: round(float(p.at_point_c), 2) for p in judged}
    if not over:
        return {
            "within_limits": True,
            "time_to_limit_s": None,
            "limiting_part": None,
            "limits_c": limits_c,
            "at_point_c": at_point,
            "judged": [p.part for p in judged],
            "note": ("every part this machine states a limit for (%s) is inside "
                     "it at this operating point, so it may be held for ever"
                     % (", ".join(p.part for p in judged) or "none")),
        }

    net = tdc.network_from_steady(
        thermal_result,
        mount_g_w_per_k=float((cooling or {}).get("mount_g_w_per_k") or 0.0),
        mount_temp_c=(cooling or {}).get("mount_temp_c"),
        t_ambient_c=(None if (cooling or {}).get("ambient_temp") is None
                     else float((cooling or {})["ambient_temp"])),
        side_areas=side_areas,
        d_housing_m=(None if d_housing_m is None else float(d_housing_m)),
        emissivity=(cooling or {}).get("emissivity"),
        geometry=geometry, magnet_k_w_per_mk=magnet_k_w_per_mk,
        calibration_duty=(str(duty) or None))
    seg = _segment(em_summary, thermal_result, duty)

    starts: Dict[str, Any] = {}
    cold_T0 = {n: float(net.t_ambient_c) for n in NODES}
    for name, T0, why in (
        ("cold", cold_T0,
         "every node at the ambient / coolant inlet %.1f °C — the machine was "
         "switched on just now" % float(net.t_ambient_c)),
        ("rated", (dict(rated_state_c) if rated_state_c else None),
         str(rated_source or "")),
    ):
        if T0 is None:
            continue
        rec = _one_start(seg, net, caps, over, T0)
        rows, t_min, part_min = _parts_block(rec, over)
        starts[name] = {
            "start": name,
            "start_state_c": {k: round(float(v), 2) for k, v in T0.items()},
            "start_source": why,
            "time_to_limit_s": t_min,
            "time_to_limit_words": fmt_seconds(t_min) if t_min is not None else None,
            "limiting_part": part_min,
            "horizon_s": rec.get("t_horizon_s"),
            "settled": rec.get("settled"),
            "end_state_c": rec.get("end_state_c"),
            "parts": rows,
        }
    cold = starts.get("cold") or {}
    out: Dict[str, Any] = {
        "within_limits": False,
        "time_to_limit_s": cold.get("time_to_limit_s"),
        "time_to_limit_words": cold.get("time_to_limit_words"),
        "limiting_part": cold.get("limiting_part") or over[0].part,
        "time_to_limit_from_rated_s": (starts.get("rated") or {}).get(
            "time_to_limit_s"),
        "limits_c": limits_c,
        "at_point_c": at_point,
        "over_by_K": {p.part: round(p.over_by_k, 2) for p in over},
        "over_parts": [p.part for p in over],
        "judged": [p.part for p in judged],
        "parts": cold.get("parts") or [],
        "starts": starts,
        # WHY there is no warm start, when there is none.  A reader who finds one
        # time instead of two must be able to see that the rated pull was not
        # skipped but could not be ASKED — the rated duty has not been solved.
        **({} if rated_state_c else {"rated_start_note": str(rated_source or (
            "this configuration has no rated duty with a coupled record, so "
            "only the pull from cold is reported"))}),
        "network": _fit_residual(seg, net, caps, thermal_result),
        "model": (
            "a four-node lumped network (winding, stator, rotor, magnet) fitted "
            "to THIS run's own converged thermal map, switched on at the start "
            "state and integrated at this operating point; only the copper loss "
            "moves with temperature, each part is judged on its node plus the "
            "map's own constant offset, and the limit is the first crossing"),
        "calibration_duty": str(duty or ""),
    }
    if runaway:
        out["calibration_runaway"] = True
    out["note"] = headline(out)
    if runaway:
        out["note"] = (out["note"] + "; the map this network was fitted to is a "
                       "RUNAWAY map — it has no equilibrium, so the conductances "
                       "come from a state the machine cannot actually hold")
    return out
