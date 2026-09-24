"""Angular-sampling policy: no FEM, mesh change, or torque filtering."""

import ast
from pathlib import Path

import pytest

from motor_ai_sim.simulation.fem_solver_2d import _cogging_frame_policy


@pytest.mark.parametrize("slots,poles,pairs", [(12, 14, 7), (24, 28, 14)])
def test_twelve_cogging_cycles_need_seventy_two_raw_frames(slots, poles, pairs):
    decision = _cogging_frame_policy(slots, poles, pairs, 12, 144)
    assert decision["cycles_per_electrical_period"] == 12
    assert decision["min_required_steps_per_period"] == 72
    assert decision["steps_per_period"] == 72
    assert decision["raw_samples_per_cycle"] == 6
    assert decision["sufficient"] and decision["auto_raised"]
    assert decision["reason"] == "raised_to_existing_slip_ring_divisor"


def test_smallest_existing_divisor_is_used_without_refining_ring():
    decision = _cogging_frame_policy(12, 14, 7, 12, 120)
    assert decision["steps_per_period"] == 120  # 72..119 do not divide 120
    assert decision["raw_samples_per_cycle"] == 10
    assert 120 % decision["steps_per_period"] == 0


def test_insufficient_ring_keeps_every_raw_sample_and_explains_it():
    decision = _cogging_frame_policy(12, 14, 7, 48, 48)
    assert decision["steps_per_period"] == 48
    assert decision["raw_samples_per_cycle"] == 4
    assert not decision["sufficient"]
    assert not decision["auto_raised"]
    assert decision["reason"] == "insufficient_existing_slip_ring_divisor"


def test_continuous_angle_path_uses_maximum_without_ring_refinement():
    raised = _cogging_frame_policy(12, 14, 7, 48, 48, continuous_angle=True)
    kept = _cogging_frame_policy(12, 14, 7, 96, 48, continuous_angle=True)
    assert raised["steps_per_period"] == 72
    assert raised["auto_raised"] and raised["sufficient"]
    assert raised["reason"] == "raised_continuous_angle_to_target_samples_per_cycle"
    assert kept["steps_per_period"] == 96
    assert not kept["auto_raised"] and kept["reason"] is None


def test_private_daxis_calibration_keeps_its_flux_sampling_grid():
    decision = _cogging_frame_policy(
        12, 14, 7, 24, 144, internal_daxis_calibration=True)
    assert decision["steps_per_period"] == 24
    assert not decision["auto_raised"]
    assert decision["reason"] == "internal_daxis_calibration_exempt"


def test_optimization_three_raw_samples_per_cycle_and_separate_final_quality():
    small = _cogging_frame_policy(12, 14, 7, 12, 144,
                                  sampling_purpose="optimization")
    large = _cogging_frame_policy(24, 28, 14, 12, 120,
                                  sampling_purpose="optimization")
    assert small["steps_per_period"] == 36
    assert large["steps_per_period"] == 40
    assert small["target_raw_samples_per_cycle"] == 3
    assert small["purpose"] == "optimization"
    assert small["sufficient"] and not small["final_quality_sufficient"]
    assert large["sufficient"] and not large["final_quality_sufficient"]
    assert _cogging_frame_policy(12, 14, 7, 12, 144)["steps_per_period"] == 72
    assert _cogging_frame_policy(24, 28, 14, 12, 120)["steps_per_period"] == 120


@pytest.mark.parametrize("purpose", ["final", "manual", "", None, 3])
def test_unknown_sampling_purpose_is_rejected(purpose):
    with pytest.raises(ValueError, match="sampling_purpose"):
        _cogging_frame_policy(12, 14, 7, 12, 144,
                              sampling_purpose=purpose)


@pytest.mark.parametrize("args", [
    (0, 14, 7, 12, 144), (12, -14, 7, 12, 144),
    (12, 13, 6, 12, 144), (12, 14, 6, 12, 144),
    (12, 14, 7, 0, 144), (12, 14, 7, 12, 0),
    (12, 14, 7, 12.5, 144),
])
def test_invalid_counts_are_rejected(args):
    with pytest.raises(ValueError):
        _cogging_frame_policy(*args)


def test_final_count_precedes_excitation_and_schedule_and_is_reported():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    solver = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "fem_transient_sliding_band")
    policy_call = next(n for n in ast.walk(solver) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name)
                       and n.func.id == "_cogging_frame_policy")
    hook = next(n for n in ast.walk(solver) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "_snap_hook")
    assert policy_call.lineno < hook.lineno
    assert 'getattr(_DAXIS_TLS, "calibrating", False)' in source
    assert source.index('n_steps_per_period = _cogging_sampling["steps_per_period"]') < source.index(
        '_snap_hook = getattr(_src, "on_steps_snapped", None)')
    for field in ("cogging_sampling_purpose", "cogging_target_raw_samples_per_cycle",
                  "cogging_cycles_per_electrical_period",
                  "cogging_min_required_steps_per_period",
                  "cogging_raw_samples_per_cycle", "cogging_sampling_sufficient",
                  "cogging_sampling_final_quality_sufficient",
                  "cogging_sampling_auto_raised", "cogging_sampling_reason"):
        assert f'"{field}":' in source
    assert '"n_steps_per_period_requested": int(_req_steps)' in source
    assert '"steps_snapped": bool(int(n_steps_per_period) != int(_req_steps))' in source
