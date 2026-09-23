"""Offline run-current sector comparison using production terminal_work_mean.

The archive parity validator is reused before this tool reads linkage arrays.
Only saved samples are loaded; there is no FEM solve, API call or filtering.
"""
from __future__ import annotations

import argparse
import hashlib
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
from scripts import torque_sector_parity_review as parity  # noqa: E402


POLE_PAIRS = 14
N_PARALLEL = 1
PROVISIONAL_MEAN_DELTA_LIMIT_NM = parity.GATES["max_abs_mean_delta_Nm"]
EXPECTED_SIGNS = dict(parity.EXPECTED_BC_SIGN)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _complex_rows(values: np.ndarray, omega: np.ndarray, *, derivative: bool = False) -> list[dict[str, float | int]]:
    """Serialize every full DFT bin, retaining signed-frequency conjugate bins."""
    bins = np.fft.fft(np.asarray(values, dtype=float)) / len(values)
    if derivative:
        bins = 1j * omega * bins
    rows = []
    for index, coeff in enumerate(bins):
        signed_bin = int(round(float(np.fft.fftfreq(len(values))[index] * len(values))))
        rows.append({
            "fft_bin": index,
            "signed_electrical_order": signed_bin,
            "coefficient_real": float(coeff.real),
            "coefficient_imag": float(coeff.imag),
        })
    return rows


