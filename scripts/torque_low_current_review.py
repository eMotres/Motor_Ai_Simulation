"""Read-only review of archived low-current selector cases 0–3."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scratchpad" / "torque_low_current_20260923_run17"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def spectral_periodic_work(current_a, flux_wb, angle_rad, *, pole_pairs, parallel_branches=1):
    """Endpoint-excluded periodic DFT sum(i dψ/dθ); diagnostic only.

    All resolved Fourier bins are used. The even-grid Nyquist cross term has
    zero real contribution; its unsampled phase is intrinsically ambiguous.
    No current/flux sample or AC torque component is filtered.
    """
    i, psi, angle = (np.asarray(x, float) for x in (current_a, flux_wb, angle_rad))
    if i.ndim != 2 or psi.shape != i.shape or angle.shape != (i.shape[1],) or i.shape[1] < 8:
        raise ValueError("Matching phase/sample arrays with at least 8 samples required")
    if not all(np.all(np.isfinite(x)) for x in (i, psi, angle)):
        raise ValueError("Finite waveforms and angles required")
    if int(pole_pairs) != pole_pairs or pole_pairs <= 0:
        raise ValueError("Positive integer pole-pair count required")
    if int(parallel_branches) != parallel_branches or parallel_branches <= 0:
        raise ValueError("Positive integer parallel count required")
    steps = np.diff(angle)
    step = float(np.mean(steps))
    if step == 0 or not np.allclose(steps, step, rtol=1e-9, atol=1e-12):
        raise ValueError("Uniform signed mechanical angles required")
    n = angle.size
    if not np.isclose(abs(n*step), 2*np.pi/int(pole_pairs), rtol=1e-9, atol=1e-12):
        raise ValueError("Samples must cover one endpoint-excluded electrical period")
    omega = 2*np.pi*np.fft.fftfreq(n, d=step)  # radian^-1 in MECHANICAL angle
    I = np.fft.fft(i, axis=1)
    Psi = np.fft.fft(psi, axis=1)
    cross = np.conj(I)*(1j*omega[None, :])*Psi
    return float(parallel_branches*np.real(np.sum(cross))/n**2)


def analyze(directory=DATA):
    directory = Path(directory)
    rows = []
    base_source = None
    for case in range(4):
        folder = directory / f"case_{case:02d}"
        review = json.loads((folder / "case_review.json").read_text())
        provenance = json.loads((folder / "provenance.json").read_text())
        if sha(folder / "raw_result.json") != review["raw_result_sha256"]:
            raise ValueError(f"Case {case}: raw result hash differs")
        if sha(folder / "raw_waveforms.npz") != review["raw_waveforms_sha256"]:
            raise ValueError(f"Case {case}: raw waveform hash differs")
        if base_source is None:
            base_source = provenance["source_hashes"]
        elif provenance["source_hashes"] != base_source:
            raise ValueError(f"Case {case}: pre-run source differs from case 0")
        if case < 3 and not review["source_hashes_unchanged"]:
            raise ValueError(f"Case {case}: unexpected source change")
        if case == 3 and review["source_hashes_unchanged"]:
            source_status = "unchanged"
        elif case == 3:
            source_status = ("pre-run hashes match cases 0–2; sb_postproc.py changed on disk "
                             "after import, in snapshot/retention metadata only per parent diff audit; "
                             "raw/convergence data retained, no rerun")
        else:
            source_status = "unchanged"
        with np.load(folder / "raw_waveforms.npz", allow_pickle=False) as raw:
            i, psi = raw["currents_a"], raw["flux_linkages_wb"]
            angle = np.deg2rad(raw["rotor_angle_deg"])
            maxwell = raw["torque_maxwell_nm"]
            selected = raw["torque_selected_nm"]
        if (i.shape != (3, 48) or review["n_samples"] != 48
                or not review["picard_converged"] or review["picard_unconverged_frames"]
                or review["n_parallel_eff"] != 1 or review["slip_nodes_per_period"] != 144):
            raise ValueError(f"Case {case}: output shape/convergence/mesh cadence differs")
        actual_peak = float(np.max(np.abs(i)))
        if abs(actual_peak-review["requested_peak_per_branch_A"]) > 1e-9:
            raise ValueError(f"Case {case}: achieved current peak differs")
        raw_mean, selected_mean = float(np.mean(maxwell)), float(np.mean(selected))
        if (abs(raw_mean-review["raw_maxwell_mean_Nm"]) > 1e-10
                or abs(selected_mean-review["selected_mean_Nm"]) > 1e-10):
            raise ValueError(f"Case {case}: saved waveform and review mean differ")
        rows.append({
            "case": case, "peak_per_branch_A": actual_peak,
            "selected_method": review["selected_method"],
            "raw_maxwell_mean_Nm": raw_mean,
            "space_vector_candidate_Nm": review["torque_method_diagnostics"]["space_vector_mean_candidate_Nm"],
            "selected_mean_Nm": selected_mean,
            "spectral_terminal_work_candidate_Nm": spectral_periodic_work(
                i, psi, angle, pole_pairs=7, parallel_branches=1),
            "source_status": source_status,
        })
    left, right = rows[2], rows[3]
    return {
        "status": "uncertified_low_current_characterization",
        "rows": rows,
        "boundary_1_to_1p001_selected_jump_Nm": right["selected_mean_Nm"]-left["selected_mean_Nm"],
        "boundary_1_to_1p001_space_vector_increment_Nm": right["space_vector_candidate_Nm"]-left["space_vector_candidate_Nm"],
        "boundary_1_to_1p001_maxwell_increment_Nm": right["raw_maxwell_mean_Nm"]-left["raw_maxwell_mean_Nm"],
        "note": "DFT work uses one endpoint-excluded sampled period and all resolved bins, with no extra /pole_pairs. It assumes periodic continuation of terminal samples; no endpoint magnetic state or independent low-current virtual work was captured. Zero-current work is zero and does not assess PM cogging.",
    }


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
