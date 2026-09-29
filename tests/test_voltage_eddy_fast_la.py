"""Voltage+eddy fixed-projection optimization against the original algebra."""
import logging

import numpy as np
import pytest
from scipy.sparse import csr_matrix, diags

from motor_ai_sim.simulation import p2_drive


class ToyNonlinearField:
    """Monotone cubic field law with an exact Newton tangent."""
    def __init__(self, nonlinear):
        self.alpha = 20. if nonlinear else 0.
        self.base = np.array([2., 3., 4., 2.5, 3.5])
        self.solve_count = 0
        self.kpw_count = 0
        self.rhs_shapes = []

    def asmK(self, nu):
        return diags(self.base, format="csr")

    def Kpw(self, A):
        self.kpw_count += 1
        return diags(self.base + self.alpha * A*A, format="csr"), A.copy()

    def tangent2(self, A):
        return diags(2*self.alpha*A*A, format="csr")

    def solve_ff(self, matrix, rhs):
        self.solve_count += 1
        self.rhs_shapes.append(rhs.shape)
        return np.linalg.solve(matrix.toarray(), rhs)

    def pad2(self, projection, free, x):
        expanded = np.zeros(projection.shape[1])
        expanded[free] = x
        return np.asarray(projection @ expanded)


PROJECTION = csr_matrix([[1., 0., 0., 0.], [0., 1., 0., 0.],
                         [0., 0., 1., 0.], [0., 0., 0., 1.],
                         [.5, 0., .5, 0.]])
FREE = np.array([0, 2, 3])


def make_drive(nonlinear):
    field = ToyNonlinearField(nonlinear)
    G = csr_matrix([[.02, .01, -.015, .004], [.01, -.02, .005, -.003],
                    [-.01, .015, .02, .002], [.005, -.01, .015, .003],
                    [.015, .005, -.01, -.002]])
    flux_columns = G.toarray()[:, :3] * .1

    def psi(A):
        return tuple(flux_columns.T @ A)

    bodies = [dict(S=2.+j, key="cu", Iunit=1., phase=phase)
              for j, phase in enumerate("ABC")]
    bodies.append(dict(S=1.5, key="mag", Iunit=0., phase="A"))
    return p2_drive.P2Drive(
        p2=field, psi=psi, f_mag=np.array([4., 3., 2., -1., .5]),
        Pa=np.zeros(5), Pb=np.zeros(5), R_phase=.8, v_phase_peak=3.,
        n_dof=5, pic_tol=1e-3, dt=.01, log=logging.getLogger(__name__),
        ed_con=bodies, G=G, Msig=diags([.004, .003, .005, .004, .002], format="csr"),
        Msd=diags([.4, .3, .5, .4, .2], format="csr"),
        Sdt=np.array([.02, .03, .04, .015]))


def run_frame(drive, dtk, nonlinear, *, previous=None, projection=PROJECTION,
              free=FREE, voltage=1.):
    if previous is None:
        Aprev = np.zeros(5)
        Uprev = np.zeros(4)
        iprev = (0., 0.)
    else:
        Aprev, Uprev, iprev = previous
    iv_prev = dict(A=iprev[0], B=iprev[1], C=-sum(iprev))
    psi_prev = dict(zip("ABC", drive.psi(Aprev)))
    Vt = dict(A=voltage, B=-.3*voltage, C=-.7*voltage)
    result = drive.ve_newton(
        projection, free, Aprev.copy(), Uprev.copy(), iprev, Aprev,
        Vt, dtk, iv_prev, psi_prev, None if nonlinear else np.ones(5), 40)
    assert result[0], result
    A, U, ia, ib = result[1:5]
    # Verify the solved field, conductor constraints and voltage circuit,
    # including the zero-terminal-current rotor body, against their equations.
    K = diags(drive.p2.base + drive.p2.alpha*A*A, format="csr")
    mass = drive.Msig * (1./dtk)
    field_residual = np.asarray(projection.T @ (
        K @ A + mass @ (A-Aprev) - drive.G @ U - drive.f_mag)).ravel()[free]
    constraint = (drive.S_raw*dtk*U - drive.G.T @ (A-Aprev)
                  - dtk*(ia*drive.ed_ca + ib*drive.ed_cb))
    circuit = drive.circ_r(drive.psi(A), ia, ib, iv_prev, psi_prev, Vt, dtk)
    assert np.linalg.norm(field_residual) < 1e-6
    assert np.linalg.norm(constraint) < 1e-8
    assert np.max(np.abs(circuit)) < 1e-6
    assert all(shape == (free.size+4, 3) for shape in drive.p2.rhs_shapes)
    return result


def assert_same_solution(actual, reference):
    for index in (1, 2, 3, 4, 7):
        np.testing.assert_allclose(actual[index], reference[index], rtol=2e-11, atol=1e-12)
    assert actual[6] == reference[6]


@pytest.mark.parametrize("dtk", [.002, .01, .037])
@pytest.mark.parametrize("nonlinear", [False, True], ids=["linear", "nonlinear"])
def test_fast_la_matches_original_bordered_solve(monkeypatch, dtk, nonlinear):
    results = []
    for fast in (False, True):
        monkeypatch.setattr(p2_drive, "_SB_FAST_LA", fast)
        drive = make_drive(nonlinear)
        results.append(run_frame(drive, dtk, nonlinear))
        if nonlinear:
            # One Kpw per iterate plus at least one trial per Newton step.
            assert drive.p2.kpw_count >= 2*drive.p2.solve_count + 1
    assert_same_solution(results[1], results[0])


def test_nonlinear_backtracking_rejects_trials_in_both_paths(monkeypatch):
    results = []
    for fast in (False, True):
        monkeypatch.setattr(p2_drive, "_SB_FAST_LA", fast)
        drive = make_drive(True)
        results.append(run_frame(drive, .037, True))
        # Strictly more than one trial per step demonstrates backtracking,
        # rather than merely entering a line-search loop accepting lambda=1.
        assert drive.p2.kpw_count > 2*drive.p2.solve_count + 1
    assert_same_solution(results[1], results[0])


def test_projection_and_timestep_are_recomputed_each_frame(monkeypatch):
    traces = []
    for fast in (False, True):
        monkeypatch.setattr(p2_drive, "_SB_FAST_LA", fast)
        drive = make_drive(True)
        previous = None
        trace = []
        for index, dtk in enumerate((.01, .002, .037)):
            projection = PROJECTION.copy()
            # Change the weighted slip constraint while keeping the DOF layout.
            projection.data[-2:] = [.3+.1*index, .7-.1*index]
            result = run_frame(drive, dtk, True, previous=previous,
                               projection=projection, voltage=1.-.8*index)
            trace.append(result)
            previous = result[1], result[2], (result[3], result[4])
        traces.append(trace)
    for reference, actual in zip(traces[0], traces[1]):
        assert_same_solution(actual, reference)