def evaluate_modes(
    modes: Mapping[int, Mapping[str, Any]],
    *,
    parity_validation: Mapping[str, Any],
    helper: Callable[..., float] = terminal_work_mean,
    mean_delta_limit_nm: float = PROVISIONAL_MEAN_DELTA_LIMIT_NM,
) -> dict[str, Any]:
    """Apply production helper to already matched, unmodified mode records."""
    if parity_validation.get("passed") is not True:
        errors = parity_validation.get("errors", [])
        raise ValueError(f"raw sector parity/provenance validation failed: {errors}")
    if set(modes) != {1, 2, 4}:
        raise ValueError("NS1, NS2 and NS4 records are all required")
    prepared: dict[int, dict[str, np.ndarray | int]] = {}
    for ns in (1, 2, 4):
        row = modes[ns]
        if int(row.get("effective_ns", -1)) != ns:
            raise ValueError(f"NS{ns}: effective built sector count mismatch")
        if int(row.get("bc_sign", 0)) != EXPECTED_SIGNS[ns]:
            raise ValueError(f"NS{ns}: boundary sign mismatch")
        arrays = {key: np.asarray(row[key], dtype=float) for key in
                  ("angle_rad", "currents_A", "linkages_Wb", "raw_maxwell_Nm")}
        if arrays["angle_rad"].shape != (20,) or arrays["currents_A"].shape != (3, 20) \
                or arrays["linkages_Wb"].shape != (3, 20) \
                or arrays["raw_maxwell_Nm"].shape != (20,):
            raise ValueError(f"NS{ns}: expected exactly 20 matched raw samples")
        if not all(np.all(np.isfinite(value)) for value in arrays.values()):
            raise ValueError(f"NS{ns}: non-finite matched input")
        if not np.all(np.diff(arrays["angle_rad"]) > 0):
            raise ValueError(f"NS{ns}: mechanical angle must retain positive signed order")
        prepared[ns] = {**arrays, "bc_sign": EXPECTED_SIGNS[ns]}

    ref_angle = prepared[1]["angle_rad"]
    ref_current = prepared[1]["currents_A"]
    angle_deltas: dict[int, float] = {1: 0.0}
    current_deltas: dict[int, float] = {1: 0.0}
    for ns in (2, 4):
        angle_deltas[ns] = float(np.max(np.abs(prepared[ns]["angle_rad"] - ref_angle)))
        current_deltas[ns] = float(np.max(np.abs(prepared[ns]["currents_A"] - ref_current)))
        if not np.allclose(prepared[ns]["angle_rad"], ref_angle, rtol=0, atol=1e-12):
            raise ValueError(f"NS{ns}: matched mechanical angles differ from NS1")
        if not np.allclose(prepared[ns]["currents_A"], ref_current, rtol=0, atol=1e-12):
            raise ValueError(f"NS{ns}: matched three-phase currents differ from NS1")

    modes_out: dict[str, Any] = {}
    for ns in (1, 2, 4):
        row = prepared[ns]
        angles = np.asarray(row["angle_rad"])
        current = np.asarray(row["currents_A"])
        psi = np.asarray(row["linkages_Wb"])
        step = float(np.diff(angles)[0])
        omega = 2 * np.pi * np.fft.fftfreq(angles.size, d=step)
        psi_fft = np.fft.fft(psi, axis=1) / angles.size
        derivative_operator_bins = 1j * omega[None, :] * psi_fft
        derivative = np.fft.ifft(
            1j * omega[None, :] * np.fft.fft(psi, axis=1), axis=1
        ).real
        # Call the current production implementation on the exact saved arrays.
        selected_mean = float(helper(
            *psi, *current, angles, POLE_PAIRS, n_parallel=N_PARALLEL))
        reconstructed = float(N_PARALLEL * np.mean(np.sum(current * derivative, axis=0)))
        if not math.isclose(selected_mean, reconstructed, rel_tol=0, abs_tol=1e-12):
            raise ValueError(f"NS{ns}: production helper and full-DFT evidence disagree")
        modes_out[str(ns)] = {
            "effective_ns": ns,
            "bc_sign": int(row["bc_sign"]),
            "sample_count": 20,
            "mechanical_angle_rad": angles.tolist(),
            "phase_current_A": {ph: current[i].tolist() for i, ph in enumerate("ABC")},
            "phase_linkage_Wb_full_machine": {ph: psi[i].tolist() for i, ph in enumerate("ABC")},
            "raw_maxwell_torque_Nm_diagnostic_only": np.asarray(row["raw_maxwell_Nm"]).tolist(),
            "raw_maxwell_mean_Nm_diagnostic_only": float(np.mean(row["raw_maxwell_Nm"])),
            "raw_maxwell_peak_to_peak_Nm_diagnostic_only": float(np.ptp(row["raw_maxwell_Nm"])),
            "production_terminal_work_mean_Nm": selected_mean,
            "derivative_dot_current_reconstruction_Nm": reconstructed,
            "production_reconstruction_abs_delta_Nm": abs(selected_mean - reconstructed),
            "dpsi_dmechanical_angle_Wb_per_rad": {
                ph: derivative[i].tolist() for i, ph in enumerate("ABC")},
            "full_dft_all_bins_by_phase": {
                ph: {
                    "current": _complex_rows(current[i], omega),
                    "linkage": _complex_rows(psi[i], omega),
                    "derivative_operator_applied_to_linkage": _complex_rows(
                        psi[i], omega, derivative=True),
                    "effective_real_grid_derivative": _complex_rows(derivative[i], omega),
                }
                for i, ph in enumerate("ABC")
            },
            "nyquist_bin": {
                "bin": 10,
                "production_real_grid_derivative_convention": "real(ifft(j*omega*fft(psi))); the real Nyquist derivative is zero",
                "effective_derivative_bin_max_abs": float(np.max(np.abs(
                    np.fft.fft(derivative, axis=1)[:, 10] / angles.size))),
            },
            "scaling": {
                "pole_pairs_for_production_helper": POLE_PAIRS,
                "n_parallel_for_production_helper": N_PARALLEL,
                "extra_sector_multiplier_applied_to_linkage": False,
                "linkage_convention": "solver convention is stack_length*effective_NS/n_parallel times the winding source pairing; archived terminal phase linkage is passed unchanged with n_parallel=1",
                "source_pairing_recomputed_from_frame": False,
                "source_pairing_limitation": "these symmetry frame NPZs do not save per-phase coil source vectors needed to independently recompute A·f_coil",
            },
        }

    comparisons = {}
    for ns in (2, 4):
        nsm = modes_out[str(ns)]
        ref = modes_out["1"]
        terminal_delta = nsm["production_terminal_work_mean_Nm"] - ref["production_terminal_work_mean_Nm"]
        maxwell_delta = nsm["raw_maxwell_mean_Nm_diagnostic_only"] - ref["raw_maxwell_mean_Nm_diagnostic_only"]
        comparisons[str(ns)] = {
            "terminal_work_mean_delta_vs_ns1_Nm": terminal_delta,
            "terminal_work_mean_abs_delta_vs_ns1_Nm": abs(terminal_delta),
            "raw_maxwell_mean_delta_vs_ns1_diagnostic_only_Nm": maxwell_delta,
            "max_actual_mechanical_angle_delta_rad": angle_deltas[ns],
            "max_three_phase_current_delta_A": current_deltas[ns],
            "terminal_work_mean_gate": {
                "passed": abs(terminal_delta) <= mean_delta_limit_nm,
                "provisional_limit_Nm": float(mean_delta_limit_nm),
            },
        }
    return {
        "passed": all(row["terminal_work_mean_gate"]["passed"] for row in comparisons.values()),
        "certified": False,
        "scope": "current production terminal_work_mean applied to archived 24s28p NS1/NS2/NS4 common 20-angle samples",
        "modes": modes_out,
        "comparisons_vs_ns1": comparisons,
        "mean_gate": "provisional only; same-grid symmetry sensitivity, not an energy closure or continuum test",
        "all_samples_and_bins_retained": True,
        "filters_or_harmonic_deletion": False,
        "mesh_refinement_performed": False,
        "interpretation": "All 20 saved samples per mode and every full DFT bin are retained. The result tests sampled production-helper sensitivity across archived sector modes only; it does not certify stored-energy closure, alias-free ripple, continuum torque, or other operating points.",
    }


