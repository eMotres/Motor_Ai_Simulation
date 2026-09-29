"""Shaft skin layer: the skin-depth-driven structured wall mesh.

docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md.  A 42CrMo4 shaft carries
its eddy current in δ = sqrt(2/(ωμσ)) ≈ 0.05-0.3 mm under its OD; the CDT
meshed the wall with 2-3 mm cells (1-2 across a 5 mm wall), which reads the
loss low.  `conductor_skin.shaft_skin_spec` sizes a layered wall on δ and
`geo_mesh` builds it as a structured patch stitched into the CDT.

Asserted here:
  * the rule's arithmetic (δ, μ_r,max of a B-H curve, the reference frequency,
    the spec) — against closed forms, not restatements;
  * the 1-D P2 accuracy the rule's h1 = δ, growth 1.5 rests on (≤ 0.5 %);
  * the layer radii (first layer h1, geometric growth, capped, no sliver);
  * the built mesh on two real rotors, full ring and half-model sector:
    the shaft region is still the CAD tube, the first layer is h1 thick, the
    mesh is conforming (no hanging node at the stitched arcs: every edge used
    by ONE triangle lies on the rotor OD or on a sector cut ray), the cut rays
    carry identical radii on both sides (anti-periodic pairing), and nothing
    outside the shaft changes but the iron cells next to the finer OD ring.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation import conductor_skin as cs
from motor_ai_sim.simulation.geo_mesh import (geo_mesh_halves,
                                              skin_layer_radii, _skin_patch)
from motor_ai_sim.simulation.sb_domains import DOM_SHAFT

MU0 = 4e-7 * math.pi
_ROOT = Path(__file__).resolve().parents[1]
_PRESETS = json.loads((_ROOT / "config" / "motor_presets.json")
                      .read_text(encoding="utf-8"))
G150 = {
    "stator_diameter": 150.0, "slot_height": 14.0, "core_thickness": 4.2,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.5, "tooth_width": 9.2, "tooth2_width": 5.5, "cut_width": 6.0,
    "insulation_thickness": 0.15, "wire_width": 5.0, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 14,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 16.0,
    "rotor_house_height": 1.2, "shaft_height": 3.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 2.5, "magnet_up_gap": 2.0,
    "rotor_hole": 0.6, "magnet_down_height": 1.8, "magnet_lamination": 0,
    "stator_fillet_r": 3.5, "stator_fillet_r1": 1.2, "rotor_fill_r": 0.2,
    "motor_length": 35.0,
}
G40 = _PRESETS["my_40mm_last"]["geometry"]


# ── the rule ─────────────────────────────────────────────────────────────────
def test_skin_depth_closed_form():
    # copper at 50 Hz: 9.2 mm (textbook); steel sigma 4.4e6, mu_r 1000, 2840 Hz
    assert cs.skin_depth_m(50.0, 5.8e7, 1.0) == pytest.approx(9.35e-3, rel=5e-3)
    d = cs.skin_depth_m(2840.0, 4.4e6, 1000.0)
    assert d == pytest.approx(math.sqrt(2.0 / (2 * math.pi * 2840 * MU0 * 1000 * 4.4e6)))
    assert cs.skin_depth_m(0.0, 4.4e6, 1000.0) == math.inf
    assert cs.skin_depth_m(100.0, 0.0, 1.0) == math.inf


def test_mu_r_max_is_the_largest_secant_permeability():
    bh = [[0, 0.0], [200, 0.25], [1000, 1.0], [5000, 1.5], [50000, 2.0]]
    want = max(0.25 / (MU0 * 200), 1.0 / (MU0 * 1000), 1.5 / (MU0 * 5000),
               2.0 / (MU0 * 50000))
    assert cs.bh_mu_r_max(bh) == pytest.approx(want)
    assert cs.bh_mu_r_max(None) == 1.0
    assert cs.bh_mu_r_max([]) == 1.0


def test_reference_frequency_is_slot_passing_or_the_carrier():
    assert cs.rotor_frame_ref_hz(12, 14200.0) == pytest.approx(12 * 14200 / 60)
    assert cs.rotor_frame_ref_hz(12, 14200.0, 24000.0) == pytest.approx(24000.0)
    assert cs.rotor_frame_ref_hz(24, 1000.0, 5.0) == pytest.approx(400.0)


def test_spec_values(monkeypatch):
    for k in ("SB_SKIN_H1_FRAC", "SB_SKIN_GROWTH", "SB_SKIN_CELLS_PER_WL",
              "SB_SKIN_CHORD_MM"):
        monkeypatch.delenv(k, raising=False)
    sp = cs.shaft_skin_spec(4.4e6, 1000.0, 2840.0, 25.6, 12, 5)
    d_mm = cs.skin_depth_m(2840.0, 4.4e6, 1000.0) * 1e3
    assert sp["delta_mm"] == pytest.approx(d_mm)
    assert sp["h1_mm"] == pytest.approx(cs.SKIN_H1_FRAC * d_mm)
    lam = 2 * math.pi * 25.6 / 17
    assert sp["chord_mm"] == pytest.approx(lam / cs.SKIN_CELLS_PER_WAVELENGTH)
    assert sp["h_max_mm"] == pytest.approx(cs.SKIN_HMAX_CHORDS * sp["chord_mm"])
    assert sp["growth"] == cs.SKIN_GROWTH
    # nothing to resolve: no conductivity, no speed, no shaft
    assert cs.shaft_skin_spec(0.0, 1000.0, 2840.0, 25.6, 12, 5) is None
    assert cs.shaft_skin_spec(4.4e6, 1000.0, 0.0, 25.6, 12, 5) is None
    assert cs.shaft_skin_spec(4.4e6, 1000.0, 2840.0, 0.0, 12, 5) is None
    # the first layer never exceeds the chord (low-frequency / non-magnetic)
    sp2 = cs.shaft_skin_spec(3.5e7, 1.0, 10.0, 5.0, 12, 7)
    assert sp2["h1_mm"] <= sp2["chord_mm"] + 1e-12


# ── why h1 = δ, growth 1.5 is enough: the 1-D P2 skin loss ──────────────────
def _p2_skin_loss(x, mu, sig, w):
    """P2 FE of -(1/μ)A'' + jωσA = 0, (1/μ)A'(0) = -1, A(x_end) = 0; returns
    the loss per unit area ½∫σω²|A|²."""
    ne = len(x) - 1
    N = 2 * ne + 1
    K = np.zeros((N, N), complex)
    gp, gw = np.polynomial.legendre.leggauss(5)
    gp = 0.5 * (gp + 1.0); gw = 0.5 * gw
    mloc = []
    for e in range(ne):
        h = x[e + 1] - x[e]
        ids = [2 * e, 2 * e + 1, 2 * e + 2]
        ke = np.zeros((3, 3)); me = np.zeros((3, 3))
        for s, ww in zip(gp, gw):
            n_ = np.array([2 * (s - .5) * (s - 1), -4 * s * (s - 1), 2 * s * (s - .5)])
            dn = np.array([4 * s - 3, -8 * s + 4, 4 * s - 1]) / h
            ke += ww * h * np.outer(dn, dn) / mu
            me += ww * h * np.outer(n_, n_)
        K[np.ix_(ids, ids)] += ke + 1j * w * sig * me
        mloc.append((ids, me))
    f = np.zeros(N, complex); f[0] = 1.0
    K[-1, :] = 0.0; K[-1, -1] = 1.0; f[-1] = 0.0
    A = np.linalg.solve(K, f)
    return sum(0.5 * sig * w * w * float(np.real(np.conj(A[i]) @ m @ A[i]))
               for i, m in mloc)


@pytest.mark.parametrize("f_hz", [947.0, 2840.0])
def test_one_skin_depth_first_layer_resolves_the_loss(f_hz):
    sig, mur = 4.4e6, 1000.0
    mu = MU0 * mur
    w = 2 * math.pi * f_hz
    d_ref = cs.skin_depth_m(2840.0, sig, mur)          # the rule's reference δ
    d = cs.skin_depth_m(f_hz, sig, mur)
    exact = 1.0 / (2.0 * sig * d)
    r = skin_layer_radii(5e-3, 0.0, d_ref, cs.SKIN_GROWTH, 5e-3)
    x = (5e-3 - r)                                      # depth from the surface
    assert abs(_p2_skin_loss(x, mu, sig, w) / exact - 1.0) < 5e-3
    # ... and the CDT's old wall (two 2.5 mm cells) is far off
    coarse = _p2_skin_loss(np.array([0.0, 2.5e-3, 5e-3]), mu, sig, w)
    assert coarse / exact < 0.85


# ── the layer radii ──────────────────────────────────────────────────────────
def test_layer_radii_grow_geometrically_to_the_cap():
    r = skin_layer_radii(25.0, 20.0, 0.1, 1.5, 0.6)
    t = -np.diff(r)
    assert r[0] == 25.0 and r[-1] == pytest.approx(20.0)
    assert np.all(t > 0)
    assert t[0] == pytest.approx(0.1)
    assert t[1] == pytest.approx(0.15) and t[2] == pytest.approx(0.225)
    assert t.max() <= 0.6 * 1.5 + 1e-9         # last layer may absorb a remainder
    assert t[-1] >= 0.5 * t[-2] - 1e-12         # never a sliver layer
    assert skin_layer_radii(25.0, 25.0, 0.1, 1.5, 0.6).size == 1


def test_patch_sector_is_conforming_and_clones_the_rays():
    span = 2 * math.pi / 10
    radii = skin_layer_radii(25.6, 20.6, 0.14, 1.5, 0.59)
    P = _skin_patch(radii, 27, span, False)
    V, T = P["V"], P["T"]
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    a = 0.5 * ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
               - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))
    assert np.all(a > 0)                                 # CCW, non-degenerate
    # polygonal annular sector: sum of the trapezoid chords
    want = 0.5 * 27 * math.sin(span / 27) * (25.6 ** 2 - 20.6 ** 2)
    assert a.sum() == pytest.approx(want, rel=1e-9)
    r0 = np.hypot(*V[np.abs(V[:, 1]) < 1e-12].T)
    ang = np.arctan2(V[:, 1], V[:, 0])
    rS = np.hypot(*V[np.abs(ang - span) < 1e-12].T)
    assert np.allclose(np.sort(r0), np.sort(rS), atol=1e-12)


# ── the built mesh on real rotors ────────────────────────────────────────────
SPEC = {"shaft": {"h1_mm": 0.05, "growth": 1.5, "chord_mm": 0.4,
                  "h_max_mm": 0.4}}


def _build(geo, n_sectors, skin):
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    p = motor.parameters
    polys = motor.get_2d_polygons(rotor_angle_deg=0.0)
    _ms, _ts, _cs, mesh_r, tags_r, _cr = geo_mesh_halves(
        p, polys, r_si=float(p["stator_inner_radius"]),
        r_ro=float(p["rotor_outer_radius"]), mesh_edge_mm=4.0, n_slip=1008,
        n_sectors=n_sectors, skin_layers=skin)
    return p, polys, mesh_r.p.T * 1e3, mesh_r.t.T, np.asarray(tags_r)


@pytest.fixture(scope="module",
                params=[("150", 1), ("150", 2), ("40", 1), ("40", 2)],
                ids=["150-full", "150-half", "40-full", "40-half"])
def built(request):
    geo = G150 if request.param[0] == "150" else G40
    ns = request.param[1]
    return ns, _build(geo, ns, SPEC), _build(geo, ns, None)


def _areas(V, T):
    p0, p1, p2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return 0.5 * np.abs((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1])
                        - (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))


def test_shaft_region_is_still_the_cad_tube(built):
    ns, (p, polys, V, T, tags), _old = built
    meshed = float(_areas(V, T)[tags == DOM_SHAFT].sum())
    cad = float(polys["shaft"].area) / ns
    assert meshed == pytest.approx(cad, rel=5e-3)


def test_first_layer_is_h1_thick(built):
    ns, (p, polys, V, T, tags), _old = built
    r_sh = float(p["rotor_inner_radius"])
    nodes = np.unique(T[tags == DOM_SHAFT])
    rr = np.unique(np.round(np.hypot(V[nodes, 0], V[nodes, 1]), 5))
    below = rr[rr < r_sh - 0.01]          # the OD ring sits within 1 µm snap
    assert below.max() == pytest.approx(r_sh - 0.05, abs=2e-3)


def _unmatched_off_rays(V, T, ns, r_lo, r_hi):
    """Edges used by ONE triangle, with both nodes in r_lo..r_hi, that do not
    lie on a sector cut ray."""
    E = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [0, 2]]]), axis=1)
    u, cnt = np.unique(E, axis=0, return_counts=True)
    assert cnt.max() <= 2
    bnd = u[cnt == 1]
    r = np.hypot(V[:, 0], V[:, 1])
    ang = np.arctan2(V[:, 1], V[:, 0])
    inz = ((r[bnd[:, 0]] > r_lo) & (r[bnd[:, 0]] < r_hi)
           & (r[bnd[:, 1]] > r_lo) & (r[bnd[:, 1]] < r_hi))
    ok = np.zeros(len(bnd), bool)
    if ns > 1:
        span = 2 * math.pi / ns

        def on_ray(i, th):
            d = np.abs(np.arctan2(np.sin(ang[i] - th), np.cos(ang[i] - th)))
            return d * r[i] < 1e-3
        ok = (on_ray(bnd[:, 0], 0.0) & on_ray(bnd[:, 1], 0.0)) \
            | (on_ray(bnd[:, 0], span) & on_ray(bnd[:, 1], span))
    return int((inz & ~ok).sum())


def test_mesh_is_conforming_after_the_stitch(built):
    """No hanging node at the stitched arcs: inside and around the shaft wall
    every edge is shared by two triangles, except on the sector cut rays."""
    ns, (p, polys, V, T, tags), _old = built
    r_sh = float(p["rotor_inner_radius"])
    r_b = float(p["shaft_inner_radius"])
    assert _unmatched_off_rays(V, T, ns, 0.5 * r_b, r_sh + 0.5 * (r_sh - r_b)) == 0


def test_cut_rays_carry_identical_radii(built):
    ns, (p, polys, V, T, tags), _old = built
    if ns == 1:
        pytest.skip("full ring: no cut")
    span = 2 * math.pi / ns
    r = np.hypot(V[:, 0], V[:, 1])
    ang = np.arctan2(V[:, 1], V[:, 0])
    used = np.unique(T)
    d0 = np.abs(np.arctan2(np.sin(ang - 0.0), np.cos(ang - 0.0))) * r
    dS = np.abs(np.arctan2(np.sin(ang - span), np.cos(ang - span))) * r
    r_sh = float(p["rotor_inner_radius"])
    sel = lambda d: np.sort(r[np.intersect1d(np.where((d < 1e-6) & (r > 1e-6)
                                                      & (r <= r_sh + 1e-6))[0], used)])
    a, b = sel(d0), sel(dS)
    assert a.size == b.size and a.size > 5
    assert np.allclose(a, b, atol=1e-9)


def test_every_other_region_keeps_its_section(built):
    """Every region but the shaft keeps its area (the bore and the iron next to
    the finer shaft ring move by the polygon-vs-circle chord only).  Triangle
    re-plans the whole CDT when one ring changes, so element COUNTS elsewhere
    may move by a few per cent — the section may not."""
    ns, (p, polys, V, T, tags), (_p0, _pl0, V0, T0, tags0) = built
    a, a0 = _areas(V, T), _areas(V0, T0)
    for tg in np.unique(tags0):
        if int(tg) == int(DOM_SHAFT):
            continue
        assert float(a[tags == tg].sum()) == pytest.approx(
            float(a0[tags0 == tg].sum()), rel=1e-2, abs=1e-6)


# ── scale check: the same physics gets the same resolution on any machine ──
def _resolution(geo, rpm):
    """(first layer / δ, node rings within 3 δ, OD cells per field wavelength)
    of the mesh the solver's rule builds on this rotor."""
    motor = CadQueryMotor()
    motor.set_parameters(dict(geo))
    p = motor.parameters
    ns_, np_ = int(p["num_slots"]), int(p["num_poles"]) // 2
    r_sh = float(p["rotor_inner_radius"])
    sp = cs.shaft_skin_spec(4.4e6, 1000.0, cs.rotor_frame_ref_hz(ns_, rpm),
                            r_sh, ns_, np_)
    _p, _pl, V, T, tags = _build(geo, 1, {"shaft": sp})
    d = sp["delta_mm"]
    nodes = np.unique(T[tags == DOM_SHAFT])
    # rings, not nodes: the OD ring sits within the 1 µm snap of r_shaft
    rr = np.sort(np.hypot(V[nodes, 0], V[nodes, 1]))[::-1]
    rr = rr[np.concatenate([[True], -np.diff(rr) > 0.01])]
    od = rr[0]
    first = od - rr[rr < od - 1e-3].max()
    n3 = int(np.sum(rr > od - 3.0 * d)) - 1
    on_od = np.abs(np.hypot(V[:, 0], V[:, 1]) - r_sh) < 2e-3
    n_od = int(on_od.sum())
    lam = 2 * math.pi * r_sh / (ns_ + np_)
    return first / d, n3, n_od * lam / (2 * math.pi * r_sh)


