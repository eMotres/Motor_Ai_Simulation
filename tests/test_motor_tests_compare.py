"""Tests section: every conversion between a bench measurement and the model value of the same quantity.

SYNTHETIC inputs only (round invented numbers; no motor's bench data in the public repository), each checked against an
expectation computed by hand in the test:

* R pairs 20.0 / 20.1 / 19.9 mOhm at 30 C; model R_phase 9.0 mOhm at 20 C; two 200 mm leads of 10 mm2
  -> winding 2 * 9.0 * 264.5 / 254.5 = 18.707, leads 2 * 0.017241 * 0.2 / 10 * 264.5 / 254.5 = 0.717, model 19.424 mOhm,
  delta (19.424 - 20.0) / 20.0 = -2.88 %;
* L pairs 100 / 80 / 104 uH -> mean 94.667 = Ld + Lq, amplitude sqrt(2/3 * 330.667) = 14.847 = Lq - Ld,
  Ld 39.910, Lq 54.757 uH;
* free-run generated from E = 0.04 V/rpm (V_LL peak) and P = 100 N + 40 N^2 W (N in krpm).
"""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.motor_tests import compare as C
from motor_ai_sim.motor_tests import comparison, model_source, models

R_PAIRS_MOHM = (20.0, 20.1, 19.9)
R_TEMP_C = 30.0
L_PAIRS_UH = (100.0, 80.0, 104.0)
LEADS_10MM2 = {"count": 3, "length_mm": 200, "area_mm2": 10.0}
R_PHASE_20C = 9.0e-3

# hand-computed expectations for the synthetic inputs above
K30 = (234.5 + 30.0) / (234.5 + 20.0)                         # 1.039293
WINDING_MOHM = 2 * 9.0 * K30                                  # 18.70727
LEADS_MOHM = 2 * 0.017241 * 0.2 / 10.0 * K30 * 1000           # 0.71674
MODEL_MOHM = WINDING_MOHM + LEADS_MOHM                        # 19.42401
L_MEAN = (100 + 80 + 104) / 3                                 # 94.6667
L_AMP = math.sqrt(2 / 3 * ((100 - L_MEAN) ** 2 + (80 - L_MEAN) ** 2 + (104 - L_MEAN) ** 2))   # 14.8474

FREE_RUN = [(n, 100.0 * n / 1000 + 40.0 * (n / 1000) ** 2) for n in range(1000, 5001, 500)]


# ── resistance ──────────────────────────────────────────────────────────────────────────────

def test_copper_temperature_factor_is_234_5_plus_t():
    assert C.cu_temp_factor(20.0, 20.0) == 1.0
    assert C.cu_temp_factor(30.0, 20.0) == pytest.approx(1.039293, abs=1e-6)
    # alpha = 0.00393 1/K about 20 C
    assert (C.cu_temp_factor(21.0, 20.0) - 1.0) == pytest.approx(0.00393, rel=1e-3)


def test_lead_cable_resistance_from_awg_or_cross_section():
    assert C.awg_area_mm2(10) == pytest.approx(5.261, abs=0.005)    # AWG table value
    one = C.lead_resistance_ohm(500, awg=10, temp_c=20.0)
    assert one * 1000 == pytest.approx(0.017241 * 0.5 / 5.261 * 1000, rel=1e-3)   # 1.6386 mOhm
    assert C.lead_resistance_ohm(200, area_mm2=10.0, temp_c=30.0) * 2000 == pytest.approx(LEADS_MOHM, rel=1e-9)
    assert C.lead_resistance_ohm(200) is None                    # no section -> no number, never zero


