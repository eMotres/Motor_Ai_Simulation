# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Seawater through the stator OD notches under the sleeve
(``stator_od_cooling='sleeve_channels'``, 2026-11-04).

Three layers, none of which writes to ``config/``:

* the notch measured on the stator polygon and the film correlations, against
  numbers worked by hand in the comments;
* the conduction solve on a hand-built notched ring (no mesher, the expected
  answers are hand numbers): the facet split, the heat balance, the two limits;
* the product's own route function on a mesh from the product's own builder, with
  injected losses: the payload, the mesh independence of the wetted areas.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

yaml = pytest.importorskip("yaml")
shapely = pytest.importorskip("shapely")

from motor_ai_sim import coupled_continuous_rating as ccr  # noqa: E402
from motor_ai_sim import thermal_settings as ts  # noqa: E402
from motor_ai_sim.simulation import cooling_models as cm  # noqa: E402
from motor_ai_sim.simulation.thermal_solver_2d import solve_steady_thermal  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: A Ø200 24-slot 28-pole lamination, 220 mm stack: a fixture, not a catalog die.
GEO_200 = {
    "stator_diameter": 200.0, "slot_height": 20.4, "core_thickness": 5.6,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.65, "tooth_width": 11.3, "tooth2_width": 5.9, "cut_width": 4.7,
    "insulation_thickness": 0.2, "wire_width": 7.0, "wire_height": 0.8,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 16,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 18.7,
    "rotor_house_height": 1.7, "shaft_height": 3.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 4.0, "magnet_up_gap": 1.6,
    "rotor_hole": 0.84, "magnet_down_height": 3.4, "magnet_lamination": 0,
    "stator_fillet_r": 3.9, "stator_fillet_r1": 0.4, "rotor_fill_r": 1.3,
    "motor_length": 220.0,
}
STACK_M = GEO_200["motor_length"] * 1e-3


# ── the measured notch ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def notches():
    from motor_ai_sim.cadquery_geometry import stator_od_notches
    n = stator_od_notches(dict(GEO_200))
    assert n and n["notched"]
    return n


def test_the_notch_measured_on_the_polygon(notches):
    """Against the lamination's own area: smooth minus notched, 2184.51 mm² for
    the WHOLE ring (10422.56 - 8238.04 from the builder), so the area is not per
    sector; twelve notches, one per two slots."""
    assert notches["n_notches"] == 12
    assert notches["area_total_mm2"] == pytest.approx(2184.51, abs=0.5)
    assert notches["area_per_notch_mm2"] == pytest.approx(182.04, abs=0.05)
    assert notches["depth_mm"] == pytest.approx(21.3, abs=0.05)
    # the opening: 12 of them + the contact arcs close the OD circle
    from motor_ai_sim.cadquery_geometry import stator_od_contact
    c = stator_od_contact(dict(GEO_200))
    assert 12 * notches["sleeve_width_per_notch_mm"] + c["contact_arc_mm"] == pytest.approx(
        2 * math.pi * 100.0, rel=2e-4)
    # D_h = 4 A / (iron + sleeve side)
    assert notches["hydraulic_diameter_mm"] == pytest.approx(
        4 * notches["area_per_notch_mm2"]
        / (notches["iron_perimeter_per_notch_mm"] + notches["sleeve_width_per_notch_mm"]))
    assert notches["hydraulic_diameter_mm"] == pytest.approx(10.306, abs=0.01)


def test_a_smooth_od_has_no_notches_and_a_stator_in_pieces_is_not_measured():
    from motor_ai_sim.cadquery_geometry import measure_od_notches, stator_od_notches
    off = stator_od_notches({**GEO_200, "stator_outer_notches": False})
    assert off["notched"] is False and off["n_notches"] == 0
    two = shapely.MultiPolygon([shapely.Point(0, 0).buffer(10), shapely.Point(50, 0).buffer(10)])
    assert measure_od_notches(two, 10.0) is None


# ── the correlations, against the hand ──────────────────────────────────────

def _path(notches, flow):
    return cm.od_channel_path(
        n_notches=notches["n_notches"],
        area_per_notch_m2=notches["area_per_notch_mm2"] * 1e-6,
        iron_perimeter_per_notch_m=notches["iron_perimeter_per_notch_mm"] * 1e-3,
        sleeve_width_per_notch_m=notches["sleeve_width_per_notch_mm"] * 1e-3,
        stack_m=STACK_M, flow_lpm=flow)


