"""The 3D tab's payloads — shape, honesty, and the bound on size.

Three things can go wrong in a viewer and only one of them is visible on
screen.  A payload can be malformed (the browser shows nothing, and you find
out); a payload can be too big (the browser hangs, and you find out); or a
payload can be *quietly wrong* — the surface of a mesh that is not the mesh, a
field labelled as this machine that was solved for another one.  The third is
the one that ships, so most of what is pinned here is provenance rather than
geometry.

Nothing in this file solves.  The solve is minutes and is gated behind
``STATIC3D_FULL=1`` in the Stage A/B suites; what the tab must never do is
solve on a GET, and that is pinned directly (``test_field_without_a_cached_solve
_offers_to_run_it_and_does_not_run_it``).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import static3d as S3
from motor_ai_sim.simulation.static3d import viewer as V

PRESET = "my_40mm_last"
_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def section():
    """The pinned machine, built once — a CadQuery rebuild per test is a minute."""
    sec, fp, mats, mode, geo = S3._get_section(PRESET, "stage_a")
    return sec


@pytest.fixture(scope="module")
def tagged_mesh(section):
    """The coarse tet mesh, built once for the whole module."""
    tm, _sect = S3._build_mesh(section, V.FIDELITY["coarse"])
    return tm


# --------------------------------------------------------------------------
# the machine is the one the passport measured
# --------------------------------------------------------------------------

def test_the_tab_draws_the_machine_stage_a_measured(section):
    """A guard, not a physics test.  If the preset moves, every picture in the
    tab stops being comparable with ``config/end_effect_3d.json`` — and the tab
    exists precisely to let the passport's numbers be looked at."""
    assert section.num_slots == 12 and section.num_poles == 14
    assert section.n_sectors == 2 and section.antiperiodic
    assert abs(section.Br_T - 1.19) < 5e-3, section.Br_T
    assert abs(section.mu_rec - 1.05) < 5e-3, section.mu_rec


def test_the_passport_match_is_decided_on_geometry_not_on_a_dead_fingerprint():
    """The passport's stored ``geometry_fingerprint`` was taken against a live
    config that has since moved, so it can never be reproduced again.  The
    pinned geometry dict it ALSO stored can be, and that is what the verdict
    must rest on."""
    geo = S3._preset_geometry(PRESET)
    m = S3._passport_match(geo)
    assert m["comparable"] is True
    assert m["matches"] is True, m.get("differences")

    moved = dict(geo)
    moved["stator_diameter"] = float(geo["stator_diameter"]) + 1.0
    m2 = S3._passport_match(moved)
    assert m2["matches"] is False
    assert any(d["key"] == "stator_diameter" for d in m2["differences"])


def test_retuning_a_fidelity_rung_misses_the_cache(monkeypatch):
    """A rung's NAME is not the rung.  "coarse" was retuned once already, and a
    cache keyed on the name alone would have served the old mesh under the new
    label indefinitely — the exact stale-machine failure, one level down."""
    before = S3._stem("abc", "coarse", "mesh")
    tweaked = dict(V.FIDELITY["coarse"], h_gap=0.11)
    monkeypatch.setitem(V.FIDELITY, "coarse", tweaked)
    after = S3._stem("abc", "coarse", "mesh")
    assert before != after, (before, after)
    assert before.startswith("abc__coarse-") and after.startswith("abc__coarse-")


def test_the_fingerprint_tracks_the_machine_and_the_materials():
    geo = S3._preset_geometry(PRESET)
    a = S3._machine_fingerprint(geo, S3.STAGE_A_MATERIALS)
    assert a == S3._machine_fingerprint(dict(geo), dict(S3.STAGE_A_MATERIALS))

    moved = dict(geo, magnet_height=float(geo["magnet_height"]) + 0.1)
    assert S3._machine_fingerprint(moved, S3.STAGE_A_MATERIALS) != a

    other_mag = dict(S3.STAGE_A_MATERIALS, magnet="F52SH_120C")
    assert S3._machine_fingerprint(geo, other_mag) != a, (
        "a different magnet grade is a different machine for a FIELD; the "
        "cache key has to say so or the tab shows F45SH's field under F52SH's "
        "name")


# --------------------------------------------------------------------------
# surface extraction
# --------------------------------------------------------------------------

