"""Stage-0 3D magnetostatics spike — correctness tests.

Everything here runs on deliberately COARSE meshes so the whole file stays
inside a minute, and the expensive solves are module-scoped fixtures so each one
is paid for once.  The tolerances are what those coarse meshes honestly achieve,
not what makes the bar green.  The refinement study that actually establishes
the convergence rates is expensive and lives behind ``STATIC3D_FULL=1`` — see
``simulation/static3d/benchmarks.py``, which is also runnable as a module.

The point of the file is that every physical assertion below is against a
CLOSED-FORM solution (uniformly magnetised sphere, finite-cylinder on-axis
field) or against an identity that holds exactly (div B = 0 over a closed
surface, exact reproduction of a polynomial by the point evaluator).  No test
here compares the solver to itself.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import exact
from motor_ai_sim.simulation.static3d.exact import MU0
from motor_ai_sim.simulation.static3d.meshes import (
    cylinder_in_box, cylinder_with_iron_ring, sphere_in_box,
)
from motor_ai_sim.simulation.static3d.solver import (
    Region, solve_static3d, solve_static3d_nonlinear,
)

M_MAG = 1.0e6                      # A/m -> mu0*M = 1.257 T
R_SPH = 0.01
MU_REC = 1.05                      # a real NdFeB recoil permeability
FULL = os.environ.get("STATIC3D_FULL") == "1"


# --------------------------------------------------------------------------
# shared coarse meshes and solves
# --------------------------------------------------------------------------

def _sphere_regions(tm, M=M_MAG):
    return [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=1.0, M=(0.0, 0.0, M))]


@pytest.fixture(scope="module")
def sphere_mesh():
    return sphere_in_box(radius=R_SPH, box_factor=3.0, h_magnet=0.004,
                         optimize=False)


@pytest.fixture(scope="module")
def sphere_p1(sphere_mesh):
    return solve_static3d(sphere_mesh.mesh, _sphere_regions(sphere_mesh),
                          order=1)


@pytest.fixture(scope="module")
def sphere_p2(sphere_mesh):
    return solve_static3d(sphere_mesh.mesh, _sphere_regions(sphere_mesh),
                          order=2)


@pytest.fixture(scope="module")
def cyl_mesh():
    return cylinder_in_box(radius=0.005, length=0.02, box_factor=2.5,
                           h_magnet=0.003, optimize=False)


@pytest.fixture(scope="module")
def cyl_p2(cyl_mesh):
    regs = [Region("air", cyl_mesh.elements("air")),
            Region("magnet", cyl_mesh.elements("magnet"), mu_r=1.0,
                   M=(0.0, 0.0, M_MAG))]
    return solve_static3d(cyl_mesh.mesh, regs, order=2)


@pytest.fixture(scope="module")
def ring_mesh():
    return cylinder_with_iron_ring(h_magnet=0.0032, box_factor=2.5,
                                   optimize=False)


# --------------------------------------------------------------------------
# exact solutions — internal consistency of the reference itself
# --------------------------------------------------------------------------

def test_exact_sphere_interior_is_two_thirds_mu0_M():
    assert exact.sphere_B_inside(M_MAG) == pytest.approx(
        2.0 / 3.0 * MU0 * M_MAG, rel=1e-12)
    Bin = exact.sphere_B(np.array([[0.0], [0.0], [0.005]]), M_MAG, R_SPH)[:, 0]
    assert Bin[2] == pytest.approx(exact.sphere_B_inside(M_MAG), rel=1e-12)
    # on the axis outside, the field must fall off as an exact 1/r^3 dipole
    z1, z2 = 0.05, 0.10
    b1 = exact.sphere_B(np.array([[0.0], [0.0], [z1]]), M_MAG, R_SPH)[2, 0]
    b2 = exact.sphere_B(np.array([[0.0], [0.0], [z2]]), M_MAG, R_SPH)[2, 0]
    assert b1 / b2 == pytest.approx((z2 / z1) ** 3, rel=1e-12)


def test_exact_cylinder_ratio_matches_the_profile_formula():
    R, L = 0.005, 0.02
    mid = exact.cylinder_axis_Bz_mid(M_MAG, R, L)
    end = exact.cylinder_axis_Bz_end(M_MAG, R, L)
    assert end / mid == pytest.approx(
        exact.cylinder_end_to_mid_ratio(R, L), rel=1e-12)
    # a long thin magnet loses exactly half its on-axis field at the end face
    assert exact.cylinder_end_to_mid_ratio(1e-4, 1.0) == pytest.approx(
        0.5, abs=1e-6)
    assert 0.0 < mid < MU0 * M_MAG


# --------------------------------------------------------------------------
# meshing
# --------------------------------------------------------------------------

def test_sphere_mesh_is_conformal_and_geometrically_right(sphere_mesh):
    tm = sphere_mesh
    assert set(tm.names) == {"air", "magnet"}
    assert tm.elements("magnet").size > 0 and tm.elements("air").size > 0
    assert tm.cell_region.size == tm.n_elements
    # the faceted sphere converges to the true volume from BELOW
    v = tm.region_volume("magnet")
    v_ex = 4.0 / 3.0 * math.pi * R_SPH ** 3
    assert 0.88 * v_ex < v <= v_ex * 1.001
    # CONFORMAL: the only boundary facets are on the outer box.  If the
    # magnet/air interface had duplicated nodes it would show up here as an
    # interior surface, and the M.grad(v) source would silently lose the
    # surface charge that IS the magnet.
    p = np.asarray(tm.mesh.p)
    bf = tm.mesh.boundary_facets()
    fc = p[:, tm.mesh.facets[:, bf]].mean(axis=1)
    L = tm.meta["box_half"]
    on_box = np.isclose(np.abs(fc), L, rtol=0, atol=1e-9 + 1e-6 * L).any(axis=0)
    assert on_box.all(), f"{int((~on_box).sum())} interior boundary facets"


def test_iron_ring_mesh_has_three_regions(ring_mesh):
    tm = ring_mesh
    assert set(tm.names) == {"air", "magnet", "iron"}
    for nm in ("air", "magnet", "iron"):
        assert tm.elements(nm).size > 0, nm
    v_ex = math.pi * (tm.meta["ring_outer"] ** 2 - tm.meta["ring_inner"] ** 2) \
        * tm.meta["ring_length"]
    assert tm.region_volume("iron") == pytest.approx(v_ex, rel=0.15)


# --------------------------------------------------------------------------
# the point evaluator — must reproduce polynomials it can represent EXACTLY
# --------------------------------------------------------------------------

@pytest.mark.parametrize("order", [1, 2])
def test_point_gradient_is_exact_for_representable_polynomials(order):
    """``B_at_points`` is the machinery every on-axis profile leans on.  A P1
    basis reproduces a linear field exactly and a P2 basis a quadratic one, so
    the gradient reported at an arbitrary interior point must match the analytic
    gradient to round-off.  This isolates the evaluator from any solver error —
    on a trivial unit-cube mesh, so it costs nothing."""
    from skfem import MeshTet
    m = MeshTet().refined(2)
    sol = solve_static3d(m, [Region("all", np.arange(m.t.shape[1]))], order=order)
    X = sol.basis.doflocs

    if order == 1:
        def f(x, y, z):
            return 2.0 * x - 3.0 * y + 0.5 * z + 7.0

        def gf(x, y, z):
            return np.array([np.full_like(x, 2.0), np.full_like(x, -3.0),
                             np.full_like(x, 0.5)])
    else:
        def f(x, y, z):
            return x * x + 2.0 * y * z - 3.0 * z * z + x - 4.0

        def gf(x, y, z):
            return np.array([2.0 * x + 1.0, 2.0 * z, 2.0 * y - 6.0 * z])

    sol.phi = f(X[0], X[1], X[2])
    rng = np.random.default_rng(4)
    pts = rng.uniform(0.05, 0.95, size=(3, 30))
    B = sol.B_at_points(pts)
    want = -MU0 * gf(pts[0], pts[1], pts[2])          # mu = mu0, M = 0
    assert np.allclose(B, want, rtol=1e-8, atol=1e-12 * MU0)


# --------------------------------------------------------------------------
# (a) sphere
# --------------------------------------------------------------------------

def test_sphere_interior_field_P1(sphere_p1):
    Bex = exact.sphere_B_inside(M_MAG)
    Bmean = sphere_p1.region_mean_B("magnet")
    assert abs(Bmean[2] - Bex) / Bex < 0.08
    assert abs(Bmean[0]) / Bex < 0.02 and abs(Bmean[1]) / Bex < 0.02


def test_sphere_interior_field_P2(sphere_p2):
    Bex = exact.sphere_B_inside(M_MAG)
    Bmean = sphere_p2.region_mean_B("magnet")
    assert abs(Bmean[2] - Bex) / Bex < 0.015
    assert abs(Bmean[0]) / Bex < 0.01 and abs(Bmean[1]) / Bex < 0.01
    # and pointwise at the centre, where the exact field is the same constant
    Bc = sphere_p2.B_at_points(np.zeros((3, 1)))[:, 0]
    assert abs(Bc[2] - Bex) / Bex < 0.015


@pytest.fixture(scope="module")
def sphere_p2_recoil(sphere_mesh):
    """The same sphere, but with a REAL magnet's recoil permeability."""
    return solve_static3d(
        sphere_mesh.mesh,
        [Region("air", sphere_mesh.elements("air")),
         Region("magnet", sphere_mesh.elements("magnet"), mu_r=MU_REC,
                M=(0.0, 0.0, M_MAG))],
        order=2)


