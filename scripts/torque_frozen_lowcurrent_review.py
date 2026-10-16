"""Read-only review of run18's 1 A/branch frozen-current P2 pairs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from motor_ai_sim.simulation.field_ops import MU0, _mu_r_from_bh_vec  # noqa: E402
from torque_corrected_state_review import curve_h_energy  # noqa: E402
from torque_low_current_review import spectral_periodic_work  # noqa: E402
RUN18 = ROOT / "scratchpad" / "torque_frozen_lowcurrent_20260923_run18"
RUN15 = ROOT / "scratchpad" / "torque_corrected_state_20260923_run15"
LOW = ROOT / "scratchpad" / "torque_low_current_20260923_run17" / "case_02"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paired_secants(coenergy_j, angle_rad):
    w = np.asarray(coenergy_j, float)
    a = np.asarray(angle_rad, float)
    if w.ndim != 1 or a.shape != w.shape or w.size < 2 or w.size % 2:
        raise ValueError("One minus/plus state is required per pair")
    delta = a[1::2]-a[0::2]
    if not np.all(np.isfinite(w)) or not np.all(np.isfinite(a)) or not np.all(delta > 0):
        raise ValueError("Finite ordered pair states are required")
    return (w[1::2]-w[0::2])/delta


def source_coenergy(A, currents, fcoil, potential_J, sector_stack_m):
    """Branch-current winding source work minus magnetic potential, joules."""
    return (sector_stack_m * sum(float(currents[ph])*float(np.dot(A, fcoil[ph]))
                                 for ph in "ABC") - float(potential_J))


def analyze(run18=RUN18, run15=RUN15, low=LOW):
    run18, run15, low = Path(run18), Path(run15), Path(low)
    provenance = json.loads((run18 / "provenance.json").read_text())
    prior_provenance = json.loads((run15 / "provenance.json").read_text())
    for key in ("p2_state_capture.py", "p2_nonlinear.py"):
        if provenance["current_source_lf_hashes"][key] != prior_provenance["source_hashes"][key]:
            raise ValueError(f"{key} differs from run15 beyond checkout EOL")
    for key in ("materials_library.yaml", "pinned_run04_config"):
        if provenance["current_source_raw_hashes"][key] != prior_provenance["source_hashes"][key]:
            raise ValueError(f"{key} differs from run15")
    for key, value in provenance["approved_current_source_hashes"].items():
        if provenance["current_source_raw_hashes"][key] != value:
            raise ValueError(f"{key} differs from approved run18 source")
    field_bytes = (ROOT / "src/motor_ai_sim/simulation/field_ops.py").read_bytes()
    if hashlib.sha256(field_bytes.replace(b"\r\n", b"\n")).hexdigest() != provenance["current_source_lf_hashes"]["field_ops.py"]:
        raise ValueError("Production B-H law differs from captured LF-normalized source")
    if provenance["field_ops_git_blob"] != "bc22a3777e7ec979ee3ab3dd9679925289132615":
        raise ValueError("Reviewed d785 field_ops Git blob differs")
    capture = json.loads((run18 / "capture_review.json").read_text())
    if capture["frames_persisted"] != 96 or not capture["all_selected_newton_converged"]:
        raise ValueError("Frozen equilibrium capture is incomplete/unconverged")
    if not capture["source_hashes_unchanged"]:
        raise ValueError("Source changed during frozen study")
    low_review = json.loads((low / "case_review.json").read_text())
    low_provenance = json.loads((low / "provenance.json").read_text())
    if (low_provenance["requested_peak_per_branch_A"] != 1.0 or
            low_provenance["source_hashes"]["materials_library.yaml"] !=
            provenance["current_source_raw_hashes"]["materials_library.yaml"]):
        raise ValueError("Archived 1 A case/material provenance differs")
    wavepath = low / "raw_waveforms.npz"
    if (digest(wavepath) != low_review["raw_waveforms_sha256"] or
            digest(wavepath) != provenance["low_current_case02_waveform_sha256"]):
        raise ValueError("One-ampere waveform provenance differs")
    with np.load(wavepath, allow_pickle=False) as waves:
        centers_i = np.asarray(waves["currents_a"], float)
        centers_psi = np.asarray(waves["flux_linkages_wb"], float)
        centers_angle = np.deg2rad(np.asarray(waves["rotor_angle_deg"], float))
        maxwell_mean = float(np.mean(waves["torque_maxwell_nm"][:48]))
        selected_mean = float(np.mean(waves["torque_selected_nm"][:48]))
    if centers_i.shape != (3, 48) or not np.isclose(np.max(np.abs(centers_i)), 1., atol=1e-12):
        raise ValueError("Archived source is not 48 samples at 1 A peak per branch")
    if hashlib.sha256(np.ascontiguousarray(centers_i).tobytes()).hexdigest() != provenance["center_current_array_sha256"]:
        raise ValueError("Frozen center-current array differs")
    with np.load(run18 / "static_source.npz", allow_pickle=False) as saved:
        static = {key: saved[key] for key in saved.files}
    with np.load(run15 / "frame_00.npz", allow_pickle=False) as saved:
        for key in ("mesh_p_m", "mesh_t", "element_dofs", "quadrature_dx_m2",
                    "nu_base2", "Hc_x_effective_Apm", "Hc_y_effective_Apm",
                    "magnet_source_vector", "coil_source_vectors_per_A__A",
                    "coil_source_vectors_per_A__B", "coil_source_vectors_per_A__C"):
            if not np.array_equal(static[key], saved[key]):
                raise ValueError(f"Run18 assembly differs from run15: {key}")
    if (run18 / "assembled_curves.json").read_bytes() != (run15 / "assembled_curves.json").read_bytes():
        raise ValueError("Run18 B-H assignments differ from run15")
    curves = json.loads((run18 / "assembled_curves.json").read_text())
    fcoil = {ph: static[f"coil_source_vectors_per_A__{ph}"] for ph in "ABC"}
    dx, nu = static["quadrature_dx_m2"], static["nu_base2"]
    hx, hy, fmag = (static["Hc_x_effective_Apm"],
                    static["Hc_y_effective_Apm"], static["magnet_source_vector"])
    br_reference = None
    meta_rows, coenergy, potential = [], [], []
    source_mismatch, pm_error, max_H_error = 0., 0., 0.
    floor_points, floor_knots = 0, 0
    spacing = np.deg2rad(360./7./144.)
    for k in range(96):
        meta = json.loads((run18 / f"frame_{k:02d}.json").read_text())
        with np.load(run18 / f"frame_{k:02d}.npz", allow_pickle=False) as saved:
            A, bx, by, br = (saved[key] for key in
                            ("A_z", "Bx_quad_T", "By_quad_T", "magnet_br_state"))
        if br_reference is None:
            br_reference = br.copy()
        elif not np.array_equal(br, br_reference):
            raise ValueError("Magnet Br changed between frozen states")
        expected_shift = 3*(k//2) + (-1 if k % 2 == 0 else 1)
        expected_angle = expected_shift*spacing
        if (meta["frame_index"] != k or meta["slip_shift"] != expected_shift
                or abs(meta["mechanical_angle_rad"]-expected_angle) > 1e-12):
            raise ValueError("Frozen pair index/angle differs")
        if any(meta["current_abc_A"][ph] != centers_i[p, k//2]
               for p, ph in enumerate("ABC")):
            raise ValueError("Frozen current differs from archived 1 A center")
        if (meta["eddy"] or meta["demag"] or meta["rotor_eddy"] or meta["frozen_nu"]
                or meta["voltage_drive"] or not meta["imposed_current_drive"]
                or not meta["newton_converged"] or meta["picard_unconverged"]
                or meta["n_parallel"] != 1 or meta["n_sectors"] != 2
                or meta["pole_pairs"] != 7 or meta["stack_length_m"] != .01):
            raise ValueError("Frozen frame mode/convergence/scale differs")
        if bx.shape != dx.shape or by.shape != dx.shape:
            raise ValueError("P2 quadrature shape differs")
        B = np.hypot(bx, by)
        density = .5*nu[:, None]*B*B
        for row in curves:
            ids = np.asarray(row["element_ids"], int)
            values = B[ids]
            if np.any(values <= 1e-12):
                raise ValueError("Nonlinear B enters production low-field branch")
            H, U = curve_h_energy(values, row["H_B_curve"])
            density[ids] = U
            mu = _mu_r_from_bh_vec(row["H_B_curve"], values)
            max_H_error = max(max_H_error, float(np.max(np.abs(H-values/(MU0*mu)))))
            floor_points += int(np.sum(H > values/MU0+1e-6))
            max_b = float(np.max(values))
            knots = np.asarray([0.] + [float(pair[1]) for pair in row["H_B_curve"]
                                       if 0 < float(pair[1]) <= max_b] + [max_b])
            Hk, _ = curve_h_energy(knots, row["H_B_curve"])
            floor_knots += int(np.sum(Hk > knots/MU0+1e-6))
        if floor_points or floor_knots:
            raise ValueError("Production permeability floor active on integration path")
        factor = meta["stack_length_m"]*meta["n_sectors"]
        pm_density = -(hx[:, None]*bx + hy[:, None]*by)
        U_machine = factor*float(np.sum((density+pm_density)*dx))
        pm_error = max(pm_error, abs(factor*float(np.sum(pm_density*dx))
                                     + factor*float(np.dot(A, fmag))))
        psi_source = {ph: factor/meta["n_parallel"]*float(np.dot(A, fcoil[ph]))
                      for ph in "ABC"}
        source_mismatch = max(source_mismatch, *(abs(meta["psi_abc_Wb"][ph]-psi_source[ph])
                                                  for ph in "ABC"))
        potential.append(U_machine)
        coenergy.append(source_coenergy(A, meta["current_abc_A"], fcoil,
                                        U_machine, factor))
        meta_rows.append(meta)
    angles = np.asarray([row["mechanical_angle_rad"] for row in meta_rows])
    torque = paired_secants(coenergy, angles)
    terminal = spectral_periodic_work(
        centers_i, centers_psi, centers_angle,
        pole_pairs=7, parallel_branches=1)
    if abs(terminal - low_review["torque_method_diagnostics"]["space_vector_mean_candidate_Nm"]) > 1e-12:
        raise ValueError("Archived all-bin terminal work differs from space-vector candidate")
    fundamental_factor = float(np.sin(7*spacing)/(7*spacing))
    return {
        "status": "uncertified_discrete_frozen_current_mean",
        "pairs": 48, "slip_cell_spacing_rad": spacing,
        "secant_mean_Nm": float(np.mean(torque)),
        "secant_min_Nm": float(np.min(torque)),
        "secant_max_Nm": float(np.max(torque)),
        "secant_peak_to_peak_Nm": float(np.ptp(torque)),
        "case02_terminal_spectral_mean_Nm": terminal,
        "case02_raw_maxwell_mean_Nm": maxwell_mean,
        "case02_selected_mean_Nm": selected_mean,
        "secant_minus_terminal_spectral_Nm": float(np.mean(torque))-terminal,
        "secant_minus_raw_maxwell_Nm": float(np.mean(torque))-maxwell_mean,
        "per_pair_secants_Nm": torque.tolist(),
        "source_linkage_max_abs_Wb": source_mismatch,
        "pm_field_load_pairing_max_abs_J": pm_error,
        "production_H_max_abs_difference_Apm": max_H_error,
        "permeability_floor_points": floor_points,
        "permeability_floor_path_knots": floor_knots,
        "pure_fundamental_central_secant_factor": fundamental_factor,
        "note": "Each pair holds the archived 1 A center branch current fixed. Source coenergy uses stack*sectors*Ibranch*A·fcoil without a second parallel division. Run15 field_ops provenance was mixed-EOL; run18 pins the exact d785 Git blob and LF content, not a proven byte-identical run15 file. Integer-weld ±one-cell secants have unknown spatial error; the fundamental sinc factor is only a hypothesis, not a motor correction.",
    }


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
