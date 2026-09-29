"""Read-only archived torque/energy closure diagnostic; never certifies a method."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RUN04 = ROOT / "scratchpad" / "torque_full_state_20260923_run04"
RUN06 = ROOT / "scratchpad" / "torque_material_20260923_run06"
RUN09 = ROOT / "scratchpad" / "torque_frozen_current_20260923_run09"


def terminal_work_path(current_a, flux_wb, angle_rad, parallel_branches=1):
    """Trapezoidal sum(i dpsi)/signed angle with a *saved* final endpoint."""
    i = np.asarray(current_a, dtype=float)
    psi = np.asarray(flux_wb, dtype=float)
    angle = np.asarray(angle_rad, dtype=float)
    if i.ndim != 2 or i.shape != psi.shape or angle.shape != (i.shape[1],):
        raise ValueError("phase/sample arrays and angle must match")
    if i.shape[1] < 2 or not all(np.all(np.isfinite(x)) for x in (i, psi, angle)):
        raise ValueError("finite path with an endpoint required")
    step = np.diff(angle)
    if not (np.all(step > 0) or np.all(step < 0)):
        raise ValueError("angle path must be strictly monotone")
    if int(parallel_branches) != parallel_branches or parallel_branches < 1:
        raise ValueError("positive integer branch count required")
    return float(parallel_branches * np.sum(
        .5 * (i[:, :-1] + i[:, 1:]) * np.diff(psi, axis=1))
        / (angle[-1] - angle[0]))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(run04=RUN04, run06=RUN06, run09=RUN09):
    run04, run06, run09 = map(Path, (run04, run06, run09))
    full_state = run04 / "full_p2_state.npz"
    waves = run04 / "raw_waveforms.npz"
    energy = run06 / "energy_review.json"
    virtual = run09 / "frozen_review.json"
    e = json.loads(energy.read_text())
    v = json.loads(virtual.read_text())
    summary = json.loads((run04 / "full_state_summary.json").read_text())
    full_hash = sha256(full_state)
    if (full_hash != e["raw_full_state_sha256"] or full_hash != v["full_state_sha256"]
            or full_hash != summary["full_state_sha256"]):
        raise ValueError("archived full-state provenance differs from energy/virtual work")
    if (sha256(run06 / "assembled_materials.npz") != e["assembled_materials_sha256"]
            or e["assembled_materials_sha256"] != v["materials_sha256"]):
        raise ValueError("material captures differ")
    if (sha256(run06 / "assembled_curves.json") != e["assembled_curves_sha256"]
            or e["assembled_curves_sha256"] != v["curves_sha256"]):
        raise ValueError("constitutive curves differ")
    wave_hash = sha256(waves)
    if wave_hash != summary["new_waveforms_sha256"]:
        raise ValueError("archived waveform provenance differs from run04 summary")
    with np.load(waves, allow_pickle=False) as w:
        i = w["currents_a"]
        psi = w["flux_linkages_wb"]
        angle = np.deg2rad(w["rotor_angle_deg"])
        raw_maxwell = w["torque_maxwell_nm"]
        hybrid = w["torque_hybrid_nm"]
    if i.shape != (3, 96) or psi.shape != i.shape or angle.shape != (96,):
        raise ValueError("run04 waveform shape changed")
    span = float(angle[48] - angle[0])
    if not np.isclose(span, 2*np.pi/7, atol=1e-12, rtol=0):
        raise ValueError("archived electrical period span changed")
    # First period has an independently saved frame 48. The second period
    # lacks frame 96; never synthesize it as a measured endpoint.
    path_mean = terminal_work_path(i[:, :49], psi[:, :49], angle[:49])
    cyclic_mean = terminal_work_path(
        np.column_stack((i[:, :48], i[:, 0])),
        np.column_stack((psi[:, :48], psi[:, 0])),
        np.r_[angle[:48], angle[0] + span])
    delta_u = float(e["period_pair_differences_J"][0])
    return {
        "status": "uncertified_archived_diagnostic",
        "reason": "archived linkage predates reciprocal _psi2 correction; full periodic material-state and independent torque agreement not certified",
        "provenance_sha256": {
            "run04_waveforms": wave_hash,
            "run04_full_P2": full_hash,
            "run06_energy_review": sha256(energy),
            "run09_virtual_work_review": sha256(virtual),
        },
        "period_span_mechanical_rad": span,
        "period_path_terminal_work_Nm": path_mean,
        "period_cyclic_terminal_work_Nm": cyclic_mean,
        "cyclic_minus_saved_endpoint_Nm": cyclic_mean - path_mean,
        "magnetic_potential_delta_J": delta_u,
        "arithmetic_potential_correction_Nm": delta_u/span,
        "period_path_minus_arithmetic_potential_correction_Nm": path_mean - delta_u/span,
        "raw_maxwell_first_period_mean_Nm": float(np.mean(raw_maxwell[:48])),
        "hybrid_first_period_mean_Nm": float(np.mean(hybrid[:48])),
        "frozen_current_secants_Nm": {"one_cell": v["T_1_Nm"], "three_cells": v["T_3_Nm"]},
        "archived_source_linkage_mismatch_max_Wb": v["source_linkage_max_abs_Wb"],
        "samples_first_period_including_endpoint": 49,
        "note": "Numerical comparisons are diagnostics; path work minus potential change is not a certified mechanical torque. The virtual-work secants use frozen frame-0 current and are not a period mean.",
    }


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
