"""Angular-sampling policy: no FEM, mesh change, or torque filtering.

2026-09-24 (orchestrator's held-item fix of 8997bb3 / 821f3df): six raw
samples per cogging cycle is an OPT-IN ``sampling_purpose="cogging_quality"``
mode. Standard and optimization runs keep their requested (snapped) steps, as
68de0ca did, and only RECORD that they are below the cogging target; the
internal ψ probes (ψ_PM, Ld/Lq, bench) and the d-axis calibration are always
exempt. The raise tests below therefore pass ``cogging_quality`` explicitly —
they pinned 8997bb3's every-run default, which is intentionally gone.
"""

import ast
from pathlib import Path

import pytest

from motor_ai_sim.simulation.fem_solver_2d import _cogging_frame_policy

Q = dict(sampling_purpose="cogging_quality")


@pytest.mark.parametrize("slots,poles,pairs", [(12, 14, 7), (24, 28, 14)])
def test_twelve_cogging_cycles_need_seventy_two_raw_frames(slots, poles, pairs):
    decision = _cogging_frame_policy(slots, poles, pairs, 12, 144, **Q)
    assert decision["cycles_per_electrical_period"] == 12
    assert decision["min_required_steps_per_period"] == 72
    assert decision["steps_per_period"] == 72
    assert decision["raw_samples_per_cycle"] == 6
    assert decision["sufficient"] and decision["auto_raised"]
    assert decision["reason"] == "raised_to_existing_slip_ring_divisor"


def test_smallest_existing_divisor_is_used_without_refining_ring():
    decision = _cogging_frame_policy(12, 14, 7, 12, 120, **Q)
    assert decision["steps_per_period"] == 120  # 72..119 do not divide 120
    assert decision["raw_samples_per_cycle"] == 10
    assert 120 % decision["steps_per_period"] == 0


def test_insufficient_ring_keeps_every_raw_sample_and_explains_it():
    decision = _cogging_frame_policy(12, 14, 7, 48, 48, **Q)
    assert decision["steps_per_period"] == 48
    assert decision["raw_samples_per_cycle"] == 4
    assert not decision["sufficient"]
    assert not decision["auto_raised"]
    assert decision["reason"] == "insufficient_existing_slip_ring_divisor"


def test_continuous_angle_path_uses_maximum_without_ring_refinement():
    raised = _cogging_frame_policy(12, 14, 7, 48, 48, continuous_angle=True, **Q)
    kept = _cogging_frame_policy(12, 14, 7, 96, 48, continuous_angle=True, **Q)
    assert raised["steps_per_period"] == 72
    assert raised["auto_raised"] and raised["sufficient"]
    assert raised["reason"] == "raised_continuous_angle_to_target_samples_per_cycle"
    assert kept["steps_per_period"] == 96
    assert not kept["auto_raised"] and kept["reason"] is None


@pytest.mark.parametrize("purpose,target", [("standard", 6), ("optimization", 3)])
@pytest.mark.parametrize("slots,poles,pairs,nodes", [(12, 14, 7, 144),
                                                     (24, 28, 14, 120)])
def test_default_runs_keep_requested_steps_and_record_the_shortfall(
        purpose, target, slots, poles, pairs, nodes):
    decision = _cogging_frame_policy(slots, poles, pairs, 12, nodes,
                                     sampling_purpose=purpose)
    assert decision["steps_per_period"] == 12          # 68de0ca: as requested
    assert not decision["auto_raised"]
    assert decision["purpose"] == purpose
    assert decision["target_raw_samples_per_cycle"] == target
    assert decision["min_required_steps_per_period"] == target * 12
    assert not decision["sufficient"]
    assert not decision["final_quality_sufficient"]
    assert decision["reason"] == "requested_steps_kept_below_cogging_target"


def test_default_run_above_target_is_untouched_and_sufficient():
    decision = _cogging_frame_policy(12, 14, 7, 72, 144)
    assert decision["steps_per_period"] == 72
    assert decision["sufficient"] and decision["final_quality_sufficient"]
    assert decision["reason"] is None and not decision["auto_raised"]


def test_private_daxis_calibration_keeps_its_flux_sampling_grid():
    for purpose in ("standard", "cogging_quality"):
        decision = _cogging_frame_policy(
            12, 14, 7, 24, 144, internal_daxis_calibration=True,
            sampling_purpose=purpose)
        assert decision["steps_per_period"] == 24
        assert not decision["auto_raised"]
        assert decision["reason"] == "internal_daxis_calibration_exempt"


def test_internal_psi_probes_are_exempt():
    decision = _cogging_frame_policy(24, 28, 14, 6, 120,
                                     sampling_purpose="internal_probe")
    assert decision["steps_per_period"] == 6
    assert not decision["auto_raised"]
    assert decision["reason"] == "internal_probe_exempt"


def test_every_internal_probe_call_site_names_its_purpose():
    """ψ_PM, Ld/Lq, d-axis calibration and the bench pass internal_probe."""
    root = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
    solver = (root / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    tree = ast.parse(solver)
    for fn in ("noload_psi_pm", "noload_incremental_ldq", "_calibrate_daxis"):
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == fn)
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)
                 and isinstance(c.func, ast.Name)
                 and c.func.id == "em_transient_eval"]
        assert calls, fn
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords if k.arg}
            assert isinstance(kw.get("sampling_purpose"), ast.Constant), fn
            assert kw["sampling_purpose"].value == "internal_probe", fn
    routes = (root / "routes" / "simulation.py").read_text(encoding="utf-8")
    bench = routes[routes.index("def _bench_compute"):]
    bench = bench[:bench.index("_t0 = _t2.time()")]
    assert 'sampling_purpose="internal_probe"' in bench


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