def test_recoil_permeability_enters_the_constitutive_law(sphere_p2,
                                                         sphere_p2_recoil):
    """mu_rec = 1.05 must LOWER the interior field to 2*mu0*M/(mu_rec+2).

    This is the test Stage A did not have.  Every benchmark in this file ran at
    mu_r = 1, which is the one value where  B = mu0*mu_r*H + mu0*M  and
    B = mu0*mu_r*(H + M)  agree — so a solver using the second (remanence
    mu_rec*Br instead of Br, i.e. 5 % too strong a magnet) passed all of them.
    The 2D solver did exactly that, and it is what made the Stage A 3D-vs-2D
    check fail by a CONSTANT 4.7 % on the iron-free case.

    The assertion is deliberately made twice: once absolutely against the closed
    form, and once as a RATIO between the two solves on the SAME mesh, where the
    discretisation error cancels and the only thing left is mu_rec.
    """
    N = exact.DEMAG_FACTOR["sphere"]
    B1 = exact.demag_body_B_inside(M_MAG, 1.0, N)
    Br = exact.demag_body_B_inside(M_MAG, MU_REC, N)
    assert Br < B1                              # the law's direction, stated
    got = sphere_p2_recoil.region_mean_B("magnet")[2]
    # 2 % is what THIS coarse faceted sphere achieves (the mu_r = 1 twin above
    # sits at 1.5 % on the same mesh); the sharp assertion is the ratio below.
    assert abs(got - Br) / Br < 0.02
    # mesh-error-free form: the ratio of the two solves is 3/(mu_rec+2) exactly
    ratio = got / sphere_p2.region_mean_B("magnet")[2]
    assert ratio == pytest.approx(Br / B1, rel=2e-3), (
        f"mu_rec moved the interior field by {ratio:.5f}, the law says "
        f"{Br / B1:.5f}; a factor {MU_REC:.3f} the other way means the source "
        "is being read as mu0*mu_r*M instead of mu0*M")


