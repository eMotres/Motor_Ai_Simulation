"""drive = "inverter": the loop on the SINE, the controller's PWM once
(owner 2026-09-25: «каплинг делается только с синусоидой, а последний прогон —
с PWM из контроллера»), the sine-vs-inverter columns it yields for free, and
the reuse of an already converged sine state («если уже есть каплинг с синусом —
просто запускается расчёт с PWM из контроллера»).

Faked as ``test_coupled_sine_comparison.rig`` fakes the loop: the sine map puts
the winding at 400 °C, the PWM's own map at 420 °C, so the first PWM pass
moves the temperatures and a second one follows.
"""
from __future__ import annotations

import pytest

from tests.test_coupled_inverter_limits_s1 import _FakeCtl
from tests.test_coupled_limited_state import LOOP_BODY
from tests.test_coupled_sine_comparison import _post, client, rig  # noqa: F401


def test_final_pass_is_the_default_loop_on_sine_then_pwm(client, rig):
    c = _post(client, drive="inverter", fresh=True)
    ctl = _FakeCtl.instances[-1]
    # 2 sine passes (120 °C, then the map's 400 °C, which settles) + 2 PWM
    # passes: the first at the sine state's 400 °C, the second at the PWM
    # map's own 420 °C.
    assert rig["ctl"] == [None, None, ctl, ctl]
    assert rig["inv"][:2] == [None, None]
    assert all(isinstance(i, dict) for i in rig["inv"][2:])
    assert c["drive"] == "inverter"
    assert c["inverter_coupling"] == "final_pass"
    pf = c["pwm_final"]
    assert pf["n_pwm_passes"] == 2
    assert pf["passes"][0]["T_coil_in"] == 400.0
    assert pf["passes"][1]["T_coil_in"] == 420.0
    assert pf["dT_vs_sine_K"]["winding"] == pytest.approx(20.0)
    assert pf["converged"] is True
    # T_j seeded on the sine state (no EM run), then re-solved on each pass
    assert [p for p, _i in ctl.steps] == ["sine_seed", "pwm_final",
                                          "pwm_final"]
    assert c["controller"]["state"]["phase"] == "pwm_final"
    # the reported machine is the final PWM state
    assert c["coil_temp_c"] == 420.0
    assert [h.get("phase") for h in c["history"]][-2:] == ["pwm_final",
                                                           "pwm_final"]
    # …and the sine state is the reference column, both inverter columns kept
    sc = c["sine_comparison"]
    assert sc["algorithm"] == "final_pass" and sc["has_corrected"] is True
    keys = {r["key"] for r in sc["rows"]}
    assert {"T_em_avg_Nm", "P_loss_total_W"} <= keys
    row = next(r for r in sc["rows"] if r["key"] == "P_loss_total_W")
    assert "inverter_corrected" in row and row["delta"] is not None


def test_unknown_inverter_coupling_is_refused(client, rig):
    r = client.post("/api/coupled/run",
                    json={**LOOP_BODY, "drive": "inverter",
                          "inverter_coupling": "sometimes"})
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "bad_inverter_coupling"


def test_an_existing_sine_state_is_reused(client, rig):
    """A sine run of the same machine/point/cooling first; the inverter run
    after it goes straight to the PWM pass(es)."""
    _post(client, drive="current", fresh=True)
    n_sine = len(rig["ctl"])
    assert n_sine == 2                            # the sine run's own passes
    c = _post(client, drive="inverter")           # not fresh: may reuse
    after = rig["ctl"][n_sine:]
    ctl = _FakeCtl.instances[-1]
    assert after == [ctl, ctl]                    # ONLY the PWM passes
    pf = c["pwm_final"]
    assert pf["sine_state_reused"]["source"].startswith("run history")
    assert c["em_runs"] == 2                      # reused rows not counted


def test_a_different_input_is_named_and_the_loop_runs(client, rig):
    _post(client, drive="current", fresh=True)
    n_sine = len(rig["ctl"])
    c = _post(client, drive="inverter", I_phase_rms=21.5)
    assert rig["ctl"][n_sine:n_sine + 2] == [None, None]         # sine loop
    note = c["pwm_final"].get("sine_state_not_reused") or ""
    assert "I_phase_rms differs" in note


def test_limits_final_pass_makes_one_pwm_pass_and_rereads_the_time(
        client, rig):
    c = _post(client, drive="inverter", solve_to="limits", fresh=True)
    ctl = _FakeCtl.instances[-1]
    # sine loop pass + sine pass AT the limit + ONE PWM pass at the limit
    assert rig["ctl"] == [None, None, ctl]
    assert c["mode"] == "limited"
    assert c["pwm_final"]["n_pwm_passes"] == 1
    assert c["pwm_final"]["state"] == "limit"
    assert "pwm" in c["limited"]
    assert c["limited"]["drive_held"] == "inverter"


def test_controller_is_solved_on_a_machine_with_no_bearings():
    """No bearings → no shaft efficiency; the controller request used to come
    back None and the devices were silently never solved (Ø40 L12,
    2026-09-25).  The electromagnetic efficiency is used, and said so."""
    from motor_ai_sim.routes import coupled as cp

    c = cp._ControllerLoop.__new__(cp._ControllerLoop)
    c.cfg = {"device": "d", "devices_parallel": 1, "topology": "one_3ph",
             "set_split": "x", "h_bridge_modulation": "unipolar",
             "dead_time_us": 0.2, "v_gs_on_V": 10.0, "v_gs_off_V": 0.0,
             "e_oss_policy": "p", "r_tim_k_w": 0.1}
    c.inverter = {"v_dc_V": 22.2, "f_carrier_hz": 24000.0}
    c.star_delta, c.rpm, c.pole_pairs, c.t_j_c = "star", 13000.0, 7, 120.0
    c.warnings = []
    req = c._solve_request({"summary": {"P_loss_total_W": 68.5,
                                        "efficiency": 0.92},
                            "I_phase_rms_solved_A": 43.8,
                            "pwm": {"modulation_index": 0.86}})
    assert req is not None
    assert req["efficiency_shaft"] == pytest.approx(0.92)
    assert req["p_ac_W"] == pytest.approx(68.5 / 0.08)
    assert any("no bearings" in w for w in c.warnings)


def test_limits_pwm_pass_short_of_the_current_is_reaimed_once(
        client, rig, monkeypatch):
    """At the limit the temperatures are fixed, but a PWM pass that landed
    outside the current band gets ONE re-aimed pass at the same temperatures."""
    from motor_ai_sim.routes import coupled as cp

    errs = iter([-5.75, 0.2])
    monkeypatch.setattr(cp, "_point_error_pct",
                        lambda inv, i: next(errs, 0.0), raising=True)
    monkeypatch.setattr(cp, "_regulate_v1",
                        lambda inv, i, pts: dict(inv, v_phase_peak_V=12.6),
                        raising=True)
    c = _post(client, drive="inverter", solve_to="limits", fresh=True)
    pf = c["pwm_final"]
    assert pf["n_pwm_passes"] == 2
    assert pf["passes"][0]["T_coil_in"] == pf["passes"][1]["T_coil_in"]
    assert pf["passes"][1]["v_phase_peak_V"] == 12.6
    assert pf["dT_vs_sine_K"] is None and "limit instant" in pf["note"]
    # same temperatures on both passes: one inverter column, the re-aimed one
    assert c["sine_comparison"]["has_corrected"] is False
