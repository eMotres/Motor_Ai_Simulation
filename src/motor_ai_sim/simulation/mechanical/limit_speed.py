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

ALGORITHM
---------
1. If ``sf0`` is already at ``target`` (within ``tol_rel``), the analysed
   speed IS the limit speed — no solve is spent.
2. Otherwise BRACKET: step away from ``rpm0`` by ×1.5 each time (up if
   ``sf0 > target`` — SF falls as speed rises; down if ``sf0 < target`` — the
   rotor is already over the line, so the limit is BELOW the analysed speed)
   until the safety factor crosses ``target`` or the step would exceed
   ``max_factor`` × ``rpm0``, whichever comes first.  Not finding a crossing
   within ``max_factor`` is a real answer, not a failure: it is reported as
   ``reached: False`` rather than guessed at.
3. Then BISECT the bracket in rpm until its half-width is within ``tol_rel``
   of the limit speed, or the solve budget (``max_solves``) runs out.
4. The final answer is linearly interpolated between the last bracket's two
   solved points — never a solved rpm rounded, never an extrapolation.

A pure ``rpm0 * sqrt(sf0 / target)`` cross-check (SF ∝ 1/rpm² if EVERY load
were centrifugal) rides along as ``omega2_extrapolation_rpm``, labelled a
cross-check only: the Ø50 measurements this module was built against
(SF 3.84 @ 10,000 / 1.50 @ 20,000 / 0.76 @ 30,000 rpm) show the real curve is
NOT a pure ω² law — a constant-torque stress component (the 0.1 mm bridges)
does not scale with speed at all — so the search above is the answer and the
ω² number is only a sanity read.

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


def find_limit_speed(
    solve: SolveFn,
    rpm0: float,
    sf0: float,
    *,
    target: float = 1.0,
    tol_rel: float = 0.01,
    max_factor: float = 5.0,
    max_solves: int = 12,
) -> Dict[str, object]:
    """Find the speed at which the minimum averaged safety factor = ``target``.

    ``solve`` is called only for speeds the bracket/bisection actually needs —
    the analysed point (``rpm0``, ``sf0``) is never re-solved.  Returns a dict:

      * ``rpm_sf1``   — the limit speed, or ``None`` if not bracketed.
      * ``reached``   — whether a crossing was found within ``max_factor``.
      * ``bracket``   — ``(lo_rpm, hi_rpm)`` of the final bracket (SF ≥ target
        at ``lo_rpm``, SF < target at ``hi_rpm``), or ``None``.
      * ``limiting_part`` — the part named at the failing edge of the bracket.
      * ``sf_at_rpm0`` — ``sf0``, echoed back for the caller's convenience.
      * ``n_solves``  — solves actually spent (excludes the given ``sf0``).
      * ``log``       — ``[(rpm, sf_min, part), ...]`` in the order solved.
      * ``omega2_extrapolation_rpm`` — the pure-ω² cross-check (see module
        docstring); always computed, never the reported answer.
      * ``non_monotonic`` — SF(rpm) was not monotonically falling over the
        samples this search took; when ``reached`` is also True, ``rpm_sf1``
        and ``bracket`` are already the CONSERVATIVE crossing (see module
        docstring), not whichever branch the bisection happened to land in.
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

    log: List[LogEntry] = []
    n_solves = 0
    omega2_extrapolation_rpm = rpm0 * math.sqrt(sf0 / target)

    def _solve_at(rpm: float) -> Tuple[float, Optional[str]]:
        nonlocal n_solves
        sf, part, _per_part = solve(rpm)
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
            "non_monotonic": False,
            "note": f"the analysed speed is already at SF = {target:g}; {held_note}",
        }

    direction = 1.0 if sf0 > target else -1.0
    step = 1.5

    prev_rpm, prev_sf, prev_part = rpm0, sf0, None
    lo: Optional[Tuple[float, float, Optional[str]]] = None
    hi: Optional[Tuple[float, float, Optional[str]]] = None
    reached = False
    note = ""

    # ── bracket ──────────────────────────────────────────────────────────
    k = 1
    while True:
        factor = step ** k
        if factor > max_factor:
            note = (f"no crossing of SF = {target:g} within {max_factor:g}x "
                    f"the analysed speed ({rpm0:,.0f} rpm) in the "
                    f"{'up' if direction > 0 else 'down'} direction — search "
                    f"stopped at the max_factor limit; {held_note}")
            break
        if n_solves >= max_solves:
            note = f"solve budget ({max_solves}) exhausted while bracketing; {held_note}"
            break
        candidate = rpm0 * (factor if direction > 0 else 1.0 / factor)
        sf, part = _solve_at(candidate)
        on_start_side = (sf >= target) == (direction > 0)
        if on_start_side:
            prev_rpm, prev_sf, prev_part = candidate, sf, part
            k += 1
            continue
        if direction > 0:
            lo, hi = (prev_rpm, prev_sf, prev_part), (candidate, sf, part)
        else:
            lo, hi = (candidate, sf, part), (prev_rpm, prev_sf, prev_part)
        reached = True
        break

    if not reached or lo is None or hi is None:
        pts_nr = sorted([(rpm0, sf0, None)] + log, key=lambda t: t[0])
        return {
            "rpm_sf1": None, "reached": False, "bracket": None,
            "limiting_part": None, "sf_at_rpm0": sf0, "n_solves": n_solves,
            "log": log, "omega2_extrapolation_rpm": omega2_extrapolation_rpm,
            "non_monotonic": _is_non_monotonic(pts_nr),
            "note": note or f"limit speed not bracketed; {held_note}",
        }

    lo_rpm, lo_sf, lo_part = lo
    hi_rpm, hi_sf, hi_part = hi

    # ── bisect ───────────────────────────────────────────────────────────
    while n_solves < max_solves:
        mid_est = 0.5 * (lo_rpm + hi_rpm)
        rel_width = (hi_rpm - lo_rpm) / mid_est if mid_est else 0.0
        if rel_width <= tol_rel:
            break
        sf, part = _solve_at(mid_est)
        if sf >= target:
            lo_rpm, lo_sf, lo_part = mid_est, sf, part
        else:
            hi_rpm, hi_sf, hi_part = mid_est, sf, part

    if lo_sf == hi_sf:
        rpm_sf1 = 0.5 * (lo_rpm + hi_rpm)
    else:
        frac = (lo_sf - target) / (lo_sf - hi_sf)
        frac = min(1.0, max(0.0, frac))
        rpm_sf1 = lo_rpm + frac * (hi_rpm - lo_rpm)
    bracket = (lo_rpm, hi_rpm)
    limiting_part = hi_part or lo_part
    note = f"±{tol_rel * 100:.0f}% in rpm after {n_solves} solve(s); {held_note}"

    # ── non-monotonic SF near lift-off / a contact-state change ────────────
    # Checked over EVERY sample this search took, not just the final bracket:
    # the ×1.5 stepping or the bisection may have landed past the anomaly
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
        "note": note,
    }
