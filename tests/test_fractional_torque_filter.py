"""Exercise the transient's actual filter call without a costly FEM solve."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.field_ops import band_limit_torque


@pytest.fixture(scope="module")
def transient_filter_call():
    """Compile the production call, so a caller-side rounding regression fails."""
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    transient = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef)
                     and node.name == "fem_transient_sliding_band")
    calls = [node for node in ast.walk(transient)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "band_limit_torque"]
    assert len(calls) == 1
    return compile(ast.Expression(calls[0]), str(source), "eval")


def _run_transient_filter(call, n_samples, periods, step_periods, orders):
    theta = np.arange(n_samples) * step_periods * 2.0 * np.pi
    torque = np.full(n_samples, 10.0)
    for order, amplitude in orders.items():
        torque += amplitude * np.cos(order * theta)
    supplied_periods = []

    def capture_filter(series, steps_per_period, window_periods):
        supplied_periods.append(window_periods)
        return band_limit_torque(series, steps_per_period, window_periods)

    result = eval(call, {"band_limit_torque": capture_filter}, {
        "_T2": torque.tolist(), "n_steps_per_period": 72,
        "n_periods": periods, "period_mech": 360.0 / 7,
        "_sched_dth": [step_periods * 360.0 / 7] * n_samples,
    })
    assert supplied_periods == pytest.approx([n_samples * step_periods])
    return torque, result


def test_fractional_helper_preserves_sixth_order_ripple():
    theta = np.arange(108) * 2.0 * np.pi / 72
    torque = 10.0 + np.cos(6.0 * theta)
    filtered, ripple, raw_ripple, noise = band_limit_torque(torque, 72, 1.5)
    np.testing.assert_array_equal(filtered, torque)
    assert ripple == pytest.approx(20.0)
    assert raw_ripple == pytest.approx(20.0)
    assert noise == 0.0


@pytest.mark.parametrize("n_samples,periods,step_periods", [
    (108, 1.5, 1.5 / 108),
    # Uniform schedule preserves the requested span when frame counts round.
    (72, 1.001, 1.001 / 72),
    # Trimming 72 settling frames from a 144-frame, 2.001-period uniform march
    # leaves a measured span that differs from the requested 1.001 periods.
    (72, 1.001, 2.001 / 144),
    # A mixed PWM schedule reports fine steps after its coarse settling prefix.
    (73, 1.01, 1.0 / 72),
])
def test_transient_call_bypasses_fractional_sampled_windows(
        transient_filter_call, n_samples, periods, step_periods):
    torque, (filtered, ripple, raw_ripple, noise) = _run_transient_filter(
        transient_filter_call, n_samples, periods, step_periods, {6: 1.0})
    np.testing.assert_array_equal(filtered, torque)
    assert ripple == raw_ripple
    assert noise == 0.0


@pytest.mark.parametrize("periods", [1, 2])
def test_transient_call_keeps_integer_window_comb_behavior(
        transient_filter_call, periods):
    torque, result = _run_transient_filter(
        transient_filter_call, 72 * periods, periods, 1.0 / 72,
        {6: 1.0, 5: 0.7})
    filtered, ripple, raw_ripple, noise = result
    theta = np.arange(72 * periods) * 2.0 * np.pi / 72
    np.testing.assert_allclose(filtered, 10.0 + np.cos(6.0 * theta), atol=1e-12)
    assert ripple == pytest.approx(20.0)
    assert raw_ripple > ripple
    assert noise == pytest.approx(100.0 * 0.7 / np.sqrt(2.0) / 10.0)
    np.testing.assert_allclose(result[0], band_limit_torque(torque, 72, periods)[0])