def test_resistance_model_winding_plus_leads_against_the_mean_of_three_pairs():
    res = C.resistance_compare([v / 1000 for v in R_PAIRS_MOHM], R_TEMP_C, r_phase_ohm=R_PHASE_20C, coil_temp_c=20.0,
                               connection="star", leads=LEADS_10MM2)
    assert res["winding_ohm"] * 1000 == pytest.approx(18.707, abs=0.001)
    assert res["leads_ohm"] * 1000 == pytest.approx(0.717, abs=0.001)
    assert res["model_ohm"] * 1000 == pytest.approx(19.424, abs=0.001)
    assert res["model_ohm"] * 1000 == pytest.approx(MODEL_MOHM, rel=1e-9)
    assert res["measured_ohm"] * 1000 == pytest.approx(20.0, abs=1e-9)
    assert res["delta_pct"] == pytest.approx(-2.880, abs=0.001)
    assert res["remaining_ohm"] * 1000 == pytest.approx(0.576, abs=0.001)
    assert res["spread_pct"] == pytest.approx(1.0, abs=1e-9)       # (20.1 - 19.9) / 20.0
    assert "phase board" in res["not_modelled"]


def test_resistance_delta_connection_is_two_thirds_of_the_phase():
    star = C.resistance_compare([1.0], 20.0, r_phase_ohm=1.0, coil_temp_c=20.0, connection="star")
    delta = C.resistance_compare([1.0], 20.0, r_phase_ohm=1.0, coil_temp_c=20.0, connection="delta")
    assert star["model_ohm"] == pytest.approx(2.0)
    assert delta["model_ohm"] == pytest.approx(2.0 / 3.0)


# ── inductance ──────────────────────────────────────────────────────────────────────────────

def test_ld_lq_fit_from_three_pairs():
    fit = C.ldq_fit_from_pairs(L_PAIRS_UH)
    assert fit["sum"] == pytest.approx(94.667, abs=0.001)         # Ld + Lq
    assert fit["amp"] == pytest.approx(14.847, abs=0.001)         # Lq - Ld
    assert fit["ld"] == pytest.approx(39.910, abs=0.001)
    assert fit["lq"] == pytest.approx(54.757, abs=0.001)
    assert C.ldq_fit_from_pairs([1, 2]) is None


def test_ld_lq_fit_recovers_a_synthetic_machine_at_any_rotor_angle():
    ld, lq = 40.0, 65.0
    for phi in (0.0, 0.3, 1.1, 2.0):
        pairs = [(ld + lq) - (lq - ld) * math.cos(2 * (k * 2 * math.pi / 3) + phi) for k in range(3)]
        fit = C.ldq_fit_from_pairs(pairs)
        assert fit["ld"] == pytest.approx(ld, abs=1e-9) and fit["lq"] == pytest.approx(lq, abs=1e-9)


def test_model_ldq_line_uses_the_star_equivalent_in_delta():
    assert C.model_ldq_line(0.03, 0.06, "star") == (0.03, 0.06)
    assert C.model_ldq_line(0.03, 0.06, "delta") == pytest.approx((0.01, 0.02))


# ── EMF and Br ──────────────────────────────────────────────────────────────────────────────

CARDS = [(20.0, 1.40), (100.0, 1.30), (150.0, 1.20)]


def test_br_interpolates_between_cards_and_extrapolates_with_the_end_slope():
    assert C.br_at(CARDS, 20.0) == pytest.approx(1.40)
    assert C.br_at(CARDS, 60.0) == pytest.approx(1.35)
    assert C.br_at(CARDS, 125.0) == pytest.approx(1.25)
    assert C.br_at(CARDS, 0.0) == pytest.approx(1.425)             # end slope -0.1 / 80 K
    assert C.br_at([(150.0, 1.2)], 20.0) == 1.2                    # one card: no temperature information
    assert C.br_ratio(CARDS, None, 150.0) == 1.0


def test_emf_scales_with_speed_and_br():
    ref = {"rpm": 1000.0, "v_ll_peak": 40.0, "v_ll_rms": 28.0}     # E = 0.04 V/rpm
    ratio = C.br_ratio(CARDS, 60.0, 150.0)
    assert ratio == pytest.approx(1.125)                           # 1.35 / 1.20
    emf = C.emf_model_at(2000.0, ref, ratio)
    assert emf["peak"] == pytest.approx(90.0)                      # 80 V x 1.125
    assert emf["rms"] == pytest.approx(63.0)


# ── no-load loss ────────────────────────────────────────────────────────────────────────────

