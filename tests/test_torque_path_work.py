import numpy as np
import pytest

from motor_ai_sim.simulation.torque_path_work import (
    spline_terminal_flux_work_per_mechanical_radian,
)


def _harmonic_path(intervals, *, nonuniform=False):
    t = np.linspace(0.0, 1.0, intervals + 1)
    if nonuniform:
        t = t ** 1.15
    pole_pairs = 7
    angle = (2.0 * np.pi / pole_pairs) * t
    offsets = np.array([0.0, 2.0 * np.pi / 3.0, -2.0 * np.pi / 3.0])[:, None]
    electrical = pole_pairs * angle[None, :] - offsets
    flux = 0.02 * np.cos(electrical) + 0.002 * np.cos(5.0 * electrical)
    current = -10.0 * np.sin(electrical) - 2.0 * np.sin(5.0 * electrical)
    return angle, current, flux


def _nonlinear_expected():
    a, b, phi = 4.0, 1.5, 0.3
    pm_mean = -0.02 * b * np.cos(phi) / 2.0
    inductance_mean = -1.6e-3 * b**2 * np.sin(2.0 * phi) / 8.0
    cubic_mean = 1e-6 * (2.0 * a**3 * b + 1.5 * a * b**3) * np.sin(phi)
    return float(pm_mean + inductance_mean + cubic_mean)


def test_harmonic_path_matches_independent_fifth_harmonic_coenergy_mean():
    expected = 2.31
    coarse_angle, coarse_i, coarse_psi = _harmonic_path(72)
    fine_angle, fine_i, fine_psi = _harmonic_path(1440)
    coarse = spline_terminal_flux_work_per_mechanical_radian(
        coarse_i, coarse_psi, coarse_angle).mean_work_per_mechanical_radian
    fine = spline_terminal_flux_work_per_mechanical_radian(
        fine_i, fine_psi, fine_angle).mean_work_per_mechanical_radian
    assert abs(fine - expected) < abs(coarse - expected)
    assert fine == pytest.approx(expected, abs=1e-7)


def test_fundamental_48_intervals_improves_over_24():
    errors = []
    for intervals in (24, 48):
        theta = np.linspace(0.0, 2.0 * np.pi, intervals + 1)
        current = np.cos(theta)
        flux = np.sin(theta)
        result = spline_terminal_flux_work_per_mechanical_radian(
            current[None, :], flux[None, :], theta)
        errors.append(abs(result.mean_work_per_mechanical_radian - 0.5))
    assert errors[1] < errors[0]


def test_nonlinear_position_dependent_flux_matches_coenergy_derivative():
    theta = np.linspace(0.0, 2.0 * np.pi, 2049)
    current = 4.0 + 1.5 * np.sin(theta + 0.3)
    flux = (.02 * np.cos(theta)
            + (.004 + .0008 * np.cos(2.0 * theta)) * current
            + (2e-5 + 4e-6 * np.sin(theta)) * current**3)
    got = spline_terminal_flux_work_per_mechanical_radian(
        current[None, :], flux[None, :], theta)
    assert got.mean_work_per_mechanical_radian == pytest.approx(
        _nonlinear_expected(), abs=2e-8)


def test_nonuniform_sampling_reversal_and_parallel_branch_scaling():
    angle, current, flux = _harmonic_path(1440, nonuniform=True)
    original_i, original_psi, original_angle = current.copy(), flux.copy(), angle.copy()
    base = spline_terminal_flux_work_per_mechanical_radian(current, flux, angle)
    reversed_result = spline_terminal_flux_work_per_mechanical_radian(
        current[:, ::-1], flux[:, ::-1], angle[::-1])
    branches = spline_terminal_flux_work_per_mechanical_radian(
        current / 2.0, flux, angle, parallel_branches=2)
    assert base.mean_work_per_mechanical_radian == pytest.approx(2.31, abs=2e-6)
    assert reversed_result.signed_angle_span_rad < 0.0
    assert reversed_result.mean_work_per_mechanical_radian == pytest.approx(
        base.mean_work_per_mechanical_radian, abs=1e-12)
    assert branches.mean_work_per_mechanical_radian == pytest.approx(
        base.mean_work_per_mechanical_radian, abs=1e-12)
    assert base.sample_count == len(angle)
    np.testing.assert_array_equal(current, original_i)
    np.testing.assert_array_equal(flux, original_psi)
    np.testing.assert_array_equal(angle, original_angle)


@pytest.mark.parametrize("branches", [0, -1, 1.5, True])
def test_invalid_branch_count_rejected(branches):
    angle = np.linspace(0.0, 1.0, 4)
    with pytest.raises(ValueError):
        spline_terminal_flux_work_per_mechanical_radian(
            np.ones((1, 4)), np.ones((1, 4)), angle,
            parallel_branches=branches)


def test_malformed_waveforms_and_angles_rejected():
    theta = np.linspace(0.0, 1.0, 5)
    current = np.ones((1, 5))
    flux = np.ones_like(current)
    with pytest.raises(ValueError):
        spline_terminal_flux_work_per_mechanical_radian(current[:, :-1], flux, theta)
    with pytest.raises(ValueError):
        spline_terminal_flux_work_per_mechanical_radian(current, flux, [0, 1, 2, 1, 4])
    bad = theta.copy(); bad[2] = np.nan
    with pytest.raises(ValueError):
        spline_terminal_flux_work_per_mechanical_radian(current, flux, bad)
    with pytest.raises(ValueError):
        spline_terminal_flux_work_per_mechanical_radian(
            current[:, :3], flux[:, :3], theta[:3])
