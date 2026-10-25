"""Rotor retention WITHOUT adhesive, and sector masses — the eVTOL review fixes.

Found 2026-10-25 by the engineering agent sizing a customer eVTOL rotor to the
minimum housing wall and bridge that still give SF >= 3 (averaged stress,
1200 rpm overspeed + peak torque, separation contact, no glue).  It had to work
around three solver bugs by using full models only and discarding suspect
contact states by hand.  The owner's rule governs B2/B3:

    magnets are retained ONLY by the rotor iron — no adhesive.  A separation
    result that does not converge, runs away or ends "not retained" is a FAIL
    of the geometry; a bonded (glued) solve may only be shown as labelled info.

B1 — sector masses.  A sector solve of a 56-pole Ø400 IPM rotor reported iron
     12.14 kg / magnets 0.51 kg against the full model's 4.985 / 7.530: the
     total right to 0.1 %, 93 % of the magnet area tagged as iron.  Every
     extensive number of a sector is n times the wedge's, so the guard is that
     the wedge is 1/n of every PART and that the mesh carries the drawn parts.
B2 — the TIED state.  A separation solve ended with every pair shut and pulling
     (the two-sided penalty spring), i.e. the bonded answer: 8.4 µm of OD growth
     against 35–61 µm, 0 % open, 0 MPa contact pressure, tension across the
     joint, "poles held: no" — and an SF printed, because the gap residual of a
     stiff spring in tension is nanometres and passed the acceptance margin.
B3 — runaway fell back to BONDED and printed the bonded SF as the answer.

All rotors here are synthetic (public repository, no customer data).
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon, box

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs
from motor_ai_sim.simulation.mechanical import symmetry as sym

ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
          "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}


# ---------------------------------------------------------------------------
# B2 — the tied state is not a converged separation answer
# ---------------------------------------------------------------------------

def test_the_tension_measure_is_a_force_not_a_gap():
    """Pure arithmetic: a closed separable pair 2 nm apart pulls with k*2 nm,
    and a joint carrying nothing is not inconsistent."""
    gap = np.array([2e-9, -1e-9, 5e-6, 3e-9])
    closed = np.array([True, True, False, True])
    uni = np.array([True, True, True, False])          # the last is bonded
    ten, comp, n = ctc.separation_tension(gap, closed, uni, kc=1e12, tol_g=1e-13)
    assert n == 1
    assert ten == pytest.approx(1e12 * 2e-9)
    assert comp == pytest.approx(1e12 * 1e-9)
    assert ctc.tension_fraction(ten, comp) == pytest.approx(2.0 / 3.0)
    assert ctc.tension_fraction(0.0, 0.0) == 0.0


def _two_rings():
    from tests import test_mechanical_contact as T

    mesh, part, _rcen, _phi = T._two_ring_mesh(T.RI, T.RC, T.RO, 0.0024)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(rs.isotropic_C(T.E_STEEL, T.NU), (ne, 3, 3)).copy()
    # Only the OUTER ring has mass: it grows away from the inner one, so the
    # right answer is a joint that opens completely — and the tied answer is
    # the inner ring hanging on it in tension.
    rho = np.where(part == ctc.PART_MAGNET, 0.0, T.RHO_STEEL)
    specs = {k: ctc.ContactSpec("separation", 0.0) for k in ctc.PAIR_ORDER}
    cs = ctc.build_contact_system(mesh, part, specs, order=2)
    basis, K, f_rot, f_eig = rs.assemble_plane_stress(cs.mesh, C, rho, None,
                                                     order=2)
    return K, f_rot * T.OMEGA ** 2 + f_eig, cs, basis


@pytest.fixture(scope="module")
def rings():
    return _two_rings()


def test_the_all_tied_iterate_is_not_reported_converged(rings):
    """B2 reproduced.  One contact iteration returns the warm start's answer:
    every pair closed, every pair in tension — the bonded rings.  Its gap
    residual is 31 nm, under the 56 nm acceptance margin of this Ø112 rotor,
    and before 2026-10-25 it came back ``converged=True`` with |u| 4.3 µm
    against the true 25.4 µm.  Measured as a force it is 100 % tension."""
    K, f, cs, basis = rings
    tied = ctc.solve_contact(K, f, cs, basis, max_iter=1)
    assert not tied.converged
    assert not tied.consistent
    assert tied.tension_frac == pytest.approx(1.0)
    assert tied.n_tensile_pairs > 0

    free = ctc.solve_contact(K, f, cs, basis, max_iter=30)
    assert free.converged and free.consistent
    assert free.tension_frac <= ctc.TENSION_FRAC_TOL
    # the two are different machines: the tied one is several times stiffer
    assert np.abs(free.u).max() > 3.0 * np.abs(tied.u).max()


# The 4-pole spoke rotor of tests/test_mechanical_runaway.py, pocket cut to the
# magnet, with a retaining iron bridge of ``bridge`` mm over each magnet top.
R_BORE, R_OD = 20.0, 50.0
MAG_W, MAG_R0 = 8.0, 26.0
N_POLES = 4


def _spoke(bridge: float = 4.0):
    ann = Point(0, 0).buffer(R_OD, resolution=128).difference(
        Point(0, 0).buffer(R_BORE, resolution=96))
    mags, rotor = [], ann
    for k in range(N_POLES):
        slab = Polygon([(MAG_R0, -MAG_W / 2), (R_OD - bridge, -MAG_W / 2),
                        (R_OD - bridge, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        s = affinity.rotate(slab, 360.0 * k / N_POLES, origin=(0, 0))
        mags.append((s, 1 if k % 2 == 0 else -1))
        rotor = rotor.difference(s)
    return {"rotor": rotor, "magnets": mags, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _solve_spoke(polys, rpm: float = 20000.0, mu: float = 0.2, **kw):
    kw.setdefault("symmetry", "full")
    return rs.solve_rotor_stress(
        polys, ASSIGN, rpm, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=kw.pop("mesh_size_mm", 2.0), order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", mu)},
        lift_off_solves=0, case_mode="single", loads="centrifugal", **kw)


def test_a_tied_separation_case_is_a_fail_with_no_sf(monkeypatch):
    """B2 on a rotor, in the state the eVTOL run returned it.

    Every contact solve — the one-shot, the spin-ramp rescue and the
    frictionless rescue — is held to ONE iteration at the FULL load (the ramp
    folded in), so each ends on its warm start's answer: every pair shut and
    pulling, i.e. the bonded rotor.  It is handed back with the set it was
    ASSEMBLED with (all shut), which is what the best-iterate restoration
    returns and what the report showed: 0 % open, 0 MPa contact pressure,
    tension across the joint.  Before 2026-10-25 that state passed the gap
    margin as converged and the case printed an SF.  Now the case is a FAIL:
    no SF field carries a number, the reason names the tension, and the
    numbers that were there are kept under ``sf_info``, labelled."""
    real = ctc.solve_contact

    def tied_state(K, f, cs, basis, **k):
        ramp = k.pop("f_ramp", None)
        if ramp is not None:
            f = f + ramp
        k.update(max_iter=1, load_steps=1, closed0=None)
        sol = real(K, f, cs, basis, **k)
        sol.closed = np.ones_like(sol.closed)
        sol.free_parts, sol.n_components = [], 1
        return sol

    monkeypatch.setattr(ctc, "solve_contact", tied_state)
    out = _solve_spoke(_spoke())
    case = out["cases"][out["primary_case"]]
    assert case["contact"]["consistent"] is False
    assert case["contact"]["converged"] is False
    mr = case["interfaces"]["magnet_rotor"]
    # the reported signature: shut, no pressure, tension in the element field
    assert mr["open_fraction"] == 0.0 and mr["pressure_max_mpa"] == 0.0
    assert mr["normal_max_mpa"] > 0.0
    assert case["retention"]["status"] == "fail"
    assert any("TIED" in r or "tension" in r for r in case["retention"]["reasons"])
    assert case["sf_min"] is None and case["sf_min_p05"] is None
    assert all(p["safety_factor"] is None for p in case["parts"].values())
    assert case["sf_info"]["sf_min"] is not None
    assert "INFO ONLY" in case["sf_info"]["label"]
    assert out["retention"]["status"] == "fail"
    assert rs.NOT_RETAINED in out["retention"]["message"]


def test_the_same_rotor_solved_properly_passes():
    """The counterpart: the same rotor, the contact loop left alone — the
    magnets press on their 4 mm bridges, the set settles and the SF stands."""
    out = _solve_spoke(_spoke())
    case = out["cases"][out["primary_case"]]
    assert case["contact"]["consistent"] is True
    assert case["retention"]["status"] == "pass"
    assert case["sf_min"] is not None and case["sf_min"] > 0.0
    assert out["retention"]["status"] == "pass"


def test_the_fail_rule_itself():
    """``retention_fail_reasons`` — every door, and the one that is not."""
    ok = SimpleNamespace(converged=True, consistent=True, residual=0.0,
                         tension_frac=0.0, n_tensile_pairs=0)
    sep = {"magnet_rotor": {"type": "separation", "n_facets": 40,
                            "open_fraction": 0.4, "pressure_max_mpa": 12.0,
                            "normal_max_mpa": 3.0}}
    held = {"verdict": "pocket bottom / shoulder (iron)"}
    assert rs.retention_fail_reasons(ok, sep, held, None, True) == []
    # no separation joint: nothing that could fail to retain
    bonded = {"magnet_rotor": {**sep["magnet_rotor"], "type": "bonded"}}
    tied = SimpleNamespace(**{**vars(ok), "consistent": False,
                              "converged": False, "tension_frac": 1.0,
                              "n_tensile_pairs": 9})
    assert rs.retention_fail_reasons(tied, bonded, held, None, True) == []
    # tied
    r = rs.retention_fail_reasons(tied, sep, held, None, True)
    assert len(r) == 1 and "TIED" in r[0] and "100 %" in r[0]
    # not converged
    nc = SimpleNamespace(**{**vars(ok), "converged": False, "residual": 2e-6})
    assert "did not converge" in rs.retention_fail_reasons(nc, sep, held,
                                                           None, True)[0]
    # the element-field cross-check: shut, no pressure, tension across it
    shut = {"magnet_rotor": {**sep["magnet_rotor"], "open_fraction": 0.0,
                             "pressure_max_mpa": 0.0, "normal_max_mpa": 4.2}}
    assert "tension across it" in rs.retention_fail_reasons(
        ok, shut, held, None, True)[0]
    # …but not at standstill, where a shut joint carrying nothing is normal
    assert rs.retention_fail_reasons(ok, shut, held, None, False) == []
    # the solver's own verdicts
    gone = {"verdict": "nothing — the magnets are not retained"}
    assert "not retained" in rs.retention_fail_reasons(ok, sep, gone, None,
                                                       True)[0]
    tp = {"held": False, "verdict": "poles held: no — every separation joint "
                                    "is open, nothing is clamped"}
    assert "poles held: no" in rs.retention_fail_reasons(ok, sep, held, tp,
                                                         True)[0]
    assert rs.retention_fail_reasons(ok, sep, held, {"held": None}, True) == []


def test_marking_a_fail_moves_every_sf_to_labelled_info():
    case = {"sf_min": 2.4, "sf_min_part": "rotor", "sf_min_p05": 5.0,
            "sf_min_unaveraged": 1.1,
            "sf_min_per_part": {"rotor": {"averaged": 2.4}},
            "parts": {"rotor": {"safety_factor": 2.4},
                      "magnet": {"safety_factor": 7.0}}}
    rs.mark_not_retained(case, ["x ran away"], "INFO ONLY — glued")
    assert case["sf_min"] is None and case["sf_min_p05"] is None
    assert case["sf_min_unaveraged"] is None and case["sf_min_per_part"] == {}
    assert {p["safety_factor"] for p in case["parts"].values()} == {None}
    assert case["sf_info"] == {
        "label": "INFO ONLY — glued", "sf_min": 2.4, "sf_min_unaveraged": 1.1,
        "sf_min_p05": 5.0, "sf_min_part": "rotor",
        "per_part": {"rotor": {"averaged": 2.4}},
        "parts": {"rotor": 2.4, "magnet": 7.0}}
    assert case["retention"]["status"] == "fail"
    assert rs.case_failed(case) and not rs.case_failed({"sf_min": 1.0})


# ---------------------------------------------------------------------------
# B3 — a runaway is a FAIL, the bonded solve only labelled information
# ---------------------------------------------------------------------------

def test_a_near_zero_bridge_runs_away_in_separation():
    """The synthetic B3 rotor: a 0.017 mm bridge over each magnet (below the
    mesher's 0.02 mm weld, so the magnet's top has nothing to press on) at
    1.2x of 20,000 rpm.  The separation solve refuses as a runaway — it never
    quietly answers with a bonded number."""
    with pytest.raises(rs.RotorRanAway) as ei:
        _solve_spoke(_spoke(0.017), rpm=24000.0)
    assert ei.value.pair == "magnet_rotor"


def test_the_route_fails_the_near_zero_bridge_and_keeps_bonded_as_info(monkeypatch):
    """B3 end to end through the route, on the synthetic rotor: the bonded
    re-solve runs, its SF is NOT the answer, the answer is
    "not retained — fails" for the SEPARATION request."""
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    from motor_ai_sim.routes import mechanical as rmech

    class _Motor:
        parameters = {"motor_length": 50.0, "num_poles": N_POLES}

    monkeypatch.setattr(rmech, "_live_polys",
                        lambda geo: (_spoke(0.017), _Motor(), None))
    monkeypatch.setattr(rmech, "_assignments", lambda: dict(ASSIGN))
    monkeypatch.setattr(rs, "cache_get", lambda key: None, raising=True)
    monkeypatch.setattr(rs, "cache_put", lambda key, out: None, raising=True)
    r = TestClient(app).get("/api/mechanical/rotor_stress", params={
        "cases": "single", "rpm": 24000, "loads": "centrifugal",
        "field": "false", "mesh_size_mm": 2.0, "order": 1,
        "lift_off_solves": 0, "symmetry": "full",
        "contacts": '{"magnet_rotor": {"type": "separation", "mu": 0.2}}'})
    assert r.status_code == 200, r.text[:600]
    d = r.json()
    assert d["retention"]["status"] == "fail"
    assert "not retained without adhesive" in d["retention"]["message"]
    assert d["contact_fallback"]["valid"] is False
    assert d["contacts"]["magnet_rotor"]["type"] == "separation"
    case = d["cases"][d["primary_case"]]
    assert case["sf_min"] is None
    assert all(p["safety_factor"] is None for p in case["parts"].values())
    # the glued number survives, as information and labelled as such
    assert case["sf_info"]["sf_min"] is not None
    assert "BONDED" in case["sf_info"]["label"]


# ---------------------------------------------------------------------------
# B1 — a sector carries the machine's part masses
# ---------------------------------------------------------------------------

def _ipm(n_poles: int = 56, r_in: float = 150.0, r_out: float = 160.0,
         r_hub: float = 120.0):
    """A synthetic many-pole IPM rotor: a thin steel ring on an aluminium hub,
    one rectangular magnet per pole in a pocket 92 % of its width, 1 mm of
    bridge to the air gap.  Steel ring r_in..r_out, hub r_hub..r_in."""
    ring = Point(0, 0).buffer(r_out, resolution=512).difference(
        Point(0, 0).buffer(r_in, resolution=512))
    hub = Point(0, 0).buffer(r_in, resolution=512).difference(
        Point(0, 0).buffer(r_hub, resolution=256))
    pitch = 2.0 * math.pi * r_out / n_poles
    w, t = 0.75 * pitch, 5.0
    mags, rotor = [], ring
    for k in range(n_poles):
        ang = 360.0 * k / n_poles
        pocket = affinity.rotate(box(r_out - 1.0 - t, -w / 2, r_out - 1.0, w / 2),
                                 ang, origin=(0, 0))
        mag = affinity.rotate(box(r_out - 1.0 - t, -0.46 * w, r_out - 1.0,
                                  0.46 * w), ang, origin=(0, 0))
        rotor = rotor.difference(pocket)
        mags.append((mag, 1 if k % 2 == 0 else -1))
    return {"rotor": rotor, "magnets": mags, "shaft": hub, "sleeve": None,
            "sleeve_r_mm": None}


def _masses(out):
    case = out["cases"][out["primary_case"]]
    return {k: v["mass_kg"] for k, v in case["parts"].items()}


def _assert_same_masses(full, sector, tol=5e-3):
    mf, ms = _masses(full), _masses(sector)
    assert set(mf) == set(ms)
    bad = [f"{k}: full {mf[k]:.4f} kg, sector {ms[k]:.4f} kg"
           for k in mf if abs(ms[k] - mf[k]) > tol * mf[k]]
    assert not bad, "; ".join(bad)
    # …and both are the DRAWN parts' masses
    for out in (full, sector):
        case = out["cases"][out["primary_case"]]
        for k, p in case["parts"].items():
            assert p["mass_kg"] == pytest.approx(p["mass_drawn_kg"], rel=tol), k
        assert out["mass_check"]["worst_rel"] <= rs.MASS_CHECK_TOL


def _solve_ipm(symmetry: str):
    return rs.solve_rotor_stress(
        _ipm(), ASSIGN, 1200.0, 1.2, 0.0, stack_length_mm=120.0,
        mesh_size_mm=4.0, order=1, with_field=False,
        contacts={"magnet_rotor": ctc.ContactSpec("separation", 0.2),
                  "shaft_rotor": ctc.ContactSpec("bonded", 0.0)},
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        symmetry=symmetry)


def test_b1_a_many_pole_ipm_sector_carries_the_machines_masses():
    """56 poles, Ø320 ring on an aluminium hub: sector and full give the same
    part masses to 0.5 %, and both equal the drawn cross-section's."""
    sector = _solve_ipm("sector")
    assert sector["symmetry"]["n_sectors"] == 56
    full = _solve_ipm("full")
    _assert_same_masses(full, sector)