def test_noload_loss_scales_each_term_by_its_own_speed_law():
    ref = {"rpm": 1000.0, "hyst": 10.0, "eddy": 5.0, "excess": 2.0, "p_solid": 1.0}
    out = C.noload_em_loss_at(2000.0, ref)
    assert out["core"] == pytest.approx(10 * 2 + 5 * 4 + 2 * 2 ** 1.5)
    assert out["solid"] == pytest.approx(4.0)
    hot = C.noload_em_loss_at(1000.0, ref, ratio=1.1)
    assert hot["em"] == pytest.approx((17.0 + 1.0) * 1.21)        # loss ~ B^2
    assert C.noload_em_loss_at(2000.0, {"rpm": 1000.0, "p_core": 8.0})["core"] == pytest.approx(8 * 2 ** 1.5)


def test_loss_fit_a_n_plus_b_n2_recovers_the_generating_law():
    fit = C.fit_an_bn2(FREE_RUN)
    assert fit["a"] == pytest.approx(100.0) and fit["b"] == pytest.approx(40.0) and fit["rmse"] < 1e-9
    noisy = [(n, p + (5.0 if i % 2 else -5.0)) for i, (n, p) in enumerate(FREE_RUN)]
    nf = C.fit_an_bn2(noisy)
    assert nf["a"] == pytest.approx(100.0, abs=10.0) and nf["b"] == pytest.approx(40.0, abs=3.0)
    assert 4.0 < nf["rmse"] < 5.1
    assert C.fit_an_bn2([(1000, 1.0)]) is None


# ── loads ───────────────────────────────────────────────────────────────────────────────────

def test_a_load_point_matches_a_duty_only_within_2_pct_speed_and_3_pct_current():
    duties = [{"name": "a", "rpm": 3000.0, "current": 100.0}, {"name": "b", "rpm": 4000.0, "current": 100.0}]
    assert C.match_duty(3050, 102, duties)["name"] == "a"
    assert C.match_duty(3070, 100, duties) is None                # 2.3 % speed
    assert C.match_duty(3000, 104, duties) is None                # 4 % current
    assert C.match_duty(None, 100, duties) is None


# ── one record against a model built from documents ───────────────────────────────────────

def _cfg_doc():
    return {"winding": {"star_delta": "star"}, "materials": {"magnet": "GRADE_150C"}, "duties": [
        {"name": "rated", "rpm": 3000, "current_arms": 100, "summary": {
            "rpm": 3000, "I_phase_rms_A": 100, "R_phase_ohm": R_PHASE_20C, "coil_temp_C": 20.0,
            "Ld_mH": 0.045, "Lq_mH": 0.060, "T_em_avg_Nm": 30.0, "efficiency": 0.95, "mass_total_kg": 9.0,
            "V_line_peak_V": 400.0}},
        {"name": "no-load 3000", "rpm": 3000, "current_arms": 0, "summary": {
            "rpm": 3000, "I_phase_rms_A": 0.0, "V_line_peak_V": 120.0, "V_line_rms_V": 84.0,
            "P_core_terms": {"stator": {"hysteresis_W": 200.0, "eddy_W": 100.0, "excess_W": 50.0},
                             "rotor": {"hysteresis_W": 10.0, "eddy_W": 5.0, "excess_W": 2.0}},
            "P_core_W": 367.0, "P_solid_W": 40.0, "P_mag_W": 30.0}},
    ]}


LIB = {"magnet": {"GRADE_20C": {"grade": "GRADE", "temperature_c": 20, "Br": 1.40},
                  "GRADE_100C": {"grade": "GRADE", "temperature_c": 100, "Br": 1.30},
                  "GRADE_150C": {"grade": "GRADE", "temperature_c": 150, "Br": 1.20}}}


def _model(mech=None):
    return model_source.from_docs({}, _cfg_doc(), {}, LIB, mech=mech)


def test_model_source_reads_r_ldq_noload_and_the_magnet_grade():
    m = _model()
    assert m["r_ref"]["duty"] == "rated" and m["r_ref"]["coil_temp_c"] == 20.0
    assert m["l_ref"]["duty"] == "rated"                         # the no-load duty stores no Ld/Lq here
    assert [r["duty"] for r in m["noload"]] == ["no-load 3000"]
    nl = m["noload"][0]
    assert nl["hyst"] == 210.0 and nl["eddy"] == 105.0 and nl["excess"] == 52.0 and nl["p_solid"] == 40.0
    assert m["magnet"]["grade"] == "GRADE" and len(m["magnet"]["points"]) == 3
    assert nl["magnet_temp_c"] == 150.0                          # the card's own temperature
    assert m["mass_kg"] == 9.0


