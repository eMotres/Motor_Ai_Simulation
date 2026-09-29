# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""The gmsh CDT backend of the geometry-driven mesher (MESHER_TRANSITION S2).

`geo_mesh._triangulate` was the only Triangle call of the geometry mesher;
`geo_mesh_gmsh.triangulate_gmsh` replaces it.  Asserted here, feature by
feature of docs/MESHER_TRANSITION.md, on the 40 mm 12s/14p preset (fast):

  * the backend contract on toy PSLGs (input vertices first, boundary
    segments never split, holes, per-region sizes, CCW, deterministic);
  * cusp guard: a 0.5 deg tangent cusp builds;
  * per-part element size and the wire-cell factor act on the gmsh path;
  * tiling: every pole/slot-pair cell is the same mesh, rotated; repeat builds
    are bit-identical;
  * exact tagging: every region keeps its CAD section, as on Triangle;
  * moving-band rings R1/R2 carry exactly the uniform slip grid;
  * the optimizer mesh budget fires (and an unreached budget changes nothing);
  * the shaft skin layer: the whole of tests/test_conductor_skin_mesh.py and
    tests/test_mesh_shaft_region.py re-run on this backend (test_zz_*).
Where Triangle is installed the same machine is also compared with it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation import geo_mesh as gm
from motor_ai_sim.simulation.geo_mesh_gmsh import triangulate_gmsh
from motor_ai_sim.simulation.sb_domains import DOM_COIL_BASE, DOM_MAG_BASE

_ROOT = Path(__file__).resolve().parents[1]
G40 = json.loads((_ROOT / "config" / "motor_presets.json")
                 .read_text(encoding="utf-8"))["my_40mm_last"]["geometry"]


@pytest.fixture(autouse=True, scope="module")
def _gmsh_backend():
    gm.set_cdt_backend("gmsh")
    yield
    gm.set_cdt_backend(None)


def _areas(V, T):
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return 0.5 * ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
                  - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))


def _hash(V, T):
    return hashlib.sha1(np.ascontiguousarray(V).tobytes()
                        + np.ascontiguousarray(T).tobytes()).hexdigest()


# ── backend selection ────────────────────────────────────────────────────────
def test_backend_selection(monkeypatch):
    gm.set_cdt_backend(None)
    try:
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "gmsh")
        assert gm.cdt_backend() == "gmsh"
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "auto")
        assert gm.cdt_backend() == ("triangle" if gm.HAVE_TRIANGLE else "gmsh")
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "bogus")
        with pytest.raises(ValueError):
            gm.cdt_backend()
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "triangle")
        if gm.HAVE_TRIANGLE:
            assert gm.cdt_backend() == "triangle"
        else:
            with pytest.raises(RuntimeError):
                gm.cdt_backend()
        with pytest.raises(ValueError):
            gm.set_cdt_backend("delaunay")
    finally:
        gm.set_cdt_backend("gmsh")


# ── the contract on toy PSLGs ────────────────────────────────────────────────
def _square_pslg():
    """10 x 10 box, 4 x 4 inner box (a material interface), a 1 x 1 hole."""
    def ring(x0, y0, x1, y1, n):
        e = []
        for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                                   ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            for k in range(n):
                e.append((ax + (bx - ax) * k / n, ay + (by - ay) * k / n))
        return e
    rings = [ring(0, 0, 10, 10, 20), ring(3, 3, 7, 7, 2), ring(8, 8, 9, 9, 2)]
    V, S = [], []
    for r in rings:
        b = len(V)
        V += r
        S += [(b + i, b + (i + 1) % len(r)) for i in range(len(r))]
    return np.array(V, float), np.array(S, np.int64)


def test_contract_on_a_toy_pslg():
    V, S = _square_pslg()
    reg = [[1.0, 1.0, 1, gm._cell_area(1.0)], [5.0, 5.0, 2, gm._cell_area(0.25)]]
    Vo, To = triangulate_gmsh(V, S, 0.0, hole_pts=[[8.5, 8.5]], regions=reg)
    assert np.array_equal(Vo[:len(V)], V)                 # input vertices first
    a = _areas(Vo, To)
    assert np.all(a > 0)                                  # CCW, non-degenerate
    assert a.sum() == pytest.approx(100.0 - 1.0, rel=1e-12)   # hole removed
    c = Vo[To].mean(axis=1)
    inner = (np.abs(c[:, 0] - 5) < 2) & (np.abs(c[:, 1] - 5) < 2)
    assert a[inner].sum() == pytest.approx(16.0, rel=1e-12)
    # region sizes honoured: the inner box meshes at ~0.25 mm, the outer at ~1
    assert a[inner].mean() < 0.25 * a[~inner].mean()
    assert a[inner].max() <= gm._cell_area(0.25) * 1.6
    # the OUTER boundary and the hole rim are domain boundary: never split
    E = np.sort(np.vstack([To[:, [0, 1]], To[:, [1, 2]], To[:, [0, 2]]]), axis=1)
    u, cnt = np.unique(E, axis=0, return_counts=True)
    assert cnt.max() <= 2
    bnd = u[cnt == 1]
    assert set(map(tuple, np.sort(bnd, axis=1))) == \
        set(map(tuple, np.sort(S[np.r_[0:80, 88:96]], axis=1)))
    # deterministic
    Vo2, To2 = triangulate_gmsh(V, S, 0.0, hole_pts=[[8.5, 8.5]], regions=reg)
    assert _hash(Vo, To) == _hash(Vo2, To2)


