"""Bridge conduction against the datasheet R_DS(on)(T_j) (2026-11-03).

An application engineer's hand check of the 40 mm controller: IQE018N06NM6SC,
48 kHz, T_j 110 degC, ~47 A rms -> 3 · R_DS(on)(T_j) · I² for the three-leg
bridge.  The SPICE static table the solve uses must give that number (within
5 % of the card's datasheet curve), and a table that disagrees with the
datasheet by more than the tolerance must not be used for conduction.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.inverter import devices as D
from motor_ai_sim.inverter.devices import DeviceCard, get_device
from motor_ai_sim.inverter.losses import _leg_losses
from motor_ai_sim.passport_v1 import stage3 as S3

ROOT = Path(__file__).resolve().parents[1]


def _bridge(card, *, I_rms=47.0, t_j=110.0, n_par=1, dead_s=100e-9, r_g=10.0):
    drv = S3.drive_for(card, r_g)
    return S3.inverter_losses(card, drv, I_rms=I_rms, f_sw=48e3, dead_s=dead_s, v_dc=22.2,
                              t_j=t_j, n_par=n_par)


def _scaled_static(card: DeviceCard, k: float) -> DeviceCard:
    """The same card with its forward static table scaled by ``k`` (a stale or
    wrong table: e.g. 0.5 = the R the engineer back-computed)."""
    doc = copy.deepcopy(card.doc)
    doc["static_table"]["forward"] = [[r[0], r[1], r[2], r[3] * k, r[4] * k]
                                      for r in doc["static_table"]["forward"]]
    return DeviceCard(doc)


# ── IQE018 at the engineer's point ──────────────────────────────────────────

def test_iqe018_conduction_matches_datasheet_r_at_tj():
    c = get_device("IQE018N06NM6SC")
    I, tj = 47.0, 110.0
    r = _bridge(c, I_rms=I, t_j=tj)
    assert r["conduction_basis"] == "spice_static"
    expected = 3.0 * c.r_ds_on_ohm(tj, 10.0) * I ** 2           # datasheet typ, 2.29 mOhm
    assert expected == pytest.approx(15.16, abs=0.05)
    assert r["cond"] == pytest.approx(expected, rel=0.05)
    # and NOT the half (8 W) a two-device or 1.13 mOhm figure would give
    assert r["cond"] > 0.8 * expected


def test_iqe018_conduction_scales_with_tj():
    c = get_device("IQE018N06NM6SC")
    lo, hi = _bridge(c, t_j=25.0)["cond"], _bridge(c, t_j=110.0)["cond"]
    ds = c.r_ds_on_ohm(110.0, 10.0) / c.r_ds_on_ohm(25.0, 10.0)   # 1.53
    assert hi / lo == pytest.approx(ds, rel=0.05)


def test_two_devices_per_switch_halve_the_conduction():
    c = get_device("IQE018N06NM6SC")
    one, two = _bridge(c, n_par=1)["cond"], _bridge(c, n_par=2)["cond"]
    # the table's R rises a little with current, so half the device current is
    # a hair under half the loss
    assert two == pytest.approx(one / 2.0, rel=0.005)


@pytest.mark.parametrize("part", ["IQE018N06NM6SC", "IQE036N08NM6SC", "IQE050N08NM5SC"])
def test_passport_cards_pass_the_static_check(part):
    c = get_device(part)
    for t in (25.0, 75.0, 110.0, 150.0):
        chk = c.static_conduction_check(t, 12.0)
        assert chk["ok"], chk["line"]
        assert abs(chk["worst_pct"]) < 6.0
    q = c.static_third_quadrant_check(0.0)
    assert q["ok"], q["line"]


# ── the tolerance switch ────────────────────────────────────────────────────

def test_static_table_outside_tolerance_falls_back_to_datasheet():
    good = get_device("IQE018N06NM6SC")
    bad = _scaled_static(good, 0.5)                               # ~1.15 mOhm at 110 degC
    chk = bad.static_conduction_check(110.0, 12.0)
    assert not chk["ok"] and chk["worst_pct"] == pytest.approx(-50.0, abs=1.0)
    r = _bridge(bad)
    assert r["conduction_basis"] == "datasheet"
    I = 47.0
    f_dt = 2 * 100e-9 * 48e3
    assert r["cond"] == pytest.approx(3 * (1 - f_dt) * bad.r_ds_on_ohm(110.0, 12.0) * I ** 2,
                                      rel=1e-9)
    assert any("falls back to the datasheet" in n for n in r["notes"])
    assert "FALLBACK" in S3.conduction_provenance(bad, S3.drive_for(bad, 10.0))


def test_static_table_inside_tolerance_is_used():
    good = get_device("IQE018N06NM6SC")
    near = _scaled_static(good, 1.10)                            # +10 %: inside 15 %
    assert near.static_conduction_check(110.0, 12.0)["ok"]
    r_good, r_near = _bridge(good), _bridge(near)
    assert r_near["conduction_basis"] == "spice_static"
    assert r_near["cond"] == pytest.approx(1.10 * r_good["cond"], rel=1e-3)


def test_tolerance_is_a_parameter():
    c = _scaled_static(get_device("IQE018N06NM6SC"), 1.10)
    assert c.static_conduction_check(110.0, 12.0, tol_pct=15.0)["ok"]
    assert not c.static_conduction_check(110.0, 12.0, tol_pct=5.0)["ok"]
    assert D.SPICE_STATIC_TOL_PCT == 15.0


def test_third_quadrant_fallback_is_its_own_check():
    good = get_device("IQE018N06NM6SC")
    doc = copy.deepcopy(good.doc)
    doc["static_table"]["third_quadrant"] = [[r[0], r[1], r[2], r[3] * 2.0, r[4] * 2.0]
                                             for r in doc["static_table"]["third_quadrant"]]
    bad3q = DeviceCard(doc)
    assert not bad3q.static_third_quadrant_check(0.0)["ok"]
    leg = dict(card=bad3q, i_leg=math.sqrt(2) * 47.0 * np.sin(np.linspace(0, 2 * np.pi, 720,
                                                                           endpoint=False)),
               n_par=1, f_sw=48e3, t_j_c=110.0, v_dc=22.2, v_gs_on=12.0, v_gs_off=0.0,
               r_g=11.3, dead_time_s=100e-9, e_oss_policy="included_in_eon",
               switching_source="spice", r_g_off=10.85, l_sigma_nH=3.0)
    r = _leg_losses(**leg)
    assert r["conduction_source"] == "spice_static"       # forward table still fine
    assert r["third_quadrant_source"] == "datasheet"
    r_ok = _leg_losses(**dict(leg, card=good))
    assert r_ok["third_quadrant_source"] == "spice_static"
    assert r["p_conduction_W"] == pytest.approx(r_ok["p_conduction_W"], rel=1e-12)


# ── switching is untouched by the conduction check ──────────────────────────

def test_switching_unchanged_by_the_check():
    good = get_device("IQE018N06NM6SC")
    bad = _scaled_static(good, 0.5)
    a, b = _bridge(good), _bridge(bad)
    assert a["sw"] == pytest.approx(b["sw"], rel=1e-12)
    # and it is exactly the SPICE switching table at the solve's T_j
    drv = S3.drive_for(good, 10.0)
    th = np.linspace(0.0, 2 * np.pi, 720, endpoint=False)
    i_dev = np.abs(math.sqrt(2) * 47.0 * np.sin(th))
    ladder = np.linspace(0.0, float(i_dev.max()), 33)
    e = [sum(good.e_switch(i_d_A=float(x), t_j_c=110.0, v_dc_V=22.2, v_gs_off_V=0.0,
                           r_g_ext_ohm=drv["r_g"], v_gs_on_V=12.0, source="spice",
                           r_g_off_ext_ohm=drv["r_g_off"], l_sigma_nH=3.0)[k]
             for k in ("e_on_J", "e_off_J", "e_fr_J")) for x in ladder]
    p_sw = 3 * 48e3 * float(np.mean(np.interp(i_dev, ladder, e)))
    assert a["sw"] == pytest.approx(p_sw, rel=1e-9)
    # T_j reaches the switching table (hotter = more energy for this part)
    assert _bridge(good, t_j=150.0)["sw"] > _bridge(good, t_j=25.0)["sw"]


# ── the stored-passport rebuild ─────────────────────────────────────────────

def _script():
    spec = importlib.util.spec_from_file_location(
        "rebuild_inverter_losses", ROOT / "scripts" / "rebuild_inverter_losses.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _record(cond_scale: float = 0.5):
    """A tiny store record: the L12 Si variant at 42.78 / 48.79 A with a stale
    conduction (half the right one)."""
    c = get_device("IQE018N06NM6SC")
    pts = {}
    for I in (42.78, 48.79):
        r = _bridge(c, I_rms=I)
        pts["13000rpm_%gA" % I] = {
            "rpm": 13000.0, "I_A": I, "duty": None, "gamma_deg": 10.0,
            "inverter_loss_W": {"cond": r["cond"] * cond_scale, "sw": r["sw"], "dead": r["dead"]},
            "board_copper_W": S3.K_BOARD_CU * I ** 2, "tj_C": 110.0, "t_heatsink_C": None,
            "P_shaft_W": 900.0, "P_motor_sine_W": 80.0, "P_mech_W": 5.0, "motor_pwm_loss_W": 6.0,
            "P_battery_W": 0.0, "eta_drive_pct": 0.0, "p_cont_max_W": 500.0}
    return {"schema": "passport-v1-full-1",
            "machine": {"die": "D40 test", "configuration": "L12", "version": "6S"},
            "pwm_variants": [{"id": "si_48k", "device": "IQE018N06NM6SC", "technology": "Si",
                              "carrier_hz": 48000.0, "dead_time_s": 1e-7, "n_parallel": 1,
                              "bus_v": {"min": 18.0, "nom": 22.2, "max": 25.2},
                              "drive": {"v_gs_on": 12.0, "v_gs_off": 0.0, "r_g": 11.3,
                                        "r_g_off": 10.85, "l_sigma": 3.0},
                              "i_board_limit_A": {"favourable": 26.1}, "points": pts,
                              "provenance": {}}]}


def test_rebuild_script_restores_conduction(tmp_path):
    m = _script()
    d = tmp_path / "D40 test"
    d.mkdir()
    f = d / "L12.json"
    rec = _record()
    f.write_text(json.dumps(rec), encoding="utf-8")
    assert m.main([str(tmp_path), "--die", "D40 test", "--cfg", "L12"]) == 0
    assert json.loads(f.read_text(encoding="utf-8")) == rec                # dry run
    assert m.main([str(tmp_path), "--cfg", "L12", "--write"]) == 0
    new = json.loads(f.read_text(encoding="utf-8"))
    assert (d / "L12.json.pre-inverter.bak").is_file()
    c = get_device("IQE018N06NM6SC")
    v = new["pwm_variants"][0]
    for p in v["points"].values():
        I = p["I_A"]
        assert p["inverter_loss_W"]["cond"] == pytest.approx(
            3 * c.r_ds_on_ohm(110.0, 10.0) * I ** 2, rel=0.05)
        tot = sum(p["inverter_loss_W"].values())
        assert p["P_battery_W"] == pytest.approx(
            900 + 80 + 5 + 6 + tot + S3.K_BOARD_CU * I ** 2, rel=1e-9)
        assert p["eta_drive_pct"] == pytest.approx(100 * 900 / p["P_battery_W"], rel=1e-9)
        assert p["p_cont_max_W"] == 500.0                                    # motor side kept
    # the Si board limit is its own calibration point (README rating current)
    assert v["i_board_limit_A"]["favourable"] == pytest.approx(26.1, abs=0.01)
    assert "SPICE static" in v["provenance"]["inverter_conduction"]


def test_rebuild_refuses_unknown_board_scale():
    m = _script()
    rec = _record()
    rec["pwm_variants"][0]["n_parallel"] = 2
    with pytest.raises(ValueError, match="board_scale"):
        m.rebuild_record(rec)
    new, _ = m.rebuild_record(rec, board_scale=2.0)
    assert new["pwm_variants"][0]["board_scale"] == 2.0
