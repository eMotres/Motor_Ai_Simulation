"""Legacy filtered records must not replace raw contract values."""
from motor_ai_sim.contracts.adapters import result_ir_from_transient


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
