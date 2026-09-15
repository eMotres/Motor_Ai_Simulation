"""Modal analysis — closed-form checks, then the two routes.

Added 2026-09-05 for the user's request: "нам нужно сделать ещё модальный
анализ, чтобы понять все частоты — это очень важно для 20000 rpm".

The first five tests pin the two solvers to textbook answers on geometry the
motor config knows nothing about (a thin ring, a uniform beam).  That order is
deliberate and is the same one ``test_mechanical_rotor_stress`` uses: an
eigensolver that is not pinned to a closed form is a plausible-frequency
generator, and at 20 000 rpm a plausible frequency is worse than no frequency.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from motor_ai_sim.simulation.mechanical.modal import (
    assemble_K_M, bore_boundary_nodes, circumferential_order, eigen_modes,
    excitation_orders, nearest_excitation, outer_boundary_nodes)
from motor_ai_sim.simulation.mechanical.rotor_stress import isotropic_C
from motor_ai_sim.simulation.mechanical.rotordynamics import (
    assemble_beam, tube_section, whirl_frequencies)

E, NU, RHO = 200e9, 0.30, 7850.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _annulus(ri: float, ro: float, nr: int, nt: int):
    """Structured annulus MeshTri in METRES.

    Built by hand rather than through gmsh: a ring meshed to a closed form must
    have the same wall thickness everywhere, and a free triangulation of a 2 mm
    wall is not that.  It is also ~50x faster, and this fixture runs three
    times.
    """
    from skfem import MeshTri

    r = np.linspace(ri, ro, nr + 1)
    th = np.linspace(0.0, 2.0 * np.pi, nt, endpoint=False)
    P = np.stack([np.outer(r, np.cos(th)).ravel(),
                  np.outer(r, np.sin(th)).ravel()], axis=1)

    def idx(i: int, j: int) -> int:
        return i * nt + (j % nt)

    tri = []
    for i in range(nr):
        for j in range(nt):
            a, b, c, d = idx(i, j), idx(i, j + 1), idx(i + 1, j + 1), idx(i + 1, j)
            tri += [[a, b, c], [a, c, d]]
    t = np.asarray(tri, dtype=np.int64)
    v0, v1, v2 = P[t[:, 0]], P[t[:, 1]], P[t[:, 2]]
    a2 = ((v1[:, 0] - v0[:, 0]) * (v2[:, 1] - v0[:, 1])
          - (v2[:, 0] - v0[:, 0]) * (v1[:, 1] - v0[:, 1]))
    t[a2 < 0] = t[a2 < 0][:, [0, 2, 1]]
    return MeshTri(np.ascontiguousarray(P.T), np.ascontiguousarray(t.T))


#: mean radius and wall of the reference ring.  h/R = 1/25, thin enough for the
#: inextensional closed form below to be the truth and thick enough to mesh.
R_RING, H_RING = 0.050, 0.002


@pytest.fixture(scope="module")
def ring_modes():
    mesh = _annulus(R_RING - H_RING / 2, R_RING + H_RING / 2, 3, 160)
    ne = mesh.t.shape[1]
    C = np.broadcast_to(isotropic_C(E, NU), (ne, 3, 3)).copy()
    basis, K, M = assemble_K_M(mesh, C, np.full(ne, RHO), order=2)
    f, vecs, n_rigid = eigen_modes(K, M, 8, 3)
    return mesh, basis, f, vecs, n_rigid


def _ring_closed_form(n: int) -> float:
    """Love's in-plane (inextensional) bending frequency of a thin ring.

    f_n = n(n^2-1)/sqrt(n^2+1) * sqrt(EI/(rho A)) / (2 pi R^2), with I and A per
    unit axial length — which is exactly what a plane-stress 2-D model computes,
    so no width factor enters.  E and not E/(1-nu^2): the ring is narrow in the
    axial direction (plane STRESS), the same assumption the solver makes.
    """
    I = H_RING ** 3 / 12.0
    A = H_RING
    return (n * (n * n - 1) / math.sqrt(n * n + 1)
            * math.sqrt(E * I / (RHO * A)) / (2.0 * math.pi * R_RING ** 2))


# ---------------------------------------------------------------------------
# (a) thin ring in-plane bending modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,slot", [(2, 0), (3, 2), (4, 4)])
def test_ring_bending_modes_match_the_closed_form(ring_modes, n, slot):
    """Every in-plane ring mode is DOUBLE (a sine and a cosine pair at the same
    frequency), so mode n lives at index 2*(n-2) in the sorted list."""
    _mesh, _basis, f, _vecs, _nr = ring_modes
    want = _ring_closed_form(n)
    got = float(f[slot])
    assert abs(got - want) / want < 0.03, (
        f"n={n}: FE {got:.1f} Hz vs closed form {want:.1f} Hz")
    # the pair must be degenerate — if it is not, the mesh is not axisymmetric
    assert abs(f[slot + 1] - f[slot]) / f[slot] < 1e-3


# ---------------------------------------------------------------------------
# (c) the rigid-body modes are found and dropped
# ---------------------------------------------------------------------------

def test_free_free_drops_exactly_three_rigid_modes(ring_modes):
    """A free plane body has three zero-energy modes — two translations and a
    rotation.  Reporting one of them as a structural frequency is the classic
    shift-invert failure, so both halves are asserted: three were found, and
    nothing near zero survived into the answer."""
    _mesh, _basis, f, _vecs, n_rigid = ring_modes
    assert n_rigid == 3
    assert float(f[0]) > 1.0


# ---------------------------------------------------------------------------
# (e) the circumferential order counter
# ---------------------------------------------------------------------------

def test_first_elastic_ring_mode_is_order_two(ring_modes):
    """n = 2 is the ovalisation every ring machine hears first."""
    mesh, basis, _f, vecs, _nr = ring_modes
    pts_mm = mesh.p.T * 1e3
    ring = outer_boundary_nodes(pts_mm, mesh.t.T)
    assert ring.size > 20
    nd = basis.nodal_dofs
    u = np.stack([vecs[nd[0], 0], vecs[nd[1], 0]], axis=1)
    u = u / (np.abs(u).max() or 1.0)
    assert circumferential_order(pts_mm, u, ring) == 2


def test_the_bore_loop_reads_the_same_order_as_the_outer_one(ring_modes):
    """A pinned body is read on its bore instead of its held OD; the two
    boundaries must agree wherever both are free, or the pinned column of the
    table would not be comparable with the free one."""
    mesh, basis, _f, vecs, _nr = ring_modes
    pts_mm = mesh.p.T * 1e3
    bore = bore_boundary_nodes(pts_mm, mesh.t.T)
    outer = outer_boundary_nodes(pts_mm, mesh.t.T)
    assert bore.size > 20
    # the bore really is the inner loop
    assert np.hypot(pts_mm[bore, 0], pts_mm[bore, 1]).mean() < \
        np.hypot(pts_mm[outer, 0], pts_mm[outer, 1]).mean()
    nd = basis.nodal_dofs
    for i in (0, 2, 4):
        u = np.stack([vecs[nd[0], i], vecs[nd[1], i]], axis=1)
        u = u / (np.abs(u).max() or 1.0)
        assert circumferential_order(pts_mm, u, bore) == \
            circumferential_order(pts_mm, u, outer)


# ---------------------------------------------------------------------------
# (b) simply-supported uniform beam
# ---------------------------------------------------------------------------

def test_simply_supported_beam_first_bending_frequency():
    """f1 = pi^2/L^2 * sqrt(EI/(rho A)) / (2 pi).

    The supports are springs 8 orders stiffer than the beam rather than true
    pins, because that is all ``assemble_beam`` models — a bearing IS a spring.
    At that ratio the difference from a pin is far below the 2 % the closed form
    is asserted to.
    """
    L, OD = 1.0, 0.020
    sec = tube_section(OD, 0.0, E, NU, RHO)
    n_el = 28
    z = np.linspace(0.0, L, n_el + 1)
    k_pin = 1e11                       # ~1e8 x the beam's own EI/L^3
    M, K, G = assemble_beam(z, [sec] * n_el, [(0, k_pin), (n_el, k_pin)])
    f = whirl_frequencies(M, K, G, 0.0, 4)

    I = math.pi / 64.0 * OD ** 4
    A = math.pi / 4.0 * OD ** 2
    want = math.pi ** 2 / L ** 2 * math.sqrt(E * I / (RHO * A)) / (2.0 * math.pi)
    assert abs(float(f[0]) - want) / want < 0.02, (
        f"FE {f[0]:.3f} Hz vs closed form {want:.3f} Hz")
    # A lateral beam has an x-plane and a y-plane mode at the same frequency;
    # at zero speed there is no gyroscopic coupling to split them.
    assert abs(f[1] - f[0]) / f[0] < 1e-6


def test_gyroscopic_coupling_splits_the_whirl_at_speed():
    """The whole reason the answer is a Campbell diagram: spinning splits each
    mode into a backward branch that falls and a forward one that rises."""
    L, OD = 0.30, 0.030
    sec = tube_section(OD, 0.0, E, NU, RHO)
    n_el = 20
    z = np.linspace(0.0, L, n_el + 1)
    M, K, G = assemble_beam(z, [sec] * n_el, [(0, 2e8), (n_el, 2e8)])
    f0 = whirl_frequencies(M, K, G, 0.0, 4)
    f1 = whirl_frequencies(M, K, G, 20000.0 * 2 * math.pi / 60.0, 4)
    assert f1[0] < f0[0], "the backward branch must fall with speed"
    assert f1[1] > f0[1], "the forward branch must rise with speed"


# ---------------------------------------------------------------------------
# the excitation table
# ---------------------------------------------------------------------------

def test_excitation_orders_at_twenty_thousand_rpm():
    """The user's operating point.  10 poles, 12 slots, a 24 kHz carrier."""
    exc = {e["name"]: e["hz"] for e in
           excitation_orders(20000.0, 10, 12, 24000.0)}
    f_rot = 20000.0 / 60.0
    assert exc["rotation 1×"] == pytest.approx(f_rot)
    assert exc["rotation 2×"] == pytest.approx(2 * f_rot)
    # a Maxwell stress goes as B^2, so the force beats at 2*f_e, not at f_e
    assert exc["2·f_e"] == pytest.approx(2 * f_rot * 5)
    assert exc["slot passing"] == pytest.approx(4000.0, rel=1e-9)
    assert exc["slot passing 2×"] == pytest.approx(8000.0, rel=1e-9)
    assert exc["PWM carrier"] == pytest.approx(24000.0)
    assert exc["PWM carrier 2×"] == pytest.approx(48000.0)


