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
        # owner 2026-10-01: the default is netgen; with netgen missing the
        # default FAILS CLOSED (a RuntimeError), it never falls back to gmsh
        for _unset in ("auto", None):
            if _unset is None:
                monkeypatch.delenv("MOTOR_AI_SIM_GEO_CDT")
            else:
                monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", _unset)
            try:
                import netgen.occ  # noqa: F401
                assert gm.cdt_backend() == "netgen"
            except ImportError:
                with pytest.raises(RuntimeError, match="netgen"):
                    gm.cdt_backend()
        monkeypatch.setenv("MOTOR_AI_SIM_GEO_CDT", "gmsh")
        prov = gm.mesher_provenance("geo_cdt/gmsh")
        assert prov["build"] == "geo_cdt/gmsh" and prov["backend"] == "gmsh"
        assert prov["gmsh"] == gm.GMSH_VALIDATED or "note" in prov
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
    """SAME-ENVIRONMENT reproducibility: one process, one gmsh build, one
    thread.  Bit identity across gmsh releases/platforms is NOT promised —
    test_semantic_fingerprint defines cross-version compatibility."""
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


BACKENDS = ["gmsh"] + (["triangle"] if gm.HAVE_TRIANGLE else [])


class _Backend:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        gm.set_cdt_backend(self.name)

    def __exit__(self, *a):
        gm.set_cdt_backend("gmsh")


def _conforming(V, T):
    """Edges used by >2 triangles (never) and the boundary edge list."""
    E = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [0, 2]]]), axis=1)
    u, cnt = np.unique(E, axis=0, return_counts=True)
    return int(cnt.max()), u[cnt == 1]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("n_sectors", [1, 2])
def test_moving_band_rings(machine, backend, n_sectors):
    """R1/R2 (harmonic-macro rings, mesh level only: the macro solve is not
    implemented on P2, see below) carry exactly the uniform slip grid on
    BOTH backends: every grid angle k*2pi/1008 of the model span holds one
    node on the ring (the band-ring pinning fix; 1002/1008 before it), the
    nodes sit on the grid angles, and the halves stay conforming."""
    p, _ = machine
    r_ro, r_si = float(p["rotor_outer_radius"]), float(p["stator_inner_radius"])
    g = r_si - r_ro
    r1, r2 = r_ro + 0.3 * g, r_si - 0.3 * g
    with _Backend(backend):
        (Vs, Ts, _ts), (Vr, Tr, _tr) = _halves(machine, r1_band=r1, r2_band=r2,
                                                n_sectors=n_sectors)
    n_want = 1008 // n_sectors + (1 if n_sectors > 1 else 0)   # open wedge: both ends
    for V, T, r in ((Vr, Tr, r1), (Vs, Ts, r2)):
        used = np.unique(T)
        rr = np.hypot(V[used, 0], V[used, 1])
        on = used[np.abs(rr - r) < 2e-3]          # 1 um coordinate snap
        assert len(on) == n_want
        k = np.mod(np.arctan2(V[on, 1], V[on, 0]), 2 * math.pi) / (2 * math.pi / 1008)
        # on the grid angles, up to the 1 um coordinate snap (0.6 um measured)
        assert (np.abs(k - np.round(k)) * 2 * math.pi * r / 1008).max() < 1.5e-3
        assert len(np.unique(np.round(k))) == n_want          # one node per angle
        assert _conforming(V, T)[0] <= 2


@pytest.mark.slow
def test_moving_band_solve_is_refused_loudly():
    """The R1/R2 rings serve the harmonic-macro (moving-band) gap, which the
    P2 solver does NOT implement: a request must fail loudly before any
    solve, never degrade silently.  (Server finding 2026-09-29: the planned
    end-to-end macro solve raised this; the production sliding-band path is
    the merged structured belt, solved end to end on both backends in the
    saved-duty comparison.)"""
    from motor_ai_sim.simulation.fem_solver_2d import em_transient_eval
    with pytest.raises(NotImplementedError, match="not implemented on P2"):
        em_transient_eval(n_steps_per_period=24, n_periods=1.0, gamma_deg=0.0,
                          I_phase_rms=10.0, rpm=3000.0, mesh_size_mm=1.0,
                          min_size_mm=0.3, outer_air_factor=1.2, gap_layers=1,
                          n_sectors=2, rotor_eddy=False, iron_template=True,
                          geo_mesh=True, structured_gap=True, airgap_macro=True,
                          geo_override=dict(G40), eddy=False)


