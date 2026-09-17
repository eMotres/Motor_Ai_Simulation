"""Raw torque preservation through the actual transient postprocessing call."""
import ast
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.field_ops import band_limit_torque, torque_metrics
from motor_ai_sim.simulation.sb_postproc import torque_harmonics


@pytest.fixture(scope="module")
def transient_calls():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    transient = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "fem_transient_sliding_band")
    calls = {}
    for name in ("torque_metrics", "_torque_harmonics"):
        matches = [node for node in ast.walk(transient)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == name]
        assert len(matches) == 1
        calls[name] = compile(ast.Expression(matches[0]), str(source), "eval")
    return calls


@pytest.mark.parametrize("periods", [1.0, 1.5, 1.001, 2.0])
def test_transient_preserves_every_raw_sample_and_ripple(transient_calls, periods):
    count = round(72 * periods)
    theta = np.arange(count) * 2 * np.pi * periods / count
    torque = 10.0 + np.cos(6 * theta) + .7 * np.cos(5 * theta) + 1e-5 * np.cos(13 * theta)
    raw, ripple = eval(transient_calls["torque_metrics"],
                       {"torque_metrics": torque_metrics}, {"_T2": torque.tolist()})
    np.testing.assert_array_equal(raw, torque)
    assert ripple == 100 * (float(torque.max()) - float(torque.min())) / abs(float(torque.mean()))
    legacy_raw, legacy_ripple, alias_ripple, noise = band_limit_torque(torque, 72, periods)
    np.testing.assert_array_equal(legacy_raw, torque)
    assert legacy_ripple == alias_ripple == ripple
    assert noise is None


def test_raw_metrics_do_not_compute_a_harmonic_filter(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("raw torque must not compute an FFT filter")

    monkeypatch.setattr(np.fft, "rfft", forbidden)
    raw, ripple = torque_metrics([9., 10., 11.])
    assert raw == [9., 10., 11.] and ripple == 20.
    assert band_limit_torque([9., 10., 11.], 72, 1.) == (raw, ripple, ripple, None)


@pytest.mark.parametrize("periods", [.5, 1.001, 1.01, 1.5, 2.])
def test_actual_transient_spectrum_keeps_entire_fractional_window(transient_calls, periods):
    count = round(72 * periods)
    series = np.cos(6 * np.arange(count) * 2 * np.pi * periods / count).tolist()
    result = eval(transient_calls["_torque_harmonics"],
                  {"_torque_harmonics": torque_harmonics}, {
                      "_T2raw": series, "n_steps_per_period": 72,
                      "_sched_dth": [periods / count], "period_mech": 1.,
                  })
    orders, amplitudes = result
    np.testing.assert_allclose(orders, np.arange(1, count // 2 + 1) / periods)
    assert len(amplitudes) == count // 2
    # Independent direct DFT of every sample checks normalization and retention.
    bins = np.arange(1, count // 2 + 1)
    direct = np.abs(np.exp(-2j * np.pi * np.outer(bins, np.arange(count)) / count)
                    @ np.asarray(series)) * 2 / count
    if count % 2 == 0:
        direct[-1] /= 2
    np.testing.assert_allclose(amplitudes, direct, atol=1e-13)
