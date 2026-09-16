"""Native handle lifetime without a machine solve or a PARDISO installation."""
from concurrent.futures import ThreadPoolExecutor
import inspect
import logging
from threading import Barrier, Event

import numpy as np
import pytest
from scipy.sparse import csc_matrix

from motor_ai_sim.simulation.p2_nonlinear import P2Nonlinear
from motor_ai_sim.simulation.pardiso_lifetime import (
    own_pardiso, pardiso_scope, release_pardiso,
)


class Solver:
    def __init__(self, *, fail_phase=None, fail_cleanup=False):
        self.fail_phase = fail_phase
        self.fail_cleanup = fail_cleanup
        self.frees = []
        self.phases = []
        self.iparm = np.zeros(64, dtype=int)

    def free_memory(self, *, everything):
        self.frees.append(everything)
        if self.fail_cleanup:
            raise RuntimeError("native cleanup failed")

    def _check_A(self, A):
        pass

    def _check_b(self, A, rhs):
        return rhs

    def set_phase(self, phase):
        self.phase = phase

    def _call_pardiso(self, A, rhs):
        assert not self.frees, "use after native cleanup"
        self.phases.append(self.phase)
        if self.phase == self.fail_phase:
            raise RuntimeError("native solve failed")
        return np.linalg.solve(A.toarray(), rhs)

    def solve(self, A, rhs):
        self.set_phase(13)
        return self._call_pardiso(A, rhs)


def p2(solver):
    return P2Nonlinear(
        basis=None, n_dof=2, K_const=None, sat=[], sat_sub=[],
        pardiso=solver, log=logging.getLogger(__name__))


def test_success_keeps_symbolic_reuse_and_cleans_once():
    solver = Solver()
    A = csc_matrix([[4., 1.], [1., 3.]])
    rhs = np.array([1., 2.])

    @pardiso_scope
    def run():
        nonlinear = p2(own_pardiso(solver))
        nonlinear._reuse = True
        first = nonlinear.solve_ff(A, rhs)
        second = nonlinear.solve_ff(A * 2, rhs)
        assert solver.frees == []
        assert nonlinear.pardiso_analyses == 1
        assert nonlinear.pardiso_solves == 2
        return first, second

    first, second = run()
    np.testing.assert_allclose(A @ first, rhs)
    np.testing.assert_allclose((A * 2) @ second, rhs)
    assert solver.phases == [11, 23, 23]
    assert solver.frees == [True]


@pytest.mark.parametrize("error", [ValueError("preparation failed"), KeyboardInterrupt()])
def test_failure_before_nonlinear_construction_releases_handle(error):
    solver = Solver()

    @pardiso_scope
    def run():
        own_pardiso(solver)
        # A Basis/assembly/cancellation error before P2Nonlinear exists.
        raise error

    with pytest.raises(type(error)) as caught:
        run()
    assert caught.value is error
    assert solver.frees == [True]


@pytest.mark.parametrize("fail_phase", [11, 23, 13])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_fallback_releases_even_when_cleanup_fails(fail_phase, scoped, fail_cleanup):
    solver = Solver(fail_phase=fail_phase, fail_cleanup=fail_cleanup)
    A = csc_matrix([[4., 1.], [1., 3.]])
    rhs = np.array([[1., 3.], [2., 4.]])

    def run():
        nonlinear = p2(own_pardiso(solver) if scoped else solver)
        nonlinear._reuse = fail_phase != 13
        result = nonlinear.solve_ff(A, rhs)
        assert solver.frees == [True]
        assert nonlinear._pardiso is None
        assert nonlinear._pat is None
        # Subsequent calls stay on SuperLU without retrying the failed handle.
        again = nonlinear.solve_ff(A, rhs)
        np.testing.assert_array_equal(again, result)
        return result

    result = pardiso_scope(run)() if scoped else run()
    np.testing.assert_allclose(A @ result, rhs)
    assert solver.frees == [True]


def test_cleanup_failure_preserves_original_error_and_releases_other_handles(caplog):
    good, broken = Solver(), Solver(fail_cleanup=True)
    original = ValueError("original solve error")

    @pardiso_scope
    def run():
        own_pardiso(good)
        own_pardiso(broken)
        raise original

    with pytest.raises(ValueError) as caught:
        run()
    assert caught.value is original
    assert good.frees == broken.frees == [True]
    assert "PARDISO memory cleanup failed" in caplog.text


def test_early_release_and_duplicate_registration_are_idempotent():
    solver = Solver()

    @pardiso_scope
    def run():
        assert own_pardiso(solver) is solver
        own_pardiso(solver)
        release_pardiso(solver)
        release_pardiso(solver)

    run()
    assert solver.frees == [True]


def test_nested_calibration_failure_restores_parent_scope():
    parent, child, after_child = Solver(), Solver(), Solver()

    @pardiso_scope
    def calibration():
        own_pardiso(child)
        raise ValueError("calibration failed")

    @pardiso_scope
    def run():
        own_pardiso(parent)
        with pytest.raises(ValueError):
            calibration()
        assert child.frees == [True]
        assert parent.frees == []
        own_pardiso(after_child)

    run()
    assert parent.frees == after_child.frees == [True]
    assert child.frees == [True]
    # No stale scope remains after the outer invocation, including its error path.
    with pytest.raises(RuntimeError, match="requires a pardiso_scope"):
        own_pardiso(Solver())


def test_concurrent_calls_cannot_close_another_threads_handle():
    first, second = Solver(), Solver()
    both_registered = Barrier(2)
    first_finished = Event()

    @pardiso_scope
    def run(solver, wait_for_first):
        own_pardiso(solver)
        both_registered.wait(timeout=10)
        if wait_for_first:
            assert first_finished.wait(timeout=10)
            assert first.frees == [True]
            assert second.frees == []

    def first_worker():
        run(first, False)
        first_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_worker)
        second_future = pool.submit(run, second, True)
        first_future.result(timeout=15)
        second_future.result(timeout=15)
    assert first.frees == second.frees == [True]


def test_decorator_preserves_signature_and_handles_unavailable_backend():
    def run(value: int = 3, *, optional=None):
        """A solve with no native backend registered."""
        return value, optional

    wrapped = pardiso_scope(run)
    assert inspect.signature(wrapped) == inspect.signature(run)
    assert wrapped.__name__ == run.__name__
    assert wrapped.__doc__ == run.__doc__
    assert wrapped(7, optional="fallback") == (7, "fallback")
