#!/usr/bin/env python
"""Offline matched-grid review of archived 24s/28p sector torque samples.

This tool reads the frozen run16/15/13 artifacts only.  It does not import the
motor solver, start an API, alter samples, or filter harmonics.  The NS4 source
remains a 60-sample waveform; indices for the nested 20-angle comparison are
reported explicitly in the JSON result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOLVER_SHA256 = "67b714f726e89a696014d25a59aa593aa41fffc70f04422b19629446548c12e7"
EXPECTED_CONFIG_SHA256 = "f86e751dd57ca0cfa8e9d8dac46620dc723d11e446a4ce962ea5d4be4c6caba8"
EXPECTED_MATERIAL_LIBRARY_SHA256 = "80b269b6ab344458409fd7c56ee9e0e84c58fed0af4211f4af14f022d9b80136"
ARCHIVED_SOURCE_COMMIT = "ad3d0f12e3bf52c90973002f89ebf2a2d7e7653f"
COMMON_SHIFTS = tuple(range(0, 120, 6))
SOURCE_COUNTS = {1: 20, 2: 20, 4: 60}
EXPECTED_BC_SIGN = {1: 1, 2: 1, 4: -1}
EXPECTED_PARAMS = {
    "mesh_size_mm": 4.0,
    "min_size_mm": 0.3,
    "outer_air_factor": 1.3,
    "gap_layers": 1.0,
    "structured_gap": True,
    "geo_mesh": False,
    "iron_template": True,
    "I_phase_rms": 60.0,
    "rpm": 15000.0,
    "gamma_deg": 0.0,
    "daxis_deg": 60.0,
    "n_parallel": 1,
    "connection": "2S",
    "drive": "current",
    "eddy": False,
    "rotor_eddy": False,
    "demag": False,
    "torque_filter": False,
    "pole_copy": False,
}
GATES = {
    "max_pointwise_abs_Nm": 0.08,
    "max_abs_mean_delta_Nm": 0.01,
    "max_abs_peak_to_peak_delta_Nm": 0.06,
    "max_complex_dft_coefficient_delta_Nm": 0.02,
    "max_single_sided_bin_amplitude_delta_Nm": 0.03,
    "max_current_delta_A": 1e-12,
    "max_angle_delta_deg": 1e-12,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_archive_paths(root: Path = ROOT) -> dict[int, dict[str, Path]]:
    """Return the known immutable source artifact locations."""
    return {
        1: {
            "review": root / "scratchpad/torque_full_period_20260923_run16/ns1/full_period_review.json",
            "run_log": root / "scratchpad/torque_full_period_20260923_run16/ns1/run.log",
            "provenance": root / "scratchpad/torque_full_period_20260923_run16/ns1/run_provenance.json",
            "frames": root / "scratchpad/torque_full_period_20260923_run16/ns1",
            "config": root / "scratchpad/torque_full_period_20260923_run16/ns1/motor_config.yaml",
        },
        2: {
            "review": root / "scratchpad/torque_half_period_20260923_run15/ns2/half_period_review.json",
            "run_log": root / "scratchpad/torque_half_period_20260923_run15/ns2/run.log",
            "provenance": root / "scratchpad/torque_half_period_20260923_run15/ns2/run_provenance.json",
            "frames": root / "scratchpad/torque_half_period_20260923_run15/ns2",
            "config": root / "scratchpad/torque_half_period_20260923_run15/ns2/motor_config.yaml",
        },
        4: {
            "review": root / "scratchpad/torque_period_grid_20260923_run13/ns4/period_grid_review.json",
            "run_log": root / "scratchpad/torque_period_grid_20260923_run13/ns4/run.log",
            "provenance": None,
            "frames": root / "scratchpad/torque_period_grid_20260923_run13/ns4",
            "config": root / "scratchpad/torque_period_grid_20260923_run13/ns4/motor_config.yaml",
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _read_run_params(path: Path) -> dict[str, Any]:
    """Extract the explicit parameter JSON line from a guarded probe log."""
    for line in path.read_text(encoding="utf-8").splitlines():
        prefix = "Explicit parameters: "
        if line.startswith(prefix):
            params = json.loads(line[len(prefix):])
            if not isinstance(params, dict):
                raise ValueError(f"Explicit parameters are not an object in {path}")
            return params
    raise ValueError(f"No explicit parameter record in {path}")


def _read_frame_metadata(folder: Path, count: int) -> list[dict[str, Any]]:
    """Read scalar provenance from every retained raw frame NPZ."""
    frames: list[dict[str, Any]] = []
    for index in range(count):
        path = folder / f"frame_{index}.npz"
        with np.load(path, allow_pickle=False) as archive:
            needed = ("torque_sector_Nm", "currents_A", "shift", "angle_deg",
                      "bc_sign", "slip_nodes", "slip_spacing_deg", "newton_ok",
                      "residual")
            missing = [key for key in needed if key not in archive.files]
            if missing:
                raise ValueError(f"{path} missing raw keys: {', '.join(missing)}")
            frames.append({key: archive[key].tolist() if archive[key].ndim else archive[key].item()
                           for key in needed})
    return frames


def _close(a: float, b: float, tol: float) -> bool:
    return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= tol


def _same_json(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True, separators=(",", ":")) == json.dumps(
        b, sort_keys=True, separators=(",", ":"))


def _single_sided_spectrum(samples: np.ndarray) -> list[dict[str, float | int]]:
    """Return every rFFT bin: normalized complex coefficient and peak amplitude."""
    coefficients = np.fft.rfft(samples) / samples.size
    rows: list[dict[str, float | int]] = []
    for k, coefficient in enumerate(coefficients):
        doubled = k != 0 and not (samples.size % 2 == 0 and k == samples.size // 2)
        amplitude = abs(coefficient) * (2.0 if doubled else 1.0)
        rows.append({
            "bin": int(k),
            "electrical_order": float(k),
            "coefficient_real_Nm": float(coefficient.real),
            "coefficient_imag_Nm": float(coefficient.imag),
            "single_sided_peak_amplitude_Nm": float(amplitude),
            "amplitude_doubled": bool(doubled),
        })
    return rows


def _expected_current(angle_deg: float) -> tuple[float, float, float]:
    peak = 60.0 * math.sqrt(2.0)
    phase = math.radians(14.0 * float(angle_deg) + 60.0)
    return (peak * math.cos(phase),
            peak * math.cos(phase - 2.0 * math.pi / 3.0),
            peak * math.cos(phase + 2.0 * math.pi / 3.0))


def _validate_archive(ns: int, review: Mapping[str, Any], params: Mapping[str, Any],
                      provenance: Mapping[str, Any] | None,
                      frames: list[Mapping[str, Any]], config_hash: str,
                      material_library_hash: str) -> list[str]:
    errors: list[str] = []
    label = f"NS{ns}"
    expected_shifts = list(range(0, 120, 6)) if ns in (1, 2) else list(range(0, 120, 2))
    shifts_key = "grid_shifts" if ns in (1, 2) else "shifts"
    torque_key = "sector_scaled_torque_Nm" if ns in (1, 2) else "torque_scaled_Nm"
    if review.get("sector") != ns:
        errors.append(f"{label}: review sector is not {ns}")
    if review.get(shifts_key) != expected_shifts:
        errors.append(f"{label}: source shifts do not match the frozen grid")
    if review.get("solver_sha256") != EXPECTED_SOLVER_SHA256:
        errors.append(f"{label}: solver hash differs from archived source")
    if review.get("config_sha256") != EXPECTED_CONFIG_SHA256 or config_hash != EXPECTED_CONFIG_SHA256:
        errors.append(f"{label}: archived config hash mismatch")
    if review.get("material_library_sha256") != EXPECTED_MATERIAL_LIBRARY_SHA256 \
            or material_library_hash != EXPECTED_MATERIAL_LIBRARY_SHA256:
        errors.append(f"{label}: material library hash mismatch")
    geometry = review.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("num_slots") != 24 \
            or geometry.get("num_poles") != 28 or geometry.get("stator_diameter") != 150.0 \
            or geometry.get("motor_length") != 35.0:
        errors.append(f"{label}: geometry is not the frozen G150 24s/28p fixture")

    expected_params = dict(EXPECTED_PARAMS, n_sectors=ns, n_steps_per_period=SOURCE_COUNTS[ns],
                           n_periods=2.0)
    for key, expected in expected_params.items():
        if params.get(key) != expected:
            errors.append(f"{label}: run parameter {key} expected {expected!r}, got {params.get(key)!r}")
    if not _same_json(params.get("geo_override"), geometry):
        errors.append(f"{label}: logged geometry differs from review geometry")
    if provenance is not None:
        checks = {
            "sector": ns,
            "requested_n_sectors": ns,
            "solver_sha256": EXPECTED_SOLVER_SHA256,
            "material_library_sha256": EXPECTED_MATERIAL_LIBRARY_SHA256,
            "mesh_size_mm": 4.0,
            "min_size_mm": 0.3,
            "geo_mesh": False,
            "eddy": False,
            "rotor_eddy": False,
            "demag": False,
            "n_parallel": 1,
            "geometry": geometry,
        }
        for key, expected in checks.items():
            if provenance.get(key) != expected:
                errors.append(f"{label}: run provenance {key} mismatch")
        if provenance.get("config_source_sha256_before") != EXPECTED_CONFIG_SHA256 \
                or provenance.get("config_scratch_sha256") != EXPECTED_CONFIG_SHA256:
            errors.append(f"{label}: source/scratch config provenance mismatch")
        if provenance.get("current_law") != "60*sqrt(2) A peak; phase=14*mechanical_angle_deg+60deg":
            errors.append(f"{label}: prescribed current law mismatch")
        if provenance.get("sample_shifts") != expected_shifts:
            errors.append(f"{label}: provenance sample shifts mismatch")
        if provenance.get("samples_cover_full_endpoint_excluded_electrical_period") is not True:
            errors.append(f"{label}: full endpoint-excluded electrical period not attested")

    if len(frames) != SOURCE_COUNTS[ns]:
        errors.append(f"{label}: expected {SOURCE_COUNTS[ns]} raw frame files, got {len(frames)}")
        return errors
    raw_torque = np.asarray(review.get(torque_key, []), dtype=float)
    raw_currents = np.asarray(review.get("current_A", []), dtype=float)
    angles = np.asarray(review.get("angle_deg", []), dtype=float)
    residuals = np.asarray(review.get("residual", []), dtype=float)
    converged = np.asarray(review.get("newton_ok", []), dtype=bool)
    if not (raw_torque.size == len(frames) and raw_currents.shape == (len(frames), 3)
            and angles.size == residuals.size == converged.size == len(frames)):
        errors.append(f"{label}: review arrays do not match the complete raw frame count")
        return errors
    summary_convergence = review.get("all_newton_ok")
    if (summary_convergence is False) or not bool(np.all(converged)):
        errors.append(f"{label}: one or more archived frames failed Newton convergence")
    if not np.all(np.isfinite(raw_torque)) or not np.all(np.isfinite(raw_currents)) \
            or not np.all(np.isfinite(angles)) or not np.all(np.isfinite(residuals)):
        errors.append(f"{label}: non-finite raw values")

    step_deg = 360.0 / 1680.0
    for index, frame in enumerate(frames):
        expected_shift = expected_shifts[index]
        expected_angle = expected_shift * step_deg
        expected_i = _expected_current(expected_angle)
        if frame.get("shift") != expected_shift:
            errors.append(f"{label} frame {index}: raw frame shift mismatch")
        if int(frame.get("bc_sign", 0)) != EXPECTED_BC_SIGN[ns]:
            errors.append(f"{label} frame {index}: incorrect boundary sign")
        if int(frame.get("slip_nodes", 0)) != 1680 or not _close(
                frame.get("slip_spacing_deg", float("nan")), step_deg, 1e-12):
            errors.append(f"{label} frame {index}: slip ring grid mismatch")
        if not _close(frame.get("angle_deg", float("nan")), expected_angle, 1e-12) \
                or not _close(angles[index], expected_angle, 1e-12):
            errors.append(f"{label} frame {index}: rotor angle mismatch")
        observed_i = np.asarray(frame.get("currents_A", []), dtype=float)
        if observed_i.shape != (3,) or not np.allclose(observed_i, expected_i, rtol=0.0, atol=1e-12) \
                or not np.allclose(raw_currents[index], expected_i, rtol=0.0, atol=1e-12):
            errors.append(f"{label} frame {index}: prescribed phase current mismatch")
        if bool(frame.get("newton_ok")) is not True or not bool(converged[index]):
            errors.append(f"{label} frame {index}: convergence flag is false")
        if not _close(frame.get("residual", float("nan")), residuals[index], 1e-20):
            errors.append(f"{label} frame {index}: raw/review residual mismatch")
        scaled_raw = ns * float(frame.get("torque_sector_Nm", float("nan")))
        if not _close(scaled_raw, raw_torque[index], 1e-10):
            errors.append(f"{label} frame {index}: raw Maxwell torque differs from review")
    return errors


def _spectrum_delta(reference: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    ref_c = np.asarray([complex(row["coefficient_real_Nm"], row["coefficient_imag_Nm"])
                        for row in reference])
    got_c = np.asarray([complex(row["coefficient_real_Nm"], row["coefficient_imag_Nm"])
                        for row in candidate])
    ref_a = np.asarray([row["single_sided_peak_amplitude_Nm"] for row in reference], dtype=float)
    got_a = np.asarray([row["single_sided_peak_amplitude_Nm"] for row in candidate], dtype=float)
    dc = np.abs(got_c - ref_c)
    da = np.abs(got_a - ref_a)
    return {
        "complex_coefficient_abs_delta_Nm_by_bin": dc.tolist(),
        "single_sided_amplitude_abs_delta_Nm_by_bin": da.tolist(),
        "max_complex_coefficient_abs_delta_Nm": float(np.max(dc)),
        "max_single_sided_amplitude_abs_delta_Nm": float(np.max(da)),
    }


def analyze_archives(reviews: Mapping[int, Mapping[str, Any]],
                     run_params: Mapping[int, Mapping[str, Any]],
                     provenances: Mapping[int, Mapping[str, Any] | None],
                     frame_metadata: Mapping[int, list[Mapping[str, Any]]],
                     config_hashes: Mapping[int, str],
                     material_library_hash: str) -> dict[str, Any]:
    """Validate archives and compute matched waveform, statistics and complete DFT.

    Inputs are treated as immutable.  The 60-sample NS4 data stays represented
    in full in ``source_sample_count`` and ``source_shifts``; only its stated
    nested indices are used to form matched-angle comparisons.
    """
    errors: list[str] = []
    for ns in (1, 2, 4):
        if ns not in reviews or ns not in run_params or ns not in frame_metadata:
            errors.append(f"Missing NS{ns} archive input")
            continue
        errors.extend(_validate_archive(ns, reviews[ns], run_params[ns],
                                        provenances.get(ns), frame_metadata[ns],
                                        config_hashes.get(ns, ""), material_library_hash))

    if errors:
        return {"passed": False, "errors": errors, "gates": dict(GATES),
                "source_commit": ARCHIVED_SOURCE_COMMIT,
                "source_solver_sha256": EXPECTED_SOLVER_SHA256}

    series: dict[int, np.ndarray] = {}
    review_angles: dict[int, np.ndarray] = {}
    review_currents: dict[int, np.ndarray] = {}
    selected_indices: list[int] = []
    source_shifts: dict[int, list[int]] = {}
    for ns in (1, 2, 4):
        review = reviews[ns]
        shifts_key = "grid_shifts" if ns in (1, 2) else "shifts"
        torque_key = "sector_scaled_torque_Nm" if ns in (1, 2) else "torque_scaled_Nm"
        shifts = [int(value) for value in review[shifts_key]]
        source_shifts[ns] = shifts
        index_by_shift = {shift: index for index, shift in enumerate(shifts)}
        if ns == 4:
            selected_indices = [index_by_shift[shift] for shift in COMMON_SHIFTS]
            indices = selected_indices
        else:
            indices = [index_by_shift[shift] for shift in COMMON_SHIFTS]
        series[ns] = np.asarray(review[torque_key], dtype=float)[indices].copy()
        review_angles[ns] = np.asarray(review["angle_deg"], dtype=float)[indices].copy()
        review_currents[ns] = np.asarray(review["current_A"], dtype=float)[indices].copy()

    reference_angles = review_angles[1]
    reference_currents = review_currents[1]
    metrics: dict[str, Any] = {}
    spectra: dict[int, list[dict[str, Any]]] = {}
    for ns in (1, 2, 4):
        spectra[ns] = _single_sided_spectrum(series[ns])
        metrics[str(ns)] = {
            "raw_maxwell_torque_Nm": series[ns].tolist(),
            "sample_mean_Nm": float(np.mean(series[ns])),
            "sample_minimum_Nm": float(np.min(series[ns])),
            "sample_maximum_Nm": float(np.max(series[ns])),
            "sample_peak_to_peak_Nm": float(np.ptp(series[ns])),
            "full_rfft_bins": spectra[ns],
            "sample_count": int(series[ns].size),
            "effective_ns": ns,
            "bc_sign": EXPECTED_BC_SIGN[ns],
        }

    comparisons: dict[str, Any] = {}
    failed_gates: list[str] = []
    for ns in (2, 4):
        delta = series[ns] - series[1]
        delta_spectrum = _spectrum_delta(spectra[1], spectra[ns])
        current_delta = float(np.max(np.abs(review_currents[ns] - reference_currents)))
        angle_delta = float(np.max(np.abs(review_angles[ns] - reference_angles)))
        item = {
            "raw_waveform_delta_Nm": delta.tolist(),
            "max_pointwise_abs_delta_Nm": float(np.max(np.abs(delta))),
            "rms_pointwise_delta_Nm": float(np.sqrt(np.mean(delta ** 2))),
            "mean_delta_Nm": float(np.mean(series[ns]) - np.mean(series[1])),
            "peak_to_peak_delta_Nm": float(np.ptp(series[ns]) - np.ptp(series[1])),
            "max_current_delta_A": current_delta,
            "max_angle_delta_deg": angle_delta,
            **delta_spectrum,
            "gates": {},
        }
        gate_values = {
            "pointwise_waveform": (item["max_pointwise_abs_delta_Nm"], GATES["max_pointwise_abs_Nm"]),
            "mean": (abs(item["mean_delta_Nm"]), GATES["max_abs_mean_delta_Nm"]),
            "peak_to_peak": (abs(item["peak_to_peak_delta_Nm"]), GATES["max_abs_peak_to_peak_delta_Nm"]),
            "complex_dft": (item["max_complex_coefficient_abs_delta_Nm"],
                            GATES["max_complex_dft_coefficient_delta_Nm"]),
            "single_sided_dft_amplitude": (item["max_single_sided_amplitude_abs_delta_Nm"],
                                            GATES["max_single_sided_bin_amplitude_delta_Nm"]),
            "current": (current_delta, GATES["max_current_delta_A"]),
            "angle": (angle_delta, GATES["max_angle_delta_deg"]),
        }
        for name, (value, limit) in gate_values.items():
            passed = value <= limit
            item["gates"][name] = {"passed": passed, "value": value, "limit": limit}
            if not passed:
                failed_gates.append(f"NS{ns}:{name}")
        comparisons[str(ns)] = item

    ns4_review = reviews[4]
    source_shifts_4 = source_shifts[4]
    selected_shifts_4 = [source_shifts_4[index] for index in selected_indices]
    if selected_shifts_4 != list(COMMON_SHIFTS):
        failed_gates.append("NS4:nested_shift_mapping")
    output = {
        "passed": not failed_gates,
        "errors": [],
        "failed_gates": failed_gates,
        "scope": "24s28p G150 analytic/template P2 raw Maxwell matched-grid sensitivity",
        "source_provenance": {
            "archived_solver_commit": ARCHIVED_SOURCE_COMMIT,
            "archived_solver_sha256": EXPECTED_SOLVER_SHA256,
            "config_sha256": EXPECTED_CONFIG_SHA256,
            "material_library_sha256": EXPECTED_MATERIAL_LIBRARY_SHA256,
            "current_checkout_commit_is_expected": False,
            "caveat": ("Archived solver parent predates 909b014. That commit changes torque-mean "+
                       "selection/diagnostics and zero-mean ripple-percent handling; it does not "+
                       "change the raw per-frame Maxwell torque calculation or NS multiplier."),
            "geometry": "24-slot/28-pole, 150 mm diameter, 35 mm stack (G150)",
            "build_mode": "analytic/template (geo_mesh=false), structured gap",
            "mesh_inputs_mm": {"mesh_size": 4.0, "minimum_size": 0.3},
            "slip_ring_nodes_full_circumference": 1680,
            "slip_spacing_deg": 360.0 / 1680.0,
            "materials_from_guarded_runner": {
                "stator_core": "B15AHV950M", "rotor_core": "B15AHV950M",
                "magnet": "F45SH_120C", "shaft": "Aluminium_6061"},
            "operating_point": {
                "phase_current_rms_A": 60.0, "current_peak_A": 60.0 * math.sqrt(2.0),
                "current_law": "Ipk*cos(14*theta_mech + 60deg + phase offset)",
                "rpm": 15000.0, "connection": "2S", "n_parallel": 1,
                "eddy": False, "rotor_eddy": False, "demag": False},
        },
        "comparison_grid": {
            "common_slip_shifts": list(COMMON_SHIFTS),
            "common_angle_deg": reference_angles.tolist(),
            "period_electrical_deg": 360.0,
            "period_mechanical_deg": 360.0 / 14.0,
            "endpoint_excluded": True,
            "source_sample_count": {str(ns): SOURCE_COUNTS[ns] for ns in (1, 2, 4)},
            "source_shifts": {str(ns): shifts for ns, shifts in source_shifts.items()},
            "ns4_selected_nested_comparison_indices": selected_indices,
            "ns4_selected_nested_comparison_shifts": selected_shifts_4,
            "ns4_note": ("All 60 raw NS4 samples remain unchanged in the source archive and all "+
                         "60 source shifts are recorded here. The 20 indices form the matched-angle "+
                         "diagnostic; its DFT contains every bin of that 20-sample grid."),
            "harmonic_filtering_or_deletion": False,
            "mesh_refinement_performed": False,
        },
        "gate_limits": dict(GATES),
        "sectors": metrics,
        "comparisons_vs_ns1": comparisons,
        "dft_convention": {
            "transform": "rfft(raw full-machine Maxwell torque) / sample_count",
            "retained_bins": list(range(11)),
            "electrical_order": "bin index k for this endpoint-excluded one-electrical-period grid",
            "dc_included": True,
            "nyquist_bin": 10,
            "dc_and_nyquist_undoubled": True,
            "all_interior_single_sided_amplitudes_doubled": True,
            "input_demeaned": False,
        },
        "interpretation": ("Pass means only that archived, same-angle samples satisfy the "+
                           "provisional sector-sensitivity limits. The 20-point grid resolves "+
                           "through electrical order 10 and does not bound aliased higher orders "+
                           "or continuum/cogging error."),
    }
    return output


def run_review(paths: Mapping[int, Mapping[str, Path]], root: Path = ROOT) -> dict[str, Any]:
    reviews: dict[int, dict[str, Any]] = {}
    params: dict[int, dict[str, Any]] = {}
    provenances: dict[int, dict[str, Any] | None] = {}
    frames: dict[int, list[dict[str, Any]]] = {}
    config_hashes: dict[int, str] = {}
    for ns in (1, 2, 4):
        entry = paths[ns]
        reviews[ns] = _read_json(entry["review"])
        params[ns] = _read_run_params(entry["run_log"])
        provenance_path = entry.get("provenance")
        provenances[ns] = _read_json(provenance_path) if provenance_path else None
        count = SOURCE_COUNTS[ns]
        frames[ns] = _read_frame_metadata(entry["frames"], count)
        config_hashes[ns] = _sha256(entry["config"])
    library_hash = _sha256(root / "config/materials_library.yaml")
    return analyze_archives(reviews, params, provenances, frames, config_hashes, library_hash)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="repository root containing the frozen scratchpad artifacts")
    args = parser.parse_args(argv)
    try:
        result = run_review(default_archive_paths(args.root), args.root)
    except Exception as exc:  # CLI is machine-readable on input failure too.
        result = {"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