def test_the_surface_is_the_skin_and_nothing_but_the_skin(tagged_mesh, section):
    """Every drawn triangle is a face of a real tet, belongs to a solid region,
    and has a neighbour that is either absent or a different material.  That
    last clause is the whole compression: interior faces of one region are the
    overwhelming majority and none of them may be shipped."""
    tm = tagged_mesh
    reg = np.asarray(tm.cell_region)
    air = int(dict(tm.names)["air"])
    tri, owner, opp = V.surface_faces(tm.mesh, reg, air_id=air)

    assert tri.shape[0] == 3 and tri.shape[1] == owner.size == opp.size
    assert tri.shape[1] > 0
    assert np.all(reg[owner] != air), "air was drawn"
    # the skin is a small fraction of the volume mesh's faces
    assert tri.shape[1] < 0.2 * 4 * tm.n_elements

    # every triangle's nodes are three DISTINCT nodes of its own tet
    t = np.asarray(tm.mesh.t)
    for k in np.linspace(0, tri.shape[1] - 1, 40).astype(int):
        nodes = set(tri[:, k].tolist())
        assert len(nodes) == 3
        assert nodes <= set(t[:, owner[k]].tolist())
        assert opp[k] in set(t[:, owner[k]].tolist()) - nodes


def test_every_solid_region_reaches_the_surface(tagged_mesh, section):
    """Seven magnets, a stator, a rotor and a shaft.  A region that meshes but
    never appears in the payload is invisible in the tab, and an invisible
    magnet is exactly the failure a viewer is supposed to catch."""
    out = V.surface_payload(tagged_mesh, section)
    drawn = {r["name"] for r in out["regions"]}
    expected = {r.name for r in section.regions}
    assert expected <= drawn, expected - drawn
    assert len([n for n in drawn if n.startswith("magnet_")]) == 7
    for r in out["regions"]:
        assert r["tri_count"] > 0
        assert len(r["positions"]) == 3 * r["vertex_count"]
        assert len(r["indices"]) == 3 * r["tri_count"]
        assert max(r["indices"]) < r["vertex_count"]


def test_the_reported_counts_are_the_volume_mesh_not_the_drawn_surface(
        tagged_mesh, section):
    """The number beside the picture must describe the model, not the picture.
    A viewer that reports what it drew makes a 56 000-tet mesh look like a
    10 000-triangle one."""
    out = V.surface_payload(tagged_mesh, section)
    c = out["counts"]
    assert c["tets"] == tagged_mesh.n_elements
    assert c["nodes"] == tagged_mesh.n_vertices
    assert c["tets"] == c["tets_solid"] + c["tets_air"]
    assert c["tets"] > 10 * out["faces_total"] / 4
    assert c["sector_deg"] == 180.0 and c["antiperiodic"] is True


# --------------------------------------------------------------------------
# cut planes
# --------------------------------------------------------------------------

def test_a_cut_plane_removes_elements_and_opens_the_inside(tagged_mesh, section):
    """A z cut must drop tets above the plane AND expose the faces that were
    interior — if the face count only fell, the cut would just be hiding the
    model, not opening it."""
    tm = tagged_mesh
    full = V.surface_payload(tm, section)
    half = V.surface_payload(tm, section, cut_z_mm=3.0)
    assert half["faces_total"] < full["faces_total"]

    zc = V.element_centroids(tm.mesh)[2] / 1e-3
    keep = V.cut_mask(tm.mesh, cut_z_mm=3.0)
    assert keep.sum() < zc.size and keep.sum() > 0
    assert zc[keep].max() <= 3.0 + 1e-9
    # the newly exposed interior: faces owned by kept tets whose neighbour was
    # cut away.  There must be some, or nothing was opened.
    reg = np.asarray(tm.cell_region)
    tri_f, own_f, _ = V.surface_faces(tm.mesh, reg, air_id=0)
    tri_h, own_h, _ = V.surface_faces(tm.mesh, reg, keep=keep, air_id=0)
    solid_below = keep & (reg != 0)
    assert own_h.size > 0
    assert (own_h.size - int((keep[own_f]).sum())) > 0, (
        "the cut removed faces but exposed none — nothing was opened")
    assert solid_below.sum() > 0


def test_a_theta_cut_keeps_only_the_wedge_it_says_it_keeps(tagged_mesh):
    keep = V.cut_mask(tagged_mesh.mesh, cut_theta_deg=90.0)
    c = V.element_centroids(tagged_mesh.mesh)
    th = np.degrees(np.arctan2(c[1], c[0]))
    th = np.where(th < -1e-9, th + 360.0, th)
    assert keep.any() and not keep.all()
    assert th[keep].max() <= 90.0 + 1e-9


