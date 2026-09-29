"""WCMS900B170E53 card, standalone controller runs, per-module plumbing and
the fluid-side R_th correction (2026-09-28, Chinese customer case:
800 VDC, dual 3-phase, 470 A rms/phase, 15 kHz, 2 modules per phase, RP-3).
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.inverter import devices as dv
from motor_ai_sim.inverter import losses as lo

PART = "WCMS900B170E53"


def _case(**over):
    req = dict(standalone=True, n_inverters=2, phase_shift_deg=30.0,
               device=PART, devices_parallel=2, v_dc_V=800.0,
               i_phase_rms_A=470.0, f_elec_hz=750.0, f_carrier_hz=15000.0,
               modulation_index=0.9, power_factor=0.9,
               modulation_scheme="svpwm", dead_time_us=0.6, v_gs_on_V=18.0,
               v_gs_off_V=-5.0, r_th_jc_k_w=0.06, r_tim_k_w=0.02,
               cooling=dict(mode="liquid", coolant="rp3_kerosene",
                            flow_lpm=30.0, t_in_c=60.0, plumbing="parallel",
                            n_plates=12, r_override_k_w=0.015,
                            r_override_coolant="water_glycol_50_65c",
                            r_override_flow_lpm=2.5))
    cool = over.pop("cooling", None)
    req.update(over)
    if cool is not None:
        req["cooling"] = {**req["cooling"], **cool}
    return req


# ── the card ────────────────────────────────────────────────────────────────

def test_card_loads_and_is_in_the_catalogue():
    rows = {r["part"]: r for r in dv.list_devices()}
    assert PART in rows and not rows[PART].get("error")
    r = rows[PART]
    assert r["v_dss_V"] == 1700 and r["r_ds_on_25c_mohm"] == 1.5
    assert r["r_ds_on_175c_mohm"] == 2.67          # p.2 table, not Fig 4
    assert r["r_th_jc_k_w"] is None                # p.4 prints "-"
    assert r["weight_g"] == 346


def test_card_switching_anchors_are_the_datasheet_tables():
    c = dv.get_device(PART)
    e150 = c.e_switch(i_d_A=900, t_j_c=150, v_dc_V=800, v_gs_off_V=-5)
    assert e150["e_on_J"] == pytest.approx(97.2e-3, rel=1e-6)    # p.2
    assert e150["e_off_J"] == pytest.approx(41.3e-3, rel=1e-6)   # p.2
    assert e150["e_fr_J"] == pytest.approx(8.58e-3, rel=1e-6)    # p.3
    e25 = c.e_switch(i_d_A=900, t_j_c=25, v_dc_V=800, v_gs_off_V=-5)
    assert e25["e_fr_J"] == pytest.approx(1.325e-3, rel=1e-3)    # p.3 1.33 mJ
    assert c.v_sd_V(900, 25, -5) == pytest.approx(4.2)           # p.3


def test_unpublished_r_th_is_refused_without_a_stated_value():
    req = _case()
    req.pop("r_th_jc_k_w")
    with pytest.raises(lo.ControllerRefusal) as ei:
        lo.solve_controller(req)
    assert ei.value.code == "r_th_jc_unpublished"


def test_card_without_r_th_and_without_note_is_still_invalid():
    doc = dict(dv.get_device(PART).doc)
    doc["thermal"] = {"r_th_jc_k_w": {"typ": None, "max": None}}
    assert any("r_th_jc_k_w" in b for b in dv.validate_card(doc))


# ── standalone run ─────────────────────────────────────────────────────────

def test_standalone_dual_three_phase_case():
    out = lo.solve_controller(_case())
    assert out["topology"]["n_bridges"] == 2
    assert out["topology"]["n_devices"] == 24          # 12 modules x 2 switches
    legs = [lg for b in out["bridges"] for lg in b["legs"]]
    assert len(legs) == 6
    for lg in legs:
        assert lg["i_leg_rms_A"] == pytest.approx(470.0, abs=0.1)
        assert lg["i_device_peak_A"] == pytest.approx(470 * math.sqrt(2) / 2, abs=0.2)
    # conduction of a leg = (1 - 2 t_d f_sw) * I^2 * R(T_j)/2
    lg = legs[0]
    f_dt = 2 * 0.6e-6 * 15e3
    assert lg["p_conduction_W"] == pytest.approx(
        (1 - f_dt) * 470 ** 2 * lg["r_ds_on_mohm"] * 1e-3 / 2, rel=2e-3)
    # the power is the typed point: 2 x 3 x (m V_dc / 2 sqrt 2) x I x cos phi
    p = 2 * 3 * (0.9 * 800 / (2 * math.sqrt(2))) * 470 * 0.9
    assert out["point"]["p_ac_W"] == pytest.approx(p, rel=1e-6)
    assert out["point"]["modulation_scheme"] == "svpwm"
    assert out["losses"]["total_W"] == pytest.approx(6 * lg["p_leg_W"], rel=1e-3)
    assert out["thermal"]["converged"]
    assert any("REQUEST's assumption" in w for w in out["warnings"])


def test_loss_does_not_depend_on_power_factor_but_power_does():
    a = lo.solve_controller(_case(power_factor=0.8))
    b = lo.solve_controller(_case(power_factor=1.0))
    assert a["losses"]["total_W"] == pytest.approx(b["losses"]["total_W"], rel=1e-9)
    assert b["point"]["p_ac_W"] / a["point"]["p_ac_W"] == pytest.approx(1.25)


def test_standalone_refuses_missing_and_bad_inputs():
    with pytest.raises(lo.ControllerRefusal) as ei:
        lo.solve_controller(_case(power_factor=None))
    assert ei.value.code == "standalone_missing"
    with pytest.raises(lo.ControllerRefusal):
        lo.solve_controller(_case(power_factor=1.2))
    with pytest.raises(lo.ControllerRefusal):
        lo.solve_controller(_case(n_inverters=3))
    with pytest.raises(lo.ControllerRefusal):
        lo.solve_controller(_case(modulation_scheme="dpwm"))


def test_svpwm_linear_limit_warns_only_above_2_over_root3():
    ok = lo.solve_controller(_case(modulation_index=1.1))
    assert not any("OVERMODULATED" in w for w in ok["warnings"])
    bad = lo.solve_controller(_case(modulation_index=1.1, modulation_scheme="spwm"))
    assert any("OVERMODULATED" in w for w in bad["warnings"])


# ── plumbing and the fluid correction ──────────────────────────────────────

def test_fluid_correction_is_dittus_boelter_ratio():
    plate = lo.ColdPlate(coolant="rp3_kerosene", flow_lpm=30.0, plumbing="parallel",
                         n_plates=12, r_override_k_w=0.015,
                         r_override_coolant="water_glycol_50_65c",
                         r_override_flow_lpm=2.5)
    r = plate.resistance()
    ratio = ((0.11 / 0.41) ** 0.6 * ((775 * 2100) / (1040 * 3500)) ** 0.4
             * (1.3e-6 / 1.2e-6) ** 0.4)
    assert r["h_ratio_turbulent"] == pytest.approx(ratio, rel=1e-6)
    assert r["r_k_w"] == pytest.approx(0.015 / ratio, rel=1e-6)
    assert r["r_laminar_bound_k_w"] == pytest.approx(0.015 / (0.11 / 0.41), rel=1e-6)
    assert r["flow_per_plate_lpm"] == pytest.approx(2.5)


def test_series_plumbing_heats_the_last_plate_and_totals_the_rise():
    par = lo.solve_controller(_case())
    ser = lo.solve_controller(_case(cooling=dict(plumbing="series")))
    rise = par["losses"]["total_W"] / (30 / 60000 * 775 * 2100)
    assert par["thermal"]["coolant_rise_K"] == pytest.approx(rise, rel=2e-3)
    assert ser["thermal"]["plumbing"]["t_plate_in_c"] > 60 + 0.8 * ser["thermal"]["coolant_rise_K"]
    # 12x the flow per plate in series: a much better film, a hotter inlet
    assert ser["thermal"]["r_coldplate_k_w"] < par["thermal"]["r_coldplate_k_w"] / 5


def test_bad_plumbing_is_refused():
    for cool in (dict(plumbing="zigzag"), dict(n_plates=1),
                 dict(r_override_coolant="unobtainium"),
                 dict(r_override_k_w=-1.0)):
        with pytest.raises(lo.ControllerRefusal):
            lo.solve_controller(_case(cooling=cool))


def test_shared_plate_default_is_unchanged():
    plate = lo.ColdPlate(r_override_k_w=0.01)
    assert plate.resistance() == {
        "r_k_w": 0.01, "r_film_per_device_k_w": 0.0,
        "basis": "given by the request (the correlation was not used)"}


# ── the route ──────────────────────────────────────────────────────────────

def test_route_standalone_solve(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor_ai_sim.routes import controller as rc
    monkeypatch.setattr(rc, "_HISTORY", rc._RH.history_for("controller.solve"))
    app = FastAPI(); app.include_router(rc.router)
    c = TestClient(app)
    r = c.post("/api/controller/solve?fresh=true", json=_case())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["solved_for"].startswith("standalone")
    assert j["thermal"]["plumbing"]["n_plates"] == 12
    bad = c.post("/api/controller/solve?fresh=true", json=_case(v_dc_V=None))
    assert bad.status_code == 422
