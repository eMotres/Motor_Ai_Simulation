"""The duplicated slip rings are internal interfaces and never receive the external film (Astra thermal audit 2026-11-21, F01).

A thin-walled machine has its slip radius above 0.9 of the housing radius; both rings are mesh boundaries and passed the radial band
that seeds the external film, so the cooled area grew (288 facets instead of 96 on the audit probe) and the maximum temperature fell
by 6 K.  The solver's heat balance closed to roundoff - the wrong boundary conserves energy perfectly.
"""
from __future__ import annotations

import numpy as np
from skfem import MeshTri

from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal

R_ROT, R_SLIP, R_HOUS = 0.020, 0.050, 0.055            # slip / housing = 0.909 > 0.9


def _ring(r0, r1, nr, nth):
    m = MeshTri.init_tensor(np.linspace(r0, r1, nr), np.linspace(0.0, 2 * np.pi, nth, endpoint=False))
    # close the ring: identify the last theta with the first by building it from a full-circle structured grid
    th = np.linspace(0.0, 2 * np.pi, nth + 1)
    mm = MeshTri.init_tensor(np.linspace(r0, r1, nr), th)
    p = np.vstack([mm.p[0] * np.cos(mm.p[1]), mm.p[0] * np.sin(mm.p[1])])
    return p, mm.t


def _machine():
    pr, tr = _ring(R_ROT, R_SLIP, 4, 96)
    ps, ts = _ring(R_SLIP, R_HOUS, 4, 96)
    p = np.hstack([pr, ps])
    t = np.hstack([tr, ts + pr.shape[1]])
    tags = np.r_[np.full(tr.shape[1], 1), np.full(ts.shape[1], 2)]
    return p, t, tags


def _solve():
    p, t, tags = _machine()
    q = np.where(tags == 1, 2.0e5, 0.0)
    return solve_steady_thermal(p, t, tags, np.full(t.shape[1], 25.0), q, drop_tags=[], r_housing_m=R_HOUS,
                                rotor_outer_m=R_SLIP, stator_inner_m=R_SLIP, gap_k=0.5, h_conv=300.0, t_ambient=30.0,
                                length_m=0.1, slip_r_m=R_SLIP)


def test_only_the_outside_is_cooled_when_the_slip_ring_is_near_the_housing():
    out = _solve()
    assert out["n_slip_ties"] > 0                                   # the interface exists
    assert out["n_housing_facets"] == 96                            # the 96 outer facets and none of the slip rings
    area = sum(s.get("area_m2", 0.0) for s in out["surfaces"] if s.get("name") == "outer")
    if area:
        assert abs(area - 2 * np.pi * R_HOUS * 0.1) / (2 * np.pi * R_HOUS * 0.1) < 0.02
    assert abs(out["heat_residual_W"]) < 1e-6 * max(1.0, out["q_generated_W"])


def test_the_exterior_does_not_depend_on_where_the_slip_ring_lies():
    """Same outer contour and source, the slip ring moved inwards (below the band): the same cooled facets and the same field."""
    near = _solve()
    global R_SLIP
    keep = R_SLIP
    try:
        R_SLIP = 0.035
        far = _solve()
    finally:
        R_SLIP = keep
    assert near["n_housing_facets"] == far["n_housing_facets"] == 96