def test_seawater_properties_are_the_stated_ones():
    p = cm.SEAWATER_35C
    assert (p.rho, p.cp, p.k, p.nu) == (1025.0, 3990.0, 0.60, 0.8e-6)
    assert p.pr == pytest.approx(5.453, abs=0.001)


def test_10_lpm_is_developing_laminar(notches):
    """V = 1.667e-4 / 2184.5e-6 = 0.0763 m/s; Re = 0.0763*0.010306/0.8e-6 = 983;
    Gz = 983*5.453*0.010306/0.22 = 251; Nu = 1.86*251^(1/3) = 11.73;
    h = 11.73*0.6/0.010306 = 683 W/m²K."""
    c = _path(notches, 10.0)
    assert c["velocity_mps"] == pytest.approx(0.0763, abs=1e-4)
    assert c["re"] == pytest.approx(983, abs=2)
    assert c["nu"] == pytest.approx(11.73, abs=0.02)
    assert c["h_W_per_m2K"] == pytest.approx(683, abs=2)
    assert "laminar" in c["regime"]


def test_50_lpm_is_turbulent(notches):
    """Re = 4914; f = (0.79 ln Re - 1.64)^-2 = 0.03883;
    Nu_fd = (f/8)(Re-1000)Pr / (1 + 12.7 sqrt(f/8)(Pr^(2/3)-1)) = 36.27;
    entrance factor 1 + (0.010306/0.22)^(2/3) = 1.130; Nu = 40.98; h = 2386."""
    c = _path(notches, 50.0)
    assert c["re"] == pytest.approx(4914, abs=3)
    assert c["nu"] == pytest.approx(40.98, abs=0.05)
    assert c["h_W_per_m2K"] == pytest.approx(2386, abs=5)
    assert "turbulent" in c["regime"]


def test_energy_and_drag_numbers(notches):
    c = _path(notches, 10.0)
    # m cp = 1.6667e-4 * 1025 * 3990 = 681.6 W/K, and the mean sits on 2 m cp
    assert c["m_cp_W_per_K"] == pytest.approx(681.6, abs=0.2)
    assert c["G_bulk_W_per_K"] == pytest.approx(2 * c["m_cp_W_per_K"])
    # rho Q V = 1025 * 1.6667e-4 * 10 = 1.708 N; 50 l/min five times that
    assert c["intake_drag_N"] == pytest.approx(1.708, abs=0.001)
    assert _path(notches, 50.0)["intake_drag_N"] == pytest.approx(8.542, abs=0.001)


def test_the_film_is_continuous_and_rising_across_the_transition():
    pr, dl = cm.SEAWATER_35C.pr, 0.05
    res = np.linspace(100.0, 8000.0, 400)
    nu = np.array([cm.od_channel_nusselt(r, pr, dl)[0] for r in res])
    assert (np.diff(nu) > 0).all()
    assert np.abs(np.diff(nu)).max() < 0.5
    assert cm.od_channel_nusselt(1e-3, pr, dl)[0] == pytest.approx(3.66)   # the floor
    assert "transitional" in cm.od_channel_nusselt(3000.0, pr, dl)[1]


# ── the solve on a hand-built notched ring ─────────────────────────────────

R_I, R_O, R_ROOT = 0.070, 0.100, 0.085
N_NOTCH = 12
HALF = math.radians(6.0)
K_IRON = 25.0
L_RING = 0.22


def _ring(n_r=12, n_per_sector=20):
    """A polar grid over the ring, with the cells of the N notches removed: every
    OD node exactly on the circle, the walls radial, the root an arc.  A sector of
    30 deg per notch, n_per_sector cells, the opening centred on each sector's
    start.  Returns (points 2xN, triangles 3xM)."""
    n_t = N_NOTCH * n_per_sector
    rs = np.linspace(R_I, R_O, n_r + 1)
    root_j = int(np.argmin(np.abs(rs - R_ROOT)))
    ths = 2 * np.pi * np.arange(n_t) / n_t
    # snap the opening to the grid: half-width in cells
    half_cells = max(int(round(HALF / (2 * np.pi / n_t))), 1)
    idx = -np.ones((n_r + 1, n_t), int)
    pts = []
    for j, r in enumerate(rs):
        for i, th in enumerate(ths):
            idx[j, i] = len(pts)
            pts.append((r * math.cos(th), r * math.sin(th)))
    tris = []
    for j in range(n_r):
        for i in range(n_t):
            ii = (i + 1) % n_t
            in_open = min(i % n_per_sector, n_per_sector - 1 - i % n_per_sector
                          ) < half_cells and (i % n_per_sector < half_cells
                                              or i % n_per_sector >= n_per_sector - half_cells)
            if j >= root_j and in_open:
                continue
            tris.append((idx[j, i], idx[j, ii], idx[j + 1, ii]))
            tris.append((idx[j, i], idx[j + 1, ii], idx[j + 1, i]))
    half_real = half_cells * (2 * np.pi / n_t)
    spans = [(2 * np.pi * k / N_NOTCH, half_real) for k in range(N_NOTCH)]
    return np.array(pts).T, np.array(tris).T, spans, half_real, float(rs[root_j])