def test_every_excitation_carries_the_slope_its_campbell_line_needs():
    """order = cycles per mechanical revolution.  The carrier's is None because
    an inverter switches at its own rate however fast the shaft turns."""
    by = {e["name"]: e for e in excitation_orders(20000.0, 10, 12, 24000.0)}
    assert by["rotation 1×"]["order"] == 1
    assert by["2·f_e"]["order"] == 10            # = number of poles
    assert by["slot passing"]["order"] == 12
    assert by["slot passing 2×"]["order"] == 24
    assert by["PWM carrier"]["order"] is None
    f_rot = 20000.0 / 60.0
    for e in by.values():
        if e["order"] is not None:
            assert e["hz"] == pytest.approx(e["order"] * f_rot)


def test_no_carrier_line_without_an_inverter():
    """No f_switch means no carrier line — not a made-up 20 kHz."""
    names = {e["name"] for e in excitation_orders(20000.0, 10, 12, None)}
    assert not any("PWM" in n for n in names)


def test_separation_margin_is_relative_to_the_excitation_and_flags_under_ten_pct():
    exc = excitation_orders(20000.0, 10, 12, 24000.0)
    near = nearest_excitation(4200.0, exc)          # 5 % above slot passing
    assert near["name"] == "slot passing"
    assert near["margin_pct"] == pytest.approx(5.0, rel=1e-9)
    assert near["flag"] is True
    # 15 % above slot passing — and still nearer to it (4 kHz) than to the
    # second order (8 kHz), which is what makes it the right "far" case
    far = nearest_excitation(4600.0, exc)
    assert far["name"] == "slot passing"
    assert far["flag"] is False
    assert far["margin_pct"] == pytest.approx(15.0, rel=1e-9)


