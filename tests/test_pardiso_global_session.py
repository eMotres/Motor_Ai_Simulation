"""The shared pypardiso.spsolve handle must not be used by concurrent jobs."""

import sys
import threading
import types

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from motor_ai_sim.simulation.mechanical import contact
from motor_ai_sim.simulation.static3d import nedelec, solver as static_solver


_A = csr_matrix([[4.0, 1.0], [1.0, 3.0]])
_B = np.array([1.0, 2.0])


def _install_fake(monkeypatch, solve, reset):
    package = types.ModuleType("pypardiso")
    package.__path__ = []
    package.spsolve = solve
    aliases = types.ModuleType("pypardiso.scipy_aliases")
    aliases.pypardiso_solver = types.SimpleNamespace(
        remove_stored_factorization=reset)
    monkeypatch.setitem(sys.modules, "pypardiso", package)
    monkeypatch.setitem(sys.modules, "pypardiso.scipy_aliases", aliases)


def _run_threads(*targets):
    errors = []
    threads = []

    def run(target):
        try:
            target()
        except BaseException as exc:  # include assertion failures in workers
            errors.append(exc)

    for name, target in targets:
        thread = threading.Thread(target=run, args=(target,), name=name)
        thread.start()
        threads.append(thread)
    return threads, errors


def _join(threads, *error_lists):
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive(), f"{thread.name} did not finish"
    for errors in error_lists:
        assert not errors, errors


def test_static_mechanical_and_nedelec_share_one_global_lock(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []
    active = 0
    overlap = False
    state_lock = threading.Lock()

    def solve(A, b):
        nonlocal active, overlap
        with state_lock:
            active += 1
            overlap |= active > 1
            calls.append(threading.current_thread().name)
            first = len(calls) == 1
        if first:
            entered.set()
            assert release.wait(2)
        with state_lock:
            active -= 1
        return np.linalg.solve(A.toarray(), b)

    _install_fake(monkeypatch, solve, lambda: None)
    _, static_fac = static_solver._linear_solver()
    _, mechanical_solve = contact._solver()
    first, errors = _run_threads(("static", lambda: static_fac(_A)(_B)))
    rest, more_errors = [], []
    other_started = threading.Event()

    def other(solve):
        other_started.set()
        return solve()

    try:
        assert entered.wait(2)
        rest, more_errors = _run_threads(
            ("mechanical", lambda: other(lambda: mechanical_solve(_A, _B))),
            ("nedelec", lambda: other(lambda: nedelec._direct(_A, _B))),
        )
        assert other_started.wait(2)
        assert not release.wait(0.1)
        assert calls == ["static"]
    finally:
        release.set()
    _join(first + rest, errors, more_errors)
    assert not overlap
    assert set(calls) == {"static", "mechanical", "nedelec"}


@pytest.mark.parametrize("retry_owner", ["static", "mechanical"])
def test_reset_and_retry_are_atomic_with_other_global_solves(
    monkeypatch, retry_owner,
):
    in_reset = threading.Event()
    release_reset = threading.Event()
    operations = []

    def solve(A, b):
        name = threading.current_thread().name
        operations.append(f"solve:{name}")
        if name == "retry" and operations.count("solve:retry") == 1:
            return np.full(A.shape[0], np.nan)
        return np.linalg.solve(A.toarray(), b)

    def reset():
        operations.append("reset")
        in_reset.set()
        assert release_reset.wait(2)

    _install_fake(monkeypatch, solve, reset)
    _, static_fac = static_solver._linear_solver()
    _, mechanical_solve = contact._solver()
    retry = (lambda: static_fac(_A)(_B)) if retry_owner == "static" else (
        lambda: mechanical_solve(_A, _B))
    first, errors = _run_threads(("retry", retry))
    second, more_errors = [], []
    other_started = threading.Event()

    def other():
        other_started.set()
        return nedelec._direct(_A, _B)

    try:
        assert in_reset.wait(2)
        second, more_errors = _run_threads(("other", other))
        assert other_started.wait(2)
        assert not release_reset.wait(0.1)
        assert operations == ["solve:retry", "reset"]
    finally:
        release_reset.set()
    _join(first + second, errors, more_errors)
    assert operations == [
        "solve:retry", "reset", "solve:retry", "solve:other",
    ]
