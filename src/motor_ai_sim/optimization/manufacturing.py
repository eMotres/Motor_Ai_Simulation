# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Manufacturing floors the optimizer's search respects (not the editor's).

Minimum fillet radius, 0.15 mm (owner, 2026-09-30).  Production cuts its
laminations with 0.15 mm corner radii (stator_fillet_r1 in the Fusion model),
so a candidate with a 0.02-0.05 mm fillet is not a machine that can be built.
It is also the most expensive geometry to mesh: the arc of a sub-0.05 mm fillet
is resolved with chords of a few um, and the element size grows from there.
On the gmsh backend that cost 2x solve time and 2x memory on such candidates,
with 3x rotor elements (docs/MESHER_COMPARISON_2026-09-29.md, campaign
candidates c10/c19).

The floor bounds the SEARCH only.  The geometry schema, and with it the editor
and every saved design, keeps its own minimum (0 = a sharp corner, a legitimate
design choice).  A machine whose own fillet is already below the floor keeps
that value: the variable is left out of the search, with a reason, and never
silently moved (the optimizer must not optimise a different machine than the
one on screen).
"""
from __future__ import annotations

from typing import Optional, Tuple

#: Smallest corner radius the optimizer may propose, mm.
MIN_FILLET_MM = 0.15
#: The geometry parameters that are corner radii.
FILLET_KEYS = ("stator_fillet_r", "stator_fillet_r1", "rotor_fill_r",
               "magnet_fill_radius")


def optimizer_floor(name: str) -> Optional[float]:
    """The manufacturing lower bound of a search variable, or None."""
    return MIN_FILLET_MM if name in FILLET_KEYS else None


def apply_floor(name: str, lo: float, hi: Optional[float],
                x0: Optional[float] = None) -> Tuple[float, Optional[float], Optional[str]]:
    """(lo, hi, excluded_reason) with the floor applied.

    ``excluded_reason`` is set when the variable cannot be searched: its own
    value ``x0`` lies below the floor (kept as it is, not moved), or the floor
    leaves no range below ``hi``."""
    f = optimizer_floor(name)
    if f is None:
        return lo, hi, None
    if x0 is not None and x0 < f - 1e-12:
        return lo, hi, ("%s = %g mm is below the %g mm minimum manufacturable "
                        "fillet; kept as it is and left out of the search"
                        % (name, x0, f))
    lo2 = max(float(lo), f)
    if hi is not None and hi <= lo2:
        return lo2, hi, ("%s: no range above the %g mm minimum manufacturable "
                         "fillet (max %g)" % (name, f, hi))
    return lo2, hi, None