# ── Coulomb virtual-work layers on the real stitched halves ────────────────
def _coulomb_layers(backend):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    from motor_ai_sim.simulation.mesher import (_simplify_polys,
                                                _build_sliding_band_meshes,
                                                build_trace)
    from motor_ai_sim.simulation.virtual_work_torque import (sliding_band_layers,
                                                             AIR_TAGS)
    m = CadQueryMotor()
    m.set_parameters(dict(G40))
    p = m.parameters
    raw = m.get_2d_polygons(rotor_angle_deg=0.0)
    with _Backend(backend):
        # exactly the solver's call (fem_transient_sliding_band): merged band,
        # structured gap, 1 layer/side, the solver's stator_fillet_mm = 0
        polys = _simplify_polys(raw, tol_mm=0.005, stator_fillet_mm=0.0,
                                n_slip=1008, gap_layers=1, structured_gap=True,
                                band_mode="merged")
        ms, ts, _c, mr, tr, _c2 = _build_sliding_band_meshes(
            polys, 0.0, 1.0, min_size_mm=0.3, outer_air_factor=1.2,
            band_thickness_mm=0.4, n_sectors=2, geo_cfg=p, gap_layers=1,
            full_ring=False, iron_template=True, geo_mesh=True)
        trace = build_trace()
    ns = ms.p.shape[1]
    P = np.hstack([ms.p, mr.p])
    T = np.hstack([ms.t, mr.t + ns])
    tags = np.concatenate([ts, tr]).astype(int)
    rr = np.hypot(*P)
    r_rot = rr[ns:][np.unique(mr.t[:, ~np.isin(tr, AIR_TAGS)])].max()
    r_sta = rr[:ns][np.unique(ms.t[:, ~np.isin(ts, AIR_TAGS)])].min()
    layers = sliding_band_layers(P, T, ns, np.isin(tags, AIR_TAGS), r_rot,
                                 float(polys["mid_r_mm"]) * 1e-3, r_sta)
    return layers, trace


def test_coulomb_layers_are_pure_air_on_both_backends():
    """Coulomb's virtual-work torque displaces the rotor-side and stator-side
    gap air rings (docs/COULOMB_TORQUE_2026-09-30.md); both must be pure air
    on the geometry-driven mesh of each backend (sliding_band_layers raises
    CoulombLayerError otherwise).  The rotor-side ring is the structured belt
    (identical on both); the stator-side ring also takes the few CDT air
    elements of the slot openings next to the innermost stator iron."""
    got = {}
    for be in BACKENDS:
        layers, trace = _coulomb_layers(be)
        assert not trace["events"], trace["events"]            # no fallback
        assert trace["structured_gap_effective"]
        got[be] = {k: len(v.elements) for k, v in layers.items()}
        assert got[be]["rotor_side"] > 0 and got[be]["stator_side"] > 0
    if len(got) == 2:
        assert got["gmsh"]["rotor_side"] == got["triangle"]["rotor_side"]
        assert got["gmsh"]["stator_side"] == pytest.approx(
            got["triangle"]["stator_side"], rel=0.02)


# ── skin (shaft) and sleeve layers, per backend, on a sleeved hollow shaft ───
def _sleeved():
    """tests/test_sleeve.py's 30 mm 12s/14p (1.0 mm gap) with a 0.4 mm sleeve;
    its shaft is a TUBE (bore 0.9 mm, OD 2.9 mm)."""
    sys.path.insert(0, str(_ROOT / "tests"))
    from test_sleeve import GEO, SLEEVE_MM
    from motor_ai_sim.cadquery_geometry import CadQueryMotor
    m = CadQueryMotor()
    m.set_parameters(dict(GEO, sleeve_thickness=SLEEVE_MM))
    return m.parameters, m.get_2d_polygons(rotor_angle_deg=0.0)


SKIN = {"h1_mm": 0.05, "growth": 1.5, "chord_mm": 0.2, "h_max_mm": 0.4}


@pytest.fixture(scope="module")
def sleeved():
    return _sleeved()


