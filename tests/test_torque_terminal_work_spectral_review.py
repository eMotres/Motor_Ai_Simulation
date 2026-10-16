"""Synthetic full-bin Parseval/aliasing checks; no FEM or API use."""
from __future__ import annotations

import copy
import numpy as np
import pytest

from motor_ai_sim.simulation.sb_postproc import terminal_work_mean
from scripts import torque_terminal_work_spectral_review as review


def _modes():
    n, pp = 20, 14
    theta = 2 * np.pi * np.arange(n) / (n * pp)
    phase = pp * theta
    current = np.stack([
        np.cos(phase + offset) for offset in (0.1, -2 * np.pi / 3 + 0.1,
                                               2 * np.pi / 3 + 0.1)
    ]) * 25.0
    psi_base = np.stack([
        0.012 * np.sin(phase + offset)
        + 0.002 * np.sin(3 * phase + offset)
        + 0.004 * np.sin(21 * phase + offset)  # aliases exactly onto order 1 at N=20
        + 0.003 * np.cos(10 * phase)           # retained linkage Nyquist; derivative convention is zero
        for offset in (0.2, -2 * np.pi / 3 + 0.2, 2 * np.pi / 3 + 0.2)
    ])
    modes = {}
    for ns, factor in ((1, 1.0), (2, 1.03), (4, 0.96)):
        modes[ns] = {
            "effective_ns": ns,
            "bc_sign": review.parity.EXPECTED_BC_SIGN[ns],
            "angle_rad": theta.copy(),
            "currents_A": current.copy(),
            "linkages_Wb": (factor * psi_base).copy(),
        }
    return modes


def test_full_complex_parseval_closes_helper_and_preserves_signed_pairs():
    modes = _modes()
    before = copy.deepcopy(modes)
    result = review.attribute_modes(modes, parity_validation={"passed": True})
    assert result["passed"] is True
    assert result["certified"] is False
    assert result["spectral_convention"]["retained_fft_bins"] == list(range(20))
    for ns in (1, 2, 4):
        row = result["modes"][str(ns)]
        expected = terminal_work_mean(
            *modes[ns]["linkages_Wb"], *modes[ns]["currents_A"],
            modes[ns]["angle_rad"], 14, n_parallel=1)
        assert row["production_terminal_work_mean_Nm"] == pytest.approx(expected, abs=2e-12)
        assert row["parseval_minus_production_Nm"] == pytest.approx(0.0, abs=2e-12)
        assert len(row["summed_all_signed_bins"]) == 20
        for phase in "ABC":
            bins = row["per_phase"][phase]["all_bins"]
            assert len(bins) == 20
            positive = bins[1]["complex_mean_contribution_Nm"]
            negative = bins[19]["complex_mean_contribution_Nm"]
            assert negative["real"] == pytest.approx(positive["real"], abs=1e-12)
            assert negative["imag"] == pytest.approx(-positive["imag"], abs=1e-12)
        nyquist = row["nyquist"]
        assert max(abs(nyquist["input_linkage_coefficient_Wb_by_phase"][p]["real"])
                   for p in "ABC") > 1e-4
        assert nyquist["real_grid_mean_contribution_Nm"] == pytest.approx(0.0, abs=1e-14)
        assert all(abs(nyquist["effective_real_derivative_coefficient_Wb_per_rad_by_phase"][p]["real"])
                   < 1e-12 for p in "ABC")
    for ns in modes:
        for key, values in modes[ns].items():
            np.testing.assert_array_equal(values, before[ns][key])


def test_orthogonal_order_contributes_zero_but_aliased_source_order_is_retained():
    phase = 2 * np.pi * np.arange(20) / 20
    np.testing.assert_allclose(np.sin(21 * phase), np.sin(phase), atol=2e-14, rtol=0)
    result = review.attribute_modes(_modes(), parity_validation={"passed": True})
    row = result["modes"]["1"]
    # order 3 has no current support on this exact grid: it contributes zero
    # by Parseval orthogonality; it remains present in the full coefficient set.
    order3 = row["summed_all_signed_bins"][3]
    assert abs(order3["sum_phase_real_mean_contribution_Nm"]) < 1e-12
    assert abs(row["per_phase"]["A"]["all_bins"][3]["linkage_coefficient_Wb"]["imag"]) > 1e-4
    # The deliberately generated 21st order aliases to sampled order 1 at N=20.
    order1 = row["per_phase"]["A"]["all_bins"][1]
    assert abs(order1["linkage_coefficient_Wb"]["imag"]) > 1e-3
    assert "alias" in result["spectral_convention"]["aliasing_note"]


def test_mode_delta_by_all_bins_closes_to_helper_delta_and_bad_provenance_rejects():
    modes = _modes()
    result = review.attribute_modes(modes, parity_validation={"passed": True})
    for ns in (2, 4):
        comparison = result["mean_deltas_vs_ns1"][str(ns)]
        assert comparison["parseval_delta_vs_ns1_Nm"] == pytest.approx(
            comparison["production_mean_delta_vs_ns1_Nm"], abs=2e-12)
        assert len(comparison["all_signed_bin_deltas"]) == 20
        assert len(comparison["per_phase_bin_deltas_Nm"]["A"]) == 20
    with pytest.raises(ValueError, match="parity/provenance"):
        review.attribute_modes(modes, parity_validation={"passed": False, "errors": ["hash"]})


def test_parseval_conjugation_and_mechanical_angle_sign_against_analytic_mean():
    n, pp = 20, 14
    theta = 2 * np.pi * np.arange(n) / (n * pp)
    electrical = pp * theta
    current = np.tile(2.0 * np.cos(electrical), (3, 1))
    psi = np.tile(0.1 * np.sin(electrical), (3, 1))
    expected = 3 * 2.0 * 0.1 * pp / 2
    for sign in (1, -1):
        modes = {ns: {"effective_ns": ns, "bc_sign": review.parity.EXPECTED_BC_SIGN[ns],
                      "angle_rad": theta.copy(), "currents_A": current.copy(),
                      "linkages_Wb": sign * psi.copy()}
                 for ns in (1, 2, 4)}
        result = review.attribute_modes(modes, parity_validation={"passed": True})
        row = result["modes"]["1"]
        assert row["parseval_full_complex_bin_sum_Nm"] == pytest.approx(sign * expected, abs=1e-12)
        assert row["production_terminal_work_mean_Nm"] == pytest.approx(sign * expected, abs=1e-12)
        assert row["summed_all_signed_bins"][1]["sum_phase_real_mean_contribution_Nm"] \
            == pytest.approx(sign * expected / 2, abs=1e-12)
        assert row["summed_all_signed_bins"][19]["sum_phase_real_mean_contribution_Nm"] \
            == pytest.approx(sign * expected / 2, abs=1e-12)
