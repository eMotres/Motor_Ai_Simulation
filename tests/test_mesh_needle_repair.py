"""The mesher must never hang on a sub-tolerance feature — it meshes or it says why.

The defect (2026-09-21, CIANO14 50 edited / L15, ``rotor_hole = 1.0``,
``magnet_up_gap = 0.05``): ``build_mesh_from_polygons`` never returned — 540 s
with one core at 100 %, no exception, while the neighbouring recesses
(0, 0.02, 0.07, 0.1, 0.2, 0.3) meshed in ~4.6 s.  It was not Triangle and not a
Python loop: ``py-spy`` and ``faulthandler`` both put the process inside
``gmsh.model.mesh.generate``, and gmsh's own log (General.Terminal 1) named it::

    :-( There are 4 intersections in the 1D mesh (curves 4592 4740 4590 4738)
    8-| Splitting those edges and trying again - level 0
    ... level 1 ... level 2 ...            (still climbing past level 115)

Curve 4590 is a chord of the stator bore (r = 15.1000 mm); curve 4592 runs from
r = 15.1228 to 15.1060 mm, 5.9 um off it.  The bore is a FIXED 256-gon, so its
chords sag ~1.1 um inside the ideal circle, and a slot corner placed on the
exact circle therefore sticks OUT of the discretised ring: the Shapely union has
to walk out to the corner and come straight back, leaving a needle a couple of
microns wide.  gmsh's 2-D edge recovery cannot resolve it and re-splits for ever.

Two independent guards are pinned here:

  * ``_repair_needles`` removes the needle at the source, conformity-preserving:
    the corner is snapped ONTO the neighbouring chord (it moves less than the
    tolerance and still lies exactly on the edge the touching rings share) and
    only the overshooting vertex is dropped.  A vertex deletion alone is NOT
    equivalent — it desynchronises the rings that share the station and puts a
    2e-24 mm2 triangle in the mesh, which is how the first attempt failed.
  * ``Mesh.MaxRetries`` is bounded and gmsh's log is read back afterwards, so a
    recovery that fails anyway is a `MeshingError` the UI can show instead of a
    frozen solve.
"""
from __future__ import annotations

import math
import os
import subprocess
import sys
import textwrap

import pytest

from motor_ai_sim.simulation.mesher import (
    MeshingError, _check_gmsh_log, _repair_needles,
)

#: The stator-bore ring of the Ø50 machine around the needle, verbatim from
#: ``polys["stator"].interiors[0]`` (indices 28..33).  [29] is the 256-gon
#: station that overshoots the tooth corner [30] by 0.416°.
BORE_FRAGMENT = [
    (12.128434, 8.995059),      # 28  bore station, th 36.5625 deg
    (11.904031, 9.289997),      # 29  bore station, th 37.96875 deg  <- overshoot
    (11.971510, 9.203531),      # 30  tooth corner, th 37.5526 deg
    (11.955349, 9.233750),      # 31  slot wall
    (11.950339, 9.267652),      # 32  slot wall
    (11.957070, 9.301254),      # 33  slot wall
]


def _folds_back(ring):
    """indices where the ring turns by more than 90 deg onto itself"""
    n = len(ring)
    out = []
    for i in range(n):
        a, p, b = ring[(i - 1) % n], ring[i], ring[(i + 1) % n]
        if ((p[0] - a[0]) * (b[0] - p[0])
                + (p[1] - a[1]) * (b[1] - p[1])) < 0.0:
            out.append(i)
    return out


def _seg_dist(p, a, b):
    ex, ey = b[0] - a[0], b[1] - a[1]
    l2 = ex * ex + ey * ey
    if l2 <= 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / l2))
    return math.hypot(p[0] - (a[0] + t * ex), p[1] - (a[1] + t * ey))


def test_the_bore_needle_is_recognised_before_the_repair():
    """The fixture really is the pathological ring, not a made-up one.

    (It is an OPEN fragment of the bore ring, so the wrap-around from the last
    vertex back to the first folds too — only the interior fold is the needle.)
    """
    assert 1 in _folds_back(BORE_FRAGMENT)
    # the two flanks that gmsh reported as intersecting are 5.9 um apart:
    # the slot wall's first vertex [3] against the bore chord [1]-[2]
    d = _seg_dist(BORE_FRAGMENT[3], BORE_FRAGMENT[1], BORE_FRAGMENT[2])
    assert 1e-3 < d < 1e-2, d


