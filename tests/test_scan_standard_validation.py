"""A Sweep point may be applied only after a fresh standard-quality FEM eval.

The subprocess is fake: these tests exercise provenance and publication, not FEM.
"""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import optimization as O


def _result():
    sp = {k: None for k in O._SCAN_VALIDATION_PARAMS}
    sp.update(coil_temp_c=115.0, mesh_size_mm=3.0, min_size_mm=0.3,
              pole_copy=True, torque_filter=False, n_sectors=-1,
              gap_layers=4.0, end_winding=1.2, rotor_eddy=False,
              hi_fidelity=False, structured_gap=True, airgap_macro=False,
              iron_template=True, geo_mesh=True, element_order=2,
              demag=False, cfg_fp="fullfp", sampling_purpose="optimization",
              validation_provenance_version=1)
    return {"run_id": "scan-7", "steps_per_period": 12,
            "machine": {"fingerprint": "machinefp", "swept": ["magnet_fill_up"]},
            "scan_params": sp,
            "operating_points": [
                {"current_a": 20.0, "gamma_deg": 3.0, "rpm": 12000.0},
                {"current_a": 43.8, "gamma_deg": 10.0, "rpm": 13000.0}],
            "points": [
                {"geom_id": 2, "op_index": 0, "feasible": True,
                 "overrides": {"magnet_fill_up": 0.3, "gamma_deg": 12.0},
                 "current_a": 20.0, "gamma_deg": 12.0, "rpm": 12000.0,
                 "source_cfg_fp": "pointfp", "T_em_Nm": 0.1},
                {"geom_id": 2, "op_index": 1, "feasible": True,
                 "overrides": {"magnet_fill_up": 0.3},
                 "current_a": 43.8, "gamma_deg": 10.0, "rpm": 13000.0,
                 "source_cfg_fp": "pointfp", "T_em_Nm": 0.2}],
            "variables": [{"name": "magnet_fill_up"}]}


def _standard_result(*, converged=True, sufficient=True):
    return {"ok": True, "res": {
        "T_em_Nm": 0.25, "T_ripple_pct": 2.5, "efficiency": 0.85,
        "mass_total_kg": 0.15, "torque_per_mass_Nm_kg": 1.67,
        "P_loss_total_W": 12.0, "P_mech_W": 150.0,
        "P_cu_W": 8.0, "P_fe_W": 4.0, "V_peak": 12.0,
        "V_line_peak_V": 20.0, "KV_rpm_per_V_line": 650.0,
        "power_per_mass_W_kg": 1000.0,
        "nonlinear_converged": converged,
        "cogging_sampling_purpose": "standard",
        "cogging_sampling_final_quality_sufficient": sufficient}}


@pytest.fixture
def scan(monkeypatch):
    result = _result()
    state = {"running": False, "result": result}
    fp = {"full": "fullfp", "point": "pointfp", "machine": "machinefp"}
    calls = []
    monkeypatch.setattr(O, "_scan_state", state)
    monkeypatch.setattr(O, "_config_fingerprint",
                        lambda exclude=(): fp["point"] if exclude else fp["full"])
    monkeypatch.setattr(O, "_machine_stamp",
                        lambda exclude=(): {"fingerprint": fp["machine"]})
    monkeypatch.setattr(O, "_subprocess_eval",
                        lambda **kw: calls.append(kw) or _standard_result())
    return result, state, fp, calls


def _req(op=1):
    return O.ScanValidateRequest(run_id="scan-7", geom_id=2, op_index=op)


def test_selected_operating_point_and_every_pinned_solver_knob(scan):
    result, _, _, calls = scan
    got = O.scan_validate_point(_req())
    assert len(calls) == 1
    args = calls[0]
    assert args["overrides"] == {"magnet_fill_up": 0.3}
    assert (args["current_a"], args["gamma_deg"], args["rpm"]) == (43.8, 10.0, 13000.0)
    assert args["steps"] == 12 and args["n_periods"] == 1.0
    assert args["sampling_purpose"] == "cogging_quality"
    assert args["mesh_size_mm"] == result["scan_params"]["mesh_size_mm"]
    assert args["gap_layers"] == result["scan_params"]["gap_layers"]
    assert args["end_winding_factor"] == result["scan_params"]["end_winding"]
    assert args["structured_gap"] is True
    assert got["point"]["apply_eligible"] is True
    assert got["point"]["T_em_Nm"] == 0.25  # fresh FEM, not coarse stored 0.2

    swept_gamma = O.scan_validate_point(_req(0))
    assert calls[1]["gamma_deg"] == 12.0  # swept gamma overrides op gamma
    assert calls[1]["current_a"] == 20.0
    assert calls[1]["overrides"] == {"magnet_fill_up": 0.3}
    assert swept_gamma["point"]["gamma_deg"] == 12.0
    assert swept_gamma["point"]["overrides"] == {"magnet_fill_up": 0.3}