def _solve_ring(ring, *, q_total_w=1500.0, h_ch=None, g_bulk=None, g_arc=80.0,
                t_in=35.0, facets_arc="od_contour_arcs"):
    P, T, spans, _h, _r = ring
    a = 0.5 * np.abs((P[0, T[1]] - P[0, T[0]]) * (P[1, T[2]] - P[1, T[0]])
                     - (P[0, T[2]] - P[0, T[0]]) * (P[1, T[1]] - P[1, T[0]]))
    q = np.full(T.shape[1], q_total_w / (float(a.sum()) * L_RING))
    surf = [{"name": "outer", "facets": facets_arc, "G_W_per_K": g_arc,
             "symmetry_mult": 1, "t_sink": t_in}]
    nodes = []
    if h_ch is not None:
        surf.append({"name": "od_channels", "facets": "od_contour_notches",
                     "h": h_ch, "t_sink": t_in, "node": "water"})
        nodes.append({"name": "water", "G_W_per_K": g_bulk, "t_sink_c": t_in,
                      "symmetry_mult": 1})
    return solve_steady_thermal(
        P, T, np.ones(T.shape[1], int), np.full(T.shape[1], K_IRON), q,
        drop_tags=[], r_housing_m=R_O, rotor_outer_m=R_I / 2, stator_inner_m=R_I,
        gap_k=0.03, length_m=L_RING, surfaces=surf, lumped_nodes=nodes,
        od_contour={"opening_spans_rad": spans,
                    "arc_r_min_m": 0.5 * (R_O + R_ROOT)})


def _by_name(th):
    return {s["name"]: s for s in th["surfaces"]}


@pytest.fixture(scope="module")
def ring():
    return _ring()


def test_the_split_partitions_the_outer_contour_exactly(ring):
    _P, _T, _sp, half, r_root = ring
    th = _solve_ring(ring, h_ch=500.0, g_bulk=1400.0)
    s = _by_name(th)
    arc_a = (2 * math.pi - N_NOTCH * 2 * half) * R_O * L_RING
    # the arcs are chords of the polygon; the walls radial, the root an arc
    assert s["outer"]["area_m2"] == pytest.approx(arc_a, rel=2e-3)
    wall = 2 * (R_O - r_root) + 2 * half * r_root
    assert s["od_channels"]["area_m2"] == pytest.approx(N_NOTCH * wall * L_RING, rel=2e-3)
    # and the bore stays out of both: it is another loop


def test_heat_balance_closes_and_the_water_is_the_energy_balance(ring):
    th = _solve_ring(ring, h_ch=683.0, g_bulk=1363.0)
    s = _by_name(th)
    q_ch, q_arc = s["od_channels"]["heat_removed_W"], s["outer"]["heat_removed_W"]
    assert q_ch + q_arc == pytest.approx(th["q_generated_W"], rel=1e-9)
    assert abs(th["heat_residual_W"]) < 1e-6
    # mean water = T_in + Q/(2 m cp): the node is the film's own sink
    t_bulk = s["od_channels"]["t_sink_c"]
    assert t_bulk == pytest.approx(35.0 + q_ch / 1363.0, abs=1e-6)
    nd = th["nodes"][0]
    assert nd["heat_removed_W"] == pytest.approx(q_ch, rel=1e-9)
    # the film's identity on its own facets
    assert q_ch == pytest.approx(
        683.0 * s["od_channels"]["area_m2"] * (s["od_channels"]["t_mean_c"] - t_bulk), rel=1e-9)
    # both paths really carry heat, and the water is colder than the wall
    assert q_ch > 0.0 and q_arc > 0.0 and s["od_channels"]["t_mean_c"] > t_bulk


