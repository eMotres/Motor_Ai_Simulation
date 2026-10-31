"""The PWM speed-up of 2026-10-31: the thermal loop on sine + the PWM harmonic
delta (``coupled_pwm_delta``), the aim of the final PWM pass, the six-phase
L_xy probe reused, and the held steady sine state found by a fixed-current
request at the held current."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim import coupled_pwm_delta as D


def _mesh(shift=0.0):
    # two triangles of the unit square (areas 0.5 each), tags 1 and 2
    return {"vertices": [[0.0 + shift, 0.0], [1.0, 0.0], [1.0, 1.0],
                         [0.0, 1.0]],
            "triangles": [[0, 1, 2], [0, 2, 3]],
            "domain_per_tri": [1, 2]}


def test_delta_is_per_element_on_one_mesh_and_the_totals_follow():
    sine = dict(_mesh(), loss_density_per_tri=[10.0, 4.0],
                P_loss_total_W=100.0, P_cu_exact_W=60.0)
    pwm = dict(_mesh(), loss_density_per_tri=[13.0, 3.0],
               P_loss_total_W=118.0, P_cu_exact_W=70.0)
    d = D.loss_map_delta(pwm, sine)
    assert d["mode"] == "element"
    assert d["d_density"] == [3.0, -1.0]
    assert d["totals"] == {"P_loss_total_W": 18.0, "P_cu_exact_W": 10.0}
    # a hotter sine map: its own density + the delta, the totals moved
    hot = dict(_mesh(), loss_density_per_tri=[11.0, 0.5],
               P_loss_total_W=104.0, P_cu_exact_W=66.0)
    m, info = D.apply_loss_delta(hot, d)
    assert m["loss_density_per_tri"] == [14.0, 0.0]          # 0.5 − 1 → 0
    assert info["clipped_density_area"] == pytest.approx(0.25)
    assert m["P_loss_total_W"] == pytest.approx(122.0)
    assert m["P_cu_exact_W"] == pytest.approx(76.0)
    assert hot["loss_density_per_tri"] == [11.0, 0.5]        # untouched
    # the per-element delta refuses a map of another mesh
    with pytest.raises(ValueError):
        D.apply_loss_delta(dict(_mesh(0.1), loss_density_per_tri=[1.0, 1.0]),
                           d)


def test_delta_is_per_domain_across_two_meshes():
    sine = dict(_mesh(), loss_density_per_tri=[10.0, 4.0])
    pwm = dict(_mesh(0.2), loss_density_per_tri=[12.0, 4.0])
    d = D.loss_map_delta(pwm, sine)
    assert d["mode"] == "domain"
    # moving vertex 0 to x = 0.2 makes triangle 1 0.4 and leaves triangle 2
    # at 0.5: tag 1 12·0.4 − 10·0.5 = −0.2 per unit length, tag 2 nothing
    assert d["d_tag_watts"][1] == pytest.approx(-0.2)
    assert d["d_tag_watts"][2] == pytest.approx(0.0)
    m, _ = D.apply_loss_delta(sine, d)
    # distributed like the sine map inside each domain: 10·(1 − 0.2/5)
    assert m["loss_density_per_tri"][0] == pytest.approx(9.6)
    assert m["loss_density_per_tri"][1] == pytest.approx(4.0)


def test_feed_forward_is_the_sine_state_s_own_v1_trend():
    ff = D.v1_feed_forward({"V1_seed_peak_V": 400.0, "V1_seed_delta_deg": 30.0},
                           {"V1_seed_peak_V": 392.0, "V1_seed_delta_deg": 30.5})
    assert ff["applied"] and ff["ratio"] == pytest.approx(0.98)
    assert ff["d_delta_deg"] == pytest.approx(0.5)
    assert D.v1_feed_forward({}, {"V1_seed_peak_V": 1.0})["applied"] is False


def test_lxy_probe_is_reused_only_at_its_own_operating_point():
    from motor_ai_sim.routes import simulation as S
    blk = {"inductances": {"Lxy_mH": 0.12}, "probe": {"frames": 4},
           "probe_op": {"I_A": 157.0, "gamma_deg": 2.8, "magnet_temp_c": 64.2}}
    ctx = {"mode": "reuse", "six_phase": blk, "source": "sine state"}
    hit = S._lxy_reuse_block(ctx, i_a=158.0, gamma_deg=3.5,
                             magnet_temp_c=66.0)
    assert hit["inductances"] == {"Lxy_mH": 0.12}
    assert hit["inductances_reused"]["source"] == "sine state"
    assert S._lxy_reuse_block(ctx, i_a=163.0, gamma_deg=2.8,
                              magnet_temp_c=64.2) is None          # +3.8 %
    assert S._lxy_reuse_block(ctx, i_a=157.0, gamma_deg=6.0,
                              magnet_temp_c=64.2) is None          # 3.2°
    assert S._lxy_reuse_block(ctx, i_a=157.0, gamma_deg=2.8,
                              magnet_temp_c=75.0) is None          # 10.8 K
    assert S._lxy_reuse_block({"mode": "skip"}, i_a=157.0, gamma_deg=2.8,
                              magnet_temp_c=64.2) is None
    # a block from before `probe_op` existed is never reused
    old = {"inductances": {"Lxy_mH": 0.12}}
    assert S._lxy_reuse_block({"mode": "reuse", "six_phase": old}, i_a=157.0,
                              gamma_deg=2.8, magnet_temp_c=64.2) is None


def test_a_held_steady_state_and_its_fixed_current_request_share_an_alias():
    from motor_ai_sim.routes import coupled as cp
    cool = {"mode": "air"}
    held = {"I_phase_rms": 157.0, "gamma_deg": 2.8, "hold_torque": True,
            "torque_nm": 1000.0, "coil_temp_c": 150.0, "magnet_temp_c": 100.0,
            "rpm": 2000.0}
    fixed = {"I_phase_rms": 157.041, "gamma_deg": 2.8, "hold_torque": False,
             "coil_temp_c": 150.0, "rpm": 2000.0, "drive": "inverter"}
    a = cp._sine_state_alias_key(held, cool, "steady", 6, i_phase_rms=157.041)
    b = cp._sine_state_alias_key(fixed, cool, "steady", 6,
                                 i_phase_rms=157.041)
    assert a is not None and a == b
    assert a != cp._sine_state_key(fixed, cool, "steady", 6)
    # another current, another state
    assert cp._sine_state_alias_key(fixed, cool, "steady", 6,
                                    i_phase_rms=150.0) != a
    # `limits`: the seed temperatures are the start of the transient
    assert cp._sine_state_alias_key(held, cool, "limits", 6,
                                    i_phase_rms=157.041) is None