# ---------------------------------------------------------------------------
# (d) the routes, on the sandbox config
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def rotor_modes(client):
    r = client.get("/api/mechanical/modes",
                   params={"body": "rotor", "n": 6, "support": "free",
                           "mesh_size_mm": 3.0, "order": 1, "shapes": True,
                           "rpm": 20000})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_rotor_modes_route_returns_finite_ordered_frequencies(rotor_modes):
    out = rotor_modes
    for k in ("body", "support", "modes", "excitations", "mesh", "materials",
              "assumptions", "field"):
        assert k in out, k
    assert out["body"] == "rotor"
    assert len(out["modes"]) == 6
    assert out["mesh"]["n_rigid_modes_dropped"] == 3
    f = [m["f_hz"] for m in out["modes"]]
    assert all(np.isfinite(f))
    assert f[0] > 1.0
    assert f == sorted(f)
    for m in out["modes"]:
        assert m["order"] is None or 0 <= m["order"] <= 40
        assert m["nearest"]["name"] in {e["name"] for e in out["excitations"]}
        assert np.isfinite(m["nearest"]["margin_pct"])


def test_rotor_mode_shapes_are_normalised_and_match_the_mesh(rotor_modes):
    fld = rotor_modes["field"]
    nv = len(fld["vertices"])
    assert len(fld["modes"]) == len(rotor_modes["modes"])
    for shape in fld["modes"]:
        assert len(shape) == nv
        u = np.asarray(shape)
        assert u.shape == (nv, 2)
        # normalised so the peak component is exactly 1 — the map exaggerates
        # from there, so a shape that is not normalised is a map with no scale
        assert np.abs(u).max() == pytest.approx(1.0, abs=1e-3)
    assert len(fld["domain_per_tri"]) == len(fld["triangles"])


