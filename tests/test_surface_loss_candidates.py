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
        # every harmonic (no amplitude floor since 2026-09-24); an exactly
        # zero one contributes exactly zero
        if float(amp[m].max()) > 0.0:
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
    np.testing.assert_allclose(raw["detrended_density"], selected,
                               rtol=1e-12, atol=1e-12)
    assert selected_wrap["weight"] == 0.0
    assert selected_excursion["selected_evaluations"] > 0
    # 2026-09-24: the legacy comparator keeps its OWN envelope log.
    assert all(value is None or value is selected_excursion
               or value is raw["raw_excursion"]
               or value is raw["detrended_excursion"]
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
    # 2026-09-24 (orchestrator's held-item fix, owner: no filter may feed a
    # selected value): the DEFAULT selection is the raw window; the ramp-removed
    # density is only the labelled diagnostic beside it. This line used to
    # assert that the default returned the detrended value.
    np.testing.assert_allclose(candidate["density"], selected)
    assert not np.allclose(candidate["density"], candidate["detrended_density"])
    assert candidate["selected_candidate"] == "raw_window_unfiltered"
    assert all(value is None or value is selected_excursion
               or value is candidate["raw_excursion"]
               or value is candidate["detrended_excursion"]
               for value in surface.excursion_calls)
    with pytest.raises(ValueError, match="diagnostic only"):
        losses.surface_loss_density(X, Y, surface, 1.0, 933.33, 1.0,
                                    select_raw_window=False)
    selected_raw_candidate = {}
    raw_selected_excursion = {}
    selected_raw = losses.surface_loss_density(
        X, Y, surface, 1.0, 933.33, 1.0,
        excursion=raw_selected_excursion, raw_window_candidate=selected_raw_candidate,
        select_raw_window=True)
    np.testing.assert_allclose(selected_raw, selected_raw_candidate["density"])
    assert selected_raw_candidate["selected_candidate"] == "raw_window_unfiltered"
    assert raw_selected_excursion is selected_raw_candidate["raw_excursion"]


def test_iron_terms_select_unfiltered_raw_candidate_and_serialize(monkeypatch):
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

    assert terms["surface_selected_candidate"] == "raw_window_unfiltered"
    assert terms["surface_raw_window_candidate_W"] == terms["surface_W"]
    assert terms["surface_raw_window_candidate_W"] > terms[
        "surface_detrended_candidate_W"]
    assert terms["legacy_wrap_guard_weight"] > 0.9
    assert np.isfinite(classical).all() and np.isfinite(remainder)
    # Both returned components are in watts: ``classical`` already includes
    # steel volume (fixture area=1 m², stack=1 m, fill=0.92), while remainder
    # is the per-cycle surface total minus mean classical eddy watts.
    assert float(np.mean(classical)) + float(remainder) == pytest.approx(
        terms["surface_W"], rel=1e-12, abs=1e-12)
    json.dumps(terms, allow_nan=False)


def test_small_amplitudes_are_billed_no_floor(monkeypatch):
    """0.4 mT used to fall under the 1 mT HARMONIC_FLOOR_T and bill 0 W while
    its dB/dt did not vanish; with no floor (owner 2026-09-24) it is billed,
    and a CONSTANT field (no AC at all) still bills exactly zero."""
    class Material:
        name = "small-field steel"
        stacking_factor = 1.0

    monkeypatch.setattr(losses, "_get_surface", lambda material, coeff: QuadraticSurface())
    n = 40
    t = np.arange(n) / n
    derivative = lambda values: np.gradient(values, axis=0)
    x = (4e-4 * np.cos(2*np.pi*t))[:, None]
    terms = {}
    classical, rest = losses.iron_loss_series(
        list(x), list(np.zeros_like(x)), np.array([0]), np.array([1.0]),
        Material(), 1.0, 933.33, n, derivative,
        lambda material: (1.0, 1.0, 1.0), terms=terms)
    assert terms["surface_W"] == pytest.approx(
        float(np.sum(_raw_surface_density(x, QuadraticSurface()))), rel=1e-12)
    assert terms["surface_W"] > 0.0
    x0 = np.full((n, 1), 0.7)
    terms0 = {}
    classical0, rest0 = losses.iron_loss_series(
        list(x0), list(np.zeros_like(x0)), np.array([0]), np.array([1.0]),
        Material(), 1.0, 933.33, n, derivative,
        lambda material: (1.0, 1.0, 1.0), terms=terms0)
    # (FFT round-off of a constant is ~1e-17 T, billed as its square)
    assert terms0["surface_W"] == pytest.approx(0.0, abs=1e-20)
    assert terms0["eddy_W"] == 0.0
    assert float(np.mean(classical0)) + rest0 == pytest.approx(0.0, abs=1e-20)


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
    assert '"raw_window_unfiltered" if _has_surface_candidates' in source
