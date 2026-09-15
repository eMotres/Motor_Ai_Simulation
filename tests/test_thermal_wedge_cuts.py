"""The two cut lines of a symmetry wedge are ADIABATIC (2026-09-09).

User, on the G2-L40's quarter-machine thermal map: *"меня пугает
неравномерность, проверь граничные условия"*.  Measured on that map: the four
interior slots' coils sat at 87.0–87.3 °C, the two slots at the sector cuts at
84.0 and 84.1 — 3 K cooler — and the stator iron the same way (72.1–72.4 against
69.5/69.7).  The housing film (a liquid jacket, applied as a 1e5 W/m²K pin at
the coolant outlet) was being selected by facet RADIUS, and the outer 10 % of
each radial cut line passed that rule: the yoke at both cuts was held at 61 °C
by a boundary that does not exist.  A cut is a symmetry plane; nothing crosses
it.

Two claims:
  (a) ``wedge_cut_facets`` finds exactly the two rays of a wedge and nothing on a
      full disk;
  (b) end to end, on the sandbox 30 mm machine's half-wedge with a jacket: the
      edge slot pitches are as warm as the interior ones, and the housing set
      no longer contains a single cut facet.
"""
from __future__ import annotations

import json
import math
import pathlib
import tempfile
import time

import numpy as np
import pytest

from tests.test_thermal_routes import GEO_30MM, store_em_run

FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.0, "min_size_mm": 0.5, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
JACKET = {"cooling_mode": "liquid", "fluid": "water", "fluid_temp_in_c": 30.0,
          "flow_lpm": 8.0, "ambient_temp": 30.0}
RUN_ID = "2026-09-09T16:40:00"


# ---------------------------------------------------------------------------
# (a) the detector
# ---------------------------------------------------------------------------

def _polar_wedge(theta0: float, theta1: float, r0: float = 0.02, r1: float = 0.05):
    """A structured annular sector: two arcs and two rays as its boundary."""
    from skfem import MeshTri

    m = MeshTri.init_tensor(np.linspace(r0, r1, 5), np.linspace(theta0, theta1, 9))
    r, th = m.p[0].copy(), m.p[1].copy()
    p = np.vstack([r * np.cos(th), r * np.sin(th)])
    return MeshTri(p, m.t)


@pytest.mark.parametrize("span", [(0.0, math.pi / 2), (math.pi / 2, math.pi),
                                  (2.5, 2.5 + math.pi / 2)])   # the last straddles ±180°
def test_a_the_two_rays_of_a_wedge_and_nothing_else(span):
    from motor_ai_sim.simulation.thermal_solver_2d import wedge_cut_facets

    m = _polar_wedge(*span)
    bnd = m.boundary_facets()
    cut = wedge_cut_facets(m, bnd)
    fn = m.facets[:, cut]
    th = np.arctan2(m.p[1][fn], m.p[0][fn])
    # every cut facet lies on one of the two rays, radially
    d = np.angle(np.exp(1j * (th - span[0])))
    on_ray = (np.abs(d) < 1e-6) | (np.abs(d - (span[1] - span[0])) < 1e-6)
    assert on_ray.all(), th
    # …and ALL the ray facets were found: 4 radial cells per ray on this grid
    assert cut.size == 8, cut.size
    # the arcs are untouched — they are the bore and the housing
    rest = np.setdiff1d(bnd, cut)
    r = np.hypot(m.p[0][m.facets[:, rest]], m.p[1][m.facets[:, rest]])
    assert np.allclose(r[0], r[1], atol=1e-9)


def test_a_a_full_disk_has_no_cut():
    from skfem import MeshTri

    from motor_ai_sim.simulation.thermal_solver_2d import wedge_cut_facets

    m = MeshTri.init_circle(3)
    assert wedge_cut_facets(m, m.boundary_facets()).size == 0


# ---------------------------------------------------------------------------
# (b) end to end — the sandbox half-machine with a jacket
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def em_run():
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_wedge_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)
    yield info
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()
    yield
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def _per_pitch(payload: dict, part: str, pitch_deg: float):
    """Mean temperature of `part` per angular slot pitch (start deg, mean)."""
    V = np.asarray(payload["vertices"], float)
    T3 = np.asarray(payload["triangles"], int)
    dom = np.asarray(payload["domain_per_tri"], int)
    T = np.asarray(payload["temperature_per_node"], float)
    names = payload["part_names"]
    ids = [int(k) for k, v in names.items() if str(v).lower() == part]
    assert ids, names
    cen = V[T3].mean(axis=1)
    ang = (np.degrees(np.arctan2(cen[:, 1], cen[:, 0])) + 360.0) % 360.0
    Tt = T[T3].mean(axis=1)
    m = np.isin(dom, ids)
    lo, hi = ang[m].min(), ang[m].max()
    rows = []
    for k in range(int(math.floor(lo / pitch_deg)), int(math.ceil(hi / pitch_deg))):
        sel = m & (ang >= k * pitch_deg) & (ang < (k + 1) * pitch_deg)
        if sel.sum() >= 5:
            rows.append((k * pitch_deg, float(Tt[sel].mean())))
    return rows


