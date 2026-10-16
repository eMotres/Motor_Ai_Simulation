"""Analytic checks for the archived, read-only periodic work diagnostic."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "torque_low_current_review.py"
SPEC = importlib.util.spec_from_file_location("torque_low_current_review", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture(parallel=1, reversed_angle=False):
    n, pp = 48, 7
    angle = np.arange(n) * (2 * np.pi / (n * pp))
    x = pp * angle
    current = np.stack((-10 * np.sin(x) - 2 * np.sin(5 * x),
                        -10 * np.sin(x - 2*np.pi/3) - 2 * np.sin(5*x - 10*np.pi/3),
                        -10 * np.sin(x + 2*np.pi/3) - 2 * np.sin(5*x + 10*np.pi/3))) / parallel
    flux = np.stack((.02 * np.cos(x) + .002 * np.cos(5*x),
                     .02 * np.cos(x - 2*np.pi/3) + .002 * np.cos(5*x - 10*np.pi/3),
                     .02 * np.cos(x + 2*np.pi/3) + .002 * np.cos(5*x + 10*np.pi/3)))
    if reversed_angle:
        return current[:, ::-1], flux[:, ::-1], angle[::-1]
    return current, flux, angle


@pytest.mark.parametrize("parallel", [1, 2])
@pytest.mark.parametrize("reverse", [False, True])
def test_fundamental_and_fifth_spatial_harmonic_work(parallel, reverse):
    current, flux, angle = fixture(parallel, reverse)
    # 3/2 * pp * (10*.02 + 5*2*.002) = 2.31 Nm.
    assert MODULE.spectral_periodic_work(
        current, flux, angle, pole_pairs=7, parallel_branches=parallel
    ) == pytest.approx(2.31, abs=1e-12)
    assert MODULE.spectral_periodic_work(
        -current, flux, angle, pole_pairs=7, parallel_branches=parallel
    ) == pytest.approx(-2.31, abs=1e-12)


def test_zero_current_and_invalid_period():
    current, flux, angle = fixture()
    assert MODULE.spectral_periodic_work(
        np.zeros_like(current), flux, angle, pole_pairs=7
    ) == 0
    with pytest.raises(ValueError, match="one endpoint-excluded"):
        MODULE.spectral_periodic_work(current, flux, angle * 2, pole_pairs=7)