def test_repair_removes_the_fold_back_and_keeps_the_ring_conforming():
    out, n_fix, worst = _repair_needles(BORE_FRAGMENT, tol=0.01)

    assert n_fix == 1
    assert worst < 0.01                      # nothing moved further than the tol
    assert len(out) == len(BORE_FRAGMENT) - 1
    # the needle is gone and the repair is idempotent — a second pass finds
    # nothing, i.e. no new sub-tolerance fold-back was created
    again, n_again, _ = _repair_needles(out, tol=0.01)
    assert n_again == 0
    assert [tuple(v) for v in again] == [tuple(v) for v in out]
    # no flank of the repaired ring comes within the tolerance of another
    for i in range(1, len(out) - 1):
        for j in range(i + 2, len(out) - 1):
            assert _seg_dist(out[i], out[j], out[j + 1]) > 1e-2 or j == i + 1

    # CONFORMITY: the corner that moved must land ON the chord the neighbouring
    # rings (air gap, outer band) still use, so OCC only splits that edge.
    a, p = BORE_FRAGMENT[0], BORE_FRAGMENT[1]
    moved = out[1]
    assert _seg_dist(moved, a, p) < 1e-12
    # and it moved by less than the tolerance, i.e. the tooth did not shift
    assert math.hypot(moved[0] - BORE_FRAGMENT[2][0],
                      moved[1] - BORE_FRAGMENT[2][1]) < 0.01
    # every other vertex is untouched, byte for byte
    assert list(out[2:]) == [tuple(v) for v in BORE_FRAGMENT[3:]]
    assert tuple(out[0]) == BORE_FRAGMENT[0]


def test_a_real_notch_is_never_removed():
    """A meshable notch (a 0.05 mm magnet recess is 5x the tolerance) stays."""
    notch = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0),
             (0.5, 1.0), (0.5, 0.95), (0.45, 0.95), (0.45, 1.0),
             (0.0, 1.0)]
    out, n_fix, _ = _repair_needles(notch, tol=0.01)
    assert n_fix == 0
    assert [tuple(v) for v in out] == notch


def test_repair_terminates_on_a_ring_that_is_all_needles():
    """The repair has a hard iteration guard — it may give up, never spin."""
    ring = []
    for i in range(40):
        ring.append((float(i), 0.0))
        ring.append((float(i) - 0.5, 1e-4))   # fold back on every second vertex
    out, n_fix, _ = _repair_needles(ring, tol=0.01)
    assert len(out) >= 3
    assert n_fix <= 4 * len(ring) + 16


def test_repair_is_a_no_op_without_a_fold_back():
    circle = [(math.cos(2 * math.pi * k / 64), math.sin(2 * math.pi * k / 64))
              for k in range(64)]
    out, n_fix, worst = _repair_needles(circle, tol=0.01)
    assert (n_fix, worst) == (0, 0.0)
    assert [tuple(v) for v in out] == circle


# ---------------------------------------------------------------------------
# The loud-failure guard


def test_gmsh_recovery_failure_becomes_a_meshing_error():
    """gmsh's own complaint must surface as an exception, not a silent mesh."""
    log = ["Info: Meshing surface 132 (Plane, Frontal-Delaunay)",
           "Info: [ 90%] :-( There are 4 intersections in the 1D mesh "
           "(curves 4592 4740 4590 4738)",
           "Info: [ 90%] 8-| Splitting those edges and trying again - level 0"]
    with pytest.raises(MeshingError) as ei:
        _check_gmsh_log(log)
    assert "intersections in the 1D mesh" in str(ei.value)
    assert "NOT used" in str(ei.value)


def test_a_clean_gmsh_log_raises_nothing():
    _check_gmsh_log(["Info: Meshing surface 12 (Plane, Frontal-Delaunay)",
                     "Info: Done meshing 2D (Wall 3.1s)"])
    _check_gmsh_log([])
    _check_gmsh_log(None)


