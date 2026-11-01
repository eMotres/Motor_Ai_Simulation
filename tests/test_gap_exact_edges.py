"""Gap-facing iron edges keep their CAD angles (cogging audit 2026-11-01).

``_resample_ring_arcs`` used to ROUND the two ends of every run on the bore / rotor OD circle to the nearest slip-grid
node: each slot mouth and pole-shoe edge moved by up to half a grid step, by a different amount for every grid density
(gap layers, steps per period), and the cogging torque followed the moved edges.  ``exact_ends=True`` keeps the exact
crossing points; the circles drawn next to the iron take the same points (``geo_mesh.ring_end_angles`` +
``_grid_circle`` / ``_grid_arc``).  Pure geometry, no FEM.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation import geo_mesh as G
from motor_ai_sim.simulation.mesher import _resample_ring_arcs

R_BORE = 70.0
MOUTH_DEG = 3.11          # deliberately off every grid used below
FIRST_DEG = 10.37


def _slotted_ring():
    """Annulus 70..100 mm with four 3.11 deg slot mouths opening onto the bore."""
    ring = Point(0, 0).buffer(100.0, 512).difference(Point(0, 0).buffer(R_BORE, 2048))
    for k in range(4):
        a0 = math.radians(FIRST_DEG + 90.0 * k)
        a1 = a0 + math.radians(MOUTH_DEG)
        ring = ring.difference(Polygon([(69 * math.cos(a0), 69 * math.sin(a0)), (80 * math.cos(a0), 80 * math.sin(a0)),
                                        (80 * math.cos(a1), 80 * math.sin(a1)), (69 * math.cos(a1), 69 * math.sin(a1))]))
    return ring


def _bore_vertices_deg(geom):
    out = []
    for ring in [geom.exterior, *geom.interiors]:
        xy = np.asarray(ring.coords)
        r = np.hypot(xy[:, 0], xy[:, 1])
        out += np.degrees(np.mod(np.arctan2(xy[:, 1], xy[:, 0]), 2 * math.pi))[np.abs(r - R_BORE) < 1e-6].tolist()
    return np.asarray(sorted(out))


@pytest.mark.parametrize("n_grid", [1440, 1680, 2160, 2880])
def test_exact_ends_keep_the_mouth_edges(n_grid):
    got = _bore_vertices_deg(_resample_ring_arcs(_slotted_ring(), R_BORE, n_grid, exact_ends=True))
    for k in range(4):
        for edge in (FIRST_DEG + 90.0 * k, FIRST_DEG + MOUTH_DEG + 90.0 * k):
            assert np.min(np.abs(got - edge)) < 1e-4, (n_grid, edge)
    # every other vertex on the bore is a grid node, none closer than a quarter step to an edge
    step = 360.0 / n_grid
    off = got[np.abs(got / step - np.round(got / step)) > 1e-6]
    assert len(off) == 8


def test_legacy_rounding_moves_the_edges():
    # the defect this pins: at 1440 nodes the 10.37 deg edge lands on 10.25 deg (0.15 mm at r = 70 mm)
    got = _bore_vertices_deg(_resample_ring_arcs(_slotted_ring(), R_BORE, 1440, exact_ends=False))
    assert np.min(np.abs(got - FIRST_DEG)) > 0.1


def test_circle_follows_the_iron_chain():
    n = 2160
    iron = _resample_ring_arcs(_slotted_ring(), R_BORE, n, exact_ends=True)
    ex = G.ring_end_angles([iron], R_BORE, n)
    assert len(ex) == 8
    circ = G._grid_circle(R_BORE, n, ex)[:-1]
    a_c = np.mod(np.arctan2(circ[:, 1], circ[:, 0]), 2 * math.pi)
    # the circle's nodes inside a tooth-tip run are exactly the iron's nodes there
    a_i = np.radians(_bore_vertices_deg(iron))
    lo, hi = math.radians(FIRST_DEG + MOUTH_DEG), math.radians(FIRST_DEG + 90.0)
    sel_c = np.sort(a_c[(a_c >= lo - 1e-9) & (a_c <= hi + 1e-9)])
    sel_i = np.sort(a_i[(a_i >= lo - 1e-9) & (a_i <= hi + 1e-9)])
    assert sel_c.shape == sel_i.shape and np.allclose(sel_c, sel_i, atol=1e-9)
    # the arc variant keeps only the angles inside its span
    arc = G._grid_arc(R_BORE, n, math.radians(45.0), ex)
    a_a = np.degrees(np.arctan2(arc[:, 1], arc[:, 0]))
    assert np.min(np.abs(a_a - FIRST_DEG)) < 1e-6 and a_a.max() <= 45.0 + 1e-9


def test_legacy_switch_has_no_extra_angles(monkeypatch):
    monkeypatch.setattr(G, "_GAP_EXACT_EDGES", False)
    iron = _resample_ring_arcs(_slotted_ring(), R_BORE, 1440, exact_ends=False)
    assert G.ring_end_angles([iron], R_BORE, 1440) == []
    assert len(G._grid_circle(R_BORE, 1440, [])) == 1441