def _record(kind, **kw):
    base = {"kind": kind, "date": "2026-01-15"}
    base.update(kw)
    return models.normalize(base, who="eng@example.test")


def test_parameters_record_resistance_and_inductance_rows():
    rec = _record("parameters", conditions={"rotor": "in"}, leads=LEADS_10MM2,
                  resistance={"temp_c": R_TEMP_C, "pairs": [{"pair": p, "value_mohm": v}
                                                            for p, v in zip(("1-2", "2-3", "1-3"), R_PAIRS_MOHM)]},
                  inductance={"freq_hz": 1000, "pairs": [{"pair": p, "value_uh": v}
                                                         for p, v in zip(("1-2", "3-2", "1-3"), L_PAIRS_UH)]},
                  weight=[{"what": "motor", "mass_g": 9300}])
    out = comparison.compare_record(rec, _model(), "D", "C")
    groups = {g["key"]: g for g in out["groups"]}
    r = groups["resistance"]["rows"][0]
    assert r["key"] == "r_ll_mean" and r["model"] == pytest.approx(19.424, abs=0.001)
    assert r["delta_pct"] == pytest.approx(-2.880, abs=0.001)
    assert r["extra"]["leads_mohm"] == pytest.approx(0.717, abs=0.001)
    assert r["extra"]["remaining_mohm"] == pytest.approx(0.576, abs=0.001)
    lrows = {x["key"]: x for x in groups["inductance"]["rows"]}
    assert lrows["l_sum"]["model"] == pytest.approx(105.0) and lrows["l_amp"]["model"] == pytest.approx(15.0)
    assert lrows["l_sum"]["measured"] == pytest.approx(L_MEAN) and lrows["l_amp"]["measured"] == pytest.approx(L_AMP)
    assert lrows["l_d"]["measured"] == pytest.approx(39.910, abs=0.001) and lrows["l_d"]["model"] == pytest.approx(45.0)
    assert lrows["l_q"]["measured"] == pytest.approx(54.757, abs=0.001) and lrows["l_q"]["model"] == pytest.approx(60.0)
    assert lrows["l_sum"]["extra"]["high_freq"] is True          # 1 kHz vs a low-frequency model: flagged
    w = groups["mass"]["rows"][0]
    assert w["measured"] == 9300 and w["model"] == pytest.approx(9000.0)
    assert w["delta_pct"] == pytest.approx((9000 - 9300) / 9300 * 100)


def test_rotor_out_inductance_without_a_stator_only_run_is_no_model_point():
    rec = _record("parameters", conditions={"rotor": "out"},
                  inductance={"pairs": [{"pair": "1-2", "value_uh": 120.0}, {"pair": "2-3", "value_uh": 121.0},
                                        {"pair": "1-3", "value_uh": 122.0}]})
    row = comparison.compare_record(rec, _model(), "D", "C")["groups"][0]["rows"][0]
    assert row["key"] == "l_mean_rotor_out" and row["measured"] == pytest.approx(121.0)
    assert row["model"] is None and row["status"] == "no_model"


