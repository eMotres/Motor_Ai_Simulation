"""Unit coverage for opt-in, magnetostatic sliding-band gap probes."""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from motor_ai_sim.simulation import fem_solver_2d as fs


def _result(kw, layer, eps, torque=1000.0, ripple=14.0):
    return {
        "gap_layers_effective": layer,
        "gap_layers_requested": kw.get("gap_layers", layer),
        "slip_nodes_per_period": 144,
        "T_avg_Nm": torque,
        "T_ripple_pp_Nm": ripple,
        "solve_wall_s": 0.1,
        "eddy_settled": True if kw.get("eddy") else None,
        "coulomb_torque": {"layer_self_check": {"rel_to_ripple_scale": eps}},
    }


def _enable_probe(monkeypatch):
    monkeypatch.setenv("SB_GAP_PROBE", "1")
    monkeypatch.delenv("SB_GAP_REFINE", raising=False)


def test_probe_convergence_runs_one_full_transient_and_flags_final_mismatch(monkeypatch):
    calls, stages = [], []

    def fake(**kw):
        calls.append(dict(kw))
        if kw.get("eddy") is False:
            assert fs._GAP_PROBE_CTX.get()
            assert kw["rotor_eddy"] is True
            kw["progress_cb"](None, None, "mesh", None)
        layer = float(kw["gap_layers"])
        if kw.get("eddy"):
            return _result(kw, layer, 0.07, torque=1000.1, ripple=14.2)
        if layer == 1.0:
            return _result(kw, layer, 0.07, torque=1000.0, ripple=14.0)
        return _result(kw, layer, 0.07, torque=1000.1, ripple=14.2)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = fs._solve_with_gap_refinement({
        "gap_layers": 1.0, "sampling_purpose": "standard",
        "n_steps_per_period": 40, "n_periods": 1.0,
        "eddy": True, "demag": True, "rotor_eddy": True,
        "progress_cb": lambda done, total, stage, detail: stages.append(stage),
    })
    assert [c["gap_layers"] for c in calls] == [1.0, 2.0, 2.0]
    assert all(c["n_steps_per_period"] == 40 and c["n_periods"] == 1.0 for c in calls)
    assert all(c["rotor_eddy"] is True for c in calls)
    assert all(c["eddy"] is False and c["demag"] is False and not c["inc_ldq"]
               for c in calls[:2])
    assert calls[-1]["eddy"] is True and calls[-1]["demag"] is True
    assert all(c["slip_per_period"] == 144 for c in calls[1:])
    assert all("gap probe" in stage for stage in stages)
    assert not fs._GAP_PROBE_CTX.get()
    gr = out["gap_refinement"]
    assert gr["used_probe"] and gr["probe_converged"]
    assert gr["selection_reason"] == "successive_probe_torque_and_ripple_converged"
    assert gr["passed"] is False and gr["persist_gap_layers"] is None
    assert [a["kind"] for a in gr["attempts"]] == [
        "magnetostatic_probe", "magnetostatic_probe", "full_transient"]
    assert "ripple flagged" in out["gap_layers_note"]


def test_probe_pass_persists_only_after_final_full_ring_check(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(dict(kw))
        layer = float(kw["gap_layers"])
        if kw.get("eddy") is False:
            return _result(kw, layer, 0.2 if layer == 1.0 else 0.01)
        return _result(kw, layer, 0.01)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard"})
    assert [c["gap_layers"] for c in calls] == [1.0, 2.0, 2.0]
    assert out["gap_refinement"]["passed"] is True
    assert out["gap_refinement"]["persist_gap_layers"] == 2.0


def test_probe_thresholds_require_both_mean_and_ripple():
    assert fs._gap_probe_converged(
        {"T_avg_Nm": 1000.0, "T_ripple_pp_Nm": 14.0},
        {"T_avg_Nm": 1000.1, "T_ripple_pp_Nm": 14.2})
    assert not fs._gap_probe_converged(
        {"T_avg_Nm": 1000.0, "T_ripple_pp_Nm": 14.0},
        {"T_avg_Nm": 1000.1, "T_ripple_pp_Nm": 15.0})
    assert not fs._gap_probe_converged(
        {"T_avg_Nm": 1000.0, "T_ripple_pp_Nm": 14.0},
        {"T_avg_Nm": 1001.0, "T_ripple_pp_Nm": 14.1})


def test_rotor_history_append_is_guarded_during_probe():
    tree = ast.parse(Path(fs.__file__).read_text(encoding="utf-8"))
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)
                and len(test.values) == 2
                and isinstance(test.values[0], ast.Name)
                and test.values[0].id == "rotor_eddy"
                and isinstance(test.values[1], ast.UnaryOp)
                and isinstance(test.values[1].op, ast.Not)
                and "_GAP_PROBE_CTX" in ast.dump(test.values[1])
                and "get" in ast.dump(test.values[1])):
            continue
        guarded = any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "append"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "_histA_rot2"
            for child in ast.walk(node)
        )
    assert guarded


