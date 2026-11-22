# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The belt's iron-side ring keeps a cell-ray node that sits a few um inside the ring circle.

A tiled rotor cell snaps its cut-ray nodes onto the mean of a cluster of ray radii, so on some rotors (a wide magnet
fill, 28 poles) the ray node on the rotor outline lands 2-3 um inside the circle.  The +-2 um window used to drop it:
the seam triangle spanned the node, and the mesh was no longer pole-pair periodic (TDM refused it).  A mesh whose ring
nodes all sit inside the window must select exactly the same nodes as before.
"""
from __future__ import annotations

import math

import numpy as np

from motor_ai_sim.simulation.mesher import _belt_ring_with_boundary_neighbours as join


def _fan(radii_um, angles_deg, r0_m=0.07255):
    """A strip: one outer node per angle at r0 + offset, one inner node below it, two triangles per cell."""
    n = len(angles_deg)
    pts = []
    for a, d in zip(angles_deg, radii_um):
        r = r0_m + d * 1e-6
        pts.append((r * math.cos(math.radians(a)), r * math.sin(math.radians(a))))
    for a in angles_deg:
        r = r0_m - 5e-4
        pts.append((r * math.cos(math.radians(a)), r * math.sin(math.radians(a))))
    P = np.array(pts).T
    T = []
    for i in range(n - 1):
        T += [(i, i + 1, n + i), (i + 1, n + i + 1, n + i)]
    return P, np.array(T).T, r0_m


def _plain(P, r_iron):
    rP = np.hypot(P[0], P[1])
    return np.where(np.abs(rP - r_iron) < 2e-6)[0], rP


def test_a_ray_node_a_few_um_inside_the_circle_joins_the_ring():
    P, T, r0 = _fan([0.0, 0.0, -2.6, 0.0, 0.0], [0.0, 0.18, 0.36, 0.54, 0.72])
    ring, rP = _plain(P, r0)
    assert 2 not in ring.tolist()                      # the plain window drops it
    out = join(P, T, ring, rP, r0)
    assert out.tolist() == sorted(ring.tolist() + [2])


def test_nothing_changes_when_every_ring_node_is_inside_the_window():
    P, T, r0 = _fan([0.0, 0.3, -0.4, 0.2, 0.0], [0.0, 0.18, 0.36, 0.54, 0.72])
    ring, rP = _plain(P, r0)
    out = join(P, T, ring, rP, r0)
    assert out.tolist() == ring.tolist()


def test_a_node_far_from_the_circle_is_never_added():
    P, T, r0 = _fan([0.0, 0.0, -21.0, 0.0, 0.0], [0.0, 0.18, 0.36, 0.54, 0.72])   # a tooth-tip sized offset
    ring, rP = _plain(P, r0)
    assert join(P, T, ring, rP, r0).tolist() == ring.tolist()


# ── the real rotor mesh of a public fixture machine, at three magnet_fill_up values ──────────────────────────────
import importlib.util  # noqa: E402

import pytest  # noqa: E402


class _MeshCaptured(BaseException):
    """BaseException: the solver's own ``except Exception`` must not eat it."""


def _rotor_mesh_at(fill_up):
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation import fem_solver_2d as F
    from tests.test_physics_regression import CASES, CONNECTION, GEO_30MM, OVERRIDE, RPM
    cap = {}
    orig = F._build_sliding_band_meshes

    def spy(*a, **k):
        out = orig(*a, **k)
        cap["mr"] = out[3]
        raise _MeshCaptured()

    kw = dict(CASES["p2_eddy"])
    geo = dict(GEO_30MM, magnet_fill_up=float(fill_up))
    F._SB_WARM_CACHE.clear()
    set_request_materials(OVERRIDE)
    F._build_sliding_band_meshes = spy
    try:
        F.fem_transient_sliding_band(geo_override=geo, rpm=RPM, connection=CONNECTION, **kw)
    except _MeshCaptured:
        pass
    finally:
        F._build_sliding_band_meshes = orig
        set_request_materials(None)
        F._SB_WARM_CACHE.clear()
    num_poles = int(GEO_30MM["num_poles_per_segment"]) * int(GEO_30MM["num_seg"])
    ns = int(kw["n_sectors"])
    return cap.get("mr"), ns, (-1 if (num_poles // ns) % 2 == 1 else 1), num_poles


@pytest.mark.skipif(importlib.util.find_spec("netgen") is None, reason="netgen is not installed")
@pytest.mark.parametrize("fill_up", [1.0, 1.5, 2.0])
def test_the_rotor_mesh_is_pole_pair_periodic_at_wide_magnet_fill(fill_up):
    """The sweep of magnet_fill_up 1.0 / 1.5 / 2.0 builds a rotor mesh the TDM period map accepts: every rotor dof,
    rotated by one electrical period (and half of it) and folded into the sector, has an image."""
    from motor_ai_sim.simulation.rotor_window import period_shift_map
    from tests.test_mesh_periodicity import _h, _p2_dofs
    mr, ns, bc_sign, num_poles = _rotor_mesh_at(fill_up)
    if mr is None:
        pytest.skip("the solver did not reach the mesh build at this fill")
    dofs, h = _p2_dofs(mr), _h(mr)
    period_deg = 360.0 / (num_poles // 2)
    for frac in (1.0, 0.5):
        for direction in (-1.0, +1.0):
            mp, info = period_shift_map(dofs, h, n_sectors=ns, bc_sign=bc_sign,
                                        rot_rad=direction * math.radians(period_deg * frac))
            assert mp is not None, "magnet_fill_up %g: not periodic for a %g-period rotation: %s" % (fill_up, frac, info)


def test_a_refused_rotor_mesh_names_the_point_in_plain_words():
    """TDM's refusal says where the mesh is not periodic (radius, angle, distance), not only a dict of numbers."""
    from motor_ai_sim.simulation.fem_solver_2d import TdmMeshNotPeriodic, _tdm_not_periodic_words
    from motor_ai_sim.simulation.rotor_window import period_shift_map
    pts = np.array([[0.07, 0.07 * math.cos(math.radians(12.857)), 0.07 * math.cos(math.radians(25.7144))],
                    [0.0, 0.07 * math.sin(math.radians(12.857)), 0.07 * math.sin(math.radians(25.7144))]])
    pts[:, 2] += 1.9e-6                                   # one point 1.9 um off its pole-pair image
    mp, info = period_shift_map(pts, 6.0e-5, n_sectors=4, bc_sign=-1, rot_rad=math.radians(-25.714286))
    assert mp is None and info["unmatched"] >= 1
    words = _tdm_not_periodic_words(info)
    assert "no pole-pair image" in words and "radius" in words and "angle" in words
    assert "no pole-pair image" in str(TdmMeshNotPeriodic(words))