def _load_modes(root: Path, validation: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    paths = parity.default_archive_paths(root)
    matched = list(parity.COMMON_SHIFTS)
    output: dict[int, dict[str, Any]] = {}
    for ns in (1, 2, 4):
        review = parity._read_json(paths[ns]["review"])
        shift_key = "grid_shifts" if ns in (1, 2) else "shifts"
        torque_key = "sector_scaled_torque_Nm" if ns in (1, 2) else "torque_scaled_Nm"
        shifts = [int(value) for value in review[shift_key]]
        index_by_shift = {shift: index for index, shift in enumerate(shifts)}
        indices = [index_by_shift[shift] for shift in matched]
        current_review = np.asarray(review["current_A"], dtype=float)
        psi_key = "flux_linkages_Wb" if ns in (1, 2) else "psi_Wb"
        psi_review = np.asarray(review[psi_key], dtype=float)
        angle_review = np.asarray(review["angle_deg"], dtype=float)
        raw_current, raw_psi, raw_angle, raw_torque, raw_sign = [], [], [], [], []
        for index in indices:
            with np.load(paths[ns]["frames"] / f"frame_{index}.npz", allow_pickle=False) as frame:
                current = np.asarray(frame["currents_A"], dtype=float)
                psi = np.asarray(frame["psi_Wb"], dtype=float)
                angle = float(frame["angle_deg"])
                torque_sector = float(frame["torque_sector_Nm"])
                sign = int(frame["bc_sign"])
            if current.shape != (3,) or psi.shape != (3,):
                raise ValueError(f"NS{ns} raw frame {index}: current/linkage must have 3 phases")
            if not np.allclose(current, current_review[index], rtol=0, atol=1e-12):
                raise ValueError(f"NS{ns} raw frame {index}: current differs from validated review")
            if not np.allclose(psi, psi_review[index], rtol=0, atol=1e-14):
                raise ValueError(f"NS{ns} raw frame {index}: linkage differs from review")
            if not math.isclose(angle, float(angle_review[index]), rel_tol=0, abs_tol=1e-12):
                raise ValueError(f"NS{ns} raw frame {index}: angle differs from review")
            raw_current.append(current)
            raw_psi.append(psi)
            raw_angle.append(math.radians(angle))
            raw_torque.append(float(review[torque_key][index]))
            raw_sign.append(sign)
        if len(set(raw_sign)) != 1:
            raise ValueError(f"NS{ns}: boundary sign changes within matched samples")
        output[ns] = {
            "effective_ns": ns,
            "bc_sign": raw_sign[0],
            "angle_rad": np.asarray(raw_angle, dtype=float),
            "currents_A": np.asarray(raw_current, dtype=float).T.copy(),
            "linkages_Wb": np.asarray(raw_psi, dtype=float).T.copy(),
            "raw_maxwell_Nm": np.asarray(raw_torque, dtype=float),
        }
    return output


def run_review(root: Path = ROOT) -> dict[str, Any]:
    root = Path(root)
    validation = parity.run_review(parity.default_archive_paths(root), root)
    if not validation.get("passed"):
        return {
            "passed": False,
            "certified": False,
            "errors": validation.get("errors", []),
            "source_provenance": validation.get("source_provenance"),
        }
    modes = _load_modes(root, validation)
    result = evaluate_modes(modes, parity_validation=validation)
    postproc = root / "src/motor_ai_sim/simulation/sb_postproc.py"
    result["source_provenance"] = {
        "sector_parity_validation_passed": True,
        "archive_provenance": validation["source_provenance"],
        "production_sb_postproc_sha256_at_review": _sha256(postproc),
        "common_mechanical_angle_deg": np.rad2deg(modes[1]["angle_rad"]).tolist(),
        "ns4_source_count": validation["comparison_grid"]["source_sample_count"]["4"],
        "ns4_original_sample_count_retained_in_archive": 60,
        "ns4_matched_nested_indices": validation["comparison_grid"]["ns4_selected_nested_comparison_indices"],
        "ns4_matched_nested_shifts": validation["comparison_grid"]["ns4_selected_nested_comparison_shifts"],
    }
    result["full_dft_convention"] = {
        "transform": "full fft(samples) / 20 for current/linkage and derivative evidence",
        "all_bins_retained": list(range(20)),
        "samples_demeaned": False,
        "derivative": "ifft(1j*2π*fftfreq(20,d=actual signed mechanical step)*fft(psi)).real",
        "nyquist": "the production real-grid derivative has zero real Nyquist-bin contribution",
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = run_review(args.root)
    except Exception as exc:
        result = {"passed": False, "certified": False,
                  "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
