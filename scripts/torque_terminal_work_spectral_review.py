"""Full signed-bin Parseval attribution for archived terminal-work sector means."""
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
from scripts import torque_terminal_work_sector_review as sector_review  # noqa: E402


POLE_PAIRS = 14
N_PARALLEL = 1
PARSEVAL_ATOL_NM = 2e-12
PHASES = "ABC"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: complex) -> dict[str, float]:
    return {"real": float(value.real), "imag": float(value.imag)}


def _signed_orders(n: int) -> list[int]:
    return [int(round(float(f * n))) for f in np.fft.fftfreq(n)]


def _validate_mode(ns: int, row: Mapping[str, Any]) -> dict[str, Any]:
    if int(row.get("effective_ns", -1)) != ns:
        raise ValueError(f"NS{ns}: effective sector count mismatch")
    expected_sign = parity.EXPECTED_BC_SIGN[ns]
    if int(row.get("bc_sign", 0)) != expected_sign:
        raise ValueError(f"NS{ns}: BC sign mismatch")
    angle = np.asarray(row["angle_rad"], dtype=float)
    current = np.asarray(row["currents_A"], dtype=float)
    psi = np.asarray(row["linkages_Wb"], dtype=float)
    if angle.shape != (20,) or current.shape != (3, 20) or psi.shape != (3, 20):
        raise ValueError(f"NS{ns}: expected 20 signed-angle/current/linkage samples")
    if not all(np.all(np.isfinite(value)) for value in (angle, current, psi)):
        raise ValueError(f"NS{ns}: non-finite DFT input")
    step = np.diff(angle)
    if not np.all(step > 0) or not np.allclose(step, step[0], rtol=1e-10, atol=1e-12):
        raise ValueError(f"NS{ns}: angles must be uniformly increasing actual mechanical radians")
    if not math.isclose(20 * step[0] * POLE_PAIRS / (2 * np.pi), 1.0,
                        rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f"NS{ns}: sample grid is not one endpoint-excluded electrical period")
    return {"angle": angle, "current": current, "psi": psi, "bc_sign": expected_sign}


