#!/usr/bin/env python
"""Exact bilinear alias attribution for archived NS4 terminal-work sampling."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from motor_ai_sim.simulation.sb_postproc import terminal_work_mean  # noqa: E402
from scripts import torque_terminal_work_sampling_review as sampling  # noqa: E402

SOURCE_COUNT = 120
PHASES = "ABC"
POLE_PAIRS = 14
N_PARALLEL = 1
ARITHMETIC_ATOL_NM = 5e-11
TARGET_COUNTS = (20, 30, 40, 60, 120)


def _complex_json(value: complex) -> dict[str, float]:
    return {"real": float(value.real), "imag": float(value.imag)}


def _signed_orders(count: int) -> list[int]:
    return [int(round(float(value * count))) for value in np.fft.fftfreq(count)]


def attribute_decimation(
    source_current_A: np.ndarray,
    source_psi_Wb: np.ndarray,
    source_angle_rad: np.ndarray,
    *,
    sample_count: int,
    phase_offset: int,
    helper: Callable[..., float] = terminal_work_mean,
) -> dict[str, Any]:
    """Expand coarse work into every ordered 120-bin current/linkage pair.

    Source and coarse coefficients are normalized full complex FFTs. Rows are
    retained even when their numerical contribution is zero. Production's
    real-grid Nyquist derivative convention is represented by a zero multiplier.
    """
    current = np.asarray(source_current_A, dtype=float)
    psi = np.asarray(source_psi_Wb, dtype=float)
    angle = np.asarray(source_angle_rad, dtype=float)
    if current.shape != (3, SOURCE_COUNT) or psi.shape != (3, SOURCE_COUNT):
        raise ValueError("source current and linkage must have shape (3,120)")
    if angle.shape != (SOURCE_COUNT,) or not all(np.all(np.isfinite(v)) for v in (current, psi, angle)):
        raise ValueError("source arrays must be finite with 120 actual mechanical angles")
    if sample_count not in TARGET_COUNTS or SOURCE_COUNT % sample_count:
        raise ValueError("sample_count must be one of 20, 30, 40, 60, 120")
    stride = SOURCE_COUNT // sample_count
    if not 0 <= phase_offset < stride:
        raise ValueError("phase_offset must lie in [0, stride)")
    steps = np.diff(angle)
    if not np.all(steps > 0) or not np.allclose(steps, steps[0], rtol=0, atol=1e-14):
        raise ValueError("source angles must be uniformly increasing signed mechanical radians")

    indices = np.arange(phase_offset, SOURCE_COUNT, stride, dtype=int)
    coarse_angle = angle[indices].copy()
    coarse_current = current[:, indices].copy()
    coarse_psi = psi[:, indices].copy()
    source_step = float(steps[0])
    source_omega = 2 * np.pi * np.fft.fftfreq(SOURCE_COUNT, d=source_step)
    coarse_omega = 2 * np.pi * np.fft.fftfreq(sample_count, d=source_step * stride)
    source_i_hat = np.fft.fft(current, axis=1) / SOURCE_COUNT
    source_psi_hat = np.fft.fft(psi, axis=1) / SOURCE_COUNT
    source_signed = _signed_orders(SOURCE_COUNT)
    coarse_signed = _signed_orders(sample_count)
    phase_factor = lambda q, p: np.exp(2j * np.pi * (p - q) * phase_offset / SOURCE_COUNT)

    coarse_work = float(helper(*coarse_psi, *coarse_current, coarse_angle,
                               POLE_PAIRS, n_parallel=N_PARALLEL))
    reference_work = float(helper(*psi, *current, angle, POLE_PAIRS, n_parallel=N_PARALLEL))
    per_bin: list[dict[str, Any]] = []
    category_totals = {
        "both_source_orders_within_target_nyquist": 0j,
        "at_least_one_source_order_above_target_nyquist": 0j,
        "high_linkage_source_into_coarse_pm1": 0j,
        "current_high_source_any_bin": 0j,
    }
    source_nyquist = sample_count // 2
    source_pair_count = 0
    all_rows_by_phase_bin: dict[str, dict[str, list[list[float | int]]]] = {
        phase: {str(k): [] for k in range(sample_count)} for phase in PHASES
    }
    for k, target_order in enumerate(coarse_signed):
        q_indices = [q for q in range(SOURCE_COUNT) if q % sample_count == k]
        p_indices = q_indices
        if sample_count % 2 == 0 and k == sample_count // 2:
            derivative_multiplier = 0j
        else:
            derivative_multiplier = 1j * coarse_omega[k]
        bin_coarse = 0j
        bin_reference = 0j
        for phase_i, phase in enumerate(PHASES):
            phase_coarse = 0j
            phase_ref = 0j
            for q in q_indices:
                for p in p_indices:
                    factor = phase_factor(q, p)
                    cross = np.conj(source_i_hat[phase_i, q]) * source_psi_hat[phase_i, p]
                    coarse_term = complex(cross * factor * derivative_multiplier * N_PARALLEL)
                    if q == p and q != SOURCE_COUNT // 2:
                        ref_term = complex(np.conj(source_i_hat[phase_i, q])
                                           * (1j * source_omega[q] * source_psi_hat[phase_i, q])
                                           * N_PARALLEL)
                    else:
                        ref_term = 0j
                    delta_term = coarse_term - ref_term
                    phase_coarse += coarse_term
                    phase_ref += ref_term
                    source_pair_count += 1
                    high_i = abs(source_signed[q]) > source_nyquist
                    high_psi = abs(source_signed[p]) > source_nyquist
                    if high_i or high_psi:
                        category_totals["at_least_one_source_order_above_target_nyquist"] += delta_term
                    else:
                        category_totals["both_source_orders_within_target_nyquist"] += delta_term
                    if high_i:
                        category_totals["current_high_source_any_bin"] += delta_term
                    if target_order in (-1, 1) and high_psi:
                        category_totals["high_linkage_source_into_coarse_pm1"] += delta_term
                    all_rows_by_phase_bin[phase][str(k)].append([
                        q, source_signed[q], p, source_signed[p],
                        coarse_term.real, coarse_term.imag,
                        ref_term.real, ref_term.imag,
                        delta_term.real, delta_term.imag,
                    ])
            bin_coarse += phase_coarse
            bin_reference += phase_ref
        bin_delta = bin_coarse - bin_reference
        per_bin.append({
            "coarse_fft_bin": k,
            "coarse_signed_order": target_order,
            "effective_derivative_multiplier_Wb_per_rad": _complex_json(derivative_multiplier),
            "coarse_complex_work_sum_Nm": _complex_json(bin_coarse),
            "reference_diagonal_subtraction_Nm": _complex_json(bin_reference),
            "delta_complex_contribution_Nm": _complex_json(bin_delta),
            "source_current_linkage_pair_count_per_phase": len(q_indices) ** 2,
        })

    coarse_delta = coarse_work - reference_work
    expanded_delta = float(sum(row["delta_complex_contribution_Nm"]["real"] for row in per_bin))
    imaginary_delta = float(sum(row["delta_complex_contribution_Nm"]["imag"] for row in per_bin))
    folded_error = 0.0
    coarse_i_hat = np.fft.fft(coarse_current, axis=1) / sample_count
    coarse_psi_hat = np.fft.fft(coarse_psi, axis=1) / sample_count
    for phase_i in range(3):
        for k in range(sample_count):
            folded_i = sum(source_i_hat[phase_i, q] * np.exp(2j * np.pi * q * phase_offset / SOURCE_COUNT)
                           for q in range(k, SOURCE_COUNT, sample_count))
            folded_psi = sum(source_psi_hat[phase_i, q] * np.exp(2j * np.pi * q * phase_offset / SOURCE_COUNT)
                             for q in range(k, SOURCE_COUNT, sample_count))
            folded_error = max(folded_error, abs(folded_i - coarse_i_hat[phase_i, k]),
                               abs(folded_psi - coarse_psi_hat[phase_i, k]))
    if abs(expanded_delta - coarse_delta) > ARITHMETIC_ATOL_NM:
        raise ValueError(f"pair expansion does not close to helper delta: {expanded_delta-coarse_delta:.3g} Nm")
    if abs(imaginary_delta) > ARITHMETIC_ATOL_NM:
        raise ValueError(f"pair expansion has imaginary residual {imaginary_delta:.3g} Nm")
    if folded_error > 2e-12:
        raise ValueError(f"complex alias fold does not match directly selected FFT: {folded_error:.3g}")

    return {
        "sample_count": sample_count,
        "stride": stride,
        "phase_offset": phase_offset,
        "source_indices_and_shifts": indices.tolist(),
        "coarse_production_terminal_work_mean_Nm": coarse_work,
        "reference_120_terminal_work_mean_Nm": reference_work,
        "coarse_delta_vs_120_Nm": coarse_delta,
        "pair_expanded_delta_vs_120_Nm": expanded_delta,
        "complex_delta_imaginary_residual_Nm": imaginary_delta,
        "direct_coarse_minus_pair_fold_fft_max_abs_error": float(folded_error),
        "pair_rows_per_phase": source_pair_count // 3,
        "per_coarse_bin": per_bin,
        "source_pair_row_layout": ["source_current_fft_index", "source_current_signed_order",
                                   "source_linkage_fft_index", "source_linkage_signed_order",
                                   "coarse_work_real_Nm", "coarse_work_imag_Nm",
                                   "reference_diagonal_real_Nm", "reference_diagonal_imag_Nm",
                                   "delta_real_Nm", "delta_imag_Nm"],
        "source_pair_rows_by_phase_and_coarse_bin": all_rows_by_phase_bin,
        "delta_attribution_Nm": {key: float(value.real) for key, value in category_totals.items()},
        "delta_attribution_imaginary_residual_Nm": {
            key: float(value.imag) for key, value in category_totals.items()},
        "source_pair_count_all_phases": source_pair_count,
    }


def analyze_archive(review: Mapping[str, Any], *, tolerance_nm: float = ARITHMETIC_ATOL_NM) -> dict[str, Any]:
    """Analyze the strict merged run13/run14 archive from its existing validator."""
    if not isinstance(review, Mapping) or review.get("passed") is not True:
        raise ValueError("sampling archive validation must pass")
    if int(review.get("provenance", {}).get("effective_ns", -1)) != 4:
        raise ValueError("this attribution is bounded to archived NS4")
    ref = review.get("reference_120")
    if not isinstance(ref, Mapping):
        raise ValueError("validated merged reference_120 is missing")
    angle = np.asarray(ref["mechanical_angle_rad"], dtype=float)
    current = np.asarray([ref["phase_current_A"][phase] for phase in PHASES], dtype=float)
    psi = np.asarray([ref["phase_flux_linkage_Wb"][phase] for phase in PHASES], dtype=float)
    if angle.shape != (120,) or current.shape != (3, 120) or psi.shape != (3, 120):
        raise ValueError("validated archive arrays have unexpected shape")
    modes: dict[str, Any] = {}
    errors: list[str] = []
    for count in TARGET_COUNTS:
        stride = SOURCE_COUNT // count
        rows = []
        source_rows = review["grids"][str(count)]["phase_offset_rows"]
        if len(source_rows) != stride:
            raise ValueError(f"N={count}: expected all {stride} phase offsets")
        for source_row in source_rows:
            offset = int(source_row["phase_offset"])
            row = attribute_decimation(current, psi, angle,
                                       sample_count=count, phase_offset=offset)
            if row["source_indices_and_shifts"] != source_row["source_indices_and_shifts"]:
                raise ValueError(f"N={count} offset {offset}: source index mapping differs")
            if abs(row["coarse_production_terminal_work_mean_Nm"]
                   - float(source_row["production_terminal_work_mean_Nm"])) > tolerance_nm:
                raise ValueError(f"N={count} offset {offset}: production helper mismatch")
            if abs(row["coarse_delta_vs_120_Nm"]
                   - float(source_row["terminal_work_delta_vs_120_Nm"])) > tolerance_nm:
                raise ValueError(f"N={count} offset {offset}: sampling review delta mismatch")
            if abs(row["pair_expanded_delta_vs_120_Nm"] - row["coarse_delta_vs_120_Nm"]) > tolerance_nm:
                errors.append(f"N={count} offset {offset}: pair expansion arithmetic did not close")
            attribution = row["delta_attribution_Nm"]
            categorized_delta = (attribution["at_least_one_source_order_above_target_nyquist"]
                                 + attribution["both_source_orders_within_target_nyquist"])
            if abs(categorized_delta - row["coarse_delta_vs_120_Nm"]) > tolerance_nm:
                errors.append(f"N={count} offset {offset}: high/low source-order attribution did not close")
            rows.append(row)
        modes[str(count)] = {
            "phase_offset_count": stride,
            "phase_offset_rows": rows,
            "max_abs_delta_vs_120_Nm": max(abs(row["coarse_delta_vs_120_Nm"]) for row in rows),
            "all_offset_delta_vs_120_Nm": [row["coarse_delta_vs_120_Nm"] for row in rows],
        }
    n20_offset0 = modes["20"]["phase_offset_rows"][0]
    high = n20_offset0["delta_attribution_Nm"]["at_least_one_source_order_above_target_nyquist"]
    low = n20_offset0["delta_attribution_Nm"]["both_source_orders_within_target_nyquist"]
    n20_offset0_expected = n20_offset0["coarse_delta_vs_120_Nm"]
    if not math.isclose(high + low, n20_offset0_expected, rel_tol=0, abs_tol=tolerance_nm):
        errors.append("N20 offset0 high/low source-order attribution does not sum to delta")
    return {
        "passed": not errors,
        "arithmetic_gate_passed": not errors,
        "certified": False,
        "result_scope": "exact discrete bilinear alias attribution; no physical or continuum certificate",
        "errors": errors,
        "gate": {"pair_expansion_absolute_tolerance_Nm": tolerance_nm,
                 "phase_factor_fold_absolute_tolerance": 2e-12,
                 "all_phase_offsets_required": True,
                 "status": "arithmetic closure only"},
        "provenance": review.get("provenance"),
        "source_120": {
            "source_shifts": list(range(120)),
            "mechanical_angle_rad": angle.tolist(),
            "phase_current_A": {phase: current[i].tolist() for i, phase in enumerate(PHASES)},
            "phase_flux_linkage_Wb": {phase: psi[i].tolist() for i, phase in enumerate(PHASES)},
            "normalized_full_complex_current_spectrum_A": {
                phase: [_complex_json(c) for c in np.fft.fft(current[i]) / 120]
                for i, phase in enumerate(PHASES)},
            "normalized_full_complex_linkage_spectrum_Wb": {
                phase: [_complex_json(c) for c in np.fft.fft(psi[i]) / 120]
                for i, phase in enumerate(PHASES)},
            "signed_source_orders": _signed_orders(120),
            "interpretation": "120 positions are the finest archived integer-slip grid, not continuum truth; source orders above 60 are unobserved.",
        },
        "decimation_convention": {
            "normalized_complex_fold": "X_N[k] = sum(X_120[q]*exp(+2πi*q*r/120)) for q mod N=k",
            "bilinear_pair_term": "conj(I_120[q])*Psi_120[p]*exp(+2πi*(p-q)*r/120)*(i*omega_N[k]); q mod N=p mod N=k",
            "reference_subtraction": "subtract diagonal q=p 120-point work term; 120 Nyquist derivative is zero on production real grid",
            "coarse_nyquist": "even-N real-grid derivative multiplier is zero; all ordered current/linkage pairs are retained",
            "alias_pairing_is_ordered": True,
            "no_filtering_or_bin_discarding": True,
        },
        "coarse_grids": modes,
        "n20_offset0_high_order_attribution": {
            "coarse_delta_vs_120_Nm": n20_offset0_expected,
            "delta_terms_with_at_least_one_source_order_above_target_nyquist_Nm": high,
            "delta_terms_with_both_source_orders_within_target_nyquist_Nm": low,
            "high_linkage_source_orders_into_coarse_signed_plus_minus_one_Nm": n20_offset0["delta_attribution_Nm"]["high_linkage_source_into_coarse_pm1"],
            "high_current_source_order_involving_delta_any_bin_Nm": n20_offset0["delta_attribution_Nm"]["current_high_source_any_bin"],
            "definition": "high means |signed 120-grid source order| > N/2; attribution is exact in these finite samples and includes diagonal reference subtraction and q!=p cross terms.",
        },
        "limitations": [
            "No 120-position NS1 or NS2 data exist here; this cannot correct or attribute their coarse-grid differences.",
            "The 120-point archive is finite-grid reference only; this does not establish continuum physics or stored-energy closure.",
            "No filtering, demeaning, or harmonic deletion is applied; source and coarse Nyquist bins are retained.",
            "Bilinear aliasing contains ordered q,p cross terms; source work bins cannot be merely folded or summed independently.",
        ],
    }


def run_review() -> dict[str, Any]:
    """Load/validate the two archives through the existing strict sampling gate."""
    base = sampling.run_review()
    if base.get("passed") is not True:
        raise ValueError(f"run13/run14 archive validation failed: {base.get('errors', [])}")
    return analyze_archive(base)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args(argv)
    try:
        result = run_review()
    except Exception as exc:
        result = {"passed": False, "arithmetic_gate_passed": False, "certified": False,
                  "errors": [f"{type(exc).__name__}: {exc}"]}
    payload = json.dumps(result, sort_keys=True, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result.get("passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
