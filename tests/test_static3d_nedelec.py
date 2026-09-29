"""The A-formulation on Nedelec edges — correctness, against closed forms.

This file exists because of one hypothesis and one lesson.

The hypothesis: the total scalar potential should lose accuracy in high-mu iron,
because it recovers B = -mu grad(phi) as a huge permeability times a tiny
gradient; a formulation whose unknown IS the flux density should not.  The iron
ladder here is what settles it, and it settles it against
``exact.sphere_in_shell_B`` — a closed-form solution that is exact at EVERY
mu_r, so neither an error that grows nor one that stays flat can be blamed on
the reference.

The lesson is 40b7de7: for a whole stage the 2D solver assembled the magnet
source as M instead of M/mu_rec, i.e. a magnet 5 % too strong, and every
benchmark in the suite passed anyway — because they were all written at
mu_r = 1, the single value where the two conventions agree.  So the first thing
tested here is the constitutive law at mu_r != 1, as a RATIO between two solves
on the same mesh where the discretisation error cancels.

Everything fast runs on coarse meshes and the whole default file is inside two
minutes; the ladder and the refinement study are behind ``STATIC3D_FULL=1``.
"""
from __future__ import annotations

import math
import os

import numpy as np
import pytest

from motor_ai_sim.simulation.static3d import exact
from motor_ai_sim.simulation.static3d.meshes import sphere_in_box, tube_in_box
from motor_ai_sim.simulation.static3d.nedelec import (azimuthal_J,
                                                      discrete_gradient,
                                                      project_source,
                                                      solve_static3d_A,
                                                      source_consistency,
                                                      tree_dofs)
from motor_ai_sim.simulation.static3d.solver import Region, solve_static3d

M_MAG = 1.0e6
R_SPH = 0.01
MU_REC = 1.05
FULL = os.environ.get("STATIC3D_FULL") == "1"
requires_full = pytest.mark.skipif(
    not FULL, reason="set STATIC3D_FULL=1 (minutes, big meshes)")


# --------------------------------------------------------------------------
# the exact solutions this file leans on — checked against the ones it does not
# --------------------------------------------------------------------------

def test_the_iron_shell_solution_reduces_to_the_plain_sphere():
    """At mu_r = 1 the shell is not there, and the four-region solution must
    collapse EXACTLY onto the one-line demagnetising formula.  A reference that
    cannot reproduce a simpler reference is not a reference."""
    a, b, c = 0.01, 0.013, 0.016
    for mu_m in (1.0, MU_REC):
        got = exact.sphere_in_shell_B_inside(M_MAG, a, b, c, 1.0, mu_m)
        want = exact.demag_body_B_inside(M_MAG, mu_m,
                                         exact.DEMAG_FACTOR["sphere"])
        assert got == pytest.approx(want, rel=1e-12), mu_m
    # ...and the field evaluator agrees with the scalar it returns, inside and
    # at a point in the outer air where the answer is a pure dipole again
    B = exact.sphere_in_shell_B(np.array([[0.0], [0.0], [0.0]]),
                                M_MAG, a, b, c, 1.0)
    assert B[2, 0] == pytest.approx(
        exact.sphere_in_shell_B_inside(M_MAG, a, b, c, 1.0), rel=1e-12)
    far = np.array([[0.0], [0.0], [0.06]])
    assert exact.sphere_in_shell_B(far, M_MAG, a, b, c, 1.0)[2, 0] == \
        pytest.approx(exact.sphere_B(far, M_MAG, a)[2, 0], rel=1e-12)


def test_the_iron_shell_shields_and_boosts_monotonically():
    """Physics the closed form must show: more permeable shell -> MORE field in
    the magnet (a better return path) and LESS outside it (shielding)."""
    a, b, c = 0.01, 0.013, 0.016
    ins = [exact.sphere_in_shell_B_inside(M_MAG, a, b, c, mu)
           for mu in (1.0, 10.0, 200.0, 4625.0)]
    outs = [abs(exact.sphere_in_shell_coeffs(M_MAG, a, b, c, mu)["B4"])
            for mu in (1.0, 10.0, 200.0, 4625.0)]
    assert all(x < y for x, y in zip(ins, ins[1:])), ins
    assert all(x > y for x, y in zip(outs, outs[1:])), outs