def attribute_modes(
    modes: Mapping[int, Mapping[str, Any]],
    *,
    parity_validation: Mapping[str, Any],
    helper: Callable[..., float] = terminal_work_mean,
) -> dict[str, Any]:
    """Compute per-phase/per-bin complex Parseval contributions; retain all bins."""
    if parity_validation.get("passed") is not True:
        raise ValueError(f"raw parity/provenance validation failed: {parity_validation.get('errors', [])}")
    if set(modes) != {1, 2, 4}:
        raise ValueError("NS1, NS2 and NS4 are required")
    prepared = {ns: _validate_mode(ns, modes[ns]) for ns in (1, 2, 4)}
    ref_angle, ref_current = prepared[1]["angle"], prepared[1]["current"]
    for ns in (2, 4):
        if not np.allclose(prepared[ns]["angle"], ref_angle, rtol=0, atol=1e-12):
            raise ValueError(f"NS{ns}: common signed mechanical angles mismatch")
        if not np.allclose(prepared[ns]["current"], ref_current, rtol=0, atol=1e-12):
            raise ValueError(f"NS{ns}: common current samples mismatch")

    mode_reports: dict[str, Any] = {}
    compact_contributions: dict[int, np.ndarray] = {}
    compact_phase_contributions: dict[int, np.ndarray] = {}
    for ns in (1, 2, 4):
        angle, currents, psi = (prepared[ns][key] for key in ("angle", "current", "psi"))
        n = len(angle)
        omega = 2 * np.pi * np.fft.fftfreq(n, d=float(angle[1] - angle[0]))
        i_hat = np.fft.fft(currents, axis=1) / n
        psi_hat = np.fft.fft(psi, axis=1) / n
        # The first spectrum is the formal multiplier result. The second is
        # the actual real-grid derivative spectrum used by production helper.
        operator_d_hat = 1j * omega[None, :] * psi_hat
        dpsi = np.fft.ifft(1j * omega[None, :] * np.fft.fft(psi, axis=1), axis=1).real
        d_hat = np.fft.fft(dpsi, axis=1) / n
        complex_by_phase_bin = np.conj(i_hat) * d_hat
        bin_sum = np.sum(complex_by_phase_bin, axis=0)
        parseval_mean_complex = N_PARALLEL * np.sum(bin_sum)
        helper_mean = float(helper(*psi, *currents, angle, POLE_PAIRS,
                                   n_parallel=N_PARALLEL))
        direct_mean = float(N_PARALLEL * np.mean(np.sum(currents * dpsi, axis=0)))
        closure_delta = float(parseval_mean_complex.real - helper_mean)
        if abs(parseval_mean_complex.imag) > PARSEVAL_ATOL_NM:
            raise ValueError(f"NS{ns}: Parseval sum has nonzero imaginary residual")
        if abs(closure_delta) > PARSEVAL_ATOL_NM or not math.isclose(
                direct_mean, helper_mean, rel_tol=0, abs_tol=PARSEVAL_ATOL_NM):
            raise ValueError(f"NS{ns}: full-bin Parseval sum does not close to production mean")

        per_phase: dict[str, Any] = {}
        for phase_i, phase in enumerate(PHASES):
            phase_bins = []
            for k, order in enumerate(_signed_orders(n)):
                phase_bins.append({
                    "fft_bin": k,
                    "signed_electrical_order": order,
                    "current_coefficient_A": _number(i_hat[phase_i, k]),
                    "linkage_coefficient_Wb": _number(psi_hat[phase_i, k]),
                    "derivative_operator_coefficient_Wb_per_rad": _number(operator_d_hat[phase_i, k]),
                    "effective_real_grid_derivative_coefficient_Wb_per_rad": _number(d_hat[phase_i, k]),
                    "complex_mean_contribution_Nm": _number(N_PARALLEL * complex_by_phase_bin[phase_i, k]),
                    "real_mean_contribution_Nm": float(N_PARALLEL * complex_by_phase_bin[phase_i, k].real),
                })
            per_phase[phase] = {
                "current_samples_A": currents[phase_i].tolist(),
                "linkage_samples_Wb": psi[phase_i].tolist(),
                "derivative_samples_Wb_per_rad": dpsi[phase_i].tolist(),
                "sum_of_all_signed_bin_contributions_Nm": float(
                    N_PARALLEL * np.sum(complex_by_phase_bin[phase_i]).real),
                "all_bins": phase_bins,
            }
        summed_bins = []
        for k, order in enumerate(_signed_orders(n)):
            c = N_PARALLEL * bin_sum[k]
            summed_bins.append({
                "fft_bin": k,
                "signed_electrical_order": order,
                "sum_phase_complex_mean_contribution_Nm": _number(c),
                "sum_phase_real_mean_contribution_Nm": float(c.real),
                "phase_real_contributions_Nm": {
                    phase: float(N_PARALLEL * complex_by_phase_bin[p, k].real)
                    for p, phase in enumerate(PHASES)
                },
            })
        compact_contributions[ns] = N_PARALLEL * bin_sum
        compact_phase_contributions[ns] = N_PARALLEL * complex_by_phase_bin
        mode_reports[str(ns)] = {
            "effective_ns": ns,
            "bc_sign": prepared[ns]["bc_sign"],
            "sample_count": n,
            "actual_mechanical_angle_rad": angle.tolist(),
            "production_terminal_work_mean_Nm": helper_mean,
            "direct_sample_reconstruction_Nm": direct_mean,
            "parseval_full_complex_bin_sum_Nm": float(parseval_mean_complex.real),
            "parseval_complex_sum": _number(parseval_mean_complex),
            "parseval_minus_production_Nm": closure_delta,
            "per_phase": per_phase,
            "summed_all_signed_bins": summed_bins,
            "nyquist": {
                "fft_bin": 10,
                "signed_order_label": -10,
                "input_linkage_coefficient_Wb_by_phase": {
                    phase: _number(psi_hat[p, 10]) for p, phase in enumerate(PHASES)},
                "operator_derivative_coefficient_Wb_per_rad_by_phase": {
                    phase: _number(operator_d_hat[p, 10]) for p, phase in enumerate(PHASES)},
                "effective_real_derivative_coefficient_Wb_per_rad_by_phase": {
                    phase: _number(d_hat[p, 10]) for p, phase in enumerate(PHASES)},
                "real_grid_mean_contribution_Nm": float(N_PARALLEL * bin_sum[10].real),
                "interpretation": "The Nyquist linkage bin is retained; its formal derivative lies in an unrepresented imaginary quadrature, so production real(ifft) gives zero effective derivative contribution.",
            },
        }

    comparisons: dict[str, Any] = {}
    for ns in (2, 4):
        delta = compact_contributions[ns] - compact_contributions[1]
        comparisons[str(ns)] = {
            "production_mean_delta_vs_ns1_Nm": float(
                mode_reports[str(ns)]["production_terminal_work_mean_Nm"]
                - mode_reports["1"]["production_terminal_work_mean_Nm"]),
            "parseval_delta_vs_ns1_Nm": float(np.sum(delta).real),
            "delta_complex_sum_Nm": _number(np.sum(delta)),
            "all_signed_bin_deltas": [
                {"fft_bin": k, "signed_electrical_order": order,
                 "complex_delta_Nm": _number(delta[k]),
                 "real_delta_Nm": float(delta[k].real)}
                for k, order in enumerate(_signed_orders(20))
            ],
            "per_phase_bin_deltas_Nm": {
                phase: [
                    {
                        "fft_bin": k,
                        "signed_electrical_order": order,
                        "complex_delta_Nm": _number(
                            compact_phase_contributions[ns][p, k]
                            - compact_phase_contributions[1][p, k]),
                    }
                    for k, order in enumerate(_signed_orders(20))
                ] for p, phase in enumerate(PHASES)
            },
            "interpretation": "Every signed bin is reported, including roundoff-level entries. A zero or small sampled contribution is an orthogonality result on this grid, not a filtered or deleted term.",
        }
    return {
        "passed": True,
        "certified": False,
        "scope": "Parseval arithmetic attribution of production terminal_work_mean for the archived matched NS1/NS2/NS4 20-point grid",
        "parseval_tolerance_Nm": PARSEVAL_ATOL_NM,
        "modes": mode_reports,
        "mean_deltas_vs_ns1": comparisons,
        "spectral_convention": {
            "normalized_coefficients": "FFT(samples) / N",
            "mean_product_identity": "mean(i*dpsi) = sum_k conj(I_k)*D_k over all N signed bins",
            "retained_fft_bins": list(range(20)),
            "signed_electrical_orders": _signed_orders(20),
            "positive_negative_pairs_retained": True,
            "demeaned": False,
            "filters_or_bin_deletion": False,
            "aliasing_note": "Orders separated by integer multiples of N=20 are identical on this sampled grid; source harmonics above Nyquist can alias into bins with current support.",
        },
        "interpretation": "Pass means only that full-bin arithmetic closes to the production helper on these archived samples. This is not an energy closure or physical torque certificate.",
    }


def run_review(root: Path = ROOT) -> dict[str, Any]:
    root = Path(root)
    paths = parity.default_archive_paths(root)
    validation = parity.run_review(paths, root)
    if not validation.get("passed"):
        return {"passed": False, "certified": False,
                "errors": validation.get("errors", [])}
    modes = sector_review._load_modes(root, validation)
    report = attribute_modes(modes, parity_validation=validation)
    report["source_provenance"] = {
        "archive": validation["source_provenance"],
        "production_helper_sha256": _sha256(root / "src/motor_ai_sim/simulation/sb_postproc.py"),
        "matched_shifts": validation["comparison_grid"]["common_slip_shifts"],
        "ns4_source_sample_count": validation["comparison_grid"]["source_sample_count"]["4"],
        "ns4_matched_nested_indices": validation["comparison_grid"]["ns4_selected_nested_comparison_indices"],
    }
    return report


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