def test_sphere_P2_beats_P1_on_the_same_mesh(sphere_mesh, sphere_p1, sphere_p2):
    """The whole P1-vs-P2 decision rests on this being true on a mesh a motor
    would plausibly use, not only asymptotically."""
    Bex = exact.sphere_B_inside(M_MAG)
    mag = sphere_mesh.elements("magnet")
    e = {}
    for order, sol in ((1, sphere_p1), (2, sphere_p2)):
        B, dx = sol.B_quadrature(mag)
        ref = np.zeros_like(B)
        ref[2] = Bex
        e[order] = math.sqrt(float((((B - ref) ** 2).sum(axis=0) * dx).sum())
                             / float(dx.sum())) / Bex
    assert e[2] < 0.6 * e[1], f"P1 {e[1]:.4f} vs P2 {e[2]:.4f}"


def test_sphere_exterior_is_the_exact_dipole(sphere_p2):
    """Outside a uniformly magnetised sphere the field is EXACTLY a point
    dipole — no higher multipoles — so this checks the exterior solution at
    three independent orientations.

    The tolerance is loose on purpose and the reason is worth recording: at
    1.5-2 magnet radii the AIR mesh has already graded coarse (the size field
    ramps away from the magnet surface), and that, not the element order, sets
    the exterior accuracy.  On this fixture P2 is ~11-17% out here while it is
    ~1% out inside the magnet.  Stage A has to keep the air-gap mesh fine, not
    just the steel."""
    pts = np.array([[0.0, 0.015, 0.0],
                    [0.0, 0.0, 0.015],
                    [0.02, 0.0, 0.0]])          # on axis, off axis, equatorial
    Bf = sphere_p2.B_at_points(pts)
    Be = exact.sphere_B(pts, M_MAG, R_SPH)
    rel = np.linalg.norm(Bf - Be, axis=0) / np.linalg.norm(Be, axis=0)
    assert (rel < 0.20).all(), rel
    # the sign and the dominant direction must still be right everywhere
    assert np.sign(Bf[2, 0]) == np.sign(Be[2, 0])
    assert np.sign(Bf[2, 1]) == np.sign(Be[2, 1])