def test_a_thin_thick_solenoid_is_the_ideal_current_sheet():
    """``thick_solenoid_axis_Bz`` must collapse onto ``cylinder_axis_Bz`` as the
    wall thins, and do it at FIRST order in the thickness — that identity is what
    makes the coil benchmark a check on the solver rather than on the geometry."""
    z = np.linspace(0.0, 0.03, 9)
    R, L, K = 0.005, 0.02, 1.0e6
    ref = exact.cylinder_axis_Bz(z, K, R, L)
    scale = abs(ref[0])
    errs = []
    for t in (1e-4, 1e-5):
        got = exact.thick_solenoid_axis_Bz(z, K / t, R, R + t, L)
        errs.append(float(np.max(np.abs(got - ref)) / scale))
    assert errs[0] < 5e-3
    assert errs[1] == pytest.approx(0.1 * errs[0], rel=0.15), errs


# --------------------------------------------------------------------------
# the gauge machinery
# --------------------------------------------------------------------------

def test_the_discrete_gradient_really_is_the_null_space():
    """``curl(grad) = 0`` must hold for the DISCRETE operators to round-off, or
    every claim in this module about consistency and gauging is decoration."""
    from skfem import Basis, MeshTet, asm
    from skfem.element import ElementTetN0

    from motor_ai_sim.simulation.static3d.nedelec import _curlcurl

    m = MeshTet().refined(2)
    b = Basis(m, ElementTetN0())
    K = asm(_curlcurl, b, nu=np.ones((m.t.shape[1], b.dx.shape[1])))
    G, free = discrete_gradient(m)
    assert free.size == m.p.shape[1]
    g = G @ np.random.default_rng(0).normal(size=m.p.shape[1])
    assert np.linalg.norm(K @ g) / (abs(K).max() * np.linalg.norm(g)) < 1e-12
    assert float(np.abs(b.interpolate(g).curl).max()) < 1e-10


def test_the_tree_gauge_spans_exactly_the_free_vertices():
    """A spanning tree of the mesh graph with the Dirichlet vertices contracted
    has one edge per free vertex.  One too few leaves the system singular; one
    too many clamps a dof the physics owns."""
    from skfem import Basis, MeshTet
    from skfem.element import ElementTetN0

    m = MeshTet().refined(2)
    b = Basis(m, ElementTetN0())
    D = b.get_dofs().all()
    tree = tree_dofs(m, D)
    on_b = np.zeros(m.p.shape[1], dtype=bool)
    on_b[m.edges[0][D]] = True
    on_b[m.edges[1][D]] = True
    assert tree.size == int((~on_b).sum())
    assert np.intersect1d(tree, D).size == 0


def test_the_magnet_source_is_consistent_by_construction(sphere_mesh):
    """The magnet load is ``integral(Hc . curl v)`` and curl(grad) = 0, so its
    projection on the null space must be round-off — NOT small, zero.  That is
    what makes an ungauged CG legitimate for a magnet problem."""
    sol = solve_static3d_A(sphere_mesh.mesh, _regs(sphere_mesh), gauge="none")
    assert sol.source_residual < 1e-12, sol.source_residual


def test_a_current_source_is_not_consistent_until_it_is_projected():
    """...and a hand-built current density is NOT, because quadrature on a
    curved e_phi is not exact.  The projection has to fix it to round-off; the
    first version of it only reached 1.6e-4 and CG still did not converge."""
    from skfem import Basis, asm
    from skfem.element import ElementTetN0

    from motor_ai_sim.simulation.static3d.nedelec import _cursrc

    tm = tube_in_box(r_inner=0.005, r_outer=0.006, length=0.02,
                     box_factor=2.0, h_tube=0.003, optimize=False)
    b = Basis(tm.mesh, ElementTetN0())
    coil = tm.elements("coil")
    Jq = azimuthal_J(coil, tm.n_elements, 1.0e9)(
        np.asarray(b.global_coordinates()))
    f = asm(_cursrc, b, J=Jq)
    D = b.get_dofs().all()
    before = source_consistency(f, tm.mesh, D)
    after = source_consistency(project_source(f, tm.mesh, D), tm.mesh, D)
    assert before > 1e-4, before
    assert after < 1e-10, (before, after)


# --------------------------------------------------------------------------
# shared coarse fixtures
# --------------------------------------------------------------------------

def _regs(tm, mu_r=1.0, M=M_MAG):
    return [Region("air", tm.elements("air")),
            Region("magnet", tm.elements("magnet"), mu_r=mu_r, M=(0.0, 0.0, M))]


@pytest.fixture(scope="module")
def sphere_mesh():
    return sphere_in_box(radius=R_SPH, box_factor=3.0, h_magnet=0.004,
                         optimize=False)


# --------------------------------------------------------------------------
# the constitutive law — the 40b7de7 test, in the new formulation
# --------------------------------------------------------------------------