def test_same_rule_same_resolution_on_every_scale(monkeypatch):
    """Owner 2026-09-25: «все физические законы должны работать одинаково на
    любых масштабах».  The Ø150 and the Ø40 rotors, at speeds that put their
    skin depths 3x apart, get at least one layer per δ at the surface (exactly
    one unless the chord caps it finer), at least two layers inside 3 δ and
    the same cells per field wavelength."""
    for k in ("SB_SKIN_H1_FRAC", "SB_SKIN_GROWTH", "SB_SKIN_CELLS_PER_WL",
              "SB_SKIN_CHORD_MM", "SB_SKIN_HMAX_CHORDS"):
        monkeypatch.delenv(k, raising=False)
    a = _resolution(G150, 1500.0)
    b = _resolution(G40, 13000.0)
    # first layer = h1_frac·δ, or finer where the chord caps it (aspect ≤ 1)
    assert a[0] <= cs.SKIN_H1_FRAC * 1.02 and b[0] <= cs.SKIN_H1_FRAC * 1.02
    assert min(a[0], b[0]) > 0.25
    assert a[1] >= 2 and b[1] >= 2
    assert a[2] == pytest.approx(cs.SKIN_CELLS_PER_WAVELENGTH, rel=0.1)
    assert b[2] == pytest.approx(cs.SKIN_CELLS_PER_WAVELENGTH, rel=0.1)


