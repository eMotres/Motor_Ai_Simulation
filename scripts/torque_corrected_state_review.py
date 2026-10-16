"""Read-only energy and terminal-work review of selected corrected P2 states."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from motor_ai_sim.simulation.field_ops import MU0, _mu_r_from_bh_vec  # noqa: E402
from torque_offline_closure import terminal_work_path  # noqa: E402

RUN = ROOT / "scratchpad" / "torque_corrected_state_20260923_run15"
FRAMES = (0, 1, 48, 49)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_frame_metadata(metadata, reference):
    for key in ("eddy", "demag", "rotor_eddy", "frozen_nu", "voltage_drive"):
        if metadata.get(key) is not False:
            raise ValueError(f"Ineligible frame mode: {key}")
    if metadata.get("imposed_current_drive") is not True:
        raise ValueError("Imposed-current drive is required")
    if metadata.get("newton_converged") is not True or metadata.get("picard_unconverged") is not False:
        raise ValueError("Selected frame did not converge by accepted Newton path")
    for key in ("n_parallel", "n_sectors", "stack_length_m", "pole_pairs"):
        if metadata.get(key) != reference.get(key):
            raise ValueError(f"Selected-frame configuration differs: {key}")
    if metadata["n_parallel"] < 1 or metadata["n_sectors"] < 1 or metadata["stack_length_m"] <= 0:
        raise ValueError("Invalid selected-frame scale")


def source_linkage_mismatch(metadata, state):
    scale = (metadata["stack_length_m"] * metadata["n_sectors"]
             / metadata["n_parallel"])
    diffs = {}
    for ph in "ABC":
        source = scale * float(np.dot(state["A_z"], state[f"coil_source_vectors_per_A__{ph}"]))
        diffs[ph] = float(metadata["psi_abc_Wb"][ph]) - source
    return diffs


def curve_h_energy(values, curve):
    """Piecewise-linear production H(B) and its exact zero-referenced integral."""
    b = np.asarray(values, float)
    c = np.asarray(curve, float)
    hs, bs = c[:, 0], c[:, 1]
    if bs[0] > 0:
        hs, bs = np.r_[0., hs], np.r_[0., bs]
    if bs[0] != 0 or np.any(np.diff(bs) <= 0) or np.any(b < 0):
        raise ValueError("Invalid monotone B-H curve or B values")
    H = np.zeros_like(b)
    U = np.zeros_like(b)
    previous_energy = 0.
    for j in range(len(bs) - 1):
        lo, hi = bs[j:j+2]
        slope = (hs[j+1] - hs[j]) / (hi - lo)
        mask = (b > lo) & (b <= hi)
        d = b[mask] - lo
        H[mask] = hs[j] + slope*d
        U[mask] = previous_energy + hs[j]*d + .5*slope*d*d
        width = hi-lo
        previous_energy += hs[j]*width + .5*slope*width*width
    above = b > bs[-1]
    extra = b[above]-bs[-1]
    H[above] = hs[-1] + extra/MU0
    U[above] = previous_energy + hs[-1]*extra + .5*extra*extra/MU0
    return H, U


def analyze(directory=RUN):
    directory = Path(directory)
    provenance = json.loads((directory / "provenance.json").read_text())
    capture = json.loads((directory / "capture_review.json").read_text())
    if capture["selected_frames_captured"] != list(FRAMES) or capture["n_samples"] != 96:
        raise ValueError("Incomplete saved run")
    if not capture["picard_converged"] or capture["picard_unconverged_frames"]:
        raise ValueError("Saved run contains unconverged frames")
    if digest(directory / "raw_waveforms.npz") != capture["raw_waveforms_sha256"]:
        raise ValueError("Waveform hash differs from capture review")
    if provenance["source_hashes"]["capture_probe.py"] != digest(ROOT / "scratchpad/torque_corrected_state_probe_20260923.py"):
        raise ValueError("Capture runner source changed since run")
    if provenance["source_hashes"]["field_ops.py"] != digest(ROOT / "src/motor_ai_sim/simulation/field_ops.py"):
        raise ValueError("Production B-H law source changed since capture")
    metadata = [json.loads((directory / f"frame_{k:02d}.json").read_text()) for k in FRAMES]
    states = []
    for k in FRAMES:
        with np.load(directory / f"frame_{k:02d}.npz", allow_pickle=False) as saved:
            states.append({key: saved[key] for key in saved.files})
    for meta in metadata:
        check_frame_metadata(meta, metadata[0])
    ref = states[0]
    static_keys = ("mesh_p_m", "mesh_t", "element_dofs", "quadrature_dx_m2",
                   "nu_base2", "Hc_x_effective_Apm", "Hc_y_effective_Apm",
                   "magnet_source_vector")
    for state in states[1:]:
        for key in static_keys:
            if not np.array_equal(state[key], ref[key]):
                raise ValueError(f"Captured static assembly differs: {key}")
    dx = ref["quadrature_dx_m2"]
    nu = ref["nu_base2"]
    curves = json.loads((directory / "assembled_curves.json").read_text())
    factor = metadata[0]["stack_length_m"] * metadata[0]["n_sectors"]
    energies = []
    pm_errors = []
    max_H_error = 0.
    floor_violations = 0
    floor_path_knot_violations = 0
    sampled_b = 0
    for state in states:
        bx, by = state["Bx_quad_T"], state["By_quad_T"]
        if bx.shape != dx.shape or by.shape != dx.shape:
            raise ValueError("Quadrature B and weights differ")
        B = np.hypot(bx, by)
        density = .5 * nu[:, None] * B*B
        for row in curves:
            ids = np.asarray(row["element_ids"], int)
            values = B[ids]
            if np.any(values <= 1e-12):
                raise ValueError("Saved nonlinear B enters production low-field H branch")
            H, U = curve_h_energy(values, row["H_B_curve"])
            density[ids] = U
            knot_b = np.asarray([0.] + [float(pair[1]) for pair in row["H_B_curve"]
                                        if 0 < float(pair[1]) <= float(np.max(values))]
                                + [float(np.max(values))])
            knot_h, _ = curve_h_energy(knot_b, row["H_B_curve"])
            floor_path_knot_violations += int(np.sum(knot_h > knot_b/MU0 + 1e-6))
            mu = _mu_r_from_bh_vec(row["H_B_curve"], values)
            max_H_error = max(max_H_error, float(np.max(np.abs(H - values/(MU0*mu)))))
            floor_violations += int(np.sum(H > values/MU0 + 1e-6))
            sampled_b += values.size
        pm_density = -(state["Hc_x_effective_Apm"][:, None]*bx +
                       state["Hc_y_effective_Apm"][:, None]*by)
        pm_field = factor * float(np.sum(pm_density*dx))
        pm_load = -factor * float(np.dot(state["A_z"], state["magnet_source_vector"]))
        pm_errors.append(abs(pm_field-pm_load))
        energies.append(factor * float(np.sum((density+pm_density)*dx)))
    if floor_violations or floor_path_knot_violations:
        raise ValueError("Production permeability floor activates on saved B or integration path")
    with np.load(directory / "raw_waveforms.npz", allow_pickle=False) as saved:
        i, psi = saved["currents_a"], saved["flux_linkages_wb"]
        theta = np.deg2rad(saved["rotor_angle_deg"])
        maxwell = saved["torque_maxwell_nm"]
        hybrid = saved["torque_hybrid_nm"]
    for k, meta in zip(FRAMES, metadata):
        if not np.isclose(theta[k], meta["mechanical_angle_rad"], rtol=0, atol=1e-12):
            raise ValueError("Saved waveform and frame angle differ")
        for p, ph in enumerate("ABC"):
            if abs(i[p, k]-meta["current_abc_A"][ph]) > 1e-12 or abs(psi[p, k]-meta["psi_abc_Wb"][ph]) > 1e-15:
                raise ValueError("Selected waveform and frame metadata differ")
    linkage_diffs = [source_linkage_mismatch(meta, state)
                     for meta, state in zip(metadata, states)]
    for meta, diffs in zip(metadata, linkage_diffs):
        for ph in "ABC":
            if abs(diffs[ph] - meta["psi_minus_source_Wb"][ph]) > 1e-15:
                raise ValueError("Capture-time linkage mismatch differs from independent recomputation")
    span = theta[48]-theta[0]
    path = terminal_work_path(i[:, :49], psi[:, :49], theta[:49], metadata[0]["n_parallel"])
    path_24 = terminal_work_path(i[:, :49:2], psi[:, :49:2], theta[:49:2], metadata[0]["n_parallel"])
    delta = energies[2]-energies[0]
    fundamental_trapezoid_factor = float(np.sin(2*np.pi/48)/(2*np.pi/48))
    return {
        "status": "uncertified_same_run_diagnostic",
        "selected_frames": list(FRAMES), "n_samples": int(i.shape[1]),
        "energy_potential_J": energies,
        "potential_period_delta_0_to_48_J": delta,
        "potential_period_delta_1_to_49_J": energies[3]-energies[1],
        "arithmetic_potential_delta_over_angle_Nm": delta/span,
        "terminal_path_work_0_to_48_Nm": path,
        "terminal_path_work_every_other_step_0_to_48_Nm": path_24,
        "relative_24_to_48_step_change": abs(path_24-path)/max(abs(path), abs(path_24), 1e-12),
        "pure_fundamental_48_step_trapezoid_factor": fundamental_trapezoid_factor,
        "hybrid_mean_times_fundamental_trapezoid_factor_Nm": float(np.mean(hybrid[:48]))*fundamental_trapezoid_factor,
        "path_minus_arithmetic_potential_delta_Nm": path-delta/span,
        "raw_maxwell_first_period_mean_Nm": float(np.mean(maxwell[:48])),
        "hybrid_first_period_mean_Nm": float(np.mean(hybrid[:48])),
        "source_linkage_max_abs_Wb": max(abs(v) for row in linkage_diffs for v in row.values()),
        "pm_field_load_pairing_max_abs_J": max(pm_errors),
        "production_H_max_abs_difference_Apm": max_H_error,
        "sampled_nonlinear_quadrature_points": sampled_b,
        "permeability_floor_violations": floor_violations,
        "integration_path_knot_floor_violations": floor_path_knot_violations,
        "source_hashes_unchanged_during_run": capture["source_hashes_unchanged"],
        "reason_uncertified": "Endpoint potential and source reciprocity do not establish a complete periodic field/material certificate or an independent mean-torque error bound.",
    }


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