def test_flow_to_zero_approaches_the_sleeve_alone(ring):
    """A vanishing stream carries nothing out, and the map is the arcs' map.  One
    thing remains at the laminar floor film: the one water node still joins the
    hot root to the cooler upper wall, a net-zero bridge worth a fraction of a
    kelvin here (still water in a closed notch does stir, so its sign is right).
    With the film gone too the two solves are the same solve."""
    alone = _solve_ring(ring)
    rise = alone["T_max"] - 35.0
    th = _solve_ring(ring, h_ch=213.0, g_bulk=1e-6)
    s = _by_name(th)
    assert s["od_channels"]["heat_removed_W"] < 1e-3
    assert s["od_channels"]["t_sink_c"] == pytest.approx(s["od_channels"]["t_mean_c"], abs=1e-3)
    assert s["outer"]["heat_removed_W"] == pytest.approx(
        _by_name(alone)["outer"]["heat_removed_W"], rel=1e-5)
    assert 0.0 <= alone["T_max"] - th["T_max"] < 0.02 * rise
    no_film = _solve_ring(ring, h_ch=1e-9, g_bulk=1e-6)
    assert no_film["T_max"] == pytest.approx(alone["T_max"], abs=1e-4)


def test_more_flow_cools_more_and_the_sleeve_alone_is_the_hottest(ring):
    alone = _solve_ring(ring)["T_max"]
    t = [_solve_ring(ring, h_ch=h, g_bulk=g)["T_max"]
         for h, g in ((213.0, 13.6), (683.0, 1363.0), (2386.0, 6816.0))]
    assert alone > t[0] > t[1] > t[2] > 35.0


def test_a_high_film_pins_the_notch_wall_on_the_water(ring):
    """h -> large: the wall reaches the water, and the rise is the energy balance
    of nearly all the heat.  The iron's own resistance is what is left; it does
    not go to zero, so T_max stays above the water by a conduction drop."""
    th = _solve_ring(ring, h_ch=1e7, g_bulk=6816.0)
    s = _by_name(th)
    assert s["od_channels"]["t_mean_c"] - s["od_channels"]["t_sink_c"] < 0.01
    assert s["od_channels"]["heat_removed_W"] > s["outer"]["heat_removed_W"]
    assert abs(th["heat_residual_W"]) < 1e-6
    # bracket: the wedge of iron above the root is short; a hand conduction drop
    # q''·t²/(2 k) over the 15 mm of iron under the roots is about 1 K here
    assert 35.0 < th["T_max"] < 45.0


@pytest.mark.parametrize("n_r,n_s", [(6, 10), (24, 40)])
def test_the_wetted_areas_do_not_follow_the_mesh(n_r, n_s):
    """Long wall facets on the coarse grid stay in: the root sits under 0.9 R."""
    ring = _ring(n_r, n_s)
    _P, _T, _sp, half, r_root = ring
    s = _by_name(_solve_ring(ring, h_ch=500.0, g_bulk=1400.0))
    wall = 2 * (R_O - r_root) + 2 * half * r_root
    assert s["od_channels"]["area_m2"] == pytest.approx(N_NOTCH * wall * L_RING, rel=2e-2)


def test_without_od_contour_the_solver_has_no_such_facets():
    ring = _ring(6, 10)
    P, T, _s, _h, _r = ring
    q = np.full(T.shape[1], 1e5)
    th = solve_steady_thermal(
        P, T, np.ones(T.shape[1], int), np.full(T.shape[1], K_IRON), q, drop_tags=[],
        r_housing_m=R_O, rotor_outer_m=R_I / 2, stator_inner_m=R_I, gap_k=0.03,
        length_m=L_RING, surfaces=[{"name": "outer", "h": 1000.0, "t_sink": 35.0}])
    assert [s["name"] for s in th["surfaces"]] == ["outer"]


