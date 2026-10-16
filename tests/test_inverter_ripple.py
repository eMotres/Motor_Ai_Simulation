"""PWM current ripple / THD (``inverter.ripple``) and the power direction
(motor | generator on an active rectifier) of ``solve_controller`` —
owner 2026-09-28."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.inverter import losses as lo
from motor_ai_sim.inverter import ripple as rp

V, L, FSW = 800.0, 100e-6, 20_000.0


def _closed_form(m: float, mod: str) -> float:
    """Per-phase ripple rms of a three-phase VSI, isolated neutral, high pulse
    ratio (Hava/Kerkman/Lipo 1999; Holtz's per-carrier ripple integral):
    I = V_dc/(24 L f_sw) * m * sqrt(3/2 - (4 sqrt3/pi) m + c4 m^2),
    c4 = 9/8 (sine), 27/16 - 81 sqrt3/(64 pi) (SVPWM); m on the V_dc/2 basis."""
    c4 = 9.0 / 8.0 if mod == "sine" else 27.0 / 16.0 - 81.0 * math.sqrt(3.0) / (64.0 * math.pi)
    return V / (24.0 * L * FSW) * m * math.sqrt(
        1.5 - 4.0 * math.sqrt(3.0) / math.pi * m + c4 * m * m)


@pytest.mark.parametrize("mod,m", [("sine", 0.3), ("sine", 0.8), ("sine", 1.0),
                                   ("svpwm", 0.5), ("svpwm", 0.9), ("svpwm", 1.15)])
def test_ripple_matches_the_analytic_per_carrier_integral(mod, m):
    r = rp.pwm_ripple(v_dc_V=V, modulation_index=m, f1_hz=50.0, f_sw_hz=FSW,
                      modulation=mod, l_d_H=L, sampling="regular_symmetric")
    assert r["ripple_rms_A"] == pytest.approx(_closed_form(m, mod), rel=5e-3)


def test_reference_case_dual_three_phase():
    """800 V, SVPWM m 0.9, 750 Hz, 15 kHz, 90 uH, dual 3-ph 30 deg, isolated
    neutrals, L_xy = 20 % -> 39.6 A rms total, 10.3 A rms from alpha-beta."""
    r = rp.pwm_ripple(v_dc_V=800, modulation_index=0.9, f1_hz=750, f_sw_hz=15000,
                      modulation="svpwm", n_sets=2, set_shift_deg=30,
                      l_d_H=90e-6, l_xy_H=18e-6, i1_rms_A=300.0)
    assert r["ripple_rms_A"] == pytest.approx(39.6, rel=0.05)
    assert r["ripple_rms_ab_A"] == pytest.approx(10.3, rel=0.05)
    # the planes are orthogonal: the squares add
    assert r["ripple_rms_A"] ** 2 == pytest.approx(
        r["ripple_rms_ab_A"] ** 2 + r["ripple_rms_xy_A"] ** 2, rel=1e-3)
    assert r["synchronous"] and r["window_periods"] == 1
    assert r["thd_pct"] == pytest.approx(100 * r["ripple_rms_A"] / 300.0, rel=1e-3)
    assert len(r["spectrum"]) == 12


def test_grid_independent():
    a = rp.pwm_ripple(v_dc_V=V, modulation_index=0.9, f1_hz=50, f_sw_hz=FSW,
                      modulation="sine", l_d_H=L, samples_per_carrier=64)
    b = rp.pwm_ripple(v_dc_V=V, modulation_index=0.9, f1_hz=50, f_sw_hz=FSW,
                      modulation="sine", l_d_H=L, samples_per_carrier=512)
    assert a["ripple_rms_A"] == pytest.approx(b["ripple_rms_A"], rel=2e-3)


def test_saliency_sits_between_the_two_axes():
    kw = dict(v_dc_V=V, modulation_index=0.9, f1_hz=500, f_sw_hz=FSW, modulation="svpwm")
    r_d = rp.pwm_ripple(l_d_H=L, **kw)["ripple_rms_A"]
    r_q = rp.pwm_ripple(l_d_H=2 * L, **kw)["ripple_rms_A"]
    r_dq = rp.pwm_ripple(l_d_H=L, l_q_H=2 * L, **kw)["ripple_rms_A"]
    assert r_q < r_dq < r_d
    assert r_q == pytest.approx(r_d / 2, rel=1e-3)        # linear in 1/L


def test_asynchronous_ratio_is_warned():
    r = rp.pwm_ripple(v_dc_V=V, modulation_index=0.8, f1_hz=733.0, f_sw_hz=15000,
                      modulation="svpwm", l_d_H=L)
    assert not r["synchronous"]
    assert any("asynchronous" in w for w in r["warnings"])


def test_dual_needs_l_xy_and_common_neutral_needs_l0():
    kw = dict(v_dc_V=V, modulation_index=0.9, f1_hz=750, f_sw_hz=15000,
              n_sets=2, l_d_H=90e-6)
    with pytest.raises(rp.RippleRefusal, match="L_xy"):
        rp.pwm_ripple(**kw)
    with pytest.raises(rp.RippleRefusal, match="L_0"):
        rp.pwm_ripple(l_xy_H=18e-6, neutral="common", **kw)
    r = rp.pwm_ripple(l_xy_H=18e-6, neutral="common", l_zero_H=10e-6, **kw)
    assert r["ripple_rms_zero_A"] is not None and r["ripple_rms_zero_A"] >= 0.0


def test_interleaving_changes_the_xy_ripple():
    kw = dict(v_dc_V=800, modulation_index=0.9, f1_hz=750, f_sw_hz=15000,
              modulation="svpwm", n_sets=2, l_d_H=90e-6, l_xy_H=18e-6)
    a = rp.pwm_ripple(carrier_interleave_deg=0, **kw)
    b = rp.pwm_ripple(carrier_interleave_deg=90, **kw)
    assert b["ripple_rms_ab_A"] < a["ripple_rms_ab_A"]
    assert b["ripple_rms_xy_A"] > a["ripple_rms_xy_A"]


# ── the controller: direction + ripple ─────────────────────────────────────

PART = "IMCQ120R007M2H"


def _sa(**over):
    req = dict(standalone=True, device=PART, v_dc_V=800.0, i_phase_rms_A=300.0,
               f_elec_hz=750.0, f_carrier_hz=15000.0, modulation_index=0.9,
               power_factor=0.9, n_inverters=2, phase_shift_deg=30.0,
               modulation_scheme="svpwm", dead_time_us=0.5,
               ripple_l_d_uH=90.0, ripple_l_xy_pct=20.0,
               cooling=dict(mode="liquid", flow_lpm=20.0, t_in_c=40.0))
    req.update(over)
    return req


def test_generator_direction():
    mo = lo.solve_controller(_sa(power_direction="motor"))
    ge = lo.solve_controller(_sa(power_direction="generator"))
    assert mo["power_direction"] == "motor" and ge["power_direction"] == "generator"
    # synchronous-rectifying MOSFETs: the losses do not change with direction
    assert ge["losses"]["total_W"] == pytest.approx(mo["losses"]["total_W"], rel=1e-9)
    p_ac, p_l = ge["point"]["p_ac_W"], ge["losses"]["total_W"]
    assert ge["efficiency"]["inverter"] == pytest.approx((p_ac - p_l) / p_ac, abs=1e-5)
    assert mo["efficiency"]["inverter"] == pytest.approx(p_ac / (p_ac + p_l), abs=1e-5)
    # the link current reverses
    assert mo["dc_link"]["i_dc_mean_A"] > 0 > ge["dc_link"]["i_dc_mean_A"]
    assert ge["ripple"]["dc_link"]["i_dc_mean_A"] < 0
    # a generator carries most of its I^2 in the third quadrant
    assert mo["conduction_direction"]["reverse_share"] < 0.5
    assert ge["conduction_direction"]["reverse_share"] > 0.5
    assert ge["point"]["current_lag_deg"] == pytest.approx(
        180.0 - mo["point"]["current_lag_deg"], abs=0.01)


def test_ripple_in_the_standalone_solve_and_thd_limit():
    out = lo.solve_controller(_sa(thd_limit_pct=5.0))
    r = out["ripple"]
    assert r["status"] == "computed"
    assert r["ripple_rms_A"] == pytest.approx(39.6, rel=0.05)
    assert r["thd_verdict"] == "fail"
    assert any("THD" in w for w in out["warnings"])
    ok = lo.solve_controller(_sa(thd_limit_pct=20.0))["ripple"]
    assert ok["thd_verdict"] == "pass"


def test_ripple_without_inductance_is_said_not_guessed():
    out = lo.solve_controller(_sa(ripple_l_d_uH=None, ripple_l_xy_pct=None))
    assert out["ripple"]["status"] == "not_computed"
    miss = lo.solve_controller(_sa(ripple_l_xy_pct=None))
    assert miss["ripple"]["status"] == "missing_input"
    assert any("L_xy" in w for w in miss["warnings"])


def test_bad_direction_and_igbt_card_are_refused(monkeypatch):
    with pytest.raises(lo.ControllerRefusal):
        lo.solve_controller(_sa(power_direction="sideways"))
    real = lo.get_device

    def fake(part):
        c = real(part)
        c.doc = {**c.doc, "technology": "igbt"}
        return c
    monkeypatch.setattr(lo, "get_device", fake)
    with pytest.raises(lo.ControllerRefusal, match="IGBT"):
        lo.solve_controller(_sa())


def test_route_standalone_carries_direction_and_ripple(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc
    monkeypatch.setattr(rc, "_HISTORY", rc._RH.history_for("controller.solve"))
    app = FastAPI(); app.include_router(rc.router)
    c = TestClient(app)
    r = c.post("/api/controller/solve?fresh=true",
               json=_sa(power_direction="generator", carrier_interleave_deg=90))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["power_direction"] == "generator"
    assert j["ripple"]["carrier_interleave_deg"] == 90.0
    bad = c.post("/api/controller/solve?fresh=true",
                 json=_sa(carrier_interleave_deg=200))
    assert bad.status_code == 422


def test_report_rows_show_direction_ripple_and_thd():
    from motor_ai_sim import report as R
    out = lo.solve_controller(_sa(power_direction="generator", thd_limit_pct=20.0))
    rows = {r[0]: r for r in R.controller_rows(out)}
    assert rows["Power direction"][1].startswith("generator")
    assert rows["Phase-current PWM ripple"][1].endswith("A rms")
    assert "PASS" in rows["Phase-current THD"][2]
    assert "AC input" in rows["Inverter efficiency"][2]
