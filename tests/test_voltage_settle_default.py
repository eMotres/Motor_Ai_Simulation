"""No-FEM guards for the plain sinusoidal-voltage settle default."""

from pathlib import Path

import pytest

from motor_ai_sim.simulation.fem_solver_2d import _select_voltage_settle_periods


def _choose(periods=10, **changes):
    flags = dict(sinusoidal_voltage=True, eddy=False, demag=False,
                 explicit_override=False)
    flags.update(changes)
    return _select_voltage_settle_periods(periods, **flags)


def test_only_plain_sinusoidal_voltage_default_shortens():
    assert _choose() == (4, "plain_sinusoidal_voltage_default_ab_validated")
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


def test_selected_count_drives_schedule_trim_and_result_metadata():
    source = (Path(__file__).resolve().parents[1] / "src" / "motor_ai_sim"
              / "simulation" / "fem_solver_2d.py").read_text(encoding="utf-8")
    assert source.index("_settle_static_effective, _settle_selection_reason") < source.index(
        "_S = _build_schedule(_settle_static_effective)")
    assert "sinusoidal_voltage=(type(_src) is _SineVoltageSource)" in source
    assert "explicit_override=bool(_SOURCE_V_SETTLE_ENV)" in source
    assert "_v_settle_periods = int(_settle_p)" in source
    assert "_vskip = _v_settle_periods * max(2, _v_nspp)" in source
    assert "n_periods = float(n_periods) + float(_v_settle_periods)" in source
    assert "n_total += _vskip" in source
    assert "n_total -= _vskip" in source
    assert '"voltage_settle_periods": int(_v_settle_periods)' in source
    assert '"voltage_settle_selection_reason": _settle_selection_reason' in source
    assert '"settle_periods": int(_v_settle_periods)' in source
