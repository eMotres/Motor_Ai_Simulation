"""Actual complex sparse solves with one immutable preparation per history."""
import math

import numpy as np
import pytest
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from motor_ai_sim.simulation import eddy_solver_2d as eddy


def original_solve(p, t, nu, sigma, bodies, currents, omega, nodes, values, source=None):
    """Pre-cache solve retained as an independent numerical oracle."""
    n, count = p.shape[1], len(bodies)
    K = eddy.assemble_K(p, t, nu).astype(complex)
    M = eddy.assemble_Msigma(p, t, sigma).astype(complex)
    columns, S = [], np.zeros(count, complex)
    for body in bodies:
        indicator = np.zeros(n)
        indicator[np.unique(t[:, body].ravel())] = 1.
        column = M @ indicator
        columns.append(column)
        S[len(columns)-1] = indicator @ column
    P = np.array(columns).T if count else np.zeros((n, 0), complex)
    top = sp.hstack([K + 1j*omega*M, sp.csr_matrix(-P)], format="csr")
    bottom = sp.hstack([sp.csr_matrix(-1j*omega*P.T),
                        sp.csr_matrix(np.diag(S))], format="csr")
    matrix = sp.vstack([top, bottom], format="csr").tolil()
    rhs = np.zeros(n+count, complex)
    if source is not None:
        rhs[:n] = source
    rhs[n:] = np.asarray(currents, complex)
    free = np.ones(n+count, bool)
    free[nodes] = False
    matrix = matrix.tocsr()
    known = np.zeros(n+count, complex)
    known[nodes] = values
    rhs = rhs - matrix @ known
    reduced = matrix[free][:, free]
    solved = spsolve(reduced.tocsc(), rhs[free])
    out = known.copy()
    out[free] = solved
    return out[:n], out[n:]


def mesh_case():
    nx, ny = 9, 7
    x, y = np.meshgrid(np.linspace(-.01, .01, nx),
                       np.linspace(-.008, .008, ny), indexing="ij")
    p = np.array([x.ravel(), y.ravel()])
    triangles = []
    for i in range(nx-1):
        for j in range(ny-1):
            a = i*ny+j
            triangles.extend([(a, a+ny, a+ny+1), (a, a+ny+1, a+1)])
    t = np.array(triangles).T
    centers = p[:, t].mean(axis=1)
    bodies = [np.where(centers[0] < -.002)[0], np.where(centers[0] > .002)[0]]
    sigma = np.zeros(t.shape[1])
    sigma[bodies[0]], sigma[bodies[1]] = 1e5, 3e5
    nu = np.full(t.shape[1], 1./eddy.MU0)
    nu[centers[1] > .004] /= 40.
    nodes = np.flatnonzero((abs(p[0]) > .0099) | (abs(p[1]) > .0079))
    return p, t, nu, sigma, bodies, nodes


def assert_solution_equal(actual, reference):
    np.testing.assert_array_equal(actual[0], reference[0])
    np.testing.assert_array_equal(actual[1], reference[1])


@pytest.mark.parametrize("omega", [0., -2*math.pi*100, 2*math.pi*75, 1e5])
@pytest.mark.parametrize("with_source", [False, True])
def test_public_one_shot_matches_original_complex_sparse_solve(omega, with_source):
    p, t, nu, sigma, bodies, nodes = mesh_case()
    values = (.3-.2j)*p[1, nodes] + .01j*p[0, nodes]
    currents = [.7+.2j, -.4j]
    source = np.linspace(.001, .02, p.shape[1])*(1+.4j) if with_source else None
    args = p, t, nu, sigma, bodies, currents, omega, nodes, values, source
    actual = eddy.solve_harmonic_eddy(*args)
    reference = original_solve(*args)
    assert_solution_equal(actual, reference)
    np.testing.assert_array_equal(
        eddy.eddy_loss_per_body(p, t, sigma, *actual, bodies, omega, .03),
        eddy.eddy_loss_per_body(p, t, sigma, *reference, bodies, omega, .03))