# ---------------------------------------------------------------------------
# End to end: the case that used to hang.  Run in a CHILD process with a wall
# clock, because a regression here is a hang and pytest has no timeout plugin
# in this environment — a hung child is killed and reported, a hung test is not.

#: CIANO14 50 edited / L15 as the server holds it, pinned field by field (the
#: same reasoning as tests/test_pocket_straight_sides.py: never read
#: config/motor_config.yaml, the user edits it constantly).
GEO_50 = {
    "air_gap": 0.25, "core_thickness": 2.4, "cut_width": 1.2,
    "insulation_thickness": 0.06, "magnet_down_height": 1.5,
    "magnet_fill_down": 0.87, "magnet_fill_radius": 0.2, "magnet_fill_up": 0.34,
    "magnet_height": 7, "magnet_lamination": 0, "magnet_up_gap": 0.05,
    "motor_length": 15.0, "num_poles": 14, "num_poles_per_segment": 7,
    "num_seg": 2, "num_slots": 12, "num_slots_per_segment": 6,
    "num_wires_per_slot": 9.0, "rotor_fill_r": 0.2, "rotor_hole": 1.0,
    "rotor_house_height": 1.3, "shaft_height": 2, "sleeve_thickness": 0,
    "slot_height": 7.5, "slot_hs": 0.267, "stator_diameter": 50,
    "stator_fillet_r": 1.5, "stator_fillet_r1": 0.1, "tooth2_width": 2.1,
    "tooth_width": 4.2, "wire_height": 0.5, "wire_parallel": 1,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "wire_split": 1,
    "wire_width": 3.0,
}

_CHILD = textwrap.dedent("""
    import json, sys, time
    import numpy as np
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.mesher import build_mesh_from_polygons
    geo = json.loads(sys.argv[1])
    m = CadQueryMotor(); m.set_parameters(geo)
    polys = m.get_2d_polygons(0.0)
    t0 = time.time()
    mesh = build_mesh_from_polygons(polys, 0.0, mesh_size_mm=1.5)[0]
    x, y = mesh.p[0, mesh.t], mesh.p[1, mesh.t]
    ar = 0.5*np.abs((x[1]-x[0])*(y[2]-y[0]) - (x[2]-x[0])*(y[1]-y[0]))
    def ang(a, b, c):
        v1 = np.stack([x[b]-x[a], y[b]-y[a]]); v2 = np.stack([x[c]-x[a], y[c]-y[a]])
        n = np.maximum(np.linalg.norm(v1, axis=0)*np.linalg.norm(v2, axis=0), 1e-30)
        return np.degrees(np.arccos(np.clip((v1*v2).sum(0)/n, -1, 1)))
    am = np.minimum(np.minimum(ang(0,1,2), ang(1,0,2)), ang(2,0,1))
    print(json.dumps({"s": time.time()-t0, "ne": int(mesh.t.shape[1]),
                      "min_ang": float(am.min()), "min_area": float(ar.min()),
                      "n_sub_deg": int((am < 1).sum())}))
""")


@pytest.mark.slow
@pytest.mark.parametrize("gap", [0.0, 0.05, 0.1])
def test_the_recess_meshes_for_every_gap(gap):
    """«эта пара должна работать при любом значении magnet_up_gap» (owner)."""
    import json
    geo = dict(GEO_50, magnet_up_gap=gap)
    env = dict(os.environ)
    env.pop("SB_NEEDLE_TOL_MM", None)
    try:
        out = subprocess.run([sys.executable, "-c", _CHILD, json.dumps(geo)],
                             capture_output=True, text=True, timeout=180,
                             env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("build_mesh_from_polygons HUNG on magnet_up_gap=%s "
                    "(the 2026-09-21 defect is back)" % gap)
    assert out.returncode == 0, out.stderr[-3000:]
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["ne"] > 5000, r
    assert r["min_area"] > 1e-12, r          # no numerically-zero triangle
    assert r["n_sub_deg"] == 0, r            # no sliver anywhere
    assert r["min_ang"] > 0.5, r
