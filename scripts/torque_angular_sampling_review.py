#!/usr/bin/env python
"""Offline all-phase angular sampling review of archived NS4 torque data."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVEN_REVIEW = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4/period_grid_review.json"
DEFAULT_EVEN_FRAMES = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4"
DEFAULT_ODD_REVIEW = ROOT / "scratchpad/torque_odd_slip_20260923_run14/ns4/odd_grid_review.json"
DEFAULT_ODD_FRAMES = ROOT / "scratchpad/torque_odd_slip_20260923_run14/ns4"
EXPECTED_SOLVER_SHA256 = "67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7"
EXPECTED_CONFIG_SHA256 = "f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8"
EXPECTED_MATERIAL_SHA256 = "80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136"
SOURCE_COUNT = 120
SOURCE_SHIFTS = tuple(range(SOURCE_COUNT))
GRID_STRIDES = {20: 6, 30: 4, 40: 3, 60: 2, 120: 1}
MEAN_DELTA_LIMIT_NM = 0.01
RIPPLE_RELATIVE_LIMIT = 0.01
EXTREMA_DELTA_LIMIT_NM = 0.01
REQUIRED_FRAME_KEYS = ("torque_sector_Nm", "currents_A", "shift", "angle_deg", "bc_sign",
                       "slip_nodes", "slip_spacing_deg", "newton_ok", "residual")


def _finite_vector(values: Any, name: str, expected: int) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.shape != (expected,) or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must contain exactly {expected} finite values")
    return result


def _single_sided_spectrum(samples: np.ndarray) -> list[dict[str, Any]]:
    coeff = np.fft.rfft(samples) / samples.size
    rows = []
    for k, c in enumerate(coeff):
        doubled = k != 0 and not (samples.size % 2 == 0 and k == samples.size // 2)
        rows.append({"bin": k, "electrical_order": k,
                     "coefficient_real_Nm": float(c.real),
                     "coefficient_imag_Nm": float(c.imag),
                     "single_sided_peak_amplitude_Nm": float(abs(c) * (2 if doubled else 1)),
                     "amplitude_doubled": doubled})
    return rows


def alias_groups(source_count: int = SOURCE_COUNT, target_count: int = 20) -> list[dict[str, Any]]:
    """Full-DFT source congruence classes for every target rFFT bin."""
    if source_count <= 0 or target_count <= 0 or source_count % target_count:
        raise ValueError("target_count must be a positive divisor of source_count")
    return [{"target_bin": k,
             "source_full_fft_bins": [j for j in range(source_count) if j % target_count == k]}
            for k in range(target_count // 2 + 1)]


def _validate_source(review: Mapping[str, Any], frames: Sequence[Mapping[str, Any]],
                     expected_shifts: Sequence[int], label: str) -> tuple[np.ndarray, list[str]]:
    errors: list[str] = []
    count = len(expected_shifts)
    if review.get("sector") != 4 or review.get("bc_sign") != -1:
        errors.append(f"{label}: expected effective NS4 and anti-periodic BC sign -1")
    for key, expected in (("solver_sha256", EXPECTED_SOLVER_SHA256),
                          ("config_sha256", EXPECTED_CONFIG_SHA256),
                          ("material_library_sha256", EXPECTED_MATERIAL_SHA256)):
        if review.get(key) != expected:
            errors.append(f"{label}: {key} mismatch")
    if list(review.get("shifts", [])) != list(expected_shifts):
        errors.append(f"{label}: source shift sequence mismatch")
    try:
        torque = _finite_vector(review.get("torque_scaled_Nm", []), f"{label} torque", count)
        angles = _finite_vector(review.get("angle_deg", []), f"{label} angles", count)
        residuals = _finite_vector(review.get("residual", []), f"{label} residuals", count)
        currents = np.asarray(review.get("current_A", []), dtype=float)
        if currents.shape != (count, 3) or not np.all(np.isfinite(currents)):
            errors.append(f"{label}: current_A must contain {count} finite phase triplets")
        else:
            theta_e = 14.0 * np.deg2rad(np.asarray(expected_shifts) * 360.0 / 1680.0)
            expected_currents = 60.0 * np.sqrt(2.0) * np.column_stack((
                np.cos(theta_e + np.pi / 3.0),
                np.cos(theta_e - np.pi / 3.0),
                np.cos(theta_e + np.pi),
            ))
            if not np.allclose(currents, expected_currents, rtol=0, atol=1e-10):
                errors.append(f"{label}: current_A differs from the prescribed 60 A RMS synchronous law")
        convergence = review.get("newton_ok", [])
        if len(convergence) != count or not all(v is True for v in convergence):
            errors.append(f"{label}: all {count} strict Newton flags must be true")
        if review.get("slip_nodes") != 1680 or not math.isclose(
                float(review.get("slip_spacing_deg", float("nan"))), 360.0 / 1680.0,
                rel_tol=0, abs_tol=1e-12):
            errors.append(f"{label}: review slip-grid metadata mismatch")
        geometry = review.get("geometry", {})
        if not isinstance(geometry, dict) or geometry.get("num_slots") != 24 \
                or geometry.get("num_poles") != 28:
            errors.append(f"{label}: expected G150 24-slot/28-pole geometry")
        if len(frames) != count:
            errors.append(f"{label}: expected {count} raw frames, got {len(frames)}")
        else:
            step_deg = 360.0 / 1680.0
            for i, frame in enumerate(frames):
                missing = [key for key in REQUIRED_FRAME_KEYS if key not in frame]
                if missing:
                    errors.append(f"{label} frame {i}: missing {', '.join(missing)}")
                    continue
                shift = int(expected_shifts[i])
                if int(frame["shift"]) != shift:
                    errors.append(f"{label} frame {i}: shift mismatch")
                if int(frame["bc_sign"]) != -1 or int(frame["slip_nodes"]) != 1680:
                    errors.append(f"{label} frame {i}: BC/slip-node metadata mismatch")
                angle = shift * step_deg
                if not math.isclose(float(frame["angle_deg"]), angle, rel_tol=0, abs_tol=1e-12) \
                        or not math.isclose(angles[i], angle, rel_tol=0, abs_tol=1e-12):
                    errors.append(f"{label} frame {i}: angle mismatch")
                if not math.isclose(float(frame["slip_spacing_deg"]), step_deg,
                                    rel_tol=0, abs_tol=1e-12):
                    errors.append(f"{label} frame {i}: slip spacing mismatch")
                if frame["newton_ok"] is not True:
                    errors.append(f"{label} frame {i}: strict convergence flag is not true")
                if not math.isclose(float(frame["residual"]), residuals[i], rel_tol=0, abs_tol=1e-20):
                    errors.append(f"{label} frame {i}: residual differs from review")
                if not math.isclose(4.0 * float(frame["torque_sector_Nm"]), torque[i],
                                    rel_tol=0, abs_tol=1e-10):
                    errors.append(f"{label} frame {i}: raw sector torque times 4 differs from review")
                fcurr = np.asarray(frame["currents_A"], dtype=float)
                if fcurr.shape != (3,) or not np.all(np.isfinite(fcurr)) \
                        or not np.allclose(fcurr, currents[i], rtol=0, atol=1e-12):
                    errors.append(f"{label} frame {i}: current differs from review")
        return torque, errors
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        errors.append(f"{label}: invalid archive data: {exc}")
        return np.empty(0), errors


def _grid_metrics(values: np.ndarray, indices: list[int], offset: int,
                  stride: int) -> dict[str, Any]:
    min_local, max_local = int(np.argmin(values)), int(np.argmax(values))
    return {
        "phase_offset": offset,
        "stride": stride,
        "source_indices": indices,
        "source_shifts": indices,
        "raw_torque_Nm": values.tolist(),
        "sample_mean_Nm": float(np.mean(values)),
        "sample_minimum_Nm": float(values[min_local]),
        "sample_minimum_source_index": indices[min_local],
        "sample_maximum_Nm": float(values[max_local]),
        "sample_maximum_source_index": indices[max_local],
        "sample_peak_to_peak_Nm": float(np.ptp(values)),
        "full_rfft_bins": _single_sided_spectrum(values),
    }


def analyze_sampling(even_review: Mapping[str, Any], even_frames: Sequence[Mapping[str, Any]],
                     odd_review: Mapping[str, Any], odd_frames: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate 2×60 archived frames, merge 120 shifts, and test all nested phases."""
    even_values, errors_even = _validate_source(even_review, even_frames, range(0, 120, 2), "even archive")
    odd_values, errors_odd = _validate_source(odd_review, odd_frames, range(1, 120, 2), "odd archive")
    errors = errors_even + errors_odd
    for key in ("geometry", "n_dofs", "n_elements", "slip_nodes", "slip_spacing_deg",
                "solver_sha256", "config_sha256", "material_library_sha256"):
        if even_review.get(key) != odd_review.get(key):
            errors.append(f"even/odd archive {key} provenance differs")
    if even_values.size == 60 and odd_values.size == 60:
        merged = np.empty(SOURCE_COUNT, dtype=float)
        merged_shifts = [None] * SOURCE_COUNT
        merged[0::2], merged[1::2] = even_values, odd_values
        merged_shifts[0::2] = list(range(0, 120, 2))
        merged_shifts[1::2] = list(range(1, 120, 2))
        if merged_shifts != list(SOURCE_SHIFTS):
            errors.append("merged source shifts are not exact 0..119")
        if "combined_shifts" in odd_review and odd_review["combined_shifts"] != list(range(120)):
            errors.append("odd archive embedded combined_shifts differs from reconstructed merge")
        if "combined_angles_deg" in odd_review and not np.allclose(
                odd_review["combined_angles_deg"],
                [s * 360.0 / 1680.0 for s in range(120)], rtol=0, atol=1e-12):
            errors.append("odd archive embedded combined_angles_deg differs from reconstructed merge")
        if "combined_torque_Nm" in odd_review and not np.allclose(
                odd_review["combined_torque_Nm"], merged, rtol=0, atol=1e-12):
            errors.append("odd archive embedded combined torque differs from reconstructed merge")
        summaries = odd_review.get("combined_even_odd", {})
        if "120" in summaries:
            row = summaries["120"]
            if not math.isclose(float(row.get("mean_Nm", float("nan"))), float(merged.mean()), abs_tol=1e-12) \
                    or not math.isclose(float(row.get("peak_to_peak_Nm", float("nan"))),
                                        float(np.ptp(merged)), abs_tol=1e-12):
                errors.append("embedded 120-point summary differs from reconstructed merge")
    else:
        merged = np.empty(0, dtype=float)
    if errors:
        return {"passed": False, "errors": errors}

    reference = _grid_metrics(merged.copy(), list(range(120)), 0, 1)
    grids: dict[str, Any] = {}
    mean_gate_ok = True
    ripple_gate_ok = True
    for count, stride in GRID_STRIDES.items():
        phases = []
        phase_mean_deltas, phase_pp_deltas = [], []
        phase_min_deltas, phase_max_deltas = [], []
        for offset in range(stride):
            indices = list(range(offset, SOURCE_COUNT, stride))
            values = merged[offset::stride].copy()
            row = _grid_metrics(values, indices, offset, stride)
            row["mean_delta_vs_120_Nm"] = row["sample_mean_Nm"] - reference["sample_mean_Nm"]
            row["minimum_delta_vs_120_Nm"] = row["sample_minimum_Nm"] - reference["sample_minimum_Nm"]
            row["maximum_delta_vs_120_Nm"] = row["sample_maximum_Nm"] - reference["sample_maximum_Nm"]
            row["peak_to_peak_delta_vs_120_Nm"] = row["sample_peak_to_peak_Nm"] - reference["sample_peak_to_peak_Nm"]
            phases.append(row)
            phase_mean_deltas.append(row["mean_delta_vs_120_Nm"])
            phase_pp_deltas.append(row["peak_to_peak_delta_vs_120_Nm"])
            phase_min_deltas.append(row["minimum_delta_vs_120_Nm"])
            phase_max_deltas.append(row["maximum_delta_vs_120_Nm"])
        max_mean_delta = max(abs(v) for v in phase_mean_deltas)
        max_pp_rel_delta = max(abs(v) for v in phase_pp_deltas) / max(
            abs(reference["sample_peak_to_peak_Nm"]), np.finfo(float).tiny)
        max_extrema_delta = max(max(abs(v) for v in phase_min_deltas),
                                max(abs(v) for v in phase_max_deltas))
        mean_ok = max_mean_delta <= MEAN_DELTA_LIMIT_NM
        ripple_ok = max_pp_rel_delta <= RIPPLE_RELATIVE_LIMIT \
            and max_extrema_delta <= EXTREMA_DELTA_LIMIT_NM
        if count != SOURCE_COUNT:
            mean_gate_ok = mean_gate_ok and mean_ok
            ripple_gate_ok = ripple_gate_ok and ripple_ok
        grids[str(count)] = {
            "sample_count": count,
            "stride": stride,
            "phase_offset_count": stride,
            "phase_offset_rows": phases,
            "mean_sensitivity": {
                "max_abs_phase_mean_delta_vs_120_Nm": max_mean_delta,
                "mean_span_across_offsets_Nm": max(phase_mean_deltas) - min(phase_mean_deltas),
                "provisional_limit_Nm": MEAN_DELTA_LIMIT_NM,
                "passed": bool(mean_ok),
            },
            "ripple_extrema_sensitivity": {
                "max_abs_peak_to_peak_delta_vs_120_Nm": max(abs(v) for v in phase_pp_deltas),
                "max_relative_peak_to_peak_delta_vs_120": max_pp_rel_delta,
                "max_abs_sampled_extrema_delta_Nm": max_extrema_delta,
                "provisional_peak_to_peak_relative_limit": RIPPLE_RELATIVE_LIMIT,
                "provisional_extrema_limit_Nm": EXTREMA_DELTA_LIMIT_NM,
                "passed_all_phase_offsets": bool(ripple_ok),
            },
            "alias_groups_120_to_N": alias_groups(SOURCE_COUNT, count),
        }
    return {
        "passed": bool(mean_gate_ok and ripple_gate_ok),
        "errors": [],
        "mean_reproducibility_passed": bool(mean_gate_ok),
        "ripple_sampling_converged": bool(ripple_gate_ok),
        "continuum_or_subcell_convergence_certified": False,
        "failed_gates": ([] if mean_gate_ok else ["mean_reproducibility"])
                        + ([] if ripple_gate_ok else ["ripple_sampling_convergence_all_offsets"]),
        "gate_limits": {
            "mean_max_abs_delta_Nm": MEAN_DELTA_LIMIT_NM,
            "peak_to_peak_max_relative_delta": RIPPLE_RELATIVE_LIMIT,
            "sampled_minimum_or_maximum_max_abs_delta_Nm": EXTREMA_DELTA_LIMIT_NM,
            "status": "provisional engineering review thresholds, not validated error bounds",
        },
        "provenance": {
            "effective_ns": 4, "bc_sign": -1, "source_run_even": "run13",
            "source_run_odd": "run14", "solver_sha256": EXPECTED_SOLVER_SHA256,
            "config_sha256": EXPECTED_CONFIG_SHA256,
            "material_library_sha256": EXPECTED_MATERIAL_SHA256,
            "geometry": even_review["geometry"], "n_dofs": even_review["n_dofs"],
            "n_elements": even_review["n_elements"],
            "slip_nodes": 1680, "slip_spacing_deg": 360.0 / 1680.0,
            "all_120_source_frames_strictly_converged": True,
        },
        "reference_120": {
            **reference,
            "interpretation": "finest archived integer-slip-cell grid, not a true or continuum-converged solution",
        },
        "grids": grids,
        "sampling": {
            "source_count": 120,
            "source_shifts": list(SOURCE_SHIFTS),
            "merged_angle_deg": [s * 360.0 / 1680.0 for s in SOURCE_SHIFTS],
            "all_phase_offsets_tested": True,
            "all_raw_values_and_rfft_bins_retained": True,
            "harmonic_filtering_or_sample_discard": False,
            "nested_stride_rule": "N=20/30/40/60/120 uses strides 6/4/3/2/1; enumerate every offset 0..stride-1.",
            "aliasing": {
                "complex_formula": "For source x[n], target y[j]=x[offset+stride*j], normalized Y[k] = sum over source full-DFT bins h congruent k mod N of X[h]*exp(+2πi*h*offset/120).",
                "negative_frequency_convention": "h indexes the ordinary 120-point full complex FFT (bins above 60 are negative frequencies).",
                "common_nyquist_order": 10,
                "higher_order_note": "Do not compare bins above common order 10 as identical physical harmonics; each nested grid aliases distinct source orders.",
                "nyquist_note": "Target Nyquist bins are retained with complex coefficient and undoubled single-sided amplitude; alias classes include both signs/orders folded onto that bin.",
            },
            "rfft_convention": "rfft(raw full-machine Maxwell torque)/N; DC and target Nyquist undoubled, interior amplitudes doubled; no demean.",
        },
    }


