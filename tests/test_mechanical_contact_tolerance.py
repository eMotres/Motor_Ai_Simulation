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


def _blocks(stiffness=1.0, strain=1e-3):
    mesh = MeshTri.init_tensor(np.linspace(.02, .04, 7),
                               np.linspace(-.01, .01, 7))
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