def test_b1_the_small_reference_spoke_rotor_too():
    """The 4-pole spoke rotor (tests/test_mechanical_symmetry.py's family)."""
    full = _solve_spoke(_spoke(), symmetry="full")
    sector = _solve_spoke(_spoke(), symmetry="sector")
    assert sector["symmetry"]["n_sectors"] == N_POLES
    _assert_same_masses(full, sector)


def test_b1_a_sector_mesh_that_tags_magnets_as_iron_is_refused(monkeypatch):
    """The reported failure's signature, injected: the sector mesh comes back
    with 93 % of its magnet triangles tagged as rotor iron (the total area is
    untouched, which is exactly why it read as healthy).  Refused by name
    rather than reported as 12 kg of iron and half a kilo of magnets."""
    real = rs.build_rotor_mesh

    def mislabelled(polys, *a, **k):
        rm = real(polys, *a, **k)
        if k.get("periodic") is not None:
            mag = np.nonzero(rm.part_tri == ctc.PART_MAGNET)[0]
            rm.part_tri[mag[: int(0.93 * mag.size)]] = ctc.PART_ROTOR
        return rm

    monkeypatch.setattr(rs, "build_rotor_mesh", mislabelled)
    with pytest.raises(sym.NotPeriodic) as ei:
        _solve_spoke(_spoke(), symmetry="sector")
    msg = str(ei.value)
    assert "does not carry the drawn rotor parts" in msg and "magnet" in msg
    assert "symmetry='full'" in msg


