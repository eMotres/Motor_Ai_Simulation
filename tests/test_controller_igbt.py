"""IGBT modules (Si IGBT + anti-parallel FWD) in the Controller — 2026-11-03.

Hand-checkable assertions only: the datasheet's own table values for the
real card (Fuji 2MBI800XNE120-50), the textbook closed-form SPWM conduction
integrals on a SYNTHETIC straight-line card (motor AND generator), the stated
bus/R_G/T_j scaling of the switching energies, the two junction temperatures
rebuilt from the reported losses, and that a MOSFET card never enters the
IGBT code at all.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

from motor_ai_sim.inverter import devices as dv
from motor_ai_sim.inverter import igbt as ig
from motor_ai_sim.inverter import losses as lo
from motor_ai_sim.inverter import waveforms as wf

PART = "2MBI800XNE120-50"
REPO_CARD = (Path(__file__).resolve().parents[1] / "config" / "devices"
             / f"{PART}.yaml")
MOSFET = "IMCQ120R004M2H"


def _doc():
    return yaml.safe_load(REPO_CARD.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def test_card_loads_as_an_igbt_module_and_matches_the_tables():
    c = dv.get_device(PART)
    assert isinstance(c, ig.IgbtModuleCard)
    assert dv.validate_card(_doc()) == []
    assert c.v_dss_V == 1200 and c.t_j_max_c == 175
    assert c.r_th_jc_igbt_k_w == pytest.approx(0.037)
    assert c.r_th_jc_diode_k_w == pytest.approx(0.044)
    assert c.r_th_cs_k_w == pytest.approx(0.0167)
    assert c.r_lead_ohm == pytest.approx(1.25e-3)
    # p.2: P_tot 4050 W = (T_vj,max - 25)/R_th(j-c) — the R_th is the datasheet's
    assert (175 - 25) / c.r_th_jc_igbt_k_w == pytest.approx(4050, rel=0.01)
    # p.3 chip V_CE(sat) / V_F at 800 A, typ (raster digitisation, +-0.03 V)
    for t, vce, vf in ((25, 1.45, 1.60), (125, 1.80, 1.65), (150, 1.90, 1.60),
                       (175, 1.95, 1.60)):
        assert c.v_ce_V(800, t) == pytest.approx(vce, abs=0.03)
        assert c.v_f_V(800, t) == pytest.approx(vf, abs=0.03)
    # p.4 switching energies at 800 A, 600 V, R_G 0.5 ohm (vector digitisation, 5 %)
    for t, eon, eoff, err in ((25, 41.7, 77.6, 31.0), (125, 70.2, 92.5, 52.9),
                              (150, 81.1, 94.9, 60.2), (175, 90.4, 100.5, 67.8)):
        e = c.energies(i_A=800, t_j_igbt_c=t, t_j_diode_c=t, v_dc_V=600)
        assert e["e_on_J"] * 1e3 == pytest.approx(eon, rel=0.05)
        assert e["e_off_J"] * 1e3 == pytest.approx(eoff, rel=0.05)
        assert e["e_rr_J"] * 1e3 == pytest.approx(err, rel=0.05)
    row = next(r for r in dv.list_devices() if r["part"] == PART)
    assert not row.get("error")
    assert row["technology"] == "igbt_module" and row["i_d_100c_A"] == 800


def test_curves_are_monotonic_where_they_must_be():
    doc = _doc()
    for blk in (doc["igbt"]["v_ce_sat"], doc["diode"]["v_f"],
                doc["switching"]["e_on"], doc["switching"]["e_off"]):
        assert len(blk["curves"]) == 4
        for cv in blk["curves"]:
            pts = cv["points"]
            assert 8 <= len(pts) <= 16, cv["t_j_c"]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            assert xs == sorted(xs) and len(set(xs)) == len(xs)
            assert all(b >= a for a, b in zip(ys, ys[1:])), (cv["t_j_c"], ys)
    # E_rr rises to its peak near 1200 A (the figure's own shape), then falls
    for cv in doc["switching"]["e_rr"]["curves"]:
        ys = [p[1] for p in cv["points"] if p[0] <= 1000]
        assert all(b >= a for a, b in zip(ys, ys[1:]))
    # the E vs R_G curves: E_on, E_off rise with R_G, E_rr falls
    rg = doc["switching"]["r_g"]
    for row in rg["e_on"]:
        assert all(b > a for a, b in zip(row["e_mJ"], row["e_mJ"][1:]))
    for row in rg["e_rr"]:
        assert all(b < a for a, b in zip(row["e_mJ"], row["e_mJ"][1:]))
    # the IGBT conducts more volts when hotter above the ~200 A crossover
    c = dv.get_device(PART)
    for i in (400, 800, 1200):
        assert c.v_ce_V(i, 25) < c.v_ce_V(i, 125) < c.v_ce_V(i, 175)


# ---------------------------------------------------------------------------
# Conduction — against the closed-form SPWM integrals
# ---------------------------------------------------------------------------

V0_T, R_T = 0.80, 1.5e-3          # synthetic IGBT  V_CE = 0.80 + 1.5 mOhm * I
V0_D, R_D = 0.90, 1.2e-3          # synthetic FWD   V_F  = 0.90 + 1.2 mOhm * I


def _linear_doc():
    d = copy.deepcopy(_doc())
    d["part"] = "SYNTH-IGBT-LIN"
    xs = [0, 200, 400, 600, 800, 1000, 1200, 1600]
    for blk, v0, r in ((d["igbt"]["v_ce_sat"], V0_T, R_T), (d["diode"]["v_f"], V0_D, R_D)):
        blk["curves"] = [{"t_j_c": t, "points": [[x, v0 + r * x] for x in xs]}
                         for t in (25, 175)]
    d["terminals"]["r_lead_mohm"] = 0.0
    return d


@pytest.fixture()
def synth_dir(tmp_path, monkeypatch):
    d = tmp_path / "devices"
    d.mkdir()
    (d / "SYNTH-IGBT-LIN.yaml").write_text(yaml.safe_dump(_linear_doc()), encoding="utf-8")
    # a card the MOSFET loader accepts, of a technology neither model covers
    weird = yaml.safe_load((REPO_CARD.parent / f"{MOSFET}.yaml").read_text(encoding="utf-8"))
    weird["part"], weird["technology"] = "SYNTH-THYRISTOR", "thyristor"
    (d / "SYNTH-THYRISTOR.yaml").write_text(yaml.safe_dump(weird), encoding="utf-8")
    monkeypatch.setattr(dv, "_DIR", d)
    return d


@pytest.mark.parametrize("cos_phi", [0.95, -0.95, 0.3])
def test_leg_conduction_matches_the_closed_form(cos_phi):
    card = dv.DeviceCard(_linear_doc())
    assert isinstance(card, ig.IgbtModuleCard)
    m, ip = 0.9, 400.0
    st = wf.ModulatorSetup(f_elec_hz=50.0, f_carrier_hz=10000.0, v_dc_V=600.0,
                           modulation_index=m, samples_per_carrier=24)
    u = st.grid()
    lag = math.degrees(math.acos(cos_phi))
    i = ip * np.sin(2 * math.pi * u)
    d = wf.leg_duty(st, u, lag)
    r = ig.igbt_leg_losses(card=card, i_leg=i, duty=d, n_par=1, f_sw=10000.0,
                           t_j_igbt_c=125, t_j_diode_c=125, v_dc=600.0,
                           dead_time_s=0.0, include_lead=False)
    want_t = ig.closed_form_spwm_conduction(v0_V=V0_T, r_ohm=R_T, i_peak_A=ip, m=m,
                                            cos_phi=cos_phi, device="igbt")
    want_d = ig.closed_form_spwm_conduction(v0_V=V0_D, r_ohm=R_D, i_peak_A=ip, m=m,
                                            cos_phi=cos_phi, device="diode")
    assert r["p_igbt_conduction_W"] / 2 == pytest.approx(want_t, rel=0.03)
    assert r["p_diode_conduction_W"] / 2 == pytest.approx(want_d, rel=0.03)


@pytest.mark.parametrize("direction", ["motor", "generator"])
def test_full_solve_conduction_matches_the_closed_form(synth_dir, direction):
    m, pf, i_rms = 0.85, 0.95, 300.0
    out = lo.solve_controller({
        "standalone": True, "device": "SYNTH-IGBT-LIN", "v_dc_V": 600.0,
        "i_phase_rms_A": i_rms, "f_elec_hz": 100.0, "f_carrier_hz": 8000.0,
        "modulation_index": m, "power_factor": pf, "power_direction": direction,
        "dead_time_us": 0.0, "include_lead_resistance": False,
        "cooling": {"mode": "liquid", "r_override_k_w": 0.005}})
    cos_lag = pf if direction == "motor" else -pf
    assert out["point"]["cos_current_lag"] == pytest.approx(cos_lag, abs=1e-4)
    ip = i_rms * math.sqrt(2)
    want_t = 6 * ig.closed_form_spwm_conduction(v0_V=V0_T, r_ohm=R_T, i_peak_A=ip,
                                                m=m, cos_phi=cos_lag, device="igbt")
    want_d = 6 * ig.closed_form_spwm_conduction(v0_V=V0_D, r_ohm=R_D, i_peak_A=ip,
                                                m=m, cos_phi=cos_lag, device="diode")
    L = out["losses"]
    assert L["igbt_conduction_W"] == pytest.approx(want_t, rel=0.03)
    assert L["diode_conduction_W"] == pytest.approx(want_d, rel=0.03)
    if direction == "generator":
        # the diodes carry most of the conduction of a generator
        assert L["diode_conduction_W"] > 3 * L["igbt_conduction_W"]
        assert out["conduction_direction"]["reverse_share"] > 0.8
    else:
        assert L["igbt_conduction_W"] > 3 * L["diode_conduction_W"]


def test_dead_time_moves_conduction_from_igbt_to_diode():
    card = dv.get_device(PART)
    st = wf.ModulatorSetup(f_elec_hz=50.0, f_carrier_hz=8000.0, v_dc_V=600.0,
                           modulation_index=0.9, samples_per_carrier=24)
    u = st.grid()
    i = 400.0 * np.sin(2 * math.pi * u)
    d = wf.leg_duty(st, u, 18.0)
    kw = dict(card=card, i_leg=i, duty=d, n_par=1, f_sw=8000.0, t_j_igbt_c=125,
              t_j_diode_c=125, v_dc=600.0)
    a = ig.igbt_leg_losses(dead_time_s=0.0, **kw)
    b = ig.igbt_leg_losses(dead_time_s=3e-6, **kw)
    assert b["p_igbt_conduction_W"] < a["p_igbt_conduction_W"]
    assert b["p_diode_conduction_W"] > a["p_diode_conduction_W"]
    # lead loss is <i^2>*R_lead, whatever conducts
    assert a["p_lead_W"] == pytest.approx(400.0 ** 2 / 2 * 1.25e-3, rel=1e-3)


# ---------------------------------------------------------------------------
# Switching energies — current, bus, T_j, R_G and frequency
# ---------------------------------------------------------------------------

def test_switching_energy_scaling():
    c = dv.get_device(PART)
    e = lambda **k: c.energies(**{"i_A": 600.0, "t_j_igbt_c": 125.0,   # noqa: E731
                                  "t_j_diode_c": 125.0, "v_dc_V": 600.0, **k})
    base = e()
    # current
    lo_i = e(i_A=300.0)
    assert lo_i["e_on_J"] < base["e_on_J"] and lo_i["e_off_J"] < base["e_off_J"]
    assert lo_i["e_rr_J"] < base["e_rr_J"]
    # bus: (V/600)^1.35 on E_on/E_off, ^0.6 on E_rr (the card's stated exponents)
    hv = e(v_dc_V=800.0)
    assert hv["e_on_J"] / base["e_on_J"] == pytest.approx((800 / 600) ** 1.35, rel=1e-9)
    assert hv["e_off_J"] / base["e_off_J"] == pytest.approx((800 / 600) ** 1.35, rel=1e-9)
    assert hv["e_rr_J"] / base["e_rr_J"] == pytest.approx((800 / 600) ** 0.6, rel=1e-9)
    # T_j: between the tabulated curves, linear
    e150 = c.energies(i_A=600, t_j_igbt_c=150, t_j_diode_c=150, v_dc_V=600)
    e175 = c.energies(i_A=600, t_j_igbt_c=175, t_j_diode_c=175, v_dc_V=600)
    assert base["e_on_J"] < e150["e_on_J"] < e175["e_on_J"]
    mid = c.energies(i_A=600, t_j_igbt_c=137.5, t_j_diode_c=137.5, v_dc_V=600)
    assert mid["e_on_J"] == pytest.approx(0.5 * (base["e_on_J"] + e150["e_on_J"]), rel=1e-9)
    # E_rr follows the DIODE temperature only
    split = c.energies(i_A=600, t_j_igbt_c=125, t_j_diode_c=175, v_dc_V=600)
    assert split["e_on_J"] == pytest.approx(base["e_on_J"])
    assert split["e_rr_J"] == pytest.approx(e175["e_rr_J"])
    # R_G: the E vs R_G figure's ratio at 800 A, 125 degC (139.0 / 70.87 at 2 ohm)
    rg = e(r_g_on_ohm=2.0)
    assert rg["e_on_J"] / base["e_on_J"] == pytest.approx(139.0 / 70.87, rel=1e-6)
    assert rg["e_rr_J"] / base["e_rr_J"] == pytest.approx(40.39 / 52.76, rel=1e-6)
    assert rg["e_off_J"] / base["e_off_J"] == pytest.approx(94.01 / 92.24, rel=1e-6)
    # P_sw = f_sw * sum over the period: doubles with the carrier at fixed T_j
    st = lambda f: wf.ModulatorSetup(f_elec_hz=50.0, f_carrier_hz=f, v_dc_V=600.0,  # noqa: E731
                                     modulation_index=0.9, samples_per_carrier=24)
    res = []
    for f in (8000.0, 16000.0):
        u = st(f).grid()
        i = 400 * np.sin(2 * math.pi * u)
        res.append(ig.igbt_leg_losses(card=c, i_leg=i, duty=wf.leg_duty(st(f), u, 18.0),
                                      n_par=1, f_sw=f, t_j_igbt_c=125, t_j_diode_c=125,
                                      v_dc=600.0))
    for k in ("p_on_W", "p_off_W", "p_rr_W"):
        assert res[1][k] / res[0][k] == pytest.approx(2.0, rel=0.01)


# ---------------------------------------------------------------------------
# The two junction temperatures
# ---------------------------------------------------------------------------

def test_two_junction_temperatures_follow_from_the_reported_losses():
    out = lo.solve_controller({
        "standalone": True, "device": PART, "v_dc_V": 600.0, "i_phase_rms_A": 300.0,
        "f_elec_hz": 100.0, "f_carrier_hz": 8000.0, "modulation_index": 0.9,
        "power_factor": 0.95, "power_direction": "generator", "dead_time_us": 2.0,
        "cooling": {"mode": "liquid", "plumbing": "parallel", "n_plates": 3,
                    "r_override_k_w": 0.02, "t_in_c": 65.0, "flow_lpm": 12.0}})
    th = out["thermal"]
    assert th["converged"]
    leg = out["bridges"][0]["legs"][0]
    tm = leg["t_case_module_c"]
    assert leg["t_j_igbt_c"] == pytest.approx(tm + leg["p_igbt_per_device_W"] * 0.037, abs=0.15)
    assert leg["t_j_diode_c"] == pytest.approx(tm + leg["p_diode_per_device_W"] * 0.044, abs=0.15)
    p_pos = leg["p_switch_W"]
    assert tm == pytest.approx(th["t_case_c"] + p_pos * 0.0167, abs=0.2)
    assert th["t_j_max_c"] == pytest.approx(max(th["t_j_igbt_max_c"], th["t_j_diode_max_c"]))
    # a generator: the diodes run hotter than the IGBTs at this point
    assert th["t_j_diode_max_c"] > th["t_j_igbt_max_c"]
    names = {r["name"]: r for r in out["limits"]}
    assert names["Junction temperature, IGBT"]["value"] == pytest.approx(th["t_j_igbt_max_c"], abs=0.05)
    assert names["Junction temperature, FWD"]["value"] == pytest.approx(th["t_j_diode_max_c"], abs=0.05)
    assert out["limits_verdict"] in ("pass", "warn")
    # losses at the solved T_j add up
    L = out["losses"]
    assert L["total_W"] == pytest.approx(L["igbt_W"] + L["diode_W"] + L["lead_W"], abs=0.3)


# ---------------------------------------------------------------------------
# The refusal, and the MOSFET path untouched
# ---------------------------------------------------------------------------

def test_refusal_fires_for_unknown_technology_but_not_for_igbt_module(synth_dir):
    base = {"standalone": True, "v_dc_V": 600.0, "i_phase_rms_A": 100.0,
            "f_elec_hz": 50.0, "f_carrier_hz": 8000.0, "modulation_index": 0.8,
            "power_factor": 0.9, "cooling": {"mode": "liquid", "r_override_k_w": 0.01}}
    out = lo.solve_controller(dict(base, device="SYNTH-IGBT-LIN"))
    assert out["device_technology"] == "igbt_module"
    with pytest.raises(lo.ControllerRefusal) as exc:
        lo.solve_controller(dict(base, device="SYNTH-THYRISTOR"))
    assert exc.value.code == "technology_not_modelled"


def _mosfet_req():
    return {"standalone": True, "device": MOSFET, "v_dc_V": 750.0,
            "i_phase_rms_A": 200.0, "f_elec_hz": 400.0, "f_carrier_hz": 24000.0,
            "modulation_index": 0.85, "power_factor": 0.9,
            "power_direction": "generator", "dead_time_us": 0.5}


def test_a_mosfet_card_never_enters_the_igbt_code(monkeypatch):
    card = dv.get_device(MOSFET)
    assert type(card) is dv.DeviceCard
    ref = json.dumps(lo.solve_controller(_mosfet_req()), sort_keys=True, default=str)
    # with the IGBT module made unimportable the MOSFET answer is the same bytes
    monkeypatch.setitem(sys.modules, "motor_ai_sim.inverter.igbt", None)
    again = json.dumps(lo.solve_controller(_mosfet_req()), sort_keys=True, default=str)
    assert again == ref


def test_coupled_drop_igbt_and_mosfet():
    from motor_ai_sim.inverter import coupling as cpl
    c = dv.get_device(PART)
    d = cpl.fit_device_drop(c, t_j_c=125.0, n_parallel=1, i_leg_peak_A=424.0,
                            dead_time_s=2e-6, forward_share=0.2)
    ft = c.linear_fit("igbt", 125.0, 424.0)
    fd = c.linear_fit("diode", 125.0, 424.0)
    assert d.technology == "igbt_module"
    assert d.v_on0_V == pytest.approx(0.2 * ft["v0_V"] + 0.8 * fd["v0_V"])
    assert d.r_ds_ohm == pytest.approx(0.2 * ft["r_ohm"] + 0.8 * fd["r_ohm"] + 1.25e-3)
    assert d.v_sd_v0_V == pytest.approx(fd["v0_V"]) and d.v_sd_rd_ohm == pytest.approx(fd["r_ohm"])
    assert "v_on0_V" in d.as_dict()
    # no dead time: the pole error is -sign(i)*(v_on0 + r*|i|)
    e = cpl.pole_error_volts(modulator=None, drop=cpl.DeviceDrop(
        r_ds_ohm=d.r_ds_ohm, v_sd_v0_V=d.v_sd_v0_V, v_sd_rd_ohm=d.v_sd_rd_ohm,
        dead_time_s=0.0, v_on0_V=d.v_on0_V), phase="A", i_leg_A=-300.0,
        psi_a_deg=0.0, psi_b_deg=1.0, v_dc_real_V=600.0, deg_per_s=36000.0)
    assert e == pytest.approx(+(d.v_on0_V + 300.0 * d.r_ds_ohm))
    m = cpl.fit_device_drop(dv.get_device(MOSFET), t_j_c=125.0, n_parallel=1,
                            i_leg_peak_A=300.0)
    assert m.v_on0_V == 0.0 and "v_on0_V" not in m.as_dict()


def test_stage3_inverter_losses_accepts_an_igbt_card():
    from motor_ai_sim.passport_v1 import stage3 as s3
    c = dv.get_device(PART)
    drv = s3.drive_for(c, None)
    assert drv["tech"] == "IGBT" and drv["r_g"] == pytest.approx(0.5)
    r = s3.inverter_losses(c, drv, I_rms=300.0, f_sw=8000.0, dead_s=2e-6,
                           v_dc=600.0, t_j=125.0)
    assert r["total"] == pytest.approx(r["cond"] + r["sw"])
    assert r["cond"] > 0 and r["sw"] > 0