def test_the_recoil_permeability_enters_the_A_formulation_the_right_way(
        sphere_mesh):
    """mu_rec = 1.05 must LOWER the interior field to 2*mu0*M/(mu_rec+2).

    The A-formulation's magnet source is the equivalent coercivity M/mu_r.
    Assembling M instead models a magnet of remanence mu_rec*Br — 5 % strong —
    and would pass every mu_r = 1 benchmark in this repository.  So the sharp
    assertion is the RATIO of two solves on the same mesh, where the mesh error
    cancels and mu_rec is the only thing left; the absolute check is stated too,
    loosely, because it is the mesh that limits it.
    """
    N = exact.DEMAG_FACTOR["sphere"]
    B1 = exact.demag_body_B_inside(M_MAG, 1.0, N)
    Brec = exact.demag_body_B_inside(M_MAG, MU_REC, N)
    assert Brec < B1                       # the direction of the law, stated
    a = solve_static3d_A(sphere_mesh.mesh, _regs(sphere_mesh, 1.0))
    b = solve_static3d_A(sphere_mesh.mesh, _regs(sphere_mesh, MU_REC))
    ratio = b.region_mean_B("magnet")[2] / a.region_mean_B("magnet")[2]
    assert ratio == pytest.approx(Brec / B1, rel=5e-3), (
        f"mu_rec moved the interior field by {ratio:.5f}, the law says "
        f"{Brec / B1:.5f}; a factor {MU_REC:.3f} the other way means the source "
        "is being assembled as M rather than M/mu_r")
    assert abs(b.region_mean_B("magnet")[2] - Brec) / Brec < 0.08


def test_both_formulations_agree_on_the_sphere_at_mu_r_not_one(sphere_mesh):
    """The cross-check that matters for any three-way comparison later: the
    scalar potential and the A-formulation, handed the SAME ``Region`` list on
    the SAME mesh, must land on the same physics — within their own (different)
    discretisation errors, and on the SAME side of the exact answer as those
    errors predict."""
    N = exact.DEMAG_FACTOR["sphere"]
    Bex = exact.demag_body_B_inside(M_MAG, MU_REC, N)
    regs = _regs(sphere_mesh, MU_REC)
    bn = solve_static3d_A(sphere_mesh.mesh, regs).region_mean_B("magnet")[2]
    bp = solve_static3d(sphere_mesh.mesh, regs, order=2
                        ).region_mean_B("magnet")[2]
    assert abs(bn - bp) / Bex < 0.08
    # ...and the lowest-order edge element is the LESS accurate of the two on
    # the same mesh.  That is not a defect being tolerated, it is the measured
    # price of the formulation and it is what the Stage-B decision turns on.
    assert abs(bp - Bex) < abs(bn - Bex)


def test_the_gauge_choice_does_not_change_the_field(sphere_mesh):
    """Tree-cotree and the ungauged CG solve DIFFERENT linear systems whose
    solutions differ by a gradient.  B = curl(A) must not know that."""
    regs = _regs(sphere_mesh, MU_REC)
    bt = solve_static3d_A(sphere_mesh.mesh, regs, gauge="tree")
    bn = solve_static3d_A(sphere_mesh.mesh, regs, gauge="none")
    assert bt.region_mean_B("magnet")[2] == pytest.approx(
        bn.region_mean_B("magnet")[2], rel=1e-6)
    Bt, Bn = bt.B_elementwise(), bn.B_elementwise()
    scale = float(np.abs(Bt).max())
    assert float(np.abs(Bt - Bn).max()) / scale < 1e-5
    # the A vectors themselves must NOT agree — if they did, the two gauges
    # would not be two gauges and this test would be checking nothing
    assert np.linalg.norm(bt.A - bn.A) > 1e-3 * np.linalg.norm(bt.A)


def test_B_is_divergence_free_by_construction(sphere_mesh):
    """B = curl(A) satisfies div B = 0 identically, so the closed-surface flux
    is not a physics check here but a plumbing check on the recovery and the
    facet integration — and it must be at round-off, not merely small."""
    sol = solve_static3d_A(sphere_mesh.mesh, _regs(sphere_mesh))
    scale = exact.sphere_B_inside(M_MAG) * math.pi * R_SPH ** 2
    assert abs(sol.boundary_flux()) / scale < 1e-10


def test_no_region_may_be_left_without_material(sphere_mesh):
    with pytest.raises(ValueError, match="no Region"):
        solve_static3d_A(sphere_mesh.mesh,
                         [Region("magnet", sphere_mesh.elements("magnet"),
                                 M=(0.0, 0.0, M_MAG))])


