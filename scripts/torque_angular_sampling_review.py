#!/usr/bin/env python
"""Offline angular-sampling review of the archived 60-point NS4 torque trace."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4/period_grid_review.json"
DEFAULT_FRAMES = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4"
EXPECTED_SOLVER_SHA256 = "67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7"
EXPECTED_CONFIG_SHA256 = "f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8"
EXPECTED_MATERIAL_SHA256 = "80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136"
SOURCE_COUNT = 60
SOURCE_SHIFTS = tuple(range(0, 120, 2))
GRID_STRIDES = {20: 3, 30: 2, 60: 1}
MEAN_DELTA_LIMIT_NM = 0.01
RIPPLE_RELATIVE_LIMIT = 0.01
REQUIRED_FRAME_KEYS = ("torque_sector_Nm", "shift", "angle_deg", "bc_sign",
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
    """Return exact full-complex-DFT source-bin congruence classes for each rFFT bin."""
    if source_count <= 0 or target_count <= 0 or source_count % target_count:
        raise ValueError("target_count must be a positive divisor of source_count")
    return [{"target_bin": k,
             "source_full_fft_bins": [j for j in range(source_count) if j % target_count == k]}
            for k in range(target_count // 2 + 1)]


def analyze_sampling(review: Mapping[str, Any], frames: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate all 60 archived frames and summarize nested 20/30/60 raw samples."""
    errors: list[str] = []
    try:
        if review.get("sector") != 4 or review.get("bc_sign") != -1:
            errors.append("archive must attest effective NS4 and anti-periodic BC sign -1")
        if review.get("solver_sha256") != EXPECTED_SOLVER_SHA256:
            errors.append("archived solver hash mismatch")
        if review.get("config_sha256") != EXPECTED_CONFIG_SHA256:
            errors.append("archived config hash mismatch")
        if review.get("material_library_sha256") != EXPECTED_MATERIAL_SHA256:
            errors.append("archived material-library hash mismatch")
        shifts = list(review.get("shifts", []))
        if shifts != list(SOURCE_SHIFTS):
            errors.append("source shifts must be the complete 0,2,...,118 grid")
        torque = _finite_vector(review.get("torque_scaled_Nm", []), "torque_scaled_Nm", SOURCE_COUNT)
        angles = _finite_vector(review.get("angle_deg", []), "angle_deg", SOURCE_COUNT)
        residuals = _finite_vector(review.get("residual", []), "residual", SOURCE_COUNT)
        convergence = np.asarray(review.get("newton_ok", []), dtype=object)
        if convergence.shape != (SOURCE_COUNT,) or not all(value is True for value in convergence):
            errors.append("all 60 archived frames must be converged")
        currents = np.asarray(review.get("current_A", []), dtype=float)
        if currents.shape != (SOURCE_COUNT, 3) or not np.all(np.isfinite(currents)):
            errors.append("current_A must contain 60 finite three-phase samples")
        if len(frames) != SOURCE_COUNT:
            errors.append(f"expected {SOURCE_COUNT} raw frame metadata rows, got {len(frames)}")
        else:
            step_deg = 360.0 / 1680.0
            for i, frame in enumerate(frames):
                missing = [key for key in REQUIRED_FRAME_KEYS if key not in frame]
                if missing:
                    errors.append(f"frame {i} missing metadata: {', '.join(missing)}")
                    continue
                if int(frame["shift"]) != SOURCE_SHIFTS[i]:
                    errors.append(f"frame {i} shift mismatch")
                if int(frame["bc_sign"]) != -1 or int(frame["slip_nodes"]) != 1680:
                    errors.append(f"frame {i} symmetry/slip-grid metadata mismatch")
                if not math.isclose(float(frame["angle_deg"]), angles[i], rel_tol=0, abs_tol=1e-12):
                    errors.append(f"frame {i} angle differs from review")
                if not math.isclose(float(frame["angle_deg"]), SOURCE_SHIFTS[i] * step_deg,
                                    rel_tol=0, abs_tol=1e-12):
                    errors.append(f"frame {i} angle does not match the expected shift grid")
                if not math.isclose(float(frame["slip_spacing_deg"]), step_deg,
                                    rel_tol=0, abs_tol=1e-12):
                    errors.append(f"frame {i} slip spacing mismatch")
                if frame["newton_ok"] is not True:
                    errors.append(f"frame {i} did not converge")
                if not math.isclose(float(frame["residual"]), residuals[i], rel_tol=0, abs_tol=1e-20):
                    errors.append(f"frame {i} residual differs from review")
                if not math.isclose(4.0 * float(frame["torque_sector_Nm"]), torque[i],
                                    rel_tol=0, abs_tol=1e-10):
                    errors.append(f"frame {i} sector torque times NS differs from archived scaled torque")
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        errors.append(f"invalid archive data: {exc}")
    if errors:
        return {"passed": False, "errors": errors}

    nested: dict[str, Any] = {}
    for count, stride in GRID_STRIDES.items():
        values = torque[::stride].copy()
        nested[str(count)] = {
            "source_indices": list(range(0, SOURCE_COUNT, stride)),
            "source_shifts": [int(SOURCE_SHIFTS[i]) for i in range(0, SOURCE_COUNT, stride)],
            "raw_torque_Nm": values.tolist(),
            "sample_mean_Nm": float(np.mean(values)),
            "sample_minimum_Nm": float(np.min(values)),
            "sample_minimum_source_index": int(np.argmin(values) * stride),
            "sample_maximum_Nm": float(np.max(values)),
            "sample_maximum_source_index": int(np.argmax(values) * stride),
            "sample_peak_to_peak_Nm": float(np.ptp(values)),
            "full_rfft_bins": _single_sided_spectrum(values),
        }

    ref_mean = nested["60"]["sample_mean_Nm"]
    mean_deltas = {n: nested[n]["sample_mean_Nm"] - ref_mean for n in ("20", "30")}
    mean_passed = all(abs(v) <= MEAN_DELTA_LIMIT_NM for v in mean_deltas.values())
    ref_pp = nested["60"]["sample_peak_to_peak_Nm"]
    pp_deltas = {n: nested[n]["sample_peak_to_peak_Nm"] - ref_pp for n in ("20", "30")}
    max_pp_error = max(abs(value) for value in pp_deltas.values())
    ripple_converged = max_pp_error <= RIPPLE_RELATIVE_LIMIT * ref_pp
    return {
        "passed": bool(mean_passed and ripple_converged),
        "errors": [],
        "mean_reproducibility_passed": bool(mean_passed),
        "ripple_sampling_converged": bool(ripple_converged),
        "failed_gates": ([] if mean_passed else ["mean_reproducibility"])
                        + ([] if ripple_converged else ["ripple_sampling_convergence"]),
        "gate_limits": {"mean_delta_abs_Nm": MEAN_DELTA_LIMIT_NM,
                        "peak_to_peak_relative_delta": RIPPLE_RELATIVE_LIMIT},
        "source": {"sample_count": SOURCE_COUNT, "effective_ns": 4, "bc_sign": -1,
                   "source_torque_Nm": torque.tolist(),
                   "source_shifts": list(SOURCE_SHIFTS),
                   "interpretation": "60 samples are the archived reference grid, not a true or converged solution."},
        "grids": nested,
        "deltas_vs_60": {"mean_Nm": mean_deltas, "peak_to_peak_Nm": pp_deltas},
        "sampling": {
            "all_source_samples_retained": True,
            "harmonic_filtering_or_sample_discard": False,
            "nested_index_rule": "N=20: indices 0,3,...,57; N=30: 0,2,...,58; N=60: all indices.",
            "aliasing": {
                "full_complex_dft_rule": "For target N from 60, target bin k is the sum of 60-point complex DFT bins j with j mod N = k; negative-frequency bins use the ordinary 60-point DFT indexing.",
                "groups_60_to_20": alias_groups(60, 20),
                "groups_60_to_30": alias_groups(60, 30),
                "common_nyquist_order": 10,
                "physical_order_note": "Even target bins through common Nyquist order 10 include aliased source orders unless the source is band-limited; bins above 10 have no counterpart on the 20-point grid.",
            },
            "rfft_convention": "rfft(raw full-machine Maxwell torque)/N; DC/Nyquist undoubled, interior single-sided amplitudes doubled; no demean.",
        },
    }


def _load_frames(folder: Path) -> list[dict[str, Any]]:
    frames = []
    for i in range(SOURCE_COUNT):
        with np.load(folder / f"frame_{i}.npz", allow_pickle=False) as archive:
            frames.append({key: archive[key].item() for key in REQUIRED_FRAME_KEYS})
    return frames


def run_review(review_path: Path = DEFAULT_REVIEW, frames_dir: Path = DEFAULT_FRAMES) -> dict[str, Any]:
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(review, dict):
        raise ValueError("review JSON root must be an object")
    return analyze_sampling(review, _load_frames(frames_dir))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--frames-dir", type=Path, default=DEFAULT_FRAMES)
    args = parser.parse_args(argv)
    try:
        result = run_review(args.review, args.frames_dir)
    except Exception as exc:
        result = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