def test_b1_a_wedge_that_is_not_one_nth_of_a_part_is_refused():
    """The clip itself: a wedge whose magnet came back a sliver (or whose
    pocket lost its hole) is not 1/n of the rotor."""
    polys = _ipm(n_poles=8, r_in=40.0, r_out=50.0, r_hub=30.0)
    plan = sym.plan_sector(polys)
    sec = sym.sector_polys(polys, plan)          # the honest wedge passes
    shrunk = dict(sec)
    shrunk["magnets"] = [(affinity.scale(m, 0.25, 0.25, origin="centroid"), p)
                         for m, p in sec["magnets"]]
    with pytest.raises(sym.NotPeriodic) as ei:
        sym._check_area_conservation(polys, shrunk, plan)
    assert "magnets" in str(ei.value) and "not 1/8" in str(ei.value)
    filled = dict(sec)
    filled["rotor"] = sec["rotor"].union(
        sec["magnets"][0][0].buffer(0.5))       # a pocket filled with iron
    with pytest.raises(sym.NotPeriodic):
        sym._check_area_conservation(polys, filled, plan)


def test_b1_the_drawn_areas_follow_the_meshers_precedence():
    """A magnet drawn ON TOP of a solid ring (no pocket hole, as some CAD
    imports deliver it) is counted once — as magnet — exactly as the mesher
    tags it."""
    ring = Point(0, 0).buffer(50.0).difference(Point(0, 0).buffer(30.0))
    mag = box(40.0, -2.0, 45.0, 2.0)
    a = rs._drawn_part_areas_mm2({"rotor": ring, "magnets": [(mag, 1)]})
    assert a["magnet"] == pytest.approx(20.0)
    assert a["rotor"] == pytest.approx(ring.area - 20.0)