# --------------------------------------------------------------------------
# the bound on payload size
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cap", [500, 2000, 8000])
def test_decimation_actually_bounds_the_payload(tagged_mesh, section, cap):
    """The cap is a promise about bytes, so it is checked in bytes as well as
    in triangles — and the payload must ADMIT it was decimated, because a
    holed surface that claims to be complete is the dishonest failure.

    The byte bound is derived, not guessed.  Worst case a strided subset shares
    no vertices at all, so a triangle costs 3 vertices x 3 coordinates at ~11
    bytes ("-12.3457,") plus three indices and one value: under 110 bytes.
    That worst case is close to real here, which is exactly why the cap has to
    be stated in triangles and checked in bytes."""
    out = V.surface_payload(tagged_mesh, section, max_tris=cap)
    assert out["faces_shown"] <= cap * 1.05 + 64 * len(out["regions"])
    assert out["faces_shown"] < out["faces_total"]
    assert out["decimated"] is True
    assert out["max_tris"] == cap
    size = len(json.dumps(out))
    assert size < 110 * cap + 25_000, (cap, size)


def test_a_smaller_cap_is_a_smaller_payload(tagged_mesh, section):
    """Monotone, and strictly below the uncapped payload — a cap that does not
    shrink anything is a cap in name only."""
    sizes = [len(json.dumps(V.surface_payload(tagged_mesh, section,
                                              max_tris=c)))
             for c in (500, 2000, 8000, 10 ** 6)]
    assert sizes == sorted(sizes)
    assert sizes[0] < sizes[-1] / 4


def test_an_uncapped_payload_says_it_is_complete_and_stays_small(
        tagged_mesh, section):
    out = V.surface_payload(tagged_mesh, section, max_tris=10 ** 6)
    assert out["decimated"] is False
    assert out["faces_shown"] == out["faces_total"]
    assert len(json.dumps(out)) < 1_500_000


def test_the_budget_is_shared_so_small_regions_do_not_vanish(
        tagged_mesh, section):
    """A magnet is 3 % of the surface.  Give the cap to whoever asks first and
    the seven magnets disappear while the stator stays whole."""
    out = V.surface_payload(tagged_mesh, section, max_tris=800)
    mags = [r for r in out["regions"] if r["kind"] == "magnet"]
    assert len(mags) == 7
    for r in mags:
        assert r["tri_count"] >= 32, r


# --------------------------------------------------------------------------
# fields
# --------------------------------------------------------------------------

def test_values_ride_with_their_own_triangle(tagged_mesh, section):
    """One value per drawn triangle, taken from the owning element — never
    averaged onto shared vertices, which would draw a field the solve does not
    have."""
    ne = tagged_mesh.n_elements
    fake = np.arange(ne, dtype=float)
    out = V.surface_payload(tagged_mesh, section, values_el=fake, max_tris=10 ** 6)
    for r in out["regions"]:
        assert r["values"] is not None
        assert len(r["values"]) == r["tri_count"]
    tot = sum(len(r["values"]) for r in out["regions"])
    assert tot == out["faces_shown"]


def test_the_colour_range_is_percentiles_because_linear_iron_has_a_spike():
    """min/max on this machine puts the whole picture at one colour and one
    bright dot in a re-entrant corner.  The range must be robust and must SAY
    it clipped."""
    v = np.concatenate([np.random.RandomState(0).normal(1.0, 0.05, 10000),
                        np.array([28.4])])
    lo, hi = V.percentile_range(v)
    assert hi < 2.0 and lo > 0.5
    assert V.percentile_range(np.array([np.nan, np.nan]))[1] > \
        V.percentile_range(np.array([np.nan, np.nan]))[0]
    a, b = V.percentile_range(np.full(100, 3.0))
    assert b > a, "a constant field must still give a usable range"


