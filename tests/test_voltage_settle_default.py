"""No-FEM guards for the plain sinusoidal-voltage settle.

Since 2026-09-26 the plain sinusoid (no eddy, no demag, no explicit count) no
longer marches a FIXED 4 periods (f83e60f): it schedules a cap and stops when
the period-to-period change of the phase-current amplitude, the mean torque and
the torque ripple is under a relative tolerance.  Every other source keeps its
own count.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.fem_solver_2d import (
    _select_voltage_settle_periods, _settle_period_metrics,
    _settle_period_residual, _sine_settle_criterion)


def _choose(periods=10, **changes):
    flags = dict(sinusoidal_voltage=True, eddy=False, demag=False,
                 explicit_override=False)
    flags.update(changes)
    return _select_voltage_settle_periods(periods, **flags)


def test_only_plain_sinusoidal_voltage_converges_to_a_criterion():
    assert _choose() == (F._V_SETTLE_CAP_DEFAULT,
                         "plain_sinusoidal_voltage_converged_settle")
    assert _choose(converged_cap=17) == (
        17, "plain_sinusoidal_voltage_converged_settle")
    for changes in (dict(eddy=True), dict(demag=True),
                    dict(sinusoidal_voltage=False),
                    dict(explicit_override=True)):
        assert _choose(**changes) == (10, "source_policy_unchanged")


@pytest.mark.parametrize("periods", [0, 2, 4, 6, 12])
def test_source_counts_and_explicit_override_are_preserved(periods):
    assert _choose(periods) == (periods, "source_policy_unchanged")
    assert _choose(periods, explicit_override=True) == (
        periods, "source_policy_unchanged")


def test_invalid_settle_counts_rejected():
    with pytest.raises(ValueError):
        _choose(-1)
    with pytest.raises(ValueError):
        _choose(2.5)


def test_criterion_defaults_and_loud_validation(monkeypatch):
    monkeypatch.delenv("SB_V_SETTLE_TOL", raising=False)
    monkeypatch.delenv("SB_V_SETTLE_CAP", raising=False)
    assert _sine_settle_criterion() == (F._V_SETTLE_TOL_DEFAULT,
                                        F._V_SETTLE_CAP_DEFAULT)
    monkeypatch.setenv("SB_V_SETTLE_TOL", "2e-4")
    monkeypatch.setenv("SB_V_SETTLE_CAP", "12")
    assert _sine_settle_criterion() == (2e-4, 12)
    for tol in ("abc", "0", "-1e-3", "1.5", "nan", "inf"):
        monkeypatch.setenv("SB_V_SETTLE_TOL", tol)
        with pytest.raises(ValueError):
            _sine_settle_criterion()
    monkeypatch.setenv("SB_V_SETTLE_TOL", "1e-3")
    for cap in ("x", "1", "0", "2.5"):
        monkeypatch.setenv("SB_V_SETTLE_CAP", cap)
        with pytest.raises(ValueError):
            _sine_settle_criterion()


def _period(amp, t_mean, t_ripple_amp, n=36):
    th = np.linspace(0.0, 2 * math.pi, n, endpoint=False)
    ia = amp * np.cos(th)
    ib = amp * np.cos(th - 2 * math.pi / 3)
    ic = amp * np.cos(th + 2 * math.pi / 3)
    T = t_mean + t_ripple_amp * np.cos(6 * th)
    return T, ia, ib, ic, np.ones(n)


def test_period_metrics_are_the_reported_quantities():
    m = _settle_period_metrics(*_period(100.0, 2.0, 0.05))
    assert m["I_amp_A"] == pytest.approx(100.0, rel=1e-12)
    assert m["T_mean_Nm"] == pytest.approx(2.0, rel=1e-12)
    assert m["T_pp_Nm"] == pytest.approx(0.1, rel=1e-9)
    assert m["T_ripple_pct"] == pytest.approx(5.0, rel=1e-9)


def test_residual_is_the_largest_relative_change():
    a = _settle_period_metrics(*_period(100.0, 2.0, 0.05))
    b = _settle_period_metrics(*_period(100.1, 2.0, 0.05))
    r = _settle_period_residual(a, b)
    assert r["I_amp"] == pytest.approx(0.1 / 100.1, rel=1e-9)
    assert r["T_mean"] == pytest.approx(0.0, abs=1e-15)
    assert r["max"] == r["I_amp"]
    c = _settle_period_metrics(*_period(100.1, 2.0, 0.0505))
    r = _settle_period_residual(b, c)
    assert r["T_ripple"] == pytest.approx(0.01 / 1.01 * 1.0, rel=1e-6)
    assert r["max"] == r["T_ripple"]


def test_no_load_torque_is_judged_on_its_waveform_not_a_zero_mean():
    a = _settle_period_metrics(*_period(0.0, 0.0, 1e-3))
    b = _settle_period_metrics(*_period(0.0, 1e-6, 1e-3))
    r = _settle_period_residual(a, b)
    assert math.isfinite(r["max"])
    assert r["T_mean"] == pytest.approx(1e-6 / 2e-3, rel=1e-6)


def test_the_loop_stops_on_the_criterion_and_splices_a_prefix():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    assert source.index("_settle_static_effective, _settle_selection_reason") < source.index(
        "_S = _build_schedule(_settle_static_effective)")
    assert "sinusoidal_voltage=(type(_src) is _SineVoltageSource)" in source
    assert "explicit_override=bool(_SOURCE_V_SETTLE_ENV)" in source
    # the convergence test, the anchor guard and the prefix splice
    assert "_cs_p - 1 > int(_conv_settle[\"last_anchor_period\"])" in source
    assert "_S = _build_schedule(int(_cs_p))" in source
    assert "_fseq = _fseq[:_fi] + list(range(k + 1, int(n_total)))" in source
    assert "refusing to splice" in source
    # the schedule maths that the count drives is unchanged
    assert "_v_settle_periods = int(_settle_p)" in source
    assert "_vskip = _v_settle_periods * max(2, _v_nspp)" in source
    assert "n_periods = float(n_periods) + float(_v_settle_periods)" in source
    assert "n_total += _vskip" in source
    assert "n_total -= _vskip" in source
    # …and the result records what ran
    assert '"voltage_settle_periods": int(_v_settle_periods)' in source
    assert '"voltage_settle_selection_reason": _settle_selection_reason' in source
    assert '"voltage_settle": _voltage_settle' in source
    assert '"settle_periods": int(_v_settle_periods)' in source