# --------------------------------------------------------------------------
# the current-sheet identity
# --------------------------------------------------------------------------

def test_a_current_sheet_reproduces_the_magnet_it_replaces():
    """K = M x n: a cylindrical current sheet is the SAME source as a cylinder
    magnetised M*z_hat, and must give the same on-axis field.

    The current here is a genuine J on the edge right-hand side, in a shell of
    finite thickness.  Two references and they are not interchangeable: the
    exact field of the shell ACTUALLY meshed (solver error), and the ideal sheet
    it stands for (model error).  Both are asserted, separately.
    """
    from motor_ai_sim.simulation.static3d.benchmarks import nedelec_coil_case
    out = nedelec_coil_case(h=0.0022, verbose=False)
    assert out["source_residual"] > 1e-4          # it needed the projection
    assert out["source_residual_projected"] < 1e-9
    assert out["mid_err"] < 0.04, out
    assert out["l2"] < 0.06, out
    # the shell IS the ideal sheet to well inside the solver's own error, so the
    # identity is being tested and not a thickness correction
    assert out["sheet_vs_thick"] < 0.01, out
    assert abs(out["boundary_flux"]) < 1e-12


# --------------------------------------------------------------------------
# the iron ladder — the acceptance question
# --------------------------------------------------------------------------

@pytest.mark.skipif(not FULL, reason="set STATIC3D_FULL=1 (iron ladder)")
def test_neither_formulation_degrades_with_mu_r():
    """THE ladder.  Both formulations are measured against the exact
    sphere-in-shell field at mu_r = 1 .. 1e6, on one mesh.

    The claim under test was that the total scalar potential's error GROWS with
    mu_r (cancellation in B = -mu grad(phi)) while the A-formulation's stays
    flat.  Measured, neither grows: past mu_r ~ 200 every error is flat to
    within a few percent OF ITSELF, in the iron most of all — which is where the
    cancellation was supposed to show.  The bound below is deliberately loose
    (a factor 1.3 of spread) and is still an order of magnitude tighter than the
    growth the hypothesis needed.
    """
    from motor_ai_sim.simulation.static3d.benchmarks import iron_ladder
    rows = iron_ladder(mu_rs=(200.0, 1000.0, 4625.0, 1.0e6), h=0.0032,
                       verbose=False)
    for tag in ("N0", "P1", "P2"):
        rs = [r for r in rows if r["formulation"] == tag]
        for region in ("iron", "gap", "magnet"):
            e = [r[region] for r in rs]
            assert max(e) / min(e) < 1.3, (tag, region, e)
    # ...and on the same mesh the scalar P2 is the more accurate formulation in
    # the IRON, which is the region the hypothesis said it would be worst in
    for mu in (200.0, 4625.0):
        p2 = [r for r in rows if r["formulation"] == "P2" and r["mu_r"] == mu][0]
        n0 = [r for r in rows if r["formulation"] == "N0" and r["mu_r"] == mu][0]
        assert p2["iron"] < n0["iron"], (mu, p2["iron"], n0["iron"])


@requires_full
def test_the_edge_element_converges_on_the_sphere():
    """First order in B is what the lowest-order Nedelec element gives, and it
    is the whole cost of the formulation — so it is measured, not assumed."""
    from motor_ai_sim.simulation.static3d.benchmarks import (
        nedelec_sphere_convergence,
    )
    rows = nedelec_sphere_convergence((0.005, 0.0035, 0.0025), verbose=False)
    n0 = [r for r in rows if r["formulation"] == "N0"]
    rates = [r["rate"] for r in n0 if r.get("rate") is not None]
    assert min(rates) > 0.6, rates
    assert n0[-1]["l2"] < n0[0]["l2"]


def test_only_the_lowest_order_edge_element_exists_here():
    """``ElementTetN1`` is not a second-order Nedelec element in scikit-fem
    12.0.1 — it IS ``ElementTetN0``.  A Stage-B plan that budgets for the
    accuracy of the name would be planning on a class that is not there, so the
    probe reports the element's actual degree and this pins it.
    """
    from motor_ai_sim.simulation.static3d.benchmarks import nedelec_probe
    out = nedelec_probe(verbose=False)
    assert out["status"] == "usable", out["status"]
    assert out["edge_dofs"] == 1 and out["maxdeg"] == 1
    assert out["higher_order_edge"] is False
    assert out["ElementTetN1_is_a_distinct_class"] is False
