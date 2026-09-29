"""Production utility parity against the isolated exact-overlap mortar."""

import numpy as np
import pytest

from motor_ai_sim.simulation.p2_projection import SlipMortarDerivativeAction
from scripts.continuous_p2_mortar_energy_validation import (
    small_ring, solve_quadratic,
)


def _fixed_coordinates(mortar, full_field):
    """Express an integer-weld full field in mortar's fixed coordinates."""
    trace = mortar.trace
    values = np.zeros(mortar.shape[1])
    for root in trace.free_roots:
        dof = int(np.flatnonzero(trace.root == root)[0])
        values[trace.column[root]] = full_field[dof] / trace.sign[dof]
    return values


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, 1), (False, -1)])
def test_integer_motion_action_matches_exact_mortar_and_only_moves_trace(
    full_ring, sign,
):
    mortar = small_ring(full_ring=full_ring, sign=sign)
    projection = mortar.discrete
    action = SlipMortarDerivativeAction(projection, mortar.spacing_rad)
    rng = np.random.default_rng(881)
    rotor_rows = action._rotor_dofs
    other_rows = np.setdiff1d(np.arange(projection.N), rotor_rows)
    period = action.period
    for shift in (-2*period-1, -period, -1, 0, 1, period, 2*period+1):
        integer_p, _ = projection.build(shift)
        field = integer_p @ rng.normal(size=integer_p.shape[1])
        actual = action.motion_derivative(field, shift)
        fixed = _fixed_coordinates(mortar, field)
        reference = mortar.build(shift*mortar.spacing_rad)[1] @ fixed
        np.testing.assert_allclose(actual, reference, rtol=0., atol=3e-12)
        assert np.count_nonzero(actual[other_rows]) == 0
    assert action.mass_nnz > 0
    assert action.mass_storage_bytes > 0


@pytest.mark.parametrize("full_ring,sign", [(True, 1), (False, -1)])
def test_integer_motion_action_matches_conservative_energy_derivative(
    full_ring, sign,
):
    mortar = small_ring(full_ring=full_ring, sign=sign)
    action = SlipMortarDerivativeAction(mortar.discrete, mortar.spacing_rad)
    rng = np.random.default_rng(417)
    stiffness = 1.+rng.random(mortar.shape[0])
    source = rng.normal(size=mortar.shape[0])
    shift = -action.period-1
    theta = shift*mortar.spacing_rad
    solved = solve_quadratic(mortar, theta, stiffness, source)
    derivative = action.motion_derivative(solved["field"], shift)
    from_action = float(solved["residual"] @ derivative)
    h = 1e-5*mortar.spacing_rad
    energy_fd = (solve_quadratic(mortar, theta+h, stiffness, source)["phi"] -
                 solve_quadratic(mortar, theta-h, stiffness, source)["phi"])/(2*h)
    np.testing.assert_allclose(from_action, solved["envelope"],
                               rtol=0., atol=2e-12)
    np.testing.assert_allclose(from_action, energy_fd,
                               rtol=2e-7, atol=2e-9)