def test_thin_pocket_is_not_a_sliver():
    """Two 4 mm chords 0.1 mm apart, closed by short ends: the interior
    interfaces are subdivided by the feature size so no triangle is a sliver
    (the frozen version gave aspect ~1000)."""
    V = np.array([[0, 0], [4, 0], [4, 0.1], [0, 0.1], [0, 2], [4, 2], [0, -2], [4, -2]], float)
    S = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [3, 4], [4, 5], [5, 2],
                  [0, 6], [6, 7], [7, 1]], np.int64)
    Vo, To = triangulate_gmsh(V, S, gm._cell_area(1.0))
    ar, _ = gm._aspect_arr(Vo, To)
    assert ar.max() < 12.0


def test_cusp_guard_on_the_gmsh_path():
    """A fillet arc tangent to a wall (sub-degree input angle) builds.  Box
    (-1,0)-(4,3); arc of radius 2 from the tangent point (2,0) to (4,2)."""
    t = np.radians(np.linspace(0.0, 90.0, 61))
    arc = np.c_[2.0 + 2.0 * np.sin(t), 2.0 - 2.0 * np.cos(t)]      # (2,0)..(4,2)
    V = np.vstack([[[-1.0, 0.0], [4.0, 0.0], [4.0, 3.0], [-1.0, 3.0]], arc])
    a0, a1 = 4, 4 + len(arc) - 1
    S = [[0, a0], [a0, 1], [1, a1], [a1, 2], [2, 3], [3, 0]]
    S += [[4 + i, 5 + i] for i in range(len(arc) - 1)]
    S = np.array(S, np.int64)
    assert gm._min_input_angle_deg(V, S) < 3.0
    Vo, To = gm._triangulate(V, S, gm._cell_area(0.5), hole=False)
    a = _areas(Vo, To)
    assert len(To) > 0 and np.all(a > 0)
    assert a.sum() == pytest.approx(15.0, rel=2e-3)   # blunting moves < sagitta


# ── the real 40 mm machine ───────────────────────────────────────────────────
def _machine():
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(dict(G40))
    p = m.parameters
    return p, m.get_2d_polygons(rotor_angle_deg=0.0)


@pytest.fixture(scope="module")
def machine():
    return _machine()


def _halves(machine, **kw):
    p, polys = machine
    kw.setdefault("mesh_edge_mm", 1.0)
    ms, ts, _, mr, tr, _ = gm.geo_mesh_halves(
        p, polys, r_si=float(p["stator_inner_radius"]),
        r_ro=float(p["rotor_outer_radius"]), n_slip=1008, **kw)
    return (ms.p.T * 1e3, ms.t.T, np.asarray(ts)), (mr.p.T * 1e3, mr.t.T, np.asarray(tr))


@pytest.fixture(scope="module")
def full(machine):
    return _halves(machine)


def test_repeat_build_is_bit_identical(machine, full):
    again = _halves(machine)
    for (V, T, t), (V2, T2, t2) in zip(full, again):
        assert _hash(V, T) == _hash(V2, T2)
        assert np.array_equal(t, t2)


def _rot(V, ang):
    c, s = math.cos(ang), math.sin(ang)
    return V @ np.array([[c, s], [-s, c]])


@pytest.mark.parametrize("half,period", [(0, "pair"), (1, "pole")])
def test_every_cell_is_the_same_mesh(machine, full, half, period):
    """Rotation by one cell pitch maps the mesh onto itself: every node lands
    on a node (1 nm) and every triangle's centroid on a centroid."""
    p, _ = machine
    V, T, _t = full[half]
    n = int(p["num_slots"]) // 2 if period == "pair" else int(p["num_poles"])
    from scipy.spatial import cKDTree
    for X in (V[np.unique(T)], V[T].mean(axis=1)):
        d, _ = cKDTree(X).query(_rot(X, 2 * math.pi / n))
        assert d.max() < 1e-6


