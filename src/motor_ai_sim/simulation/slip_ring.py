"""The air-gap ring density q (slip nodes per electrical period) of a per-request ``slip_per_period``.

The mesh study (2026-11-17/20) found that the lever for a coarser air-gap band is q, not the row count.  A requested q is snapped UP
to a multiple of 24 (the grid the pole-periodic rotor mesh needs) and then to the next multiple of 24 for which the sector wedge ring
is whole (``pole_pairs * q`` divisible by the sector count).  The effective q, the pole pairs and the sector count travel in the
result next to the requested q.  Pure; used by ``fem_solver_2d``.
"""
from __future__ import annotations

import math

GRID = 24


def snap_slip_per_period(q: int, pole_pairs: int, n_sectors: int) -> int:
    """The ring nodes per electrical period that a request for ``q`` really gets."""
    eff = GRID * max(1, math.ceil(int(q) / float(GRID)))
    n = max(1, abs(int(n_sectors)))
    pp = max(1, int(pole_pairs))
    while (pp * eff) % n:
        eff += GRID
    return eff
