"""The whole outer contour is cooled, on any mesh (owner 2026-11-04).

«вода омывает весь контур статора».  The film used to reach the notch walls only
above 0.9 R, by facet midpoint, so the same stator had 0.0270 m² on the sine mesh
and 0.0205 m² on the PWM mesh while its real contour is 0.0392 m².
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from skfem import MeshTri

from motor_ai_sim.simulation.thermal_solver_2d import outer_contour_facets, wedge_cut_facets

R0, R1, R_NOTCH = 0.05, 0.10, 0.08
SPAN, N_NOTCH, HALF_WIDTH = math.pi / 2, 3, 0.05


def _notched_ring(nr: int, nth: int, span: float = SPAN, n_notch: int = N_NOTCH,
                  half_width: float = HALF_WIDTH):
    """An annular sector with radial notches cut from the outside down to R_NOTCH.

    The grid lines run along the notch edges, so the boundary is the same curve
    at every resolution and only the facet lengths change.
    """
    centres = [span * (i + 0.5) / n_notch for i in range(n_notch)]
    edges = [c + s * half_width for c in centres for s in (-1.0, 1.0)]
    m = MeshTri.init_tensor(np.unique(np.r_[np.linspace(R0, R1, nr), R_NOTCH]),
                            np.unique(np.r_[np.linspace(0.0, span, nth), edges]))
    keep = [tri for tri in m.t.T
            if not (m.p[0, tri].mean() > R_NOTCH
                    and any(abs(m.p[1, tri].mean() - c) < half_width for c in centres))]
    r, th = m.p[0], m.p[1]
    return MeshTri(np.vstack([r * np.cos(th), r * np.sin(th)]), np.array(keep).T).remove_elements(np.array([], int))


def _select(mesh):
    bnd = mesh.boundary_facets()
    cut = wedge_cut_facets(mesh, bnd)
    r = np.hypot(mesh.p[0], mesh.p[1])[mesh.facets[:, bnd]]
    seed = np.setdiff1d(bnd[r.mean(axis=0) > 0.9 * R1], cut)
    return bnd, cut, seed, outer_contour_facets(mesh, bnd, seed, cut)


def _length(mesh, facets):
    a, b = mesh.p[:, mesh.facets[0, facets]], mesh.p[:, mesh.facets[1, facets]]
    return float(np.hypot(*(a - b)).sum())


def test_the_walls_are_included_down_to_the_notch_bottom():
    mesh = _notched_ring(21, 241)
    _bnd, _cut, seed, contour = _select(mesh)
    r = np.hypot(mesh.p[0], mesh.p[1])
    assert contour.size > seed.size
    assert r[mesh.facets[:, contour]].min() == pytest.approx(R_NOTCH, abs=1e-3)


def test_no_cut_line_and_no_bore_in_the_contour():
    mesh = _notched_ring(21, 241)
    _bnd, cut, _seed, contour = _select(mesh)
    r = np.hypot(mesh.p[0], mesh.p[1])
    assert cut.size and not np.intersect1d(contour, cut).size
    assert r[mesh.facets[:, contour]].min() > R0 * 1.1


def test_the_contour_does_not_depend_on_the_mesh():
    exact = (R1 * (SPAN - 2 * N_NOTCH * HALF_WIDTH)            # the arcs
             + 2 * N_NOTCH * (R1 - R_NOTCH)                    # the walls
             + R_NOTCH * 2 * N_NOTCH * HALF_WIDTH)             # the notch bottoms
    for nr, nth in ((3, 41), (11, 121), (41, 481)):
        mesh = _notched_ring(nr, nth)
        assert _length(mesh, _select(mesh)[3]) == pytest.approx(exact, rel=0.01), (nr, nth)


def test_a_long_wall_facet_is_not_lost():
    """One wall facet reaching below 0.9 R by midpoint: the old rule dropped it."""
    mesh = _notched_ring(3, 241)        # one element spans r = 0.075..0.10
    _bnd, _cut, seed, contour = _select(mesh)
    r = np.hypot(mesh.p[0], mesh.p[1])
    mid = r[mesh.facets[:, contour]].mean(axis=0)
    assert (mid < 0.9 * R1).any()
    assert contour.size >= seed.size


def test_a_full_disk_keeps_only_the_outer_ring():
    m = MeshTri.init_tensor(np.linspace(R0, R1, 6), np.linspace(0.0, 2 * math.pi, 73))
    r, th = m.p[0], m.p[1]
    p = np.vstack([r * np.cos(th), r * np.sin(th)])
    _, idx, inv = np.unique(np.round(p, 10), axis=1, return_index=True, return_inverse=True)
    mesh = MeshTri(p[:, idx], inv.ravel()[m.t])
    bnd = mesh.boundary_facets()
    rr = np.hypot(mesh.p[0], mesh.p[1])[mesh.facets[:, bnd]]
    seed = bnd[rr.mean(axis=0) > 0.9 * R1]
    contour = outer_contour_facets(mesh, bnd, seed, np.array([], int))
    assert np.array_equal(np.sort(contour), np.sort(seed))