def test_free_run_emf_and_loss_with_bearings_windage_and_unexplained_remainder():
    def mech(rpm, temp):
        return {"bearings_w": 0.01 * rpm, "windage_w": 1e-6 * rpm ** 2}
    mech.has_bearings = True
    # E = 0.04 V/rpm peak, P = 100 N + 40 N^2 (N in krpm); magnets at 60 C
    rows = [{"rpm": n, "shaft_power_w": p, "v_ll_pp_v": 2 * 0.04 * n, "v_ll_rms_v": 0.04 * n / math.sqrt(2)}
            for n, p in FREE_RUN]
    rec = _record("free_run", conditions={"ambient_c": 25.0, "magnet_temp_c": 60.0}, rows=rows)
    out = comparison.compare_record(rec, _model(mech), "D", "C")
    g = {x["key"]: x for x in out["groups"]}
    e3000 = [r for r in g["emf_peak"]["rows"] if r["params"]["rpm"] == 3000][0]
    assert e3000["measured"] == pytest.approx(120.0)               # Vpp / 2
    assert e3000["model"] == pytest.approx(120.0 * 1.125)          # the no-load duty x Br(60 C) / Br(150 C)
    assert e3000["delta_pct"] == pytest.approx(12.5)
    loss = [r for r in g["loss"]["rows"] if r["params"]["rpm"] == 3000][0]
    em = (210 + 105 + 52 + 40) * 1.125 ** 2                        # 515.11 W at the duty's own speed
    assert loss["measured"] == pytest.approx(660.0)                # 100 * 3 + 40 * 9
    assert loss["extra"]["em_w"] == pytest.approx(em)
    assert loss["model"] == pytest.approx(em + 30.0 + 9.0)         # + bearings 0.01 n + windage 1e-6 n^2
    assert loss["extra"]["unexplained_w"] == pytest.approx(660.0 - (em + 39.0))
    assert out["fit"]["measured"]["a"] == pytest.approx(100.0) and out["fit"]["measured"]["b"] == pytest.approx(40.0)
    assert out["fit"]["model"] is not None


def test_free_run_without_a_noload_duty_offers_to_create_one():
    doc = _cfg_doc()
    doc["duties"] = doc["duties"][:1]
    m = model_source.from_docs({}, doc, {}, LIB)
    rec = _record("free_run", rows=[{"rpm": 2000, "v_ll_pp_v": 160}, {"rpm": 5000, "v_ll_pp_v": 400}])
    out = comparison.compare_record(rec, m, "D", "C")
    first = out["groups"][0]["rows"][0]
    assert first["status"] == "no_model"
    body = first["action"]["body"]
    assert body["die"] == "D" and body["duty"]["rpm"] == 5000 and body["duty"]["current_arms"] == 0


def test_load_rows_match_a_duty_or_offer_to_create_it():
    rec = _record("load", rows=[{"rpm": 3030, "i_phase_a": 101, "torque_nm": 29.0, "eff_pct": 94.0},
                                {"rpm": 1000, "i_phase_a": 50, "torque_nm": 10.0}])
    out = comparison.compare_record(rec, _model(), "D", "C")
    hit, miss = out["groups"]
    t = hit["rows"][0]
    assert t["key"] == "torque" and t["model"] == 30.0 and t["delta_pct"] == pytest.approx((30 - 29) / 29 * 100)
    assert hit["rows"][1]["model"] == pytest.approx(95.0)
    assert miss["rows"][0]["status"] == "no_model"
    assert miss["rows"][0]["action"]["body"]["duty"]["rpm"] == 1000.0



def test_a_dc_side_efficiency_is_the_system_efficiency_never_compared_with_the_motor():
    # 30 N*m at 3000 rpm = 9424.8 W shaft; 48 V x 220 A = 10560 W DC in -> 89.25 % system efficiency
    row = {"rpm": 3000, "i_phase_a": 100, "torque_nm": 30.0, "v_dc_v": 48.0, "i_dc_a": 220.0}
    sys_pct = 30.0 * 3000 * math.pi / 30.0 / (48.0 * 220.0) * 100
    rec = _record("load", rows=[row])
    rows = {r["key"]: r for r in comparison.compare_record(rec, _model(), "D", "C")["groups"][0]["rows"]}
    assert "eff" not in rows                                     # no motor efficiency was measured
    s = rows["eff_system"]
    assert s["measured"] == pytest.approx(sys_pct) and s["measured"] == pytest.approx(89.25, abs=0.01)
    assert s["model"] is None and s["delta_pct"] is None and s["status"] == "info"
    # with the duty's controller record stored, it is compared with ITS wall-to-shaft efficiency
    stored = {"rated": {"controller": {"efficiency": {"wall_to_shaft": 0.91}}}}
    m = model_source.from_docs({}, _cfg_doc(), stored, LIB)
    s = {r["key"]: r for r in comparison.compare_record(rec, m, "D", "C")["groups"][0]["rows"]}["eff_system"]
    assert s["model"] == pytest.approx(91.0) and s["delta_pct"] == pytest.approx((91.0 - sys_pct) / sys_pct * 100)
    # a measured motor efficiency is still compared with the motor's
    both = _record("load", rows=[{**row, "eff_pct": 94.0}])
    r2 = {r["key"]: r for r in comparison.compare_record(both, _model(), "D", "C")["groups"][0]["rows"]}
    assert r2["eff"]["model"] == pytest.approx(95.0) and r2["eff_system"]["status"] == "info"