def test_probe_off_or_legacy_refine_switch_keeps_legacy_path(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(dict(kw))
        eps = 0.2 if os.environ.get("SB_GAP_REFINE") == "0" else 0.01
        return _result(kw, kw["gap_layers"], eps)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    monkeypatch.delenv("SB_GAP_PROBE", raising=False)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard"})
    assert len(calls) == 1 and not out["gap_refinement"].get("used_probe", False)

    calls.clear()
    monkeypatch.setenv("SB_GAP_PROBE", "1")
    monkeypatch.setenv("SB_GAP_REFINE", "0")
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard"})
    assert len(calls) == 1
    assert out["gap_refinement"]["skipped_reason"] == "SB_GAP_REFINE=0"


def test_optimization_and_stateful_excitations_never_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(fs, "fem_transient_sliding_band", lambda **kw:
                        calls.append(dict(kw)) or _result(kw, kw["gap_layers"], 0.01))
    _enable_probe(monkeypatch)
    for purpose, options in (
        ("optimization", {}),
        ("standard", {"drive": "voltage"}),
        ("standard", {"drive": "current", "excitation": object()}),
    ):
        calls.clear()
        out = fs._solve_with_gap_refinement({
            "gap_layers": 1.0, "sampling_purpose": purpose, **options})
        assert len(calls) == 1
        assert not out["gap_refinement"].get("used_probe", False)


def test_probe_error_falls_back_to_one_full_solve_at_requested_gap(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(dict(kw))
        if fs._GAP_PROBE_CTX.get():
            raise RuntimeError("probe failed")
        return _result(kw, kw["gap_layers"], 0.01)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard"})
    assert len(calls) == 2 and [c["gap_layers"] for c in calls] == [1.0, 1.0]
    gr = out["gap_refinement"]
    assert gr["selection_reason"] == "probe_failed"
    assert "RuntimeError" in gr["probe_fallback_reason"]
    assert gr["persist_gap_layers"] is None


def test_probe_cancellation_propagates_without_full_fallback(monkeypatch):
    class Cancel(BaseException):
        pass

    calls = []
    def fake(**kw):
        calls.append(dict(kw))
        raise Cancel()

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    with pytest.raises(Cancel):
        fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                       "sampling_purpose": "standard"})
    assert len(calls) == 1 and not fs._GAP_PROBE_CTX.get()


@pytest.mark.parametrize("probe_eps", [None, float("nan"), "not-a-number"])
def test_missing_or_invalid_probe_check_stops_without_refining(monkeypatch, probe_eps):
    calls = []

    def fake(**kw):
        calls.append(dict(kw))
        eps = probe_eps if kw.get("eddy") is False else None
        return _result(kw, kw["gap_layers"], eps)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard"})
    assert [c["gap_layers"] for c in calls] == [1.0, 1.0]
    assert out["gap_refinement"]["selection_reason"] == "probe_check_unavailable"
    assert out["gap_refinement"]["passed"] is False
    assert "ring check unavailable" in out["gap_layers_note"]


def test_hi_fidelity_floor_is_not_counted_as_a_gap_refinement(monkeypatch):
    calls = []

    def fake(**kw):
        calls.append(dict(kw))
        return _result(kw, 4.0, 0.2)

    monkeypatch.setattr(fs, "fem_transient_sliding_band", fake)
    _enable_probe(monkeypatch)
    out = fs._solve_with_gap_refinement({"gap_layers": 1.0,
                                         "sampling_purpose": "standard",
                                         "hi_fidelity": True})
    assert [c["gap_layers"] for c in calls] == [1.0, 4.0]
    assert out["gap_refinement"]["applied"] is False
    assert out["gap_refinement"]["persist_gap_layers"] is None
