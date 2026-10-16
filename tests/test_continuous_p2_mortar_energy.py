"""Independent conservative-energy checks of the isolated mortar P and P'."""

import numpy as np
import pytest

from scripts.continuous_p2_mortar_energy_validation import (
    small_ring, solve_quadratic, solve_quartic,
)


def _inputs(mortar):
    rng = np.random.default_rng(421)
    n = mortar.shape[0]
    return .8+rng.random(n), rng.normal(size=n)


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, 1), (False, -1)])
@pytest.mark.parametrize("slot", [.37, -.37, 6.37])
def test_quadratic_envelope_and_coenergy_torque(full_ring, sign, slot):
    mortar = small_ring(full_ring=full_ring, sign=sign)
    stiffness, source = _inputs(mortar)
    theta = slot*mortar.spacing_rad
    h = 1e-5*mortar.spacing_rad
    solved = solve_quadratic(mortar, theta, stiffness, source)
    plus = solve_quadratic(mortar, theta+h, stiffness, source)
    minus = solve_quadratic(mortar, theta-h, stiffness, source)
    energy_fd = (plus["phi"]-minus["phi"])/(2*h)
    coenergy_fd = (plus["coenergy"]-minus["coenergy"])/(2*h)
    assert solved["projected_residual"] < 1e-11
    assert solved["full_residual"] > 1e-3
    np.testing.assert_allclose(solved["envelope"], energy_fd,
                               rtol=2e-7, atol=2e-9)
    np.testing.assert_allclose(solved["torque"], coenergy_fd,
                               rtol=2e-7, atol=2e-9)
    assert solved["torque"] == -solved["envelope"]


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, 1), (False, -1)])
def test_wrap_sign_and_reversal_of_a_pure_source(full_ring, sign):
    mortar = small_ring(full_ring=full_ring, sign=sign)
    stiffness, source = _inputs(mortar)
    theta = .37*mortar.spacing_rad
    period = mortar.trace.period*mortar.spacing_rad
    base_p, base_dp, _ = mortar.build(theta)
    wrap_p, wrap_dp, _ = mortar.build(theta+period)
    row_sign = np.ones(mortar.shape[0])
    if not full_ring:
        row_sign[np.isin(mortar.trace.root, mortar.rotor_roots)] = sign
    np.testing.assert_allclose(wrap_p.toarray(),
                               row_sign[:, None]*base_p.toarray(), atol=1e-13)
    np.testing.assert_allclose(wrap_dp.toarray(),
                               row_sign[:, None]*base_dp.toarray(), atol=1e-12)

    forward = solve_quadratic(mortar, theta, stiffness, source)
    reversed_source = solve_quadratic(mortar, theta, stiffness, -source)
    scaled = solve_quadratic(mortar, theta, stiffness, 1.8*source)
    np.testing.assert_allclose(reversed_source["a"], -forward["a"], atol=1e-12)
    np.testing.assert_allclose(reversed_source["torque"], forward["torque"],
                               atol=1e-12)
    np.testing.assert_allclose(scaled["torque"], 1.8**2*forward["torque"],
                               atol=1e-12)


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, -1)])
def test_convex_diagonal_nonlinear_energy_has_same_envelope_identity(full_ring, sign):
    mortar = small_ring(full_ring=full_ring, sign=sign)
    stiffness, source = _inputs(mortar)
    beta = np.full(mortar.shape[0], .04)
    theta = -.37*mortar.spacing_rad
    h = 1e-5*mortar.spacing_rad
    solved = solve_quartic(mortar, theta, stiffness, beta, source)
    plus = solve_quartic(mortar, theta+h, stiffness, beta, source)
    minus = solve_quartic(mortar, theta-h, stiffness, beta, source)
    energy_fd = (plus["phi"]-minus["phi"])/(2*h)
    coenergy_fd = (plus["coenergy"]-minus["coenergy"])/(2*h)
    assert solved["projected_residual"] < 2e-12
    assert solved["full_residual"] > 1e-3
    np.testing.assert_allclose(solved["envelope"], energy_fd,
                               rtol=2e-7, atol=2e-9)
    np.testing.assert_allclose(solved["torque"], coenergy_fd,
                               rtol=2e-7, atol=2e-9)
