"""Force resolution and unloaded free bodies, without a Gmsh dependency."""
import numpy as np
import pytest
from scipy.sparse.linalg import spsolve
from skfem import MeshTri

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs


@pytest.fixture(params=["superlu", "pypardiso"])
def linear_solver(request):
    if request.param == "superlu":
        return spsolve
    name, solve = ctc._solver()
    if name != "pypardiso":
        pytest.skip("PARDISO unavailable or its threading layer is unsafe")
    return solve


def _blocks(stiffness=1.0, strain=1e-3, ny=7):
    mesh = MeshTri.init_tensor(np.linspace(.02, .04, 7),
                               np.linspace(-.01, .01, ny))
    part = np.where(mesh.p[:, mesh.t].mean(axis=1)[0] < .03,
                    ctc.PART_ROTOR, ctc.PART_MAGNET)
    cs = ctc.build_contact_system(
        mesh, part, {"magnet_rotor": ctc.ContactSpec("separation", 0)}, order=1)
    ne = cs.mesh.t.shape[1]
    C = np.broadcast_to(rs.isotropic_C(2e11 * stiffness, .3), (ne, 3, 3)).copy()
    eps = np.zeros((ne, 3))
    eps[part == ctc.PART_ROTOR, :2] = strain
    basis, K, f_rot, f_eig = rs.assemble_plane_stress(
        cs.mesh, C, np.full(ne, 7600.), eps, order=1)
    return cs, basis, K, f_rot, f_eig


@pytest.mark.parametrize("stiffness,strain", [(1e-3, 1e-3), (1., 1e-3),
                                              (1e3, 1e-3), (1., 1e-5)])
def test_free_thermal_body_has_no_runaway_or_tied_verdict(linear_solver,
                                                         stiffness, strain):
    cs, basis, K, _, f = _blocks(stiffness, strain)
    sol = ctc.solve_contact(K, f, cs, basis, solve_fn=linear_solver)
    assert sol.free_parts == []
    assert sol.seated == []
    assert sol.seating_capped == []
    assert sol.consistent and sol.converged
    # A geometry-only free-component count must not be interpreted as runaway.
    assert rs.runaway_verdict(float(np.abs(sol.u).max()), .05,
                              sol.free_parts, None, {}) is None


def test_a_real_tensile_load_path_remains_a_fail(linear_solver):
    cs, basis, K, f_rot, _ = _blocks(strain=0.)
    f = f_rot * 1000.**2
    tied = ctc.solve_contact(K, f, cs, basis, solve_fn=linear_solver, max_iter=1)
    assert not tied.consistent
    assert not tied.converged
    assert tied.tension_frac > ctc.TENSION_FRAC_TOL
    loose = ctc.solve_contact(K, f, cs, basis, solve_fn=linear_solver)
    assert "magnet" in loose.free_parts
    assert rs.runaway_verdict(float(np.abs(loose.u).max()), .05,
                              loose.free_parts, None, {}) is not None


def test_roundoff_tension_is_resolved_against_the_elastic_scale(linear_solver):
    cs, basis, K, _, _ = _blocks(strain=0.)
    # Compatible expansion of BOTH blocks: zero exact contact traction.
    # Perturb the solved trace by 0.2 pm, above the active-set gap tolerance,
    # to reproduce backend-dependent cancellation without relying on its sign.
    dofs = np.arange(basis.N)
    expansion = 1e-3 * basis.doflocs[dofs % 2, dofs]
    def noisy_solve(A, rhs):
        x = np.asarray(linear_solver(A, rhs)).copy()
        x[cs.pair_dofs[:, 2:4]] += 2e-13 * cs.pair_n
        return x

    sol = ctc.solve_contact(K, K @ expansion, cs, basis,
                             solve_fn=noisy_solve, max_iter=1)
    assert sol.tension_frac > ctc.TENSION_FRAC_TOL
    assert sol.tension_n_per_m > 0.
    assert sol.consistent and sol.converged


def test_an_unloaded_disconnected_body_is_not_a_runaway(linear_solver):
    cs, basis, K, _, _ = _blocks(strain=0.)
    cs.pair_g0[:] = 1e-5
    sol = ctc.solve_contact(K, np.zeros(basis.N), cs, basis,
                             closed0=np.zeros(cs.n_pairs, dtype=bool),
                             solve_fn=linear_solver)
    assert sol.n_components > 1
    assert sol.free_parts == []
    assert sol.consistent and sol.converged


def test_a_disconnected_body_under_a_pure_couple_is_still_refused(linear_solver):
    cs, basis, K, _, _ = _blocks(strain=0.)
    cs.pair_g0[:] = 1e-5
    body = ctc._part_bodies(cs)
    body_dof = ctc._dof_components(basis, body, cs.mesh)
    magnet = body[cs.mesh.t[:, np.flatnonzero(cs.part_tri == ctc.PART_MAGNET)[0]][0]]
    dy = np.arange(1, basis.N, 2)
    dy = dy[body_dof[dy] == magnet]
    f = np.zeros(basis.N)
    f[dy[np.argmin(basis.doflocs[0, dy])]] = -1e3
    f[dy[np.argmax(basis.doflocs[0, dy])]] = 1e3
    sol = ctc.solve_contact(K, f, cs, basis,
                             closed0=np.zeros(cs.n_pairs, dtype=bool),
                             solve_fn=linear_solver)
    assert "magnet" in sol.free_parts


@pytest.mark.parametrize("n_pairs", [8, 400])
def test_tied_pairs_cannot_borrow_resolution_from_bulk_dofs(n_pairs):
    # A direct algebraic contact mesh: each pair pulls at 100 N/m, against
    # 90 N/m compression. The rest of the solid can grow arbitrarily without
    # changing either contact trace's cancellation error.
    pair_dofs = np.arange(4 * n_pairs).reshape(-1, 4)
    rows = np.full(4 * n_pairs, 1e6)
    bulk = np.full(100000, 1e9)
    tension = n_pairs * 100.
    assert ctc.tension_fraction(tension, n_pairs * 90.) > ctc.TENSION_FRAC_TOL
    assert ctc.CONTACT_FORCE_REL_TOL * np.r_[rows, bulk].sum() > tension
    local = ctc.contact_tension_resolution(pair_dofs, rows, np.zeros(n_pairs), 1e12)
    padded = ctc.contact_tension_resolution(pair_dofs, np.r_[rows, bulk],
                                           np.zeros(n_pairs), 1e12)
    assert padded == local
    assert local < tension


def test_many_tied_pairs_in_a_large_deforming_body_are_rejected():
    cs, basis, K, _, _ = _blocks(strain=0., ny=401)
    assert cs.n_pairs >= 400
    x = np.zeros(basis.N)
    # Controlled iterate: bulk motion far from the interface used to dominate
    # the global tolerance. Its rows are not adjacent to the contact traces.
    x[basis.doflocs[0] < .024] = 1.
    x[cs.pair_dofs[:, 2:4]] = 1e-8 * cs.pair_n
    def controlled_iterate(A, rhs):
        return np.r_[x, np.zeros(A.shape[0] - basis.N)]

    sol = ctc.solve_contact(K, np.zeros(basis.N), cs, basis,
                           solve_fn=controlled_iterate, max_iter=1)
    old_tol = ctc.CONTACT_FORCE_REL_TOL * (abs(K) @ abs(x)).sum()
    assert old_tol > sol.tension_n_per_m > 0
    assert sol.n_tensile_pairs >= 400
    assert not sol.consistent and not sol.converged