def test_iron_grading_points_stay_in_iron_and_off_the_rays():
    """The iron transition from the skin patch: rings at r0 + h0·g^k with a
    chord about equal to their step, half a step inside the iron, half a step
    off the cut rays of a sector cell, and none once the step reaches the
    iron's own cell size."""
    from shapely.geometry import Point
    from motor_ai_sim.simulation.geo_mesh import _iron_grade_points
    ring = Point(0, 0).buffer(20.0, 512).difference(Point(0, 0).buffer(10.0, 512))
    span = 2 * math.pi / 10
    P = _iron_grade_points(ring, 10.0, 0.1, 1.5, 1.0, span)
    r = np.hypot(P[:, 0], P[:, 1])
    th = np.arctan2(P[:, 1], P[:, 0])
    radii = np.unique(np.round(r, 6))
    steps = np.diff(np.concatenate([[10.0], radii]))
    assert steps[0] == pytest.approx(0.1) and steps[1] == pytest.approx(0.15)
    assert steps.max() < 1.0
    assert np.all(r * th > 0.049) and np.all(r * (span - th) > 0.049)
    for rr, h in zip(radii, steps):
        n = int(np.sum(np.isclose(r, rr, atol=1e-6)))
        assert span * rr / n == pytest.approx(h, rel=0.35)
    assert _iron_grade_points(ring, 10.0, 1.0, 1.5, 1.0, span).size == 0