def test_no_region_may_be_left_without_material(sphere_mesh):
    """Client-facing honesty: an unassigned element would silently be solved as
    air.  It has to raise instead."""
    with pytest.raises(ValueError, match="no Region"):
        solve_static3d(sphere_mesh.mesh,
                       [Region("magnet", sphere_mesh.elements("magnet"),
                               M=(0.0, 0.0, M_MAG))], order=1)


def test_linear_solve_conserves_flux_through_the_outer_surface(sphere_p1):
    flux = sphere_p1.boundary_flux()
    scale = exact.sphere_B_inside(M_MAG) * math.pi * R_SPH ** 2
    assert abs(flux) / scale < 1e-2, (flux, scale)


# --------------------------------------------------------------------------
# (b) cylinder end effect — the reason the whole spike exists
# --------------------------------------------------------------------------

def test_cylinder_on_axis_profile_and_end_spill(cyl_mesh, cyl_p2):
    R, L = cyl_mesh.meta["radius"], cyl_mesh.meta["length"]
    z = np.linspace(0.0, 1.2 * L, 25)
    pts = np.zeros((3, z.size))
    pts[2] = z
    Bz = cyl_p2.B_at_points(pts)[2]
    Bz_ex = exact.cylinder_axis_Bz(z, M_MAG, R, L)
    scale = abs(exact.cylinder_axis_Bz_mid(M_MAG, R, L))
    err = np.abs(Bz - Bz_ex) / scale
    assert err.max() < 0.05, f"worst on-axis error {err.max():.3%}"

    # the end spill itself — the number a 2D solve cannot produce at all
    mid = float(cyl_p2.B_at_points(np.zeros((3, 1)))[2, 0])
    end = float(cyl_p2.B_at_points(np.array([[0.0], [0.0], [0.5 * L]]))[2, 0])
    ratio_ex = exact.cylinder_end_to_mid_ratio(R, L)
    assert end / mid == pytest.approx(ratio_ex, rel=0.03)
    # and it must be a big effect, not a rounding difference: a 2D solve that
    # reports the mid-plane value everywhere is ~45% high at the end face
    assert 0.4 < ratio_ex < 0.75

    # the field must decay monotonically once outside the magnet
    zo = np.array([0.6, 0.9, 1.2]) * L
    po = np.zeros((3, 3))
    po[2] = zo
    assert (np.diff(cyl_p2.B_at_points(po)[2]) < 0).all()


# --------------------------------------------------------------------------
# (c) truncation
# --------------------------------------------------------------------------

def test_truncation_error_shrinks_and_the_bc_bracket_narrows():
    """A box too close to the magnet is the single easiest way to publish a
    confidently wrong 3D number.

    phi = 0 pulls flux into the wall and dphi/dn = 0 pushes it away, so the two
    boundary conditions bracket the truth.  The bracket narrowing with distance
    is a truncation error bar that needs no reference solution at all — the
    trick Stage A gets to reuse on the real motor, where no exact answer
    exists."""
    Bex = exact.sphere_B_inside(M_MAG)
    errs, spreads = [], []
    for bf in (1.6, 4.0):
        tm = sphere_in_box(radius=R_SPH, box_factor=bf, h_magnet=0.005,
                           optimize=False)
        regs = _sphere_regions(tm)
        bd = float(solve_static3d(tm.mesh, regs, order=1
                                  ).region_mean_B("magnet")[2])
        bn = float(solve_static3d(tm.mesh, regs, order=1, neumann_outer=True
                                  ).region_mean_B("magnet")[2])
        errs.append(abs(bd - Bex) / Bex)
        spreads.append(abs(bn - bd) / abs(bd))
    assert errs[0] > errs[1], errs
    assert errs[0] > 0.02, "a box at 1.6R should be visibly wrong"
    assert spreads[1] < 0.5 * spreads[0], spreads


# --------------------------------------------------------------------------
# (d) nonlinear iron
# --------------------------------------------------------------------------

