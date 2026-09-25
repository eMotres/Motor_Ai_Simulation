"""Limit speed (SF = 1) search — pure, solver-agnostic.

Owner 2026-09-21: *"нужно искать ещё максимальную скорость вращения, на
всякий случай — она будет, когда достигает SF = 1"*.  ``rotor_stress`` already
answers "is this rotor safe at rpm X"; this module answers the companion
question — "at what speed does it stop being safe" — by bracketing and
bisecting the SAME solve the caller already ran, with everything else held
fixed (loads, contacts, interference, temperatures, mesh, order).  It never
extrapolates the answer: every ``rpm_sf1`` returned is interpolated between two
real solves that bracket the target safety factor.

The search knows nothing about FEM, materials or the request shape — it is
handed a ``solve(rpm) -> (sf_min, limiting_part, sf_min_per_part)`` callable
(the route's closure around ``rotor_stress``/``solve_rotor_stress``) and the
already-known answer at the analysed speed (``rpm0``, ``sf0``), so the point
the caller already paid for is never re-solved.

ALGORITHM (v2, 2026-09-25)
--------------------------
Owner 2026-09-25, Ø30 L10: *"Limit speed: not reached within the searched
range (SF 10.38 at 15,000 rpm)" — "он же должен искать эту скорость, на
которой SF = 1"*.  v1 stepped ×1.5 from ``rpm0`` and stopped as soon as the
NEXT step would pass ``max_factor`` (5): 1.5, 2.25, 3.375 — so the range it
actually searched was 3.4×, never 5×, whatever SF said.  On that rotor the
stress is σ ≈ a + b·ω² with a real speed-independent part ``a`` (torque on
the bridges, the shaft fit) — SF 18.1 → 10.3 → 5.4 → 2.6 over the three steps,
crossing only at ≈5.6×.  v2 lets the physics set the range:

1. If ``sf0`` is already at ``target`` (within ``tol_rel``), the analysed
   speed IS the limit speed — no solve is spent.
2. BRACKET from a physics estimate, then verify.  Stress in a spinning rotor
   is (centrifugal ∝ ω²) + (speed-independent: torque, interference,
   thermal), i.e. ``1/SF = a + b·ω²``.  The first candidate is the pure-ω²
   estimate ``rpm0·√(sf0/target)`` (a = 0), every later one the ``a + b·ω²``
   line through the two most recent samples — aimed a few percent PAST the
   estimate so it lands on the far side of the crossing.  Each candidate is a
   real solve; the step is clamped to ×1.1 … ×4 per solve (÷ in the down
   direction), and a sample whose SF did not fall (``b ≤ 0``) doubles the
   step.  Contacts make the curve non-linear — which is why every estimate is
   only a place to LOOK, and the bracket is two real solves on either side.
3. The only range limit is a hard RUNAWAY GUARD: ``max_factor`` × ``rpm0``
   (default 20×; ÷ in the down direction) and, when the caller knows the
   rotor radius, ``rpm_cap`` (a physical tip-speed bound).  If the search
   reaches the guard without a crossing, that is reported as
   ``reached: False`` with ``searched_to_rpm``, ``sf_at_searched_to`` and a
   ``cap_reason`` saying which guard stopped it — never as a vague "not
   within the searched range".
4. REFINE inside the bracket by false position on the same ``a + b·ω²`` line
   (the next sample nudged ±0.4·``tol_rel`` toward the stale end, so the
   bracket closes from both sides; three samples in a row on one side fall
   back to plain bisection), until the bracket is within ``tol_rel`` or the
   solve budget (``max_solves``) runs out.
5. The final answer is interpolated between the final bracket's two solved
   points on that line (clamped inside the bracket) — never a solved rpm
   rounded, never an extrapolation.

A pure ``rpm0 * sqrt(sf0 / target)`` number rides along as
``omega2_extrapolation_rpm``, labelled a cross-check only: the Ø50
measurements (SF 3.84 @ 10,000 / 1.50 @ 20,000 / 0.76 @ 30,000 rpm) and the
Ø30 above show the real curve is NOT a pure ω² law, so it is where the search
STARTS looking, never the answer.

NON-MONOTONIC SF (2026-09-21, measured on a Ø50 with open pockets + a 0.4 mm
sleeve, interference 0.017 mm): 45,000 → 1.51, 47,188 → 5.78, 47,461 → 0.86,
48,281 → 0.75.  SF is NOT monotonic near lift-off / a contact-state change —
a separation contact lands on a different active-set branch between two
speeds — so a plain bisection reports whichever branch it happened to land
in, which can be the WRONG (higher) crossing.  Every sample this search takes
(plus the analysed point) is checked for it: if any sample reads more than
10 % higher than a LOWER-rpm sample, ``non_monotonic`` is set, and the
reported crossing is replaced with the CONSERVATIVE one — the lowest rpm at
which any sample already read below target, bracketed against the sample
immediately below it that was still at or above target — never a later,
higher crossing a bump happened to mask.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

#: What the caller's solve closure returns: the minimum averaged safety
#: factor at that speed, the part it belongs to (``sf_min_part``, or ``None``
#: if the solver reported none), and the full per-part table (unused by the
#: search itself — carried through only so a caller can log it).
SolveFn = Callable[[float], Tuple[float, Optional[str], Optional[Dict[str, object]]]]

#: (rpm, sf_min, limiting_part) — one entry per solve actually spent.
LogEntry = Tuple[float, float, Optional[str]]


def _is_non_monotonic(pts: List[LogEntry]) -> bool:
    """``pts`` sorted ascending by rpm: True if any sample reads more than
    10 % higher than a LOWER-rpm one — SF should only fall as rpm rises."""
    return any(pts[j][1] > pts[i][1] * 1.1
              for i in range(len(pts)) for j in range(i + 1, len(pts)))


def _conservative_crossing(
    pts: List[LogEntry], target: float,
    rpm_sf1: Optional[float], bracket: Optional[Tuple[float, float]],
    limiting_part: Optional[str],
) -> Tuple[float, Tuple[float, float], Optional[str], str]:
    """The lowest-rpm failing sample, bracketed against the sample just below
    it that was still at or above ``target`` — the conservative crossing a
    non-monotonic SF(rpm) calls for (see module docstring).  ``pts`` is
    sorted ascending by rpm and already known non-monotonic.  Falls back to
    the ORIGINAL ``(rpm_sf1, bracket, limiting_part)`` with an empty note
    when there is no earlier passing sample to bracket against (the lowest
    speed tried already failed, or nothing failed at all)."""
    bad_idx = next((k for k, p in enumerate(pts) if p[1] < target), None)
    if bad_idx is None or bad_idx == 0:
        return rpm_sf1, bracket, limiting_part, ""
    good_rpm, good_sf, good_part = pts[bad_idx - 1]
    bad_rpm, bad_sf, bad_part = pts[bad_idx]
    if good_sf < target:
        return rpm_sf1, bracket, limiting_part, ""
    if good_sf == bad_sf:
        new_rpm = 0.5 * (good_rpm + bad_rpm)
    else:
        frac = min(1.0, max(0.0, (good_sf - target) / (good_sf - bad_sf)))
        new_rpm = good_rpm + frac * (bad_rpm - good_rpm)
    return (new_rpm, (good_rpm, bad_rpm), bad_part or good_part,
           f"contact state changes between {good_rpm:,.0f} and "
           f"{bad_rpm:,.0f} rpm; the lower crossing is reported")


#: Hard runaway guard, as a multiple of the analysed speed (÷ downward).  Not
#: a search range: the range comes from the physics estimate; this only stops
#: a solver that never crosses (SF not falling at all) from running forever.
DEFAULT_MAX_FACTOR = 20.0

#: Physical tip-speed bound for :func:`tip_speed_cap_rpm`, m/s.  Even a solid
#: uniform disc — the most favourable shape — peaks at (3+ν)/8·ρ·v² ≈ 0.41·ρ·v²;
#: at 1500 m/s that is ≈ 7 GPa in steel or magnet (ρ ≈ 7.5–7.8 g/cm³), several
#: times any lamination steel's strength, and every rotor here carries steel
#: and magnets (a thin ring or sleeve is worse still: ρ·v²).  So no rotor
#: reaches SF = 1 beyond it: the guard can never cut a real crossing off.
TIP_SPEED_CAP_M_S = 1500.0

#: How far PAST the estimate a bracketing candidate is aimed, so it lands on
#: the far side of the crossing instead of just short of it.
_AIM_PAST = 0.05
#: Per-solve step clamp during bracketing (×, or ÷ downward).
_STEP_MIN, _STEP_MAX = 1.1, 4.0


def tip_speed_cap_rpm(outer_radius_mm: Optional[float],
                      v_tip_m_s: float = TIP_SPEED_CAP_M_S) -> Optional[float]:
    """The rpm at which a rotor of ``outer_radius_mm`` reaches ``v_tip_m_s``
    at its rim — the physical runaway bound (see ``TIP_SPEED_CAP_M_S``).
    ``None`` when the radius is unknown or not positive."""
    try:
        r = float(outer_radius_mm) * 1e-3
    except (TypeError, ValueError):
        return None
    if not math.isfinite(r) or r <= 0:
        return None
    return v_tip_m_s / r * 60.0 / (2.0 * math.pi)


def _line_estimate(p1: Tuple[float, float], p2: Tuple[float, float],
                   target: float) -> Optional[float]:
    """The rpm where ``1/SF = a + b·rpm²`` through ``p1``/``p2`` (each
    ``(rpm, sf)``) reaches ``1/target``; ``None`` when the line does not fall
    with speed (``b ≤ 0``), is degenerate, or puts the crossing at ω² ≤ 0."""
    (r1, s1), (r2, s2) = p1, p2
    if s1 <= 0 or s2 <= 0:
        return None
    x1, x2 = r1 * r1, r2 * r2
    if x1 == x2:
        return None
    y1, y2 = 1.0 / s1, 1.0 / s2
    b = (y2 - y1) / (x2 - x1)
    if not math.isfinite(b) or b <= 0:
        return None
    x = x1 + (1.0 / target - y1) / b
    if not math.isfinite(x) or x <= 0:
        return None
    return math.sqrt(x)


def find_limit_speed(
    solve: SolveFn,
    rpm0: float,
    sf0: float,
    *,
    target: float = 1.0,
    tol_rel: float = 0.01,
    max_factor: float = DEFAULT_MAX_FACTOR,
    max_solves: int = 12,
    rpm_cap: Optional[float] = None,
    rpm_cap_reason: str = "",
) -> Dict[str, object]:
    """Find the speed at which the minimum averaged safety factor = ``target``.

    ``solve`` is called only for speeds the bracket/refinement actually needs
    — the analysed point (``rpm0``, ``sf0``) is never re-solved.  ``rpm_cap``
    (optional) is a physical upper bound on the search (e.g.
    :func:`tip_speed_cap_rpm`), ``rpm_cap_reason`` the words that name it; the
    upward guard is ``min(max_factor·rpm0, rpm_cap)``, the downward one
    ``rpm0 / max_factor``.  Returns a dict:

      * ``rpm_sf1``   — the limit speed, or ``None`` if not bracketed.
      * ``reached``   — whether a crossing was found before a guard stopped it.
      * ``bracket``   — ``(lo_rpm, hi_rpm)`` of the final bracket (SF ≥ target
        at ``lo_rpm``, SF < target at ``hi_rpm``), or ``None``.
      * ``limiting_part`` — the part named at the failing edge of the bracket.
      * ``sf_at_rpm0`` — ``sf0``, echoed back for the caller's convenience.
      * ``n_solves``  — solves actually spent (excludes the given ``sf0``).
      * ``log``       — ``[(rpm, sf_min, part), ...]`` in the order solved.
      * ``omega2_extrapolation_rpm`` — the pure-ω² estimate (see module
        docstring); where the search starts looking, never the answer.
      * ``non_monotonic`` — SF(rpm) was not monotonically falling over the
        samples this search took; when ``reached`` is also True, ``rpm_sf1``
        and ``bracket`` are already the CONSERVATIVE crossing (see module
        docstring), not whichever branch the refinement happened to land in.
      * ``search_cap_rpm`` / ``cap_reason`` — the runaway guard in the search
        direction and what set it.
      * ``searched_to_rpm`` / ``sf_at_searched_to`` — only when NOT reached:
        the farthest speed actually solved and its SF.
      * ``stopped_by`` — ``None`` when reached, else ``"cap"`` (a guard
        stopped it) or ``"budget"`` (``max_solves`` ran out while bracketing).
      * ``note``      — one sentence: what happened / what was held fixed.
    """
    if rpm0 is None or not math.isfinite(rpm0) or rpm0 <= 0:
        raise ValueError(f"rpm0 must be a positive finite number, got {rpm0!r}")
    if sf0 is None or not math.isfinite(sf0) or sf0 <= 0:
        raise ValueError(f"sf0 must be a positive finite number, got {sf0!r}")
    if target is None or not math.isfinite(target) or target <= 0:
        raise ValueError(f"target must be a positive finite number, got {target!r}")
    if max_factor <= 1.0:
        raise ValueError(f"max_factor must be > 1.0, got {max_factor!r}")
    if rpm_cap is not None and (not math.isfinite(rpm_cap) or rpm_cap <= 0):
        rpm_cap = None

    log: List[LogEntry] = []
    n_solves = 0
    omega2_extrapolation_rpm = rpm0 * math.sqrt(sf0 / target)

    def _solve_at(rpm: float) -> Tuple[float, Optional[str]]:
        nonlocal n_solves
        sf, part, _per_part = solve(rpm)
        sf = float(sf)
        if not math.isfinite(sf):
            sf = 0.0
        n_solves += 1
        log.append((rpm, sf, part))
        return sf, part

    held_note = ("same torque, contacts, interference, temperatures, mesh "
                 "and order as the analysed case")

    # ── already there ────────────────────────────────────────────────────
    if abs(sf0 - target) <= tol_rel * target:
        return {
            "rpm_sf1": rpm0, "reached": True, "bracket": (rpm0, rpm0),
            "limiting_part": None, "sf_at_rpm0": sf0, "n_solves": 0,
            "log": log, "omega2_extrapolation_rpm": omega2_extrapolation_rpm,
            "non_monotonic": False, "search_cap_rpm": None, "cap_reason": "",
            "stopped_by": None,
            "note": f"the analysed speed is already at SF = {target:g}; {held_note}",
        }

    up = sf0 > target
    # ── the runaway guard in the search direction ────────────────────────
    if up:
        cap = rpm0 * max_factor
        cap_reason = (f"{max_factor:g}x the analysed speed (max_factor "
                      f"runaway guard)")
        if rpm_cap is not None and rpm_cap < cap:
            cap = max(rpm_cap, rpm0)
            cap_reason = rpm_cap_reason or "the caller's physical speed bound"
    else:
        cap = rpm0 / max_factor
        cap_reason = (f"1/{max_factor:g} of the analysed speed (max_factor "
                      f"runaway guard)")

    def _start_side(sf: float) -> bool:
        return (sf >= target) if up else (sf < target)

    # ── bracket: aim at the a + b·ω² crossing, verify by real solves ─────
    near: Tuple[float, float, Optional[str]] = (rpm0, sf0, None)
    prev_near: Optional[Tuple[float, float, Optional[str]]] = None
    far: Optional[Tuple[float, float, Optional[str]]] = None
    stopped_by: Optional[str] = None
    while far is None:
        if n_solves >= max_solves:
            stopped_by = "budget"
            break
        if near[0] == cap:
            stopped_by = "cap"
            break
        est = (_line_estimate((prev_near[0], prev_near[1]),
                              (near[0], near[1]), target)
               if prev_near is not None
               else near[0] * math.sqrt(near[1] / target))
        if up:
            step = (est * (1.0 + _AIM_PAST) / near[0]) if est else 2.0
            step = min(_STEP_MAX, max(_STEP_MIN, step))
            cand = min(near[0] * step, cap)
        else:
            step = (near[0] / (est * (1.0 - _AIM_PAST))) if est else 2.0
            step = min(_STEP_MAX, max(_STEP_MIN, step))
            cand = max(near[0] / step, cap)
        sf, part = _solve_at(cand)
        if _start_side(sf):
            prev_near, near = near, (cand, sf, part)
        else:
            far = (cand, sf, part)

    if far is None:
        pts_nr = sorted([(rpm0, sf0, None)] + log, key=lambda t: t[0])
        last_rpm, last_sf = near[0], near[1]
        if stopped_by == "cap":
            if up:
                note = (f"SF = {target:g} not reached up to {last_rpm:,.0f} rpm "
                        f"(SF {last_sf:.2f} there) — the search stopped at "
                        f"{cap_reason}; {held_note}")
            else:
                note = (f"SF stays below {target:g} down to {last_rpm:,.0f} rpm "
                        f"(SF {last_sf:.2f} there) — the speed-independent "
                        f"loads alone exceed the strength; the search stopped "
                        f"at {cap_reason}; {held_note}")
        else:
            note = (f"solve budget ({max_solves}) exhausted while bracketing, "
                    f"last at {last_rpm:,.0f} rpm (SF {last_sf:.2f}); {held_note}")
        return {
            "rpm_sf1": None, "reached": False, "bracket": None,
            "limiting_part": None, "sf_at_rpm0": sf0, "n_solves": n_solves,
            "log": log, "omega2_extrapolation_rpm": omega2_extrapolation_rpm,
            "non_monotonic": _is_non_monotonic(pts_nr),
            "search_cap_rpm": cap, "cap_reason": cap_reason,
            "searched_to_rpm": last_rpm, "sf_at_searched_to": last_sf,
            "stopped_by": stopped_by,
            "note": note,
        }

    if up:
        (lo_rpm, lo_sf, lo_part), (hi_rpm, hi_sf, hi_part) = near, far
    else:
        (lo_rpm, lo_sf, lo_part), (hi_rpm, hi_sf, hi_part) = far, near

    # ── refine: false position on the a + b·ω² line, closing both sides ──
    last_side: Optional[str] = None
    same_side_run = 0
    while n_solves < max_solves:
        mid = 0.5 * (lo_rpm + hi_rpm)
        if (hi_rpm - lo_rpm) / mid <= tol_rel:
            break
        width = hi_rpm - lo_rpm
        est = _line_estimate((lo_rpm, lo_sf), (hi_rpm, hi_sf), target)
        if est is None or same_side_run >= 3:
            x = mid
            same_side_run = 0
        else:
            push = 0.4 * tol_rel * est
            if last_side == "hi":
                x = est - push      # hi keeps moving: land on the lo side
            elif last_side == "lo":
                x = est + push      # lo keeps moving: land on the hi side
            else:
                x = est
        x = min(hi_rpm - 0.02 * width, max(lo_rpm + 0.02 * width, x))
        sf, part = _solve_at(x)
        side = "lo" if sf >= target else "hi"
        if side == "lo":
            lo_rpm, lo_sf, lo_part = x, sf, part
        else:
            hi_rpm, hi_sf, hi_part = x, sf, part
        same_side_run = same_side_run + 1 if side == last_side else 1
        last_side = side

    est = _line_estimate((lo_rpm, lo_sf), (hi_rpm, hi_sf), target)
    if est is not None:
        rpm_sf1 = min(hi_rpm, max(lo_rpm, est))
    elif lo_sf == hi_sf:
        rpm_sf1 = 0.5 * (lo_rpm + hi_rpm)
    else:
        frac = min(1.0, max(0.0, (lo_sf - target) / (lo_sf - hi_sf)))
        rpm_sf1 = lo_rpm + frac * (hi_rpm - lo_rpm)
    bracket = (lo_rpm, hi_rpm)
    limiting_part = hi_part or lo_part
    rel_w = (hi_rpm - lo_rpm) / (0.5 * (hi_rpm + lo_rpm))
    note = (f"bracket {lo_rpm:,.0f}–{hi_rpm:,.0f} rpm (±{rel_w * 50:.1f} %) "
            f"after {n_solves} solve(s); {held_note}")

    # ── non-monotonic SF near lift-off / a contact-state change ────────────
    # Checked over EVERY sample this search took, not just the final bracket:
    # the bracketing jump or the refinement may have landed past the anomaly
    # without ever bracketing it directly (see module docstring).
    pts = sorted([(rpm0, sf0, None)] + log, key=lambda t: t[0])
    non_monotonic = _is_non_monotonic(pts)
    if non_monotonic:
        rpm_sf1, bracket, limiting_part, mono_note = _conservative_crossing(
            pts, target, rpm_sf1, bracket, limiting_part)
        if mono_note:
            note += f"; {mono_note}"

    return {
        "rpm_sf1": rpm_sf1,
        "reached": True,
        "bracket": bracket,
        "limiting_part": limiting_part,
        "sf_at_rpm0": sf0,
        "n_solves": n_solves,
        "log": log,
        "omega2_extrapolation_rpm": omega2_extrapolation_rpm,
        "non_monotonic": non_monotonic,
        "search_cap_rpm": cap,
        "cap_reason": cap_reason,
        "stopped_by": None,
        "note": note,
    }