def test_demag_is_defined_only_inside_the_magnets(tagged_mesh, section):
    """``H . M_hat`` has no meaning in iron or air, and a field view that paints
    it there is inventing data.  The array must be NaN everywhere else."""
    class _FakeSol:
        def __init__(self, ne):
            self._B = np.zeros((3, ne))
            self._B[0] = 0.5
            self.M_el = np.zeros((3, ne))

        def B_elementwise(self):
            return self._B

        def mu_diag(self):
            return np.full((3, self._B.shape[1]), 4e-7 * np.pi)

    fa = V.field_arrays(_FakeSol(tagged_mesh.n_elements), tagged_mesh, section)
    d = fa["demag"]
    mag_el = np.concatenate([tagged_mesh.elements(r.name)
                             for r in section.magnet_regions()])
    other = np.setdiff1d(np.arange(d.size), mag_el)
    assert np.all(np.isfinite(d[mag_el]))
    assert np.all(np.isnan(d[other])), "demag was painted outside the magnets"


# --------------------------------------------------------------------------
# geometry payload
# --------------------------------------------------------------------------

def test_geometry_payload_carries_the_magnetisation_direction(section):
    """Spoke PM: the magnetisation is TANGENTIAL and alternates.  A viewer that
    draws it radial (the default assumption for a surface-PM machine) would be
    drawing a different motor, so the direction is shipped per magnet and the
    alternation is pinned here."""
    g = V.geometry_payload(section)
    mags = [r for r in g["regions"] if r["kind"] == "magnet"]
    assert len(mags) == 7
    for m in mags:
        assert m["M_dir_deg"] is not None
        assert m["M_mag_A_per_m"] > 1e5
        assert abs(m["M_A_per_m"][2]) < 1e-9, "M_z must be zero for spoke PM"
    # tangential, not radial: the direction is ~90 deg off the centroid radius
    import numpy as _np
    offs = []
    for m in mags:
        assert m["parts"] and m["parts"][0]["outer"], m["name"]
        cx, cy = m["centroid_mm"]
        radial = _np.degrees(_np.arctan2(cy, cx))
        d = abs(((m["M_dir_deg"] - radial) + 90.0) % 180.0 - 90.0)
        offs.append(d)
    assert min(offs) > 45.0, offs


def test_geometry_payload_is_small_enough_to_be_the_first_thing_drawn(section):
    g = V.geometry_payload(section)
    assert len(json.dumps(g)) < 250 * 1024
    assert g["sector"]["sector_deg"] == 180.0
    assert g["extrusion"]["modelled_z_lo_mm"] == 0.0
    assert g["extrusion"]["z_lo_mm"] == -g["extrusion"]["z_hi_mm"]
    assert g["coils"]["sides"], "no copper — the winding would be invisible"


def test_magnet_polarity_comes_from_M_not_from_the_index(section):
    """The sector holds SEVEN poles — an odd number.  Colour them by index
    parity and the repeated view puts two same-poled magnets against each other
    at the seam, which is a picture of a machine that does not exist.  Polarity
    is read off each magnet's own M against the local tangent, and the browser
    flips the whole copy instead."""
    pol = V.magnet_polarity(section)
    assert len(pol) == 7
    assert set(pol.values()) == {1, -1}, pol
    # spoke PM alternates: consecutive magnets round the bore must differ
    order = sorted(section.magnet_regions(),
                   key=lambda r: math.atan2(r.polygon.centroid.y,
                                            r.polygon.centroid.x))
    signs = [pol[r.name] for r in order]
    assert all(a != b for a, b in zip(signs, signs[1:])), signs
    # and index parity would have got it right INSIDE the sector, which is
    # exactly why the bug survives casual inspection
    assert len(signs) % 2 == 1


def test_polarity_travels_with_both_payloads(section, tagged_mesh):
    g = V.geometry_payload(section)
    for r in g["regions"]:
        assert (r["polarity"] in (1, -1)) == (r["kind"] == "magnet")
    s = V.surface_payload(tagged_mesh, section)
    for r in s["regions"]:
        assert (r["polarity"] in (1, -1)) == (r["kind"] == "magnet")


def test_a_hole_stays_a_hole_and_a_second_body_stays_a_second_body():
    """Nesting is structure, not decoration.

    Flatten ``[exterior, hole]`` and ``[body_a, body_b]`` into one ring list and
    the browser cannot tell them apart: a pocket renders as solid metal, or two
    separate pieces render as one piece with a bite out of it.  Pinned on
    synthetic polygons rather than on this machine, because THIS machine's
    sector happens to have no interior ring at all — which is precisely the
    condition under which the bug would ship unnoticed."""
    from shapely.geometry import MultiPolygon, Polygon

    square = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                     [[(3, 3), (3, 6), (6, 6), (6, 3)]])
    parts = V._rings(square)
    assert len(parts) == 1
    assert len(parts[0]["holes"]) == 1
    assert len(parts[0]["outer"]) == 4 and len(parts[0]["holes"][0]) == 4

    two = MultiPolygon([Polygon([(0, 0), (1, 0), (1, 1)]),
                        Polygon([(5, 5), (6, 5), (6, 6)])])
    parts = V._rings(two)
    assert len(parts) == 2
    assert all(p["holes"] == [] for p in parts)