def test_prepared_sequence_has_no_stale_rhs_boundary_or_solution():
    p, t, nu, sigma, bodies, nodes = mesh_case()
    prepared = eddy._PreparedHarmonicEddy(p, t, nu, sigma, bodies, nodes)
    matrix_data = [prepared.K.data.copy(), prepared.M.data.copy(), prepared.P.copy()]
    for index in (1, 2, 3, 1):
        omega = index*2*math.pi*100
        values = (index-.2j)*p[1, nodes]
        currents = [.7*index+.2j, -.4j*index]
        source = np.linspace(.001, .02, p.shape[1])*(1+index*.4j)
        saved = values.copy(), source.copy()
        actual = prepared.solve(currents, omega, values, source)
        reference = original_solve(p, t, nu, sigma, bodies, currents, omega,
                                   nodes, values, source)
        assert_solution_equal(actual, reference)
        np.testing.assert_array_equal(values, saved[0])
        np.testing.assert_array_equal(source, saved[1])
        # Modifying a returned solution must not poison later frequencies.
        actual[0][:] = np.nan
        actual[1][:] = np.nan
    for actual, original in zip((prepared.K.data, prepared.M.data, prepared.P), matrix_data):
        np.testing.assert_array_equal(actual, original)


def test_new_preparation_reflects_changed_mesh_material_and_boundary():
    p, t, nu, sigma, bodies, nodes = mesh_case()
    first = eddy._PreparedHarmonicEddy(p, t, nu, sigma, bodies, nodes)
    values = .1*p[1, nodes].astype(complex)
    reference_first = first.solve([0., 0.], 600., values)
    p *= 1.2
    sigma *= 1.7
    nu *= .8
    nodes[:] = nodes[::-1]  # first owns the original boundary indexing
    second = eddy._PreparedHarmonicEddy(p, t, nu, sigma, bodies, nodes)
    changed_values = (.1+.02j)*p[1, nodes]
    reference_second = original_solve(p, t, nu, sigma, bodies, [0., 0.],
                                      900., nodes, changed_values)
    assert_solution_equal(second.solve([0., 0.], 900., changed_values), reference_second)
    assert_solution_equal(first.solve([0., 0.], 600., values), reference_first)


def test_no_body_and_fully_prescribed_field_edges():
    p, t, nu, sigma, _, nodes = mesh_case()
    for all_prescribed in (False, True):
        boundary = np.arange(p.shape[1]) if all_prescribed else nodes
        values = (.1+.2j)*p[1, boundary]
        source = np.ones(p.shape[1], complex)*.01
        args = p, t, nu, sigma, [], [], 200., boundary, values, source
        assert_solution_equal(eddy.solve_harmonic_eddy(*args), original_solve(*args))


def history_case():
    p, t, nu, sigma, bodies, nodes = mesh_case()
    phase = 2*math.pi*np.arange(32)/32
    boundary_wave = (np.cos(phase) + .3*np.sin(3*phase) + 1e-8*np.cos(5*phase))
    history = boundary_wave[:, None]*p[1][None, :]
    currents = [np.sin(phase)*.2, np.cos(3*phase)*.1]
    mask = np.zeros(p.shape[1], bool)
    mask[nodes] = True
    return p, t, nu, sigma, bodies, nodes, mask, history, currents