# ── 3-D end effect on the model ──────────────────────────────────────────────────────────────

def _free_run_rec():
    rows = [{"rpm": n, "shaft_power_w": p, "v_ll_pp_v": 2 * 0.04 * n, "v_ll_rms_v": 0.04 * n / math.sqrt(2)}
            for n, p in FREE_RUN]
    return _record("free_run", conditions={"ambient_c": 25.0, "magnet_temp_c": 60.0}, rows=rows)


def test_free_run_emf_carries_the_stage_a_flux_factor_and_says_so():
    m = model_source.from_docs({}, _cfg_doc(), {}, LIB, end3d={"k_flux": 0.95})
    assert m["end3d"]["k_flux"] == 0.95 and m["end3d"]["source"] == "Stage A"
    out = comparison.compare_record(_free_run_rec(), m, "D", "C")
    g = {x["key"]: x for x in out["groups"]}
    pk = [r for r in g["emf_peak"]["rows"] if r["params"]["rpm"] == 3000][0]
    rms = [r for r in g["emf_rms"]["rows"] if r["params"]["rpm"] == 3000][0]
    assert pk["model"] == pytest.approx(120.0 * 1.125 * 0.95)
    assert pk["extra"]["model_2d"] == pytest.approx(120.0 * 1.125) and pk["extra"]["k_3d"] == 0.95
    assert rms["model"] == pytest.approx(84.0 * 1.125 * 0.95)
    assert "Br ratio 1.125" in pk["detail"] and "3-D ×0.950 (Stage A)" in pk["detail"]
    assert g["emf_peak"]["basis"] == "magnets at 60 C, 3-D ×0.950 (Stage A)"
    # losses stay 2-D (the flux factor is not applied) and the group says so
    loss = [r for r in g["loss"]["rows"] if r["params"]["rpm"] == 3000][0]
    assert loss["extra"]["em_w"] == pytest.approx((210 + 105 + 52 + 40) * 1.125 ** 2)
    assert "losses 2-D" in g["loss"]["basis"]


def test_without_a_passport_the_2d_value_stays_and_says_so():
    out = comparison.compare_record(_free_run_rec(), _model(), "D", "C")
    g = {x["key"]: x for x in out["groups"]}
    pk = [r for r in g["emf_peak"]["rows"] if r["params"]["rpm"] == 3000][0]
    assert pk["model"] == pytest.approx(120.0 * 1.125) and pk["extra"]["k_3d"] is None
    assert pk["detail"].endswith("2-D, no 3-D passport")
    assert g["emf_peak"]["basis"].endswith("2-D, no 3-D passport")


def test_the_passport_stored_with_a_duty_is_used_when_none_is_resolved():
    doc = _cfg_doc()
    doc["duties"][0]["summary"]["end3d"] = {"k_flux": 0.9}
    m = model_source.from_docs({}, doc, {}, LIB)
    assert m["end3d"]["k_flux"] == 0.9 and "stored with the duty" in m["end3d"]["source"]
    # an absurd factor is not a passport
    assert model_source.factors_from_end3d({"k_flux": 1.4})["k_flux"] is None


