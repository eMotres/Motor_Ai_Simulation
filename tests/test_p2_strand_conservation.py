"""Kirchhoff checks on the actual bordered eddy solve, without live config."""
import logging

import numpy as np
import pytest
from scipy.sparse import csr_matrix, eye

from motor_ai_sim.simulation import p2_drive


@pytest.fixture(params=[False, True], ids=["standard-la", "fast-la"])
def fast_la(request, monkeypatch):
    monkeypatch.setattr(p2_drive, "_SB_FAST_LA", request.param)


class LinearField:
    sat = []

    def __init__(self, corruption=None):
        self.corruption = corruption

    def asmK(self, nu):
        return csr_matrix([[10.]])

    def solve_ff(self, matrix, rhs):
        result = np.linalg.solve(matrix.toarray(), rhs)
        if self.corruption is not None:
            index, error = self.corruption
            result[index] += error
        return result

    def pad2(self, projection, free, x):
        return np.asarray(projection @ x)


def make_drive(group_sizes=(3,), *, g=None, series=True, corruption=None):
    count = sum(group_sizes)
    if g is None:
        g = .13 + .19 * np.arange(count)
    bodies = [dict(S=1., key="cu", Iunit=1., phase="A") for _ in range(count)]
    paths = dict(paths=[[(j, 1.)] for j in range(count)],
                 group=np.repeat(np.arange(len(group_sizes)), group_sizes),
                 n_group=len(group_sizes), rep=list(range(count)))
    return p2_drive.P2Drive(
        p2=LinearField(corruption), psi=lambda a: (0., 0., 0.),
        f_mag=np.array([1.]), Pa=np.zeros(1), Pb=np.zeros(1), R_phase=1.,
        v_phase_peak=1., n_dof=1, pic_tol=1e-3, dt=.01,
        log=logging.getLogger(__name__), ed_con=bodies, G=csr_matrix([g]),
        Msig=csr_matrix([[20.]]), Msd=csr_matrix([[2000.]]),
        Sdt=np.full(count, .01), paths=paths if series else None)


def solve(drive, group_sizes, imposed):
    per_path = np.repeat(np.asarray(imposed) / group_sizes, group_sizes)
    return drive.eddy_solve(
        eye(1, format="csr"), np.array([0]), np.array([.4]),
        np.zeros(sum(group_sizes)), per_path, np.array([.4]), np.array([1.]), 4)


@pytest.mark.parametrize("g", [[.1, .2, .7], [.13, .29, .71], [.3, .5, .9]])
def test_zero_current_audit_reproducer_with_circulating_paths(fast_la, g):
    drive = make_drive(g=g)
    result = solve(drive, (3,), [0.])
    assert result[0], result
    assert result[3] < 1e-7
    assert np.max(np.abs(drive.path_currents)) > 1e-4
    assert abs(np.sum(drive.path_currents)) < 1e-12


@pytest.mark.parametrize("imposed", [1e-14, -1e-14, 1e-8, -1e-8, 60., -60.])
def test_tiny_and_loaded_currents_with_circulation(fast_la, imposed):
    drive = make_drive()
    result = solve(drive, (3,), [imposed])
    assert result[0], result
    np.testing.assert_allclose(np.sum(drive.path_currents), imposed,
                               rtol=1e-10, atol=1e-12)
    assert drive.path_circ > 1e-4


def test_distinct_group_scales_solve_and_conserve_locally(fast_la):
    drive = make_drive((3, 3, 3))
    imposed = np.array([0., 1e-8, -1e5])
    result = solve(drive, (3, 3, 3), imposed)
    assert result[0], result
    # This test's shared field couples the loaded coil to all three groups.
    # Check each group's sum rather than normalizing by the loaded coil.
    total = np.asarray(drive.pQ.T @ drive.path_currents).ravel()
    np.testing.assert_allclose(total[:2], imposed[:2], atol=1e-11, rtol=0.)
    np.testing.assert_allclose(total[2], imposed[2], rtol=1e-12)


@pytest.mark.parametrize("imposed,error", [(0., 1e-6), (1e-8, 1e-6),
                                             (60., 1e-5), (-60., -1e-5)])
def test_actual_solve_rejects_corrupted_path_currents(fast_la, imposed, error):
    # First path-current unknown follows one field and six body potentials.
    # The large second coil makes Newton's norm accept this small error;
    # the independent Kirchhoff invariant must still catch the first coil.
    drive = make_drive((3, 3), corruption=(1 + 6, error))
    with pytest.raises(RuntimeError, match="coil index 0:.*tolerance"):
        solve(drive, (3, 3), [imposed, 1e5])


@pytest.mark.parametrize("imposed", [0., 60., -60.])
def test_one_path_matches_transposed_winding(fast_la, imposed):
    series = make_drive((1,), g=[.29])
    transposed = make_drive((1,), g=[.29], series=False)
    actual = solve(series, (1,), [imposed])
    reference = solve(transposed, (1,), [imposed])
    assert actual[0] and reference[0]
    np.testing.assert_allclose(actual[1], reference[1], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(actual[2], reference[2], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(series.path_currents, [imposed], rtol=1e-12, atol=1e-12)


def test_cancellation_roundoff_is_allowed_but_not_relative_circulation_error():
    drive = make_drive()
    # A few ulps of a large circulating current are unavoidable in its sum.
    drive._check_path_current_conservation(np.array([1e9, -1e9, 1e-6]), np.zeros(1))
    # 1e-8*sum(abs(paths)) would erroneously allow this 1 A imbalance.
    with pytest.raises(RuntimeError, match="coil index 0"):
        drive._check_path_current_conservation(np.array([1e9, -1e9, 1.]), np.zeros(1))


@pytest.mark.parametrize("imposed", [0., 1e-8])
def test_local_absolute_floor_does_not_allow_nanoamp_leakage(imposed):
    drive = make_drive()
    with pytest.raises(RuntimeError, match="coil index 0"):
        drive._check_path_current_conservation(
            np.array([.1, -.1, imposed + 1e-9]), np.array([imposed]))


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("where", ["path", "imposed"])
def test_nonfinite_currents_cannot_pass_the_invariant(value, where):
    drive = make_drive()
    currents, imposed = np.zeros(3), np.zeros(1)
    (currents if where == "path" else imposed)[0] = value
    with pytest.raises(RuntimeError, match="non-finite"):
        drive._check_path_current_conservation(currents, imposed)


def test_overflow_in_local_scale_cannot_hide_a_violation():
    drive = make_drive()
    with pytest.raises(RuntimeError, match="coil index 0"):
        drive._check_path_current_conservation(
            np.array([1e308, 1e308, -1e308]), np.zeros(1))