def _gmsh_stator(poly, lmax):
    """A mesh of one stator polygon: lmax large = the polygon's own vertices only,
    a straight notch wall one long facet (what a PWM-resolution mesh does)."""
    gmsh = pytest.importorskip("gmsh")
    from skfem import MeshTri
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("s")
    loops = []
    for ring_ in [poly.exterior] + list(poly.interiors):
        cs = list(ring_.coords)[:-1]
        pts = [gmsh.model.geo.addPoint(x, y, 0, lmax) for x, y in cs]
        lines = [gmsh.model.geo.addLine(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
        loops.append(gmsh.model.geo.addCurveLoop(lines))
    gmsh.model.geo.addPlaneSurface(loops)
    gmsh.model.geo.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", lmax)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.model.mesh.generate(2)
    tags, xyz, _ = gmsh.model.mesh.getNodes()
    idx = {int(t): i for i, t in enumerate(tags)}
    P = np.asarray(xyz).reshape(-1, 3)[:, :2].T * 1e-3
    _et, en = gmsh.model.mesh.getElementsByType(2)
    T = np.array([idx[int(i)] for i in en]).reshape(-1, 3).T
    gmsh.finalize()
    return MeshTri(P, T)


def _tracked_geo(name):
    with open(ROOT / "config" / "dies" / name / "die.yaml", encoding="utf-8") as f:
        return dict((yaml.safe_load(f) or {}).get("geometry") or {})


@pytest.mark.parametrize("geo", [GEO_200, _tracked_geo("CIANO14 30_10"),
                                 _tracked_geo("CIANO20 150_35 new")],
                         ids=["d200", "d30", "d150"])
def test_the_split_matches_the_polygon_at_any_facet_length(geo):
    """The real lamination, boundary refined and not: the same two areas, the
    polygon's, with a straight notch wall one long facet on the coarse mesh."""
    import contextlib
    import io
    from motor_ai_sim.cadquery_geometry import (CadQueryMotor, stator_od_contact,
                                                stator_od_notches)
    from motor_ai_sim.simulation.thermal_solver_2d import (od_contour_split,
                                                           outer_contour_facets)
    with contextlib.redirect_stdout(io.StringIO()):
        m = CadQueryMotor()
        m.set_parameters(dict(geo))
        st = m.get_2d_polygons(0.0)["stator"]
    n = stator_od_notches(geo, stator=st)
    c = stator_od_contact(geo, stator=st)
    R = c["od_circumference_mm"] / (2 * math.pi) * 1e-3

    def lens(mesh, sel):
        return float(np.hypot(*(mesh.p[:, mesh.facets[0, sel]]
                                - mesh.p[:, mesh.facets[1, sel]])).sum())

    for lmax in (R * 1e3 / 100.0, R * 1e3):
        mesh = _gmsh_stator(st, lmax)
        bnd = mesh.boundary_facets()
        r = np.hypot(mesh.p[0], mesh.p[1])[mesh.facets[:, bnd]].min(axis=0)
        contour = outer_contour_facets(mesh, bnd, bnd[r >= R * (1 - 2e-4)], np.array([], int))
        arcs, notch = od_contour_split(mesh, contour, n["opening_spans_rad"],
                                       arc_r_min_m=R - 0.5e-3 * n["depth_mm"])
        assert np.intersect1d(arcs, notch).size == 0
        assert lens(mesh, arcs) * 1e3 == pytest.approx(c["contact_arc_mm"], rel=2e-3)
        assert lens(mesh, notch) * 1e3 == pytest.approx(
            n["n_notches"] * n["iron_perimeter_per_notch_mm"], rel=2e-3)


# ── the request: defaults untouched, refusals named ─────────────────────────

BASE = dict(cooling_mode="manual", ambient_temp=35, h_conv=1100, air_speed_mps=0,
            fluid="water", fluid_temp_in_c=25, flow_lpm=0)


def test_the_validator_refuses_what_it_cannot_solve():
    from fastapi import HTTPException
    from motor_ai_sim.routes import thermal as th
    ok = dict(stator_od_cooling="sleeve_channels", od_channel_flow_lpm=10.0)
    assert th._validate_field_params(**BASE, **ok) == ("manual", "none")
    for bad in (dict(stator_od_cooling="sleeve_channels"),
                dict(stator_od_cooling="sleeve_channels", od_channel_flow_lpm=0.0),
                dict(stator_od_cooling="sleeve_channels", od_channel_flow_lpm=-5.0),
                dict(stator_od_cooling="sleeve_channels",
                     od_channel_flow_lpm=float("nan")),
                {**ok, "od_sleeve_wall_mm": 0},
                {**ok, "cooling_mode": "robotics"},
                {**ok, "cooling_mode": "none"}):
        with pytest.raises(HTTPException) as exc:
            th._validate_field_params(**{**BASE, **bad})
        assert exc.value.status_code == 422


def test_a_smooth_stator_is_refused_by_name():
    from fastapi import HTTPException
    from motor_ai_sim.routes import thermal as th
    with pytest.raises(HTTPException) as exc:
        th.stator_od_channel_geometry({**GEO_200, "stator_outer_notches": False})
    assert exc.value.status_code == 422
    assert "stator_outer_notches" in json.dumps(exc.value.detail)


def test_the_settings_mapper_sends_the_flow_only_in_this_mode():
    base = {"coolMode": "manual", "ambientT": "35", "hConv": "1100"}
    ch = ts.cooling_fields({**base, "statorOd": "sleeve_channels",
                            "odChannelFlowLpm": "50"})
    assert ch["stator_od_cooling"] == "sleeve_channels"
    assert (ch["od_sleeve_wall_mm"], ch["od_sleeve_k"], ch["od_channel_flow_lpm"]) == (
        1.5, 160.0, 50.0)
    sl = ts.cooling_fields({**base, "statorOd": "sleeve_contact", "odChannelFlowLpm": "50"})
    assert "od_channel_flow_lpm" not in sl and sl["stator_od_cooling"] == "sleeve_contact"
    assert "od_channel_flow_lpm" not in ts.cooling_fields(base)
    assert ts.cooling_issue({**base, "statorOd": "sleeve_channels"})
    assert ts.cooling_issue({**base, "statorOd": "sleeve_channels",
                             "odChannelFlowLpm": "50"}) is None
    assert ts.cooling_issue({"coolMode": "robotics", "statorOd": "sleeve_channels",
                             "odChannelFlowLpm": "50"})
    assert "50 L/min" in ts.cooling_words(ch)


def test_the_rating_keeps_the_flow_only_with_its_mode():
    both = {"cooling_mode": "manual", "ambient_temp": 35.0, "h_conv": 1100.0,
            "stator_od_cooling": "sleeve_channels", "od_sleeve_wall_mm": 1.5,
            "od_sleeve_k": 160.0, "od_channel_flow_lpm": 10.0}
    assert ccr._prune(dict(both))["od_channel_flow_lpm"] == 10.0
    pruned = ccr._prune({**both, "stator_od_cooling": "sleeve_contact"})
    assert "od_channel_flow_lpm" not in pruned and "od_sleeve_k" in pruned
    assert "od_sleeve_k" not in ccr._prune({**both, "stator_od_cooling": "direct"})
    # read back from a stored map
    back = ccr.cooling_from_duty_thermal({
        "point": {"cooling_mode": "manual", "ambient_temp": 35.0},
        "cooling": {"outer": {"mode": "manual", "h_conv": 1100.0, "stator_od": {
            "mode": "sleeve_channels", "wall_mm": 1.5, "k_sleeve_W_per_mK": 160.0,
            "flow_lpm": 50.0}}}})
    assert back["stator_od_cooling"] == "sleeve_channels"
    assert back["od_channel_flow_lpm"] == 50.0


def test_the_cache_key_of_every_other_mode_is_unchanged(monkeypatch):
    from motor_ai_sim.routes import thermal as th
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *_a, **_k: "fp")
    monkeypatch.setattr(sim, "_config_physics_fingerprint", lambda *_a, **_k: "cfg")
    monkeypatch.setattr(th, "_override_props", lambda: {})
    monkeypatch.setattr(th, "_thermal_k", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(th, "_thermal_k_any", lambda *_a, **_k: 1.0)
    kw = dict(ambient_temp=40, h_conv=50, slot_k=0.5, rpm=1000, gamma_deg=0,
              I_phase_rms=10, n_steps_per_period=10, n_periods=1, mesh_size_mm=1,
              min_size_mm=0.5, outer_air_factor=1.2, n_sectors=1, coil_temp_c=80,
              component_mesh="auto", cooling_mode="manual", air_speed_mps=0,
              fluid="water", fluid_temp_in_c=25, flow_lpm=0)
    d = th._field_cache_key(None, {}, **kw)
    sl = th._field_cache_key(None, {}, stator_od_cooling="sleeve_contact", **kw)
    # a stray flow changes neither
    assert th._field_cache_key(None, {}, od_channel_flow_lpm=9.0, **kw) == d
    assert th._field_cache_key(None, {}, stator_od_cooling="sleeve_contact",
                               od_channel_flow_lpm=9.0, **kw) == sl
    c10 = th._field_cache_key(None, {}, stator_od_cooling="sleeve_channels",
                              od_channel_flow_lpm=10.0, **kw)
    c50 = th._field_cache_key(None, {}, stator_od_cooling="sleeve_channels",
                              od_channel_flow_lpm=50.0, **kw)
    assert c10 != c50 and c10[:len(d)] == d and c10 != sl


# ── the product's own function, on a mesh from the product's own builder ───

@pytest.fixture(scope="module")
def _sandbox_materials():
    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache
    path = Path(DEFAULT_CONFIG_PATH)
    original = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(original) or {}
    doc["materials"] = {**(doc.get("materials") or {}), "stator_core": "20SW1200",
                        "rotor_core": "20SW1200", "shaft": "Aluminium_6061",
                        "slot_insulation": "Nomex", "wire_insulation": "polyimide",
                        "slot": "copper"}
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    clear_config_cache()
    yield
    path.write_text(original, encoding="utf-8")
    clear_config_cache()


def _em_map(mesh_mm, min_mm, split):
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.routes.simulation import _parse_geo_override, build_fem_mesh_2d_sliding_band
    geo_json = json.dumps(GEO_200)
    ov = _parse_geo_override(geo_json)
    em = th._run_coro(build_fem_mesh_2d_sliding_band(
        rotor_angle_deg=0.0, mesh_size_mm=mesh_mm, min_size_mm=min_mm, outer_air_factor=1.3,
        band_thickness_mm=0.4, gap_layers=1.0, n_sectors=4, stator_fillet_mm=0.0,
        component_mesh=th._auto_coil_mesh("", mesh_mm, ov), surface_deviation=0.005,
        normal_deviation=8.0, aspect_ratio=10.0, pole_copy=False, iron_template=True,
        geo_mesh=True, hi_fidelity=False, structured_gap=True, geo=geo_json))
    v, t = np.asarray(em["vertices"], float), np.asarray(em["triangles"], int)
    tg = np.asarray(em["domain_per_tri"], int)
    xy = v[t]
    a = 0.5 * np.abs((xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1])
                     - (xy[:, 2, 0] - xy[:, 0, 0]) * (xy[:, 1, 1] - xy[:, 0, 1]))
    dens = np.zeros(t.shape[0])
    for tags, w in (((1,), split["stator"]), ((5,), split["rotor"]), ((4, 44), split["magnet"])):
        m = np.isin(tg, tags)
        dens[m] = w / (a[m].sum() * STACK_M * 4)
    tot = sum(split.values())
    return {**em, "loss_density_per_tri": dens.tolist(), "P_cu_exact_W": split["cu"],
            "P_cu_W": split["cu"], "P_fe_W": split["stator"] + split["rotor"],
            "P_mag_eddy_W": split["magnet"], "P_loss_total_exact_W": tot, "P_loss_total_W": tot,
            "n_sectors": 4, "symmetry_mult": 4, "poles_per_sector": 7}, geo_json


@pytest.fixture(scope="module")
def maps(_sandbox_materials):
    """Two resolutions.  Netgen where it can load; on a workstation whose App
    Control blocks it, the explicitly named gmsh backend."""
    old = os.environ.get("MOTOR_AI_SIM_GEO_CDT")
    try:
        import netgen  # noqa: F401
    except Exception:  # noqa: BLE001
        os.environ["MOTOR_AI_SIM_GEO_CDT"] = "gmsh"
    split = {"cu": 1000.0, "stator": 500.0, "rotor": 195.0, "magnet": 510.0}
    try:
        yield {"fine": _em_map(1.5, 0.3, split), "coarse": _em_map(15.0, 1.0, split)}
    finally:
        if old is None:
            os.environ.pop("MOTOR_AI_SIM_GEO_CDT", None)
        else:
            os.environ["MOTOR_AI_SIM_GEO_CDT"] = old


def _field(maps, which, **kw):
    from motor_ai_sim.routes import thermal as th
    (em, geo_json), mesh = maps[which], {"fine": (1.5, 0.3), "coarse": (15.0, 1.0)}[which]
    return th.solve_thermal_field(
        ambient_temp=35.0, h_conv=1100.0, cooling_mode="manual", bore_mode="none", rpm=2600.0,
        gamma_deg=40.0, I_phase_rms=100.0, n_steps_per_period=4, n_periods=1.0,
        mesh_size_mm=mesh[0], min_size_mm=mesh[1], n_sectors=4, coil_temp_c=100.0, geo=geo_json,
        _em_map=em, _em_loss_source={"kind": "provided", "note": "fixture"},
        bearing_node="off", end_faces="none", **kw)


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    from motor_ai_sim.routes import thermal as th
    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path", lambda: str(tmp_path / ".last.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path", lambda: str(tmp_path / ".maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    yield
    th._LOSS_MAPS.clear()


def _sod(out):
    return out["cooling"]["outer"]["stator_od"]


def test_the_payload_states_every_number_and_closes(maps):
    out = _field(maps, "fine", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=10.0)
    s = _sod(out)
    assert s["mode"] == "sleeve_channels" and s["flow_lpm"] == 10.0 and s["n_notches"] == 12
    assert s["notch_area_total_mm2"] == pytest.approx(2184.5, abs=0.5)
    assert s["h_channel_W_per_m2K"] == pytest.approx(683, abs=2) and s["re"] == pytest.approx(983, abs=2)
    assert s["intake_momentum_drag_estimate_N"] == pytest.approx(1.708, abs=0.001)
    assert s["warnings"] == []
    hb = s["heat_balance"]
    assert hb["closes"] and abs(hb["channel_W"] + hb["arc_W"] - hb["losses_W"]) < 0.01
    # the water rise is the energy balance of the channel heat, the node its mean
    assert s["water_rise_K"] == pytest.approx(s["channel_heat_removed_W"] / 681.6, abs=0.01)
    assert s["bulk_mean_error_K"] < 1e-3
    assert s["t_out_c"] == pytest.approx(35.0 + s["water_rise_K"], abs=1e-3)
    # the budget sees one OD with its two lines
    b = out["cooling"]["heat_budget"]
    assert b["housing_W"] == pytest.approx(b["od_arcs_W"] + b["od_channels_W"], abs=0.02)
    # the sleeve's own film keeps its key
    assert s["G_film_W_per_K"] == pytest.approx(1100.0 * s["sleeve_outer_area_m2"], rel=1e-3)
    # hand: the two paths in parallel against the water, within the 2-D spreading
    g_ch, g_arc = s["G_channel_film_W_per_K"], s["G_total_W_per_K"]
    assert hb["channel_W"] / hb["arc_W"] == pytest.approx(g_ch / g_arc, rel=0.35)


def test_flow_order_on_the_real_section(maps):
    t0 = _field(maps, "fine", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=1e-4)
    t10 = _field(maps, "fine", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=10.0)
    t50 = _field(maps, "fine", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=50.0)
    w = [x["components"]["winding"]["max"] for x in (t0, t10, t50)]
    assert w[0] > w[1] > w[2] > 35.0
    # the no-flow limit is the series arc path: 35 + P / G_total at the arcs
    s0 = _sod(t0)
    assert s0["t_contact_mean_c"] == pytest.approx(
        35.0 + s0["heat_balance"]["losses_W"] / s0["G_total_W_per_K"], abs=0.5)
    assert s0["channel_heat_removed_W"] < 1.0
    assert s0["warnings"]          # the mean of in and out is out of its range there


def test_the_wetted_areas_match_between_two_resolutions(maps):
    a = _sod(_field(maps, "fine", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=10.0))
    b = _sod(_field(maps, "coarse", stator_od_cooling="sleeve_channels", od_channel_flow_lpm=10.0))
    for s in (a, b):
        assert s["channel_iron_area_mesh_m2"] == pytest.approx(s["channel_iron_area_m2"], rel=0.02)
        assert s["arc_area_mesh_m2"] == pytest.approx(s["contact_area_m2"], rel=0.02)
    assert a["channel_iron_area_mesh_m2"] == pytest.approx(b["channel_iron_area_mesh_m2"], rel=0.02)
    assert a["arc_area_mesh_m2"] == pytest.approx(b["arc_area_mesh_m2"], rel=0.02)
    assert a["channel_heat_removed_W"] == pytest.approx(b["channel_heat_removed_W"], rel=0.02)


def test_a_direct_map_carries_nothing_of_the_new_mode(maps):
    out = _field(maps, "coarse")
    assert "stator_od" not in out["cooling"]["outer"]
    assert "od_channels_W" not in out["cooling"]["heat_budget"]
    sl = _field(maps, "coarse", stator_od_cooling="sleeve_contact")
    assert _sod(sl)["mode"] == "sleeve_contact"
    assert "flow_lpm" not in _sod(sl) and "od_channels_W" not in sl["cooling"]["heat_budget"]


def test_the_coupled_route_refuses_a_smooth_stator_before_its_em_run():
    from fastapi import HTTPException
    from motor_ai_sim.routes import coupled as cp
    cp._od_channel_preflight({"geo": json.dumps(GEO_200)})
    with pytest.raises(HTTPException) as exc:
        cp._od_channel_preflight({"geo": json.dumps({**GEO_200, "stator_outer_notches": False})})
    assert exc.value.status_code == 422


def test_the_route_query_parameter_reaches_the_cache_key_fields():
    """/field and /coupled both declare the flow; the duty cycle compares it."""
    import inspect
    from motor_ai_sim.routes import thermal as th
    for fn in (th.solve_thermal_field, th._validate_field_params, th._field_cache_key):
        assert "od_channel_flow_lpm" in inspect.signature(fn).parameters
    assert "od_channel_flow_lpm" in th._DC_COOLING_KEYS
    assert "od_channel_flow_lpm" in ccr.COOLING_KEYS
