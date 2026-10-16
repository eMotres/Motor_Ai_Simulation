"""controller.r_th_jc_k_w reaches the coupled run's controller settings
(for cards whose datasheet prints no R_th(j-c); 2026-09-28)."""
from motor_ai_sim.routes import coupled as cp


def _settings(ctl):
    return cp._controller_settings({"controller": ctl}, rpm=20900.0,
                                   inverter={"f_carrier_hz": 24000.0, "v_dc_V": 799.2})


def test_stated_r_th_jc_is_passed_through():
    s = _settings({"device": "IMCQ120R004M2H", "devices_parallel": 2,
                   "r_th_jc_k_w": 0.06, "v_gs_off_V": -5.0})
    assert s["r_th_jc_k_w"] == 0.06


def test_absent_r_th_jc_stays_none():
    s = _settings({"device": "IMCQ120R004M2H", "devices_parallel": 3})
    assert s["r_th_jc_k_w"] is None
