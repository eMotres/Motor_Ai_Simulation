"""TDM source-work regressions for the production time-periodic helper."""
import numpy as np
import pytest
from scipy.sparse import coo_matrix, csr_matrix, eye

from motor_ai_sim.simulation import time_periodic as TP

def _map_matrix(n, rotor, period_map, half_neg=False):
    perm, signs = period_map
    rotor = np.asarray(rotor, dtype=int)
    perm = np.asarray(perm, dtype=int)
    global_sign = -1.0 if half_neg else 1.0
    stator = np.setdiff1d(np.arange(n), rotor)
    rows = np.r_[stator, rotor]
    cols = np.r_[stator, rotor[perm]]
    data = np.r_[np.full(stator.size, global_sign),
                 global_sign * np.asarray(signs)]
    return coo_matrix((data, (rows, cols)), shape=(n, n)).tocsr()


def _checks(P, f, back, fwd, *, source_work=False, transpose=None):
    n = P.shape[0]
    I = eye(n, format="csr")
    GT = csr_matrix(([1.0], ([0], [0])), shape=(1, n))
    return TP.period_map_checks(
        wrap=back, wrapf=fwd, P_from=P, P_to=P, P_start=P, P_fwd=P,
        K=I, Msig=I, GT=GT, S_raw=np.ones(1), f_mag=f,
        source_work=source_work, wrap_transpose=transpose)


@pytest.mark.parametrize("perm,signs,neg", [
    (np.array([1, 2, 0]), np.array([1., -1., 1.]), False),
    (np.array([0, 0, 2]), np.array([1., -1., -1.]), False),
    (np.array([1, 1, 0]), np.array([-1., 1., -1.]), True),
])
def test_top_level_helper_is_exact_sparse_transpose(perm, signs, neg):
    n = 7
    rotor = np.array([1, 3, 5])
    mp = (perm, signs)
    H = _map_matrix(n, rotor, mp, neg)
    f = np.array([.3, 1.2, -.7, 2.1, .5, -1.8, .9])
    got = TP.period_map_transpose(f, rotor, mp, half_neg=neg)
    assert np.allclose(got, H.T @ f, atol=1e-14, rtol=0)


def test_legacy_source_check_is_byte_for_byte_same_result_by_default():
    P = eye(3, format="csr")
    rotor = np.arange(3)
    mp = (np.array([1, 0, 2]), np.ones(3))
    H = _map_matrix(3, rotor, mp)
    f = np.array([1., 3., 0.])
    args = dict(P=P, f=f, back=lambda x: H @ x, fwd=lambda x: H.T @ x)
    ok0, rec0 = _checks(**args)
    ok1, rec1 = _checks(**args, source_work=False, transpose=None)
    assert (ok0, rec0) == (ok1, rec1)
    assert set(rec0["dev"]) == {"inverse", "constrained", "operators",
                                 "bodies", "source"}
    assert rec0["dev"]["source"] > 1e-4


def test_constrained_alias_work_passes_and_retains_raw_defect():
    # Full nodes 1/2 encode one constrained periodic-cut degree of freedom.
    P = csr_matrix(np.array([[1., 0.], [0., 1.], [0., 1.]]))
    rotor = np.array([1, 2])
    mp = (np.array([1, 0]), np.ones(2))
    H = _map_matrix(3, rotor, mp)
    f = np.array([1., 3., 0.])
    transpose = lambda x: TP.period_map_transpose(x, rotor, mp)
    ok, rec = _checks(P, f, lambda x: H @ x, lambda x: H.T @ x,
                      source_work=True, transpose=transpose)
    assert ok, rec
    assert rec["dev"]["source"] == 0.0
    assert rec["dev"]["source_work"] == 0.0
    assert rec["dev"]["source_raw"] > 1e-4
    assert rec["tol"]["source"] == 1e-4


def test_wrong_source_is_rejected_and_missing_transpose_refuses_opt_in():
    P = eye(3, format="csr")
    rotor = np.arange(3)
    mp = (np.array([1, 0, 2]), np.ones(3))
    H = _map_matrix(3, rotor, mp)
    f = np.array([1., -1., 0.])  # wrong polarity under the map
    transpose = lambda x: TP.period_map_transpose(x, rotor, mp)
    ok, rec = _checks(P, f, lambda x: H @ x, lambda x: H.T @ x,
                      source_work=True, transpose=transpose)
    assert not ok and "source" in rec["failed"]
    assert rec["dev"]["source"] > rec["tol"]["source"]
    with pytest.raises(ValueError, match="exact period-map transpose"):
        _checks(P, f, lambda x: H @ x, lambda x: H.T @ x, source_work=True)


def test_zero_reduced_reference_with_nonzero_defect_refuses():
    P = csr_matrix(np.array([[1.], [-1.]]))
    f = np.array([1., 1.])
    ok, rec = _checks(P, f, lambda x: x, lambda x: x,
                      source_work=True, transpose=lambda x: np.array([1., -1.]))
    assert not ok and "source" in rec["failed"]
    assert rec["dev"]["source"] == float("inf")