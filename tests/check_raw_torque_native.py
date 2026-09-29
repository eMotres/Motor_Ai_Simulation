"""Native audit: the deprecated flag cannot discard torque samples/orders."""
import numpy as np
import pytest

from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as fem
from motor_ai_sim.simulation.sb_postproc import torque_harmonics
from test_physics_regression import COMMON, CONNECTION, GEO_30MM, OVERRIDE, RPM


@pytest.mark.parametrize("requested_filter", [False, True])
def test_native_torque_is_raw_even_when_legacy_flag_is_requested(requested_filter):
    kwargs = dict(COMMON, element_order=2, demag=False, eddy=False,
                  I_phase_rms=60., daxis_deg=60., gamma_deg=0.,
                  rpm=RPM, connection=CONNECTION, n_parallel=1, star_delta="star",
                  geo_override=dict(GEO_30MM), torque_filter=requested_filter)
    try:
        set_request_materials(OVERRIDE)
        result = fem.fem_transient_sliding_band(**kwargs)
    finally:
        set_request_materials(None)
    np.testing.assert_array_equal(result["T_em_filt_Nm"], result["T_em_raw_Nm"])
    np.testing.assert_array_equal(result["T_em_Nm"], result["T_em_raw_Nm"])
    assert result["T_ripple_filt_pct"] == result["T_ripple_raw_pct"] == result["T_ripple_pct"]
    assert result["torque_filter_applied"] is False
    assert result["T_noise_floor_pct"] is None
    assert result["T_avg_maxwell_Nm"] == float(np.mean(result["T_em_maxwell_Nm"]))
    expected = torque_harmonics(result["T_em_maxwell_Nm"], result["n_steps_per_period"],
                                step_periods=result["dt_s"] / result["T_period_s"])
    assert result["T_harm_order"] == expected[0]
    np.testing.assert_array_equal(result["T_harm_amp"], expected[1])
    count = len(result["T_em_maxwell_Nm"])
    assert len(result["T_harm_order"]) == len(result["T_harm_amp"]) == count // 2
    np.testing.assert_allclose(result["T_harm_order"],
                               np.arange(1, count // 2 + 1) /
                               (count * result["dt_s"] / result["T_period_s"]))