def original_history(p, t, nu, sigma, bodies, nodes, history, period, length,
                     currents=None):
    """Independent oracle: EVERY bin to Nyquist (no cap, no amplitude floor —
    owner 2026-09-24), Nyquist bin of an even window at |C|, a bin skipped only
    when its whole drive is exactly zero."""
    count = history.shape[0]
    Ah = np.fft.rfft(history, axis=0)/count
    Ih = [np.fft.rfft(np.asarray(i, float))/count for i in currents] if currents is not None else None
    losses, used = np.zeros(len(bodies)), []
    for k in range(1, Ah.shape[0]):
        a_k = 1. if (count % 2 == 0 and k == count//2) else 2.
        amplitude = a_k*Ah[k]
        imposed = [complex(a_k*Ih[b][k]) for b in range(len(bodies))] if Ih is not None else [0.]*len(bodies)
        if not np.any(amplitude[nodes]) and not any(imposed):
            continue
        omega = 2.*math.pi*k/period
        A, E0 = original_solve(p, t, nu, sigma, bodies, imposed, omega, nodes, amplitude[nodes])
        losses += eddy.eddy_loss_per_body(p, t, sigma, A, E0, bodies, omega, length)
        used.append(k/period)
    return losses, used


@pytest.mark.parametrize("with_currents", [False, True])
def test_history_losses_and_frequency_selection_match_original(with_currents):
    p, t, nu, sigma, bodies, nodes, mask, history, currents = history_case()
    currents = currents if with_currents else None
    actual = eddy.region_eddy_from_history(p, t, nu, sigma, bodies, mask,
                                          history, .01, .03, currents)
    reference = original_history(p, t, nu, sigma, bodies, nodes,
                                 history, .01, .03, currents)
    np.testing.assert_array_equal(actual[0], reference[0])
    assert actual[1] == reference[1]
    # the 1e-8 fifth harmonic is SOLVED now (it used to fall under the 5e-4
    # amplitude floor), and nothing is capped below Nyquist
    assert {100., 300., 500.} <= set(actual[1])


def test_no_cap_and_no_floor_every_physical_line_is_billed():
    """An order-20 line (above the retired k <= 16 cap) and a line at 1e-6 of
    the fundamental (below the retired 5e-4 amplitude floor) both reach the
    loss, each exactly as its own single-line history would bill it."""
    p, t, nu, sigma, bodies, nodes, mask, _, _ = history_case()
    n = 64
    ph = 2*math.pi*np.arange(n)/n

    def run(wave):
        return eddy.region_eddy_from_history(
            p, t, nu, sigma, bodies, mask, wave[:, None]*p[1][None, :], .01, .03)
    base, _ = run(np.cos(ph))
    for order, amp in ((20, 1.), (3, 1e-6)):
        both, used = run(np.cos(ph) + amp*np.cos(order*ph))
        alone, _ = run(amp*np.cos(order*ph))
        assert order/.01 in used
        assert float(np.sum(alone)) > 0.
        if amp == 1.:          # (a 1e-12-relative sum cannot be read by subtraction)
            np.testing.assert_allclose(np.sum(both) - np.sum(base),
                                       np.sum(alone), rtol=1e-6)


def test_each_history_assembles_once_but_factorizes_every_frequency(monkeypatch):
    p, t, nu, sigma, bodies, _, mask, history, currents = history_case()
    counts = dict(K=0, M=0, solve=0)
    for name, key in (("assemble_K", "K"), ("assemble_Msigma", "M"), ("spsolve", "solve")):
        original = getattr(eddy, name)

        def counted(*args, _original=original, _key=key, **kwargs):
            counts[_key] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(eddy, name, counted)
    solved = 0
    for expected_calls in (1, 2):
        result = eddy.region_eddy_from_history(p, t, nu, sigma, bodies, mask,
                                              history, .01, .03, currents)
        solved += len(result[1])
        assert {100., 300.} <= set(result[1])
        assert counts == dict(K=expected_calls, M=expected_calls, solve=solved)


@pytest.mark.parametrize("mode", ["single-frame", "zero-history"])
def test_history_without_retained_harmonics_does_not_prepare(monkeypatch, mode):
    p, t, nu, sigma, bodies, _, mask, history, _ = history_case()

    def forbidden(*args, **kwargs):
        raise AssertionError("assembly attempted without a retained harmonic")

    monkeypatch.setattr(eddy, "_PreparedHarmonicEddy", forbidden)
    if mode == "single-frame":
        history = history[:1]
    else:
        history = np.zeros_like(history)     # no drive at all: exactly zero
    losses, used = eddy.region_eddy_from_history(
        p, t, nu, sigma, bodies, mask, history, .01, .03)
    np.testing.assert_array_equal(losses, np.zeros(len(bodies)))
    assert used == []
