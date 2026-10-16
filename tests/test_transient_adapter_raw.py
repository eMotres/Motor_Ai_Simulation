"""Legacy filtered records must not replace raw contract values."""
from motor_ai_sim.contracts.adapters import (
    alias_linear_cross_check_keys,
    read_linear_cross_check,
    result_ir_from_transient,
)


def test_contract_prefers_raw_waveform_and_ripple_over_filtered_summary():
    result = result_ir_from_transient({
        "time_s": [0., 1.], "T_em_raw_Nm": [1., 3.], "T_em_Nm": [2., 2.],
        "T_em_filt_Nm": [2., 2.], "T_ripple_raw_pct": 100.,
        "summary": {"T_ripple_pct": 0., "T_ripple_filt_pct": 0.},
    })
    assert result.series.torque_Nm == [1., 3.]
    assert result.scalars.torque_ripple_pct == 100.


def test_contract_accepts_original_raw_series_key():
    result = result_ir_from_transient({
        "time_s": [0., 1.], "T_em_Nm": [1., 3.], "T_em_filt_Nm": [2., 2.],
        "T_ripple_pct": 100.,
    })
    assert result.series.torque_Nm == [1., 3.]
    assert result.scalars.torque_ripple_pct == 100.


def test_filtered_only_record_is_not_silently_used_as_raw():
    result = result_ir_from_transient({
        "time_s": [0., 1.], "T_em_filt_Nm": [2., 2.], "T_ripple_filt_pct": 0.,
    })
    assert result.series.torque_Nm == []
    assert result.scalars.torque_ripple_pct is None


def test_representative_contract_selfcheck_uses_raw_samples():
    from motor_ai_sim.contracts._selfcheck import _result

    assert "failure-payload ok" in _result()


# ── "honest" -> "linear" cross-check rename (read-compat) ────────────────────
# P_mag_honest_W / P_shaft_honest_W are a LINEAR estimate, not a frequency-
# domain "honest" one (docs/EDDY_TIME_INTEGRATION_2026-09-25.md); the solver
# (simulation/fem_solver_2d.py) still emits the old key names, so the adapter
# boundary above aliases the new names on without removing the old ones.

def test_result_ir_from_transient_aliases_new_linear_keys():
    result = result_ir_from_transient({
        "time_s": [0., 1.],
        "P_mag_honest_W": 1.587, "P_shaft_honest_W": 0.565,
    })
    # Old keys still read (a stored record from before the rename)…
    assert result.raw["P_mag_honest_W"] == 1.587
    assert result.raw["P_shaft_honest_W"] == 0.565
    # …and the new "linear cross-check" names are aliased on top of them.
    assert result.raw["P_mag_linear_W"] == 1.587
    assert result.raw["P_shaft_linear_W"] == 0.565


def test_result_ir_from_transient_alias_is_a_noop_with_no_honest_keys():
    result = result_ir_from_transient({"time_s": [0., 1.]})
    assert "P_mag_linear_W" not in result.raw
    assert "P_shaft_linear_W" not in result.raw


def test_alias_linear_cross_check_keys_never_overwrites_new_key():
    d = {"P_mag_honest_W": 1.0, "P_mag_linear_W": 2.0}
    alias_linear_cross_check_keys(d)
    assert d["P_mag_linear_W"] == 2.0


def test_read_linear_cross_check_prefers_new_key_falls_back_to_old():
    assert read_linear_cross_check({"P_mag_linear_W": 9.0, "P_mag_honest_W": 1.0}, "P_mag") == 9.0
    # An old stored record, never touched by the rename, still reads.
    assert read_linear_cross_check({"P_mag_honest_W": 1.0}, "P_mag") == 1.0
    assert read_linear_cross_check({}, "P_mag") is None
