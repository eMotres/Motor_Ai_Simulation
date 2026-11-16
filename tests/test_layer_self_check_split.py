import numpy as np

from motor_ai_sim.simulation.virtual_work_torque import layer_self_check


def test_pure_dc_offset_has_no_ac_part():
    a = -0.1 + 0.02 * np.sin(np.linspace(0, 6.28, 40, endpoint=False))
    r = layer_self_check(a + 0.002, a)
    assert abs(r["dc_diff_Nm"] - 0.002) < 1e-12 and r["ac_max_abs_diff_Nm"] < 1e-12
    assert r["rel_ac_to_ripple_scale"] < 1e-9
    assert abs(r["mean_diff_Nm"] - r["dc_diff_Nm"]) < 1e-15
    assert r["rel_to_ripple_scale"] > 0.04          # the legacy number still carries the offset


def test_ac_part_is_the_centred_max_and_legacy_keys_unchanged():
    t = np.linspace(0, 6.28, 40, endpoint=False)
    a = -0.1 + 0.02 * np.sin(t)
    b = a + 0.001 + 0.003 * np.sin(3 * t)
    r = layer_self_check(a, b)
    d = a - b
    assert abs(r["ac_max_abs_diff_Nm"] - np.max(np.abs(d - d.mean()))) < 1e-12
    assert r["max_abs_diff_Nm"] == float(np.max(np.abs(d)))


def test_empty_keeps_all_keys_none():
    r = layer_self_check([], [])
    assert r["dc_diff_Nm"] is None and r["rel_ac_to_ripple_scale"] is None
