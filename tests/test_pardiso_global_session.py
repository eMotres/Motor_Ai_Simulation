"""The 3-D and mechanical solves no longer share a module-level solver.

Until 2026-09-30 they all went through ``pypardiso.spsolve``, ONE global
PyPardisoSolver, and had to hold ``global_pardiso_session`` around every solve
and retry.  They now go through ``linear_backend.solve_once``: each solve owns
its factorisation, so concurrent jobs cannot see each other's factors and need
no lock.  These tests pin that (on whatever backend is installed) and that the
old lock itself still serialises, for any caller that still uses it.
"""

import threading

import numpy as np
from scipy.sparse import csr_matrix

from motor_ai_sim.simulation.mechanical import contact
from motor_ai_sim.simulation.pardiso_lifetime import global_pardiso_session
from motor_ai_sim.simulation.static3d import nedelec, solver as static_solver


def _system(k):
    n = 60
    rng = np.random.default_rng(k)
    d = 4.0 + rng.random(n)
    off = -1.0 - 0.1 * rng.random(n - 1)
    import scipy.sparse as sp
    A = sp.diags([off, d, off], [-1, 0, 1], format="csr")
    return csr_matrix(A), rng.standard_normal(n)


def test_static_mechanical_and_nedelec_solve_concurrently_and_correctly():
    _, static_fac = static_solver._linear_solver()
    _, mechanical_solve = contact._solver()
    jobs = {
        "static": lambda A, b: static_fac(A)(b),
        "mechanical": mechanical_solve,
        "nedelec": lambda A, b: nedelec._direct(A, b)[0],
    }
    start = threading.Barrier(len(jobs) * 3)
    errors = []

    def run(name, k):
        try:
            A, b = _system(k)
            start.wait(timeout=10)
            for _ in range(5):
                x = jobs[name](A, b)
                assert np.allclose(A @ x, b, rtol=1e-10, atol=1e-12), name
        except BaseException as exc:          # noqa: BLE001 — report below
            errors.append((name, exc))

    threads = [threading.Thread(target=run, args=(name, 10 * i + r))
               for i, name in enumerate(jobs) for r in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()
    assert not errors, errors


def test_global_session_lock_still_serialises():
    inside = []
    overlap = []
    lock = threading.Lock()

    def work():
        with global_pardiso_session():
            with lock:
                inside.append(1)
                overlap.append(len(inside) > 1)
            threading.Event().wait(0.02)
            with lock:
                inside.pop()

    ts = [threading.Thread(target=work) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=10)
    assert not any(overlap)
