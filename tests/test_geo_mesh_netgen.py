# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) MOTRES d.o.o. and contributors
"""The netgen CDT backend of the geometry-driven mesher (evaluation,
docs/MESHER_NETGEN_2026-09-30.md).

`geo_mesh_netgen.triangulate_netgen` is the third drop-in for
`geo_mesh._triangulate`.  Asserted here, feature by feature of the gmsh
backend's parity table (docs/MESHER_TRANSITION.md), on toy PSLGs and the
40 mm 12s/14p preset:

  * the contract: input vertices first and bit-exact, domain-boundary segments
    never split (also next to a much finer neighbour, which netgen's own 1-D
    meshing would split), embedded dangling segments and free vertices kept,
    holes, per-region sizes, CCW, deterministic;
  * cusp guard, per-part size, wire-cell factor, tiling (every cell the same
    mesh), CAD sections, moving-band rings, shaft skin layers, sleeve layers;
  * fail closed: a netgen failure raises NetgenCDTError and releases the lock,
    a missing netgen is an actionable error, the budget preflight rejects
    before meshing; provenance names the netgen version.
Skipped where netgen-mesher is not installed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("netgen.occ")

from motor_ai_sim.simulation import geo_mesh as gm  # noqa: E402
from motor_ai_sim.simulation import geo_mesh_netgen as gn  # noqa: E402
from motor_ai_sim.simulation.geo_mesh_netgen import (  # noqa: E402
    NetgenCDTError, triangulate_netgen)
from motor_ai_sim.simulation.sb_domains import DOM_COIL_BASE, DOM_MAG_BASE  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
G40 = json.loads((_ROOT / "config" / "motor_presets.json")
                 .read_text(encoding="utf-8"))["my_40mm_last"]["geometry"]


@pytest.fixture(autouse=True, scope="module")
def _netgen_backend():
    gm.set_cdt_backend("netgen")
    yield
    gm.set_cdt_backend(None)


def _areas(V, T):
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return 0.5 * ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
                  - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))


def _hash(V, T):
    return hashlib.sha1(np.ascontiguousarray(V).tobytes()
                        + np.ascontiguousarray(T).tobytes()).hexdigest()


def _edges(T):
    E = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [0, 2]]]), axis=1)
    return np.unique(E, axis=0, return_counts=True)


def _conforming(V, T):
    u, cnt = _edges(T)
    return int(cnt.max()), u[cnt == 1]


# ── selection and provenance ─────────────────────────────────────────────────
def test_backend_selection_and_provenance(monkeypatch):
    gm.set_cdt_backend(None)
    try:
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "netgen")
        assert gm.cdt_backend() == "netgen"
        prov = gm.mesher_provenance("geo_cdt/netgen")
        assert prov["backend"] == "netgen"
        assert prov["netgen"] == gn.NETGEN_VALIDATED or "note" in prov
        # owner 2026-10-01: netgen IS the default (auto and unset); gmsh is
        # selectable, never a fallback; triangle was removed (2026-10-03)
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "auto")
        assert gm.cdt_backend() == "netgen"
        monkeypatch.delenv("MOTOR_AI_SIM_GEO_CDT")
        assert gm.cdt_backend() == "netgen"
        assert gm.mesher_provenance("geo_cdt/netgen")["backend"] == "netgen"
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "gmsh")
        assert gm.cdt_backend() == "gmsh"
    finally:
        gm.set_cdt_backend("netgen")


def test_missing_netgen_is_an_actionable_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("netgen"):
            raise ImportError("libTKernel.so.7.8.1: cannot open shared object file")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RuntimeError, match="pip install netgen-mesher==") as ei:
        gm.cdt_backend()
    msg = str(ei.value)
    assert "WSL2" in msg and "server" in msg and "triangle" not in msg.lower()
    assert "No backend is chosen automatically" in msg


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
    Vo, To = triangulate_netgen(V, S, 0.0, hole_pts=[[8.5, 8.5]], regions=reg)
    assert np.array_equal(Vo[:len(V)], V)                 # input vertices first
    a = _areas(Vo, To)
    assert np.all(a > 0)                                  # CCW, non-degenerate
    assert a.sum() == pytest.approx(100.0 - 1.0, rel=1e-12)   # hole removed
    c = Vo[To].mean(axis=1)
    inner = (np.abs(c[:, 0] - 5) < 2) & (np.abs(c[:, 1] - 5) < 2)
    assert a[inner].sum() == pytest.approx(16.0, rel=1e-12)
    # region sizes act (netgen's grading keeps the outer face finer than
    # gmsh's near the 0.5 mm boundary spacing: ratio 3.9 measured)
    assert a[inner].mean() < 0.5 * a[~inner].mean()
    assert a[inner].max() <= gm._cell_area(0.25) * 1.6
    # the outer boundary and the hole rim are domain boundary: never split
    u, cnt = _edges(To)
    assert cnt.max() <= 2
    assert set(map(tuple, u[cnt == 1])) == \
        set(map(tuple, np.sort(S[np.r_[0:80, 88:96]], axis=1)))
    Vo2, To2 = triangulate_netgen(V, S, 0.0, hole_pts=[[8.5, 8.5]], regions=reg)
    assert _hash(Vo, To) == _hash(Vo2, To2)               # deterministic


def _box_over_fine_box(gap, pitch=None):
    """2 x 1 box over a 1 x 0.5 interface box meshed at 0.02 mm, `gap` above
    the bottom edge; the box sides are boundary segments of `pitch` (None: one
    segment per side)."""
    corners = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    V = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        n = 1 if pitch is None else int(round(math.hypot(bx - ax, by - ay) / pitch))
        V += [[ax + (bx - ax) * k / n, ay + (by - ay) * k / n] for k in range(n)]
    nb = len(V)
    S = [[i, (i + 1) % nb] for i in range(nb)]
    V += [[0.5, gap], [1.5, gap], [1.5, 0.5 + gap], [0.5, 0.5 + gap]]
    S += [[nb, nb + 1], [nb + 1, nb + 2], [nb + 2, nb + 3], [nb + 3, nb]]
    reg = [[1.0, 0.9, 1, gm._cell_area(0.5)],
           [1.0, gap + 0.25, 2, gm._cell_area(0.02)]]
    bnd = {tuple(sorted(x)) for x in S[:nb]}
    return np.array(V, float), np.array(S, np.int64), reg, bnd


def test_boundary_next_to_a_fine_neighbour_is_not_split():
    """0.25 mm boundary segments 0.25 mm from an interface divided at 0.02 mm:
    netgen's own 1-D meshing (geom2d) splits boundary segments longer than the
    graded local size there; the fixed OCC partition must not."""
    V, S, reg, bnd = _box_over_fine_box(0.25, pitch=0.25)
    Vo, To = triangulate_netgen(V, S, 0.0, regions=reg)
    u, cnt = _edges(To)
    assert set(map(tuple, u[cnt == 1])) == bnd
    assert _areas(Vo, To).sum() == pytest.approx(2.0, rel=1e-12)


def test_mandatory_sliver_fails_closed_or_meshes_correctly():
    """One unsplittable 2 mm boundary segment 0.05 mm (also 0.3 mm) from the
    fine interface: the boundary forces cells far larger than netgen's graded
    local size.  Triangle and gmsh build them (slivers at 0.05 mm); netgen
    6.2.2607's advancing front leaves the face unmeshed.  Whatever the release
    does, the backend must never return a partial mesh: a valid mesh with the
    boundary whole, or NetgenCDTError."""
    V, S, reg, bnd = _box_over_fine_box(0.05)
    try:
        Vo, To = triangulate_netgen(V, S, 0.0, regions=reg)
    except NetgenCDTError as e:
        assert "incompletely meshed" in str(e) or "failed" in str(e)
        return
    u, cnt = _edges(To)
    assert set(map(tuple, u[cnt == 1])) == bnd
    assert _areas(Vo, To).sum() == pytest.approx(2.0, rel=1e-12)


def test_dangling_segment_and_free_vertex_are_kept():
    """A segment inside a face (both sides the same face) and a free vertex
    are mesh edges/nodes, as on Triangle and gmsh."""
    V = np.array([[0, 0], [4, 0], [4, 4], [0, 4], [1, 1], [2, 2], [3, 1.3]], float)
    S = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [4, 5]], np.int64)
    Vo, To = triangulate_netgen(V, S, gm._cell_area(1.0))
    assert np.array_equal(Vo[:len(V)], V)
    used = set(np.unique(To).tolist())
    assert {4, 5, 6} <= used
    u, _cnt = _edges(To)
    assert (4, 5) in set(map(tuple, u))
    assert _areas(Vo, To).sum() == pytest.approx(16.0, rel=1e-12)


def test_thin_pocket_is_not_a_sliver():
    V = np.array([[0, 0], [4, 0], [4, 0.1], [0, 0.1], [0, 2], [4, 2], [0, -2], [4, -2]], float)
    S = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [3, 4], [4, 5], [5, 2],
                  [0, 6], [6, 7], [7, 1]], np.int64)
    Vo, To = triangulate_netgen(V, S, gm._cell_area(1.0))
    ar, _ = gm._aspect_arr(Vo, To)
    assert ar.max() < 12.0


def test_cusp_guard_on_the_netgen_path():
    t = np.radians(np.linspace(0.0, 90.0, 61))
    arc = np.c_[2.0 + 2.0 * np.sin(t), 2.0 - 2.0 * np.cos(t)]
    V = np.vstack([[[-1.0, 0.0], [4.0, 0.0], [4.0, 3.0], [-1.0, 3.0]], arc])
    a0, a1 = 4, 4 + len(arc) - 1
    S = [[0, a0], [a0, 1], [1, a1], [a1, 2], [2, 3], [3, 0]]
    S += [[4 + i, 5 + i] for i in range(len(arc) - 1)]
    S = np.array(S, np.int64)
    assert gm._min_input_angle_deg(V, S) < 3.0
    Vo, To = gm._triangulate(V, S, gm._cell_area(0.5), hole=False)
    a = _areas(Vo, To)
    assert len(To) > 0 and np.all(a > 0)
    assert a.sum() == pytest.approx(15.0, rel=2e-3)


# ── fail closed ──────────────────────────────────────────────────────────────
def test_netgen_failure_is_loud_and_releases_the_lock(monkeypatch):
    import netgen.occ as occ
    V, S = _square_pslg()

    def boom(self, *a, **k):
        raise RuntimeError("synthetic meshing failure")
    monkeypatch.setattr(occ.OCCGeometry, "GenerateMesh", boom)
    with pytest.raises(NetgenCDTError, match="synthetic meshing failure"):
        triangulate_netgen(V, S, gm._cell_area(1.0), hole_pts=[[8.5, 8.5]])
    assert gn._LOCK.acquire(timeout=5)
    gn._LOCK.release()
    monkeypatch.undo()
    Vo, To = triangulate_netgen(V, S, gm._cell_area(1.0), hole_pts=[[8.5, 8.5]])
    assert len(To) > 0


def test_tile_does_not_fall_back_on_a_netgen_failure(machine, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise NetgenCDTError("synthetic")
    monkeypatch.setattr(gn, "triangulate_netgen", boom)
    with pytest.raises(NetgenCDTError):
        _halves(machine)
    assert len(calls) == 1


def test_budget_preflight_rejects_before_meshing(monkeypatch):
    import netgen.occ as occ
    V, S = _square_pslg()
    called = []
    monkeypatch.setattr(occ.OCCGeometry, "GenerateMesh",
                        lambda *a, **k: called.append(1))
    with pytest.raises(gm.MeshBudgetExceeded, match="before meshing"):
        triangulate_netgen(V, S, gm._cell_area(0.05), budget=1000)
    assert not called


# ── the real 40 mm machine ───────────────────────────────────────────────────
@pytest.fixture(scope="module")
def machine():
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(dict(G40))
    return m.parameters, m.get_2d_polygons(rotor_angle_deg=0.0)


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


def test_same_sections_as_gmsh(machine, full):
    gm.set_cdt_backend("gmsh")
    try:
        ref = _halves(machine)
    finally:
        gm.set_cdt_backend("netgen")
    for (V, T, t), (V0, T0, t0) in zip(full, ref):
        a, a0 = np.abs(_areas(V, T)), np.abs(_areas(V0, T0))
        for tg in np.unique(t0):
            if int(tg) in (0, 8):         # air / outer air: centroid split
                continue
            assert float(a[t == tg].sum()) == pytest.approx(
                float(a0[t0 == tg].sum()), rel=2e-3, abs=1e-4), tg
        assert 0.4 < len(T) / len(T0) < 3.0


def test_per_part_size_acts_on_netgen(machine, full):
    (_s, (Vr, Tr, tr)) = full
    (_s2, (Vr2, Tr2, tr2)) = _halves(machine, part_mesh_mm={"magnet": 0.3})
    assert int(np.sum(tr2 >= DOM_MAG_BASE)) > 1.5 * int(np.sum(tr >= DOM_MAG_BASE))
    a = np.abs(_areas(Vr2, Tr2))[tr2 >= DOM_MAG_BASE]
    # netgen's face maxh steers the advancing front (not a hard area cap)
    assert np.percentile(a, 99) <= gm._cell_area(0.3) * 1.6
    assert a.max() <= gm._cell_area(0.3) * 2.0


def test_wire_cell_factor_acts_on_netgen(machine):
    counts = {}
    for rel in (0.5, 2.0):
        (Vs, Ts, ts), _r = _halves(machine, part_mesh_mm={"coil_rel": rel})
        counts[rel] = int(np.sum(ts >= DOM_COIL_BASE))
    assert counts[0.5] > counts[2.0]


@pytest.mark.parametrize("n_sectors", [1, 2])
def test_moving_band_rings(machine, n_sectors):
    p, _ = machine
    r_ro, r_si = float(p["rotor_outer_radius"]), float(p["stator_inner_radius"])
    g = r_si - r_ro
    r1, r2 = r_ro + 0.3 * g, r_si - 0.3 * g
    (Vs, Ts, _ts), (Vr, Tr, _tr) = _halves(machine, r1_band=r1, r2_band=r2,
                                            n_sectors=n_sectors)
    n_want = 1008 // n_sectors + (1 if n_sectors > 1 else 0)
    for V, T, r in ((Vr, Tr, r1), (Vs, Ts, r2)):
        used = np.unique(T)
        rr = np.hypot(V[used, 0], V[used, 1])
        on = used[np.abs(rr - r) < 2e-3]
        assert len(on) == n_want
        k = np.mod(np.arctan2(V[on, 1], V[on, 0]), 2 * math.pi) / (2 * math.pi / 1008)
        assert (np.abs(k - np.round(k)) * 2 * math.pi * r / 1008).max() < 1.5e-3
        assert len(np.unique(np.round(k))) == n_want
        assert _conforming(V, T)[0] <= 2


# ── skin (shaft) and sleeve layers on a sleeved hollow shaft ────────────────
SKIN = {"h1_mm": 0.05, "growth": 1.5, "chord_mm": 0.2, "h_max_mm": 0.4}


@pytest.fixture(scope="module")
def sleeved():
    sys.path.insert(0, str(_ROOT / "tests"))
    from test_sleeve import GEO, SLEEVE_MM
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(dict(GEO, sleeve_thickness=SLEEVE_MM))
    return m.parameters, m.get_2d_polygons(rotor_angle_deg=0.0)


def _rotor_sk(sleeved, layers):
    from motor_ai_sim.simulation.sb_domains import DOM_SHAFT, DOM_SLEEVE
    p, polys = sleeved
    _ri, r_out = gm._sleeve_radii(polys)
    _ms, _ts, _cs, mr, tr, _cr = gm.geo_mesh_halves(
        p, polys, r_si=float(p["stator_inner_radius"]), r_ro=float(r_out),
        n_slip=1008, mesh_edge_mm=0.5, n_sectors=1,
        skin_layers={"shaft": dict(SKIN), "sleeve": {"layers": layers}})
    return mr.p.T * 1e3, mr.t.T, np.asarray(tr), DOM_SHAFT, DOM_SLEEVE


def test_shaft_skin_layers_structure(sleeved):
    p, _polys = sleeved
    V, T, tags, DOM_SHAFT, _ = _rotor_sk(sleeved, 2)
    r_sh, r_b = float(p["rotor_inner_radius"]), float(p["shaft_inner_radius"])
    want = gm.skin_layer_radii(r_sh, r_b, SKIN["h1_mm"], SKIN["growth"], SKIN["h_max_mm"])
    nodes = np.unique(T[tags == DOM_SHAFT])
    rr = np.hypot(V[nodes, 0], V[nodes, 1])
    for r in want:
        assert np.sum(np.abs(rr - r) < 2e-3) >= 16, r
    mx, bnd = _conforming(V, T)
    assert mx <= 2
    rb = np.hypot(V[bnd, 0], V[bnd, 1])
    assert np.all(np.abs(rb - rb.max()) < 2e-3)


def test_sleeve_resolution_follows_the_layer_request(sleeved):
    p, polys = sleeved
    r_in, r_out = gm._sleeve_radii(polys)
    t = r_out - r_in
    prev = None
    for n in (2, 4):
        V, T, tags, _s, DOM_SLEEVE = _rotor_sk(sleeved, n)
        a = np.abs(_areas(V, T))[tags == DOM_SLEEVE]
        assert math.sqrt(a.mean() / 0.433) <= 1.25 * t / n
        assert a.sum() == pytest.approx(polys["sleeve"].area, rel=5e-3)
        if prev is not None:
            # never coarser; netgen's grading (0.3) already carries the slip-grid
            # spacing of the OD (0.09 mm) through the 0.4 mm ring at n = 2
            assert len(a) >= prev
        prev = len(a)
        assert _conforming(V, T)[0] <= 2
