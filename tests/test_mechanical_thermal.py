"""Rotor TEMPERATURE — the thermal eigenstrain, and the fit it changes.

Added 2026-09-07 for the user's request: "нужно универсально добавить
температуру ротора, чтобы можно было задавать; для моторов без бандажа этот
эффект вообще минимальный".

Five checks, in the order they matter:

  (a) a FREE ring heated uniformly must come back STRESS-FREE and grown by
      exactly alpha*dT*r.  This is the whole eigenstrain formulation in one
      assertion: if the subtraction in ``recover_stress`` were missing, or the
      Voigt rotation transposed, this test would fail before any sleeve did.
  (b) a bonded two-ring shrink against the Lame closed form — the number.
  (c) the machine-level answer: a hot rotor under a cold band is a TIGHTER fit,
      and ``interference_effective_mm`` says by how much.
  (d) the route round-trips the two parameters and keys the cache on them.
  (e) the magnets' anisotropy: NdFeB grows +5 ppm/K along the magnetisation and
      CONTRACTS -1.5 ppm/K across it, and the solver applies that in the
      magnet's own frame.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs
from motor_ai_sim.simulation.mechanical.rotor_stress import (
    REF_TEMP_C, isotropic_C, part_mech, solve_plane_stress, thermal_eigenstrain)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mechanical_rotor_stress import _polar, _ring_mesh  # noqa: E402

ALPHA_FE = 12e-6          # 1/K — the library's electrical-steel figure
DT = 100.0                # K above the 20 C reference


def _hoop_angle(mesh) -> np.ndarray:
    """Per-element angle of the HOOP direction — material axis 1, phi + 90."""
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    return np.arctan2(cen[:, 1], cen[:, 0]) + 0.5 * math.pi


def _radial_u(mesh, u_node: np.ndarray) -> np.ndarray:
    ps = mesh.p.T
    r = np.hypot(ps[:, 0], ps[:, 1])
    return (u_node[:, 0] * ps[:, 0] + u_node[:, 1] * ps[:, 1]) / np.maximum(r, 1e-12)


# ---------------------------------------------------------------------------
# (a) a free ring, heated
# ---------------------------------------------------------------------------

RI_A, RO_A = 0.020, 0.050


@pytest.fixture(scope="module")
def heated_free_ring():
    mesh = _ring_mesh(RI_A, RO_A, 0.0015)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(isotropic_C(200e9, 0.30), (ne, 3, 3)).copy()
    eps0 = thermal_eigenstrain(ALPHA_FE, ALPHA_FE, DT, _hoop_angle(mesh))
    sol = solve_plane_stress(mesh, C, np.zeros(ne), 0.0, eps0, order=2)
    return mesh, sol


def test_a_free_heated_ring_carries_no_stress(heated_free_ring):
    """Nothing holds it, so nothing stresses it — the eigenstrain has to be
    subtracted back out in ``recover_stress`` for this to hold at all."""
    mesh, sol = heated_free_ring
    _r, srr, stt = _polar(mesh, sol.sigma_tri)
    assert float(np.abs(sol.sigma_tri).max()) < 1e6, "free thermal stress"
    assert float(np.abs(srr).max()) < 1e6
    assert float(np.abs(stt).max()) < 1e6


def test_a_free_heated_ring_grows_by_alpha_dt_r(heated_free_ring):
    mesh, sol = heated_free_ring
    ur = _radial_u(mesh, sol.u_node)
    ps = mesh.p.T
    r = np.hypot(ps[:, 0], ps[:, 1])
    for rad in (RI_A, RO_A):
        m = np.abs(r - rad) < 1e-6
        assert m.sum() > 10
        fem, ana = float(ur[m].mean()), ALPHA_FE * DT * rad
        assert abs(fem - ana) / ana < 0.01, f"r = {rad}: {fem:.4e} vs {ana:.4e}"


# ---------------------------------------------------------------------------
# (b) a bonded two-ring shrink vs the Lame closed form
# ---------------------------------------------------------------------------
# Steel annulus a..b heated by dT inside a ring b..c that stays at the
# reference.  The mismatch delta = alpha*dT*b is exactly the shrink-fit
# interference, so the classical plane-stress result applies:
#
#   delta = p*b/E_o * [(c^2+b^2)/(c^2-b^2) + nu_o]
#         + p*b/E_i * [(b^2+a^2)/(b^2-a^2) - nu_i]
#
# The OUTER ring is given the carbon fibre-direction modulus but as an ISOTROPIC
# card on purpose: the closed form is isotropic, and checking the solver against
# a formula that does not describe it would prove nothing.  The orthotropic
# sleeve is exercised by (c) and by the live-machine table.

A_B, B_B, C_B = 0.020, 0.050, 0.054
E_I, E_O, NU = 200e9, 165e9, 0.30


def _lame_shrink_pressure(delta: float) -> float:
    k_o = (C_B ** 2 + B_B ** 2) / (C_B ** 2 - B_B ** 2) + NU
    k_i = (B_B ** 2 + A_B ** 2) / (B_B ** 2 - A_B ** 2) - NU
    return delta / (B_B / E_O * k_o + B_B / E_I * k_i)


def test_b_two_ring_shrink_matches_lame():
    mesh = _ring_mesh(A_B, C_B, 0.0010)
    p = mesh.p.T
    cen = (p[mesh.t[0]] + p[mesh.t[1]] + p[mesh.t[2]]) / 3.0
    r = np.hypot(cen[:, 0], cen[:, 1])
    outer = r > B_B
    ne = mesh.t.shape[1]

    C = np.zeros((ne, 3, 3))
    C[~outer] = isotropic_C(E_I, NU)
    C[outer] = isotropic_C(E_O, NU)

    # dT on the INNER ring only: the outer one stays at the reference, which is
    # the sleeve's situation on a hot rotor.
    eps0 = np.zeros((ne, 3))
    eps0[~outer] = thermal_eigenstrain(ALPHA_FE, ALPHA_FE, DT,
                                       _hoop_angle(mesh))[~outer]

    sol = solve_plane_stress(mesh, C, np.zeros(ne), 0.0, eps0, order=2)
    rr, srr, stt = _polar(mesh, sol.sigma_tri)

    # The interface pressure, read off the INNER annulus rather than averaged
    # over a band across the joint.  sigma_rr varies steeply through the thin
    # outer ring (-p at the bore, 0 at the free OD), so a band mean is a mean of
    # the gradient, not the value — it reads ~9 % low and would have been
    # mistaken for a solver error.  Inside the annulus the field is EXACTLY
    # Lame, sigma_rr = A + B/r^2, so two coefficients fitted to the elements and
    # evaluated at r = b give the pressure with no extrapolation guesswork.
    inner = (rr > A_B + 0.002) & (rr < B_B - 0.001)
    assert inner.sum() > 50
    coef = np.linalg.lstsq(
        np.stack([np.ones(inner.sum()), 1.0 / rr[inner] ** 2], axis=1),
        srr[inner], rcond=None)[0]
    fem_p = -float(coef[0] + coef[1] / B_B ** 2)
    ana_p = _lame_shrink_pressure(ALPHA_FE * DT * B_B)
    assert fem_p > 0, "the heated core must PUSH on the ring"
    assert abs(fem_p - ana_p) / ana_p < 0.03, \
        f"interface pressure FEM {fem_p / 1e6:.3f} MPa vs Lame {ana_p / 1e6:.3f}"
    # …and the ring it pushes on must be in hoop TENSION.
    assert float(stt[outer].mean()) > 0


# ---------------------------------------------------------------------------
# (c) the machine: a hot rotor is a tighter fit
# ---------------------------------------------------------------------------
# A concentric steel annulus with a carbon band on it, no magnets and no shaft,
# so the only thing in the answer is the thermal fit.  Built from shapely
# directly rather than from the live config: this test is about the physics, and
# it must not move when the machine does.

R_BORE, R_OD, R_SLEEVE = 20.0, 50.0, 53.0        # mm
ASSIGN_C = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
            "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}
CONTACTS_C = {"sleeve_rotor": ctc.ContactSpec("separation", 0.2)}


def _band_polys():
    from shapely.geometry import Point

    disk = Point(0, 0).buffer(R_OD, resolution=96)
    rotor = disk.difference(Point(0, 0).buffer(R_BORE, resolution=64))
    sleeve = Point(0, 0).buffer(R_SLEEVE, resolution=96).difference(disk)
    return {"rotor": rotor, "sleeve": sleeve, "magnets": [],
            "sleeve_r_mm": (R_OD, R_SLEEVE)}


def _band_solve(rotor_c: float, sleeve_c: float):
    return rs.solve_rotor_stress(
        _band_polys(), ASSIGN_C, 0.0, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=1.5, order=2, with_field=False, contacts=CONTACTS_C,
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        rotor_temp_c=rotor_c, sleeve_temp_c=sleeve_c)


@pytest.fixture(scope="module")
def band_cold():
    return _band_solve(REF_TEMP_C, REF_TEMP_C)


@pytest.fixture(scope="module")
def band_hot():
    return _band_solve(120.0, REF_TEMP_C)


def test_c_no_interference_and_no_heat_leaves_the_sleeve_unstressed(band_cold):
    out = band_cold
    assert out["thermal"]["active"] is False
    assert out["interference_effective_mm"] == pytest.approx(0.0, abs=1e-9)
    sleeve = out["cases"][out["primary_case"]]["parts"]["sleeve"]
    assert abs(sleeve["hoop_max_mpa"]) < 1.0, sleeve["hoop_max_mpa"]


def test_c_a_hot_rotor_loads_the_band_and_tightens_the_fit(band_hot):
    out = band_hot
    th = out["thermal"]
    assert th["active"] is True
    assert th["rotor_temp_c"] == pytest.approx(120.0)
    assert th["sleeve_temp_c"] == pytest.approx(REF_TEMP_C)
    assert th["parts"]["rotor"]["thermal_strain_ppm"] == pytest.approx(
        ALPHA_FE * 100.0 * 1e6, rel=1e-9)

    sleeve = out["cases"][out["primary_case"]]["parts"]["sleeve"]
    assert sleeve["hoop_max_mpa"] > 1.0, "a heated rotor must stretch the band"

    # The number the user asked for: the fit AT TEMPERATURE.  With the band at
    # the reference its bore has not moved, so the whole of it is the iron's
    # free growth alpha_Fe * dT * r_o.
    eff = out["interference_effective_mm"]
    ana = ALPHA_FE * 100.0 * R_OD          # mm (alpha * dT * r_o in mm)
    assert abs(eff - ana) / ana < 0.05, f"effective {eff:.5f} mm vs {ana:.5f}"
    fit = th["fit"]
    assert fit["free_growth_sleeve_bore_um"] == pytest.approx(0.0, abs=1e-9)
    assert fit["free_growth_under_sleeve_um"] > 0


def test_c_a_hot_band_pulls_its_own_bore_IN():
    """The orthotropic sign check, and a finding worth keeping.

    Heat the band alone and its bore does NOT grow: along the fibres it barely
    moves (-0.5 ppm/K), while ACROSS them the matrix expands at 30 ppm/K, so a
    3 mm wall thickens by ~9 um and — the hoop holding the mean radius almost
    still — about half of that goes INWARD.  A hot band on a cold rotor is
    therefore a TIGHTER fit, not a looser one, which is the opposite of the
    isotropic intuition.  If the sleeve's two coefficients were ever swapped,
    this is the test that would catch it.
    """
    out = _band_solve(REF_TEMP_C, 120.0)
    fit = out["thermal"]["fit"]
    assert fit["free_growth_under_sleeve_um"] == pytest.approx(0.0, abs=1e-9)
    bore = fit["free_growth_sleeve_bore_um"]
    hoop_only = -0.5e-6 * 100.0 * (R_OD * 1e-3) * 1e6      # um, fibres alone
    assert bore < hoop_only, \
        f"bore moved {bore:.2f} um; the fibres alone would give {hoop_only:.2f}"
    assert -15.0 < bore < -2.0, bore
    assert out["interference_effective_mm"] > 0


#: A rotor steel card with everything the solve needs EXCEPT a CTE — the shape
#: of a `?mat=` override, and of any record written before 2026-09-07.
NO_CTE_STEEL = {"category": "steel", "density": 7600.0,
                "youngs_modulus_gpa": 200.0, "poisson_ratio": 0.30,
                "yield_strength_mpa": 400.0}


def test_c_a_material_with_no_cte_is_a_NOTE_not_a_422():
    """The rule the user's "универсально" turns on: a card that predates the
    field must still solve.  The part simply does not expand, and the answer
    says which one and why."""
    out = rs.solve_rotor_stress(
        _band_polys(), ASSIGN_C, 0.0, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=2.5, order=1, with_field=False, contacts=CONTACTS_C,
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        material_overrides={ASSIGN_C["rotor_core"]: NO_CTE_STEEL},
        rotor_temp_c=120.0, sleeve_temp_c=REF_TEMP_C)
    assert out["thermal"]["parts"]["rotor"]["cte_source"] == "missing"
    assert out["thermal"]["parts"]["rotor"]["thermal_strain_ppm"] == 0.0
    assert any("no thermal-expansion" in n for n in out["thermal_notes"]), \
        out["thermal_notes"]
    assert out["materials"]["rotor"]["cte_ppm_k_1"] is None
    # …and with nothing under the band expanding, the fit has not moved.
    assert out["interference_effective_mm"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# (d) the route
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


def test_d_route_round_trips_the_two_temperatures(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8000, "cases": "single",
                           "mesh_size_mm": 3.0, "order": 1, "field": False,
                           "rotor_temp_c": 150, "sleeve_temp_c": 80})
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    th = out["thermal"]
    assert th["rotor_temp_c"] == pytest.approx(150.0)
    assert th["sleeve_temp_c"] == pytest.approx(80.0)
    assert th["ref_temp_c"] == pytest.approx(20.0)
    assert "thermal_notes" in out
    # The sandbox machine has no sleeve, so the sleeve temperature must be
    # reported as having changed nothing rather than silently ignored.
    if not out["has_sleeve"]:
        assert any("no sleeve" in n for n in out["thermal_notes"]), out["thermal_notes"]
    # …and every part that WAS solved carries its strain.
    for name in out["cases"][out["primary_case"]]["parts"]:
        assert name in th["parts"], name
        assert "thermal_strain_ppm" in th["parts"][name]

    last = client.get("/api/mechanical/last", params={"field": False})
    assert last.status_code == 200
    p = last.json()["rotor_stress"]["params"]
    assert p["rotor_temp_c"] == pytest.approx(150.0)
    assert p["sleeve_temp_c"] == pytest.approx(80.0)


def test_d_the_temperatures_are_part_of_the_cache_key():
    from motor_ai_sim.routes.mechanical import _cache_key

    base = dict(geo_ov=None, assign={"rotor_core": "20SW1200"}, rpm=8000.0,
                osf=1.0, interf=0.0, mesh_mm=3.0, order=1,
                contacts={}, cases="single", loads="centrifugal", torque_nm=0.0)
    k20 = _cache_key(**base, rotor_temp_c=20.0, sleeve_temp_c=20.0)
    k150 = _cache_key(**base, rotor_temp_c=150.0, sleeve_temp_c=20.0)
    k80 = _cache_key(**base, rotor_temp_c=20.0, sleeve_temp_c=80.0)
    assert k20 != k150 and k20 != k80 and k150 != k80


def test_d_an_impossible_temperature_is_refused_by_the_field(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={"rpm": 8000, "rotor_temp_c": -400})
    assert r.status_code == 422, r.text[:300]


# ---------------------------------------------------------------------------
# (e) the magnets' anisotropy
# ---------------------------------------------------------------------------

def test_e_the_magnet_card_carries_both_coefficients():
    pm = part_mech("magnet", "F52SH_120C")
    assert pm.cte_source == "card"
    assert pm.cte_1 == pytest.approx(5e-6)
    assert pm.cte_pair()[1] == pytest.approx(-1.5e-6)
    assert pm.cte_anisotropic is True
    # …and the sleeve's, which is the opposite pairing: nothing along the
    # fibres, a lot across them.
    sl = part_mech("sleeve", "HM63_UD_60")
    assert sl.cte_1 == pytest.approx(-0.5e-6)
    assert sl.cte_pair()[1] == pytest.approx(30e-6)


@pytest.mark.parametrize("theta_deg", [0.0, 37.0, 90.0])
def test_e_a_free_magnet_block_strains_differently_along_and_across(theta_deg):
    """A free block heated with the magnet card: the strain ALONG the
    magnetisation and ACROSS it must come back as the two coefficients, in the
    block's own frame, whatever angle that frame sits at."""
    from skfem import MeshTri

    pm = part_mech("magnet", "F52SH_120C")
    a1, a2 = pm.cte_pair()
    theta = math.radians(theta_deg)

    mesh = MeshTri().refined(4)          # unit square, corners at (0,0)-(1,1)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(isotropic_C(pm.E, pm.nu), (ne, 3, 3)).copy()
    eps0 = thermal_eigenstrain(a1, a2, DT, np.full(ne, theta))
    sol = solve_plane_stress(mesh, C, np.zeros(ne), 0.0, eps0, order=2)

    # Free body: no stress, and the strain IS the eigenstrain.
    assert float(np.abs(sol.sigma_tri).max()) < 1e5

    ps = mesh.p.T
    e1 = np.array([math.cos(theta), math.sin(theta)])       # magnetisation axis
    e2 = np.array([-math.sin(theta), math.cos(theta)])
    for axis, alpha, name in ((e1, a1, "parallel"), (e2, a2, "perpendicular")):
        s = ps @ axis                    # coordinate along this axis
        d = sol.u_node @ axis            # displacement along it
        # Least-squares slope du/ds — the normal strain in that direction.
        slope = float(np.polyfit(s, d, 1)[0])
        assert slope == pytest.approx(alpha * DT, rel=1e-3), \
            f"{name}: {slope:.4e} vs {alpha * DT:.4e}"

    assert a1 * DT != pytest.approx(a2 * DT), "the point of the test"
