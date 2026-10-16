"""Synthetic tests for terminal-work symmetry review; no saved solve/API use."""
from __future__ import annotations

import copy
import math

import numpy as np
import pytest

from scripts import torque_terminal_work_sector_review as review
from motor_ai_sim.simulation.sb_postproc import terminal_work_mean


def _modes(*, nyquist=False):
    n = 20
    theta = 2 * np.pi * np.arange(n) / (n * 14)
    current = np.stack([
        60 * np.sqrt(2) * np.cos(14 * theta + phase)
        for phase in (math.pi / 3, math.pi / 3 - 2 * math.pi / 3,
                      math.pi / 3 + 2 * math.pi / 3)
    ])
    psi = np.stack([
        .02 * np.sin(14 * theta + phase) + .003 * np.cos(2 * 14 * theta - phase)
        for phase in (0.2, -1.1, 1.7)
    ])
    if nyquist:
        psi[0] += 0.001 * (-1.0) ** np.arange(n)
    modes = {}
    for ns in (1, 2, 4):
        modes[ns] = {
            "effective_ns": ns,
            "bc_sign": review.EXPECTED_SIGNS[ns],
            "angle_rad": theta.copy(),
            "currents_A": current.copy(),
            "linkages_Wb": psi.copy(),
            "raw_maxwell_Nm": (40 + np.cos(14 * theta)).copy(),
        }
    return modes


def test_production_helper_exact_spectra_all_samples_and_no_mutation():
    modes = _modes(nyquist=True)
    before = copy.deepcopy(modes)
    result = review.evaluate_modes(modes, parity_validation={"passed": True})
    assert result["certified"] is False
    assert result["all_samples_and_bins_retained"] is True
    assert all(len(row["full_dft_all_bins_by_phase"]["A"]["linkage"]) == 20
               for row in result["modes"].values())
    for ns in (1, 2, 4):
        row = result["modes"][str(ns)]
        source = modes[ns]
        expected = terminal_work_mean(
            *source["linkages_Wb"], *source["currents_A"], source["angle_rad"],
            14, n_parallel=1)
        assert row["production_terminal_work_mean_Nm"] == pytest.approx(expected, abs=1e-14)
        assert row["sample_count"] == 20
        for phase in "ABC":
            spectra = row["full_dft_all_bins_by_phase"][phase]
            assert len(spectra["current"]) == len(spectra["linkage"]) == 20
            assert len(spectra["derivative_operator_applied_to_linkage"]) == 20
            assert len(spectra["effective_real_grid_derivative"]) == 20
        assert row["nyquist_bin"]["effective_derivative_bin_max_abs"] < 1e-12
    assert modes.keys() == before.keys()
    for ns in modes:
        for key in modes[ns]:
            np.testing.assert_array_equal(modes[ns][key], before[ns][key])


def test_provenance_failure_and_mode_mismatches_reject():
    with pytest.raises(ValueError, match="parity/provenance"):
        review.evaluate_modes(_modes(), parity_validation={"passed": False, "errors": ["hash"]})

    modes = _modes()
    modes[4]["bc_sign"] = 1
    with pytest.raises(ValueError, match="boundary sign"):
        review.evaluate_modes(modes, parity_validation={"passed": True})

    modes = _modes()
    modes[2]["angle_rad"][4] += 1e-6
    with pytest.raises(ValueError, match="angles differ"):
        review.evaluate_modes(modes, parity_validation={"passed": True})

    modes = _modes()
    modes[4]["currents_A"][0, 3] += 1e-6
    with pytest.raises(ValueError, match="currents differ"):
        review.evaluate_modes(modes, parity_validation={"passed": True})


def test_wrong_effective_ns_and_nonpositive_angles_reject():
    modes = _modes()
    modes[2]["effective_ns"] = 1
    with pytest.raises(ValueError, match="effective built"):
        review.evaluate_modes(modes, parity_validation={"passed": True})
    modes = _modes()
    modes[1]["angle_rad"] = modes[1]["angle_rad"][::-1].copy()
    with pytest.raises(ValueError, match="positive signed order"):
        review.evaluate_modes(modes, parity_validation={"passed": True})


def test_provisional_mean_gate_fails_when_terminal_linkage_is_inconsistent():
    modes = _modes()
    modes[4]["linkages_Wb"] *= 1.2
    result = review.evaluate_modes(modes, parity_validation={"passed": True})
    assert result["certified"] is False
    assert result["passed"] is False
    assert result["comparisons_vs_ns1"]["4"]["terminal_work_mean_gate"]["passed"] is False


def test_cli_returns_nonzero_on_failed_raw_parity(monkeypatch, capsys):
    monkeypatch.setattr(review, "run_review", lambda root: {
        "passed": False, "certified": False, "errors": ["synthetic provenance mismatch"]
    })
    assert review.main([]) == 2
    assert "synthetic provenance mismatch" in capsys.readouterr().out