def test_every_emitted_ring_is_a_ring(section):
    g = V.geometry_payload(section)
    for r in g["regions"]:
        assert r["parts"], r["name"]
        for p in r["parts"]:
            assert len(p["outer"]) >= 3
            for h in p["holes"]:
                assert len(h) >= 3
    for c in g["coils"]["sides"]:
        assert c["parts"] and all(len(p["outer"]) >= 3 for p in c["parts"])


def test_the_copper_is_clipped_to_the_same_sector_as_the_iron(section):
    """A full ring of conductors around half a stator is a picture of nothing.
    The copper is cut by the same wedge the iron was."""
    g = V.geometry_payload(section)
    c = g["coils"]
    assert c["n_sides_full_ring"] > c["n_sides_sector"] > 0
    assert c["n_sides_sector"] == len(c["sides"])
    # 180 deg of a 360 deg ring: half the conductors, give or take a cut one
    ratio = c["n_sides_sector"] / c["n_sides_full_ring"]
    assert 0.4 < ratio < 0.6, ratio
    span = math.radians(section.sector_deg)
    for side in c["sides"]:
        for p in side["parts"]:
            for x, y in p["outer"]:
                th = math.atan2(y, x)
                if th < -1e-6:
                    th += 2 * math.pi
                assert -1e-6 <= th <= span + 1e-6, (x, y, th)


# --------------------------------------------------------------------------
# the routes
# --------------------------------------------------------------------------

def test_machine_endpoint_states_the_sector_and_the_passport(client):
    r = client.get("/api/static3d/machine", params={"preset": PRESET})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["machine"]["num_slots"] == 12
    assert d["machine"]["sector_deg"] == 180.0
    assert d["machine"]["antiperiodic"] is True
    assert d["materials_mode"] == "stage_a"
    assert d["materials_requested"]["magnet"] == "F45SH_120C"
    assert d["passport"]["match"]["matches"] is True
    assert 0.9 < d["passport"]["k_flux"] < 1.0
    assert set(d["fidelities"]) == set(V.FIDELITY)
    for k in d["cost_quote_s"]:
        assert d["cost_quote_s"][k] > 0


def test_geometry_endpoint_labels_the_sector_honestly(client):
    r = client.get("/api/static3d/geometry", params={"preset": PRESET})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["units"] == "mm"
    assert d["sector"]["n_sectors"] == 2
    assert "sector" in d["what"] and "n_sectors" in d["what"]
    assert len(d["regions"]) == 10
    assert d["fingerprint"] and d["fingerprint"] != "nofp"


