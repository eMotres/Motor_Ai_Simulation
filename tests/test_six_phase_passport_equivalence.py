from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
from motor_ai_sim.winding_sets import resolve_six_phase
from tests.test_physics_regression import GEO_30MM, OVERRIDE, RPM

RUN = dict(
    n_steps_per_period=4,
    n_periods=1.0,
    mesh_size_mm=1.4,
    min_size_mm=0.35,
    gap_layers=1.0,
    n_sectors=-1,  # full ring so both inverter sets are present in the model
    structured_gap=True,
    iron_template=True,
    geo_mesh=False,
    coil_temp_c=120.0,
    element_order=2,
    demag=False,
    eddy=False,
    rotor_eddy=False,
    I_phase_rms=60.0,
    gamma_deg=10.0,
    daxis_deg=60.0,  # pinned public-fixture calibration reference
    connection="2P",
)


def _metrics(result: dict) -> dict:
    return {
        "torque_mean_Nm": float(result["T_avg_Nm"]),
        "flux_linkage_A_Wb": {
            phase: np.asarray(result[f"psi_{phase}_Wb"], dtype=float).tolist()
            for phase in "ABC"
        },
        "copper_dc_W": float(result["P_cu_dc_W"]),
        "iron_mean_W": float(np.mean(result["P_fe_W"])),
        "total_loss_mean_W": float(result["P_loss_total_avg_W"]),
    }


def _relative_difference(a: dict, b: dict) -> dict:
    flux_diffs = []
    for phase in "ABC":
        av = np.asarray(a["flux_linkage_A_Wb"][phase])
        bv = np.asarray(b["flux_linkage_A_Wb"][phase])
        flux_diffs.append(float(np.max(np.abs(av - bv)) /
                                 max(float(np.max(np.abs(av))), 1e-30)))
    return {
        "torque_mean_Nm": abs(a["torque_mean_Nm"] - b["torque_mean_Nm"])
        / max(abs(a["torque_mean_Nm"]), 1e-30),
        "flux_linkage_max": max(flux_diffs),
        "copper_dc_W": abs(a["copper_dc_W"] - b["copper_dc_W"])
        / max(abs(a["copper_dc_W"]), 1e-30),
        "iron_mean_W": abs(a["iron_mean_W"] - b["iron_mean_W"])
        / max(abs(a["iron_mean_W"]), 1e-30),
        "total_loss_mean_W": abs(a["total_loss_mean_W"] - b["total_loss_mean_W"])
        / max(abs(a["total_loss_mean_W"]), 1e-30),
    }


