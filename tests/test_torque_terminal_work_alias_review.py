"""Synthetic checks for exact bilinear Fourier-alias attribution."""
from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from scripts import torque_terminal_work_alias_review as review


def _source(order_current: int = 1, order_psi: int = 21):
    n, pp = 120, 14
    theta = 2 * np.pi * np.arange(n) / (n * pp)
    electrical = pp * theta
    shifts = (0.17, -2 * np.pi / 3 + 0.17, 2 * np.pi / 3 + 0.17)
    current = np.stack([np.cos(order_current * electrical + phase) for phase in shifts]) * 3.0
    psi = np.stack([0.1 * np.sin(order_psi * electrical + phase) for phase in shifts])
    return current, psi, theta


def test_order1_times_order21_is_fully_attributed_across_every_n20_offset():
    current, psi, theta = _source()
    before = (current.copy(), psi.copy(), theta.copy())
    expected = []
    for offset in range(6):
        result = review.attribute_decimation(current, psi, theta,
                                             sample_count=20, phase_offset=offset)
        expected_value = 3 * 3.0 * 0.1 * 14 / 2 * np.cos(np.pi * offset / 3)
        expected.append(expected_value)
        assert result["coarse_delta_vs_120_Nm"] == pytest.approx(expected_value, abs=2e-11)
        assert result["pair_expanded_delta_vs_120_Nm"] == pytest.approx(expected_value, abs=2e-11)
        assert abs(result["complex_delta_imaginary_residual_Nm"]) < 5e-11
        assert result["delta_attribution_Nm"]["at_least_one_source_order_above_target_nyquist"] \
            == pytest.approx(expected_value, abs=2e-11)
        assert result["delta_attribution_Nm"]["both_source_orders_within_target_nyquist"] \
            == pytest.approx(0.0, abs=2e-11)
        assert result["pair_rows_per_phase"] == 20 * 6**2
        assert result["source_pair_count_all_phases"] == 3 * 20 * 6**2
        alias_rows = result["source_pair_rows_by_phase_and_coarse_bin"]["A"]["1"]
        assert len(alias_rows) == 36
        assert any(row[1] == 1 and row[3] == 21 and abs(row[8]) > 1e-10 for row in alias_rows)
        assert len(result["per_coarse_bin"]) == 20
    np.testing.assert_allclose(expected, [6.3, 3.15, -3.15, -6.3, -3.15, 3.15], atol=2e-11)
    np.testing.assert_array_equal(current, before[0])
    np.testing.assert_array_equal(psi, before[1])
    np.testing.assert_array_equal(theta, before[2])


@pytest.mark.parametrize("count", [20, 30, 40, 60, 120])
def test_every_decimation_offset_closes_and_retains_all_ordered_pairs(count):
    current, psi, theta = _source()
    stride = 120 // count
    for offset in range(stride):
        result = review.attribute_decimation(current, psi, theta,
                                             sample_count=count, phase_offset=offset)
        assert result["pair_expanded_delta_vs_120_Nm"] == pytest.approx(
            result["coarse_delta_vs_120_Nm"], abs=5e-11)
        assert result["direct_coarse_minus_pair_fold_fft_max_abs_error"] < 2e-12
        assert len(result["per_coarse_bin"]) == count
        for bin_rows in result["source_pair_rows_by_phase_and_coarse_bin"]["A"].values():
            aliases_per_axis = stride
            assert len(bin_rows) == aliases_per_axis**2


def test_nyquist_is_present_and_production_zero_derivative_is_used():
    current, psi, theta = _source(order_current=10, order_psi=10)
    result = review.attribute_decimation(current, psi, theta,
                                         sample_count=20, phase_offset=0)
    nyquist = result["per_coarse_bin"][10]
    assert nyquist["coarse_signed_order"] == -10
    assert nyquist["effective_derivative_multiplier_Wb_per_rad"] == {"real": 0.0, "imag": 0.0}
    rows = result["source_pair_rows_by_phase_and_coarse_bin"]["A"]["10"]
    assert len(rows) == 36
    assert all(row[4] == pytest.approx(0.0, abs=1e-13) for row in rows)
    assert any(abs(row[6]) + abs(row[7]) > 1e-8 for row in rows)


def test_invalid_shapes_and_phase_offsets_reject():
    current, psi, theta = _source()
    with pytest.raises(ValueError, match="shape"):
        review.attribute_decimation(current[:, :-1], psi, theta,
                                    sample_count=20, phase_offset=0)
    with pytest.raises(ValueError, match="phase_offset"):
        review.attribute_decimation(current, psi, theta,
                                    sample_count=20, phase_offset=6)


def test_analyze_archive_rejects_unvalidated_or_non_ns4_source():
    with pytest.raises(ValueError, match="validation"):
        review.analyze_archive({"passed": False})
    with pytest.raises(ValueError, match="NS4"):
        review.analyze_archive({"passed": True, "provenance": {"effective_ns": 2}})


def test_cli_emits_json_and_returns_nonzero_for_a_failed_run(monkeypatch, capsys, tmp_path):
    expected = {"passed": True, "arithmetic_gate_passed": True, "certified": False}
    monkeypatch.setattr(review, "run_review", lambda: expected)
    output = tmp_path / "result.json"
    assert review.main(["--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert json.loads(output.read_text(encoding="utf-8")) == expected

    def fail():
        raise ValueError("synthetic archive mismatch")

    monkeypatch.setattr(review, "run_review", fail)
    assert review.main([]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["certified"] is False
    assert "synthetic archive mismatch" in payload["errors"][0]
