"""Read-only review of 48 saved frozen-current ±one-cell P2 pairs."""
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
from torque_path_work_run15_review import analyze as run15_path_review  # noqa: E402

RUN16 = ROOT / "scratchpad" / "torque_frozenmean_20260923_run16"
RUN15 = ROOT / "scratchpad" / "torque_corrected_state_20260923_run15"


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


def analyze(run16=RUN16, run15=RUN15):
    run16, run15 = Path(run16), Path(run15)
    provenance = json.loads((run16 / "provenance.json").read_text())
    if digest(ROOT / "src/motor_ai_sim/simulation/field_ops.py") != provenance["run15_source_hashes"]["field_ops.py"]:
        raise ValueError("Production B-H law differs from captured source")
    capture = json.loads((run16 / "capture_review.json").read_text())
    if capture["frames_persisted"] != 96 or not capture["all_selected_newton_converged"]:
        raise ValueError("Frozen equilibrium capture is incomplete/unconverged")
    if not capture["source_hashes_unchanged"]:
        raise ValueError("Source changed during frozen study")
    baseline = json.loads((run15 / "capture_review.json").read_text())
    wavepath = run15 / "raw_waveforms.npz"
    if digest(wavepath) != baseline["raw_waveforms_sha256"] or digest(wavepath) != provenance["run15_waveform_sha256"]:
        raise ValueError("Run15 waveform provenance differs")
    with np.load(wavepath, allow_pickle=False) as waves:
        centers_i = np.asarray(waves["currents_a"][:, :48], float)
        maxwell_mean = float(np.mean(waves["torque_maxwell_nm"][:48]))
        hybrid_mean = float(np.mean(waves["torque_hybrid_nm"][:48]))
    if hashlib.sha256(np.ascontiguousarray(centers_i).tobytes()).hexdigest() != provenance["center_current_array_sha256"]:
        raise ValueError("Frozen center-current array differs")
    with np.load(run16 / "static_source.npz", allow_pickle=False) as saved:
        static = {key: saved[key] for key in saved.files}
    with np.load(run15 / "frame_00.npz", allow_pickle=False) as saved:
        for key in ("mesh_p_m", "mesh_t", "element_dofs", "quadrature_dx_m2",
                    "nu_base2", "Hc_x_effective_Apm", "Hc_y_effective_Apm",
                    "magnet_source_vector", "coil_source_vectors_per_A__A",
                    "coil_source_vectors_per_A__B", "coil_source_vectors_per_A__C"):
            if not np.array_equal(static[key], saved[key]):
                raise ValueError(f"Run16 assembly differs from run15: {key}")
    if (run16 / "assembled_curves.json").read_bytes() != (run15 / "assembled_curves.json").read_bytes():
        raise ValueError("Run16 B-H assignments differ from run15")
    curves = json.loads((run16 / "assembled_curves.json").read_text())
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
        meta = json.loads((run16 / f"frame_{k:02d}.json").read_text())
        with np.load(run16 / f"frame_{k:02d}.npz", allow_pickle=False) as saved:
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
            raise ValueError("Frozen current differs from run15 center")
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
    path = run15_path_review(run15)["nested_grids"][0]["cubic_gauss_Nm"]
    fundamental_factor = float(np.sin(7*spacing)/(7*spacing))
    return {
        "status": "uncertified_discrete_frozen_current_mean",
        "pairs": 48, "slip_cell_spacing_rad": spacing,
        "secant_mean_Nm": float(np.mean(torque)),
        "secant_min_Nm": float(np.min(torque)),
        "secant_max_Nm": float(np.max(torque)),
        "secant_peak_to_peak_Nm": float(np.ptp(torque)),
        "run15_cubic_path_mean_Nm": path,
        "run15_hybrid_mean_Nm": hybrid_mean,
        "run15_raw_maxwell_mean_Nm": maxwell_mean,
        "secant_minus_cubic_path_Nm": float(np.mean(torque))-path,
        "source_linkage_max_abs_Wb": source_mismatch,
        "pm_field_load_pairing_max_abs_J": pm_error,
        "production_H_max_abs_difference_Apm": max_H_error,
        "permeability_floor_points": floor_points,
        "permeability_floor_path_knots": floor_knots,
        "pure_fundamental_central_secant_factor": fundamental_factor,
        "note": "Each pair holds run15 center branch current fixed; source coenergy uses stack*sectors*Ibranch*A·fcoil without a second parallel division. Integer-weld ±one-cell secants have unknown spatial error; the fundamental sinc factor is only a hypothesis, not a motor correction.",
    }


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
