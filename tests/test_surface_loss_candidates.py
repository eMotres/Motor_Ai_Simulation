import json
import ast

import numpy as np
import pytest

from motor_ai_sim.simulation import losses


class QuadraticSurface:
    n_points = 3
    f = np.array([100.0, 1000.0])

    def __init__(self):
        self.excursion_calls = []

    def w_per_m3(self, amplitude, frequency_hz, excursion=None, weight=None):
        self.excursion_calls.append(excursion)
        if excursion is not None:
            excursion["selected_evaluations"] = (
                excursion.get("selected_evaluations", 0) + 1)
        return np.asarray(amplitude) ** 2 * (1.0 + frequency_hz / 1000.0)


def _raw_surface_density(signal, surface, f_elec=933.33, n_periods=1.0):
    n = signal.shape[0]
    coeff = np.fft.rfft(signal, axis=0) / n
    amp = 2.0 * np.abs(coeff)
    if n % 2 == 0:
        amp[-1] = np.abs(coeff[-1])
    freq = np.arange(amp.shape[0]) * f_elec / n_periods
    density = np.zeros(signal.shape[1])
    for m in range(1, amp.shape[0]):
        if float(amp[m].max()) >= losses.HARMONIC_FLOOR_T:
            density += surface.w_per_m3(amp[m], float(freq[m]), None, None)
    return density


def test_closed_window_raw_and_detrended_surface_candidates_are_equal():
    n = 64
    theta = 2.0 * np.pi * np.arange(n) / n
    X = (np.cos(theta) + 0.1 * np.cos(5.0 * theta + 0.4))[:, None]
    Y = (0.2 * np.sin(theta))[:, None]
    surface = QuadraticSurface()
    raw = {}
    selected_wrap = {}
    selected_excursion = {}

    selected = losses.surface_loss_density(
        X, Y, surface, 1.0, 933.33, 1.0,
        excursion=selected_excursion, wrap=selected_wrap,
        raw_window_candidate=raw)

    np.testing.assert_allclose(raw["density"], selected, rtol=1e-12, atol=1e-12)
    assert selected_wrap["weight"] == 0.0
    assert selected_excursion["selected_evaluations"] > 0
    assert all(value is None or value is selected_excursion
               for value in surface.excursion_calls)


def test_open_window_retains_unmodified_raw_dft_surface_candidate():
    n = 40
    t = np.arange(n) / n
    X = np.cos(2.0 * np.pi * 1.714 * t)[:, None]
    Y = np.zeros_like(X)
    original_x, original_y = X.copy(), Y.copy()
    surface = QuadraticSurface()
    candidate = {}
    wrap = {}
    selected_excursion = {}

    selected = losses.surface_loss_density(
        X, Y, surface, 1.0, 933.33, 1.0,
        excursion=selected_excursion, wrap=wrap,
        raw_window_candidate=candidate)

    np.testing.assert_array_equal(X, original_x)
    np.testing.assert_array_equal(Y, original_y)
    np.testing.assert_allclose(
        candidate["density"], _raw_surface_density(X, surface),
        rtol=1e-13, atol=1e-13)
    assert wrap["weight"] > 0.9
    assert not np.allclose(candidate["density"], selected)
    assert all(value is None or value is selected_excursion
               for value in surface.excursion_calls)


def test_iron_terms_expose_json_safe_candidates_without_changing_selection(monkeypatch):
    class Material:
        name = "test steel"
        stacking_factor = 0.92

    surface = QuadraticSurface()
    monkeypatch.setattr(losses, "_get_surface", lambda material, coeff: surface)
    n = 40
    t = np.arange(n) / n
    X = np.cos(2.0 * np.pi * 1.714 * t)[:, None]
    Y = np.zeros_like(X)
    terms = {}
    classical, remainder = losses.iron_loss_series(
        [row.copy() for row in X], [row.copy() for row in Y],
        np.array([0]), np.array([1.0]), Material(), 1.0,
        933.33, n, lambda values: np.gradient(values, axis=0),
        lambda material: (1.0, 1.0, 1.0), terms=terms)

    assert terms["surface_selected_candidate"] == "detrended_legacy"
    assert terms["surface_detrended_candidate_W"] == terms["surface_W"]
    assert terms["surface_raw_window_candidate_W"] != pytest.approx(
        terms["surface_detrended_candidate_W"])
    assert np.isfinite(classical).all() and np.isfinite(remainder)
    json.dumps(terms, allow_nan=False)


def test_fem_result_has_one_entry_for_each_candidate_total():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/motor_ai_sim/simulation/fem_solver_2d.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    result_dicts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)]
        if "P_fe_avg_W" in keys and "P_fe_terms" in keys:
            result_dicts.append(keys)
    assert len(result_dicts) == 1
    keys = result_dicts[0]
    for key in ("P_fe_raw_window_candidate_avg_W",
                "P_fe_detrended_candidate_avg_W",
                "P_fe_surface_selected_candidate"):
        assert keys.count(key) == 1