def test_every_region_keeps_its_cad_section(machine, full):
    p, polys = machine
    (Vs, Ts, ts), (Vr, Tr, tr) = full
    As, Ar = np.abs(_areas(Vs, Ts)), np.abs(_areas(Vr, Tr))
    mag = sum(m[0].area for m in polys["magnets"] if m is not None)
    coil = sum(c.area for c in polys["coils"] if c is not None)
    assert Ar[tr >= DOM_MAG_BASE].sum() == pytest.approx(mag, rel=2e-3)
    assert As[ts >= DOM_COIL_BASE].sum() == pytest.approx(coil, rel=2e-3)


@pytest.mark.skipif(not gm.HAVE_TRIANGLE, reason="Triangle not installed")
def test_same_sections_as_triangle(machine, full):
    gm.set_cdt_backend("triangle")
    try:
        tri = _halves(machine)
    finally:
        gm.set_cdt_backend("gmsh")
    for (V, T, t), (V0, T0, t0) in zip(full, tri):
        a, a0 = np.abs(_areas(V, T)), np.abs(_areas(V0, T0))
        for tg in np.unique(t0):
            want = float(a0[t0 == tg].sum())
            got = float(a[t == tg].sum())
            if int(tg) in (0, 8):         # air vs outer air: split by a circle
                continue                  # not in the PSLG, tagged by centroid
            assert got == pytest.approx(want, rel=2e-3, abs=1e-4), tg
        # comparable size: same boundary, graded fill within 0.5x-3x
        assert 0.5 < len(T) / len(T0) < 3.0


def test_per_part_size_acts_on_gmsh(machine, full):
    (_s, (Vr, Tr, tr)) = full
    (_s2, (Vr2, Tr2, tr2)) = _halves(machine, part_mesh_mm={"magnet": 0.3})
    n0 = int(np.sum(tr >= DOM_MAG_BASE))
    n1 = int(np.sum(tr2 >= DOM_MAG_BASE))
    assert n1 > 1.5 * n0
    a = np.abs(_areas(Vr2, Tr2))[tr2 >= DOM_MAG_BASE]
    assert a.max() <= gm._cell_area(0.3) * 1.6


def test_wire_cell_factor_acts_on_gmsh(machine):
    counts = {}
    for rel in (0.5, 2.0):
        (Vs, Ts, ts), _r = _halves(machine, part_mesh_mm={"coil_rel": rel})
        counts[rel] = int(np.sum(ts >= DOM_COIL_BASE))
    assert counts[0.5] > counts[2.0]


def test_moving_band_rings(machine):
    p, _ = machine
    r_ro, r_si = float(p["rotor_outer_radius"]), float(p["stator_inner_radius"])
    g = r_si - r_ro
    r1, r2 = r_ro + 0.3 * g, r_si - 0.3 * g
    (Vs, Ts, _ts), (Vr, Tr, _tr) = _halves(machine, r1_band=r1, r2_band=r2)
    for V, T, r in ((Vr, Tr, r1), (Vs, Ts, r2)):
        used = np.unique(T)
        rr = np.hypot(V[used, 0], V[used, 1])
        on = used[np.abs(rr - r) < 2e-3]          # 1 um coordinate snap
        assert len(on) == 1008
        ang = np.sort(np.mod(np.arctan2(V[on, 1], V[on, 0]), 2 * math.pi))
        assert np.allclose(np.diff(ang), 2 * math.pi / 1008, atol=1e-4)


def test_mesh_budget_on_gmsh(machine, full):
    try:
        gm.set_tri_budget(500)
        with pytest.raises(gm.MeshBudgetExceeded):
            _halves(machine)
        gm.set_tri_budget(10_000_000)          # armed, not reached: no change
        again = _halves(machine)
    finally:
        gm.set_tri_budget(None)
    for (V, T, _t), (V2, T2, _t2) in zip(full, again):
        assert _hash(V, T) == _hash(V2, T2)


# ── shaft skin layers and the shaft region, re-run on this backend ───────────
@pytest.mark.parametrize("mod", ["test_conductor_skin_mesh.py",
                                 "test_mesh_shaft_region.py"])
def test_zz_skin_and_shaft_suites_on_gmsh(mod):
    env = dict(os.environ, MOTOR_AI_SIM_GEO_CDT="gmsh")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(_ROOT / "tests" / mod)], cwd=str(_ROOT), env=env,
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert " passed" in r.stdout