def test_mesh_endpoint_ships_a_surface_and_reports_the_volume(client):
    r = client.get("/api/static3d/mesh",
                   params={"preset": PRESET, "fidelity": "coarse"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["counts"]["tets"] > 10000
    assert d["faces_total"] < d["counts"]["tets"]
    assert d["knobs"]["h_gap"] == V.FIDELITY["coarse"]["h_gap"]
    assert len(r.content) < 2_000_000, len(r.content)


def test_mesh_endpoint_honours_a_cut_and_a_cap(client):
    base = client.get("/api/static3d/mesh",
                      params={"preset": PRESET, "fidelity": "coarse"}).json()
    cut = client.get("/api/static3d/mesh",
                     params={"preset": PRESET, "fidelity": "coarse",
                             "cut_z_mm": 3.0, "max_tris": 1500}).json()
    assert cut["faces_total"] < base["faces_total"]
    assert cut["decimated"] is True
    assert cut["cut"]["z_mm"] == 3.0


def test_an_unknown_preset_is_a_404_and_an_unknown_fidelity_a_422(client):
    assert client.get("/api/static3d/geometry",
                      params={"preset": "no_such_motor"}).status_code == 404
    assert client.get("/api/static3d/mesh",
                      params={"preset": PRESET,
                              "fidelity": "ludicrous"}).status_code == 422
    assert client.get("/api/static3d/field",
                      params={"preset": PRESET,
                              "quantity": "vibes"}).status_code == 422


def test_field_without_a_cached_solve_offers_to_run_it_and_does_not_run_it(
        client, monkeypatch, tmp_path):
    """The failure this pins: a GET that quietly starts a ten-minute solve.
    An empty cache must come back as an OFFER, with the measured cost, in well
    under the time a solve would take."""
    monkeypatch.setattr(S3, "_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    import time as _t
    t0 = _t.perf_counter()
    r = client.get("/api/static3d/field",
                   params={"preset": PRESET, "fidelity": "medium",
                           "nonlinear": True})
    dt = _t.perf_counter() - t0
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["available"] is False
    assert d["quote_s"] == V.COST_S[("medium", False)]
    assert "no cached solve" in d["reason"]
    assert dt < 20.0, f"a GET spent {dt:.0f}s — it solved something"
    assert not list(Path(tmp_path).glob("*magnet*"))


def _fake_entry(tmp_path, section, tm, fingerprint):
    """Write a cache entry by hand, so the stale guard can be tested without
    spending ten minutes producing a real one."""
    ne = tm.n_elements
    meta = S3._mesh_meta(tm, V.FIDELITY["coarse"], "coarse")
    meta.update(fingerprint=fingerprint, kind="magnet_nonlinear",
                preset=PRESET, materials=dict(S3.STAGE_A_MATERIALS),
                materials_mode="stage_a", wall_s=1.0,
                solved_utc="2026-01-01T00:00:00Z",
                solve={"excitation": "magnet-only (I = 0)", "tets": ne,
                       "ndofs": 123, "picard_converged": True,
                       "picard_residual": 0.0012, "element_order": 1,
                       "iron": "nonlinear B-H (Picard)"})
    rng = np.random.RandomState(0)
    arrays = {
        "p": np.asarray(tm.mesh.p, dtype=np.float64),
        "t": np.asarray(tm.mesh.t, dtype=np.int32),
        "cell_region": np.asarray(tm.cell_region, dtype=np.int16),
        "Bmag": rng.uniform(0.1, 1.8, ne).astype(np.float32),
        "B": rng.normal(0, 1, (3, ne)).astype(np.float32),
        "demag_el": np.full(ne, np.nan, dtype=np.float32),
    }
    mag = np.concatenate([tm.elements(r.name) for r in section.magnet_regions()])
    arrays["demag_el"][mag] = rng.uniform(-7e5, -3e5, mag.size)
    S3._write_entry(S3._stem(fingerprint, "coarse", "magnet_nonlinear"),
                    meta, arrays)


def test_a_field_solved_for_another_machine_comes_back_flagged_stale(
        client, monkeypatch, tmp_path, section, tagged_mesh):
    """The whole point of the fingerprint.  A cached field whose machine has
    moved must be served with ``stale_geometry: true`` and a reason the UI can
    paint red — not withheld (the user asked for it) and not served silently
    (the user would believe it)."""
    monkeypatch.setattr(S3, "_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    live = S3._machine_fingerprint(S3._preset_geometry(PRESET),
                                   S3.STAGE_A_MATERIALS)

    _fake_entry(tmp_path, section, tagged_mesh, live)
    ok = client.get("/api/static3d/field",
                    params={"preset": PRESET, "fidelity": "coarse"}).json()
    assert ok["available"] is True
    assert ok["stale_geometry"] is False
    assert ok["stale_reason"] is None
    assert ok["fingerprint_solved"] == live

    # now the same entry, but stamped with a different machine
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    stem = S3._stem(live, "coarse", "magnet_nonlinear")
    js = Path(tmp_path) / f"{stem}.json"
    m = json.loads(js.read_text(encoding="utf-8"))
    m["fingerprint"] = "0" * 16
    js.write_text(json.dumps(m), encoding="utf-8")

    bad = client.get("/api/static3d/field",
                     params={"preset": PRESET, "fidelity": "coarse"}).json()
    assert bad["available"] is True, "a stale result is flagged, not withheld"
    assert bad["stale_geometry"] is True
    assert bad["stale_reason"] == "geometry"
    assert bad["fingerprint_solved"] == "0" * 16
    assert bad["fingerprint_live"] == live


def test_a_result_with_no_fingerprint_is_unknown_not_fine(
        client, monkeypatch, tmp_path, section, tagged_mesh):
    """``stale_geometry`` must be null, never false, when the stored run predates
    the stamp — "cannot prove" is not "proved fine"."""
    monkeypatch.setattr(S3, "_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    live = S3._machine_fingerprint(S3._preset_geometry(PRESET),
                                   S3.STAGE_A_MATERIALS)
    _fake_entry(tmp_path, section, tagged_mesh, live)
    stem = S3._stem(live, "coarse", "magnet_nonlinear")
    js = Path(tmp_path) / f"{stem}.json"
    m = json.loads(js.read_text(encoding="utf-8"))
    m.pop("fingerprint")
    js.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])

    d = client.get("/api/static3d/field",
                   params={"preset": PRESET, "fidelity": "coarse"}).json()
    assert d["stale_geometry"] is None
    assert d["stale_reason"] is None


def test_a_cached_field_names_the_solve_that_produced_it(
        client, monkeypatch, tmp_path, section, tagged_mesh):
    """A picture of a field with no provenance is decoration.  Mesh size, dof
    count, element order, iron model and Picard residual travel with it."""
    monkeypatch.setattr(S3, "_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    live = S3._machine_fingerprint(S3._preset_geometry(PRESET),
                                   S3.STAGE_A_MATERIALS)
    _fake_entry(tmp_path, section, tagged_mesh, live)

    d = client.get("/api/static3d/field",
                   params={"preset": PRESET, "fidelity": "coarse",
                           "quantity": "Bmag", "vectors": True,
                           "max_vectors": 300}).json()
    s = d["solve"]
    assert s["excitation"] == "magnet-only (I = 0)"
    assert "nonlinear" in s["iron"]
    assert s["tets"] == tagged_mesh.n_elements
    assert s["picard_converged"] is True and s["picard_residual"] > 0
    assert d["scale"]["unit"] == "T"
    assert d["scale"]["vmax"] > d["scale"]["vmin"]
    assert d["counts"]["tets"] == tagged_mesh.n_elements
    assert d["vectors"]["shown"] <= 300
    assert len(d["vectors"]["points"]) == 3 * d["vectors"]["shown"]
    assert len(d["vectors"]["vectors"]) == 3 * d["vectors"]["shown"]


def test_the_demag_view_draws_magnets_only(
        client, monkeypatch, tmp_path, section, tagged_mesh):
    monkeypatch.setattr(S3, "_CACHE_DIR", Path(tmp_path))
    monkeypatch.setattr(S3, "_ENTRY_CACHE", {})
    monkeypatch.setattr(S3, "_ENTRY_ORDER", [])
    live = S3._machine_fingerprint(S3._preset_geometry(PRESET),
                                   S3.STAGE_A_MATERIALS)
    _fake_entry(tmp_path, section, tagged_mesh, live)

    d = client.get("/api/static3d/field",
                   params={"preset": PRESET, "fidelity": "coarse",
                           "quantity": "demag"}).json()
    assert {r["kind"] for r in d["regions"]} == {"magnet"}
    assert d["scale"]["unit"] == "A/m"
    assert d["scale"]["vmax"] < 0, "H . M_hat is demagnetising here"
    assert all(all(np.isfinite(r["values"])) for r in d["regions"])


def test_passport_endpoint_serves_the_spill_curve_and_the_demag_slices(client):
    d = client.get("/api/static3d/passport",
                   params={"preset": PRESET}).json()
    sp = d["spill_profile"]
    assert len(sp["z_over_half_stack"]) == len(sp["B1_normalised"]) > 20
    assert abs(sp["B1_normalised"][0] - 1.0) < 1e-9
    assert 0.9 < d["k_flux"] < 1.0
    assert len(d["demag"]["slices"]) == 6
    assert d["demag"]["end_worst_H"] < d["demag"]["mid_worst_H"] < 0
    assert d["match"]["matches"] is True


def test_solve_is_a_post_and_refuses_a_second_one(client, monkeypatch):
    """No GET may start a solve, and two concurrent ones would fight over gmsh
    and pardiso, both of which are process-global."""
    monkeypatch.setitem(S3._solve_state, "running", True)
    r = client.post("/api/static3d/solve",
                    json={"preset": PRESET, "fidelity": "coarse"})
    assert r.status_code == 409
    p = client.get("/api/static3d/solve/progress").json()
    assert p["running"] is True
