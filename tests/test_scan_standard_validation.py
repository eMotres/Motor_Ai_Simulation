"""A picked Sweep point applies DIRECTLY, with its own (screening-resolution)
result — no server re-solve.

2026-09-25 (owner): "Давай не будем в Sweep пересчитывать 6× — только при
повторном расчёте уже в Electromagnetic." Apply must not re-solve a picked
Sweep point at standard/cogging_quality resolution before writing it into the
machine; the standard-resolution answer comes only from the owner's next
Electromagnetic run of the applied machine.

The one thing kept from the old verify-before-apply gate
(O._scan_validation_inputs / O.scan_validate_point — still present, still
used by nothing in Sweep any more; see tests/test_sampling_purpose_flow.py for
where a fresh cogging_quality re-solve is still legitimate) is the
machine-mismatch protection: a stored Sweep point can never be applied onto a
different machine than the one the Sweep solved it on. That check lives in
O._scan_point_for_apply / O.scan_apply_point and is exercised here.
"""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import optimization as O


def _result():
    sp = {"validation_provenance_version": 1, "sampling_purpose": "optimization"}
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


@pytest.fixture
def scan(monkeypatch):
    result = _result()
    state = {"running": False, "result": result}
    fp = {"full": "fullfp", "point": "pointfp", "machine": "machinefp"}
    monkeypatch.setattr(O, "_scan_state", state)
    monkeypatch.setattr(O, "_config_fingerprint",
                        lambda exclude=(): fp["point"] if exclude else fp["full"])
    monkeypatch.setattr(O, "_machine_stamp",
                        lambda exclude=(): {"fingerprint": fp["machine"]})
    return result, state, fp


def _req(op=1):
    return O.ScanValidateRequest(run_id="scan-7", geom_id=2, op_index=op)


def test_apply_returns_the_points_own_result_with_no_solve(scan, monkeypatch):
    result, _, _ = scan

    def _must_not_solve(**kw):
        raise AssertionError("scan_apply_point must never call _subprocess_eval")
    monkeypatch.setattr(O, "_subprocess_eval", _must_not_solve)

    got = O.scan_apply_point(_req(1))
    assert got["point"]["overrides"] == {"magnet_fill_up": 0.3}
    assert got["point"]["T_em_Nm"] == 0.2      # the sweep's OWN coarse number, not re-solved
    assert got["point"]["current_a"] == 43.8
    assert got["point"]["gamma_deg"] == 10.0
    assert got["provenance"] == "pinned"

    swept_gamma = O.scan_apply_point(_req(0))
    assert swept_gamma["point"]["gamma_deg"] == 12.0   # swept gamma, not the op's
    assert swept_gamma["point"]["T_em_Nm"] == 0.1


def test_forged_client_fields_are_ignored(scan):
    """The request model carries only run_id/geom_id/op_index — any extra
    client-supplied geometry or metrics are dropped, never trusted."""
    req = O.ScanValidateRequest(run_id="scan-7", geom_id=2, op_index=1,
                                overrides={"magnet_fill_up": 0.99},
                                current_a=999.0, T_em_Nm=999.0)
    got = O.scan_apply_point(req)
    assert got["point"]["overrides"] == {"magnet_fill_up": 0.3}
    assert got["point"]["current_a"] == 43.8
    assert got["point"]["T_em_Nm"] == 0.2


@pytest.mark.parametrize("mutate", [
    lambda r: r["points"][1].pop("source_cfg_fp"),   # pinned sweep, point unstamped
    lambda r: r["points"].append(deepcopy(r["points"][1])),   # now ambiguous
    lambda r: r["points"][1].pop("overrides"),
])
def test_incomplete_or_ambiguous_point_is_refused(scan, mutate):
    result, _, _ = scan
    mutate(result)
    with pytest.raises(HTTPException) as ei:
        O.scan_apply_point(_req())
    assert ei.value.status_code == 422


def _legacy(result):
    """A stored Sweep from before 2026-09-24 (the owner's 22 Sep sweep): no
    provenance version, no per-point source stamp."""
    result["scan_params"].pop("validation_provenance_version")
    for p in result["points"]:
        p.pop("source_cfg_fp")
    return result


def test_legacy_sweep_applies_under_the_machine_stamp(scan):
    result, _, fp = scan
    _legacy(result)
    got = O.scan_apply_point(_req())
    assert got["provenance"] == "legacy_machine_stamp"
    assert got["point"]["overrides"] == {"magnet_fill_up": 0.3}
    # …and the machine stamp (swept keys excluded) is still the identity test.
    fp["machine"] = "another-motor"
    with pytest.raises(HTTPException) as ei:
        O.scan_apply_point(_req())
    assert ei.value.status_code == 409


def test_second_point_of_the_same_sweep_after_applying_the_first(scan):
    """Applying point 1 writes its swept values into the config: the FULL
    fingerprint changes, the swept-excluded one does not.  Point 2 must still
    apply (it used to 409 on the full fingerprint before 0f973bb)."""
    result, _, fp = scan
    assert O.scan_apply_point(_req(1))["provenance"] == "pinned"
    fp["full"] = "after-apply-of-point-1"          # magnet_fill_up now in config
    got = O.scan_apply_point(_req(0))
    assert got["point"]["overrides"] == {"magnet_fill_up": 0.3}


def test_stale_run_or_moved_machine_is_refused(scan):
    result, state, fp = scan
    with pytest.raises(HTTPException) as ei:
        O.scan_apply_point(O.ScanValidateRequest(run_id="old", geom_id=2, op_index=1))
    assert ei.value.status_code == 409
    fp["point"] = "edited"        # a non-swept key moved — the pinned source stamp catches it
    with pytest.raises(HTTPException) as ei:
        O.scan_apply_point(_req())
    assert ei.value.status_code == 409
    fp["point"] = "pointfp"
    fp["machine"] = "edited"
    with pytest.raises(HTTPException) as ei:
        O.scan_apply_point(_req())
    assert ei.value.status_code == 409
    fp["machine"] = "machinefp"
    assert O.scan_apply_point(_req())["point"]["overrides"] == {"magnet_fill_up": 0.3}


def test_apply_works_even_while_a_sweep_is_still_running(scan):
    """No re-solve happens, so Apply does not need the old solve-based gate's
    'wait for the Sweep to finish' / 'already being validated' locks."""
    result, state, _ = scan
    state["running"] = True
    got = O.scan_apply_point(_req(1))
    assert got["point"]["overrides"] == {"magnet_fill_up": 0.3}