def _load_frames(folder: Path) -> list[dict[str, Any]]:
    frames = []
    for i in range(60):
        with np.load(folder / f"frame_{i}.npz", allow_pickle=False) as archive:
            frames.append({key: archive[key].tolist() if archive[key].ndim else archive[key].item()
                           for key in REQUIRED_FRAME_KEYS})
    return frames


def run_review(even_review_path: Path = DEFAULT_EVEN_REVIEW,
               even_frames_dir: Path = DEFAULT_EVEN_FRAMES,
               odd_review_path: Path = DEFAULT_ODD_REVIEW,
               odd_frames_dir: Path = DEFAULT_ODD_FRAMES) -> dict[str, Any]:
    even_review = json.loads(even_review_path.read_text(encoding="utf-8"))
    odd_review = json.loads(odd_review_path.read_text(encoding="utf-8"))
    if not isinstance(even_review, dict) or not isinstance(odd_review, dict):
        raise ValueError("review JSON roots must be objects")
    return analyze_sampling(even_review, _load_frames(even_frames_dir),
                            odd_review, _load_frames(odd_frames_dir))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--even-review", type=Path, default=DEFAULT_EVEN_REVIEW)
    parser.add_argument("--even-frames", type=Path, default=DEFAULT_EVEN_FRAMES)
    parser.add_argument("--odd-review", type=Path, default=DEFAULT_ODD_REVIEW)
    parser.add_argument("--odd-frames", type=Path, default=DEFAULT_ODD_FRAMES)
    args = parser.parse_args(argv)
    try:
        result = run_review(args.even_review, args.even_frames, args.odd_review, args.odd_frames)
    except Exception as exc:
        result = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
