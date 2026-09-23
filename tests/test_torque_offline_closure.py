"""Small, no-FEM checks of the archived torque closure arithmetic."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "torque_offline_closure.py"
spec = importlib.util.spec_from_file_location("torque_offline_closure", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_saved_endpoint_work_respects_signed_angle_and_parallel_scale():
    theta = np.linspace(0, 1, 101)
    psi = (theta**2)[None, :]
    current = np.full_like(psi, 2.)
    assert module.terminal_work_path(current, psi, theta) == pytest.approx(2.)
    assert module.terminal_work_path(current/3, psi, theta, 3) == pytest.approx(2.)
    assert module.terminal_work_path(current[:, ::-1], psi[:, ::-1], theta[::-1]) == pytest.approx(2.)
    bad_angle = theta.copy()
    bad_angle[50] = bad_angle[49]
    with pytest.raises(ValueError, match="monotone"):
        module.terminal_work_path(current, psi, bad_angle)


def test_endpoint_trapezoid_has_known_fundamental_sampling_factor():
    n = 48
    theta = 2*np.pi*np.arange(n+1)/n
    psi = np.cos(theta)[None, :]
    current = -np.sin(theta)[None, :]
    sampled = module.terminal_work_path(current, psi, theta)
    assert sampled == pytest.approx(.5*np.sin(2*np.pi/n)/(2*np.pi/n), abs=1e-14)


def test_offline_comparison_keeps_certificate_unresolved(tmp_path):
    import json
    run04, run06, run09 = (tmp_path / name for name in ("run04", "run06", "run09"))
    for directory in (run04, run06, run09):
        directory.mkdir()
    (run04 / "full_p2_state.npz").write_bytes(b"synthetic full state")
    (run06 / "assembled_materials.npz").write_bytes(b"synthetic material")
    (run06 / "assembled_curves.json").write_text("[]")
    theta = 2*np.pi*np.arange(96)/48/7
    current = np.cos(7*theta)[None, :]
    flux = np.sin(7*theta)[None, :]
    np.savez(run04 / "raw_waveforms.npz", currents_a=np.repeat(current, 3, axis=0),
             flux_linkages_wb=np.repeat(flux, 3, axis=0), rotor_angle_deg=np.rad2deg(theta),
             torque_maxwell_nm=np.zeros(96), torque_hybrid_nm=np.ones(96))
    full_hash = module.sha256(run04 / "full_p2_state.npz")
    material_hash = module.sha256(run06 / "assembled_materials.npz")
    curve_hash = module.sha256(run06 / "assembled_curves.json")
    (run04 / "full_state_summary.json").write_text(json.dumps({
        "full_state_sha256": full_hash,
        "new_waveforms_sha256": module.sha256(run04 / "raw_waveforms.npz")}))
    (run06 / "energy_review.json").write_text(json.dumps({
        "raw_full_state_sha256": full_hash,
        "assembled_materials_sha256": material_hash,
        "assembled_curves_sha256": curve_hash,
        "period_pair_differences_J": [0.001]}))
    (run09 / "frozen_review.json").write_text(json.dumps({
        "full_state_sha256": full_hash, "materials_sha256": material_hash,
        "curves_sha256": curve_hash, "T_1_Nm": 1., "T_3_Nm": 2.,
        "source_linkage_max_abs_Wb": 0.}))
    result = module.analyze(run04, run06, run09)
    assert result["status"] == "uncertified_archived_diagnostic"
    assert result["samples_first_period_including_endpoint"] == 49
    assert result["period_span_mechanical_rad"] == pytest.approx(2*np.pi/7)
    assert result["arithmetic_potential_correction_Nm"] == pytest.approx(
        result["magnetic_potential_delta_J"] / result["period_span_mechanical_rad"])
    assert result["period_path_minus_arithmetic_potential_correction_Nm"] == pytest.approx(
        result["period_path_terminal_work_Nm"] - result["arithmetic_potential_correction_Nm"])
    (run06 / "assembled_curves.json").write_text("tampered")
    with pytest.raises(ValueError, match="constitutive curves"):
        module.analyze(run04, run06, run09)
