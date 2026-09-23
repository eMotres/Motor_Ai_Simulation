#!/usr/bin/env python
"""Offline sampling sensitivity of production terminal_work_mean on run13/14."""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVEN_REVIEW = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4/period_grid_review.json"
DEFAULT_EVEN_FRAMES = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4"
DEFAULT_EVEN_LOG = ROOT / "scratchpad/torque_period_grid_20260923_run13/ns4/run.log"
DEFAULT_ODD_REVIEW = ROOT / "scratchpad/torque_odd_slip_20260923_run14/ns4/odd_grid_review.json"
DEFAULT_ODD_FRAMES = ROOT / "scratchpad/torque_odd_slip_20260923_run14/ns4"
DEFAULT_ODD_LOG = ROOT / "scratchpad/torque_odd_slip_20260923_run14/ns4/run.log"
GRID_STRIDES = {20: 6, 30: 4, 40: 3, 60: 2, 120: 1}
POLE_PAIRS = 14
N_PARALLEL = 1
CURRENT_RMS_A = 60.0
CURRENT_PEAK_A = CURRENT_RMS_A * math.sqrt(2.0)
CURRENT_PHASE_OFFSET_DEG = 60.0
MEAN_DELTA_LIMIT_NM = 0.01
RAW_FRAME_KEYS = ("torque_sector_Nm", "currents_A", "psi_Wb", "shift", "angle_deg",
                  "bc_sign", "slip_nodes", "slip_spacing_deg", "newton_ok", "residual")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ANGULAR = _load_module("torque_terminal_sampling_angular_review",
                        ROOT / "scripts/torque_angular_sampling_review.py")
_SB_POSTPROC = _load_module("torque_terminal_sampling_sb_postproc",
                            ROOT / "src/motor_ai_sim/simulation/sb_postproc.py")
terminal_work_mean = _SB_POSTPROC.terminal_work_mean