def _rotor_sk(sleeved, backend, layers, n_sectors=1):
    from motor_ai_sim.simulation.sb_domains import DOM_SHAFT, DOM_SLEEVE
    p, polys = sleeved
    _ri, r_out = gm._sleeve_radii(polys)
    with _Backend(backend):
        _ms, _ts, _cs, mr, tr, _cr = gm.geo_mesh_halves(
            p, polys, r_si=float(p["stator_inner_radius"]), r_ro=float(r_out),
            n_slip=1008, mesh_edge_mm=0.5, n_sectors=n_sectors,
            skin_layers={"shaft": dict(SKIN), "sleeve": {"layers": layers}})
    return mr.p.T * 1e3, mr.t.T, np.asarray(tr), DOM_SHAFT, DOM_SLEEVE


@pytest.mark.parametrize("backend", BACKENDS)
def test_shaft_skin_layers_structure(sleeved, backend):
    """The structured wall: every planned ring is a node ring of the shaft
    (layer count), the first cell is h1, successive layers grow by the ratio
    until the cap, and the stitched rotor has no hanging node."""
    p, _polys = sleeved
    V, T, tags, DOM_SHAFT, _ = _rotor_sk(sleeved, backend, 2)
    r_sh, r_b = float(p["rotor_inner_radius"]), float(p["shaft_inner_radius"])
    want = gm.skin_layer_radii(r_sh, r_b, SKIN["h1_mm"], SKIN["growth"], SKIN["h_max_mm"])
    nodes = np.unique(T[tags == DOM_SHAFT])
    rr = np.hypot(V[nodes, 0], V[nodes, 1])
    for r in want:                                     # every planned ring exists
        assert np.sum(np.abs(rr - r) < 2e-3) >= 16, r
    t = -np.diff(want)
    assert t[0] == pytest.approx(SKIN["h1_mm"], abs=2e-3)              # first cell
    grow = t[1:] / t[:-1]
    capped = t[1:] >= SKIN["h_max_mm"] - 1e-9
    assert np.allclose(grow[~capped][:-1], SKIN["growth"], rtol=1e-6)  # progression
    assert len(want) - 1 >= 4                                          # layer count
    mx, bnd = _conforming(V, T)
    assert mx <= 2
    rb = np.hypot(V[bnd, 0], V[bnd, 1])            # boundary = the rotor OD only
    assert np.all(np.abs(rb - rb.max()) < 2e-3)


@pytest.mark.skipif(len(BACKENDS) < 2, reason="Triangle not installed")
def test_shaft_skin_patch_identical_on_both_backends(sleeved):
    """The structured patch is shared code: its node rings are the same on
    Triangle and gmsh (only the CDT around it differs)."""
    rings = {}
    for be in BACKENDS:
        V, T, tags, DOM_SHAFT, _ = _rotor_sk(sleeved, be, 2)
        nodes = np.unique(T[tags == DOM_SHAFT])
        rings[be] = np.unique(np.round(np.hypot(V[nodes, 0], V[nodes, 1]), 4))
    assert np.array_equal(rings["gmsh"], rings["triangle"])


@pytest.mark.parametrize("backend", BACKENDS)
def test_sleeve_resolution_follows_the_layer_request(sleeved, backend):
    """The sleeve is CDT-meshed to the target cell of `layers` elements
    across its thickness (it is not a structured patch on either backend):
    the mean cell edge is <= 1.25 t/n, it refines when n doubles, and the ring
    region keeps its CAD section."""
    p, polys = sleeved
    r_in, r_out = gm._sleeve_radii(polys)
    t = r_out - r_in
    prev = None
    for n in (2, 4):
        V, T, tags, _s, DOM_SLEEVE = _rotor_sk(sleeved, backend, n)
        a = np.abs(_areas(V, T))[tags == DOM_SLEEVE]
        edge = math.sqrt(a.mean() / 0.433)
        assert edge <= 1.25 * t / n
        assert a.sum() == pytest.approx(polys["sleeve"].area, rel=5e-3)
        if prev is not None:
            assert len(a) > 1.5 * prev      # boundary chords bound the n^2 scaling
        prev = len(a)
        assert _conforming(V, T)[0] <= 2


