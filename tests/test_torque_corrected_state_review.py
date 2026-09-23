"""Synthetic guards for the read-only selected-P2-state review."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("torque_corrected_state_review", SCRIPTS / "torque_corrected_state_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def _metadata():
    return dict(eddy=False, demag=False, rotor_eddy=False, frozen_nu=False,
                voltage_drive=False, imposed_current_drive=True,
                newton_converged=True, picard_unconverged=False,
                n_parallel=2, n_sectors=2, stack_length_m=.01, pole_pairs=7,
                psi_abc_Wb={"A": .02, "B": -.03, "C": .01})


def test_metadata_rejects_loss_state_and_scaling_mismatch():
    reference = _metadata()
    review.check_frame_metadata(reference, reference)
    for change in ({"eddy": True}, {"demag": True}, {"rotor_eddy": True},
                   {"frozen_nu": True}, {"voltage_drive": True},
                   {"imposed_current_drive": False}, {"newton_converged": False},
                   {"n_parallel": 1}, {"n_sectors": 4},
                   {"stack_length_m": .02}, {"pole_pairs": 14}):
        with pytest.raises(ValueError):
            review.check_frame_metadata(dict(reference, **change), reference)


def test_source_linkage_is_recomputed_from_saved_A_and_source_vectors():
    meta = _metadata()
    state = dict(A_z=np.array([2., 3.]),
                 coil_source_vectors_per_A__A=np.array([1., 0.]),
                 coil_source_vectors_per_A__B=np.array([0., -1.]),
                 coil_source_vectors_per_A__C=np.array([.5, 0.]))
    assert review.source_linkage_mismatch(meta, state) == pytest.approx({"A": 0., "B": 0., "C": 0.})
    state["A_z"] = state["A_z"] + np.array([.1, 0.])
    assert review.source_linkage_mismatch(meta, state)["A"] == pytest.approx(-.001)


def test_piecewise_energy_derivative_and_zero_field():
    curve = [(0., 0.), (1000., .1), (3000., .2)]
    b = np.array([0., .05, .1, .15, .2, .25])
    H, U = review.curve_h_energy(b, curve)
    assert H[0] == U[0] == 0.
    assert H[2] == pytest.approx(1000.)
    assert U[2] == pytest.approx(50.)
    eps = 1e-7
    for value in (.05, .15, .25):
        numerical = (review.curve_h_energy(np.array([value+eps]), curve)[1][0]
                     - review.curve_h_energy(np.array([value-eps]), curve)[1][0])/(2*eps)
        analytical = review.curve_h_energy(np.array([value]), curve)[0][0]
        assert numerical == pytest.approx(analytical, rel=1e-8)