def _rfft_evidence(values: np.ndarray, unit: str) -> list[dict[str, Any]]:
    coeff = np.fft.rfft(values) / values.size
    rows = []
    for k, c in enumerate(coeff):
        doubled = k != 0 and not (values.size % 2 == 0 and k == values.size // 2)
        rows.append({"bin": k, "electrical_order": k,
                     "coefficient_real": float(c.real), "coefficient_imag": float(c.imag),
                     "coefficient_unit": unit,
                     "single_sided_peak_amplitude": float(abs(c) * (2 if doubled else 1)),
                     "amplitude_doubled": doubled})
    return rows


def production_derivative(psi: np.ndarray, mechanical_angle_rad: np.ndarray) -> np.ndarray:
    """Mirror the derivative operation used inside sb_postproc.terminal_work_mean."""
    n = int(psi.shape[-1])
    step = float(mechanical_angle_rad[1] - mechanical_angle_rad[0])
    omega = 2.0 * math.pi * np.fft.fftfreq(n, d=step)
    return np.fft.ifft(1j * omega[None, :] * np.fft.fft(psi, axis=-1), axis=-1).real


def _expected_current(angle_rad: float) -> np.ndarray:
    phase = POLE_PAIRS * float(angle_rad) + math.radians(CURRENT_PHASE_OFFSET_DEG)
    offsets = np.asarray((0.0, 2.0 * math.pi / 3.0, -2.0 * math.pi / 3.0))
    return CURRENT_PEAK_A * np.cos(phase - offsets)


def _params_from_log(path: Path) -> dict[str, Any]:
    prefix = "Explicit parameters: "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            result = json.loads(line[len(prefix):])
            if not isinstance(result, dict):
                break
            return result
    raise ValueError(f"no explicit parameter record in {path}")


def _load_review(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"review root must be an object: {path}")
    return value


def _load_frames(folder: Path) -> list[dict[str, Any]]:
    frames = []
    for i in range(60):
        with np.load(folder / f"frame_{i}.npz", allow_pickle=False) as archive:
            frames.append({key: archive[key].tolist() if archive[key].ndim else archive[key].item()
                           for key in RAW_FRAME_KEYS})
    return frames


def _validate_drive_params(params: Mapping[str, Any], label: str) -> list[str]:
    expected = {"n_sectors": 4, "n_parallel": N_PARALLEL,
                "I_phase_rms": CURRENT_RMS_A, "rpm": 15000.0,
                "daxis_deg": CURRENT_PHASE_OFFSET_DEG, "connection": "2S",
                "drive": "current", "eddy": False, "rotor_eddy": False,
                "demag": False, "torque_filter": False,
                "n_steps_per_period": 60, "n_periods": 2.0,
                "mesh_size_mm": 4.0, "min_size_mm": 0.3,
                "outer_air_factor": 1.3, "gap_layers": 1.0,
                "structured_gap": True, "geo_mesh": False, "iron_template": True}
    errors = [f"{label}: run parameter {key} expected {value!r}, got {params.get(key)!r}"
              for key, value in expected.items() if params.get(key) != value]
    return errors


def analyze_terminal_sampling(even_review: Mapping[str, Any],
                              even_frames: Sequence[Mapping[str, Any]],
                              odd_review: Mapping[str, Any],
                              odd_frames: Sequence[Mapping[str, Any]],
                              even_params: Mapping[str, Any],
                              odd_params: Mapping[str, Any]) -> dict[str, Any]:
    """Run production terminal_work_mean on each phase offset of the merged trace."""
    base_validation = _ANGULAR.analyze_sampling(even_review, even_frames, odd_review, odd_frames)
    errors = list(base_validation.get("errors", []))
    errors.extend(_validate_drive_params(even_params, "even run"))
    errors.extend(_validate_drive_params(odd_params, "odd run"))
    for key in ("n_sectors", "n_parallel", "I_phase_rms", "rpm", "daxis_deg", "connection",
                "drive", "eddy", "rotor_eddy", "demag", "torque_filter"):
        if even_params.get(key) != odd_params.get(key):
            errors.append(f"even/odd run parameter {key} differs")
    expected_geometry = {"num_slots": 24, "num_poles": 28,
                         "stator_diameter": 150.0, "motor_length": 35.0}
    geometry = even_review.get("geometry")
    if not isinstance(geometry, Mapping) or any(geometry.get(k) != v
                                                for k, v in expected_geometry.items()):
        errors.append("review geometry does not match G150 24s/28p fixture")

    # The angular audit verifies provenance, order, currents_A, raw Maxwell torque,
    # hashes, geometry and convergence. Here add linkage and prescribed-current checks.
    if not errors:
        for label, review, frames, expected_shifts in (
                ("even", even_review, even_frames, range(0, 120, 2)),
                ("odd", odd_review, odd_frames, range(1, 120, 2))):
            psi_review = np.asarray(review.get("psi_Wb", []), dtype=float)
            if psi_review.shape != (60, 3) or not np.all(np.isfinite(psi_review)):
                errors.append(f"{label}: psi_Wb must have shape (60,3) and finite Wb values")
                continue
            for i, (frame, shift) in enumerate(zip(frames, expected_shifts)):
                psi = np.asarray(frame.get("psi_Wb", []), dtype=float)
                current = np.asarray(frame.get("currents_A", []), dtype=float)
                angle = int(shift) * (2.0 * math.pi / 1680.0)
                if psi.shape != (3,) or not np.all(np.isfinite(psi)):
                    errors.append(f"{label} frame {i}: psi_Wb must contain three finite phase linkages")
                    continue
                if not np.allclose(psi, psi_review[i], rtol=0, atol=1e-14):
                    errors.append(f"{label} frame {i}: raw psi_Wb differs from review")
                if not np.allclose(current, _expected_current(angle), rtol=0, atol=1e-12):
                    errors.append(f"{label} frame {i}: current violates signed 60 A RMS three-phase law")
    if errors:
        return {"passed": False, "errors": errors}

    records: dict[int, dict[str, Any]] = {}
    for review, frames, shifts in ((even_review, even_frames, range(0, 120, 2)),
                                   (odd_review, odd_frames, range(1, 120, 2))):
        torque_review = np.asarray(review["torque_scaled_Nm"], dtype=float)
        for i, shift in enumerate(shifts):
            frame = frames[i]
            records[int(shift)] = {
                "mechanical_angle_rad": int(shift) * (2.0 * math.pi / 1680.0),
                "current_A": np.asarray(frame["currents_A"], dtype=float),
                "psi_Wb": np.asarray(frame["psi_Wb"], dtype=float),
                "raw_maxwell_torque_Nm": float(torque_review[i]),
            }
    if sorted(records) != list(range(120)):
        return {"passed": False, "errors": ["merged current/linkage records are not exact shifts 0..119"]}

    merged_angles = np.asarray([records[k]["mechanical_angle_rad"] for k in range(120)])
    merged_current = np.asarray([records[k]["current_A"] for k in range(120)]).T.copy()
    merged_psi = np.asarray([records[k]["psi_Wb"] for k in range(120)]).T.copy()
    merged_maxwell = np.asarray([records[k]["raw_maxwell_torque_Nm"] for k in range(120)])
    reference_work = terminal_work_mean(*merged_psi, *merged_current,
                                        merged_angles, POLE_PAIRS, n_parallel=N_PARALLEL)
    ref_derivative = production_derivative(merged_psi, merged_angles)
    ref_work_reconstructed = float(N_PARALLEL * np.mean(np.sum(merged_current * ref_derivative, axis=0)))
    if not math.isclose(reference_work, ref_work_reconstructed, rel_tol=0, abs_tol=1e-12):
        return {"passed": False, "errors": ["production helper and derivative evidence disagree"]}

    reference_raw_mean = float(np.mean(merged_maxwell))
    grids: dict[str, Any] = {}
    phase_work_deltas: list[float] = []
    for count, stride in GRID_STRIDES.items():
        phase_rows = []
        for offset in range(stride):
            indices = np.arange(offset, 120, stride, dtype=int)
            angle = merged_angles[indices].copy()
            current = merged_current[:, indices].copy()
            psi = merged_psi[:, indices].copy()
            raw_maxwell = merged_maxwell[indices].copy()
            derivative = production_derivative(psi, angle)
            work = terminal_work_mean(*psi, *current, angle, POLE_PAIRS,
                                      n_parallel=N_PARALLEL)
            work_from_derivative = float(N_PARALLEL * np.mean(np.sum(current * derivative, axis=0)))
            raw_maxwell_mean = float(np.mean(raw_maxwell))
            row = {
                "phase_offset": offset,
                "source_indices_and_shifts": indices.tolist(),
                "mechanical_angle_rad": angle.tolist(),
                "phase_current_A": {ph: current[i].tolist() for i, ph in enumerate("ABC")},
                "phase_flux_linkage_Wb": {ph: psi[i].tolist() for i, ph in enumerate("ABC")},
                "dpsi_dmechanical_angle_Wb_per_rad": {
                    ph: derivative[i].tolist() for i, ph in enumerate("ABC")},
                "spectra": {
                    "current": {ph: _rfft_evidence(current[i], "A")
                                for i, ph in enumerate("ABC")},
                    "flux_linkage": {ph: _rfft_evidence(psi[i], "Wb")
                                     for i, ph in enumerate("ABC")},
                    "flux_derivative": {ph: _rfft_evidence(derivative[i], "Wb/rad")
                                        for i, ph in enumerate("ABC")},
                },
                "production_terminal_work_mean_Nm": float(work),
                "derivative_dot_current_reconstruction_Nm": work_from_derivative,
                "production_vs_derivative_abs_delta_Nm": abs(work - work_from_derivative),
                "terminal_work_delta_vs_120_Nm": float(work - reference_work),
                "raw_maxwell_mean_diagnostic_only_Nm": raw_maxwell_mean,
                "raw_maxwell_mean_delta_vs_120_diagnostic_only_Nm": float(raw_maxwell_mean - reference_raw_mean),
                "full_rfft_bin_count_per_phase": count // 2 + 1,
            }
            phase_rows.append(row)
            if count != 120:
                phase_work_deltas.append(float(work - reference_work))
        work_values = [row["production_terminal_work_mean_Nm"] for row in phase_rows]
        raw_means = [row["raw_maxwell_mean_diagnostic_only_Nm"] for row in phase_rows]
        raw_mean_deltas = [row["raw_maxwell_mean_delta_vs_120_diagnostic_only_Nm"]
                           for row in phase_rows]
        deltas = [row["terminal_work_delta_vs_120_Nm"] for row in phase_rows]
        grids[str(count)] = {
            "sample_count": count,
            "stride": stride,
            "phase_offset_count": stride,
            "phase_offset_rows": phase_rows,
            "terminal_work_sampling_sensitivity": {
                "mean_across_offsets_Nm": float(np.mean(work_values)),
                "span_across_offsets_Nm": float(max(work_values) - min(work_values)),
                "max_abs_delta_vs_120_Nm": float(max(abs(v) for v in deltas)),
                "all_offset_work_values_Nm": work_values,
            },
            "raw_maxwell_mean_diagnostic_only": {
                "mean_across_offsets_Nm": float(np.mean(raw_means)),
                "span_across_offsets_Nm": float(max(raw_means) - min(raw_means)),
                "max_abs_delta_vs_120_diagnostic_only_Nm": float(max(abs(v) for v in raw_mean_deltas)),
                "values_Nm": raw_means,
                "deltas_vs_120_Nm": raw_mean_deltas,
            },
        }
    max_abs_work_delta = max(abs(v) for v in phase_work_deltas)
    return {
        "passed": bool(max_abs_work_delta <= MEAN_DELTA_LIMIT_NM),
        "result_scope": "sampling reproducibility only; not a torque-physics certificate",
        "errors": [],
        "terminal_work_sampling_reproducible": bool(max_abs_work_delta <= MEAN_DELTA_LIMIT_NM),
        "does_not_certify": ["stored-energy closure", "conservative periodic field state",
                             "virtual-work identity for production geometry", "continuum correctness"],
        "gate_limit": {"max_abs_phase_offset_delta_vs_120_Nm": MEAN_DELTA_LIMIT_NM,
                       "status": "provisional sampling reproducibility threshold only"},
        "provenance": {
            "source_runs": {"even": "run13", "odd": "run14"},
            "geometry": even_review["geometry"],
            "effective_ns": 4, "bc_sign": -1, "pole_pairs": POLE_PAIRS,
            "n_parallel": N_PARALLEL,
            "solver_sha256": even_review["solver_sha256"],
            "config_sha256": even_review["config_sha256"],
            "material_library_sha256": even_review["material_library_sha256"],
            "n_dofs": even_review["n_dofs"], "n_elements": even_review["n_elements"],
            "current_law": "84.8528137423857 A peak; phase=14*theta_mech_rad+60deg; RMS=60 A",
            "mechanical_angle_units": "radians, signed increasing path",
            "flux_linkage_units": "Wb per phase (archived source pairing)",
            "drive_parameters": {k: even_params.get(k) for k in
                                  ("n_parallel", "I_phase_rms", "rpm", "daxis_deg", "connection", "drive")},
            "all_source_frames_strictly_converged": True,
        },
        "reference_120": {
            "source_shifts": list(range(120)),
            "mechanical_angle_rad": merged_angles.tolist(),
            "phase_current_A": {ph: merged_current[i].tolist() for i, ph in enumerate("ABC")},
            "phase_flux_linkage_Wb": {ph: merged_psi[i].tolist() for i, ph in enumerate("ABC")},
            "dpsi_dmechanical_angle_Wb_per_rad": {
                ph: ref_derivative[i].tolist() for i, ph in enumerate("ABC")},
            "production_terminal_work_mean_Nm": float(reference_work),
            "derivative_dot_current_reconstruction_Nm": ref_work_reconstructed,
            "raw_maxwell_mean_diagnostic_only_Nm": reference_raw_mean,
            "interpretation": "120 positions are the finest archived integer-shift trajectory, not a truth or continuum certificate.",
        },
        "grids": grids,
        "sampling": {
            "all_phase_offsets_evaluated": True,
            "strides": {str(n): stride for n, stride in GRID_STRIDES.items()},
            "source_samples_demeaned_or_filtered": False,
            "full_dft_evidence": "Every phase offset exports raw selected currents/linkages, production derivative samples, and all rFFT bins of each input/derivative phase series.",
            "rfft_convention": "rfft(series)/N; retain DC and Nyquist, do not double DC/Nyquist amplitudes, double only interior single-sided amplitudes.",
            "production_derivative_convention": "ifft(1j*2*pi*fftfreq(N,d=signed_dtheta)*fft(psi)).real; even-grid Nyquist derivative follows production's zero-real-quadrature convention.",
            "aliasing": {
                "common_nyquist_order": 10,
                "normalized_complex_alias_rule": "For offset r and stride D=120/N, Y_N[k] is the sum of X_120[h]*exp(+2πi*h*r/120) over h mod N = k.",
                "groups_120_to_N": {str(n): _ANGULAR.alias_groups(120, n) for n in GRID_STRIDES},
            },
        },
    }


def run_review(even_review_path: Path = DEFAULT_EVEN_REVIEW,
               even_frames_dir: Path = DEFAULT_EVEN_FRAMES,
               even_log_path: Path = DEFAULT_EVEN_LOG,
               odd_review_path: Path = DEFAULT_ODD_REVIEW,
               odd_frames_dir: Path = DEFAULT_ODD_FRAMES,
               odd_log_path: Path = DEFAULT_ODD_LOG) -> dict[str, Any]:
    return analyze_terminal_sampling(
        _load_review(even_review_path), _load_frames(even_frames_dir),
        _load_review(odd_review_path), _load_frames(odd_frames_dir),
        _params_from_log(even_log_path), _params_from_log(odd_log_path))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--even-review", type=Path, default=DEFAULT_EVEN_REVIEW)
    parser.add_argument("--even-frames", type=Path, default=DEFAULT_EVEN_FRAMES)
    parser.add_argument("--even-log", type=Path, default=DEFAULT_EVEN_LOG)
    parser.add_argument("--odd-review", type=Path, default=DEFAULT_ODD_REVIEW)
    parser.add_argument("--odd-frames", type=Path, default=DEFAULT_ODD_FRAMES)
    parser.add_argument("--odd-log", type=Path, default=DEFAULT_ODD_LOG)
    args = parser.parse_args(argv)
    try:
        result = run_review(args.even_review, args.even_frames, args.even_log,
                            args.odd_review, args.odd_frames, args.odd_log)
    except Exception as exc:
        result = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