def test_stator_modes_carry_the_winding_as_non_structural_mass(client):
    free = client.get("/api/mechanical/modes",
                      params={"body": "stator", "n": 4, "support": "free",
                              "mesh_size_mm": 3.5, "order": 1, "shapes": False,
                              "winding_mass": True, "rpm": 20000})
    assert free.status_code == 200, free.text[:600]
    a = free.json()
    assert a["winding_mass_kg_per_m"] > 0
    assert "non-structural mass" in a["assumptions"]

    bare = client.get("/api/mechanical/modes",
                      params={"body": "stator", "n": 4, "support": "free",
                              "mesh_size_mm": 3.5, "order": 1, "shapes": False,
                              "winding_mass": False, "rpm": 20000})
    assert bare.status_code == 200, bare.text[:600]
    b = bare.json()
    assert b["winding_mass_kg_per_m"] == 0
    # Mass without stiffness can only push a frequency DOWN.  That inequality is
    # the whole reason the copper is modelled as NSM rather than smeared into
    # soft elements, so it is the thing worth asserting.
    assert a["modes"][0]["f_hz"] < b["modes"][0]["f_hz"]


def test_pinning_the_stator_od_stiffens_it_and_leaves_one_rigid_mode(client):
    r = client.get("/api/mechanical/modes",
                   params={"body": "stator", "n": 4, "support": "pinned",
                           "mesh_size_mm": 3.5, "order": 1, "shapes": False,
                           "rpm": 20000})
    assert r.status_code == 200, r.text[:600]
    out = r.json()
    # A ring held RADIALLY can still spin rigidly inside its housing: exactly
    # one zero-energy mode, not three and not none.  Before the support was
    # built from the outer FACETS the P2 edge dofs stayed free and two extra
    # near-zero translation modes came back — a housing made of nothing.
    assert out["mesh"]["n_rigid_modes_dropped"] == 1
    assert out["mesh"]["n_pinned_locations"] > 0
    assert out["mesh"]["order_counted_on"] == "bore"
    assert out["modes"][0]["f_hz"] > 1.0

    free = client.get("/api/mechanical/modes",
                      params={"body": "stator", "n": 4, "support": "free",
                              "mesh_size_mm": 3.5, "order": 1, "shapes": False,
                              "rpm": 20000}).json()
    assert out["modes"][0]["f_hz"] > free["modes"][0]["f_hz"]


def test_pinning_the_rotor_is_refused_with_a_reason(client):
    """Nothing holds a rotor cross-section in its own plane."""
    r = client.get("/api/mechanical/modes",
                   params={"body": "rotor", "support": "pinned", "n": 4})
    assert r.status_code == 422
    assert "free-free" in r.json()["detail"]["error"]


def test_an_unknown_body_is_a_422(client):
    r = client.get("/api/mechanical/modes", params={"body": "housing", "n": 4})
    assert r.status_code == 422


def _frozen_geo() -> str:
    """The Ø200 the shaft-line tests were written for, as a ``geo=`` override.

    They used to solve whichever machine the user had open (the sandbox copies
    the live config).  On 2026-09-09 that was the G2-L40 — a Ø106 × 3 mm tube
    shaft under a 40 mm, 2.3 kg stack, whose first critical sits above the
    60 000 rpm sweep and whose 40 mm stack FITS a 40 mm span — and three of the
    four assertions below stopped being about the code.  The frozen Ø200 of
    tests/test_mechanical_contact.py (160 mm stack, Ø53 × 3 tube) has its first
    critical inside the sweep and runs off a 40 mm shaft, which is what these
    tests assert.
    """
    import json

    from tests.test_mechanical_contact import FROZEN_GEO
    return json.dumps(FROZEN_GEO)


