"""The air-gap ring density q (slip nodes per electrical period) of a per-request ``slip_per_period``.

The mesh study (2026-11-17/20) found that the lever for a coarser air-gap band is q, not the row count.  A requested q is snapped UP
to a multiple of 24 (the grid the pole-periodic rotor mesh needs) and then to the next multiple of 24 for which the sector wedge ring
is whole (``pole_pairs * q`` divisible by the sector count).  The effective q, the pole pairs and the sector count travel in the
result next to the requested q.  Pure; used by ``fem_solver_2d``.
"""
from __future__ import annotations

import math

GRID = 24
Q_MIN, Q_MAX = 24, 2400

REQUEST_KEYS = ("slip_per_period_requested", "slip_per_period_pole_pairs",
                "slip_per_period_n_sectors", "slip_per_period_gap_layers")


def validate_slip_per_period(q):
    """The ONE check of a requested q on every transport (GET query, kernel payload, ``run_one``, solver entry).

    None stays None (the adaptive ring).  Anything else must be a finite whole number in 24..2400: a bool, a fraction (25.9), a
    string or NaN is refused, never truncated.  Raises ``ValueError`` with the message every transport reports.
    """
    if q is None:
        return None
    msg = ("slip_per_period must be a whole number of ring nodes per electrical period, %d..%d "
           "(it is snapped up to a multiple of 24 and of the sector count)" % (Q_MIN, Q_MAX))
    if isinstance(q, bool) or not isinstance(q, (int, float)):
        raise ValueError(msg)
    if isinstance(q, float) and (not math.isfinite(q) or q != int(q)):
        raise ValueError(msg)
    if not (Q_MIN <= int(q) <= Q_MAX):
        raise ValueError(msg)
    return int(q)


def restore_request_provenance(result, requested):
    """Put the EXTERNAL request back into a result after refinement / probe re-solves stamped their internal ring.

    ``requested`` is the validated q captured at the public boundary (None = none asked: the four request keys are removed, so a
    run without q never gains a key).  Returns the same dict.
    """
    if not isinstance(result, dict):
        return result
    if requested is None:
        for k in REQUEST_KEYS:
            result.pop(k, None)
    else:
        result["slip_per_period_requested"] = int(requested)
    return result


def snap_slip_per_period(q: int, pole_pairs: int, n_sectors: int) -> int:
    """The ring nodes per electrical period that a request for ``q`` really gets."""
    eff = GRID * max(1, math.ceil(int(q) / float(GRID)))
    n = max(1, abs(int(n_sectors)))
    pp = max(1, int(pole_pairs))
    while (pp * eff) % n:
        eff += GRID
    return eff
