"""Mesh-option helpers shared by the mesher, the routes and the optimizer.

This module used to hold the geometry-driven constrained-Delaunay mesher built
on J. R. Shewchuk's Triangle.  Triangle's licence forbids commercial use
without the author's permission, which is incompatible with distributing this
project under the GNU AGPL-3.0-or-later, so the CDT mesher and the `triangle`
dependency were removed on 2026-09-29.  A geo-mesh request is now served by the
gmsh build (mesher._build_sliding_band_meshes), which also conforms to the real
CadQuery polygons.  What remains here are the option vocabularies and small
geometry helpers other modules import.
"""
from __future__ import annotations
import logging
import math
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

import numpy as np

# DOM_* tags — must match iron_template / fem_solver_2d.
DOM_AIR, DOM_STATOR, DOM_ROTOR, DOM_SHAFT, DOM_OUTER = 0, 1, 5, 6, 8
DOM_SLEEVE = 11       # carbon-fibre retaining ring on the rotor OD
                      # (9/10 are iron_template's insulation / wire enamel)
DOM_MAG_BASE, DOM_COIL_BASE = 100, 200

# Per-part element size keys the (removed) geometry-driven mesher honoured.
# Kept as the vocabulary of the persisted UI block; "coil_rel" is a factor,
# not a size (see COIL_REL_KEY below).
GEO_PART_KEYS = frozenset(("stator", "rotor", "magnet", "coil", "outer", "air",
                           "coil_rel"))
GEO_REGION_KEYS = ("stator", "rotor", "magnet", "coil")

# ── "Wire cell" — the copper cell size as a FACTOR of the wire's own height ──
# UI: ½h / 1h / 2h, h = the wire's short side.  A factor, not a mm size, because
# the mm value is tied to the wire it was chosen for: a 0.6 mm request saved
# against a 0.6 mm wire silently becomes a 2h cell after the user halves
# wire_height, whereas `coil_rel` re-reads h at build time and stays ½h/1h/2h.
#
# Only the three UI values carry meaning, so anything else is SNAPPED to the
# nearest of them rather than rejected: the key is a discretisation preference,
# not physics (copper cell size 0.2-0.6 mm moves torque by <0.01 %), and a duty
# file written by an older/hand-edited client must still open instead of 400ing
# a whole simulation over a mesh cosmetic.  An explicit `coil` size in mm WINS —
# a user who typed an absolute size asked for that size.
COIL_REL_KEY = "coil_rel"
COIL_REL_CHOICES = (0.5, 1.0, 2.0)


def snap_coil_rel(v) -> float:
    """The requested wire-cell factor snapped to the nearest allowed choice, or
    0.0 when it is absent / unparseable / non-positive (→ the 1h default path,
    which is bit-identical to no key at all)."""
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not (fv > 0.0) or not math.isfinite(fv):
        return 0.0
    return min(COIL_REL_CHOICES, key=lambda c: abs(math.log(fv / c)))


def _coil_rel_of(part_mesh_mm: Optional[Dict]) -> float:
    """The wire-cell factor carried alongside the per-part element SIZES."""
    if not part_mesh_mm:
        return 0.0
    return snap_coil_rel((part_mesh_mm or {}).get(COIL_REL_KEY))


# ── optimizer mesh budget (process-scoped, OFF by default) ───────────────────
# Armed by the optimizer's one-candidate eval subprocess (optimization/
# refine_proc).  It capped the removed CDT mesher's refinement; the gmsh build
# does not consult it, so arming it is currently a no-op.  MeshBudgetExceeded
# keeps its "mesh budget:" message contract for the optimizer's classifier.
_TRI_BUDGET: Dict[str, Optional[int]] = {"v": None}


class MeshBudgetExceeded(RuntimeError):
    """The armed triangle budget was hit — the candidate meshes pathologically.

    Raised instead of letting the refinement cascade: an eval this size could
    not have finished inside the optimizer's subprocess timeout anyway, so the
    candidate is rejected in seconds, counted, and named — not silently burned
    as a 300 s timeout."""


def set_tri_budget(n: Optional[int]) -> None:
    """Arm (int) or disarm (None/0) the process-scoped triangle budget."""
    _TRI_BUDGET["v"] = int(n) if n else None


def tri_budget() -> Optional[int]:
    return _TRI_BUDGET["v"]


def _shaft_bore_r(polys: Dict, r_shaft: float) -> float:
    """Inner radius (mm) of the CadQuery shaft TUBE — 0.0 for a solid shaft.

    The shaft CadQuery builds is a hollow tube (rotor_inner_radius −
    shaft_height .. rotor_inner_radius); the bore inside it is AIR.  Meshing the
    whole inner disk as ONE DOM_SHAFT region handed the eddy solve ~7x the
    conductive section the machine has (150 mm: 5425 mm² of "aluminium" for a
    756 mm² tube), so the shaft eddy tile billed metal that does not exist.

    The bore is read off the polygon rather than off `shaft_inner_radius` so the
    meshed conductor is exactly the section the mass model bills (masses.py
    measures the same polygon)."""
    g = (polys or {}).get("shaft")
    if g is None or getattr(g, "is_empty", True):
        return 0.0
    r_in = 0.0
    for sub in getattr(g, "geoms", [g]):
        for h in getattr(sub, "interiors", []):
            c = np.asarray(h.coords, float)
            if len(c):
                r_in = max(r_in, float(np.hypot(c[:, 0], c[:, 1]).max()))
    return r_in if 1e-6 < r_in < r_shaft - 1e-3 else 0.0