def test_b_the_edge_slots_are_as_warm_as_the_interior_ones(client, em_run):
    r = client.get("/api/thermal/field",
                   params={**FAST, **JACKET, "geo": json.dumps(GEO_30MM)})
    assert r.status_code == 200, r.text[:900]
    out = r.json()
    assert out["n_cut_facets"] > 0, "the half-wedge has two cut lines"
    # …and the field says how many sectors it is of the machine, so the map's
    # tiler can draw the whole motor (user 2026-09-09: "поля на весь мотор")
    assert out["n_sectors"] == 2 and out["symmetry_mult"] == 2
    # 12 slots on the 30 mm: 30° per pitch, six of them on the half
    rows = _per_pitch(out, "coil", 360.0 / 12)
    assert len(rows) >= 4, rows
    means = np.array([m for _a, m in rows])
    interior = means[1:-1].mean()
    # Before the fix the two edge pitches sat ~3 K under the interior on the
    # G2; on a symmetric half nothing distinguishes an edge slot from an
    # interior one, so the spread is mesh noise — a fraction of a kelvin.
    assert abs(means[0] - interior) < 0.5, (rows, "first pitch")
    assert abs(means[-1] - interior) < 0.5, (rows, "last pitch")
    assert means.max() - means.min() < 0.8, rows
    # The ROTOR too: the bore film's 2 % radius band used to take the first
    # radial facet of each cut, ~0.5 W per cut, a 2 K hump with cool edges
    # across the G2's quarter (user 2026-09-09: "в роторе та же
    # неравномерность и осталась").  14 poles on the 30 mm: seven per half.
    rrows = _per_pitch(out, "rotor", 360.0 / 14)
    assert len(rrows) >= 5, rrows
    rmeans = np.array([m for _a, m in rrows])
    assert abs(rmeans[0] - rmeans[1:-1].mean()) < 0.3, (rrows, "first pole")
    assert abs(rmeans[-1] - rmeans[1:-1].mean()) < 0.3, (rrows, "last pole")
    # …and the cuts really are outside every film: the nodes on them are not
    # pulled toward the coolant (the housing is)
    V = np.asarray(out["vertices"], float)
    T = np.asarray(out["temperature_per_node"], float)
    T3 = np.asarray(out["triangles"], int)
    dom = np.asarray(out["domain_per_tri"], int)
    names = out["part_names"]
    stator_ids = [int(k) for k, v in names.items() if str(v).lower() == "stator"]
    # the STATOR's own nodes: this mesh carries far-field air beyond the
    # housing, so the mesh's outer radius is not the housing's
    sn = np.unique(T3[np.isin(dom, stator_ids)])
    rr = np.hypot(V[:, 0], V[:, 1])
    ang = (np.degrees(np.arctan2(V[:, 1], V[:, 0])) + 360.0) % 360.0
    r_h = rr[sn].max()
    housing = sn[rr[sn] > r_h - 2e-4]
    t_cool = T[housing].mean()
    yoke = sn[(rr[sn] > 0.9 * r_h) & (rr[sn] < 0.985 * r_h)]
    a_lo, a_hi = ang[yoke].min(), ang[yoke].max()
    on_cut = yoke[(np.abs(ang[yoke] - a_lo) < 0.5) | (np.abs(ang[yoke] - a_hi) < 0.5)]
    inside = np.setdiff1d(yoke, on_cut)
    # a 2 mm mesh puts only a couple of yoke nodes on each cut — enough
    assert on_cut.size >= 2 and inside.size > 10
    # MATCHED by radius AND by slot-pitch phase: the yoke is a 1.5 mm band with
    # a 2–3 K gradient across it, and at one radius it is warmer over a tooth
    # than over a slot — so a cut node is compared with the interior nodes one
    # or more pitches in at its own radius and its own phase in the pitch
    drop = abs(T[inside].mean() - t_cool)
    pitch = 360.0 / 12
    diffs = []
    for i in on_cut:
        phase = (ang[i] - a_lo) % pitch
        same_phase = np.abs(((ang[inside] - a_lo) % pitch) - phase) < 0.6
        same_phase |= np.abs(((ang[inside] - a_lo) % pitch) - phase) > pitch - 0.6
        near = inside[same_phase & (np.abs(rr[inside] - rr[i]) < 0.15e-3)]
        if near.size >= 1:
            diffs.append(float(T[i] - T[near].mean()))
    assert diffs, "no radius-matched interior nodes for the cut nodes"
    # diagnostic beside the claim: how many radial boundary facets sit on the
    # two extreme angles (loose 0.5°), against how many the solver kept out
    from skfem import MeshTri
    m = MeshTri(V.T.copy(), T3.T.copy())
    bnd = m.boundary_facets()
    fn = m.facets[:, bnd]
    a0, a1 = ang[fn[0]], ang[fn[1]]
    lo, hi = ang.min(), ang.max()
    near = lambda a, c: np.abs(((a - c + 180) % 360) - 180) < 0.5
    radial = np.abs(rr[fn[0]] - rr[fn[1]]) > 0.25 * np.hypot(V[fn[0], 0] - V[fn[1], 0], V[fn[0], 1] - V[fn[1], 1])
    on_rays = ((near(a0, lo) & near(a1, lo)) | (near(a0, hi) & near(a1, hi))) & radial
    print(f"\ncut facets: solver {out['n_cut_facets']}, recount {int(on_rays.sum())}; "
          f"angles {lo:.2f}..{hi:.2f}; housing facets {out.get('n_housing_facets')}")
    assert abs(np.mean(diffs)) < 0.5 * max(drop, 0.2), (diffs, drop)
