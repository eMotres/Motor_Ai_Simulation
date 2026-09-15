"""Rotor centrifugal stress solver — analytic checks and the route contract.

The first three tests are the ones that matter: a structural solver that is not
pinned to a closed form is a plausible-number generator.  They drive
``solve_plane_stress`` directly (no motor geometry, no materials library) so a
failure points at the elasticity, not at the plumbing.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical.rotor_stress import (
    SF_CLAMP, PartMech, element_safety_factor, isotropic_C, orthotropic_C,
    rotate_C, solve_plane_stress)

RPM = 20000.0
OMEGA = RPM * 2.0 * math.pi / 60.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _ring_mesh(ri: float, ro: float, h: float):
    """Annulus mesh in METRES (gmsh, free triangles)."""
    import gmsh
    from skfem import MeshTri

    from motor_ai_sim.simulation.sb_domains import _GMSH_LOCK

    _GMSH_LOCK.acquire()
    try:
        try:
            gmsh.initialize([], interruptible=False)
        except TypeError:
            gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.model.add("ring")
            occ = gmsh.model.occ
            o = occ.addDisk(0, 0, 0, ro, ro)
            i = occ.addDisk(0, 0, 0, ri, ri)
            occ.cut([(2, o)], [(2, i)])
            occ.synchronize()
            gmsh.option.setNumber("Mesh.MeshSizeMin", h * 0.6)
            gmsh.option.setNumber("Mesh.MeshSizeMax", h)
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 120)
            gmsh.model.mesh.generate(2)
            nt, nc, _ = gmsh.model.mesh.getNodes()
            et, _, en = gmsh.model.mesh.getElements(2)
            p = np.asarray(nc).reshape(-1, 3)[:, :2]
            order = np.argsort(np.asarray(nt, np.int64))
            m = np.zeros(int(np.max(nt)) + 1, np.int64)
            m[np.asarray(nt, np.int64)[order]] = np.arange(len(nt))
            p = p[order]
            t = np.vstack([m[np.asarray(e, np.int64)].reshape(-1, 3)
                           for ty, e in zip(et, en) if ty == 2])
        finally:
            gmsh.finalize()
    finally:
        _GMSH_LOCK.release()

    used = np.unique(t)
    rm = -np.ones(len(p), np.int64)
    rm[used] = np.arange(used.size)
    p, t = p[used], rm[t]
    v0, v1, v2 = p[t[:, 0]], p[t[:, 1]], p[t[:, 2]]
    a = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
         - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    t[a < 0] = t[a < 0][:, [0, 2, 1]]
    return MeshTri(np.ascontiguousarray(p.T), np.ascontiguousarray(t.T))


def _polar(mesh, sigma):
    """(r, sigma_rr, sigma_tt) at every element centroid."""
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    phi = np.arctan2(cen[:, 1], cen[:, 0])
    r = np.hypot(cen[:, 0], cen[:, 1])
    c, s = np.cos(phi), np.sin(phi)
    sxx, syy, sxy = sigma[:, 0], sigma[:, 1], sigma[:, 2]
    srr = sxx * c * c + 2 * sxy * c * s + syy * s * s
    stt = sxx * s * s - 2 * sxy * c * s + syy * c * c
    return r, srr, stt


# ---------------------------------------------------------------------------
# (a) rotating ring vs Lame
# ---------------------------------------------------------------------------

RI, RO, NU, RHO, E = 0.0246, 0.0621, 0.30, 7650.0, 200e9


def _lame_hoop(r):
    k = RHO * OMEGA ** 2 * (3 + NU) / 8
    return k * (RI ** 2 + RO ** 2 + RI ** 2 * RO ** 2 / r ** 2
                - (1 + 3 * NU) / (3 + NU) * r ** 2)


def _lame_radial(r):
    k = RHO * OMEGA ** 2 * (3 + NU) / 8
    return k * (RI ** 2 + RO ** 2 - RI ** 2 * RO ** 2 / r ** 2 - r ** 2)


@pytest.fixture(scope="module")
def ring_solution():
    mesh = _ring_mesh(RI, RO, 0.0012)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(isotropic_C(E, NU), (ne, 3, 3)).copy()
    sol = solve_plane_stress(mesh, C, np.full(ne, RHO), OMEGA, None, order=2)
    return mesh, sol


@pytest.mark.parametrize("where", ["inner", "mid", "outer"])
def test_rotating_ring_hoop_matches_lame(ring_solution, where):
    mesh, sol = ring_solution
    r, _srr, stt = _polar(mesh, sol.sigma_tri)
    band = {"inner": r < RI + 0.0015,
            "mid": np.abs(r - 0.5 * (RI + RO)) < 0.0012,
            "outer": r > RO - 0.0015}[where]
    assert band.sum() > 20
    fem = float(stt[band].mean())
    ana = float(_lame_hoop(r[band].mean()))
    assert abs(fem - ana) / abs(ana) < 0.02, f"{where}: FEM {fem:.3e} vs {ana:.3e}"


def test_rotating_ring_radial_matches_lame(ring_solution):
    mesh, sol = ring_solution
    r, srr, _stt = _polar(mesh, sol.sigma_tri)
    band = np.abs(r - 0.5 * (RI + RO)) < 0.001
    fem = float(srr[band].mean())
    ana = float(_lame_radial(r[band].mean()))
    assert abs(fem - ana) / abs(ana) < 0.02


def test_rotating_ring_free_surfaces_carry_no_radial_stress(ring_solution):
    """sigma_rr must die away at a free ID/OD — the check that the rigid-body
    handling did not quietly clamp the ring.

    Compared against Lame at the SAMPLED radius, not against zero: the outermost
    element centroids sit ~0.5 mm inside the surface, where the closed form is
    already ~1 MPa, so 'must be zero' would be testing the mesh, not the solve.
    """
    mesh, sol = ring_solution
    r, srr, _ = _polar(mesh, sol.sigma_tri)
    peak = float(np.abs(_lame_radial(0.5 * (RI + RO))))
    for band in (r < RI + 0.0008, r > RO - 0.0008):
        fem = float(srr[band].mean())
        ana = float(_lame_radial(r[band].mean()))
        assert abs(fem - ana) < 0.06 * peak, f"FEM {fem:.3e} vs Lame {ana:.3e}"
        assert abs(fem) < 0.12 * peak


def test_centrifugal_load_is_self_equilibrated(ring_solution):
    _mesh, sol = ring_solution
    assert sol.rigid_residual < 1e-6


# ---------------------------------------------------------------------------
# (b) thin ring: sigma_theta = rho w^2 r^2
# ---------------------------------------------------------------------------

def test_thin_sleeve_ring_matches_rho_omega2_r2():
    ri, ro, rho = 0.0621, 0.0631, 1580.0
    mesh = _ring_mesh(ri, ro, 0.0004)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(isotropic_C(165e9, 0.30), (ne, 3, 3)).copy()
    sol = solve_plane_stress(mesh, C, np.full(ne, rho), OMEGA, None, order=2)
    _r, _srr, stt = _polar(mesh, sol.sigma_tri)
    rm = 0.5 * (ri + ro)
    ana = rho * OMEGA ** 2 * rm ** 2
    assert abs(float(stt.mean()) - ana) / ana < 0.02


# ---------------------------------------------------------------------------
# (c) interference fit
# ---------------------------------------------------------------------------

def test_interference_puts_the_sleeve_in_hoop_tension_and_the_core_in_compression():
    """A sleeve shrunk onto a much stiffer core: at STANDSTILL the sleeve must
    be in hoop tension and the interface in radial COMPRESSION (the clamp)."""
    from motor_ai_sim.simulation.mechanical.rotor_stress import PART_SLEEVE

    r_if, r_od = 0.050, 0.052          # core 0..50, sleeve 50..52 mm
    mesh = _ring_mesh(0.020, r_od, 0.0012)
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    r = np.hypot(cen[:, 0], cen[:, 1])
    phi = np.arctan2(cen[:, 1], cen[:, 0])
    ne = mesh.t.shape[1]
    sleeve = r > r_if

    C = np.zeros((ne, 3, 3))
    C[~sleeve] = isotropic_C(200e9, 0.30)      # a stiff-ish core
    C[sleeve] = isotropic_C(165e9, 0.30)
    rho = np.where(sleeve, 1580.0, 7650.0)

    delta_r = 50e-6                            # 50 um radial oversize
    eps0 = np.zeros((ne, 3))
    e_t0 = -delta_r / (0.5 * (r_if + r_od))
    s, c = np.sin(phi[sleeve]), np.cos(phi[sleeve])
    eps0[sleeve, 0] = e_t0 * s * s
    eps0[sleeve, 1] = e_t0 * c * c
    eps0[sleeve, 2] = -2.0 * e_t0 * s * c

    sol = solve_plane_stress(mesh, C, rho, 0.0, eps0, order=2)
    _rr, srr, stt = _polar(mesh, sol.sigma_tri)

    assert float(stt[sleeve].mean()) > 0, "sleeve must be in hoop TENSION"
    iface = np.abs(r - r_if) < 0.0015
    assert float(srr[iface].mean()) < 0, "interface must be radially COMPRESSED"
    # And the pressure must be in the right ballpark: p ~ E_sleeve * eps_hoop * t/r
    p_est = 165e9 * (delta_r / r_if) * ((r_od - r_if) / r_if)
    assert 0.2 * p_est < abs(float(srr[iface].mean())) < 3.0 * p_est
    assert PART_SLEEVE == 2      # the tag the field payload colours by


def test_orthotropic_rotation_is_consistent():
    """A rotated ISOTROPIC matrix must come back unchanged — the check that the
    hoop-fibre rotation is not silently transposed."""
    C = isotropic_C(200e9, 0.3)
    ang = np.linspace(0, 2 * np.pi, 17)
    Cr = rotate_C(C, ang)
    assert np.allclose(Cr, C[None, :, :], rtol=1e-9, atol=1e-3)
    # An orthotropic card rotated by 90 deg must swap E1 and E2.
    Co = orthotropic_C(165e9, 9e9, 0.30, 5e9)
    C90 = rotate_C(Co, np.array([math.pi / 2]))[0]
    assert C90[0, 0] == pytest.approx(Co[1, 1], rel=1e-6)
    assert C90[1, 1] == pytest.approx(Co[0, 0], rel=1e-6)


# ---------------------------------------------------------------------------
# (c2) safety factor — one criterion per part, on hand-made elements
# ---------------------------------------------------------------------------

def _pm(**kw) -> PartMech:
    base = dict(part="rotor", material="X", density=7650.0, E=200e9, nu=0.30,
                strength=350e6, strength_kind="yield")
    base.update(kw)
    return PartMech(**base)      # type: ignore[arg-type]


def test_ductile_safety_factor_is_yield_over_von_mises():
    """Three elements with a known von Mises: SF must be the plain division,
    and an unstressed element must clamp instead of returning inf."""
    pm = _pm(strength=350e6, strength_kind="yield")
    vm = np.array([100e6, 350e6, 0.0])
    z = np.zeros(3)
    sf = element_safety_factor(pm, "rotor", vm, z, z, z, z)
    assert sf[0] == pytest.approx(3.5, rel=1e-12)
    assert sf[1] == pytest.approx(1.0, rel=1e-12)
    assert sf[2] == SF_CLAMP
    assert np.isfinite(sf).all()


def test_sleeve_safety_factor_is_fibre_strength_over_hoop():
    """The sleeve is checked on sigma_theta, NOT on von Mises: a hoop-wound
    laminate bursts along its fibres."""
    pm = _pm(part="sleeve", strength=2500e6, strength_kind="tensile",
             E_transverse=9e9, G=5e9)
    stt = np.array([500e6, 1250e6])
    # von Mises deliberately DIFFERENT, so a von-Mises criterion would fail here
    vm = np.array([9e9, 9e9])
    z = np.zeros(2)
    sf = element_safety_factor(pm, "sleeve", vm, z, z, z, stt)
    assert sf[0] == pytest.approx(5.0, rel=1e-12)
    assert sf[1] == pytest.approx(2.0, rel=1e-12)
    # a card that carries a transverse strength adds the radial term
    pm2 = _pm(part="sleeve", strength=2500e6, strength_kind="tensile",
              strength_transverse=50e6, E_transverse=9e9, G=5e9)
    srr = np.array([-25e6, 0.0])
    sf2 = element_safety_factor(pm2, "sleeve", vm, z, z, srr,
                                np.array([500e6, 500e6]))
    assert sf2[0] == pytest.approx(2.0, rel=1e-12)   # 50/25 beats 2500/500
    assert sf2[1] == pytest.approx(5.0, rel=1e-12)


def test_magnet_in_pure_compression_is_judged_on_its_compressive_strength():
    """A sintered magnet is ~12x stronger in compression than in tension, and a
    magnet squeezed by its sleeve carries NO tension at all.  The tensile term
    must drop out (not go negative, not clamp the element to 'safe') and the
    compressive one must decide."""
    pm = _pm(part="magnet", strength=80e6, strength_kind="tensile",
             compressive_strength=1000e6)
    # both principals compressive: sigma_1 = -10 MPa, sigma_2 = -500 MPa
    p1 = np.array([-10e6, 40e6])
    p2 = np.array([-500e6, -100e6])
    vm = np.array([490e6, 140e6])
    z = np.zeros(2)
    sf = element_safety_factor(pm, "magnet", vm, p1, p2, z, z)
    assert sf[0] == pytest.approx(1000e6 / 500e6, rel=1e-12)   # compression wins
    # the second element is in tension: 80/40 = 2.0 beats 1000/100 = 10
    assert sf[1] == pytest.approx(2.0, rel=1e-12)
    # and with no compressive strength on the card the term is skipped, not
    # invented: the compressed element then reads as unstressed.
    pm_nc = _pm(part="magnet", strength=80e6, strength_kind="tensile")
    sf_nc = element_safety_factor(pm_nc, "magnet", vm, p1, p2, z, z)
    assert sf_nc[0] == SF_CLAMP


# ---------------------------------------------------------------------------
# (d) + (e) the route
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def sandbox_result(client):
    # PINNED, not the live machine (2026-09-10).  This fixture used to solve
    # whatever the sandbox copy of the user's loaded motor happened to be, so
    # its numbers moved with his work: the pocket-opening test below went red
    # at 49 % on a rotor whose opening now equals the magnet width, on a claim
    # written for one with lips.  Same medicine as tests/test_mechanical_
    # contact.FROZEN_GEO, which is the geometry borrowed here — a sleeveless
    # copy of it, because the rest of this module is the no-sleeve path.
    import json as _json

    from tests.test_mechanical_contact import FROZEN_GEO

    geo = dict(FROZEN_GEO)
    geo["sleeve_thickness"] = 0.0
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8000, "overspeed_factor": 1.2,
                           "interference_mm": 0.0, "mesh_size_mm": 3.0,
                           "order": 1, "geo": _json.dumps(geo)})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_route_returns_the_expected_shape(sandbox_result):
    out = sandbox_result
    for k in ("rpm", "overspeed_rpm", "has_sleeve", "mesh", "materials",
              "cases", "lift_off_rpm", "field"):
        assert k in out, k
    assert set(out["cases"]) == {"standstill", "rated", "overspeed"}
    assert out["cases"]["rated"]["rpm"] == pytest.approx(8000.0)
    assert out["cases"]["overspeed"]["rpm"] == pytest.approx(9600.0)
    # The sandbox config has sleeve_thickness forced to 0 (see conftest), so a
    # sleeve must NOT appear — this is also the "no sleeve" path's smoke test.
    assert out["has_sleeve"] is False
    assert "sleeve" not in out["cases"]["rated"]["parts"]


def test_route_numbers_are_finite_and_physical(sandbox_result):
    rated = sandbox_result["cases"]["rated"]
    assert rated["parts"], "no parts solved"
    for name, part in rated["parts"].items():
        for k in ("von_mises_max_mpa", "principal_max_mpa", "hoop_max_mpa",
                  "radial_min_mpa", "strength_mpa"):
            v = part[k]
            assert isinstance(v, float) and math.isfinite(v), f"{name}.{k} = {v}"
        assert part["von_mises_max_mpa"] >= 0
        assert part["strength_mpa"] > 0
        assert part["von_mises_p995_mpa"] <= part["von_mises_max_mpa"] + 1e-6
    assert math.isfinite(rated["rotor_od_growth_um"])
    # 8 krpm on a small rotor: microns, not millimetres.  This is the assertion
    # that would have caught the rigid-body bug (it read 2e10 um).
    assert 0.0 < rated["rotor_od_growth_um"] < 1000.0
    assert sandbox_result["mesh"]["rigid_residual"] < 1e-3
    # standstill with no interference is the unloaded state
    st = sandbox_result["cases"]["standstill"]
    assert st["max_displacement_um"] == pytest.approx(0.0, abs=1e-9)


def test_the_outer_surface_travel_is_reported_as_its_own_number(sandbox_result):
    """User 2026-09-10: "нужно ещё считать максимальное радиальное смещение
    верха бандажа как отдельное число в таблице".

    The outermost ROTATING surface is what closes the mechanical clearance, so
    its travel is quoted on its own rather than left to be inferred from the
    displacement magnitude (which peaks wherever the magnets are, not on the
    surface that rubs).  The maximum IS ``rotor_od_growth_um`` — the same nodes,
    the same number — and the block adds the mean and the least of them, which
    is what separates the whole ring growing from one lobe being pushed out.
    """
    rated = sandbox_result["cases"]["rated"]
    g = rated["od_growth"]
    assert g["max_um"] == pytest.approx(rated["rotor_od_growth_um"], rel=1e-12)
    assert g["min_um"] <= g["mean_um"] <= g["max_um"]
    # Read on the outer surface, so its radius is the rotor's own — and this is
    # a sleeveless sandbox, so the surface belongs to the iron.
    assert g["part"] == "rotor"
    assert g["n_nodes"] > 0
    assert g["r_mm"] > 0.0
    # It cannot exceed the largest displacement anywhere: it is a radial
    # component read on a subset of the same nodes.
    assert g["max_um"] <= rated["max_displacement_um"] + 1e-9


def test_route_field_payload_is_consistent(sandbox_result):
    f = sandbox_result["field"]
    nv, nt = len(f["vertices"]), len(f["triangles"])
    assert nv > 100 and nt > 100
    assert len(f["domain_per_tri"]) == nt
    assert max(max(t) for t in f["triangles"]) < nv
    assert f["n_sectors"] == 1 and f["symmetry_mult"] == 1
    assert f["extent"] > 0 and f["outlines"]
    for case in ("standstill", "rated", "overspeed"):
        c = f["cases"][case]
        for k in ("vm_per_tri", "s_hoop_per_tri", "s_rad_per_tri",
                  "s_p1_per_tri", "sf_per_tri"):
            assert len(c[k]) == nt, f"{case}.{k}"
        # the SF map is never inf, never negative, and never a raw division
        assert all(0.0 < v <= 1e3 for v in c["sf_per_tri"]), case
        assert len(c["u_per_node"]) == nv
        assert len(c["u_mag_per_node"]) == nv
        assert all(len(u) == 2 for u in c["u_per_node"][:20])


def test_case_reports_the_min_safety_factor_and_its_percentile(sandbox_result):
    """Every case carries the raw min SF, the 5th percentile beside it and the
    part each belongs to — and the percentile can never be BELOW the raw min,
    which is the invariant that says the two are not swapped."""
    for case in ("standstill", "rated", "overspeed"):
        c = sandbox_result["cases"][case]
        per = c["sf_min_per_part"]
        assert per and set(per) == set(c["parts"]), case
        for name, row in per.items():
            assert 0.0 < row["min"] <= 1e3, f"{case}/{name}"
            assert row["p05"] >= row["min"] - 1e-9, f"{case}/{name}"
            assert row["criterion"], f"{case}/{name}"
        # THE safety factor is the AVERAGED one (2026-09-10): strength over
        # the governing nodal stress, which is the stress every table and map
        # prints beside it.  The element minimum is still solved and still
        # reported, one key over, as the singularity gauge.
        assert c["sf_min"] == pytest.approx(
            min(r["averaged"] for r in per.values()))
        assert c["sf_min_unaveraged"] == pytest.approx(
            min(r["min"] for r in per.values()))
        assert c["sf_min"] >= c["sf_min_unaveraged"] - 1e-9, case
        assert c["sf_min_p05"] == pytest.approx(min(r["p05"] for r in per.values()))
        assert c["sf_min_p05"] >= c["sf_min_unaveraged"] - 1e-9, case
        assert c["sf_min_part"] in per and c["sf_min_p05_part"] in per
    # a magnet is checked on BOTH principals, steel on von Mises — the criterion
    # sentence is what the map's tooltip quotes, so it has to name them
    mats = sandbox_result["materials"]
    assert "principal" in mats["magnet"]["sf_criterion"]
    assert "von Mises" in mats["rotor"]["sf_criterion"]
    # spinning costs safety factor: overspeed can never be safer than standstill
    over = sandbox_result["cases"]["overspeed"]["sf_min_p05"]
    still = sandbox_result["cases"]["standstill"]["sf_min_p05"]
    assert over <= still + 1e-9


@pytest.fixture(scope="module")
def sandbox_bonded(client):
    """The same machine with every interface BONDED — i.e. the v1 model.

    With no contact free to open and no interference, the problem is linear in
    omega^2 again, which is the only state in which the exact-scaling invariant
    below is a statement about the solver rather than about the contact.
    """
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8000, "overspeed_factor": 1.2,
                           "interference_mm": 0.0, "mesh_size_mm": 3.0,
                           "order": 1, "field": False, "lift_off_solves": 0,
                           "contacts": '{"magnet_rotor": {"type": "bonded"},'
                                       ' "sleeve_rotor": {"type": "bonded"},'
                                       ' "sleeve_magnet": {"type": "bonded"},'
                                       ' "shaft_rotor": {"type": "bonded"}}'})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_scaling_is_quadratic_in_speed_when_nothing_can_open(sandbox_bonded):
    """Overspeed / rated hoop stress must be exactly the speed ratio squared
    once every interface is bonded and there is no interference — the linear
    model v1 always was.

    With the DEFAULT separation contacts this is deliberately NOT asserted: each
    case is its own nonlinear solve, and a contact that changed state between
    two speeds is supposed to break the scaling.
    """
    rated = sandbox_bonded["cases"]["rated"]["parts"]
    over = sandbox_bonded["cases"]["overspeed"]["parts"]
    for name in rated:
        a = rated[name]["hoop_max_mpa"]
        b = over[name]["hoop_max_mpa"]
        if abs(a) > 1.0:
            assert b / a == pytest.approx(1.2 ** 2, rel=1e-6), name


def test_separation_opens_the_pockets_and_names_what_carries_the_magnets(
        sandbox_result):
    """The point of v2, on the sandbox machine (no sleeve — conftest zeroes it).

    With Separation contacts the magnet pockets open almost completely as soon
    as the rotor turns — a bonded model would have held the magnets by TENSION
    across those same faces, which no pocket does — and the report has to say
    which surface is left carrying them.
    """
    st = sandbox_result["cases"]["standstill"]["interfaces"]["magnet_rotor"]
    rated = sandbox_result["cases"]["rated"]
    # unloaded: g = 0 with p = 0 is a CLOSED joint, not an open one
    assert st["open_fraction"] == pytest.approx(0.0, abs=1e-9)
    assert rated["interfaces"]["magnet_rotor"]["open_fraction"] > 0.7
    assert rated["interfaces"]["magnet_rotor"]["pressure_min_mpa"] >= -1e-6
    ret = rated["magnet_retention"]
    assert ret["verdict"] and ret["magnet_centrifugal_kn_per_m"] > 0
    assert max(v for v in ret["share"].values() if v is not None) > 0.8
    # the shaft is BONDED, so it cannot open — but it can be pulled, and on a
    # rotor spinning this fast it is: that is the warning v1 gave too.
    assert sandbox_result["cases"]["rated"]["interfaces"]["shaft_rotor"][
        "open_fraction"] == 0.0


def test_interface_verdict_follows_the_contact_type(sandbox_result):
    """v2: the verdict is what the CONTACT did, not what a stress percentile
    suggests.

    A ``separation`` pair can actually open, so its verdict is geometric — half
    the arc gone — and its pressure can never be negative.  A ``bonded`` tie
    cannot open, so it keeps the v1 test: is it holding tension?
    """
    for case in ("standstill", "rated", "overspeed"):
        for name, i in sandbox_result["cases"][case]["interfaces"].items():
            if not i:
                continue
            assert i["normal_min_mpa"] <= i["normal_p95_mpa"] <= i["normal_max_mpa"] + 1e-9, name
            assert 0.0 <= i["open_fraction"] <= 1.0, name
            if i["type"] == "separation":
                assert i["pressure_min_mpa"] >= -1e-6, f"{case}/{name} pulled"
                assert i["lift_off"] == (i["open_fraction"] > 0.5), f"{case}/{name}"
            else:
                assert i["open_fraction"] == 0.0, f"{case}/{name}"
                assert i["lift_off"] == (i["normal_p95_mpa"] > 0.0), f"{case}/{name}"


def test_materials_route_reports_every_rotor_part(client):
    r = client.get("/api/mechanical/materials")
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert "parts" in out
    for part in ("rotor", "magnet", "shaft"):
        assert part in out["parts"], part
        row = out["parts"][part]
        assert "error" not in row, row
        assert row["youngs_modulus_gpa"] > 0
        assert 0 < row["poisson_ratio"] < 0.5
        assert row["strength_mpa"] > 0
        assert row["strength_kind"] in ("yield", "tensile")
    assert out["parts"]["magnet"]["strength_kind"] == "tensile"


def test_missing_mechanical_property_is_a_422_naming_it(client):
    """A material with no youngs_modulus_gpa must stop the solve and SAY SO,
    never fall back to a plausible default."""
    mat = ('{"assignment": {"rotor_core": "PhantomSteel"},'
           ' "materials": {"PhantomSteel": {"category": "steel",'
           ' "density": 7650, "sigma": 2000000}}}')
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 4000, "mesh_size_mm": 4.0, "order": 1,
                           "field": False, "mat": mat})
    assert r.status_code == 422, f"{r.status_code}: {r.text[:400]}"
    detail = r.json()["detail"]
    text = detail if isinstance(detail, str) else str(detail)
    assert "PhantomSteel" in text
    assert "youngs_modulus_gpa" in text


def test_interference_on_a_sleeveless_machine_is_rejected(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 4000, "interference_mm": 0.05,
                           "mesh_size_mm": 4.0, "order": 1, "field": False})
    assert r.status_code == 422, r.text[:300]
    assert "sleeve" in str(r.json()["detail"])


def test_zero_rpm_is_rejected_with_the_field_named(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 0, "mesh_size_mm": 4.0, "order": 1})
    assert r.status_code == 422
    bad = r.json()["detail"]["invalid_parameters"]
    assert bad and bad[0]["field"] == "rpm"


# ---------------------------------------------------------------------------
# (f) ONE speed instead of three
# ---------------------------------------------------------------------------
# User 2026-09-06: "давай будем рассчитывать только на 23 000 оборотов — всё,
# что ниже, всяко выдержит, и проще будет считать только одну величину".  The
# claim under test is not "it is faster" (that follows from solving one case
# instead of three) but that the ANSWER SHAPE does not change: one entry in
# `cases`, named by its speed, with the safety factors, the retention verdict
# and the lift-off search all computed on it.

@pytest.fixture(scope="module")
def single_case_result(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 23000, "mesh_size_mm": 3.0, "order": 1,
                           "cases": "single", "field": False})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_single_mode_solves_exactly_one_case_named_by_its_speed(single_case_result):
    out = single_case_result
    assert out["case_mode"] == "single"
    assert list(out["cases"]) == ["23,000 rpm"], list(out["cases"])
    assert out["primary_case"] == "23,000 rpm"
    c = out["cases"]["23,000 rpm"]
    assert c["rpm"] == pytest.approx(23000.0)
    # Nothing above the one solved speed may be QUOTED: no overspeed was run.
    assert out["overspeed_factor"] == pytest.approx(1.0)
    assert out["overspeed_rpm"] == pytest.approx(23000.0)


def test_single_mode_keeps_the_safety_factors_and_the_verdict(single_case_result):
    c = single_case_result["cases"]["23,000 rpm"]
    assert c["sf_min"] is not None and math.isfinite(c["sf_min"])
    assert c["sf_min_p05"] is not None and c["sf_min_p05"] >= c["sf_min"] - 1e-9
    assert c["sf_min_part"] in c["parts"]
    for name, s in c["sf_min_per_part"].items():
        assert 0.0 < s["min"] <= s["p05"] + 1e-9, name
        assert s["criterion"], name
    assert c["magnet_retention"]["verdict"]
    # The lift-off search still answers for every interface — `None` where the
    # joint has not opened up to this speed, never a made-up higher number.
    lo = single_case_result["lift_off_rpm"]
    assert lo, "no lift-off entries"
    for label, v in lo.items():
        assert v is None or (math.isfinite(v) and 0.0 <= v <= 23000.0 + 1e-6), \
            f"{label}: {v}"


def test_single_mode_is_a_different_cache_entry_than_three_cases():
    """A single-speed answer must never be served out of a three-case entry.

    The case table is part of the ANSWER (one column vs three), not a view of
    it, so it belongs in the key — this is the assertion that says so.
    """
    from motor_ai_sim.routes.mechanical import _cache_key
    from motor_ai_sim.simulation.mechanical.contact import DEFAULT_CONTACTS

    args = (None, {"rotor_core": "M270-35A"}, 23000.0, 1.2, 0.0, 3.0, 1,
            dict(DEFAULT_CONTACTS))
    assert _cache_key(*args, "single") != _cache_key(*args, "three")
    # …and the default is still the three-case key, so nothing that predates
    # the parameter changes its cache identity.
    assert _cache_key(*args) == _cache_key(*args, "three")


def test_an_unknown_case_table_is_a_422_naming_the_field(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 4.0, "order": 1,
                           "cases": "both"})
    assert r.status_code == 422, r.text[:300]
    bad = r.json()["detail"]["invalid_parameters"]
    assert bad and bad[0]["field"] == "cases"


# ---------------------------------------------------------------------------
# (g) the high-modulus sleeve cards
# ---------------------------------------------------------------------------
# User 2026-09-06: "у нас цель сделать бандаж как можно тоньше и чтобы он смог
# всё выдержать".  Three published-datasheet UD carbon laminates were added to
# config/materials_library.yaml beside T800_UD_60; this test is what says they
# still resolve as SLEEVE material — orthotropic, checked in tension, with the
# hoop modulus the card claims.

@pytest.mark.parametrize("name,e1,e2,g,strength,rho", [
    ("HM63_UD_60", 250.0, 8.0, 5.0, 2600.0, 1600.0),
    ("M40X_UD_60", 215.0, 8.0, 5.0, 2700.0, 1590.0),
    ("M55J_UD_60", 305.0, 7.0, 4.5, 1900.0, 1650.0),
])
def test_the_new_carbon_sleeves_resolve_with_their_datasheet_numbers(
        name, e1, e2, g, strength, rho):
    from motor_ai_sim.simulation.mechanical.rotor_stress import part_mech

    pm = part_mech("sleeve", name)
    assert pm.E / 1e9 == pytest.approx(e1)
    assert pm.E_transverse / 1e9 == pytest.approx(e2)
    assert pm.G / 1e9 == pytest.approx(g)
    assert pm.nu == pytest.approx(0.30)
    assert pm.density == pytest.approx(rho)
    # Brittle: a CFRP band bursts, it does not yield.
    assert pm.strength_kind == "tensile"
    assert pm.strength / 1e6 == pytest.approx(strength)
    # Hoop-wound = stiff along theta, matrix-soft radially: the solver rotates
    # this card per element, which it only does for an orthotropic one.
    assert pm.orthotropic
    assert pm.E > 10.0 * pm.E_transverse


def test_the_new_carbon_sleeves_are_offered_as_sleeve_materials():
    """They must be pickable where T800 is — the sleeve picker lists the
    `insulator` category, and the mechanical materials route resolves it."""
    from motor_ai_sim.materials import PART_CATEGORIES, all_insulators

    lib = all_insulators()
    assert "insulator" in PART_CATEGORIES["sleeve"]
    for n in ("T800_UD_60", "HM63_UD_60", "M40X_UD_60", "M55J_UD_60"):
        assert n in lib, n
        # The EM sleeve-loss model reads sigma; a more graphitised fibre is the
        # better conductor, which is the ordering the cards were scaled on.
        assert lib[n].sigma > 0, n
    assert (lib["M55J_UD_60"].sigma > lib["HM63_UD_60"].sigma
            > lib["M40X_UD_60"].sigma > lib["T800_UD_60"].sigma)
