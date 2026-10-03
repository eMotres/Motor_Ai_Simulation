# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""The rotor mesh of every CDT backend is pole-pair periodic (what TDM needs),
and a TDM request on a mesh that is not periodic is REFUSED, never marched.

TDM (the default eddy method, ``time_periodic.py``) maps the rotor dofs through
``rotor_window.period_shift_map`` for a rotation by one electrical period (one
pole pair) or half of it.  Every rotor dof, rotated and folded into the
modelled sector, must have an image within ``MATCH_REL_TOL`` (1e-6) x h of its
own, h = sqrt(smallest rotor triangle area) -- the SAME tolerance TDM applies.

The test builds the REAL rotor half of the 30 mm 12s/14p fixture through the
unchanged solver entry point (the solve is aborted right after the mesh), per
CDT backend that is installed, and applies the SAME map to the P2 dofs (nodes +
edge midpoints).  ``scripts/mesh_periodicity_check.py`` is the owner-run
variant for saved duties (L12, L13, L155) with the distance report.

Netgen is the default backend; gmsh and Triangle are checked where installed.
"""
from __future__ import annotations

import importlib.util
import math
from typing import Dict, Tuple

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation import geo_mesh as gm
from motor_ai_sim.simulation.rotor_window import MATCH_REL_TOL, period_shift_map

from tests.test_physics_regression import (CASES, CONNECTION, GEO_30MM,
                                           OVERRIDE, RPM)


class _MeshCaptured(BaseException):
    """BaseException: the solver's own ``except Exception`` must not eat it."""


def _have(backend: str) -> bool:
    mod = {"netgen": "netgen", "gmsh": "gmsh", "triangle": "triangle"}[backend]
    if importlib.util.find_spec(mod) is None:
        return False
    try:
        if backend == "netgen":
            import netgen.occ  # noqa: F401
        elif backend == "gmsh":
            import gmsh  # noqa: F401
        else:
            import triangle  # noqa: F401
    except Exception:       # noqa: BLE001 -- e.g. blocked DLLs on Windows
        return False
    return True


BACKENDS = [pytest.param(b, marks=pytest.mark.skipif(
    not _have(b), reason="%s is not installed / cannot be loaded" % b))
    for b in ("netgen", "gmsh", "triangle")]


def _rotor_mesh(backend: str):
    """(MeshTri of the rotor half, n_sectors, bc_sign, num_poles) of the 30 mm
    fixture on ``backend``; the solve is abandoned right after the mesh."""
    cap: Dict[str, object] = {}
    orig = F._build_sliding_band_meshes

    def spy(*a, **k):
        out = orig(*a, **k)
        cap["mr"] = out[3]
        raise _MeshCaptured()

    kw = dict(CASES["p2_eddy"])
    gm.set_cdt_backend(backend)
    F._SB_WARM_CACHE.clear()
    set_request_materials(OVERRIDE)
    F._build_sliding_band_meshes = spy
    try:
        F.fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                     connection=CONNECTION, **kw)
    except _MeshCaptured:
        pass
    finally:
        F._build_sliding_band_meshes = orig
        set_request_materials(None)
        gm.set_cdt_backend(None)
        F._SB_WARM_CACHE.clear()
    assert "mr" in cap, "the solver never reached the mesh build"
    ns = int(kw["n_sectors"])
    num_poles = int(GEO_30MM["num_poles_per_segment"]) * int(GEO_30MM["num_seg"])
    bc_sign = -1 if ((num_poles // ns) % 2 == 1) else 1
    return cap["mr"], ns, bc_sign, num_poles


def _p2_dofs(mesh) -> np.ndarray:
    """Nodes + edge midpoints of the mesh: the dof locations of a P2 basis."""
    P = np.asarray(mesh.p, float)
    T = np.asarray(mesh.t, int)
    E = np.sort(np.vstack([T[[0, 1]].T, T[[1, 2]].T, T[[0, 2]].T]), axis=1)
    E = np.unique(E, axis=0)
    mid = 0.5 * (P[:, E[:, 0]] + P[:, E[:, 1]])
    used = np.unique(T)
    return np.hstack([P[:, used], mid])


def _h(mesh) -> float:
    P = np.asarray(mesh.p, float)
    T = np.asarray(mesh.t, int)
    a, b, c = P[:, T[0]], P[:, T[1]], P[:, T[2]]
    area = 0.5 * np.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))
    return float(np.sqrt(area.min()))


@pytest.mark.slow
@pytest.mark.parametrize("backend", BACKENDS)
def test_rotor_mesh_is_pole_pair_periodic(backend):
    """Full-period and half-period rotations, both directions: every rotor dof
    has an image inside TDM's own tolerance (MATCH_REL_TOL x h)."""
    mr, ns, bc_sign, num_poles = _rotor_mesh(backend)
    dofs = _p2_dofs(mr)
    h = _h(mr)
    period_deg = 360.0 / (num_poles // 2)
    for frac in (1.0, 0.5):
        for direction in (-1.0, +1.0):
            mp, info = period_shift_map(
                dofs, h, n_sectors=ns, bc_sign=bc_sign,
                rot_rad=direction * math.radians(period_deg * frac))
            assert mp is not None, (
                "%s rotor mesh is NOT pole-pair periodic for a %g-period "
                "rotation (%s direction): %s -- TDM would refuse it"
                % (backend, frac, direction, info))
    assert MATCH_REL_TOL <= 1e-6        # the tolerance is not loosened here


def test_a_non_periodic_rotor_mesh_refuses_tdm_loudly(monkeypatch):
    """TDM is the default.  When its period map finds the rotor mesh not
    periodic (the whole-wedge fallback), the solve raises TdmMeshNotPeriodic
    with a machine-readable code; it is NOT silently marched.  An explicit
    march on the same mesh is untouched (it never builds the map)."""
    pytest.importorskip("netgen.occ")
    monkeypatch.setattr(F, "_period_shift_map",
                        lambda *a, **k: (None, {"reason": "forced: not periodic"}))
    kw = dict(CASES["p2_eddy"])
    F._SB_WARM_CACHE.clear()
    set_request_materials(OVERRIDE)
    try:
        with pytest.raises(F.TdmMeshNotPeriodic) as ei:
            F.fem_transient_sliding_band(geo_override=dict(GEO_30MM), rpm=RPM,
                                         connection=CONNECTION, **kw)
    finally:
        set_request_materials(None)
        F._SB_WARM_CACHE.clear()
    e = ei.value
    assert e.code == "tdm_mesh_not_periodic"
    assert "tdm_mesh_not_periodic" in str(e) and "march" in str(e)
    assert "forced: not periodic" in e.detail
    assert not isinstance(e, F.TdmAttemptFailed)    # not routed to the march


def test_tdm_mesh_error_is_not_swallowed_by_the_march_fallback():
    """Static guard: the TDM attempt's catch-all re-raises the mesh reject
    before wrapping anything into a TdmAttemptFailed (which marches)."""
    import inspect
    src = inspect.getsource(F._fem_transient_sliding_band_once)
    i = src.index("except Exception as _e_tdm")
    j = src.index("raise TdmAttemptFailed(", i)
    block = src[i:j]
    assert "isinstance(_e_tdm, TdmMeshNotPeriodic)" in block and "raise" in block