def test_torque_carries_the_torque_factor_k_t_else_the_flux_factor():
    rec = _record("load", rows=[{"rpm": 3000, "i_phase_a": 100, "torque_nm": 29.0}])
    flux = model_source.from_docs({}, _cfg_doc(), {}, LIB, end3d={"k_flux": 0.95})
    t = comparison.compare_record(rec, flux, "D", "C")["groups"][0]["rows"][0]
    assert t["model"] == pytest.approx(30.0 * 0.95) and "flux factor" in t["detail"] and t["extra"]["model_2d"] == 30.0
    meas = model_source.from_docs({}, _cfg_doc(), {}, LIB, end3d={"k_flux": 0.95, "k_T": 0.98})
    t = comparison.compare_record(rec, meas, "D", "C")["groups"][0]["rows"][0]
    assert t["model"] == pytest.approx(30.0 * 0.98) and "measured k_T" in t["detail"]
    grp = comparison.compare_record(rec, meas, "D", "C")["groups"][0]
    assert grp["basis"].startswith("torque 3-D ×0.980")
    # the efficiency is not scaled
    rec2 = _record("load", rows=[{"rpm": 3000, "i_phase_a": 100, "torque_nm": 29.0, "eff_pct": 94.0}])
    eff = comparison.compare_record(rec2, meas, "D", "C")["groups"][0]["rows"][1]
    assert eff["model"] == pytest.approx(95.0)


def test_inductance_and_resistance_take_no_flux_factor():
    m = model_source.from_docs({}, _cfg_doc(), {}, LIB, end3d={"k_flux": 0.9})
    rec = _record("parameters", conditions={"rotor": "in"}, leads=LEADS_10MM2,
                  resistance={"temp_c": R_TEMP_C, "pairs": [{"pair": p, "value_mohm": v}
                                                            for p, v in zip(("1-2", "2-3", "1-3"), R_PAIRS_MOHM)]},
                  inductance={"freq_hz": 100, "pairs": [{"pair": p, "value_uh": v}
                                                        for p, v in zip(("1-2", "3-2", "1-3"), L_PAIRS_UH)]})
    g = {x["key"]: x for x in comparison.compare_record(rec, m, "D", "C")["groups"]}
    assert g["resistance"]["rows"][0]["model"] == pytest.approx(19.424, abs=0.001)
    ls = {r["key"]: r for r in g["inductance"]["rows"]}
    assert ls["l_sum"]["model"] == pytest.approx(105.0) and ls["l_sum"]["detail"].endswith("2-D")
    assert g["inductance"]["basis"].startswith("2-D")


def test_resolve_end3d_looks_the_passport_up_by_the_configuration_geometry(monkeypatch):
    from motor_ai_sim.routes import simulation as sim
    seen = {}
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda geo=None: seen.setdefault("geo", geo) and "fp1")
    monkeypatch.setattr(sim, "_end3d_lookup", lambda fp, geo=None, exact=False: {"k_flux": 0.93} if fp == "fp1" else None)
    die = {"geometry": {"motor_length": 20.0, "wire_height": 1.0}}
    got = model_source.resolve_end3d(die, {"geometry_overrides": {"motor_length": 25.0, "wire_height": None}})
    assert got == {"k_flux": 0.93}
    assert seen["geo"]["motor_length"] == 25.0 and seen["geo"]["wire_height"] == 1.0


# ── validation is loud ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, msg", [
    ({"kind": "parameters", "date": "2026-13-01", "weight": [{"mass_g": 1}]}, "date"),
    ({"kind": "parameters", "date": "2026-01-15"}, "at least one"),
    ({"kind": "free_run", "date": "2026-01-15", "rows": [{"rpm": "abc"}]}, "not a number"),
    ({"kind": "free_run", "date": "2026-01-15", "rows": [{"rpm": -5}]}, "outside"),
    ({"kind": "load", "date": "2026-01-15", "rows": []}, "at least one row"),
    ({"kind": "x", "date": "2026-01-15"}, "kind"),
    ({"kind": "parameters", "date": "2026-01-15", "resistance": {"pairs": [{"pair": "1 2 3", "value_mohm": 1}]}},
     "terminal pair"),
    ({"kind": "parameters", "date": "2026-01-15", "weight": [{"mass_g": 1}], "leads": {"length_mm": 200}}, "AWG"),
])
def test_bad_input_is_refused_with_the_field_named(body, msg):
    with pytest.raises(models.TestInputError, match=msg):
        models.normalize(body, who="x@example.test")
