"""Synthetic checks for the offline paired virtual-work arithmetic."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("torque_frozen_mean_review", SCRIPTS / "torque_frozen_mean_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_source_coenergy_uses_branch_current_without_extra_parallel_division():
    A = np.array([1., 2.])
    fcoil = {"A": np.array([3., 4.]), "B": np.zeros(2), "C": np.zeros(2)}
    assert review.source_coenergy(A, {"A": 2., "B": 0., "C": 0.}, fcoil, .5, .02) == pytest.approx(-.06)


def test_signed_pair_secant_and_fundamental_finite_displacement_factor():
    center, delta, harmonic = .3, .01, 7.
    angles = np.array([center-delta, center+delta])
    W = np.cos(harmonic*angles)
    secant = review.paired_secants(W, angles)[0]
    exact_derivative = -harmonic*np.sin(harmonic*center)
    assert secant/exact_derivative == pytest.approx(np.sin(harmonic*delta)/(harmonic*delta))
    assert review.paired_secants(np.array([0., 1., 2., 3.]), np.array([0., 1., 2., 3.])).tolist() == [1., 1.]
    with pytest.raises(ValueError):
        review.paired_secants(np.array([0., 1.]), np.array([1., 0.]))