@pytest.fixture(scope="module")
def criticals(client):
    r = client.get("/api/mechanical/critical_speeds",
                   params={"rpm": 20000, "bearing_span_mm": 250,
                           "stack_offset_mm": 0, "overhang_a_mm": 30,
                           "overhang_b_mm": 30, "bearing_k_n_per_m": 2e8,
                           "n_modes": 6, "mesh_size_mm": 4.0,
                           "geo": _frozen_geo()})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_critical_speeds_route_shape_and_physics(criticals):
    out = criticals
    for k in ("rated_rpm", "overspeed_rpm", "critical_speeds", "campbell",
              "verdict", "layout", "shaft", "stack", "inputs", "assumptions"):
        assert k in out, k
    assert out["rated_rpm"] == pytest.approx(20000.0)
    assert out["overspeed_rpm"] == pytest.approx(24000.0)
    assert out["critical_speeds"], "no critical speed found at all"
    for c in out["critical_speeds"]:
        assert c["whirl"] in ("forward", "backward")
        assert c["rpm"] > 0 and np.isfinite(c["rpm"])
        # a critical speed IS the crossing of the 1x line
        assert c["hz"] == pytest.approx(c["rpm"] / 60.0, rel=1e-6)
        assert c["excited_by_unbalance"] is (c["whirl"] == "forward")
    # every shaft-line dimension must be flagged as an assumption: none of it
    # comes from the motor config
    for k in ("bearing_span_mm", "overhang_a_mm", "bearing_k_n_per_m"):
        assert k in out["inputs"]["assumed"]


def test_campbell_branches_are_monotone_in_speed(criticals):
    cam = criticals["campbell"]
    rpms = np.asarray(cam["rpm"])
    bw = np.asarray(cam["backward"])
    fw = np.asarray(cam["forward"])
    assert rpms[0] == 0.0 and rpms[-1] > criticals["rated_rpm"]
    assert bw.shape == fw.shape
    # At STANDSTILL every whirl pair is one frequency: the two branch lists must
    # coincide exactly.  2026-09-09: the orbit sense at Ω = 0 is dust, and on
    # the G2-L40 it handed both copies of mode 1 to "backward" and both copies
    # of mode 2 to "forward" — bw 1161/1161, fw 2290/2290 — which no
    # "forward-above-backward" check can see; adjacency pairing at Ω = 0 is now
    # unconditional.
    assert np.allclose(fw[0], bw[0], rtol=1e-9, atol=1e-6), (fw[0], bw[0])
    for k in range(fw.shape[1]):
        # the forward branch stiffens and the backward one softens; a slip in
        # the sign of the gyroscopic matrix swaps exactly this
        assert fw[-1, k] >= fw[0, k] - 1e-9
        assert bw[-1, k] <= bw[0, k] + 1e-9
        # At standstill the two branches ARE the same frequency, so the two
        # curves start on top of each other to within the eigensolver's noise.
        assert np.all(fw[:, k] - bw[:, k] >= -1e-6 * np.maximum(fw[:, k], 1.0))


def test_a_stiffer_bearing_raises_the_first_critical(client):
    def first_forward(k: float) -> float:
        r = client.get("/api/mechanical/critical_speeds",
                       params={"rpm": 20000, "bearing_span_mm": 250,
                               "bearing_k_n_per_m": k, "n_modes": 4,
                               "mesh_size_mm": 4.0, "geo": _frozen_geo()})
        assert r.status_code == 200, r.text[:400]
        fwd = [c["rpm"] for c in r.json()["critical_speeds"]
               if c["whirl"] == "forward"]
        assert fwd
        return fwd[0]

    soft, stiff = first_forward(5e7), first_forward(1e9)
    assert stiff > soft, (soft, stiff)


def test_a_shaft_line_that_cannot_exist_names_the_number(client):
    """The client-facing validation rule: never solve a machine nobody built."""
    r = client.get("/api/mechanical/critical_speeds",
                   params={"rpm": 20000, "bearing_span_mm": 40,
                           "overhang_a_mm": 0, "overhang_b_mm": 0,
                           "n_modes": 4, "mesh_size_mm": 4.0,
                           "geo": _frozen_geo()})
    assert r.status_code == 422
    d = r.json()["detail"]
    assert "runs off" in d["error"]
    assert d["invalid_parameters"][0]["kind"] == "impossible_shaft_line"


def test_zero_rpm_is_rejected_with_the_field_named(client):
    r = client.get("/api/mechanical/critical_speeds",
                   params={"rpm": 0, "bearing_span_mm": 250})
    assert r.status_code == 422
    assert r.json()["detail"]["invalid_parameters"][0]["field"] == "rpm"


def test_both_modal_routes_are_tier_gated(client):
    """They are the same class of compute as the field solves, so they ride the
    same tier — an entry missing from `_GATED` is a free eigensolve."""
    from motor_ai_sim.auth import _GATED

    assert _GATED[("GET", "/api/mechanical/modes")] == "pro"
    assert _GATED[("GET", "/api/mechanical/critical_speeds")] == "pro"