def test_nonlinear_iron_picard_converges_saturates_and_conserves_flux(ring_mesh):
    from motor_ai_sim.materials import all_steels
    steels = all_steels()
    name = "20SW1200" if "20SW1200" in steels else sorted(steels)[0]
    bh = [tuple(p) for p in steels[name].bh_curve]
    assert len(bh) >= 2

    tm = ring_mesh
    regs = [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=1.0,
                   M=(0.0, 0.0, M_MAG)),
            Region("iron", tm.elements("iron"), mu_r=1000.0, bh_curve=bh)]
    sol = solve_static3d_nonlinear(tm.mesh, regs, order=1, tol=8e-3,
                                   max_iter=40, damping=0.4)

    assert sol.picard["converged"], sol.picard["history"]
    assert sol.picard["history"][-1] < sol.picard["history"][0]

    # the curve must actually have BITTEN.  A ring left on its initial slope
    # would pass a convergence-only test with the nonlinearity switched off.
    iron = tm.elements("iron")
    mur = sol.mu_el[iron] / MU0
    Biron = np.linalg.norm(sol.B_elementwise()[:, iron], axis=0)
    assert mur.min() >= 1.0
    assert mur.max() < 500.0, f"iron never saturated (mu_r up to {mur.max():.0f})"
    assert Biron.mean() > 1.4, f"ring not saturated ({Biron.mean():.2f} T)"

    # the real fixed-point check: the permeability the solve USED must be the
    # one the B-H curve prescribes for the B that came OUT of it.  Anything
    # less is a loop that stopped, not a loop that converged.
    from motor_ai_sim.simulation.field_ops import _mu_r_from_bh_vec
    mur_curve = np.maximum(_mu_r_from_bh_vec(bh, Biron), 1.0)
    assert np.allclose(mur, mur_curve, rtol=0.05), (
        float(np.max(np.abs(mur / mur_curve - 1.0))))

    # div B = 0 as a closed-surface integral: exact physics, no reference needed
    flux = sol.boundary_flux()
    scale = float(np.linalg.norm(sol.region_mean_B("magnet"))) \
        * math.pi * tm.meta["radius"] ** 2
    assert abs(flux) / scale < 2e-2, (flux, scale)


def test_picard_answer_does_not_depend_on_the_relaxation_path(ring_mesh):
    """Two very different starting damping factors must land on the SAME iron.
    A fixed point that moves when you change the step size is not a solution to
    the physics, it is a solution to the iteration."""
    from motor_ai_sim.materials import all_steels
    steels = all_steels()
    bh = [tuple(p) for p in
          steels["20SW1200" if "20SW1200" in steels else sorted(steels)[0]
                 ].bh_curve]
    tm = ring_mesh
    out = []
    for d in (0.5, 0.15):
        regs = [Region("air", tm.elements("air")),
                Region("magnet", tm.elements("magnet"), mu_r=1.0,
                       M=(0.0, 0.0, M_MAG)),
                Region("iron", tm.elements("iron"), mu_r=1000.0, bh_curve=bh)]
        s = solve_static3d_nonlinear(tm.mesh, regs, order=1, tol=8e-3,
                                     max_iter=40, damping=d)
        assert s.picard["converged"], (d, s.picard["history"])
        out.append(np.linalg.norm(
            s.B_elementwise()[:, tm.elements("iron")], axis=0).mean())
    assert out[0] == pytest.approx(out[1], rel=0.02), out


# --------------------------------------------------------------------------
# (e) Stage-B lookahead
# --------------------------------------------------------------------------

def test_nedelec_edge_elements_are_available():
    from motor_ai_sim.simulation.static3d.benchmarks import nedelec_probe
    out = nedelec_probe(verbose=False)
    assert out["status"] == "usable", out["status"]
    assert out["dofs_are_edges"] is True
    assert out["symmetric"] is True
    assert out["solved"] is True


# --------------------------------------------------------------------------
# the expensive refinement study — opt in
# --------------------------------------------------------------------------

@pytest.mark.skipif(not FULL, reason="set STATIC3D_FULL=1 (minutes, big meshes)")
def test_full_convergence_rates():
    from motor_ai_sim.simulation.static3d.benchmarks import (
        cylinder_convergence, sphere_convergence,
    )
    rows = sphere_convergence((0.004, 0.0028, 0.002, 0.0014), verbose=False)
    for order in (1, 2):
        rs = [r for r in rows if r["order"] == order]
        rates = [r["rate"] for r in rs if r["rate"] is not None]
        assert min(rates) > 0.8, (order, rates)
        assert rs[-1]["l2"] < rs[0]["l2"]
    cyl = cylinder_convergence((0.002, 0.0014, 0.001), orders=(2,),
                               verbose=False)
    assert min(r["ratio_err"] for r in cyl) < 0.01
