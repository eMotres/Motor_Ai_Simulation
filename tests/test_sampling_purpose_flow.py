"""Optimization sampling purpose travels through the no-FEM process seam."""

import ast
import inspect
import json
from pathlib import Path

import pytest

from motor_ai_sim.routes import optimization as opt
from motor_ai_sim.simulation.fem_solver_2d import (
    em_transient_eval, fem_transient_sliding_band,
)


def test_optimizer_subprocess_spec_explicitly_names_purpose(monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 987654
        returncode = 0

        def __init__(self, argv, **kwargs):
            assert argv[-1] == "motor_ai_sim.optimization.refine_proc"

        def communicate(self, input=None, timeout=None):
            captured.update(json.loads(input))
            return '@@RESULT@@{"ok":false,"error":"stub only"}', ""

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(opt, "_eval_env_for", lambda threads: {})
    monkeypatch.setattr(opt, "_record_eval_seconds", lambda *a, **kw: None)
    monkeypatch.setattr(opt, "measured_eval_seconds",
                        lambda *a, **kw: {"s_per_eval": 0.0})
    monkeypatch.setattr(opt, "_scan_worker_count", lambda: 1)
    out = opt._subprocess_eval({}, 50.0, 12, 120.0, _log=False)
    assert out == {"ok": False, "error": "stub only"}
    assert captured["sampling_purpose"] == "optimization"


def test_cache_keys_separate_purposes_and_preserve_provenance():
    kwargs = dict(overrides={}, current_a=50.0, steps=12,
                  coil_temp_c=120.0, n_periods=1.0, gamma_deg=0.0,
                  mesh_size_mm=4.0, min_size_mm=0.3, n_sectors=2,
                  pole_copy=False, torque_filter=False, cfg_fp="fixture",
                  demag=False)
    assert opt._eval_cache_key(**kwargs, sampling_purpose="optimization") != (
        opt._eval_cache_key(**kwargs, sampling_purpose="standard"))
    for field in ("cogging_sampling_purpose",
                  "cogging_target_raw_samples_per_cycle",
                  "cogging_raw_samples_per_cycle",
                  "cogging_sampling_sufficient",
                  "cogging_sampling_final_quality_sufficient"):
        assert field in opt._RES_KEYS
    with pytest.raises(ValueError, match="sampling_purpose"):
        opt._eval_cache_key(**kwargs, sampling_purpose="unknown")


def test_sampling_provenance_survives_result_adapter_and_point_projection():
    from motor_ai_sim.contracts.adapters import result_ir_from_transient
    metadata = {
        "cogging_sampling_purpose": "optimization",
        "cogging_target_raw_samples_per_cycle": 3,
        "cogging_cycles_per_electrical_period": 12,
        "cogging_min_required_steps_per_period": 36,
        "cogging_final_quality_min_required_steps_per_period": 72,
        "cogging_raw_samples_per_cycle": 3.0,
        "cogging_sampling_sufficient": True,
        "cogging_sampling_final_quality_sufficient": False,
        "cogging_sampling_auto_raised": True,
        "cogging_sampling_reason": "raised_to_existing_slip_ring_divisor",
    }
    raw = result_ir_from_transient({"T_avg_Nm": 1.0, **metadata}).raw
    assert all(raw[k] == v for k, v in metadata.items())
    candidate = opt._point_from_eval({"ok": True, "res": dict(raw)}, {}, 50.0,
                                     0, 0, 100.0)
    cached = {k: candidate[k] for k in opt._RES_KEYS if k in candidate}
    assert all(cached[k] == v for k, v in metadata.items())


def test_solver_and_manual_route_default_to_standard():
    from motor_ai_sim.optimization.refine_proc import run_one
    from motor_ai_sim.routes.simulation import get_fem_transient
    for fn in (get_fem_transient, em_transient_eval, fem_transient_sliding_band):
        assert inspect.signature(fn).parameters["sampling_purpose"].default == "standard"
    assert inspect.signature(run_one).parameters["sampling_purpose"].default == "optimization"


def test_declared_purpose_survives_kernel_and_route_adapters():
    from motor_ai_sim.modules.solvers import _call_filtered
    assert _call_filtered(lambda *, sampling_purpose="standard": sampling_purpose,
                          {"sampling_purpose": "optimization", "other": 1}) == (
        "optimization")
    root = Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
    refine = (root / "optimization" / "refine_proc.py").read_text(encoding="utf-8")
    route = (root / "routes" / "simulation.py").read_text(encoding="utf-8")
    fem = (root / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    assert 'sampling_purpose=spec.get("sampling_purpose", "optimization")' in refine
    assert '"sampling_purpose": sampling_purpose' in refine
    assert 'sampling_purpose=sampling_purpose' in route
    assert 'sampling_purpose=_sampling_purpose(sampling_purpose)' in fem
    assert '("sampling_purpose", sampling_purpose)' in route


def test_fixed_48_frame_optimizer_floors_are_gone():
    src = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
           / "routes" / "optimization.py").read_text(encoding="utf-8")
    assert "steps = max(steps, 48)" not in src
    assert "steps_pp = max(steps_pp, 48)" not in src
    # The independently required PWM resolution guard remains in FEM.
    fem = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
           / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    assert "if _carriers and n_steps_per_period < _req_steps:" in fem


def test_every_optimizer_eval_and_cache_call_explicitly_names_purpose():
    path = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
            / "routes" / "optimization.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # The enclosing top-level function of every call, so a literal "standard"
    # can be pinned to the two routes whose job IS a standard re-solve.
    owner = {}
    for fn in tree.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner[id(node)] = fn.name
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name)
             and node.func.id in ("_subprocess_eval", "_eval_cache_key")]
    assert len(calls) >= 10  # sweep, descent, current probes and auto search
    dynamic = 0
    standard = set()
    for call in calls:
        purpose = [kw.value for kw in call.keywords
                   if kw.arg == "sampling_purpose"]
        assert len(purpose) == 1, (call.func.id, call.lineno)
        if isinstance(purpose[0], ast.Constant) and purpose[0].value == "cogging_quality":
            # Only the Sweep point's Apply check and the on-demand re-check of a
            # stored optimizer point solve at final (cogging) quality by
            # construction; the workers pass it as their `purpose`.
            standard.add(owner.get(id(call)))
        elif isinstance(purpose[0], ast.Constant):
            assert purpose[0].value == "optimization", (call.func.id, call.lineno)
        else:
            # The four worker-local _eval_at closures and screen cache key
            # explicitly forward the caller's validated purpose so final
            # standard re-evals can reuse the exact pinned solve arguments.
            assert isinstance(purpose[0], ast.Name)
            assert purpose[0].id == "purpose"
            dynamic += 1
    assert dynamic == 5
    assert standard == {"scan_validate_point", "descent_validate_point"}