def test_forged_client_metrics_and_geometry_are_ignored(scan):
    _, _, _, calls = scan
    req = O.ScanValidateRequest(run_id="scan-7", geom_id=2, op_index=1,
                                overrides={"magnet_fill_up": 0.99},
                                current_a=999.0, T_em_Nm=999.0)
    got = O.scan_validate_point(req)
    assert calls[0]["overrides"] == {"magnet_fill_up": 0.3}
    assert calls[0]["current_a"] == 43.8
    assert got["point"]["T_em_Nm"] == 0.25


@pytest.mark.parametrize("mutate", [
    lambda r: r["scan_params"].pop("demag"),
    lambda r: r["points"][1].pop("source_cfg_fp"),   # pinned sweep, point unstamped
    lambda r: r["points"].append(deepcopy(r["points"][1])),
])
def test_incomplete_or_ambiguous_point_fails_before_solve(scan, mutate):
    result, _, _, calls = scan
    mutate(result)
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 422
    assert not calls


def _legacy(result):
    """A stored Sweep from before 2026-09-24 (the owner's 22 Sep sweep): no
    provenance version, no sampling purpose, no per-point source stamp."""
    for k in ("validation_provenance_version", "sampling_purpose"):
        result["scan_params"].pop(k)
    for p in result["points"]:
        p.pop("source_cfg_fp")
    return result


def test_legacy_sweep_is_rechecked_on_demand_not_refused(scan):
    result, _, fp, calls = scan
    _legacy(result)
    got = O.scan_validate_point(_req())
    assert len(calls) == 1 and calls[0]["sampling_purpose"] == "cogging_quality"
    assert got["provenance"] == "legacy_machine_stamp"
    assert got["point"]["apply_eligible"] is True
    # …and the machine stamp (swept keys excluded) is still the identity test.
    fp["machine"] = "another-motor"
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409
    assert len(calls) == 1


def test_second_point_of_the_same_sweep_after_applying_the_first(scan):
    """Applying point 1 writes its swept values into the config: the FULL
    fingerprint changes, the swept-excluded ones do not.  Point 2 must still
    verify (it used to 409 on the full fingerprint)."""
    result, _, fp, calls = scan
    assert O.scan_validate_point(_req(1))["provenance"] == "pinned"
    fp["full"] = "after-apply-of-point-1"          # magnet_fill_up now in config
    got = O.scan_validate_point(_req(0))
    assert got["point"]["apply_eligible"] is True
    assert len(calls) == 2


def test_stale_run_or_config_and_running_scan_fail_before_solve(scan):
    result, state, fp, calls = scan
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(O.ScanValidateRequest(run_id="old", geom_id=2, op_index=1))
    assert ei.value.status_code == 409
    fp["point"] = "edited"        # a non-swept key moved
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409
    fp["point"] = "pointfp"
    fp["machine"] = "edited"
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409
    fp["machine"] = "machinefp"
    state["running"] = True
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409
    assert not calls


@pytest.mark.parametrize("converged,sufficient", [(False, True), (True, False)])
def test_failed_standard_quality_never_returns_apply_eligibility(scan, monkeypatch,
                                                                  converged, sufficient):
    monkeypatch.setattr(O, "_subprocess_eval",
                        lambda **kw: _standard_result(converged=converged,
                                                      sufficient=sufficient))
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 422


def test_config_or_run_changed_during_solve_is_rejected(scan, monkeypatch):
    _, state, fp, _ = scan
    def edit_config(**kw):
        fp["machine"] = "edited"
        return _standard_result()
    monkeypatch.setattr(O, "_subprocess_eval", edit_config)
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409

    fp["machine"] = "machinefp"
    def replace_scan(**kw):
        state["result"] = deepcopy(state["result"])
        return _standard_result()
    monkeypatch.setattr(O, "_subprocess_eval", replace_scan)
    with pytest.raises(HTTPException) as ei:
        O.scan_validate_point(_req())
    assert ei.value.status_code == 409


def test_duplicate_validation_refused_and_inflight_state_cleared(scan, monkeypatch):
    _, state, _, _ = scan
    def nested(**kw):
        with pytest.raises(HTTPException) as ei:
            O.scan_validate_point(_req())
        assert ei.value.status_code == 409
        return _standard_result()
    monkeypatch.setattr(O, "_subprocess_eval", nested)
    assert O.scan_validate_point(_req())["point"]["apply_eligible"] is True
    assert state.get("validation_running") is None

    def failed(**kw):
        raise RuntimeError("fake worker failed")
    monkeypatch.setattr(O, "_subprocess_eval", failed)
    with pytest.raises(RuntimeError, match="fake worker failed"):
        O.scan_validate_point(_req())
    assert state.get("validation_running") is None