def test_six_phase_in_phase_matches_three_phase_parallel_twin():
    six = resolve_six_phase(
        {"phases": 6, "n_parallel": 2},
        num_slots=12,
        num_poles=14,
        single_layer=True,
    )
    assert six is not None

    set_request_materials(OVERRIDE)
    try:
        three = fem_transient_sliding_band(
            geo_override=dict(GEO_30MM), rpm=RPM, **RUN
        )
        dual = fem_transient_sliding_band(
            geo_override=dict(GEO_30MM), rpm=RPM, six_phase=six, **RUN
        )
    finally:
        set_request_materials(None)

    three_metrics = _metrics(three)
    dual_metrics = _metrics(dual)
    record = {
        "status": "SIMULATION_RESULT_CAPTURED",
        "project": "motor_ai_sim",
        "revision": os.environ.get("SIXPASS_SOURCE_REVISION", "unknown"),
        "solver_sha256": os.environ.get("SIXPASS_SOLVER_SHA256", "unknown"),
        "inputs": {
            "geometry_fixture": "tests.test_physics_regression.GEO_30MM (12s14p, 30 mm public regression fixture)",
            "rpm": RPM,
            "winding_connection": "2P",
            "phase_current_rms_A": RUN["I_phase_rms"],
            "six_phase_current_per_set_rms_A": RUN["I_phase_rms"] / 2,
            "gamma_deg": RUN["gamma_deg"],
            "daxis_deg": RUN["daxis_deg"],
            "mesh_size_requested_mm": RUN["mesh_size_mm"],
            "min_size_requested_mm": RUN["min_size_mm"],
            "element_order": RUN["element_order"],
            "steps_per_period_requested": RUN["n_steps_per_period"],
            "periods": RUN["n_periods"],
            "n_sectors": RUN["n_sectors"],
            "drive": "current",
            "eddy": RUN["eddy"],
            "materials": "tests.test_physics_regression.OVERRIDE",
            "material_assignment": {
                "magnet": "F45SH_120C",
                "stator_core": "B15AHV950M",
                "rotor_core": "B15AHV950M",
                "shaft": "Aluminium_6061",
            },
            "material_library_sha256": hashlib.sha256(
                (Path(__file__).resolve().parents[1] / "config" /
                 "materials_library.yaml").read_bytes()
            ).hexdigest(),
        },
        "convergence": {
            "three_phase_picard_converged": three.get("picard_converged"),
            "six_phase_picard_converged": dual.get("picard_converged"),
            "three_phase_picard_resid_max": three.get("picard_resid_max"),
            "six_phase_picard_resid_max": dual.get("picard_resid_max"),
            "three_phase_n_frames_solved": three.get("n_frames_solved"),
            "six_phase_n_frames_solved": dual.get("n_frames_solved"),
            "actual_steps_per_period": dual.get("n_steps_per_period"),
            "mesh_build": {
                "three_phase": {
                    "builder": (three.get("mesher") or {}).get("build"),
                    "mesher_diagnostic": three.get("mesher"),
                    "events": three.get("mesh_build_events"),
                    "structured_gap_effective": three.get("structured_gap_effective"),
                    "slip_nodes_per_period": three.get("slip_nodes_per_period"),
                    "gap_layers_effective": three.get("gap_layers_effective"),
                },
                "six_phase": {
                    "builder": (dual.get("mesher") or {}).get("build"),
                    "mesher_diagnostic": dual.get("mesher"),
                    "events": dual.get("mesh_build_events"),
                    "structured_gap_effective": dual.get("structured_gap_effective"),
                    "slip_nodes_per_period": dual.get("slip_nodes_per_period"),
                    "gap_layers_effective": dual.get("gap_layers_effective"),
                },
            },
            "six_phase_source_sum_residual": dual["six_phase"]["source_sum_residual"],
            "sets_in_model": dual["six_phase"]["sets_in_model"],
        },
        "solver_output": {
            "torque_method": three.get("torque_method"),
            "torque_mean_method": three.get("T_mean_method"),
            "six_phase_torque_method": dual.get("torque_method"),
            "six_phase_torque_mean_method": dual.get("T_mean_method"),
            "mesh_build_events": {
                "three_phase": three.get("mesh_build_events"),
                "six_phase": dual.get("mesh_build_events"),
            },
        },
        "three_phase": three_metrics,
        "six_phase": dual_metrics,
        "relative_difference": _relative_difference(three_metrics, dual_metrics),
    }
    out_path = os.environ.get("SIXPASS_EQUIVALENCE_JSON")
    if out_path:
        Path(out_path).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    # A failed nonlinear frame invalidates this comparison even when the
    # aggregate values happen to be close.
    assert three["picard_converged"] is True, three["picard_resid_max"]
    assert dual["picard_converged"] is True, dual["picard_resid_max"]
    assert dual["six_phase"]["sets_in_model"] == [1, 2]
    assert dual["six_phase"]["source_sum_residual"] < 1e-12
    assert dual["six_phase"]["xy_probe_valid"] is True

    # Identical source and mesh should differ only by floating-point summation.
    assert dual["T_avg_Nm"] == pytest.approx(three["T_avg_Nm"], rel=1e-8)
    for phase in "ABC":
        assert np.allclose(dual[f"psi_{phase}_Wb"], three[f"psi_{phase}_Wb"],
                           rtol=1e-8, atol=1e-15)
    assert dual["P_cu_dc_W"] == pytest.approx(three["P_cu_dc_W"], rel=1e-8)
    assert np.allclose(dual["P_fe_W"], three["P_fe_W"], rtol=1e-8, atol=1e-12)
    # This summary rounds to 1 mW; use half a reporting quantum plus margin.
    assert dual["P_loss_total_avg_W"] == pytest.approx(
        three["P_loss_total_avg_W"], rel=1e-8, abs=0.0015
    )
    if out_path:
        record["status"] = "SIMULATION_VALID"
        Path(out_path).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