# ── fail closed ──────────────────────────────────────────────────────────────
def test_gmsh_failure_is_loud_and_cleans_up(monkeypatch):
    """A gmsh meshing failure raises GmshCDTError (never a silent fallback),
    finalizes the gmsh session it opened, and releases the process lock; the
    next triangulation works."""
    import threading
    import gmsh
    from motor_ai_sim.simulation.geo_mesh_gmsh import GmshCDTError
    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK
    V, S = _square_pslg()

    def boom(*a, **k):
        raise Exception("synthetic meshing failure")
    monkeypatch.setattr(gmsh.model.mesh, "generate", boom)
    with pytest.raises(GmshCDTError, match="synthetic meshing failure"):
        triangulate_gmsh(V, S, gm._cell_area(1.0), hole_pts=[[8.5, 8.5]])
    assert not gmsh.isInitialized()
    got = []
    def _probe():
        ok = _GMSH_LOCK.acquire(timeout=5)
        got.append(ok)
        if ok:
            _GMSH_LOCK.release()
    th = threading.Thread(target=_probe)
    th.start(); th.join()
    assert got == [True]
    monkeypatch.undo()
    Vo, To = triangulate_gmsh(V, S, gm._cell_area(1.0), hole_pts=[[8.5, 8.5]])
    assert len(To) > 0


def test_tile_does_not_fall_back_on_a_gmsh_failure(machine, monkeypatch):
    """geo_mesh_halves re-raises a gmsh CDT failure instead of retrying as a
    whole wedge (a different mesh)."""
    import motor_ai_sim.simulation.geo_mesh_gmsh as gg
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise gg.GmshCDTError("synthetic")
    monkeypatch.setattr(gg, "triangulate_gmsh", boom)
    with pytest.raises(gg.GmshCDTError):
        _halves(machine)
    assert len(calls) == 1


def test_missing_gmsh_is_an_actionable_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "gmsh":
            raise ImportError("libGLU.so.1: cannot open shared object file")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RuntimeError, match="pip install gmsh==") as ei:
        gm.cdt_backend()
    assert "MOTOR_AI_SIM_GEO_CDT=triangle" in str(ei.value)


def test_budget_preflight_rejects_before_meshing(monkeypatch):
    """A cross-section whose target sizes alone predict > 2x the budget is
    rejected without calling gmsh at all."""
    import gmsh
    V, S = _square_pslg()
    called = []
    monkeypatch.setattr(gmsh.model.mesh, "generate", lambda *a: called.append(1))
    with pytest.raises(gm.MeshBudgetExceeded, match="before meshing"):
        triangulate_gmsh(V, S, gm._cell_area(0.05), budget=1000)
    assert not called


# ── cross-version compatibility: semantic, not bitwise ──────────────────────
_FP = _ROOT / "tests" / "data" / "geo_mesh_gmsh_fingerprint_40mm.json"


def _fingerprint(full, machine):
    p, _ = machine
    out = {}
    for nm, (V, T, t), ring in (("stator", full[0], float(p["stator_inner_radius"])),
                                ("rotor", full[1], float(p["rotor_outer_radius"]))):
        a = np.abs(_areas(V, T))
        ar, _ = gm._aspect_arr(V, T)
        used = np.unique(T)
        rr = np.hypot(V[used, 0], V[used, 1])
        out[nm] = {"n_tri": int(len(T)),
                   "ring_nodes": int(np.sum(np.abs(rr - ring) < 2e-3)),
                   "area_by_tag": {str(int(k)): float(a[t == k].sum())
                                   for k in np.unique(t)},
                   "ar_p99": float(np.percentile(ar, 99)), "ar_max": float(ar.max())}
    return out


def test_semantic_fingerprint(machine, full):
    """What must hold on ANY supported gmsh build (the pinned 4.15.2 recorded
    it): triangle counts within 5 %, gap-ring node counts exact, every tag's
    area within 0.1 %, quality no worse than 1.3x the reference."""
    got = _fingerprint(full, machine)
    if os.environ.get("GEO_MESH_WRITE_FINGERPRINT") == "1":
        import gmsh
        _FP.parent.mkdir(parents=True, exist_ok=True)
        _FP.write_text(json.dumps({"gmsh": gmsh.__version__, "halves": got},
                                  indent=1), encoding="utf-8")
    ref = json.loads(_FP.read_text(encoding="utf-8"))["halves"]
    for nm in ("stator", "rotor"):
        g, r = got[nm], ref[nm]
        assert g["n_tri"] == pytest.approx(r["n_tri"], rel=0.05)
        assert g["ring_nodes"] == r["ring_nodes"]
        assert set(g["area_by_tag"]) == set(r["area_by_tag"])
        for k, v in r["area_by_tag"].items():
            if k in ("0", "8"):          # air / outer air share a centroid split
                continue
            assert g["area_by_tag"][k] == pytest.approx(v, rel=1e-3, abs=1e-6)
        assert g["ar_p99"] <= 1.3 * r["ar_p99"]
        assert g["ar_max"] <= 1.3 * r["ar_max"]


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
