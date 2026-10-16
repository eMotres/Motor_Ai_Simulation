"""Synthetic arithmetic gates; archived FEM files are not required."""
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "torque_frozen_lowcurrent_review", SCRIPTS / "torque_frozen_lowcurrent_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def test_pair_secants_preserve_every_center_and_signed_slope():
    centers = np.arange(48) * .02
    delta = .003
    angle = np.ravel(np.stack((centers-delta, centers+delta), axis=1))
    energy = 2.5*angle + .1
    secants = REVIEW.paired_secants(energy, angle)
    assert secants.shape == (48,)
    np.testing.assert_allclose(secants, 2.5, atol=1e-13)
    with pytest.raises(ValueError):
        REVIEW.paired_secants(energy, angle[::-1])


def test_branch_current_source_coenergy_has_no_extra_parallel_division():
    A = np.array([1., 2.])
    fcoil = {"A": np.array([3., 4.]), "B": np.zeros(2), "C": np.zeros(2)}
    current = {"A": 1., "B": 0., "C": 0.}
    assert REVIEW.source_coenergy(A, current, fcoil, .05, .02) == pytest.approx(.17)
